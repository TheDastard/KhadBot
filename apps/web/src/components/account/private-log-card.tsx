// apps/web/src/components/account/private-log-card.tsx
//
// "This log is private" (canvas "Account", signed out, a private log looked up): shown in place of a lookup's
// "report not found" when the visitor is signed out, because the API answers 404 for a log they may not read.
// It says what signing in allows and offers the sign-in link, which returns to this very page afterwards.
//
//   <PrivateLogCard />   (the Raids page shows it at its `private-log-card` spot)

"use client";

import { CheckIcon, ExternalLinkIcon, LockIcon } from "lucide-react";
import { usePathname, useSearchParams } from "next/navigation";

import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { nextPath, signInHref } from "./sign-in";

/** What signing in allows, in the canvas's words. */
export const SIGN_IN_PERKS = [
  "Open private logs your Warcraft Logs account can see, like your guild’s.",
  "Follow a log live while your raid is still recording it.",
  "Pulls you open load with your own Warcraft Logs account.",
] as const;

export function PrivateLogCard({ className }: { className?: string }) {
  const pathname = usePathname();
  const search = useSearchParams().toString();
  const href = signInHref(nextPath(pathname, search));
  return (
    <section
      aria-labelledby="private-log-h"
      data-testid="private-log-card"
      className={cn("flex gap-4 rounded-sm border border-line bg-s1 px-6 py-[22px]", className)}
    >
      <span
        aria-hidden
        className="inline-flex size-11 flex-none items-center justify-center rounded-sm bg-s2 text-mut"
      >
        <LockIcon className="size-[22px]" />
      </span>
      <div className="flex min-w-0 flex-col gap-2.5">
        <h2 id="private-log-h" className="m-0 font-display text-xl leading-[1.3] font-semibold">
          This log is private
        </h2>
        <p className="m-0 max-w-[62ch]">
          Warcraft Logs only shows it to people who can see it there, such as members of the guild that uploaded it.
          Sign in with your Warcraft Logs account to open it.
        </p>
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-sm">
          {SIGN_IN_PERKS.map((perk) => (
            <li key={perk} className="flex items-start gap-2">
              <CheckIcon className="mt-[3px] size-4 flex-none text-ok" aria-hidden />
              <span>{perk}</span>
            </li>
          ))}
        </ul>
        <div className="mt-1 flex flex-wrap items-center gap-x-3.5 gap-y-2.5">
          <a href={href} className={cn(buttonVariants({ variant: "primary", size: "lg" }), "min-h-11 px-[18px] no-underline")}>
            Sign in with Warcraft Logs
            <ExternalLinkIcon className="size-3.5" aria-hidden />
          </a>
          <span className="text-[13px] text-mut">You come straight back to this log afterwards.</span>
        </div>
        <p className="m-0 max-w-[70ch] text-xs text-mut">
          Signing in happens on warcraftlogs.com; KhadBot never sees your password. It gets your Warcraft Logs name and
          can read the logs your account can see. It only reads, and you can sign out at any time.
        </p>
      </div>
    </section>
  );
}
