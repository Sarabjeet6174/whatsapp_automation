"""
Send Messages — composer (center) + live WhatsApp-style preview (right).
Uses LocalWorkflowController; does not import Tk.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QCloseEvent, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.db.local_access import (
    create_local_scheduled_job,
    fetch_contact_lists,
    fetch_contacts,
    fetch_local_profiles,
    fetch_templates,
    fetch_whatsapp_directory,
    fetch_groups,
    init_local_db,
)
from app.services.constants import SEND_TEMPLATE_CUSTOM, WA_SEND_ID_OFFSET
from app.services.local_workflow_controller import LocalWorkflowController, render_message_template

from app.ui.qt.widgets.chat_preview import ChatPreviewPanel
from app.ui.qt.widgets.send_page_widgets import MessageComposer, SendActionBar, pick_schedule_time


class SendMessagesPage(QWidget):
    """Composer + recipients table + enqueue sends."""

    status_message = Signal(str)

    def __init__(self, workflow: LocalWorkflowController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._workflow = workflow
        self._profiles: list[dict[str, Any]] = []
        self._send_cache: list[dict[str, Any]] = []
        self._view_cache: list[dict[str, Any]] = []
        self._templates: list[dict[str, Any]] = []
        self._groups: list[dict[str, Any]] = []
        self._group_view_cache: list[str] = []
        self._pending_attachments: list[str] = []
        self._contact_lists: list[dict[str, Any]] = []
        self._selected_recipient_keys: set[tuple[str, int]] = set()
        self._selected_group_names_set: set[str] = set()
        self._pending_external_selection: dict[str, Any] | None = None
        self._updating_tables = False

        self._build_ui()
        self._connect_preview()
        init_local_db()
        self.reload_profiles()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._stage_stack = QStackedWidget()
        root.addWidget(self._stage_stack, 1)

        compose_page = QWidget()
        cp = QVBoxLayout(compose_page)
        cp.setContentsMargins(0, 0, 0, 0)
        cp.setSpacing(0)

        main_row = QHBoxLayout()
        main_row.setContentsMargins(16, 16, 16, 8)
        main_row.setSpacing(16)

        left_scroll = QScrollArea()
        left_scroll.setObjectName("ComposerScroll")
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        left_inner = QWidget()
        lv = QVBoxLayout(left_inner)
        lv.setContentsMargins(0, 0, 8, 0)
        lv.setSpacing(16)

        title = QLabel("Send messages")
        title.setProperty("class", "sectionTitle")
        lv.addWidget(title)

        row_pf = QHBoxLayout()
        row_pf.setSpacing(12)
        pl = QLabel("Profile")
        pl.setProperty("class", "fieldLabel")
        row_pf.addWidget(pl, 0)
        self._profile_combo = QComboBox()
        self._profile_combo.setMinimumHeight(40)
        self._profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        row_pf.addWidget(self._profile_combo, 1)
        btn_open = QPushButton("Open WhatsApp")
        btn_open.setObjectName("Primary")
        btn_open.clicked.connect(self._open_profile)
        row_pf.addWidget(btn_open, 0)
        lv.addLayout(row_pf)

        tmpl_row = QHBoxLayout()
        tmpl_row.setSpacing(12)
        tl = QLabel("Template")
        tl.setProperty("class", "fieldLabel")
        tmpl_row.addWidget(tl, 0)
        self._template_combo = QComboBox()
        self._template_combo.setMinimumHeight(40)
        tmpl_row.addWidget(self._template_combo, 1)
        lv.addLayout(tmpl_row)

        self._composer = MessageComposer()
        self._composer.attach_clicked.connect(self._pick_files)
        self._composer.clear_attachments_clicked.connect(self._clear_attachments)
        lv.addWidget(self._composer, 1)

        self._attach_chips_host = QWidget()
        self._attach_chips_layout = QHBoxLayout(self._attach_chips_host)
        self._attach_chips_layout.setContentsMargins(0, 0, 0, 0)
        self._attach_chips_layout.setSpacing(8)
        lv.addWidget(self._attach_chips_host)

        self._attach_drop = QLabel("Drop files here to attach")
        self._attach_drop.setMinimumHeight(40)
        self._attach_drop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._attach_drop.setStyleSheet(
            "border: 1px dashed #475569; border-radius: 10px; color: #94A3B8; "
            "font-size: 12px; background-color: #0F172A;"
        )
        self._attach_drop.setAcceptDrops(True)
        self._attach_drop.dragEnterEvent = self._attach_drag_enter  # type: ignore[method-assign]
        self._attach_drop.dropEvent = self._attach_drop_ev  # type: ignore[method-assign]
        lv.addWidget(self._attach_drop)

        left_scroll.setWidget(left_inner)
        main_row.addWidget(left_scroll, stretch=58)

        self._preview = ChatPreviewPanel()
        main_row.addWidget(self._preview, stretch=42)
        cp.addLayout(main_row, 1)
        next_row = QHBoxLayout()
        next_row.setContentsMargins(16, 0, 16, 12)
        next_row.addStretch(1)
        self._btn_next_stage = QPushButton("← Back to contacts")
        self._btn_next_stage.setObjectName("Primary")
        self._btn_next_stage.setMinimumHeight(42)
        self._btn_next_stage.clicked.connect(self._go_to_recipients_stage)
        next_row.addWidget(self._btn_next_stage)
        cp.addLayout(next_row)
        self._compose_action_bar = SendActionBar()
        self._compose_action_bar.send_clicked.connect(lambda: self._enqueue_send(schedule=False))
        self._compose_action_bar.schedule_clicked.connect(lambda: self._enqueue_send(schedule=True))
        cp.addWidget(self._compose_action_bar)
        self._stage_stack.addWidget(compose_page)

        recipients_page = QWidget()
        wl = QVBoxLayout(recipients_page)
        wl.setContentsMargins(16, 16, 16, 8)
        wl.setSpacing(12)
        nav = QHBoxLayout()
        self._btn_back_stage = QPushButton("Next: Compose message →")
        self._btn_back_stage.clicked.connect(self._go_to_message_stage)
        nav.addWidget(self._btn_back_stage, 0)
        nav.addStretch(1)
        wl.addLayout(nav)

        recipients_scroll = QScrollArea()
        recipients_scroll.setWidgetResizable(True)
        recipients_scroll.setFrameShape(QFrame.Shape.NoFrame)
        recipients_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        recipients_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        recipients_inner = QWidget()
        tv = QVBoxLayout(recipients_inner)
        tv.setSpacing(12)
        tv.setContentsMargins(0, 0, 0, 0)

        self._c_contacts = QCheckBox("Contact lists (CSV)")
        self._c_wa = QCheckBox("WhatsApp directory (search by saved name)")
        self._c_group = QCheckBox("WhatsApp groups")
        self._c_contacts.setChecked(True)
        self._c_contacts.stateChanged.connect(self._on_target_change)
        self._c_wa.stateChanged.connect(self._on_target_change)
        self._c_group.stateChanged.connect(self._on_target_change)
        self._allow_search = QCheckBox("Search sidebar by phone when sending (not needed for search_name rows)")
        self._allow_search.setChecked(True)
        self._allow_search.setToolTip(
            "For contacts with a phone, optionally search the sidebar instead of using chat-by-number. "
            "Rows with search_name always use sidebar search on that name."
        )

        self._list_filter_wrap = QWidget()
        lfw = QHBoxLayout(self._list_filter_wrap)
        lfw.setContentsMargins(0, 0, 0, 0)
        lfw.setSpacing(10)
        list_lbl = QLabel("List filter")
        list_lbl.setProperty("class", "fieldLabel")
        lfw.addWidget(list_lbl)
        self._list_combo = QComboBox()
        self._list_combo.setMinimumHeight(38)
        self._list_combo.currentIndexChanged.connect(self._refresh_recipients)
        lfw.addWidget(self._list_combo, 1)

        contacts_box = QGroupBox("Contacts")
        cb = QHBoxLayout(contacts_box)
        cb.setContentsMargins(12, 16, 12, 12)
        cb.setSpacing(12)
        contacts_left = QWidget()
        cl = QVBoxLayout(contacts_left)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(8)
        cl.addWidget(self._c_contacts)
        cl.addWidget(self._c_wa)
        cl.addWidget(self._c_group)
        cl.addWidget(self._allow_search)
        cl.addWidget(self._list_filter_wrap)
        cl.addStretch(1)
        cb.addWidget(contacts_left, 0)

        self._recipient_table_wrap = QWidget()
        rtw = QVBoxLayout(self._recipient_table_wrap)
        rtw.setContentsMargins(0, 0, 0, 0)
        rtw.setSpacing(8)
        rhead = QHBoxLayout()
        rhead.setSpacing(12)
        rh = QLabel("Contacts / WhatsApp names")
        rh.setProperty("class", "fieldLabel")
        rhead.addWidget(rh)
        self._recipient_stat_lbl = QLabel("0 selected")
        self._recipient_stat_lbl.setProperty("class", "muted")
        rhead.addWidget(self._recipient_stat_lbl)
        rhead.addStretch(1)
        rtw.addLayout(rhead)
        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        self._recipient_search = QLineEdit()
        self._recipient_search.setPlaceholderText("Filter by name, phone, search_name…")
        self._recipient_search.setMinimumHeight(38)
        self._recipient_search.textChanged.connect(self._render_recipients_table)
        search_row.addWidget(self._recipient_search, 1)
        self._select_all_recipients = QCheckBox("Select all visible")
        self._select_all_recipients.setTristate(True)
        self._select_all_recipients.stateChanged.connect(self._on_select_all_recipients_changed)
        search_row.addWidget(self._select_all_recipients)
        rtw.addLayout(search_row)
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["", "Name", "Phone / search", "Source"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setMinimumHeight(620)
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setColumnWidth(0, 44)
        self._table.setColumnWidth(1, 160)
        self._table.setColumnWidth(2, 180)
        self._table.itemChanged.connect(self._on_recipient_item_changed)
        self._table.itemSelectionChanged.connect(self._highlight_recipient_rows)
        rtw.addWidget(self._table)
        cb.addWidget(self._recipient_table_wrap, 1)
        tv.addWidget(contacts_box)

        self._group_section = QGroupBox("Groups")
        gs_wrap = QHBoxLayout(self._group_section)
        gs_wrap.setContentsMargins(12, 16, 12, 12)
        gs_wrap.setSpacing(12)
        groups_right = QWidget()
        gs = QVBoxLayout(groups_right)
        gs.setContentsMargins(0, 0, 0, 0)
        gs.setSpacing(8)
        gh_row = QHBoxLayout()
        gh = QLabel("Groups")
        gh.setProperty("class", "fieldLabel")
        gh_row.addWidget(gh)
        self._group_stat_lbl = QLabel("0 selected")
        self._group_stat_lbl.setProperty("class", "muted")
        gh_row.addWidget(self._group_stat_lbl)
        gh_row.addStretch(1)
        gs.addLayout(gh_row)
        g_search_row = QHBoxLayout()
        g_search_row.setSpacing(10)
        self._group_search = QLineEdit()
        self._group_search.setPlaceholderText("Filter groups…")
        self._group_search.setMinimumHeight(38)
        self._group_search.textChanged.connect(self._render_groups_table)
        g_search_row.addWidget(self._group_search, 1)
        self._select_all_groups = QCheckBox("Select all visible")
        self._select_all_groups.setTristate(True)
        self._select_all_groups.stateChanged.connect(self._on_select_all_groups_changed)
        g_search_row.addWidget(self._select_all_groups)
        g_search_row.addWidget(QPushButton("Reload", clicked=self._load_groups_combo))
        gs.addLayout(g_search_row)
        self._group_table = QTableWidget(0, 2)
        self._group_table.setHorizontalHeaderLabels(["", "Group name"])
        self._group_table.horizontalHeader().setStretchLastSection(True)
        self._group_table.setMinimumHeight(140)
        self._group_table.setAlternatingRowColors(True)
        self._group_table.setShowGrid(True)
        self._group_table.verticalHeader().setVisible(False)
        self._group_table.setColumnWidth(0, 44)
        self._group_table.itemChanged.connect(self._on_group_item_changed)
        gs.addWidget(self._group_table)
        gs_wrap.addWidget(groups_right, 1)
        tv.addWidget(self._group_section)
        self._group_section.setVisible(False)

        recipients_scroll.setWidget(recipients_inner)
        wl.addWidget(recipients_scroll, 1)

        self._action_bar = SendActionBar()
        self._action_bar.send_clicked.connect(lambda: self._enqueue_send(schedule=False))
        self._action_bar.schedule_clicked.connect(lambda: self._enqueue_send(schedule=True))
        wl.addWidget(self._action_bar)
        self._stage_stack.addWidget(recipients_page)
        self._stage_stack.setCurrentIndex(1)

        self._template_combo.currentIndexChanged.connect(self._on_template_picked)
        self._apply_target_visibility()

    def _go_to_recipients_stage(self) -> None:
        self._stage_stack.setCurrentIndex(1)

    def _go_to_message_stage(self) -> None:
        self._stage_stack.setCurrentIndex(0)

    def open_with_selection(self, sel: dict[str, Any]) -> None:
        self._pending_external_selection = dict(sel or {})
        pid = int(self._pending_external_selection.get("profile_id", 0) or 0)
        if pid > 0:
            for i in range(self._profile_combo.count()):
                d = self._profile_combo.itemData(i)
                if isinstance(d, dict) and int(d.get("id", 0)) == pid:
                    self._profile_combo.setCurrentIndex(i)
                    break
        source = str(self._pending_external_selection.get("source", "")).strip().lower()
        self._c_contacts.setChecked(source == "contacts")
        self._c_wa.setChecked(source == "wa")
        self._c_group.setChecked(source == "groups")
        self._on_target_change()
        self._stage_stack.setCurrentIndex(1)

    def _apply_target_visibility(self) -> None:
        has_contacts = self._c_contacts.isChecked()
        has_wa = self._c_wa.isChecked()
        has_group = self._c_group.isChecked()
        has_table_sources = has_contacts or has_wa
        self._group_section.setVisible(has_group)
        self._recipient_table_wrap.setVisible(has_table_sources)
        self._allow_search.setVisible(has_contacts)
        self._list_filter_wrap.setVisible(has_contacts)

    def _connect_preview(self) -> None:
        self._composer.message_edit().textChanged.connect(self._update_preview)
        self._composer.attach_only_checkbox().toggled.connect(self._update_preview)

    def _attach_drag_enter(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def _attach_drop_ev(self, event: QDropEvent) -> None:
        paths: list[str] = []
        for u in event.mimeData().urls():
            p = u.toLocalFile()
            if p and os.path.isfile(p):
                paths.append(os.path.abspath(p))
        self._add_attachment_paths(paths)

    def _pick_files(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        paths, _ = QFileDialog.getOpenFileNames(self, "Attach files")
        self._add_attachment_paths([os.path.abspath(p) for p in paths if p])

    def _add_attachment_paths(self, paths: list[str]) -> None:
        for p in paths:
            if p not in self._pending_attachments and os.path.isfile(p):
                self._pending_attachments.append(p)
        self._rebuild_attachment_chips()
        self._update_preview()

    def _clear_attachments(self) -> None:
        self._pending_attachments.clear()
        self._rebuild_attachment_chips()
        self._update_preview()

    def _remove_attachment(self, path: str) -> None:
        try:
            self._pending_attachments.remove(path)
        except ValueError:
            pass
        self._rebuild_attachment_chips()
        self._update_preview()

    def _rebuild_attachment_chips(self) -> None:
        while self._attach_chips_layout.count():
            it = self._attach_chips_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for p in self._pending_attachments:
            b = QPushButton(f"{os.path.basename(p)}  ×")
            b.setToolTip(p)
            b.clicked.connect(lambda _=False, x=p: self._remove_attachment(x))
            self._attach_chips_layout.addWidget(b)
        self._attach_chips_layout.addStretch(1)

    def _update_preview(self) -> None:
        attach_only = self._composer.attach_only_checkbox().isChecked()
        body = self._composer.message_edit().toPlainText().strip()
        if attach_only and self._pending_attachments:
            self._preview.set_message_text("")
        else:
            self._preview.set_message_text(body if body else " ")
        self._preview.set_attachments(self._pending_attachments)

    def reload_profiles(self) -> None:
        self._profiles = fetch_local_profiles()
        self._workflow.sync_profile_list(self._profiles)
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()
        for p in self._profiles:
            label = f'{p.get("name", "")} ({p.get("phone", "")})'
            self._profile_combo.addItem(label, p)
        self._profile_combo.blockSignals(False)
        if self._profiles:
            self._on_profile_changed()

    def _current_profile(self) -> dict[str, Any] | None:
        i = self._profile_combo.currentIndex()
        if i < 0:
            return None
        return self._profile_combo.itemData(i)

    def _on_profile_changed(self, *_a: Any) -> None:
        p = self._current_profile()
        if not p:
            return
        self._preview.set_sender_name(str(p.get("name", "") or p.get("phone", "")))
        self._load_templates()
        self._load_contact_lists()
        self._load_groups_combo()
        self._refresh_recipients()

    def _load_templates(self) -> None:
        p = self._current_profile()
        self._template_combo.blockSignals(True)
        self._template_combo.clear()
        self._template_combo.addItem(SEND_TEMPLATE_CUSTOM, None)
        if not p:
            self._template_combo.blockSignals(False)
            return
        self._templates = fetch_templates(int(p["id"]))
        for t in self._templates:
            self._template_combo.addItem(t["name"], t)
        self._template_combo.blockSignals(False)

    def _load_groups_combo(self) -> None:
        self._groups = []
        self._group_view_cache.clear()
        self._selected_group_names_set.clear()
        self._group_table.setRowCount(0)
        p = self._current_profile()
        if p:
            try:
                self._groups = fetch_groups(int(p["id"]))
            except Exception:
                self._groups = []
        if (
            self._pending_external_selection
            and str(self._pending_external_selection.get("source", "")).strip().lower() == "groups"
        ):
            wanted = {
                str(x).strip()
                for x in (self._pending_external_selection.get("group_names") or [])
                if str(x).strip()
            }
            self._selected_group_names_set = {x for x in wanted if x}
            self._pending_external_selection = None
        self._render_groups_table()

    def _render_groups_table(self) -> None:
        q = self._group_search.text().strip().lower() if hasattr(self, "_group_search") else ""
        all_names = [str(g.get("name", "")).strip() for g in self._groups]
        all_names = [x for x in all_names if x]
        if not q:
            self._group_view_cache = all_names
        else:
            self._group_view_cache = [x for x in all_names if q in x.lower()]
        self._selected_group_names_set.intersection_update(set(all_names))
        self._updating_tables = True
        self._group_table.setRowCount(len(self._group_view_cache))
        for r, gname in enumerate(self._group_view_cache):
            chk = QTableWidgetItem()
            chk.setFlags(chk.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            chk.setCheckState(
                Qt.CheckState.Checked
                if gname in self._selected_group_names_set
                else Qt.CheckState.Unchecked
            )
            self._group_table.setItem(r, 0, chk)
            self._group_table.setItem(r, 1, QTableWidgetItem(gname))
        self._updating_tables = False
        self._sync_group_select_all_checkbox()
        self._update_group_stats()

    def _sync_group_select_all_checkbox(self) -> None:
        self._select_all_groups.blockSignals(True)
        vis = list(self._group_view_cache)
        if not vis:
            self._select_all_groups.setCheckState(Qt.CheckState.Unchecked)
        else:
            sel = sum(1 for g in vis if g in self._selected_group_names_set)
            if sel == 0:
                self._select_all_groups.setCheckState(Qt.CheckState.Unchecked)
            elif sel == len(vis):
                self._select_all_groups.setCheckState(Qt.CheckState.Checked)
            else:
                self._select_all_groups.setCheckState(Qt.CheckState.PartiallyChecked)
        self._select_all_groups.blockSignals(False)

    def _update_group_stats(self) -> None:
        self._group_stat_lbl.setText(f"{len(self._selected_group_names_set)} selected")

    def _on_select_all_groups_changed(self, state: int) -> None:
        if self._updating_tables:
            return
        st = Qt.CheckState(state)
        if st == Qt.CheckState.PartiallyChecked:
            return
        want = st == Qt.CheckState.Checked
        self._updating_tables = True
        for r, gname in enumerate(self._group_view_cache):
            if want:
                self._selected_group_names_set.add(gname)
            else:
                self._selected_group_names_set.discard(gname)
            it = self._group_table.item(r, 0)
            if it:
                it.setCheckState(Qt.CheckState.Checked if want else Qt.CheckState.Unchecked)
        self._updating_tables = False
        self._update_group_stats()

    def _on_group_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_tables or item.column() != 0:
            return
        row = item.row()
        if row < 0 or row >= len(self._group_view_cache):
            return
        gname = self._group_view_cache[row]
        if item.checkState() == Qt.CheckState.Checked:
            self._selected_group_names_set.add(gname)
        else:
            self._selected_group_names_set.discard(gname)
        self._sync_group_select_all_checkbox()
        self._update_group_stats()

    def _selected_group_names(self) -> list[str]:
        return sorted(self._selected_group_names_set)

    def _on_template_picked(self) -> None:
        data = self._template_combo.currentData()
        if isinstance(data, dict) and data.get("content"):
            self._composer.message_edit().setPlainText(str(data["content"]))
            self._update_preview()

    def _load_contact_lists(self) -> None:
        self._contact_lists = []
        self._list_combo.blockSignals(True)
        self._list_combo.clear()
        self._list_combo.addItem("All lists", None)
        p = self._current_profile()
        if p:
            try:
                self._contact_lists = fetch_contact_lists(int(p["id"]))
                for lst in self._contact_lists:
                    self._list_combo.addItem(str(lst.get("name", "")), int(lst.get("id", 0)))
            except Exception:
                self._contact_lists = []
        self._list_combo.blockSignals(False)

    def _on_target_change(self) -> None:
        self._apply_target_visibility()
        self._refresh_recipients()

    def _selected_sources(self) -> tuple[bool, bool, bool]:
        return self._c_contacts.isChecked(), self._c_wa.isChecked(), self._c_group.isChecked()

    def _refresh_recipients(self) -> None:
        self._table.setRowCount(0)
        self._send_cache.clear()
        self._view_cache.clear()
        self._selected_recipient_keys.clear()
        p = self._current_profile()
        if not p:
            return
        pid = int(p["id"])
        use_contacts, use_wa, _use_group = self._selected_sources()
        rows: list[dict[str, Any]] = []
        if use_wa:
            try:
                for r in fetch_whatsapp_directory(pid):
                    cid = int(r.get("id", 0))
                    nm = (r.get("name") or "").strip()
                    waph = str(r.get("phone", "") or "").strip()
                    if cid <= 0 or not nm:
                        continue
                    rows.append(
                        {
                            "id": WA_SEND_ID_OFFSET + cid,
                            "name": nm,
                            "phone": waph,
                            "email": "",
                            "company": "",
                            "extra": {},
                            "display_phone": waph if waph else "sidebar name",
                            "list_name": "WhatsApp",
                            "source_type": "wa_directory",
                        }
                    )
            except Exception:
                pass
        if use_contacts:
            try:
                selected_list_id = self._list_combo.currentData()
                lists = self._contact_lists if self._contact_lists else fetch_contact_lists(pid)
                if selected_list_id is not None:
                    lists = [x for x in lists if int(x.get("id", 0)) == int(selected_list_id)]
                for lst in lists:
                    for c in fetch_contacts(pid, int(lst["id"])):
                        ex = c.get("extra") or {}
                        if not isinstance(ex, dict):
                            ex = {}
                        phone = str(c.get("phone", "") or "").strip()
                        sn = str(ex.get("search_name", "") or "").strip()
                        display_phone = phone if phone else (f"search: {sn}" if sn else "—")
                        rows.append(
                            {
                                "id": int(c.get("id", 0)),
                                "name": str(c.get("name", "")),
                                "phone": phone,
                                "email": str(c.get("email", "")),
                                "company": str(c.get("company", "")),
                                "extra": ex,
                                "display_phone": display_phone,
                                "list_name": str(lst.get("name", "")),
                                "source_type": "contacts",
                            }
                        )
            except Exception:
                pass
        seen: set[tuple[str, int]] = set()
        for c in rows:
            iid = int(c["id"])
            skey = (str(c.get("source_type", "")), iid)
            if iid <= 0 or skey in seen:
                continue
            seen.add(skey)
            self._send_cache.append(c)
        if self._pending_external_selection:
            src = str(self._pending_external_selection.get("source", "")).strip().lower()
            if src == "contacts":
                wanted_ids = {int(x) for x in (self._pending_external_selection.get("contact_ids") or [])}
                self._selected_recipient_keys = {
                    ("contacts", int(c.get("id", 0)))
                    for c in self._send_cache
                    if str(c.get("source_type", "")) == "contacts" and int(c.get("id", 0)) in wanted_ids
                }
                self._pending_external_selection = None
            elif src == "wa":
                wanted_names = {
                    str(x).strip().lower()
                    for x in (self._pending_external_selection.get("wa_names") or [])
                    if str(x).strip()
                }
                self._selected_recipient_keys = {
                    ("wa_directory", int(c.get("id", 0)))
                    for c in self._send_cache
                    if str(c.get("source_type", "")) == "wa_directory"
                    and str(c.get("name", "")).strip().lower() in wanted_names
                }
                self._pending_external_selection = None
        self._render_recipients_table()

    def _render_recipients_table(self) -> None:
        q = self._recipient_search.text().strip().lower()
        if not q:
            self._view_cache = list(self._send_cache)
        else:

            def _match(row: dict[str, Any]) -> bool:
                ex = row.get("extra") or {}
                sn = str(ex.get("search_name", "")).lower() if isinstance(ex, dict) else ""
                return (
                    q in str(row.get("name", "")).lower()
                    or q in str(row.get("phone", "")).lower()
                    or q in str(row.get("list_name", "")).lower()
                    or q in str(row.get("display_phone", "")).lower()
                    or (bool(sn) and q in sn)
                )

            self._view_cache = [c for c in self._send_cache if _match(c)]
        valid_keys = {
            (str(c.get("source_type", "")), int(c.get("id", 0)))
            for c in self._send_cache
            if int(c.get("id", 0)) > 0
        }
        self._selected_recipient_keys.intersection_update(valid_keys)
        self._updating_tables = True
        self._table.setRowCount(len(self._view_cache))
        for r, c in enumerate(self._view_cache):
            chk = QTableWidgetItem()
            chk.setFlags(chk.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            key = (str(c.get("source_type", "")), int(c.get("id", 0)))
            chk.setCheckState(
                Qt.CheckState.Checked if key in self._selected_recipient_keys else Qt.CheckState.Unchecked
            )
            self._table.setItem(r, 0, chk)
            self._table.setItem(r, 1, QTableWidgetItem(c["name"]))
            note = str(c.get("display_phone", c.get("phone", "")))
            self._table.setItem(r, 2, QTableWidgetItem(note))
            self._table.setItem(r, 3, QTableWidgetItem(c.get("list_name", "")))
        self._updating_tables = False
        self._sync_recipient_select_all_checkbox()
        self._update_recipient_stats()
        self._highlight_recipient_rows()

    def _sync_recipient_select_all_checkbox(self) -> None:
        self._select_all_recipients.blockSignals(True)
        keys: list[tuple[str, int]] = []
        for c in self._view_cache:
            k = (str(c.get("source_type", "")), int(c.get("id", 0)))
            if k[1] > 0:
                keys.append(k)
        if not keys:
            self._select_all_recipients.setCheckState(Qt.CheckState.Unchecked)
        else:
            sel = sum(1 for k in keys if k in self._selected_recipient_keys)
            if sel == 0:
                self._select_all_recipients.setCheckState(Qt.CheckState.Unchecked)
            elif sel == len(keys):
                self._select_all_recipients.setCheckState(Qt.CheckState.Checked)
            else:
                self._select_all_recipients.setCheckState(Qt.CheckState.PartiallyChecked)
        self._select_all_recipients.blockSignals(False)

    def _update_recipient_stats(self) -> None:
        n = sum(
            1
            for c in self._view_cache
            if int(c.get("id", 0)) > 0
            and (str(c.get("source_type", "")), int(c.get("id", 0))) in self._selected_recipient_keys
        )
        self._recipient_stat_lbl.setText(f"{n} selected")

    def _on_select_all_recipients_changed(self, state: int) -> None:
        if self._updating_tables:
            return
        st = Qt.CheckState(state)
        if st == Qt.CheckState.PartiallyChecked:
            return
        want = st == Qt.CheckState.Checked
        self._updating_tables = True
        for r, c in enumerate(self._view_cache):
            key = (str(c.get("source_type", "")), int(c.get("id", 0)))
            if key[1] <= 0:
                continue
            if want:
                self._selected_recipient_keys.add(key)
            else:
                self._selected_recipient_keys.discard(key)
            it = self._table.item(r, 0)
            if it:
                it.setCheckState(Qt.CheckState.Checked if want else Qt.CheckState.Unchecked)
        self._updating_tables = False
        self._update_recipient_stats()
        self._highlight_recipient_rows()

    def _highlight_recipient_rows(self) -> None:
        for r, c in enumerate(self._view_cache):
            key = (str(c.get("source_type", "")), int(c.get("id", 0)))
            sel = key in self._selected_recipient_keys and key[1] > 0
            for col in range(self._table.columnCount()):
                it = self._table.item(r, col)
                if not it:
                    continue
                if sel:
                    it.setBackground(QBrush(QColor("#334155")))
                else:
                    it.setData(Qt.ItemDataRole.BackgroundRole, None)

    def _on_recipient_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_tables or item.column() != 0:
            return
        row = item.row()
        if row < 0 or row >= len(self._view_cache):
            return
        c = self._view_cache[row]
        key = (str(c.get("source_type", "")), int(c.get("id", 0)))
        if key[1] <= 0:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._selected_recipient_keys.add(key)
        else:
            self._selected_recipient_keys.discard(key)
        self._sync_recipient_select_all_checkbox()
        self._update_recipient_stats()
        self._highlight_recipient_rows()

    def _selected_row_indices(self) -> list[int]:
        out: list[int] = []
        for r, c in enumerate(self._view_cache):
            key = (str(c.get("source_type", "")), int(c.get("id", 0)))
            if key in self._selected_recipient_keys:
                out.append(r)
        return out

    def _open_profile(self) -> None:
        p = self._current_profile()
        if not p:
            QMessageBox.information(self, "Profile", "Select a profile first.")
            return
        st, err = self._workflow.ensure_local_profile_ready(
            int(p["id"]), str(p["phone"]), str(p.get("name", ""))
        )
        if st is None:
            QMessageBox.critical(self, "Open profile", err)
            self.status_message.emit(f"Open failed: {err}")
            return
        self.status_message.emit("WhatsApp profile ready.")

    def _validate_outgoing(self, body: str, paths: list[str]) -> str | None:
        if self._composer.attach_only_checkbox().isChecked():
            if not paths:
                return "Turn off attachment-only mode or add files."
            return None
        if not body and not paths:
            return "Enter a message or attach a file."
        return None

    def _enqueue_send(self, *, schedule: bool = False) -> None:
        p = self._current_profile()
        if not p:
            QMessageBox.information(self, "Send", "Select a profile first.")
            return
        template_body = self._composer.message_edit().toPlainText()
        paths = [os.path.abspath(x) for x in self._pending_attachments if os.path.isfile(x)]
        err = self._validate_outgoing(template_body.strip(), paths)
        if err:
            QMessageBox.information(self, "Send", err)
            return

        if not schedule:
            st, oerr = self._workflow.ensure_local_profile_ready(
                int(p["id"]), str(p["phone"]), str(p.get("name", ""))
            )
            if st is None:
                QMessageBox.critical(self, "Send", f"Could not open profile:\n{oerr}")
                return

        use_contacts, use_wa, use_group = self._selected_sources()
        if not (use_contacts or use_wa or use_group):
            QMessageBox.information(self, "Send", "Select at least one recipient source.")
            return
        selected_groups = self._selected_group_names() if use_group else []
        if use_contacts and use_wa:
            target_mode = "mixed"
        elif use_wa and not use_contacts and not use_group:
            target_mode = "wa_directory"
        elif use_group and not use_contacts and not use_wa:
            target_mode = "group"
        else:
            target_mode = "contacts"
        custom_vars: dict[str, str] = {}
        attachment_only = self._composer.attach_only_checkbox().isChecked()
        allow_search = (use_wa or self._allow_search.isChecked()) if use_contacts else use_wa

        items: list[dict[str, Any]] = []
        if use_contacts or use_wa:
            for idx in self._selected_row_indices():
                c = self._view_cache[idx]
                src = str(c.get("source_type", "contacts"))
                is_wa_row = src == "wa_directory"
                ex = c.get("extra") or {}
                if not isinstance(ex, dict):
                    ex = {}
                sn = str(ex.get("search_name", "")).strip()
                if is_wa_row:
                    receiver = str(c.get("name", ""))
                    force_search = True
                elif sn:
                    receiver = sn
                    force_search = True
                else:
                    receiver = str(c.get("phone", ""))
                    force_search = False
                items.append(
                    {
                        "item_type": "contact",
                        "receiver": receiver,
                        "name": str(c.get("name", "")),
                        "rendered": render_message_template(template_body, c, custom_vars),
                        "force_allow_search": force_search,
                    }
                )
        if use_group:
            rendered_group_body = render_message_template(template_body, {}, custom_vars)
            for gname in selected_groups:
                items.append(
                    {
                        "item_type": "group",
                        "receiver": gname,
                        "name": gname,
                        "rendered": rendered_group_body,
                    }
                )
        if not items:
            QMessageBox.information(
                self,
                "Send",
                "Select at least one recipient in the table and/or one or more groups.",
            )
            return

        job: dict[str, Any] = {
            "profile_id": int(p["id"]),
            "profile_phone": str(p["phone"]),
            "profile_name": str(p.get("name", "")),
            "target_mode": target_mode,
            "allow_search": allow_search,
            "items": items,
            "attachment_paths": paths,
            "attachment_only_no_caption": attachment_only,
        }
        if schedule:
            run_at = pick_schedule_time(self, initial=datetime.now() + timedelta(minutes=5))
            if run_at is None:
                return
            if run_at <= datetime.now():
                QMessageBox.information(self, "Schedule", "Schedule time must be in the future.")
                return
            create_local_scheduled_job(int(p["id"]), run_at, job)
            self.status_message.emit(
                f"Scheduled {len(items)} message(s) for {run_at.strftime('%Y-%m-%d %H:%M')}."
            )
        else:
            self._workflow.enqueue_send_job(job)
            self.status_message.emit(f"Queued {len(items)} message(s).")

    def closeEvent(self, event: QCloseEvent) -> None:
        event.accept()
