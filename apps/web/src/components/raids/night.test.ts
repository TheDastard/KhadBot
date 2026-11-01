// apps/web/src/components/raids/night.test.ts: chip ordering, best-pull choice and the lines of text on the Raids page.

import { describe, expect, it } from "vitest";

import {
  bestFightOf,
  bestPullOf,
  groupSub,
  nightFromLookup,
  nightMeta,
  pullLabel,
  splitPulls,
  summaryLine,
  type LookupData,
  type Night,
  type NightGroup,
  type NightPull,
} from "./night";

const pull = (fight: number, ordinal: number, kill: boolean, bossPct: number | null, durationMs = 60_000): NightPull => ({
  fight,
  ordinal,
  kill,
  bossPct,
  durationMs,
});

const GROUP: NightGroup = {
  encounterId: 3492,
  name: "Ula'tek",
  difficulty: "Heroic",
  bestFight: null,
  pulls: [pull(10, 1, false, 96.6, 37_000), pull(11, 2, false, 5.57, 618_000), pull(12, 3, true, 0, 579_000), pull(13, 4, false, 5.65)],
};

describe("splitPulls", () => {
  it("puts kills first and keeps wipes in pull order", () => {
    const { kills, wipes } = splitPulls(GROUP.pulls);
    expect(kills.map((p) => p.fight)).toEqual([12]);
    expect(wipes.map((p) => p.fight)).toEqual([10, 11, 13]);
  });

  it("has no kills for a night of wipes", () => {
    expect(splitPulls([pull(1, 1, false, 50)]).kills).toEqual([]);
  });
});

describe("bestPullOf", () => {
  it("is the first kill", () => {
    expect(bestPullOf([pull(1, 1, false, 3), pull(2, 2, true, 0), pull(3, 3, true, 0)])?.fight).toBe(2);
  });

  it("is the lowest fight % left when nobody killed", () => {
    expect(bestPullOf([pull(1, 1, false, 40), pull(2, 2, false, 5.6), pull(3, 3, false, 5.7)])?.fight).toBe(2);
  });

  it("takes the first of equal wipes and treats an unknown % as the worst", () => {
    expect(bestPullOf([pull(1, 1, false, null), pull(2, 2, false, 20), pull(3, 3, false, 20)])?.fight).toBe(2);
  });

  it("is null without pulls", () => {
    expect(bestPullOf([])).toBeNull();
  });
});

describe("bestFightOf", () => {
  it("trusts the source's best pull when it is one of the pulls", () => {
    expect(bestFightOf({ ...GROUP, bestFight: 10 })).toBe(10);
  });

  it("works it out when the source names none or a stranger", () => {
    expect(bestFightOf(GROUP)).toBe(12);
    expect(bestFightOf({ ...GROUP, bestFight: 99 })).toBe(12);
  });
});

describe("text", () => {
  it("summarises the nights", () => {
    const night: Night = { code: "A", title: null, startedAt: "2026-10-06T19:00:00Z", groups: [GROUP] };
    expect(summaryLine([night])).toBe("1 night · 4 boss pulls");
    expect(summaryLine([night, night])).toBe("2 nights · 8 boss pulls");
    expect(nightMeta(night)).toBe("Oct 6, 2026 · 4 boss pulls");
    expect(nightMeta({ ...night, startedAt: null })).toBe("4 boss pulls");
  });

  it("describes a group", () => {
    expect(groupSub(GROUP)).toBe("Heroic · 4 pulls · best kill");
    const wipes = { ...GROUP, pulls: GROUP.pulls.filter((p) => !p.kill) };
    expect(groupSub(wipes)).toBe("Heroic · 3 pulls · best 5.6%");
    expect(groupSub({ ...GROUP, difficulty: null, pulls: [GROUP.pulls[0]!] })).toBe("1 pull · best 96.6%");
  });

  it("describes a pull", () => {
    expect(pullLabel(GROUP, GROUP.pulls[0]!, false)).toBe("P1 0:37 96.6%, Ula'tek Heroic, fight 10, wipe with 96.6% left");
    expect(pullLabel(GROUP, GROUP.pulls[2]!, true)).toBe("P3 9:39 Kill, Ula'tek Heroic, fight 12, best pull");
  });
});

describe("nightFromLookup", () => {
  it("adapts the lookup, naming difficulties for players", () => {
    const data: LookupData = {
      code: "ABC",
      title: "Night",
      started_at: "2026-10-06T19:00:00Z",
      zone: null,
      focus_fight: null,
      groups: [
        {
          encounter_id: 1,
          encounter_name: "Boss",
          difficulty: 5,
          difficulty_name: "mythic",
          best_fight: 3,
          pulls: [
            { fight: 3, ordinal: 1, label: "P1", outcome: "wipe", duration_ms: 1000, boss_pct: 12.3, boss_hp_pct: 12, ready: true },
          ],
        },
      ],
    };
    const night = nightFromLookup(data);
    expect(night.groups[0]).toMatchObject({ difficulty: "Mythic", bestFight: 3 });
    expect(night.groups[0]!.pulls[0]).toEqual({ fight: 3, ordinal: 1, kill: false, bossPct: 12.3, durationMs: 1000 });
  });
});
