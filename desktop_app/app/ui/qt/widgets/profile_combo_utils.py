"""Fill QComboBox with local profiles (same labels as Tk local mode)."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QComboBox


def populate_profile_combo(cb: QComboBox, profiles: list[dict[str, Any]]) -> None:
    cb.blockSignals(True)
    cb.clear()
    for p in profiles:
        label = f'{p.get("name", "")} ({p.get("phone", "")})'
        cb.addItem(label, p)
    cb.blockSignals(False)


def current_profile(cb: QComboBox) -> dict[str, Any] | None:
    i = cb.currentIndex()
    if i < 0:
        return None
    data = cb.itemData(i)
    return data if isinstance(data, dict) else None
