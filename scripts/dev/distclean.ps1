# scripts/dev/distclean.ps1 — remove the project virtual environment robustly on Windows.
#
# Deleting .venv commonly fails because something launched from it still holds
# a file open (ruff.exe / python.exe from the VSCode extensions, a pre-commit
# hook, a stray `uv run`). This script stops those processes, retries the
# delete, and reports exactly which files are still locked if it gives up.
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev/distclean.ps1 [-Venv .venv]

param(
    [string]$Venv = ".venv",
    [int]$Attempts = 4
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $Venv)) {
    Write-Host "No $Venv to remove."
    exit 0
}

$venvPath = (Resolve-Path -LiteralPath $Venv).Path

function Stop-VenvProcesses {
    # Any process whose executable lives inside the venv (excluding this shell).
    $prefix = $venvPath.TrimEnd('\') + '\'
    $procs = Get-Process | Where-Object {
        $_.Id -ne $PID -and $_.Path -and $_.Path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
    }
    foreach ($p in $procs) {
        Write-Host "Stopping $($p.ProcessName) (pid $($p.Id)) - running from $Venv"
        Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
    }
    return @($procs).Count
}

for ($i = 1; $i -le $Attempts; $i++) {
    Stop-VenvProcesses | Out-Null
    try {
        Remove-Item -LiteralPath $venvPath -Recurse -Force -ErrorAction Stop
        Write-Host "Removed $Venv."
        exit 0
    } catch {
        Write-Host "Attempt $i/$Attempts failed: $($_.Exception.Message)"
        Start-Sleep -Milliseconds (500 * $i)
    }
}

# Last resort: delete what we can, then name whatever is left.
Remove-Item -LiteralPath $venvPath -Recurse -Force -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $venvPath) {
    Write-Host ""
    Write-Host "Could not fully remove $Venv. Remaining files:"
    Get-ChildItem -LiteralPath $venvPath -Recurse -File -Force -ErrorAction SilentlyContinue |
        Select-Object -First 10 -ExpandProperty FullName | ForEach-Object { Write-Host "  $_" }
    Write-Host "Close editors/terminals using the venv (VSCode Python/Ruff extensions are common culprits) and re-run."
    exit 1
}
Write-Host "Removed $Venv."
