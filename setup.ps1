# KPI Bridge - Windows install (idempotent). Equivalent: launch.cmd (installs on first run).
# This script does not change the execution policy: if it blocks scripts, use launch.cmd.
param([switch]$NoAi, [switch]$NoOcr)
$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot
$py = $null
foreach ($cand in @(@('py', '-3'), @('python'))) {
    $exe = Get-Command $cand[0] -ErrorAction SilentlyContinue
    if ($exe) {
        $args2 = @($cand | Select-Object -Skip 1) + @('-c', 'import sys;sys.exit(0 if sys.version_info>=(3,12) else 1)')
        & $cand[0] @args2 2>$null
        if ($LASTEXITCODE -eq 0) { $py = $cand; break }
    }
}
if (-not $py) { Write-Error 'Python 3.12 or newer is required: https://www.python.org/downloads/'; exit 1 }
$extra = @()
if ($NoAi) { $extra += '--no-ai' }
if ($NoOcr) { $extra += '--no-ocr' }
$argv = @($py | Select-Object -Skip 1) + @('scripts\kpictl.py', 'setup') + $extra
& $py[0] @argv
exit $LASTEXITCODE
