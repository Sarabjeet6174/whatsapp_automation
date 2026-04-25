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
import tkinter as tk
from datetime import datetime
from string import Formatter
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

from app.core.message_loop import SCHEDULER_INTERVAL
from app.core.profile_state import ProfileState
from app.core.scheduler import Scheduler
from app.db.local_access import (
    create_contact,
    create_contact_list,
    create_group,
    create_local_profile,
    delete_contact_list,
    delete_contacts,
    delete_local_logs,
    delete_local_profile,
    delete_template,
    fetch_contact_lists,
    fetch_contacts,
    fetch_groups,
    fetch_local_logs,
    fetch_local_profiles,
    fetch_templates,
    init_local_db,
    log_local_send,
    rename_contact_list,
    rename_template,
    update_contact_list_fields,
    upsert_template,
)
from app.db.sql import fetch_clients
from app.whatsapp.sender import send_message
from config import allow_search_from_env

logger = logging.getLogger(__name__)


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
        self.local_template_var = tk.StringVar()
        self.local_group_var = tk.StringVar()
        self.send_target_var = tk.StringVar(value="contacts")
        self._local_contacts_cache: list[dict] = []
        self._local_contact_pick: set[int] = set()
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
        style.configure("Title.TLabel", font=("Segoe UI", 12, "bold"), background="#eef2ff", foreground="#1f2937")
        style.configure("Hint.TLabel", font=("Segoe UI", 9), background="#eef2ff", foreground="#4b5563")
        style.configure("Primary.TButton", font=("Segoe UI", 9, "bold"))
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

        row1 = ttk.Frame(wrap, style="Card.TFrame")
        row1.pack(fill=tk.X)
        ttk.Label(row1, text="Profile").pack(side=tk.LEFT)
        self.local_profile_cb = ttk.Combobox(row1, textvariable=self.local_profile_var, state="readonly", width=32)
        self.local_profile_cb.pack(side=tk.LEFT, padx=6)
        self.local_profile_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_local_profile_selected())
        ttk.Button(row1, text="Delete profile", command=self._delete_local_profile).pack(side=tk.LEFT, padx=4)

        ttk.Label(row1, text="Contact List").pack(side=tk.LEFT, padx=(14, 0))
        self.local_list_cb = ttk.Combobox(row1, textvariable=self.local_list_var, state="readonly", width=26)
        self.local_list_cb.pack(side=tk.LEFT, padx=6)
        self.local_list_cb.bind("<<ComboboxSelected>>", lambda _e: self._refresh_local_contacts())
        ttk.Button(row1, text="Add List…", command=self._open_new_contact_list_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Button(row1, text="Rename list", command=self._rename_local_list).pack(side=tk.LEFT, padx=2)
        ttk.Button(row1, text="Delete list", command=self._delete_local_list).pack(side=tk.LEFT, padx=2)

        profile_new = ttk.Frame(wrap, style="Card.TFrame")
        profile_new.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(profile_new, text="New profile", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        ttk.Label(profile_new, text="Name").pack(side=tk.LEFT, padx=(8, 2))
        self.new_local_profile_name = ttk.Entry(profile_new, width=22)
        self.new_local_profile_name.pack(side=tk.LEFT, padx=2)
        ttk.Label(profile_new, text="WhatsApp #").pack(side=tk.LEFT, padx=(10, 2))
        self.new_local_profile_phone = ttk.Entry(profile_new, width=18)
        self.new_local_profile_phone.pack(side=tk.LEFT, padx=2)
        ttk.Button(profile_new, text="Save profile", command=self._add_local_profile, style="Primary.TButton").pack(
            side=tk.LEFT, padx=8
        )

        mid = ttk.PanedWindow(wrap, orient=tk.HORIZONTAL)
        mid.pack(fill=tk.BOTH, expand=True, pady=(8, 8))

        left = ttk.Frame(mid, style="Card.TFrame")
        right = ttk.Frame(mid, style="Card.TFrame")
        mid.add(left, weight=3)
        mid.add(right, weight=2)

        ttk.Label(left, text="Contacts", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W)
        ttk.Label(
            left,
            text="Click the Pick column to check/uncheck a row, or double-click Name/Phone/etc. to toggle. "
            "Check all / Uncheck all for bulk. If nothing is checked, Send Selected uses highlighted rows "
            "(Ctrl+click or Shift+click).",
            font=("Segoe UI", 8),
            foreground="#4b5563",
            wraplength=520,
        ).pack(anchor=tk.W, pady=(0, 2))
        self.local_contacts_tree = ttk.Treeview(
            left,
            columns=("pick", "name", "phone", "email", "company"),
            show="headings",
            height=13,
            selectmode="extended",
        )
        self.local_contacts_tree.heading("pick", text="Pick")
        self.local_contacts_tree.column("pick", width=44, stretch=False, anchor=tk.CENTER)
        for c, w in (("name", 160), ("phone", 120), ("email", 170), ("company", 120)):
            self.local_contacts_tree.heading(c, text=c.capitalize())
            self.local_contacts_tree.column(c, width=w)
        self.local_contacts_tree.pack(fill=tk.BOTH, expand=True, pady=(4, 6))
        self.local_contacts_tree.bind("<ButtonRelease-1>", self._on_local_contacts_tree_release, add=True)
        self.local_contacts_tree.bind("<Double-1>", self._on_local_contacts_tree_double1)
        cbtn = ttk.Frame(left, style="Card.TFrame")
        cbtn.pack(fill=tk.X)
        ttk.Button(cbtn, text="Add contact…", command=self._open_add_contact_dialog).pack(side=tk.LEFT, padx=2)
        ttk.Button(cbtn, text="Import Contacts CSV", command=self._import_contacts_csv).pack(side=tk.LEFT, padx=2)
        ttk.Button(cbtn, text="Check all", command=self._check_all_local_contacts).pack(side=tk.LEFT, padx=2)
        ttk.Button(cbtn, text="Uncheck all", command=self._uncheck_all_local_contacts).pack(side=tk.LEFT, padx=2)
        ttk.Button(cbtn, text="Delete Selected", command=self._delete_selected_contacts).pack(side=tk.LEFT, padx=2)

        ttk.Label(right, text="Template / Variables", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W)
        top_t = ttk.Frame(right, style="Card.TFrame")
        top_t.pack(fill=tk.X, pady=(4, 4))
        self.local_template_cb = ttk.Combobox(top_t, textvariable=self.local_template_var, state="readonly", width=20)
        self.local_template_cb.pack(side=tk.LEFT, padx=(0, 4))
        self.local_template_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_local_template_pick())
        ttk.Button(top_t, text="Save Template", command=self._save_template).pack(side=tk.LEFT, padx=2)
        ttk.Button(top_t, text="Rename", command=self._rename_template).pack(side=tk.LEFT, padx=2)
        ttk.Button(top_t, text="Delete", command=self._delete_template).pack(side=tk.LEFT, padx=2)

        self.template_name_entry = ttk.Entry(right)
        self.template_name_entry.pack(fill=tk.X, pady=(0, 4))
        self.template_name_entry.insert(0, "template_name")

        self.template_text = scrolledtext.ScrolledText(right, height=7, wrap=tk.WORD)
        self.template_text.pack(fill=tk.BOTH, expand=False)
        self.template_text.insert(tk.END, "Hi {name}, your company is {company}.")

        logs_head = ttk.Frame(right, style="Card.TFrame")
        logs_head.pack(fill=tk.X, pady=(6, 2))
        ttk.Label(logs_head, text="Logs", font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
        ttk.Button(logs_head, text="Refresh", command=self._refresh_local_logs_panel).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(logs_head, text="Delete Logs", command=self._delete_local_logs).pack(side=tk.RIGHT)
        self.local_logs_panel_text = scrolledtext.ScrolledText(right, height=9, wrap=tk.WORD, state=tk.DISABLED)
        self.local_logs_panel_text.configure(
            bg="#0b1220",
            fg="#d1e4ff",
            insertbackground="#ffffff",
            font=("Consolas", 9),
        )
        self.local_logs_panel_text.pack(fill=tk.BOTH, expand=True)

        bottom = ttk.Frame(wrap, style="Card.TFrame")
        bottom.pack(fill=tk.X)
        ttk.Radiobutton(bottom, text="Send to Contacts", variable=self.send_target_var, value="contacts").pack(side=tk.LEFT)
        ttk.Radiobutton(bottom, text="Send to Group", variable=self.send_target_var, value="group").pack(side=tk.LEFT, padx=8)
        ttk.Label(bottom, text="Group").pack(side=tk.LEFT, padx=(8, 2))
        self.local_group_cb = ttk.Combobox(bottom, textvariable=self.local_group_var, state="readonly", width=26)
        self.local_group_cb.pack(side=tk.LEFT, padx=4)
        ttk.Button(bottom, text="Add Group", command=self._add_group).pack(side=tk.LEFT, padx=2)
        ttk.Button(bottom, text="Open Profile", command=self._open_local_profile).pack(side=tk.LEFT, padx=(10, 2))
        ttk.Button(bottom, text="Send Selected", command=lambda: self._send_local(selected_only=True), style="Primary.TButton").pack(side=tk.LEFT, padx=2)
        ttk.Button(bottom, text="Send All", command=lambda: self._send_local(selected_only=False)).pack(side=tk.LEFT, padx=2)

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
        self._refresh_local_logs_panel()

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
            self.status_var.set("Local profile created.")
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
        if names:
            cur = self.local_list_var.get()
            if cur in names:
                sel = cur
            elif prefer_list_name and prefer_list_name in names:
                sel = prefer_list_name
            else:
                sel = names[0]
            self.local_list_var.set(sel)
            self._refresh_local_contacts()
        else:
            self.local_list_var.set("")
            for item in self.local_contacts_tree.get_children():
                self.local_contacts_tree.delete(item)
            self._configure_local_contacts_tree_columns([])

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
                self.status_var.set("List created. Add contacts or import CSV.")
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

    def _load_templates(self, prefer_name: str | None = None) -> None:
        p = self._current_local_profile()
        if not p:
            return
        self._local_templates = fetch_templates(p["id"])
        names = [t["name"] for t in self._local_templates]
        self.local_template_cb["values"] = names
        if names:
            cur = self.local_template_var.get()
            if prefer_name and prefer_name in names:
                sel = prefer_name
            elif cur in names:
                sel = cur
            else:
                sel = names[0]
            self.local_template_var.set(sel)
            self._on_local_template_pick()
        else:
            self.local_template_var.set("")
            self.template_name_entry.delete(0, tk.END)
            self.template_text.delete("1.0", tk.END)

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
        self.status_var.set("Template saved.")

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

    def _open_local_profile(self) -> None:
        p = self._current_local_profile()
        if not p:
            messagebox.showinfo("Local mode", "Select profile first.")
            return
        ph = str(p["phone"])
        state = self.profile_by_phno.get(ph)
        if state is None:
            state = ProfileState(client_idno=p["id"], client_name=p["name"], client_phno=ph)
            self.profile_by_phno[ph] = state
        result = self.scheduler.open_profile(state)
        self.status_var.set("Local profile opened." if result == "SUCCESS" else f"Open failed: {result}")
        if result != "SUCCESS":
            messagebox.showerror("Open profile", result)

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

    def _run_local_send_job(self, job: dict[str, Any]) -> None:
        profile_phone = str(job["profile_phone"])
        profile_id = int(job["profile_id"])
        target_mode = str(job["target_mode"])
        allow_search = bool(job.get("allow_search", False))

        def emit_local(event_type: str, message: str) -> None:
            logger.info("[%s][%s] %s", profile_phone, event_type, message)

        state = self.profile_by_phno.get(profile_phone)
        driver = state.get_driver() if state else None
        if driver is None:
            emit_local("queue_error", f"Skipped queued send for {profile_phone}: profile is not open.")
            for item in job.get("items", []):
                target_type = "group" if target_mode == "group" else "contact"
                target_value = str(item.get("receiver", ""))
                rendered = str(item.get("rendered", ""))
                try:
                    log_local_send(profile_id, target_type, target_value, rendered, "ERROR", "Profile not open")
                except Exception:
                    pass
            return

        if target_mode == "group":
            item = (job.get("items") or [{}])[0]
            receiver = str(item.get("receiver", "")).strip()
            rendered = str(item.get("rendered", ""))
            if not receiver:
                emit_local("group_send_error", "Group not selected.")
                return
            result = send_message(
                driver,
                receiver_identifier=receiver,
                message=rendered,
                is_group=True,
                allow_search=allow_search,
            )
            if result == "SUCCESS":
                try:
                    log_local_send(profile_id, "group", receiver, rendered, "SENT", "")
                except Exception as e:
                    emit_local("log_error", f"Group log write failed: {e}")
                emit_local("group_sent", f"Sent to group: {receiver}")
            else:
                try:
                    log_local_send(profile_id, "group", receiver, rendered, "ERROR", result)
                except Exception as e:
                    emit_local("log_error", f"Group log write failed: {e}")
                emit_local("group_error", f"{receiver}: {result}")
            return

        for item in job.get("items", []):
            receiver = str(item.get("receiver", ""))
            rendered = str(item.get("rendered", ""))
            name = str(item.get("name", ""))
            try:
                result = send_message(
                    driver,
                    receiver_identifier=receiver,
                    message=rendered,
                    is_group=False,
                    allow_search=allow_search,
                )
                if result == "SUCCESS":
                    try:
                        log_local_send(profile_id, "contact", receiver, rendered, "SENT", "")
                    except Exception as e:
                        emit_local("log_error", f"Contact log write failed: {e}")
                    emit_local("contact_sent", f"{name} ({receiver})")
                else:
                    try:
                        log_local_send(profile_id, "contact", receiver, rendered, "ERROR", result)
                    except Exception as e:
                        emit_local("log_error", f"Contact log write failed: {e}")
                    emit_local("contact_error", f"{name} ({receiver}): {result}")
            except Exception as e:
                emit_local("contact_exception", f"{name} ({receiver}): {e}")

    def _send_local(self, selected_only: bool) -> None:
        p = self._current_local_profile()
        if not p:
            messagebox.showinfo("Local mode", "Select profile first.")
            return
        ph = str(p["phone"])
        state = self.profile_by_phno.get(ph)
        if state is None or state.get_driver() is None:
            messagebox.showinfo("Local mode", "Open profile first.")
            return

        template = self.template_text.get("1.0", tk.END).strip()
        custom_vars = self._parse_custom_vars()
        target_mode = self.send_target_var.get()
        selected_ids = (
            set(self._local_contact_pick)
            if self._local_contact_pick
            else {int(x) for x in self.local_contacts_tree.selection()}
        )

        if target_mode == "contacts":
            if selected_only and not selected_ids:
                messagebox.showinfo(
                    "Local mode",
                    "Pick at least one contact (Pick column or double-click a row), or highlight rows, or use Send All.",
                )
                return
            if not self._local_contacts_cache:
                messagebox.showinfo("Local mode", "No contacts in current list.")
                return

        queue_key = str(p["phone"])
        items: list[dict[str, str]] = []
        if target_mode == "group":
            group = self.local_group_var.get().strip()
            if not group:
                messagebox.showinfo("Local mode", "Select a group first.")
                return
            items.append({"receiver": group, "name": group, "rendered": self._render_template(template, {}, custom_vars)})
        else:
            contacts = self._local_contacts_cache[:]
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
            "target_mode": target_mode,
            "allow_search": self.allow_search_var.get(),
            "items": items,
        }
        if queue_key not in self._local_send_queues:
            self._local_send_queues[queue_key] = queue.Queue()
        self._local_send_queues[queue_key].put(job)
        self._ensure_local_send_worker(queue_key)
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
   • “Send to Contacts”: uses your contact list.
   • “Send Selected”: sends only checked rows (Pick column), or only highlighted rows if nothing is checked.
   • “Send All”: sends to every contact in the current list.
   • “Send to Group”: uses the selected group instead of individual numbers.

11) LOGS
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
