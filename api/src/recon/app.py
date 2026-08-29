"""HTTP surface.

Read endpoints go straight to Postgres; the two write endpoints drive the
graph. Live progress is served by tailing the audit log rather than by pushing
from the graph, so a reconnecting client sees the same history and progress can
never report a step the audit trail does not contain.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from recon import __version__
from recon.api.queries import get_run, line_audit, list_runs, run_events
from recon.api.service import RunService, pairs_from_resolutions, write_back
from recon.config import get_settings
from recon.db import connect
from recon.graph.runner import get_queue
from recon.llm.pricing import format_micro


class RunRequest(BaseModel):
    period: str = "2026-06"
    adjudicator: str = "auto"


class Resolution(BaseModel):
    """One reviewer decision.

    `approve` needs the ledger entries being accepted; `reject` asserts none of
    the candidates is right and deliberately carries none.
    """

    bank_ref: str
    action: str = Field(pattern="^(approve|reject|reassign)$")
    ledger_entry_ids: list[int] = Field(default_factory=list)
    doc_refs: list[str] = Field(default_factory=list)
    reviewer: str = "reviewer"
    note: str | None = None


class ResolveRequest(BaseModel):
    resolutions: list[Resolution] = Field(min_length=1)


app = FastAPI(title="Reconciliation", version=__version__)

# The web container serves from a different origin in development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_settings = get_settings()
_service = RunService(_settings)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "recon", "version": __version__}


@app.get("/stats")
def stats() -> dict[str, Any]:
    tenant = _settings.recon_tenant
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


@app.get("/runs")
def runs() -> list[dict[str, Any]]:
    with connect() as conn:
        return list_runs(conn, _settings.recon_tenant)


@app.post("/runs")
def create_run(body: RunRequest) -> dict[str, str]:
    period = body.period
    adjudicator = body.adjudicator
    if adjudicator == "auto":
        adjudicator = "anthropic" if _settings.anthropic_api_key else "stub"
    run_id = _service.start(period, adjudicator)
    return {"run_id": run_id, "adjudicator": adjudicator}


@app.get("/runs/{run_id}")
def run_detail(run_id: str) -> dict[str, Any]:
    with connect() as conn:
        run = get_run(conn, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id}")
    handle = _service.status(run_id)
    run["in_flight"] = bool(handle and handle.thread.is_alive())
    run["error"] = handle.error if handle else None
    run["cost_display"] = format_micro(int(run.get("cost_total_micro") or 0))
    return run


@app.get("/runs/{run_id}/events")
def events(run_id: str, after: int = -1) -> list[dict[str, Any]]:
    with connect() as conn:
        return run_events(conn, run_id, after)


@app.get("/runs/{run_id}/stream")
def stream(run_id: str) -> StreamingResponse:
    """Server-sent events, tailing the audit log for this run."""

    def generate() -> Iterator[str]:
        last = -1
        idle = 0
        while idle < 300:  # ~5 minutes of silence ends the stream
            with connect() as conn:
                new = run_events(conn, run_id, last)
                run = get_run(conn, run_id)
            for event in new:
                last = int(event["seq"])
                idle = 0
                yield (
                    "event: node\ndata: "
                    + json.dumps(
                        {
                            "seq": event["seq"],
                            "node": event["node"],
                            "payload": event["payload"],
                            "hash": event["hash"][:12],
                        },
                        default=str,
                    )
                    + "\n\n"
                )

            handle = _service.status(run_id)
            status = (run or {}).get("status")
            in_flight = bool(handle and handle.thread.is_alive())
            if not new:
                idle += 1
            if status in {"completed", "halted_cost", "failed"} and not in_flight:
                yield "event: done\ndata: " + json.dumps({"status": status}) + "\n\n"
                return
            time.sleep(1)
        yield 'event: done\ndata: {"status": "timeout"}\n\n'

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/runs/{run_id}/queue")
def queue(run_id: str) -> dict[str, Any]:
    items = get_queue(_settings, run_id)
    return {"run_id": run_id, "count": len(items), "items": items}


@app.post("/runs/{run_id}/resolve")
def resolve(run_id: str, body: ResolveRequest) -> dict[str, Any]:
    """Apply reviewer decisions, resume the graph, and write back to retrieval."""
    resolutions = [r.model_dump() for r in body.resolutions]
    items = get_queue(_settings, run_id)
    entry_ids = {int(e) for r in resolutions for e in (r.get("ledger_entry_ids") or [])}
    counterparty_by_entry: dict[int, str] = {}
    if entry_ids:
        with connect() as conn:
            rows = conn.execute(
                "select id, counterparty from ledger_entries where id = any(%s)",
                (list(entry_ids),),
            ).fetchall()
        counterparty_by_entry = {
            int(r["id"]): str(r["counterparty"]) for r in rows if r["counterparty"]
        }

    summary = _service.resume(run_id, resolutions)

    # Write-back is best effort on purpose: the decision is already committed,
    # so a retrieval failure costs future recall, never accuracy.
    written = 0
    write_back_error: str | None = None
    try:
        written = write_back(
            _settings, pairs_from_resolutions(items, resolutions, counterparty_by_entry)
        )
    except Exception as exc:
        write_back_error = f"{type(exc).__name__}: {exc}"

    summary["written_back"] = written
    summary["write_back_error"] = write_back_error
    summary["cost_display"] = format_micro(int(summary.get("cost_micro") or 0))
    return summary


@app.get("/lines/{bank_ref}/audit")
def audit(bank_ref: str) -> dict[str, Any]:
    """Every node, decision, review and model call that touched one line."""
    with connect() as conn:
        payload = line_audit(conn, _settings.recon_tenant, bank_ref)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"unknown bank line {bank_ref}")
    return payload
