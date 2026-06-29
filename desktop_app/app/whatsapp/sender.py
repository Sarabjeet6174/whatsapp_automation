"""
WhatsApp Web sender for desktop app. Uses shared driver, 20s group timeout, never raises.
Returns "SUCCESS" or error string for DB logging.
"""
import logging
import os
import json
import re
import subprocess
import sys
import threading
import time
from typing import Callable
import urllib.error
import urllib.request
from contextlib import nullcontext

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    WebDriverException,
    TimeoutException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
)
from selenium.webdriver.remote.webelement import WebElement

from config import get_profile_dir

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], None] | None


def _report_progress(progress: ProgressCallback, msg: str) -> None:
    logger.info(msg)
    if progress:
        try:
            progress(msg)
        except Exception:
            pass


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
            logger.info("CDP WebSocket connected for file chooser interception.")
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
                logger.info(
                    "Intercepted native file chooser (backendNodeId=%s); assigning %d file(s) via CDP.",
                    bid,
                    len(self.paths),
                )
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
    """Send ESC — only when a native Open dialog is confirmed visible (see _dismiss_native_file_dialog_if_open)."""
    if pyautogui is None:
        return
    for _ in range(attempts):
        try:
            pyautogui.press("esc")
            time.sleep(delay)
        except Exception:
            break


