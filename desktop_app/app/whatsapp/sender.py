"""
WhatsApp Web sender for desktop app. Uses shared driver, 20s group timeout, never raises.
Returns "SUCCESS" or error string for DB logging.
"""
import logging
import os
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from contextlib import nullcontext

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import (
    WebDriverException,
    TimeoutException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
)
from selenium.webdriver.remote.webelement import WebElement

from config import get_profile_dir

logger = logging.getLogger(__name__)

GROUP_SEARCH_TIMEOUT = 20
NUMBER_SEARCH_TIMEOUT = 20
CHAT_LOAD_TIMEOUT = 60
# WhatsApp changes data-tab on the compose box; trying wrong locators with the main
# 60s wait made each failed XPath cost a full minute. Use a short try per locator.
MESSAGE_BOX_LOCATOR_TIMEOUT = 8

# WhatsApp Web empty search state (class names change; match visible copy).
_NO_SEARCH_RESULTS_TEXT = "No chats, contacts or messages found"

_MEDIA_EXTENSIONS = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".bmp",
        ".mp4",
        ".webm",
        ".mov",
        ".3gp",
        ".mkv",
    }
)
_VIDEO_EXT = frozenset({".mp4", ".webm", ".mov", ".3gp", ".mkv"})
# Images we can put on the Windows clipboard for Ctrl+V into WhatsApp compose (not PDFs/docs).
_CLIPBOARD_IMAGE_EXT = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"})

try:
    import pyautogui  # pyright: ignore[reportMissingModuleSource]
except ImportError:
    pyautogui = None

try:
    import websocket as ws_cdp
except ImportError:
    ws_cdp = None

try:
    import pyperclip
except ImportError:
    pyperclip = None


def _resolve_attachment_paths(paths: list[str] | None) -> list[str]:
    if not paths:
        return []
    out: list[str] = []
    for p in paths:
        if not (p or "").strip():
            continue
        ap = os.path.abspath(os.path.normpath(p))
        if os.path.isfile(ap):
            out.append(ap)
    return out


def _is_gallery_media_accept(acc: str) -> bool:
    a = (acc or "").lower()
    if not a or "image" not in a:
        return False
    if "video/mp4" in a or "3gpp" in a or "quicktime" in a:
        return True
    if "image/*" in a and ("mp4" in a or "mov" in a):
        return True
    return False


def _matches_document_accept(acc: str) -> bool:
    a = (acc or "").strip().lower()
    if a in ("*", "*/*"):
        return True
    if a and "image" not in a and "video" not in a:
        return True
    return False


def _chrome_page_websocket_debugger_url(driver: webdriver.Chrome, url_substr: str = "web.whatsapp") -> str | None:
    caps = driver.capabilities
    dbg = None
    for key in ("goog:chromeOptions", "ms:edgeOptions"):
        opt = caps.get(key)
        if isinstance(opt, dict):
            dbg = opt.get("debuggerAddress")
            if dbg:
                break
    if not dbg:
        return None
    dbg = dbg.strip()
    base = dbg if dbg.startswith("http") else f"http://{dbg}"
    try:
        req = urllib.request.Request(f"{base.rstrip('/')}/json/list")
        with urllib.request.urlopen(req, timeout=8) as resp:
            targets = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None
    for t in targets:
        if t.get("type") != "page":
            continue
        url = t.get("url") or ""
        ws_url = t.get("webSocketDebuggerUrl")
        if ws_url and url_substr in url.lower():
            return ws_url
    for t in targets:
        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
            return t["webSocketDebuggerUrl"]
    return None


class _FileChooserInterceptor:
    def __init__(self, ws_url: str, paths: list[str]):
        self.ws_url = ws_url
        self.paths = [str(os.path.abspath(os.path.normpath(p))) for p in paths]
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ws_conn = None
        self.did_assign_files = False

    def __enter__(self):
        if ws_cdp is None:
            logger.debug("FileChooserInterceptor skipped (websocket-client missing).")
            return self
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        time.sleep(0.35)
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        try:
            if self._ws_conn is not None:
                self._ws_conn.close()
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=4.0)
        self._ws_conn = None

    def _run(self) -> None:
        if ws_cdp is None:
            return
        ws = None
        try:
            ws = ws_cdp.create_connection(self.ws_url, timeout=15, suppress_origin=True)
            self._ws_conn = ws
        except Exception as ex:
            logger.warning("CDP WebSocket connect failed (interception inactive): %s", ex)
            return

        cid = 0

        def send(method: str, params: dict) -> None:
            nonlocal cid
            if ws is None:
                return
            cid += 1
            ws.send(json.dumps({"id": cid, "method": method, "params": params}))

        try:
            send("Page.enable", {})
            send("DOM.enable", {})
            send("Page.setInterceptFileChooserDialog", {"enabled": True})
            ws.settimeout(0.35)
            handled = False
            while not self._stop.is_set():
                try:
                    raw = ws.recv()
                except Exception:
                    if self._stop.is_set():
                        break
                    continue
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if data.get("method") != "Page.fileChooserOpened":
                    continue
                bid = (data.get("params") or {}).get("backendNodeId")
                if bid is None or handled:
                    continue
                handled = True
                send("DOM.setFileInputFiles", {"files": self.paths, "backendNodeId": bid})
                self.did_assign_files = True
                send("Page.setInterceptFileChooserDialog", {"enabled": False})
        finally:
            try:
                send("Page.setInterceptFileChooserDialog", {"enabled": False})
            except Exception:
                pass
            try:
                if ws is not None:
                    ws.close()
            except Exception:
                pass


def _dismiss_native_file_dialog(attempts: int = 2, delay: float = 0.16) -> None:
    if pyautogui is None:
        return
    for _ in range(attempts):
        try:
            pyautogui.press("esc")
            time.sleep(delay)
        except Exception:
            break


def _click_attach_menu(driver: webdriver.Chrome) -> None:
    candidates = [
        (By.CSS_SELECTOR, 'span[data-icon="plus"]'),
        (By.CSS_SELECTOR, 'span[data-icon="plus-rounded"]'),
        (By.CSS_SELECTOR, 'span[data-icon="attach-menu-plus"]'),
        (By.CSS_SELECTOR, 'button[aria-label="Attach"]'),
        (By.CSS_SELECTOR, '[aria-label="Attach"]'),
        (By.CSS_SELECTOR, 'div[title="Attach"]'),
        (By.XPATH, "//div[@role='button' and (@title='Attach' or @aria-label='Attach')]"),
    ]
    deadline = time.monotonic() + 8.0
    last = None
    while time.monotonic() < deadline:
        for by, sel in candidates:
            try:
                els = driver.find_elements(by, sel)
                if not els:
                    continue
                el = els[0]
                try:
                    el.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                return
            except Exception as e:
                last = e
                continue
        time.sleep(0.15)
    raise RuntimeError(f"Could not find Attach button: {last}")


