"""
bridge_joystick_panel.py — 音频桥接器·摇杆绑定子页面 (v1)
===========================================================
设计思路：
  - 纯手动摇杆控制，不涉及音频驱动
  - 调制目标统一为三个参数（与自动调制页面保持一致）：
      1. 叠加速度  — 摇杆驱动效果速度的即时微调幅度（speed_delta）
      2. 动作幅度  — 摇杆驱动效果动作幅度的叠加量（amplitude_delta）
      3. 密度      — 摇杆驱动效果相位差的叠加量（phase_step_delta）
  - 每个目标参数均有：
      · 启用开关
      · 摇杆轴绑定（X轴/Y轴/半径/角度）
      · 叠加幅度（overlay_amount）：最大叠加量
      · 调制范围（range_lo / range_hi）：叠加后参数的上下限约束
      · 响应曲线（linear / exp / log / step）
      · 反转开关
  - 支持独立启用/关闭
  - 通过 overlay_params_updated 信号发射叠加参数字典给 AudioBridgePanel
"""
from __future__ import annotations

import math
from typing import Dict, Optional

from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QPointF
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QFont,
    QRadialGradient, QBrush, QLinearGradient,
)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSlider, QCheckBox, QComboBox,
    QFrame, QSizePolicy,
)

from .. import qascade_theme as _th

# ─── 颜色常量（引用主题令牌，不再自建十六进制）───────────────────────────────
BG_MAIN   = _th.BG_ROOT
BG_PANEL  = _th.BG_EDITOR
BG_CARD   = _th.BG_BOX
BG_HEADER = _th.BG_EDITOR
FG_MAIN   = _th.TEXT_PRIMARY
FG_DIM    = _th.TEXT_SECONDARY
FG_ACCENT = _th.FOCUS_BLUE
FG_GREEN  = _th.BRIGHT_SUCCESS
FG_ORANGE = _th.BRIGHT_WARN
FG_RED    = _th.BRIGHT_ERROR
FG_CYAN   = _th.BRIGHT_INFO

# ─── 样式表（通用外观交给全局主题接管，此处只留语义色特化）───────────────────
# 已删除 _COMBO：与全局 QComboBox 外观完全重复，两处调用点已各自
# setFixedWidth，无需局部 min-width（§3 结构性统一）。
_SLIDER = (
    f"QSlider::groove:horizontal{{background:{_th.BG_EDITOR};height:4px;border-radius:2px;}}"
    f"QSlider::handle:horizontal{{background:{FG_ACCENT};width:12px;height:12px;"
    "margin:-4px 0;border-radius:6px;}"
    "QSlider::sub-page:horizontal{background:#3A4A7A;border-radius:2px;}"
)
_SLIDER_GREEN = (
    f"QSlider::groove:horizontal{{background:{_th.BG_EDITOR};height:4px;border-radius:2px;}}"
    f"QSlider::handle:horizontal{{background:{FG_GREEN};width:12px;height:12px;"
    "margin:-4px 0;border-radius:6px;}"
    "QSlider::sub-page:horizontal{background:#3a4a3a;border-radius:2px;}"
)
_SLIDER_ORANGE = (
    f"QSlider::groove:horizontal{{background:{_th.BG_EDITOR};height:4px;border-radius:2px;}}"
    f"QSlider::handle:horizontal{{background:{FG_ORANGE};width:12px;height:12px;"
    "margin:-4px 0;border-radius:6px;}"
    "QSlider::sub-page:horizontal{background:#3a3a2a;border-radius:2px;}"
)
_SLIDER_CYAN = (
    f"QSlider::groove:horizontal{{background:{_th.BG_EDITOR};height:4px;border-radius:2px;}}"
    f"QSlider::handle:horizontal{{background:{FG_CYAN};width:12px;height:12px;"
    "margin:-4px 0;border-radius:6px;}"
    "QSlider::sub-page:horizontal{background:#2a3a4a;border-radius:2px;}"
)
_BTN_ENABLE = (
    f"QPushButton{{background:{_th.BG_BUTTON};color:{FG_GREEN};border:1px solid {_th.BORDER_NORMAL};"
    f"border-radius:{_th.RADIUS}px;padding:4px 14px;font-size:11px;font-weight:bold;}}"
    f"QPushButton:hover{{background:{_th.BG_BUTTON_HOVER};}}"
    "QPushButton:checked{background:#4a6a4a;color:#FFFFFF;}"
)

