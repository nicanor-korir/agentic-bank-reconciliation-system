"""Weaviate collections.

Two collections, both multi-tenant on the client entity. Multi-tenancy is not
decoration: one deployment serves many clients, and a candidate leaking across
tenants would be a data-protection incident, not a bad match. Weaviate enforces
the isolation at the shard level, so a query that forgets its tenant fails
loudly instead of returning someone else's ledger.

Named vectors are used even though each collection has one, because the vector
is then addressed by name at query time -- adding a second view of the same
object later does not require reindexing the first.

Written against weaviate-client 4.23 signatures, checked with `inspect`, not
recalled.
"""

from __future__ import annotations

import weaviate.classes as wvc
from weaviate import WeaviateClient
from weaviate.collections.collection.sync import Collection

OPEN_ITEMS = "OpenItem"
RESOLVED_PAIRS = "ResolvedPair"

OPEN_ITEM_VECTOR = "description"
RESOLVED_PAIR_VECTOR = "narrative"


def ensure_schema(client: WeaviateClient) -> None:
    """Idempotent. Safe to call on every startup."""
    if not client.collections.exists(OPEN_ITEMS):
        client.collections.create(
            name=OPEN_ITEMS,
            properties=[
                wvc.config.Property(name="ledger_entry_id", data_type=wvc.config.DataType.INT),
                wvc.config.Property(name="doc_ref", data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="description", data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="counterparty", data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="side", data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="amount_minor", data_type=wvc.config.DataType.INT),
            ],
            multi_tenancy_config=wvc.config.Configure.multi_tenancy(
                enabled=True, auto_tenant_creation=True, auto_tenant_activation=True
            ),
            vector_config=[
                wvc.config.Configure.Vectors.text2vec_transformers(
                    name=OPEN_ITEM_VECTOR,
                    source_properties=["description", "counterparty"],
                    vectorize_collection_name=False,
                )
            ],
        )

    if not client.collections.exists(RESOLVED_PAIRS):
        client.collections.create(
            name=RESOLVED_PAIRS,
            properties=[
                wvc.config.Property(name="bank_ref", data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="narrative", data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="counterparty", data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="doc_ref", data_type=wvc.config.DataType.TEXT),
                wvc.config.Property(name="amount_minor", data_type=wvc.config.DataType.INT),
            ],
            multi_tenancy_config=wvc.config.Configure.multi_tenancy(
                enabled=True, auto_tenant_creation=True, auto_tenant_activation=True
            ),
            vector_config=[
                wvc.config.Configure.Vectors.text2vec_transformers(
                    name=RESOLVED_PAIR_VECTOR,
                    source_properties=["narrative"],
                    vectorize_collection_name=False,
                )
            ],
        )


def open_items(client: WeaviateClient, tenant: str) -> Collection:
    return client.collections.use(OPEN_ITEMS).with_tenant(tenant)


def resolved_pairs(client: WeaviateClient, tenant: str) -> Collection:
    return client.collections.use(RESOLVED_PAIRS).with_tenant(tenant)


def drop_all(client: WeaviateClient) -> None:
    """Only for tests and `make reset`."""
    for name in (OPEN_ITEMS, RESOLVED_PAIRS):
        if client.collections.exists(name):
            client.collections.delete(name)
