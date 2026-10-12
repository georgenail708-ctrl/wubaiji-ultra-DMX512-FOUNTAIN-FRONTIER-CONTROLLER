# -*- coding: utf-8 -*-
"""监视器常驻统计行（R183 W2；深查② §四 #11）。

需求原文：「统计行（min/max/avg/活动数）：`_sel_label` 旁常驻
`data.min/max/mean, (data>0).sum()`（节流刷新）」。

设计：纯 numpy 一行式统计（无 Qt 依赖，可离屏直测），文本格式化与节流
判定分开放——节流阈值是"刷新策略"，不是数据语义。
"""
from __future__ import annotations

import numpy as np

STATS_MIN_INTERVAL = 0.25    # 节流：20Hz 刷新下 ≥250ms 才重算统计文本


def frame_stats(data) -> dict:
    """纯函数：512 帧值 → {min,max,mean,active,n}；空输入返回全零。"""
    arr = np.asarray(data).reshape(-1)
    if arr.size == 0:
        return {"min": 0, "max": 0, "mean": 0.0, "active": 0, "n": 0}
    f = arr.astype(np.float64, copy=False)
    return {
        "min": int(f.min()),
        "max": int(f.max()),
        "mean": float(f.mean()),
        "active": int(np.count_nonzero(f > 0)),
        "n": int(f.size),
    }


def format_stats(stats: dict) -> str:
    """统计 dict → 单行文本（固定宽度观感；缺键按零，容忍 None）。"""
    s = stats or {}
    try:
        return (f"min {int(s.get('min', 0))} | max {int(s.get('max', 0))} | "
                f"avg {float(s.get('mean', 0.0)):.1f} | "
                f"活动 {int(s.get('active', 0))}/{int(s.get('n', 0))}")
    except (TypeError, ValueError):
        return "min 0 | max 0 | avg 0.0 | 活动 0/0"


def stats_due(last_ts: float, now: float,
              interval: float = STATS_MIN_INTERVAL) -> bool:
    """节流判定纯函数：距上次刷新 ≥interval 才到期（last 为 -inf 恒真）。"""
    return (float(now) - float(last_ts)) >= float(interval)
