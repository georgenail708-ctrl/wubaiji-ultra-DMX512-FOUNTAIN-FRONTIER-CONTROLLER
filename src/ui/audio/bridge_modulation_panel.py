"""
bridge_modulation_panel.py — 音频桥接器·自动调制子页面 (v2)
=============================================================
设计思路：
  - 音频来源统一为三个维度：速度（BPM/节拍）、强度（RMS/能量）、复杂度（织体/频谱变化）
  - 调制目标统一为三个参数：
      1. 叠加速度  — 在效果当前速度基础上的即时微调幅度（speed_delta）
      2. 动作幅度  — 效果动作的最大/最小值范围（amplitude_delta）
      3. 密度      — 效果相位差（phase_step_delta）
  - 每个目标参数均有：
      · 启用开关
      · 音频来源选择（速度/强度/复杂度）
      · 叠加幅度（overlay_amount）：最大叠加量
      · 调制范围（mod_range_lo / mod_range_hi）：叠加后参数的上下限约束
      · 平滑系数（smooth）：防止参数突变
      · 响应曲线（linear / exp / log / step）
  - 识别参数（精度/速度/范围/基线/模式）参考 programmer 的 AudioDriverWidget
  - 支持独立启用/关闭，不影响桥接器主逻辑
  - 每帧通过 on_analysis_tick 接收 MusicStructureEvent，计算叠加值后
    通过 overlay_params_updated 信号发射给 AudioBridgePanel
"""
from __future__ import annotations

import math
from typing import Dict, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QLinearGradient
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSlider, QCheckBox, QComboBox,
    QFrame, QDoubleSpinBox, QGridLayout,
)

from .. import qascade_theme as _th

# ─── 颜色常量（与 audio_bridge_panel 保持一致，引用主题令牌）──────────────
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
FG_PINK   = "#FF88CC"  # wings_delta 专属识别色，主题令牌无对应色阶，保留原值

# ─── 样式表（通用外观交给全局主题接管，此处只留语义色特化）───────────────────
# 已删除 _COMBO：与全局 QComboBox 外观完全重复（§3 结构性统一）。
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

# ─── 音频来源维度 ─────────────────────────────────────────────────────────────
AUDIO_DIM_LABELS = {
    "speed":      "速度（BPM/节拍）",
    "intensity":  "强度（RMS/能量）",
    "complexity": "复杂度（织体/变化率）",
}

# ─── 调制目标参数定义 ─────────────────────────────────────────────────────────
TARGET_PARAMS = [
    {
        "key":            "speed_delta",
        "label":          "叠加速度",
        "desc":           "在效果当前速度上即时微调的叠加幅度（相对量±）",
        "color":          FG_GREEN,
        "slider_style":   _SLIDER_GREEN,
        "default_amount": 0.5,
        "default_lo":    -1.0,
        "default_hi":     1.0,
    },
    {
        "key":            "amplitude_delta",
        "label":          "动作幅度",
        "desc":           "效果动作幅度的叠加量（相对量±）",
        "color":          FG_ORANGE,
        "slider_style":   _SLIDER_ORANGE,
        "default_amount": 0.4,
        "default_lo":    -0.5,
        "default_hi":     0.5,
    },
    {
        "key":            "phase_step_delta",
        "label":          "密度（相位差）",
        "desc":           "效果相位差的叠加量，影响多设备间的相位分布（相对量±）",
        "color":          FG_CYAN,
        "slider_style":   _SLIDER_CYAN,
        "default_amount": 0.3,
        "default_lo":    -0.3,
        "default_hi":     0.3,
    },
    {
        "key":            "wings_delta",
        "label":          "翼数量调制",
        "desc":           "按音频强弱对当前效果的 wings（翼）参数做增量调制；翼表示重复的同形波形数量（如 2 翼=两段完全一致的同向波形），范围决定可增加/减少的翼数。",
        "color":          FG_PINK if 'FG_PINK' in globals() else FG_ACCENT,
        "slider_style":   _SLIDER_ORANGE,
        "default_amount": 1.0,
        "default_lo":    -4.0,
        "default_hi":     4.0,
        "range_min":     -32.0,
        "range_max":      32.0,
        "amount_max":     2.0,
        "integer":        True,
    },
    {
        "key":            "block_size_delta",
        "label":          "块大小调制",
        "desc":           "按音频强弱对当前效果的 block_size（块）参数做增量调制；范围决定可增加/减少的块大小，灵敏度由叠加幅度控制。",
        "color":          FG_ACCENT,
        "slider_style":   _SLIDER_CYAN,
        "default_amount": 1.0,
        "default_lo":    -8.0,
        "default_hi":     8.0,
        "range_min":     -64.0,
        "range_max":      64.0,
        "amount_max":     2.0,
        "integer":        True,
    },
]


