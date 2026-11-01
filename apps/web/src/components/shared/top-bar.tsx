// apps/web/src/components/shared/top-bar.tsx
//
// The top bar of every page, as the canvas's desktop artboards draw it: the KhadBot home link (the wordmark; there is
// no logo image), "Encounter review" (and "Local" when the server has no sign-in), then the page's breadcrumb, and on
// the right the views, the account control and the theme menu.
//
// Slots (each a few lines at its marked spot):
// - breadcrumb: pages pass it (<Breadcrumb items=... />).
// - views: empty this round; the night overview's tab comes back here when that screen is built.
// - account: the sign-in button or account menu (components/account/) renders where <AccountSlot/> is, when the
//   server has sign-in (`features.sign_in`).

"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { useFeatures } from "@/lib/capabilities";

import { ThemeMenu } from "./theme-menu";

export interface BreadcrumbItem {
  label: string;
  /** A link to an earlier page; the last item (the current page) has none. */
  href?: string;
  /** Report codes and refs are set in mono. */
  mono?: boolean;
}

export function Breadcrumb({ items }: { items: BreadcrumbItem[] }) {
  return (
    <nav aria-label="Breadcrumb" className="min-w-0">
      <ol className="m-0 flex min-w-0 list-none flex-wrap items-center gap-1.5 p-0 text-[13px] text-mut">
        {items.map((item, i) => {
          const last = i === items.length - 1;
          const mono = item.mono ? "font-mono" : undefined;
          return (
            <li key={`${item.label}-${i}`} className="inline-flex items-center gap-1.5">
              {i > 0 ? <span aria-hidden>/</span> : null}
              {item.href && !last ? (
                <Link href={item.href} className={["text-mut", mono].filter(Boolean).join(" ")}>
                  {item.label}
                </Link>
              ) : (
                <span
                  className={[last ? "text-ink" : undefined, mono].filter(Boolean).join(" ") || undefined}
                  aria-current={last ? "page" : undefined}
                >
                  {item.label}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/** The account control's place: empty until the sign-in area fills it. Rendered only when sign-in exists. */
function AccountSlot() {
  // Slot: account. Replace this line with the sign-in button / account menu (components/account/).
  return <span data-slot="account" className="inline-block min-h-[34px]" />;
}

export function TopBar({ breadcrumb }: { breadcrumb?: ReactNode }) {
  const features = useFeatures();
  return (
    <header className="flex flex-wrap items-center gap-x-5 gap-y-3 border-b border-line bg-s1 px-6 py-3">
      <Link href="/" className="inline-flex items-center gap-2 font-display text-xl leading-none font-semibold text-ink no-underline">
        KhadBot
      </Link>
      <span className="inline-flex items-center gap-2 text-[13px] text-mut">
        Encounter review
        {features.sign_in ? null : (
          <span data-testid="local-label" className="rounded-sm border border-line bg-s2 px-2 py-px text-xs font-semibold text-ink">
            Local
          </span>
        )}
      </span>
      {breadcrumb}
      <div className="ml-auto flex items-center gap-2">
        {/* Slot: views. Empty this round: the night overview's tab returns here with that screen. */}
        {features.sign_in ? <AccountSlot /> : null}
        <ThemeMenu />
      </div>
    </header>
  );
}

/** A page under the top bar, in the canvas's centred column (1040 wide, 32 px gutter). */
export function PageColumn({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <main className={["mx-auto flex w-full max-w-[1040px] flex-col gap-6 px-8 pt-8 pb-16", className].filter(Boolean).join(" ")}>
      {children}
    </main>
  );
}
