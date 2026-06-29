"""
PySide6 shell: icon sidebar + stacked pages. Local mode; SQL/hybrid can use legacy Tk.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QComboBox,
    QLineEdit,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QStackedWidget,
    QStatusBar,
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
from app.ui.qt.styles import apply_app_theme

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

PAGE_META = {
    "home": ("Dashboard", "Quick launch for common workflows"),
    "profiles": ("Profiles", "Manage identities and WhatsApp sessions"),
    "contacts": ("Contacts & lists", "Create lists and maintain recipients"),
    "wa": ("WhatsApp contacts", "Sync names from WhatsApp Web"),
    "groups": ("WhatsApp groups", "Sync groups and extract members"),
    "templates": ("Templates", "Reusable message content with variables"),
    "send": ("Send", "Select recipients and compose messages"),
    "schedule": ("Schedule", "Manage pending and dispatched jobs"),
    "logs": ("Logs", "Inspect send results and errors"),
}

TOP_ACTIONS = {
    "home": ("Go to Send", 6),
    "profiles": ("Open WhatsApp", None),
    "contacts": ("Open Send", 6),
    "wa": ("Open Send", 6),
    "groups": ("Open Send", 6),
    "templates": ("Go to Send", 6),
    "send": ("Go to Schedule", 7),
    "schedule": ("View Logs", 8),
    "logs": ("Go to Dashboard", 0),
}

QUICK_ACTIONS = {
    "home": [("Go to Send", 6), ("Go to Profiles", 1), ("Go to Logs", 8)],
    "profiles": [("Open Send page", 6), ("Go to Dashboard", 0)],
    "contacts": [("Open Send page", 6), ("Go to Templates", 5)],
    "wa": [("Open Send page", 6), ("Go to Groups", 4)],
    "groups": [("Open Send page", 6), ("Go to Contacts", 2)],
    "templates": [("Open Send page", 6), ("Go to Contacts", 2)],
    "send": [("Go to Schedule", 7), ("Go to Logs", 8)],
    "schedule": [("Go to Send", 6), ("Go to Logs", 8)],
    "logs": [("Go to Dashboard", 0), ("Go to Schedule", 7)],
}


class CommandPaletteDialog(QDialog):
    def __init__(self, parent: QWidget, nav_rows: list[tuple[int, str]]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Command Palette")
        self.setModal(True)
        self.resize(520, 360)
        self._rows = nav_rows
        self._selected_idx = -1
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 14)
        v.setSpacing(10)
        self._query = QLineEdit()
        self._query.setPlaceholderText("Type module name…")
        self._query.textChanged.connect(self._refill)
        v.addWidget(self._query)
        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.itemDoubleClicked.connect(lambda _it: self._accept_selected())
        v.addWidget(self._list, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = QPushButton("Open")
        ok.setObjectName("Primary")
        ok.clicked.connect(self._accept_selected)
        row.addWidget(ok)
        v.addLayout(row)
        self._refill()

    def selected_index(self) -> int:
        return self._selected_idx

    def _refill(self) -> None:
        q = self._query.text().strip().lower()
        self._list.clear()
        for idx, label in self._rows:
            if q and q not in label.lower():
                continue
            it = QListWidgetItem(label)
            it.setData(32, idx)
            self._list.addItem(it)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _accept_selected(self) -> None:
        it = self._list.currentItem()
        if it is None:
            return
        self._selected_idx = int(it.data(32))
        self.accept()


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
        self._compact_density = False

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

        right_shell = QWidget()
        right_lay = QVBoxLayout(right_shell)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)

        topbar = QFrame()
        topbar.setObjectName("TopBar")
        tb = QHBoxLayout(topbar)
        tb.setContentsMargins(18, 12, 18, 12)
        tb.setSpacing(12)
        title_wrap = QVBoxLayout()
        title_wrap.setContentsMargins(0, 0, 0, 0)
        title_wrap.setSpacing(2)
        self._page_title = QLabel("Dashboard")
        self._page_title.setObjectName("TopBarTitle")
        self._page_subtitle = QLabel("Quick launch for common workflows")
        self._page_subtitle.setObjectName("TopBarSubtitle")
        self._breadcrumbs = QLabel("Home / Dashboard")
        self._breadcrumbs.setObjectName("TopBarCrumbs")
        title_wrap.addWidget(self._page_title)
        title_wrap.addWidget(self._page_subtitle)
        title_wrap.addWidget(self._breadcrumbs)
        tb.addLayout(title_wrap, 1)
        self._global_search = QLineEdit()
        self._global_search.setObjectName("GlobalSearch")
        self._global_search.setReadOnly(True)
        self._global_search.setPlaceholderText("Search modules or actions (Ctrl+K)")
        self._global_search.setMinimumWidth(300)
        self._global_search.mousePressEvent = (  # type: ignore[method-assign]
            lambda _e: self._open_command_palette()
        )
        tb.addWidget(self._global_search, 0)
        self._quick_actions = QComboBox()
        self._quick_actions.setObjectName("QuickActions")
        self._quick_actions.setMinimumWidth(170)
        tb.addWidget(self._quick_actions, 0)
        self._top_action_btn = QPushButton("Run")
        self._top_action_btn.setObjectName("TopAction")
        self._top_action_btn.clicked.connect(self._run_top_action)
        tb.addWidget(self._top_action_btn, 0)
        self._density_btn = QPushButton("Compact")
        self._density_btn.setCheckable(True)
        self._density_btn.clicked.connect(self._toggle_density)
        tb.addWidget(self._density_btn, 0)
        self._btn_notif = QPushButton("🔔")
        self._btn_notif.setToolTip("Notifications")
        self._btn_notif.setFixedWidth(42)
        self._btn_notif.clicked.connect(
            lambda: self._set_status("Notifications panel is not wired yet.")
        )
        tb.addWidget(self._btn_notif, 0)
        self._btn_settings = QPushButton("⚙")
        self._btn_settings.setToolTip("Settings")
        self._btn_settings.setFixedWidth(42)
        self._btn_settings.clicked.connect(
            lambda: self._set_status("Settings panel is not wired yet.")
        )
        tb.addWidget(self._btn_settings, 0)
        right_lay.addWidget(topbar, 0)

        self._stack = QStackedWidget()
        right_lay.addWidget(self._stack, 1)
        lay.addWidget(right_shell, 1)

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
        self._palette_shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        self._palette_shortcut.activated.connect(self._open_command_palette)

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
        nav_id = NAV[row][0]
        title, subtitle = PAGE_META.get(nav_id, ("WhatsApp Desktop", ""))
        self._page_title.setText(title)
        self._page_subtitle.setText(subtitle)
        self._breadcrumbs.setText(f"Home / {title}")
        self._quick_actions.clear()
        for label, target in QUICK_ACTIONS.get(nav_id, []):
            self._quick_actions.addItem(label, target)
        has_actions = self._quick_actions.count() > 0
        self._quick_actions.setVisible(has_actions)
        self._top_action_btn.setVisible(has_actions)
        self._refresh_page_for_nav(row)

    def _run_top_action(self) -> None:
        target = self._quick_actions.currentData()
        if isinstance(target, int) and 0 <= target < len(NAV):
            self._nav.setCurrentRow(target)

    def _toggle_density(self) -> None:
        self._compact_density = not self._compact_density
        mode = "compact" if self._compact_density else "comfortable"
        self.centralWidget().setProperty("density", mode)
        self.centralWidget().style().unpolish(self.centralWidget())
        self.centralWidget().style().polish(self.centralWidget())
        self._density_btn.setText("Comfort" if self._compact_density else "Compact")

    def _open_command_palette(self) -> None:
        options = [(i, NAV[i][2]) for i in range(len(NAV))]
        dlg = CommandPaletteDialog(self, options)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        idx = dlg.selected_index()
        if 0 <= idx < len(NAV):
            self._nav.setCurrentRow(idx)

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
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication.instance() or QApplication(sys.argv)
    apply_app_theme(app)
    win = ModernMainWindow()
    win.show()
    sys.exit(app.exec())
