"""Qt Stylesheets — dark slate shell + WhatsApp-style preview pane."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication


def apply_app_theme(app: QApplication) -> None:
    """
    Force a fixed dark Fusion theme so the UI looks the same on every PC
    regardless of Windows light/dark mode or accent colors.
    """
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))

    pal = QPalette()
    bg = QColor("#0F172A")
    panel = QColor("#1E293B")
    text = QColor("#E2E8F0")
    muted = QColor("#94A3B8")
    btn = QColor("#334155")
    btn_text = QColor("#F8FAFC")
    accent = QColor("#22C55E")
    pal.setColor(QPalette.ColorRole.Window, bg)
    pal.setColor(QPalette.ColorRole.WindowText, text)
    pal.setColor(QPalette.ColorRole.Base, bg)
    pal.setColor(QPalette.ColorRole.AlternateBase, panel)
    pal.setColor(QPalette.ColorRole.ToolTipBase, panel)
    pal.setColor(QPalette.ColorRole.ToolTipText, btn_text)
    pal.setColor(QPalette.ColorRole.Text, text)
    pal.setColor(QPalette.ColorRole.Button, btn)
    pal.setColor(QPalette.ColorRole.ButtonText, btn_text)
    pal.setColor(QPalette.ColorRole.BrightText, btn_text)
    pal.setColor(QPalette.ColorRole.Link, accent)
    pal.setColor(QPalette.ColorRole.Highlight, QColor("#334155"))
    pal.setColor(QPalette.ColorRole.HighlightedText, btn_text)
    pal.setColor(QPalette.ColorRole.PlaceholderText, muted)
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.HighlightedText,
    ):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor("#64748B"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, QColor("#1E293B"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Window, bg)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, bg)
    app.setPalette(pal)
    app.setStyleSheet(APP_QSS + CHAT_SURFACE_STYLE + BUBBLE_SENT + SEND_PAGE_STYLE)


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

QFrame#TopBar {
    background-color: #111C31;
    border-bottom: 1px solid #334155;
}
QLabel#TopBarTitle {
    color: #F8FAFC;
    font-size: 18px;
    font-weight: 700;
}
QLabel#TopBarSubtitle {
    color: #94A3B8;
    font-size: 12px;
}
QLabel#TopBarCrumbs {
    color: #64748B;
    font-size: 11px;
}
QLineEdit#GlobalSearch {
    background-color: #0B1324;
    border: 1px solid #334155;
    border-radius: 10px;
    min-height: 18px;
    color: #94A3B8;
    padding: 8px 12px;
}
QComboBox#QuickActions {
    min-width: 170px;
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

QPushButton#IconPlus {
    background-color: #22C55E;
    color: #052E16;
    border-radius: 10px;
    font-size: 20px;
    font-weight: 700;
    padding: 0;
    min-width: 40px;
    max-width: 40px;
    min-height: 40px;
    max-height: 40px;
}
QPushButton#IconPlus:hover { background-color: #16A34A; }

QPushButton#IconPlusSmall {
    background-color: #334155;
    color: #F8FAFC;
    border-radius: 8px;
    font-size: 18px;
    font-weight: 700;
    padding: 0;
    min-width: 34px;
    max-width: 34px;
    min-height: 34px;
    max-height: 34px;
}
QPushButton#IconPlusSmall:hover { background-color: #475569; }

QWidget#ContactsPage QPushButton#IconPlusSmall {
    min-width: 40px;
    max-width: 40px;
    min-height: 40px;
    max-height: 40px;
}

QTableWidget#ContactsTable {
    background-color: #0F172A;
    alternate-background-color: #1E293B;
    color: #E2E8F0;
    border: 1px solid #475569;
    border-radius: 12px;
    gridline-color: #334155;
    font-size: 13px;
}
QTableWidget#ContactsTable::item {
    padding: 6px 6px;
}
QTableWidget#ContactsTable QScrollBar:vertical {
    background: #1E293B;
    width: 12px;
    margin: 4px 2px 4px 0;
    border-radius: 6px;
}
QTableWidget#ContactsTable QScrollBar::handle:vertical {
    background: #475569;
    min-height: 28px;
    border-radius: 6px;
}
QTableWidget#ContactsTable QScrollBar::handle:vertical:hover {
    background: #64748B;
}
QTableWidget#ContactsTable QScrollBar::add-line:vertical,
QTableWidget#ContactsTable QScrollBar::sub-line:vertical {
    height: 0;
}
QTableWidget#ContactsTable QScrollBar:horizontal {
    background: #1E293B;
    height: 12px;
    margin: 0 4px 2px 4px;
    border-radius: 6px;
}
QTableWidget#ContactsTable QScrollBar::handle:horizontal {
    background: #475569;
    min-width: 28px;
    border-radius: 6px;
}

QMenu {
    background-color: #1E293B;
    color: #E2E8F0;
    border: 1px solid #475569;
    padding: 6px 0;
}
QMenu::item {
    padding: 8px 24px;
}
QMenu::item:selected {
    background-color: #334155;
}

QToolTip {
    background-color: #1E293B;
    color: #F1F5F9;
    border: 1px solid #475569;
    padding: 6px 10px;
}

QPushButton#PageNavBtn {
    background-color: #334155;
    color: #F8FAFC;
    border: 1px solid #475569;
    border-radius: 8px;
    min-width: 36px;
    min-height: 36px;
    max-width: 36px;
    max-height: 36px;
    padding: 0;
    font-size: 20px;
    font-weight: 700;
}
QPushButton#PageNavBtn:hover:enabled { background-color: #475569; }
QPushButton#PageNavBtn:disabled {
    background-color: #1E293B;
    color: #94A3B8;
    border-color: #334155;
}

QFrame#PaginationBar {
    background-color: #0F172A;
    border-top: 1px solid #334155;
}

QFrame#RecipientTableCard {
    background-color: #0F172A;
    border: 1px solid #334155;
    border-radius: 12px;
}
QFrame#RecipientTableCard QTableWidget {
    border: none;
    border-radius: 0;
    background-color: transparent;
}

QFrame#RecipientSelectionBar {
    background-color: #1E293B;
    border: 1px solid #334155;
    border-radius: 8px;
}
QLabel#SelectionBarText {
    color: #CBD5E1;
    font-size: 12px;
    font-weight: 500;
}

QFrame#SendFlowFooter {
    background-color: #111C31;
    border-top: 1px solid #334155;
    min-height: 56px;
}

QFrame#RecipientSelectionSummary {
    background-color: #1E293B;
    border: 1px solid #334155;
    border-radius: 12px;
}
QLabel#SelectionSummaryTitle {
    color: #F1F5F9;
    font-size: 14px;
    font-weight: 600;
    border-bottom: 1px solid #334155;
}
QLabel#SelectionStatValue {
    font-size: 24px;
    font-weight: 700;
    color: #F8FAFC;
    background: transparent;
}
QLabel#SelectionStatValue[accent="green"] { color: #22C55E; }
QLabel#SelectionStatValue[accent="purple"] { color: #A855F7; }
QLabel[class="selectionStatLabel"] {
    color: #94A3B8;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.4px;
}
QLabel#SelectionEtaValue {
    color: #F1F5F9;
    font-size: 15px;
    font-weight: 600;
}
QFrame#SelectionDivider {
    background-color: #334155;
    max-height: 1px;
}

QPushButton#Primary {
    background-color: #22C55E;
    color: #052E16;
}
QPushButton#Primary:hover { background-color: #16A34A; }

QPushButton#TopAction {
    background-color: #22C55E;
    color: #052E16;
    min-width: 84px;
}
QPushButton#TopAction:hover { background-color: #16A34A; }

QPushButton#SourceToggle {
    background-color: #0F172A;
    border: 1px solid #475569;
    color: #CBD5E1;
    min-height: 28px;
    padding: 8px 12px;
}
QPushButton#SourceToggle:checked {
    background-color: #1E293B;
    border: 1px solid #22C55E;
    color: #22C55E;
}

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

QComboBox, QLineEdit, QSpinBox, QDateTimeEdit, QDateEdit, QTimeEdit {
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

QWidget#centralSurface[density="compact"] QPushButton {
    padding: 8px 12px;
    min-height: 16px;
}
QWidget#centralSurface[density="compact"] QLineEdit,
QWidget#centralSurface[density="compact"] QComboBox,
QWidget#centralSurface[density="compact"] QDateTimeEdit {
    padding: 6px 10px;
}
QWidget#centralSurface[density="compact"] QTableWidget::item {
    padding: 4px 4px;
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

/* —— Send Messages guided flow —— */
QWidget#SendFlowStepper {
    background-color: #1E293B;
    border: 1px solid #334155;
    border-radius: 12px;
    padding: 12px 16px;
}
QWidget#SendFlowStepper[compact="true"] {
    padding: 4px 10px;
    border-radius: 8px;
}
QLabel[class="stepTitleCompact"] {
    color: #F1F5F9;
    font-size: 11px;
    font-weight: 600;
}
QLabel[class="stepArrow"] {
    color: #64748B;
    font-size: 18px;
    min-width: 28px;
}
QLabel[class="stepTitle"] {
    color: #F1F5F9;
    font-size: 13px;
    font-weight: 700;
}
QLabel[class="stepSub"] {
    color: #94A3B8;
    font-size: 11px;
}
QPushButton#StepCircle {
    background-color: #334155;
    color: #94A3B8;
    border-radius: 20px;
    font-weight: 700;
    font-size: 15px;
    padding: 0;
}
QPushButton#StepCircle[active="true"] {
    background-color: #22C55E;
    color: #052E16;
}
QPushButton#StepCircle[done="true"] {
    background-color: #14532D;
    color: #BBF7D0;
}
QPushButton#StepCircle[locked="true"]:disabled {
    background-color: #1E293B;
    color: #64748B;
}

QFrame#SendStepAccordionItem {
    background-color: #1E293B;
    border: 1px solid #334155;
    border-radius: 10px;
    margin-bottom: 8px;
}
QFrame#SendStepAccordionItem[expanded="true"] {
    border-color: #22C55E;
}
QPushButton#StepAccordionHeader {
    background-color: transparent;
    border: none;
    text-align: left;
    padding: 0;
}
QPushButton#StepAccordionHeader:hover {
    background-color: #334155;
}
QWidget#StepAccordionBody {
    border-top: 1px solid #334155;
}
QLabel#StepBadge {
    background-color: #334155;
    color: #CBD5E1;
    border-radius: 8px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
}

QFrame#SummaryStatCard {
    background-color: #0F172A;
    border: 1px solid #334155;
    border-radius: 10px;
}
QFrame#SummaryStatCard[accent="green"] { border-left: 4px solid #22C55E; }
QFrame#SummaryStatCard[accent="purple"] { border-left: 4px solid #A855F7; }
QFrame#SummaryStatCard[accent="blue"] { border-left: 4px solid #3B82F6; }
QFrame#SummaryStatCard[accent="amber"] { border-left: 4px solid #F59E0B; }
QLabel#SummaryValue {
    color: #F8FAFC;
    font-size: 22px;
    font-weight: 700;
    background: transparent;
    min-height: 28px;
}
QFrame#SummaryStatCard QLabel[class="muted"] {
    font-size: 12px;
    color: #94A3B8;
}

QLabel#InfoAlert {
    background-color: #1E3A5F;
    color: #BFDBFE;
    border: 1px solid #3B82F6;
    border-radius: 10px;
    padding: 12px 14px;
    font-size: 13px;
}

QPushButton#OutlineGreen {
    background-color: transparent;
    border: 2px solid #22C55E;
    color: #22C55E;
}
QPushButton#OutlineGreen:hover { background-color: #14532D; color: #BBF7D0; }
QPushButton#OutlineBlue {
    background-color: transparent;
    border: 2px solid #3B82F6;
    color: #93C5FD;
}
QPushButton#OutlineBlue:hover { background-color: #1E3A5F; }

QPushButton#SourceTab {
    background-color: #0F172A;
    border: 1px solid #475569;
    color: #CBD5E1;
    min-height: 32px;
    padding: 8px 16px;
}
QPushButton#SourceTab:checked {
    background-color: #1E293B;
    border: 1px solid #22C55E;
    color: #22C55E;
}

QLabel#AttachDropZone {
    border: 1px dashed #475569;
    border-radius: 10px;
    color: #94A3B8;
    font-size: 12px;
    background-color: #0F172A;
    padding: 10px;
}

QFrame#SendLeftPanel {
    background-color: #1E293B;
    border: 1px solid #334155;
    border-radius: 12px;
}
"""

