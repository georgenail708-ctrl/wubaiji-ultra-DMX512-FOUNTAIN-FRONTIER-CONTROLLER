"""
screen_capture_overlay.py — 屏幕区域采样选取框
=====================================================
可拖动/缩放的半透明叠加窗口，用于交互式选定屏幕像素采样区域。

设计参考：视频录制器的采集区域选取框（OBS / Bandicam 风格）
  - 无边框、置顶、透明背景
  - 绿色边框 + 角落缩放手柄
  - 可叠加显示设备网格划分（自动根据设备数量/布局计算）
  - 确认后发出 region_confirmed(dict) 信号

使用方式：
    overlay = ScreenCaptureOverlay()
    overlay.set_grid(cols, rows)          # 可选：显示设备网格
    overlay.region_confirmed.connect(cb)  # cb({"left","top","width","height"})
    overlay.show()
"""
from __future__ import annotations
from ..main_icon_library import load_tabler_icon

from typing import Optional

from PyQt6.QtWidgets import QWidget, QPushButton, QLabel, QApplication
from PyQt6.QtCore import Qt, pyqtSignal, QRect, QPoint
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QCursor, QFont

# ─── 常量 ────────────────────────────────────────────────────────────────────
BORDER_W  = 8        # 可拖动边框宽度（像素）
CORNER_R  = 18       # 角落手柄检测半径
MIN_W     = 120      # 窗口最小宽度
MIN_H     = 80       # 窗口最小高度

_NONE  = 0           # 无操作
_MOVE  = 1           # 整体拖动
_TL    = 2           # top-left 角
_TR    = 3           # top-right 角
_BL    = 4           # bottom-left 角
_BR    = 5           # bottom-right 角
_L     = 6           # 左边
_R     = 7           # 右边
_T     = 8           # 上边
_B     = 9           # 下边

_CURSOR_MAP = {
    _NONE: Qt.CursorShape.ArrowCursor,
    _MOVE: Qt.CursorShape.SizeAllCursor,
    _TL:   Qt.CursorShape.SizeFDiagCursor,
    _BR:   Qt.CursorShape.SizeFDiagCursor,
    _TR:   Qt.CursorShape.SizeBDiagCursor,
    _BL:   Qt.CursorShape.SizeBDiagCursor,
    _L:    Qt.CursorShape.SizeHorCursor,
    _R:    Qt.CursorShape.SizeHorCursor,
    _T:    Qt.CursorShape.SizeVerCursor,
    _B:    Qt.CursorShape.SizeVerCursor,
}

# 采样框颜色
_BORDER_COLOR = QColor(60, 210, 110, 230)   # 绿色边框
_HANDLE_COLOR = QColor(140, 255, 160, 255)  # 角落手柄亮绿
_GRID_COLOR   = QColor(60, 210, 110, 70)    # 网格线（半透明）
_DOT_COLOR    = QColor(60, 210, 110, 140)   # 设备格中心点
_INNER_COLOR  = QColor(0, 0, 0, 30)         # 内区域轻微暗化
_LABEL_BG     = QColor(10, 20, 10, 190)     # 信息标签背景


