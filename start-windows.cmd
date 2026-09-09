@echo off
rem ============================================================
rem  start-windows.cmd - ONE double-click to bring Supex Chat up
rem  on Windows: builds the executables on first run, boots
rem  SketchUp with the runtime injected, and opens the chat window.
rem
rem  - Run from anywhere; paths are computed from this file.
rem  - If driver\dist\supex-chat.exe is missing it builds first
rem    (driver\packaging\windows\build-exe.cmd) - a few minutes.
rem  - Boots SketchUp (scripts\launch-sketchup-windows.cmd) in its
rem    own window and waits for the runtime bridge on :9876.
rem  - Starts supex-chat.exe serve and opens http://127.0.0.1:8765
rem    in your default browser.
rem ============================================================
setlocal

set "ROOT=%~dp0"
set "DIST=%ROOT%driver\dist"
set "CHAT_EXE=%DIST%\supex-chat.exe"
set "BUILD_CMD=%ROOT%driver\packaging\windows\build-exe.cmd"
set "SKETCHUP_CMD=%ROOT%scripts\launch-sketchup-windows.cmd"

echo === Supex Chat one-click launcher ===
echo.

rem --- 1. make sure the executables exist (build once if missing) ---
if exist "%CHAT_EXE%" goto :have_exe

echo The Supex Chat executables were not found in %DIST%.
echo Building them now (first run only; takes a few minutes)...
echo.
if not exist "%BUILD_CMD%" (
    echo ERROR: build script not found at %BUILD_CMD%
    pause
    exit /b 1
)
start "Supex build" cmd /c ""%BUILD_CMD%""
echo Waiting for the build to finish (check the 'Supex build' window)...
set /a TRIES=0
:wait_build
if exist "%CHAT_EXE%" goto :have_exe
set /a TRIES+=1
if %TRIES% GEQ 90 (
    echo.
    echo Build did not finish in time. Check the 'Supex build' window
    echo for errors, then run this script again.
    pause
    exit /b 1
)
timeout /t 5 /nobreak >nul
goto :wait_build

:have_exe
echo Executables found: %DIST%
echo.

rem --- 2. boot SketchUp with the Supex runtime injected ---
echo Launching SketchUp with the Supex runtime (separate window)...
if not exist "%SKETCHUP_CMD%" (
    echo WARNING: %SKETCHUP_CMD% not found - skipping SketchUp launch.
    echo          If SketchUp is not already running, Supex Chat will
    echo          not be able to reach it.
) else (
    start "Supex SketchUp" cmd /c ""%SKETCHUP_CMD%""
)

rem --- 3. open the chat window ---
echo.
echo Opening Supex Chat (server runs in this window)...
echo Close this window or press Ctrl-C to stop the server.
echo.
"%CHAT_EXE%" serve

echo.
echo Supex Chat server stopped.
pause
