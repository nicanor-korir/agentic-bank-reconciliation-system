"""Write a generated statement out as CAMT.053.

Exists so the CAMT parser can be demonstrated on a file rather than described,
and so the round trip (generate CSV -> write XML -> parse XML) is testable end
to end. Nothing in the matching path depends on it.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

NAMESPACE = "urn:iso:std:iso:20022:tech:xsd:camt.053.001.02"


def write_camt053(statement_csv: Path, out_path: Path, account: str = "GB00RECON00000001") -> int:
    with statement_csv.open(newline="") as fh:
        rows = list(csv.DictReader(fh))

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<Document xmlns="{NAMESPACE}">',
        "  <BkToCstmrStmt>",
        "    <Stmt>",
        f"      <Acct><Id><IBAN>{account}</IBAN></Id></Acct>",
    ]
    for row in rows:
        amount = Decimal(row["amount"])
        indicator = "CRDT" if amount >= 0 else "DBIT"
        party = "Dbtr" if indicator == "CRDT" else "Cdtr"
        parts += [
            "      <Ntry>",
            f'        <Amt Ccy="{row["currency"]}">{abs(amount):.2f}</Amt>',
            f"        <CdtDbtInd>{indicator}</CdtDbtInd>",
            "        <Sts>BOOK</Sts>",
            f"        <BookgDt><Dt>{row['booking_date']}</Dt></BookgDt>",
            f"        <ValDt><Dt>{row['value_date']}</Dt></ValDt>",
            f"        <AcctSvcrRef>{escape(row['transaction_id'])}</AcctSvcrRef>",
            "        <NtryDtls><TxDtls>",
            f"          <RmtInf><Ustrd>{escape(row['description'])}</Ustrd></RmtInf>",
            f"          <RltdPties><{party}><Nm>{escape(row['counterparty'])}</Nm>"
            f"</{party}></RltdPties>",
            "        </TxDtls></NtryDtls>",
            "      </Ntry>",
        ]
    parts += ["    </Stmt>", "  </BkToCstmrStmt>", "</Document>", ""]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts))
    return len(rows)
