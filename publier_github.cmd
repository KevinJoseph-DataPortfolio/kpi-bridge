@echo off
rem Publie KPI Bridge dans le depot prive GitHub KevinJoseph-DataPortfolio/kpi-bridge.
rem 1) une page GitHub s'ouvre et le code est deja copie : Ctrl+V puis "Continue" puis "Authorize github"
rem 2) le script envoie ensuite le code automatiquement.
setlocal
cd /d "%~dp0"
set "GH=%USERPROFILE%\.kpi-bridge\tools\gh\bin\gh.exe"
if not exist "%GH%" (
  echo GitHub CLI introuvable : %GH%
  pause
  exit /b 1
)
"%GH%" auth status >nul 2>nul || "%GH%" auth login --hostname github.com --git-protocol https --web --skip-ssh-key
"%GH%" auth setup-git
git remote get-url origin >nul 2>nul || git remote add origin https://github.com/KevinJoseph-DataPortfolio/kpi-bridge.git
git push -u origin main
echo.
echo Depot : https://github.com/KevinJoseph-DataPortfolio/kpi-bridge
pause
