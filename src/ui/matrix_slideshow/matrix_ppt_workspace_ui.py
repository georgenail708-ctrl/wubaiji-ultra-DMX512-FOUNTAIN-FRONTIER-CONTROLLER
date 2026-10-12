"""M02 slideshow editor assembly; reuse original widgets, commands and data.

References/licences: r185-matrix-redesign-adoption.md section 11.
FlowLayout is the repository's Qt BSD-3-Clause example adaptation.
"""
from __future__ import annotations

import weakref

from PyQt6.QtCore import QEvent, QObject, QPoint, QSize, Qt, pyqtSlot
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStyledItemDelegate, QToolButton,
    QVBoxLayout, QWidget,
)

from .. import qascade_theme as theme
from ..widget_kit import PanelSection, scope_qss
from ..flow_layout import FlowLayout
from ..main_icon_library import make_main_icon
from .matrix_child_window import MatrixChildWindow
from .matrix_slide_properties_ui import native_form, time_spin
from .matrix_ppt_catalog import CATEGORIES, entry_for_key, query_entries
from .matrix_ppt_composition_controls import build_composition_controls
from . import matrix_wing_flags


def _host():
    from . import matrix_slideshow_panel
    return matrix_slideshow_panel


def _caption(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color:{theme.TEXT_SECONDARY};font-size:{theme.FS_SMALL}px;")
    return label


def _button(panel, text: str, slot, *, icon: str, primary: bool = False):
    colour = theme.FOCUS_BLUE if primary else theme.TEXT_PRIMARY
    button = panel._mk_btn(text, theme.BG_BUTTON, colour, icon_key=icon)
    button.setMinimumHeight(24)
    button.clicked.connect(slot)
    return button


def _flow_host() -> tuple:
    host = QWidget()
    layout = FlowLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    policy.setHeightForWidth(True)
    host.setSizePolicy(policy)
    return host, layout


class SequenceContext(QObject):
    """Native slots with container ownership before its dynamic thumbnails."""

    def __init__(self, panel, container):
        super().__init__(container)
        self._owner_ref = weakref.ref(panel)
        container.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.ChildAdded:
            child = event.child()
            mp = _host()
            if isinstance(child, mp.SlideThumbnail):
                _configure_sequence_thumbnail(child)
                child.double_clicked.connect(self.after_thumbnail_update)
            elif isinstance(child, mp.MatrixEmptyGuide):
                _wrap_empty_actions(child)
                self.refresh_details()
        return super().eventFilter(watched, event)

    @pyqtSlot()
    def refresh_details(self) -> None:
        owner = self._owner_ref()
        properties = getattr(owner, "_slide_param_panel", None)
        if properties is not None:
            properties._ppt_properties_context.refresh()

    @pyqtSlot(int)
    def after_thumbnail_update(self, _idx: int) -> None:
        self.refresh_details()

    @pyqtSlot(QPoint)
    def open_menu(self, pos: QPoint) -> None:
        owner = self._owner_ref()
        if owner is None:
            return
        container = self.parent()
        child = container.childAt(pos)
        if isinstance(child, _host().SlideThumbnail):
            owner._on_thumb_clicked(child._idx)
        _open_sequence_menu(owner, container, container.mapToGlobal(pos))

    @pyqtSlot(QAction)
    def run_action(self, action) -> None:
        owner = self._owner_ref()
        if owner is not None:
            _run_sequence_command(owner, action.data())

    @pyqtSlot()
    def refresh_menu(self) -> None:
        owner = self._owner_ref()
        menu = self.sender()
        for action in menu.actions():
            action.setEnabled(owner is not None and _command_enabled(owner, action.data()))


def _configure_sequence_thumbnail(thumb) -> None:
    thumb.THUMB_W = 224
    thumb.THUMB_H = 96
    thumb.setFixedSize(thumb.THUMB_W + 4, thumb.THUMB_H + 28)
    slide = thumb._slide
    thumb.setToolTip(
        f"帧 {thumb._idx + 1} · {slide.name}\n"
        f"停留 {slide.hold_time:.3f}s · 过渡 {slide.transition_time:.3f}s\n"
        "单击查看；双击沿原入口用主画布更新该帧。右击打开帧操作。")


def _wrap_empty_actions(guide) -> None:
    root = guide.layout()
    for index in range(root.count()):
        row = root.itemAt(index).layout()
        if isinstance(row, QHBoxLayout):
            while row.count():
                row.takeAt(0)
            flow = FlowLayout()
            flow.setSpacing(4)
            for button in guide._buttons:
                flow.addWidget(button)
            row.addLayout(flow)


def _open_sequence_menu(panel, parent, pos) -> None:
    menu = QMenu(parent)
    for key, label, icon in (
        ("add", "添加空帧", "plus"), ("copy", "复制当前帧", "copy"),
        ("up", "上移", "arrow-up"), ("down", "下移", "arrow-down"),
        ("rename", "重命名", "pencil"), ("delete", "删除", "trash"),
    ):
        action = menu.addAction(make_main_icon(icon, 16), label)
        action.setData(key)
        action.setEnabled(_command_enabled(panel, key))
    chosen = menu.exec(pos)
    if chosen is not None:
        _run_sequence_command(panel, chosen.data())
    menu.deleteLater()


def _command_enabled(panel, key: str) -> bool:
    if key == "add":
        return True
    count = len(panel._engine.slides)
    idx = panel._engine.current_idx
    if not 0 <= idx < count:
        return False
    return {"up": idx > 0, "down": idx < count - 1, "delete": count > 1}.get(key, True)


def _run_sequence_command(panel, key: str) -> None:
    commands = {
        "add": panel._add_slide, "copy": panel._dup_slide,
        "rename": panel._rename_slide, "delete": panel._del_slide,
        "up": lambda: panel._move_slide(-1),
        "down": lambda: panel._move_slide(1),
    }
    commands[key]()
    slide_panel = panel._slide_param_panel
    idx = panel._engine.current_idx
    if 0 <= idx < len(panel._engine.slides):
        slide_panel.load_slide(panel._engine.slides[idx])
    else:
        slide_panel._ppt_properties_context.refresh()


def _build_sequence_list(panel) -> QWidget:
    section = PanelSection("帧序列")
    host = QWidget()
    host.setMinimumWidth(240)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    header = QHBoxLayout()
    header.setSpacing(4)
    panel._thumb_container = QWidget()
    context = SequenceContext(panel, panel._thumb_container)
    panel._ppt_sequence_context = context
    header.addWidget(_button(panel, "添加空帧", panel._add_slide, icon="plus", primary=True))
    menu_button = QToolButton()
    menu_button.setText("帧操作")
    menu_button.setIcon(make_main_icon("settings", 16))
    menu_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    menu_button.setToolTip("复制、上下移、重命名和删除所选帧；也可右击缩略图。")
    menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    menu = QMenu(menu_button)
    for key, label in (
        ("copy", "复制当前帧"), ("up", "上移"), ("down", "下移"),
        ("rename", "重命名"), ("delete", "删除"),
    ):
        action = menu.addAction(label)
        action.setData(key)
    menu.triggered.connect(context.run_action)
    menu.aboutToShow.connect(context.refresh_menu)
    menu_button.setMenu(menu)
    header.addWidget(menu_button)
    layout.addLayout(header)
    panel._thumb_scroll = QScrollArea()
    panel._thumb_scroll.setWidgetResizable(True)
    panel._thumb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    panel._thumb_container.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    panel._thumb_container.customContextMenuRequested.connect(context.open_menu)
    panel._thumb_lay = QVBoxLayout(panel._thumb_container)
    panel._thumb_lay.setContentsMargins(4, 4, 4, 4)
    panel._thumb_lay.setSpacing(4)
    panel._thumb_lay.addStretch()
    panel._thumb_scroll.setWidget(panel._thumb_container)
    layout.addWidget(panel._thumb_scroll, 1)
    layout.addWidget(_caption("单击查看 · 双击从主画布更新 · 右击修改"))
    section.add_widget(host)
    return section


def _build_frame_commands(panel) -> QWidget:
    section = PanelSection("主画布 → 幻灯片帧")
    save_host, save_flow = _flow_host()
    save_flow.addWidget(_button(panel, "保存为新幻灯片", panel._save_as_new_slide,
                                icon="project_save", primary=True))
    update_button = _button(panel, "更新当前帧", panel._update_current_slide, icon="project_save")
    update_button.clicked.connect(panel._ppt_sequence_context.refresh_details)
    save_flow.addWidget(update_button)
    section.add_widget(save_host)
    section.add_widget(_caption("以 Matrix 主窗口当前画布存为新帧，或更新当前帧；不保存文件。"))
    actions_host, actions_flow = _flow_host()
    for label, slot, icon in (
        ("全选 ON", panel._canvas.set_all_on, "select_all"),
        ("全选 OFF", panel._canvas.set_all_off, "clear_output"),
        ("反选", panel._canvas.invert_all, "layout_reset"),
    ):
        actions_flow.addWidget(_button(panel, label, slot, icon=icon))
    section.add_widget(actions_host)
    ext_host, ext_flow = _flow_host()
    panel._add_frame_ext_buttons(ext_flow)
    section.add_widget(ext_host)
    section.add_widget(_caption("ON/OFF、反选与画笔修改主画布；所查帧需显式更新或存为新帧。"))
    return section


class PptCatalogContext(QObject):
    """Filter the existing sequence combo; changing a filter never loads frames."""

    @pyqtSlot()
    def refill(self) -> None:
        panel = self.parent()
        combo = panel._preset_combo
        old_key = combo.currentData()
        rows = query_entries(panel._ppt_category_combo.currentData(), panel._ppt_search_edit.text())
        combo.blockSignals(True)
        combo.clear()
        getattr(combo, "_icon_factories", {}).clear()
        combo.addItem("── 选择内置序列 ──", "")
        combo.setItemData(0, _picker_item_size(combo, combo.itemText(0)), Qt.ItemDataRole.SizeHintRole)
        for entry in rows:
            self._append_entry(entry)
        selected = combo.findData(old_key)
        combo.setCurrentIndex(max(0, selected))
        combo.blockSignals(False)
        self.refresh_selection()

    def _append_entry(self, entry) -> None:
        mp = _host()
        combo = self.parent()._preset_combo
        factory = lambda name=entry.key: mp._matrix_builtin_sequence_icon(name)
        if isinstance(combo, mp._LazyIconComboBox):
            combo.add_lazy_item(factory, entry.label, entry.key)
        else:
            combo.addItem(factory(), entry.label, entry.key)
        combo.setItemData(combo.count() - 1, entry.summary, Qt.ItemDataRole.ToolTipRole)
        combo.setItemData(combo.count() - 1, _picker_item_size(combo, entry.label), Qt.ItemDataRole.SizeHintRole)

    @pyqtSlot()
    def refresh_selection(self) -> None:
        panel = self.parent()
        entry = entry_for_key(panel._preset_combo.currentData() or "")
        panel._ppt_preset_load.setEnabled(entry is not None)
        panel._ppt_preset_summary.setText(entry.summary if entry else "按用途选择；输入名称可搜索全部类别和旧名称。")
        density = getattr(panel, "_ppt_density_spin", None)
        if density is not None:
            density.setEnabled(entry is not None and entry.density_supported)
            density.setToolTip("生成时调整同时开启的设备数量。" if entry and entry.density_supported
                               else "该序列的开启数量由图案决定；可用翼、块和方向编组。")


def _picker_item_size(combo, label):
    metrics = combo.fontMetrics()
    return QSize(max(200, metrics.horizontalAdvance(label) + combo.iconSize().width() + 20),
                 max(metrics.height() + 8, combo.iconSize().height() + 2))


def _preset_field(panel) -> QWidget:
    mp = _host()
    host = QWidget()
    root = QVBoxLayout(host)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(4)
    filters, flow = _flow_host()
    panel._ppt_category_combo = QComboBox()
    for category in CATEGORIES:
        panel._ppt_category_combo.addItem(category, category)
    panel._ppt_category_combo.setAccessibleName("内置序列用途")
    panel._ppt_search_edit = QLineEdit()
    panel._ppt_search_edit.setPlaceholderText("搜索名称 / 旧名称")
    panel._ppt_search_edit.setAccessibleName("搜索内置序列")
    flow.addWidget(panel._ppt_category_combo)
    flow.addWidget(panel._ppt_search_edit)
    root.addWidget(filters)
    chooser, flow = _flow_host()
    combo = mp._LazyIconComboBox() if mp._LAZY_ICONS else QComboBox()
    panel._preset_combo = combo
    combo.setIconSize(QSize(32, 32))
    combo.setMaxVisibleItems(18)
    combo.setItemDelegate(QStyledItemDelegate(combo))
    combo.view().window().setMaximumHeight(combo.maxVisibleItems() * _picker_item_size(combo, "").height() + 16)
    combo.setMinimumWidth(200)
    combo.setAccessibleName("内置序列")
    combo.view().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    flow.addWidget(combo)
    panel._ppt_preset_load = _button(panel, "加载", panel._load_preset, icon="layout_reset")
    panel._ppt_preset_load.setToolTip("用所选内置序列替换当前帧序列。")
    flow.addWidget(panel._ppt_preset_load)
    root.addWidget(chooser)
    panel._ppt_preset_summary = _caption("")
    root.addWidget(panel._ppt_preset_summary)
    root.addWidget(build_composition_controls(panel))
    context = PptCatalogContext(panel)
    panel._ppt_catalog_context = context
    panel._ppt_category_combo.currentIndexChanged.connect(context.refill)
    panel._ppt_search_edit.textChanged.connect(context.refill)
    combo.currentIndexChanged.connect(context.refresh_selection)
    context.refill()
    from .matrix_library_preferences import bind_ppt_library_preferences
    bind_ppt_library_preferences(panel)
    return host


def _build_sequence_settings(panel) -> QWidget:
    section = PanelSection("内置序列与编排")
    form = native_form()
    form.addRow("内置序列", _preset_field(panel))
    panel._wings_spin = QSpinBox()
    panel._wings_spin.setRange(1, 64)
    panel._wings_spin.setValue(1)
    panel._wings_spin.setToolTip("翼数：1翼=整体波形；2翼=两段完全一致的同向波形。")
    panel._wings_spin.valueChanged.connect(panel._sync_wing_block_to_pixel_engine)
    form.addRow("翼", panel._wings_spin)
    panel._block_spin = QSpinBox()
    panel._block_spin.setRange(1, 128)
    panel._block_spin.setValue(1)
    panel._block_spin.setToolTip("块大小：N个设备作为一个单元同步运动。")
    panel._block_spin.valueChanged.connect(panel._sync_wing_block_to_pixel_engine)
    form.addRow("块", panel._block_spin)
    form.addRow("方向", panel._make_wing_direction_toolbar())
    form.addRow("翼变换", matrix_wing_flags.create(panel))
    panel._skip_base_order_chk = QCheckBox("跳过编号排序")
    panel._skip_base_order_chk.setToolTip("勾选后按坐标排序；不勾选保持设备组选择顺序分翼块。")
    panel._skip_base_order_chk.toggled.connect(panel._on_skip_base_order_toggled)
    form.addRow(panel._skip_base_order_chk)
    panel._ppt_density_spin = QDoubleSpinBox()
    panel._ppt_density_spin.setRange(0.02, 1.0)
    panel._ppt_density_spin.setSingleStep(0.05)
    panel._ppt_density_spin.setDecimals(2)
    panel._ppt_density_spin.setValue(0.35)
    panel._ppt_density_spin.setToolTip("数值越高，同一帧同时开启的设备越多。")
    form.addRow("密度", panel._ppt_density_spin)
    panel._ppt_catalog_context.refresh_selection()
    section.add_layout(form)
    return section


def _apply_field(panel, spin, slot) -> QWidget:
    host = QWidget()
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    row.addWidget(spin, 1)
    row.addWidget(_button(panel, "应用", slot, icon="play"))
    return host


def _build_global_times(panel) -> QWidget:
    section = PanelSection("批量时间 · 所有帧")
    form = native_form()
    panel._global_hold_spin = time_spin(1.0, 0.1, manual=True)
    panel._global_hold_spin.setToolTip("应用后更新所有帧停留；0=手动切换，最大24小时。")
    form.addRow("停留", _apply_field(panel, panel._global_hold_spin, panel._apply_global_hold))
    panel._global_trans_spin = time_spin(0.2, 0.05)
    panel._global_trans_spin.setToolTip("应用后更新所有帧进入时的过渡；0=立切，最大24小时。")
    form.addRow("过渡", _apply_field(panel, panel._global_trans_spin, panel._apply_global_transition))
    section.add_layout(form)
    return section


def _build_properties_scroll(panel) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.setSpacing(4)
    selected = PanelSection("所查帧参数")
    panel._slide_param_panel = _host().SlideParamPanel()
    panel._slide_param_panel.params_changed.connect(panel._on_slide_params_changed)
    panel._slide_param_panel._ppt_properties_context.bind_owner(panel)
    selected.add_widget(panel._slide_param_panel)
    layout.addWidget(selected)
    layout.addWidget(_build_frame_commands(panel))
    layout.addWidget(_build_sequence_settings(panel))
    panel._ppt_bpm_section = PanelSection("序列节拍 · BPM")
    layout.addWidget(panel._ppt_bpm_section)
    layout.addWidget(_build_global_times(panel))
    layout.addStretch()
    scroll.setWidget(host)
    return scroll


def build_ppt_workspace(panel) -> QWidget:
    window = MatrixChildWindow(panel, "矩阵 · 幻灯片", size=(1040, 720), min_size=(640, 480))
    layout = QVBoxLayout(window)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.setSpacing(4)
    header = QFrame()
    header.setObjectName("matrixPptContextHeader")
    scope_qss(header, f"background:{theme.BG_EDITOR};border-radius:4px;")
    header_layout = QVBoxLayout(header)
    header_layout.setContentsMargins(8, 4, 8, 4)
    header_layout.addWidget(_caption("幻灯片 · 帧序列　/　画布编辑在 Matrix 主窗口"))
    layout.addWidget(header)
    split = QSplitter(Qt.Orientation.Horizontal)
    split.setHandleWidth(4)
    split.setChildrenCollapsible(False)
    split.addWidget(_build_sequence_list(panel))
    split.addWidget(_build_properties_scroll(panel))
    split.setStretchFactor(0, 0)
    split.setStretchFactor(1, 1)
    split.setSizes([280, 752])
    panel._ppt_workspace_splitter = split
    layout.addWidget(split, 1)
    return window


__all__ = ["build_ppt_workspace"]
