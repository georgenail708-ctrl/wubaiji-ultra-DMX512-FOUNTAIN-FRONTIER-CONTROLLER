"""
global_audio_source_bar.py
────────────────────────────────────────────────────────────────────────────
全局音频来源切换条（GlobalAudioSourceBar）

功能：
  - 三种来源切换：模拟 / 麦克风 / 时间线音频
  - 麦克风设备选择下拉框（仅麦克风模式下可见）
  - 麦克风实时电平条（仅麦克风模式下可见）
  - 外部摇杆设备选择和启用/停用（PhysicalJoystickDriver）
  - 切换来源只影响 AudioAnalyzer 的识别输入，不影响已加载的音频文件

设计原则：
  - 切换到麦克风：AudioAnalyzer.set_mode("microphone")，_file_data 保留不变
  - 切换回文件：AudioAnalyzer.set_mode("file")，继续从时间线位置识别
  - 切换到模拟：AudioAnalyzer.set_mode("simulate")
────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

from typing import Optional, List, Dict, Any

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QSettings, QRect, QPoint
from PyQt6.QtGui import QPainter, QColor, QPen, QCursor, QFont
from PyQt6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QComboBox, QFrame, QSizePolicy,
    QDialog, QDialogButtonBox, QScrollArea, QCheckBox,
    QDoubleSpinBox, QGroupBox, QSpinBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
)

# ─────────────────────────────────────────────────────────────────────────────
#  颜色常量（统一映射自 qascade_theme 主题令牌；藏蓝家族已清除）
# ─────────────────────────────────────────────────────────────────────────────
from .qascade_theme import (
    BG_EDITOR as _BG,
    FOCUS_BLUE as _ACCENT,
    SELECT_BLUE as _SEL_BLUE,
    BORDER_NORMAL as _BORDER,
    BRIGHT_SUCCESS as _GREEN,
    BRIGHT_WARN as _ORANGE,
    BRIGHT_ERROR as _RED,
    BRIGHT_INFO as _CYAN,
    TEXT_PRIMARY as _FG,
    TEXT_SECONDARY as _FG_DIM,
)
from .tabler_button import tabler_button

_BTN_CSS = """
QPushButton {{
    background: {bg};
    color: {fg};
    border: 1px solid {border};
    border-radius: 3px;
    padding: 2px 8px;
    font-size: 11px;
    min-height: 22px;
}}
QPushButton:hover {{ background: {hover}; }}
QPushButton:checked {{
    background: {checked_bg};
    color: {checked_fg};
    border-color: {checked_fg};
    font-weight: bold;
}}
"""

_COMBO_CSS = """
QComboBox {
    background: #282828;
    color: #CCCCCC;
    border: 1px solid #3D3D3D;
    border-radius: 3px;
    padding: 2px 6px;
    font-size: 10px;
    min-height: 20px;
}
QComboBox::drop-down { border: none; width: 16px; }
QComboBox QAbstractItemView {
    background: #282828;
    color: #CCCCCC;
    selection-background-color: #4772B3;
}
"""

_LABEL_CSS = "color:{color};font-size:10px;background:transparent;border:none;"


def _apply_label_color(lbl: QLabel, color: str) -> None:
    """细粒度状态换色：仅当颜色变化时才重写 QSS（避免高频整段刷新）。"""
    if getattr(lbl, "_qss_color", None) == color:
        return
    lbl._qss_color = color
    lbl.setStyleSheet(_LABEL_CSS.format(color=color))


# ─────────────────────────────────────────────────────────────────────────────
#  麦克风电平条
# ─────────────────────────────────────────────────────────────────────────────
class _MicLevelBar(QWidget):
    """实时麦克风电平条（水平，绿→黄→红）。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._level = 0.0
        self._peak  = 0.0
        self._peak_hold = 0
        self.setFixedSize(80, 12)

    def set_level(self, level: float):
        """更新电平值（0.0 ~ 1.0）。"""
        self._level = max(0.0, min(1.0, level))
        if self._level > self._peak:
            self._peak = self._level
            self._peak_hold = 30
        else:
            if self._peak_hold > 0:
                self._peak_hold -= 1
            else:
                self._peak = max(0.0, self._peak - 0.02)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        # 背景
        p.fillRect(0, 0, w, h, QColor("#181818"))
        p.setPen(QPen(QColor("#3D3D3D"), 1))
        p.drawRect(0, 0, w - 1, h - 1)
        # 电平条（分段着色）
        bar_w = int(self._level * (w - 2))
        if bar_w > 0:
            # 绿色段（0~70%）
            green_w = min(bar_w, int((w - 2) * 0.7))
            p.fillRect(1, 1, green_w, h - 2, QColor("#6aaa5a"))
            # 黄色段（70%~90%）
            if bar_w > green_w:
                yellow_w = min(bar_w - green_w, int((w - 2) * 0.2))
                p.fillRect(1 + green_w, 1, yellow_w, h - 2, QColor("#aaaa5a"))
                # 红色段（90%~100%）
                if bar_w > green_w + yellow_w:
                    red_w = bar_w - green_w - yellow_w
                    p.fillRect(1 + green_w + yellow_w, 1, red_w, h - 2, QColor("#aa5a5a"))
        # 峰值指示线
        if self._peak > 0.01:
            peak_x = 1 + int(self._peak * (w - 2))
            p.setPen(QPen(QColor("#FFFFFF"), 1))
            p.drawLine(peak_x, 1, peak_x, h - 2)
        p.end()



