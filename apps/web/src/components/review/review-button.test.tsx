import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReviewControl } from "./review-button";
import type { ReviewView } from "./logic";

afterEach(cleanup);

function show(view: ReviewView, onStart = vi.fn()) {
  render(<ReviewControl view={view} onStart={onStart} />);
  return onStart;
}

describe("the review control", () => {
  it("offers the button with the estimate and the reviews left, and starts on click", () => {
    const onStart = show({ kind: "ready", tokens: 10500, remaining: 7, limit: 10 });
    expect(screen.getByText("About 10.5k tokens · 7 of 10 reviews left")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Review this pull" }));
    expect(onStart).toHaveBeenCalledOnce();
  });

  it("shows queued, then progress with a labelled progress bar", () => {
    show({ kind: "running", pct: 0, waiting: true });
    expect(screen.getByRole("status").textContent).toBe("Review queued…");
    cleanup();
    show({ kind: "running", pct: 40, waiting: false });
    expect(screen.getByText("Reviewing… 40%")).toBeTruthy();
    expect(screen.getByRole("progressbar", { name: "Review progress" }).getAttribute("aria-valuenow")).toBe("40");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("says a cached review is up to date, with no button", () => {
    show({ kind: "cached" });
    expect(screen.getByText("Reviewed")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("says the limit is reached, with no button", () => {
    show({ kind: "quota", limit: 10, used: 10, nextSlotAt: null });
    expect(screen.getByRole("status").textContent).toContain("Review limit reached: 10 of 10");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("shows a failure with a word and a way to try again", () => {
    const onStart = show({ kind: "failed", message: "The review failed unexpectedly.", code: "internal_error" });
    expect(screen.getByText("Failed")).toBeTruthy();
    expect(screen.getByText("The review failed unexpectedly.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onStart).toHaveBeenCalledOnce();
  });

  it("shows a visitor a sign-in line and an unavailable status as nothing", () => {
    show({ kind: "signed-out" });
    expect(screen.getByText("Sign in to review this pull.")).toBeTruthy();
    cleanup();
    const { container } = render(<ReviewControl view={{ kind: "unavailable" }} onStart={() => undefined} />);
    expect(container.textContent).toBe("");
  });
});
