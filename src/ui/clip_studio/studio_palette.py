# -*- coding: utf-8 -*-
"""Clip Studio 左侧卡片栏（R172 设计 §3.1 / B8；R183 A5 = 蜂巢网格 + 分类折叠并存）。

* 数据唯一来源：`studio_cards.PALETTE_GROUPS`（8 组）+ `CARD_CATALOG`（勿自建第二份清单）。
* 结构（R183 A5，用户定案"既要目前的排列方式，又要支持在分类折叠的基础上展示"）：
  搜索框 + 推荐条 + **8 个可折叠分组**；每组 = 组名头（tabler chevron + 组名 + 卡数）
  + **组内一个 `HoneycombGrid`**（R182 蜂巢瓦片排布与组色带 `GROUP_COLORS` 原样沿用）。
* **搜索**：命中时全展开且只显示命中的分组；清空后恢复用户的折叠意图。
  搜索与主动分组折叠沿 OperatingPreferences 保存，临时强制展开不落盘。
* 每个卡条目：tabler 图标（`HoneycombGrid` 自绘；B0 资产命中时换 `spec.art` 缩略，
  R183-B1）+ 中文名 + 一句话说明。
  **拖拽源**：QDrag mime `application/x-qascade-studio-card` = 卡 key
  （mime 构造抽为模块级 `make_card_mime` 供测试断言内容）。
* 顶部**推荐条**（B8）：`set_recommendations(keys)` 渲染前 4 个快捷创建按钮，
  点击 → `quick_create_requested(key)`；拖动开始 → `card_drag_started(key)`
  （面板用它更新画布上下文推荐）。
"""
from __future__ import annotations

from PyQt6.QtCore import QMimeData, QPoint, QPointF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QDrag
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QToolButton, QVBoxLayout, QWidget

from src.ui import qascade_theme as _th
from src.ui.audio_mod_draw.honeycomb_grid import HoneycombGrid
from src.ui.clip_studio.studio_cards import CARD_CATALOG, GROUP_COLORS, GROUP_LABELS, PALETTE_GROUPS
from src.ui.clip_studio.studio_graph import node_group
from src.ui.main_icon_library import load_tabler_icon
from src.ui.palette_operating_preferences import bind_studio_preferences

__all__ = [
    "MIME_CARD",
    "CardPalette",
    "PaletteGroup",
    "catalog_rows",
    "make_card_mime",
]

MIME_CARD = "application/x-qascade-studio-card"
_REC_MAX = 4
_HEAD_H = 24
_HEAD_ICON_PX = 14
#: chevron 折叠指示（tabler 矢量实存：outline/chevron-down|chevron-right）。
_CHEVRON_ON = "chevron-down"
_CHEVRON_OFF = "chevron-right"
_HEAD_QSS = (
    "QToolButton {{ background: {bg}; border: none; border-radius: 4px;"
    " color: {fg}; font-size: 11px; font-weight: bold; text-align: left;"
    " padding: 2px 6px; }}"
    "QToolButton:hover {{ background: {hover_bg}; color: {hover_fg}; }}"
)

#: 左栏组名（中文）→ 效果大类 key：`GROUP_LABELS` 反查（组色带/校验都按该 key）。
_GROUP_BY_TITLE = {label: key for key, label in GROUP_LABELS.items()}


def make_card_mime(key: str) -> QMimeData:
    """卡 key → QDrag mime（唯一构造点；测试直接断言内容）。"""
    mime = QMimeData()
    mime.setData(MIME_CARD, str(key).encode("utf-8"))
    return mime


def catalog_rows(keys) -> list:
    """卡 key 列表 → `HoneycombGrid` 行 (key, 名称, 一句话说明, icon, 组色带, art_id)。

    组色带 = `GROUP_COLORS[node_group(key)]`（未知组回退焦点蓝）；目录外 key 跳过。
    art_id = `spec.art`（B0 `cards/` 25 id 与 ART_KEYS 一一同名，R183-B1；46 卡全命中）。
    """
    rows = []
    for key in keys:
        spec = CARD_CATALOG.get(str(key))
        if spec is None:
            continue
        color = GROUP_COLORS.get(node_group({"key": str(key)}), _th.FOCUS_BLUE)
        rows.append((str(key), spec.name, spec.desc.split("。")[0] if spec.desc else "",
                     spec.icon, color, str(getattr(spec, "art", "") or "")))
    return rows


class _GridUnion:
    """8 个分组网格的只读聚合（`CardPalette.card_grid()` 的返回值）。

    R182 的 `card_grid()` 只有一张跨目录大网格；R183 拆成 8 组后，本聚合按
    "跨组并集"保留同一套方法语义（键序 = `PALETTE_GROUPS` 展平序 = 目录序）。
    """

    def __init__(self, grids) -> None:
        self._grids = list(grids)

    def grids(self) -> list:
        """各组网格本体（按分组顺序）。"""
        return list(self._grids)

    def item_keys(self) -> list:
        return [k for g in self._grids for k in g.item_keys()]

    def visible_keys(self) -> list:
        return [k for g in self._grids for k in g.visible_keys()]

    def tile_rect(self, key: str):
        for g in self._grids:
            rect = g.tile_rect(key)
            if rect is not None:
                return rect
        return None

    def layout_columns(self) -> int:
        return max((g.layout_columns() for g in self._grids), default=0)

    def apply_filter(self, query: str) -> None:
        for g in self._grids:
            g.apply_filter(query)


