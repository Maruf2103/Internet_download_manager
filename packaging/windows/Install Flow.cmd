@echo off
if not exist "%~dp0runtime\python.exe" (
  echo Please extract the entire ZIP before running this installer.
  pause
  exit /b 1
)
"%~dp0runtime\python.exe" -I "%~dp0install.py" %*
if errorlevel 1 pause
