@echo off
setlocal

rem Force-closes any running Local Mind Monitor process. You normally never
rem need this -- the app now surfaces its existing window instead of stacking
rem copies, and shuts down cleanly. Keep it around as a rescue in case a launch
rem ever seems stuck (an invisible pythonw.exe) or a Bluetooth teardown hangs.

echo Looking for running Local Mind Monitor processes...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('pythonw.exe','python.exe') -and $_.CommandLine -match 'local_mind_monitor\.app' }; if ($p) { $p | ForEach-Object { Write-Host ('Closing PID ' + $_.ProcessId + ' -> ' + $_.CommandLine); Stop-Process -Id $_.ProcessId -Force } } else { Write-Host 'Nothing running -- all clear.' }"

echo.
echo Done. You can close this window.
timeout /t 3 >nul
