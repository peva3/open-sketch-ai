@echo off
rem ============================================================
rem  launch-sketchup-windows.cmd - launch SketchUp with the Supex
rem  Ruby runtime injected from this repository (Windows).
rem
rem  Mirror of scripts/launch-sketchup.sh for macOS. Uses
rem  SketchUp's -RubyStartup switch to load runtime/src/injector.rb
rem  so the extension sources run directly (no .rbz build needed).
rem
rem  Usage:  launch-sketchup-windows.cmd  [model.skp] [--console]
rem
rem  Optional env (all inherited by SketchUp):
rem    SUPEX_SKETCHUP_EXE  path to SketchUp.exe (else auto-detected)
rem    SUPEX_WORKSPACE     workspace root (default: current dir)
rem    SUPEX_AUTH_TOKEN    shared secret; must match the agent side
rem    SUPEX_VERBOSE=1     verbose runtime logging
rem
rem  After SketchUp starts, the bridge listens on 127.0.0.1:9876.
rem  Verify with:  supex-chat --check   (from this repo, via uv)
rem ============================================================
setlocal

set "SCRIPT_DIR=%~dp0"
set "REPO_ROOT=%SCRIPT_DIR%.."
set "INJECTOR=%REPO_ROOT%\runtime\src\injector.rb"

rem --- resolve SketchUp executable -----------------------------
set "SKETCHUP_EXE=%SUPEX_SKETCHUP_EXE%"
if defined SKETCHUP_EXE goto :exe_ok
if exist "C:\Program Files\SketchUp\SketchUp 2026\SketchUp.exe" (
    set "SKETCHUP_EXE=C:\Program Files\SketchUp\SketchUp 2026\SketchUp.exe"
    goto :exe_ok
)
if exist "C:\Program Files\SketchUp\SketchUp 2025\SketchUp.exe" (
    set "SKETCHUP_EXE=C:\Program Files\SketchUp\SketchUp 2025\SketchUp.exe"
    goto :exe_ok
)
for /f "delims=" %%i in ('where sketchup.exe 2^>nul') do (
    set "SKETCHUP_EXE=%%i"
    goto :exe_ok
)
echo SketchUp.exe was not found.
echo Set SUPEX_SKETCHUP_EXE to its full path and re-run.
pause
exit /b 1
:exe_ok

if not exist "%INJECTOR%" (
    echo Runtime injector not found: %INJECTOR%
    pause
    exit /b 1
)

rem --- optional model file -------------------------------------
set "MODEL_ARG="
set "ARG1=%~1"
if /i "%ARG1%"=="--console" goto :no_model
if defined ARG1 (
    set "MODEL_ARG=%ARG1%"
)
:no_model

rem --- default workspace to current directory -------------------
if not defined SUPEX_WORKSPACE (
    for %%i in ("%CD%") do set "SUPEX_WORKSPACE=%%~fi"
)

rem --- optional console toggle for dev --------------------------
if /i "%ARG1%"=="--console" (
    set "SUPEX_VERBOSE=1"
)

echo SketchUp : %SKETCHUP_EXE%
echo Injector : %INJECTOR%
echo Workspace: %SUPEX_WORKSPACE%
if defined SUPEX_AUTH_TOKEN echo Auth     : enabled (SUPEX_AUTH_TOKEN set)
echo.
echo Launching SketchUp with the Supex runtime injected...
echo Close SketchUp to end. Bridge listens on 127.0.0.1:9876 after startup.

if defined MODEL_ARG (
    start "" "%SKETCHUP_EXE%" "%MODEL_ARG%" -RubyStartup "%INJECTOR%"
) else (
    start "" "%SKETCHUP_EXE%" -RubyStartup "%INJECTOR%"
)

echo.
echo SketchUp started. Verify the bridge with:  supex-chat --check
pause
