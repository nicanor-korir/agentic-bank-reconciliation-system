# NOTES.md — decisions and their reasons

Running log of architectural decisions. Becomes the client's architecture doc.

---

# Phase 0 — Plan readback (no code)

Status: **awaiting approval**. Nothing has been installed or generated. `CLAUDE.md` is written.

---

## 0.1 Repo structure

```
.
├── CLAUDE.md                  # standing contract (written)
├── NOTES.md                   # this file
├── PROMPT.md                  # the brief
├── DEMO.md                    # Phase 6
├── Makefile                   # up / down / seed / run / eval / replay / demo / test / lint
├── docker-compose.yml         # postgres, weaviate, api, web
├── .env.example               # ANTHROPIC_API_KEY, DB/Weaviate URLs, RECON_TENANT
│
├── api/
│   ├── pyproject.toml         # uv-managed; ruff + mypy config
│   ├── migrations/            # plain .sql, applied in order by a tiny runner
│   │   └── 001_init.sql
│   └── src/recon/
│       ├── config.py          # frozen settings; feeds runs.config_snapshot
│       ├── money.py           # Decimal <-> minor units; the ONLY money conversion site
│       ├── hashing.py         # canonical_json, content_hash, chain_hash
│       ├── db/                # engine, repositories, event log writer
│       ├── ingest/
│       │   ├── normalise.py   # raw row -> canonical BankLine / LedgerEntry
│       │   └── parsers/       # csv.py (+ camt053.py / mt940.py only if needed)
│       ├── matching/
│       │   ├── tier0_exact.py
│       │   ├── tier1_struct.py
│       │   ├── tier2_candidates.py   # amount/date windows + subset-sum + Weaviate
│       │   └── subset_sum.py         # bounded split/batch detection
│       ├── retrieval/         # Weaviate client, schema, tenant mgmt, write-back
│       ├── llm/
│       │   ├── client.py      # Anthropic client + cost accounting + cost ceiling
│       │   ├── prompts/adjudicate.v1.md   # versioned; prompt_version = file hash
│       │   ├── schema.py      # the adjudication output schema
│       │   └── cache.py       # request-hash -> response store (replay; see 0.5)
│       ├── graph/
│       │   ├── state.py       # RunState TypedDict
│       │   ├── nodes.py       # one function per node
│       │   ├── build.py       # StateGraph wiring + PostgresSaver
│       │   └── audit.py       # node decorator: write event before return
│       ├── evals/
│       │   ├── golden/        # 300 labelled cases (jsonl + labels)
│       │   ├── runner.py      # make eval
│       │   ├── metrics.py
│       │   └── ablation.py    # rules-only | +retrieval | full cascade
│       ├── replay.py          # make replay: strict diff, non-zero exit
│       ├── seed/generator.py  # seeded synthetic month; plants the hard cases
│       └── app.py             # FastAPI: runs, exceptions, resume, SSE, audit
│
├── web/                       # Vite + React 19 + TS + Tailwind
│   └── src/
│       ├── routes/            # RunView (live SSE), ExceptionQueue, AuditDrilldown
│       └── components/        # CandidateCard, TierBreakdown, EventTimeline
│
├── evals/                     # report-<git_sha>.json outputs (committed baselines)
└── tests/                     # pytest; matching/ has the hard cases as unit tests
```

Rationale for the parts that aren't obvious:

- `money.py` as the single conversion site makes non-negotiable #8 greppable: any `float` outside it is a bug, and a test asserts no `float` appears in matching/ or db/.
- `hashing.py` shared by content hashing (idempotency), the event chain, and the LLM request cache (replay). One canonical-JSON implementation, three uses — if it drifts, all three break together and loudly.
- `prompts/` as versioned files, not inline strings, so `prompt_version` is a content hash and replay can detect prompt drift.
- Migrations as plain `.sql`, not Alembic — one schema, one phase boundary at which it may change. Alembic is machinery we would never exercise.

---

## 0.2 Data model DDL

