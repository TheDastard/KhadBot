// apps/web/src/components/raids/lookup.ts
//
// The Raids page's data hooks: the report lookup (GET /v1/lookup/report) and the server's reports list for a server
// without lookup. Whether the visitor is signed in (it picks which "report not found" the page shows) is `useSignedIn`
// in lib/session.ts, shared with the account menu.

"use client";

import { unwrap } from "@khadbot/api-client";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";

import { nightFromLookup, nightFromReport } from "./night";

/** The lookup of a code or URL. Not retried: the page's error cards have their own Retry. */
export function useReportLookup(source: string | null) {
  return useQuery({
    queryKey: ["lookup", source],
    enabled: !!source,
    retry: false,
    queryFn: async () => {
      const data = await unwrap(api().GET("/v1/lookup/report", { params: { query: { source: source ?? "" } } }));
      return { data, night: nightFromLookup(data) };
    },
  });
}

/** The server's reports (lookup off): each report's detail, as a night. */
export function useServerNights() {
  return useQuery({
    queryKey: ["reports", "nights"],
    retry: false,
    queryFn: async () => {
      const list = await unwrap(api().GET("/v1/reports"));
      const details = await Promise.all(
        list.reports.map((r) => unwrap(api().GET("/v1/reports/{code}", { params: { path: { code: r.code } } }))),
      );
      return { nights: details.map(nightFromReport), truncated: list.truncated, total: list.total };
    },
  });
}

/** Seconds left of a wait that began at `startedAt` (ms), counting down; 0 when there is no wait. */
export function useCountdown(waitS: number | null, startedAt: number): number {
  const [now, setNow] = useState(() => Date.now());
  const endsAt = waitS === null ? null : startedAt + waitS * 1000;
  useEffect(() => {
    if (endsAt === null) return;
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, [endsAt]);
  return endsAt === null ? 0 : Math.max(0, (endsAt - now) / 1000);
}
