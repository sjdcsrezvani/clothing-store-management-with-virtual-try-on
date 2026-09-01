@echo off
REM Build the Raykid Store desktop app for Windows.
REM Produces dist\RaykidStore\RaykidStore.exe
REM
REM Prerequisites:
REM   - Python 3.10+ (this project uses 3.12)
REM   - All dependencies from requirements.txt installed
REM   - pywebview + pyinstaller installed
REM
REM Usage: build_windows.bat (double-click or run from cmd)

cd /d "%~dp0"

echo Building Raykid Store desktop app for Windows...

REM Ensure build deps are installed
python -c "import PyInstaller" 2>nul || (
    echo Installing PyInstaller...
    pip install pyinstaller
)
python -c "import webview" 2>nul || (
    echo Installing pywebview...
    pip install pywebview
)

REM Clean previous builds
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM Run PyInstaller with our spec
python -m PyInstaller raykid_store.spec --noconfirm --clean

echo.
echo Build complete!
echo   App: dist\RaykidStore\RaykidStore.exe
echo.
echo   The app stores its data in:
echo   %%APPDATA%%\RaykidStore\
echo.
pause
