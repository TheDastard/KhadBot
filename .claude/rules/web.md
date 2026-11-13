---
paths:
  - "apps/**"
  - "packages/**"
---

# Web and TypeScript

The TypeScript side is a pnpm workspace: `apps/web` (the dashboard), later `apps/desktop` and `apps/discord`, and `packages/api-client`, the client generated from `contracts/openapi.json` that every app uses.

- Data views are client components over the hosted API (TanStack Query). Don't fetch report data in server components.
- The browser talks to the KhadBot API, with one stated exception: Wowhead's tooltip script (`https://wow.zamimg.com/js/tooltips.js`), which turns ability links (`https://www.wowhead.com/spell=<id>`) into tooltips and icons, as Warcraft Logs does. The server fetches and stores no Wowhead content. The embed config and the Content-Security-Policy hosts are in `docs/game-art.md`. Every other image has a fallback and the app passes its tests with all of them missing.
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

## The workspace

- `package.json` (private, `packageManager` pins pnpm), `pnpm-workspace.yaml` and `pnpm-lock.yaml` at the root. The chosen versions and the reasons are in `docs/decisions.md` (web workspace).
- `packages/api-client` (`@khadbot/api-client`): `src/schema.d.ts` generated from `contracts/openapi.json` (`mise run gen:types`; `check:web` fails when it drifts), `createApiClient` (openapi-fetch; the cookie, and the `X-KhadBot-CSRF` header on every POST, PUT, PATCH and DELETE), `unwrap` (data, or an `ApiError` with `status`, `code`, `message`, `retryAfterS`; `api_unreachable` when nothing answered) and `apiUrl` / `openEventStream`. Type a response as `ApiGet<"/v1/...">`, not `Schema<"...">`: openapi-fetch reads tuples more loosely than the schema. `Schema<"...">` is for request bodies and enums.
- `apps/web` (`@khadbot/web`): Next.js App Router, strict TypeScript, Tailwind v4, shadcn/ui components written into `components/ui/` by hand from shadcn's source (its CLI fetches from its own registry), TanStack Query. Pages are client components; `app/layout.tsx` is the only server component that matters.
- In the app, reach the API only through `lib/api.ts` (`api()`, `apiHref`, `apiEvents`). It reads `/v1/health` without the cookie and sends the cookie afterwards only when `features.sign_in` is on (a server without sign-in refuses credentialed CORS).
- Gate: `mise run check:web` (drift check, ESLint with no warnings, `tsc`, Vitest); `mise run check:all` runs it after `mise run check`.

## Who owns what in apps/web/src

Each area works in its own directories; wiring into someone else's file is a few lines at a marked slot. A change an area needs in `components/shared/` or `lib/` after the shell has landed goes in its own small commit.

