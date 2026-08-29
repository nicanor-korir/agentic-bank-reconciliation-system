"""`recon` command line.

Phase 1 covers migrate / generate / ingest / seed / stats. Later phases add
run, eval and replay -- declared in the Makefile so an early call fails with a
sentence rather than a stack trace.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from recon.config import get_settings
from recon.db import apply_migrations, transaction
from recon.ingest.loader import load_manifest_dir
from recon.seed.generator import generate


def _cmd_migrate(_: argparse.Namespace) -> int:
    applied = apply_migrations()
    print(f"applied {len(applied)} migration(s)" + (f": {', '.join(applied)}" if applied else ""))
    return 0


def _cmd_generate(args: argparse.Namespace) -> int:
    settings = get_settings()
    out = Path(args.out or settings.recon_data_dir)
    manifest = generate(out, settings.recon_seed, settings.recon_tenant)
    for period in manifest["periods"]:
        print(
            f"{period['period']}: {period['bank_line_count']} bank lines, "
            f"{period['ledger_entry_count']} ledger entries"
        )
    golden = manifest["golden_set"]["counts"]
    print(f"golden set: {golden['clean']} clean + {golden['hard']} hard")
    print(f"manifest: {out / 'manifest.json'}")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    settings = get_settings()
    data_dir = Path(args.dir or settings.recon_data_dir)
    with transaction() as conn:
        results = load_manifest_dir(conn, data_dir, settings.recon_tenant)
    for r in results:
        if r.skipped_duplicate_file:
            print(f"{r.filename}: already ingested, skipped ({r.rows_read} rows)")
        else:
            note = f", {r.rows_deduped} deduped" if r.rows_deduped else ""
            print(f"{r.filename}: {r.rows_inserted} inserted{note}")
    return 0


def _cmd_seed(args: argparse.Namespace) -> int:
    apply_migrations()
    _cmd_generate(args)
    return _cmd_ingest(args)


def _resolve_mode(settings: Any, mode: str) -> str:
    if mode != "auto":
        return mode
    if settings.anthropic_api_key:
        return "anthropic"
    print(
        "WARNING: ANTHROPIC_API_KEY is not set, so Tier 3 will run with the stub\n"
        "         adjudicator. It is NOT a model: it commits only exact single\n"
        "         matches and escalates everything else. It exercises the graph,\n"
        "         checkpointing, interrupts, audit chain and cost ceiling -- it\n"
        "         cannot demonstrate judgement, and its output must never be\n"
        "         reported as model quality.\n"
    )
    return "stub"


def _print_run(summary: dict[str, Any]) -> None:
    from recon.llm.pricing import format_micro

    print(f"run {summary['run_id']}  status {summary['status']}")
    print(f"  bank lines            {summary['bank_lines']}")
    print(f"  committed             {summary['committed']}  by tier {summary['by_tier']}")
    print(f"  queued for human      {summary['queued_for_human']}")
    print(f"  unresolved            {summary['unresolved']}")
    print(f"  model calls           {summary['llm_calls']}")
    if summary.get("adjudication_errors"):
        print(
            f"  ADJUDICATION ERRORS   {summary['adjudication_errors']} "
            f"(escalated, never auto-committed)"
        )
    print(f"  cost                  {format_micro(summary['cost_micro'])}")
    if summary.get("halt_reason"):
        print(f"  HALTED: {summary['halt_reason']}")


def _cmd_run(args: argparse.Namespace) -> int:
    from recon.graph.runner import start_run
    from recon.llm.adjudicator import ReplayMissError

    settings = get_settings()
    mode = _resolve_mode(settings, args.adjudicator)
    try:
        summary = start_run(
            settings,
            period=args.period,
            adjudicator_mode=mode,
            use_retrieval=not args.no_retrieval,
            replay_of=args.replay_of,
        )
    except ReplayMissError as exc:
        # Non-zero, loudly. A replay that finishes with a longer review queue
        # instead of failing is the one outcome replay must never produce.
        print(f"REPLAY FAILED: {exc}")
        print(
            "  The recorded run cannot be reproduced because the input to the "
            "model differs.\n"
            "  Check for changes to the prompt, the matching config, the "
            "candidate set, or the\n  underlying ledger data since "
            f"{args.replay_of}."
        )
        return 1
    _print_run(summary)
    if summary["status"] == "awaiting_human":
        print(f"\n  resume with: recon resume {summary['run_id']} --simulate-reviewer")
    return 0


def _cmd_resume(args: argparse.Namespace) -> int:
    """Resume a paused run with reviewer decisions.

    `--simulate-reviewer` accepts the top candidate for every queued item. It
    exists so the interrupt/resume path can be exercised end to end; the
    reviewer is recorded as 'simulated' so nothing downstream can mistake it
    for a person.
    """
    import json

    from recon.graph.runner import resume_run

    settings = get_settings()
    if args.resolutions:
        resolutions = json.loads(Path(args.resolutions).read_text())
    elif args.simulate_reviewer:
        from recon.db import connect

        with connect() as conn:
            row = conn.execute(
                "select payload from events where run_id = %s and node = 'tier3_adjudicate' "
                "order by seq desc limit 1",
                (args.run_id,),
            ).fetchone()
        _ = row
        resolutions = _simulated_resolutions(settings, args.run_id)
    else:
        print("provide --resolutions FILE or --simulate-reviewer")
        return 2

    summary = resume_run(settings, args.run_id, resolutions)
    _print_run(summary)
    return 0


def _simulated_resolutions(settings: Any, run_id: str) -> list[dict[str, Any]]:
    """Accept the top-ranked candidate for each queued item."""
    from langgraph.checkpoint.postgres import PostgresSaver

    from recon.graph.build import build_graph
    from recon.graph.nodes import Deps
    from recon.graph.runner import build_index, choose_adjudicator, load_prompt
    from recon.llm.adjudicator import CostMeter

    prompt, _ = load_prompt()
    index, closer = build_index(settings, False)
    deps = Deps(
        settings,
        index,
        choose_adjudicator(settings, "stub"),
        prompt,
        CostMeter(settings.match.run_cost_ceiling_micro),
    )
    try:
        with PostgresSaver.from_conn_string(settings.database_url) as cp:
            graph = build_graph(deps, cp)
            snapshot = graph.get_state({"configurable": {"thread_id": run_id}})
            interrupts = getattr(snapshot, "interrupts", ()) or ()
            queue = (
                (getattr(interrupts[0], "value", {}) or {}).get("queue", []) if interrupts else []
            )
    finally:
        if closer is not None:
            closer.__exit__(None, None, None)

    out: list[dict[str, Any]] = []
    for item in queue:
        candidates = item.get("candidates") or []
        if not candidates:
            out.append(
                {
                    "bank_ref": item["bank_ref"],
                    "action": "reject",
                    "reviewer": "simulated",
                    "note": "No candidate offered.",
                }
            )
            continue
        top = candidates[0]
        out.append(
            {
                "bank_ref": item["bank_ref"],
                "action": "approve",
                "reviewer": "simulated",
                "ledger_entry_ids": top["ledger_entry_ids"],
                "doc_refs": top["doc_refs"],
                "note": (
                    f"Accepted {', '.join(top['doc_refs']) or 'candidate'} (simulated reviewer)."
                ),
            }
        )
    return out


def _cmd_index(args: argparse.Namespace) -> int:
    """Build the Weaviate index from what is already in Postgres."""
    from recon.db import connect, load_ledger_entries
    from recon.retrieval.weaviate_index import WeaviateIndex
    from recon.retrieval.weaviate_index import connect as weaviate_connect

    settings = get_settings()
    with connect() as conn:
        entries = load_ledger_entries(conn, settings.recon_tenant)

    with weaviate_connect(settings) as client:
        index = WeaviateIndex(client, settings.match)
        if args.rebuild:
            index.clear(settings.recon_tenant)
            print("cleared existing index")
        count = index.index_open_items(settings.recon_tenant, entries)
    print(f"indexed {count} open ledger entries for tenant {settings.recon_tenant}")
    return 0


def _cmd_eval(args: argparse.Namespace) -> int:
    from recon.evals.runner import check_regression, print_report, run_eval, write_report

    settings = get_settings()
    evals_dir = Path(args.evals_dir or settings.recon_evals_dir)

    payload = run_eval(settings)
    print_report(payload)
    path = write_report(payload, evals_dir)
    print(f"report: {path}")

    failures = check_regression(payload, evals_dir)
    if args.set_baseline:
        if failures:
            print("\nrefusing to set a baseline from a failing run:")
            for f in failures:
                print(f"  - {f}")
            return 1
        baseline = evals_dir / "baseline.json"
        baseline.write_text(path.read_text())
        print(f"baseline: {baseline}")
        return 0

    if failures:
        print("REGRESSION:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return 0


def _cmd_stats(_: argparse.Namespace) -> int:
    from recon.app import stats

    for key, value in stats().items():
        print(f"{key:22} {value}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recon", description="Agentic bank reconciliation")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="apply pending migrations").set_defaults(fn=_cmd_migrate)

    gen = sub.add_parser("generate", help="write the seeded dataset")
    gen.add_argument("--out", help="output directory (default: RECON_DATA_DIR)")
    gen.set_defaults(fn=_cmd_generate)

    ing = sub.add_parser("ingest", help="load a generated dataset into Postgres")
    ing.add_argument("--dir", help="dataset directory (default: RECON_DATA_DIR)")
    ing.set_defaults(fn=_cmd_ingest)

    seed = sub.add_parser("seed", help="migrate + generate + ingest")
    seed.add_argument("--out", help="output directory (default: RECON_DATA_DIR)")
    seed.add_argument("--dir", help=argparse.SUPPRESS)
    seed.set_defaults(fn=_cmd_seed)

    rn = sub.add_parser("run", help="execute a reconciliation run through the graph")
    rn.add_argument("--period", default="2026-06")
    rn.add_argument(
        "--adjudicator", default="auto", choices=("auto", "anthropic", "stub", "recorded")
    )
    rn.add_argument("--replay-of", help="run id whose recorded model calls to replay")
    rn.add_argument("--no-retrieval", action="store_true")
    rn.set_defaults(fn=_cmd_run)

    rs = sub.add_parser("resume", help="resume a paused run with reviewer decisions")
    rs.add_argument("run_id")
    rs.add_argument("--resolutions", help="JSON file of reviewer decisions")
    rs.add_argument("--simulate-reviewer", action="store_true")
    rs.set_defaults(fn=_cmd_resume)

    idx = sub.add_parser("index", help="index open ledger entries into Weaviate")
    idx.add_argument("--rebuild", action="store_true", help="clear the tenant index first")
    idx.set_defaults(fn=_cmd_index)

    ev = sub.add_parser("eval", help="score the golden set and write a report")
    ev.add_argument("--evals-dir", help="report directory (default: RECON_EVALS_DIR)")
    ev.add_argument(
        "--set-baseline",
        action="store_true",
        help="also write evals/baseline.json, which future runs are checked against",
    )
    ev.set_defaults(fn=_cmd_eval)

    sub.add_parser("stats", help="row counts for the active tenant").set_defaults(fn=_cmd_stats)

    args = parser.parse_args(argv)
    if getattr(args, "dir", None) is None:
        args.dir = getattr(args, "out", None)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