# ─────────────────────────────────────────────────────────────────────────────
#  实时电平条
# ─────────────────────────────────────────────────────────────────────────────
class _DimMeterBar(QWidget):
    """单个音频维度的实时电平条"""
    def __init__(self, color: str = FG_GREEN, parent=None):
        super().__init__(parent)
        self._value = 0.0
        self._color = QColor(color)
        self.setFixedHeight(8)
        self.setMinimumWidth(80)

    def set_value(self, v: float):
        self._value = max(0.0, min(1.0, float(v)))
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor("#303030"))
        fill_w = int(w * self._value)
        if fill_w > 0:
            grad = QLinearGradient(0, 0, w, 0)
            c = self._color
            grad.setColorAt(0.0, QColor(c.red() // 2, c.green() // 2, c.blue() // 2))
            grad.setColorAt(1.0, c)
            p.fillRect(0, 0, fill_w, h, grad)
        # 精修：外圈 1px 发丝线（rgba(255,255,255,21)），电平语义与渐变保持不变
        p.setPen(QColor(255, 255, 255, 21))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(0, 0, w - 1, h - 1)
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
#  单个调制目标参数的控制卡片
# ─────────────────────────────────────────────────────────────────────────────
class _TargetParamCard(QFrame):
    """
    单个调制目标参数的控制卡片。
    包含：启用开关、音频来源选择、叠加幅度、调制范围（上下限）、平滑、曲线
    """
    changed = pyqtSignal()

    def __init__(self, param_def: dict, parent=None):
        super().__init__(parent)
        self._param_def = param_def
        self._smooth_state = 0.0
        self.setStyleSheet(
            f"QFrame{{background:{BG_CARD};border:1px solid #303030;"
            f"border-radius:4px;}}"
        )
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        pd = self._param_def
        color = pd["color"]
        sty = pd["slider_style"]

        # ── 标题行：启用开关 + 参数名 + 实时输出值 ──
        title_row = QHBoxLayout()
        title_row.setSpacing(8)

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

        # 实时电平条
        self._meter = _DimMeterBar(color)
        lay.addWidget(self._meter)

        # ── 参数网格 ──
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)

        def _mk_lbl(text, c=FG_DIM):
            l = QLabel(text)
            l.setStyleSheet(
                f"color:{c};font-size:10px;background:transparent;border:none;"
            )
            l.setMinimumWidth(70)
            return l

        def _mk_val_lbl(text, c=FG_ACCENT):
            l = QLabel(text)
            l.setStyleSheet(
                f"color:{c};font-size:10px;min-width:45px;"
                f"background:transparent;border:none;"
            )
            return l

        # 行0：音频来源 + 响应曲线
        grid.addWidget(_mk_lbl("音频来源:"), 0, 0)
        self._combo_source = QComboBox()
        self._combo_source.setFixedWidth(150)
        for key, label in AUDIO_DIM_LABELS.items():
            self._combo_source.addItem(label, key)
        self._combo_source.currentIndexChanged.connect(self.changed)
        grid.addWidget(self._combo_source, 0, 1)

        grid.addWidget(_mk_lbl("曲线:"), 0, 2)
        self._combo_curve = QComboBox()
        self._combo_curve.setFixedWidth(80)
        for key, label in [("linear", "线性"), ("exp", "指数"),
                            ("log", "对数"), ("step", "阶梯")]:
            self._combo_curve.addItem(label, key)
        self._combo_curve.currentIndexChanged.connect(self.changed)
        grid.addWidget(self._combo_curve, 0, 3)

        # 行1：叠加幅度 / 灵敏度
        grid.addWidget(_mk_lbl("灵敏度/幅度:", color), 1, 0)
        self._amount_scale = 100
        self._range_scale = 100
        self._sld_amount = QSlider(Qt.Orientation.Horizontal)
        self._sld_amount.setRange(0, int(float(pd.get("amount_max", 2.0)) * self._amount_scale))
        self._sld_amount.setValue(int(pd["default_amount"] * self._amount_scale))
        self._sld_amount.setStyleSheet(sty)
        self._sld_amount.setMinimumWidth(120)
        self._lbl_amount = _mk_val_lbl(f"{pd['default_amount']:.2f}", color)
        self._sld_amount.valueChanged.connect(
            lambda v: self._lbl_amount.setText(f"{v/self._amount_scale:.2f}"))
        self._sld_amount.valueChanged.connect(lambda _: self.changed.emit())
        grid.addWidget(self._sld_amount, 1, 1)
        grid.addWidget(self._lbl_amount, 1, 2)

        # 行2：范围下限
        grid.addWidget(_mk_lbl("范围下限:"), 2, 0)
        self._sld_lo = QSlider(Qt.Orientation.Horizontal)
        self._sld_lo.setRange(int(float(pd.get("range_min", -2.0)) * self._range_scale), 0)
        self._sld_lo.setValue(int(pd["default_lo"] * self._range_scale))
        self._sld_lo.setStyleSheet(_SLIDER)
        self._sld_lo.setMinimumWidth(120)
        self._lbl_lo = _mk_val_lbl(f"{pd['default_lo']:.2f}", FG_DIM)
        self._sld_lo.valueChanged.connect(
            lambda v: self._lbl_lo.setText(f"{v/self._range_scale:.2f}"))
        self._sld_lo.valueChanged.connect(lambda _: self.changed.emit())
        grid.addWidget(self._sld_lo, 2, 1)
        grid.addWidget(self._lbl_lo, 2, 2)

        # 行3：范围上限
        grid.addWidget(_mk_lbl("范围上限:"), 3, 0)
        self._sld_hi = QSlider(Qt.Orientation.Horizontal)
        self._sld_hi.setRange(0, int(float(pd.get("range_max", 2.0)) * self._range_scale))
        self._sld_hi.setValue(int(pd["default_hi"] * self._range_scale))
        self._sld_hi.setStyleSheet(sty)
        self._sld_hi.setMinimumWidth(120)
        self._lbl_hi = _mk_val_lbl(f"{pd['default_hi']:.2f}", color)
        self._sld_hi.valueChanged.connect(
            lambda v: self._lbl_hi.setText(f"{v/self._range_scale:.2f}"))
        self._sld_hi.valueChanged.connect(lambda _: self.changed.emit())
        grid.addWidget(self._sld_hi, 3, 1)
        grid.addWidget(self._lbl_hi, 3, 2)

        # 行4：平滑系数
        grid.addWidget(_mk_lbl("平滑:"), 4, 0)
        self._sld_smooth = QSlider(Qt.Orientation.Horizontal)
        self._sld_smooth.setRange(0, 95)
        self._sld_smooth.setValue(15)
        self._sld_smooth.setStyleSheet(_SLIDER)
        self._sld_smooth.setMinimumWidth(120)
        self._lbl_smooth = _mk_val_lbl("0.15", FG_DIM)
        self._sld_smooth.valueChanged.connect(
            lambda v: self._lbl_smooth.setText(f"{v/100:.2f}"))
        self._sld_smooth.valueChanged.connect(lambda _: self.changed.emit())
        grid.addWidget(self._sld_smooth, 4, 1)
        grid.addWidget(self._lbl_smooth, 4, 2)

        grid.setColumnStretch(1, 3)
        lay.addLayout(grid)

        # 描述文字
        desc_lbl = QLabel(pd["desc"])
        desc_lbl.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;background:transparent;border:none;"
        )
        desc_lbl.setWordWrap(True)
        lay.addWidget(desc_lbl)

    # ── 属性读取 ──────────────────────────────────────────────────────────
    @property
    def enabled(self) -> bool:
        return self._cb_enabled.isChecked()

    @property
    def source(self) -> str:
        return str(self._combo_source.currentData() or "intensity")

    @property
    def amount(self) -> float:
        return self._sld_amount.value() / float(getattr(self, "_amount_scale", 100))

    @property
    def range_lo(self) -> float:
        return self._sld_lo.value() / float(getattr(self, "_range_scale", 100))

    @property
    def range_hi(self) -> float:
        return self._sld_hi.value() / float(getattr(self, "_range_scale", 100))

    @property
    def smooth(self) -> float:
        return self._sld_smooth.value() / 100.0

    @property
    def curve(self) -> str:
        return str(self._combo_curve.currentData() or "linear")

    def update_meter(self, value: float, overlay: float):
        self._meter.set_value(value)
        self._lbl_output.setText(f"叠加: {overlay:+.3f}")

    def compute_overlay(self, dim_values: Dict[str, float]) -> float:
        """
        根据当前音频维度值计算叠加量（已平滑，已约束在 [range_lo, range_hi]）。
        """
        if not self.enabled:
            self._smooth_state = 0.0
            return 0.0

        raw = dim_values.get(self.source, 0.0)

        # 曲线变换
        t = max(0.0, min(1.0, raw))
        curve = self.curve
        if curve == "exp":
            t = t * t
        elif curve == "log":
            t = math.sqrt(t)
        elif curve == "step":
            t = 1.0 if t >= 0.5 else 0.0

        # 叠加量 = 曲线值 * 叠加幅度，映射到 [range_lo, range_hi]
        lo = self.range_lo
        hi = self.range_hi
        target = (lo + t * (hi - lo)) * self.amount

        # 平滑
        alpha = self.smooth
        self._smooth_state = (self._smooth_state
                              + (1.0 - alpha) * (target - self._smooth_state))
        return max(lo, min(hi, self._smooth_state))

    def to_dict(self) -> dict:
        return {
            "enabled":  self.enabled,
            "source":   self.source,
            "amount":   self.amount,
            "range_lo": self.range_lo,
            "range_hi": self.range_hi,
            "smooth":   self.smooth,
            "curve":    self.curve,
        }

    def load_dict(self, d: dict):
        self._cb_enabled.setChecked(bool(d.get("enabled", True)))
        idx = self._combo_source.findData(d.get("source", "intensity"))
        if idx >= 0:
            self._combo_source.setCurrentIndex(idx)
        self._sld_amount.setValue(int(d.get("amount", 0.5) * getattr(self, "_amount_scale", 100)))
        self._sld_lo.setValue(int(d.get("range_lo", -1.0) * getattr(self, "_range_scale", 100)))
        self._sld_hi.setValue(int(d.get("range_hi", 1.0) * getattr(self, "_range_scale", 100)))
        self._sld_smooth.setValue(int(d.get("smooth", 0.15) * 100))
        idx2 = self._combo_curve.findData(d.get("curve", "linear"))
        if idx2 >= 0:
            self._combo_curve.setCurrentIndex(idx2)


