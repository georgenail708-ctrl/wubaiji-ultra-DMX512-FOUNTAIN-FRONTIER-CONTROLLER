"""
joystick_control_binding_popups.py — 摇杆轴检测弹窗
────────────────────────────────────────────────────────────────────────────
AxisDetectPopup        — 单轴自动检测（普通控件用）
MultiAxisDetectPopup   — 多轴顺序检测（XYJoystick 用）
────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations
from typing import Dict, List, Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QMessageBox, QLabel,
    QDialogButtonBox, QProgressBar, QWidget, QPushButton,
)
from PyQt6.QtCore import Qt, QTimer

from .physical_joystick_driver import get_physical_joystick_driver
from .joystick_control_binding import (
    _AXIS_LABELS, _DETECT_TIMEOUT_S, _DETECT_THRESHOLD,
    BG_DARK, BG_CARD, BG_INPUT, FG_MAIN, FG_DIM, FG_ACCENT, FG_CYAN, FG_GREEN, BORDER,
)

# D 波任务 A：浮窗外框标准件
from .qascade_theme import (
    SELECT_BLUE as _SEL_BLUE,
    BG_BUTTON_HOVER as _BTN_HOVER,
    TEXT_PRIMARY as _TXT,
    TEXT_SECONDARY as _TXT_DIM,
    BRIGHT_INFO as _C_INFO,
    BRIGHT_SUCCESS as _C_OK,
    ACTIVE_ORANGE as _C_W,
)
from .widget_kit import apply_dialog_chrome


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


def _axis_bar(label: str, color: str) -> QProgressBar:
    """检测弹层轴值条（扁平风：BG_INPUT 底 + 主题令牌描边/填充）。"""
    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.setValue(50)
    bar.setFormat(f"{label}: %v%%")
    bar.setTextVisible(True)
    bar.setStyleSheet(
        f"QProgressBar{{background:{BG_INPUT};border:1px solid {BORDER};"
        f"border-radius:4px;min-height:16px;text-align:center;"
        f"color:{_TXT_DIM};font-size:11px;}}"
        f"QProgressBar::chunk{{background:{color};border-radius:3px;}}")
    return bar


def _mark_primary(btn: QPushButton):
    """确认按钮 primary 语义 + 显式禁用态（避免蓝底常亮误导）。"""
    btn.setObjectName("primary")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        "QPushButton#primary:disabled{background:#303030;border-color:#262626;"
        "color:#8B8B8B;}")

_AXIS_IDX = {"x": 0, "y": 1, "z": 2, "w": 3}
# 单帧大幅拨动豁免阈值：快速点推/拨动（行程大但可能回位、净位移≈0）仍可检测；
# 握持抖动幅度远小于该值，不受影响（JOY-NET-FLICK）
_FLICK_STEP = 0.08


def _net_motion_ok(best: str, acc: dict, cur: tuple, base: tuple,
                   step_delta: float) -> bool:
    """抖动防护：累计位移须有显著净位移支撑（当前-基线同向且占比 ≥50%）。

    来回震荡的握持抖动（绝对值累计大、净位移≈0）不触发；慢速单向推动
    净位移=累计位移，仍可正常检测。单帧大幅拨动（|step| ≥ _FLICK_STEP，
    快拨/点推手势）豁免净位移判定——行程大但回位后净≈0 也视为真实动作。
    base 为 None（未建立基线）时放行。
    """
    if base is None:
        return True
    if step_delta >= _FLICK_STEP:
        return True
    idx = _AXIS_IDX.get(best, 0)
    net = abs(cur[idx] - base[idx])
    return net >= acc[best] * 0.5

class AxisDetectPopup(QDialog):
    """模态弹窗：等待用户推动摇杆来自动检测目标轴。"""

    def __init__(self, ch_type: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.ch_type = ch_type
        self.detected_axis: Optional[str] = None
        self._countdown = _DETECT_TIMEOUT_S
        # ★ 活动轴判定 = 逐轴累计位移（相对上一次采样），只统计"当前真的在动"
        #   的轴；停在非中位的轴不产生位移，不会在检测开始时抢先判定。
        self._move_acc: Dict[str, float] = {"x": 0.0, "y": 0.0, "z": 0.0, "w": 0.0}
        self._last_axis_sample: Optional[tuple] = None
        self._base_sample: Optional[tuple] = None   # 触发判定用的净位移基线

        self.setWindowTitle(f"绑定摇杆轴 — {ch_type}")
        self.setMinimumWidth(360)

        lay = QVBoxLayout(self)
        # D 波任务 A：浮窗解剖——内容边距 12 + 标题行
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        lay.addWidget(_kit_title_row(f"绑定摇杆轴 — {ch_type}", self.reject))

        # 状态标签
        self._status = QLabel(f"请推动要绑定的摇杆轴…\n（{self._countdown} 秒后超时）")
        self._status.setStyleSheet(f"color:{_TXT};font-size:12px;")
        self._status.setWordWrap(True)
        lay.addWidget(self._status)

        # 检测结果显示
        self._detected_label = QLabel("")
        self._detected_label.setStyleSheet(
            f"color:{_C_OK};font-size:14px;font-weight:bold;"
            f"background:{BG_CARD};border:1px solid rgba(86,182,91,90);"
            f"border-radius:4px;padding:12px;")
        self._detected_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detected_label.setVisible(False)
        lay.addWidget(self._detected_label)

        # 轴值可视化
        self._x_bar = _axis_bar("X", _SEL_BLUE)
        lay.addWidget(self._x_bar)

        self._y_bar = _axis_bar("Y", _C_INFO)
        lay.addWidget(self._y_bar)

        # Z/W 轴值条（与 X/Y 统一样式）
        self._z_bar2 = _axis_bar("Z", _C_OK)
        lay.addWidget(self._z_bar2)
        self._w_bar2 = _axis_bar("W", _C_W)
        lay.addWidget(self._w_bar2)

        # 按钮（任务 A：确认=primary / 取消=cancel）
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel)
        btn_box.rejected.connect(self.reject)
        self._confirm_btn = btn_box.addButton("确认绑定 →",
                                               QDialogButtonBox.ButtonRole.AcceptRole)
        self._confirm_btn.setEnabled(False)
        self._confirm_btn.clicked.connect(self._on_confirm)
        for b in btn_box.buttons():
            if b is not self._confirm_btn:
                b.setProperty("role", "cancel")
                b.setCursor(Qt.CursorShape.PointingHandCursor)
        _mark_primary(self._confirm_btn)
        lay.addWidget(btn_box)
        apply_dialog_chrome(self)

        # 倒计时定时器
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._tick_countdown)
        self._countdown_timer.start()

        # 监听物理摇杆（4轴）
        driver = get_physical_joystick_driver()
        driver.axis3d_changed.connect(self._on_driver_axis4d)

        self._driver_connected = True

    def _tick_countdown(self):
        self._countdown -= 1
        if self._countdown <= 0:
            self._countdown_timer.stop()
            if self.detected_axis is None:
                self._status.setText("未检测到摇杆动作\n点击[重试]或[取消]")
                self._confirm_btn.setVisible(False)
            return
        if self.detected_axis is None:
            self._status.setText(f"请推动要绑定的摇杆轴…\n（{self._countdown} 秒后超时）")

    def _on_driver_axis4d(self, x: float, y: float, z: float, w: float = 0.5):
        if self.detected_axis is not None:
            return
        self._x_bar.setValue(int(x * 100))
        self._y_bar.setValue(int(y * 100))
        self._z_bar2.setValue(int(z * 100))
        self._w_bar2.setValue(int(w * 100))
        # ★ 位移累计判定：与轴停在哪无关，只累计采样间真实位移。首个采样仅
        #   建立"上一次位置"基线（位移基线，非中心点基线），不参与判定。
        prev = self._last_axis_sample
        self._last_axis_sample = (x, y, z, w)
        if prev is None:
            self._base_sample = (x, y, z, w)
            return
        px, py, pz, pw = prev
        # 位移累计：只累计采样间真实位移，与轴停在哪无关（停在非中位的轴
        # 驱动不产生事件 → 零累计，不会抢先于实际运动的轴）。不设逐采样
        # 死区——慢速匀速推动（每步增量 < 0.01）也必须能累计到阈值。
        d_x, d_y, d_z, d_w = abs(x - px), abs(y - py), abs(z - pz), abs(w - pw)
        move = {
            "x": self._move_acc["x"] + d_x,
            "y": self._move_acc["y"] + d_y,
            "z": self._move_acc["z"] + d_z,
            "w": self._move_acc["w"] + d_w,
        }
        self._move_acc = move
        best = max(move, key=move.get)
        step = {"x": d_x, "y": d_y, "z": d_z, "w": d_w}.get(best, 0.0)
        if move[best] > _DETECT_THRESHOLD and _net_motion_ok(
                best, move, (x, y, z, w), self._base_sample, step):
            self._detect(best)

    def _detect(self, axis: str):
        self.detected_axis = axis
        self._countdown_timer.stop()
        label = _AXIS_LABELS.get(axis, axis.upper())
        self._detected_label.setText(f"✓ 检测到: {label}")
        self._detected_label.setVisible(True)
        self._status.setText("请确认绑定，或继续推动其他轴重新检测")
        self._confirm_btn.setEnabled(True)

    def _on_confirm(self):
        if self.detected_axis:
            self.accept()
        else:
            QMessageBox.warning(self, "未检测", "请先推动摇杆轴再确认。")

    def closeEvent(self, event):
        driver = get_physical_joystick_driver()
        try:
            driver.axis3d_changed.disconnect(self._on_driver_axis4d)
        except (TypeError, RuntimeError):
            pass
        self._driver_connected = False
        super().closeEvent(event)


# ═══════════════════════════════════════════════════════════════════════════
#  4. MultiAxisDetectPopup — 多轴顺序检测（XYJoystick 专用）
# ═══════════════════════════════════════════════════════════════════════════

class MultiAxisDetectPopup(QDialog):
    """模态弹窗：依次检测 X → Y → (Z) 三个子轴对应的物理轴。"""

    def __init__(self, group_name: str, sub_axes: List[str],
                 parent: Optional[QWidget] = None):
        """
        Args:
            group_name: 控件组名，如 "Motor XYZ"
            sub_axes: 子轴列表，如 ["x","y"] (2D) 或 ["x","y","z"] (3D)
        """
        super().__init__(parent)
        self.group_name = group_name
        self.sub_axes = list(sub_axes)
        self._step = 0
        self._detected: Dict[str, str] = {}   # sub_axis → physical_axis
        self._locked_axes: List[str] = []      # 已锁定的物理轴
        self._countdown = _DETECT_TIMEOUT_S
        # ★ 活动轴判定 = 逐轴累计位移（相对上一次采样），只统计"当前真的在动"
        #   的轴；停在非中位的轴不产生位移，不会抢先于实际运动的轴。每步重置。
        self._move_acc: Dict[str, float] = {"x": 0.0, "y": 0.0, "z": 0.0, "w": 0.0}
        self._last_axis_sample: Optional[tuple] = None
        self._base_sample: Optional[tuple] = None   # 触发判定用的净位移基线

        n = len(self.sub_axes)
        self.setWindowTitle(f"绑定摇杆轴 — {group_name} ({n} 步)")
        self.setMinimumWidth(400)

        lay = QVBoxLayout(self)
        # D 波任务 A：浮窗解剖——内容边距 12 + 标题行
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(10)
        lay.addWidget(_kit_title_row(f"绑定摇杆轴 — {group_name}", self.reject))

        # 步骤标题
        self._step_title = QLabel()
        self._step_title.setStyleSheet(
            "color:#E6E6E6;font-size:12px;font-weight:bold;background:transparent;")
        lay.addWidget(self._step_title)

        # 状态
        self._status = QLabel()
        self._status.setStyleSheet(f"color:{_TXT};font-size:12px;")
        self._status.setWordWrap(True)
        lay.addWidget(self._status)

        # 已检测汇总
        self._summary = QLabel("")
        self._summary.setStyleSheet(
            f"color:{_C_OK};font-size:12px;"
            f"background:{BG_CARD};border:1px solid rgba(86,182,91,90);"
            f"border-radius:4px;padding:8px;")
        self._summary.setVisible(False)
        lay.addWidget(self._summary)

        # 轴值条
        self._x_bar = _axis_bar("X", _SEL_BLUE)
        self._y_bar = _axis_bar("Y", _C_INFO)
        lay.addWidget(self._x_bar)
        lay.addWidget(self._y_bar)
        # Z 轴进度条（3 轴时显示）
        self._z_bar = None
        if len(self.sub_axes) >= 3:
            self._z_bar = _axis_bar("Z", _C_OK)
            lay.addWidget(self._z_bar)
        # W 轴进度条（始终显示，供参考，与 Z 轴统一样式）
        self._w_bar = _axis_bar("W", _C_W)
        if self._z_bar is None:
            # No Z → W uses same compact style
            pass
        lay.addWidget(self._w_bar)

        # 按钮（任务 A：确认=primary / 取消=cancel / 重试=普通次操作）
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel)
        btn_box.rejected.connect(self.reject)
        self._confirm_btn = btn_box.addButton("确认全部绑定 →",
                                               QDialogButtonBox.ButtonRole.AcceptRole)
        self._confirm_btn.setEnabled(False)
        self._confirm_btn.clicked.connect(self._on_confirm)
        self._retry_btn = btn_box.addButton("重试当前步骤",
                                             QDialogButtonBox.ButtonRole.ActionRole)
        self._retry_btn.clicked.connect(self._retry_step)
        self._retry_btn.setVisible(False)
        for b in btn_box.buttons():
            if b is not self._confirm_btn:
                b.setProperty("role", "cancel")
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                if b is self._retry_btn:
                    b.setToolTip("重新等待当前子轴动作（已锁定的物理轴会解除）")
        _mark_primary(self._confirm_btn)
        lay.addWidget(btn_box)
        apply_dialog_chrome(self)

        # 定时器
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._tick_countdown)

        # 监听（使用 3D 信号获得完整的 x/y/z 轴值）
        driver = get_physical_joystick_driver()
        driver.axis3d_changed.connect(self._on_driver_axis3d)
        self._driver_connected = True

        self._begin_step()

    def _begin_step(self):
        if self._step >= len(self.sub_axes):
            self._all_done()
            return
        # 每步重置位移累计：上一步已检测轴的位移不得带入下一步判定
        self._move_acc = {"x": 0.0, "y": 0.0, "z": 0.0, "w": 0.0}
        self._last_axis_sample = None
        self._base_sample = None
        self._countdown = _DETECT_TIMEOUT_S
        sub = self.sub_axes[self._step]
        label = _AXIS_LABELS.get(sub, sub.upper())
        n = len(self.sub_axes)
        self._step_title.setText(f"步骤 {self._step + 1}/{n}：请推动摇杆 {label}")
        self._status.setText(f"等待 {label} 动作…（{self._countdown} 秒）")
        self._retry_btn.setVisible(False)
        self._countdown_timer.start()

    def _tick_countdown(self):
        self._countdown -= 1
        if self._countdown <= 0:
            self._countdown_timer.stop()
            self._status.setText("超时：未检测到轴动作")
            self._retry_btn.setVisible(True)
            return
        sub = self.sub_axes[self._step]
        label = _AXIS_LABELS.get(sub, sub.upper())
        self._status.setText(f"等待 {label} 动作…（{self._countdown} 秒）")

    def _on_driver_axis3d(self, x: float, y: float, z: float, w: float = 0.5):
        self._x_bar.setValue(int(x * 100))
        self._y_bar.setValue(int(y * 100))
        if self._z_bar:
            self._z_bar.setValue(int(z * 100))
        self._w_bar.setValue(int(w * 100))
        if self._step >= len(self.sub_axes):
            return
        # ★ 位移累计判定：与轴停在哪无关，只累计采样间真实位移；首个采样仅
        #   建立"上一次位置"基线（位移基线，非中心点基线），不参与判定。
        prev = self._last_axis_sample
        self._last_axis_sample = (x, y, z, w)
        if prev is None:
            self._base_sample = (x, y, z, w)
            return
        px, py, pz, pw = prev
        # 位移累计：只累计采样间真实位移，与轴停在哪无关。不设逐采样死区
        # ——慢速匀速推动（每步增量 < 0.01）也必须能累计到阈值。
        d_x, d_y, d_z, d_w = abs(x - px), abs(y - py), abs(z - pz), abs(w - pw)
        move = {
            "x": self._move_acc["x"] + d_x,
            "y": self._move_acc["y"] + d_y,
            "z": self._move_acc["z"] + d_z,
            "w": self._move_acc["w"] + d_w,
        }
        self._move_acc = move    # 完整累计（4 键常驻，锁定轴也继续累计）
        # 检测候选排除已锁定物理轴：上一步骤轴的松手回中位移不得误判
        cand = {k: v for k, v in move.items() if k not in (self._locked_axes or ())}
        if not cand:
            return
        best = max(cand, key=cand.get)
        step = {"x": d_x, "y": d_y, "z": d_z, "w": d_w}.get(best, 0.0)
        if cand[best] > _DETECT_THRESHOLD and _net_motion_ok(
                best, cand, (x, y, z, w), self._base_sample, step):
            detected = best
        else:
            return

        self._countdown_timer.stop()
        sub = self.sub_axes[self._step]
        self._detected[sub] = detected
        if detected not in self._locked_axes:
            self._locked_axes.append(detected)   # 本步物理轴锁定，防止下步回中位移误判
        label = _AXIS_LABELS.get(sub, sub.upper())
        phys_label = _AXIS_LABELS.get(detected, detected.upper())
        self._status.setText(f"✓ {label} → 物理 {phys_label}")

        # 更新汇总
        lines = []
        for s, p in self._detected.items():
            sl = _AXIS_LABELS.get(s, s.upper())
            pl = _AXIS_LABELS.get(p, p.upper())
            lines.append(f"  {sl} → 物理 {pl}")
        self._summary.setText("已检测：\n" + "\n".join(lines))
        self._summary.setVisible(True)

        self._step += 1
        if self._step >= len(self.sub_axes):
            self._all_done()
        else:
            self._begin_step()

    def _all_done(self):
        self._step_title.setText("✓ 全部检测完成")
        self._status.setText("请确认绑定，或点击[重试当前步骤]重新检测最后一步")
        self._confirm_btn.setEnabled(True)
        self._retry_btn.setVisible(True)  # 允许重试最后一步

    def _retry_step(self):
        """重试当前步骤：超时状态重跑正在等待的步骤，全部完成后重跑最后一步。"""
        if self._step >= len(self.sub_axes):
            target = len(self.sub_axes) - 1
        else:
            target = self._step
        sub = self.sub_axes[target]
        phys = self._detected.pop(sub, None)
        # 解除该步骤物理轴锁定：否则重试时该轴被候选排除、永远检测不到
        if phys is not None and phys in self._locked_axes:
            self._locked_axes.remove(phys)
        if self._step > target:
            self._step = target
        self._confirm_btn.setEnabled(False)
        self._retry_btn.setVisible(False)
        self._begin_step()

    def _on_confirm(self):
        if len(self._detected) >= len(self.sub_axes):
            self.accept()
        else:
            QMessageBox.warning(self, "未完成", "请完成所有步骤再确认。")

    def get_detected_map(self) -> Dict[str, str]:
        """返回 {sub_axis: physical_axis} 映射。"""
        return dict(self._detected)

    def closeEvent(self, event):
        driver = get_physical_joystick_driver()
        try:
            driver.axis3d_changed.disconnect(self._on_driver_axis3d)
        except (TypeError, RuntimeError):
            pass
        self._driver_connected = False
        super().closeEvent(event)


# ═══════════════════════════════════════════════════════════════════════════
#  5. BindingMappingDialog — 映射调整
# ═══════════════════════════════════════════════════════════════════════════

