// apps/web/src/lib/utils.ts: `cn`, the class-name joiner shadcn/ui components use.

import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
