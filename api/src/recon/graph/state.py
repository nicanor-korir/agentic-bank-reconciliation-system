"""Graph state.

Deliberately primitive. The state is checkpointed to Postgres and reloaded
after an interrupt that may be hours later and in a different process, so it
holds ids, plain dicts and integers -- never a live connection, a client, or a
dataclass whose shape might change between the checkpoint and the resume.
"""

from __future__ import annotations

from typing import Any, TypedDict


class RunState(TypedDict, total=False):
    run_id: str
    tenant: str
    period: str

    bank_line_ids: list[int]
    unmatched_ids: list[int]

    # What Tier 2 produced, keyed by bank line id as a string. Checkpointed so
    # Tier 3 adjudicates exactly these -- see tier2_candidates for why
    # recomputing them was wrong.
    candidate_sets: dict[str, Any]

    # Committed decisions, as serialisable records.
    decisions: list[dict[str, Any]]
    # Items awaiting a human, each carrying its candidates and the reasoning.
    queue: list[dict[str, Any]]
    # What the human decided, applied on resume.
    resolutions: list[dict[str, Any]]

    cost_micro: int
    llm_calls: int
    adjudication_errors: int
    status: str
    halt_reason: str
