import { describe, expect, it } from "vitest";

import { barFill, fightPctDescription, fightPctLabel, qualityOf } from "./quality";

describe("qualityOf", () => {
  it.each([
    [100, "poor"],
    [90, "poor"],
    [89.99, "common"],
    [75, "common"],
    [74.99, "uncommon"],
    [50, "uncommon"],
    [49.99, "rare"],
    [25, "rare"],
    [24.99, "epic"],
    [10, "epic"],
    [9.99, "legendary"],
    [5.57, "legendary"],
    [0, "legendary"],
  ] as const)("a wipe with %s%% left is %s", (pct, quality) => {
    expect(qualityOf(pct, false)).toBe(quality);
  });

  it("is artifact for a kill whatever the figure", () => {
    expect(qualityOf(0, true)).toBe("artifact");
    expect(qualityOf(96.6, true)).toBe("artifact");
  });

  it("clamps figures outside 0 to 100", () => {
    expect(qualityOf(140, false)).toBe("poor");
    expect(qualityOf(-3, false)).toBe("legendary");
    expect(qualityOf(Number.NaN, false)).toBe("poor");
  });
});

describe("barFill", () => {
  it("fills toward a kill", () => {
    expect(barFill(96.6, false)).toBeCloseTo(3.4);
    expect(barFill(5.57, false)).toBeCloseTo(94.43);
    expect(barFill(100, false)).toBe(0);
    expect(barFill(0, false)).toBe(100);
  });

  it("is full for a kill", () => {
    expect(barFill(42, true)).toBe(100);
  });
});

describe("labels", () => {
  it("says Kill or the figure", () => {
    expect(fightPctLabel(5.57, false)).toBe("5.6%");
    expect(fightPctLabel(0, true)).toBe("Kill");
    expect(fightPctDescription(5.57, false)).toBe("Wipe with 5.6% of the fight left");
    expect(fightPctDescription(0, true)).toBe("Kill");
  });
});