def _win32_file_open_dialog_visible() -> bool:
    """True when a visible Windows common-file-dialog (#32770) is on screen."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found = False

        def _enum_cb(hwnd, _lparam):
            nonlocal found
            if not user32.IsWindowVisible(hwnd):
                return True
            class_name = ctypes.create_unicode_buffer(260)
            user32.GetClassNameW(hwnd, class_name, 260)
            if class_name.value != "#32770":
                return True
            title = ctypes.create_unicode_buffer(520)
            user32.GetWindowTextW(hwnd, title, 520)
            t = title.value.lower()
            if "open" in t or "choose file" in t or "file upload" in t or "select file" in t:
                found = True
                return False
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
        return found
    except Exception:
        return False


def _dismiss_native_file_dialog_if_open(attempts: int = 2, delay: float = 0.1) -> bool:
    """Close the OS file picker with ESC only when it is actually open — never while WA chat is focused."""
    if not _win32_file_open_dialog_visible():
        return False
    logger.info("Closing native Windows Open dialog.")
    _dismiss_native_file_dialog(attempts=attempts, delay=delay)
    time.sleep(0.06)
    return True


def _footer_compose_visible(driver: webdriver.Chrome) -> bool:
    try:
        return bool(_snapshot_wa_ui_state(driver).get("elements", {}).get("compose_visible"))
    except Exception:
        return False


def _footer_ready_for_attach(driver: webdriver.Chrome) -> bool:
    try:
        el = _snapshot_wa_ui_state(driver).get("elements") or {}
        return bool(el.get("compose_visible") or el.get("footer_attach_visible"))
    except Exception:
        return False


def _attach_menu_is_open(driver: webdriver.Chrome) -> bool:
    try:
        return "attach-menu-dropdown" in (_snapshot_wa_ui_state(driver).get("screens") or [])
    except Exception:
        return False


def _attach_flow_in_progress(driver: webdriver.Chrome) -> bool:
    if _attachment_preview_is_open(driver):
        return True
    if _attach_menu_is_open(driver):
        return True
    try:
        el = _snapshot_wa_ui_state(driver).get("elements") or {}
        if el.get("caption_visible") or el.get("preview_send_visible"):
            return True
    except Exception:
        pass
    return False


def _chat_is_no_selection(driver: webdriver.Chrome) -> bool:
    try:
        return "no-chat-selected" in (_snapshot_wa_ui_state(driver).get("screens") or [])
    except Exception:
        return False


def _wait_attachment_preview_closed(driver: webdriver.Chrome, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + max(0.5, timeout)
    while time.monotonic() < deadline:
        if not _attachment_preview_is_open(driver):
            return
        time.sleep(0.25)


def _force_close_file_picker_at_session_end() -> None:
    if pyautogui is None:
        return
    try:
        pyautogui.press("escape")
        time.sleep(0.1)
        pyautogui.press("escape")
    except Exception:
        pass


def _dismiss_discard_selection_modal(driver: webdriver.Chrome, prefer: str = "cancel") -> bool:
    want = "discard" if (prefer or "cancel").lower() == "discard" else "cancel"
    try:
        dismissed = driver.execute_script(
            r"""
            function vis(el) {
              if (!el) return false;
              var r = el.getBoundingClientRect();
              return r.width > 4 && r.height > 4;
            }
            var prefer = (arguments[0] || 'cancel').toLowerCase();
            var dialogs = document.querySelectorAll(
              '[role="dialog"], [data-animate-modal-popup="true"], [data-animate-modal="true"]'
            );
            for (var i = 0; i < dialogs.length; i++) {
              var d = dialogs[i];
              if (!vis(d)) continue;
              var t = (d.textContent || '').trim();
              if (t.indexOf('Discard selection') < 0) continue;
              var btns = d.querySelectorAll('button, [role="button"]');
              var cancelBtn = null, discardBtn = null;
              for (var j = 0; j < btns.length; j++) {
                var b = btns[j];
                var lab = ((b.textContent || '') + ' ' + (b.getAttribute('aria-label') || '')).trim().toLowerCase();
                if (lab === 'cancel' || lab.indexOf('cancel') === 0) cancelBtn = b;
                if (lab === 'discard' || lab.indexOf('discard') === 0) discardBtn = b;
              }
              var pick = prefer === 'discard' ? (discardBtn || cancelBtn) : (cancelBtn || discardBtn);
              if (pick) { try { pick.click(); return true; } catch (e) {} }
            }
            return false;
            """,
            want,
        )
        if dismissed:
            logger.info("Dismissed 'Discard selection?' modal (%s).", want)
            time.sleep(0.15)
            return True
    except Exception:
        pass
    return False


_DISMISS_WA_OK_MODAL_JS = r"""
function vis(el) {
  if (!el) return false;
  var r = el.getBoundingClientRect();
  return r.width > 4 && r.height > 4;
}
var dialogs = document.querySelectorAll('[role="dialog"], [data-animate-modal-popup="true"], [data-animate-modal="true"]');
for (var i = 0; i < dialogs.length; i++) {
  var d = dialogs[i];
  if (!vis(d)) continue;
  var t = (d.textContent || '').trim();
  if (t.indexOf('Cannot set to HD') < 0 && t.indexOf('not HD resolution') < 0) continue;
  var btns = d.querySelectorAll('button, [role="button"]');
  for (var j = 0; j < btns.length; j++) {
    var b = btns[j];
    var lab = ((b.textContent || '') + ' ' + (b.getAttribute('aria-label') || '')).trim().toUpperCase();
    if (lab === 'OK' || lab.indexOf('OK') === 0) {
      try { b.click(); return true; } catch (e) {}
    }
  }
}
return false;
"""


def _dismiss_media_editor_modals(driver: webdriver.Chrome) -> bool:
    try:
        if driver.execute_script(_DISMISS_WA_OK_MODAL_JS):
            logger.info("Dismissed 'Cannot set to HD' modal.")
            time.sleep(0.12)
            return True
    except Exception:
        pass
    return False


def cleanup_whatsapp_send_session(driver: webdriver.Chrome) -> None:
    _dismiss_discard_selection_modal(driver, prefer="discard")
    _dismiss_media_editor_modals(driver)
    _dismiss_native_file_dialog_if_open(attempts=2, delay=0.1)
    _force_close_file_picker_at_session_end()


def _cleanup_after_attachment_send(driver: webdriver.Chrome) -> None:
    """Between queued recipients — close preview overlay and any stuck OS file picker."""
    _wait_attachment_preview_closed(driver, timeout=12.0)
    _dismiss_native_file_dialog_if_open(attempts=2, delay=0.1)
    _dismiss_discard_selection_modal(driver, prefer="discard")


def _prepare_attachment_send_after_chat_open(
    driver: webdriver.Chrome,
    phone_digits: str | None,
    progress: ProgressCallback = None,
) -> bool:
    _dismiss_native_file_dialog_if_open(attempts=1, delay=0.08)
    _dismiss_select_chats_modal(driver)
    _dismiss_discard_selection_modal(driver, prefer="discard")
    _dismiss_media_editor_modals(driver)
    _wait_attachment_preview_closed(driver, timeout=4.0)
    return _wait_direct_chat_settled(driver, timeout=6.0)


def _ensure_chat_compose_ready(driver: webdriver.Chrome) -> None:
    """Focus footer compose so the attach control is visible on newer WhatsApp Web builds."""
    try:
        driver.execute_script(
            """
            const footer = document.querySelector('footer');
            const box = footer && footer.querySelector(
              "div[contenteditable='true'][role='textbox'], div[contenteditable='true'][data-tab]"
            );
            if (box) { try { box.click(); box.focus(); } catch (e) {} }
            """
        )
        time.sleep(0.35)
    except Exception:
        pass


_WA_UI_SNAPSHOT_JS = r"""
function vis(el) {
  if (!el) return false;
  var r = el.getBoundingClientRect();
  if (r.width < 4 || r.height < 4) return false;
  var s = window.getComputedStyle(el);
  if (s.display === 'none' || s.visibility === 'hidden') return false;
  if (parseFloat(s.opacity || '1') < 0.05) return false;
  return true;
}
function txt(el) {
  return ((el && (el.getAttribute('aria-label') || el.getAttribute('title') || el.textContent)) || '').trim();
}
var out = { url: location.href, screens: [], elements: {} };
if (vis(document.querySelector('canvas[aria-label*="QR"], [data-testid="qrcode"]'))) {
  out.screens.push('login-QR');
}
var chatModal = document.querySelector('[data-testid="chat-modal"]');
if (vis(chatModal)) {
  out.screens.push('select-chats-modal');
  var title = chatModal.querySelector('[data-testid="drawer-title-body"], h2');
  out.elements.dialog = txt(title) || 'Select chats';
}
var modals = document.querySelectorAll('[role="dialog"], [data-animate-modal-popup="true"], [data-animate-modal="true"]');
for (var m = 0; m < modals.length; m++) {
  var dlg = modals[m];
  if (!vis(dlg)) continue;
  var t = txt(dlg).slice(0, 120);
  if (/new chat|search contacts|select contact|choose contact/i.test(t)) {
    out.screens.push('new-chat-contact-picker');
    out.elements.dialog = t || 'new-chat-modal';
  } else if (/isn't on WhatsApp|not on WhatsApp|invalid number/i.test(t)) {
    out.screens.push('not-on-whatsapp-modal');
    out.elements.dialog = t || 'not-on-whatsapp';
  } else if (t) {
    out.screens.push('modal:' + t.slice(0, 48));
    out.elements.dialog = t.slice(0, 80);
  }
}
var sideSearch = document.querySelector('#side input[type="text"], #side div[contenteditable="true"]');
if (vis(sideSearch)) {
  var q = (sideSearch.value || sideSearch.textContent || '').trim();
  if (q.length > 0 || document.activeElement === sideSearch) {
    out.screens.push('sidebar-search-active');
    out.elements.sidebar_search_query = q.slice(0, 40);
  }
}
var main = document.querySelector('#main');
if (main && vis(main)) out.screens.push('#main-panel');
var compose = document.querySelector(
  '#main footer div[contenteditable="true"], [data-testid="conversation-compose-box-input"]'
);
out.elements.compose_visible = vis(compose);
if (vis(compose)) out.screens.push('chat-compose-footer');
var footerAttach = document.querySelector(
  '#main footer span[data-icon="plus"], #main footer span[data-icon="plus-rounded"], ' +
  '#main footer [aria-label="Attach"]'
);
out.elements.footer_attach_visible = vis(footerAttach);
if (vis(footerAttach)) out.screens.push('footer-attach-plus');
var sidebarPlus = document.querySelector(
  '#side header span[data-icon="plus"], #side span[data-icon="new-chat-outline"], #side [aria-label="New chat"]'
);
out.elements.sidebar_new_chat_plus = vis(sidebarPlus);
if (vis(sidebarPlus)) out.screens.push('sidebar-new-chat-plus');
var attachMenu = document.querySelector(
  '[data-testid="attach-menu"], [role="menu"] [data-animate-dropdown-item], div[data-animate-dropdown-item="true"]'
);
if (vis(attachMenu)) out.screens.push('attach-menu-dropdown');
var media = document.querySelector(
  '[data-testid="media-editor"], [data-testid="media-viewer"], [data-animate-media-popup="true"], [data-testid="preview-generic"]'
);
if (vis(media)) out.screens.push('attachment-preview');
var cap = document.querySelector('[data-testid="media-caption-input-container"]');
out.elements.caption_visible = vis(cap);
if (vis(cap)) out.screens.push('attachment-caption-box');
var previewSend = document.querySelector(
  '[aria-label*="Send"][aria-label*="selected"], span[data-icon="wds-ic-send-filled"]'
);
out.elements.preview_send_visible = vis(previewSend);
if (vis(previewSend)) out.screens.push('preview-send-button');
var mainHeader = document.querySelector('#main header');
if (vis(document.querySelector('#pane-side')) && !vis(compose) && !vis(mainHeader)) {
  out.screens.push('no-chat-selected');
}
if (!out.screens.length) out.screens.push('unknown');
out.primary = out.screens[out.screens.length - 1];
return out;
"""


def _snapshot_wa_ui_state(driver: webdriver.Chrome) -> dict:
    try:
        raw = driver.execute_script(_WA_UI_SNAPSHOT_JS)
        return raw if isinstance(raw, dict) else {"url": "?", "screens": ["unknown"], "elements": {}}
    except Exception as e:
        return {"url": "?", "screens": ["snapshot-error"], "elements": {}, "error": str(e)}


def _format_wa_ui_state(snap: dict) -> str:
    screens = snap.get("screens") or ["unknown"]
    el = snap.get("elements") or {}
    url = str(snap.get("url") or "?")
    if len(url) > 72:
        url = url[:69] + "..."
    parts = [f"visible={', '.join(screens)}"]
    parts.append(f"compose={'YES' if el.get('compose_visible') else 'no'}")
    parts.append(f"footer_attach={'YES' if el.get('footer_attach_visible') else 'no'}")
    if el.get("sidebar_new_chat_plus"):
        parts.append("sidebar_plus=YES")
    if el.get("preview_send_visible"):
        parts.append("preview_send=YES")
    if el.get("caption_visible"):
        parts.append("caption=YES")
    if el.get("sidebar_search_query"):
        parts.append(f"search='{el['sidebar_search_query']}'")
    if el.get("dialog"):
        parts.append(f"dialog='{el['dialog'][:60]}'")
    if "select-chats-modal" in screens:
        parts.append("WARNING:select-chats-modal")
    parts.append(f"url={url}")
    return " | ".join(parts)


def _log_wa_ui_state(
    driver: webdriver.Chrome,
    step: str,
    progress: ProgressCallback = None,
    level: str = "info",
) -> str:
    """Log which WhatsApp Web panels/divs are visible at this step (console + send UI)."""
    summary = _format_wa_ui_state(_snapshot_wa_ui_state(driver))
    msg = f"UI @{step}: {summary}"
    if level == "warning":
        logger.warning(msg)
    elif level == "error":
        logger.error(msg)
    else:
        logger.info(msg)
    _report_progress(progress, msg)
    return summary


def _is_select_chats_modal_open(driver: webdriver.Chrome) -> bool:
    try:
        return bool(
            driver.execute_script(
                """
                var el = document.querySelector('[data-testid="chat-modal"]');
                if (!el) return false;
                var r = el.getBoundingClientRect();
                if (r.width < 8 || r.height < 8) return false;
                var s = window.getComputedStyle(el);
                return s.display !== 'none' && s.visibility !== 'hidden';
                """
            )
        )
    except Exception:
        return False


def _dismiss_select_chats_modal(driver: webdriver.Chrome) -> bool:
    """Close WhatsApp 'Select chats' picker — opened when files hit the wrong upload input."""
    if not _is_select_chats_modal_open(driver):
        return False
    for by, sel in (
        (By.CSS_SELECTOR, '[data-testid="chat-modal"] button[aria-label="Close"]'),
        (By.CSS_SELECTOR, '[data-testid="chat-modal"] [data-tab="2"][aria-label="Close"]'),
        (By.XPATH, "//*[@data-testid='chat-modal']//button[@aria-label='Close']"),
    ):
        try:
            els = driver.find_elements(by, sel)
            for el in els:
                try:
                    el.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", el)
                time.sleep(0.35)
                if not _is_select_chats_modal_open(driver):
                    logger.info("Dismissed Select chats modal.")
                    return True
        except Exception:
            continue
    try:
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        time.sleep(0.25)
    except Exception:
        pass
    return not _is_select_chats_modal_open(driver)


def _assign_files_landed_in_chat(driver: webdriver.Chrome) -> bool:
    """True when upload opened in-chat preview — not the Select chats forward picker."""
    if _is_select_chats_modal_open(driver):
        return False
    time.sleep(0.2)
    if _attachment_preview_is_open(driver):
        return True
    return _wait_for_attachment_preview(driver, timeout_s=3.0)


def _wait_direct_chat_settled(driver: webdriver.Chrome, timeout: float = 18.0) -> bool:
    """Wait until footer compose is stable and blocking modals (Starting chat / Select chats) are gone."""
    deadline = time.monotonic() + max(2.0, timeout)
    while time.monotonic() < deadline:
        _dismiss_select_chats_modal(driver)
        snap = _snapshot_wa_ui_state(driver)
        el = snap.get("elements") or {}
        screens = snap.get("screens") or []
        if _is_select_chats_modal_open(driver) or "select-chats-modal" in screens:
            time.sleep(0.35)
            continue
        dialog = str(el.get("dialog") or "")
        if "starting chat" in dialog.lower():
            time.sleep(0.4)
            continue
        if el.get("compose_visible"):
            return True
        time.sleep(0.3)
    return bool(_snapshot_wa_ui_state(driver).get("elements", {}).get("compose_visible"))


def _ensure_direct_chat_for_attach(
    driver: webdriver.Chrome,
    phone_digits: str | None,
    progress: ProgressCallback = None,
) -> bool:
    """Re-open send?phone= only when no chat is selected — never during attach/preview."""
    if _attach_flow_in_progress(driver):
        return True
    if _footer_ready_for_attach(driver):
        return True
    if _wait_direct_chat_settled(driver, timeout=2.0):
        return True
    if not _chat_is_no_selection(driver):
        return _footer_ready_for_attach(driver)
    _dismiss_select_chats_modal(driver)
    phone = (phone_digits or "").strip()
    if phone and len(phone) >= 8:
        logger.info("Re-opening direct chat via send link (no chat selected).")
        if _open_chat_via_phone_link_same_tab(driver, phone):
            if _wait_direct_chat_settled(driver, timeout=12.0):
                _log_wa_ui_state(driver, "chat-reopened-for-attach", progress)
                return True
    return _wait_direct_chat_settled(driver, timeout=4.0)


def _cdp_node_in_main_footer(driver: webdriver.Chrome, node_id: int) -> bool:
    try:
        resolved = driver.execute_cdp_cmd("DOM.resolveNode", {"nodeId": node_id})
        object_id = resolved.get("object", {}).get("objectId")
        if not object_id:
            return False
        out = driver.execute_cdp_cmd(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": """function() {
                    if (this.closest && this.closest('[data-testid="chat-modal"]')) return false;
                    return !!(this.closest && this.closest('#main footer'));
                }""",
                "returnByValue": True,
            },
        )
        return bool(out.get("result", {}).get("value"))
    except Exception:
        return False


def _list_footer_file_inputs(driver: webdriver.Chrome) -> list:
    """Hidden file inputs only inside the open conversation footer — not Select chats modal."""
    try:
        return driver.find_elements(By.CSS_SELECTOR, "#main footer input[type='file']")
    except Exception:
        return []


def _element_in_chat_footer(driver: webdriver.Chrome, el) -> bool:
    """True when el is inside the conversation footer — not the sidebar New chat (+)."""
    try:
        return bool(
            driver.execute_script(
                "return !!(arguments[0].closest && arguments[0].closest('#main footer, footer'));",
                el,
            )
        )
    except Exception:
        return False


def _click_attach_menu(
    driver: webdriver.Chrome,
    phone_digits: str | None = None,
    progress: ProgressCallback = None,
) -> None:
    """Footer-only (+) click. Never press ESC here — it closes the chat."""
    logger.info("Clicking Attach (+) menu control…")
    candidates = [
        (By.XPATH, "//footer//span[@data-testid='plus']"),
        (By.XPATH, "//footer//span[@data-icon='plus']"),
        (By.XPATH, "//footer//span[@data-icon='plus-rounded']"),
        (By.XPATH, "//footer//span[@data-icon='attach-menu-plus']"),
        (By.XPATH, "//footer//*[@aria-label='Attach']"),
        (By.XPATH, "//footer//button[@aria-label='Attach']"),
        (By.XPATH, "//footer//div[@title='Attach']"),
        (By.XPATH, "//footer//div[@role='button' and (@title='Attach' or @aria-label='Attach')]"),
        (By.CSS_SELECTOR, "footer span[data-icon='plus']"),
        (By.CSS_SELECTOR, "footer span[data-icon='plus-rounded']"),
        (By.CSS_SELECTOR, "footer span[data-icon='attach-menu-plus']"),
        (By.CSS_SELECTOR, "footer button[aria-label='Attach']"),
        (By.CSS_SELECTOR, "footer [aria-label='Attach']"),
    ]
    deadline = time.monotonic() + 8.0
    last: Exception | None = None
    while time.monotonic() < deadline:
        if phone_digits and _chat_is_no_selection(driver):
            logger.warning("Chat closed while locating attach (+) — recovering once.")
            _ensure_direct_chat_for_attach(driver, phone_digits, progress)
            time.sleep(0.35)
            continue
        for by, sel in candidates:
            try:
                els = driver.find_elements(by, sel)
                for el in els:
                    if not _element_in_chat_footer(driver, el):
                        continue
                    try:
                        el.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", el)
                    logger.info("Attach menu opened using selector %s %r.", by, sel)
                    return
            except Exception as e:
                last = e
                continue
        time.sleep(0.15)
    _log_wa_ui_state(driver, "attach-button-not-found", level="error")
    raise RuntimeError(f"Could not find Attach button: {last}")


_CLICK_ATTACH_SUBMENU_JS = r"""
function _waVis(el) {
  if (!el) return false;
  var r = el.getBoundingClientRect();
  if (r.width < 4 || r.height < 4) return false;
  var s = window.getComputedStyle(el);
  return s.display !== 'none' && s.visibility !== 'hidden';
}
function _clickNode(el) {
  if (!el || !_waVis(el)) return false;
  try { el.scrollIntoView({block: 'center'}); } catch (e) {}
  try { el.click(); return true; } catch (e) {}
  var btn = (el.closest && el.closest('[role="button"], button, li')) || el;
  try { btn.click(); return true; } catch (e2) {}
  return false;
}
var want = (arguments[0] || '').toLowerCase();
var selectors = [
  '[role="menuitem"]',
  '[data-animate-dropdown-item]',
  'div[data-animate-dropdown-item="true"]',
  'li[role="button"]',
  'div[role="button"]',
  'span[data-testid^="mi-"]',
  'span[data-testid^="attach-"]',
];
var seen = new Set();
for (var s = 0; s < selectors.length; s++) {
  var nodes = document.querySelectorAll(selectors[s]);
  for (var i = 0; i < nodes.length; i++) {
    var el = nodes[i];
    if (seen.has(el)) continue;
    seen.add(el);
    var t = ((el.textContent || '') + ' ' + (el.getAttribute('aria-label') || '')).trim().toLowerCase();
    if (!t || t.indexOf(want) < 0) continue;
    if (_clickNode(el)) return true;
  }
}
return false;
"""


def _click_attach_submenu_by_keyword(driver: webdriver.Chrome, keyword: str) -> bool:
    """Click a visible attach-menu row whose label contains keyword (e.g. document, photos)."""
    try:
        return bool(driver.execute_script(_CLICK_ATTACH_SUBMENU_JS, keyword))
    except Exception:
        return False


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
        if _click_attach_submenu_by_keyword(driver, "document"):
            return
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
    last: Exception | None = None
    deadline = time.monotonic() + 7.0
    while time.monotonic() < deadline:
        if _click_attach_submenu_by_keyword(driver, "photos"):
            return
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
                logger.info("Photos/videos menu clicked with selector %s %r.", by, sel)
                return
            except Exception as e:
                last = e
                continue
        time.sleep(0.15)
    raise RuntimeError(
        "Could not click 'Photos & videos' in the attach menu. "
        "Update WhatsApp Web or pick the option manually once to see the exact label."
    ) from last


def _find_document_file_input(driver: webdriver.Chrome):
    wait = WebDriverWait(driver, 12)

    def pick_in_footer(d: webdriver.Chrome):
        for el in _list_footer_file_inputs(d):
            acc = (el.get_attribute("accept") or "").strip().lower()
            if acc in ("*", "*/*"):
                return el
            if acc and "image" not in acc and "video" not in acc:
                return el
        for el in reversed(_list_footer_file_inputs(d)):
            acc = (el.get_attribute("accept") or "").strip().lower()
            if not acc or acc == "*":
                return el
        return None

    try:
        found = wait.until(pick_in_footer)
        if found is not None:
            return found
    except TimeoutException:
        pass
    return None


def _find_photos_and_videos_file_input(driver: webdriver.Chrome):
    wait = WebDriverWait(driver, 12)

    def pick_gallery_input(d: webdriver.Chrome):
        for e in _list_footer_file_inputs(d):
            if _is_gallery_media_accept(e.get_attribute("accept") or ""):
                return e
        for e in _list_footer_file_inputs(d):
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
        if not _cdp_node_in_main_footer(driver, nid):
            continue
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
    phone_digits: str | None = None,
    progress: ProgressCallback = None,
) -> bool:
    """
    CDP upload scoped to #main footer — mirrors reference two-phase poll.
    Phase A: poll footer file inputs right after attach (+) menu opens.
    Phase B: click Document/Photos once, poll again. Never blind ESC (closes WA chat).
    """
    mode = "gallery/media" if for_gallery else "document"
    logger.info("CDP attach: footer-scoped upload (%s).", mode)

    def poll_assign(deadline_secs: float, phase: str) -> bool:
        deadline = time.monotonic() + deadline_secs
        while time.monotonic() < deadline:
            if _chat_is_no_selection(driver) and phone_digits:
                logger.warning("CDP attach: chat closed during %s — recovering.", phase)
                if not _ensure_direct_chat_for_attach(driver, phone_digits, progress):
                    return False
                return False
            if chooser_assigned and chooser_assigned():
                _dismiss_native_file_dialog_if_open(attempts=1, delay=0.08)
                if _assign_files_landed_in_chat(driver):
                    logger.info("CDP attach: chooser interceptor assigned files (%s).", phase)
                    return True
            nid = _cdp_pick_matching_file_node_id(driver, for_gallery)
            if nid is not None:
                try:
                    _cdp_set_files_on_node(driver, nid, paths)
                    _dismiss_native_file_dialog_if_open(attempts=1, delay=0.08)
                    if _assign_files_landed_in_chat(driver):
                        logger.info("CDP DOM.setFileInputFiles succeeded (%s, nodeId=%s).", phase, nid)
                        return True
                    logger.warning("CDP set files on footer node %s opened wrong UI.", nid)
                    _dismiss_select_chats_modal(driver)
                except Exception as ex:
                    logger.warning("CDP set files failed on node %s: %s", nid, ex)
            time.sleep(0.25)
        return False

    logger.info("CDP attach: phase A poll (%s).", mode)
    if poll_assign(7.0, "phase A"):
        return True

    logger.info("CDP attach: phase B — clicking %s.", "Photos & videos" if for_gallery else "Document")
    if for_gallery:
        try:
            _click_photos_menu_item(driver)
        except RuntimeError:
            logger.info("CDP attach: Photos menu missing; trying Document submenu.")
            _click_document_menu_item(driver)
    else:
        _click_document_menu_item(driver)
    time.sleep(0.35)
    _dismiss_native_file_dialog_if_open(attempts=2, delay=0.1)

    if poll_assign(6.0, "phase B"):
        return True

    if _attachment_preview_is_open(driver):
        _dismiss_native_file_dialog_if_open(attempts=1, delay=0.08)
        return True

    if not _footer_ready_for_attach(driver) and phone_digits:
        _ensure_direct_chat_for_attach(driver, phone_digits, progress)
    if not _attach_menu_is_open(driver):
        _click_attach_menu(driver, phone_digits=phone_digits, progress=progress)
        time.sleep(0.25)
    if for_gallery:
        try:
            _click_photos_menu_item(driver)
        except RuntimeError:
            _click_document_menu_item(driver)
    else:
        _click_document_menu_item(driver)
    time.sleep(0.35)
    _dismiss_native_file_dialog_if_open(attempts=2, delay=0.1)
    return poll_assign(5.0, "phase B retry")


def _try_direct_attachment_upload(
    driver: webdriver.Chrome,
    paths: list[str],
    chooser_assigned=None,
) -> bool:
    """
    Newer WhatsApp Web: assign files to hidden input[accept='*'] in #main/footer
    without opening the attach menu — opens the media preview overlay directly.
    """
    logger.info("CDP attach: trying direct hidden file input (no attach menu).")
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        if chooser_assigned and chooser_assigned():
            return True
        for for_gallery in (False, True):
            nid = _cdp_pick_matching_file_node_id(driver, for_gallery)
            if nid is None:
                continue
            try:
                _cdp_set_files_on_node(driver, nid, paths)
                logger.info("Direct CDP upload succeeded (gallery=%s, nodeId=%s).", for_gallery, nid)
                inp = _find_document_file_input(driver) or _find_photos_and_videos_file_input(driver)
                _nudge_file_input_for_react(driver, inp)
                time.sleep(0.6)
                if _wait_for_attachment_preview(driver, timeout_s=10.0):
                    return True
                if _attachment_preview_is_open(driver):
                    return True
            except Exception as ex:
                logger.debug("Direct CDP upload failed (gallery=%s): %s", for_gallery, ex)
        time.sleep(0.25)
    return False


def _fallback_send_keys_file_input(driver: webdriver.Chrome, paths: list[str], for_gallery: bool) -> None:
    logger.info("Fallback: locating footer file input and send_keys (%d path(s)).", len(paths))
    if for_gallery:
        try:
            _click_photos_menu_item(driver)
        except RuntimeError:
            _click_document_menu_item(driver)
    else:
        _click_document_menu_item(driver)
    time.sleep(0.4)
    if for_gallery:
        file_input = _find_photos_and_videos_file_input(driver) or _find_document_file_input(driver)
    else:
        file_input = _find_document_file_input(driver) or _find_photos_and_videos_file_input(driver)
    if not file_input:
        raise RuntimeError(
            "Could not find footer file upload control. "
            "If a system Open dialog is visible, close it and try again."
        )
    joined = "\n".join(str(os.path.abspath(os.path.normpath(p))) for p in paths)
    file_input.send_keys(joined)
    if not _assign_files_landed_in_chat(driver):
        _dismiss_select_chats_modal(driver)
        raise RuntimeError("File upload opened Select chats picker instead of in-chat attachment preview")
    logger.info("Fallback send_keys completed.")


def _all_paths_are_images_or_video(paths: list[str]) -> bool:
    exts = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".mp4", ".mov", ".3gp", ".mkv", ".webm"}
    for p in paths:
        if os.path.splitext(p)[1].lower() not in exts:
            return False
    return bool(paths)


def _attach_via_gallery_menu(paths: list[str]) -> bool:
    """Images and videos use Photos & videos; PDFs/docs use Document — same as standalone sender."""
    return _all_paths_are_images_or_video(paths)


def _send_attachments_in_current_chat(
    driver: webdriver.Chrome,
    paths: list[str],
    message_text: str = "",
    progress: ProgressCallback = None,
    phone_digits: str | None = None,
) -> str | None:
    """
    Attach queued files in the already-open chat and click Send.
    Mirrors WHATSAPP test/whatsapp_attachment_sender.py line-for-line.
    Returns None on success, or an error string.
    """
    resolved = _resolve_attachment_paths(paths)
    if not resolved:
        raw = [p for p in (paths or []) if (p or "").strip()]
        if raw:
            names = [os.path.basename(p) for p in raw[:3]]
            return f"No valid attachment files on disk: {names}"[:400]
        return "No valid attachment files"

    ws_url = _chrome_page_websocket_debugger_url(driver) if ws_cdp else None
    if ws_url:
        logger.info("CDP debugger WebSocket OK (native file dialog interception enabled).")
    elif ws_cdp:
        logger.warning(
            "Could not read debugger WebSocket URL; OS file dialog may still appear."
        )
    else:
        logger.warning(
            "websocket-client not installed; skipping file-chooser interception. "
            "Install websocket-client or rebuild the .exe with updated hiddenimports."
        )

    attach_ctx = _FileChooserInterceptor(ws_url, list(resolved)) if ws_url else nullcontext()
    if not _footer_ready_for_attach(driver):
        if phone_digits and not _ensure_direct_chat_for_attach(driver, phone_digits, progress):
            return "Chat compose not ready for attachments (footer missing)"
    _ensure_chat_compose_ready(driver)
    _log_wa_ui_state(driver, "attach-start", progress)
    try:
        with attach_ctx:
            _dismiss_select_chats_modal(driver)
            logger.info("Opening attach menu…")
            _click_attach_menu(driver, phone_digits=phone_digits, progress=progress)
            time.sleep(0.25)
            _log_wa_ui_state(driver, "attach-menu-clicked", progress)

            for_gallery = _all_paths_are_images_or_video(resolved)
            logger.info("Attachment mode: %s.", "photos/videos" if for_gallery else "document/other")
            chooser_cb = (
                (lambda: attach_ctx.did_assign_files)
                if isinstance(attach_ctx, _FileChooserInterceptor)
                else None
            )
            cdp_attached = _cdp_try_assign_files(
                driver,
                resolved,
                for_gallery,
                chooser_assigned=chooser_cb,
                phone_digits=phone_digits,
                progress=progress,
            )
            intercepted_attach = isinstance(attach_ctx, _FileChooserInterceptor) and attach_ctx.did_assign_files
            if intercepted_attach and not cdp_attached and _assign_files_landed_in_chat(driver):
                logger.info("Chooser interceptor assigned files in chat.")
                cdp_attached = True
                _dismiss_native_file_dialog_if_open(attempts=2, delay=0.1)
            if not cdp_attached and not intercepted_attach:
                logger.info("Attaching files (fallback send_keys)…")
                try:
                    _fallback_send_keys_file_input(driver, resolved, for_gallery)
                    cdp_attached = True
                except Exception as e:
                    return f"Could not upload files: {e!r}"[:400]
                _dismiss_native_file_dialog_if_open(attempts=2, delay=0.1)
    except Exception as e:
        _log_wa_ui_state(driver, "attach-flow-failed", progress, level="error")
        return f"Attach menu flow failed: {e!r}"[:400]

    _log_wa_ui_state(driver, "files-assigned", progress)
    _dismiss_discard_selection_modal(driver, prefer="cancel")
    _dismiss_media_editor_modals(driver)
    if _is_select_chats_modal_open(driver):
        _dismiss_select_chats_modal(driver)
        return "Upload opened Select chats picker (wrong file input) — not the open chat footer"
    if not _assign_files_landed_in_chat(driver):
        return "Files were set but attachment preview did not open in the current chat"

    if _all_paths_are_images_or_video(resolved):
        logger.info("Checking media editor sticker mode…")
        _switch_editor_from_sticker_to_photo(driver)
        _dismiss_media_editor_modals(driver)

    if message_text:
        logger.info("Adding caption text…")
        _dismiss_discard_selection_modal(driver, prefer="cancel")
        if not _set_attachment_caption(driver, message_text):
            time.sleep(0.2)
            _set_attachment_caption(driver, message_text)
        time.sleep(0.12)

    logger.info("Files queued — sending…")
    _dismiss_discard_selection_modal(driver, prefer="cancel")
    _dismiss_media_editor_modals(driver)
    _log_wa_ui_state(driver, "before-send-click", progress)
    sent = _click_send_after_upload(driver)
    _log_wa_ui_state(driver, "after-send-click", progress, level="warning" if not sent else "info")
    if sent:
        _wait_attachment_preview_closed(driver, timeout=12.0)
        _dismiss_native_file_dialog_if_open(attempts=2, delay=0.1)
        if _attachment_preview_is_open(driver):
            return "Send clicked but attachment preview is still open"
        return None
    return "Could not auto-click Send (preview may still be open — click Send manually once)"


def _switch_editor_from_sticker_to_photo(driver: webdriver.Chrome) -> None:
    """Switch Sticker → Photo only. Never click HD (triggers blocking modal on non-HD images)."""
    short = WebDriverWait(driver, 3)
    in_sticker = False
    for by, sel in (
        (By.XPATH, "//div[@role='tab' and contains(., 'Sticker')]"),
        (By.CSS_SELECTOR, '[data-testid="media-editor-send-sticker"]'),
    ):
        try:
            el = driver.find_element(by, sel)
            if el.is_displayed():
                in_sticker = True
                break
        except Exception:
            continue
    if not in_sticker:
        return
    for by, sel in (
        (By.XPATH, "//div[@role='tab'][contains(., 'Photo') and not(contains(., 'Sticker'))]"),
        (By.CSS_SELECTOR, '[data-testid="media-editor-send-photo"]'),
    ):
        try:
            short.until(EC.element_to_be_clickable((by, sel))).click()
            time.sleep(0.15)
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


_FIND_ATTACHMENT_CAPTION_BOX_JS = r"""
function _waVis(el) {
  if (!el) return false;
  var r = el.getBoundingClientRect();
  if (r.width < 4 || r.height < 4) return false;
  var s = window.getComputedStyle(el);
  if (s.display === 'none' || s.visibility === 'hidden') return false;
  if (parseFloat(s.opacity || '1') < 0.05) return false;
  return true;
}
var roots = [
  document.querySelector('[data-testid="media-editor"]'),
  document.querySelector('[data-testid="media-viewer"]'),
  document.querySelector('[data-animate-media-popup="true"]'),
  document.querySelector('[data-testid="attach-media"]'),
  document.querySelector('div[aria-label="Media editor"]'),
];
var cap = document.querySelector('[data-testid="media-caption-input-container"][contenteditable="true"]');
if (_waVis(cap)) return cap;
for (var i = 0; i < roots.length; i++) {
  var root = roots[i];
  if (!_waVis(root)) continue;
  var boxes = root.querySelectorAll(
    'div[contenteditable="true"][data-tab], div[contenteditable="true"][role="textbox"], div[contenteditable="true"]'
  );
  for (var j = boxes.length - 1; j >= 0; j--) {
    if (_waVis(boxes[j])) return boxes[j];
  }
}
var footers = document.querySelectorAll('footer');
for (var f = footers.length - 1; f >= 0; f--) {
  var footer = footers[f];
  if (!_waVis(footer)) continue;
  var inPopup = footer.closest(
    '[data-animate-media-popup], [data-testid="media-editor"], [data-testid="media-viewer"]'
  );
  if (!inPopup || !_waVis(inPopup)) continue;
  var fb = footer.querySelector(
    'div[contenteditable="true"][data-tab], div[contenteditable="true"][role="textbox"]'
  );
  if (_waVis(fb)) return fb;
}
return null;
"""


def _find_attachment_caption_box(driver: webdriver.Chrome):
    """Caption field inside the media preview overlay — not main chat compose behind it."""
    try:
        box = driver.execute_script(_FIND_ATTACHMENT_CAPTION_BOX_JS)
        if box is not None:
            return box
    except Exception:
        pass
    for by, sel in (
        (By.CSS_SELECTOR, '[data-testid="media-caption-input-container"][contenteditable="true"]'),
        (By.CSS_SELECTOR, '[data-testid="media-caption-input-container"] div[contenteditable="true"]'),
        (
            By.XPATH,
            "//*[@data-testid='media-editor' or @data-testid='media-viewer' or @data-animate-media-popup='true']"
            "//div[@contenteditable='true' and (@role='textbox' or @data-tab)]",
        ),
        (
            By.XPATH,
            "//*[@contenteditable='true' and (@aria-label='Type a message' or @title='Type a message')]",
        ),
        (By.XPATH, "//*[contains(@aria-label,'message') and @contenteditable='true']"),
    ):
        try:
            return WebDriverWait(driver, 4).until(EC.element_to_be_clickable((by, sel)))
        except Exception:
            continue
    return None


def _set_attachment_caption(driver: webdriver.Chrome, text: str) -> bool:
    if not text:
        return False
    _dismiss_discard_selection_modal(driver, prefer="cancel")
    box = _find_attachment_caption_box(driver)
    if box is None:
        for by, sel in (
            (By.CSS_SELECTOR, "div[contenteditable='true'][data-tab]"),
            (By.CSS_SELECTOR, '[data-testid="media-caption-input-container"] div[contenteditable="true"]'),
            (By.XPATH, "//*[@contenteditable='true' and (@aria-label='Type a message' or @title='Type a message')]"),
            (By.XPATH, "//*[contains(@aria-label,'message') and @contenteditable='true']"),
        ):
            try:
                box = WebDriverWait(driver, 4).until(EC.element_to_be_clickable((by, sel)))
                break
            except Exception:
                continue
    if box is None:
        logger.warning("Could not locate attachment caption box (Type a message).")
        return False
    try:
        box.click()
        ActionChains(driver).key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL).send_keys(
            Keys.BACKSPACE
        ).perform()
        box.send_keys(text)
        logger.info("Attachment caption entered in Type a message box.")
        return True
    except Exception:
        try:
            _insert_text_into_contenteditable(driver, box, text)
            return True
        except Exception:
            return False


_ATTACHMENT_PREVIEW_OPEN_JS = r"""
function _waVis(el) {
  if (!el) return false;
  var r = el.getBoundingClientRect();
  if (r.width < 6 || r.height < 6) return false;
  var s = window.getComputedStyle(el);
  if (s.display === 'none' || s.visibility === 'hidden') return false;
  if (parseFloat(s.opacity || '1') < 0.05) return false;
  return true;
}
var selectors = [
  '[data-testid="media-editor"]',
  '[data-testid="media-viewer"]',
  '[data-animate-media-popup="true"]',
  '[data-testid="attach-media"]',
  'div[aria-label="Media editor"]',
  '[data-testid="preview-generic"]',
  '[data-testid="media-caption-input-container"]',
];
for (var i = 0; i < selectors.length; i++) {
  var el = document.querySelector(selectors[i]);
  if (_waVis(el)) return true;
}
var hints = document.querySelectorAll('span, div');
for (var j = 0; j < hints.length; j++) {
  var t = (hints[j].textContent || '').trim();
  if (t === 'No preview available' || (t.indexOf('MB') >= 0 && t.indexOf('PDF') >= 0)) {
    if (!_waVis(hints[j])) continue;
    var panel = hints[j].closest('[data-animate-media-popup], [role="dialog"], [data-testid="media-editor"]');
    if (panel && _waVis(panel)) return true;
    if (hints[j].getBoundingClientRect().height > 24) return true;
  }
}
var footer = document.querySelector('footer');
if (footer) {
  var media = footer.querySelectorAll('img, video, canvas');
  for (var k = 0; k < media.length; k++) {
    var mr = media[k].getBoundingClientRect();
    if (mr.width > 48 && mr.height > 48) return true;
  }
}
var sends = document.querySelectorAll(
    '[data-testid="send"], [role="button"][aria-label*="Send"], span[data-icon="wds-ic-send-filled"]'
  );
