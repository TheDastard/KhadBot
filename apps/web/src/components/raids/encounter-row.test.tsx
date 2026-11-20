import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EncounterRow } from "./encounter-row";
import type { NightGroup } from "./night";

vi.mock("./pull-chip", () => ({ PullChip: () => <span data-testid="chip" /> }));

afterEach(cleanup);

const GROUP: NightGroup = {
  encounterId: 3492,
  name: "Ula'tek",
  difficulty: "Heroic",
  pulls: [{ fight: 3, ordinal: 1, kill: true, bossPct: 0, durationMs: 300_000 }],
  bestFight: 3,
};

describe("EncounterRow portrait", () => {
  it("draws the 48 by 24 portrait beside the name when it has a URL", () => {
    render(<EncounterRow code="ABC" group={GROUP} portraitSrc="http://api/v1/art/portraits/Ulatek.png?v=1" />);
    const img = screen.getByTestId("boss-portrait");
    expect(img.getAttribute("width")).toBe("48");
    expect(img.getAttribute("height")).toBe("24");
    expect(screen.getByText("Ula'tek")).toBeTruthy();
  });

  it("shows the name alone with no URL or after an error", () => {
    const { rerender } = render(<EncounterRow code="ABC" group={GROUP} />);
    expect(screen.queryByTestId("boss-portrait")).toBeNull();
    rerender(<EncounterRow code="ABC" group={GROUP} portraitSrc="http://api/p.png" />);
    fireEvent.error(screen.getByTestId("boss-portrait"));
    expect(screen.queryByTestId("boss-portrait")).toBeNull();
    expect(screen.getByText("Ula'tek")).toBeTruthy();
  });
});
