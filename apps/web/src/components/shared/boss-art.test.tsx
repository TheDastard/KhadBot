import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ArtAttributionLine, BossPortrait, BossThumbnail, portraitUrl } from "./boss-art";

afterEach(cleanup);

describe("BossThumbnail", () => {
  it("draws the image beside the name, with no alt", () => {
    render(<BossThumbnail src="https://render.worldofwarcraft.com/us/icons/56/1.jpg" size={28} />);
    const box = screen.getByTestId("boss-thumbnail");
    expect(box.getAttribute("data-art")).toBe("image");
    expect(box.querySelector("img")?.getAttribute("alt")).toBe("");
    expect(box.style.width).toBe("28px");
  });

  it("draws the neutral square without a URL", () => {
    render(<BossThumbnail size={28} />);
    const box = screen.getByTestId("boss-thumbnail");
    expect(box.getAttribute("data-art")).toBe("fallback");
    expect(box.querySelector("img")).toBeNull();
  });

  it("falls back after an image error, and tries a new URL afresh", () => {
    const { rerender } = render(<BossThumbnail src="https://render.example/a.jpg" size="fill" />);
    fireEvent.error(screen.getByTestId("boss-thumbnail").querySelector("img")!);
    expect(screen.getByTestId("boss-thumbnail").getAttribute("data-art")).toBe("fallback");
    rerender(<BossThumbnail src="https://render.example/b.jpg" size="fill" />);
    expect(screen.getByTestId("boss-thumbnail").getAttribute("data-art")).toBe("image");
  });
});

describe("BossPortrait", () => {
  it("is 2:1 and has no alt", () => {
    render(<BossPortrait src="http://api/v1/art/portraits/x.png?v=1" width={48} />);
    const img = screen.getByTestId("boss-portrait");
    expect(img.getAttribute("alt")).toBe("");
    expect(img.getAttribute("width")).toBe("48");
    expect(img.getAttribute("height")).toBe("24");
  });

  it("draws nothing without a URL", () => {
    render(<BossPortrait width={96} />);
    expect(screen.queryByTestId("boss-portrait")).toBeNull();
  });

  it("draws nothing after an image error", () => {
    render(<BossPortrait src="http://api/p.png" width={96} />);
    fireEvent.error(screen.getByTestId("boss-portrait"));
    expect(screen.queryByTestId("boss-portrait")).toBeNull();
  });
});

describe("portraitUrl", () => {
  it("puts the API's origin before the path, and keeps null as nothing", () => {
    expect(portraitUrl("/v1/art/portraits/a.png?v=ab", "http://localhost:8000")).toBe(
      "http://localhost:8000/v1/art/portraits/a.png?v=ab",
    );
    expect(portraitUrl(null, "http://localhost:8000")).toBeUndefined();
  });
});

describe("ArtAttributionLine", () => {
  it("shows the text, or nothing", () => {
    const { rerender } = render(<ArtAttributionLine text="Boss art is the property of Blizzard." />);
    expect(screen.getByTestId("art-attribution").textContent).toContain("Blizzard");
    rerender(<ArtAttributionLine text={null} />);
    expect(screen.queryByTestId("art-attribution")).toBeNull();
  });
});

describe("useBossArt", () => {
  it("finds a boss by WCL encounter ID, prefixes the API origin to its portrait and leaves the thumbnail URL as is", async () => {
    vi.resetModules();
    vi.doMock("@/lib/api", () => ({ apiBase: () => "http://localhost:8000" }));
    vi.doMock("@/lib/queries", () => ({
      useEncounterArt: () => ({
        data: {
          attribution: "Boss art is the property of Blizzard.",
          encounters: [
            { encounter_id: 1, name: "A", portrait_url: null, thumbnail_url: null },
            { encounter_id: 3492, name: "Ula'tek", portrait_url: "/v1/art/portraits/Ulatek.png?v=ab", thumbnail_url: "https://render.worldofwarcraft.com/us/icons/56/9.jpg" },
          ],
        },
      }),
    }));
    const { renderHook } = await import("@testing-library/react");
    const mod = await import("./boss-art");
    expect(renderHook(() => mod.useBossArt(3492)).result.current).toEqual({
      portraitSrc: "http://localhost:8000/v1/art/portraits/Ulatek.png?v=ab",
      thumbnailSrc: "https://render.worldofwarcraft.com/us/icons/56/9.jpg",
    });
    expect(renderHook(() => mod.useBossArt(1)).result.current).toEqual({ portraitSrc: undefined, thumbnailSrc: undefined });
    expect(renderHook(() => mod.useBossArt(999)).result.current.portraitSrc).toBeUndefined();
    expect(renderHook(() => mod.useBossArt(null)).result.current.thumbnailSrc).toBeUndefined();
    expect(renderHook(() => mod.useArtAttribution()).result.current).toContain("Blizzard");
    vi.doUnmock("@/lib/api");
    vi.doUnmock("@/lib/queries");
  });
});
