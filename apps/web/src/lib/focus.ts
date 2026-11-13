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
//
// A change is a shallow push (`window.history.pushState`, which Next.js folds into its router and useSearchParams),
// not a router navigation, so a focus change never asks the server for the page again. Components read the focus
// through slices (`useFocusRef`, `useFocusFull`, `useFocusWindow`, `useIsFocusedRef`, `useFocusBaseSearch`), each a
// useSyncExternalStore over the URL that re-renders its component only when that slice changes: selecting a marker
// or a chip re-renders the timeline's selection, the two chips involved and the panel, not the whole pull page.
// `<FocusSync />`, mounted once by the pull page, tells the slices about URL changes Next.js makes itself (a Link to
// another pull, Back and Forward).

"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useLayoutEffect, useMemo, useSyncExternalStore } from "react";

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

// ---------------------------------------------------------------------------
// The URL as a store
// ---------------------------------------------------------------------------

const listeners = new Set<() => void>();

function notify() {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("popstate", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("popstate", listener);
  };
}

/** The current query string without "?"; empty on the server, so client slices start empty and catch up. */
function currentSearch(): string {
  return typeof window === "undefined" ? "" : window.location.search.replace(/^\?/, "");
}

/** A slice of the focus: its component re-renders only when `select` returns a different primitive. */
function useSlice<T extends string | boolean | null>(select: (params: URLSearchParams) => T): T {
  return useSyncExternalStore(
    subscribe,
    () => select(new URLSearchParams(currentSearch())),
    () => select(new URLSearchParams()),
  );
}

/**
 * Keeps the focus slices in step with URL changes Next.js makes (a Link to another pull, Back and Forward). Mount
 * it once where the focus is read; it renders nothing, and it is the only focus reader useSearchParams re-renders.
 */
export function FocusSync(): null {
  const search = useSearchParams().toString();
  useLayoutEffect(() => notify(), [search]);
  return null;
}

/** Push an href on the current page (a focus change, or an evidence chip's) without a router navigation. */
export function pushFocusHref(href: string): void {
  const next = new URL(href, window.location.href);
  if (next.pathname === window.location.pathname && next.search === window.location.search) return;
  window.history.pushState(null, "", `${next.pathname}${next.search}${next.hash}`);
  notify();
}

/** Apply a change to the current URL with a shallow history push (Back undoes it). */
export function pushFocus(change: FocusChange): void {
  pushFocusHref(focusHref(window.location.pathname, new URLSearchParams(currentSearch()), change));
}

// ---------------------------------------------------------------------------
// Slices
// ---------------------------------------------------------------------------

/** The selected ref (`?ref=`), or null. */
export function useFocusRef(): string | null {
  return useSlice((p) => parseFocus(p).ref);
}

/** Whether `evidenceRef` is the selected ref; re-renders only when that answer changes. */
export function useIsFocusedRef(evidenceRef: string): boolean {
  return useSlice((p) => parseFocus(p).ref === evidenceRef);
}

/** "Show everything" (`?full=1`). */
export function useFocusFull(): boolean {
  return useSlice((p) => parseFocus(p).full);
}

/** The zoomed window (`?t=`), or null. */
export function useFocusWindow(): TimeWindow | null {
  const text = useSlice((p) => {
    const t = parseFocus(p).t;
    return t ? formatWindow(t) : null;
  });
  return useMemo(() => parseWindow(text), [text]);
}

/** The query string without `ref` and `t`, which an evidence chip's href keeps. */
export function useFocusBaseSearch(): string {
  return useSlice((p) => {
    p.delete("ref");
    p.delete("t");
    return p.toString();
  });
}

export interface FocusActions {
  /** Apply a change with a history push (Back undoes it). */
  setFocus: (change: FocusChange) => void;
  setRef: (ref: string | null) => void;
  setWindow: (t: TimeWindow | null) => void;
  setFull: (full: boolean) => void;
}

const ACTIONS: FocusActions = {
  setFocus: pushFocus,
  setRef: (ref) => pushFocus({ ref }),
  setWindow: (t) => pushFocus({ t }),
  setFull: (full) => pushFocus({ full }),
};

/** The focus setters: stable, and holding them never re-renders a component on a focus change. */
export function useFocusActions(): FocusActions {
  return ACTIONS;
}

export interface UseFocus extends Focus, FocusActions {
  /** The selected ref, parsed; null when there is none or it has no known shape. */
  parsedRef: ParsedRef | null;
  /** The href a change would lead to (the current page unless `pathname` is given), for links. */
  hrefFor: (change: FocusChange, pathname?: string) => string;
}

/** The whole focus and its setters. Re-renders on any focus change; prefer a slice where one is enough. */
export function useFocus(): UseFocus {
  const ref = useFocusRef();
  const t = useFocusWindow();
  const full = useFocusFull();
  const parsedRef = useMemo(() => (ref ? parseRef(ref) : null), [ref]);
  const hrefFor = useCallback(
    (change: FocusChange, path?: string) =>
      focusHref(path ?? window.location.pathname, new URLSearchParams(currentSearch()), change),
    [],
  );
  return { ref, t, full, parsedRef, hrefFor, ...ACTIONS };
}
