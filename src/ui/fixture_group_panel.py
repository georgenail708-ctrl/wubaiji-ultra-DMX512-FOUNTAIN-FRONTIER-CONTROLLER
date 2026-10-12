"""
DMXTimeline — Fixture Group Panel
参考 设备选择组 界面：
  - 顶部标签栏：All Fixtures / Group 1 / Group 2 / ...
  - 设备组按钮网格：每个组一个大按钮，显示组名和设备数
  - 支持创建、重命名、删除、复制设备组
  - 选中组后通过信号通知 Programmer 面板更新显示
  - 支持从 TopView 导入位置数据到组
"""
from __future__ import annotations
import uuid
import logging
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QPushButton, QLabel, QDialog, QDialogButtonBox,
    QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QAbstractItemView, QFrame, QSizePolicy, QGridLayout,
    QColorDialog, QMessageBox, QSplitter, QCheckBox,
    QGroupBox, QSpinBox,
)
from PyQt6.QtCore import Qt, QRectF, pyqtSignal, QSize, QPoint, QMimeData, QEvent
from PyQt6.QtGui import (QColor, QFont, QIcon, QDrag, QMouseEvent,
                         QPainter, QPen, QBrush, QPainterPath, QFontMetrics)

from .widget_kit import EmptyState, apply_dialog_chrome
from .qascade_theme import (
    BG_ROOT, BG_DEEP, BG_BOX, BG_EDITOR, BG_BUTTON,
    BG_PANEL, BORDER_NORMAL,
    SELECT_BLUE, FOCUS_BLUE, TEXT_PRIMARY, TEXT_SECONDARY,
    TEXT_SELECTED_FG,
    SEP_LINE, BRIGHT_SUCCESS, BRIGHT_INFO,
    BRAND_ORANGE_SELECTED, BRAND_ORANGE_INK,
)

log = logging.getLogger(__name__)

def _hex_alpha(hex_color: str, alpha_hex: str) -> str:
    """将 #RRGGBB + 2位十六进制 alpha 转换为 rgba(r,g,b,a)"""
    try:
        h = hex_color.lstrip('#')
        if len(h) != 6: return f"rgba(0,0,0,{int(alpha_hex, 16)/255.0:.2f})"
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        a = int(alpha_hex, 16) / 255.0
        return f"rgba({r},{g},{b},{a:.2f})"
    except Exception:
        return "rgba(0,0,0,0.5)"


