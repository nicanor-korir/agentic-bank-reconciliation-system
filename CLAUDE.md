# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

The repo contains no code yet — only `PROMPT.md` (the build brief), `README.md`, and `LICENSE`. Work is driven by the phase plan in `PROMPT.md`; **read it before starting anything**. Everything below is the standing contract distilled from that brief. When brief and CLAUDE.md disagree, `PROMPT.md` wins and this file should be updated.

### Approved parameters (supersede the placeholders in `PROMPT.md`)

- **Currency:** USD — `amount_minor` is cents.
- **Client vertical:** property management — rent, service charges, deposits, maintenance suppliers, agent commission.
- **Statement format:** CSV for Phases 1–5; a CAMT.053 XML parser lands in Phase 6. Keep `ingest/parsers/` behind a protocol so the second parser needs no changes elsewhere.
- **Ledger source:** assumed generic Xero-shaped AR/AP CSV export (unconfirmed — see `NOTES.md` §0.7).
- **Dataset:** two periods. `2026-06` is the 1,200-line demo month; `2026-07` is a ~200-line follow-up that exists so the feedback loop (demo point 9) can be shown. Do not drop the second period.
- **Golden set:** a frozen labelled subset of the generated month — all 120 hard cases plus 180 deterministically sampled clean ones, recorded in the generator manifest.

## What this is

An agentic bank reconciliation system: it matches bank statement lines against ledger entries, auto-commits only what it can defend in writing, and routes the rest to a human exception queue with reasoned candidates. Human corrections feed back into retrieval so accuracy improves per client entity. It is a **capability demo for a prospective client**, not a production deployment — but it must be architecturally honest enough to survive "what happens when it's wrong?"