def _click_document_menu_item(driver: webdriver.Chrome) -> None:
    candidates = [
        (By.CSS_SELECTOR, '[data-testid="mi-document"]'),
        (By.CSS_SELECTOR, '[data-testid="mi-attach-document"]'),
        (By.CSS_SELECTOR, 'span[data-testid="attach-document"]'),
        (By.XPATH, "//span[contains(text(),'Document')]"),
        (By.XPATH, "//div[contains(text(),'Document')]"),
        (By.XPATH, "//*[contains(@aria-label,'Document')]"),
    ]
    deadline = time.monotonic() + 7.0
    while time.monotonic() < deadline:
        for by, sel in candidates:
            try:
                els = driver.find_elements(by, sel)
                if not els:
                    continue
                el = els[0]
                try:
                    el.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                return
            except Exception:
                continue
        time.sleep(0.15)


def _click_photos_menu_item(driver: webdriver.Chrome) -> None:
    candidates = [
        (By.CSS_SELECTOR, '[data-testid="mi-attach-media"]'),
        (By.CSS_SELECTOR, '[data-testid="attach-menu-image"]'),
        (By.XPATH, "//span[contains(., 'Photos') and contains(., 'video')]"),
        (By.XPATH, "//span[contains(., 'Photos') and contains(., 'Video')]"),
        (By.XPATH, "//div[contains(., 'Photos') and contains(., 'video')]"),
        (By.XPATH, "//*[contains(@aria-label, 'Photos') and contains(@aria-label, 'video')]"),
    ]
    deadline = time.monotonic() + 7.0
    while time.monotonic() < deadline:
        for by, sel in candidates:
            try:
                els = driver.find_elements(by, sel)
                if not els:
                    continue
                el = els[0]
                try:
                    el.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                return
            except Exception:
                continue
        time.sleep(0.15)
    raise RuntimeError("Could not click 'Photos & videos' in the attach menu.")


def _find_document_file_input(driver: webdriver.Chrome):
    wait = WebDriverWait(driver, 30)
    try:
        els = driver.find_elements(By.CSS_SELECTOR, 'input[type="file"]')
        for el in els:
            acc = (el.get_attribute("accept") or "").strip().lower()
            if acc in ("*", "*/*"):
                return el
            if acc and "image" not in acc and "video" not in acc:
                return el
        for el in reversed(els):
            acc = (el.get_attribute("accept") or "").strip().lower()
            if not acc or acc == "*":
                return el
    except Exception:
        pass
    try:
        return wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[type="file"][accept="*"]')))
    except TimeoutException:
        return None


def _find_photos_and_videos_file_input(driver: webdriver.Chrome):
    wait = WebDriverWait(driver, 30)

    def pick_gallery_input(d: webdriver.Chrome):
        inputs = d.find_elements(By.CSS_SELECTOR, 'input[type="file"]')
        for e in inputs:
            if _is_gallery_media_accept(e.get_attribute("accept") or ""):
                return e
        for e in inputs:
            acc = (e.get_attribute("accept") or "").lower()
            if "image/webp" in acc and "video" not in acc and "image/*" not in acc:
                continue
            if "image" in acc and len(acc) >= 28:
                return e
        return None

    try:
        return wait.until(pick_gallery_input)
    except TimeoutException:
        pass
    return None


def _cdp_enable_dom(driver: webdriver.Chrome) -> None:
    try:
        driver.execute_cdp_cmd("DOM.enable", {})
    except Exception:
        pass


def _cdp_accept_for_node(driver: webdriver.Chrome, node_id: int) -> str:
    out = driver.execute_cdp_cmd("DOM.getAttributes", {"nodeId": node_id})
    pairs = out.get("attributes") or []
    if len(pairs) < 2:
        return ""
    m: dict[str, str] = dict(zip(pairs[::2], pairs[1::2]))
    return (m.get("accept") or "").strip()


def _cdp_list_file_input_node_ids(driver: webdriver.Chrome) -> list[int]:
    _cdp_enable_dom(driver)
    try:
        r = driver.execute_cdp_cmd(
            "DOM.performSearch",
            {"query": 'input[type="file"]', "includeUserAgentShadowDOM": True},
        )
    except Exception:
        return []
    search_id = r["searchId"]
    try:
        count = int(r["resultCount"])
        if count <= 0:
            return []
        r2 = driver.execute_cdp_cmd(
            "DOM.getSearchResults",
            {"searchId": search_id, "fromIndex": 0, "toIndex": count},
        )
        return list(r2.get("nodeIds") or [])
    finally:
        try:
            driver.execute_cdp_cmd("DOM.discardSearchResults", {"searchId": search_id})
        except Exception:
            pass


def _cdp_pick_matching_file_node_id(driver: webdriver.Chrome, for_gallery_media: bool) -> int | None:
    node_ids = _cdp_list_file_input_node_ids(driver)
    accepts: list[tuple[int, str]] = []
    for nid in node_ids:
        try:
            accepts.append((nid, _cdp_accept_for_node(driver, nid)))
        except Exception:
            continue

    if for_gallery_media:
        for nid, acc in accepts:
            if _is_gallery_media_accept(acc):
                return nid
        for nid, acc in accepts:
            al = (acc or "").lower()
            if "image/webp" in al and "video" not in al and "image/*" not in al:
                continue
            if "image" in al and len(al) >= 28:
                return nid
        return None

    for nid, acc in accepts:
        if _matches_document_accept(acc):
            return nid
    for nid, acc in reversed(accepts):
        a = (acc or "").strip().lower()
        if not a or a == "*":
            return nid
    return None


def _cdp_set_files_on_node(driver: webdriver.Chrome, node_id: int, paths: list[str]) -> None:
    files = [str(os.path.abspath(os.path.normpath(p))) for p in paths]
    driver.execute_cdp_cmd("DOM.setFileInputFiles", {"files": files, "nodeId": node_id})


def _cdp_try_assign_files(
    driver: webdriver.Chrome,
    paths: list[str],
    for_gallery: bool,
    chooser_assigned=None,
) -> bool:
    deadline_a = time.monotonic() + 7.0
    while time.monotonic() < deadline_a:
        if chooser_assigned and chooser_assigned():
            return True
        nid = _cdp_pick_matching_file_node_id(driver, for_gallery)
        if nid is not None:
            try:
                _cdp_set_files_on_node(driver, nid, paths)
                return True
            except Exception:
                pass
        time.sleep(0.25)

    if for_gallery:
        _click_photos_menu_item(driver)
    else:
        _click_document_menu_item(driver)
    time.sleep(0.35)

    deadline_b = time.monotonic() + 5.0
    while time.monotonic() < deadline_b:
        if chooser_assigned and chooser_assigned():
            return True
        nid = _cdp_pick_matching_file_node_id(driver, for_gallery)
        if nid is not None:
            try:
                _cdp_set_files_on_node(driver, nid, paths)
                return True
            except Exception:
                pass
        time.sleep(0.25)
    return False


