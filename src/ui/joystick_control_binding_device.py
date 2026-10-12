"""
joystick_control_binding_device.py — 统一外接摇杆设备配置窗口
────────────────────────────────────────────────────────────────────────────
ExternalJoystickConfigDialog — 统一的设备管理窗口，支持：
  - HID 游戏摇杆 (pygame.joystick) — 轴编号绑定 + 死区 + 反转
  - MIDI 摇杆 (pygame.midi) — CC 号映射 + MIDI 通道 + 死区 + 反转
  - 共用轴测试区域 (X/Y/Z 进度条)
────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations
from typing import Optional

from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QFormLayout,
    QPushButton, QLabel, QDialog, QDialogButtonBox,
    QComboBox, QProgressBar,
    QCheckBox, QSpinBox, QStackedWidget, QWidget,
    QSizePolicy, QScrollArea, QFrame,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon

from .physical_joystick_driver import get_physical_joystick_driver
from .joystick_control_binding import (
    BG_INPUT, FG_GREEN,
    get_joystick_bind_manager,
)
from .qascade_theme import (
    app_icon_path,
    SELECT_BLUE as _SEL_BLUE,
    BG_BUTTON_HOVER as _BTN_HOVER,
    TEXT_PRIMARY as _TXT,
    TEXT_SECONDARY as _TXT_DIM,
    FOCUS_BLUE as _C_FOCUS,
    BRIGHT_INFO as _C_INFO,
    BRIGHT_SUCCESS as _C_OK,
    ACTIVE_ORANGE as _C_W,
)
# D 波任务 A/B：浮窗解剖标准件
from .widget_kit import NumSlider, PanelSection, apply_dialog_chrome, prop_row

# 2026-08-27 D 波：原 _INPUT_SS/_BTN_SS 共享片段与全局主题重复，按 §3 删除，
# 通用控件外观统一走 GLOBAL_QSS。仅保留轴值条这一局部特化（语义色 chunk）。


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


def _axis_test_bar(label: str, color: str) -> QProgressBar:
    """轴测试条（局部特化：token 化底/描边 + 语义色 chunk；数值格式由回调写）。"""
    bar = QProgressBar()
    bar.setRange(0, 1000)
    bar.setValue(500)
    bar.setFormat(f"{label}: %v")
    bar._axis_label = label   # 供 _on_axis 显示原始值（放缩后数值在进度条上）
    bar.setStyleSheet(
        f"QProgressBar{{background:{BG_INPUT};border:1px solid #3D3D3D;"
        f"border-radius:4px;min-height:18px;text-align:center;"
        f"color:{_TXT_DIM};font-size:11px;}}"
        f"QProgressBar::chunk{{background:{color};border-radius:3px;}}")
    return bar


class ExternalJoystickConfigDialog(QDialog):
    """统一外接摇杆设备配置窗口（菜单入口）。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        # R54：可缩窗口——先置收缩态标记（resizeEvent 在布局期即可触发）
        self._compact = False
        self._detail_widgets: list = []
        self.setWindowTitle("外接摇杆设备")
        self.setMinimumSize(480, 360)          # R54：合理最小 480×360，其余自由缩放
        self.setSizeGripEnabled(True)          # R54：右下角尺寸手柄
        self.setWindowIcon(QIcon(app_icon_path()))   # R54：官方 ico

        # ── 驱动引用（必须在 UI 构建之前） ──────────────────────────────────
        self._hid_driver = get_physical_joystick_driver()
        self._midi_driver = None
        try:
            from .midi_joystick_driver import get_midi_joystick_driver
            self._midi_driver = get_midi_joystick_driver()
        except Exception:
            pass
        self._current_driver = self._hid_driver

        lay = QVBoxLayout(self)
        # D 波任务 A：浮窗解剖——内容边距 12 + 标题行
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        lay.addWidget(_kit_title_row("外接摇杆设备", self.close))

        # ── R54：主体包滚动区（小窗时可滚动，框架隐藏、底色透出面板） ────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            "QScrollArea>QWidget>QWidget{background:transparent;}")
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(8)
        self._scroll.setWidget(body)
        lay.addWidget(self._scroll, 1)

        # ── 设备类型选择 ────────────────────────────────────────────────
        type_row = QHBoxLayout()
        type_row.setSpacing(6)
        type_lbl = QLabel("设备类型")
        type_lbl.setStyleSheet(f"color:{_TXT_DIM};font-size:11px;")
        type_row.addWidget(type_lbl)
        self._type_cb = QComboBox()
        self._type_cb.addItem("HID 游戏摇杆 (pygame)", "hid")
        self._type_cb.addItem("MIDI 摇杆 (pygame.midi)", "midi")
        self._type_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self._type_cb.currentIndexChanged.connect(self._on_type_changed)
        type_row.addWidget(self._type_cb, 1)
        body_lay.addLayout(type_row)

        # ── 连接状态 ────────────────────────────────────────────────────
        self._status_lbl = QLabel("○ 未连接")
        self._status_lbl.setStyleSheet(f"color:{_TXT_DIM};font-size:12px;")
        body_lay.addWidget(self._status_lbl)

        # ── 设备选择（共用） ────────────────────────────────────────────
        dev_row = QHBoxLayout()
        dev_row.setSpacing(6)
        dev_lbl = QLabel("设备")
        dev_lbl.setStyleSheet(f"color:{_TXT_DIM};font-size:11px;")
        dev_row.addWidget(dev_lbl)
        self._dev_cb = QComboBox()
        self._dev_cb.setMinimumWidth(200)
        self._dev_cb.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dev_cb.currentIndexChanged.connect(self._on_device_selected)
        dev_row.addWidget(self._dev_cb, 1)
        ref_btn = QPushButton("刷新")
        ref_btn.setToolTip("重新扫描可用的摇杆/MIDI 设备")
        ref_btn.setCursor(Qt.CursorShape.PointingHandCursor)   # §9
        ref_btn.clicked.connect(self._refresh_devices)
        dev_row.addWidget(ref_btn)
        body_lay.addLayout(dev_row)

        # ── 启用 ────────────────────────────────────────────────────────
        self._enable_cb = QCheckBox("启用摇杆输入")
        self._enable_cb.toggled.connect(self._toggle_enabled)
        body_lay.addWidget(self._enable_cb)

        # ── 配置面板（QStackedWidget 切换 HID / MIDI） ──────────────────
        sec_cfg = PanelSection("轴配置")
        self._config_stack = QStackedWidget()
        # Size to current widget, not the largest one
        self._config_stack.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._config_stack.addWidget(self._build_hid_panel())
        self._config_stack.addWidget(self._build_midi_panel())
        sec_cfg.add_widget(self._config_stack)
        body_lay.addWidget(sec_cfg)

        # ── 轴测试（共用） ──────────────────────────────────────────────
        sec_test = PanelSection("轴测试")
        self._x_test = _axis_test_bar("X", _C_FOCUS)
        self._y_test = _axis_test_bar("Y", _C_INFO)
        self._z_test = _axis_test_bar("Z", _C_OK)
        self._w_test = _axis_test_bar("W", _C_W)
        sec_test.add_widget(self._x_test)
        sec_test.add_widget(self._y_test)
        sec_test.add_widget(self._z_test)
        sec_test.add_widget(self._w_test)
        body_lay.addWidget(sec_test)

        # ── 输出放缩比例（XYZ 三轴独立，0-100%，100%=原样） ──────────────
        sec_scale = PanelSection("输出放缩比例")
        mgr = get_joystick_bind_manager()
        self._scale_x = self._mk_scale_row("X", mgr.get_axis_scale("x"), sec_scale)
        self._scale_y = self._mk_scale_row("Y", mgr.get_axis_scale("y"), sec_scale)
        self._scale_z = self._mk_scale_row("Z", mgr.get_axis_scale("z"), sec_scale)
        # ★ 提示：放缩作用于所有摇杆输出（绑定通道/虚拟摇杆/音频桥），测试条即时反映
        scale_hint = QLabel("放缩对摇杆实际输出生效（中位对称，100%=原样）——上方轴测试条即时反映放缩后数值")
        scale_hint.setStyleSheet(f"color:{_TXT_DIM};font-size:11px;")
        scale_hint.setWordWrap(True)
        sec_scale.add_widget(scale_hint)
        body_lay.addWidget(sec_scale)

        # R54：详细参数区（轴配置 + 输出放缩）——小窗收缩模式收起，仅留
        # 连接状态 + 轴测试条 + 主按钮
        body_lay.addStretch(1)
        self._detail_widgets = [sec_cfg, sec_scale]

        # ── 按钮 ────────────────────────────────────────────────────────
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.close)
        for btn in btn_box.buttons():
            btn.setProperty("role", "cancel")   # 任务 A：关闭=cancel 次按钮
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
        lay.addWidget(btn_box)
        apply_dialog_chrome(self)   # D 波任务 A：统一浮窗外框

        self._connect_signals()
        self._refresh_devices()

    # ── HID 配置面板 ────────────────────────────────────────────────────

    def _build_hid_panel(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        fl.setSpacing(8)

        self._hid_axis_x = QSpinBox()
        self._hid_axis_x.setRange(0, 7)
        self._hid_axis_x.setValue(0)
        self._hid_axis_x.valueChanged.connect(lambda v: self._hid_driver.set_axis_x(v))
        fl.addRow("X 轴编号:", self._hid_axis_x)

        self._hid_axis_y = QSpinBox()
        self._hid_axis_y.setRange(0, 7)
        self._hid_axis_y.setValue(1)
        self._hid_axis_y.valueChanged.connect(lambda v: self._hid_driver.set_axis_y(v))
        fl.addRow("Y 轴编号:", self._hid_axis_y)

        self._hid_axis_z = QSpinBox()
        self._hid_axis_z.setRange(0, 7)
        self._hid_axis_z.setValue(2)
        self._hid_axis_z.valueChanged.connect(lambda v: self._hid_driver.set_axis_z(v))
        fl.addRow("Z 轴编号:", self._hid_axis_z)

        self._hid_axis_w = QSpinBox()
        self._hid_axis_w.setRange(0, 7)
        self._hid_axis_w.setValue(3)
        self._hid_axis_w.valueChanged.connect(lambda v: self._hid_driver.set_axis_w(v))
        fl.addRow("W 轴编号:", self._hid_axis_w)

        inv_row = QHBoxLayout()
        for lbl_txt, drv_setter in (
                ("反X", "x"), ("反Y", "y"), ("反Z", "z"), ("反W", "w")):
            cb = QCheckBox(lbl_txt)
            setattr(self, f"_hid_inv_{drv_setter}", cb)
            cb.toggled.connect(getattr(
                self._hid_driver,
                {"x": "set_invert_x", "y": "set_invert_y",
                 "z": "set_invert_z", "w": "set_invert_w"}[drv_setter]))
            inv_row.addWidget(cb)
        inv_w = QWidget()
        inv_w.setLayout(inv_row)
        fl.addRow("", inv_w)

        # 死区（百分比 → NumSlider %；int 范围 0~50 与旧滑杆一致）
        self._hid_dz_sl = NumSlider(0.0, 50.0, 5.0, decimals=0, suffix="%")
        self._hid_dz_sl.setToolTip("摇杆中位死区：范围内小幅抖动视为中位（右键输入数值）")
        self._hid_dz_sl.valueChanged.connect(lambda v: (
            self._hid_driver.set_deadzone(v / 100.0)))
        dz_row = QHBoxLayout()
        dz_row.setSpacing(6)
        dz_lbl = QLabel("死区")
        dz_lbl.setStyleSheet(f"color:{_TXT_DIM};font-size:11px;")
        dz_row.addWidget(dz_lbl)
        dz_row.addWidget(self._hid_dz_sl, 1)
        dz_w = QWidget()
        dz_w.setLayout(dz_row)
        fl.addRow("", dz_w)

        return w

    # ── MIDI 配置面板 ───────────────────────────────────────────────────

    def _build_midi_panel(self) -> QWidget:
        w = QWidget()
        fl = QFormLayout(w)
        fl.setSpacing(8)

        self._midi_ch_spin = QSpinBox()
        self._midi_ch_spin.setRange(1, 16)
        self._midi_ch_spin.setValue(1)
        self._midi_ch_spin.valueChanged.connect(
            lambda v: self._midi_driver.set_midi_channel(v - 1)
            if self._midi_driver else None)
        fl.addRow("MIDI 通道:", self._midi_ch_spin)

        cc_sec = PanelSection("CC 号映射")
        cc_fl = QFormLayout()
        cc_fl.setSpacing(6)

        self._midi_cc_x = QSpinBox()
        self._midi_cc_x.setRange(0, 127)
        self._midi_cc_x.setValue(1)
        self._midi_cc_x.valueChanged.connect(
            lambda v: self._midi_driver.set_cc_mapping("x", v)
            if self._midi_driver else None)
        cc_fl.addRow("X 轴 → CC:", self._midi_cc_x)

        self._midi_cc_y = QSpinBox()
        self._midi_cc_y.setRange(0, 127)
        self._midi_cc_y.setValue(2)
        self._midi_cc_y.valueChanged.connect(
            lambda v: self._midi_driver.set_cc_mapping("y", v)
            if self._midi_driver else None)
        cc_fl.addRow("Y 轴 → CC:", self._midi_cc_y)

        self._midi_cc_z = QSpinBox()
        self._midi_cc_z.setRange(0, 127)
        self._midi_cc_z.setValue(3)
        self._midi_cc_z.valueChanged.connect(
            lambda v: self._midi_driver.set_cc_mapping("z", v)
            if self._midi_driver else None)
        cc_fl.addRow("Z 轴 → CC:", self._midi_cc_z)
        cc_body = QWidget()
        cc_body.setLayout(cc_fl)
        cc_sec.add_widget(cc_body)
        fl.addRow(cc_sec)

        inv_row = QHBoxLayout()
        for lbl_txt, attr, setter in (
                ("反转 X", "_midi_inv_x", "set_invert_x"),
                ("反转 Y", "_midi_inv_y", "set_invert_y"),
                ("反转 Z", "_midi_inv_z", "set_invert_z")):
            cb = QCheckBox(lbl_txt)
            setattr(self, attr, cb)
            cb.toggled.connect(
                lambda v, s=setter: getattr(self._midi_driver, s)(v)
                if self._midi_driver else None)
            inv_row.addWidget(cb)
        inv_w = QWidget()
        inv_w.setLayout(inv_row)
        fl.addRow("", inv_w)

        # 死区（百分比 → NumSlider %；int 范围 0~50 与旧滑杆一致）
        self._midi_dz_sl = NumSlider(0.0, 50.0, 5.0, decimals=0, suffix="%")
        self._midi_dz_sl.setToolTip("MIDI 摇杆中位死区（右键输入数值）")
        self._midi_dz_sl.valueChanged.connect(
            lambda v: (self._midi_driver.set_deadzone(v / 100.0))
            if self._midi_driver else None)
        dz_row = QHBoxLayout()
        dz_row.setSpacing(6)
        dz_lbl = QLabel("死区")
        dz_lbl.setStyleSheet(f"color:{_TXT_DIM};font-size:11px;")
        dz_row.addWidget(dz_lbl)
        dz_row.addWidget(self._midi_dz_sl, 1)
        dz_w = QWidget()
        dz_w.setLayout(dz_row)
        fl.addRow("", dz_w)

        return w

    # ── 输出放缩比例行 ────────────────────────────────────────────────────

    def _mk_scale_row(self, axis: str, value: float,
                      parent_section: PanelSection) -> NumSlider:
        """一行放缩滑块：0-100%，变更即写入绑定管理器并持久化。

        D 波任务 B：浮点/百分比参数 → NumSlider（% 后缀，值绘在滑槽上，
        右键弹数值输入）；内部 int 范围 0~100 与旧 QSlider 完全一致。
        """
        key = axis.lower()   # 管理器键为小写（x/y/z），显示用大写
        slider = NumSlider(0.0, 100.0, float(value * 100), decimals=0, suffix="%")
        slider.setToolTip(f"{axis} 轴输出放缩比例（中位对称；右键输入数值）")
        slider.valueChanged.connect(
            lambda v: get_joystick_bind_manager().set_axis_scale(key, v / 100.0))
        parent_section.add_widget(prop_row(f"{axis} 轴", slider))
        return slider

    # ── 设备类型切换 ────────────────────────────────────────────────────

    def _on_type_changed(self):
        dev_type = self._type_cb.currentData()
        if dev_type == "midi":
            self._config_stack.setCurrentIndex(1)
            self._current_driver = self._midi_driver
        else:
            self._config_stack.setCurrentIndex(0)
            self._current_driver = self._hid_driver
        self._disconnect_signals()
        self._connect_signals()
        self._refresh_devices()
        # Snap dialog size to fit current panel (HID is shorter than MIDI)
        self.adjustSize()

    # ── 设备列表 ────────────────────────────────────────────────────────

    def _refresh_devices(self):
        if self._current_driver is None:
            return
        # Sync HID config
        if self._current_driver is self._hid_driver:
            self._hid_driver.refresh_devices()
            self._hid_axis_x.blockSignals(True)
            self._hid_axis_x.setValue(self._hid_driver.get_axis_x())
            self._hid_axis_x.blockSignals(False)
            self._hid_axis_y.blockSignals(True)
            self._hid_axis_y.setValue(self._hid_driver.get_axis_y())
            self._hid_axis_y.blockSignals(False)
            self._hid_axis_z.blockSignals(True)
            self._hid_axis_z.setValue(self._hid_driver.get_axis_z())
            self._hid_axis_z.blockSignals(False)
            self._hid_axis_w.blockSignals(True)
            self._hid_axis_w.setValue(self._hid_driver.get_axis_w())
            self._hid_axis_w.blockSignals(False)
            self._hid_inv_x.setChecked(self._hid_driver._invert_x)
            self._hid_inv_y.setChecked(self._hid_driver._invert_y)
            self._hid_inv_z.setChecked(self._hid_driver._invert_z)
            self._hid_inv_w.setChecked(self._hid_driver._invert_w)
            self._hid_dz_sl.setValue(int(self._hid_driver.get_deadzone() * 100))
        elif self._current_driver is self._midi_driver:
            self._midi_driver.refresh_devices()
            self._midi_ch_spin.blockSignals(True)
            self._midi_ch_spin.setValue(self._midi_driver.get_midi_channel() + 1)
            self._midi_ch_spin.blockSignals(False)
            self._midi_cc_x.blockSignals(True)
            self._midi_cc_x.setValue(self._midi_driver.get_cc_mapping("x"))
            self._midi_cc_x.blockSignals(False)
            self._midi_cc_y.blockSignals(True)
            self._midi_cc_y.setValue(self._midi_driver.get_cc_mapping("y"))
            self._midi_cc_y.blockSignals(False)
            self._midi_cc_z.blockSignals(True)
            self._midi_cc_z.setValue(self._midi_driver.get_cc_mapping("z"))
            self._midi_cc_z.blockSignals(False)
            self._midi_inv_x.setChecked(self._midi_driver.get_invert_x())
            self._midi_inv_y.setChecked(self._midi_driver.get_invert_y())
            self._midi_inv_z.setChecked(self._midi_driver.get_invert_z())
            self._midi_dz_sl.setValue(int(self._midi_driver.get_deadzone() * 100))
        # Refresh device list
        devices = self._current_driver.get_devices()
        self._dev_cb.blockSignals(True)
        self._dev_cb.clear()
        if not devices:
            self._dev_cb.addItem("(无设备)", -1)
        else:
            for d in devices:
                if hasattr(d, 'num_axes'):
                    label = f"{d.name} (轴:{d.num_axes} 按钮:{d.num_buttons})"
                    self._dev_cb.addItem(label, d.index)
                else:
                    label = f"{d.name}"
                    self._dev_cb.addItem(label, d.device_id)
        self._dev_cb.blockSignals(False)
        # 如果没有已连接设备，自动选择第一个可用设备
        if not self._current_driver.is_connected() and devices:
            first_idx = devices[0].index if hasattr(devices[0], 'index') else devices[0].device_id
            self._current_driver.select_device(first_idx)
        self._on_conn_status(self._current_driver.is_connected(), "")

    def _on_device_selected(self):
        idx = self._dev_cb.currentData()
        if idx is not None and idx >= 0:
            self._current_driver.select_device(idx)

    def _toggle_enabled(self, enabled: bool):
        self._current_driver.set_enabled(enabled)

    # ── 信号连接 ────────────────────────────────────────────────────────

    def _connect_signals(self):
        """连接当前驱动的信号（先断开旧连接确保幂等）。"""
        self._disconnect_signals()
        if self._current_driver is None:
            return
        self._current_driver.axis3d_changed.connect(self._on_axis)
        self._current_driver.connection_status_changed.connect(self._on_conn_status)
        self._current_driver.device_list_changed.connect(self._on_devices_changed)

    def _disconnect_signals(self):
        if self._current_driver is None:
            return
        try:
            self._current_driver.axis3d_changed.disconnect(self._on_axis)
        except (TypeError, RuntimeError):
            pass
        try:
            self._current_driver.connection_status_changed.disconnect(self._on_conn_status)
        except (TypeError, RuntimeError):
            pass
        try:
            self._current_driver.device_list_changed.disconnect(self._on_devices_changed)
        except (TypeError, RuntimeError):
            pass

    # ── 回调 ────────────────────────────────────────────────────────────

    def _on_axis(self, x: float, y: float, z: float, w: float = 0.5):
        """轴测试条显示放缩后的输出数值（原始值在格式文本中可见，便于确认轴在读）。"""
        mgr = get_joystick_bind_manager()
        for axis, raw, bar in (
                ("x", x, self._x_test), ("y", y, self._y_test),
                ("z", z, self._z_test), ("w", w, self._w_test)):
            scaled = 0.5 + (raw - 0.5) * mgr.get_axis_scale(axis)
            bar.setValue(int(scaled * 1000))
            bar.setFormat(f"{bar._axis_label}: {int(scaled * 1000)} (原始 {int(raw * 1000)})")

    def _on_conn_status(self, connected: bool, name: str):
        self._status_lbl.setText(
            f"● 已连接: {name}" if connected else "○ 未连接")
        self._status_lbl.setStyleSheet(
            f"color:{FG_GREEN if connected else _TXT_DIM};font-size:12px;")
        self._enable_cb.blockSignals(True)
        self._enable_cb.setChecked(connected and self._current_driver.is_enabled())
        self._enable_cb.blockSignals(False)

    def _on_devices_changed(self, devices: list):
        self._refresh_devices()

    # ── R54：关键信息收缩模式（纯 UI 显隐，不动逻辑） ────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_compact_mode()

    def _update_compact_mode(self):
        """窗口高度 < 480 时收起详细参数区（轴配置/输出放缩），仅留
        连接状态 + 轴测试条 + 主按钮；大窗自动恢复。"""
        if not self._detail_widgets:
            return
        compact = self.height() < 480
        if compact == self._compact:
            return
        self._compact = compact
        for w in self._detail_widgets:
            w.setVisible(not compact)

    def closeEvent(self, event):
        self._disconnect_signals()
        self._current_driver = None
        super().closeEvent(event)
