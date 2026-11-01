import { describe, expect, it } from "vitest";

import { formatResetTime, pointsView } from "./points";

describe("pointsView", () => {
  it("says how many points are left of the hour's allowance", () => {
    const view = pointsView({ limit: 180, left: 120 });
    expect(view.label).toBe("120 of 180 left this hour");
    expect(view.state).toBe("part");
    expect(view.fraction).toBeCloseTo(0.667, 2);
    expect(view.valueNow).toBe(120);
  });

  it("is empty at zero", () => {
    const view = pointsView({ limit: 180, left: 0 });
    expect(view.label).toBe("0 of 180 left this hour");
    expect(view.state).toBe("empty");
    expect(view.fraction).toBe(0);
  });

  it("is full when nothing is used", () => {
    const view = pointsView({ limit: 180, left: 180 });
    expect(view.label).toBe("180 of 180 left this hour");
    expect(view.state).toBe("full");
    expect(view.fraction).toBe(1);
  });

  it("rounds points left down and keeps them within the allowance", () => {
    expect(pointsView({ limit: 180, left: 119.9 }).label).toBe("119 of 180 left this hour");
    expect(pointsView({ limit: 180, left: 400 }).fraction).toBe(1);
    expect(pointsView({ limit: 180, left: -3 }).state).toBe("empty");
  });

  it("copes with an allowance of zero", () => {
    const view = pointsView({ limit: 0, left: 0 });
    expect(view.fraction).toBe(0);
    expect(view.state).toBe("empty");
  });

  it("groups thousands", () => {
    expect(pointsView({ limit: 3600, left: 1234 }).label).toBe("1,234 of 3,600 left this hour");
  });
});

describe("formatResetTime", () => {
  const iso = "2026-11-01T20:00:00Z";
  it("writes the reset in the viewer's local time", () => {
    expect(formatResetTime(iso, { locale: "en-GB", timeZone: "Europe/Paris" })).toBe("21:00");
    expect(formatResetTime(iso, { locale: "en-GB", timeZone: "UTC" })).toBe("20:00");
    expect(formatResetTime(iso, { locale: "en-GB", timeZone: "Asia/Kolkata" })).toBe("01:30");
  });

  it("is empty for a time it cannot read", () => {
    expect(formatResetTime("soon")).toBe("");
  });
});
