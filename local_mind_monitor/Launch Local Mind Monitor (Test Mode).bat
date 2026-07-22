@echo off
setlocal

rem Test-mode launcher. Opens the app running on BrainFlow's synthetic signal
rem generator instead of a real headband -- use it to confirm everything
rem installs and runs before your Muse is on hand. Shares the same .venv as
rem the normal launcher, so if you have already run that one, this starts
rem instantly too. The app window title will read "(synthetic)".

set "SCRIPT_DIR=%~dp0"
set "VENV_DIR=%SCRIPT_DIR%.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
rem Written only after dependencies install successfully. We gate setup on THIS
rem (not pyvenv.cfg): a venv can exist with no pip / no packages, and keying off
rem pyvenv.cfg made every later launch skip setup and crash silently.
set "READY_MARKER=%VENV_DIR%\.deps-installed"
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

rem A partially-deleted .venv (Scripts\ present but no pyvenv.cfg) makes Python
rem fail with "No pyvenv.cfg file". Wipe it so it rebuilds cleanly.
if exist "%VENV_DIR%\Scripts" if not exist "%VENV_DIR%\pyvenv.cfg" (
    echo Repairing a damaged setup folder...
    rmdir /s /q "%VENV_DIR%"
)

if not exist "%READY_MARKER%" (
    echo Setting up Local Mind Monitor for the first time...
    echo This installs its dependencies into a private folder here ^(.venv^)
    echo and only happens once. It can take a minute.
    echo.

    if not exist "%VENV_DIR%\pyvenv.cfg" (
        %PY_LAUNCHER% -m venv "%VENV_DIR%"
        if errorlevel 1 (
            echo.
            echo Could not create the virtual environment. See the error above.
            pause
            exit /b 1
        )
    )

    rem Some venvs come up without pip (interrupted install, AV, etc.). Make
    rem sure it exists before we try to use it, otherwise the installs below
    rem fail with "No module named pip".
    "%VENV_PY%" -m pip --version >nul 2>nul
    if errorlevel 1 "%VENV_PY%" -m ensurepip --upgrade

    "%VENV_PY%" -m pip install --quiet --upgrade pip
    "%VENV_PY%" -m pip install --quiet -r "%SCRIPT_DIR%requirements.txt"
    if errorlevel 1 (
        echo.
        echo Dependency install failed. See the error above.
        echo If it mentions "Access is denied", close any running copy of the
        echo app ^(look for pythonw.exe in Task Manager^) and try again.
        pause
        exit /b 1
    )

    rem Mark setup complete only after a successful install, so a half-finished
    rem setup is retried next time instead of launching a broken app.
    > "%READY_MARKER%" echo installed
    echo.
    echo Setup complete. Launching in test mode...
)

rem local_mind_monitor is invoked as a module, so it needs to be run from
rem the folder that CONTAINS local_mind_monitor\ (one level up from here).
pushd "%SCRIPT_DIR%.."
start "" "%VENV_DIR%\Scripts\pythonw.exe" -m local_mind_monitor.app --synthetic %*
popd

exit /b 0
