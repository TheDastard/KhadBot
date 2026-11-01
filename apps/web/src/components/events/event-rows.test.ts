import { describe, expect, it } from "vitest";

import { asEventRows, rowCount, rowFlag, windowTitle, type EventRow } from "./event-rows";

const base: EventRow = { ref: "ev:T:1:event:1", seq: 1, t_ms: 1000, type: "damage" };

describe("rowFlag", () => {
  it("names a failure and who it is charged to", () => {
    expect(rowFlag({ ...base, failure: true, blamed: "Kouw" })).toEqual({ text: "Failure · Kouw", tone: "bad" });
    expect(rowFlag({ ...base, failure: true })).toEqual({ text: "Failure", tone: "bad" });
  });

  it("notes deaths and overkill, and leaves the rest blank", () => {
    expect(rowFlag({ ...base, type: "death" }).text).toBe("Death");
    expect(rowFlag({ ...base, overkill: 313512 }).text).toBe("Overkill 313,512");
    expect(rowFlag(base)).toEqual({ text: "", tone: "none" });
  });
});

describe("asEventRows", () => {
  it("keeps rows with a ref, a type and a time", () => {
    expect(asEventRows([{ ...base }, { ref: 5 }, {}])).toHaveLength(1);
  });
});

describe("headings", () => {
  it("writes the window and the row count", () => {
    expect(windowTitle(420_341, 430_341)).toBe("7:00.3 – 7:10.3");
    expect(rowCount(13, 13)).toBe("13 of 13 rows");
    expect(rowCount(1, 1)).toBe("1 of 1 row");
    expect(rowCount(200, 1500)).toBe("200 of 1,500 rows");
  });
});
