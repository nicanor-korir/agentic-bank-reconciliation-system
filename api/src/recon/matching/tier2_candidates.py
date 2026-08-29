"""Tier 2 -- candidate generation. Retrieval only, no model.

Produces at most `candidate_limit` singles plus any pre-assembled subsets, from
four independent generators: an amount window, a counterparty/date window,
bounded subset-sum for split and batched settlements, and hybrid search over
both the open ledger and this tenant's previously resolved pairs.

Recall is the only thing that matters here. A candidate this tier fails to
surface is one Tier 3 can never choose, so the ceiling on everything downstream
is set right here -- which is why `recall@10` is reported per generator as well
as overall. Precision is Tier 3's problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from recon.config import MatchConfig
from recon.matching.subset_sum import Subset, find_subsets
from recon.matching.types import BankLine, LedgerEntry
from recon.retrieval.base import NarrativeIndex

# Where a candidate came from. Reported so a weak generator is visible rather
# than hidden inside an aggregate recall number.
BY_REFERENCE = "reference"
BY_AMOUNT = "amount_window"
BY_COUNTERPARTY = "counterparty_window"
BY_SUBSET = "subset_sum"
BY_HYBRID = "hybrid_open"
BY_HISTORY = "resolved_pair"


@dataclass(frozen=True, slots=True)
class Candidate:
    kind: str  # "single" | "subset"
    ledger_entry_ids: tuple[int, ...]
    doc_refs: tuple[str, ...]
    total_minor: int
    delta_minor: int
    sources: tuple[str, ...]
    score_milli: int  # ranking hint only, per mille; never a probability


@dataclass
class CandidateSet:
    bank_line_id: int
    bank_ref: str
    candidates: list[Candidate] = field(default_factory=list)
    subset_truncated: bool = False

    @property
    def doc_ref_sets(self) -> list[frozenset[str]]:
        return [frozenset(c.doc_refs) for c in self.candidates]


def _tolerance(amount_minor: int, config: MatchConfig) -> int:
    return max(
        amount_minor * config.amount_tolerance_bps // 10_000,
        config.amount_tolerance_floor_minor,
    )


def _within_days(line: BankLine, entry: LedgerEntry, days: int) -> bool:
    return abs((line.value_date - entry.entry_date).days) <= days


def _subset_score(subset: Subset) -> int:
    """Rank subsets by exactness first, then by whether they hang together.

    Needed because arithmetic alone is not decisive: when one managing agent
    remits twice in a month, several combinations of their invoices sum to the
    same total exactly, and the labelled one was losing a lexicographic
    tie-break. Same-day issuance is the signal that separates a real remittance
    from a coincidence of amounts.
    """
    if subset.delta_minor != 0:
        return 700 if subset.is_cohort else 650
    return 940 if subset.is_cohort else 860


def generate(
    lines: list[BankLine],
    entries: list[LedgerEntry],
    index: NarrativeIndex,
    config: MatchConfig,
    tenant: str,
    claimed: set[int] | None = None,
) -> dict[int, CandidateSet]:
    claimed = claimed or set()
    open_entries = [e for e in entries if e.status == "open" and e.id not in claimed]
    by_id = {e.id: e for e in open_entries}

    by_counterparty: dict[tuple[str, str], list[LedgerEntry]] = {}
    for entry in open_entries:
        if entry.counterparty:
            by_counterparty.setdefault((entry.side, entry.counterparty), []).append(entry)

    out: dict[int, CandidateSet] = {}
    for line in lines:
        out[line.id] = _for_line(line, open_entries, by_id, by_counterparty, index, config, tenant)
    return out


def _for_line(
    line: BankLine,
    open_entries: list[LedgerEntry],
    by_id: dict[int, LedgerEntry],
    by_counterparty: dict[tuple[str, str], list[LedgerEntry]],
    index: NarrativeIndex,
    config: MatchConfig,
    tenant: str,
) -> CandidateSet:
    result = CandidateSet(bank_line_id=line.id, bank_ref=line.bank_ref)
    tolerance = _tolerance(line.abs_minor, config)
    same_side = [e for e in open_entries if e.side == line.side]

    # Scores are ranking hints only; nothing downstream treats them as
    # probabilities. Tier 3 sees the candidates, not the arithmetic. Integer
    # per mille, so no float ever meets a monetary value.
    scored: dict[int, tuple[int, set[str]]] = {}

    def note(entry_id: int, score_milli: int, source: str) -> None:
        prev, sources = scored.get(entry_id, (0, set()))
        sources.add(source)
        scored[entry_id] = (max(prev, score_milli), sources)

    # 1. Amount window, inside a generous date window.
    for entry in same_side:
        if not _within_days(line, entry, config.tier2_date_window_days):
            continue
        delta = abs(entry.amount_minor - line.abs_minor)
        if delta <= tolerance:
            # Exact amounts rank top; the penalty grows with the gap.
            note(entry.id, 1000 - (delta * 200) // (tolerance + 1), BY_AMOUNT)

    # 2. Same counterparty, any amount, inside the date window. Catches partial
    #    payments and fee-netted receipts, where the amount deliberately differs.
    #    Scored by how much of the open item the payment covers, on a scale
    #    wide enough to separate the two cases that land here. A receipt short
    #    by a wire fee covers ~99% of its invoice and is near-certain; a partial
    #    payment covers half and is a guess. A flat score for both meant an
    #    exact-sum subset outranked a fee-netted single and pushed the correct
    #    answer out of the ten -- it cost 10 of 13 fee-netted cases.
    if line.counterparty:
        for entry in by_counterparty.get((line.side, line.counterparty), ()):
            if _within_days(line, entry, config.tier2_date_window_days):
                gap = abs(entry.amount_minor - line.abs_minor)
                penalty = min(450, gap * 900 // max(entry.amount_minor, 1))
                note(entry.id, 950 - penalty, BY_COUNTERPARTY)

    # 3. Hybrid search over open item descriptions.
    for hit in index.search_open_items(tenant, line.narrative, line.side, config.candidate_limit):
        if hit.ledger_entry_id in by_id:
            note(hit.ledger_entry_id, min(hit.score_milli, 950), BY_HYBRID)

    # 4. This tenant's resolved history. A previous human decision names the
    #    counterparty behind an opaque narrative; that counterparty's open
    #    items become candidates this time round without anyone re-deciding.
    for pair in index.search_resolved_pairs(tenant, line.narrative, config.candidate_limit):
        for entry in by_counterparty.get((line.side, pair.counterparty), ()):
            if _within_days(line, entry, config.tier2_date_window_days):
                note(entry.id, min(pair.score_milli, 900), BY_HISTORY)

    raw: list[Candidate] = []

    for entry_id, (score_milli, sources) in scored.items():
        entry = by_id[entry_id]
        raw.append(
            Candidate(
                kind="single",
                ledger_entry_ids=(entry.id,),
                doc_refs=(entry.doc_ref,) if entry.doc_ref else (),
                total_minor=entry.amount_minor,
                delta_minor=entry.amount_minor - line.abs_minor,
                sources=tuple(sorted(sources)),
                score_milli=score_milli,
            )
        )

    # 5. Subsets. Deep over a counterparty-scoped pool, shallow over an open
    #    one -- see subset_sum for why the two scopes exist. Enough subsets are
    #    requested to compete for places in the final list: one agent can remit
    #    twice in a month, so several *exact* combinations can exist and the
    #    labelled one must not be crowded out by its siblings.
    for pool, max_items, cap in (
        (
            [
                e
                for e in by_counterparty.get((line.side, line.counterparty or ""), ())
                if _within_days(line, e, config.tier2_date_window_days)
            ],
            config.subset_max_items,
            config.subset_max_pool,
        ),
        (
            [
                e
                for e in same_side
                if _within_days(line, e, config.tier1_date_window_days)
                and e.amount_minor < line.abs_minor
            ],
            config.subset_unscoped_max_items,
            config.subset_unscoped_max_pool,
        ),
    ):
        if len(pool) < 2:
            continue
        search = find_subsets(
            sorted(pool, key=lambda e: (e.amount_minor, e.doc_ref or "", e.id))[:cap],
            line.abs_minor,
            tolerance,
            max_items=max_items,
            max_results=config.candidate_limit,
        )
        result.subset_truncated = result.subset_truncated or search.truncated
        for subset in search.subsets:
            raw.append(
                Candidate(
                    kind="subset",
                    ledger_entry_ids=subset.ids,
                    doc_refs=subset.doc_refs,
                    total_minor=subset.total_minor,
                    delta_minor=subset.delta_minor,
                    sources=(BY_SUBSET,),
                    score_milli=_subset_score(subset),
                )
            )

    # One ranked list, capped once. The brief asks for at most ten candidates
    # per line; capping singles and subsets separately quietly returns twenty,
    # and capping them independently lets a weak single displace the correct
    # combination for a batched settlement.
    best: dict[frozenset[int], Candidate] = {}
    for candidate in raw:
        key = frozenset(candidate.ledger_entry_ids)
        current = best.get(key)
        if current is None or candidate.score_milli > current.score_milli:
            best[key] = candidate

    result.candidates = sorted(
        best.values(),
        key=lambda c: (-c.score_milli, abs(c.delta_minor), c.doc_refs),
    )[: config.candidate_limit]

    return result
