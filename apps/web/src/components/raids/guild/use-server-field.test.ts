// apps/web/src/components/raids/guild/use-server-field.test.ts: a server named before the list loads is chosen when it arrives.

import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { foldName, type ServerList } from "./servers";
import { useServerField } from "./use-server-field";

const entry = (name: string, slug: string, region: string) => ({ name, slug, region, match: foldName(name) });
const LIST: ServerList = {
  regions: [
    { slug: "us", name: "US", complete: true, servers: [entry("Skullcrusher", "skullcrusher", "us")] },
    { slug: "eu", name: "EU", complete: true, servers: [entry("Stormrage", "stormrage", "eu")] },
  ],
};

describe("useServerField", () => {
  it("chooses a server named by slug once the list arrives", () => {
    const { result, rerender } = renderHook(({ list }) => useServerField(list, null), {
      initialProps: { list: undefined as ServerList | undefined },
    });
    act(() => result.current.chooseBySlug("us", "skullcrusher"));
    expect(result.current.text).toBe("skullcrusher");
    expect(result.current.choice).toBeNull();
    rerender({ list: LIST });
    expect(result.current.choice?.slug).toBe("skullcrusher");
    expect(result.current.text).toBe("Skullcrusher (US)");
  });

  it("keeps the slug as text when the list has no such server", () => {
    const { result } = renderHook(() => useServerField(LIST, null));
    act(() => result.current.chooseBySlug("us", "nowhere"));
    expect(result.current.choice).toBeNull();
    expect(result.current.text).toBe("nowhere");
  });
});
