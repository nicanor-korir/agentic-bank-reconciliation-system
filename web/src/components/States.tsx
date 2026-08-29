/** Loading / empty / error / notice blocks, so no view can render blank. */

import type { ReactNode } from "react";
import { Button } from "./Panel";
import { API_URL } from "../shared/api";

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-3 px-6 py-10 text-sm text-slate-500">
      <span className="h-2 w-2 animate-pulse rounded-full bg-indigo-500" />
      {label}
    </div>
  );
}

export function EmptyState({
  title,
  detail,
  action,
}: {
  title: string;
  detail?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="px-6 py-12 text-center">
      <p className="text-sm font-medium text-slate-700">{title}</p>
      {detail !== undefined && (
        <p className="mx-auto mt-1.5 max-w-md text-xs leading-relaxed text-slate-500">
          {detail}
        </p>
      )}
      {action !== undefined && <div className="mt-4">{action}</div>}
    </div>
  );
}

/**
 * The error block. It always names the API base URL, because the most common
 * failure in a demo is the API simply not being up.
 */
export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="m-6 rounded-md border border-rose-200 bg-rose-50 px-4 py-3.5">
      <p className="text-sm font-medium text-rose-900">Could not load this view</p>
      <p className="mt-1 font-mono text-xs break-words text-rose-700">{message}</p>
      <p className="mt-2 text-xs text-rose-700/80">
        API base: <span className="font-mono">{API_URL}</span>
      </p>
      {onRetry !== undefined && (
        <div className="mt-3">
          <Button variant="secondary" onClick={onRetry}>
            Retry
          </Button>
        </div>
      )}
    </div>
  );
}

type NoticeTone = "info" | "warning" | "danger" | "positive";

const NOTICE_CLASS: Record<NoticeTone, string> = {
  info: "border-indigo-200 bg-indigo-50 text-indigo-900",
  warning: "border-amber-300 bg-amber-50 text-amber-900",
  danger: "border-rose-200 bg-rose-50 text-rose-900",
  positive: "border-emerald-200 bg-emerald-50 text-emerald-900",
};

export function Notice({
  tone = "info",
  title,
  children,
  className = "",
}: {
  tone?: NoticeTone;
  title?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`rounded-md border px-4 py-3 ${NOTICE_CLASS[tone]} ${className}`}>
      {title !== undefined && <p className="text-sm font-medium">{title}</p>}
      <div className="text-xs leading-relaxed opacity-90">{children}</div>
    </div>
  );
}

/**
 * The stub adjudicator is a deterministic stand-in, not a model. Saying so in
 * the UI is the difference between a demo and a misleading demo.
 */
export function StubWarning({ className = "" }: { className?: string }) {
  return (
    <Notice tone="warning" title="This run used the stub adjudicator" className={className}>
      The stub is a deterministic placeholder, <strong>not a language model</strong>. Its
      Tier 3 decisions, confidences and rationales are fixtures — they are not model
      quality and must not be read as evidence of matching accuracy. Set an Anthropic API
      key and start a run with the <span className="font-mono">anthropic</span>{" "}
      adjudicator for a representative result.
    </Notice>
  );
}
