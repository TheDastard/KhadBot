// apps/web/src/components/panel/registry.tsx
//
// The registry from ref kind to renderer. `EVIDENCE_RENDERERS` is typed over the generated union's `kind`, so a new
// member of the API's `Evidence` union fails to compile until it has a renderer; `renderEvidence` switches over the
// union exhaustively. `REF_KIND_VIEWS` covers the ref kinds a URL can hold (lib/refs.ts's RefKind), which adds the
// plan kind: it has no record, so its view needs no request.

import type { ReactNode } from "react";

import type { RefKind } from "@/lib/refs";

import {
  CooldownView,
  DeathView,
  EventView,
  MechanicView,
  PullView,
  WindowView,
  type EvidenceKindName,
  type ViewProps,
} from "./views";

export const EVIDENCE_RENDERERS = {
  death: DeathView,
  mech: MechanicView,
  cd: CooldownView,
  win: WindowView,
  event: EventView,
  pull: PullView,
} satisfies { [K in EvidenceKindName]: (props: ViewProps<K>) => ReactNode };

/** Every ref kind a URL can hold: a record from the API, or the plan view that needs none. */
export const REF_KIND_VIEWS = {
  death: "evidence",
  mech: "evidence",
  cd: "evidence",
  win: "evidence",
  event: "evidence",
  pull: "evidence",
  plan: "plan",
} as const satisfies Record<RefKind, "evidence" | "plan">;

type Common = Omit<ViewProps<EvidenceKindName>, "record">;

/** Render an evidence response with its kind's renderer. */
export function renderEvidence(
  evidence: { [K in EvidenceKindName]: { kind: K; record: ViewProps<K>["record"] } }[EvidenceKindName],
  common: Common,
): ReactNode {
  switch (evidence.kind) {
    case "death":
      return <DeathView {...common} record={evidence.record} />;
    case "mech":
      return <MechanicView {...common} record={evidence.record} />;
    case "cd":
      return <CooldownView {...common} record={evidence.record} />;
    case "win":
      return <WindowView {...common} record={evidence.record} />;
    case "event":
      return <EventView {...common} record={evidence.record} />;
    case "pull":
      return <PullView {...common} record={evidence.record} />;
    default: {
      const unreachable: never = evidence;
      return unreachable;
    }
  }
}
