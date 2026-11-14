// apps/web/src/components/account/account-menu.tsx
//
// The signed-in account menu (canvas "Signed in, account menu open"): a panel from the account button in the top
// bar with the Warcraft Logs name, the hour's points with a progress bar and the reset time (hidden when the server
// has no budget), the logs followed live with Stop (hidden when live watch is off or nothing is followed), and
// Sign out. The budget and the watches are read when the menu opens. It is a non-modal popover rather than a
// role="menu" (the canvas draws a menu): a menu may hold only menu items, and this panel has headings, a progress bar
// and buttons. Enter or Space on the account button opens it, Tab walks Stop and Sign out, Escape closes it and
// returns focus to the button.

"use client";

import { ChevronUpIcon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useFeatures } from "@/lib/capabilities";
import { cn } from "@/lib/utils";

import { formatResetTime, pointsView } from "./points";
import {
  useBudget,
  useReportTitle,
  useSignOut,
  useSignOutEverywhere,
  useStopWatch,
  useWatches,
  type BudgetData,
  type WatchData,
} from "./use-account";

export function AccountMenu({ name }: { name: string }) {
  const features = useFeatures();
  const [open, setOpen] = useState(false);
  const budget = useBudget(open && features.budget);
  const watches = useWatches(open && features.live_watch);
  const signOut = useSignOut();
  const signOutEverywhere = useSignOutEverywhere();
  const followed = features.live_watch ? (watches.data ?? []) : [];

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="secondary"
          data-testid="account-button"
          className={cn("gap-2 py-1 pr-2.5 pl-1 text-[13px] font-medium", open && "border-info")}
        >
          <span
            aria-hidden
            className="inline-flex size-[26px] items-center justify-center rounded-sm bg-infot font-semibold text-info"
          >
            {initialOf(name)}
          </span>
          <span data-testid="account-name" className="max-w-[18ch] truncate">
            {name}
          </span>
          <ChevronUpIcon className={cn("size-3.5 transition-transform", !open && "rotate-180")} aria-hidden />
        </Button>
      </PopoverTrigger>
      <PopoverContent aria-label="Account" className="w-[360px] max-w-[calc(100vw-32px)] p-0">
        <div className="flex flex-col gap-0.5 border-b border-line px-4 py-3.5">
          <span className="text-xs text-mut">Signed in with Warcraft Logs as</span>
          <b className="text-[15px] font-semibold break-words">{name}</b>
        </div>
        {features.budget ? <PointsSection budget={budget.data} failed={budget.isError} /> : null}
        {followed.length > 0 ? (
          <section aria-labelledby="live-h" className="flex flex-col gap-1.5 border-b border-line px-4 py-3.5">
            <h2 id="live-h" className="m-0 font-sans text-[13px] font-semibold">
              Following live
            </h2>
            {followed.map((watch) => (
              <FollowedLog key={watch.report_code} watch={watch} />
            ))}
          </section>
        ) : null}
        <div className="px-4 pt-2.5 pb-3.5">
          <Button
            variant="outline"
            size="lg"
            onClick={() => signOut.mutate()}
            disabled={signOut.isPending}
            className="w-full bg-transparent"
          >
            Sign out
          </Button>
          <Button
            variant="link"
            size="sm"
            onClick={() => signOutEverywhere.mutate()}
            disabled={signOutEverywhere.isPending}
            className="mt-1.5 w-full text-mut"
            title="Signs you out on every device and removes your Warcraft Logs sign-in from KhadBot"
          >
            Sign out everywhere
          </Button>
          {signOutEverywhere.isError ? (
            <p role="alert" className="m-0 mt-1.5 text-xs text-mut">
              That didn’t work. Try again.
            </p>
          ) : null}
        </div>
      </PopoverContent>
    </Popover>
  );
}

function PointsSection({ budget, failed }: { budget: BudgetData | undefined; failed: boolean }) {
  const view = budget ? pointsView(budget.allowance) : null;
  return (
    <section aria-labelledby="pts-h" className="flex flex-col gap-1.5 border-b border-line px-4 py-3.5">
      <div className="flex items-baseline justify-between gap-2">
        <h2 id="pts-h" className="m-0 font-sans text-[13px] font-semibold">
          Warcraft Logs points
        </h2>
        {budget ? (
          <span data-testid="points-reset" className="text-xs text-mut">
            resets {formatResetTime(budget.resets_at)}
          </span>
        ) : null}
      </div>
      {view ? (
        <>
          <span
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={view.limit}
            aria-valuenow={view.valueNow}
            aria-label="Points left this hour"
            className="relative block h-1.5 overflow-hidden rounded-[1px] bg-s3"
          >
            <span className="absolute inset-y-0 left-0 bg-info" style={{ width: `${view.fraction * 100}%` }} />
          </span>
          <span data-testid="points-left" data-state={view.state} className="text-[13px]">
            <b className="font-semibold">{formatPointsLeft(view.valueNow)}</b> of {formatPointsLeft(view.limit)} left this hour
          </span>
          <span className="text-xs text-mut">
            Pulls you open load with your own Warcraft Logs account. When your share runs out, new pulls wait for the
            reset.
          </span>
        </>
      ) : (
        <span className="text-xs text-mut">
          {failed ? "Points are not available right now." : "Reading your points…"}
        </span>
      )}
    </section>
  );
}

function formatPointsLeft(value: number): string {
  return value.toLocaleString("en-US");
}

function FollowedLog({ watch }: { watch: WatchData }) {
  const stop = useStopWatch();
  const title = useReportTitle(watch.report_code).data;
  return (
    <div className="flex items-center gap-2.5 text-[13px]" data-testid="followed-log">
      <span aria-hidden className="size-2 flex-none bg-bad" />
      <span className="flex min-w-0 flex-1 flex-col">
        <b className="font-medium">{title || watch.report_code}</b>
        <span className="font-mono text-[11px] leading-[1.4] text-mut">
          {watch.report_code} &middot; new pulls appear as they&rsquo;re logged
        </span>
      </span>
      <Button
        variant="secondary"
        size="sm"
        onClick={() => stop.mutate(watch.report_code)}
        disabled={stop.isPending}
        aria-label={`Stop following ${title || watch.report_code}`}
      >
        Stop
      </Button>
    </div>
  );
}

function initialOf(name: string): string {
  return (name.trim()[0] ?? "?").toUpperCase();
}
