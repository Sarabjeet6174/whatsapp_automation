# WhatsApp Desktop – Multi-Profile Sender

Desktop application that reads pending messages from the Gear Up SQL table and sends them via WhatsApp Web using **multiple Chrome profiles** (one per client). No need to run Python scripts manually. Can be built as a **Windows .exe** that includes Python inside it, so users never have to install Python (or pip) on their PC.

## What you need to install (on the PC where the app runs)

| Requirement | Run with Python | Run with .exe |
|-------------|------------------|----------------|
| **Google Chrome** | Yes | Yes |
| **ODBC Driver 18 for SQL Server** | Yes | Yes |
| **.env file** (DB credentials) | Yes (repo root or next to script) | Optional: bundle it in the .exe when building, or put next to the .exe |
| **Python 3** | Yes | **No** – the .exe has Python built in |
| **pip packages** (pyodbc, selenium, etc.) | Yes | **No** – bundled inside the .exe |

- **Chrome**: [Download Chrome](https://www.google.com/chrome/) if not already installed. The app opens WhatsApp Web in Chrome.
- **ODBC Driver 18**: Required for the app to connect to your SQL Server. You can install it in two ways from the app:
  - **Help → Install ODBC Driver (open download page)** – opens Microsoft’s page in your browser; download and run the installer yourself.
  - **Help → Download and run ODBC installer** – the app downloads the official Microsoft MSI (from a stable URL) and starts the installer. You may see a UAC (admin) prompt; complete the setup in the installer window, then restart the app. This can fail on locked-down or offline PCs; in that case use the “open download page” option instead.
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

- **Desktop UI**: Select client/profile, Open WhatsApp Web, Start / Pause / Resume / Stop sending.
- **Multi-profile**: Each client has its own Chrome profile (`desktop_app/chrome_profiles/<phone>`). Run multiple profiles at the same time.
- **Internal scheduler**: For each running profile, the app checks the DB every **15 seconds** and sends PENDING messages for that client. No manual trigger.
- **Pause / Resume**: Pause or resume one profile or all. Same Chrome tab is reused.
- **Error handling**: If a message fails, error is saved in DB (`TMR_STATUS='ERROR'`, `TMR_ERR`). Loop continues with the next message. Group search timeout 20s; if group not found, error is logged and processing continues.
- **Recovery**: If the loop crashes, it restarts automatically after 15 seconds. Scheduler never stops due to one error.
- **Group vs number**: If both group name and number exist, message is sent to the **group**. Group search is limited to 20 seconds.
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

**Override:** A `.env` file placed **next to the .exe** always overrides the bundled one (useful for different servers or users).

**On the PC where you run the .exe you still need:**
- **Google Chrome** installed.
- **ODBC Driver 18 for SQL Server** (for the DB connection). If missing, install from [Microsoft’s download page](https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server).

## Usage

1. **Refresh list**: Load clients from `MST_CLIENT`.
2. **Select** a client in the table.
3. **Open Profile**: Opens Chrome with WhatsApp Web for that client (first time you may need to scan QR). Chrome stays open until you close it.
4. **Start**: Starts the scheduler for that profile. PENDING messages for this client are sent one by one; then the app waits 15s and checks again.
5. **Pause / Resume**: Pause or resume sending for the selected profile.
6. **Stop**: Stops the scheduler for that profile. Chrome is not closed.
7. **Pause All / Resume All**: Pause or resume all running profiles.

Chrome is only closed when you close the window yourself. The same tab is reused for sending.

## Scope vs main project

- **Original repo** (`main.py`, `worker.py`): unchanged. FastAPI, single profile, worker script.
- **This folder** (`desktop_app`): new desktop app with multi-profile, UI, internal 15s DB poll, pause/resume, and per-profile Chrome windows.
