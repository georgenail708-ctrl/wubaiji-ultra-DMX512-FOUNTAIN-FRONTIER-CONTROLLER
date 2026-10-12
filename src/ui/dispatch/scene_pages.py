# -*- coding: utf-8 -*-
"""★ R183 T7：**场景页（Page）** —— 槽位网格分页（槽位网格分页）。

语义：`EffectCache.page`（0-based；默认 0）把场景分到不同页；存储区只显示当前页，
键绑定**按页生效**（同一键在不同页可绑不同场景 —— 控台执行器页的核心价值）。

数据层改动只有一处：`EffectCache.page` + `to_dict/from_dict`（旧工程缺键 → 0，
真工程 To The Moon 存储区 0 场景 → **零迁移负担**）。本模块负责：
  · 页号 → 该页场景列表（视图输入）；
  · 页切换 UI（顶栏"◀ 1/1 ▶"；当前页存 QSettings，与总控/分界同一套偏好机制）；
  · 菜单动作：移动到页 / 复制到页。

为什么页号存 QSettings 而不是工程：**当前正在看哪一页**是操作员的视图状态
（与"分界在哪"同类），不是节目数据；若存工程，A 机切到第 3 页会通过工程同步
"带偏"B 机的视野（多机协作时是 bug）。
"""
from __future__ import annotations

import logging

from PyQt6.QtWidgets import QInputDialog, QLabel, QPushButton, QWidget

_log = logging.getLogger(__name__)

_QS_ORG, _QS_APP, _QS_KEY = "Qascade", "MainWindow", "dispatch/scene_page"

#: 页号上限（防手滑；100 页 × 常见 4×N 槽位远超任何真实演出用量）
MAX_PAGE = 99


def load_page() -> int:
    """读持久化的当前页（QSettings 不可用 → 0）。"""
    try:
        from PyQt6.QtCore import QSettings
        return max(0, min(MAX_PAGE, int(QSettings(_QS_ORG, _QS_APP).value(_QS_KEY, 0))))
    except Exception:
        return 0


def save_page(page: int) -> None:
    """写持久化当前页（失败静默）。"""
    try:
        from PyQt6.QtCore import QSettings
        QSettings(_QS_ORG, _QS_APP).setValue(_QS_KEY, max(0, min(MAX_PAGE, int(page))))
    except Exception:
        pass


def page_of(cache) -> int:
    """场景的页号（缺字段/坏值 → 0）。"""
    try:
        return max(0, int(getattr(cache, "page", 0) or 0))
    except (TypeError, ValueError):
        return 0


def scenes_on_page(scenes, page: int) -> list:
    """该页的场景（保持传入顺序；页号比较同口径 `page_of`）。"""
    p = max(0, int(page))
    return [c for c in (scenes or []) if page_of(c) == p]


def page_numbers(scenes) -> list:
    """出现过的页号（升序；用于"◀▶ 跨过空页"）。"""
    nums = sorted({page_of(c) for c in (scenes or [])})
    return nums or [0]


def next_page(scenes, page: int, delta: int, only_used: bool = True) -> int:
    """相对翻页：默认只落在**有场景的页**上（空页翻过去只会看到一片空网格）。

    `only_used=False` 时线性翻页（1 页 1 步，可为空页）。
    """
    cur = max(0, int(page))
    if not only_used:
        return max(0, min(MAX_PAGE, cur + int(delta)))
    nums = page_numbers(scenes)
    if int(delta) >= 0:
        for n in nums:
            if n > cur:
                return n
        return nums[-1]
    for n in reversed(nums):
        if n < cur:
            return n
    return nums[0]


# ── 面板动作（模块级；`_install_page_handlers` 挂到面板类）──────────────────
def _set_page(panel, page: int) -> None:
    """切到某页：更新面板当前页 + 重画存储区 + 刷新页标。"""
    p = max(0, min(MAX_PAGE, int(page)))
    panel._scene_page = p
    save_page(p)
    try:
        panel.refresh_saved()
    except Exception:
        _log.debug("切页刷新存储区失败 page=%s", p, exc_info=True)
    _sync_page_label(panel)


def _step_page(panel, delta: int) -> None:
    """相对翻页（◀▶ 按钮）。"""
    from ...processing.dispatch import get_effect_cache_store
    try:
        scenes = get_effect_cache_store().list_caches()
    except Exception:
        scenes = []
    _set_page(panel, next_page(scenes, getattr(panel, "_scene_page", 0), delta))


