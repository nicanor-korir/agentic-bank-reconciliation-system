/**
 * Run detail — the tier breakdown and the live node-by-node progress.
 *
 * Progress is not pushed from the graph: the API tails the durable audit log,
 * so every row shown here corresponds to an event that is already hash-chained
 * in Postgres. The stream replays from the beginning, which is why a reload
 * mid-run shows the whole history rather than the tail.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  describeError,
  fetchRun,
  fetchRunEvents,
  parseRunEvent,
  streamUrl,
} from "../shared/api";
import type { RunDetail, RunEvent } from "../shared/types";
import {
  formatInteger,
  formatMicro,
  formatTime,
  formatTimestamp,
  humaniseKey,
  shortHash,
  shortId,
} from "../shared/format";
import { GitSha, StatusBadge, Tag } from "../components/Badge";
import {
  Button,
  Field,
  FieldGrid,
  Panel,
  PanelBody,
  PanelHeader,
} from "../components/Panel";
import { EmptyState, ErrorState, Loading, Notice, StubWarning } from "../components/States";
import { TierBar } from "../components/TierBar";
import { isRecord } from "../shared/parse";

type DetailState =
  | { kind: "loading" }
  | { kind: "ready"; run: RunDetail }
  | { kind: "error"; message: string };

type StreamState = "connecting" | "live" | "reconnecting" | "done" | "error";

const TERMINAL = new Set(["completed", "halted_cost", "failed"]);

export function RunDetailView({
  runId,
  onBack,
  onOpenQueue,
  onOpenAudit,
}: {
  runId: string;
  onBack: () => void;
  onOpenQueue: (runId: string) => void;
  onOpenAudit: (bankRef: string) => void;
}) {
  const [state, setState] = useState<DetailState>({ kind: "loading" });
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [stream, setStream] = useState<StreamState>("connecting");
  const [auditRef, setAuditRef] = useState("");
  const [refreshToken, setRefreshToken] = useState(0);

  const refresh = useCallback(() => setRefreshToken((token) => token + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    let timer: number | undefined;

    async function load(initial: boolean) {
      try {
        const run = await fetchRun(runId, controller.signal);
        if (cancelled) return;
        setState({ kind: "ready", run });
        // Stop polling once the run can no longer change on its own.
        if (TERMINAL.has(run.status) && !run.in_flight && timer !== undefined) {
          window.clearInterval(timer);
          timer = undefined;
        }
      } catch (error) {
        if (cancelled || controller.signal.aborted) return;
        if (initial) setState({ kind: "error", message: describeError(error) });
      }
    }

    void load(true);
    timer = window.setInterval(() => void load(false), 3000);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearInterval(timer);
      controller.abort();
    };
  }, [runId, refreshToken]);

  useEffect(() => {
    const controller = new AbortController();
    let source: EventSource | null = null;
    let cancelled = false;

    /** Events are keyed by seq, so replay and backfill cannot double-count. */
    const merge = (incoming: RunEvent[]) => {
      if (incoming.length === 0) return;
      setEvents((current) => {
        const bySeq = new Map<number, RunEvent>();
        for (const event of current) bySeq.set(event.seq, event);
        for (const event of incoming) {
          const existing = bySeq.get(event.seq);
          if (existing === undefined) {
            bySeq.set(event.seq, event);
            continue;
          }
          // The backfill carries prev_hash, created_at and the full hash; the
          // stream carries only a 12-character prefix. Keep the richer half of
          // each field regardless of which arrived first.
          bySeq.set(event.seq, {
            ...existing,
            ...event,
            hash: event.hash.length >= existing.hash.length ? event.hash : existing.hash,
            prev_hash: event.prev_hash ?? existing.prev_hash,
            created_at: event.created_at ?? existing.created_at,
          });
        }
        return [...bySeq.values()].sort((a, b) => a.seq - b.seq);
      });
    };

    async function backfill() {
      try {
        const history = await fetchRunEvents(runId, -1, controller.signal);
        if (!cancelled) merge(history);
      } catch {
        // Non-fatal: the stream replays from seq 0 anyway.
      }
    }

    void backfill();

    try {
      source = new EventSource(streamUrl(runId));
    } catch {
      setStream("error");
      return () => {
        cancelled = true;
        controller.abort();
      };
    }

    const onNode = (event: Event) => {
      if (!(event instanceof MessageEvent)) return;
      const raw: unknown = event.data;
      if (typeof raw !== "string") return;
      try {
        const parsed: unknown = JSON.parse(raw);
        merge([parseRunEvent(parsed)]);
        setStream("live");
      } catch {
        // A malformed frame is not worth tearing the stream down for.
      }
    };

    const onDone = () => {
      setStream("done");
      source?.close();
      // Pull a final summary: status, end time and cost are only settled now.
      setRefreshToken((token) => token + 1);
    };

    const onError = () => {
      // EventSource retries on its own unless it has given up entirely.
      setStream(source?.readyState === EventSource.CLOSED ? "error" : "reconnecting");
    };

    source.addEventListener("node", onNode);
    source.addEventListener("done", onDone);
    source.addEventListener("error", onError);
    source.addEventListener("open", () => setStream("live"));

    return () => {
      cancelled = true;
      controller.abort();
      if (source !== null) {
        source.removeEventListener("node", onNode);
        source.removeEventListener("done", onDone);
        source.removeEventListener("error", onError);
        source.close();
      }
    };
  }, [runId]);

  const run = state.kind === "ready" ? state.run : null;
  const totalCommitted = useMemo(
    () =>
      run === null
        ? 0
        : Object.values(run.by_tier).reduce((sum, count) => sum + count, 0),
    [run],
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Button variant="ghost" onClick={onBack}>
            ← Runs
          </Button>
          <h2 className="font-mono text-sm text-slate-900">{shortId(runId, 36)}</h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" onClick={() => onOpenQueue(runId)}>
            Exception queue
          </Button>
          <form
            className="flex items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              const value = auditRef.trim();
              if (value !== "") onOpenAudit(value);
            }}
          >
            <input
              value={auditRef}
              onChange={(event) => setAuditRef(event.target.value)}
              placeholder="bank_ref"
              aria-label="Bank reference to audit"
              className="w-40 rounded-md border border-slate-300 px-2.5 py-1.5 font-mono text-xs text-slate-900 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 focus:outline-none"
            />
            <Button variant="secondary" type="submit" disabled={auditRef.trim() === ""}>
              Open audit
            </Button>
          </form>
        </div>
      </div>

      {state.kind === "loading" && (
        <Panel>
          <Loading label="Loading run…" />
        </Panel>
      )}

      {state.kind === "error" && (
        <Panel>
          <ErrorState message={state.message} onRetry={refresh} />
        </Panel>
      )}

      {run !== null && (
        <>
          {run.adjudicator === "stub" && <StubWarning />}

          {run.error !== null && (
            <Notice tone="danger" title="The run thread reported an error">
              <span className="font-mono">{run.error}</span>
            </Notice>
          )}

          {run.status === "awaiting_human" && (
            <Notice tone="info" title="Paused for human review">
              <span className="flex flex-wrap items-center gap-3">
                <span>
                  The graph is holding at an interrupt. It resumes from this checkpoint
                  once the queue is resolved — nothing is recomputed.
                </span>
                <Button variant="primary" onClick={() => onOpenQueue(runId)}>
                  Open the exception queue
                </Button>
              </span>
            </Notice>
          )}

          <Panel>
            <PanelHeader
              title="Run"
              subtitle={
                run.replay_of !== null ? (
                  <span>
                    Replay of{" "}
                    <span className="font-mono">{shortId(run.replay_of, 12)}</span>
                  </span>
                ) : (
                  "Original run"
                )
              }
              actions={
                <>
                  {run.in_flight && <Tag tone="accent">in flight</Tag>}
                  <StatusBadge status={run.status} />
                </>
              }
            />
            <PanelBody>
              <FieldGrid columns={4}>
                <Field label="Cost" mono>
                  {run.cost_display}
                </Field>
                <Field label="Model version" mono>
                  {run.model_version}
                </Field>
                <Field label="Prompt version" mono>
                  {run.prompt_version}
                </Field>
                <Field label="Adjudicator" mono>
                  {run.adjudicator ?? "—"}
                </Field>
                <Field label="Commit">
                  <GitSha sha={run.git_sha} dirty={run.git_dirty} />
                </Field>
                <Field label="Started">{formatTimestamp(run.started_at)}</Field>
                <Field label="Ended">{formatTimestamp(run.ended_at)}</Field>
                <Field label="Auto-committed" mono>
                  {formatInteger(totalCommitted)}
                </Field>
              </FieldGrid>
            </PanelBody>
          </Panel>

          <Panel>
            <PanelHeader
              title="Tier breakdown"
              subtitle="Auto-committed decisions per tier of the cascade"
            />
            <PanelBody>
              {totalCommitted === 0 ? (
                <EmptyState
                  title="Nothing auto-committed yet"
                  detail="The breakdown fills in as the deterministic tiers and the adjudicator commit decisions."
                />
              ) : (
                <TierBar byTier={run.by_tier} />
              )}
            </PanelBody>
          </Panel>
        </>
      )}

      <Panel>
        <PanelHeader
          title="Progress"
          subtitle="One row per graph node, streamed from the hash-chained audit log"
          actions={<StreamIndicator state={stream} />}
        />
        {events.length === 0 ? (
          <EmptyState
            title="No events yet"
            detail={
              stream === "error"
                ? "The event stream could not be opened. The run may still be progressing — reopen this view to retry."
                : "Waiting for the first node to record its event."
            }
          />
        ) : (
          <ol className="divide-y divide-slate-100">
            {events.map((event) => (
              <EventRow key={event.seq} event={event} />
            ))}
          </ol>
        )}
      </Panel>
    </div>
  );
}

