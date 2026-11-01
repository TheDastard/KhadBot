import { describe, expect, it } from "vitest";

import { clampWindow, fitWindow, formatTick, makeTimeScale, tickStepMs, tickTimes, zoomWindowAround } from "./scale";

describe("makeTimeScale", () => {
  const scale = makeTimeScale(618_026, 1000);

  it("maps milliseconds to pixels and back", () => {
    expect(scale.toX(0)).toBe(0);
    expect(scale.toX(618_026)).toBe(1000);
    expect(scale.toX(309_013)).toBeCloseTo(500, 6);
    for (const ms of [0, 12_345, 425_341, 618_026]) {
      expect(scale.toMs(scale.toX(ms))).toBeCloseTo(ms, 6);
    }
  });

  it("clamps outside the axis", () => {
    expect(scale.toX(-5)).toBe(0);
    expect(scale.toX(10_000_000)).toBe(1000);
    expect(scale.toMs(-20)).toBe(0);
    expect(scale.toMs(5000)).toBe(618_026);
  });

  it("gives percentages for CSS positions", () => {
    expect(scale.toPct(309_013)).toBeCloseTo(50, 6);
  });

  it("survives an empty fight or an unmeasured axis", () => {
    expect(makeTimeScale(0, 800).toX(100)).toBe(0);
    expect(makeTimeScale(1000, 0).toMs(50)).toBe(0);
  });
});

describe("ticks", () => {
  it("steps by the pull's length", () => {
    expect(tickStepMs(40_000)).toBe(5000);
    expect(tickStepMs(120_000)).toBe(10_000);
    expect(tickStepMs(618_026)).toBe(60_000);
    expect(tickStepMs(3 * 3600_000)).toBe(900_000);
    expect(tickStepMs(20 * 3600_000)).toBe(1_800_000);
  });

  it("lists every step from zero", () => {
    const times = tickTimes(618_026);
    expect(times[0]).toBe(0);
    expect(times[times.length - 1]).toBe(600_000);
    expect(times).toHaveLength(11);
    expect(formatTick(600_000)).toBe("10:00");
  });
});

describe("clampWindow", () => {
  it("orders the edges and keeps them inside the fight", () => {
    expect(clampWindow(430_000, 420_000, 618_026)).toEqual({ startMs: 420_000, endMs: 430_000 });
    expect(clampWindow(-1000, 5000, 618_026)).toEqual({ startMs: 0, endMs: 5000 });
    expect(clampWindow(610_000, 700_000, 618_026)).toEqual({ startMs: 610_000, endMs: 618_026 });
  });

  it("limits a window to 30 s, keeping its start", () => {
    expect(clampWindow(100_000, 200_000, 618_026)).toEqual({ startMs: 100_000, endMs: 130_000 });
  });

  it("treats a click as no window", () => {
    expect(clampWindow(100_000, 100_100, 618_026)).toBeNull();
    expect(clampWindow(NaN, 1, 10)).toBeNull();
  });
});

describe("zoomWindowAround", () => {
  it("centres 10 s on the marker", () => {
    expect(zoomWindowAround(425_341, 618_026)).toEqual({ startMs: 420_341, endMs: 430_341 });
  });

  it("slides to stay inside the fight", () => {
    expect(zoomWindowAround(2000, 618_026)).toEqual({ startMs: 0, endMs: 10_000 });
    expect(zoomWindowAround(617_000, 618_026)).toEqual({ startMs: 608_026, endMs: 618_026 });
  });

  it("gives the whole fight when it is shorter than the span", () => {
    expect(zoomWindowAround(3000, 6000)).toEqual({ startMs: 0, endMs: 6000 });
  });
});

describe("fitWindow", () => {
  it("makes a URL window safe for the events route", () => {
    expect(fitWindow({ startMs: 0, endMs: 100_000 }, 618_026)).toEqual({ startMs: 0, endMs: 30_000 });
    expect(fitWindow({ startMs: 700_000, endMs: 710_000 }, 618_026)).toBeNull();
  });
});
