"""
Qascade V1.0 — Signal Tester Widget（对接后端多模式版）

所有动态/静态模式均通过 SignalTester 后端发送，
UI 只负责：
  - 模式选择 ComboBox（静态 / 动态 以 tabler 图标区分）
  - 动态参数控件（SliderSpinBox）
  - 通道测试（保留原有功能）
  - 512 通道热图
  - 状态日志

Universe 编号规范：
  UI 层统一使用 1-based（最小值 1，默认值 1）。
  协议转换由 NetworkManager 统一处理，此层无需关心。
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSpinBox, QLineEdit, QScrollArea, QFrame,
    QTextEdit, QSizePolicy,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QFont

from ...network.signal_tester import SignalTester

# S1 令牌化：配色统一走中心主题令牌
# 注意：DmxHeatmap._heat 的黑到红 jet 梯度为值域编码（audit 高风险点），保持不动。
from ..qascade_theme import (
    BG_ROOT, BG_INPUT, SELECT_BLUE, FOCUS_BLUE, POPUP_BORDER,
    BORDER_NORMAL, SEP_LINE, RADIUS, MIN_CONTROL_HEIGHT,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_DISABLED, TEXT_SELECTED_FG,
    STATUS_WARNING_INK as BRIGHT_WARN, STATUS_SUCCESS_INK as BRIGHT_SUCCESS,
    ERROR_RED as BRIGHT_ERROR, INFO_BLUE as BRIGHT_INFO,
)
# D波标准件
from ..widget_kit import PanelSection, prop_row, NumSlider
from ..main_icon_library import load_tabler_icon
from .network_button_factory import make_button

# ─────────────────────────────────────────────────────────────────────────────
# 样式常量（变量名保留，值全部重定向到 主题令牌）
# ─────────────────────────────────────────────────────────────────────────────
DARK_BG = BG_ROOT        # #1D1D1D（原 #1e1e1e）
ACCENT  = FOCUS_BLUE     # #71A8FF 强调/高亮文字（原 #4a90d9）
GREEN   = BRIGHT_SUCCESS # #56B65B 发送=运行语义（原 #6aaa7a）
AMBER   = BRIGHT_WARN    # #E6B455 金=输入值惯例（原 #f0c060）
RED     = BRIGHT_ERROR   # #E36855 全灭=危险语义（原 #ff6666）
BLUE_LT = BRIGHT_INFO    # #6FA8DC 信息动作（原 #a0d4ff）

# 语义填充 rgba 分量：
_G = (24, 134, 37)       # SUCCESS_GREEN #188625
_R = (153, 22, 22)       # ERROR_RED #991616
_I = (40, 72, 125)       # INFO_BLUE #28487D


def _btn(bg, fg, hover, border=None, fs=11, h=28):
    """按钮四态：通用按压/选中走 select_blue+白字（与全局一致）；未传 border 取 bg 同色。"""
    b = border or bg
    return (
        f"QPushButton{{background:{bg};color:{fg};border:1px solid {b};"
        f"border-radius:{RADIUS}px;font-size:{fs}px;padding:0 14px;"
        f"min-height:{max(h, MIN_CONTROL_HEIGHT)}px;}}"
        f"QPushButton:hover{{background:{hover};border-color:#5C5C5C;}}"
        f"QPushButton:pressed{{background:{SELECT_BLUE};"
        f"color:{TEXT_SELECTED_FG};}}"
        f"QPushButton:checked{{background:{SELECT_BLUE};color:{TEXT_SELECTED_FG};"
        f"border:1px solid {SELECT_BLUE};font-weight:bold;}}"
    )


# D波：旧 _slider/_spinbox/_grp 局部样式工厂已删除——
# 通用控件外观统一走 qascade_theme.GLOBAL_QSS，参数滑杆换 NumSlider 标准件。

# 测试参数后缀（仅装饰性单位，数值语义与旧 QSlider/QDoubleSpinBox 完全一致）：
#   speed 用 x（倍率）、duration 用 s（秒）；其余整数/比例参数不加后缀。
_PARAM_SUFFIX = {"speed": "x", "duration": "s"}


# ─────────────────────────────────────────────────────────────────────────────
# 512 通道热图
# ─────────────────────────────────────────────────────────────────────────────
class DmxHeatmap(QWidget):
    channel_hovered = pyqtSignal(int, int)
    COLS, ROWS = 32, 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buf = bytearray(512)
        self._hover = -1
        self.setMouseTracking(True)
        self.setMinimumHeight(72)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def update_buffer(self, buf):
        self._buf = bytearray(buf[:512])
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        # D波 chrome：底色填充，让 1px 格间距呈现为均匀细缝（纯视觉）
        p.fillRect(self.rect(), QColor(BG_ROOT))
        w, h = self.width(), self.height()
        cw, ch = w / self.COLS, h / self.ROWS
        for i in range(512):
            row, col = divmod(i, self.COLS)
            x, y = int(col * cw), int(row * ch)
            bw = max(1, int(cw) - 1)
            bh = max(1, int(ch) - 1)
            color = self._heat(self._buf[i])
            if i == self._hover:
                color = color.lighter(180)
            p.fillRect(x, y, bw, bh, color)
        p.end()

    @staticmethod
    def _heat(v: int) -> QColor:
        t = v / 255.0
        if   t < 0.2: r,g,b = 0, 0, int(255*t/0.2)
        elif t < 0.4: r,g,b = 0, int(255*(t-0.2)/0.2), 255
        elif t < 0.6: r,g,b = 0, 255, int(255*(1-(t-0.4)/0.2))
        elif t < 0.8: r,g,b = int(255*(t-0.6)/0.2), 255, 0
        else:         r,g,b = 255, int(255*(1-(t-0.8)/0.2)), 0
        return QColor(r, g, b)

    def mouseMoveEvent(self, e):
        cw, ch = self.width()/self.COLS, self.height()/self.ROWS
        col, row = int(e.pos().x()/cw), int(e.pos().y()/ch)
        idx = row * self.COLS + col
        if 0 <= idx < 512:
            self._hover = idx
            self.channel_hovered.emit(idx, self._buf[idx])
        self.update()

    def leaveEvent(self, _):
        self._hover = -1
        self.update()


# ─────────────────────────────────────────────────────────────────────────────
# 模式定义表（纯数据，不含逻辑）
# ─────────────────────────────────────────────────────────────────────────────

# (display_name, backend_mode_key, is_dynamic, description)
# R181：display_name 不再带形状字形前缀——静态/动态由下拉项 tabler 图标区分
# （square / player-play）。
PATTERN_LIST = [
    ("全亮 Full",          "full",        False, "所有通道设为指定亮度"),
    ("全灭 Zero",          "zero",        False, "所有通道清零"),
    ("斜坡 Ramp",          "ramp",        False, "通道值从 0 线性升至亮度值"),
    ("棋盘格 Checkerboard","checkerboard",False, "奇偶通道交替满/零"),
    ("单通道 Single",      "single",      False, "只点亮指定单一通道"),
    ("跑灯 Chase",         "chase",       True,  "单点（可调宽度）依次扫过通道"),
    ("乒乓 Ping-Pong",     "pingpong",    True,  "单点在通道间来回弹射"),
    ("呼吸 Fade",          "fade",        True,  "全通道正弦呼吸，可调上下限"),
    ("彩虹 Rainbow",       "rainbow",     True,  "正弦亮度波在通道间滚动"),
    ("随机闪烁 Strobe",    "strobe",      True,  "随机通道随机时刻闪烁"),
    ("分组跑灯 Group",     "groupchase",  True,  "按组宽分块，各组依次全亮"),
    ("拉链 Zipper",        "zipper",      True,  "奇偶通道交替按速度翻转"),
    ("雨滴 Raindrop",      "raindrop",    True,  "随机通道随机亮度随机持续"),
    ("脉冲波 Pulse",       "pulse",       True,  "正弦脉冲从通道 0 向 511 展开"),
    ("楼梯 Staircase",     "staircase",   True,  "分段阶梯亮度依次滚动"),
]

# 每个模式的参数定义：mode_key 映射 [(param_key, label, lo, hi, default, step, type), ...]
PARAM_DEFS: dict[str, list[tuple]] = {
    "full":         [("level",       "亮度",    0,   255,  255,  1,    int)],
    "checkerboard": [("level",       "亮度",    0,   255,  255,  1,    int)],
    "single":       [("channel",     "通道",    0,   511,    0,  1,    int),
                     ("level",       "亮度",    0,   255,  255,  1,    int)],
    "chase":        [("speed",       "速度",    0.1, 20.0,  2.0, 0.1, float),
                     ("level",       "亮度",    0,   255,  255,  1,    int),
                     ("width",       "点宽",    1,    32,    1,  1,    int),
                     ("ch_count",    "范围",    1,   512,  512,  1,    int)],
    "pingpong":     [("speed",       "速度",    0.1, 20.0,  1.5, 0.1, float),
                     ("level",       "亮度",    0,   255,  255,  1,    int),
                     ("width",       "点宽",    1,    32,    2,  1,    int),
                     ("ch_count",    "范围",    1,   512,  512,  1,    int)],
    "fade":         [("speed",       "速度",    0.1,  5.0,  0.5, 0.1, float),
                     ("lo",          "低值",    0,   255,    0,  1,    int),
                     ("hi",          "高值",    0,   255,  255,  1,    int)],
    "rainbow":      [("speed",       "速度",    0.1, 10.0,  1.0, 0.1, float),
                     ("level",       "亮度",    0,   255,  255,  1,    int),
                     ("freq",        "频率",    0.5, 16.0,  4.0, 0.5, float),
                     ("ch_count",    "范围",    1,   512,  512,  1,    int)],
    "strobe":       [("speed",       "速度",    0.1, 10.0,  1.0, 0.1, float),
                     ("density",     "密度",    0.01, 0.5, 0.05, 0.01,float),
                     ("level",       "亮度",    0,   255,  255,  1,    int),
                     ("duration",    "时长s",   0.01, 0.5, 0.05, 0.01,float)],
    "groupchase":   [("speed",       "速度",    0.1, 20.0,  1.5, 0.1, float),
                     ("level",       "亮度",    0,   255,  255,  1,    int),
                     ("group_width", "组宽",    1,    64,    8,  1,    int),
                     ("ch_count",    "范围",    1,   512,  512,  1,    int)],
    "zipper":       [("speed",       "速度",    0.1, 20.0,  2.0, 0.1, float),
                     ("level",       "亮度",    0,   255,  255,  1,    int)],
    "raindrop":     [("speed",       "速度",    0.1, 10.0,  1.0, 0.1, float),
                     ("density",     "密度",    0.01, 1.0,  0.3, 0.01,float),
                     ("level",       "亮度",    0,   255,  255,  1,    int)],
    "pulse":        [("speed",       "速度",    0.1, 10.0,  1.0, 0.1, float),
                     ("level",       "亮度",    0,   255,  255,  1,    int),
                     ("freq",        "频率",    0.5,  8.0,  2.0, 0.5, float),
                     ("ch_count",    "范围",    1,   512,  512,  1,    int)],
    "staircase":    [("speed",       "速度",    0.1, 10.0,  1.0, 0.1, float),
                     ("level",       "亮度",    0,   255,  255,  1,    int),
                     ("steps",       "阶数",    2,    32,    8,  1,    int),
                     ("ch_count",    "范围",    1,   512,  512,  1,    int)],
}


# ─────────────────────────────────────────────────────────────────────────────
# 参数面板
# ─────────────────────────────────────────────────────────────────────────────
class _ParamPanel(QWidget):
    params_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._widgets: dict[str, QWidget] = {}
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(4)

    def load(self, mode_key: str):
        # 清空
        while self._lay.count():
            item = self._lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())
        self._widgets.clear()

        defs = PARAM_DEFS.get(mode_key, [])
        if not defs:
            lbl = QLabel("（此模式无可调参数）")
            lbl.setStyleSheet(f"color:{TEXT_DISABLED};font-size:11px;")
            self._lay.addWidget(lbl)
            self.params_changed.emit({})
            return

        # D波：参数行 = prop_row + NumSlider（值绘在滑槽上，浮点/后缀按 key 装饰）
        for row, (key, label, lo, hi, default, step, typ) in enumerate(defs):
            suffix = _PARAM_SUFFIX.get(key, "")
            decimals = 0 if typ is int else 2
            sl = NumSlider(float(lo), float(hi), float(default),
                           decimals=decimals, suffix=suffix)
            sl.setToolTip(f"{label}：拖动调整 / 右键输入精确数值")
            sl.valueChanged.connect(self._emit)
            self._lay.addWidget(prop_row(label, sl))
            self._widgets[key] = sl

        self._emit()

    def get(self) -> dict:
        out = {}
        for k, w in self._widgets.items():
            # D波：NumSlider 用 float_value()（与旧 value() 数值同源）
            out[k] = w.float_value()
        return out

    def _emit(self, *_):
        self.params_changed.emit(self.get())

    @staticmethod
    def _clear_layout(lay):
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()


# ─────────────────────────────────────────────────────────────────────────────
# 主 Widget
# ─────────────────────────────────────────────────────────────────────────────
class SignalTesterWidget(QWidget):
    """
    信号测试面板。

    Universe 显示和传递均为 1-based（与 sACN 一致）。
    协议层转换（Art-Net -1）由 NetworkManager.send_raw 统一处理。
    """

    def __init__(self, network_manager, parent=None):
        super().__init__(parent)
        self._nm     = network_manager
        self._tester = SignalTester(network_manager, self)
        self._tester.test_status.connect(self._log)
        self._params: dict = {}
        self._current_mode = "full"
        self._is_dynamic = False

        self._setup_ui()

        # 热图刷新定时器（~15fps 轮询）
        self._hm_timer = QTimer(self)
        self._hm_timer.setInterval(66)
        self._hm_timer.timeout.connect(self._refresh_heatmap)
        self._hm_timer.start()

    def showEvent(self, ev):  # noqa: N802
        super().showEvent(ev)
        if not self._hm_timer.isActive():
            self._hm_timer.start()

    def hideEvent(self, ev):  # noqa: N802
        self._hm_timer.stop()
        super().hideEvent(ev)

    # ── UI 构建 ───────────────────────────────────────────────────────────────

    def _setup_ui(self):
        """根布局 + QScrollArea 滚动容器 + 五个分区构建器。

        R182：补 QScrollArea（此前内容直接铺在根布局上，矮窗口/小屏下
        「通道测试」「状态日志」被挤出可视区且无法滚动）。滚动视口内保持
        R181 的五个分区与日志行顺序不变。
        """
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # S1 重构：裸 background 通配改为类限定选择器（audit-B：防击穿子控件继承）
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"SignalTesterWidget{{background:{DARK_BG};color:{TEXT_PRIMARY};}}")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea{{background:{DARK_BG};border:none;}}")
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        self._build_mode_section(root)
        self._build_param_section(root)
        self._build_control_section(root)
        self._build_heatmap_section(root)
        self._build_channel_section(root)

        # ── 状态日志 ──────────────────────────────────────────────────────────
        self._status = QTextEdit()
        self._status.setReadOnly(True)
        self._status.setMaximumHeight(100)
        self._status.setStyleSheet(
            f"QTextEdit{{background:{BG_INPUT};color:{TEXT_SECONDARY};"
            f"border:1px solid {SEP_LINE};"
            f"border-radius:{RADIUS}px;font-family:Consolas;font-size:11px;"
            f"padding:4px;}}")
        root.addWidget(self._status)

        # 输出统计行（D波：22px 详情行，数值走等宽）
        self._stat_lbl = QLabel("输出: — ")
        self._stat_lbl.setFixedHeight(22)
        self._stat_lbl.setStyleSheet(
            f"color:{TEXT_SECONDARY};font-size:11px;"
            "font-family:'Consolas','DejaVu Sans Mono',monospace;padding:0 4px;")
        root.addWidget(self._stat_lbl)
        root.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)

        # 触发一次初始化
        self._mode_combo.setCurrentIndex(0)
        self._on_mode_changed(0)

    def _build_mode_section(self, root: QVBoxLayout):
        """测试模式分区：模式下拉（tabler 图标标识静态/动态）+ Universe 行。"""
        mode_grp = PanelSection("测试模式")
        mode_lay = QVBoxLayout()
        mode_lay.setContentsMargins(0, 0, 0, 0)
        mode_lay.setSpacing(6)

        self._mode_combo = QComboBox()
        for display, mode_key, is_dyn, desc in PATTERN_LIST:
            # 静态/动态以 tabler 图标区分（square / player-play），文案不带形状字形。
            icon = load_tabler_icon("player-play" if is_dyn else "square", 12,
                                    BLUE_LT if is_dyn else TEXT_SECONDARY)
            self._mode_combo.addItem(icon, display, userData=(mode_key, is_dyn, desc))
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_lay.addWidget(self._mode_combo)

        self._desc_lbl = QLabel("")
        self._desc_lbl.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:10px;padding:1px 4px;")
        self._desc_lbl.setWordWrap(True)
        mode_lay.addWidget(self._desc_lbl)

        # Universe 行（D波：prop_row 版式 + 22px 控件）
        self._uni_spin = QSpinBox()
        # 修正：Universe 从 1 开始（1-based），最大 32768 对应 Art-Net 上限
        self._uni_spin.setRange(1, 32768)
        self._uni_spin.setValue(1)
        self._uni_spin.setFixedWidth(80)
        self._uni_spin.setToolTip(
            "Universe 编号从 1 开始。\n"
            "Art-Net 发出时自动转换为 0-based（内部处理，无需手动 -1）。"
        )
        # 显示实际发出的协议 universe 编号（仅用于 debug 提示）
        self._uni_hint = QLabel("")
        self._uni_hint.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:10px;")
        self._uni_spin.valueChanged.connect(self._update_uni_hint)
        uni_row_w = prop_row("Universe", self._uni_spin, label_width=64)
        uni_row_w.layout().addWidget(self._uni_hint)
        mode_lay.addWidget(uni_row_w)
        mode_grp.add_layout(mode_lay)
        root.addWidget(mode_grp)

        self._update_uni_hint(1)

    def _build_param_section(self, root: QVBoxLayout):
        """参数分区（prop_row + NumSlider，按模式动态换行）。"""
        param_grp = PanelSection("参数")
        self._param_panel = _ParamPanel()
        self._param_panel.params_changed.connect(self._on_params)
        param_grp.add_widget(self._param_panel)
        root.addWidget(param_grp)

    def _build_control_section(self, root: QVBoxLayout):
        """控制分区：开始/停止、单次、全灭（按钮统一 24px + tabler 图标）。"""
        ctrl_grp = PanelSection("控制")
        ctrl_lay = QHBoxLayout()
        ctrl_lay.setContentsMargins(0, 0, 0, 0)
        ctrl_lay.setSpacing(6)

        # R182：统一走 network_button_factory（22px/11px/14px 图标/令牌色，
        # 与配置页、诊断页同一套规格）。
        self._start_btn = make_button(
            "开始发送", "player-play", "success", checkable=True,
            tooltip="按当前模式与参数持续发送测试信号（再次点击停止）")
        self._start_btn.toggled.connect(self._on_start_toggled)
        ctrl_lay.addWidget(self._start_btn)

        self._once_btn = make_button(
            "单次", "send", "neutral",
            tooltip="只发送一帧：静态模式即当前帧，动态模式推进一帧后停表")
        self._once_btn.clicked.connect(self._send_once)
        ctrl_lay.addWidget(self._once_btn)

        self._blackout_btn = make_button(
            "全灭", "ban", "danger",
            tooltip="向当前 Universe 直发一帧全 0（立即清场，不受层清空影响）")
        self._blackout_btn.clicked.connect(self._blackout)
        ctrl_lay.addWidget(self._blackout_btn)
        ctrl_lay.addStretch()
        ctrl_grp.add_layout(ctrl_lay)
        root.addWidget(ctrl_grp)

    def _build_heatmap_section(self, root: QVBoxLayout):
        """512 通道热图分区（jet 梯度编码保持不动）。"""
        hm_grp = PanelSection("512 通道热图")
        hm_lay = QVBoxLayout()
        hm_lay.setContentsMargins(0, 0, 0, 0)
        hm_lay.setSpacing(4)

        self._heatmap = DmxHeatmap()
        self._heatmap.channel_hovered.connect(
            lambda ch, val: self._hover_lbl.setText(
                f"通道 {ch+1:>3d}  值: {val:>3d}  ({val/255*100:.1f}%)"))
        hm_lay.addWidget(self._heatmap)

        self._hover_lbl = QLabel("悬停通道查看实时值")
        self._hover_lbl.setFixedHeight(22)
        self._hover_lbl.setStyleSheet(
            f"color:{TEXT_SECONDARY};font-size:11px;"
            "font-family:'Consolas','DejaVu Sans Mono',monospace;padding:0 4px;")
        hm_lay.addWidget(self._hover_lbl)
        hm_grp.add_layout(hm_lay)
        root.addWidget(hm_grp)

    def _build_channel_section(self, root: QVBoxLayout):
        """通道测试分区（通道表达式 + 值；保留原有功能）。"""
        ch_grp = PanelSection("通道测试")
        ch_lay = QVBoxLayout()
        ch_lay.setContentsMargins(0, 0, 0, 0)
        ch_lay.setSpacing(6)

        self._ch_input = QLineEdit()
        self._ch_input.setPlaceholderText("例: 1,3,5-10,20")
        # 金=输入值惯例（协议字段），取 bright_warn 文字
        self._ch_input.setStyleSheet(
            f"QLineEdit{{background:{BG_INPUT};color:{AMBER};"
            f"border:1px solid {BORDER_NORMAL};"
            f"border-radius:{RADIUS}px;font-family:Consolas;font-size:12px;"
            f"padding:2px 8px;}}"
            f"QLineEdit:focus{{border-color:{FOCUS_BLUE};}}")
        self._ch_val = QSpinBox()
        self._ch_val.setRange(0, 255)
        self._ch_val.setValue(255)
        self._ch_val.setFixedWidth(70)
        val_wrap = QWidget()
        vw_lay = QHBoxLayout(val_wrap)
        vw_lay.setContentsMargins(0, 0, 0, 0)
        vw_lay.setSpacing(6)
        vw_lab = QLabel("值:")
        vw_lab.setStyleSheet(f"color:{TEXT_SECONDARY};font-size:11px;background:transparent;")
        vw_lay.addWidget(self._ch_input, stretch=1)
        vw_lay.addWidget(vw_lab)
        vw_lay.addWidget(self._ch_val)
        ch_lay.addWidget(prop_row("通道", val_wrap))

        # R182：与配置/诊断页同一按钮规格（原为独立 rgba 底 + 24px 高）。
        self._ch_send_btn = make_button(
            "发送通道值", "send", "neutral",
            tooltip="把「通道」表达式里的通道设为「值」并发送一帧（其余通道不接管）")
        self._ch_send_btn.clicked.connect(self._send_channel_test)
        ch_lay.addWidget(self._ch_send_btn)
        ch_grp.add_layout(ch_lay)
        root.addWidget(ch_grp)

    # ── 槽 ───────────────────────────────────────────────────────────────────

    def _update_uni_hint(self, val: int):
        """在 SpinBox 旁显示 Art-Net 实际发出的 0-based 编号，方便调试"""
        self._uni_hint.setText(f"(Art-Net: {val - 1})")

    def _on_mode_changed(self, idx: int):
        mode_key, is_dyn, desc = self._mode_combo.itemData(idx)
        self._current_mode = mode_key
        self._is_dynamic = is_dyn
        self._desc_lbl.setText(("动态  " if is_dyn else "静态  ") + desc)
        self._param_panel.load(mode_key)

        # 如果正在运行，立即切换模式
        if self._start_btn.isChecked():
            self._tester.start_test(mode_key, self._uni_spin.value(), self._params)

    def _on_params(self, params: dict):
        self._params = params
        # 运行中改参数要重发 start_test，否则已启动的测试仍沿用旧参数
        if self._start_btn.isChecked():
            self._tester.start_test(
                self._current_mode, self._uni_spin.value(), self._params)

    def _on_start_toggled(self, checked: bool):
        if checked:
            self._tester.start_test(
                self._current_mode, self._uni_spin.value(), self._params)
            self._start_btn.setText("停止发送")
            self._start_btn.setIcon(load_tabler_icon("player-pause", 13, GREEN))
        else:
            self._tester.stop_test()
            self._start_btn.setText("开始发送")
            self._start_btn.setIcon(load_tabler_icon("player-play", 13, GREEN))

    def _send_once(self):
        """单次发送：静态模式发一帧；动态模式只发一帧后停止（保留该帧）。"""
        self._tester.start_test(
            self._current_mode, self._uni_spin.value(), self._params)
        if self._is_dynamic:
            # 动态模式：start_test 只启动 33ms 定时器、并不立即发帧；这里手动推进
            # 一帧后停表（不清 tester 层），实现「只发一帧后 stop」的单发语义。
            self._tester._send_step()
            timer = self._tester._timer
            if timer is not None:
                timer.stop()
                self._tester._timer = None
            self._tester._running = False
        # 单次发送不是持续运行：同步「开始发送」按钮为未运行态（blockSignals 避免
        # 触发 _on_start_toggled 而 stop_test 清空刚发出的那一帧）。
        self._start_btn.blockSignals(True)
        self._start_btn.setChecked(False)
        self._start_btn.setText("开始发送")
        self._start_btn.setIcon(load_tabler_icon("player-play", 13, GREEN))
        self._start_btn.blockSignals(False)

    def _blackout(self):
        """全灭：直接向当前 Universe 实发一帧全 0（R182 P1-6 修）。

        修前 ``start_test("zero") + stop_test()`` 同槽连调：``start_test``
        先 ``stop_test``（清 tester 层）再写全零层，紧接着的 ``stop_test``
        又把刚写好的层清掉——净效果是什么都没发出去，"全灭"完全无效。

        改为 ``NetworkManager.send_raw``：绕过层混音直接下发一帧全零到硬件
        路径（与通道测试的定向写层语义不同，全灭要的是**立即清场**），
        且不受"层被后续清空"影响。
        """
        if self._start_btn.isChecked():
            self._start_btn.setChecked(False)     # 触发 _on_start_toggled -> stop_test
        uni = self._uni_spin.value()
        send_raw = getattr(self._nm, "send_raw", None) if self._nm is not None else None
        if callable(send_raw):
            try:
                send_raw(uni, np.zeros(512, dtype=np.uint8))
                self._log(f"全灭 Blackout U{uni}（已直发全 0 帧）")
                return
            except Exception as e:
                self._log(f"全灭直发失败，回退零层: {e}")
        # 无 NM / send_raw 不可用（离线替身）时回退：置一次零层并保持不下线。
        try:
            self._tester.start_test("zero", uni, {})
            self._log(f"全灭 Blackout U{uni}（零层已置位）")
        except Exception as e:
            self._log(f"全灭失败: {e}")

    def _send_channel_test(self):
        expr = self._ch_input.text().strip()
        if not expr:
            return
        channels = self._parse_channels(expr)
        if not channels:
            self._log("无效的通道表达式")
            return
        self._tester.start_channel_test(
            self._uni_spin.value(), channels, self._ch_val.value())
        self._log(f"通道测试: {len(channels)} 个通道  值={self._ch_val.value()}")

    def _refresh_heatmap(self):
        """从 network_manager 拉最新 DMX 数据刷新热图（universe 传 1-based）"""
        try:
            if hasattr(self._nm, "get_last_dmx"):
                buf = self._nm.get_last_dmx(self._uni_spin.value())
                if buf is not None:
                    self._heatmap.update_buffer(bytes(buf))
                    nz  = sum(1 for v in buf if v > 0)
                    tot = int(sum(int(v) for v in buf))
                    self._stat_lbl.setText(
                        f"输出: {nz} 个非零通道 | 总值: {int(tot):,}")
        except Exception:
            pass

    @staticmethod
    def _parse_channels(expr: str) -> list[int]:
        result = []
        for part in re.split(r"[,;]+", expr):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                try:
                    lo, hi = part.split("-", 1)
                    lo, hi = int(lo.strip()), int(hi.strip())
                    if 1 <= lo <= 512 and 1 <= hi <= 512:
                        result.extend(range(min(lo, hi), max(lo, hi) + 1))
                except ValueError:
                    pass
            else:
                try:
                    ch = int(part)
                    if 1 <= ch <= 512:
                        result.append(ch)
                except ValueError:
                    pass
        return sorted(set(result))

    def _log(self, msg: str):
        self._status.append(msg)

    # ── 公共接口 ──────────────────────────────────────────────────────────────

    def stop_test(self):
        """外部调用停止（保持与原有接口兼容）"""
        if self._start_btn.isChecked():
            self._start_btn.setChecked(False)
        else:
            self._tester.stop_test()
