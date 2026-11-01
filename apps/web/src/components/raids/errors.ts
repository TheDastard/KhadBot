// apps/web/src/components/raids/errors.ts: turns whatever a query threw into an ApiError the error cards can read.

import { ApiError, isApiError } from "@khadbot/api-client";

export function isApiErrorLike(error: unknown): ApiError {
  if (isApiError(error)) return error;
  return new ApiError({ status: 0, code: "api_unreachable", message: "KhadBot isn't responding." });
}
