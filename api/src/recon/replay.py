"""`make replay RUN_ID=...` -- reproduce a stored run and diff it, strictly.

The claim this command exists to support is narrow and worth stating exactly:
*given the same inputs, the system reaches the same decisions, and we can prove
the model was not re-rolled to make that true.*

So the deterministic tiers are re-executed for real -- that is where genuine
replay risk lives, in an accidental set iteration or a wall-clock date window --
while Tier 3 is served from the recorded calls, keyed by a hash of the exact
request. A recording miss is a failure, never a live call.

Any divergence exits non-zero with the specific rows that differ. A replay that
"mostly" matches is a failed replay.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from recon.config import Settings
from recon.db import connect
from recon.graph.runner import start_run
from recon.llm.adjudicator import ReplayMissError

# What must be identical. Deliberately includes the rationale: an identical
# match reached with a different explanation is a different decision as far as
# anyone reading the audit trail is concerned.
COMPARED = ("bank_line_id", "tier", "decision", "confidence", "rationale", "ledger_entry_ids")


@dataclass
class Divergence:
    bank_ref: str
    field: str
    original: Any
    replayed: Any


def _decisions(conn: Any, run_id: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        """
        select bl.bank_ref, d.bank_line_id, d.tier, d.decision, d.confidence,
               d.rationale, d.ledger_entry_ids
        from decisions d
        join bank_lines bl on bl.id = d.bank_line_id
        where d.run_id = %s and d.auto_committed
        order by bl.bank_ref
        """,
        (run_id,),
    ).fetchall()
    return {str(r["bank_ref"]): dict(r) for r in rows}


def compare(
    original: dict[str, dict[str, Any]], replayed: dict[str, dict[str, Any]]
) -> tuple[list[Divergence], list[str], list[str]]:
    """Returns (field divergences, missing from replay, unexpected in replay)."""
    missing = sorted(set(original) - set(replayed))
    extra = sorted(set(replayed) - set(original))

    divergences: list[Divergence] = []
    for bank_ref in sorted(set(original) & set(replayed)):
        before, after = original[bank_ref], replayed[bank_ref]
        for field in COMPARED:
            if before[field] != after[field]:
                divergences.append(Divergence(bank_ref, field, before[field], after[field]))
    return divergences, missing, extra


def replay(settings: Settings, run_id: str, period: str | None = None) -> int:
    with connect() as conn:
        row = conn.execute(
            "select id, status, config_snapshot, model_version, prompt_version, "
            "git_sha, git_dirty from runs where id = %s",
            (run_id,),
        ).fetchone()
        if row is None:
            print(f"unknown run {run_id}")
            return 2
        original = _decisions(conn, run_id)
        source = conn.execute(
            "select distinct s.period from bank_lines bl join sources s on s.id = bl.source_id "
            "join decisions d on d.bank_line_id = bl.id where d.run_id = %s",
            (run_id,),
        ).fetchall()

    period = period or (str(source[0]["period"]) if source else "2026-06")
    print(f"replaying {run_id}")
    print(f"  period          {period}")
    print(f"  model           {row['model_version']}")
    print(f"  prompt          {row['prompt_version']}")
    print(f"  recorded at git {row['git_sha']}{' (dirty)' if row['git_dirty'] else ''}")
    if row["git_dirty"]:
        print(
            "  NOTE: the recorded run came from an uncommitted tree, so the code "
            "that produced it\n        is not fully identified by its git sha."
        )
    print(f"  decisions       {len(original)}\n")

    try:
        summary = start_run(
            settings,
            period=period,
            adjudicator_mode="recorded",
            use_retrieval=True,
            replay_of=run_id,
        )
    except ReplayMissError as exc:
        print(f"REPLAY FAILED: {exc}")
        print(
            "  The input to the model differs from the recorded run. Check the "
            "prompt, the\n  matching config, the candidate set, and the underlying "
            "ledger data."
        )
        return 1

    with connect() as conn:
        replayed = _decisions(conn, summary["run_id"])

    divergences, missing, extra = compare(original, replayed)

    print(f"  replay run      {summary['run_id']}")
    print(f"  decisions       {len(replayed)}\n")

    if not divergences and not missing and not extra:
        print(f"IDENTICAL: {len(original)} of {len(original)} decisions match exactly.")
        return 0

    print(
        f"DIVERGED: {len(divergences)} field difference(s), "
        f"{len(missing)} missing, {len(extra)} unexpected.\n"
    )
    for bank_ref in missing[:10]:
        print(f"  missing    {bank_ref}: decided in the original, not in the replay")
    for bank_ref in extra[:10]:
        print(f"  unexpected {bank_ref}: decided in the replay, not in the original")
    for d in divergences[:20]:
        print(
            f"  {d.bank_ref} {d.field}:\n    original {d.original!r}\n    replayed {d.replayed!r}"
        )
    if len(divergences) > 20:
        print(f"  ... and {len(divergences) - 20} more")
    return 1
