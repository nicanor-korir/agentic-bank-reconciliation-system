"""Hybrid retrieval over Weaviate.

`alpha` sits at 0.4, i.e. weighted toward BM25. That is a considered default,
not a shrug: bank narratives are templated machine text, so the signal is
rare-token overlap -- invoice references, processor codes, payer names -- and
not paraphrase similarity. The knob is in `MatchConfig`, so the eval can sweep
it and the choice can be defended with a number instead of an opinion.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any

import weaviate
import weaviate.classes as wvc
from weaviate import WeaviateClient
from weaviate.classes.init import AdditionalConfig, Timeout
from weaviate.collections.collection.sync import Collection

from recon.config import MatchConfig, Settings
from recon.matching.types import LedgerEntry
from recon.retrieval.base import OpenItemHit, ResolvedPair, ResolvedPairHit
from recon.retrieval.schema import (
    OPEN_ITEM_VECTOR,
    OPEN_ITEMS,
    RESOLVED_PAIR_VECTOR,
    RESOLVED_PAIRS,
    ensure_schema,
    open_items,
    resolved_pairs,
)


def _batch_insert(collection: Collection, rows: list[dict[str, Any]]) -> int:
    """Insert through the batch helper rather than `insert_many`.

    A single `insert_many` of ~1,800 objects blows the gRPC deadline, because
    every object is embedded synchronously by a CPU-only sidecar. The batch
    helper chunks the work and surfaces per-object failures instead of failing
    the whole call, so a partial index is visible rather than silent.
    """
    if not rows:
        return 0
    with collection.batch.fixed_size(batch_size=64, concurrent_requests=2) as batch:
        for row in rows:
            batch.add_object(properties=row)
    failed = collection.batch.failed_objects
    if failed:
        raise RuntimeError(
            f"{len(failed)} object(s) failed to index; first error: {failed[0].message}"
        )
    return len(rows)


def _milli(score: float | None) -> int:
    """Weaviate returns a float relevance score; convert once, at the boundary.

    This module is the only place in the retrieval/matching path that handles a
    float at all, and it never touches a monetary value.
    """
    return max(0, min(1000, round((score or 0.0) * 1000)))


@contextmanager
def connect(settings: Settings) -> Iterator[WeaviateClient]:
    client = weaviate.connect_to_custom(
        http_host=settings.weaviate_host,
        http_port=settings.weaviate_http_port,
        http_secure=False,
        grpc_host=settings.weaviate_host,
        grpc_port=settings.weaviate_grpc_port,
        grpc_secure=False,
        # Vectorisation runs on CPU in a sidecar, so an insert takes far longer
        # than the 90s default. The batch helper below keeps each request small,
        # but the ceiling still has to allow for a cold model.
        additional_config=AdditionalConfig(timeout=Timeout(init=60, query=120, insert=600)),
    )
    try:
        yield client
    finally:
        client.close()


class WeaviateIndex:
    """Implements `NarrativeIndex` against a live Weaviate."""

    def __init__(self, client: WeaviateClient, config: MatchConfig) -> None:
        self._client = client
        self._config = config
        ensure_schema(client)

    # -- indexing ---------------------------------------------------------

    def ensure_tenant(self, tenant: str) -> None:
        """Create the tenant shard on both collections.

        A tenant with no resolved pairs yet still has to be queryable -- on the
        first run nobody has corrected anything, and "tenant not found" would
        take the whole retrieval arm down rather than returning no history.
        """
        for name in (OPEN_ITEMS, RESOLVED_PAIRS):
            collection = self._client.collections.use(name)
            if not collection.tenants.exists(tenant):
                collection.tenants.create(tenant)

    def index_open_items(self, tenant: str, entries: Iterable[LedgerEntry]) -> int:
        self.ensure_tenant(tenant)
        collection = open_items(self._client, tenant)
        rows: list[dict[str, Any]] = [
            {
                "ledger_entry_id": e.id,
                "doc_ref": e.doc_ref or "",
                "description": e.description,
                "counterparty": e.counterparty or "",
                "side": e.side,
                "amount_minor": e.amount_minor,
            }
            for e in entries
            if e.status == "open"
        ]
        return _batch_insert(collection, rows)

    def index_resolved_pairs(self, tenant: str, pairs: Iterable[ResolvedPair]) -> int:
        self.ensure_tenant(tenant)
        collection = resolved_pairs(self._client, tenant)
        rows: list[dict[str, Any]] = [
            {
                "bank_ref": p.bank_ref,
                "narrative": p.narrative,
                "counterparty": p.counterparty,
                "doc_ref": p.doc_ref or "",
                "amount_minor": p.amount_minor,
            }
            for p in pairs
        ]
        return _batch_insert(collection, rows)

    def clear(self, tenant: str) -> None:
        """Drop the tenant's shard entirely.

        Cheaper and more thorough than a filtered delete, and it works on a
        tenant that does not exist yet -- `auto_tenant_creation` recreates it
        on the next insert. A filtered delete raises "tenant not found" on a
        first run, which turns `make seed` into a failure on a clean stack.
        """
        for name in (OPEN_ITEMS, RESOLVED_PAIRS):
            collection = self._client.collections.use(name)
            if collection.tenants.exists(tenant):
                collection.tenants.remove(tenant)

    # -- search -----------------------------------------------------------

    def search_open_items(
        self, tenant: str, narrative: str, side: str, limit: int
    ) -> list[OpenItemHit]:
        response = open_items(self._client, tenant).query.hybrid(
            query=narrative,
            alpha=self._config.hybrid_alpha,
            target_vector=OPEN_ITEM_VECTOR,
            query_properties=["description", "counterparty", "doc_ref"],
            filters=wvc.query.Filter.by_property("side").equal(side),
            limit=limit,
            return_metadata=wvc.query.MetadataQuery(score=True),
        )
        return [
            OpenItemHit(
                ledger_entry_id=int(o.properties["ledger_entry_id"]),  # type: ignore[arg-type]
                doc_ref=str(o.properties["doc_ref"]) or None,
                score_milli=_milli(o.metadata.score),
            )
            for o in response.objects
        ]

    def search_resolved_pairs(
        self, tenant: str, narrative: str, limit: int
    ) -> list[ResolvedPairHit]:
        response = resolved_pairs(self._client, tenant).query.hybrid(
            query=narrative,
            alpha=self._config.hybrid_alpha,
            target_vector=RESOLVED_PAIR_VECTOR,
            query_properties=["narrative"],
            limit=limit,
            return_metadata=wvc.query.MetadataQuery(score=True),
        )
        return [
            ResolvedPairHit(
                narrative=str(o.properties["narrative"]),
                counterparty=str(o.properties["counterparty"]),
                doc_ref=str(o.properties["doc_ref"]) or None,
                score_milli=_milli(o.metadata.score),
            )
            for o in response.objects
        ]
