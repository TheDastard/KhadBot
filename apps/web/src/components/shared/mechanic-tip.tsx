// apps/web/src/components/shared/mechanic-tip.tsx
//
// A mechanic (an encounter-file ID) as the pull page shows it: KhadBot's own tooltip, never Wowhead's. Spells keep
// Wowhead's tooltip through AbilityName; one element carries one tooltip, chosen by what it names, and no tooltip
// holds a link (it closes when the pointer leaves its trigger).
//
// - MechanicTooltip: the compact ink tooltip (name, description, the failure rule in plain words) on any focusable
//   trigger, opening on hover and keyboard focus. Without a description or a rule it adds nothing.
// - MechanicName: a mechanic's name as a hover and focus target (dotted underline: not a link), with its icon.
// - MechanicIcon: the mechanic's icon, a plain image of its icon ability. No route serves an icon image the browser
//   may load for a spell ID outside Wowhead's script (docs/game-art.md), so it is the neutral square for now, marked
//   with the ability ID it stands for.
//
//   <MechanicTooltip facts={facts}><a ...>mech</a></MechanicTooltip>

"use client";

import type { ReactElement } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

import { IconFallback } from "./ability-name";

/** What the pull page knows about one mechanic, from the encounter's names lookup. */
export interface MechanicFacts {
  id: string;
  name: string;
  /** The encounter file's plain-text description; null when it has none. */
  description: string | null;
  /** The failure rule in plain words; null without a rule (or without the lookup). */
  failureSummary: string | null;
  /** The game's spell IDs of its abilities; empty without the lookup. */
  abilityIds: number[];
  /** The ability that stands for it as an icon; null without the lookup. */
  iconAbilityId: number | null;
  kind: string | null;
  severity: "low" | "medium" | "high" | null;
}

/** True when the tooltip would say more than the name. */
export function hasMechanicTip(facts: MechanicFacts): boolean {
  return !!(facts.description || facts.failureSummary);
}

/** The compact tooltip's lines: the name, the description, then the rule muted. */
export function MechanicTipBody({ facts }: { facts: MechanicFacts }) {
  return (
    <span className="flex flex-col gap-0.5" data-mechanic-tip={facts.id}>
      <b className="font-semibold">{facts.name}</b>
      {facts.description ? <span>{facts.description}</span> : null}
      {facts.failureSummary ? <span className="opacity-80">{facts.failureSummary}</span> : null}
    </span>
  );
}

export function MechanicTooltip({
  facts,
  children,
  side = "top",
}: {
  facts: MechanicFacts;
  /** One focusable element that takes a ref and props (Radix's asChild). */
  children: ReactElement;
  side?: "top" | "bottom" | "left" | "right";
}) {
  if (!hasMechanicTip(facts)) return children;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side={side} className="w-80">
        <MechanicTipBody facts={facts} />
      </TooltipContent>
    </Tooltip>
  );
}

export function MechanicIcon({
  facts,
  size = 16,
  className,
}: {
  facts: MechanicFacts;
  size?: 16 | 22;
  className?: string;
}) {
  return (
    <span className={cn("inline-flex shrink-0", className)} data-icon-ability-id={facts.iconAbilityId ?? undefined}>
      <IconFallback size={size} />
    </span>
  );
}

/** A mechanic's name with its icon; a focusable tooltip target when there is more to say than the name. */
export function MechanicName({ facts, className }: { facts: MechanicFacts; className?: string }) {
  const tip = hasMechanicTip(facts);
  return (
    <span className={cn("inline-flex min-w-0 items-baseline gap-1.5", className)}>
      <MechanicIcon facts={facts} className="self-center" />
      <MechanicTooltip facts={facts}>
        <span
          tabIndex={tip ? 0 : undefined}
          className={cn("min-w-0 truncate", tip && "cursor-help border-b border-dotted border-mut")}
        >
          {facts.name}
        </span>
      </MechanicTooltip>
    </span>
  );
}
