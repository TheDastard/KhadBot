// apps/web/src/components/pull/queries.ts
//
// The queries the pull page and the context panel share, built on lib/queries.ts's key prefixes so that opening a
// pull (which invalidates `queryKeys.pull`) refreshes them too: the report's pulls (the rail and the header counts),
// the encounter's display names, the cooldown registry's names, the report's ability names, a pull's cached findings
// and an evidence record.

"use client";

import { unwrap, type ApiGet } from "@khadbot/api-client";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import { parseRef } from "@/lib/refs";

function isClientError(error: unknown): boolean {
  const status = (error as { status?: number } | null)?.status ?? 0;
  return status >= 400 && status < 500;
}

const retryServerErrors = (count: number, error: unknown) => count < 2 && !isClientError(error);

export type ReportDetailData = ApiGet<"/v1/reports/{code}">;
export type EncounterNamesData = ApiGet<"/v1/encounters/{encounter_id}/names">;
export type CooldownListData = ApiGet<"/v1/cooldowns">;
export type ReportAbilitiesData = ApiGet<"/v1/reports/{code}/abilities">;
export type FindingsData = ApiGet<"/v1/reports/{code}/pulls/{fight}/findings">;
export type EvidenceData = ApiGet<"/v1/evidence/{ref}">;

/** `GET /v1/reports/{code}`: the loaded pulls in groups by encounter and difficulty (the rail). */
export function useReportDetail(code: string) {
  return useQuery({
    queryKey: [...queryKeys.report(code), "detail"],
    queryFn: () => unwrap(api().GET("/v1/reports/{code}", { params: { path: { code } } })),
    retry: retryServerErrors,
  });
}

/** `GET /v1/encounters/{id}/names`: display names for phase, mechanic and window IDs. Disabled without an ID. */
export function useEncounterNames(encounterId: number | undefined, difficulty: number | null | undefined) {
  return useQuery({
    queryKey: ["encounter-names", encounterId, difficulty],
    queryFn: () =>
      unwrap(
        api().GET("/v1/encounters/{encounter_id}/names", {
          params: { path: { encounter_id: encounterId as number }, query: { difficulty: difficulty as number } },
        }),
      ),
    enabled: encounterId !== undefined && typeof difficulty === "number",
    staleTime: 30 * 60_000,
    retry: retryServerErrors,
  });
}

/** `GET /v1/cooldowns`: every registry spell's name and whether it is verified. Changes rarely. */
export function useCooldownNames() {
  return useQuery({
    queryKey: ["cooldowns"],
    queryFn: () => unwrap(api().GET("/v1/cooldowns")),
    staleTime: 60 * 60_000,
    retry: retryServerErrors,
  });
}

/** `GET /v1/reports/{code}/abilities`: the names WCL gives the report's abilities, by game ID. */
export function useReportAbilities(code: string, enabled = true) {
  return useQuery({
    queryKey: [...queryKeys.report(code), "abilities"],
    queryFn: () => unwrap(api().GET("/v1/reports/{code}/abilities", { params: { path: { code } } })),
    enabled,
    staleTime: 30 * 60_000,
    retry: retryServerErrors,
  });
}

/**
 * A pull's digest, fetched only when `enabled` (a rail row's coverage tooltip opening). `full` selects the complete
 * digest (the panel's other-uses lists); the key is lib/queries.ts's, so the page's own digest is shared.
 */
export function useLazyDigest(code: string, fight: number, enabled: boolean, full = false) {
  return useQuery({
    queryKey: [...queryKeys.digest(code, fight), { full }],
    queryFn: () =>
      unwrap(
        api().GET("/v1/reports/{code}/pulls/{fight}/digest", {
          params: { path: { code, fight }, query: full ? { full: true } : {} },
        }),
      ),
    enabled,
    retry: retryServerErrors,
  });
}

/** `GET /v1/reports/{code}/pulls/{fight}/findings`: cached findings; empty when none. Never runs the analyst. */
export function usePullFindings(code: string, fight: number) {
  return useQuery({
    queryKey: [...queryKeys.pull(code, fight), "findings"],
    queryFn: () =>
      unwrap(api().GET("/v1/reports/{code}/pulls/{fight}/findings", { params: { path: { code, fight } } })),
    retry: retryServerErrors,
  });
}

/** `GET /v1/evidence/{ref}`. Plan refs are not fetched (the API answers 501). */
export function useEvidence(ref: string | null) {
  const parsed = ref ? parseRef(ref) : null;
  const key =
    parsed && parsed.kind !== "plan"
      ? [...queryKeys.pull(parsed.report, parsed.fight), "evidence", ref]
      : ["evidence", ref];
  return useQuery({
    queryKey: key,
    queryFn: () => unwrap(api().GET("/v1/evidence/{ref}", { params: { path: { ref: ref ?? "" } } })),
    enabled: !!ref && parsed?.kind !== "plan",
    staleTime: 5 * 60_000,
    retry: retryServerErrors,
  });
}
