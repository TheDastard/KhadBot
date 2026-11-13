// apps/web/src/lib/queries.ts
//
// TanStack Query keys and the hooks more than one area reads. A key starts with the route's resource and its path
// parameters, so `invalidateQueries({ queryKey: queryKeys.pull(code, fight) })` refreshes everything about a pull
// (the opening-a-pull flow does this when the digest is written, which swaps the pull page in without a reload).
// Area-specific queries live with their components and build their keys from these prefixes.

"use client";

import { unwrap, type ApiGet } from "@khadbot/api-client";
import { keepPreviousData, queryOptions, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { api, healthApi } from "./api";

export const queryKeys = {
  health: ["health"] as const,
  me: ["me"] as const,
  report: (code: string) => ["report", code] as const,
  pull: (code: string, fight: number) => ["report", code, "pull", fight] as const,
  digest: (code: string, fight: number) => ["report", code, "pull", fight, "digest"] as const,
  players: (code: string) => ["report", code, "players"] as const,
};

/** `GET /v1/health`: what this server offers. Read once per visit; the capability gate waits for it. */
export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: () => unwrap(healthApi().GET("/v1/health")),
    staleTime: Infinity,
    retry: 1,
  });
}

/**
 * `GET /v1/reports/{code}/pulls/{fight}/timeline`: the timeline's markers, one request per pull. The timeline
 * (components/timeline/) reads it; `usePullDigest` starts it beside the digest, so the chart doesn't wait for the
 * page to render before asking.
 */
export function pullTimelineQuery(code: string, fight: number) {
  return queryOptions({
    queryKey: [...queryKeys.pull(code, fight), "timeline"] as const,
    queryFn: () =>
      unwrap(api().GET("/v1/reports/{code}/pulls/{fight}/timeline", { params: { path: { code, fight } } })),
    retry: (count, error) => count < 2 && !isClientError(error),
  });
}

/**
 * `GET /v1/reports/{code}/pulls/{fight}/digest`: the pull page's main payload. A 404 (`pull_not_found`, or
 * `report_not_found` for a report this server holds nothing of) means the pull isn't loaded here; the pull route
 * shell shows its "not loaded" slot then. Not retried on a 4xx.
 */
export function usePullDigest(code: string, fight: number, options: { full?: boolean } = {}) {
  const client = useQueryClient();
  // The timeline is the page's other large read: start it now rather than once the digest has rendered the page.
  // For a pull that isn't loaded it answers 404 like the digest, and opening the pull refetches it.
  useEffect(() => {
    void client.prefetchQuery(pullTimelineQuery(code, fight));
  }, [client, code, fight]);
  return useQuery({
    queryKey: [...queryKeys.digest(code, fight), { full: !!options.full }],
    queryFn: () =>
      unwrap(
        api().GET("/v1/reports/{code}/pulls/{fight}/digest", {
          params: { path: { code, fight }, query: options.full ? { full: true } : {} },
        }),
      ),
    retry: (count, error) => count < 2 && !isClientError(error),
    // "Show everything" changes the key: keep the page on screen (the capped digest) while the full one loads,
    // rather than swapping the whole pull page for its skeleton.
    placeholderData: keepPreviousData,
  });
}

/** `GET /v1/reports/{code}/players`: the report's players and their classes (PlayerName colours). */
export function usePlayers(code: string | undefined) {
  return useQuery({
    queryKey: queryKeys.players(code ?? ""),
    queryFn: () => unwrap(api().GET("/v1/reports/{code}/players", { params: { path: { code: code ?? "" } } })),
    enabled: !!code,
    staleTime: 5 * 60_000,
    retry: (count, error) => count < 2 && !isClientError(error),
  });
}

function isClientError(error: unknown): boolean {
  const status = (error as { status?: number } | null)?.status ?? 0;
  return status >= 400 && status < 500;
}

/** The digest as the client reads it. */
export type PullDigestData = ApiGet<"/v1/reports/{code}/pulls/{fight}/digest">;
