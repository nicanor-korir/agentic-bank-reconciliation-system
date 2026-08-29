"""Retrieval interface.

Tier 2 depends on this protocol, not on Weaviate. Two reasons that matter: the
candidate-generation logic stays unit-testable without a vector database, and
`recall@10` can be measured for retrieval-off and retrieval-on arms by swapping
the implementation rather than reconfiguring a service.

Note what a resolved pair carries. The transferable signal from a human
correction is **the counterparty**, not the invoice -- June's invoice is closed
by July, so remembering "this narrative meant INV-2026-06-0231" is useless next
month, while "this narrative shape means Cedarbrook Holdings" keeps paying off.
That distinction is the whole feedback loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class OpenItemHit:
    ledger_entry_id: int
    doc_ref: str | None
    # Per mille (0-1000). Integer so that nothing downstream of retrieval
    # needs a float, which keeps the money guard absolute in matching/.
    score_milli: int


@dataclass(frozen=True, slots=True)
class ResolvedPairHit:
    narrative: str
    counterparty: str
    doc_ref: str | None
    score_milli: int


@dataclass(frozen=True, slots=True)
class ResolvedPair:
    """A human-confirmed match, written back so retrieval improves."""

    bank_ref: str
    narrative: str
    counterparty: str
    doc_ref: str | None
    amount_minor: int


@runtime_checkable
class NarrativeIndex(Protocol):
    def search_open_items(
        self, tenant: str, narrative: str, side: str, limit: int
    ) -> list[OpenItemHit]: ...

    def search_resolved_pairs(
        self, tenant: str, narrative: str, limit: int
    ) -> list[ResolvedPairHit]: ...

    # Recording hooks. Retrieval is per bank line but the search signatures do
    # not carry the id, so the caller announces which line it is about; a
    # recording implementation needs that to key what it stores.
    def bind(self, bank_line_id: int) -> None: ...

    def flush(self) -> int: ...


class NullIndex:
    """Retrieval disabled. The 'rules only' ablation arm runs against this."""

    def bind(self, bank_line_id: int) -> None:
        return None

    def flush(self) -> int:
        return 0

    def search_open_items(
        self, tenant: str, narrative: str, side: str, limit: int
    ) -> list[OpenItemHit]:
        return []

    def search_resolved_pairs(
        self, tenant: str, narrative: str, limit: int
    ) -> list[ResolvedPairHit]:
        return []
