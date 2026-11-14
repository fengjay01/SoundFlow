@echo off
setlocal
rem ============================================================
rem SoundFlow Windows launcher - double-click or run from cmd.
rem
rem Thin wrapper around the SAME cross-platform entry point used on
rem macOS / Linux:  pnpm dev:desktop
rem   -> node scripts/dev-env.mjs run -- pnpm tauri dev
rem
rem dev-env.mjs injects every cache/temp path into .venv (nothing on C:),
rem resolves the Python interpreter per platform, pins the managed runtime
rem directory, and puts the project toolchain first on PATH. So this .bat
rem needs no environment setup of its own, no PowerShell and no absolute
rem drive letters - all paths derive from %~dp0, so the checkout can move.
rem
rem Usage:
rem   dev-local.bat            launch the desktop app
rem   dev-local.bat --check    verify Node / pnpm without launching
rem ============================================================
cd /d "%~dp0"
set "ROOT=%~dp0"
set "ENV=%ROOT%.venv"

rem Prefer the project-local Node/pnpm when the self-contained .venv exists;
rem otherwise use whatever Node 22 the developer installed system-wide.
set "PATH=%ENV%\node\runtime;%ENV%\node\npm-global;%PATH%"

where node >nul 2>&1
if errorlevel 1 (
    echo [dev-local] ERROR: Node.js 22 not found.
    echo [dev-local] Install Node 22 ^(corepack included^), or run: corepack pnpm dev:desktop
    pause
    exit /b 1
)

if /i "%~1"=="--check" (
    echo [dev-local] Node:
    where node
    echo [dev-local] pnpm:
    if exist "%ENV%\node\npm-global\pnpm.cmd" (
        call "%ENV%\node\npm-global\pnpm.cmd" --version
    ) else (
        call corepack pnpm --version
    )
    echo [dev-local] OK. Launch with dev-local.bat, or corepack pnpm dev:desktop.
    exit /b 0
)

echo [dev-local] starting SoundFlow ^(pnpm dev:desktop^) ...
if exist "%ENV%\node\npm-global\pnpm.cmd" (
    call "%ENV%\node\npm-global\pnpm.cmd" dev:desktop
) else (
    call corepack pnpm dev:desktop
)
set "RC=%ERRORLEVEL%"
echo.
echo [dev-local] exited with code %RC%. Keep this window open to read any error above.
pause
exit /b %RC%
