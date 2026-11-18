# scripts/dev/setup.ps1 — first-time setup for a KhadBot checkout on Windows.
#
# Installs what the project needs and wires it up, skipping anything already in place, so it is safe to re-run:
#   1. Tools via winget: Git (with Git LFS), uv 0.13 or later, mise, and optionally the GitHub CLI and Docker Desktop.
#      Docker Desktop is only checked unless -WithDocker is given: the native dev loop (mise run dev:all) runs
#      Postgres and the bucket in it. The check names what is missing: Docker itself, WSL 2, or a stopped engine.
#      With -WithDocker a missing WSL 2 is installed too (`wsl --install --no-distribution`; it asks for
#      elevation and needs a reboot).
#   2. mise's shims folder on the user PATH, so node and pnpm resolve outside `mise run` too. No `mise trust` is
#      needed: the project's mise.toml uses nothing that requires it.
#   3. Git LFS hooks, so the recorded WCL fixtures download as real files.
#   4. Node and pnpm (`mise install`), the Python environment (`mise run install`), the TypeScript workspace's
#      packages (`pnpm install --frozen-lockfile`) and the pre-commit hooks. Playwright's browser for the web app's
#      end-to-end tests is a large download, so it comes only with -WithPlaywright.
#      Every worktree shares the main clone's hooks, and `pre-commit install` writes the path of the Python that
#      ran it into them, so the hooks are installed from the main checkout only. A worktree just checks them;
#      installing from one would break the hooks for every checkout once that worktree is removed.
#   5. .env copied from .env.example if it doesn't exist yet. Fill in its keys yourself. Likewise deploy/dev.env from
#      deploy/dev.env.example, the dev loop's throwaway settings (no secrets; created in a worktree too). Copies that
#      already exist are compared with their examples by name (scripts/dev/check_env.py), never by value.
#   6. The Postgres and SeaweedFS images the dev loop and the local stack run, pulled when Docker's engine is
#      running, so the first `mise run dev:all` doesn't stop to download them.
#
# Only developer machines run this. A hosted deployment needs none of it: it runs the image built from
# deploy/Dockerfile with the platform's Postgres, bucket and environment (docs/hosting/).
#
# Never touches an existing .env, never changes Python or .venv outside uv, and asks winget before installing
# anything (winget shows its own agreement prompts).
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev/setup.ps1 [-Full] [-WithGitHubCli] [-WithDocker] [-WithPlaywright] [-SkipInstall]
#   -Full           everything a full development machine needs: the same as -WithGitHubCli -WithDocker -WithPlaywright
#   -WithGitHubCli  also install the GitHub CLI (`gh`), which /land uses to watch CI when present
#   -WithDocker     also install Docker Desktop (winget Docker.DockerDesktop, a large download under Docker's
#                   subscription agreement). It may then ask for WSL 2, a reboot or Docker's own licence prompt;
#                   finish those, start Docker Desktop and rerun this script to check the engine.
#   -WithPlaywright also download Playwright's Chromium (about 150 MB) for `mise run test:e2e`
#   -SkipInstall    only check and configure; don't install tools or dependencies

param(
    [switch]$Full,
    [switch]$WithGitHubCli,
    [switch]$WithDocker,
    [switch]$WithPlaywright,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

if ($Full) {
    $WithGitHubCli = $true
    $WithDocker = $true
    $WithPlaywright = $true
}

# ── Banner ──────────────────────────────────────────────────────────────────
Write-Host @'
$$\   $$\ $$\                       $$\ $$$$$$$\             $$\
$$ | $$  |$$ |                      $$ |$$  __$$\            $$ |
$$ |$$  / $$$$$$$\   $$$$$$\   $$$$$$$ |$$ |  $$ | $$$$$$\ $$$$$$\
$$$$$  /  $$  __$$\  \____$$\ $$  __$$ |$$$$$$$\ |$$  __$$\\_$$  _|
$$  $$<   $$ |  $$ | $$$$$$$ |$$ /  $$ |$$  __$$\ $$ /  $$ | $$ |
$$ |\$$\  $$ |  $$ |$$  __$$ |$$ |  $$ |$$ |  $$ |$$ |  $$ | $$ |$$\
$$ | \$$\ $$ |  $$ |\$$$$$$$ |\$$$$$$$ |$$$$$$$  |\$$$$$$  | \$$$$  |
\__|  \__|\__|  \__| \_______| \_______|\_______/  \______/   \____/
'@ -ForegroundColor Cyan

# ── Helpers ─────────────────────────────────────────────────────────────────
$script:Problems = @()

function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "  ok    $text" -ForegroundColor Green }
function Did($text) { Write-Host "  done  $text" -ForegroundColor Yellow }
function Problem($text) { Write-Host "  !!    $text" -ForegroundColor Red; $script:Problems += $text }

