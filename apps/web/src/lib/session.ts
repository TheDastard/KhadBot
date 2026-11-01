// apps/web/src/lib/session.ts
//
// Who is signed in, for every area that asks: the Raids page (which "report not found" to show), the account menu in
// the top bar and the sign-in cards. One query, `GET /v1/me` under `queryKeys.me`, so they always agree; signing out
// or an expired session (a 401 from any call) sets it to null, which flips them all together.
//
//   useMe()        the query: the user, null when signed out, pending until the first answer
//   useSignedIn()  true/false once known; null while unknown; always false on a server without sign-in

"use client";

import { isApiError, unwrap, type ApiGet } from "@khadbot/api-client";
import { useQuery } from "@tanstack/react-query";

import { api } from "./api";
import { useFeatures } from "./capabilities";
import { queryKeys } from "./queries";

/** The signed-in user (`GET /v1/me`). */
export type MeData = ApiGet<"/v1/me">;

/** `GET /v1/me`: the user, or null when there is no session (a 401 is the answer, not an error). */
export function useMe() {
  const { sign_in } = useFeatures();
  return useQuery({
    queryKey: queryKeys.me,
    enabled: sign_in,
    retry: false,
    staleTime: 60_000,
    queryFn: async (): Promise<MeData | null> => {
      try {
        return await unwrap(api().GET("/v1/me"));
      } catch (error) {
        if (isApiError(error) && error.status === 401) return null;
        throw error;
      }
    },
  });
}

/** True/false once `GET /v1/me` has answered; null while unknown, and always false on a server without sign-in. */
export function useSignedIn(): boolean | null {
  const { sign_in } = useFeatures();
  const me = useMe();
  if (!sign_in) return false;
  if (me.isPending) return null;
  return me.data != null;
}
