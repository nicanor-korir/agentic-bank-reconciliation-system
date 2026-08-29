"""Read models for the UI.

Kept apart from the graph: the UI reads committed state out of Postgres and
never holds a graph handle. The audit drill-down in particular has to work for
a run that finished weeks ago in a process that no longer exists.
"""

from __future__ import annotations

from typing import Any

from recon.db.engine import Db


def list_runs(conn: Db, tenant: str, limit: int = 25) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select r.id, r.started_at, r.ended_at, r.status, r.model_version,
               r.git_sha, r.git_dirty, r.cost_total_micro, r.replay_of,
               r.config_snapshot->>'adjudicator' as adjudicator,
               (select count(*) from decisions d
                 where d.run_id = r.id and d.auto_committed) as auto_committed,
               (select count(*) from decisions d
                 where d.run_id = r.id and d.decision = 'escalated') as escalated
        from runs r
        where r.tenant_id = %s
        order by r.started_at desc
        limit %s
        """,
        (tenant, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_run(conn: Db, run_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "select id, tenant_id, started_at, ended_at, status, config_snapshot, "
        "model_version, prompt_version, git_sha, git_dirty, seed, cost_total_micro, "
        "replay_of from runs where id = %s",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    run = dict(row)
    tiers = conn.execute(
        "select tier, count(*) as n from decisions where run_id = %s and auto_committed "
        "group by tier order by tier",
        (run_id,),
    ).fetchall()
    run["by_tier"] = {str(t["tier"]): int(t["n"]) for t in tiers}
    run["adjudicator"] = (run.get("config_snapshot") or {}).get("adjudicator")
    return run


def run_events(conn: Db, run_id: str, after_seq: int = -1) -> list[dict[str, Any]]:
    rows = conn.execute(
        "select seq, node, payload, hash, prev_hash, created_at from events "
        "where run_id = %s and seq > %s order by seq",
        (run_id, after_seq),
    ).fetchall()
    return [dict(r) for r in rows]


def line_audit(conn: Db, tenant: str, bank_ref: str) -> dict[str, Any] | None:
    """Everything that touched one transaction.

    The question a finance buyer actually asks -- "show me this one" -- and the
    reason decisions are append-only: the superseded escalation is still here,
    next to what replaced it.
    """
    line = conn.execute(
        "select id, bank_ref, value_date, booking_date, amount_minor, currency, "
        "narrative, counterparty, content_hash from bank_lines "
        "where tenant_id = %s and bank_ref = %s",
        (tenant, bank_ref),
    ).fetchone()
    if line is None:
        return None

    decisions = conn.execute(
        """
        select d.id, d.run_id, d.tier, d.decision, d.confidence, d.rationale,
               d.evidence, d.auto_committed, d.supersedes_id, d.created_at,
               d.ledger_entry_ids,
               coalesce(
                 (select array_agg(le.doc_ref order by le.doc_ref)
                  from ledger_entries le where le.id = any(d.ledger_entry_ids)),
                 '{}'
               ) as doc_refs
        from decisions d
        where d.bank_line_id = %s
        order by d.created_at, d.id
        """,
        (line["id"],),
    ).fetchall()

    reviews = conn.execute(
        "select hr.* from human_reviews hr join decisions d on d.id = hr.decision_id "
        "where d.bank_line_id = %s order by hr.reviewed_at",
        (line["id"],),
    ).fetchall()

    calls = conn.execute(
        "select id, run_id, request_hash, input_tokens, output_tokens, cost_micro, "
        "latency_ms, created_at, response from llm_calls where bank_line_id = %s "
        "order by created_at",
        (line["id"],),
    ).fetchall()

    events = conn.execute(
        "select e.seq, e.node, e.hash, e.created_at, e.run_id from events e "
        "where e.run_id in (select distinct run_id from decisions where bank_line_id = %s) "
        "order by e.run_id, e.seq",
        (line["id"],),
    ).fetchall()

    return {
        "line": dict(line),
        "decisions": [dict(d) for d in decisions],
        "human_reviews": [dict(r) for r in reviews],
        "model_calls": [dict(c) for c in calls],
        "events": [dict(e) for e in events],
    }


def tier_breakdown(conn: Db, tenant: str, period: str) -> dict[str, Any]:
    row = conn.execute(
        "select count(*) as lines from bank_lines bl join sources s on s.id = bl.source_id "
        "where bl.tenant_id = %s and s.period = %s",
        (tenant, period),
    ).fetchone()
    return {"period": period, "bank_lines": int(row["lines"]) if row else 0}
