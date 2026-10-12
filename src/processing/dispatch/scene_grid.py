# -*- coding: utf-8 -*-
"""存储页「编号槽位」的**纯布局函数**（无 Qt、无副作用、可单测）。

存在理由（用户需求 2026-09-13）：
  存储区要照 的**场景存储页**做——固定列数的**编号槽位**网格，点空格
  新建、点有场景格调用、拖拽换位。槽位是"位置"语义（第 3 格永远是第 3 格），
  不是"列表顺序"语义；因此需要一层纯函数把"场景的位置值"与"网格下标"互转，
  并把"移动/换位/补位"做成可测的纯逻辑，UI 只负责画与鼠标。

  ★ 为什么单独成模块：EffectCacheStore 是持久化真源（`effect_cache.py`），
    本模块**只算位置算术**，不持有数据、不改 store。这样存储页 UI 与持久化
    解耦——换 UI 不动数据，改数据不动 UI（本项目历史上的"显示逻辑与数据逻辑
    混在一处"是易损点）。

位置语义：`position` 为 0-based 整数。`slot_count()` 保证网格至少铺满
"最大位置 + 1"与一个最小格数，中间空位就是**空槽**（点它=新建）。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

#: 默认最小槽位数（DPENCE 场景页观感的空表也能看出"这是槽位网格"）
DEFAULT_MIN_SLOTS = 12
#: 默认列数（存储区宽度固定时每行几个槽）
DEFAULT_COLS = 4


def slot_count(positions: Iterable[int], *, min_slots: int = DEFAULT_MIN_SLOTS,
               cols: int = DEFAULT_COLS) -> int:
    """网格需要的槽位总数（向上取整到整行，且不小于 `min_slots`）。

    向上取整到整行是刻意的：否则最后一行缺角，编号网格看着会"断"。
    """
    max_pos = -1
    for p in positions or ():
        try:
            max_pos = max(max_pos, int(p))
        except (TypeError, ValueError):
            continue
    needed = max(max_pos + 1, 0)
    cols = max(1, int(cols))
    rows = (needed + cols - 1) // cols if needed else 0
    slots = rows * cols
    return max(int(min_slots), slots)


def index_of_position(position: int) -> int:
    """位置 → 网格下标（当前为恒等，保留函数是为了让"位置≠下标"的将来改动只改一处）。"""
    try:
        return int(position)
    except (TypeError, ValueError):
        return -1


def position_for_index(index: int) -> int:
    """网格下标 → 位置（`index_of_position` 的逆）。"""
    try:
        return int(index)
    except (TypeError, ValueError):
        return -1


def slot_at(positions: Sequence[int], index: int) -> Optional[int]:
    """该下标处**已占用**的位置值；空槽返回 None。"""
    try:
        idx = int(index)
    except (TypeError, ValueError):
        return None
    for p in positions or ():
        try:
            if index_of_position(p) == idx:
                return int(p)
        except (TypeError, ValueError):
            continue
    return None


def move_position(position: int, target: int,
                  positions: Sequence[int]) -> Dict[int, int]:
    """把 `position` 挪到 `target`：返回 `{旧位置: 新位置}` 的**改动表**。

    语义与 场景页一致（拖动交换）：
      · 目标槽为空        → 源槽腾空、目标槽占用（其余不动）
      · 目标槽已被占用    → **两者交换**（源去目标、目标来源）

    返回改动表而非新列表：调用方只需把它套到自己的数据上（store 的每个场景），
    避免本模块依赖任何数据结构。
    """
    src = index_of_position(position)
    dst = index_of_position(target)
    if src < 0 or dst < 0 or src == dst:
        return {}
    occupied = set()
    for p in positions or ():
        try:
            occupied.add(index_of_position(p))
        except (TypeError, ValueError):
            continue
    if dst in occupied:
        return {src: dst, dst: src}      # 交换
    return {src: dst}                     # 挪入空槽


def compact_positions(positions: Sequence[int]) -> Dict[int, int]:
    """把位置压紧成 0..n-1（保持原有相对次序）——删场景后消空槽用。

    返回 `{旧位置: 新位置}`；已是连续则不产生改动（空表）。
    """
    ordered = sorted({int(p) for p in (positions or ()) if _is_int(p)})
    out: Dict[int, int] = {}
    for new_pos, old_pos in enumerate(ordered):
        if old_pos != new_pos:
            out[old_pos] = new_pos
    return out


def uniquify_positions(items) -> None:
    """把一批带 `.position` 的对象就地**位置唯一化**（重复者挪到空槽）。

    ★★ R131 修复（旧工程加载后场景"少了"、点不到）：`position` 是 2026-09-14 才加的
      字段，此前保存的工程条目缺该键 → `EffectCache.from_dict` 一律回落 0，而视图建
      槽位映射是 `by_pos[pos] = item`（后写覆盖）→ 同位置**只有最后一条画得出来、
      也唯一能被 `_hit` 命中**：用户看到"已保存 N 条"，网格里却只有一张卡。

    语义：**首次出现的位置保留原值**（尊重用户手工摆的槽），重复者取下一个空槽。
    刻意不做整体 `compact_positions` —— 那会把用户摆好的布局压掉。
    """
    taken: set = set()
    for c in (items or []):
        try:
            pos = max(0, int(getattr(c, "position", 0) or 0))
        except Exception:
            continue
        if pos in taken:
            pos = int(free_position(taken))
        try:
            c.position = pos
        except Exception:
            continue
        taken.add(pos)


def free_position(taken, want=None) -> int:
    """挑一个**未被占用**的槽位：`want` 为空或已被占用 → 第一个空槽。

    ★ 2026-09-14 深查修正（场景被吞）：显式槽位原先盲信传入值，若该槽已占就会出现
      两个场景同 position；视图建映射 `by_pos[pos]=item`（后写覆盖）→ 先存的那张卡
      **画不出来也点不到**（A@0 + B@0 → 只画出 B，A 从网格消失，但库里还在）。
      位置唯一性在**此处**收口（位置算术的唯一真源），所有入口自动受保护。
    """
    taken_set = {int(t) for t in (taken or [])}
    if want is not None and int(want) not in taken_set:
        return int(want)
    return int(next_free_position(sorted(taken_set)))


def next_free_position(positions: Sequence[int]) -> int:
    """第一个空槽位置（补位新建用）；全满则返回最大+1。"""
    occupied = {int(p) for p in (positions or ()) if _is_int(p)}
    pos = 0
    while pos in occupied:
        pos += 1
    return pos


def _is_int(v) -> bool:
    try:
        int(v)
        return True
    except (TypeError, ValueError):
        return False


__all__ = [
    "DEFAULT_MIN_SLOTS", "DEFAULT_COLS",
    "slot_count", "index_of_position", "position_for_index", "slot_at",
    "move_position", "compact_positions", "uniquify_positions", "next_free_position",
]
