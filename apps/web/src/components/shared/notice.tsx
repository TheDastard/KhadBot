// apps/web/src/components/shared/notice.tsx
//
// An inline message next to the control or content it is about ("Copied", "Your session ended; sign in again"),
// announced politely with role="status". The dashboard has no toasts: a notice stays where the reader is looking.

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type NoticeTone = "neutral" | "ok" | "warn" | "bad" | "info";

const TONE: Record<NoticeTone, string> = {
  neutral: "text-mut",
  ok: "text-ok",
  warn: "text-warn",
  bad: "text-bad",
  info: "text-info",
};

export interface NoticeProps {
  children: ReactNode;
  tone?: NoticeTone;
  /** Draw it as a tinted box rather than a line of text (a notice above the page content). */
  boxed?: boolean;
  className?: string;
}

const BOX: Record<NoticeTone, string> = {
  neutral: "bg-s2 border-line",
  ok: "bg-okt border-ok/40",
  warn: "bg-warnt border-warn/40",
  bad: "bg-badt border-bad/40",
  info: "bg-infot border-info/40",
};

export function Notice({ children, tone = "neutral", boxed = false, className }: NoticeProps) {
  return (
    <p
      role="status"
      aria-live="polite"
      className={cn(
        "m-0 text-[13px]",
        boxed ? cn("rounded-sm border px-3 py-2 text-ink", BOX[tone]) : TONE[tone],
        className,
      )}
    >
      {children}
    </p>
  );
}
