// apps/web/src/components/raids/guild/search-parts.tsx
//
// What the guild and the character search share besides the server field: the URL they keep their search in, the
// loading skeleton, the notes under a result (capped, left out) and the error cards. The not-found cards and the
// empty-result card are not on the canvas; they are built as ErrorState cards in its voice (listed in the task report).

"use client";

import type { ApiError } from "@khadbot/api-client";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { ErrorState } from "@/components/shared/error-state";
import { SkeletonBlock, SkeletonCard } from "@/components/shared/skeletons";

import { LookupError } from "../lookup-error";
import { pluralize } from "../night";

/** Reads the search params and pushes a new search (`tab` plus the given fields) onto the history. */
export function useSearchUrl(tab: "guild" | "character") {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  return {
    get: (name: string) => params.get(name)?.trim() || null,
    push(fields: Record<string, string | number | null | undefined>) {
      const q = new URLSearchParams({ tab });
      for (const [key, value] of Object.entries(fields)) if (value !== null && value !== undefined && value !== "") q.set(key, String(value));
      router.push(`${pathname}?${q.toString()}`);
    },
  };
}

export function ResultsSkeleton({ label }: { label: string }) {
  return (
    <div role="status" aria-busy="true" aria-label={label} className="flex flex-col gap-3">
      <SkeletonBlock width={220} height={24} />
      <SkeletonCard height={200} />
    </div>
  );
}

/** "Showing the newest N reports" and "N reports are left out": facts about the list the canvas has no line for. */
export function ResultNotes({ capped, leftOut, shown }: { capped: boolean; leftOut: number; shown: number }) {
  const lines = [
    capped ? `Showing the newest ${pluralize(shown + leftOut, "report")}; there are more than one search lists.` : null,
    leftOut > 0 ? `${pluralize(leftOut, "report")} left out: private to others, or WarcraftLogs could not load them.` : null,
  ].filter(Boolean);
  if (lines.length === 0) return null;
  return (
    <p data-testid="result-notes" className="m-0 text-[13px] text-mut">
      {lines.join(" ")}
    </p>
  );
}

export interface SearchSubject {
  kind: "guild" | "character";
  name: string;
  /** "Skullcrusher (US)" */
  server: string;
}

export function SearchError({
  error,
  failedAt,
  subject,
  onRetry,
  onEdit,
}: {
  error: ApiError;
  failedAt: number;
  subject: SearchSubject;
  onRetry: () => void;
  onEdit: () => void;
}) {
  const edit = { label: "Edit the search", onClick: onEdit };
  switch (error.code) {
    case "guild_not_found":
      return (
        <ErrorState
          tone="bad"
          where="Search"
          code={error.code}
          title="Guild not found"
          body={`WarcraftLogs has no guild called “${subject.name}” on ${subject.server}. Check the spelling and the server.`}
          action={edit}
        />
      );
    case "character_not_found":
      return (
        <ErrorState
          tone="bad"
          where="Search"
          code={error.code}
          title="Character not found"
          body={`WarcraftLogs has no character called “${subject.name}” on ${subject.server}. Check the spelling and the server.`}
          action={edit}
        />
      );
    case "server_unknown":
      return (
        <ErrorState
          tone="bad"
          where="Search"
          code={error.code}
          title="That server isn't on the list"
          body="Pick a server from the list as you type; it fills in the region too."
          action={edit}
        />
      );
    case "bad_guild_url":
      return (
        <ErrorState
          tone="bad"
          where="Search"
          code={error.code}
          title="That isn't a WarcraftLogs guild link"
          body="Paste a link like warcraftlogs.com/guild/us/server/guild-name, or type the server and the guild."
          action={edit}
        />
      );
    default:
      // Busy, can't connect, not responding: the same cards as the log search.
      return <LookupError error={error} failedAt={failedAt} onRetry={onRetry} onEditCode={onEdit} />;
  }
}

/** A search that worked and found nothing to show. */
export function NothingFound({ title, body, onEdit }: { title: string; body: string; onEdit: () => void }) {
  return (
    <ErrorState tone="info" where="Search" title={title} body={body} action={{ label: "Edit the search", onClick: onEdit }} />
  );
}
