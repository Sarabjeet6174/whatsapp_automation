"""
Main Tkinter window with mode-aware tabs:
- local: local MS Access mode only
- sql: existing SQL scheduler mode only
- hybrid: both tabs
"""
from __future__ import annotations

import csv
import logging
import os
import queue
import subprocess
import tempfile
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from string import Formatter
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
from urllib.request import urlretrieve

from app.core.message_loop import SCHEDULER_INTERVAL
from app.core.profile_state import ProfileState
from app.core.scheduler import Scheduler
from app.db.local_access import (
    create_contact,
    create_contact_list,
    create_group,
    create_local_profile,
    delete_contacts,
    delete_local_logs,
    fetch_contact_lists,
    fetch_contacts,
    fetch_groups,
    fetch_local_logs,
    fetch_local_profiles,
    fetch_templates,
    init_local_db,
    log_local_send,
    upsert_template,
)
from app.db.sql import fetch_clients
from app.whatsapp.sender import send_message
from config import allow_search_from_env

logger = logging.getLogger(__name__)

ODBC_DOWNLOAD_URL = "https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server"
ODBC_MSI_URL = "https://go.microsoft.com/fwlink/?linkid=2345415"


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
        ttk.Label(top, text="WhatsApp Desktop - Multi-Profile Sender", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(
            top,
            text="Modes: local (default), hybrid (local+sql), sql.",
            style="Hint.TLabel",
        ).pack(anchor=tk.W, pady=(2, 0))

        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))

        if self.run_mode in ("sql", "hybrid"):
            self.sql_tab = ttk.Frame(self.tabs, style="Card.TFrame")
            self.tabs.add(self.sql_tab, text="SQL Mode")
            self._build_sql_tab(self.sql_tab)

        if self.run_mode in ("local", "hybrid"):
            self.local_tab = ttk.Frame(self.tabs, style="Card.TFrame")
            self.tabs.add(self.local_tab, text="Local Mode")
            self._build_local_tab(self.local_tab)

        status_strip = ttk.Frame(self.root, padding=(10, 6), style="Card.TFrame")
        status_strip.pack(fill=tk.X, padx=10, pady=(0, 6))
        ttk.Label(status_strip, text="Status:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(status_strip, textvariable=self.status_var, foreground="#1f2937").pack(side=tk.LEFT)

        logs_frame = ttk.LabelFrame(self.root, text="Runtime Logs", padding=8)
        logs_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.log_text = scrolledtext.ScrolledText(logs_frame, height=9, state=tk.DISABLED, wrap=tk.WORD)
        self.log_text.configure(bg="#0b1220", fg="#d1e4ff", insertbackground="#ffffff", font=("Consolas", 9))
        self.log_text.pack(fill=tk.BOTH, expand=True)

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
        ttk.Button(row1, text="Add Profile", command=self._add_local_profile).pack(side=tk.LEFT, padx=2)

        ttk.Label(row1, text="Contact List").pack(side=tk.LEFT, padx=(14, 0))
        self.local_list_cb = ttk.Combobox(row1, textvariable=self.local_list_var, state="readonly", width=26)
        self.local_list_cb.pack(side=tk.LEFT, padx=6)
        self.local_list_cb.bind("<<ComboboxSelected>>", lambda _e: self._refresh_local_contacts())
        ttk.Button(row1, text="Add List", command=self._add_local_list).pack(side=tk.LEFT, padx=2)

        mid = ttk.PanedWindow(wrap, orient=tk.HORIZONTAL)
        mid.pack(fill=tk.BOTH, expand=True, pady=(8, 8))

        left = ttk.Frame(mid, style="Card.TFrame")
        right = ttk.Frame(mid, style="Card.TFrame")
        mid.add(left, weight=3)
        mid.add(right, weight=2)

        ttk.Label(left, text="Contacts", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W)
        self.local_contacts_tree = ttk.Treeview(
            left,
            columns=("name", "phone", "email", "company"),
            show="headings",
            height=13,
            selectmode="extended",
        )
        for c, w in (("name", 160), ("phone", 120), ("email", 170), ("company", 120)):
            self.local_contacts_tree.heading(c, text=c.capitalize())
            self.local_contacts_tree.column(c, width=w)
        self.local_contacts_tree.pack(fill=tk.BOTH, expand=True, pady=(4, 6))
        cbtn = ttk.Frame(left, style="Card.TFrame")
        cbtn.pack(fill=tk.X)
        ttk.Button(cbtn, text="Import Contacts CSV", command=self._import_contacts_csv).pack(side=tk.LEFT, padx=2)
        ttk.Button(cbtn, text="Delete Selected", command=self._delete_selected_contacts).pack(side=tk.LEFT, padx=2)

        ttk.Label(right, text="Template / Variables", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W)
        top_t = ttk.Frame(right, style="Card.TFrame")
        top_t.pack(fill=tk.X, pady=(4, 4))
        self.local_template_cb = ttk.Combobox(top_t, textvariable=self.local_template_var, state="readonly", width=20)
        self.local_template_cb.pack(side=tk.LEFT, padx=(0, 4))
        self.local_template_cb.bind("<<ComboboxSelected>>", lambda _e: self._on_local_template_pick())
        ttk.Button(top_t, text="Save Template", command=self._save_template).pack(side=tk.LEFT, padx=2)

        self.template_name_entry = ttk.Entry(right)
        self.template_name_entry.pack(fill=tk.X, pady=(0, 4))
        self.template_name_entry.insert(0, "template_name")

        self.template_text = scrolledtext.ScrolledText(right, height=7, wrap=tk.WORD)
        self.template_text.pack(fill=tk.BOTH, expand=False)
        self.template_text.insert(tk.END, "Hi {name}, your company is {company}.")

        ttk.Label(right, text="Custom variables (key=value per line)").pack(anchor=tk.W, pady=(6, 2))
        self.custom_vars_text = scrolledtext.ScrolledText(right, height=5, wrap=tk.WORD)
        self.custom_vars_text.pack(fill=tk.BOTH, expand=True)

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
        ttk.Button(bottom, text="View Logs", command=self._show_local_logs).pack(side=tk.LEFT, padx=2)
        ttk.Button(bottom, text="Delete Logs", command=self._delete_local_logs).pack(side=tk.LEFT, padx=2)

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="Install ODBC Driver (open download page)", command=self._open_odbc_download)
        help_menu.add_command(label="Download and run ODBC installer", command=self._download_and_run_odbc_installer)

    def _load_by_mode(self) -> None:
        if self.run_mode in ("sql", "hybrid"):
            self._load_clients()
        if self.run_mode in ("local", "hybrid"):
            try:
                init_local_db()
                self._load_local_profiles()
            except Exception as e:
                messagebox.showerror("Local DB error", str(e))

    # SQL mode methods
    def _load_clients(self) -> None:
        try:
            clients = fetch_clients()
        except Exception as e:
            err_msg = str(e)
            if _is_odbc_driver_error(err_msg):
                if messagebox.askyesno("ODBC Driver not found", "Install ODBC Driver 18 for SQL Server now?"):
                    self._open_odbc_download()
            else:
                messagebox.showerror("DB Error", err_msg)
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

    def _add_local_profile(self) -> None:
        name = simpledialog.askstring("Profile name", "Enter profile name:")
        if not name:
            return
        phone = simpledialog.askstring("Profile phone", "Enter WhatsApp profile phone:")
        if not phone:
            return
        create_local_profile(name, phone)
        self._load_local_profiles()
        self.status_var.set("Local profile created.")

    def _load_contact_lists(self) -> None:
        p = self._current_local_profile()
        if not p:
            return
        lists = fetch_contact_lists(p["id"])
        self._local_lists = lists
        names = [x["name"] for x in lists]
        self.local_list_cb["values"] = names
        if names:
            self.local_list_var.set(names[0])
            self._refresh_local_contacts()
        else:
            self.local_list_var.set("")
            for item in self.local_contacts_tree.get_children():
                self.local_contacts_tree.delete(item)

    def _selected_contact_list(self) -> dict | None:
        name = self.local_list_var.get()
        for x in getattr(self, "_local_lists", []):
            if x["name"] == name:
                return x
        return None

    def _add_local_list(self) -> None:
        p = self._current_local_profile()
        if not p:
            messagebox.showinfo("Local mode", "Select profile first.")
            return
        name = simpledialog.askstring("Contact list", "Enter list name:")
        if not name:
            return
        create_contact_list(p["id"], name)
        self._load_contact_lists()

    def _refresh_local_contacts(self) -> None:
        p = self._current_local_profile()
        lst = self._selected_contact_list()
        if not p or not lst:
            return
        self._local_contacts_cache = fetch_contacts(p["id"], lst["id"])
        for item in self.local_contacts_tree.get_children():
            self.local_contacts_tree.delete(item)
        for c in self._local_contacts_cache:
            self.local_contacts_tree.insert("", tk.END, iid=str(c["id"]), values=(c["name"], c["phone"], c["email"], c["company"]))

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
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                payload = {
                    "name": row.get("name", ""),
                    "phone": row.get("phone", ""),
                    "email": row.get("email", ""),
                    "company": row.get("company", ""),
                    "extra": {k: v for k, v in row.items() if k not in ("name", "phone", "email", "company")},
                }
                create_contact(p["id"], lst["id"], payload)
                count += 1
        self._refresh_local_contacts()
        self.status_var.set(f"Imported {count} contacts.")

    def _delete_selected_contacts(self) -> None:
        ids = [int(x) for x in self.local_contacts_tree.selection()]
        if not ids:
            return
        delete_contacts(ids)
        self._refresh_local_contacts()

    def _load_templates(self) -> None:
        p = self._current_local_profile()
        if not p:
            return
        self._local_templates = fetch_templates(p["id"])
        names = [t["name"] for t in self._local_templates]
        self.local_template_cb["values"] = names
        if names:
            self.local_template_var.set(names[0])
            self._on_local_template_pick()

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
        self._load_templates()
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
        out: dict[str, str] = {}
        raw = self.custom_vars_text.get("1.0", tk.END).strip()
        for line in raw.splitlines():
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
        return out

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

        def worker() -> None:
            driver = state.get_driver()
            if driver is None:
                return
            if target_mode == "group":
                group = self.local_group_var.get().strip()
                if not group:
                    self._append_log("LOCAL", "group_send_error", "Group not selected.")
                    return
                rendered = self._render_template(template, {}, custom_vars)
                result = send_message(driver, receiver_identifier=group, message=rendered, is_group=True, allow_search=self.allow_search_var.get())
                if result == "SUCCESS":
                    log_local_send(p["id"], "group", group, rendered, "SENT", "")
                    self._append_log("LOCAL", "group_sent", f"Sent to group: {group}")
                else:
                    log_local_send(p["id"], "group", group, rendered, "ERROR", result)
                    self._append_log("LOCAL", "group_error", f"{group}: {result}")
                return

            contacts = self._local_contacts_cache[:]
            if selected_only:
                selected_ids = {int(x) for x in self.local_contacts_tree.selection()}
                contacts = [c for c in contacts if c["id"] in selected_ids]
            for c in contacts:
                rendered = self._render_template(template, c, custom_vars)
                result = send_message(
                    driver,
                    receiver_identifier=str(c["phone"]),
                    message=rendered,
                    is_group=False,
                    allow_search=self.allow_search_var.get(),
                )
                if result == "SUCCESS":
                    log_local_send(p["id"], "contact", str(c["phone"]), rendered, "SENT", "")
                    self._append_log("LOCAL", "contact_sent", f'{c["name"]} ({c["phone"]})')
                else:
                    log_local_send(p["id"], "contact", str(c["phone"]), rendered, "ERROR", result)
                    self._append_log("LOCAL", "contact_error", f'{c["name"]} ({c["phone"]}): {result}')

        threading.Thread(target=worker, daemon=True).start()
        self.status_var.set("Local send started in background.")

    def _show_local_logs(self) -> None:
        p = self._current_local_profile()
        if not p:
            return
        logs = fetch_local_logs(p["id"], limit=200)
        win = tk.Toplevel(self.root)
        win.title("Local send logs")
        win.geometry("900x420")
        txt = scrolledtext.ScrolledText(win, wrap=tk.WORD)
        txt.pack(fill=tk.BOTH, expand=True)
        for row in logs:
            txt.insert(
                tk.END,
                f'[{row["created_at"]}] [{row["status"]}] [{row["target_type"]}] {row["target_value"]}\n{row["error_text"]}\n{row["rendered_message"]}\n\n',
            )
        txt.configure(state=tk.DISABLED)

    def _delete_local_logs(self) -> None:
        p = self._current_local_profile()
        if not p:
            return
        if not messagebox.askyesno("Delete logs", "Delete all local send logs for selected profile?"):
            return
        delete_local_logs(p["id"])
        self.status_var.set("Local logs deleted.")

    # shared ui/runtime logging
    def _open_odbc_download(self) -> None:
        try:
            webbrowser.open(ODBC_DOWNLOAD_URL)
            self.status_var.set("Opened ODBC Driver download page.")
            self._append_log("SYSTEM", "odbc_help", "Opened ODBC driver download page.")
        except Exception as e:
            messagebox.showerror("Error", f"Could not open browser: {e}\n{ODBC_DOWNLOAD_URL}")

    def _download_and_run_odbc_installer(self) -> None:
        if os.name != "nt":
            messagebox.showinfo("Windows only", "This option is for Windows.")
            return
        self.status_var.set("Downloading ODBC installer...")
        self.root.update_idletasks()
        try:
            fd, path = tempfile.mkstemp(suffix=".msi", prefix="msodbcsql18_")
            os.close(fd)
            try:
                urlretrieve(ODBC_MSI_URL, path)
            except Exception as e:
                os.remove(path)
                raise e
            subprocess.Popen(["msiexec", "/i", path], shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.status_var.set("Installer started.")
            self._append_log("SYSTEM", "odbc_install", "Started ODBC installer via msiexec.")
        except Exception as e:
            logger.exception("ODBC download/run failed")
            messagebox.showerror("Install failed", f"Could not download or run installer:\n{e}")
            self.status_var.set("ODBC install failed.")

    def _enqueue_log(self, client_phno: str, event_type: str, message: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_queue.put((ts, client_phno, event_type, message))

    def _append_log(self, client_phno: str, event_type: str, message: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{client_phno}] [{event_type}] {message}\n"
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _drain_log_queue(self) -> None:
        try:
            while True:
                ts, client_phno, event_type, message = self.log_queue.get_nowait()
                line = f"[{ts}] [{client_phno}] [{event_type}] {message}\n"
                self.log_text.configure(state=tk.NORMAL)
                self.log_text.insert(tk.END, line)
                self.log_text.see(tk.END)
                self.log_text.configure(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.root.after(500, self._drain_log_queue)

    def run(self) -> None:
        self._refresh_statuses()
        self._drain_log_queue()
        self.root.mainloop()


def _is_odbc_driver_error(err_msg: str) -> bool:
    s = (err_msg or "").lower()
    return (
        "im002" in s
        or "data source name not found" in s
        or "driver" in s and "not found" in s
        or "no default driver specified" in s
    )
