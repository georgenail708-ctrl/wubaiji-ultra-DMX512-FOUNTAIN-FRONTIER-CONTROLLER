""" 
Qascade V1.0 — Channel Grid
512通道网格：实时显示数值 + 框选/多选

R183 D 拆分（文件 ≤500 行门禁）：
  * 峰值保持 → peak_hold.py（本模块 re-export 保兼容）
  * 温标色板 / 数值格 / 热图绘制 → channel_grid_paint.py（Mixin）
  * 快照对比 / delta → channel_grid_delta.py（本模块 re-export 保兼容）

R183 W2 拆分（深查② §四 续批）：
  * 地址标注 / 冻结角标 → channel_grid_overlay.py（Mixin）
  * 键盘导航（方向键/Shift/Home/End）→ channel_grid_keys.py（Mixin）
  * 变化行集合（渲染优化）→ monitor_dirty.py
"""

from __future__ import annotations

import numpy as np
from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import Qt, QRect, QPoint, pyqtSignal, QSize
from PyQt6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont,
    QPaintEvent, QMouseEvent, QKeyEvent,
)

# S1 令牌化：画布配色重定向到中心主题令牌
from ..qascade_theme import (  # noqa: F401  （主题令牌 re-export：既有引用面保持）
    BG_ROOT, BG_DEEP, BG_PANEL_SUB,
    SELECT_BLUE, FOCUS_BLUE, BORDER_NORMAL, BG_BUTTON,
    TEXT_SECONDARY, TEXT_DISABLED, TEXT_SELECTED_FG, BRIGHT_INFO,
    cached_font, MONO_FONT_FAMILIES,
)

# R183 D：拆分模块（常量/函数 re-export 保持既有 import 路径可用）
from .peak_hold import (  # noqa: F401  （拆分前公开名，删除破坏引用）
    PEAK_HOLD_CHANNELS, PEAK_HOLD_SEC, PeakHold,
)
from .channel_grid_paint import (  # noqa: F401  （温标/绘制助手 re-export）
    HEAT_STOPS, HEAT_ZERO, HEAT_HOT, HEAT_GAP, HEAT_LEGEND_OPACITY,
    ChannelGridPaintMixin, _heatmap_color, _heat_lut,
)
from .channel_grid_delta import (  # noqa: F401  （delta 常量/助手 re-export）
    DELTA_EPS, DELTA_GOLD, DELTA_GOLD_DIM, DELTA_MARK,
    draw_delta_band, draw_delta_cell, draw_delta_mark,
    delta_mask_of, normalize_snapshot,
)
from .channel_grid_overlay import (  # noqa: F401  （W2：标注/冻结角标 Mixin）
    ANNOT_BAND_H, ANNOT_OPACITY, ChannelGridOverlayMixin,
)
from .channel_grid_keys import ChannelGridKeysMixin
from .channel_grid_groups import (  # noqa: F401  （W2：行几何/折叠组头 Mixin）
    ROW_BAND_H, GROUP_BAND_BG, ChannelGridGroupsMixin,
)
from .monitor_dirty import changed_rows, needs_full_repaint

COLS = 32       # 每行32个通道
ROWS = 16       # 16行 = 512个
CELL_W = 46
CELL_H = 38
MARGIN = 4
# R56 用户指令：顶部列号行（1 5 9 13…）删除——与格子内自带通道号重复，
# 白占一行。保留常量为 0 以免触碰所有 y 映射公式（paint/命中测试共用）。
RULER_H = 0

# R186-UP03②：行带裁剪（clip=None 全量；否则按脏区相交）
def _row_clipped(clip, y, row_h, w):
    return clip is None or clip.intersects(QRect(0, int(y) - 1, w, int(row_h) + 2))


