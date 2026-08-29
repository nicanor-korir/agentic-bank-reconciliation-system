/**
 * Status and tier vocabulary.
 *
 * Colours live here and nowhere else: a run that is amber in the list must be
 * amber in the detail header, and Tier 3 must be violet in the breakdown bar
 * and in the audit timeline. Class strings are written out in full because
 * Tailwind only sees literals.
 */

import type { ReactNode } from "react";

const STATUS_CLASS: Record<string, string> = {
  running: "bg-amber-50 text-amber-800 ring-amber-200",
  awaiting_human: "bg-indigo-50 text-indigo-800 ring-indigo-200",
  completed: "bg-emerald-50 text-emerald-800 ring-emerald-200",
  halted_cost: "bg-orange-50 text-orange-800 ring-orange-200",
  failed: "bg-rose-50 text-rose-800 ring-rose-200",
  timeout: "bg-slate-100 text-slate-700 ring-slate-300",
};

const NEUTRAL = "bg-slate-100 text-slate-700 ring-slate-300";

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${
        STATUS_CLASS[status] ?? NEUTRAL
      }`}
    >
      {status === "running" && (
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-500" />
      )}
      {status.replace(/_/g, " ")}
    </span>
  );
}

const TIER_CLASS: Record<string, string> = {
  "0": "bg-emerald-50 text-emerald-800 ring-emerald-200",
  "1": "bg-sky-50 text-sky-800 ring-sky-200",
  "2": "bg-slate-100 text-slate-700 ring-slate-300",
  "3": "bg-violet-50 text-violet-800 ring-violet-200",
  "4": "bg-amber-50 text-amber-800 ring-amber-200",
};

/** Fill colours for the breakdown bar, matched to the badge palette. */
export const TIER_BAR_CLASS: Record<string, string> = {
  "0": "bg-emerald-500",
  "1": "bg-sky-500",
  "2": "bg-slate-400",
  "3": "bg-violet-500",
  "4": "bg-amber-500",
};

export const TIER_LABEL: Record<string, string> = {
  "0": "Deterministic exact",
  "1": "Deterministic structural",
  "2": "Candidate generation",
  "3": "LLM adjudication",
  "4": "Human review",
};

export function TierBadge({ tier }: { tier: number | string }) {
  const key = String(tier);
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 font-mono text-xs font-medium ring-1 ring-inset ${
        TIER_CLASS[key] ?? NEUTRAL
      }`}
      title={TIER_LABEL[key] ?? "Unknown tier"}
    >
      T{key}
    </span>
  );
}

type TagTone = "neutral" | "accent" | "positive" | "warning" | "danger";

const TAG_CLASS: Record<TagTone, string> = {
  neutral: "bg-slate-100 text-slate-600 ring-slate-200",
  accent: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  positive: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  warning: "bg-amber-50 text-amber-800 ring-amber-200",
  danger: "bg-rose-50 text-rose-700 ring-rose-200",
};

export function Tag({
  tone = "neutral",
  mono = false,
  title,
  children,
}: {
  tone?: TagTone;
  mono?: boolean;
  title?: string;
  children: ReactNode;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${
        TAG_CLASS[tone]
      } ${mono ? "font-mono" : ""}`}
    >
      {children}
    </span>
  );
}

/** A git sha the run was produced from, flagged when the tree was dirty. */
export function GitSha({ sha, dirty }: { sha: string; dirty: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="font-mono text-xs text-slate-600">{sha.slice(0, 7) || "—"}</span>
      {dirty && (
        <span
          className="inline-flex items-center rounded bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-800 ring-1 ring-inset ring-amber-200"
          title="Working tree was uncommitted when this run started — replay is not guaranteed byte-identical."
        >
          dirty
        </span>
      )}
    </span>
  );
}
