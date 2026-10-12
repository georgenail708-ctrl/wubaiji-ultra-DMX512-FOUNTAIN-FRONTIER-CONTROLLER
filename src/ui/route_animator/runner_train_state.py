# -*- coding: utf-8 -*-
"""R183 W2：火车运行态的**快照/回填**纯逻辑（从 `_rebuild_engine` 原样搬出）。

引擎没有 remove_track/remove_switch，结构变更（镜像/清轨/删道岔）后要靠
"从面板场景重放 add_*"重建引擎；重建时必须保留每列火车的运行态，否则
镜像一次就把 R1 斜坡、R6 错峰延迟、R8 段循环全部清零。

搬出动机：`runner_panel._rebuild_engine` 已入架构基线（42 行/CC 11），
W2 要新增 R1/R6/R8 三个运行态字段，只能在别处实现、面板只留两行调用。
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: 快照里除元数据外的运行态键（回填时用同名字段 setattr）
RUNTIME_KEYS = ("ramp_sec", "ramp_easing", "start_delay", "loop_span",
                "loop_span_edge")


def snapshot_train_state(tr) -> dict:
    """单列火车 → 可重放快照（元数据 + R1/R6/R8 运行态）。"""
    return {
        "tid": str(tr.train_id), "car_count": int(tr.car_count),
        "car_length": float(tr.car_length), "radius": float(tr.radius),
        "base_speed": float(tr.base_speed), "color": str(tr.color),
        "throttle": float(tr.throttle),
        "track_id": str(getattr(tr, "_home_edge_id", "") or ""),
        "nose_s": float(tr.nose_s),
        "chain_length": float(tr.chain_length), "mode": tr.mode,
        "skip_gaps": bool(tr.skip_gaps),
        "enabled": not bool(tr.ended),
        # R1/R6/R8 运行态随重建保留（镜像/清轨等结构变更不再丢斜坡/段循环）
        "ramp_sec": float(getattr(tr, "ramp_sec", 0.0) or 0.0),
        "ramp_easing": str(getattr(tr, "ramp_easing", "") or "OutCubic"),
        "start_delay": float(getattr(tr, "start_delay", 0.0) or 0.0),
        "loop_span": tuple(tr.loop_span) if getattr(tr, "loop_span", None) else None,
        "loop_span_edge": getattr(tr, "loop_span_edge", None),
    }


def restore_train_state(eng, item: dict) -> None:
    """把快照回填到新建引擎（挂轨/油门/模式/R1/R6/R8 全量）。"""
    tid = str(item["tid"])
    eng.set_train_mode(tid, item["mode"])
    eng.set_train_skip_gaps(tid, item["skip_gaps"])
    tr2 = eng._trains[tid]
    tr2.ended = not bool(item.get("enabled", True))
    if item.get("ramp_sec"):
        tr2.ramp_sec = float(item["ramp_sec"])
        tr2.ramp_easing = str(item.get("ramp_easing") or "OutCubic")
    tr2.start_delay = float(item.get("start_delay", 0.0) or 0.0)
    if item.get("loop_span"):
        eng.set_train_loop_span(tid, *item["loop_span"])
        tr2.loop_span_edge = item.get("loop_span_edge") or tr2.loop_span_edge


def card_param_specs(tr, fallback_car_unit: float) -> list:
    """火车卡片五参数的量程规格（R183 W2 从 `_build_train_card` 原样搬出）。

    每项 = (标签, 下限, 默认量程上限, 当前值, 小数位, 字段名)；上限用设备
    间距 `cu` 推导（车长/锁链/半径 ×10，最大速度 ×20），车厢数按 cu/10。
    """
    cu = max(1.0, float(fallback_car_unit or 1.0))
    return [
        ("车厢", 1, max(10, int(round(cu / 10.0))), tr.car_count, 0, "car_count"),
        ("车长", 0.0, max(100.0, cu * 10.0), tr.car_length, 2, "car_length"),
        ("锁链", 0.5, max(100.0, cu * 10.0), tr.chain_length, 2, "chain_length"),
        ("半径", 0.0, max(100.0, cu * 10.0), tr.radius, 2, "radius"),
        ("最大速度", 0.0, max(200.0, cu * 20.0), tr.base_speed, 2, "base_speed"),
    ]


__all__ = ["RUNTIME_KEYS", "card_param_specs", "restore_train_state",
           "snapshot_train_state"]
