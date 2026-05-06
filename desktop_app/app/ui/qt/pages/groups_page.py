"""WhatsApp groups directory — sync from New chat > Groups tab."""

from __future__ import annotations

import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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

from app.db.local_access import (
    fetch_groups,
    fetch_local_profiles,
    merge_group_members_into_contact_list,
    replace_groups,
)
from app.services.local_workflow_controller import LocalWorkflowController
from app.ui.qt.widgets.profile_combo_utils import populate_profile_combo
from app.whatsapp.sender import (
    sync_group_members_to_whatsapp_directory,
    sync_whatsapp_groups_from_new_chat,
)


class GroupsPage(QWidget):
    status_message = Signal(str)
    open_send_requested = Signal(object)
    _sync_finished = Signal(str)
    _members_finished = Signal(str)

    def __init__(self, workflow: LocalWorkflowController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._workflow = workflow
        self._sync_finished.connect(self._on_sync_finished)
        self._members_finished.connect(self._on_members_finished)

        v = QVBoxLayout(self)
        v.setContentsMargins(40, 36, 48, 36)
        v.setSpacing(16)

        title = QLabel("WhatsApp groups")
        title.setProperty("class", "sectionTitle")
        v.addWidget(title)

        hint = QLabel(
            "Loads group names from WhatsApp New chat → Groups tab. "
            "Use “Add members to contact list” on selected groups to open each group, "
            "read member names and numbers from the participant list, and merge them into "
            "normal Contacts & Lists for this profile (list name = group name). "
            "Keep WhatsApp Web on the main chat screen."
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
        row.addWidget(QPushButton("Load groups", clicked=self._sync))
        row.addWidget(QPushButton("Clear saved list", clicked=self._clear))
        row.addWidget(
            QPushButton(
                "Add members to contact list",
                clicked=self._add_members_to_contact_list,
            )
        )
        row.addWidget(QPushButton("Send selected…", clicked=self._open_send_with_selected))
        v.addLayout(row)

        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        search_row.addWidget(QLabel("Search"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search saved group names…")
        self._search.setMinimumHeight(36)
        self._search.textChanged.connect(self.refresh_table)
        search_row.addWidget(self._search, 1)
        v.addLayout(search_row)

        self._table = QTableWidget(0, 1)
        self._table.setHorizontalHeaderLabels(["Group name"])
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
            rows = fetch_groups(pid)
        except Exception as e:
            self._table.setRowCount(1)
            self._table.setItem(0, 0, QTableWidgetItem(f"Error: {e}"))
            return
        q = self._search.text().strip().lower() if hasattr(self, "_search") else ""
        if q:
            rows = [x for x in rows if q in str(x.get("name", "")).lower()]
        self._table.setRowCount(len(rows))
        for r, x in enumerate(rows):
            self._table.setItem(r, 0, QTableWidgetItem(str(x.get("name", ""))))

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
        self.status_message.emit("Syncing groups from WhatsApp…")

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
            status, groups = sync_whatsapp_groups_from_new_chat(driver)
            if status != "SUCCESS":
                self._sync_finished.emit(status)
                return
            try:
                replace_groups(int(p["id"]), groups)
            except Exception as e:
                self._sync_finished.emit(f"Save failed: {e}")
                return
            self._sync_finished.emit(f"OK:{len(groups)}")

        threading.Thread(target=work, daemon=True).start()

    def _on_sync_finished(self, msg: str) -> None:
        if msg.startswith("OK:"):
            self.refresh_table()
            n = msg.split(":", 1)[-1]
            self.status_message.emit(f"Saved {n} group(s).")
        else:
            self.status_message.emit(msg)
            if not msg.startswith("Open failed") and "Save failed" not in msg:
                QMessageBox.warning(self, "Sync groups", msg)

    def _add_members_to_contact_list(self) -> None:
        p = self._combo.itemData(self._combo.currentIndex())
        if not isinstance(p, dict):
            QMessageBox.information(self, "Profile", "Select a profile.")
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
            QMessageBox.information(
                self,
                "Add members",
                "Select one or more saved group rows first (same names as in WhatsApp).",
            )
            return
        self.status_message.emit("Opening group(s) and reading members…")

        def work() -> None:
            st, err = self._workflow.ensure_local_profile_ready(
                int(p["id"]), str(p["phone"]), str(p.get("name", ""))
            )
            if st is None:
                self._members_finished.emit(f"Open failed: {err}")
                return
            driver = st.get_driver()
            if driver is None:
                self._members_finished.emit("No browser session.")
                return
            pid = int(p["id"])
            total_written = 0
            total_read = 0
            total_written = 0
            for gname in names:
                status, rows = sync_group_members_to_whatsapp_directory(driver, gname)
                if status != "SUCCESS":
                    self._members_finished.emit(f"{gname}: {status}")
                    return
                total_read += len(rows)
                try:
                    total_written += merge_group_members_into_contact_list(pid, gname, rows)
                except Exception as e:
                    self._members_finished.emit(f"{gname}: Save failed: {e}")
                    return
            self._members_finished.emit(f"OK:{total_written}:{total_read}")

        threading.Thread(target=work, daemon=True).start()

    def _on_members_finished(self, msg: str) -> None:
        if msg.startswith("OK:"):
            parts = msg.split(":")
            n_saved = parts[1] if len(parts) > 1 else "0"
            n_read = parts[2] if len(parts) > 2 else ""
            self.status_message.emit(
                f"Merged {n_saved} contact row(s) from {n_read} member detail(s) read."
            )
            QMessageBox.information(
                self,
                "Add members",
                f"Merged {n_saved} row(s) into Contacts & Lists "
                f"(read {n_read} member detail(s)). "
                "Open Contacts & Lists page and select the group-named list to review.",
            )
        else:
            self.status_message.emit(msg)
            QMessageBox.warning(self, "Add members", msg)

    def _clear(self) -> None:
        pid = self._pid()
        if not pid:
            return
        if (
            QMessageBox.question(self, "Clear", "Remove all saved groups for this profile?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            replace_groups(pid, [])
        except Exception as e:
            QMessageBox.critical(self, "Clear", str(e))
            return
        self.refresh_table()
        self.status_message.emit("Cleared.")

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
            QMessageBox.information(self, "Send", "Select one or more group rows.")
            return
        self.open_send_requested.emit({"source": "groups", "profile_id": int(pid), "group_names": names})
