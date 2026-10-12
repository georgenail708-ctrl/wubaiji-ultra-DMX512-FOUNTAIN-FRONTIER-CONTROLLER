# -*- coding: utf-8 -*-
"""双击调度台格 → 唤起效果来源页面并**还原该效果的制作状态**。

存在理由（用户 2026-09-14 要求）：
  "你能够通过我点击这些通道**唤起它的效果来源的页面并且回到这个效果制作的状态**，
   programmer 需要对这个效果的翼块，条带，值等完全还原，effect 需要自动切换到这个
   效果的调出页面并且还原里面的每一个控件的状态，matrix 也是同理。"

分工（各文件都保持小、复杂度低 —— 架构门禁要求函数 ≤100 行 / CC ≤8）：
  · `build_recall_payload`  把"双击的格"翻译成 `RecallPayload`
  · `handle_focus_target`   打开面板所在 dock 并调 `面板.dispatch_recall_state`

各面板的 `dispatch_recall_state(node, state)` **已存在**（本次不重写其还原逻辑）：
  · effect    : 按 node 的 track_type/group_key 从 state.active_effects 取该格参数，
                还原参数/方向/切片/全局速度并刷可见控件
  · programmer: 切设备作用域 → 还原翼/块/翼方向/单控整体 → 写回数值（复用 CUE 通路）
  · matrix    : 按 node 切组后 `load_state`（模式 + 帧序列 + 像素参数）
  · linker    : `dispatch_recall_state` → `load_state`
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from ..dispatch_flow import _EDIT_AWARE_RECALL

_log = logging.getLogger(__name__)

#: 目标面板 id → 其所在 dock 的 MainWindow 属性名（用于打开对应面板）
_PANEL_DOCKS = (
    ("programmer", "_programmer_dock"),
    ("matrix", "_matrix_pattern_dock"),
    ("effect", "_effect_browser_dock"),
    ("linker", "_processor_dock"),
)


@dataclass
class RecallPayload:
    """一次"唤起并还原"所需的全部信息。"""
    panel_id: str = ""
    fids: List[str] = field(default_factory=list)
    node: Dict[str, Any] = field(default_factory=dict)
    state: Dict[str, Any] = field(default_factory=dict)


def _try(fn, default=None):
    """执行 `fn()`；任何异常返回 `default`（唤起路径全部容错，绝不打断 UI）。"""
    try:
        return fn()
    except Exception:
        return default


def _cell_of(panel, key):
    """格键 → `TableCell`（取不到 None）。"""
    try:
        pair = panel.view._cells.get(tuple(key), (None, None))
    except Exception:
        return None
    return pair[1] if isinstance(pair, tuple) and len(pair) > 1 else None


def _all_nodes(panel) -> List[Any]:
    """全部总线节点（优先视图缓存的按面板索引，缺失时回落到全局总线）。"""
    by_panel = getattr(getattr(panel, "view", None), "_nodes_by_panel", None) or {}
    nodes = [n for group in by_panel.values() for n in (group or ())]
    if nodes:
        return nodes
    try:
        from ...processing.dispatch import EffectBus
        return list(EffectBus.instance().cells() or [])
    except Exception:
        return []


def node_of_cell(panel, key, panel_id: str = ""):
    """格 → 对应总线节点（"这个值是谁写的" → 双击路由到来源面板）。

    ★★ 2026-09-14 修正（双击不唤起的真因）：原实现按
      `view._nodes_by_panel[sec.key]` 找节点，而监控区现在是**"当前选中设备"表**
      （分区 key = `__selection__`，不是面板 id）→ 永远查不到 → 双击静默无效。
      现改为**跨所有面板**搜索。

    `panel_id` 非空时**只接受同面板节点**：目标面板由格的真实层归属（`_sources`）
    算出，拿别的面板的 node 去还原会用错设备集（张冠李戴）。

    选择优先级（用户规则："默认无效果唤起 programmer；已有写入则按规则唤起"）：
      ① 效果类格（无 ch_type）：节点 `cell_key` 的列与格列一致（最精确）；
      ② 通道格：同面板中 `track_type` 与 ch_type 一致者
         （Programmer 节点的 track_type 就是通道类型）；
      ③ 兜底：同面板中任一**覆盖该设备**的节点；取不到 → None
         （回落给 programmer 的"只推设备选择"路径，绝不乱还原别的效果）。
    """
    cell = _cell_of(panel, key)
    if cell is None:
        return None
    nodes = _nodes_of_panel(panel, panel_id)
    if not nodes:
        return None
    by_col = _match_by_col_key(cell, nodes)
    if by_col is not None:
        return by_col
    return _match_covering(cell, nodes, key)


def _nodes_of_panel(panel, panel_id: str) -> List[Any]:
    """该面板的节点集（`panel_id` 为空则全部面板）。"""
    nodes = _all_nodes(panel)
    want = str(panel_id or "")
    if not want:
        return nodes
    # 同面板无节点 → 该格与任何节点无关（如 programmer 静态值）
    return [n for n in nodes if str(getattr(n, "panel", "") or "") == want]


def _match_by_col_key(cell, nodes: List[Any]):
    """效果类格（无 ch_type）：按节点身份列键精确匹配；不适用/未命中 → None。"""
    if str(getattr(cell, "ch_type", "") or ""):
        return None
    want = str(getattr(cell, "col_ref", "") or getattr(cell, "col_key", ""))
    for n in nodes:
        if _node_col_key(n) == want:
            return n
    return None


def _match_covering(cell, nodes: List[Any], key):
    """通道格：优先 `track_type` == ch_type 者，否则任一覆盖该设备的节点。"""
    fid = str(key[0])
    covering = [n for n in nodes
                if fid in {str(x) for x in (getattr(n, "fixture_ids", None) or ())}]
    ch_type = str(getattr(cell, "ch_type", "") or "")
    if ch_type:
        for n in covering:
            if str(getattr(n, "track_type", "") or "") == ch_type:
                return n
    return covering[0] if covering else None


def _node_col_key(node) -> str:
    """节点身份列键 `panel|track_type|group_key`（与 cell_key 同构）。"""
    try:
        panel, tt, gk = node.cell_key()
    except Exception:
        panel = str(getattr(node, "panel", "") or "")
        tt = str(getattr(node, "track_type", "") or "")
        gk = str(getattr(node, "group_key", "") or "")
    return f"{panel}|{tt}|{gk}"


def _cell_fids(panel, key) -> List[str]:
    """格 → 设备 id 列表（聚合格取全组；拿不到则格主自身）。"""
    pair = _try(lambda: panel.view._cells.get(tuple(key), (None, None)), (None, None))
    cell = pair[1] if isinstance(pair, tuple) and len(pair) > 1 else None
    if cell is None:
        return [str(key[0])]
    fids = [str(f) for f in (getattr(cell, "fids", None) or ())]
    return fids or [str(getattr(cell, "fixture_id", "") or "")]


def _node_dict_of(panel, key, panel_id: str = "") -> Dict[str, Any]:
    """格 → 节点 dict。`panel_id` 非空时只接受**同面板**的节点（防张冠李戴）。"""
    node = _try(lambda: node_of_cell(panel, key, panel_id))
    if node is None:
        return {}
    return _try(lambda: dict(node.to_dict() or {}), {}) or {}


def _target_panel(panel, key, panel_id: str) -> tuple[str, Dict[str, Any]]:
    """解析 `(目标面板 id, 同源节点 dict)`。

    面板优先用调用方给的 `panel_id`（= 格的真实层归属所决定）；为空才回落到
    节点自报的 `panel`。节点由 `node_of_cell` 保证与目标面板同源。
    """
    target = str(panel_id or "")
    node = _node_dict_of(panel, key, target)
    if not target:
        target = str(node.get("panel") or "") or "programmer"
    return target, node


def build_recall_payload(panel, key, panel_id: str) -> RecallPayload:
    """双击的格 → `RecallPayload`。

    · 目标面板以**格来源**为准（用户规则："默认无效果自动唤起 programmer；
      已有效果写入则按规则唤起对应面板"）：`panel_id` 由
      `table_view.target_panel_for` 从**真实混音层归属**（`_sources`）算出，
      精确反映"这格的值是谁写的"；只有它拿不到时才回落到节点自报的 `panel`。
    · 节点必须与目标面板**同源**（否则会把 A 面板的 node 喂给 B 面板的 recall，
      还原出错误设备集）——由 `node_of_cell(key, panel_id)` 保证。
    · 设备集优先取**该效果自身的 fixture_ids**（用户："选中 30 台但实际只编辑其中
      15 台" → 唤起的应是那 15 台）；拿不到才回落到行聚合设备。
    · `state` 取该面板**当前状态快照**（`_capture_state`）——效果此刻正在运行，
      当前状态就是"这个效果制作的状态"。
    """
    target, node = _target_panel(panel, key, panel_id)
    own = [str(x) for x in (node.get("fixture_ids") or []) if str(x)]
    cap = getattr(panel, "_capture_state", None)
    state = _try(lambda: dict(cap(target) or {}), {}) if cap else {}
    return RecallPayload(panel_id=target, fids=(own or _cell_fids(panel, key)),
                        node=node, state=(state or {}))


def _open_dock(main_window, widget) -> None:
    """把目标面板所在的 dock 显示出来（widget 已由调用方确保非空）。"""
    for _pid, attr in _PANEL_DOCKS:
        dock = getattr(main_window, attr, None)
        if dock is not None and _try(lambda: dock.widget() is widget):
            main_window._toggle_dock(dock, True)
            return


def _push_selection(main_window, panel_id: str, widget, ids: List[str]) -> None:
    """退化路径：只把设备选择推给面板（无 node/无 recall 方法时用）。"""
    if not ids:
        return
    if panel_id == "programmer":
        _try(lambda: main_window._load_programmer_from_topview_selection(
            ids, getattr(main_window, "_programmer_widget", None)))
    elif hasattr(widget, "load_fixture_group"):
        _try(lambda: widget.load_fixture_group("", ids, "调度台"))
    elif hasattr(widget, "set_fixtures"):
        _try(lambda: widget.set_fixtures(ids))


def _resolve_target(main_window, panel_id: str):
    """解析目标面板 → `(widget, recall)`；不可达返回 None。"""
    main_window._ensure_dispatch_panel(panel_id)
    target = (main_window._dispatch_panel_targets().get(panel_id) or {})
    widget = target.get("widget")
    if widget is None and panel_id != "programmer":
        return None
    return widget, target.get("recall")


def _invoke_recall_for_edit(rec, node: dict, state: dict) -> bool:
    """调面板 recall 的**编辑语义**入口（`for_edit=True`）；能力不足则两参旧契约。

    ★★ 2026-09-18 回访修正（R88 默认值把双击语义改掉了）：双击监控区格 = 用户原话
      "唤起它的效果来源的页面并且**回到这个效果制作的状态**"（= 编辑语义）。而 R88 给
      `dispatch_recall_state` 加了 `for_edit` 且**默认 False = 纯播放**，本函数仍以两参
      调用 → 面板一律走纯播放分支：`load_state` 整段跳过、控件纹丝不动。
      实测（真实主窗 + Effect 面板 + 真 `handle_focus_target`）：存档 global_speed=4.0，
      双击后 `load_state` **0 次调用**、spin 仍是用户的 1.0 —— 用户要的"回到制作状态"
      静默失效。本函数显式传 `for_edit=True`（能力位仍是 `dispatch_flow` 的单一真源）。
    """
    if not callable(rec):
        return False
    if not getattr(rec, _EDIT_AWARE_RECALL, False):
        return bool(rec(node, state))          # 未升级面板：两参旧契约
    try:
        return bool(rec(node, state, for_edit=True))
    except TypeError:                          # 手工绑定的回调不接关键字
        return bool(rec(node, state))


def handle_focus_target(main_window, payload: RecallPayload) -> bool:
    """打开目标面板并**还原该效果制作状态**；返回是否处理成功。

    主路径：`面板.dispatch_recall_state(node, state, for_edit=True)`（编辑语义，
    见 `_invoke_recall_for_edit`）。
    退化路径：只推设备选择（无 node、无 recall 方法、或 recall 抛错时）。
    """
    try:
        panel_id = str(payload.panel_id or "programmer")
        resolved = _resolve_target(main_window, panel_id)
        if resolved is None:
            return False
        widget, recall = resolved
        _open_dock(main_window, widget)
        if recall is not None and payload.node:
            try:
                if _invoke_recall_for_edit(recall, dict(payload.node),
                                           dict(payload.state)):
                    return True          # ★ 已还原到该效果的制作状态
            except Exception:
                _log.debug("dispatch recall_state failed", exc_info=True)
        _push_selection(main_window, panel_id, widget, payload.fids)
        return True
    except Exception:
        _log.debug("dispatch focus target failed", exc_info=True)
        return False


__all__ = ["RecallPayload", "build_recall_payload", "handle_focus_target",
           "node_of_cell"]

