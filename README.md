# Agentic bank reconciliation

Matches bank statement lines against ledger entries, auto-commits only what it
can defend in writing, and routes everything else to a human exception queue
with a reasoned recommendation. Human corrections feed back into retrieval, so
it improves per client entity.

Built on LangGraph with durable checkpointing, an append-only hash-chained
audit log, and a 300-case eval harness.

## It does not touch your ledger

The system **proposes** matches. It writes only to its own tables. No journal
is posted, no invoice is closed, no ledger record is created, updated or
deleted by this software. Reconciliation output is a set of proposed matches
with a rationale and an audit trail; applying them to the accounting system
remains a separate, human-authorised step.

## Design commitments

- **Rules first, model last.** Deterministic tiers clear the bulk with zero AI
  cost. The model adjudicates only genuine ambiguity — roughly 240 calls per
  1,200 lines, not 1,200.
- **False positives are the cardinal sin.** Auto-committing a wrong match is
  far worse than escalating a correct one; thresholds are tuned accordingly and
  the eval report states the false-positive count explicitly.
- **Every auto-match is explainable in one sentence** a bookkeeper understands.
- **Exact replay.** Every model call is recorded and keyed by a hash of its
  request. Replaying a run re-executes the deterministic tiers for real and
  serves the model's part from the recording — so replay is provably exact
  rather than dependent on a model behaving identically twice.
- **Append-only audit.** Decisions and events are never updated or deleted,
  only superseded. The event log is hash-chained and the database rejects
  mutation.
- **Idempotent ingest.** Re-ingesting a file is a no-op, whatever it is named.
- **Money is `Decimal`, stored as integer minor units.** Never float.

## Quickstart

```bash
make up      # postgres, weaviate, api, web; applies migrations
make seed    # generate the seeded dataset and ingest it
make check   # lint, typecheck, tests
```

API on http://localhost:8000, web on http://localhost:5173.

`make down` stops the stack; `make reset` also destroys the data.

## Status

Phase 1 of 6. Skeleton, schema and seeded dataset are in place; there is no
matching engine yet.

| Phase | Scope | State |
|---|---|---|
| 0 | Plan, contract, resolved parameters | done |
| 1 | Stack, migrations, seeded dataset | done |
| 2 | Tiers 0–1, golden set, `make eval` | next |
| 3 | Weaviate, Tier 2 candidate generation | |
| 4 | LangGraph, Tier 3 adjudication, audit chain | |
| 5 | Interrupts, exception queue UI, write-back | |
| 6 | Replay, CAMT.053, demo polish | |

`CLAUDE.md` holds the working contract. `NOTES.md` is the decision log.
