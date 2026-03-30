"""
WhatsApp Web sender for desktop app. Uses shared driver, 20s group timeout, never raises.
Returns "SUCCESS" or error string for DB logging.
"""
import logging
import os
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import WebDriverException, TimeoutException

from config import get_profile_dir

logger = logging.getLogger(__name__)

GROUP_SEARCH_TIMEOUT = 20
NUMBER_SEARCH_TIMEOUT = 20
CHAT_LOAD_TIMEOUT = 60

# WhatsApp Web empty search state (class names change; match visible copy).
_NO_SEARCH_RESULTS_TEXT = "No chats, contacts or messages found"


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


def _open_chat_via_phone_link_same_tab(driver: webdriver.Chrome, phone_digits: str) -> bool:
    """
    Open the chat in the same tab via WhatsApp Web's send URL (same session, no new tabs).
    Avoids https://wa.me/... which often opens intermediate pages or target=_blank links.
    """
    footer = (By.XPATH, "//footer//div[@contenteditable='true' and @role='textbox']")
    send_url = f"https://web.whatsapp.com/send?phone={phone_digits}"
    try:
        driver.get(send_url)
    except Exception:
        return False
    try:
        WebDriverWait(driver, CHAT_LOAD_TIMEOUT).until(
            EC.element_to_be_clickable(footer)
        )
        return True
    except (TimeoutException, WebDriverException):
        return False


def create_driver_for_profile(client_phno: str) -> webdriver.Chrome:
    """Create Chrome driver for this client's profile. Caller must quit() when done."""
    profile_dir = get_profile_dir(client_phno)
    os.makedirs(profile_dir, exist_ok=True)
    chrome_options = Options()
    chrome_options.add_argument("--user-data-dir=" + profile_dir)
    chrome_options.add_argument("--profile-directory=Default")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--no-sandbox")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=chrome_options)


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


def send_message(
    driver: webdriver.Chrome,
    receiver_identifier: str,
    message: str,
    is_group: bool,
) -> str:
    """
    Send one message. Does not raise. Returns 'SUCCESS' or error string (for DB).
    Group search limited to GROUP_SEARCH_TIMEOUT seconds; if group not found, returns error.
    """
    try:
        wait = WebDriverWait(driver, CHAT_LOAD_TIMEOUT)
        group_wait = WebDriverWait(driver, GROUP_SEARCH_TIMEOUT)
        number_wait = WebDriverWait(driver, NUMBER_SEARCH_TIMEOUT)

        # Keep same tab/session and open chats using WhatsApp side search for both
        # groups and direct numbers (instead of reloading /send?phone URL each time).
        search_locators = [
            (By.XPATH, "//input[@role='textbox' and @type='text' and @data-tab='3']"),
            (By.XPATH, "//div[@contenteditable='true' and @data-tab='3']"),
            (By.XPATH, "//div[@contenteditable='true' and @aria-label='Search']"),
        ]
        search_box = None
        for locator in search_locators:
            try:
                search_box = wait.until(EC.element_to_be_clickable(locator))
                break
            except (TimeoutException, WebDriverException):
                continue
        if not search_box:
            return "Search box not found"

        if is_group:
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
            phone_digits = _normalize_phone(receiver_identifier)
            if not phone_digits:
                return "Invalid phone number"
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
                    # Prefer explicit result row click by title containing the searched number.
                    # WhatsApp formats numbers as '+91 63751 96831', so use contains(...) with variants.
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
                    # If explicit result click failed, try ENTER fallback on first result.
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

        time.sleep(5)
        message_box_locators = [
            (By.XPATH, "//div[@contenteditable='true' and @data-tab='10']"),
            (By.XPATH, "//div[@contenteditable='true' and @data-tab='6']"),
            (By.XPATH, "//footer//div[@contenteditable='true' and @role='textbox']"),
        ]
        message_box = None
        for locator in message_box_locators:
            try:
                message_box = wait.until(EC.element_to_be_clickable(locator))
                break
            except (TimeoutException, WebDriverException):
                continue
        if not message_box:
            return "Message box not found"
        if message:
            message_box.click()
            message_box.send_keys(message)
            message_box.send_keys(Keys.ENTER)
        time.sleep(3)
        return "SUCCESS"
    except Exception as e:
        return f"Selenium/error: {e!r}"[:500]
