# -*- coding: utf-8 -*-
"""
PatchPanel — 设备配接面板
功能：
  1. 设备类型库管理（创建/编辑/删除）
  2. 每个通道可定义类别：R/G/B/W/Dimmer/Valve/Motor X/Y/Z/Pump/占位
  3. 批量配接到指定宇宙和起始通道
  4. 可视化配接网格（每宇宙 512 通道，32×16 格）
"""
from __future__ import annotations
import uuid
import math
import copy

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QPushButton, QListWidget, QListWidgetItem,
    QDialog, QFormLayout, QLineEdit, QSpinBox, QComboBox,
    QScrollArea, QGridLayout, QGroupBox, QMessageBox,
    QDialogButtonBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QInputDialog,
    QFrame, QSizePolicy, QSpacerItem
)
from PyQt6.QtCore import Qt, QRectF, pyqtSignal, QSize
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QBrush, QPalette

from ..core.models import FixtureDefinition, ChannelType
from ..core.device_type_registry import DeviceTypeRegistry
from .main_icon_library import make_main_icon
from .widget_kit import EmptyState, PanelSection
from .qascade_theme import (
    BG_ROOT, BG_DEEP, BG_BUTTON, BG_BUTTON_HOVER, BORDER_NORMAL,
    SELECT_BLUE, FOCUS_BLUE, BRIGHT_ERROR, BRIGHT_SUCCESS,
    INFO_BLUE, TEXT_PRIMARY, TEXT_SECONDARY, TEXT_DISABLED,
    TEXT_SELECTED_FG, SEP_LINE, SCROLLBAR_HANDLE, CATEGORY_COLORS,
)

# ──────────────────────────────────────────────────────────────────────────────
#  颜色映射（通道类别色并轨到主题共享常量 CATEGORY_COLORS；键名语义不变）
# ──────────────────────────────────────────────────────────────────────────────
def _shaded(hex_color: str, factor: int) -> str:
    """按因子派生明暗变体（fine 精通道用，纯视觉派生不改数据）。"""
    c = QColor(hex_color)
    return (c.darker(factor) if factor < 100 else c.lighter(factor)).name()


CHANNEL_COLORS = {
    "R":           CATEGORY_COLORS["red"],
    "G":           CATEGORY_COLORS["green"],
    "B":           CATEGORY_COLORS["blue"],
    "W":           CATEGORY_COLORS["white"],
    "dimmer":      CATEGORY_COLORS["dimmer"],
    "valve":       CATEGORY_COLORS["prism"],      # 紫族（原 #8e44ad）
    "motor_x":     CATEGORY_COLORS["cyan"],       # 青/绿松石族
    "motor_y":     CATEGORY_COLORS["tilt"],
    "motor_z":     CATEGORY_COLORS["fx"],
    "motor_x_fine": _shaded(CATEGORY_COLORS["cyan"], 130),
    "motor_y_fine": _shaded(CATEGORY_COLORS["tilt"], 130),
    "motor_z_fine": _shaded(CATEGORY_COLORS["fx"], 130),
    "pump":        CATEGORY_COLORS["amber"],      # 橙族（原 #d35400）
    "placeholder": CATEGORY_COLORS["other"],
}

FUNCTION_CHANNEL_PREFIX = "function_"
NEW_FUNCTION_CHANNEL = "__new_function_channel__"
MOTOR_X_16BIT = "__motor_x_16bit__"
MOTOR_Y_16BIT = "__motor_y_16bit__"
MOTOR_Z_16BIT = "__motor_z_16bit__"
MOTOR_FINE_TYPES = {"motor_x_fine", "motor_y_fine", "motor_z_fine"}
# ★ 通用 16bit 通道（用户定案 2026-08-02）：读到一个语义对应粗+精两个通道即 16bit；
#   手动创建设备时选「通用 16位」→ 粗通道 generic16 + 下一行 generic16_fine 自动配对，
#   输出层按 _fine 后缀通用拆分（与电机 16bit 同款逻辑，不限定 x/y/z）。
GENERIC_16BIT = "__generic_16bit__"
GENERIC_16BIT_COARSE = "generic16"
GENERIC_16BIT_FINE = "generic16_fine"


def _is_function_channel(ch_type: str) -> bool:
    return str(ch_type or "").startswith(FUNCTION_CHANNEL_PREFIX)


def _function_channel_number(ch_type: str) -> int:
    try:
        return int(str(ch_type).split("_", 1)[1])
    except Exception:
        return 0


def _function_channel_label(ch_type: str, names: dict[str, str] | None = None) -> str:
    if names:
        custom = str(names.get(str(ch_type), "") or "").strip()
        if custom:
            return custom
    n = _function_channel_number(ch_type)
    return f"功能通道 {n}" if n > 0 else "功能通道"


def _channel_label(ch_type: str, names: dict[str, str] | None = None) -> str:
    if _is_function_channel(ch_type):
        return _function_channel_label(ch_type, names)
    try:
        return ChannelType(ch_type).label()
    except Exception:
        return str(ch_type or "")


def _channel_color(ch_type: str) -> str:
    if _is_function_channel(ch_type):
        # 功能通道特殊色与 programmer_panel 保持跨页一致（并轨需两文件同步，登记待办）
        return "#607D8B"
    return CHANNEL_COLORS.get(ch_type, CATEGORY_COLORS["other"])

# 局部特化：仅面板基底底色与文字基线；控件四态全部由全局 GLOBAL_QSS 提供
# （原 STYLE 中按钮/列表/分组框/输入框/表格/表头/Tab/滚动条等通用段已删）。
# §10.4 对话框按钮语义段（本面板携带内联 STYLE，需自带 primary/cancel 规则；
# 纯净对话框直接走 widget_kit.apply_dialog_chrome）。
_BTN_ROLE_QSS = f"""
QDialog QPushButton#primary {{
    background-color:{SELECT_BLUE}; color:{TEXT_SELECTED_FG};
    border:1px solid {SELECT_BLUE}; border-radius:4px;
    min-height:24px; padding:2px 18px; font-weight:normal; }}
QDialog QPushButton#primary:hover {{ background-color:#5480C5; }}
QDialog QPushButton[role="cancel"] {{
    background-color:{BG_BUTTON}; color:{TEXT_PRIMARY};
    border:1px solid {BORDER_NORMAL}; border-radius:4px;
    min-height:24px; padding:2px 18px; }}
QDialog QPushButton[role="cancel"]:hover {{ background-color:{BG_BUTTON_HOVER}; }}
"""
STYLE = f"""
QWidget {{ background:{BG_ROOT}; color:{TEXT_PRIMARY}; font-size:12px; }}
QListWidget::item {{ border-bottom:1px solid {SEP_LINE}; }}
""" + _BTN_ROLE_QSS


