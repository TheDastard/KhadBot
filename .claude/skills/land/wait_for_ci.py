"""
.claude/skills/land/wait_for_ci.py

Wait for every GitHub Actions run on one commit to finish, then report each job. For failed jobs it also prints the
failed steps and GitHub's annotations, which carry the error message (for example an action tag that doesn't
resolve). Exits 0 when every run succeeded, 1 otherwise.

With ``--branch`` it also waits until a run for that branch exists. Pushing ``main`` to a commit whose branch run has
already finished would otherwise report that old run before GitHub registers the new one.

Uses the GitHub CLI when it is installed and logged in; otherwise the public REST API (the repo is public), with
GITHUB_TOKEN if set. Standard library only, so it runs with ``uv run --no-project python -I``.

Usage: wait_for_ci.py <commit-sha> [--branch <name>] [--timeout-min 30]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

# ---------------------------------------------------------------------------
# GitHub access
# ---------------------------------------------------------------------------


def _repo_slug() -> str:
    url = subprocess.run(
        ["git", "remote", "get-url", "origin"], capture_output=True, text=True, check=True
    ).stdout.strip()
    match = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    if not match:
        sys.exit(f"origin is not a GitHub remote: {url}")
    return match.group(1)


def _use_gh() -> bool:
    if not shutil.which("gh"):
        return False
    return subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0


USE_GH = _use_gh()


def api(path: str) -> object:
    """GET a REST API path such as ``repos/<owner>/<repo>/actions/runs``."""
    if USE_GH:
        out = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=True).stdout
        return json.loads(out)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "khadbot-land"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"https://api.github.com/{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


# ---------------------------------------------------------------------------
# Waiting and reporting
# ---------------------------------------------------------------------------

# Unauthenticated API calls are limited to 60 an hour, so poll gently without gh or a token.
POLL_SECONDS = 15 if USE_GH or os.environ.get("GITHUB_TOKEN") else 45


def wait(repo: str, sha: str, timeout_min: int, branch: str | None = None) -> list[dict]:
    """Poll until every run on ``sha`` is completed and, with ``branch``, at least one of them ran for that branch."""
    deadline = time.monotonic() + timeout_min * 60
    while True:
        runs = api(f"repos/{repo}/actions/runs?head_sha={sha}&per_page=20")["workflow_runs"]  # type: ignore[index]
        has_branch_run = branch is None or any(r["head_branch"] == branch for r in runs)
        if runs and has_branch_run and all(r["status"] == "completed" for r in runs):
            return runs
        if time.monotonic() > deadline:
            states = ", ".join(f"{r['name']} ({r['head_branch']}): {r['status']}" for r in runs) or "no runs yet"
            if not has_branch_run:
                states += f"; no run for branch {branch}"
            sys.exit(f"timed out after {timeout_min} min ({states})")
        time.sleep(POLL_SECONDS)


def report(repo: str, runs: list[dict]) -> bool:
    ok = True
    for run in runs:
        print(f"run {run['name']} ({run['event']}): {run['conclusion']}  {run['html_url']}")
        jobs = api(f"repos/{repo}/actions/runs/{run['id']}/jobs")["jobs"]  # type: ignore[index]
        for job in jobs:
            print(f"  job {job['name']}: {job['conclusion']}")
            if job["conclusion"] in ("success", "skipped"):
                continue
            ok = False
            for step in job["steps"]:
                if step["conclusion"] not in ("success", "skipped"):
                    print(f"    step {step['name']}: {step['conclusion']}")
            for note in api(f"repos/{repo}/check-runs/{job['id']}/annotations"):  # type: ignore[union-attr]
                print(f"    [{note['annotation_level']}] {note['message']}")
        ok = ok and run["conclusion"] == "success"
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("sha", help="full commit SHA, e.g. $(git rev-parse HEAD)")
    parser.add_argument("--branch", help="also wait until a run for this branch exists, e.g. main after pushing it")
    parser.add_argument("--timeout-min", type=int, default=30)
    args = parser.parse_args()
    repo = _repo_slug()
    target = f"{repo}@{args.sha[:7]}" + (f" on {args.branch}" if args.branch else "")
    print(f"waiting for CI on {target} ({'gh' if USE_GH else 'public API'})", flush=True)
    sys.exit(0 if report(repo, wait(repo, args.sha, args.timeout_min, args.branch)) else 1)


if __name__ == "__main__":
    main()
