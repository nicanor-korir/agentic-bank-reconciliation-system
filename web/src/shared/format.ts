/**
 * Display formatting. Nothing here does arithmetic on money.
 *
 * `amount_minor` / `total_minor` / `difference_minor` are integer minor units
 * (US cents). The single division by 100 below is the only float a monetary
 * value is ever allowed to touch, and it happens at the point of display.
 */

const CURRENCY_CODE = /^[A-Za-z]{3}$/;

/** Integer minor units -> a currency string. Never used for comparisons. */
export function formatMinor(minor: number, currency = "USD"): string {
  const code = CURRENCY_CODE.test(currency) ? currency.toUpperCase() : "USD";
  try {
    return (minor / 100).toLocaleString("en-US", {
      style: "currency",
      currency: code,
    });
  } catch {
    return (minor / 100).toLocaleString("en-US", {
      style: "currency",
      currency: "USD",
    });
  }
}

/** Signed, for the exception queue where the direction of money matters. */
export function formatMinorSigned(minor: number, currency = "USD"): string {
  const formatted = formatMinor(Math.abs(minor), currency);
  return minor < 0 ? `-${formatted}` : formatted;
}

/** Costs are micro-dollars, a different unit from statement amounts entirely. */
export function formatMicro(micro: number): string {
  return `$${(micro / 1_000_000).toLocaleString("en-US", {
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
  })}`;
}

export function formatInteger(value: number): string {
  return value.toLocaleString("en-US");
}

/**
 * `value_date` is a plain calendar date. Parsing it as an instant would shift
 * it a day for anyone west of UTC, so it is split by hand.
 */
export function formatDate(value: string | null): string {
  if (!value) return "—";
  const parts = value.slice(0, 10).split("-");
  if (parts.length !== 3) return value;
  const [year, month, day] = parts;
  if (year === undefined || month === undefined || day === undefined) return value;
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    timeZone: "UTC",
  });
}

/** Timestamps are real instants and are shown in the reviewer's zone. */
export function formatTimestamp(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function shortId(value: string, length = 8): string {
  if (!value) return "—";
  return value.length <= length ? value : value.slice(0, length);
}

export function shortHash(value: string, length = 12): string {
  if (!value) return "—";
  return value.length <= length ? value : value.slice(0, length);
}

export function formatPercent(part: number, total: number): string {
  if (total <= 0) return "0.0%";
  return `${((part / total) * 100).toFixed(1)}%`;
}

export function formatConfidence(value: number | null): string {
  if (value === null) return "—";
  return value.toFixed(3);
}

export function pluralise(count: number, singular: string, plural?: string): string {
  return count === 1 ? singular : (plural ?? `${singular}s`);
}

/** `tier2_candidates` -> `Tier 2 candidates`. Node names are the graph's. */
export function humaniseKey(key: string): string {
  return key.replace(/_/g, " ");
}
