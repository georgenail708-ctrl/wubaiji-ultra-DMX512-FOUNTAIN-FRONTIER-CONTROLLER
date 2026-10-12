"""
DMXTimeline — Channel Controller  v21
通用控制器模块：推杆 / 滑块 / 摇杆 适配所有通道类型

架构：
  ChannelMapping      — 控制器输出值 → 通道值的映射规则
  UniversalFader      — 推杆（垂直，适配所有通道类型）
  UniversalSlider     — 滑块（水平，适配所有通道类型）
  UniversalJoystick   — 摇杆（XY/Z，适配 motor/color/valve/pump 等）
  AudioDriver         — 音频分析驱动器（通用，可驱动任意控制器）
  ControllerPanel     — 组合面板（推杆组 + 摇杆 + 音频驱动）
  PresetControlPanel  — 预设参数编辑面板（嵌入通道类型行）
"""
from __future__ import annotations
import math
import time as _time
import threading
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QSpinBox, QDoubleSpinBox, QComboBox, QCheckBox,
    QFrame, QSizePolicy, QScrollArea, QGroupBox, QGridLayout,
    QTabWidget, QButtonGroup,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QPointF, QRectF
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QLinearGradient, QFont,
    QMouseEvent, QPainterPath,
)

from . import qascade_theme as _th

log = logging.getLogger(__name__)

# ─── 颜色常量（统一引用 主题设计令牌，见 qascade_theme.py） ────────────────
BG_MAIN    = _th.BG_ROOT          # #1D1D1D 页面底
BG_PANEL   = _th.BG_EDITOR        # #303030 面板基底
BG_HEADER  = _th.BG_EDITOR
BG_CTRL    = _th.BG_EDITOR
FG_MAIN    = _th.TEXT_PRIMARY     # #E6E6E6
FG_DIM     = _th.TEXT_SECONDARY   # #999999
FG_ACCENT  = _th.FOCUS_BLUE       # #71A8FF 焦点/高亮文字
FG_GREEN   = _th.BRIGHT_SUCCESS   # 文字级亮绿（填充语义见 AudioDriver）
FG_ORANGE  = _th.BRIGHT_WARN
FG_RED     = _th.BRIGHT_ERROR
FG_CYAN    = _th.BRIGHT_INFO      # 音频驱动指示青蓝家族
FG_PINK    = _th.TEXT_SECONDARY

# 通道类别色：并轨到全站统一的 CATEGORY_COLORS。
# 中央表没有的键按就近语义取值（valve→amber、pump→uv、motor_z→fx），键名保持不变。
CH_COLORS = {
    "dimmer":  _th.CATEGORY_COLORS["dimmer"],
    "R":       _th.CATEGORY_COLORS["red"],
    "G":       _th.CATEGORY_COLORS["green"],
    "B":       _th.CATEGORY_COLORS["blue"],
    "W":       _th.CATEGORY_COLORS["white"],
    "valve":   _th.CATEGORY_COLORS["amber"],   # 中央表无 valve → 琥珀近义
    "pump":    _th.CATEGORY_COLORS["uv"],      # 泵：保留旧粉紫邻近的紫系
    "motor_x": _th.CATEGORY_COLORS["pan"],
    "motor_y": _th.CATEGORY_COLORS["tilt"],
    "motor_z": _th.CATEGORY_COLORS["fx"],
}

# 通道类型 → 控制器推荐模式
CH_CTRL_MODE = {
    "dimmer":  "fader",     # 推杆（0-255 线性）
    "R":       "fader",
    "G":       "fader",
    "B":       "fader",
    "W":       "fader",
    "valve":   "fader",
    "pump":    "fader",
    "motor_x": "joystick",  # 摇杆 X 轴
    "motor_y": "joystick",  # 摇杆 Y 轴
    "motor_z": "slider",    # 滑块（旋转）
}


def _hex_alpha(hex_color: str, alpha_hex: str) -> str:
    """将 #RRGGBB + 2位十六进制 alpha 转换为 rgba(r,g,b,a)"""
    try:
        h = hex_color.lstrip('#')
        if len(h) != 6: return hex_color
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        a = int(alpha_hex, 16) / 255.0
        return f"rgba({r},{g},{b},{a:.2f})"
    except Exception:
        log.debug("_hex_alpha: 解析颜色字符串失败，返回原始值")
        return hex_color


# ══════════════════════════════════════════════════════════════════════════════
#  ChannelMapping — 控制器输出 → 通道值映射规则
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class ChannelMapping:
    """
    描述如何将控制器输出（0.0-1.0）映射到 DMX 通道值（0-255）

    支持的映射类型：
      linear    — 线性映射（默认）
      gamma     — Gamma 曲线（适合亮度感知）
      s_curve   — S 形曲线（适合电机/阀门的平滑控制）
      stepped   — 阶梯映射（适合离散状态通道）
      inverted  — 反向线性（1.0→0, 0.0→255）
      audio     — 由音频驱动（忽略手动输入）
    """
    ch_type: str = "dimmer"
    map_type: str = "linear"      # linear/gamma/s_curve/stepped/inverted/audio
    lo: int = 0
    hi: int = 255
    gamma: float = 1.0            # gamma 曲线指数
    steps: int = 8                # stepped 模式的阶梯数
    audio_band: str = "bass"      # audio 模式使用的频段
    audio_sensitivity: float = 1.0

    def apply(self, normalized: float) -> int:
        """将 0.0-1.0 的归一化值映射到 0-255 的 DMX 值"""
        n = max(0.0, min(1.0, normalized))
        if self.map_type == "linear":
            v = n
        elif self.map_type == "gamma":
            v = math.pow(n, self.gamma) if self.gamma > 0 else n
        elif self.map_type == "s_curve":
            # S 形曲线：慢-快-慢
            v = n * n * (3 - 2 * n)
        elif self.map_type == "stepped":
            steps = max(2, self.steps)
            v = round(n * (steps - 1)) / (steps - 1)
        elif self.map_type == "inverted":
            v = 1.0 - n
        else:
            v = n
        return int(self.lo + v * (self.hi - self.lo))

    def apply_audio(self, band_value: float) -> int:
        """将音频频段值（0.0-1.0）映射到 DMX 值"""
        n = max(0.0, min(1.0, band_value * self.audio_sensitivity))
        return self.apply(n)