for (var s = 0; s < sends.length; s++) {
  if (!_waVis(sends[s])) continue;
  var inPopup = sends[s].closest(
    '[data-animate-media-popup], [data-testid="media-editor"], [data-testid="media-viewer"], [data-testid="media-caption-input-container"]'
  );
  if (inPopup && _waVis(inPopup)) return true;
  var lab = (sends[s].getAttribute && sends[s].getAttribute('aria-label')) || '';
  if (lab.indexOf('selected') >= 0) return true;
}
return false;
"""


def _attachment_preview_is_open(driver: webdriver.Chrome) -> bool:
    """
    True only when WhatsApp's media/document preview overlay is open — not the normal
    footer compose Send button (that caused false-positive 'upload succeeded' bugs).
    """
    try:
        return bool(driver.execute_script(_ATTACHMENT_PREVIEW_OPEN_JS))
    except Exception:
        pass
    return False


def _attachment_preview_send_visible(driver: webdriver.Chrome) -> bool:
    """Deprecated alias — use _attachment_preview_is_open."""
    return _attachment_preview_is_open(driver)


def _wait_for_attachment_preview(driver: webdriver.Chrome, timeout_s: float = 12.0) -> bool:
    deadline = time.monotonic() + max(0.5, timeout_s)
    while time.monotonic() < deadline:
        if _attachment_preview_is_open(driver):
            return True
        time.sleep(0.25)
    return False


def _upload_attachments_hidden_input_fallback(driver: webdriver.Chrome, resolved: list[str]) -> str | None:
    def _attempt_assign() -> str | None:
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
            if _wait_for_attachment_preview(driver, timeout_s=12.0):
                return None
            return "Upload attempted but attachment preview did not appear"

        logger.info("send_keys hidden-input upload failed (%s); trying CDP.", send_err)
        if _cdp_set_files_on_first_matching_input(driver, resolved):
            inp2 = _pick_file_input(_list_whatsapp_file_inputs(driver), resolved)
            _nudge_file_input_for_react(driver, inp2)
            if _wait_for_attachment_preview(driver, timeout_s=12.0):
                return None
            return "CDP hidden-input upload set files, but attachment preview did not appear"
        return send_err

    err = _attempt_assign()
    if err is None:
        return None

    try:
        _click_attach_menu(driver)
        time.sleep(0.2)
        if _attach_via_gallery_menu(resolved):
            try:
                _click_photos_menu_item(driver)
            except RuntimeError:
                _click_document_menu_item(driver)
        else:
            _click_document_menu_item(driver)
        time.sleep(0.35)
    except Exception as menu_err:
        logger.debug("Could not open attach submenu for hidden fallback: %s", menu_err)
        return err

    return _attempt_assign()


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
        kwargs: dict = {"env": env, "capture_output": True, "timeout": 45}
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        r = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            **kwargs,
        )
        return r.returncode == 0
    except Exception:
        return False


def _find_chat_compose_box(driver: webdriver.Chrome):
    for by, sel in (
        (By.XPATH, "//footer//div[@contenteditable='true' and @role='textbox']"),
        (By.XPATH, "//div[@contenteditable='true' and @data-tab='10']"),
        (By.XPATH, "//div[@contenteditable='true' and @data-tab='6']"),
    ):
        try:
            return driver.find_element(by, sel)
        except Exception:
            continue
    return None


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
    _ensure_chat_compose_ready(driver)
    for ap in resolved:
        if not _powershell_set_clipboard_image(ap):
            return f"Could not copy image to clipboard: {os.path.basename(ap)}"
        try:
            compose = None
            for _ in range(12):
                compose = _find_chat_compose_box(driver)
                if compose is not None:
                    break
                time.sleep(0.25)
            if compose is None:
                return "Compose box not found for clipboard paste"
            compose.click()
            time.sleep(0.2)
            ActionChains(driver).key_down(Keys.CONTROL).send_keys("v").key_up(Keys.CONTROL).perform()
            time.sleep(1.0)
            if _wait_for_attachment_preview(driver, timeout_s=10.0):
                return None
        except Exception as e:
            return f"Paste into compose failed: {e!r}"[:300]
    if _attachment_preview_is_open(driver):
        return None
    return "Clipboard paste did not open attachment preview"


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
    xpaths = (
        "//div[@role='button' and contains(@aria-label,'Send') and not(@aria-disabled='true')]"
        "[.//span[@data-testid='wds-ic-send-filled' or @data-icon='wds-ic-send-filled' or @data-icon='send']]",
        "//*[@data-testid='media-editor' or @data-testid='media-viewer' or @data-animate-media-popup='true']"
        "//div[@role='button' and contains(@aria-label,'Send') and not(@aria-disabled='true')]",
        "//div[@role='button' and contains(@aria-label,'Send') and contains(@aria-label,'selected') "
        "and not(@aria-disabled='true')]",
    )
    end = time.time() + max(0.5, max_wait)
    while time.time() < end:
        btn = None
        for xp in xpaths:
            els = driver.find_elements(By.XPATH, xp)
            if els:
                btn = els[-1]
                break
        if btn is not None:
            time.sleep(0.6)
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
        time.sleep(0.28)
    return False


_CLICK_ATTACHMENT_PREVIEW_SEND_JS = r"""
function _waVis(el) {
  if (!el) return false;
  var r = el.getBoundingClientRect();
  if (r.width < 6 || r.height < 6) return false;
  var s = window.getComputedStyle(el);
  if (s.display === 'none' || s.visibility === 'hidden') return false;
  if (parseFloat(s.opacity || '1') < 0.05) return false;
  return true;
}
function tryClick(el) {
  if (!el || !_waVis(el)) return false;
  if (el.getAttribute && el.getAttribute('aria-disabled') === 'true') return false;
  try { el.scrollIntoView({block: 'center', inline: 'center'}); } catch (e) {}
  try { el.click(); return true; } catch (e) {}
  return false;
}
var roots = [
  document.querySelector('[data-testid="media-editor"]'),
  document.querySelector('[data-testid="media-viewer"]'),
  document.querySelector('[data-animate-media-popup="true"]'),
  document.querySelector('[data-testid="attach-media"]'),
];
for (var i = 0; i < roots.length; i++) {
  var root = roots[i];
  if (!_waVis(root)) continue;
  var sends = root.querySelectorAll(
    '[data-testid="send"], span[data-testid="wds-ic-send-filled"], span[data-icon="wds-ic-send-filled"], ' +
    'span[data-icon="send"], [role="button"][aria-label*="Send"]'
  );
  for (var j = sends.length - 1; j >= 0; j--) {
    var hit = sends[j];
    var btn = (hit.closest && hit.closest('[role="button"]')) || hit;
    if (tryClick(btn)) return true;
  }
}
var docSends = document.querySelectorAll('[role="button"][aria-label*="Send"]');
for (var d = docSends.length - 1; d >= 0; d--) {
  var lab = docSends[d].getAttribute('aria-label') || '';
  if (lab.indexOf('selected') >= 0 && tryClick(docSends[d])) return true;
}
return false;
"""


def _click_send_after_upload(driver: webdriver.Chrome) -> bool:
    """
    WhatsApp rebuilds the footer/preview DOM after files are attached, so any
    element found before upload can go stale. Re-locate the send control on
    each attempt and use JS click as a fallback. Matches reference sender timing.
    """
    logger.info("Locating Send control and clicking…")
    _dismiss_discard_selection_modal(driver, prefer="cancel")
    _dismiss_media_editor_modals(driver)
    time.sleep(0.35)
    pairs = [
        (By.CSS_SELECTOR, '[data-testid="send"]'),
        (By.CSS_SELECTOR, 'span[data-icon="wds-ic-send-filled"]'),
        (By.CSS_SELECTOR, 'span[data-icon="send"]'),
        (By.CSS_SELECTOR, 'button[data-testid="compose-btn-send"]'),
        (
            By.XPATH,
            "//*[@role='button' and contains(@aria-label,'Send') and contains(@aria-label,'selected')]",
        ),
        (By.XPATH, "//div[@role='button' and (@aria-label='Send' or @aria-label='Send message')]"),
    ]
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        _dismiss_discard_selection_modal(driver, prefer="cancel")
        _dismiss_media_editor_modals(driver)
        try:
            if driver.execute_script(_CLICK_ATTACHMENT_PREVIEW_SEND_JS):
                logger.info("Send clicked via preview panel JS.")
                return True
        except Exception:
            pass
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
    if _wait_and_click_preview_send(driver, max_wait=22.0):
        return True
    return _click_send_after_upload(driver)


def _attachment_send_only_error(attach_err: str | None) -> bool:
    """True when files are likely queued but only the Send click failed."""
    if not attach_err:
        return False
    low = attach_err.lower()
    return "could not auto-click send" in low or "preview may still be open" in low


def _normalize_phone(phone: str) -> str:
    return "".join(c for c in phone if c.isdigit())


def normalize_phone(phone: str) -> str:
    """Digits-only phone for wa.me / send?phone= links."""
    return _normalize_phone(phone)


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


def _wait_for_chat_compose_ready(driver: webdriver.Chrome, timeout: int) -> bool:
    """Wait until WhatsApp chat compose is ready (matches reference attachment sender)."""
    wait = WebDriverWait(driver, timeout)
    try:
        wait.until(
            EC.any_of(
                EC.presence_of_element_located((By.CSS_SELECTOR, '[data-testid="conversation-compose-box-input"]')),
                EC.presence_of_element_located((By.CSS_SELECTOR, 'div[contenteditable="true"][data-tab]')),
                EC.presence_of_element_located((By.CSS_SELECTOR, 'footer div[contenteditable="true"]')),
                EC.presence_of_element_located(
                    (By.XPATH, "//footer//div[@contenteditable='true' and @role='textbox']")
                ),
            )
        )
        return True
    except TimeoutException:
        return False


def _open_chat_via_phone_link_same_tab(driver: webdriver.Chrome, phone_digits: str) -> bool:
    """
    Open the chat in the same tab via WhatsApp Web's send URL (same session, no new tabs).
    If WhatsApp shows a modal like \"The number ... isn't on WhatsApp.\", click OK and
    report failure so the caller can mark the row as ERROR and continue.
    """
    send_url = f"https://web.whatsapp.com/send?phone={phone_digits}"
    try:
        driver.get(send_url)
    except Exception:
        return False
    if _try_dismiss_not_on_whatsapp_modal(driver):
        return False
    if _wait_for_chat_compose_ready(driver, CHAT_LOAD_TIMEOUT):
        return _wait_direct_chat_settled(driver, timeout=18.0)
    if _try_dismiss_not_on_whatsapp_modal(driver):
        return False
    return False


def _build_chrome_options(profile_dir: str) -> Options:
    chrome_options = Options()
    chrome_options.add_argument("--user-data-dir=" + profile_dir)
    chrome_options.add_argument("--profile-directory=Default")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--remote-allow-origins=*")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    return chrome_options


def _prepare_chrome_window(driver: webdriver.Chrome) -> None:
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});",
            },
        )
    except Exception:
        pass
    try:
        driver.maximize_window()
    except Exception:
        pass


def _format_chrome_startup_error(exc: Exception, profile_dir: str) -> str:
    raw = str(exc)
    hints: list[str] = []
    low = raw.lower()
    if "session not created" in low or "chrome instance exited" in low:
        hints.append("Close every Chrome window opened by this app (or end chrome.exe / chromedriver.exe in Task Manager).")
        hints.append(
            "Use only one WhatsAppDesktop.exe — do not run a copy from desktop_app\\dist and another from a duplicate exe."
        )
        if "onedrive" in profile_dir.lower():
            hints.append(
                "Your Chrome profile folder is under OneDrive, which often locks files and makes Chrome exit immediately. "
                "Move the app folder (or at least dist\\chrome_profiles) outside OneDrive, e.g. C:\\WhatsAppDesktop\\."
            )
        hints.append(f"If it still fails, delete the profile folder and log in again: {profile_dir}")
    msg = raw[:400]
    if hints:
        msg += "\n\nTry:\n• " + "\n• ".join(hints)
    return msg


def _launch_chrome_with_backend(chrome_options: Options, backend: str) -> webdriver.Chrome:
    if backend == "webdriver_manager":
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager

        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=chrome_options)
    return webdriver.Chrome(options=chrome_options)


def create_driver_for_profile(client_phno: str) -> webdriver.Chrome:
    """Create Chrome driver for this client's profile. Caller must quit() when done."""
    profile_dir = get_profile_dir(client_phno)
    os.makedirs(profile_dir, exist_ok=True)
    chrome_options = _build_chrome_options(profile_dir)
    # In the frozen .exe, Selenium Manager often fails; webdriver-manager is more reliable.
    backends = (
        ("webdriver_manager", "selenium_manager")
        if getattr(sys, "frozen", False)
        else ("selenium_manager", "webdriver_manager")
    )
    last_err: Exception | None = None
    for backend in backends:
        try:
            driver = _launch_chrome_with_backend(chrome_options, backend)
            _prepare_chrome_window(driver)
            return driver
        except Exception as e:
            last_err = e
            logger.warning("Chrome startup via %s failed: %s", backend, e)
    raise RuntimeError(_format_chrome_startup_error(last_err or RuntimeError("unknown"), profile_dir))


