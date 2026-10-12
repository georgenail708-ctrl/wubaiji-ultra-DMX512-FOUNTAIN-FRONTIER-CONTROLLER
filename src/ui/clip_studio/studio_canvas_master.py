# -*- coding: utf-8 -*-
"""Clip Studio 画布上的**主控过渡卡内嵌控件**交互混入（R182）—— 从 studio_canvas_interact
拆出（≤500 行门禁）。

画布自绘控件（类型标签 / 曲线下拉 / 比例滑杆 / ＋加行 / ×删行）谁响应什么：

* 滑杆：按下 → `_ratio_drag` 进入拖动，移动即写行比例（两向同值），松手发
  `graph_changed` 一次（面板 300ms 防抖应用，与挪卡/连线同一条回路）；
* 曲线下拉：点击即整体换档（曲线在引擎里是 clip 级，见 `studio_master` docstring）；
* ＋/×：点击即增删行，立即提交。

几何与参数读写全部委托 `studio_master`（绘制/命中同源，两个模块不各写一套）。
宿主（ClipStudioCanvas）需提供：`_node_at/_card_rect/_header_h/_px/_s2w/_world_to_screen/
update/graph_changed` 与属性 `_ratio_drag`。
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF

from src.ui.clip_studio import studio_master

__all__ = ["MasterControlMixin"]

_RATIO_REDRAW_PAD = 6      # 滑杆拖动重绘外扩（屏幕像素）


class MasterControlMixin:
    """主控过渡卡内嵌控件的按下/拖动/松手（宿主 = ClipStudioCanvas）。"""

    def _press_master_control(self, wp: QPointF) -> bool:
        """按下命中内嵌控件 → True（命中即 accept，不进入卡片拖动/连线通道）。"""
        found = self._master_hit(wp)
        if found is None:
            return False
        kind, index, node = found
        if kind == "ratio":
            self._ratio_drag = {"node": node, "index": index, "changed": False}
            self._drag_ratio_to(wp)
            return True
        if studio_master.press_control(kind, index, node):
            self.graph_changed.emit()
            self.update()
        return True

    def _master_hit(self, wp: QPointF):
        """命中主控过渡卡内嵌控件 → (kind, index, node)；未命中 → None。"""
        node = self._node_at(wp)
        if node is None:
            return None
        rect = self._card_rect(node)
        found = studio_master.hit_node(wp, rect, self._header_h(rect), node, self._px(1.0))
        return None if found is None else (found[0], found[1], node)

    def _drag_ratio_to(self, wp: QPointF) -> None:
        """滑杆拖动一帧：按 x 位置写行比例并按卡重绘（几何 = 绘制同源）。"""
        drag = self._ratio_drag
        node, index = drag["node"], int(drag["index"])
        rect = self._card_rect(node)
        slider = studio_master.slider_rect_at(node, rect, self._header_h(rect),
                                              index, self._px(1.0))
        if slider.isEmpty():
            return
        if studio_master.drag_ratio(node, index, wp, slider):
            drag["changed"] = True        # 真改过才在松手时提交（防噪声撤销）
        tl = self._world_to_screen(rect.topLeft())
        br = self._world_to_screen(rect.bottomRight())
        pad = _RATIO_REDRAW_PAD
        self.update(QRectF(tl, br).normalized().toAlignedRect().adjusted(-pad, -pad, pad, pad))


# ── 主控卡预填 helpers（自 studio_master 迁入压行数门禁）────────────────────
def existing_rows(clip, types) -> list:
    """clip 已存比例 → 行表（无该类型回落 100=RATIO_NEUTRAL）。"""
    from .studio_reverse import _ratio_rows
    from .studio_master import RATIO_NEUTRAL, _CURVE_VALUES  # noqa: F401
    try:
        from .studio_reverse import _ratio_rows as _rr
        existing = {r["type"]: r for r in _rr(clip)}
    except Exception:
        existing = {}
    from .studio_master import RATIO_NEUTRAL as RN
    rows = []
    for ctype in (types or ()):
        got = existing.get(str(ctype))
        rows.append({"type": ctype,
                     "ratio_in": got["ratio_in"] if got else RN,
                     "ratio_out": got["ratio_out"] if got else RN})
    return rows


def prefill_curves(node, clip) -> None:
    """曲线预填：clip 已有过渡曲线 → 读进卡 params（编译恒写即写现值）。"""
    from .studio_master import _CURVE_VALUES
    try:
        cur_in = str(getattr(clip, "transition_curve_in", "") or "")
        cur_out = str(getattr(clip, "transition_curve_out", "") or "")
        params = node.setdefault("params", {})
        if cur_in in _CURVE_VALUES:
            params.setdefault("curve_in", cur_in)
        if cur_out in _CURVE_VALUES:
            params.setdefault("curve_out", cur_out)
    except Exception:
        pass
