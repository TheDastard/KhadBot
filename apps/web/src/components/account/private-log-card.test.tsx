import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PrivateLogCard, SIGN_IN_PERKS } from "./private-log-card";

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams("source=ABC123"),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe("PrivateLogCard", () => {
  it("explains the three things signing in allows and links back to this log", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "http://api.test");
    render(<PrivateLogCard />);
    expect(screen.getByRole("heading", { name: "This log is private" })).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByText("You come straight back to this log afterwards.")).toBeTruthy();
    const href = screen.getByRole("link", { name: /Sign in with Warcraft Logs/ }).getAttribute("href") ?? "";
    expect(new URL(href).searchParams.get("next")).toBe("/?source=ABC123");
  });

  it("makes no promise about waiting less", () => {
    expect(SIGN_IN_PERKS.join(" ")).not.toMatch(/waiting/i);
  });
});
