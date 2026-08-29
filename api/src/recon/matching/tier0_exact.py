"""Tier 0 -- deterministic exact.

Amount equal, date within 0-2 days, and a document reference quoted in both.
Auto-commits at confidence 1.0 with zero model cost.

The uniqueness requirement is the whole safety story here: if a quoted
reference resolves to more than one open item, this tier declines rather than
picking one. NON-NEGOTIABLE #2 -- escalating a correct match is cheap,
committing a wrong one is not.
"""

from __future__ import annotations

from recon.config import MatchConfig
from recon.ingest.normalise import extract_refs
from recon.matching.types import BankLine, LedgerEntry, Match
from recon.money import format_minor


def match_tier0(
    lines: list[BankLine],
    by_doc_ref: dict[str, list[LedgerEntry]],
    claimed: set[int],
    config: MatchConfig,
) -> list[Match]:
    matches: list[Match] = []

    for line in lines:
        refs = extract_refs(line.narrative)
        if not refs:
            continue

        seen: set[int] = set()
        candidates: list[tuple[str, LedgerEntry]] = []
        for ref in refs:
            for entry in by_doc_ref.get(ref, ()):
                if entry.id in claimed or entry.id in seen:
                    continue
                if entry.status != "open" or entry.side != line.side:
                    continue
                if entry.amount_minor != line.abs_minor:
                    continue
                if abs((line.value_date - entry.entry_date).days) > config.tier0_date_window_days:
                    continue
                seen.add(entry.id)
                candidates.append((ref, entry))

        if len(candidates) != 1:
            continue

        ref, entry = candidates[0]
        offset = (line.value_date - entry.entry_date).days
        when = "the same day" if offset == 0 else f"{abs(offset)} day{'s'[: offset != 1]} later"
        matches.append(
            Match(
                bank_line_id=line.id,
                bank_ref=line.bank_ref,
                ledger_entry_ids=(entry.id,),
                doc_refs=(ref,),
                tier=0,
                decision="match",
                confidence=config.tier0_confidence,
                rationale=(
                    f"{ref} is quoted in the payment narrative, the amount "
                    f"{format_minor(entry.amount_minor, entry.currency)} agrees exactly, "
                    f"and the payment landed {when}."
                ),
                evidence=(
                    f"narrative:{ref}",
                    f"amount:{entry.amount_minor}",
                    f"date_offset:{offset}",
                ),
            )
        )
        claimed.add(entry.id)

    return matches
