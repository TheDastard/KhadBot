// apps/web/src/components/panel/parts.tsx
//
// The pieces every panel view is built from. A view renders a PanelLayout (the kind label, a title, the full ref and
// an optional close button) around its rows, and nothing in a view knows the panel's frame: the desktop panel is a
// sticky 380 px aside today, and the phone follow-up can put the same views in a bottom sheet.

import { XIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function PanelLayout({
  kind,
  title,
  evidenceRef,
  onClose,
  children,
}: {
  /** The ref kind in small mono capitals: "death", "mech", "cd", "win", "event", "pull", "plan", "error", "loading". */
  kind: string;
  title?: ReactNode;
  /** The full ref, shown under the title. */
  evidenceRef?: string;
  onClose?: () => void;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3" data-panel-kind={kind}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 flex-col gap-1">
          <span className="font-mono text-[11px] leading-snug font-medium tracking-[0.06em] text-acc uppercase">{kind}</span>
          {title ? <h2 className="m-0 font-display text-[19px] leading-tight font-semibold">{title}</h2> : null}
        </div>
        {onClose ? (
          <button
            type="button"
            aria-label="Close panel"
            onClick={onClose}
            className="inline-flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-sm border border-line bg-s2 text-ink hover:bg-s3"
          >
            <XIcon className="size-3.5" strokeWidth={2.4} aria-hidden />
          </button>
        ) : null}
      </div>
      {evidenceRef ? (
        <code className="font-mono text-[11px] leading-snug break-words text-mut [overflow-wrap:anywhere]">{evidenceRef}</code>
      ) : null}
      {children}
    </div>
  );
}

/** Label and value rows in a two-column list (value right-aligned). */
export function Rows({ children }: { children: ReactNode }) {
  return <dl className="m-0 grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1.5 text-[13px]">{children}</dl>;
}

export function Row({ label, children, strong }: { label: string; children: ReactNode; strong?: boolean }) {
  return (
    <>
      <dt className="text-mut">{label}</dt>
      <dd className={cn("m-0 text-right", strong && "font-semibold")}>{children}</dd>
    </>
  );
}

export function Tag({ children, tone = "plain" }: { children: ReactNode; tone?: "plain" | "bad" | "warn" }) {
  return (
    <span
      className={cn(
        "rounded-sm px-2 text-xs",
        tone === "plain" && "border border-line bg-s2",
        tone === "bad" && "bg-badt font-medium text-bad",
        tone === "warn" && "bg-warnt font-medium text-warn",
      )}
    >
      {children}
    </span>
  );
}

export function Muted({ children }: { children: ReactNode }) {
  return <span className="text-xs text-mut">{children}</span>;
}
