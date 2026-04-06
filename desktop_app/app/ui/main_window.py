"""
Main Tkinter window: list of clients (profiles), Open/Start/Pause/Resume/Stop, Pause All/Resume All.
"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import tempfile
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from datetime import datetime
from urllib.request import urlretrieve

from app.db.sql import fetch_clients
from app.core.message_loop import SCHEDULER_INTERVAL
from app.core.profile_state import ProfileState
from app.core.scheduler import Scheduler

logger = logging.getLogger(__name__)

# Official Microsoft page to download ODBC Driver 18 for SQL Server
ODBC_DOWNLOAD_URL = "https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server"
# Stable Microsoft redirect to the x64 MSI installer (ODBC Driver 18). May change when Microsoft updates the driver.
ODBC_MSI_URL = "https://go.microsoft.com/fwlink/?linkid=2345415"


class MainWindow:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("WhatsApp Desktop – Multi-Profile Sender")
        self.root.geometry("980x650")
        self.root.minsize(860, 560)

        self.log_queue: "queue.Queue[tuple[str, str, str, str]]" = queue.Queue()
        self.scheduler = Scheduler(on_log=self._enqueue_log)
        self.profiles: list[ProfileState] = []
        self.profile_by_phno: dict[str, ProfileState] = {}

        self._configure_styles()
        self._build_ui()
        self._build_menu()
        self._load_clients()

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
        style.configure("Treeview", rowheight=26, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill=tk.X)
        ttk.Label(top, text="WhatsApp Desktop - Multi-Profile Sender", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(
            top,
            text="Open profile, start scheduler, and monitor runtime logs in one place.",
            style="Hint.TLabel",
        ).pack(anchor=tk.W, pady=(2, 0))

        body = ttk.Frame(self.root, padding=(10, 0, 10, 0), style="Card.TFrame")
        body.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(body, padding=10, style="Card.TFrame")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right = ttk.Frame(body, padding=10, style="Card.TFrame")
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=False)

        ttk.Label(left, text="Clients (Chrome profiles)", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, pady=(0, 8))
        self.tree = ttk.Treeview(
            left,
            columns=("name", "phone", "status"),
            show="headings",
            height=12,
            selectmode="browse",
        )
        self.tree.heading("name", text="Client")
        self.tree.heading("phone", text="Phone")
        self.tree.heading("status", text="Status")
        self.tree.column("name", width=260)
        self.tree.column("phone", width=150)
        self.tree.column("status", width=140)
        self.tree.tag_configure("status_running", foreground="#065f46")
        self.tree.tag_configure("status_paused", foreground="#92400e")
        self.tree.tag_configure("status_open", foreground="#1d4ed8")
        self.tree.tag_configure("status_not_open", foreground="#6b7280")
        scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 0), pady=(0, 10))
        scroll.pack(side=tk.RIGHT, fill=tk.Y, padx=(6, 0), pady=(0, 10))

        ttk.Label(right, text="Actions", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, pady=(0, 8))
        ttk.Button(
            right,
            text="Open Profile (WhatsApp Web)",
            command=self._on_open,
            style="Primary.TButton",
            width=28,
        ).pack(fill=tk.X, pady=(0, 6))
        ttk.Button(right, text="Start", command=self._on_start, width=28).pack(fill=tk.X, pady=3)
        ttk.Button(right, text="Pause", command=self._on_pause, width=28).pack(fill=tk.X, pady=3)
        ttk.Button(right, text="Resume", command=self._on_resume, width=28).pack(fill=tk.X, pady=3)
        ttk.Button(right, text="Stop", command=self._on_stop, width=28).pack(fill=tk.X, pady=3)
        ttk.Separator(right, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)
        ttk.Button(right, text="Pause All", command=self._on_pause_all, width=28).pack(fill=tk.X, pady=3)
        ttk.Button(right, text="Resume All", command=self._on_resume_all, width=28).pack(fill=tk.X, pady=3)
        ttk.Button(right, text="Refresh List", command=self._load_clients, width=28).pack(fill=tk.X, pady=3)

        self.status_var = tk.StringVar(value="Select a client and use Open / Start / Pause / Resume / Stop.")
        status_strip = ttk.Frame(self.root, padding=(10, 6), style="Card.TFrame")
        status_strip.pack(fill=tk.X, padx=10, pady=(8, 6))
        ttk.Label(status_strip, text="Status:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(status_strip, textvariable=self.status_var, foreground="#1f2937").pack(side=tk.LEFT)

        logs_frame = ttk.LabelFrame(self.root, text="Runtime Logs", padding=8)
        logs_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.log_text = scrolledtext.ScrolledText(logs_frame, height=10, state=tk.DISABLED, wrap=tk.WORD)
        self.log_text.configure(
            bg="#0b1220",
            fg="#d1e4ff",
            insertbackground="#ffffff",
            font=("Consolas", 9),
            relief=tk.FLAT,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="Install ODBC Driver (open download page)", command=self._open_odbc_download)
        help_menu.add_command(
            label="Download and run ODBC installer (admin may be required)",
            command=self._download_and_run_odbc_installer,
        )

    def _open_odbc_download(self) -> None:
        """Open Microsoft's ODBC Driver 18 download page in the default browser."""
        try:
            webbrowser.open(ODBC_DOWNLOAD_URL)
            self.status_var.set("Opened ODBC Driver download page in your browser.")
            self._append_log("SYSTEM", "odbc_help", "Opened ODBC Driver download page.")
        except Exception as e:
            messagebox.showerror("Error", f"Could not open browser: {e}\n\nOpen this URL manually:\n{ODBC_DOWNLOAD_URL}")

    def _download_and_run_odbc_installer(self) -> None:
        """
        Download the ODBC Driver 18 MSI from Microsoft's stable URL and run the installer.
        Best-effort: may require admin (UAC), and can fail on locked-down or offline PCs.
        """
        if os.name != "nt":
            messagebox.showinfo("Windows only", "This option is for Windows. On other systems, use the download page.")
            return
        self.status_var.set("Downloading ODBC Driver 18 installer...")
        self.root.update_idletasks()
        try:
            fd, path = tempfile.mkstemp(suffix=".msi", prefix="msodbcsql18_")
            os.close(fd)
            try:
                urlretrieve(ODBC_MSI_URL, path)
            except Exception as e:
                os.remove(path)
                raise e
            self.status_var.set("Starting installer...")
            self.root.update_idletasks()
            subprocess.Popen(
                ["msiexec", "/i", path],
                shell=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            messagebox.showinfo(
                "Installer started",
                "The ODBC Driver 18 installer window should open.\n\n"
                "Complete the setup (you may see a UAC prompt).\n"
                "When done, restart this app and try again.",
            )
            self.status_var.set("Installer started. Complete setup, then restart this app.")
            self._append_log("SYSTEM", "odbc_install", "Started ODBC installer via msiexec.")
        except Exception as e:
            logger.exception("ODBC download/run failed")
            messagebox.showerror(
                "Install failed",
                f"Could not download or run the installer:\n{e}\n\n"
                "Use Help → Install ODBC Driver (open download page) and install manually.",
            )
            self.status_var.set("ODBC install failed. Use download page instead.")
            self._append_log("SYSTEM", "odbc_install_error", str(e))

    def _load_clients(self) -> None:
        try:
            clients = fetch_clients()
        except Exception as e:
            err_msg = str(e)
            if _is_odbc_driver_error(err_msg):
                if messagebox.askyesno(
                    "ODBC Driver not found",
                    "The SQL Server ODBC driver is not installed.\n\n"
                    "Install \"ODBC Driver 18 for SQL Server\" from Microsoft (Windows x64), then restart this app.\n\n"
                    "Open the download page in your browser now?",
                ):
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
            profile = ProfileState(
                client_idno=c["client_idno"],
                client_name=c["client_name"] or "",
                client_phno=phno,
            )
            self.profiles.append(profile)
            self.profile_by_phno[phno] = profile
            self.tree.insert("", tk.END, values=(profile.client_name, phno, "Not open"), iid=phno)
        self._refresh_statuses()
        self._append_log("SYSTEM", "clients_load", f"Loaded {len(self.profiles)} client profile(s).")

    def _get_selected_profile(self) -> ProfileState | None:
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        return self.profile_by_phno.get(iid)

    def _refresh_statuses(self) -> None:
        for profile in self.profiles:
            if profile.is_stopped() or not profile.is_running():
                if profile.get_driver() is not None:
                    status = "Open"
                else:
                    status = "Not open"
            elif profile.is_paused():
                status = "Paused"
            else:
                status = "Running"
            try:
                tag = "status_not_open"
                if status == "Running":
                    tag = "status_running"
                elif status == "Paused":
                    tag = "status_paused"
                elif status == "Open":
                    tag = "status_open"
                self.tree.item(
                    profile.client_phno,
                    values=(profile.client_name, profile.client_phno, status),
                    tags=(tag,),
                )
            except tk.TclError:
                pass
        self.root.after(2000, self._refresh_statuses)

    def _on_open(self) -> None:
        p = self._get_selected_profile()
        if not p:
            self.status_var.set("Select a client first.")
            return
        self.status_var.set("Opening WhatsApp Web...")
        self.root.update_idletasks()
        result = self.scheduler.open_profile(p)
        if result == "SUCCESS":
            self.status_var.set("WhatsApp Web opened. You can Start sending.")
        else:
            self.status_var.set(f"Open failed: {result}")
            messagebox.showerror("Open Profile", result)
        self._refresh_statuses()

    def _on_start(self) -> None:
        p = self._get_selected_profile()
        if not p:
            self.status_var.set("Select a client first.")
            return
        self.scheduler.start_loop(p)
        self.status_var.set(
            f"Started. Checking DB every {SCHEDULER_INTERVAL}s."
        )
        self._refresh_statuses()

    def _on_pause(self) -> None:
        p = self._get_selected_profile()
        if not p:
            self.status_var.set("Select a client first.")
            return
        self.scheduler.pause(p)
        self.status_var.set("Paused.")
        self._refresh_statuses()

    def _on_resume(self) -> None:
        p = self._get_selected_profile()
        if not p:
            self.status_var.set("Select a client first.")
            return
        self.scheduler.resume(p)
        self.status_var.set("Resumed.")
        self._refresh_statuses()

    def _on_stop(self) -> None:
        p = self._get_selected_profile()
        if not p:
            self.status_var.set("Select a client first.")
            return
        self.scheduler.stop_loop(p)
        self.status_var.set("Stopped. Chrome stays open.")
        self._refresh_statuses()

    def _on_pause_all(self) -> None:
        for p in self.profiles:
            if p.is_running():
                self.scheduler.pause(p)
        self.status_var.set("All paused.")
        self._refresh_statuses()

    def _on_resume_all(self) -> None:
        for p in self.profiles:
            if p.is_running():
                self.scheduler.resume(p)
        self.status_var.set("All resumed.")
        self._refresh_statuses()

    def _enqueue_log(self, client_phno: str, event_type: str, message: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.log_queue.put((ts, client_phno, event_type, message))

    def _append_log(self, client_phno: str, event_type: str, message: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [{client_phno}] [{event_type}] {message}\n"
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line)
        self.log_text.see(tk.END)
        # Keep UI responsive with bounded log size.
        max_lines = 1000
        current_lines = int(self.log_text.index("end-1c").split(".")[0])
        if current_lines > max_lines:
            self.log_text.delete("1.0", f"{current_lines - max_lines}.0")
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
    """True if the error is likely due to missing ODBC driver (e.g. IM002, driver not found)."""
    s = (err_msg or "").lower()
    return (
        "im002" in s
        or "data source name not found" in s
        or "driver" in s and "not found" in s
        or "no default driver specified" in s
    )