```sql
-- 001_init.sql
create extension if not exists pgcrypto;

-- Tenancy is per client entity; mirrors the Weaviate tenant name 1:1.
create table tenants (
  id             text primary key,
  name           text not null,
  base_currency  char(3) not null
);

create table sources (
  id           bigserial primary key,
  tenant_id    text not null references tenants(id),
  kind         text not null check (kind in ('bank_statement','ledger_export')),
  filename     text not null,
  sha256       char(64) not null,
  row_count    integer not null,
  ingested_at  timestamptz not null default now(),
  unique (tenant_id, sha256)        -- NON-NEGOTIABLE #6: re-ingest is a no-op
);

create table bank_lines (
  id            bigserial primary key,
  tenant_id     text not null references tenants(id),
  source_id     bigint not null references sources(id),
  value_date    date not null,
  booking_date  date,
  amount_minor  bigint not null,    -- signed, minor units. NEVER float.
  currency      char(3) not null,
  narrative     text not null,
  counterparty  text,
  bank_ref      text,
  raw           jsonb not null,
  content_hash  char(64) not null,
  unique (tenant_id, content_hash)
);
create index on bank_lines (tenant_id, value_date);
create index on bank_lines (tenant_id, amount_minor);

create table ledger_entries (
  id                bigserial primary key,
  tenant_id         text not null references tenants(id),
  source_id         bigint not null references sources(id),
  entry_date        date not null,
  due_date          date,
  amount_minor      bigint not null,
  open_amount_minor bigint not null,   -- supports partial payments
  currency          char(3) not null,
  description       text not null,
  counterparty      text,
  doc_ref           text,              -- invoice / credit-note number
  status            text not null check (status in ('open','closed')),
  raw               jsonb not null,
  content_hash      char(64) not null,
  unique (tenant_id, content_hash)
);
create index on ledger_entries (tenant_id, status, amount_minor);
create index on ledger_entries (tenant_id, doc_ref);

create table runs (
  id               uuid primary key,        -- = LangGraph thread_id
  tenant_id        text not null references tenants(id),
  started_at       timestamptz not null default now(),
  ended_at         timestamptz,
  status           text not null check (status in
                     ('running','awaiting_human','completed','halted_cost','failed')),
  config_snapshot  jsonb not null,          -- every threshold + window, frozen
  model_version    text not null,           -- exact model id used
  prompt_version   text not null,           -- sha256 of the prompt file
  git_sha          text not null,
  git_dirty        boolean not null,        -- honesty: a dirty tree is not replayable
  seed             bigint not null,
  cost_total_micro bigint not null default 0,  -- integer micro-USD, never float
  replay_of        uuid references runs(id)
);

-- Append-only. A correction inserts a NEW row pointing at the one it supersedes.
create table decisions (
  id                bigserial primary key,
  run_id            uuid not null references runs(id),
  tenant_id         text not null references tenants(id),
  bank_line_id      bigint not null references bank_lines(id),
  ledger_entry_ids  bigint[] not null default '{}',
  tier              smallint not null check (tier between 0 and 4),
  decision          text not null check (decision in
                      ('match','no_match','split_match','insufficient_evidence','escalated')),
  confidence        numeric(4,3) not null check (confidence between 0 and 1),
  rationale         text not null,
  evidence          jsonb not null default '[]',
  auto_committed    boolean not null,
  supersedes_id     bigint references decisions(id),
  created_at        timestamptz not null default now()
);
create index on decisions (run_id, tier);
create index on decisions (tenant_id, bank_line_id);
create unique index on decisions (supersedes_id) where supersedes_id is not null;

create rule decisions_no_update as on update to decisions do instead nothing;
create rule decisions_no_delete as on delete to decisions do instead nothing;

-- Hash-chained audit. One chain per run.
create table events (
  id          bigserial primary key,
  run_id      uuid not null references runs(id),
  seq         integer not null,
  node        text not null,
  payload     jsonb not null,
  prev_hash   char(64) not null,   -- 64 zeros for seq = 0
  hash        char(64) not null,   -- sha256(prev_hash || canonical_json(payload))
  created_at  timestamptz not null default now(),
  unique (run_id, seq),
  unique (run_id, hash)
);
create rule events_no_update as on update to events do instead nothing;
create rule events_no_delete as on delete to events do instead nothing;

create table human_reviews (
  id                          bigserial primary key,
  decision_id                 bigint not null references decisions(id),
  reviewer                    text not null,
  action                      text not null check (action in ('approve','reject','reassign')),
  corrected_ledger_entry_ids  bigint[] not null default '{}',
  note                        text,
  reviewed_at                 timestamptz not null default now(),
  written_back_at             timestamptz     -- when it reached Weaviate; drives the demo
);

-- Recorded model I/O. This is what makes replay byte-identical (see 0.5).
create table llm_calls (
  id                bigserial primary key,
  run_id            uuid not null references runs(id),
  bank_line_id      bigint not null references bank_lines(id),
  request_hash      char(64) not null,   -- sha256(canonical_json(full request body))
  request           jsonb not null,
  response          jsonb not null,
  input_tokens      integer not null,
  output_tokens     integer not null,
  cost_micro        bigint not null,
  latency_ms        integer not null,
  created_at        timestamptz not null default now()
);
create index on llm_calls (request_hash);
```