| Path | Owner |
|---|---|
| Workspace root files, `packages/api-client/`, `app/layout.tsx`, `app/providers.tsx`, `app/fonts.ts`, `app/page.tsx`, `app/r/[code]/p/[fight]/` (route shells and slots), `lib/`, `styles/`, `components/ui/`, `components/shared/` | Shell |
| `components/account/` (and the top bar's account slot) | Sign-in and account menu |
| `components/raids/` (and the `raids-page` slot, filled; its Guild and Character tab slots are `raids-guild` and `raids-character`) | Raids page and log search |
| `components/raids/guild/`, `components/raids/character/` (and the Guild and Character tab slots) | Guild and character search |
| `components/open-pull/` (and the `pull-not-loaded` slot) | Opening a pull |
| `components/pull/`, `components/panel/` (and the `pull-body` slot) | Pull page and context panel |
| `components/timeline/`, `components/events/` (and the `pull-timeline` slot) | Timeline and zoomed events |

## Route shells and slots

A slot is a spot marked `Slot: <name>` in a comment, where one area's component goes; a new slot starts as a `<SlotPlaceholder slot="...">` (a dashed box with `data-slot`) until its area replaces that line. Every slot of the first round is filled. Marked spots with nothing in them yet: `review-button` in the pull header (pull reviews, `features.reviews`), and the night overview's links (`views` in the top bar, `night-overview` on the night card, and the breadcrumb's report code, plain text until then).

- `/` (`app/page.tsx`): `raids-page` inside the page column under the top bar. The Raids page renders its own "Raids" heading. With `features.report_lookup` off it lists the server's reports instead of the search.
- `/r/[code]/p/[fight]` (`pull-route.tsx`, slots in `slots.tsx`): the shell reads the digest (`usePullDigest`) and picks a branch. Digest written: `pull-body` (`PullPage` from `components/pull/`, props `code`, `fight`, `digest`, `timeline`), which lays out the 240 px rail, the header, the `timeline` node, then the sections and the 380 px panel; `pull-timeline` (`PullTimelineSlot` in `slots.tsx`, props `code`, `fight`, `digest`). Digest 404 (`pull_not_found` or `report_not_found`) and `features.ingest_jobs` on: `pull-not-loaded` (`PullNotLoaded` from `components/open-pull/`, props `code`, `fight`); it invalidates `queryKeys.pull(code, fight)` when the digest is written, which swaps the pull page in without a reload. With `ingest_jobs` off that branch shows "Not available on this server yet".
- Top bar (`components/shared/top-bar.tsx`): pages pass `breadcrumb` (`<Breadcrumb items=...>`); the views slot is empty this round (the night overview's tab returns there); `AccountSlot` renders only with `features.sign_in`.

## Shared code

- `lib/capabilities.tsx`: `CapabilitiesGate` (reads `/v1/health` first), `useFeatures()`, `useFeature(name)`, `<RequireFeature feature area>` (the "Not available on this server yet" notice when off).
- `lib/focus.ts`: the pull page's focus in the URL: `ref`, `t` (fight-relative ms, `start-end`) and `full` (`1`); each change is a shallow history push (`pushState`, no server round trip). Read it through a slice (`useFocusRef`, `useFocusFull`, `useFocusWindow`, `useIsFocusedRef`, `useFocusBaseSearch`) so a component re-renders only when its part changes, and change it with `useFocusActions()`; `useFocus()` returns everything and re-renders on every change. `<FocusSync />` (in the pull page) passes on Next.js's own URL changes. `windowAround(ms)` gives the 10 s zoom.
- `lib/refs.ts`: `parseRef` / `buildRef` (the API's ref shapes), `pullPath`, `pullRef`, `refShortLabel`.
- `lib/format.ts`: `formatClock` (m:ss), `formatClockTenths` (m:ss.s), `formatPct` (one decimal, `<0.1%` above zero), `formatNumber`, `formatCompact`, `formatCountdown`.
- `lib/quality.ts`: the fight % bands (`qualityOf`, `barFill`, labels); `lib/classes.ts`: class to colour token.
- `lib/queries.ts`: `queryKeys` (prefixes by report and pull), `useHealth`, `usePullDigest` (which starts `pullTimelineQuery` beside it), `usePlayers`. Area queries live with their components and build keys from these prefixes.
- `components/shared/`: `StatusBadge` (every status a word; loading uses Waiting, Loading, Ready, Stopped, Failed), `FightPct` and `FightPctBar`, `PlayerName`, `AbilityName` (a Wowhead spell link; fallback square and name-and-ID tooltip while the script is absent), `EvidenceChip`, `ErrorState` (title, body, action, the `where · code` label), `Notice` (inline `role="status"`, no toasts), `NotAvailable`, skeletons (`SkeletonBlock`, `SkeletonText`, `SkeletonCard`), `TopBar`, `Breadcrumb`, `PageColumn`, `ThemeMenu`.
- Colours, fonts and radius come only from the tokens in `styles/globals.css` (`bg-s1`, `text-mut`, `text-q-epic-text`, `bg-q-epic`, `text-cls-druid`, ...). Never write a literal colour in a component. Fight % text uses the `-text` tokens, bars the plain ones.

## Tests

- Vitest for logic and small components: `src/**/*.test.ts(x)` beside the code, jsdom.
- Playwright (`apps/web/e2e/`), with `test` and `expect` from `e2e/support/fixtures.ts`: it aborts every request off the machine (so Wowhead's script and any third-party image are missing, as the rules require the app to handle) and `test.use({ user: "alice" })` signs in as a test user (`alice` and `bob`, sessions from `main.py test-session`, made in `e2e/support/global-setup.ts`; the storage-state files in `e2e/.auth/` are gitignored). `expectAccessible(page)` (`e2e/support/axe.ts`) runs axe in light and dark at 1440 and fails on serious or critical violations: every screen calls it.
- Tests that measure latency carry `{ tag: "@budget" }` and assert their budget. `pnpm e2e` (`e2e/run.mjs`) runs everything else on `E2E_WORKERS` parallel workers (default 4, `e2e/support/env.ts`; never Playwright's default, which overloads the one dev server and API) (project `chromium`), then the tagged tests on one worker (project `budgets`), because a budget measured beside parallel tests measures the load. Never loosen a budget to cope with load; tag the test. `expectAccessible` takes a `ready` check that runs after each theme's reload, so axe never scans a screen whose data has not arrived.
- Hosted suite: `mise run test:e2e`, against the dev loop (`mise run dev:all` with WCL replay on in `deploy/dev.env`, then `mise run dev:seed -- --only SYNTH01`). Reports the tests may read are `E2E_REPORTS` in `e2e/support/env.ts`. Global setup marks each of them readable by the test users and not public, so a report a test uses as public (the Raids spec's `trMgApZCVYkv3jdq`) must never be in `E2E_REPORTS`. A spec that reads a pull the stack may not have loaded calls `ensurePullLoaded` (`e2e/support/pulls.ts`) in a `beforeAll`, so it does not depend on another spec having opened it first. Local suite: `mise run test:e2e:local` (`e2e/local/`), which starts `main.py serve` on 8001 over `.khadbot_fixture_data` and its own web server on 3000; stop the dev loop's web server first. `docs/hosting.md`, "The web app", has the steps.
