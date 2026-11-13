// apps/web/src/components/pull/ref-chip.tsx
//
// The evidence chip as the main column draws it: the kind as its text ("death", "mech", "cd", "window"), "Selected"
// while its ref is open in the panel.

"use client";

import { EvidenceChip } from "@/components/shared/evidence-chip";
import { useIsFocusedRef } from "@/lib/focus";

export function RefChip({ evidenceRef, label }: { evidenceRef: string; label: string }) {
  const selected = useIsFocusedRef(evidenceRef);
  return <EvidenceChip evidenceRef={evidenceRef} label={selected ? "Selected" : label} selected={selected} />;
}
