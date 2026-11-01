// apps/web/src/lib/queries.ts
//
// TanStack Query keys and the hooks more than one area reads. A key starts with the route's resource and its path
// parameters, so `invalidateQueries({ queryKey: queryKeys.pull(code, fight) })` refreshes everything about a pull
// (the opening-a-pull flow does this when the digest is written, which swaps the pull page in without a reload).
// Area-specific queries live with their components and build their keys from these prefixes.

"use client";

import { unwrap, type ApiGet } from "@khadbot/api-client";
import { useQuery } from "@tanstack/react-query";

import { api, healthApi } from "./api";

export const queryKeys = {
  health: ["health"] as const,
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
 * `GET /v1/reports/{code}/pulls/{fight}/digest`: the pull page's main payload. A 404 (`pull_not_found`, or
 * `report_not_found` for a report this server holds nothing of) means the pull isn't loaded here; the pull route
 * shell shows its "not loaded" slot then. Not retried on a 4xx.
 */
export function usePullDigest(code: string, fight: number, options: { full?: boolean } = {}) {
  return useQuery({
    queryKey: [...queryKeys.digest(code, fight), { full: !!options.full }],
    queryFn: () =>
      unwrap(
        api().GET("/v1/reports/{code}/pulls/{fight}/digest", {
          params: { path: { code, fight }, query: options.full ? { full: true } : {} },
        }),
      ),
    retry: (count, error) => count < 2 && !isClientError(error),
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
