# -*- coding: utf-8 -*-
"""监视器键盘导航增强（R183 W2；深查② §四 #12）。

需求原文：「键盘导航增强：方向键移动游标、Shift 扩选、Home/End
（`_pos_to_channel` 坐标换算复用）」。

坐标语义与 ChannelGrid 一致：ch 1-based、``row=(ch-1)//cols``。
折叠行不可落点（与 #6 折叠语义一致：折叠行不参与命中/框选）。
纯逻辑无 Qt → 可直测。
"""
from __future__ import annotations

DMX_CHANNELS = 512
NAV_COLS = 32
NAV_ROWS = 16

# Qt 键名 → 通道增量语义（up/down 用 ±cols，左右 ±1）
_STEP = {"left": -1, "right": 1, "up": -1, "down": 1}


def _clamp(ch: int) -> int:
    return max(1, min(DMX_CHANNELS, int(ch)))


def row_of(ch: int, cols: int = NAV_COLS) -> int:
    """通道号 → 行号（1-based 通道；越界钳到合法范围）。"""
    return (_clamp(ch) - 1) // max(1, cols)


def next_channel(cur: int, key: str, *, cols: int = NAV_COLS,
                 rows: int = NAV_ROWS, collapsed=frozenset()) -> int:
    """纯函数：当前游标 + 方向键 → 新游标（折叠行跳过，不落点）。

    上下移动逐行推进，撞到折叠行继续同方向找下一可见行；到边界停住
    （返回原值而不回绕——现场操作"按了没动"比"跳到对面"安全）。
    """
    step = _STEP.get(str(key).lower())
    if step is None:
        return _clamp(cur)
    if key.lower() in ("left", "right"):
        return _clamp(int(cur) + step)
    row, col = row_of(cur, cols), (int(cur) - 1) % max(1, cols)
    for _ in range(max(1, rows)):
        row += step
        if row < 0 or row >= rows:
            return _clamp(cur)          # 越界：不动
        if row not in collapsed:
            return _clamp(row * cols + col + 1)
    return _clamp(cur)


def home_end(cur: int, key: str, *, cols: int = NAV_COLS,
             ctrl: bool = False) -> int:
    """Home/End：默认行首/行尾，Ctrl 时全域首/全域尾。"""
    if ctrl:
        return 1 if str(key).lower() == "home" else DMX_CHANNELS
    row = row_of(cur, cols)
    if str(key).lower() == "home":
        return _clamp(row * cols + 1)
    return _clamp(min(DMX_CHANNELS, row * cols + cols))


def extend_selection(selection, anchor: int, target: int) -> set:
    """Shift 扩选：锚点→目标闭区间并入原选择集（不丢弃既有选择）。"""
    lo, hi = sorted((_clamp(anchor), _clamp(target)))
    return set(selection or ()) | set(range(lo, hi + 1))