# ══════════════════════════════════════════════════════════════════════════════
#  UniversalFader — 通用推杆（垂直，适配所有通道类型）
# ══════════════════════════════════════════════════════════════════════════════
class UniversalFader(QWidget):
    """
    通用推杆控件
    - 适配所有通道类型（亮度/颜色/阀门/泵/电机）
    - 支持映射规则（线性/Gamma/S曲线/阶梯/反向）
    - 支持音频驱动（由 AudioDriver 注入值）
    - 支持触摸/鼠标拖拽
    """
    value_changed = pyqtSignal(str, int)   # (ch_type, dmx_value)

    def __init__(self, ch_type: str = "dimmer",
                 mapping: Optional[ChannelMapping] = None,
                 label: str = "", parent=None):
        super().__init__(parent)
        self._ch_type = ch_type
        self._mapping = mapping or ChannelMapping(ch_type=ch_type)
        self._label = label or ch_type.upper()
        self._normalized = 0.0
        self._dragging = False
        self._audio_driven = False
        self._color = QColor(CH_COLORS.get(ch_type, FG_DIM))
        self.setFixedSize(44, 160)
        # 诚实描述现有交互（此前误写"右键设置映射"，右键功能并不存在）
        self.setToolTip(f"{self._label} — 拖拽或滚轮调节，Shift+滚轮微调")
        self.setCursor(Qt.CursorShape.SizeVerCursor)

    @property
    def ch_type(self) -> str:
        return self._ch_type

    def set_mapping(self, mapping: ChannelMapping):
        self._mapping = mapping
        self.update()

    def set_normalized(self, v: float, emit: bool = True):
        self._normalized = max(0.0, min(1.0, v))
        if emit:
            dmx = self._mapping.apply(self._normalized)
            self.value_changed.emit(self._ch_type, dmx)
        self.update()

    def get_normalized(self) -> float:
        return self._normalized

    def get_dmx_value(self) -> int:
        return self._mapping.apply(self._normalized)

    def set_dmx_value(self, v: int, emit: bool = True):
        lo, hi = self._mapping.lo, self._mapping.hi
        if hi == lo:
            self._normalized = 0.0
        else:
            self._normalized = max(0.0, min(1.0, (v - lo) / (hi - lo)))
        if emit:
            self.value_changed.emit(self._ch_type, v)
        self.update()

    def set_audio_driven(self, driven: bool):
        self._audio_driven = driven
        self.update()

    def inject_audio_value(self, band_value: float):
        """由 AudioDriver 调用，注入音频驱动值"""
        if self._audio_driven:
            self._normalized = max(0.0, min(1.0, band_value))
            dmx = self._mapping.apply_audio(band_value)
            self.value_changed.emit(self._ch_type, dmx)
            self.update()

    # ── 绘制 ──────────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        track_x = w // 2 - 4
        track_w = 8
        track_top = 16
        track_bot = h - 24

        # 背景
        p.fillRect(0, 0, w, h, QColor(BG_CTRL))

        # 轨道背景
        p.setBrush(QBrush(QColor(BG_CTRL)))
        p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1))
        p.drawRoundedRect(track_x, track_top, track_w, track_bot - track_top, 4, 4)

        # 填充区域（渐变）
        fill_h = int((track_bot - track_top) * self._normalized)
        fill_top = track_bot - fill_h
        if fill_h > 0:
            grad = QLinearGradient(0, fill_top, 0, track_bot)
            c = self._color
            if self._audio_driven:
                grad.setColorAt(0, QColor(FG_CYAN))
                _cy = QColor(FG_CYAN); _cy.setAlpha(136)
                grad.setColorAt(1, _cy)
            else:
                grad.setColorAt(0, c.lighter(140))
                grad.setColorAt(1, c.darker(120))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(track_x, fill_top, track_w, fill_h, 4, 4)

        # 推杆手柄
        handle_y = int(track_bot - self._normalized * (track_bot - track_top)) - 6
        handle_y = max(track_top, min(track_bot - 12, handle_y))
        p.setBrush(QBrush(QColor(FG_MAIN)))
        p.setPen(QPen(QColor(self._color), 1))
        p.drawRoundedRect(track_x - 6, handle_y, track_w + 12, 12, 3, 3)

        # 标签
        p.setPen(QPen(QColor(CH_COLORS.get(self._ch_type, FG_DIM))))
        font = QFont("Consolas", 8)
        font.setBold(True)
        p.setFont(font)
        p.drawText(0, h - 14, w, 14, Qt.AlignmentFlag.AlignHCenter,
                   self._label[:4])

        # 值标签
        p.setPen(QPen(QColor(FG_DIM)))
        font2 = QFont("Consolas", 7)
        p.setFont(font2)
        p.drawText(0, 2, w, 12, Qt.AlignmentFlag.AlignHCenter,
                   str(self.get_dmx_value()))

        # 音频驱动指示
        if self._audio_driven:
            p.setPen(QPen(QColor(FG_CYAN), 1))
            p.drawText(0, track_top - 2, w, 10,
                       Qt.AlignmentFlag.AlignHCenter, "♪")
        p.end()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._update_from_mouse(event.position().y())

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            self._update_from_mouse(event.position().y())

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._dragging = False

    def _update_from_mouse(self, y: float):
        track_top = 16
        track_bot = self.height() - 24
        span = track_bot - track_top
        if span <= 0:
            return
        n = 1.0 - (y - track_top) / span
        self.set_normalized(max(0.0, min(1.0, n)))

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        step = 0.02 if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier) else 0.005
        self.set_normalized(self._normalized + (step if delta > 0 else -step))


