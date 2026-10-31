# Parallel work with subagents

How a session that coordinates subagents (the orchestrator) gives each one its own worktree, integrates their
branches and cleans up. Everything in `git-commits.md` still applies. A single integration branch that collects
several tasks before landing is allowed only when the user asks for it; otherwise each task lands on its own.

## When to use subagents

Tasks that come from a plan are always executed by subagents, one per task, never by the orchestrator itself. The
orchestrator reads the plan, picks the tasks, writes each brief, spawns the agents, reviews their reports and
integrates their branches into one result (see below); it writes no task code. Tasks that depend on each other run
in order, the rest in parallel. Work that didn't come from a plan stays with whoever was asked.

## Setting up worktrees

- **The orchestrator stays in the main checkout.** A session that has entered a worktree (`EnterWorktree`) pins
  every subagent it spawns to that worktree: their `EnterWorktree` into another worktree is refused and they can
  write nowhere else. If you have entered one, leave it (`ExitWorktree` with `keep`) before spawning. Run your own
  git in a worktree with `cd <worktree> && git ...` in the same command, never in the main checkout.
- **Create each task's worktree yourself** before spawning its agent:
  `git worktree add -b <branch> .claude/worktrees/<slug> <base>`, where `<base>` is `refs/remotes/origin/main` or
  the integration branch. Don't use the Agent tool's `isolation: "worktree"` when the base is anything but
  `origin/main`: it always branches from `origin/main`.
- **Tell the subagent to work by absolute path, not `EnterWorktree`.** From the main checkout `EnterWorktree` is
  refused ("not an isolated worktree"). The brief says:
  - start every shell command with `cd <worktree> &&`, and use absolute paths under the worktree for file tools;
  - before the first edit, check that `git rev-parse --show-toplevel` and `git branch --show-current` name that
    worktree and branch, and stop if they don't;
  - never write or run git in the main checkout or another task's worktree, and never use `git stash` (the stash
    is shared by every worktree);
  - run `uv sync --extra dev` first, and again after any rebase that changes dependencies: `uv run` on its own
    doesn't install the `dev` extra, so a new dev dependency (moto) is missing until you sync;
  - number new `docs/decisions.md` rows after the highest one on its branch; the orchestrator renumbers on conflict;
  - stop every background job it started before it reports.

## While agents run

- Check each report before integrating: read the diff against the task, run `mise run check` in the task
  worktree, and check local mode. A report is a claim, not proof.
- Integrate one branch at a time: in the task worktree rebase onto the integration tip, run the check, then
  `git merge --ff-only <branch>` in the integration worktree and run the check there.
- Parallel test runs share one machine. Under load, Windows timing and file-sharing tests flake, and an agent that
  leaves test runs or servers going slows everyone. Stop a finished agent that keeps reporting with `TaskStop`;
  start full checks one at a time where you can.
- Before landing, prove every commit:
  `git rebase --exec "uv sync --extra dev -q && uv run --extra dev ruff check src tests scripts && uv run --extra dev mypy src && uv run --extra dev lint-imports && uv run --extra dev pytest tests/unit -q --no-cov" refs/remotes/origin/main`.
  A flaky failure stops the rebase; rerun that commit's tests, and `git rebase --continue` only once they pass.

## Cleaning up

After `main` is pushed and its CI is green, the orchestrator removes every worktree and branch it created: each
task's and the integration branch's. Leave worktrees you didn't create alone.

1. Stop each worktree's processes and delete its `.venv`, or `git worktree remove` fails halfway on Windows:
   `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev/distclean.ps1 -Venv <worktree>/.venv`.
2. `git worktree remove --force <worktree>` for each one, then `git worktree prune`.
3. Delete the branches. A task branch that was rebased during integration is not an ancestor of `main`, so
   `git branch -d` refuses it. Run `git cherry -v refs/heads/main <branch>` first: every commit must show `-`
   (already on `main`), except one you changed yourself while integrating (a folded fix or a renumbered row),
   which you can name. Then `git branch -D <branch>`. Delete pushed branches on GitHub too
   (`git push origin --delete <branch>`).
4. Confirm with `git worktree list`, `git branch -a` and `ls .claude/worktrees` that nothing of yours is left, and
   tell the user about any leftover directory that isn't a registered worktree.
