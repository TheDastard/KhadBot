// apps/web/src/lib/theme-script.ts
//
// The theme's plain values: the three choices, their labels, the storage key and THEME_SCRIPT, the inline <head>
// script that sets the `dark` class on <html> before the first paint. No React here, so the root layout (a server
// component) can import it; the hook is in lib/theme.ts.

export type ThemeChoice = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "khadbot-theme";
export const THEME_CHOICES: readonly ThemeChoice[] = ["system", "light", "dark"];
export const THEME_LABELS: Record<ThemeChoice, string> = { system: "System", light: "Light", dark: "Dark" };

export function isThemeChoice(value: unknown): value is ThemeChoice {
  return value === "system" || value === "light" || value === "dark";
}

export function resolveTheme(choice: ThemeChoice, systemDark: boolean): ResolvedTheme {
  if (choice === "system") return systemDark ? "dark" : "light";
  return choice;
}

/** Inline script for <head>: reads the stored choice and sets the class before anything paints. */
export const THEME_SCRIPT = `(function(){try{var c=localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)});` +
  `if(c!=="light"&&c!=="dark")c="system";var d=c==="dark"||(c==="system"&&matchMedia("(prefers-color-scheme: dark)").matches);` +
  `var r=document.documentElement;r.classList.toggle("dark",d);r.dataset.theme=c;}catch(e){}})();`;
