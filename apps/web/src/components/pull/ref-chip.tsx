// apps/web/src/components/pull/ref-chip.tsx
//
// The evidence chip as the main column draws it: the kind as its text ("death", "mech", "cd", "window"), "Selected"
// while its ref is open in the panel. A mechanic's chip (`mechanic`) carries the mechanic's icon and KhadBot's
// description tooltip. MechanicChip is a mechanic's chip by name: the chip beside a spell that belongs to a mechanic
// (a killing blow, a top hit, an event's ability), which opens the mechanic in the panel.

"use client";

import { EvidenceChip } from "@/components/shared/evidence-chip";
import { MechanicIcon, MechanicTooltip, type MechanicFacts } from "@/components/shared/mechanic-tip";
import { useIsFocusedRef } from "@/lib/focus";
import { buildRef } from "@/lib/refs";

export function RefChip({
  evidenceRef,
  label,
  mechanic,
}: {
  evidenceRef: string;
  label: string;
  /** The mechanic a `mech` chip names: its icon and its tooltip. */
  mechanic?: MechanicFacts;
}) {
  const selected = useIsFocusedRef(evidenceRef);
  const chip = (
    <EvidenceChip
      evidenceRef={evidenceRef}
      label={selected ? "Selected" : label}
      selected={selected}
      icon={mechanic ? <MechanicIcon facts={mechanic} /> : undefined}
    />
  );
  return mechanic ? <MechanicTooltip facts={mechanic}>{chip}</MechanicTooltip> : chip;
}

/** A mechanic's chip, labelled with its name, for one pull. */
export function MechanicChip({ code, fight, facts }: { code: string; fight: number; facts: MechanicFacts }) {
  return (
    <RefChip evidenceRef={buildRef({ kind: "mech", report: code, fight, key: facts.id })} label={facts.name} mechanic={facts} />
  );
}
