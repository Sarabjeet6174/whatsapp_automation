"""
Per-profile state: client info, driver, running/paused, and lock for thread safety.
"""
import threading
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ProfileState:
    client_idno: int
    client_name: str
    client_phno: str
    # Keep driver type generic here to avoid runtime imports of selenium chrome
    # submodules during EXE startup.
    driver: Optional[Any] = None
    running: bool = False
    paused: bool = False
    stopped: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def with_lock(self, fn):
        with self._lock:
            return fn()

    def set_running(self, value: bool) -> None:
        with self._lock:
            self.running = value

    def set_paused(self, value: bool) -> None:
        with self._lock:
            self.paused = value

    def set_stopped(self, value: bool) -> None:
        with self._lock:
            self.stopped = value

    def is_running(self) -> bool:
        with self._lock:
            return self.running

    def is_paused(self) -> bool:
        with self._lock:
            return self.paused

    def is_stopped(self) -> bool:
        with self._lock:
            return self.stopped

    def set_driver(self, d: Optional[Any]) -> None:
        with self._lock:
            self.driver = d

    def get_driver(self) -> Optional[Any]:
        with self._lock:
            return self.driver
