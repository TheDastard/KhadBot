# Git commits and pull requests

## Message format

```
[KB][<tag>] <Imperative summary, no trailing period>

<Body: what changed and why, wrapped at ~72 columns. Paragraphs or
"- " bullets, matching recent history (`git log -5`).>
```

Example: `[KB][encounter-ingest] Add boss health to pulls and digests`.

- The tag names the system or module the commit mainly changes. Exactly one tag, lowercase, from the list below, with no space between `[KB]` and the tag and one space before the summary.
- When a commit spans several areas, tag the one where the change starts or that it is about; the body covers the rest.
- The summary starts with a verb: Add, Fix, Update, Remove, Swap, Implement.
- The body describes the change in project terms (modules, behaviour, reasons), so it still makes sense to someone who has never seen the refactor plan.

## Tags

Encounter mode (`src/khadbot/encounter/`, its config and data):

| Tag | Covers |
|---|---|
| `encounter` | Work spanning the whole encounter mode (setup, mode wiring) |
| `encounter-ingest` | WCL event queries and fetch, raw page cache, normalize, event store, ingest |
| `encounter-config` | Encounter-file and cooldown-registry formats and loaders |
| `encounter-files` | Authored per-boss encounter data under `config/encounters/` |
| `encounter-annotate` | Phase detection, annotation, failure rules and blame |
| `encounter-analysis` | `encounter/analysis/` (deaths, mechanics, cooldowns, aggro) |
| `encounter-digest` | Pull digest building, rendering and its CLI commands |
| `encounter-evidence` | Evidence refs and resolution |
| `encounter-analyst` | Analyst query tools, versioning, verify, analyst graph |
| `encounter-findings` | Findings store and the cached per-pull review |
| `encounter-tests` | Encounter test infrastructure, goldens and perf tests |
| `encounter-fixtures` | Recorded and synthetic WCL fixtures, fixture encounter files and registries (`tests/fixtures/encounter/`) |
| `encounter-eval` | Golden-question eval harness for the analyst |

Rest of the app:

| Tag | Covers |
|---|---|
| `coach` | Coach agent, personas, prompt assembly, `config/agents/` |
| `cli` | Rich + prompt_toolkit REPL |
| `llm` | `llm_factory.py` and model/provider selection |
| `app-config` | `config.py`, `.env.example`, environment variables |
| `agent-tools` | The agent's LangChain tools and their registry (`agent/tools/`) |
| `wcl-client` | Shared WarcraftLogs client (`wcl/`) |
| `rag` | Retrieval and guide data |
| `simc` | SimC generated client data: codes, parser and fetch (`simc/`) |
| `db` | Postgres for hosted mode (`src/khadbot/db/`): connection pool, migrations, SQL-backed stores, the `migrate` subcommand |
| `jobs` | Job specs and job functions, and the queue wiring that runs them (`src/khadbot/jobs/`) |
| `storage` | Blob stores the pipeline's caches and event store write through, and their factory (`storage/`) |
| `api` | The dashboard API (`src/khadbot/api/`), its `serve` subcommand and the generated `contracts/` |
| `web` | The dashboard frontend (`apps/web/`) |
| `api-client` | The generated API client every TypeScript app uses (`packages/`) |

Repo-wide:

| Tag | Covers |
|---|---|
| `repo` | Package layout, entry point, dependencies, tooling, Claude rules |
| `tests` | Test infrastructure shared across the app (`tests/conftest.py`, `tests/helpers/fake_llm.py`) and non-encounter tests |
| `fixtures` | Non-encounter test fixtures |
| `eval` | Eval tooling shared across the app (the `test:eval` task, eval markers) |
| `docs` | Commits that change only documentation |

For tests, fixtures and evals, prefer the system's own tag (`encounter-tests`, `encounter-fixtures`, `encounter-eval`) whenever the change belongs to one system. Use the broader `tests`, `fixtures` or `eval` only when it is shared across systems or belongs to no single one. If no tag fits, add one to this list in the same commit rather than stretching an existing tag.

## Never include

- **AI attribution.** No `Co-Authored-By: Claude ...` trailer, no "Generated with Claude Code" footer, no session links. This applies to commit messages and pull request titles and descriptions.
- **Plan-stage IDs.** No milestone, task, human-task, spike or decision IDs such as `M1`, `M2.3`, `H4`, `S1`, `D7`, and no phrases like "milestone 1" or "spike 2". Name what the change does instead: "Add paginated WCL event fetcher", not "M1.4 fetcher".

Before every commit, reread the full message and remove anything from either list.

## Workflow

- Commit or push only when the user asks. Branch off `main` first; don't commit to `main` directly, apart from the trivial changes described under "Branches and merging".
- Run `mise run check` (or the uv equivalents in CLAUDE.md) before committing.
- One logical change per commit. Don't mix formatting-only churn with behaviour changes.
- Never commit `.env`, `.khadbot_data/`, recorded data containing private report codes the user hasn't approved, or API keys.

## Branches and merging

- One task per branch, each in its own worktree under `.claude/worktrees/`, branched from current `main`. Work in the worktree; never `cd` back to the main checkout to run git.
- `main` history stays linear: rebase the branch onto `main`, then fast-forward `main` to it. No merge commits, no squash merges on GitHub.
- Rewrite the branch's history freely until it is pushed (fold fixes into the commit they belong to). After a push, rewrite only your own branch, and push it with `--force-with-lease`, never `--force`.
- `main` moves only after CI is green on the exact commit being fast-forwarded.
- After `main` is pushed, delete the branch locally and on GitHub, and remove its worktree.
- Pushing a branch, moving `main` and deleting branches each need the user's go-ahead, like commits. `/land` runs the whole sequence once they ask for it.
- Work split across subagents follows `parallel-work.md`: how their worktrees are created, how their branches are integrated, and how the orchestrator removes every worktree and branch it created after landing.
- **Trivial changes** can be committed directly on `main`, with no branch or worktree: a typo, a wording fix in docs or comments, a broken link. A change is trivial only if it changes no behaviour, test, config, dependency, contract or generated file, and fits in one small commit. Still run `mise run check` first, follow the commit rules, and commit and push only when asked. After pushing, confirm CI on `main` is green, and fix a red run straight away. When in doubt, use a branch.
