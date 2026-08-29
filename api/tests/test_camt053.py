"""CAMT.053 parsing.

Answers "does it take real bank formats?" with a file. The parser emits the
same RawRow shape as the CSV reader, so nothing downstream changes -- which is
the whole reason the parser protocol exists.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from recon.ingest.camt_writer import write_camt053
from recon.ingest.normalise import normalise_bank_line
from recon.ingest.parsers import BankCsvParser, Camt053Parser, ParseError

SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.02">
  <BkToCstmrStmt><Stmt>
    <Ntry>
      <Amt Ccy="USD">1450.00</Amt>
      <CdtDbtInd>CRDT</CdtDbtInd>
      <BookgDt><Dt>2026-06-03</Dt></BookgDt>
      <ValDt><Dt>2026-06-03</Dt></ValDt>
      <AcctSvcrRef>TXN-2026-06-00001</AcctSvcrRef>
      <NtryDtls><TxDtls>
        <RmtInf><Ustrd>ACH CREDIT RENT INV-2026-06-0412 J MORRISON</Ustrd></RmtInf>
        <RltdPties><Dbtr><Nm>J Morrison</Nm></Dbtr></RltdPties>
      </TxDtls></NtryDtls>
    </Ntry>
    <Ntry>
      <Amt Ccy="USD">320.50</Amt>
      <CdtDbtInd>DBIT</CdtDbtInd>
      <BookgDt><Dt>2026-06-04</Dt></BookgDt>
      <ValDt><Dt>2026-06-04</Dt></ValDt>
      <AcctSvcrRef>TXN-2026-06-00002</AcctSvcrRef>
      <NtryDtls><TxDtls>
        <RmtInf><Ustrd>ACH DEBIT BILL-2026-06-0091 NORTHSIDE PLUMBING</Ustrd></RmtInf>
        <RltdPties><Cdtr><Nm>Northside Plumbing</Nm></Cdtr></RltdPties>
      </TxDtls></NtryDtls>
    </Ntry>
  </Stmt></BkToCstmrStmt>
</Document>
"""


@pytest.fixture
def sample(tmp_path) -> Path:
    path = tmp_path / "statement.xml"
    path.write_text(SAMPLE)
    return path


def test_direction_comes_from_cdtdbtind_not_the_amount(sample):
    """CAMT amounts are unsigned. Reading the amount alone turns every payment
    into a receipt, which would silently invert half the ledger."""
    rows = list(Camt053Parser().parse(sample))
    assert rows[0]["amount"] == "1450.00"
    assert rows[1]["amount"] == "-320.50"


def test_rows_normalise_exactly_like_csv_rows(sample):
    rows = list(Camt053Parser().parse(sample))
    record = normalise_bank_line(rows[0], "harborview")
    assert record["amount_minor"] == 145_000
    assert record["bank_ref"] == "TXN-2026-06-00001"
    assert "INV-2026-06-0412" in record["narrative"]
    assert record["counterparty"] == "J MORRISON"


def test_the_counterparty_is_the_debtor_on_a_credit_and_creditor_on_a_debit(sample):
    rows = list(Camt053Parser().parse(sample))
    assert rows[0]["counterparty"] == "J Morrison"
    assert rows[1]["counterparty"] == "Northside Plumbing"


def test_a_different_namespace_version_still_parses(tmp_path):
    """The camt.053 URI carries a minor version; matching it exactly breaks
    against any bank on a different one."""
    path = tmp_path / "v4.xml"
    path.write_text(SAMPLE.replace("camt.053.001.02", "camt.053.001.08"))
    assert len(list(Camt053Parser().parse(path))) == 2


def test_a_missing_direction_is_an_error_not_a_guess(tmp_path):
    path = tmp_path / "bad.xml"
    path.write_text(SAMPLE.replace("<CdtDbtInd>CRDT</CdtDbtInd>", ""))
    with pytest.raises(ParseError, match="CdtDbtInd"):
        list(Camt053Parser().parse(path))


def test_malformed_xml_is_rejected(tmp_path):
    path = tmp_path / "broken.xml"
    path.write_text("<Document><Ntry>")
    with pytest.raises(ParseError, match="well-formed"):
        list(Camt053Parser().parse(path))


def test_a_file_that_is_not_a_statement_is_rejected(tmp_path):
    path = tmp_path / "empty.xml"
    path.write_text('<?xml version="1.0"?><Document><BkToCstmrStmt/></Document>')
    with pytest.raises(ParseError, match=r"camt\.053"):
        list(Camt053Parser().parse(path))


def test_csv_to_camt_and_back_is_lossless_for_everything_matching_uses(tmp_path):
    """Round trip: the ingest path cannot tell which format it came from."""
    csv_path = tmp_path / "statement.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(
            [
                "transaction_id",
                "value_date",
                "booking_date",
                "description",
                "amount",
                "currency",
                "counterparty",
            ]
        )
        writer.writerow(
            [
                "TXN-1",
                "2026-06-03",
                "2026-06-03",
                "ACH CREDIT RENT INV-2026-06-0412",
                "1450.00",
                "USD",
                "J Morrison",
            ]
        )
        writer.writerow(
            [
                "TXN-2",
                "2026-06-04",
                "2026-06-04",
                "ACH DEBIT BILL-2026-06-0091",
                "-320.50",
                "USD",
                "Northside Plumbing",
            ]
        )

    xml_path = tmp_path / "statement.xml"
    assert write_camt053(csv_path, xml_path) == 2

    from_csv = [normalise_bank_line(r, "t") for r in BankCsvParser().parse(csv_path)]
    from_xml = [normalise_bank_line(r, "t") for r in Camt053Parser().parse(xml_path)]

    # Identical content hashes means the two formats are the same rows as far as
    # idempotency and matching are concerned.
    assert [r["content_hash"] for r in from_csv] == [r["content_hash"] for r in from_xml]
