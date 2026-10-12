"""Creation filters and actual Pixel folds, separate from saved Matrix scenes.

Qt 6.11 textEdited/activated/QSettings and original PanelHeader are reused.
Reference: r189-aux-operating-preferences.md sections 10-12. No new library.
"""
from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QSignalBlocker, Qt, pyqtSlot

from ..operating_preferences import bool_value, choice_value, settings, text_value
from .matrix_ppt_catalog import CATEGORIES


class _PptLibraryMemory(QObject):
    def __init__(self, panel):
        super().__init__(panel._ppt_search_edit)
        self._panel = panel
        self._store = settings("matrix_ppt", self)
        search, category = panel._ppt_search_edit, panel._ppt_category_combo
        preferred = choice_value(self._store, "category", CATEGORIES, category.currentData())
        with QSignalBlocker(search), QSignalBlocker(category):
            search.setText(text_value(self._store, "search"))
            category.setCurrentIndex(category.findData(preferred))
        panel._ppt_catalog_context.refill()
        search.textEdited.connect(self.remember_search)
        category.activated.connect(self.remember_category)

    @pyqtSlot(str)
    def remember_search(self, text):
        self._store.setValue("search", text)

    @pyqtSlot(int)
    def remember_category(self, _row):
        self._store.setValue("category", self._panel._ppt_category_combo.currentData())


class _PixelLibraryMemory(QObject):
    def __init__(self, context):
        super().__init__(context.parent())
        self._context = context
        self._store = settings("matrix_pixel", self)
        self._headers = {}
        for index, (section, _view) in enumerate(context.sections):
            header = section._header
            key = "expanded/" + header._title
            section.set_expanded(bool_value(self._store, key, header._expanded))
            self._headers[header] = (key, index)
            header.installEventFilter(self)
        search = context.parent()
        with QSignalBlocker(search):
            search.setText(text_value(self._store, "search"))
        context.filter_modes(search.text())
        search.textEdited.connect(self.remember_search)

    @pyqtSlot(str)
    def remember_search(self, text):
        self._store.setValue("search", text)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            key, index = self._headers[watched]
            self._store.setValue(key, watched._expanded)
            folds = self._context._folds
            if folds is not None:
                folds[index] = watched._expanded
        return super().eventFilter(watched, event)


def bind_ppt_library_preferences(panel):
    panel._ppt_search_edit._operating_library_memory = _PptLibraryMemory(panel)


def bind_pixel_library_preferences(context):
    context.parent()._operating_library_memory = _PixelLibraryMemory(context)