Notes:
- `open_amount_minor` is what makes partial payments representable. Without it, "split_match" has nowhere to land.
- `git_dirty` is deliberate. Claiming replayability from an uncommitted tree is the kind of thing a technical buyer will catch.
- Cost is `bigint` micro-USD for the same reason money is minor units.
- The `rule ... do instead nothing` pair enforces append-only at the database, not by convention. It silently drops writes rather than erroring; if you prefer a hard failure, a `BEFORE` trigger raising an exception is the alternative — say which you want.

---

## 0.3 Graph node list

State (`RunState`): `run_id`, `tenant_id`, `config`, `unmatched: list[int]` (bank_line ids), `decisions: list[Decision]`, `candidates: dict[int, list[Candidate]]`, `cost_micro: int`, `queue: list[int]`.

| # | Node | Does | LLM | Writes event |
|---|---|---|---|---|
| 1 | `ingest` | Parse files, insert `sources` + rows; short-circuits on known sha256 | no | yes |
| 2 | `normalise` | Raw → canonical: amounts to minor units, dates to `date`, narrative case/whitespace/diacritic folding, counterparty extraction | no | yes |
| 3 | `dedupe` | Drop rows whose `content_hash` already exists for the tenant | no | yes |
| 4 | `tier0_exact` | Amount equal ∧ date ±0–2d ∧ shared reference → commit @ 1.0 | no | yes |
| 5 | `tier1_struct` | Amount equal ∧ date ±7d ∧ unique counterparty; plus standing orders, recurring fees, FX tolerance → commit @ ≥0.95 | no | yes |
| 6 | `tier2_candidates` | ≤10 candidates: amount window, date window, bounded subset-sum, Weaviate hybrid over open items **and** this tenant's resolved pairs | no | yes |
| 7 | `tier3_adjudicate` | Fan-out ≤8 concurrent; one structured call per line; ≥0.90 → commit | **yes** | one per line |
| 8 | `cost_gate` | Checked before every batch in node 7; over ceiling → `halted_cost` | no | yes |
| 9 | `tier4_interrupt` | `interrupt()` with candidates + rationale + evidence | no | yes |
| 10 | `apply_human` | On resume: append decision, write `human_reviews`, **write back to Weaviate** | no | yes |
| 11 | `close_run` | Set status/ended_at, verify the hash chain end-to-end | no | yes |
| 12 | `emit_report` | Tier breakdown, cost, latency, escalation rate → run report JSON | no | yes |

Edges follow the brief exactly. Batched, not per-transaction: nodes 4–6 operate on the whole unmatched set; only node 7 fans out. `audit.py` wraps every node so the event write cannot be forgotten.

