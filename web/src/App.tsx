/**
 * View switcher.
 *
 * Four views, one piece of state. No router: this is a single-operator demo
 * tool, and a URL scheme would be more machinery than the whole app needs.
 */

import { useCallback, useState } from "react";
import { SystemBar } from "./components/SystemBar";
import { RunsView } from "./views/RunsView";
import { RunDetailView } from "./views/RunDetailView";
import { QueueView } from "./views/QueueView";
import { AuditView } from "./views/AuditView";
import { shortId } from "./shared/format";

type View =
  | { kind: "runs" }
  | { kind: "run"; runId: string }
  | { kind: "queue"; runId: string }
  /** `runId` is kept so "back" returns to where the drill-down was opened. */
  | { kind: "audit"; bankRef: string; runId: string | null };

export default function App() {
  const [view, setView] = useState<View>({ kind: "runs" });

  const openRuns = useCallback(() => setView({ kind: "runs" }), []);
  const openRun = useCallback((runId: string) => setView({ kind: "run", runId }), []);
  const openQueue = useCallback((runId: string) => setView({ kind: "queue", runId }), []);

  const openAudit = useCallback(
    (bankRef: string) =>
      setView((current) => ({
        kind: "audit",
        bankRef,
        runId:
          current.kind === "run" || current.kind === "queue"
            ? current.runId
            : current.kind === "audit"
              ? current.runId
              : null,
      })),
    [],
  );

  const leaveAudit = useCallback(
    (runId: string | null) => (runId === null ? openRuns() : openRun(runId)),
    [openRun, openRuns],
  );

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-7xl px-6 py-5">
          <div className="flex flex-wrap items-baseline justify-between gap-4">
            <div>
              <h1 className="text-base font-semibold tracking-tight">
                Bank reconciliation
              </h1>
              <p className="mt-0.5 text-xs text-slate-500">
                Deterministic tiers auto-commit what they can defend. Everything else
                comes here.
              </p>
            </div>
            <Breadcrumbs view={view} onOpenRuns={openRuns} onOpenRun={openRun} />
          </div>
          <div className="mt-4">
            <SystemBar />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8">
        {view.kind === "runs" && <RunsView onOpenRun={openRun} />}

        {view.kind === "run" && (
          <RunDetailView
            key={view.runId}
            runId={view.runId}
            onBack={openRuns}
            onOpenQueue={openQueue}
            onOpenAudit={openAudit}
          />
        )}

        {view.kind === "queue" && (
          <QueueView
            key={view.runId}
            runId={view.runId}
            onBack={() => openRun(view.runId)}
            onOpenAudit={openAudit}
          />
        )}

        {view.kind === "audit" && (
          <AuditView
            bankRef={view.bankRef}
            onBack={() => leaveAudit(view.runId)}
            onLookup={openAudit}
          />
        )}
      </main>

      <footer className="mx-auto max-w-7xl px-6 pb-10">
        <p className="border-t border-slate-200 pt-4 text-xs leading-relaxed text-slate-400">
          The system proposes matches and writes only to its own tables — it never mutates
          the ledger. Decisions are append-only and hash-chained; a correction supersedes,
          it does not overwrite.
        </p>
      </footer>
    </div>
  );
}

function Breadcrumbs({
  view,
  onOpenRuns,
  onOpenRun,
}: {
  view: View;
  onOpenRuns: () => void;
  onOpenRun: (runId: string) => void;
}) {
  const crumbs: { label: string; onClick?: () => void }[] = [
    { label: "Runs", onClick: view.kind === "runs" ? undefined : onOpenRuns },
  ];

  if (view.kind === "run") {
    crumbs.push({ label: shortId(view.runId) });
  }
  if (view.kind === "queue") {
    crumbs.push({ label: shortId(view.runId), onClick: () => onOpenRun(view.runId) });
    crumbs.push({ label: "Exception queue" });
  }
  if (view.kind === "audit") {
    if (view.runId !== null) {
      const runId = view.runId;
      crumbs.push({ label: shortId(runId), onClick: () => onOpenRun(runId) });
    }
    crumbs.push({ label: `Audit ${view.bankRef || "—"}` });
  }

  return (
    <nav aria-label="Breadcrumb" className="flex items-center gap-2 text-xs">
      {crumbs.map((crumb, index) => (
        <span key={`${crumb.label}-${index}`} className="flex items-center gap-2">
          {index > 0 && <span className="text-slate-300">/</span>}
          {crumb.onClick === undefined ? (
            <span className="font-medium text-slate-700">{crumb.label}</span>
          ) : (
            <button
              type="button"
              onClick={crumb.onClick}
              className="text-indigo-600 hover:text-indigo-800 hover:underline"
            >
              {crumb.label}
            </button>
          )}
        </span>
      ))}
    </nav>
  );
}
