@echo off
REM Build WhatsApp Desktop .exe. Run from desktop_app folder.
REM IMPORTANT: Use the repo venv (..\venv) so PyInstaller bundles the SAME Python
REM that has PySide6. If you run pyinstaller manually with a different "python"
REM (e.g. 3.8 on PATH), PySide6 will be missing and the Qt UI will not run.

cd /d "%~dp0"

set "PY_CMD=python"
if exist "..\venv\Scripts\python.exe" (
  set "PY_CMD=..\venv\Scripts\python.exe"
)

if not exist "main_desktop.py" (
  echo Run this script from the desktop_app folder.
  exit /b 1
)

echo Using Python:
%PY_CMD% -c "import sys; print(sys.executable)"
echo.

echo Closing running WhatsAppDesktop.exe if present...
taskkill /f /im WhatsAppDesktop.exe >nul 2>&1

echo Installing dependencies + PyInstaller...
%PY_CMD% -m pip install -r requirements_desktop.txt --quiet
%PY_CMD% -m pip install pyinstaller --quiet

echo Verifying PySide6 - required for Qt UI bundled in the exe...
%PY_CMD% -c "import PySide6; import PySide6.QtWidgets; print('PySide6 OK:', PySide6.__version__)" 2>nul
if errorlevel 1 (
  echo.
  echo ERROR: PySide6 is not installed for this Python interpreter.
  echo Fix: use the repo venv and install deps:
  echo   ..\venv\Scripts\python.exe -m pip install -r requirements_desktop.txt
  echo Then run this script again.
  exit /b 1
)

echo Building .exe...
echo Full log will be saved to: %~dp0build_last.log
%PY_CMD% -m PyInstaller --clean --noconfirm whatsapp_desktop.spec > "%~dp0build_last.log" 2>&1

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
