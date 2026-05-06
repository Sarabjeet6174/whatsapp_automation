"""Send logs table for the selected profile."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.db.local_access import delete_local_logs, fetch_local_logs, fetch_local_profiles
from app.ui.qt.widgets.profile_combo_utils import current_profile, populate_profile_combo


class LogsPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(40, 36, 48, 36)
        v.setSpacing(16)

        title = QLabel("Logs")
        title.setProperty("class", "sectionTitle")
        v.addWidget(title)

        row = QHBoxLayout()
        row.addWidget(QLabel("Profile"))
        self._combo = QComboBox()
        self._combo.setMinimumHeight(40)
        self._combo.currentIndexChanged.connect(self.refresh)
        row.addWidget(self._combo, 1)
        row.addWidget(QPushButton("Refresh", clicked=self.refresh))
        row.addWidget(QPushButton("Clear logs", clicked=self._clear_logs))
        v.addLayout(row)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["Time", "Status", "Type", "Target", "Error"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(400)
        v.addWidget(self._table)

        self._profiles: list = []

    def reload_profiles(self) -> None:
        self._profiles = fetch_local_profiles()
        populate_profile_combo(self._combo, self._profiles)
        self.refresh()

    def refresh(self) -> None:
        p = current_profile(self._combo)
        self._table.setRowCount(0)
        if not p:
            return
        try:
            rows = fetch_local_logs(int(p["id"]), limit=300)
        except Exception as e:
            self._table.setRowCount(1)
            self._table.setItem(0, 0, QTableWidgetItem(f"Error loading logs: {e}"))
            return
        self._table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            self._table.setItem(r, 0, QTableWidgetItem(str(row.get("created_at", ""))[:22]))
            self._table.setItem(r, 1, QTableWidgetItem(str(row.get("status", ""))))
            self._table.setItem(r, 2, QTableWidgetItem(str(row.get("target_type", ""))))
            tv = str(row.get("target_value", ""))
            if len(tv) > 48:
                tv = tv[:45] + "…"
            self._table.setItem(r, 3, QTableWidgetItem(tv))
            err = str(row.get("error_text", "") or "")
            if len(err) > 120:
                err = err[:117] + "…"
            self._table.setItem(r, 4, QTableWidgetItem(err))

    def _clear_logs(self) -> None:
        p = current_profile(self._combo)
        if not p:
            QMessageBox.information(self, "Logs", "Select a profile.")
            return
        if (
            QMessageBox.question(self, "Clear logs", "Delete all send logs for this profile?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            delete_local_logs(int(p["id"]))
        except Exception as e:
            QMessageBox.critical(self, "Logs", str(e))
            return
        self.refresh()