---

## 0.4 Things in the brief I think are wrong

Ordered by how much they cost if we get them wrong.

### (a) "Temperature 0" is not available on current models, and would not buy determinism anyway

Non-negotiable #4 says temperature 0 + pinned model ⇒ replay reproduces identical decisions. Two problems:

1. **`temperature` has been removed** from Claude Sonnet 5 / Opus 5 / Opus 4.7 / 4.8 / Fable 5 — sending it returns HTTP 400. It is still accepted on Sonnet 4.6 and Opus 4.6 only. So the brief's literal wording is implementable only by pinning to Sonnet 4.6, which is both older and *more expensive* than Sonnet 5 ($3/$15 vs $2/$10 per MTok).
2. **Temperature 0 was never a determinism guarantee** on any hosted LLM. Same request, same model version, different day → possibly different tokens. Building the demo's headline claim on it means the claim breaks live, in front of the buyer, at the worst possible moment.

**Proposed resolution — record, don't re-roll.** Every Tier 3 call is persisted in `llm_calls`, keyed by `request_hash` = sha256 of the canonical request body. `make replay RUN_ID=...`:
- re-executes Tiers 0–2 for real (these are genuinely deterministic and that is where the real replay risk lives — an accidental `set` iteration, a wall-clock date window, an unsorted candidate list);
- for Tier 3, recomputes each request body, hashes it, and looks up the recorded response. A hash miss is a **failure**, reported as a diff — it means the input to the model changed, which is exactly the regression worth catching;
- compares the full decision set byte-for-byte and exits non-zero on any divergence.

This is stronger than the brief's version and it survives the hard question. The claim becomes: *"replay is exact, and we can prove the model wasn't re-rolled to make it exact"* — with `--live` available to re-call the model and show the delta, which is a genuinely interesting second demo beat. If you want the literal temperature-0 wording instead, we pin Sonnet 4.6 and pay more for a weaker guarantee. **My recommendation: record-and-replay.** Needs your call before Phase 4; does not block Phases 1–3.

### (b) `claude-sonnet-4-6` is a real model, but the wrong one now

Sonnet 5 (`claude-sonnet-5`) is newer *and* cheaper ($2/$10 vs $3/$15). Recommend `claude-sonnet-5` for adjudication. Estimated Tier 3 cost at ~140 calls per 1,200 lines, ~1.5K in / ~200 out per call: **~$0.60 per 1,000 lines**, before prompt caching on the system prompt (which should roughly halve the input side). That is the number for the deck.

### (c) Haiku has no job in the cascade as specified

The stack table assigns Haiku to "cheap classification", but the cascade has no classification step — Tiers 0–2 are explicitly LLM-free and Tier 3 is a single adjudication call. Adding a Haiku step would violate "no LLM in Tiers 0–2". Recommend **dropping Haiku from the MVP**: one model, one call site, a cleaner cost story. If you want it, it needs a defined job and a tier number.

### (d) "Tier 2 recall@10 ≥ 95%" is underspecified for split matches

For a batched settlement where one credit clears six invoices, the correct answer is a *set*. "Top-10 candidates" over individual ledger entries can contain all six and still not surface the right *combination*, and subset-sum over an open-item pool is combinatorial. Proposal: Tier 2 emits two candidate kinds — singles (≤10) and *pre-assembled subsets* from a bounded subset-sum (cap: 4 items, ±tolerance, same counterparty, capped search) — and recall@10 is measured as **"the correct answer, whether single or subset, appears in the emitted candidate list"**. That is measurable and honest. Without this the metric is ambiguous and the 120 hard cases can't be scored.

### (e) The feedback-loop demo has a data dependency that must land in Phase 1

