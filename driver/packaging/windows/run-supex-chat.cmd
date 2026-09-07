@echo off
rem ============================================================
rem  run-supex-chat.cmd - double-click launcher for the Supex
rem  Chat windowed agent.
rem
rem  This file is copied next to the executables by build-exe.cmd.
rem  It starts the local agent server and opens the chat window
rem  in your default browser (http://127.0.0.1:8765).
rem
rem  Provider configuration comes from environment variables or
rem  the profiles file - see docs/agent.md. Example:
rem      set SUPEX_AI_BASE_URL=https://api.openai.com/v1
rem      set SUPEX_AI_API_KEY=sk-...
rem      set SUPEX_AI_MODEL=gpt-5
rem ============================================================
setlocal

set "EXE_DIR=%~dp0"
cd /d "%EXE_DIR%"

if not exist "supex-chat.exe" (
    echo supex-chat.exe was not found next to this launcher.
    echo Run build-exe.cmd first, then launch from the dist folder.
    echo.
    pause
    exit /b 1
)

echo Starting Supex Chat...
echo Close this window or press Ctrl-C to stop the server.
echo.
supex-chat.exe serve

echo.
echo Supex Chat server stopped.
pause
