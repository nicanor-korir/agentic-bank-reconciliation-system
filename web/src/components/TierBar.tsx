/**
 * The tier breakdown: one stacked bar plus a legend with counts and shares.
 *
 * The cascade only claims to be cheap if most of the volume clears in Tiers 0
 * and 1, so this bar is the architectural argument in one picture.
 */

import { TIER_BAR_CLASS, TIER_LABEL } from "./Badge";
import { formatInteger, formatPercent } from "../shared/format";

/** Tiers always shown, even at zero — an empty tier is information. */
const BASE_TIERS = ["0", "1", "3", "4"];

export function TierBar({ byTier }: { byTier: Record<string, number> }) {
  const tiers = [...BASE_TIERS, ...Object.keys(byTier).filter((t) => !BASE_TIERS.includes(t))]
    .slice()
    .sort((a, b) => Number(a) - Number(b));

  const rows = tiers.map((tier) => ({ tier, count: byTier[tier] ?? 0 }));
  const total = rows.reduce((sum, row) => sum + row.count, 0);

  return (
    <div>
      <div className="flex h-3 w-full overflow-hidden rounded-full bg-slate-100">
        {total > 0 &&
          rows
            .filter((row) => row.count > 0)
            .map((row) => (
              <div
                key={row.tier}
                className={TIER_BAR_CLASS[row.tier] ?? "bg-slate-400"}
                style={{ width: `${(row.count / total) * 100}%` }}
                title={`Tier ${row.tier}: ${formatInteger(row.count)}`}
              />
            ))}
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
        {rows.map((row) => (
          <div key={row.tier} className="flex items-start gap-2.5">
            <span
              className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-sm ${
                TIER_BAR_CLASS[row.tier] ?? "bg-slate-400"
              } ${row.count === 0 ? "opacity-30" : ""}`}
            />
            <div className="min-w-0">
              <dt className="text-[11px] font-medium tracking-wide text-slate-500 uppercase">
                Tier {row.tier}
              </dt>
              <dd
                className={`font-mono text-sm tabular-nums ${
                  row.count === 0 ? "text-slate-400" : "text-slate-900"
                }`}
              >
                {formatInteger(row.count)}{" "}
                <span className="text-xs text-slate-500">
                  {formatPercent(row.count, total)}
                </span>
              </dd>
              <p className="mt-0.5 truncate text-[11px] text-slate-400">
                {TIER_LABEL[row.tier] ?? "—"}
              </p>
            </div>
          </div>
        ))}
      </dl>

      <p className="mt-4 text-xs text-slate-500">
        {formatInteger(total)} auto-committed{" "}
        {total === 1 ? "decision" : "decisions"} across the cascade. Tier 2 is retrieval
        only and commits nothing; Tier 4 decisions are made by a reviewer and are never
        auto-committed, so they are counted in the audit trail rather than here.
      </p>
    </div>
  );
}
