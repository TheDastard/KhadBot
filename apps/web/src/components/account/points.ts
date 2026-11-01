// apps/web/src/components/account/points.ts
//
// What the account menu says about the visitor's Warcraft Logs points: "120 of 180 left this hour" and when the
// hour ends. Pure functions over `GET /v1/budget`.

import { formatNumber } from "@/lib/format";

export interface PointsView {
  left: number;
  limit: number;
  /** The share of the allowance left, 0 to 1 (the progress bar's fill). */
  fraction: number;
  /** "120 of 180 left this hour". */
  label: string;
  /** Whole points left, for the progress bar's value. */
  valueNow: number;
  state: "full" | "part" | "empty";
}

/** The points copy for an allowance. Points left are rounded down, so the menu never promises more than there is. */
export function pointsView(allowance: { limit: number; left: number }): PointsView {
  const limit = Math.max(0, allowance.limit);
  const left = Math.min(limit, Math.max(0, allowance.left));
  const whole = Math.floor(left);
  const fraction = limit > 0 ? whole / limit : 0;
  const state = whole <= 0 ? "empty" : left >= limit ? "full" : "part";
  return {
    left,
    limit,
    fraction,
    label: `${formatNumber(whole)} of ${formatNumber(limit)} left this hour`,
    valueNow: whole,
    state,
  };
}

export interface ResetTimeOptions {
  locale?: string;
  timeZone?: string;
}

/** When the allowance resets, in the visitor's local time ("21:00", or "9:00 PM" where that is the habit). */
export function formatResetTime(iso: string, options: ResetTimeOptions = {}): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  return at.toLocaleTimeString(options.locale, { hour: "2-digit", minute: "2-digit", timeZone: options.timeZone });
}