_SEARCH_LOCATORS = [
    (By.XPATH, "//input[@role='textbox' and @type='text' and @data-tab='3']"),
    (By.XPATH, "//div[@contenteditable='true' and @data-tab='3']"),
    (By.XPATH, "//div[@contenteditable='true' and @aria-label='Search']"),
    (By.XPATH, "//div[@contenteditable='true' and contains(@aria-label, 'Search')]"),
    (By.CSS_SELECTOR, 'div[contenteditable="true"][data-tab="3"]'),
    (By.CSS_SELECTOR, 'div[contenteditable="true"][title="Search input textbox"]'),
    (By.XPATH, "//div[@id='side']//div[@contenteditable='true' and @role='textbox']"),
    (By.XPATH, "//div[@id='side']//div[@contenteditable='true']"),
]


def is_driver_alive(driver: webdriver.Chrome | None) -> bool:
    """False when the user closed Chrome or the session is invalid."""
    if driver is None:
        return False
    try:
        _ = driver.window_handles
        return True
    except Exception:
        return False


def _ensure_whatsapp_main_list(driver: webdriver.Chrome) -> bool:
    """
    Close overlays (group info, search members, contact info) and ensure the
    left-panel chat search is reachable.
    """
    try:
        for _ in range(5):
            if _group_members_search_is_open(driver):
                _close_group_members_search_modal(driver)
                time.sleep(0.25)
            if _member_context_menu_is_open(driver):
                _dismiss_member_context_menu(driver)
                time.sleep(0.2)
            if _member_contact_info_is_open(driver):
                _back_from_member_contact_info_to_group_info(driver)
                time.sleep(0.3)
            _try_click_back_or_escape(driver)
            time.sleep(0.25)
        short = WebDriverWait(driver, 4)
        if _find_side_search_box(driver, short):
            return True
        driver.get("https://web.whatsapp.com/")
        WebDriverWait(driver, CHAT_LOAD_TIMEOUT).until(
            EC.presence_of_element_located((By.ID, "side"))
        )
        time.sleep(2.5)
        return _find_side_search_box(driver, WebDriverWait(driver, 12)) is not None
    except Exception:
        return False


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
    # Section headers in the New chat list are often a single letter (A, B, C, …).
    if len(t) == 1 and t.isalpha():
        return False
    low = t.lower()
    for p in _SKIP_TITLE_PREFIXES:
        if low == p or low.startswith(p + " "):
            return False
    return True


_WA_NEW_CHAT_CONTACTS_SNAPSHOT_JS = r"""
const drawer = document.querySelector('[data-testid="new-chat-drawer"]');
if (!drawer) return [];
const out = [];
const seen = new Set();
const items = drawer.querySelectorAll('[data-testid^="list-item-"], div[role="listitem"]');
for (const item of items) {
  const nameEl =
    item.querySelector("[data-testid='cell-frame-title'] span[dir='auto'][title]") ||
    item.querySelector("[data-testid='cell-frame-title'] span[title]") ||
    item.querySelector("[data-testid='cell-frame-title'] [title]");
  if (!nameEl) continue;
  const raw = (nameEl.getAttribute('title') || nameEl.textContent || '').trim();
  if (!raw || seen.has(raw)) continue;
  seen.add(raw);
  out.push(raw);
}
return out;
"""


_WA_NEW_CHAT_CONTACTS_SCROLL_STEP_JS = r"""
const drawer = document.querySelector('[data-testid="new-chat-drawer"]');
if (!drawer) return false;

function findScroller(root) {
  let best = null;
  let bestScore = -1;
  for (const el of root.querySelectorAll('div, section')) {
    const tid = (el.getAttribute('data-testid') || '').toLowerCase();
    // Never scroll the A–Z letter rail; it is not the contact list.
    if (tid === 'contact-list-key' || tid.includes('letter') || tid.includes('alphabet')) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 140 || r.height < 100) continue;
    const sh = el.scrollHeight || 0;
    const ch = el.clientHeight || 0;
    if (sh <= ch + 50) continue;
    const itemCount = el.querySelectorAll('[data-testid^="list-item-"], div[role="listitem"]').length;
    let score = (sh - ch) * 4 + itemCount * 6;
    if (el.getAttribute('role') === 'list') score += 800;
    if (el.tabIndex === 0) score += 200;
    if (score > bestScore) {
      bestScore = score;
      best = el;
    }
  }
  return best;
}

let moved = false;
const items = drawer.querySelectorAll('[data-testid^="list-item-"], div[role="listitem"]');
if (items.length) {
  const last = items[items.length - 1];
  const beforeTop = last.getBoundingClientRect().top;
  try { last.scrollIntoView({block: 'end', inline: 'nearest'}); } catch (e) {}
  if (Math.abs(last.getBoundingClientRect().top - beforeTop) > 2) moved = true;
}

const scroller = findScroller(drawer);
if (scroller) {
  const before = scroller.scrollTop || 0;
  const step = Math.max(320, Math.floor((scroller.clientHeight || 420) * 0.92));
  scroller.scrollTop = before + step;
  try { scroller.dispatchEvent(new Event('scroll', {bubbles: true})); } catch (e2) {}
  if ((scroller.scrollTop || 0) > before + 2) moved = true;
  if (!moved) {
    const prevH = scroller.scrollHeight || 0;
    scroller.scrollTop = prevH;
    try { scroller.dispatchEvent(new Event('scroll', {bubbles: true})); } catch (e3) {}
    if ((scroller.scrollTop || 0) > before + 2 || (scroller.scrollHeight || 0) > prevH + 20) {
      moved = true;
    }
  }
  if (!moved) {
    try {
      scroller.focus({preventScroll: true});
      scroller.dispatchEvent(new KeyboardEvent('keydown', {key: 'End', code: 'End', bubbles: true}));
    } catch (e4) {}
  }
}
return moved;
"""


