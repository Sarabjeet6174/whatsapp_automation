"""Dashboard — quick navigation cards so the home screen is not empty."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class DashboardPage(QWidget):
    """Emits stack index to switch sidebar pages."""

    open_stack_index = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(40, 36, 48, 36)
        v.setSpacing(28)

        title = QLabel("Dashboard")
        title.setProperty("class", "sectionTitle")
        v.addWidget(title)

        sub = QLabel(
            "Choose what you want to do. All sections below are connected to your local database."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet("color: #94A3B8; font-size: 15px; max-width: 720px;")
        v.addWidget(sub)

        grid = QGridLayout()
        grid.setSpacing(20)

        cards = [
            (6, "💬", "Send messages", "Compose, attachments, recipients, live preview."),
            (1, "👤", "Profiles", "Create profiles and open WhatsApp Web."),
            (2, "📇", "Contacts & lists", "Manage lists, import CSV, and curate contacts."),
            (3, "📱", "WhatsApp contacts", "Sync names from New chat in WhatsApp."),
            (4, "👥", "WhatsApp groups", "Sync group names from New chat > Groups."),
            (5, "📝", "Templates", "Save reusable templates with variable placeholders."),
            (7, "🕐", "Schedule", "See upcoming scheduled sends."),
            (8, "📋", "Logs", "Recent send results and errors."),
        ]
        for i, (idx, icon, heading, desc) in enumerate(cards):
            grid.addWidget(self._card(idx, icon, heading, desc), i // 3, i % 3)

        v.addLayout(grid)
        v.addStretch(1)

    def _card(self, stack_idx: int, icon: str, heading: str, desc: str) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        card.setMinimumHeight(160)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(20, 18, 20, 18)
        cv.setSpacing(10)
        h = QLabel(f"{icon}  {heading}")
        h.setStyleSheet("font-size: 16px; font-weight: 700; color: #F8FAFC;")
        cv.addWidget(h)
        d = QLabel(desc)
        d.setWordWrap(True)
        d.setStyleSheet("color: #94A3B8; font-size: 13px;")
        cv.addWidget(d)
        btn = QPushButton("Open")
        btn.setObjectName("Primary")
        btn.clicked.connect(lambda _=False, si=stack_idx: self.open_stack_index.emit(si))
        cv.addWidget(btn, alignment=Qt.AlignmentFlag.AlignLeft)
        return card
