import { useEffect, useState } from "react";
import type { ReactNode } from "react";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

/** Shape of `GET /health` on the API. */
type Health = {
  status: string;
  service: string;
  version: string;
};

type State =
  | { kind: "loading" }
  | { kind: "ok"; health: Health }
  | { kind: "error"; message: string };

function isHealth(value: unknown): value is Health {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.status === "string" &&
    typeof candidate.service === "string" &&
    typeof candidate.version === "string"
  );
}

export default function App() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();

    async function check() {
      try {
        const response = await fetch(`${API_URL}/health`, {
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const body: unknown = await response.json();
        if (!isHealth(body)) {
          throw new Error("unexpected response shape");
        }
        setState({ kind: "ok", health: body });
      } catch (error) {
        if (controller.signal.aborted) return;
        setState({
          kind: "error",
          message: error instanceof Error ? error.message : "unknown error",
        });
      }
    }

    void check();
    return () => controller.abort();
  }, []);

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 p-6 text-slate-900">
      <section className="w-full max-w-md rounded-lg border border-slate-200 bg-white p-8 shadow-sm">
        <h1 className="text-lg font-semibold tracking-tight">Reconciliation</h1>
        <p className="mt-1 text-sm text-slate-500">API connectivity check</p>

        <dl className="mt-6 space-y-3 text-sm">
          <Row label="API">
            <span className="font-mono text-slate-500">{API_URL}</span>
          </Row>

          {state.kind === "loading" && (
            <Row label="Status">
              <span className="font-mono text-slate-500">checking…</span>
            </Row>
          )}

          {state.kind === "error" && (
            <>
              <Row label="Status">
                <span className="font-mono font-medium text-red-600">
                  unreachable
                </span>
              </Row>
              <Row label="Detail">
                <span className="font-mono text-slate-500">{state.message}</span>
              </Row>
            </>
          )}

          {state.kind === "ok" && (
            <>
              <Row label="Status">
                <span className="font-mono font-medium text-emerald-600">
                  {state.health.status}
                </span>
              </Row>
              <Row label="Service">
                <span className="font-mono">{state.health.service}</span>
              </Row>
              <Row label="Version">
                <span className="font-mono">{state.health.version}</span>
              </Row>
            </>
          )}
        </dl>
      </section>
    </main>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-slate-100 pb-2 last:border-0 last:pb-0">
      <dt className="text-slate-500">{label}</dt>
      <dd className="truncate">{children}</dd>
    </div>
  );
}
