# -*- coding: utf-8 -*-
"""R14：跑动顺序表 → 轨道（"顺序即轨道"，消除二次描线）。

深查③ R14：`route_order_state`（真工程「顺序1」已排 174 台）与火车无联动；
本模块取当前页顺序表的 ``ordered_ids``，按 TopView 世界坐标串成一条轨道，
经既有 ``runner_panel.apply_topview_route`` 契约（画布 → 平滑 → 引擎 add_track）
喂进引擎——引擎与画布零改动。

数据源（只读工程字段，不新建第二份状态）：
  · 顺序表：``project.route_order_state["sheets"]``，``active`` 指向激活页
    （与 RouteOrderPanel 同口径：`active_sheet` 索引）；
  · 坐标：TopView 页面 ``devices``（真工程 fixture 自身 pos 恒为 (0,0)，
    坐标真源在页 devices 里——与 `runner_panel._apply_page_devices` 同源）。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

#: 生成轨道的最少点数（<2 点无法成轨，给调用方文案用）
MIN_POINTS = 2


def _project_of(panel):
    try:
        proj = panel._project()
    except Exception:
        proj = None
    if proj is not None:
        return proj
    pm = getattr(panel, "_project_manager", None)
    if pm is None:
        return None
    try:
        return (getattr(pm, "project", None)
                or getattr(pm, "_project", None))
    except Exception:
        return None


def active_sheet(proj) -> Optional[dict]:
    """当前激活的顺序表（工程无该键/空表 → None，旧档零迁移）。"""
    ros = getattr(proj, "route_order_state", None)
    if not isinstance(ros, dict):
        return None
    sheets = ros.get("sheets")
    if not isinstance(sheets, list) or not sheets:
        return None
    idx = 0
    try:
        idx = int(ros.get("active_sheet", 0) or 0)
    except (TypeError, ValueError):
        idx = 0
    if not (0 <= idx < len(sheets)):
        idx = 0
    sheet = sheets[idx]
    return sheet if isinstance(sheet, dict) else None


def sheet_order_ids(sheet) -> List[str]:
    """顺序表里的设备 id 序列（去重保序；空表给空列表）。"""
    out: List[str] = []
    seen: set = set()
    for raw in (sheet.get("ordered_ids") or []) if sheet else ():
        sid = str(raw or "")
        if sid and sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def _points_of_devices(devices) -> Dict[str, Tuple[float, float]]:
    """设备列表 → {id: (x, y)}（坏数据跳过，不炸）。"""
    out: Dict[str, Tuple[float, float]] = {}
    for dev in devices or ():
        did = str(dev.get("id", ""))
        if not did:
            continue
        try:
            out[did] = (float(dev.get("x", 0.0)), float(dev.get("y", 0.0)))
        except (TypeError, ValueError):
            continue
    return out


def _cached_positions(panel) -> Dict[str, Tuple[float, float]]:
    """面板 `_device_positions` 归一化（坏条目跳过 → 空表走 TopView 回退）。"""
    pos = getattr(panel, "_device_positions", None)
    if not isinstance(pos, dict) or not pos:
        return {}
    try:
        return {str(k): (float(v[0]), float(v[1])) for k, v in pos.items()}
    except (TypeError, ValueError, IndexError, KeyError):
        return {}


def _page_positions(panel) -> Dict[str, Tuple[float, float]]:
    """当前页设备坐标（面板内存缓存优先，缺失回退 TopView 页数据）。"""
    cached = _cached_positions(panel)
    if cached:
        return cached
    try:
        pages = panel._topview_pages()
    except Exception:
        return {}
    pid = str(getattr(panel, "_current_page_id", "") or "")
    for pg in (pages or []):
        if not pid or str(pg.get("id", "")) == pid:
            return _points_of_devices(pg.get("devices"))
    return {}


def order_route_points(panel) -> Tuple[List[Tuple[float, float]], int]:
    """顺序表 → 世界坐标点序；返回 (点序, 命中台数)。缺坐标的 id 跳过。"""
    proj = _project_of(panel)
    sheet = active_sheet(proj) if proj is not None else None
    ids = sheet_order_ids(sheet)
    if not ids:
        return [], 0
    positions = _page_positions(panel)
    pts: List[Tuple[float, float]] = []
    for sid in ids:
        p = positions.get(sid)
        if p is None:
            continue
        pts.append((float(p[0]), float(p[1])))
    return pts, len(pts)


def apply_order_as_track(panel) -> int:
    """「按顺序生成轨道」入口：点序喂给既有 apply_topview_route 契约。

    返回喂入点数（0 = 未生成；状态行给原因）。顺序表 174 台真工程坐标齐全时
    174 点一条轨道，平滑/接道岔/挂车全走既有链路。
    """
    if _project_of(panel) is None:
        _status(panel, "没有打开的工程，无法读取跑动顺序表")
        return 0
    pts, hit = order_route_points(panel)
    if hit < MIN_POINTS:
        _status(panel, "当前页顺序表为空或本页设备坐标不足"
                       "（先到「跑动顺序」面板排好顺序再试）")
        return 0
    try:
        # 复用既有浮条投递端：内部走 track_chain_drawn → 平滑 → 引擎
        panel.apply_topview_route(str(getattr(panel, "_current_page_id", "")), pts)
    except Exception:
        log.debug("order route apply failed", exc_info=True)
        _status(panel, "按顺序生成轨道失败（详情见日志）")
        return 0
    _status(panel, f"已按跑动顺序生成轨道（{hit} 台设备串线）")
    return hit


def _status(panel, text: str) -> None:
    try:
        panel._set_status(text)
    except Exception:
        pass


__all__ = ["apply_order_as_track", "order_route_points", "active_sheet",
           "sheet_order_ids"]
