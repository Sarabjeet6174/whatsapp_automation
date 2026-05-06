"""
Main Tkinter window with mode-aware tabs:
- local: local SQLite mode only
- sql: existing SQL scheduler mode only
- hybrid: both tabs
"""
from __future__ import annotations

import csv
from typing import Any
import logging
import os
import queue
import threading
import time
import tkinter as tk
from datetime import date, datetime, timedelta
from string import Formatter
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

try:
    from PIL import Image, ImageTk

    _HAS_PIL = True
except ImportError:
    Image = None  # type: ignore[misc, assignment]
    ImageTk = None  # type: ignore[misc, assignment]
    _HAS_PIL = False

try:
    from tkcalendar import Calendar

    _HAS_TKCALENDAR = True
except ImportError:
    Calendar = None  # type: ignore[misc, assignment]
    _HAS_TKCALENDAR = False

from app.core.message_loop import SCHEDULER_INTERVAL
from app.core.profile_state import ProfileState
from app.core.scheduler import Scheduler
from app.db.local_access import (
    create_contact,
    create_contact_list,
    create_group,
    create_local_profile,
    create_local_scheduled_job,
    delete_contact_list,
    delete_contacts,
    delete_local_logs,
    delete_local_profile,
    delete_local_scheduled_job,
    delete_template,
    fetch_contact_lists,
    fetch_contacts,
    fetch_groups,
    fetch_local_logs,
    fetch_local_profiles,
    fetch_local_scheduled_jobs,
    fetch_templates,
    fetch_whatsapp_directory,
    fetch_due_local_scheduled_jobs,
    init_local_db,
    log_local_send,
    mark_local_scheduled_job_dispatched,
    mark_local_scheduled_job_error,
    rename_contact_list,
    rename_template,
    replace_whatsapp_directory,
    update_contact_list_fields,
    upsert_template,
)
from app.db.sql import fetch_clients
from app.whatsapp.sender import send_message, sync_whatsapp_contacts_from_new_chat
from config import allow_search_from_env
from app.services.constants import WA_SEND_ID_OFFSET as _WA_SEND_ID_OFFSET

logger = logging.getLogger(__name__)

_COMPOSE_BG = "#1e2228"
_COMPOSE_CARD = "#2b3038"
_COMPOSE_CAPTION_BG = "#3a4149"
_COMPOSE_FG = "#e8eaed"
_COMPOSE_MUTED = "#9aa0a6"
_COMPOSE_GREEN = "#00a884"
_COMPOSE_THUMB_SEL = "#00a884"
_IMAGE_PREVIEW_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_COMPOSE_EMOJI_ROW = (
    "😀 😃 😄 😁 😆 😅 🤣 😂 🙂 😉 😊 😍 🥰 😘 😗 😙 😚 😋 😛 😜 🤪 🥳 😏 🤗 🤔 🤐 🤫 😐 😑 😶 🙄 😬 🤥 😌 😔 😪 🤤 😴 🎉 ✨ 🔥 ❤️ 👍 👎 🙏 ✅ ❌ ⭐ 📎 📄 💼 🏠 🇮🇳".split()
)


def _build_new_contact_list_field_order(extra_fields_text: str) -> list[str]:
    """name and phone always first; then unique extras from comma-separated headers (order preserved)."""
    out: list[str] = ["name", "phone"]
    seen = {"name", "phone"}
    for part in (extra_fields_text or "").split(","):
        s = part.strip()
        if not s:
            continue
        sl = s.lower()
        if sl in seen:
            continue
        seen.add(sl)
        out.append(s)
    return out


def _csv_fieldnames_require_name_phone(fieldnames: list[str] | None) -> bool:
    if not fieldnames:
        return False
    lowers = {(f or "").strip().lower() for f in fieldnames}
    return "name" in lowers and "phone" in lowers


def _csv_dict_row_to_payload(row: dict[str, Any]) -> dict[str, Any]:
    canon: dict[str, str] = {}
    for k, v in row.items():
        if k is None:
            continue
        canon[str(k).strip().lower()] = (v if v is not None else "").strip()
    return {
        "name": canon.get("name", ""),
        "phone": canon.get("phone", ""),
        "email": canon.get("email", ""),
        "company": canon.get("company", ""),
        "extra": {k: v for k, v in canon.items() if k not in ("name", "phone", "email", "company")},
    }