_WA_NEW_CHAT_RAIL_LETTERS_JS = r"""
const drawer = document.querySelector('[data-testid="new-chat-drawer"]');
if (!drawer) return [];
const rail = drawer.querySelector('[data-testid="contact-list-key"]');
if (!rail) return [];
const out = [];
for (const el of rail.querySelectorAll('[role="button"], button, span, div')) {
  const t = (el.textContent || '').trim();
  if (!t || t.length > 2) continue;
  if (!/^[#A-Z]$/i.test(t)) continue;
  out.push(t.toUpperCase());
}
return [...new Set(out)].sort((a, b) => a.localeCompare(b));
"""


_WA_NEW_CHAT_CLICK_RAIL_LETTER_JS = r"""
const drawer = document.querySelector('[data-testid="new-chat-drawer"]');
const want = (arguments[0] || '').toUpperCase();
if (!drawer || !want) return false;
const rail = drawer.querySelector('[data-testid="contact-list-key"]');
if (!rail) return false;
for (const el of rail.querySelectorAll('[role="button"], button, span, div')) {
  const t = (el.textContent || '').trim().toUpperCase();
  if (t !== want) continue;
  try { el.click(); return true; } catch (e) {}
}
return false;
"""


_WA_NEW_CHAT_CLICK_NEXT_UNCLICKED_JS = r"""
const drawer = document.querySelector('[data-testid="new-chat-drawer"]');
const clicked = new Set(arguments[0] || []);
if (!drawer) return null;
const candidates = [];
for (const el of drawer.querySelectorAll('[role="button"], button, span, div')) {
  const t = (el.textContent || '').trim();
  if (!t || t.length > 2) continue;
  if (!/^[#A-Z]$/i.test(t)) continue;
  const key = t.toUpperCase();
  if (clicked.has(key)) continue;
  const r = el.getBoundingClientRect();
  if (r.width < 4 || r.height < 4) continue;
  candidates.push({el, key});
}
candidates.sort((a, b) => a.key.localeCompare(b.key));
for (const c of candidates) {
  try {
    c.el.click();
    return c.key;
  } catch (e) {}
}
return null;
"""


def _new_chat_contacts_snapshot(driver: webdriver.Chrome) -> list[str]:
    try:
        raw = driver.execute_script(_WA_NEW_CHAT_CONTACTS_SNAPSHOT_JS) or []
        if not isinstance(raw, list):
            return []
        return [str(x).strip() for x in raw if str(x).strip()]
    except Exception:
        return []


def _new_chat_contacts_scroll_step(driver: webdriver.Chrome) -> bool:
    moved = False
    try:
        moved = bool(driver.execute_script(_WA_NEW_CHAT_CONTACTS_SCROLL_STEP_JS))
    except Exception:
        pass
    if not moved:
        try:
            drawer = driver.find_element(By.CSS_SELECTOR, '[data-testid="new-chat-drawer"]')
            drawer.send_keys(Keys.PAGE_DOWN)
            moved = True
        except Exception:
            pass
    return moved


def _new_chat_click_next_letter(driver: webdriver.Chrome, clicked: set[str]) -> str | None:
    try:
        for letter in _new_chat_rail_letters(driver):
            key = str(letter).upper()
            if key in clicked:
                continue
            if _new_chat_click_rail_letter(driver, key):
                clicked.add(key)
                return key
        key = driver.execute_script(_WA_NEW_CHAT_CLICK_NEXT_UNCLICKED_JS, sorted(clicked))
        if key:
            clicked.add(str(key).upper())
            return str(key)
    except Exception:
        pass
    return None


def _new_chat_rail_letters(driver: webdriver.Chrome) -> list[str]:
    try:
        raw = driver.execute_script(_WA_NEW_CHAT_RAIL_LETTERS_JS) or []
        if not isinstance(raw, list):
            return []
        return [str(x).strip().upper() for x in raw if str(x).strip()]
    except Exception:
        return []


def _new_chat_click_rail_letter(driver: webdriver.Chrome, letter: str) -> bool:
    try:
        return bool(driver.execute_script(_WA_NEW_CHAT_CLICK_RAIL_LETTER_JS, str(letter).upper()))
    except Exception:
        return False


def _collect_visible_contact_names(driver: webdriver.Chrome, names: set[str]) -> bool:
    before = len(names)
    for raw in _new_chat_contacts_snapshot(driver):
        if _title_is_contact_candidate(raw):
            names.add(raw)
    return len(names) > before


def _sweep_new_chat_rail(driver: webdriver.Chrome, names: set[str], clicked: set[str]) -> bool:
    grew = False
    for letter in _new_chat_rail_letters(driver):
        key = str(letter).upper()
        if key in clicked:
            continue
        if not _new_chat_click_rail_letter(driver, key):
            continue
        clicked.add(key)
        time.sleep(0.32)
        if _collect_visible_contact_names(driver, names):
            grew = True
    return grew


