import { describe, expect, it } from "vitest";

import { buildRef, parseRef, pullPagePath, pullPath, pullRef, refShortLabel } from "./refs";

const R = "H7z1fqDXjRTFnbtg";

describe("parseRef", () => {
  it.each([
    [`ev:${R}:15:death:2`, { kind: "death", report: R, fight: 15, n: 2 }],
    [`ev:${R}:15:mech:caustic_waves`, { kind: "mech", report: R, fight: 15, key: "caustic_waves" }],
    [`ev:${R}:15:cd:29166:3`, { kind: "cd", report: R, fight: 15, spellId: 29166, n: 3 }],
    [`ev:${R}:15:win:rage_of_the_shackled:2`, { kind: "win", report: R, fight: 15, key: "rage_of_the_shackled", n: 2 }],
    [`ev:${R}:15:event:0`, { kind: "event", report: R, fight: 15, n: 0 }],
    [`ev:${R}:15:pull`, { kind: "pull", report: R, fight: 15 }],
    ["plan:night-1:0", { kind: "plan", key: "night-1", n: 0 }],
    [`  ev:${R}:15:pull  `, { kind: "pull", report: R, fight: 15 }],
  ])("parses %s", (text, parsed) => {
    expect(parseRef(text)).toEqual(parsed);
  });

  it.each([
    "",
    "nonsense",
    `ev::15:pull`,
    `ev:${R}:x:pull`,
    `ev:${R}:-1:pull`,
    `ev:${R}:15:death:0`,
    `ev:${R}:15:death`,
    `ev:${R}:15:death:1:2`,
    `ev:${R}:15:cd:29166:0`,
    `ev:${R}:15:win:w:0`,
    `ev:${R}:15:event:-1`,
    `ev:${R}:15:mech:`,
    `ev:${R}:15:unknown:1`,
    "plan:x",
    "plan::1",
    "plan:x:-1",
  ])("rejects %j", (text) => {
    expect(parseRef(text)).toBeNull();
  });

  it("round-trips through buildRef", () => {
    for (const text of [
      `ev:${R}:15:death:2`,
      `ev:${R}:15:mech:m`,
      `ev:${R}:15:cd:1:1`,
      `ev:${R}:15:win:w:3`,
      `ev:${R}:15:event:9`,
      `ev:${R}:15:pull`,
      "plan:p:2",
    ]) {
      const parsed = parseRef(text);
      expect(parsed).not.toBeNull();
      expect(buildRef(parsed!)).toBe(text);
    }
  });
});

describe("paths and labels", () => {
  it("knows each ref's pull page", () => {
    expect(pullPath(parseRef(`ev:${R}:15:death:2`)!)).toBe(`/r/${R}/p/15`);
    expect(pullPath(parseRef("plan:p:1")!)).toBeNull();
    expect(pullPagePath("a b", 3)).toBe("/r/a%20b/p/3");
    expect(pullRef(R, 20)).toBe(`ev:${R}:20:pull`);
  });

  it("labels chips briefly", () => {
    expect(refShortLabel(parseRef(`ev:${R}:15:death:2`)!)).toBe("death 2");
    expect(refShortLabel(parseRef(`ev:${R}:15:win:w:2`)!)).toBe("win 2");
    expect(refShortLabel(parseRef(`ev:${R}:15:cd:1:1`)!)).toBe("cd");
  });
});
