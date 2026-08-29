"""Replay: recorded retrieval and the strict diff.

The claim is narrow and worth testing exactly: given the same inputs the system
reaches the same decisions, and the model was not re-rolled to make that true.
"""

from __future__ import annotations

import pytest

from recon.replay import Divergence, compare
from recon.retrieval.base import OpenItemHit, ResolvedPairHit
from recon.retrieval.recording import (
    ReplayIndex,
    RetrievalReplayMissError,
    query_hash,
)


def decision(bank_ref: str, **kw):
    base = dict(
        bank_line_id=1,
        tier=0,
        decision="match",
        confidence="1.000",
        rationale="INV-1 is quoted and the amount agrees.",
        ledger_entry_ids=[100],
    )
    return {bank_ref: {**base, **kw}}


# -- the diff -------------------------------------------------------------


def test_identical_runs_produce_no_divergence():
    original = decision("TXN-1")
    divergences, missing, extra = compare(original, dict(original))
    assert (divergences, missing, extra) == ([], [], [])


def test_a_different_ledger_entry_is_a_divergence():
    divergences, _, _ = compare(decision("TXN-1"), decision("TXN-1", ledger_entry_ids=[101]))
    assert len(divergences) == 1
    assert divergences[0].field == "ledger_entry_ids"


def test_a_changed_rationale_counts_as_a_different_decision():
    """The same match reached with a different explanation is a different
    decision to anyone reading the audit trail."""
    divergences, _, _ = compare(
        decision("TXN-1"), decision("TXN-1", rationale="Something else entirely.")
    )
    assert [d.field for d in divergences] == ["rationale"]


def test_a_decision_the_replay_did_not_reach_is_reported_as_missing():
    _, missing, extra = compare(decision("TXN-1"), {})
    assert missing == ["TXN-1"] and extra == []


def test_a_decision_only_the_replay_reached_is_reported_as_unexpected():
    _, missing, extra = compare({}, decision("TXN-1"))
    assert extra == ["TXN-1"] and missing == []


def test_divergences_name_both_sides_so_the_diff_is_actionable():
    divergences, _, _ = compare(decision("TXN-1"), decision("TXN-1", tier=3))
    d = divergences[0]
    assert isinstance(d, Divergence)
    assert (d.original, d.replayed) == (0, 3)


# -- recorded retrieval ---------------------------------------------------


def test_the_query_hash_covers_everything_that_changes_the_answer():
    base = query_hash("open_items", "t", "NARRATIVE", "AR", 10)
    assert base != query_hash("open_items", "t", "NARRATIVE", "AP", 10)
    assert base != query_hash("open_items", "t", "OTHER", "AR", 10)
    assert base != query_hash("open_items", "other-tenant", "NARRATIVE", "AR", 10)
    assert base != query_hash("open_items", "t", "NARRATIVE", "AR", 5)
    assert base != query_hash("resolved_pairs", "t", "NARRATIVE", "AR", 10)
    assert base == query_hash("open_items", "t", "NARRATIVE", "AR", 10)


def test_recorded_retrieval_is_served_without_querying_the_vector_store():
    digest = query_hash("open_items", "t", "NARRATIVE", "AR", 10)
    index = ReplayIndex(
        {digest: [{"ledger_entry_id": 100, "doc_ref": "INV-1", "score_milli": 900}]}
    )
    hits = index.search_open_items("t", "NARRATIVE", "AR", 10)
    assert hits == [OpenItemHit(ledger_entry_id=100, doc_ref="INV-1", score_milli=900)]


def test_recorded_resolved_pairs_round_trip():
    digest = query_hash("resolved_pairs", "t", "NARRATIVE", "", 10)
    index = ReplayIndex(
        {
            digest: [
                {"narrative": "N", "counterparty": "ACME", "doc_ref": "INV-1", "score_milli": 880}
            ]
        }
    )
    hits = index.search_resolved_pairs("t", "NARRATIVE", 10)
    assert hits == [ResolvedPairHit("N", "ACME", "INV-1", 880)]


def test_an_empty_recording_is_a_real_answer_not_a_miss():
    """ "Retrieval found nothing" is a recorded outcome and must replay as one."""
    digest = query_hash("open_items", "t", "NARRATIVE", "AR", 10)
    assert ReplayIndex({digest: []}).search_open_items("t", "NARRATIVE", "AR", 10) == []


def test_a_retrieval_miss_fails_rather_than_querying_live():
    """Falling back to a live query is how a replay silently compares two
    different worlds -- most obviously after a write-back."""
    index = ReplayIndex({})
    with pytest.raises(RetrievalReplayMissError, match="query changed"):
        index.search_open_items("t", "NARRATIVE", "AR", 10)
    assert len(index.misses) == 1
