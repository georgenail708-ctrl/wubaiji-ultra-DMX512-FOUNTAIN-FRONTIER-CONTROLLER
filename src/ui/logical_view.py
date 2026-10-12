# -*- coding: utf-8 -*-
"""逻辑设备 control_mode 的写回助手（纯逻辑、不依赖 Qt，可单测）。"""
from __future__ import annotations

from typing import List


def _logical_records(pm) -> List[dict]:
    """从 ProjectManager / Project 读 logical_devices 的 dict 列表（容错）。"""
    recs = None
    for src in (pm, getattr(pm, "project", None)):
        if src is None:
            continue
        recs = getattr(src, "logical_devices", None)
        if recs is not None:
            break
    out: List[dict] = []
    for r in (recs or []):
        if isinstance(r, dict):
            out.append(r)
        elif hasattr(r, "to_dict"):
            try:
                out.append(r.to_dict())
            except Exception:
                continue
    return out


def set_control_mode(pm, logical_id: str, mode: str) -> bool:
    """设置某逻辑设备的 control_mode（merged/decomposed），写回 pm.logical_devices。

    返回是否发生变化。调用方负责触发 project_changed / 刷新。
    """
    if mode not in ("merged", "decomposed"):
        return False
    recs = _logical_records(pm)
    changed = False
    for r in recs:
        if str(r.get("id", "")) == str(logical_id):
            if str(r.get("control_mode", "merged")) != mode:
                r["control_mode"] = mode
                changed = True
            break
    if changed:
        try:
            pm.logical_devices = recs   # 经 setter 标脏 + emit
        except Exception:
            # 直接写 project 兜底
            proj = getattr(pm, "project", None) or pm
            try:
                proj.logical_devices = recs
            except Exception:
                return False
    return changed
