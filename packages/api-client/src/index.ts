// packages/api-client/src/index.ts
//
// The one way a KhadBot app reaches the API: the generated types (schema.d.ts, from contracts/openapi.json; never
// edit it, run `mise run gen:types`), the typed client and ApiError.

import type { components } from "./schema";

export type { components, operations, paths } from "./schema";
export { apiUrl, createApiClient, CSRF_HEADER, CSRF_VALUE, openEventStream, unwrap } from "./client";
export type { ApiClient, ApiClientOptions, ApiGet } from "./client";
export {
  API_UNREACHABLE,
  ApiError,
  errorFromResponse,
  isApiError,
  parseRetryAfter,
  UNEXPECTED_RESPONSE,
  unreachable,
} from "./errors";
export type { BudgetWaitBody, ErrorBody } from "./errors";

/** A named schema from the contract: `Schema<"PullDigest">`. */
export type Schema<Name extends keyof components["schemas"]> = components["schemas"][Name];