def _fallback_send_keys_file_input(driver: webdriver.Chrome, paths: list[str], for_gallery: bool) -> None:
    if for_gallery:
        file_input = _find_photos_and_videos_file_input(driver) or _find_document_file_input(driver)
    else:
        file_input = _find_document_file_input(driver) or _find_photos_and_videos_file_input(driver)
    if not file_input:
        if for_gallery:
            _click_photos_menu_item(driver)
        else:
            _click_document_menu_item(driver)
        time.sleep(0.35)
        if for_gallery:
            file_input = _find_photos_and_videos_file_input(driver) or _find_document_file_input(driver)
        else:
            file_input = _find_document_file_input(driver) or _find_photos_and_videos_file_input(driver)
    if not file_input:
        raise RuntimeError("Could not find file upload control.")
    joined = "\n".join(str(os.path.abspath(os.path.normpath(p))) for p in paths)
    file_input.send_keys(joined)


def _all_paths_are_images_or_video(paths: list[str]) -> bool:
    exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".mp4", ".mov", ".3gp", ".mkv", ".webm"}
    for p in paths:
        if os.path.splitext(p)[1].lower() not in exts:
            return False
    return bool(paths)


def _switch_editor_from_sticker_to_photo(driver: webdriver.Chrome) -> None:
    short = WebDriverWait(driver, 4)
    for by, sel in (
        (By.XPATH, "//div[@role='tab' and contains(., 'HD')]"),
        (By.XPATH, "//button[contains(@aria-label, 'HD')]"),
        (By.XPATH, "//div[@role='tab'][contains(., 'Photo') and not(contains(., 'Sticker'))]"),
        (By.XPATH, "//span[contains(., 'HD') and string-length(.) < 24]"),
        (By.CSS_SELECTOR, '[data-testid="media-editor-send-hd"]'),
    ):
        try:
            short.until(EC.element_to_be_clickable((by, sel))).click()
            time.sleep(0.2)
            return
        except (TimeoutException, StaleElementReferenceException):
            continue


def _insert_text_into_contenteditable(driver: webdriver.Chrome, element: WebElement, text: str) -> None:
    """
    Insert text into a WhatsApp Web contenteditable (compose or attachment caption).
    Selenium send_keys drops many emoji (non-BMP); CDP Input.insertText or clipboard paste works.
    """
    element.click()
    time.sleep(0.06)
    ActionChains(driver).key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL).send_keys(Keys.BACKSPACE).perform()
    time.sleep(0.05)
    if not text:
        return
    try:
        driver.execute_cdp_cmd("Input.insertText", {"text": text})
        return
    except Exception as e:
        logger.debug("Input.insertText failed: %s", e)
    if pyperclip is not None:
        try:
            pyperclip.copy(text)
            ActionChains(driver).key_down(Keys.CONTROL).send_keys("v").key_up(Keys.CONTROL).perform()
            return
        except Exception as e:
            logger.warning("Clipboard paste failed: %s", e)
    for ch in text:
        if ch == "\n":
            element.send_keys(Keys.SHIFT, Keys.ENTER)
        else:
            element.send_keys(ch)


def _set_attachment_caption(driver: webdriver.Chrome, text: str) -> bool:
    if not text:
        return False
    # In attachment preview this is the "Type a message" caption box.
    for by, sel in (
        (By.CSS_SELECTOR, "div[contenteditable='true'][data-tab]"),
        (By.XPATH, "//*[@contenteditable='true' and (@aria-label='Type a message' or @title='Type a message')]"),
        (By.XPATH, "//*[contains(@aria-label,'message') and @contenteditable='true']"),
    ):
        try:
            box = WebDriverWait(driver, 6).until(EC.element_to_be_clickable((by, sel)))
            _insert_text_into_contenteditable(driver, box, text)
            logger.info("Attachment caption entered in Type a message box.")
            return True
        except Exception:
            continue
    logger.warning("Could not locate attachment caption box (Type a message).")
    return False


def _attachment_preview_send_visible(driver: webdriver.Chrome) -> bool:
    checks = (
        (By.CSS_SELECTOR, '[data-testid="send"]'),
        (By.CSS_SELECTOR, 'span[data-icon="wds-ic-send-filled"]'),
        (By.XPATH, "//div[@role='button' and (@aria-label='Send' or @aria-label='Send message')]"),
    )
    for by, sel in checks:
        try:
            for el in driver.find_elements(by, sel):
                try:
                    if el.is_displayed():
                        return True
                except Exception:
                    continue
        except Exception:
            continue
    return False


def _wait_for_attachment_preview(driver: webdriver.Chrome, timeout_s: float = 8.0) -> bool:
    deadline = time.monotonic() + max(0.5, timeout_s)
    while time.monotonic() < deadline:
        if _attachment_preview_send_visible(driver):
            return True
        time.sleep(0.2)
    return False


def _upload_attachments_hidden_input_fallback(driver: webdriver.Chrome, resolved: list[str]) -> str | None:
    inputs = _list_whatsapp_file_inputs(driver)
    inp = _pick_file_input(inputs, resolved)
    if inp is None:
        return "File upload input not found"
    accept = inp.get_attribute("accept") or ""
    if _needs_non_media_upload(resolved) and _accept_is_media_only(accept):
        return (
            "No hidden document file input found (only image/*). "
            "WhatsApp Web may not expose document upload to automation on this build."
        )
    send_err: str | None = None
    try:
        inp.send_keys("\n".join(resolved))
        _nudge_file_input_for_react(driver, inp)
    except Exception as e:
        send_err = f"Could not upload files: {e!r}"[:400]
    if send_err is None:
        if _wait_for_attachment_preview(driver, timeout_s=8.0):
            return None
        return "Upload attempted but attachment preview did not appear"

    logger.info("send_keys hidden-input upload failed (%s); trying CDP.", send_err)
    if _cdp_set_files_on_first_matching_input(driver, resolved):
        inp2 = _pick_file_input(_list_whatsapp_file_inputs(driver), resolved)
        _nudge_file_input_for_react(driver, inp2)
        if _wait_for_attachment_preview(driver, timeout_s=8.0):
            return None
        return "CDP hidden-input upload set files, but attachment preview did not appear"
    return send_err


def _cdp_set_files_on_first_matching_input(driver: webdriver.Chrome, resolved: list[str]) -> bool:
    """
    Assign files via Chrome DevTools DOM.setFileInputFiles (no send_keys on the file input).
    Uses DOM.performSearch so nodes inside shadow roots (common in WhatsApp Web) are found.
    """
    if not resolved:
        return False
    if not hasattr(driver, "execute_cdp_cmd"):
        return False
    try:
        driver.execute_cdp_cmd("DOM.enable", {})
    except Exception:
        pass

    # Avoid bare "input[type=file]" — first hit can be outside the chat and break WA.
    queries = (
        "footer input[type=file]",
        "#main input[type=file]",
    )
    for query in queries:
        sid = None
        try:
            pr = driver.execute_cdp_cmd(
                "DOM.performSearch",
                {"query": query, "includeUserAgentShadowDOM": True},
            )
            sid = pr.get("searchId")
            count = int(pr.get("resultCount", 0))
            if not sid or count <= 0:
                if sid:
                    try:
                        driver.execute_cdp_cmd("DOM.discardSearchResults", {"searchId": sid})
                    except Exception:
                        pass
                continue
            res = driver.execute_cdp_cmd(
                "DOM.getSearchResults",
                {"searchId": sid, "fromIndex": 0, "toIndex": 1},
            )
            nids = res.get("nodeIds") or []
            if not nids:
                continue
            nid = int(nids[0])
            driver.execute_cdp_cmd(
                "DOM.setFileInputFiles",
                {"nodeId": nid, "files": list(resolved)},
            )
            logger.info("Attached files via CDP DOM.setFileInputFiles (search: %s)", query)
            return True
        except Exception as e:
            logger.debug("CDP upload failed for query %r: %s", query, e)
            continue
        finally:
            if sid:
                try:
                    driver.execute_cdp_cmd("DOM.discardSearchResults", {"searchId": sid})
                except Exception:
                    pass
    return False


