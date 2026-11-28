"""
scripts/dev/dev.py

The native dev loop on the local stack: Postgres and SeaweedFS run in Docker (deploy/compose.yaml), the API and the
worker run on this machine in hosted mode and restart when a Python or config file changes.

    uv run python scripts/dev/dev.py infra          # start Postgres and SeaweedFS, create the bucket, migrate
    uv run python scripts/dev/dev.py api [args]     # the hosted API with --reload (default: port 8000)
    uv run python scripts/dev/dev.py worker [args]  # the job worker with --reload
    uv run python scripts/dev/dev.py seed [args]    # load the recorded fixture reports (--only CODE ...)
    uv run python scripts/dev/dev.py smoke          # health, a night summary and a queued job against the loop
    uv run python scripts/dev/dev.py web            # the web dev server on :3000, pointed at the dev loop's API
    uv run python scripts/dev/dev.py all [--no-web] # infra, then the API, the worker and the web dev server
    uv run python scripts/dev/dev.py session [args] # a test user's session cookie (main.py test-session)

``mise run dev:infra``, ``dev:api``, ``dev:worker``, ``dev:web``, ``dev:seed``, ``dev:smoke``, ``dev:all`` and
``dev:session`` run these. Settings
come from deploy/dev.env (copied from deploy/dev.env.example by scripts/dev/setup.ps1) on top of the main checkout's
.env, which supplies the WCL credentials. See docs/hosting/local.md, "Local development".

The API and the worker log JSON (``KHADBOT_LOG_FORMAT=json``). Each process's lines go to
``.khadbot_dev_logs/<service>.jsonl`` at this checkout's root, emptied at each start, one JSON object per line (output
that isn't a KhadBot JSON line, such as the web dev server's, is wrapped into one), and to the terminal as compact
prefixed text.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

REPO = Path(__file__).resolve().parents[2]
COMPOSE = ["docker", "compose", "-f", str(REPO / "deploy" / "compose.yaml")]
DEV_ENV = REPO / "deploy" / "dev.env"
DEV_ENV_EXAMPLE = REPO / "deploy" / "dev.env.example"
API_HOST = "127.0.0.1"
API_PORT = 8000
WEB_PORT = 3000
WEB_DIR = REPO / "apps" / "web"
DEV_LOGS = REPO / ".khadbot_dev_logs"
PLACEHOLDER_WCL = {
    "WARCRAFTLOGS_CLIENT_ID": "local-dev-client-id",
    "WARCRAFTLOGS_CLIENT_SECRET": "local-dev-client-secret",
}


def fail(message: str) -> None:
    """Exit with ``message`` on stderr."""
    sys.exit(f"dev: {message}")


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def dev_environment() -> dict[str, str]:
    """The environment for the native processes: the process environment, ``.env``, then deploy/dev.env on top.

    ``.env`` is found the way the app finds it (walking up from here, so a worktree reaches the main checkout's). The
    dev file overrides it for the names it sets. Without WCL credentials anywhere, placeholders let the API start; they
    cannot sign anyone in.
    """
    from dotenv import dotenv_values, find_dotenv

    if not DEV_ENV.exists():
        fail(f"{DEV_ENV} is missing: copy {DEV_ENV_EXAMPLE.name} to {DEV_ENV.name} (scripts/dev/setup.ps1 does).")
    env = dict(os.environ)
    dotenv_path = find_dotenv(usecwd=False)
    shared = dotenv_values(dotenv_path) if dotenv_path else {}
    for name, value in shared.items():
        if value is not None and name not in env:
            env[name] = value
    for name, value in dotenv_values(DEV_ENV).items():
        if value is not None:
            env[name] = value
    if not env.get("WARCRAFTLOGS_CLIENT_ID") or not env.get("WARCRAFTLOGS_CLIENT_SECRET"):
        env.update(PLACEHOLDER_WCL)
        print("dev: no WCL credentials in .env; using placeholders (everything but WCL sign-in works)", flush=True)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run(command: list[str], env: dict[str, str] | None = None) -> None:
    """Run ``command`` from the repository root and stop the script when it fails."""
    result = subprocess.run(command, cwd=REPO, env=env)
    if result.returncode != 0:
        sys.exit(result.returncode)


def python_script(script: str, *args: str) -> list[str]:
    """A command that runs ``script`` (relative to the repository root) with this interpreter."""
    return [sys.executable, str(REPO / script), *args]


# ---------------------------------------------------------------------------
# Infrastructure
# ---------------------------------------------------------------------------


def engine_running() -> bool:
    """True when the Docker engine answers."""
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def create_bucket(env: dict[str, str], timeout: float = 120) -> None:
    """Create the bucket named by KHADBOT_S3_BUCKET once the S3 gateway answers; an existing bucket is fine."""
    os.environ.update(env)  # boto3 reads the AWS_* names from the process environment
    from khadbot.storage.s3 import make_client

    client = make_client(endpoint_url=env["KHADBOT_S3_ENDPOINT"], region=env["KHADBOT_S3_REGION"])
    bucket = env["KHADBOT_S3_BUCKET"]
    deadline = time.monotonic() + timeout
    while True:
        try:
            client.create_bucket(Bucket=bucket)
            break
        except Exception as exc:  # noqa: BLE001 - botocore raises many types while the gateway starts
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                break
            if time.monotonic() > deadline:
                raise
            time.sleep(1)
    print(f"dev: bucket ready: {bucket}", flush=True)


def infra(env: dict[str, str]) -> None:
    """Start Postgres and SeaweedFS (waiting for Postgres' health check), create the bucket and apply migrations."""
    if not engine_running():
        fail(
            "the Docker engine is not running (or Docker is not installed): start Docker Desktop, then run this again."
        )
    started = time.monotonic()
    run([*COMPOSE, "up", "--detach", "--wait", "postgres", "seaweedfs"])
    create_bucket(env)
    run(python_script("main.py", "migrate"), env)
    print(f"dev: infrastructure ready in {time.monotonic() - started:.1f}s", flush=True)


# ---------------------------------------------------------------------------
# Processes
# ---------------------------------------------------------------------------


def port_in_use(port: int) -> bool:
    """True when something listens on 127.0.0.1:``port``."""
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((API_HOST, port)) == 0


def require_free_port(port: int, what: str = "the API") -> None:
    if not port_in_use(port):
        return
    if port == API_PORT:
        fail(
            f"port {port} is in use. `mise run stack:up` and the dev loop both want 8000: `mise run stack:down` first."
        )
    fail(f"port {port} is in use; {what} needs it. Stop whatever listens there first.")


def api_port(args: list[str]) -> int:
    """The port an ``api`` argument list asks for."""
    for i, arg in enumerate(args):
        if arg == "--port" and i + 1 < len(args):
            return int(args[i + 1])
        if arg.startswith("--port="):
            return int(arg.split("=", 1)[1])
    return API_PORT


def khadbot_command(role: str, args: list[str]) -> list[str]:
    """``main.py <role> --reload`` plus the caller's arguments (the API binds 127.0.0.1 only)."""
    extra = ["--host", API_HOST] if role == "api" and "--host" not in args else []
    return python_script("main.py", role, "--reload", *extra, *args)


def khadbot_environment(env: dict[str, str]) -> dict[str, str]:
    """The API's and the worker's environment: JSON log lines, which the log files keep and the terminal renders."""
    return {**env, "KHADBOT_LOG_FORMAT": "json"}


# ---------------------------------------------------------------------------
# Log lines
# ---------------------------------------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")  # colour and cursor codes
_RENDERED = ("time", "level", "logger", "message", "service", "version", "env", "exc")


def _log_entry(line: str) -> dict[str, object] | None:
    """``line`` as a KhadBot JSON log entry, or None when it is other output."""
    if not line.startswith("{"):
        return None
    try:
        entry = json.loads(line)
    except ValueError:
        return None
    return entry if isinstance(entry, dict) and "message" in entry and "level" in entry else None


def json_line(line: str, service: str) -> str:
    """``line`` as one JSON object for the log file: a KhadBot JSON line as it is, other output wrapped and redacted."""
    if _log_entry(line) is not None:
        return line
    from khadbot.logging_setup import redact

    entry = {
        "time": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "level": "INFO",
        "logger": "output",
        "message": redact(_ANSI.sub("", line)),
        "service": service,
    }
    return json.dumps(entry, ensure_ascii=False)


def render_line(line: str) -> str:
    """``line`` for the terminal: a KhadBot JSON line as ``HH:MM:SS LEVEL logger: message key=value``, else as it is.

    The process fields (service, version, env) are left out; the context IDs and event fields follow the message, and
    an exception's traceback follows on its own lines.
    """
    entry = _log_entry(line)
    if entry is None:
        return line
    clock = str(entry.get("time", ""))[11:19]
    text = f"{clock} {entry['level']} {entry.get('logger', '')}: {entry['message']}".lstrip()
    fields = " ".join(f"{key}={value}" for key, value in entry.items() if key not in _RENDERED)
    text = f"{text} {fields}" if fields else text
    return f"{text}\n{entry['exc']}" if entry.get("exc") else text


def open_log(service: str, directory: Path | None = None) -> IO[str]:
    """``<directory>/<service>.jsonl`` (``.khadbot_dev_logs/`` by default), emptied, for one process's lines."""
    folder = directory or DEV_LOGS
    folder.mkdir(parents=True, exist_ok=True)
    return (folder / f"{service}.jsonl").open("w", encoding="utf-8", buffering=1)


def pump(stream: IO[str], label: str, log: IO[str] | None = None) -> None:
    """Copy a process's output to the terminal with ``[label]`` prefixes and, when given, to ``log`` as JSON lines."""
    try:
        for raw in stream:
            line = raw.rstrip()
            for part in render_line(line).splitlines() or [""]:
                print(f"[{label}] {part}", flush=True)
            if log is not None and line.strip():
                log.write(json_line(line, label) + "\n")
    finally:
        if log is not None:
            log.close()


# ---------------------------------------------------------------------------
# Process control
# ---------------------------------------------------------------------------


def signal_tree(proc: subprocess.Popen[str]) -> None:
    """Ask ``proc`` and everything it started (the reloader runs the server as a child) to stop, without waiting."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        import signal

        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def wait_tree(proc: subprocess.Popen[str]) -> None:
    """Wait for ``proc`` to exit after ``signal_tree``, killing it after 10 seconds."""
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def stop_tree(proc: subprocess.Popen[str]) -> None:
    """Stop ``proc`` and everything it started, and wait for it."""
    signal_tree(proc)
    wait_tree(proc)


def ignore_stop_requests() -> None:
    """Ignore Ctrl-C and terminate requests from now on: the loop is already stopping its children."""
    import signal

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), signal.SIG_IGN)


def stop_all(procs: dict[str, subprocess.Popen[str]]) -> None:
    """
    Stop every process, ignoring further stop requests while it does. Every tree is signalled before any is waited
    for, so a slow API or worker never delays the web dev server's signal: a second terminate (CI's ``pkill`` reaches
    both ``uv run`` and this script, and ``uv`` forwards it) used to interrupt the waits and leave later children,
    and the port they hold, running.
    """
    ignore_stop_requests()
    for proc in procs.values():
        signal_tree(proc)
    for proc in procs.values():
        wait_tree(proc)


def spawn(command: list[str], env: dict[str, str], label: str | None) -> subprocess.Popen[str]:
    """Start ``command`` in its own process group.

    With a ``label``, its output is prefixed with it in the terminal and written to ``.khadbot_dev_logs/<label>.jsonl``;
    without one, it goes straight to the terminal.
    """
    kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # Ctrl-C reaches this script only
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        command,
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE if label else None,
        stderr=subprocess.STDOUT if label else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        **kwargs,  # type: ignore[arg-type]
    )
    if label:
        assert proc.stdout is not None
        threading.Thread(target=pump, args=(proc.stdout, label, open_log(label)), daemon=True).start()
    return proc


def supervise(procs: dict[str, subprocess.Popen[str]]) -> int:
    """Wait until Ctrl-C or until any process exits; then stop them all. Returns the exit status."""
    status = 0
    try:
        while True:
            for name, proc in procs.items():
                code = proc.poll()
                if code is not None:
                    print(f"dev: {name} exited with status {code}; stopping the rest", flush=True)
                    status = code or 1
                    return status
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\ndev: stopping", flush=True)
        return 0
    finally:
        stop_all(procs)


def wait_for_health(port: int, procs: dict[str, subprocess.Popen[str]], timeout: float = 120) -> float | None:
    """Poll /v1/health until it answers; returns the seconds it took, or None when a process died or time ran out."""
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        if any(p.poll() is not None for p in procs.values()):
            return None
        try:
            with urllib.request.urlopen(f"http://{API_HOST}:{port}/v1/health", timeout=2):
                return time.monotonic() - started
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.25)
    return None


def web_command() -> list[str] | None:
    """``pnpm --filter @khadbot/web dev``, or None when pnpm or the workspace's packages are missing."""
    pnpm = shutil.which("pnpm")
    if pnpm is None or not (WEB_DIR / "node_modules").is_dir():
        return None
    return [pnpm, "--filter", "@khadbot/web", "dev"]


def web_environment(env: dict[str, str], port: int) -> dict[str, str]:
    """The web dev server's environment: the API on ``localhost``, the host the session cookie is set for."""
    return {**env, "NEXT_PUBLIC_API_BASE": f"http://localhost:{port}"}


def run_web(env: dict[str, str]) -> None:
    """Run the web dev server in the foreground (Next reloads on its own)."""
    command = web_command()
    if command is None:
        fail("pnpm or the web packages are missing: run `pnpm install` at the repository root (setup.ps1 does).")
        return
    require_free_port(WEB_PORT, "the web dev server")
    proc = spawn(command, web_environment(env, API_PORT), label=None)
    sys.exit(supervise({"web": proc}))


def run_one(role: str, args: list[str], env: dict[str, str]) -> None:
    """Run the API or the worker in the foreground, with reload."""
    if role == "api":
        require_free_port(api_port(args))
    proc = spawn(khadbot_command(role, args), khadbot_environment(env), label=role)
    sys.exit(supervise({role: proc}))


def run_all(args: list[str], env: dict[str, str]) -> None:
    """Start the infrastructure, then the API, the worker and (unless ``--no-web``) the web dev server.

    Ctrl-C stops them all. Without pnpm or the workspace's packages the web dev server is left out, with a note.
    """
    with_web = "--no-web" not in args
    args = [a for a in args if a != "--no-web"]
    port = api_port(args)
    require_free_port(port)
    web = web_command() if with_web else None
    if with_web and web is None:
        print("dev: no web dev server (pnpm or apps/web/node_modules is missing; run `pnpm install`)", flush=True)
    if web is not None:
        require_free_port(WEB_PORT, "the web dev server")
    started = time.monotonic()
    infra(env)
    procs = {
        "api": spawn(khadbot_command("api", args), khadbot_environment(env), label="api"),
        "worker": spawn(khadbot_command("worker", []), khadbot_environment(env), label="worker"),
    }
    if web is not None:
        procs["web"] = spawn(web, web_environment(env, port), label="web")
    try:
        took = wait_for_health(port, procs)
    except KeyboardInterrupt:
        took = None
    if took is None:
        print("dev: the API did not come up", flush=True)
        supervise_status = 1
        stop_all(procs)
        sys.exit(supervise_status)
    print(
        f"dev: ready at http://{API_HOST}:{port} (API healthy {took:.1f}s after launch, "
        f"{time.monotonic() - started:.1f}s from the command)"
        + (f"; the web app at http://localhost:{WEB_PORT}" if "web" in procs else "")
        + "; Ctrl-C stops them",
        flush=True,
    )
    sys.exit(supervise(procs))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def stop_on_terminate() -> None:
    """
    Treat a terminate request (Ctrl-Break on Windows, SIGTERM elsewhere) like Ctrl-C, so children are stopped. The
    first request of either kind turns further ones off before it raises: the second arrives milliseconds later (CI's
    ``pkill`` reaches both ``uv run`` and this script, and ``uv`` forwards it), before ``stop_all`` could ignore it.
    """
    import signal

    def handler(signum: int, frame: object) -> None:
        ignore_stop_requests()
        raise KeyboardInterrupt

    signal.signal(getattr(signal, "SIGBREAK", signal.SIGTERM), handler)
    signal.signal(signal.SIGINT, handler)


def main() -> None:
    stop_on_terminate()
    # The children's output is UTF-8 (Next prints symbols); a Windows console may not be, and a failed print would
    # stop the thread that drains a child's pipe, which then blocks the child.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("command", choices=["infra", "api", "worker", "web", "seed", "smoke", "all", "session"])
    parser.add_argument("args", nargs=argparse.REMAINDER, help="passed to the command (for example --only SYNTH01)")
    parsed = parser.parse_args()
    args = [a for a in parsed.args if a != "--"]
    env = dev_environment()

    if parsed.command == "infra":
        infra(env)
    elif parsed.command == "seed":
        run(python_script("scripts/data/seed_stack.py", *args), env)
    elif parsed.command == "smoke":
        run(python_script("scripts/dev/stack_smoke.py", "--native-session", *args), env)
        run(python_script("scripts/dev/queue_smoke.py"), env)
    elif parsed.command == "session":
        run(python_script("main.py", "test-session", *args), env)
    elif parsed.command == "all":
        run_all(args, env)
    elif parsed.command == "web":
        run_web(env)
    else:
        run_one(parsed.command, args, env)


if __name__ == "__main__":
    main()
