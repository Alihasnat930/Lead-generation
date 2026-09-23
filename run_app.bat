@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "PYTHONPYCACHEPREFIX=%~dp0data\cache\python"
set "PYTHONUNBUFFERED=1"

title Prospect Studio
echo.
echo Prospect Studio - local lead research
echo.

if exist ".venv\Scripts\python.exe" goto launch

echo Creating the local Python environment...
where py >nul 2>nul
if errorlevel 1 goto try_python
py -3.11 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if errorlevel 1 goto try_py_latest
py -3.11 -m venv ".venv"
if errorlevel 1 goto setup_failed
goto launch

:try_py_latest
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if errorlevel 1 goto try_python
py -3 -m venv ".venv"
if errorlevel 1 goto setup_failed
goto launch

:try_python
where python >nul 2>nul
if errorlevel 1 goto missing_python
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if errorlevel 1 goto missing_python
python -m venv ".venv"
if errorlevel 1 goto setup_failed

:launch
".venv\Scripts\python.exe" "scripts\start.py" %*
set "APP_EXIT_CODE=%ERRORLEVEL%"
if "%APP_EXIT_CODE%"=="0" goto done
echo.
echo Startup failed. Read the message above and data\logs\server.log.
echo Run run_app.bat --repair if dependencies need reinstalling.
goto failed

:missing_python
echo Python 3.11 or newer is required.
echo Install it from https://www.python.org/downloads/windows/ and enable Add Python to PATH.
set "APP_EXIT_CODE=1"
goto failed

:setup_failed
echo Could not create .venv. Check your Python installation and folder permissions.
set "APP_EXIT_CODE=1"

:failed
if /i "%PROSPECT_NO_PAUSE%"=="1" goto done
pause

:done
exit /b %APP_EXIT_CODE%
