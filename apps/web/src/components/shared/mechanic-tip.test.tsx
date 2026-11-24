import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";

import { EvidenceChip } from "./evidence-chip";
import { MechanicIcon, MechanicName, MechanicTooltip, type MechanicFacts } from "./mechanic-tip";

vi.mock("next/navigation", () => ({
  usePathname: () => "/r/A/p/1",
  useSearchParams: () => new URLSearchParams(),
}));

afterEach(cleanup);

const FACTS: MechanicFacts = {
  id: "unchecked_rage",
  name: "Unchecked Rage",
  description: "The boss hits the whole raid when nobody is in melee.",
  failureSummary: "Every occurrence is a failure, charged to whoever last held the NPC's aggro.",
  abilityIds: [1301007, 1301008],
  iconAbilityId: 1301008,
  kind: "avoidable",
  severity: "high",
};

const tip = () => document.querySelector("[data-slot=tooltip-content]");

describe("MechanicName", () => {
  it("opens KhadBot's description tooltip on keyboard focus", () => {
    render(
      <TooltipProvider>
        <MechanicName facts={FACTS} />
      </TooltipProvider>,
    );
    const name = screen.getByText("Unchecked Rage");
    expect(name.tabIndex).toBe(0);
    expect(tip()).toBeNull();
    act(() => name.focus());
    expect(tip()?.textContent).toContain(FACTS.description);
    expect(tip()?.textContent).toContain(FACTS.failureSummary);
    // Never a link, and the tooltip holds none.
    expect(screen.queryByRole("link")).toBeNull();
    expect(tip()?.querySelector("a")).toBeNull();
  });

  it("is plain text when there is nothing more to say than the name", () => {
    render(
      <TooltipProvider>
        <MechanicName facts={{ ...FACTS, description: null, failureSummary: null }} />
      </TooltipProvider>,
    );
    const name = screen.getByText("Unchecked Rage");
    expect(name.hasAttribute("tabindex")).toBe(false);
    act(() => name.focus());
    expect(tip()).toBeNull();
  });
});

describe("MechanicIcon", () => {
  it("is a plain image of the icon ability, not a link", () => {
    const { container } = render(<MechanicIcon facts={FACTS} />);
    expect(container.querySelector("[data-icon-ability-id]")?.getAttribute("data-icon-ability-id")).toBe("1301008");
    expect(container.querySelector("[data-icon-fallback]")).not.toBeNull();
    expect(container.querySelector("a")).toBeNull();
  });
});

describe("a chip as the tooltip's trigger", () => {
  it("keeps the chip a link to the evidence and describes it with the tooltip", () => {
    render(
      <TooltipProvider>
        <MechanicTooltip facts={FACTS}>
          <EvidenceChip evidenceRef="ev:A:1:mech:unchecked_rage" label="mech" />
        </MechanicTooltip>
      </TooltipProvider>,
    );
    const chip = screen.getByRole("link", { name: /^mech: open evidence/ });
    expect(chip.getAttribute("href")).toContain("ref=ev%3AA%3A1%3Amech%3Aunchecked_rage");
    act(() => chip.focus());
    expect(tip()?.textContent).toContain(FACTS.description);
    const describedBy = chip.getAttribute("aria-describedby");
    expect(describedBy && document.getElementById(describedBy)?.textContent).toContain(FACTS.description);
  });
});