class ScreenCaptureOverlay(QWidget):
    """
    屏幕区域采样选取框。

    信号：
        region_confirmed(dict)  —  用户点击"确认"时发出，
            dict = {"left": int, "top": int, "width": int, "height": int}
            坐标为全局屏幕坐标，已去除边框宽度，对应实际采样区域。
    """

    region_confirmed = pyqtSignal(dict)

    def __init__(self, parent=None):
        # parent=None 使其成为独立顶层窗口，不随主窗口关闭
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMouseTracking(True)

        # 设备网格（可选，用于叠加显示）
        self._grid_cols: int = 0
        self._grid_rows: int = 0

        # 拖拽/缩放状态
        self._op: int = _NONE
        self._drag_start_global: Optional[QPoint] = None
        self._drag_start_geom:   Optional[QRect]  = None

        # 初始位置：主屏幕居中 400×300
        screen = QApplication.primaryScreen()
        if screen:
            sg = screen.geometry()
            w, h = 400, 300
            self.setGeometry(
                sg.x() + (sg.width()  - w) // 2,
                sg.y() + (sg.height() - h) // 2,
                w, h,
            )
        else:
            self.setGeometry(200, 200, 400, 300)

        self.setMinimumSize(MIN_W, MIN_H)
        self._build_controls()

    # ── 公共接口 ─────────────────────────────────────────────────────────────

    def set_grid(self, cols: int, rows: int):
        """可选：在采样框内叠加显示 cols×rows 的设备网格划分。"""
        self._grid_cols = max(0, cols)
        self._grid_rows = max(0, rows)
        self.update()

    # ── 内部控件 ─────────────────────────────────────────────────────────────

    def _build_controls(self):
        btn_style_ok = (
            "QPushButton{background:#0D3A1A;color:#44FF88;"
            "border:1.5px solid #44FF88;border-radius:4px;"
            "padding:4px 14px;font-size:11px;font-weight:bold;}"
            "QPushButton:hover{background:#1A5A2A;}"
        )
        btn_style_cl = (
            "QPushButton{background:#3A0D0D;color:#E36855;"    # 文字/描边→bright_error 档（原 #FF6666）；暗红语义容器保留
            "border:1.5px solid #E36855;border-radius:4px;"
            "padding:4px 8px;font-size:11px;}"
            "QPushButton:hover{background:#5A1A1A;}"
        )
        lbl_style = (
            f"QLabel{{color:#AAFFCC;background:rgba(10,20,10,0.75);"
            f"border:1px solid #226633;border-radius:3px;"
            f"padding:1px 6px;font-size:10px;font-family:Consolas;}}"
        )

        self._confirm_btn = QPushButton("确认采样区域", self)
        self._confirm_btn.setStyleSheet(btn_style_ok)
        self._confirm_btn.clicked.connect(self._on_confirm)
        self._confirm_btn.adjustSize()

        self._close_btn = QPushButton(load_tabler_icon("x", 14), "", self)
        self._close_btn.setStyleSheet(btn_style_cl)
        self._close_btn.setFixedWidth(30)
        self._close_btn.setToolTip("隐藏采样框")
        self._close_btn.adjustSize()
        self._close_btn.clicked.connect(self.hide)

        self._info_lbl = QLabel("", self)
        self._info_lbl.setStyleSheet(lbl_style)

        self._reposition_controls()

    def _reposition_controls(self):
        W, H = self.width(), self.height()
        bh = self._confirm_btn.height()
        bw = self._confirm_btn.width()

        # 信息标签 — 左上角（在边框内）
        self._info_lbl.setText(
            f"  {max(0, W - 2*BORDER_W)} × {max(0, H - 2*BORDER_W)} px"
            + (f"  ·  网格 {self._grid_cols}×{self._grid_rows}"
               if self._grid_cols > 0 else ""))
        self._info_lbl.adjustSize()
        self._info_lbl.move(BORDER_W + 4, BORDER_W + 4)

        # 确认按钮 — 右下角（在边框内）
        self._confirm_btn.move(
            W - bw - BORDER_W - 36, H - bh - BORDER_W - 4)

        # 关闭按钮 — 确认按钮右侧
        self._close_btn.move(
            W - 30 - BORDER_W - 2, H - self._close_btn.height() - BORDER_W - 4)

    # ── 确认 ─────────────────────────────────────────────────────────────────

    def _on_confirm(self):
        geom = self.geometry()
        region = {
            "left":   geom.x() + BORDER_W,
            "top":    geom.y() + BORDER_W,
            "width":  max(1, geom.width()  - 2 * BORDER_W),
            "height": max(1, geom.height() - 2 * BORDER_W),
        }
        self.hide()                       # 先隐藏，避免截图到自己
        self.region_confirmed.emit(region)

    # ── 绘制 ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        bw = BORDER_W
        inner = QRect(bw, bw, W - 2*bw, H - 2*bw)

        # ── 内区域（轻微暗化，增强边框可见性）──
        p.setBrush(QBrush(_INNER_COLOR))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(inner)

        # ── 边框（四条不透明矩形条）──
        p.setBrush(QBrush(_BORDER_COLOR))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(0, 0, W, bw)                      # 上
        p.drawRect(0, H - bw, W, bw)                 # 下
        p.drawRect(0, bw, bw, H - 2*bw)              # 左
        p.drawRect(W - bw, bw, bw, H - 2*bw)        # 右

        # ── 内边框虚线轮廓（帮助用户看清边界）──
        p.setPen(QPen(_BORDER_COLOR, 1, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(bw, bw, W - 2*bw, H - 2*bw)

        # ── 角落缩放手柄 ──
        p.setBrush(QBrush(_HANDLE_COLOR))
        p.setPen(QPen(QColor(0, 0, 0, 100), 1))
        r = CORNER_R // 2
        for cx, cy in [(0, 0), (W, 0), (0, H), (W, H)]:
            p.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        # ── 设备网格叠加 ──
        if self._grid_cols > 0 and self._grid_rows > 0:
            gx, gy = inner.x(), inner.y()
            gw, gh = inner.width(), inner.height()
            cw = gw / self._grid_cols
            ch = gh / self._grid_rows

            p.setPen(QPen(_GRID_COLOR, 1, Qt.PenStyle.DotLine))
            for c in range(1, self._grid_cols):
                x = int(gx + c * cw)
                p.drawLine(x, gy, x, gy + gh)
            for r_ in range(1, self._grid_rows):
                y = int(gy + r_ * ch)
                p.drawLine(gx, y, gx + gw, y)

            # 每格中心画小圆（代表一台设备）
            p.setBrush(QBrush(_DOT_COLOR))
            p.setPen(Qt.PenStyle.NoPen)
            dot_r = max(2, int(min(cw, ch) * 0.15))
            for c in range(self._grid_cols):
                for r_ in range(self._grid_rows):
                    cx = int(gx + (c + 0.5) * cw)
                    cy = int(gy + (r_ + 0.5) * ch)
                    p.drawEllipse(cx - dot_r, cy - dot_r, dot_r*2, dot_r*2)

        # ── 标题提示文字 ──
        p.setFont(QFont("Arial", 8))
        p.setPen(QColor(120, 255, 150, 180))
        hint = "  拖动边框移动 · 拖角缩放"
        p.drawText(QRect(bw, H - bw - 14, W - 2*bw, 12),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   hint)

    # ── 事件 ─────────────────────────────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_controls()
        self.update()

    def _hit_test(self, pos: QPoint) -> int:
        W, H = self.width(), self.height()
        x, y = pos.x(), pos.y()
        if x <= CORNER_R and y <= CORNER_R:          return _TL
        if x >= W - CORNER_R and y <= CORNER_R:      return _TR
        if x <= CORNER_R and y >= H - CORNER_R:      return _BL
        if x >= W - CORNER_R and y >= H - CORNER_R:  return _BR
        if x <= BORDER_W:   return _L
        if x >= W - BORDER_W: return _R
        if y <= BORDER_W:   return _T
        if y >= H - BORDER_W: return _B
        return _MOVE  # 内部 = 整体移动

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._op = self._hit_test(event.pos())
            self._drag_start_global = event.globalPosition().toPoint()
            self._drag_start_geom  = self.geometry()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._op = _NONE
            self.unsetCursor()
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        if self._op == _NONE:
            # 悬停时实时更新光标
            h = self._hit_test(event.pos())
            self.setCursor(QCursor(_CURSOR_MAP.get(h, Qt.CursorShape.ArrowCursor)))
        elif self._drag_start_global and self._drag_start_geom:
            delta = event.globalPosition().toPoint() - self._drag_start_global
            dx, dy = delta.x(), delta.y()
            g = self._drag_start_geom
            op = self._op

            if op == _MOVE:
                self.move(g.x() + dx, g.y() + dy)
            elif op == _TL:
                self.setGeometry(g.x()+dx, g.y()+dy,
                                 max(MIN_W, g.width()-dx), max(MIN_H, g.height()-dy))
            elif op == _TR:
                self.setGeometry(g.x(), g.y()+dy,
                                 max(MIN_W, g.width()+dx), max(MIN_H, g.height()-dy))
            elif op == _BL:
                self.setGeometry(g.x()+dx, g.y(),
                                 max(MIN_W, g.width()-dx), max(MIN_H, g.height()+dy))
            elif op == _BR:
                self.resize(max(MIN_W, g.width()+dx), max(MIN_H, g.height()+dy))
            elif op == _L:
                self.setGeometry(g.x()+dx, g.y(), max(MIN_W, g.width()-dx), g.height())
            elif op == _R:
                self.resize(max(MIN_W, g.width()+dx), g.height())
            elif op == _T:
                self.setGeometry(g.x(), g.y()+dy, g.width(), max(MIN_H, g.height()-dy))
            elif op == _B:
                self.resize(g.width(), max(MIN_H, g.height()+dy))

            self._reposition_controls()

        super().mouseMoveEvent(event)
