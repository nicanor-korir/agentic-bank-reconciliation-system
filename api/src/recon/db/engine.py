"""Postgres access. psycopg3 directly -- no ORM.

An ORM would sit between us and `bigint` money columns and is the usual way a
float sneaks into a monetary path. Explicit SQL keeps NON-NEGOTIABLE #8
inspectable.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from recon.config import get_settings

# Rows are dicts everywhere. Positional row access plus a bigint money column
# is how a column reorder silently becomes a wrong amount.
Db = Connection[DictRow]


@lru_cache(maxsize=1)
def pool() -> ConnectionPool[Db]:
    settings = get_settings()
    p = ConnectionPool[Db](
        settings.database_url,
        kwargs={"row_factory": dict_row},
        min_size=1,
        max_size=10,
        open=False,
    )
    p.open(wait=True, timeout=30)
    return p


@contextmanager
def connect() -> Iterator[Db]:
    with pool().connection() as conn:
        yield conn


@contextmanager
def transaction() -> Iterator[Db]:
    with connect() as conn, conn.transaction():
        yield conn