# ─────────────────────────────────────────────────────────────────────────────
#  识别参数控制区（参考 programmer 的 AudioDriverWidget 参数体系）
# ─────────────────────────────────────────────────────────────────────────────
class _RecognitionParamsWidget(QFrame):
    """
    音频识别参数控制区。
    包含：精度（灵敏度）、响应速度（Attack/Release）、噪声底限（基线）、识别模式
    """
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"QFrame{{background:{BG_PANEL};border:1px solid #303030;border-radius:4px;}}"
        )
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)

        title = QLabel("识别参数")
        title.setStyleSheet(
            f"color:{FG_ACCENT};font-size:11px;font-weight:bold;"
            f"background:transparent;border:none;"
        )
        lay.addWidget(title)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)

        def _mk_lbl(text):
            l = QLabel(text)
            l.setStyleSheet(
                f"color:{FG_DIM};font-size:10px;background:transparent;border:none;"
            )
            l.setMinimumWidth(70)
            return l

        def _mk_val_lbl(text, color=FG_ACCENT):
            l = QLabel(text)
            l.setStyleSheet(
                f"color:{color};font-size:10px;min-width:45px;"
                f"background:transparent;border:none;"
            )
            return l

        params = [
            ("精度（灵敏度）",     _SLIDER_GREEN,  0,  200, 100,
             "sensitivity",  "1.00", FG_GREEN),
            ("响应速度（Attack）", _SLIDER,         1,  100,  10,
             "attack",       "0.10", FG_ACCENT),
            ("释放速度（Release）",_SLIDER,        10, 2000, 200,
             "release",      "200ms", FG_DIM),
            ("噪声底限（基线）",   _SLIDER,         0,   30,   2,
             "noise_floor",  "0.02", FG_DIM),
            ("段落灵敏度",        _SLIDER_ORANGE,  5,  200,  55,
             "section_sens", "0.55", FG_ORANGE),
        ]

        self._param_sliders: Dict[str, QSlider] = {}
        self._param_labels:  Dict[str, QLabel]  = {}

        for row, (label, sty, lo, hi, default, key, val_text, color) in enumerate(params):
            grid.addWidget(_mk_lbl(label), row, 0)
            sld = QSlider(Qt.Orientation.Horizontal)
            sld.setRange(lo, hi)
            sld.setValue(default)
            sld.setStyleSheet(sty)
            sld.setMinimumWidth(120)
            sld.valueChanged.connect(self.changed)
            self._param_sliders[key] = sld
            grid.addWidget(sld, row, 1)
            val_lbl = _mk_val_lbl(val_text, color)
            self._param_labels[key] = val_lbl
            grid.addWidget(val_lbl, row, 2)

        # 连接显示更新
        def _upd(key, divisor, suffix):
            def _fn(v):
                self._param_labels[key].setText(f"{v/divisor:.2f}{suffix}")
            return _fn

        self._param_sliders["sensitivity"].valueChanged.connect(
            _upd("sensitivity", 100, ""))
        self._param_sliders["attack"].valueChanged.connect(
            _upd("attack", 100, ""))
        self._param_sliders["release"].valueChanged.connect(
            lambda v: self._param_labels["release"].setText(f"{v}ms"))
        self._param_sliders["noise_floor"].valueChanged.connect(
            _upd("noise_floor", 100, ""))
        self._param_sliders["section_sens"].valueChanged.connect(
            _upd("section_sens", 100, ""))

        grid.setColumnStretch(1, 3)
        lay.addLayout(grid)

        # 识别模式
        mode_row = QHBoxLayout()
        mode_row.addWidget(_mk_lbl("识别模式:"))
        self._combo_mode = QComboBox()
        self._combo_mode.setFixedWidth(140)
        for key, label in [
            ("auto",    "自动（推荐）"),
            ("beat",    "节拍优先"),
            ("energy",  "能量优先"),
            ("section", "段落优先"),
        ]:
            self._combo_mode.addItem(label, key)
        self._combo_mode.currentIndexChanged.connect(self.changed)
        mode_row.addWidget(self._combo_mode)
        mode_row.addStretch()
        lay.addLayout(mode_row)

    def get_sensitivity(self) -> float:
        return self._param_sliders["sensitivity"].value() / 100.0

    def get_attack(self) -> float:
        return self._param_sliders["attack"].value() / 100.0

    def get_release_ms(self) -> int:
        return self._param_sliders["release"].value()

    def get_noise_floor(self) -> float:
        return self._param_sliders["noise_floor"].value() / 100.0

    def get_section_sens(self) -> float:
        return self._param_sliders["section_sens"].value() / 100.0

    def get_mode(self) -> str:
        return str(self._combo_mode.currentData() or "auto")

    def to_dict(self) -> dict:
        d = {k: s.value() for k, s in self._param_sliders.items()}
        d["mode"] = self.get_mode()
        return d

    def load_dict(self, d: dict):
        for k, s in self._param_sliders.items():
            if k in d:
                s.setValue(int(d[k]))
        if "mode" in d:
            idx = self._combo_mode.findData(d["mode"])
            if idx >= 0:
                self._combo_mode.setCurrentIndex(idx)


