"""FastAPI surface.

Phase 1 is a health endpoint and a dataset summary -- enough for `make up` to
prove the stack is wired end to end. Runs, SSE progress, the exception queue
and audit drill-down arrive in Phases 4-5.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from recon import __version__
from recon.config import get_settings
from recon.db import connect

app = FastAPI(title="Reconciliation", version=__version__)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "recon", "version": __version__}


@app.get("/stats")
def stats() -> dict[str, Any]:
    tenant = get_settings().recon_tenant
    with connect() as conn:
        counts = conn.execute(
            """
            select
              (select count(*) from sources where tenant_id = %(t)s)        as sources,
              (select count(*) from bank_lines where tenant_id = %(t)s)     as bank_lines,
              (select count(*) from ledger_entries where tenant_id = %(t)s) as ledger_entries,
              (select count(*) from ledger_entries
                 where tenant_id = %(t)s and status = 'open')               as open_ledger_entries
            """,
            {"t": tenant},
        ).fetchone()
    return {"tenant": tenant, **{k: int(v) for k, v in (counts or {}).items()}}
