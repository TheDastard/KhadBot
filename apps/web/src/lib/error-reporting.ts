// apps/web/src/lib/error-reporting.ts
//
// Client-side error reporting through the Sentry SDK (Sentry or GlitchTip), on only when the build sets
// NEXT_PUBLIC_SENTRY_DSN. Without it the SDK is never loaded. Events carry `environment` from NEXT_PUBLIC_KHADBOT_ENV
// ("unset" when empty) and `release` from NEXT_PUBLIC_KHADBOT_VERSION, as the API's do (src/khadbot/error_reporting.py).
// `instrumentation-client.ts` calls `initErrorReporting()`
// before the app hydrates; the error boundaries (`app/error.tsx`, `app/global-error.tsx`) call `reportError(error)`.
// No user details, cookies, headers, bodies or query strings leave the browser: the SDK is told not to collect them
// and `scrubEvent` / `scrubBreadcrumb` strip whatever still carries them. When the app gets a Content-Security-Policy,
// its `connect-src` must allow the DSN's ingest host (.claude/rules/web.md).

import type { Breadcrumb, ErrorEvent } from "@sentry/nextjs";

type Sdk = typeof import("@sentry/nextjs");

/** The DSN the build was given, or undefined (reporting off). */
export const SENTRY_DSN: string | undefined = process.env.NEXT_PUBLIC_SENTRY_DSN || undefined;

/** The environment of a build without NEXT_PUBLIC_KHADBOT_ENV; the SDK would otherwise call it "production". */
export const UNSET_ENVIRONMENT = "unset";

/** The build's `environment` and `release`, read when the SDK starts. */
export function buildIdentity(): { environment: string; release: string | undefined } {
  return {
    environment: process.env.NEXT_PUBLIC_KHADBOT_ENV || UNSET_ENVIRONMENT,
    release: process.env.NEXT_PUBLIC_KHADBOT_VERSION || undefined,
  };
}

/** `url` without its query string and fragment. */
export function stripQuery(url: string): string {
  return url.split(/[?#]/, 1)[0] ?? url;
}

/** The event to send: request cookies, headers, body and query string removed, and the URL cut at its "?". */
export function scrubEvent(event: ErrorEvent): ErrorEvent {
  if (event.request) {
    const { url } = event.request;
    event.request = url ? { url: stripQuery(url) } : {};
  }
  if (event.breadcrumbs) event.breadcrumbs = event.breadcrumbs.map(scrubBreadcrumb);
  return event;
}

const URL_FIELDS = ["url", "from", "to"] as const;

/** The breadcrumb with the URLs it records (fetch, XHR, navigation) cut at their "?". */
export function scrubBreadcrumb(crumb: Breadcrumb): Breadcrumb {
  if (!crumb.data) return crumb;
  const data = { ...crumb.data };
  for (const field of URL_FIELDS) {
    if (typeof data[field] === "string") data[field] = stripQuery(data[field]);
  }
  return { ...crumb, data };
}

let started: Promise<Sdk> | null = null;

/** Start the SDK once, when `dsn` is set. Returns whether reporting is on. */
export function initErrorReporting(dsn: string | undefined = SENTRY_DSN): boolean {
  if (!dsn) return false;
  started ??= import("@sentry/nextjs").then((sdk) => {
    sdk.init({
      dsn,
      ...buildIdentity(),
      dataCollection: {
        userInfo: false,
        cookies: false,
        httpHeaders: false,
        httpBodies: [],
        urlQueryParams: false,
        stackFrameVariables: false,
      },
      beforeSend: scrubEvent,
      beforeBreadcrumb: scrubBreadcrumb,
    });
    return sdk;
  });
  return true;
}

/** Report an error a boundary caught. Does nothing while reporting is off. */
export function reportError(error: unknown, dsn: string | undefined = SENTRY_DSN): void {
  if (!initErrorReporting(dsn) || !started) return;
  void started.then((sdk) => sdk.captureException(error)).catch(() => undefined);
}

/** Tests only: forget the started SDK. */
export function resetErrorReportingForTests(): void {
  started = null;
}
