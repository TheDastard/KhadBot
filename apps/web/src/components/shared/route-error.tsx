// apps/web/src/components/shared/route-error.tsx
//
// What the route error boundaries (app/error.tsx, app/global-error.tsx) show when rendering throws: an ErrorState
// with a "Try again" action, and the request reference when the error is a 5xx API answer that carried an ID. It
// reports the error to the error tracker once per error, when reporting is on (lib/error-reporting.ts).
//
//   <RouteError error={error} retry={retry} />

"use client";

import { useEffect } from "react";

import { reportError } from "@/lib/error-reporting";

import { ErrorState } from "./error-state";

export function RouteError({ error, retry }: { error: unknown; retry: () => void }) {
  useEffect(() => {
    reportError(error);
  }, [error]);
  return (
    <div className="mx-auto max-w-[720px] px-4 py-10">
      <ErrorState
        tone="bad"
        where="Any page"
        title="Something went wrong"
        body="This page hit an error it could not recover from. Try again; if it keeps happening, report it with the reference below when there is one."
        action={{ label: "Try again", onClick: retry }}
        error={error}
      />
    </div>
  );
}
