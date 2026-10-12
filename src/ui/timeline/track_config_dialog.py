"""
TrackConfigDialog — 新建/编辑轨道弹窗
★ v2：每个域（Universe）拥有独立的通道过滤范围配置
"""
from __future__ import annotations
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QSpinBox,
    QPushButton, QColorDialog, QWidget, QFrame, QScrollArea, QSizePolicy
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from ...core.models import Track, FilterRule
from ..monitor.filter_numpad import FilterNumpad

# D 波任务 A/B：浮窗解剖标准件
from ..widget_kit import (PanelSection, apply_dialog_chrome, prop_row,
                           scope_qss)
from ..qascade_theme import BG_BOX, BG_PANEL, BORDER_NORMAL
from ..main_icon_library import load_tabler_icon


def _kit_title_row(title: str, on_close) -> QWidget:
    """D 波任务 A：浮窗标准标题行——24px 高，标题 12px 左、✕ 22px 右。"""
    from ..qascade_theme import TEXT_PRIMARY as _TXT, TEXT_SECONDARY as _TXT_DIM, \
        BG_BUTTON_HOVER as _BTN_HOVER
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


TRACK_COLORS = [
    "#4A90D9", "#E74C3C", "#2ECC71", "#F39C12",
    "#9B59B6", "#1ABC9C", "#E67E22", "#3498DB",
]
# ↑ §4.B 用户资产色板（写入 Track.color），禁止换肤触碰。

# 2026-08-27 D 波：原 _DARK_STYLE 已并入 widget_kit.apply_dialog_chrome +
# 全局主题；仅保留 QScrollArea 局部特化（视口透出对话框底色，防原生白底）。
_SCROLL_STYLE = """
    QScrollArea { border: none; background: transparent; }
    QScrollArea > QWidget > QWidget { background: transparent; }
"""


