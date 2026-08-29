# Claude Code Kickoff Prompt — Agentic Reconciliation MVP

---

## 0. Fill these in before pasting

```
CLIENT_VERTICAL:      <<< e.g. property management / logistics / SaaS billing >>>
CURRENCY:             <<< e.g. EUR / USD / KES / ZAR >>>
LEDGER_SOURCE:        <<< e.g. Xero export / ERPNext GL / QuickBooks CSV >>>
STATEMENT_FORMAT:     <<< e.g. CSV, CAMT.053 XML, MT940 >>>
DEMO_DATE:            <<< date of the client walkthrough >>>
MONTHLY_TXN_VOLUME:   <<< e.g. ~4,000 bank lines / month >>>
```

---

# MISSION

Build a demo-ready MVP of an **agentic bank reconciliation system**: it matches bank statement lines against ledger entries, auto-commits only what it can defend, and routes everything else to a human exception queue with a reasoned recommendation.

This is a **capability demonstration for a prospective client**, not a production deployment. It must be *architecturally honest* — the demo has to survive a technical buyer asking "what happens when it's wrong?" That question is the whole point of the build.

## What "done" means

At the end, I run one command, the system comes up, and I can walk a client through this in 10 minutes:

1. Ingest a month of statement lines + ledger entries.
2. Watch the graph process them live, tier by tier.
3. See ~70–85% auto-matched with a confidence score and a written rationale per match.
4. See the residue in an exception queue, each with the agent's top-3 candidates and its reasoning.
5. Approve, reject, or re-assign an exception in the UI. The graph resumes from its checkpoint and commits.
6. Show the audit trail for any single transaction: every node that touched it, every model call, every cost.
7. Re-run the exact same batch with `make replay RUN_ID=...` and get **byte-identical** auto-match decisions.
8. Show the eval report: precision, recall, false-positive rate, cost per 1,000 lines, p95 latency.
9. Show that a human correction on Monday changes the agent's behaviour on Tuesday's batch (the feedback loop).

Point 7 and point 9 are the ones that close the deal. Do not deprioritise them.

---

# NON-NEGOTIABLES

These are architectural commitments. If you think one is wrong, stop and argue with me before writing code — do not silently work around it.

1. **The LLM is the last resort, not the first.** Deterministic rules handle the bulk. The model only adjudicates genuine ambiguity. If the demo shows an LLM call per transaction, the architecture has failed.
2. **False positives are the cardinal sin.** Auto-committing a wrong match is far worse than escalating a correct one. Tune thresholds accordingly and say so in the eval report.
3. **Every auto-match must be explainable in one sentence a bookkeeper understands.** No bare confidence scores.
4. **Determinism.** Temperature 0, pinned model version, seeded shuffles, no wall-clock or randomness inside decision logic. Replay of a stored run must reproduce identical decisions or exit non-zero with a diff.
5. **Append-only audit.** Decisions are never updated or deleted, only superseded by a new event. Hash-chain the event log (`prev_hash` on each row).
6. **Idempotency.** Re-ingesting the same file must be a no-op. Content-hash every source row.
7. **No mutation of the ledger.** The system proposes journal matches; it writes to its own tables only. Say this out loud in the README — finance buyers care.
8. **Money is `Decimal`, never `float`.** Store as integer minor units. Any float in a monetary path is a bug.

---

# STACK

Do not substitute without asking.

| Layer | Choice | Reason |
|---|---|---|
| Orchestration | **LangGraph** (Python) | Interrupts + durable checkpointing are the demo. Python because `langgraph-checkpoint-postgres` and the interrupt/resume story are more mature than the JS port. |
| LLM | Anthropic API, `claude-sonnet-4-6` for adjudication, Haiku for cheap classification | Cost tiering is part of the story |
| Vector store | **Weaviate** (docker) | Hybrid BM25+vector over transaction narratives, and native multi-tenancy so one deployment serves many client entities |
| State / audit | **Postgres 16** | Checkpointer + append-only event log in one place |
| API | FastAPI + SSE for live graph progress | |
| Front end | Vite + React 19 + TypeScript + Tailwind | Exception queue, run viewer, audit drill-down |
| Runtime | docker-compose, one `make demo` | It must come up on a laptop with no network config |

