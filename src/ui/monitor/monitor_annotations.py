# -*- coding: utf-8 -*-
"""地址标注叠加：格/热图上叠灯具短名或地址（R183 W2；深查② §四 #13）。

需求原文：「地址标注叠加模式（热图/格子叠灯具名/地址）：加"标注"切换档；
半透明」。

数据=``project.fixtures`` 的 universe+start_channel 连续段（与 #6 组折叠
同源，复用 monitor_groups 的段表解析），不新增配接解析逻辑。
输出结构 {row: [(col, span_cols, label), ...]}，绘制侧按 span 居中叠半透明文本。
"""
from __future__ import annotations

from .monitor_groups import (
    DMX_CHANNELS, MONITOR_COLS, MONITOR_ROWS, fixture_spans_for_universe,
)

ANNOTATION_CHAR_PX = 6        # 10px 等宽字近似字宽（截断估算用）
ANNOTATION_MIN_CHARS = 3      # 再窄也要留 3 字符可辨


def fit_label(label: str, span_px: float,
              char_px: float = ANNOTATION_CHAR_PX) -> str:
    """纯函数：按可用像素宽度截断标签（超宽以 '..' 结尾）。"""
    text = str(label or "")
    if not text:
        return ""
    limit = max(ANNOTATION_MIN_CHARS, int(float(span_px) // max(1.0, char_px)))
    if len(text) <= limit:
        return text
    if limit <= 2:
        return text[:limit]
    return text[: limit - 2] + ".."


def annotation_label(start: int, end: int, name: str, universe: int,
                     use_address: bool = False) -> str:
    """标注文本：地址档 ``U1:001``，名称档 ``Front Wash  1-16``。"""
    if use_address:
        return f"U{int(universe)}:{int(start):03d}"
    return f"{name}  {start}-{end}"


def annotation_spans(fixtures, defs, universe: int, *,
                     cols: int = MONITOR_COLS,
                     use_address: bool = False) -> dict:
    """纯函数：灯具段 → {行: [(起始列, 列数, 标签), ...]}（跨行段按行切分）。

    只为该设备**起始行**产标签：跨行大灯（>32 通道）在续行上不再重复叠字，
    避免热图被同一名字刷屏；续行仍返回 (列, 列数, "") 供绘制侧描段边界。
    """
    out: dict = {}
    for start, end, name in fixture_spans_for_universe(fixtures, defs, universe):
        rows = range((start - 1) // cols, (end - 1) // cols + 1)
        for pos, row in enumerate(rows):
            if row >= MONITOR_ROWS:
                break
            lo = max(start, row * cols + 1)
            hi = min(end, (row + 1) * cols, DMX_CHANNELS)
            label = (annotation_label(start, end, name, universe, use_address)
                     if pos == 0 else "")
            out.setdefault(int(row), []).append(
                (int(lo - row * cols - 1), int(hi - lo + 1), label))
    return out