def _all_clipboard_image_files(paths: list[str]) -> bool:
    resolved = _resolve_attachment_paths(paths)
    if not resolved:
        return False
    exts = {os.path.splitext(p)[1].lower() for p in resolved}
    return bool(exts) and exts <= _CLIPBOARD_IMAGE_EXT


def _powershell_set_clipboard_image(abs_path: str) -> bool:
    """Load an image file onto the Windows clipboard (System.Drawing + Clipboard)."""
    if os.name != "nt":
        return False
    ap = os.path.abspath(os.path.normpath(abs_path))
    if not os.path.isfile(ap):
        return False
    env = {**os.environ, "WA_CLIPBOARD_IMAGE_PATH": ap}
    script = (
        "Add-Type -AssemblyName System.Windows.Forms, System.Drawing; "
        "$p = $env:WA_CLIPBOARD_IMAGE_PATH; "
        "if (-not (Test-Path -LiteralPath $p)) { exit 1 }; "
        "$img = [System.Drawing.Image]::FromFile($p); "
        "[System.Windows.Forms.Clipboard]::SetImage($img); "
        "$img.Dispose(); exit 0"
    )
    try:
        r = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            env=env,
            capture_output=True,
            timeout=45,
        )
        return r.returncode == 0
    except Exception:
        return False


def _try_clipboard_image_attach(
    driver: webdriver.Chrome,
    message_box,
    attach_list: list[str],
) -> str | None:
    """
    Fallback when <input type=file> upload fails: put each image on the Windows clipboard
    and paste into the compose box (Ctrl+V). PDFs and other files are not supported here.
    Returns None on success, else an error string.
    """
    if os.name != "nt":
        return "Clipboard image paste is only available on Windows"
    resolved = _resolve_attachment_paths(list(attach_list))
    if not resolved:
        return "No valid attachment files"
    exts = {os.path.splitext(p)[1].lower() for p in resolved}
    if exts - _CLIPBOARD_IMAGE_EXT:
        return "Clipboard fallback supports image files only (png, jpg, gif, bmp, webp, …)"
    for ap in resolved:
        if not _powershell_set_clipboard_image(ap):
            return f"Could not copy image to clipboard: {os.path.basename(ap)}"
        try:
            message_box.click()
            time.sleep(0.15)
            message_box.send_keys(Keys.CONTROL, "v")
            time.sleep(0.9)
        except Exception as e:
            return f"Paste into compose failed: {e!r}"[:300]
    return None


def _nudge_file_input_for_react(driver: webdriver.Chrome, inp: WebElement | None) -> None:
    """WhatsApp/React may need change/input after programmatic file assignment (CDP or send_keys)."""
    targets: list[WebElement] = []
    if inp is not None:
        targets.append(inp)
    else:
        targets.extend(driver.find_elements(By.CSS_SELECTOR, "footer input[type=file]"))
    for el in targets:
        try:
            driver.execute_script(
                "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));"
                "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
                el,
            )
        except Exception:
            continue


def _list_whatsapp_file_inputs(driver: webdriver.Chrome) -> list[WebElement]:
    """Collect file inputs WhatsApp owns (footer + main chat; DOM varies by WA version)."""
    seen: set[int] = set()
    out: list[WebElement] = []
    for by, sel in (
        (By.CSS_SELECTOR, "#main input[type=file]"),
        (By.CSS_SELECTOR, "footer input[type=file]"),
        (By.CSS_SELECTOR, "#app input[type=file]"),
    ):
        for el in driver.find_elements(by, sel):
            try:
                i = id(el)
            except Exception:
                continue
            if i in seen:
                continue
            seen.add(i)
            out.append(el)
    return out


def _accept_is_documentish(accept: str) -> bool:
    a = (accept or "").lower()
    if not a:
        return False
    if "*" in a:
        return True
    if "application" in a or ".pdf" in a or "text/" in a:
        return True
    # Long comma-separated accept lists are usually the document picker.
    if a.count(",") >= 3:
        return True
    return False


def _accept_is_media_only(accept: str) -> bool:
    a = (accept or "").strip().lower()
    if not a:
        return False
    return a == "image/*" or (a.startswith("image/") and "video" not in a and "*" not in a)


def _video_only_batch(paths: list[str]) -> bool:
    exts = {os.path.splitext(p)[1].lower() for p in paths if p}
    return bool(exts) and exts <= _VIDEO_EXT


def _needs_non_media_upload(paths: list[str]) -> bool:
    """True if any path is not a typical WA media extension (e.g. PDF) — needs a broad-accept input."""
    exts = {os.path.splitext(p)[1].lower() for p in paths if p}
    return bool(exts - _MEDIA_EXTENSIONS)


def _pick_file_input(inputs: list[WebElement], paths: list[str]) -> WebElement | None:
    """Pick the best hidden file input (never triggers the OS file dialog)."""
    if not inputs:
        return None
    exts = {os.path.splitext(p)[1].lower() for p in paths}
    video_only = bool(exts) and exts <= _VIDEO_EXT

    doc_candidates: list[WebElement] = []
    media_candidates: list[WebElement] = []
    for inp in inputs:
        accept = inp.get_attribute("accept") or ""
        if _accept_is_documentish(accept):
            doc_candidates.append(inp)
        if "image" in accept.lower() or "video" in accept.lower():
            media_candidates.append(inp)

    if video_only and media_candidates:
        return media_candidates[0]
    if doc_candidates:
        return doc_candidates[0]
    if media_candidates:
        return media_candidates[0]
    return inputs[0]


