import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MechanicFacts } from "@/components/shared/mechanic-tip";
import { TooltipProvider } from "@/components/ui/tooltip";

import { buildTimeline } from "./markers";
import type { PullTimelineData } from "./queries";
import TimelineChart from "./timeline-chart";

vi.mock("@/lib/queries", () => ({ usePlayers: () => ({ data: undefined }) }));

afterEach(cleanup);

const R = "ev:T:1";
const data: PullTimelineData = {
  pull_key: "T:1",
  ref: `${R}:pull`,
  duration_ms: 100_000,
  cutoff_ms: null,
  phases: [],
  deaths: [
    { t_ms: 20_000, name: "Aaa", after_cutoff: false, ref: `${R}:death:1` },
    { t_ms: 50_000, name: "Bbb", after_cutoff: false, ref: `${R}:death:2` },
  ],
  mechanics: [
    { mechanic_id: "m", name: "Mech", cluster: 1, t_ms: 30_000, end_ms: 31_000, hits: 1, failures: 1, ref: `${R}:mech:m` },
  ],
  cooldowns: [{ t_ms: 40_000, spell_id: 1, spell: "Spell", caster: "C", category: "defensive", ref: `${R}:cd:1:1` }],
  windows: [],
};

function setup(selectedRef: string | null = null) {
  const onSelect = vi.fn();
  const onZoomAround = vi.fn();
  render(
    <TooltipProvider>
      <TimelineChart
        code="T"
        model={buildTimeline(data)}
        durationMs={data.duration_ms}
        cutoffMs={null}
        phases={[]}
        selectedRef={selectedRef}
        zoom={null}
        onSelect={onSelect}
        onZoom={vi.fn()}
        onZoomAround={onZoomAround}
      />
    </TooltipProvider>,
  );
  return { onSelect, onZoomAround, group: screen.getByRole("group", { name: "Timeline markers" }) };
}

const markers = () => screen.getAllByRole("button", { pressed: false });

describe("TimelineChart keyboard", () => {
  it("is one tab stop", () => {
    setup();
    const stops = markers().filter((b) => b.tabIndex === 0);
    expect(stops).toHaveLength(1);
    expect(stops[0]!.getAttribute("aria-label")).toMatch(/^Death 1/);
  });

  it("steps through markers in time order across lanes", () => {
    const { group } = setup();
    const first = markers().find((b) => b.tabIndex === 0)!;
    act(() => first.focus());
    const seen: string[] = [first.getAttribute("aria-label")!];
    for (let i = 0; i < 3; i++) {
      fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
      seen.push(document.activeElement!.getAttribute("aria-label")!);
    }
    expect(seen.map((s) => s.split(",")[0])).toEqual(["Death 1", "Mech", "Spell", "Death 2"]);
    fireEvent.keyDown(group, { key: "ArrowLeft" });
    expect(document.activeElement!.getAttribute("aria-label")).toMatch(/^Spell/);
  });

  it("selects on click, clears on Escape and zooms on Z", () => {
    const { onSelect, onZoomAround } = setup();
    const death2 = screen.getByRole("button", { name: /^Death 2/ });
    fireEvent.click(death2);
    expect(onSelect).toHaveBeenLastCalledWith(`${R}:death:2`);
    act(() => death2.focus());
    fireEvent.keyDown(death2, { key: "z" });
    expect(onZoomAround).toHaveBeenCalledWith(50_000);
    fireEvent.keyDown(death2, { key: "Escape" });
    expect(onSelect).toHaveBeenLastCalledWith(null);
  });

  it("marks the selection apart from focus", () => {
    setup(`${R}:death:2`);
    expect(screen.getByRole("button", { name: /^Death 2/ }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: /^Death 2/ }).tabIndex).toBe(0);
  });

  it("mirrors the markers in a list in the same order, once it is open", () => {
    setup();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
    const details = screen.getByText(/^Markers as a list/).closest("details")!;
    act(() => {
      details.open = true;
      details.dispatchEvent(new Event("toggle"));
    });
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items[0]).toContain("Death 1");
    expect(items).toHaveLength(4);
  });
});

describe("TimelineChart mechanic tooltips", () => {
  const facts = (id: string): MechanicFacts => ({
    id,
    name: "Mech",
    description: "A mechanic the file describes.",
    failureSummary: "Every hit is a failure, charged to the player hit.",
    abilityIds: [1301007],
    iconAbilityId: 1301007,
    kind: "avoidable",
    severity: "high",
  });

  function setupWith() {
    render(
      <TooltipProvider>
        <TimelineChart
          code="T"
          model={buildTimeline(data)}
          durationMs={data.duration_ms}
          cutoffMs={null}
          phases={[{ id: "p1", name: "Stage One", start_ms: 0, end_ms: 100_000 }]}
          selectedRef={null}
          zoom={null}
          onSelect={vi.fn()}
          onZoom={vi.fn()}
          onZoomAround={vi.fn()}
          mechanics={facts}
        />
      </TooltipProvider>,
    );
  }

  it("gives the lane label KhadBot's compact tooltip on focus", () => {
    setupWith();
    const label = document.querySelector("[data-lane-label=m]")!;
    const name = label.querySelector<HTMLElement>("[tabindex='0']")!;
    expect(name.textContent).toBe("Mech");
    expect(label.querySelector("[data-icon-ability-id='1301007']")).not.toBeNull();
    act(() => name.focus());
    const tip = document.querySelector("[data-slot=tooltip-content] [data-mechanic-tip=m]");
    expect(tip?.textContent).toContain("Every hit is a failure");
    expect(tip?.querySelector("a")).toBeNull();
  });

  it("shows the mechanic's card on a marker, with the occurrence and no links", () => {
    setupWith();
    const marker = screen.getByRole("button", { name: /^Mech,/ });
    act(() => marker.focus());
    const card = document.getElementById("timeline-marker-tip")!;
    expect(card.className).toContain("bg-s1");
    expect(card.textContent).toContain("A mechanic the file describes.");
    expect(card.textContent).toContain("Severity high");
    expect(card.textContent).toContain("0:30.0 – 0:31.0 · Stage One");
    expect(card.querySelector("a")).toBeNull();
  });

  it("keeps the compact marker tooltip without facts", () => {
    setup();
    const marker = screen.getByRole("button", { name: /^Mech,/ });
    act(() => marker.focus());
    const tip = document.getElementById("timeline-marker-tip")!;
    expect(tip.className).toContain("bg-ink");
    expect(tip.textContent).toContain("1 hit");
  });
});
