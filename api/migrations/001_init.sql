-- Phase 1 schema. See NOTES.md 0.2 for the reasoning behind each choice.

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
  period       text not null,
  sha256       char(64) not null,
  row_count    integer not null,
  ingested_at  timestamptz not null default now(),
  -- NON-NEGOTIABLE #6: re-ingesting the same bytes is a no-op.
  unique (tenant_id, sha256)
);

create table bank_lines (
  id            bigserial primary key,
  tenant_id     text not null references tenants(id),
  source_id     bigint not null references sources(id),
  value_date    date not null,
  booking_date  date,
  -- Signed integer minor units. NON-NEGOTIABLE #8: never float, never numeric.
  amount_minor  bigint not null,
  currency      char(3) not null,
  narrative     text not null,
  counterparty  text,
  bank_ref      text,
  raw           jsonb not null,
  content_hash  char(64) not null,
  unique (tenant_id, content_hash)
);
create index bank_lines_tenant_date_idx on bank_lines (tenant_id, value_date);
create index bank_lines_tenant_amount_idx on bank_lines (tenant_id, amount_minor);
create index bank_lines_bank_ref_idx on bank_lines (tenant_id, bank_ref);

create table ledger_entries (
  id                bigserial primary key,
  tenant_id         text not null references tenants(id),
  source_id         bigint not null references sources(id),
  entry_date        date not null,
  due_date          date,
  amount_minor      bigint not null,
  -- What makes partial payments representable at all.
  open_amount_minor bigint not null,
  currency          char(3) not null,
  description       text not null,
  counterparty      text,
  doc_ref           text,
  side              text not null check (side in ('AR','AP')),
  status            text not null check (status in ('open','closed')),
  raw               jsonb not null,
  content_hash      char(64) not null,
  unique (tenant_id, content_hash)
);
create index ledger_open_amount_idx on ledger_entries (tenant_id, status, amount_minor);
create index ledger_doc_ref_idx on ledger_entries (tenant_id, doc_ref);
create index ledger_entry_date_idx on ledger_entries (tenant_id, entry_date);

create table runs (
  id               uuid primary key,
  tenant_id        text not null references tenants(id),
  started_at       timestamptz not null default now(),
  ended_at         timestamptz,
  status           text not null check (status in
                     ('running','awaiting_human','completed','halted_cost','failed')),
  config_snapshot  jsonb not null,
  model_version    text not null,
  prompt_version   text not null,
  git_sha          text not null,
  -- Claiming replayability from an uncommitted tree is exactly the kind of
  -- thing a technical buyer catches. Record it.
  git_dirty        boolean not null,
  seed             bigint not null,
  cost_total_micro bigint not null default 0,
  replay_of        uuid references runs(id)
);

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
create index decisions_run_tier_idx on decisions (run_id, tier);
create index decisions_line_idx on decisions (tenant_id, bank_line_id);
create unique index decisions_supersedes_uniq on decisions (supersedes_id)
  where supersedes_id is not null;

create table events (
  id          bigserial primary key,
  run_id      uuid not null references runs(id),
  seq         integer not null,
  node        text not null,
  payload     jsonb not null,
  prev_hash   char(64) not null,
  hash        char(64) not null,
  created_at  timestamptz not null default now(),
  unique (run_id, seq),
  unique (run_id, hash)
);

create table human_reviews (
  id                          bigserial primary key,
  decision_id                 bigint not null references decisions(id),
  reviewer                    text not null,
  action                      text not null check (action in ('approve','reject','reassign')),
  corrected_ledger_entry_ids  bigint[] not null default '{}',
  note                        text,
  reviewed_at                 timestamptz not null default now(),
  written_back_at             timestamptz
);

-- Recorded model I/O. This is what makes replay byte-identical without
-- pretending the model itself is deterministic (NOTES.md 0.4a).
create table llm_calls (
  id             bigserial primary key,
  run_id         uuid not null references runs(id),
  bank_line_id   bigint not null references bank_lines(id),
  request_hash   char(64) not null,
  request        jsonb not null,
  response       jsonb not null,
  input_tokens   integer not null,
  output_tokens  integer not null,
  cost_micro     bigint not null,
  latency_ms     integer not null,
  created_at     timestamptz not null default now()
);
create index llm_calls_request_hash_idx on llm_calls (request_hash);
create index llm_calls_run_idx on llm_calls (run_id);

-- NON-NEGOTIABLE #5: append-only, enforced by the database rather than by
-- convention. Raising (not a silent RULE) so a violation is a loud test
-- failure instead of a decision that quietly fails to persist.
create or replace function reject_mutation() returns trigger language plpgsql as $$
begin
  raise exception 'table % is append-only (%.% attempted)',
    tg_table_name, tg_table_name, lower(tg_op);
end;
$$;

create trigger decisions_append_only before update or delete on decisions
  for each statement execute function reject_mutation();
create trigger events_append_only before update or delete on events
  for each statement execute function reject_mutation();