Demo point 9 ("Monday's correction changes Tuesday's behaviour") only works if a pattern exists that (i) escalates to Tier 3/4 on the first run and (ii) auto-matches after the write-back. If the synthetic generator doesn't deliberately plant such a pattern — a recurring counterparty with a consistently messy narrative, appearing in both the "Monday" and "Tuesday" batches, and unreachable by Tiers 0–1 — this beat cannot be demonstrated at Phase 5. The generator must plant it in **Phase 1**, four phases before anyone notices it's missing. Flagging now because it is the cheapest possible time to fix.

### (f) Golden set vs demo dataset relationship is unspecified

Phase 1 generates 1,200 lines; Phase 2 needs 300 labelled ones. Recommend the 300 be a **labelled, frozen subset of the same generated month** (same seed, indices recorded), so the eval measures the thing being demoed. Alternative — an independent set — doubles generator work. Your call, but I need it before Phase 1 ends.

### (g) Minor

- **`make demo` on a laptop with no network config** — Anthropic calls need network + a key. Tier 3 will be served from `llm_calls` in the canned demo path, so the offline claim holds; worth being explicit that the live path needs a key.
- **`git_sha` from a dirty tree** — recorded with `git_dirty`, see DDL.
- **"No wall-clock in decision logic"** — date windows must be relative to `value_date`, never `today()`. Enforced by a lint rule banning `date.today()` / `datetime.now()` inside `matching/`.

---

## 0.5 Open questions

**Blocking Phase 1:**
1. The six placeholders at the top of `PROMPT.md`: `CLIENT_VERTICAL`, `CURRENCY`, `LEDGER_SOURCE`, `STATEMENT_FORMAT`, `DEMO_DATE`, `MONTHLY_TXN_VOLUME`. Currency and statement format drive the generator and the parser; vertical drives narrative realism (which is most of what Tier 2 retrieves on).
2. Golden set = subset of the demo month, or independent? (0.4f)

**Blocking Phase 4, not before:**
3. Record-and-replay vs literal temperature-0-on-Sonnet-4.6. (0.4a) — my recommendation is record-and-replay.
4. Drop Haiku? (0.4c)
5. Per-run cost ceiling value — proposing $2.00 for a 1,200-line run, which halts well before a runaway but never trips in the demo.

**Answerable later:**
6. Append-only enforcement: silent `RULE` (as in the DDL) or a raising trigger?
7. Multi-currency in scope, or single-currency with an FX-tolerance rule only? The FX-difference hard case implies at least tolerance handling; full multi-currency matching is materially more work.

---

## 0.6 Verification debts

Per the brief, no LangGraph or Weaviate API is written from memory. Before Phase 3 and Phase 4 respectively, confirm against the installed versions: Weaviate collection config for multi-tenancy + named vectors + hybrid `alpha`; LangGraph `StateGraph` compile signature, `PostgresSaver.setup()`, `interrupt()` payload shape, and `Command(resume=...)`. Nothing is installed yet — Phase 0 is plan-only.

---

## 0.7 Resolved — approved parameters (2026-08-29)

| Brief placeholder | Value | Consequence |
|---|---|---|
| `CURRENCY` | **USD** | 2 minor digits; `amount_minor` is cents. FX hard cases become foreign-billed invoices settled in USD with a rate delta. |
| `CLIENT_VERTICAL` | **Property management** | Narratives are rent, service charges, deposits, maintenance suppliers, agent commission. Recurring tenants give the feedback loop (0.4e) a natural home. |
| `STATEMENT_FORMAT` | **CSV now, CAMT.053 in Phase 6** | `ingest/parsers/` gets a `Parser` protocol from day one so the XML parser drops in later without touching `normalise`. |
| `LEDGER_SOURCE` | *assumed* generic AR/AP CSV export | Not answered. Assuming a Xero-shaped CSV export (invoice no, date, due date, contact, description, total, amount due, status). Trivial to re-map if wrong — say the word. |
| `DEMO_DATE` | *not answered* | Generator uses a fixed synthetic month rather than anything relative to the demo date, so this only affects scheduling, not code. |
| `MONTHLY_TXN_VOLUME` | *not answered* | Using the brief's 1,200 lines/month. |

