"""
Send Messages — guided 4-step flow (recipients → compose → draft → send).
Uses LocalWorkflowController; theme-safe fixed dark styles via global QSS.
"""

from __future__ import annotations

import math
import os
from datetime import datetime
from typing import Any

from PySide6.QtCore import QDate, QDateTime, QTime, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QCloseEvent, QDragEnterEvent, QDropEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from app.db.local_access import (
    create_local_scheduled_job,
    fetch_contact_lists,
    fetch_contacts,
    fetch_local_profiles,
    fetch_templates,
    fetch_groups,
    init_local_db,
)
from app.services.constants import SEND_TEMPLATE_CUSTOM
from app.services.local_workflow_controller import LocalWorkflowController, render_message_template
from app.whatsapp.sender import normalize_phone

from app.ui.qt.widgets.chat_preview import ChatPreviewPanel
from app.ui.qt.widgets.send_flow_widgets import (
    RecipientSelectionBar,
    SendFlowNavFooter,
    SendFlowStepper,
)
from app.ui.qt.widgets.send_page_widgets import MessageComposer

_TABLE_ROW_HEIGHT = 28
_TOOLBAR_H = 30


class SendMessagesPage(QWidget):
    """Guided send workflow with stepper, recipient panel, and step accordion."""

    status_message = Signal(str)
    _send_log_line = Signal(str)
    _send_finished = Signal()

    def __init__(self, workflow: LocalWorkflowController, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SendMessagesPage")
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
        self._draft_saved = False
        self._current_step = 1
        self._active_source_tab = "lists"
        self._preview_recipient_index = 0
        self._send_operation_active = False

        self._build_ui()
        self._connect_preview()
        self._bind_shortcuts()
        self._send_log_line.connect(self._append_send_log)
        self._send_finished.connect(self._finish_send_operation_ui)
        init_local_db()
        self.reload_profiles()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 12)
        root.setSpacing(6)

        self._stepper = SendFlowStepper(compact=True)
        root.addWidget(self._stepper)

        self._step_stack = QStackedWidget()
        self._step_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # —— Step 1: recipients — table fills the page, scroll to browse all ——
        step1 = QFrame()
        step1.setObjectName("SendLeftPanel")
        s1 = QVBoxLayout(step1)
        s1.setContentsMargins(8, 8, 8, 8)
        s1.setSpacing(6)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self._tab_lists = QPushButton("Lists")
        self._tab_lists.setObjectName("SourceTab")
        self._tab_lists.setCheckable(True)
        self._tab_lists.setChecked(True)
        self._tab_lists.setFixedHeight(_TOOLBAR_H)
        self._tab_groups = QPushButton("Groups")
        self._tab_groups.setObjectName("SourceTab")
        self._tab_groups.setCheckable(True)
        self._tab_groups.setFixedHeight(_TOOLBAR_H)
        self._tab_grp = QButtonGroup(self)
        self._tab_grp.setExclusive(True)
        self._tab_grp.addButton(self._tab_lists)
        self._tab_grp.addButton(self._tab_groups)
        self._tab_lists.toggled.connect(self._on_source_tab_changed)
        toolbar.addWidget(self._tab_lists)
        toolbar.addWidget(self._tab_groups)

        self._list_combo = QComboBox()
        self._list_combo.setFixedHeight(_TOOLBAR_H)
        self._list_combo.setMinimumWidth(130)
        self._list_combo.currentIndexChanged.connect(self._refresh_recipients)
        toolbar.addWidget(self._list_combo)

        self._recipient_search = QLineEdit()
        self._recipient_search.setPlaceholderText("Filter name or phone…")
        self._recipient_search.setFixedHeight(_TOOLBAR_H)
        self._recipient_search.textChanged.connect(self._on_recipient_filter_changed)
        toolbar.addWidget(self._recipient_search, 1)

        self._group_search = QLineEdit()
        self._group_search.setPlaceholderText("Filter groups…")
        self._group_search.setFixedHeight(_TOOLBAR_H)
        self._group_search.textChanged.connect(self._on_group_filter_changed)
        self._group_search.setVisible(False)
        toolbar.addWidget(self._group_search, 1)

        self._select_all_recipients = QCheckBox("All")
        self._select_all_recipients.setTristate(True)
        self._select_all_recipients.stateChanged.connect(self._on_select_all_recipients_changed)
        toolbar.addWidget(self._select_all_recipients)

        self._select_all_groups = QCheckBox("All")
        self._select_all_groups.setTristate(True)
        self._select_all_groups.stateChanged.connect(self._on_select_all_groups_changed)
        self._select_all_groups.setVisible(False)
        toolbar.addWidget(self._select_all_groups)

        self._btn_reload_groups = QPushButton("Reload")
        self._btn_reload_groups.setFixedHeight(_TOOLBAR_H)
        self._btn_reload_groups.clicked.connect(self._load_groups_combo)
        self._btn_reload_groups.setVisible(False)
        toolbar.addWidget(self._btn_reload_groups)

        self._profile_combo = QComboBox()
        self._profile_combo.setFixedHeight(_TOOLBAR_H)
        self._profile_combo.setMinimumWidth(170)
        self._profile_combo.currentIndexChanged.connect(self._on_profile_changed)
        toolbar.addWidget(self._profile_combo)

        btn_open = QPushButton("Open WhatsApp")
        btn_open.setObjectName("Primary")
        btn_open.setFixedHeight(_TOOLBAR_H)
        btn_open.clicked.connect(self._open_profile)
        toolbar.addWidget(btn_open)
        s1.addLayout(toolbar)

        self._selection_bar = RecipientSelectionBar()
        s1.addWidget(self._selection_bar)

        self._lists_table_wrap = QFrame()
        self._lists_table_wrap.setObjectName("RecipientTableCard")
        ltw = QVBoxLayout(self._lists_table_wrap)
        ltw.setContentsMargins(0, 0, 0, 0)
        ltw.setSpacing(0)
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["", "Name", "Phone", "Source"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_TABLE_ROW_HEIGHT)
        self._table.setColumnWidth(0, 36)
        self._table.setShowGrid(False)
        self._table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._table.itemChanged.connect(self._on_recipient_item_changed)
        self._table.itemSelectionChanged.connect(self._highlight_recipient_rows)
        self._table_empty = QLabel("No contacts found. Pick a list or import contacts.")
        self._table_empty.setProperty("class", "muted")
        self._table_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ltw.addWidget(self._table, 1)
        ltw.addWidget(self._table_empty, 0)

        self._groups_table_wrap = QFrame()
        self._groups_table_wrap.setObjectName("RecipientTableCard")
        gtw = QVBoxLayout(self._groups_table_wrap)
        gtw.setContentsMargins(0, 0, 0, 0)
        gtw.setSpacing(0)
        self._group_table = QTableWidget(0, 2)
        self._group_table.setHorizontalHeaderLabels(["", "Group name"])
        self._group_table.horizontalHeader().setStretchLastSection(True)
        self._group_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._group_table.setAlternatingRowColors(True)
        self._group_table.verticalHeader().setVisible(False)
        self._group_table.verticalHeader().setDefaultSectionSize(_TABLE_ROW_HEIGHT)
        self._group_table.setColumnWidth(0, 36)
        self._group_table.setShowGrid(False)
        self._group_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._group_table.itemChanged.connect(self._on_group_item_changed)
        self._group_empty = QLabel("No groups found. Sync from WhatsApp or click Reload.")
        self._group_empty.setProperty("class", "muted")
        self._group_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gtw.addWidget(self._group_table, 1)
        gtw.addWidget(self._group_empty, 0)

        self._recipient_stack = QStackedWidget()
        self._recipient_stack.addWidget(self._lists_table_wrap)
        self._recipient_stack.addWidget(self._groups_table_wrap)
        s1.addWidget(self._recipient_stack, 1)

        self._list_filter_wrap = self._list_combo
        self._group_search_row = self._group_search

        step1.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._step_stack.addWidget(step1)

        # —— Step 2: Compose ——
        step2_scroll = QScrollArea()
        step2_scroll.setWidgetResizable(True)
        step2_scroll.setFrameShape(QFrame.Shape.NoFrame)
        step2_inner = QWidget()
        s2 = QVBoxLayout(step2_inner)
        s2.setContentsMargins(8, 8, 8, 8)
        s2.setSpacing(12)
        s2.addWidget(QLabel("Compose your message and attach files if needed."))
        s2.itemAt(0).widget().setProperty("class", "muted")
        tmpl_row = QHBoxLayout()
        tmpl_row.addWidget(QLabel("Template"))
        tmpl_row.itemAt(0).widget().setProperty("class", "fieldLabel")
        self._template_combo = QComboBox()
        self._template_combo.setMinimumHeight(38)
        tmpl_row.addWidget(self._template_combo, 1)
        s2.addLayout(tmpl_row)
        self._composer = MessageComposer()
        self._composer.attach_clicked.connect(self._pick_files)
        self._composer.clear_attachments_clicked.connect(self._clear_attachments)
        s2.addWidget(self._composer)
        self._attach_chips_host = QWidget()
        self._attach_chips_layout = QHBoxLayout(self._attach_chips_host)
        self._attach_chips_layout.setContentsMargins(0, 0, 0, 0)
        self._attach_chips_layout.setSpacing(8)
        s2.addWidget(self._attach_chips_host)
        self._attach_drop = QLabel("Drop files here to attach")
        self._attach_drop.setObjectName("AttachDropZone")
        self._attach_drop.setMinimumHeight(40)
        self._attach_drop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._attach_drop.setAcceptDrops(True)
        self._attach_drop.dragEnterEvent = self._attach_drag_enter  # type: ignore[method-assign]
        self._attach_drop.dropEvent = self._attach_drop_ev  # type: ignore[method-assign]
        s2.addWidget(self._attach_drop)
        s2.addStretch(1)
        step2_scroll.setWidget(step2_inner)
        self._step_stack.addWidget(step2_scroll)

        # —— Step 3: Preview ——
        step3 = QWidget()
        s3 = QVBoxLayout(step3)
        s3.setContentsMargins(12, 4, 12, 8)
        s3.setSpacing(8)
        title3 = QLabel("Preview")
        title3.setProperty("class", "sectionTitle")
        title3.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s3.addWidget(title3)
        hint3 = QLabel("See how your message looks for each recipient.")
        hint3.setWordWrap(True)
        hint3.setProperty("class", "muted")
        hint3.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s3.addWidget(hint3)
        nav = QHBoxLayout()
        nav.setSpacing(12)
        self._preview_prev = QPushButton("‹ Previous")
        self._preview_prev.setObjectName("OutlineBlue")
        self._preview_prev.clicked.connect(lambda: self._shift_preview_recipient(-1))
        self._preview_recipient_label = QLabel("—")
        self._preview_recipient_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_recipient_label.setMinimumWidth(160)
        self._preview_next = QPushButton("Next ›")
        self._preview_next.setObjectName("OutlineBlue")
        self._preview_next.clicked.connect(lambda: self._shift_preview_recipient(1))
        nav.addStretch(1)
        nav.addWidget(self._preview_prev)
        nav.addWidget(self._preview_recipient_label, 1)
        nav.addWidget(self._preview_next)
        nav.addStretch(1)
        s3.addLayout(nav)
        preview_row = QHBoxLayout()
        preview_row.addStretch(1)
        self._preview = ChatPreviewPanel()
        self._preview.setMinimumSize(320, 300)
        self._preview.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        preview_row.addWidget(self._preview)
        preview_row.addStretch(1)
        s3.addLayout(preview_row, 1)
        self._step_stack.addWidget(step3)

        # —— Step 4: Send or schedule ——
        step4 = QFrame()
        step4.setObjectName("Card")
        s4 = QVBoxLayout(step4)
        s4.setContentsMargins(20, 20, 20, 20)
        s4.setSpacing(14)
        step4_title = QLabel("Schedule")
        step4_title.setProperty("class", "sectionTitle")
        s4.addWidget(step4_title)
        step4_hint = QLabel("Choose when to send your messages.")
        step4_hint.setProperty("class", "muted")
        s4.addWidget(step4_hint)

        self._send_mode_group = QButtonGroup(self)
        self._card_send_now, self._radio_send_now = self._build_schedule_option_card(
            "Send now", "Queue immediately after confirmation", checked=True
        )
        self._card_schedule, self._radio_schedule = self._build_schedule_option_card(
            "Schedule", "Pick date and time", checked=False
        )
        self._send_mode_group.addButton(self._radio_send_now, 0)
        self._send_mode_group.addButton(self._radio_schedule, 1)
        s4.addWidget(self._card_send_now)
        s4.addWidget(self._card_schedule)

        self._schedule_fields = QFrame()
        self._schedule_fields.setObjectName("ScheduleFields")
        self._schedule_fields.setVisible(False)
        sched_row = QHBoxLayout(self._schedule_fields)
        sched_row.setContentsMargins(0, 0, 0, 0)
        sched_row.setSpacing(12)
        date_col = QVBoxLayout()
        date_col.setSpacing(6)
        date_lbl = QLabel("Date")
        date_lbl.setProperty("class", "fieldLabel")
        date_col.addWidget(date_lbl)
        self._schedule_date = QDateEdit()
        self._schedule_date.setObjectName("ScheduleDateEdit")
        self._schedule_date.setCalendarPopup(True)
        self._schedule_date.setDisplayFormat("dd-MM-yyyy")
        self._schedule_date.setMinimumDate(QDate.currentDate())
        self._schedule_date.setMinimumHeight(40)
        date_col.addWidget(self._schedule_date)
        sched_row.addLayout(date_col, 1)
        time_col = QVBoxLayout()
        time_col.setSpacing(6)
        time_lbl = QLabel("Time")
        time_lbl.setProperty("class", "fieldLabel")
        time_col.addWidget(time_lbl)
        self._schedule_time = QTimeEdit()
        self._schedule_time.setObjectName("ScheduleTimeEdit")
        self._schedule_time.setDisplayFormat("HH:mm")
        self._schedule_time.setTime(QTime(9, 0))
        self._schedule_time.setMinimumHeight(40)
        time_col.addWidget(self._schedule_time)
        sched_row.addLayout(time_col, 1)
        s4.addWidget(self._schedule_fields)

        self._radio_send_now.toggled.connect(self._on_send_mode_changed)
        self._radio_schedule.toggled.connect(self._on_send_mode_changed)
        self._set_default_schedule_date()

        act = QHBoxLayout()
        self._btn_send_now = QPushButton("Send Now")
        self._btn_send_now.setObjectName("Primary")
        self._btn_send_now.setMinimumHeight(44)
        self._btn_send_now.clicked.connect(lambda: self._enqueue_send(schedule=False))
        self._btn_schedule = QPushButton("Schedule…")
        self._btn_schedule.setObjectName("OutlineBlue")
        self._btn_schedule.setMinimumHeight(44)
        self._btn_schedule.clicked.connect(lambda: self._enqueue_send(schedule=True))
        act.addWidget(self._btn_send_now)
        act.addWidget(self._btn_schedule)
        act.addStretch(1)
        s4.addLayout(act)
        self._update_schedule_action_buttons()
        self._send_log_label = QLabel("Live activity")
        self._send_log_label.setProperty("class", "fieldLabel")
        self._send_log_label.setVisible(False)
        s4.addWidget(self._send_log_label)
        self._send_activity = QTextEdit()
        self._send_activity.setObjectName("SendLiveLog")
        self._send_activity.setReadOnly(True)
        self._send_activity.setPlaceholderText("Send progress appears here when you click Send Now…")
        self._send_activity.setMinimumHeight(120)
        self._send_activity.setMaximumHeight(180)
        self._send_activity.setVisible(False)
        s4.addWidget(self._send_activity)
        self._btn_cancel_operation = QPushButton("Cancel operation")
        self._btn_cancel_operation.setObjectName("SendCancelOperation")
        self._btn_cancel_operation.setVisible(False)
        self._btn_cancel_operation.clicked.connect(self._cancel_send_operation)
        s4.addWidget(self._btn_cancel_operation)
        s4.addStretch(1)
        self._step_stack.addWidget(step4)

        root.addWidget(self._step_stack, 1)

        self._nav_footer = SendFlowNavFooter()
        self._nav_footer.setMinimumHeight(56)
        self._nav_footer.previous_clicked.connect(self._go_previous)
        self._nav_footer.next_clicked.connect(self._go_next)
        root.addWidget(self._nav_footer)

        self._template_combo.currentIndexChanged.connect(self._on_template_picked)
        self._show_step(1)

    @staticmethod
    def _estimate_delivery_eta(recipient_count: int, group_count: int) -> str:
        total = recipient_count + group_count
        if total <= 0:
            return "—"
        minutes = max(1, math.ceil(total * 10 / 60))
        return f"{minutes} min"

    def _build_schedule_option_card(
        self, title: str, subtitle: str, *, checked: bool
    ) -> tuple[QFrame, QRadioButton]:
        card = QFrame()
        card.setObjectName("ScheduleOptionCard")
        card.setProperty("selected", "true" if checked else "false")
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 12, 14, 12)
        row.setSpacing(12)
        radio = QRadioButton()
        radio.setObjectName("ScheduleOptionRadio")
        radio.setChecked(checked)
        radio.setCursor(Qt.CursorShape.PointingHandCursor)
        row.addWidget(radio, 0, Qt.AlignmentFlag.AlignTop)
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("ScheduleOptionTitle")
        sub_lbl = QLabel(subtitle)
        sub_lbl.setProperty("class", "muted")
        text_col.addWidget(title_lbl)
        text_col.addWidget(sub_lbl)
        row.addLayout(text_col, 1)
        return card, radio

    def _on_send_mode_changed(self) -> None:
        schedule_mode = self._radio_schedule.isChecked()
        self._schedule_fields.setVisible(schedule_mode)
        self._card_send_now.setProperty("selected", "false" if schedule_mode else "true")
        self._card_schedule.setProperty("selected", "true" if schedule_mode else "false")
        for w in (self._card_send_now, self._card_schedule):
            w.style().unpolish(w)
            w.style().polish(w)
        if schedule_mode:
            self._ensure_schedule_date_valid()
        self._update_schedule_action_buttons()

    def _update_schedule_action_buttons(self) -> None:
        schedule_mode = self._radio_schedule.isChecked()
        self._btn_send_now.setVisible(not schedule_mode)
        if schedule_mode:
            self._btn_schedule.setText("Schedule")
            self._btn_schedule.setObjectName("Primary")
        else:
            self._btn_schedule.setText("Schedule…")
            self._btn_schedule.setObjectName("OutlineBlue")
        self._btn_schedule.style().unpolish(self._btn_schedule)
        self._btn_schedule.style().polish(self._btn_schedule)

    def _ensure_schedule_date_valid(self) -> None:
        today = QDate.currentDate()
        if not self._schedule_date.date().isValid() or self._schedule_date.date() < today:
            self._schedule_date.setDate(today.addDays(1))

    def _set_default_schedule_date(self) -> None:
        self._schedule_date.setDate(QDate.currentDate().addDays(1))

    def _read_schedule_datetime(self) -> datetime | None:
        qd = self._schedule_date.date()
        qt = self._schedule_time.time()
        if not qd.isValid() or not qt.isValid():
            return None
        return datetime(qd.year(), qd.month(), qd.day(), qt.hour(), qt.minute())

    def _on_source_tab_changed(self, lists_checked: bool) -> None:
        self._active_source_tab = "lists" if lists_checked else "groups"
        show_lists = lists_checked
        self._list_combo.setVisible(show_lists)
        self._recipient_search.setVisible(show_lists)
        self._select_all_recipients.setVisible(show_lists)
        self._group_search.setVisible(not show_lists)
        self._select_all_groups.setVisible(not show_lists)
        self._btn_reload_groups.setVisible(not show_lists)
        self._recipient_stack.setCurrentIndex(0 if show_lists else 1)
        self._refresh_flow_state()

    def _on_recipient_filter_changed(self) -> None:
        self._render_recipients_table()

    def _on_group_filter_changed(self) -> None:
        self._render_groups_table()

    def _has_recipients_selected(self) -> bool:
        return bool(self._selected_recipient_keys) or bool(self._selected_group_names_set)

    def _can_advance_from_step(self, step: int) -> bool:
        if step == 1:
            return self._has_recipients_selected()
        if step == 2:
            return self._has_composed_content()
        if step == 3:
            return self._has_composed_content() and self._has_recipients_selected()
        return False

    def _refresh_flow_state(self) -> None:
        self._stepper.set_state(self._current_step, self._current_step)

        rec_n = len(self._selected_recipient_keys)
        grp_n = len(self._selected_group_names_set)
        total = len(self._view_cache) if self._active_source_tab == "lists" else len(self._group_view_cache)
        self._selection_bar.set_stats(
            recipients=rec_n,
            groups=grp_n,
            total=total,
            eta=self._estimate_delivery_eta(rec_n, grp_n),
        )

        self._nav_footer.configure(
            self._current_step,
            can_prev=self._current_step > 1,
            can_next=self._can_advance_from_step(self._current_step),
        )

    def _show_step(self, step: int) -> None:
        step = max(1, min(4, step))
        self._current_step = step
        self._step_stack.setCurrentIndex(step - 1)
        self._stepper.set_state(step, step)
        if step == 3:
            self._preview_recipient_index = 0
            self._update_preview()
        self._refresh_flow_state()

    def _go_next(self) -> None:
        if self._current_step == 1:
            if not self._has_recipients_selected():
                QMessageBox.information(self, "Step 1", "Select at least one contact or group.")
                return
            self._show_step(2)
        elif self._current_step == 2:
            if not self._has_composed_content():
                QMessageBox.information(self, "Step 2", "Enter a message or add attachments.")
                return
            self._show_step(3)
        elif self._current_step == 3:
            if not self._has_composed_content():
                QMessageBox.information(self, "Step 3", "Message or attachments required.")
                return
            self._draft_saved = True
            self._update_preview()
            self._show_step(4)
            self.status_message.emit("Draft saved.")

    def _go_previous(self) -> None:
        if self._current_step > 1:
            if self._current_step == 4:
                self._draft_saved = False
            self._show_step(self._current_step - 1)

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
        if source == "groups":
            self._tab_groups.setChecked(True)
        else:
            self._tab_lists.setChecked(True)
        self._refresh_recipients()
        self._show_step(1)

    def _connect_preview(self) -> None:
        self._composer.message_edit().textChanged.connect(self._update_preview)
        self._composer.message_edit().textChanged.connect(self._on_draft_content_changed)
        self._composer.attach_only_checkbox().toggled.connect(self._update_preview)
        self._composer.attach_only_checkbox().toggled.connect(self._on_draft_content_changed)

    def _bind_shortcuts(self) -> None:
        self._send_sc = QShortcut(QKeySequence("Ctrl+Return"), self)
        self._send_sc.activated.connect(self._on_send_shortcut)
        self._schedule_sc = QShortcut(QKeySequence("Ctrl+Shift+S"), self)
        self._schedule_sc.activated.connect(lambda: self._enqueue_send(schedule=True))

    def _on_send_shortcut(self) -> None:
        if self._radio_schedule.isChecked():
            self._enqueue_send(schedule=True)
        else:
            self._enqueue_send(schedule=False)

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
        self._on_draft_content_changed()
        self._update_preview()

    def _clear_attachments(self) -> None:
        self._pending_attachments.clear()
        self._rebuild_attachment_chips()
        self._on_draft_content_changed()
        self._update_preview()

    def _remove_attachment(self, path: str) -> None:
        try:
            self._pending_attachments.remove(path)
        except ValueError:
            pass
        self._rebuild_attachment_chips()
        self._on_draft_content_changed()
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

    def _preview_recipient_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for idx in self._selected_row_indices():
            c = self._view_cache[idx]
            label = str(c.get("name") or c.get("phone") or "Contact").strip() or "Contact"
            entries.append({"label": label, "contact": dict(c)})
        for gname in self._selected_group_names():
            entries.append(
                {
                    "label": gname,
                    "contact": {"name": gname, "phone": "", "email": "", "company": "", "extra": {}},
                }
            )
        return entries

    def _shift_preview_recipient(self, delta: int) -> None:
        entries = self._preview_recipient_entries()
        if not entries:
            return
        self._preview_recipient_index = (self._preview_recipient_index + delta) % len(entries)
        self._update_preview()

    def _append_send_log(self, line: str) -> None:
        ts = QDateTime.currentDateTime().toString("HH:mm:ss")
        self._send_activity.append(f"[{ts}] {line}")
        sb = self._send_activity.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_send_progress(self, event_type: str, message: str) -> None:
        self._send_log_line.emit(message)
        if event_type in ("queue_finished", "queue_cancelled"):
            self._send_log_line.emit("You can close this page or start another send.")
            self._send_finished.emit()

    def _show_send_operation_ui(self, profile_phone: str) -> None:
        self._send_operation_active = True
        self._send_log_label.setVisible(True)
        self._send_activity.clear()
        self._send_activity.setVisible(True)
        self._btn_cancel_operation.setVisible(True)
        self._btn_send_now.setEnabled(False)
        self._btn_schedule.setEnabled(False)
        self._workflow.set_send_progress_handler(profile_phone, self._on_send_progress)

    def _finish_send_operation_ui(self) -> None:
        p = self._current_profile()
        if p:
            self._workflow.set_send_progress_handler(str(p["phone"]), None)
        self._send_operation_active = False
        self._btn_cancel_operation.setVisible(False)
        self._btn_send_now.setEnabled(True)
        self._btn_schedule.setEnabled(True)
        self._update_schedule_action_buttons()

    def _cancel_send_operation(self) -> None:
        p = self._current_profile()
        if not p:
            return
        n = self._workflow.cancel_send_queue(str(p["phone"]))
        extra = f" ({n} pending job(s) removed)" if n else ""
        self._send_log_line.emit(f"Cancel requested{extra}.")
        self.status_message.emit("Send operation cancelled.")

    def _update_preview(self) -> None:
        attach_only = self._composer.attach_only_checkbox().isChecked()
        template_body = self._composer.message_edit().toPlainText()
        entries = self._preview_recipient_entries()
        if entries:
            self._preview_recipient_index = min(self._preview_recipient_index, len(entries) - 1)
            entry = entries[self._preview_recipient_index]
            self._preview_recipient_label.setText(entry["label"])
            self._preview.set_recipient_name(entry["label"])
            rendered = render_message_template(template_body, entry["contact"], {})
        else:
            self._preview_recipient_index = 0
            self._preview_recipient_label.setText("—")
            self._preview.set_recipient_name("Recipient")
            rendered = template_body
        if attach_only and self._pending_attachments:
            self._preview.set_message_text("")
        else:
            self._preview.set_message_text(rendered if rendered.strip() else " ")
        self._preview.set_attachments(self._pending_attachments)
        has_multi = len(entries) > 1
        self._preview_prev.setEnabled(has_multi)
        self._preview_next.setEnabled(has_multi)
        self._refresh_flow_state()

    def _has_composed_content(self) -> bool:
        body = self._composer.message_edit().toPlainText().strip()
        if body:
            return True
        return bool(self._pending_attachments)

    def _on_draft_content_changed(self, *_a: Any) -> None:
        if self._current_step >= 3:
            self._draft_saved = False
        self._refresh_flow_state()

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
        self._load_templates()
        self._load_contact_lists()
        self._load_groups_combo()
        self._refresh_recipients()
        self._refresh_flow_state()

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
            filtered = all_names
        else:
            filtered = [x for x in all_names if q in x.lower()]
        self._group_view_cache = filtered
        self._selected_group_names_set.intersection_update(set(all_names))
        page_items = self._group_view_cache
        self._updating_tables = True
        self._group_table.setRowCount(len(page_items))
        for r, gname in enumerate(page_items):
            chk = QTableWidgetItem()
            chk.setFlags(chk.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            chk.setCheckState(
                Qt.CheckState.Checked if gname in self._selected_group_names_set else Qt.CheckState.Unchecked
            )
            self._group_table.setItem(r, 0, chk)
            self._group_table.setItem(r, 1, QTableWidgetItem(gname))
        self._updating_tables = False
        self._sync_group_select_all_checkbox()
        has_rows = len(self._group_view_cache) > 0
        self._group_table.setVisible(has_rows)
        self._group_empty.setVisible(not has_rows)
        self._refresh_flow_state()

    def _sync_group_select_all_checkbox(self) -> None:
        self._select_all_groups.blockSignals(True)
        page_items = self._group_view_cache
        if not page_items:
            self._select_all_groups.setCheckState(Qt.CheckState.Unchecked)
        else:
            sel = sum(1 for g in page_items if g in self._selected_group_names_set)
            if sel == 0:
                self._select_all_groups.setCheckState(Qt.CheckState.Unchecked)
            elif sel == len(page_items):
                self._select_all_groups.setCheckState(Qt.CheckState.Checked)
            else:
                self._select_all_groups.setCheckState(Qt.CheckState.PartiallyChecked)
        self._select_all_groups.blockSignals(False)

    def _on_select_all_groups_changed(self, state: int) -> None:
        if self._updating_tables:
            return
        st = Qt.CheckState(state)
        if st == Qt.CheckState.PartiallyChecked:
            return
        want = st == Qt.CheckState.Checked
        page_items = self._group_view_cache
        self._updating_tables = True
        for r, gname in enumerate(page_items):
            if want:
                self._selected_group_names_set.add(gname)
            else:
                self._selected_group_names_set.discard(gname)
            it = self._group_table.item(r, 0)
            if it:
                it.setCheckState(Qt.CheckState.Checked if want else Qt.CheckState.Unchecked)
        self._updating_tables = False
        self._refresh_flow_state()

    def _on_group_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating_tables or item.column() != 0:
            return
        row = item.row()
        page_items = self._group_view_cache
        if row < 0 or row >= len(page_items):
            return
        gname = page_items[row]
        if item.checkState() == Qt.CheckState.Checked:
            self._selected_group_names_set.add(gname)
        else:
            self._selected_group_names_set.discard(gname)
        self._sync_group_select_all_checkbox()
        self._refresh_flow_state()

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

    def _refresh_recipients(self) -> None:
        self._table.setRowCount(0)
        self._send_cache.clear()
        self._view_cache.clear()
        if not self._pending_external_selection:
            self._selected_recipient_keys.clear()
        p = self._current_profile()
        if not p:
            return
        pid = int(p["id"])
        rows: list[dict[str, Any]] = []
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
                    display_phone = phone if phone else "—"
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
        self._render_recipients_table()

    def _render_recipients_table(self) -> None:
        q = self._recipient_search.text().strip().lower()
        if not q:
            filtered = list(self._send_cache)
        else:

            def _match(row: dict[str, Any]) -> bool:
                return (
                    q in str(row.get("name", "")).lower()
                    or q in str(row.get("phone", "")).lower()
                    or q in str(row.get("list_name", "")).lower()
                    or q in str(row.get("display_phone", "")).lower()
                )

            filtered = [c for c in self._send_cache if _match(c)]
        self._view_cache = filtered
        valid_keys = {
            (str(c.get("source_type", "")), int(c.get("id", 0)))
            for c in self._send_cache
            if int(c.get("id", 0)) > 0
        }
        self._selected_recipient_keys.intersection_update(valid_keys)
        page_items = self._view_cache
        self._updating_tables = True
        self._table.setRowCount(len(page_items))
        for r, c in enumerate(page_items):
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
        self._highlight_recipient_rows()
        has_rows = len(self._view_cache) > 0
        self._table.setVisible(has_rows)
        self._table_empty.setVisible(not has_rows)
        self._refresh_flow_state()

    def _sync_recipient_select_all_checkbox(self) -> None:
        self._select_all_recipients.blockSignals(True)
        page_items = self._view_cache
        keys: list[tuple[str, int]] = []
        for c in page_items:
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

    def _on_select_all_recipients_changed(self, state: int) -> None:
        if self._updating_tables:
            return
        st = Qt.CheckState(state)
        if st == Qt.CheckState.PartiallyChecked:
            return
        want = st == Qt.CheckState.Checked
        page_items = self._view_cache
        self._updating_tables = True
        for r, c in enumerate(page_items):
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
        self._highlight_recipient_rows()
        self._refresh_flow_state()

    def _highlight_recipient_rows(self) -> None:
        page_items = self._view_cache
        for r, c in enumerate(page_items):
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
        page_items = self._view_cache
        if row < 0 or row >= len(page_items):
            return
        c = page_items[row]
        key = (str(c.get("source_type", "")), int(c.get("id", 0)))
        if key[1] <= 0:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._selected_recipient_keys.add(key)
        else:
            self._selected_recipient_keys.discard(key)
        self._sync_recipient_select_all_checkbox()
        self._highlight_recipient_rows()
        self._refresh_flow_state()

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
        if self._current_step != 4 or not self._draft_saved:
            QMessageBox.information(self, "Send", "Complete all steps and save your draft first (use Next on step 3).")
            return
        if not self._has_recipients_selected():
            QMessageBox.information(self, "Send", "Select at least one contact or group (step 1).")
            return
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

        use_contacts = bool(self._selected_recipient_keys)
        use_group = bool(self._selected_group_names_set)
        selected_groups = self._selected_group_names() if use_group else []
        if use_group and not use_contacts:
            target_mode = "group"
        else:
            target_mode = "contacts"
        custom_vars: dict[str, str] = {}
        attachment_only = self._composer.attach_only_checkbox().isChecked()

        items: list[dict[str, Any]] = []
        skipped_no_phone = 0
        if use_contacts:
            for idx in self._selected_row_indices():
                c = self._view_cache[idx]
                phone = str(c.get("phone", "") or "").strip()
                digits = normalize_phone(phone)
                if not digits:
                    skipped_no_phone += 1
                    continue
                items.append(
                    {
                        "item_type": "contact",
                        "receiver": digits,
                        "name": str(c.get("name", "")),
                        "rendered": render_message_template(template_body, c, custom_vars),
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
            if skipped_no_phone:
                QMessageBox.information(
                    self,
                    "Send",
                    "No selected contacts have a saved phone number. "
                    "Contacts without a number are skipped.",
                )
            else:
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
            "allow_search": False,
            "items": items,
            "attachment_paths": paths,
            "attachment_only_no_caption": attachment_only,
        }
        if schedule:
            if not self._radio_schedule.isChecked():
                QMessageBox.information(
                    self,
                    "Schedule",
                    "Select the Schedule option above, then pick a date and time.",
                )
                return
            run_at = self._read_schedule_datetime()
            if run_at is None:
                QMessageBox.information(self, "Schedule", "Pick a valid date and time.")
                return
            if run_at <= datetime.now():
                QMessageBox.information(self, "Schedule", "Schedule time must be in the future.")
                return
            try:
                create_local_scheduled_job(int(p["id"]), run_at, job)
            except Exception as e:
                QMessageBox.critical(self, "Schedule", f"Could not save scheduled job:\n{e}")
                return
            when = run_at.strftime("%d-%m-%Y %H:%M")
            self.status_message.emit(f"Scheduled {len(items)} message(s) for {when}.")
            QMessageBox.information(
                self,
                "Scheduled",
                f"Scheduled {len(items)} message(s) for {when}.\n\n"
                "View or edit on the Schedule page. Keep this app running until send time.",
            )
        else:
            self._workflow.enqueue_send_job(job)
            self._show_send_operation_ui(str(p["phone"]))
            self._send_log_line.emit(f"Queued {len(items)} message(s).")
            if skipped_no_phone:
                self._send_log_line.emit(f"Skipped {skipped_no_phone} contact(s) without a phone number.")
            msg = f"Queued {len(items)} message(s)."
            if skipped_no_phone:
                msg += f" Skipped {skipped_no_phone} without a phone number."
            self.status_message.emit(msg)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._send_operation_active:
            p = self._current_profile()
            if p:
                self._workflow.set_send_progress_handler(str(p["phone"]), None)
        event.accept()
