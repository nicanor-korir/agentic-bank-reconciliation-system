"""Ablation: what does each layer actually buy?

The brief calls this slide worth more than the UI, and it is the one number a
technical buyer can check. Rows are added as the phases land; anything not yet
built is absent from the table rather than reported as zero, because a zero
reads as "we tried it and it did nothing".
"""

from __future__ import annotations

from dataclasses import dataclass

from recon.config import MatchConfig
from recon.matching import run_deterministic
from recon.matching.types import BankLine, LedgerEntry, Match


@dataclass(frozen=True, slots=True)
class Arm:
    key: str
    label: str
    max_tier: int


ARMS: tuple[Arm, ...] = (
    Arm("tier0", "Tier 0 only (exact reference)", 0),
    Arm("tier0_1", "Tiers 0-1 (all deterministic rules)", 1),
)


def run_arm(
    arm: Arm,
    lines: list[BankLine],
    entries: list[LedgerEntry],
    config: MatchConfig,
) -> tuple[dict[str, Match], dict[int, int], int]:
    """Returns (matches keyed by bank_ref, tier counts, unmatched count)."""
    result = run_deterministic(lines, entries, config, max_tier=arm.max_tier)
    return (
        {m.bank_ref: m for m in result.matches},
        result.by_tier,
        len(result.unmatched),
    )
