@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0python\python.exe" goto incomplete
if not exist "%~dp0app\skate_wizard.py" goto incomplete
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0setup-wizard.ps1"
if errorlevel 1 pause
exit /b
:incomplete
echo Extract the complete mod ZIP to a folder before running Setup.cmd.
echo Keep this extracted folder for future updates and uninstall.
pause
exit /b 1
