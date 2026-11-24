// apps/web/src/lib/capabilities.tsx
//
// What the server offers, read from `GET /v1/health` before the first screen (.claude/rules/web.md). The app
// branches only on `features`, never on `mode`, the API base URL, the environment or a 404.
//
//   <CapabilitiesGate>   waits for /v1/health, then renders the app; shows "KhadBot isn't responding" if it can't
//   useFeatures()        the Features object (every flag false until known, which the gate never shows)
//   <RequireFeature feature="raid_search">   a direct link to an area whose flag is off shows the notice
//                                            "Not available on this server yet" instead of its content

"use client";

import type { Schema } from "@khadbot/api-client";
import { createContext, useContext, type ReactNode } from "react";

import { ErrorState } from "@/components/shared/error-state";
import { NotAvailable } from "@/components/shared/not-available";
import { ShellSkeleton } from "@/components/shared/skeletons";

import { setApiCredentials } from "./api";
import { useHealth } from "./queries";

export type Features = Required<Schema<"Features">>;
export type FeatureName = keyof Features;

export const NO_FEATURES: Features = {
  sign_in: false,
  ingest_jobs: false,
  live_watch: false,
  budget: false,
  reviews: false,
  chat: false,
  report_lookup: false,
  raid_search: false,
};

interface Capabilities {
  features: Features;
  /** The entry point's label (`local` or `hosted`): show it if useful, never branch on it. */
  modeLabel: string;
}

const CapabilitiesContext = createContext<Capabilities>({ features: NO_FEATURES, modeLabel: "" });

/** The features of a health answer, with any flag the server didn't send as false. */
export function featuresOf(health: Schema<"Health"> | undefined): Features {
  return { ...NO_FEATURES, ...(health?.features ?? {}) };
}

export function CapabilitiesGate({ children }: { children: ReactNode }) {
  const health = useHealth();
  if (health.isPending) return <ShellSkeleton />;
  if (health.isError) {
    return (
      <main className="mx-auto flex max-w-[1040px] flex-col gap-5 px-8 pt-8 pb-16">
        <ErrorState
          tone="bad"
          where="Any page"
          code={(health.error as { code?: string }).code ?? "api_unreachable"}
          error={health.error}
          title="KhadBot isn't responding"
          body="Check your connection, then try again."
          action={{ label: "Retry", onClick: () => void health.refetch() }}
        />
      </main>
    );
  }
  const features = featuresOf(health.data);
  // Before any child renders (and queries): the cookie goes only to a server with sign-in.
  setApiCredentials(features.sign_in);
  return (
    <CapabilitiesContext.Provider value={{ features, modeLabel: health.data.mode ?? "" }}>
      {children}
    </CapabilitiesContext.Provider>
  );
}

export function useCapabilities(): Capabilities {
  return useContext(CapabilitiesContext);
}

export function useFeatures(): Features {
  return useContext(CapabilitiesContext).features;
}

export function useFeature(feature: FeatureName): boolean {
  return useFeatures()[feature];
}

/** Renders `children` when `feature` is on, else the "Not available on this server yet" notice. */
export function RequireFeature({ feature, area, children }: { feature: FeatureName; area: string; children: ReactNode }) {
  return useFeature(feature) ? <>{children}</> : <NotAvailable area={area} />;
}
