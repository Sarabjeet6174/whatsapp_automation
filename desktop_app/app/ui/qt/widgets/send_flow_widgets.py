"""Stepper + accordion panels for the Send Messages guided flow."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

_STEP_META = (
    ("Select Recipients", "Choose numbers or groups"),
    ("Compose Message", "Write your message"),
    ("Save Draft", "Review and save"),
    ("Send or Schedule", "Send now or schedule"),
)


class SendFlowStepper(QWidget):
    """Horizontal 4-step progress indicator."""

    step_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None, *, compact: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("SendFlowStepper")
        if compact:
            self.setProperty("compact", "true")
            self.setMaximumHeight(48)
        self._compact = compact
        self._current = 1
        self._max_unlocked = 1
        h = QHBoxLayout(self)
        h.setContentsMargins(8 if compact else 0, 4 if compact else 0, 8 if compact else 0, 4 if compact else 0)
        h.setSpacing(0)
        self._nodes: list[QPushButton] = []
        self._subs: list[QLabel] = []
        circle = 28 if compact else 40
        for i, (title, sub) in enumerate(_STEP_META, start=1):
            if i > 1:
                arrow = QLabel("→")
                arrow.setProperty("class", "stepArrow")
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                h.addWidget(arrow, 0)
            cw = QVBoxLayout()
            cw.setSpacing(0 if compact else 2)
            btn = QPushButton(str(i))
            btn.setObjectName("StepCircle")
            btn.setFixedSize(circle, circle)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, n=i: self.step_clicked.emit(n))
            self._nodes.append(btn)
            t = QLabel(title if not compact else title.split()[0])  # "Select", "Compose", ...
            t.setProperty("class", "stepTitleCompact" if compact else "stepTitle")
            t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            s = QLabel(sub)
            s.setProperty("class", "stepSub")
            s.setAlignment(Qt.AlignmentFlag.AlignCenter)
            s.setWordWrap(True)
            s.setVisible(not compact)
            self._subs.append(s)
            cw.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)
            cw.addWidget(t)
            if not compact:
                cw.addWidget(s)
            box = QWidget()
            box.setLayout(cw)
            h.addWidget(box, 1)
        h.addStretch(0)

    def set_state(self, current: int, max_unlocked: int) -> None:
        self._current = max(1, min(4, current))
        self._max_unlocked = max(1, min(4, max_unlocked))
        for i, btn in enumerate(self._nodes, start=1):
            btn.setProperty("active", "true" if i == self._current else "false")
            btn.setProperty("done", "true" if i < self._current else "false")
            btn.setProperty("locked", "false")
            btn.setEnabled(False)
            btn.setCursor(Qt.CursorShape.ArrowCursor)
            btn.style().unpolish(btn)
            btn.style().polish(btn)


class RecipientSelectionBar(QFrame):
    """Thin horizontal strip — selected counts without stealing table space."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RecipientSelectionBar")
        self.setFixedHeight(32)
        h = QHBoxLayout(self)
        h.setContentsMargins(12, 0, 12, 0)
        h.setSpacing(8)
        self._text = QLabel("0 selected · 0 groups · scroll to browse all contacts")
        self._text.setObjectName("SelectionBarText")
        h.addWidget(self._text, 1)

    def set_stats(self, *, recipients: int, groups: int, total: int, eta: str) -> None:
        self._text.setText(
            f"{recipients} selected  ·  {groups} groups  ·  {total} in list  ·  Est. {eta}"
        )


