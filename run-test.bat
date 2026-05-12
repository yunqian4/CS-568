@echo off
setlocal

set "ROOT=%~dp0"
cd /d "%ROOT%"

set "npm_config_cache=%ROOT%\.npm-cache"
set "XDG_CONFIG_HOME=%ROOT%\.wrangler\config"

echo [1/3] Exporting cached study assets...
backend\.venv\Scripts\python.exe human-study\scripts\3_export_cloudflare.py
if errorlevel 1 goto :failed

echo [2/3] Building frontend...
pushd frontend
npm.cmd run build
set "BUILD_EXIT=%ERRORLEVEL%"
popd
if not "%BUILD_EXIT%"=="0" goto :failed

echo [3/3] Starting Cloudflare Pages preview for /study...
echo.
echo Open http://127.0.0.1:8788/study
echo.
echo The STUDY_RESULTS R2 binding must be configured for real score submission.
echo Upload private answer keys from study\private-r2 before a real study run.
echo.
npx.cmd wrangler pages dev frontend\dist --compatibility-date=2026-05-10 --port=8788
goto :eof

:failed
echo.
echo run-test.bat failed.
exit /b 1
