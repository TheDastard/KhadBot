// apps/web/src/lib/api.ts
//
// The app's API client. NEXT_PUBLIC_API_BASE picks the API in every environment (the dev loop sets
// http://localhost:8000, so the session cookie the API sets on localhost reaches it); there is no default, and a
// build without it fails loudly at the first call rather than guessing a host.
//
// Credentials follow the server's features: `/v1/health` is read without the cookie (a server without sign-in, such
// as `main.py serve`, refuses credentialed CORS), and the capability gate then turns the cookie on only when the
// server has sign-in (`features.sign_in`). Everything after the gate goes through api().

import { apiUrl, createApiClient, openEventStream, type ApiClient } from "@khadbot/api-client";

let withCredentials = false;
let client: ApiClient | null = null;
let healthClient: ApiClient | null = null;

/** The API's origin. Throws when NEXT_PUBLIC_API_BASE is unset. */
export function apiBase(): string {
  const base = process.env.NEXT_PUBLIC_API_BASE;
  if (!base) {
    throw new Error('NEXT_PUBLIC_API_BASE is not set: point it at the KhadBot API (docs/hosting/local.md, "The web app").');
  }
  return base;
}

/** The shared typed client (openapi-fetch with the CSRF header; see packages/api-client). */
export function api(): ApiClient {
  client ??= createApiClient({ baseUrl: apiBase(), withCredentials });
  return client;
}

/** The client for `/v1/health`, which never sends the cookie. */
export function healthApi(): ApiClient {
  healthClient ??= createApiClient({ baseUrl: apiBase(), withCredentials: false });
  return healthClient;
}

/** Send the session cookie from now on, or not. The capability gate calls it with `features.sign_in`. */
export function setApiCredentials(on: boolean): void {
  if (on !== withCredentials) {
    withCredentials = on;
    client = null;
  }
}

/** An absolute API URL, for links the browser follows itself (sign-in) rather than fetches. */
export function apiHref(path: string, query?: Record<string, string | number | undefined>): string {
  return apiUrl(apiBase(), path, query);
}

/** A server-sent event stream from the API with the session cookie (job progress). */
export function apiEvents(path: string): EventSource {
  return openEventStream(apiBase(), path);
}
