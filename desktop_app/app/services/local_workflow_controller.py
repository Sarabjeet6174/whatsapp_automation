"""
Tk-free local-mode orchestration: profile Chrome sessions, send queues, scheduled dispatch.
Qt UI (and optionally Tk later) use this to avoid duplicating worker logic.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from string import Formatter
from typing import Any, Callable

from app.core.profile_state import ProfileState
from app.core.scheduler import Scheduler
from app.db.local_access import (
    fetch_due_local_scheduled_jobs,
    log_local_send,
    mark_local_scheduled_job_dispatched,
    mark_local_scheduled_job_error,
)
from app.whatsapp.sender import cleanup_whatsapp_send_session, is_driver_alive, send_message

logger = logging.getLogger(__name__)

_SCHEDULE_POLL_IDLE_S = 30.0
_SCHEDULE_POLL_ACTIVE_S = 3.0
_SEND_PAUSE_POLL_S = 0.4


def render_message_template(template: str, contact: dict[str, Any], custom_vars: dict[str, str]) -> str:
    ex = contact.get("extra") or {}
    vals = {
        "name": contact.get("name", ""),
        "phone": contact.get("phone", ""),
        "email": contact.get("email", ""),
        "company": contact.get("company", ""),
    }
    for k, v in ex.items():
        vals[str(k)] = str(v)
    vals.update(custom_vars)
    out = template
    keys = {fname for _, fname, _, _ in Formatter().parse(template) if fname}
    for key in keys:
        out = out.replace("{" + key + "}", str(vals.get(key, "")))
    return out


@dataclass
class _ProfileSendControl:
    paused: threading.Event = field(default_factory=threading.Event)
    cancel: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def request_cancel(self) -> None:
        with self._lock:
            self.cancel = True

    def clear_cancel(self) -> None:
        with self._lock:
            self.cancel = False

    def is_cancelled(self) -> bool:
        with self._lock:
            return self.cancel


class LocalWorkflowController:
    """
    Holds ProfileState map, send queues, and scheduler — same role as the local-mode
    fields on the legacy MainWindow, without any UI toolkit.
    """

    def __init__(
        self,
        on_scheduler_log: Callable[[str, str, str], None] | None = None,
        on_schedule_due: Callable[[], None] | None = None,
    ) -> None:
        self.scheduler = Scheduler(on_log=on_scheduler_log)
        self.profile_by_phno: dict[str, ProfileState] = {}
        self.local_profiles: list[dict[str, Any]] = []

        self._local_send_queues: dict[str, "queue.Queue[dict[str, Any]]"] = {}
        self._local_send_workers_running: set[str] = set()
        self._local_send_lock = threading.Lock()
        self._send_controls: dict[str, _ProfileSendControl] = {}
        self._send_progress_handlers: dict[str, Callable[[str, str], None]] = {}

        self._local_schedule_worker_running = False
        self._local_schedule_lock = threading.Lock()
        self._on_schedule_due = on_schedule_due

    def sync_profile_list(self, profiles: list[dict[str, Any]]) -> None:
        self.local_profiles = list(profiles)

    def _send_control(self, profile_phone: str) -> _ProfileSendControl:
        key = str(profile_phone or "").strip()
        if key not in self._send_controls:
            self._send_controls[key] = _ProfileSendControl()
        return self._send_controls[key]

    def pending_send_count(self, profile_phone: str) -> int:
        with self._local_send_lock:
            q = self._local_send_queues.get(str(profile_phone or "").strip())
            if q is None:
                return 0
            return q.qsize()

    def is_send_queue_paused(self, profile_phone: str) -> bool:
        return self._send_control(profile_phone).paused.is_set()

    def pause_send_queue(self, profile_phone: str) -> None:
        self._send_control(profile_phone).paused.set()

    def resume_send_queue(self, profile_phone: str) -> None:
        self._send_control(profile_phone).paused.clear()

    def cancel_send_queue(self, profile_phone: str) -> int:
        """Drop pending jobs; in-flight job stops before the next recipient."""
        phone = str(profile_phone or "").strip()
        ctrl = self._send_control(phone)
        ctrl.request_cancel()
        drained = 0
        with self._local_send_lock:
            q = self._local_send_queues.get(phone)
            if q is not None:
                while True:
                    try:
                        q.get_nowait()
                        q.task_done()
                        drained += 1
                    except queue.Empty:
                        break
        return drained

    def set_send_progress_handler(
        self, profile_phone: str, handler: Callable[[str, str], None] | None
    ) -> None:
        key = str(profile_phone or "").strip()
        if handler is None:
            self._send_progress_handlers.pop(key, None)
        else:
            self._send_progress_handlers[key] = handler

    def _emit_send_progress(self, profile_phone: str, event_type: str, message: str) -> None:
        logger.info("[%s][%s] %s", profile_phone, event_type, message)
        handler = self._send_progress_handlers.get(str(profile_phone or "").strip())
        if handler:
            try:
                handler(event_type, message)
            except Exception:
                pass

    def ensure_local_profile_ready(
        self,
        profile_id: int,
        profile_phone: str,
        profile_name: str | None = None,
    ) -> tuple[ProfileState | None, str]:
        phone = str(profile_phone or "").strip()
        if not phone:
            return None, "Missing profile phone."
        state = self.profile_by_phno.get(phone)
        if state is None:
            resolved_name = (profile_name or "").strip()
            if not resolved_name:
                for lp in self.local_profiles:
                    if str(lp.get("phone", "")).strip() == phone:
                        resolved_name = str(lp.get("name", "")).strip()
                        break
            if not resolved_name:
                resolved_name = phone
            state = ProfileState(client_idno=int(profile_id), client_name=resolved_name, client_phno=phone)
            self.profile_by_phno[phone] = state
        if state.get_driver() is not None:
            if is_driver_alive(state.get_driver()):
                return state, ""
            try:
                state.set_driver(None)
            except Exception:
                state.set_driver(None)
        result = self.scheduler.open_profile(state)
        if result != "SUCCESS":
            return None, result
        return state, ""

    def enqueue_send_job(self, job: dict[str, Any]) -> None:
        queue_key = str(job["profile_phone"])
        self._send_control(queue_key).clear_cancel()
        with self._local_send_lock:
            if queue_key not in self._local_send_queues:
                self._local_send_queues[queue_key] = queue.Queue()
            self._local_send_queues[queue_key].put(job)
        self._ensure_local_send_worker(queue_key)

    def _ensure_local_send_worker(self, profile_phone: str) -> None:
        with self._local_send_lock:
            if profile_phone in self._local_send_workers_running:
                return
            self._local_send_workers_running.add(profile_phone)
            if profile_phone not in self._local_send_queues:
                self._local_send_queues[profile_phone] = queue.Queue()
        threading.Thread(target=self._local_send_worker_loop, args=(profile_phone,), daemon=True).start()

    def _wait_while_paused_or_cancelled(self, profile_phone: str, emit: Callable[[str, str], None]) -> bool:
        """Return False if cancelled; True when ready to continue."""
        ctrl = self._send_control(profile_phone)
        while ctrl.paused.is_set():
            if ctrl.is_cancelled():
                emit("queue_cancelled", "Send queue cancelled while paused.")
                ctrl.clear_cancel()
                return False
            time.sleep(_SEND_PAUSE_POLL_S)
        if ctrl.is_cancelled():
            emit("queue_cancelled", "Send queue cancelled.")
            ctrl.clear_cancel()
            return False
        return True

    def _try_release_driver_if_idle(self, profile_phone: str) -> None:
        """Keep Chrome open after sends so the WhatsApp session stays logged in."""
        return

    def _local_send_worker_loop(self, profile_phone: str) -> None:
        while True:
            with self._local_send_lock:
                q = self._local_send_queues.get(profile_phone)
            if q is None:
                return
            try:
                job = q.get(timeout=1.0)
            except queue.Empty:
                with self._local_send_lock:
                    q2 = self._local_send_queues.get(profile_phone)
                    if q2 is q and q.empty():
                        self._local_send_workers_running.discard(profile_phone)
                        return
                continue
            try:
                self.run_local_send_job(job)
            finally:
                q.task_done()

    def run_local_send_job(self, job: dict[str, Any]) -> None:
        profile_phone = str(job["profile_phone"])
        profile_id = int(job["profile_id"])
        target_mode = str(job["target_mode"])
        ctrl = self._send_control(profile_phone)

        def emit_local(event_type: str, message: str) -> None:
            self._emit_send_progress(profile_phone, event_type, message)

        items = list(job.get("items", []) or [])
        emit_local("queue_started", f"Sending {len(items)} message(s)…")

        if not self._wait_while_paused_or_cancelled(profile_phone, emit_local):
            emit_local("queue_finished", "Send operation cancelled.")
            return

        state, open_err = self.ensure_local_profile_ready(
            profile_id=profile_id,
            profile_phone=profile_phone,
            profile_name=str(job.get("profile_name", "")),
        )
        if state is None:
            emit_local("queue_error", f"Skipped queued send for {profile_phone}: profile auto-open failed: {open_err}")
            for item in job.get("items", []):
                item_type = str(item.get("item_type") or ("group" if target_mode == "group" else "contact"))
                target_type = "group" if item_type == "group" else "contact"
                target_value = str(item.get("receiver", ""))
                rendered = str(item.get("rendered", ""))
                try:
                    log_local_send(
                        profile_id,
                        target_type,
                        target_value,
                        rendered,
                        "ERROR",
                        f"Profile auto-open failed: {open_err}",
                    )
                except Exception:
                    pass
            emit_local("queue_finished", "Send operation finished.")
            return
        driver = state.get_driver()
        if driver is None:
            emit_local("queue_error", f"Skipped queued send for {profile_phone}: driver unavailable after open.")
            for item in job.get("items", []):
                item_type = str(item.get("item_type") or ("group" if target_mode == "group" else "contact"))
                target_type = "group" if item_type == "group" else "contact"
                target_value = str(item.get("receiver", ""))
                rendered = str(item.get("rendered", ""))
                try:
                    log_local_send(
                        profile_id,
                        target_type,
                        target_value,
                        rendered,
                        "ERROR",
                        "Driver unavailable after opening profile",
                    )
                except Exception:
                    pass
            emit_local("queue_finished", "Send operation finished.")
            return

        att = [str(x) for x in (job.get("attachment_paths") or []) if x]
        att_kw = att or None
        attachment_only = bool(job.get("attachment_only_no_caption", False))

        for item in job.get("items", []):
            if not self._wait_while_paused_or_cancelled(profile_phone, emit_local):
                break
            item_type = str(item.get("item_type") or ("group" if target_mode == "group" else "contact"))
            is_group = item_type == "group"
            receiver = str(item.get("receiver", ""))
            rendered = str(item.get("rendered", ""))
            name = str(item.get("name", ""))
            if is_group:
                receiver = receiver.strip()
                if not receiver:
                    emit_local("group_send_error", "Group not selected.")
                    continue
            out_msg = "" if (attachment_only and att_kw) else rendered
            try:
                result = send_message(
                    driver,
                    receiver_identifier=receiver,
                    message=out_msg,
                    is_group=is_group,
                    allow_search=False,
                    attachment_paths=att_kw,
                    progress=lambda msg: emit_local("send_ui", msg),
                )
                if result == "SUCCESS":
                    try:
                        log_local_send(profile_id, "group" if is_group else "contact", receiver, out_msg, "SENT", "")
                    except Exception as e:
                        emit_local("log_error", f"{'Group' if is_group else 'Contact'} log write failed: {e}")
                    emit_local("group_sent" if is_group else "contact_sent", f"{name} ({receiver})")
                    if att_kw and len(job.get("items", [])) > 1:
                        time.sleep(1.0)
                else:
                    try:
                        log_local_send(profile_id, "group" if is_group else "contact", receiver, out_msg, "ERROR", result)
                    except Exception as e:
                        emit_local("log_error", f"{'Group' if is_group else 'Contact'} log write failed: {e}")
                    emit_local("group_error" if is_group else "contact_error", f"{name} ({receiver}): {result}")
            except Exception as e:
                emit_local("group_exception" if is_group else "contact_exception", f"{name} ({receiver}): {e}")

        ctrl.clear_cancel()
        if att_kw and driver is not None:
            try:
                cleanup_whatsapp_send_session(driver)
            except Exception:
                pass
        emit_local("queue_finished", "Send operation finished.")

    def ensure_schedule_worker(self) -> None:
        with self._local_schedule_lock:
            if self._local_schedule_worker_running:
                return
            self._local_schedule_worker_running = True
        threading.Thread(target=self._local_schedule_worker_loop, daemon=True).start()

    def _local_schedule_worker_loop(self) -> None:
        while True:
            due_jobs: list[dict[str, Any]] = []
            try:
                due_jobs = fetch_due_local_scheduled_jobs(datetime.now(), limit=40)
                for sched in due_jobs:
                    sid = int(sched.get("id", 0))
                    payload = sched.get("payload") or {}
                    if sid <= 0:
                        continue
                    profile_phone = str(payload.get("profile_phone", "")).strip()
                    if not profile_phone:
                        mark_local_scheduled_job_error(sid, "Missing profile phone in scheduled payload")
                        continue
                    mark_local_scheduled_job_dispatched(sid)
                    with self._local_send_lock:
                        if profile_phone not in self._local_send_queues:
                            self._local_send_queues[profile_phone] = queue.Queue()
                    self._local_send_queues[profile_phone].put(payload)
                    self._ensure_local_send_worker(profile_phone)
                if due_jobs and self._on_schedule_due:
                    try:
                        self._on_schedule_due()
                    except Exception:
                        pass
            except Exception as e:
                logger.error("Local schedule worker loop error: %s", e)
            time.sleep(_SCHEDULE_POLL_ACTIVE_S if due_jobs else _SCHEDULE_POLL_IDLE_S)


__all__ = [
    "LocalWorkflowController",
    "render_message_template",
]
