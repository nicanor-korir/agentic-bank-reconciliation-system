"""Score a completed run's real decisions against the golden labels.

The ablation arms in `runner.py` re-execute the deterministic tiers in-process,
which is cheap and repeatable. Tier 3 cannot work that way: it costs money and
its answers are recorded, not recomputed. So the full-cascade arm scores what a
*run actually decided* -- the rows in `decisions` -- rather than simulating it.

That is also the more honest measurement. It scores the decisions that were
committed to the database and that a bookkeeper would have acted on, including
anything the graph did on the way.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from recon.db.engine import Db
from recon.evals.golden import GoldenCase
from recon.evals.metrics import Report, score
from recon.matching.types import Match

_SQL = """
select bl.bank_ref, d.bank_line_id, d.ledger_entry_ids, d.tier, d.decision,
       d.confidence, d.rationale, d.evidence,
       coalesce(
         (select array_agg(le.doc_ref order by le.doc_ref)
          from ledger_entries le where le.id = any(d.ledger_entry_ids)),
         '{}'
       ) as doc_refs
from decisions d
join bank_lines bl on bl.id = d.bank_line_id
where d.run_id = %s and d.auto_committed
order by bl.bank_ref
"""


def load_run_matches(conn: Db, run_id: str) -> dict[str, Match]:
    """Auto-committed decisions only.

    Escalations and human confirmations are excluded deliberately: this arm
    measures what the system was willing to commit *without* a person, which is
    the only thing precision is meaningful about.
    """
    rows = conn.execute(_SQL, (run_id,)).fetchall()
    return {
        str(r["bank_ref"]): Match(
            bank_line_id=int(r["bank_line_id"]),
            bank_ref=str(r["bank_ref"]),
            ledger_entry_ids=tuple(r["ledger_entry_ids"]),
            doc_refs=tuple(r["doc_refs"]),
            tier=int(r["tier"]),
            decision=str(r["decision"]),
            confidence=Decimal(str(r["confidence"])),
            rationale=str(r["rationale"]),
            evidence=tuple(r["evidence"]),
        )
        for r in rows
    }


def _tier_report(report: Report, matches: dict[str, Match], tier: int) -> dict[str, Any]:
    """Precision for one tier in isolation.

    Recall is deliberately absent: a tier only ever sees what earlier tiers
    could not resolve, so "recall for Tier 3" has no denominator that means
    anything. Precision does -- of what this tier committed, how much was right.
    """
    outcomes = [o for o in report.outcomes if matches.get(o.bank_ref) is not None]
    at_tier = [o for o in outcomes if matches[o.bank_ref].tier == tier]
    correct = sum(o.verdict == "true_positive" for o in at_tier)
    wrong = sum(o.verdict == "false_positive" for o in at_tier)
    committed = correct + wrong
    return {
        "tier": tier,
        "committed": committed,
        "correct": correct,
        "false_positives": wrong,
        "precision": (correct / committed) if committed else None,
    }


def score_run(
    conn: Db,
    run_id: str,
    golden: list[GoldenCase],
    population: list[GoldenCase],
) -> dict[str, Any]:
    matches = load_run_matches(conn, run_id)
    golden_report = score(golden, matches)
    population_report = score(population, matches)

    row = conn.execute(
        "select status, cost_total_micro, model_version, prompt_version, "
        "config_snapshot->>'adjudicator' as adjudicator from runs where id = %s",
        (run_id,),
    ).fetchone()

    calls = conn.execute(
        "select count(*) as n, coalesce(sum(input_tokens), 0) as input_tokens, "
        "coalesce(sum(output_tokens), 0) as output_tokens, "
        "coalesce(sum(cost_micro), 0) as cost_micro, "
        "coalesce(percentile_disc(0.5) within group (order by latency_ms), 0) as p50, "
        "coalesce(percentile_disc(0.95) within group (order by latency_ms), 0) as p95, "
        "coalesce(sum((response->'usage'->>'cache_read_tokens')::int), 0) as cache_read, "
        "coalesce(sum((response->'usage'->>'cache_write_tokens')::int), 0) as cache_write "
        "from llm_calls where run_id = %s",
        (run_id,),
    ).fetchone()

    # What the model actually answered, before the confidence threshold is
    # applied. The share of `insufficient_evidence` is the restraint the whole
    # design is arguing for, and it is invisible in a precision number.
    mix = conn.execute(
        "select response->'decision'->>'decision' as decision, count(*) as n "
        "from llm_calls where run_id = %s group by 1 order by 2 desc",
        (run_id,),
    ).fetchall()

    lines = conn.execute(
        "select count(*) as n from decisions where run_id = %s", (run_id,)
    ).fetchone()
    line_count = int(lines["n"]) if lines else 0

    return {
        "run_id": run_id,
        "adjudicator": (row or {}).get("adjudicator"),
        "model_version": (row or {}).get("model_version"),
        "prompt_version": (row or {}).get("prompt_version"),
        "committed": len(matches),
        "by_tier": {
            str(tier): _tier_report(population_report, matches, tier)
            for tier in sorted({m.tier for m in matches.values()})
        },
        "model_calls": int(calls["n"]) if calls else 0,
        "input_tokens": int(calls["input_tokens"]) if calls else 0,
        "cache_read_tokens": int(calls["cache_read"]) if calls else 0,
        "cache_write_tokens": int(calls["cache_write"]) if calls else 0,
        "cache_hit_rate": (
            int(calls["cache_read"]) / int(calls["input_tokens"])
            if calls and int(calls["input_tokens"])
            else None
        ),
        "decision_mix": {str(r["decision"]): int(r["n"]) for r in mix},
        "output_tokens": int(calls["output_tokens"]) if calls else 0,
        "cost_micro": int(calls["cost_micro"]) if calls else 0,
        "cost_micro_per_1000_lines": (
            round(int(calls["cost_micro"]) * 1000 / line_count) if calls and line_count else 0
        ),
        # Real numbers at last: one model call per line means a distribution
        # exists, which it did not for the deterministic tiers.
        "latency_p50_ms": int(calls["p50"]) if calls else None,
        "latency_p95_ms": int(calls["p95"]) if calls else None,
        "golden": golden_report.as_dict(),
        "full_population": population_report.as_dict(),
    }
