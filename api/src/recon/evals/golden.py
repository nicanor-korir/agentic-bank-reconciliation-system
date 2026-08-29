"""The golden set: 300 labelled bank lines drawn from the demo month.

Labels come from the generator manifest, so the eval measures the exact
dataset being demonstrated (NOTES.md 0.7). `expected_tier` is a design
expectation, not ground truth -- scoring uses `expected_decision` and
`expected_doc_refs` only, and the actual tier distribution is reported as a
diagnostic rather than asserted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MATCHABLE = frozenset({"match", "split_match"})


@dataclass(frozen=True, slots=True)
class GoldenCase:
    bank_ref: str
    period: str
    case_class: str
    expected_decision: str
    expected_doc_refs: frozenset[str]
    expected_tier: int

    @property
    def is_matchable(self) -> bool:
        """True when a correct system produces a committed match for this line."""
        return self.expected_decision in MATCHABLE

    @property
    def is_hard(self) -> bool:
        return self.case_class.startswith("h_")


def _to_case(raw: dict[str, Any]) -> GoldenCase:
    return GoldenCase(
        bank_ref=str(raw["bank_ref"]),
        period=str(raw["period"]),
        case_class=str(raw["case_class"]),
        expected_decision=str(raw["expected_decision"]),
        expected_doc_refs=frozenset(raw["expected_doc_refs"]),
        expected_tier=int(raw["expected_tier"]),
    )


def load_manifest(data_dir: Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((data_dir / "manifest.json").read_text())
    return payload


def load_golden(data_dir: Path) -> list[GoldenCase]:
    """The 300 labelled cases, in a stable order."""
    manifest = load_manifest(data_dir)
    golden = manifest["golden_set"]
    refs = set(golden["clean"]) | set(golden["hard"])
    cases = [_to_case(c) for c in manifest["cases"] if c["bank_ref"] in refs]
    if len(cases) != len(refs):
        missing = refs - {c.bank_ref for c in cases}
        raise ValueError(f"manifest golden_set names {len(missing)} unknown bank_refs")
    return sorted(cases, key=lambda c: c.bank_ref)


def load_all_cases(data_dir: Path, period: str | None = None) -> list[GoldenCase]:
    """Every labelled case, for whole-population diagnostics."""
    manifest = load_manifest(data_dir)
    cases = [_to_case(c) for c in manifest["cases"]]
    if period:
        cases = [c for c in cases if c.period == period]
    return sorted(cases, key=lambda c: c.bank_ref)