# ─── 摇杆轴定义 ──────────────────────────────────────────────────────────────
JOY_AXES = {
    "joy_x":      "X 轴（左右）",
    "joy_y":      "Y 轴（上下）",
    "joy_radius": "半径（距中心）",
    "joy_angle":  "角度（极坐标）",
}

# ─── 调制目标参数 ─────────────────────────────────────────────────────────────
TARGET_PARAMS = [
    {
        "key":    "speed_delta",
        "label":  "叠加速度",
        "desc":   "摇杆驱动效果速度的即时微调叠加幅度",
        "color":  FG_GREEN,
        "sty":    _SLIDER_GREEN,
        "def_axis":   "joy_y",
        "def_amount": 0.5,
        "def_lo":    -1.0,
        "def_hi":     1.0,
    },
    {
        "key":    "amplitude_delta",
        "label":  "动作幅度",
        "desc":   "摇杆驱动效果动作幅度的叠加量",
        "color":  FG_ORANGE,
        "sty":    _SLIDER_ORANGE,
        "def_axis":   "joy_radius",
        "def_amount": 0.4,
        "def_lo":    -0.5,
        "def_hi":     0.5,
    },
    {
        "key":    "phase_step_delta",
        "label":  "密度（相位差）",
        "desc":   "摇杆驱动效果相位差的叠加量",
        "color":  FG_CYAN,
        "sty":    _SLIDER_CYAN,
        "def_axis":   "joy_x",
        "def_amount": 0.3,
        "def_lo":    -0.3,
        "def_hi":     0.3,
    },
]


