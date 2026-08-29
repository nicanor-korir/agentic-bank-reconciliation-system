"""Canonical hashing.

One canonical-JSON implementation with three uses -- source-row idempotency
(#6), the append-only event chain (#5), and the LLM request cache that makes
replay exact (NOTES.md 0.4a). They share this module deliberately: if the
canonical form ever drifts, all three break together and loudly, rather than
one of them silently disagreeing with the other two.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

ZERO_HASH = "0" * 64


def _default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        # Money must already be minor units by the time it is hashed. A Decimal
        # here means somebody hashed a pre-conversion value; normalise it to a
        # string so the hash is at least stable and reviewable.
        return str(obj)
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    if isinstance(obj, tuple | set | frozenset):
        return sorted(obj, key=repr) if isinstance(obj, set | frozenset) else list(obj)
    raise TypeError(f"{type(obj).__name__} is not canonically serialisable")


def canonical_json(payload: Any) -> str:
    """Deterministic JSON: sorted keys, no insignificant whitespace, ASCII-safe.

    ensure_ascii=True is deliberate -- it keeps the hashed bytes stable
    regardless of the filesystem or terminal encoding a run happens to use.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_default,
        allow_nan=False,
    )


def sha256_hex(data: str | bytes) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def content_hash(payload: Any) -> str:
    """Idempotency key for an ingested source row (#6)."""
    return sha256_hex(canonical_json(payload))


def chain_hash(prev_hash: str, payload: Any) -> str:
    """Next link in a run's append-only audit chain (#5)."""
    if len(prev_hash) != 64:
        raise ValueError(f"prev_hash must be 64 hex chars, got {len(prev_hash)}")
    return sha256_hex(prev_hash + canonical_json(payload))


def verify_chain(links: list[tuple[str, str, Any]]) -> int | None:
    """Verify an ordered (prev_hash, hash, payload) chain.

    Returns the index of the first broken link, or None if the chain is intact.
    """
    expected_prev = ZERO_HASH
    for i, (prev_hash, this_hash, payload) in enumerate(links):
        if prev_hash != expected_prev or chain_hash(prev_hash, payload) != this_hash:
            return i
        expected_prev = this_hash
    return None
