# -*- coding: utf-8 -*-
"""src/ui/effect_browser_w2_paint.py — R183 W2-FX 卡片附加层（绘制 + 缩略动画）。

深查② §一：
  · #6  实时 A/B 对比槽 —— 卡片左下角 "A"/"B" 槽位徽标；
  · #10 使用计数/热度   —— 卡片左下角 "×N" 计数徽标（次数 <2 不画，避免噪声）；
  · #12 目标通道不兼容  —— 不匹配当前选中设备通道的卡片盖 60% 暗罩（**不隐藏**，
                          与筛选语义区分：筛选是"看不见"，本项是"看得见但不适用"）；
  · #14 卡片缩略动画     —— 可见卡片低频（≥500ms 节流）算 3 相位循环，画三段亮度
                          条；相位值连续变化 → 亮度微呼吸，不做"卡死帧"观感。

★ 全部为 **paint-only + 只读引擎计算**：不写 DMX、不改参数、不动模型。
  播放期/切片期直接跳过计算 —— 性能优先级高于装饰（``_tick_play`` 是 40Hz 热路径）。
★ 本模块不 import ``effect_browser``（顶层）：必须用它时在函数内延迟 import，
  避免 import 环。
"""
from __future__ import annotations

import logging
import time
import weakref
from functools import lru_cache
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QObject, QPoint, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QPainter
from PyQt6.QtWidgets import QStyle

from . import effect_browser_w2_store as _store
from . import qascade_theme as _th

logger = logging.getLogger(__name__)

#: 缩略条单段宽度/高度/段间距（px）
THUMB_SEG_W = 11
THUMB_SEG_H = 5
THUMB_SEG_GAP = 2
#: 每次 tick 最多重算的卡片数（其余沿用上一轮值）
THUMB_PER_TICK = 4
#: 缩略计算的时间预算（毫秒；超预算立即收手，不留卡顿）
THUMB_BUDGET_MS = 45.0
#: 缩略计算参与的设备上限（首 N 台；卡片装饰不值得全量算）
THUMB_FIXTURES = 24
#: 缩略相位（循环周期 2s 的 0/⅓/⅔）
THUMB_PHASES = (0.0, 2.0 / 3.0, 4.0 / 3.0)
#: 暗罩 alpha（60% 不透明度观感）
DIM_ALPHA = 150
#: 卡片条目类型角色（与 effect_browser._ItemTypeRole 一致）
ITEM_TYPE_ROLE = Qt.ItemDataRole.UserRole + 100
#: 缩略刷新定时器周期（≥500ms；#14 的"低频"下限）
THUMB_INTERVAL_MS = 600


# ── 几何 / 取数 ──────────────────────────────────────────────────────────

def card_body(rect):
    """卡片实心区（与 ``paint_effect_card`` 同一几何：``rect.adjusted(3,1,-4,-3)``）。"""
    return rect.adjusted(3, 1, -4, -3)


def _clamp01(value) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _float_or_none(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def preset_id_at(index) -> str:
    """该行对应的预设 id（分组头/空行 → ""）。"""
    if str(index.data(ITEM_TYPE_ROLE) or "") != "item":
        return ""
    return str(index.data(Qt.ItemDataRole.UserRole) or "")


def _badge_origin(rect):
    body = card_body(rect)
    return int(body.left() + 8), int(body.bottom() - 15)


# ── 附加层绘制 ───────────────────────────────────────────────────────────

@lru_cache(maxsize=2)
def _extras_style(ivory=False):
    roles = _th.EDITOR_IVORY if ivory else {}
    overlay = QColor(roles.get("panel", _th.BG_EFFECT_DIM))
    overlay.setAlpha(105 if ivory else DIM_ALPHA)
    badge = QColor(roles.get("accent", _th.SELECT_BLUE))
    badge.setAlpha(230)
    return {"overlay": QBrush(overlay), "badge": QBrush(badge),
            "heat": QColor(roles.get("muted", _th.TEXT_SECONDARY)),
            "ink": QColor(roles.get("ink", _th.TEXT_PRIMARY)),
            "badge_ink": QColor(_th.TEXT_SELECTED_FG)}


def dim_overlay(painter: QPainter, rect, *, ivory=False) -> None:
    """不兼容卡片暗罩（圆角与卡片一致；画在卡片内容之上、徽标之下）。"""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_extras_style(ivory)["overlay"])
    painter.drawRoundedRect(card_body(rect), 4, 4)
    if ivory:
        body = card_body(rect)
        painter.setFont(_th.cached_font(10))
        painter.setPen(_extras_style(True)["ink"])
        painter.drawText(body.left() + 8, body.top() + 78, 78, 14,
                         int(Qt.AlignmentFlag.AlignCenter), "通道不适配")
    painter.restore()


