"""Tier 2 candidate generation.

Recall is the only thing this tier is judged on: a candidate it fails to
surface is one adjudication can never choose. So these tests are mostly about
what must be *present*, and about the two things that must never be offered --
a closed item and one an earlier tier already claimed.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from recon.config import MatchConfig
from recon.matching.tier2_candidates import (
    BY_HISTORY,
    BY_SUBSET,
    generate,
)
from recon.matching.types import BankLine, LedgerEntry
from recon.retrieval.base import NullIndex, OpenItemHit, ResolvedPairHit

CONFIG = MatchConfig()
D = date(2026, 6, 10)
TENANT = "harborview"


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
        doc_ref="INV-001",
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


class FakeIndex:
    """Stands in for Weaviate so candidate logic is testable without a service."""

    def __init__(self, open_hits=(), pair_hits=()):
        self.open_hits = list(open_hits)
        self.pair_hits = list(pair_hits)

    def search_open_items(self, tenant, narrative, side, limit):
        return self.open_hits[:limit]

    def search_resolved_pairs(self, tenant, narrative, limit):
        return self.pair_hits[:limit]


def run(lines, entries, index=None, claimed=None):
    return generate(lines, entries, index or NullIndex(), CONFIG, TENANT, claimed=claimed)


def refs(candidate_set):
    return [frozenset(c.doc_refs) for c in candidate_set.candidates]


# -- basics ---------------------------------------------------------------


def test_never_offers_more_than_the_configured_limit():
    entries = [
        entry(id=200 + i, doc_ref=f"INV-{i:03d}", amount_minor=145_000 + i) for i in range(40)
    ]
    result = run([line()], entries)[1]
    assert len(result.candidates) <= CONFIG.candidate_limit


def test_an_exact_amount_ranks_first():
    entries = [
        entry(id=101, doc_ref="INV-NEAR", amount_minor=145_900),
        entry(id=102, doc_ref="INV-EXACT", amount_minor=145_000),
    ]
    assert refs(run([line()], entries)[1])[0] == {"INV-EXACT"}


def test_a_closed_item_is_never_a_candidate():
    result = run([line()], [entry(status="closed")])[1]
    assert result.candidates == []


def test_an_item_claimed_by_an_earlier_tier_is_never_offered():
    result = run([line()], [entry()], claimed={100})[1]
    assert result.candidates == []


def test_a_payable_is_not_offered_against_an_incoming_receipt():
    result = run([line(amount_minor=145_000)], [entry(side="AP")])[1]
    assert result.candidates == []


# -- the hard cases -------------------------------------------------------


def test_a_partial_payment_still_surfaces_its_invoice():
    """The amount deliberately disagrees, so only the payer connects them."""
    result = run([line(amount_minor=72_500)], [entry()])[1]
    assert {"INV-001"} in refs(result)


def test_a_fee_netted_receipt_surfaces_its_invoice():
    result = run([line(amount_minor=145_000 - 2_500)], [entry()])[1]
    assert {"INV-001"} in refs(result)


def test_a_near_exact_receipt_outranks_a_half_payment_candidate():
    """A receipt short by a wire fee is near-certain; a half payment is a guess.

    Scoring both flat let exact-sum subsets displace the fee-netted answer.
    """
    entries = [
        entry(id=101, doc_ref="INV-FEE", amount_minor=147_500),
        entry(id=102, doc_ref="INV-BIG", amount_minor=290_000),
    ]
    ordered = refs(run([line(amount_minor=145_000)], entries)[1])
    assert ordered.index({"INV-FEE"}) < ordered.index({"INV-BIG"})


def test_a_batched_settlement_is_offered_as_one_combined_candidate():
    """Six invoices individually is not the answer; the six together is."""
    entries = [
        entry(
            id=200 + i,
            doc_ref=f"INV-{i:03d}",
            amount_minor=100_000 + i * 5_000,
            open_amount_minor=100_000 + i * 5_000,
            counterparty="GRANITE LETTINGS",
        )
        for i in range(6)
    ]
    total = sum(e.amount_minor for e in entries)
    result = run([line(amount_minor=total, counterparty="GRANITE LETTINGS")], entries)[1]
    combined = frozenset(e.doc_ref for e in entries)
    assert combined in refs(result)
    subset = next(c for c in result.candidates if frozenset(c.doc_refs) == combined)
    assert subset.kind == "subset"
    assert BY_SUBSET in subset.sources
    assert subset.delta_minor == 0


# -- the feedback loop ----------------------------------------------------

PROCESSOR_LINE = dict(
    amount_minor=500_000,
    narrative="RTP CREDIT 774891 ORIG=PAYCLEAR SETTLEMENT SETL BATCH 8222",
    counterparty="PAYCLEAR SETTLEMENT",
)
COMMERCIAL_INVOICE = dict(
    id=300,
    doc_ref="INV-CEDAR",
    amount_minor=509_200,
    open_amount_minor=509_200,
    counterparty="CEDARBROOK HOLDINGS LLC",
)


def test_a_processor_obscured_payment_is_unreachable_without_history():
    """Names the processor, not the payer, and arrives net of a fee.

    No window can bridge that. If this ever starts passing without history,
    the feedback-loop demo has stopped proving anything.
    """
    result = run([line(**PROCESSOR_LINE)], [entry(**COMMERCIAL_INVOICE)])[1]
    assert {"INV-CEDAR"} not in refs(result)


def test_a_resolved_pair_makes_it_reachable():
    """The transferable signal is the counterparty, not the invoice.

    June's invoice is closed by July, so remembering the document would be
    useless; remembering who the narrative belongs to keeps paying off.
    """
    index = FakeIndex(
        pair_hits=[
            ResolvedPairHit(
                narrative="RTP CREDIT 774891 ORIG=PAYCLEAR SETTLEMENT SETL BATCH 8115",
                counterparty="CEDARBROOK HOLDINGS LLC",
                doc_ref="INV-PRIOR-MONTH",
                score_milli=880,
            )
        ]
    )
    result = run([line(**PROCESSOR_LINE)], [entry(**COMMERCIAL_INVOICE)], index)[1]
    assert {"INV-CEDAR"} in refs(result)
    candidate = next(c for c in result.candidates if c.doc_refs == ("INV-CEDAR",))
    assert BY_HISTORY in candidate.sources


def test_hybrid_hits_only_count_for_items_that_are_still_open():
    """A stale index must not resurrect a claimed or closed item."""
    index = FakeIndex(
        open_hits=[OpenItemHit(ledger_entry_id=100, doc_ref="INV-001", score_milli=990)]
    )
    assert run([line()], [entry(status="closed")], index)[1].candidates == []
    assert run([line()], [entry()], index, claimed={100})[1].candidates == []


# -- determinism ----------------------------------------------------------


@pytest.mark.parametrize("shuffle", [0, 1, 2])
def test_candidate_order_does_not_depend_on_input_order(shuffle):
    import random

    entries = [
        entry(
            id=200 + i,
            doc_ref=f"INV-{i:03d}",
            amount_minor=140_000 + i * 900,
            entry_date=D - timedelta(days=i % 5),
        )
        for i in range(20)
    ]
    baseline = refs(run([line()], entries)[1])
    shuffled = list(entries)
    random.Random(shuffle).shuffle(shuffled)
    assert refs(run([line()], shuffled)[1]) == baseline
