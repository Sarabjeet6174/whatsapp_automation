"""Contact lists and rows — manage lists and single contacts."""

from __future__ import annotations

import csv
import os
from typing import Any

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.db.local_access import (
    DEFAULT_LIST_FIELDS,
    create_contact,
    create_contact_list,
    delete_contact_list,
    delete_contacts,
    fetch_contact_lists,
    fetch_contacts,
    fetch_local_profiles,
    rename_contact_list,
    update_contact,
    update_contact_list_fields,
)
from app.ui.qt.widgets.profile_combo_utils import populate_profile_combo


class ContactsPage(QWidget):
    open_send_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ContactsPage")
        v = QVBoxLayout(self)
        v.setContentsMargins(40, 36, 48, 24)
        v.setSpacing(10)

        title = QLabel("Contacts & lists")
        title.setProperty("class", "sectionTitle")
        v.addWidget(title)

        note = QLabel(
            "Manage your contact lists and contacts here, including CSV import into the selected list."
        )
        note.setWordWrap(True)
        note.setProperty("class", "muted")
        v.addWidget(note)

        row = QHBoxLayout()
        row.addWidget(QLabel("Profile"))
        self._prof = QComboBox()
        self._prof.setMinimumHeight(40)
        self._prof.currentIndexChanged.connect(self._fill_lists)
        row.addWidget(self._prof, 1)
        row.addWidget(QLabel("List"))
        self._list_cb = QComboBox()
        self._list_cb.setMinimumHeight(40)
        self._list_cb.currentIndexChanged.connect(self._fill_table)
        row.addWidget(self._list_cb, 1)
        row.addWidget(QPushButton("Add list", clicked=self._add_list))
        row.addWidget(QPushButton("Rename list", clicked=self._rename_list))
        row.addWidget(QPushButton("Delete list", clicked=self._delete_list))
        row.addWidget(QPushButton("Import Contacts CSV", clicked=self._import_contacts_csv))
        v.addLayout(row)

        col_hint = QLabel(
            "Each list has columns for templates (name, phone, plus custom fields). "
            "Click + beside the toolbar to add a column; click a column header to rename or remove it."
        )
        col_hint.setWordWrap(True)
        col_hint.setProperty("class", "muted")
        v.addWidget(col_hint)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Find by any column value…")
        self._search.setMinimumHeight(36)
        self._search.textChanged.connect(self._fill_table)
        search_row.addWidget(self._search, 1)
        v.addLayout(search_row)

        act_row = QHBoxLayout()
        act_row.setSpacing(10)
        self._add_contact_btn = QPushButton("+")
        self._add_contact_btn.setObjectName("IconPlus")
        self._add_contact_btn.setToolTip("Add contact")
        self._add_contact_btn.clicked.connect(self._add_contact)
        act_row.addWidget(self._add_contact_btn)
        del_btn = QPushButton("Delete selected")
        del_btn.clicked.connect(self._delete_selected_contacts)
        act_row.addWidget(del_btn)
        copy_btn = QPushButton("New list from selected…")
        copy_btn.clicked.connect(self._new_list_from_selected)
        act_row.addWidget(copy_btn)
        send_btn = QPushButton("Send selected…")
        send_btn.clicked.connect(self._open_send_with_selected)
        act_row.addWidget(send_btn)
        act_row.addWidget(QLabel("Double-click a cell or use ✎ to edit."))
        act_row.addStretch(1)
        self._add_col_btn = QPushButton("+")
        self._add_col_btn.setObjectName("IconPlusSmall")
        self._add_col_btn.setToolTip("Add column")
        self._add_col_btn.clicked.connect(self._add_column)
        act_row.addWidget(self._add_col_btn)
        v.addLayout(act_row)

        self._table = QTableWidget(0, 0)
        self._table.setObjectName("ContactsTable")
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(34)
        self._table.setMinimumHeight(220)
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self._table.itemChanged.connect(self._on_table_item_changed)
        self._table.cellClicked.connect(self._on_table_cell_clicked)
        self._table.horizontalHeader().sectionClicked.connect(self._on_header_section_clicked)
        v.addWidget(self._table, 1)

        self._loading_table = False

        self._reload_profiles()

    def _reload_profiles(self) -> None:
        populate_profile_combo(self._prof, fetch_local_profiles())
        self._fill_lists()

    def _pid(self) -> int | None:
        i = self._prof.currentIndex()
        if i < 0:
            return None
        d = self._prof.itemData(i)
        return int(d["id"]) if isinstance(d, dict) else None

    def _fill_lists(self) -> None:
        self._list_cb.blockSignals(True)
        self._list_cb.clear()
        pid = self._pid()
        if pid:
            try:
                for lst in fetch_contact_lists(pid):
                    self._list_cb.addItem(lst["name"], int(lst["id"]))
            except Exception:
                pass
        self._list_cb.blockSignals(False)
        self._fill_table()

    @staticmethod
    def _base_keys() -> set[str]:
        return {"name", "phone", "email", "company"}

    def _list_field_keys(self) -> list[str]:
        cl = self._current_list()
        if not cl:
            return list(DEFAULT_LIST_FIELDS)
        pid, lid = cl
        try:
            for lst in fetch_contact_lists(pid):
                if int(lst.get("id", 0)) == int(lid):
                    fields = list(lst.get("fields") or DEFAULT_LIST_FIELDS)
                    out: list[str] = []
                    for f in fields:
                        k = str(f).strip()
                        if k and k.lower() not in {x.lower() for x in out}:
                            out.append(k)
                    low = {x.lower() for x in out}
                    if "name" in low and "phone" in low:
                        return out
        except Exception:
            pass
        return list(DEFAULT_LIST_FIELDS)

    def _contact_field_value(self, c: dict, field: str) -> str:
        fk = field.strip().lower()
        if fk == "name":
            return str(c.get("name", ""))
        if fk == "phone":
            return str(c.get("phone", ""))
        if fk == "email":
            return str(c.get("email", ""))
        if fk == "company":
            return str(c.get("company", ""))
        ex = c.get("extra") or {}
        for k, v in ex.items():
            if str(k).strip().lower() == fk:
                return str(v)
        return ""

    def _fill_table(self) -> None:
        self._table.setRowCount(0)
        pid = self._pid()
        lid = self._list_cb.currentData()
        if not pid or lid is None:
            return
        fields = self._list_field_keys()
        self._loading_table = True
        self._table.setColumnCount(len(fields) + 1)
        self._table.setHorizontalHeaderLabels(["✎", *fields])
        try:
            rows = fetch_contacts(pid, int(lid))
            # Keep latest inserts easy to find/edit instead of name-sorted placement.
            rows = sorted(rows, key=lambda x: int(x.get("id", 0)), reverse=True)
            q = self._search.text().strip().lower() if hasattr(self, "_search") else ""
            if q:
                keep: list[dict[str, Any]] = []
                for c in rows:
                    vals = [self._contact_field_value(c, f).lower() for f in fields]
                    if any(q in v for v in vals):
                        keep.append(c)
                rows = keep
        except Exception as e:
            self._table.setColumnCount(1)
            self._table.setHorizontalHeaderLabels(["Error"])
            self._table.setRowCount(1)
            self._table.setItem(0, 0, QTableWidgetItem(str(e)))
            return
        self._table.setRowCount(len(rows))
        id_col = next((i for i, f in enumerate(fields) if f.strip().lower() == "name"), 0) + 1
        for r, c in enumerate(rows):
            cid = int(c.get("id", 0))
            pencil = QTableWidgetItem("✎")
            pencil.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._table.setItem(r, 0, pencil)
            for col, field in enumerate(fields):
                cell = QTableWidgetItem(self._contact_field_value(c, field))
                if (col + 1) == id_col:
                    cell.setData(Qt.ItemDataRole.UserRole, cid)
                self._table.setItem(r, col + 1, cell)
        self._loading_table = False

    def _save_list_fields(self, fields: list[str]) -> bool:
        cl = self._current_list()
        if not cl:
            return False
        pid, lid = cl
        lowers = {x.lower() for x in fields}
        if "name" not in lowers or "phone" not in lowers:
            QMessageBox.warning(self, "Columns", "Lists must always include name and phone columns.")
            return False
        try:
            update_contact_list_fields(pid, lid, fields)
        except Exception as e:
            QMessageBox.critical(self, "Columns", str(e))
            return False
        self._fill_table()
        return True

    def _add_column(self) -> None:
        cl = self._current_list()
        if not cl:
            QMessageBox.information(self, "Columns", "Select a profile and list.")
            return
        text, ok = QInputDialog.getText(
            self,
            "Add column",
            "Column key (used in templates as {key}):",
        )
        if not ok:
            return
        key = text.strip()
        if not key:
            QMessageBox.information(self, "Columns", "Enter a column name.")
            return
        fields = self._list_field_keys()
        if key.lower() in {f.lower() for f in fields}:
            QMessageBox.information(self, "Columns", "That column already exists.")
            return
        fields.append(key)
        self._save_list_fields(fields)

    def _on_header_section_clicked(self, section: int) -> None:
        if section <= 0:
            return
        fields = self._list_field_keys()
        col_idx = section - 1
        if col_idx < 0 or col_idx >= len(fields):
            return
        field = fields[col_idx]
        menu = QMenu(self)
        edit_act = menu.addAction("Edit column…")
        del_act = menu.addAction("Delete column…")
        if field.strip().lower() in ("name", "phone"):
            del_act.setEnabled(False)
        chosen = menu.exec(
            self._table.horizontalHeader().mapToGlobal(
                QPoint(
                    self._table.horizontalHeader().sectionPosition(section)
                    + self._table.horizontalHeader().sectionSize(section) // 2,
                    self._table.horizontalHeader().height(),
                )
            )
        )
        if chosen == edit_act:
            self._edit_column(col_idx)
        elif chosen == del_act:
            self._delete_column(col_idx)

    def _edit_column(self, col_idx: int) -> None:
        cl = self._current_list()
        if not cl:
            return
        pid, lid = cl
        fields = list(self._list_field_keys())
        if col_idx < 0 or col_idx >= len(fields):
            return
        old_key = fields[col_idx]
        new_key, ok = QInputDialog.getText(self, "Edit column", "Column key:", text=old_key)
        if not ok:
            return
        new_key = new_key.strip()
        if not new_key or new_key.lower() == old_key.lower():
            return
        if new_key.lower() in {f.lower() for f in fields}:
            QMessageBox.information(self, "Columns", "That column name is already in use.")
            return
        fields[col_idx] = new_key
        if not self._save_list_fields(fields):
            return
        if old_key.strip().lower() not in self._base_keys():
            try:
                for c in fetch_contacts(pid, lid):
                    ex = dict(c.get("extra") or {})
                    if old_key in ex:
                        ex[new_key] = ex.pop(old_key)
                        payload = {
                            "name": c.get("name", ""),
                            "phone": c.get("phone", ""),
                            "email": c.get("email", ""),
                            "company": c.get("company", ""),
                            "extra": ex,
                        }
                        update_contact(pid, int(c["id"]), payload)
            except Exception:
                pass
            self._fill_table()

    def _delete_column(self, col_idx: int) -> None:
        fields = list(self._list_field_keys())
        if col_idx < 0 or col_idx >= len(fields):
            return
        field = fields[col_idx]
        if field.strip().lower() in ("name", "phone"):
            QMessageBox.information(self, "Columns", "Name and phone columns cannot be removed.")
            return
        if (
            QMessageBox.question(self, "Delete column", f"Remove column “{field}” from this list?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        fields.pop(col_idx)
        self._save_list_fields(fields)

    def _current_list(self) -> tuple[int, int] | None:
        pid = self._pid()
        lid = self._list_cb.currentData()
        if not pid or lid is None:
            return None
        return int(pid), int(lid)

    def _name_column_index(self) -> int:
        for i, f in enumerate(self._list_field_keys()):
            if f.strip().lower() == "name":
                return i + 1
        return 1

    def _payload_from_row(self, row: int) -> dict[str, Any]:
        extra: dict[str, str] = {}
        payload: dict[str, Any] = {
            "name": "",
            "phone": "",
            "email": "",
            "company": "",
            "extra": extra,
        }
        for i, key in enumerate(self._list_field_keys()):
            it = self._table.item(row, i + 1)
            v = (it.text() if it else "").strip()
            kl = key.strip().lower()
            if kl == "name":
                payload["name"] = v
            elif kl == "phone":
                payload["phone"] = v
            elif kl == "email":
                payload["email"] = v
            elif kl == "company":
                payload["company"] = v
            else:
                extra[key] = v
        return payload

    @staticmethod
    def _payload_has_identity(p: dict[str, Any]) -> bool:
        return bool((p.get("name") or "").strip() or (p.get("phone") or "").strip())

    def _selected_contact_id(self) -> int | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        it = self._table.item(row, self._name_column_index())
        if not it:
            return None
        try:
            return int(it.data(Qt.ItemDataRole.UserRole))
        except Exception:
            return None

    def _selected_contact_ids(self) -> list[int]:
        ids: list[int] = []
        seen: set[int] = set()
        nc = self._name_column_index()
        for r in {i.row() for i in self._table.selectedItems()}:
            it = self._table.item(r, nc)
            if not it:
                continue
            try:
                cid = int(it.data(Qt.ItemDataRole.UserRole))
            except Exception:
                continue
            if cid > 0 and cid not in seen:
                seen.add(cid)
                ids.append(cid)
        return ids

    def _selected_rows(self) -> list[int]:
        rows = sorted({i.row() for i in self._table.selectedItems() if i.row() >= 0})
        return rows

    @staticmethod
    def _csv_headers_ok(fieldnames: list[str] | None) -> bool:
        if not fieldnames:
            return False
        low = {str(f).strip().lower() for f in fieldnames if f}
        return "name" in low and "phone" in low

    @staticmethod
    def _csv_dict_row_to_payload(row: dict) -> dict:
        canon: dict[str, str] = {}
        for k, v in row.items():
            if k is None:
                continue
            canon[str(k).strip().lower()] = str(v or "").strip()
        phone = canon.get("phone", "")
        if not phone:
            phone = canon.get("whatsapp_name", "")
        excluded = ("name", "phone", "whatsapp_name", "email", "company")
        extra = {k: v for k, v in canon.items() if k not in excluded}
        name = canon.get("name", "")
        return {
            "name": name,
            "phone": phone,
            "email": canon.get("email", ""),
            "company": canon.get("company", ""),
            "extra": extra,
        }

    def _on_table_cell_clicked(self, row: int, col: int) -> None:
        if col != 0:
            return
        self._table.editItem(self._table.item(row, self._name_column_index()))

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading_table or item.column() == 0:
            return
        pid = self._pid()
        row = item.row()
        name_it = self._table.item(row, self._name_column_index())
        if not pid or not name_it:
            return
        try:
            cid = int(name_it.data(Qt.ItemDataRole.UserRole))
        except Exception:
            return
        payload = self._payload_from_row(row)
        if not self._payload_has_identity(payload):
            return
        try:
            update_contact(pid, cid, payload)
        except Exception as e:
            QMessageBox.critical(self, "Contacts", f"Update failed: {e}")
            self._fill_table()

    def _add_list(self) -> None:
        pid = self._pid()
        if not pid:
            QMessageBox.information(self, "Lists", "Select a profile first.")
            return
        name, ok = QInputDialog.getText(self, "New list", "List name:")
        if not ok:
            return
        name = name.strip()
        if not name:
            QMessageBox.information(self, "Lists", "Enter a list name.")
            return
        try:
            create_contact_list(pid, name)
        except Exception as e:
            QMessageBox.critical(self, "Lists", str(e))
            return
        self._fill_lists()
        idx = self._list_cb.findText(name)
        if idx >= 0:
            self._list_cb.setCurrentIndex(idx)

    def _rename_list(self) -> None:
        cl = self._current_list()
        if not cl:
            QMessageBox.information(self, "Lists", "Select a profile and list.")
            return
        pid, lid = cl
        old_name = self._list_cb.currentText().strip()
        new_name, ok = QInputDialog.getText(self, "Rename list", "New list name:", text=old_name)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            QMessageBox.information(self, "Lists", "Enter a list name.")
            return
        try:
            rename_contact_list(pid, lid, new_name)
        except Exception as e:
            QMessageBox.critical(self, "Lists", str(e))
            return
        self._fill_lists()
        idx = self._list_cb.findText(new_name)
        if idx >= 0:
            self._list_cb.setCurrentIndex(idx)

    def _delete_list(self) -> None:
        cl = self._current_list()
        if not cl:
            QMessageBox.information(self, "Lists", "Select a profile and list.")
            return
        pid, lid = cl
        name = self._list_cb.currentText().strip() or "this list"
        if (
            QMessageBox.question(self, "Delete list", f"Delete '{name}' and all its contacts?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            delete_contact_list(pid, lid)
        except Exception as e:
            QMessageBox.critical(self, "Lists", str(e))
            return
        self._fill_lists()

    def _import_contacts_csv(self) -> None:
        cl = self._current_list()
        if not cl:
            QMessageBox.information(self, "Import CSV", "Select a profile and list first.")
            return
        pid, lid = cl
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Contacts CSV",
            "",
            "CSV files (*.csv);;All files (*.*)",
        )
        if not path:
            return
        added = 0
        skipped = 0
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                if not self._csv_headers_ok(reader.fieldnames):
                    QMessageBox.warning(
                        self,
                        "Import CSV",
                        "CSV must include name and phone columns. Other columns are stored as extra fields.",
                    )
                    return
                for row in reader:
                    payload = self._csv_dict_row_to_payload(row or {})
                    if not self._payload_has_identity(payload):
                        skipped += 1
                        continue
                    create_contact(pid, lid, payload)
                    added += 1
        except Exception as e:
            QMessageBox.critical(self, "Import CSV", f"{os.path.basename(path)}\n\n{e}")
            return
        self._fill_table()
        QMessageBox.information(
            self,
            "Import CSV",
            f"Imported {added} contact(s). Skipped {skipped} empty row(s).",
        )

    def _add_contact(self) -> None:
        cl = self._current_list()
        if not cl:
            QMessageBox.information(self, "Contacts", "Select a profile and list first.")
            return
        pid, lid = cl
        payload = {"name": "New contact", "phone": "", "email": "", "company": "", "extra": {}}
        try:
            create_contact(pid, lid, payload)
        except Exception as e:
            QMessageBox.critical(self, "Contacts", str(e))
            return
        self._fill_table()
        if self._table.rowCount() > 0:
            r = 0
            self._table.setCurrentCell(r, self._name_column_index())
            self._table.editItem(self._table.item(r, self._name_column_index()))

    def _delete_selected_contacts(self) -> None:
        ids = self._selected_contact_ids()
        if not ids:
            QMessageBox.information(self, "Contacts", "Select one or more contact rows.")
            return
        if (
            QMessageBox.question(self, "Delete contacts", f"Delete {len(ids)} selected contact(s)?")
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            delete_contacts(ids)
        except Exception as e:
            QMessageBox.critical(self, "Contacts", str(e))
            return
        self._fill_table()

    def _new_list_from_selected(self) -> None:
        cl = self._current_list()
        rows = self._selected_rows()
        if not cl:
            QMessageBox.information(self, "Lists", "Select a profile and list first.")
            return
        if not rows:
            QMessageBox.information(self, "Lists", "Select one or more rows to copy.")
            return
        pid, _src_lid = cl
        src_name = self._list_cb.currentText().strip() or "list"
        suggested = f"{src_name} - selected"
        new_name, ok = QInputDialog.getText(self, "New list from selected", "New list name:", text=suggested)
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            QMessageBox.information(self, "Lists", "Enter a list name.")
            return
        fields = self._list_field_keys()
        before_ids = {int(x.get("id", 0)) for x in fetch_contact_lists(pid)}
        try:
            create_contact_list(pid, new_name, fields=fields)
            created_lists = fetch_contact_lists(pid)
            new_ids = [int(x.get("id", 0)) for x in created_lists if int(x.get("id", 0)) not in before_ids]
            if not new_ids:
                raise RuntimeError("Could not resolve new list id after creation.")
            new_lid = max(new_ids)
            copied = 0
            for row in rows:
                payload = self._payload_from_row(row)
                if not self._payload_has_identity(payload):
                    continue
                create_contact(pid, new_lid, payload)
                copied += 1
        except Exception as e:
            QMessageBox.critical(self, "Lists", f"Could not create copied list.\n\n{e}")
            return
        self._fill_lists()
        idx = self._list_cb.findText(new_name)
        if idx >= 0:
            self._list_cb.setCurrentIndex(idx)
        QMessageBox.information(self, "Lists", f"Created '{new_name}' with {copied} copied contact(s).")

    def _open_send_with_selected(self) -> None:
        pid = self._pid()
        ids = self._selected_contact_ids()
        if not pid:
            QMessageBox.information(self, "Send", "Select a profile first.")
            return
        if not ids:
            QMessageBox.information(self, "Send", "Select one or more contact rows.")
            return
        self.open_send_requested.emit({"source": "contacts", "profile_id": int(pid), "contact_ids": ids})

    def reload_profiles(self) -> None:
        self._reload_profiles()
