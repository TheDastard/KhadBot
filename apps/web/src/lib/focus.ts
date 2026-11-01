// apps/web/src/lib/focus.ts
//
// What the pull page is focused on lives in the URL, which is the source of truth: reload restores it and Back steps
// through it. Three parameters:
//
//   ref   the selected evidence ref (the context panel shows it), e.g. ?ref=ev:H7z1fqDXjRTFnbtg:15:death:2
//   t     the zoomed time window, fight-relative milliseconds "start-end", e.g. ?t=420341-430341
//   full  "1" shows everything after the death cutoff ("Show everything")
//
// Every change is a history push, so Back undoes it. Other query parameters are kept as they are.

"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo } from "react";

import { parseRef, type ParsedRef } from "./refs";

export interface TimeWindow {
  startMs: number;
  endMs: number;
}

export interface Focus {
  ref: string | null;
  t: TimeWindow | null;
  full: boolean;
}

export type FocusChange = Partial<Focus>;

/** A "start-end" window; null unless both are whole milliseconds with 0 <= start < end. */
export function parseWindow(text: string | null): TimeWindow | null {
  if (!text) return null;
  const match = /^(\d+)-(\d+)$/.exec(text.trim());
  if (!match) return null;
  const startMs = Number(match[1]);
  const endMs = Number(match[2]);
  if (!Number.isSafeInteger(startMs) || !Number.isSafeInteger(endMs) || endMs <= startMs) return null;
  return { startMs, endMs };
}

export function formatWindow(t: TimeWindow): string {
  return `${Math.round(t.startMs)}-${Math.round(t.endMs)}`;
}

/** The window of `spanMs` centred on `atMs`, never starting before 0 (Z zooms to 10 s around a marker). */
export function windowAround(atMs: number, spanMs = 10_000): TimeWindow {
  const startMs = Math.max(0, Math.round(atMs - spanMs / 2));
  return { startMs, endMs: startMs + spanMs };
}

/** The focus a query string holds. A malformed `t` reads as no window; `ref` is kept as given. */
export function parseFocus(params: URLSearchParams): Focus {
  const ref = params.get("ref");
  return {
    ref: ref && ref.trim() ? ref.trim() : null,
    t: parseWindow(params.get("t")),
    full: params.get("full") === "1",
  };
}

/** The query string (without "?") after applying `change` to `current`; a null or false value removes its key. */
export function focusSearch(current: URLSearchParams, change: FocusChange): string {
  const next = new URLSearchParams(current);
  if ("ref" in change) {
    if (change.ref) next.set("ref", change.ref);
    else next.delete("ref");
  }
  if ("t" in change) {
    if (change.t) next.set("t", formatWindow(change.t));
    else next.delete("t");
  }
  if ("full" in change) {
    if (change.full) next.set("full", "1");
    else next.delete("full");
  }
  return next.toString();
}

/** The href of `pathname` with `change` applied to the current focus. */
export function focusHref(pathname: string, current: URLSearchParams, change: FocusChange): string {
  const search = focusSearch(current, change);
  return search ? `${pathname}?${search}` : pathname;
}

export interface UseFocus extends Focus {
  /** The selected ref, parsed; null when there is none or it has no known shape. */
  parsedRef: ParsedRef | null;
  /** Apply a change with a history push (Back undoes it). */
  setFocus: (change: FocusChange) => void;
  setRef: (ref: string | null) => void;
  setWindow: (t: TimeWindow | null) => void;
  setFull: (full: boolean) => void;
  /** The href a change would lead to, for links (EvidenceChip uses it). */
  hrefFor: (change: FocusChange, pathname?: string) => string;
}

/** Read and write the pull page's focus in the URL. Use inside a <Suspense> boundary (useSearchParams needs one). */
export function useFocus(): UseFocus {
  const params = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const search = params.toString();

  const focus = useMemo(() => parseFocus(new URLSearchParams(search)), [search]);
  const parsedRef = useMemo(() => (focus.ref ? parseRef(focus.ref) : null), [focus.ref]);

  const hrefFor = useCallback(
    (change: FocusChange, path?: string) => focusHref(path ?? pathname, new URLSearchParams(search), change),
    [pathname, search],
  );
  const setFocus = useCallback(
    (change: FocusChange) => router.push(hrefFor(change), { scroll: false }),
    [router, hrefFor],
  );

  return {
    ...focus,
    parsedRef,
    setFocus,
    setRef: useCallback((ref) => setFocus({ ref }), [setFocus]),
    setWindow: useCallback((t) => setFocus({ t }), [setFocus]),
    setFull: useCallback((full) => setFocus({ full }), [setFocus]),
    hrefFor,
  };
}
