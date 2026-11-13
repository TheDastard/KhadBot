# scripts/dev/clean.ps1 — remove caches and build output, and optionally the installed dependencies.
#
# With no switches it removes what the next command rebuilds by itself: Python caches and bytecode, coverage output,
# and the web app's build and test output (.next, *.tsbuildinfo, Playwright reports and test results).
#
# -Venv also deletes the Python virtual environment. Deleting .venv on Windows commonly fails because something
# launched from it still holds a file open (ruff.exe or python.exe from editor extensions, a pre-commit hook, a stray
# `uv run`), so the script stops every process running from inside it, retries the delete, and names the files still
# locked if it gives up. -All also deletes the TypeScript workspace's node_modules and .khadbot_fixture_data.
#
# Never touches .env, deploy/dev.env or .khadbot_data/ (KHADBOT_DATA_DIR holds fetched WCL data and findings).
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev/clean.ps1 [-Venv] [-All] [-Root <checkout>]
#   -Venv      also delete .venv (`mise run clean:all`); reinstall with `mise run install`
#   -All       also delete .venv, every node_modules and .khadbot_fixture_data
#   -Root      the checkout to clean (default: the one this script is in), e.g. a worktree before
#              `git worktree remove`: `-Root .claude/worktrees/<slug> -Venv`
#   -Attempts  how many times to try deleting a locked directory (default 4)

param(
    [switch]$Venv,
    [switch]$All,
    [string]$Root = "",
    [int]$Attempts = 4
)

$ErrorActionPreference = "Stop"

if ($All) { $Venv = $true }
if ([string]::IsNullOrEmpty($Root)) { $Root = Join-Path $PSScriptRoot "../.." }
if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
    Write-Host "No checkout at $Root."
    exit 1
}
$Root = (Resolve-Path -LiteralPath $Root).Path

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

# Folders never searched for stray output, at any depth: dependencies, data, git and worktrees (.claude/worktrees).
$skipNames = @(".venv", "node_modules", ".khadbot_data", ".khadbot_fixture_data", ".git", ".claude")

# Every file and folder under $dir, without descending into the skipped folders (walking .venv or node_modules
# would take far longer than the clean itself).
function Get-Tree($dir) {
    foreach ($item in Get-ChildItem -LiteralPath $dir -Force -ErrorAction SilentlyContinue) {
        if ($item.PSIsContainer -and $skipNames -contains $item.Name) { continue }
        $item
        if ($item.PSIsContainer -and $item.Name -ne "__pycache__") { Get-Tree $item.FullName }
    }
}

function Remove-IfPresent($path) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Recurse -Force
        Write-Host "Removed $path"
    }
}

# Stop every process whose executable lives inside $dir (excluding this shell).
function Stop-ProcessesIn($dir) {
    $prefix = $dir.TrimEnd('\') + '\'
    Get-Process | Where-Object {
        $_.Id -ne $PID -and $_.Path -and $_.Path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
    } | ForEach-Object {
        Write-Host "Stopping $($_.ProcessName) (pid $($_.Id)), running from $dir"
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
}

# Delete a directory that a running process may hold open: stop those processes, retry, then name what is left.
function Remove-Locked($dir) {
    if (-not (Test-Path -LiteralPath $dir)) { return $true }
    for ($i = 1; $i -le $Attempts; $i++) {
        Stop-ProcessesIn $dir
        try {
            Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction Stop
            Write-Host "Removed $dir"
            return $true
        } catch {
            Write-Host "Attempt $i/$Attempts to remove $dir failed: $($_.Exception.Message)"
            Start-Sleep -Milliseconds (500 * $i)
        }
    }
    Remove-Item -LiteralPath $dir -Recurse -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path -LiteralPath $dir)) { Write-Host "Removed $dir"; return $true }
    Write-Host ""
    Write-Host "Could not fully remove $dir. Remaining files:"
    Get-ChildItem -LiteralPath $dir -Recurse -File -Force -ErrorAction SilentlyContinue |
        Select-Object -First 10 -ExpandProperty FullName | ForEach-Object { Write-Host "  $_" }
    Write-Host "Close editors, terminals and dev servers using it (editor Python and Ruff extensions are common culprits) and re-run."
    return $false
}

# ----------------------------------------------------------------------------
# Caches and build output
# ----------------------------------------------------------------------------

$dirs = @(
    "src/khadbot.egg-info",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",
    "htmlcov",
    "apps/web/.next",
    "apps/web/playwright-report",
    "apps/web/playwright-report-local",
    "apps/web/test-results"
)
foreach ($d in $dirs) { Remove-IfPresent (Join-Path $Root $d) }
foreach ($f in @(".coverage", "coverage.xml")) { Remove-IfPresent (Join-Path $Root $f) }

# Stray bytecode, __pycache__ folders, coverage shards and TypeScript build info anywhere outside the skipped folders.
Get-Tree $Root |
    Where-Object {
        ($_.PSIsContainer -and $_.Name -eq "__pycache__") -or
        (-not $_.PSIsContainer -and ($_.Extension -in ".pyc", ".pyo", ".tsbuildinfo" -or $_.Name -like ".coverage.*"))
    } |
    ForEach-Object { if (Test-Path -LiteralPath $_.FullName) { Remove-Item -LiteralPath $_.FullName -Recurse -Force } }

Write-Host "Cleaned caches and build output in $Root."

# ----------------------------------------------------------------------------
# Dependencies and generated data
# ----------------------------------------------------------------------------

$ok = $true
if ($Venv) {
    $ok = (Remove-Locked (Join-Path $Root ".venv")) -and $ok
}
if ($All) {
    $modules = @(Join-Path $Root "node_modules")
    foreach ($group in @("apps", "packages")) {
        $groupDir = Join-Path $Root $group
        if (Test-Path -LiteralPath $groupDir) {
            Get-ChildItem -LiteralPath $groupDir -Directory | ForEach-Object { $modules += Join-Path $_.FullName "node_modules" }
        }
    }
    foreach ($m in $modules) { $ok = (Remove-Locked $m) -and $ok }
    Remove-IfPresent (Join-Path $Root ".khadbot_fixture_data")
}

if (-not $ok) { exit 1 }
