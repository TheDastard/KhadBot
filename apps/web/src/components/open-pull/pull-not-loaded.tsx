// apps/web/src/components/open-pull/pull-not-loaded.tsx
//
// The pull route's not-loaded slot: opens the pull, follows its job and draws "Opening a pull, loading". The pull
// page replaces it when the digest is written (use-open-pull.ts invalidates the pull's queries).

"use client";

import { unwrap, type ApiGet } from "@khadbot/api-client";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { signInHrefHere } from "@/components/account";
import { useArtAttribution, useBossArt } from "@/components/shared/boss-art";
import { useFeature } from "@/lib/capabilities";
import { api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";
import { useSignedIn } from "@/lib/session";

import { errorCard } from "./error-cards";
import {
  phaseOf,
  pullOfJob,
  remainingSeconds,
  stepsOf,
  type JobPull,
  type JobStreamState,
  type Phase,
} from "./job-events";
import { difficultyWord, OpeningPull, type PullTileInfo } from "./opening-pull";
import { useOpenPull } from "./use-open-pull";

type Lookup = ApiGet<"/v1/lookup/report">;

export interface PullNotLoadedProps {
  code: string;
  fight: number;
}

export function PullNotLoaded({ code, fight }: PullNotLoadedProps) {
  const open = useOpenPull(code, fight);
  const canLookUp = useFeature("report_lookup");
  // A signed-out visitor opens a public log's pull like anyone else; only a log they can't read (404) gets the
  // private-log card, and only a request the API still refuses signed out (401) the sign-in card.
  const signedOut = useSignedIn() === false;
  // The lookup answers from the metadata the open request just cached, so it costs no second request. It also runs
  // when the request was refused (a 401 or 404), so the tile still names the pull it can.
  const lookup = useQuery({
    queryKey: [...queryKeys.report(code), "lookup"],
    queryFn: () => unwrap(api().GET("/v1/lookup/report", { params: { query: { source: code } } })),
    enabled: canLookUp && (open.requested || open.openError !== null),
    staleTime: 60_000,
    retry: false,
  });

  const pull = pullOfJob(open.stream.job, fight);
  const tile = tileOf(lookup.data, fight, pull);
  const { thumbnailSrc } = useBossArt(tile?.encounterId);
  const attribution = useArtAttribution();
  const phase = phaseWithOpenError(open.stream, open.openError, open.connectionLost, fight);

  const waitS =
    phase.kind === "waiting" ? phase.etaS : phase.kind === "busy" ? phase.retryAfterS : retryOf(open.openError);
  const countdownS = useCountdown(waitS, open.stream.receivedAt);

  return (
    <OpeningPull
      code={code}
      fight={fight}
      tile={tile}
      steps={stepsOf(phase.kind === "failed" && !pull ? null : pull)}
      phase={phase}
      countdownS={countdownS}
      onStartAgain={open.restart}
      signInHref={signInHrefHere()}
      signedOut={signedOut}
      thumbnailSrc={thumbnailSrc}
      attribution={attribution}
    />
  );
}

/** The phase to draw: a failed request to open the pull, or a lost connection, outranks what the job last said. */
function phaseWithOpenError(
  stream: JobStreamState,
  openError: { code: string; message: string } | null,
  connectionLost: boolean,
  fight: number,
): Phase {
  if (openError) return { kind: "failed", code: openError.code, message: openError.message, step: null };
  const phase = phaseOf(stream, fight);
  if (connectionLost && phase.kind !== "done") {
    return { kind: "failed", code: "api_unreachable", message: errorCard({ code: "api_unreachable", message: "" }).body, step: null };
  }
  return phase;
}

function retryOf(error: { retryAfterS: number | null } | null): number | null {
  return error?.retryAfterS ?? null;
}

/** Seconds left of a wait the server announced, ticking once a second. Null without an estimate. */
function useCountdown(totalS: number | null, announcedAt: number): number | null {
  const [mountedAt] = useState<number>(() => Date.now());
  const [now, setNow] = useState<number>(mountedAt);
  const active = totalS !== null;
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active, totalS, announcedAt]);
  // A wait from a failed request counts from when it was shown; one from the job counts from when it was announced.
  return remainingSeconds(totalS, announcedAt || mountedAt, now);
}

/** The tile's facts from the report lookup, else what the job knows (encounter and difficulty only). */
function tileOf(lookup: Lookup | undefined, fight: number, pull: JobPull | null): PullTileInfo | null {
  for (const group of lookup?.groups ?? []) {
    const found = group.pulls.find((p) => p.fight === fight);
    if (found) {
      return {
        encounterId: group.encounter_id,
        encounter: group.encounter_name,
        difficulty: difficultyWord(group.difficulty_name),
        label: found.label,
        pct: found.boss_pct,
        kill: found.outcome === "kill",
        durationMs: found.duration_ms,
      };
    }
  }
  if (pull?.encounter_name) {
    return {
      encounterId: pull.encounter_id ?? null,
      encounter: pull.encounter_name,
      difficulty: difficultyWord(pull.difficulty_name),
      label: null,
      pct: null,
      kill: false,
      durationMs: null,
    };
  }
  return null;
}
