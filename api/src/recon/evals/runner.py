"""`make eval` -- score the golden set, print the table, write the report.

Two populations are reported and they answer different questions. The golden
300 give precision, recall and the false-positive count. The full 1,200-line
month gives the tier breakdown and the auto-match rate -- the number that goes
in the client deck.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from recon.config import Settings
from recon.db import connect, load_bank_lines, load_ledger_entries
from recon.evals.ablation import ARMS, run_arm
from recon.evals.golden import GoldenCase, load_all_cases, load_golden
from recon.evals.metrics import Report, score
from recon.evals.retrieval_metrics import score_retrieval
from recon.evals.run_scoring import score_run
from recon.matching import run_deterministic
from recon.matching.tier2_candidates import generate as generate_candidates
from recon.matching.types import BankLine, LedgerEntry
from recon.retrieval.base import NarrativeIndex, NullIndex, ResolvedPair

DEMO_PERIOD = "2026-06"
FEEDBACK_PERIOD = "2026-07"
PRECISION_TARGET = 0.995


@dataclass
class ArmResult:
    key: str
    label: str
    report: Report
    # The manifest labels every line, not just the golden 300, so the
    # false-positive count can be stated over the whole month. "Zero wrong
    # commits in 300 sampled lines" is a much weaker claim than "zero in
    # 1,200", and the second one is the one a buyer will ask for.
    population_report: Report
    tier_counts: dict[int, int]
    population: int
    auto_matched: int
    wall_ms: float

    @property
    def auto_match_rate(self) -> float:
        return self.auto_matched / self.population if self.population else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "population": self.population,
            "auto_matched": self.auto_matched,
            "auto_match_rate": self.auto_match_rate,
            "tier_counts": {str(k): v for k, v in sorted(self.tier_counts.items())},
            "wall_ms": round(self.wall_ms, 1),
            "ms_per_1000_lines": round(self.wall_ms * 1000 / self.population, 1)
            if self.population
            else None,
            # Deterministic tiers do one pass over an index; there is no
            # per-line request to take a distribution of. These become real
            # numbers in Phase 4, when Tier 3 issues one model call per line.
            "latency_p50_ms": None,
            "latency_p95_ms": None,
            "cost_micro_per_1000_lines": 0,
            "golden": self.report.as_dict(),
            "full_population": self.population_report.as_dict(),
        }


def _fmt(value: float | None, spec: str = ".4f") -> str:
    return "n/a" if value is None else format(value, spec)


def run_eval(settings: Settings, run_id: str | None = None) -> dict[str, Any]:
    data_dir = Path(settings.recon_data_dir)
    cases = load_golden(data_dir)
    all_cases = load_all_cases(data_dir, DEMO_PERIOD)

    with connect() as conn:
        lines = load_bank_lines(conn, settings.recon_tenant, DEMO_PERIOD)
        entries = load_ledger_entries(conn, settings.recon_tenant)

    if not lines:
        raise RuntimeError(
            f"no bank lines for tenant {settings.recon_tenant!r} in {DEMO_PERIOD}. "
            f"Run `make seed` first."
        )

    results: list[ArmResult] = []
    for arm in ARMS:
        started = time.perf_counter()
        matches, tier_counts, unmatched = run_arm(arm, lines, entries, settings.match)
        wall_ms = (time.perf_counter() - started) * 1000
        results.append(
            ArmResult(
                key=arm.key,
                label=arm.label,
                report=score(cases, matches),
                population_report=score(all_cases, matches),
                tier_counts=tier_counts,
                population=len(lines),
                auto_matched=len(lines) - unmatched,
                wall_ms=wall_ms,
            )
        )

    retrieval = _run_retrieval_arms(settings, lines, entries, all_cases)
    feedback = _run_feedback_arm(settings, entries)

    # The full cascade is scored from a real run's committed decisions rather
    # than simulated: Tier 3 costs money and its answers are recorded, not
    # recomputed.
    cascade: dict[str, Any] | None = None
    if run_id:
        with connect() as conn:
            cascade = score_run(conn, run_id, cases, all_cases)

    return {
        "git_sha": settings.git_sha,
        "git_dirty": settings.git_dirty,
        "tenant": settings.recon_tenant,
        "period": DEMO_PERIOD,
        "seed": settings.recon_seed,
        "golden_set_size": len(cases),
        "population_size": len(all_cases),
        "config": json.loads(settings.match.model_dump_json()),
        "arms": [r.as_dict() for r in results],
        "retrieval": retrieval,
        "feedback_loop": feedback,
        "full_cascade": cascade,
    }


class _SplitIndex:
    """Open items from the live tenant, resolved pairs from a scratch tenant.

    Lets the feedback measurement add and drop history without touching the
    real index, and without paying to re-embed 1,776 open items on CPU just to
    answer one question.
    """

    def __init__(self, base: Any, open_tenant: str, pair_tenant: str) -> None:
        self._base = base
        self._open_tenant = open_tenant
        self._pair_tenant = pair_tenant

    def bind(self, bank_line_id: int) -> None:
        return None

    def flush(self) -> int:
        return 0

    def search_open_items(self, tenant: str, narrative: str, side: str, limit: int) -> Any:
        return self._base.search_open_items(self._open_tenant, narrative, side, limit)

    def search_resolved_pairs(self, tenant: str, narrative: str, limit: int) -> Any:
        return self._base.search_resolved_pairs(self._pair_tenant, narrative, limit)


def _run_feedback_arm(settings: Settings, entries: list[LedgerEntry]) -> dict[str, Any]:
    """Demo point 9, as a number rather than a claim.

    Measures Tier 2 recall on the following month's processor-obscured lines
    before and after this month's human corrections are written back. These
    lines carry no invoice reference and name the processor rather than the
    payer, and the credit arrives net of a processor fee -- so no window can
    reach them, and resolved history is the only route to a candidate.
    """
    data_dir = Path(settings.recon_data_dir)
    period = FEEDBACK_PERIOD
    cases = [c for c in load_all_cases(data_dir, period) if c.case_class == "h_feedback"]
    source_cases = [
        c for c in load_all_cases(data_dir, DEMO_PERIOD) if c.case_class == "h_feedback"
    ]
    if not cases or not source_cases:
        return {"available": False, "reason": "no feedback cases in the manifest"}

    with connect() as conn:
        next_month = load_bank_lines(conn, settings.recon_tenant, period)
        this_month = load_bank_lines(conn, settings.recon_tenant, DEMO_PERIOD)

    det = run_deterministic(next_month, entries, settings.match)
    line_ids = {line.bank_ref: line.id for line in det.unmatched}
    scored = [c for c in cases if c.bank_ref in line_ids]

    narratives = {line.bank_ref: line.narrative for line in this_month}
    by_doc_ref = {e.doc_ref: e for e in entries if e.doc_ref}
    pairs = [
        ResolvedPair(
            bank_ref=c.bank_ref,
            narrative=narratives[c.bank_ref],
            counterparty=by_doc_ref[ref].counterparty or "",
            doc_ref=ref,
            amount_minor=0,
        )
        for c in source_cases
        if c.bank_ref in narratives
        for ref in sorted(c.expected_doc_refs)
        if ref in by_doc_ref
    ]

    scratch = f"{settings.recon_tenant}--eval-feedback"
    try:
        from recon.retrieval.weaviate_index import WeaviateIndex
        from recon.retrieval.weaviate_index import connect as weaviate_connect

        with weaviate_connect(settings) as client:
            base = WeaviateIndex(client, settings.match)
            base.clear(scratch)
            base.ensure_tenant(scratch)

            def measure() -> dict[str, Any]:
                index = _SplitIndex(base, settings.recon_tenant, scratch)
                sets = generate_candidates(
                    det.unmatched,
                    entries,
                    index,
                    settings.match,
                    settings.recon_tenant,
                    claimed=det.claimed_entry_ids,
                )
                report = score_retrieval(scored, sets, line_ids, settings.match.candidate_limit)
                return report.as_dict()

            before = measure()
            base.index_resolved_pairs(scratch, pairs)
            after = measure()
            base.clear(scratch)
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"[:200]}

    return {
        "available": True,
        "period": period,
        "corrections_written_back": len(pairs),
        "lines_scored": before["evaluated"],
        "before": before,
        "after": after,
    }


def _run_retrieval_arms(
    settings: Settings,
    lines: list[BankLine],
    entries: list[LedgerEntry],
    all_cases: list[GoldenCase],
) -> list[dict[str, Any]]:
    """Tier 2 recall@10 over what the deterministic tiers could not resolve.

    This is the ablation the brief cares about -- rules only versus rules plus
    retrieval -- measured on the population that actually reaches retrieval.
    Measuring it over the whole month would drown the signal in lines Tier 0
    already settled.
    """
    deterministic = run_deterministic(lines, entries, settings.match)
    unmatched = deterministic.unmatched
    line_ids = {line.bank_ref: line.id for line in unmatched}
    escalated = [c for c in all_cases if c.bank_ref in line_ids]

    def score_with(index: NarrativeIndex) -> dict[str, Any]:
        candidate_sets = generate_candidates(
            unmatched,
            entries,
            index,
            settings.match,
            settings.recon_tenant,
            claimed=deterministic.claimed_entry_ids,
        )
        report = score_retrieval(
            escalated, candidate_sets, line_ids, settings.match.candidate_limit
        )
        return {
            "available": True,
            "escalated_population": len(unmatched),
            **report.as_dict(),
        }

    arms: list[dict[str, Any]] = [
        {
            "key": "windows_only",
            "label": "Windows + subset-sum (no retrieval)",
            **score_with(NullIndex()),
        }
    ]

    try:
        from recon.retrieval.weaviate_index import WeaviateIndex
        from recon.retrieval.weaviate_index import connect as weaviate_connect

        with weaviate_connect(settings) as client:
            arms.append(
                {
                    "key": "hybrid",
                    "label": "Windows + subset-sum + hybrid retrieval",
                    **score_with(WeaviateIndex(client, settings.match)),
                }
            )
    except Exception as exc:
        # A missing vector store degrades the report, it does not fail the run.
        # Reported as unavailable rather than as a zero -- a zero would read as
        # "retrieval added nothing", which is a very different claim.
        arms.append(
            {
                "key": "hybrid",
                "label": "Windows + subset-sum + hybrid retrieval",
                "available": False,
                "reason": f"{type(exc).__name__}: {exc}"[:200],
            }
        )
    return arms


def print_report(payload: dict[str, Any]) -> None:
    arms: list[dict[str, Any]] = payload["arms"]

    print(
        f"\nEval  {payload['tenant']}  {payload['period']}  "
        f"git {payload['git_sha']}{' (dirty)' if payload['git_dirty'] else ''}"
    )
    print(f"golden set: {payload['golden_set_size']} labelled lines\n")

    print("Ablation -- full month")
    print(f"  {'arm':<38} {'auto-matched':>13} {'rate':>7} {'ms/1k':>8}")
    for a in arms:
        print(
            f"  {a['label']:<38} {a['auto_matched']:>7}/{a['population']:<5} "
            f"{a['auto_match_rate']:>6.1%} {a['ms_per_1000_lines']:>8.1f}"
        )

    print(f"\nFull month -- all {payload['population_size']} labelled lines")
    print(f"  {'arm':<38} {'prec':>7} {'recall':>7} {'FP':>4}")
    for a in arms:
        f = a["full_population"]
        print(
            f"  {a['label']:<38} {_fmt(f['precision']):>7} {_fmt(f['recall']):>7} "
            f"{f['false_positives']:>4}"
        )

    print(
        f"\nGolden set -- {payload['golden_set_size']} labelled lines "
        f"(deliberately enriched with hard cases)"
    )
    print(f"  {'arm':<38} {'prec':>7} {'recall':>7} {'FP':>4} {'missed':>7} {'escal':>7}")
    for a in arms:
        g = a["golden"]
        print(
            f"  {a['label']:<38} {_fmt(g['precision']):>7} {_fmt(g['recall']):>7} "
            f"{g['false_positives']:>4} {g['missed']:>7} {g['escalation_rate']:>6.1%}"
        )

    final = arms[-1]
    print(f"\nBy case class -- {final['label']}")
    print(f"  {'class':<26} {'correct':>8} {'wrong':>6} {'declined':>9} {'missed':>7}")
    for name, counts in final["golden"]["by_class"].items():
        print(
            f"  {name:<26} {counts['true_positive']:>8} {counts['false_positive']:>6} "
            f"{counts['correct_restraint']:>9} {counts['missed']:>7}"
        )

    for arm in payload.get("retrieval", []):
        if not arm.get("available"):
            print(f"\nTier 2 retrieval -- {arm['label']}: unavailable ({arm['reason']})")
            continue
        recall = arm["recall_at_k"]
        print(f"\nTier 2 -- {arm['label']}")
        print(f"  escalated population   {arm['escalated_population']}")
        print(f"  scored (known answer)  {arm['evaluated']}")
        print(
            f"  recall@{arm['k']}               "
            f"{'n/a' if recall is None else f'{recall:.4f}'}"
            f"   ({arm['found']}/{arm['evaluated']})"
        )
        print(f"  mean candidates        {arm['mean_candidates_offered']}")
        if arm["truncated_searches"]:
            print(f"  TRUNCATED subset searches: {arm['truncated_searches']}")
        if arm["winning_source_counts"]:
            sources = ", ".join(f"{k}={v}" for k, v in arm["winning_source_counts"].items())
            print(f"  winning sources        {sources}")
        misses = {k: v["missed"] for k, v in arm["by_class"].items() if v["missed"]}
        if misses:
            print(f"  missed by class        {misses}")

    cascade = payload.get("full_cascade")
    if cascade:
        print(
            f"\nFull cascade -- run {cascade['run_id'][:8]} "
            f"({cascade['adjudicator']}, {cascade['model_version']})"
        )
        g, f = cascade["golden"], cascade["full_population"]
        print(f"  {'population':<22} {'prec':>7} {'recall':>7} {'FP':>4}")
        print(
            f"  {'full month (1200)':<22} {_fmt(f['precision']):>7} "
            f"{_fmt(f['recall']):>7} {f['false_positives']:>4}"
        )
        print(
            f"  {'golden set (300)':<22} {_fmt(g['precision']):>7} "
            f"{_fmt(g['recall']):>7} {g['false_positives']:>4}"
        )
        print(f"\n  {'tier':<8} {'committed':>10} {'correct':>8} {'wrong':>6} {'precision':>10}")
        for tier, stats in cascade["by_tier"].items():
            print(
                f"  tier {tier:<3} {stats['committed']:>10} {stats['correct']:>8} "
                f"{stats['false_positives']:>6} {_fmt(stats['precision']):>10}"
            )
        print(f"\n  model calls            {cascade['model_calls']}")
        print(
            f"  tokens                 {cascade['input_tokens']:,} in / "
            f"{cascade['output_tokens']:,} out"
        )
        print(
            f"  cost                   ${cascade['cost_micro'] / 1e6:,.4f} "
            f"(${cascade['cost_micro_per_1000_lines'] / 1e6:,.4f} per 1,000 lines)"
        )
        print(
            f"  latency                p50 {cascade['latency_p50_ms']} ms, "
            f"p95 {cascade['latency_p95_ms']} ms"
        )
        if cascade.get("cache_hit_rate") is not None:
            print(
                f"  prompt cache           {cascade['cache_hit_rate']:.1%} of "
                f"input tokens served from cache"
            )
        if cascade.get("decision_mix"):
            mix = ", ".join(f"{k}={v}" for k, v in cascade["decision_mix"].items())
            print(f"  model answered         {mix}")
        wrong = f["wrong_commits"]
        if wrong:
            print(f"\n  {len(wrong)} WRONG COMMIT(S) by the full cascade:")
            for w in wrong[:10]:
                print(
                    f"    {w['bank_ref']} [{w['case_class']}] tier {w['tier']}: "
                    f"expected {w['expected_doc_refs']}, committed {w['actual_doc_refs']}"
                )

    fb = payload.get("feedback_loop") or {}
    if fb.get("available"):
        before, after = fb["before"], fb["after"]
        print(f"\nFeedback loop -- {fb['period']}, processor-obscured lines")
        print(
            f"  before any correction        recall@10 {_fmt(before['recall_at_k'])}"
            f"   ({before['found']}/{before['evaluated']})"
        )
        print(
            f"  after {fb['corrections_written_back']:>3} corrections        "
            f"recall@10 {_fmt(after['recall_at_k'])}"
            f"   ({after['found']}/{after['evaluated']})"
        )
    elif fb:
        print(f"\nFeedback loop: unavailable ({fb.get('reason')})")

    wrong = final["golden"]["wrong_commits"]
    if wrong:
        print(f"\n  {len(wrong)} WRONG COMMIT(S) -- non-negotiable #2 violated:")
        for w in wrong[:10]:
            print(
                f"    {w['bank_ref']} [{w['case_class']}] tier {w['tier']}: "
                f"expected {w['expected_decision']} {w['expected_doc_refs']}, "
                f"committed {w['actual_doc_refs']}"
            )
    else:
        print("\n  0 wrong commits.")
    print()


def check_regression(payload: dict[str, Any], evals_dir: Path) -> list[str]:
    """Compare against the committed baseline. Returns failure messages."""
    failures: list[str] = []
    final = payload["arms"][-1]["golden"]
    population = payload["arms"][-1]["full_population"]

    checks = [("golden set", final), ("full month", population)]
    cascade = payload.get("full_cascade")
    if cascade:
        checks += [
            ("full cascade, golden set", cascade["golden"]),
            ("full cascade, full month", cascade["full_population"]),
        ]

    for label, block in checks:
        if block["false_positives"] > 0:
            failures.append(
                f"{block['false_positives']} false positive(s) on the {label}; "
                f"the target is 0 (NON-NEGOTIABLE #2)"
            )

    precision = final["precision"]
    if precision is not None and precision < PRECISION_TARGET:
        failures.append(f"precision {precision:.4f} is below the {PRECISION_TARGET} target")

    baseline_path = evals_dir / "baseline.json"
    if not baseline_path.exists():
        return failures

    baseline = json.loads(baseline_path.read_text())
    prev = baseline["arms"][-1]["golden"]
    if precision is not None and prev["precision"] is not None and precision < prev["precision"]:
        failures.append(
            f"precision regressed: {precision:.4f} < baseline {prev['precision']:.4f} "
            f"(git {baseline.get('git_sha')})"
        )
    if final["true_positives"] < prev["true_positives"]:
        failures.append(
            f"true positives regressed: {final['true_positives']} < "
            f"{prev['true_positives']} (git {baseline.get('git_sha')})"
        )
    return failures


def write_report(payload: dict[str, Any], evals_dir: Path) -> Path:
    evals_dir.mkdir(parents=True, exist_ok=True)
    path = evals_dir / f"report-{payload['git_sha']}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path
