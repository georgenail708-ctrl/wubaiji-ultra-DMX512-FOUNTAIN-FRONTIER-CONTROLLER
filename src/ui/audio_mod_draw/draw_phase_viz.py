# -*- coding: utf-8 -*-
"""Draw 相位展开可视化纯函数（成熟化轮⑦）—— 从 draw_canvas_paint 拆出。

为什么单独成模块：draw_canvas_paint 已顶到 500 行红线（硬约束：新增逻辑放
新小模块，paint 里只留调用）。轮⑥画固定 8 个示意点（不知真灯数）；轮⑦升级
为 **真 scope 灯数**（MA3 Change Phase 黄点语义：每灯一个点），count 由
`draw_panel.set_context` 注入（`canvas._scope_fixture_count`，0=未知）。

点位公式与轮⑥逐位同源：`x = left + 2 + spread·i/(360·n)·(轨宽−4)`（i=1..n，
n=8 时 360·8=2880 与轮⑥恒等）；系数 360 复用 `audio_mod_spread.FULL_TURN_DEG`
（同一「整圈度数」常量——驱动侧每灯真相位即 `fixture_phase_offset` 的
spread·i/count 项，本函数是其 spread 项的几何投影）。示意轨**不取模**：
spread=360 时末点钉在轨右缘（轮⑥行为不变），环绕语义由驱动侧求值承担。

性能：paint-only、点数硬上限 32、无缓存需求（每卡 ≤32 次浮点运算）；
不进命中区（`RULE_CONTROLS["spread_deg"]` 命中归滑杆，见 draw_canvas）。
"""
from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter

from ...engine.audio_mod_spread import FULL_TURN_DEG
from ..qascade_theme import BRIGHT_WARN

__all__ = ["PHASE_DOT_CAP", "FALLBACK_DOTS", "dot_count", "phase_dot_xs",
           "phase_fan_dots"]

#: 真灯数点数上限（防刷屏；真灯数超出也只画 32）
PHASE_DOT_CAP = 32
#: count≤0（未知）时的示意点数（轮⑥口径，行为不变）
FALLBACK_DOTS = 8


def dot_count(count: Optional[int]) -> int:
    """count → 实画点数：>0 取 min(count, 32)；≤0/None 回退 8 示意（轮⑥）。"""
    try:
        c = int(count) if count is not None else 0
    except (TypeError, ValueError):
        c = 0
    return min(c, PHASE_DOT_CAP) if c > 0 else FALLBACK_DOTS


def phase_dot_xs(box: QRectF, spread_deg: float, count: Optional[int]) -> List[float]:
    """每灯相位点的 x 坐标表（纯几何；绘制与测试/探针同源、无 Qt 绘制依赖）。"""
    n = dot_count(count)
    return [box.left() + 2.0 + spread_deg * i / (FULL_TURN_DEG * n)
            * (box.width() - 4.0) for i in range(1, n + 1)]


def phase_fan_dots(p: QPainter, box: QRectF, spread_deg: float,
                   count: Optional[int] = None) -> None:
    """spread 轨道每灯相位点（MA3 黄点；轮⑥⑦实现体，paint 只留调用）。"""
    p.setPen(Qt.PenStyle.NoPen)
    warn = QColor(BRIGHT_WARN)
    p.setBrush(QBrush(QColor(warn.red(), warn.green(), warn.blue(), 200)))
    y = box.top() + 2.5
    for x in phase_dot_xs(box, spread_deg, count):
        p.drawEllipse(QPointF(x, y), 1.6, 1.6)
