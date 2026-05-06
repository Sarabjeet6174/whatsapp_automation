"""WhatsApp directory — sync from New chat, table of names."""

from __future__ import annotations

import csv
import os
import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.db.local_access import fetch_local_profiles, fetch_whatsapp_directory, replace_whatsapp_directory
from app.services.local_workflow_controller import LocalWorkflowController
from app.ui.qt.widgets.profile_combo_utils import populate_profile_combo
from app.whatsapp.sender import sync_whatsapp_contacts_from_new_chat


class WaContactsPage(QWidget):
    status_message = Signal(str)
    open_send_requested = Signal(object)
    _sync_finished = Signal(str)

    def __init__(self, workflow: LocalWorkflowController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._workflow = workflow
        self._sync_finished.connect(self._on_sync_finished)

        v = QVBoxLayout(self)
        v.setContentsMargins(40, 36, 48, 36)
        v.setSpacing(16)

        title = QLabel("WhatsApp contacts")
        title.setProperty("class", "sectionTitle")
        v.addWidget(title)

        hint = QLabel(
            "Loads display names from WhatsApp’s New chat list. "
            "Click Open WhatsApp first if needed, then Load from WhatsApp and wait until it finishes."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #94A3B8; font-size: 14px; max-width: 900px;")
        v.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(QLabel("Profile"))
        self._combo = QComboBox()
        self._combo.setMinimumHeight(40)
        self._combo.currentIndexChanged.connect(self.refresh_table)
        row.addWidget(self._combo, 1)
        ob = QPushButton("Open WhatsApp")
        ob.setObjectName("Primary")
        ob.clicked.connect(self._open_whatsapp)
        row.addWidget(ob)
        row.addWidget(QPushButton("Load from WhatsApp", clicked=self._sync))
        row.addWidget(QPushButton("Export CSV", clicked=self._export_csv))
        row.addWidget(QPushButton("Clear saved list", clicked=self._clear))
        row.addWidget(QPushButton("Send selected…", clicked=self._open_send_with_selected))
        v.addLayout(row)

        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        search_row.addWidget(QLabel("Search"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search saved WhatsApp names…")
        self._search.setMinimumHeight(36)
        self._search.textChanged.connect(self.refresh_table)
        search_row.addWidget(self._search, 1)
        v.addLayout(search_row)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Name", "Phone"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setMinimumHeight(380)
        self._table.setAlternatingRowColors(True)
        v.addWidget(self._table)

        self.reload_profiles()

    def reload_profiles(self) -> None:
        populate_profile_combo(self._combo, fetch_local_profiles())
        self.refresh_table()

    def _pid(self) -> int | None:
        i = self._combo.currentIndex()
        if i < 0:
            return None
        d = self._combo.itemData(i)
        if isinstance(d, dict):
            return int(d["id"])
        return None

    def refresh_table(self) -> None:
        pid = self._pid()
        self._table.setRowCount(0)
        if not pid:
            return
        try:
            rows = fetch_whatsapp_directory(pid)
        except Exception as e:
            self._table.setRowCount(1)
            self._table.setItem(0, 0, QTableWidgetItem(f"Error: {e}"))
            self._table.setItem(0, 1, QTableWidgetItem(""))
            return
        q = self._search.text().strip().lower() if hasattr(self, "_search") else ""
        if q:
            rows = [x for x in rows if q in str(x.get("name", "")).lower()]
        self._table.setRowCount(len(rows))
        for r, x in enumerate(rows):
            self._table.setItem(r, 0, QTableWidgetItem(str(x.get("name", ""))))
            self._table.setItem(r, 1, QTableWidgetItem(str(x.get("phone", ""))))

    def _open_whatsapp(self) -> None:
        p = self._combo.itemData(self._combo.currentIndex())
        if not isinstance(p, dict):
            QMessageBox.information(self, "Profile", "Select a profile.")
            return

        def work() -> None:
            st, err = self._workflow.ensure_local_profile_ready(
                int(p["id"]), str(p["phone"]), str(p.get("name", ""))
            )
            if st is None:
                self.status_message.emit(f"Open failed: {err}")
            else:
                self.status_message.emit("WhatsApp ready.")

        threading.Thread(target=work, daemon=True).start()

    def _sync(self) -> None:
        p = self._combo.itemData(self._combo.currentIndex())
        if not isinstance(p, dict):
            QMessageBox.information(self, "Profile", "Select a profile.")
            return
        self.status_message.emit("Syncing contacts from WhatsApp…")

        def work() -> None:
            st, err = self._workflow.ensure_local_profile_ready(
                int(p["id"]), str(p["phone"]), str(p.get("name", ""))
            )
            if st is None:
                self._sync_finished.emit(f"Open failed: {err}")
                return
            driver = st.get_driver()
            if driver is None:
                self._sync_finished.emit("No browser session.")
                return
            status, names = sync_whatsapp_contacts_from_new_chat(driver)
            if status != "SUCCESS":
                self._sync_finished.emit(status)
                return
            try:
                replace_whatsapp_directory(int(p["id"]), names)
            except Exception as e:
                self._sync_finished.emit(f"Save failed: {e}")
                return
            self._sync_finished.emit(f"OK:{len(names)}")

        threading.Thread(target=work, daemon=True).start()

    def _on_sync_finished(self, msg: str) -> None:
        if msg.startswith("OK:"):
            self.refresh_table()
            n = msg.split(":", 1)[-1]
            self.status_message.emit(f"Saved {n} contact name(s).")
        else:
            self.status_message.emit(msg)
            if not msg.startswith("Open failed") and "Save failed" not in msg:
                QMessageBox.warning(self, "Sync", msg)

    def _clear(self) -> None:
        pid = self._pid()
        if not pid:
            return
        if (
            QMessageBox.question(self, "Clear", "Remove all saved names for this profile?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            replace_whatsapp_directory(pid, [])
        except Exception as e:
            QMessageBox.critical(self, "Clear", str(e))
            return
        self.refresh_table()
        self.status_message.emit("Cleared.")

    def _export_csv(self) -> None:
        pid = self._pid()
        if not pid:
            QMessageBox.information(self, "Export CSV", "Select a profile first.")
            return
        try:
            rows = fetch_whatsapp_directory(pid)
        except Exception as e:
            QMessageBox.critical(self, "Export CSV", str(e))
            return
        if not rows:
            QMessageBox.information(self, "Export CSV", "No WhatsApp contacts to export.")
            return
        default_name = "whatsapp_contacts_template.csv"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export WhatsApp Contacts CSV",
            default_name,
            "CSV files (*.csv);;All files (*.*)",
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                # Keep both fields so users can correct/normalize names later.
                # contacts_page importer maps whatsapp_name -> phone when phone is blank.
                # search_name = exact WhatsApp sidebar search string; name left blank to fill later.
                w.writerow(["name", "search_name", "phone", "email", "company"])
                for r in rows:
                    wa_name = str(r.get("name", "")).strip()
                    wa_phone = str(r.get("phone", "")).strip()
                    if not wa_name:
                        continue
                    w.writerow(["", wa_name, wa_phone, "", ""])
        except Exception as e:
            QMessageBox.critical(self, "Export CSV", f"{os.path.basename(path)}\n\n{e}")
            return
        self.status_message.emit(f"Exported {len(rows)} WhatsApp contact row(s) to CSV.")

    def _open_send_with_selected(self) -> None:
        pid = self._pid()
        if not pid:
            QMessageBox.information(self, "Send", "Select a profile first.")
            return
        names: list[str] = []
        seen: set[str] = set()
        for r in {i.row() for i in self._table.selectedItems()}:
            it = self._table.item(r, 0)
            name = (it.text() if it else "").strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        if not names:
            QMessageBox.information(self, "Send", "Select one or more WhatsApp contacts.")
            return
        self.open_send_requested.emit({"source": "wa", "profile_id": int(pid), "wa_names": names})
