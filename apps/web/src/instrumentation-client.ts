// apps/web/src/instrumentation-client.ts
//
// Runs in the browser before the app hydrates (Next.js instrumentation-client convention): starts client-side error
// reporting when the build sets NEXT_PUBLIC_SENTRY_DSN, and does nothing otherwise (lib/error-reporting.ts).

import { initErrorReporting } from "@/lib/error-reporting";

initErrorReporting();
