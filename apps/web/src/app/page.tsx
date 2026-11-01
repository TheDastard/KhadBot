// apps/web/src/app/page.tsx
//
// The home page: Raids, the first screen for everyone (signed in, signed out, and on a server without sign-in).
//
// Slot: raids-page, filled by the Raids page (components/raids/: its "Raids" heading, the search and the results).
// It reads `features.report_lookup` itself: with lookup off (main.py serve) it lists the reports on the server from GET /v1/reports instead of the search.

"use client";

import { RaidsPage } from "@/components/raids";
import { PageColumn, TopBar } from "@/components/shared/top-bar";

export default function HomePage() {
  return (
    <>
      <TopBar />
      <PageColumn>
        <RaidsPage />
      </PageColumn>
    </>
  );
}
