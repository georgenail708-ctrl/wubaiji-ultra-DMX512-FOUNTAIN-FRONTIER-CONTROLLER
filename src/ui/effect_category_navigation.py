"""Native Effects navigation over the original category combo models.

References: Qt model/view + QItemSelectionModel APIs, original EffectCategoryBar,
Property-list layout with facet structure.
No classification, counts, engine calls or replacement item model live here.
"""
from __future__ import annotations

import weakref
from typing import TYPE_CHECKING

from PyQt6 import sip
from PyQt6.QtCore import (
    QItemSelectionModel, QModelIndex, QSignalBlocker, QSize, Qt, pyqtSlot,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QFrame, QLabel, QListView, QSizePolicy,
    QStyledItemDelegate, QToolTip, QVBoxLayout, QWidget,
)

from . import qascade_theme as theme
from .effect_categories import TOP_FAV, TOP_RECENT

if TYPE_CHECKING:
    from .effect_category_bar import EffectCategoryBar

_ROW_HEIGHT = 28
_TOP_VISIBLE_ROWS = 10


def _alive(ref):
    obj = ref()
    return obj if obj is not None and not sip.isdeleted(obj) else None


def _combo_accepts_index(combo, index):
    return (not sip.isdeleted(combo) and not combo.signalsBlocked()
            and index.model() is combo.model())


class _WeakSync:
    """The bar may outlive a replaced UI; it never retains that UI via a slot."""

    def __init__(self, navigation):
        self._navigation_ref = weakref.ref(navigation)

    def __call__(self):
        navigation = _alive(self._navigation_ref)
        if navigation is not None:
            navigation.sync_from_bar()


class _CategoryDelegate(QStyledItemDelegate):
    """Keep native painting; only supply row height and all-library context."""

    def __init__(self, parent, *, bar, fine=False):
        super().__init__(parent)
        self._bar_ref = weakref.ref(bar)
        self._fine = fine

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width(), _ROW_HEIGHT)

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        bar = _alive(self._bar_ref)
        if not self._fine or bar is None or bar.top_key():
            return
        key = str(index.data(Qt.ItemDataRole.UserRole) or "")
        if "/" in key and not option.text.startswith(key):
            option.text = f"{key.split('/', 1)[0]} · {option.text}"

    def helpEvent(self, event, view, option, index):
        if not index.isValid():
            return False
        key = str(index.data(Qt.ItemDataRole.UserRole) or "")
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        QToolTip.showText(event.globalPos(), f"{key}\n{text}" if self._fine and key else text, view)
        return True


def _view_style(name):
    colors = theme.EDITOR_IVORY
    selector = f"QListView#{name}"
    return (
        f'{selector}{{background:{colors["panel"]};color:{colors["ink"]};'
        'border:none;padding:0px;}'
        f'{selector}::item{{padding:0px 4px;border:1px solid transparent;'
        'border-radius:3px;background:transparent;}'
        f'{selector}::item:hover{{background:{colors["hover"]};}}'
        f'{selector}::item:selected{{background:{colors["selected"]};'
        f'color:{colors["ink"]};}}'
        f'{selector}::item:focus{{border-color:{colors["focus"]};}}'
        f'{selector}:disabled{{color:{colors["disabled"]};}}'
    )


def _heading(parent, text, name):
    label = QLabel(text, parent)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setFont(theme.cached_font(12, bold=True))
    label.setStyleSheet(
        f'color:{theme.EDITOR_IVORY["muted"]};background:transparent;border:none;')
    return label


def _list_view(parent, combo, *, bar, fine=False):
    view = QListView(parent)
    name = "fxCategoryFine" if fine else "fxCategoryTop"
    view.setObjectName(name)
    view.setAccessibleName("效果细分类" if fine else "效果大类与快捷分类")
    view.setFrameShape(QFrame.Shape.NoFrame)
    view.setFont(theme.cached_font(12))
    view.setIconSize(QSize(16, 16))
    view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    view.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    view.setTextElideMode(Qt.TextElideMode.ElideRight)
    view.setUniformItemSizes(True)
    view.setSpacing(0)
    view.setModel(combo.model())
    view.setItemDelegate(_CategoryDelegate(view, bar=bar, fine=fine))
    view.setStyleSheet(_view_style(name))
    view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    return view


