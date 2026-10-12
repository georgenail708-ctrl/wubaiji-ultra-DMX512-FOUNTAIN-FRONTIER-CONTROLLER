# -*- coding: utf-8 -*-
"""Clip Studio 画布剪贴板（Shift+D 原地复制 / Ctrl+C·V 剪贴板，R178 收尾项）。

从 `studio_canvas_interact` 拆出（该文件贴着 500 行门禁）。操作宿主画布 `cv`
的 `_sheet/_sel_nodes/_sel_node`；批操作**单次 graph_changed**（一条撤销记录，
R173 Group2 同口径）。内部连线随剪贴板走：复制带"选中集内部边"，粘贴按新旧
id 映射重接。
"""
from __future__ import annotations

import copy

from src.ui.clip_studio.studio_graph import _gen_id

_OFFSET_DUP = 40.0    # Shift+D 副本偏移（世界单位）
_OFFSET_PASTE = 60.0  # Ctrl+V 粘贴偏移


def selected_node_ids(cv) -> set:
    """当前选中卡 id 集（多选优先，单选兜底）。"""
    return set(cv._sel_nodes) or ({cv._sel_node} if cv._sel_node else set())


def duplicate_selected(cv) -> None:
    """Shift+D：选中卡深拷贝（新 id、偏移 _OFFSET_DUP），选中新卡。"""
    nids = selected_node_ids(cv)
    if not nids:
        return
    graph = cv._sheet["graph"]
    nodes = graph.setdefault("nodes", [])
    by_id = {str(n.get("id")): n for n in nodes}
    new_sel = set()
    for nid in nids:
        src = by_id.get(nid)
        if src is None:
            continue
        clone = copy.deepcopy(src)
        clone["id"] = _gen_id("n")
        clone["x"] = float(src.get("x", 0.0)) + _OFFSET_DUP
        clone["y"] = float(src.get("y", 0.0)) + _OFFSET_DUP
        nodes.append(clone)
        new_sel.add(str(clone["id"]))
    _finish_batch_add(cv, new_sel)


def copy_selected(cv) -> None:
    """Ctrl+C：选中卡（连同它们之间的内部连线）进画布剪贴板。"""
    nids = selected_node_ids(cv)
    if not nids:
        return
    graph = cv._sheet["graph"]
    by_id = {str(n.get("id")): n for n in graph.get("nodes", [])}
    nodes = [copy.deepcopy(by_id[n]) for n in nids if n in by_id]
    edges = [copy.deepcopy(e) for e in graph.get("edges", [])
             if str(e.get("from")) in nids and str(e.get("to")) in nids]
    cv._studio_clipboard = {"nodes": nodes, "edges": edges}


def paste_clipboard(cv) -> None:
    """Ctrl+V：剪贴板克隆到偏移处（连线按 id 映射重接）。"""
    clip = getattr(cv, "_studio_clipboard", None)
    if not clip or not clip.get("nodes"):
        return
    graph = cv._sheet["graph"]
    nodes = graph.setdefault("nodes", [])
    id_map = {}
    new_sel = set()
    for src in clip["nodes"]:
        clone = copy.deepcopy(src)
        old = str(clone.get("id"))
        clone["id"] = _gen_id("n")
        clone["x"] = float(src.get("x", 0.0)) + _OFFSET_PASTE
        clone["y"] = float(src.get("y", 0.0)) + _OFFSET_PASTE
        id_map[old] = str(clone["id"])
        nodes.append(clone)
        new_sel.add(str(clone["id"]))
    edges = graph.setdefault("edges", [])
    for e in clip.get("edges", []):
        ne = copy.deepcopy(e)
        ne["id"] = _gen_id("e")
        ne["from"] = id_map.get(str(e.get("from")), e.get("from"))
        ne["to"] = id_map.get(str(e.get("to")), e.get("to"))
        edges.append(ne)
    _finish_batch_add(cv, new_sel)


def _finish_batch_add(cv, new_sel: set) -> None:
    """批量添加收尾：选中新卡 + 单次 graph_changed + 重绘。"""
    if not new_sel:
        return
    cv._clear_selection()
    cv._sel_nodes = set(new_sel)
    cv._sel_node = next(iter(new_sel))
    cv.selection_changed.emit(cv._sel_node)
    cv.graph_changed.emit()
    cv.update()
