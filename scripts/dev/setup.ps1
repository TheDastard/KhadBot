# scripts/dev/setup.ps1 — first-time setup for a KhadBot checkout on Windows.
#
# Installs what the project needs and wires it up, skipping anything already in place, so it is safe to re-run:
#   1. Tools via winget: Git (with Git LFS), uv 0.13 or later, mise, and optionally the GitHub CLI.
#   2. mise's shims folder on the user PATH, so node and pnpm resolve outside `mise run` too. No `mise trust` is
#      needed: the project's mise.toml uses nothing that requires it.
#   3. Git LFS hooks, so the recorded WCL fixtures download as real files.
#   4. Node and pnpm (`mise install`), the Python environment (`mise run install`) and the pre-commit hooks.
#      Every worktree shares the main clone's hooks, and `pre-commit install` writes the path of the Python that
#      ran it into them, so the hooks are installed from the main checkout only. A worktree just checks them;
#      installing from one would break the hooks for every checkout once that worktree is removed.
#   5. .env copied from .env.example if it doesn't exist yet. Fill in its keys yourself.
#
# Never touches an existing .env, never changes Python or .venv outside uv, and asks winget before installing
# anything (winget shows its own agreement prompts).
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev/setup.ps1 [-WithGitHubCli] [-SkipInstall]
#   -WithGitHubCli  also install the GitHub CLI (`gh`), which /land uses to watch CI when present
#   -SkipInstall    only check and configure; don't install tools or dependencies

param(
    [switch]$WithGitHubCli,
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

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

# The Python the shared pre-commit hook runs, if the hook exists and that Python is still there.
function Test-PreCommitHook($hooksDir) {
    $hook = Join-Path $hooksDir "pre-commit"
    if (-not (Test-Path $hook)) { return $null }
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
    } else {
        mise install
        # On a fresh machine the shims folder doesn't exist until mise has installed something, so the earlier
        # Add-SessionPath skipped it; add it now so node and pnpm resolve in this run.
        Add-SessionPath $shims
        if ($LASTEXITCODE -ne 0) { Problem "mise install failed" } else { Ok "Node $(node --version), pnpm $(pnpm --version)" }
        mise run install
        if ($LASTEXITCODE -ne 0) { Problem "mise run install failed" } else { Ok "$(uv run python --version) in .venv" }
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
