---
name: land
description: Land a finished task branch on main. Syncs with main, runs the checks, pushes the branch, waits for CI on that commit, fast-forwards main, pushes it, then deletes the branch locally and on GitHub and removes its worktree. Use when the user asks to land, ship, merge or finish a branch.
---

# Land a branch

Follows "Branches and merging" in `.claude/rules/git-commits.md`: linear history, `main` moves only on green CI,
the branch and worktree go away afterwards. Every push and every change to `main` here is one the user asked for by
invoking this skill; anything outside these steps (pushing other branches, force-pushing `main`, deleting branches
that aren't this one) still needs its own go-ahead.

Steps 1 to 4 run from the task's worktree; steps 5 and 6 run from the main checkout. Name `main` by ref
(`refs/heads/main`) and never switch branches in a checkout to reach it.

## 1. Preflight

- `git status --short` is empty, and every commit is in its final shape: one logical change each, messages per the
  commit rules. Fold stray fixes into the commit they belong to before anything is pushed.
- Note the branch name (`git branch --show-current`) and the worktree path (`git rev-parse --show-toplevel`).

## 2. Sync with main

```bash
git fetch origin
git merge-base --is-ancestor refs/remotes/origin/main HEAD && echo up-to-date
```

If `main` has moved, rebase onto it (`git rebase refs/remotes/origin/main`). Then, if the task touches anything that
has goldens or generated files, regenerate them rather than hand-merging (`uv run pytest tests/unit --update-goldens
--no-cov`, `mise run gen:contracts`), read the diff, and fold the result into the right commit.

## 3. Check

`mise run check` must be green. Stop and fix it here; never push a red branch to see what CI says.

## 4. Push the branch and wait for CI

```bash
git push -u origin <branch>                    # first push
git push --force-with-lease origin <branch>    # after a rebase or a folded fix
uv run --no-project python -I .claude/skills/land/wait_for_ci.py "$(git rev-parse HEAD)" --branch <branch>
```

`wait_for_ci.py` waits for every Actions run on that exact commit, then prints each job's result and, for failures,
the failed steps and GitHub's annotations (it uses `gh` when installed, otherwise the public API). Run it in the
background; it can take several minutes.

If CI fails: read the annotations, fix the cause, fold the fix into the commit it belongs to, rerun `mise run check`,
force-push with lease and wait again. "Set up job" failures are action references that don't resolve (see the CI
gotcha in CLAUDE.md). Never move `main` past a red or still-running run.

## 5. Fast-forward main and push it

Leave the task's worktree first (`ExitWorktree` with `keep`). `main` is often checked out in the main checkout, and
a session isolated in a worktree can't run git there; the cleanup in step 6 also needs you outside the worktree.
Everything from here on runs from the main checkout, and calls the task worktree's copy of `wait_for_ci.py` by path
(the main checkout may be on a branch that doesn't have it yet).

```bash
git fetch origin
git merge-base --is-ancestor refs/remotes/origin/main refs/heads/<branch>   # main hasn't moved since step 2
```

If `main` moved in the meantime, re-enter the worktree (`EnterWorktree` with its path) and go back to step 2.
Otherwise move `main` in whichever way its checkout state allows; both refuse anything but a fast-forward:

- `main` checked out nowhere (`git worktree list` doesn't show `[main]`):
  `git fetch . "refs/heads/<branch>:refs/heads/main"`
- `main` checked out in the main checkout: confirm `git status --short` is empty there, then
  `git merge --ff-only refs/heads/<branch>`. If it has uncommitted changes, stop and ask the user.
- `main` checked out in some other worktree: stop and ask the user.

Then push it:

```bash
git push origin refs/heads/main:refs/heads/main
uv run --no-project python -I <worktree>/.claude/skills/land/wait_for_ci.py "$(git rev-parse refs/heads/main)" --branch main
```

Pushing `main` starts CI again on the same commit; `wait_for_ci.py` reports every run on it. Always pass
`--branch main`: the branch run on that commit has already finished, and without it the script can return as soon as
it sees that old run, before GitHub has registered `main`'s. Tell the user if it isn't green.

## 6. Clean up

1. Stop anything still running from the worktree's `.venv` and delete it. On Windows a process holding a file open
   makes `git worktree remove` fail halfway:
   `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/distclean.ps1 -Venv <worktree>/.venv`
   (this also stops a CI watcher started from that venv, so finish step 5 first).
2. From the main checkout:
   ```bash
   git worktree remove --force <worktree>     # only ignored files remain: caches, htmlcov
   git branch -d <branch>                     # -d, not -D: refuses if the branch isn't on main
   git push origin --delete <branch>
   git worktree prune
   ```
   `git branch -d` may warn that the branch isn't merged into `HEAD` when the main checkout is on another branch;
   it is merged into `main`, which is what matters.

## 7. Report

Tell the user: the commits now on `main` (short SHA and summary), the CI run links and results for the branch and
for `main`, and what was deleted. Mention any other worktree that is now behind `main`.
