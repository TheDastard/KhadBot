import { afterEach, describe, expect, it, vi } from "vitest";

import { nextPath, signInHref } from "./sign-in";

afterEach(() => vi.unstubAllEnvs());

describe("signInHref", () => {
  it("returns to the page the visitor was on", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "http://api.test");
    const url = new URL(signInHref("/?source=ABC123"));
    expect(url.origin + url.pathname).toBe("http://api.test/v1/auth/login");
    expect(url.searchParams.get("next")).toBe("/?source=ABC123");
  });

  it("never passes on a next that is not a path of this site", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "http://api.test");
    for (const bad of ["https://evil.example", "//evil.example", "evil"]) {
      expect(new URL(signInHref(bad)).searchParams.get("next")).toBe("/");
    }
  });
});

describe("nextPath", () => {
  it("joins the path and its query", () => {
    expect(nextPath("/r/ABC/p/3", "?ref=death:1")).toBe("/r/ABC/p/3?ref=death:1");
    expect(nextPath("/", "")).toBe("/");
    expect(nextPath("/", "?")).toBe("/");
    expect(nextPath("/", "source=ABC")).toBe("/?source=ABC");
  });
});
