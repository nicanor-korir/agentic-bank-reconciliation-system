"""`recon` command line.

Phase 1 covers migrate / generate / ingest / seed / stats. Later phases add
run, eval and replay -- declared in the Makefile so an early call fails with a
sentence rather than a stack trace.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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
