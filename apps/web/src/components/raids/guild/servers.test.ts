// apps/web/src/components/raids/guild/servers.test.ts: server filtering with its dividers, the bolded prefix and guild URL parsing.

import { describe, expect, it } from "vitest";

import { exactServer, filterServers, findServer, foldName, parseGuildUrl, prefixLength, type ServerList } from "./servers";

const entry = (name: string, slug: string, region: string) => ({ name, slug, region, match: foldName(name) });
const LIST: ServerList = {
  regions: [
    {
      slug: "us",
      name: "US",
      complete: true,
      servers: [entry("Area 52", "area-52", "us"), entry("Stormrage", "stormrage", "us"), entry("Stormreaver", "stormreaver", "us")],
    },
    {
      slug: "eu",
      name: "EU",
      complete: true,
      servers: [entry("Aggra (Português)", "aggra-português", "eu"), entry("Stormrage", "stormrage", "eu")],
    },
  ],
};

describe("filterServers", () => {
  it("keeps United States and Europe as dividers, each with its own matches", () => {
    const groups = filterServers(LIST, "Storm");
    expect(groups.map((g) => g.heading)).toEqual(["United States", "Europe"]);
    expect(groups[0]!.servers.map((s) => s.name)).toEqual(["Stormrage", "Stormreaver"]);
    expect(groups[1]!.servers.map((s) => `${s.name} ${s.regionName}`)).toEqual(["Stormrage EU"]);
  });

  it("drops a region with no match, and folds case, accents and punctuation", () => {
    expect(filterServers(LIST, "aggra port").map((g) => g.heading)).toEqual(["Europe"]);
    expect(filterServers(LIST, "AREA52").map((g) => g.servers[0]!.slug)).toEqual(["area-52"]);
    expect(filterServers(LIST, "zzz")).toEqual([]);
  });

  it("lists everything for an empty field", () => {
    expect(filterServers(LIST, "").flatMap((g) => g.servers)).toHaveLength(5);
  });
});

describe("prefixLength", () => {
  it("covers the typed characters, spaces and punctuation included", () => {
    expect(prefixLength("Stormrage", "storm")).toBe(5);
    expect(prefixLength("Area 52", "area5")).toBe(6);
    expect(prefixLength("Aggra (Português)", "aggrapor")).toBe(10);
    expect(prefixLength("Stormrage", "")).toBe(0);
    expect(prefixLength("Stormrage", "xyz")).toBe(0);
  });
});

describe("findServer and exactServer", () => {
  it("finds by region and slug", () => {
    expect(findServer(LIST, "EU", "stormrage")?.regionName).toBe("EU");
    expect(findServer(LIST, "us", "nowhere")).toBeNull();
  });

  it("takes a typed name only when exactly one server has it", () => {
    expect(exactServer(LIST, "Area 52")?.slug).toBe("area-52");
    expect(exactServer(LIST, "Stormrage")).toBeNull();
    expect(exactServer(LIST, "Storm")).toBeNull();
  });
});

describe("parseGuildUrl", () => {
  it("reads region, server and the percent-decoded name", () => {
    expect(parseGuildUrl("https://www.warcraftlogs.com/guild/us/skullcrusher/death%20and%20destruction")).toEqual({
      region: "us",
      server: "skullcrusher",
      name: "death and destruction",
    });
    expect(parseGuildUrl("warcraftlogs.com/guild/EU/silvermoon/Some%20Guild/")?.region).toBe("eu");
  });

  it("refuses anything else", () => {
    expect(parseGuildUrl("Death and Destruction")).toBeNull();
    expect(parseGuildUrl("https://www.warcraftlogs.com/reports/H7z1fqDXjRTFnbtg")).toBeNull();
    expect(parseGuildUrl("https://example.com/guild/us/skullcrusher/x")).toBeNull();
    expect(parseGuildUrl("")).toBeNull();
  });
});
