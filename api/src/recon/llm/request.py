"""Building the adjudication request.

Kept as a pure function of (bank line, candidates, config) for two reasons.

First, `request_hash` is what makes replay exact: the same inputs must produce
byte-identical request bytes, so a hash miss during replay means the *input*
changed, which is precisely the regression worth catching. Anything
non-deterministic in here -- a clock, a set iteration, a dict built from an
unordered source -- would make replay report spurious drift.

Second, it means the request shape is unit-testable without an API key.
"""

from __future__ import annotations

from typing import Any

from recon.config import MatchConfig
from recon.hashing import canonical_json, sha256_hex
from recon.llm.schema import TOOL_NAME, adjudication_tool
from recon.matching.tier2_candidates import CandidateSet
from recon.matching.types import BankLine, LedgerEntry

MAX_TOKENS = 1_024


def candidate_id(index: int) -> str:
    """Stable, opaque handle for a candidate within one request.

    Deliberately not the ledger id: the model should choose on evidence, and a
    database id is an invitation to pattern-match on numbers that mean nothing.
    """
    return f"C{index + 1}"


def describe_line(line: BankLine) -> dict[str, Any]:
    return {
        "value_date": line.value_date.isoformat(),
        "amount_minor": line.amount_minor,
        "currency": line.currency,
        "direction": "money_in" if line.amount_minor > 0 else "money_out",
        "narrative": line.narrative,
        "counterparty": line.counterparty,
    }


def describe_candidates(
    candidate_set: CandidateSet, by_id: dict[int, LedgerEntry]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidate_set.candidates):
        entries = [by_id[i] for i in candidate.ledger_entry_ids if i in by_id]
        out.append(
            {
                "id": candidate_id(index),
                "kind": candidate.kind,
                "total_minor": candidate.total_minor,
                "difference_from_payment_minor": candidate.delta_minor,
                "items": [
                    {
                        "doc_ref": e.doc_ref,
                        "entry_date": e.entry_date.isoformat(),
                        "amount_minor": e.amount_minor,
                        "open_amount_minor": e.open_amount_minor,
                        "description": e.description,
                        "counterparty": e.counterparty,
                    }
                    for e in entries
                ],
                # Why retrieval surfaced it. Explicitly labelled as a hint so the
                # prompt's "ranking is not evidence" rule has something to point at.
                "found_by": list(candidate.sources),
            }
        )
    return out


def build_request(
    line: BankLine,
    candidate_set: CandidateSet,
    by_id: dict[int, LedgerEntry],
    system_prompt: str,
    config: MatchConfig,
) -> dict[str, Any]:
    """The exact body sent to the API. Deterministic for deterministic inputs."""
    payload = {
        "bank_line": describe_line(line),
        "candidates": describe_candidates(candidate_set, by_id),
    }
    return {
        "model": config.model_version,
        "max_tokens": MAX_TOKENS,
        # The prompt is the stable prefix and the payload varies per line, so a
        # cache breakpoint here is what makes the input side cheap at volume.
        "system": [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": canonical_json(payload)}],
        "tools": [adjudication_tool()],
        "tool_choice": {"type": "tool", "name": TOOL_NAME},
        # Structured extraction over a short payload; depth buys nothing here
        # and the whole point of the cascade is that this call stays cheap.
        "output_config": {"effort": "low"},
    }


def request_hash(request: dict[str, Any]) -> str:
    """Identity of a call, for the recorded-replay store.

    Covers the whole body -- model, prompt, tools and payload -- so a changed
    prompt or a changed candidate list is a different call rather than a silent
    reuse of an answer given to a different question.
    """
    return sha256_hex(canonical_json(request))
