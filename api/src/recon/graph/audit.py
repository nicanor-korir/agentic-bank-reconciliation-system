"""Append-only, hash-chained audit log (NON-NEGOTIABLE #5).

Every node writes an event before returning. The chain is per run, and each
link hashes the previous hash together with the canonical payload, so removing
or editing an event breaks every link after it.

The writer reloads its tail from the database rather than trusting memory,
because a run resumes in a different process after an interrupt and an
in-memory `prev_hash` would be stale -- producing a chain that verifies inside
one process and fails everywhere else.
"""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from recon.db.engine import Db
from recon.hashing import ZERO_HASH, chain_hash, verify_chain


class EventLog:
    def __init__(self, conn: Db, run_id: str) -> None:
        self._conn = conn
        self._run_id = run_id

    def _tail(self) -> tuple[int, str]:
        row = self._conn.execute(
            "select seq, hash from events where run_id = %s order by seq desc limit 1",
            (self._run_id,),
        ).fetchone()
        return (-1, ZERO_HASH) if row is None else (int(row["seq"]), str(row["hash"]))

    def append(self, node: str, payload: dict[str, Any]) -> str:
        seq, prev_hash = self._tail()
        digest = chain_hash(prev_hash, payload)
        self._conn.execute(
            "insert into events (run_id, seq, node, payload, prev_hash, hash) "
            "values (%s, %s, %s, %s, %s, %s)",
            (self._run_id, seq + 1, node, Jsonb(payload), prev_hash, digest),
        )
        return digest

    def verify(self) -> int | None:
        """Index of the first broken link, or None when the chain is intact."""
        rows = self._conn.execute(
            "select prev_hash, hash, payload from events where run_id = %s order by seq",
            (self._run_id,),
        ).fetchall()
        return verify_chain([(r["prev_hash"], r["hash"], r["payload"]) for r in rows])
