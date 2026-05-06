"""Scheduled jobs list — same data as Tk local schedule."""

from __future__ import annotations

import copy
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.db.local_access import (
    delete_local_scheduled_job,
    fetch_local_profiles,
    fetch_local_scheduled_jobs,
    update_local_scheduled_job,
)
from app.ui.qt.widgets.profile_combo_utils import populate_profile_combo


class SchedulePage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._jobs_by_id: dict[int, dict] = {}
        v = QVBoxLayout(self)
        v.setContentsMargins(40, 36, 48, 36)
        v.setSpacing(16)

        title = QLabel("Schedule")
        title.setProperty("class", "sectionTitle")
        v.addWidget(title)

        hint = QLabel(
            "Jobs listed here run automatically when their time is due (same engine as the classic app). "
            "Create new scheduled sends from the Send page with “schedule” timing when that option exists, "
            "or use the classic UI for full scheduling until we add a composer here."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #94A3B8; font-size: 14px; max-width: 880px;")
        v.addWidget(hint)

        row = QHBoxLayout()
        row.addWidget(QLabel("Profile"))
        self._combo = QComboBox()
        self._combo.setMinimumHeight(40)
        self._combo.currentIndexChanged.connect(self.refresh)
        row.addWidget(self._combo, 1)
        row.addWidget(QPushButton("Refresh", clicked=self.refresh))
        row.addWidget(QPushButton("Edit selected job", clicked=self._edit_job))
        row.addWidget(QPushButton("Delete selected job", clicked=self._delete_job))
        v.addLayout(row)

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            ["Run at", "Status", "Target", "Recipients", "Message", "Items", "Last error"]
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(400)
        self._table.doubleClicked.connect(lambda _idx: self._edit_job())
        v.addWidget(self._table)

        self.reload_profiles()

    def reload_profiles(self) -> None:
        populate_profile_combo(self._combo, fetch_local_profiles())
        self.refresh()

    def _pid(self) -> int | None:
        i = self._combo.currentIndex()
        if i < 0:
            return None
        d = self._combo.itemData(i)
        return int(d["id"]) if isinstance(d, dict) else None

    def refresh(self) -> None:
        pid = self._pid()
        self._table.setRowCount(0)
        self._jobs_by_id.clear()
        if not pid:
            return
        try:
            jobs = fetch_local_scheduled_jobs(pid, limit=200)
        except Exception as e:
            self._table.setRowCount(1)
            self._table.setItem(0, 0, QTableWidgetItem(str(e)))
            return
        self._table.setRowCount(len(jobs))
        for r, j in enumerate(jobs):
            jid = int(j.get("id", 0))
            self._jobs_by_id[jid] = j
            ra = j.get("run_at")
            if isinstance(ra, datetime):
                ra_s = ra.strftime("%Y-%m-%d %H:%M")
            else:
                ra_s = str(ra or "")
            payload = j.get("payload") or {}
            mode = str(payload.get("target_mode", ""))
            items = payload.get("items") or []
            n = len(items)
            recipients = self._recipients_preview(items)
            msg_prev = self._message_preview(items)
            err = str(j.get("error_text") or "")
            if len(err) > 80:
                err = err[:77] + "…"
            it0 = QTableWidgetItem(ra_s)
            it0.setData(Qt.ItemDataRole.UserRole, jid)
            self._table.setItem(r, 0, it0)
            self._table.setItem(r, 1, QTableWidgetItem(str(j.get("status", ""))))
            self._table.setItem(r, 2, QTableWidgetItem(mode))
            self._table.setItem(r, 3, QTableWidgetItem(recipients))
            self._table.setItem(r, 4, QTableWidgetItem(msg_prev))
            self._table.setItem(r, 5, QTableWidgetItem(str(n)))
            self._table.setItem(r, 6, QTableWidgetItem(err))

    def _selected_job_id(self) -> int:
        sel = self._table.selectedItems()
        if not sel:
            return 0
        row = sel[0].row()
        it = self._table.item(row, 0)
        return int(it.data(Qt.ItemDataRole.UserRole)) if it else 0

    @staticmethod
    def _message_preview(items: list[dict]) -> str:
        msg = ""
        for it in items:
            txt = str(it.get("rendered", "") or "").strip()
            if txt:
                msg = txt
                break
        if len(msg) > 90:
            msg = msg[:87] + "…"
        return msg

    @staticmethod
    def _recipients_preview(items: list[dict]) -> str:
        vals: list[str] = []
        for it in items:
            nm = str(it.get("name", "") or "").strip()
            rc = str(it.get("receiver", "") or "").strip()
            vals.append(nm or rc or "—")
        txt = ", ".join(vals)
        if len(txt) > 120:
            txt = txt[:117] + "…"
        return txt

    def _edit_job(self) -> None:
        pid = self._pid()
        if not pid:
            return
        jid = self._selected_job_id()
        if not jid:
            QMessageBox.information(self, "Schedule", "Select a row.")
            return
        job = self._jobs_by_id.get(jid) or {}
        status = str(job.get("status", "")).upper()
        if status != "PENDING":
            QMessageBox.information(self, "Schedule", "Only PENDING jobs can be edited.")
            return
        payload = copy.deepcopy(job.get("payload") or {})
        items = payload.get("items") or []
        cur_msg = ""
        for it in items:
            cur_msg = str(it.get("rendered", "") or "").strip()
            if cur_msg:
                break
        recipients = self._recipients_preview(items)
        run_at = job.get("run_at")
        if not isinstance(run_at, datetime):
            QMessageBox.warning(self, "Schedule", "Job time is invalid.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Edit scheduled job")
        lay = QVBoxLayout(dlg)
        form = QFormLayout()
        time_edit = QDateTimeEdit(dlg)
        time_edit.setCalendarPopup(True)
        time_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        time_edit.setDateTime(run_at)
        form.addRow("Run at", time_edit)
        rec_edit = QLineEdit(recipients, dlg)
        rec_edit.setReadOnly(True)
        form.addRow("Recipients", rec_edit)
        msg_edit = QPlainTextEdit(dlg)
        msg_edit.setPlainText(cur_msg)
        msg_edit.setMinimumHeight(140)
        form.addRow("Message", msg_edit)
        lay.addLayout(form)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, dlg
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)
        if dlg.exec() != int(QDialog.DialogCode.Accepted):
            return

        new_run = time_edit.dateTime().toPython()
        if new_run <= datetime.now():
            QMessageBox.information(self, "Schedule", "Schedule time must be in the future.")
            return
        new_msg = msg_edit.toPlainText().strip()
        if not new_msg:
            QMessageBox.information(self, "Schedule", "Message cannot be empty.")
            return
        for it in items:
            if isinstance(it, dict):
                it["rendered"] = new_msg
        payload["items"] = items
        try:
            update_local_scheduled_job(int(pid), int(jid), new_run, payload)
        except Exception as e:
            QMessageBox.critical(self, "Schedule", str(e))
            return
        self.refresh()

    def _delete_job(self) -> None:
        pid = self._pid()
        if not pid:
            return
        jid = self._selected_job_id()
        if not jid:
            QMessageBox.information(self, "Schedule", "Select a row.")
            return
        if (
            QMessageBox.question(self, "Delete", "Remove this scheduled job?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            delete_local_scheduled_job(pid, int(jid))
        except Exception as e:
            QMessageBox.critical(self, "Schedule", str(e))
            return
        self.refresh()
