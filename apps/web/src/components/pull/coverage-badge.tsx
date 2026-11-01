// apps/web/src/components/pull/coverage-badge.tsx
//
// A pull's mechanic coverage (Full, Partial, None) as a badge with a tooltip listing the top unregistered abilities:
// the damage no encounter-file entry claims, which is what lowers coverage. The tooltip's list comes from the pull's
// digest; a rail row fetches it only when its tooltip opens.

"use client";

import { useState } from "react";

import { StatusBadge } from "@/components/shared/status-badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatPct } from "@/lib/format";
import type { PullDigestData } from "@/lib/queries";

import { useLazyDigest } from "./queries";

type Coverage = "full" | "partial" | "none";

export function coverageExplanation(coverage: Coverage, unregistered: PullDigestData["unregistered"] | undefined): string {
  if (coverage === "none") return "No encounter file for this boss and difficulty, so its mechanics aren't checked.";
  if (!unregistered) return "Loading the unregistered abilities…";
  if (unregistered.length === 0) return "No unregistered abilities: every damage source on this pull is claimed.";
  return `Top unregistered abilities: ${unregistered
    .map((u) => `${u.name ?? `Ability ${u.ability_id}`} ${formatPct(u.share_pct)}`)
    .join(", ")}`;
}

export function CoverageBadge({
  code,
  fight,
  coverage,
  digest,
  prefix,
  focusable = false,
}: {
  code: string;
  fight: number;
  coverage: Coverage;
  /** The pull's digest when the caller has it; otherwise it is fetched when the tooltip opens. */
  digest?: PullDigestData;
  /** "Coverage: " in the header; the rail shows the bare word. */
  prefix?: string;
  /** The header's badge is a tab stop so the tooltip reaches the keyboard; a rail badge sits inside a link. */
  focusable?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const lazy = useLazyDigest(code, fight, !digest && open && coverage !== "none");
  const unregistered = (digest ?? lazy.data)?.unregistered;
  const status = `coverage-${coverage}` as const;
  return (
    <Tooltip open={open} onOpenChange={setOpen}>
      <TooltipTrigger asChild>
        <span tabIndex={focusable ? 0 : undefined} data-testid="coverage-badge" className="inline-flex">
          <StatusBadge status={status}>{prefix ? `${prefix}${coverage}` : undefined}</StatusBadge>
        </span>
      </TooltipTrigger>
      <TooltipContent>{coverageExplanation(coverage, unregistered)}</TooltipContent>
    </Tooltip>
  );
}
