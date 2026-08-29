/**
 * Audit drill-down — "show me this one transaction".
 *
 * The decisions list is append-only. A correction never edits the row it
 * replaces; it is a new row pointing back at the old one, and both stay here
 * forever. This view makes that visible rather than asserting it.
 */

import { useEffect, useMemo, useState } from "react";
import { describeError, fetchLineAudit, isNotFound } from "../shared/api";
import type { Decision, HumanReview, LineAudit, ModelCall } from "../shared/types";
import {
  formatConfidence,
  formatDate,
  formatInteger,
  formatMicro,
  formatTimestamp,
  shortHash,
  shortId,
} from "../shared/format";
import { TierBadge, Tag } from "../components/Badge";
import {
  Button,
  Field,
  FieldGrid,
  Panel,
  PanelBody,
  PanelHeader,
} from "../components/Panel";
import { EmptyState, ErrorState, Loading, Notice } from "../components/States";
import { Direction, Money } from "../components/Money";

type AuditState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; audit: LineAudit }
  | { kind: "missing"; bankRef: string }
  | { kind: "error"; message: string };

export function AuditView({
  bankRef,
  onBack,
  onLookup,
}: {
  bankRef: string;
  onBack: () => void;
  /** Lifted so the searched ref stays in the URL-less view state. */
  onLookup: (bankRef: string) => void;
}) {
  const [state, setState] = useState<AuditState>(
    bankRef === "" ? { kind: "idle" } : { kind: "loading" },
  );
  const [draft, setDraft] = useState(bankRef);
  // Retrying the same reference must refetch, and `bankRef` alone would not change.
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    setDraft(bankRef);
    if (bankRef === "") {
      setState({ kind: "idle" });
      return;
    }

    const controller = new AbortController();
    setState({ kind: "loading" });

    async function load() {
      try {
        const audit = await fetchLineAudit(bankRef, controller.signal);
        if (controller.signal.aborted) return;
        setState({ kind: "ready", audit });
      } catch (error) {
        if (controller.signal.aborted) return;
        setState(
          isNotFound(error)
            ? { kind: "missing", bankRef }
            : { kind: "error", message: describeError(error) },
        );
      }
    }

    void load();
    return () => controller.abort();
  }, [bankRef, attempt]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Button variant="ghost" onClick={onBack}>
            ← Back
          </Button>
          <h2 className="text-sm font-semibold text-slate-900">Audit trail</h2>
        </div>
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            const value = draft.trim();
            if (value !== "") onLookup(value);
          }}
        >
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="bank_ref"
            aria-label="Bank reference"
            className="w-56 rounded-md border border-slate-300 px-2.5 py-1.5 font-mono text-xs text-slate-900 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 focus:outline-none"
          />
          <Button variant="primary" type="submit" disabled={draft.trim() === ""}>
            Look up
          </Button>
        </form>
      </div>

      {state.kind === "idle" && (
        <Panel>
          <EmptyState
            title="Enter a bank reference"
            detail="Every node, decision, review and model call that touched one statement line."
          />
        </Panel>
      )}

      {state.kind === "loading" && (
        <Panel>
          <Loading label="Loading audit trail…" />
        </Panel>
      )}

      {state.kind === "missing" && (
        <Panel>
          <EmptyState
            title={`No bank line with reference ${state.bankRef}`}
            detail="References are exact and tenant-scoped. Check the reference from the runs progress log or the exception queue."
          />
        </Panel>
      )}

      {state.kind === "error" && (
        <Panel>
          <ErrorState
            message={state.message}
            onRetry={() => setAttempt((value) => value + 1)}
          />
        </Panel>
      )}

      {state.kind === "ready" && <AuditBody audit={state.audit} />}
    </div>
  );
}

