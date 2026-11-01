// apps/web/src/components/raids/before-search.tsx
//
// The card shown before any search (canvas "Raids, before a search"): used by the Log tab and by the guild and
// character searches, which are written in their own directories and import it from here.

import { SearchIcon } from "lucide-react";

export function BeforeSearch() {
  return (
    <section
      aria-labelledby="raids-empty-h"
      className="flex flex-col items-center gap-2.5 rounded-sm border border-dashed border-line bg-s1 px-6 py-8 text-center"
    >
      <SearchIcon className="size-10 text-mut" strokeWidth={1.6} aria-hidden />
      <h2 id="raids-empty-h" className="m-0 font-display text-xl leading-[1.3]">
        Find a night of raiding
      </h2>
      <p className="m-0 max-w-[56ch]">
        Paste a WarcraftLogs report link, or search by guild or character. Their pulls show up here; open any pull to review it.
      </p>
    </section>
  );
}
