# -*- coding: utf-8 -*-
"""Draw 画布图结构操作（R182）—— 节点/连线增删与命中测试（纯逻辑，无 Qt）。

图 = {"nodes": [node, ...], "edges": [edge, ...]}（与 engine.audio_mod_draw_compile
同 schema）。本模块只管**画布侧结构**：建卡/连线/删除/命中/世界矩形/内置控件
命中区。图 ↔ 调制状态的编译/反编译在 `draw_compile` / `draw_decompile`
（实现走引擎侧纯函数，UI 与 driver 同源消费）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .draw_cards import (CARD_GAP, DEFAULT_CH_TYPES, RULE_CARD_H, RULE_CARD_W,
                         SOURCE_CARD_H, SOURCE_CARD_W, ch_label, effect_card_node,
                         rule_card_node, source_card_node,
                         type_label as _type_label)

#: 卡片世界尺寸（节点 x/y = 卡**中心**，与 clip_studio 同口径）
CARD_SIZE = {"source": (SOURCE_CARD_W, SOURCE_CARD_H),
             "rule": (RULE_CARD_W, RULE_CARD_H)}
PORT_HIT_TOL = 10.0


def card_size(kind: str) -> tuple:
    return CARD_SIZE.get(str(kind), CARD_SIZE["rule"])


def new_node_id(nodes, prefix: str) -> str:
    """下一个不冲突的节点 id（`<prefix>-<n>`，n 从 1 起，扫已有 id）。"""
    used = {str(n.get("id", "")) for n in (nodes or []) if isinstance(n, dict)}
    i = 1
    while f"{prefix}-{i}" in used:
        i += 1
    return f"{prefix}-{i}"


def add_source(nodes, feature: str, x: float, y: float,
               template: str = "") -> dict:
    """画布追加一张源卡，返回该节点（原地修改 nodes 列表）。"""
    nid = new_node_id(nodes, "src")
    node = source_card_node(nid, feature, (x, y), template=template)
    nodes.append(node)
    return node


def add_rule(nodes, ch_type: str, x: float, y: float) -> dict:
    """画布追加一张效果卡（兼容签名：单通道默认效果；同目标可多卡=叠加）。"""
    nid = new_node_id(nodes, "rule")
    node = rule_card_node(nid, ch_type, (x, y))
    nodes.append(node)
    return node


def add_effect(nodes, effect: str, x: float, y: float, *,
               ch_types=None, params=None) -> dict:
    """画布追加一张效果卡（R182 效果库形态；唯一新构造入口）。"""
    nid = new_node_id(nodes, "fx")
    node = effect_card_node(nid, effect, (x, y), ch_types=ch_types,
                            params=params)
    nodes.append(node)
    return node


def add_edge(edges, src: str, dst: str) -> Optional[dict]:
    """源卡 → 规则卡建边（自连/重边/未知端拒绝；返回新边或 None）。

    同 (from,to) 重复连线直接拒绝（幂等）——一根源连多卡是不同 to，合法。
    """
    s, d = str(src or ""), str(dst or "")
    if not _edge_allowed(edges, s, d):
        return None
    edge = {"id": f"e-{s}-{d}", "from": s, "to": d}
    edges.append(edge)
    return edge


def _edge_allowed(edges, src: str, dst: str) -> bool:
    """端点合法（非空/非自连）且不是已有重边。"""
    if not src or not dst or src == dst:
        return False
    return not any(str(e.get("from", "")) == src and str(e.get("to", "")) == dst
                   for e in (edges or []))


def delete_nodes(graph, nids) -> int:
    """删节点（连带其相连边）；返回删除的节点数（原地修改 graph）。"""
    wanted = {str(n) for n in (nids or []) if str(n)}
    if not wanted:
        return 0
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    before = len(nodes or [])
    graph["nodes"] = [n for n in (nodes or []) if str(n.get("id", "")) not in wanted]
    graph["edges"] = [e for e in (edges or [])
                      if str(e.get("from", "")) not in wanted
                      and str(e.get("to", "")) not in wanted]
    return before - len(graph["nodes"])


def node_by_id(graph, nid: str) -> Optional[dict]:
    """按 id 取节点（无 → None）。"""
    for n in graph.get("nodes") or []:
        if str(n.get("id", "")) == str(nid):
            return n
    return None


def linked_sources(graph, rule_id: str) -> List[str]:
    """规则卡连入的源卡 id 列表（按边序）。"""
    return [str(e.get("from", "")) for e in (graph.get("edges") or [])
            if str(e.get("to", "")) == str(rule_id) and str(e.get("from", ""))]


def linked_rules(graph, src_id: str) -> List[str]:
    """源卡连出的规则卡 id 列表（按边序）。"""
    return [str(e.get("to", "")) for e in (graph.get("edges") or [])
            if str(e.get("from", "")) == str(src_id) and str(e.get("to", ""))]


def _node_size(n) -> tuple:
    return card_size(str(n.get("kind", "")))


def node_rect(node) -> tuple:
    """节点 → (x0, y0, x1, y1) 世界矩形（卡中心 + 尺寸）。"""
    w, h = _node_size(node)
    cx, cy = float(node.get("x", 0.0)), float(node.get("y", 0.0))
    return (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)


def hit_node(graph, wx: float, wy: float) -> Optional[dict]:
    """世界坐标命中节点（后画者在上，倒序扫描）。"""
    for n in reversed(graph.get("nodes") or []):
        x0, y0, x1, y1 = node_rect(n)
        if x0 <= wx <= x1 and y0 <= wy <= y1:
            return n
    return None


def port_pos(node) -> tuple:
    """规则卡入桩（左中）与源卡出桩（右中）世界坐标。"""
    x0, y0, x1, y1 = node_rect(node)
    cy = (y0 + y1) / 2.0
    if str(node.get("kind", "")) == "source":
        return (x1, cy)
    return (x0, cy)


def hit_link_target(graph, wx: float, wy: float) -> Optional[dict]:
    """保留卡体接线，并以现有出桩容差覆盖入桩圆的外半侧。

    QPointF.toPoint四舍五入可能把可见入桩中心映到卡体外；命中应基于
    端口而非只有卡体（对照通用端口命中衰减）。
    """
    body = hit_node(graph, wx, wy)
    if body is not None:
        return body
    for node in reversed(graph.get("nodes") or []):
        if str(node.get("kind", "")) != "rule":
            continue
        px, py = port_pos(node)
        if abs(wx - px) <= PORT_HIT_TOL and abs(wy - py) <= PORT_HIT_TOL:
            return node
    return None


def free_spot(graph, x: float, y: float) -> tuple:
    """自动布点：与现有卡片重叠时沿对角错开（避免叠卡，成熟软件惯例）。"""
    fx, fy = float(x), float(y)
    for _ in range(16):
        if not _overlaps(graph, fx, fy):
            return (fx, fy)
        fx += CARD_GAP * 0.5
        fy += CARD_GAP * 0.35
    return (fx, fy)


def _overlaps(graph, x: float, y: float) -> bool:
    """任意卡片与该点（按规则卡尺寸外扩半格）重叠即 True。"""
    for n in graph.get("nodes") or []:
        w, h = _node_size(n)
        if (abs(float(n.get("x", 0.0)) - x) < (w + RULE_CARD_W) / 2.0
                and abs(float(n.get("y", 0.0)) - y) < (h + RULE_CARD_H) / 2.0):
            return True
    return False


def default_rule_types(scope_channels) -> List[str]:
    """规则卡可选通道类型：scope 识别优先，空 → 目录全量（有序去重）。"""
    out: List[str] = []
    for ct in (scope_channels or []):
        s = str(ct or "")
        if s and s not in out:
            out.append(s)
    if out:
        return out
    return list(DEFAULT_CH_TYPES)


def type_label(ch_type: str) -> str:
    """通道类型显示文案（re-export `draw_cards.type_label`；单一实现点）。"""
    return _type_label(ch_type)


#: 效果卡内置控件命中区（相对卡片左上角的世界偏移；绘制与命中同源）
#: 布局（卡 232×172；R183 修复轮 B2 按 02 规范 4px 栅格重排）：标题带 24 /
#: 波形下拉 16 / 预览 16 / 七滑杆（3×2 + 展开整行；行高 16、行距 4）/ 目标行 16；
#: 内边距 8、双列间距 8（8+104+8+104+8=232）。修前 8px 字滑杆挤在 12px 槽里
#: （用户截图"标签压字"），现行高 16 容 11px 字、左名右值。
RULE_CONTROLS: Dict[str, tuple] = {
    "wave": (8.0, 28.0, 216.0, 16.0),         # 波形下拉（整行）
    "preview": (8.0, 48.0, 216.0, 16.0),      # 波形预览小图（整行）
    "depth": (8.0, 68.0, 104.0, 16.0),        # 深度滑杆
    "freq": (120.0, 68.0, 104.0, 16.0),       # 频率滑杆
    "attack": (8.0, 88.0, 104.0, 16.0),       # 起应滑杆
    "release": (120.0, 88.0, 104.0, 16.0),    # 落应滑杆
    "threshold": (8.0, 108.0, 104.0, 16.0),   # 开阈值滑杆
    "smoothing": (120.0, 108.0, 104.0, 16.0), # 平滑滑杆
    "spread_deg": (8.0, 128.0, 216.0, 16.0),  # 相位展开滑杆（整行）
    "targets": (8.0, 148.0, 216.0, 16.0),     # 目标通道勾选行（chip 区）
}

#: 目标勾选行内 chip 尺寸（绘制与命中同源；5×36 + 4×4 = 196，右端留 +N 徽标位）
TARGET_CHIP_W, TARGET_CHIP_H, TARGET_CHIP_GAP = 36.0, 16.0, 4.0
TARGET_CHIP_X0, TARGET_CHIP_Y0 = 8.0, 148.0
#: +N 溢出徽标位（chips 行右端、卡内——修前画在末 chip 下方会伸出卡底）
OVERFLOW_TAG_X0, OVERFLOW_TAG_X1 = 206.0, 224.0
#: 卡宽 232 内 chips 行最多可见数（i≥5 画出卡外——审计问题6 的硬上限）
MAX_TARGET_CHIPS = 5


def target_chip_rect(node, index: int):
    """目标勾选行第 index 个 chip 的世界矩形（越界/超可见上限 → None）。

    ★ R182 审计修：渲染与命中共用 MAX_TARGET_CHIPS 截断（修前渲染截 8、
    命中无上限 → 画外 chip 成为幽灵命中区）。
    """
    if (str(node.get("kind", "")) != "rule" or index < 0
            or index >= MAX_TARGET_CHIPS):
        return None
    x0, y0, _x1, _y1 = node_rect(node)
    cx = x0 + TARGET_CHIP_X0 + index * (TARGET_CHIP_W + TARGET_CHIP_GAP)
    return (cx, y0 + TARGET_CHIP_Y0, cx + TARGET_CHIP_W,
            y0 + TARGET_CHIP_Y0 + TARGET_CHIP_H)


def overflow_tag_rect(node):
    """+N 溢出徽标的命中矩形（清查③②P3：徽标可见补交互入口）。

    位置 = chips 行右端徽标位（OVERFLOW_TAG_X0..X1，与末 chip 同高；与
    draw_card_skin.paint_overflow_tag 的绘制位置同源）。
    无截断溢出或 chips 不足 → None。
    """
    if str(node.get("kind", "")) != "rule":
        return None
    chips = list(node.get("ch_types") or [])
    if len(chips) <= MAX_TARGET_CHIPS:
        return None
    base = target_chip_rect(node, MAX_TARGET_CHIPS - 1)
    if base is None:
        return None
    x0, _y0, _x1, _y1 = node_rect(node)
    return (x0 + OVERFLOW_TAG_X0, base[1], x0 + OVERFLOW_TAG_X1, base[3])


def chip_at(node, wx: float, wy: float):
    """世界坐标命中目标 chip → 通道类型键（未命中 → None）。"""
    chips = list(node.get("ch_types") or [])[:MAX_TARGET_CHIPS]
    for i, ct in enumerate(chips):
        r = target_chip_rect(node, i)
        if r and r[0] <= wx <= r[2] and r[1] <= wy <= r[3]:
            return str(ct)
    return None


def control_rect(node, key: str) -> Optional[tuple]:
    """控件命中区世界矩形（相对卡左上角偏移 + 卡中心坐标）。"""
    spec = RULE_CONTROLS.get(str(key))
    if spec is None or str(node.get("kind", "")) != "rule":
        return None
    x0, y0, _x1, _y1 = node_rect(node)
    dx, dy, w, h = spec
    return (x0 + dx, y0 + dy, x0 + dx + w, y0 + dy + h)