CHAT_SURFACE_STYLE = """
QFrame#ChatPreviewPhone {
    background-color: #0b141a;
    border: 1px solid #475569;
    border-radius: 22px;
}
QLabel#PhonePreviewHeader {
    color: #d1d5db;
    font-size: 13px;
    font-weight: 600;
    padding-bottom: 10px;
}
QFrame#PhonePreviewChat {
    background-color: #101920;
    border-radius: 16px;
    border: none;
    min-height: 170px;
}
QFrame#WaBubble {
    background-color: #005c4b;
    border-radius: 10px;
    max-width: 280px;
}
QLabel#PreviewAttachmentHint {
    color: #8696a0;
    font-size: 11px;
    padding-top: 8px;
}
QLabel#ChatPreviewHint {
    color: #8696A0;
    font-size: 12px;
}
QLabel#ChatPreviewThumb {
    border-radius: 8px;
    background: #202C33;
    border: 1px solid #2A3942;
    color: #8696A0;
    font-size: 10px;
    padding: 4px;
}
"""

BUBBLE_SENT = """
QLabel#BubbleSent {
    background: transparent;
    color: #e9edef;
    border: none;
    padding: 0;
    font-size: 13px;
}
"""

SEND_PAGE_STYLE = """
QWidget#SendMessagesPage QFrame#ScheduleOptionCard {
    background-color: #1E293B;
    border: 1px solid #334155;
    border-radius: 12px;
}
QWidget#SendMessagesPage QFrame#ScheduleOptionCard[selected="true"] {
    border-color: rgba(34, 197, 94, 0.45);
    background-color: rgba(34, 197, 94, 0.06);
}
QWidget#SendMessagesPage QLabel#ScheduleOptionTitle {
    color: #F1F5F9;
    font-size: 14px;
    font-weight: 700;
}
QWidget#SendMessagesPage QRadioButton#ScheduleOptionRadio {
    spacing: 0px;
}
QWidget#SendMessagesPage QRadioButton#ScheduleOptionRadio::indicator {
    width: 18px;
    height: 18px;
    border-radius: 9px;
    border: 2px solid #475569;
    background: transparent;
}
QWidget#SendMessagesPage QRadioButton#ScheduleOptionRadio::indicator:checked {
    border-color: #22C55E;
    background-color: #22C55E;
}
QWidget#SendMessagesPage QFrame#ScheduleFields {
    margin-top: 2px;
}
QWidget#SendMessagesPage QDateEdit#ScheduleDateEdit,
QWidget#SendMessagesPage QTimeEdit#ScheduleTimeEdit {
    background-color: #0F172A;
    border: 1px solid #475569;
    border-radius: 10px;
    padding: 8px 12px;
    min-height: 22px;
    color: #F1F5F9;
    font-size: 14px;
}
QWidget#SendMessagesPage QDateEdit#ScheduleDateEdit::drop-down,
QWidget#SendMessagesPage QTimeEdit#ScheduleTimeEdit::up-button,
QWidget#SendMessagesPage QTimeEdit#ScheduleTimeEdit::down-button {
    border: none;
    width: 24px;
}
QWidget#SendMessagesPage QPushButton#SendCancelOperation {
    background-color: #DC2626;
    color: #FFFFFF;
    border: none;
    border-radius: 12px;
    font-size: 16px;
    font-weight: 700;
    padding: 14px 24px;
    min-height: 52px;
}
QWidget#SendMessagesPage QPushButton#SendCancelOperation:hover {
    background-color: #B91C1C;
}
QWidget#SendMessagesPage QPushButton#SendCancelOperation:disabled {
    background-color: #475569;
    color: #94A3B8;
}
QWidget#SendMessagesPage QTextEdit#SendLiveLog {
    background-color: #0f172a;
    color: #94a3b8;
    border: 1px solid #334155;
    border-radius: 10px;
    font-family: Consolas, "Courier New", monospace;
    font-size: 12px;
    padding: 8px;
}
"""
