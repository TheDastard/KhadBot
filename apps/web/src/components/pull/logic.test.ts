import { describe, expect, it } from "vitest";

import { allCooldownUses, cutoffSentence, cutoffTail, deathNumber, describeAnomaly, foldAnomalies, ordinal, splitKillsAndWipes, topCharged } from "./logic";
import { buildNames, humanize, spellIdOf } from "./names";
import type { PullDigestData } from "@/lib/queries";

describe("rail ordering", () => {
  it("puts kills first and keeps pull order within each", () => {
    const pulls = [
      { fight: 10, outcome: "wipe" as const },
      { fight: 15, outcome: "wipe" as const },
      { fight: 20, outcome: "kill" as const },
      { fight: 25, outcome: "kill" as const },
    ];
    const { kills, wipes } = splitKillsAndWipes(pulls);
    expect(kills.map((p) => p.fight)).toEqual([20, 25]);
    expect(wipes.map((p) => p.fight)).toEqual([10, 15]);
  });
});

describe("anomaly folding", () => {
  const codes = ["cooldown_unverified:871", "no_hp_data", "cooldown_unverified:47788", "unregistered_damage:12.5"];

  it("folds one group per kind in order of first appearance", () => {
    const groups = foldAnomalies(codes);
    expect(groups.map((g) => g.kind)).toEqual(["cooldown_unverified", "no_hp_data", "unregistered_damage"]);
    expect(groups[0]).toEqual({ kind: "cooldown_unverified", args: ["871", "47788"], count: 2 });
    expect(groups[1]?.args).toEqual([""]);
  });

  it("titles a group with its count and flags spell arguments", () => {
    const [cd, , unreg] = foldAnomalies(codes).map(describeAnomaly);
    expect(cd?.title).toBe("2 cooldowns not yet verified");
    expect(cd?.spells).toBe(true);
    expect(unreg?.title).toBe("12.5% of damage taken is unregistered");
  });

  it("singularises and passes unknown kinds through", () => {
    expect(describeAnomaly(foldAnomalies(["cooldown_unverified:1"])[0]!).title).toBe("1 cooldown not yet verified");
    expect(describeAnomaly(foldAnomalies(["odd_thing:x"])[0]!).title).toBe("odd_thing");
  });
});

describe("cutoff note", () => {
  it("writes ordinals", () => {
    expect([1, 2, 3, 4, 11, 12, 21, 22].map(ordinal)).toEqual(["1st", "2nd", "3rd", "4th", "11th", "12th", "21st", "22nd"]);
  });

  it("names the death and the time", () => {
    expect(cutoffSentence(11, 577773)).toBe("Analysis stops at the 11th death, 9:37.8, when half the raid had died.");
  });

  it("says what the switch changes", () => {
    expect(cutoffTail(25, 11, false)).toBe("14 later deaths are hidden.");
    expect(cutoffTail(25, 11, true)).toBe("Showing all 25 deaths; the 14 after the cutoff are muted.");
    expect(cutoffTail(12, 11, false)).toBe("1 later death is hidden.");
    expect(cutoffTail(null, null, false)).toBe("");
  });
});

describe("digest helpers", () => {
  it("merges cooldown categories in time order", () => {
    const use = (spell_id: number, t_ms: number, category: "defensive" | "offensive" | "utility") => ({
      caster: "A", category, phase_id: null, ref: `r${spell_id}`, spell_id, spell_name: "S", t_ms,
    });
    const digest = {
      defensive_cooldowns: [use(1, 300, "defensive")],
      offensive_cooldowns: [use(2, 100, "offensive")],
      utility_cooldowns: [use(3, 200, "utility")],
    } as unknown as PullDigestData;
    expect(allCooldownUses(digest).map((c) => c.spell_id)).toEqual([2, 3, 1]);
  });

  it("reads the death number from a ref", () => {
    expect(deathNumber("ev:H7z1fqDXjRTFnbtg:15:death:2")).toBe(2);
    expect(deathNumber("ev:H7z1fqDXjRTFnbtg:15:pull")).toBeNull();
  });

  it("finds the most charged player", () => {
    expect(topCharged({ B: 2, A: 2, C: 1 })).toEqual({ name: "A", failures: 2 });
    expect(topCharged({ A: 0 })).toBeNull();
  });
});

describe("names", () => {
  it("falls back to readable IDs", () => {
    const names = buildNames(undefined, undefined, { 5: "Extra" });
    expect(names.mechanic("caustic_waves")).toBe("Caustic waves");
    expect(names.spell(5)).toBe("Extra");
    expect(names.spell(6)).toBe("Spell 6");
    expect(names.phase(null)).toBeNull();
    expect(humanize("p3")).toBe("P3");
  });

  it("only real spells link out", () => {
    expect([spellIdOf(0), spellIdOf(1), spellIdOf(2), spellIdOf(null)]).toEqual([null, null, 2, null]);
  });
});
