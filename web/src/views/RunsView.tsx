/**
 * Runs list — the landing view.
 *
 * One row per reconciliation run, with the provenance a technical buyer asks
 * for first: which adjudicator produced it, and which commit it ran from.
 */

import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { createRun, describeError, fetchRuns } from "../lib/api";
import type { RunSummary } from "../lib/types";
import { formatInteger, formatMicro, formatTimestamp, shortId } from "../lib/format";
import { GitSha, StatusBadge, Tag } from "../components/Badge";
import { Button, Panel, PanelHeader, SectionTitle } from "../components/Panel";
import { EmptyState, ErrorState, Loading, Notice, StubWarning } from "../components/States";

type ListState =
  | { kind: "loading" }
  | { kind: "ready"; runs: RunSummary[] }
  | { kind: "error"; message: string };

const ADJUDICATORS = ["auto", "anthropic", "stub"] as const;

export function RunsView({ onOpenRun }: { onOpenRun: (runId: string) => void }) {
  const [state, setState] = useState<ListState>({ kind: "loading" });
  const [reloadToken, setReloadToken] = useState(0);
  const [period, setPeriod] = useState("2026-06");
  const [adjudicator, setAdjudicator] = useState<string>("auto");
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    async function load(initial: boolean) {
      try {
        const runs = await fetchRuns(controller.signal);
        if (cancelled) return;
        setState({ kind: "ready", runs });
      } catch (error) {
        if (cancelled || controller.signal.aborted) return;
        // A refresh that fails should not blank a table that is already there.
        if (initial) setState({ kind: "error", message: describeError(error) });
      }
    }

    void load(true);
    // Runs execute on a worker thread; polling keeps the list honest while one
    // is in flight without holding a socket open for a page that is mostly idle.
    const timer = window.setInterval(() => void load(false), 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      controller.abort();
    };
  }, [reloadToken]);

  const start = useCallback(async () => {
    setStarting(true);
    setStartError(null);
    try {
      const created = await createRun(period.trim(), adjudicator);
      onOpenRun(created.run_id);
    } catch (error) {
      setStartError(describeError(error));
    } finally {
      setStarting(false);
    }
  }, [adjudicator, onOpenRun, period]);

  const runs = state.kind === "ready" ? state.runs : [];
  const hasStubRun = runs.some((run) => run.adjudicator === "stub");

  return (
    <div className="space-y-6">
      <Panel>
        <PanelHeader
          title="Start a run"
          subtitle="Reconciles one period of bank lines against the open ledger."
        />
        <div className="flex flex-wrap items-end gap-4 px-6 py-5">
          <label className="block">
            <span className="mb-1 block text-[11px] font-medium tracking-wide text-slate-500 uppercase">
              Period
            </span>
            <input
              value={period}
              onChange={(event) => setPeriod(event.target.value)}
              placeholder="2026-06"
              className="w-36 rounded-md border border-slate-300 px-2.5 py-1.5 font-mono text-sm text-slate-900 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 focus:outline-none"
            />
          </label>

          <label className="block">
            <span className="mb-1 block text-[11px] font-medium tracking-wide text-slate-500 uppercase">
              Adjudicator
            </span>
            <select
              value={adjudicator}
              onChange={(event) => setAdjudicator(event.target.value)}
              className="w-44 rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-sm text-slate-900 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 focus:outline-none"
            >
              {ADJUDICATORS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>

          <Button variant="primary" onClick={() => void start()} disabled={starting}>
            {starting ? "Starting…" : "New run"}
          </Button>

          <p className="max-w-sm text-xs leading-relaxed text-slate-500">
            <span className="font-mono">auto</span> uses the Anthropic adjudicator when an
            API key is configured and falls back to the stub when it is not.
          </p>
        </div>

        {startError !== null && (
          <div className="px-6 pb-5">
            <Notice tone="danger" title="Could not start the run">
              <span className="font-mono">{startError}</span>
            </Notice>
          </div>
        )}
      </Panel>

      {hasStubRun && <StubWarning />}

      <Panel>
        <PanelHeader
          title="Runs"
          subtitle={
            state.kind === "ready"
              ? `${formatInteger(runs.length)} most recent`
              : "Loading history"
          }
          actions={
            <Button onClick={() => setReloadToken((token) => token + 1)}>Refresh</Button>
          }
        />

        {state.kind === "loading" && <Loading label="Loading runs…" />}

        {state.kind === "error" && (
          <ErrorState
            message={state.message}
            onRetry={() => setReloadToken((token) => token + 1)}
          />
        )}

        {state.kind === "ready" && runs.length === 0 && (
          <EmptyState
            title="No runs yet"
            detail="Start one above. The first run ingests the period and walks the full cascade."
          />
        )}

        {state.kind === "ready" && runs.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b border-slate-200 text-[11px] tracking-wide text-slate-500 uppercase">
                  <Th>Run</Th>
                  <Th>Started</Th>
                  <Th>Status</Th>
                  <Th>Adjudicator</Th>
                  <Th align="right">Auto-committed</Th>
                  <Th align="right">Escalated</Th>
                  <Th align="right">Cost</Th>
                  <Th>Commit</Th>
                  <Th />
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {runs.map((run) => (
                  <tr
                    key={run.id}
                    onClick={() => onOpenRun(run.id)}
                    className="cursor-pointer transition-colors hover:bg-slate-50"
                  >
                    <Td>
                      <span className="font-mono text-xs text-slate-900">
                        {shortId(run.id)}
                      </span>
                      {run.replay_of !== null && (
                        <span className="ml-2">
                          <Tag tone="accent" title={`Replay of ${run.replay_of}`}>
                            replay
                          </Tag>
                        </span>
                      )}
                    </Td>
                    <Td>
                      <span className="text-xs whitespace-nowrap text-slate-600">
                        {formatTimestamp(run.started_at)}
                      </span>
                    </Td>
                    <Td>
                      <StatusBadge status={run.status} />
                    </Td>
                    <Td>
                      {run.adjudicator === "stub" ? (
                        <Tag tone="warning" mono title="Not a model — fixtures only">
                          stub
                        </Tag>
                      ) : (
                        <span className="font-mono text-xs text-slate-600">
                          {run.adjudicator ?? "—"}
                        </span>
                      )}
                    </Td>
                    <Td align="right">
                      <span className="font-mono text-xs tabular-nums text-slate-900">
                        {formatInteger(run.auto_committed)}
                      </span>
                    </Td>
                    <Td align="right">
                      <span
                        className={`font-mono text-xs tabular-nums ${
                          run.escalated > 0 ? "text-indigo-700" : "text-slate-400"
                        }`}
                      >
                        {formatInteger(run.escalated)}
                      </span>
                    </Td>
                    <Td align="right">
                      <span className="font-mono text-xs tabular-nums text-slate-600">
                        {formatMicro(run.cost_total_micro)}
                      </span>
                    </Td>
                    <Td>
                      <GitSha sha={run.git_sha} dirty={run.git_dirty} />
                    </Td>
                    <Td align="right">
                      <span className="text-xs font-medium text-indigo-600">Open →</span>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel className="bg-slate-50/60">
        <div className="px-6 py-5">
          <SectionTitle>How to read this</SectionTitle>
          <p className="max-w-3xl text-xs leading-relaxed text-slate-600">
            <strong>Auto-committed</strong> counts decisions the system was willing to
            defend on its own. <strong>Escalated</strong> counts the residue routed to a
            human. A <span className="font-mono">dirty</span> marker on the commit means
            the run was produced from an uncommitted working tree, so a replay of it is
            not guaranteed to reproduce byte-identical decisions.
          </p>
        </div>
      </Panel>
    </div>
  );
}

function Th({
  children,
  align = "left",
}: {
  children?: ReactNode;
  align?: "left" | "right";
}) {
  return (
    <th
      scope="col"
      className={`px-6 py-2.5 font-medium ${align === "right" ? "text-right" : "text-left"}`}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  align = "left",
}: {
  children?: ReactNode;
  align?: "left" | "right";
}) {
  return (
    <td className={`px-6 py-3 ${align === "right" ? "text-right" : "text-left"}`}>
      {children}
    </td>
  );
}
