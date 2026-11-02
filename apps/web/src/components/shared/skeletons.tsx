// apps/web/src/components/shared/skeletons.tsx
//
// Loading placeholders sized like what they replace, so nothing jumps when the data arrives. Each takes the size of
// the canvas element it stands for; build new ones from SkeletonBlock and keep them next to these.

import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/** A block of a given size (px or any CSS length). */
export function SkeletonBlock({
  width = "100%",
  height,
  className,
}: {
  width?: number | string;
  height: number | string;
  className?: string;
}) {
  return <Skeleton className={className} style={{ width, height }} />;
}

/** Lines of text: 14 px body on 1.5 line height, the last one shorter. */
export function SkeletonText({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div className={cn("flex flex-col gap-2", className)} aria-hidden>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className="h-3.5" style={{ width: i === lines - 1 && lines > 1 ? "60%" : "100%" }} />
      ))}
    </div>
  );
}

/** A card of the canvas's shape (surface 1, line, 2 px corners) holding a heading and lines. */
export function SkeletonCard({ height = 160, className }: { height?: number; className?: string }) {
  return (
    <div className={cn("flex flex-col gap-3 rounded-sm border border-line bg-s1 px-4 py-3.5", className)} style={{ minHeight: height }}>
      <Skeleton className="h-4 w-40" />
      <SkeletonText lines={3} />
    </div>
  );
}

/** The top bar's height (a 56 px bar) and the page column, while /v1/health is read before the first screen. */
export function ShellSkeleton() {
  return (
    <div role="status" aria-busy="true" aria-label="Loading KhadBot">
      <div className="h-[57px] border-b border-line bg-s1" />
      <div className="mx-auto flex max-w-[1040px] flex-col gap-5 px-8 pt-8">
        <SkeletonBlock width={160} height={34} />
        <SkeletonCard height={180} />
      </div>
    </div>
  );
}
