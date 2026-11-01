// apps/web/src/components/open-pull/opening-pull.tsx
//
// "Opening a pull, loading": the screen while one pull loads (canvas: Opening a pull, loading; Errors). Presentation
// only: the tile, the heading, the status line (aria-live), the three steps and the way back, or an error card in
// place of the status line and steps. pull-not-loaded.tsx feeds it from the job's event stream.

"use client";

import { CheckIcon, XIcon } from "lucide-react";
import Link from "next/link";

import { ErrorState } from "@/components/shared/error-state";
import { FightPct, FightPctBar } from "@/components/shared/fight-pct";
import { Button } from "@/components/ui/button";
import { formatClock, formatCountdown } from "@/lib/format";
import { cn } from "@/lib/utils";

import { errorCard, failedStepCard, type CardAction, type ErrorCard } from "./error-cards";
import { STEP_COPY, statusLine, type JobStep, type Phase, type StepState } from "./job-events";

/** What the tile and heading show about the pull, from the report lookup (or the job, when the lookup has nothing). */
export interface PullTileInfo {
  encounter: string | null;
  difficulty: string | null;
  /** "P3" */
  label: string | null;
  /** WCL's fight % left, null when unknown. */
  pct: number | null;
  kill: boolean;
  durationMs: number | null;
}

export interface OpeningPullProps {
  code: string;
  fight: number;
  tile: PullTileInfo | null;
  steps: JobStep[];
  phase: Phase;
  /** Seconds left of a wait or retry, null without an estimate. */
  countdownS: number | null;
  /** Start the pull again (POST again). */
  onStartAgain: () => void;
  /** Where "Sign in with Warcraft Logs" goes. */
  signInHref: string;
}

const DIFFICULTY_WORD: Record<string, string> = { lfr: "LFR", normal: "Normal", heroic: "Heroic", mythic: "Mythic" };

export function difficultyWord(name: string | null | undefined): string | null {
  return name ? (DIFFICULTY_WORD[name] ?? name) : null;
}

/** "Ula'tek · Heroic · P3", or "Fight 12" when nothing is known yet. */
export function tileHeading(fight: number, tile: PullTileInfo | null): string {
  const parts = [tile?.encounter, tile?.difficulty, tile?.label].filter(Boolean);
  return parts.length ? parts.join(" · ") : `Fight ${fight}`;
}

/** "H7z1fqDXjRTFnbtg · fight 12 · wipe at 24.1% · 8:01" */
export function tileMonoLine(code: string, fight: number, tile: PullTileInfo | null): string {
  const parts = [code, `fight ${fight}`];
  if (tile?.pct != null || tile?.kill) {
    parts.push(tile.kill ? "kill" : `wipe at ${tile.pct!.toFixed(1)}%`);
  }
  if (tile?.durationMs != null) parts.push(formatClock(tile.durationMs));
  return parts.join(" · ");
}

export function OpeningPull(props: OpeningPullProps) {
  const { code, fight, tile, steps, phase, countdownS, onStartAgain, signInHref } = props;
  const countdown = countdownS === null ? null : formatCountdown(countdownS);
  const card = cardFor(phase, steps);
  return (
    <main
      data-slot="pull-not-loaded"
      data-phase={phase.kind}
      className="mx-auto flex w-full max-w-[1040px] flex-col gap-[22px] px-8 pt-8 pb-16"
    >
      <div className="flex flex-col gap-3">
        <span className="font-mono text-xs leading-none font-medium text-mut">Opening a pull</span>
        <div className="flex min-w-0 items-center gap-3.5">
          <Tile tile={tile} />
          <div className="flex min-w-0 flex-col gap-1">
            <h1 className="m-0 font-display text-[28px] leading-[1.2] font-semibold [overflow-wrap:anywhere]">
              {tileHeading(fight, tile)}
            </h1>
            <span className="font-mono text-xs leading-[1.4] text-mut [overflow-wrap:anywhere]">
              {tileMonoLine(code, fight, tile)}
            </span>
          </div>
        </div>
        {card ? null : (
          <p role="status" aria-live="polite" className="m-0 text-[15px]">
            {statusLine(phase, countdown)}
          </p>
        )}
      </div>

      {card ? (
        <ErrorCard_
          card={card.card}
          countdownS={countdownS}
          onStartAgain={onStartAgain}
          signInHref={signInHref}
        />
      ) : null}

      <section
        aria-labelledby="open-pull-steps"
        className="flex flex-col gap-1 rounded-sm border border-line bg-s1 px-4 py-3.5"
      >
        <h2 id="open-pull-steps" className="m-0 mb-1.5 font-display text-base leading-[1.3] font-semibold">
          Steps
        </h2>
        <ol className="m-0 flex list-none flex-col p-0">
          {steps.map((step) => (
            <StepRow key={step.name} step={step} />
          ))}
        </ol>
      </section>

      <div className="flex flex-wrap items-center gap-2.5">
        <Button asChild variant="outline" size="lg">
          <Link href="/">Back to the search</Link>
        </Button>
        <span className="text-[13px] text-mut">You can leave; it keeps loading, and opens straight away next time.</span>
      </div>
    </main>
  );
}

// ----------------------------------------------------------------------------------------------------------------
// The tile
// ----------------------------------------------------------------------------------------------------------------

