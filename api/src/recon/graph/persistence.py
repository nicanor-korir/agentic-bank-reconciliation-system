"""Writing runs, decisions and recorded model calls.

`decisions` is append-only and enforced by a database trigger, so a human
correction inserts a new row pointing at the one it supersedes rather than
updating it. That is what makes "who decided what, and when" answerable months
later.
"""

from __future__ import annotations

import subprocess
from typing import Any

from psycopg.types.json import Jsonb

from recon.config import Settings
from recon.db.engine import Db


def git_state(settings: Settings) -> tuple[str, bool]:
    """Prefer what the Makefile injected; fall back to asking git directly."""
    if settings.git_sha != "unknown":
        return settings.git_sha, settings.git_dirty
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            ).stdout.strip()
        )
        return sha, dirty
    except Exception:
        return "unknown", True


def create_run(
    conn: Db,
    run_id: str,
    settings: Settings,
    prompt_version: str,
    adjudicator: str,
) -> None:
    sha, dirty = git_state(settings)
    config = settings.match.model_dump(mode="json")
    # Which adjudicator produced the decisions is part of the run's identity:
    # a stub run must never be mistaken for a model run in a report.
    config["adjudicator"] = adjudicator
    conn.execute(
        "insert into runs (id, tenant_id, status, config_snapshot, model_version, "
        "prompt_version, git_sha, git_dirty, seed) "
        "values (%s, %s, 'running', %s, %s, %s, %s, %s, %s)",
        (
            run_id,
            settings.recon_tenant,
            Jsonb(config),
            settings.match.model_version,
            prompt_version,
            sha,
            dirty,
            settings.recon_seed,
        ),
    )


def finish_run(conn: Db, run_id: str, status: str, cost_micro: int) -> None:
    conn.execute(
        "update runs set status = %s, ended_at = now(), cost_total_micro = %s where id = %s",
        (status, cost_micro, run_id),
    )


def insert_decision(
    conn: Db,
    run_id: str,
    tenant: str,
    record: dict[str, Any],
    supersedes_id: int | None = None,
) -> int:
    row = conn.execute(
        "insert into decisions (run_id, tenant_id, bank_line_id, ledger_entry_ids, tier, "
        "decision, confidence, rationale, evidence, auto_committed, supersedes_id) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
        (
            run_id,
            tenant,
            record["bank_line_id"],
            list(record["ledger_entry_ids"]),
            record["tier"],
            record["decision"],
            record["confidence"],
            record["rationale"],
            Jsonb(list(record["evidence"])),
            record["auto_committed"],
            supersedes_id,
        ),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def record_llm_call(conn: Db, run_id: str, bank_line_id: int, adjudication: Any) -> None:
    """Persist the exact request and response, keyed by the request hash.

    This is what makes replay exact without pretending the model is
    deterministic. Recordings served during a replay are not written again.
    """
    if adjudication.served_from_recording:
        return
    conn.execute(
        "insert into llm_calls (run_id, bank_line_id, request_hash, request, response, "
        "input_tokens, output_tokens, cost_micro, latency_ms) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            run_id,
            bank_line_id,
            adjudication.request_hash,
            Jsonb(adjudication.request),
            Jsonb(adjudication.response),
            adjudication.usage.input_tokens,
            adjudication.usage.output_tokens,
            adjudication.cost_micro,
            adjudication.latency_ms,
        ),
    )


def load_recordings(conn: Db, run_id: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "select request_hash, response, latency_ms from llm_calls where run_id = %s",
        (run_id,),
    ).fetchall()
    return {
        str(r["request_hash"]): {"response": r["response"], "latency_ms": r["latency_ms"]}
        for r in rows
    }


def insert_human_review(
    conn: Db,
    decision_id: int,
    reviewer: str,
    action: str,
    corrected: list[int],
    note: str | None,
) -> int:
    row = conn.execute(
        "insert into human_reviews (decision_id, reviewer, action, "
        "corrected_ledger_entry_ids, note) values (%s, %s, %s, %s, %s) returning id",
        (decision_id, reviewer, action, corrected, note),
    ).fetchone()
    assert row is not None
    return int(row["id"])