def _scroll_candidate_for_new_chat_list(driver: webdriver.Chrome):
    try:
        return driver.execute_script(
            """
            const drawer = document.querySelector('[data-testid="new-chat-drawer"]');
            if (drawer) {
              const inDrawer = drawer.querySelectorAll('div[tabindex="0"], div[role="list"]');
              for (const d of inDrawer) {
                const tid = (d.getAttribute('data-testid') || '').toLowerCase();
                if (tid === 'contact-list-key') continue;
                if (d.scrollHeight > d.clientHeight + 80) return d;
              }
              let best = null;
              let bestScore = 0;
              for (const d of drawer.querySelectorAll('div, section')) {
                const tid = (d.getAttribute('data-testid') || '').toLowerCase();
                if (tid === 'contact-list-key') continue;
                const r = d.getBoundingClientRect();
                if (r.width < 140) continue;
                const sh = d.scrollHeight || 0;
                const ch = d.clientHeight || 0;
                if (sh > ch + 80 && sh > bestScore) {
                  bestScore = sh;
                  best = d;
                }
              }
              if (best) return best;
            }
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
    driver: webdriver.Chrome, max_rounds: int = 250, stable_stop: int = 8
) -> tuple[str, list[str]]:
    """
    Open the New chat panel and scroll the contact list to collect display names.
    WhatsApp virtualizes the list — we scroll the real list container (not the A–Z rail),
    scroll the last visible row into view, and jump via the A–Z rail on the drawer edge.
    Returns ('SUCCESS', names) or (error_string, []).
    """
    try:
        if not _try_click_new_chat(driver):
            return ("Could not find the New chat button (is WhatsApp fully loaded?)", [])
        time.sleep(0.75)
        names: set[str] = set()
        clicked_letters: set[str] = set()
        idle = 0
        idle_limit = max(10, int(stable_stop))
        max_steps = max(160, int(max_rounds))

        _collect_visible_contact_names(driver, names)
        _sweep_new_chat_rail(driver, names, clicked_letters)

        for step in range(max_steps):
            before = len(names)
            _collect_visible_contact_names(driver, names)
            grew = len(names) > before

            scrolled = _new_chat_contacts_scroll_step(driver)
            if grew or scrolled:
                idle = 0
            else:
                idle += 1

            if idle >= 2 or (step > 0 and step % 14 == 0):
                if _sweep_new_chat_rail(driver, names, clicked_letters):
                    idle = 0
                elif _new_chat_click_next_letter(driver, clicked_letters):
                    time.sleep(0.32)
                    _collect_visible_contact_names(driver, names)
                    idle = 0

            rail_letters = _new_chat_rail_letters(driver)
            rail_done = bool(rail_letters) and all(l in clicked_letters for l in rail_letters)
            if idle >= idle_limit and rail_done:
                break
            time.sleep(0.22)

        for _ in range(3):
            if not _sweep_new_chat_rail(driver, names, clicked_letters):
                break

        _try_click_back_or_escape(driver)
        time.sleep(0.5)
        out = sorted(names, key=lambda s: s.lower())
        if not out:
            return (
                "No contact names were read. Open New chat manually once to confirm the layout, then try again.",
                [],
            )
        logger.info("Contact sync collected %d name(s) after %d scroll step(s).", len(out), min(max_steps, step + 1))
        return ("SUCCESS", out)
    except Exception as e:
        _try_click_back_or_escape(driver)
        return (f"Contact sync failed: {e!r}"[:500], [])


def _click_groups_filter_in_chat_list(driver: webdriver.Chrome, timeout_seconds: int = 20) -> bool:
    """
    Click WhatsApp's built-in Groups chat filter tab (role='tab').
    This mirrors the user's proven selector flow.
    """
    try:
        groups_button = WebDriverWait(driver, timeout_seconds).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//button[@role='tab' and .//span[normalize-space()='Groups']]",
                )
            )
        )
        try:
            groups_button.click()
        except Exception:
            driver.execute_script("arguments[0].click();", groups_button)
        WebDriverWait(driver, timeout_seconds).until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//button[@role='tab' and @aria-pressed='true' and .//span[normalize-space()='Groups']]",
                )
            )
        )
        return True
    except Exception:
        return False


def _scroll_chat_list_for_groups(driver: webdriver.Chrome, max_idle_rounds: int = 3) -> None:
    previous_height = 0
    idle_rounds = 0
    while idle_rounds < max_idle_rounds:
        try:
            pane = driver.find_element(By.ID, "pane-side")
        except Exception:
            return
        try:
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", pane)
            time.sleep(1.2)
            current_height = int(driver.execute_script("return arguments[0].scrollHeight;", pane) or 0)
        except Exception:
            return
        if current_height == previous_height:
            idle_rounds += 1
        else:
            idle_rounds = 0
            previous_height = current_height


def _collect_group_names_visible_snapshot(driver: webdriver.Chrome) -> list[str]:
    js = """
    const rows = Array.from(document.querySelectorAll("#pane-side [data-testid^='list-item-']"));
    const names = [];
    for (const row of rows) {
      const titleNode =
        row.querySelector("[data-testid='cell-frame-title'] span[dir='auto'][title]") ||
        row.querySelector("span[dir='auto'][title]");
      if (!titleNode) continue;
      const t = (titleNode.getAttribute("title") || "").trim();
      if (t) names.push(t);
    }
    return names;
    """
    out = driver.execute_script(js) or []
    groups = {str(name).strip() for name in out if isinstance(name, str) and str(name).strip()}
    return sorted(groups, key=str.casefold)


def sync_whatsapp_groups_from_new_chat(
    driver: webdriver.Chrome, max_rounds: int = 70, stable_stop: int = 4
) -> tuple[str, list[str]]:
    """
    Collect group names from the main WhatsApp chat list by identifying group rows,
    then scrolling #pane-side until no new groups are discovered.
    This deliberately does not click "New chat".
    Returns ('SUCCESS', names) or (error_string, []).
    """
    try:
        try:
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "pane-side")))
        except TimeoutException:
            return ("Could not locate WhatsApp chat list (pane-side).", [])

        group_names: list[str] = []
        for attempt in range(2):
            try:
                if not _click_groups_filter_in_chat_list(driver):
                    if attempt == 1:
                        return ("Could not open WhatsApp Groups filter tab.", [])
                    time.sleep(1.0)
                    continue

                names_set: set[str] = set()
                # Capture first viewport before scrolling (top rows are often missed otherwise).
                names_set.update(_collect_group_names_visible_snapshot(driver))
                _scroll_chat_list_for_groups(driver)
                names_set.update(_collect_group_names_visible_snapshot(driver))
                group_names = sorted(names_set, key=str.casefold)
                break
            except StaleElementReferenceException:
                if attempt == 1:
                    raise
                time.sleep(1.0)

        if not group_names:
            return ("No groups were detected in the Groups-filtered chat list.", [])
        return ("SUCCESS", group_names)
    except Exception as e:
        return (f"Group sync failed: {e!r}"[:500], [])


def _group_shell_click_title_in_pane(driver: webdriver.Chrome, group_name: str) -> bool:
    try:
        return bool(
            driver.execute_script(
                """
                const want = arguments[0].trim().toLowerCase();
                const roots = document.querySelectorAll('#pane-side span[title], #pane-side span[dir="auto"]');
                for (const el of roots) {
                  const t = (el.getAttribute('title') || el.textContent || '').trim();
                  if (t.toLowerCase() !== want) continue;
                  try {
                    const row = el.closest('div[role="row"]');
                    if (row) { row.click(); return true; }
                    el.click(); return true;
                  } catch (e) {}
                }
                return false;
                """,
                group_name,
            )
        )
    except Exception:
        return False


def _open_group_chat_from_search(driver: webdriver.Chrome, group_name: str) -> str | None:
    """Search the sidebar for the group and open its chat. Returns None on success."""
    name = (group_name or "").strip()
    if not name:
        return "Empty group name"
    try:
        if not _ensure_whatsapp_main_list(driver):
            return "Search box not found (return to chat list failed)"
        wait = WebDriverWait(driver, CHAT_LOAD_TIMEOUT)
        search_box = _find_side_search_box(driver, wait)
        if not search_box:
            return "Search box not found"
        _clear_search_box(search_box)
        search_box.send_keys(name)
        search_box.send_keys(Keys.ENTER)
        time.sleep(1.8)
        if _search_shows_no_results(driver):
            return "Group not found in search"
        deadline = time.time() + float(GROUP_SEARCH_TIMEOUT)
        opened = False
        while time.time() < deadline:
            if _group_shell_click_title_in_pane(driver, name):
                try:
                    WebDriverWait(driver, NUMBER_SEARCH_TIMEOUT).until(
                        EC.presence_of_element_located(
                            (
                                By.XPATH,
                                "//footer//div[@contenteditable='true' and @role='textbox']",
                            )
                        )
                    )
                    opened = True
                    break
                except (TimeoutException, WebDriverException):
                    pass
            time.sleep(0.35)
        if not opened:
            return "Could not open group chat"
        return None
    except Exception as e:
        return f"Open group failed: {e!r}"[:500]


def _click_open_group_info_panel(driver: webdriver.Chrome) -> bool:
    try:
        return bool(
            driver.execute_script(
                """
                const main = document.querySelector('#main');
                if (!main) return false;
                const header = main.querySelector('[data-testid="conversation-header"]') || main.querySelector('header');
                if (!header) return false;
                const title =
                  header.querySelector('[data-testid="conversation-info-header-chat-title"]') ||
                  header.querySelector('[data-testid="conversation-info-header"]') ||
                  header.querySelector('div[role="button"] span[title]') ||
                  header.querySelector('span[title]');
                if (title) {
                  try { title.click(); return true; } catch (e) {}
                }
                try { header.click(); return true; } catch (e) {}
                return false;
                """
            )
        )
    except Exception:
        return False


def _maybe_click_view_all_members(driver: webdriver.Chrome) -> None:
    try:
        driver.execute_script(
            """
            const root = document.querySelector('[data-testid="drawer-right"]') || document.body;
            const cand = root.querySelectorAll('div[role="button"], button, span');
            for (const el of cand) {
              const t = (el.textContent || '').trim().toLowerCase();
              if (!t) continue;
              if (t.includes('view all') && t.includes('member')) {
                try { el.click(); return; } catch (e) {}
              }
            }
            """
        )
        time.sleep(0.6)
    except Exception:
        pass


def _group_members_drawer_js_root() -> str:
    return (
        'document.querySelector(\'[data-testid="drawer-right"]\') || '
        'document.querySelector(\'[data-testid="chat-info-drawer"]\')'
    )


def _is_group_member_title(title: str) -> bool:
    t = (title or "").strip()
    if len(t) < 2:
        return False
    low = t.lower()
    if low == "you":
        return False
    if low in ("group admin", "add member tag"):
        return False
    if low.startswith("add ") or "invite" in low:
        return False
    if re.fullmatch(r"[\W_]+", t):
        return False
    return True


_WA_GROUP_MEMBERS_MODAL_ROOT_FN = r"""
function groupMembersModalRoot() {
  return (
    document.querySelector('[data-testid="contacts-modal"]') ||
    document.querySelector('[role="dialog"][aria-label="Search members"]') ||
    document.querySelector('[aria-label="Search members"][role="dialog"]')
  );
}
function groupMembersModalVisible() {
  const modal = groupMembersModalRoot();
  if (!modal) return false;
  const r = modal.getBoundingClientRect();
  if (r.width < 60 || r.height < 60) return false;
  const st = window.getComputedStyle(modal);
  if (st.display === 'none' || st.visibility === 'hidden') return false;
  if (parseFloat(st.opacity || '1') < 0.1) return false;
  return true;
}
function groupMembersListRoot() {
  const modal = groupMembersModalRoot();
  if (groupMembersModalVisible()) return modal;
  return (
    document.querySelector('[data-testid="drawer-right"]') ||
    document.querySelector('[data-testid="chat-info-drawer"]')
  );
}
"""


def _member_title_search_query(title: str) -> str:
    """First meaningful token for the modal Search contacts filter."""
    t = (title or "").strip()
    if not t:
        return ""
    parts = re.split(r"[\s~._\-|]+", t)
    for part in parts:
        p = part.strip()
        if len(p) >= 2 and re.search(r"[a-zA-Z0-9]", p):
            return p[:32]
    return t[:32]


def _filter_group_members_search(driver: webdriver.Chrome, query: str) -> bool:
    try:
        return bool(
            driver.execute_script(
                _WA_GROUP_MEMBERS_MODAL_ROOT_FN
                + """
const modal = groupMembersModalRoot();
if (!modal) return false;
const input =
  modal.querySelector('input[aria-label="Search contacts"]') ||
  modal.querySelector('input[placeholder*="Search contacts" i]');
if (!input) return false;
try { input.focus({preventScroll: true}); } catch (e) {}
const q = String(arguments[0] || '');
try {
  input.value = '';
  input.dispatchEvent(new Event('input', {bubbles: true}));
} catch (e0) {}
if (q) {
  try {
    input.value = q;
    input.dispatchEvent(new Event('input', {bubbles: true}));
    input.dispatchEvent(new Event('change', {bubbles: true}));
  } catch (e1) {}
}
return true;
""",
                query,
            )
        )
    except Exception:
        return False


def _clear_group_members_search_filter(driver: webdriver.Chrome) -> None:
    _filter_group_members_search(driver, "")
    time.sleep(0.25)


def _selenium_type_members_search_filter(driver: webdriver.Chrome, query: str) -> bool:
    try:
        inp = driver.find_element(
            By.CSS_SELECTOR,
            '[data-testid="contacts-modal"] input[aria-label="Search contacts"], '
            '[role="dialog"][aria-label="Search members"] input[aria-label="Search contacts"]',
        )
        inp.click()
        inp.send_keys(Keys.CONTROL, "a")
        inp.send_keys(Keys.BACKSPACE)
        if query:
            inp.send_keys(query[:40])
        time.sleep(0.55)
        return True
    except Exception:
        return False


_WA_CLICK_GROUP_MEMBERS_SEARCH_JS = (
    _WA_GROUP_MEMBERS_MODAL_ROOT_FN
    + r"""
const root =
  document.querySelector('[data-testid="drawer-right"]') ||
  document.querySelector('[data-testid="chat-info-drawer"]');
if (!root) return false;
if (groupMembersModalVisible()) return false;

function clickSearchBtn(btn) {
  if (!btn) return false;
  if (btn.closest('[data-testid^="list-item-"]')) return false;
  const label = (btn.getAttribute('aria-label') || '').trim().toLowerCase();
  if (!label.includes('search') || !label.includes('member')) return false;
  if (label.includes('message') || label === 'search') return false;
  try { btn.scrollIntoView({block: 'center'}); btn.click(); return true; } catch (e) {}
  return false;
}

// Prefer exact "Search members" control beside the "N members" header row.
for (const el of root.querySelectorAll('span, div, h2')) {
  const t = (el.textContent || '').trim();
  if (!/\d+\s+members?\b/i.test(t)) continue;
  let block = el.parentElement;
  for (let depth = 0; depth < 8 && block; depth++) {
    for (const sel of [
      'button[aria-label="Search members"]',
      '[aria-label="Search members"]',
    ]) {
      const btn = block.querySelector(sel);
      if (clickSearchBtn(btn)) return true;
    }
    for (const btn of block.querySelectorAll('button,[role="button"]')) {
      if (clickSearchBtn(btn)) return true;
    }
    block = block.parentElement;
  }
}

for (const sel of ['button[aria-label="Search members"]', '[aria-label="Search members"]']) {
  const btn = root.querySelector(sel);
  if (clickSearchBtn(btn)) return true;
}

return false;
"""
)


_WA_GROUP_MEMBERS_SEARCH_OPEN_JS = (
    _WA_GROUP_MEMBERS_MODAL_ROOT_FN
    + r"""
return groupMembersModalVisible();
"""
)


_WA_GROUP_MEMBERS_SEARCH_SCROLL_JS = (
    _WA_GROUP_MEMBERS_MODAL_ROOT_FN
    + r"""
const root = groupMembersListRoot();
if (!root) return false;
let best = null;
let bestScore = -1;
for (const el of root.querySelectorAll('div')) {
  const sh = el.scrollHeight || 0;
  const ch = el.clientHeight || 0;
  if (sh <= ch + 60) continue;
  const items = el.querySelectorAll('[data-testid^="list-item-"]').length;
  if (items < 1) continue;
  let score = (sh - ch) * 5 + items * 6;
  if (el.tabIndex === 0) score += 900;
  if (el.tabIndex === -1 && items > 0) score += 500;
  if (el.getAttribute('role') === 'list') score += 400;
  if (score > bestScore) { bestScore = score; best = el; }
}
if (!best) {
  best = root.querySelector('div[tabindex="0"]') || root.querySelector('div[tabindex="-1"]');
}
if (!best) return false;
const items = best.querySelectorAll('[data-testid^="list-item-"]');
if (items.length) {
  const last = items[items.length - 1];
  try { last.scrollIntoView({block: 'end', inline: 'nearest'}); } catch (e) {}
}
try { best.focus({preventScroll: true}); } catch (e0) {}
const before = best.scrollTop || 0;
const step = Math.max(360, Math.floor((best.clientHeight || 420) * 0.92));
best.scrollTop = before + step;
try { best.dispatchEvent(new Event('scroll', {bubbles: true})); } catch (e2) {}
if ((best.scrollTop || 0) > before + 2) return true;
const prevH = best.scrollHeight || 0;
best.scrollTop = prevH;
try { best.dispatchEvent(new Event('scroll', {bubbles: true})); } catch (e3) {}
if ((best.scrollTop || 0) > before + 2 || (best.scrollHeight || 0) > prevH + 20) {
  return true;
}
try {
  best.dispatchEvent(new WheelEvent('wheel', {deltaY: step, bubbles: true}));
} catch (e4) {}
return (best.scrollTop || 0) > before + 2;
"""
)


_WA_GROUP_MEMBERS_SEARCH_SNAPSHOT_JS = (
    _WA_GROUP_MEMBERS_MODAL_ROOT_FN
    + r"""
const root = groupMembersListRoot();
if (!root) return [];
const out = [];
const seen = new Set();
for (const item of root.querySelectorAll('[data-testid^="list-item-"]')) {
  if (item.querySelector('[data-testid="section-header"]')) continue;
  const titleEl =
    item.querySelector("[data-testid='cell-frame-title'] span[dir='auto'][title]") ||
    item.querySelector("[data-testid='cell-frame-title'] span[title]") ||
    item.querySelector("[data-testid='cell-frame-title'] span[dir='auto']");
  if (!titleEl) continue;
  const title = (titleEl.getAttribute('title') || titleEl.textContent || '').trim();
  if (!title) continue;
  let phone = '';
  const phoneSpans = item.querySelectorAll('[role="gridcell"] span.xnpuxes, [role="gridcell"] span[dir="auto"]');
  for (const el of phoneSpans) {
    const tx = (el.textContent || '').trim();
    if (!tx || tx.length > 40) continue;
    if (/\+?\d[\d\s\-().]{7,}/.test(tx)) { phone = tx; break; }
  }
  if (!phone) {
    const cells = item.querySelectorAll('[role="gridcell"] span, [data-testid="cell-frame-secondary"] span');
    for (const el of cells) {
      const tx = (el.textContent || '').trim();
      if (!tx || tx.length > 40) continue;
      if (/\+?\d[\d\s\-().]{7,}/.test(tx)) { phone = tx; break; }
    }
  }
  const key = title + '|' + phone.replace(/\D/g, '');
  if (seen.has(key)) continue;
  seen.add(key);
  out.push({title, phone});
}
return out;
"""
)


def _click_group_members_search_icon(driver: webdriver.Chrome) -> bool:
    try:
        return bool(driver.execute_script(_WA_CLICK_GROUP_MEMBERS_SEARCH_JS))
    except Exception:
        return False


def _group_members_search_is_open(driver: webdriver.Chrome) -> bool:
    try:
        return bool(driver.execute_script(_WA_GROUP_MEMBERS_SEARCH_OPEN_JS))
    except Exception:
        return False


def _group_members_search_scroll_step(driver: webdriver.Chrome) -> bool:
    try:
        if bool(driver.execute_script(_WA_GROUP_MEMBERS_SEARCH_SCROLL_JS)):
            return True
    except Exception:
        pass
    try:
        return bool(
            driver.execute_script(
                _WA_GROUP_MEMBERS_MODAL_ROOT_FN
                + """
const modal = groupMembersModalRoot();
if (!modal) return false;
const scroller = modal.querySelector('div[tabindex="0"]') || modal;
const before = scroller.scrollTop || 0;
try { scroller.scrollTop = before + Math.max(280, scroller.clientHeight || 320); } catch (e) {}
try { scroller.dispatchEvent(new Event('scroll', {bubbles: true})); } catch (e2) {}
return (scroller.scrollTop || 0) > before + 2;
"""
            )
        )
    except Exception:
        return False


def _focus_group_members_modal_list(driver: webdriver.Chrome) -> None:
    try:
        driver.execute_script(
            _WA_GROUP_MEMBERS_MODAL_ROOT_FN
            + """
const modal = groupMembersModalRoot();
if (!modal) return;
const scroller = modal.querySelector('div[tabindex="0"]');
if (scroller) {
  try { scroller.focus({preventScroll: true}); } catch (e) {}
}
"""
        )
    except Exception:
        pass


def _group_members_search_snapshot(driver: webdriver.Chrome) -> list[dict[str, str]]:
    try:
        raw = driver.execute_script(_WA_GROUP_MEMBERS_SEARCH_SNAPSHOT_JS) or []
        if not isinstance(raw, list):
            return []
        out: list[dict[str, str]] = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            phone = str(row.get("phone") or "").strip()
            if _is_group_member_title(title):
                out.append({"title": title, "phone": phone})
        return out
    except Exception:
        return []


def _member_collect_key(title: str, phone: str) -> str:
    norm_title = (title or "").strip().lower()
    digits = _normalize_phone(phone)
    if len(digits) >= 8:
        return f"{norm_title}|{digits}"
    return f"{norm_title}|__name"


def _stable_collect_group_members_from_search(
    driver: webdriver.Chrome, progress: ProgressCallback = None
) -> list[dict[str, str]]:
    _report_progress(progress, "Scrolling member list (phase 1)…")
    order: list[dict[str, str]] = []
    seen: set[str] = set()
    idle = 0
    steps = 0
    max_steps = 120
    stable_snapshots = 0
    prev_snap_size = -1
    while idle < 4 and steps < max_steps:
        snap = _group_members_search_snapshot(driver)
        moved = False
        for row in snap:
            title = row["title"]
            phone = row.get("phone", "")
            key = _member_collect_key(title, phone)
            if key in seen:
                continue
            seen.add(key)
            order.append(row)
            moved = True
        scrolled = _group_members_search_scroll_step(driver)
        steps += 1
        snap_size = len(snap)
        if snap_size == prev_snap_size and not moved:
            stable_snapshots += 1
        else:
            stable_snapshots = 0
        prev_snap_size = snap_size
        if stable_snapshots >= 2 and not scrolled:
            break
        if moved or scrolled:
            idle = 0
        else:
            idle += 1
        time.sleep(0.08)
    _report_progress(progress, f"Phase 1 done: {len(order)} member row(s) collected in {steps} scroll step(s).")
    logger.info("Group member scroll pass: %d rows, %d scroll steps", len(order), steps)
    return order


_WA_TITLE_MATCH_FN = r"""
function normMemberTitle(s) {
  return (s || '').trim().toLowerCase().replace(/\s+/g, ' ');
}
function stripEmoji(s) {
  try {
    return (s || '').replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, '').trim();
  } catch (e) {
    return (s || '').trim();
  }
}
function titlesMatch(a, b) {
  const na = normMemberTitle(a);
  const nb = normMemberTitle(b);
  if (!na || !nb) return false;
  if (na === nb) return true;
  const ea = stripEmoji(na);
  const eb = stripEmoji(nb);
  if (ea && eb && ea === eb) return true;
  if (ea && eb && (ea.startsWith(eb) || eb.startsWith(ea))) return true;
  return false;
}
"""


_WA_FIND_GROUP_MEMBER_ROW_JS = (
    _WA_GROUP_MEMBERS_MODAL_ROOT_FN
    + _WA_TITLE_MATCH_FN
    + r"""
const want = arguments[0];
const wantDigits = (arguments[1] || '').replace(/\D/g, '');
const skipMatches = parseInt(arguments[2] || '0', 10) || 0;
const root = groupMembersModalVisible() ? groupMembersModalRoot() : (groupMembersListRoot() || document.body);
let matched = 0;
for (const item of root.querySelectorAll('[data-testid^="list-item-"]')) {
  if (item.querySelector('[data-testid="section-header"]')) continue;
  const titleEl =
    item.querySelector("[data-testid='cell-frame-title'] span[dir='auto'][title]") ||
    item.querySelector("[data-testid='cell-frame-title'] span[title]") ||
    item.querySelector("[data-testid='cell-frame-title'] span[dir='auto']");
  if (!titleEl) continue;
  const tAttr = (titleEl.getAttribute('title') || '').trim();
  const tText = (titleEl.textContent || '').trim();
  const t = tAttr || tText;
  if (!titlesMatch(t, want) && !titlesMatch(tAttr, want) && !titlesMatch(tText, want)) continue;
  if (wantDigits) {
    let rowDigits = '';
    const cells = item.querySelectorAll('[role="gridcell"] span, [data-testid="cell-frame-secondary"] span');
    for (const el of cells) {
      const tx = (el.textContent || '').trim();
      const m = tx.match(/\+?\d[\d\s\-().]{7,}/);
      if (m) { rowDigits = (m[0] || '').replace(/\D/g, ''); break; }
    }
    if (rowDigits && rowDigits !== wantDigits) continue;
  }
  if (matched < skipMatches) { matched++; continue; }
  try { item.scrollIntoView({block: 'center', inline: 'nearest'}); } catch (e) {}
  const btn =
    item.querySelector('[data-testid="cell-frame-container"]') ||
    item.querySelector('[role="button"]') ||
    titleEl;
  return btn;
}
return null;
"""
)


def _group_member_visible_in_search(
    driver: webdriver.Chrome, title: str, phone_hint: str = "", name_match_skip: int = 0
) -> bool:
    try:
        el = driver.execute_script(
            _WA_FIND_GROUP_MEMBER_ROW_JS,
            title,
            _normalize_phone(phone_hint),
            int(name_match_skip),
        )
        return el is not None
    except Exception:
        return False


def _scroll_group_members_search_to_top(driver: webdriver.Chrome) -> None:
    try:
        driver.execute_script(
            _WA_GROUP_MEMBERS_MODAL_ROOT_FN
            + """