function StreamIndicator({ state }: { state: StreamState }) {
  const label: Record<StreamState, string> = {
    connecting: "connecting…",
    live: "live",
    reconnecting: "reconnecting…",
    done: "stream closed",
    error: "stream unavailable",
  };
  const dot: Record<StreamState, string> = {
    connecting: "bg-slate-300",
    live: "bg-emerald-500 animate-pulse",
    reconnecting: "bg-amber-500 animate-pulse",
    done: "bg-slate-400",
    error: "bg-rose-500",
  };
  return (
    <span className="inline-flex items-center gap-2 text-xs text-slate-500">
      <span className={`h-1.5 w-1.5 rounded-full ${dot[state]}`} />
      {label[state]}
    </span>
  );
}

function EventRow({ event }: { event: RunEvent }) {
  return (
    <li className="flex flex-wrap items-baseline gap-x-4 gap-y-2 px-6 py-3">
      <span className="w-10 shrink-0 font-mono text-xs text-slate-400 tabular-nums">
        #{event.seq}
      </span>
      <span className="w-44 shrink-0 font-mono text-xs font-medium text-slate-900">
        {event.node}
      </span>
      <span className="flex flex-1 flex-wrap items-center gap-1.5">
        <PayloadChips payload={event.payload} />
      </span>
      <span className="shrink-0 text-xs text-slate-400">{formatTime(event.created_at)}</span>
      <span
        className="shrink-0 font-mono text-[11px] text-slate-400"
        title={event.prev_hash !== null ? `prev ${event.prev_hash}` : "hash prefix"}
      >
        {shortHash(event.hash)}
      </span>
    </li>
  );
}

