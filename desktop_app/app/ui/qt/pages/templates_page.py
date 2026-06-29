"""Message templates with {name} placeholders."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.db.local_access import (
    DEFAULT_LIST_FIELDS,
    delete_template,
    fetch_contact_lists,
    fetch_local_profiles,
    fetch_templates,
    rename_template,
    upsert_template,
)
from app.services.constants import SEND_TEMPLATE_CUSTOM
from app.ui.qt.widgets.profile_combo_utils import populate_profile_combo

_EMOJI = (
    "😀 😃 😄 😁 😆 🙂 😊 😉 😍 🥰 😘 🤝 🙏 👍 👌 👏 💪 🎉 ✨ 🔥 ❤️ 💚 💙 💛 🧡 💜 "
    "📞 📱 💬 📌 ✅ ❌ ⚡ 📢 🛍️ 💼 🏠 🚚 ⏰ 📅"
).split()


class TemplatesPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(40, 36, 48, 36)
        v.setSpacing(18)

        title = QLabel("Templates")
        title.setProperty("class", "sectionTitle")
        v.addWidget(title)

        hint = QLabel(
            "Use placeholders like {name}, {phone}, {company}, or any column from your selected list."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #94A3B8; font-size: 14px;")
        v.addWidget(hint)

        row_vs = QHBoxLayout()
        row_vs.addWidget(QLabel("Insert variables from list"))
        self._var_source = QComboBox()
        self._var_source.setMinimumHeight(38)
        self._var_source.currentIndexChanged.connect(self._rebuild_variable_chips)
        row_vs.addWidget(self._var_source, 1)
        v.addLayout(row_vs)

        var_scroll = QScrollArea()
        var_scroll.setFixedHeight(52)
        var_scroll.setWidgetResizable(True)
        var_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        var_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        var_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._var_inner = QWidget()
        self._var_btn_row = QHBoxLayout(self._var_inner)
        self._var_btn_row.setContentsMargins(0, 0, 0, 0)
        self._var_btn_row.setSpacing(8)
        var_scroll.setWidget(self._var_inner)
        v.addWidget(var_scroll)

        row = QHBoxLayout()
        row.addWidget(QLabel("Profile"))
        self._prof = QComboBox()
        self._prof.setMinimumHeight(40)
        self._prof.currentIndexChanged.connect(self._load_templates_list)
        row.addWidget(self._prof, 1)
        v.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Existing"))
        self._pick = QComboBox()
        self._pick.setMinimumHeight(40)
        self._pick.currentIndexChanged.connect(self._apply_pick)
        row2.addWidget(self._pick, 1)
        v.addLayout(row2)

        v.addWidget(QLabel("Template name"))
        self._name = QLineEdit()
        self._name.setMinimumHeight(40)
        self._name.setPlaceholderText("e.g. greeting_sales")
        v.addWidget(self._name)

        v.addWidget(QLabel("Content"))
        self._body = QTextEdit()
        self._body.setMinimumHeight(220)
        self._body.setPlaceholderText("Hi {name}, …")
        v.addWidget(self._body)

        v.addWidget(QLabel("Quick emoji"))
        emoji_scroll = QScrollArea()
        emoji_scroll.setFixedHeight(56)
        emoji_scroll.setWidgetResizable(True)
        emoji_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        emoji_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        emoji_scroll.setFrameShape(QFrame.Shape.NoFrame)
        emoji_inner = QWidget()
        emoji_row = QHBoxLayout(emoji_inner)
        emoji_row.setContentsMargins(0, 0, 0, 0)
        emoji_row.setSpacing(8)
        em_font = QFont()
        em_font.setFamilies(["Segoe UI Emoji", "Segoe UI Symbol", "Segoe UI"])
        em_font.setPixelSize(18)
        for em in _EMOJI:
            b = QPushButton(em)
            b.setObjectName("EmojiPick")
            b.setFont(em_font)
            b.setToolTip(f"Insert {em}")
            b.clicked.connect(lambda _c=False, e=em: self._insert_emoji(e))
            emoji_row.addWidget(b)
        emoji_row.addStretch(1)
        emoji_scroll.setWidget(emoji_inner)
        v.addWidget(emoji_scroll)

        btns = QHBoxLayout()
        save = QPushButton("Save template")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        btns.addWidget(save)
        upd = QPushButton("Update selected")
        upd.clicked.connect(self._update_selected)
        btns.addWidget(upd)
        dele = QPushButton("Delete selected")
        dele.clicked.connect(self._delete_selected)
        btns.addWidget(dele)
        btns.addStretch(1)
        v.addLayout(btns)

        self.reload_profiles()

    def reload_profiles(self) -> None:
        populate_profile_combo(self._prof, fetch_local_profiles())
        self._load_templates_list()

    def _reload_var_source_combo(self) -> None:
        self._var_source.blockSignals(True)
        self._var_source.clear()
        self._var_source.addItem("Default fields", None)
        pid = self._pid()
        if pid:
            try:
                for lst in fetch_contact_lists(pid):
                    self._var_source.addItem(str(lst.get("name", "")), lst)
            except Exception:
                pass
        self._var_source.blockSignals(False)
        self._rebuild_variable_chips()

    def _rebuild_variable_chips(self) -> None:
        while self._var_btn_row.count():
            it = self._var_btn_row.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        data = self._var_source.currentData()
        if data is None:
            fields = ["name", "phone", "email", "company"]
        else:
            fields = list((data or {}).get("fields") or DEFAULT_LIST_FIELDS)
        for f in fields:
            key = str(f).strip()
            if not key:
                continue
            b = QPushButton("{" + key + "}")
            b.setToolTip(f"Insert {{{key}}}")
            b.clicked.connect(lambda _=False, k=key: self._insert_var(k))
            self._var_btn_row.addWidget(b)
        self._var_btn_row.addStretch(1)

    def _insert_var(self, key: str) -> None:
        if not key:
            return
        self._body.insertPlainText("{" + key + "}")

    def _pid(self) -> int | None:
        i = self._prof.currentIndex()
        if i < 0:
            return None
        d = self._prof.itemData(i)
        return int(d["id"]) if isinstance(d, dict) else None

    def _load_templates_list(self) -> None:
        self._pick.blockSignals(True)
        self._pick.clear()
        self._pick.addItem(SEND_TEMPLATE_CUSTOM, None)
        pid = self._pid()
        if pid:
            try:
                for t in fetch_templates(pid):
                    self._pick.addItem(t["name"], t)
            except Exception:
                pass
        self._pick.blockSignals(False)
        self._reload_var_source_combo()
        self._apply_pick()

    def _apply_pick(self) -> None:
        data = self._pick.currentData()
        if isinstance(data, dict):
            self._name.setText(str(data.get("name", "")))
            self._body.setPlainText(str(data.get("content", "")))
        else:
            self._name.clear()
            self._body.clear()

    def _insert_emoji(self, em: str) -> None:
        if not em:
            return
        self._body.insertPlainText(em)

    def _current_template(self) -> dict | None:
        data = self._pick.currentData()
        return data if isinstance(data, dict) else None

    def _save(self) -> None:
        pid = self._pid()
        if not pid:
            QMessageBox.information(self, "Templates", "Select a profile.")
            return
        name = self._name.text().strip()
        body = self._body.toPlainText().strip()
        if not name:
            QMessageBox.information(self, "Templates", "Enter a template name.")
            return
        try:
            upsert_template(pid, name, body)
        except Exception as e:
            QMessageBox.critical(self, "Templates", str(e))
            return
        QMessageBox.information(self, "Templates", "Template saved.")
        self._load_templates_list()

    def _update_selected(self) -> None:
        pid = self._pid()
        cur = self._current_template()
        if not pid or not cur:
            QMessageBox.information(self, "Templates", "Pick an existing template to update.")
            return
        tid = int(cur.get("id", 0))
        if tid <= 0:
            QMessageBox.information(self, "Templates", "Pick an existing template to update.")
            return
        new_name = self._name.text().strip()
        body = self._body.toPlainText().strip()
        if not new_name:
            QMessageBox.information(self, "Templates", "Enter a template name.")
            return
        try:
            if new_name != str(cur.get("name", "")).strip():
                rename_template(pid, tid, new_name)
            upsert_template(pid, new_name, body)
        except Exception as e:
            QMessageBox.critical(self, "Templates", str(e))
            return
        QMessageBox.information(self, "Templates", "Template updated.")
        self._load_templates_list()
        idx = self._pick.findText(new_name)
        if idx >= 0:
            self._pick.setCurrentIndex(idx)

    def _delete_selected(self) -> None:
        pid = self._pid()
        cur = self._current_template()
        if not pid or not cur:
            QMessageBox.information(self, "Templates", "Pick an existing template to delete.")
            return
        tid = int(cur.get("id", 0))
        name = str(cur.get("name", "")).strip() or "this template"
        if tid <= 0:
            QMessageBox.information(self, "Templates", "Pick an existing template to delete.")
            return
        if (
            QMessageBox.question(self, "Delete template", f"Delete '{name}'?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            delete_template(pid, tid)
        except Exception as e:
            QMessageBox.critical(self, "Templates", str(e))
            return
        self._load_templates_list()
        QMessageBox.information(self, "Templates", "Template deleted.")