**Replay: record-and-replay (0.4a approved).** `llm_calls` is in the schema. Tiers 0–2 re-execute for real on replay; Tier 3 is served from the recorded store, and a `request_hash` miss fails with a diff. No `--live` flag (the plain option was chosen); adding one later is ~20 lines if you want the extra demo beat.

**Adjudication model: `claude-sonnet-5`** ($2/$10 per MTok). Supersedes the brief's `claude-sonnet-4-6`, which is real but older and more expensive. Note this model rejects `temperature` — record-and-replay is what makes that a non-issue.

**Still open, not blocking:** drop Haiku (0.4c, recommended), per-run cost ceiling (proposing $2.00), append-only enforcement style (0.5.6), multi-currency scope (0.5.7).

**Golden set (0.4f): resolved as a frozen labelled subset of the generated month.** The generator writes a manifest recording the exact indices of all 120 hard cases plus a deterministic sample of 180 clean ones. The eval therefore measures the dataset being demoed, and no second generator is needed.

### Two-month dataset — required by demo point 9

The generator emits **two** statement periods, not one:

- `2026-06` — the demo month, 1,200 bank lines. The feedback-loop counterparties escalate to Tier 3/4 here.
- `2026-07` — a ~200-line follow-up month containing the same messy-narrative counterparties.

Without the second period there is nothing to re-run after a human correction, and demo beats 8–9 cannot be shown. This is the Phase 1 fix for 0.4e.

---

# Phase 1 — Skeleton + data

Status: **complete, awaiting approval**. `make up` and `make seed` work end to
end; lint, typecheck and tests are clean.

## 1.1 What landed

- `docker-compose.yml`: postgres 16, weaviate 1.34, api, web. Weaviate is unused
  until Phase 3 but present now so `make up` never has to change shape.
- `001_init.sql`: the full schema from 0.2, including `llm_calls`.
- Ingest: a `StatementParser` protocol with CSV implementations, strict
  normalisation, and an idempotent loader.
- Seeded generator producing two periods and a manifest that doubles as the
  golden-set label file.
- 67 tests, `ruff` and `mypy --strict` clean.

## 1.2 The brief's numbers cannot all hold at once

`PROMPT.md` states three figures that are mutually inconsistent on a
1,200-line month:

| Source | Figure |
|---|---|
| Cascade spec, Tier 0 | clears 40–60% |
| "Done" criteria | ~70–85% auto-matched |
| Demo script, beat 4 | ~140 model calls |

140 model calls out of 1,200 means the deterministic tiers cleared ~88%, which
is above the 70–85% band. Conversely, holding auto-match at 85% leaves ~180
lines for the model even before Tier 3 escalations to the human queue.

**Resolution taken.** The dataset honours the one figure that is explicit and
unambiguous — Tier 0 clears **55%**, inside the stated 40–60% band. Tiers 0+1
clear **80%**, and **20% (240 lines)** reach retrieval and adjudication. The
demo line becomes *"240 calls, not 1,200"*, which makes the same point.

I read the 70–85% band as **auto-committed vs. routed to a human**, not as
"cleared by deterministic rules" — that reading is the only one under which the
brief is self-consistent, since Tier 3 auto-commits also count as auto-matched.
Phase 2 will produce the real number and it can be revisited against evidence
rather than arithmetic. Flagging rather than silently picking.

**Cost consequence:** ~240 calls per 1,200 lines at ~1.5K in / 200 out on
`claude-sonnet-5` is **~$1.00 per 1,000 lines**, up from the ~$0.60 estimated
in 0.4b, and still well inside the $2.00 per-run ceiling. Prompt caching on the
system prompt should bring it back down; Phase 4 will measure rather than
estimate.

## 1.3 Dataset composition (period 2026-06, 1,200 lines)

