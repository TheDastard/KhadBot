import type { ErrorEvent } from "@sentry/nextjs";
import { afterEach, describe, expect, it, vi } from "vitest";

const sdk = vi.hoisted(() => ({ init: vi.fn(), captureException: vi.fn() }));
vi.mock("@sentry/nextjs", () => sdk);

import {
  initErrorReporting,
  reportError,
  resetErrorReportingForTests,
  scrubBreadcrumb,
  scrubEvent,
  stripQuery,
} from "./error-reporting";

afterEach(() => {
  resetErrorReportingForTests();
  vi.clearAllMocks();
  vi.unstubAllEnvs();
});

describe("scrubbing", () => {
  it("cuts query strings and fragments", () => {
    expect(stripQuery("https://app.example/r/abc/p/3?ref=x&t=1-2#top")).toBe("https://app.example/r/abc/p/3");
    expect(stripQuery("/v1/health")).toBe("/v1/health");
  });

  it("drops request cookies, headers, body and query string, and strips breadcrumb URLs", () => {
    const event = {
      type: undefined,
      request: {
        url: "https://app.example/auth/callback?code=secret-code&state=s1",
        query_string: "code=secret-code&state=s1",
        cookies: { khadbot_session: "secret-cookie" },
        headers: { Cookie: "khadbot_session=secret-cookie", "User-Agent": "test" },
        data: { password: "secret-body" },
      },
      breadcrumbs: [
        { category: "fetch", data: { url: "https://api.example/v1/reports?code=secret-code", method: "GET" } },
        { category: "navigation", data: { from: "/r/a?ref=secret-ref", to: "/r/b?ref=secret-ref" } },
        { category: "console", message: "plain" },
      ],
    } as unknown as ErrorEvent;
    const sent = scrubEvent(event);
    expect(sent.request).toEqual({ url: "https://app.example/auth/callback" });
    expect(JSON.stringify(sent)).not.toMatch(/secret/);
    expect(sent.breadcrumbs?.[0]?.data).toEqual({ url: "https://api.example/v1/reports", method: "GET" });
    expect(sent.breadcrumbs?.[2]).toEqual({ category: "console", message: "plain" });
  });

  it("leaves a breadcrumb without data alone", () => {
    const crumb = { category: "ui.click", message: "button" };
    expect(scrubBreadcrumb(crumb)).toBe(crumb);
  });
});

describe("starting and reporting", () => {
  it("does nothing and loads no SDK without a DSN", async () => {
    expect(initErrorReporting(undefined)).toBe(false);
    reportError(new Error("boom"), undefined);
    await vi.dynamicImportSettled();
    expect(sdk.init).not.toHaveBeenCalled();
    expect(sdk.captureException).not.toHaveBeenCalled();
  });

  it("starts once with nothing personal collected and the scrubbers, then reports", async () => {
    const dsn = "https://public@errors.example.invalid/1";
    expect(initErrorReporting(dsn)).toBe(true);
    const error = new Error("boom");
    reportError(error, dsn);
    await vi.waitFor(() => expect(sdk.captureException).toHaveBeenCalledWith(error));
    expect(sdk.init).toHaveBeenCalledTimes(1);
    const options = sdk.init.mock.calls[0]?.[0];
    expect(options).toMatchObject({
      dsn,
      dataCollection: { userInfo: false, cookies: false, httpHeaders: false, httpBodies: [], urlQueryParams: false },
      beforeSend: scrubEvent,
      beforeBreadcrumb: scrubBreadcrumb,
    });
  });

  it("tags events with the build's environment and release", async () => {
    vi.stubEnv("NEXT_PUBLIC_KHADBOT_ENV", "staging");
    vi.stubEnv("NEXT_PUBLIC_KHADBOT_VERSION", "abc1234");
    expect(initErrorReporting("https://public@errors.example.invalid/1")).toBe(true);
    await vi.waitFor(() => expect(sdk.init).toHaveBeenCalledTimes(1));
    expect(sdk.init.mock.calls[0]?.[0]).toMatchObject({ environment: "staging", release: "abc1234" });
  });

  it('calls a build without them "unset" with no release', async () => {
    vi.stubEnv("NEXT_PUBLIC_KHADBOT_ENV", "");
    vi.stubEnv("NEXT_PUBLIC_KHADBOT_VERSION", "");
    expect(initErrorReporting("https://public@errors.example.invalid/1")).toBe(true);
    await vi.waitFor(() => expect(sdk.init).toHaveBeenCalledTimes(1));
    const options = sdk.init.mock.calls[0]?.[0];
    expect(options?.environment).toBe("unset");
    expect(options).toHaveProperty("release", undefined);
  });
});
