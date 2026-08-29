"""Scoring.

The headline number is the false-positive count, not precision. NON-NEGOTIABLE
#2 says auto-committing a wrong match is the cardinal sin, and a precision of
0.997 sounds fine right up until someone asks how many wrong journal matches
that is.

A committed match counts as correct only when the set of ledger documents
matches the label exactly. Partial credit would let a six-invoice batch score
5/6 and hide a real error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from recon.evals.golden import GoldenCase
from recon.matching.types import Match

# What the system did, judged against the label.
TRUE_POSITIVE = "true_positive"
FALSE_POSITIVE = "false_positive"
CORRECT_RESTRAINT = "correct_restraint"  # declined, and declining was right
MISSED = "missed"  # declined, but a correct match was available


@dataclass(frozen=True, slots=True)
class Outcome:
    bank_ref: str
    case_class: str
    verdict: str
    expected_decision: str
    expected_doc_refs: tuple[str, ...]
    actual_doc_refs: tuple[str, ...]
    tier: int | None
    confidence: Decimal | None

    @property
    def is_wrong_commit(self) -> bool:
        return self.verdict == FALSE_POSITIVE


@dataclass
class Report:
    outcomes: list[Outcome] = field(default_factory=list)

    # -- headline ---------------------------------------------------------

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def true_positives(self) -> int:
        return sum(o.verdict == TRUE_POSITIVE for o in self.outcomes)

    @property
    def false_positives(self) -> int:
        return sum(o.verdict == FALSE_POSITIVE for o in self.outcomes)

    @property
    def correct_restraint(self) -> int:
        return sum(o.verdict == CORRECT_RESTRAINT for o in self.outcomes)

    @property
    def missed(self) -> int:
        return sum(o.verdict == MISSED for o in self.outcomes)

    @property
    def committed(self) -> int:
        return self.true_positives + self.false_positives

    @property
    def matchable(self) -> int:
        return self.true_positives + self.false_positives + self.missed

    @property
    def precision(self) -> float | None:
        """None when nothing was committed -- 1.0 would flatter a no-op."""
        return None if not self.committed else self.true_positives / self.committed

    @property
    def recall(self) -> float | None:
        matchable = sum(o.expected_decision in {"match", "split_match"} for o in self.outcomes)
        return None if not matchable else self.true_positives / matchable

    @property
    def escalation_rate(self) -> float:
        escalated = self.correct_restraint + self.missed
        return escalated / self.total if self.total else 0.0

    def by_class(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for o in self.outcomes:
            bucket = out.setdefault(
                o.case_class,
                {TRUE_POSITIVE: 0, FALSE_POSITIVE: 0, CORRECT_RESTRAINT: 0, MISSED: 0},
            )
            bucket[o.verdict] += 1
        return dict(sorted(out.items()))

    def wrong_commits(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.is_wrong_commit]

    def as_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "committed": self.committed,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "correct_restraint": self.correct_restraint,
            "missed": self.missed,
            "precision": self.precision,
            "recall": self.recall,
            "escalation_rate": self.escalation_rate,
            "by_class": self.by_class(),
            "wrong_commits": [
                {
                    "bank_ref": o.bank_ref,
                    "case_class": o.case_class,
                    "expected_decision": o.expected_decision,
                    "expected_doc_refs": list(o.expected_doc_refs),
                    "actual_doc_refs": list(o.actual_doc_refs),
                    "tier": o.tier,
                }
                for o in self.wrong_commits()
            ],
        }


def score(cases: list[GoldenCase], matches: dict[str, Match]) -> Report:
    report = Report()
    for case in sorted(cases, key=lambda c: c.bank_ref):
        match = matches.get(case.bank_ref)

        if match is None:
            verdict = MISSED if case.is_matchable else CORRECT_RESTRAINT
            report.outcomes.append(
                Outcome(
                    bank_ref=case.bank_ref,
                    case_class=case.case_class,
                    verdict=verdict,
                    expected_decision=case.expected_decision,
                    expected_doc_refs=tuple(sorted(case.expected_doc_refs)),
                    actual_doc_refs=(),
                    tier=None,
                    confidence=None,
                )
            )
            continue

        correct = case.is_matchable and set(match.doc_refs) == case.expected_doc_refs
        report.outcomes.append(
            Outcome(
                bank_ref=case.bank_ref,
                case_class=case.case_class,
                verdict=TRUE_POSITIVE if correct else FALSE_POSITIVE,
                expected_decision=case.expected_decision,
                expected_doc_refs=tuple(sorted(case.expected_doc_refs)),
                actual_doc_refs=tuple(sorted(match.doc_refs)),
                tier=match.tier,
                confidence=match.confidence,
            )
        )
    return report
