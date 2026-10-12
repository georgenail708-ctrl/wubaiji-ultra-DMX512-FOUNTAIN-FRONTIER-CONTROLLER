"""User search/fold preferences for the two existing card palettes."""
from __future__ import annotations

from functools import partial

from PyQt6.QtCore import QEvent, QObject, QSignalBlocker, Qt

from .operating_preferences import bool_value, settings, text_value


class _PaletteMemory(QObject):
    def __init__(self, palette, page):
        super().__init__(palette)
        self.palette = palette
        self.store = settings(page, palette)
        self.headers = {}

    def restore_search(self):
        query = text_value(self.store, "search")
        blocker = QSignalBlocker(self.palette.search)
        self.palette.search.setText(query)
        del blocker
        self.palette.apply_filter(query)
        self.palette.search.textEdited.connect(self.remember_search)

    def remember_search(self, query):
        self.store.setValue("search", query)

    def remember_studio_fold(self, section, _checked):
        expanded = section.is_expanded()
        if section._mem_expanded is not None:
            section._mem_expanded = expanded
        self.store.setValue("expanded/" + section.group(), expanded)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton:
                key, section = self.headers[watched]
                self.store.setValue("expanded/" + key, section._header._expanded)
        return False


def bind_studio_preferences(palette):
    memory = _PaletteMemory(palette, "studio")
    palette._operating_palette_memory = memory
    for section in palette.sections():
        key = "expanded/" + section.group()
        section.set_expanded(bool_value(memory.store, key, section.is_expanded()))
        section.header().clicked.connect(partial(memory.remember_studio_fold, section))
    memory.restore_search()


def bind_draw_preferences(palette):
    memory = _PaletteMemory(palette, "audio_mod_draw")
    palette._operating_palette_memory = memory
    sections = {"sources": palette._sections[0], "templates": palette._sections[1],
                "presets": palette._preset_section, "effects": palette._fx_section}
    for key, section in sections.items():
        expanded = bool_value(memory.store, "expanded/" + key, section._header._expanded)
        section.set_expanded(expanded)
        memory.headers[section._header] = (key, section)
        section._header.installEventFilter(memory)
    memory.restore_search()