# ──────────────────────────────────────────────────────────────────────────────
#  设备类型编辑对话框
# ──────────────────────────────────────────────────────────────────────────────
class FixtureDefDialog(QDialog):
    """创建或编辑设备类型（FixtureDefinition）"""

    def __init__(self, fdef: FixtureDefinition = None, parent=None):
        super().__init__(parent)
        self._fdef = fdef if fdef is not None else FixtureDefinition()
        self._function_channel_names = self._collect_function_channel_names()
        self._function_channel_defaults = self._collect_function_channel_defaults()
        self._function_channels = self._collect_function_channels()
        self.setWindowTitle("设备类型编辑器")
        self.setMinimumSize(520, 520)
        self.setStyleSheet(STYLE)
        self._build_ui()
        self._load_data()

    def _collect_function_channel_names(self) -> dict[str, str]:
        names = getattr(self._fdef, "function_channel_names", {}) or {}
        if not isinstance(names, dict):
            return {}
        return {str(k): str(v) for k, v in names.items() if _is_function_channel(str(k))}

    def _collect_function_channel_defaults(self) -> dict[str, int]:
        defaults = getattr(self._fdef, "function_channel_defaults", {}) or {}
        if not isinstance(defaults, dict):
            return {}
        result = {}
        for k, v in defaults.items():
            ch_type = str(k)
            if not _is_function_channel(ch_type):
                continue
            try:
                result[ch_type] = max(0, min(255, int(v)))
            except Exception:
                result[ch_type] = 0
        return result

    def _collect_function_channels(self) -> list[str]:
        found = []
        for ch_type in getattr(self._fdef, "channels", []) or []:
            if _is_function_channel(ch_type) and ch_type not in found:
                found.append(str(ch_type))
        for ch_type in self._function_channel_names:
            if _is_function_channel(ch_type) and ch_type not in found:
                found.append(str(ch_type))
        found.sort(key=_function_channel_number)
        return found

    def _next_function_channel(self, name: str = "") -> str:
        used = {_function_channel_number(ch) for ch in self._function_channels}
        n = 1
        while n in used:
            n += 1
        ch_type = f"{FUNCTION_CHANNEL_PREFIX}{n}"
        self._function_channels.append(ch_type)
        self._function_channel_names[ch_type] = name.strip() or _function_channel_label(ch_type)
        self._function_channel_defaults[ch_type] = 0
        self._function_channels.sort(key=_function_channel_number)
        return ch_type

    def _refresh_function_list(self):
        if not hasattr(self, "_function_list"):
            return
        self._function_list.clear()
        for ch_type in self._function_channels:
            label = _function_channel_label(ch_type, self._function_channel_names)
            default = int(self._function_channel_defaults.get(ch_type, 0))
            item = QListWidgetItem(f"{label}    默认 {default}")
            item.setData(Qt.ItemDataRole.UserRole, ch_type)
            self._function_list.addItem(item)

    def _refresh_all_channel_combos(self, current_by_row: dict[int, str] | None = None):
        for r in range(self._ch_table.rowCount()):
            combo = self._ch_table.cellWidget(r, 1)
            if not isinstance(combo, QComboBox):
                continue
            old = current_by_row.get(r) if current_by_row else combo.currentData()
            if _is_function_channel(old) and old not in self._function_channels:
                old = ChannelType.PLACEHOLDER.value
            combo.blockSignals(True)
            self._fill_channel_combo(combo, str(old or ""))
            combo.blockSignals(False)
            self._update_color_cell(r)

    def _prompt_function_channel_name(self, title: str, current: str = "") -> tuple[str, bool]:
        text, ok = QInputDialog.getText(
            self, title, "功能通道名称:", QLineEdit.EchoMode.Normal, current)
        name = text.strip()
        if ok and not name:
            QMessageBox.warning(self, "错误", "请输入功能通道名称。")
            return "", False
        return name, ok

    def _prompt_function_channel_default(self, current: int = 0) -> tuple[int, bool]:
        value, ok = QInputDialog.getInt(
            self, "功能通道默认值", "默认 DMX 值 (0-255):",
            int(current), 0, 255)
        return value, ok

    def _add_function_channel(self) -> str:
        name, ok = self._prompt_function_channel_name("新增功能通道")
        if not ok:
            return ""
        ch_type = self._next_function_channel(name)
        value, ok = self._prompt_function_channel_default(0)
        if not ok:
            self._function_channels = [ch for ch in self._function_channels if ch != ch_type]
            self._function_channel_names.pop(ch_type, None)
            self._function_channel_defaults.pop(ch_type, None)
            return ""
        self._function_channel_defaults[ch_type] = value
        self._refresh_function_list()
        self._refresh_all_channel_combos()
        return ch_type

    def _rename_function_channel(self):
        item = self._function_list.currentItem()
        if not item:
            QMessageBox.information(self, "提示", "请先选择一个功能通道。")
            return
        ch_type = item.data(Qt.ItemDataRole.UserRole)
        current = _function_channel_label(ch_type, self._function_channel_names)
        name, ok = self._prompt_function_channel_name("重命名功能通道", current)
        if not ok:
            return
        self._function_channel_names[ch_type] = name
        self._refresh_function_list()
        self._refresh_all_channel_combos()

    def _set_function_channel_default(self):
        item = self._function_list.currentItem()
        if not item:
            QMessageBox.information(self, "提示", "请先选择一个功能通道。")
            return
        ch_type = item.data(Qt.ItemDataRole.UserRole)
        value, ok = self._prompt_function_channel_default(
            int(self._function_channel_defaults.get(ch_type, 0)))
        if not ok:
            return
        self._function_channel_defaults[ch_type] = value
        self._refresh_function_list()

    def _delete_function_channel(self):
        item = self._function_list.currentItem()
        if not item:
            QMessageBox.information(self, "提示", "请先选择一个功能通道。")
            return
        ch_type = item.data(Qt.ItemDataRole.UserRole)
        label = _function_channel_label(ch_type, self._function_channel_names)
        ret = QMessageBox.question(
            self, "删除功能通道",
            f"确认删除 [{label}]？已使用该功能通道的行会改为占位。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._function_channels = [ch for ch in self._function_channels if ch != ch_type]
        self._function_channel_names.pop(ch_type, None)
        self._function_channel_defaults.pop(ch_type, None)
        current_by_row = {}
        for r in range(self._ch_table.rowCount()):
            combo = self._ch_table.cellWidget(r, 1)
            old = combo.currentData() if isinstance(combo, QComboBox) else ""
            current_by_row[r] = ChannelType.PLACEHOLDER.value if old == ch_type else old
        self._refresh_function_list()
        self._refresh_all_channel_combos(current_by_row)

    def _fill_channel_combo(self, combo: QComboBox, current: str = ""):
        combo.clear()
        for ct in ChannelType:
            if ct == ChannelType.DIMMER:
                continue
            if ct.value in MOTOR_FINE_TYPES and current != ct.value:
                continue
            combo.addItem(ct.label(), ct.value)
        combo.addItem("电机 X 双通道 16位", MOTOR_X_16BIT)
        combo.addItem("电机 Y 双通道 16位", MOTOR_Y_16BIT)
        combo.addItem("电机 Z 双通道 16位", MOTOR_Z_16BIT)
        combo.addItem("通用 双通道 16位", GENERIC_16BIT)
        # 通用 16bit 的粗/精类型也必须可选项，否则「通用 16位」分支 findData
        # 恒 -1、保存的是标记串（审查 HIGH-1）
        combo.addItem("通用 16位 (粗)", GENERIC_16BIT_COARSE)
        combo.addItem("通用 16位 (精)", GENERIC_16BIT_FINE)
        for ch_type in self._function_channels:
            combo.addItem(_function_channel_label(ch_type, self._function_channel_names), ch_type)
        combo.addItem("+ 新增功能通道", NEW_FUNCTION_CHANNEL)
        if current:
            idx = combo.findData(current)
            if idx >= 0:
                combo.setCurrentIndex(idx)

    def _on_channel_combo_changed(self, combo: QComboBox, row: int):
        current_data = combo.currentData()
        if current_data in (MOTOR_X_16BIT, MOTOR_Y_16BIT, MOTOR_Z_16BIT, GENERIC_16BIT):
            if current_data == MOTOR_X_16BIT:
                coarse, fine = "motor_x", "motor_x_fine"
            elif current_data == MOTOR_Y_16BIT:
                coarse, fine = "motor_y", "motor_y_fine"
            elif current_data == MOTOR_Z_16BIT:
                coarse, fine = "motor_z", "motor_z_fine"
            else:
                coarse, fine = GENERIC_16BIT_COARSE, GENERIC_16BIT_FINE
            idx = combo.findData(coarse)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            if row + 1 >= self._ch_table.rowCount():
                QMessageBox.information(self, "16位电机通道", "双通道电机需要当前通道后面还有一个 Fine 通道。")
                self._update_color_cell(row)
                return
            next_combo = self._ch_table.cellWidget(row + 1, 1)
            if isinstance(next_combo, QComboBox):
                next_idx = next_combo.findData(fine)
                if next_idx < 0:
                    next_combo.addItem(_channel_label(fine), fine)
                    next_idx = next_combo.findData(fine)
                if next_idx >= 0:
                    next_combo.setCurrentIndex(next_idx)
                    self._update_color_cell(row + 1)
            self._update_color_cell(row)
            return
        if current_data == NEW_FUNCTION_CHANNEL:
            ch_type = self._add_function_channel()
            if not ch_type:
                combo.setCurrentIndex(combo.findData(ChannelType.PLACEHOLDER.value))
                self._update_color_cell(row)
                return
            idx = combo.findData(ch_type)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        self._update_color_cell(row)

    # ── 构建 UI ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.setContentsMargins(14, 14, 14, 14)

        # 基本信息
        info_group = QGroupBox("基本信息")
        form = QFormLayout(info_group)
        form.setSpacing(8)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("例如：LED Par 7ch")
        form.addRow("设备名称:", self._name_edit)
        # ★ 类型注册表选择（选后自动填充各字段）
        self._registry_combo = QComboBox()
        self._registry_combo.addItem("— 手动配置 —", "")
        for _dtype in DeviceTypeRegistry.list_all():
            label = f"{_dtype.name} ({len(_dtype.channels)}ch)"
            self._registry_combo.addItem(label, _dtype.id)
        self._registry_combo.currentIndexChanged.connect(
            self._on_registry_selected)
        form.addRow("从类型注册表选择:", self._registry_combo)
        self._ch_count_spin = QSpinBox()
        self._ch_count_spin.setRange(1, 512)
        self._ch_count_spin.valueChanged.connect(self._rebuild_table)
        form.addRow("通道数量:", self._ch_count_spin)
        lay.addWidget(info_group)

        # 通道定义表格
        ch_group = QGroupBox("通道类别定义（每行对应一个 DMX 通道）")
        ch_lay = QVBoxLayout(ch_group)
        ch_lay.setContentsMargins(6, 12, 6, 6)

        fn_row = QHBoxLayout()
        self._function_list = QListWidget()
        self._function_list.setMaximumHeight(88)
        self._function_list.itemDoubleClicked.connect(lambda _item: self._rename_function_channel())
        fn_row.addWidget(self._function_list, 1)
        fn_btn_col = QVBoxLayout()
        add_fn_btn = QPushButton("新增")
        add_fn_btn.setIcon(make_main_icon("add", 16))
        add_fn_btn.setToolTip("新增功能通道")
        add_fn_btn.clicked.connect(self._add_function_channel)
        rename_fn_btn = QPushButton("重命名")
        rename_fn_btn.clicked.connect(self._rename_function_channel)
        default_fn_btn = QPushButton("默认值")
        default_fn_btn.clicked.connect(self._set_function_channel_default)
        del_fn_btn = QPushButton("删除")
        del_fn_btn.clicked.connect(self._delete_function_channel)
        fn_btn_col.addWidget(add_fn_btn)
        fn_btn_col.addWidget(rename_fn_btn)
        fn_btn_col.addWidget(default_fn_btn)
        fn_btn_col.addWidget(del_fn_btn)
        fn_btn_col.addStretch()
        fn_row.addLayout(fn_btn_col)
        ch_lay.addLayout(fn_row)

        self._ch_table = QTableWidget(0, 3)
        self._ch_table.setHorizontalHeaderLabels(["通道", "类别", "颜色预览"])
        self._ch_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self._ch_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._ch_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self._ch_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._ch_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._ch_table.setAlternatingRowColors(True)
        # 原 alternate-background-color:#2d2d2d 行内覆盖已删：隔行色统一走全局 QSS token
        ch_lay.addWidget(self._ch_table)
        lay.addWidget(ch_group)

        # 按钮（§10.4：确定=primary 蓝、取消=cancel 语义标记）
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("保存设备")
        btns.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        cancel_b = btns.button(QDialogButtonBox.StandardButton.Cancel)
        cancel_b.setText("取消")
        cancel_b.setProperty("role", "cancel")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _load_data(self):
        self._name_edit.setText(self._fdef.name)
        self._refresh_function_list()
        self._ch_count_spin.setValue(self._fdef.channel_count)
        self._rebuild_table(self._fdef.channel_count)

    # ── 类型注册表操作 ─────────────────────────────────────────────────────
    def _on_registry_selected(self):
        """当用户从类型注册表下拉框中选择一项时触发。"""
        type_id = self._registry_combo.currentData()
        if type_id:
            self._apply_registry_type(type_id)

    def _apply_registry_type(self, type_id: str):
        """从注册表应用选定的设备类型模板，自动填充所有对话框字段。

        弹出确认后，会覆盖当前的名称、通道数和通道布局。
        功能通道的名称和默认值也会从模板同步更新。
        """
        if not type_id:
            return
        dtype = DeviceTypeRegistry.get(type_id)
        if not dtype:
            return

        self._name_edit.setText(dtype.name)

        # 替换功能通道列表为模板中的定义
        self._function_channels.clear()
        self._function_channel_names.clear()
        self._function_channel_defaults.clear()
        for ch in dtype.channels:
            if _is_function_channel(ch) and ch not in self._function_channels:
                self._function_channels.append(ch)
                self._function_channel_names[ch] = dtype.channel_names.get(
                    ch, _function_channel_label(ch))
                self._function_channel_defaults[ch] = dtype.channel_defaults.get(
                    ch, 0)
        self._function_channels.sort(key=_function_channel_number)
        self._refresh_function_list()

        # 更新 fdef 通道数组（_rebuild_table 会从此读取）
        self._fdef.channels = list(dtype.channels)

        # 触发表格重建（阻塞信号避免二次重建）
        self._ch_count_spin.blockSignals(True)
        self._ch_count_spin.setValue(len(dtype.channels))
        self._ch_count_spin.blockSignals(False)
        self._rebuild_table(len(dtype.channels))

    def _rebuild_table(self, count: int = None):
        if count is None:
            count = self._ch_count_spin.value()
        self._ch_table.setRowCount(count)
        for i in range(count):
            # 列0：通道编号
            num_item = QTableWidgetItem(f"CH {i + 1}")
            num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            num_item.setFlags(num_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._ch_table.setItem(i, 0, num_item)

            # 列1：类别下拉框（外观由全局 QSS 提供，不再行内重复）
            combo = QComboBox()
            current = self._fdef.channels[i] if i < len(self._fdef.channels) else ""
            if current == ChannelType.DIMMER.value:
                current = ChannelType.PLACEHOLDER.value
            self._fill_channel_combo(combo, current)
            if i < len(self._fdef.channels):
                current_saved = self._fdef.channels[i]
                if current_saved == ChannelType.DIMMER.value:
                    current_saved = ChannelType.PLACEHOLDER.value
                idx = combo.findData(current_saved)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            combo.currentIndexChanged.connect(
                lambda _, row=i, c=combo: self._on_channel_combo_changed(c, row))
            self._ch_table.setCellWidget(i, 1, combo)

            # 列2：颜色预览
            color_item = QTableWidgetItem("  ")
            color_item.setFlags(color_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._ch_table.setItem(i, 2, color_item)
            self._update_color_cell(i)

    def _update_color_cell(self, row: int):
        combo = self._ch_table.cellWidget(row, 1)
        color_item = self._ch_table.item(row, 2)
        if combo and color_item:
            ch_val = combo.currentData()
            color = _channel_color(ch_val)
            color_item.setBackground(QColor(color))
            color_item.setText(f"  {_channel_label(ch_val, self._function_channel_names)}  ")

    def _on_accept(self):
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "错误", "请输入设备名称。")
            return
        self._fdef.name = name
        self._fdef.channel_count = self._ch_count_spin.value()
        channels = []
        for i in range(self._fdef.channel_count):
            combo = self._ch_table.cellWidget(i, 1)
            ch_data = combo.currentData() if combo else ChannelType.PLACEHOLDER.value
            if ch_data == ChannelType.DIMMER.value:
                ch_data = ChannelType.PLACEHOLDER.value
            channels.append(ch_data)
        self._fdef.channels = channels
        self._fdef.function_channel_names = {
            ch: self._function_channel_names.get(ch, _function_channel_label(ch))
            for ch in self._function_channels
        }
        # 保留非功能通道的显式默认值（如特殊类型升降电机 motor_z=0=落底面），
        # 否则一编辑设备定义就抹掉导入得到的绝对默认值，运行时回退电机归中 128。
        preserved_defaults = {}
        for k, v in (getattr(self._fdef, "function_channel_defaults", {}) or {}).items():
            if _is_function_channel(str(k)):
                continue
            try:
                preserved_defaults[str(k)] = max(0, min(255, int(v)))
            except Exception:
                continue
        preserved_defaults.update({
            ch: int(self._function_channel_defaults.get(ch, 0))
            for ch in self._function_channels
        })
        self._fdef.function_channel_defaults = preserved_defaults
        self.accept()

    def get_definition(self) -> FixtureDefinition:
        return self._fdef


# ──────────────────────────────────────────────────────────────────────────────
#  配接网格单元格
# ──────────────────────────────────────────────────────────────────────────────
class _ColorDot(QFrame):
    """10px 类别色点（图例/徽标用，一次性少量实例，不涉网格红线）。"""

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self.setToolTip("")
        self.setStyleSheet(
            f"background:{color}; border:none; border-radius:3px;")


def _legend_label(ch_type: str) -> str:
    if ch_type == "function_*":
        return "功能通道"
    return _channel_label(ch_type)



class PatchCell(QWidget):
    """512 通道网格中的单个通道单元格。

    性能红线（§10.9 / 任务 A）：全网格禁止逐格 setStyleSheet——
    本格所有状态（空态/已配接/hover/选中）均在 paintEvent 内按成员
    变量直绘；assign/clear 只改字段并 update()，512 格构建与整网
    刷新零样式表解析，开销低于旧实现。
    """

    def __init__(self, channel: int, parent=None):
        super().__init__(parent)
        self.channel = channel
        self._assigned = False
        self._color: QColor | None = None   # CATEGORY_COLORS 派生色（仅视觉）
        self._lines = ""                    # 与旧版一致的 "通道号\n类别缩写"
        self._selected = False
        self._hover = False
        self._pressed = False   # §11.2 反馈补全：按下态直绘
        # ★ R183/F2 (P2)：地址冲突标记（由 patch_table_view 的冲突扫描注入；
        #   直绘红描边，仍不碰 setStyleSheet——性能红线不变）
        self._conflict = False
        self.setFixedSize(38, 38)
        self.setMouseTracking(True)
        # §9/§10：格子可点击高亮，手型光标
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("")

    # ── 状态注入 ──
    def assign(self, fixture_name: str, ch_type: str, ch_index: int,
               channel_names: dict[str, str] | None = None,
               channel_defaults: dict[str, int] | None = None):
        if _is_function_channel(ch_type):
            label = _channel_label(ch_type, channel_names)
            short = label[:3] if label else f"F{_function_channel_number(ch_type)}"
        else:
            short = ch_type[:3].upper()
        self._color = QColor(_channel_color(ch_type))
        self._lines = f"{self.channel}\n{short}"
        default_text = ""
        if _is_function_channel(ch_type) and channel_defaults is not None:
            default_text = f"\n默认值: {int(channel_defaults.get(ch_type, 0))}"
        self.setToolTip(
            f"设备: {fixture_name}\n"
            f"通道: CH{self.channel} (设备内第{ch_index + 1}通道)\n"
            f"类别: {_channel_label(ch_type, channel_names)}{default_text}")
        self._assigned = True
        self.update()

    def clear_assign(self):
        self._assigned = False
        self._color = None
        self._lines = ""
        self.setToolTip("")
        self.update()

    def set_selected(self, on: bool):
        on = bool(on)
        if self._selected != on:
            self._selected = on
            self.update()

    def set_conflict(self, on: bool):
        """地址冲突标记（R183/F2 P2）：纯视觉，只切需要重绘的那些格。"""
        on = bool(on)
        if self._conflict != on:
            self._conflict = on
            self.update()

    # ── 交互（纯视觉选中，不发业务信号）──
    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        if self._pressed:
            self._pressed = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        grid = self.parent()
        while grid is not None and not isinstance(grid, UniverseGrid):
            grid = grid.parentWidget()
        if isinstance(grid, UniverseGrid):
            grid._on_cell_clicked(self)
        self._pressed = True   # §11.2：pressed 三态反馈
        self.update()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(e)

    # ── 绘制 ──
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        if self._assigned and self._color is not None:
            base = QColor(self._color)
            # 明度对齐：按底色亮度选字色（纯视觉派生，不改数据色）
            lum = (0.299 * base.redF() + 0.587 * base.greenF()
                   + 0.114 * base.blueF())
            fg = QColor("#151515") if lum > 0.55 else QColor("#F2F2F2")
            border = QColor(255, 255, 255, 38)
        else:
            base = QColor(BG_DEEP)
            fg = QColor(TEXT_DISABLED)
            border = QColor(BORDER_NORMAL)
        p.setPen(QPen(border, 1))
        p.setBrush(QBrush(base))
        p.drawRoundedRect(r, 4, 4)
        # hover：白 40 叠加层（§10 任务 A）
        if self._hover:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(255, 255, 255, 40)))
            p.drawRoundedRect(r, 4, 4)
        # pressed：再叠白 55 提亮（§11.2 按下三态）
        if self._pressed:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(255, 255, 255, 55)))
            p.drawRoundedRect(r, 4, 4)
        # 冲突：bright_error 红描边（最外圈，选中环内缩半像素避免互相盖住）
        if self._conflict:
            p.setPen(QPen(QColor(BRIGHT_ERROR), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(-1.0, -1.0, 1.0, 1.0), 4, 4)
        # 选中：select_blue 描边 + select_blue 12% 底（叠加在原底色上）
        if self._selected:
            p.setPen(QPen(QColor(SELECT_BLUE), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(71, 114, 179, 31)))   # SELECT_BLUE ≈12%
            p.drawRoundedRect(r, 4, 4)
        # 文本：等宽通道号 + 类别缩写，居中两行（内容与旧版一致）
        lines = [s for s in self._lines.split("\n")] if self._lines \
            else [str(self.channel)]
        f = QFont()
        f.setFamilies(["Consolas", "DejaVu Sans Mono"])
        f.setPixelSize(10)
        f.setBold(self._assigned)
        p.setFont(f)
        line_h = 12
        y0 = self.height() / 2 - len(lines) * line_h / 2 + 1
        for i, line in enumerate(lines):
            rect = QRectF(1, y0 + i * line_h, self.width() - 2, line_h)
            p.setPen(QPen(fg))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, line)
        p.end()