def paint_ab_badge(painter: QPainter, rect, slot: str, *, ivory=False) -> None:
    """A/B 槽位徽标（当前预设的参数来自哪个槽；未入槽不画）。"""
    if slot not in ("A", "B"):
        return
    x, y = _badge_origin(rect)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_extras_style(ivory)["badge"])
    painter.drawRoundedRect(x, y, 15, 13, 3, 3)
    painter.setFont(_th.cached_font(9, bold=True))
    painter.setPen(_extras_style(ivory)["badge_ink"])
    painter.drawText(x, y, 15, 13, int(Qt.AlignmentFlag.AlignCenter), str(slot))
    painter.restore()


def paint_heat_badge(painter: QPainter, rect, count, *, ivory=False) -> None:
    """热度徽标 "×N"（次数 <2 不画：用过一次不值得占位）。"""
    value = _int_or_none(count)
    if value is None or value < 2:
        return
    x, y = _badge_origin(rect)
    painter.save()
    painter.setFont(_th.cached_font(9))
    painter.setPen(_extras_style(ivory)["heat"])
    painter.drawText(x + 19, y, 34, 13,
                     int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                     f"×{value}")
    painter.restore()


def _int_or_none(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _seg_color(value: float, breath: float) -> QColor:
    """一段缩略条的填充色：亮度 → alpha 渐变；``breath`` 为相位呼吸量。"""
    alpha = int(45 + 205 * _clamp01(value) * (0.82 + 0.18 * _clamp01(breath)))
    color = QColor(_th.SELECT_BLUE)
    color.setAlpha(max(30, min(255, alpha)))
    return color


def paint_thumb_strip(painter: QPainter, rect, values, phase: float = 0.0) -> None:
    """三段亮度缩略条（无数据不画）。"""
    if not values:
        return
    body = card_body(rect)
    vals = list(values)[:3]
    x0 = int(body.left() + 62)
    y0 = int(body.bottom() - 12)
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    for i, val in enumerate(vals):
        painter.setBrush(QBrush(_seg_color(val, phase + i * 0.33)))
        painter.drawRect(x0 + i * (THUMB_SEG_W + THUMB_SEG_GAP), y0,
                         THUMB_SEG_W, THUMB_SEG_H)
    painter.restore()


def paint_card_extras(panel, painter: QPainter, rect, index) -> None:
    """delegate 卡片附加层总入口（暗罩 → 徽标 → 缩略条）。

    由 ``effect_browser_w2.make_delegate`` 产出的 delegate 在 ``super().paint``
    之后调用 —— 即在 A 批星标之后绘制。星标在卡片右下角，本层元素在左下角与
    描述行右侧，互不覆盖。
    """
    try:
        pid = preset_id_at(index)
        if not pid:
            return
        ivory = hasattr(panel, "_effect_surface_context")
        if pid in (getattr(panel, "_w2_incompatible", None) or ()):
            dim_overlay(painter, rect, ivory=ivory)
        paint_ab_badge(painter, rect, ab_slot_for(panel, pid), ivory=ivory)
        paint_heat_badge(painter, rect, _store.usage_count(pid), ivory=ivory)
        _paint_thumb_if_enabled(panel, painter, rect, pid)
    except Exception:
        logger.debug("effect_browser_w2: paint extras failed", exc_info=True)


def _paint_thumb_if_enabled(panel, painter, rect, pid: str) -> None:
    """缩略条绘制（开关关闭/无数据 → 不画）。"""
    if not _store.pref("thumb_anim", True):
        return
    values = (getattr(panel, "_w2_thumbs", None) or {}).get(pid)
    phase = float(getattr(panel, "_w2_thumb_cursor", 0) or 0) * 0.37
    paint_thumb_strip(painter, rect, values, phase)


def ab_slot_for(panel, preset_id: str) -> str:
    """该预设当前生效的 A/B 槽（未选中/未入槽 → ""）。"""
    if str(preset_id) != str(getattr(panel, "_selected_id", "") or ""):
        return ""
    return str((getattr(panel, "_w2_ab", None) or {}).get("slot", "") or "")


# ── #14 缩略动画：低频重算（节流 ≥500ms）──────────────────────────────────

def thumb_allowed(panel) -> bool:
    """能否做缩略计算：播放期/切片期一律跳过（性能优先于装饰）。"""
    if getattr(panel, "_is_playing", False):
        return False
    if getattr(panel, "_external_states", None):
        return False
    return not getattr(panel, "_slice_key", None)


def thumb_values_for(panel, preset_id: str) -> Optional[List[float]]:
    """算该预设的 3 相位平均亮度（数据不足/播放期 → None，绝不抛）。"""
    if not preset_id or not thumb_allowed(panel):
        return None
    try:
        ctx = _thumb_context(panel, preset_id)
        if ctx is None:
            return None
        eng, params, fixtures, fdefs, positions, ch_types = ctx
        return [_frames_mean(panel._compute_channel_frames(
            eng, preset_id, float(phase), params, fixtures, fdefs,
            positions, 30.0, "index", [], ch_types)) for phase in THUMB_PHASES]
    except Exception:
        logger.debug("effect_browser_w2: thumb compute failed for %s", preset_id,
                     exc_info=True)
        return None


def _thumb_context(panel, preset_id: str):
    """缩略计算上下文 ``(eng, params, fixtures, fdefs, positions, ch_types)``。"""
    from .effect_engine import get_engine
    from .effect_browser import _fx_fdefs_map
    eng = get_engine()
    preset = eng.get_preset(preset_id)
    fixtures = list(panel._get_active_fixtures() or [])[:THUMB_FIXTURES]
    if preset is None or not fixtures:
        return None
    return (eng, _thumb_params(panel, preset), fixtures,
            _fx_fdefs_map(panel, fixtures), panel._positions_for_fixtures(fixtures),
            [str(c) for c in (getattr(preset, "target_channels", None) or ())])


def _thumb_params(panel, preset) -> dict:
    """缩略用参数 = 预设默认值 + 界面上的三个作用域开关。"""
    params = dict(preset.default_params() or {})
    for key, getter in (("_app_direction", "_get_app_direction"),
                        ("_app_scope", "_get_app_scope"),
                        ("spatial_scope", "_get_spatial_scope")):
        try:
            params[key] = getattr(panel, getter)()
        except Exception:
            logger.debug("effect_browser_w2: thumb scope param %s failed", key)
    return params


def _frames_mean(frames) -> float:
    """一组 (frame, allowed, fixtures) 的平均通道值（0..1）。"""
    total, count = _sum_frames(frames)
    return 0.0 if count <= 0 else _clamp01(total / (count * 255.0))


def _sum_frames(frames) -> tuple:
    """累加全部通道值 → ``(总和, 计数)``（非数值项跳过）。"""
    total = 0.0
    count = 0
    for frame in _frames_of(frames):
        for values in (getattr(frame, "values", None) or ()):
            if not isinstance(values, dict):
                continue
            for val in values.values():
                value = _float_or_none(val)
                if value is not None:
                    total += value
                    count += 1
    return total, count


def _frames_of(frames) -> list:
    """取出帧对象（``(frame, allowed, fixtures)`` 元组的第一项）。"""
    out = []
    for item in (frames or ()):
        try:
            out.append(item[0])
        except (TypeError, IndexError):
            continue
    return out


def visible_item_ids(panel, cap: int = 40) -> List[str]:
    """列表视口内当前可见的预设 id（含滚动位置；隐藏行自然不在内）。"""
    view = getattr(panel, "_preset_list", None)
    if view is None or cap <= 0:
        return []
    bounds = _visible_rows(view)
    if bounds is None:
        return []
    first, last = bounds
    out: List[str] = []
    for row in range(first, last + 1):
        pid = _visible_row_id(view, row)
        if pid and pid not in out:
            out.append(pid)
        if len(out) >= cap:
            break
    return out


def _visible_rows(view):
    """视口覆盖的行号区间 ``(first, last)``；无法判定 → None。"""
    try:
        model = view.model()
        viewport = view.viewport()
        if model is None or viewport is None:
            return None
        width, height = viewport.width(), viewport.height()
        xs = (3, width // 4, width // 2, width * 3 // 4, max(3, width - 4))
        top = _edge_rows(view, xs, 3)
        bottom = _edge_rows(view, xs, max(3, height - 4))
        first = max(0, min(top) - 2) if top else 0
        last = min(max(bottom) + 2, model.rowCount() - 1) if bottom else model.rowCount() - 1
        return first, last
    except Exception:
        logger.debug("effect_browser_w2: visible rows failed", exc_info=True)
        return None


def _edge_rows(view, xs, y):
    return [index.row() for x in xs
            if (index := view.indexAt(QPoint(x, y))).isValid()]


def _visible_row_id(view, row: int) -> str:
    """该行的预设 id（隐藏行/取不到 → ""）。"""
    try:
        if view.isRowHidden(int(row)):
            return ""
        index = view.model().index(int(row), 0)
        if not view.visualRect(index).intersects(view.viewport().rect()):
            return ""
        return preset_id_at(index)
    except Exception:
        return ""


def thumb_tick(panel) -> None:
    """定时器回调：对可见卡片轮转重算缩略（时间预算内，绝不拖慢 UI）。"""
    try:
        if not _store.pref("thumb_anim", True):
            return
        view = getattr(panel, "_preset_list", None)
        if view is None or not view.isVisible():
            return
        ids = visible_item_ids(panel)
        if ids:
            _fill_thumbs(panel, view, ids)
    except Exception:
        logger.debug("effect_browser_w2: thumb tick failed", exc_info=True)


def _fill_thumbs(panel, view, ids) -> None:
    """从游标处轮转补齐缩略缓存（每 tick 限条数 + 限时）。"""
    cache = getattr(panel, "_w2_thumbs", None)
    if not isinstance(cache, dict):
        cache = {}
        panel._w2_thumbs = cache
    cursor = int(getattr(panel, "_w2_thumb_cursor", 0) or 0) % len(ids)
    panel._w2_thumb_cursor = cursor + 1
    deadline = time.perf_counter() + THUMB_BUDGET_MS / 1000.0
    done = 0
    for pid in ids[cursor:] + ids[:cursor]:
        if done >= THUMB_PER_TICK or time.perf_counter() > deadline:
            break
        if pid in cache:
            continue
        cache[pid] = thumb_values_for(panel, pid)
        done += 1
    if done:
        _update_viewport(view)


def _update_viewport(view) -> None:
    try:
        view.viewport().update()
    except Exception:
        logger.debug("effect_browser_w2: thumb viewport update failed")


class _ThumbTicker(QObject):
    """缩略刷新宿主：持面板**弱引用**（绝不因定时器把面板钉在内存里）。

    ★ 为什么不用 ``lambda: thumb_tick(panel)``：闭包捕获的是强引用 —— 面板的
      C++ 对象被 Qt 删掉后，Python 包装体仍被定时器拴住，下一次 tick 就会去碰
      已删除的 C++ 对象（崩溃级，非异常可捕获）。本类以面板为父 QObject：
      面板一销毁宿主随之销毁、定时器自然停；派发前再核对弱引用与 C++ 活性。
    """

    def __init__(self, panel):
        super().__init__(panel)
        self._ref = weakref.ref(panel)
        self._timer = QTimer(self)
        self._timer.setInterval(THUMB_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

    def alive_panel(self):
        """存活且 C++ 对象未被删除的面板；否则 None。"""
        panel = self._ref()
        if panel is None:
            return None
        try:
            panel.objectName()          # 已删 C++ 对象 → RuntimeError
        except RuntimeError:
            return None
        return panel

    def start(self, interval_ms: int) -> None:
        self._timer.setInterval(max(500, int(interval_ms)))
        self._timer.start()

    def _tick(self) -> None:
        panel = self.alive_panel()
        if panel is None:
            self._timer.stop()
            return
        thumb_tick(panel)


def start_thumb_timer(panel, interval_ms: int = THUMB_INTERVAL_MS) -> None:
    """启动缩略刷新定时器（宿主挂面板下 → 面板销毁自动停）；已存在则不重复建。"""
    if getattr(panel, "_w2_thumb_host", None) is not None:
        return
    host = _ThumbTicker(panel)
    host.start(interval_ms)
    panel._w2_thumb_host = host
    panel._w2_thumb_timer = host._timer


def invalidate_thumbs(panel) -> None:
    """清缩略缓存（预设可见集变化/切换预置时调用）。"""
    try:
        panel._w2_thumbs = {}
    except Exception:
        logger.debug("effect_browser_w2: invalidate thumbs failed")


__all__ = [
    "DIM_ALPHA", "ITEM_TYPE_ROLE", "THUMB_INTERVAL_MS", "THUMB_PER_TICK",
    "THUMB_PHASES", "ab_slot_for", "card_body", "dim_overlay",
    "invalidate_thumbs", "paint_ab_badge", "paint_card_extras",
    "paint_heat_badge", "paint_thumb_strip", "preset_id_at",
    "start_thumb_timer", "thumb_allowed", "thumb_tick", "thumb_values_for",
    "visible_item_ids",
]
_ = Any, Dict, QStyle
