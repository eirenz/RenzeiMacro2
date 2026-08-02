@echo off
:: Check for admin rights
NET SESSION >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo Requesting administrative privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo Running RenzeiMacro installation script...
powershell -ExecutionPolicy Bypass -File "%~dp0setup.ps1"

echo.
echo Press any key to exit...
pause >nul
