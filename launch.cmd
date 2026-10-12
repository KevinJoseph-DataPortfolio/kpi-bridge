@echo off
rem KPI Bridge - Windows launcher (installs on first run, then reuses the installation).
rem Runs no PowerShell script and changes no system policy.
setlocal
cd /d "%~dp0"
set "PYEXE="
where py >nul 2>nul && py -3 -c "import sys;sys.exit(0 if sys.version_info>=(3,12) else 1)" >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE python -c "import sys;sys.exit(0 if sys.version_info>=(3,12) else 1)" >nul 2>nul && set "PYEXE=python"
if not defined PYEXE (
  echo Python 3.12 or newer is required: https://www.python.org/downloads/
  pause
  exit /b 1
)
%PYEXE% scripts\kpictl.py launch %*
if errorlevel 1 pause
