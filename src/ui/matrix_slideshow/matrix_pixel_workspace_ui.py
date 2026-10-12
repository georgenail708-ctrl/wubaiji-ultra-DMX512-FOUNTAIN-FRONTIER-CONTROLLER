"""M03 independent editor: functional library, current details, common values.

References and licences: r185-matrix-pixel-redesign-preflight.md sections 0/9.
Qt native item views and the original lazy Tabler (MIT) icon factories are used.
The original combo is the sole mode-selection authority; no engine work here.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, QSignalBlocker, QSize, Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListView, QListWidget, QListWidgetItem, QScrollArea, QSizePolicy,
    QSplitter, QVBoxLayout, QWidget,
)

from .. import qascade_theme as theme
from ..widget_kit import PanelSection
from .matrix_child_window import MatrixChildWindow
from .matrix_pixel_catalog import creation_categories, purpose

_COMBO_ROW = int(Qt.ItemDataRole.UserRole)
_SEARCH_TEXT = _COMBO_ROW + 1
_ICON_READY = _COMBO_ROW + 2


def _host():
    from . import matrix_slideshow_panel
    return matrix_slideshow_panel


def _caption(text):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color:{theme.TEXT_SECONDARY};font-size:{theme.FS_SMALL}px;")
    return label


class _ModeTiles(QListWidget):
    """Native tiles; reuse combo icons only for exposed viewport items."""

    def __init__(self, combo, parent=None):
        super().__init__(parent)
        self._combo = combo
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setMovement(QListView.Movement.Static)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setLayoutMode(QListView.LayoutMode.Batched)
        self.setBatchSize(32)
        self.setWrapping(True)
        self.setWordWrap(True)
        self.setUniformItemSizes(True)
        self.setIconSize(QSize(34, 34))
        self.setGridSize(QSize(96, 80))
        self.setSpacing(0)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumHeight(84)
        self.setMaximumHeight(244)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def refresh_icons(self):
        viewport = self.viewport()
        visible = viewport.visibleRegion()
        if visible.isEmpty():
            return
        for index in range(self.count()):
            item = self.item(index)
            rect = self.visualItemRect(item)
            if item.isHidden() or item.data(_ICON_READY) or not visible.intersects(rect):
                continue
            row = int(item.data(_COMBO_ROW))
            render = getattr(self._combo, "_render_range", None)
            if render is not None:
                render(row, row)
            item.setIcon(self._combo.itemIcon(row))
            item.setData(_ICON_READY, True)

    def paintEvent(self, event):
        self.refresh_icons()
        super().paintEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_icons()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.refresh_icons()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self.refresh_icons()


class _PixelLibraryContext(QObject):
    """UI-only native slots, owned before combo/view models are constructed."""

    def __init__(self, search):
        super().__init__(search)
        self.combo = None
        self.sections = []
        self.count_label = None
        self.scope_label = None
        self._folds = None

    @pyqtSlot(str)
    def filter_modes(self, text):
        query = text.strip().casefold()
        self._remember_folds(bool(query))
        found = 0
        total = 0
        for section, view in self.sections:
            matches = _filter_view(view, query)
            found += matches
            total += view.count()
            section.setVisible(matches > 0)
            if query and matches:
                section.set_expanded(True)
            view.refresh_icons()
        self.count_label.setText(f"{found} / {total} 个效果" if query else f"{total} 个效果")

    def _remember_folds(self, searching):
        if searching and self._folds is None:
            self._folds = [section._header._expanded for section, _view in self.sections]
        elif not searching and self._folds is not None:
            for (section, _view), expanded in zip(self.sections, self._folds):
                section.set_expanded(expanded)
            self._folds = None

    @pyqtSlot("QListWidgetItem*")
    def choose_item(self, item):
        row = int(item.data(_COMBO_ROW))
        if self.combo.itemData(row) and self.combo.currentIndex() != row:
            self.combo.setCurrentIndex(row)

    @pyqtSlot(int)
    def current_mode_changed(self, row):
        mode_id = self.combo.itemData(row)
        if mode_id:
            self.scope_label.setText(f"当前参数 · {self.combo.itemText(row)}")
        else:
            self.scope_label.setText("选择像素效果 · 当前参数")
        for _section, view in self.sections:
            _mark_current(view, row if mode_id else -1)

    @pyqtSlot(int)
    def library_scrolled(self, _value):
        for _section, view in self.sections:
            view.refresh_icons()


def _filter_view(view, query):
    count = 0
    for index in range(view.count()):
        item = view.item(index)
        matches = not query or query in item.data(_SEARCH_TEXT)
        item.setHidden(not matches)
        count += int(matches)
    return count


def _mark_current(view, row):
    view.clearSelection()
    for index in range(view.count()):
        item = view.item(index)
        if item.data(_COMBO_ROW) == row:
            item.setSelected(True)
            view.setCurrentItem(item)
            return


def _library_shell(window):
    """Search owns the context before the other new UI QObject subtrees."""
    library = QWidget(window)
    layout = QVBoxLayout(library)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    search = QLineEdit(library)
    search.setPlaceholderText("搜索效果名称、分类或 ID")
    search.setClearButtonEnabled(True)
    context = _PixelLibraryContext(search)
    layout.addWidget(_caption("功能库"))
    layout.addWidget(search)
    context.count_label = _caption("")
    layout.addWidget(context.count_label)
    scroll = QScrollArea(library)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    body = QWidget()
    contents = QVBoxLayout(body)
    contents.setContentsMargins(0, 0, 0, 0)
    contents.setSpacing(4)
    scroll.setWidget(body)
    layout.addWidget(scroll, 1)
    scroll.verticalScrollBar().valueChanged.connect(context.library_scrolled)
    search.textChanged.connect(context.filter_modes)
    library.setMinimumWidth(144)
    return library, contents, context


def _mode_combo(panel, parent):
    mp = _host()
    combo = mp._LazyIconComboBox(parent) if mp._LAZY_ICONS else QComboBox(parent)
    combo.setIconSize(QSize(34, 34))
    combo.setMaxVisibleItems(22)
    for category, effects in creation_categories(mp.PIXEL_MODES).items():
        combo.addItem(f"── {category} ──")
        _add_combo_effects(combo, category, effects, mp)
    combo.setMinimumWidth(160)
    combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    combo.view().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    # Keep original noneditable combo and its initial noData category row.
    # Initial engine-to-selector restoration is a separately validated candidate.
    panel._pixel_effect_combo = combo
    combo.currentIndexChanged.connect(panel._on_pixel_effect_changed)
    return combo


def _add_combo_effects(combo, category, effects, mp):
    for mode_id, name in effects:
        if isinstance(combo, mp._LazyIconComboBox):
            combo.add_lazy_item(
                lambda m=mode_id, n=name, c=category: mp._matrix_pixel_mode_icon(m, n, c),
                name, mode_id)
        else:
            combo.addItem(mp._matrix_pixel_mode_icon(mode_id, name, category), name, mode_id)


def _populate_library(contents, context):
    row = 0
    for index, (category, effects) in enumerate(creation_categories(_host().PIXEL_MODES).items()):
        row += 1  # Original combo category row has no mode data.
        section = PanelSection(category, expanded=index == 0)
        view = _ModeTiles(context.combo, section)
        view.setMinimumHeight(min(244, max(84, ((len(effects) + 1) // 2) * 80 + 4)))
        for mode_id, name in effects:
            item = QListWidgetItem(name)
            item.setSizeHint(QSize(96, 80))
            item.setData(_COMBO_ROW, row)
            item.setData(_SEARCH_TEXT, f"{category} {name} {mode_id}".casefold())
            item.setToolTip(f"{purpose(mode_id, category)}\n{mode_id}")
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            view.addItem(item)
            row += 1
        view.itemClicked.connect(context.choose_item)
        view.itemActivated.connect(context.choose_item)
        section.add_widget(view)
        context.sections.append((section, view))
        contents.addWidget(section)
    contents.addStretch()
    context.filter_modes("")


def _parameter_panel(panel):
    params = _host().PixelEffectParamPanel()
    params.set_engine(panel._pixel_engine)
    params.params_changed.connect(panel._on_pixel_params_changed)
    params.color_changed.connect(panel._on_matrix_effect_color_changed)
    if hasattr(params, "video_cleared"):
        params.video_cleared.connect(panel._on_pixel_video_cleared)
    panel._pixel_param_panel = params
    return params


def _compat_mode_row(combo, mode_id):
    """Missing saved identity is displayed, never substituted or activated."""
    row = combo.findData(mode_id)
    if row >= 0:
        return row
    is_ndi = mode_id == "ext_ndi"
    label = ("当前工程 · NDI（远期，当前不支持）" if is_ndi
             else f"当前工程 · {mode_id}（兼容模式）")
    with QSignalBlocker(combo):
        combo.addItem(label, mode_id)
        row = combo.count() - 1
        if is_ndi:
            item = combo.model().item(row)
            item.setFlags(item.flags() & ~(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable))
    return row


def sync_pixel_editor(panel):
    """Restore selection and properties from the existing engine without edits."""
    from .matrix_external_sources import mode_of_kind
    from .matrix_pixel_engine import MODE_VIDEO_SAMPLE
    combo = getattr(panel, "_pixel_effect_combo", None)
    params = getattr(panel, "_pixel_param_panel", None)
    engine = getattr(panel, "_pixel_engine", None)
    if combo is None or params is None or engine is None:
        return
    mode = engine._mode
    target = mode_of_kind(getattr(engine, "video_source", "")) if mode == MODE_VIDEO_SAMPLE else ""
    row = _compat_mode_row(combo, target or mode)
    if row >= 0:
        with QSignalBlocker(combo):
            combo.setCurrentIndex(row)
        panel._pixel_library_context.current_mode_changed(row)
    params.show_mode_params(MODE_VIDEO_SAMPLE if (target or mode) == "ext_ndi" else mode)
    params._sync_pixel_param_widgets()


def build_pixel_workspace(panel):
    window = MatrixChildWindow(panel, "矩阵 · 像素效果",
                               size=(880, 680), min_size=(560, 420))
    root = QVBoxLayout(window)
    root.setContentsMargins(4, 4, 4, 4)
    root.setSpacing(4)
    library, contents, context = _library_shell(window)
    header = QHBoxLayout()
    header.setSpacing(4)
    header.addWidget(QLabel("像素效果"))
    combo = _mode_combo(panel, window)
    header.addWidget(combo, 1)
    header.addWidget(_caption("播放 / 输出 · Matrix 主窗"))
    root.addLayout(header)
    context.combo = combo
    context.scope_label = _caption("当前参数")
    root.addWidget(context.scope_label)
    _populate_library(contents, context)
    combo.currentIndexChanged.connect(context.current_mode_changed)
    context.current_mode_changed(combo.currentIndex())
    split = QSplitter(Qt.Orientation.Horizontal, window)
    split.setHandleWidth(4)
    split.setChildrenCollapsible(False)
    split.addWidget(library)
    split.addWidget(_parameter_panel(panel))
    split.setStretchFactor(0, 0)
    split.setStretchFactor(1, 1)
    split.setSizes([220, 652])
    root.addWidget(split, 1)
    panel._pixel_library_context = context
    panel._pixel_workspace_splitter = split
    sync_pixel_editor(panel)
    from .matrix_library_preferences import bind_pixel_library_preferences
    bind_pixel_library_preferences(context)
    return window
