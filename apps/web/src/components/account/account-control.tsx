// apps/web/src/components/account/account-control.tsx
//
// The top bar's account control, rendered only when the server has sign-in: "Sign in with Warcraft Logs" for a
// visitor (returning to the page they are on), the account menu for someone signed in. A session that ends mid-visit
// puts it back to signed out with a notice, not an error page.

"use client";

import { usePathname, useSearchParams } from "next/navigation";

import { Notice } from "@/components/shared/notice";
import { buttonVariants } from "@/components/ui/button";
import { useMe } from "@/lib/session";
import { cn } from "@/lib/utils";

import { AccountMenu } from "./account-menu";
import { nextPath, signInHref } from "./sign-in";
import { useSessionEnded, useSessionWatch } from "./use-account";

/** Needs a Suspense boundary (it reads the URL). */
export function AccountControl() {
  useSessionWatch();
  const me = useMe();
  const ended = useSessionEnded();
  if (me.isPending) return <span className="inline-block min-h-[34px]" aria-hidden />;
  if (me.data) return <AccountMenu name={me.data.name} />;
  return (
    <>
      {ended ? <Notice tone="warn">Your session ended. Sign in again to carry on.</Notice> : null}
      <SignInLink />
    </>
  );
}

/** "Sign in with Warcraft Logs", returning to the current page. */
function SignInLink() {
  const pathname = usePathname();
  const search = useSearchParams().toString();
  return (
    <a
      href={signInHref(nextPath(pathname, search))}
      className={cn(
        buttonVariants({ variant: "outline" }),
        "min-h-[34px] border-info px-3 py-1.5 text-[13px] font-semibold text-info no-underline",
      )}
    >
      Sign in with Warcraft Logs
    </a>
  );
}