function AuditBody({ audit }: { audit: LineAudit }) {
  const { line } = audit;

  /** A decision is superseded when a later row points back at it. */
  const supersededIds = useMemo(
    () =>
      new Set(
        audit.decisions
          .map((decision) => decision.supersedes_id)
          .filter((id): id is number => id !== null),
      ),
    [audit.decisions],
  );

  const reviewsByDecision = useMemo(() => {
    const map = new Map<number, HumanReview[]>();
    for (const review of audit.human_reviews) {
      const bucket = map.get(review.decision_id);
      if (bucket === undefined) map.set(review.decision_id, [review]);
      else bucket.push(review);
    }
    return map;
  }, [audit.human_reviews]);

  return (
    <div className="space-y-6">
      <Panel>
        <PanelHeader
          title={<span className="font-mono">{line.bank_ref}</span>}
          subtitle="Bank statement line, as ingested"
          actions={<Direction minor={line.amount_minor} />}
        />
        <PanelBody className="space-y-5">
          <FieldGrid columns={4}>
            <Field label="Amount">
              <Money minor={line.amount_minor} currency={line.currency} size="lg" />
            </Field>
            <Field label="Value date">{formatDate(line.value_date)}</Field>
            <Field label="Booking date">{formatDate(line.booking_date)}</Field>
            <Field label="Currency" mono>
              {line.currency}
            </Field>
            <Field label="Counterparty">{line.counterparty ?? "—"}</Field>
            <Field label="Content hash" mono>
              <span title={line.content_hash}>{shortHash(line.content_hash, 16)}</span>
            </Field>
          </FieldGrid>
          <div>
            <p className="text-[11px] font-medium tracking-wide text-slate-500 uppercase">
              Narrative
            </p>
            <p className="mt-1 text-sm text-slate-800">{line.narrative || "—"}</p>
          </div>
        </PanelBody>
      </Panel>

      <Panel>
        <PanelHeader
          title="Decisions"
          subtitle="Ordered by the moment they were written."
          actions={
            <Tag tone="neutral">
              {formatInteger(audit.decisions.length)}{" "}
              {audit.decisions.length === 1 ? "row" : "rows"}
            </Tag>
          }
        />
        {audit.decisions.length === 0 ? (
          <EmptyState
            title="No decision recorded for this line"
            detail="It was ingested but no tier has committed or escalated it yet."
          />
        ) : (
          <PanelBody className="space-y-5">
            <Notice tone="info" title="This table is append-only">
              A decision is never edited or deleted — the database rejects both. A
              correction is written as a <em>new</em> row that supersedes the one before
              it, so the escalation the reviewer overruled is still here, struck through,
              next to what replaced it.
            </Notice>
            <ol className="relative space-y-4 border-l border-slate-200 pl-6">
              {audit.decisions.map((decision) => (
                <DecisionRow
                  key={decision.id}
                  decision={decision}
                  superseded={supersededIds.has(decision.id)}
                  reviews={reviewsByDecision.get(decision.id) ?? []}
                />
              ))}
            </ol>
          </PanelBody>
        )}
      </Panel>

      <Panel>
        <PanelHeader
          title="Human reviews"
          subtitle="Every reviewer action, with what it corrected the match to."
        />
        {audit.human_reviews.length === 0 ? (
          <EmptyState title="No human touched this line" />
        ) : (
          <div className="overflow-x-auto px-6 py-4">
            <table className="w-full text-left text-xs">
              <thead className="text-[11px] tracking-wide text-slate-500 uppercase">
                <tr className="border-b border-slate-200">
                  <th className="py-2 pr-4 font-medium">Reviewed</th>
                  <th className="py-2 pr-4 font-medium">Reviewer</th>
                  <th className="py-2 pr-4 font-medium">Action</th>
                  <th className="py-2 pr-4 font-medium">Corrected to</th>
                  <th className="py-2 pr-4 font-medium">Note</th>
                  <th className="py-2 font-medium">Written back</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {audit.human_reviews.map((review) => (
                  <tr key={review.id}>
                    <td className="py-2 pr-4 whitespace-nowrap text-slate-600">
                      {formatTimestamp(review.reviewed_at)}
                    </td>
                    <td className="py-2 pr-4 font-mono text-slate-800">
                      {review.reviewer}
                    </td>
                    <td className="py-2 pr-4">
                      <Tag tone={review.action === "reject" ? "danger" : "positive"}>
                        {review.action}
                      </Tag>
                    </td>
                    <td className="py-2 pr-4 font-mono text-slate-600">
                      {review.corrected_ledger_entry_ids.length === 0
                        ? "—"
                        : review.corrected_ledger_entry_ids.join(", ")}
                    </td>
                    <td className="py-2 pr-4 text-slate-700">{review.note ?? "—"}</td>
                    <td className="py-2 whitespace-nowrap text-slate-600">
                      {formatTimestamp(review.written_back_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel>
        <PanelHeader
          title="Model calls"
          subtitle="Recorded request hash, tokens, latency and cost — the basis for byte-identical replay."
          actions={
            <Tag tone="neutral">
              {formatInteger(audit.model_calls.length)}{" "}
              {audit.model_calls.length === 1 ? "call" : "calls"}
            </Tag>
          }
        />
        {audit.model_calls.length === 0 ? (
          <EmptyState
            title="No model call for this line"
            detail="The cascade resolved it deterministically — the LLM is the last resort, not the first."
          />
        ) : (
          <div className="divide-y divide-slate-100">
            {audit.model_calls.map((call) => (
              <ModelCallRow key={call.id} call={call} />
            ))}
          </div>
        )}
      </Panel>

      <Panel>
        <PanelHeader
          title="Event chain"
          subtitle="Every graph node of every run that touched this line, in order, each hashed over its predecessor."
        />
        {audit.events.length === 0 ? (
          <EmptyState title="No events recorded" />
        ) : (
          <ol className="divide-y divide-slate-100">
            {audit.events.map((event) => (
              <li
                key={`${event.run_id}-${event.seq}`}
                className="flex flex-wrap items-baseline gap-x-4 gap-y-1 px-6 py-2.5"
              >
                <span className="w-24 shrink-0 font-mono text-[11px] text-slate-400">
                  {shortId(event.run_id, 8)}
                </span>
                <span className="w-10 shrink-0 font-mono text-xs text-slate-400 tabular-nums">
                  #{event.seq}
                </span>
                <span className="flex-1 font-mono text-xs text-slate-800">
                  {event.node}
                </span>
                <span className="shrink-0 text-xs text-slate-400">
                  {formatTimestamp(event.created_at)}
                </span>
                <span
                  className="shrink-0 font-mono text-[11px] text-slate-400"
                  title={event.hash}
                >
                  {shortHash(event.hash)}
                </span>
              </li>
            ))}
          </ol>
        )}
      </Panel>
    </div>
  );
}

function DecisionRow({
  decision,
  superseded,
  reviews,
}: {
  decision: Decision;
  superseded: boolean;
  reviews: HumanReview[];
}) {
  return (
    <li className="relative">
      <span
        className={`absolute top-2 -left-[31px] h-2.5 w-2.5 rounded-full ring-4 ring-white ${
          superseded
            ? "bg-slate-300"
            : decision.auto_committed
              ? "bg-emerald-500"
              : "bg-indigo-500"
        }`}
      />
      <div
        className={`rounded-md border px-4 py-3 ${
          superseded
            ? "border-slate-200 bg-slate-50 opacity-70"
            : "border-slate-200 bg-white"
        }`}
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <TierBadge tier={decision.tier} />
          <span
            className={`font-mono text-sm font-medium ${
              superseded ? "text-slate-500 line-through" : "text-slate-900"
            }`}
          >
            {decision.decision}
          </span>
          {superseded && (
            <Tag tone="warning" title="Replaced by a later decision — kept on the record">
              superseded
            </Tag>
          )}
          {decision.supersedes_id !== null && (
            <Tag tone="accent" mono title="This row replaced an earlier decision">
              replaces #{decision.supersedes_id}
            </Tag>
          )}
          <Tag tone={decision.auto_committed ? "positive" : "neutral"}>
            {decision.auto_committed ? "auto-committed" : "not auto-committed"}
          </Tag>
          <span className="ml-auto flex items-center gap-3 text-xs text-slate-500">
            <span className="font-mono">
              confidence {formatConfidence(decision.confidence)}
            </span>
            <span>{formatTimestamp(decision.created_at)}</span>
          </span>
        </div>

        <p
          className={`mt-2 text-sm leading-relaxed ${
            superseded ? "text-slate-500" : "text-slate-800"
          }`}
        >
          {decision.rationale || "—"}
        </p>

        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] text-slate-400">matched</span>
          {decision.doc_refs.length === 0 ? (
            <span className="text-[11px] text-slate-400">nothing</span>
          ) : (
            decision.doc_refs.map((ref) => (
              <Tag key={ref} tone="neutral" mono>
                {ref}
              </Tag>
            ))
          )}
          {decision.ledger_entry_ids.length > 0 && (
            <span
              className="text-[11px] text-slate-400"
              title={decision.ledger_entry_ids.join(", ")}
            >
              · {formatInteger(decision.ledger_entry_ids.length)} ledger{" "}
              {decision.ledger_entry_ids.length === 1 ? "entry" : "entries"}
            </span>
          )}
          <span className="text-[11px] text-slate-400">
            · run <span className="font-mono">{shortId(decision.run_id)}</span>
          </span>
        </div>

        {decision.evidence.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {decision.evidence.map((entry, index) => (
              <Tag key={`${decision.id}-${index}`} tone="neutral" mono>
                {entry}
              </Tag>
            ))}
          </div>
        )}

        {reviews.map((review) => (
          <p key={review.id} className="mt-2 text-xs text-slate-600">
            Recorded by <span className="font-mono">{review.reviewer}</span> as{" "}
            <strong className="font-medium">{review.action}</strong>
            {review.note !== null && <> — {review.note}</>}
          </p>
        ))}
      </div>
    </li>
  );
}

function ModelCallRow({ call }: { call: ModelCall }) {
  const [open, setOpen] = useState(false);
  const response = useMemo(() => {
    try {
      return JSON.stringify(call.response, null, 2);
    } catch {
      return "unserialisable response";
    }
  }, [call.response]);

  return (
    <div className="px-6 py-3">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-xs">
        <span className="font-mono text-slate-800" title={call.request_hash}>
          {shortHash(call.request_hash, 16)}
        </span>
        <span className="text-slate-500">
          tokens{" "}
          <span className="font-mono text-slate-800 tabular-nums">
            {formatInteger(call.input_tokens)} in / {formatInteger(call.output_tokens)} out
          </span>
        </span>
        <span className="text-slate-500">
          latency{" "}
          <span className="font-mono text-slate-800 tabular-nums">
            {formatInteger(call.latency_ms)} ms
          </span>
        </span>
        <span className="text-slate-500">
          cost{" "}
          <span className="font-mono text-slate-800 tabular-nums">
            {formatMicro(call.cost_micro)}
          </span>
        </span>
        <span className="text-slate-400">{formatTimestamp(call.created_at)}</span>
        <span className="ml-auto flex items-center gap-2">
          <span className="font-mono text-[11px] text-slate-400">
            run {shortId(call.run_id)}
          </span>
          <Button variant="ghost" onClick={() => setOpen((value) => !value)}>
            {open ? "Hide response" : "Show response"}
          </Button>
        </span>
      </div>
      {open && (
        <pre className="mt-3 max-h-80 overflow-auto rounded-md bg-slate-900 px-4 py-3 font-mono text-[11px] leading-relaxed text-slate-100">
          {response}
        </pre>
      )}
    </div>
  );
}
