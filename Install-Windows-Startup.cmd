@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install_windows_boot.ps1"
if errorlevel 1 pause
