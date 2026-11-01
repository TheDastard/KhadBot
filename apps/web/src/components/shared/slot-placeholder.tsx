// apps/web/src/components/shared/slot-placeholder.tsx
//
// Stands in a route slot until the area that owns it fills it: a dashed box of the slot's size with its name, marked
// `data-slot` so tests can find it. An area replaces the <SlotPlaceholder> line at its marked spot with its own
// component (.claude/rules/web.md, "Route shells and slots").

import { cn } from "@/lib/utils";

export interface SlotPlaceholderProps {
  /** The slot's id, also its data-slot value ("raids-page", "pull-body", "pull-timeline", "pull-not-loaded"). */
  slot: string;
  /** What will be here, shown in the box ("Raids search"). */
  label: string;
  minHeight?: number;
  className?: string;
}

export function SlotPlaceholder({ slot, label, minHeight = 120, className }: SlotPlaceholderProps) {
  return (
    <div
      data-slot={slot}
      className={cn(
        "flex items-center justify-center rounded-sm border border-dashed border-line bg-s1 text-[13px] text-mut",
        className,
      )}
      style={{ minHeight }}
    >
      {label}
    </div>
  );
}
