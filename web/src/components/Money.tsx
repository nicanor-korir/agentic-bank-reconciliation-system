/**
 * Money display.
 *
 * Amounts are signed integer minor units. A negative amount is money leaving
 * the account, and a bookkeeper should be able to see that without reading the
 * digits, so it gets its own colour and an explicit sign.
 */

import { formatMinorSigned } from "../lib/format";

export function Money({
  minor,
  currency = "USD",
  className = "",
  size = "base",
}: {
  minor: number;
  currency?: string;
  className?: string;
  size?: "sm" | "base" | "lg";
}) {
  const sizeClass =
    size === "lg" ? "text-lg" : size === "sm" ? "text-xs" : "text-sm";
  const toneClass = minor < 0 ? "text-rose-700" : "text-slate-900";
  return (
    <span
      className={`font-mono tabular-nums ${sizeClass} ${toneClass} ${className}`}
      title={minor < 0 ? "Money out" : "Money in"}
    >
      {formatMinorSigned(minor, currency)}
    </span>
  );
}

/** Direction word, spelled out where there is room for it. */
export function Direction({ minor }: { minor: number }) {
  return (
    <span
      className={`text-[11px] font-medium ${minor < 0 ? "text-rose-600" : "text-emerald-700"}`}
    >
      {minor < 0 ? "money out" : "money in"}
    </span>
  );
}

/**
 * The gap between a bank line and a candidate total. Zero is the thing a
 * reviewer is looking for, so it is named rather than shown as $0.00.
 */
export function Difference({
  minor,
  currency = "USD",
}: {
  minor: number;
  currency?: string;
}) {
  if (minor === 0) {
    return (
      <span className="inline-flex items-center rounded bg-emerald-50 px-1.5 py-0.5 text-[11px] font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200">
        exact
      </span>
    );
  }
  return (
    <span className="inline-flex items-center rounded bg-amber-50 px-1.5 py-0.5 font-mono text-[11px] font-medium text-amber-800 ring-1 ring-inset ring-amber-200">
      {minor > 0 ? "+" : "−"}
      {formatMinorSigned(Math.abs(minor), currency)} off
    </span>
  );
}
