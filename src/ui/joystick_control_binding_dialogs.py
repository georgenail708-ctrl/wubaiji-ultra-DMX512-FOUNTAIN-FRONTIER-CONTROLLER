"""
joystick_control_binding_dialogs.py — 摇杆绑定配置弹窗
────────────────────────────────────────────────────────────────────────────
BindingMappingDialog         — 映射调整（曲线/范围/死区/反转 + 实时预览）
BindingManagerDialog         — 绑定管理子窗口（非模态表格）
ExternalJoystickConfigDialog — 设备配置窗口（菜单入口）
────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations
from typing import Optional, List, Tuple

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QDialog, QDialogButtonBox,
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar, QMessageBox,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt, QSettings, pyqtSignal, QPointF

from .physical_joystick_driver import get_physical_joystick_driver
from .qascade_theme import (
    BG_DEEP as _C_BG_DEEP,
    FOCUS_BLUE as _C_FOCUS,
    BRIGHT_SUCCESS as _C_SUCCESS,
    BRIGHT_ERROR as _C_ERROR,
    TEXT_SECONDARY as _C_AXIS,
    SELECT_BLUE as _SEL_BLUE,
    BG_BUTTON_HOVER as _BTN_HOVER,
    TEXT_PRIMARY as _TXT,
    TEXT_SECONDARY as _TXT_DIM,
)

# ── CurveEditor QPainter 调色板：全部走 qascade_theme 主题令牌。
#    功能语义不变：拖拽控制点=成功绿、普通点/曲线=聚焦蓝、光标虚线=错误红。
CURVE_EDITOR_THEME = {
    "canvas":       _C_BG_DEEP,     # 画布底
    "grid":         "rgba(255,255,255,13)",          # 网格线（任务 C）
    "axis_text":    _C_AXIS,        # 坐标轴文字
    "curve":        _C_FOCUS,       # 曲线折线
    "point":        _C_FOCUS,       # 普通控制点
    "point_active": _C_SUCCESS,     # 拖拽中的控制点
    "point_border": "#FFFFFF",      # 点描边(刻意白)
    "cursor":       _C_ERROR,       # 实时位置光标虚线
}

# 任务 C 精修常量：1.25px 抗锯齿线宽；控制点 5px 圆点（直径）+hover 外圈
_CURVE_LINE_W = 1.25

from .joystick_control_binding import (
    JoystickControlBindingRule, JoystickControlBindManager, get_joystick_bind_manager,
    _AXIS_LABELS, _normalize_axis, CURVE_TYPES, CURVE_LABELS,
    BG_CARD, FG_ACCENT, FG_GREEN, FG_RED, BORDER, FG_DIM,
)

# D 波任务 A/B：浮窗解剖标准件
from .widget_kit import (
    EmptyState, NumSlider, PanelSection, apply_dialog_chrome,
    prop_row as _prop_row_base,
)


def prop_row(label, widget, label_width: int = 96):
    """兼容包装：允许传入既有 QLabel（动态文案行，如「配平系数/Gamma」）。"""
    if isinstance(label, str):
        return _prop_row_base(label, widget, label_width)
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(8, 0, 8, 0)
    h.setSpacing(8)
    label.setStyleSheet(
        "color:#999999; font-size:11px; background:transparent;")
    label.setMinimumWidth(label_width)
    label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    h.addWidget(label)
    h.addWidget(widget, 1)
    return row


def _kit_title_row(title: str, on_close) -> QWidget:
    """D 波任务 A：浮窗标准标题行——24px 高，标题 12px 左、✕ 22px 右。"""
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(8)
    t = QLabel(title)
    t.setStyleSheet("color:#E6E6E6; font-size:12px; font-weight:bold;"
                    "background:transparent;")
    h.addWidget(t)
    h.addStretch(1)
    x = QPushButton("✕")
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

# ═══════════════════════════════════════════════════════════════════════════
#  CurveEditor — 二维曲线描点编辑器
# ═══════════════════════════════════════════════════════════════════════════

class CurveEditor(QWidget):
    """可交互的映射曲线编辑器：X=摇杆输入(0-1)，Y=DMX输出(0-255)"""

    curve_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._points: List[Tuple[float, float]] = [(0.0, 0.0), (1.0, 255.0)]
        self._dragging_idx: int = -1
        self._hover_idx: int = -1   # 任务 C：hover 外圈高亮目标点
        self._cursor_pos: Optional[float] = None  # current joystick pos for indicator
        self.setMinimumSize(300, 200)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_points(self, pts: List[Tuple[float, float]]):
        self._points = sorted(pts, key=lambda p: p[0])
        self.update()

    def get_points(self) -> List[Tuple[float, float]]:
        return list(self._points)

    def set_cursor_pos(self, x: float):
        """Set joystick position indicator (0.0-1.0)."""
        self._cursor_pos = max(0.0, min(1.0, x))
        self.update()

    def evaluate(self, x: float) -> float:
        """Evaluate curve at input x (0-1), returns output (0-255)."""
        x = max(0.0, min(1.0, x))
        pts = self._points
        if not pts:
            return 0.0
        if x <= pts[0][0]:
            return pts[0][1]
        if x >= pts[-1][0]:
            return pts[-1][1]
        for i in range(len(pts) - 1):
            if pts[i][0] <= x <= pts[i + 1][0]:
                dx = pts[i + 1][0] - pts[i][0]
                if dx == 0:
                    return pts[i][1]
                t = (x - pts[i][0]) / dx
                return pts[i][1] + t * (pts[i + 1][1] - pts[i][1])
        return pts[-1][1]

    # ── painting ────────────────────────────────────────────────────────

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QPen, QColor, QFont
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        margin = 30
        gw, gh = w - 2 * margin, h - 2 * margin

        # Background
        p.fillRect(self.rect(), QColor(CURVE_EDITOR_THEME["canvas"]))

        # Grid（任务 C：rgba(255,255,255,13)）
        pen = QPen(QColor(CURVE_EDITOR_THEME["grid"]), 1)
        p.setPen(pen)
        for i in range(5):
            x = margin + gw * i / 4
            y = margin + gh * i / 4
            p.drawLine(int(margin), int(y), int(margin + gw), int(y))
            p.drawLine(int(x), int(margin), int(x), int(margin + gh))

        # Axis labels（任务 C：10px text_secondary）
        font = QFont("Consolas")
        font.setPixelSize(10)
        p.setFont(font)
        p.setPen(QColor(CURVE_EDITOR_THEME["axis_text"]))
        p.drawText(0, margin - 4, w, 16, Qt.AlignmentFlag.AlignCenter, "DMX输出")
        p.drawText(0, h - margin + 12, w, 16, Qt.AlignmentFlag.AlignCenter, "摇杆输入")
        for i in range(5):
            x_lbl = margin + gw * i / 4
            y_lbl = margin + gh * (4 - i) / 4
            p.drawText(int(x_lbl - 14), h - margin + 2, 28, 14,
                       Qt.AlignmentFlag.AlignCenter, f"{i / 4:.1f}")
            p.drawText(2, int(y_lbl - 7), 24, 14,
                       Qt.AlignmentFlag.AlignRight, str(int(i * 255 / 4)))

        # Coordinate transform helpers
        def to_screen(px, py):
            return (margin + px * gw, margin + (1.0 - py / 255.0) * gh)

        # Curve line（任务 C：1.25px 抗锯齿折线）
        curve_pen = QPen(QColor(CURVE_EDITOR_THEME["curve"]), _CURVE_LINE_W)
        p.setPen(curve_pen)
        prev = None
        for px, py in self._points:
            sx, sy = to_screen(px, py)
            if prev:
                p.drawLine(QPointF(prev[0], prev[1]), QPointF(sx, sy))
            prev = (sx, sy)

        # Control points（任务 C：5px 圆点 + hover/拖拽 focus_blue 半透外圈；
        # 颜色含义不变：普通点=聚焦蓝，拖拽中=成功绿）
        lw = _CURVE_LINE_W
        for i, (px, py) in enumerate(self._points):
            sx, sy = to_screen(px, py)
            color = QColor(CURVE_EDITOR_THEME["point_active"]) if i == self._dragging_idx else QColor(CURVE_EDITOR_THEME["point"])
            if i == self._hover_idx and i != self._dragging_idx:
                ring = QPen(QColor(113, 168, 255, 130), lw)   # focus_blue 半透外圈
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(ring)
                p.drawEllipse(QPointF(sx, sy), 7.0, 7.0)
            p.setBrush(color)
            p.setPen(QPen(QColor(CURVE_EDITOR_THEME["point_border"]), lw))
            p.drawEllipse(QPointF(sx, sy), 3.25, 3.25)   # ≈5px 圆点（含描边）

        # Cursor indicator
        if self._cursor_pos is not None:
            cx, _ = to_screen(self._cursor_pos, 128)
            p.setPen(QPen(QColor(CURVE_EDITOR_THEME["cursor"]), 1, Qt.PenStyle.DashLine))
            p.drawLine(int(cx), margin, int(cx), margin + int(gh))

    # ── mouse interaction ───────────────────────────────────────────────

    def _screen_to_value(self, sx: float, sy: float) -> Tuple[float, float]:
        w, h = self.width(), self.height()
        margin = 30
        gw, gh = w - 2 * margin, h - 2 * margin
        px = max(0.0, min(1.0, (sx - margin) / gw))
        py = max(0.0, min(255.0, (1.0 - (sy - margin) / gh) * 255.0))
        return (px, py)

    def _find_point(self, sx: float, sy: float) -> int:
        for i, (px, py) in enumerate(self._points):
            w, h = self.width(), self.height()
            margin = 30
            gw, gh = w - 2 * margin, h - 2 * margin
            psx = margin + px * gw
            psy = margin + (1.0 - py / 255.0) * gh
            if abs(sx - psx) < 8 and abs(sy - psy) < 8:
                return i
        return -1

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self._find_point(event.position().x(), event.position().y())
            if idx >= 0:
                self._dragging_idx = idx
            elif len(self._points) < 8:
                # Add new point
                px, py = self._screen_to_value(event.position().x(), event.position().y())
                self._points.append((px, py))
                self._points.sort(key=lambda p: p[0])
                self._dragging_idx = self._points.index((px, py))
                self.curve_changed.emit()
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            idx = self._find_point(event.position().x(), event.position().y())
            if idx >= 0 and len(self._points) > 2:
                del self._points[idx]
                self.curve_changed.emit()
                self.update()

    def mouseMoveEvent(self, event):
        if self._dragging_idx >= 0:
            px, py = self._screen_to_value(event.position().x(), event.position().y())
            # Clamp X between neighbors
            pts = self._points
            lo = pts[self._dragging_idx - 1][0] + 0.01 if self._dragging_idx > 0 else 0.0
            hi = pts[self._dragging_idx + 1][0] - 0.01 if self._dragging_idx < len(pts) - 1 else 1.0
            px = max(lo, min(hi, px))
            py = max(0.0, min(255.0, py))
            pts[self._dragging_idx] = (px, py)
            self.curve_changed.emit()
            self.update()
        elif event.buttons() == Qt.MouseButton.NoButton:
            # 任务 C：非拖拽悬停时更新 hover 外圈目标点
            idx = self._find_point(event.position().x(), event.position().y())
            if idx != self._hover_idx:
                self._hover_idx = idx
                self.update()

    def enterEvent(self, event):
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover_idx = -1   # 离开画布即清除 hover 外圈（拖拽不受影响）
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging_idx = -1
        self.update()


class BindingMappingDialog(QDialog):
    """调整绑定规则的映射参数，含实时预览。"""

    def __init__(self, rule: JoystickControlBindingRule,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._rule = rule
        self.setWindowTitle(f"调整映射 — {rule.ch_type}")
        self.setMinimumWidth(420)

        lay = QVBoxLayout(self)
        # D 波任务 A：浮窗解剖——内容边距 12 + 标题行
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        lay.addWidget(_kit_title_row("调整映射", self.reject))

        # 信息行
        info = QLabel(f"通道: {rule.ch_type}  物理轴: {rule.physical_axis.upper()}"
                      f"{'  子轴: ' + rule.sub_axis.upper() if rule.sub_axis else ''}"
                      f"{'  模式: ' + rule.space_mode.upper() if rule.space_mode else ''}")
        info.setProperty("hint", True)
        lay.addWidget(info)

        sec_map = PanelSection("映射参数")

        # 曲线预设（离散枚举 → 保留下拉，任务 B）
        self._curve_cb = QComboBox()
        for ct in CURVE_TYPES:
            self._curve_cb.addItem(CURVE_LABELS.get(ct, ct), ct)
        idx = self._curve_cb.findData(rule.curve)
        if idx >= 0:
            self._curve_cb.setCurrentIndex(idx)
        self._curve_cb.setToolTip("曲线形状；也可直接拖拽下方描点自定义")
        self._curve_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        sec_map.add_widget(prop_row("曲线预设", self._curve_cb))

        # 二维曲线描点编辑器
        self._curve_editor = CurveEditor()
        self._curve_editor.set_points([(0.0, float(rule.range_lo)),
                                        (1.0, float(rule.range_hi))])
        self._curve_editor.curve_changed.connect(self._update_preview)
        self._curve_cb.currentIndexChanged.connect(self._on_curve_preset)
        sec_map.add_widget(self._curve_editor)

        # 输出范围
        range_row = QHBoxLayout()
        range_row.setSpacing(6)
        self._range_lo = QSpinBox()
        _range_max = 1000000 if getattr(rule, "target_kind", "") == "effect_param" else 255
        self._range_lo.setRange(0, _range_max)
        self._range_lo.setValue(rule.range_lo)
        self._range_lo.valueChanged.connect(self._on_range_changed)
        lab_lo = QLabel("Lo")
        lab_lo.setProperty("hint", True)
        range_row.addWidget(lab_lo)
        range_row.addWidget(self._range_lo, 1)
        self._range_hi = QSpinBox()
        self._range_hi.setRange(0, _range_max)
        self._range_hi.setValue(rule.range_hi)
        self._range_hi.valueChanged.connect(self._on_range_changed)
        lab_hi = QLabel("Hi")
        lab_hi.setProperty("hint", True)
        range_row.addWidget(lab_hi)
        range_row.addWidget(self._range_hi, 1)
        range_w = QWidget()
        range_w.setLayout(range_row)
        sec_map.add_widget(prop_row("输出范围", range_w))

        # 配平系数（仅手感配平 / gamma 曲线显示）——浮点 → NumSlider（任务 B，
        # 「x」倍率后缀；内部 int 范围 5~30 与旧滑杆一致，信号语义不变）
        self._gamma_sl = NumSlider(0.5, 3.0, float(rule.gamma), decimals=1,
                                   suffix="x")
        self._gamma_sl.valueChanged.connect(lambda: self._update_preview())
        self._gamma_sl.valueChanged.connect(lambda: self._on_gamma_changed())
        self._gamma_label = QLabel("配平系数:")
        self._gamma_label.setStyleSheet(
            f"color:{_TXT_DIM}; font-size:11px; background:transparent;")
        self._gamma_label.setMinimumWidth(96)
        self._gamma_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._gamma_row = prop_row(self._gamma_label, self._gamma_sl)
        sec_map.add_widget(self._gamma_row)

        # 死区（百分比 → NumSlider % 后缀；int 范围 0~50 与旧一致）
        self._deadzone_sl = NumSlider(0.0, 50.0, float(rule.deadzone * 100),
                                      decimals=0, suffix="%")
        # 2026-08-27 D 波：旧旁挂百分比 QLabel 删除（数值已绘在滑槽上），
        # 对应 setText 纯视觉连接随行消除。
        sec_map.add_widget(prop_row("死区", self._deadzone_sl))

        # 反转
        self._invert_cb = QCheckBox("反转轴方向")
        self._invert_cb.setChecked(rule.invert)
        sec_map.add_widget(self._invert_cb)

        lay.addWidget(sec_map)

        # 实时预览
        sec_prev = PanelSection("实时预览")
        pv_lay = QHBoxLayout()
        pv_lay.setSpacing(8)
        self._preview_bar = QProgressBar()
        self._preview_bar.setRange(0, 100)
        self._preview_bar.setValue(50)
        self._preview_bar.setFormat("摇杆: %v%% (点击选择测试点)")
        self._preview_bar.mousePressEvent = self._on_preview_bar_click
        pv_lay.addWidget(self._preview_bar, 1)
        arrow_lbl = QLabel("→")
        arrow_lbl.setStyleSheet(f"color:{_TXT_DIM}; background:transparent;")
        pv_lay.addWidget(arrow_lbl)
        self._preview_val = QLabel("127")
        self._preview_val.setStyleSheet(
            f"color:{_C_SUCCESS};font-size:18px;font-weight:bold;"
            f"min-width:40px;background:transparent;")
        pv_lay.addWidget(self._preview_val)
        prev_body = QWidget()
        prev_body.setLayout(pv_lay)
        sec_prev.add_widget(prev_body)
        lay.addWidget(sec_prev)

        # 预览更新信号
        self._deadzone_sl.valueChanged.connect(self._update_preview)
        self._invert_cb.toggled.connect(self._update_preview)
        self._curve_editor.curve_changed.connect(self._update_preview)
        self._on_curve_preset()  # init visibility + preset points

        # 实时摇杆预览：连接物理驱动，推摇杆看映射输出
        self._live_axis_val = 0.5  # 当前轴值
        try:
            from .physical_joystick_driver import get_physical_joystick_driver
            driver = get_physical_joystick_driver()
            driver.axis3d_changed.connect(self._on_live_axis)
        except Exception:
            pass

        # 按钮（任务 A：确定=primary / 取消=cancel 语义）
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self._apply)
        btn_box.rejected.connect(self.reject)
        ok_btn = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        cancel_btn = btn_box.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_btn is not None:
            ok_btn.setObjectName("primary")
        if cancel_btn is not None:
            cancel_btn.setProperty("role", "cancel")
        lay.addWidget(btn_box)
        apply_dialog_chrome(self)   # D 波任务 A：统一浮窗外框

        self._update_preview()

    def _on_curve_preset(self):
        """曲线预设：根据选择的曲线类型重置描点为标准形状。"""
        curve = self._curve_cb.currentData()
        # 显示/隐藏配平系数滑块（仅 gamma 和 feel_balance）
        show_gamma = curve in ("gamma", "feel_balance")
        self._gamma_row.setVisible(show_gamma)
        if curve == "feel_balance":
            self._gamma_label.setText("配平系数:")
            self._gamma_sl.setToolTip("手感配平强度：越高近中位放大越多，越低越接近线性（右键输入数值）")
        else:
            self._gamma_label.setText("Gamma:")
            self._gamma_sl.setToolTip("Gamma 曲线强度（右键输入数值）")
        #
        lo = float(self._range_lo.value())
        hi = float(self._range_hi.value())
        if curve == "linear":
            pts = [(0.0, lo), (1.0, hi)]
        elif curve == "gamma":
            pts = [(0.0, lo), (0.25, lo + (hi - lo) * 0.125),
                   (0.5, lo + (hi - lo) * 0.35),
                   (0.75, lo + (hi - lo) * 0.65),
                   (1.0, hi)]
        elif curve == "s_curve":
            pts = [(0.0, lo), (0.25, lo + (hi - lo) * 0.1),
                   (0.5, lo + (hi - lo) * 0.5),
                   (0.75, lo + (hi - lo) * 0.9),
                   (1.0, hi)]
        elif curve == "stepped":
            s = max(2, 4)
            pts = []
            for i in range(s):
                pts.append((i / (s - 1), lo + (hi - lo) * (i // (s // 2)) * 0.5 if s > 2 else lo + (hi - lo) * (1 if i > 0 else 0)))
                # simpler: just staircase
            pts = [(i / (s - 1), lo if i < s // 2 else hi) for i in range(s)]
        elif curve == "inverted":
            pts = [(0.0, hi), (1.0, lo)]
        elif curve == "feel_balance":
            # 手感配平：根据当前配平系数生成描点
            balance = self._gamma_sl.value() / 10.0
            mid = (lo + hi) / 2.0
            half = (hi - lo) / 2.0
            amplify = 0.5 + balance * 0.15  # 系数越高越陡
            pts = [(0.0, lo),
                   (0.10, mid - half * (1.0 - balance * 0.12)),
                   (0.25, mid - half * 0.3),
                   (0.50, mid),
                   (0.75, mid + half * 0.3),
                   (0.90, mid + half * (1.0 - balance * 0.12)),
                   (1.0, hi)]
        else:
            pts = [(0.0, lo), (1.0, hi)]
        self._curve_editor.set_points(pts)
        self._update_preview()

    def _on_preview_bar_click(self, event):
        """点击预览条选择测试输入位置（手动模式，禁用实时跟随）。"""
        w = self._preview_bar.width()
        self._live_axis_val = event.position().x() / max(1, w)
        self._update_preview(self._live_axis_val)

    def _on_live_axis(self, x: float, y: float, z: float, w: float = 0.5):
        """实时摇杆输入 → 更新预览。"""
        axis_map = {"x": x, "y": y, "z": z, "w": w}
        raw = axis_map.get(self._rule.physical_axis, 0.5)
        # 应用死区 + 反转
        norm = _normalize_axis(raw, self._deadzone_sl.value() / 100.0,
                               self._invert_cb.isChecked())
        self._live_axis_val = norm
        self._update_preview(norm)

    def _on_gamma_changed(self):
        """配平系数/Gamma 变化时实时更新曲线描点。"""
        curve = self._curve_cb.currentData()
        if curve in ("feel_balance", "gamma"):
            self._on_curve_preset()

    def _on_range_changed(self):
        """输出范围变化时更新曲线端点Y值。"""
        lo = float(self._range_lo.value())
        hi = float(self._range_hi.value())
        pts = self._curve_editor.get_points()
        if pts and len(pts) >= 2:
            pts[0] = (pts[0][0], lo)
            pts[-1] = (pts[-1][0], hi)
            self._curve_editor.set_points(pts)
        self._update_preview()

    def _update_preview(self, test_input: float = -1):
        """实时预览：默认使用当前摇杆实时轴值。曲线编辑器本身就是预览。"""
        editor = self._curve_editor
        if test_input < 0:
            test_input = getattr(self, '_live_axis_val', 0.5)
        test_input = max(0.0, min(1.0, test_input))
        # 跳过死区直接在编辑器上评估（曲线编辑器已包含完整的输入-输出映射）
        mapped = int(round(editor.evaluate(test_input)))
        if getattr(self._rule, "target_kind", "") == "effect_param":
            mapped = max(self._rule.range_lo, min(self._rule.range_hi, mapped))
        else:
            mapped = max(0, min(255, mapped))
        self._preview_bar.setValue(int(test_input * 100))
        self._preview_val.setText(str(mapped))
        # 更新编辑器的光标指示线
        editor.set_cursor_pos(test_input)

    def _apply(self):
        self._rule.invert = self._invert_cb.isChecked()
        self._rule.deadzone = self._deadzone_sl.value() / 100.0
        self._rule.range_lo = self._range_lo.value()
        self._rule.range_hi = self._range_hi.value()
        self._rule.curve = self._curve_cb.currentData()
        self._rule.gamma = self._gamma_sl.value() / 10.0
        get_joystick_bind_manager().save()
        self.accept()

    def closeEvent(self, event):
        try:
            from .physical_joystick_driver import get_physical_joystick_driver
            get_physical_joystick_driver().axis3d_changed.disconnect(
                self._on_live_axis)
        except (TypeError, RuntimeError):
            pass
        super().closeEvent(event)

# ═══════════════════════════════════════════════════════════════════════════
#  6. BindingManagerDialog — 绑定管理子窗口
# ═══════════════════════════════════════════════════════════════════════════

class BindingManagerDialog(QDialog):
    """非模态表格窗口：查看和管理所有摇杆控件绑定。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("摇杆绑定管理")
        self.setMinimumSize(750, 400)
        self._signals_connected = False

        lay = QVBoxLayout(self)
        # D 波任务 A：浮窗解剖——内容边距 12 + 标题行
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        lay.addWidget(_kit_title_row("摇杆绑定管理", self.close))

        # 连接状态栏
        status_row = QHBoxLayout()
        self._conn_status = QLabel("○ 未连接")
        self._conn_status.setStyleSheet(f"color:{FG_DIM};font-size:12px;")
        status_row.addWidget(self._conn_status)
        status_row.addStretch()
        cfg_btn = QPushButton("设备配置…")
        cfg_btn.setToolTip("打开外接摇杆设备配置窗口")
        cfg_btn.setCursor(Qt.CursorShape.PointingHandCursor)   # §9 手型光标
        cfg_btn.clicked.connect(self._open_device_config)
        status_row.addWidget(cfg_btn)
        lay.addLayout(status_row)

        # 表格（外观统一走全局主题；2026-08-27 D 波按 §3 删除行内重复 QSS）
        self._table = QTableWidget()
        self._table.setColumnCount(8)
        self._table.setHorizontalHeaderLabels(
            ["通道", "子轴", "空间模式", "物理轴", "曲线", "范围", "", ""])
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setColumnWidth(1, 40)
        self._table.setColumnWidth(2, 60)
        self._table.setColumnWidth(3, 50)
        self._table.setColumnWidth(4, 60)
        self._table.setColumnWidth(5, 70)
        self._table.setColumnWidth(6, 60)
        self._table.setColumnWidth(7, 60)
        lay.addWidget(self._table)

        # 空态（任务 E / §10.6）：无绑定时以 EmptyState 取代空白表格区
        self._empty_state = EmptyState(
            "暂无摇杆绑定",
            "在效果/通道控制上点击绑定按钮，或使用「设备配置…」后完成轴检测",
            icon_key="device-gamepad")
        self._empty_state.setVisible(False)
        lay.addWidget(self._empty_state, 1)

        # 底部按钮
        btn_row = QHBoxLayout()
        clear_btn = QPushButton("清除全部绑定")
        clear_btn.setToolTip("清空所有摇杆→输出绑定规则（不可撤销）")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setStyleSheet(
            f"QPushButton{{background:rgba(153,22,22,55);color:{FG_RED};"
            f"border:1px solid rgba(227,104,85,80);border-radius:4px;"
            f"padding:2px 18px;min-height:24px;}}"
            f"QPushButton:hover{{background:rgba(153,22,22,95);}}")   # error_red 语义保留
        clear_btn.clicked.connect(self._clear_all)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setProperty("role", "cancel")     # 任务 A：关闭=cancel 次按钮
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)
        apply_dialog_chrome(self)

    def showEvent(self, event):
        """每次显示时连接信号 + 刷新状态（singleton 复用支持）。"""
        super().showEvent(event)
        self._connect_signals()
        self._refresh()
        self._refresh_conn_status()

    def _connect_signals(self):
        if self._signals_connected:
            return
        mgr = get_joystick_bind_manager()
        mgr.binding_added.connect(self._on_binding_updated)
        mgr.binding_removed.connect(self._on_binding_updated)
        mgr.bindings_cleared.connect(self._on_binding_updated)
        driver = get_physical_joystick_driver()
        driver.connection_status_changed.connect(self._on_conn_changed)
        self._signals_connected = True

    def _refresh_conn_status(self):
        """从驱动读取当前连接状态并更新显示。"""
        driver = get_physical_joystick_driver()
        connected = driver.is_connected()
        if connected:
            devs = driver.get_devices()
            name = ""
            for d in devs:
                if hasattr(d, 'index') and d.index == driver._device_index:
                    name = d.name
                    break
            self._conn_status.setText(f"● 已连接: {name}")
            self._conn_status.setStyleSheet(f"color:{FG_GREEN};font-size:12px;")
        else:
            self._conn_status.setText("○ 未连接")
            self._conn_status.setStyleSheet(f"color:{FG_DIM};font-size:12px;")

    def _on_binding_updated(self, *args):
        """Slot for binding_added/removed/cleared — all trigger a table refresh."""
        self._refresh()

    def _refresh(self):
        mgr = get_joystick_bind_manager()
        bindings = mgr.get_all_bindings()
        self._table.setRowCount(len(bindings))
        # 空态切换（视觉层；不改变任何刷新/信号逻辑）
        self._table.setVisible(bool(bindings))
        self._empty_state.setVisible(not bindings)
        # 行内小按钮：默认灰面 / 危险红字（token 化语义色）
        btn_style = (
            f"QPushButton{{background:{BG_CARD};color:{FG_ACCENT};"
            f"border:1px solid {BORDER};border-radius:3px;padding:2px 8px;"
            f"font-size:10px;}}"
            f"QPushButton:hover{{background:{_BTN_HOVER};}}")
        danger_style = (
            f"QPushButton{{background:{BG_CARD};color:{FG_RED};"
            f"border:1px solid {BORDER};border-radius:3px;padding:2px 8px;"
            f"font-size:10px;}}"
            f"QPushButton:hover{{background:{_BTN_HOVER};}}")
        for i, rule in enumerate(bindings):
            # effect 参数绑定标注「效果:」前缀，与通道绑定统一列表管理
            if getattr(rule, "target_kind", "channel") == "effect_param":
                _disp = f"效果: {rule.param_label or rule.ch_type}"
                if getattr(rule, "channel", ""):
                    _disp += f"（{rule.channel}）"
            else:
                _disp = rule.ch_type
            self._table.setItem(i, 0, QTableWidgetItem(_disp))
            self._table.setItem(i, 1, QTableWidgetItem(
                rule.sub_axis.upper() if rule.sub_axis else "—"))
            mode_text = rule.space_mode.upper() if rule.space_mode else "通用"
            self._table.setItem(i, 2, QTableWidgetItem(mode_text))
            self._table.setItem(i, 3, QTableWidgetItem(
                _AXIS_LABELS.get(rule.physical_axis, rule.physical_axis.upper())))
            self._table.setItem(i, 4, QTableWidgetItem(
                CURVE_LABELS.get(rule.curve, rule.curve)))
            self._table.setItem(i, 5, QTableWidgetItem(
                f"{rule.range_lo}-{rule.range_hi}"))
            # 调整按钮
            adj_btn = QPushButton("调整")
            adj_btn.setStyleSheet(btn_style)
            adj_btn.setToolTip("打开映射调整（曲线/范围/死区）")
            adj_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            adj_btn.clicked.connect(lambda checked, r=rule: self._edit_binding(r))
            self._table.setCellWidget(i, 6, adj_btn)
            # 取消按钮
            del_btn = QPushButton("取消")
            del_btn.setStyleSheet(danger_style)
            del_btn.setToolTip("解除该条绑定")
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.clicked.connect(lambda checked, r=rule: self._remove_binding(r))
            self._table.setCellWidget(i, 7, del_btn)

    def _edit_binding(self, rule: JoystickControlBindingRule):
        dlg = BindingMappingDialog(rule, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._refresh()

    def _remove_binding(self, rule: JoystickControlBindingRule):
        mgr = get_joystick_bind_manager()
        if getattr(rule, "target_kind", "channel") == "effect_param":
            mgr.remove_effect_param_binding(rule.channel, rule.param_key)
        else:
            mgr.remove_binding(rule.ch_type, rule.space_mode)
        self._refresh()

    def _clear_all(self):
        reply = QMessageBox.question(
            self, "确认清除", "确定要清除全部摇杆绑定吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        if reply == QMessageBox.StandardButton.Yes:
            get_joystick_bind_manager().clear_all_bindings()

    def _on_conn_changed(self, connected: bool, name: str):
        self._conn_status.setText(
            f"● 已连接: {name}" if connected else "○ 未连接")
        self._conn_status.setStyleSheet(
            f"color:{FG_GREEN if connected else FG_DIM};font-size:12px;")

    def _open_device_config(self):
        from .joystick_control_binding_device import ExternalJoystickConfigDialog
        dlg = ExternalJoystickConfigDialog(self)
        dlg.exec()

    def closeEvent(self, event):
        """断开信号后关闭。showEvent 会在下次打开时重新连接。"""
        self._disconnect_signals()
        super().closeEvent(event)

    def _disconnect_signals(self):
        if not self._signals_connected:
            return
        mgr = get_joystick_bind_manager()
        try:
            mgr.binding_added.disconnect(self._on_binding_updated)
        except (TypeError, RuntimeError):
            pass
        try:
            mgr.binding_removed.disconnect(self._on_binding_updated)
        except (TypeError, RuntimeError):
            pass
        try:
            mgr.bindings_cleared.disconnect(self._on_binding_updated)
        except (TypeError, RuntimeError):
            pass
        try:
            driver = get_physical_joystick_driver()
            driver.connection_status_changed.disconnect(self._on_conn_changed)
        except (TypeError, RuntimeError):
            pass
        self._signals_connected = False


# ═══════════════════════════════════════════════════════════════════════════
#  7. ExternalJoystickConfigDialog — 设备配置
# ═══════════════════════════════════════════════════════════════════════════