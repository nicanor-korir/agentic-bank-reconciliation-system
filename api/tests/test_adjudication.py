"""Tier 3: cost accounting, the output contract, and replay identity.

None of this needs an API key. The request builder is a pure function and the
recorded/stub adjudicators are ordinary objects, which is the point of keeping
the transport thin.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from recon.config import MatchConfig
from recon.llm.adjudicator import (
    CostCeilingError,
    CostMeter,
    RecordedAdjudicator,
    ReplayMissError,
    StubAdjudicator,
)
from recon.llm.pricing import RATES_NANO, UnknownModelError, Usage, format_micro
from recon.llm.request import build_request, candidate_id, request_hash
from recon.llm.schema import InvalidAdjudicationError, validate
from recon.matching.tier2_candidates import Candidate, CandidateSet
from recon.matching.types import BankLine, LedgerEntry

CONFIG = MatchConfig()
D = date(2026, 6, 10)
PROMPT = "system prompt"


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


def candidate_set(*candidates: Candidate) -> CandidateSet:
    return CandidateSet(bank_line_id=1, bank_ref="TXN-1", candidates=list(candidates))


def single(entry_id=100, doc_ref="INV-001", total=145_000, delta=0) -> Candidate:
    return Candidate("single", (entry_id,), (doc_ref,), total, delta, ("amount_window",), 900)


# -- cost -----------------------------------------------------------------


def test_cost_is_exact_integer_arithmetic():
    """A ceiling that halts a run is a decision, and decisions are not floats."""
    usage = Usage(input_tokens=1_500, output_tokens=200)
    rate = RATES_NANO["claude-sonnet-5"]
    expected = (1_500 * rate["input"] + 200 * rate["output"]) // 1_000
    assert usage.cost_micro("claude-sonnet-5") == expected
    assert isinstance(usage.cost_micro("claude-sonnet-5"), int)


def test_cached_input_is_cheaper_than_fresh_input():
    fresh = Usage(input_tokens=10_000).cost_micro("claude-sonnet-5")
    cached = Usage(cache_read_tokens=10_000).cost_micro("claude-sonnet-5")
    assert cached < fresh


def test_an_unpriced_model_refuses_rather_than_guessing():
    with pytest.raises(UnknownModelError, match="no published rate"):
        Usage(input_tokens=1).cost_micro("some-model-we-never-priced")


def test_cost_formatting_is_readable():
    assert format_micro(1_060_000) == "$1.0600"


def test_the_ceiling_is_checked_before_spending_not_after():
    meter = CostMeter(ceiling_micro=1_000)
    meter.check()
    meter.record(999)
    meter.check()
    meter.record(1)
    with pytest.raises(CostCeilingError, match="halted after 2 call"):
        meter.check()


# -- output contract ------------------------------------------------------


def _payload(**kw):
    base = dict(
        decision="match",
        candidate_ids=["C1"],
        confidence=0.95,
        rationale="INV-001 is quoted and the amount agrees.",
        evidence=["x"],
    )
    return {**base, **kw}


def test_confidence_becomes_decimal_before_any_comparison():
    result = validate(_payload(confidence=0.9), {"C1"})
    assert result["confidence_decimal"] == Decimal("0.900")
    assert isinstance(result["confidence_decimal"], Decimal)


def test_a_candidate_that_was_never_offered_is_rejected():
    """Otherwise a decision points at a ledger item nobody proposed."""
    with pytest.raises(InvalidAdjudicationError, match="not offered"):
        validate(_payload(candidate_ids=["C9"]), {"C1"})


@pytest.mark.parametrize("ids", [[], ["C1", "C2"]])
def test_match_must_name_exactly_one_candidate(ids):
    with pytest.raises(InvalidAdjudicationError, match="exactly one"):
        validate(_payload(candidate_ids=ids), {"C1", "C2"})


@pytest.mark.parametrize("decision", ["no_match", "insufficient_evidence"])
def test_declining_must_not_name_candidates(decision):
    with pytest.raises(InvalidAdjudicationError, match="must not name"):
        validate(_payload(decision=decision, candidate_ids=["C1"]), {"C1"})


def test_an_empty_rationale_is_rejected():
    """NON-NEGOTIABLE #3: no bare confidence scores."""
    with pytest.raises(InvalidAdjudicationError, match="rationale is empty"):
        validate(_payload(rationale="   "), {"C1"})


