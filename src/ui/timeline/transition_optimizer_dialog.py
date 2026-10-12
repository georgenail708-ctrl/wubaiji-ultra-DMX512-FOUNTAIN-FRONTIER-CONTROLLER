"""过渡优化器预览对话框 — 让用户在应用前确认每对 Clip 的过渡策略"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from src.ui.main_icon_library import load_tabler_icon, make_main_icon
from src.ui.widget_kit import (  # D 波任务 A/B：浮窗解剖标准件
    PanelSection, apply_dialog_chrome, prop_row,
)
from src.ui.qascade_theme import TEXT_SECONDARY as _TXT_DIM


#: 历史 5 曲线（引擎闭式公式；名字与顺序冻结——旧存档字段按名回读）
LEGACY_CURVES = ("Linear", "EaseIn", "EaseOut", "EaseInOut", "Bezier")


def transition_curve_choices() -> list:
    """过渡曲线下拉词表（R16）：5 历史名 + 缓动库全名（去 Linear 重复）。

    缓动库词表来自 `engine.route_animator.easing.easing_names()`——与油门
    斜坡下拉同一份，两处缓动语言统一（深查③ R16）。
    """
    out = list(LEGACY_CURVES)
    try:
        from src.engine.route_animator.easing import easing_names
        for name in easing_names():
            if name not in out:
                out.append(name)
    except Exception:
        pass
    return out


def _kit_title_row(title: str, on_close) -> QWidget:
    """D 波任务 A：浮窗标准标题行——24px 高，标题 12px 左、✕ 22px 右。"""
    from src.ui.qascade_theme import (
        BG_BUTTON_HOVER as _BTN_HOVER,
        TEXT_PRIMARY as _TXT,
    )
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)
    t = QLabel(title)
    t.setStyleSheet(f"color:{_TXT}; font-size:12px; font-weight:bold;"
                    f"background:transparent;")
    h.addWidget(t)
    h.addStretch(1)
    x = QPushButton(load_tabler_icon("x", 14), "")
    x.setFixedSize(22, 22)
    x.setCursor(Qt.CursorShape.PointingHandCursor)
    x.setToolTip("关闭")
    x.setStyleSheet(
        f"QPushButton{{background:transparent;border:none;border-radius:3px;"
        f"color:{_TXT_DIM};font-size:11px;padding:0px;}}"
        f"QPushButton:hover{{background:{_BTN_HOVER};color:{_TXT};}}")
    x.clicked.connect(on_close)
    h.addWidget(x)
    row.setFixedHeight(24)
    return row


# 8种关水策略（对应既有填表系统的transition_mode）
_SHUTDOWN_STRATEGIES = [
    ("standard_fade", "标准淡入淡出", "渐变过渡，适用于灯光和平滑电机动作"),
    ("immediate", "立即切换", "无过渡，直接跳转（硬切）"),
    ("decelerate", "减速停止", "逐渐降速至零，模拟惯性"),
    ("hold_decay", "保持衰减", "保持当前值一段时间后衰减"),
    ("step_down", "阶梯下降", "分步降低强度，模拟量级切换"),
    ("smooth_ease", "平滑缓出", "S曲线缓出，更自然的结束"),
    ("delay_then_cut", "延迟硬切", "延迟后立即关闭，用于定时效果"),
]

# 5种运行模式
_RUN_MODES = [
    ("normal", "正常", "正向播放一次"),
    ("reverse", "反向", "反向播放"),
    ("pingpong", "往返", "正向播放后反向返回"),
    ("loop", "循环", "循环播放"),
    ("once", "单次", "播放一次后停止"),
]


class TransitionOptimizerDialog(QDialog):
    """过渡策略预览与编辑对话框 — 完整版"""

    def __init__(self, clip_pairs: list, parent=None, *, cross_track_default: bool = False):
        """
        Args:
            clip_pairs: list of (clip_a, clip_b, inferred_types_a, inferred_types_b, default_fade_ms)
        """
        super().__init__(parent)
        self.setWindowTitle("优化过渡方式")
        self.setWindowIcon(make_main_icon("transition", 20))
        self.setMinimumSize(960, 620)
        self.resize(1080, 680)
        self._clip_pairs = clip_pairs
        self._cross_track_default = bool(cross_track_default)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)   # 任务 A：内容边距 12
        layout.setSpacing(8)
        self._build_header(layout)
        self._build_table(layout)
        self._build_global_group(layout)
        self._build_buttons(layout)
        apply_dialog_chrome(self)   # D 波任务 A：统一浮窗外框

    def _build_header(self, layout: QVBoxLayout):
        # D 波任务 A：标准浮窗标题行（含 22px ✕）
        layout.addWidget(_kit_title_row("检查关联 Clip 的过渡设置", self.reject))
        summary = QLabel(
            f"共 {len(self._clip_pairs)} 对关联 Clip。可逐行调整淡入、淡出与策略，"
            "全局运行模式和曲线会应用到本次确认的目标 Clip。"
        )
        summary.setWordWrap(True)
        summary.setProperty("hint", True)   # 浮窗 QSS 弱化说明样式
        layout.addWidget(summary)

    def _build_table(self, layout: QVBoxLayout):
        # Clip 对列表表格
        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels([
            "Clip A", "Clip B", "推断类型", "淡出(ms)", "淡入(ms)", "策略"
        ])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(34)
        for i, pair in enumerate(self._clip_pairs):
            self._populate_row(i, pair)
        layout.addWidget(self._table)

    @staticmethod
    def _clip_display_name(clip) -> str:
        label = getattr(clip, "label", "") or ""
        return label if label.strip() else f"Clip {clip.id[:8]}"

    def _populate_row(self, i: int, pair):
        ca, cb, types_a, types_b, fade_ms = pair
        self._table.insertRow(i)

        item_a = QTableWidgetItem(self._clip_display_name(ca))
        item_a.setFlags(item_a.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(i, 0, item_a)

        item_b = QTableWidgetItem(self._clip_display_name(cb))
        item_b.setFlags(item_b.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(i, 1, item_b)

        # 推断类型
        types_str = f"{','.join(types_a)} → {','.join(types_b)}"
        item_types = QTableWidgetItem(types_str)
        item_types.setFlags(item_types.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(i, 2, item_types)

        # 淡出/淡入时长（可编辑）
        fade_out_ms = fade_ms if not isinstance(fade_ms, tuple) else fade_ms[0]
        self._table.setItem(i, 3, QTableWidgetItem(f"{int(fade_out_ms * 1000)}"))
        fade_in_ms = fade_ms if not isinstance(fade_ms, tuple) else fade_ms[1]
        self._table.setItem(i, 4, QTableWidgetItem(f"{int(fade_in_ms * 1000)}"))

        # 策略（下拉框 — 完整版8种关水策略）
        strategy = QComboBox()
        for strategy_id, strategy_name, strategy_desc in _SHUTDOWN_STRATEGIES:
            strategy.addItem(strategy_name, strategy_id)
            strategy.setItemData(
                strategy.count() - 1,
                strategy_desc,
                Qt.ItemDataRole.ToolTipRole,
            )
        strategy.setCurrentIndex(0)
        strategy.setToolTip(_SHUTDOWN_STRATEGIES[0][2])
        strategy.currentIndexChanged.connect(
            lambda index, combo=strategy: combo.setToolTip(
                combo.itemData(index, Qt.ItemDataRole.ToolTipRole) or ""
            )
        )
        self._table.setCellWidget(i, 5, strategy)

    def _build_global_group(self, layout: QVBoxLayout):
        # 全局设置（完整版：8种关水策略+5种RUN_MODE）；任务 B：PanelSection + prop_row
        sec_global = PanelSection("本次应用设置")

        # 默认运行模式
        self._run_mode_combo = QComboBox()
        for mode_id, mode_name, mode_desc in _RUN_MODES:
            self._run_mode_combo.addItem(mode_name, mode_id)
            self._run_mode_combo.setItemData(
                self._run_mode_combo.count() - 1, mode_desc,
                Qt.ItemDataRole.ToolTipRole)
        self._run_mode_combo.setToolTip("Clip播放模式：正常/反向/往返/循环/单次")
        sec_global.add_widget(prop_row("Clip 运行模式", self._run_mode_combo))

        # 跨track配对选项
        self._cross_track_check = QCheckBox("启用跨track配对")
        self._cross_track_check.setToolTip("允许不同track上的相邻clip优化过渡（默认仅同track）")
        self._cross_track_check.setChecked(self._cross_track_default)
        sec_global.add_widget(self._cross_track_check)

        # Curve profile选择（R16：5 旧名 + QEasingCurve 同名缓动库全名）
        self._curve_combo = QComboBox()
        self._curve_combo.addItems(transition_curve_choices())
        self._curve_combo.setToolTip(
            "淡入淡出曲线类型：前 5 项为历史曲线，其余为缓动库预设"
            "（Linear/InOutCubic/OutBounce…，与油门斜坡同一套语义）")
        sec_global.add_widget(prop_row("过渡曲线", self._curve_combo))

        layout.addWidget(sec_global)

    def _build_buttons(self, layout: QVBoxLayout):
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        apply_button = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        cancel_button = btn_box.button(QDialogButtonBox.StandardButton.Cancel)
        apply_button.setText("应用过渡设置")
        apply_button.setObjectName("primary")     # 任务 A：确定=primary 蓝底
        apply_button.setIcon(make_main_icon("transition", 16))
        cancel_button.setText("取消")
        cancel_button.setProperty("role", "cancel")   # 任务 A：取消=cancel
        cancel_button.setIcon(make_main_icon("clear_output", 16))
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def get_adjusted_pairs(self) -> list:
        """返回用户调整后的 (clip_a, clip_b, fade_out_sec, fade_in_sec, strategy_id, run_mode, curve) 列表"""
        result = []
        for i in range(len(self._clip_pairs)):
            ca, cb, _, _, _ = self._clip_pairs[i]
            try:
                fade_out_ms = float(self._table.item(i, 3).text())
                fade_in_ms = float(self._table.item(i, 4).text())
            except (ValueError, AttributeError):
                fade_out_ms = fade_in_ms = 200.0

            strategy_widget = self._table.cellWidget(i, 5)
            strategy_id = strategy_widget.currentData() if isinstance(strategy_widget, QComboBox) else "standard_fade"

            run_mode = self._run_mode_combo.currentData()
            curve_profile = self._curve_combo.currentText()

            result.append((ca, cb, fade_out_ms / 1000.0, fade_in_ms / 1000.0, strategy_id, run_mode, curve_profile))
        return result

    def get_global_settings(self) -> dict:
        """返回全局设置"""
        return {
            "cross_track_enabled": self._cross_track_check.isChecked(),
            "default_run_mode": self._run_mode_combo.currentData(),
            "default_curve": self._curve_combo.currentText(),
        }

def resolve_adjusted_pairs(dlg, cross_track_pairs, cross_track_only) -> tuple:
    """Resolve the accepted dialog scope before snapshotting or writing clips."""
    # 第4步：用户确认后，应用调整
    adjusted_pairs = dlg.get_adjusted_pairs()
    global_settings = dlg.get_global_settings()

    cross_track_enabled = bool(global_settings.get("cross_track_enabled"))
    if cross_track_only and not cross_track_enabled:
        adjusted_pairs = []
    elif cross_track_enabled and not cross_track_only:
        default_run_mode = global_settings.get("default_run_mode", "normal")
        default_curve = global_settings.get("default_curve", "Linear")
        default_strategy = adjusted_pairs[0][4] if adjusted_pairs else "standard_fade"
        existing = {(id(row[0]), id(row[1])) for row in adjusted_pairs}
        for ca, cb, _types_a, _types_b, duration in cross_track_pairs:
            pair_key = (id(ca), id(cb))
            if pair_key in existing:
                continue
            adjusted_pairs.append(
                (
                    ca,
                    cb,
                    duration,
                    duration,
                    default_strategy,
                    default_run_mode,
                    default_curve,
                )
            )
            existing.add(pair_key)

    return adjusted_pairs, global_settings
