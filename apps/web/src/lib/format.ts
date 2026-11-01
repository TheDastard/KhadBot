// apps/web/src/lib/format.ts
//
// Number and time formatting, with the canvas's rounding: fight-relative clocks (m:ss, m:ss.s), percentages to one
// decimal, amounts with thousands separators or compact (12.3k, 1.2M). Pure functions, no locale guessing: the
// dashboard writes numbers the way the canvas does (en-US grouping).

/** Fight-relative time as m:ss, truncating to the second ("10:18" for 618 s). Negative times clamp to 0:00. */
export function formatClock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total - minutes * 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

/** Fight-relative time as m:ss.s, rounded to the tenth ("7:05.3"). */
export function formatClockTenths(ms: number): string {
  const tenths = Math.max(0, Math.round(ms / 100));
  const minutes = Math.floor(tenths / 600);
  const rest = tenths - minutes * 600;
  const seconds = Math.floor(rest / 10);
  return `${minutes}:${String(seconds).padStart(2, "0")}.${rest % 10}`;
}

/** A duration in seconds as m:ss ("10:18"). */
export function formatDuration(seconds: number): string {
  return formatClock(seconds * 1000);
}

/**
 * A percentage with one decimal and the sign ("5.6%", "96.6%", "100.0%"). Values that would show as 0.0 but are
 * above zero show as "<0.1%", so a nearly finished wipe never reads as a kill.
 */
export function formatPct(value: number, digits = 1): string {
  if (value > 0 && value < 0.05 && digits === 1) return "<0.1%";
  return `${value.toFixed(digits)}%`;
}

/** An amount with thousands separators ("1,234,567"). */
export function formatNumber(value: number): string {
  return Math.round(value).toLocaleString("en-US");
}

/** An amount in short form: under 1,000 as is, then k and M with one decimal ("950", "12.3k", "1.2M"). */
export function formatCompact(value: number): string {
  const abs = Math.abs(value);
  if (abs < 1000) return String(Math.round(value));
  if (abs < 1_000_000) return `${trimZero((value / 1000).toFixed(1))}k`;
  return `${trimZero((value / 1_000_000).toFixed(1))}M`;
}

function trimZero(text: string): string {
  return text.endsWith(".0") ? text.slice(0, -2) : text;
}

/** A countdown in seconds as m:ss, rounding up so it never shows 0:00 while time is left ("Retry in 0:42"). */
export function formatCountdown(seconds: number): string {
  return formatClock(Math.ceil(Math.max(0, seconds)) * 1000);
}