def _sync_page_label(panel) -> None:
    """页标文案：`第 N 页`（并让 ▶ 在最后一页仍可用 —— 允许用户翻到新空页放场景）。"""
    lbl = getattr(panel, "_page_lbl", None)
    if lbl is None:
        return
    try:
        n = int(getattr(panel, "_scene_page", 0) or 0)
        lbl.setText(f"{n + 1}")
    except Exception:
        _log.debug("页标更新失败", exc_info=True)


def build_page_bar(panel):
    """构建"◀ 页 ▶"小条（挂在存储区标题行右侧）；幂等。"""
    if getattr(panel, "_page_lbl", None) is not None:
        return None
    from PyQt6.QtWidgets import QHBoxLayout, QWidget
    from .. import qascade_theme as _th
    box = QWidget(panel)
    box.setObjectName("dispatchPage")
    box.setStyleSheet(f"#dispatchPage{{background:{_th.BG_DEEP};"
                      f"border:1px solid {_th.BORDER_NORMAL};border-radius:4px;}}")
    lay = QHBoxLayout(box)
    lay.setContentsMargins(2, 0, 2, 0)
    lay.setSpacing(2)
    prev = QPushButton("◀", box)
    nxt = QPushButton("▶", box)
    lbl = QLabel("1", box)
    for b in (prev, nxt):
        b.setFixedSize(18, 18)
        b.setStyleSheet("QPushButton{background:transparent;color:#CCCCCC;"
                        "border:none;font-size:10px;padding:0px;}"
                        "QPushButton:hover{color:#FFFFFF;}")
    lbl.setFixedWidth(20)
    lbl.setStyleSheet(f"color:{_th.TEXT_PRIMARY};font-size:10px;background:transparent;")
    lay.addWidget(prev); lay.addWidget(lbl); lay.addWidget(nxt)
    prev.clicked.connect(lambda: _step_page(panel, -1))
    nxt.clicked.connect(lambda: _step_page(panel, +1))
    panel._page_lbl = lbl
    panel._page_prev = prev
    panel._page_next = nxt
    panel._scene_page = load_page() if getattr(panel, "_scene_page", None) is None \
        else int(panel._scene_page)
    box.setToolTip("场景页：◀▶ 翻页（只落在有场景的页）· 右键卡片可「移动到页 / 复制到页」")
    _sync_page_label(panel)
    return box


def build_header_row(panel, parent) -> QWidget:
    """存储区标题行（标题 + 翻页条）：`_build_right_zone` 的装配段（外提压行数）。

    返回 holder（含底部分割线），标题标签挂 `panel._saved_hdr`，翻页条挂
    `panel._page_lbl/_page_prev/_page_next`（`build_page_bar` 内完成）。
    """
    from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget
    from .. import qascade_theme as _th
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)
    panel._saved_hdr = QLabel("存储区 · 场景（点空格=保存当前效果，点格=调用）", parent)
    panel._saved_hdr.setStyleSheet(
        f"color:{_th.TEXT_SECONDARY};font-size:10px;padding:4px 8px;"
        f"background:{_th.BG_DEEP};border-bottom:1px solid {_th.SEP_LINE};")
    row.addWidget(panel._saved_hdr, 1)
    holder = QWidget(parent)
    holder.setLayout(row)
    holder.setStyleSheet(f"background:{_th.BG_DEEP};"
                         f"border-bottom:1px solid {_th.SEP_LINE};")
    # ★ R183 T7：场景页翻页条（插在标题行右侧）
    try:
        bar = build_page_bar(panel)
        if bar is not None:
            row.addWidget(bar)
    except Exception:
        _log.debug("翻页条装配失败", exc_info=True)
    return holder


def install_page_bar(panel) -> None:
    """把翻页条插进存储区标题行（`_saved_hdr` 所在行）末尾；幂等。"""
    if getattr(panel, "_page_lbl", None) is not None:
        return
    try:
        box = build_page_bar(panel)
        if box is None:
            return
        hdr = getattr(panel, "_saved_hdr", None)
        parent = hdr.parentWidget() if hdr is not None else None
        lay = parent.layout() if parent is not None else None
        if lay is not None:
            lay.addWidget(box)
    except Exception:
        _log.debug("翻页条装配失败", exc_info=True)


