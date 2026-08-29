"""CAMT.053 (ISO 20022) bank statement parser.

Deferred from Phase 1 to Phase 6 on purpose: CSV got the cascade built, and
this exists so the demo can answer "does it take real bank formats?" with a
file rather than a promise.

It emits exactly the same `RawRow` shape as the CSV parser, so `normalise`,
the loader and every matcher are untouched. That is what the parser protocol
was for.

Two things this handles that a naive reader gets wrong:

  * **Sign.** CAMT amounts are unsigned; direction lives in `CdtDbtInd`. Reading
    the amount alone turns every payment into a receipt.
  * **Namespaces.** The camt.053 namespace URI carries a version, so matching
    on a hard-coded URI breaks against a bank on a different minor version.
    Tags are matched on local name instead.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree

from recon.ingest.parsers.base import ParseError, RawRow

_NS = re.compile(r"^\{[^}]*\}")


def _local(tag: str) -> str:
    return _NS.sub("", tag)


def _find(node: ElementTree.Element, *path: str) -> ElementTree.Element | None:
    current: ElementTree.Element | None = node
    for name in path:
        if current is None:
            return None
        current = next((child for child in current if _local(child.tag) == name), None)
    return current


def _text(node: ElementTree.Element | None) -> str:
    return (node.text or "").strip() if node is not None else ""


def _first_text(node: ElementTree.Element, name: str) -> str:
    for element in node.iter():
        if _local(element.tag) == name:
            return (element.text or "").strip()
    return ""


class Camt053Parser:
    kind = "bank_statement"

    def parse(self, path: Path) -> Iterator[RawRow]:
        try:
            root = ElementTree.parse(path).getroot()
        except ElementTree.ParseError as exc:
            raise ParseError(f"{path.name}: not well-formed XML: {exc}") from exc

        entries = [e for e in root.iter() if _local(e.tag) == "Ntry"]
        if not entries:
            raise ParseError(
                f"{path.name}: no <Ntry> elements found; this does not look like "
                f"a camt.053 statement"
            )

        for index, entry in enumerate(entries, start=1):
            yield self._row(entry, index, path.name)

    def _row(self, entry: ElementTree.Element, index: int, filename: str) -> RawRow:
        amount_node = _find(entry, "Amt")
        raw_amount = _text(amount_node)
        currency = (amount_node.get("Ccy") if amount_node is not None else "") or ""
        if not raw_amount or not currency:
            raise ParseError(f"{filename}: entry {index} has no <Amt Ccy=...>")

        try:
            magnitude = Decimal(raw_amount)
        except InvalidOperation as exc:
            raise ParseError(
                f"{filename}: entry {index} amount {raw_amount!r} is not a number"
            ) from exc

        indicator = _text(_find(entry, "CdtDbtInd")).upper()
        if indicator not in {"CRDT", "DBIT"}:
            raise ParseError(
                f"{filename}: entry {index} has CdtDbtInd={indicator!r}; expected "
                f"CRDT or DBIT. The amount is unsigned, so without this every "
                f"payment would be read as a receipt."
            )
        signed = magnitude if indicator == "CRDT" else -magnitude

        value_date = _text(_find(entry, "ValDt", "Dt")) or _text(_find(entry, "BookgDt", "Dt"))
        booking_date = _text(_find(entry, "BookgDt", "Dt")) or value_date
        if not value_date:
            raise ParseError(f"{filename}: entry {index} has neither ValDt nor BookgDt")

        reference = (
            _text(_find(entry, "AcctSvcrRef"))
            or _first_text(entry, "EndToEndId")
            or f"{filename}:{index}"
        )

        # Unstructured remittance information is where narratives actually live.
        narrative = " ".join(
            (element.text or "").strip()
            for element in entry.iter()
            if _local(element.tag) == "Ustrd" and (element.text or "").strip()
        ) or _text(_find(entry, "AddtlNtryInf"))

        # The counterparty is the debtor on a credit and the creditor on a debit.
        party = "Dbtr" if indicator == "CRDT" else "Cdtr"
        counterparty = ""
        for element in entry.iter():
            if _local(element.tag) == party:
                counterparty = _first_text(element, "Nm")
                if counterparty:
                    break

        return {
            "transaction_id": reference,
            "value_date": value_date,
            "booking_date": booking_date,
            "description": narrative,
            "amount": f"{signed:.2f}",
            "currency": currency.upper(),
            "counterparty": counterparty,
        }
