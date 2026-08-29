/**
 * The API surface, as the UI sees it.
 *
 * Field names mirror the JSON exactly (snake_case) so a reader can diff this
 * file against the FastAPI handlers without translating.
 */

export type Health = {
  status: string;
  service: string;
  version: string;
};

export type Stats = {
  tenant: string;
  sources: number;
  bank_lines: number;
  ledger_entries: number;
  open_ledger_entries: number;
};

/** A row of `GET /runs`. */
export type RunSummary = {
  id: string;
  started_at: string | null;
  ended_at: string | null;
  status: string;
  model_version: string;
  git_sha: string;
  git_dirty: boolean;
  cost_total_micro: number;
  replay_of: string | null;
  adjudicator: string | null;
  auto_committed: number;
  escalated: number;
};

/** `GET /runs/{id}`. `by_tier` counts *auto-committed* decisions only. */
export type RunDetail = {
  id: string;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  by_tier: Record<string, number>;
  cost_display: string;
  cost_total_micro: number;
  in_flight: boolean;
  error: string | null;
  model_version: string;
  prompt_version: string;
  git_sha: string;
  git_dirty: boolean;
  adjudicator: string | null;
  replay_of: string | null;
  config_snapshot: Record<string, unknown>;
};

/**
 * One node of the audit chain. The SSE stream sends a 12-character `hash` and
 * omits `prev_hash` / `created_at`; `GET /runs/{id}/events` sends all three in
 * full. Both are normalised into this shape.
 */
export type RunEvent = {
  seq: number;
  node: string;
  payload: Record<string, unknown>;
  hash: string;
  prev_hash: string | null;
  created_at: string | null;
};

export type CandidateKind = "single" | "subset";

export type CandidateLineItem = {
  doc_ref: string | null;
  description: string;
  counterparty: string | null;
  amount_minor: number;
};

export type Candidate = {
  id: string;
  kind: CandidateKind;
  ledger_entry_ids: number[];
  doc_refs: string[];
  total_minor: number;
  /** Signed gap between the bank line and the candidate total. 0 is exact. */
  difference_minor: number;
  found_by: string[];
  items: CandidateLineItem[];
};

export type QueueItem = {
  bank_line_id: number;
  bank_ref: string;
  value_date: string;
  amount_minor: number;
  currency: string;
  narrative: string;
  counterparty: string | null;
  candidates: Candidate[];
  /** Present when the model reached a decision it would not auto-commit. */
  decision: string | null;
  confidence: number | null;
  rationale: string | null;
  evidence: string[];
  /** Present instead of the above when adjudication itself failed. */
  reason: string | null;
};

export type Queue = {
  run_id: string;
  count: number;
  items: QueueItem[];
};

export type ResolutionAction = "approve" | "reject" | "reassign";

export type Resolution = {
  bank_ref: string;
  action: ResolutionAction;
  ledger_entry_ids: number[];
  doc_refs: string[];
  reviewer: string;
  note?: string;
};

/** `POST /runs/{id}/resolve` — the run summary plus the write-back outcome. */
export type ResolveSummary = {
  run_id: string;
  status: string;
  bank_lines: number;
  committed: number;
  by_tier: Record<string, number>;
  queued_for_human: number;
  unresolved: number;
  llm_calls: number;
  adjudication_errors: number;
  cost_micro: number;
  cost_display: string;
  halt_reason: string | null;
  written_back: number;
  write_back_error: string | null;
};

export type BankLine = {
  id: number;
  bank_ref: string;
  value_date: string;
  booking_date: string | null;
  amount_minor: number;
  currency: string;
  narrative: string;
  counterparty: string | null;
  content_hash: string;
};

export type Decision = {
  id: number;
  run_id: string;
  tier: number;
  decision: string;
  confidence: number | null;
  rationale: string;
  evidence: string[];
  auto_committed: boolean;
  /** Set when this row replaces an earlier one. Never an in-place edit. */
  supersedes_id: number | null;
  created_at: string | null;
  ledger_entry_ids: number[];
  doc_refs: string[];
};

export type HumanReview = {
  id: number;
  decision_id: number;
  reviewer: string;
  action: string;
  corrected_ledger_entry_ids: number[];
  note: string | null;
  reviewed_at: string | null;
  written_back_at: string | null;
};

export type ModelCall = {
  id: number;
  run_id: string;
  request_hash: string;
  input_tokens: number;
  output_tokens: number;
  cost_micro: number;
  latency_ms: number;
  created_at: string | null;
  response: unknown;
};

export type AuditEvent = {
  seq: number;
  node: string;
  hash: string;
  run_id: string;
  created_at: string | null;
};

export type LineAudit = {
  line: BankLine;
  decisions: Decision[];
  human_reviews: HumanReview[];
  model_calls: ModelCall[];
  events: AuditEvent[];
};

export type CreatedRun = {
  run_id: string;
  adjudicator: string;
};
