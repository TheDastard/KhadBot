// apps/web/src/components/events/zoomed-events.tsx
//
// "Events m:ss – m:ss": the rows of the zoomed window (`?t=`, 30 s at most), under the timeline. The header names the
// filter ("Kouw · damage and death"), "N of M rows" and Clear zoom; the table is time, type, ability (with its icon
// through AbilityName), source, amount, HP after and flag, in its own overflow box. Raw events are rows only, never
// marks on the timeline.
//
// The filter starts as the selected death's player (when a death is selected) and damage and deaths; each can be
// switched off here. It isn't in the URL: `t` is the only part of the zoom the URL holds.

"use client";

import { isApiError } from "@khadbot/api-client";
import { useState } from "react";

import { AbilityName, IconFallback } from "@/components/shared/ability-name";
import { ErrorState } from "@/components/shared/error-state";
import { Notice } from "@/components/shared/notice";
import { PlayerName } from "@/components/shared/player-name";
import { SkeletonBlock } from "@/components/shared/skeletons";
import type { TimeWindow } from "@/lib/focus";
import { formatClockTenths, formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";
import { usePullEvents } from "@/components/timeline/queries";

import { asEventRows, DEFAULT_TYPES, rowCount, rowFlag, windowTitle, type EventRow } from "./event-rows";

export interface ZoomedEventsProps {
  code: string;
  fight: number;
  window: TimeWindow;
  /** A player to filter to (the selected death's), or null. */
  actor: string | null;
  onClear: () => void;
}

const TH = "px-2 py-1.5 text-left text-xs font-semibold text-mut";

export function ZoomedEvents({ code, fight, window, actor, onClear }: ZoomedEventsProps) {
  const [everyone, setEveryone] = useState(false);
  const [allTypes, setAllTypes] = useState(false);
  const useActor = actor !== null && !everyone;
  const query = usePullEvents(code, fight, {
    fromMs: window.startMs,
    toMs: window.endMs,
    actor: useActor ? actor : null,
    types: allTypes ? null : [...DEFAULT_TYPES],
  });
  const rows = query.data ? asEventRows(query.data.rows) : [];

  return (
    <section
      aria-labelledby="events-heading"
      data-testid="zoomed-events"
      className="flex min-w-0 flex-col gap-2.5 rounded-sm border border-line bg-s1 px-4 py-3.5"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1.5">
        <h2 id="events-heading" className="m-0 text-base leading-[1.3]">
          Events {windowTitle(window.startMs, window.endMs)}
        </h2>
        <span className="flex flex-wrap items-center gap-2 text-xs text-mut">
          {useActor && actor && <PlayerName name={actor} code={code} />}
          {useActor && <span aria-hidden="true">·</span>}
          <span>{allTypes ? "all event types" : "damage and death"}</span>
          <span aria-hidden="true">·</span>
          <span data-testid="row-count" role="status">
            {query.data ? rowCount(rows.length, query.data.total) : "Loading rows"}
          </span>
          {actor !== null && (
            <ToggleButton pressed={useActor} onClick={() => setEveryone(useActor)}>
              {`Only ${actor}`}
            </ToggleButton>
          )}
          <ToggleButton pressed={!allTypes} onClick={() => setAllTypes(!allTypes)}>
            Damage and deaths only
          </ToggleButton>
          <button
            type="button"
            onClick={onClear}
            className="rounded-sm border border-line bg-s2 px-[9px] py-[3px] text-xs text-ink hover:bg-s3"
          >
            Clear zoom
          </button>
        </span>
      </div>

      {query.isError ? (
        <ErrorState
          tone="bad"
          where="Events"
          code={isApiError(query.error) ? query.error.code : "unexpected_error"}
          title="These events couldn't be shown"
          body={(query.error as Error).message}
          action={{ label: "Try again", onClick: () => void query.refetch() }}
        />
      ) : query.isPending ? (
        <SkeletonBlock height={220} />
      ) : (
        <>
          {query.data.truncated && query.data.note && <Notice tone="warn">{query.data.note}</Notice>}
          {rows.length === 0 ? (
            <Notice>No events in this window{useActor || !allTypes ? " with this filter" : ""}.</Notice>
          ) : (
            <EventsTable code={code} rows={rows} />
          )}
        </>
      )}
    </section>
  );
}

function ToggleButton({ pressed, onClick, children }: { pressed: boolean; onClick: () => void; children: string }) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      onClick={onClick}
      className={cn(
        "rounded-sm border px-[9px] py-[3px] text-xs text-ink hover:bg-s3",
        pressed ? "border-info bg-infot" : "border-line bg-s2",
      )}
    >
      {children}
    </button>
  );
}

function EventsTable({ code, rows }: { code: string; rows: EventRow[] }) {
  return (
    <div className="max-h-[440px] overflow-auto" tabIndex={0} role="region" aria-label="Event rows">
      <table className="w-full min-w-[620px] border-collapse text-[13px]">
        <thead className="sticky top-0 bg-s1">
          <tr>
            <th scope="col" className={TH}>
              Time
            </th>
            <th scope="col" className={TH}>
              Type
            </th>
            <th scope="col" className={TH}>
              Ability
            </th>
            <th scope="col" className={TH}>
              Source
            </th>
            <th scope="col" className={cn(TH, "text-right")}>
              Amount
            </th>
            <th scope="col" className={cn(TH, "text-right")}>
              HP after
            </th>
            <th scope="col" className={TH}>
              Flag
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const flag = rowFlag(row);
            return (
              <tr key={row.ref} data-ref={row.ref} className={cn("border-t border-line", row.type === "death" && "bg-acct")}>
                <td className="px-2 py-1.5 font-mono">{formatClockTenths(row.t_ms)}</td>
                <td className="px-2 py-1.5">{row.type}</td>
                <td className="px-2 py-1.5">
                  {row.ability ? (
                    <AbilityName spellId={row.ability_id} name={row.ability} />
                  ) : (
                    <span className="inline-flex items-baseline gap-1.5 text-mut">
                      <IconFallback size={16} />—
                    </span>
                  )}
                </td>
                <td className="px-2 py-1.5">
                  {row.source ? <PlayerName name={row.source} code={code} className="font-normal" /> : <span className="text-mut">—</span>}
                  {row.target && row.target !== row.source && (
                    <span className="block text-xs text-mut">to {row.target}</span>
                  )}
                </td>
                <td className="px-2 py-1.5 text-right">{row.amount == null ? "" : formatNumber(row.amount)}</td>
                <td className="px-2 py-1.5 text-right">{row.hp_pct == null ? "" : `${row.hp_pct.toFixed(1)}%`}</td>
                <td className="px-2 py-1.5">
                  {flag.tone === "bad" ? (
                    <span className="rounded-sm bg-badt px-2 text-xs font-semibold text-bad">{flag.text}</span>
                  ) : (
                    <span className="text-xs text-mut">{flag.text}</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
