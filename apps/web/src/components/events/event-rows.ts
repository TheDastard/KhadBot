// apps/web/src/components/events/event-rows.ts
//
// The zoomed events table's rows, read from the events route: the route types its rows as open objects, which are
// the API's EventRecord (one annotated event, fields that are null absent), so each row is narrowed to that type
// here. Pure functions: the flag a row carries, and the window's title.

import type { Schema } from "@khadbot/api-client";

import { formatClockTenths, formatNumber } from "@/lib/format";

export type EventRow = Schema<"EventRecord">;

/** The event types the table shows by default: what hurt people and who died. */
export const DEFAULT_TYPES = ["damage", "death"] as const;

export function asEventRows(rows: ReadonlyArray<Record<string, unknown>>): EventRow[] {
  return rows.filter(
    (r): r is EventRow => typeof r.ref === "string" && typeof r.type === "string" && typeof r.t_ms === "number",
  );
}

export interface RowFlag {
  text: string;
  /** `bad` for a failure, `mut` for a note. */
  tone: "bad" | "mut" | "none";
}

/** The Flag column: a failure and who it is charged to, a death, an overkill. */
export function rowFlag(row: EventRow): RowFlag {
  if (row.failure) return { text: row.blamed ? `Failure · ${row.blamed}` : "Failure", tone: "bad" };
  if (row.type === "death") return { text: "Death", tone: "mut" };
  if (row.overkill && row.overkill > 0) return { text: `Overkill ${formatNumber(row.overkill)}`, tone: "mut" };
  return { text: "", tone: "none" };
}

/** "7:00.3 – 7:10.3", the heading's window. */
export function windowTitle(startMs: number, endMs: number): string {
  return `${formatClockTenths(startMs)} – ${formatClockTenths(endMs)}`;
}

/** "13 of 13 rows". `total` counts every row the filter matched; the route returns at most 200. */
export function rowCount(shown: number, total: number): string {
  return `${formatNumber(shown)} of ${formatNumber(total)} row${total === 1 ? "" : "s"}`;
}
