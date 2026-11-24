// apps/web/src/components/shared/evidence-chip.tsx
//
// A link to a piece of evidence: it opens the ref's pull page with `?ref=` set (the context panel shows the record),
// keeping the current `full` setting. The visible label is short ("P2 · death 2"); the full ref is the link's title
// and the end of its accessible name, which starts with the visible text (WCAG 2.5.3). A plan ref stays on the
// current page. Every chip is a link: Tab reaches it, Enter follows. A chip for the page it is on changes the focus
// with a shallow history push (lib/focus.ts), not a router navigation, so the panel opens without a server round
// trip; a chip for another pull navigates. The chip re-renders only when its href or its selection changes. Other
// props (a tooltip trigger's handlers, `aria-describedby`, its ref) pass through to the link, so a chip can be a
// Radix `asChild` trigger.
//
//   <EvidenceChip evidenceRef="ev:H7z1fqDXjRTFnbtg:15:death:2" label="P2 · death 2" />

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ComponentProps, ReactNode } from "react";

import { focusHref, pushFocusHref, useFocusBaseSearch } from "@/lib/focus";
import { parseRef, pullPath, refShortLabel } from "@/lib/refs";
import { cn } from "@/lib/utils";

export interface EvidenceChipProps extends Omit<ComponentProps<typeof Link>, "href" | "children"> {
  evidenceRef: string;
  /** The visible text; defaults to the ref's kind and number ("death 2"). */
  label?: string;
  /** Marks the chip whose ref is open in the panel. */
  selected?: boolean;
  className?: string;
  /** Drawn before the label (a mechanic's icon). */
  icon?: ReactNode;
}

/** The href a chip leads to: the ref's pull page (or the current page for plan refs) with `ref` set, `t` cleared. */
export function evidenceHref(evidenceRef: string, pathname: string, current: URLSearchParams): string {
  const parsed = parseRef(evidenceRef);
  const target = (parsed && pullPath(parsed)) ?? pathname;
  const keep = target === pathname ? current : new URLSearchParams(current.get("full") === "1" ? "full=1" : "");
  return focusHref(target, keep, { ref: evidenceRef, t: null });
}

export function EvidenceChip({ evidenceRef, label, selected = false, className, icon, ...rest }: EvidenceChipProps) {
  const pathname = usePathname();
  const base = useFocusBaseSearch();
  const parsed = parseRef(evidenceRef);
  const text = label ?? (parsed ? refShortLabel(parsed) : evidenceRef);
  const href = evidenceHref(evidenceRef, pathname, new URLSearchParams(base));
  return (
    <Link
      {...rest}
      href={href}
      scroll={false}
      onNavigate={(event) => {
        rest.onNavigate?.(event);
        if (new URL(href, window.location.href).pathname !== pathname) return;
        event.preventDefault();
        pushFocusHref(href);
      }}
      title={evidenceRef}
      aria-label={`${text}: open evidence ${evidenceRef}`}
      aria-current={selected ? "true" : undefined}
      data-ref={evidenceRef}
      className={cn(
        "inline-flex min-h-[26px] items-center gap-[5px] rounded-sm border border-info bg-infot px-[9px] py-0.5 text-xs text-ink no-underline",
        "hover:bg-info/15",
        selected && "shadow-[0_0_0_2px_var(--acc)]",
        className,
      )}
    >
      {icon}
      {text}
    </Link>
  );
}