function Has($command) { [bool](Get-Command $command -ErrorAction SilentlyContinue) }

# Make tools installed during this run visible to the rest of it; winget and mise only update the user PATH,
# which new shells pick up.
function Add-SessionPath($dir) {
    if ((Test-Path $dir) -and -not (($env:PATH -split ";") -contains $dir)) { $env:PATH = "$dir;$env:PATH" }
}

# The Python the shared pre-commit hooks run, if every hook .pre-commit-config.yaml installs exists and that Python
# is still there.
function Test-PreCommitHook($hooksDir) {
    $hook = Join-Path $hooksDir "pre-commit"
    if (-not (Test-Path $hook)) { return $null }
    foreach ($other in "commit-msg", "post-checkout", "post-merge") {
        if (-not (Test-Path (Join-Path $hooksDir $other))) { return $null }
    }
    $line = Select-String -Path $hook -Pattern "^INSTALL_PYTHON='(.*)'$" | Select-Object -First 1
    if (-not $line) { return $null }
    $python = $line.Matches[0].Groups[1].Value
    if (Test-Path $python) { return $python } else { return $null }
}

function Install-WingetPackage($id, $name) {
    if ($SkipInstall) { Problem "$name is missing (rerun without -SkipInstall, or: winget install --id $id -e)"; return }
    if (-not (Has winget)) { Problem "$name is missing and winget isn't available; install it by hand"; return }
    Write-Host "  installing $name with winget..."
    winget install --id $id -e
    if ($LASTEXITCODE -ne 0) { Problem "winget could not install $name (exit $LASTEXITCODE)"; return }
    Add-SessionPath "$env:LOCALAPPDATA\Microsoft\WinGet\Links"
    Add-SessionPath "$env:USERPROFILE\.local\bin"
    Add-SessionPath "$env:ProgramFiles\Git\cmd"
    Add-SessionPath "$env:ProgramFiles\GitHub CLI"
    Add-SessionPath "$env:ProgramFiles\Docker\Docker\resources\bin"
    Did "installed $name"
}

Add-SessionPath "$env:LOCALAPPDATA\Microsoft\WinGet\Links"
Add-SessionPath "$env:USERPROFILE\.local\bin"

# ── 1. Tools ────────────────────────────────────────────────────────────────
Step "Tools"

if (Has git) { Ok (git --version) } else { Install-WingetPackage "Git.Git" "Git" }

if (Has git-lfs) { Ok (git lfs version) } else { Problem "Git LFS is missing; Git for Windows includes it, so reinstall Git with it enabled" }

if (Has uv) {
    $uvVersion = [version](((uv --version) -split " ")[1])
    if ($uvVersion -ge [version]"0.13.0") { Ok "uv $uvVersion" }
    elseif ($SkipInstall) { Problem "uv $uvVersion is older than 0.13 (run: uv self update)" }
    else { uv self update; Did "updated uv" }
} else { Install-WingetPackage "astral-sh.uv" "uv" }

if (Has mise) { Ok "mise $(((mise version --quiet) -split ' ')[0])" } else { Install-WingetPackage "jdx.mise" "mise" }

if ($WithGitHubCli) {
    if (Has gh) { Ok ((gh --version) | Select-Object -First 1) } else { Install-WingetPackage "GitHub.cli" "GitHub CLI" }
}

# Docker Desktop runs Postgres and the bucket for the dev loop. Local mode (the CLI) does not need it.
function Test-WslReady {
    if (-not (Has wsl)) { return $false }
    wsl --status *> $null
    return ($LASTEXITCODE -eq 0)
}

