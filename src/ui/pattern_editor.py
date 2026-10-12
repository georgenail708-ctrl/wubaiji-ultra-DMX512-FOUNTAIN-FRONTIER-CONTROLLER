"""
pattern_editor.py — 自定义矩阵图案编辑器
==========================================
提供 PatternEditorDialog：
  - 可视化网格编辑（点击切换 0/1）
  - 行列数调整（1-32 × 1-32）
  - 内置图案库浏览和加载
  - 保存到 EffectEngine 的用户图案库
  - 常用绘制工具：全选、全清、反转、随机、对角、棋盘
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QLabel, QSpinBox, QLineEdit, QScrollArea,
    QFrame, QComboBox, QMessageBox, QSizePolicy, QToolBar,
    QSplitter
)
from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QFont

from .effect_engine import PatternDef, get_engine, BUILTIN_PATTERNS
from .tabler_button import tabler_button
from .qascade_theme import (
    ACTIVE_ORANGE, BG_DEEP, BG_EDITOR, BG_INPUT, BG_ROOT, BORDER_NORMAL,
    FOCUS_BLUE, TEXT_PRIMARY, TEXT_SECONDARY,
)

# ─────────────────────────────────────────────────────────────────────────────
#  颜色常量（原海军蓝/绯红 v4 主题已收敛到全局 主题令牌）
# ─────────────────────────────────────────────────────────────────────────────
BG_MAIN     = BG_ROOT          # 对话框根底（原靛蓝夜底）
BG_PANEL    = BG_EDITOR        # 编辑区卡片基底
FG_MAIN     = TEXT_PRIMARY
FG_DIM      = TEXT_SECONDARY
ACCENT      = ACTIVE_ORANGE    # 玫红双职责拆分：按钮强调统一激活橙
CELL_ON     = ACTIVE_ORANGE    # 点亮格＝全局「激活」语义色
CELL_OFF    = BG_INPUT         # 熄灭格＝凹陷输入底
CELL_BORDER = BORDER_NORMAL
_ON_HOVER   = QColor(ACTIVE_ORANGE).lighter(120).name()   # 自绘 hover 派生（纯视觉）
_OFF_HOVER  = QColor(CELL_OFF).lighter(170).name()
_ACCENT_HOVER = QColor(ACTIVE_ORANGE).lighter(112).name()


# ─────────────────────────────────────────────────────────────────────────────
#  图案网格绘制 Widget
# ─────────────────────────────────────────────────────────────────────────────

class PatternGridWidget(QWidget):
    """可交互的矩阵图案网格，支持点击/拖拽绘制"""

    pattern_changed = pyqtSignal()

    CELL_SIZE = 28

    def __init__(self, rows: int = 4, cols: int = 4, parent=None):
        super().__init__(parent)
        self._rows = rows
        self._cols = cols
        self._cells: list[list[int]] = [[0]*cols for _ in range(rows)]
        self._hover_r = -1
        self._hover_c = -1
        self._drag_val = None   # 拖拽时统一设置的值（0 或 1）
        self.setMouseTracking(True)
        # §9 交互润色：绘制画布使用十字光标
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._update_size()

    def _update_size(self):
        w = self._cols * self.CELL_SIZE + 2
        h = self._rows * self.CELL_SIZE + 2
        self.setFixedSize(w, h)

    def set_pattern(self, pattern: PatternDef):
        self._rows = pattern.rows
        self._cols = pattern.cols
        self._cells = [row[:] for row in pattern.cells]
        self._update_size()
        self.update()

    def get_pattern_def(self, name: str = "Pattern") -> PatternDef:
        import uuid
        p = PatternDef(
            id=str(uuid.uuid4()),
            name=name,
            rows=self._rows,
            cols=self._cols,
        )
        p.cells = [row[:] for row in self._cells]
        return p

    def resize_grid(self, rows: int, cols: int):
        new_cells = [[0]*cols for _ in range(rows)]
        for r in range(min(rows, self._rows)):
            for c in range(min(cols, self._cols)):
                new_cells[r][c] = self._cells[r][c]
        self._rows = rows
        self._cols = cols
        self._cells = new_cells
        self._update_size()
        self.update()
        self.pattern_changed.emit()

    def fill_all(self, val: int):
        for r in range(self._rows):
            for c in range(self._cols):
                self._cells[r][c] = val
        self.update()
        self.pattern_changed.emit()

    def invert(self):
        for r in range(self._rows):
            for c in range(self._cols):
                self._cells[r][c] = 1 - self._cells[r][c]
        self.update()
        self.pattern_changed.emit()

    def checkerboard(self):
        for r in range(self._rows):
            for c in range(self._cols):
                self._cells[r][c] = (r + c) % 2
        self.update()
        self.pattern_changed.emit()

    def diagonal(self, direction: str = "down"):
        for r in range(self._rows):
            for c in range(self._cols):
                if direction == "down":
                    self._cells[r][c] = 1 if r == c else 0
                else:
                    self._cells[r][c] = 1 if r == (self._cols - 1 - c) else 0
        self.update()
        self.pattern_changed.emit()

    def random_fill(self, density: float = 0.5):
        import random
        for r in range(self._rows):
            for c in range(self._cols):
                self._cells[r][c] = 1 if random.random() < density else 0
        self.update()
        self.pattern_changed.emit()

    def border_only(self):
        for r in range(self._rows):
            for c in range(self._cols):
                is_border = (r == 0 or r == self._rows - 1 or
                             c == 0 or c == self._cols - 1)
                self._cells[r][c] = 1 if is_border else 0
        self.update()
        self.pattern_changed.emit()

    def _cell_at(self, x: int, y: int):
        r = y // self.CELL_SIZE
        c = x // self.CELL_SIZE
        if 0 <= r < self._rows and 0 <= c < self._cols:
            return r, c
        return -1, -1

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        cs = self.CELL_SIZE
        for r in range(self._rows):
            for c in range(self._cols):
                x = c * cs
                y = r * cs
                is_hover = (r == self._hover_r and c == self._hover_c)
                if self._cells[r][c]:
                    color = QColor(_ON_HOVER if is_hover else CELL_ON)
                else:
                    color = QColor(_OFF_HOVER if is_hover else CELL_OFF)
                p.fillRect(x + 1, y + 1, cs - 2, cs - 2, color)
                p.setPen(QPen(QColor(CELL_BORDER), 1))
                p.drawRect(x, y, cs - 1, cs - 1)

    def mousePressEvent(self, event):
        r, c = self._cell_at(event.pos().x(), event.pos().y())
        if r >= 0:
            self._drag_val = 1 - self._cells[r][c]
            self._cells[r][c] = self._drag_val
            self.update()
            self.pattern_changed.emit()

    def mouseMoveEvent(self, event):
        r, c = self._cell_at(event.pos().x(), event.pos().y())
        self._hover_r = r
        self._hover_c = c
        if self._drag_val is not None and r >= 0:
            self._cells[r][c] = self._drag_val
            self.update()
            self.pattern_changed.emit()
        else:
            self.update()

    def mouseReleaseEvent(self, event):
        self._drag_val = None

    def leaveEvent(self, event):
        self._hover_r = -1
        self._hover_c = -1
        self.update()


# ─────────────────────────────────────────────────────────────────────────────
#  图案编辑器对话框
# ─────────────────────────────────────────────────────────────────────────────

# _BTN_STYLE/_SPIN_STYLE/_COMBO_STYLE/_INPUT_STYLE 已删除：
# 普通按钮/SpinBox/ComboBox/LineEdit 外观由全局 GLOBAL_QSS 统一提供。
_ACCENT_BTN = (   # 局部特化：唯一主操作按钮，激活橙填充（全局按钮面为灰阶）
    f"QPushButton{{background:{ACCENT};color:{BG_DEEP};border:none;"
    f"border-radius:4px;padding:5px 16px;font-size:12px;font-weight:bold;}}"
    f"QPushButton:hover{{background:{_ACCENT_HOVER};}}"
)


class PatternEditorDialog(QDialog):
    """
    自定义矩阵图案编辑器对话框。
    用户可以：
    - 新建图案（命名 + 设置行列数）
    - 点击/拖拽绘制
    - 使用快捷工具（全选/全清/反转/棋盘/对角/随机）
    - 从内置图案库加载
    - 保存到用户图案库
    """

    pattern_saved = pyqtSignal(object)   # 发出 PatternDef

    def __init__(self, parent=None, initial_pattern: PatternDef = None):
        super().__init__(parent)
        self.setWindowTitle("图案编辑器")
        self.setMinimumSize(640, 460)
        # 局部特化：仅固定对话框底色，控件外观全部走全局主题
        self.setStyleSheet(f"QDialog{{background:{BG_MAIN};}}")
        self._build_ui()
        if initial_pattern:
            self._load_pattern(initial_pattern)

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # ── 左侧：内置图案库 ──────────────────────────────────────────────
        left = QVBoxLayout()
        left.setSpacing(6)

        lib_lbl = QLabel("内置图案库")
        lib_lbl.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
        left.addWidget(lib_lbl)

        self._lib_combo = QComboBox()
        self._lib_combo.setMinimumWidth(150)
        for pid, pat in BUILTIN_PATTERNS.items():
            self._lib_combo.addItem(pat.name, pid)
        left.addWidget(self._lib_combo)

        load_btn = QPushButton("加载到编辑器")
        load_btn.clicked.connect(self._load_from_lib)
        left.addWidget(load_btn)

        left.addSpacing(12)

        # 用户图案库
        user_lbl = QLabel("用户图案库")
        user_lbl.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
        left.addWidget(user_lbl)

        self._user_combo = QComboBox()
        self._user_combo.setMinimumWidth(150)
        left.addWidget(self._user_combo)
        self._refresh_user_list()

        load_user_btn = QPushButton("加载用户图案")
        load_user_btn.clicked.connect(self._load_from_user)
        left.addWidget(load_user_btn)

        del_user_btn = QPushButton("删除用户图案")
        del_user_btn.clicked.connect(self._delete_user_pattern)
        left.addWidget(del_user_btn)

        left.addStretch()
        root.addLayout(left)

        # ── 中间：编辑区 ─────────────────────────────────────────────────
        mid = QVBoxLayout()
        mid.setSpacing(8)

        # 名称 + 尺寸
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("名称:"))
        self._name_edit = QLineEdit("My Pattern")
        self._name_edit.setFixedWidth(140)
        top_row.addWidget(self._name_edit)

        top_row.addSpacing(12)
        top_row.addWidget(QLabel("行:"))
        self._rows_spin = QSpinBox()
        self._rows_spin.setRange(1, 32)
        self._rows_spin.setValue(4)
        self._rows_spin.setFixedWidth(55)
        top_row.addWidget(self._rows_spin)

        top_row.addWidget(QLabel("列:"))
        self._cols_spin = QSpinBox()
        self._cols_spin.setRange(1, 32)
        self._cols_spin.setValue(4)
        self._cols_spin.setFixedWidth(55)
        top_row.addWidget(self._cols_spin)

        resize_btn = QPushButton("应用尺寸")
        resize_btn.clicked.connect(self._apply_resize)
        top_row.addWidget(resize_btn)
        top_row.addStretch()
        mid.addLayout(top_row)

        # 工具栏
        tools_row = QHBoxLayout()
        tools_row.setSpacing(4)
        tool_defs = [
            ("全亮", lambda: self._grid.fill_all(1)),
            ("全清", lambda: self._grid.fill_all(0)),
            ("反转", lambda: self._grid.invert()),
            ("棋盘", lambda: self._grid.checkerboard()),
            ("对角↘", lambda: self._grid.diagonal("down")),
            ("对角↗", lambda: self._grid.diagonal("up")),
            ("边框", lambda: self._grid.border_only()),
            ("随机25%", lambda: self._grid.random_fill(0.25)),
            ("随机50%", lambda: self._grid.random_fill(0.50)),
            ("随机75%", lambda: self._grid.random_fill(0.75)),
        ]
        for label, fn in tool_defs:
            btn = QPushButton(label)
            btn.setFixedHeight(26)
            btn.clicked.connect(fn)
            tools_row.addWidget(btn)
        tools_row.addStretch()
        mid.addLayout(tools_row)

        # 网格编辑区（带滚动）— 局部特化：语义容器底 + 描边
        scroll = QScrollArea()
        scroll.setStyleSheet(f"QScrollArea{{background:{BG_PANEL};"
                             f"border:1px solid {BORDER_NORMAL};}}")
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)

        grid_container = QWidget()
        grid_container.setStyleSheet(f"background:{BG_PANEL};")
        grid_lay = QVBoxLayout(grid_container)
        grid_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._grid = PatternGridWidget(4, 4)
        grid_lay.addWidget(self._grid, alignment=Qt.AlignmentFlag.AlignCenter)
        scroll.setWidget(grid_container)
        mid.addWidget(scroll, 1)

        # 提示
        hint = QLabel("点击格子切换 亮/暗，拖拽批量绘制")
        hint.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        mid.addWidget(hint)

        root.addLayout(mid, 1)

        # ── 右侧：预览 + 保存 ─────────────────────────────────────────────
        right = QVBoxLayout()
        right.setSpacing(8)

        prev_lbl = QLabel("预览（当前图案）")
        prev_lbl.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
        right.addWidget(prev_lbl)

        self._preview = PatternPreviewWidget()
        self._preview.setFixedSize(160, 160)
        right.addWidget(self._preview)

        self._grid.pattern_changed.connect(self._on_pattern_changed)

        right.addSpacing(16)

        # 统计
        self._stats_lbl = QLabel("0 / 0 格亮")
        self._stats_lbl.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
        right.addWidget(self._stats_lbl)

        right.addStretch()

        # 保存按钮
        save_btn = tabler_button("保存图案", "device-floppy", BG_DEEP)
        save_btn.setStyleSheet(_ACCENT_BTN)
        save_btn.clicked.connect(self._save_pattern)
        right.addWidget(save_btn)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        right.addWidget(close_btn)

        root.addLayout(right)

        # 初始刷新
        self._on_pattern_changed()

    def _apply_resize(self):
        r = self._rows_spin.value()
        c = self._cols_spin.value()
        self._grid.resize_grid(r, c)

    def _on_pattern_changed(self):
        # 更新预览
        pd = self._grid.get_pattern_def(self._name_edit.text() or "Pattern")
        self._preview.set_pattern(pd)
        # 统计
        total = pd.rows * pd.cols
        on_count = sum(cell for row in pd.cells for cell in row)
        self._stats_lbl.setText(f"{on_count} / {total} 格亮 ({100*on_count//max(1,total)}%)")

    def _load_from_lib(self):
        pid = self._lib_combo.currentData()
        if pid and pid in BUILTIN_PATTERNS:
            self._load_pattern(BUILTIN_PATTERNS[pid])

    def _load_from_user(self):
        pid = self._user_combo.currentData()
        eng = get_engine()
        user_pats = eng.all_patterns()
        if pid and pid in user_pats:
            self._load_pattern(user_pats[pid])

    def _load_pattern(self, pat: PatternDef):
        self._name_edit.setText(pat.name)
        self._rows_spin.setValue(pat.rows)
        self._cols_spin.setValue(pat.cols)
        self._grid.set_pattern(pat)
        self._on_pattern_changed()

    def _save_pattern(self):
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "保存图案", "请输入图案名称。")
            return
        pd = self._grid.get_pattern_def(name)
        eng = get_engine()
        eng.add_user_pattern(pd)
        self._refresh_user_list()
        self.pattern_saved.emit(pd)
        QMessageBox.information(self, "保存图案", f"图案「{name}」已保存到用户图案库。")

    def _delete_user_pattern(self):
        pid = self._user_combo.currentData()
        if not pid:
            return
        eng = get_engine()
        eng.remove_user_pattern(pid)
        self._refresh_user_list()

    def _refresh_user_list(self):
        self._user_combo.clear()
        eng = get_engine()
        for pid, pat in eng._user_patterns.items():
            self._user_combo.addItem(pat.name, pid)


# ─────────────────────────────────────────────────────────────────────────────
#  小型预览 Widget
# ─────────────────────────────────────────────────────────────────────────────

class PatternPreviewWidget(QWidget):
    """只读的图案预览小控件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pattern: PatternDef = PatternDef()
        # 局部特化：预览画布容器底与描边
        self.setStyleSheet(f"background:{BG_PANEL};border:1px solid {BORDER_NORMAL};")

    def set_pattern(self, pat: PatternDef):
        self._pattern = pat
        self.update()

    def paintEvent(self, event):
        if not self._pattern or self._pattern.rows == 0 or self._pattern.cols == 0:
            return
        p = QPainter(self)
        w = self.width()
        h = self.height()
        cw = w / self._pattern.cols
        ch = h / self._pattern.rows
        for r in range(self._pattern.rows):
            for c in range(self._pattern.cols):
                # 跨格取整避免 int(cw)-2 造成的非整数像素接缝闪烁（audit 16.6）
                x0, x1 = int(c * cw), int((c + 1) * cw)
                y0, y1 = int(r * ch), int((r + 1) * ch)
                color = QColor(CELL_ON) if self._pattern.cells[r][c] else QColor(CELL_OFF)
                p.fillRect(x0 + 1, y0 + 1, max(1, x1 - x0 - 2),
                           max(1, y1 - y0 - 2), color)
