@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 scripts\local_deploy.py %*
goto result
:use_python
where python >nul 2>nul
if errorlevel 1 goto missing
python scripts\local_deploy.py %*
:result
set "CIRP_EXIT=%ERRORLEVEL%"
if "%CIRP_EXIT%"=="0" exit /b 0
if "%CIRP_EXIT%"=="130" exit /b 0
echo CIRP stopped with an error. See the message above.
pause
exit /b %CIRP_EXIT%
:missing
echo Install Python 3.11 or newer, then run this file again.
pause
exit /b 2
