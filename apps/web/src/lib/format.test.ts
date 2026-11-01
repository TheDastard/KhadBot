import { describe, expect, it } from "vitest";

import {
  formatClock,
  formatClockTenths,
  formatCompact,
  formatCountdown,
  formatDuration,
  formatNumber,
  formatPct,
} from "./format";

describe("clocks", () => {
  it("formats m:ss, truncating to the second", () => {
    expect(formatClock(0)).toBe("0:00");
    expect(formatClock(9_999)).toBe("0:09");
    expect(formatClock(425_341)).toBe("7:05");
    expect(formatClock(-500)).toBe("0:00");
    expect(formatDuration(618)).toBe("10:18");
  });

  it("formats m:ss.s, rounding to the tenth", () => {
    expect(formatClockTenths(425_341)).toBe("7:05.3");
    expect(formatClockTenths(59_960)).toBe("1:00.0");
    expect(formatClockTenths(457_080)).toBe("7:37.1");
    expect(formatClockTenths(0)).toBe("0:00.0");
  });

  it("counts down rounding up", () => {
    expect(formatCountdown(41.2)).toBe("0:42");
    expect(formatCountdown(0)).toBe("0:00");
    expect(formatCountdown(125)).toBe("2:05");
  });
});

describe("percentages", () => {
  it("shows one decimal", () => {
    expect(formatPct(5.57)).toBe("5.6%");
    expect(formatPct(96.6)).toBe("96.6%");
    expect(formatPct(100)).toBe("100.0%");
    expect(formatPct(0)).toBe("0.0%");
  });

  it("never rounds a tiny remainder to zero", () => {
    expect(formatPct(0.01)).toBe("<0.1%");
    expect(formatPct(0.05)).toBe("0.1%");
  });

  it("honours other precisions", () => {
    expect(formatPct(66.666, 0)).toBe("67%");
  });
});

describe("amounts", () => {
  it("groups thousands", () => {
    expect(formatNumber(1_234_567.4)).toBe("1,234,567");
    expect(formatNumber(950)).toBe("950");
  });

  it("shortens large ones", () => {
    expect(formatCompact(950)).toBe("950");
    expect(formatCompact(12_345)).toBe("12.3k");
    expect(formatCompact(12_000)).toBe("12k");
    expect(formatCompact(1_250_000)).toBe("1.3M");
    expect(formatCompact(-4_200)).toBe("-4.2k");
  });
});