# ─────────────────────────────────────────────────────────────────────────────
#  虚拟摇杆控件
# ─────────────────────────────────────────────────────────────────────────────
class _VirtualJoystick(QWidget):
    """
    可拖动的虚拟摇杆控件。
    position_changed(x, y) 发射归一化坐标：x/y 均在 [0, 1]，中心为 (0.5, 0.5)。
    """
    position_changed = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._x = 0.5
        self._y = 0.5
        self._dragging = False
        self._spring = False
        self.setFixedSize(140, 140)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_spring(self, enabled: bool):
        self._spring = enabled
        if enabled:
            self._x = 0.5
            self._y = 0.5
            self.position_changed.emit(self._x, self._y)
            self.update()

    def get_position(self):
        return self._x, self._y

    def set_position(self, x: float, y: float):
        self._x = max(0.0, min(1.0, x))
        self._y = max(0.0, min(1.0, y))
        self.update()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._update_from_mouse(ev.position())

    def mouseMoveEvent(self, ev):
        if self._dragging:
            self._update_from_mouse(ev.position())

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            if self._spring:
                self._x = 0.5
                self._y = 0.5
                self.position_changed.emit(self._x, self._y)
                self.update()

    def _update_from_mouse(self, pos):
        margin = 14
        w, h = self.width(), self.height()
        x = (pos.x() - margin) / max(1, w - 2 * margin)
        y = (pos.y() - margin) / max(1, h - 2 * margin)
        self._x = max(0.0, min(1.0, x))
        self._y = max(0.0, min(1.0, 1.0 - y))
        self.position_changed.emit(self._x, self._y)
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        margin = 14
        cx, cy = w / 2, h / 2

        # 背景
        p.fillRect(0, 0, w, h, QColor("#1D1D1D"))
        # 边框
        p.setPen(QPen(QColor("#545454"), 1))
        p.drawRoundedRect(2, 2, w - 4, h - 4, 6, 6)
        # 网格
        p.setPen(QPen(QColor("#303030"), 1))
        p.drawLine(int(cx), margin, int(cx), h - margin)
        p.drawLine(margin, int(cy), w - margin, int(cy))
        # 同心圆
        p.setPen(QPen(QColor("#303030"), 1, Qt.PenStyle.DashLine))
        for r_frac in [0.33, 0.66, 1.0]:
            r = int((min(w, h) / 2 - margin) * r_frac)
            p.drawEllipse(int(cx) - r, int(cy) - r, r * 2, r * 2)
        # 摇杆位置
        px = margin + self._x * (w - 2 * margin)
        py = margin + (1.0 - self._y) * (h - 2 * margin)
        # 连线（focus_blue 族，青蓝夜光退役）
        p.setPen(QPen(QColor("#71A8FF"), 1))
        p.drawLine(int(cx), int(cy), int(px), int(py))
        # 摇杆点
        grad = QRadialGradient(px, py, 10)
        grad.setColorAt(0, QColor("#71A8FF"))
        grad.setColorAt(1, QColor("#35507C"))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(QColor("#71A8FF"), 2))
        p.drawEllipse(QPointF(px, py), 8, 8)
        # 坐标文字
        f = QFont()
        f.setPixelSize(10)
        p.setFont(f)
        p.setPen(QPen(QColor(FG_DIM)))
        p.drawText(4, h - 4, f"X:{self._x:.2f}  Y:{self._y:.2f}")
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
#  单个调制目标参数的控制卡片
# ─────────────────────────────────────────────────────────────────────────────
class _JoyTargetCard(QFrame):
    """单个摇杆调制目标参数的控制卡片"""
    changed = pyqtSignal()

    def __init__(self, param_def: dict, parent=None):
        super().__init__(parent)
        self._param_def = param_def
        self.setStyleSheet(
            f"QFrame{{background:{BG_CARD};border:1px solid #303030;border-radius:4px;}}"
        )
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(5)

        pd = self._param_def
        color = pd["color"]
        sty = pd["sty"]

        # ── 标题行 ──
        title_row = QHBoxLayout()
        self._cb_enabled = QCheckBox(pd["label"])
        self._cb_enabled.setStyleSheet(
            f"color:{color};font-size:11px;font-weight:bold;"
            f"background:transparent;border:none;"
        )
        self._cb_enabled.setChecked(True)
        self._cb_enabled.toggled.connect(self.changed)
        title_row.addWidget(self._cb_enabled)
        title_row.addStretch()
        self._lbl_output = QLabel("叠加: 0.000")
        self._lbl_output.setStyleSheet(
            f"color:{color};font-size:10px;font-family:Consolas,monospace;"
            f"background:transparent;border:none;"
        )
        title_row.addWidget(self._lbl_output)
        lay.addLayout(title_row)

        # ── 参数网格 ──
        grid_lay = QHBoxLayout()
        grid_lay.setSpacing(8)

        def _mk_lbl(text, c=FG_DIM):
            l = QLabel(text)
            l.setStyleSheet(
                f"color:{c};font-size:10px;background:transparent;border:none;"
            )
            l.setMinimumWidth(60)
            return l

        def _mk_val(text, c=FG_ACCENT):
            l = QLabel(text)
            l.setStyleSheet(
                f"color:{c};font-size:10px;min-width:38px;"
                f"background:transparent;border:none;"
            )
            return l

        # 左列
        left = QVBoxLayout()
        left.setSpacing(3)

        # 摇杆轴
        ax_row = QHBoxLayout()
        ax_row.addWidget(_mk_lbl("摇杆轴:"))
        self._combo_axis = QComboBox()
        self._combo_axis.setFixedWidth(130)
        for key, label in JOY_AXES.items():
            self._combo_axis.addItem(label, key)
        idx = self._combo_axis.findData(pd["def_axis"])
        if idx >= 0:
            self._combo_axis.setCurrentIndex(idx)
        self._combo_axis.currentIndexChanged.connect(self.changed)
        ax_row.addWidget(self._combo_axis)
        # 曲线
        ax_row.addWidget(_mk_lbl("曲线:"))
        self._combo_curve = QComboBox()
        self._combo_curve.setFixedWidth(75)
        for key, label in [("linear","线性"),("exp","指数"),("log","对数"),("step","阶梯")]:
            self._combo_curve.addItem(label, key)
        self._combo_curve.currentIndexChanged.connect(self.changed)
        ax_row.addWidget(self._combo_curve)
        # 反转
        self._cb_invert = QCheckBox("反转")
        self._cb_invert.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;background:transparent;border:none;"
        )
        self._cb_invert.toggled.connect(self.changed)
        ax_row.addWidget(self._cb_invert)
        ax_row.addStretch()
        left.addLayout(ax_row)

        # 叠加幅度
        amt_row = QHBoxLayout()
        amt_row.addWidget(_mk_lbl("叠加幅度:", color))
        self._sld_amount = QSlider(Qt.Orientation.Horizontal)
        self._sld_amount.setRange(0, 200)
        self._sld_amount.setValue(int(pd["def_amount"] * 100))
        self._sld_amount.setStyleSheet(sty)
        self._sld_amount.setMinimumWidth(100)
        self._lbl_amount = _mk_val(f"{pd['def_amount']:.2f}", color)
        self._sld_amount.valueChanged.connect(
            lambda v: self._lbl_amount.setText(f"{v/100:.2f}"))
        self._sld_amount.valueChanged.connect(lambda _: self.changed.emit())
        amt_row.addWidget(self._sld_amount)
        amt_row.addWidget(self._lbl_amount)
        left.addLayout(amt_row)

        # 范围下限
        lo_row = QHBoxLayout()
        lo_row.addWidget(_mk_lbl("范围下限:"))
        self._sld_lo = QSlider(Qt.Orientation.Horizontal)
        self._sld_lo.setRange(-200, 0)
        self._sld_lo.setValue(int(pd["def_lo"] * 100))
        self._sld_lo.setStyleSheet(_SLIDER)
        self._sld_lo.setMinimumWidth(100)
        self._lbl_lo = _mk_val(f"{pd['def_lo']:.2f}", FG_DIM)
        self._sld_lo.valueChanged.connect(
            lambda v: self._lbl_lo.setText(f"{v/100:.2f}"))
        self._sld_lo.valueChanged.connect(lambda _: self.changed.emit())
        lo_row.addWidget(self._sld_lo)
        lo_row.addWidget(self._lbl_lo)
        left.addLayout(lo_row)

        # 范围上限
        hi_row = QHBoxLayout()
        hi_row.addWidget(_mk_lbl("范围上限:"))
        self._sld_hi = QSlider(Qt.Orientation.Horizontal)
        self._sld_hi.setRange(0, 200)
        self._sld_hi.setValue(int(pd["def_hi"] * 100))
        self._sld_hi.setStyleSheet(sty)
        self._sld_hi.setMinimumWidth(100)
        self._lbl_hi = _mk_val(f"{pd['def_hi']:.2f}", color)
        self._sld_hi.valueChanged.connect(
            lambda v: self._lbl_hi.setText(f"{v/100:.2f}"))
        self._sld_hi.valueChanged.connect(lambda _: self.changed.emit())
        hi_row.addWidget(self._sld_hi)
        hi_row.addWidget(self._lbl_hi)
        left.addLayout(hi_row)

        grid_lay.addLayout(left, 1)
        lay.addLayout(grid_lay)

        # 描述
        desc_lbl = QLabel(pd["desc"])
        desc_lbl.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;background:transparent;border:none;"
        )
        lay.addWidget(desc_lbl)

    # ── 属性 ──────────────────────────────────────────────────────────────
    @property
    def enabled(self) -> bool:
        return self._cb_enabled.isChecked()

    @property
    def axis(self) -> str:
        return str(self._combo_axis.currentData() or "joy_x")

    @property
    def amount(self) -> float:
        return self._sld_amount.value() / 100.0

    @property
    def range_lo(self) -> float:
        return self._sld_lo.value() / 100.0

    @property
    def range_hi(self) -> float:
        return self._sld_hi.value() / 100.0

    @property
    def curve(self) -> str:
        return str(self._combo_curve.currentData() or "linear")

    @property
    def invert(self) -> bool:
        return self._cb_invert.isChecked()

    def compute_overlay(self, joy_sources: Dict[str, float]) -> float:
        """根据摇杆源值计算叠加量"""
        if not self.enabled:
            return 0.0
        raw = joy_sources.get(self.axis, 0.5)
        t = max(0.0, min(1.0, raw))
        if self.invert:
            t = 1.0 - t
        curve = self.curve
        if curve == "exp":
            t = t * t
        elif curve == "log":
            t = math.sqrt(t)
        elif curve == "step":
            t = 1.0 if t >= 0.5 else 0.0
        lo = self.range_lo
        hi = self.range_hi
        # 与 bridge_modulation_panel 同款对中语义: 完整双向映射后再 amount
        # 独立缩放, 中性位(t=0.5)叠加恒 0, 而非旧式的 lo 一侧恒偏移。
        return (lo + t * (hi - lo)) * self.amount

    def update_output(self, overlay: float):
        self._lbl_output.setText(f"叠加: {overlay:+.3f}")

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "axis": self.axis,
            "amount": self.amount,
            "range_lo": self.range_lo,
            "range_hi": self.range_hi,
            "curve": self.curve,
            "invert": self.invert,
        }

    def load_dict(self, d: dict):
        self._cb_enabled.setChecked(bool(d.get("enabled", True)))
        idx = self._combo_axis.findData(d.get("axis", "joy_x"))
        if idx >= 0:
            self._combo_axis.setCurrentIndex(idx)
        self._sld_amount.setValue(int(d.get("amount", 0.5) * 100))
        self._sld_lo.setValue(int(d.get("range_lo", -1.0) * 100))
        self._sld_hi.setValue(int(d.get("range_hi", 1.0) * 100))
        idx2 = self._combo_curve.findData(d.get("curve", "linear"))
        if idx2 >= 0:
            self._combo_curve.setCurrentIndex(idx2)
        self._cb_invert.setChecked(bool(d.get("invert", False)))


