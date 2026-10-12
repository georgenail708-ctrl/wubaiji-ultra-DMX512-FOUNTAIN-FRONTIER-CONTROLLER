# -*- coding: utf-8 -*-
"""组选择规格（R172 Clip Studio §2.2）—— 模块级纯函数，零 UI / Qt 依赖。

设计文档 `docs/clip_studio_r172_design.md` §2.2::

    spec := {"mode": "range"|"odd"|"even"|"stride",
             "start": int, "end": int, "take": int, "gap": int}
    group_indices_for_spec(spec, total_groups) -> frozenset[int]
    spec_label(spec) -> str        # "第1-3组" / "奇数组" / "每2组取1组，从第3组开始"

**编号语义（对外一律 1-based）**：第 1 组..第 total_groups 组。内部实现可用
0-based，但 `group_indices_for_spec` 的返回值必须是 1-based 组号集合。

**语义对齐 TopView**（`topview_panel.apply_selection_preset` / `apply_selection_filter`）：

* `odd`  = 奇数组（1, 3, 5, …）；`even` = 偶数组（2, 4, 6, …）。
* `stride` = 「从第 start 组开始，选 take 组、隔 gap 组」循环取直到超出总数，
  与 `apply_selection_filter` 的 `units[i:i+take]; i += take + gap` 逐位一致
  （TopView 里 `start=max(0,值-1)`、`take=max(1,值)`、`gap=max(0,值)`）。
* `range` = start..end **含两端**。

容错纪律：缺键补默认、非法值回退、mode 未知 → range；越界裁剪、空集 → 空
frozenset。任何畸形输入都不得抛异常（ Studio 卡片反编译/工程加载路径会喂进
手改过的存档数据）。
"""
from __future__ import annotations

from typing import Any, FrozenSet, List

__all__ = [
    "SPEC_MODES",
    "DEFAULT_GROUP_SPEC",
    "normalize_group_spec",
    "group_indices_for_spec",
    "spec_label",
    "spec_signature",
    "normalize_direction_overrides",
]

#: 合法的 spec.mode 取值（设计文档 §2.2）。
SPEC_MODES = ("range", "odd", "even", "stride")

#: 归一化后的缺省 spec（=「第 1 组」）。
DEFAULT_GROUP_SPEC = {"mode": "range", "start": 1, "end": 1, "take": 1, "gap": 0}


