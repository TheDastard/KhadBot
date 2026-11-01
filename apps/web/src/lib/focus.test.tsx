import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { focusHref, focusSearch, parseFocus, parseWindow, useFocus, windowAround } from "./focus";

const nav = vi.hoisted(() => ({
  search: "",
  pathname: "/r/H7z1fqDXjRTFnbtg/p/15",
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(nav.search),
  usePathname: () => nav.pathname,
  useRouter: () => ({ push: nav.push }),
}));

const REF = "ev:H7z1fqDXjRTFnbtg:15:death:2";

describe("parseWindow", () => {
  it("reads start-end in whole milliseconds", () => {
    expect(parseWindow("420341-430341")).toEqual({ startMs: 420341, endMs: 430341 });
  });

  it.each([null, "", "430-420", "5-5", "a-b", "1.5-3", "-1-3", "10"])("rejects %j", (text) => {
    expect(parseWindow(text)).toBeNull();
  });
});

describe("windowAround", () => {
  it("centres a 10 s window and never starts before zero", () => {
    expect(windowAround(425_341)).toEqual({ startMs: 420_341, endMs: 430_341 });
    expect(windowAround(2_000)).toEqual({ startMs: 0, endMs: 10_000 });
  });
});

describe("parseFocus and focusSearch", () => {
  it("reads ref, t and full", () => {
    expect(parseFocus(new URLSearchParams(`ref=${REF}&t=1-2&full=1&x=y`))).toEqual({
      ref: REF,
      t: { startMs: 1, endMs: 2 },
      full: true,
    });
    expect(parseFocus(new URLSearchParams("full=true&t=bad&ref="))).toEqual({ ref: null, t: null, full: false });
  });

  it("changes only what it is given and keeps other parameters", () => {
    const current = new URLSearchParams("x=y&full=1");
    expect(focusSearch(current, { ref: REF })).toBe(`x=y&full=1&ref=${encodeURIComponent(REF)}`);
    expect(focusSearch(current, { full: false })).toBe("x=y");
    expect(focusSearch(new URLSearchParams("t=1-2&ref=a"), { t: null, ref: null })).toBe("");
    expect(focusHref("/p", new URLSearchParams(), { t: { startMs: 3, endMs: 9 } })).toBe("/p?t=3-9");
    expect(focusHref("/p", new URLSearchParams("t=1-2"), { t: null })).toBe("/p");
  });
});

describe("useFocus", () => {
  beforeEach(() => {
    nav.search = "";
    nav.push.mockReset();
  });

  it("reads the URL", () => {
    nav.search = `ref=${encodeURIComponent(REF)}&full=1`;
    const { result } = renderHook(() => useFocus());
    expect(result.current.ref).toBe(REF);
    expect(result.current.parsedRef).toEqual({ kind: "death", report: "H7z1fqDXjRTFnbtg", fight: 15, n: 2 });
    expect(result.current.full).toBe(true);
    expect(result.current.t).toBeNull();
  });

  it("pushes each change to history without scrolling", () => {
    nav.search = "full=1";
    const { result } = renderHook(() => useFocus());
    act(() => result.current.setRef(REF));
    expect(nav.push).toHaveBeenLastCalledWith(
      `/r/H7z1fqDXjRTFnbtg/p/15?full=1&ref=${encodeURIComponent(REF)}`,
      { scroll: false },
    );
    act(() => result.current.setWindow(windowAround(425_341)));
    expect(nav.push).toHaveBeenLastCalledWith("/r/H7z1fqDXjRTFnbtg/p/15?full=1&t=420341-430341", { scroll: false });
    act(() => result.current.setFull(false));
    expect(nav.push).toHaveBeenLastCalledWith("/r/H7z1fqDXjRTFnbtg/p/15", { scroll: false });
  });

  it("gives an unknown ref no parsed form", () => {
    nav.search = "ref=garbage";
    const { result } = renderHook(() => useFocus());
    expect(result.current.ref).toBe("garbage");
    expect(result.current.parsedRef).toBeNull();
  });
});
