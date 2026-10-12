# -*- coding: utf-8 -*-
"""Patch 配接守卫（R183/F2 · 深查③ P2/P3/P4）：冲突检测 / 自动地址 / 重编号。

纯逻辑模块（只依赖 `core.models` 的类型注解语义，不 import Qt）：这样
`tests/test_r183_tv_patch.py` 可以直接对几何与地址算法做 headless 单测。

三个能力：
  * **P2 冲突检测**：同一宇宙内 [start, start+channel_count-1] 区间两两重叠 →
    `find_conflicts`；调色板/表格用它做红底高亮与"将要被移除的旧设备"清单。
    不改写入语义：`patch_panel._do_patch` 仍然自动去重叠，但冲突详情**显式
    列出来**，不再静默（原行为只在成功框里加一句"已自动移除 N 台"）。
  * **P3 自动地址建议**：`suggest_start_channel` 在目标宇宙里找**能容纳 N 台的
    连续空位**（不是"最大占用末端+1"就够——那样会跳过前面的空洞）；
    无空洞则给最大占用末端 + 1（溢出 512 时返回 None，由调用方提示换域）。
  * **P4 重编号**：`renumber_plan` 对给定顺序逐台按各自通道数推进，跨 512 自动
    进位到下一宇宙（真工程 1356 台 / 25 域，跨域进位是常态而非边缘情况）。

真工程实测（`../To The Moon项目.dmxt`）：1356 台 / 3 类型（HJC 二维 8ch、
LED 灯组 3ch、调压气爆 19ch）/ 25 域 / **0 地址重叠** / 最大末端恰为 512 ——
故冲突检测在真工程上应为空集（本模块的判据必须能复现这个 0）。
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

UNIVERSE_MAX_CHANNEL = 512
UNIVERSE_MIN = 1
UNIVERSE_MAX = 32767


# ── 基础几何 ──────────────────────────────────────────────────────────────

def channel_count_of(fi, defs_by_id: Dict[str, object], default: int = 1) -> int:
    """设备实例的通道数（按 def_id 查类型；查不到按 default，至少 1）。"""
    fdef = defs_by_id.get(str(getattr(fi, "def_id", "") or ""))
    try:
        n = int(getattr(fdef, "channel_count", 0) or 0)
    except Exception:
        n = 0
    return n if n > 0 else int(default)


def span_of(fi, defs_by_id: Dict[str, object]) -> Optional[Tuple[int, int, int]]:
    """(universe, start_channel, end_channel)；字段非法返回 None。"""
    try:
        universe = int(getattr(fi, "universe", 1) or 1)
        start = int(getattr(fi, "start_channel", 1) or 1)
    except Exception:
        return None
    if universe < UNIVERSE_MIN or start < 1:
        return None
    return universe, start, start + channel_count_of(fi, defs_by_id) - 1


def ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """闭区间重叠判定（端点相接不算重叠：CH1-4 与 CH5-8 合法相邻）。"""
    return not (a_end < b_start or a_start > b_end)


def _defs_by_id(project) -> Dict[str, object]:
    defs = getattr(project, "fixture_defs", None) or []
    return {str(getattr(d, "id", "")): d for d in defs}


def normalize_ranges(start_channel: int, channel_count: int, count: int) -> List[Tuple[int, int]]:
    """批量配接的占用区间（与 `_do_patch` 原实现同式）。"""
    out: List[Tuple[int, int]] = []
    for i in range(max(0, int(count))):
        s = int(start_channel) + i * int(channel_count)
        out.append((s, s + int(channel_count) - 1))
    return out


# ── P2 冲突检测 ───────────────────────────────────────────────────────────

def find_conflicts(fixtures: Sequence, defs_by_id: Dict[str, object],
                   universes: Optional[Iterable[int]] = None,
                   ) -> List[dict]:
    """找出同宇宙内地址区间两两重叠的设备对。

    返回记录列表（稳定排序：宇宙、起始通道、设备 id）：
      `{"universe", "a":{"id","name","start","end"}, "b":{...},
        "overlap_start", "overlap_end"}`
    """
    want = None if universes is None else {int(u) for u in universes}
    buckets = _bucket_by_universe(fixtures, defs_by_id, want)
    out: List[dict] = []
    for universe in sorted(buckets):
        out.extend(_pairs_in_universe(universe, buckets[universe]))
    return out


def _bucket_by_universe(fixtures: Sequence, defs_by_id: Dict[str, object],
                        want: Optional[set]) -> Dict[int, List[tuple]]:
    """设备按宇宙分桶：(start, end, fixture)；地址非法/不在目标域内则跳过。"""
    buckets: Dict[int, List[tuple]] = {}
    for fi in fixtures or []:
        span = span_of(fi, defs_by_id)
        if span is None:
            continue
        universe, start, end = span
        if want is not None and universe not in want:
            continue
        buckets.setdefault(universe, []).append((start, end, fi))
    return buckets


def _pairs_in_universe(universe: int, rows: Sequence[tuple]) -> List[dict]:
    """单个宇宙内的重叠对（按 start 排序后一旦越出 prev.end 即停）。"""
    ordered = sorted(rows, key=lambda t: (t[0], t[1], str(getattr(t[2], "id", ""))))
    out: List[dict] = []
    for i, (s1, e1, f1) in enumerate(ordered):
        for s2, e2, f2 in ordered[i + 1:]:
            if s2 > e1:
                break
            if not ranges_overlap(s1, e1, s2, e2):
                continue
            out.append({
                "universe": universe,
                "a": _row(f1, s1, e1), "b": _row(f2, s2, e2),
                "overlap_start": max(s1, s2), "overlap_end": min(e1, e2),
            })
    return out


def _row(fi, start: int, end: int) -> dict:
    return {"id": str(getattr(fi, "id", "")), "name": str(getattr(fi, "name", "")),
            "start": int(start), "end": int(end)}


def conflict_ids(conflicts: Sequence[dict]) -> set:
    """冲突对涉及的全部设备 id（表格红底高亮用）。"""
    out = set()
    for c in conflicts:
        out.add(str(c["a"]["id"]))
        out.add(str(c["b"]["id"]))
    return out


def conflict_text(conflicts: Sequence[dict], limit: int = 12) -> str:
    """冲突详情多行文本（对话框 / tooltip 共用）。"""
    if not conflicts:
        return "无地址冲突"
    lines = []
    for c in conflicts[:limit]:
        lines.append(
            f"· 宇宙 {c['universe']}　重叠 CH{c['overlap_start']}–CH{c['overlap_end']}\n"
            f"    {c['a']['name'] or c['a']['id']} (CH{c['a']['start']}–CH{c['a']['end']})\n"
            f"    {c['b']['name'] or c['b']['id']} (CH{c['b']['start']}–CH{c['b']['end']})")
    if len(conflicts) > limit:
        lines.append(f"… 另有 {len(conflicts) - limit} 对未列出")
    return "\n".join(lines)


def overlapping_fixtures(fixtures: Sequence, defs_by_id: Dict[str, object],
                         universe: int, new_ranges: Sequence[Tuple[int, int]],
                         ) -> List[object]:
    """将要被新配接覆盖的旧设备（`_do_patch` 的去重叠判据，原样抽出以便复用）。"""
    hit: List[object] = []
    for old in fixtures or []:
        span = span_of(old, defs_by_id)
        if span is None or span[0] != int(universe):
            continue
        if any(ranges_overlap(span[1], span[2], ns, ne) for ns, ne in new_ranges):
            hit.append(old)
    return hit


def fixtures_text(fixtures: Sequence, defs_by_id: Dict[str, object], limit: int = 12) -> str:
    """设备清单文本（名字 + 地址区间）。"""
    lines = []
    items = list(fixtures or [])
    for fi in items[:limit]:
        span = span_of(fi, defs_by_id)
        addr = f"宇宙 {span[0]} CH{span[1]}–CH{span[2]}" if span else "地址未知"
        lines.append(f"· {getattr(fi, 'name', '') or getattr(fi, 'id', '')}　{addr}")
    if len(items) > limit:
        lines.append(f"… 另有 {len(items) - limit} 台未列出")
    return "\n".join(lines)


# ── P3 自动地址建议 ───────────────────────────────────────────────────────

def used_ranges(fixtures: Sequence, defs_by_id: Dict[str, object], universe: int,
                exclude: Iterable[str] = ()) -> List[Tuple[int, int]]:
    """目标宇宙已占用区间（可排除指定设备 id，用于"重排自己"的场景）。"""
    skip = {str(x) for x in exclude}
    out: List[Tuple[int, int]] = []
    for fi in fixtures or []:
        if str(getattr(fi, "id", "")) in skip:
            continue
        span = span_of(fi, defs_by_id)
        if span is None or span[0] != int(universe):
            continue
        out.append((span[1], span[2]))
    out.sort()
    return out


def suggest_start_channel(fixtures: Sequence, defs_by_id: Dict[str, object],
                          universe: int, channel_count: int, *, count: int = 1,
                          exclude: Iterable[str] = ()) -> Optional[int]:
    """在 `universe` 里为 `count` 台 × `channel_count` 通道找**连续空位**起始通道。

    找不到足够大的空洞时回落"最大占用末端 + 1"；仍越界（>512）返回 None
    （调用方据此提示"该域已满，请换域"）。
    """
    need = max(1, int(channel_count)) * max(1, int(count))
    if need > UNIVERSE_MAX_CHANNEL:
        return None
    ranges = used_ranges(fixtures, defs_by_id, universe, exclude)
    cursor = 1
    for start, end in ranges:
        if end < cursor:
            continue
        if start - cursor >= need:
            return cursor
        cursor = max(cursor, end + 1)
    return cursor if cursor + need - 1 <= UNIVERSE_MAX_CHANNEL else None


def universe_usage(fixtures: Sequence, defs_by_id: Dict[str, object],
                   universe: int) -> Tuple[int, int]:
    """(已用通道数, 占用率百分比)——用于域页签/表格摘要。"""
    ranges = used_ranges(fixtures, defs_by_id, universe)
    used = sum(max(0, e - s + 1) for s, e in ranges)
    return used, int(round(used * 100.0 / UNIVERSE_MAX_CHANNEL))


# ── P4 重编号 ─────────────────────────────────────────────────────────────

def renumber_plan(order: Sequence, start_universe: int, start_channel: int,
                  defs_by_id: Dict[str, object],
                  ) -> List[dict]:
    """按给定顺序重排地址：每台占各自通道数，跨 512 自动进位到下一宇宙。

    返回 `[{"fixture", "universe", "start", "end", "carried"}]`；`carried=True`
    表示这台已被推进到下一宇宙（调用方提示用户"跨域进位 N 台"）。
    """
    out: List[dict] = []
    universe = int(start_universe)
    cursor = int(start_channel)
    if universe < UNIVERSE_MIN:
        universe = UNIVERSE_MIN
    if cursor < 1:
        cursor = 1
    for fi in order or []:
        n = channel_count_of(fi, defs_by_id)
        if cursor + n - 1 > UNIVERSE_MAX_CHANNEL:
            universe += 1
            cursor = 1
            carried = True
        else:
            carried = False
        end = cursor + n - 1
        out.append({"fixture": fi, "universe": universe, "start": cursor,
                    "end": end, "carried": carried})
        cursor = end + 1
    return out


def plan_summary(plan: Sequence[dict]) -> str:
    """重编号结果摘要（"N 台 · 宇宙 a CHx–CHy → 宇宙 b CHm–CHn，跨域进位 k 台"）。"""
    if not plan:
        return "没有需要重编号的设备"
    first, last = plan[0], plan[-1]
    carried = sum(1 for step in plan if step.get("carried"))
    text = (f"{len(plan)} 台：宇宙 {first['universe']} CH{first['start']} "
            f"→ 宇宙 {last['universe']} CH{last['end']}")
    if carried:
        text += f"（跨域进位 {carried} 台）"
    return text
