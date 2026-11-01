import { describe, expect, it } from "vitest";

import { abbreviate, assignCooldownTiers, buildTimeline, humanize, indexOfRef, stepIndex } from "./markers";
import type { PullTimelineData } from "./queries";

const R = "ev:TEST:15";

function sample(): PullTimelineData {
  return {
    pull_key: "TEST:15",
    ref: `${R}:pull`,
    duration_ms: 100_000,
    cutoff_ms: 80_000,
    phases: [{ id: "p1", name: "Stage One", start_ms: 0, end_ms: 100_000 }],
    deaths: [
      { t_ms: 50_000, name: "Kouw", after_cutoff: false, ref: `${R}:death:2` },
      { t_ms: 20_000, name: "Mortamur", after_cutoff: false, ref: `${R}:death:1` },
      { t_ms: 90_000, name: "Late", after_cutoff: true, ref: `${R}:death:3` },
    ],
    mechanics: [
      { mechanic_id: "quiet", name: null, cluster: 1, t_ms: 20_000, end_ms: 21_000, hits: 9, failures: 0, ref: `${R}:mech:quiet` },
      { mechanic_id: "caustic_waves", name: "Caustic Waves", cluster: 1, t_ms: 20_000, end_ms: 25_000, hits: 3, failures: 2, ref: `${R}:mech:caustic_waves` },
      { mechanic_id: "caustic_waves", name: "Caustic Waves", cluster: 2, t_ms: 60_000, end_ms: 61_000, hits: 3, failures: 1, ref: `${R}:mech:caustic_waves` },
    ],
    cooldowns: [
      { t_ms: 30_000, spell_id: 51052, spell: "Anti-Magic Zone", caster: "A", category: "defensive", ref: `${R}:cd:51052:1` },
      { t_ms: 31_000, spell_id: 29166, spell: "Innervate", caster: "B", category: "utility", ref: `${R}:cd:29166:1` },
      { t_ms: 70_000, spell_id: 51052, spell: "Anti-Magic Zone", caster: "A", category: "defensive", ref: `${R}:cd:51052:2` },
    ],
    windows: [{ t_ms: 50_000, window_id: "rots", name: "Rage of the Shackled", occurrence: 1, covered: false, ref: `${R}:win:rots:1` }],
  };
}

describe("buildTimeline", () => {
  const model = buildTimeline(sample());

  it("orders every marker by time, then lane, then id, across lanes", () => {
    expect(model.ordered.map((m) => `${m.tMs}:${m.lane}`)).toEqual([
      "20000:mechanic",
      "20000:mechanic",
      "20000:death",
      "30000:cooldown",
      "31000:cooldown",
      "50000:death",
      "50000:window",
      "60000:mechanic",
      "70000:cooldown",
      "90000:death",
    ]);
  });

  it("makes one row per mechanic, failures first, with names filled in", () => {
    expect(model.rows.map((r) => [r.name, r.failures, r.marks.length])).toEqual([
      ["Caustic Waves", 3, 2],
      ["Quiet", 0, 1],
    ]);
  });

  it("labels markers for screen readers", () => {
    const labels = model.ordered.map((m) => m.label);
    expect(labels).toContain("Death 2, Kouw, 0:50.0");
    expect(labels).toContain("Death 3, Late, 1:30.0, after cutoff");
    expect(labels).toContain("Caustic Waves, 0:20.0, 2 failures");
    expect(labels).toContain("Quiet, 0:20.0, no failures");
    expect(labels).toContain("Rage of the Shackled 1, 0:50.0, missed");
  });

  it("counts cooldown uses of a spell", () => {
    const amz = model.cooldowns.filter((c) => c.spellId === 51052);
    expect(amz.map((c) => [c.useN, c.useOf])).toEqual([
      [1, 2],
      [2, 2],
    ]);
  });

  it("gives clusters of one mechanic distinct ids but the same ref", () => {
    const marks = model.rows[0]!.marks;
    expect(new Set(marks.map((m) => m.id)).size).toBe(2);
    expect(new Set(marks.map((m) => m.ref)).size).toBe(1);
  });

  it("finds a ref's first marker", () => {
    expect(indexOfRef(model.ordered, `${R}:death:2`)).toBe(5);
    expect(indexOfRef(model.ordered, null)).toBe(-1);
    expect(indexOfRef(model.ordered, "nope")).toBe(-1);
  });
});

describe("assignCooldownTiers", () => {
  it("keeps spread casts on the upper tier", () => {
    expect(assignCooldownTiers([0, 100, 200], 50)).toEqual([0, 0, 0]);
  });

  it("drops a cast close to the last one to the lower tier", () => {
    expect(assignCooldownTiers([0, 10, 200], 50)).toEqual([0, 1, 0]);
  });

  it("never lets three close casts hide each other where a tier is free", () => {
    expect(assignCooldownTiers([0, 10, 60, 70], 50)).toEqual([0, 1, 0, 1]);
  });

  it("falls back to the tier with the older cast when both are crowded", () => {
    expect(assignCooldownTiers([0, 10, 20], 50)).toEqual([0, 1, 0]);
  });
});

describe("stepIndex", () => {
  it("steps without wrapping", () => {
    expect(stepIndex(5, 2, "ArrowRight")).toBe(3);
    expect(stepIndex(5, 4, "ArrowRight")).toBe(4);
    expect(stepIndex(5, 0, "ArrowLeft")).toBe(0);
    expect(stepIndex(5, 2, "ArrowUp")).toBe(1);
  });

  it("jumps to the ends and starts from an end when nothing is focused", () => {
    expect(stepIndex(5, 2, "Home")).toBe(0);
    expect(stepIndex(5, 2, "End")).toBe(4);
    expect(stepIndex(5, -1, "ArrowRight")).toBe(0);
    expect(stepIndex(5, -1, "ArrowLeft")).toBe(4);
    expect(stepIndex(0, -1, "ArrowRight")).toBe(-1);
  });
});

describe("names", () => {
  it("humanizes ids and abbreviates windows", () => {
    expect(humanize("caustic_waves")).toBe("Caustic waves");
    expect(abbreviate("Rage of the Shackled")).toBe("RotS");
    expect(abbreviate("Soak")).toBe("Soak");
  });
});
