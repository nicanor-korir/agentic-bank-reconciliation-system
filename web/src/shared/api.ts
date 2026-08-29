/**
 * The only place that talks to the API.
 *
 * Every response is parsed into a declared type before it reaches a component,
 * so a schema change shows up here rather than as a blank screen three views
 * away.
 */

import {
  asArray,
  asBoolean,
  asNumber,
  asNumberArray,
  asNumberMap,
  asOptionalNumber,
  asOptionalString,
  asRecord,
  asString,
  asStringArray,
  isRecord,
} from "./parse";
import type {
  AuditEvent,
  BankLine,
  Candidate,
  CandidateKind,
  CandidateLineItem,
  CreatedRun,
  Decision,
  Health,
  HumanReview,
  LineAudit,
  ModelCall,
  Queue,
  QueueItem,
  Resolution,
  ResolveSummary,
  RunDetail,
  RunEvent,
  RunSummary,
  Stats,
} from "./types";

export const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  readonly status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** A message worth putting in front of a person, whatever went wrong. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Unknown error";
}

export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

async function request(
  path: string,
  init: RequestInit & { signal?: AbortSignal },
): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, init);
  } catch (error) {
    if (init.signal?.aborted) throw error;
    throw new ApiError(
      `Cannot reach the API at ${API_URL}. Is it running? (${describeError(error)})`,
    );
  }

  if (!response.ok) {
    const detail = await readDetail(response);
    throw new ApiError(
      detail ? `HTTP ${response.status} — ${detail}` : `HTTP ${response.status}`,
      response.status,
    );
  }

  try {
    return (await response.json()) as unknown;
  } catch {
    throw new ApiError(`${path} returned a body that is not JSON`, response.status);
  }
}

/** FastAPI puts the human-readable half of an error in `detail`. */
async function readDetail(response: Response): Promise<string | null> {
  try {
    const body: unknown = await response.json();
    if (isRecord(body) && typeof body.detail === "string") return body.detail;
  } catch {
    return null;
  }
  return null;
}

function get(path: string, signal?: AbortSignal): Promise<unknown> {
  return request(path, { signal, headers: { Accept: "application/json" } });
}

