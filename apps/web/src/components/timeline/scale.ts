// apps/web/src/components/timeline/scale.ts
//
// The timeline's time axis, without any drawing library so the pull page's main bundle stays small: a linear map
// between fight-relative milliseconds and a width (both ways), tick steps that suit the pull's length, and the zoom
// window rules (a brushed or keyed window is clamped to the fight and to 30 s, the events route's limit).

import type { TimeWindow } from "@/lib/focus";
import { formatClock } from "@/lib/format";

/** The events route refuses a window longer than this. */
export const MAX_ZOOM_MS = 30_000;
/** Z zooms to this much time around the focused marker. */
export const ZOOM_SPAN_MS = 10_000;
/** A brushed window shorter than this is a click, not a zoom. */
export const MIN_ZOOM_MS = 500;

export interface TimeScale {
  durationMs: number;
  widthPx: number;
  /** Milliseconds to pixels, clamped to the axis. */
  toX: (ms: number) => number;
  /** Pixels to milliseconds, clamped to the fight. */
  toMs: (x: number) => number;
  /** Milliseconds to a percentage of the axis (0 to 100), for positions in CSS. */
  toPct: (ms: number) => number;
}

/** A linear scale from [0, durationMs] to [0, widthPx]. A zero or negative duration maps everything to 0. */
export function makeTimeScale(durationMs: number, widthPx: number): TimeScale {
  const d = Math.max(0, durationMs);
  const w = Math.max(0, widthPx);
  const clampMs = (ms: number) => Math.min(d, Math.max(0, ms));
  return {
    durationMs: d,
    widthPx: w,
    toX: (ms) => (d === 0 ? 0 : (clampMs(ms) / d) * w),
    toMs: (x) => (w === 0 ? 0 : clampMs((x / w) * d)),
    toPct: (ms) => (d === 0 ? 0 : (clampMs(ms) / d) * 100),
  };
}

const TICK_STEPS_S = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800];

/** The smallest round step (1 s ... 30 min) that gives no more than `maxTicks` ticks across the fight. */
export function tickStepMs(durationMs: number, maxTicks = 12): number {
  for (const s of TICK_STEPS_S) {
    if (durationMs / (s * 1000) <= maxTicks) return s * 1000;
  }
  return TICK_STEPS_S[TICK_STEPS_S.length - 1]! * 1000;
}

/** Tick times from 0 to the end of the fight, every `stepMs`. */
export function tickTimes(durationMs: number, stepMs = tickStepMs(durationMs)): number[] {
  const out: number[] = [];
  for (let t = 0; t <= durationMs; t += stepMs) out.push(t);
  return out;
}

export function formatTick(ms: number): string {
  return formatClock(ms);
}

/**
 * A window from two edges in either order, clamped to [0, durationMs] and to `maxSpanMs` (the later edge gives way, so
 * a long drag keeps its start). Null when it is shorter than MIN_ZOOM_MS: a click, not a zoom.
 */
export function clampWindow(
  aMs: number,
  bMs: number,
  durationMs: number,
  maxSpanMs: number = MAX_ZOOM_MS,
): TimeWindow | null {
  if (!Number.isFinite(aMs) || !Number.isFinite(bMs)) return null;
  const lo = Math.min(aMs, bMs);
  const hi = Math.max(aMs, bMs);
  const startMs = Math.round(Math.min(durationMs, Math.max(0, lo)));
  const endMs = Math.round(Math.min(durationMs, startMs + maxSpanMs, Math.max(0, hi)));
  return endMs - startMs < MIN_ZOOM_MS ? null : { startMs, endMs };
}

/**
 * The `spanMs` window centred on `atMs`, slid to stay inside [0, durationMs] (Z zooms to 10 s around the focused
 * marker). A fight shorter than the span gives the whole fight.
 */
export function zoomWindowAround(atMs: number, durationMs: number, spanMs: number = ZOOM_SPAN_MS): TimeWindow {
  const span = Math.min(spanMs, Math.max(durationMs, 1));
  const startMs = Math.round(Math.min(Math.max(0, atMs - span / 2), Math.max(0, durationMs - span)));
  return { startMs, endMs: startMs + span };
}

/** A window from the URL made safe for the events route: inside the fight and at most 30 s. Null when nothing is left. */
export function fitWindow(window: TimeWindow, durationMs: number): TimeWindow | null {
  return clampWindow(window.startMs, window.endMs, durationMs);
}
