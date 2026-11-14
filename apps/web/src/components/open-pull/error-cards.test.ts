// apps/web/src/components/open-pull/error-cards.test.ts
//
// Which card each API error or failed job gets on the opening-a-pull screen.

import { describe, expect, it } from "vitest";

import { errorCard } from "./error-cards";

describe("errorCard", () => {
  it("offers sign-in when too many visitors' pulls are loading", () => {
    const card = errorCard({ code: "visitor_queue_full", message: "Many pulls are being opened right now." });
    expect(card.action).toBe("sign-in");
    expect(card.tone).toBe("info");
    expect(card.title).toMatch(/many pulls are loading/i);
  });

  it.each(["rate_limited", "budget_wait", "budget_exhausted"])("shows the busy card with retry for %s", (code) => {
    const card = errorCard({ code, message: "" });
    expect(card).toMatchObject({ code, action: "retry", tone: "warn" });
  });

  it("answers a job that is gone, or on a report that can no longer be read, with start again", () => {
    expect(errorCard({ code: "job_not_found", message: "No such job." }).action).toBe("start-again");
  });

  it("falls back to the server's message for an unknown code", () => {
    const card = errorCard({ code: "something_new", message: "Details from the server." });
    expect(card).toMatchObject({ body: "Details from the server.", action: "start-again" });
  });
});
