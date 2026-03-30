@echo off
REM Build WhatsApp Desktop .exe. Run from desktop_app folder.
REM Requires: pip install pyinstaller

cd /d "%~dp0"

set "PY_CMD=python"
if exist "..\venv\Scripts\python.exe" (
  set "PY_CMD=..\venv\Scripts\python.exe"
)

if not exist "main_desktop.py" (
  echo Run this script from the desktop_app folder.
  exit /b 1
)

echo Closing running WhatsAppDesktop.exe if present...
taskkill /f /im WhatsAppDesktop.exe >nul 2>&1

echo Installing PyInstaller if needed...
%PY_CMD% -m pip install -r requirements_desktop.txt --quiet
%PY_CMD% -m pip install pyinstaller --quiet

echo Building .exe...
echo Full log will be saved to: %~dp0build_last.log
%PY_CMD% -m PyInstaller --noconfirm whatsapp_desktop.spec > "%~dp0build_last.log" 2>&1

if exist "dist\WhatsAppDesktop.exe" (
  echo.
  echo Done. .exe is at: dist\WhatsAppDesktop.exe
  echo Log saved at: build_last.log - for reference
  echo Copy .env next to the .exe in the same folder before running, or bundle .env when building.
) else (
  echo.
  echo Build failed.
  echo Open build_last.log and scroll to the bottom for errors.
  echo   %~dp0build_last.log
  start "" notepad "%~dp0build_last.log"
  exit /b 1
)
