"""
MS Access storage for local/hybrid desktop mode.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any

import pyodbc

from config import get_local_access_db_path


def _conn_str() -> str:
    db_path = get_local_access_db_path()
    return f"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={db_path};"


def _create_access_db_file(db_path: str) -> None:
    """Create an empty .accdb file using ADOX COM via PowerShell."""
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    safe_db_path = db_path.replace("'", "''")
    ps_script = (
        "$ErrorActionPreference='Stop';"
        f"$path='{safe_db_path}';"
        "$catalog=New-Object -ComObject ADOX.Catalog;"
        "$catalog.Create(\"Provider=Microsoft.ACE.OLEDB.12.0;Data Source=\" + $path + \";\");"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
        check=True,
        capture_output=True,
        text=True,
    )


def _ensure_local_db_exists() -> None:
    db_path = get_local_access_db_path()
    if os.path.isfile(db_path):
        return
    try:
        _create_access_db_file(db_path)
    except Exception as e:
        raise RuntimeError(
            "Could not create local_store.accdb automatically. "
            "Install 'Microsoft Access Database Engine' and run app as normal user with write access."
        ) from e
    if not os.path.isfile(db_path):
        raise RuntimeError("local_store.accdb was not created. Please check folder write permissions.")


def get_conn() -> pyodbc.Connection:
    _ensure_local_db_exists()
    try:
        return pyodbc.connect(_conn_str(), autocommit=False)
    except Exception as e:
        raise RuntimeError(
            "Could not open local MS Access database. "
            "Install 'Microsoft Access Database Engine' and ensure the Access ODBC driver is available."
        ) from e


def _safe_create(cursor: pyodbc.Cursor, ddl: str) -> None:
    try:
        cursor.execute(ddl)
    except Exception:
        pass


def _is_table_missing_error(err: Exception) -> bool:
    s = str(err).lower()
    return "42s02" in s or "cannot find the input table or query" in s


DEFAULT_LIST_FIELDS: list[str] = ["name", "phone", "email", "company"]


def _ensure_contact_lists_fields_json(conn: pyodbc.Connection) -> None:
    cur = conn.cursor()
    try:
        cur.execute("ALTER TABLE local_contact_lists ADD COLUMN fields_json MEMO")
        conn.commit()
    except Exception:
        pass


def _parse_list_fields_json(raw: Any) -> list[str]:
    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
        return list(DEFAULT_LIST_FIELDS)
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(data, list) and data:
            out = [str(x).strip() for x in data if str(x).strip()]
            if "name" in {x.lower() for x in out} and "phone" in {x.lower() for x in out}:
                return out
    except Exception:
        pass
    return list(DEFAULT_LIST_FIELDS)


def _ensure_local_send_logs_table(conn: pyodbc.Connection) -> None:
    """Create local_send_logs using multiple Access-compatible DDL variants and verify."""
    cur = conn.cursor()
    ddls = [
        """
        CREATE TABLE local_send_logs (
            id AUTOINCREMENT PRIMARY KEY,
            profile_id INTEGER,
            target_type TEXT(20),
            target_value TEXT(255),
            rendered_message MEMO,
            log_status TEXT(20),
            error_text MEMO,
            created_at DATETIME
        )
        """,
        """
        CREATE TABLE local_send_logs (
            id COUNTER PRIMARY KEY,
            profile_id INTEGER,
            target_type TEXT(20),
            target_value TEXT(255),
            rendered_message MEMO,
            log_status TEXT(20),
            error_text MEMO,
            created_at DATETIME
        )
        """,
        # Backward-compatible schema with [status] column.
        """
        CREATE TABLE local_send_logs (
            id COUNTER PRIMARY KEY,
            profile_id INTEGER,
            target_type TEXT(20),
            target_value TEXT(255),
            rendered_message MEMO,
            [status] TEXT(20),
            error_text MEMO,
            created_at DATETIME
        )
        """,
    ]

    # First check if table already exists.
    try:
        cur.execute("SELECT TOP 1 id FROM local_send_logs")
        return
    except Exception:
        pass

    last_err: Exception | None = None
    for ddl in ddls:
        try:
            cur.execute(ddl)
            conn.commit()
        except Exception as e:
            last_err = e
        # Verify after each attempt.
        try:
            cur.execute("SELECT TOP 1 id FROM local_send_logs")
            return
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(
        "Could not create required table 'local_send_logs' in local_store.accdb."
    ) from last_err


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
                extra_json MEMO
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
                template_content MEMO,
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
                rendered_message MEMO,
                log_status TEXT(20),
                error_text MEMO,
                created_at DATETIME
            )
            """,
        )
        _ensure_local_send_logs_table(conn)
        _ensure_contact_lists_fields_json(conn)
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


def delete_local_profile(profile_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM local_contacts WHERE profile_id=?", (profile_id,))
        cur.execute("DELETE FROM local_contact_lists WHERE profile_id=?", (profile_id,))
        cur.execute("DELETE FROM local_templates WHERE profile_id=?", (profile_id,))
        cur.execute("DELETE FROM local_groups WHERE profile_id=?", (profile_id,))
        cur.execute("DELETE FROM local_send_logs WHERE profile_id=?", (profile_id,))
        cur.execute("DELETE FROM local_profiles WHERE id=?", (profile_id,))
        conn.commit()
    finally:
        conn.close()


def fetch_contact_lists(profile_id: int) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT id, list_name, fields_json FROM local_contact_lists WHERE profile_id=? ORDER BY list_name",
                (profile_id,),
            )
            rows = cur.fetchall()
            return [
                {
                    "id": r.id,
                    "name": r.list_name or "",
                    "fields": _parse_list_fields_json(getattr(r, "fields_json", None)),
                }
                for r in rows
            ]
        except Exception:
            cur.execute(
                "SELECT id, list_name FROM local_contact_lists WHERE profile_id=? ORDER BY list_name",
                (profile_id,),
            )
            rows = cur.fetchall()
            return [{"id": r.id, "name": r.list_name or "", "fields": list(DEFAULT_LIST_FIELDS)} for r in rows]
    finally:
        conn.close()