**LangChain is allowed only for loaders/splitters/integrations.** Do not build application logic on LangChain chains or agents.

Before using any LangGraph or Weaviate API, check the installed version's actual signatures (`pip show`, read the package source or docs). Do not write from memory — these libraries move fast and a hallucinated API costs an hour.

---

# THE MATCHING CASCADE

This is the core spec. Five tiers, each only sees what the previous tier could not resolve.

**Tier 0 — Deterministic exact.**
Amount equal, date within 0–2 days, reference/invoice number present in both. Auto-commit at confidence 1.0. Expect this to clear 40–60%. Zero LLM calls.

**Tier 1 — Deterministic structural.**
Amount equal, date within a 7-day window, unique counterparty match. Also handles the *known-pattern* cases: standing orders, recurring fees, FX-rounded amounts within tolerance. Auto-commit at ≥0.95.

**Tier 2 — Candidate generation.**
For each unmatched line, produce ≤10 candidates via:
- amount window (± tolerance, and split/partial-payment detection: does this line equal the sum of 2–3 open items?)
- date window
- Weaviate hybrid search on the narrative against (a) open ledger item descriptions and (b) **historically resolved pairs from this tenant**.

No LLM yet. This tier is retrieval only. Log recall@10 — if it's below 95% on the golden set, everything downstream is capped.

**Tier 3 — LLM adjudication.**
One call per unresolved line, with its candidates. Structured output only:
```json
{
  "decision": "match" | "no_match" | "split_match" | "insufficient_evidence",
  "candidate_ids": ["..."],
  "confidence": 0.0,
  "rationale": "one sentence, bookkeeper-readable",
  "evidence": ["narrative token or field that drove the decision"]
}
```
Confidence ≥ 0.90 → auto-commit. Below → Tier 4. `insufficient_evidence` is a first-class, encouraged answer; make that explicit in the prompt.

**Tier 4 — Human exception queue.**
`interrupt()` on the graph. The item sits in the queue with the candidates, the rationale, and the evidence. On resume, the human's decision is committed *and* written back to Weaviate as a resolved pair, so it improves Tier 2 recall on the next run. That write-back is the demo's closing move.

---

# GRAPH SHAPE

```
ingest → normalise → dedupe(content_hash)
  → tier0_exact ─┬→ commit
  → tier1_struct ┬→ commit
  → tier2_candidates
  → tier3_adjudicate ─┬→ commit
                      └→ tier4_interrupt → (human) → commit
  → close_run → emit_report
```

Batch through the tiers, do not run one graph invocation per transaction. Fan out Tier 3 with bounded concurrency (8) and a per-run cost ceiling that halts the graph if exceeded.

Checkpoint with `PostgresSaver`. Thread ID = `run_id`. Every node writes an event to the audit log before returning.

---

# DATA MODEL (minimum)

- `sources` — uploaded file, sha256, row count, ingested_at
- `bank_lines` — id, source_id, value_date, amount_minor, currency, narrative, counterparty, raw jsonb, content_hash (unique)
- `ledger_entries` — same shape, plus open/closed status
- `runs` — id, started_at, config_snapshot jsonb, model_version, prompt_version, git_sha, cost_total, status
- `decisions` — run_id, bank_line_id, ledger_entry_ids[], tier, decision, confidence, rationale, evidence jsonb, committed_at
- `events` — append-only: run_id, node, payload jsonb, prev_hash, hash, created_at
- `human_reviews` — decision_id, reviewer, action, corrected_ledger_entry_ids[], note, reviewed_at

`config_snapshot` + `model_version` + `prompt_version` + `git_sha` on the run row is what makes replay meaningful. Do not skip it.

---

# EVAL HARNESS

Build this in Phase 2, not at the end. It is a deliverable, not a nice-to-have.

