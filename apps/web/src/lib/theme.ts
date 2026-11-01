// apps/web/src/lib/theme.ts
//
// The theme menu's three choices (System, Light, Dark), remembered per browser in localStorage. The resolved theme is
// the `dark` class on <html> (styles/globals.css). THEME_SCRIPT (lib/theme-script.ts) runs in <head> before the first
// paint so a dark page never flashes light; useTheme() keeps it in step afterwards, including when the system
// setting changes.

"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";

import {
  isThemeChoice,
  resolveTheme,
  THEME_STORAGE_KEY,
  type ResolvedTheme,
  type ThemeChoice,
} from "./theme-script";

export * from "./theme-script";

function readChoice(): ThemeChoice {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isThemeChoice(stored) ? stored : "system";
  } catch {
    return "system";
  }
}

const listeners = new Set<() => void>();
const DARK_QUERY = "(prefers-color-scheme: dark)";

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const media = window.matchMedia(DARK_QUERY);
  media.addEventListener("change", listener);
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    media.removeEventListener("change", listener);
    window.removeEventListener("storage", listener);
  };
}

function apply(choice: ThemeChoice): ResolvedTheme {
  const resolved = resolveTheme(choice, window.matchMedia(DARK_QUERY).matches);
  const root = document.documentElement;
  root.classList.toggle("dark", resolved === "dark");
  root.dataset.theme = choice;
  return resolved;
}

/** The current choice and resolved theme, and a setter that remembers the choice. */
export function useTheme(): { choice: ThemeChoice; resolved: ResolvedTheme; setChoice: (choice: ThemeChoice) => void } {
  const choice = useSyncExternalStore(subscribe, readChoice, () => "system" as ThemeChoice);
  const systemDark = useSyncExternalStore(
    subscribe,
    () => window.matchMedia(DARK_QUERY).matches,
    () => false,
  );
  const resolved = resolveTheme(choice, systemDark);

  useEffect(() => {
    apply(choice);
  }, [choice, systemDark]);

  const setChoice = useCallback((next: ThemeChoice) => {
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Storage refused (private window): the choice still applies for this page.
    }
    apply(next);
    listeners.forEach((listener) => listener());
  }, []);

  return { choice, resolved, setChoice };
}
