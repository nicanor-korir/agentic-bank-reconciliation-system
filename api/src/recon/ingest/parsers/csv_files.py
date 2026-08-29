"""CSV parsers for the bank statement and the ledger export.

Parsing is deliberately strict: a missing or extra column is an error rather
than a silently defaulted field. A statement that does not parse exactly is a
bug in the parser, not an invitation to guess.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

from recon.ingest.parsers.base import ParseError, RawRow

BANK_COLUMNS = (
    "transaction_id",
    "value_date",
    "booking_date",
    "description",
    "amount",
    "currency",
    "counterparty",
)
LEDGER_COLUMNS = (
    "doc_ref",
    "entry_date",
    "due_date",
    "contact",
    "description",
    "total",
    "amount_due",
    "currency",
    "side",
    "status",
)


def _read(path: Path, expected: tuple[str, ...]) -> Iterator[RawRow]:
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        header = tuple(reader.fieldnames or ())
        if header != expected:
            raise ParseError(f"{path.name}: expected columns {expected}, got {header}")
        for lineno, row in enumerate(reader, start=2):
            if any(v is None for v in row.values()):
                raise ParseError(f"{path.name}:{lineno}: ragged row")
            yield {k: (v or "").strip() for k, v in row.items()}


class BankCsvParser:
    kind = "bank_statement"

    def parse(self, path: Path) -> Iterator[RawRow]:
        return _read(path, BANK_COLUMNS)


class LedgerCsvParser:
    kind = "ledger_export"

    def parse(self, path: Path) -> Iterator[RawRow]:
        return _read(path, LEDGER_COLUMNS)
