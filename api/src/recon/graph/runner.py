"""Driving the graph: start a run, resume one after human review.

Adjudicator selection is explicit and recorded on the run row, because "which
thing produced these decisions" is the first question any number in a report
has to survive.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command

from recon.config import Settings
from recon.db import connect, transaction
from recon.graph.build import build_graph
from recon.graph.nodes import Deps
from recon.graph.persistence import create_run, load_recordings
from recon.hashing import sha256_hex
from recon.llm.adjudicator import (
    Adjudicator,
    AnthropicAdjudicator,
    CostMeter,
    RecordedAdjudicator,
    StubAdjudicator,
)
from recon.retrieval.base import NarrativeIndex, NullIndex

PROMPT_PATH = Path(__file__).resolve().parents[1] / "llm" / "prompts" / "adjudicate.v1.md"


def load_prompt() -> tuple[str, str]:
    """Returns (text, version). Version is the content hash, so a prompt edit
    is a different version without anyone having to remember to bump it."""
    text = PROMPT_PATH.read_text()
    return text, sha256_hex(text)[:16]


def choose_adjudicator(settings: Settings, mode: str, replay_of: str | None = None) -> Adjudicator:
    if mode == "recorded":
        if not replay_of:
            raise ValueError("recorded adjudication needs a run to replay")
        with connect() as conn:
            recordings = load_recordings(conn, replay_of)
        if not recordings:
            raise ValueError(f"run {replay_of} recorded no model calls to replay")
        return RecordedAdjudicator(recordings)

    if mode == "stub":
        return StubAdjudicator(settings.match.tier3_autocommit_confidence)

    if not settings.anthropic_api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is not set. Use --adjudicator stub to exercise the "
            "graph without a key, or --adjudicator recorded --replay-of RUN_ID."
        )
    from anthropic import Anthropic

    return AnthropicAdjudicator(Anthropic(api_key=settings.anthropic_api_key))


def build_index(settings: Settings, enabled: bool) -> tuple[NarrativeIndex, Any]:
    """Returns (index, closer). Weaviate is optional; the run degrades to
    windows-only retrieval rather than failing."""
    if not enabled:
        return NullIndex(), None
    try:
        from recon.retrieval.weaviate_index import WeaviateIndex
        from recon.retrieval.weaviate_index import connect as weaviate_connect

        ctx = weaviate_connect(settings)
        client = ctx.__enter__()
        return WeaviateIndex(client, settings.match), ctx
    except Exception:
        return NullIndex(), None


def start_run(
    settings: Settings,
    period: str,
    adjudicator_mode: str,
    use_retrieval: bool = True,
    replay_of: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    prompt, prompt_version = load_prompt()
    adjudicator = choose_adjudicator(settings, adjudicator_mode, replay_of)
    index, closer = build_index(settings, use_retrieval)
    run_id = run_id or str(uuid.uuid4())

    with transaction() as conn:
        create_run(conn, run_id, settings, prompt_version, adjudicator.name)
        if replay_of:
            conn.execute("update runs set replay_of = %s where id = %s", (replay_of, run_id))

    deps = Deps(
        settings=settings,
        index=index,
        adjudicator=adjudicator,
        system_prompt=prompt,
        meter=CostMeter(settings.match.run_cost_ceiling_micro),
    )

    try:
        with PostgresSaver.from_conn_string(settings.database_url) as checkpointer:
            checkpointer.setup()
            graph = build_graph(deps, checkpointer)
            config = {"configurable": {"thread_id": run_id}}
            state = graph.invoke(
                {"run_id": run_id, "tenant": settings.recon_tenant, "period": period},
                config=config,
            )
            return _summarise(run_id, state, graph, config)
    finally:
        if closer is not None:
            closer.__exit__(None, None, None)


def resume_run(
    settings: Settings, run_id: str, resolutions: list[dict[str, Any]]
) -> dict[str, Any]:
    """Resume from the checkpoint with the reviewer's decisions."""
    prompt, _ = load_prompt()
    with connect() as conn:
        row = conn.execute("select config_snapshot from runs where id = %s", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown run {run_id}")

    mode = str(row["config_snapshot"].get("adjudicator", "stub"))
    adjudicator = choose_adjudicator(settings, "stub" if mode == "stub" else mode, run_id)
    index, closer = build_index(settings, True)
    deps = Deps(
        settings=settings,
        index=index,
        adjudicator=adjudicator,
        system_prompt=prompt,
        meter=CostMeter(settings.match.run_cost_ceiling_micro),
    )
    try:
        with PostgresSaver.from_conn_string(settings.database_url) as checkpointer:
            checkpointer.setup()
            graph = build_graph(deps, checkpointer)
            config = {"configurable": {"thread_id": run_id}}
            state = graph.invoke(Command(resume=resolutions), config=config)
            return _summarise(run_id, state, graph, config)
    finally:
        if closer is not None:
            closer.__exit__(None, None, None)


def get_queue(settings: Settings, run_id: str) -> list[dict[str, Any]]:
    """The exception queue for a paused run, read from its checkpoint.

    Read from the checkpoint rather than a table because it is the interrupt
    payload -- the exact thing the graph will resume against. A separately
    maintained queue table could drift from what resume actually expects.
    """
    prompt, _ = load_prompt()
    index, closer = build_index(settings, False)
    deps = Deps(
        settings=settings,
        index=index,
        adjudicator=StubAdjudicator(settings.match.tier3_autocommit_confidence),
        system_prompt=prompt,
        meter=CostMeter(settings.match.run_cost_ceiling_micro),
    )
    try:
        with PostgresSaver.from_conn_string(settings.database_url) as checkpointer:
            graph = build_graph(deps, checkpointer)
            snapshot = graph.get_state({"configurable": {"thread_id": run_id}})
            interrupts = getattr(snapshot, "interrupts", ()) or ()
            if not interrupts:
                return []
            payload = getattr(interrupts[0], "value", {}) or {}
            queue: list[dict[str, Any]] = payload.get("queue", [])
            return queue
    finally:
        if closer is not None:
            closer.__exit__(None, None, None)


def _summarise(
    run_id: str, state: dict[str, Any], graph: Any, config: dict[str, Any]
) -> dict[str, Any]:
    snapshot = graph.get_state(config)
    interrupts = getattr(snapshot, "interrupts", ()) or ()
    queue = state.get("queue", [])
    if interrupts:
        payload = getattr(interrupts[0], "value", {}) or {}
        queue = payload.get("queue", queue)

    decisions = state.get("decisions", [])
    by_tier: dict[int, int] = {}
    for record in decisions:
        by_tier[record["tier"]] = by_tier.get(record["tier"], 0) + 1

    return {
        "run_id": run_id,
        "status": "awaiting_human" if interrupts else state.get("status", "completed"),
        "bank_lines": len(state.get("bank_line_ids", [])),
        "committed": len(decisions),
        "by_tier": dict(sorted(by_tier.items())),
        "queued_for_human": len(queue),
        "unresolved": len(state.get("unmatched_ids", [])),
        "llm_calls": state.get("llm_calls", 0),
        "adjudication_errors": state.get("adjudication_errors", 0),
        "cost_micro": state.get("cost_micro", 0),
        "halt_reason": state.get("halt_reason", ""),
        "queue": queue,
    }
