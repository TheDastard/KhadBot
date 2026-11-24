import { act, cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { buildNames } from "@/components/pull/names";
import type { EncounterNamesData } from "@/components/pull/queries";
import { TooltipProvider } from "@/components/ui/tooltip";

import { DeathView, MechanicView, type RecordOf } from "./views";

vi.mock("next/navigation", () => ({
  usePathname: () => "/r/A/p/1",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/queries", () => ({ usePlayers: () => ({ data: undefined }) }));
vi.mock("@/components/pull/queries", () => ({
  useLazyDigest: () => ({
    data: {
      deaths: [
        { ref: "ev:A:1:death:3", name: "Fay", phase_id: "p1", mechanic_ids: ["rage"], t_ms: 75_600 },
        { ref: "ev:A:1:death:4", name: "Gus", phase_id: null, mechanic_ids: ["waves"], t_ms: 90_000 },
      ],
    },
  }),
  useReportAbilities: () => ({ data: { code: "A", abilities: [{ id: 1301007, name: "Unchecked Rage", icon: null }] } }),
}));

afterEach(cleanup);

const ENCOUNTER = {
  encounter_id: 1,
  difficulty: 4,
  difficulty_name: "heroic",
  encounter_name: "Boss",
  phases: { p1: { name: "Stage One: Start", short_name: "Stage One" } },
  mechanics: { rage: "Unchecked Rage", waves: "Caustic Waves" },
  mechanic_descriptions: { rage: "The boss hits the raid when nobody is in melee." },
  mechanic_verified: { rage: false, waves: true },
  mechanic_info: {
    rage: {
      ability_ids: [1301007, 1301009],
      icon_ability_id: 1301007,
      kind: "avoidable",
      severity: "high",
      failure_summary: "Every occurrence is a failure, charged to whoever last held the NPC's aggro.",
    },
    waves: { ability_ids: [1301100], icon_ability_id: 1301100, kind: "avoidable", severity: "medium", failure_summary: null },
  },
  damage_windows: {},
} as EncounterNamesData;
const names = buildNames(ENCOUNTER, undefined);

const MECH: RecordOf<"mech"> = {
  mechanic_id: "rage",
  ref: "ev:A:1:mech:rage",
  hits: 40,
  failures: 2,
  clusters: 2,
  damage_taken: 4_812_330,
  deaths_in_window: 1,
  failures_by_player: { Ana: 1, Bob: 1 },
  cluster_rows: [
    {
      cluster: 1,
      t_ms: 72_400,
      end_ms: 72_900,
      hits: 20,
      failures: 1,
      damage: 1,
      players: 20,
      phase_id: "p1",
      failed_players: [],
      blamed: "Ana",
      blame_state: "beyond_reach",
      blame_dist_yd: 26.8,
      blame_reach_yd: 21,
    },
    { cluster: 2, t_ms: 242_100, end_ms: 242_400, hits: 20, failures: 1, damage: 1, players: 20, phase_id: "p1", failed_players: [], blamed: "Bob", blame_state: "dead" },
  ],
};

function renderIn(node: React.ReactNode) {
  return render(<TooltipProvider>{node}</TooltipProvider>);
}

describe("MechanicView", () => {
  it("lists the mechanic's abilities as Wowhead links with their IDs", () => {
    renderIn(<MechanicView record={MECH} evidenceRef={MECH.ref} code="A" fight={1} names={names} />);
    const list = screen.getByTestId("mechanic-abilities");
    const links = within(list).getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual([
      "https://www.wowhead.com/spell=1301007",
      "https://www.wowhead.com/spell=1301009",
    ]);
    expect(links[0]!.textContent).toBe("Unchecked Rage");
    expect(links[1]!.textContent).toBe("Spell 1301009");
    expect(list.textContent).toContain("1301009");
  });

  it("says what it is, how failures count, and draws the blame context", () => {
    renderIn(<MechanicView record={MECH} evidenceRef={MECH.ref} code="A" fight={1} names={names} />);
    expect(screen.getByText("The boss hits the raid when nobody is in melee.")).toBeTruthy();
    expect(screen.getByTestId("mechanic-rule").textContent).toContain("last held the NPC's aggro");
    expect(screen.getByText("Avoidable")).toBeTruthy();
    expect(screen.getByText("Severity high")).toBeTruthy();
    expect(screen.getByText(/Mechanic not yet verified/)).toBeTruthy();
    const clusters = screen.getByRole("region", { name: "Clusters" });
    expect(within(clusters).getByText("Charged to")).toBeTruthy();
    expect(within(clusters).getByText("Beyond reach")).toBeTruthy();
    expect(within(clusters).getByText("26.8 yd of 21")).toBeTruthy();
    expect(within(clusters).getByText("Dead")).toBeTruthy();
    expect(within(screen.getByRole("list", { name: "Failures by player" })).getAllByRole("listitem")).toHaveLength(2);
  });

  it("chips the deaths in its window, from the digest", () => {
    renderIn(<MechanicView record={MECH} evidenceRef={MECH.ref} code="A" fight={1} names={names} />);
    const deaths = within(screen.getByTestId("mechanic-deaths")).getAllByRole("link");
    expect(deaths.map((d) => d.textContent)).toEqual(["Stage One · death 3 · Fay"]);
  });
});

describe("DeathView", () => {
  const DEATH: RecordOf<"death"> = {
    ref: "ev:A:1:death:3",
    actor_id: 7,
    name: "Fay",
    t_ms: 75_600,
    after_cutoff: false,
    phase_id: "p1",
    killing_ability_id: 1301009,
    killing_ability_name: "Unchecked Rage",
    killer_name: "Boss",
    overkill: null,
    time_to_die_ms: null,
    window_ms: 10_000,
    window_damage: 1,
    window_self_damage: 0,
    window_healing: null,
    active_defensives: [],
    mechanic_ids: [],
    top_hits: [],
  };

  it("keeps the killing blow a Wowhead link and puts its mechanic's chip beside it", () => {
    renderIn(<DeathView record={DEATH} evidenceRef={DEATH.ref} code="A" fight={1} names={names} />);
    const spell = screen.getByRole("link", { name: "Unchecked Rage" });
    expect(spell.getAttribute("href")).toBe("https://www.wowhead.com/spell=1301009");
    const chip = screen.getByRole("link", { name: /^Unchecked Rage: open evidence ev:A:1:mech:rage$/ });
    expect(chip.parentElement).toBe(spell.closest("span")!.parentElement);
    // The spell link has no KhadBot tooltip; the chip does.
    act(() => chip.focus());
    const tip = document.querySelector("[data-mechanic-tip=rage]");
    expect(tip?.textContent).toContain("The boss hits the raid when nobody is in melee.");
    expect(spell.getAttribute("aria-describedby") ?? "").not.toContain(tip?.closest("[id]")?.id ?? "-");
  });
});