| Class | Count | Resolved by |
|---|---|---|
| `t0_rent_exact` | 480 | Tier 0 |
| `t0_supplier_exact` | 180 | Tier 0 |
| `t1_unique_counterparty` | 180 | Tier 1 |
| `t1_standing_order` | 70 | Tier 1 |
| `t1_recurring_fee` | 50 | Tier 1 |
| `h_partial` | 36 | Tier 3 |
| `h_batch` (1 credit = 6 invoices) | 28 | Tier 3 |
| `h_fx` (15–45bps drift) | 20 | Tier 3 |
| `h_dup_amount` | 28 | Tier 4 — `insufficient_evidence` |
| `h_transposed_ref` | 28 | Tier 3 |
| `h_fee_netted` | 28 | Tier 3 |
| `h_no_match` | 20 | Tier 3 — `no_match` |
| `h_feedback` | 32 | Tier 4, then Tier 2/3 after write-back |
| `h_narrative_only` | 20 | Tier 3 |

Plus 250 distractor ledger entries with no payment, a third of them `closed`
(the set that must never be proposed as a candidate). Period `2026-07` adds 200
lines carrying the same four `h_feedback` counterparties.

## 1.4 Decisions taken during the build

- **Tests live in `api/tests/`, not at the repo root.** The api image only
  copies `./api`, so root-level tests would not exist inside the container that
  `make test` runs in.
- **psycopg3 directly, no ORM.** An ORM sits between the code and `bigint`
  money columns and is the usual route by which a float reaches a monetary
  path. Rows are `dict_row` throughout — positional access plus a money column
  means a column reorder silently becomes a wrong amount.
- **Append-only enforced by a raising trigger, not a silent `RULE`** (closes
  0.5.6). A `RULE ... DO INSTEAD NOTHING` drops the write silently, which
  during development looks identical to a decision that was never made.
  Verified: `update`/`delete` on `events` both raise.