def _upload_attachments(driver: webdriver.Chrome, paths: list[str]) -> str | None:
    """
    Upload hidden file inputs without opening the Attach (+) menu.

    1) send_keys on the best footer/main input (reliable for WhatsApp + Selenium).
    2) If that fails, try Chrome CDP DOM.setFileInputFiles (no send_keys).
    Nudges input/change events so React/WA picks up the assignment.
    """
    resolved = _resolve_attachment_paths(paths)
    if not paths:
        return None
    if not resolved:
        return "No valid attachment files"
    ws_url = _chrome_page_websocket_debugger_url(driver) if ws_cdp else None
    attach_ctx = _FileChooserInterceptor(ws_url, list(resolved)) if ws_url else nullcontext()
    try:
        with attach_ctx:
            _dismiss_native_file_dialog(attempts=1, delay=0.1)
            _click_attach_menu(driver)
            time.sleep(0.25)
            _dismiss_native_file_dialog(attempts=2, delay=0.12)

            for_gallery = _all_paths_are_images_or_video(resolved)
            cdp_attached = _cdp_try_assign_files(
                driver,
                resolved,
                for_gallery,
                chooser_assigned=((lambda: attach_ctx.did_assign_files) if isinstance(attach_ctx, _FileChooserInterceptor) else None),
            )
            intercepted_attach = isinstance(attach_ctx, _FileChooserInterceptor) and attach_ctx.did_assign_files
            if not cdp_attached and not intercepted_attach:
                _dismiss_native_file_dialog(attempts=2, delay=0.12)
                _fallback_send_keys_file_input(driver, resolved, for_gallery)
                _dismiss_native_file_dialog(attempts=1, delay=0.08)
            if _all_paths_are_images_or_video(resolved):
                _switch_editor_from_sticker_to_photo(driver)
            if _wait_for_attachment_preview(driver, timeout_s=8.0):
                return None
            logger.warning("Attach-menu flow did not produce preview; trying hidden-input fallback.")
            return _upload_attachments_hidden_input_fallback(driver, resolved)
    except Exception as e:
        logger.warning("Attach-menu upload failed: %s. Trying hidden-input fallback.", e)
        fallback_err = _upload_attachments_hidden_input_fallback(driver, resolved)
        if fallback_err is None:
            return None
        return f"Could not upload files: {e!r} | Fallback: {fallback_err}"[:480]


def _cdp_click_viewport(driver: webdriver.Chrome, x: float, y: float) -> None:
    """Synthesize a left click at viewport CSS pixels (bypasses some hit-target / overlay issues)."""
    if not hasattr(driver, "execute_cdp_cmd"):
        return
    xi, yi = int(round(x)), int(round(y))
    try:
        driver.execute_cdp_cmd(
            "Input.dispatchMouseEvent",
            {"type": "mouseMoved", "x": xi, "y": yi, "pointerType": "mouse", "buttons": 0},
        )
        driver.execute_cdp_cmd(
            "Input.dispatchMouseEvent",
            {
                "type": "mousePressed",
                "x": xi,
                "y": yi,
                "pointerType": "mouse",
                "button": "left",
                "buttons": 1,
                "clickCount": 1,
            },
        )
        driver.execute_cdp_cmd(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseReleased",
                "x": xi,
                "y": yi,
                "pointerType": "mouse",
                "button": "left",
                "buttons": 0,
                "clickCount": 1,
            },
        )
    except Exception as e:
        logger.debug("CDP viewport click failed: %s", e)


# Icons may live in open shadow roots; span has data-testid / data-icon, parent may lack aria-label on same node.
_WA_CLICK_MEDIA_SEND_JS = r"""
(function () {
  var SEL = '[data-testid="wds-ic-send-filled"], [data-icon="wds-ic-send-filled"]';
  function queryAllDeep(sel) {
    var out = [];
    var stack = [document];
    var seen = new Set();
    while (stack.length) {
      var root = stack.pop();
      if (!root || !root.querySelectorAll || seen.has(root)) continue;
      seen.add(root);
      try {
        root.querySelectorAll(sel).forEach(function (n) { out.push(n); });
        root.querySelectorAll("*").forEach(function (el) {
          if (el.shadowRoot) stack.push(el.shadowRoot);
        });
      } catch (e) {}
    }
    return out;
  }
  function fireMouse(el, x, y) {
    try {
      el.dispatchEvent(new MouseEvent("pointerover", { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y }));
      el.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, button: 0 }));
      el.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, button: 0 }));
    } catch (e) {}
    try {
      el.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, button: 0 }));
      el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, button: 0 }));
      el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, button: 0 }));
      el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, button: 0 }));
    } catch (e2) {}
    try {
      el.click();
    } catch (e3) {}
    return true;
  }
  function tryClickAt(el) {
    if (!el) return false;
    try {
      el.scrollIntoView({ block: "center", inline: "center" });
    } catch (e) {}
    var target =
      (el.closest && el.closest('[role="button"][aria-label="Send"]')) ||
      (el.closest && el.closest('[role="button"]')) ||
      (el.closest && el.closest("button")) ||
      el;
    var r = target.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return false;
    var cx = r.left + r.width / 2;
    var cy = r.top + r.height / 2;
    return fireMouse(target, cx, cy);
  }
  var icons = Array.from(document.querySelectorAll(SEL));
  if (!icons.length) icons = queryAllDeep(SEL);
  for (var k = icons.length - 1; k >= 0; k--) {
    var ic = icons[k];
    var r = ic.getBoundingClientRect();
    if (r.width < 3 || r.height < 3) continue;
    var x = r.left + r.width / 2;
    var y = r.top + r.height / 2;
    var sendHost = ic.closest && ic.closest('[role="button"][aria-label="Send"]');
    if (sendHost && tryClickAt(sendHost)) return true;
    var hit = document.elementFromPoint(x, y);
    var hitRel =
      hit &&
      ((typeof ic.contains === "function" && ic.contains(hit)) ||
        (hit.contains && hit.contains(ic)));
    if (hitRel && tryClickAt(hit)) return true;
    if (tryClickAt(ic)) return true;
    var p = ic.parentElement;
    for (var d = 0; d < 24 && p; d++, p = p.parentElement) {
      if (tryClickAt(p)) return true;
    }
  }
  var nodes = document.querySelectorAll('[aria-label="Send"]');
  var best = null;
  var bestY = -1e12;
  for (var j = 0; j < nodes.length; j++) {
    var el = nodes[j];
    if (el.getAttribute("aria-disabled") === "true") continue;
    var rr = el.getBoundingClientRect();
    if (rr.width < 6 || rr.height < 6) continue;
    var cy = rr.top + rr.height / 2;
    if (cy > bestY) {
      bestY = cy;
      best = el;
    }
  }
  if (best) {
    return tryClickAt(best);
  }
  return false;
})()
"""

_WA_SEND_DIV_CENTER_FOR_CDP_JS = r"""
(function () {
  var nodes = document.querySelectorAll('[role="button"][aria-label="Send"]');
  for (var i = nodes.length - 1; i >= 0; i--) {
    var d = nodes[i];
    if (d.getAttribute("aria-disabled") === "true") continue;
    if (!d.querySelector('[data-testid="wds-ic-send-filled"], [data-icon="wds-ic-send-filled"]')) continue;
    var r = d.getBoundingClientRect();
    if (r.width >= 12 && r.height >= 12) {
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    }
  }
  return null;
})()
"""


