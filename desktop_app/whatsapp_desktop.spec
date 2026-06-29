# PyInstaller spec for WhatsApp Desktop .exe
# Run from desktop_app folder: build_exe.bat   OR   pyinstaller whatsapp_desktop.spec
# Build with the SAME interpreter that has PySide6 (repo ..\venv\Scripts\python.exe).
# If you use a different python (e.g. 3.8 on PATH), you will see "Hidden import PySide6 not found"
# and the Qt UI will be missing or crash at runtime.
# If .env exists in desktop_app or repo root, it is bundled into the .exe so users don't need a separate .env file.

import os
try:
    _spec_dir = os.path.dirname(os.path.abspath(SPEC))
except NameError:
    _spec_dir = os.getcwd()
_env_local = os.path.join(_spec_dir, '.env')
_env_repo = os.path.join(os.path.dirname(_spec_dir), '.env')
_env_to_bundle = _env_local if os.path.isfile(_env_local) else (_env_repo if os.path.isfile(_env_repo) else None)
datas_list = [(_env_to_bundle, '.')] if _env_to_bundle else []

block_cipher = None

a = Analysis(
    ['main_desktop.py'],
    pathex=['.'],
    binaries=[],
    datas=datas_list,
    hiddenimports=[
        'pyodbc',
        'app',
        'app.db',
        'app.db.sql',
        'app.whatsapp',
        'app.whatsapp.sender',
        'app.core',
        'app.core.profile_state',
        'app.core.message_loop',
        'app.core.scheduler',
        'app.ui',
        'app.ui.main_window',
        'app.services',
        'app.services.constants',
        'app.services.local_workflow_controller',
        'app.ui.qt',
        'app.ui.qt.styles',
        'app.ui.qt.modern_main_window',
        'app.ui.qt.pages',
        'app.ui.qt.pages.send_messages_page',
        'app.ui.qt.widgets',
        'app.ui.qt.widgets.chat_preview',
        'PySide6',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'shiboken6',
        'config',
        'selenium',
        'selenium.webdriver',
        'selenium.webdriver.chrome',
        'selenium.webdriver.chrome.webdriver',
        'selenium.webdriver.chrome.options',
        'selenium.webdriver.chrome.service',
        'webdriver_manager',
        'webdriver_manager.chrome',
        'dotenv',
        # Attachment send (CDP file-chooser intercept + Esc to dismiss native Open dialog)
        'websocket',
        'pyautogui',
        'pyscreeze',
        'pygetwindow',
        'pytweening',
        'mouseinfo',
        'pyperclip',
        'PIL',
        'PIL.Image',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='WhatsAppDesktop',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # No console window for desktop app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # Set to 'desktop_app/icon.ico' if you add an icon
)
