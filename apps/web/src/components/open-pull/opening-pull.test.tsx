import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OpeningPull, tileHeading, tileMonoLine, type OpeningPullProps, type PullTileInfo } from "./opening-pull";
import { stepsOf, type Phase } from "./job-events";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/r/ABC/p/12",
  useSearchParams: () => new URLSearchParams(""),
}));

afterEach(cleanup);

const TILE: PullTileInfo = {
  encounterId: 3000,
  encounter: "Ula'tek",
  difficulty: "Heroic",
  label: "P3",
  pct: 24.1,
  kill: false,
  durationMs: 481_000,
};

function show(phase: Phase, over: Partial<OpeningPullProps> = {}) {
  return render(
    <OpeningPull
      code="H7z1fqDXjRTFnbtg"
      fight={12}
      tile={TILE}
      steps={stepsOf(null)}
      phase={phase}
      countdownS={null}
      onStartAgain={() => undefined}
      signInHref="http://api/v1/auth/login"
      {...over}
    />,
  );
}

describe("the tile and heading", () => {
  it("words the heading and the mono line as the canvas does", () => {
    expect(tileHeading(12, TILE)).toBe("Ula'tek · Heroic · P3");
    expect(tileMonoLine("H7z1fqDXjRTFnbtg", 12, TILE)).toBe("H7z1fqDXjRTFnbtg · fight 12 · wipe at 24.1% · 8:01");
    expect(tileMonoLine("H7z1fqDXjRTFnbtg", 3, { ...TILE, kill: true, pct: 0 })).toContain("fight 3 · kill · 8:01");
  });

  it("falls back to the fight number with nothing known", () => {
    expect(tileHeading(12, null)).toBe("Fight 12");
    expect(tileMonoLine("ABC", 12, null)).toBe("ABC · fight 12");
  });
});

describe("states", () => {
  it("loading shows the status line, three steps each with a state word, and the way back", () => {
    show({ kind: "loading" });
    expect(screen.getByRole("status").textContent).toContain("Loading this pull from WarcraftLogs");
    const steps = screen.getAllByRole("listitem");
    expect(steps).toHaveLength(3);
    expect(within(steps[0]!).getByText("Download the fight")).toBeTruthy();
    expect(within(steps[0]!).getByText("Waiting")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Back to the search" }).getAttribute("href")).toBe("/");
    expect(screen.getByText(/it keeps loading/)).toBeTruthy();
  });

  it("waiting for points says so with the countdown and keeps the steps waiting", () => {
    show({ kind: "waiting", etaS: 42 }, { countdownS: 42 });
    expect(screen.getByRole("status").textContent).toBe("Waiting for Warcraft Logs points. This pull starts in 0:42.");
    expect(screen.getAllByText("Waiting")).toHaveLength(3);
  });

  it("busy shows the retry countdown on a disabled button, then an enabled one", () => {
    const { unmount } = show({ kind: "busy", retryAfterS: 42 }, { countdownS: 42 });
    expect(screen.getByText("WarcraftLogs is busy")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Retry in 0:42" }) as HTMLButtonElement).disabled).toBe(true);
    unmount();
    show({ kind: "busy", retryAfterS: 42 }, { countdownS: 0 });
    expect((screen.getByRole("button", { name: "Retry" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("a failed step shows its message and Start again", () => {
    const onStartAgain = vi.fn();
    show({ kind: "failed", code: "pulls_failed", message: "Events were not in order", step: "prepare" }, { onStartAgain });
    expect(screen.getByRole("alert").textContent).toContain("Events were not in order");
    screen.getByRole("button", { name: "Start again" }).click();
    expect(onStartAgain).toHaveBeenCalledOnce();
  });

  it("a job the server can't find says it stopped loading", () => {
    show({ kind: "stopped" });
    expect(screen.getByText("This stopped loading")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Start again" })).toBeTruthy();
    expect(screen.queryByText(/it keeps loading/)).toBeNull();
  });

  it("an API that doesn't answer says so", () => {
    show({ kind: "failed", code: "api_unreachable", message: "x", step: null });
    expect(screen.getByText("KhadBot isn’t responding")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("a signed-out visitor is offered sign-in", () => {
    show({ kind: "failed", code: "not_signed_in", message: "x", step: null });
    expect(screen.getByRole("link", { name: "Sign in with Warcraft Logs" }).getAttribute("href")).toBe(
      "http://api/v1/auth/login",
    );
  });
});

describe("a log the visitor can't read", () => {
  const notFound: Phase = { kind: "failed", code: "report_not_found", message: "x", step: null };

  it("signed out, says the log is private and offers sign-in back to this pull", () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE", "http://api.test");
    show(notFound, { signedOut: true });
    expect(screen.getByRole("heading", { name: "This log is private" })).toBeTruthy();
    expect(screen.queryByText("Report not found or not shared with your Warcraft Logs account")).toBeNull();
    const href = screen.getByRole("link", { name: /Sign in with Warcraft Logs/ }).getAttribute("href") ?? "";
    expect(new URL(href).searchParams.get("next")).toBe("/r/ABC/p/12");
    vi.unstubAllEnvs();
  });

  it("signed in, says the report was not found", () => {
    show(notFound, { signedOut: false });
    expect(screen.getByText("Report not found or not shared with your Warcraft Logs account")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "This log is private" })).toBeNull();
  });
});

describe("boss art on the tile", () => {
  const SRC = "https://render.worldofwarcraft.com/us/icons/56/1.jpg";

  it("fills the tile with the thumbnail and shows the attribution line", () => {
    show({ kind: "loading" }, { thumbnailSrc: SRC, attribution: "Boss art is the property of Blizzard Entertainment, Inc." });
    const tile = screen.getByTestId("pull-tile");
    expect(within(tile).getByTestId("boss-thumbnail").getAttribute("data-art")).toBe("image");
    expect(screen.getByTestId("art-attribution").textContent).toContain("Blizzard");
  });

  it("keeps the plain tile, the label and no attribution without art", () => {
    show({ kind: "loading" });
    const tile = screen.getByTestId("pull-tile");
    expect(within(tile).getByTestId("boss-thumbnail").getAttribute("data-art")).toBe("fallback");
    expect(within(tile).getByText("P3")).toBeTruthy();
    expect(screen.queryByTestId("art-attribution")).toBeNull();
  });
});
