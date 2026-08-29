"""Write-back: turning a human correction into retrieval history.

This is the closing move of the demo, so the rules about what does and does
not become history matter more than the plumbing.
"""

from __future__ import annotations

from recon.api.service import pairs_from_resolutions

QUEUE = [
    {
        "bank_ref": "TXN-1",
        "bank_line_id": 1,
        "amount_minor": 500_000,
        "narrative": "RTP CREDIT 774891 ORIG=PAYCLEAR SETTLEMENT SETL BATCH 8222",
    },
    {
        "bank_ref": "TXN-2",
        "bank_line_id": 2,
        "amount_minor": 145_000,
        "narrative": "ACH CREDIT RENT",
    },
]
COUNTERPARTIES = {100: "CEDARBROOK HOLDINGS LLC", 200: "J MORRISON"}


def approve(bank_ref: str, entry_ids: list[int], doc_refs: list[str]) -> dict:
    return {
        "bank_ref": bank_ref,
        "action": "approve",
        "ledger_entry_ids": entry_ids,
        "doc_refs": doc_refs,
        "reviewer": "someone",
    }


def test_an_approval_becomes_history_keyed_on_the_payer():
    """The invoice is closed by next month; the counterparty is not."""
    pairs = pairs_from_resolutions(QUEUE, [approve("TXN-1", [100], ["INV-CEDAR"])], COUNTERPARTIES)
    assert len(pairs) == 1
    assert pairs[0].counterparty == "CEDARBROOK HOLDINGS LLC"
    assert pairs[0].narrative.startswith("RTP CREDIT")
    assert pairs[0].bank_ref == "TXN-1"


def test_a_rejection_never_becomes_history():
    """ "None of these" is useful in an audit trail and actively misleading as
    retrieval history -- it would teach the index a payer nobody confirmed."""
    pairs = pairs_from_resolutions(
        QUEUE,
        [{"bank_ref": "TXN-1", "action": "reject", "reviewer": "someone"}],
        COUNTERPARTIES,
    )
    assert pairs == []


def test_a_reassignment_is_not_silently_treated_as_an_approval():
    pairs = pairs_from_resolutions(
        QUEUE,
        [{**approve("TXN-1", [100], ["INV-CEDAR"]), "action": "reassign"}],
        COUNTERPARTIES,
    )
    assert pairs == []


def test_an_unknown_bank_ref_is_ignored_rather_than_crashing():
    pairs = pairs_from_resolutions(
        QUEUE, [approve("TXN-NOT-IN-QUEUE", [100], ["X"])], COUNTERPARTIES
    )
    assert pairs == []


def test_an_entry_with_no_counterparty_produces_no_history():
    """Nothing transferable to remember, so nothing is written."""
    pairs = pairs_from_resolutions(QUEUE, [approve("TXN-1", [999], ["X"])], COUNTERPARTIES)
    assert pairs == []


def test_a_split_match_records_the_payer_once():
    pairs = pairs_from_resolutions(
        QUEUE,
        [approve("TXN-2", [200, 201, 202], ["INV-A", "INV-B", "INV-C"])],
        COUNTERPARTIES,
    )
    assert len(pairs) == 1
    assert pairs[0].counterparty == "J MORRISON"
    assert pairs[0].doc_ref == "INV-A"


def test_several_approvals_produce_several_pairs():
    pairs = pairs_from_resolutions(
        QUEUE,
        [approve("TXN-1", [100], ["A"]), approve("TXN-2", [200], ["B"])],
        COUNTERPARTIES,
    )
    assert {p.bank_ref for p in pairs} == {"TXN-1", "TXN-2"}


# -- resolution field handling --------------------------------------------


def _rationale_for(resolution: dict) -> str:
    """Mirrors what apply_human writes to the NOT NULL rationale column."""
    return resolution.get("note") or f"Confirmed by {resolution.get('reviewer') or 'reviewer'}."


def test_an_explicitly_null_note_still_produces_a_rationale():
    """`.get(key, default)` returns None when the key is present and null.

    The UI sends `note: null` for an empty box, `rationale` is NOT NULL, and
    the insert failed -- rolling back the entire resume. `or` is the operator
    that was actually wanted.
    """
    assert (
        _rationale_for(
            {"bank_ref": "TXN-1", "action": "approve", "reviewer": "n.korir", "note": None}
        )
        == "Confirmed by n.korir."
    )


def test_an_omitted_note_produces_a_rationale():
    assert _rationale_for({"bank_ref": "TXN-1", "reviewer": "n.korir"}).startswith("Confirmed by")


def test_a_supplied_note_becomes_the_rationale():
    assert _rationale_for({"note": "Confirmed against the remittance advice."}) == (
        "Confirmed against the remittance advice."
    )


def test_a_null_reviewer_does_not_leak_into_the_rationale():
    assert _rationale_for({"reviewer": None, "note": None}) == "Confirmed by reviewer."
