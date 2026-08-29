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
from recon.evals.golden import load_all_cases, load_golden
from recon.evals.metrics import Report, score

DEMO_PERIOD = "2026-06"
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


def run_eval(settings: Settings) -> dict[str, Any]:
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
    }


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

    for label, block in (("golden set", final), ("full month", population)):
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
