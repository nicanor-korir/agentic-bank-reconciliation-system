"""Deterministic cascade driver (Tiers 0-1).

Each tier sees only what the previous tier could not resolve, and a ledger
entry claimed by an earlier tier is invisible to later ones -- one open item
cannot settle two bank lines.

Determinism (NON-NEGOTIABLE #4) rests on three things here: bank lines are
processed in a total order that does not depend on how they came out of the
database, candidate lists are built by iterating sorted inputs, and nothing
in this path reads a clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from recon.config import MatchConfig
from recon.matching.tier0_exact import match_tier0
from recon.matching.tier1_struct import match_tier1
from recon.matching.types import BankLine, LedgerEntry, Match


@dataclass
class CascadeResult:
    matches: list[Match] = field(default_factory=list)
    unmatched: list[BankLine] = field(default_factory=list)
    # Entries settled by an earlier tier. Tier 2 must not offer these as
    # candidates: one open item cannot settle two bank lines.
    claimed_entry_ids: set[int] = field(default_factory=set)

    @property
    def by_tier(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for m in self.matches:
            counts[m.tier] = counts.get(m.tier, 0) + 1
        return counts

    @property
    def matched_ids(self) -> set[int]:
        return {m.bank_line_id for m in self.matches}


def _ordered_lines(lines: list[BankLine]) -> list[BankLine]:
    return sorted(lines, key=lambda line: (line.value_date, line.bank_ref, line.id))


def _ordered_entries(entries: list[LedgerEntry]) -> list[LedgerEntry]:
    return sorted(entries, key=lambda e: (e.entry_date, e.doc_ref or "", e.id))


def run_deterministic(
    lines: list[BankLine],
    entries: list[LedgerEntry],
    config: MatchConfig,
    max_tier: int = 1,
) -> CascadeResult:
    """Run Tiers 0-1. `max_tier` exists for the ablation table."""
    ordered_lines = _ordered_lines(lines)
    ordered_entries = _ordered_entries(entries)

    by_doc_ref: dict[str, list[LedgerEntry]] = {}
    for entry in ordered_entries:
        if entry.doc_ref:
            by_doc_ref.setdefault(entry.doc_ref, []).append(entry)

    claimed: set[int] = set()
    result = CascadeResult()

    if max_tier >= 0:
        result.matches.extend(match_tier0(ordered_lines, by_doc_ref, claimed, config))

    if max_tier >= 1:
        remaining = [line for line in ordered_lines if line.id not in result.matched_ids]
        result.matches.extend(match_tier1(remaining, ordered_entries, claimed, config))

    matched = result.matched_ids
    result.unmatched = [line for line in ordered_lines if line.id not in matched]
    result.claimed_entry_ids = claimed
    return result