class FixtureShortcutListWidget(QListWidget):
    """Shared fixture-list selection shortcuts."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selection_anchor_row = -1

    def _valid_anchor_row(self, fallback: int) -> int:
        if 0 <= self._selection_anchor_row < self.count():
            return self._selection_anchor_row
        current = self.currentRow()
        if 0 <= current < self.count():
            return current
        selected_rows = sorted(self.row(item) for item in self.selectedItems())
        if selected_rows:
            return selected_rows[-1]
        return max(0, min(int(fallback), self.count() - 1))

    def _select_range(self, start: int, end: int, keep_existing: bool):
        if not keep_existing:
            self.clearSelection()
        lo, hi = sorted((start, end))
        for i in range(lo, hi + 1):
            it = self.item(i)
            if it is not None:
                it.setSelected(True)

    def mousePressEvent(self, ev: QMouseEvent):
        if ev.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(ev)

        item = self.itemAt(ev.position().toPoint())
        if item is None:
            if not (ev.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self.clearSelection()
                self._selection_anchor_row = -1
            ev.accept()
            return

        row = self.row(item)
        mods = ev.modifiers()
        block = self.blockSignals(True)
        try:
            if mods & Qt.KeyboardModifier.ControlModifier and mods & Qt.KeyboardModifier.ShiftModifier:
                anchor = self._valid_anchor_row(row)
                self._select_range(anchor, row, keep_existing=True)
                self.setCurrentRow(row)
            elif mods & Qt.KeyboardModifier.ControlModifier:
                item.setSelected(not item.isSelected())
                self.setCurrentRow(row)
                self._selection_anchor_row = row
            elif mods & Qt.KeyboardModifier.ShiftModifier:
                anchor = self._valid_anchor_row(row)
                self._select_range(anchor, row, keep_existing=False)
                self.setCurrentRow(row)
            else:
                self.clearSelection()
                item.setSelected(True)
                self.setCurrentRow(row)
                self._selection_anchor_row = row
        finally:
            self.blockSignals(block)

        self.itemSelectionChanged.emit()
        ev.accept()

# ─── 数据模型 ──────────────────────────────────────────────────────────────────

@dataclass
class FixtureGroup:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Group 1"
    color: str = "#2A4A7A"
    fixture_ids: List[str] = field(default_factory=list)
    base_order_fixture_ids: List[str] = field(default_factory=list)
    route_order_sheets: List[dict] = field(default_factory=list)

    def to_dict(self):
        base_order = list(self.base_order_fixture_ids or [])
        if not base_order:
            base_order = list(self.fixture_ids)
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "fixture_ids": list(self.fixture_ids),
            "base_order_fixture_ids": base_order,
            "route_order_sheets": list(self.route_order_sheets or []),
        }

    @classmethod
    def from_dict(cls, d):
        fixture_ids = list(d.get("fixture_ids", []))
        base_order = list(d.get("base_order_fixture_ids", [])) or list(fixture_ids)
        return cls(
            id=d.get("id", str(uuid.uuid4())),
            name=d.get("name", "Group"),
            color=d.get("color", "#2A4A7A"),
            fixture_ids=fixture_ids,
            base_order_fixture_ids=base_order,
            route_order_sheets=list(d.get("route_order_sheets", []) or []),
        )


# ─── 组按钮 ──────────────────────────────────────────────────────────────────

class GroupButton(QPushButton):
    """单个设备组按钮（§10 任务 B：卡片解剖，QPainter 直绘）。

    - 卡片底 BG_EDITOR + 1px rgba(255,255,255,17) 边 + 4px 圆角
    - 组色（用户工程数据）只落在左侧 3px 竖条 + 组名前 9px 圆点
    - 选中 = select_blue 底 + 白字 + 竖条提亮；hover = bg_panel
    - 零 setStyleSheet：状态变化只改标志并 update()，重建网格零样式解析
    """

    def __init__(self, group: FixtureGroup, fixture_count: int = 0, parent=None):
        super().__init__(parent)
        self.group = group
        self._selected = False
        self._fixture_count = fixture_count
        self._press_pos = QPoint()
        self._hover = False
        # §11 病灶#7：高度固定 64、宽度弹性（min GROUP_BTN_MIN_W）。
        # 网格列数随面板可用宽自适应，窄侧栏（最窄 150）单列即可完整显示，
        # 名称在 paintEvent 内 elide，永不溢出卡片。
        self.setFixedHeight(64)
        self.setMinimumWidth(GROUP_BTN_MIN_W)
        _sp = self.sizePolicy()
        _sp.setHorizontalPolicy(QSizePolicy.Policy.Expanding)
        _sp.setVerticalPolicy(QSizePolicy.Policy.Fixed)
        self.setSizePolicy(_sp)
        self.setCheckable(True)
        # §9 交互润色：可点击元素手型光标
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._update_style()

    def set_selected(self, v: bool):
        self._selected = bool(v)
        self.setChecked(self._selected)
        self._update_style()

    def set_fixture_count(self, n: int):
        if self._fixture_count != n:
            self._fixture_count = n
            self._update_style()

    def _update_style(self):
        """只更新 tooltip 文本与重绘标志；外观全部在 paintEvent 直绘。"""
        # §9：完整组名入 tooltip（长名被按钮宽度截断时可悬停查看）
        self.setToolTip(f"{self.group.name} · {self._fixture_count} fixtures")
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self._update_style()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self._update_style()
        super().leaveEvent(e)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, hgt = self.width(), self.height()
        r = QRectF(0.5, 0.5, w - 1, hgt - 1)
        checked = self.isChecked() or self._selected
        gc = QColor(self.group.color)
        if not gc.isValid():
            gc = QColor("#8A8A8A")
        if checked:
            bg = QColor(BRAND_ORANGE_SELECTED)
            border_c = QColor(BRAND_ORANGE_INK)
            fg_name = QColor(TEXT_PRIMARY)
            fg_cnt = QColor(TEXT_SECONDARY)
            bar_col = QColor(gc.lighter(140))
        else:
            # §11.2 三态补全：hover=bg_panel、pressed=BG_BOX 沉底，均直绘零样式表
            if self.isDown():
                bg = QColor(BG_BOX)
                border_c = QColor(BRAND_ORANGE_INK)
            else:
                bg = QColor(BG_PANEL) if self._hover else QColor(BG_EDITOR)
                border_c = QColor(BRAND_ORANGE_INK if self._hover else BORDER_NORMAL)
            fg_name = QColor(TEXT_PRIMARY)
            fg_cnt = QColor(TEXT_SECONDARY)
            bar_col = QColor(gc)
        p.setPen(QPen(border_c, 1))
        p.setBrush(QBrush(bg))
        p.drawRoundedRect(r, 4, 4)
        # 左侧 3px 组色竖条（组色唯一大面积落点）
        p.save()
        path = QPainterPath()
        path.addRoundedRect(r, 4, 4)
        p.setClipPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(bar_col))
        p.drawRect(QRectF(r.left(), r.top(), 3, r.height()))
        p.restore()
        # 组名前 9px 圆点 + 组名 + 设备数
        dot_r = QRectF(13, 12, 9, 9)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(gc))
        p.drawEllipse(dot_r)
        f = QFont()
        f.setFamilies(["Inter", "Segoe UI", "Microsoft YaHei UI"])
        f.setPixelSize(12)
        f.setBold(True)
        p.setFont(f)
        fm = QFontMetrics(f)
        name = fm.elidedText(self.group.name, Qt.TextElideMode.ElideRight,
                             w - 46)
        p.setPen(QPen(fg_name))
        p.drawText(QRectF(28, 0, w - 34, 33),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   name)
        f2 = QFont(f)
        f2.setBold(False)
        f2.setPixelSize(11)
        p.setFont(f2)
        p.setPen(QPen(fg_cnt))
        p.drawText(QRectF(14, 32, w - 20, hgt - 38),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   f"{self._fixture_count} fixtures")
        p.end()


# ─── 设备选择对话框 ──────────────────────────────────────────────────────────

GROUP_BTN_MIN_W = 110


class _GroupGridPage(QWidget):
    """设备组按钮网格容器（§11 病灶#7：排版自适应）。

    - GroupButton 高度固定、宽度弹性：窄侧栏（最窄 150）时退化为单列，
      容器 minimumSizeHint = 按钮 min + margins ≤ 148，不再触发
      SQUEEZE/HSCROLL；可用宽增加时按步长恢复多列（侧栏上限 300 → 两列）。
    - 重排只搬运既有按钮（takeAt/addWidget），不销毁重建，
      不触碰任何信号连接与数据。
    """
    MARGIN_X = 2   # 左右布局边距
    GAP_X = 6      # 列间距

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cols = 1
        lay = QGridLayout(self)
        lay.setSpacing(self.GAP_X)
        lay.setContentsMargins(self.MARGIN_X, 2, self.MARGIN_X, 2)
        self._lay = lay

    def grid_layout(self) -> QGridLayout:
        return self._lay

    def resizeEvent(self, ev):
        try:
            avail = self.width() - self.MARGIN_X * 2
            step = GROUP_BTN_MIN_W + self.GAP_X
            cols = max(1, (avail + self.GAP_X) // step) if avail > 0 else 1
            self._set_columns(int(cols))
        except Exception:
            pass
        super().resizeEvent(ev)

    def _set_columns(self, cols: int):
        cols = max(1, int(cols))
        if cols == self._cols:
            return
        self._cols = cols
        btns = []
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                btns.append(w)
        for i, b in enumerate(btns):
            row, col = divmod(i, cols)
            self._lay.addWidget(b, row, col)


class FixtureSelectDialog(QDialog):
    """从所有设备中选择要加入组的设备"""
    selection_changed = pyqtSignal(list)

    def __init__(self, all_fixtures, selected_ids: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择设备")
        self.setMinimumSize(340, 380)
        # 对话框/标签/输入框/按钮外观由全局 GLOBAL_QSS 提供（原行内样式已删）
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)   # §10.4 内容边距
        lay.setSpacing(8)

        # 搜索框（22px 行高对齐 §10 任务 A 排版）
        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        search_row.addWidget(QLabel("搜索:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("输入设备名或 ID…")
        self._search.textChanged.connect(self._filter)
        search_row.addWidget(self._search)
        lay.addLayout(search_row)

        # 全选/全不选
        ctrl_row = QHBoxLayout()
        sa = QPushButton("全选")
        sa.setFixedHeight(24)
        sa.setToolTip("选中当前列表中的所有设备")
        sa.clicked.connect(self._select_all)
        sn = QPushButton("全不选")
        sn.setFixedHeight(24)
        sn.setToolTip("清空当前列表的设备选择")
        sn.clicked.connect(self._select_none)
        ctrl_row.addWidget(sa); ctrl_row.addWidget(sn); ctrl_row.addStretch()
        lay.addLayout(ctrl_row)

        # 设备列表（选中态＝select_blue 底白字，由全局 QSS 统一；行高 24px）
        self._list = FixtureShortcutListWidget()
        self._list.setStyleSheet("QListWidget::item{min-height:24px;}")
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._all_fixtures = list(all_fixtures)
        self._selected_ids = set(selected_ids)
        self._updating_list = False
        self._populate(self._all_fixtures)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        lay.addWidget(self._list)

        # 按钮（§10.4：确定=primary / 取消=cancel 语义标记 + 标准浮窗外框）
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setProperty("role", "cancel")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        apply_dialog_chrome(self)

    def _populate(self, fixtures):
        self._updating_list = True
        self._list.blockSignals(True)
        self._list.clear()
        for fi in fixtures:
            item = QListWidgetItem(f"[{getattr(fi, 'id', '?')[:6]}]  {fi.name}")
            item.setData(Qt.ItemDataRole.UserRole, fi.id)
            if fi.id in self._selected_ids:
                item.setSelected(True)
            self._list.addItem(item)
        self._list.blockSignals(False)
        self._updating_list = False

    def _filter(self, text):
        text = text.lower()
        filtered = [f for f in self._all_fixtures
                    if text in f.name.lower() or text in f.id.lower()]
        self._populate(filtered)

    def _select_all(self):
        for i in range(self._list.count()):
            self._list.item(i).setSelected(True)

    def _select_none(self):
        for i in range(self._list.count()):
            self._list.item(i).setSelected(False)

    def get_selected_ids(self) -> List[str]:
        return [self._list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self._list.count())
                if self._list.item(i).isSelected()]

    def _on_selection_changed(self):
        if self._updating_list:
            return
        self._selected_ids = set(str(fid) for fid in self.get_selected_ids())
        self.selection_changed.emit(list(self._selected_ids))


# ─── 主面板 ──────────────────────────────────────────────────────────────────

class FixtureGroupPanel(QWidget):
    """
    设备选择组面板（参考 设备选择组）
    发出信号：
      group_selected(group_id, fixture_ids)  — 选中某组，通知 Programmer 更新
      all_selected(fixture_ids)              — 选中"All Fixtures"
    """
    group_selected = pyqtSignal(str, list, str)   # group_id, [fixture_id, ...], group_name
    all_selected   = pyqtSignal(list)             # [fixture_id, ...]
    fixture_selection_changed = pyqtSignal(list)  # live row/group fixture selection

    # 预设颜色
    GROUP_COLORS = [
        "#1A4A7A", "#1A5A3A", "#5A1A1A", "#4A3A1A",
        "#3A1A5A", "#1A4A4A", "#5A3A1A", "#2A2A5A",
    ]

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self._pm = project
        self._groups: List[FixtureGroup] = []
        self._sel_group_id = None
        self._color_idx = 0          # ★ 修复：循环颜色索引，_on_new_group 需要
        self._all_fixtures = []      # ★ 修复：设备列表缓存，_build_ui 前初始化
        self._updating_fixture_list = False
        self._build_ui()
        # 初始化时先加载组数据，再刷新设备
        self.refresh()
        # 监听工程变化（Patch 面板添加/删除设备后自动刷新）
        self._pm.project_changed.connect(self.refresh)

    def _save_to_project(self):
        """将组数据同步回 project 对象，并通知依赖面板刷新设备组。"""
        data = [g.to_dict() for g in self._groups]
        try:
            if hasattr(self._pm, "fixture_groups"):
                self._pm.fixture_groups = data
            elif hasattr(self._pm, "_project"):
                self._pm._project.fixture_groups = data
                if hasattr(self._pm, "_mark_dirty"):
                    self._pm._mark_dirty()
                elif hasattr(self._pm, "set_dirty"):
                    self._pm.set_dirty()
                if hasattr(self._pm, "project_changed"):
                    self._pm.project_changed.emit()
        except Exception:
            log.exception("保存设备组到项目失败")

    def _snapshot_for_undo(self):
        # ★★ 2026-09-20 修复（与 patch_panel 同款问题）：原实现的
        #   `from ...core.project_manager import DOMAIN_PATCH` 有两处错 ——
        #   ① 相对层级越界（本模块是 `src.ui.*`，`..` 才到 `src`）→ ImportError；
        #   ② 导入位置在别的函数末尾，而使用点在本方法 → 即使路径对也 NameError。
        #   两者都会让"编辑设备组时的撤销快照"整条路径失败。
        from ..core.project_manager import DOMAIN_PATCH
        if self._pm is not None and hasattr(self._pm, "_snapshot"):
            try:
                self._pm._snapshot(domain=DOMAIN_PATCH)
            except Exception:
                pass

    def get_groups(self) -> List[FixtureGroup]:
        """返回当前设备组对象列表，供 Programmer/Matrix 等面板同步。"""
        return list(self._groups)

    # ── 构建 UI ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        # 仅保留面板基底色（局部特化）；滚动条/控件外观由全局 GLOBAL_QSS 提供
        self.setStyleSheet(f"QWidget{{background:{BG_ROOT};color:{TEXT_PRIMARY};}}")
        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        # ── 顶部工具栏 ──
        toolbar = QWidget()
        toolbar.setFixedHeight(42)
        toolbar.setStyleSheet(
            f"background:{BG_DEEP}; border-bottom:1px solid {SEP_LINE};")
        tb_lay = QHBoxLayout(toolbar)
        tb_lay.setContentsMargins(8, 4, 8, 4)
        tb_lay.setSpacing(6)

        title = QLabel("Fixture Groups")
        title.setStyleSheet(
            f"color:{TEXT_SECONDARY}; font-size:12px; font-weight:bold;")
        tb_lay.addWidget(title)
        tb_lay.addStretch()

        new_btn = self._mk_tool_btn("New Group", TEXT_PRIMARY)
        try:
            from .main_icon_library import load_tabler_icon
            _ic = load_tabler_icon("plus", 14, TEXT_PRIMARY)
            if not _ic.isNull():
                new_btn.setIcon(_ic)
        except Exception:
            pass
        new_btn.setToolTip("创建新设备组")
        new_btn.clicked.connect(self._on_new_group)
        tb_lay.addWidget(new_btn)

        main_lay.addWidget(toolbar)

        # ── 主体：左侧组列表 + 右侧设备列表 ──
        splitter = QSplitter(Qt.Orientation.Horizontal)
        # 原 QSplitter::handle 行内样式已删：分隔条由全局 GLOBAL_QSS 统一提供
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(5)

        # 左侧：组按钮网格
        left = QWidget()
        left.setMinimumWidth(150)
        left.setMaximumWidth(300)
        left.setStyleSheet(f"background:{BG_ROOT};")
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(4, 4, 4, 4)
        left_lay.setSpacing(4)

        # "All Fixtures" 特殊按钮（局部特化：导航主按钮，选中＝select_blue 底白字）
        self._all_btn = QPushButton("All Fixtures")
        self._all_btn.setFixedHeight(42)
        self._all_btn.setCheckable(True)
        self._all_btn.setChecked(True)
        self._all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._all_btn.setToolTip("显示工程中的全部已配接设备")
        self._all_btn.setStyleSheet(f"""
            QPushButton {{
                background:{BG_BOX}; border:1px solid {BORDER_NORMAL};
                border-radius:4px; color:{FOCUS_BLUE};
                font-size:12px; font-weight:bold;
                text-align:left; padding-left:12px;
            }}
            QPushButton:checked {{
                background:{SELECT_BLUE}; border:1px solid {FOCUS_BLUE};
                color:{TEXT_SELECTED_FG};
            }}
            QPushButton:hover {{ background:{BG_PANEL}; }}
        """)
        self._all_btn.clicked.connect(self._on_all_clicked)
        left_lay.addWidget(self._all_btn)

        # 组按钮滚动区（外观由全局 QSS 提供，原滚动条行内样式已删）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        scroll.setStyleSheet(f"QScrollArea {{ border:none; background:{BG_ROOT}; }}")
        # §11 病灶#7：自适应列数网格容器（窄列 1 列、宽处两列，minHint ≤148）
        self._grid_container = _GroupGridPage()
        self._grid_container.setStyleSheet(f"background:{BG_ROOT};")
        self._grid_lay = self._grid_container.grid_layout()
        scroll.setWidget(self._grid_container)
        left_lay.addWidget(scroll)
        self._scroll = scroll

        # 空态（§10.6）：尚无设备组时替换网格占位
        self._groups_empty = EmptyState(
            "尚无设备组", "点击右上角「＋ New Group」创建第一个设备组",
            icon_key="folder-plus")
        self._groups_empty.setVisible(False)
        left_lay.addWidget(self._groups_empty)

        splitter.addWidget(left)

        # 右侧：该组的设备列表
        right = QWidget()
        right.setStyleSheet(f"background:{BG_ROOT};")
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(4, 4, 4, 4)
        right_lay.setSpacing(4)

        # 右侧标题栏
        rh = QHBoxLayout()
        self._group_title = QLabel("All Fixtures")
        self._group_title.setStyleSheet(
            f"color:{FOCUS_BLUE}; font-size:12px; font-weight:bold;")
        rh.addWidget(self._group_title)
        rh.addStretch()

        self._edit_btn = self._mk_tool_btn("编辑成员")
        self._edit_btn.setToolTip("编辑该组的设备成员")
        self._edit_btn.clicked.connect(self._on_edit_members)
        rh.addWidget(self._edit_btn)
        right_lay.addLayout(rh)
        self._right_lay = right_lay   # 供空态切换插入/移除占位

        # 设备列表（选中态＝select_blue 底白字，滚动条/边框由全局 QSS 提供；
        # 行高 24px 解剖——§10 任务 B）
        self._fixture_list = FixtureShortcutListWidget()
        self._fixture_list.setStyleSheet("QListWidget::item{min-height:24px;}")
        self._fixture_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._fixture_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self._fixture_list.itemSelectionChanged.connect(
            self._on_fixture_list_selection_changed)
        right_lay.addWidget(self._fixture_list)

        # 空态（§10.6）：右侧成员列表为空时由 _set_members_empty 动态重建占位
        self._members_empty = None

        # 底部信息栏（原 #556677 对比度过低，提升为次级文字色）
        self._info_lbl = QLabel("0 fixtures")
        self._info_lbl.setStyleSheet(
            f"color:{TEXT_SECONDARY}; font-size:10px; padding:2px 4px;")
        right_lay.addWidget(self._info_lbl)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([170, 360])
        main_lay.addWidget(splitter, stretch=1)

    # ── 工具按钮 ─────────────────────────────────────────────────────────────

    def _mk_tool_btn(self, text, fg=BRIGHT_INFO):
        """工具按钮：保留文字色语义（新增组=绿 / 编辑成员=蓝），
        面色与描边由主题派生；24px 高（§10 任务 B 组操作按钮统一）；
        透明度一律走 rgba()（_hex_alpha），禁 8 位 hex。"""
        btn = QPushButton(text)
        btn.setFixedHeight(24)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background:{BG_BUTTON}; color:{fg};
                border:1px solid {_hex_alpha(fg, "44")};
                border-radius:4px; padding:0 10px; font-size:11px;
            }}
            QPushButton:hover {{ background:{_hex_alpha(fg, "33")}; border-color:{_hex_alpha(fg, "88")}; }}
        """)
        return btn

    # ── 空态切换（纯呈现，不改数据流）───────────────────────────────────────
    def _set_members_empty(self, shown_count: int, total_count: int,
                           group_name: str | None = None):
        """右侧成员列表为空时显示对应空态文案并隐藏列表。

        空态文案依赖当前视图（无设备/空组），故显示时按需重建轻量
        EmptyState（3 个 QLabel 级别，切换频率低）；否则原样隐藏。
        """
        lay = getattr(self, "_right_lay", None)
        if lay is None:
            return
        show = (total_count == 0) or (shown_count == 0)
        empty = getattr(self, "_members_empty", None)
        if show:
            if total_count == 0:
                text, hint = "工程中还没有设备实例", "先在「配接」面板完成设备配接"
            elif group_name is not None:
                text, hint = (f"组「{group_name}」还没有设备成员",
                              "点击右上角「编辑成员」勾选设备加入")
            else:
                text, hint = "该视图没有设备成员", ""
            if empty is not None:
                idx = lay.indexOf(empty)
                lay.removeWidget(empty)
                empty.deleteLater()
            else:
                idx = max(0, lay.indexOf(self._info_lbl))
            self._members_empty = EmptyState(text, hint, icon_key="user-plus")
            lay.insertWidget(max(0, idx), self._members_empty)
        self._fixture_list.setVisible(not show)
        if getattr(self, "_members_empty", None) is not None:
            self._members_empty.setVisible(show)

    # ── 数据刷新 ─────────────────────────────────────────────────────────────

    def _refresh_fixtures(self):
        try:
            self._all_fixtures = list(self._pm.fixtures)
        except Exception:
            self._all_fixtures = []
        self._rebuild_grid()
        # ★★ 2026-09-17 深查修正（选中组被刷新顶掉）：原实现无条件 `_show_all`，
        #   而本面板的 `refresh()` 挂在 `pm.project_changed` 上、`_save_to_project()`
        #   又会触发该信号 —— 于是**用户自己的重命名/改色/复制**（以及任何 Patch
        #   改动）都会把右列表从「选中的组」悄悄切成「全部设备」，同时组瓦片仍
        #   高亮、`get_selected_fixture_ids()` 仍返回该组成员 → 显示与选择源不一致
        #   （用户看到"改个名字，列表就跳回全部设备了"）。已选组时保持组视图。
        if self._sel_group_id is None:
            self._show_all(emit_live=False)
        else:
            self._reshow_selected_group(emit_live=False)

    def _reshow_selected_group(self, emit_live: bool = False) -> None:
        """按当前 `_sel_group_id` 重新显示该组（找不到则回落全部设备）。"""
        grp = next((g for g in self._groups if g.id == self._sel_group_id), None)
        if grp is None:
            self._sel_group_id = None
            self._show_all(emit_live=emit_live)
            return
        self._show_group(grp, emit_live=emit_live)

    def refresh(self):
        # 核心修复：每次刷新时确保从项目重新加载最新的设备列表和组列表
        if hasattr(self._pm, "_project") and hasattr(self._pm._project, "fixture_groups"):
            new_groups = []
            for gd in self._pm._project.fixture_groups:
                try:
                    new_groups.append(FixtureGroup.from_dict(gd))
                except Exception:
                    pass
            self._groups = new_groups
        self._refresh_fixtures()

    # ── 组网格重建 ───────────────────────────────────────────────────────────

    def _rebuild_grid(self):
        # 清除旧按钮
        while self._grid_lay.count():
            item = self._grid_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # 有效设备 id 集合一次性构建，避免每个组每个 fid 都线性扫描全部设备
        # (原 O(组×组内设备×全部设备) 在 1356 设备下达数百万次比较,是全选卡顿主因)。
        all_ids = {f.id for f in self._all_fixtures}
        for idx, group in enumerate(self._groups):
            cnt = sum(1 for fid in group.fixture_ids if fid in all_ids)
            btn = GroupButton(group, cnt)
            btn.set_selected(group.id == self._sel_group_id)
            btn.clicked.connect(
                lambda checked, g=group: self._on_group_clicked(g))
            btn.customContextMenuRequested.connect(
                lambda pos, g=group, b=btn: self._ctx_menu(g, b.mapToGlobal(pos)))
            # 列数由容器按可用宽度自适应（§11 病灶#7），此处只管追加顺序
            self._grid_lay.addWidget(btn)
        # 空态切换（§10.6）：有组显示网格滚动区，无组显示占位
        has_groups = len(self._groups) > 0
        self._scroll.setVisible(has_groups)
        if getattr(self, "_groups_empty", None) is not None:
            self._groups_empty.setVisible(not has_groups)

    # ── 事件处理 ─────────────────────────────────────────────────────────────

    def _on_all_clicked(self):
        # 已处于"全部"视图且设备集未变 → 跳过昂贵的网格+1356项列表重建(仍重发实时选择,
        # 保证下游拿到选择;下游各自有去重守卫)。避免连点/重复触发时每次全量重建。
        fx_sig = (id(self._all_fixtures), len(self._all_fixtures))
        if (self._sel_group_id is None
                and getattr(self, "_all_view_fx_sig", None) == fx_sig):
            self._show_all()
            return
        self._sel_group_id = None
        self._all_btn.setChecked(True)
        self._rebuild_grid()
        self._show_all()
        self._all_view_fx_sig = fx_sig

    def _show_all(self, emit_live: bool = True):
        self._group_title.setText("All Fixtures")
        fids = [f.id for f in self._all_fixtures]
        # 列表内容只取决于设备集;未变则跳过 1356 项重建(仍走下方 emit 保证下游同步)。
        list_sig = (id(self._all_fixtures), len(self._all_fixtures))
        if getattr(self, "_all_list_sig", None) != list_sig:
            self._updating_fixture_list = True
            self._fixture_list.blockSignals(True)
            self._fixture_list.setUpdatesEnabled(False)   # 抑制 1356 次 addItem 的中间重绘
            self._fixture_list.clear()
            for fi in self._all_fixtures:
                item = QListWidgetItem(
                    f"  [{fi.id[:6]}]  {fi.name}  "
                    f"(U{getattr(fi, 'universe', 1)}  Ch{getattr(fi, 'start_channel', 1)})")
                item.setData(Qt.ItemDataRole.UserRole, fi.id)
                self._fixture_list.addItem(item)
            self._fixture_list.setUpdatesEnabled(True)
            self._fixture_list.blockSignals(False)
            self._updating_fixture_list = False
            self._all_list_sig = list_sig
        self._info_lbl.setText(f"{len(self._all_fixtures)} fixtures")
        self._set_members_empty(len(self._all_fixtures), len(self._all_fixtures))
        if emit_live:
            self.fixture_selection_changed.emit(fids)
            self.all_selected.emit(fids)
            # 同时发出 group_selected 以兼容 Programmer
            self.group_selected.emit("__all__", fids, "全部设备")

    def _on_group_clicked(self, group: FixtureGroup):
        self._sel_group_id = group.id
        self._all_btn.setChecked(False)
        self._rebuild_grid()
        self._show_group(group)

    def get_groups(self) -> List[FixtureGroup]:
        """返回当前所有设备组"""
        return list(self._groups)

    def _show_group(self, group: FixtureGroup, emit_live: bool = True):
        self._group_title.setText(f"  {group.name}")
        self._updating_fixture_list = True
        self._fixture_list.blockSignals(True)
        self._fixture_list.clear()
        ordered_ids = self._ordered_group_fixture_ids(group)
        by_id = {fi.id: fi for fi in self._all_fixtures}
        shown = []
        for fid in ordered_ids:
            fi = by_id.get(fid)
            if fi is None:
                continue
            item = QListWidgetItem(
                f"  [{fi.id[:6]}]  {fi.name}  "
                f"(U{getattr(fi, 'universe', 1)}  Ch{getattr(fi, 'start_channel', 1)})")
            item.setData(Qt.ItemDataRole.UserRole, fi.id)
            self._fixture_list.addItem(item)
            shown.append(fi.id)
        self._fixture_list.blockSignals(False)
        self._updating_fixture_list = False
        self._info_lbl.setText(f"{len(shown)} / {len(self._all_fixtures)} fixtures")
        self._set_members_empty(len(shown), len(self._all_fixtures), group.name)
        if emit_live:
            self.fixture_selection_changed.emit(shown)
            # 修复信号签名，增加 group_name
            self.group_selected.emit(group.id, shown, group.name)

    def _on_fixture_list_selection_changed(self):
        if self._updating_fixture_list:
            return
        self.fixture_selection_changed.emit(self.get_live_selected_fixture_ids())

    def _on_new_group(self):
        dlg = _GroupEditDialog(self._all_fixtures, [], parent=self)
        dlg.setWindowTitle("新建设备组")
        dlg.selection_changed.connect(self.fixture_selection_changed.emit)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        name, color, fids = dlg.get_result()
        if not name:
            name = f"Group {len(self._groups)+1}"
        color = color or self.GROUP_COLORS[
            self._color_idx % len(self.GROUP_COLORS)]
        self._color_idx += 1
        group = FixtureGroup(
            name=name,
            color=color,
            fixture_ids=fids,
            base_order_fixture_ids=list(fids),
        )
        self._snapshot_for_undo()
        self._groups.append(group)
        self._save_to_project()
        self._rebuild_grid()
        # 自动选中新组
        self._on_group_clicked(group)

    def _on_edit_members(self):
        if self._sel_group_id is None:
            return
        group = self._get_group(self._sel_group_id)
        if group is None:
            return
        dlg = _GroupEditDialog(
            self._all_fixtures, group.fixture_ids,
            name=group.name, color=group.color, parent=self)
        dlg.setWindowTitle(f"编辑组：{group.name}")
        dlg.selection_changed.connect(self.fixture_selection_changed.emit)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name, color, fids = dlg.get_result()
            self._snapshot_for_undo()
            group.name = name
            group.color = color
            group.fixture_ids = fids
            prev_order = list(getattr(group, "base_order_fixture_ids", []) or [])
            keep = [fid for fid in prev_order if fid in set(fids)]
            append_new = [fid for fid in fids if fid not in set(keep)]
            group.base_order_fixture_ids = keep + append_new
            allowed = set(fids)
            route_sheets = []
            for raw in getattr(group, "route_order_sheets", []) or []:
                if not isinstance(raw, dict):
                    continue
                sheet = dict(raw)
                src = [fid for fid in sheet.get("source_ids", fids) if fid in allowed]
                if not src:
                    src = list(fids)
                order = [fid for fid in sheet.get("ordered_ids", src) if fid in allowed]
                order.extend([fid for fid in src if fid not in set(order)])
                sheet["source_ids"] = src
                sheet["ordered_ids"] = order
                route_sheets.append(sheet)
            group.route_order_sheets = route_sheets
            self._save_to_project()
            self._rebuild_grid()
            self._on_group_clicked(group)

    def _ctx_menu(self, group: FixtureGroup, pos):
        menu = QMenu(self)   # 右键菜单直接用全局样式（原行内菜单 QSS 已删）
        ea = menu.addAction("编辑成员…")
        ra = menu.addAction("重命名…")
        ca = menu.addAction("更改颜色…")
        da = menu.addAction("复制组")
        menu.addSeparator()
        dela = menu.addAction("删除")
        act = menu.exec(pos)
        if act == ea:
            self._sel_group_id = group.id
            self._on_edit_members()
        elif act == ra:
            self._rename_group(group)
        elif act == ca:
            self._change_color(group)
        elif act == da:
            self._duplicate_group(group)
        elif act == dela:
            self._delete_group(group)

    def _rename_group(self, group: FixtureGroup):
        dlg = QDialog(self)
        dlg.setWindowTitle("重命名组")
        dlg.setMinimumSize(300, 100)
        dlg.resize(340, 120)
        # §10.4 对话框解剖：标准浮窗外框 + 内容边距 12 + 确定/取消语义
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        edit = QLineEdit(group.name)
        lay.addWidget(edit)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setProperty("role", "cancel")
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)
        apply_dialog_chrome(dlg)
        # §9 交互润色：打开即聚焦并全选，长名单免重新定位光标（audit 3.7）
        edit.setFocus()
        edit.selectAll()
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._snapshot_for_undo()
            group.name = edit.text().strip() or group.name
            self._save_to_project()
            self._rebuild_grid()

    def _change_color(self, group: FixtureGroup):
        color = QColorDialog.getColor(QColor(group.color), self)
        if color.isValid():
            self._snapshot_for_undo()
            group.color = color.name()
            self._save_to_project()
            self._rebuild_grid()

    def _duplicate_group(self, group: FixtureGroup):
        new_g = FixtureGroup(
            name=f"{group.name} Copy",
            color=group.color,
            fixture_ids=list(group.fixture_ids),
            base_order_fixture_ids=list(
                getattr(group, "base_order_fixture_ids", []) or group.fixture_ids
            ),
            route_order_sheets=[
                dict(sheet) for sheet in (getattr(group, "route_order_sheets", []) or [])
                if isinstance(sheet, dict)
            ],
        )
        self._snapshot_for_undo()
        self._groups.append(new_g)
        self._save_to_project()
        self._rebuild_grid()

    def _delete_group(self, group: FixtureGroup):
        if QMessageBox.question(
                self, "删除组",
                f"确认删除组 '{group.name}'？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) == QMessageBox.StandardButton.Yes:
            self._snapshot_for_undo()
            self._groups = [g for g in self._groups if g.id != group.id]
            self._save_to_project()
            if self._sel_group_id == group.id:
                self._sel_group_id = None
                self._all_btn.setChecked(True)
                self._show_all()
            self._rebuild_grid()

    # ── 辅助 ─────────────────────────────────────────────────────────────────

    def _get_group(self, group_id: str) -> Optional[FixtureGroup]:
        for g in self._groups:
            if g.id == group_id:
                return g
        return None

    def _ordered_group_fixture_ids(self, group: FixtureGroup) -> List[str]:
        # ★ R195 用户实报：保存实时组（奇偶/间隔子集）后点击该组，所有相关页面
        #   被同步成**母集合全部设备**——旧实现把 base_order（R165 母集合序，
        #   全集）拼进成员。成员以 fixture_ids 为准；base_order 只做顺序追溯
        #   （∩成员 在前，其余按 fixture_ids 原序补齐）。
        valid = {f.id for f in self._all_fixtures}
        member = [fid for fid in group.fixture_ids if fid in valid]
        from src.core.fixture_selection import ordered_members
        return ordered_members(member, group.base_order_fixture_ids)

    def load_groups(self, groups: List[FixtureGroup]):
        self._groups = list(groups)
        self._rebuild_grid()

    def get_selected_fixture_ids(self) -> List[str]:
        """返回当前选中组（或 All）的设备 ID 列表"""
        if self._sel_group_id is None:
            return [f.id for f in self._all_fixtures]
        group = self._get_group(self._sel_group_id)
        if group is None:
            return [f.id for f in self._all_fixtures]
        return self._ordered_group_fixture_ids(group)

    def get_live_selected_fixture_ids(self) -> List[str]:
        """返回右侧列表当前行选择；没有行选择时回退到当前组/All。"""
        ids = []
        for item in self._fixture_list.selectedItems():
            fid = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if fid:
                ids.append(str(fid))
        return ids or self.get_selected_fixture_ids()


# ─── 组编辑对话框 ────────────────────────────────────────────────────────────

class _GroupEditDialog(QDialog):
    selection_changed = pyqtSignal(list)

    def __init__(self, all_fixtures, selected_ids: List[str],
                 name: str = "", color: str = "#1A4A7A", parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑设备组")
        self.setMinimumSize(360, 420)
        # 对话框/标签/输入框外观由全局 QSS 提供（原行内样式已删）
        self._color = color
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)   # §10.4 内容边距
        lay.setSpacing(10)

        # 名称
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("组名:"))
        self._name_edit = QLineEdit(name)
        name_row.addWidget(self._name_edit)
        lay.addLayout(name_row)

        # 颜色
        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("颜色:"))
        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(80, 28)
        self._color_btn.setToolTip("更改组颜色")
        self._color_btn.setCursor(Qt.CursorShape.PointingHandCursor)   # §9：可点击手型
        self._update_color_swatch()
        self._color_btn.clicked.connect(self._pick_color)
        color_row.addWidget(self._color_btn)
        color_row.addStretch()
        lay.addLayout(color_row)

        # 设备选择
        lay.addWidget(QLabel("选择设备（Ctrl/Shift 多选）:"))

        # 搜索
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索设备名…")
        self._search.textChanged.connect(self._filter)
        lay.addWidget(self._search)

        # 全选/全不选
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(6)
        for lbl, fn, tip in [("全选", self._sel_all, "选中当前列表中的所有设备"),
                             ("全不选", self._sel_none, "清空当前列表的设备选择")]:
            b = QPushButton(lbl)
            b.setFixedHeight(24)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            ctrl_row.addWidget(b)
        ctrl_row.addStretch()
        lay.addLayout(ctrl_row)
        # ── 快捷选择工具 ─────────────────────────────────────────────────────
        # 输入框/分组框外观由全局 QSS 提供（原 _spin_style/QGroupBox 行内样式已删）；
        # 标签与动作按钮保留局部语义色。
        _lbl_style = f"QLabel{{color:{TEXT_SECONDARY};font-size:10px;}}"
        # R50：去绿——中性面，主操作=focus_blue 描边，次操作纯中性
        _btn_style = (f"QPushButton{{background:{BG_BUTTON};color:{TEXT_PRIMARY};"
                      f"border:1px solid #3D3D3D;"
                      "border-radius:4px;padding:2px 10px;font-size:11px;}"
                      f"QPushButton:hover{{background:#545454;}}")
        _btn_style_primary = (f"QPushButton{{background:{BG_BUTTON};color:{TEXT_PRIMARY};"
                      f"border:1px solid {FOCUS_BLUE};"
                      "border-radius:4px;padding:2px 10px;font-size:11px;}"
                      f"QPushButton:hover{{background:#545454;}}")
        quick_box = QGroupBox("快捷选择")
        quick_lay = QVBoxLayout(quick_box)
        quick_lay.setSpacing(4)
        quick_lay.setContentsMargins(6, 4, 6, 4)
        # 第一行：选几个 / 间隔几个 / 从第几个开始
        row1 = QHBoxLayout()
        row1.setSpacing(4)
        row1.addWidget(QLabel("选", styleSheet=_lbl_style))
        self._qs_count = QSpinBox()
        self._qs_count.setRange(1, 512)
        self._qs_count.setValue(1)
        self._qs_count.setToolTip("每组选几个设备")
        row1.addWidget(self._qs_count)
        row1.addWidget(QLabel("个，间隔", styleSheet=_lbl_style))
        self._qs_skip = QSpinBox()
        self._qs_skip.setRange(0, 512)
        self._qs_skip.setValue(1)
        self._qs_skip.setToolTip("每组后跳过几个设备（0=不跳过）")
        row1.addWidget(self._qs_skip)
        row1.addWidget(QLabel("个，从第", styleSheet=_lbl_style))
        self._qs_start = QSpinBox()
        self._qs_start.setRange(1, 512)
        self._qs_start.setValue(1)
        self._qs_start.setToolTip("从第几个设备开始（1=第一个）")
        row1.addWidget(self._qs_start)
        row1.addWidget(QLabel("个开始", styleSheet=_lbl_style))
        row1.addStretch()
        quick_lay.addLayout(row1)
        # 第二行：应用按钮
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        apply_btn = QPushButton("应用快捷选择")
        apply_btn.setStyleSheet(_btn_style_primary)
        apply_btn.setFixedHeight(24)
        apply_btn.setToolTip("按照上方规则快速选择设备")
        apply_btn.clicked.connect(self._apply_quick_select)
        row2.addWidget(apply_btn)
        add_btn = QPushButton("追加到当前选择")
        add_btn.setStyleSheet(_btn_style)
        add_btn.setFixedHeight(24)
        add_btn.setToolTip("在当前已选设备基础上追加快捷选择的设备")
        add_btn.clicked.connect(lambda: self._apply_quick_select(add=True))
        row2.addWidget(add_btn)
        row2.addStretch()
        quick_lay.addLayout(row2)
        lay.addWidget(quick_box)

        self._list = FixtureShortcutListWidget()
        self._list.setStyleSheet("QListWidget::item{min-height:24px;}")
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.MultiSelection)
        # 选中态＝select_blue 底白字，由全局 QSS 统一（原行内列表样式已删）
        self._all_fixtures = list(all_fixtures)
        self._sel_ids = set(selected_ids)
        self._updating_list = False
        self._populate(self._all_fixtures)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        lay.addWidget(self._list)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        # §10.4：确定=primary / 取消=cancel 语义标记 + 标准浮窗外框
        btns.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setProperty("role", "cancel")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
        apply_dialog_chrome(self)

    def _update_color_swatch(self):
        """色板按钮预览：底色为用户组色（数据），描边走主题 token。"""
        self._color_btn.setStyleSheet(
            f"background:{self._color};"
            f"border:1px solid {BORDER_NORMAL};border-radius:4px;")

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(self._color), self)
        if c.isValid():
            self._color = c.name()
            self._update_color_swatch()

    def _populate(self, fixtures):
        self._updating_list = True
        self._list.blockSignals(True)
        self._list.clear()
        for fi in fixtures:
            item = QListWidgetItem(
                f"  [{fi.id[:6]}]  {fi.name}"
                f"  (U{getattr(fi,'universe',1)} Ch{getattr(fi,'start_channel',1)})")
            item.setData(Qt.ItemDataRole.UserRole, fi.id)
            if fi.id in self._sel_ids:
                item.setSelected(True)
            self._list.addItem(item)
        self._list.blockSignals(False)
        self._updating_list = False

    def _filter(self, text):
        text = text.lower()
        filtered = [f for f in self._all_fixtures
                    if text in f.name.lower() or text in f.id.lower()]
        self._populate(filtered)

    def _sel_all(self):
        for i in range(self._list.count()):
            self._list.item(i).setSelected(True)

    def _sel_none(self):
        for i in range(self._list.count()):
            self._list.item(i).setSelected(False)

    def _apply_quick_select(self, add: bool = False):
        """快捷选择工具：按照"选N个、跳过M个、从第K个开始"的规则选择设备。

        逻辑：
          - 从当前列表中第 (start-1) 个设备开始
          - 每轮选 count 个，跳过 skip 个，循环直到列表末尾
          - add=False 时先清除当前选择；add=True 时在已选基础上追加
        """
        count = max(1, self._qs_count.value())
        skip  = max(0, self._qs_skip.value())
        start = max(1, self._qs_start.value()) - 1  # 转为 0-indexed

        total = self._list.count()
        if total == 0:
            return

        # 计算要选中的行号集合
        select_rows = set()
        i = start
        while i < total:
            # 选 count 个
            for j in range(count):
                if i + j < total:
                    select_rows.add(i + j)
            # 跳过 skip 个
            i += count + skip

        # 应用选择
        for row in range(total):
            item = self._list.item(row)
            if item is None:
                continue
            if row in select_rows:
                item.setSelected(True)
            elif not add:
                item.setSelected(False)

    def _selected_ids(self) -> List[str]:
        return [str(self._list.item(i).data(Qt.ItemDataRole.UserRole))
                for i in range(self._list.count())
                if self._list.item(i).isSelected()]

    def _on_selection_changed(self):
        if self._updating_list:
            return
        self._sel_ids = set(self._selected_ids())
        self.selection_changed.emit(list(self._sel_ids))

    def get_result(self):
        name = self._name_edit.text().strip()
        fids = self._selected_ids()
        return name, self._color, fids
