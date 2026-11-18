---
paths:
  - "src/khadbot/api/**"
  - "contracts/**"
  - "packages/api-client/**"
---

# The KhadBot API

The API documents itself: `contracts/openapi.json`, generated from the FastAPI app, is the reference for every
route, and the TypeScript client's types are generated from it. There is no hand-written endpoint doc, and none
should be added to `docs/`.

## Documenting a route

- The handler's docstring is the route's description in the contract: what it returns and to whom (signed in,
  visitor, local mode). Keep implementation notes in the module docstring or comments, not in the handler's.
- Every field of a response or request schema carries `Field(description=...)` unless its name says everything.
  Units go in the description (`ms from fight start`, `percent, 0 to 100`).
- Every error a route can answer is listed in `responses=` with `ErrorBody` and a description that names its
  `code` (`report_not_found`, `wcl_unavailable`). Reuse the shared response maps (`_NOT_FOUND`,
  `ACCESS_BUDGET_RESPONSE`) instead of restating them.
- Tags group routes by resource (`reports`, `pulls`, `jobs`, `meta`).

## Changing the contract

- Any change to a route, schema, docstring or response map regenerates the contract in the same commit:
  `mise run gen:contracts`, then `mise run gen:types`. `tests/unit/api/test_app.py` and `mise run check:web` fail
  while either drifts.
- A breaking change (a removed or renamed field, a narrowed type) updates every caller in `apps/` in the same
  change.
- Settings, deployment and operations of the API (environment variables, limits, sign-in setup) are documented in
  `docs/hosting/`, not in the contract.

## Access and safety

- Every report route checks that the caller may read the report. A report the caller cannot read answers 404,
  never 403, so the API never confirms that a private report exists.
- Report routes get the caller from `auth.routes.optional_user` or `current_user` and check with
  `ReportAccess.require_readable` (`wiring.report_access()`); never write a second access check.
- State-changing routes depend on `auth.routes.require_csrf` (the `X-KhadBot-CSRF: 1` header plus an `Origin`
  check). Cookies stay HttpOnly and SameSite=Lax, and Secure everywhere but plain-HTTP localhost.
