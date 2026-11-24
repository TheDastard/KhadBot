# Git commits and pull requests

## Message format

```
[<tag>][<subtag>] <Imperative summary, no trailing period>

<Body: what changed and why, wrapped at ~72 columns. Paragraphs or
"- " bullets, matching recent history (`git log -5`).>
```

Examples: `[encounter][ingest] Add boss health to pulls and digests`, `[deploy][tests] Queue a job in the stack smoke
test`, `[storage] Add an S3-compatible blob store`.

- The tag names the system the commit mainly changes; the sub-tag names the part of it. Both are lowercase, from
  the registry below, with no space between the brackets and one space before the summary.
- The sub-tag is optional. Use one whenever the change sits within one registered part of the system; leave it off
  for a change across the whole system or a system that has no sub-tags.
- A sub-tag always names a part of its system. Every tag also takes three universal sub-tags without listing
  them: `docs`, `tests` and `fixtures`, for that system's own documentation, test infrastructure and fixtures
  (`[wcl][docs]`, `[deploy][tests]`). A feature commit that brings its tests or docs along keeps the feature's tag
  (`[encounter][ingest]`), not `[...][tests]`.
- Documentation about one system takes that system's tag: `[wcl][docs] Document WCL rate limits`, not
  `[docs][wcl]`. The `docs` tag is for documentation that belongs to no single system (the README, the decisions
  log, follow-ups and plans as a whole).
- Two levels only. What a path already says (which boss, which file) goes in the summary, not a third tag:
  `git log -- <path>` gives a file's history exactly.
- When a commit spans several areas, tag the one where the change starts or that it is about; the body covers the
  rest.
- The summary starts with an imperative verb: Add, Fix, Update, Remove, Swap, Implement. Aim for a subject of 72
  characters or fewer.
- The body describes the change in project terms (modules, behaviour, reasons), so it still makes sense to someone
  who has never seen the refactor plan. Every commit has one.
- There is no project prefix (the old `[KB]`): every commit in this repo is KhadBot's.

## Tags

The tags and sub-tags live in one registry, `scripts/dev/commit_tags.yaml`, each with a description and the paths
it owns. `mise run lint:tags` lists them. The broad shape:

| Tag | Covers | Sub-tags |
|---|---|---|
| `encounter` | Encounter mode, its config and data | `ingest`, `config`, `annotate`, `analysis`, `digest`, `evidence`, `analyst`, `findings`, `tests`, `fixtures`, `eval` |
| `raid` | Authored per-boss encounter files (`config/encounters/`); the summary names the boss | one per raid zone: `venomous-abyss` |
| `agent` | The coach agent | `coach`, `tools`, `rag` |
| `llm`, `config` | `llm_factory.py` and model prices; `config.py` and `.env.example` | |
| `wcl`, `simc`, `blizzard`, `storage` | The shared WCL client, SimC data, the Blizzard API client, blob stores | `wcl`: `probes` (`scripts/wcl/`) |
| `db`, `jobs`, `auth` | Hosted mode | `db`: `migrations`, `read-models`; `jobs`: `queue`, `budget`, `watch` |
| `cli`, `api`, `web` | The apps | `api`: `art`, `raids`, `contracts`, `client`; `web`: `pull`, `timeline`, `raids`, `account`, `ui`, `e2e` |
| `deploy` | Image, compose stack, dev loop, observability | `image`, `compose`, `dev-loop`, `tests`, `observability` |
| `repo` | Repo-wide | `layout`, `deps`, `tooling`, `ci`, `rules`, `commits` |
| `tests`, `fixtures`, `eval` | Test, fixture and eval infrastructure shared across systems | |
| `docs` | Documentation that belongs to no single system | `decisions`, `follow-ups`, `plans` |

Any tag also takes `docs`, `tests` and `fixtures`. The registry is the source of truth; this table is a map of it. For tests, fixtures and evals, prefer the system's
own sub-tag (`[encounter][tests]`) whenever the change belongs to one system; use the top-level `tests`,
`fixtures` or `eval` only for what is shared or belongs to no single system.

### Adding tags

- If no tag or sub-tag fits, add one to the registry in the same commit rather than stretching an existing one.
- A new top-level package or folder gets a tag or sub-tag with its paths in the commit that adds it:
  `tests/unit/scripts/dev/test_commit_tags.py` fails while any tracked file matches no tag.
- Add a sub-tag when a part of a system becomes its own sub-package (see "Module layout" in CLAUDE.md) or keeps
  getting commits of its own.
- A new raid gets a `raid` sub-tag named after its folder without the zone ID (`53-the_venomous_abyss` →
  `venomous-abyss`), in the commit that adds its first boss file.
- When you add, rename or remove a tag, update the table above in the same commit.

### Checks

- The `commit-msg` pre-commit hook (`scripts/dev/check_commit_msg.py`) checks each message as you commit: format,
  registered tag and sub-tag, imperative summary, body, AI attribution and plan IDs. It warns, without failing,
  when none of the staged files match the chosen tag's paths, and when a `raid` commit changes one boss file
  without naming the boss. `fixup!` and `squash!` commits pass it.
- The `commit-messages` CI job runs the same checks on every commit a push adds, and rejects unfolded `fixup!`
  commits; `mise run lint:commits` runs them on a branch locally.

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
- Work split across subagents follows the `parallel-work` skill (`.claude/skills/parallel-work/`): how their worktrees are created, how their branches are integrated, and how the orchestrator removes every worktree and branch it created after landing.
- **Docs-only changes** of any size (`*.md`, `docs/`, `.claude/` rules and skills prose, planning docs) are committed directly on `main`, with no branch or worktree, and skip CI monitoring; `/land` covers the sequence. A comment-only code edit counts only if it alters no executable line and touches no `@tool` docstring.
- **Trivial changes** can be committed directly on `main` too: a typo, a wording fix in comments, a broken link. A change is trivial only if it changes no behaviour, test, config, dependency, contract or generated file, and fits in one small commit. Still run `mise run check` first, follow the commit rules, and commit and push only when asked. After pushing, confirm CI on `main` is green, and fix a red run straight away. When in doubt, use a branch.