class ChannelGrid(ChannelGridKeysMixin, ChannelGridOverlayMixin,
                  ChannelGridGroupsMixin, ChannelGridPaintMixin, QWidget):
    """
    512通道网格
    - 实时显示每个通道的DMX值（0-255）
    - 颜色深浅反映值大小
    - 支持：单击、Ctrl多选、Shift连续选、鼠标框选
    """
    selection_changed = pyqtSignal(set)   # 当前选中的通道集合（1-based）

    # 绘制 Mixin 用的几何别名（常量仍以模块级 COLS/ROWS/CELL_* 为准）
    _cols = COLS
    _rows = ROWS
    _cell_w = CELL_W
    _cell_h = CELL_H
    _margin = MARGIN
    _ruler_h = RULER_H

    def __init__(self, parent=None):
        super().__init__(parent)
        self._display_mode = "value" # ★ R178/深查②#1：value|heatmap|delta
        self._data = np.zeros(512, dtype=np.uint8)
        self._snapshot: np.ndarray | None = None   # 快照对比基准（深查②#1）
        self._compare = False                      # 比快照高亮开关
        self._selected: set = set()        # 1-based channel numbers
        self._last_clicked: int = 0        # Shift连续选的起点

        # 深查②#7：行分组（row_idx → 灯具标签）与折叠行集合
        self._row_groups: dict = {}        # {0-15: label}
        self._collapsed: set = set()       # 已折叠的行号

        # 深查②#10：右键菜单回调（panel 注入；签名 cb(grid, QPoint, set)）
        self._context_cb = None

        # 深查②#5：冻结——照常收数（_live 留最新），但 _data 定格不写
        self._frozen = False
        self._freeze_label = "冻结"          # 深查②#14：回放时改文案
        self._live = self._data.copy()
        # 深查②#13：地址标注叠加 {行: [(列, 列数, 标签)]}
        self._annotation_spans: dict = {}
        self._annotation_address = False
        # 深查②#12：键盘游标（与 _last_clicked 的鼠标锚点分开记账）
        self._cursor = 0

        # 框选状态
        self._drag_start: QPoint = QPoint()
        self._drag_current: QPoint = QPoint()
        self._dragging = False
        self._drag_sel_base: set = set()  # 框选开始前的选择状态

        # 高亮通道（鼠标悬停）
        self._hover_ch: int = 0

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)

    # ── 行几何（深查②#7 折叠行；W2 起几何唯一出处=channel_grid_groups）──

    def _col_x(self, col: int) -> int:
        """列号 → 该列左沿 x（数值/标注共用，含左 MARGIN）。"""
        return MARGIN + int(col) * CELL_W


    def _row_rect(self, row: int) -> QRect:
        """行矩形（深查②#15 脏区重绘用；宽=整表宽，含 1px 冗余防边缘残影）。"""
        if row < 0 or row >= ROWS:
            return QRect()
        return QRect(0, self._row_top(row) - 1,
                     COLS * CELL_W + MARGIN * 2, self._row_h(row) + 2)

    def sizeHint(self) -> QSize:
        w = COLS * CELL_W + MARGIN * 2
        return QSize(w, self._total_h() + 2)

    # ── 数据更新 ──────────────────────────────────

    @property
    def _heatmap_mode(self) -> bool:
        """R178 兼容读口：热图模式即 display_mode == 'heatmap'。"""
        return self._display_mode == "heatmap"

    def update_data(self, data: np.ndarray):
        """更新512通道数据（冻结时只留 _live 不写 _data；变化行局部重绘）"""
        if len(data) < 512:
            return
        arr = np.asarray(data[:512], dtype=np.uint8)
        self._live = arr.copy()
        if self._frozen:
            return
        if np.array_equal(self._data, arr):
            return
        rows = changed_rows(self._data, arr, COLS, ROWS)
        self._data = arr.copy()
        if needs_full_repaint(rows):
            self.update()
        else:
            for row in rows:
                self.update(self._row_rect(row))

    def get_data(self) -> np.ndarray:
        """当前显示帧（深查②：快照/导出/录制取数共用）。"""
        return self._data.copy()

    def get_live_data(self) -> np.ndarray:
        """最新到帧（冻结时 = 定格帧之后的实时值；未冻结与 _data 同值）。"""
        return self._live.copy()

    # ── 冻结（深查②#5）──────────────────────────────

    def set_frozen(self, on: bool) -> bool:
        """定格当前显示帧（继续收数）；解冻立即跳到最新帧。返回新状态。"""
        on = bool(on)
        if on == self._frozen:
            return self._frozen
        self._frozen = on
        if not on:
            self._data = self._live.copy()
        self.update()
        return self._frozen

    def is_frozen(self) -> bool:
        return self._frozen

    def set_freeze_label(self, text: str) -> None:
        """定格角标文案（"冻结"=手动；"回放"=时间轴回放，深查②#14）。"""
        self._freeze_label = str(text or "冻结")
        if self._frozen:
            self.update()

    def show_frame(self, frame) -> None:
        """回放帧：直接写显示帧并定格（不动 _live；滑条拖动路径专用）。"""
        try:
            arr = np.asarray(frame, dtype=np.uint8).reshape(-1)[:512]
        except (TypeError, ValueError):
            return
        if arr.size < 512:
            padded = np.zeros(512, dtype=np.uint8)
            padded[: arr.size] = arr
            arr = padded
        self._frozen = True
        self._data = arr.copy()
        self.update()

    # ── 快照对比（深查②#1；逻辑在 channel_grid_delta）──────

    def set_snapshot(self, snap: np.ndarray | None) -> None:
        """存/清参考快照（None=清除）。比较基准取前 512 字节。"""
        self._snapshot = normalize_snapshot(snap)
        self.update()

    def set_compare(self, on: bool) -> None:
        """比快照高亮开关（变化通道金色角标，数值/热图模式都生效）。"""
        if self._compare == bool(on):
            return
        self._compare = bool(on)
        self.update()

    def delta_mask(self) -> np.ndarray | None:
        """|当前-快照|>DELTA_EPS 的布尔掩码；无快照返回 None。"""
        if self._snapshot is None:
            return None
        return delta_mask_of(self._data, self._snapshot)

    # set_row_groups / toggle_row / collapse_all → channel_grid_groups Mixin

    # ── 右键菜单（深查②#10）───────────────────────────

    def set_context_menu_callback(self, cb) -> None:
        """注入 cb(grid, global_pos, selected_set)，由 panel 构建菜单。"""
        self._context_cb = cb

    def contextMenuEvent(self, e):
        if self._context_cb is None:
            super().contextMenuEvent(e)
            return
        if not self._selected:
            ch = self._pos_to_channel(e.pos())
            if ch > 0:   # 右键空白选中集时：先选中点中的通道再弹菜单
                self._selected = {ch}
                self.selection_changed.emit(self._selected.copy())
                self.update()
        self._context_cb(self, e.globalPos(), self._selected.copy())

    # ── 绘制 ─────────────────────────────────────

    def paintEvent(self, event: QPaintEvent):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # 背景
        p.fillRect(self.rect(), QColor(BG_ROOT))

        # audit-B：原 7/8pt 在 1080p 不可读 → 换像素字号（≥10px）
        # R183 横向性能批：QFont 逐帧构造 → cached_font 模块级缓存（共享只读）
        font_small = cached_font(10, families=MONO_FONT_FAMILIES)
        font_val   = cached_font(11, True, families=MONO_FONT_FAMILIES)
        font_band  = cached_font(11, families=MONO_FONT_FAMILIES)

        # ★ R178：热图模式——整格色块（值→暖色温标），无文字，格子密铺满窗
        if self._display_mode == "heatmap":
            self._paint_heatmap(p)
            if self._annotation_spans:
                self._paint_heatmap_annotations(p)
            if self._row_groups:
                self._paint_heatmap_group_chips(p)
        else:
            vr = event.rect()
            clip = vr if (vr.width() < self.width() or vr.height() < self.height()) else None
            self._paint_rows(p, font_small, font_val, font_band, clip=clip)

        # 深查②#5：冻结角标（定格提示，任何显示模式都叠）
        if self._frozen:
            self._paint_frozen_badge(p)

        # 绘制框选矩形
        if self._dragging:
            rect = self._get_drag_rect()
            p.setPen(QPen(QColor(FOCUS_BLUE), 1, Qt.PenStyle.DashLine))
            p.setBrush(QBrush(QColor(113, 168, 255, 35)))   # FOCUS_BLUE α35
            p.drawRect(rect)

        p.end()

    def _paint_rows(self, p: QPainter, font_small, font_val, font_band,
                    *, clip: QRect = None) -> None:
        """数值/Δ值模式逐行绘制；折叠行画组头条（深查②#7）。

        R186-UP03②：行级脏区裁剪（修前 paint 发满 16 行×512 格调用；实测
        1 行变化 22ms→1.65ms）。clip=None 行为不变；行内元素随行裁剪安全。"""
        mode_delta = (self._display_mode == "delta")
        mask = self.delta_mask() if (self._compare or mode_delta) else None
        for row in range(ROWS):
            y = self._row_top(row)
            if not _row_clipped(clip, y, self._row_h(row), self.width()):
                continue
            if row in self._collapsed:
                self._draw_group_band(p, row, y, font_band)
            else:
                self._paint_row_cells(p, row=row, y=y, font_small=font_small,
                                      font_val=font_val, mode_delta=mode_delta,
                                      mask=mask)
                # 深查②#13：地址标注叠加（数值模式贴行下沿）
                if self._annotation_spans:
                    self._paint_row_annotations(p, row=row, y=y, cell_h=CELL_H)
            # 深查②#6：展开态组起始行画折叠小标（点击即折叠）
            if row in self._row_groups and row not in self._collapsed:
                self._draw_group_chip(p, row, font_band)

    def _paint_row_cells(self, p: QPainter, *, row: int, y: int,
                         font_small, font_val, mode_delta: bool,
                         mask) -> None:
        """单行 32 格（数值或 Δ值模式）+ 变化角标。"""
        base = row * COLS
        for col in range(COLS):
            idx = base + col
            ch = idx + 1
            x = MARGIN + col * CELL_W
            selected = ch in self._selected
            hovered = ch == self._hover_ch
            val = int(self._data[idx])
            if mode_delta and self._snapshot is not None:
                self._draw_delta_cell(p, x=x, y=y, ch=ch, val=val,
                                      snap=int(self._snapshot[idx]),
                                      selected=selected, hovered=hovered,
                                      font_small=font_small)
            else:
                self._draw_cell(p, x=x, y=y, ch=ch, val=val, selected=selected,
                                hovered=hovered, font_small=font_small,
                                font_val=font_val)
            if mask is not None and mask[idx]:
                self._draw_delta_mark(p, right=x + CELL_W, top=y)

    def _draw_delta_mark(self, p: QPainter, *, right: float, top: float) -> None:
        """金色角标（右上三角）：|当前-快照|>DELTA_EPS 的通道。"""
        draw_delta_mark(p, right, top)

    def _draw_delta_cell(self, p: QPainter, *, x: int, y: int, ch: int,
                         val: int, snap: int, selected: bool, hovered: bool,
                         font_small: QFont) -> None:
        """Δ值模式格：背景按 |delta| 强度，文本为带符号差值。"""
        draw_delta_cell(p, rect=QRect(x + 1, y + 1, CELL_W - 2, CELL_H - 2),
                        ch=ch, val=val, snap=snap, selected=selected,
                        hovered=hovered, font=font_small)

    def set_display_mode(self, mode: str) -> None:
        """显示模式："value"=数值格（默认）|"heatmap"=512 通道热图|"delta"=Δ值。"""
        self._display_mode = mode if mode in ("value", "heatmap", "delta") else "value"
        self.update()

    # ── 坐标转换 ──────────────────────────────────

    def _pos_to_channel(self, pos: QPoint) -> int:
        """像素坐标 → 通道号（1-based），越界/折叠行返回0"""
        x = pos.x() - MARGIN
        row = self._row_at(pos.y())
        if x < 0 or row < 0 or row in self._collapsed:
            return 0
        col = x // CELL_W
        if col >= COLS:
            return 0
        ch = row * COLS + col + 1
        return ch if 1 <= ch <= 512 else 0

    def _get_drag_rect(self) -> QRect:
        return QRect(self._drag_start, self._drag_current).normalized()

    def _channels_in_rect(self, rect: QRect) -> set:
        """返回与矩形相交的通道集合（折叠行不可选——深查②#7）"""
        result = set()
        for row in range(ROWS):
            if row in self._collapsed:
                continue
            y = self._row_top(row)
            for col in range(COLS):
                idx = row * COLS + col
                x = MARGIN + col * CELL_W
                cell_rect = QRect(x + 1, y + 1, CELL_W - 2, self._row_h(row) - 2)
                if rect.intersects(cell_rect):
                    result.add(idx + 1)
        return result

    # ── 鼠标事件 ──────────────────────────────────

    def mousePressEvent(self, e: QMouseEvent):
        # 折叠行组头点击 = 展开（深查②#7）；展开态小标点击 = 折叠（#6）
        if self._consume_group_chip_click(e):
            return
        row = self._row_at(e.pos().y())
        if row >= 0 and row in self._collapsed:
            self.toggle_row(row)
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        ch = self._pos_to_channel(e.pos())
        mods = e.modifiers()
        if ch == 0:
            self._begin_drag(e.pos(), mods)
        else:
            self._click_channel(ch, mods)
        self.update()

    def _begin_drag(self, pos: QPoint, mods) -> None:
        """空白区域开始框选（Ctrl 追加到原有选择）。"""
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        self._dragging = True
        self._drag_start = pos
        self._drag_current = pos
        self._drag_sel_base = self._selected.copy() if ctrl else set()
        if not ctrl:
            self._selected.clear()

    def _click_channel(self, ch: int, mods) -> None:
        """点选/Shift 连续选/Ctrl 切换。"""
        if mods & Qt.KeyboardModifier.ShiftModifier and self._last_clicked:
            lo, hi = min(ch, self._last_clicked), max(ch, self._last_clicked)
            self._selected |= set(range(lo, hi + 1))
        elif mods & Qt.KeyboardModifier.ControlModifier:
            if ch in self._selected:
                self._selected.discard(ch)
            else:
                self._selected.add(ch)
        else:
            self._selected = {ch}
        self._last_clicked = ch
        self._cursor = ch          # 键盘游标跟随鼠标落点（深查②#12）
        self.selection_changed.emit(self._selected.copy())

    def mouseMoveEvent(self, e: QMouseEvent):
        ch = self._pos_to_channel(e.pos())
        self._hover_ch = ch

        if self._dragging:
            self._drag_current = e.pos()
            rect = self._get_drag_rect()
            in_rect = self._channels_in_rect(rect)
            self._selected = self._drag_sel_base | in_rect
            self.update()

    def mouseReleaseEvent(self, e: QMouseEvent):
        if self._dragging:
            self._dragging = False
            self.selection_changed.emit(self._selected.copy())
            self.update()

    def mouseDoubleClickEvent(self, e: QMouseEvent):
        """双击：选中整行（折叠行=展开，深查②#7）"""
        ch = self._pos_to_channel(e.pos())
        if ch <= 0:
            row = self._row_at(e.pos().y())
            if row >= 0 and row in self._collapsed:
                self.toggle_row(row)
            return
        row = (ch - 1) // COLS
        row_chs = set(range(row * COLS + 1, row * COLS + COLS + 1))
        self._selected |= row_chs
        self.selection_changed.emit(self._selected.copy())
        self.update()

    # ── 键盘事件 ──────────────────────────────────

    def keyPressEvent(self, e: QKeyEvent):
        # 深查②#12：Ctrl+A 全选 / Esc|Del 清空；方向键与 Home/End 走 keys Mixin
        if e.key() == Qt.Key.Key_A \
                and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._selected = set(range(1, 513))
            self._cursor = self._cursor or 1
            self.selection_changed.emit(self._selected.copy())
            self.update()
            return
        if e.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Delete):
            self.clear_selection()
            return
        if not self._handle_nav_key(e):
            super().keyPressEvent(e)

    # ── 外部接口 ──────────────────────────────────

    def set_selected_channels(self, channels: set):
        """从外部（小键盘）同步选择状态"""
        self._selected = channels.copy()
        self.update()

    def get_selected_channels(self) -> set:
        return self._selected.copy()

    def clear_selection(self):
        self._selected.clear()
        self.selection_changed.emit(set())
        self.update()
