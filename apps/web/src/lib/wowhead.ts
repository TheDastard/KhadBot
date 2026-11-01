// apps/web/src/lib/wowhead.ts
//
// Wowhead's tooltip script: its URL, the embed config and spell links (docs/game-art.md, "Embed config"). Plain
// values, so the root layout (a server component) can render the config script; the loader and the refresh live in
// components/shared/wowhead.tsx.

export const WOWHEAD_SCRIPT = "https://wow.zamimg.com/js/tooltips.js";

export const WOWHEAD_CONFIG = { colorLinks: true, iconizeLinks: true, renameLinks: false, iconSize: "small" } as const;

/** The config as an inline <head> script, set before the tooltip script loads. */
export const WOWHEAD_CONFIG_SCRIPT = `window.whTooltips = ${JSON.stringify(WOWHEAD_CONFIG)};`;

/** The Wowhead page of a spell, which the script turns into a tooltip and icon. */
export function wowheadSpellUrl(spellId: number): string {
  return `https://www.wowhead.com/spell=${spellId}`;
}