/** Keys that carry the story of the run, in the order a reader wants them. */
const KEY_ORDER = [
  "bank_lines",
  "period",
  "committed",
  "applied",
  "lines",
  "candidates_offered",
  "truncated_subset_searches",
  "calls",
  "escalated",
  "cost_micro",
  "decisions",
  "unresolved",
  "status",
  "halt_reason",
];

function orderKeys(payload: Record<string, unknown>): string[] {
  const keys = Object.keys(payload).filter((key) => key !== "node");
  return keys.sort((a, b) => {
    const ia = KEY_ORDER.indexOf(a);
    const ib = KEY_ORDER.indexOf(b);
    if (ia === -1 && ib === -1) return a.localeCompare(b);
    if (ia === -1) return 1;
    if (ib === -1) return -1;
    return ia - ib;
  });
}

function PayloadChips({ payload }: { payload: Record<string, unknown> }) {
  const chips: ReactNode[] = [];

  for (const key of orderKeys(payload)) {
    const value = payload[key];

    // `bank_refs` can be hundreds of entries — summarise, keep it reachable.
    if (key === "bank_refs" && Array.isArray(value)) {
      const refs = value.filter((item): item is string => typeof item === "string");
      if (refs.length === 0) continue;
      chips.push(
        <Chip
          key={key}
          label="refs"
          value={formatInteger(refs.length)}
          title={refs.slice(0, 40).join(", ") + (refs.length > 40 ? " …" : "")}
        />,
      );
      continue;
    }

    // `by_tier` is `{"tier0": n, "tier1": n}` on the deterministic node.
    if (key === "by_tier" && isRecord(value)) {
      for (const [tier, count] of Object.entries(value)) {
        if (typeof count !== "number") continue;
        chips.push(
          <Chip key={`${key}-${tier}`} label={humaniseKey(tier)} value={formatInteger(count)} />,
        );
      }
      continue;
    }

    if (typeof value === "number") {
      const display = key === "cost_micro" ? formatMicro(value) : formatInteger(value);
      const tone =
        key === "truncated_subset_searches" && value > 0
          ? "warning"
          : key === "escalated" && value > 0
            ? "accent"
            : "neutral";
      chips.push(<Chip key={key} label={humaniseKey(key)} value={display} tone={tone} />);
      continue;
    }

    if (typeof value === "string" && value !== "") {
      chips.push(
        <Chip
          key={key}
          label={humaniseKey(key)}
          value={value}
          tone={key === "halt_reason" ? "warning" : "neutral"}
        />,
      );
      continue;
    }

    if (typeof value === "boolean") {
      chips.push(<Chip key={key} label={humaniseKey(key)} value={value ? "yes" : "no"} />);
    }
  }

  if (chips.length === 0) {
    return <span className="text-xs text-slate-400">—</span>;
  }
  return <>{chips}</>;
}

function Chip({
  label,
  value,
  title,
  tone = "neutral",
}: {
  label: string;
  value: string;
  title?: string;
  tone?: "neutral" | "accent" | "warning";
}) {
  const toneClass =
    tone === "warning"
      ? "bg-amber-50 text-amber-800 ring-amber-200"
      : tone === "accent"
        ? "bg-indigo-50 text-indigo-700 ring-indigo-200"
        : "bg-slate-50 text-slate-600 ring-slate-200";
  return (
    <span
      title={title}
      className={`inline-flex items-baseline gap-1.5 rounded px-1.5 py-0.5 text-[11px] ring-1 ring-inset ${toneClass}`}
    >
      <span className="opacity-70">{label}</span>
      <span className="font-mono font-medium tabular-nums">{value}</span>
    </span>
  );
}
