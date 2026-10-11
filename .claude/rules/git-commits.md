# Git commits and pull requests

## Message format

```
[KB] <Imperative summary, no trailing period>

<Body: what changed and why, wrapped at ~72 columns. Paragraphs or
"- " bullets, matching recent history (`git log -5`).>
```

- The summary starts with a verb: Add, Fix, Update, Remove, Swap, Implement.
- The body describes the change in project terms (modules, behaviour, reasons), so it still makes sense to someone who has never seen the refactor plan.

## Never include

- **AI attribution.** No `Co-Authored-By: Claude ...` trailer, no "Generated with Claude Code" footer, no session links. This applies to commit messages and pull request titles and descriptions.
- **Plan-stage IDs.** No milestone, task, human-task, spike or decision IDs such as `M1`, `M2.3`, `H4`, `S1`, `D7`, and no phrases like "milestone 1" or "spike 2". Name what the change does instead: "Add paginated WCL event fetcher", not "M1.4 fetcher".

Before every commit, reread the full message and remove anything from either list.

## Workflow

- Commit or push only when the user asks. Branch off `main` first; don't commit to `main` directly.
- Run `make check` (or the uv equivalents in CLAUDE.md) before committing.
- One logical change per commit. Don't mix formatting-only churn with behaviour changes.
- Never commit `.env`, `.khadbot_data/`, recorded data containing private report codes the user hasn't approved, or API keys.
