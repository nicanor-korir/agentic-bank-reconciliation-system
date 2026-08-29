"""Load parsed source files into Postgres.

NON-NEGOTIABLE #6: re-ingesting the same file is a no-op, at two levels. The
file's sha256 is unique per tenant, so a repeated file short-circuits before
any row work; and every row carries a content_hash unique per tenant, so the
same row arriving inside a *different* file is still not duplicated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from recon.db import Db
from recon.hashing import sha256_hex
from recon.ingest.normalise import normalise_bank_line, normalise_ledger_entry
from recon.ingest.parsers import BankCsvParser, LedgerCsvParser


@dataclass
class LoadResult:
    filename: str
    kind: str
    source_id: int | None
    rows_read: int
    rows_inserted: int
    skipped_duplicate_file: bool = False

    @property
    def rows_deduped(self) -> int:
        return self.rows_read - self.rows_inserted


def ensure_tenant(conn: Db, tenant_id: str, name: str, currency: str) -> None:
    conn.execute(
        "insert into tenants (id, name, base_currency) values (%s, %s, %s) "
        "on conflict (id) do nothing",
        (tenant_id, name, currency),
    )


def _insert_source(
    conn: Db, tenant_id: str, kind: str, path: Path, period: str, row_count: int
) -> tuple[int | None, bool]:
    digest = sha256_hex(path.read_bytes())
    row = conn.execute(
        "insert into sources (tenant_id, kind, filename, period, sha256, row_count) "
        "values (%s, %s, %s, %s, %s, %s) "
        "on conflict (tenant_id, sha256) do nothing returning id",
        (tenant_id, kind, path.name, period, digest, row_count),
    ).fetchone()
    if row is None:
        return None, True
    return int(row["id"]), False


_BANK_SQL = """
insert into bank_lines (tenant_id, source_id, value_date, booking_date, amount_minor,
                        currency, narrative, counterparty, bank_ref, raw, content_hash)
values (%(tenant_id)s, %(source_id)s, %(value_date)s, %(booking_date)s, %(amount_minor)s,
        %(currency)s, %(narrative)s, %(counterparty)s, %(bank_ref)s, %(raw)s, %(content_hash)s)
on conflict (tenant_id, content_hash) do nothing
"""

_LEDGER_SQL = """
insert into ledger_entries (tenant_id, source_id, entry_date, due_date, amount_minor,
                            open_amount_minor, currency, description, counterparty,
                            doc_ref, side, status, raw, content_hash)
values (%(tenant_id)s, %(source_id)s, %(entry_date)s, %(due_date)s, %(amount_minor)s,
        %(open_amount_minor)s, %(currency)s, %(description)s, %(counterparty)s,
        %(doc_ref)s, %(side)s, %(status)s, %(raw)s, %(content_hash)s)
on conflict (tenant_id, content_hash) do nothing
"""


def _load(
    conn: Db,
    path: Path,
    tenant_id: str,
    period: str,
    parser: BankCsvParser | LedgerCsvParser,
    normalise: Any,
    sql: str,
) -> LoadResult:
    records = [normalise(row, tenant_id) for row in parser.parse(path)]
    source_id, duplicate = _insert_source(conn, tenant_id, parser.kind, path, period, len(records))
    if duplicate:
        return LoadResult(path.name, parser.kind, None, len(records), 0, True)

    inserted = 0
    for record in records:
        record["source_id"] = source_id
        record["raw"] = Jsonb(record["raw"])
        inserted += conn.execute(sql, record).rowcount
    return LoadResult(path.name, parser.kind, source_id, len(records), inserted)


def load_bank_statement(conn: Db, path: Path, tenant_id: str, period: str) -> LoadResult:
    return _load(conn, path, tenant_id, period, BankCsvParser(), normalise_bank_line, _BANK_SQL)


def load_ledger(conn: Db, path: Path, tenant_id: str, period: str) -> LoadResult:
    return _load(
        conn, path, tenant_id, period, LedgerCsvParser(), normalise_ledger_entry, _LEDGER_SQL
    )


def load_manifest_dir(conn: Db, data_dir: Path, tenant_id: str) -> list[LoadResult]:
    """Ingest every period described by the generator manifest, in order."""
    manifest = json.loads((data_dir / "manifest.json").read_text())
    ensure_tenant(conn, tenant_id, "Harborview Property Group", manifest["currency"])

    results: list[LoadResult] = []
    for period in manifest["periods"]:
        name = period["period"]
        base = data_dir / name
        # Ledger first: an open item must exist before a payment can point at it.
        results.append(load_ledger(conn, base / period["ledger_file"], tenant_id, name))
        results.append(load_bank_statement(conn, base / period["statement_file"], tenant_id, name))
    return results
