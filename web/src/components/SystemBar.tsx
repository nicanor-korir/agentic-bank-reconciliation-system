/**
 * The strip under the title: is the API up, and what is loaded for this tenant.
 *
 * It is deliberately the first thing that renders. If the API is down, the
 * operator learns it here instead of from an empty table.
 */

import { useEffect, useState } from "react";
import { API_URL, describeError, fetchHealth, fetchStats } from "../shared/api";
import type { Health, Stats } from "../shared/types";
import { formatInteger } from "../shared/format";

type SystemState =
  | { kind: "loading" }
  | { kind: "ok"; health: Health; stats: Stats | null }
  | { kind: "error"; message: string };

export function SystemBar() {
  const [state, setState] = useState<SystemState>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      try {
        const health = await fetchHealth(controller.signal);
        // Stats are secondary: a healthy API with an empty database is still
        // worth showing, so a failure here does not fail the whole bar.
        let stats: Stats | null = null;
        try {
          stats = await fetchStats(controller.signal);
        } catch {
          stats = null;
        }
        if (controller.signal.aborted) return;
        setState({ kind: "ok", health, stats });
      } catch (error) {
        if (controller.signal.aborted) return;
        setState({ kind: "error", message: describeError(error) });
      }
    }

    void load();
    return () => controller.abort();
  }, []);

  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-slate-500">
      <span className="inline-flex items-center gap-2">
        <span
          className={`h-1.5 w-1.5 rounded-full ${
            state.kind === "ok"
              ? "bg-emerald-500"
              : state.kind === "error"
                ? "bg-rose-500"
                : "bg-slate-300"
          }`}
        />
        <span className="font-mono text-slate-600">{API_URL}</span>
      </span>

      {state.kind === "loading" && <span>checking API…</span>}

      {state.kind === "error" && (
        <span className="font-medium text-rose-600">unreachable — {state.message}</span>
      )}

      {state.kind === "ok" && (
        <>
          <Stat label="service" value={`${state.health.service} v${state.health.version}`} />
          {state.stats !== null && (
            <>
              <Stat label="tenant" value={state.stats.tenant} />
              <Stat label="bank lines" value={formatInteger(state.stats.bank_lines)} />
              <Stat
                label="ledger entries"
                value={`${formatInteger(state.stats.open_ledger_entries)} open / ${formatInteger(
                  state.stats.ledger_entries,
                )}`}
              />
            </>
          )}
        </>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="text-slate-400">{label}</span>
      <span className="font-mono text-slate-700">{value}</span>
    </span>
  );
}
