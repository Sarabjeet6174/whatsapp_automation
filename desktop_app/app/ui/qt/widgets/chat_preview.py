"""WhatsApp-style live message preview (dark chat background, green outbound bubble)."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class ChatPreviewPanel(QWidget):
    """
    Right panel: scrollable conversation preview with one outbound bubble (self).
    Attachments render as small thumbnails above the text bubble.
    """

    BG = "#0B141A"
    BUBBLE = "#005C4B"
    TEXT = "#E9EDEF"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ChatSurface")
        self.setStyleSheet(
            f"""
            QWidget#ChatSurface {{
                background-color: {self.BG};
                border-radius: 12px;
                border: 1px solid #1F2C34;
            }}
            """
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        head_row = QHBoxLayout()
        self._profile_label = QLabel("You")
        self._profile_label.setStyleSheet(
            "color: #E9EDEF; font-size: 15px; font-weight: 700; padding-bottom: 2px;"
        )
        head_row.addWidget(self._profile_label)
        head_row.addStretch(1)
        title = QLabel("Live preview")
        title.setStyleSheet("color: #8696A0; font-size: 11px; font-weight: 600; letter-spacing: 0.5px;")
        head_row.addWidget(title)
        outer.addLayout(head_row)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet("background: transparent;")

        self._bubble_host = QWidget()
        self._bubble_host.setStyleSheet(f"background-color: {self.BG};")
        self._bubble_layout = QVBoxLayout(self._bubble_host)
        self._bubble_layout.setContentsMargins(8, 8, 8, 24)
        self._bubble_layout.setSpacing(10)
        self._bubble_layout.addStretch(1)

        self._attachment_row = QWidget()
        self._attachment_layout = QHBoxLayout(self._attachment_row)
        self._attachment_layout.setContentsMargins(0, 0, 0, 0)
        self._attachment_layout.setSpacing(8)
        self._attachment_layout.addStretch(1)

        att_wrap = QHBoxLayout()
        att_wrap.addStretch(1)
        att_wrap.addWidget(self._attachment_row)
        self._bubble_layout.addLayout(att_wrap)

        self._text_label = QLabel("")
        self._text_label.setObjectName("BubbleSent")
        self._text_label.setWordWrap(True)
        self._text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._text_label.setFont(QFont("Segoe UI", 11))
        self._text_label.setStyleSheet(
            f"""
            QLabel#BubbleSent {{
                background-color: {self.BUBBLE};
                color: {self.TEXT};
                border-radius: 10px;
                padding: 10px 12px;
            }}
            """
        )
        self._text_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)

        text_row = QHBoxLayout()
        text_row.setContentsMargins(0, 0, 0, 0)
        text_row.addStretch(1)
        text_row.addWidget(self._text_label, alignment=Qt.AlignmentFlag.AlignRight)
        self._bubble_layout.addLayout(text_row)

        self._scroll.setWidget(self._bubble_host)
        outer.addWidget(self._scroll, 1)

        self._hint = QLabel("Updates as you type.")
        self._hint.setStyleSheet("color: #8696A0; font-size: 12px; padding-top: 4px;")
        outer.addWidget(self._hint)

    def set_sender_name(self, name: str) -> None:
        n = (name or "").strip()
        self._profile_label.setText(n if n else "You")

    def clear_attachments(self) -> None:
        while self._attachment_layout.count():
            item = self._attachment_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def set_attachments(self, paths: list[str]) -> None:
        self.clear_attachments()
        self._attachment_layout.addStretch(1)
        for p in paths[:12]:
            if not os.path.isfile(p):
                continue
            thumb = QLabel()
            thumb.setFixedSize(QSize(56, 56))
            thumb.setStyleSheet("border-radius: 8px; background: #202C33; border: 1px solid #2A3942;")
            pix = QPixmap(p)
            if not pix.isNull():
                thumb.setPixmap(pix.scaled(56, 56, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
                thumb.setScaledContents(True)
            else:
                thumb.setText(Path(p).suffix.upper()[:4] or "FILE")
                thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
                thumb.setStyleSheet(thumb.styleSheet() + "color: #8696A0; font-size: 10px;")
            self._attachment_layout.insertWidget(self._attachment_layout.count() - 1, thumb)

    def set_message_text(self, text: str) -> None:
        t = (text or "").strip()
        self._text_label.setText(t if t else " ")
        self._text_label.setVisible(True)