$script:NeedsReboot = $false
if ($WithDocker -and -not $SkipInstall -and -not (Test-WslReady)) {
    # Docker Desktop's engine runs on WSL 2. --no-distribution installs only the platform, not a Linux distribution.
    Write-Host "  installing WSL 2 (Windows asks for elevation)..."
    Start-Process -FilePath "wsl.exe" -ArgumentList "--install", "--no-distribution" -Verb RunAs -Wait
    $script:NeedsReboot = $true
    Did "asked Windows to install WSL 2; reboot before starting Docker Desktop"
}
if ($WithDocker -and -not (Has docker)) {
    Install-WingetPackage "Docker.DockerDesktop" "Docker Desktop"
    if (Has docker) {
        Write-Host "  Docker Desktop shows its licence prompt on first start; start it and rerun this script to check the engine." -ForegroundColor Yellow
    }
}
$script:EngineRunning = $false
if (Has docker) {
    docker info *> $null
    if ($LASTEXITCODE -eq 0) {
        $script:EngineRunning = $true
        Ok "Docker engine running ($(docker version --format '{{.Server.Version}}'))"
    }
    elseif ($script:NeedsReboot) { Problem "Docker is installed but WSL 2 needs a reboot (reboot, start Docker Desktop, then rerun this script)" }
    elseif (-not (Test-WslReady)) { Problem "Docker is installed but WSL 2 isn't ready (rerun with -WithDocker, or run: wsl --install --no-distribution, then reboot)" }
    else { Problem "Docker's engine isn't running (start Docker Desktop and wait until it says it is running)" }
} else {
    Problem "Docker Desktop is not installed; the dev loop needs it (rerun with -WithDocker or -Full, or install Docker.DockerDesktop)"
}

# The rest needs git and mise.
if (-not ((Has git) -and (Has mise))) {
    Write-Host ""
    Write-Host "Stopping: git and mise are required for the remaining steps. Open a new shell and rerun this script." -ForegroundColor Red
    exit 1
}

# ── 2. mise shims ───────────────────────────────────────────────────────────
Step "mise shims"

