// apps/web/src/components/shared/status-badge.tsx
//
// Every status the dashboard shows, as a word (and an icon where the canvas draws one) on its tint, so it reads in
// greyscale: the canvas's "Keyboard and status" page, "Every status has a word". Loading and job states use the
// player words the plan settled (Waiting, Loading, Ready, Stopped), not the canvas's older ingest-stage words.

import { CheckIcon, ClockIcon, XIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

type Tone = "ok" | "warn" | "bad" | "info" | "mut" | "ink";
type Frame = "fill" | "line" | "dashed";
type Icon = "check" | "cross" | "clock";

interface StatusSpec {
  label: string;
  tone: Tone;
  frame?: Frame;
  icon?: Icon;
}

/** Each status, its word, tone and icon. The keys are what callers pass. */
export const STATUSES = {
  // Outcome of a pull
  kill: { label: "Kill", tone: "ok", icon: "check" },
  wipe: { label: "Wipe", tone: "mut", frame: "line" },
  // Mechanic coverage of a pull
  "coverage-full": { label: "Full", tone: "ok" },
  "coverage-partial": { label: "Partial", tone: "warn" },
  "coverage-none": { label: "None", tone: "mut", frame: "dashed" },
  // A damage window
  covered: { label: "Covered", tone: "ok", icon: "check" },
  missed: { label: "Missed", tone: "bad", icon: "cross" },
  // A death against the cutoff
  "before-cutoff": { label: "Before cutoff", tone: "bad" },
  "after-cutoff": { label: "After cutoff", tone: "mut" },
  // A cached finding
  "confidence-high": { label: "High confidence", tone: "ink" },
  "confidence-medium": { label: "Medium confidence", tone: "ink" },
  "confidence-low": { label: "Low confidence", tone: "ink" },
  "out-of-date": { label: "Out of date", tone: "warn", icon: "clock" },
  // One step of opening a pull (JobStep.state)
  "step-waiting": { label: "Waiting", tone: "mut" },
  "step-running": { label: "Running", tone: "info" },
  "step-done": { label: "Done", tone: "ok", icon: "check" },
  "step-failed": { label: "Failed", tone: "bad", icon: "cross" },
  "step-skipped": { label: "Skipped", tone: "mut" },
  // A pull or night being loaded, as a player sees it
  waiting: { label: "Waiting", tone: "mut", icon: "clock" },
  loading: { label: "Loading", tone: "info" },
  ready: { label: "Ready", tone: "ok", icon: "check" },
  stopped: { label: "Stopped", tone: "warn" },
  failed: { label: "Failed", tone: "bad", icon: "cross" },
} as const satisfies Record<string, StatusSpec>;

export type Status = keyof typeof STATUSES;

const TONE: Record<Tone, string> = {
  ok: "text-ok bg-okt",
  warn: "text-warn bg-warnt",
  bad: "text-bad bg-badt",
  info: "text-info bg-infot",
  mut: "text-mut bg-s2",
  ink: "text-ink bg-s2 font-medium",
};

const FRAME: Record<Frame, string> = {
  fill: "",
  line: "border border-line",
  dashed: "border border-dashed border-mut bg-s1",
};

const ICONS: Record<Icon, ReactNode> = {
  check: <CheckIcon className="size-3" strokeWidth={3} aria-hidden />,
  cross: <XIcon className="size-3" strokeWidth={3} aria-hidden />,
  clock: <ClockIcon className="size-3" strokeWidth={2.5} aria-hidden />,
};

export interface StatusBadgeProps {
  status: Status;
  /** Replaces the word (a countdown, "Wipe at 5.6%"); the tone and icon stay. */
  children?: ReactNode;
  className?: string;
}

export function StatusBadge({ status, children, className }: StatusBadgeProps) {
  const spec: StatusSpec = STATUSES[status];
  return (
    <span
      data-status={status}
      className={cn(
        "inline-flex items-center gap-1 rounded-sm px-[9px] py-px text-xs leading-[1.5] font-semibold whitespace-nowrap",
        TONE[spec.tone],
        FRAME[spec.frame ?? "fill"],
        className,
      )}
    >
      {spec.icon ? ICONS[spec.icon] : null}
      {children ?? spec.label}
    </span>
  );
}

/** The badge status of a loading step's state (`JobStep.state`). */
export function stepStatus(state: "waiting" | "running" | "done" | "failed" | "skipped"): Status {
  return `step-${state}`;
}
