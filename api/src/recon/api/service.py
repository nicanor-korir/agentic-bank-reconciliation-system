"""Run orchestration for the API.

Runs execute on a worker thread so an HTTP request never holds a 1,200-line
reconciliation open. Progress is not pushed from the graph: the audit log is
already written per node, so SSE tails `events` and streams what was durably
recorded. That means a reconnecting client sees the same history, and progress
cannot report a step the audit trail does not contain.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from recon.config import Settings
from recon.graph.runner import resume_run, start_run
from recon.retrieval.base import ResolvedPair


@dataclass
class RunHandle:
    run_id: str
    thread: threading.Thread
    error: str | None = None
    summary: dict[str, Any] | None = field(default=None)


class RunService:
    """Tracks in-flight runs. Deliberately process-local.

    Durable state lives in Postgres and the checkpointer; this only knows which
    runs *this* process started, so a restart loses no committed work.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._runs: dict[str, RunHandle] = {}
        self._lock = threading.Lock()

    def start(self, period: str, adjudicator: str) -> str:
        import uuid

        run_id = str(uuid.uuid4())

        def work() -> None:
            try:
                summary = start_run(
                    self._settings,
                    period=period,
                    adjudicator_mode=adjudicator,
                    run_id=run_id,
                )
                with self._lock:
                    self._runs[run_id].summary = summary
            except Exception as exc:
                with self._lock:
                    self._runs[run_id].error = f"{type(exc).__name__}: {exc}"

        thread = threading.Thread(target=work, name=f"run-{run_id[:8]}", daemon=True)
        with self._lock:
            self._runs[run_id] = RunHandle(run_id=run_id, thread=thread)
        thread.start()
        return run_id

    def resume(self, run_id: str, resolutions: list[dict[str, Any]]) -> dict[str, Any]:
        summary = resume_run(self._settings, run_id, resolutions)
        with self._lock:
            handle = self._runs.get(run_id)
            if handle is not None:
                handle.summary = summary
        return summary

    def status(self, run_id: str) -> RunHandle | None:
        with self._lock:
            return self._runs.get(run_id)


def write_back(settings: Settings, pairs: list[ResolvedPair]) -> int:
    """Push confirmed matches into retrieval.

    This is the closing move of the demo and the only place a human decision
    changes future behaviour. It stores the **counterparty** behind the
    narrative, not the invoice: this month's invoice is closed by next month,
    so the document is worthless and the payer is not.

    Failure here must not lose the correction -- the decision is already
    committed in Postgres, so a failed write-back costs future recall, not
    accuracy. It is reported, not raised.
    """
    if not pairs:
        return 0
    from recon.retrieval.weaviate_index import WeaviateIndex
    from recon.retrieval.weaviate_index import connect as weaviate_connect

    with weaviate_connect(settings) as client:
        index = WeaviateIndex(client, settings.match)
        return index.index_resolved_pairs(settings.recon_tenant, pairs)


def pairs_from_resolutions(
    queue: list[dict[str, Any]],
    resolutions: list[dict[str, Any]],
    counterparty_by_entry: dict[int, str],
) -> list[ResolvedPair]:
    """Turn approved corrections into retrievable history.

    Only approvals produce a pair. A rejection says "none of these", which is
    useful to a person reading the audit trail and actively misleading as
    retrieval history -- it would teach the index to associate a narrative with
    a counterparty nobody confirmed.
    """
    by_ref = {item["bank_ref"]: item for item in queue}
    pairs: list[ResolvedPair] = []

    for resolution in resolutions:
        if resolution.get("action") != "approve":
            continue
        item = by_ref.get(resolution["bank_ref"])
        if item is None:
            continue
        entry_ids = [int(e) for e in resolution.get("ledger_entry_ids", [])]
        counterparty = next(
            (counterparty_by_entry[e] for e in entry_ids if e in counterparty_by_entry),
            None,
        )
        if not counterparty:
            continue
        doc_refs = resolution.get("doc_refs") or []
        pairs.append(
            ResolvedPair(
                bank_ref=item["bank_ref"],
                narrative=item["narrative"],
                counterparty=counterparty,
                doc_ref=doc_refs[0] if doc_refs else None,
                amount_minor=int(item["amount_minor"]),
            )
        )
    return pairs