# ─────────────────────────────────────────────────────────────────────────────
#  主面板：BridgeJoystickPanel
# ─────────────────────────────────────────────────────────────────────────────
class BridgeJoystickPanel(QWidget):
    """
    音频桥接器·摇杆绑定子页面。
    通过 overlay_params_updated 信号将叠加参数字典发射给 AudioBridgePanel。
    叠加参数字典格式与自动调制页面相同：
      {
        "speed_delta":      float,
        "amplitude_delta":  float,
        "phase_step_delta": float,
      }
    """
    overlay_params_updated = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._enabled = False
        self._joy_x = 0.5
        self._joy_y = 0.5
        self.setStyleSheet(f"background:{BG_MAIN};")
        self._build_ui()
        # 定时发射（30fps）
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._on_tick)
        # D4-1：构造不启动（默认未启用）；toggled 启停，禁用时零空转

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ── 顶部：启用开关 + 说明 ──
        header = QFrame()
        header.setStyleSheet(
            f"background:{BG_HEADER};border-radius:4px;border:1px solid #303030;"
        )
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(12, 8, 12, 8)
        header_lay.setSpacing(12)

        self._btn_enable = QPushButton("启用摇杆绑定")
        self._btn_enable.setCheckable(True)
        self._btn_enable.setStyleSheet(_BTN_ENABLE)
        self._btn_enable.toggled.connect(self._on_enable_toggled)
        header_lay.addWidget(self._btn_enable)

        # 回中开关
        self._cb_spring = QCheckBox("松手回中")
        self._cb_spring.setStyleSheet(
            f"color:{FG_ORANGE};font-size:10px;background:transparent;border:none;"
        )
        self._cb_spring.toggled.connect(self._on_spring_toggled)
        header_lay.addWidget(self._cb_spring)

        header_lay.addStretch()

        info = QLabel("摇杆直接驱动效果参数叠加 | 叠加量在已有效果参数基础上增量调制")
        info.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;background:transparent;border:none;"
        )
        info.setWordWrap(True)
        header_lay.addWidget(info, 1)
        root.addWidget(header)

        # ── 中部：摇杆 + 源值显示 ──
        joy_frame = QFrame()
        joy_frame.setStyleSheet(
            f"background:{BG_PANEL};border-radius:4px;border:1px solid #303030;"
        )
        joy_lay = QHBoxLayout(joy_frame)
        joy_lay.setContentsMargins(12, 10, 12, 10)
        joy_lay.setSpacing(16)

        # 摇杆
        joy_col = QVBoxLayout()
        joy_title = QLabel("虚拟摇杆")
        joy_title.setStyleSheet(
            f"color:{FG_CYAN};font-size:11px;font-weight:bold;"
            f"background:transparent;border:none;"
        )
        joy_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        joy_col.addWidget(joy_title)
        self._joystick = _VirtualJoystick()
        self._joystick.position_changed.connect(self._on_joy_moved)
        joy_col.addWidget(self._joystick, 0, Qt.AlignmentFlag.AlignCenter)
        joy_lay.addLayout(joy_col)

        # 源值显示
        src_col = QVBoxLayout()
        src_col.setSpacing(4)
        src_title = QLabel("摇杆源值")
        src_title.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;font-weight:bold;"
            f"background:transparent;border:none;"
        )
        src_col.addWidget(src_title)

        self._src_labels: Dict[str, QLabel] = {}
        for key, label in JOY_AXES.items():
            row = QHBoxLayout()
            lbl_key = QLabel(f"{label}:")
            lbl_key.setStyleSheet(
                f"color:{FG_DIM};font-size:10px;background:transparent;border:none;"
            )
            lbl_key.setMinimumWidth(100)
            row.addWidget(lbl_key)
            lbl_val = QLabel("0.500")
            lbl_val.setStyleSheet(
                f"color:{FG_ACCENT};font-size:10px;font-family:Consolas,monospace;"
                f"background:transparent;border:none;"
            )
            lbl_val.setMinimumWidth(50)
            row.addWidget(lbl_val)
            row.addStretch()
            self._src_labels[key] = lbl_val
            src_col.addLayout(row)

        src_col.addStretch()
        joy_lay.addLayout(src_col)
        joy_lay.addStretch()
        root.addWidget(joy_frame)

        # ── 三个目标参数卡片 ──
        self._target_cards: Dict[str, _JoyTargetCard] = {}
        for pd in TARGET_PARAMS:
            card = _JoyTargetCard(pd)
            card.changed.connect(lambda: None)  # 参数变化不需要立即处理
            self._target_cards[pd["key"]] = card
            root.addWidget(card)

        root.addStretch()

    def _on_enable_toggled(self, checked: bool):
        self._enabled = checked
        (self._timer.start if checked else self._timer.stop)()
        if not checked:
            self.overlay_params_updated.emit({
                "speed_delta": 0.0,
                "amplitude_delta": 0.0,
                "phase_step_delta": 0.0,
            })

    def _on_spring_toggled(self, checked: bool):
        self._joystick.set_spring(checked)

    def _on_joy_moved(self, x: float, y: float):
        self._joy_x = x
        self._joy_y = y
        # 更新源值显示
        sources = self._get_joy_sources()
        for key, lbl in self._src_labels.items():
            lbl.setText(f"{sources[key]:.3f}")

    def _get_joy_sources(self) -> Dict[str, float]:
        x = self._joy_x
        y = self._joy_y
        dx = x - 0.5
        dy = y - 0.5
        radius = min(1.0, math.sqrt(dx * dx + dy * dy) * 2.0)
        angle = (math.atan2(dy, dx) / math.pi + 1.0) / 2.0  # 归一化到 [0, 1]
        return {
            "joy_x": x,
            "joy_y": y,
            "joy_radius": radius,
            "joy_angle": angle,
        }

    def _on_tick(self):
        """定时计算并发射叠加参数"""
        if not self._enabled:
            return
        sources = self._get_joy_sources()
        overlays = {}
        for pd in TARGET_PARAMS:
            key = pd["key"]
            card = self._target_cards.get(key)
            if card is None:
                overlays[key] = 0.0
                continue
            overlay = card.compute_overlay(sources)
            overlays[key] = overlay
            card.update_output(overlay)
        self.overlay_params_updated.emit(overlays)

    def inject_external_joystick(self, x: float, y: float):
        """允许外部物理摇杆/MIDI控制器注入位置
        ★ 放缩：中位对称输出 = 中位 + (原始 − 中位) × 比例（与绑定路由一致）。
        """
        try:
            from ..joystick_control_binding import get_joystick_bind_manager
            mgr = get_joystick_bind_manager()
            x = 0.5 + (x - 0.5) * mgr.get_axis_scale("x")
            y = 0.5 + (y - 0.5) * mgr.get_axis_scale("y")
        except Exception:
            pass
        self._joy_x = max(0.0, min(1.0, x))
        self._joy_y = max(0.0, min(1.0, y))
        self._joystick.set_position(self._joy_x, self._joy_y)
        sources = self._get_joy_sources()
        for key, lbl in self._src_labels.items():
            lbl.setText(f"{sources[key]:.3f}")

    def to_dict(self) -> dict:
        return {
            "enabled": self._enabled,
            "spring": self._cb_spring.isChecked(),
            "targets": {k: c.to_dict() for k, c in self._target_cards.items()},
        }

    def load_dict(self, d: dict):
        self._btn_enable.setChecked(bool(d.get("enabled", False)))
        self._cb_spring.setChecked(bool(d.get("spring", False)))
        targets = d.get("targets", {})
        for k, c in self._target_cards.items():
            if k in targets:
                c.load_dict(targets[k])
