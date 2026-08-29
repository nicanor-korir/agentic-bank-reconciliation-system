"""Domain records for matching.

Deliberately plain and frozen. The matchers are pure functions over these --
no database handle reaches a matcher, which is what makes the tiers unit
testable against the hard cases and replayable without a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class BankLine:
    id: int
    bank_ref: str
    value_date: date
    amount_minor: int
    currency: str
    narrative: str
    counterparty: str | None

    @property
    def side(self) -> str:
        """Money in settles a receivable; money out settles a payable."""
        return "AR" if self.amount_minor > 0 else "AP"

    @property
    def abs_minor(self) -> int:
        return abs(self.amount_minor)


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    id: int
    doc_ref: str | None
    entry_date: date
    amount_minor: int
    open_amount_minor: int
    currency: str
    description: str
    counterparty: str | None
    side: str
    status: str


@dataclass(frozen=True, slots=True)
class Match:
    bank_line_id: int
    bank_ref: str
    ledger_entry_ids: tuple[int, ...]
    doc_refs: tuple[str, ...]
    tier: int
    decision: str
    confidence: Decimal
    rationale: str
    evidence: tuple[str, ...]
