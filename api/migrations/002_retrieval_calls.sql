-- Recorded retrieval, for the same reason model calls are recorded.
--
-- Replay reproduces a run "given the same inputs", and a vector index is an
-- input that changes underneath you -- most obviously when a human correction
-- is written back between the run and the replay, which is a thing this system
-- does on purpose. Re-executing retrieval on replay therefore compares two
-- different worlds and reports drift that is not a regression.
--
-- The cut is deliberate: deterministic code (the amount/date windows, the
-- subset search, Tiers 0-1) is re-executed for real on replay, because that is
-- where genuine replay bugs hide. Anything that leaves the process -- the
-- model, the vector store -- is recorded and replayed.
create table retrieval_calls (
  id            bigserial primary key,
  run_id        uuid not null references runs(id),
  bank_line_id  bigint not null references bank_lines(id),
  kind          text not null check (kind in ('open_items','resolved_pairs')),
  query_hash    char(64) not null,
  response      jsonb not null,
  created_at    timestamptz not null default now(),
  unique (run_id, bank_line_id, kind)
);
create index retrieval_calls_lookup_idx on retrieval_calls (run_id, query_hash);
