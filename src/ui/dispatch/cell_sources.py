# -*- coding: utf-8 -*-
"""R138：把「这一格的值是谁写的」喂给监控区表格（`view.set_sources`）。

用户实报（2026-09-21）::

    "…但是电机的通道上却正确显示了是电机有波形效果 effect，此时点击这个电机通道，
     会唤起 programmer（默认状态）这表示我的 effect 效果丢了，如果真的存了 effect
     的电机效果，应该唤起的是 x 轴的加载的 effect。"

## 真因

`TopViewMonitorView.target_panel_for(key)`（`table_view.py:465`）的**第一优先级**
就是"该格声明的来源面板"：

    srcs = self._sources.get(key, ())
    if len(srcs) == 1:  return str(srcs[0])

来源角标与双击跳转都靠它。**但全仓没有任何调用方 `set_sources(...)`**
（`grep -rn "set_sources" src/` 只剩定义与注释，且 `set_values()` 每次还会把它清空）
→ `_sources` 恒为空 → `len(srcs) == 0` 落到第三优先级 `return "programmer"`。

于是无论这一格真实由哪个面板写入（effect/matrix/linker/audio…），双击永远唤起
**programmer**；而 programmer 里并没有该格的效果参数 → 用户看到"默认状态"，
正是他说的"我的 effect 效果丢了"。

## 修法

来源从 **`EffectBus` 的实时节点**推导（单一真源，不另建"谁写了值"的账）：

  节点 = (panel, track_type, group_key, fixture_ids, ch_types, ...)

把每个节点覆盖的 `(设备id, 列键)` 映射成监控区视图的真实格键
（布局层口径：`key = (row.fixture_id, cell.col_key)`，见 `table_layout`），
累积成 `{格键: (panel, ...)}` 推给 `view.set_sources`。

★ 为什么按**节点**而不是按 NM 层反查：层归属只能回答"哪个层有值"，回答不了
  "哪个**面板**"（effect/matrix/audio 各写自己的层，programmer 写 fader）；
  节点自带 `panel` 字段，是各面板自报的权威来源。总线为空时（无效果在跑）
  不产出任何来源 → 保持原有"回落 programmer"行为（零回归）。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Tuple

log = logging.getLogger(__name__)

#: 面板 id → 显示名（角标/提示用；未知面板原样显示 id）
PANEL_LABELS = {
    "programmer": "编程器",
    "effect": "效果",
    "matrix": "矩阵",
    "linker": "关联器",
    "audio": "音频",
    "route": "跑动",
    "joystick": "摇杆",
    "clip": "时间线",
}


def panel_label(panel_id: str) -> str:
    """面板 id → 中文显示名（未知原样返回）。"""
    pid = str(panel_id or "")
    return PANEL_LABELS.get(pid, pid)


def _cell_index(cells) -> Dict[str, List[Tuple[str, str]]]:
    """格列表 → `{设备id: [(格键, 归一化列键), ...]}`。

    格键口径与布局层一致：`(row.fixture_id, col_key)`（见 `table_layout` 的
    `_order.append(key)`）。`col_key` 可能带 `@子序号`（复合设备拆行）——
    归一化时按 `@` 前段比对，使"节点作用于该通道类型"能覆盖全部子行。
    """
    out: Dict[str, List[Tuple[str, str]]] = {}
    for cell in (cells or ()):
        rec = _cell_record(cell)
        if rec is None:
            continue
        fid, col_key, base_col = rec
        out.setdefault(fid, []).append(((fid, col_key), base_col))
    return out


def _node_record(node):
    """节点 → `(面板id, 设备id列表, 通道类型集)`；无面板/异常 → None。"""
    try:
        panel = str(getattr(node, "panel", "") or "")
    except Exception:
        return None
    if not panel:
        return None
    try:
        fids = [str(x) for x in (getattr(node, "fixture_ids", None) or []) if str(x)]
        ctypes = {str(x).lower() for x in (getattr(node, "ch_types", None) or []) if str(x)}
    except Exception:
        fids, ctypes = [], set()
    return panel, fids, ctypes


def _cell_record(cell):
    """格 → `(设备id, 列键, 归一化列键)`；不可用 → None。"""
    try:
        fid = str(getattr(cell, "fixture_id", "") or "")
        col_key = str(getattr(cell, "col_key", "") or "")
        ch_type = str(getattr(cell, "ch_type", "") or "")
    except Exception:
        return None
    if not fid or not col_key:
        return None
    return fid, col_key, (col_key.split("@", 1)[0] or ch_type)


def sources_from_nodes(nodes, cells) -> Dict[Tuple[str, str], Tuple[str, ...]]:
    """由效果总线节点 + 当前格列表算出 `{格键: (面板id, ...)}`。

    仅当**该设备确实在节点作用域内**且**通道类型匹配**时才建立归属
    （节点 `ch_types` 为空 = 不限定通道 → 该设备所有格都算，与其它消费侧同口径）；
    同一格被多个面板写入时全部记录（`target_panel_for` 按交互型优先挑一个）。
    节点 `fixture_ids` 为空 = 未声明设备集 → 跳过（不猜"全部设备"，避免误标满表）。
    """
    by_device = _cell_index(cells)
    if not by_device:
        return {}
    out: Dict[Tuple[str, str], List[str]] = {}
    for node in (nodes or ()):
        rec = _node_record(node)
        if rec is None:
            continue
        panel, fids, ctypes = rec
        for fid in fids:
            for key, base_col in by_device.get(fid, ()):
                if _col_matches(base_col, ctypes):
                    _add_source(out, key, panel)
    return {k: tuple(v) for k, v in out.items()}


def _col_matches(base_col, ctypes) -> bool:
    """节点限定了通道类型时，该格列键是否落在其内（未限定 → 恒真）。"""
    return (not ctypes) or str(base_col).lower() in ctypes


def _add_source(out: dict, key, panel: str) -> None:
    """把面板记到该格（去重、保序）。"""
    bucket = out.setdefault(key, [])
    if panel not in bucket:
        bucket.append(panel)


def bus_nodes() -> list:
    """效果总线当前节点（失败/无总线 → 空列表，绝不抛）。"""
    try:
        from ...processing.dispatch import EffectBus
        return list(EffectBus.instance().nodes() or [])
    except Exception:
        log.debug("读取效果总线节点失败", exc_info=True)
        return []


def sync_view_sources(view, cells) -> int:
    """把当前来源表推给视图；返回建立的格数（0 = 无来源 → 视图回落原行为）。"""
    try:
        srcs = sources_from_nodes(bus_nodes(), cells)
        view.set_sources(srcs)
        return len(srcs)
    except Exception:
        log.debug("推送格子来源失败", exc_info=True)
        return 0


def effect_panel_of(view, key) -> str:
    """该格是否有"真效果"来源 → 返回其面板 id；否则 ""（★ R138）。

    判据用**总线节点自报的 `effect_id`**（"这一格真的是某个效果"），而不是
    "哪些层有这个地址" —— 后者把 fader 残留（电机归中 128 等）也算成来源，
    于是多来源时硬优先 programmer → 用户点电机格唤起的却是 programmer 的默认状态。

    查询走 `view._node_lookup`（面板可选注入）；未注入 → 恒空 = 保持旧行为（零回归）。
    """
    fn = getattr(view, "_node_lookup", None)
    if not callable(fn):
        return ""
    try:
        node = fn(key)
    except Exception:
        return ""
    if not isinstance(node, dict):
        return ""
    if not str(node.get("effect_id", "") or ""):
        return ""
    return str(node.get("panel", "") or "")


def node_for_cell(panel, key):
    """按格键取**总线节点**（供视图判"这一格是否有真效果"）；取不到 → None。"""
    try:
        from .cell_recall import node_of_cell
        return node_of_cell(panel, key, "")
    except Exception:
        return None


def install_node_lookup(panel) -> None:
    """把"按格键取节点"的回调装到该面板的视图上（幂等；失败静默）。"""
    view = getattr(panel, "view", None)
    if view is None or not hasattr(view, "_node_lookup"):
        return
    try:
        view._node_lookup = lambda key, _s=panel: node_for_cell(_s, key)
    except Exception:
        pass


#: 多来源且「都不是真效果」时的交互型面板优先级（原有口径，保持零回归）
_FALLBACK_PREF = ("programmer", "matrix", "effect", "linker", "clip")


def pick_target_panel(view, key, srcs) -> str:
    """该格双击应打开哪个面板（★ R138 的完整优先级）。

    0 来源 → `programmer`（设备通道值的默认归属页，与旧行为一致）
    1 来源 → 就是它
    多来源 → ① **真效果**（节点 `effect_id` 非空）优先；② 否则按交互型面板序。
    """
    srcs = tuple(srcs or ())
    if not srcs:
        return _section_panel(view, key) or "programmer"
    if len(srcs) == 1:
        return str(srcs[0])
    hit = effect_panel_of(view, key)
    if hit:
        return hit
    return next((p for p in _FALLBACK_PREF if p in srcs), str(srcs[0]))


def _section_panel(view, key):
    """该格所属**面板分区**的 id；非面板分区（汇总/当前选中）→ ""。"""
    try:
        sec = view._cells.get(key, (None, None))[0]
        pid = str(getattr(sec, "key", "") or "") if sec is not None else ""
    except Exception:
        return ""
    return pid if pid and pid not in _NON_PANEL_SECTIONS else ""


#: 不是面板 id 的分区键（与 `table_model` 的常量一致）
_NON_PANEL_SECTIONS = ("__summary__", "__selection__")