class PaletteGroup(QFrame):
    """单个可折叠分组：头（chevron + 组名 + 卡数）+ 体（组内蜂巢网格）。

    折叠态 `_expanded` 是本控件实例属性；主卡库适配保存主动用户选择。
    搜索期 `begin_search()` 先快照用户折叠态再强制全展开，`end_search()` 复原。
    """

    def __init__(self, title: str, group: str, rows, parent=None) -> None:
        super().__init__(parent)
        self._title = str(title)
        self._group = str(group or "")
        self._color = GROUP_COLORS.get(self._group, _th.FOCUS_BLUE)
        self._expanded = True
        self._mem_expanded = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        self._head = QToolButton()
        self._head.setCheckable(True)
        self._head.setChecked(True)
        self._head.setFixedHeight(_HEAD_H)
        self._head.setCursor(Qt.CursorShape.PointingHandCursor)
        self._head.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._head.setIconSize(QSize(_HEAD_ICON_PX, _HEAD_ICON_PX))
        self._head.setStyleSheet(_HEAD_QSS.format(
            bg=_th.BG_PANEL_SUB, fg=_th.TEXT_PRIMARY,
            hover_bg=_th.BG_PANEL, hover_fg=_th.CANVAS_SELECTION))
        self._head.toggled.connect(self.set_expanded)
        v.addWidget(self._head)
        self._body = QWidget()
        body_lay = QVBoxLayout(self._body)
        body_lay.setContentsMargins(2, 0, 0, 2)
        body_lay.setSpacing(0)
        self._grid = HoneycombGrid()
        self._grid._face_prefix = "studio:"   # D1：与 Studio 画布卡同一幅画
        self._grid.set_items(rows)
        body_lay.addWidget(self._grid)
        v.addWidget(self._body)
        self._sync_head()

    # ── 身份/访问 ───────────────────────────────────────────────────────
    def title(self) -> str:
        return self._title

    def group(self) -> str:
        return self._group

    def count(self) -> int:
        return len(self._grid.item_keys())

    def grid(self) -> HoneycombGrid:
        return self._grid

    def header(self) -> QToolButton:
        return self._head

    def body(self) -> QWidget:
        """折叠体的容器（`set_expanded(False)` → 不可见）。"""
        return self._body

    def card_keys(self) -> list:
        """本组卡 key（顺序 = `PALETTE_GROUPS` 该组内顺序）。"""
        return self._grid.item_keys()

    # ── 折叠 ────────────────────────────────────────────────────────────
    def is_expanded(self) -> bool:
        return bool(self._expanded)

    def set_expanded(self, on: bool) -> None:
        """设置折叠态（头点击 → `toggled` 也走这里；体可见性随之切换）。"""
        on = bool(on)
        self._expanded = on
        self._body.setVisible(on)
        if self._head.isChecked() != on:
            self._head.blockSignals(True)
            self._head.setChecked(on)
            self._head.blockSignals(False)
        self._sync_head()

    def _sync_head(self) -> None:
        self._head.setIcon(load_tabler_icon(
            _CHEVRON_ON if self._expanded else _CHEVRON_OFF, _HEAD_ICON_PX, self._color))
        self._head.setText(f"{self._title} · {self.count()}")
        self._head.setToolTip(
            f"{self._title}：{self.count()} 张卡"
            f"（点击{'收起' if self._expanded else '展开'}）")

    # ── 搜索 ────────────────────────────────────────────────────────────
    def begin_search(self) -> None:
        """进入搜索：快照用户折叠态（只第一次）后强制展开。"""
        if self._mem_expanded is None:
            self._mem_expanded = self._expanded
        self.set_expanded(True)

    def end_search(self) -> None:
        """退出搜索：恢复搜索前的折叠记忆。"""
        if self._mem_expanded is not None:
            memo, self._mem_expanded = self._mem_expanded, None
            self.set_expanded(memo)

    def apply_filter(self, query: str) -> int:
        """组内网格过滤；返回命中卡数（0 → 调用方隐藏本分组）。"""
        self._grid.apply_filter(query)
        return len(self._grid.visible_keys())