# ─────────────────────────────────────────────────────────────────────────────
#  屏幕区域选择覆盖层
# ─────────────────────────────────────────────────────────────────────────────
class ScreenRegionSelectorDialog(QDialog):
    """半透明全屏覆盖层：拖拽选择一个屏幕矩形区域供实体摇杆模拟鼠标。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._origin: Optional[QPoint] = None
        self._current: Optional[QPoint] = None
        self._selected_rect: Optional[QRect] = None
        self.setWindowTitle("选择摇杆鼠标模拟区域")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        screen = QApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())
        self.setStyleSheet("background: transparent;")

    def selected_region(self) -> Optional[Dict[str, int]]:
        if self._selected_rect is None:
            return None
        r = self._selected_rect.normalized()
        if r.width() < 8 or r.height() < 8:
            return None
        return {"x": int(r.x()), "y": int(r.y()), "w": int(r.width()), "h": int(r.height())}

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # D 波任务 E：选区遮罩视觉——暗色帘幕 + select_blue 半透选区 +
        # focus_blue 描边与四角刻点（状态机逻辑不动）
        p.fillRect(self.rect(), QColor(0, 0, 0, 140))
        rect = self._preview_rect()
        if rect is not None:
            p.fillRect(rect, QColor(71, 114, 179, 60))          # select_blue 半透
            p.setPen(QPen(QColor(_ACCENT), 2))
            p.drawRect(rect)
            # 四角刻点（矩形 6px 外角标记）
            corner = 6
            p.setPen(QPen(QColor("#FFFFFF"), 2))
            x0, y0 = rect.x(), rect.y()
            x1, y1 = rect.right(), rect.bottom()
            for cx, cy, dx, dy in ((x0, y0, 1, 1), (x1, y0, -1, 1),
                                   (x0, y1, 1, -1), (x1, y1, -1, -1)):
                p.drawLine(cx, cy, cx + dx * corner, cy)
                p.drawLine(cx, cy, cx, cy + dy * corner)

        # 文案：BG_DEEP 圆角背卡保证任意壁纸/桌面上可读（§10 弹层解剖）
        tip_x, tip_y, tip_w, tip_h = 16, 16, 460, 30
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        from PyQt6.QtGui import QBrush
        brush = QBrush(QColor(24, 24, 24, 200))              # BG_DEEP 半透
        p.fillRect(tip_x, tip_y, tip_w, tip_h, brush)
        p.setPen(QPen(QColor("#242424"), 1))                 # POPUP_BORDER
        p.drawRect(tip_x, tip_y, tip_w, tip_h)
        f = QFont("Segoe UI")
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QPen(QColor(230, 230, 230)))                # TEXT_PRIMARY
        p.drawText(tip_x + 10, tip_y + 20,
                   "拖拽框选摇杆接替鼠标操作的屏幕区域；松开鼠标确认，Esc 取消。")
        p.end()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._current = self._origin
            self.update()

    def mouseMoveEvent(self, event):
        if self._origin is not None:
            self._current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._origin is not None:
            self._current = event.position().toPoint()
            rect = self._preview_rect()
            self._origin = None
            self._current = None
            if rect is not None and rect.width() >= 8 and rect.height() >= 8:
                self._selected_rect = rect.normalized()
                self.accept()
            else:
                self.reject()

    def _preview_rect(self) -> Optional[QRect]:
        if self._origin is None or self._current is None:
            return None
        return QRect(self._origin, self._current).normalized()

# ─────────────────────────────────────────────────────────────────────────────
#  外部摇杆设备面板（弹出对话框）
# ─────────────────────────────────────────────────────────────────────────────
class ExternalJoystickDialog(QDialog):
    """外部实体摇杆设备选择和配置对话框。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("外部摇杆设备")
        self.setMinimumWidth(640)
        self.setStyleSheet(f"background:{_BG};color:{_FG};")
        self._driver = None
        self._bindings: List[Dict[str, Any]] = []
        self._learn_waiting = False
        self._mouse_region: Dict[str, int] = self._default_mouse_region()
        self._build_ui()
        self._load_mouse_region()
        self._load_bindings()
        self._load_driver()

    def _disconnect_driver(self):
        """断开本对话框与驱动单例的全部信号连接（幂等，不抛异常）。

        ★ 2026-09-13 修连接累积：`get_physical_joystick_driver()` 返回的是**全局
          单例**（长寿命对象）。本对话框每次重新打开都会新建实例并再次执行
          `_load_driver`，若不断开旧连接，旧对话框的槽会一直挂在单例上累积
          （每开一次多一份，轴事件被重复处理）。断开后恒为 1 份连接。
        """
        d = getattr(self, "_driver", None)
        if d is None:
            return
        for name, slot in (("device_list_changed", self._on_devices_changed),
                           ("connection_status_changed", self._on_connection_changed),
                           ("axis_changed", self._on_axis_changed)):
            sig = getattr(d, name, None)
            if sig is None:
                continue
            try:
                sig.disconnect(slot)
            except Exception:
                pass   # 原本未连接 / 该信号不支持断连：忽略

    def _load_driver(self):
        try:
            from .physical_joystick_driver import get_physical_joystick_driver
            self._driver = get_physical_joystick_driver()
            # ★ 先断旧连再连（幂等）：驱动是单例，重复 `_load_driver` 不能累积槽。
            self._disconnect_driver()
            U = Qt.ConnectionType.UniqueConnection
            self._driver.device_list_changed.connect(self._on_devices_changed, U)
            self._driver.connection_status_changed.connect(self._on_connection_changed, U)
            try:
                self._driver.axis_changed.connect(self._on_axis_changed, U)
            except Exception:
                pass
            self._refresh_device_list()
            self._sync_ui_from_driver()
            self._sync_region_label()
        except Exception as e:
            self._status_lbl.setText(f"驱动加载失败: {e}")

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.setContentsMargins(14, 12, 14, 12)

        # ── 设备选择 ──
        grp_dev = QGroupBox("设备选择")
        grp_dev.setStyleSheet(
            f"QGroupBox {{ color:{_ACCENT};font-size:11px;border:1px solid #3D3D3D;"
            f"border-radius:4px;margin-top:8px;padding-top:8px; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin;left:8px; }}"
        )
        dev_lay = QVBoxLayout(grp_dev)
        dev_lay.setSpacing(6)

        # 设备列表下拉
        row1 = QHBoxLayout()
        row1.addWidget(self._mk_label("设备:"))
        self._device_combo = QComboBox()
        self._device_combo.setStyleSheet(_COMBO_CSS)
        self._device_combo.setMinimumWidth(220)
        self._device_combo.addItem("-- 未选择 --", -1)
        row1.addWidget(self._device_combo, 1)
        self._btn_refresh = QPushButton("刷新")
        self._btn_refresh.setStyleSheet(
            _BTN_CSS.format(bg="#282828", fg=_FG_DIM, border="#3D3D3D",
                            hover="#3D3D3D", checked_bg=_ACCENT, checked_fg="#000"))
        self._btn_refresh.setFixedWidth(50)
        self._btn_refresh.clicked.connect(self._on_refresh_clicked)
        row1.addWidget(self._btn_refresh)
        dev_lay.addLayout(row1)

        # 连接状态
        self._status_lbl = QLabel("未连接")
        _apply_label_color(self._status_lbl, _FG_DIM)
        dev_lay.addWidget(self._status_lbl)

        # 启用/停用
        row2 = QHBoxLayout()
        self._btn_enable = QPushButton("启用摇杆")
        self._btn_enable.setCheckable(True)
        self._btn_enable.setStyleSheet(
            _BTN_CSS.format(bg="#0A1A0A", fg=_GREEN, border="#1A3A1A",
                            hover="#1A3A1A", checked_bg=_GREEN, checked_fg="#000"))
        self._btn_enable.toggled.connect(self._on_enable_toggled)
        row2.addWidget(self._btn_enable)
        row2.addStretch()
        dev_lay.addLayout(row2)
        lay.addWidget(grp_dev)

        # ── 轴绑定 ──
        grp_axis = QGroupBox("轴绑定")
        grp_axis.setStyleSheet(
            f"QGroupBox {{ color:{_ACCENT};font-size:11px;border:1px solid #3D3D3D;"
            f"border-radius:4px;margin-top:8px;padding-top:8px; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin;left:8px; }}"
        )
        axis_lay = QVBoxLayout(grp_axis)
        axis_lay.setSpacing(6)

        # X 轴
        row_x = QHBoxLayout()
        row_x.addWidget(self._mk_label("X 轴 (轴号):"))
        self._spin_axis_x = QSpinBox()
        self._spin_axis_x.setRange(0, 31)
        self._spin_axis_x.setValue(0)
        self._spin_axis_x.setFixedWidth(60)
        self._spin_axis_x.setStyleSheet(
            "background:#1D1D1D;color:#CCCCCC;border:1px solid #3D3D3D;"
            "border-radius:3px;padding:2px 4px;")
        self._spin_axis_x.valueChanged.connect(
            lambda v: self._driver.set_axis_x(v) if self._driver else None)
        row_x.addWidget(self._spin_axis_x)
        self._cb_inv_x = QCheckBox("反转")
        self._cb_inv_x.setStyleSheet(f"color:{_FG_DIM};font-size:10px;")
        self._cb_inv_x.toggled.connect(
            lambda v: self._driver.set_invert_x(v) if self._driver else None)
        row_x.addWidget(self._cb_inv_x)
        row_x.addStretch()
        axis_lay.addLayout(row_x)

        # Y 轴
        row_y = QHBoxLayout()
        row_y.addWidget(self._mk_label("Y 轴 (轴号):"))
        self._spin_axis_y = QSpinBox()
        self._spin_axis_y.setRange(0, 31)
        self._spin_axis_y.setValue(1)
        self._spin_axis_y.setFixedWidth(60)
        self._spin_axis_y.setStyleSheet(
            "background:#1D1D1D;color:#CCCCCC;border:1px solid #3D3D3D;"
            "border-radius:3px;padding:2px 4px;")
        self._spin_axis_y.valueChanged.connect(
            lambda v: self._driver.set_axis_y(v) if self._driver else None)
        row_y.addWidget(self._spin_axis_y)
        self._cb_inv_y = QCheckBox("反转")
        self._cb_inv_y.setStyleSheet(f"color:{_FG_DIM};font-size:10px;")
        self._cb_inv_y.toggled.connect(
            lambda v: self._driver.set_invert_y(v) if self._driver else None)
        row_y.addWidget(self._cb_inv_y)
        row_y.addStretch()
        axis_lay.addLayout(row_y)

        # 死区
        row_dz = QHBoxLayout()
        row_dz.addWidget(self._mk_label("死区:"))
        self._spin_dz = QDoubleSpinBox()
        self._spin_dz.setRange(0.0, 0.5)
        self._spin_dz.setValue(0.05)
        self._spin_dz.setSingleStep(0.01)
        self._spin_dz.setDecimals(2)
        self._spin_dz.setFixedWidth(70)
        self._spin_dz.setStyleSheet(
            "background:#1D1D1D;color:#CCCCCC;border:1px solid #3D3D3D;"
            "border-radius:3px;padding:2px 4px;")
        self._spin_dz.valueChanged.connect(
            lambda v: self._driver.set_deadzone(v) if self._driver else None)
        row_dz.addWidget(self._spin_dz)
        row_dz.addWidget(self._mk_label("（±比例，建议 0.03~0.10）"))
        row_dz.addStretch()
        axis_lay.addLayout(row_dz)

        # 实时轴值显示
        self._axis_display = QLabel("X: 0.500  Y: 0.500")
        self._axis_display.setStyleSheet(
            f"color:{_CYAN};font-size:10px;font-family:Consolas,monospace;"
            f"background:transparent;border:none;")
        axis_lay.addWidget(self._axis_display)
        lay.addWidget(grp_axis)

        # ── 通用鼠标模拟区域 ──
        grp_mouse = QGroupBox("通用鼠标模拟")
        grp_mouse.setStyleSheet(
            f"QGroupBox {{ color:{_ACCENT};font-size:11px;border:1px solid #3D3D3D;"
            f"border-radius:4px;margin-top:8px;padding-top:8px; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin;left:8px; }}"
        )
        mouse_lay = QVBoxLayout(grp_mouse)
        mouse_lay.setSpacing(6)
        mouse_row = QHBoxLayout()
        mouse_row.addWidget(self._mk_label("模拟区域:"))
        self._region_lbl = QLabel("")
        self._region_lbl.setStyleSheet(_LABEL_CSS.format(color=_CYAN))
        mouse_row.addWidget(self._region_lbl, 1)
        self._btn_select_region = QPushButton("选择屏幕区域")
        self._btn_select_region.setToolTip("拖拽一个屏幕框，实体摇杆会在该框内接替鼠标移动。")
        self._btn_select_region.setStyleSheet(_BTN_CSS.format(bg="#102020", fg=_CYAN, border="#1A4A4A", hover="#1A4A4A", checked_bg=_CYAN, checked_fg="#000"))
        self._btn_select_region.clicked.connect(self._choose_mouse_region)
        mouse_row.addWidget(self._btn_select_region)
        self._btn_full_region = QPushButton("全屏")
        self._btn_full_region.setStyleSheet(_BTN_CSS.format(bg="#282828", fg=_FG_DIM, border="#3D3D3D", hover="#3D3D3D", checked_bg=_ACCENT, checked_fg="#000"))
        self._btn_full_region.clicked.connect(self._use_full_screen_region)
        mouse_row.addWidget(self._btn_full_region)
        mouse_lay.addLayout(mouse_row)
        mouse_tip = QLabel("说明：仍然先识别并选择实体摇杆设备；启用后，选中设备的 X/Y 轴会像游戏手柄模拟鼠标一样映射到上方屏幕区域，不再直接内接到音频桥接器或 Programmer 窗口。")
        mouse_tip.setWordWrap(True)
        mouse_tip.setStyleSheet(f"color:{_FG_DIM};font-size:10px;background:transparent;border:none;")
        mouse_lay.addWidget(mouse_tip)
        lay.addWidget(grp_mouse)

        # ── 输出绑定管理 ──
        grp_target = QGroupBox("输出绑定")
        grp_target.setStyleSheet(
            f"QGroupBox {{ color:{_ACCENT};font-size:11px;border:1px solid #3D3D3D;"
            f"border-radius:4px;margin-top:8px;padding-top:8px; }}"
            f"QGroupBox::title {{ subcontrol-origin:margin;left:8px; }}"
        )
        target_lay = QVBoxLayout(grp_target)
        bind_row = QHBoxLayout()
        bind_row.addWidget(self._mk_label("目标:"))
        self._target_combo = QComboBox()
        self._target_combo.setStyleSheet(_COMBO_CSS)
        self._target_combo.addItem("通用屏幕区域 / 鼠标模拟", "mouse.region")
        bind_row.addWidget(self._target_combo, 1)
        bind_row.addWidget(self._mk_label("来源:"))
        self._source_combo = QComboBox()
        self._source_combo.setStyleSheet(_COMBO_CSS)
        self._source_combo.addItem("双轴 XY", "xy")
        self._source_combo.addItem("仅 X 轴", "x")
        self._source_combo.addItem("仅 Y 轴", "y")
        bind_row.addWidget(self._source_combo)
        self._btn_add_binding = QPushButton("添加绑定")
        self._btn_add_binding.setStyleSheet(_BTN_CSS.format(bg="#102010", fg=_GREEN, border="#1A3A1A", hover="#1A3A1A", checked_bg=_GREEN, checked_fg="#000"))
        self._btn_add_binding.clicked.connect(self._add_binding_from_ui)
        bind_row.addWidget(self._btn_add_binding)
        target_lay.addLayout(bind_row)

        self._binding_table = QTableWidget(0, 3)
        self._binding_table.setHorizontalHeaderLabels(["输出目标", "摇杆来源", "说明"])
        self._binding_table.verticalHeader().setVisible(False)
        self._binding_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._binding_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._binding_table.setStyleSheet("QTableWidget{background:#181818;color:#CCCCCC;border:1px solid #3D3D3D;gridline-color:#3D3D3D;font-size:10px;} QHeaderView::section{background:#3D3D3D;color:#71A8FF;border:1px solid #3D3D3D;}")
        try:
            self._binding_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            self._binding_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            self._binding_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        except Exception:
            pass
        target_lay.addWidget(self._binding_table)

        manage_row = QHBoxLayout()
        self._btn_learn = QPushButton("学习下一次摇杆动作")
        self._btn_learn.setToolTip("点击后推动一个外接摇杆轴，系统会自动识别 X/Y/双轴并绑定到上方选定目标。")
        self._btn_learn.setStyleSheet(_BTN_CSS.format(bg="#20160A", fg=_ORANGE, border="#4A3212", hover="#4A3212", checked_bg=_ORANGE, checked_fg="#000"))
        self._btn_learn.clicked.connect(self._start_learning)
        manage_row.addWidget(self._btn_learn)
        self._btn_remove_binding = QPushButton("删除选中绑定")
        self._btn_remove_binding.setStyleSheet(_BTN_CSS.format(bg="#2A1010", fg=_RED, border="#4A1A1A", hover="#4A1A1A", checked_bg=_RED, checked_fg="#000"))
        self._btn_remove_binding.clicked.connect(self._remove_selected_binding)
        manage_row.addWidget(self._btn_remove_binding)
        self._btn_clear_bindings = QPushButton("清空绑定")
        self._btn_clear_bindings.setStyleSheet(_BTN_CSS.format(bg="#282828", fg=_FG_DIM, border="#3D3D3D", hover="#3D3D3D", checked_bg=_ACCENT, checked_fg="#000"))
        self._btn_clear_bindings.clicked.connect(self._clear_bindings)
        manage_row.addWidget(self._btn_clear_bindings)
        manage_row.addStretch()
        target_lay.addLayout(manage_row)
        lay.addWidget(grp_target)

        # ── 提示 ──
        tip = QLabel(
            "提示：插入实体摇杆后点击「刷新」，从设备列表选择已有设备，再启用摇杆。\n"
            "点击「学习下一次摇杆动作」可自动识别正在操作的 X/Y 轴来源；最终输出为通用屏幕区域鼠标模拟。"
        )
        tip.setStyleSheet(f"color:{_FG_DIM};font-size:10px;background:transparent;border:none;")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        # ── 关闭按钮 ──
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.close)
        btns.setStyleSheet(f"color:{_FG};")
        lay.addWidget(btns)

        # 轴值更新定时器
        self._axis_timer = QTimer(self)
        self._axis_timer.setInterval(100)
        self._axis_timer.timeout.connect(self._update_axis_display)
        self._axis_timer.start()

    def _mk_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(_LABEL_CSS.format(color=_FG_DIM))
        return lbl

    def _refresh_device_list(self):
        if self._driver is None:
            return
        devices = self._driver.get_devices()
        self._device_combo.blockSignals(True)
        self._device_combo.clear()
        self._device_combo.addItem("-- 未选择 --", -1)
        for d in devices:
            self._device_combo.addItem(
                f"[{d.index}] {d.name}  ({d.num_axes}轴/{d.num_buttons}键)", d.index)
        self._device_combo.blockSignals(False)
        # 连接信号（只连一次）
        try:
            self._device_combo.currentIndexChanged.disconnect()
        except Exception:
            pass
        self._device_combo.currentIndexChanged.connect(self._on_device_selected)

    def _sync_ui_from_driver(self):
        if self._driver is None:
            return
        self._spin_axis_x.setValue(self._driver.get_axis_x())
        self._spin_axis_y.setValue(self._driver.get_axis_y())
        self._spin_dz.setValue(self._driver.get_deadzone())
        self._btn_enable.blockSignals(True)
        self._btn_enable.setChecked(self._driver.is_enabled())
        self._btn_enable.blockSignals(False)

    def _on_refresh_clicked(self):
        if self._driver:
            self._driver.refresh_devices()
        self._refresh_device_list()

    def _on_device_selected(self, idx: int):
        if self._driver is None:
            return
        device_index = self._device_combo.itemData(idx)
        if device_index is None or device_index < 0:
            return
        self._driver.select_device(device_index)
        # 同步轴绑定
        self._spin_axis_x.setValue(self._driver.get_axis_x())
        self._spin_axis_y.setValue(self._driver.get_axis_y())

    def _on_enable_toggled(self, checked: bool):
        if self._driver:
            self._driver.set_enabled(checked)
        self._btn_enable.setText("停用摇杆" if checked else "启用摇杆")

    def _on_devices_changed(self, devices: list):
        self._refresh_device_list()

    def _on_connection_changed(self, connected: bool, name: str):
        if connected:
            self._status_lbl.setText(f"已连接: {name}")
            _apply_label_color(self._status_lbl, _GREEN)
        else:
            self._status_lbl.setText("未连接")
            _apply_label_color(self._status_lbl, _FG_DIM)

    def _update_axis_display(self):
        if not self.isVisible():   # D4-2：对话框关闭后对象驻留，不做工
            return
        if self._driver and self._driver.is_connected() and self._driver.is_enabled():
            x = self._driver._last_x
            y = self._driver._last_y
            self._axis_display.setText(f"X: {x:.3f}  Y: {y:.3f}")
        else:
            self._axis_display.setText("X: --  Y: --")

    def _target_label(self, target_id: str) -> str:
        labels = {
            "mouse.region": "通用屏幕区域 / 鼠标模拟",
            "bridge.joystick": "已迁移：通用鼠标模拟",
            "programmer.joystick": "已迁移：通用鼠标模拟",
        }
        return labels.get(target_id, target_id)

    def _source_label(self, source: str) -> str:
        return {"xy": "双轴 XY", "x": "仅 X 轴", "y": "仅 Y 轴"}.get(source, source)

    def _binding_note(self, target_id: str, source: str) -> str:
        if target_id == "mouse.region":
            if source == "xy":
                return "X/Y 轴映射为屏幕区域内的鼠标位置"
            return "单轴鼠标模拟：未使用方向保持区域中心"
        if source == "xy":
            return "旧绑定已兼容迁移为鼠标模拟"
        return "旧单轴绑定已兼容迁移为鼠标模拟"

    def _sync_binding_table(self):
        if not hasattr(self, "_binding_table"):
            return
        self._binding_table.setRowCount(0)
        for b in self._bindings:
            row = self._binding_table.rowCount()
            self._binding_table.insertRow(row)
            target_id = str(b.get("target", ""))
            source = str(b.get("source", "xy"))
            for col, text in enumerate([self._target_label(target_id), self._source_label(source), self._binding_note(target_id, source)]):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, {"target": target_id, "source": source})
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._binding_table.setItem(row, col, item)

    def _normalize_binding(self, target_id: str, source: str) -> Dict[str, Any]:
        source = source if source in ("xy", "x", "y") else "xy"
        # 旧版本可把实体摇杆直接接入内部窗口；现在统一迁移为屏幕区域鼠标模拟。
        if target_id in ("bridge.joystick", "programmer.joystick", ""):
            target_id = "mouse.region"
        return {"target": target_id, "source": source}

    def _add_binding(self, target_id: str, source: str, notify: bool = True):
        b = self._normalize_binding(target_id, source)
        if not b["target"]:
            return
        for old in self._bindings:
            if old.get("target") == b["target"] and old.get("source") == b["source"]:
                return
        self._bindings.append(b)
        self._save_bindings()
        self._sync_binding_table()
        if notify:
            self._status_lbl.setText(f"已添加绑定: {self._target_label(target_id)} ← {self._source_label(source)}")
            _apply_label_color(self._status_lbl, _GREEN)

    def _add_binding_from_ui(self):
        self._add_binding(str(self._target_combo.currentData()), str(self._source_combo.currentData()))

    def _remove_selected_binding(self):
        rows = sorted({i.row() for i in self._binding_table.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self._bindings):
                self._bindings.pop(row)
        self._save_bindings()
        self._sync_binding_table()

    def _clear_bindings(self):
        self._bindings = []
        self._save_bindings()
        self._sync_binding_table()

    def _start_learning(self):
        self._learn_waiting = True
        self._btn_learn.setText("请推动摇杆...")
        self._status_lbl.setText("学习中：推动要绑定的摇杆轴。")
        _apply_label_color(self._status_lbl, _ORANGE)

    def _on_axis_changed(self, x: float, y: float):
        if not self._learn_waiting:
            return
        dx = abs(float(x) - 0.5)
        dy = abs(float(y) - 0.5)
        if max(dx, dy) < 0.08:
            return
        source = "xy" if (dx > 0.08 and dy > 0.08) else ("x" if dx >= dy else "y")
        self._learn_waiting = False
        self._btn_learn.setText("学习下一次摇杆动作")
        self._add_binding(str(self._target_combo.currentData()), source)

    def _save_bindings(self):
        try:
            import json
            s = QSettings("Qascade", "ExternalJoystickBindings")
            s.setValue("bindings_v2", json.dumps(self._bindings, ensure_ascii=False))
        except Exception:
            pass

    def _load_bindings(self):
        try:
            import json
            s = QSettings("Qascade", "ExternalJoystickBindings")
            raw = s.value("bindings_v2", "", type=str)
            if raw:
                data = json.loads(raw)
                if isinstance(data, list):
                    self._bindings = [self._normalize_binding(str(x.get("target", "")), str(x.get("source", "xy"))) for x in data if isinstance(x, dict) and x.get("target")]
        except Exception:
            self._bindings = []
        if not self._bindings:
            self._bindings = [{"target": "mouse.region", "source": "xy"}]
        self._sync_binding_table()

    def get_bindings(self) -> List[Dict[str, Any]]:
        return [dict(x) for x in (self._bindings or [])]

    def get_bind_targets(self) -> dict:
        return {"mouse": any(b.get("target") == "mouse.region" for b in self._bindings)}

    def _default_mouse_region(self) -> Dict[str, int]:
        screen = QApplication.primaryScreen()
        if screen is not None:
            g = screen.geometry()
            return {"x": int(g.x()), "y": int(g.y()), "w": int(g.width()), "h": int(g.height())}
        return {"x": 0, "y": 0, "w": 1920, "h": 1080}

    def _sync_region_label(self):
        if not hasattr(self, "_region_lbl"):
            return
        r = self._mouse_region or self._default_mouse_region()
        self._region_lbl.setText(f"x={int(r.get('x', 0))}, y={int(r.get('y', 0))}, w={int(r.get('w', 0))}, h={int(r.get('h', 0))}")

    def _save_mouse_region(self):
        try:
            import json
            s = QSettings("Qascade", "ExternalJoystickBindings")
            s.setValue("mouse_region_v1", json.dumps(self._mouse_region, ensure_ascii=False))
        except Exception:
            pass

    def _load_mouse_region(self):
        try:
            import json
            s = QSettings("Qascade", "ExternalJoystickBindings")
            raw = s.value("mouse_region_v1", "", type=str)
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict):
                    self._mouse_region = {
                        "x": int(data.get("x", 0)),
                        "y": int(data.get("y", 0)),
                        "w": max(8, int(data.get("w", 8))),
                        "h": max(8, int(data.get("h", 8))),
                    }
        except Exception:
            self._mouse_region = self._default_mouse_region()
        self._sync_region_label()

    def _set_mouse_region(self, region: Dict[str, int]):
        self._mouse_region = {
            "x": int(region.get("x", 0)),
            "y": int(region.get("y", 0)),
            "w": max(8, int(region.get("w", 8))),
            "h": max(8, int(region.get("h", 8))),
        }
        self._save_mouse_region()
        self._sync_region_label()

    def _choose_mouse_region(self):
        dlg = ScreenRegionSelectorDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            region = dlg.selected_region()
            if region:
                self._set_mouse_region(region)
                self._status_lbl.setText("已更新摇杆鼠标模拟区域")
                _apply_label_color(self._status_lbl, _GREEN)

    def _use_full_screen_region(self):
        self._set_mouse_region(self._default_mouse_region())
        self._status_lbl.setText("已设置为全屏鼠标模拟区域")
        _apply_label_color(self._status_lbl, _GREEN)

    def get_mouse_region(self) -> Dict[str, int]:
        return dict(self._mouse_region or self._default_mouse_region())


