# -*- coding: utf-8 -*-
"""监视器通道组折叠：灯具地址连续段 → 行分组（R183 W2；深查② §四 #6）。

需求原文：「通道组折叠（按灯具分段折叠 512 格）：绘制循环按地址连续段分组
加行组头，点组头折叠；组=灯具（用 project fixtures 的 universe+start_channel
连续段）」。

坐标语义（与 ChannelGrid 对齐）：idx 0-based、ch 1-based，
``row = (ch-1) // cols``、``col = (ch-1) % cols``。
本模块纯逻辑（无 Qt），组表喂 ``ChannelGrid.set_row_groups``。

设备通道数来源：``FixtureDefinition.channel_count``（缺失时回退
``len(def.channels)``，再缺失按 1 通道处理——真工程复核见
ai-memory/_references/deep-sweep-2-ui-panels.md §〇）。
"""
from __future__ import annotations

DMX_CHANNELS = 512          # 单域通道数（监视器视野）
MONITOR_COLS = 32           # ChannelGrid 每行 32 格
MONITOR_ROWS = 16
GROUP_LABEL_LIMIT = 48      # 组头条文本上限（超长截断，防越行）


def fixture_def_index(defs) -> dict:
    """设备定义表 → {def_id: def}；list/dict 两种入参都受理（脏数据跳过）。"""
    if isinstance(defs, dict):
        return {str(k): v for k, v in defs.items() if k is not None}
    out: dict = {}
    for d in defs or ():
        did = str(getattr(d, "id", "") or "")
        if did:
            out[did] = d
    return out


def def_channel_count(definition) -> int:
    """设备定义 → 通道数（channel_count 优先，回退 channels 长度，下限 1）。"""
    if definition is None:
        return 1
    try:
        count = int(getattr(definition, "channel_count", 0) or 0)
    except (TypeError, ValueError):
        count = 0
    if count <= 0:
        count = len(getattr(definition, "channels", None) or [])
    return max(1, count)


def fixture_span(fixture, index: dict) -> tuple[int, int] | None:
    """设备实例 → (起始通道, 结束通道)，1-based 闭区间；越界/无效返回 None。"""
    try:
        start = int(getattr(fixture, "start_channel", 0) or 0)
    except (TypeError, ValueError):
        return None
    if start < 1 or start > DMX_CHANNELS:
        return None
    defn = index.get(str(getattr(fixture, "def_id", "") or ""))
    count = def_channel_count(defn)
    return start, min(DMX_CHANNELS, start + count - 1)


def fixture_short_name(fixture, limit: int = 18) -> str:
    """设备短名（截断；空名回退 'Fixture'）。"""
    name = str(getattr(fixture, "name", "") or "").strip() or "Fixture"
    return name if len(name) <= limit else name[: max(1, limit - 1)] + ">"


def fixture_spans_for_universe(fixtures, defs, universe: int,
                               limit: int = DMX_CHANNELS) -> list:
    """纯函数：该域的灯具段表 [(start, end, name), ...]，按起始通道升序。

    ``limit`` 之外的设备（起址越界）直接过滤。
    """
    index = fixture_def_index(defs)
    out = []
    for fx in fixtures or ():
        try:
            uni = int(getattr(fx, "universe", 0) or 0)
        except (TypeError, ValueError):
            continue
        if uni != int(universe):
            continue
        span = fixture_span(fx, index)
        if span is None or span[0] > limit:
            continue
        out.append((span[0], min(span[1], limit), fixture_short_name(fx)))
    out.sort(key=lambda item: item[0])
    return out


def row_groups_for_universe(fixtures, defs, universe: int,
                            cols: int = MONITOR_COLS,
                            rows: int = MONITOR_ROWS) -> dict:
    """纯函数：灯具段 → {行号: 组头标签}（同一行多灯取起址最小者）。

    标签形如 ``"Front Wash  1-16"``；行号越界不产出（表格只有 16 行）。
    """
    out: dict = {}
    for start, end, name in fixture_spans_for_universe(fixtures, defs, universe):
        row = (start - 1) // cols
        if row < 0 or row >= rows or row in out:
            continue
        label = f"{name}  {start}-{end}"
        out[int(row)] = label[:GROUP_LABEL_LIMIT]
    return out
