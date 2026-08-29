"""Tier 1 -- deterministic structural.

No reference to lean on, so the evidence is amount plus counterparty plus a
7-day window. Standing orders and recurring fees fall out of the same rule
rather than needing their own: they are exact-amount, known-counterparty
payments, which is precisely what this tier tests.

An FX-rounded amount is treated as equal within a tight tolerance. Tight is
the point -- a wider band stops being "the same amount arrived" and becomes a
genuine ambiguity, which belongs to retrieval and adjudication downstream.
"""

from __future__ import annotations

from collections import defaultdict

from recon.config import MatchConfig
from recon.matching.types import BankLine, LedgerEntry, Match
from recon.money import format_minor


def _tolerance(amount_minor: int, config: MatchConfig) -> int:
    return amount_minor * config.tier1_fx_tolerance_bps // 10_000


def _index(entries: list[LedgerEntry]) -> dict[tuple[str, str], list[LedgerEntry]]:
    """Open entries grouped by (side, counterparty)."""
    index: dict[tuple[str, str], list[LedgerEntry]] = defaultdict(list)
    for entry in entries:
        if entry.status == "open" and entry.counterparty:
            index[(entry.side, entry.counterparty)].append(entry)
    return index


def match_tier1(
    lines: list[BankLine],
    entries: list[LedgerEntry],
    claimed: set[int],
    config: MatchConfig,
) -> list[Match]:
    index = _index(entries)
    matches: list[Match] = []

    for line in lines:
        if not line.counterparty:
            continue
        pool = [
            e
            for e in index.get((line.side, line.counterparty), ())
            if e.id not in claimed
            and abs((line.value_date - e.entry_date).days) <= config.tier1_date_window_days
        ]
        if not pool:
            continue

        exact = [e for e in pool if e.amount_minor == line.abs_minor]
        near = [
            e
            for e in pool
            if e.amount_minor != line.abs_minor
            and abs(e.amount_minor - line.abs_minor) <= _tolerance(e.amount_minor, config)
        ]

        # An exact amount outranks a tolerated one. Only fall through to the FX
        # band when nothing matched exactly, so a rounding rule can never
        # displace a genuine exact match.
        if len(exact) == 1 and not near:
            entry, kind = exact[0], "exact"
        elif not exact and len(near) == 1:
            entry, kind = near[0], "fx"
        else:
            continue

        evidence: tuple[str, ...]
        if kind == "exact":
            confidence = config.tier1_confidence
            detail = f"the amount {format_minor(entry.amount_minor, entry.currency)} agrees exactly"
            evidence = (f"counterparty:{line.counterparty}", f"amount:{entry.amount_minor}")
        else:
            drift = line.abs_minor - entry.amount_minor
            confidence = config.tier1_fx_confidence
            detail = (
                f"the amount is within {config.tier1_fx_tolerance_bps} basis points of "
                f"{format_minor(entry.amount_minor, entry.currency)} "
                f"({format_minor(drift, entry.currency)} of FX rounding)"
            )
            evidence = (
                f"counterparty:{line.counterparty}",
                f"amount:{entry.amount_minor}",
                f"fx_drift_minor:{drift}",
            )

        offset = abs((line.value_date - entry.entry_date).days)
        matches.append(
            Match(
                bank_line_id=line.id,
                bank_ref=line.bank_ref,
                ledger_entry_ids=(entry.id,),
                doc_refs=(entry.doc_ref,) if entry.doc_ref else (),
                tier=1,
                decision="match",
                confidence=confidence,
                rationale=(
                    f"{entry.doc_ref or 'This open item'} is the only open item for "
                    f"{line.counterparty} within {config.tier1_date_window_days} days where "
                    f"{detail}."
                ),
                evidence=(*evidence, f"date_offset:{offset}"),
            )
        )
        claimed.add(entry.id)

    return matches
