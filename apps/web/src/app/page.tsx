// apps/web/src/app/page.tsx
//
// The home page: Raids, the first screen for everyone (signed in, signed out, and on a server without sign-in).
//
// Slot: raids-page. The Raids page (components/raids/: its "Raids" heading, the search and the results) replaces
// the <SlotPlaceholder> below with its page component. It reads `features.report_lookup` itself: with lookup off
// (main.py serve) it lists the reports on the server from GET /v1/reports instead of the search.

"use client";

import { SlotPlaceholder } from "@/components/shared/slot-placeholder";
import { PageColumn, TopBar } from "@/components/shared/top-bar";

export default function HomePage() {
  return (
    <>
      <TopBar />
      <PageColumn>
        <SlotPlaceholder slot="raids-page" label="Raids search" minHeight={240} />
      </PageColumn>
    </>
  );
}