const root = groupMembersListRoot();
if (!root) return;
const scrollers = root.querySelectorAll('div[tabindex="0"], div[tabindex="-1"]');
for (const el of scrollers) {
  try { el.scrollTop = 0; } catch (e) {}
}
"""
        )
    except Exception:
        pass


def _scroll_to_group_member_in_search(
    driver: webdriver.Chrome, title: str, phone_hint: str = "", name_match_skip: int = 0
) -> bool:
    if _group_member_visible_in_search(driver, title, phone_hint, name_match_skip):
        return True
    if _group_members_search_is_open(driver):
        query = _member_title_search_query(title) or title[:24]
        _filter_group_members_search(driver, query)
        _selenium_type_members_search_filter(driver, query)
        time.sleep(0.35)
        if _group_member_visible_in_search(driver, title, phone_hint, name_match_skip):
            return True
        _clear_group_members_search_filter(driver)
        time.sleep(0.2)
    if _group_member_visible_in_search(driver, title, phone_hint, name_match_skip):
        return True
    time.sleep(0.12)
    if _group_member_visible_in_search(driver, title, phone_hint, name_match_skip):
        return True
    for _ in range(40):
        if _group_member_visible_in_search(driver, title, phone_hint, name_match_skip):
            return True
        if not _group_members_search_scroll_step(driver):
            break
        time.sleep(0.08)
    return _group_member_visible_in_search(driver, title, phone_hint, name_match_skip)


def _click_group_member_in_search(
    driver: webdriver.Chrome, title: str, phone_hint: str = "", name_match_skip: int = 0
) -> bool:
    try:
        el = driver.execute_script(
            _WA_FIND_GROUP_MEMBER_ROW_JS,
            title,
            _normalize_phone(phone_hint),
            int(name_match_skip),
        )
        if el is None:
            return False
        try:
            ActionChains(driver).move_to_element(el).pause(0.05).click(el).perform()
            return True
        except Exception:
            pass
        try:
            el.click()
            return True
        except Exception:
            return bool(
                driver.execute_script(
                    "try { arguments[0].click(); return true; } catch (e) { return false; }",
                    el,
                )
            )
    except Exception:
        return False


def _open_group_member_menu_in_search(
    driver: webdriver.Chrome, title: str, phone_hint: str = "", name_match_skip: int = 0
) -> bool:
    """Left-click a member row to open the action menu (Contact info, Message, …)."""
    for attempt in range(3):
        if _click_group_member_in_search(driver, title, phone_hint, name_match_skip):
            time.sleep(0.45)
            if _member_context_menu_is_open(driver):
                return True
        if _right_click_group_member_in_search(driver, title, phone_hint, name_match_skip):
            return True
        time.sleep(0.2)
    return False


def _right_click_group_member_in_search(
    driver: webdriver.Chrome, title: str, phone_hint: str = "", name_match_skip: int = 0
) -> bool:
    try:
        el = driver.execute_script(
            _WA_FIND_GROUP_MEMBER_ROW_JS,
            title,
            _normalize_phone(phone_hint),
            int(name_match_skip),
        )
        if el is None:
            return False
        for attempt in range(3):
            try:
                ActionChains(driver).move_to_element(el).pause(0.08).context_click(el).perform()
            except Exception:
                pass
            time.sleep(0.35)
            if _member_context_menu_is_open(driver):
                return True
            try:
                driver.execute_script(
                    """
                    const target = arguments[0];
                    const r = target.getBoundingClientRect();
                    const x = r.left + Math.max(12, r.width * 0.55);
                    const y = r.top + Math.max(12, r.height * 0.5);
                    const opts = {bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, button: 2};
                    target.dispatchEvent(new MouseEvent('contextmenu', opts));
                    """,
                    el,
                )
            except Exception:
                pass
            time.sleep(0.35)
            if _member_context_menu_is_open(driver):
                return True
        return False
    except Exception:
        return False


def _member_context_menu_is_open(driver: webdriver.Chrome) -> bool:
    try:
        return bool(
            driver.execute_script(
                """
                const menus = document.querySelectorAll('[role="application"]');
                for (const menu of menus) {
                  const r = menu.getBoundingClientRect();
                  if (r.width < 40 || r.height < 20 || r.opacity === 0) continue;
                  if (menu.querySelector('[data-testid="mi-grp-contact-info"]')) return true;
                }
                return false;
                """
            )
        )
    except Exception:
        return False


def _read_phone_from_member_context_menu(driver: webdriver.Chrome) -> str:
    try:
        out = driver.execute_script(
            """
            const menus = document.querySelectorAll('[role="application"]');
            for (const menu of menus) {
              const txt = (menu.textContent || '').trim();
              const m = txt.match(/Message\\s+(\\+?[\\d\\s\\-().]{8,})/i);
              if (m) return (m[1] || '').trim();
              const spans = menu.querySelectorAll('span');
              for (const el of spans) {
                const t = (el.textContent || '').trim();
                if (!t) continue;
                const m2 = t.match(/^Message\\s+(\\+?[\\d\\s\\-().]{8,})$/i);
                if (m2) return (m2[1] || '').trim();
              }
            }
            return '';
            """
        )
        return str(out or "").strip()
    except Exception:
        return ""


def _click_contact_info_in_context_menu(driver: webdriver.Chrome) -> bool:
    try:
        for _ in range(8):
            clicked = bool(
                driver.execute_script(
                    """
                    const menus = document.querySelectorAll('[role="application"]');
                    for (const menu of menus) {
                      const r = menu.getBoundingClientRect();
                      if (r.width < 40 || r.height < 20) continue;
                      const item =
                        menu.querySelector('[data-testid="mi-grp-contact-info"][role="button"]') ||
                        menu.querySelector('[data-testid="mi-grp-contact-info"]');
                      if (!item) continue;
                      try { item.scrollIntoView({block: 'center'}); item.click(); return true; } catch (e) {}
                    }
                    const item = document.querySelector('[data-testid="mi-grp-contact-info"]');
                    if (item) {
                      try { item.scrollIntoView({block: 'center'}); item.click(); return true; } catch (e2) {}
                    }
                    return false;
                    """
                )
            )
            if clicked:
                time.sleep(0.35)
                return True
            time.sleep(0.25)
        return False
    except Exception:
        return False


def _dismiss_member_context_menu(driver: webdriver.Chrome) -> None:
    try:
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
        time.sleep(0.2)
    except Exception:
        pass


def _member_contact_info_is_open(driver: webdriver.Chrome) -> bool:
    try:
        return bool(
            driver.execute_script(
                """
                const sub =
                  document.querySelector('[data-testid="contact-info-subtitle selectable-text"]') ||
                  document.querySelector('[data-testid="contact-info-subtitle"]');
                if (sub) {
                  const r = sub.getBoundingClientRect();
                  if (r.width > 8 && r.height > 8) return true;
                }
                const hdr = document.querySelector('[data-testid="contact-info-header"]');
                if (hdr) {
                  const r2 = hdr.getBoundingClientRect();
                  if (r2.width > 8 && r2.height > 8) return true;
                }
                return false;
                """
            )
        )
    except Exception:
        return False


def _read_member_detail_from_panel(driver: webdriver.Chrome) -> tuple[str, str]:
    try:
        data = driver.execute_script(
            """
            const root =
              document.querySelector('[data-testid="contact-info-header"]')?.closest('[data-testid="drawer-right"]') ||
              document.querySelector('[data-testid="chat-info-drawer"]') ||
              document.querySelector('[data-testid="drawer-right"]') ||
              document.body;
            let name = '';
            const sub =
              root.querySelector('[data-testid="contact-info-subtitle selectable-text"]') ||
              root.querySelector('[data-testid="contact-info-subtitle"]');
            if (sub) name = (sub.textContent || '').trim();
            let phone = '';
            const isSubtitle = (el) => {
              if (!el) return false;
              if (el.matches('[data-testid="contact-info-subtitle"], [data-testid="contact-info-subtitle selectable-text"]')) return true;
              return !!el.closest('[data-testid="contact-info-subtitle"], [data-testid="contact-info-subtitle selectable-text"]');
            };
            const pickPhone = (el) => {
              if (!el || isSubtitle(el)) return;
              const tx = (el.textContent || '').trim();
              if (!tx || tx.length > 40) return;
              const m = tx.match(/^\\+?[\\d][\\d\\s\\-().]{7,}$/);
              if (m) phone = (m[0] || '').trim();
            };
            if (sub) {
              let block = sub.closest('div')?.parentElement?.parentElement || sub.parentElement;
              for (let i = 0; i < 4 && block; i++) {
                block.querySelectorAll('[data-testid="selectable-text"], span[dir="auto"]').forEach(pickPhone);
                if (phone) break;
                block = block.parentElement;
              }
            }
            if (!phone) {
              root.querySelectorAll('[data-testid="selectable-text"], span[dir="auto"]').forEach(pickPhone);
            }
            if (!phone) {
              root.querySelectorAll('span').forEach(el => {
                if (phone) return;
                pickPhone(el);
              });
            }
            return {name: name, phone: phone};
            """
        )
        if not isinstance(data, dict):
            return ("", "")
        n = str(data.get("name") or "").strip()
        p = str(data.get("phone") or "").strip()
        if not p:
            raw_phones = data.get("phones")
            if isinstance(raw_phones, list):
                for cand in raw_phones:
                    c = str(cand or "").strip()
                    m = re.search(r"\+?\d[\d\s\-().]{7,}", c)
                    if not m:
                        continue
                    token = m.group(0).strip()
                    if len(_normalize_phone(token)) >= 8:
                        p = token
                        break
        return (n, p)
    except Exception:
        return ("", "")


def _back_from_member_contact_info_to_group_info(driver: webdriver.Chrome) -> bool:
    try:
        clicked = bool(
            driver.execute_script(
                """
                const scopes = [];
                const ciHeader = document.querySelector('[data-testid="contact-info-header"]');
                if (ciHeader) scopes.push(ciHeader.closest('div') || ciHeader.parentElement);
                const drawer = document.querySelector('[data-testid="drawer-right"]') ||
                  document.querySelector('[data-testid="chat-info-drawer"]');
                if (drawer) scopes.push(drawer);
                scopes.push(document.body);
                for (const scope of scopes) {
                  if (!scope) continue;
                  const cands = scope.querySelectorAll(
                    '[data-testid="back-refreshed"], span[data-icon="back"], button[aria-label="Back"], div[role="button"][aria-label="Back"]'
                  );
                  for (const el of cands) {
                    try {
                      const btn =
                        el.closest('button,[role="button"],div[tabindex="0"],div[tabindex="-1"]') ||
                        el.parentElement ||
                        el;
                      const r = btn.getBoundingClientRect();
                      if (r.width < 4 || r.height < 4) continue;
                      btn.click();
                      return true;
                    } catch (e) {}
                  }
                }
                return false;
                """
            )
        )
        if clicked:
            time.sleep(0.55)
        return clicked
    except Exception:
        return False


def _reopen_group_members_search_modal(
    driver: webdriver.Chrome, progress: ProgressCallback = None
) -> bool:
    if not _navigate_to_group_info_hub(driver, progress):
        _report_progress(progress, "Group info panel not ready.")
        return False
    _report_progress(progress, "Clicking Search members…")
    for attempt in range(5):
        if _click_group_members_search_icon(driver):
            time.sleep(0.55)
            if _group_members_search_is_open(driver):
                _clear_group_members_search_filter(driver)
                _scroll_group_members_search_to_top(driver)
                _focus_group_members_modal_list(driver)
                _report_progress(progress, "Search members list is open.")
                return True
        time.sleep(0.4)
    ok = _group_members_search_is_open(driver)
    if not ok:
        _report_progress(progress, "Failed to open Search members list.")
    return ok


def _ensure_search_members_ready(
    driver: webdriver.Chrome,
    progress: ProgressCallback = None,
    *,
    force_reopen: bool = False,
) -> bool:
    """Return to group info and open a fresh Search members modal."""
    if force_reopen:
        if not _navigate_to_group_info_hub(driver, progress):
            return False
        return _reopen_group_members_search_modal(driver, progress)
    if _group_members_search_is_open(driver):
        _clear_group_members_search_filter(driver)
        _scroll_group_members_search_to_top(driver)
        _focus_group_members_modal_list(driver)
        return True
    return _reopen_group_members_search_modal(driver, progress)


def _wait_for_member_contact_info(driver: webdriver.Chrome, timeout_s: float = 8.0) -> bool:
    deadline = time.monotonic() + max(0.5, timeout_s)
    while time.monotonic() < deadline:
        if _member_contact_info_is_open(driver):
            return True
        time.sleep(0.2)
    return False


def _return_to_group_info_from_member_panels(driver: webdriver.Chrome) -> None:
    """Dismiss member menu and back out of Contact info / Search members."""
    _navigate_to_group_info_hub(driver)


def _open_group_member_contact_info_from_search(
    driver: webdriver.Chrome,
    title: str,
    phone_hint: str = "",
    name_match_skip: int = 0,
    progress: ProgressCallback = None,
) -> tuple[str, str]:
    """Left-click member → action menu → Contact info → read phone → back to group info."""
    _report_progress(progress, f"Left-clicking {title}…")
    if not _open_group_member_menu_in_search(driver, title, phone_hint, name_match_skip):
        _report_progress(progress, f"Could not open action menu for {title}.")
        return ("", "")
    _report_progress(progress, f"Opening Contact info for {title}…")
    phone = _read_phone_from_member_context_menu(driver)
    if phone and len(_normalize_phone(phone)) >= 8:
        _dismiss_member_context_menu(driver)
        _report_progress(progress, f"Got phone for {title} from menu.")
        return (title, phone)
    if not _click_contact_info_in_context_menu(driver):
        _dismiss_member_context_menu(driver)
        _report_progress(progress, f"Contact info menu item not found for {title}.")
        return ("", "")
    if not _wait_for_member_contact_info(driver, timeout_s=10.0):
        _return_to_group_info_from_member_panels(driver)
        _report_progress(progress, f"Contact info panel did not open for {title}.")
        return ("", "")
    time.sleep(0.5)
    name, phone = _read_member_detail_from_panel(driver)
    if not phone or len(_normalize_phone(phone)) < 8:
        time.sleep(0.35)
        name, phone = _read_member_detail_from_panel(driver)
    _report_progress(progress, f"Read phone for {title}: {phone or '(none)'}")
    _navigate_to_group_info_hub(driver, progress)
    return (name or title, phone)


def _recover_group_member_sync_context(
    driver: webdriver.Chrome, group_name: str, progress: ProgressCallback = None
) -> bool:
    """Re-open group info + members search after a back/escape closed panels."""
    if _ensure_search_members_ready(driver, progress, force_reopen=True):
        return True
    gname = (group_name or "").strip()
    if not gname:
        return False
    _report_progress(progress, f"Re-navigating to group {gname}…")
    err = _open_group_chat_from_search(driver, gname)
    if err:
        _report_progress(progress, f"Could not open group chat: {err}")
        return False
    time.sleep(0.6)
    if not _click_open_group_info_panel(driver):
        _report_progress(progress, "Could not open group info panel.")
        return False
    time.sleep(0.45)
    return _ensure_search_members_ready(driver, progress, force_reopen=True)


def _dismiss_side_search_overlay(driver: webdriver.Chrome) -> bool:
    """
    Dismiss the left-panel chat search when a group/query is still filled in
    (End icon / ic-close beside the search field). Stale search blocks later ops.
    """
    try:
        clicked = driver.execute_script(
            r"""
            const side = document.querySelector('#side');
            if (!side) return false;
            const fields = side.querySelectorAll(
              'input[role="textbox"][data-tab="3"], input[type="text"][data-tab="3"], ' +
              'div[contenteditable="true"][data-tab="3"]'
            );
            let hasQuery = false;
            for (const inp of fields) {
              const v = ((inp.value != null ? inp.value : inp.textContent) || '').trim();
              if (v.length >= 1) { hasQuery = true; break; }
            }
            if (!hasQuery) return false;
            const closeBtns = side.querySelectorAll('button[aria-label="End icon button"]');
            for (const btn of closeBtns) {
              if (btn.getAttribute('aria-disabled') === 'true') continue;
              const title = btn.querySelector('svg title');
              const isClose =
                title && (title.textContent || '').toLowerCase().includes('close');
              if (!isClose && !btn.querySelector('svg path')) continue;
              const r = btn.getBoundingClientRect();
              if (r.width < 4 || r.height < 4) continue;
              try { btn.click(); return true; } catch (e) {}
            }
            for (const btn of closeBtns) {
              if (btn.getAttribute('aria-disabled') === 'true') continue;
              const r = btn.getBoundingClientRect();
              if (r.width < 4 || r.height < 4) continue;
              try { btn.click(); return true; } catch (e) {}
            }
            return false;
            """
        )
        if clicked:
            time.sleep(0.35)
            return True
    except Exception:
        pass
    try:
        for btn in driver.find_elements(By.CSS_SELECTOR, '#side button[aria-label="End icon button"]'):
            try:
                if btn.is_displayed() and btn.is_enabled():
                    btn.click()
                    time.sleep(0.35)
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _force_cleanup_group_member_sync(driver: webdriver.Chrome) -> None:
    try:
        _dismiss_member_context_menu(driver)
        for _ in range(4):
            if _member_contact_info_is_open(driver):
                _back_from_member_contact_info_to_group_info(driver)
                time.sleep(0.3)
            else:
                break
        _close_group_members_search_modal(driver)
        time.sleep(0.2)
        _try_click_back_or_escape(driver)
        time.sleep(0.25)
        _try_click_back_or_escape(driver)
        time.sleep(0.2)
        if _dismiss_side_search_overlay(driver):
            logger.info("Cleared sidebar search overlay after group member sync.")
        else:
            try:
                short = WebDriverWait(driver, 3)
                search_box = _find_side_search_box(driver, short)
                if search_box:
                    val = (search_box.get_attribute("value") or search_box.text or "").strip()
                    if val:
                        _clear_search_box(search_box)
                        time.sleep(0.2)
            except Exception:
                pass
    except Exception:
        pass


def _fill_missing_member_phones_via_contact_info(
    driver: webdriver.Chrome,
    members: list[dict[str, str]],
    group_name: str = "",
    progress: ProgressCallback = None,
) -> None:
    """Second pass: for rows without a phone, reopen search and use Contact info."""
    need = [
        m
        for m in members
        if len(_normalize_phone(str(m.get("phone") or ""))) < 8
        and str(m.get("name") or m.get("title") or "").strip().lower() != "you"
    ]
    if not need:
        _report_progress(progress, "All members already have phones in the list — skipping Contact info pass.")
        return
    _report_progress(progress, f"Phase 2: looking up phones for {len(need)} member(s) via Contact info…")
    title_skip: dict[str, int] = {}
    recover_failures = 0
    for idx, member in enumerate(need, start=1):
        phone = str(member.get("phone") or "").strip()
        title = str(member.get("name") or member.get("title") or "").strip()
        if not title:
            continue
        skip = title_skip.get(title, 0)
        title_skip[title] = skip + 1
        _report_progress(progress, f"Member {idx}/{len(need)}: {title}")
        started = time.monotonic()
        if not _ensure_search_members_ready(
            driver, progress, force_reopen=(idx > 1)
        ):
            recover_failures += 1
            _report_progress(progress, f"Could not reopen Search members for {title} (attempt {recover_failures}).")
            if recover_failures >= 5:
                _report_progress(progress, "Stopping — too many panel recovery failures.")
                break
            if not _recover_group_member_sync_context(driver, group_name, progress):
                continue
        recover_failures = 0
        _focus_group_members_modal_list(driver)
        if not _scroll_to_group_member_in_search(driver, title, phone, skip):
            _report_progress(progress, f"Could not find {title} in Search members list.")
            _return_to_group_info_from_member_panels(driver)
            continue
        name, found_phone = _open_group_member_contact_info_from_search(
            driver, title, phone, skip, progress
        )
        if found_phone and len(_normalize_phone(found_phone)) >= 8:
            member["name"] = (name or title).strip()
            member["phone"] = found_phone.strip()
            _report_progress(progress, f"Saved phone for {title}: {found_phone}")
        elif time.monotonic() - started > 35:
            _report_progress(progress, f"Timed out looking up phone for {title}.")
        else:
            _report_progress(progress, f"No phone found for {title}.")


def _open_group_member_details(driver: webdriver.Chrome, title: str, phone_hint: str = "") -> tuple[str, str]:
    """Click member in search list; read phone from menu or contact-info panel."""
    if not _click_group_member_in_search(driver, title, phone_hint):
        return ("", "")
    time.sleep(0.35)
    if _member_context_menu_is_open(driver):
        phone = _read_phone_from_member_context_menu(driver)
        if phone and len(_normalize_phone(phone)) >= 8:
            _dismiss_member_context_menu(driver)
            return (title, phone)
        if _click_contact_info_in_context_menu(driver):
            time.sleep(0.45)
            name, phone = _read_member_detail_from_panel(driver)
            _ensure_group_members_search_visible(driver)
            return (name or title, phone)
        _dismiss_member_context_menu(driver)
        return (title, "")
    if _member_contact_info_is_open(driver):
        name, phone = _read_member_detail_from_panel(driver)
        _ensure_group_members_search_visible(driver)
        return (name or title, phone)
    return (title, "")


def _close_group_members_search_modal(driver: webdriver.Chrome) -> bool:
    closed = False
    try:
        closed = bool(
            driver.execute_script(
                _WA_GROUP_MEMBERS_MODAL_ROOT_FN
                + """