class ColorSwatch(QFrame):
    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self.color = color
        self.setFixedSize(24, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_color()

    def _apply_color(self):
        self.setStyleSheet(
            f"background: {self.color}; border: 2px solid transparent; border-radius: 3px;")

    def set_selected(self, sel: bool):
        if sel:
            self.setStyleSheet(
                f"background: {self.color}; border: 2px solid #FFFFFF; border-radius: 3px;")
        else:
            self._apply_color()

    def mousePressEvent(self, e):
        self.parent()._select_color(self.color)


class UniverseFilterRow(QWidget):
    """
    单行域配置：Universe 编号 + 展开/折叠的独立通道过滤小键盘 + 删除按钮
    """
    def __init__(self, universe: int, filter_rule: FilterRule = None,
                 removable: bool = True, parent=None):
        super().__init__(parent)
        self._filter_rule = filter_rule or FilterRule()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── 顶行：Universe 编号 + 展开按钮 + 删除按钮 ──────────────────────
        header = QWidget()
        scope_qss(header, f"background:{BG_PANEL};border-radius:4px;")  # bg_panel 行头
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(6, 4, 6, 4)
        hlay.setSpacing(6)

        hlay.addWidget(QLabel("Universe:"))
        self.spin = QSpinBox()
        self.spin.setRange(1, 32767)
        self.spin.setValue(universe)
        self.spin.setFixedWidth(90)
        # 内联外观已删（§3，走全局 QSpinBox）
        hlay.addWidget(self.spin)

        # 过滤范围预览标签
        self._filter_label = QLabel(self._make_label())
        self._filter_label.setStyleSheet("color:#71A8FF;font-size:11px;")
        hlay.addWidget(self._filter_label, stretch=1)

        # 展开/折叠按钮
        self._expand_btn = QPushButton("通道过滤")
        self._expand_btn.setCheckable(True)
        self._expand_btn.setChecked(False)
        self._expand_btn.setFixedHeight(22)
        self._expand_btn.setCursor(Qt.CursorShape.PointingHandCursor)  # §9
        self._expand_btn.setStyleSheet(
            "QPushButton{background:#545454;border:1px solid #3D3D3D;color:#CCCCCC;"
            "padding:2px 8px;border-radius:3px;font-size:11px;}"
            "QPushButton:hover{background:#656565;}"
            "QPushButton:checked{background:#4772B3;color:#FFFFFF;border-color:#4772B3;}")
        self._expand_btn.toggled.connect(self._toggle_filter)
        hlay.addWidget(self._expand_btn)

        self.del_btn = QPushButton(load_tabler_icon("x", 14), "")
        self.del_btn.setFixedSize(22, 22)
        self.del_btn.setToolTip("移除该 Universe 行")
        self.del_btn.setCursor(Qt.CursorShape.PointingHandCursor)  # §9
        self.del_btn.setStyleSheet(
            "QPushButton{background:rgba(153,22,22,100);border:none;color:#E36855;"
            "border-radius:4px;font-weight:bold;}"
            "QPushButton:hover{background:rgba(153,22,22,170);}")
        self.del_btn.setVisible(removable)
        hlay.addWidget(self.del_btn)
        outer.addWidget(header)

        # ── 折叠区：通道过滤小键盘 ─────────────────────────────────────────
        self._filter_panel = QWidget()
        scope_qss(self._filter_panel, f"background:{BG_BOX};border:1px solid {BORDER_NORMAL};"
                              f"border-radius:4px;")  # bg_box
        fp_lay = QVBoxLayout(self._filter_panel)
        fp_lay.setContentsMargins(8, 6, 8, 6)
        fp_lay.setSpacing(4)

        tip = QLabel("输入通道范围（如：1 THRU 50 ENTER），空白 = 全通道")
        tip.setStyleSheet("color:#8B8B8B;font-size:10px;")
        fp_lay.addWidget(tip)

        self._numpad = FilterNumpad()
        if self._filter_rule.channels:
            self._numpad.set_channels_from_selection(self._filter_rule.channels)
        self._numpad.rule_changed.connect(self._on_channels_changed)
        fp_lay.addWidget(self._numpad)

        self._filter_panel.setVisible(False)
        outer.addWidget(self._filter_panel)

    def _make_label(self) -> str:
        expr = self._filter_rule.to_expression()
        return f"[{expr}]" if expr else "[全通道]"

    def _toggle_filter(self, checked: bool):
        self._filter_panel.setVisible(checked)
        self._expand_btn.setText("收起通道过滤" if checked else "通道过滤")

    def _on_channels_changed(self):
        channels = self._numpad.get_channels()
        self._filter_rule = FilterRule(channels=channels)
        self._filter_label.setText(self._make_label())

    def value(self) -> int:
        return self.spin.value()

    def get_filter_rule(self) -> FilterRule:
        channels = self._numpad.get_channels()
        return FilterRule(channels=channels)


class TrackConfigDialog(QDialog):
    """新建/编辑轨道弹窗 — 每域独立通道过滤"""
    def __init__(self, track: Track = None, parent=None):
        super().__init__(parent)
        self._is_edit = track is not None
        self._track = track or Track()
        self._selected_color = self._track.color
        self._color_swatches = []
        self._universe_rows: list[UniverseFilterRow] = []
        self.setWindowTitle("编辑轨道" if self._is_edit else "新建轨道")
        self.setMinimumWidth(560)
        self.setMinimumHeight(500)
        self._setup_ui()
        apply_dialog_chrome(self)   # D 波任务 A：统一浮窗外框

    def _setup_ui(self):
        vlay = QVBoxLayout(self)
        vlay.setSpacing(8)
        vlay.setContentsMargins(12, 12, 12, 12)   # 任务 A：内容边距 12
        vlay.addWidget(_kit_title_row(
            "编辑轨道" if self._is_edit else "新建轨道", self.reject))

        # ── 基本信息（任务 B：PanelSection + prop_row）──────────────────────
        sec_basic = PanelSection("基本信息")
        self._name_edit = QLineEdit(self._track.name)
        sec_basic.add_widget(prop_row("轨道名称", self._name_edit))
        vlay.addWidget(sec_basic)

        # ── 多域配置区（每域独立过滤）──────────────────────────────────────
        sec_uni = PanelSection("接收/发送域 (Universe) — 每域可独立设置通道过滤范围")
        uni_outer = QVBoxLayout()
        uni_outer.setSpacing(6)

        # 滚动区域（域数量多时可滚动）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(350)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(_SCROLL_STYLE)   # 局部特化：全局未覆盖
        self._uni_container = QWidget()
        self._uni_layout = QVBoxLayout(self._uni_container)
        self._uni_layout.setContentsMargins(0, 0, 0, 0)
        self._uni_layout.setSpacing(6)
        self._uni_layout.addStretch()
        scroll.setWidget(self._uni_container)
        uni_outer.addWidget(scroll)

        # 加载现有域配置
        existing = list(self._track.universes) if self._track.universes else [1]
        for i, u in enumerate(existing):
            existing_rule = self._track.universe_filters.get(u, self._track.filter_rule)
            self._add_universe_row(u, filter_rule=existing_rule, removable=(i > 0))

        add_uni_btn = QPushButton("＋ 添加域")
        add_uni_btn.setToolTip("新增一行 Universe 过滤配置")
        add_uni_btn.setCursor(Qt.CursorShape.PointingHandCursor)  # §9
        add_uni_btn.setFixedHeight(24)   # 工具行统一 24px
        add_uni_btn.setStyleSheet(
            "QPushButton{background:#4772B3;border:1px solid #4772B3;color:#FFFFFF;"
            "padding:2px 14px;border-radius:4px;}"   # select_blue 主操作
            "QPushButton:hover{background:#5480C5;}"
            "QPushButton:pressed{background:#35507C;}")
        add_uni_btn.clicked.connect(lambda: self._add_universe_row(1, removable=True))
        uni_outer.addWidget(add_uni_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        body_uni = QWidget()
        body_uni.setLayout(uni_outer)
        sec_uni.add_widget(body_uni)
        vlay.addWidget(sec_uni)

        # ── 颜色选择 ────────────────────────────────────────────────────────
        sec_color = PanelSection("轨道颜色")
        color_lay = QHBoxLayout()
        color_lay.setContentsMargins(0, 0, 0, 0)
        color_lay.setSpacing(8)
        for c in TRACK_COLORS:
            sw = ColorSwatch(c, self)
            sw.set_selected(c == self._selected_color)
            sw.setToolTip(f"轨道颜色 {c}")
            self._color_swatches.append(sw)
            color_lay.addWidget(sw)
        color_lay.addStretch()
        custom_btn = QPushButton("自定义...")
        custom_btn.setCursor(Qt.CursorShape.PointingHandCursor)  # §9
        # 内联外观已删（§3，走全局 QPushButton）
        custom_btn.clicked.connect(self._pick_custom_color)
        color_lay.addWidget(custom_btn)
        body_color = QWidget()
        body_color.setLayout(color_lay)
        sec_color.add_widget(body_color)
        vlay.addWidget(sec_color)

        # ── 按钮 ────────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.setProperty("role", "cancel")   # 任务 A：取消=cancel
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)  # §9
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("确定")
        ok_btn.setObjectName("primary")             # 任务 A：确定=primary 蓝底
        ok_btn.setDefault(True)
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)  # §9
        ok_btn.clicked.connect(self._ok)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(ok_btn)
        vlay.addLayout(btn_row)

    def _add_universe_row(self, value: int = 1, filter_rule: FilterRule = None,
                          removable: bool = True):
        row = UniverseFilterRow(value, filter_rule=filter_rule,
                                removable=removable, parent=self)
        row.del_btn.clicked.connect(lambda: self._remove_universe_row(row))
        self._universe_rows.append(row)
        # 插入到 stretch 之前
        idx = self._uni_layout.count() - 1
        self._uni_layout.insertWidget(idx, row)

    def _remove_universe_row(self, row: UniverseFilterRow):
        if len(self._universe_rows) <= 1:
            return
        self._universe_rows.remove(row)
        row.setParent(None)

    def _select_color(self, color: str):
        self._selected_color = color
        for sw in self._color_swatches:
            sw.set_selected(sw.color == color)

    def _pick_custom_color(self):
        c = QColorDialog.getColor(QColor(self._selected_color), self)
        if c.isValid():
            self._select_color(c.name())

    def _ok(self):
        self._track.name = self._name_edit.text().strip() or "Track"
        universes = list(dict.fromkeys(r.value() for r in self._universe_rows))
        self._track.universes = universes
        self._track.color = self._selected_color

        # ★ 保存每域独立过滤规则
        self._track.universe_filters = {}
        for row in self._universe_rows:
            u = row.value()
            rule = row.get_filter_rule()
            self._track.universe_filters[u] = rule

        # 兼容旧版：filter_rule 使用第一个域的规则
        if self._universe_rows:
            self._track.filter_rule = self._universe_rows[0].get_filter_rule()

        self.accept()

    def get_track(self) -> Track:
        return self._track