- **Golden set:** 300 labelled bank lines. 180 clean, 120 deliberately hard: partial payments, batched settlements (one credit = 6 invoices), FX differences, duplicate amounts on the same day to different counterparties, transposed digits in the reference, bank fees netted off, a genuine unmatched line that *should* have no match.
- **Metrics:** auto-match precision (target ≥ 0.995), auto-match recall, **false-positive count (target: 0)**, escalation rate, Tier 2 recall@10, cost per 1,000 lines, p50/p95 latency.
- `make eval` prints a table and writes `evals/report-<git_sha>.json`.
- CI-style regression: fail the run if precision drops below the last committed baseline.
- Ablation table for the demo deck: rules-only vs rules+retrieval vs full cascade. This one slide is worth more than the UI.

---

# PHASE PLAN

Work phase by phase. **Stop at the end of each phase**, show me what you built and the test output, and wait for me to say continue. Commit at each boundary with a real message.

**Phase 0 — Plan (no code).**
Read this brief back to me as: repo structure, data model DDL, graph node list, open questions, and anything above you think is wrong. Write `CLAUDE.md` with the non-negotiables, the cascade spec, and the commands. Wait for approval.

**Phase 1 — Skeleton + data.**
docker-compose (postgres, weaviate, api, web). Migrations. Synthetic data generator producing 1,200 bank lines + matching ledger for one month in `CURRENCY`, seeded, reproducible, with the hard cases from the eval spec planted in known positions. `make up`, `make seed`. No agent yet.

**Phase 2 — Tiers 0–1 + eval harness.**
Deterministic matchers, golden set, `make eval`. Establish the baseline: what fraction does pure rules solve? That number goes in the client deck.

**Phase 3 — Weaviate + Tier 2.**
Collections with multi-tenancy on tenant/entity, named vectors, hybrid search. Ingest resolved historical pairs. Report recall@10.

**Phase 4 — LangGraph + Tier 3.**
The graph, PostgresSaver checkpointing, structured adjudication, cost ceiling, event log with hash chain. `make run FILE=...`.

**Phase 5 — Interrupts + exception queue UI.**
`interrupt()` at Tier 4, resume via `Command(resume=...)`, React queue with candidate cards, approve/reject/reassign, SSE live progress, audit drill-down view. Write-back of human decisions to Weaviate.

**Phase 6 — Replay + demo polish.**
`make replay RUN_ID=...` with a strict diff. Kill the API mid-run and show it resumes from checkpoint. Seeded demo scenario, `DEMO.md` with the exact click path and the lines I say at each step.

---

# DEMO SCRIPT (write this as `DEMO.md` in Phase 6)

Ten minutes, in this order:

1. *"Here's 1,200 bank lines and a ledger."* — `make demo`
2. *"Rules clear 58% with zero AI cost."* — show the tier breakdown live
3. *"Retrieval finds candidates for the rest, using this client's own history."* — show a Weaviate hit on a messy narrative
4. *"The model only adjudicates what's genuinely ambiguous — 140 calls, not 1,200."* — show cost
5. *"Here's one it got right and why."* — read the rationale aloud
6. *"Here's one it refused to guess on."* — the `insufficient_evidence` case. **Dwell here.** Restraint is the product.
7. Approve an exception in the UI. Graph resumes. Committed.
8. *"That correction is now training data."* — re-run, show the same pattern auto-matching
9. `make replay` — identical decisions
10. Eval table + ablation slide

---

# ANTI-GOALS

Do not build: user auth, multi-user roles, real bank API integrations, a chat interface, a settings page, dark mode, ledger write-back, PDF statement OCR, or anything at all for mobile. If you find yourself building any of these, you have drifted.

Do not use floats for money. Do not call the LLM in Tiers 0–2. Do not `pip install` anything not required by a phase you are currently in. Do not write more than 400 lines without running the tests.

---

# WORKING AGREEMENT

- Ask before adding a dependency, changing the schema after Phase 1, or deviating from the cascade spec.
- Tests alongside code, not after. Matcher logic needs unit tests with the hard cases.
- Type hints everywhere, `ruff` + `mypy` clean.
- If something in this brief turns out to be impractical once you're in the code, say so directly rather than building a workaround and telling me later.
- Keep a running `NOTES.md` of decisions and their reasons. It becomes the client's architecture doc.

Start with Phase 0. Plan only, no code.
