"""recall@10 for Tier 2.

The brief calls this the ceiling on everything downstream, and it is: a
candidate retrieval never surfaces is one adjudication can never choose. If
this number is 0.90, then 10% of the escalated population is unreachable no
matter how good the model is.

Scoring is set-exact and deliberately so (NOTES.md 0.4d). For a batched
settlement the correct answer is a *combination*, and a candidate list holding
all six invoices individually while never proposing the six together has not
actually found the answer. Singles and pre-assembled subsets are both eligible;
what counts is whether the labelled document set appears as one candidate.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from recon.evals.golden import GoldenCase
from recon.matching.tier2_candidates import CandidateSet


@dataclass(frozen=True, slots=True)
class RetrievalOutcome:
    bank_ref: str
    case_class: str
    found: bool
    rank: int | None
    sources: tuple[str, ...]
    candidates_offered: int
    subset_truncated: bool


@dataclass
class RetrievalReport:
    k: int
    outcomes: list[RetrievalOutcome] = field(default_factory=list)

    @property
    def evaluated(self) -> int:
        return len(self.outcomes)

    @property
    def found(self) -> int:
        return sum(o.found for o in self.outcomes)

    @property
    def recall_at_k(self) -> float | None:
        return self.found / self.evaluated if self.evaluated else None

    @property
    def mean_candidates(self) -> float:
        if not self.outcomes:
            return 0.0
        return sum(o.candidates_offered for o in self.outcomes) / len(self.outcomes)

    @property
    def truncated_searches(self) -> int:
        return sum(o.subset_truncated for o in self.outcomes)

    def by_class(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for o in self.outcomes:
            bucket = out.setdefault(o.case_class, {"found": 0, "missed": 0})
            bucket["found" if o.found else "missed"] += 1
        return dict(sorted(out.items()))

    def by_source(self) -> dict[str, int]:
        """Which generator surfaced the winning candidate.

        A generator that never wins is either redundant or broken, and an
        aggregate recall number hides both.
        """
        counter: Counter[str] = Counter()
        for o in self.outcomes:
            if o.found:
                for source in o.sources:
                    counter[source] += 1
        return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))

    def as_dict(self) -> dict[str, object]:
        return {
            "k": self.k,
            "evaluated": self.evaluated,
            "found": self.found,
            "recall_at_k": self.recall_at_k,
            "mean_candidates_offered": round(self.mean_candidates, 2),
            "truncated_searches": self.truncated_searches,
            "by_class": self.by_class(),
            "winning_source_counts": self.by_source(),
            "missed": sorted(o.bank_ref for o in self.outcomes if not o.found),
        }


def score_retrieval(
    cases: list[GoldenCase],
    candidate_sets: dict[int, CandidateSet],
    line_ids_by_ref: dict[str, int],
    k: int,
) -> RetrievalReport:
    report = RetrievalReport(k=k)

    for case in sorted(cases, key=lambda c: c.bank_ref):
        # A line with no correct answer cannot contribute to recall; whether
        # retrieval offered it anything is Tier 3's problem, not this metric's.
        if not case.expected_doc_refs:
            continue
        line_id = line_ids_by_ref.get(case.bank_ref)
        if line_id is None:
            continue
        candidate_set = candidate_sets.get(line_id)
        if candidate_set is None:
            continue

        rank: int | None = None
        sources: tuple[str, ...] = ()
        for position, candidate in enumerate(candidate_set.candidates, start=1):
            if frozenset(candidate.doc_refs) == case.expected_doc_refs:
                rank, sources = position, candidate.sources
                break

        report.outcomes.append(
            RetrievalOutcome(
                bank_ref=case.bank_ref,
                case_class=case.case_class,
                found=rank is not None,
                rank=rank,
                sources=sources,
                candidates_offered=len(candidate_set.candidates),
                subset_truncated=candidate_set.subset_truncated,
            )
        )
    return report
