@echo off
cd /d "%~dp0"
py -3 -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Ready. Open Launch Bench Control.cmd to start.
pause
exit /b 0
:failed
echo Setup failed. Check that Python with Tcl/Tk is installed.
pause
exit /b 1
