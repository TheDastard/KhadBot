// apps/web/eslint.config.mjs: Next's flat configs (core web vitals and TypeScript). `pnpm lint` allows no warnings.
import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },
  {
    // Playwright fixtures pass values with a function named `use`, which the React hooks rule mistakes for a hook.
    files: ["e2e/**", "playwright*.config.ts"],
    rules: { "react-hooks/rules-of-hooks": "off" },
  },
  globalIgnores([".next/**", "next-env.d.ts", "playwright-report/**", "test-results/**", "e2e/.auth/**"]),
]);
