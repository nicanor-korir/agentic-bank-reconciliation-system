"""Migration runner.

Plain .sql files applied in filename order. Alembic is machinery this project
would never exercise -- one schema, one phase boundary at which it may change.
"""

from __future__ import annotations

from pathlib import Path

from recon.db.engine import connect
from recon.hashing import sha256_hex

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"

_BOOTSTRAP = """
create table if not exists schema_migrations (
  filename    text primary key,
  sha256      char(64) not null,
  applied_at  timestamptz not null default now()
)
"""


def apply_migrations(directory: Path | None = None) -> list[str]:
    """Apply pending migrations. Returns the filenames applied this call."""
    directory = directory or MIGRATIONS_DIR
    files = sorted(directory.glob("*.sql"))
    applied: list[str] = []

    with connect() as conn:
        conn.execute(_BOOTSTRAP)
        conn.commit()
        rows = conn.execute("select filename, sha256 from schema_migrations").fetchall()
        seen = {r["filename"]: r["sha256"] for r in rows}

        for path in files:
            body = path.read_text()
            digest = sha256_hex(body)
            if path.name in seen:
                if seen[path.name] != digest:
                    raise RuntimeError(
                        f"{path.name} changed after being applied. Migrations are "
                        f"immutable once run -- add a new file instead."
                    )
                continue
            with conn.transaction():
                conn.execute(body)
                conn.execute(
                    "insert into schema_migrations (filename, sha256) values (%s, %s)",
                    (path.name, digest),
                )
            applied.append(path.name)
    return applied
