# WhatsApp Desktop – Multi-Profile Sender

Desktop application with 3 run modes:
- **local** (default): local profiles, contact lists, templates, groups, and logs stored in local MS Access DB
- **sql**: existing SQL scheduler mode only
- **hybrid**: both local + SQL tabs

Can be built as a **Windows .exe** that includes Python inside it, so users never have to install Python (or pip) on their PC.

## What you need to install (on the PC where the app runs)

| Requirement | Run with Python | Run with .exe |
|-------------|------------------|----------------|
| **Google Chrome** | Yes | Yes |
| **ODBC Driver 18 for SQL Server** (SQL / Hybrid modes) | Yes | Yes |
| **Microsoft Access Database Engine x64** (Local / Hybrid modes) | Yes | Yes |
| **.env file** (DB credentials) | Yes (repo root or next to script) | Optional: bundle it in the .exe when building, or put next to the .exe |
| **Python 3** | Yes | **No** – the .exe has Python built in |
| **pip packages** (pyodbc, selenium, etc.) | Yes | **No** – bundled inside the .exe |

- **Chrome**: [Download Chrome](https://www.google.com/chrome/) if not already installed. The app opens WhatsApp Web in Chrome.
- **ODBC Driver 18**: Required for the app to connect to your SQL Server. You can install it in two ways from the app:
  - **Help → Install ODBC Driver (open download page)** – opens Microsoft’s page in your browser; download and run the installer yourself.
  - **Help → Download and run ODBC installer** – the app downloads the official Microsoft MSI (from a stable URL) and starts the installer. You may see a UAC (admin) prompt; complete the setup in the installer window, then restart the app. This can fail on locked-down or offline PCs; in that case use the “open download page” option instead.
- **Microsoft Access Database Engine (x64)**: Required for local mode storage.
  - Download page: [Access Database Engine 2016 Redistributable](https://www.microsoft.com/en-us/download/details.aspx?id=54920)
  - Direct file (x64): [accessdatabaseengine_X64.exe](https://download.microsoft.com/download/3/5/C/35C84C36-661A-44E6-9324-8786B8DBE231/accessdatabaseengine_X64.exe)
- **Local DB file**: Local mode uses `local_store.accdb` in the app base folder (next to exe in frozen mode; project base when running as script).
- **.env**: Create a file named `.env` in the right place (see Setup / Option B) with:
  ```
  SQL_SERVER=your_server
  SQL_DATABASE=GearUp
  SQL_USER=your_user
  SQL_PASSWORD=your_password
  ```

No other software (e.g. SQL Server Management Studio, Node.js, etc.) is required on the user’s PC.

---

## Features

- **Mode-based desktop UI**: Local, SQL, and Hybrid runs.
- **Multi-profile**: Each client has its own Chrome profile (`desktop_app/chrome_profiles/<phone>`). Run multiple profiles at the same time.
- **Local mode data**: Store local profiles, contact lists, contacts, templates, groups, and send logs in MS Access.
- **Template variables**: Replace placeholders like `{name}` with contact fields (`name`, `phone`, `email`, `company`, CSV extra columns) and custom key/value variables.
- **Local send options**: Send to selected contacts, all contacts, or selected group.
- **Internal scheduler**: For each running profile, the app checks the DB every **15 seconds** and sends PENDING messages for that client. No manual trigger.
- **Pause / Resume**: Pause or resume one profile or all. Same Chrome tab is reused.
- **Error handling**: If a message fails, error is saved in DB (`TMR_STATUS='ERROR'`, `TMR_ERR`). Loop continues with the next message. Group search timeout 20s; if group not found, error is logged and processing continues.
- **Recovery**: If the loop crashes, it restarts automatically after 15 seconds. Scheduler never stops due to one error.
- **Group vs number**: If both group name and number exist, message is sent to the **group**. Group search is limited to 20 seconds.
- **Phone number routing**: By default, direct-number messages open the chat with `https://web.whatsapp.com/send?phone=…` only (no side search). Enable **Allow side search for phone numbers** in the UI to use the WhatsApp search box first (then the send link if needed). Optional `.env`: `ALLOW_SEARCH=true` pre-checks that option on startup.
- **Direct number fallback**: If the contact is not found in the WhatsApp Web side search (including the “No chats, contacts or messages found” state), the same Chrome tab navigates to `https://web.whatsapp.com/send?phone=<number>` (digits only, international format as stored) so the session is reused and extra tabs are not opened; if the chat still cannot open, the row is marked ERROR.

## Database tables required

The app expects these tables in your SQL Server database (see **`scripts/DATABASE_TABLES.md`** for full column list and examples):

| Table | Purpose |
|-------|--------|
| **MST_CLIENT** | Clients (profiles): `CLIENT_IDNO`, `CLIENT_NAME`, `CLIENT_PHNO`. |
| **TRAN_MSG_REQUEST** | Message queue: `TMR_IDNO`, `TMR_FROM_NO`, `TMR_TO_NO`, `TMR_MSG`, `TMR_SCH_DTTIME`, `TMR_STATUS`, `TMR_GROUP_NAME`; app updates `TMR_STATUS` ('SENT'/'ERROR'), `TMR_SENT_TIME`, `TMR_ERR`. |
| **APP_ERROR_LOG** | Optional. Desktop app error log; create with `scripts/create_app_error_log.sql` if you want it. |
| **APP_ACTIVITY_LOG** | Optional (recommended). UI + runtime activity logs (also shown in app); create with `scripts/create_app_activity_log.sql`. |

---

## Setup

1. Use the same `.env` as the main project (in repo root: `SQL_SERVER`, `SQL_DATABASE`, `SQL_USER`, `SQL_PASSWORD`).
2. Install ODBC Driver 18 for SQL Server if not already.
3. From repo root:
   ```bash
   pip install -r desktop_app/requirements_desktop.txt
   ```
4. (Optional) Create table for app-level error logging (runtime/loop/selenium errors):
   ```sql
   CREATE TABLE APP_ERROR_LOG (
     ID INT IDENTITY(1,1) PRIMARY KEY,
     CLIENT_PHNO NVARCHAR(50),
     ERROR_TYPE NVARCHAR(50),
     ERROR_TEXT NVARCHAR(500),
     CREATED_DT DATETIME DEFAULT GETDATE()
   );
   ```
   If the table does not exist, errors are only logged to the console.
5. (Optional, recommended) Create table for UI/runtime activity logs:
   ```sql
   -- Run script: desktop_app/scripts/create_app_activity_log.sql
   ```

## Run

**Option A – Python (from repo root):**

```bash
python desktop_app/main_desktop.py
```
Default mode is **local** (MS Access-backed local profiles/contact lists/templates/logs).

To run in other modes:

```bash
python desktop_app/main_desktop.py hybrid   # local + existing SQL mode
python desktop_app/main_desktop.py sql      # existing SQL mode only
```

Or from `desktop_app`:

```bash
cd desktop_app
python main_desktop.py
```

**Option B – Windows .exe (standalone: Python is inside the .exe)**

The built `.exe` includes the Python runtime and all dependencies. Users do **not** need to install Python (or pip) on their PC—just copy the .exe and add a `.env` file.

1. **Build the .exe once** (on a machine that has Python, only for building):

   ```bash
   cd desktop_app
   pip install -r requirements_desktop.txt
   pip install pyinstaller
   pyinstaller --noconfirm whatsapp_desktop.spec
   ```

   Or double‑click `build_exe.bat` (run from inside `desktop_app`). If the build fails, open **`desktop_app/build_last.log`**—PyInstaller output is saved there and Notepad may open automatically.

2. You get **`desktop_app\dist\WhatsAppDesktop.exe`**.

3. **Include .env in the .exe (recommended):** Before building, put a `.env` file in the `desktop_app` folder (or in the repo root) with your DB settings. The build will **bundle it into the .exe**, so you don’t need a separate .env on the target PC. If you prefer not to bundle (e.g. different credentials per PC), leave .env out of the build and put a `.env` file in the same folder as the .exe when you copy it.

4. **Copy** the `.exe` to the PC where you want to run it. If you didn’t bundle .env, put a `.env` file in the same folder with:
   ```
   SQL_SERVER=your_server
   SQL_DATABASE=GearUp
   SQL_USER=your_user
   SQL_PASSWORD=your_password
   ```

5. **Run** `WhatsAppDesktop.exe`. Chrome profiles are stored in a `chrome_profiles` folder created next to the .exe. No Python or terminal needed.
   - Default (local): `WhatsAppDesktop.exe`
   - Hybrid: `WhatsAppDesktop.exe hybrid`
   - SQL only: `WhatsAppDesktop.exe sql`

**Override:** A `.env` file placed **next to the .exe** always overrides the bundled one (useful for different servers or users).

**On the PC where you run the .exe you still need:**
- **Google Chrome** installed.
- **ODBC Driver 18 for SQL Server** (SQL/Hybrid modes). If missing, install from [Microsoft’s download page](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server).
- **Microsoft Access Database Engine x64** (Local/Hybrid modes), from [Download Center](https://www.microsoft.com/en-us/download/details.aspx?id=54920).
- `local_store.accdb` file in the app base folder for local mode.

## Usage

### Local mode

1. **Add Profile**: Enter profile name + WhatsApp number.
2. **Open Profile**: Click **Open Profile** to open/create the Chrome profile for that number (QR scan first time).
3. **Add List** and import contacts using **Import Contacts CSV**.
4. **Create/Save Template** with placeholders like `{name}`.
5. (Optional) add custom variables (`key=value`) and groups.
6. Send via **Send Selected**, **Send All**, or **Send to Group**.
7. Use **View Logs** / **Delete Logs** for local send history.

### SQL mode

1. **Refresh SQL List**: Load clients from `MST_CLIENT`.
2. **Select** a client in the SQL table.
3. **Open Profile**: Opens Chrome with WhatsApp Web for that client.
4. **Start**: Starts SQL scheduler for that profile (15s poll).
5. **Pause / Resume / Stop** as needed.
6. **Pause All / Resume All** controls all running SQL profiles.

Chrome is only closed when you close the window yourself. The same tab is reused for sending.

## Scope vs main project

- **Original repo** (`main.py`, `worker.py`): unchanged. FastAPI, single profile, worker script.
- **This folder** (`desktop_app`): new desktop app with multi-profile, UI, internal 15s DB poll, pause/resume, and per-profile Chrome windows.