class RecipientSelectionSummary(QFrame):
    """Right-side selection summary — matches HTML wizard step 1 panel."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("RecipientSelectionSummary")
        self.setFixedWidth(228)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        head = QLabel("Selected")
        head.setObjectName("SelectionSummaryTitle")
        head.setContentsMargins(14, 12, 14, 8)
        root.addWidget(head)

        body = QVBoxLayout()
        body.setContentsMargins(14, 4, 14, 14)
        body.setSpacing(14)

        rec_block = QVBoxLayout()
        rec_block.setSpacing(2)
        self._rec_value = QLabel("0")
        self._rec_value.setObjectName("SelectionStatValue")
        self._rec_value.setProperty("accent", "green")
        self._rec_label = QLabel("recipients")
        self._rec_label.setProperty("class", "selectionStatLabel")
        rec_block.addWidget(self._rec_value)
        rec_block.addWidget(self._rec_label)
        body.addLayout(rec_block)

        grp_block = QVBoxLayout()
        grp_block.setSpacing(2)
        self._grp_value = QLabel("0")
        self._grp_value.setProperty("accent", "purple")
        self._grp_value.setObjectName("SelectionStatValue")
        self._grp_label = QLabel("groups")
        self._grp_label.setProperty("class", "selectionStatLabel")
        grp_block.addWidget(self._grp_value)
        grp_block.addWidget(self._grp_label)
        body.addLayout(grp_block)

        line = QFrame()
        line.setObjectName("SelectionDivider")
        line.setFixedHeight(1)
        body.addWidget(line)

        eta_block = QVBoxLayout()
        eta_block.setSpacing(4)
        eta_caption = QLabel("Estimated delivery")
        eta_caption.setProperty("class", "selectionStatLabel")
        self._eta_value = QLabel("—")
        self._eta_value.setObjectName("SelectionEtaValue")
        eta_block.addWidget(eta_caption)
        eta_block.addWidget(self._eta_value)
        body.addLayout(eta_block)

        body.addStretch(1)
        root.addLayout(body, 1)

        for lbl in (self._rec_value, self._grp_value):
            font = QFont("Segoe UI", 22, QFont.Weight.Bold)
            lbl.setFont(font)
            lbl.setMinimumHeight(30)
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)

    def set_stats(self, *, recipients: int, groups: int, eta: str) -> None:
        self._rec_value.setText(str(recipients))
        self._grp_value.setText(str(groups))
        self._eta_value.setText(eta)


class SummaryStatCard(QFrame):
    def __init__(self, title: str, accent: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SummaryStatCard")
        self.setProperty("accent", accent)
        self.setMinimumWidth(148)
        self.setMinimumHeight(76)
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(4)
        self._value = QLabel("0")
        self._value.setObjectName("SummaryValue")
        self._value.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._value.setMinimumHeight(28)
        val_font = QFont("Segoe UI", 20, QFont.Weight.Bold)
        self._value.setFont(val_font)
        self._title = QLabel(title)
        self._title.setProperty("class", "muted")
        self._title.setWordWrap(True)
        v.addWidget(self._value)
        v.addWidget(self._title)

    def set_value(self, n: int | str) -> None:
        self._value.setText(str(n))


class SendStepAccordionItem(QFrame):
    """One collapsible step panel (header + body)."""

    toggled = Signal(int)

    def __init__(self, step: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.step = step
        self.setObjectName("SendStepAccordionItem")
        self._unlocked = step == 1
        self._expanded = step == 1
        title, sub = _STEP_META[step - 1]
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._header = QPushButton()
        self._header.setObjectName("StepAccordionHeader")
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        hh = QHBoxLayout(self._header)
        hh.setContentsMargins(14, 12, 14, 12)
        hh.setSpacing(10)
        self._num = QLabel(f"Step {step}")
        self._num.setProperty("class", "fieldLabel")
        self._htitle = QLabel(title)
        self._htitle.setProperty("class", "stepTitle")
        self._lock = QLabel("🔒")
        self._lock.setVisible(False)
        self._badge = QLabel("Waiting")
        self._badge.setObjectName("StepBadge")
        hh.addWidget(self._num)
        hh.addWidget(self._htitle, 1)
        hh.addWidget(self._lock)
        hh.addWidget(self._badge)
        self._header.clicked.connect(lambda: self.toggled.emit(self.step))
        root.addWidget(self._header)

        self._body_host = QWidget()
        self._body_host.setObjectName("StepAccordionBody")
        self._body_layout = QVBoxLayout(self._body_host)
        self._body_layout.setContentsMargins(14, 8, 14, 14)
        self._body_layout.setSpacing(10)
        root.addWidget(self._body_host)
        self._sub = QLabel(sub)
        self._sub.setProperty("class", "muted")
        self._body_layout.addWidget(self._sub)
        self.set_expanded(step == 1)

    def body_layout(self) -> QVBoxLayout:
        return self._body_layout

    def set_unlocked(self, unlocked: bool) -> None:
        self._unlocked = unlocked
        self._lock.setVisible(not unlocked)
        self._header.setEnabled(unlocked)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded and self._unlocked
        self._body_host.setVisible(self._expanded)
        self.setProperty("expanded", "true" if self._expanded else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def set_badge(self, text: str) -> None:
        self._badge.setText(text)


class SendFlowNavFooter(QFrame):
    """Wizard footer — Previous / Next only."""

    previous_clicked = Signal()
    next_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SendFlowFooter")
        h = QHBoxLayout(self)
        h.setContentsMargins(20, 14, 20, 14)
        h.setSpacing(12)
        self._prev = QPushButton("← Previous")
        self._prev.clicked.connect(self.previous_clicked.emit)
        h.addWidget(self._prev)
        h.addStretch(1)
        self._next = QPushButton("Next →")
        self._next.setObjectName("Primary")
        self._next.clicked.connect(self.next_clicked.emit)
        h.addWidget(self._next)

    def configure(self, step: int, *, can_prev: bool, can_next: bool) -> None:
        self._prev.setEnabled(can_prev)
        if step >= 4:
            self._next.setVisible(False)
        else:
            self._next.setVisible(True)
            self._next.setEnabled(can_next)


class SendFlowFooter(QFrame):
    cancel_clicked = Signal()
    save_draft_clicked = Signal()
    schedule_clicked = Signal()
    send_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SendFlowFooter")
        h = QHBoxLayout(self)
        h.setContentsMargins(20, 14, 20, 14)
        h.setSpacing(12)
        self._cancel = QPushButton("Cancel")
        self._cancel.clicked.connect(self.cancel_clicked.emit)
        h.addWidget(self._cancel)
        h.addStretch(1)
        self._save = QPushButton("Save Draft")
        self._save.setObjectName("OutlineGreen")
        self._save.clicked.connect(self.save_draft_clicked.emit)
        self._sched = QPushButton("Schedule")
        self._sched.setObjectName("OutlineBlue")
        self._sched.clicked.connect(self.schedule_clicked.emit)
        self._send = QPushButton("Send Now")
        self._send.setObjectName("Primary")
        self._send.clicked.connect(self.send_clicked.emit)
        h.addWidget(self._save)
        h.addWidget(self._sched)
        h.addWidget(self._send)

    def set_actions_enabled(self, *, save: bool, schedule: bool, send: bool) -> None:
        self._save.setEnabled(save)
        self._sched.setEnabled(schedule)
        self._send.setEnabled(send)


__all__ = [
    "SendFlowStepper",
    "SendStepAccordionItem",
    "SummaryStatCard",
    "RecipientSelectionSummary",
    "RecipientSelectionBar",
    "SendFlowFooter",
    "SendFlowNavFooter",
]