const modal = groupMembersModalRoot();
if (!modal) return false;
const closeBtn = modal.querySelector('button[aria-label="Close"]');
if (closeBtn) {
  try { closeBtn.click(); return true; } catch (e) {}
}
return false;
"""
            )
        )
    except Exception:
        pass
    if not closed:
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.25)
        except Exception:
            pass
    return closed or not _group_members_search_is_open(driver)


def _navigate_to_group_info_hub(
    driver: webdriver.Chrome, progress: ProgressCallback = None
) -> bool:
    """Back out of Contact info and Search members until only the group info drawer remains."""
    for step in range(10):
        if _member_contact_info_is_open(driver):
            _report_progress(progress, "Backing out of Contact info…")
            _back_from_member_contact_info_to_group_info(driver)
            time.sleep(0.5)
            continue
        if _member_context_menu_is_open(driver):
            _dismiss_member_context_menu(driver)
            time.sleep(0.2)
            continue
        if _group_members_search_is_open(driver):
            _report_progress(progress, "Closing Search members modal…")
            _close_group_members_search_modal(driver)
            time.sleep(0.45)
            continue
        drawers = driver.find_elements(By.CSS_SELECTOR, '[data-testid="drawer-right"]')
        if drawers and not _member_contact_info_is_open(driver) and not _group_members_search_is_open(driver):
            return True
        time.sleep(0.2)
    return bool(driver.find_elements(By.CSS_SELECTOR, '[data-testid="drawer-right"]'))


def _ensure_group_members_search_visible(driver: webdriver.Chrome) -> bool:
    for _ in range(5):
        if _group_members_search_is_open(driver) and not _member_contact_info_is_open(driver):
            if not _member_context_menu_is_open(driver):
                return True
        if _member_context_menu_is_open(driver):
            _dismiss_member_context_menu(driver)
            time.sleep(0.25)
            continue
        if _member_contact_info_is_open(driver):
            try:
                clicked = bool(
                    driver.execute_script(
                        _WA_GROUP_MEMBERS_MODAL_ROOT_FN
                        + """
                        const root = groupMembersModalRoot() ||
                          document.querySelector('[data-testid="chat-info-drawer"]') ||
                          document.querySelector('[data-testid="drawer-right"]') ||
                          document.body;
                        const cands = root.querySelectorAll(
                          "button[aria-label='Back'], div[role='button'][aria-label='Back'], span[data-icon='back'], span[data-testid='back-refreshed']"
                        );
                        for (const el of cands) {
                          try {
                            const btn = el.closest('button,[role="button"]') || el;
                            btn.click();
                            return true;
                          } catch (e) {}
                        }
                        return false;
                        """
                    )
                )
                if clicked:
                    time.sleep(0.35)
                    continue
            except Exception:
                pass
            try:
                driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
                time.sleep(0.3)
            except Exception:
                pass
            continue
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.25)
        except Exception:
            pass
    return _group_members_search_is_open(driver)


def _ensure_members_list_visible(driver: webdriver.Chrome) -> bool:
    return _ensure_group_members_search_visible(driver)


def sync_group_members_to_whatsapp_directory(
    driver: webdriver.Chrome,
    group_display_name: str,
    progress: ProgressCallback = None,
) -> tuple[str, list[dict[str, str]]]:
    """
    Open a group, open the members search list (ic-search), scroll all virtualized rows,
    and read each member's name + phone from the row, context menu, or contact-info panel.
    """
    out: list[dict[str, str]] = []
    gname = (group_display_name or "").strip()
    if not gname:
        return ("Empty group name.", [])

    try:
        _report_progress(progress, f"Opening group {gname}…")
        err = _open_group_chat_from_search(driver, gname)
        if err:
            return (err, [])
        time.sleep(0.7)
        _report_progress(progress, "Opening group info…")
        if not _click_open_group_info_panel(driver):
            return ("Could not open group info (header click). Is this a group chat?", [])
        try:
            WebDriverWait(driver, 18).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '[data-testid="drawer-right"]'))
            )
        except TimeoutException:
            return ("Group info panel did not open.", [])
        time.sleep(0.5)
        _report_progress(progress, "Opening Search members…")
        if not _click_group_members_search_icon(driver):
            _maybe_click_view_all_members(driver)
            time.sleep(0.4)
            if not _click_group_members_search_icon(driver):
                return ("Could not open the group members search list (Search members button).", [])
        time.sleep(0.6)
        if not _group_members_search_is_open(driver):
            return ("Members search list did not open.", [])
        _focus_group_members_modal_list(driver)
        time.sleep(0.25)

        members = _stable_collect_group_members_from_search(driver, progress)
        if not members:
            return ("No participants were listed in the members search panel.", [])

        pending: list[dict[str, str]] = []
        for row in members:
            raw_title = str(row.get("title") or "").strip()
            if not _is_group_member_title(raw_title):
                continue
            if raw_title.lower() == "you":
                continue
            phone = str(row.get("phone") or "").strip()
            pending.append({"name": raw_title, "phone": phone})

        _report_progress(
            progress,
            f"Found {len(pending)} member(s), "
            f"{sum(1 for m in pending if len(_normalize_phone(m.get('phone', ''))) >= 8)} with phone in list.",
        )

        _fill_missing_member_phones_via_contact_info(driver, pending, gname, progress)

        seen_keys: set[str] = set()
        for member in pending:
            name = str(member.get("name") or "").strip()
            phone = str(member.get("phone") or "").strip()
            if not name:
                continue
            key = _normalize_phone(phone) if len(_normalize_phone(phone)) >= 8 else name.strip().lower()
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            out.append({"name": name, "phone": phone})

        if not out:
            return ("Could not read any member details (privacy settings may hide numbers).", [])
        with_phone = sum(1 for m in out if len(_normalize_phone(m.get("phone", ""))) >= 8)
        _report_progress(progress, f"Done: {len(out)} name(s), {with_phone} with phone.")
        logger.info(
            "Group member sync %s: %d names, %d with phone",
            gname,
            len(out),
            with_phone,
        )
        return ("SUCCESS", out)
    except Exception as e:
        return (f"Group member sync failed: {e!r}"[:500], [])
    finally:
        _force_cleanup_group_member_sync(driver)

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
    progress: ProgressCallback = None,
) -> str:
    """
    Send one message. Does not raise. Returns 'SUCCESS' or error string (for DB).
    Group search limited to GROUP_SEARCH_TIMEOUT seconds; if group not found, returns error.
    For direct numbers: opens chat via web.whatsapp.com/send?phone=... (no sidebar search).
    Groups still use the WhatsApp sidebar search by group name.
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
                _log_wa_ui_state(driver, "group-not-found", progress, level="warning")
                return "Group not found (timeout)"
            _log_wa_ui_state(driver, "group-chat-opened", progress)
        else:
            phone_digits = _normalize_phone(receiver_identifier)
            if not phone_digits:
                return "Missing or invalid phone number"
            if not _open_chat_via_phone_link_same_tab(driver, phone_digits):
                _log_wa_ui_state(driver, "send-link-failed", progress, level="warning")
                return "Web send link could not open chat"
            _log_wa_ui_state(driver, "direct-chat-opened", progress)

        attach_list = list(attachment_paths or [])
        resolved_attach = _resolve_attachment_paths(attach_list)
        phone_for_attach = None if is_group else _normalize_phone(receiver_identifier)

        if resolved_attach:
            if not _prepare_attachment_send_after_chat_open(driver, phone_for_attach, progress):
                _log_wa_ui_state(driver, "compose-not-ready", progress, level="warning")
                return "Message box not found"
            logger.info(
                "Send with attachments: count=%d names=%s caption_len=%d",
                len(resolved_attach),
                [os.path.basename(p) for p in resolved_attach],
                len(message or ""),
            )
            attach_err = _send_attachments_in_current_chat(
                driver,
                attach_list,
                message_text=message or "",
                progress=progress,
                phone_digits=phone_for_attach,
            )
            if attach_err:
                _log_wa_ui_state(driver, "attach-send-error", progress, level="warning")
                return attach_err[:500]
            _cleanup_after_attachment_send(driver)
        else:
            time.sleep(1)
            _log_wa_ui_state(driver, "before-text-send", progress)
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
                _log_wa_ui_state(driver, "message-box-not-found", progress, level="warning")
                return "Message box not found"
            if message:
                _insert_text_into_contenteditable(driver, message_box, message)
                message_box.send_keys(Keys.ENTER)
            _log_wa_ui_state(driver, "text-sent", progress)
        time.sleep(1)
        return "SUCCESS"
    except Exception as e:
        _log_wa_ui_state(driver, "send-exception", progress, level="error")
        return f"Selenium/error: {e!r}"[:500]