$shims = "$env:LOCALAPPDATA\mise\shims"
$userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if (($userPath -split ";") -contains $shims) {
    Ok "mise shims are on the user PATH"
} else {
    $newPath = if ([string]::IsNullOrEmpty($userPath)) { $shims } else { "$($userPath.TrimEnd(';'));$shims" }
    [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
    Did "added $shims to the user PATH (new shells and editors pick it up)"
}
Add-SessionPath $shims

# ── 3. Git LFS ──────────────────────────────────────────────────────────────
Step "Git LFS"

# The main clone, even when this runs from a worktree: worktrees share its LFS objects and its .env.
$commonDir = git -C $PSScriptRoot rev-parse --path-format=absolute --git-common-dir
$cloneRoot = (Split-Path -Parent $commonDir) -replace "\\", "/"
$inWorktree = (git -C $PSScriptRoot rev-parse --show-toplevel) -ne $cloneRoot

git lfs install | Out-Null
Ok "Git LFS hooks installed"
$meta = Join-Path $cloneRoot "tests/fixtures/encounter/wcl/H7z1fqDXjRTFnbtg/report.json"
if ((Test-Path $meta) -and ((Get-Content $meta -TotalCount 1) -like "version https://git-lfs*")) {
    if ($SkipInstall) { Problem "recorded fixtures are LFS pointer files (run: git lfs pull)" }
    else { git -C $cloneRoot lfs pull; Did "downloaded the recorded fixtures (about 200 MB)" }
} else {
    Ok "recorded fixtures are present"
}

# ── 4. Project dependencies ─────────────────────────────────────────────────
Step "Project dependencies"

Push-Location (git -C $PSScriptRoot rev-parse --show-toplevel)
try {
    if ($SkipInstall) {
        if (Test-Path .venv) { Ok ".venv exists" } else { Problem ".venv is missing (run: mise run install)" }
        if (Test-Path node_modules) { Ok "the TypeScript workspace's packages are installed" }
        else { Problem "the TypeScript workspace's packages are missing (run: mise run install:web)" }
    } else {
        mise install
        # On a fresh machine the shims folder doesn't exist until mise has installed something, so the earlier
        # Add-SessionPath skipped it; add it now so node and pnpm resolve in this run.
        Add-SessionPath $shims
        if ($LASTEXITCODE -ne 0) { Problem "mise install failed" } else { Ok "Node $(node --version), pnpm $(pnpm --version)" }
        mise run install
        if ($LASTEXITCODE -ne 0) { Problem "mise run install failed" } else { Ok "$(uv run python --version) in .venv" }
        pnpm install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) { Problem "pnpm install failed" } else { Ok "the TypeScript workspace's packages are installed" }
        if ($WithPlaywright) {
            mise run install:playwright
            if ($LASTEXITCODE -ne 0) { Problem "Playwright's browser download failed" } else { Ok "Playwright's Chromium is installed" }
        } else {
            Write-Host "  note  the web app's end-to-end tests need Playwright's Chromium: rerun with -WithPlaywright, or run: mise run install:playwright"
        }
    }

    $hookPython = Test-PreCommitHook (Join-Path $commonDir "hooks")
    if ($inWorktree -or $SkipInstall) {
        if ($hookPython) { Ok "pre-commit hooks installed (run with $hookPython)" }
        else { Problem "pre-commit hooks are missing or point at a removed .venv (run in $cloneRoot`: uv run pre-commit install)" }
    } else {
        uv run pre-commit install | Out-Null
        if ($LASTEXITCODE -ne 0) { Problem "pre-commit install failed" } else { Ok "pre-commit hooks installed" }
    }

    # ── 5. .env ─────────────────────────────────────────────────────────────
    Step ".env"
    if (Test-Path .env) {
        Ok ".env exists (left untouched)"
    } elseif ($inWorktree) {
        Ok "worktree: no .env needed; load_dotenv finds the main checkout's"
    } else {
        Copy-Item .env.example .env
        Did "created .env from .env.example; set LLM_PROVIDER, its API key and WARCRAFTLOGS_CLIENT_ID/SECRET"
    }

    # The dev loop's settings are throwaway local values, so unlike .env they are copied in a worktree as well.
    if (Test-Path deploy/dev.env) {
        Ok "deploy/dev.env exists (left untouched)"
    } else {
        Copy-Item deploy/dev.env.example deploy/dev.env
        Did "created deploy/dev.env from deploy/dev.env.example (the dev loop's settings; WCL credentials stay in .env)"
    }

    # Existing copies keep their values, so report the names their examples gained or lost since (names only).
    $envReport = uv run python scripts/dev/check_env.py --strict 2>&1
    if ($LASTEXITCODE -eq 0) {
        Ok ".env and deploy/dev.env list every name their examples do"
    } else {
        $envReport | ForEach-Object { Write-Host "  note  $_" }
        Write-Host "  note  mise run env:fix appends the missing names, commented out" -ForegroundColor Yellow
    }

    # ── 6. Docker images ────────────────────────────────────────────────────
    Step "Docker images"
    if (-not $script:EngineRunning) {
        Write-Host "  note  skipped: Docker's engine isn't running (the first mise run dev:all pulls them instead)"
    } elseif ($SkipInstall) {
        Ok "skipped pulling the dev loop's images (-SkipInstall)"
    } else {
        docker compose -f deploy/compose.yaml pull postgres seaweedfs
        if ($LASTEXITCODE -ne 0) { Problem "docker compose could not pull the Postgres and SeaweedFS images" }
        else { Ok "Postgres and SeaweedFS images are pulled" }
    }
} finally {
    Pop-Location
}

# ── Summary ─────────────────────────────────────────────────────────────────
Write-Host ""
if ($script:Problems.Count -eq 0) {
    Write-Host "Setup complete. Open a new shell so PATH changes apply, then run: mise run check" -ForegroundColor Green
    exit 0
}
Write-Host "Setup finished with $($script:Problems.Count) problem(s):" -ForegroundColor Red
$script:Problems | ForEach-Object { Write-Host "  - $_" }
exit 1
