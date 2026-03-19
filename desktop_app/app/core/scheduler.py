"""
Scheduler: one thread per profile running the message loop. Start/stop/pause/resume.
"""
import logging
import threading
from typing import Callable, Dict, Optional

from app.core.profile_state import ProfileState
from app.core.message_loop import run_loop_for_profile
from app.whatsapp.sender import create_driver_for_profile, open_whatsapp_web
from app.db.sql import log_app_activity

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(
        self,
        on_log: Optional[Callable[[str, str, str], None]] = None,
    ) -> None:
        self._threads: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._on_log = on_log

    def _profile_key(self, profile: ProfileState) -> str:
        return str(profile.client_phno)

    def _emit(self, profile: ProfileState, event_type: str, message: str) -> None:
        log_app_activity(profile.client_phno, event_type, message, source="scheduler")
        if self._on_log:
            try:
                self._on_log(profile.client_phno, event_type, message)
            except Exception:
                pass

    def open_profile(self, profile: ProfileState) -> str:
        """Open WhatsApp Web for this profile. Returns 'SUCCESS' or error message."""
        if profile.get_driver() is not None:
            self._emit(profile, "profile_open", "Profile already open.")
            return "SUCCESS"
        try:
            driver = create_driver_for_profile(profile.client_phno)
            profile.set_driver(driver)
            result = open_whatsapp_web(driver)
            if result == "SUCCESS":
                self._emit(profile, "profile_open", "WhatsApp Web opened.")
            else:
                self._emit(profile, "profile_open_error", result)
            return result
        except Exception as e:
            self._emit(profile, "profile_open_error", str(e)[:500])
            return str(e)[:500]

    def start_loop(self, profile: ProfileState) -> None:
        """Start the 60s polling loop for this profile in a background thread."""
        with self._lock:
            key = self._profile_key(profile)
            if key in self._threads and self._threads[key].is_alive():
                self._emit(profile, "start", "Loop already running.")
                return
            profile.set_stopped(False)
            profile.set_running(True)
            profile.set_paused(False)
            t = threading.Thread(
                target=run_loop_for_profile,
                args=(profile, self._on_log),
                daemon=True,
            )
            self._threads[key] = t
            t.start()
        logger.info("Started loop for %s", profile.client_phno)
        self._emit(profile, "start", "Started profile scheduler.")

    def stop_loop(self, profile: ProfileState) -> None:
        """Stop the loop; do not close Chrome."""
        profile.set_stopped(True)
        profile.set_running(False)
        with self._lock:
            key = self._profile_key(profile)
            if key in self._threads:
                self._threads[key].join(timeout=10)
                del self._threads[key]
        logger.info("Stopped loop for %s", profile.client_phno)
        self._emit(profile, "stop", "Stopped profile scheduler.")

    def pause(self, profile: ProfileState) -> None:
        profile.set_paused(True)
        self._emit(profile, "pause", "Paused profile.")

    def resume(self, profile: ProfileState) -> None:
        profile.set_paused(False)
        self._emit(profile, "resume", "Resumed profile.")

    def close_profile(self, profile: ProfileState) -> None:
        """Stop loop and quit driver (close Chrome)."""
        self.stop_loop(profile)
        driver = profile.get_driver()
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
            profile.set_driver(None)
        logger.info("Closed profile %s", profile.client_phno)
        self._emit(profile, "profile_close", "Closed profile Chrome driver.")
