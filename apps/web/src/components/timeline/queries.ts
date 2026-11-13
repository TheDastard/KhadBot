// apps/web/src/components/timeline/queries.ts
//
// The timeline's two reads, with keys under the pull's prefix (`queryKeys.pull`), so opening a pull that was loading
// refreshes them with the digest:
//   GET /v1/reports/{code}/pulls/{fight}/timeline   the markers, one request per pull
//   GET /v1/reports/{code}/pulls/{fight}/events     the zoomed window's rows (30 s at most, 200 rows at most)

"use client";

import { unwrap, type ApiGet } from "@khadbot/api-client";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { pullTimelineQuery, queryKeys } from "@/lib/queries";

export type PullTimelineData = ApiGet<"/v1/reports/{code}/pulls/{fight}/timeline">;
export type EventsPageData = ApiGet<"/v1/reports/{code}/pulls/{fight}/events">;

function isClientError(error: unknown): boolean {
  const status = (error as { status?: number } | null)?.status ?? 0;
  return status >= 400 && status < 500;
}

/** The timeline's markers (the query is lib/queries.ts's, which the pull route starts beside the digest). */
export function usePullTimeline(code: string, fight: number) {
  return useQuery(pullTimelineQuery(code, fight));
}

export interface EventsQuery {
  fromMs: number;
  toMs: number;
  /** Actor name (as source or target); omit for everyone. */
  actor?: string | null;
  /** Event types to keep; omit for every type. */
  types?: string[] | null;
}

export function usePullEvents(code: string, fight: number, query: EventsQuery | null) {
  return useQuery({
    queryKey: [...queryKeys.pull(code, fight), "events", query],
    enabled: query !== null,
    queryFn: () =>
      unwrap(
        api().GET("/v1/reports/{code}/pulls/{fight}/events", {
          params: {
            path: { code, fight },
            query: {
              t_from_ms: query?.fromMs ?? 0,
              t_to_ms: query?.toMs ?? 0,
              ...(query?.actor ? { actor: query.actor } : {}),
              ...(query?.types?.length ? { types: query.types } : {}),
            },
          },
        }),
      ),
    placeholderData: (previous) => previous,
    retry: (count, error) => count < 1 && !isClientError(error),
  });
}
