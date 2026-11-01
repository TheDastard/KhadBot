// apps/web/src/components/ui/popover.tsx
//
// shadcn/ui's Popover over Radix, restyled to the canvas tokens like the dropdown menu (surface 1, a 1 px line, 2 px
// corners, the menu shadow). A non-modal panel anchored to its trigger for content that is more than a list of
// commands (the account panel's headings, progress bar and buttons), which a role="menu" cannot hold.

"use client";

import { Popover as PopoverPrimitive } from "radix-ui";
import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

export const Popover = PopoverPrimitive.Root;
export const PopoverTrigger = PopoverPrimitive.Trigger;

export function PopoverContent({
  className,
  align = "end",
  sideOffset = 6,
  ...props
}: ComponentProps<typeof PopoverPrimitive.Content>) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        data-slot="popover-content"
        align={align}
        sideOffset={sideOffset}
        className={cn(
          "z-50 overflow-hidden rounded-sm border border-line bg-s1 text-ink shadow-[0_12px_32px_rgba(0,0,0,.28)] outline-none",
          "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
          className,
        )}
        {...props}
      />
    </PopoverPrimitive.Portal>
  );
}