# -- request identity -----------------------------------------------------


def test_the_same_inputs_produce_the_same_request_bytes():
    """Replay identity rests on this. Anything non-deterministic in request
    building would make replay report drift that never happened."""
    args = (line(), candidate_set(single()), {100: entry()}, PROMPT, CONFIG)
    assert request_hash(build_request(*args)) == request_hash(build_request(*args))


def test_a_changed_prompt_is_a_different_call():
    base = build_request(line(), candidate_set(single()), {100: entry()}, PROMPT, CONFIG)
    edited = build_request(
        line(), candidate_set(single()), {100: entry()}, PROMPT + " revised", CONFIG
    )
    assert request_hash(base) != request_hash(edited)


def test_a_changed_candidate_list_is_a_different_call():
    by_id = {100: entry(), 101: entry(id=101, doc_ref="INV-002")}
    one = build_request(line(), candidate_set(single()), by_id, PROMPT, CONFIG)
    two = build_request(
        line(),
        candidate_set(single(), single(101, "INV-002", 145_000, 0)),
        by_id,
        PROMPT,
        CONFIG,
    )
    assert request_hash(one) != request_hash(two)


def test_candidates_are_offered_under_opaque_handles():
    """Not the ledger id: the model should decide on evidence, and a database
    id invites pattern-matching on numbers that mean nothing."""
    request = build_request(line(), candidate_set(single()), {100: entry()}, PROMPT, CONFIG)
    body = request["messages"][0]["content"]
    assert '"id":"C1"' in body
    assert '"100"' not in body


def test_the_prompt_carries_a_cache_breakpoint():
    """The prompt is the stable prefix; without a breakpoint the input side
    never gets cheap at volume."""
    request = build_request(line(), candidate_set(single()), {100: entry()}, PROMPT, CONFIG)
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_structured_output_is_forced():
    request = build_request(line(), candidate_set(single()), {100: entry()}, PROMPT, CONFIG)
    assert request["tool_choice"]["type"] == "tool"
    assert request["tools"][0]["strict"] is True


# -- adjudicators ---------------------------------------------------------


def _adjudicate(adjudicator, cset):
    request = build_request(line(), cset, {100: entry(), 101: entry(id=101)}, PROMPT, CONFIG)
    ids = {candidate_id(i) for i in range(len(cset.candidates))}
    return adjudicator.adjudicate(request, ids)


def test_the_stub_commits_only_an_unambiguous_exact_match():
    stub = StubAdjudicator(CONFIG.tier3_autocommit_confidence)
    result = _adjudicate(stub, candidate_set(single()))
    assert result.decision == "match"
    assert result.confidence == CONFIG.tier3_autocommit_confidence


def test_the_stub_declines_when_two_candidates_fit_equally():
    stub = StubAdjudicator(CONFIG.tier3_autocommit_confidence)
    result = _adjudicate(stub, candidate_set(single(), single(101, "INV-002", 145_000, 0)))
    assert result.decision == "insufficient_evidence"
    assert result.candidate_ids == ()


def test_a_recorded_call_is_served_without_touching_the_model():
    stub = StubAdjudicator(CONFIG.tier3_autocommit_confidence)
    cset = candidate_set(single())
    live = _adjudicate(stub, cset)

    recorded = RecordedAdjudicator(
        {
            live.request_hash: {
                "response": live.response,
                "latency_ms": 12,
            }
        }
    )
    replayed = _adjudicate(recorded, cset)
    assert replayed.served_from_recording
    assert (replayed.decision, replayed.candidate_ids, replayed.confidence) == (
        live.decision,
        live.candidate_ids,
        live.confidence,
    )
    assert replayed.rationale == live.rationale


def test_a_replay_miss_fails_and_never_falls_back_to_a_live_call():
    """The whole claim is that replay reproduces the stored run. A fallback
    would make that claim false while appearing to succeed."""
    recorded = RecordedAdjudicator({})
    with pytest.raises(ReplayMissError, match="input to the model changed"):
        _adjudicate(recorded, candidate_set(single()))
    assert len(recorded.misses) == 1