# ══════════════════════════════════════════════════════════════════════════════
#  UniversalSlider — 通用滑块（水平，适配所有通道类型）
# ══════════════════════════════════════════════════════════════════════════════
class UniversalSlider(QWidget):
    """
    通用水平滑块
    - 适配所有通道类型
    - 支持映射规则
    - 支持音频驱动
    """
    value_changed = pyqtSignal(str, int)

    def __init__(self, ch_type: str = "dimmer",
                 mapping: Optional[ChannelMapping] = None,
                 label: str = "", parent=None):
        super().__init__(parent)
        self._ch_type = ch_type
        self._mapping = mapping or ChannelMapping(ch_type=ch_type)
        self._label = label or ch_type.upper()
        self._normalized = 0.0
        self._dragging = False
        self._audio_driven = False
        self._color = QColor(CH_COLORS.get(ch_type, FG_DIM))
        self.setFixedHeight(36)
        self.setMinimumWidth(120)
        self.setCursor(Qt.CursorShape.SizeHorCursor)

    @property
    def ch_type(self) -> str:
        return self._ch_type

    def set_mapping(self, mapping: ChannelMapping):
        self._mapping = mapping
        self.update()

    def set_normalized(self, v: float, emit: bool = True):
        self._normalized = max(0.0, min(1.0, v))
        if emit:
            dmx = self._mapping.apply(self._normalized)
            self.value_changed.emit(self._ch_type, dmx)
        self.update()

    def get_dmx_value(self) -> int:
        return self._mapping.apply(self._normalized)

    def set_dmx_value(self, v: int, emit: bool = True):
        lo, hi = self._mapping.lo, self._mapping.hi
        if hi == lo:
            self._normalized = 0.0
        else:
            self._normalized = max(0.0, min(1.0, (v - lo) / (hi - lo)))
        if emit:
            self.value_changed.emit(self._ch_type, v)
        self.update()

    def set_audio_driven(self, driven: bool):
        self._audio_driven = driven
        self.update()

    def inject_audio_value(self, band_value: float):
        if self._audio_driven:
            self._normalized = max(0.0, min(1.0, band_value))
            dmx = self._mapping.apply_audio(band_value)
            self.value_changed.emit(self._ch_type, dmx)
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        pad_l, pad_r = 8, 8
        track_y = h // 2 - 3
        track_h = 6
        track_w = w - pad_l - pad_r

        p.fillRect(0, 0, w, h, QColor(BG_CTRL))

        # 标签
        p.setPen(QPen(QColor(CH_COLORS.get(self._ch_type, FG_DIM))))
        font = QFont("Consolas", 8)
        p.setFont(font)
        p.drawText(0, 0, 40, h, Qt.AlignmentFlag.AlignVCenter |
                   Qt.AlignmentFlag.AlignLeft, self._label[:4])

        # 轨道
        p.setBrush(QBrush(QColor(BG_CTRL)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(pad_l + 36, track_y, track_w - 36, track_h, 3, 3)

        # 填充
        fill_w = int((track_w - 36) * self._normalized)
        if fill_w > 0:
            c = self._color if not self._audio_driven else QColor(FG_CYAN)
            grad = QLinearGradient(pad_l + 36, 0, pad_l + 36 + fill_w, 0)
            grad.setColorAt(0, c.darker(120))
            grad.setColorAt(1, c.lighter(140))
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(pad_l + 36, track_y, fill_w, track_h, 3, 3)

        # 手柄
        hx = pad_l + 36 + int((track_w - 36) * self._normalized) - 5
        hx = max(pad_l + 36, min(w - pad_r - 10, hx))
        p.setBrush(QBrush(QColor(FG_MAIN)))
        p.setPen(QPen(self._color, 1))
        p.drawRoundedRect(hx, track_y - 3, 10, track_h + 6, 3, 3)

        # 值
        p.setPen(QPen(QColor(FG_DIM)))
        font2 = QFont("Consolas", 8)
        p.setFont(font2)
        p.drawText(w - 30, 0, 30, h, Qt.AlignmentFlag.AlignVCenter |
                   Qt.AlignmentFlag.AlignRight, str(self.get_dmx_value()))
        p.end()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._update_from_mouse(event.position().x())

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            self._update_from_mouse(event.position().x())

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._dragging = False

    def _update_from_mouse(self, x: float):
        pad_l = 8 + 36
        pad_r = 8
        span = self.width() - pad_l - pad_r
        if span <= 0:
            return
        n = (x - pad_l) / span
        self.set_normalized(max(0.0, min(1.0, n)))

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        step = 0.02 if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier) else 0.005
        self.set_normalized(self._normalized + (step if delta > 0 else -step))


# ══════════════════════════════════════════════════════════════════════════════
#  UniversalJoystick — 通用摇杆（XY + Z，适配多种通道类型）
# ══════════════════════════════════════════════════════════════════════════════
class UniversalJoystick(QWidget):
    """
    通用摇杆控件
    - XY 轴：适配 motor_x/motor_y, R/G（颜色混合）, valve/pump（双通道）
    - Z 轴：额外的垂直推杆（适配 motor_z, dimmer, W 等）
    - 支持弹性回中、锁定模式
    - 支持音频驱动（X轴=低频, Y轴=高频）
    - 支持预设轨迹（圆形/8字/扫描）
    """
    position_changed = pyqtSignal(float, float)    # x, y (0.0-1.0)
    z_changed = pyqtSignal(float)                  # z (0.0-1.0)
    xy_dmx_changed = pyqtSignal(str, int, str, int)  # ch_x, val_x, ch_y, val_y

    def __init__(self,
                 ch_x: str = "motor_x",
                 ch_y: str = "motor_y",
                 ch_z: Optional[str] = None,
                 mapping_x: Optional[ChannelMapping] = None,
                 mapping_y: Optional[ChannelMapping] = None,
                 parent=None):
        super().__init__(parent)
        self._ch_x = ch_x
        self._ch_y = ch_y
        self._ch_z = ch_z
        self._mapping_x = mapping_x or ChannelMapping(ch_type=ch_x)
        self._mapping_y = mapping_y or ChannelMapping(ch_type=ch_y)
        self._x = 0.5
        self._y = 0.5
        self._z = 0.0
        self._target_x = 0.5
        self._target_y = 0.5
        self._dragging = False
        self._spring = True
        self._locked = False
        self._audio_driven = False
        self._preset_mode: Optional[str] = None
        self._preset_params: Dict[str, Any] = {}
        self._preset_t = 0.0
        self._color_x = QColor(CH_COLORS.get(ch_x, CH_COLORS["motor_x"]))
        self._color_y = QColor(CH_COLORS.get(ch_y, CH_COLORS["motor_y"]))

        self.setFixedSize(160, 160)
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._physics_step)
        # ★ R183 3.1：不可见不空转（照 dispatch_panel showEvent/hideEvent 先例）。
        #   原 `__init__` 直接 start 且从不停止 → 面板隐藏后仍 16ms PreciseTimer
        #   常烧 CPU；物理步进是纯 UI 动画（阻尼趋近目标值），隐藏期间无观测意义。
        self._timer.start()

    def showEvent(self, event):  # noqa: N802
        """可见 → 恢复物理步进（隐藏期间暂停，见 __init__ 说明）。"""
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start()

    def hideEvent(self, event):  # noqa: N802
        """隐藏 → 停 16ms 轮询（重见时 showEvent 恢复）。"""
        self._timer.stop()
        super().hideEvent(event)

    # ── 属性 ──────────────────────────────────────────────────────────────────
    @property
    def ch_x(self) -> str:
        return self._ch_x

    @property
    def ch_y(self) -> str:
        return self._ch_y

    def set_spring(self, enabled: bool):
        self._spring = enabled

    def set_locked(self, locked: bool):
        self._locked = locked

    def set_audio_driven(self, driven: bool):
        self._audio_driven = driven

    def inject_audio_xy(self, x_band: float, y_band: float):
        """音频驱动：x_band=低频能量, y_band=高频能量"""
        if self._audio_driven:
            self._target_x = max(0.0, min(1.0, x_band))
            self._target_y = max(0.0, min(1.0, y_band))

    def set_preset_mode(self, mode: Optional[str], params: dict = None):
        """设置摇杆轨迹预设（circle/figure8/scan/none）"""
        self._preset_mode = mode
        self._preset_params = params or {}
        self._preset_t = 0.0

    def get_position(self) -> Tuple[float, float]:
        return self._x, self._y

    def set_position(self, x: float, y: float, emit: bool = True):
        self._x = max(0.0, min(1.0, x))
        self._y = max(0.0, min(1.0, y))
        self._target_x = self._x
        self._target_y = self._y
        if emit:
            self._emit_xy()
        self.update()

    def _emit_xy(self):
        vx = self._mapping_x.apply(self._x)
        vy = self._mapping_y.apply(self._y)
        self.position_changed.emit(self._x, self._y)
        self.xy_dmx_changed.emit(self._ch_x, vx, self._ch_y, vy)

    # ── 物理步进 ──────────────────────────────────────────────────────────────
    def _physics_step(self):
        dt = 0.016

        # 预设轨迹
        if self._preset_mode and not self._dragging:
            self._preset_t += dt
            t = self._preset_t
            p = self._preset_params
            if self._preset_mode == "circle":
                speed = p.get("speed", 1.0)
                radius = p.get("radius", 0.4)
                cx, cy = p.get("cx", 0.5), p.get("cy", 0.5)
                angle = t * speed * 2 * math.pi
                self._target_x = cx + radius * math.cos(angle)
                self._target_y = cy + radius * math.sin(angle)
            elif self._preset_mode == "figure8":
                speed = p.get("speed", 0.5)
                rx, ry = p.get("rx", 0.35), p.get("ry", 0.2)
                angle = t * speed * 2 * math.pi
                self._target_x = 0.5 + rx * math.sin(angle)
                self._target_y = 0.5 + ry * math.sin(2 * angle)
            elif self._preset_mode == "scan":
                speed = p.get("speed", 0.3)
                self._target_x = 0.5 + 0.4 * math.sin(t * speed * 2 * math.pi)
                self._target_y = p.get("y", 0.5)
            elif self._preset_mode == "zigzag":
                speed = p.get("speed", 0.5)
                rows = p.get("rows", 3)
                period = 1.0 / speed
                row_idx = int((t % (period * rows)) / period)
                row_t = (t % period) / period
                self._target_x = row_t if row_idx % 2 == 0 else 1.0 - row_t
                self._target_y = row_idx / max(1, rows - 1)

        # 弹性回中
        if self._spring and not self._dragging and not self._preset_mode:
            self._target_x = 0.5
            self._target_y = 0.5

        # 物理阻尼
        damp = 0.18
        dx = self._target_x - self._x
        dy = self._target_y - self._y
        if abs(dx) > 0.001 or abs(dy) > 0.001:
            self._x += dx * damp
            self._y += dy * damp
            self._x = max(0.0, min(1.0, self._x))
            self._y = max(0.0, min(1.0, self._y))
            self._emit_xy()
            self.update()

    # ── 鼠标事件 ──────────────────────────────────────────────────────────────
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._preset_mode = None
            self._update_target(event.position())

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging:
            self._update_target(event.position())

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._dragging = False

    def _update_target(self, pos: QPointF):
        w, h = self.width(), self.height()
        self._target_x = max(0.0, min(1.0, pos.x() / w))
        self._target_y = max(0.0, min(1.0, pos.y() / h))

    # ── 绘制 ──────────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # 背景
        p.fillRect(0, 0, w, h, QColor(BG_CTRL))
        p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1))
        p.drawRect(0, 0, w - 1, h - 1)

        # 网格线
        p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1))
        p.drawLine(w // 2, 0, w // 2, h)
        p.drawLine(0, h // 2, w, h // 2)

        # 同心圆（参考）
        p.setPen(QPen(QColor(_th.BORDER_NORMAL), 1))
        for r in [0.25, 0.5]:
            cx, cy = w // 2, h // 2
            rr = int(r * min(w, h))
            p.drawEllipse(cx - rr, cy - rr, rr * 2, rr * 2)

        # 轨迹预设路径预览
        if self._preset_mode:
            path = QPainterPath()
            steps = 60
            for i in range(steps + 1):
                t = i / steps * (2 * math.pi)
                p_params = self._preset_params
                if self._preset_mode == "circle":
                    speed = p_params.get("speed", 1.0)
                    radius = p_params.get("radius", 0.4)
                    cx2, cy2 = p_params.get("cx", 0.5), p_params.get("cy", 0.5)
                    # speed 应用于角速度：速度越快则圆弧越少（预览展示当前周期内的圆弧）
                    arc_end = min(2 * math.pi, 2 * math.pi / max(0.1, speed))
                    t_scaled = i / steps * arc_end
                    px = (cx2 + radius * math.cos(t_scaled)) * w
                    py = (cy2 + radius * math.sin(t_scaled)) * h
                elif self._preset_mode == "figure8":
                    rx = p_params.get("rx", 0.35)
                    ry = p_params.get("ry", 0.2)
                    px = (0.5 + rx * math.sin(t)) * w
                    py = (0.5 + ry * math.sin(2 * t)) * h
                else:
                    continue
                if i == 0:
                    path.moveTo(px, py)
                else:
                    path.lineTo(px, py)
            _acc = QColor(FG_ACCENT); _acc.setAlpha(68)   # 等价旧 "44" 透明度
            p.setPen(QPen(_acc, 1))
            p.drawPath(path)

        # 当前位置
        cx = int(self._x * w)
        cy = int(self._y * h)
        p.setPen(QPen(self._color_x, 2))
        _pos = QColor(FG_ACCENT); _pos.setAlpha(85)       # 等价旧 "55" 透明度
        p.setBrush(QBrush(_pos))
        p.drawEllipse(cx - 12, cy - 12, 24, 24)
        p.setPen(QPen(QColor(FG_ACCENT), 1))
        p.drawLine(cx - 6, cy, cx + 6, cy)
        p.drawLine(cx, cy - 6, cx, cy + 6)

        # 轴标签
        p.setPen(QPen(self._color_x))
        font = QFont("Consolas", 7)
        p.setFont(font)
        p.drawText(w - 24, h - 12, 24, 12,
                   Qt.AlignmentFlag.AlignRight, self._ch_x[-1].upper())
        p.setPen(QPen(self._color_y))
        p.drawText(2, 2, 24, 12,
                   Qt.AlignmentFlag.AlignLeft, self._ch_y[-1].upper())

        # 音频驱动指示
        if self._audio_driven:
            p.setPen(QPen(QColor(FG_CYAN), 1))
            p.drawText(0, 0, w, 14, Qt.AlignmentFlag.AlignHCenter, "♪ 音频驱动")
        p.end()


# ══════════════════════════════════════════════════════════════════════════════
#  AudioDriver — 音频分析通用驱动器
# ══════════════════════════════════════════════════════════════════════════════
class _BandBar(QWidget):
    """音频频段电平条：paintEvent 直接绘制，替代高频回调拼 QSS。

    ★ 性能契约（restyle_spec §5）：电平条处于 20fps×5 条的高频回调路径上，
      禁止每帧 setStyleSheet 重解析；此处仅更新浮点成员并 update()。
    """
    BAR_W, BAR_H = 20, 40

    def __init__(self, band: str, color_hex: str, parent=None):
        super().__init__(parent)
        self._band = band
        self._color = QColor(color_hex)
        self._level = 0.0
        self.setFixedSize(self.BAR_W, self.BAR_H)
        self.setToolTip(band)

    def set_level(self, v: float):
        # 电平数值计算逻辑不动：只做显示钳制
        self._level = max(0.0, min(1.0, float(v)))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        rest = QColor(BG_CTRL)
        p.fillRect(0, 0, w, h, rest)
        lvl = self._level
        if lvl > 0.001:
            c = self._color
            fill_h = max(2, int(lvl * (h - 2)))
            stop = max(0.01, min(0.99, fill_h / float(h)))   # 渐变 stop 与旧公式一致
            grad = QLinearGradient(0, h, 0, 0)               # 自下而上
            _cc = QColor(c); _cc.setAlpha(204)               # 等价旧 'CC' 段
            grad.setColorAt(0.0, _cc)
            grad.setColorAt(stop, c)
            grad.setColorAt(min(stop + 0.01, 1.0), rest)
            grad.setColorAt(1.0, rest)
            p.fillRect(1, 1, w - 2, h - 2, QBrush(grad))
        border = QColor(self._color); border.setAlpha(102)   # 等价旧 '66' 描边
        p.setPen(QPen(border, 1))
        p.drawRect(0, 0, w - 1, h - 1)
        p.end()


class AudioDriver(QWidget):
    """
    音频分析通用驱动器
    - 实时分析音频输入（麦克风/文件）
    - 提取多个频段能量：bass/mid/high/presence/brilliance
    - 检测节拍（BPM）
    - 通过信号驱动任意控制器（推杆/滑块/摇杆）
    - 支持灵敏度、平滑度、频段绑定配置
    """
    # 频段能量信号（0.0-1.0）
    band_changed    = pyqtSignal(str, float)  # band_name, value

    BANDS = ["bass", "mid", "high", "presence", "brilliance"]
    # 频段五色（红→黄→绿→青的频率语义序列，受保护：保 hue 不动，audit C 汇总 C.7）
    BAND_BAR_COLORS = {
        "bass":       "#FF5555",
        "mid":        "#FFAA44",
        "high":       "#FFDD88",
        "presence":   "#5a9e6f",
        "brilliance": "#44CCFF",
    }
    BAND_RANGES = {
        "bass":       (20,   250),
        "mid":        (250,  2000),
        "high":       (2000, 8000),
        "presence":   (2000, 4000),
        "brilliance": (6000, 20000),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = False
        self._sensitivity = 1.0
        self._smoothing = 0.7
        self._bpm = 120.0
        self._band_values: Dict[str, float] = {b: 0.0 for b in self.BANDS}
        self._band_smooth: Dict[str, float] = {b: 0.0 for b in self.BANDS}
        self._beat_history: List[float] = []
        self._audio_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._sim_t = 0.0
        self._sim_timer = QTimer(self)
        self._sim_timer.setInterval(50)
        self._sim_timer.timeout.connect(self._simulate_audio)
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        # 标题行
        title_row = QHBoxLayout()
        title_lbl = QLabel("♪ 音频驱动")
        title_lbl.setStyleSheet(f"color:{FG_CYAN};font-size:11px;font-weight:bold;")
        title_row.addWidget(title_lbl)
        title_row.addStretch()

        self._active_btn = QPushButton("启动")
        self._active_btn.setCheckable(True)
        self._active_btn.setFixedSize(50, 22)
        self._active_btn.setStyleSheet(
            f"QPushButton{{background:#1A2A1A;color:{FG_GREEN};"
            "border:1px solid rgba(136,255,136,0.27);border-radius:3px;font-size:10px;}"
            "QPushButton:checked{background:rgba(90,158,111,0.20);border-color:#56B65B;}"  # checked 描边→bright_success 档（原 #5a9e6f）
            "QPushButton:hover{background:rgba(136,255,136,0.13);}")
        self._active_btn.toggled.connect(self._on_active_toggled)
        title_row.addWidget(self._active_btn)
        outer.addLayout(title_row)

        # 频段可视化（电平条为自绘 _BandBar，禁止高频拼 QSS）
        self._band_bars: Dict[str, QWidget] = {}
        self._band_colors: Dict[str, str] = {}  # 存储每个频段的颜色
        bar_row = QHBoxLayout()
        bar_row.setSpacing(2)
        for band in self.BANDS:
            col = self.BAND_BAR_COLORS.get(band, "#999999")  # 兜底灰→text_secondary 档（原 #888888）
            self._band_colors[band] = col
            bar = _BandBar(band, col)
            self._band_bars[band] = bar
            bar_row.addWidget(bar)
        bar_row.addStretch()
        outer.addLayout(bar_row)

        # 参数行
        param_row = QHBoxLayout()
        param_row.setSpacing(6)

        param_row.addWidget(QLabel("灵敏度:"))
        self._sens_spin = QDoubleSpinBox()
        self._sens_spin.setRange(0.1, 5.0)
        self._sens_spin.setValue(1.0)
        self._sens_spin.setSingleStep(0.1)
        self._sens_spin.setFixedWidth(60)
        # 基础外观交由全局主题（原行内 QDoubleSpinBox 样式已删除）
        self._sens_spin.valueChanged.connect(lambda v: setattr(self, "_sensitivity", v))
        param_row.addWidget(self._sens_spin)

        param_row.addWidget(QLabel("BPM:"))
        self._bpm_spin = QDoubleSpinBox()
        self._bpm_spin.setRange(30.0, 300.0)
        self._bpm_spin.setValue(120.0)
        self._bpm_spin.setSingleStep(1.0)
        self._bpm_spin.setFixedWidth(70)
        # 基础外观交由全局主题（原行内 QDoubleSpinBox 样式已删除）
        self._bpm_spin.valueChanged.connect(lambda v: setattr(self, "_bpm", v))
        param_row.addWidget(self._bpm_spin)

        param_row.addStretch()
        outer.addLayout(param_row)

        # 标签样式
        for lbl in self.findChildren(QLabel):
            if not lbl.styleSheet():
                lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")

    def _on_active_toggled(self, active: bool):
        self._active = active
        self._active_btn.setText("停止" if active else "启动")
        if active:
            self._sim_timer.start()
        else:
            self._sim_timer.stop()
            for band in self.BANDS:
                self._band_values[band] = 0.0
                self._band_smooth[band] = 0.0
                self._update_bar(band, 0.0)

    def _simulate_audio(self):
        """模拟音频分析（无真实音频输入时使用 BPM 驱动）"""
        self._sim_t += 0.05
        t = self._sim_t
        bpm = self._bpm
        beat_period = 60.0 / bpm

        # 模拟各频段
        beat_phase = (t % beat_period) / beat_period
        beat_pulse = math.exp(-beat_phase * 8.0)  # 节拍后快速衰减

        raw = {
            "bass":       beat_pulse * 0.9 + 0.1 * abs(math.sin(t * 2.3)),
            "mid":        0.3 + 0.4 * abs(math.sin(t * 3.7 + 0.5)),
            "high":       0.1 + 0.2 * abs(math.sin(t * 7.1 + 1.2)),
            "presence":   0.2 + 0.3 * abs(math.sin(t * 5.3 + 0.8)),
            "brilliance": 0.05 + 0.15 * abs(math.sin(t * 11.0 + 2.1)),
        }

        for band, val in raw.items():
            val = min(1.0, val * self._sensitivity)
            smooth = self._band_smooth[band]
            smooth = smooth * self._smoothing + val * (1 - self._smoothing)
            self._band_smooth[band] = smooth
            self._band_values[band] = smooth
            self._update_bar(band, smooth)
            self.band_changed.emit(band, smooth)

    def _update_bar(self, band: str, value: float):
        bar = self._band_bars.get(band)
        if bar is not None:
            # 高频回调只更新数据 + 触发重绘，绝不拼 QSS（见 _BandBar 说明）
            bar.set_level(value)

    def get_band_value(self, band: str) -> float:
        return self._band_values.get(band, 0.0)

    def is_active(self) -> bool:
        return self._active

    def bind_to_fader(self, fader: UniversalFader, band: str = "bass"):
        """将指定频段绑定到推杆"""
        self.band_changed.connect(
            lambda b, v, f=fader, bd=band: f.inject_audio_value(v) if b == bd else None)
        fader.set_audio_driven(True)

    def bind_to_slider(self, slider: UniversalSlider, band: str = "mid"):
        self.band_changed.connect(
            lambda b, v, s=slider, bd=band: s.inject_audio_value(v) if b == bd else None)
        slider.set_audio_driven(True)

    def bind_to_joystick(self, joy: UniversalJoystick,
                         x_band: str = "bass", y_band: str = "high"):
        def _on_band(band, val, j=joy, xb=x_band, yb=y_band):
            if band == xb:
                j.inject_audio_xy(val, j.get_position()[1])
            elif band == yb:
                j.inject_audio_xy(j.get_position()[0], val)
        self.band_changed.connect(_on_band)
        joy.set_audio_driven(True)

    def unbind_fader(self, fader: UniversalFader):
        fader.set_audio_driven(False)

    def unbind_joystick(self, joy: UniversalJoystick):
        joy.set_audio_driven(False)


# ══════════════════════════════════════════════════════════════════════════════
#  MappingEditor — 映射规则编辑器
# ══════════════════════════════════════════════════════════════════════════════
class MappingEditor(QWidget):
    """映射规则编辑器（嵌入通道类型行的展开区域）"""
    mapping_changed = pyqtSignal(object)  # ChannelMapping

    def __init__(self, mapping: ChannelMapping, parent=None):
        super().__init__(parent)
        self._mapping = mapping
        self._build_ui()

    def _build_ui(self):
        lay = QGridLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        style_lbl = f"color:{FG_DIM};font-size:10px;"
        # 控件基础外观交由全局主题（原 style_ctrl 重复定义已删除）

        lay.addWidget(self._lbl("映射类型:", style_lbl), 0, 0)
        self._type_combo = QComboBox()
        self._type_combo.addItems(["linear", "gamma", "s_curve", "stepped", "inverted"])
        self._type_combo.setCurrentText(self._mapping.map_type)
        self._type_combo.currentTextChanged.connect(self._on_type_changed)
        lay.addWidget(self._type_combo, 0, 1)

        lay.addWidget(self._lbl("最小值:", style_lbl), 1, 0)
        self._lo_spin = QSpinBox()
        self._lo_spin.setRange(0, 255)
        self._lo_spin.setValue(self._mapping.lo)
        self._lo_spin.valueChanged.connect(lambda v: self._update("lo", v))
        lay.addWidget(self._lo_spin, 1, 1)

        lay.addWidget(self._lbl("最大值:", style_lbl), 2, 0)
        self._hi_spin = QSpinBox()
        self._hi_spin.setRange(0, 255)
        self._hi_spin.setValue(self._mapping.hi)
        self._hi_spin.valueChanged.connect(lambda v: self._update("hi", v))
        lay.addWidget(self._hi_spin, 2, 1)

        self._gamma_lbl = self._lbl("Gamma:", style_lbl)
        lay.addWidget(self._gamma_lbl, 3, 0)
        self._gamma_spin = QDoubleSpinBox()
        self._gamma_spin.setRange(0.1, 5.0)
        self._gamma_spin.setValue(self._mapping.gamma)
        self._gamma_spin.setSingleStep(0.1)
        self._gamma_spin.valueChanged.connect(lambda v: self._update("gamma", v))
        lay.addWidget(self._gamma_spin, 3, 1)

        self._steps_lbl = self._lbl("阶梯数:", style_lbl)
        lay.addWidget(self._steps_lbl, 4, 0)
        self._steps_spin = QSpinBox()
        self._steps_spin.setRange(2, 32)
        self._steps_spin.setValue(self._mapping.steps)
        self._steps_spin.valueChanged.connect(lambda v: self._update("steps", v))
        lay.addWidget(self._steps_spin, 4, 1)

        self._on_type_changed(self._mapping.map_type)

    def _lbl(self, text, style):
        l = QLabel(text)
        l.setStyleSheet(style)
        return l

    def _on_type_changed(self, t: str):
        self._mapping.map_type = t
        show_gamma = t == "gamma"
        show_steps = t == "stepped"
        self._gamma_lbl.setVisible(show_gamma)
        self._gamma_spin.setVisible(show_gamma)
        self._steps_lbl.setVisible(show_steps)
        self._steps_spin.setVisible(show_steps)
        self.mapping_changed.emit(self._mapping)

    def _update(self, attr: str, val):
        setattr(self._mapping, attr, val)
        self.mapping_changed.emit(self._mapping)

    def get_mapping(self) -> ChannelMapping:
        return self._mapping


# ══════════════════════════════════════════════════════════════════════════════
#  ControllerPanel — 通道类型行的通用控制器面板
# ══════════════════════════════════════════════════════════════════════════════
class ControllerPanel(QWidget):
    """
    通用控制器面板，嵌入 ChannelTypeRow 的展开区域
    根据通道类型自动选择最合适的控制器组合：
      - dimmer/valve/pump/W → 推杆（UniversalFader）
      - R/G/B → 推杆组 + 拾色器
      - motor_x/motor_y → 摇杆（UniversalJoystick）
      - motor_z → 滑块（UniversalSlider）
      - 所有类型 → 可选音频驱动
    """
    value_changed = pyqtSignal(str, int)   # (ch_type, dmx_value)

    def __init__(self, ch_types: List[str], parent=None):
        super().__init__(parent)
        self._ch_types = ch_types
        self._faders: Dict[str, UniversalFader] = {}
        self._sliders: Dict[str, UniversalSlider] = {}
        self._joystick: Optional[UniversalJoystick] = None
        self._audio_driver: Optional[AudioDriver] = None
        self._mappings: Dict[str, ChannelMapping] = {
            ct: ChannelMapping(ch_type=ct) for ct in ch_types
        }
        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet(f"background:{BG_PANEL};")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(6)

        # Tab 区域：手动控制 / 音频驱动 / 映射设置
        tabs = QTabWidget()
        # QTabWidget::pane / QTabBar::tab 基础外观交由全局主题（原重复定义已删除）

        # ── Tab 1：手动控制 ──────────────────────────────────────────────────
        manual_tab = QWidget()
        manual_tab.setStyleSheet(f"background:{BG_PANEL};")
        manual_lay = QVBoxLayout(manual_tab)
        manual_lay.setContentsMargins(4, 4, 4, 4)
        manual_lay.setSpacing(4)

        has_xy = "motor_x" in self._ch_types and "motor_y" in self._ch_types
        has_z = "motor_z" in self._ch_types
        fader_types = [ct for ct in self._ch_types
                       if ct not in ("motor_x", "motor_y")]

        if has_xy:
            joy_row = QHBoxLayout()
            self._joystick = UniversalJoystick(
                ch_x="motor_x", ch_y="motor_y",
                mapping_x=self._mappings.get("motor_x"),
                mapping_y=self._mappings.get("motor_y"),
            )
            self._joystick.xy_dmx_changed.connect(self._on_xy_changed)
            joy_row.addWidget(self._joystick)

            # 摇杆控制选项
            joy_ctrl = QVBoxLayout()
            spring_chk = QCheckBox("弹性回中")
            spring_chk.setChecked(True)
            spring_chk.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
            spring_chk.toggled.connect(self._joystick.set_spring)
            joy_ctrl.addWidget(spring_chk)

            lock_chk = QCheckBox("锁定位置")
            lock_chk.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
            lock_chk.toggled.connect(self._joystick.set_locked)
            joy_ctrl.addWidget(lock_chk)

            # 轨迹预设
            traj_lbl = QLabel("轨迹预设:")
            traj_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
            joy_ctrl.addWidget(traj_lbl)
            traj_combo = QComboBox()
            traj_combo.addItems(["无", "圆形", "8字形", "扫描", "锯齿"])
            # 基础外观交由全局主题（原行内 QComboBox 样式已删除）
            traj_combo.currentTextChanged.connect(self._on_traj_changed)
            joy_ctrl.addWidget(traj_combo)

            # 轨迹速度
            speed_lbl = QLabel("速度:")
            speed_lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
            joy_ctrl.addWidget(speed_lbl)
            self._traj_speed = QDoubleSpinBox()
            self._traj_speed.setRange(0.1, 5.0)
            self._traj_speed.setValue(1.0)
            self._traj_speed.setSingleStep(0.1)
            # 基础外观交由全局主题（原行内 QDoubleSpinBox 样式已删除）
            self._traj_speed.valueChanged.connect(self._on_traj_speed_changed)
            joy_ctrl.addWidget(self._traj_speed)
            joy_ctrl.addStretch()

            joy_row.addLayout(joy_ctrl)
            manual_lay.addLayout(joy_row)

        if fader_types:
            fader_row = QHBoxLayout()
            fader_row.setSpacing(4)
            for ct in fader_types:
                fader = UniversalFader(
                    ch_type=ct,
                    mapping=self._mappings.get(ct),
                    label=ct[:4].upper(),
                )
                fader.value_changed.connect(self.value_changed)
                self._faders[ct] = fader
                fader_row.addWidget(fader)
            fader_row.addStretch()
            manual_lay.addLayout(fader_row)

        if has_z:
            slider = UniversalSlider(
                ch_type="motor_z",
                mapping=self._mappings.get("motor_z"),
                label="Z轴",
            )
            slider.value_changed.connect(self.value_changed)
            self._sliders["motor_z"] = slider
            manual_lay.addWidget(slider)

        manual_lay.addStretch()
        tabs.addTab(manual_tab, "手动控制")

        # ── Tab 2：音频驱动 ──────────────────────────────────────────────────
        audio_tab = QWidget()
        audio_tab.setStyleSheet(f"background:{BG_PANEL};")
        audio_lay = QVBoxLayout(audio_tab)
        audio_lay.setContentsMargins(4, 4, 4, 4)
        audio_lay.setSpacing(4)

        self._audio_driver = AudioDriver()
        audio_lay.addWidget(self._audio_driver)

        # 绑定配置
        bind_grp = QGroupBox("频段绑定")
        # 基础外观交由全局主题（原行内 QGroupBox 样式已删除）
        bind_lay = QGridLayout(bind_grp)
        bind_lay.setSpacing(4)

        self._bind_combos: Dict[str, QComboBox] = {}
        self._bind_checks: Dict[str, QCheckBox] = {}
        band_options = ["(不绑定)"] + AudioDriver.BANDS

        for row_i, ct in enumerate(self._ch_types):
            c = CH_COLORS.get(ct, FG_DIM)
            lbl = QLabel(ct.upper())
            lbl.setStyleSheet(f"color:{c};font-size:10px;font-weight:bold;")
            bind_lay.addWidget(lbl, row_i, 0)

            combo = QComboBox()
            combo.addItems(band_options)
            # 默认绑定
            default = {"dimmer": "bass", "R": "bass", "G": "mid", "B": "high",
                       "W": "mid", "valve": "bass", "pump": "mid",
                       "motor_x": "bass", "motor_y": "high", "motor_z": "mid"}
            default_band = default.get(ct, "(不绑定)")
            if default_band in band_options:
                combo.setCurrentText(default_band)
            # 基础外观交由全局主题（原行内 QComboBox 样式已删除）
            combo.currentTextChanged.connect(
                lambda band, c_=ct: self._on_bind_changed(c_, band))
            self._bind_combos[ct] = combo
            bind_lay.addWidget(combo, row_i, 1)

        audio_lay.addWidget(bind_grp)

        # 连接音频驱动信号
        self._audio_driver.band_changed.connect(self._on_audio_band)
        audio_lay.addStretch()
        tabs.addTab(audio_tab, "♪ 音频驱动")

        # ── Tab 3：映射设置 ──────────────────────────────────────────────────
        map_tab = QScrollArea()
        map_tab.setWidgetResizable(True)
        map_tab.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        map_content = QWidget()
        map_content.setStyleSheet(f"background:{BG_PANEL};")
        map_lay = QVBoxLayout(map_content)
        map_lay.setContentsMargins(4, 4, 4, 4)
        map_lay.setSpacing(8)

        for ct in self._ch_types:
            c = CH_COLORS.get(ct, FG_DIM)
            grp = QGroupBox(f"{ct.upper()} 映射规则")
            # 类别色专属描边（语义特化，按通道类别取色）
            grp.setStyleSheet(
                f"QGroupBox{{border:1px solid {_hex_alpha(c, '44')};"
                f"margin-top:8px;font-size:10px;}}")
            editor = MappingEditor(self._mappings[ct])
            editor.mapping_changed.connect(
                lambda m, c_=ct: self._on_mapping_changed(c_, m))
            grp_lay = QVBoxLayout(grp)
            grp_lay.addWidget(editor)
            map_lay.addWidget(grp)

        map_lay.addStretch()
        map_tab.setWidget(map_content)
        tabs.addTab(map_tab, "映射设置")

        self._tabs = tabs   # 持有引用，防止 GC
        outer.addWidget(tabs)

    def _on_xy_changed(self, ch_x: str, vx: int, ch_y: str, vy: int):
        self.value_changed.emit(ch_x, vx)
        self.value_changed.emit(ch_y, vy)

    def _on_traj_changed(self, name: str):
        if not self._joystick:
            return
        mode_map = {"无": None, "圆形": "circle", "8字形": "figure8",
                    "扫描": "scan", "锯齿": "zigzag"}
        mode = mode_map.get(name)
        speed = self._traj_speed.value() if hasattr(self, "_traj_speed") else 1.0
        params = {"speed": speed}
        self._joystick.set_preset_mode(mode, params)

    def _on_traj_speed_changed(self, speed: float):
        if self._joystick and self._joystick._preset_mode:
            self._joystick._preset_params["speed"] = speed

    def _on_audio_band(self, band: str, value: float):
        """音频频段值 → 绑定的控制器"""
        for ct, combo in self._bind_combos.items():
            if combo.currentText() == band:
                dmx = self._mappings[ct].apply_audio(value)
                # 更新对应控制器
                if ct in self._faders:
                    self._faders[ct].inject_audio_value(value)
                elif ct in self._sliders:
                    self._sliders[ct].inject_audio_value(value)
                elif self._joystick:
                    if ct == self._joystick.ch_x:
                        self._joystick.inject_audio_xy(
                            value, self._joystick.get_position()[1])
                    elif ct == self._joystick.ch_y:
                        self._joystick.inject_audio_xy(
                            self._joystick.get_position()[0], value)
                self.value_changed.emit(ct, dmx)

    def _on_bind_changed(self, ch_type: str, band: str):
        fader = self._faders.get(ch_type)
        slider = self._sliders.get(ch_type)
        driven = band != "(不绑定)"
        if fader:
            fader.set_audio_driven(driven)
        if slider:
            slider.set_audio_driven(driven)

    def _on_mapping_changed(self, ch_type: str, mapping: ChannelMapping):
        self._mappings[ch_type] = mapping
        if ch_type in self._faders:
            self._faders[ch_type].set_mapping(mapping)
        if ch_type in self._sliders:
            self._sliders[ch_type].set_mapping(mapping)

    def get_values(self) -> Dict[str, int]:
        """获取所有通道当前 DMX 值"""
        result = {}
        for ct, fader in self._faders.items():
            result[ct] = fader.get_dmx_value()
        for ct, slider in self._sliders.items():
            result[ct] = slider.get_dmx_value()
        if self._joystick:
            x, y = self._joystick.get_position()
            result[self._joystick.ch_x] = self._joystick._mapping_x.apply(x)
            result[self._joystick.ch_y] = self._joystick._mapping_y.apply(y)
        return result

    def set_values(self, values: Dict[str, int]):
        """从外部设置通道值（如加载 Cue）"""
        for ct, val in values.items():
            if ct in self._faders:
                self._faders[ct].set_dmx_value(val, emit=False)
            if ct in self._sliders:
                self._sliders[ct].set_dmx_value(val, emit=False)
            if self._joystick:
                if ct == self._joystick.ch_x:
                    m = self._joystick._mapping_x
                    n = (val - m.lo) / max(1, m.hi - m.lo)
                    self._joystick.set_position(n, self._joystick.get_position()[1], False)
                elif ct == self._joystick.ch_y:
                    m = self._joystick._mapping_y
                    n = (val - m.lo) / max(1, m.hi - m.lo)
                    self._joystick.set_position(self._joystick.get_position()[0], n, False)

    def clear(self):
        """清除所有值"""
        for fader in self._faders.values():
            fader.set_normalized(0.0, emit=False)
            fader.update()
        for slider in self._sliders.values():
            slider.set_normalized(0.0, emit=False)
            slider.update()
        if self._joystick:
            self._joystick.set_position(0.5, 0.5, emit=False)
