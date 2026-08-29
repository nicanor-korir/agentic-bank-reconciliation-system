"""Recording and replaying retrieval.

`RecordingIndex` wraps a live index and writes what it returned.
`ReplayIndex` serves those recordings and fails on a miss, exactly like the
model path -- a miss means the query changed, which is the regression worth
catching.

Why retrieval is recorded at all: a human correction written back to the vector
store changes what retrieval returns for the *same* bank line. That is the
feedback loop working as intended, and it means re-executing retrieval during a
replay compares two different worlds. Recording it keeps replay honest without
freezing the deterministic matching logic, which is still re-executed in full.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from recon.db.engine import Db
from recon.hashing import canonical_json, sha256_hex
from recon.retrieval.base import NarrativeIndex, OpenItemHit, ResolvedPairHit


class RetrievalReplayMissError(RuntimeError):
    """No recorded retrieval for this query during a replay."""


def query_hash(kind: str, tenant: str, narrative: str, side: str, limit: int) -> str:
    return sha256_hex(
        canonical_json(
            {"kind": kind, "tenant": tenant, "narrative": narrative, "side": side, "limit": limit}
        )
    )


class RecordingIndex:
    """Passes through to a real index and records every response."""

    def __init__(self, inner: NarrativeIndex, conn_factory: Any, run_id: str) -> None:
        self._inner = inner
        self._conn_factory = conn_factory
        self._run_id = run_id
        self._pending: list[tuple[int, str, str, list[dict[str, Any]]]] = []

    def bind(self, bank_line_id: int) -> None:
        """Retrieval is per bank line, but the protocol does not carry the id."""
        self._line_id = bank_line_id

    def search_open_items(
        self, tenant: str, narrative: str, side: str, limit: int
    ) -> list[OpenItemHit]:
        hits = self._inner.search_open_items(tenant, narrative, side, limit)
        self._record(
            "open_items",
            query_hash("open_items", tenant, narrative, side, limit),
            [
                {
                    "ledger_entry_id": h.ledger_entry_id,
                    "doc_ref": h.doc_ref,
                    "score_milli": h.score_milli,
                }
                for h in hits
            ],
        )
        return hits

    def search_resolved_pairs(
        self, tenant: str, narrative: str, limit: int
    ) -> list[ResolvedPairHit]:
        hits = self._inner.search_resolved_pairs(tenant, narrative, limit)
        self._record(
            "resolved_pairs",
            query_hash("resolved_pairs", tenant, narrative, "", limit),
            [
                {
                    "narrative": h.narrative,
                    "counterparty": h.counterparty,
                    "doc_ref": h.doc_ref,
                    "score_milli": h.score_milli,
                }
                for h in hits
            ],
        )
        return hits

    def _record(self, kind: str, digest: str, payload: list[dict[str, Any]]) -> None:
        self._pending.append((getattr(self, "_line_id", 0), kind, digest, payload))

    def flush(self) -> int:
        """Written in one batch at the end of the node, not per query."""
        if not self._pending:
            return 0
        rows = self._pending
        self._pending = []
        with self._conn_factory() as conn:
            db: Db = conn
            for line_id, kind, digest, payload in rows:
                db.execute(
                    "insert into retrieval_calls (run_id, bank_line_id, kind, query_hash, "
                    "response) values (%s, %s, %s, %s, %s) "
                    "on conflict (run_id, bank_line_id, kind) do nothing",
                    (self._run_id, line_id, kind, digest, Jsonb(payload)),
                )
        return len(rows)


class ReplayIndex:
    """Serves recorded retrieval. A miss is a failure, never a live query."""

    def __init__(self, recordings: dict[str, list[dict[str, Any]]]) -> None:
        self._recordings = recordings
        self.misses: list[str] = []

    def bind(self, bank_line_id: int) -> None:
        return None

    def flush(self) -> int:
        return 0

    def _lookup(self, digest: str, kind: str) -> list[dict[str, Any]]:
        payload = self._recordings.get(digest)
        if payload is None:
            self.misses.append(digest)
            raise RetrievalReplayMissError(
                f"no recorded {kind} retrieval for query {digest[:12]}; the query "
                f"changed since the recorded run"
            )
        return payload

    def search_open_items(
        self, tenant: str, narrative: str, side: str, limit: int
    ) -> list[OpenItemHit]:
        digest = query_hash("open_items", tenant, narrative, side, limit)
        return [OpenItemHit(**row) for row in self._lookup(digest, "open_items")]

    def search_resolved_pairs(
        self, tenant: str, narrative: str, limit: int
    ) -> list[ResolvedPairHit]:
        digest = query_hash("resolved_pairs", tenant, narrative, "", limit)
        return [ResolvedPairHit(**row) for row in self._lookup(digest, "resolved_pairs")]


def load_retrieval_recordings(conn: Db, run_id: str) -> dict[str, list[dict[str, Any]]]:
    rows = conn.execute(
        "select query_hash, response from retrieval_calls where run_id = %s", (run_id,)
    ).fetchall()
    return {str(r["query_hash"]): list(r["response"]) for r in rows}
