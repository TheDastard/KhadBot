// apps/web/src/components/ui/button.tsx
//
// shadcn/ui's Button, restyled to the canvas tokens: 2 px corners, `primary` is the teal action the canvas draws
// ("Look up", "Retry"), `secondary` the quiet surface-2 button (theme menu, "Copy briefing"), `outline` a bordered one,
// `danger` the outlined Stop, `link` a text link. Written by hand from shadcn's source (the shadcn CLI fetches from its
// own registry, which the build doesn't reach).

import { cva, type VariantProps } from "class-variance-authority";
import { Slot } from "radix-ui";
import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

export const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-1.5 rounded-sm font-sans whitespace-nowrap transition-colors " +
    "cursor-pointer disabled:cursor-not-allowed disabled:opacity-100 [&_svg]:pointer-events-none [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-info text-s1 font-semibold hover:bg-info/90 disabled:bg-s2 disabled:text-mut disabled:border disabled:border-line",
        secondary: "bg-s2 text-ink border border-line hover:bg-s3",
        outline: "bg-s1 text-ink border border-line hover:bg-s2",
        danger: "bg-s1 text-bad border border-bad font-semibold hover:bg-badt",
        link: "text-info underline-offset-2 hover:underline",
      },
      size: {
        sm: "min-h-8 px-2.5 py-1 text-xs",
        md: "min-h-9 px-3.5 py-1.5 text-sm",
        lg: "min-h-10 px-4 py-2 text-sm",
        icon: "size-9",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export type ButtonProps = ComponentProps<"button"> & VariantProps<typeof buttonVariants> & { asChild?: boolean };

export function Button({ className, variant, size, asChild = false, ...props }: ButtonProps) {
  const Comp = asChild ? Slot.Root : "button";
  return <Comp data-slot="button" className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}