def _wait_and_click_preview_send(driver: webdriver.Chrome, max_wait: float) -> bool:
    """
    Wait for the media-preview Send control (stable aria-label), let React settle, then:
    Selenium click -> JS click -> Enter on footer compose (matches common WA automation fixes).
    """
    xp = (
        "//div[@role='button' and @aria-label='Send' and not(@aria-disabled='true')]"
        "[.//span[@data-testid='wds-ic-send-filled' or @data-icon='wds-ic-send-filled']]"
    )
    end = time.time() + max(0.5, max_wait)
    while time.time() < end:
        els = driver.find_elements(By.XPATH, xp)
        if els:
            btn = els[-1]
            time.sleep(1.0)
            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center', inline: 'center'});",
                    btn,
                )
            except Exception:
                pass
            time.sleep(0.12)
            try:
                btn.click()
                logger.info("Preview Send: native click succeeded.")
                return True
            except Exception as e:
                logger.debug("Preview Send native click: %s", e)
            try:
                driver.execute_script("arguments[0].click();", btn)
                logger.info("Preview Send: JS click succeeded.")
                return True
            except Exception as e:
                logger.debug("Preview Send JS click: %s", e)
            try:
                compose = driver.find_element(
                    By.XPATH, "//footer//div[@contenteditable='true' and @role='textbox']"
                )
                compose.click()
                time.sleep(0.08)
                compose.send_keys(Keys.ENTER)
                logger.info("Preview Send: used footer compose Enter fallback.")
                return True
            except Exception as e:
                logger.debug("Preview Send Enter fallback: %s", e)
            return False
        time.sleep(0.28)
    return False


def _click_send_after_upload(driver: webdriver.Chrome) -> bool:
    """
    WhatsApp rebuilds the footer/preview DOM after files are attached, so any
    element found before upload can go stale. Re-locate the send control on
    each attempt and use JS click as a fallback.
    """
    logger.info("Locating Send control and clicking after attachment upload…")
    time.sleep(0.75)
    pairs = [
        (By.CSS_SELECTOR, '[data-testid="send"]'),
        (By.CSS_SELECTOR, 'span[data-icon="wds-ic-send-filled"]'),
        (By.CSS_SELECTOR, 'span[data-icon="send"]'),
        (By.CSS_SELECTOR, 'button[data-testid="compose-btn-send"]'),
        (By.XPATH, "//div[@role='button' and (@aria-label='Send' or @aria-label='Send message')]"),
    ]
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        for by, sel in pairs:
            try:
                el = WebDriverWait(driver, 2).until(EC.element_to_be_clickable((by, sel)))
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                try:
                    el.click()
                except (StaleElementReferenceException, ElementClickInterceptedException):
                    try:
                        el = driver.find_element(by, sel)
                        driver.execute_script("arguments[0].click();", el)
                    except Exception:
                        time.sleep(0.3)
                        continue
                logger.info("Send clicked via selector %s %r.", by, sel)
                return True
            except TimeoutException:
                continue
            except StaleElementReferenceException:
                time.sleep(0.25)
                continue
        time.sleep(0.2)

    # Fallback: focus chat and press Enter (often confirms media preview)
    try:
        for box_sel in (
            '[data-testid="conversation-compose-box-input"]',
            "footer div[contenteditable='true']",
        ):
            try:
                box = driver.find_element(By.CSS_SELECTOR, box_sel)
                box.click()
                ActionChains(driver).send_keys(Keys.ENTER).perform()
                logger.info("Send attempted via compose box Enter fallback (%s).", box_sel)
                return True
            except Exception:
                continue
    except Exception:
        pass
    logger.warning("Send control not found within timeout.")
    return False


def _try_click_whatsapp_send_button(driver: webdriver.Chrome) -> bool:
    return _click_send_after_upload(driver)


def _normalize_phone(phone: str) -> str:
    return "".join(c for c in phone if c.isdigit())


def _clear_search_box(search_box) -> None:
    """Hard-clear WhatsApp search input so new searches never append."""
    search_box.click()
    try:
        search_box.clear()
    except Exception:
        pass
    # Reliable clear for contenteditable/input variants.
    search_box.send_keys(Keys.CONTROL, "a")
    search_box.send_keys(Keys.BACKSPACE)


def _search_shows_no_results(driver: webdriver.Chrome) -> bool:
    """True when WhatsApp shows the standard 'no results' empty search message."""
    try:
        xpath = (
            "//span[contains(normalize-space(.),"
            f" '{_NO_SEARCH_RESULTS_TEXT}')]"
        )
        for el in driver.find_elements(By.XPATH, xpath):
            try:
                if el.is_displayed():
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _try_dismiss_not_on_whatsapp_modal(driver: webdriver.Chrome) -> bool:
    """
    If WhatsApp shows \"The number ... isn't on WhatsApp\", click OK immediately.
    Uses aria-label on the modal (fast/reliable) plus text fallbacks.
    Returns True if a modal was dismissed (send flow should stop as failure).
    """
    not_on_wa = "isn't on WhatsApp"
    root_xpaths = [
        # WhatsApp sets this on the popup wrapper (user-reported DOM).
        f"//*[contains(@aria-label, \"{not_on_wa}\")]",
        "//div[@data-animate-modal-popup='true'][.//span[contains(., \"isn't on WhatsApp\")]]",
        f"//div[contains(normalize-space(.), \"isn't on WhatsApp.\")]",
    ]
    ok_rel_xpaths = (
        ".//span[normalize-space(text())='OK']/ancestor::button[1]",
        ".//button[.//span[normalize-space(text())='OK']]",
    )
    for rx in root_xpaths:
        try:
            for root in driver.find_elements(By.XPATH, rx):
                try:
                    if not root.is_displayed():
                        continue
                except Exception:
                    continue
                for ok_xpath in ok_rel_xpaths:
                    try:
                        btn = root.find_element(By.XPATH, ok_xpath)
                        if btn.is_displayed():
                            btn.click()
                            time.sleep(0.15)
                            return True
                    except Exception:
                        continue
        except Exception:
            continue
    return False


def _open_chat_via_phone_link_same_tab(driver: webdriver.Chrome, phone_digits: str) -> bool:
    """
    Open the chat in the same tab via WhatsApp Web's send URL (same session, no new tabs).
    Avoids https://wa.me/... which often opens intermediate pages or target=_blank links.
    If WhatsApp shows a modal like \"The number ... isn't on WhatsApp.\", click OK and
    report failure so the caller can mark the row as ERROR and continue.
    """
    footer = (By.XPATH, "//footer//div[@contenteditable='true' and @role='textbox']")
    send_url = f"https://web.whatsapp.com/send?phone={phone_digits}"
    try:
        driver.get(send_url)
    except Exception:
        return False

    # Prefer dismissing error modal before checking compose (immediate OK -> next message).
    deadline = time.time() + CHAT_LOAD_TIMEOUT
    poll = 0.1
    while time.time() < deadline:
        if _try_dismiss_not_on_whatsapp_modal(driver):
            return False
        try:
            for el in driver.find_elements(*footer):
                if el.is_displayed():
                    return True
        except Exception:
            pass
        time.sleep(poll)

    return False


def create_driver_for_profile(client_phno: str) -> webdriver.Chrome:
    """Create Chrome driver for this client's profile. Caller must quit() when done."""
    profile_dir = get_profile_dir(client_phno)
    os.makedirs(profile_dir, exist_ok=True)
    chrome_options = Options()
    chrome_options.add_argument("--user-data-dir=" + profile_dir)
    chrome_options.add_argument("--profile-directory=Default")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--remote-allow-origins=*")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=chrome_options)


