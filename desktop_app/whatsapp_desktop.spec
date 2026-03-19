# PyInstaller spec for WhatsApp Desktop .exe
# Run from desktop_app folder:  pyinstaller whatsapp_desktop.spec
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
