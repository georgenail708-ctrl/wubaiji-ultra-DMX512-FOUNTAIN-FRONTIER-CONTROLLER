# -*- coding: utf-8 -*-
"""复合过渡后端（R172 Clip Studio §4）—— 指定组 × 方向覆盖的解析与签名。

设计文档 `docs/clip_studio_r172_design.md` §4.1 语义（用户定案 2026-09-26）::

    主方向卡决定 base order 与组切块（整体仍是一个过渡方式——组员归属按主方向）。
    每组可有覆盖方向：该组内部设备相位用覆盖方向计算
    （`_direction_positions_for_fixture_ids(ids, 覆盖方向, scope_ids=…)`——
    positions 仍是全局归一化，因此组级起步顺序（延迟翼铺开）与组内扫向
    **同时**由覆盖方向决定：1-3 组 ltr + 4-6 组 rtl → 两翼相向收束，天然正确）。
    批次/停顿/两翼全部不动：`batch_idx = group_idx % batch_count`、
    `_seq_latency_start`、`_parallel_in_window` 均只消费 group_idx/group_pos/
    group_sizes 三数组。

数据源是 `models.Clip.fade_group_direction_overrides`
（每项 `{"spec": {组选择规格}, "direction": "ltr"}`；序列化归一在
`core/group_selection.normalize_direction_overrides`，W1a 落地）。

## 引擎挂钩点（playback_engine.py，上帝类——只改既有方法体，未新增方法）

`PlaybackEngine._sequential_fixture_group_data`：

* 两处缓存键（`seq_group_data_quick` / `seq_group_data`）追加
  `overrides_signature(clip, int(group_count or 1))` —— 改覆盖内容即换键，
  不得串缓存（本模块测试 f 项钉死）。
* 分组循环内 `positions = self._direction_positions_for_fixture_ids(
  ids, overrides_map.get(group_start + _gi, direction), …)` —— 覆盖表为空时
  `dict.get(_, direction)` 恒返回主方向，**无覆盖路径与改动前逐字节一致**
  （黄金门禁 `tools/real_project_wings_gate.py --check` 42/42 保证）。
* 覆盖方向只替换 `_direction_positions_for_fixture_ids` 的 **direction 实参**；
  `_order_fixture_ids_for_direction`（base order）与组切块仍用主方向 ——
  对齐 §4.1「组员归属按主方向」。

## 不接覆盖的回退路径（与「无方向」同级，刻意保持）

* `_sequential_fixture_group_data` 的**无几何回退**（`total <= 0 or K <= 0`）
  与 **meta 缺失回退**（任一设备不在 meta 里）：走编号序切块 +
  `_compute_direction_positions`（纯序位、无设备坐标），无从谈设备级覆盖 ——
  与「无方向」同级，不接覆盖（设计文档 §4.2 明示可接受）。
* `_compute_group_gains_vectorized`（playback_engine.py :9000）：仅由
  `_render_clip_frame` 的**通道级回退**（universe 无设备映射、`u_fixtures`
  为空）调用（:9503），positions 来自 `_compute_direction_positions`、组按
  **通道索引**切块 —— 没有设备身份，组覆盖无从解析；其 C 侧镜像
  `timeline_ctx.DirRowBuilder._build_path_b` 同样用 `_compute_direction_positions`
  （两侧同源，见下）。不经过 `_sequential_fixture_group_data` 的组相位计算
  仅此一处，且只服务无设备回退，报告已注明证据。
* `_order_fixture_ids_for_direction` 内部的 `_direction_positions_for_fixture_ids`
  （:8202）是 **base order 层**，必须保持主方向（§4.1），不是漏改。

## CE 同步取证结论（2026-09-26，§4.3）

**dir 表由 Python 侧产出、C 侧只消费** —— 不存在 C 独立重算，无需门禁：

* 生产链：`render_lab/qascade_ce/__init__.py` 的 `_k2_build_tables`（:6245，
  由 :7169 monkeypatch 到 `PlaybackEngine._k2_build_tables`）→ 每段建
  `DirRowBuilder`（`render_lab/qascade_ce/timeline_ctx.py` :202 起）→
  `_build_path_a`（:411 起）调用 **`engine._sequential_fixture_group_data`**
  取 `(gi, gp, gs)` 三数组写入 dir 行（`gi_in/gp_in/gs_in/gi_out/gp_out/gs_out`）。
* C 侧：`CeDirBlock`（timeline_ctx.py，与 `ce_engine.c` `ce_dir_block`
  :2889-2976 严格同序）只接收这些指针；C 不重算组相位。
* 因此覆盖只改变 Python 写入表的值，C 自动获得正确数据；验证 =
  `tools/_probe_r172_composite_ce.py`（真实工程挂 CE，断言 CE 输出 ==
  纯 Python 输出逐点相同）。
* 通道级 path B（无设备身份）两侧同用 `_compute_direction_positions` 纯序位
  —— 与覆盖正交，两侧天然一致。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from src.core.group_selection import (
    group_indices_for_spec,
    normalize_direction_overrides,
)

__all__ = [
    "overrides_map_for_clip",
    "overrides_signature",
    "overrides_signature_with_group_count",
]

#: `overrides_signature(clip)`（缺省 total_groups）展开 spec 用的规范组数上限。
#: 仅签名/探针用途 —— 引擎缓存键处应传真实 `int(group_count or 1)` 取精确组号。
_CANONICAL_TOTAL_GROUPS = 256


def _total_groups_or(value: Any, fallback: int) -> int:
    """宽容取整；非法（≤0/非数）→ fallback。"""
    try:
        total = int(value)
    except (TypeError, ValueError):
        return fallback
    return total if total >= 1 else fallback


def _valid_raw_items(raw: Any) -> list:
    """取 raw 覆盖列表里的**可消费项**：dict 且带非空 spec；其余丢弃。

    ★ 与 `core.group_selection.normalize_direction_overrides` 的实现一致
    （非 dict 项 / 非 dict spec 项一律丢弃 —— normalize 现已按其 docstring
    落实，`spec="x"` 等错型 spec 同样被丢弃而非回退默认）。本模块保留
    `_valid_raw_items` 作为消费侧防御层（normalize 之前先少一层规整开销）。
    """
    if not isinstance(raw, (list, tuple)):
        return []
    return [it for it in raw
            if isinstance(it, dict) and it.get("spec") is not None]


def overrides_map_for_clip(clip: Any, total_groups: int) -> Dict[int, str]:
    """解析 clip 的复合方向覆盖 → `{0-based 组号: 覆盖方向}`。

    * `clip.fade_group_direction_overrides` 为空/缺失/非列表 → `{}`（调用方
      据此走与改动前逐字节一致的无覆盖路径）。
    * 组号用 `core.group_selection.group_indices_for_spec` 对
      `total_groups`（= 实际分组数，引擎处传入**夹取后**的 group_count）
      展开：1-based → 0-based；越界组自动裁掉。
    * 畸形项由 `normalize_direction_overrides` 丢弃（非 dict/缺 spec → 跳过，
      direction 非法 → 回退 "ltr"）；本函数自身**永不抛异常**。
    * 同组被多项覆盖时**后者胜出**（对齐 §3.3「冲突时按链序后者胜出」）。
    """
    raw = getattr(clip, "fade_group_direction_overrides", None)
    if not raw:
        return {}
    items = normalize_direction_overrides(_valid_raw_items(raw))
    if not items:
        return {}
    total = _total_groups_or(total_groups, 0)
    if total < 1:
        return {}
    out: Dict[int, str] = {}
    for item in items:
        direction = str(item.get("direction", "ltr") or "ltr")
        for group_1based in group_indices_for_spec(item.get("spec"), total):
            out[int(group_1based) - 1] = direction
    return out


def overrides_signature(clip: Any, total_groups: Optional[int] = None) -> tuple:
    """覆盖列表 → 稳定可哈希签名（缓存键用）。

    形态：**按列表序**的「每项 (1-based 组号, 方向) 有序元组」的元组::

        ( ((1, "ltr"), (2, "ltr"), (3, "ltr")), ((4, "rtl"), …) )

    * **保留列表序**：同组多覆盖是「后者胜出」语义，项序变化必须换签名
      （否则串缓存）；项内组号排序保证 determinism（spec 展开是集合）。
    * `total_groups` 给定时（引擎缓存键处传 `int(group_count or 1)`）按真实
      组数展开取精确组号；缺省按 `_CANONICAL_TOTAL_GROUPS` 规范展开（仅
      探针/测试用 —— 组号可能超出真实组数，但恒稳定可哈希）。
    * 空覆盖 → `()`；畸形输入永不抛异常（normalize 链路兜底）。
    """
    raw = getattr(clip, "fade_group_direction_overrides", None)
    if not raw:
        return ()
    items = normalize_direction_overrides(_valid_raw_items(raw))
    if not items:
        return ()
    # None/垃圾值 → 规范上限（_total_groups_or 兜底）
    total = _total_groups_or(total_groups, _CANONICAL_TOTAL_GROUPS)
    out: Tuple[tuple, ...] = ()
    for item in items:
        direction = str(item.get("direction", "ltr") or "ltr")
        groups = sorted(group_indices_for_spec(item.get("spec"), total))
        out = out + (tuple((int(g), direction) for g in groups),)
    return out


def overrides_signature_with_group_count(clip: Any, group_count: Any) -> tuple:
    """引擎缓存键专用：`group_count` 按 `int(group_count or 1)` 同义归一后取签名。

    归一放在本模块（新函数有 CC 预算）而不是引擎方法体里 —— 上帝类冻结，
    `_sequential_fixture_group_data` 的圈复杂度基线一个 `or` 都不能涨。
    None/0/负数/非数 → 1（比 `or` 版更保守：负数不再漏过）。
    """
    return overrides_signature(clip, _total_groups_or(group_count, 1))