function post(path: string, body: unknown, signal?: AbortSignal): Promise<unknown> {
  return request(path, {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------- parsers ---

function parseHealth(raw: unknown): Health {
  const value = asRecord(raw);
  return {
    status: asString(value.status, "unknown"),
    service: asString(value.service, "unknown"),
    version: asString(value.version, "?"),
  };
}

function parseStats(raw: unknown): Stats {
  const value = asRecord(raw);
  return {
    tenant: asString(value.tenant, "—"),
    sources: asNumber(value.sources),
    bank_lines: asNumber(value.bank_lines),
    ledger_entries: asNumber(value.ledger_entries),
    open_ledger_entries: asNumber(value.open_ledger_entries),
  };
}

function parseRunSummary(raw: unknown): RunSummary {
  const value = asRecord(raw);
  return {
    id: asString(value.id),
    started_at: asOptionalString(value.started_at),
    ended_at: asOptionalString(value.ended_at),
    status: asString(value.status, "unknown"),
    model_version: asString(value.model_version, "—"),
    git_sha: asString(value.git_sha, "—"),
    git_dirty: asBoolean(value.git_dirty),
    cost_total_micro: asNumber(value.cost_total_micro),
    replay_of: asOptionalString(value.replay_of),
    adjudicator: asOptionalString(value.adjudicator),
    auto_committed: asNumber(value.auto_committed),
    escalated: asNumber(value.escalated),
  };
}

function parseRunDetail(raw: unknown): RunDetail {
  const value = asRecord(raw);
  return {
    id: asString(value.id),
    status: asString(value.status, "unknown"),
    started_at: asOptionalString(value.started_at),
    ended_at: asOptionalString(value.ended_at),
    by_tier: asNumberMap(value.by_tier),
    cost_display: asString(value.cost_display, "$0.0000"),
    cost_total_micro: asNumber(value.cost_total_micro),
    in_flight: asBoolean(value.in_flight),
    error: asOptionalString(value.error),
    model_version: asString(value.model_version, "—"),
    prompt_version: asString(value.prompt_version, "—"),
    git_sha: asString(value.git_sha, "—"),
    git_dirty: asBoolean(value.git_dirty),
    adjudicator: asOptionalString(value.adjudicator),
    replay_of: asOptionalString(value.replay_of),
    config_snapshot: asRecord(value.config_snapshot),
  };
}

export function parseRunEvent(raw: unknown): RunEvent {
  const value = asRecord(raw);
  return {
    seq: asNumber(value.seq, -1),
    node: asString(value.node, "unknown"),
    payload: asRecord(value.payload),
    hash: asString(value.hash),
    prev_hash: asOptionalString(value.prev_hash),
    created_at: asOptionalString(value.created_at),
  };
}

function parseCandidateItem(raw: unknown): CandidateLineItem {
  const value = asRecord(raw);
  return {
    doc_ref: asOptionalString(value.doc_ref),
    description: asString(value.description, "—"),
    counterparty: asOptionalString(value.counterparty),
    amount_minor: asNumber(value.amount_minor),
  };
}

function parseCandidateKind(raw: unknown): CandidateKind {
  return raw === "subset" ? "subset" : "single";
}

function parseCandidate(raw: unknown, index: number): Candidate {
  const value = asRecord(raw);
  return {
    id: asString(value.id, `C${index + 1}`),
    kind: parseCandidateKind(value.kind),
    ledger_entry_ids: asNumberArray(value.ledger_entry_ids),
    doc_refs: asStringArray(value.doc_refs),
    total_minor: asNumber(value.total_minor),
    difference_minor: asNumber(value.difference_minor),
    found_by: asStringArray(value.found_by),
    items: asArray(value.items).map(parseCandidateItem),
  };
}

function parseQueueItem(raw: unknown): QueueItem {
  const value = asRecord(raw);
  return {
    bank_line_id: asNumber(value.bank_line_id, -1),
    bank_ref: asString(value.bank_ref, "—"),
    value_date: asString(value.value_date),
    amount_minor: asNumber(value.amount_minor),
    currency: asString(value.currency, "USD"),
    narrative: asString(value.narrative),
    counterparty: asOptionalString(value.counterparty),
    candidates: asArray(value.candidates).map(parseCandidate),
    decision: asOptionalString(value.decision),
    confidence: asOptionalNumber(value.confidence),
    rationale: asOptionalString(value.rationale),
    evidence: asStringArray(value.evidence),
    reason: asOptionalString(value.reason),
  };
}

function parseQueue(raw: unknown): Queue {
  const value = asRecord(raw);
  const items = asArray(value.items).map(parseQueueItem);
  return {
    run_id: asString(value.run_id),
    count: asNumber(value.count, items.length),
    items,
  };
}

function parseResolveSummary(raw: unknown): ResolveSummary {
  const value = asRecord(raw);
  return {
    run_id: asString(value.run_id),
    status: asString(value.status, "unknown"),
    bank_lines: asNumber(value.bank_lines),
    committed: asNumber(value.committed),
    by_tier: asNumberMap(value.by_tier),
    queued_for_human: asNumber(value.queued_for_human),
    unresolved: asNumber(value.unresolved),
    llm_calls: asNumber(value.llm_calls),
    adjudication_errors: asNumber(value.adjudication_errors),
    cost_micro: asNumber(value.cost_micro),
    cost_display: asString(value.cost_display, "$0.0000"),
    halt_reason: asOptionalString(value.halt_reason),
    written_back: asNumber(value.written_back),
    write_back_error: asOptionalString(value.write_back_error),
  };
}

function parseBankLine(raw: unknown): BankLine {
  const value = asRecord(raw);
  return {
    id: asNumber(value.id, -1),
    bank_ref: asString(value.bank_ref, "—"),
    value_date: asString(value.value_date),
    booking_date: asOptionalString(value.booking_date),
    amount_minor: asNumber(value.amount_minor),
    currency: asString(value.currency, "USD"),
    narrative: asString(value.narrative),
    counterparty: asOptionalString(value.counterparty),
    content_hash: asString(value.content_hash),
  };
}

function parseDecision(raw: unknown): Decision {
  const value = asRecord(raw);
  return {
    id: asNumber(value.id, -1),
    run_id: asString(value.run_id),
    tier: asNumber(value.tier, -1),
    decision: asString(value.decision, "unknown"),
    confidence: asOptionalNumber(value.confidence),
    rationale: asString(value.rationale),
    evidence: asStringArray(value.evidence),
    auto_committed: asBoolean(value.auto_committed),
    supersedes_id: asOptionalNumber(value.supersedes_id),
    created_at: asOptionalString(value.created_at),
    ledger_entry_ids: asNumberArray(value.ledger_entry_ids),
    doc_refs: asStringArray(value.doc_refs),
  };
}

function parseHumanReview(raw: unknown): HumanReview {
  const value = asRecord(raw);
  return {
    id: asNumber(value.id, -1),
    decision_id: asNumber(value.decision_id, -1),
    reviewer: asString(value.reviewer, "—"),
    action: asString(value.action, "—"),
    corrected_ledger_entry_ids: asNumberArray(value.corrected_ledger_entry_ids),
    note: asOptionalString(value.note),
    reviewed_at: asOptionalString(value.reviewed_at),
    written_back_at: asOptionalString(value.written_back_at),
  };
}

function parseModelCall(raw: unknown): ModelCall {
  const value = asRecord(raw);
  return {
    id: asNumber(value.id, -1),
    run_id: asString(value.run_id),
    request_hash: asString(value.request_hash),
    input_tokens: asNumber(value.input_tokens),
    output_tokens: asNumber(value.output_tokens),
    cost_micro: asNumber(value.cost_micro),
    latency_ms: asNumber(value.latency_ms),
    created_at: asOptionalString(value.created_at),
    response: value.response,
  };
}

function parseAuditEvent(raw: unknown): AuditEvent {
  const value = asRecord(raw);
  return {
    seq: asNumber(value.seq, -1),
    node: asString(value.node, "unknown"),
    hash: asString(value.hash),
    run_id: asString(value.run_id),
    created_at: asOptionalString(value.created_at),
  };
}

function parseLineAudit(raw: unknown): LineAudit {
  const value = asRecord(raw);
  return {
    line: parseBankLine(value.line),
    decisions: asArray(value.decisions).map(parseDecision),
    human_reviews: asArray(value.human_reviews).map(parseHumanReview),
    model_calls: asArray(value.model_calls).map(parseModelCall),
    events: asArray(value.events).map(parseAuditEvent),
  };
}

// -------------------------------------------------------------- endpoints ---

export async function fetchHealth(signal?: AbortSignal): Promise<Health> {
  return parseHealth(await get("/health", signal));
}

export async function fetchStats(signal?: AbortSignal): Promise<Stats> {
  return parseStats(await get("/stats", signal));
}

export async function fetchRuns(signal?: AbortSignal): Promise<RunSummary[]> {
  return asArray(await get("/runs", signal)).map(parseRunSummary);
}

export async function createRun(
  period: string,
  adjudicator: string,
  signal?: AbortSignal,
): Promise<CreatedRun> {
  const value = asRecord(await post("/runs", { period, adjudicator }, signal));
  return {
    run_id: asString(value.run_id),
    adjudicator: asString(value.adjudicator, adjudicator),
  };
}

export async function fetchRun(runId: string, signal?: AbortSignal): Promise<RunDetail> {
  return parseRunDetail(await get(`/runs/${encodeURIComponent(runId)}`, signal));
}

export async function fetchRunEvents(
  runId: string,
  after = -1,
  signal?: AbortSignal,
): Promise<RunEvent[]> {
  const path = `/runs/${encodeURIComponent(runId)}/events?after=${after}`;
  return asArray(await get(path, signal)).map(parseRunEvent);
}

export async function fetchQueue(runId: string, signal?: AbortSignal): Promise<Queue> {
  return parseQueue(await get(`/runs/${encodeURIComponent(runId)}/queue`, signal));
}

export async function submitResolutions(
  runId: string,
  resolutions: Resolution[],
  signal?: AbortSignal,
): Promise<ResolveSummary> {
  const path = `/runs/${encodeURIComponent(runId)}/resolve`;
  return parseResolveSummary(await post(path, { resolutions }, signal));
}

export async function fetchLineAudit(
  bankRef: string,
  signal?: AbortSignal,
): Promise<LineAudit> {
  const path = `/lines/${encodeURIComponent(bankRef)}/audit`;
  return parseLineAudit(await get(path, signal));
}

export function streamUrl(runId: string): string {
  return `${API_URL}/runs/${encodeURIComponent(runId)}/stream`;
}
