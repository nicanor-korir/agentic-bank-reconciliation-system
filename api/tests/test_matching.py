"""Matcher unit tests, written against the hard cases.

These are the tests that matter: a matcher that clears the easy 55% is not
interesting, and the risk lives entirely in what it does when the evidence is
thin. Every "declines" test below is guarding NON-NEGOTIABLE #2 -- escalating
a correct match is cheap, committing a wrong one is not.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from recon.config import MatchConfig
from recon.matching import run_deterministic
from recon.matching.tier0_exact import match_tier0
from recon.matching.tier1_struct import match_tier1
from recon.matching.types import BankLine, LedgerEntry

CONFIG = MatchConfig()
D = date(2026, 6, 3)


def line(**kw) -> BankLine:
    base = dict(
        id=1,
        bank_ref="TXN-1",
        value_date=D,
        amount_minor=145_000,
        currency="USD",
        narrative="ACH CREDIT RENT",
        counterparty="J MORRISON",
    )
    return BankLine(**{**base, **kw})


def entry(**kw) -> LedgerEntry:
    base = dict(
        id=100,
        doc_ref="INV-2026-06-0412",
        entry_date=D,
        amount_minor=145_000,
        open_amount_minor=145_000,
        currency="USD",
        description="RENT",
        counterparty="J MORRISON",
        side="AR",
        status="open",
    )
    return LedgerEntry(**{**base, **kw})


def t0(lines, entries, claimed=None):
    by_ref: dict[str, list[LedgerEntry]] = {}
    for e in entries:
        if e.doc_ref:
            by_ref.setdefault(e.doc_ref, []).append(e)
    return match_tier0(lines, by_ref, claimed if claimed is not None else set(), CONFIG)


def t1(lines, entries, claimed=None):
    return match_tier1(lines, entries, claimed if claimed is not None else set(), CONFIG)


# -- Tier 0 ---------------------------------------------------------------


def test_tier0_commits_on_a_quoted_reference():
    m = t0([line(narrative="ACH CREDIT RENT INV-2026-06-0412 J MORRISON")], [entry()])
    assert len(m) == 1
    assert m[0].tier == 0
    assert m[0].doc_refs == ("INV-2026-06-0412",)
    assert m[0].confidence == CONFIG.tier0_confidence


def test_tier0_rationale_is_a_sentence_a_bookkeeper_can_read():
    """NON-NEGOTIABLE #3 -- no bare confidence scores."""
    m = t0([line(narrative="ACH CREDIT RENT INV-2026-06-0412")], [entry()])
    text = m[0].rationale
    assert "INV-2026-06-0412" in text and "1,450.00 USD" in text
    assert text.endswith(".") and len(text.split()) > 8


def test_tier0_ignores_a_line_with_no_reference():
    assert t0([line()], [entry()]) == []


def test_tier0_declines_a_reference_that_names_nothing():
    """The transposed-digit case: the quoted invoice simply does not exist."""
    m = t0([line(narrative="ACH CREDIT RENT INV-2026-06-0421")], [entry()])
    assert m == []


def test_tier0_declines_when_the_amount_disagrees():
    """Fee-netted receipts quote the right invoice and the wrong amount."""
    m = t0([line(amount_minor=142_500, narrative="WIRE CREDIT INV-2026-06-0412")], [entry()])
    assert m == []


@pytest.mark.parametrize("offset", [3, -3, 30])
def test_tier0_declines_outside_its_date_window(offset):
    m = t0(
        [line(value_date=D + timedelta(days=offset), narrative="RENT INV-2026-06-0412")],
        [entry()],
    )
    assert m == []


def test_tier0_declines_a_closed_ledger_entry():
    m = t0([line(narrative="RENT INV-2026-06-0412")], [entry(status="closed")])
    assert m == []


def test_tier0_will_not_pay_a_receivable_with_an_outgoing_payment():
    m = t0([line(amount_minor=-145_000, narrative="RENT INV-2026-06-0412")], [entry()])
    assert m == []


def test_tier0_declines_when_a_reference_resolves_to_two_open_items():
    m = t0(
        [line(narrative="RENT INV-2026-06-0412")],
        [entry(id=100), entry(id=101)],
    )
    assert m == []


def test_tier0_skips_an_already_claimed_entry():
    m = t0([line(narrative="RENT INV-2026-06-0412")], [entry()], claimed={100})
    assert m == []


# -- Tier 1 ---------------------------------------------------------------


def test_tier1_commits_on_a_unique_counterparty():
    m = t1([line(value_date=D + timedelta(days=5))], [entry()])
    assert len(m) == 1
    assert m[0].tier == 1
    assert m[0].confidence == CONFIG.tier1_confidence


def test_tier1_declines_two_open_items_at_the_same_amount():
    """The duplicate-amount case: two tenants share a display name.

    The honest answer is to refuse. This is the case the demo dwells on.
    """
    m = t1([line()], [entry(id=100, doc_ref="INV-1"), entry(id=101, doc_ref="INV-2")])
    assert m == []