# ─────────────────────────────────────────────────────────────────────────────
#  全局音频来源切换条

# ─────────────────────────────────────────────────────────────────────────────
#  全局音频来源切换条
# ─────────────────────────────────────────────────────────────────────────────
class GlobalAudioSourceBar(QWidget):
    """
    全局音频来源切换条，嵌入主界面 TransportBar 右侧区域。

    包含：
      - 来源切换按钮组（模拟 / 麦克风 / 时间线音频）
      - 麦克风设备选择下拉（仅麦克风模式可见）
      - 麦克风实时电平条（仅麦克风模式可见）
      - 外部摇杆设备按钮（打开配置对话框）
    """
    source_changed = pyqtSignal(str)   # "simulate" | "microphone" | "file"

    _SETTINGS_ORG = "Qascade"
    _SETTINGS_APP = "GlobalAudioSource"
    _SETTINGS_KEY = "source_config_v1"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_source = "simulate"
        self._mic_level_timer = QTimer(self)
        self._mic_level_timer.setInterval(50)  # 20fps 电平更新
        self._mic_level_timer.timeout.connect(self._update_mic_level)
        self._joystick_dialog: Optional[ExternalJoystickDialog] = None
        self._joystick_route_connected = False
        self._build_ui()
        self._load_settings()
        # 连接到全局音频驱动器
        self._connect_analyzer()

    def _build_ui(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(4)

        # 分隔线
        sep = QLabel("│")
        sep.setStyleSheet("color:#545454;padding:0 4px;")
        lay.addWidget(sep)

        # 来源标签
        lbl = QLabel("音频来源:")
        lbl.setStyleSheet(_LABEL_CSS.format(color=_FG_DIM))
        lay.addWidget(lbl)

        # 三个来源按钮
        self._btn_simulate = self._mk_src_btn("模拟", "simulate", _FG_DIM)
        self._btn_mic      = self._mk_src_btn("麦克风", "microphone", _CYAN)
        self._btn_file     = self._mk_src_btn("时间线", "file", _ORANGE)
        for btn in (self._btn_simulate, self._btn_mic, self._btn_file):
            lay.addWidget(btn)

        # 麦克风设备选择（仅麦克风模式可见）
        self._mic_device_combo = QComboBox()
        self._mic_device_combo.setStyleSheet(_COMBO_CSS)
        self._mic_device_combo.setMinimumWidth(130)
        self._mic_device_combo.setMaximumWidth(180)
        self._mic_device_combo.setToolTip("选择麦克风输入设备")
        self._mic_device_combo.currentIndexChanged.connect(self._on_mic_device_changed)
        lay.addWidget(self._mic_device_combo)

        # 麦克风电平条（仅麦克风模式可见）
        self._mic_level_bar = _MicLevelBar()
        self._mic_level_bar.setToolTip("麦克风实时电平")
        lay.addWidget(self._mic_level_bar)

        # 麦克风状态标签
        self._mic_status_lbl = QLabel("无输入")
        _apply_label_color(self._mic_status_lbl, _FG_DIM)
        self._mic_status_lbl.setMinimumWidth(40)
        lay.addWidget(self._mic_status_lbl)

        # 分隔线
        sep2 = QLabel("│")
        sep2.setStyleSheet("color:#545454;padding:0 4px;")
        lay.addWidget(sep2)

        # 外部摇杆按钮
        self._btn_joystick = tabler_button("外部摇杆", "device-gamepad-2", _FG_DIM)
        self._btn_joystick.setStyleSheet(
            _BTN_CSS.format(bg="#282828", fg=_FG_DIM, border="#3D3D3D",
                            hover="#3D3D3D", checked_bg=_ACCENT, checked_fg="#000"))
        self._btn_joystick.setToolTip("配置外部游戏摇杆设备")
        self._btn_joystick.clicked.connect(self._open_joystick_dialog)
        lay.addWidget(self._btn_joystick)

        # 初始状态：隐藏麦克风相关控件
        self._set_mic_widgets_visible(False)
        # 默认选中模拟
        self._btn_simulate.setChecked(True)

    def _mk_src_btn(self, text: str, source_id: str, color: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setFixedHeight(22)
        btn.setStyleSheet(
            _BTN_CSS.format(
                bg="#282828", fg=_FG_DIM, border="#3D3D3D",
                hover="#3D3D3D", checked_bg=color, checked_fg="#000"))
        btn.clicked.connect(lambda checked, s=source_id: self._on_source_btn_clicked(s))
        return btn

    def _set_mic_widgets_visible(self, visible: bool):
        self._mic_device_combo.setVisible(visible)
        self._mic_level_bar.setVisible(visible)
        self._mic_status_lbl.setVisible(visible)
        if visible:
            self._refresh_mic_devices()
            self._mic_level_timer.start()
        else:
            self._mic_level_timer.stop()

    def _on_source_btn_clicked(self, source: str):
        """来源按钮点击处理。"""
        self._current_source = source
        # 更新按钮状态
        self._btn_simulate.setChecked(source == "simulate")
        self._btn_mic.setChecked(source == "microphone")
        self._btn_file.setChecked(source == "file")
        # 显示/隐藏麦克风控件
        self._set_mic_widgets_visible(source == "microphone")
        # 切换全局音频分析器模式
        try:
            from .audio_driver import get_audio_driver
            analyzer = get_audio_driver()
            analyzer.set_mode(source)
        except Exception as e:
            print(f"[GlobalAudioSourceBar] 切换来源失败: {e}")
        self.source_changed.emit(source)
        self._save_settings()

    def _refresh_mic_devices(self):
        """刷新麦克风设备列表。"""
        try:
            from .audio_driver import AudioAnalyzer
            devices = AudioAnalyzer.list_mic_devices()
            self._mic_device_combo.blockSignals(True)
            self._mic_device_combo.clear()
            self._mic_device_combo.addItem("系统默认", None)
            for idx, name in devices:
                self._mic_device_combo.addItem(f"{name}", idx)
            self._mic_device_combo.blockSignals(False)
            # 恢复之前选中的设备
            try:
                from .audio_driver import get_audio_driver
                current_dev = get_audio_driver().get_mic_device()
                if current_dev is not None:
                    for i in range(self._mic_device_combo.count()):
                        if self._mic_device_combo.itemData(i) == current_dev:
                            self._mic_device_combo.setCurrentIndex(i)
                            break
            except Exception:
                pass
        except Exception as e:
            print(f"[GlobalAudioSourceBar] 刷新麦克风设备失败: {e}")

    def _on_mic_device_changed(self, idx: int):
        """麦克风设备选择变化。"""
        device_index = self._mic_device_combo.itemData(idx)
        try:
            from .audio_driver import get_audio_driver
            get_audio_driver().set_mic_device(device_index)
        except Exception as e:
            print(f"[GlobalAudioSourceBar] 设置麦克风设备失败: {e}")

    def _update_mic_level(self):
        """更新麦克风电平条（从全局音频驱动器读取 RMS）。"""
        try:
            from .audio_driver import get_audio_driver
            analyzer = get_audio_driver()
            # 读取最新帧的 RMS
            if hasattr(analyzer, '_last_rms'):
                level = float(analyzer._last_rms)
            else:
                level = 0.0
            self._mic_level_bar.set_level(level)
            # 更新状态标签
            if level > 0.01:
                self._mic_status_lbl.setText(f"{level:.2f}")
                _apply_label_color(self._mic_status_lbl, _GREEN)
            else:
                self._mic_status_lbl.setText("无输入")
                _apply_label_color(self._mic_status_lbl, _FG_DIM)
        except Exception:
            pass

    def _open_joystick_dialog(self):
        """打开外部摇杆配置对话框。"""
        if self._joystick_dialog is None or not self._joystick_dialog.isVisible():
            # ★ 被替换的旧对话框仍存活（parent 为本控件，不会自动析构），先断开它
            #   与驱动单例的连接，避免旧实例的槽继续挂在单例上累积（见 _disconnect_driver）。
            if self._joystick_dialog is not None:
                try:
                    self._joystick_dialog._disconnect_driver()
                except Exception:
                    pass
            self._joystick_dialog = ExternalJoystickDialog(self)
            # 连接摇杆信号到绑定目标
            self._connect_joystick_to_targets(self._joystick_dialog)
        self._joystick_dialog.show()
        self._joystick_dialog.raise_()

    def _connect_joystick_to_targets(self, dialog: ExternalJoystickDialog):
        """将实体摇杆的 axis_changed 信号接入统一绑定路由器。"""
        try:
            from .physical_joystick_driver import get_physical_joystick_driver
            driver = get_physical_joystick_driver()
            if not self._joystick_route_connected:
                driver.axis_changed.connect(self._route_external_joystick)
                self._joystick_route_connected = True
        except Exception as e:
            print(f"[GlobalAudioSourceBar] 连接摇杆目标失败: {e}")

    def _load_joystick_bindings(self) -> List[Dict[str, Any]]:
        if self._joystick_dialog is not None and hasattr(self._joystick_dialog, "get_bindings"):
            bindings = self._joystick_dialog.get_bindings()
            if bindings:
                return bindings
        try:
            import json
            qs = QSettings("Qascade", "ExternalJoystickBindings")
            raw = qs.value("bindings_v2", "", type=str)
            if raw:
                data = json.loads(raw)
                if isinstance(data, list):
                    return [x for x in data if isinstance(x, dict) and x.get("target")]
        except Exception:
            pass
        return [{"target": "mouse.region", "source": "xy"}]

    def _axis_pair_for_source(self, x: float, y: float, source: str):
        if source == "x":
            return float(x), 0.5
        if source == "y":
            return 0.5, float(y)
        return float(x), float(y)

    def _route_external_joystick(self, x: float, y: float):
        for b in self._load_joystick_bindings():
            target = str(b.get("target", ""))
            source = str(b.get("source", "xy"))
            tx, ty = self._axis_pair_for_source(x, y, source)
            # ★ 放缩：中位对称输出 = 中位 + (原始 − 中位) × 比例（与绑定路由一致）
            try:
                from .joystick_control_binding import get_joystick_bind_manager
                mgr = get_joystick_bind_manager()
                tx = 0.5 + (tx - 0.5) * mgr.get_axis_scale("x")
                ty = 0.5 + (ty - 0.5) * mgr.get_axis_scale("y")
            except Exception:
                pass
            if target in ("mouse.region", "bridge.joystick", "programmer.joystick"):
                self._inject_to_mouse_region(tx, ty)

    def _inject_to_mouse_region(self, x: float, y: float):
        """把实体摇杆归一化坐标映射成全局鼠标位置。"""
        try:
            region = None
            if self._joystick_dialog is not None and hasattr(self._joystick_dialog, "get_mouse_region"):
                region = self._joystick_dialog.get_mouse_region()
            if not region:
                import json
                qs = QSettings("Qascade", "ExternalJoystickBindings")
                raw = qs.value("mouse_region_v1", "", type=str)
                region = json.loads(raw) if raw else None
            if not isinstance(region, dict):
                screen = QApplication.primaryScreen()
                g = screen.geometry() if screen is not None else QRect(0, 0, 1920, 1080)
                region = {"x": g.x(), "y": g.y(), "w": g.width(), "h": g.height()}
            rx = int(region.get("x", 0)); ry = int(region.get("y", 0))
            rw = max(8, int(region.get("w", 8))); rh = max(8, int(region.get("h", 8)))
            px = rx + int(max(0.0, min(1.0, float(x))) * max(1, rw - 1))
            py = ry + int(max(0.0, min(1.0, float(y))) * max(1, rh - 1))
            QCursor.setPos(px, py)
        except Exception:
            pass

    def _inject_to_bridge_joystick(self, x: float, y: float):
        """将实体摇杆值注入到音频桥接器的摇杆绑定面板。"""
        try:
            # 通过父链查找 MainWindow → AudioBridgePanel
            mw = self._find_main_window()
            if mw is None:
                return
            abw = getattr(mw, '_audio_bridge_widget', None)
            if abw is None:
                return
            joy_panel = getattr(abw, '_joy_panel', None)
            if joy_panel is not None and hasattr(joy_panel, 'inject_external_joystick'):
                joy_panel.inject_external_joystick(x, y)
        except Exception:
            pass

    def _inject_to_programmer_joystick(self, x: float, y: float):
        """将实体摇杆值注入到 Programmer 的虚拟摇杆。"""
        try:
            mw = self._find_main_window()
            if mw is None:
                return
            pw = getattr(mw, '_programmer_widget', None)
            if pw is None:
                return
            # 查找 XYJoystick 控件
            if hasattr(pw, '_joystick') and hasattr(pw._joystick, 'set_position'):
                pw._joystick.set_position(x, y)
            elif hasattr(pw, 'inject_external_joystick'):
                pw.inject_external_joystick(x, y)
        except Exception:
            pass

    def _find_main_window(self):
        """向上查找 MainWindow。"""
        p = self.parent()
        while p is not None:
            if hasattr(p, '_audio_bridge_widget') and hasattr(p, '_programmer_widget'):
                return p
            p = p.parent() if hasattr(p, 'parent') else None
        return None

    # ── 持久化 ───────────────────────────────────────────────────────────────

    def _save_settings(self):
        try:
            import json
            s = QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)
            data = {"source": self._current_source}
            s.setValue(self._SETTINGS_KEY, json.dumps(data))
        except Exception:
            pass

    def _load_settings(self):
        try:
            import json
            s = QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)
            raw = s.value(self._SETTINGS_KEY, "", type=str)
            if raw:
                data = json.loads(raw)
                src = data.get("source", "simulate")
                # 延迟应用（等 UI 完全初始化后）
                QTimer.singleShot(200, lambda: self._on_source_btn_clicked(src))
        except Exception:
            pass

    def _connect_analyzer(self):
        """连接到全局音频分析器，用于更新电平条。"""
        try:
            from .audio_driver import get_audio_driver
            analyzer = get_audio_driver()
            # 监听 energy_updated 信号，缓存 RMS 值
            analyzer.energy_updated.connect(self._on_energy_updated)
        except Exception:
            pass

    def _on_energy_updated(self, rms: float):
        """接收音频能量更新，缓存到 _last_rms（供电平条读取）。"""
        try:
            from .audio_driver import get_audio_driver
            get_audio_driver()._last_rms = rms
        except Exception:
            pass

    def get_current_source(self) -> str:
        return self._current_source
