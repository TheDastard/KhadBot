// apps/web/src/components/account/use-account.ts
//
// The account menu's data and actions: the WCL budget (GET /v1/budget), the logs the visitor follows live
// (GET /v1/watches, Stop = DELETE /v1/reports/{code}/watch), sign-out (POST /v1/auth/logout) and the "your session
// ended" state, which a 401 from any call sets by emptying the shared `me` query (lib/session.ts).

"use client";

import { isApiError, unwrap, type ApiGet } from "@khadbot/api-client";
import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";

export type BudgetData = ApiGet<"/v1/budget">;
export type WatchData = ApiGet<"/v1/watches">["watches"][number];

const budgetKey = ["budget"] as const;
const watchesKey = ["watches"] as const;
const endedKey = ["session", "ended"] as const;

/** `GET /v1/budget`, read while the menu is open. */
export function useBudget(enabled: boolean) {
  return useQuery({
    queryKey: budgetKey,
    enabled,
    retry: false,
    staleTime: 10_000,
    queryFn: () => unwrap(api().GET("/v1/budget")),
  });
}

/** `GET /v1/watches`: the visitor's active watches, newest first. */
export function useWatches(enabled: boolean) {
  return useQuery({
    queryKey: watchesKey,
    enabled,
    retry: false,
    staleTime: 10_000,
    queryFn: async () => (await unwrap(api().GET("/v1/watches"))).watches,
  });
}

/** A followed log's title (from the report's loaded pulls), or null while unknown or when it has none yet. */
export function useReportTitle(code: string) {
  return useQuery({
    queryKey: [...queryKeys.report(code), "title"],
    retry: false,
    staleTime: 60_000,
    queryFn: async () => {
      try {
        return (await unwrap(api().GET("/v1/reports/{code}", { params: { path: { code } } }))).title;
      } catch (error) {
        if (isApiError(error) && error.status === 404) return null;
        throw error;
      }
    },
  });
}

/** Stop following a log. Refreshes the list either way (a watch that already stopped is simply gone). */
export function useStopWatch() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (code: string) => unwrap(api().DELETE("/v1/reports/{code}/watch", { params: { path: { code } } })),
    onSettled: () => client.invalidateQueries({ queryKey: watchesKey }),
  });
}

// True for a moment after the visitor signs out on purpose, so the emptied `me` query is not read as an expired session.
let signingOut = false;

const KEPT = new Set(["health", queryKeys.me[0], endedKey[0]]);

/** Everything the visitor could see only while signed in leaves the cache; the server's capabilities stay. */
function forgetSession(client: QueryClient): void {
  client.removeQueries({ predicate: (q) => !KEPT.has(String(q.queryKey[0])) });
  client.setQueryData(queryKeys.me, null);
}

/** Sign out, then go to Raids, signed out. */
export function useSignOut() {
  const client = useQueryClient();
  const router = useRouter();
  return useMutation({
    mutationFn: () => unwrap(api().POST("/v1/auth/logout")),
    onSuccess: () => {
      signingOut = true;
      window.setTimeout(() => (signingOut = false), 1000);
      client.setQueryData(endedKey, false);
      forgetSession(client);
      router.push("/");
    },
  });
}

/**
 * Sign out on every device and remove the stored Warcraft Logs sign-in (`POST /v1/auth/forget`), then go to Raids,
 * signed out. Loads and live follows that needed the visitor's own Warcraft Logs sign-in stop.
 */
export function useSignOutEverywhere() {
  const client = useQueryClient();
  const router = useRouter();
  return useMutation({
    mutationFn: () => unwrap(api().POST("/v1/auth/forget")),
    onSuccess: () => {
      signingOut = true;
      window.setTimeout(() => (signingOut = false), 1000);
      client.setQueryData(endedKey, false);
      forgetSession(client);
      router.push("/");
    },
  });
}

/**
 * Watches for a 401 on any call while someone is signed in, which means the session ended: the shared `me` query is
 * emptied (so Raids and the menu agree they are signed out) and `useSessionEnded` turns true. Mount it once, in the
 * top bar's account control.
 */
export function useSessionWatch(): void {
  const client = useQueryClient();
  const signedIn = useRef(false);
  useEffect(() => {
    const cache = client.getQueryCache();
    signedIn.current = client.getQueryData(queryKeys.me) != null;
    return cache.subscribe((event) => {
      if (event.type !== "updated") return;
      const key = event.query.queryKey;
      if (key[0] === queryKeys.me[0]) {
        const me = client.getQueryData(queryKeys.me);
        // Signed in before, and `me` now says no (a refetch got a 401): the session ended.
        if (signedIn.current && me === null && !signingOut) client.setQueryData(endedKey, true);
        signedIn.current = me != null;
        return;
      }
      if (event.action.type !== "error") return;
      const error = event.action.error;
      if (isApiError(error) && error.status === 401 && client.getQueryData(queryKeys.me) != null) forgetSession(client);
    });
  }, [client]);
}

/** True after a signed-in visit lost its session. Cleared by signing out on purpose or reloading. */
export function useSessionEnded(): boolean {
  const { data } = useQuery({ queryKey: endedKey, queryFn: () => false, initialData: false, staleTime: Infinity });
  return data;
}
