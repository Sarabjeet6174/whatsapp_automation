"""
Per-profile message loop: fetch PENDING for client, send one by one, delay, never stop on one error.
If loop crashes, log and restart. Runs in a thread per profile.
"""
import logging
import random
import time
from typing import Callable, Optional

from app.db.sql import (
    fetch_pending_for_client,
    update_status_sent,
    update_status_error,
    log_app_error,
    log_app_activity,
)
from app.whatsapp.sender import send_message, create_driver_for_profile, open_whatsapp_web
from app.core.profile_state import ProfileState

logger = logging.getLogger(__name__)

DELAY_LOW = 4
DELAY_HIGH = 13
SCHEDULER_INTERVAL = 15
PAUSE_POLL = 5


def _delay_between_messages() -> None:
    time.sleep(DELAY_LOW + (DELAY_HIGH - DELAY_LOW) * random.random())


def run_loop_for_profile(
    profile: ProfileState,
    on_log: Optional[Callable[[str, str, str], None]] = None,
) -> None:
    """
    Run indefinitely until stopped. Each iteration: if paused wait; fetch pending;
    send one by one (reuse driver); on any exception log and continue; sleep SCHEDULER_INTERVAL
    between DB polls (empty queue, after batch, driver errors, loop crash).
    If driver is None and we need to send, create and open WhatsApp.
    """
    def emit(event_type: str, message: str) -> None:
        log_app_activity(profile.client_phno, event_type, message, source="message_loop")
        if on_log:
            try:
                on_log(profile.client_phno, event_type, message)
            except Exception:
                pass

    emit("loop_start", "Profile loop started.")
    while profile.is_running() and not profile.is_stopped():
        try:
            while profile.is_paused() and profile.is_running() and not profile.is_stopped():
                time.sleep(PAUSE_POLL)

            if profile.is_stopped() or not profile.is_running():
                break

            client_phno = profile.client_phno
            rows = fetch_pending_for_client(client_phno)
            if not rows:
                emit(
                    "poll",
                    f"No pending messages. Sleeping for {SCHEDULER_INTERVAL}s.",
                )
                time.sleep(SCHEDULER_INTERVAL)
                continue
            emit("poll", f"Fetched {len(rows)} pending message(s).")

            driver = profile.get_driver()
            if driver is None:
                try:
                    driver = create_driver_for_profile(client_phno)
                    profile.set_driver(driver)
                    emit("driver", "Created profile Chrome driver.")
                    res = open_whatsapp_web(driver)
                    if res != "SUCCESS":
                        emit("driver_error", f"Open WhatsApp failed: {res}")
                        log_app_error(client_phno, "open_whatsapp", res)
                        time.sleep(SCHEDULER_INTERVAL)
                        continue
                    emit("driver", "WhatsApp Web opened successfully.")
                except Exception as e:
                    emit("driver_error", f"Driver create failed: {e}")
                    log_app_error(client_phno, "driver_create", str(e)[:500])
                    time.sleep(SCHEDULER_INTERVAL)
                    continue

            for row in rows:
                if profile.is_stopped() or not profile.is_running():
                    break
                while profile.is_paused() and profile.is_running() and not profile.is_stopped():
                    time.sleep(PAUSE_POLL)

                tmr_idno = row["tmr_idno"]
                to_no = row["to_no"]
                msg_text = row["msg"] or ""
                group_name = row["group_name"] or "NA"
                is_group = group_name != "NA"
                target = group_name if is_group else str(to_no)
                emit(
                    "message_start",
                    f"Processing TMR_IDNO={tmr_idno}, target={'group' if is_group else 'number'}:{target}",
                )

                try:
                    result = send_message(
                        driver,
                        receiver_identifier=target,
                        message=msg_text,
                        is_group=is_group,
                    )
                    if result == "SUCCESS":
                        update_status_sent(tmr_idno)
                        emit("message_sent", f"TMR_IDNO={tmr_idno} marked SENT.")
                    else:
                        update_status_error(tmr_idno, result)
                        log_app_error(client_phno, "send_message", result)
                        emit("message_error", f"TMR_IDNO={tmr_idno} marked ERROR: {result}")
                except Exception as e:
                    err = str(e)[:500]
                    update_status_error(tmr_idno, err)
                    log_app_error(client_phno, "send_exception", err)
                    emit("message_exception", f"TMR_IDNO={tmr_idno} exception: {err}")

                _delay_between_messages()

            emit(
                "poll",
                f"Batch complete. Sleeping for {SCHEDULER_INTERVAL}s.",
            )
            time.sleep(SCHEDULER_INTERVAL)
        except Exception as e:
            logger.exception("Loop crash for %s", profile.client_phno)
            log_app_error(profile.client_phno, "loop_crash", str(e)[:500])
            emit("loop_crash", f"Loop crash recovered: {e}")
            time.sleep(SCHEDULER_INTERVAL)
    emit("loop_stop", "Profile loop stopped.")
