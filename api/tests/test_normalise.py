import pytest

from recon.ingest.normalise import extract_refs, fold, normalise_bank_line
from recon.ingest.parsers.base import ParseError

ROW = {
    "transaction_id": "TXN-2026-06-00001",
    "value_date": "2026-06-03",
    "booking_date": "2026-06-03",
    "description": "ACH CREDIT RENT INV-2026-06-0412 J MORRISON WLC",
    "amount": "1450.00",
    "currency": "USD",
    "counterparty": "J Morrison",
}


def test_fold_collapses_whitespace_and_diacritics():
    assert fold("  Café   Ndiaye \n") == "CAFE NDIAYE"


def test_fold_keeps_reference_punctuation():
    # Tier 0 matches on references, which live in the punctuation.
    assert "INV-2026-06-0412" in fold("ach credit inv-2026-06-0412")


def test_extract_refs_finds_both_document_kinds():
    assert extract_refs("PAID INV-2026-06-0412 AND BILL-2026-06-0091") == [
        "INV-2026-06-0412",
        "BILL-2026-06-0091",
    ]


def test_extract_refs_ignores_lookalikes():
    assert extract_refs("INV-26-6-41 REF 99213") == []


def test_amount_becomes_minor_units():
    assert normalise_bank_line(ROW, "harborview")["amount_minor"] == 145_000


def test_content_hash_ignores_the_file_it_arrived_in():
    a = normalise_bank_line(ROW, "harborview")
    b = normalise_bank_line({**ROW, "booking_date": "2026-06-04"}, "harborview")
    # booking_date is not part of identity; the row is the same row.
    assert a["content_hash"] == b["content_hash"]


def test_content_hash_is_tenant_scoped():
    a = normalise_bank_line(ROW, "harborview")
    b = normalise_bank_line(ROW, "other-client")
    assert a["content_hash"] != b["content_hash"]


def test_amount_change_changes_identity():
    a = normalise_bank_line(ROW, "harborview")
    b = normalise_bank_line({**ROW, "amount": "1450.01"}, "harborview")
    assert a["content_hash"] != b["content_hash"]


def test_bad_date_is_a_parse_error():
    with pytest.raises(ParseError, match="ISO date"):
        normalise_bank_line({**ROW, "value_date": "03/06/2026"}, "harborview")


def test_bad_amount_is_a_parse_error():
    with pytest.raises(ParseError, match="valid amount"):
        normalise_bank_line({**ROW, "amount": "1,4five0.00"}, "harborview")