Two features close the deal and must not be deprioritised: **byte-identical replay** of a stored run, and the **human-correction feedback loop** (a Monday correction changes Tuesday's behaviour).

## Non-negotiables

These are architectural commitments. If one seems wrong, argue it before writing code — do not silently work around it.

1. **The LLM is the last resort.** Deterministic rules handle the bulk; the model only adjudicates genuine ambiguity. An LLM call per transaction means the architecture has failed. No LLM calls in Tiers 0–2.
2. **False positives are the cardinal sin.** Auto-committing a wrong match is far worse than escalating a correct one. Tune thresholds accordingly and say so in the eval report.
3. **Every auto-match is explainable in one sentence a bookkeeper understands.** No bare confidence scores.
4. **Determinism, via record-and-replay.** Pinned model version, seeded shuffles, no wall-clock or randomness inside decision logic — date windows are relative to `value_date`, never `today()`. Every Tier 3 call is persisted to `llm_calls` keyed by `sha256(canonical request body)`. `make replay` re-executes Tiers 0–2 for real and serves Tier 3 from the recorded store; a `request_hash` miss is a failure reported as a diff. Do **not** try to get determinism from `temperature: 0` — current models reject the parameter outright, and it never guaranteed identical output anyway (`NOTES.md` §0.4a).
5. **Append-only audit.** Decisions are never updated or deleted, only superseded by a new event. Hash-chain the event log (`prev_hash` on each row).
6. **Idempotency.** Re-ingesting the same file is a no-op. Content-hash every source row.
7. **No mutation of the ledger.** The system proposes journal matches and writes only to its own tables. State this in the README — finance buyers care.
8. **Money is `Decimal`, never `float`.** Store as integer minor units (`amount_minor`). Any float in a monetary path is a bug.

## The matching cascade

Five tiers; each sees only what the previous tier could not resolve.

- **Tier 0 — deterministic exact.** Amount equal, date within 0–2 days, reference/invoice number present in both. Auto-commit at confidence 1.0. Expect 40–60% cleared. Zero LLM calls.
- **Tier 1 — deterministic structural.** Amount equal, date within 7 days, unique counterparty match. Also known patterns: standing orders, recurring fees, FX-rounded amounts within tolerance. Auto-commit at ≥0.95.
- **Tier 2 — candidate generation (retrieval only, no LLM).** ≤10 candidates per unmatched line from: amount window (± tolerance, including split/partial-payment detection — does this line equal the sum of 2–3 open items?), date window, and Weaviate hybrid search on the narrative against open ledger item descriptions **and historically resolved pairs from this tenant**. Log recall@10; below 95% on the golden set caps everything downstream.
- **Tier 3 — LLM adjudication.** One call per unresolved line with its candidates, structured output only: `{decision: "match"|"no_match"|"split_match"|"insufficient_evidence", candidate_ids, confidence, rationale (one bookkeeper-readable sentence), evidence[]}`. Confidence ≥0.90 auto-commits; below goes to Tier 4. `insufficient_evidence` is a first-class, encouraged answer — make that explicit in the prompt.
- **Tier 4 — human exception queue.** `interrupt()` on the graph. On resume the human's decision is committed *and* written back to Weaviate as a resolved pair, improving Tier 2 recall next run.

## Graph shape

```
ingest → normalise → dedupe(content_hash)
  → tier0_exact ─┬→ commit
  → tier1_struct ┬→ commit
  → tier2_candidates
  → tier3_adjudicate ─┬→ commit
                      └→ tier4_interrupt → (human) → commit
  → close_run → emit_report
```

Batch through the tiers — do **not** run one graph invocation per transaction. Fan out Tier 3 with bounded concurrency (8) and a per-run cost ceiling that halts the graph when exceeded. Checkpoint with `PostgresSaver`, thread ID = `run_id`. Every node writes an audit event before returning.

## Data model (minimum)

`sources` (file, sha256, row count) · `bank_lines` (value_date, amount_minor, currency, narrative, counterparty, raw jsonb, unique content_hash) · `ledger_entries` (same shape + open/closed) · `runs` (config_snapshot jsonb, model_version, prompt_version, git_sha, cost_total, status) · `decisions` (run_id, bank_line_id, ledger_entry_ids[], tier, decision, confidence, rationale, evidence jsonb) · `events` (append-only: run_id, node, payload, prev_hash, hash) · `human_reviews` (decision_id, reviewer, action, corrected_ledger_entry_ids[], note).

`config_snapshot` + `model_version` + `prompt_version` + `git_sha` on the run row is what makes replay meaningful — do not skip it.

## Stack (do not substitute without asking)

| Layer | Choice |
|---|---|
| Orchestration | LangGraph (Python) — interrupts + durable checkpointing are the demo |
| LLM | Anthropic API, `claude-sonnet-5` for adjudication — the only call site in the system. (The brief's `claude-sonnet-4-6` is real but older and pricier; Haiku is dropped because the cascade has no classification step and adding one would violate "no LLM in Tiers 0–2".) |
| Vector store | Weaviate (docker) — hybrid BM25+vector, native multi-tenancy per client entity |
| State / audit | Postgres 16 — checkpointer + append-only event log in one place |
| API | FastAPI + SSE for live graph progress |
| Front end | Vite + React 19 + TypeScript + Tailwind |
| Runtime | docker-compose, one `make demo`, no network config needed |

LangChain is allowed **only** for loaders/splitters/integrations — never for application logic (no LangChain chains or agents).

Before using any LangGraph or Weaviate API, check the installed version's actual signatures (`pip show`, read the package source or docs). Do not write from memory; these libraries move fast.

## Commands

Phase 1 targets are live. Later ones are declared in the Makefile and fail with a one-line message until their phase lands. Keep this list accurate as phases land.

| Command | Purpose |
|---|---|
| `make up` | Bring up postgres, weaviate, api, web and apply migrations |
| `make seed` | Generate the seeded dataset (1,200 + 200 lines over two periods, hard cases planted) and ingest it |
| `make index` | Rebuild the Weaviate index from Postgres |
| `make check` | lint + typecheck + tests |
| `make down` / `make reset` | Stop the stack / stop and destroy data |
| `make eval` | Score the golden set, print the ablation + retrieval tables, write `evals/report-<git_sha>.json` |
| `make eval-baseline` | Score and record the result as the regression baseline |
| `make run [PERIOD=2026-06]` | Execute a reconciliation run through the graph |
| `make resume RUN_ID=... [SIMULATE=1]` | Resume a paused run with reviewer decisions |
| `make replay RUN_ID=...` | Re-run a stored run; strict diff, non-zero exit on any divergence (Phase 6) |
| `make demo` | Full seeded demo scenario, one command (Phase 6) |

## Eval harness

Built in Phase 2, not at the end — it is a deliverable.

- **Golden set:** 300 labelled bank lines — 180 clean, 120 deliberately hard (partial payments, batched settlements where one credit covers 6 invoices, FX differences, duplicate amounts same day to different counterparties, transposed reference digits, bank fees netted off, and a genuine unmatched line that *should* have no match).
- **Metrics:** auto-match precision (target ≥0.995), recall, **false-positive count (target 0)**, escalation rate, Tier 2 recall@10, cost per 1,000 lines, p50/p95 latency.
- Regression gate: fail the run if precision drops below the last committed baseline.
- Ablation table (rules-only vs rules+retrieval vs full cascade) for the demo deck — that slide is worth more than the UI.

## Working agreement

- **Stop at the end of each phase.** Show what was built plus test output and wait for approval before continuing. Commit at each phase boundary with a real message.
- Ask before adding a dependency, changing the schema after Phase 1, or deviating from the cascade spec.
- Tests alongside code, not after. Matcher logic needs unit tests covering the hard cases.
- Type hints everywhere; `ruff` and `mypy` clean.
- Do not write more than 400 lines without running the tests.
- Do not `pip install` anything not required by the phase currently in progress.
- If something in the brief turns out to be impractical once in the code, say so directly rather than building a workaround and mentioning it later.
- Keep a running `NOTES.md` of decisions and their reasons — it becomes the client's architecture doc.

## Anti-goals

Do not build: user auth, multi-user roles, real bank API integrations, a chat interface, a settings page, dark mode, ledger write-back, PDF statement OCR, or anything for mobile. Building any of these means drift.
