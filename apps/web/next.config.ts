// apps/web/next.config.ts
//
// The dashboard's Next.js settings. Data views render in the browser over the KhadBot API (NEXT_PUBLIC_API_BASE);
// the workspace's api-client package ships TypeScript source, so Next compiles it.

import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  transpilePackages: ["@khadbot/api-client"],
  poweredByHeader: false,
  // KhadBot keeps its agent instructions in CLAUDE.md and .claude/rules/; next dev would otherwise write AGENTS.md.
  agentRules: false,
};

export default config;
