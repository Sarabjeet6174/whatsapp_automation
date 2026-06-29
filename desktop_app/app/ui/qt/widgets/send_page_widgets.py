"""Reusable pieces for Send Messages page — composer shell, action bar, schedule dialog."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

_EMOJI = "😀 😃 😄 😁 😆 🙂 😊 😍 🥰 😘 🎉 ✨ 🔥 ❤️ 👍 👎 🙏 ✅ ❌ 📎 💼 🏠".split()


class MessageComposer(QFrame):
    """Left column: large text area, emoji strip, attachment shortcuts."""

    textChanged = Signal()
    attach_clicked = Signal()
    clear_attachments_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 22, 22)
        v.setSpacing(14)

        self._message = QTextEdit()
        self._message.setMinimumHeight(160)
        self._message.setPlaceholderText("Type a message...")
        self._message.textChanged.connect(self.textChanged.emit)
        v.addWidget(self._message)

        tool = QHBoxLayout()
        tool.setSpacing(8)
        emoji_scroll = QScrollArea()
        emoji_scroll.setFixedHeight(48)
        emoji_scroll.setWidgetResizable(True)
        emoji_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        emoji_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        emoji_scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        er = QHBoxLayout(inner)
        er.setContentsMargins(0, 0, 0, 0)
        er.setSpacing(6)
        em_font = QFont()
        em_font.setFamilies(["Segoe UI Emoji", "Segoe UI Symbol", "Segoe UI"])
        em_font.setPixelSize(17)
        self._emoji_buttons: list[QPushButton] = []
        for em in _EMOJI:
            b = QPushButton(em)
            b.setObjectName("EmojiPick")
            b.setFont(em_font)
            b.setToolTip(f"Insert {em}")
            b.clicked.connect(lambda _=False, e=em: self._insert_emoji(e))
            er.addWidget(b)
            self._emoji_buttons.append(b)
        er.addStretch(1)
        emoji_scroll.setWidget(inner)
        tool.addWidget(emoji_scroll, 1)
        self._btn_attach = QPushButton("Attach")
        self._btn_clear_att = QPushButton("Clear files")
        self._btn_attach.clicked.connect(self.attach_clicked.emit)
        self._btn_clear_att.clicked.connect(self.clear_attachments_clicked.emit)
        tool.addWidget(self._btn_attach)
        tool.addWidget(self._btn_clear_att)
        v.addLayout(tool)

        self._attach_only = QCheckBox("Attachment only (no text)")
        self._attach_only.setToolTip("Send files without a caption.")
        v.addWidget(self._attach_only)

    def _insert_emoji(self, em: str) -> None:
        self._message.insertPlainText(em)

    def message_edit(self) -> QTextEdit:
        return self._message

    def attach_only_checkbox(self) -> QCheckBox:
        return self._attach_only


class SendActionBar(QFrame):
    """Bottom sticky bar with Send Now + Schedule."""

    send_clicked = Signal()
    schedule_clicked = Signal()
    pause_clicked = Signal()
    resume_clicked = Signal()
    cancel_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SendActionBar")
        self.setStyleSheet(
            """
            QFrame#SendActionBar {
                background-color: #1E293B;
                border-top: 1px solid #334155;
                border-radius: 0px;
            }
            """
        )
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 12, 16, 12)
        h.setSpacing(12)
        self._pause = QPushButton("Pause")
        self._pause.setMinimumHeight(44)
        self._pause.setToolTip("Pause before the next recipient (current message may still finish).")
        self._pause.clicked.connect(self.pause_clicked.emit)
        self._resume = QPushButton("Resume")
        self._resume.setMinimumHeight(44)
        self._resume.clicked.connect(self.resume_clicked.emit)
        self._cancel = QPushButton("Cancel queue")
        self._cancel.setMinimumHeight(44)
        self._cancel.setToolTip("Stop remaining sends and clear the queue.")
        self._cancel.clicked.connect(self.cancel_clicked.emit)
        h.addWidget(self._pause)
        h.addWidget(self._resume)
        h.addWidget(self._cancel)
        h.addStretch(1)
        self._send = QPushButton("Send Now")
        self._send.setObjectName("Primary")
        self._send.setMinimumHeight(44)
        self._send.clicked.connect(self.send_clicked.emit)
        self._sched = QPushButton("Schedule…")
        self._sched.setMinimumHeight(44)
        self._sched.clicked.connect(self.schedule_clicked.emit)
        h.addWidget(self._send)
        h.addWidget(self._sched)
        h.addStretch(1)


def pick_schedule_time(parent: QWidget, initial: datetime | None = None) -> datetime | None:
    """Modal datetime picker; returns None if cancelled."""
    from PySide6.QtCore import QDateTime
    from PySide6.QtWidgets import QDateTimeEdit, QVBoxLayout

    dlg = QDialog(parent)
    dlg.setWindowTitle("Schedule send")
    dlg.setModal(True)
    v = QVBoxLayout(dlg)
    dt = QDateTimeEdit(dlg)
    dt.setCalendarPopup(True)
    dt.setDisplayFormat("yyyy-MM-dd HH:mm")
    dt.setMinimumHeight(40)
    if initial:
        dt.setDateTime(QDateTime.fromSecsSinceEpoch(int(initial.timestamp())))
    else:
        dt.setDateTime(QDateTime.currentDateTime().addSecs(300))
    v.addWidget(dt)
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    v.addWidget(bb)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    qdt = dt.dateTime()
    if not qdt.isValid():
        return None
    return qdt.toPython()


__all__ = ["MessageComposer", "SendActionBar", "pick_schedule_time"]
