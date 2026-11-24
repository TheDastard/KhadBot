// apps/web/src/app/error.tsx
//
// The error boundary for every page under the root layout: reports the error (when reporting is on) and shows it
// with a retry (components/shared/route-error.tsx). The layout's providers stay in place around it.

"use client";

import { RouteError } from "@/components/shared/route-error";

export default function ErrorPage({ error, retry }: { error: unknown; retry: () => void }) {
  return <RouteError error={error} retry={retry} />;
}
