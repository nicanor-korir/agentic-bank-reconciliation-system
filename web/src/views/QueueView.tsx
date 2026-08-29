/**
 * Exception queue — the residue the system would not commit on its own.
 *
 * Decisions accumulate locally and are submitted in a single POST, because the
 * graph is paused on one interrupt for the whole batch: resuming it once with
 * every resolution is what the checkpoint expects.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { describeError, fetchQueue, submitResolutions } from "../shared/api";
import type {
  Candidate,
  Queue,
  QueueItem,
  Resolution,
  ResolutionAction,
  ResolveSummary,
} from "../shared/types";
import {
  formatConfidence,
  formatDate,
  formatInteger,
  pluralise,
  shortId,
} from "../shared/format";
import { StatusBadge, Tag } from "../components/Badge";
import { Button, Panel, PanelBody, PanelHeader, SectionTitle } from "../components/Panel";
import { EmptyState, ErrorState, Loading, Notice } from "../components/States";
import { Difference, Direction, Money } from "../components/Money";

type QueueState =
  | { kind: "loading" }
  | { kind: "ready"; queue: Queue }
  | { kind: "error"; message: string };

type Decided = {
  action: ResolutionAction;
  /** null for a rejection: no candidate was right. */
  candidateId: string | null;
};

export function QueueView({
  runId,
  onBack,
  onOpenAudit,
}: {
  runId: string;
  onBack: () => void;
  onOpenAudit: (bankRef: string) => void;
}) {
  const [state, setState] = useState<QueueState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [decided, setDecided] = useState<Record<string, Decided>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [reviewer, setReviewer] = useState("reviewer");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [result, setResult] = useState<ResolveSummary | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      setState({ kind: "loading" });
      try {
        const queue = await fetchQueue(runId, controller.signal);
        if (controller.signal.aborted) return;
        setState({ kind: "ready", queue });
      } catch (error) {
        if (controller.signal.aborted) return;
        setState({ kind: "error", message: describeError(error) });
      }
    }

    void load();
    return () => controller.abort();
  }, [runId, reloadToken]);

  const reload = useCallback(() => {
    setSelected({});
    setDecided({});
    setNotes({});
    setSubmitError(null);
    // Clearing the result unlocks the cards: a resolve may leave a fresh queue.
    setResult(null);
    setReloadToken((token) => token + 1);
  }, []);

  const items = state.kind === "ready" ? state.queue.items : [];
  const decidedCount = useMemo(
    () => items.filter((item) => decided[item.bank_ref] !== undefined).length,
    [decided, items],
  );

  const select = useCallback((bankRef: string, candidateId: string) => {
    setSelected((current) => ({ ...current, [bankRef]: candidateId }));
    // Picking a different candidate invalidates a decision already recorded.
    setDecided((current) => {
      if (current[bankRef] === undefined) return current;
      const next = { ...current };
      delete next[bankRef];
      return next;
    });
  }, []);

  const decide = useCallback(
    (bankRef: string, action: ResolutionAction, candidateId: string | null) => {
      setDecided((current) => ({ ...current, [bankRef]: { action, candidateId } }));
    },
    [],
  );

  const undecide = useCallback((bankRef: string) => {
    setDecided((current) => {
      const next = { ...current };
      delete next[bankRef];
      return next;
    });
  }, []);

  const submit = useCallback(async () => {
    const resolutions: Resolution[] = [];
    for (const item of items) {
      const decision = decided[item.bank_ref];
      if (decision === undefined) continue;
      const candidate =
        decision.candidateId === null
          ? undefined
          : item.candidates.find((entry) => entry.id === decision.candidateId);
      const note = (notes[item.bank_ref] ?? "").trim();
      const resolution: Resolution = {
        bank_ref: item.bank_ref,
        action: decision.action,
        ledger_entry_ids: candidate?.ledger_entry_ids ?? [],
        doc_refs: candidate?.doc_refs ?? [],
        reviewer: reviewer.trim() === "" ? "reviewer" : reviewer.trim(),
      };
      if (note !== "") resolution.note = note;
      resolutions.push(resolution);
    }

    if (resolutions.length === 0) return;

    setSubmitting(true);
    setSubmitError(null);
    try {
      const summary = await submitResolutions(runId, resolutions);
      setResult(summary);
    } catch (error) {
      setSubmitError(describeError(error));
    } finally {
      setSubmitting(false);
    }
  }, [decided, items, notes, reviewer, runId]);

  return (
    <div className="space-y-6 pb-24">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Button variant="ghost" onClick={onBack}>
            ← Run
          </Button>
          <h2 className="text-sm font-semibold text-slate-900">
            Exception queue{" "}
            <span className="font-mono text-xs font-normal text-slate-500">
              {shortId(runId, 12)}
            </span>
          </h2>
        </div>
        <Button onClick={reload}>Reload queue</Button>
      </div>

      {result !== null && <ResolveResult summary={result} onReload={reload} />}

      {state.kind === "loading" && (
        <Panel>
          <Loading label="Loading exception queue…" />
        </Panel>
      )}

      {state.kind === "error" && (
        <Panel>
          <ErrorState message={state.message} onRetry={reload} />
        </Panel>
      )}

      {state.kind === "ready" && items.length === 0 && (
        <Panel>
          <EmptyState
            title="Nothing waiting for a reviewer"
            detail="This run has no open interrupt. Either every line was auto-committed, the run has not reached human review yet, or its queue was already resolved."
            action={<Button onClick={onBack}>Back to the run</Button>}
          />
        </Panel>
      )}

      {state.kind === "ready" && items.length > 0 && (
        <>
          <Notice tone="info" title={`${formatInteger(items.length)} exceptions`}>
            Each line below is one the cascade would not commit on its own. Pick the
            candidate that settles it and approve, choose a different candidate to
            reassign, or reject when none of them is right. Nothing is sent until you
            submit.
          </Notice>

          <div className="space-y-5">
            {items.map((item) => (
              <ExceptionCard
                key={item.bank_ref}
                item={item}
                selectedId={selected[item.bank_ref] ?? null}
                decision={decided[item.bank_ref] ?? null}
                note={notes[item.bank_ref] ?? ""}
                locked={result !== null}
                onSelect={(candidateId) => select(item.bank_ref, candidateId)}
                onDecide={(action, candidateId) =>
                  decide(item.bank_ref, action, candidateId)
                }
                onUndecide={() => undecide(item.bank_ref)}
                onNote={(value) =>
                  setNotes((current) => ({ ...current, [item.bank_ref]: value }))
                }
                onOpenAudit={() => onOpenAudit(item.bank_ref)}
              />
            ))}
          </div>

          <SubmitBar
            decidedCount={decidedCount}
            total={items.length}
            reviewer={reviewer}
            onReviewer={setReviewer}
            submitting={submitting}
            submitted={result !== null}
            error={submitError}
            onSubmit={() => void submit()}
          />
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------- card ---

function ExceptionCard({
  item,
  selectedId,
  decision,
  note,
  locked,
  onSelect,
  onDecide,
  onUndecide,
  onNote,
  onOpenAudit,
}: {
  item: QueueItem;
  selectedId: string | null;
  decision: Decided | null;
  note: string;
  locked: boolean;
  onSelect: (candidateId: string) => void;
  onDecide: (action: ResolutionAction, candidateId: string | null) => void;
  onUndecide: () => void;
  onNote: (value: string) => void;
  onOpenAudit: () => void;
}) {
  const topId = item.candidates.length > 0 ? item.candidates[0]?.id ?? null : null;
  const isTopSelected = selectedId !== null && selectedId === topId;
  const canApprove = !locked && isTopSelected;
  const canReassign = !locked && selectedId !== null && !isTopSelected;

  return (
    <Panel
      className={
        decision !== null ? "ring-2 ring-indigo-200 ring-offset-0" : undefined
      }
    >
      <PanelHeader
        title={
          <span className="flex flex-wrap items-baseline gap-3">
            <span className="font-mono text-sm text-slate-900">{item.bank_ref}</span>
            <span className="text-xs font-normal text-slate-500">
              {formatDate(item.value_date)}
            </span>
            <Money minor={item.amount_minor} currency={item.currency} size="base" />
            <Direction minor={item.amount_minor} />
          </span>
        }
        subtitle={
          <span className="block max-w-3xl">
            <span className="text-slate-700">{item.narrative || "—"}</span>
            {item.counterparty !== null && (
              <span className="text-slate-400"> · {item.counterparty}</span>
            )}
          </span>
        }
        actions={
          <>
            {decision !== null && (
              <Tag tone={decision.action === "reject" ? "danger" : "positive"}>
                {decision.action}
                {decision.candidateId !== null ? ` ${decision.candidateId}` : ""}
              </Tag>
            )}
            <Button variant="ghost" onClick={onOpenAudit}>
              Audit
            </Button>
          </>
        }
      />

      <PanelBody className="space-y-5">
        <AgentReasoning item={item} />

        <div>
          <SectionTitle
            hint={
              item.candidates.length > 0
                ? `ranked ${item.candidates.length === 1 ? "candidate" : "candidates"}`
                : undefined
            }
          >
            Candidates
          </SectionTitle>

          {item.candidates.length === 0 ? (
            <p className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-xs text-slate-600">
              Retrieval offered nothing for this line. Rejecting records that plainly,
              and the line stays unmatched.
            </p>
          ) : (
            <ul className="space-y-3">
              {item.candidates.map((candidate, index) => (
                <li key={candidate.id}>
                  <CandidateCard
                    candidate={candidate}
                    rank={index + 1}
                    currency={item.currency}
                    selected={selectedId === candidate.id}
                    disabled={locked}
                    onSelect={() => onSelect(candidate.id)}
                  />
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="border-t border-slate-100 pt-4">
          {decision === null ? (
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="positive"
                disabled={!canApprove}
                onClick={() => onDecide("approve", selectedId)}
                title={
                  canApprove
                    ? undefined
                    : "Select the top-ranked candidate to approve it as offered."
                }
              >
                Approve {topId !== null ? topId : ""}
              </Button>
              <Button
                variant="primary"
                disabled={!canReassign}
                onClick={() => onDecide("reassign", selectedId)}
                title={
                  canReassign
                    ? undefined
                    : "Select a candidate other than the top-ranked one to reassign."
                }
              >
                Reassign{selectedId !== null && !isTopSelected ? ` to ${selectedId}` : ""}
              </Button>
              <Button
                variant="danger"
                disabled={locked}
                onClick={() => onDecide("reject", null)}
              >
                Reject — none of these
              </Button>
              <span className="text-xs text-slate-400">
                Approving accepts the ranking; reassigning overrides it. Both write the
                same match, and the audit trail records which one you chose.
              </span>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-3">
              <p className="text-xs text-slate-600">
                Recorded as <strong className="font-medium">{decision.action}</strong>
                {decision.candidateId !== null && (
                  <>
                    {" "}
                    on <span className="font-mono">{decision.candidateId}</span>
                  </>
                )}
                . It will be sent when you submit the batch.
              </p>
              {!locked && (
                <Button variant="ghost" onClick={onUndecide}>
                  Undo
                </Button>
              )}
            </div>
          )}

          <label className="mt-3 block">
            <span className="mb-1 block text-[11px] font-medium tracking-wide text-slate-500 uppercase">
              Note (optional)
            </span>
            <input
              value={note}
              disabled={locked}
              onChange={(event) => onNote(event.target.value)}
              placeholder="Why this call was made — stored on the decision."
              className="w-full max-w-2xl rounded-md border border-slate-300 px-2.5 py-1.5 text-sm text-slate-900 placeholder:text-slate-400 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 focus:outline-none disabled:bg-slate-50"
            />
          </label>
        </div>
      </PanelBody>
    </Panel>
  );
}

/** What the adjudicator said, or why it never got to say anything. */
function AgentReasoning({ item }: { item: QueueItem }) {
  if (item.reason !== null) {
    return (
      <Notice tone="danger" title="Adjudication failed for this line">
        <span className="font-mono">{item.reason}</span>
        <span className="mt-1 block">
          No model decision exists, so this line was escalated on the failure itself. The
          candidates below come from retrieval, unranked by any model judgement.
        </span>
      </Notice>
    );
  }

  if (item.rationale === null && item.decision === null) {
    return (
      <Notice tone="info" title="Escalated without a model decision">
        The cascade routed this line to review before adjudication produced a committable
        answer.
      </Notice>
    );
  }

  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-medium tracking-wide text-slate-500 uppercase">
          Agent recommendation
        </span>
        {item.decision !== null && (
          <Tag tone="accent" mono>
            {item.decision}
          </Tag>
        )}
        {item.confidence !== null && (
          <Tag tone="neutral" mono title="Below the auto-commit threshold">
            confidence {formatConfidence(item.confidence)}
          </Tag>
        )}
      </div>
      {item.rationale !== null && (
        <p className="mt-2 text-sm leading-relaxed text-slate-700">{item.rationale}</p>
      )}
      {item.evidence.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {item.evidence.map((entry) => (
            <Tag key={entry} tone="neutral" mono>
              {entry}
            </Tag>
          ))}
        </div>
      )}
    </div>
  );
}

function CandidateCard({
  candidate,
  rank,
  currency,
  selected,
  disabled,
  onSelect,
}: {
  candidate: Candidate;
  rank: number;
  currency: string;
  selected: boolean;
  disabled: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={disabled}
      aria-pressed={selected}
      // The visible label is a layout of amounts and tags, none of which reads
      // as a name. Without this the whole candidate list announces as a row of
      // unnamed buttons, which is exactly the choice a reviewer has to make.
      aria-label={
        `Candidate ${candidate.id}, rank ${rank}` +
        (candidate.kind === "subset"
          ? `, combination of ${candidate.ledger_entry_ids.length} items`
          : ", single item") +
        (candidate.doc_refs.length ? `, ${candidate.doc_refs.join(", ")}` : "") +
        `, ${candidate.difference_minor === 0 ? "exact match" : "amount differs"}`
      }
      className={`block w-full rounded-md border px-4 py-3 text-left transition-colors ${
        selected
          ? "border-indigo-400 bg-indigo-50/40 ring-1 ring-indigo-300"
          : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50"
      } disabled:cursor-not-allowed disabled:opacity-60`}
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span
          className={`inline-flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-semibold ${
            rank === 1 ? "bg-indigo-600 text-white" : "bg-slate-200 text-slate-600"
          }`}
          title={rank === 1 ? "Top-ranked candidate" : `Rank ${rank}`}
        >
          {rank}
        </span>
        <span className="font-mono text-xs font-medium text-slate-900">
          {candidate.id}
        </span>

        {candidate.kind === "subset" ? (
          <Tag
            tone="accent"
            title="One bank line settling several ledger entries at once"
          >
            subset · settles {candidate.ledger_entry_ids.length}{" "}
            {pluralise(candidate.ledger_entry_ids.length, "entry", "entries")}
          </Tag>
        ) : (
          <Tag tone="neutral">single</Tag>
        )}

        <span className="ml-auto flex items-center gap-3">
          <Difference minor={candidate.difference_minor} currency={currency} />
          <Money minor={candidate.total_minor} currency={currency} size="sm" />
        </span>
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {candidate.doc_refs.length === 0 ? (
          <span className="text-[11px] text-slate-400">no document references</span>
        ) : (
          candidate.doc_refs.map((ref) => (
            <Tag key={ref} tone="neutral" mono>
              {ref}
            </Tag>
          ))
        )}
        {candidate.found_by.length > 0 && (
          <span className="ml-2 flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] text-slate-400">found by</span>
            {candidate.found_by.map((source) => (
              <Tag key={source} tone="positive" mono>
                {source}
              </Tag>
            ))}
          </span>
        )}
      </div>

      {candidate.items.length > 0 && (
        <table className="mt-3 w-full text-left text-xs">
          <tbody className="divide-y divide-slate-100">
            {candidate.items.map((line, index) => (
              <tr key={`${candidate.id}-${line.doc_ref ?? index}`}>
                <td className="w-28 py-1.5 pr-3 font-mono text-slate-600">
                  {line.doc_ref ?? "—"}
                </td>
                <td className="py-1.5 pr-3 text-slate-700">{line.description}</td>
                <td className="w-48 py-1.5 pr-3 text-slate-500">
                  {line.counterparty ?? "—"}
                </td>
                <td className="w-32 py-1.5 text-right">
                  <Money minor={line.amount_minor} currency={currency} size="sm" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </button>
  );
}

// ------------------------------------------------------------------ chrome ---

function SubmitBar({
  decidedCount,
  total,
  reviewer,
  onReviewer,
  submitting,
  submitted,
  error,
  onSubmit,
}: {
  decidedCount: number;
  total: number;
  reviewer: string;
  onReviewer: (value: string) => void;
  submitting: boolean;
  submitted: boolean;
  error: string | null;
  onSubmit: () => void;
}) {
  return (
    <div className="sticky bottom-0 z-10 -mx-6 border-t border-slate-200 bg-white/95 px-6 py-3 backdrop-blur">
      {error !== null && (
        <div className="mb-3">
          <Notice tone="danger" title="Submitting the batch failed">
            <span className="font-mono">{error}</span>
          </Notice>
        </div>
      )}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <p className="text-sm text-slate-700">
          <span className="font-mono font-medium tabular-nums">
            {formatInteger(decidedCount)}
          </span>{" "}
          of{" "}
          <span className="font-mono tabular-nums">{formatInteger(total)}</span> decided
          <span className="ml-2 text-xs text-slate-500">
            submitted together in one call, which resumes the graph from its checkpoint
          </span>
        </p>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2">
            <span className="text-[11px] font-medium tracking-wide text-slate-500 uppercase">
              Reviewer
            </span>
            <input
              value={reviewer}
              disabled={submitted}
              onChange={(event) => onReviewer(event.target.value)}
              className="w-40 rounded-md border border-slate-300 px-2.5 py-1.5 font-mono text-xs text-slate-900 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 focus:outline-none disabled:bg-slate-50"
            />
          </label>
          <Button
            variant="primary"
            disabled={decidedCount === 0 || submitting || submitted}
            onClick={onSubmit}
          >
            {submitting
              ? "Submitting…"
              : submitted
                ? "Submitted"
                : `Submit ${formatInteger(decidedCount)} ${pluralise(decidedCount, "resolution")}`}
          </Button>
        </div>
      </div>
    </div>
  );
}

function ResolveResult({
  summary,
  onReload,
}: {
  summary: ResolveSummary;
  onReload: () => void;
}) {
  return (
    <Panel className="border-emerald-200">
      <PanelHeader
        title="Batch applied"
        subtitle="The graph resumed from its checkpoint and committed your decisions."
        actions={
          <>
            <StatusBadge status={summary.status} />
            <Button onClick={onReload}>Reload queue</Button>
          </>
        }
      />
      <PanelBody className="space-y-4">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-6">
          <Metric label="Bank lines" value={formatInteger(summary.bank_lines)} />
          <Metric label="Committed" value={formatInteger(summary.committed)} />
          <Metric label="Still queued" value={formatInteger(summary.queued_for_human)} />
          <Metric label="Unresolved" value={formatInteger(summary.unresolved)} />
          <Metric label="Model calls" value={formatInteger(summary.llm_calls)} />
          <Metric label="Cost" value={summary.cost_display} />
        </dl>

        {summary.halt_reason !== null && (
          <Notice tone="warning" title="The run halted">
            <span className="font-mono">{summary.halt_reason}</span>
          </Notice>
        )}

        <Notice
          tone={summary.written_back > 0 ? "positive" : "info"}
          title={`${formatInteger(summary.written_back)} ${pluralise(
            summary.written_back,
            "correction",
          )} written back to retrieval`}
        >
          Those corrections are now retrieval history: the next run searches them
          alongside the ledger, so a match you confirmed today is a candidate the system
          finds on its own tomorrow. Rejections are deliberately not written back — "none
          of these" is useful to a person reading the audit trail and misleading as
          retrieval history.
        </Notice>

        {summary.write_back_error !== null && (
          <Notice tone="warning" title="Write-back did not complete">
            <span className="font-mono">{summary.write_back_error}</span>
            <span className="mt-1 block">
              Your decisions are committed regardless — write-back is best effort by
              design, so a retrieval failure costs future recall, never accuracy.
            </span>
          </Notice>
        )}
      </PanelBody>
    </Panel>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[11px] font-medium tracking-wide text-slate-500 uppercase">
        {label}
      </dt>
      <dd className="mt-1 font-mono text-sm tabular-nums text-slate-900">{value}</dd>
    </div>
  );
}
