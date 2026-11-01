// apps/web/src/components/raids/raids-page.tsx
//
// The Raids page (canvas "Raids, log search" and "Raids, before a search"): the "Raids" heading, a card with the Log,
// Guild and Character tabs and the search form, and the results under it. The form stays open above the results.
// The search lives in the URL (`/?source=<code or URL>`, plus `tab=guild|character` for the later tabs), so reload
// and back restore it. With `features.report_lookup` off (main.py serve) the page lists the server's reports instead.

"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { SkeletonBlock, SkeletonCard } from "@/components/shared/skeletons";
import { SlotPlaceholder } from "@/components/shared/slot-placeholder";
import { useFeatures } from "@/lib/capabilities";
import { pullPagePath } from "@/lib/refs";
import { cn } from "@/lib/utils";

import { BeforeSearch } from "./before-search";
import { LookupError } from "./lookup-error";
import { useReportLookup } from "./lookup";
import { NightCard } from "./night-card";
import { summaryLine } from "./night";
import { ServerReports } from "./server-reports";
import { isApiErrorLike } from "./errors";

type Tab = "log" | "guild" | "character";
const TAB_LABELS: Record<Tab, string> = { log: "Log", guild: "Guild", character: "Character" };

export function RaidsPage() {
  const features = useFeatures();
  return (
    <>
      <h1 id="raids-h" className="m-0 font-display text-[28px] leading-[1.2]">
        Raids
      </h1>
      {features.report_lookup ? (
        <Suspense fallback={<SkeletonCard height={180} />}>
          <Search />
        </Suspense>
      ) : (
        <ServerReports />
      )}
    </>
  );
}

function Search() {
  const features = useFeatures();
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const tabs: Tab[] = features.raid_search ? ["log", "guild", "character"] : ["log"];
  const asked = params.get("tab");
  const tab: Tab = tabs.find((t) => t === asked) ?? "log";
  const source = params.get("source")?.trim() || null;
  const baseId = useId();

  function go(next: Tab, value?: string) {
    const q = new URLSearchParams();
    if (next !== "log") q.set("tab", next);
    if (value) q.set("source", value);
    const qs = q.toString();
    router.push(qs ? `${pathname}?${qs}` : pathname);
  }

  function onTabKey(event: KeyboardEvent<HTMLButtonElement>) {
    const at = tabs.indexOf(tab);
    const to =
      event.key === "ArrowRight" ? at + 1 : event.key === "ArrowLeft" ? at - 1 : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : null;
    if (to === null) return;
    event.preventDefault();
    const next = tabs[(to + tabs.length) % tabs.length]!;
    go(next);
    document.getElementById(`${baseId}-tab-${next}`)?.focus();
  }

  return (
    <>
      <section aria-labelledby="raids-h" className="flex flex-col rounded-sm border border-line bg-s1">
        <div role="tablist" aria-label="Search by" className="flex gap-1 overflow-x-auto border-b border-line px-3.5 pt-1.5">
          {tabs.map((t) => (
            <button
              key={t}
              type="button"
              role="tab"
              id={`${baseId}-tab-${t}`}
              aria-selected={t === tab}
              aria-controls={`${baseId}-panel`}
              tabIndex={t === tab ? 0 : -1}
              onClick={() => go(t)}
              onKeyDown={onTabKey}
              className={cn(
                "-mb-px min-h-11 cursor-pointer border-b-2 bg-transparent px-3.5 py-2 text-sm whitespace-nowrap",
                t === tab ? "border-acc font-semibold text-ink" : "border-transparent font-medium text-mut",
              )}
            >
              {TAB_LABELS[t]}
            </button>
          ))}
        </div>
        <div id={`${baseId}-panel`} role="tabpanel" aria-labelledby={`${baseId}-tab-${tab}`} className="px-5 pt-4 pb-[18px]">
          {tab === "log" ? <LogForm key={source ?? ""} source={source} onSubmit={(v) => go("log", v)} /> : null}
          {/* Slot: raids-guild. The guild search form (components/raids/guild/) replaces this line. */}
          {tab === "guild" ? <SlotPlaceholder slot="raids-guild" label="Guild search" minHeight={96} /> : null}
          {/* Slot: raids-character. The character search form (components/raids/character/) replaces this line. */}
          {tab === "character" ? <SlotPlaceholder slot="raids-character" label="Character search" minHeight={96} /> : null}
        </div>
      </section>
      {tab === "log" ? <LogResults source={source} /> : null}
    </>
  );
}

