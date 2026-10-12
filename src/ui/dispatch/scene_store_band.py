# -*- coding: utf-8 -*-
"""存储区**框选 / 多选**逻辑（`SceneStoreView` 的 mixin，拆出以满足门禁 ≤500 行）。

用户 2026-09-14：
  "需要你能够把保存区也支持框选，框选可以一键开启多个保存的 scene，
   也能一键改变多个 scene 的触发模式。"
  "各个保存区的格子之间在选中的时候是排他性的，不能同时选中多个格子。选中一个
   自动释放其它的。"

本模块只管"选中集怎么算"：空白处起框、矩形命中、Shift 范围追加；动作（一键开启/
关闭/改模式）在 `store_handlers`（经面板服务注入），保持零耦合。
"""
from __future__ import annotations

import bisect

from typing import List

from PyQt6.QtCore import QRect

#: 判定"框选 vs 原地单击"的位移阈值（像素）
_BAND_SLOP = 3
#: 滚动到距底部多少像素时续铺一行（与 scene_store_view 同值，避免循环导入）
GROW_MARGIN_PX = 160
#: ★ 滚动续铺的**上限**（额外行数）：防"滚到底就无限加行"导致槽位数/绘制量无界。
MAX_EXTRA_ROWS = 60
#: 槽位边长/间距上界（与 scene_store_view 同值，避免循环导入；`_slice_slots`
#: 的跨界余量按"槽最高 108px"兜底）。
SLOT_MAX = 108
SLOT_GAP = 6


class SceneStoreBandMixin:
    """框选 + 多选（依赖宿主提供的 `_lay_items` / `_selected` / `update()`）。"""

    def _maybe_begin_band(self, pos, shift: bool = False) -> None:
        """从**空槽/空白**处起框选（在**占用**格上按下则走"点格/拖拽换位"）。

        ★★ 2026-09-14 用户实报："存储区的框选不正确，调不出框选控件。"
          真因：全屏几乎都是**空槽**（无限列表），而原实现只要 `_hit(pos)` 命中任何
          槽位（含空槽）就直接 return → **框选永远起不来**。正解：只有命中
          **占用槽**（真的拖动一张场景卡）时才让位给"点格/换位"；空槽照常起框。
        `shift=True` → 框选结果**并入**原有选中（与 Shift 点选同语义）。
        """
        it = self._hit(pos)
        if it is not None and it.get("item") is not None:
            return                     # 命中占用卡 → 让位给点选/拖拽换位
        self._band_origin = pos
        self._band_current = pos
        self._banding = True
        self._band_base = set(self._selected)
        self._band_shift = bool(shift)

    def _band_rect(self):
        """当前框选矩形（未在框选中返回 None）。"""
        if not getattr(self, "_banding", False):
            return None
        return QRect(self._band_origin, self._band_current).normalized()

    def slot_ids_in(self, rect) -> List[str]:
        """落在矩形内的**占用槽**条目 id（框选动作的作用对象）。"""
        out: List[str] = []
        for it in self._lay_items:
            if it["kind"] != "slot" or it.get("item") is None:
                continue
            if rect is None or rect.intersects(it["rect"]):
                out.append(str(getattr(it["item"], "id", "")))
        return out

    def band_selected_ids(self) -> List[str]:
        """框选命中的条目 id（供面板做"一键开启/一键改模式"）。

        · 框选**进行中**：按实时矩形算（拖动时高亮随动）；
        · 框选**已结束**：返回**已提交进选中集**的那批（`_finish_band` 写入）——
          原实现结束后 `_band_rect()` 变 None，这里恒返回空，导致"一键开启"永远拿不到
          对象（深查实测：拖过 3 张卡后 `band_selected_ids()==[]`，功能整体不可用）。
        """
        rect = self._band_rect()
        if rect is not None:
            return self.slot_ids_in(rect)
        # 已结束：用选中集与实际占用槽求交（避免把"后来被删掉的卡"算进来）
        committed = {str(x) for x in (self._selected or ())}
        return [iid for iid in self._occupied_slots().values() if iid in committed]

    def _occupied_slots(self) -> dict:
        """{槽位序号: 条目 id}——仅占用槽（空槽不参与范围选择）。"""
        out = {}
        for it in self._lay_items:
            if it["kind"] != "slot" or it.get("item") is None:
                continue
            out[int(it.get("position", -1))] = str(getattr(it["item"], "id", ""))
        return out

    def _select_range(self, anchor_iid: str, anchor_pos: int) -> None:
        """Shift 点选：把 [上次点击槽位, 本次槽位] 之间的**占用槽**全部加入选中。"""
        try:
            pos_of = self._occupied_slots()
            prev = getattr(self, "_last_click_iid", None)
            start = next((pos for pos, iid in pos_of.items() if iid == prev),
                         anchor_pos)
            lo, hi = min(start, anchor_pos), max(start, anchor_pos)
            self._selected.update(
                iid for pos, iid in pos_of.items() if lo <= pos <= hi)
        except Exception:
            self._selected.add(anchor_iid)


