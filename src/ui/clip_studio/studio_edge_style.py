# -*- coding: utf-8 -*-
"""连线样式纯函数（R183 W2-ST #4：贝塞尔 / 正交折线 / 直线）—— 无状态、无自绘上下文。

**为什么不用现成**：现成节点库（MIT）的 `pipe` / `pipe_with_arrows` 布局是库内
`NodeGraphNodeLayout` + QGraphicsItem 体系的一环（其 0.6.44 为 PySide 实现，与本仓
PyQt6 混用有风险），本仓既定纪律是「只搬语义不引库」（`studio_theme_nodes` /
`studio_frames` 同）；语义出处
（起/终桩切线方向 + 水平 S 曲线公式）。三种样式的段生成全部走本模块 —— 绘制、
命中测试（`_edge_at`）、拖线预览、脏区共用同一公式，防两道几何漂移。

* ``bezier``（默认）：R182 定稿的水平切线 S 曲线（标准同式）；
  复合桩改纵向切线变体。旧档无 ``edge_style`` 键 → 默认本样式 = **逐字节旧行为**。
* ``ortho``：正交折线（两处直角弯的 mid 路由；复合桩改纵向 mid 路由）。
* ``line``：直线（强制走直线；密集图核对用）。

样式存 sheet：``sheet["edge_style"]``（可选键；`studio_graph.normalize_state` 归一
时保留合法值，旧档不写键）。
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QPainterPath

from src.ui.clip_studio.studio_cards import PORT_COMPOSITE, PORT_IN, PORT_OUT

__all__ = [
    "EDGE_BEZIER", "EDGE_ORTHO", "EDGE_LINE", "EDGE_STYLES", "EDGE_STYLE_LABELS",
    "DEFAULT_EDGE_STYLE", "edge_style_of", "set_edge_style", "cycle_edge_style",
    "seg_path", "seg_bezier", "seg_ortho", "seg_straight",
]

EDGE_BEZIER = "bezier"
EDGE_ORTHO = "ortho"
EDGE_LINE = "line"
#: 合法样式（顺序 = 工具栏三态循环顺序）。
EDGE_STYLES = (EDGE_BEZIER, EDGE_ORTHO, EDGE_LINE)
#: 三态菜单 (值, 中文标签)。
EDGE_STYLE_LABELS = ((EDGE_BEZIER, "曲线"), (EDGE_ORTHO, "直角"), (EDGE_LINE, "直线"))
#: 默认样式（旧档/坏值回退 —— 与 R182 贝塞尔逐字节一致）。
DEFAULT_EDGE_STYLE = EDGE_BEZIER
_STYLES = frozenset(EDGE_STYLES)

_LIMIT_W = 1.2     # S 曲线控制点水平偏移限幅（× CARD_W，R182 定稿）
_LIMIT_H = 1.2     # 复合桩纵向切线偏移限幅（× CARD_H，R182 定稿）


def edge_style_of(sheet) -> str:
    """sheet 的连线样式（缺失/非 dict/非法值 → 贝塞尔 = 旧档旧行为）。"""
    style = ""
    if isinstance(sheet, dict):
        style = str(sheet.get("edge_style") or "")
    return style if style in _STYLES else DEFAULT_EDGE_STYLE


def set_edge_style(sheet, style) -> bool:
    """写入合法样式（非法值忽略）；真变化才返回 True（防噪声落盘/撤销）。"""
    if not isinstance(sheet, dict) or style not in _STYLES:
        return False
    if sheet.get("edge_style") == style:
        return False
    sheet["edge_style"] = str(style)
    return True


def cycle_edge_style(style) -> str:
    """三态循环：曲线 → 直角 → 直线 → 曲线（工具栏单钮切换用）。"""
    idx = EDGE_STYLES.index(style) if style in _STYLES else 0
    return EDGE_STYLES[(idx + 1) % len(EDGE_STYLES)]


def _card_limits() -> tuple:
    """卡尺寸来自 `studio_canvas` 唯一真源（函数内 import 断模块级循环依赖）。"""
    from src.ui.clip_studio.studio_canvas import CARD_H, CARD_W
    return CARD_W * _LIMIT_W, CARD_H * _LIMIT_H


def seg_bezier(p1: QPointF, p2: QPointF,
               from_port: str = PORT_OUT,
               to_port: str = PORT_IN) -> QPainterPath:
    """三次贝塞尔：标准节点编辑器**水平切线 S 曲线**（标准同式）。

    `dx = |p2.x-p1.x|; off = min(dx*0.5, CARD_W*1.2)`，cp1/cp2 与各自端点**同 y**
    → 曲线恒不越出两端 y 区间（旧「弦法向 0.22·dist」弓形会让反向边鼓过卡顶、
    纵向边甩到卡侧）。复合桩（底边中点）改**纵向切线**变体
    `off_v = min(|dy|*0.5, CARD_H*1.2)`：起点向下、终点自下而上（S 形收束）。
    `_edge_at` 命中测试与绘制/脏区共用本函数（同一公式，防两道几何漂移）。
    """
    path = QPainterPath(p1)
    dx, dy = abs(p2.x() - p1.x()), abs(p2.y() - p1.y())
    if dx < 1.0 and dy < 1.0:
        path.lineTo(p2)
        return path
    lim_w, lim_h = _card_limits()
    off_h = min(dx * 0.5, lim_w)
    off_v = min(dy * 0.5, lim_h)
    cp1 = (QPointF(p1.x(), p1.y() + off_v) if from_port == PORT_COMPOSITE
           else QPointF(p1.x() + off_h, p1.y()))
    cp2 = (QPointF(p2.x(), p2.y() - off_v) if to_port == PORT_COMPOSITE
           else QPointF(p2.x() - off_h, p2.y()))
    path.cubicTo(cp1, cp2, p2)
    return path


def seg_ortho(p1: QPointF, p2: QPointF,
              from_port: str = PORT_OUT,
              to_port: str = PORT_IN) -> QPainterPath:
    """正交折线：两处直角弯的 mid 路由（起/终桩水平进出，复合桩改纵向 mid 路由）。

    退化（|dx|<1 或 |dy|<1，端点已正交相邻）→ 单段直线；mid 路由恒在两端点 bbox
    内 → 命中/脏区沿用贝塞尔的保守余量公式（`studio_canvas_dirty`）。
    """
    path = QPainterPath(p1)
    dx, dy = abs(p2.x() - p1.x()), abs(p2.y() - p1.y())
    if dx < 1.0 or dy < 1.0:
        path.lineTo(p2)
        return path
    if from_port == PORT_COMPOSITE or to_port == PORT_COMPOSITE:
        my = (p1.y() + p2.y()) * 0.5
        path.lineTo(QPointF(p1.x(), my))
        path.lineTo(QPointF(p2.x(), my))
    else:
        mx = (p1.x() + p2.x()) * 0.5
        path.lineTo(QPointF(mx, p1.y()))
        path.lineTo(QPointF(mx, p2.y()))
    path.lineTo(p2)
    return path


def seg_straight(p1: QPointF, p2: QPointF,
                 from_port: str = PORT_OUT,
                 to_port: str = PORT_IN) -> QPainterPath:
    """直线（样式三态之一；端口参数仅为统一签名保留）。"""
    path = QPainterPath(p1)
    path.lineTo(p2)
    return path


#: 样式 → 段生成函数（未知值回退贝塞尔）。
_STYLE_FNS = {EDGE_BEZIER: seg_bezier, EDGE_ORTHO: seg_ortho, EDGE_LINE: seg_straight}


def seg_path(p1: QPointF, p2: QPointF, from_port: str = PORT_OUT,
             to_port: str = PORT_IN,
             edge_style: str = DEFAULT_EDGE_STYLE) -> QPainterPath:
    """按样式生成连线路径（绘制/命中/预览唯一入口）。"""
    return _STYLE_FNS.get(edge_style, seg_bezier)(p1, p2, from_port, to_port)
