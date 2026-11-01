// apps/web/src/app/layout.tsx
//
// The root layout: fonts, the theme set before first paint, Wowhead's tooltip config and script, and the providers.
// Pages render their own top bar (components/shared/top-bar.tsx) so each fills its breadcrumb.

import type { Metadata } from "next";
import type { ReactNode } from "react";

import { WowheadScript } from "@/components/shared/wowhead";
import { THEME_SCRIPT } from "@/lib/theme-script";
import { WOWHEAD_CONFIG_SCRIPT } from "@/lib/wowhead";

import "@/styles/globals.css";

import { fontVariables } from "./fonts";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: { default: "KhadBot", template: "%s · KhadBot" },
  description: "Encounter review for World of Warcraft raids, from Warcraft Logs data.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={fontVariables} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
        <script dangerouslySetInnerHTML={{ __html: WOWHEAD_CONFIG_SCRIPT }} />
      </head>
      <body>
        <Providers>{children}</Providers>
        <WowheadScript />
      </body>
    </html>
  );
}