class CardPalette(QWidget):
    """左侧卡片栏：搜索框 + 推荐条 + 8 个可折叠分组的蜂巢网格（数据 = PALETTE_GROUPS）。"""

    card_drag_started = pyqtSignal(str)         # 条目开始拖拽（面板更新画布上下文推荐）
    quick_create_requested = pyqtSignal(str)    # 推荐条/瓦片快捷创建

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(256)
        self._last_query = ""
        self._search_mode = False
        self._sections: list[PaletteGroup] = []
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(4)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索卡片…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.apply_filter)
        v.addWidget(self.search)
        self.rec_title = QLabel("推荐")
        self.rec_title.setStyleSheet(
            f"color: {_th.TEXT_SECONDARY}; font-size: 11px; background: transparent;")
        v.addWidget(self.rec_title)
        self.rec_bar = QWidget()
        self._rec_lay = QHBoxLayout(self.rec_bar)
        self._rec_lay.setContentsMargins(0, 0, 0, 0)
        self._rec_lay.setSpacing(4)
        self._rec_lay.addStretch(1)
        v.addWidget(self.rec_bar)
        v.addWidget(self._build_body(), 1)
        bind_studio_preferences(self)

    def _build_body(self) -> QScrollArea:
        """分组滚动体：按 `PALETTE_GROUPS` 建 8 个可折叠分组（组内蜂巢网格）。"""
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(6)
        for title, keys in PALETTE_GROUPS:
            # 组 key 走 `GROUP_LABELS` 反查（左栏组名就是它的值域真源；不依赖首卡）
            group = _GROUP_BY_TITLE.get(title, "")
            sec = PaletteGroup(title, group, catalog_rows(keys))
            sec.grid().activated.connect(self._on_grid_activated)
            sec.grid().drag_started.connect(self._on_grid_drag)
            self._sections.append(sec)
            body_lay.addWidget(sec)
        body_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        return scroll

    # ── 访问器 ──────────────────────────────────────────────────────────
    def sections(self) -> list:
        """全部分组控件（按 `PALETTE_GROUPS` 顺序）。"""
        return list(self._sections)

    def section(self, title: str) -> PaletteGroup | None:
        """按组名取分组（无 → None）。"""
        return next((s for s in self._sections if s.title() == str(title)), None)

    def card_grid(self):
        """8 组网格的聚合视图（旧 R182 单网格 API 的跨组并集语义）。"""
        return _GridUnion([sec.grid() for sec in self._sections])

    def entries(self) -> list:
        """全部卡 key（分组顺序展平；测试用：目录覆盖断言）。"""
        return [k for sec in self._sections for k in sec.card_keys()]

    def _on_grid_activated(self, key: str) -> None:
        """点瓦片 = 快捷创建（与推荐条同路径）。"""
        self.quick_create_requested.emit(str(key))

    def _on_grid_drag(self, key: str) -> None:
        """按住拖出 = QDrag(mime=卡 key) 入画布（R183 B3：半透明瓦片影像）。"""
        self.card_drag_started.emit(str(key))
        grid = self.sender() if isinstance(self.sender(), HoneycombGrid) else None
        drag = QDrag(grid if isinstance(grid, QWidget) else self)
        drag.setMimeData(make_card_mime(str(key)))
        # R183 清查④③（P2）：CardPalette 无 _grid 属性（分组网格在
        #   PaletteGroup 内）——影像源用信号发送者（HoneycombGrid 本体）。
        if grid is not None:
            pm = grid.make_drag_pixmap(str(key))
            if pm is not None:
                drag.setPixmap(pm)
                # 清查③②D：hotspot 用逻辑像素（DPR>1 缩放屏偏移 1/4 瓦片）
                _dpr = pm.devicePixelRatio() or 1.0
                drag.setHotSpot(QPoint(int(round(pm.width() / _dpr / 2.0)),
                                       int(round(pm.height() / _dpr / 2.0))))
        drag.exec(Qt.DropAction.CopyAction)

    # ── 搜索过滤 ────────────────────────────────────────────────────────
    def apply_filter(self, query: str) -> None:
        """搜索：全展开且只显命中分组；清空 → 恢复各分组折叠记忆。"""
        self._last_query = str(query or "")
        searching = bool(self._last_query.strip())
        if searching != self._search_mode:
            self._search_mode = searching
            for sec in self._sections:
                if searching:
                    sec.begin_search()
                else:
                    sec.end_search()
        for sec in self._sections:
            sec.setVisible(sec.apply_filter(self._last_query) > 0)

    # ── 推荐条（B8）─────────────────────────────────────────────────────
    def set_recommendations(self, keys) -> None:
        """渲染前 4 个推荐卡快捷按钮（空 → 显示占位说明）。"""
        while self._rec_lay.count():
            item = self._rec_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)      # 立即脱离父子（deleteLater 要等事件循环）
                w.deleteLater()
        shown = 0
        for key in list(keys or [])[:_REC_MAX]:
            spec = CARD_CATALOG.get(str(key))
            if spec is None:
                continue
            btn = QToolButton()
            btn.setIcon(load_tabler_icon(spec.icon, 15, _th.CANVAS_INK))
            btn.setText(spec.name)
            btn.setToolTip(f"{spec.name}：{spec.desc}")
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            btn.clicked.connect(lambda _c=False, k=str(key): self.quick_create_requested.emit(k))
            self._rec_lay.addWidget(btn)
            shown += 1
        if shown == 0:
            hint = QLabel("拖入卡片后出现推荐")
            hint.setStyleSheet(f"color: {_th.TEXT_DISABLED}; font-size: 11px;"
                               "background: transparent;")
            self._rec_lay.addWidget(hint)
        self._rec_lay.addStretch(1)
