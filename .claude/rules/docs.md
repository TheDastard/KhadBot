---
paths:
  - "docs/**"
  - "CLAUDE.md"
  - "README.md"
  - ".claude/rules/**"
  - ".claude/skills/**"
---

# Docs

Three kinds of doc, each with its own rules:

- **Reference docs** describe how things are now: `docs/wcl/`, `docs/hosting/`, `docs/encounter/`,
  `docs/game-art.md`.
- **Logs and queues**: `docs/decisions.md` (why things are the way they are), `docs/follow-ups.md` (work left to
  do) and `docs/plan-changes.md` (emptied by each plan sync).
- **Context for Claude**: `CLAUDE.md`, `.claude/rules/` and `.claude/skills/`. These are loaded into sessions, so
  every line costs tokens on every task it reaches.

## Keeping docs tight

- **Write for lookup.** Lead with the fact; cut preamble, recaps and "as described above". A reader skims for one
  answer.
- **One home per fact.** Link to it instead of repeating it. `CLAUDE.md` and rules point to reference docs rather
  than copy them, and a reference doc never restates what the code or a generated file (`contracts/openapi.json`,
  `mise tasks`, `scripts/dev/commit_tags.yaml`) already says.
- **No history.** Git holds it. No dated notes, "Update:" lines, "was X, now Y", milestone or task IDs, or
  narratives of how a number was found.
- **Delete what stopped being true** in the commit that made it untrue: a renamed setting, a removed route, a
  finished follow-up, a superseded decision.
- **Size.** A doc stays under 400 lines and 40 KB. Past that, split it by subject into a folder with a short
  `README.md` that says what each file covers (`docs/wcl/`, `docs/hosting/`). Split along subjects a reader looks
  up on their own, never into "part 1" and "part 2".

## Reference docs

- **Edit in place.** Change the section that covers the subject so it states the new fact. Never append a note
  below the old text.
- **Present tense, final state.** Describe what KhadBot and the external system do. A rejected alternative earns at
  most one line under "Known alternatives", with the reason, and only when someone is likely to propose it again.
- **Organised by subject.** Section titles name the subject (`## Report listings`), never a date or a task.
- **Every fact has a source**: the schema page, the documentation URL, the recording under `tests/fixtures/`, or
  the script that measured it. A date belongs only with a fact that expires (a price, a limit), as "as of <date>".
- **Measured numbers** give the value, what was measured and the script, and replace the estimate they settle.
- **Unknowns** go in a short "Open questions" section at the doc's end. An answered question moves into the section
  it belongs to; one that needs work goes to `docs/follow-ups.md`.
- **Names match the code.** A setting, route, file or field named in a doc exists in the same commit. A rename or
  removal updates every doc that names it.
- The KhadBot API is documented by its contract, not here; see `api.md`.

## decisions.md

- One table row per decision still in force: `| D<n> | Decision | Choice | Why | Date |`. The choice says what was
  decided in one or two sentences; the why gives the reason in one or two. A row stays under 600 characters.
- A row records a choice someone could reasonably undo without knowing the reason. How something is built belongs
  in the reference doc, which the row links to.
- Number a new row after the highest ID in the file. IDs are never reused or renumbered; tests and docs cite them.
- When a decision is reversed, edit its row to the new choice (and date) or delete it if nothing replaces it. No
  "superseded" rows.

## follow-ups.md

- One bullet per item: what is missing and where, in a few lines. Detail lives in the issue, the code comment or the
  reference doc it links to.
- Delete an item in the commit that fixes it or decides not to. There is no "Resolved" section.

## Context for Claude

- **`CLAUDE.md`** holds only what every session needs: what the project is, where things are, the gate commands,
  gotchas, conventions. Keep it under about 10 KB. Anything that applies to one area goes in a rule.
- **Rules** (`.claude/rules/*.md`) carry `paths:` frontmatter so they load only when Claude reads matching files.
  `git-commits.md` is the one rule without paths, because every session may commit.
- **Skills** (`.claude/skills/<name>/SKILL.md`) hold procedures used now and then (landing a branch, coordinating
  subagents). They load only when invoked, so a long procedure costs nothing the rest of the time.
- Nested `CLAUDE.md` files in source folders are not used; path-scoped rules do the same job.
- Rules state what to do and the reason in a clause; the history and the full reasoning stay in `docs/decisions.md`.

## Moving and splitting

- Moving a section between files is its own commit, with no wording changes in it, so the diff shows a move.
- Update every link to a moved section in the same commit
  (`grep -rn "<old path>" docs .claude CLAUDE.md README.md src scripts tests apps`).