def _finish_band(view, pos) -> None:
    """结束一次框选：位移超过阈值 = **提交框选**；原地松手 = 点空槽新建。

    ★ 2026-09-14 用户实报"框选不正确"：空槽铺满全屏，若按下即发新建、或松开一律
      当框选，两种意图会互相打架。用"有没有拖动"区分，两者都保留。

    ★★ 深查修正（框选"画了但从没选中"）：原实现只画矩形、从不把命中项写进
      `view._selected` → `band_selected_ids()` 恒空，"一键开启/一键改模式"拿不到
      任何对象（功能整体不可用）。现在**把框选结果提交进选中集**（并保持 Shift
      增量语义：带 Shift 时并入 `_band_base`）。
    """
    origin = getattr(view, "_band_origin", None)
    dragged = bool(origin is not None and (
        abs(pos.x() - origin.x()) > _BAND_SLOP or
        abs(pos.y() - origin.y()) > _BAND_SLOP))
    view._banding = False
    view._band_current = pos
    if dragged:
        rect = QRect(origin, pos).normalized() if origin is not None else None
        hit = set(view.slot_ids_in(rect))
        # Shift 起框 = 在原有选中上并入（与 Shift 点选同语义）
        if getattr(view, "_band_shift", False):
            hit |= set(getattr(view, "_band_base", None) or ())
        view._selected = hit
    else:
        it = view._hit(pos)
        if it is not None and it.get("kind") == "slot" and it.get("item") is None:
            view.empty_slot_clicked.emit(int(it.get("position", 0)))
    view.update()


def ensure_growth_for_scroll(view, viewport_bottom: int) -> bool:
    """滚动接近底部时续铺一行空槽（"无限列表"的实现）。返回是否续铺了。

    ★ 为什么不用"真的无限"：无限绘制会无限分配/无限重绘；本实现让空槽数量随滚动
      按需增长，**绘制量始终 ∝ 可见槽数**（性能纪律：复杂度不得 ∝ 场景数）。
    """
    items = getattr(view, "_lay_items", None) or []
    if not items:
        return False
    # ★★ 深查修正（无界生长）：反复滚到底会无限加行 → 槽位数与绘制量无限涨
    #   （实测 205 次滚动 → 1696 槽、minHeight 15498）。加**上限**：
    #   额外行数不超过 MAX_EXTRA_ROWS，避免"无限列表"变成"无限内存"。
    extra = int(getattr(view, "_extra_rows", 0) or 0)
    if extra >= MAX_EXTRA_ROWS:
        return False
    bottom = max((it["rect"].bottom() for it in items
                  if it.get("kind") == "slot"), default=0)
    if viewport_bottom < (bottom - GROW_MARGIN_PX):
        return False
    view._extra_rows = extra + 1
    view._relayout()
    view.update()
    return True


__all__ = ["SceneStoreBandMixin", "_finish_band", "ensure_growth_for_scroll",
           "on_hover_or_band", "_slice_slots"]

def on_hover_or_band(view, pos) -> None:
    """鼠标移动：框选中推进矩形；否则更新悬停高亮。

    · 原 `scene_store_view` 完全没有 mouseMoveEvent → 框选起框后永远不动、
      看起来"调不出框选控件"（用户 2026-09-14 实报）。
    · R194/S2-01：去掉 `or pos != old`（同格内每移一像素就整片重绘 → 1696 槽
      极限态鼠标划过即满屏重画）。只有**悬停身份**变化才重绘，且收窄到
      旧∪新两个槽位矩形（`_narrow_hover_update`，照 table_input 先例）。
      悬停身份 = (条目 id, 槽位序)：空槽之间移动 id 恒为 None，但空槽的"＋"
      标记按槽位画，身份必须含槽位序才算变了。
    """
    old = getattr(view, "_hover_pos", None)
    view._hover_pos = pos
    if getattr(view, "_banding", False):
        view._band_current = pos
        view.update()
        view._hover_pos = old          # 框选中不画悬停"＋"
        return
    it = view._hit(pos)
    hov_id = str(getattr(it.get("item"), "id", "")) if (it and it.get("item")) else None
    hov_slot = int(it.get("position", -1)) if it is not None else None
    if (hov_id != getattr(view, "_hover", None)
            or hov_slot != getattr(view, "_hover_slot", None)):
        _narrow_hover_update(view, it, hov_id, hov_slot)


def _narrow_hover_update(view, it, hov_id, hov_slot) -> None:
    """悬停身份变化：只 update 旧∪新两个槽位矩形（照 table_input.py:148 先例）。"""
    old_rect = getattr(view, "_hover_rect", None)
    new_rect = it["rect"] if it is not None else None
    view._hover = hov_id
    view._hover_slot = hov_slot
    view._hover_rect = new_rect
    if old_rect is not None:
        view.update(old_rect)
    if new_rect is not None:
        view.update(new_rect)
    if old_rect is None and new_rect is None:
        view.update()                  # 兜底：拿不到矩形（进出空白）才整片


def _slice_slots(items, vis):
    """对 **y 递增**的槽位布局项做 bisect 切片，返回与 `vis` 相交的段。

    R194/S2-01（照 `table_view._slice_items` 先例）：绘制成本必须 ∝ 可视槽数，
    不 ∝ 总槽数（滚动续铺极限态可到 1696+ 槽）。`tops` 缓存挂在列表身份上 ——
    `_relayout` 每次整体替换 `_lay_items`，身份一变即重建。
    """
    n = len(items)
    if n == 0:
        return ()
    tops = _slice_slots._tops
    if tops is None or len(tops) != n or _slice_slots._src is not items:
        tops = [it["rect"].top() for it in items]
        _slice_slots._tops = tops
        _slice_slots._src = items
    # 槽位是方形且边长 ≤ SLOT_MAX：top 低于 vis.top()-SLOT_MAX-GAP 的项
    # 必整体在视区上方之外（下边跨界由该余量兜住），无需逐项回溯。
    i0 = bisect.bisect_left(tops, vis.top() - SLOT_MAX - SLOT_GAP)
    i1 = bisect.bisect_right(tops, vis.bottom())
    return items[max(0, i0):i1]


_slice_slots._tops = None
_slice_slots._src = None

