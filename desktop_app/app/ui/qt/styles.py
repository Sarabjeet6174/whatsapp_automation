"""Qt Stylesheets — dark slate shell + WhatsApp-style preview pane."""

APP_QSS = """
QMainWindow, QWidget#centralSurface {
    background-color: #0F172A;
    color: #E2E8F0;
    font-family: "Segoe UI", "Segoe UI Emoji", "Segoe UI Symbol", Roboto, sans-serif;
    font-size: 14px;
}

QLabel { color: #E2E8F0; font-size: 14px; }
QWidget { color: #E2E8F0; }
QLabel[class="muted"] { color: #94A3B8; font-size: 13px; }
QLabel[class="sectionTitle"] {
    color: #F1F5F9;
    font-size: 18px;
    font-weight: 700;
    padding-bottom: 4px;
}
QLabel[class="fieldLabel"] {
    color: #CBD5E1;
    font-size: 13px;
    font-weight: 600;
    min-width: 88px;
}

QFrame#Card {
    background-color: #1E293B;
    border-radius: 14px;
    border: 1px solid #334155;
}

QFrame#Sidebar {
    background-color: #0F172A;
    border-right: 1px solid #334155;
}

QFrame#ComposerScroll {
    background-color: transparent;
}

QListWidget#NavList {
    background-color: transparent;
    border: none;
    outline: none;
    padding: 10px 6px;
    font-size: 14px;
}
QListWidget#NavList::item {
    padding: 14px 16px;
    border-radius: 12px;
    margin: 4px 8px;
    color: #CBD5E1;
    min-height: 22px;
}
QListWidget#NavList::item:selected {
    background-color: #1E293B;
    color: #22C55E;
    font-weight: 600;
}
QListWidget#NavList::item:hover:!selected {
    background-color: #1E293B;
}

QPushButton {
    background-color: #334155;
    color: #F8FAFC;
    border: none;
    border-radius: 10px;
    padding: 10px 18px;
    font-weight: 600;
    font-size: 13px;
    min-height: 20px;
}
QPushButton:hover { background-color: #475569; }
QPushButton:pressed { background-color: #64748B; }

QPushButton#Primary {
    background-color: #22C55E;
    color: #052E16;
}
QPushButton#Primary:hover { background-color: #16A34A; }

QPushButton#EmojiPick {
    background-color: #334155;
    border-radius: 8px;
    padding: 2px;
    min-width: 40px;
    min-height: 40px;
    max-width: 44px;
    max-height: 44px;
    font-family: "Segoe UI Emoji", "Segoe UI Symbol", "Segoe UI";
    font-size: 18px;
}
QPushButton#EmojiPick:hover { background-color: #475569; }

QGroupBox {
    font-size: 14px;
    font-weight: 700;
    color: #F1F5F9;
    border: 1px solid #334155;
    border-radius: 12px;
    margin-top: 16px;
    padding-top: 20px;
    padding-bottom: 12px;
    padding-left: 12px;
    padding-right: 12px;
    background-color: #1E293B;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 8px;
    background-color: #1E293B;
}

QComboBox, QLineEdit, QSpinBox, QDateTimeEdit {
    background-color: #0F172A;
    border: 1px solid #475569;
    border-radius: 10px;
    padding: 10px 12px;
    min-height: 22px;
    color: #F1F5F9;
    font-size: 14px;
}
QComboBox::drop-down { border: none; width: 32px; }
QComboBox QAbstractItemView {
    background-color: #0F172A;
    color: #E2E8F0;
    border: 1px solid #475569;
    selection-background-color: #334155;
    selection-color: #F8FAFC;
}
QLineEdit::placeholder, QTextEdit[placeholderText], QPlainTextEdit[placeholderText] {
    color: #94A3B8;
}

QTextEdit, QPlainTextEdit {
    background-color: #0F172A;
    border: 1px solid #475569;
    border-radius: 12px;
    padding: 14px;
    color: #F1F5F9;
    font-size: 15px;
    selection-background-color: #22C55E;
    selection-color: #052E16;
}

QTableWidget {
    background-color: #0F172A;
    alternate-background-color: #1E293B;
    color: #E2E8F0;
    border: 1px solid #475569;
    border-radius: 12px;
    gridline-color: #334155;
    font-size: 13px;
}
QTableWidget::item {
    padding: 8px 6px;
    color: #E2E8F0;
    background-color: transparent;
}
QTableWidget::item:selected {
    background-color: #334155;
    color: #F8FAFC;
}
QHeaderView::section {
    background-color: #334155;
    color: #F1F5F9;
    padding: 10px 8px;
    border: none;
    font-weight: 600;
    font-size: 13px;
}

QScrollArea { border: none; background: transparent; }
QScrollArea QWidget#qt_scrollarea_viewport { background: transparent; }

QCheckBox { spacing: 10px; color: #E2E8F0; font-size: 13px; }
QCheckBox::indicator { width: 18px; height: 18px; }

QTableWidget::indicator,
QTableView::indicator {
    width: 18px;
    height: 18px;
}
QTableWidget::indicator:unchecked,
QTableView::indicator:unchecked {
    border: 2px solid #64748B;
    border-radius: 4px;
    background-color: #0F172A;
}
QTableWidget::indicator:checked,
QTableView::indicator:checked {
    border: 2px solid #22C55E;
    border-radius: 4px;
    background-color: #22C55E;
}

QRadioButton { spacing: 10px; color: #E2E8F0; font-size: 14px; padding: 4px 0; }
QRadioButton::indicator {
    width: 18px;
    height: 18px;
}
QRadioButton::indicator:unchecked {
    border: 2px solid #64748B;
    border-radius: 9px;
    background: #0F172A;
}
QRadioButton::indicator:checked {
    border: 2px solid #22C55E;
    border-radius: 9px;
    background: #22C55E;
}

QSplitter::handle { background: #334155; width: 2px; }

QStatusBar {
    background-color: #0F172A;
    border-top: 1px solid #334155;
    color: #94A3B8;
    font-size: 13px;
    padding: 4px 8px;
}
QPushButton:disabled,
QComboBox:disabled,
QLineEdit:disabled,
QDateTimeEdit:disabled,
QTextEdit:disabled,
QTableWidget:disabled,
QCheckBox:disabled,
QRadioButton:disabled,
QLabel:disabled {
    color: #94A3B8;
}
"""

CHAT_SURFACE_STYLE = """
QWidget#ChatSurface {
    background-color: #0B141A;
    border-radius: 12px;
    border: 1px solid #1F2C34;
}
"""

BUBBLE_SENT = """
QLabel#BubbleSent {
    background-color: #005C4B;
    color: #E9EDEF;
    border-radius: 10px;
    padding: 10px 12px;
    font-size: 15px;
}
"""
