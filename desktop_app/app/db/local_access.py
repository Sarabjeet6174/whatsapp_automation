"""
MS Access storage for local/hybrid desktop mode.
"""
from __future__ import annotations

import json
from typing import Any

import pyodbc

from config import get_local_access_db_path


def _conn_str() -> str:
    db_path = get_local_access_db_path()
    return f"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={db_path};"


def get_conn() -> pyodbc.Connection:
    try:
        return pyodbc.connect(_conn_str(), autocommit=False)
    except Exception as e:
        raise RuntimeError(
            "Could not open local MS Access database. "
            "Install 'Microsoft Access Database Engine' and ensure local_store.accdb exists."
        ) from e


def _safe_create(cursor: pyodbc.Cursor, ddl: str) -> None:
    try:
        cursor.execute(ddl)
    except Exception:
        pass


def init_local_db() -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        _safe_create(
            cur,
            """
            CREATE TABLE local_profiles (
                id AUTOINCREMENT PRIMARY KEY,
                profile_name TEXT(100),
                profile_phone TEXT(50),
                created_at DATETIME
            )
            """,
        )
        _safe_create(
            cur,
            """
            CREATE TABLE local_contact_lists (
                id AUTOINCREMENT PRIMARY KEY,
                profile_id INTEGER,
                list_name TEXT(120),
                created_at DATETIME
            )
            """,
        )
        _safe_create(
            cur,
            """
            CREATE TABLE local_contacts (
                id AUTOINCREMENT PRIMARY KEY,
                profile_id INTEGER,
                contact_list_id INTEGER,
                contact_name TEXT(150),
                contact_phone TEXT(50),
                email TEXT(150),
                company TEXT(150),
                extra_json LONGTEXT
            )
            """,
        )
        _safe_create(
            cur,
            """
            CREATE TABLE local_templates (
                id AUTOINCREMENT PRIMARY KEY,
                profile_id INTEGER,
                template_name TEXT(120),
                template_content LONGTEXT,
                created_at DATETIME
            )
            """,
        )
        _safe_create(
            cur,
            """
            CREATE TABLE local_groups (
                id AUTOINCREMENT PRIMARY KEY,
                profile_id INTEGER,
                group_name TEXT(200),
                created_at DATETIME
            )
            """,
        )
        _safe_create(
            cur,
            """
            CREATE TABLE local_send_logs (
                id AUTOINCREMENT PRIMARY KEY,
                profile_id INTEGER,
                target_type TEXT(20),
                target_value TEXT(255),
                rendered_message LONGTEXT,
                status TEXT(20),
                error_text TEXT(500),
                created_at DATETIME
            )
            """,
        )
        conn.commit()
    finally:
        conn.close()


def fetch_local_profiles() -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, profile_name, profile_phone FROM local_profiles ORDER BY profile_name")
        rows = cur.fetchall()
        return [{"id": r.id, "name": r.profile_name or "", "phone": r.profile_phone or ""} for r in rows]
    finally:
        conn.close()


def create_local_profile(profile_name: str, profile_phone: str) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO local_profiles (profile_name, profile_phone, created_at) VALUES (?, ?, NOW())",
            (profile_name[:100], profile_phone[:50]),
        )
        conn.commit()
    finally:
        conn.close()


def fetch_contact_lists(profile_id: int) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, list_name FROM local_contact_lists WHERE profile_id=? ORDER BY list_name",
            (profile_id,),
        )
        rows = cur.fetchall()
        return [{"id": r.id, "name": r.list_name or ""} for r in rows]
    finally:
        conn.close()


def create_contact_list(profile_id: int, list_name: str) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO local_contact_lists (profile_id, list_name, created_at) VALUES (?, ?, NOW())",
            (profile_id, list_name[:120]),
        )
        conn.commit()
    finally:
        conn.close()


