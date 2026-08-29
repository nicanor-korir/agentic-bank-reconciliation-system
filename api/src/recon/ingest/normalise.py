"""Raw source rows -> canonical records.

Two jobs, both load-bearing:

  * Money crosses from text to integer minor units here and nowhere else in
    the ingest path (NON-NEGOTIABLE #8).
  * `content_hash` is computed over the identifying fields only, so the same
    row arriving in a differently-named file is still recognised as the same
    row (NON-NEGOTIABLE #6).

Narrative folding is intentionally conservative. It upper-cases, collapses
whitespace and strips diacritics -- but does not remove punctuation, because
reference numbers live in the punctuation and Tier 0 needs them intact.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from recon.hashing import content_hash
from recon.ingest.parsers.base import ParseError, RawRow
from recon.money import to_minor

_WS = re.compile(r"\s+")
_REF = re.compile(r"\b(?:INV|BILL)-\d{4}-\d{2}-\d{4}\b")


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _WS.sub(" ", stripped).strip().upper()


def extract_refs(narrative: str) -> list[str]:
    """Document references appearing in a narrative, in order of appearance."""
    return _REF.findall(narrative.upper())


def _parse_date(value: str, field: str, row_id: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ParseError(f"{row_id}: {field} is not an ISO date: {value!r}") from exc


def _parse_amount(value: str, currency: str, field: str, row_id: str) -> int:
    try:
        return to_minor(Decimal(value.replace(",", "")), currency)
    except (InvalidOperation, ArithmeticError, ValueError) as exc:
        raise ParseError(f"{row_id}: {field} is not a valid amount: {value!r}") from exc


def normalise_bank_line(row: RawRow, tenant_id: str) -> dict[str, Any]:
    ref = row["transaction_id"]
    currency = row["currency"].upper()
    amount_minor = _parse_amount(row["amount"], currency, "amount", ref)
    value_date = _parse_date(row["value_date"], "value_date", ref)
    booking = row["booking_date"]
    narrative = fold(row["description"])

    record: dict[str, Any] = {
        "tenant_id": tenant_id,
        "value_date": value_date,
        "booking_date": _parse_date(booking, "booking_date", ref) if booking else None,
        "amount_minor": amount_minor,
        "currency": currency,
        "narrative": narrative,
        "counterparty": fold(row["counterparty"]) or None,
        "bank_ref": ref or None,
        "raw": dict(row),
    }
    # Identity is the tenant plus what the bank actually told us. The filename
    # and ingest time are deliberately excluded.
    record["content_hash"] = content_hash(
        {
            "tenant_id": tenant_id,
            "kind": "bank_line",
            "bank_ref": ref,
            "value_date": value_date.isoformat(),
            "amount_minor": amount_minor,
            "currency": currency,
            "narrative": narrative,
        }
    )
    return record


def normalise_ledger_entry(row: RawRow, tenant_id: str) -> dict[str, Any]:
    ref = row["doc_ref"]
    currency = row["currency"].upper()
    amount_minor = _parse_amount(row["total"], currency, "total", ref)
    open_minor = _parse_amount(row["amount_due"], currency, "amount_due", ref)
    entry_date = _parse_date(row["entry_date"], "entry_date", ref)
    due = row["due_date"]
    description = fold(row["description"])

    side = row["side"].upper()
    if side not in {"AR", "AP"}:
        raise ParseError(f"{ref}: side must be AR or AP, got {side!r}")
    status = row["status"].lower()
    if status not in {"open", "closed"}:
        raise ParseError(f"{ref}: status must be open or closed, got {status!r}")

    record: dict[str, Any] = {
        "tenant_id": tenant_id,
        "entry_date": entry_date,
        "due_date": _parse_date(due, "due_date", ref) if due else None,
        "amount_minor": amount_minor,
        "open_amount_minor": open_minor,
        "currency": currency,
        "description": description,
        "counterparty": fold(row["contact"]) or None,
        "doc_ref": ref or None,
        "side": side,
        "status": status,
        "raw": dict(row),
    }
    record["content_hash"] = content_hash(
        {
            "tenant_id": tenant_id,
            "kind": "ledger_entry",
            "doc_ref": ref,
            "entry_date": entry_date.isoformat(),
            "amount_minor": amount_minor,
            "currency": currency,
            "description": description,
        }
    )
    return record
