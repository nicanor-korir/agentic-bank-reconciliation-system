/**
 * Coercion helpers for untrusted JSON.
 *
 * Everything crossing the API boundary arrives as `unknown`. These narrow it
 * without `any` and without non-null assertions: a field the API stopped
 * sending degrades to a fallback rather than throwing inside a render.
 */

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function asRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

export function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

/** Empty strings are treated as absent — the API uses "" for "nothing here". */
export function asOptionalString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

export function asNumber(value: unknown, fallback = 0): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallback;
}

/**
 * Numbers that may legitimately be missing. Note that `confidence` arrives as
 * a JSON number from the audit endpoint and as a decimal *string* from the
 * queue endpoint, so both are accepted.
 */
export function asOptionalNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

export function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

export function asStringArray(value: unknown): string[] {
  return asArray(value).filter((item): item is string => typeof item === "string");
}

export function asNumberArray(value: unknown): number[] {
  return asArray(value)
    .map((item) => asOptionalNumber(item))
    .filter((item): item is number => item !== null);
}

/** `{"0": 660, "1": 328}` — keys are tier numbers rendered as strings. */
export function asNumberMap(value: unknown): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [key, raw] of Object.entries(asRecord(value))) {
    const parsed = asOptionalNumber(raw);
    if (parsed !== null) out[key] = parsed;
  }
  return out;
}