# ──────────────────────────────────────────────────────────────────────────────
#  单宇宙配接网格（32列 × 16行 = 512格）
# ──────────────────────────────────────────────────────────────────────────────
class _UniverseCanvas(QWidget):
    """512 格画布容器（§11 病灶#11：设计性横向滚动登记）。

    32 列 ×38px 格阵的内容宽是配接网格的设计尺寸（同监视器 ChannelGrid
    先例，审计按 HSCROLL 设计性滚动处理）。minimumSizeHint 不向父级申报
    该大宽，避免被误判为 SQUEEZE；真实内容几何由本类固定尺寸给出，
    内部 QGridLayout 排布与单元交互完全不变。
    """
    GRID_W = 4 * 2 + 32 * 38 + 31 * 2     # = 1286（同旧 QGridLayout 最小宽）
    GRID_H = 4 * 2 + 16 * 38 + 15 * 2     # = 646（同旧最小高）

    def sizeHint(self) -> QSize:
        return QSize(self.GRID_W, self.GRID_H)

    def minimumSizeHint(self) -> QSize:
        return QSize(1, 1)


class UniverseGrid(QWidget):
    """单个 DMX 宇宙的 512 通道可视化网格"""

    def __init__(self, universe: int, parent=None):
        super().__init__(parent)
        self.universe = universe
        self._cells: dict[int, PatchCell] = {}
        self._built = False
        self._selected_cell: PatchCell | None = None   # 仅视觉高亮，不发信号
        # 512 单元格构造(每格 setStyleSheet,昂贵)延到该宇宙标签首次显示时,
        # 避免打开 Patch 面板时一次性建 N×512 格(N宇宙)造成数秒卡顿。
        self._pending: list = []   # [(start_ch, fdef, name)] 建成前暂存的配接

    def ensure_built(self):
        if self._built:
            return
        self._built = True
        self._build()
        # 补放建成前累积的配接
        for start_ch, fdef, name in self._pending:
            self._apply_now(start_ch, fdef, name)
        self._pending = []

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)   # §11.1 外缘 4px 贴边
        outer.setSpacing(6)

        title = QLabel(f"宇宙 {self.universe}  —  512 通道配接视图")
        title.setStyleSheet(
            f"color:{FOCUS_BLUE};font-weight:bold;font-size:13px;"
            f"padding:4px 0;border-bottom:1px solid {SEP_LINE};")   # 强调文字走焦点蓝
        outer.addWidget(title)

        # 滚动条/外框全部由全局 GLOBAL_QSS 提供（原局部 QScrollArea 行内样式已删）；
        # 视口底色=面板底（BG_ROOT），杜绝白底穿透（§11.1）。
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        _vp_pal = scroll.viewport().palette()
        _vp_pal.setColor(QPalette.ColorRole.Window, QColor(BG_ROOT))
        scroll.viewport().setPalette(_vp_pal)
        scroll.viewport().setAutoFillBackground(True)
        canvas = _UniverseCanvas()
        grid = QGridLayout(canvas)
        grid.setSpacing(2)
        grid.setContentsMargins(4, 4, 4, 4)

        # 构建 512 格期间关闭重绘/更新,抑制每次 addWidget 触发的中间 relayout+repaint。
        # 512 次逐格布局重算是主要开销,批量构建后一次性开启,单网格构建从 ~200ms 降至数十ms。
        canvas.setUpdatesEnabled(False)
        for ch in range(1, 513):
            row = (ch - 1) // 32
            col = (ch - 1) % 32
            cell = PatchCell(ch, canvas)
            self._cells[ch] = cell
            grid.addWidget(cell, row, col)
        canvas.setUpdatesEnabled(True)

        # 设计性内容尺寸：画布保持固有几何，横向/纵向滚动条随视口出现
        # （滚动条外观走全局主题；横滚为配接网格设计特性——审计登记 HSCROLL）。
        scroll.setWidgetResizable(False)
        canvas.resize(_UniverseCanvas.GRID_W, _UniverseCanvas.GRID_H)
        scroll.setWidget(canvas)
        outer.addWidget(scroll)

    def apply_fixture(self, start_ch: int, fdef: FixtureDefinition,
                      instance_name: str):
        if not self._built:
            self._pending.append((start_ch, fdef, instance_name))  # 延到建成再放
            return
        self._apply_now(start_ch, fdef, instance_name)

    # ── 单格选中（纯视觉：只切两格的重绘，不发业务信号）──
    def _on_cell_clicked(self, cell: PatchCell):
        prev = self._selected_cell
        if prev is cell:
            cell.set_selected(False)
            self._selected_cell = None
            return
        if prev is not None:
            prev.set_selected(False)
        cell.set_selected(True)
        self._selected_cell = cell

    def _apply_now(self, start_ch: int, fdef: FixtureDefinition,
                   instance_name: str):
        channel_names = getattr(fdef, "function_channel_names", {}) or {}
        channel_defaults = getattr(fdef, "function_channel_defaults", {}) or {}
        for i, ch_type in enumerate(fdef.channels):
            ch = start_ch + i
            if 1 <= ch <= 512:
                self._cells[ch].assign(
                    instance_name, ch_type, i, channel_names, channel_defaults)

    def set_conflict_channels(self, channels) -> int:
        """按通道号集合刷冲突标记（R183/F2 P2）：只动状态变化的格，返回标记数。

        `clear_all` 之前调用无意义（未建成），故与 `_pending` 同款守卫。
        """
        want = {int(c) for c in (channels or ())}
        if not self._built:
            return 0
        marked = 0
        for ch, cell in self._cells.items():
            on = ch in want
            if bool(getattr(cell, "_conflict", False)) != on:
                cell.set_conflict(on)
            marked += 1 if on else 0
        return marked

    def clear_all(self):
        if not self._built:
            self._pending = []
            return
        if self._selected_cell is not None:
            self._selected_cell.set_selected(False)
            self._selected_cell = None
        for cell in self._cells.values():
            cell.clear_assign()
            cell.set_conflict(False)   # R183/F2 P2：冲突标记随整网刷新一起复位


