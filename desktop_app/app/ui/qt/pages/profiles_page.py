"""Profiles — create, list, open WhatsApp, delete."""

from __future__ import annotations

import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
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
    create_local_profile,
    delete_local_profile,
    fetch_local_profiles,
)
from app.services.local_workflow_controller import LocalWorkflowController
from app.ui.qt.widgets.profile_combo_utils import populate_profile_combo


class ProfilesPage(QWidget):
    status_message = Signal(str)

    def __init__(self, workflow: LocalWorkflowController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._workflow = workflow
        self._profiles: list = []

        v = QVBoxLayout(self)
        v.setContentsMargins(40, 36, 48, 36)
        v.setSpacing(20)

        title = QLabel("Profiles")
        title.setProperty("class", "sectionTitle")
        v.addWidget(title)

        hint = QLabel(
            "One profile per WhatsApp number. Open a profile once to scan QR in Chrome; the session is saved."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #94A3B8; font-size: 14px; max-width: 800px;")
        v.addWidget(hint)

        form = QHBoxLayout()
        form.setSpacing(12)
        self._name = QLineEdit()
        self._name.setPlaceholderText("Display name")
        self._name.setMinimumHeight(40)
        self._phone = QLineEdit()
        self._phone.setPlaceholderText("WhatsApp phone (with country code)")
        self._phone.setMinimumHeight(40)
        form.addWidget(QLabel("New profile"))
        form.addWidget(self._name, 1)
        form.addWidget(self._phone, 1)
        save = QPushButton("Save profile")
        save.setObjectName("Primary")
        save.clicked.connect(self._save_profile)
        form.addWidget(save)
        v.addLayout(form)

        row = QHBoxLayout()
        row.addWidget(QLabel("Selected"))
        self._combo = QComboBox()
        self._combo.setMinimumHeight(40)
        self._combo.currentIndexChanged.connect(self._on_sel_changed)
        row.addWidget(self._combo, 1)
        open_btn = QPushButton("Open WhatsApp")
        open_btn.setObjectName("Primary")
        open_btn.clicked.connect(self._open_whatsapp)
        del_btn = QPushButton("Delete profile")
        del_btn.clicked.connect(self._delete_profile)
        row.addWidget(open_btn)
        row.addWidget(del_btn)
        v.addLayout(row)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Name", "Phone"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setMinimumHeight(280)
        self._table.setAlternatingRowColors(True)
        v.addWidget(self._table)

        ref = QPushButton("Refresh list")
        ref.clicked.connect(self.reload)
        v.addWidget(ref)

        self.reload()

    def reload(self) -> None:
        self._profiles = fetch_local_profiles()
        populate_profile_combo(self._combo, self._profiles)
        self._table.setRowCount(len(self._profiles))
        for r, p in enumerate(self._profiles):
            self._table.setItem(r, 0, QTableWidgetItem(str(p.get("name", ""))))
            self._table.setItem(r, 1, QTableWidgetItem(str(p.get("phone", ""))))

    def _current(self) -> dict | None:
        i = self._combo.currentIndex()
        if i < 0:
            return None
        d = self._combo.itemData(i)
        return d if isinstance(d, dict) else None

    def _on_sel_changed(self) -> None:
        p = self._current()
        if not p:
            return
        for r in range(self._table.rowCount()):
            it = self._table.item(r, 1)
            if it and it.text() == str(p.get("phone", "")):
                self._table.selectRow(r)
                break

    def _save_profile(self) -> None:
        name = self._name.text().strip()
        phone = self._phone.text().strip()
        if not name or not phone:
            QMessageBox.information(self, "Profiles", "Enter name and phone.")
            return
        try:
            create_local_profile(name, phone)
        except Exception as e:
            QMessageBox.critical(self, "Profiles", str(e))
            return
        self._name.clear()
        self._phone.clear()
        self.reload()
        self.status_message.emit("Profile saved.")

    def _open_whatsapp(self) -> None:
        p = self._current()
        if not p:
            QMessageBox.information(self, "Profiles", "Select a profile.")
            return

        def work() -> None:
            st, err = self._workflow.ensure_local_profile_ready(
                int(p["id"]), str(p["phone"]), str(p.get("name", ""))
            )
            if st is None:
                self.status_message.emit(f"Open failed: {err}")
            else:
                self.status_message.emit("WhatsApp opened.")

        threading.Thread(target=work, daemon=True).start()

    def _delete_profile(self) -> None:
        p = self._current()
        if not p:
            QMessageBox.information(self, "Profiles", "Select a profile.")
            return
        if (
            QMessageBox.question(
                self,
                "Delete profile",
                f'Delete profile "{p.get("name")}" and all its data?',
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            delete_local_profile(int(p["id"]))
        except Exception as e:
            QMessageBox.critical(self, "Profiles", str(e))
            return
        self.reload()
        self.status_message.emit("Profile deleted.")
