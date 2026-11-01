// apps/web/src/components/pull/section-card.tsx
//
// The card every main-column section sits in (the canvas's surface-1 card, a serif heading and an optional note on
// the right) and the pieces its tables share. Tables scroll in their own box so the page never scrolls sideways.

import { useId, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export function SectionCard({
  title,
  note,
  children,
  className,
  testId,
}: {
  title: string;
  note?: ReactNode;
  children: ReactNode;
  className?: string;
  testId?: string;
}) {
  const id = useId();
  return (
    <section
      aria-labelledby={id}
      data-testid={testId}
      className={cn("flex min-w-0 flex-col gap-2 rounded-sm border border-line bg-s1 px-4 py-3.5", className)}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 id={id} className="m-0 font-display text-base leading-snug font-semibold">
          {title}
        </h2>
        {note ? <span className="text-xs text-mut">{note}</span> : null}
      </div>
      {children}
    </section>
  );
}

/** A table that scrolls inside its own box when the column is narrower than `minWidth`. */
export function TableBox({ minWidth, label, children }: { minWidth: number; label: string; children: ReactNode }) {
  return (
    <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={label}>
      <table className="w-full border-collapse text-[13px]" style={{ minWidth }}>
        {children}
      </table>
    </div>
  );
}

export function Th({ children, right, hidden }: { children: ReactNode; right?: boolean; hidden?: boolean }) {
  return (
    <th scope="col" className={cn("px-2 py-1.5 text-xs font-semibold text-mut", right ? "text-right" : "text-left")}>
      {hidden ? <span className="sr-only">{children}</span> : children}
    </th>
  );
}

export function Mono({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("font-mono", className)}>{children}</span>;
}
