@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo  Store Management - Windows build
echo ============================================================
echo.

REM ---- Python 3.12 ---------------------------------------------------------
set "PYTHON="
py -3.12 --version >nul 2>&1
if not errorlevel 1 set "PYTHON=py -3.12"

if not defined PYTHON (
    python -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON=python"
)

if not defined PYTHON (
    echo ERROR: Python 3.12 was not found.
    echo.
    echo Install it from:
    echo   https://www.python.org/downloads/windows/
    echo Tick "Add python.exe to PATH" and the py launcher during setup.
    echo Then run this file again.
    echo.
    pause
    exit /b 1
)

echo Using Python:
%PYTHON% --version
echo.

REM ---- Build environment ---------------------------------------------------
if not exist ".venv-build\Scripts\python.exe" (
    echo Creating the build environment...
    %PYTHON% -m venv .venv-build
    if errorlevel 1 goto :failed
)

set "PY=.venv-build\Scripts\python.exe"
set "PYTHONUTF8=1"

echo Installing dependencies (first run needs internet)...
"%PY%" -m pip install --upgrade pip wheel >nul
if errorlevel 1 goto :failed
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :failed
"%PY%" -m pip install pywebview==6.2.1 pyinstaller==6.22.2
if errorlevel 1 goto :failed
echo.

REM ---- Build ---------------------------------------------------------------
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo Building the app...
"%PY%" -m PyInstaller store_management.spec --noconfirm --clean
if errorlevel 1 goto :failed

if not exist "dist\StoreManagement\StoreManagement.exe" (
    echo.
    echo ERROR: the build finished but dist\StoreManagement\StoreManagement.exe is missing.
    goto :failed
)

REM ---- Optional zip for handing to a shop ----------------------------------
echo.
echo Creating the distribution zip...
if exist "dist\StoreManagement-windows.zip" del /q "dist\StoreManagement-windows.zip"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path 'dist\StoreManagement' -DestinationPath 'dist\StoreManagement-windows.zip' -Force" >nul 2>&1
if errorlevel 1 (
    echo Warning: could not create the zip. The app folder itself is ready.
) else (
    echo Zip: dist\StoreManagement-windows.zip
)

echo.
echo ============================================================
echo  Build complete
echo ============================================================
echo.
echo Run:  dist\StoreManagement\StoreManagement.exe
echo.
echo Keep the whole StoreManagement folder together.
echo The .exe alone will not run.
echo.
echo Data and database live in:
echo   %%LOCALAPPDATA%%\StoreManagement\
echo.
echo First launch opens the setup wizard.
echo If Windows Firewall asks, allow private networks for phone access.
echo.
pause
exit /b 0

:failed
echo.
echo ============================================================
echo  Build failed
echo ============================================================
echo Review the messages above, then run this file again.
echo.
pause
exit /b 1
