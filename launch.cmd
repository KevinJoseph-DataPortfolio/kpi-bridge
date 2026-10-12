@echo off
rem KPI Bridge - lancement Windows (installe au premier lancement, puis reutilise l'installation).
rem N'execute aucun script PowerShell et ne modifie aucune politique systeme.
setlocal
cd /d "%~dp0"
set "PYEXE="
where py >nul 2>nul && py -3 -c "import sys;sys.exit(0 if sys.version_info>=(3,12) else 1)" >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE python -c "import sys;sys.exit(0 if sys.version_info>=(3,12) else 1)" >nul 2>nul && set "PYEXE=python"
if not defined PYEXE (
  echo Python 3.12 ou plus est requis : https://www.python.org/downloads/
  pause
  exit /b 1
)
%PYEXE% scripts\kpictl.py launch %*
if errorlevel 1 pause
