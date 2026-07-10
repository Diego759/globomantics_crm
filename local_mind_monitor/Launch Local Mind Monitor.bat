@echo off
setlocal

rem Double-click launcher. First run creates a local virtual environment and
rem installs dependencies (shows progress in this window); every run after
rem that skips straight to launching the app.

set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%.venv"
set "PY_LAUNCHER=py -3"

where py >nul 2>nul
if errorlevel 1 (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python was not found on this computer.
        echo.
        echo Install Python 3.11 or newer from https://python.org/downloads/
        echo ^(check "Add python.exe to PATH" during install^), then double-click
        echo this file again.
        echo.
        pause
        exit /b 1
    )
    set "PY_LAUNCHER=python"
)

if not exist "%VENV_DIR%\Scripts\pythonw.exe" (
    echo Setting up Local Mind Monitor for the first time...
    echo This installs its dependencies into a private folder here ^(.venv^)
    echo and only happens once. It can take a minute.
    echo.

    %PY_LAUNCHER% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo.
        echo Could not create the virtual environment. See the error above.
        pause
        exit /b 1
    )

    "%VENV_DIR%\Scripts\python.exe" -m pip install --quiet --upgrade pip
    "%VENV_DIR%\Scripts\python.exe" -m pip install --quiet -r "%SCRIPT_DIR%requirements.txt"
    if errorlevel 1 (
        echo.
        echo Dependency install failed. See the error above.
        pause
        exit /b 1
    )

    echo.
    echo Setup complete. Launching...
)

rem local_mind_monitor is invoked as a module, so it needs to be run from
rem the folder that CONTAINS local_mind_monitor\ (one level up from here).
pushd "%SCRIPT_DIR%.."
start "" "%VENV_DIR%\Scripts\pythonw.exe" -m local_mind_monitor.app %*
popd

exit /b 0
