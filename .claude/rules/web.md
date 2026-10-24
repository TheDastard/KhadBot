---
paths:
  - "apps/**"
  - "packages/**"
---

# Web and TypeScript

The TypeScript side is a pnpm workspace: `apps/web` (the dashboard), later `apps/desktop` and `apps/discord`, and `packages/api-client`, the client generated from `contracts/openapi.json` that every app uses.

- Data views are client components over the hosted API (TanStack Query). Don't fetch report data in server components.
- Types come only from the generated client. Never hand-write a type for an API payload and never edit the generated files; regenerate them after `mise run gen:contracts`.
- Build to the dashboard design canvas: https://claude.ai/artifact/GJLAo3qYdKq24Ro79bN7cr (read it with the Artifact tool).
- Every status (kill or wipe, pass or fail, running or done) has a text label. Colour alone never carries meaning.
- Add packages with `pnpm --filter <package> add` at an exact version. Never hand-merge `pnpm-lock.yaml`; run `pnpm install`.
