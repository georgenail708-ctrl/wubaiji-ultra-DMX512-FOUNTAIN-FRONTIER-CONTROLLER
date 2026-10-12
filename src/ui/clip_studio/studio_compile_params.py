# -*- coding: utf-8 -*-
"""参数卡贡献编译（R183 W2-ST）—— 从 `studio_compile` 拆出（500 行门禁）。

R203：clip 级参数拖入即生效；需要下游目标的相位供给仍须接线。按
各卡语义产出 clip 级字段（contrib）或对下游卡的字段供给（supplies）。本模块是
唯一实现点；`studio_compile.compile_sheet` 只调用 `param_outputs`。

分派签名统一为 ``fn(节点, 节点id, 目标边表, (contrib, supplies, hint), res)`` ——
目标已预过滤为「连到效果大类卡」的入边（`EFFECT_GROUPS`）；`hint` 是
`studio_compile._hint` 的注入引用（提示级 issue 写入器，防循环 import）。
"""
from __future__ import annotations

from src.ui.clip_studio.studio_cards import GROUP_PARAM, _RATIO_TYPES
from src.ui.clip_studio.studio_graph import card_targets, node_group, param_value

__all__ = ["PARAM_COMPILERS", "param_outputs"]

#: 参与"参数供给"的效果大类（与 studio_compile.EFFECT_GROUPS 同口径）。
EFFECT_GROUPS = frozenset({"direction", "smooth", "batch", "tracking", "master", "optimizer"})


# ── 参数卡贡献：独立clip参数与下游供给分别处理 ─────────────────────────────

def _param_gb(n, nid, targets, out, res) -> None:
    """组数/批数卡（spec 关闭）：设组数；批次数 >0 才写批次字段。"""
    if bool(param_value(n, "spec_enabled")):
        return          # 模块语义（指定组）：不设组数/批数，覆盖走 overrides
    contrib = out[0]
    fields = {"fade_groups": int(param_value(n, "groups"))}
    if int(param_value(n, "batches")) > 0:
        fields["fade_group_sequence_batches"] = int(param_value(n, "batches"))
    contrib.append((nid, fields))


def _param_percent(n, nid, targets, out, res) -> None:
    """百分比卡：落点=速度 → clip 级 playback_rate；落点=相位差强度 → 供电机相位差卡。"""
    supplies = out[1]
    target = str(param_value(n, "target"))
    value = float(param_value(n, "value")) / 100.0
    if target == "playback_rate":
        supplies[str(n.get("id"))] = {"playback_rate": value}
        return
    hit = [d for _e, d in targets if str(d.get("key")) == "motor_phase"]
    if not hit:
        out[2](res, "V6", nid, "百分比卡落点为相位差强度，但未连到电机相位差卡（不生效）")
        return
    for d in hit:
        supplies[str(d.get("id"))] = {"motor_transition_phase_amount": value}


def _param_duration(n, nid, targets, out, res) -> None:
    """时长卡：淡入/淡出 clip 级；组间停顿仅在连到批次卡时经供给落 fade_group_pause。"""
    contrib, supplies = out[0], out[1]
    contrib.append((nid, {"fade_in": float(param_value(n, "fade_in")),
                          "fade_out": float(param_value(n, "fade_out"))}))
    if any(str(d.get("key")) == "batch_seq" for _e, d in targets):
        supplies[str(n.get("id"))] = {"fade_group_pause": float(param_value(n, "pause"))}


def _param_ch_ratio(n, nid, targets, out, res) -> None:
    """通道延迟比例卡：仅收 ≠1.0 的档位（与引擎/原面板「1.0=100%」口径一致）。"""
    contrib = out[0]
    d_in = {t[0]: float(param_value(n, f"in_{t[0]}")) for t in _RATIO_TYPES
            if abs(float(param_value(n, f"in_{t[0]}")) - 1.0) > 1e-9}
    d_out = {t[0]: float(param_value(n, f"out_{t[0]}")) for t in _RATIO_TYPES
             if abs(float(param_value(n, f"out_{t[0]}")) - 1.0) > 1e-9}
    if d_in:
        contrib.append((nid, {"fade_type_ratio_in": d_in}))
    if d_out:
        contrib.append((nid, {"fade_type_ratio_out": d_out}))


#: 参数卡编译分派（统一签名：节点, 节点id, 目标, (contrib, supplies, hint), res）。
PARAM_COMPILERS = {
    "group_batch": _param_gb, "percent": _param_percent,
    "duration": _param_duration, "ch_ratio": _param_ch_ratio,
}


def param_outputs(nodes, idx, cycle_ids, res, hint) -> tuple:
    """参数卡贡献：返回（clip 级字段列表, 供给表）。

    `hint` = `studio_compile._hint`（提示级 issue 写入器；由调用方注入 —— 本模块
    不反向 import 编译器模块，防循环依赖）。"""
    contrib: list = []
    supplies: dict = {}
    for n in nodes:
        nid = str(n.get("id"))
        if node_group(n) != GROUP_PARAM or nid in cycle_ids:
            continue
        targets = [(e, d) for e, d in card_targets(idx, nid, to_port="in")
                   if node_group(d) in EFFECT_GROUPS]
        fn = PARAM_COMPILERS.get(str(n.get("key")))
        if fn is not None:
            fn(n, nid, targets, (contrib, supplies, hint), res)
    return contrib, supplies