_SEARCH_LOCATORS = [
    (By.XPATH, "//input[@role='textbox' and @type='text' and @data-tab='3']"),
    (By.XPATH, "//div[@contenteditable='true' and @data-tab='3']"),
    (By.XPATH, "//div[@contenteditable='true' and @aria-label='Search']"),
]


def _find_side_search_box(driver: webdriver.Chrome, wait: WebDriverWait):
    """Return WhatsApp left-panel search element, or None."""
    for locator in _SEARCH_LOCATORS:
        try:
            return wait.until(EC.element_to_be_clickable(locator))
        except (TimeoutException, WebDriverException):
            continue
    return None


def open_whatsapp_web(driver: webdriver.Chrome) -> str:
    """Open WhatsApp Web in the given driver. Returns 'SUCCESS' or error string."""
    try:
        driver.get("https://web.whatsapp.com/")
        wait = WebDriverWait(driver, CHAT_LOAD_TIMEOUT)
        wait.until(EC.presence_of_element_located((By.ID, "side")))
        time.sleep(3)
        return "SUCCESS"
    except Exception as e:
        return f"Open WhatsApp failed: {e!r}"[:500]


_NEW_CHAT_BUTTON_LOCATORS = [
    (By.CSS_SELECTOR, "span[data-icon='new-chat-outline']"),
    (By.CSS_SELECTOR, "span[data-icon='chat-new']"),
    (By.CSS_SELECTOR, "[aria-label='New chat']"),
    (By.XPATH, "//div[@role='button'][@aria-label='New chat']"),
    (By.XPATH, "//button[@aria-label='New chat']"),
]


def _try_click_new_chat(driver: webdriver.Chrome) -> bool:
    short_wait = WebDriverWait(driver, 8)
    for by, sel in _NEW_CHAT_BUTTON_LOCATORS:
        try:
            el = short_wait.until(EC.element_to_be_clickable((by, sel)))
            el.click()
            time.sleep(1.2)
            return True
        except (TimeoutException, WebDriverException, ElementClickInterceptedException):
            continue
    return False


def _try_click_back_or_escape(driver: webdriver.Chrome) -> None:
    for by, sel in (
        (By.CSS_SELECTOR, "span[data-icon='back']"),
        (By.CSS_SELECTOR, "[aria-label='Back']"),
        (By.XPATH, "//button[@aria-label='Back']"),
        (By.XPATH, "//div[@role='button'][@aria-label='Back']"),
    ):
        try:
            for el in driver.find_elements(by, sel):
                try:
                    if el.is_displayed():
                        el.click()
                        time.sleep(0.4)
                        return
                except Exception:
                    continue
        except Exception:
            continue
    try:
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        time.sleep(0.35)
    except Exception:
        pass


_SKIP_TITLE_PREFIXES = (
    "new chat",
    "search",
    "menu",
    "settings",
    "status",
    "communities",
    "channels",
    "archived",
)


def _title_is_contact_candidate(title: str) -> bool:
    t = (title or "").strip()
    if len(t) < 2:
        return False
    low = t.lower()
    for p in _SKIP_TITLE_PREFIXES:
        if low == p or low.startswith(p + " "):
            return False
    return True


def _scroll_candidate_for_new_chat_list(driver: webdriver.Chrome):
    try:
        return driver.execute_script(
            """
            const pane = document.getElementById('pane-side');
            if (!pane) return null;
            const cand = pane.querySelectorAll('div[tabindex="0"]');
            for (const d of cand) {
              if (d.scrollHeight > d.clientHeight + 80) return d;
            }
            for (const d of pane.querySelectorAll('div')) {
              if (d.scrollHeight > d.clientHeight + 200) return d;
            }
            return pane;
            """
        )
    except Exception:
        return None


def sync_whatsapp_contacts_from_new_chat(
    driver: webdriver.Chrome, max_rounds: int = 70, stable_stop: int = 4
) -> tuple[str, list[str]]:
    """
    Open the New chat panel and scroll the contact list to collect display names.
    Returns ('SUCCESS', names) or (error_string, []).
    """
    try:
        if not _try_click_new_chat(driver):
            return ("Could not find the New chat button (is WhatsApp fully loaded?)", [])
        time.sleep(0.6)
        names: set[str] = set()
        prev_count = -1
        stable = 0
        for _ in range(max_rounds):
            try:
                for el in driver.find_elements(By.XPATH, "//div[@role='row']//span[@title]"):
                    try:
                        raw = (el.get_attribute("title") or "").strip()
                        if _title_is_contact_candidate(raw):
                            names.add(raw)
                    except StaleElementReferenceException:
                        continue
                    except Exception:
                        continue
            except Exception:
                pass
            n = len(names)
            if n == prev_count:
                stable += 1
                if stable >= stable_stop:
                    break
            else:
                stable = 0
            prev_count = n
            sc = _scroll_candidate_for_new_chat_list(driver)
            if sc is None:
                break
            try:
                driver.execute_script(
                    "arguments[0].scrollTop = arguments[0].scrollTop + Math.max(120, arguments[0].clientHeight * 0.85);",
                    sc,
                )
            except Exception:
                break
            time.sleep(0.35)
        _try_click_back_or_escape(driver)
        time.sleep(0.5)
        out = sorted(names, key=lambda s: s.lower())
        if not out:
            return (
                "No contact names were read. Open New chat manually once to confirm the layout, then try again.",
                [],
            )
        return ("SUCCESS", out)
    except Exception as e:
        _try_click_back_or_escape(driver)
        return (f"Contact sync failed: {e!r}"[:500], [])


def _click_side_search_result_row_for_name(driver: webdriver.Chrome, display_name: str) -> bool:
    want = (display_name or "").strip().lower()
    if not want:
        return False
    exact_el = None
    partial_el = None
    partial_len = 10**9
    for el in driver.find_elements(By.XPATH, "//div[@role='row']//span[@title]"):
        try:
            t = (el.get_attribute("title") or "").strip()
            if not t:
                continue
            tl = t.lower()
            if tl == want:
                exact_el = el
                break
            if want in tl or tl in want:
                if len(t) < partial_len:
                    partial_len = len(t)
                    partial_el = el
        except StaleElementReferenceException:
            continue
        except Exception:
            continue
    chosen = exact_el or partial_el
    if chosen is None:
        return False
    try:
        row = chosen.find_element(By.XPATH, "./ancestor::div[@role='row'][1]")
        row.click()
        return True
    except Exception:
        return False