def _sync_selection(view, combo, *, search):
    selection = view.selectionModel()
    blocker = QSignalBlocker(selection)
    try:
        selection.clear()
        if not search:
            index = combo.model().index(combo.currentIndex(), 0)
            selection.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
    finally:
        blocker.unblock()


class EffectCategoryNavigation(QWidget):
    """Owned presentation only; the bar remains the filter and model owner."""

    def __init__(self, bar: EffectCategoryBar):
        super().__init__(bar)
        self._bar_ref = weakref.ref(bar)
        self.setObjectName("fxCategoryNavigation")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._top_heading = _heading(self, "效果分类", "fxCategoryHeading")
        self._top_view = _list_view(self, bar._top_combo, bar=bar)
        self._sub_heading = _heading(self, "细分类", "fxCategoryFineHeading")
        self._sub_view = _list_view(self, bar.fine_combo, bar=bar, fine=True)
        self._search_hint = _heading(self, "全库搜索中 · 分类暂不参与筛选", "fxCategorySearch")
        self._search_hint.setWordWrap(True)
        layout.addWidget(self._top_heading)
        layout.addWidget(self._top_view)
        layout.addSpacing(4)
        layout.addWidget(self._sub_heading)
        layout.addWidget(self._sub_view, 1)
        layout.addWidget(self._search_hint)
        self._top_view.selectionModel().currentChanged.connect(self._top_changed)
        self._sub_view.selectionModel().currentChanged.connect(self._sub_changed)
        self.sync_from_bar()

    @pyqtSlot()
    def _fit_top(self):
        bar = _alive(self._bar_ref)
        model = self._top_view.model()
        if bar is not None and model is not None and not sip.isdeleted(model):
            rows = min(_TOP_VISIBLE_ROWS, max(1, model.rowCount()))
            self._top_view.setFixedHeight(rows * _ROW_HEIGHT + 2)

    @pyqtSlot(QModelIndex, QModelIndex)
    def _top_changed(self, current, _previous):
        self._dispatch(current, fine=False)

    @pyqtSlot(QModelIndex, QModelIndex)
    def _sub_changed(self, current, _previous):
        self._dispatch(current, fine=True)

    def _dispatch(self, current, *, fine):
        bar = _alive(self._bar_ref)
        if bar is None or not current.isValid() or bar._search_active:
            return
        combo = bar.fine_combo if fine else bar._top_combo
        if not _combo_accepts_index(combo, current):
            return
        if fine:
            bar._on_sub_combo(current.row())
        else:
            bar._on_top_combo(current.row())

    @pyqtSlot()
    def sync_from_bar(self):
        bar = _alive(self._bar_ref)
        if bar is None:
            return
        search = bool(bar._search_active)
        fine_visible = not search and bar.top_key() not in (TOP_RECENT, TOP_FAV)
        self._top_view.setEnabled(not search)
        self._sub_view.setEnabled(not search)
        self._sub_view.setVisible(fine_visible)
        self._sub_heading.setVisible(fine_visible)
        self._search_hint.setVisible(search)
        _sync_selection(self._top_view, bar._top_combo, search=search)
        _sync_selection(self._sub_view, bar.fine_combo, search=search)
        self._fit_top()
        self._sub_view.viewport().update()


def install(bar: EffectCategoryBar) -> EffectCategoryNavigation:
    """Install once into the original bar; preserve all combo/model parents."""
    navigation = getattr(bar, "_category_navigation", None)
    if navigation is not None and not sip.isdeleted(navigation):
        return navigation
    navigation = EffectCategoryNavigation(bar)
    bar._category_navigation = navigation
    bar._navigation_sync_hook = _WeakSync(navigation)
    bar._rows.hide()
    bar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
    bar.layout().addWidget(navigation, 1)
    return navigation
