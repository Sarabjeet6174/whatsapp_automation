"""
SQL Server access for desktop app. Uses .env from repo root.
"""
from __future__ import annotations

import os
from typing import Any

import pyodbc
from dotenv import load_dotenv

from config import get_env_path

load_dotenv(get_env_path())


def get_conn() -> pyodbc.Connection:
    server = os.getenv("SQL_SERVER")
    database = os.getenv("SQL_DATABASE")
    user = os.getenv("SQL_USER")
    password = os.getenv("SQL_PASSWORD")
    if not all([server, database, user, password]):
        raise RuntimeError(
            "SQL env vars missing. Set SQL_SERVER, SQL_DATABASE, SQL_USER, SQL_PASSWORD."
        )
    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={server};DATABASE={database};UID={user};PWD={password};"
        "Encrypt=yes;TrustServerCertificate=yes;"
    )
    return pyodbc.connect(conn_str)


def fetch_clients() -> list[dict[str, Any]]:
    """Return list of clients from MST_CLIENT for profile selection."""
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT CLIENT_IDNO, CLIENT_NAME, CLIENT_PHNO FROM MST_CLIENT WITH (NOLOCK) ORDER BY CLIENT_IDNO"
        )
        rows = cursor.fetchall()
        return [
            {
                "client_idno": r.CLIENT_IDNO,
                "client_name": getattr(r, "CLIENT_NAME", "") or "",
                "client_phno": str(r.CLIENT_PHNO or "").strip(),
            }
            for r in rows
        ]
    finally:
        conn.close()


def fetch_pending_for_client(client_phno: str) -> list[dict[str, Any]]:
    """Fetch PENDING messages for this sender (TMR_FROM_NO = client_phno)."""
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                TMR_IDNO, TMR_FROM_NO, TMR_TO_NO, TMR_MSG,
                TMR_SCH_DTTIME, TMR_STATUS,
                ISNULL(TMR_GROUP_NAME, 'NA') AS TMR_GROUP_NAME
            FROM TRAN_MSG_REQUEST
            WHERE TMR_STATUS = 'PENDING'
              AND TMR_SCH_DTTIME < GETDATE()
              AND TMR_FROM_NO IS NOT NULL
              AND TMR_TO_NO IS NOT NULL
              AND TMR_FROM_NO = ?
            ORDER BY TMR_SCH_DTTIME
            """,
            (client_phno,),
        )
        rows = cursor.fetchall()
        return [
            {
                "client_idno": None,
                "tmr_idno": r.TMR_IDNO,
                "from_no": r.TMR_FROM_NO,
                "to_no": r.TMR_TO_NO,
                "msg": r.TMR_MSG or "",
                "group_name": getattr(r, "TMR_GROUP_NAME", "NA") or "NA",
            }
            for r in rows
        ]
    finally:
        conn.close()


def update_status_sent(tmr_idno: int) -> None:
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE TRAN_MSG_REQUEST SET TMR_STATUS='SENT', TMR_SENT_TIME=GETDATE() WHERE TMR_IDNO = ?",
            (tmr_idno,),
        )
        conn.commit()
    finally:
        conn.close()


def update_status_error(tmr_idno: int, error_text: str) -> None:
    err = (error_text or "")[:500]
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE TRAN_MSG_REQUEST SET TMR_STATUS='ERROR', TMR_ERR = ? WHERE TMR_IDNO = ?",
            (err, tmr_idno),
        )
        conn.commit()
    finally:
        conn.close()


def log_app_error(client_phno: str, error_type: str, error_text: str) -> None:
    """Log runtime/loop/element/selenium errors to APP_ERROR_LOG if table exists."""
    err = (error_text or "")[:500]
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO APP_ERROR_LOG (CLIENT_PHNO, ERROR_TYPE, ERROR_TEXT, CREATED_DT) VALUES (?, ?, ?, GETDATE())",
            (str(client_phno)[:50], (error_type or "runtime")[:50], err),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def log_app_activity(
    client_phno: str,
    event_type: str,
    message: str,
    source: str = "desktop_app",
) -> None:
    """
    Log app activity/events to APP_ACTIVITY_LOG if table exists.
    Intended for UI-visible + runtime trace logs.
    """
    conn = get_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO APP_ACTIVITY_LOG (CLIENT_PHNO, EVENT_TYPE, MESSAGE, SOURCE, CREATED_DT)
            VALUES (?, ?, ?, ?, GETDATE())
            """,
            (
                str(client_phno or "")[:50],
                str(event_type or "event")[:50],
                str(message or "")[:1000],
                str(source or "desktop_app")[:50],
            ),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()
