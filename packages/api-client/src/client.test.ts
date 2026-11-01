import { describe, expect, it } from "vitest";

import { API_UNREACHABLE, ApiError, apiUrl, createApiClient, CSRF_HEADER, parseRetryAfter, unwrap } from "./index";

function recorder(respond: (req: Request) => Response) {
  const seen: Request[] = [];
  const fetch = async (req: Request) => {
    seen.push(req);
    return respond(req);
  };
  return { seen, fetch };
}

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });

describe("createApiClient", () => {
  it("sends the cookie on every call and the CSRF header only on state-changing ones", async () => {
    const { seen, fetch } = recorder((req) =>
      req.method === "GET" ? json(200, { status: "ok", data_dir: "x" }) : new Response(null, { status: 204 }),
    );
    const api = createApiClient({ baseUrl: "http://api.test/", fetch });
    await unwrap(api.GET("/v1/health"));
    await unwrap(api.POST("/v1/auth/logout"));
    expect(seen.map((r) => r.url)).toEqual(["http://api.test/v1/health", "http://api.test/v1/auth/logout"]);
    expect(seen.map((r) => r.credentials)).toEqual(["include", "include"]);
    expect(seen[0]?.headers.get(CSRF_HEADER)).toBeNull();
    expect(seen[1]?.headers.get(CSRF_HEADER)).toBe("1");
  });

  it("omits the cookie when asked (a server without sign-in)", async () => {
    const { seen, fetch } = recorder(() => json(200, { status: "ok", data_dir: "x" }));
    const api = createApiClient({ baseUrl: "http://api.test", withCredentials: false, fetch });
    await unwrap(api.GET("/v1/health"));
    expect(seen[0]?.credentials).toBe("omit");
  });

  it("sends the CSRF header on DELETE", async () => {
    const { seen, fetch } = recorder(() => json(200, {}));
    const api = createApiClient({ baseUrl: "http://api.test", fetch });
    await unwrap(api.DELETE("/v1/reports/{code}/watch", { params: { path: { code: "ABC" } } }));
    expect(seen[0]?.headers.get(CSRF_HEADER)).toBe("1");
  });
});

describe("unwrap", () => {
  it("returns the data of a success", async () => {
    const { fetch } = recorder(() => json(200, { status: "ok", data_dir: "d", mode: "local", features: {} }));
    const api = createApiClient({ baseUrl: "http://api.test", fetch });
    const health = await unwrap(api.GET("/v1/health"));
    expect(health.data_dir).toBe("d");
  });

  it("maps the API's error body to an ApiError", async () => {
    const { fetch } = recorder(() => json(404, { code: "report_not_found", message: "No such report." }));
    const api = createApiClient({ baseUrl: "http://api.test", fetch });
    const err = await unwrap(api.GET("/v1/reports/{code}", { params: { path: { code: "X" } } })).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 404, code: "report_not_found", message: "No such report.", retryAfterS: null });
  });

  it("takes the budget wait from wait_s over Retry-After", async () => {
    const { fetch } = recorder(() =>
      json(429, { code: "budget_exhausted", message: "Wait.", wait_s: 42.5 }, { "Retry-After": "43" }),
    );
    const api = createApiClient({ baseUrl: "http://api.test", fetch });
    const err = await unwrap(api.GET("/v1/lookup/report", { params: { query: { source: "X" } } })).catch((e) => e);
    expect(err).toMatchObject({ status: 429, code: "budget_exhausted", retryAfterS: 42.5 });
  });

  it("falls back to Retry-After when the body has no wait", async () => {
    const { fetch } = recorder(() => json(503, { code: "wcl_unavailable", message: "Down." }, { "Retry-After": "30" }));
    const api = createApiClient({ baseUrl: "http://api.test", fetch });
    const err = await unwrap(api.GET("/v1/me")).catch((e) => e);
    expect(err).toMatchObject({ code: "wcl_unavailable", retryAfterS: 30 });
  });

  it("names a body that isn't the API's shape", async () => {
    const { fetch } = recorder(() => new Response("<html>Bad gateway</html>", { status: 502 }));
    const api = createApiClient({ baseUrl: "http://api.test", fetch });
    const err = await unwrap(api.GET("/v1/me")).catch((e) => e);
    expect(err).toMatchObject({ status: 502, code: "unexpected_response" });
  });

  it("reports a call that got no answer as api_unreachable", async () => {
    const api = createApiClient({
      baseUrl: "http://api.test",
      fetch: async () => {
        throw new TypeError("Failed to fetch");
      },
    });
    const err = await unwrap(api.GET("/v1/health")).catch((e) => e);
    expect(err).toMatchObject({ status: 0, code: API_UNREACHABLE });
  });
});

describe("helpers", () => {
  it("parses Retry-After as seconds or a date", () => {
    expect(parseRetryAfter(null)).toBeNull();
    expect(parseRetryAfter("12")).toBe(12);
    expect(parseRetryAfter("soon")).toBeNull();
    const now = Date.parse("2026-11-01T12:00:00Z");
    expect(parseRetryAfter("Sun, 01 Nov 2026 12:00:30 GMT", now)).toBe(30);
    expect(parseRetryAfter("Sun, 01 Nov 2026 11:00:00 GMT", now)).toBe(0);
  });

  it("builds absolute URLs with a query", () => {
    expect(apiUrl("http://api.test/", "/v1/auth/login", { next: "/r/A/p/1?ref=x", skip: undefined })).toBe(
      "http://api.test/v1/auth/login?next=%2Fr%2FA%2Fp%2F1%3Fref%3Dx",
    );
  });
});
