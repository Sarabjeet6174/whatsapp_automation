"""
Desktop app entry point. Run from repo root or desktop_app:
  python desktop_app/main_desktop.py
  or from desktop_app:  python main_desktop.py
  Or run the built .exe (WhatsAppDesktop.exe).

Default UI is PySide6 (local mode). Use --tk for the classic Tkinter UI.
SQL/hybrid modes always use Tk until ported to Qt.
"""
import os
import sys
import logging
import argparse

# When built as .exe (PyInstaller), path is set by the bootloader; no need to change.
if not getattr(sys, "frozen", False):
    _DESKTOP_APP_DIR = os.path.dirname(os.path.abspath(__file__))
    if _DESKTOP_APP_DIR not in sys.path:
        sys.path.insert(0, _DESKTOP_APP_DIR)
    _REPO_ROOT = os.path.dirname(_DESKTOP_APP_DIR)
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)

logging.basicConfig(level=logging.INFO)

from app.ui.main_window import MainWindow


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WhatsApp Desktop runner")
    parser.add_argument(
        "mode",
        nargs="?",
        choices=["sql", "hybrid"],
        help="Run mode: sql=only SQL mode, hybrid=SQL + local mode. Default is local mode only.",
    )
    parser.add_argument(
        "--tk",
        action="store_true",
        help="Use the classic Tkinter UI instead of the default PySide6 (Qt) interface.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    run_mode = args.mode or "local"
    # PySide6 build is local-mode only; SQL / hybrid still use the classic window.
    use_tk = args.tk or run_mode in ("sql", "hybrid")
    if use_tk:
        w = MainWindow(run_mode=run_mode)
        w.run()
        return
    from app.ui.qt.modern_main_window import run_qt_app

    run_qt_app()


if __name__ == "__main__":
    main()
