"""Explicit Patch/TopView tool searches; original objects remain authoritative.

Reuse Qt 6.11 QSettings/textEdited/QSignalBlocker, no copied upstream code.
Reference: r189-aux-operating-preferences.md sections 10-12.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, QSignalBlocker, pyqtSlot

from .operating_preferences import settings, text_value


class _SearchMemory(QObject):
    def __init__(self, page, editor, refresh):
        super().__init__(editor)
        self._store = settings(page, self)
        with QSignalBlocker(editor):
            editor.setText(text_value(self._store, "search"))
        refresh()
        editor.textEdited.connect(self.remember_search)

    @pyqtSlot(str)
    def remember_search(self, text):
        self._store.setValue("search", text)


def bind_search_preference(page, editor, refresh):
    editor._operating_search_memory = _SearchMemory(page, editor, refresh)
