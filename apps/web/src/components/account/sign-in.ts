// apps/web/src/components/account/sign-in.ts
//
// The "Sign in with Warcraft Logs" link: the API's `/v1/auth/login` with `next` set to the page the visitor is on, so
// they come straight back to it. The API accepts only a path on the web app's own site as `next` (anything else
// becomes "/"), so this builds a path and never an absolute URL.

import { apiHref } from "@/lib/api";

/** The sign-in link that returns to `next`, a path with its query (`/?source=ABC`, `/r/ABC/p/3`). */
export function signInHref(next: string): string {
  return apiHref("/v1/auth/login", { next: next.startsWith("/") && !next.startsWith("//") ? next : "/" });
}

/** The path and query of a location, as `next` wants it. */
export function nextPath(pathname: string, search: string): string {
  const query = search.startsWith("?") ? search : search ? `?${search}` : "";
  return `${pathname || "/"}${query === "?" ? "" : query}`;
}

/** The sign-in link for the page the browser is on now. Call it from an event handler or an effect, not a render. */
export function signInHrefHere(): string {
  if (typeof window === "undefined") return signInHref("/");
  return signInHref(nextPath(window.location.pathname, window.location.search));
}
