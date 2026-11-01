// apps/web/src/components/ui/skeleton.tsx: shadcn/ui's Skeleton, a pulsing block in the bar-track colour (--s3).

import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

export function Skeleton({ className, ...props }: ComponentProps<"div">) {
  return (
    <div data-slot="skeleton" aria-hidden className={cn("animate-pulse rounded-sm bg-s3", className)} {...props} />
  );
}
