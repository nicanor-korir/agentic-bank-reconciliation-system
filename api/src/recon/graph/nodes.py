"""Graph nodes.

Two structural commitments from the brief, both visible here.

The tiers are **batched**: each node processes the whole unresolved set in one
pass. One graph invocation per transaction would multiply checkpoint writes by
1,200 and make the cost ceiling unenforceable, because nothing would hold the
running total.

Only Tier 3 fans out, with bounded concurrency and a cost check before every
call. The ceiling is checked *before* spending, not after, so a run halts at
the limit rather than just past it.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from langgraph.types import interrupt

from recon.config import Settings
from recon.db import connect, load_bank_lines, load_ledger_entries, transaction
from recon.graph.audit import EventLog
from recon.graph.persistence import (
    finish_run,
    insert_decision,
    insert_human_review,
    record_llm_call,
)
from recon.graph.state import RunState
from recon.llm.adjudicator import (
    Adjudicator,
    CostCeilingError,
    CostMeter,
    ReplayMissError,
)
from recon.llm.request import build_request, candidate_id
from recon.llm.schema import COMMITTABLE, InvalidAdjudicationError
from recon.matching.cascade import run_deterministic
from recon.matching.tier2_candidates import CandidateSet
from recon.matching.tier2_candidates import generate as generate_candidates
from recon.matching.types import BankLine, LedgerEntry, Match
from recon.retrieval.base import NarrativeIndex


@dataclass
class Deps:
    settings: Settings
    index: NarrativeIndex
    adjudicator: Adjudicator
    system_prompt: str
    meter: CostMeter


def _match_record(match: Match) -> dict[str, Any]:
    return {
        "bank_line_id": match.bank_line_id,
        "bank_ref": match.bank_ref,
        "ledger_entry_ids": list(match.ledger_entry_ids),
        "doc_refs": list(match.doc_refs),
        "tier": match.tier,
        "decision": match.decision,
        "confidence": str(match.confidence),
        "rationale": match.rationale,
        "evidence": list(match.evidence),
        "auto_committed": True,
    }


def _persist(
    state: RunState, records: list[dict[str, Any]], node: str, extra: dict[str, Any] | None = None
) -> None:
    """Write decisions and the node's audit event in one transaction.

    Same transaction deliberately: a decision that exists without its audit
    event, or an event describing a decision that was rolled back, is worse
    than either failing.
    """

    with transaction() as conn:
        for record in records:
            insert_decision(conn, state["run_id"], state["tenant"], record)
        EventLog(conn, state["run_id"]).append(
            node,
            {
                "node": node,
                "committed": len(records),
                # A sample, not the whole list. One node committed 988 refs,
                # which made the hashed payload enormous and the SSE frame
                # unreadable. Nothing is lost: the full set is exactly
                # `select bank_line_id from decisions where run_id = ...`,
                # so the event stays a summary and the table stays the record.
                "sample_bank_refs": [r["bank_ref"] for r in records[:10]],
                **(extra or {}),
            },
        )


def _load(state: RunState) -> tuple[list[BankLine], list[LedgerEntry]]:
    with connect() as conn:
        lines = load_bank_lines(conn, state["tenant"], state["period"])
        entries = load_ledger_entries(conn, state["tenant"])
    return lines, entries


def make_nodes(deps: Deps) -> dict[str, Any]:
    settings = deps.settings

    def ingest(state: RunState) -> RunState:
        lines, _ = _load(state)
        ids = [line.id for line in lines]
        _persist(state, [], "ingest", {"bank_lines": len(ids), "period": state["period"]})
        return {
            **state,
            "bank_line_ids": ids,
            "unmatched_ids": ids,
            "decisions": [],
            "queue": [],
            "resolutions": [],
            "cost_micro": 0,
            "llm_calls": 0,
            "status": "running",
        }

    def deterministic(state: RunState) -> RunState:
        """Tiers 0 and 1 in one node.

        They share a `claimed` set -- an entry settled by Tier 0 must be
        invisible to Tier 1 -- and splitting them across nodes would mean
        checkpointing that set as graph state for no benefit.
        """
        lines, entries = _load(state)
        wanted = set(state["unmatched_ids"])
        result = run_deterministic(
            [line for line in lines if line.id in wanted], entries, settings.match
        )
        records = [_match_record(m) for m in result.matches]
        by_tier: dict[str, int] = {}
        for match in result.matches:
            key = f"tier{match.tier}"
            by_tier[key] = by_tier.get(key, 0) + 1
        _persist(state, records, "deterministic_tiers", {"by_tier": by_tier})
        return {
            **state,
            "decisions": [*state["decisions"], *records],
            "unmatched_ids": [line.id for line in result.unmatched],
        }

    def candidates(state: RunState) -> RunState:
        lines, entries = _load(state)
        wanted = set(state["unmatched_ids"])
        unmatched = [line for line in lines if line.id in wanted]
        claimed = {eid for record in state["decisions"] for eid in record["ledger_entry_ids"]}
        sets = generate_candidates(
            unmatched, entries, deps.index, settings.match, state["tenant"], claimed=claimed
        )
        offered = sum(len(s.candidates) for s in sets.values())
        truncated = sum(1 for s in sets.values() if s.subset_truncated)
        _persist(
            state,
            [],
            "tier2_candidates",
            {
                "lines": len(unmatched),
                "candidates_offered": offered,
                # Never silent: a truncated search is a bounded search, and the
                # difference between "none exists" and "I stopped looking" matters.
                "truncated_subset_searches": truncated,
            },
        )
        return state

    def adjudicate(state: RunState) -> RunState:
        lines, entries = _load(state)
        by_id = {e.id: e for e in entries}
        wanted = set(state["unmatched_ids"])
        unmatched = sorted((line for line in lines if line.id in wanted), key=lambda line: line.id)
        claimed = {eid for record in state["decisions"] for eid in record["ledger_entry_ids"]}
        sets = generate_candidates(
            unmatched, entries, deps.index, settings.match, state["tenant"], claimed=claimed
        )

        committed: list[dict[str, Any]] = []
        queue: list[dict[str, Any]] = []
        errors: list[str] = []
        halt_reason = ""

        def call(line: BankLine) -> tuple[BankLine, Any, Exception | None]:
            request = build_request(line, sets[line.id], by_id, deps.system_prompt, settings.match)
            ids = {candidate_id(i) for i in range(len(sets[line.id].candidates))}
            try:
                return line, deps.adjudicator.adjudicate(request, ids), None
            except Exception as exc:
                return line, None, exc

        # Bounded fan-out. Results are re-sorted by bank line id afterwards so
        # completion order cannot leak into the decisions.
        with ThreadPoolExecutor(max_workers=settings.match.tier3_concurrency) as pool:
            pending: list[BankLine] = []
            for line in unmatched:
                try:
                    deps.meter.check()
                except CostCeilingError as exc:
                    halt_reason = str(exc)
                    break
                pending.append(line)
            results = list(pool.map(call, pending))

        for line, adjudication, error in sorted(results, key=lambda r: r[0].id):
            candidate_set: CandidateSet = sets[line.id]
            if isinstance(error, ReplayMissError):
                # A replay miss is a verification failure, not a business
                # outcome. Escalating it would let a replay whose inputs had
                # drifted finish "successfully" with a longer review queue,
                # which is precisely the claim replay exists to disprove.
                raise error

            if error is not None:
                # Otherwise fail safe: escalate, never auto-commit. But a
                # systematic failure escalating everything looks exactly like a
                # model that declined everything, so the count is recorded --
                # 212 adjudication crashes once read as a cautious model.
                errors.append(f"{line.bank_ref}: {type(error).__name__}: {error}")
                queue.append(_queue_item(line, candidate_set, by_id, reason=str(error)))
                continue

            deps.meter.record(adjudication.cost_micro)
            with transaction() as conn:
                record_llm_call(conn, state["run_id"], line.id, adjudication)

            chosen = _resolve_candidates(adjudication, candidate_set)
            auto = (
                adjudication.decision in COMMITTABLE
                and adjudication.confidence >= settings.match.tier3_autocommit_confidence
                and bool(chosen)
            )
            if auto:
                committed.append(
                    {
                        "bank_line_id": line.id,
                        "bank_ref": line.bank_ref,
                        "ledger_entry_ids": list(chosen),
                        "doc_refs": _doc_refs(chosen, by_id),
                        "tier": 3,
                        "decision": adjudication.decision,
                        "confidence": str(adjudication.confidence),
                        "rationale": adjudication.rationale,
                        "evidence": list(adjudication.evidence),
                        "auto_committed": True,
                    }
                )
            else:
                queue.append(
                    _queue_item(
                        line,
                        candidate_set,
                        by_id,
                        decision=adjudication.decision,
                        confidence=str(adjudication.confidence),
                        rationale=adjudication.rationale,
                        evidence=list(adjudication.evidence),
                    )
                )

        matched = {r["bank_line_id"] for r in committed}
        _persist(
            state,
            committed,
            "tier3_adjudicate",
            {
                "calls": deps.meter.calls,
                "cost_micro": deps.meter.spent_micro,
                "escalated": len(queue),
                "halt_reason": halt_reason,
            },
        )
        return {
            **state,
            "decisions": [*state["decisions"], *committed],
            "unmatched_ids": [i for i in state["unmatched_ids"] if i not in matched],
            "queue": queue,
            "cost_micro": deps.meter.spent_micro,
            "llm_calls": deps.meter.calls,
            "status": "halted_cost" if halt_reason else "running",
            "halt_reason": halt_reason,
            "adjudication_errors": len(errors),
        }

    def human_review(state: RunState) -> RunState:
        """Pause the graph. The queue is the payload a reviewer acts on.

        One interrupt for the whole batch rather than one per item: this is a
        batch graph, and 200 separate pauses would mean 200 checkpoint
        round-trips to clear one month.
        """
        resolutions = interrupt(
            {
                "run_id": state["run_id"],
                "awaiting": len(state["queue"]),
                "queue": state["queue"],
            }
        )
        return {**state, "resolutions": list(resolutions or [])}

    def apply_human(state: RunState) -> RunState:
        """Commit human decisions, superseding the escalation they replace."""
        from recon.db import transaction

        applied: list[dict[str, Any]] = []
        by_ref = {item["bank_ref"]: item for item in state["queue"]}

        with transaction() as conn:
            for resolution in state["resolutions"]:
                item = by_ref.get(resolution["bank_ref"])
                if item is None:
                    continue
                action = resolution["action"]
                entry_ids = list(resolution.get("ledger_entry_ids", []))
                escalation_id = insert_decision(
                    conn,
                    state["run_id"],
                    state["tenant"],
                    {
                        "bank_line_id": item["bank_line_id"],
                        "ledger_entry_ids": [],
                        "tier": 4,
                        "decision": "escalated",
                        "confidence": Decimal("0.000"),
                        "rationale": item.get("rationale") or "Escalated for human review.",
                        "evidence": item.get("evidence", []),
                        "auto_committed": False,
                    },
                )
                decision = (
                    "no_match"
                    if action == "reject"
                    else ("split_match" if len(entry_ids) > 1 else "match")
                )
                record = {
                    "bank_line_id": item["bank_line_id"],
                    "bank_ref": item["bank_ref"],
                    "ledger_entry_ids": entry_ids,
                    "doc_refs": resolution.get("doc_refs", []),
                    "tier": 4,
                    "decision": decision,
                    "confidence": "1.000",
                    "rationale": resolution.get(
                        "note", f"Confirmed by {resolution.get('reviewer', 'reviewer')}."
                    ),
                    "evidence": ["human_review"],
                    "auto_committed": False,
                }
                decision_id = insert_decision(
                    conn,
                    state["run_id"],
                    state["tenant"],
                    record,
                    supersedes_id=escalation_id,
                )
                insert_human_review(
                    conn,
                    decision_id,
                    resolution.get("reviewer", "reviewer"),
                    action,
                    entry_ids,
                    resolution.get("note"),
                )
                applied.append(record)
            EventLog(conn, state["run_id"]).append(
                "apply_human",
                {
                    "node": "apply_human",
                    "applied": len(applied),
                    "sample_bank_refs": [r["bank_ref"] for r in applied[:10]],
                },
            )

        matched = {r["bank_line_id"] for r in applied if r["ledger_entry_ids"]}
        return {
            **state,
            "decisions": [*state["decisions"], *applied],
            "unmatched_ids": [i for i in state["unmatched_ids"] if i not in matched],
        }

    def close_run(state: RunState) -> RunState:
        from recon.db import transaction

        status = state.get("status") or "running"
        final = "halted_cost" if status == "halted_cost" else "completed"
        with transaction() as conn:
            log = EventLog(conn, state["run_id"])
            log.append(
                "close_run",
                {
                    "node": "close_run",
                    "status": final,
                    "decisions": len(state["decisions"]),
                    "unresolved": len(state["unmatched_ids"]),
                    "cost_micro": state.get("cost_micro", 0),
                },
            )
            broken = log.verify()
            if broken is not None:
                raise RuntimeError(
                    f"audit chain broken at event {broken} for run {state['run_id']}"
                )
            finish_run(conn, state["run_id"], final, state.get("cost_micro", 0))
        return {**state, "status": final}

    return {
        "ingest": ingest,
        "deterministic_tiers": deterministic,
        "tier2_candidates": candidates,
        "tier3_adjudicate": adjudicate,
        "human_review": human_review,
        "apply_human": apply_human,
        "close_run": close_run,
    }


def _doc_refs(entry_ids: list[int], by_id: dict[int, LedgerEntry]) -> list[str]:
    return [ref for i in entry_ids if i in by_id and (ref := by_id[i].doc_ref)]


def _resolve_candidates(adjudication: Any, candidate_set: CandidateSet) -> list[int]:
    index = {candidate_id(i): c for i, c in enumerate(candidate_set.candidates)}
    chosen: list[int] = []
    for cid in adjudication.candidate_ids:
        candidate = index.get(cid)
        if candidate is None:
            raise InvalidAdjudicationError(f"unknown candidate id {cid!r}")
        chosen.extend(candidate.ledger_entry_ids)
    return chosen


def _queue_item(
    line: BankLine, candidate_set: CandidateSet, by_id: dict[int, LedgerEntry], **extra: Any
) -> dict[str, Any]:
    """What a reviewer sees: the line, the top candidates, and the reasoning."""
    return {
        "bank_line_id": line.id,
        "bank_ref": line.bank_ref,
        "value_date": line.value_date.isoformat(),
        "amount_minor": line.amount_minor,
        "currency": line.currency,
        "narrative": line.narrative,
        "counterparty": line.counterparty,
        "candidates": [
            {
                "id": candidate_id(i),
                "kind": c.kind,
                "ledger_entry_ids": list(c.ledger_entry_ids),
                "doc_refs": list(c.doc_refs),
                "total_minor": c.total_minor,
                "difference_minor": c.delta_minor,
                "found_by": list(c.sources),
                "items": [
                    {
                        "doc_ref": by_id[e].doc_ref,
                        "description": by_id[e].description,
                        "counterparty": by_id[e].counterparty,
                        "amount_minor": by_id[e].amount_minor,
                    }
                    for e in c.ledger_entry_ids
                    if e in by_id
                ],
            }
            for i, c in enumerate(candidate_set.candidates)
        ],
        **extra,
    }
