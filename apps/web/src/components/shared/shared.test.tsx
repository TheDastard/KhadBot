import { ApiError } from "@khadbot/api-client";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import { AbilityName } from "./ability-name";
import { ErrorState } from "./error-state";
import { evidenceHref } from "./evidence-chip";
import { FightPct, FightPctBar } from "./fight-pct";
import { PlayerName } from "./player-name";
import { STATUSES, StatusBadge } from "./status-badge";

vi.mock("@/lib/queries", () => ({ usePlayers: () => ({ data: undefined }) }));

afterEach(cleanup);

describe("StatusBadge", () => {
  it("gives every status a word", () => {
    for (const [status, spec] of Object.entries(STATUSES)) {
      expect(spec.label.trim(), status).not.toBe("");
    }
  });

  it("renders the word, or what replaces it", () => {
    render(
      <>
        <StatusBadge status="missed" />
        <StatusBadge status="waiting">Waiting · 0:42</StatusBadge>
      </>,
    );
    expect(screen.getByText("Missed")).toBeTruthy();
    expect(screen.getByText("Waiting · 0:42").closest("[data-status]")?.getAttribute("data-status")).toBe("waiting");
  });
});

describe("FightPct", () => {
  it("shows the figure in its quality colour and describes it", () => {
    render(<FightPct pct={5.57} kill={false} />);
    const el = screen.getByLabelText("Wipe with 5.6% of the fight left");
    expect(el.textContent).toBe("5.6%");
    expect(el.getAttribute("data-quality")).toBe("legendary");
    expect(el.className).toContain("text-q-legendary-text");
  });

  it("says Kill in artifact gold", () => {
    render(<FightPct pct={0} kill />);
    const el = screen.getByLabelText("Kill");
    expect(el.textContent).toBe("Kill");
    expect(el.className).toContain("text-q-artifact-text");
  });

  it("fills its bar toward a kill", () => {
    const { container } = render(<FightPctBar pct={96.6} kill={false} />);
    const bar = screen.getByRole("img", { name: "Wipe with 96.6% of the fight left" });
    expect(bar.getAttribute("data-fill")).toBe("3.40");
    expect(parseFloat((bar.firstElementChild as HTMLElement).style.width)).toBeCloseTo(3.4);
    expect(container.querySelector(".bg-q-poor")).not.toBeNull();
  });

  it("can be decorative", () => {
    render(<FightPctBar pct={0} kill decorative />);
    expect(screen.queryByRole("img")).toBeNull();
  });
});

describe("PlayerName", () => {
  it("colours by class and leaves an unknown class plain", () => {
    render(
      <>
        <PlayerName name="Kouw" playerClass="Paladin" />
        <PlayerName name="Nobody" playerClass={null} />
      </>,
    );
    expect(screen.getByText("Kouw").className).toContain("text-cls-paladin");
    expect(screen.getByText("Nobody").className).not.toContain("text-cls-");
  });
});

describe("AbilityName", () => {
  it("links a spell to Wowhead with the fallback square until the script loads", () => {
    const { container } = render(
      <TooltipProvider>
        <AbilityName spellId={1234} name="Caustic Waves" />
      </TooltipProvider>,
    );
    const link = screen.getByRole("link", { name: "Caustic Waves" });
    expect(link.getAttribute("href")).toBe("https://www.wowhead.com/spell=1234");
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(container.querySelector("[data-icon-fallback]")).not.toBeNull();
  });

  it("is plain text without a spell ID", () => {
    render(<AbilityName spellId={null} name="Melee" />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("Melee")).toBeTruthy();
  });
});

describe("evidenceHref", () => {
  const REF = "ev:H7z1fqDXjRTFnbtg:15:death:2";

  it("opens another pull's page with only full kept", () => {
    expect(evidenceHref(REF, "/", new URLSearchParams("full=1&x=y"))).toBe(
      `/r/H7z1fqDXjRTFnbtg/p/15?full=1&ref=${encodeURIComponent(REF)}`,
    );
  });

  it("stays on the same pull, keeping its parameters and clearing the zoom", () => {
    expect(evidenceHref(REF, "/r/H7z1fqDXjRTFnbtg/p/15", new URLSearchParams("t=1-2&x=y"))).toBe(
      `/r/H7z1fqDXjRTFnbtg/p/15?x=y&ref=${encodeURIComponent(REF)}`,
    );
  });

  it("keeps a plan ref on the current page", () => {
    expect(evidenceHref("plan:p:1", "/r/A/p/1", new URLSearchParams())).toBe("/r/A/p/1?ref=plan%3Ap%3A1");
  });
});

describe("ErrorState", () => {
  it("says what happened, the code and the next step", () => {
    render(
      <ErrorState
        tone="warn"
        where="Loading"
        code="rate_limited"
        title="WarcraftLogs is busy"
        body="Everything loaded so far is kept."
        action={{ label: "Retry in 0:42", disabled: true }}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("Loading · rate_limited");
    expect(screen.getByRole("heading", { name: "WarcraftLogs is busy" })).toBeTruthy();
    expect((screen.getByRole("button", { name: "Retry in 0:42" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("uses status, not alert, for information", () => {
    render(<ErrorState tone="info" title="Nothing here" body="Search for a night." />);
    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("ErrorState reference", () => {
  const ID = "7f3a9c0e5b2d4e61";
  const failure = (status: number, requestId?: string) =>
    new ApiError({ status, code: "wcl_unavailable", message: "Down.", ...(requestId ? { requestId } : {}) });
  const show = (error: unknown) =>
    render(<ErrorState tone="bad" title="KhadBot can't connect to WarcraftLogs" body="Try again later." error={error} />);

  afterEach(() => vi.unstubAllGlobals());

  it("shows the first 8 characters for a 5xx with an ID", () => {
    show(failure(503, ID));
    expect(screen.getByRole("alert").textContent).toContain("Reference: 7f3a9c0e");
    expect(screen.getByRole("alert").textContent).not.toContain(ID);
    expect(screen.getByRole("button", { name: "Copy the full reference" })).toBeTruthy();
  });

  it("shows nothing for a 404, a 5xx without an ID or a call that got no answer", () => {
    for (const error of [failure(404, ID), failure(503), failure(0), new Error("x"), undefined]) {
      const { unmount } = show(error);
      expect(screen.queryByText(/Reference:/)).toBeNull();
      unmount();
    }
  });

  it("copies the full ID", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    show(failure(500, ID));
    fireEvent.click(screen.getByRole("button", { name: "Copy the full reference" }));
    expect(await screen.findByText("Copied")).toBeTruthy();
    expect(writeText).toHaveBeenCalledWith(ID);
  });

  it("shows the full ID when the clipboard refuses", async () => {
    vi.stubGlobal("navigator", { clipboard: { writeText: async () => Promise.reject(new Error("denied")) } });
    show(failure(502, ID));
    fireEvent.click(screen.getByRole("button", { name: "Copy the full reference" }));
    expect(await screen.findByText(ID)).toBeTruthy();
  });
});