def fetch_contacts(profile_id: int, contact_list_id: int) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, contact_name, contact_phone, email, company, extra_json
            FROM local_contacts
            WHERE profile_id=? AND contact_list_id=?
            ORDER BY contact_name
            """,
            (profile_id, contact_list_id),
        )
        rows = cur.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            extra = {}
            try:
                extra = json.loads(r.extra_json) if r.extra_json else {}
            except Exception:
                extra = {}
            out.append(
                {
                    "id": r.id,
                    "name": r.contact_name or "",
                    "phone": r.contact_phone or "",
                    "email": r.email or "",
                    "company": r.company or "",
                    "extra": extra,
                }
            )
        return out
    finally:
        conn.close()


def create_contact(profile_id: int, contact_list_id: int, payload: dict[str, Any]) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO local_contacts (
                profile_id, contact_list_id, contact_name, contact_phone, email, company, extra_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                contact_list_id,
                str(payload.get("name", ""))[:150],
                str(payload.get("phone", ""))[:50],
                str(payload.get("email", ""))[:150],
                str(payload.get("company", ""))[:150],
                json.dumps(payload.get("extra", {}))[:5000],
            ),
        )
        conn.commit()
    finally:
        conn.close()


def delete_contacts(ids: list[int]) -> None:
    if not ids:
        return
    conn = get_conn()
    try:
        cur = conn.cursor()
        for cid in ids:
            cur.execute("DELETE FROM local_contacts WHERE id=?", (cid,))
        conn.commit()
    finally:
        conn.close()


def fetch_templates(profile_id: int) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, template_name, template_content FROM local_templates WHERE profile_id=? ORDER BY template_name",
            (profile_id,),
        )
        rows = cur.fetchall()
        return [{"id": r.id, "name": r.template_name or "", "content": r.template_content or ""} for r in rows]
    finally:
        conn.close()


def upsert_template(profile_id: int, template_name: str, template_content: str) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM local_templates WHERE profile_id=? AND template_name=?",
            (profile_id, template_name[:120]),
        )
        found = cur.fetchone()
        if found:
            cur.execute(
                "UPDATE local_templates SET template_content=? WHERE id=?",
                (template_content, found.id),
            )
        else:
            cur.execute(
                """
                INSERT INTO local_templates (profile_id, template_name, template_content, created_at)
                VALUES (?, ?, ?, NOW())
                """,
                (profile_id, template_name[:120], template_content),
            )
        conn.commit()
    finally:
        conn.close()


def fetch_groups(profile_id: int) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, group_name FROM local_groups WHERE profile_id=? ORDER BY group_name", (profile_id,))
        rows = cur.fetchall()
        return [{"id": r.id, "name": r.group_name or ""} for r in rows]
    finally:
        conn.close()


def create_group(profile_id: int, group_name: str) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO local_groups (profile_id, group_name, created_at) VALUES (?, ?, NOW())",
            (profile_id, group_name[:200]),
        )
        conn.commit()
    finally:
        conn.close()


def log_local_send(
    profile_id: int,
    target_type: str,
    target_value: str,
    rendered_message: str,
    status: str,
    error_text: str = "",
) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO local_send_logs (
                profile_id, target_type, target_value, rendered_message, status, error_text, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, NOW())
            """,
            (
                profile_id,
                target_type[:20],
                target_value[:255],
                rendered_message,
                status[:20],
                (error_text or "")[:500],
            ),
        )
        conn.commit()
    finally:
        conn.close()


def fetch_local_logs(profile_id: int, limit: int = 200) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT TOP ? id, target_type, target_value, rendered_message, status, error_text, created_at
            FROM local_send_logs
            WHERE profile_id=?
            ORDER BY id DESC
            """,
            (limit, profile_id),
        )
        rows = cur.fetchall()
        return [
            {
                "id": r.id,
                "target_type": r.target_type or "",
                "target_value": r.target_value or "",
                "rendered_message": r.rendered_message or "",
                "status": r.status or "",
                "error_text": r.error_text or "",
                "created_at": str(r.created_at or ""),
            }
            for r in rows
        ]
    finally:
        conn.close()


def delete_local_logs(profile_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM local_send_logs WHERE profile_id=?", (profile_id,))
        conn.commit()
    finally:
        conn.close()
