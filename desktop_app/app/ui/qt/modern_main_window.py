"""
PySide6 shell: icon sidebar + stacked pages. Local mode; SQL/hybrid can use legacy Tk.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from app.db.local_access import init_local_db
from app.services.local_workflow_controller import LocalWorkflowController
from app.ui.qt.pages.contacts_page import ContactsPage
from app.ui.qt.pages.dashboard_page import DashboardPage
from app.ui.qt.pages.groups_page import GroupsPage
from app.ui.qt.pages.logs_page import LogsPage
from app.ui.qt.pages.profiles_page import ProfilesPage
from app.ui.qt.pages.schedule_page import SchedulePage
from app.ui.qt.pages.send_messages_page import SendMessagesPage
from app.ui.qt.pages.templates_page import TemplatesPage
from app.ui.qt.pages.wa_contacts_page import WaContactsPage
from app.ui.qt.styles import APP_QSS

logger = logging.getLogger(__name__)

# Nav: (id, icon, label) — order must match stacked widget indices.
NAV = [
    ("home", "🏠", "Dashboard"),
    ("profiles", "👤", "Profiles"),
    ("contacts", "📇", "Contacts & lists"),
    ("wa", "📱", "WhatsApp contacts"),
    ("groups", "👥", "WhatsApp groups"),
    ("templates", "📝", "Templates"),
    ("send", "💬", "Send"),
    ("schedule", "🕐", "Schedule"),
    ("logs", "📋", "Logs"),
]


class ModernMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("WhatsApp Desktop")
        self.setMinimumSize(1180, 700)
        self.resize(1360, 820)

        self._workflow = LocalWorkflowController(
            on_scheduler_log=self._on_sched_log,
            on_schedule_due=None,
        )

        try:
            init_local_db()
        except Exception as e:
            logger.exception("init_local_db")
            QMessageBox.critical(
                self,
                "Database",
                f"Local database could not be initialized.\n\n{e}",
            )

        root = QWidget()
        root.setObjectName("centralSurface")
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._sidebar_collapsed = False

        self._side = QFrame()
        self._side.setObjectName("Sidebar")
        self._side.setFixedWidth(268)
        sv = QVBoxLayout(self._side)
        sv.setContentsMargins(14, 22, 14, 22)
        sv.setSpacing(8)
        top = QHBoxLayout()
        self._brand = QLabel("  WA Desktop")
        self._brand.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        self._brand.setStyleSheet("color: #22C55E;")
        top.addWidget(self._brand, 1)
        self._btn_toggle_sidebar = QPushButton("⟨")
        self._btn_toggle_sidebar.setFixedSize(34, 30)
        self._btn_toggle_sidebar.clicked.connect(self._toggle_sidebar)
        top.addWidget(self._btn_toggle_sidebar, 0)
        sv.addLayout(top)
        self._nav = QListWidget()
        self._nav.setObjectName("NavList")
        for _nid, icon, label in NAV:
            QListWidgetItem(f"  {icon}  {label}", self._nav)
        self._nav.setCurrentRow(0)
        self._nav.currentRowChanged.connect(self._on_nav_changed)
        sv.addWidget(self._nav, 1)
        self._foot = QLabel("  Local mode")
        self._foot.setStyleSheet("color: #64748B; font-size: 12px; padding-top: 8px;")
        sv.addWidget(self._foot)
        lay.addWidget(self._side)

        self._stack = QStackedWidget()
        lay.addWidget(self._stack, 1)

        self._dashboard = DashboardPage()
        self._dashboard.open_stack_index.connect(self._go_stack_index)
        self._stack.addWidget(self._dashboard)

        self._profiles_page = ProfilesPage(self._workflow)
        self._profiles_page.status_message.connect(self._set_status)
        self._stack.addWidget(self._profiles_page)

        self._contacts_page = ContactsPage()
        self._contacts_page.open_send_requested.connect(self._open_send_with_selection)
        self._stack.addWidget(self._contacts_page)

        self._wa_page = WaContactsPage(self._workflow)
        self._wa_page.status_message.connect(self._set_status)
        self._wa_page.open_send_requested.connect(self._open_send_with_selection)
        self._stack.addWidget(self._wa_page)

        self._groups_page = GroupsPage(self._workflow)
        self._groups_page.status_message.connect(self._set_status)
        self._groups_page.open_send_requested.connect(self._open_send_with_selection)
        self._stack.addWidget(self._groups_page)

        self._templates_page = TemplatesPage()
        self._stack.addWidget(self._templates_page)

        self._send_page = SendMessagesPage(self._workflow)
        self._send_page.status_message.connect(self._set_status)
        self._stack.addWidget(self._send_page)

        self._schedule_page = SchedulePage()
        self._stack.addWidget(self._schedule_page)

        self._logs_page = LogsPage()
        self._stack.addWidget(self._logs_page)

        sb = QStatusBar()
        sb.showMessage("Ready.")
        self.setStatusBar(sb)

        self._workflow.ensure_schedule_worker()

    def _on_sched_log(self, ph: str, event: str, msg: str) -> None:
        logger.info("[sched][%s] %s %s", ph, event, msg)

    @Slot(str)
    def _set_status(self, text: str) -> None:
        self.statusBar().showMessage(text, 8000)

    @Slot(int)
    def _go_stack_index(self, index: int) -> None:
        if index < 0 or index >= len(NAV):
            return
        self._nav.setCurrentRow(index)

    @Slot(object)
    def _open_send_with_selection(self, sel: object) -> None:
        if not isinstance(sel, dict):
            return
        self._nav.setCurrentRow(6)
        self._send_page.open_with_selection(sel)

    def _refresh_page_for_nav(self, row: int) -> None:
        nav_id = NAV[row][0]
        if nav_id == "profiles":
            self._profiles_page.reload()
        elif nav_id == "contacts":
            self._contacts_page.reload_profiles()
        elif nav_id == "wa":
            self._wa_page.reload_profiles()
        elif nav_id == "groups":
            self._groups_page.reload_profiles()
        elif nav_id == "templates":
            self._templates_page.reload_profiles()
        elif nav_id == "send":
            self._send_page.reload_profiles()
        elif nav_id == "schedule":
            self._schedule_page.reload_profiles()
        elif nav_id == "logs":
            self._logs_page.reload_profiles()

    def _on_nav_changed(self, row: int) -> None:
        if row < 0:
            return
        self._stack.setCurrentIndex(row)
        self._refresh_page_for_nav(row)

    def _toggle_sidebar(self) -> None:
        self._sidebar_collapsed = not self._sidebar_collapsed
        if self._sidebar_collapsed:
            self._side.setFixedWidth(78)
            self._brand.setText("  WA")
            self._foot.setText("")
            self._btn_toggle_sidebar.setText("⟩")
            for i, (_nid, icon, _label) in enumerate(NAV):
                it = self._nav.item(i)
                if it:
                    it.setText(f"  {icon}")
        else:
            self._side.setFixedWidth(268)
            self._brand.setText("  WA Desktop")
            self._foot.setText("  Local mode")
            self._btn_toggle_sidebar.setText("⟨")
            for i, (_nid, icon, label) in enumerate(NAV):
                it = self._nav.item(i)
                if it:
                    it.setText(f"  {icon}  {label}")

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        event.accept()


def run_qt_app() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI"))
    app.setStyleSheet(APP_QSS)
    win = ModernMainWindow()
    win.show()
    sys.exit(app.exec())
