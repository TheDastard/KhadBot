// apps/web/src/components/pull/unverified-tag.tsx
//
// A small tag for an encounter-file mechanic nobody has checked against the logs yet (its `verified` is false).
// Shown next to the mechanic's name wherever a view lists it, until a person flips the entry to verified.

export function UnverifiedTag({ verified }: { verified: boolean | null }) {
  if (verified !== false) return null;
  return (
    <span
      className="ml-1.5 rounded-sm bg-warnt px-1.5 align-middle text-[11px] font-medium text-warn"
      title="Not yet checked by a person against the logs"
    >
      Unverified
    </span>
  );
}
