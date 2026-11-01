---
paths:
  - "docs/**"
---

# Reference docs

Reference docs are the permanent, authoritative description of one subject: `docs/wcl/` (the WarcraftLogs API as
KhadBot uses it), `docs/game-art.md`, `docs/encounter-pipeline.md`, `docs/hosting.md`. A reader should learn how
things are now, without reading history. These rules don't apply to the logs and queues: `docs/decisions.md`
(append a row), `docs/follow-ups.md` (add an item; remove it when fixed) and `docs/plan-changes.md` (emptied by
each plan sync).

## Updating one

- **Edit in place.** Change the section that covers the subject so it states the new fact. Never append a dated
  note, an "Update:" line or an "Answered on" paragraph below the old text. Git history and `docs/decisions.md`
  hold the history.
- **Present tense, final state.** Describe what KhadBot does and what the external system does. Remove superseded
  designs, abandoned options and research steps. A rejected alternative earns at most one line under "Known
  alternatives", with the reason, and only when someone is likely to propose it again.
- **Organised by subject, not by when it was learned.** Put a new fact in the section it belongs to, or add a
  section for a new subject. Section titles name the subject (`## Report listings`), never a date or a task.
- **Every fact has a source.** Cite the schema page, the documentation URL, the recording under `tests/fixtures/`,
  or the script that measured it (`scripts/wcl/measure_search_cost.py`). A date belongs only with a fact that
  expires (a price, a limit WCL says will change), written as "as of <date>".
- **Measured numbers** give the value, what was measured (which request, which report) and the script. They replace
  the estimate they settle; no "estimated … then measured …" narrative.
- **Unknowns live in one place.** Each doc keeps a short "Open questions" section at its end. When a question is
  answered, move the answer into the section it belongs to and delete the question. When one needs work, it goes
  to `docs/follow-ups.md` instead.
- **Settings and names match the code.** A setting, route, file or field named in a doc exists in the same commit.
  When the code renames or removes one, the same commit updates every doc that names it.
- **One home per fact.** Other docs link to it instead of repeating it. `CLAUDE.md` and `.claude/rules/` point to
  reference docs rather than copy them.

## Size and layout

- Keep a doc under about 400 lines. Past that, split it by subject into a folder with a short `README.md` that says
  what each file covers (`docs/wcl/README.md`, `docs/wcl/points.md`, `docs/wcl/queries.md`).
- Split along subjects a reader looks up on their own, not into "part 1" and "part 2".
- Moving a section between files is its own commit, with no wording changes in it, so the diff shows a move.
- Update every link to a moved section in the same commit (`grep -rn "<old path>" docs .claude CLAUDE.md src scripts tests`).
