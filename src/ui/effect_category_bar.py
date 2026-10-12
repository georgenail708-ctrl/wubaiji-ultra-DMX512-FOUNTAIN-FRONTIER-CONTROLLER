# -*- coding: utf-8 -*-
"""src/ui/effect_category_bar.py — 效果库两级分类选择条（R180；R183 修复轮 C4 重排）。

布局：第 1 行「全部 / 最近 / 收藏 / 一级大类」chips；第 2 行当前大类（「全部」视图 =
全库）的二级细分类 chips。两行都是**流式换行**（``src/ui/flow_layout.FlowLayout``）。

C4 修（用户截图 13-22-08：分类条整条空白、白占一条 ~60px 的带）：修前两行是不换行
的 QHBoxLayout，「全部」视图第 2 行有 24 个细分类 chip → 行最小宽 ≈2000px，经
QStackedWidget（宽屏 chips 页 / 窄屏下拉页）向上传导，再叠加只在 resize 时判定的
紧凑态迟滞状态机，两页都没有正确呈现。数据与状态逻辑本身经真实引擎全库 1223 个
预设实测无误（8 个大类 chip + 24 个细分类 chip 全部构建成功）——问题在呈现机制。
重排后：
  * 删 QStackedWidget 与紧凑态状态机：宽度不够就换行，不存在"第二种形态"；
  * 条高 = FlowLayout.heightForWidth 算出的实际行数，不再预留空带；
  * 公共 API 与语义全部保留（refresh / set_search_active / set_recent_count /
    set_favorite_count / clear / set_top_key / current_filters / top_key /
    filterChanged / ``fine_combo`` 兼容句柄）；两个下拉仍在但隐藏，只作旧调用点
    ``currentData()`` 的兼容读口，与 chips 双写同步。
颜色走 qascade_theme 令牌；图标走 load_tabler_icon（无 emoji）；chip 五态齐全。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from . import qascade_theme as _th
from .effect_categories import (
    TOP_GROUPS, TOP_RECENT, TOP_FAV, group_counts, sub_counts, sub_label, top_group,
)
from .flow_layout import FlowLayout
from .main_icon_library import load_tabler_icon

#: chip 外观挂**行容器**（R184-C1：重建时 32 个 chip 各解析一次样式表 → 每行
#: 只解析一次；`QPushButton{...}` 祖先选择器只命中行内 chips，不出行）
_ROW_QSS = (
    f"QPushButton{{background:{_th.BG_PANEL_SUB};color:{_th.TEXT_SECONDARY};"
    f"border:1px solid {_th.BORDER_NORMAL};border-radius:11px;"
    "padding:2px 10px;font-size:11px;}"
    f"QPushButton:hover{{background:{_th.BG_BUTTON_HOVER};color:{_th.TEXT_PRIMARY};}}"
    f"QPushButton:pressed{{background:{_th.SELECT_BLUE_PRESSED};}}"
    f"QPushButton:checked{{background:{_th.SELECT_BLUE};color:{_th.TEXT_SELECTED_FG};"
    f"border-color:{_th.FOCUS_BLUE};}}"
    f"QPushButton:disabled{{color:{_th.TEXT_DISABLED};}}"
)
#: 条自身底色 + 下沿分隔（带选择器：不级联到 chip）
_BAR_QSS = (f"QWidget#fxCategoryBar{{background:{_th.BG_PANEL};"
            f"border-bottom:1px solid {_th.BORDER_NORMAL};}}")
_ROW_MARGINS = (8, 4, 8, 4)
_ROW_SPACING = 4
_ICON_PX = 12


def _clear_layout(lay: QLayout) -> None:
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()          # R184-C1：先隐再删（deleteLater 到执行有一帧空窗）
            w.deleteLater()


def _flow_row(widget: QWidget) -> FlowLayout:
    lay = FlowLayout(widget)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(_ROW_SPACING)
    return lay


class EffectCategoryBar(QWidget):
    """两级分类选择条；选中态变化发 ``filterChanged()``。"""

    filterChanged = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._top = ""            # ""=全部 / TOP_RECENT / TOP_GROUPS key
        self._sub = ""            # 二级细分类全名，""=不限
        self._search_active = False   # R182：搜索态（全库检索时筛选被旁路）
        self._presets: List = []
        self._counts: Dict[str, int] = {}
        self._recent_n = 0
        self._fav_n = 0          # ★ R183 批 A：收藏数（0 → 收藏 chip 不显示）
        self._compact = False    # 兼容字段：紧凑态已废弃（流式换行替代，恒 False）
        self._top_need = 0
        self._sub_need = 0
        self._top_btns: List[QPushButton] = []
        self._sub_btns: List[QPushButton] = []

        self.setObjectName("fxCategoryBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_BAR_QSS)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        root = QVBoxLayout(self)
        root.setContentsMargins(*_ROW_MARGINS)
        root.setSpacing(0)
        self._rows = QWidget(self)
        rows_lay = QVBoxLayout(self._rows)
        rows_lay.setContentsMargins(0, 0, 0, 0)
        rows_lay.setSpacing(_ROW_SPACING)
        self._top_row = QWidget(self._rows)
        self._top_row.setStyleSheet(_ROW_QSS)    # C1：chip 外观每行只解析一次
        self._top_lay = _flow_row(self._top_row)
        self._sub_row = QWidget(self._rows)
        self._sub_row.setStyleSheet(_ROW_QSS)
        self._sub_lay = _flow_row(self._sub_row)
        rows_lay.addWidget(self._top_row)
        rows_lay.addWidget(self._sub_row)
        root.addWidget(self._rows)

        # 兼容读口（隐藏）：effect_browser 的 `_cat_combo.currentData()` = 当前细分类
        self._top_combo = QComboBox(self)
        self._sub_combo = QComboBox(self)
        self._top_combo.hide()
        self._sub_combo.hide()
        self._top_combo.currentIndexChanged.connect(self._on_top_combo)
        self._sub_combo.currentIndexChanged.connect(self._on_sub_combo)
        self.fine_combo = self._sub_combo

    # ── 公共 API ─────────────────────────────────────────────────────────

    def current_filters(self) -> Tuple[str, str]:
        """当前筛选：(一级键, 二级细分类)；("","","") 形态下空串=不限。"""
        return (self._top, self._sub)

    def top_key(self) -> str:
        return self._top

    def set_top_key(self, key: str) -> None:
        """外部直接设大类（★ R183 批 A：另存/删除用户预设后把视图拉回全库）。

        走 `_set_top` 的同一状态机（非法键自动落回 ""）并照常发 `filterChanged`，
        不做"静默改状态"—— 视图与筛选永远只有一个真源。
        """
        self._set_top(key)

    def refresh(self, presets: Sequence) -> None:
        """按当前全库重建两组控件；已有的选择若仍有效则保留。"""
        self._presets = list(presets or [])
        self._counts = group_counts(self._presets)
        self._rebuild_top_views()
        self._rebuild_sub_views()
        self._sync_views()

    def set_search_active(self, active: bool) -> None:
        """搜索态一致性（R182）：query 非空时筛选被旁路（全库搜索）。

        chips 仍可点，但视觉上必须如实告知"当前筛选未生效"——改文案 + 加
        property：``search`` 为真时 chip 强制不勾选（``_sync_views`` 跳过），
        二级行显示"全库搜索中"提示条；退出搜索恢复原筛选与勾选。
        """
        active = bool(active)
        if active == getattr(self, "_search_active", False):
            return
        self._search_active = active
        self._rebuild_sub_views()
        self._sync_views()

    def set_recent_count(self, n: int) -> None:
        """刷新「最近」chip/下拉项的计数（历史长度由 effect_browser 提供）。"""
        self._recent_n = max(0, int(n))
        for btn in self._top_btns:
            if btn.property("grp") == TOP_RECENT:
                btn.setText(f"最近 {self._recent_n}")
        idx = self._top_combo.findData(TOP_RECENT)
        if idx >= 0:
            self._top_combo.setItemText(idx, f"最近使用（{self._recent_n}）")

    def set_favorite_count(self, n: int) -> None:
        """刷新「收藏」chip 计数（★ R183 批 A）。

        与「最近」不同：收藏数为 0 时该 chip **整条不显示**（零计数大类不显示的
        既有规则），故计数变化可能增删控件 → 需要重建 top 视图（保留当前选择）。

        归零时若正停在「收藏」视图（chips 会消失、视图必然为空）→ 静默回退
        「全部」，否则用户会留在一个没有入口可点的空列表里。回退**不发**
        `filterChanged`：调用方（`_populate_list`）紧接着就会读 `top_key()` 重排。
        """
        n = max(0, int(n))
        if n == getattr(self, "_fav_n", 0):
            return
        self._fav_n = n
        if n == 0 and self._top == TOP_FAV:
            self._top = ""
            self._sub = ""
            self._rebuild_sub_views()
        self._rebuild_top_views()
        self._sync_views()

    def clear(self) -> None:
        """回到全库视图（场景调入等"必须让该预设可见"的路径用）。"""
        if not self._top and not self._sub:
            return
        self._top = ""
        self._sub = ""
        self._rebuild_sub_views()
        self._sync_views()
        self.filterChanged.emit()

    # ── 视图重建 ─────────────────────────────────────────────────────────

    def _top_entries(self) -> List[Tuple[str, str, int, str, str]]:
        """(key, label, count, icon, color)；计数为零的大类不显示。

        R183 批 A：「全部」「最近」为常驻入口（计数恒显示）；「收藏」只在
        ``_fav_n > 0`` 时出现 —— 与真实大类同一规则（零计数不显示），避免
        给从未用过收藏的用户多一个恒空入口。星标主题色复用 ``KEYFRAME_GOLD``
        （既有令牌，不引新色值）。
        """
        entries = [("", "全部", len(self._presets), "apps", _th.TEXT_SECONDARY),
                   (TOP_RECENT, "最近", self._recent_n, "history", _th.BRIGHT_WARN)]
        if getattr(self, "_fav_n", 0) > 0:
            entries.append((TOP_FAV, "收藏", self._fav_n, "star", _th.KEYFRAME_GOLD))
        for g in TOP_GROUPS:
            n = self._counts.get(g.key, 0)
            if n:
                entries.append((g.key, g.label, n, g.icon, g.color))
        return entries

    def _chip(self, text: str, key: str, icon: str, color: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setFixedHeight(22)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setProperty("grp", key)
        ic = load_tabler_icon(icon, _ICON_PX, color) if icon else None
        if ic is not None and not ic.isNull():
            btn.setIcon(ic)
            btn.setIconSize(QSize(_ICON_PX, _ICON_PX))
        return btn

    def _fill_buttons(self, entries, lay, store: List[QPushButton], slot) -> int:
        """按 entries 重建一行 chips，返回该行单行排布所需宽度（诊断/兼容用）。

        ``n`` 为 None 时只显示标签（搜索态提示 chip 无计数）。
        """
        _clear_layout(lay)
        store.clear()
        width = _ROW_MARGINS[0] + _ROW_MARGINS[2]
        for key, label, n, icon, color in entries:
            btn = self._chip(f"{label} {n}" if n is not None else str(label),
                             key, icon, color)
            btn.clicked.connect(lambda _=False, k=key: slot(k))
            store.append(btn)
            lay.addWidget(btn)
            width += btn.sizeHint().width() + lay.spacing()
        return width

    def _rebuild_top_views(self) -> None:
        entries = self._top_entries()
        self._top_need = self._fill_buttons(
            entries, self._top_lay, self._top_btns, self._set_top)
        self._top_combo.blockSignals(True)
        self._top_combo.clear()
        for key, label, n, icon, color in entries:
            self._top_combo.addItem(
                load_tabler_icon(icon, 13, color), f"{label}（{n}）", key)
        self._top_combo.blockSignals(False)

    def _rebuild_sub_views(self) -> None:
        grp = top_group(self._top)
        # 最近/收藏：无细分语义（不看 sub）——两者都是"id 子集视图"
        hide_row = self._top in (TOP_RECENT, TOP_FAV)
        if grp is not None:
            cats = sub_counts(self._presets, grp.key)
        elif hide_row:
            cats = []
        else:
            cats = self._all_fine_counts()
        entries = [("", "全部", sum(n for _, n in cats), "", _th.TEXT_SECONDARY)]
        if self._search_active:
            # 搜索态：二级筛选不参与检索（跨全库），如实标注而非伪装勾选。
            entries = [("", "全库搜索中", None, "search", _th.BRIGHT_WARN)]
            cats = []
        elif not hide_row:
            entries += [(c, sub_label(c), n, "", _th.TEXT_SECONDARY)
                        for c, n in cats]
        self._sub_need = self._fill_buttons(
            entries, self._sub_lay, self._sub_btns, self._set_sub)
        # ★ R182：top==""（全部视图）也显示二级行 —— 用户"我要精简，不是找不到
        #   分类"：细分类名必须始终可见可点，否则旧细分类等同被藏。
        self._sub_row.setVisible(self._search_active or (bool(cats) and not hide_row))
        self._sub_combo.blockSignals(True)
        self._sub_combo.clear()
        self._sub_combo.addItem("全部子类", "")
        for cat, n in cats:
            self._sub_combo.addItem(f"{sub_label(cat)}（{n}）", cat)
        self._sub_combo.blockSignals(False)

    def _all_fine_counts(self) -> List[Tuple[str, int]]:
        """top="" 时「全部」视图的二级口径（按语义位次排序，含游离细分类）。"""
        from .effect_categories import fine_category_sort_key
        from .effect_catalog_curation import display_category as normalize_effect_category
        counter: Dict[str, int] = {}
        for p in self._presets:
            cat = normalize_effect_category(getattr(p, "category", ""))
            counter[cat] = counter.get(cat, 0) + 1
        return sorted(counter.items(), key=lambda kv: fine_category_sort_key(kv[0]))

    # ── 状态迁移与视图同步 ───────────────────────────────────────────────

    def _set_top(self, key) -> None:
        key = str(key or "")
        if key and key not in (TOP_RECENT, TOP_FAV) and top_group(key) is None:
            key = ""
        if key != self._top:
            self._top = key
            self._sub = ""
            self._rebuild_sub_views()
        self._sync_views()
        self.filterChanged.emit()

    def _set_sub(self, cat) -> None:
        cat = str(cat or "")
        valid = {self._sub_combo.itemData(i) for i in range(self._sub_combo.count())}
        self._sub = cat if cat in valid else ""
        self._sync_views()
        self.filterChanged.emit()

    def _sync_views(self) -> None:
        search = bool(getattr(self, "_search_active", False))
        # 搜索态：筛选被旁路（全库检索）→ 整组禁用 + 不勾选，避免"点了没反应"
        # 还被高亮（R182 用户实报"搜索态忽略大类筛选但 chip 仍高亮"）。
        self._rows.setEnabled(not search)
        for btn in self._top_btns:
            btn.setChecked(not search and btn.property("grp") == self._top)
        for btn in self._sub_btns:
            btn.setChecked(not search and btn.property("grp") == self._sub)
        self._top_combo.blockSignals(True)
        self._sub_combo.blockSignals(True)
        idx = self._top_combo.findData(self._top)
        self._top_combo.setCurrentIndex(idx if idx >= 0 else 0)
        idx = self._sub_combo.findData(self._sub)
        self._sub_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._top_combo.setEnabled(not search)
        self._sub_combo.setEnabled(not search)
        self._top_combo.blockSignals(False)
        self._sub_combo.blockSignals(False)
        getattr(self, "_navigation_sync_hook", lambda: None)()

    def _on_top_combo(self, idx: int) -> None:
        self._set_top(self._top_combo.itemData(idx) if idx >= 0 else "")

    def _on_sub_combo(self, idx: int) -> None:
        self._set_sub(self._sub_combo.itemData(idx) if idx >= 0 else "")

    def _update_compact(self) -> None:
        """兼容入口：紧凑态已废弃（FlowLayout 自动换行），保留空实现供旧调用点。"""
        return
