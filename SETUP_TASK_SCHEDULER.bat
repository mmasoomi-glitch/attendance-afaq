@echo off
:: ============================================================
::  AFAQ ATTENDANCE — Task Scheduler Setup
::  Run this ONCE as Administrator.
::  Creates a Windows Task that launches AfaqAttendance.exe
::  every day at 09:00 AM automatically.
:: ============================================================

set "EXE_PATH=%~dp0AfaqAttendance.exe"
set "TASK_NAME=AfaqAttendance"

echo.
echo  Setting up daily auto-launch at 09:00 AM...
echo  EXE: %EXE_PATH%
echo.

:: Delete old task if exists
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

:: Create new daily task at 09:00
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "\"%EXE_PATH%\"" ^
  /sc DAILY ^
  /st 09:00 ^
  /ru "%USERNAME%" ^
  /rl HIGHEST ^
  /f

if %errorlevel% == 0 (
    echo  [OK] Task created. AfaqAttendance.exe will launch every day at 09:00 AM.
    echo  [OK] App will auto-shutdown at 23:59 and relaunch next morning.
    echo.
    echo  Starting app now for today...
    start "" "%EXE_PATH%"
) else (
    echo  [ERROR] Could not create task. Make sure you ran this as Administrator.
)

echo.
pause