def _open_direct_chat_by_display_name(
    driver: webdriver.Chrome,
    wait: WebDriverWait,
    number_wait: WebDriverWait,
    display_name: str,
) -> str | None:
    """Return None on success, error string on failure."""
    name = (display_name or "").strip()
    if not name:
        return "Empty display name"
    search_box = _find_side_search_box(driver, wait)
    if not search_box:
        return "Search box not found"
    _clear_search_box(search_box)
    search_box.send_keys(name)
    time.sleep(1.2)
    if _search_shows_no_results(driver):
        return "No matching contact in WhatsApp search"
    deadline = time.time() + NUMBER_SEARCH_TIMEOUT
    while time.time() < deadline:
        if _click_side_search_result_row_for_name(driver, name):
            try:
                number_wait.until(
                    EC.presence_of_element_located(
                        (By.XPATH, "//footer//div[@contenteditable='true' and @role='textbox']")
                    )
                )
                return None
            except (TimeoutException, WebDriverException):
                pass
        time.sleep(0.3)
    try:
        search_box.send_keys(Keys.ENTER)
        number_wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//footer//div[@contenteditable='true' and @role='textbox']")
            )
        )
        return None
    except (TimeoutException, WebDriverException):
        return "Could not open chat for this contact name"


def send_message(
    driver: webdriver.Chrome,
    receiver_identifier: str,
    message: str,
    is_group: bool,
    allow_search: bool = False,
    attachment_paths: list[str] | None = None,
) -> str:
    """
    Send one message. Does not raise. Returns 'SUCCESS' or error string (for DB).
    Group search limited to GROUP_SEARCH_TIMEOUT seconds; if group not found, returns error.
    For direct numbers: if allow_search is False (default), open chat only via
    web.whatsapp.com/send?phone=...; if True, use side search first (with link fallback).
    Optional attachment_paths: local file paths uploaded via WhatsApp Web footer file input
    before the message is typed (caption) and sent.
    """
    try:
        wait = WebDriverWait(driver, CHAT_LOAD_TIMEOUT)
        group_wait = WebDriverWait(driver, GROUP_SEARCH_TIMEOUT)
        number_wait = WebDriverWait(driver, NUMBER_SEARCH_TIMEOUT)

        if is_group:
            search_box = _find_side_search_box(driver, wait)
            if not search_box:
                return "Search box not found"

            _clear_search_box(search_box)
            search_box.send_keys(receiver_identifier)
            search_box.send_keys(Keys.ENTER)
            time.sleep(2)
            try:
                chat = group_wait.until(
                    EC.element_to_be_clickable(
                        (By.XPATH, f"//span[@title='{receiver_identifier}']")
                    )
                )
                chat.click()
            except (TimeoutException, WebDriverException):
                return "Group not found (timeout)"
        else:
            raw_id = (receiver_identifier or "").strip()
            phone_digits = _normalize_phone(receiver_identifier)
            if not phone_digits and raw_id:
                if not allow_search:
                    return "Enable “search by name” to send using saved WhatsApp contact names (no phone number)"
                err = _open_direct_chat_by_display_name(driver, wait, number_wait, raw_id)
                if err:
                    return err
            elif not phone_digits:
                return "Invalid phone number"
            elif not allow_search:
                if not _open_chat_via_phone_link_same_tab(driver, phone_digits):
                    return "Web send link could not open chat"
            else:
                search_box = _find_side_search_box(driver, wait)
                if not search_box:
                    return "Search box not found"
                search_terms = [receiver_identifier, phone_digits, phone_digits[-10:]]
                opened = False
                for term in search_terms:
                    if not term:
                        continue
                    _clear_search_box(search_box)
                    search_box.send_keys(term)
                    time.sleep(1.2)
                    if _search_shows_no_results(driver):
                        break
                    try:
                        chat = number_wait.until(
                            EC.element_to_be_clickable(
                                (
                                    By.XPATH,
                                    (
                                        f"//span[contains(@title,'{term}')]/ancestor::div[@role='row'][1]"
                                        f"|//span[contains(@title,'{phone_digits[-10:]}')]/ancestor::div[@role='row'][1]"
                                    ),
                                ),
                            )
                        )
                        chat.click()
                        time.sleep(1)
                        number_wait.until(
                            EC.presence_of_element_located(
                                (By.XPATH, "//footer//div[@contenteditable='true' and @role='textbox']")
                            )
                        )
                        opened = True
                        break
                    except (TimeoutException, WebDriverException):
                        if _search_shows_no_results(driver):
                            break
                        try:
                            search_box.send_keys(Keys.ENTER)
                            number_wait.until(
                                EC.presence_of_element_located(
                                    (By.XPATH, "//footer//div[@contenteditable='true' and @role='textbox']")
                                )
                            )
                            opened = True
                            break
                        except (TimeoutException, WebDriverException):
                            pass
                        if _search_shows_no_results(driver):
                            break
                        continue
                if not opened:
                    if not _open_chat_via_phone_link_same_tab(driver, phone_digits):
                        return (
                            "Phone not found in WhatsApp search; web send link could not open chat"
                        )

        time.sleep(1)
        # Footer compose box is the most stable; try it first so we do not sit on a
        # stale data-tab XPath for up to CHAT_LOAD_TIMEOUT seconds each.
        message_box_locators = [
            (By.XPATH, "//footer//div[@contenteditable='true' and @role='textbox']"),
            (By.XPATH, "//div[@contenteditable='true' and @data-tab='10']"),
            (By.XPATH, "//div[@contenteditable='true' and @data-tab='6']"),
        ]
        message_box = None
        box_wait = WebDriverWait(driver, MESSAGE_BOX_LOCATOR_TIMEOUT)
        for locator in message_box_locators:
            try:
                message_box = box_wait.until(EC.element_to_be_clickable(locator))
                break
            except (TimeoutException, WebDriverException):
                continue
        if not message_box:
            return "Message box not found"

        attach_list = list(attachment_paths or [])
        if attach_list:
            resolved_attach = _resolve_attachment_paths(attach_list)
            logger.info(
                "Attachment preflight: requested=%d resolved=%d names=%s",
                len(attach_list),
                len(resolved_attach),
                [os.path.basename(p) for p in resolved_attach],
            )
            ws_dbg = _chrome_page_websocket_debugger_url(driver) if ws_cdp else None
            logger.info("Attachment preflight: cdp_websocket=%s", ws_dbg or "none")
            err = _upload_attachments(driver, attach_list)
            if err:
                clip_err = _try_clipboard_image_attach(driver, message_box, attach_list)
                if clip_err:
                    return f"{err} | Clipboard fallback: {clip_err}"[:500]
            message_box = None
            for locator in message_box_locators:
                try:
                    message_box = box_wait.until(EC.element_to_be_clickable(locator))
                    break
                except (TimeoutException, WebDriverException):
                    continue
            if not message_box:
                return "Message box not found after attachment upload"
            time.sleep(0.45)

        had_attachments = bool(_resolve_attachment_paths(attach_list))

        if had_attachments:
            if message:
                _set_attachment_caption(driver, message)
            if not _try_click_whatsapp_send_button(driver):
                return "Attachment uploaded but could not click Send"
        elif message:
            _insert_text_into_contenteditable(driver, message_box, message)
            message_box.send_keys(Keys.ENTER)
        time.sleep(1)
        return "SUCCESS"
    except Exception as e:
        return f"Selenium/error: {e!r}"[:500]