# ── 移动到页 / 复制到页（右键菜单动作）────────────────────────────────────
def _ask_page(panel, title: str, default: int) -> int:
    """问页号（1-based 展示，内部 0-based）。取消 → -1。"""
    val, ok = QInputDialog.getInt(panel, title, "目标页（1 起）：",
                                  int(default) + 1, 1, MAX_PAGE + 1, 1)
    return int(val) - 1 if ok else -1


def _move_to_page(panel, cid) -> None:
    """把该场景移到另一页（页内位置不变）。"""
    from ...processing.dispatch import get_effect_cache_store
    store = get_effect_cache_store()
    c = store.get(str(cid))
    if c is None:
        return
    page = _ask_page(panel, "移动到页", page_of(c))
    if page < 0:
        return
    c.page = page
    panel.refresh_saved()
    panel.cacheChanged.emit()
    try:
        panel._status.setText(f"「{c.name}」已移动到第 {page + 1} 页")
    except Exception:
        _log.debug("移动到页状态栏文案失败", exc_info=True)


def _copy_to_page(panel, cid) -> None:
    """复制该场景到另一页（新 id、落该页空槽）。"""
    from ...processing.dispatch import get_effect_cache_store
    store = get_effect_cache_store()
    c = store.get(str(cid))
    if c is None:
        return
    page = _ask_page(panel, "复制到页", page_of(c) + 1)
    if page < 0:
        return
    dup = store.duplicate(str(cid), str(c.name) + " 副本")
    if dup is None:
        return
    dup.page = page
    dup.position = max(0, len(scenes_on_page(store.list_caches(), page)) - 1)
    panel.refresh_saved()
    panel.cacheChanged.emit()
    try:
        panel._status.setText(f"已复制「{c.name}」到第 {page + 1} 页")
    except Exception:
        _log.debug("复制到页状态栏文案失败", exc_info=True)


def _install_page_handlers(cls) -> None:
    """把页动作挂到面板类上（幂等）。"""
    for fn in (_set_page, _step_page, _move_to_page, _copy_to_page):
        setattr(cls, fn.__name__, fn)


def wire_zone_teardown(panel) -> None:
    """装配存储区的两个**应用级**副作用清理：键盘过滤 + 跑灯计时器（幂等）。

    ★ 两者都是"面板删了它们还在"的全局态（深查 A3/M1/L4 与 R183 T6），必须在
    `destroyed` 上摘干净；且必须**捕获对象引用**（destroyed 触发时 self 已析构，
    回调里回查 panel 会 RuntimeError）。从 `dispatch_panel._build_right_zone`
    外提（该文件守 500 行上限）。
    """
    try:
        from functools import partial
        from .scene_key_filter import install_scene_key_filter
        install_scene_key_filter(panel)
        from .store_area_ops import _teardown_captured
        from .chase_runner_ui import _players_of as _chase_players_of
        from .chase_runner_ui import teardown_chase
        # ★ R194/S2-02：_fade_runner 是首次淡化时才懒建的——构造期 getattr
        #   恒 None，destroyed 安全网成死路径。改传可变 cell（懒建点登记实例）。
        _fade_cell = getattr(panel, "_fade_runner_cell", None)
        if _fade_cell is None:
            _fade_cell = [None]
            panel._fade_runner_cell = _fade_cell
        panel.destroyed.connect(partial(
            _teardown_captured,
            getattr(panel, "_scene_key_filter", None),
            _fade_cell))
        # ★ R183 T6：跑灯计时器随面板销毁一并停（否则死面板仍被计时器点亮 =
        #   幽灵出光；与键盘过滤器/层增益同一条 teardown 纪律）。
        panel.destroyed.connect(partial(teardown_chase, _chase_players_of(panel)))
    except Exception:
        _log.debug("存储区 teardown 装配失败", exc_info=True)


__all__ = ["MAX_PAGE", "load_page", "save_page", "page_of", "scenes_on_page",
           "page_numbers", "next_page", "_set_page", "_step_page",
           "_move_to_page", "_copy_to_page", "_install_page_handlers",
           "install_page_bar", "build_page_bar", "build_header_row",
           "wire_zone_teardown"]
