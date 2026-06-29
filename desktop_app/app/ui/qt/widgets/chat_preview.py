"""WhatsApp-style message preview (phone frame + outbound bubble), matching send_messages.html."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class ChatPreviewPanel(QFrame):
    """Phone-style preview: header, chat surface, green outbound bubble, attachment hint."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ChatPreviewPhone")
        self.setMinimumSize(300, 320)
        self.setMaximumWidth(420)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 10)
        outer.setSpacing(0)

        self._header = QLabel("Recipient")
        self._header.setObjectName("PhonePreviewHeader")
        outer.addWidget(self._header)

        self._chat = QFrame()
        self._chat.setObjectName("PhonePreviewChat")
        chat_outer = QVBoxLayout(self._chat)
        chat_outer.setContentsMargins(14, 14, 14, 14)
        chat_outer.setSpacing(0)

        bubble_wrap = QHBoxLayout()
        bubble_wrap.setContentsMargins(0, 0, 0, 0)
        bubble_wrap.addStretch(1)

        self._bubble = QFrame()
        self._bubble.setObjectName("WaBubble")
        bubble_lay = QVBoxLayout(self._bubble)
        bubble_lay.setContentsMargins(10, 9, 10, 9)
        bubble_lay.setSpacing(8)

        self._attachment_row = QWidget()
        self._attachment_layout = QHBoxLayout(self._attachment_row)
        self._attachment_layout.setContentsMargins(0, 0, 0, 0)
        self._attachment_layout.setSpacing(8)
        bubble_lay.addWidget(self._attachment_row)
        self._attachment_row.setVisible(False)

        self._text_label = QLabel("Your message preview will appear here.")
        self._text_label.setObjectName("BubbleSent")
        self._text_label.setWordWrap(True)
        self._text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._text_label.setFont(QFont("Segoe UI", 10))
        self._text_label.setMaximumWidth(260)
        bubble_lay.addWidget(self._text_label)

        bubble_wrap.addWidget(self._bubble, alignment=Qt.AlignmentFlag.AlignRight)
        chat_outer.addLayout(bubble_wrap)
        chat_outer.addStretch(1)

        self._attach_hint = QLabel("")
        self._attach_hint.setObjectName("PreviewAttachmentHint")
        self._attach_hint.setVisible(False)
        chat_outer.addWidget(self._attach_hint, alignment=Qt.AlignmentFlag.AlignRight)

        outer.addWidget(self._chat, 1)

        self._hint = QLabel("Updates as you type.")
        self._hint.setObjectName("ChatPreviewHint")
        self._hint.setContentsMargins(0, 8, 0, 0)
        outer.addWidget(self._hint)

    def set_recipient_name(self, name: str) -> None:
        n = (name or "").strip()
        self._header.setText(n if n else "Recipient")

    def set_sender_name(self, name: str) -> None:
        """Backward compatible — preview header shows the recipient, not the profile."""
        self.set_recipient_name(name)

    def clear_attachments(self) -> None:
        while self._attachment_layout.count():
            item = self._attachment_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def set_attachments(self, paths: list[str]) -> None:
        self.clear_attachments()
        shown = 0
        for p in paths[:4]:
            if not os.path.isfile(p):
                continue
            thumb = QLabel()
            thumb.setFixedSize(QSize(120, 90))
            thumb.setObjectName("ChatPreviewThumb")
            pix = QPixmap(p)
            if not pix.isNull():
                thumb.setPixmap(
                    pix.scaled(
                        120,
                        90,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                thumb.setScaledContents(True)
            else:
                thumb.setText(Path(p).name[:14] or "FILE")
                thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._attachment_layout.addWidget(thumb)
            shown += 1
        self._attachment_row.setVisible(shown > 0)
        if shown:
            extra = len([p for p in paths if os.path.isfile(p)]) - shown
            hint = f"📎 {shown} attachment{'s' if shown != 1 else ''}"
            if extra > 0:
                hint += f" (+{extra} more)"
            self._attach_hint.setText(hint)
            self._attach_hint.setVisible(True)
        else:
            self._attach_hint.clear()
            self._attach_hint.setVisible(False)

    def set_message_text(self, text: str) -> None:
        t = (text or "").strip()
        self._text_label.setText(t if t else "Your message preview will appear here.")
        self._text_label.setVisible(bool(t))