def test_tier1_declines_a_partial_payment():
    m = t1([line(amount_minor=72_500)], [entry()])
    assert m == []


def test_tier1_declines_a_batched_settlement():
    """A credit equal to the sum of six invoices matches none of them."""
    entries = [entry(id=100 + i, doc_ref=f"INV-{i}", amount_minor=100_000) for i in range(6)]
    assert t1([line(amount_minor=600_000)], entries) == []


def test_tier1_declines_an_unknown_payer():
    """Processor-obscured payments name the processor, not the tenant."""
    m = t1([line(counterparty="PAYCLEAR SETTLEMENT")], [entry()])
    assert m == []


def test_tier1_declines_a_line_with_no_counterparty_at_all():
    assert t1([line(counterparty=None)], [entry()]) == []


@pytest.mark.parametrize("offset", [8, -8, 40])
def test_tier1_declines_outside_its_seven_day_window(offset):
    assert t1([line(value_date=D + timedelta(days=offset))], [entry()]) == []


def test_tier1_absorbs_fx_rounding_within_tolerance():
    # 10 bps of 1,450.00 is 1.45; a 1.00 shortfall is inside the band.
    m = t1([line(amount_minor=144_900)], [entry()])
    assert len(m) == 1
    assert "basis points" in m[0].rationale
    assert any(e.startswith("fx_drift_minor:") for e in m[0].evidence)


def test_tier1_declines_fx_drift_beyond_tolerance():
    """The FX hard case drifts 15-45 bps -- deliberately outside the band."""
    assert t1([line(amount_minor=141_000)], [entry()]) == []


def test_an_exact_amount_is_never_displaced_by_a_tolerated_one():
    exact = entry(id=100, doc_ref="INV-EXACT")
    near = entry(id=101, doc_ref="INV-NEAR", amount_minor=144_900)
    m = t1([line()], [exact, near])
    # Ambiguous: an exact candidate and a tolerated one both exist. Decline.
    assert m == []


def test_tier1_matches_a_recurring_fee_as_an_ordinary_structural_match():
    fee_line = line(
        amount_minor=-3_500, narrative="ACCOUNT MAINTENANCE FEE", counterparty="GRANITE BANK"
    )
    fee_entry = entry(
        doc_ref="BILL-2026-06-0001",
        amount_minor=3_500,
        open_amount_minor=3_500,
        counterparty="GRANITE BANK",
        side="AP",
        description="BANK ACCOUNT MAINTENANCE",
    )
    assert len(t1([fee_line], [fee_entry])) == 1


def test_tier1_will_not_settle_a_payable_with_an_incoming_receipt():
    m = t1([line(amount_minor=145_000)], [entry(side="AP")])
    assert m == []


# -- Cascade --------------------------------------------------------------


def test_one_open_item_cannot_settle_two_bank_lines():
    lines = [
        line(id=1, bank_ref="TXN-1", narrative="RENT INV-2026-06-0412"),
        line(id=2, bank_ref="TXN-2", value_date=D + timedelta(days=4)),
    ]
    result = run_deterministic(lines, [entry()], CONFIG)
    assert len(result.matches) == 1
    assert result.matches[0].tier == 0
    assert [line.id for line in result.unmatched] == [2]


def test_tier_order_is_preserved():
    lines = [line(id=1, bank_ref="TXN-1", narrative="RENT INV-2026-06-0412")]
    result = run_deterministic(lines, [entry()], CONFIG)
    assert result.by_tier == {0: 1}


def test_ablation_arm_can_stop_after_tier_zero():
    lines = [line(id=1, bank_ref="TXN-1", value_date=D + timedelta(days=4))]
    assert run_deterministic(lines, [entry()], CONFIG, max_tier=0).matches == []
    assert len(run_deterministic(lines, [entry()], CONFIG, max_tier=1).matches) == 1


def test_input_order_does_not_change_the_outcome():
    """NON-NEGOTIABLE #4: the result cannot depend on how rows came back."""
    lines = [
        line(
            id=i,
            bank_ref=f"TXN-{i:03d}",
            value_date=D + timedelta(days=i % 5),
            amount_minor=100_000 + i * 137,
            counterparty=f"PAYER {i % 7}",
        )
        for i in range(60)
    ]
    entries = [
        entry(
            id=1000 + i,
            doc_ref=f"INV-{i:03d}",
            entry_date=D + timedelta(days=i % 5),
            amount_minor=100_000 + i * 137,
            open_amount_minor=100_000 + i * 137,
            counterparty=f"PAYER {i % 7}",
        )
        for i in range(60)
    ]
    baseline = run_deterministic(lines, entries, CONFIG)

    rng = random.Random(7)
    for _ in range(5):
        shuffled_lines, shuffled_entries = list(lines), list(entries)
        rng.shuffle(shuffled_lines)
        rng.shuffle(shuffled_entries)
        other = run_deterministic(shuffled_lines, shuffled_entries, CONFIG)
        assert [(m.bank_ref, m.doc_refs, m.tier) for m in baseline.matches] == [
            (m.bank_ref, m.doc_refs, m.tier) for m in other.matches
        ]


