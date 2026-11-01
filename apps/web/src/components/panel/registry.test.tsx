import { describe, expect, it } from "vitest";

import { EVIDENCE_RENDERERS, REF_KIND_VIEWS } from "./registry";
import { formatClockMillis } from "./views";

describe("renderer registry", () => {
  it("has a view for every ref kind a URL can hold", () => {
    const withRecords = Object.keys(REF_KIND_VIEWS).filter((k) => REF_KIND_VIEWS[k as keyof typeof REF_KIND_VIEWS] === "evidence");
    expect(Object.keys(EVIDENCE_RENDERERS).sort()).toEqual(withRecords.sort());
    expect(REF_KIND_VIEWS.plan).toBe("plan");
    expect(Object.keys(REF_KIND_VIEWS).sort()).toEqual(["cd", "death", "event", "mech", "plan", "pull", "win"]);
  });

  it("formats an event's exact time", () => {
    expect(formatClockMillis(424722)).toBe("7:04.722");
    expect(formatClockMillis(5)).toBe("0:00.005");
  });
});
