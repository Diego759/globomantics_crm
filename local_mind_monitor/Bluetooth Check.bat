@echo off
setlocal

rem Diagnostic launcher. Scans for nearby Bluetooth LE devices and reports
rem whether this PC can see the Muse headband. Unlike the app launchers this
rem one keeps its console window open so you can read the results.

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

rem A partially-deleted .venv (Scripts\ present but no pyvenv.cfg) makes Python
rem fail with "No pyvenv.cfg file". Wipe it so it rebuilds cleanly.
if exist "%VENV_DIR%\Scripts" if not exist "%VENV_DIR%\pyvenv.cfg" (
    echo Repairing a damaged setup folder...
    rmdir /s /q "%VENV_DIR%"
)

if not exist "%VENV_DIR%\pyvenv.cfg" (
    echo Setting up for the first time ^(installing dependencies^)...
    echo This only happens once and can take a minute.
    echo.
    %PY_LAUNCHER% -m venv "%VENV_DIR%"
    "%VENV_DIR%\Scripts\python.exe" -m pip install --quiet --upgrade pip
    "%VENV_DIR%\Scripts\python.exe" -m pip install --quiet -r "%SCRIPT_DIR%requirements.txt"
)

rem Ensure the Bluetooth library is present even if the venv predates it.
"%VENV_DIR%\Scripts\python.exe" -c "import bleak" >nul 2>nul
if errorlevel 1 (
    echo Installing the Bluetooth scanner library...
    "%VENV_DIR%\Scripts\python.exe" -m pip install --quiet bleak
    echo.
)

pushd "%SCRIPT_DIR%.."
"%VENV_DIR%\Scripts\python.exe" -m local_mind_monitor.bluetooth_check
popd

echo.
echo ------------------------------------------------------------
echo Copy the results above if you need to share them. Press a key to close.
pause >nul
exit /b 0