/** The 72 px tile: ordinal, fight % and the bar over a plain surface (the boss icon waits for game art). */
function Tile({ tile }: { tile: PullTileInfo | null }) {
  return (
    <span
      data-testid="pull-tile"
      className="relative block size-[72px] flex-none overflow-hidden rounded-sm border border-line bg-s2"
    >
      {tile?.label ? (
        <span className="absolute top-1 left-[5px] font-mono text-[11px] leading-none font-semibold text-ink">
          {tile.label}
        </span>
      ) : null}
      {tile?.pct != null || tile?.kill ? (
        <>
          <FightPct
            pct={tile.pct ?? 0}
            kill={tile.kill}
            className="absolute right-[5px] bottom-[9px] text-[13px] leading-none"
          />
          <FightPctBar
            pct={tile.pct ?? 0}
            kill={tile.kill}
            height={4}
            decorative
            className="absolute inset-x-0 bottom-0 rounded-none"
          />
        </>
      ) : null}
    </span>
  );
}

// ----------------------------------------------------------------------------------------------------------------
// Steps
// ----------------------------------------------------------------------------------------------------------------

const STATE_WORD: Record<StepState, string> = {
  waiting: "Waiting",
  running: "Running",
  done: "Done",
  failed: "Failed",
  skipped: "Skipped",
};

const STATE_TONE: Record<StepState, { text: string; well: string; bar: string }> = {
  waiting: { text: "text-mut", well: "text-mut bg-s2", bar: "bg-mut" },
  running: { text: "text-info", well: "text-info bg-infot", bar: "bg-info" },
  done: { text: "text-ok", well: "text-ok bg-okt", bar: "bg-ok" },
  failed: { text: "text-bad", well: "text-bad bg-badt", bar: "bg-bad" },
  skipped: { text: "text-mut", well: "text-mut bg-s2", bar: "bg-mut" },
};

function StepRow({ step }: { step: JobStep }) {
  const copy = STEP_COPY[step.name];
  const tone = STATE_TONE[step.state];
  const pct = step.state === "done" ? 100 : Math.round(step.pct);
  return (
    <li
      data-step={step.name}
      data-state={step.state}
      className="grid grid-cols-[24px_minmax(0,1fr)_110px_200px] items-center gap-3 border-t border-line py-2.5"
    >
      <span className={cn("inline-flex size-[22px] items-center justify-center rounded-sm", tone.well)}>
        {step.state === "done" ? (
          <CheckIcon className="size-3" strokeWidth={3.2} aria-hidden />
        ) : step.state === "failed" ? (
          <XIcon className="size-3" strokeWidth={3.2} aria-hidden />
        ) : (
          <span aria-hidden className="size-[3px] rounded-full bg-current" />
        )}
      </span>
      <span className="flex min-w-0 flex-col">
        <b className="font-semibold">{copy.name}</b>
        <span className="text-xs text-mut">{copy.detail}</span>
      </span>
      <span data-testid="step-state" className={cn("text-[13px] font-semibold", tone.text)}>
        {STATE_WORD[step.state]}
      </span>
      <span
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-label={`${copy.name} progress`}
        className="relative block h-1.5 overflow-hidden rounded-[1px] bg-s3"
      >
        <span className={cn("absolute inset-y-0 left-0", tone.bar)} style={{ width: `${pct}%` }} />
      </span>
    </li>
  );
}

// ----------------------------------------------------------------------------------------------------------------
// Error cards
// ----------------------------------------------------------------------------------------------------------------

function cardFor(phase: Phase, steps: JobStep[]): { card: ErrorCard } | null {
  switch (phase.kind) {
    case "busy":
      return { card: errorCard({ code: "rate_limited", message: "" }) };
    case "stopped":
      return { card: errorCard({ code: "stopped", message: "" }) };
    case "failed": {
      const stepName = phase.step ? STEP_COPY[phase.step].name : null;
      const failedStep = steps.find((s) => s.state === "failed");
      return { card: failedStepCard(phase, stepName ?? (failedStep ? STEP_COPY[failedStep.name].name : null)) };
    }
    default:
      return null;
  }
}

function ErrorCard_({
  card,
  countdownS,
  onStartAgain,
  signInHref,
}: {
  card: ErrorCard;
  countdownS: number | null;
  onStartAgain: () => void;
  signInHref: string;
}) {
  const waiting = card.action === "retry" && countdownS !== null && countdownS > 0;
  const action = actionFor(card.action, waiting, countdownS, onStartAgain, signInHref);
  return (
    <ErrorState
      tone={card.tone}
      where={card.where}
      code={card.code}
      title={card.title}
      body={card.body}
      action={action}
    />
  );
}

function actionFor(
  kind: CardAction,
  waiting: boolean,
  countdownS: number | null,
  onStartAgain: () => void,
  signInHref: string,
) {
  switch (kind) {
    case "retry":
      return waiting
        ? { label: `Retry in ${formatCountdown(countdownS ?? 0)}`, disabled: true }
        : { label: "Retry", onClick: onStartAgain };
    case "start-again":
      return { label: "Start again", onClick: onStartAgain };
    case "sign-in":
      return { label: "Sign in with Warcraft Logs", href: signInHref };
    case "back":
      return { label: "Back to Raids", href: "/" };
  }
}
