import { describe, expect, it } from "vitest";

import { isThemeChoice, resolveTheme, THEME_SCRIPT, THEME_STORAGE_KEY } from "./theme-script";

describe("theme", () => {
  it("resolves system from the media query and keeps explicit choices", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });

  it("accepts only the three choices", () => {
    expect(isThemeChoice("dark")).toBe(true);
    expect(isThemeChoice("blue")).toBe(false);
    expect(isThemeChoice(null)).toBe(false);
  });

  it("sets the class before paint from the stored choice", () => {
    localStorage.setItem(THEME_STORAGE_KEY, "dark");
    new Function(THEME_SCRIPT)();
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(document.documentElement.dataset.theme).toBe("dark");
    localStorage.setItem(THEME_STORAGE_KEY, "light");
    new Function(THEME_SCRIPT)();
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });
});
