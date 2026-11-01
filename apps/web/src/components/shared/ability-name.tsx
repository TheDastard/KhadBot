// apps/web/src/components/shared/ability-name.tsx
//
// A boss ability or spell by name: a link to its Wowhead page, which Wowhead's script (shared/wowhead.tsx) turns into
// a tooltip with the spell's icon on hover and focus, as on Warcraft Logs. Until the script has loaded (or when it
// can't, offline or in the tests) the name has a 16 px neutral square before it and a tooltip with the name and
// spell ID. An ability with no spell ID (Melee from the environment, an unknown ability) is plain text with the square.
//
//   <AbilityName spellId={1234} name="Caustic Waves" />

"use client";

import { useEffect } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { wowheadSpellUrl } from "@/lib/wowhead";

import { refreshWowheadLinks, useWowheadReady } from "./wowhead";

export interface AbilityNameProps {
  /** The game's spell ID (WCL's ability game ID); null when unknown. */
  spellId: number | null | undefined;
  name: string;
  /** Icon size: 16 px beside text (the default), 22 px beside a KPI value. */
  size?: 16 | 22;
  className?: string;
}

/** The neutral square shown where a spell icon would be. */
export function IconFallback({ size = 16, className }: { size?: 16 | 22; className?: string }) {
  return (
    <span
      aria-hidden
      data-icon-fallback=""
      className={cn("inline-block shrink-0 rounded-sm border border-line bg-s3 align-[-3px]", className)}
      style={{ width: size, height: size }}
    />
  );
}

export function AbilityName({ spellId, name, size = 16, className }: AbilityNameProps) {
  const ready = useWowheadReady();
  const hasId = typeof spellId === "number" && spellId > 0;

  useEffect(() => {
    if (ready && hasId) refreshWowheadLinks();
  }, [ready, hasId, spellId, name]);

  if (!hasId) {
    return (
      <span className={cn("inline-flex items-baseline gap-1.5", className)}>
        <IconFallback size={size} />
        <span>{name}</span>
      </span>
    );
  }

  const link = (
    <a
      href={wowheadSpellUrl(spellId)}
      target="_blank"
      rel="noopener noreferrer"
      data-wh-icon-size={size === 22 ? "medium" : "small"}
      data-ability-id={spellId}
      className="text-ink underline decoration-line underline-offset-2 hover:decoration-info"
    >
      {name}
    </a>
  );

  if (ready) {
    return <span className={cn("inline-flex items-baseline gap-1.5", className)}>{link}</span>;
  }
  return (
    <span className={cn("inline-flex items-baseline gap-1.5", className)}>
      <IconFallback size={size} />
      <Tooltip>
        <TooltipTrigger asChild>{link}</TooltipTrigger>
        <TooltipContent>
          <b className="font-semibold">{name}</b> · spell {spellId}
        </TooltipContent>
      </Tooltip>
    </span>
  );
}
