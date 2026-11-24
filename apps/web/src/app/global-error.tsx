// apps/web/src/app/global-error.tsx
//
// The last error boundary, for an error in the root layout itself. It replaces the layout, so it renders its own
// <html> and <body> with the global styles and fonts; the theme script does not run, so it follows the OS scheme.

"use client";

import { RouteError } from "@/components/shared/route-error";

import "@/styles/globals.css";

import { fontVariables } from "./fonts";

export default function GlobalError({ error, retry }: { error: unknown; retry: () => void }) {
  return (
    <html lang="en" className={fontVariables}>
      <body>
        <title>Something went wrong · KhadBot</title>
        <RouteError error={error} retry={retry} />
      </body>
    </html>
  );
}
