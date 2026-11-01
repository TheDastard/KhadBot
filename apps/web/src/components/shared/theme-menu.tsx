// apps/web/src/components/shared/theme-menu.tsx
//
// The top bar's theme control: a menu of System, Light and Dark (review answer 8), remembered per browser
// (lib/theme.ts). The trigger names the current choice in its label and accessible name.

"use client";

import { MonitorIcon, MoonIcon, SunIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { isThemeChoice, THEME_CHOICES, THEME_LABELS, useTheme, type ThemeChoice } from "@/lib/theme";

const ICON: Record<ThemeChoice, typeof SunIcon> = { system: MonitorIcon, light: SunIcon, dark: MoonIcon };

export function ThemeMenu() {
  const { choice, setChoice } = useTheme();
  const Icon = ICON[choice];
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="secondary" aria-label={`Theme: ${choice}. Change theme`} className="text-[13px]">
          <Icon className="size-4" aria-hidden />
          {THEME_LABELS[choice]}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" aria-label="Theme">
        <DropdownMenuRadioGroup value={choice} onValueChange={(value) => isThemeChoice(value) && setChoice(value)}>
          {THEME_CHOICES.map((option) => {
            const OptionIcon = ICON[option];
            return (
              <DropdownMenuRadioItem key={option} value={option}>
                <OptionIcon aria-hidden />
                {THEME_LABELS[option]}
              </DropdownMenuRadioItem>
            );
          })}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