def create_contact_list(profile_id: int, list_name: str, fields: list[str] | None = None) -> None:
    conn = get_conn()
    try:
        _ensure_contact_lists_fields_json(conn)
        cur = conn.cursor()
        field_list = fields if fields else list(DEFAULT_LIST_FIELDS)
        fj = json.dumps(field_list)[:8000]
        cur.execute(
            """
            INSERT INTO local_contact_lists (profile_id, list_name, created_at, fields_json)
            VALUES (?, ?, NOW(), ?)
            """,
            (profile_id, list_name[:120], fj),
        )
        conn.commit()
    finally:
        conn.close()


def rename_contact_list(profile_id: int, list_id: int, new_name: str) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE local_contact_lists SET list_name=? WHERE id=? AND profile_id=?",
            (new_name[:120], list_id, profile_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_contact_list_fields(profile_id: int, list_id: int, fields: list[str]) -> None:
    conn = get_conn()
    try:
        _ensure_contact_lists_fields_json(conn)
        cur = conn.cursor()
        cur.execute(
            "UPDATE local_contact_lists SET fields_json=? WHERE id=? AND profile_id=?",
            (json.dumps(fields)[:8000], list_id, profile_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_contact_list(profile_id: int, list_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM local_contacts WHERE contact_list_id=? AND profile_id=?", (list_id, profile_id))
        cur.execute("DELETE FROM local_contact_lists WHERE id=? AND profile_id=?", (list_id, profile_id))
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


def rename_template(profile_id: int, template_id: int, new_name: str) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM local_templates WHERE profile_id=? AND template_name=? AND id<>?",
            (profile_id, new_name[:120], template_id),
        )
        if cur.fetchone():
            raise ValueError("A template with that name already exists for this profile.")
        cur.execute(
            "UPDATE local_templates SET template_name=? WHERE id=? AND profile_id=?",
            (new_name[:120], template_id, profile_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_template(profile_id: int, template_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM local_templates WHERE id=? AND profile_id=?", (template_id, profile_id))
        conn.commit()
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
        params = (
            profile_id,
            target_type[:20],
            target_value[:255],
            rendered_message,
            status[:20],
            (error_text or "")[:500],
        )
        try:
            # Preferred schema (new).
            cur.execute(
                """
                INSERT INTO local_send_logs (
                    profile_id, target_type, target_value, rendered_message, log_status, error_text, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, NOW())
                """,
                params,
            )
        except Exception:
            # Backward-compatibility with old schema that used reserved word [status].
            try:
                cur.execute(
                    """
                    INSERT INTO local_send_logs (
                        profile_id, target_type, target_value, rendered_message, [status], error_text, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NOW())
                    """,
                    params,
                )
            except Exception as e2:
                if _is_table_missing_error(e2):
                    conn.close()
                    init_local_db()
                    conn2 = get_conn()
                    try:
                        cur2 = conn2.cursor()
                        cur2.execute(
                            """
                            INSERT INTO local_send_logs (
                                profile_id, target_type, target_value, rendered_message, log_status, error_text, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, NOW())
                            """,
                            params,
                        )
                        conn2.commit()
                        return
                    finally:
                        conn2.close()
                raise
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def fetch_local_logs(profile_id: int, limit: int = 200) -> list[dict[str, Any]]:
    conn = get_conn()
    try:
        cur = conn.cursor()
        safe_limit = max(1, min(int(limit), 1000))
        try:
            cur.execute(
                f"""
                SELECT TOP {safe_limit} id, target_type, target_value, rendered_message, log_status AS status, error_text, created_at
                FROM local_send_logs
                WHERE profile_id=?
                ORDER BY id DESC
                """,
                (profile_id,),
            )
        except Exception:
            # Backward-compatibility with old schema that used reserved word [status].
            try:
                cur.execute(
                    f"""
                    SELECT TOP {safe_limit} id, target_type, target_value, rendered_message, [status] AS status, error_text, created_at
                    FROM local_send_logs
                    WHERE profile_id=?
                    ORDER BY id DESC
                    """,
                    (profile_id,),
                )
            except Exception as e2:
                if _is_table_missing_error(e2):
                    conn.close()
                    init_local_db()
                    conn2 = get_conn()
                    try:
                        cur2 = conn2.cursor()
                        cur2.execute(
                            f"""
                            SELECT TOP {safe_limit} id, target_type, target_value, rendered_message, log_status AS status, error_text, created_at
                            FROM local_send_logs
                            WHERE profile_id=?
                            ORDER BY id DESC
                            """,
                            (profile_id,),
                        )
                        rows = cur2.fetchall()
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
                        conn2.close()
                raise
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
        try:
            conn.close()
        except Exception:
            pass


def delete_local_logs(profile_id: int) -> None:
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM local_send_logs WHERE profile_id=?", (profile_id,))
        conn.commit()
    finally:
        conn.close()
