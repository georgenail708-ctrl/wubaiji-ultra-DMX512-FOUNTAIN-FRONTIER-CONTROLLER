# -*- coding: utf-8 -*-
"""时间线**缩放域**单一真源：上下限常量 + 钳制 + 预设档换算（纯函数，可无 GUI 单测）。

存在理由（R183 时间线批 · Top10 真 bug）：
  全仓此前有**两套互相矛盾**的缩放上限——`timeline_widget._set_zoom_at` 钳 500，
  而 `timeline_ruler.wheelEvent` / `clip_area._handle_wheel` / `timeline_controller
  .set_zoom` / `timeline_widget.set_zoom_centered` 钳 2000。标尺滚轮明明一路放大到
  2000，经过 `_set_zoom_at` 却被静默折断回 500：`abs(new_zoom - old_zoom) < 1e-6`
  提前 return → **不做锚点换算**（播放指针在屏幕上滑走）= "缩放锚点漂移"。
  修法：上限/下限只在这里定义一次，所有入口调 `clamp_zoom()`；断点也随之上移到
  2000，500 与 2000 的矛盾从根上消失（不是把两边改成同一个字面量）。

预设档（语义："屏幕上显示 1s / 5s / 30s"）由视口宽度换算 px_per_sec，
故 `px_per_sec_for_span()` 需要 `viewport_px`；视口无效（<=0）时返回当前档不变。

★ 已登记的不一致（审计⑦B P3-14）：主窗**键盘/工具栏**缩放入口
（`main_window._adjust_zoom`，非本批文件）自带 `max(10.0, …)` 下限，而本模块
MIN=5.0 —— 即"键盘最多缩到 10px/s，滚轮可到 5px/s"。两者都受上限 2000 约束，
**越界方向一致**（都不会把 px_per_sec 推到不可用），差异只体现在最广视野档；
统一它需要改 `main_window`（上帝类，不在本批域内），故**登记**于此并加钉子
（`tests/test_r183_tl_timeline.py::test_zoom_presets_map_viewport_to_zoom` 钉住
`clamp_zoom` 不会把 9.0 抬到 10.0 —— 域下限就是 5，UI 下限是另一回事）。
"""
from __future__ import annotations

#: 缩放上下限（px/秒）——**本域唯一真源**；新增入口一律用 `clamp_zoom`，勿再写裸字面量。
MIN_PX_PER_SEC = 5.0
MAX_PX_PER_SEC = 2000.0

#: 预设档（展示用标签, 该档在视口内铺满的秒数）
ZOOM_PRESETS = (
    ("1 秒 / 屏", 1.0),
    ("5 秒 / 屏", 5.0),
    ("30 秒 / 屏", 30.0),
    ("2 分钟 / 屏", 120.0),
)


def clamp_zoom(px_per_sec) -> float:
    """把任意输入钳到 [MIN, MAX]；非法输入回落到下限（绝不抛）。"""
    try:
        v = float(px_per_sec)
    except (TypeError, ValueError):
        return MIN_PX_PER_SEC
    if v != v:                      # NaN
        return MIN_PX_PER_SEC
    return max(MIN_PX_PER_SEC, min(MAX_PX_PER_SEC, v))


def px_per_sec_for_span(span_seconds: float, viewport_px: float,
                        fallback: float = 100.0) -> float:
    """让 `span_seconds` 正好铺满 `viewport_px` 宽所需的 px/秒（已钳制）。"""
    try:
        span = float(span_seconds)
        width = float(viewport_px)
    except (TypeError, ValueError):
        return clamp_zoom(fallback)
    if span <= 0.0 or width <= 0.0:
        return clamp_zoom(fallback)
    return clamp_zoom(width / span)


__all__ = ["MIN_PX_PER_SEC", "MAX_PX_PER_SEC", "ZOOM_PRESETS",
           "clamp_zoom", "px_per_sec_for_span"]
