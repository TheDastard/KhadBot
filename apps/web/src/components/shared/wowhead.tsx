// apps/web/src/components/shared/wowhead.tsx
//
// Wowhead's tooltip script, the one third-party script the browser loads (.claude/rules/web.md; embed config, hosts
// and terms in docs/game-art.md). It turns `https://www.wowhead.com/spell=<id>` links into tooltips and adds each
// spell's icon. The KhadBot server fetches nothing from Wowhead.
//
// - The root layout sets `whTooltips` in <head> (WOWHEAD_CONFIG_SCRIPT) and renders <WowheadScript/>, which loads
//   the script once the page is interactive. When it has loaded, <html data-wowhead="ready"> marks it and AbilityName
//   drops its neutral fallback square; when it can't load (offline, blocked, the e2e tests), the fallback square and
//   the name-and-ID tooltip stay.
// - refreshWowheadLinks() asks the script to scan again after a client render: it scans only at load, and the
//   dashboard renders afterwards. WH.Tooltips.refreshLinks comes from the script's code, not Wowhead's docs.
//   Calls in the same frame are batched into one scan. This module is the only place that calls it.

"use client";

import Script from "next/script";
import { useSyncExternalStore } from "react";

import { WOWHEAD_SCRIPT } from "@/lib/wowhead";

declare global {
  interface Window {
    whTooltips?: Record<string, unknown>;
    WH?: { Tooltips?: { refreshLinks?: (element?: Element) => void } };
  }
}

const READY_ATTR = "wowhead";
const listeners = new Set<() => void>();

function markReady() {
  document.documentElement.dataset[READY_ATTR] = "ready";
  listeners.forEach((listener) => listener());
  refreshWowheadLinks();
}

/** Loads the script once the page is interactive (the root layout renders it). */
export function WowheadScript() {
  return <Script src={WOWHEAD_SCRIPT} strategy="afterInteractive" onReady={markReady} />;
}

/** True once Wowhead's script has loaded in this page. */
export function useWowheadReady(): boolean {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => document.documentElement.dataset[READY_ATTR] === "ready",
    () => false,
  );
}

let scheduled = false;

/** Ask the script to scan the page's links again, once per animation frame however many callers ask. */
export function refreshWowheadLinks(): void {
  if (typeof window === "undefined" || scheduled) return;
  scheduled = true;
  window.requestAnimationFrame(() => {
    scheduled = false;
    window.WH?.Tooltips?.refreshLinks?.();
  });
}