def _to_int(value, fallback: int) -> int:
    """宽容取整：数值/数字串 → int；其余 → fallback（bool 视作 0/1，可接受）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def normalize_group_spec(spec: Any) -> dict:
    """把任意输入收敛成**规范的五键 spec**；永不抛异常。

    * 非 dict（None/列表/垃圾）→ 整体回退默认 spec。
    * `mode` 非法（含大小写噪音）→ `"range"`；未知值一律回退。
    * `start`/`end`/`take` 非 ≥1 整数 → 回退默认（1）；`gap` 非 ≥0 → 回退 0。
      对齐 TopView：`take=max(1,值)`、`gap=max(0,值)`、start 从 1 计。
    * `end < start` 属**合法但空**（range 展开为空集），不做静默交换。
    """
    if not isinstance(spec, dict):
        return dict(DEFAULT_GROUP_SPEC)

    mode = str(spec.get("mode", "range") or "").strip().lower()
    if mode not in SPEC_MODES:
        mode = "range"

    start = _to_int(spec.get("start"), 0)
    if start < 1:
        start = 1
    end = _to_int(spec.get("end"), 0)
    if end < 1:
        end = 1
    take = _to_int(spec.get("take"), 0)
    if take < 1:
        take = 1
    gap = _to_int(spec.get("gap"), -1)
    if gap < 0:
        gap = 0

    return {"mode": mode, "start": start, "end": end, "take": take, "gap": gap}


def _stride_indices(start: int, take: int, gap: int, total: int) -> List[int]:
    """stride 展开（0-based 内部）—— 与 TopView `apply_selection_filter` 逐位一致。

    TopView 原型::

        i = start          # TopView 里 start 已减 1（此处传入的 start 已是 0-based）
        while i < len(units):
            chosen.extend(units[i:i + take])
            i += take + gap
    """
    out: List[int] = []
    i = start
    while 0 <= i < total:
        out.extend(range(i, min(i + take, total)))
        i += take + gap
    return out


def group_indices_for_spec(spec: Any, total_groups: int) -> FrozenSet[int]:
    """按 spec 展开**1-based 组号集合**（越界裁剪；空集 → `frozenset()`）。

    * `range`  = start..end 含两端，与 [1, total] 求交。
    * `odd`    = 奇数组 1,3,5,…；`even` = 偶数组 2,4,6,…（1-based 语义，
      与 TopView `apply_selection_preset` 的 i%2 判据等价）。
    * `stride` = 从第 start 组开始，选 take 组、隔 gap 组，循环取直到超出总数。
    * `total_groups` 非法（≤0/非数）→ 空集。
    """
    total = _to_int(total_groups, 0)
    if total < 1:
        return frozenset()

    s = normalize_group_spec(spec)
    mode = s["mode"]

    if mode == "odd":
        return frozenset(g for g in range(1, total + 1) if g % 2 == 1)
    if mode == "even":
        return frozenset(g for g in range(1, total + 1) if g % 2 == 0)
    if mode == "stride":
        zero_based = _stride_indices(s["start"] - 1, s["take"], s["gap"], total)
        return frozenset(i + 1 for i in zero_based)

    # range（含未知 mode 回退后的情形）：start..end 含两端，越界裁剪
    lo, hi = s["start"], s["end"]
    if hi < lo:
        return frozenset()
    return frozenset(range(max(1, lo), min(total, hi) + 1))


def spec_label(spec: Any) -> str:
    """spec → 传神中文（Studio 卡片/总览显示用）。

    例：`"第1-3组"` / `"第2组"` / `"奇数组"` / `"偶数组"` /
    `"每2组取1组，从第3组开始"` / `"选2组隔1组，从第1组开始"` / `"从第3组起全部"`。
    """
    s = normalize_group_spec(spec)
    mode = s["mode"]
    if mode == "odd":
        return "奇数组"
    if mode == "even":
        return "偶数组"
    if mode == "range":
        lo, hi = s["start"], s["end"]
        if hi > lo:
            return f"第{lo}-{hi}组"
        return f"第{lo}组"
    # stride
    start, take, gap = s["start"], s["take"], s["gap"]
    if gap <= 0:
        # 不隔组循环取 = 从 start 起连续选中到末尾
        return f"从第{start}组起全部"
    if take == 1:
        return f"每{gap + 1}组取1组，从第{start}组开始"
    return f"选{take}组隔{gap}组，从第{start}组开始"


def spec_signature(spec: Any) -> tuple:
    """spec → 可哈希签名（供复合过渡缓存键用；先归一再签名）。"""
    s = normalize_group_spec(spec)
    return (s["mode"], s["start"], s["end"], s["take"], s["gap"])


# ── 复合方向覆盖列表（models.Clip.fade_group_direction_overrides 的序列化归一）──

#: 覆盖项 direction 的合法取值（与 models.Clip 淡入淡出方向词汇一致，§4.1）。
DIRECTION_VALUES = ("ltr", "rtl", "ftb", "btf", "cw", "ccw", "ctr", "rtc",
                    "alt", "alt_block", "random", "wave", "cascade",
                    "rev_cascade", "zigzag")


def normalize_direction_overrides(raw: Any) -> List[dict]:
    """把覆盖列表收敛成 `[{"spec": {...}, "direction": "ltr"}, …]`；畸形项丢弃。

    `models.py` 的 to_dict/_from_dict 照 `fade_group_pauses` 模式调用本函数
    （缺键给默认、绝不崩）。非 dict 项 / 缺 spec 项一律丢弃；direction 非法
    → 回退 `"ltr"`。
    """
    if not isinstance(raw, (list, tuple)):
        return []
    out: List[dict] = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("spec"), dict):
            continue
        direction = str(item.get("direction", "ltr") or "").strip().lower()
        if direction not in DIRECTION_VALUES:
            direction = "ltr"
        out.append({
            "spec": normalize_group_spec(item.get("spec")),
            "direction": direction,
        })
    return out
