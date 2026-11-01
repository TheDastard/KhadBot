// apps/web/src/components/raids/guild/servers.ts
//
// The server list the guild and character searches share (GET /v1/servers): the hook, the folding that matches
// what is typed against it, the grouped options the combobox shows, and the guild URL parser. Kept in guild/ because
// the character search imports it from here; it has no other home inside the two directories this area owns.

"use client";

import { unwrap, type ApiGet } from "@khadbot/api-client";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

export type ServerList = ApiGet<"/v1/servers">;

/** One server, with the region it is in. `region` is WCL's slug (what the searches take), `regionName` the short name. */
export interface ServerChoice {
  region: string;
  regionName: string;
  slug: string;
  name: string;
  match: string;
}

/** The whole list, once a day at most (the API sends a day's Cache-Control and an ETag). */
export function useServers() {
  return useQuery({
    queryKey: ["servers"],
    staleTime: 60 * 60_000,
    retry: 1,
    queryFn: () => unwrap(api().GET("/v1/servers")),
  });
}

// ----------------------------------------------------------------------------
// Matching
// ----------------------------------------------------------------------------

/** Lower case, accents removed, only letters and digits: how the API folds `match`, so typed text can meet it. */
export function foldName(text: string): string {
  return text
    .normalize("NFD")
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]/gu, "");
}

/** How many leading characters of `name` the folded text `typed` covers (the part to bold); 0 when it isn't a prefix. */
export function prefixLength(name: string, typed: string): number {
  if (!typed) return 0;
  let folded = "";
  let end = 0;
  for (const ch of name) {
    folded += foldName(ch);
    end += ch.length;
    if (folded.length >= typed.length) return folded.startsWith(typed) ? end : 0;
  }
  return 0;
}

export function serverLabel(server: Pick<ServerChoice, "name" | "regionName">): string {
  return `${server.name} (${server.regionName})`;
}

const REGION_HEADINGS: Record<string, string> = { us: "United States", eu: "Europe" };

export interface ServerGroup {
  region: string;
  heading: string;
  servers: ServerChoice[];
}

/** The servers whose folded name starts with the folded `typed` text, under their region's divider; empty regions are dropped. */
export function filterServers(list: ServerList | undefined, typed: string): ServerGroup[] {
  const folded = foldName(typed);
  const groups: ServerGroup[] = [];
  for (const region of list?.regions ?? []) {
    const servers = region.servers
      .filter((s) => s.match.startsWith(folded))
      .map((s) => ({ region: region.slug, regionName: region.name, slug: s.slug, name: s.name, match: s.match }));
    if (servers.length > 0) groups.push({ region: region.slug, heading: REGION_HEADINGS[region.slug] ?? region.name, servers });
  }
  return groups;
}

export function findServer(list: ServerList | undefined, region: string, slug: string): ServerChoice | null {
  for (const r of list?.regions ?? []) {
    if (r.slug !== region.toLowerCase()) continue;
    const s = r.servers.find((x) => x.slug === slug.toLowerCase());
    if (s) return { region: r.slug, regionName: r.name, slug: s.slug, name: s.name, match: s.match };
  }
  return null;
}

/** The one server a typed name names exactly, when no choice was made from the list; null when none or several do. */
export function exactServer(list: ServerList | undefined, typed: string): ServerChoice | null {
  const folded = foldName(typed);
  if (!folded) return null;
  const hits = filterServers(list, typed).flatMap((g) => g.servers.filter((s) => s.match === folded));
  return hits.length === 1 ? hits[0]! : null;
}

// ----------------------------------------------------------------------------
// Guild URLs
// ----------------------------------------------------------------------------

export interface GuildUrl {
  region: string;
  server: string;
  name: string;
}

/**
 * The guild a warcraftlogs.com guild URL names (`/guild/<region>/<server slug>/<percent-encoded name>`, the one form
 * WCL uses and the API accepts); null when the text is not such a URL.
 */
export function parseGuildUrl(text: string): GuildUrl | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  let url: URL;
  try {
    url = new URL(trimmed.includes("://") ? trimmed : `https://${trimmed}`);
  } catch {
    return null;
  }
  if (!/(^|\.)warcraftlogs\.com$/i.test(url.hostname)) return null;
  const match = /^\/guild\/([^/]+)\/([^/]+)\/([^/]+)\/?$/i.exec(url.pathname);
  if (!match) return null;
  try {
    const [region, server, name] = [match[1]!, match[2]!, match[3]!].map((part) => decodeURIComponent(part));
    return { region: region!.toLowerCase(), server: server!.toLowerCase(), name: name!.trim() };
  } catch {
    return null;
  }
}

/** True for text that looks like a try at a WarcraftLogs link, so a failed parse can say so. */
export function looksLikeLink(text: string): boolean {
  return /warcraftlogs\.com|^https?:\/\//i.test(text.trim());
}