def _merge_field_order(current: list[str], incoming: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in (current or []) + (incoming or []):
        s = str(raw).strip()
        if not s:
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    core = [x for x in out if x.lower() in ("name", "phone")]
    rest = [x for x in out if x.lower() not in ("name", "phone")]
    if "name" not in {x.lower() for x in core}:
        core.insert(0, "name")
    if "phone" not in {x.lower() for x in core}:
        core.append("phone")
    return core + rest


class MainWindow:
    def __init__(self, run_mode: str = "local") -> None:
        self.run_mode = run_mode
        self.root = tk.Tk()
        self.root.title("WhatsApp Desktop - Multi-Profile Sender")
        self.root.geometry("1100x760")
        self.root.minsize(900, 600)

        self.log_queue: "queue.Queue[tuple[str, str, str, str]]" = queue.Queue()
        self.scheduler = Scheduler(on_log=self._enqueue_log)
        self.profiles: list[ProfileState] = []
        self.profile_by_phno: dict[str, ProfileState] = {}
        self.allow_search_var = tk.BooleanVar(value=allow_search_from_env())
        self.status_var = tk.StringVar(value="Ready.")

        # local mode state
        self.local_profiles: list[dict] = []
        self.local_profile_var = tk.StringVar()
        self.local_list_var = tk.StringVar()
        self.template_list_var = tk.StringVar()
        self.local_template_var = tk.StringVar()
        self.local_group_var = tk.StringVar()
        self.send_target_var = tk.StringVar(value="contacts")
        self._local_contacts_cache: list[dict] = []
        self._local_contact_pick: set[int] = set()
        self._send_contacts_cache: list[dict[str, Any]] = []
        self._send_contact_pick: set[int] = set()
        self._wa_contacts_cache: list[dict[str, Any]] = []
        self._wa_contact_pick: set[int] = set()
        self.wa_contacts_tree: ttk.Treeview | None = None
        self._local_logs_window: tk.Toplevel | None = None
        self._local_logs_text: scrolledtext.ScrolledText | None = None
        self._local_logs_profile_id: int | None = None
        self.local_logs_panel_text: scrolledtext.ScrolledText | None = None
        self.sql_runtime_logs_frame: ttk.LabelFrame | None = None
        self.sql_runtime_logs_text: scrolledtext.ScrolledText | None = None
        self._local_send_queues: dict[str, "queue.Queue[dict[str, Any]]"] = {}
        self._local_send_workers_running: set[str] = set()
        self._local_send_lock = threading.Lock()
        self._how_to_window: tk.Toplevel | None = None
        self._pending_attachment_paths: list[str] = []
        self._local_attach_status_var = tk.StringVar(value="No attachments.")
        self._local_pages: dict[str, ttk.Frame] = {}
        self._local_nav_buttons: dict[str, ttk.Button] = {}
        self._local_nav_labels: dict[str, str] = {}
        self._local_active_page = "home"
        self._local_page_accents: dict[str, str] = {
            "profiles": "#0284c7",
            "contacts": "#7c3aed",
            "wa_contacts": "#0d9488",
            "templates": "#db2777",
            "send": "#059669",
            "schedule": "#2563eb",
            "logs": "#ea580c",
        }
        self._schedule_target_var = tk.StringVar(value="contacts")
        self._local_schedule_dt_var = tk.StringVar()
        self._send_timing_var = tk.StringVar(value="now")
        self._send_schedule_dt_var = tk.StringVar()
        self.local_schedule_tree: ttk.Treeview | None = None
        self.send_contacts_tree: ttk.Treeview | None = None
        self.send_template_cb: ttk.Combobox | None = None
        self.template_list_cb: ttk.Combobox | None = None
        self.template_vars_wrap: ttk.Frame | None = None
        self._local_schedule_worker_running = False
        self._local_schedule_lock = threading.Lock()
        self.SEND_TEMPLATE_CUSTOM = "(Custom — type message below)"
        self.send_template_var = tk.StringVar(value=self.SEND_TEMPLATE_CUSTOM)
        self.attachment_only_no_caption_var = tk.BooleanVar(value=False)
        self.send_compose_text: scrolledtext.ScrolledText | None = None
        self.schedule_compose_text: scrolledtext.ScrolledText | None = None
        self._compose_preview_win: tk.Toplevel | None = None
        self._compose_caption_text: tk.Text | None = None
        self._compose_preview_photo_ref: Any = None
        self._compose_preview_index: int = 0

        self._configure_styles()
        self._build_ui()
        self._build_menu()
        self._load_by_mode()

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        self.root.configure(bg="#eef2ff")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("SoftCard.TFrame", background="#f8fbff")
        style.configure("Sidebar.TFrame", background="#eef6ff")
        style.configure("Title.TLabel", font=("Segoe UI", 12, "bold"), background="#eef2ff", foreground="#1f2937")
        style.configure("Hint.TLabel", font=("Segoe UI", 9), background="#eef2ff", foreground="#4b5563")
        style.configure("Primary.TButton", font=("Segoe UI", 9, "bold"))
        style.configure("Nav.TButton", padding=12, anchor=tk.W, font=("Segoe UI", 10, "bold"))
        style.configure("Hero.TFrame", background="#f5f8ff")
        style.configure("HeroTitle.TLabel", font=("Segoe UI", 14, "bold"), background="#f5f8ff", foreground="#1e3a8a")
        style.configure("HeroText.TLabel", font=("Segoe UI", 10), background="#f5f8ff", foreground="#334155")
        style.configure("DashCard.TFrame", background="#ffffff")
        style.configure("DashCardTitle.TLabel", font=("Segoe UI", 11, "bold"), background="#ffffff", foreground="#1f2937")
        style.configure("DashCardText.TLabel", font=("Segoe UI", 9), background="#ffffff", foreground="#475569")
        style.configure("NavActiveProfiles.TButton", padding=12, anchor=tk.W, font=("Segoe UI", 10, "bold"))
        style.configure("NavActiveContacts.TButton", padding=12, anchor=tk.W, font=("Segoe UI", 10, "bold"))
        style.configure("NavActiveWa.TButton", padding=12, anchor=tk.W, font=("Segoe UI", 10, "bold"))
        style.configure("NavActiveTemplates.TButton", padding=12, anchor=tk.W, font=("Segoe UI", 10, "bold"))
        style.configure("NavActiveSend.TButton", padding=12, anchor=tk.W, font=("Segoe UI", 10, "bold"))
        style.configure("NavActiveSchedule.TButton", padding=12, anchor=tk.W, font=("Segoe UI", 10, "bold"))
        style.configure("NavActiveLogs.TButton", padding=12, anchor=tk.W, font=("Segoe UI", 10, "bold"))
        style.map("NavActiveProfiles.TButton", background=[("active", "#d9f2ff"), ("!active", "#d9f2ff")])
        style.map("NavActiveContacts.TButton", background=[("active", "#efe2ff"), ("!active", "#efe2ff")])
        style.map("NavActiveWa.TButton", background=[("active", "#ccfbf1"), ("!active", "#ccfbf1")])
        style.map("NavActiveTemplates.TButton", background=[("active", "#ffe4f1"), ("!active", "#ffe4f1")])
        style.map("NavActiveSend.TButton", background=[("active", "#dcfce7"), ("!active", "#dcfce7")])
        style.map("NavActiveSchedule.TButton", background=[("active", "#dbeafe"), ("!active", "#dbeafe")])
        style.map("NavActiveLogs.TButton", background=[("active", "#ffedd5"), ("!active", "#ffedd5")])
        style.configure("SectionTitle.TLabel", font=("Segoe UI", 11, "bold"), background="#ffffff", foreground="#1f2937")
        style.configure("Step.TLabel", font=("Segoe UI", 9, "bold"), background="#ffffff", foreground="#1d4ed8")
        style.configure("TButton", padding=6)
        style.configure("Treeview", rowheight=24, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill=tk.X)
        ttk.Label(top, text="WhatsApp Desktop - Multi-Profile Sender", style="Title.TLabel").pack(side=tk.LEFT, anchor=tk.W)
        ttk.Button(top, text="How To Use", command=self._show_how_to_use).pack(side=tk.RIGHT)

        status_strip = ttk.Frame(self.root, padding=(10, 6), style="Card.TFrame")
        status_strip.pack(fill=tk.X, padx=10, pady=(0, 6))
        ttk.Label(status_strip, text="Status:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(status_strip, textvariable=self.status_var, foreground="#1f2937").pack(side=tk.LEFT)

        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        if self.run_mode in ("sql", "hybrid"):
            self.sql_tab = ttk.Frame(self.tabs, style="Card.TFrame")
            self.tabs.add(self.sql_tab, text="SQL Mode")
            self._build_sql_tab(self.sql_tab)

        if self.run_mode in ("local", "hybrid"):
            self.local_tab = ttk.Frame(self.tabs, style="Card.TFrame")
            self.tabs.add(self.local_tab, text="Local Mode")
            self._build_local_tab(self.local_tab)

        if self.run_mode in ("sql", "hybrid"):
            self.sql_runtime_logs_frame = ttk.LabelFrame(self.root, text="Runtime Logs", padding=8)
            self.sql_runtime_logs_text = scrolledtext.ScrolledText(
                self.sql_runtime_logs_frame,
                height=8,
                state=tk.DISABLED,
                wrap=tk.WORD,
            )
            self.sql_runtime_logs_text.configure(
                bg="#0b1220",
                fg="#d1e4ff",
                insertbackground="#ffffff",
                font=("Consolas", 9),
            )
            self.sql_runtime_logs_text.pack(fill=tk.BOTH, expand=True)
            self.sql_runtime_logs_frame.pack(fill=tk.BOTH, expand=False, padx=10, pady=(0, 10))
            self.tabs.bind("<<NotebookTabChanged>>", self._on_tab_changed)
            self._on_tab_changed()


    def _build_sql_tab(self, parent: ttk.Frame) -> None:
        body = ttk.Frame(parent, padding=10, style="Card.TFrame")
        body.pack(fill=tk.BOTH, expand=True)
        left = ttk.Frame(body, padding=8, style="Card.TFrame")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right = ttk.Frame(body, padding=8, style="Card.TFrame")
        right.pack(side=tk.RIGHT, fill=tk.Y, expand=False)

        ttk.Label(left, text="Clients (SQL profiles)", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, pady=(0, 8))
        self.tree = ttk.Treeview(left, columns=("name", "phone", "status"), show="headings", height=14, selectmode="browse")
        self.tree.heading("name", text="Client")
        self.tree.heading("phone", text="Phone")
        self.tree.heading("status", text="Status")
        self.tree.column("name", width=250)
        self.tree.column("phone", width=140)
        self.tree.column("status", width=120)
        self.tree.tag_configure("status_running", foreground="#065f46")
        self.tree.tag_configure("status_paused", foreground="#92400e")
        self.tree.tag_configure("status_open", foreground="#1d4ed8")
        self.tree.tag_configure("status_not_open", foreground="#6b7280")
        self.tree.pack(fill=tk.BOTH, expand=True)

        ttk.Label(right, text="SQL Actions", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, pady=(0, 8))
        ttk.Checkbutton(right, text="Allow side search for phone numbers", variable=self.allow_search_var).pack(anchor=tk.W, pady=(0, 8))
        ttk.Button(right, text="Open Profile", command=self._on_open, style="Primary.TButton", width=24).pack(fill=tk.X, pady=3)
        ttk.Button(right, text="Start", command=self._on_start, width=24).pack(fill=tk.X, pady=3)
        ttk.Button(right, text="Pause", command=self._on_pause, width=24).pack(fill=tk.X, pady=3)
        ttk.Button(right, text="Resume", command=self._on_resume, width=24).pack(fill=tk.X, pady=3)
        ttk.Button(right, text="Stop", command=self._on_stop, width=24).pack(fill=tk.X, pady=3)
        ttk.Separator(right, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        ttk.Button(right, text="Pause All", command=self._on_pause_all, width=24).pack(fill=tk.X, pady=3)
        ttk.Button(right, text="Resume All", command=self._on_resume_all, width=24).pack(fill=tk.X, pady=3)
        ttk.Button(right, text="Refresh SQL List", command=self._load_clients, width=24).pack(fill=tk.X, pady=3)

    def _build_local_tab(self, parent: ttk.Frame) -> None:
        wrap = ttk.Frame(parent, padding=10, style="Card.TFrame")
        wrap.pack(fill=tk.BOTH, expand=True)
        wrap.columnconfigure(1, weight=1)
        wrap.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(wrap, style="Sidebar.TFrame", padding=(8, 10))
        sidebar.grid(row=0, column=0, sticky="nsw", padx=(0, 10))
        content = ttk.Frame(wrap, style="Card.TFrame")
        content.grid(row=0, column=1, sticky="nsew")
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)

        ttk.Label(sidebar, text="Local Workflow", style="SectionTitle.TLabel").pack(anchor=tk.W, pady=(0, 8))
        ttk.Label(sidebar, text="Follow the steps from top to bottom.", style="Hint.TLabel", wraplength=180).pack(
            anchor=tk.W, pady=(0, 10)
        )

        pages = [
            ("home", "[H] Dashboard"),
            ("profiles", "[P] Profiles"),
            ("contacts", "[C] Contacts & Lists"),
            ("wa_contacts", "[W] Your WhatsApp contacts"),
            ("templates", "[T] Templates"),
            ("send", "[S] Send Messages"),
            ("schedule", "[SC] Scheduling"),
            ("logs", "[L] Logs"),
        ]
        self._local_nav_buttons.clear()
        self._local_nav_labels.clear()
        for page_id, label in pages:
            btn = ttk.Button(sidebar, text=label, style="Nav.TButton", command=lambda p=page_id: self._show_local_page(p))
            btn.pack(fill=tk.X, pady=3)
            self._local_nav_buttons[page_id] = btn
            self._local_nav_labels[page_id] = label

        self._local_pages = {}
        self._local_pages["home"] = self._build_local_home_page(content)
        self._local_pages["profiles"] = self._build_local_profiles_page(content)
        self._local_pages["contacts"] = self._build_local_contacts_page(content)
        self._local_pages["wa_contacts"] = self._build_local_wa_contacts_page(content)
        self._local_pages["templates"] = self._build_local_templates_page(content)
        self._local_pages["send"] = self._build_local_send_page(content)
        self._local_pages["schedule"] = self._build_local_schedule_page(content)
        self._local_pages["logs"] = self._build_local_logs_page(content)
        if self._local_active_page not in self._local_pages:
            self._local_active_page = "home"
        self._show_local_page(self._local_active_page)

    def _build_local_home_page(self, parent: ttk.Frame) -> ttk.Frame:
        page = ttk.Frame(parent, style="Card.TFrame", padding=10)
        page.grid(row=0, column=0, sticky="nsew")
        hero = ttk.Frame(page, style="Hero.TFrame", padding=12)
        hero.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(hero, text="Welcome to Local Mode", style="HeroTitle.TLabel").pack(anchor=tk.W)
        ttk.Label(
            hero,
            text="Follow the steps below to manage profiles, contacts, templates, send messages, and schedule for later.",
            style="HeroText.TLabel",
            wraplength=860,
        ).pack(anchor=tk.W, pady=(4, 0))

        cards_wrap = ttk.Frame(page, style="Card.TFrame")
        cards_wrap.pack(fill=tk.BOTH, expand=True)
        for c in range(3):
            cards_wrap.columnconfigure(c, weight=1)
        for r in range(2):
            cards_wrap.rowconfigure(r, weight=1)

        cards = [
            ("1 Profiles", "Create, manage, and open WhatsApp profiles.", "profiles"),
            ("2 Contacts & Lists", "Add lists, import CSV, and pick recipients.", "contacts"),
            ("3 Templates", "Create and manage reusable message templates.", "templates"),
            ("4 Send Messages", "Send now to selected contacts or groups.", "send"),
            ("5 Scheduling", "Schedule messages for automatic sending later.", "schedule"),
            ("6 Logs", "Review send outcomes and errors quickly.", "logs"),
        ]

        for idx, (title, desc, page_id) in enumerate(cards):
            r, c = divmod(idx, 3)
            card = ttk.Frame(cards_wrap, style="DashCard.TFrame", padding=12)
            card.grid(row=r, column=c, padx=6, pady=6, sticky="nsew")
            ttk.Label(card, text=title, style="DashCardTitle.TLabel").pack(anchor=tk.W)
            ttk.Label(card, text=desc, style="DashCardText.TLabel", wraplength=220).pack(anchor=tk.W, pady=(4, 10))
            ttk.Button(card, text=f"Go to {title.split(' ', 1)[1]}", command=lambda p=page_id: self._show_local_page(p)).pack(
                anchor=tk.W
            )

        quick = ttk.LabelFrame(page, text="Quick Guide", padding=8)
        quick.pack(fill=tk.X, pady=(8, 0))
        quick_text = (
            "1. Create or open a profile\n"
            "2. Add or import contacts\n"
            "3. Create a message template\n"
            "4. Send now or schedule for later\n"
            "5. Monitor logs and status"
        )
        ttk.Label(quick, text=quick_text, justify=tk.LEFT).pack(anchor=tk.W)
        return page

    def _build_local_page_shell(self, parent: ttk.Frame, page_id: str, title: str, subtitle: str) -> ttk.Frame:
        page = ttk.Frame(parent, style="Card.TFrame", padding=10)
        page.grid(row=0, column=0, sticky="nsew")
        accent = self._local_page_accents.get(page_id, "#2563eb")
        accent_bar = tk.Frame(page, bg=accent, height=5, bd=0, highlightthickness=0)
        accent_bar.pack(fill=tk.X, pady=(0, 8))
        ttl = ttk.Label(page, text=title, style="SectionTitle.TLabel")
        ttl.configure(foreground=accent)
        ttl.pack(anchor=tk.W)
        ttk.Label(page, text=subtitle, style="Hint.TLabel", wraplength=860).pack(anchor=tk.W, pady=(2, 8))
        return page

    def _build_local_profiles_page(self, parent: ttk.Frame) -> ttk.Frame:
        page = self._build_local_page_shell(
            parent,
            "profiles",
            "Profiles",
            "Create one profile per WhatsApp number, then open it once to scan QR and keep that Chrome profile.",
        )
        ttk.Label(page, text="Step 1: Choose an existing profile", style="Step.TLabel").pack(anchor=tk.W)
        row1 = ttk.Frame(page, style="SoftCard.TFrame", padding=8)
        row1.pack(fill=tk.X, pady=(3, 10))
        ttk.Label(row1, text="Profile").pack(side=tk.LEFT)
        self.local_profile_cb = ttk.Combobox(row1, textvariable=self.local_profile_var, state="readonly", width=42)
        self.local_profile_cb.pack(side=tk.LEFT, padx=6)
        self.local_profile_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_local_profile_selected())
        ttk.Button(row1, text="Open Profile", command=self._open_local_profile, style="Primary.TButton").pack(
            side=tk.LEFT, padx=(10, 2)
        )
        ttk.Button(row1, text="Delete profile", command=self._delete_local_profile).pack(side=tk.LEFT, padx=4)

        ttk.Separator(page, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)
        ttk.Label(page, text="Step 2: Create a new profile", style="Step.TLabel").pack(anchor=tk.W, pady=(0, 3))
        profile_new = ttk.Frame(page, style="SoftCard.TFrame", padding=8)
        profile_new.pack(fill=tk.X)
        ttk.Label(profile_new, text="Name").pack(side=tk.LEFT, padx=(0, 2))
        self.new_local_profile_name = ttk.Entry(profile_new, width=28)
        self.new_local_profile_name.pack(side=tk.LEFT, padx=2)
        ttk.Label(profile_new, text="WhatsApp #").pack(side=tk.LEFT, padx=(10, 2))
        self.new_local_profile_phone = ttk.Entry(profile_new, width=20)
        self.new_local_profile_phone.pack(side=tk.LEFT, padx=2)
        ttk.Button(profile_new, text="Save profile", command=self._add_local_profile, style="Primary.TButton").pack(
            side=tk.LEFT, padx=8
        )
        return page

    def _build_local_contacts_page(self, parent: ttk.Frame) -> ttk.Frame:
        page = self._build_local_page_shell(
            parent,
            "contacts",
            "Contacts & Lists",
            "Pick a list, import CSV or add contacts manually, then mark recipients using the Pick column.",
        )
        top = ttk.Frame(page, style="SoftCard.TFrame", padding=8)
        top.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(top, text="Contact List").pack(side=tk.LEFT)
        self.local_list_cb = ttk.Combobox(top, textvariable=self.local_list_var, state="readonly", width=32)
        self.local_list_cb.pack(side=tk.LEFT, padx=6)
        self.local_list_cb.bind("<<ComboboxSelected>>", lambda _e: self._refresh_local_contacts())
        ttk.Button(top, text="Add List…", command=self._open_new_contact_list_dialog, style="Primary.TButton").pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(top, text="Rename list", command=self._rename_local_list).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Delete list", command=self._delete_local_list).pack(side=tk.LEFT, padx=2)

        ttk.Label(
            page,
            text="Click Pick to check/uncheck rows, or double-click any data column. If nothing is checked, Send Selected uses highlighted rows.",
            style="Hint.TLabel",
            wraplength=860,
        ).pack(anchor=tk.W, pady=(0, 2))
        self.local_contacts_tree = ttk.Treeview(
            page,
            columns=("pick", "name", "phone", "email", "company"),
            show="headings",
            height=16,
            selectmode="extended",
        )
        self.local_contacts_tree.heading("pick", text="Pick")
        self.local_contacts_tree.column("pick", width=44, stretch=False, anchor=tk.CENTER)
        for c, w in (("name", 180), ("phone", 130), ("email", 190), ("company", 140)):
            self.local_contacts_tree.heading(c, text=c.capitalize())
            self.local_contacts_tree.column(c, width=w)
        self.local_contacts_tree.pack(fill=tk.BOTH, expand=True, pady=(4, 6))
        self.local_contacts_tree.bind("<ButtonRelease-1>", self._on_local_contacts_tree_release, add=True)
        self.local_contacts_tree.bind("<Double-1>", self._on_local_contacts_tree_double1)

        cbtn = ttk.Frame(page, style="SoftCard.TFrame", padding=8)
        cbtn.pack(fill=tk.X)
        ttk.Button(cbtn, text="Add contact…", command=self._open_add_contact_dialog, style="Primary.TButton").pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(cbtn, text="Import Contacts CSV", command=self._import_contacts_csv).pack(side=tk.LEFT, padx=2)
        ttk.Button(cbtn, text="Check all", command=self._check_all_local_contacts).pack(side=tk.LEFT, padx=2)
        ttk.Button(cbtn, text="Uncheck all", command=self._uncheck_all_local_contacts).pack(side=tk.LEFT, padx=2)
        ttk.Button(cbtn, text="Delete Selected", command=self._delete_selected_contacts).pack(side=tk.LEFT, padx=2)
        return page

    def _build_local_wa_contacts_page(self, parent: ttk.Frame) -> ttk.Frame:
        page = self._build_local_page_shell(
            parent,
            "wa_contacts",
            "Your WhatsApp contacts",
            "Sync display names from WhatsApp’s New chat list (same as in the app). Pick people here, then go to Send and choose “WhatsApp contacts (search by name)”. The browser will search by name and open the chat.",
        )
        top = ttk.Frame(page, style="SoftCard.TFrame", padding=8)
        top.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(top, text="Open Profile", command=self._open_local_profile, style="Primary.TButton").pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(top, text="Load from WhatsApp (New chat)", command=self._sync_whatsapp_contacts_from_driver).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(top, text="Clear saved list", command=self._clear_saved_whatsapp_directory).pack(side=tk.LEFT, padx=2)
        ttk.Label(
            page,
            text="Keep this profile’s Chrome window on WhatsApp Web. Loading opens New chat and scrolls the list; large lists may take a few seconds.",
            style="Hint.TLabel",
            wraplength=860,
        ).pack(anchor=tk.W, pady=(0, 4))
        ttk.Label(
            page,
            text="Click Pick to choose who receives the next send; double-click toggles too.",
            style="Hint.TLabel",
            wraplength=860,
        ).pack(anchor=tk.W, pady=(0, 4))
        self.wa_contacts_tree = ttk.Treeview(
            page,
            columns=("pick", "name"),
            show="headings",
            height=16,
            selectmode="extended",
        )
        self.wa_contacts_tree.heading("pick", text="Pick")
        self.wa_contacts_tree.column("pick", width=44, stretch=False, anchor=tk.CENTER)
        self.wa_contacts_tree.heading("name", text="Name (from WhatsApp)")
        self.wa_contacts_tree.column("name", width=520, stretch=True)
        self.wa_contacts_tree.pack(fill=tk.BOTH, expand=True, pady=(4, 6))
        self.wa_contacts_tree.bind("<ButtonRelease-1>", self._on_wa_contacts_tree_release, add=True)
        self.wa_contacts_tree.bind("<Double-1>", self._on_wa_contacts_tree_double1)

        wrow = ttk.Frame(page, style="SoftCard.TFrame", padding=8)
        wrow.pack(fill=tk.X)
        ttk.Button(wrow, text="Check all", command=self._check_all_wa_contacts).pack(side=tk.LEFT, padx=2)
        ttk.Button(wrow, text="Uncheck all", command=self._uncheck_all_wa_contacts).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            wrow,
            text="Go to Send Messages with these picks",
            command=self._goto_send_with_whatsapp_picks,
            style="Primary.TButton",
        ).pack(side=tk.LEFT, padx=8)
        return page

    def _build_local_templates_page(self, parent: ttk.Frame) -> ttk.Frame:
        page = self._build_local_page_shell(
            parent,
            "templates",
            "Templates",
            "Create reusable messages using placeholders like {name}, {company}, and your CSV extra columns.",
        )
        top_t = ttk.Frame(page, style="SoftCard.TFrame", padding=8)
        top_t.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(top_t, text="Template").pack(side=tk.LEFT)
        self.local_template_cb = ttk.Combobox(top_t, textvariable=self.local_template_var, state="readonly", width=24)
        self.local_template_cb.pack(side=tk.LEFT, padx=(6, 4))
        self.local_template_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_local_template_pick())
        ttk.Button(top_t, text="Save Template", command=self._save_template, style="Primary.TButton").pack(side=tk.LEFT, padx=2)
        ttk.Button(top_t, text="Rename", command=self._rename_template).pack(side=tk.LEFT, padx=2)
        ttk.Button(top_t, text="Delete", command=self._delete_template).pack(side=tk.LEFT, padx=2)

        var_row = ttk.Frame(page, style="SoftCard.TFrame", padding=8)
        var_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(var_row, text="List for variables").pack(side=tk.LEFT)
        self.template_list_cb = ttk.Combobox(var_row, textvariable=self.template_list_var, state="readonly", width=30)
        self.template_list_cb.pack(side=tk.LEFT, padx=(6, 8))
        self.template_list_cb.bind("<<ComboboxSelected>>", lambda _e: self._refresh_template_variable_buttons())
        ttk.Label(var_row, text="Click a variable to insert at cursor:", style="Hint.TLabel").pack(side=tk.LEFT)

        self.template_vars_wrap = ttk.Frame(page, style="Card.TFrame")
        self.template_vars_wrap.pack(fill=tk.X, pady=(0, 6))

        self.template_name_entry = ttk.Entry(page)
        self.template_name_entry.pack(fill=tk.X, pady=(0, 4))
        self.template_name_entry.insert(0, "template_name")
        self.template_text = scrolledtext.ScrolledText(page, height=15, wrap=tk.WORD)
        self.template_text.pack(fill=tk.BOTH, expand=True)
        self.template_text.insert(tk.END, "Hi {name}, your company is {company}.")
        self._refresh_template_variable_buttons()
        return page

    def _build_local_send_page(self, parent: ttk.Frame) -> ttk.Frame:
        page = self._build_local_page_shell(
            parent,
            "send",
            "Send Messages",
            "Choose recipients or group, optionally attach files, then queue sends. Jobs run one by one per profile.",
        )
        tmpl_row = ttk.Frame(page, style="SoftCard.TFrame", padding=8)
        tmpl_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(tmpl_row, text="Template").pack(side=tk.LEFT)
        self.send_template_cb = ttk.Combobox(tmpl_row, textvariable=self.send_template_var, state="readonly", width=34)
        self.send_template_cb.pack(side=tk.LEFT, padx=6)
        self.send_template_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_send_template_pick())
        ttk.Button(tmpl_row, text="Go to Templates Page", command=lambda: self._show_local_page("templates")).pack(
            side=tk.LEFT, padx=(8, 2)
        )

        msg_row = ttk.Frame(page, style="SoftCard.TFrame", padding=8)
        msg_row.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        ttk.Label(msg_row, text="Message for this send", style="Step.TLabel").pack(anchor=tk.W)
        ttk.Label(
            msg_row,
            text="Type here without picking a template, or choose a template above to fill this box. "
            "Placeholders like {name} are replaced per contact.",
            style="Hint.TLabel",
        ).pack(anchor=tk.W, pady=(0, 4))
        self.send_compose_text = scrolledtext.ScrolledText(msg_row, height=8, wrap=tk.WORD, font=("Segoe UI", 10))
        self.send_compose_text.pack(fill=tk.BOTH, expand=True)

        attach_row = ttk.Frame(page, style="SoftCard.TFrame", padding=8)
        attach_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(attach_row, text="Attach files…", command=self._pick_local_attachments, style="Primary.TButton").pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(attach_row, text="Clear attachments", command=self._clear_local_attachments).pack(side=tk.LEFT, padx=2)
        ttk.Button(attach_row, text="Compose preview…", command=self._open_compose_preview).pack(side=tk.LEFT, padx=8)
        ttk.Label(attach_row, textvariable=self._local_attach_status_var, style="Hint.TLabel").pack(
            side=tk.LEFT, padx=8, anchor=tk.W
        )
        ttk.Checkbutton(
            attach_row,
            text="If attachments: send file only (no text/caption)",
            variable=self.attachment_only_no_caption_var,
        ).pack(side=tk.LEFT, padx=(12, 0))

        row = ttk.Frame(page, style="SoftCard.TFrame", padding=8)
        row.pack(fill=tk.X, pady=(0, 6))
        ttk.Radiobutton(row, text="Send to Contacts (lists/CSV)", variable=self.send_target_var, value="contacts").pack(
            side=tk.LEFT
        )
        ttk.Radiobutton(
            row, text="WhatsApp contacts (search by name)", variable=self.send_target_var, value="wa_directory"
        ).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Radiobutton(row, text="Send to Group", variable=self.send_target_var, value="group").pack(side=tk.LEFT, padx=8)
        ttk.Label(row, text="Group").pack(side=tk.LEFT, padx=(8, 2))
        self.local_group_cb = ttk.Combobox(row, textvariable=self.local_group_var, state="readonly", width=28)
        self.local_group_cb.pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="Add Group", command=self._add_group).pack(side=tk.LEFT, padx=2)
        self.send_target_var.trace_add("write", lambda *_a: self._on_send_target_mode_changed())

        if not self._send_schedule_dt_var.get():
            self._send_schedule_dt_var.set((datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M"))
        timing_row = ttk.Frame(page, style="SoftCard.TFrame", padding=8)
        timing_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(timing_row, text="When").pack(side=tk.LEFT)
        ttk.Radiobutton(timing_row, text="Send now", variable=self._send_timing_var, value="now").pack(side=tk.LEFT, padx=(6, 0))
        ttk.Radiobutton(timing_row, text="Schedule for later", variable=self._send_timing_var, value="later").pack(
            side=tk.LEFT, padx=(10, 0)
        )
        ttk.Label(timing_row, text="Run at").pack(side=tk.LEFT, padx=(12, 2))
        ttk.Entry(timing_row, textvariable=self._send_schedule_dt_var, width=22).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            timing_row,
            text="Pick date & time…",
            command=lambda: self._open_schedule_datetime_picker("send"),
        ).pack(side=tk.LEFT, padx=(4, 2))
        ttk.Label(timing_row, text="(type or calendar)", style="Hint.TLabel").pack(side=tk.LEFT, padx=(4, 0))

        send_btns = ttk.Frame(page, style="SoftCard.TFrame", padding=8)
        send_btns.pack(fill=tk.X)
        ttk.Button(send_btns, text="Open Profile", command=self._open_local_profile).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            send_btns,
            text="Send Selected",
            command=lambda: self._dispatch_local_send_or_schedule(selected_only=True),
            style="Primary.TButton",
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(send_btns, text="Send All", command=lambda: self._dispatch_local_send_or_schedule(selected_only=False)).pack(
            side=tk.LEFT, padx=2
        )

        ttk.Label(
            page,
            text="Recipients: use lists/CSV, or load names on “Your WhatsApp contacts” and pick “WhatsApp contacts (search by name)” above.",
            style="Step.TLabel",
        ).pack(anchor=tk.W, pady=(8, 2))
        self.send_contacts_tree = ttk.Treeview(
            page,
            columns=("pick", "name", "phone", "list_name"),
            show="headings",
            height=10,
            selectmode="extended",
        )
        self.send_contacts_tree.heading("pick", text="Pick")
        self.send_contacts_tree.column("pick", width=44, stretch=False, anchor=tk.CENTER)
        self.send_contacts_tree.heading("name", text="Name")
        self.send_contacts_tree.column("name", width=180, stretch=False)
        self.send_contacts_tree.heading("phone", text="Phone")
        self.send_contacts_tree.column("phone", width=130, stretch=False)
        self.send_contacts_tree.heading("list_name", text="List")
        self.send_contacts_tree.column("list_name", width=180, stretch=True)
        self.send_contacts_tree.pack(fill=tk.BOTH, expand=True, pady=(2, 4))
        self.send_contacts_tree.bind("<ButtonRelease-1>", self._on_send_contacts_tree_release, add=True)
        self.send_contacts_tree.bind("<Double-1>", self._on_send_contacts_tree_double1)

        pick_row = ttk.Frame(page, style="Card.TFrame")
        pick_row.pack(fill=tk.X)
        ttk.Button(pick_row, text="Check all recipients", command=self._check_all_send_contacts).pack(side=tk.LEFT, padx=2)
        ttk.Button(pick_row, text="Uncheck all recipients", command=self._uncheck_all_send_contacts).pack(side=tk.LEFT, padx=2)
        ttk.Button(pick_row, text="Refresh recipients", command=self._refresh_send_contacts).pack(side=tk.LEFT, padx=6)
        return page

    def _dispatch_local_send_or_schedule(self, selected_only: bool) -> None:
        if self._send_timing_var.get() == "later":
            self._schedule_target_var.set(self.send_target_var.get())
            self._local_schedule_dt_var.set(self._send_schedule_dt_var.get())
            self._schedule_local(selected_only=selected_only)
            return
        self._send_local(selected_only=selected_only)

    def _build_local_schedule_page(self, parent: ttk.Frame) -> ttk.Frame:
        page = self._build_local_page_shell(
            parent,
            "schedule",
            "Scheduling",
            "Queue messages for a future date/time. The app will dispatch them automatically when due.",
        )
        if not self._local_schedule_dt_var.get():
            self._local_schedule_dt_var.set((datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M"))

        row1 = ttk.Frame(page, style="SoftCard.TFrame", padding=8)
        row1.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(row1, text="Run at (YYYY-MM-DD HH:MM)").pack(side=tk.LEFT)
        ttk.Entry(row1, textvariable=self._local_schedule_dt_var, width=22).pack(side=tk.LEFT, padx=6)
        ttk.Button(
            row1,
            text="Pick date & time…",
            command=lambda: self._open_schedule_datetime_picker("schedule"),
        ).pack(side=tk.LEFT, padx=(2, 6))
        ttk.Radiobutton(row1, text="Contacts", variable=self._schedule_target_var, value="contacts").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Radiobutton(row1, text="WhatsApp names", variable=self._schedule_target_var, value="wa_directory").pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Radiobutton(row1, text="Group", variable=self._schedule_target_var, value="group").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(row1, text="Refresh jobs", command=self._refresh_local_schedule_jobs, style="Primary.TButton").pack(
            side=tk.RIGHT, padx=(6, 0)
        )

        msg_sched = ttk.Frame(page, style="SoftCard.TFrame", padding=8)
        msg_sched.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        ttk.Label(msg_sched, text="Message for scheduled run", style="Step.TLabel").pack(anchor=tk.W)
        ttk.Label(
            msg_sched,
            text="Kept in sync with Send Messages when you switch between those pages. Add files on Send before scheduling.",
            style="Hint.TLabel",
        ).pack(anchor=tk.W, pady=(0, 4))
        self.schedule_compose_text = scrolledtext.ScrolledText(msg_sched, height=6, wrap=tk.WORD, font=("Segoe UI", 10))
        self.schedule_compose_text.pack(fill=tk.BOTH, expand=True)

        sched_attach_row = ttk.Frame(page, style="SoftCard.TFrame", padding=8)
        sched_attach_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Checkbutton(
            sched_attach_row,
            text="If attachments: send file only (no text/caption)",
            variable=self.attachment_only_no_caption_var,
        ).pack(side=tk.LEFT)
        ttk.Label(sched_attach_row, text="(Choose attachments on Send Messages.)", style="Hint.TLabel").pack(
            side=tk.LEFT, padx=(8, 0)
        )

        row2 = ttk.Frame(page, style="SoftCard.TFrame", padding=8)
        row2.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(
            row2,
            text="Schedule Selected",
            command=lambda: self._schedule_local(selected_only=True),
            style="Primary.TButton",
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(row2, text="Schedule All", command=lambda: self._schedule_local(selected_only=False)).pack(side=tk.LEFT, padx=2)
        ttk.Button(row2, text="Delete Selected Job", command=self._delete_selected_schedule_job).pack(side=tk.LEFT, padx=8)

        self.local_schedule_tree = ttk.Treeview(
            page,
            columns=("run_at", "status", "target", "count", "error"),
            show="headings",
            height=14,
            selectmode="browse",
        )
        for col, txt, w in (
            ("run_at", "Run At", 145),
            ("status", "Status", 95),
            ("target", "Target", 110),
            ("count", "Items", 70),
            ("error", "Last Error", 420),
        ):
            self.local_schedule_tree.heading(col, text=txt)
            self.local_schedule_tree.column(col, width=w, stretch=False if col in ("run_at", "status", "target", "count") else True)
        self.local_schedule_tree.pack(fill=tk.BOTH, expand=True)
        return page

    def _parse_schedule_dt(self, text: str) -> datetime | None:
        raw = (text or "").strip()
        for fmt in ("%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M"):
            try:
                return datetime.strptime(raw, fmt)
            except Exception:
                continue
        return None

    def _open_schedule_datetime_picker(self, which: str = "send") -> None:
        """Open a month calendar + time chooser; updates both send and schedule time fields."""
        if not _HAS_TKCALENDAR or Calendar is None:
            messagebox.showinfo(
                "Scheduling",
                "Install the calendar package to use the visual date picker:\n"
                "  pip install tkcalendar",
            )
            return
        src = self._send_schedule_dt_var if which == "send" else self._local_schedule_dt_var
        raw = (src.get() or "").strip()
        initial = self._parse_schedule_dt(raw)
        if initial is None:
            initial = datetime.now() + timedelta(minutes=5)
        now = datetime.now()
        if initial < now:
            initial = now + timedelta(minutes=1)

        top = tk.Toplevel(self.root)
        top.title("Pick date & time")
        top.transient(self.root)
        top.grab_set()
        top.resizable(True, True)

        day0 = initial.date()
        today = date.today()
        if day0 < today:
            day0 = today

        cal = Calendar(
            top,
            selectmode="day",
            year=day0.year,
            month=day0.month,
            day=day0.day,
            mindate=today,
            font=("Segoe UI", 10),
        )
        cal.pack(padx=12, pady=(12, 8))

        time_fr = ttk.Frame(top, padding=(12, 0, 12, 12))
        time_fr.pack(fill=tk.X)
        ttk.Label(time_fr, text="Time (24-hour):").pack(side=tk.LEFT, padx=(0, 10))
        hour_var = tk.StringVar(value=f"{initial.hour:02d}")
        min_var = tk.StringVar(value=f"{initial.minute:02d}")
        ttk.Spinbox(time_fr, from_=0, to=23, textvariable=hour_var, width=4).pack(side=tk.LEFT, padx=2)
        ttk.Label(time_fr, text=":").pack(side=tk.LEFT)
        ttk.Spinbox(time_fr, from_=0, to=59, textvariable=min_var, width=4).pack(side=tk.LEFT, padx=2)

        btn_fr = ttk.Frame(top, padding=(12, 0, 12, 12))
        btn_fr.pack(fill=tk.X)

        def apply_choice() -> None:
            try:
                picked = cal.selection_get()
            except Exception:
                messagebox.showerror("Scheduling", "Could not read the selected date.", parent=top)
                return
            if picked is None:
                messagebox.showinfo("Scheduling", "Click a day in the calendar first.", parent=top)
                return
            picked_d = picked.date() if isinstance(picked, datetime) else picked
            try:
                h = int(str(hour_var.get()).strip())
                m = int(str(min_var.get()).strip())
            except ValueError:
                messagebox.showerror("Scheduling", "Enter valid hour (0–23) and minute (0–59).", parent=top)
                return
            h = max(0, min(23, h))
            m = max(0, min(59, m))
            dt = datetime.combine(picked_d, datetime.min.time().replace(hour=h, minute=m))
            if dt <= datetime.now():
                messagebox.showinfo("Scheduling", "Choose a date and time in the future.", parent=top)
                return
            s = dt.strftime("%Y-%m-%d %H:%M")
            self._send_schedule_dt_var.set(s)
            self._local_schedule_dt_var.set(s)
            top.destroy()

        def cancel_choice() -> None:
            top.destroy()

        ttk.Button(btn_fr, text="Cancel", command=cancel_choice).pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btn_fr, text="OK", command=apply_choice, style="Primary.TButton").pack(side=tk.RIGHT)

        top.bind("<Return>", lambda _e: apply_choice())
        top.bind("<Escape>", lambda _e: cancel_choice())

    def _collect_local_send_items(self, selected_only: bool, target_mode: str, template: str, custom_vars: dict[str, str]) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        if target_mode == "group":
            group = self.local_group_var.get().strip()
            if not group:
                messagebox.showinfo("Scheduling", "Select a group first.")
                return []
            items.append({"receiver": group, "name": group, "rendered": self._render_template(template, {}, custom_vars)})
            return items
        if target_mode == "wa_directory":
            p = self._current_local_profile()
            if not p:
                messagebox.showinfo("Scheduling", "Select profile first.")
                return []
            try:
                rows = fetch_whatsapp_directory(int(p["id"]))
            except Exception as e:
                messagebox.showerror("Scheduling", f"Could not load WhatsApp contacts:\n{e}")
                return []
            combined_pick: set[int] = set(self._send_contact_pick)
            for wid in self._wa_contact_pick:
                try:
                    combined_pick.add(_WA_SEND_ID_OFFSET + int(wid))
                except (TypeError, ValueError):
                    continue
            if not combined_pick and self.send_contacts_tree is not None:
                combined_pick = {int(x) for x in self.send_contacts_tree.selection()}
            if not combined_pick and self.wa_contacts_tree is not None:
                for x in self.wa_contacts_tree.selection():
                    try:
                        combined_pick.add(_WA_SEND_ID_OFFSET + int(x))
                    except (TypeError, ValueError):
                        continue
            if selected_only and not combined_pick:
                messagebox.showinfo(
                    "Scheduling",
                    "Pick at least one WhatsApp contact (Pick column on Send or Your WhatsApp contacts), or use Send/Schedule All.",
                )
                return []
            for r in rows:
                cid = int(r.get("id", 0))
                nm = (r.get("name") or "").strip()
                if cid <= 0 or not nm:
                    continue
                vid = _WA_SEND_ID_OFFSET + cid
                if selected_only and vid not in combined_pick:
                    continue
                c = {
                    "id": vid,
                    "name": nm,
                    "phone": "",
                    "email": "",
                    "company": "",
                    "extra": {},
                    "list_name": "WhatsApp",
                }
                items.append(
                    {
                        "receiver": nm,
                        "name": nm,
                        "rendered": self._render_template(template, c, custom_vars),
                    }
                )
            return items
        selected_ids = (
            set(self._send_contact_pick)
            if self._send_contact_pick
            else {int(x) for x in (self.send_contacts_tree.selection() if self.send_contacts_tree is not None else ())}
        )
        if selected_only and not selected_ids:
            messagebox.showinfo(
                "Scheduling",
                "Pick at least one contact (Pick column or highlighted row), or use Schedule All.",
            )
            return []
        contacts = self._send_contacts_cache[:]
        if selected_only:
            contacts = [c for c in contacts if c["id"] in selected_ids]
        for c in contacts:
            items.append(
                {
                    "receiver": str(c.get("phone", "")),
                    "name": str(c.get("name", "")),
                    "rendered": self._render_template(template, c, custom_vars),
                }
            )
        return items

    def _outgoing_message_body(self) -> str:
        if self._local_active_page == "schedule" and self.schedule_compose_text is not None:
            return self.schedule_compose_text.get("1.0", tk.END).strip()
        if self.send_compose_text is not None:
            return self.send_compose_text.get("1.0", tk.END).strip()
        return ""

    def _push_send_compose_to_schedule(self) -> None:
        if self.send_compose_text is None or self.schedule_compose_text is None:
            return
        self.schedule_compose_text.delete("1.0", tk.END)
        self.schedule_compose_text.insert("1.0", self.send_compose_text.get("1.0", tk.END))

    def _push_schedule_compose_to_send(self) -> None:
        if self.send_compose_text is None or self.schedule_compose_text is None:
            return
        self.send_compose_text.delete("1.0", tk.END)
        self.send_compose_text.insert("1.0", self.schedule_compose_text.get("1.0", tk.END))

    def _on_send_template_pick(self) -> None:
        choice = self.send_template_var.get()
        if choice == self.SEND_TEMPLATE_CUSTOM:
            return
        for t in getattr(self, "_local_templates", []):
            if t["name"] == choice:
                content = t.get("content") or ""
                if self.send_compose_text is not None:
                    self.send_compose_text.delete("1.0", tk.END)
                    self.send_compose_text.insert("1.0", content)
                if self.schedule_compose_text is not None:
                    self.schedule_compose_text.delete("1.0", tk.END)
                    self.schedule_compose_text.insert("1.0", content)
                break

    def _validate_outgoing_local(self, message_body: str, attachment_snapshot: list[str], attachment_only: bool) -> str | None:
        if attachment_only:
            if not attachment_snapshot:
                return "Turn off “send file only” or add at least one attachment on Send Messages."
            return None
        if not message_body and not attachment_snapshot:
            return "Enter a message, or attach at least one file, or use “send file only” with an attachment."
        return None

    def _schedule_local(self, selected_only: bool) -> None:
        p = self._current_local_profile()
        if not p:
            messagebox.showinfo("Scheduling", "Select profile first.")
            return
        run_at = self._parse_schedule_dt(self._local_schedule_dt_var.get())
        if run_at is None:
            messagebox.showinfo("Scheduling", "Use date-time format: YYYY-MM-DD HH:MM")
            return
        if run_at <= datetime.now():
            messagebox.showinfo("Scheduling", "Run time must be in the future.")
            return
        template = self._outgoing_message_body()
        attachment_only = self.attachment_only_no_caption_var.get()
        attachment_snapshot = [
            os.path.abspath(os.path.normpath(pth))
            for pth in self._pending_attachment_paths
            if (pth or "").strip() and os.path.isfile(pth)
        ]
        err = self._validate_outgoing_local(template, attachment_snapshot, attachment_only)
        if err:
            messagebox.showinfo("Scheduling", err)
            return
        target_mode = self._schedule_target_var.get().strip() or "contacts"
        custom_vars = self._parse_custom_vars()
        items = self._collect_local_send_items(selected_only=selected_only, target_mode=target_mode, template=template, custom_vars=custom_vars)
        if not items:
            return
        allow_sched = target_mode == "wa_directory" or self.allow_search_var.get()
        payload: dict[str, Any] = {
            "profile_id": int(p["id"]),
            "profile_phone": str(p["phone"]),
            "profile_name": str(p.get("name", "")),
            "target_mode": target_mode,
            "allow_search": allow_sched,
            "items": items,
            "attachment_paths": list(attachment_snapshot),
            "attachment_only_no_caption": bool(attachment_only),
        }
        try:
            create_local_scheduled_job(int(p["id"]), run_at, payload)
        except Exception as e:
            messagebox.showerror("Scheduling", f"Could not create scheduled job:\n{e}")
            return
        self._refresh_local_schedule_jobs()
        self.status_var.set(f"Scheduled {len(items)} target(s) for {run_at.strftime('%Y-%m-%d %H:%M')}.")

    def _refresh_local_schedule_jobs(self) -> None:
        if self.local_schedule_tree is None:
            return
        p = self._current_local_profile()
        for iid in self.local_schedule_tree.get_children():
            self.local_schedule_tree.delete(iid)
        if not p:
            return
        try:
            jobs = fetch_local_scheduled_jobs(int(p["id"]))
        except Exception:
            return
        for j in jobs:
            payload = j.get("payload") or {}
            target = str(payload.get("target_mode", "contacts"))
            count = len(payload.get("items") or [])
            run_at = j.get("run_at")
            run_txt = run_at.strftime("%Y-%m-%d %H:%M") if hasattr(run_at, "strftime") else str(run_at or "")
            self.local_schedule_tree.insert(
                "",
                tk.END,
                iid=str(j["id"]),
                values=(run_txt, str(j.get("status", "")), target, str(count), str(j.get("error_text", ""))),
            )

    def _delete_selected_schedule_job(self) -> None:
        p = self._current_local_profile()
        if not p or self.local_schedule_tree is None:
            return
        sel = self.local_schedule_tree.selection()
        if not sel:
            messagebox.showinfo("Scheduling", "Select a scheduled job first.")
            return
        jid = int(sel[0])
        try:
            delete_local_scheduled_job(int(p["id"]), jid)
        except Exception as e:
            messagebox.showerror("Scheduling", str(e))
            return
        self._refresh_local_schedule_jobs()
        self.status_var.set("Scheduled job deleted.")

    def _build_local_logs_page(self, parent: ttk.Frame) -> ttk.Frame:
        page = self._build_local_page_shell(
            parent,
            "logs",
            "Logs",
            "Review recent local sends for the selected profile. Refresh to pull latest entries.",
        )
        logs_head = ttk.Frame(page, style="SoftCard.TFrame", padding=8)
        logs_head.pack(fill=tk.X, pady=(0, 2))
        ttk.Button(logs_head, text="Refresh", command=self._refresh_local_logs_panel, style="Primary.TButton").pack(
            side=tk.LEFT, padx=(0, 4)
        )
        ttk.Button(logs_head, text="Delete Logs", command=self._delete_local_logs).pack(side=tk.LEFT)
        self.local_logs_panel_text = scrolledtext.ScrolledText(page, height=20, wrap=tk.WORD, state=tk.DISABLED)
        self.local_logs_panel_text.configure(
            bg="#0b1220",
            fg="#d1e4ff",
            insertbackground="#ffffff",
            font=("Consolas", 9),
        )
        self.local_logs_panel_text.pack(fill=tk.BOTH, expand=True)
        return page

    def _show_local_page(self, page_id: str) -> None:
        if page_id not in self._local_pages:
            return
        prev = self._local_active_page
        if prev == "send" and page_id == "schedule":
            self._push_send_compose_to_schedule()
        elif prev == "schedule" and page_id == "send":
            self._push_schedule_compose_to_send()
        self._local_active_page = page_id
        active_style = {
            "home": "NavActiveProfiles.TButton",
            "profiles": "NavActiveProfiles.TButton",
            "contacts": "NavActiveContacts.TButton",
            "wa_contacts": "NavActiveWa.TButton",
            "templates": "NavActiveTemplates.TButton",
            "send": "NavActiveSend.TButton",
            "schedule": "NavActiveSchedule.TButton",
            "logs": "NavActiveLogs.TButton",
        }
        for pid, frame in self._local_pages.items():
            if pid == page_id:
                frame.tkraise()
            btn = self._local_nav_buttons.get(pid)
            if btn is not None:
                base = self._local_nav_labels.get(pid, btn.cget("text"))
                if pid == page_id:
                    btn.configure(text=f">> {base}", style=active_style.get(pid, "Nav.TButton"))
                else:
                    btn.configure(text=f"   {base}", style="Nav.TButton")

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

    def _load_by_mode(self) -> None:
        if self.run_mode in ("sql", "hybrid"):
            self._load_clients()
        if self.run_mode in ("local", "hybrid"):
            try:
                init_local_db()
                self._load_local_profiles()
                self._ensure_local_schedule_worker()
            except Exception as e:
                messagebox.showerror(
                    "Local storage error",
                    "Local mode could not initialize its database.\n\n"
                    "What to do:\n"
                    "1) Close the app.\n"
                    "2) Run app from the latest code / rebuilt EXE.\n"
                    "3) Ensure app folder is writable.\n\n"
                    f"Details: {e}",
                )

    # SQL mode methods
    def _load_clients(self) -> None:
        try:
            clients = fetch_clients()
        except Exception as e:
            messagebox.showerror("DB Error", str(e))
            return
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.profiles.clear()
        self.profile_by_phno.clear()
        for c in clients:
            phno = c["client_phno"]
            if not phno:
                continue
            p = ProfileState(client_idno=c["client_idno"], client_name=c["client_name"] or "", client_phno=phno)
            self.profiles.append(p)
            self.profile_by_phno[phno] = p
            self.tree.insert("", tk.END, values=(p.client_name, phno, "Not open"), iid=phno)
        self._append_log("SYSTEM", "clients_load", f"Loaded {len(self.profiles)} SQL profile(s).")

    def _get_selected_profile(self) -> ProfileState | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return self.profile_by_phno.get(sel[0])

    def _refresh_statuses(self) -> None:
        if self.run_mode in ("sql", "hybrid"):
            for profile in self.profiles:
                if profile.is_stopped() or not profile.is_running():
                    status = "Open" if profile.get_driver() is not None else "Not open"
                elif profile.is_paused():
                    status = "Paused"
                else:
                    status = "Running"
                tag = {
                    "Running": "status_running",
                    "Paused": "status_paused",
                    "Open": "status_open",
                    "Not open": "status_not_open",
                }.get(status, "status_not_open")
                try:
                    self.tree.item(profile.client_phno, values=(profile.client_name, profile.client_phno, status), tags=(tag,))
                except tk.TclError:
                    pass
        self.root.after(2000, self._refresh_statuses)

    def _on_open(self) -> None:
        p = self._get_selected_profile()
        if not p:
            self.status_var.set("Select a SQL client first.")
            return
        self.status_var.set("Opening WhatsApp Web...")
        result = self.scheduler.open_profile(p)
        self.status_var.set("WhatsApp opened." if result == "SUCCESS" else f"Open failed: {result}")
        if result != "SUCCESS":
            messagebox.showerror("Open Profile", result)

    def _on_start(self) -> None:
        p = self._get_selected_profile()
        if not p:
            self.status_var.set("Select a SQL client first.")
            return
        self.scheduler.start_loop(p, allow_search=self.allow_search_var.get())
        self.status_var.set(f"SQL loop started. Poll interval={SCHEDULER_INTERVAL}s.")

    def _on_pause(self) -> None:
        p = self._get_selected_profile()
        if p:
            self.scheduler.pause(p)
            self.status_var.set("Paused.")

    def _on_resume(self) -> None:
        p = self._get_selected_profile()
        if p:
            self.scheduler.resume(p)
            self.status_var.set("Resumed.")

    def _on_stop(self) -> None:
        p = self._get_selected_profile()
        if p:
            self.scheduler.stop_loop(p)
            self.status_var.set("Stopped. Chrome stays open.")

    def _on_pause_all(self) -> None:
        for p in self.profiles:
            if p.is_running():
                self.scheduler.pause(p)
        self.status_var.set("All paused.")

    def _on_resume_all(self) -> None:
        for p in self.profiles:
            if p.is_running():
                self.scheduler.resume(p)
        self.status_var.set("All resumed.")

    # Local mode methods
    def _load_local_profiles(self) -> None:
        self.local_profiles = fetch_local_profiles()
        labels = [f'{p["name"]} ({p["phone"]})' for p in self.local_profiles]
        self.local_profile_cb["values"] = labels
        if labels and not self.local_profile_var.get():
            self.local_profile_var.set(labels[0])
            self._on_local_profile_selected()

    def _current_local_profile(self) -> dict | None:
        label = self.local_profile_var.get()
        for p in self.local_profiles:
            if label == f'{p["name"]} ({p["phone"]})':
                return p
        return None

    def _on_local_profile_selected(self) -> None:
        self._load_contact_lists()
        self._load_templates()
        self._load_groups()
        self._refresh_send_contacts()
        self._refresh_wa_contacts_tree()
        self._refresh_local_logs_panel()
        self._refresh_local_schedule_jobs()

    def _add_local_profile(self) -> None:
        name = self.new_local_profile_name.get().strip()
        phone = self.new_local_profile_phone.get().strip()
        if not name:
            messagebox.showinfo("Local profile", "Enter a profile name.")
            return
        if not phone:
            messagebox.showinfo("Local profile", "Enter the WhatsApp number for this profile.")
            return
        try:
            create_local_profile(name, phone)
            self.new_local_profile_name.delete(0, tk.END)
            self.new_local_profile_phone.delete(0, tk.END)
            self._load_local_profiles()
            self.local_profile_var.set(f"{name} ({phone})")
            self._on_local_profile_selected()
            self._show_local_page("profiles")
            self.status_var.set("Profile saved. Next: Open Profile, then go to Contacts & Lists.")
        except Exception as e:
            messagebox.showerror("Local profile", f"Could not create profile:\n{e}")
            return
        if messagebox.askyesno("Open profile now?", "Profile saved. Open WhatsApp profile now to create/use Chrome profile?"):
            self._open_local_profile()

    def _load_contact_lists(self, prefer_list_name: str | None = None) -> None:
        p = self._current_local_profile()
        if not p:
            return
        lists = fetch_contact_lists(p["id"])
        self._local_lists = lists
        names = [x["name"] for x in lists]
        self.local_list_cb["values"] = names
        if self.template_list_cb is not None:
            self.template_list_cb["values"] = names
        if names:
            cur = self.local_list_var.get()
            if cur in names:
                sel = cur
            elif prefer_list_name and prefer_list_name in names:
                sel = prefer_list_name
            else:
                sel = names[0]
            self.local_list_var.set(sel)
            if self.template_list_var.get() not in names:
                self.template_list_var.set(sel)
            self._refresh_local_contacts()
        else:
            self.local_list_var.set("")
            self.template_list_var.set("")
            for item in self.local_contacts_tree.get_children():
                self.local_contacts_tree.delete(item)
            self._configure_local_contacts_tree_columns([])
        self._refresh_template_variable_buttons()

    def _configure_local_contacts_tree_columns(self, extra_keys: list[str]) -> None:
        """Pick column + base columns + one Treeview column per distinct CSV extra field."""
        base = ("pick", "name", "phone", "email", "company")
        cols = base + tuple(extra_keys)
        tree = self.local_contacts_tree
        tree.configure(columns=cols)
        tree.heading("pick", text="Pick")
        tree.column("pick", width=44, stretch=False, anchor=tk.CENTER)
        base_widths = {"name": 160, "phone": 120, "email": 170, "company": 120}
        for c in ("name", "phone", "email", "company"):
            tree.heading(c, text=str(c).replace("_", " ").title())
            tree.column(c, width=base_widths.get(c, 120), stretch=False)
        for ek in extra_keys:
            key = str(ek)
            tree.heading(key, text=key.replace("_", " ").title())
            w = min(180, max(72, min(len(key) * 9, 160)))
            tree.column(key, width=w, stretch=False)

    def _selected_contact_list(self) -> dict | None:
        name = self.local_list_var.get()
        for x in getattr(self, "_local_lists", []):
            if x["name"] == name:
                return x
        return None

    def _selected_template_list(self) -> dict | None:
        name = self.template_list_var.get().strip()
        if not name:
            return None
        for x in getattr(self, "_local_lists", []):
            if x["name"] == name:
                return x
        return None

    def _insert_template_variable(self, key: str) -> None:
        token = "{" + str(key).strip() + "}"
        try:
            self.template_text.focus_set()
            self.template_text.insert(tk.INSERT, token)
        except Exception:
            self.template_text.insert(tk.END, token)

    def _refresh_template_variable_buttons(self) -> None:
        wrap = self.template_vars_wrap
        if wrap is None:
            return
        for child in wrap.winfo_children():
            child.destroy()
        lst = self._selected_template_list()
        fields: list[str] = []
        if lst:
            fields = [str(f).strip() for f in (lst.get("fields") or []) if str(f).strip()]
        if not fields:
            fields = ["name", "phone", "email", "company"]
        dedup: list[str] = []
        seen: set[str] = set()
        for f in fields:
            k = f.lower()
            if k in seen:
                continue
            seen.add(k)
            dedup.append(f)
        ttk.Label(wrap, text="Available:", style="Hint.TLabel").pack(side=tk.LEFT, padx=(0, 6))
        for f in dedup:
            ttk.Button(wrap, text="{" + f + "}", command=lambda key=f: self._insert_template_variable(key)).pack(
                side=tk.LEFT, padx=2, pady=2
            )

    def _delete_local_profile(self) -> None:
        p = self._current_local_profile()
        if not p:
            messagebox.showinfo("Local mode", "Select a profile first.")
            return
        if not messagebox.askyesno(
            "Delete profile",
            "Delete this profile and all its contact lists, contacts, templates, groups, and send logs?\n\n"
            "This cannot be undone. Close WhatsApp/Chrome for this number first if it is open.",
        ):
            return
        ph = str(p["phone"])
        st = self.profile_by_phno.get(ph)
        if st is not None:
            try:
                self.scheduler.stop_loop(st)
            except Exception:
                pass
            self.profile_by_phno.pop(ph, None)
        try:
            delete_local_profile(int(p["id"]))
        except Exception as e:
            messagebox.showerror("Delete profile", str(e))
            return
        self._load_local_profiles()
        self.status_var.set("Profile deleted.")

    def _delete_local_list(self) -> None:
        p = self._current_local_profile()
        lst = self._selected_contact_list()
        if not p or not lst:
            messagebox.showinfo("Local mode", "Select a contact list first.")
            return
        if not messagebox.askyesno(
            "Delete list",
            f'Delete list "{lst["name"]}" and all contacts in it? This cannot be undone.',
        ):
            return
        try:
            delete_contact_list(int(p["id"]), int(lst["id"]))
        except Exception as e:
            messagebox.showerror("Delete list", str(e))
            return
        self._local_contact_pick.clear()
        self._load_contact_lists()
        self.status_var.set("List deleted.")

    def _rename_local_list(self) -> None:
        p = self._current_local_profile()
        lst = self._selected_contact_list()
        if not p or not lst:
            messagebox.showinfo("Local mode", "Select a contact list first.")
            return
        new_name = simpledialog.askstring("Rename list", "New list name:", initialvalue=lst["name"])
        if not new_name or not str(new_name).strip() or str(new_name).strip() == lst["name"]:
            return
        nn = str(new_name).strip()
        try:
            rename_contact_list(int(p["id"]), int(lst["id"]), nn)
        except Exception as e:
            messagebox.showerror("Rename list", str(e))
            return
        self._load_contact_lists(prefer_list_name=nn)
        self.status_var.set("List renamed.")

    def _open_new_contact_list_dialog(self) -> None:
        p = self._current_local_profile()
        if not p:
            messagebox.showinfo("Local mode", "Select profile first.")
            return
        win = tk.Toplevel(self.root)
        win.title("New contact list")
        win.transient(self.root)
        win.resizable(True, False)
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frm, text="List name").grid(row=0, column=0, sticky=tk.W, pady=4)
        name_e = ttk.Entry(frm, width=36)
        name_e.grid(row=0, column=1, sticky=tk.EW, pady=4)
        mode_var = tk.StringVar(value="manual")
        mode_wrap = ttk.Frame(frm)
        mode_wrap.grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(6, 2))
        ttk.Radiobutton(mode_wrap, text="Manual: define columns now", variable=mode_var, value="manual").pack(side=tk.LEFT)
        ttk.Radiobutton(
            mode_wrap,
            text="From CSV: inherit columns (and import rows)",
            variable=mode_var,
            value="csv",
        ).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(frm, text="Required columns: name, phone (always).", font=("Segoe UI", 9, "bold")).grid(
            row=2, column=0, columnspan=2, sticky=tk.W, pady=(8, 2)
        )
        ttk.Label(
            frm,
            text="Extra column names for this list (comma-separated). Examples: email, company, city\n"
            "Leave empty for a list with only name + phone. You can add contacts manually or import CSV later.",
            wraplength=480,
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=2)
        extra_e = ttk.Entry(frm, width=36)
        extra_e.grid(row=4, column=0, columnspan=2, sticky=tk.EW, pady=4)
        extra_e.insert(0, "email, company")
        csv_row = ttk.Frame(frm)
        csv_row.grid(row=5, column=0, columnspan=2, sticky=tk.EW, pady=(2, 2))
        ttk.Label(csv_row, text="CSV file").pack(side=tk.LEFT)
        csv_path_var = tk.StringVar()
        csv_path_e = ttk.Entry(csv_row, textvariable=csv_path_var, width=32)
        csv_path_e.pack(side=tk.LEFT, padx=6, fill=tk.X, expand=True)

        def pick_csv() -> None:
            pth = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
            if pth:
                csv_path_var.set(pth)

        csv_btn = ttk.Button(csv_row, text="Browse...", command=pick_csv)
        csv_btn.pack(side=tk.LEFT)
        frm.columnconfigure(1, weight=1)

        def sync_mode() -> None:
            is_manual = mode_var.get() == "manual"
            extra_e.configure(state=tk.NORMAL if is_manual else tk.DISABLED)
            csv_path_e.configure(state=tk.NORMAL if not is_manual else tk.DISABLED)
            csv_btn.configure(state=tk.NORMAL if not is_manual else tk.DISABLED)

        mode_var.trace_add("write", lambda *_a: sync_mode())
        sync_mode()

        def save() -> None:
            ln = name_e.get().strip()
            if not ln:
                messagebox.showinfo("New list", "Enter a list name.")
                return
            if mode_var.get() == "manual":
                fields = _build_new_contact_list_field_order(extra_e.get())
                try:
                    create_contact_list(int(p["id"]), ln, fields)
                except Exception as e:
                    messagebox.showerror("New list", str(e))
                    return
                win.destroy()
                self._load_contact_lists(prefer_list_name=ln)
                self._show_local_page("contacts")
                self.status_var.set("List created. Next: add contacts or import CSV.")
                return

            csv_path = csv_path_var.get().strip()
            if not csv_path:
                messagebox.showinfo("New list", "Choose a CSV file.")
                return
            try:
                with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                    reader = csv.DictReader(f)
                    if not _csv_fieldnames_require_name_phone(reader.fieldnames):
                        messagebox.showerror("CSV format", "CSV must include name and phone columns.")
                        return
                    fields = _merge_field_order(["name", "phone"], [str(h).strip() for h in (reader.fieldnames or [])])
                    create_contact_list(int(p["id"]), ln, fields)
                    self._load_contact_lists(prefer_list_name=ln)
                    new_lst = self._selected_contact_list()
                    if not new_lst:
                        raise RuntimeError("List was created but could not be selected.")
                    count = 0
                    for row in reader:
                        payload = _csv_dict_row_to_payload(row)
                        if not payload.get("name") and not payload.get("phone"):
                            continue
                        create_contact(int(p["id"]), int(new_lst["id"]), payload)
                        count += 1
            except Exception as e:
                messagebox.showerror("New list", str(e))
                return
            win.destroy()
            self._refresh_local_contacts()
            self._show_local_page("contacts")
            self.status_var.set(f'List "{ln}" created and imported {count} contact(s).')

        ttk.Button(frm, text="Create list", command=save, style="Primary.TButton").grid(
            row=6, column=1, sticky=tk.E, pady=(12, 0)
        )
        ttk.Button(frm, text="Cancel", command=win.destroy).grid(row=6, column=0, sticky=tk.W, pady=(12, 0))

    def _open_add_contact_dialog(self) -> None:
        p = self._current_local_profile()
        lst = self._selected_contact_list()
        if not p or not lst:
            messagebox.showinfo("Local mode", "Select profile and contact list first.")
            return
        fields: list[str] = list(lst.get("fields") or ["name", "phone", "email", "company"])
        for c in self._local_contacts_cache:
            ex = c.get("extra") or {}
            if isinstance(ex, dict):
                fields = _merge_field_order(fields, [str(k) for k in ex.keys()])
        win = tk.Toplevel(self.root)
        win.title("Add contact")
        win.transient(self.root)
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)
        entries: dict[str, ttk.Entry] = {}
        for r, fld in enumerate(fields):
            ttk.Label(frm, text=fld).grid(row=r, column=0, padx=6, pady=4, sticky=tk.W)
            e = ttk.Entry(frm, width=42)
            e.grid(row=r, column=1, padx=6, pady=4, sticky=tk.EW)
            entries[fld] = e
        frm.columnconfigure(1, weight=1)

        def save() -> None:
            row = {k: entries[k].get().strip() for k in fields}
            if not row.get("name") or not row.get("phone"):
                messagebox.showinfo("Add contact", "Name and Phone are required.")
                return
            payload = {
                "name": row.get("name", ""),
                "phone": row.get("phone", ""),
                "email": row.get("email", ""),
                "company": row.get("company", ""),
                "extra": {
                    k: row[k]
                    for k in fields
                    if str(k).strip().lower() not in ("name", "phone", "email", "company")
                },
            }
            try:
                create_contact(int(p["id"]), int(lst["id"]), payload)
            except Exception as e:
                messagebox.showerror("Add contact", str(e))
                return
            win.destroy()
            self._refresh_local_contacts()
            self.status_var.set("Contact added.")

        ttk.Button(frm, text="Save contact", command=save, style="Primary.TButton").grid(
            row=len(fields), column=1, sticky=tk.E, pady=(12, 0)
        )
        ttk.Button(frm, text="Cancel", command=win.destroy).grid(row=len(fields), column=0, sticky=tk.W, pady=(12, 0))

    def _current_template_record(self) -> dict | None:
        name = self.local_template_var.get()
        for t in getattr(self, "_local_templates", []):
            if t["name"] == name:
                return t
        return None

    def _rename_template(self) -> None:
        p = self._current_local_profile()
        t = self._current_template_record()
        if not p or not t:
            messagebox.showinfo("Template", "Select a template from the dropdown first.")
            return
        new_name = simpledialog.askstring("Rename template", "New template name:", initialvalue=t["name"])
        if not new_name or not str(new_name).strip() or str(new_name).strip() == t["name"]:
            return
        nn = str(new_name).strip()
        try:
            rename_template(int(p["id"]), int(t["id"]), nn)
        except ValueError as e:
            messagebox.showerror("Rename template", str(e))
            return
        except Exception as e:
            messagebox.showerror("Rename template", str(e))
            return
        self._load_templates(prefer_name=nn)
        self._show_local_page("templates")
        self.status_var.set("Template renamed.")

    def _delete_template(self) -> None:
        p = self._current_local_profile()
        t = self._current_template_record()
        if not p or not t:
            messagebox.showinfo("Template", "Select a template from the dropdown first.")
            return
        if not messagebox.askyesno("Delete template", f'Delete template "{t["name"]}"?'):
            return
        try:
            delete_template(int(p["id"]), int(t["id"]))
        except Exception as e:
            messagebox.showerror("Delete template", str(e))
            return
        self._load_templates()
        self._show_local_page("templates")
        self.status_var.set("Template deleted.")

    def _refresh_local_contacts(self) -> None:
        p = self._current_local_profile()
        lst = self._selected_contact_list()
        if not p or not lst:
            return
        self._local_contacts_cache = fetch_contacts(p["id"], lst["id"])
        valid_ids = {c["id"] for c in self._local_contacts_cache}
        self._local_contact_pick &= valid_ids
        core_lc = {"name", "phone", "email", "company"}
        keys_set: set[str] = set()
        for f in lst.get("fields") or []:
            fl = str(f).strip().lower()
            if fl and fl not in core_lc:
                keys_set.add(str(f).strip())
        for c in self._local_contacts_cache:
            ex = c.get("extra") or {}
            if isinstance(ex, dict):
                for k in ex.keys():
                    kl = str(k).strip().lower()
                    if kl not in core_lc:
                        keys_set.add(str(k).strip())
        extra_keys = sorted(keys_set, key=str.lower)
        self._configure_local_contacts_tree_columns(extra_keys)
        for item in self.local_contacts_tree.get_children():
            self.local_contacts_tree.delete(item)
        for c in self._local_contacts_cache:
            ex = c.get("extra") or {}
            if not isinstance(ex, dict):
                ex = {}
            tail = tuple(str(ex.get(k, "") or "") for k in extra_keys)
            mark = "☑" if c["id"] in self._local_contact_pick else "☐"
            self.local_contacts_tree.insert(
                "",
                tk.END,
                iid=str(c["id"]),
                values=(mark, c["name"], c["phone"], c["email"], c["company"]) + tail,
            )

    def _import_contacts_csv(self) -> None:
        p = self._current_local_profile()
        lst = self._selected_contact_list()
        if not p or not lst:
            messagebox.showinfo("Local mode", "Select profile and list first.")
            return
        path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        count = 0
        skipped = 0
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not _csv_fieldnames_require_name_phone(reader.fieldnames):
                messagebox.showerror(
                    "CSV format",
                    "The CSV must include a header row with both name and phone columns.\n\n"
                    "Column names are matched case-insensitively (Name, PHONE, etc. are fine).\n"
                    "Optional: email, company, and any extra columns for templates.",
                )
                return
            csv_fields = _merge_field_order(["name", "phone"], [str(h).strip() for h in (reader.fieldnames or [])])
            merged_fields = _merge_field_order(list(lst.get("fields") or []), csv_fields)
            try:
                update_contact_list_fields(int(p["id"]), int(lst["id"]), merged_fields)
                lst["fields"] = merged_fields
            except Exception:
                pass
            for row in reader:
                payload = _csv_dict_row_to_payload(row)
                if not payload.get("name") and not payload.get("phone"):
                    skipped += 1
                    continue
                create_contact(int(p["id"]), int(lst["id"]), payload)
                count += 1
        self._refresh_local_contacts()
        msg = f"Imported {count} contact(s)."
        if skipped:
            msg += f" Skipped {skipped} empty row(s)."
        self.status_var.set(msg)

    def _contact_ids_for_bulk_action(self) -> list[int]:
        if self._local_contact_pick:
            return sorted(self._local_contact_pick)
        return [int(x) for x in self.local_contacts_tree.selection()]

    def _toggle_contact_pick(self, cid: int) -> None:
        tree = self.local_contacts_tree
        row = str(cid)
        if not tree.exists(row):
            return
        if cid in self._local_contact_pick:
            self._local_contact_pick.discard(cid)
            mark = "☐"
        else:
            self._local_contact_pick.add(cid)
            mark = "☑"
        vals = list(tree.item(row, "values"))
        if vals:
            vals[0] = mark
            tree.item(row, values=vals)

    def _on_local_contacts_tree_release(self, event: tk.Event) -> None:
        tree = self.local_contacts_tree
        if tree.identify_region(event.x, event.y) != "cell":
            return
        row = tree.identify_row(event.y)
        col = tree.identify_column(event.x)
        if not row or col != "#1":
            return
        try:
            cid = int(row)
        except ValueError:
            return
        self._toggle_contact_pick(cid)

    def _on_local_contacts_tree_double1(self, event: tk.Event) -> None:
        """Toggle pick when double-clicking a wide cell (not Pick), to avoid double-toggle on Pick."""
        tree = self.local_contacts_tree
        if tree.identify_region(event.x, event.y) != "cell":
            return
        if tree.identify_column(event.x) == "#1":
            return
        row = tree.identify_row(event.y)
        if not row:
            return
        try:
            cid = int(row)
        except ValueError:
            return
        self._toggle_contact_pick(cid)

    def _refresh_contact_pick_marks(self) -> None:
        tree = self.local_contacts_tree
        for row in tree.get_children():
            try:
                cid = int(row)
            except ValueError:
                continue
            vals = list(tree.item(row, "values"))
            if not vals:
                continue
            vals[0] = "☑" if cid in self._local_contact_pick else "☐"
            tree.item(row, values=vals)

    def _check_all_local_contacts(self) -> None:
        if not self._local_contacts_cache:
            return
        self._local_contact_pick = {c["id"] for c in self._local_contacts_cache}
        self._refresh_contact_pick_marks()

    def _uncheck_all_local_contacts(self) -> None:
        self._local_contact_pick.clear()
        self._refresh_contact_pick_marks()

    def _delete_selected_contacts(self) -> None:
        ids = self._contact_ids_for_bulk_action()
        if not ids:
            return
        delete_contacts(ids)
        self._local_contact_pick -= set(ids)
        self._refresh_local_contacts()
        self._refresh_send_contacts()

    def _on_send_target_mode_changed(self) -> None:
        self._send_contact_pick.clear()
        self._refresh_send_contacts()

    def _refresh_send_contacts(self) -> None:
        tree = self.send_contacts_tree
        if tree is None:
            return
        p = self._current_local_profile()
        for row in tree.get_children():
            tree.delete(row)
        self._send_contacts_cache = []
        self._send_contact_pick.clear()
        if not p:
            return
        if self.send_target_var.get() == "wa_directory":
            try:
                rows = fetch_whatsapp_directory(int(p["id"]))
            except Exception:
                return
            self._send_contacts_cache = []
            for r in rows:
                cid = int(r.get("id", 0))
                nm = str(r.get("name", "")).strip()
                if cid <= 0 or not nm:
                    continue
                vid = _WA_SEND_ID_OFFSET + cid
                self._send_contacts_cache.append(
                    {
                        "id": vid,
                        "name": nm,
                        "phone": "",
                        "email": "",
                        "company": "",
                        "extra": {},
                        "list_name": "WhatsApp",
                    }
                )
            for c in self._send_contacts_cache:
                mark = "☑" if c["id"] in self._send_contact_pick else "☐"
                tree.insert("", tk.END, iid=str(c["id"]), values=(mark, c["name"], c["phone"], c["list_name"]))
            return

        all_contacts: list[dict[str, Any]] = []
        try:
            for lst in fetch_contact_lists(int(p["id"])):
                rows = fetch_contacts(int(p["id"]), int(lst["id"]))
                for c in rows:
                    all_contacts.append(
                        {
                            "id": int(c.get("id", 0)),
                            "name": str(c.get("name", "")),
                            "phone": str(c.get("phone", "")),
                            "email": str(c.get("email", "")),
                            "company": str(c.get("company", "")),
                            "extra": c.get("extra") or {},
                            "list_name": str(lst.get("name", "")),
                        }
                    )
        except Exception:
            return
        seen: set[int] = set()
        unique: list[dict[str, Any]] = []
        for c in all_contacts:
            cid = int(c.get("id", 0))
            if cid <= 0 or cid in seen:
                continue
            seen.add(cid)
            unique.append(c)
        self._send_contacts_cache = unique
        for c in self._send_contacts_cache:
            mark = "☑" if c["id"] in self._send_contact_pick else "☐"
            tree.insert("", tk.END, iid=str(c["id"]), values=(mark, c["name"], c["phone"], c["list_name"]))

    def _toggle_send_contact_pick(self, cid: int) -> None:
        tree = self.send_contacts_tree
        if tree is None:
            return
        row = str(cid)
        if not tree.exists(row):
            return
        if cid in self._send_contact_pick:
            self._send_contact_pick.discard(cid)
            mark = "☐"
        else:
            self._send_contact_pick.add(cid)
            mark = "☑"
        vals = list(tree.item(row, "values"))
        if vals:
            vals[0] = mark
            tree.item(row, values=vals)

    def _on_send_contacts_tree_release(self, event: tk.Event) -> None:
        tree = self.send_contacts_tree
        if tree is None or tree.identify_region(event.x, event.y) != "cell":
            return
        row = tree.identify_row(event.y)
        col = tree.identify_column(event.x)
        if not row or col != "#1":
            return
        try:
            self._toggle_send_contact_pick(int(row))
        except Exception:
            return

    def _on_send_contacts_tree_double1(self, event: tk.Event) -> None:
        tree = self.send_contacts_tree
        if tree is None or tree.identify_region(event.x, event.y) != "cell":
            return
        if tree.identify_column(event.x) == "#1":
            return
        row = tree.identify_row(event.y)
        if not row:
            return
        try:
            self._toggle_send_contact_pick(int(row))
        except Exception:
            return

    def _refresh_send_contact_pick_marks(self) -> None:
        tree = self.send_contacts_tree
        if tree is None:
            return
        for row in tree.get_children():
            try:
                cid = int(row)
            except Exception:
                continue
            vals = list(tree.item(row, "values"))
            if not vals:
                continue
            vals[0] = "☑" if cid in self._send_contact_pick else "☐"
            tree.item(row, values=vals)

    def _check_all_send_contacts(self) -> None:
        if not self._send_contacts_cache:
            return
        self._send_contact_pick = {int(c["id"]) for c in self._send_contacts_cache}
        self._refresh_send_contact_pick_marks()

    def _uncheck_all_send_contacts(self) -> None:
        self._send_contact_pick.clear()
        self._refresh_send_contact_pick_marks()

    def _refresh_wa_contacts_tree(self) -> None:
        tree = self.wa_contacts_tree
        if tree is None:
            return
        p = self._current_local_profile()
        for row in tree.get_children():
            tree.delete(row)
        self._wa_contacts_cache = []
        self._wa_contact_pick.clear()
        if not p:
            return
        try:
            rows = fetch_whatsapp_directory(int(p["id"]))
        except Exception:
            return
        valid = {int(r["id"]) for r in rows if int(r.get("id", 0)) > 0}
        self._wa_contact_pick &= valid
        self._wa_contacts_cache = [{"id": int(r["id"]), "name": str(r.get("name", "")).strip()} for r in rows if r.get("name")]
        for c in self._wa_contacts_cache:
            mark = "☑" if c["id"] in self._wa_contact_pick else "☐"
            tree.insert("", tk.END, iid=str(c["id"]), values=(mark, c["name"]))

    def _toggle_wa_contact_pick(self, cid: int) -> None:
        tree = self.wa_contacts_tree
        if tree is None:
            return
        row = str(cid)
        if not tree.exists(row):
            return
        if cid in self._wa_contact_pick:
            self._wa_contact_pick.discard(cid)
            mark = "☐"
        else:
            self._wa_contact_pick.add(cid)
            mark = "☑"
        vals = list(tree.item(row, "values"))
        if vals:
            vals[0] = mark
            tree.item(row, values=vals)

    def _on_wa_contacts_tree_release(self, event: tk.Event) -> None:
        tree = self.wa_contacts_tree
        if tree is None or tree.identify_region(event.x, event.y) != "cell":
            return
        row = tree.identify_row(event.y)
        col = tree.identify_column(event.x)
        if not row or col != "#1":
            return
        try:
            self._toggle_wa_contact_pick(int(row))
        except Exception:
            return

    def _on_wa_contacts_tree_double1(self, event: tk.Event) -> None:
        tree = self.wa_contacts_tree
        if tree is None or tree.identify_region(event.x, event.y) != "cell":
            return
        if tree.identify_column(event.x) == "#1":
            return
        row = tree.identify_row(event.y)
        if not row:
            return
        try:
            self._toggle_wa_contact_pick(int(row))
        except Exception:
            return

    def _check_all_wa_contacts(self) -> None:
        if not self._wa_contacts_cache:
            return
        self._wa_contact_pick = {int(c["id"]) for c in self._wa_contacts_cache}
        tree = self.wa_contacts_tree
        if tree is None:
            return
        for row in tree.get_children():
            try:
                cid = int(row)
            except Exception:
                continue
            vals = list(tree.item(row, "values"))
            if vals:
                vals[0] = "☑"
                tree.item(row, values=vals)

    def _uncheck_all_wa_contacts(self) -> None:
        self._wa_contact_pick.clear()
        tree = self.wa_contacts_tree
        if tree is None:
            return
        for row in tree.get_children():
            vals = list(tree.item(row, "values"))
            if vals:
                vals[0] = "☐"
                tree.item(row, values=vals)

    def _sync_whatsapp_contacts_from_driver(self) -> None:
        p = self._current_local_profile()
        if not p:
            messagebox.showinfo("WhatsApp contacts", "Select a profile first.")
            return
        self.status_var.set("Reading contacts from WhatsApp (New chat)...")

        def work() -> None:
            state, open_err = self._ensure_local_profile_ready(
                profile_id=int(p["id"]),
                profile_phone=str(p["phone"]),
                profile_name=str(p.get("name", "")),
            )
            if state is None:
                self.root.after(
                    0,
                    lambda: messagebox.showerror("WhatsApp contacts", f"Could not open profile:\n{open_err}"),
                )
                self.root.after(0, lambda: self.status_var.set("Ready."))
                return
            driver = state.get_driver()
            if driver is None:
                self.root.after(0, lambda: messagebox.showerror("WhatsApp contacts", "Browser not available."))
                self.root.after(0, lambda: self.status_var.set("Ready."))
                return
            status, names = sync_whatsapp_contacts_from_new_chat(driver)
            if status != "SUCCESS":
                self.root.after(
                    0,
                    lambda s=status: messagebox.showwarning("WhatsApp contacts", s),
                )
                self.root.after(0, lambda: self.status_var.set("Contact sync finished with warnings."))
                return
            try:
                replace_whatsapp_directory(int(p["id"]), names)
            except Exception as e:
                self.root.after(
                    0,
                    lambda: messagebox.showerror("WhatsApp contacts", f"Could not save contacts:\n{e}"),
                )
                self.root.after(0, lambda: self.status_var.set("Ready."))
                return
            self.root.after(0, self._refresh_wa_contacts_tree)
            self.root.after(0, lambda: self._refresh_send_contacts())
            n = len(names)
            self.root.after(
                0,
                lambda: self.status_var.set(f"Saved {n} WhatsApp contact name(s). Open Send to message them."),
            )

        threading.Thread(target=work, daemon=True).start()

    def _clear_saved_whatsapp_directory(self) -> None:
        p = self._current_local_profile()
        if not p:
            messagebox.showinfo("WhatsApp contacts", "Select a profile first.")
            return
        if not messagebox.askyesno("Clear list", "Remove all saved WhatsApp contact names for this profile?"):
            return
        try:
            replace_whatsapp_directory(int(p["id"]), [])
        except Exception as e:
            messagebox.showerror("WhatsApp contacts", str(e))
            return
        self._refresh_wa_contacts_tree()
        self._refresh_send_contacts()
        self.status_var.set("WhatsApp contact list cleared.")

    def _goto_send_with_whatsapp_picks(self) -> None:
        self.send_target_var.set("wa_directory")
        self._refresh_send_contacts()
        self._send_contact_pick.clear()
        for wid in self._wa_contact_pick:
            self._send_contact_pick.add(_WA_SEND_ID_OFFSET + int(wid))
        self._refresh_send_contact_pick_marks()
        self._show_local_page("send")
        self.status_var.set("Send page: WhatsApp contacts (search by name). Compose your message and click Send.")

    def _load_templates(self, prefer_name: str | None = None) -> None:
        p = self._current_local_profile()
        if not p:
            return
        self._local_templates = fetch_templates(p["id"])
        names = [t["name"] for t in self._local_templates]
        send_values = [self.SEND_TEMPLATE_CUSTOM] + names
        self.local_template_cb["values"] = names
        if self.send_template_cb is not None:
            self.send_template_cb["values"] = send_values
        if names:
            cur_local = self.local_template_var.get()
            if prefer_name and prefer_name in names:
                sel_local = prefer_name
            elif cur_local in names:
                sel_local = cur_local
            else:
                sel_local = names[0]
            self.local_template_var.set(sel_local)
            self._on_local_template_pick()
            cur_send = self.send_template_var.get()
            if prefer_name and prefer_name in names:
                self.send_template_var.set(prefer_name)
                self._on_send_template_pick()
            elif cur_send in send_values:
                if cur_send != self.SEND_TEMPLATE_CUSTOM:
                    self._on_send_template_pick()
            else:
                self.send_template_var.set(self.SEND_TEMPLATE_CUSTOM)
        else:
            self.local_template_var.set("")
            self.template_name_entry.delete(0, tk.END)
            self.template_text.delete("1.0", tk.END)
            self.send_template_var.set(self.SEND_TEMPLATE_CUSTOM)

    def _on_local_template_pick(self) -> None:
        name = self.local_template_var.get()
        for t in getattr(self, "_local_templates", []):
            if t["name"] == name:
                self.template_name_entry.delete(0, tk.END)
                self.template_name_entry.insert(0, t["name"])
                self.template_text.delete("1.0", tk.END)
                self.template_text.insert("1.0", t["content"])
                break

    def _save_template(self) -> None:
        p = self._current_local_profile()
        if not p:
            return
        name = self.template_name_entry.get().strip()
        content = self.template_text.get("1.0", tk.END).strip()
        if not name:
            messagebox.showinfo("Template", "Enter template name.")
            return
        upsert_template(p["id"], name, content)
        self._load_templates(prefer_name=name)
        self._show_local_page("templates")
        self.status_var.set("Template saved. Next: go to Send Messages page.")

    def _load_groups(self) -> None:
        p = self._current_local_profile()
        if not p:
            return
        self._local_groups = fetch_groups(p["id"])
        names = [g["name"] for g in self._local_groups]
        self.local_group_cb["values"] = names
        if names and not self.local_group_var.get():
            self.local_group_var.set(names[0])

    def _add_group(self) -> None:
        p = self._current_local_profile()
        if not p:
            return
        g = simpledialog.askstring("Group", "Enter group name exactly as in WhatsApp:")
        if not g:
            return
        create_group(p["id"], g)
        self._load_groups()

    def _ensure_local_profile_ready(
        self,
        profile_id: int,
        profile_phone: str,
        profile_name: str | None = None,
    ) -> tuple[ProfileState | None, str]:
        phone = str(profile_phone or "").strip()
        if not phone:
            return None, "Missing profile phone."
        state = self.profile_by_phno.get(phone)
        if state is None:
            resolved_name = (profile_name or "").strip()
            if not resolved_name:
                for lp in self.local_profiles:
                    if str(lp.get("phone", "")).strip() == phone:
                        resolved_name = str(lp.get("name", "")).strip()
                        break
            if not resolved_name:
                resolved_name = phone
            state = ProfileState(client_idno=int(profile_id), client_name=resolved_name, client_phno=phone)
            self.profile_by_phno[phone] = state
        if state.get_driver() is not None:
            return state, ""
        result = self.scheduler.open_profile(state)
        if result != "SUCCESS":
            return None, result
        return state, ""

    def _open_local_profile(self) -> None:
        p = self._current_local_profile()
        if not p:
            messagebox.showinfo("Local mode", "Select profile first.")
            return
        state, err = self._ensure_local_profile_ready(
            profile_id=int(p["id"]),
            profile_phone=str(p["phone"]),
            profile_name=str(p.get("name", "")),
        )
        if state is None:
            self.status_var.set(f"Open failed: {err}")
            messagebox.showerror("Open profile", err)
            return
        self.status_var.set("Local profile opened.")

    def _parse_custom_vars(self) -> dict[str, str]:
        return {}

    def _refresh_local_logs_panel(self) -> None:
        txt = self.local_logs_panel_text
        p = self._current_local_profile()
        if txt is None:
            return
        if not p:
            txt.configure(state=tk.NORMAL)
            txt.delete("1.0", tk.END)
            txt.insert(tk.END, "Select a profile to view logs.\n")
            txt.configure(state=tk.DISABLED)
            return
        try:
            logs = fetch_local_logs(int(p["id"]), limit=80)
        except Exception as e:
            txt.configure(state=tk.NORMAL)
            txt.delete("1.0", tk.END)
            txt.insert(tk.END, f"Could not load logs:\n{e}\n")
            txt.configure(state=tk.DISABLED)
            return
        txt.configure(state=tk.NORMAL)
        txt.delete("1.0", tk.END)
        if not logs:
            txt.insert(tk.END, "No logs found for this profile yet.\n")
        else:
            for row in logs:
                txt.insert(
                    tk.END,
                    f'[{row["created_at"]}] [{row["status"]}] [{row["target_type"]}] {row["target_value"]}\n',
                )
        txt.configure(state=tk.DISABLED)

    def _render_template(self, template: str, contact: dict, custom_vars: dict[str, str]) -> str:
        vals = {
            "name": contact.get("name", ""),
            "phone": contact.get("phone", ""),
            "email": contact.get("email", ""),
            "company": contact.get("company", ""),
        }
        for k, v in contact.get("extra", {}).items():
            vals[str(k)] = str(v)
        vals.update(custom_vars)
        out = template
        keys = {fname for _, fname, _, _ in Formatter().parse(template) if fname}
        for key in keys:
            out = out.replace("{" + key + "}", str(vals.get(key, "")))
        return out

    def _ensure_local_send_worker(self, profile_phone: str) -> None:
        with self._local_send_lock:
            if profile_phone in self._local_send_workers_running:
                return
            self._local_send_workers_running.add(profile_phone)
            if profile_phone not in self._local_send_queues:
                self._local_send_queues[profile_phone] = queue.Queue()
        threading.Thread(target=self._local_send_worker_loop, args=(profile_phone,), daemon=True).start()

    def _local_send_worker_loop(self, profile_phone: str) -> None:
        while True:
            with self._local_send_lock:
                q = self._local_send_queues.get(profile_phone)
            if q is None:
                return
            try:
                job = q.get(timeout=1.0)
            except queue.Empty:
                with self._local_send_lock:
                    q2 = self._local_send_queues.get(profile_phone)
                    if q2 is q and q.empty():
                        self._local_send_workers_running.discard(profile_phone)
                        return
                continue
            try:
                self._run_local_send_job(job)
            finally:
                q.task_done()

    def _ensure_local_schedule_worker(self) -> None:
        with self._local_schedule_lock:
            if self._local_schedule_worker_running:
                return
            self._local_schedule_worker_running = True
        threading.Thread(target=self._local_schedule_worker_loop, daemon=True).start()

    def _local_schedule_worker_loop(self) -> None:
        while True:
            try:
                due_jobs = fetch_due_local_scheduled_jobs(datetime.now(), limit=40)
                for sched in due_jobs:
                    sid = int(sched.get("id", 0))
                    payload = sched.get("payload") or {}
                    if sid <= 0:
                        continue
                    profile_phone = str(payload.get("profile_phone", "")).strip()
                    if not profile_phone:
                        mark_local_scheduled_job_error(sid, "Missing profile phone in scheduled payload")
                        continue
                    mark_local_scheduled_job_dispatched(sid)
                    with self._local_send_lock:
                        if profile_phone not in self._local_send_queues:
                            self._local_send_queues[profile_phone] = queue.Queue()
                    self._local_send_queues[profile_phone].put(payload)
                    self._ensure_local_send_worker(profile_phone)
                if due_jobs and self.local_schedule_tree is not None:
                    try:
                        self.root.after(0, self._refresh_local_schedule_jobs)
                    except Exception:
                        pass
            except Exception as e:
                logger.error("Local schedule worker loop error: %s", e)
            time.sleep(2.0)

    def _run_local_send_job(self, job: dict[str, Any]) -> None:
        profile_phone = str(job["profile_phone"])
        profile_id = int(job["profile_id"])
        target_mode = str(job["target_mode"])
        allow_search = bool(job.get("allow_search", False))

        def emit_local(event_type: str, message: str) -> None:
            logger.info("[%s][%s] %s", profile_phone, event_type, message)

        state, open_err = self._ensure_local_profile_ready(
            profile_id=profile_id,
            profile_phone=profile_phone,
            profile_name=str(job.get("profile_name", "")),
        )
        if state is None:
            emit_local("queue_error", f"Skipped queued send for {profile_phone}: profile auto-open failed: {open_err}")
            for item in job.get("items", []):
                target_type = "group" if target_mode == "group" else "contact"
                target_value = str(item.get("receiver", ""))
                rendered = str(item.get("rendered", ""))
                try:
                    log_local_send(profile_id, target_type, target_value, rendered, "ERROR", f"Profile auto-open failed: {open_err}")
                except Exception:
                    pass
            return
        driver = state.get_driver()
        if driver is None:
            emit_local("queue_error", f"Skipped queued send for {profile_phone}: driver unavailable after open.")
            for item in job.get("items", []):
                target_type = "group" if target_mode == "group" else "contact"
                target_value = str(item.get("receiver", ""))
                rendered = str(item.get("rendered", ""))
                try:
                    log_local_send(profile_id, target_type, target_value, rendered, "ERROR", "Driver unavailable after opening profile")
                except Exception:
                    pass
            return

        att = [str(x) for x in (job.get("attachment_paths") or []) if x]
        att_kw = att or None
        attachment_only = bool(job.get("attachment_only_no_caption", False))

        if target_mode == "group":
            item = (job.get("items") or [{}])[0]
            receiver = str(item.get("receiver", "")).strip()
            rendered = str(item.get("rendered", ""))
            if not receiver:
                emit_local("group_send_error", "Group not selected.")
                return
            out_msg = "" if (attachment_only and att_kw) else rendered
            result = send_message(
                driver,
                receiver_identifier=receiver,
                message=out_msg,
                is_group=True,
                allow_search=allow_search,
                attachment_paths=att_kw,
            )
            if result == "SUCCESS":
                try:
                    log_local_send(profile_id, "group", receiver, out_msg, "SENT", "")
                except Exception as e:
                    emit_local("log_error", f"Group log write failed: {e}")
                emit_local("group_sent", f"Sent to group: {receiver}")
            else:
                try:
                    log_local_send(profile_id, "group", receiver, out_msg, "ERROR", result)
                except Exception as e:
                    emit_local("log_error", f"Group log write failed: {e}")
                emit_local("group_error", f"{receiver}: {result}")
            return

        for item in job.get("items", []):
            receiver = str(item.get("receiver", ""))
            rendered = str(item.get("rendered", ""))
            name = str(item.get("name", ""))
            out_msg = "" if (attachment_only and att_kw) else rendered
            try:
                result = send_message(
                    driver,
                    receiver_identifier=receiver,
                    message=out_msg,
                    is_group=False,
                    allow_search=allow_search,
                    attachment_paths=att_kw,
                )
                if result == "SUCCESS":
                    try:
                        log_local_send(profile_id, "contact", receiver, out_msg, "SENT", "")
                    except Exception as e:
                        emit_local("log_error", f"Contact log write failed: {e}")
                    emit_local("contact_sent", f"{name} ({receiver})")
                else:
                    try:
                        log_local_send(profile_id, "contact", receiver, out_msg, "ERROR", result)
                    except Exception as e:
                        emit_local("log_error", f"Contact log write failed: {e}")
                    emit_local("contact_error", f"{name} ({receiver}): {result}")
            except Exception as e:
                emit_local("contact_exception", f"{name} ({receiver}): {e}")

    def _refresh_local_attachments_label(self) -> None:
        n = len(self._pending_attachment_paths)
        if n == 0:
            self._local_attach_status_var.set("No attachments.")
            return
        names = [os.path.basename(p) for p in self._pending_attachment_paths[:3]]
        extra = f" (+{n - 3} more)" if n > 3 else ""
        self._local_attach_status_var.set(f"{n} file(s): {', '.join(names)}{extra}")

    def _pick_local_attachments(self) -> None:
        paths = filedialog.askopenfilenames(title="Attach files for the next send")
        if not paths:
            return
        self._pending_attachment_paths = [str(p) for p in paths]
        self._refresh_local_attachments_label()

    def _clear_local_attachments(self) -> None:
        self._pending_attachment_paths.clear()
        self._refresh_local_attachments_label()

    def _compose_preview_alive(self) -> bool:
        w = self._compose_preview_win
        if w is None:
            return False
        try:
            return bool(w.winfo_exists())
        except tk.TclError:
            return False

    def _format_file_size(self, path: str) -> str:
        try:
            n = os.path.getsize(path)
        except OSError:
            return ""
        if n >= 1024 * 1024:
            return f"{n / (1024 * 1024):.1f} MB"
        if n >= 1024:
            return f"{n / 1024:.0f} KB"
        return f"{n} B"

    def _open_compose_preview(self) -> None:
        if self._compose_preview_alive():
            self._compose_preview_win.deiconify()
            self._compose_preview_win.lift()
            self._compose_preview_refresh_content()
            return
        self._compose_preview_index = 0
        win = tk.Toplevel(self.root)
        self._compose_preview_win = win
        win.title("Compose message")
        win.configure(bg=_COMPOSE_BG)
        win.minsize(460, 520)
        win.transient(self.root)
        try:
            win.attributes("-topmost", False)
        except tk.TclError:
            pass

        header = tk.Frame(win, bg=_COMPOSE_BG, padx=12, pady=10)
        header.pack(fill=tk.X)

        def close_preview() -> None:
            self._compose_preview_close_save()

        tk.Button(
            header,
            text="✕",
            font=("Segoe UI", 12),
            bg=_COMPOSE_BG,
            fg=_COMPOSE_FG,
            activebackground=_COMPOSE_CARD,
            activeforeground=_COMPOSE_FG,
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            command=close_preview,
        ).pack(side=tk.LEFT)

        self._compose_title_var = tk.StringVar(value="Compose")
        tk.Label(
            header,
            textvariable=self._compose_title_var,
            bg=_COMPOSE_BG,
            fg=_COMPOSE_FG,
            font=("Segoe UI", 11),
            wraplength=360,
        ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=8)

        preview_wrap = tk.Frame(win, bg=_COMPOSE_BG, padx=16, pady=(0, 8))
        preview_wrap.pack(fill=tk.BOTH, expand=True)

        self._compose_preview_card = tk.Frame(preview_wrap, bg=_COMPOSE_CARD, highlightthickness=0)
        self._compose_preview_card.pack(fill=tk.BOTH, expand=True)

        cap_outer = tk.Frame(win, bg=_COMPOSE_BG, padx=16, pady=(0, 10))
        cap_outer.pack(fill=tk.X)

        cap_bar = tk.Frame(cap_outer, bg=_COMPOSE_CAPTION_BG, highlightthickness=1, highlightbackground="#4a5058")
        cap_bar.pack(fill=tk.X, ipady=4, ipadx=6)

        self._compose_caption_text = tk.Text(
            cap_bar,
            height=3,
            bg=_COMPOSE_CAPTION_BG,
            fg=_COMPOSE_FG,
            insertbackground=_COMPOSE_FG,
            relief=tk.FLAT,
            font=("Segoe UI", 11),
            wrap=tk.WORD,
            padx=8,
            pady=6,
        )
        self._compose_caption_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        if self.send_compose_text is not None:
            self._compose_caption_text.insert("1.0", self.send_compose_text.get("1.0", tk.END))

        cap_btns = tk.Frame(cap_bar, bg=_COMPOSE_CAPTION_BG)
        cap_btns.pack(side=tk.RIGHT, padx=(4, 6))

        def clear_caption() -> None:
            if self._compose_caption_text:
                self._compose_caption_text.delete("1.0", tk.END)

        tk.Button(
            cap_btns,
            text="✕",
            font=("Segoe UI", 10),
            bg=_COMPOSE_CAPTION_BG,
            fg=_COMPOSE_MUTED,
            activebackground=_COMPOSE_CAPTION_BG,
            relief=tk.FLAT,
            cursor="hand2",
            command=clear_caption,
        ).pack(side=tk.TOP, pady=(0, 4))

        def pick_emoji() -> None:
            if not self._compose_caption_text:
                return
            m = tk.Menu(win, tearoff=0, bg=_COMPOSE_CARD, fg=_COMPOSE_FG, activebackground=_COMPOSE_GREEN)
            for em in _COMPOSE_EMOJI_ROW:
                m.add_command(
                    label=em,
                    command=lambda e=em: self._compose_insert_emoji(e),
                )
            try:
                bx = cap_btns.winfo_rootx()
                by = cap_btns.winfo_rooty() + cap_btns.winfo_height()
                m.post(int(bx), int(by))
            except tk.TclError:
                pass

        tk.Button(
            cap_btns,
            text="😀",
            font=("Segoe UI", 14),
            bg=_COMPOSE_CAPTION_BG,
            fg=_COMPOSE_FG,
            activebackground=_COMPOSE_CAPTION_BG,
            relief=tk.FLAT,
            cursor="hand2",
            command=pick_emoji,
        ).pack(side=tk.TOP)

        thumb_row = tk.Frame(win, bg=_COMPOSE_BG, padx=16, pady=(0, 8))
        thumb_row.pack(fill=tk.X)
        self._compose_thumb_row = thumb_row

        bottom = tk.Frame(win, bg=_COMPOSE_BG, padx=16, pady=(0, 16))
        bottom.pack(fill=tk.X)

        def queue_sel() -> None:
            self._compose_preview_queue(selected_only=True)

        def queue_all() -> None:
            self._compose_preview_queue(selected_only=False)

        tk.Button(
            bottom,
            text="Queue — selected",
            bg=_COMPOSE_GREEN,
            fg="#ffffff",
            activebackground="#06cf9c",
            activeforeground="#ffffff",
            font=("Segoe UI", 10, "bold"),
            relief=tk.FLAT,
            padx=16,
            pady=10,
            cursor="hand2",
            command=queue_sel,
        ).pack(side=tk.LEFT)

        tk.Button(
            bottom,
            text="Queue — all",
            bg=_COMPOSE_CARD,
            fg=_COMPOSE_FG,
            activebackground="#3d4654",
            font=("Segoe UI", 10),
            relief=tk.FLAT,
            padx=14,
            pady=10,
            cursor="hand2",
            command=queue_all,
        ).pack(side=tk.LEFT, padx=(10, 0))

        win.protocol("WM_DELETE_WINDOW", close_preview)
        self._compose_preview_refresh_content()

    def _compose_insert_emoji(self, em: str) -> None:
        t = self._compose_caption_text
        if t is None:
            return
        try:
            t.insert(tk.INSERT, em)
            t.focus_set()
        except tk.TclError:
            pass

    def _compose_preview_sync_caption_to_main(self) -> None:
        if self._compose_caption_text is None or self.send_compose_text is None:
            return
        body = self._compose_caption_text.get("1.0", tk.END)
        self.send_compose_text.delete("1.0", tk.END)
        self.send_compose_text.insert("1.0", body)
        if self.schedule_compose_text is not None:
            self.schedule_compose_text.delete("1.0", tk.END)
            self.schedule_compose_text.insert("1.0", body)

    def _compose_preview_close_save(self) -> None:
        self._compose_preview_sync_caption_to_main()
        if self._compose_preview_win is not None:
            try:
                self._compose_preview_win.destroy()
            except tk.TclError:
                pass
        self._compose_preview_win = None
        self._compose_caption_text = None

    def _compose_preview_queue(self, selected_only: bool) -> None:
        self._compose_preview_sync_caption_to_main()
        if self._compose_preview_win is not None:
            try:
                self._compose_preview_win.destroy()
            except tk.TclError:
                pass
        self._compose_preview_win = None
        self._compose_caption_text = None
        self._dispatch_local_send_or_schedule(selected_only=selected_only)

    def _compose_preview_refresh_content(self) -> None:
        if not self._compose_preview_alive():
            return
        paths = [p for p in self._pending_attachment_paths if (p or "").strip() and os.path.isfile(p)]
        if self._compose_preview_index >= len(paths) and paths:
            self._compose_preview_index = len(paths) - 1
        if self._compose_preview_index < 0:
            self._compose_preview_index = 0

        if paths:
            name = os.path.basename(paths[self._compose_preview_index])
            self._compose_title_var.set(name if len(name) < 56 else name[:53] + "…")
        else:
            self._compose_title_var.set("Compose message")

        for w in self._compose_preview_card.winfo_children():
            w.destroy()
        self._compose_preview_photo_ref = None

        if not paths:
            inner = tk.Frame(self._compose_preview_card, bg=_COMPOSE_CARD)
            inner.pack(expand=True, fill=tk.BOTH, padx=24, pady=48)
            tk.Label(
                inner,
                text="No attachment yet",
                bg=_COMPOSE_CARD,
                fg=_COMPOSE_MUTED,
                font=("Segoe UI", 12),
            ).pack(pady=(0, 8))
            tk.Label(
                inner,
                text="Add files with + below or use Attach files on the main page.",
                bg=_COMPOSE_CARD,
                fg=_COMPOSE_MUTED,
                font=("Segoe UI", 10),
                wraplength=400,
                justify=tk.CENTER,
            ).pack()
        else:
            path = paths[self._compose_preview_index]
            ext = os.path.splitext(path)[1].lower()
            inner = tk.Frame(self._compose_preview_card, bg=_COMPOSE_CARD)
            inner.pack(expand=True, fill=tk.BOTH, padx=16, pady=24)

            shown = False
            if _HAS_PIL and Image is not None and ImageTk is not None and ext in _IMAGE_PREVIEW_EXT:
                try:
                    img = Image.open(path)
                    _resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.LANCZOS)
                    img.thumbnail((440, 300), _resample)
                    self._compose_preview_photo_ref = ImageTk.PhotoImage(img)
                    tk.Label(inner, image=self._compose_preview_photo_ref, bg=_COMPOSE_CARD).pack(pady=(0, 8))
                    shown = True
                except Exception:
                    shown = False

            if not shown:
                tk.Label(inner, text="📄", font=("Segoe UI", 56), bg=_COMPOSE_CARD, fg=_COMPOSE_MUTED).pack(pady=(12, 8))
                tk.Label(
                    inner,
                    text="No preview available" if ext == ".pdf" or ext not in _IMAGE_PREVIEW_EXT else "Preview not available",
                    bg=_COMPOSE_CARD,
                    fg=_COMPOSE_FG,
                    font=("Segoe UI", 12),
                ).pack()
                sz = self._format_file_size(path)
                kind = ext.upper().lstrip(".") or "file"
                tk.Label(
                    inner,
                    text=f"{sz} — {kind}" if sz else kind,
                    bg=_COMPOSE_CARD,
                    fg=_COMPOSE_MUTED,
                    font=("Segoe UI", 10),
                ).pack(pady=(4, 0))

        for w in self._compose_thumb_row.winfo_children():
            w.destroy()

        def select_idx(i: int) -> None:
            self._compose_preview_index = i
            self._compose_preview_refresh_content()

        for i, pth in enumerate(paths):
            ext = os.path.splitext(pth)[1].lower()
            box = tk.Frame(
                self._compose_thumb_row,
                bg=_COMPOSE_BG,
                highlightthickness=2,
                highlightbackground=_COMPOSE_THUMB_SEL if i == self._compose_preview_index else _COMPOSE_CARD,
                cursor="hand2",
            )
            box.pack(side=tk.LEFT, padx=(0, 8), pady=4)
            box.bind("<Button-1>", lambda _e, ii=i: select_idx(ii))

            if _HAS_PIL and Image is not None and ImageTk is not None and ext in _IMAGE_PREVIEW_EXT:
                try:
                    img = Image.open(pth)
                    _resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.LANCZOS)
                    img.thumbnail((52, 52), _resample)
                    ph = ImageTk.PhotoImage(img)
                    lbl = tk.Label(box, image=ph, bg=_COMPOSE_CARD)
                    lbl.image = ph  # keep ref
                    lbl.pack()
                    lbl.bind("<Button-1>", lambda _e, ii=i: select_idx(ii))
                except Exception:
                    tk.Label(box, text=os.path.splitext(pth)[1][1:].upper()[:4] or "?", bg=_COMPOSE_CARD, fg=_COMPOSE_FG, font=("Segoe UI", 8)).pack(
                        ipadx=8, ipady=16
                    )
            else:
                tk.Label(
                    box,
                    text="PDF" if ext == ".pdf" else (ext[1:4].upper() if ext else "?"),
                    bg=_COMPOSE_CARD,
                    fg=_COMPOSE_FG,
                    font=("Segoe UI", 8, "bold"),
                    width=6,
                ).pack(ipadx=4, ipady=14)

            for child in box.winfo_children():
                child.bind("<Button-1>", lambda _e, ii=i: select_idx(ii))

        add_fr = tk.Frame(self._compose_thumb_row, bg=_COMPOSE_BG, highlightthickness=1, highlightbackground=_COMPOSE_MUTED, cursor="hand2")
        add_fr.pack(side=tk.LEFT, padx=(4, 0))

        def do_append(_e=None) -> None:
            paths_add = filedialog.askopenfilenames(title="Add files")
            if not paths_add:
                return
            for p in paths_add:
                ap = os.path.abspath(os.path.normpath(str(p)))
                if os.path.isfile(ap) and ap not in self._pending_attachment_paths:
                    self._pending_attachment_paths.append(ap)
            self._refresh_local_attachments_label()
            self._compose_preview_refresh_content()

        tk.Label(add_fr, text="+", bg=_COMPOSE_BG, fg=_COMPOSE_FG, font=("Segoe UI", 18)).pack(ipadx=10, ipady=8)
        add_fr.bind("<Button-1>", do_append)

    def _send_local(self, selected_only: bool) -> None:
        p = self._current_local_profile()
        if not p:
            messagebox.showinfo("Local mode", "Select profile first.")
            return
        template = self._outgoing_message_body()
        attachment_only = self.attachment_only_no_caption_var.get()
        attachment_snapshot = [
            os.path.abspath(os.path.normpath(p))
            for p in self._pending_attachment_paths
            if (p or "").strip() and os.path.isfile(p)
        ]
        err = self._validate_outgoing_local(template, attachment_snapshot, attachment_only)
        if err:
            messagebox.showinfo("Local mode", err)
            return

        ph = str(p["phone"])
        self.status_var.set("Opening profile if needed...")
        state, open_err = self._ensure_local_profile_ready(
            profile_id=int(p["id"]),
            profile_phone=ph,
            profile_name=str(p.get("name", "")),
        )
        if state is None:
            self.status_var.set(f"Open failed: {open_err}")
            messagebox.showerror("Local mode", f"Could not open profile automatically:\n{open_err}")
            return
        custom_vars = self._parse_custom_vars()
        target_mode = self.send_target_var.get()
        selected_ids = (
            set(self._send_contact_pick)
            if self._send_contact_pick
            else {int(x) for x in (self.send_contacts_tree.selection() if self.send_contacts_tree is not None else ())}
        )

        if target_mode == "contacts":
            if selected_only and not selected_ids:
                messagebox.showinfo(
                    "Local mode",
                    "Pick at least one recipient in Send Messages page, or use Send All.",
                )
                return
            if not self._send_contacts_cache:
                messagebox.showinfo("Local mode", "No contacts available for this profile.")
                return
        if target_mode == "wa_directory":
            try:
                wa_rows = fetch_whatsapp_directory(int(p["id"]))
            except Exception as e:
                messagebox.showerror("Local mode", f"Could not load WhatsApp contacts:\n{e}")
                return
            if not wa_rows:
                messagebox.showinfo(
                    "Local mode",
                    "No WhatsApp contacts saved for this profile. Open “Your WhatsApp contacts” and click Load from WhatsApp.",
                )
                return
            if selected_only:
                pick = set(self._send_contact_pick)
                for wid in self._wa_contact_pick:
                    pick.add(_WA_SEND_ID_OFFSET + int(wid))
                if not pick:
                    pick = {int(x) for x in (self.send_contacts_tree.selection() if self.send_contacts_tree else ())}
                if not pick and self.wa_contacts_tree:
                    pick = {_WA_SEND_ID_OFFSET + int(x) for x in self.wa_contacts_tree.selection()}
                if not pick:
                    messagebox.showinfo(
                        "Local mode",
                        "Pick at least one WhatsApp contact, or use Send All.",
                    )
                    return

        queue_key = str(p["phone"])
        items: list[dict[str, str]] = []
        if target_mode == "group":
            group = self.local_group_var.get().strip()
            if not group:
                messagebox.showinfo("Local mode", "Select a group first.")
                return
            items.append({"receiver": group, "name": group, "rendered": self._render_template(template, {}, custom_vars)})
        elif target_mode == "wa_directory":
            items = self._collect_local_send_items(
                selected_only=selected_only,
                target_mode="wa_directory",
                template=template,
                custom_vars=custom_vars,
            )
            if not items:
                return
        else:
            contacts = self._send_contacts_cache[:]
            if selected_only:
                contacts = [c for c in contacts if c["id"] in selected_ids]
            for c in contacts:
                items.append(
                    {
                        "receiver": str(c.get("phone", "")),
                        "name": str(c.get("name", "")),
                        "rendered": self._render_template(template, c, custom_vars),
                    }
                )
        job: dict[str, Any] = {
            "profile_id": int(p["id"]),
            "profile_phone": queue_key,
            "profile_name": str(p.get("name", "")),
            "target_mode": target_mode,
            "allow_search": True if target_mode == "wa_directory" else self.allow_search_var.get(),
            "items": items,
            "attachment_paths": list(attachment_snapshot),
            "attachment_only_no_caption": bool(attachment_only),
        }
        if queue_key not in self._local_send_queues:
            self._local_send_queues[queue_key] = queue.Queue()
        self._local_send_queues[queue_key].put(job)
        self._ensure_local_send_worker(queue_key)
        self._pending_attachment_paths.clear()
        self._refresh_local_attachments_label()
        pending = self._local_send_queues[queue_key].qsize()
        self.status_var.set(f"Queued {len(items)} target(s). Pending jobs for this profile: {pending}.")

    def _show_local_logs(self) -> None:
        p = self._current_local_profile()
        if not p:
            return
        if self._local_logs_window is not None and self._local_logs_window.winfo_exists():
            self._local_logs_profile_id = p["id"]
            self._local_logs_window.deiconify()
            self._local_logs_window.lift()
            self._refresh_local_logs_window()
            return

        win = tk.Toplevel(self.root)
        win.title("Local send logs")
        win.geometry("900x420")
        txt = scrolledtext.ScrolledText(win, wrap=tk.WORD)
        txt.pack(fill=tk.BOTH, expand=True)

        self._local_logs_window = win
        self._local_logs_text = txt
        self._local_logs_profile_id = p["id"]

        def _on_close() -> None:
            self._local_logs_window = None
            self._local_logs_text = None
            self._local_logs_profile_id = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_close)
        self._refresh_local_logs_window()

    def _refresh_local_logs_window(self) -> None:
        if self._local_logs_window is None or not self._local_logs_window.winfo_exists():
            return
        if self._local_logs_text is None or self._local_logs_profile_id is None:
            return
        try:
            logs = fetch_local_logs(self._local_logs_profile_id, limit=200)
        except Exception as e:
            self._local_logs_text.configure(state=tk.NORMAL)
            self._local_logs_text.delete("1.0", tk.END)
            self._local_logs_text.insert(tk.END, f"Could not load local logs:\n{e}\n")
            self._local_logs_text.configure(state=tk.DISABLED)
            self._local_logs_window.after(2500, self._refresh_local_logs_window)
            return

        self._local_logs_text.configure(state=tk.NORMAL)
        self._local_logs_text.delete("1.0", tk.END)
        if not logs:
            self._local_logs_text.insert(tk.END, "No logs found for this profile yet.\n")
        else:
            for row in logs:
                self._local_logs_text.insert(
                    tk.END,
                    f'[{row["created_at"]}] [{row["status"]}] [{row["target_type"]}] {row["target_value"]}\n{row["error_text"]}\n{row["rendered_message"]}\n\n',
                )
        self._local_logs_text.configure(state=tk.DISABLED)
        self._local_logs_window.after(2000, self._refresh_local_logs_window)

    def _delete_local_logs(self) -> None:
        p = self._current_local_profile()
        if not p:
            return
        if not messagebox.askyesno("Delete logs", "Delete all local send logs for selected profile?"):
            return
        delete_local_logs(p["id"])
        self.status_var.set("Local logs deleted.")
        self._refresh_local_logs_panel()

    # shared ui/runtime logging
    def _show_how_to_use(self) -> None:
        if self._how_to_window is not None and self._how_to_window.winfo_exists():
            self._how_to_window.deiconify()
            self._how_to_window.lift()
            return

        win = tk.Toplevel(self.root)
        self._how_to_window = win
        win.title("How To Use — WhatsApp Desktop")
        win.geometry("760x640")
        win.minsize(520, 400)

        body = ttk.Frame(win, padding=10)
        body.pack(fill=tk.BOTH, expand=True)
        txt = scrolledtext.ScrolledText(body, wrap=tk.WORD, font=("Segoe UI", 10), height=28, padx=8, pady=8)
        txt.pack(fill=tk.BOTH, expand=True)

        local_section = """LOCAL MODE — STEP BY STEP
================================================================================
NAVIGATION
   • Use the left sidebar pages in order: 1) Profiles, 2) Contacts & Lists, 3) Templates, 4) Send Messages, 5) Logs.
   • You can switch pages anytime; your selections and unsent edits stay in memory while the app is open.

1) SAVE A PROFILE
   • In the “New profile” row, type a display name and the WhatsApp phone number (country code included, digits only is best).
   • Click “Save profile”.
   • The profile appears in the Profile dropdown at the top.
   • “Delete profile” removes that profile and all of its contact lists, contacts, templates, groups, and send logs (cannot be undone). Close Chrome/WhatsApp for that number first if it is open.

2) LOG IN WITH THAT PROFILE (WHATSAPP WEB)
   • Select your profile in the Profile dropdown.
   • Click “Open Profile”. Chrome opens WhatsApp Web.
   • The first time, scan the QR code with your phone. Leave that Chrome window open while you send messages.

3) CREATE A CONTACT LIST (FIELDS FIRST, THEN DATA)
   • Click “Add List…”.
   • Enter a list name (for example “Customers April”).
   • name and phone are always stored for every contact in that list.
   • “Extra column names” (comma-separated): add headers you want for this list, e.g. email, company, city. Leave that box empty if you only need name + phone. The dialog defaults to email, company — edit or clear as you like.
   • Click “Create list”, then pick the list in the “Contact List” dropdown.
   • After the list exists, add people with “Add contact…” (one row at a time) and/or “Import Contacts CSV” (bulk). Both add to the same saved list.

4) CSV FORMAT (REQUIRED FOR IMPORT)
   • Row 1 must be column headers (not data).
   • Mandatory columns: name and phone — spelling can be any case (Name, PHONE, etc.).
   • Optional: email, company, plus any extra headers. Extra headers are stored per contact; in templates use placeholders that match the header text, e.g. {city} for a column named city.
   • One contact per row. Rows with both name and phone empty are skipped.
   • Save as UTF-8 CSV; files with a UTF-8 BOM are fine.

5) ADD CONTACTS TO THE CURRENT LIST
   • “Add contact…” opens a form with one field per column you defined for this list (always at least name and phone). Fill and save; repeat to add more numbers to the same list.
   • “Import Contacts CSV” loads many rows at once into the list you have selected (same CSV rules as step 4).

6) CHOOSE CONTACTS WITH THE MOUSE (WHO GETS “SEND SELECTED”)
   • Pick column: click the box in the first column once to check (☑) or uncheck (☐) a row.
   • Double-click on Name, Phone, Email, or any other data column (not the Pick column) to toggle that row’s check in one go — easier than tiny clicks.
   • “Check all” / “Uncheck all” selects or clears everyone in the current list quickly.
   • Classic multi-select still works: if no row is checked, “Send Selected” uses the highlighted rows (Ctrl+click to add, Shift+click for a range).

7) MANAGE LISTS AND TEMPLATES
   • “Rename list” / “Delete list”: apply to the contact list currently selected in the dropdown. Delete list removes all contacts in that list.
   • Templates: use the dropdown to select a template. “Save Template” saves the name + body from the fields on screen (creates or updates). “Rename” only changes the template’s name. “Delete” removes that template.

8) TEMPLATES — WRITE AND SAVE
   • Pick a template from the dropdown to load it, or type a new name in the name field.
   • Edit the message in the large text area. Use placeholders that match your data, for example:
     Hi {name}, from {company} in {city}
   • Click “Save Template” to store it for this profile.

9) GROUPS (OPTIONAL)
   • Click “Add Group” and type the group name exactly as it appears in WhatsApp.
   • Choose “Send to Group”, pick the group, then send (template is sent once to that group chat).

10) SEND MESSAGES
   • “Attach files…” (above the send row): optional files for the next queued send. Paths are copied when you click Send; the same files go to each recipient in that batch. Clear with “Clear attachments”. Images/videos use the media picker; other types use the document picker when possible.
   • “Send to Contacts”: uses your contact list.
   • “Send Selected”: sends only checked rows (Pick column), or only highlighted rows if nothing is checked.
   • “Send All”: sends to every contact in the current list.
   • “Send to Group”: uses the selected group instead of individual numbers.

11) SCHEDULING
   • Open the “Scheduling” page in the left sidebar.
   • Set “Run at” in this format: YYYY-MM-DD HH:MM.
   • Choose Contacts or Group, then click “Schedule Selected” or “Schedule All”.
   • Keep the app running; scheduled jobs are dispatched automatically when due.
   • For successful auto-send, keep that profile opened/logged in on WhatsApp Web.

12) LOGS
   • “View Logs” shows send history for the profile you have selected.
   • “Delete Logs” clears that history (does not delete contacts).
"""
        sql_section = """SQL MODE — STEP BY STEP
================================================================================
1) Put a .env file next to the app (or bundle it when building) with SQL Server settings. Install “ODBC Driver 18 for SQL Server” on the PC.

2) Open the SQL Mode tab. Click “Refresh SQL List” to load clients from your database.

3) Select one client row, then “Open Profile” and complete QR login the first time.

4) Click “Start” to run the automatic scheduler (it checks the database about every 15 seconds for pending messages).

5) Use “Pause”, “Resume”, or “Stop” for that profile. “Pause All” / “Resume All” control every running SQL profile.

6) Optional: turn on “Allow side search for phone numbers” if direct chat links are not enough for your setup.
"""
        if self.run_mode == "local":
            guide = (
                "HOW TO USE\n\n"
                "This guide is for the currently opened workspace.\n\n"
                "================================================================================\n"
                + local_section
                + "\n================================================================================\n"
                "REQUIREMENTS (SHORT)\n"
                "================================================================================\n"
                "• Google Chrome must be installed.\n"
                "• Microsoft Access Database Engine (ACE) is required for local_store.accdb.\n\n"
                "Close this window when you are done reading.\n"
            )
        elif self.run_mode == "sql":
            guide = (
                "HOW TO USE\n\n"
                "This guide is for the currently opened workspace.\n\n"
                "================================================================================\n"
                + sql_section
                + "\n================================================================================\n"
                "REQUIREMENTS (SHORT)\n"
                "================================================================================\n"
                "• Google Chrome must be installed.\n"
                "• ODBC Driver 18 for SQL Server and a valid .env are required.\n\n"
                "Close this window when you are done reading.\n"
            )
        else:
            guide = (
                "HOW TO USE (HYBRID)\n\n"
                "This guide includes both tabs visible in this run.\n\n"
                "================================================================================\n"
                + local_section
                + "\n================================================================================\n"
                + sql_section
                + "\n================================================================================\n"
                "REQUIREMENTS (SHORT)\n"
                "================================================================================\n"
                "• Google Chrome must be installed.\n"
                "• Microsoft Access Database Engine (ACE) is required for local_store.accdb.\n"
                "• ODBC Driver 18 for SQL Server and a valid .env are required.\n\n"
                "Close this window when you are done reading.\n"
            )
        txt.insert(tk.END, guide)
        txt.configure(state=tk.DISABLED)

        def _on_close() -> None:
            self._how_to_window = None
            win.destroy()

        foot = ttk.Frame(win, padding=(10, 0, 10, 10))
        foot.pack(fill=tk.X)
        ttk.Button(foot, text="Close", command=_on_close).pack(side=tk.RIGHT)

        win.protocol("WM_DELETE_WINDOW", _on_close)

    def _enqueue_log(self, client_phno: str, event_type: str, message: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_queue.put((ts, client_phno, event_type, message))

    def _append_log(self, client_phno: str, event_type: str, message: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{client_phno}] [{event_type}] {message}\n"
        if self.sql_runtime_logs_text is None:
            return
        self.sql_runtime_logs_text.configure(state=tk.NORMAL)
        self.sql_runtime_logs_text.insert(tk.END, line)
        self.sql_runtime_logs_text.see(tk.END)
        self.sql_runtime_logs_text.configure(state=tk.DISABLED)

    def _on_tab_changed(self, _event: tk.Event | None = None) -> None:
        if self.sql_runtime_logs_frame is None:
            return
        if self.run_mode == "sql":
            self.sql_runtime_logs_frame.pack(fill=tk.BOTH, expand=False, padx=10, pady=(0, 10))
            return
        try:
            tab_text = self.tabs.tab(self.tabs.select(), "text")
        except Exception:
            tab_text = ""
        if tab_text == "SQL Mode":
            self.sql_runtime_logs_frame.pack(fill=tk.BOTH, expand=False, padx=10, pady=(0, 10))
        else:
            self.sql_runtime_logs_frame.pack_forget()

    def _drain_log_queue(self) -> None:
        try:
            while True:
                ts, client_phno, event_type, message = self.log_queue.get_nowait()
                if self.sql_runtime_logs_text is not None:
                    line = f"[{ts}] [{client_phno}] [{event_type}] {message}\n"
                    self.sql_runtime_logs_text.configure(state=tk.NORMAL)
                    self.sql_runtime_logs_text.insert(tk.END, line)
                    self.sql_runtime_logs_text.see(tk.END)
                    self.sql_runtime_logs_text.configure(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.root.after(500, self._drain_log_queue)

    def run(self) -> None:
        self._refresh_statuses()
        self._drain_log_queue()
        self._schedule_local_logs_panel_refresh()
        self.root.mainloop()

    def _schedule_local_logs_panel_refresh(self) -> None:
        try:
            self._refresh_local_logs_panel()
        finally:
            self.root.after(2000, self._schedule_local_logs_panel_refresh)
