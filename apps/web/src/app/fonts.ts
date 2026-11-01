// apps/web/src/app/fonts.ts
//
// The canvas's three typefaces through next/font, from the Fontsource packages on npm (latin subset), so the build
// fetches nothing from Google Fonts: Source Serif 4 (headings, 600), IBM Plex Sans (text, 400/500/600) and IBM Plex
// Mono (refs, codes, times; 400/500). Each sets a CSS variable that styles/globals.css maps to font-display,
// font-sans and font-mono.

import localFont from "next/font/local";

export const sourceSerif = localFont({
  src: "../../node_modules/@fontsource-variable/source-serif-4/files/source-serif-4-latin-wght-normal.woff2",
  weight: "200 900",
  style: "normal",
  display: "swap",
  variable: "--font-source-serif",
  fallback: ["Georgia", "Times New Roman", "serif"],
});

export const plexSans = localFont({
  src: [
    { path: "../../node_modules/@fontsource/ibm-plex-sans/files/ibm-plex-sans-latin-400-normal.woff2", weight: "400" },
    { path: "../../node_modules/@fontsource/ibm-plex-sans/files/ibm-plex-sans-latin-500-normal.woff2", weight: "500" },
    { path: "../../node_modules/@fontsource/ibm-plex-sans/files/ibm-plex-sans-latin-600-normal.woff2", weight: "600" },
  ],
  style: "normal",
  display: "swap",
  variable: "--font-plex-sans",
  fallback: ["Segoe UI", "system-ui", "sans-serif"],
});

export const plexMono = localFont({
  src: [
    { path: "../../node_modules/@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-400-normal.woff2", weight: "400" },
    { path: "../../node_modules/@fontsource/ibm-plex-mono/files/ibm-plex-mono-latin-500-normal.woff2", weight: "500" },
  ],
  style: "normal",
  display: "swap",
  variable: "--font-plex-mono",
  fallback: ["ui-monospace", "Consolas", "monospace"],
});

export const fontVariables = [sourceSerif.variable, plexSans.variable, plexMono.variable].join(" ");
