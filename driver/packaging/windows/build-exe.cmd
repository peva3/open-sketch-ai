@echo off
rem ============================================================
rem  build-exe.cmd - build the three Supex Chat executables
rem  locally on Windows (no GitHub Actions, no cloud services).
rem
rem  Double-click this file once from the repository checkout.
rem  On success it prints the output folder. Afterwards you can
rem  double-click dist\run-supex-chat.cmd to launch the chat app.
rem ============================================================
setlocal

set "SCRIPT_DIR=%~dp0"
set "DRIVER_DIR=%SCRIPT_DIR%..\.."
cd /d "%DRIVER_DIR%"

echo === Supex Chat local Windows build ===
echo Working directory: %CD%
echo.

rem --- locate or install uv ------------------------------------
where uv >nul 2>nul
if errorlevel 1 (
    echo uv was not found on PATH. Attempting the official installer...
    powershell -NoProfile -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
)

set "UV=%USERPROFILE%\.local\bin\uv.exe"
if exist "%UV%" goto :uv_ok
set "UV=uv"
where uv >nul 2>nul
if errorlevel 1 (
    echo.
    echo Could not find or install uv automatically.
    echo Install it from https://docs.astral.sh/uv/ and re-run this script.
    pause
    exit /b 1
)
:uv_ok

rem --- install build tooling and run pyinstaller ----------------
echo === Installing build dependencies (first run may take a while) ===
"%UV%" sync --group build
if errorlevel 1 (
    echo uv sync failed. See the message above.
    pause
    exit /b 1
)

echo.
echo === Building executables with PyInstaller ===
"%UV%" run pyinstaller --noconfirm --clean --distpath dist --workpath build packaging\supex.spec
if errorlevel 1 (
    echo PyInstaller failed. See the message above.
    pause
    exit /b 1
)

rem --- drop the launcher next to the binaries -------------------
copy /y "%SCRIPT_DIR%run-supex-chat.cmd" "dist\run-supex-chat.cmd" >nul

echo.
echo === Done ===
echo Executables are in:
echo    %DRIVER_DIR%dist
echo.
echo     supex-chat.exe   the windowed/terminal agent
echo     supex-mcp.exe    the MCP backend (spawned by supex-chat)
echo     supex.exe        the classic Supex CLI
echo     run-supex-chat.cmd  double-click to open the chat window
echo.
echo Tip: right-click run-supex-chat.cmd and choose
echo      "Send to - Desktop (create shortcut)" for a desktop icon.
echo.
pause