# ─────────────────────────────────────────────────────────────────────────────
#  主面板：BridgeModulationPanel
# ─────────────────────────────────────────────────────────────────────────────
class BridgeModulationPanel(QWidget):
    """
    音频桥接器·自动调制子页面。
    通过 overlay_params_updated 信号将叠加参数字典发射给 AudioBridgePanel。
    叠加参数字典格式：
      {
        "speed_delta":      float,   # 叠加速度（相对量，±）
        "amplitude_delta":  float,   # 动作幅度叠加量（±）
        "phase_step_delta": float,   # 密度（相位差）叠加量（±）
      }
    """
    # 叠加参数更新信号
    overlay_params_updated = pyqtSignal(dict)
    # 识别参数变更信号（供 AudioBridgePanel 同步到 MusicStructureAnalyzer）
    recognition_params_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._enabled = False
        self._last_ev = None
        self._dim_values: Dict[str, float] = {
            "speed": 0.0, "intensity": 0.0, "complexity": 0.0
        }
        self.setStyleSheet(f"background:{BG_MAIN};")
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ── 顶部：启用开关 + 三维度电平条 ──
        header = QFrame()
        header.setStyleSheet(
            f"background:{BG_HEADER};border-radius:4px;border:1px solid #303030;"
        )
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(12, 8, 12, 8)
        header_lay.setSpacing(12)

        self._btn_enable = QPushButton("启用自动调制")
        self._btn_enable.setCheckable(True)
        self._btn_enable.setStyleSheet(_BTN_ENABLE)
        self._btn_enable.toggled.connect(self._on_enable_toggled)
        header_lay.addWidget(self._btn_enable)

        header_lay.addWidget(self._mk_vsep())

        # 三个维度的实时电平条
        for dim_key, dim_label, color in [
            ("speed",      "速度",   FG_GREEN),
            ("intensity",  "强度",   FG_ORANGE),
            ("complexity", "复杂度", FG_CYAN),
        ]:
            lbl = QLabel(f"{dim_label}:")
            lbl.setStyleSheet(
                f"color:{FG_DIM};font-size:10px;background:transparent;border:none;"
            )
            header_lay.addWidget(lbl)
            meter = _DimMeterBar(color)
            meter.setFixedWidth(60)
            setattr(self, f"_meter_{dim_key}", meter)
            header_lay.addWidget(meter)

        header_lay.addStretch()

        info = QLabel("音频实时驱动效果参数叠加 | 叠加量在已有效果参数基础上增量调制")
        info.setStyleSheet(
            f"color:{FG_DIM};font-size:10px;background:transparent;border:none;"
        )
        info.setWordWrap(True)
        header_lay.addWidget(info, 1)
        root.addWidget(header)

        # ── 识别参数区 ──
        self._recognition_params = _RecognitionParamsWidget()
        self._recognition_params.changed.connect(self._on_recognition_changed)
        root.addWidget(self._recognition_params)

        # ── 三个目标参数卡片 ──
        self._target_cards: Dict[str, _TargetParamCard] = {}
        for pd in TARGET_PARAMS:
            card = _TargetParamCard(pd)
            card.changed.connect(lambda: None)  # 参数变化时不需要立即重算
            self._target_cards[pd["key"]] = card
            root.addWidget(card)

        root.addStretch()

    def _mk_vsep(self) -> QLabel:
        s = QLabel("│")
        s.setStyleSheet(
            f"color:#545454;padding:0 4px;background:transparent;border:none;")
        return s

    def _on_enable_toggled(self, checked: bool):
        self._enabled = checked
        if not checked:
            # 关闭时发射零叠加，清除残留
            self.overlay_params_updated.emit({
                "speed_delta":      0.0,
                "amplitude_delta":  0.0,
                "phase_step_delta": 0.0,
            })

    def _on_recognition_changed(self):
        rp = self._recognition_params
        self.recognition_params_changed.emit({
            "sensitivity":  rp.get_sensitivity(),
            "attack":       rp.get_attack(),
            "release_ms":   rp.get_release_ms(),
            "noise_floor":  rp.get_noise_floor(),
            "section_sens": rp.get_section_sens(),
            "mode":         rp.get_mode(),
        })

    def on_analysis_tick(self, ev) -> None:
        """
        接收 MusicStructureEvent，计算三个维度的归一化值，
        再计算各目标参数的叠加量，发射 overlay_params_updated 信号。
        """
        if not self._enabled:
            return
        self._last_ev = ev

        # ── 提取三个维度的归一化值 ──
        bpm        = float(getattr(ev, "bpm", 120.0))
        rms        = float(getattr(ev, "rms", 0.0))
        beat_phase = float(getattr(ev, "beat_phase", 0.0))
        is_beat    = bool(getattr(ev, "is_beat", False))
        bands      = list(getattr(ev, "bands", [0.0] * 8))
        while len(bands) < 8:
            bands.append(0.0)
        extras         = getattr(ev, "extras", {}) or {}
        novelty        = float(getattr(ev, "novelty", 0.0))
        flux           = float(extras.get("spectral_flux",
                                          extras.get("flux", novelty)))
        onset_density  = float(extras.get("onset_density", 0.0))

        # 速度维度：BPM 归一化（40~240 BPM → 0~1）+ 节拍相位 + 节拍脉冲
        speed_raw   = max(0.0, min(1.0, (bpm - 40.0) / 200.0))
        beat_pulse  = 0.3 if is_beat else 0.0
        speed_val   = min(1.0, speed_raw * 0.7 + beat_phase * 0.2 + beat_pulse)

        # 强度维度：RMS + 低频能量
        bass_energy   = (bands[0] + bands[1]) / 2.0 if len(bands) >= 2 else 0.0
        intensity_val = min(1.0, rms * 0.6 + bass_energy * 0.4)

        # 复杂度维度：频谱变化率 + 节奏密度 + 中高频能量
        mid_high       = sum(bands[2:6]) / 4.0 if len(bands) >= 6 else 0.0
        complexity_val = min(1.0, flux * 0.4 + onset_density * 0.3 + mid_high * 0.3)

        # 识别参数影响：灵敏度缩放 + 噪声底限
        sens        = self._recognition_params.get_sensitivity()
        noise_floor = self._recognition_params.get_noise_floor()
        speed_val      = min(1.0, speed_val * sens)
        intensity_val  = min(1.0, intensity_val * sens)
        complexity_val = min(1.0, complexity_val * sens)
        if rms < noise_floor:
            intensity_val  = 0.0
            complexity_val = 0.0

        self._dim_values = {
            "speed":      speed_val,
            "intensity":  intensity_val,
            "complexity": complexity_val,
        }

        # 更新电平条
        self._meter_speed.set_value(speed_val)
        self._meter_intensity.set_value(intensity_val)
        self._meter_complexity.set_value(complexity_val)

        # ── 计算各目标参数的叠加量 ──
        overlays: Dict[str, float] = {}
        for pd in TARGET_PARAMS:
            key  = pd["key"]
            card = self._target_cards.get(key)
            if card is None:
                overlays[key] = 0.0
                continue
            overlay = card.compute_overlay(self._dim_values)
            overlays[key] = overlay
            dim_val = self._dim_values.get(card.source, 0.0)
            card.update_meter(dim_val, overlay)

        self.overlay_params_updated.emit(overlays)

    # ─────────────────────────────────────────────────────────────────────
    #  持久化
    # ─────────────────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "enabled":     self._enabled,
            "recognition": self._recognition_params.to_dict(),
            "targets":     {k: c.to_dict() for k, c in self._target_cards.items()},
        }

    def load_dict(self, d: dict):
        if not isinstance(d, dict):
            return
        enabled = bool(d.get("enabled", False))
        self._btn_enable.setChecked(enabled)
        rec = d.get("recognition", {})
        if rec:
            self._recognition_params.load_dict(rec)
        targets = d.get("targets", {})
        for k, c in self._target_cards.items():
            if k in targets:
                c.load_dict(targets[k])
