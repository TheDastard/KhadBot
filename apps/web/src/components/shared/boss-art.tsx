// apps/web/src/components/shared/boss-art.tsx
//
// Boss art: the 2:1 journal portrait (served by the KhadBot API) and the square kill-achievement thumbnail (a Blizzard
// image URL the API resolves), plus the attribution line that goes wherever either is drawn (docs/game-art.md).
// `useBossArt(encounterId)` finds a boss's URLs in the one cached `GET /v1/art/encounters` listing, by WCL's encounter
// ID. Both components are drawn beside the boss's name, so their `alt` is empty. Every image has a fallback: a
// thumbnail is a neutral square and a portrait is nothing, on no URL and on a load error alike.

"use client";

import { useState } from "react";

import { apiBase } from "@/lib/api";
import { useEncounterArt } from "@/lib/queries";
import { cn } from "@/lib/utils";

// ----------------------------------------------------------------------------------------------------------------
// Lookup
// ----------------------------------------------------------------------------------------------------------------

export interface BossArt {
  /** Absolute URL of the 128 by 64 journal portrait, or undefined until it is uploaded. */
  portraitSrc: string | undefined;
  /** The Blizzard kill-achievement icon's URL, or undefined without one. */
  thumbnailSrc: string | undefined;
}

const NO_ART: BossArt = { portraitSrc: undefined, thumbnailSrc: undefined };

/** The API serves a portrait as a path on its own origin; the browser needs the whole address. */
export function portraitUrl(path: string | null | undefined, base: string): string | undefined {
  return path ? new URL(path, base).toString() : undefined;
}

/** One boss's art from the listing, by WCL encounter ID; nothing for an unknown boss or an unread listing. */
export function useBossArt(encounterId: number | null | undefined): BossArt {
  const art = useEncounterArt();
  const found = encounterId == null ? undefined : art.data?.encounters.find((e) => e.encounter_id === encounterId);
  if (!found) return NO_ART;
  return { portraitSrc: portraitUrl(found.portrait_url, apiBase()), thumbnailSrc: found.thumbnail_url ?? undefined };
}

// ----------------------------------------------------------------------------------------------------------------
// Images
// ----------------------------------------------------------------------------------------------------------------

/** True once `src` failed to load; a new `src` starts afresh. */
function useImageFailed(src: string | undefined): [boolean, () => void] {
  const [failed, setFailed] = useState<string | null>(null);
  return [failed !== null && failed === src, () => setFailed(src ?? null)];
}

/**
 * The boss's square thumbnail, `size` px (or filling its parent with `size="fill"`). The neutral square (surface 2)
 * stands in on no URL or a load error.
 */
export function BossThumbnail({
  src,
  size,
  className,
}: {
  src?: string | undefined;
  size: number | "fill";
  className?: string;
}) {
  const [failed, onError] = useImageFailed(src);
  const box = size === "fill" ? { width: "100%", height: "100%" } : { width: size, height: size };
  return (
    <span
      data-testid="boss-thumbnail"
      data-art={src && !failed ? "image" : "fallback"}
      className={cn("block flex-none overflow-hidden rounded-sm bg-s2", className)}
      style={box}
    >
      {src && !failed ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} alt="" width={56} height={56} onError={onError} className="block size-full object-cover" />
      ) : null}
    </span>
  );
}

/** The boss's journal portrait, 2:1 and `width` px wide, contained. Nothing on no URL or a load error. */
export function BossPortrait({
  src,
  width,
  className,
}: {
  src?: string | undefined;
  width: number;
  className?: string;
}) {
  const [failed, onError] = useImageFailed(src);
  if (!src || failed) return null;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      data-testid="boss-portrait"
      src={src}
      alt=""
      width={width}
      height={width / 2}
      onError={onError}
      className={cn("flex-none object-contain", className)}
      style={{ width, height: width / 2 }}
    />
  );
}

// ----------------------------------------------------------------------------------------------------------------
// Attribution
// ----------------------------------------------------------------------------------------------------------------

/** The attribution line, small and muted. Draws nothing without text. */
export function ArtAttributionLine({ text, className }: { text: string | null | undefined; className?: string }) {
  if (!text) return null;
  return (
    <p data-testid="art-attribution" className={cn("m-0 text-xs text-mut", className)}>
      {text}
    </p>
  );
}

/** The listing's attribution, once the listing holds any art; for the screens that draw art through the hooks above. */
export function useArtAttribution(): string | null {
  const art = useEncounterArt();
  const any = art.data?.encounters.some((e) => e.portrait_url || e.thumbnail_url);
  return any ? (art.data?.attribution ?? null) : null;
}

/** The footer line of a screen that shows boss art. */
export function ArtFooter({ className }: { className?: string }) {
  return <ArtAttributionLine text={useArtAttribution()} className={className} />;
}
