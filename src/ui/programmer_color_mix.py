# -*- coding: utf-8 -*-
"""programmer_color_mix — Programmer 的 Color Mix 编辑器（R183 批次 I，项 1/2）。

参考语义（不引第三方库，本仓全无 PyQt 色轮依赖）：
    FF0000/FF9900/FFFF00/00FF00/00FFFF/0000FF/9900FF/FF00FF）+ 8 级灰阶
    （`getHTMLColor(round(index*36.4285))`），点一下即写色。
    `wheelEnabled: true`（滚轮微调）。
    （`colorFiltersFileList`）按编号选色（数据见 programmer_gel_data）。
  · 官方 Color Mix —— 面板自动检测加色/减色构成，存在哪个分量就写哪个。

本模块只管"选色 + 把 6 个分量交给调用方"，不碰面板写值链：写值经
`apply_color_to_row`（鸭子类型调 ChannelTypeRow 的 `_set_value`），能力检测复用
`programmer_panel._fixture_supports_logical_channel`（延迟 import 防循环依赖）。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor, QConicalGradient, QLinearGradient, QMouseEvent, QPainter, QPen,
    QRadialGradient, QWheelEvent,
)
from PyQt6.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSlider, QTabWidget, QVBoxLayout, QWidget,
)

#: 加色/减色成员分量键（顺序固定；W/A/L/UV 统称"扩展分量"）。
EXTRA_ORDER = ("W", "A", "L", "UV")
EXTRA_LABELS = {"W": "白 W", "A": "琥珀 A", "L": "柠檬 L", "UV": "紫外 UV"}

#: ColorToolBasic `baseColors` 8 基色（Apache-2.0 语义移植）。
from .programmer_gel_data import GEL_COLORS  # noqa: E402  (项 2：色纸表独立模块，见其文档)

#: ColorToolBasic `baseColors` 8 基色（Apache-2.0 语义移植）。
BASE_COLORS: Tuple[Tuple[str, int, int, int], ...] = (
    ("红", 255, 0, 0), ("橙", 255, 153, 0), ("黄", 255, 255, 0), ("绿", 0, 255, 0),
    ("青", 0, 255, 255), ("蓝", 0, 0, 255), ("紫", 153, 0, 255), ("品红", 255, 0, 255),
)

#: 8 级灰阶（同 `index * 36.4285`）。
GRAY_STEPS: Tuple[int, ...] = tuple(int(round(i * 36.4285)) for i in range(8))

def hsv_to_rgb(h: float, s: float, v: float) -> Tuple[int, int, int]:
    """HSV(h 0..360, s/v 0..1) → RGB 0..255（纯函数，便于测试）。"""
    h = float(h) % 360.0
    s = max(0.0, min(1.0, float(s)))
    v = max(0.0, min(1.0, float(v)))
    c = v * s
    x = c * (1.0 - abs((h / 60.0) % 2.0 - 1.0))
    m = v - c
    seg = int(h // 60.0) % 6
    rgb = ((c, x, 0.0), (x, c, 0.0), (0.0, c, x), (0.0, x, c), (x, 0.0, c), (c, 0.0, x))[seg]
    return tuple(int(round((comp + m) * 255.0)) for comp in rgb)  # type: ignore[return-value]


def rgb_to_hsv(r: int, g: int, b: int) -> Tuple[float, float, float]:
    """RGB 0..255 → HSV(h 0..360, s/v 0..1)。"""
    rf, gf, bf = (max(0, min(255, int(x))) / 255.0 for x in (r, g, b))
    mx, mn = max(rf, gf, bf), min(rf, gf, bf)
    span = mx - mn
    if span <= 1e-9:
        hue = 0.0
    elif mx == rf:
        hue = (60.0 * ((gf - bf) / span)) % 360.0
    elif mx == gf:
        hue = 60.0 * ((bf - rf) / span) + 120.0
    else:
        hue = 60.0 * ((rf - gf) / span) + 240.0
    sat = 0.0 if mx <= 1e-9 else span / mx
    return hue, sat, mx


class HueSatWheel(QWidget):
    """色相-饱和度圆盘（ColorToolFull 的 `wheelEnabled` 交互）。

    绘制：QConicalGradient 画色相环 + QRadialGradient 白心叠加表示饱和度，
    最外层按 value 叠一层黑罩做明度；
    交互：左键按下/拖动取色；滚轮微调明度（±1/±5，Shift 加速）。
    """

    color_picked = pyqtSignal(int, int, int)
    value_changed = pyqtSignal(float)

    _RING = 6.0          # 外圈描边内缩

    def __init__(self, parent=None):
        super().__init__(parent)
        self._h = 0.0
        self._s = 1.0
        self._v = 1.0
        self._dragging = False
        self.setMinimumSize(150, 150)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setToolTip("色相-饱和度圆盘：左键拖动取色；滚轮调明度（Shift=大步）")

    # ── 值 ──────────────────────────────────────────────────────────────
    def hsv(self) -> Tuple[float, float, float]:
        return self._h, self._s, self._v

    def rgb(self) -> Tuple[int, int, int]:
        return hsv_to_rgb(self._h, self._s, self._v)

    def set_hsv(self, h: float, s: float, v: float):
        self._h = float(h) % 360.0
        self._s = max(0.0, min(1.0, float(s)))
        self._v = max(0.0, min(1.0, float(v)))
        self.update()

    def set_rgb(self, r: int, g: int, b: int):
        self.set_hsv(*rgb_to_hsv(r, g, b))

    # ── 几何 ────────────────────────────────────────────────────────────
    def _disc_rect(self) -> QRectF:
        side = float(min(self.width(), self.height())) - self._RING
        side = max(20.0, side)
        return QRectF((self.width() - side) / 2.0, (self.height() - side) / 2.0, side, side)

    def _pick(self, pos: QPointF):
        rect = self._disc_rect()
        cx, cy = rect.center().x(), rect.center().y()
        radius = rect.width() / 2.0
        dx, dy = pos.x() - cx, pos.y() - cy
        dist = math.hypot(dx, dy)
        if radius <= 0:
            return
        hue = math.degrees(math.atan2(-dy, dx)) % 360.0
        sat = max(0.0, min(1.0, dist / radius))
        self._h, self._s = hue, sat
        self.update()
        self.color_picked.emit(*self.rgb())

    def mousePressEvent(self, event: QMouseEvent):  # noqa: N802 (Qt 覆写)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = True
        self._pick(event.position())

    def mouseMoveEvent(self, event: QMouseEvent):  # noqa: N802 (Qt 覆写)
        if self._dragging:
            self._pick(event.position())

    def mouseReleaseEvent(self, event: QMouseEvent):  # noqa: N802 (Qt 覆写)
        self._dragging = False

    def wheelEvent(self, event: QWheelEvent):  # noqa: N802 (Qt 覆写)
        steps = event.angleDelta().y() / 120.0
        if abs(steps) < 1e-6:
            return
        step = 0.05 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.01
        self._v = max(0.0, min(1.0, self._v + steps * step))
        self.update()
        self.value_changed.emit(self._v)
        self.color_picked.emit(*self.rgb())
        event.accept()

    def paintEvent(self, event):  # noqa: N802 (Qt 覆写)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._disc_rect()
        center = rect.center()
        radius = rect.width() / 2.0
        # 1) 色相环
        cone = QConicalGradient(center, 0.0)
        for i in range(13):
            ang = i * 30.0
            cone.setColorAt(min(1.0, ang / 360.0), QColor(*hsv_to_rgb(ang, 1.0, 1.0)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(cone)
        p.drawEllipse(rect)
        # 2) 饱和度：白心 → 透明边
        rad = QRadialGradient(center, radius)
        rad.setColorAt(0.0, QColor(255, 255, 255, 255))
        rad.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(rad)
        p.drawEllipse(rect)
        # 3) 明度：黑罩
        if self._v < 0.999:
            black = QColor(0, 0, 0, int(round((1.0 - self._v) * 255)))
            p.setBrush(black)
            p.drawEllipse(rect)
        # 4) 边框 + 当前色标记
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(60, 60, 60), 1))
        p.drawEllipse(rect)
        ang = math.radians(self._h)
        marker = QPointF(center.x() + math.cos(ang) * self._s * radius,
                         center.y() - math.sin(ang) * self._s * radius)
        p.setPen(QPen(QColor(255, 255, 255, 230), 2))
        p.drawEllipse(marker, 5.0, 5.0)
        p.setPen(QPen(QColor(20, 20, 20), 1))
        p.drawEllipse(marker, 6.5, 6.5)
        p.end()


def _swatch_button(hex_color: str, title: str, size: int = 26) -> QPushButton:
    btn = QPushButton()
    btn.setFixedSize(size, size)
    btn.setToolTip(title)
    btn.setStatusTip(title)
    btn.setStyleSheet(
        f"QPushButton{{background:{hex_color};border:1px solid #3D3D3D;border-radius:3px;"
        "padding:0;min-height:0px;}"
        "QPushButton:hover{border:1px solid #FFFFFF;}")
    return btn


class ColorMixEditor(QDialog):
    """Color Mix 对话框：色轮 / 基色 / 色纸 三页（ColorTool 三视图语义）。

    `caps` 为扩展分量的能力表（{"W": True, ...}），只显示设备真正有的分量；
    `color_applied` 每次带全部 6 个分量发出，由调用方决定写哪些。
    """

    color_applied = pyqtSignal(int, int, int, int, int, int)  # r, g, b, w, a, uv

    def __init__(self, parent=None, rgb: Tuple[int, int, int] = (255, 255, 255),
                 caps: Optional[Dict[str, bool]] = None, title: str = "Color Mix"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        caps = dict(caps or {})
        self._extra_values: Dict[str, int] = {k: 0 for k in EXTRA_ORDER}
        self._extra_touched: Set[str] = set()
        self._extra_sliders: Dict[str, QSlider] = {}
        self._extra_readouts: Dict[str, QLabel] = {}
        self._extra_enabled = [k for k in EXTRA_ORDER if caps.get(k)]

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        self._preview = QLabel()
        self._preview.setFixedHeight(26)
        root.addWidget(self._preview)

        tabs = QTabWidget()
        tabs.addTab(self._build_wheel_page(), "色轮")
        tabs.addTab(self._build_basic_page(), "基色")
        tabs.addTab(self._build_gel_page(), "色纸")
        root.addWidget(tabs, 1)

        self._wheel.set_rgb(*rgb)
        self._rgb_edit = [max(0, min(255, int(c))) for c in rgb]
        self._sync_extra_rows()
        self._refresh_preview()

    # ── 页 1：色轮 + 明度 + 扩展分量 ────────────────────────────────────
    def _build_wheel_page(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(10)
        self._wheel = HueSatWheel()
        self._wheel.color_picked.connect(self._on_wheel_picked)
        lay.addWidget(self._wheel, 1)

        side = QVBoxLayout()
        side.setSpacing(4)
        val_lbl = QLabel("明度")
        val_lbl.setToolTip("对应 DMX 满程的明度（滚轮微调，Shift 大步）")
        side.addWidget(val_lbl)
        self._value_slider = QSlider(Qt.Orientation.Vertical)
        self._value_slider.setRange(0, 255)
        self._value_slider.setValue(255)
        self._value_slider.setFixedWidth(28)
        self._value_slider.valueChanged.connect(self._on_value_slider)
        side.addWidget(self._value_slider, 1, Qt.AlignmentFlag.AlignHCenter)

        for ct in self._extra_enabled:
            row = QHBoxLayout()
            row.setSpacing(4)
            lab = QLabel(EXTRA_LABELS.get(ct, ct))
            lab.setFixedWidth(52)
            row.addWidget(lab)
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(0, 255)
            sl.setValue(0)
            sl.setFixedWidth(110)
            sl.setToolTip(f"{EXTRA_LABELS.get(ct, ct)} 0..255（滚轮微调）")
            sl.valueChanged.connect(lambda v, c=ct: self._on_extra_changed(c, v))
            row.addWidget(sl)
            ro = QLabel("0")
            ro.setFixedWidth(30)
            row.addWidget(ro)
            self._extra_sliders[ct] = sl
            self._extra_readouts[ct] = ro
            side.addLayout(row)
        side.addStretch()
        lay.addLayout(side)
        return page

    # ── 页 2：8 基色 + 8 灰阶 ──────────────────────────────────────────
    def _build_basic_page(self) -> QWidget:
        page = QWidget()
        grid = QGridLayout(page)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(4)
        for idx, (name, r, g, b) in enumerate(BASE_COLORS):
            hex_color = QColor(r, g, b).name()
            btn = _swatch_button(hex_color, f"{name}  DMX({r},{g},{b})")
            btn.clicked.connect(lambda _c=False, rr=r, gg=g, bb=b: self._apply_rgb(rr, gg, bb))
            grid.addWidget(btn, 0, idx)
        for idx, gray in enumerate(GRAY_STEPS):
            hex_color = QColor(gray, gray, gray).name()
            btn = _swatch_button(hex_color, f"灰 {gray}  ({round(gray / 2.55)}%)")
            btn.clicked.connect(lambda _c=False, v=gray: self._apply_rgb(v, v, v))
            grid.addWidget(btn, 1, idx)
        note = QLabel("上排：ColorToolBasic 8 基色；下排：8 级灰阶。点击即写 R/G/B"
                      "（设备有的扩展分量按当前滑杆值一并写）。")
        note.setWordWrap(True)
        grid.addWidget(note, 2, 0, 1, 8)
        return page

    # ── 页 3：色纸（Gel）──────────────────────────────────────────────
    def _build_gel_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)
        tip = QLabel("常用色纸（Lee / Rosco 各 25 色，RGB 为公开参考值的近似整量化）"
                     "；点击即写 R/G/B。")
        tip.setWordWrap(True)
        outer.addWidget(tip)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        grid = QGridLayout(inner)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setSpacing(6)
        for idx, (code, name, hex_color) in enumerate(GEL_COLORS):
            col = idx % 5
            row = (idx // 5) * 2
            rgb = QColor(hex_color)
            btn = _swatch_button(hex_color, f"{code} {name}  {hex_color.upper()}",
                                 size=30)
            btn.clicked.connect(
                lambda _c=False, rr=rgb.red(), gg=rgb.green(), bb=rgb.blue():
                self._apply_rgb(rr, gg, bb))
            grid.addWidget(btn, row, col)
            lab = QLabel(code)
            lab.setToolTip(f"{code} {name}")
            lab.setStyleSheet("color:#999999;font-size:9px;")
            grid.addWidget(lab, row + 1, col, Qt.AlignmentFlag.AlignHCenter)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        return page

    # ── 交互 ────────────────────────────────────────────────────────────
    def _on_wheel_picked(self, r: int, g: int, b: int):
        self._rgb_edit = [r, g, b]
        self._refresh_preview()
        self._emit_applied()

    def _on_value_slider(self, value: int):
        self._wheel.set_hsv(*self._wheel.hsv()[:2], v=float(value) / 255.0)
        self._on_wheel_picked(*self._wheel.rgb())

    def _on_extra_changed(self, ct: str, value: int):
        self._extra_values[ct] = max(0, min(255, int(value)))
        self._extra_touched.add(ct)
        ro = self._extra_readouts.get(ct)
        if ro is not None:
            ro.setText(str(self._extra_values[ct]))
        self._emit_applied()

    def _apply_rgb(self, r: int, g: int, b: int):
        qc = QColor(r, g, b)
        self._rgb_edit = [qc.red(), qc.green(), qc.blue()]
        self._wheel.set_rgb(*self._rgb_edit)
        self._refresh_preview()
        self._emit_applied()

    def _sync_extra_rows(self):
        """刷新扩展分量的读数与可见性（能力表变化时也安全）。"""
        for ct, sl in self._extra_sliders.items():
            ro = self._extra_readouts.get(ct)
            if ro is not None:
                ro.setText(str(sl.value()))

    def _current_extras(self) -> Tuple[int, int, int]:
        """(w, a, uv)：未触碰过的分量恒 0（不写），被触碰过则按滑杆值写（含 0）。"""
        w = self._extra_values.get("W", 0)
        a = self._extra_values.get("A", 0)
        uv = self._extra_values.get("UV", 0)
        return (w if "W" in self._extra_enabled else 0,
                a if "A" in self._extra_enabled else 0,
                uv if "UV" in self._extra_enabled else 0)

    def touched_extras(self) -> Set[str]:
        """本次会话里用户真正动过的扩展分量（调用方据此决定写哪些）。"""
        return {k for k in self._extra_touched if k in self._extra_enabled}

    def _refresh_preview(self):
        r, g, b = self._rgb_edit
        self._preview.setStyleSheet(
            f"background:{QColor(r, g, b).name()};border:1px solid #3D3D3D;"
            "border-radius:3px;")
        self._preview.setToolTip(f"R{r} G{g} B{b}")

    def _emit_applied(self):
        r, g, b = self._rgb_edit
        w, a, uv = self._current_extras()
        self._refresh_preview()
        self.color_applied.emit(r, g, b, w, a, uv)

    def current_rgb(self) -> Tuple[int, int, int]:
        return self._rgb_edit[0], self._rgb_edit[1], self._rgb_edit[2]


# ─────────────────────────────────────────────────────────────────────────────
#  与 ChannelTypeRow 的对接（鸭子类型，避免循环 import）
# ─────────────────────────────────────────────────────────────────────────────

def _supports(fd: Any, ch_type: str) -> bool:
    try:
        from .programmer_panel import _fixture_supports_logical_channel
        return bool(_fixture_supports_logical_channel(fd, ch_type))
    except Exception:
        return False


def capability_map(row: Any, extra_keys: Sequence[str] = EXTRA_ORDER) -> Dict[str, bool]:
    """按行内设备定义集合，汇总"哪些 R/G/B 扩展分量存在"。"""
    defs = getattr(row, "_fixture_defs", {}) or {}
    fixtures = list(getattr(row, "_fixtures", []) or [])
    caps: Dict[str, bool] = {}
    for ct in extra_keys:
        caps[ct] = any(_supports(defs.get(getattr(fi, "def_id", "")), ct) for fi in fixtures)
    return caps


def _color_writes(r: int, g: int, b: int, extras: Optional[Dict[str, int]]):
    """本次要写的 (通道, 值) 列表：RGB + 用户动过的扩展分量。"""
    wanted: List[Tuple[str, int]] = [("R", int(r)), ("G", int(g)), ("B", int(b))]
    for ct, val in (extras or {}).items():
        if ct in EXTRA_ORDER:
            wanted.append((str(ct), int(val)))
    return wanted


def apply_color_to_row(row: Any, r: int, g: int, b: int,
                       extras: Optional[Dict[str, int]] = None) -> int:
    """把颜色写到该行内所有**支持该通道**的设备（复用 `_set_value` 全链路）。

    extras 只包含用户真的动过的扩展分量（见 `touched_extras`）——设备没有该
    通道则不写。返回写入的通道数（测试与状态提示用）。
    """
    defs = getattr(row, "_fixture_defs", {}) or {}
    wanted = _color_writes(r, g, b, extras)
    written = 0
    for fi in (getattr(row, "_fixtures", []) or []):
        fd = defs.get(getattr(fi, "def_id", ""))
        for ct, val in wanted:
            if not _supports(fd, ct):
                continue
            try:
                row._set_value(fi.id, ct, max(0, min(255, int(val))))
                row.value_changed.emit(ct, max(0, min(255, int(val))))
                written += 1
            except Exception:
                continue
    try:
        row._update_color_preview()
    except Exception:
        pass
    return written


def open_color_mix_editor(row: Any, title: str = "Color Mix") -> Optional[ColorMixEditor]:
    """从通道行打开 Color Mix（挂在"拾色"旁的"色轮"钮上）。

    全黑起步时以白起始（否则色相环标记落在饱和度为 0 的点上，看不出颜色）。
    """
    def _merged(ct: str) -> int:
        try:
            return int(row._get_merged_value(ct))
        except Exception:
            return 255

    initial = (_merged("R"), _merged("G"), _merged("B"))
    if initial == (0, 0, 0):
        initial = (255, 255, 255)
    dlg = ColorMixEditor(parent=row.window() if hasattr(row, "window") else None,
                         rgb=initial, caps=capability_map(row), title=title)

    def _apply(r: int, g: int, b: int, parts: dict):
        """把编辑器当前色写到本行（`parts` 含 w/a/uv，仅写用户动过的分量）。"""
        extras = {k: int(parts.get(k, 0)) for k in ("W", "A", "UV")
                  if k in dlg.touched_extras()}
        apply_color_to_row(row, r, g, b, extras)

    dlg.color_applied.connect(
        lambda r, g, b, w, a, uv: _apply(r, g, b, {"W": w, "A": a, "UV": uv}))
    dlg.exec()
    return dlg
