# Database tables required for WhatsApp Desktop / web_automation

The app connects to your SQL Server database (Gear Up) and uses the following tables.

---

## 1. **MST_CLIENT** (required)

Stores WhatsApp clients (one row per sender profile). The desktop app lists these for “Open Profile” / “Start”.

| Column         | Type          | Required | Description                          |
|----------------|---------------|----------|--------------------------------------|
| CLIENT_IDNO    | INT           | Yes      | Primary key / unique client id       |
| CLIENT_NAME    | NVARCHAR      | No       | Display name in the app              |
| CLIENT_PHNO    | NVARCHAR      | Yes      | Sender phone number (e.g. 7014671454). Must match `TMR_FROM_NO` in `TRAN_MSG_REQUEST` for messages to be picked up. |

**Example:**

```sql
-- Example rows (adjust types/lengths to match your DB)
INSERT INTO MST_CLIENT (CLIENT_IDNO, CLIENT_NAME, CLIENT_PHNO) VALUES (1, 'Client A', '7014671454');
INSERT INTO MST_CLIENT (CLIENT_IDNO, CLIENT_NAME, CLIENT_PHNO) VALUES (2, 'Client B', '8210422669');
```

---

## 2. **TRAN_MSG_REQUEST** (required)

Message queue. Rows with `TMR_STATUS = 'PENDING'` and `TMR_SCH_DTTIME < GETDATE()` are read and sent via WhatsApp. The app updates status and error fields after each send.

| Column          | Type          | Required | Description |
|-----------------|---------------|----------|-------------|
| TMR_IDNO        | INT           | Yes      | Primary key. Used in UPDATE after send. |
| TMR_FROM_NO     | NVARCHAR      | Yes      | Sender phone; must match `MST_CLIENT.CLIENT_PHNO` so the correct profile sends it. |
| TMR_TO_NO       | NVARCHAR      | Yes      | Recipient phone (for direct messages). |
| TMR_MSG         | NVARCHAR(MAX) | No       | Message text to send. |
| TMR_SCH_DTTIME  | DATETIME      | Yes      | When to send. Only rows with `TMR_SCH_DTTIME < GETDATE()` are processed. |
| TMR_STATUS      | NVARCHAR      | Yes      | `'PENDING'` = to be sent; app sets `'SENT'` or `'ERROR'` after processing. |
| TMR_GROUP_NAME  | NVARCHAR      | No       | If not NULL and not `'NA'`, message is sent to this WhatsApp group name instead of `TMR_TO_NO`. |
| TMR_SENT_TIME   | DATETIME      | No       | Set by app when status is updated to `'SENT'`. |
| TMR_ERR         | NVARCHAR(500) | No       | Set by app when status is updated to `'ERROR'` (e.g. send failed, group not found). |

**Example:**

```sql
-- Example: one PENDING message for client 7014671454
INSERT INTO TRAN_MSG_REQUEST (TMR_IDNO, TMR_FROM_NO, TMR_TO_NO, TMR_MSG, TMR_SCH_DTTIME, TMR_STATUS, TMR_GROUP_NAME)
VALUES (1, '7014671454', '9876543210', 'Hello', GETDATE(), 'PENDING', 'NA');
```

---

## 3. **APP_ERROR_LOG** (optional)

Used by the **desktop app** to log runtime/loop/selenium errors. If this table does not exist, errors are only logged to the console.

| Column      | Type           | Description |
|-------------|----------------|-------------|
| ID          | INT IDENTITY   | Primary key. |
| CLIENT_PHNO | NVARCHAR(50)   | Client phone for the profile that hit the error. |
| ERROR_TYPE  | NVARCHAR(50)   | e.g. `open_whatsapp`, `send_message`, `loop_crash`. |
| ERROR_TEXT  | NVARCHAR(500)  | Error message. |
| CREATED_DT  | DATETIME       | When the error was logged (default GETDATE()). |

**Create script:** run `desktop_app/scripts/create_app_error_log.sql` to create this table in your database.

---

## 4. **APP_ACTIVITY_LOG** (optional, recommended)

Stores UI/runtime activity logs that you can also see in the app screen.

| Column      | Type            | Description |
|-------------|-----------------|-------------|
| ID          | INT IDENTITY    | Primary key. |
| CLIENT_PHNO | NVARCHAR(50)    | Profile/client phone number. |
| EVENT_TYPE  | NVARCHAR(50)    | e.g. `start`, `pause`, `poll`, `message_sent`, `message_error`. |
| MESSAGE     | NVARCHAR(1000)  | Human-readable log message. |
| SOURCE      | NVARCHAR(50)    | Log source (e.g. `scheduler`, `message_loop`). |
| CREATED_DT  | DATETIME        | Log timestamp (default GETDATE()). |

**Create script:** run `desktop_app/scripts/create_app_activity_log.sql` to create this table.

---

## Summary

| Table               | Purpose                          | Required? |
|---------------------|----------------------------------|-----------|
| **MST_CLIENT**      | List of clients (profiles)       | **Yes**   |
| **TRAN_MSG_REQUEST**| Message queue (pending → sent)   | **Yes**   |
| **APP_ERROR_LOG**   | Desktop app error log            | No        |
| **APP_ACTIVITY_LOG**| UI/runtime activity log          | No (recommended) |

Your database name and connection are set in `.env`: `SQL_SERVER`, `SQL_DATABASE`, `SQL_USER`, `SQL_PASSWORD`.
