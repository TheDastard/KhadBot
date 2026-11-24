import { ApiError } from "@khadbot/api-client";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const reporting = vi.hoisted(() => ({ reportError: vi.fn() }));
vi.mock("@/lib/error-reporting", () => reporting);

import ErrorPage from "@/app/error";

import { RouteError } from "./route-error";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RouteError", () => {
  it("shows the error state with a retry and reports the error", () => {
    const retry = vi.fn();
    const error = new Error("render failed");
    render(<ErrorPage error={error} retry={retry} />);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Something went wrong");
    expect(screen.queryByText(/Reference:/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledTimes(1);
    expect(reporting.reportError).toHaveBeenCalledWith(error);
  });

  it("shows the request reference for a 5xx API error", () => {
    const error = new ApiError({ status: 503, code: "wcl_unavailable", message: "Down.", requestId: "7f3a9c0e12345678" });
    render(<RouteError error={error} retry={() => undefined} />);
    expect(screen.getByRole("alert").textContent).toContain("Reference: 7f3a9c0e");
  });

  it("handles a thrown value that is not an Error", () => {
    render(<RouteError error="plain string" retry={() => undefined} />);
    expect(screen.getByRole("alert").textContent).toContain("Something went wrong");
    expect(reporting.reportError).toHaveBeenCalledWith("plain string");
  });
});
