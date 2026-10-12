# -*- coding: utf-8 -*-
"""监视器渲染优化：变化行集合（R183 W2；深查② §四 #15）。

需求原文：「渲染优化：paintEvent 仅重绘变化行（`_dirty_rect_for` 精神复用
clip studio studio_canvas_dirty 先例；diff 行集合→update(rect)）」。

先例：``src/ui/clip_studio/studio_canvas_dirty.py``（拖动会话只 update 脏
矩形，非脏区不重绘）。本模块给出"哪些行变了"的纯判定，几何矩形由
ChannelGrid 按自己的行几何（含 #6 折叠/组头高度）计算——保持本模块无 Qt。
"""
from __future__ import annotations

import numpy as np

DIRTY_FULL_ROWS = 8     # 变化行数 ≥ 此值直接整窗重绘（逐行 update 收益归零）


def changed_rows(old, new, cols: int = 32, rows: int = 16) -> list:
    """纯函数：两帧 → 变化行号升序表。

    - 任一帧为 None / 长度不等 / cols<=0 → 全行（保守整窗）；
    - 逐字节比较（uint8 视窗位等价，与 NM `_frame_bit_equal` 同判据思路）。
    """
    if old is None or new is None or int(cols) <= 0:
        return list(range(max(0, int(rows))))
    a = np.asarray(old).reshape(-1)
    b = np.asarray(new).reshape(-1)
    if a.size != b.size or a.size == 0:
        return list(range(max(0, int(rows))))
    idx = np.flatnonzero(a != b)
    if idx.size == 0:
        return []
    return sorted({int(i) // int(cols) for i in idx})


def needs_full_repaint(rows: list) -> bool:
    """变化行过多（≥ DIRTY_FULL_ROWS）→ 整窗重绘更省。"""
    return len(rows) >= DIRTY_FULL_ROWS