function LogForm({ source, onSubmit }: { source: string | null; onSubmit: (value: string) => void }) {
  const [draft, setDraft] = useState(source ?? "");
  function submit(event: FormEvent) {
    event.preventDefault();
    const value = draft.trim();
    if (value) onSubmit(value);
  }
  return (
    <form onSubmit={submit} className="flex flex-col gap-3.5">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex min-w-0 flex-[1_1_420px] flex-col gap-1.5">
          <label htmlFor="raids-source" className="text-sm font-medium">
            WCL report code or URL
          </label>
          <input
            id="raids-source"
            name="source"
            type="text"
            autoComplete="off"
            spellCheck={false}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="H7z1fqDXjRTFnbtg or https://www.warcraftlogs.com/reports/…"
            className="min-h-10 w-full rounded-sm border border-line bg-s2 px-3 py-2 text-sm text-ink"
          />
        </div>
        <Button type="submit" size="lg">
          Look up
        </Button>
      </div>
      <p className="m-0 text-[13px] text-mut">
        Lists the report&rsquo;s boss pulls; open any of them. A URL with <code className="font-mono">#fight=</code> opens that
        pull straight away.
      </p>
    </form>
  );
}

function LogResults({ source }: { source: string | null }) {
  const router = useRouter();
  const lookup = useReportLookup(source);
  const focus = lookup.data?.data.focus_fight ?? null;
  const code = lookup.data?.data.code;
  const fixedFocus = useRef<string | null>(null);

  // A URL with #fight= opens that pull at once; replacing the search entry keeps Back from bouncing straight in again.
  useEffect(() => {
    if (focus === null || !code || fixedFocus.current === `${code}:${focus}`) return;
    fixedFocus.current = `${code}:${focus}`;
    router.replace(pullPagePath(code, focus));
  }, [focus, code, router]);

  if (!source) return <BeforeSearch />;
  if (lookup.isPending || focus !== null) return <ResultsSkeleton />;
  if (lookup.isError) {
    const error = isApiErrorLike(lookup.error);
    return (
      <LookupError
        error={error}
        failedAt={lookup.errorUpdatedAt}
        onRetry={() => void lookup.refetch()}
        onEditCode={() => document.getElementById("raids-source")?.focus()}
      />
    );
  }
  const night = lookup.data.night;
  return (
    <section aria-labelledby="raids-results-h" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 id="raids-results-h" className="m-0 font-display text-lg leading-[1.3]">
          {summaryLine([night])}
        </h2>
        <span className="text-[13px] text-mut">Kills first; best pull marked on top</span>
      </div>
      {night.groups.length === 0 ? (
        <div role="status" className="rounded-sm border border-dashed border-line bg-s1 px-6 py-8 text-center">
          <h3 className="m-0 font-display text-lg">This report has no boss pulls</h3>
          <p className="m-0 mt-2 text-sm text-mut">WarcraftLogs lists no boss fights in it, so there is nothing to review.</p>
        </div>
      ) : (
        <NightCard night={night} />
      )}
    </section>
  );
}

function ResultsSkeleton() {
  return (
    <div aria-busy="true" aria-label="Looking up the report" className="flex flex-col gap-3">
      <SkeletonBlock width={220} height={24} />
      <SkeletonCard height={200} />
    </div>
  );
}
