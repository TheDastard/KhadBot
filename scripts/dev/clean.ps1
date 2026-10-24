# scripts/dev/clean.ps1 — remove caches and build output.
#
# Never touches .venv, .env or .khadbot_data/ (scripts/dev/distclean.ps1 removes the venv).
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev/clean.ps1

$ErrorActionPreference = "Stop"

$dirs = @(
    "src/khadbot.egg-info",
    "src/__pycache__",
    "tests/__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "htmlcov"
)
foreach ($d in $dirs) {
    if (Test-Path -LiteralPath $d) {
        Remove-Item -LiteralPath $d -Recurse -Force
    }
}

# Stray bytecode anywhere in the project, except the protected directories.
$protected = @(".venv", ".env", ".khadbot_data", ".git") | ForEach-Object { Join-Path (Get-Location).Path $_ }
Get-ChildItem -Path . -Recurse -File -Include *.pyc, *.pyo -Force -ErrorAction SilentlyContinue |
    Where-Object {
        $path = $_.FullName
        -not ($protected | Where-Object { $path.StartsWith($_ + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) })
    } |
    Remove-Item -Force

Write-Host "Cleaned."
