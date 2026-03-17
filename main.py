from fastapi import FastAPI, Form, Request, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import WebDriverException, TimeoutException
import time
import os
import logging
from typing import Optional

from dotenv import load_dotenv
import pyodbc
import random


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()


app = FastAPI(title="WhatsApp Sender (Selenium)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_sql_conn() -> pyodbc.Connection:
    """
    Create a SQL Server connection using environment variables and ODBC Driver 18.

    Expected env vars:
      SQL_SERVER, SQL_DATABASE, SQL_USER, SQL_PASSWORD
    """
    server = os.getenv("SQL_SERVER")
    database = os.getenv("SQL_DATABASE")
    user = os.getenv("SQL_USER")
    password = os.getenv("SQL_PASSWORD")

    if not all([server, database, user, password]):
        raise RuntimeError(
            "SQL connection env vars missing. "
            "Please set SQL_SERVER, SQL_DATABASE, SQL_USER, SQL_PASSWORD."
        )

    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"UID={user};"
        f"PWD={password};"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
    )
    return pyodbc.connect(conn_str)


def normalize_phone_for_wa(phone: str) -> str:
    """Convert phone like '+91XXXXXXXXXX' or '91-XXXX' to digits only for wa.me URL."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    return digits


def send_whatsapp_via_web(
    receiver_identifier: str,
    message: str,
    is_group: bool = False,
    attachment_path: Optional[str] = None,
) -> None:
    """
    Use Selenium to open WhatsApp Web and send a message (and optional attachment).

    - If is_group is False, receiver_identifier is treated as a phone number in
      international format and a direct chat URL is used.
    - If is_group is True, receiver_identifier is treated as the exact group name
      and the chat is opened via the search box in WhatsApp Web.
    """
    user_data_dir = os.path.join(os.getcwd(), "chrome_profile")

    chrome_options = Options()
    chrome_options.add_argument("--user-data-dir=" + user_data_dir)
    chrome_options.add_argument("--profile-directory=Default")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--no-sandbox")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)

    try:
        # Give you time to scan QR code on first run and for chat to load.
        wait = WebDriverWait(driver, 180)

        logger.info(
            "Starting WhatsApp send. receiver_identifier=%r, is_group=%s, has_attachment=%s",
            receiver_identifier,
            is_group,
            bool(attachment_path),
        )

        if is_group:
            # Open WhatsApp Web home and search for the group by name.
            logger.info("Opening WhatsApp Web home for group search...")
            driver.get("https://web.whatsapp.com/")

            # Wait for the search box. WhatsApp Web changes its DOM often,
            # so we try a few common selectors in order (newest first).
            search_locators = [
                # Newer WhatsApp Web search/group textbox variant (input)
                (
                    By.XPATH,
                    "//input[@role='textbox' and @type='text' and @data-tab='3']",
                ),
                # Fallbacks for older DOM variants (div contenteditable)
                (By.XPATH, "//div[@contenteditable='true' and @data-tab='3']"),
                (
                    By.XPATH,
                    "//div[@contenteditable='true' and @aria-label='Search']",
                ),
                (
                    By.XPATH,
                    "//div[@contenteditable='true' and @role='textbox' and contains(@aria-label,'Search')]",
                ),
                (
                    By.XPATH,
                    "//div[@contenteditable='true' and @title='Search input textbox']",
                ),
            ]

            search_box = None
            last_exc: Optional[Exception] = None
            for locator in search_locators:
                try:
                    logger.info("Trying search box locator: %r", locator)
                    search_box = wait.until(
                        EC.element_to_be_clickable(locator)
                    )
                    if search_box:
                        break
                except (TimeoutException, WebDriverException) as exc:
                    last_exc = exc
                    continue

            if not search_box:
                raise RuntimeError(
                    f"Could not locate WhatsApp search box. Last error: {last_exc}"
                )

            logger.info("Search box found, searching for group %r", receiver_identifier)
            search_box.click()
            search_box.clear()
            search_box.send_keys(receiver_identifier)

            # First try the simple path: press ENTER to open the first matching chat.
            search_box.send_keys(Keys.ENTER)
            time.sleep(2)

            # Best-effort fallback: if the chat is not open yet, try clicking it explicitly.
            try:
                logger.info("Trying to click explicit chat span for %r", receiver_identifier)
                chat = wait.until(
                    EC.element_to_be_clickable(
                        (By.XPATH, f"//span[@title='{receiver_identifier}']")
                    )
                )
                chat.click()
            except (TimeoutException, WebDriverException):
                # If this fails, we assume ENTER already opened the chat or nothing matched.
                logger.info("Skipping explicit group click; ENTER key path used instead.")
        else:
            # Build direct chat URL. receiver_identifier should be in international format.
            phone_digits = normalize_phone_for_wa(receiver_identifier)
            if not phone_digits:
                raise RuntimeError("Receiver phone must contain digits.")

            from urllib.parse import quote_plus

            encoded_message = quote_plus(message)
            chat_url = f"https://web.whatsapp.com/send?phone={phone_digits}"
            logger.info("Opening direct chat URL: %s", chat_url)
            driver.get(chat_url)

        # Give WhatsApp a bit more time to fully load the chat UI.
        logger.info("Waiting 5s for chat UI to fully load...")
        time.sleep(5)

        # Wait for message input box to be available (support multiple DOM variants).
        message_box_locators = [
            (By.XPATH, "//div[@contenteditable='true' and @data-tab='10']"),
            (
                By.XPATH,
                "//div[@contenteditable='true' and @data-tab='6']",
            ),
            (
                By.XPATH,
                "//footer//div[@contenteditable='true' and @role='textbox']",
            ),
        ]

        message_box = None
        last_exc: Optional[Exception] = None
        for locator in message_box_locators:
            try:
                logger.info("Trying message box locator: %r", locator)
                message_box = wait.until(
                    EC.element_to_be_clickable(locator)
                )
                if message_box:
                    break
            except (TimeoutException, WebDriverException) as exc:
                last_exc = exc
                continue

        if not message_box:
            raise RuntimeError(
                f"Could not locate WhatsApp message box. Last error: {last_exc}"
            )
        logger.info("Message box located successfully.")

        # Handle optional attachment (images/videos/other files) FIRST, then caption.
        if attachment_path:
            logger.info("Attaching file at path %s", attachment_path)

            # 1) Click the chat footer Attach button (the plus-rounded button).
            # Prefer the button that lives inside the footer so we don't trigger
            # the global "Select contacts" / forwarding dialog.
            attach_locators = [
                (By.XPATH, "//footer//button[@aria-label='Attach']"),
                (By.XPATH, "//button[@aria-label='Attach' and @data-tab='10']"),
                (By.XPATH, "//button[@aria-label='Attach']"),
            ]

            attach_button = None
            last_exc = None
            for locator in attach_locators:
                try:
                    logger.info("Trying attach button locator: %r", locator)
                    attach_button = wait.until(
                        EC.element_to_be_clickable(locator)
                    )
                    if attach_button:
                        break
                except (TimeoutException, WebDriverException) as exc:
                    last_exc = exc
                    continue

            if not attach_button:
                raise RuntimeError(
                    f"Could not locate WhatsApp attach button. Last error: {last_exc}"
                )

            attach_button.click()
            logger.info("Attach button clicked.")

            # 2) Use the file input that belongs to this chat's footer.
            # Scope to footer and prefer inputs that explicitly have an accept attribute.
            file_input_locators = [
                (By.XPATH, "//footer//input[@type='file' and @accept]"),
                (By.XPATH, "//footer//input[@type='file']"),
                (By.XPATH, "//input[@type='file' and @accept]"),
            ]

            file_input = None
            last_exc = None
            for locator in file_input_locators:
                try:
                    logger.info("Trying file input locator: %r", locator)
                    file_input = wait.until(
                        EC.presence_of_element_located(locator)
                    )
                    if file_input:
                        break
                except (TimeoutException, WebDriverException) as exc:
                    last_exc = exc
                    continue

            if not file_input:
                raise RuntimeError(
                    f"Could not locate WhatsApp file input. Last error: {last_exc}"
                )

            file_input.send_keys(os.path.abspath(attachment_path))
            logger.info("File path sent to file input.")

            # 3) Give WhatsApp a bit of time to build the preview (especially for videos / large files).
            try:
                logger.info("Waiting for WhatsApp to build the attachment preview...")
                time.sleep(3)
            except Exception:
                pass

            # 4) Wait for the send button in the preview and click it.
            send_button_locators = [
                # Most reliable: send button inside the footer/preview area.
                (
                    By.XPATH,
                    "//footer//span[@data-icon='send']/ancestor::div[@role='button']",
                ),
                (
                    By.XPATH,
                    "//span[@data-icon='send']/ancestor::div[@role='button']",
                ),
                (By.XPATH, "//button[@aria-label='Send']"),
            ]

            send_button = None
            last_exc = None
            for locator in send_button_locators:
                try:
                    logger.info("Trying send button locator: %r", locator)
                    send_button = wait.until(
                        EC.element_to_be_clickable(locator)
                    )
                    if send_button:
                        break
                except (TimeoutException, WebDriverException) as exc:
                    last_exc = exc
                    continue

            if not send_button:
                # As a final fallback, try clicking the raw send icon via JS if it exists.
                try:
                    logger.info("Falling back to JS click on raw send icon...")
                    raw_send_icon = driver.find_element(By.XPATH, "//span[@data-icon='send']")
                    driver.execute_script("arguments[0].click();", raw_send_icon)
                except Exception as exc:
                    raise RuntimeError(
                        f"Could not locate WhatsApp send button. Last error: {last_exc}"
                    ) from exc
            else:
                # Optional caption/message after attaching (before clicking send).
                if message:
                    logger.info("Adding caption message before sending attachment...")
                    message_box.click()
                    message_box.send_keys(message)

                logger.info("Clicking send button for attachment...")
                try:
                    send_button.click()
                except WebDriverException:
                    logger.info("Standard click failed, retrying with JS click...")
                    driver.execute_script("arguments[0].click();", send_button)
        else:
            # No attachment: just send plain text if provided.
            if message:
                logger.info("Sending plain text message (no attachment).")
                message_box.click()
                message_box.send_keys(message)
                message_box.send_keys(Keys.ENTER)

        # Allow time for send so you can see it succeed.
        logger.info("Waiting 5s after send so you can visually confirm in browser...")
        time.sleep(5)
    except WebDriverException as exc:
        logger.exception("Selenium/WebDriver error while sending WhatsApp message")
        raise RuntimeError(f"Selenium/WebDriver error: {exc}") from exc
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def process_pending_messages_from_sql(pause_seconds: int = 2, test_override_number: str | None = None) -> None:
    """
    Read pending messages from SQL Server and send them one by one
    with a small pause between each message.
    Mirrors the VBA TRAN_MSG_REQUEST / MST_CLIENT query.
    """
    conn = get_sql_conn()
    cursor = conn.cursor()

    sql = """
    SELECT
        CLIENT_IDNO,
        TMR_IDNO,
        TMR_FROM_NO,
        TMR_TO_NO,
        TMR_MSG,
        TMR_SCH_DTTIME,
        TMR_STATUS,
        ISNULL(TMR_GROUP_NAME, 'NA') AS TMR_GROUP_NAME
    FROM TRAN_MSG_REQUEST WITH (NOLOCK)
    JOIN MST_CLIENT WITH (NOLOCK) ON TMR_FROM_NO = CLIENT_PHNO
    WHERE
        TMR_STATUS = 'PENDING'
        AND TMR_SCH_DTTIME < GETDATE()
        AND TMR_FROM_NO IS NOT NULL
        AND TMR_TO_NO IS NOT NULL
    ORDER BY TMR_SCH_DTTIME
    """

    logger.info("Fetching pending messages from SQL Server...")
    cursor.execute(sql)
    rows = cursor.fetchall()

    for row in rows:
        client_idno = row.CLIENT_IDNO
        tmr_idno = row.TMR_IDNO
        from_no = row.TMR_FROM_NO
        to_no = row.TMR_TO_NO
        msg_text = row.TMR_MSG
        group_name = row.TMR_GROUP_NAME  # 'NA' or actual group name

        logger.info(
            "Processing TMR_IDNO=%s for client_id=%s, to_no=%s, group=%s",
            tmr_idno,
            client_idno,
            to_no,
            group_name,
        )

        # For testing: optionally override sender/receiver numbers so all messages go to you.
        effective_from_no = from_no
        effective_to_no = to_no
        if test_override_number:
            effective_from_no = test_override_number
            effective_to_no = test_override_number
            logger.info(
                "TEST OVERRIDE ACTIVE: using from_no=%s, to_no=%s instead of DB values",
                effective_from_no,
                effective_to_no,
            )

        try:
            send_whatsapp_via_web(
                receiver_identifier=str(effective_to_no),
                message=msg_text or "",
                is_group=(group_name != "NA"),
                attachment_path=None,
            )

            cursor.execute(
                """
                UPDATE TRAN_MSG_REQUEST
                SET TMR_STATUS='SENT', TMR_SENT_TIME=GETDATE()
                WHERE TMR_IDNO = ?
                """,
                tmr_idno,
            )
            conn.commit()
        except Exception as exc:
            logger.exception("Error sending message for TMR_IDNO=%s", tmr_idno)
            cursor.execute(
                """
                UPDATE TRAN_MSG_REQUEST
                SET TMR_STATUS='ERROR', TMR_ERR = ?
                WHERE TMR_IDNO = ?
                """,
                str(exc)[:500],
                tmr_idno,
            )
            conn.commit()

        # Pause between messages (similar to Delay in VBA).
        # If pause_seconds is <= 0, use a random delay between 4 and 13 seconds.
        if pause_seconds > 0:
            time.sleep(pause_seconds)
        else:
            lowerbound = 4
            upperbound = 13
            delay_seconds = lowerbound + (upperbound - lowerbound) * (random.random())
            time.sleep(delay_seconds)

    cursor.close()
    conn.close()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>WhatsApp Sender</title>
        <style>
            body {
                font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
                background: #0f172a;
                color: #e5e7eb;
                display: flex;
                align-items: center;
                justify-content: center;
                min-height: 100vh;
                margin: 0;
            }
            .card {
                background: radial-gradient(circle at top left, #22c55e33, #111827 50%);
                border-radius: 18px;
                padding: 28px 32px;
                max-width: 420px;
                width: 100%;
                box-shadow: 0 24px 60px rgba(15,23,42,0.9);
                border: 1px solid rgba(148,163,184,0.3);
            }
            h1 {
                margin-top: 0;
                margin-bottom: 4px;
                font-size: 1.7rem;
            }
            p.subtitle {
                margin-top: 0;
                margin-bottom: 20px;
                color: #9ca3af;
                font-size: 0.9rem;
            }
            label {
                display: block;
                margin-bottom: 6px;
                font-size: 0.85rem;
                color: #d1d5db;
            }
            input, textarea {
                width: 100%;
                padding: 9px 11px;
                border-radius: 9px;
                border: 1px solid #374151;
                background: #020617;
                color: #e5e7eb;
                font-size: 0.9rem;
                box-sizing: border-box;
            }
            input:focus, textarea:focus {
                outline: 2px solid #22c55e55;
                border-color: #22c55eaa;
            }
            textarea {
                resize: vertical;
                min-height: 70px;
            }
            .field {
                margin-bottom: 14px;
            }
            .hint {
                font-size: 0.75rem;
                color: #6b7280;
                margin-top: 4px;
            }
            button {
                width: 100%;
                padding: 10px 16px;
                border-radius: 9999px;
                border: none;
                background: linear-gradient(135deg, #22c55e, #16a34a);
                color: #022c22;
                font-weight: 600;
                cursor: pointer;
                font-size: 0.95rem;
                margin-top: 4px;
            }
            button:hover {
                background: linear-gradient(135deg, #16a34a, #22c55e);
            }
            .status {
                margin-top: 10px;
                font-size: 0.85rem;
            }
            .status.error {
                color: #fecaca;
            }
            .status.success {
                color: #bbf7d0;
            }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>WhatsApp Sender</h1>
            <p class="subtitle">Send a WhatsApp message using FastAPI & Selenium (WhatsApp Web).</p>
            <form id="sendForm" enctype="multipart/form-data">
                <div class="field">
                    <label for="receiver_phone">Receiver</label>
                    <input id="receiver_phone" name="receiver_phone" placeholder="+91XXXXXXXXXX or Group Name" required />
                    <div class="hint">For groups, enter the exact group name and check "Send to group".</div>
                </div>
                <div class="field" style="display:flex;align-items:center;gap:8px;">
                    <input type="checkbox" id="is_group" name="is_group" style="width:auto;" />
                    <label for="is_group" style="margin:0;">Send to group</label>
                </div>
                <div class="field">
                    <label for="message">Message</label>
                    <textarea id="message" name="message" placeholder="Type your WhatsApp message... (optional if you attach a file)"></textarea>
                </div>
                <div class="field">
                    <label for="attachment">Attachment (image / video / file)</label>
                    <input id="attachment" name="attachment" type="file" />
                    <div class="hint">Optional. WhatsApp Web supported formats only.</div>
                </div>
                <button type="submit">Send WhatsApp Message</button>
                <div id="status" class="status"></div>
            </form>
        </div>

        <script>
        const form = document.getElementById('sendForm');
        const statusEl = document.getElementById('status');

        form.addEventListener('submit', async (e) => {
            e.preventDefault();

            const msg = document.getElementById('message').value.trim();
            const file = document.getElementById('attachment').files[0];

            if (!msg && !file) {
                statusEl.textContent = 'Please enter a message or attach a file.';
                statusEl.className = 'status error';
                return;
            }

            statusEl.textContent = 'Sending...';
            statusEl.className = 'status';

            try {
                const res = await fetch('/send-whatsapp', {
                    method: 'POST',
                    body: new FormData(form),
                });

                const json = await res.json();
                if (res.ok) {
                    statusEl.textContent = 'Message sent via WhatsApp Web';
                    statusEl.className = 'status success';
                } else {
                    statusEl.textContent = json.detail || 'Failed to send message.';
                    statusEl.className = 'status error';
                }
            } catch (err) {
                statusEl.textContent = 'Error: ' + err.message;
                statusEl.className = 'status error';
            }
        });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.post("/send-whatsapp")
async def send_whatsapp(
    receiver_phone: str = Form(...),
    message: str = Form(""),
    is_group: bool = Form(False),
    attachment: Optional[UploadFile] = File(None),
):
    try:
        if not receiver_phone:
            return JSONResponse(
                status_code=400,
                content={
                    "detail": "Receiver phone / name is required (must match a WhatsApp contact, chat or group)."
                },
            )

        attachment_path: Optional[str] = None
        if attachment and attachment.filename:
            uploads_dir = os.path.join(os.getcwd(), "uploads")
            os.makedirs(uploads_dir, exist_ok=True)

            safe_filename = attachment.filename.replace("/", "_").replace("\\", "_")
            attachment_path = os.path.join(uploads_dir, safe_filename)

            with open(attachment_path, "wb") as f:
                f.write(await attachment.read())

        send_whatsapp_via_web(
            receiver_identifier=receiver_phone,
            message=message,
            is_group=is_group,
            attachment_path=attachment_path,
        )
        return {"status": "ok"}
    except Exception as exc:
        logger.exception("Error in /send-whatsapp endpoint")
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc)},
        )


@app.post("/process-sql-queue")
async def process_sql_queue():
    """
    Trigger processing of pending messages from SQL Server.
    """
    try:
        process_pending_messages_from_sql(pause_seconds=2)
        return {"status": "ok", "detail": "Processed pending SQL messages"}
    except Exception as exc:
        logger.exception("Error while processing SQL message queue")
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc)},
        )


# Entry point for uvicorn if running `python main.py`
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

