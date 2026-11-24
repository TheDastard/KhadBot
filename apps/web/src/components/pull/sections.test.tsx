import { act, cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import type { PullDigestData } from "@/lib/queries";

import { buildNames } from "./names";
import type { EncounterNamesData } from "./queries";
import { DeathsSection, MechanicsSection } from "./sections";

vi.mock("next/navigation", () => ({
  usePathname: () => "/r/A/p/1",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/queries", () => ({ usePlayers: () => ({ data: undefined }) }));

afterEach(cleanup);

const DESCRIPTION = "Venom waves roll out and hurt everyone in their path.";
const names = buildNames(
  {
    encounter_id: 1,
    difficulty: 4,
    difficulty_name: "heroic",
    encounter_name: "Boss",
    phases: {},
    mechanics: { waves: "Caustic Waves" },
    mechanic_descriptions: { waves: DESCRIPTION },
    mechanic_verified: { waves: false },
    mechanic_info: {
      waves: {
        ability_ids: [1301100],
        icon_ability_id: 1301100,
        kind: "avoidable",
        severity: "medium",
        failure_summary: "Every hit is a failure, charged to the player hit.",
      },
    },
    damage_windows: {},
  } as EncounterNamesData,
  undefined,
);

const digest = {
  deaths: [
    {
      ref: "ev:A:1:death:1",
      t_ms: 1000,
      name: "Fay",
      after_cutoff: false,
      killing_ability_id: 1301100,
      killing_ability_name: "Caustic Waves",
      killer_name: "Boss",
      mechanic_ids: ["waves"],
    },
    {
      ref: "ev:A:1:death:2",
      t_ms: 2000,
      name: "Gus",
      after_cutoff: false,
      killing_ability_id: 1,
      killing_ability_name: "Melee",
      killer_name: "Boss",
      mechanic_ids: [],
    },
  ],
  mechanics: [
    { mechanic_id: "waves", ref: "ev:A:1:mech:waves", hits: 3, failures: 2, damage_taken: 10, failures_by_player: { Fay: 2 } },
  ],
} as unknown as PullDigestData;

const tip = () => document.querySelector("[data-slot=tooltip-content]");
const props = { code: "A", fight: 1, digest, names };

describe("MechanicsSection", () => {
  it("gives the mechanic's name and its chip KhadBot's tooltip, never a Wowhead link", () => {
    render(
      <TooltipProvider>
        <MechanicsSection {...props} />
      </TooltipProvider>,
    );
    const table = screen.getByTestId("section-mechanics");
    expect(within(table).queryAllByRole("link").filter((a) => a.getAttribute("href")?.includes("wowhead"))).toEqual([]);
    act(() => within(table).getByText("Caustic Waves").focus());
    expect(tip()?.textContent).toContain(DESCRIPTION);
    expect(within(table).getByText("Unverified")).toBeTruthy();
    cleanup();
    render(
      <TooltipProvider>
        <MechanicsSection {...props} />
      </TooltipProvider>,
    );
    const chip = screen.getByRole("link", { name: /^mech: open evidence ev:A:1:mech:waves$/ });
    act(() => chip.focus());
    expect(tip()?.textContent).toContain("Every hit is a failure");
  });
});

describe("DeathsSection", () => {
  it("puts the mechanic's chip beside a killing blow that belongs to one, and only there", () => {
    render(
      <TooltipProvider>
        <DeathsSection {...props} total={2} before={2} full={false} />
      </TooltipProvider>,
    );
    const rows = within(screen.getByTestId("section-deaths")).getAllByRole("row").slice(1);
    const spell = within(rows[0]!).getByRole("link", { name: "Caustic Waves" });
    expect(spell.getAttribute("href")).toBe("https://www.wowhead.com/spell=1301100");
    expect(within(rows[0]!).getByRole("link", { name: /^Caustic Waves: open evidence ev:A:1:mech:waves$/ })).toBeTruthy();
    expect(within(rows[1]!).queryByRole("link", { name: /open evidence ev:A:1:mech/ })).toBeNull();
  });
});