# ──────────────────────────────────────────────────────────────────────────────
#  PatchPanel 主面板
# ──────────────────────────────────────────────────────────────────────────────
class PatchPanel(QWidget):
    """Patch 面板主体"""
    fixtures_changed = pyqtSignal()

    def __init__(self, project, parent=None, project_manager=None):
        super().__init__(parent)
        self._project = project
        self._pm = project_manager
        self._universe_grids: dict[int, UniverseGrid] = {}
        self.setStyleSheet(STYLE)
        self._build_ui()
        # R183/F2：Patch 表格页（P1/P2/P3/P4）运行时注入——本行零逻辑，
        # `_build_ui` 基线已顶格（183 行），故不放里面。
        from .patch_table_view import attach_patch_table; attach_patch_table(self)
        from .patch_workspace_ui import install_workspace
        install_workspace(self)
        self._refresh_all()

    def _snapshot_for_undo(self):
        # ★★ 2026-09-20 修复（用户实报"导入一个文件只出现空的配接页面"）：
        #   原实现把 `from ...core.project_manager import DOMAIN_PATCH` 写在
        #   `__init__` 里 —— 两处都错：
        #     ① 相对层级越界：本模块是 `src.ui.patch_panel`，`..` 才到 `src`，
        #        `...` 已越过顶层包 → `ImportError: attempted relative import
        #        beyond top-level package` → **PatchPanel 构造失败** →
        #        懒加载把它记成 warning、`_patch_widget` 保持 None →
        #        用户导入 工程后看到的就是**空的配接页面**，无法继续。
        #     ② 作用域错位：名字导入在 `__init__` 的局部，而使用点在
        #        `_snapshot_for_undo` —— 即使路径写对也仍是 NameError。
        #   修：改到使用点按正确层级（`..core`）局部导入，与
        #   `programmer_panel` 的同款写法一致。
        from ..core.project_manager import DOMAIN_PATCH
        if self._pm is not None and hasattr(self._pm, "_snapshot"):
            try:
                self._pm._snapshot(domain=DOMAIN_PATCH)
            except Exception:
                pass

    # ── 构建 UI ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        main_lay = QHBoxLayout(self)
        main_lay.setContentsMargins(4, 4, 4, 4)   # §11.1 外缘常数：panelouter=4
        main_lay.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── 左侧：设备类型库 + 配接操作 ────────────────────────────────────────
        left = QWidget()
        # §11 病灶#11：左栏压缩至 220 内（§11.1 容器常数），按钮改 2×2 网格防粘连/防溢出
        left.setMinimumWidth(190)
        left.setMaximumWidth(220)
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(8)
        self._left_lay = left_lay  # 供导入配接表会话横幅插入

        # 导入配接表会话横幅（默认隐藏，导入流程第1步后显示，点击触发第2步）
        self._import_banner = None
        self._import_finish_cb = None
        self._import_def_ids = None

        lib_label = QLabel("设备类型库 (Fixture Library)")
        lib_label.setStyleSheet(
            f"font-weight:bold;font-size:13px;color:{FOCUS_BLUE};"
            f"padding:4px 0;border-bottom:1px solid {SEP_LINE};")
        left_lay.addWidget(lib_label)

        self._def_list = QListWidget()
        self._def_list.setMinimumHeight(160)
        self._def_list.setAlternatingRowColors(True)
        self._def_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._def_list.setToolTip("在此选择要配接的设备类型（双击可直接编辑通道）")
        self._def_list.setCursor(Qt.CursorShape.PointingHandCursor)  # §9：可点击元素手型光标
        self._def_list.itemDoubleClicked.connect(lambda _it: self._edit_fixture_def())
        left_lay.addWidget(self._def_list)

        # 空态（§10.6）：设备类型库为空时替换列表占位，禁止大片纯黑
        self._def_empty = EmptyState(
            "库中还没有设备类型",
            "点击「新建」/「模板」创建，或从 灯库导入", icon_key="package")
        self._def_empty.setVisible(False)
        left_lay.addWidget(self._def_empty)

        def_btn_grid = QGridLayout()   # 病灶#11：2×2 紧凑网格，左栏 ≤220 时四钮不挤出
        def_btn_grid.setSpacing(4)
        new_def_btn = QPushButton("新建")
        new_def_btn.setFixedHeight(24)   # §10 任务 A：工具行统一 24px
        new_def_btn.setIcon(make_main_icon("add", 16))
        new_def_btn.setToolTip("创建新设备类型")
        new_def_btn.clicked.connect(self._new_fixture_def)
        template_btn = QPushButton("模板")
        template_btn.setFixedHeight(24)
        template_btn.setIcon(make_main_icon("project_open", 16))
        template_btn.setToolTip("从预定义模板创建设备类型（HJC三维/LED灯组/气爆等）")
        template_btn.clicked.connect(self._new_from_template)
        edit_def_btn = QPushButton("编辑")
        edit_def_btn.setFixedHeight(24)
        edit_def_btn.setIcon(make_main_icon("edit", 16))
        edit_def_btn.setToolTip("编辑选中的设备类型")
        edit_def_btn.clicked.connect(self._edit_fixture_def)
        del_def_btn = QPushButton("删除")
        del_def_btn.setFixedHeight(24)
        del_def_btn.setIcon(make_main_icon("clear_output", 16))
        del_def_btn.setToolTip("删除选中的设备类型")
        del_def_btn.clicked.connect(self._del_fixture_def)
        for _i, _b in enumerate((new_def_btn, template_btn,
                                 edit_def_btn, del_def_btn)):
            _sp = _b.sizePolicy()
            _sp.setHorizontalPolicy(QSizePolicy.Policy.MinimumExpanding)
            _b.setSizePolicy(_sp)
            def_btn_grid.addWidget(_b, _i // 2, _i % 2)
        left_lay.addLayout(def_btn_grid)

        ofl_btn = QPushButton("导入")
        ofl_btn.setFixedHeight(24)
        ofl_btn.setIcon(make_main_icon("import", 16))
        ofl_btn.setToolTip("从 Open Fixture Library 导入设备类型定义（50+ 内置灯库）")
        ofl_btn.clicked.connect(self._import_from_ofl)
        left_lay.addWidget(ofl_btn)

        # 通道类别图例（§10 任务 A：prop_row 式排版——色点 10px + 类别名 11px）
        self._legend_section = self._build_legend()
        left_lay.addWidget(self._legend_section)

        # 配接参数
        inst_group = QGroupBox("配接参数")
        inst_lay = QFormLayout(inst_group)
        inst_lay.setContentsMargins(8, 14, 8, 8)
        # §11 病灶#11 防粘连：标签-控件水平间距 4px（§10 prop_row 常数），行距 6 保持≥4
        inst_lay.setHorizontalSpacing(4)
        inst_lay.setVerticalSpacing(6)
        # 局部特化：数值选择（宇宙/起始通道等）行高统一 22px（§10 任务 A）
        inst_group.setStyleSheet(f"QSpinBox{{min-height:22px; padding:1px 6px;}}")

        self._inst_count_spin = QSpinBox()
        self._inst_count_spin.setRange(1, 512)
        self._inst_count_spin.setValue(1)
        self._inst_count_spin.setToolTip("要配接的设备数量")
        inst_lay.addRow("设备数量:", self._inst_count_spin)

        self._inst_universe_spin = QSpinBox()
        self._inst_universe_spin.setRange(1, 32767)  # ★ Art-Net 最大域编号
        self._inst_universe_spin.setValue(1)
        self._inst_universe_spin.setToolTip("目标 DMX 宇宙编号（1-32767）")
        inst_lay.addRow("目标宇宙:", self._inst_universe_spin)

        self._inst_start_ch_spin = QSpinBox()
        self._inst_start_ch_spin.setRange(1, 512)
        self._inst_start_ch_spin.setValue(1)
        self._inst_start_ch_spin.setToolTip("第一台设备的起始通道（1-512）")
        inst_lay.addRow("起始通道:", self._inst_start_ch_spin)

        patch_btn = QPushButton("执行配接")
        patch_btn.setIcon(make_main_icon("play", 16))
        patch_btn.setStyleSheet(   # 局部特化：主操作按钮＝选中蓝填充（全局按钮面为灰阶）
            f"QPushButton{{background:{SELECT_BLUE};border:1px solid {SELECT_BLUE};"
            f"color:{TEXT_SELECTED_FG};padding:7px 14px;border-radius:4px;font-weight:bold;}}"
            f"QPushButton:hover{{border-color:{FOCUS_BLUE};}}")
        patch_btn.setToolTip("将选中的设备类型配接到网格")
        patch_btn.clicked.connect(self._do_patch)
        inst_lay.addRow(patch_btn)

        clear_btn = QPushButton("清空配接…")
        clear_btn.setIcon(make_main_icon("clear_output", 16))
        clear_btn.setStyleSheet(   # 局部特化：危险操作＝哑光错误红底+亮字（§11.1）
            f"QPushButton{{background:rgba(153,22,22,90);border:1px solid rgba(227,104,85,120);"
            f"color:{BRIGHT_ERROR};padding:5px 14px;border-radius:4px;}}"
            f"QPushButton:hover{{background:rgba(153,22,22,130);border-color:{BRIGHT_ERROR};}}")
        clear_btn.clicked.connect(self._on_clear_all)
        inst_lay.addRow(clear_btn)

        excel_row = QHBoxLayout()
        excel_row.setSpacing(4)
        export_patch_btn = QPushButton("导出配接表")
        export_patch_btn.setFixedHeight(24)
        export_patch_btn.setToolTip("导出 Patch 配接关系为 Office 可编辑的 .xlsx 表格")
        export_patch_btn.clicked.connect(self._export_patch_excel)
        import_patch_btn = QPushButton("导入配接表")
        import_patch_btn.setFixedHeight(24)
        import_patch_btn.setToolTip("从 Office .xlsx 表格导入 Patch 配接关系")
        import_patch_btn.clicked.connect(self._import_patch_excel)
        excel_row.addWidget(export_patch_btn)
        excel_row.addWidget(import_patch_btn)
        inst_lay.addRow(excel_row)

        left_lay.addWidget(inst_group)
        left_lay.addStretch()
        splitter.addWidget(left)

        # ── 右侧：配接网格 ──────────────────────────────────────────────────────
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(4)

        grid_header = QHBoxLayout()
        grid_label = QLabel("配接网格（各宇宙 512 通道可视化）")
        grid_label.setStyleSheet(
            f"font-weight:bold;font-size:13px;color:{FOCUS_BLUE};"
            f"padding:4px 0;border-bottom:1px solid {SEP_LINE};")
        grid_header.addWidget(grid_label)
        add_uni_btn = QPushButton("添加宇宙")
        add_uni_btn.setFixedHeight(24)   # 工具行 24px 统一
        add_uni_btn.setIcon(make_main_icon("add", 16))
        add_uni_btn.setToolTip("手动添加一个宇宙的配接网格")
        add_uni_btn.clicked.connect(self._add_universe_grid_dialog)
        grid_header.addWidget(add_uni_btn, alignment=Qt.AlignmentFlag.AlignRight)
        right_lay.addLayout(grid_header)

        self._grid_tabs = QTabWidget()
        self._grid_tabs.setTabsClosable(False)
        self._grid_tabs.setDocumentMode(True)
        right_lay.addWidget(self._grid_tabs)

        splitter.addWidget(right)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(5)
        # 原 QSplitter::handle 行内样式已删：分隔条外观由全局 GLOBAL_QSS 统一提供
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 950])   # 左栏上限同步 ≤220（§11 病灶#11）
        main_lay.addWidget(splitter)

    # ── 通道类别图例（prop_row 式：左色点右类别名，逐项手型提示）──
    def _build_legend(self) -> PanelSection:
        sec = PanelSection("通道类别图例", expanded=True)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        entries = [
            (key, col) for key, col in CHANNEL_COLORS.items()
            if not key.endswith("_fine")
        ]
        entries.append(("function_*", "#607D8B"))   # 功能通道并轨特例色
        per_col = 2
        for idx, (key, color) in enumerate(entries):
            row, col = divmod(idx, per_col)
            item = QWidget()
            h = QHBoxLayout(item)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)   # §10 任务 C：徽标点与文字 6px 间距
            dot = _ColorDot(color)
            lab = QLabel(_legend_label(key))
            lab.setStyleSheet(
                f"color:{TEXT_SECONDARY}; font-size:11px; background:transparent;")
            h.addWidget(dot)
            h.addWidget(lab, 1)
            item.setToolTip(f"通道类别「{_legend_label(key)}」在配接网格中的颜色")
            grid.addWidget(item, row, col)
        while grid.count() % per_col:
            grid.addItem(QSpacerItem(1, 1), grid.count() // per_col,
                         grid.count() % per_col)
        sec.add_layout(grid)
        return sec

    # ── 刷新逻辑 ───────────────────────────────────────────────────────────────
    def _refresh_all(self):
        self._refresh_def_list()
        self._refresh_grids()

    def _refresh_def_list(self):
        self._def_list.clear()
        for fdef in self._project.fixture_defs:
            ch_summary = self._make_ch_summary(fdef)
            item = QListWidgetItem(f"{fdef.name}  [{fdef.channel_count}ch]")
            item.setToolTip(f"通道构成: {ch_summary}")
            item.setData(Qt.ItemDataRole.UserRole, fdef.id)
            self._def_list.addItem(item)
        # 空态切换（§10.6）：列表/占位互斥显示，不改任何数据流
        has_items = self._def_list.count() > 0
        self._def_list.setVisible(has_items)
        self._def_empty.setVisible(not has_items)

    def _make_ch_summary(self, fdef: FixtureDefinition) -> str:
        from collections import Counter
        c = Counter(fdef.channels)
        names = getattr(fdef, "function_channel_names", {}) or {}
        return "  ".join(f"{_channel_label(k, names)}×{v}" for k, v in c.items())

    def _refresh_grids(self):
        used_universes = sorted(set(fi.universe for fi in self._project.fixtures))
        if not used_universes:
            used_universes = [1]
        for u in used_universes:
            self._ensure_universe_grid(u)
        for grid in self._universe_grids.values():
            grid.clear_all()
        for fi in self._project.fixtures:
            fdef = self._project.get_fixture_def(fi.def_id)
            if fdef and fi.universe in self._universe_grids:
                self._universe_grids[fi.universe].apply_fixture(
                    fi.start_channel, fdef, fi.name)
        # 只构建当前可见标签的网格(其余延到切到时再建),避免一次性建 N×512 格卡顿
        self._build_current_grid()
        # 其余宇宙网格在空闲时后台逐个预建,使首次切标签也瞬时(不阻塞打开)。
        self._schedule_background_grid_build()

    def _build_current_grid(self):
        idx = self._grid_tabs.currentIndex()
        w = self._grid_tabs.widget(idx) if idx >= 0 else None
        if isinstance(w, UniverseGrid):
            w.ensure_built()

    def _schedule_background_grid_build(self):
        """空闲时逐个预建未构建的宇宙网格(每次 singleShot 建一个,不阻塞 UI)。

        链仅在面板可见时推进: 面板隐藏(如导入完成回到 TopView/Programmer 工作)时
        链在下一次 _build_next 处自动终止, 不再每个 processEvents 周期建 512 格 +
        setStyleSheet 拖慢全局 UI; 面板再次显示时由 showEvent 恢复预建, 内容不变。
        """
        from PyQt6.QtCore import QTimer

        # 代际计数:每次调度让上一条未完成的链作废,避免多次导入后多链并存空扫。
        gen = getattr(self, "_grid_build_gen", 0) + 1
        self._grid_build_gen = gen

        def _build_next():
            if getattr(self, "_grid_build_gen", 0) != gen:
                return  # 已被新一次调度取代
            if not self.isVisible():
                return  # 面板隐藏: 终止后台链, 恢复显示时由 showEvent 重启
            for grid in list(self._universe_grids.values()):
                if not getattr(grid, "_built", True):
                    grid.ensure_built()
                    QTimer.singleShot(0, _build_next)  # 让出事件循环,再建下一个
                    return
        QTimer.singleShot(0, _build_next)

    def showEvent(self, event):
        """面板显示时恢复未完成的后台网格预建(构建内容与隐藏前完全一致)。"""
        super().showEvent(event)
        if self._universe_grids:
            self._schedule_background_grid_build()

    def _ensure_universe_grid(self, universe: int):
        if universe not in self._universe_grids:
            grid = UniverseGrid(universe)
            self._universe_grids[universe] = grid
            self._grid_tabs.addTab(grid, f"宇宙 {universe}")
            if not getattr(self, "_grid_tab_signal_connected", False):
                self._grid_tabs.currentChanged.connect(
                    lambda _i: self._build_current_grid())
                self._grid_tab_signal_connected = True

    # ── 设备类型操作 ───────────────────────────────────────────────────────────
    def _new_fixture_def(self):
        dlg = FixtureDefDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._snapshot_for_undo()
            self._project.fixture_defs.append(dlg.get_definition())
            self._refresh_def_list()
            if hasattr(self, "_pm") and self._pm:
                self._pm.project_changed.emit()

    def _new_from_template(self):
        """从 DeviceTypeRegistry 预定义模板创建设备类型。"""
        templates = DeviceTypeRegistry.list_all()
        if not templates:
            QMessageBox.information(self, "模板库", "无可用模板。")
            return

        # 模板选择对话框（§10.4：内容边距 12；确定=primary / 取消=cancel）
        dlg = QDialog(self)
        dlg.setWindowTitle("选择设备模板")
        dlg.setMinimumWidth(450)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        label = QLabel("选择一个预定义设备模板，快速创建配接定义：")
        lay.addWidget(label)

        template_list = QListWidget()
        template_list.setAlternatingRowColors(True)
        for tmpl in templates:
            cat_label = {"motor": "电机", "light": "灯光", "pump": "水泵", "valve": "阀门", "composite": "复合"}.get(tmpl.category, tmpl.category)
            item_text = f"{tmpl.name}  [{cat_label}, {len(tmpl.channels)}ch]"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, tmpl.id)
            item.setToolTip(tmpl.description)
            template_list.addItem(item)
        template_list.setCurrentRow(0)
        lay.addWidget(template_list)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setProperty("role", "cancel")
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        lay.addWidget(btn_box)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        sel_item = template_list.currentItem()
        if not sel_item:
            return
        tmpl_id = sel_item.data(Qt.ItemDataRole.UserRole)
        tmpl = DeviceTypeRegistry.get(tmpl_id)
        if not tmpl:
            return

        # 转换为 FixtureDefinition
        fdef = FixtureDefinition(
            id=str(uuid.uuid4()),
            name=tmpl.name,
            channel_count=len(tmpl.channels),
            channels=[],
        )
        # 审计 module-29 P1: 旧代码把 dict 塞进 channels(List[str]) 致
        # FixtureDefDialog findData 恒 -1, 每个通道下拉落到首项 R, 保存即全 R
        # 错误定义。修复: channels 存枚举 .value 字符串, 默认值进
        # function_channel_defaults(与 _apply_registry_type 同口径)。
        for i, ch_type_str in enumerate(tmpl.channels):
            ch_type = getattr(ChannelType, ch_type_str.upper(), ChannelType.PLACEHOLDER)
            default_val = tmpl.channel_defaults.get(ch_type_str, 0)
            fdef.channels.append(ch_type.value)
            if default_val:
                fdef.function_channel_defaults[ch_type.value] = int(default_val)

        # 打开编辑对话框让用户确认/修改
        edit_dlg = FixtureDefDialog(fdef, parent=self)
        if edit_dlg.exec() == QDialog.DialogCode.Accepted:
            self._snapshot_for_undo()
            self._project.fixture_defs.append(edit_dlg.get_definition())
            self._refresh_def_list()
            if hasattr(self, "_pm") and self._pm:
                self._pm.project_changed.emit()


    def _edit_fixture_def(self):
        item = self._def_list.currentItem()
        if not item:
            QMessageBox.information(self, "提示", "请先在左侧列表中选择一个设备类型。")
            return
        def_id = item.data(Qt.ItemDataRole.UserRole)
        self._edit_fixture_def_by_id(def_id)

    def _edit_fixture_def_by_id(self, def_id) -> bool:
        """按 def_id 编辑设备类型通道。返回 True=用户确认保存，False=取消/无效。

        与 _edit_fixture_def 共用：后者取列表选中项后委托本方法。导入会话中
        open_placeholder_editors 亦调用本方法，逐个主动唤起占位类型的编辑框。
        """
        fdef = self._project.get_fixture_def(def_id)
        if not fdef:
            return False
        dlg = FixtureDefDialog(copy.deepcopy(fdef), parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._snapshot_for_undo()
            edited = dlg.get_definition()
            try:
                for attr, value in vars(edited).items():
                    setattr(fdef, attr, value)
            except Exception:
                self._project.fixture_defs = [
                    edited if fd.id == def_id else fd
                    for fd in self._project.fixture_defs
                ]
            # 导入会话期间：把刚填好的配置传播给「名称相似 + 通道数相同」的同族未填类型，
            # 免去重复设置（气爆泵填好 → 跑泉泵自动继承）。仅填全占位者，不覆盖已填。
            # 用「先规划→用户确认→再应用」避免单字 CJK 后缀误判(摇头灯/染色灯)静默污染；
            # 范围限本次导入的设备类型(_import_def_ids)，不波及历史遗留 def。
            if getattr(self, "_import_finish_cb", None) is not None:
                try:
                    from .import_patch_csv import plan_family_channels, apply_family_plan
                    ids = getattr(self, "_import_def_ids", None)
                    scope = [d for d in self._project.fixture_defs
                             if (not ids) or getattr(d, "id", None) in ids]
                    plan = plan_family_channels(scope)
                    if plan:
                        pairs = "、".join(
                            f"{getattr(scope[t],'name','?')}（配置来自{getattr(scope[s],'name','?')}）"
                            for t, s in plan[:10]) + ("…" if len(plan) > 10 else "")
                        r = QMessageBox.question(
                            self, "套用同族通道配置？",
                            f"检测到名称相似且通道数相同的设备类型，可把已填通道配置套用过去，"
                            f"省去重复设置：\n{pairs}\n\n是否套用？(只填写尚未配置的类型，不覆盖已填)",
                            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                        if r == QMessageBox.StandardButton.Yes:
                            apply_family_plan(scope, plan)
                except Exception:
                    pass
            self._refresh_def_list()
            self._refresh_grids()
            self.fixtures_changed.emit()
            if hasattr(self, "_pm") and self._pm:
                self._pm.project_changed.emit()
            if hasattr(self._project, "project_changed"):
                self._project.project_changed.emit()
            return True
        return False

    def open_placeholder_editors(self, def_ids) -> None:
        """导入后主动逐个唤起「通道仍全为占位」的设备类型编辑框。

        解决「系统提示补全通道类型、却不主动弹编辑框」的问题：用户点某类型确认后，
        同族传播可能已顺带填好后续类型，故每轮都重新判定占位状态、跳过已填者。
        用户在某个框点取消即停止队列（其余可事后用「编辑」按钮/双击补），不强塞一长串。
        """
        for did in list(def_ids or []):
            fd = self._project.get_fixture_def(did)
            chs = list(getattr(fd, "channels", []) or []) if fd else []
            if not (chs and all(c == "placeholder" for c in chs)):
                continue  # 已填 或 无通道 → 跳过
            if not self._edit_fixture_def_by_id(did):
                break  # 用户取消 → 停止逐个唤起

    def _del_fixture_def(self):
        item = self._def_list.currentItem()
        if not item:
            return
        def_id = item.data(Qt.ItemDataRole.UserRole)
        fdef = self._project.get_fixture_def(def_id)
        if not fdef:
            return
        ret = QMessageBox.question(
            self, "删除确认",
            f"确认删除设备类型 [{fdef.name}]？\n"
            f"这将同时移除所有已配接的该类型实例。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret == QMessageBox.StandardButton.Yes:
            self._snapshot_for_undo()
            self._project.fixture_defs = [
                fd for fd in self._project.fixture_defs if fd.id != def_id]
            self._project.fixtures = [
                fi for fi in self._project.fixtures if fi.def_id != def_id]
            self._refresh_all()
            self.fixtures_changed.emit()
            if hasattr(self, "_pm") and self._pm:
                self._pm.project_changed.emit()

    def _import_from_ofl(self):
        """Open Importer dialog and import selected fixture definitions."""
        try:
            from .ofl_importer import 通用ImporterDialog
        except ImportError as exc:
            QMessageBox.critical(
                self, "Import Error",
                f"Cannot load importer: {exc}\n\n"
                "Make sure 'requests' is installed.")
            return
        try:
            old_count = len(self._project.fixture_defs)
            result = 通用ImporterDialog.import_to_project(self._project, self)
            if result:
                imported = len(self._project.fixture_defs) - old_count
                if imported > 0:
                    self._snapshot_for_undo()
                    self._refresh_def_list()
                    self.fixtures_changed.emit()
                    if hasattr(self, "_pm") and self._pm:
                        self._pm.project_changed.emit()
                    if hasattr(self._project, "project_changed"):
                        self._project.project_changed.emit()
                    QMessageBox.information(
                        self, "Import Complete",
                        f"Successfully imported {imported} fixture "
                        f"definition{'s' if imported > 1 else ''}.")
        except Exception as exc:
            logger = __import__("logging").getLogger(__name__)
            logger.exception("import failed")
            QMessageBox.critical(
                self, "Import Error",
                f"Failed to import fixtures:\n{exc}")

    # ── 配接操作 ───────────────────────────────────────────────────────────────
    def _do_patch(self):
        """R183/F2：本体搬到 `patch_actions.do_patch`（P2 冲突确认使其超基线）。

        写入语义零变化：仍是"先移除重叠旧设备、再写入新设备"，仅在移除前把
        将被覆盖的设备清单弹给用户确认（不再静默丢配接）。
        """
        from .patch_actions import do_patch
        return do_patch(self)

    def _on_clear_all(self):
        fixtures = list(getattr(self._project, "fixtures", []) or [])
        if not fixtures:
            QMessageBox.information(self, "清空配接", "当前没有已配接的设备实例。")
            return
        universes = sorted(set(int(getattr(fi, "universe", 1) or 1) for fi in fixtures))
        options = [("全部域", None)]
        for u in universes:
            count = sum(1 for fi in fixtures if int(getattr(fi, "universe", 1) or 1) == u)
            options.append((f"宇宙 {u}（{count} 台）", u))
        labels = [label for label, _u in options]
        current_u = int(self._inst_universe_spin.value()) if hasattr(self, "_inst_universe_spin") else 1
        default_idx = next((i for i, (_label, u) in enumerate(options) if u == current_u), 0)
        choice, ok = QInputDialog.getItem(
            self, "清空配接", "选择要清空的域/宇宙:", labels,
            default_idx, False)
        if not ok:
            return
        selected = dict(options).get(choice)
        target_text = "所有已配接的设备实例" if selected is None else f"宇宙 {selected} 的已配接设备实例"
        ret = QMessageBox.question(
            self, "清空确认", f"确认清空{target_text}？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret == QMessageBox.StandardButton.Yes:
            if selected is None:
                removed = len(fixtures)
                new_fixtures = []
            else:
                new_fixtures = [
                    fi for fi in fixtures
                    if int(getattr(fi, "universe", 1) or 1) != int(selected)
                ]
                removed = len(fixtures) - len(new_fixtures)
            if removed <= 0:
                return
            self._snapshot_for_undo()
            self._project.fixtures = new_fixtures
            self._refresh_grids()
            self.fixtures_changed.emit()
            if self._pm:
                self._pm.project_changed.emit()
            if hasattr(self._project, "project_changed"):
                self._project.project_changed.emit()
            QMessageBox.information(self, "清空完成", f"已清空 {removed} 台设备实例。")

    def _add_universe_grid_dialog(self):
        u, ok = QInputDialog.getInt(
            self, "添加宇宙网格", "请输入宇宙编号 (1-32767):", 1, 1, 32767)  # ★ 支持任意域
        if ok:
            self._ensure_universe_grid(u)
            self._grid_tabs.setCurrentIndex(
                self._grid_tabs.count() - 1)

    def refresh(self):
        """外部调用刷新"""
        self._refresh_all()

    # ── 导入配接表会话横幅 ────────────────────────────────────────────────────
    def begin_import_session(self, on_finish, def_ids=None):
        """显示顶部"完成配接"横幅，提示用户补全各设备类型的通道含义后点击。

        on_finish: 用户点击"完成配接"时调用的回调（无参）。
        def_ids: 本次导入涉及的设备类型 id 集合（用于把同族通道传播限定在本次导入范围内，
                 不波及工程中历史遗留的同名/同后缀 def）。
        """
        self._import_def_ids = set(def_ids) if def_ids else None
        self._import_finish_cb = on_finish
        if self._import_banner is None:
            bar = QFrame()
            bar.setStyleSheet(   # 局部特化：导入会话横幅＝信息蓝语义容器
                f"QFrame{{background:{INFO_BLUE};border:1px solid {FOCUS_BLUE};"
                "border-radius:4px;}")
            lay = QHBoxLayout(bar)
            lay.setContentsMargins(8, 6, 8, 6)
            lay.setSpacing(8)
            lab = QLabel("配接导入中：请为每个设备类型补全各通道类别，然后点击右侧「完成配接」。")
            lab.setWordWrap(True)
            lab.setStyleSheet(
                f"color:{TEXT_PRIMARY};font-weight:bold;border:none;background:transparent;")
            btn = QPushButton("完成配接")
            btn.setIcon(make_main_icon("play", 16))
            btn.setStyleSheet(   # §11.1 大红大绿净化：确认动作＝低饱和成功绿哑光底 + bright_success 亮字
                f"QPushButton{{background:rgba(24,134,37,70);color:{BRIGHT_SUCCESS};font-weight:bold;"
                "padding:6px 14px;border-radius:4px;border:1px solid rgba(86,182,91,110);}"
                f"QPushButton:hover{{background:rgba(24,134,37,110);"
                f"border-color:{BRIGHT_SUCCESS};}}")
            btn.clicked.connect(self._on_import_finish_clicked)
            lay.addWidget(lab, 1)
            lay.addWidget(btn, 0)
            self._import_banner = bar
            if hasattr(self, "_left_lay") and self._left_lay is not None:
                self._left_lay.insertWidget(0, bar)
        self._import_banner.show()

    def _on_import_finish_clicked(self):
        cb = self._import_finish_cb
        if callable(cb):
            cb()

    def end_import_session(self):
        """隐藏"完成配接"横幅，结束导入会话。"""
        if self._import_banner is not None:
            self._import_banner.hide()
        self._import_finish_cb = None
        self._import_def_ids = None


    def _ensure_openpyxl(self):
        """R183/F2 (P7)：探测 openpyxl；缺失时**不再弹错误框**。

        旧实现在缺库时弹「缺少 Excel 支持」并返回 None → 两个按钮变成纯报错
        （真机 venv 未装 openpyxl = 功能隐形不可用）。现在返回 None 只表示
        "走 csv 降级路径"（见 `patch_table_io`），调用方据此换后缀与提示。
        """
        from .table_io_fallback import openpyxl_pack
        return openpyxl_pack()

    def _export_patch_excel(self):
        """R183/F2 (P7)：本体在 `patch_table_io.export_patch_table`（xlsx/csv 双通道）。"""
        from .patch_table_io import export_patch_table
        return export_patch_table(self)

    def _import_patch_excel(self):
        """R183/F2 (P2/P7)：本体在 `patch_table_io.import_patch_table`（含冲突确认）。"""
        from .patch_table_io import import_patch_table
        return import_patch_table(self)