- **Two guard tests encode non-negotiables the type system cannot.**
  `test_no_floats` AST-scans every module under `db/ ingest/ matching/ seed/
  graph/ retrieval/ llm/` for float literals and `float()` calls (#8), and
  `test_no_wall_clock` bans `today()`/`now()` in decision paths (#4) — that one
  fails silently in production, since the run still produces decisions, just
  different ones tomorrow.
- **Transposed-reference cases are generated last**, once every real document
  reference exists, and the mistyped reference is checked against them. A wrong
  reference that happens to name a real invoice is not a hard case, it is an
  unresolvable one — Tier 0 would confidently commit the wrong match. Caught by
  a test, not by inspection.
- **Dataset composition is asserted, not assumed.** A test fails if Tier 0
  drifts outside the brief's 40–60% band.

## 1.5 Verified against the running stack

| Non-negotiable | Evidence |
|---|---|
| #6 idempotency (file) | Re-running `recon ingest` skips all four files; row counts unchanged. |
| #6 idempotency (row) | Same rows in a re-ordered file: new source row created, 1,200 read, **0 inserted**, 1,200 deduped. |
| #5 append-only | `update`/`delete` on `events` both raise `table events is append-only`. |
| #8 integer money | Every money column is `bigint`; no float or numeric anywhere in a monetary path. |

## 1.6 Still open

Unchanged from 0.5: `LEDGER_SOURCE` confirmation (assumed Xero-shaped CSV),
`DEMO_DATE`, dropping Haiku (0.4c), the $2.00 cost ceiling (0.5.5), and
multi-currency scope (0.5.7). None block Phase 2.

---

# Phase 2 — Tiers 0-1 + eval harness

Status: **complete**. `make eval` runs; 108 tests, `ruff` and `mypy --strict` clean.

## 2.1 The baseline number

This is the client-deck number, measured on the 1,200-line demo month.

| Arm | Auto-matched | Rate | Precision | Recall | False positives |
|---|---|---|---|---|---|
| Tier 0 only (exact reference) | 660 / 1200 | 55.0% | 1.0000 | 0.5729 | **0** |
| Tiers 0–1 (all deterministic rules) | 988 / 1200 | **82.3%** | **1.0000** | 0.8576 | **0** |

Deterministic rules clear **82.3% of the month with zero AI cost and zero wrong
commits**, across all 1,200 labelled lines — not a 300-line sample. Tier 0
alone clears 55%, inside the brief's stated 40–60%.

The 212 lines left over are exactly the classes that need retrieval and
judgement: partial payments, batched settlements, FX drift, processor-obscured
payers, narrative-only identification, and the genuinely ambiguous. **Revised
cost estimate: ~212 model calls per 1,200 lines, ≈$0.88 per 1,000 lines** at
`claude-sonnet-5` rates before prompt caching — down from the $1.00 projected
in 1.2, because the deterministic tiers did better than the composition assumed.

Two populations are reported because they answer different questions. The full
month gives the auto-match rate and the false-positive count. The golden 300 is
deliberately enriched with hard cases (40% hard, against 20% in the population),
so its recall of 0.6968 and escalation of 35.7% are *pessimistic by
construction* — do not quote them as the system's behaviour on real volume.

## 2.2 The transposed-reference case is not hard

Labelled Tier 3 in Phase 1 on the assumption that a mistyped invoice number
needs a model to untangle. It does not. The Tier 1 structural rule matches on
payer + amount + 7-day window and never reads the reference at all, so all 28
instances resolve deterministically at confidence 0.95.

The label is corrected to Tier 1 in the generator. Worth stating plainly because
it cuts the other way from the usual story: **a whole class of "hard" case
turned out to need no intelligence at all**, and the eval is what revealed it.
`expected_tier` is treated as a design expectation throughout, never as ground
truth — scoring uses `expected_decision` and `expected_doc_refs` only.

## 2.3 The golden set had a hole

The first eval scored `t1_recurring_fee` **zero times**. Six instances among 960
clean lines, sampled flat at 180, rounds to nothing — so the recurring-fee rule
shipped with no coverage and the table gave no hint, because a class that is
absent simply does not appear.

Both halves of the golden set are now stratified by case class with a floor of
3, so no rule the cascade implements can be scored by nothing. Generalises: a
sampled eval set silently under-tests exactly the rare paths that rules exist to
handle.

## 2.4 Design decisions

- **Confidence is `Decimal`, not `float`.** Thresholds moved into `MatchConfig`,
  which means `matching/` contains no float literal at all and the guard test
  from Phase 1 covers it without exceptions. It also matches the
  `numeric(4,3)` column, so no conversion sits between the decision and the
  audit row.
- **A claimed ledger entry is invisible to later tiers.** One open item cannot
  settle two bank lines; tested directly.
- **An exact amount is never displaced by an FX-tolerated one.** If both exist,
  the tier declines rather than preferring either. The FX band is 10 bps —
  tight on purpose, since a wider band stops meaning "the same amount arrived".
- **Ablation arms name only what exists.** Retrieval and adjudication rows are
  absent rather than reported as zero: a zero reads as "we tried it and it did
  nothing".
- **The regression gate fails on any false positive**, on either population,
  independently of the precision baseline. Precision ≥ 0.995 and
  no-regression-vs-baseline are additional gates, not the primary one.
- **`git_sha` is injected by the Makefile.** The api container has no `.git`
  mount, and a run that cannot name its own commit cannot claim to be
  replayable.

## 2.5 An operational note

Changing the generator and re-running `make seed` *adds* a second dataset
alongside the first rather than replacing it — which is correct idempotent
behaviour (different content is different content), but it silently doubles the
population an eval sees. `make reset && make up && make seed` is the right move
after any generator change. Worth a line in `DEMO.md` in Phase 6.

## 2.6 Still open

Unchanged: `LEDGER_SOURCE` confirmation, `DEMO_DATE`, dropping Haiku, the $2.00
ceiling, multi-currency scope. Tier 2 recall@10 (0.4d) becomes measurable in
Phase 3 and its definition — correct answer present as a single *or* a
pre-assembled subset — should be re-confirmed against real retrieval numbers.
