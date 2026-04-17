"""
Desktop app config.
- When run as .exe (PyInstaller): .env can be bundled inside the exe, or next to the exe (overrides bundled).
  chrome_profiles live next to the .exe.
- When run as script: .env from repo root, chrome_profiles in desktop_app folder.
"""
import os
import sys


def _get_base_dir() -> str:
    """Directory for chrome_profiles. When frozen, same folder as the .exe."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_env_path() -> str:
    """
    Path to .env file. When frozen: use .env next to the exe if present (override),
    otherwise use the .env bundled inside the exe (sys._MEIPASS). When not frozen: repo root.
    """
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        env_next_to_exe = os.path.join(exe_dir, ".env")
        if os.path.isfile(env_next_to_exe):
            return env_next_to_exe
        return os.path.join(getattr(sys, "_MEIPASS", exe_dir), ".env")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


_BASE = _get_base_dir()


def get_chrome_profiles_base() -> str:
    """Base directory for per-client Chrome profiles."""
    return os.path.join(_BASE, "chrome_profiles")


def get_profile_dir(client_phno: str) -> str:
    """Directory for one client's Chrome profile. Safe subdir name from phone number."""
    safe = "".join(c for c in str(client_phno) if c.isalnum() or c in "-_")
    return os.path.join(get_chrome_profiles_base(), safe or "default")


def get_local_access_db_path() -> str:
    """MS Access DB path for local mode storage."""
    return os.path.join(_BASE, "local_store.accdb")


def allow_search_from_env() -> bool:
    """
    ALLOW_SEARCH in .env / environment: if true, phone sends use WhatsApp side search first.
    Default false (use web.whatsapp.com/send?phone= only for numbers).
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(get_env_path())
    except ImportError:
        pass
    v = os.getenv("ALLOW_SEARCH", "").strip().lower()
    return v in ("1", "true", "yes", "on")
