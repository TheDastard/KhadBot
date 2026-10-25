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

## Local and hosted

One web app runs against the hosted API and against a local one. Local mode is the CLI plus `main.py serve` (`mise run serve:fixtures` serves a recorded data directory): the read routes and no sign-in, a quick look at a data directory. The full experience (sign-in, ingest, live watch, the budget, reviews, chat) runs on the local test environment, hosted mode on your own machine, which `docs/hosting.md` documents. The API says what it offers; the app does not guess.

- Read `GET /v1/health` at start, before the first screen. Its `features` (`sign_in`, `ingest_jobs`, `live_watch`, `budget`, `reviews`, `chat`) say which areas exist, each true only when that area's routes are mounted. Branch only on `features`: never on `mode`, the API base URL, the environment or a 404. `mode` (`local` or `hosted`) is a label the entry point gives; show it if you like, never test it.
- With `sign_in` off the app shows the reports without a session: no landing page, no sign-in or sign-out, the reports list is the first screen, and the header is labelled "Local".
- Hide navigation to any area whose feature is off. A direct link to one shows "Not available on this server yet", not a blank page or an error.
- `main.py serve` has no cookie, no CSRF header and no write routes; it answers `GET` only, from `http://localhost:3000` or `http://127.0.0.1:3000`. `NEXT_PUBLIC_API_BASE` picks the API in every environment; assume no `localhost` default in code.
- A new optional area adds its flag to `Features` in the same change that mounts its routes; the app shows it only when the flag is true.