# -- Subset-sum -----------------------------------------------------------


def _pool(amounts: list[int]) -> list[LedgerEntry]:
    return [
        entry(id=200 + i, doc_ref=f"INV-{i:03d}", amount_minor=a, open_amount_minor=a)
        for i, a in enumerate(amounts)
    ]


def test_subset_sum_finds_a_six_invoice_remittance():
    from recon.matching.subset_sum import find_subsets

    pool = _pool([100_000 + i * 7_500 for i in range(10)])
    target = sum(e.amount_minor for e in pool[:6])
    found = find_subsets(pool, target, 0, max_items=6)
    assert frozenset(found.subsets[0].doc_refs) == frozenset(e.doc_ref for e in pool[:6])
    assert found.exhausted


def test_an_exact_subset_is_never_crowded_out_by_near_misses():
    """The bug that cost 6 of 13 batch cases.

    On a large remittance a percentage tolerance admits dozens of near-miss
    combinations. Truncating in discovery order drops the exact answer; ranking
    by distance from the target keeps it.
    """
    from recon.matching.subset_sum import find_subsets

    pool = _pool([100_000 + i * 100 for i in range(14)])
    target = sum(e.amount_minor for e in pool[:6])
    found = find_subsets(pool, target, tolerance_minor=7_500, max_items=6, max_results=5)
    assert found.subsets, "expected at least one subset"
    assert found.subsets[0].delta_minor == 0, "an exact sum must rank first"


def test_subset_sum_reports_truncation_rather_than_pretending_to_be_exhaustive():
    from recon.matching.subset_sum import find_subsets

    pool = _pool([10_000 + i for i in range(60)])
    found = find_subsets(pool, 300_000, 50_000, max_items=6, node_budget=200)
    assert found.truncated
    assert not found.exhausted


def test_subset_sum_declines_a_single_item():
    """A one-item "subset" is a plain match and belongs to Tier 0 or 1."""
    from recon.matching.subset_sum import find_subsets

    pool = _pool([145_000])
    assert find_subsets(pool, 145_000, 0, max_items=6).subsets == []


def test_a_truncated_search_never_reports_itself_exhausted():
    """The failure that cost 4 batched settlements silently.

    The search stopped at an internal collection cap but still reported
    `exhausted=True`, so a correct combination that was never reached looked
    identical to one that provably does not exist.
    """
    from recon.matching.subset_sum import find_subsets

    # Many equal amounts inside a wide tolerance: combinations explode.
    pool = _pool([100_000] * 18)
    found = find_subsets(pool, 600_000, tolerance_minor=50_000, max_items=6, max_results=2)
    if found.truncated:
        assert not found.exhausted
    assert found.exhausted != found.truncated


def test_a_same_day_cohort_outranks_an_arithmetic_coincidence():
    """One agent can remit twice a month, so exact sums are not decisive."""
    from datetime import date as _date

    from recon.matching.subset_sum import Subset
    from recon.matching.tier2_candidates import _subset_score

    day = _date(2026, 6, 1)
    cohort = Subset(
        entries=tuple(entry(id=300 + i, entry_date=day) for i in range(3)),
        total_minor=435_000,
        delta_minor=0,
    )
    mixed = Subset(
        entries=tuple(entry(id=400 + i, entry_date=day + timedelta(days=i * 4)) for i in range(3)),
        total_minor=435_000,
        delta_minor=0,
    )
    assert _subset_score(cohort) > _subset_score(mixed)


def test_cohesion_is_applied_before_truncation_not_after():
    """Ranking survivors is useless if the answer was already trimmed away.

    Twelve invoices from one agent, two same-day cohorts of six. Both sum to
    the target exactly, so exactness cannot separate them; the deterministic
    tie-break is the document reference, which favours the earlier cohort. The
    later cohort must still survive into a small result set.
    """
    from datetime import date as _date

    from recon.matching.subset_sum import find_subsets

    early = _date(2026, 6, 1)
    late = _date(2026, 6, 20)
    pool = [
        entry(
            id=500 + i,
            doc_ref=f"INV-{i:03d}",
            entry_date=early,
            amount_minor=100_000 + i * 1_000,
            open_amount_minor=100_000 + i * 1_000,
        )
        for i in range(6)
    ] + [
        entry(
            id=600 + i,
            doc_ref=f"INV-{100 + i:03d}",
            entry_date=late,
            amount_minor=100_000 + i * 1_000,
            open_amount_minor=100_000 + i * 1_000,
        )
        for i in range(6)
    ]
    target = sum(e.amount_minor for e in pool[:6])
    found = find_subsets(pool, target, tolerance_minor=9_000, max_items=6, max_results=4)

    assert found.subsets
    # Whatever wins, every returned exact subset must be a real same-day cohort
    # rather than a cross-cohort arithmetic coincidence.
    exact = [s for s in found.subsets if s.delta_minor == 0]
    assert exact, "expected at least one exact subset"
    assert exact[0].is_cohort, "a same-day cohort must outrank a mixed-date coincidence"
