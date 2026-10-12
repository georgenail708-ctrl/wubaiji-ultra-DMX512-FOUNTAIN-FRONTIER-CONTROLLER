# -*- coding: utf-8 -*-
"""Clip Studio sheet 图模型（R172 设计 §2.3/§3.3）—— 纯 dict schema + 模块级函数，无 Qt。

schema（唯一契约，设计 §2.3 / W1c 任务书）::

    节点  {"id", "key", "x", "y", "params": {...}}
          # R183 W2-ST 可选 UI 键：collapsed / children / disabled / text
          # （collapsed 纯 UI 层，编译器只读 —— 见 studio_collapse docstring）
    边    {"id", "from", "from_port", "to", "to_port"}   # port: in/out/composite
    框    {"id", "title", "x", "y", "w", "h", "color"}  # R183 Backdrop 分组框（可选键）
    sheet {"id", "name", "graph": {"nodes": [], "edges": [], "frames": []},
           "edge_style": "bezier"|"ortho"|"line", "created": float}   # 后两项可选
    state {"sheets": [...], "active_sheet": id, "view_mode": "single"|"grid"}

容错纪律：`normalize_state` 面向**手改过/旧版/畸形**存档数据 —— 任何输入都不得
抛异常；非法节点 key 剔除、悬空边剔除、缺失/重复 id 重排、坏值回退默认。
`frames` / `edge_style` / 节点 UI 键都是 R183 新增的**可选**键：旧档（无这些键）
在归一后按需补齐，绝不报错、绝不改语义（缺 `edge_style` = 贝塞尔 = 旧行为）。
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from src.core.group_selection import normalize_group_spec
from src.ui.clip_studio.studio_cards import CARD_CATALOG
from src.ui.clip_studio.studio_note import lookup_spec

__all__ = [
    "new_node", "new_edge", "new_sheet", "new_state",
    "normalize_state", "find_cycles", "graph_signature",
    "graph_parts", "node_group", "param_value", "edge_index", "card_targets",
    "frames_of", "spec_of",
]


def spec_of(key):
    """统一规格查询（效果目录 ∪ 便签伪卡）—— 图模型的唯一查表入口。"""
    return lookup_spec(key)


def frames_of(sheet) -> list:
    """sheet 的 Backdrop 分组框列表（**活引用**；缺键自动补空表 —— 旧档兼容）。

    实现点唯一在 `studio_frames`（几何/序列化同源），此处仅转出以免调用方
    为了一个取表动作 import 两个模块。"""
    from src.ui.clip_studio.studio_frames import frames_of as _frames_of
    return _frames_of(sheet)


def _safe_list(value) -> list:
    return value if isinstance(value, list) else []


def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def graph_parts(sheet) -> tuple:
    """(nodes, edges) 安全取出（非 dict 项剔除；缺 graph / 非 dict sheet → 空表）。"""
    graph = sheet.get("graph") if isinstance(sheet, dict) else None
    graph = graph if isinstance(graph, dict) else {}
    nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
    edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
    return nodes, edges


def node_group(node) -> str:
    """节点 → 效果大类 key（CardSpec.group；未知/便签 → ""）。"""
    spec = spec_of(node.get("key", "")) if isinstance(node, dict) else None
    return getattr(spec, "group", "") if spec is not None else ""


def param_value(node, key):
    """读节点参数（经 ParamSpec 类型收敛；spec 未收录的键原样返回）。"""
    spec = spec_of(node.get("key", "")) if isinstance(node, dict) else None
    ps = next((q for q in spec.params if q.key == key), None) if spec else None
    raw = (node.get("params") or {}).get(key, ps.default if ps else None)
    return _coerce_param(ps, raw) if ps is not None else raw


def edge_index(nodes, edges) -> dict:
    """按端点建索引：{"by_id", "src": {nid: [edge]}, "dst": {nid: [edge]}}（保持边序）。"""
    idx = {"by_id": {str(n.get("id")): n for n in nodes if n.get("id")},
           "src": {}, "dst": {}}
    for e in edges:
        idx["src"].setdefault(str(e.get("from", "")), []).append(e)
        idx["dst"].setdefault(str(e.get("to", "")), []).append(e)
    return idx


def card_targets(idx, nid, of_group=None, to_port=None) -> list:
    """某节点出边的（边, 目标节点）列表；可按目标大类/入桩过滤（保持边序）。"""
    out = []
    for e in idx["src"].get(nid, []):
        dst = idx["by_id"].get(str(e.get("to")))
        if dst is None:
            continue
        if of_group is not None and node_group(dst) != of_group:
            continue
        if to_port is not None and str(e.get("to_port", "")) != to_port:
            continue
        out.append((e, dst))
    return out


def _graph_of(raw) -> dict:
    graph = raw.get("graph") if isinstance(raw, dict) else None
    return graph if isinstance(graph, dict) else {}


# ── 构造 ────────────────────────────────────────────────────────────────────

def new_node(key: str, x: float = 0.0, y: float = 0.0, params: dict | None = None) -> dict:
    """新建一个卡片节点（key 须在 CARD_CATALOG 内，调用方负责）。"""
    return {"id": _gen_id("n"), "key": str(key), "x": float(x), "y": float(y),
            "params": dict(params) if isinstance(params, dict) else {}}


def new_edge(src_id: str, dst_id: str,
             from_port: str = "out", to_port: str = "in") -> dict:
    """新建一条边（桩位合法性由 normalize/compile 校验）。"""
    return {"id": _gen_id("e"), "from": str(src_id), "from_port": str(from_port),
            "to": str(dst_id), "to_port": str(to_port)}


def new_sheet(existing_names: Any = None) -> dict:
    """新建空 sheet，名 `Sheet N` 在 `existing_names`（可迭代/可含非串项）中唯一。"""
    if existing_names is None:
        used = set()
    elif isinstance(existing_names, (set, frozenset, list, tuple)):
        used = {str(n) for n in existing_names}
    else:
        used = {str(n) for n in _safe_list(existing_names)}
    n = 1
    while f"Sheet{n}" in used:
        n += 1
    # 注意：不预置 "frames" 键（R183）——旧契约 `new_sheet()["graph"] ==
    # {"nodes": [], "edges": []}` 被既有测试钉死；框键由 `frames_of`/归一按需补齐。
    return {"id": _gen_id("sheet"), "name": f"Sheet{n}",
            "graph": {"nodes": [], "edges": []}, "created": time.time()}


def new_state() -> dict:
    """空工程状态（单页模式，无 sheet）。"""
    return {"sheets": [], "active_sheet": "", "view_mode": "single"}


# ── 归一（容错旧档/畸形）────────────────────────────────────────────────────

def _coerce_int(value, spec):
    return int(value)


def _coerce_float(value, spec):
    return float(value)


def _coerce_bool(value, spec):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _coerce_choice(value, spec):
    sval = str(value)
    return sval if sval in [c[0] for c in spec.choices] else spec.default


def _coerce_spec(value, spec):
    return normalize_group_spec(value)


def _coerce_rows(value, spec):
    """主控过渡卡动态行表（R182）——归一在 `studio_master`（唯一实现点）。"""
    from src.ui.clip_studio.studio_master import normalize_rows
    return normalize_rows(value)


def _coerce_str(value, spec):
    return str(value)


#: 类型 → 收敛函数（表驱动：新增参数类型只加一行，不给 `_coerce_param` 涨 CC）。
_COERCERS = {
    "int": _coerce_int, "float": _coerce_float, "bool": _coerce_bool,
    "choice": _coerce_choice, "spec": _coerce_spec, "rows": _coerce_rows,
}


def _coerce_param(spec, value):
    """按 ParamSpec.type 收敛参数值（坏值回默认；归一分派见 `_COERCERS`）。"""
    try:
        return _COERCERS.get(str(spec.type), _coerce_str)(value, spec)
    except (TypeError, ValueError):
        return spec.default


def _norm_params(node: dict) -> dict:
    """只留该卡已知参数键并按类型收敛；params 非 dict → 空。"""
    spec = spec_of(node.get("key", ""))
    raw = node.get("params")
    if spec is None or not isinstance(raw, dict):
        return {}
    out = {}
    for ps in spec.params:
        if ps.key in raw:
            out[ps.key] = _coerce_param(ps, raw[ps.key])
    return out


#: 节点可选 UI 键（R183 W2-ST）：值非法 → 回默认；进签名（改折叠/禁用 → 落盘）。
_UI_NODE_KEYS = ("collapsed", "children", "disabled")


def _ui_keys(raw: dict) -> dict:
    """收敛节点 UI 键：collapsed/disabled 布尔；children 字符串表（缺键不写）。"""
    out = {}
    if "collapsed" in raw:
        out["collapsed"] = bool(raw.get("collapsed"))
    if "disabled" in raw:
        out["disabled"] = bool(raw.get("disabled"))
    if isinstance(raw.get("children"), list):
        out["children"] = [str(c) for c in raw["children"][:256]
                           if isinstance(c, (str, int, float))]
    return out


def _norm_nodes(sheet: dict) -> list:
    """剔除非法节点 key；缺失/重复 id 重排；x/y 与 params 收敛；UI 键透传收敛。"""
    nodes, used = [], set()
    for raw in _safe_list(_graph_of(sheet).get("nodes")):
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("key", ""))
        if spec_of(key) is None:
            continue
        nid = str(raw.get("id", "") or "")
        if (not nid) or nid in used:
            nid = _gen_id("n")
        used.add(nid)
        try:
            x, y = float(raw.get("x", 0.0)), float(raw.get("y", 0.0))
        except (TypeError, ValueError):
            x, y = 0.0, 0.0
        node = {"id": nid, "key": key, "x": x, "y": y, "params": _norm_params(raw)}
        node.update(_ui_keys(raw))
        nodes.append(node)
    return nodes


def _edge_valid(raw, by_id: dict, ports: dict) -> bool:
    """边有效性：两端节点存在（非悬空）且桩位合法。"""
    if not isinstance(raw, dict):
        return False
    src, dst = str(raw.get("from", "")), str(raw.get("to", ""))
    if src not in by_id or dst not in by_id:
        return False
    fp, tp = str(raw.get("from_port", "out")), str(raw.get("to_port", "in"))
    return fp in ports[src] and tp in ports[dst]


def _norm_edges(raw_edges, nodes: list) -> list:
    """剔除悬空边与非法桩位（from_port/to_port 必须是两端卡片的已有桩）。"""
    by_id = {n["id"]: n for n in nodes}
    ports = {n["id"]: tuple(spec_of(n["key"]).ports) for n in nodes}
    edges, used = [], set()
    for raw in _safe_list(raw_edges):
        if not _edge_valid(raw, by_id, ports):
            continue
        eid = str(raw.get("id", "") or "")
        if (not eid) or eid in used:
            eid = _gen_id("e")
        used.add(eid)
        edges.append({"id": eid, "from": str(raw["from"]),
                      "from_port": str(raw.get("from_port", "out")),
                      "to": str(raw["to"]), "to_port": str(raw.get("to_port", "in"))})
    return edges


def _norm_sheet(raw: Any, index: int, used_ids: set) -> dict:
    """单 sheet 归一：id 唯一化、name/graph 兜底、created 收敛、frames 可选键补齐。"""
    sheet = raw if isinstance(raw, dict) else {}
    sid = str(sheet.get("id", "") or "")
    if (not sid) or sid in used_ids:
        sid = _gen_id("sheet")
    used_ids.add(sid)
    name = str(sheet.get("name", "") or "").strip() or f"Sheet{index + 1}"
    try:
        created = float(sheet.get("created", 0.0))
    except (TypeError, ValueError):
        created = 0.0
    nodes = _norm_nodes(sheet)
    edges = _norm_edges(_graph_of(sheet).get("edges"), nodes)
    from src.ui.clip_studio.studio_frames import normalize_frames
    frames = normalize_frames(_graph_of(sheet).get("frames"))
    out = {"id": sid, "name": name,
           "graph": {"nodes": nodes, "edges": edges, "frames": frames},
           "created": created if created > 0 else time.time()}
    _norm_edge_style(sheet, out)
    return out


def _norm_edge_style(raw_sheet: dict, out: dict) -> None:
    """连线样式（R183 可选键）：合法才写回；缺键/坏值 → 不写（默认贝塞尔=旧行为）。"""
    from src.ui.clip_studio.studio_edge_style import EDGE_STYLES
    style = str(raw_sheet.get("edge_style") or "")
    if style in EDGE_STYLES:
        out["edge_style"] = style


def normalize_state(state: Any) -> dict:
    """把任意输入收敛成规范 state；永不抛异常，不修改入参。

    * 非 dict → 空状态；sheets 非列表 → 逐项容错（非 dict 项给空 sheet）。
    * 节点：非法 key 剔除；id 缺失/重复 → 重排；params 只留已知键并按类型收敛。
    * 边：端点缺失（悬空）、桩位非法 → 剔除；id 缺失/重复 → 重排。
    * active_sheet 不在 sheets 里 → 首个 sheet id（无 sheet → ""）；
      view_mode 非 "grid" → "single"。
    """
    sheets_raw = state.get("sheets") if isinstance(state, dict) else None
    sheets, used_ids = [], set()
    for i, raw in enumerate(_safe_list(sheets_raw)):
        sheets.append(_norm_sheet(raw, i, used_ids))
    ids = [s["id"] for s in sheets]
    active = str(state.get("active_sheet", "") or "") if isinstance(state, dict) else ""
    if active not in ids:
        active = ids[0] if ids else ""
    view = "grid" if isinstance(state, dict) and state.get("view_mode") == "grid" else "single"
    return {"sheets": sheets, "active_sheet": active, "view_mode": view}


# ── 环检测 / 变更签名 ───────────────────────────────────────────────────────

def _dfs_step(frame, stack: list, adj: dict, color: dict, path: list) -> tuple:
    """从栈顶帧推进一步：白目标 → 压栈(返回 True)；灰目标 → 记环(返回 (False, 环))。

    返回 `(advanced, cycle_or_None)`；iterator 消费进度保存在 stack 帧里，回帧续走。
    """
    _node, it = frame
    for nxt in it:
        nxt = str(nxt)
        if nxt not in color:
            continue
        if color[nxt] == 0:
            color[nxt] = 1
            path.append(nxt)
            stack.append((nxt, iter(adj.get(nxt, ()))))
            return True, None
        if color[nxt] == 1:
            return False, list(path[path.index(nxt):])
    return False, None


def _dfs_visit(start, adj: dict, color: dict, cycles: list) -> None:
    """从 start 出发的迭代式 DFS（三色标记：0 白 / 1 灰在栈 / 2 黑完成）。"""
    color[start] = 1
    path = [start]
    stack = [(start, iter(adj.get(start, ())))]
    while stack:
        frame = stack[-1]
        advanced, cycle = _dfs_step(frame, stack, adj, color, path)
        if cycle is not None:
            cycles.append(cycle)
        if not advanced:
            color[frame[0]] = 2
            path.pop()
            stack.pop()


def _collect_cycles(node_ids: list, adj: dict) -> list:
    """对每个未访起点做 DFS；每条回边对应输出一个环（节点 id 列表，按环序）。"""
    color = {n: 0 for n in node_ids}
    cycles: list = []
    for start in node_ids:
        if not color[start]:
            _dfs_visit(start, adj, color, cycles)
    return cycles


def find_cycles(graph: Any) -> list:
    """环检测（V4）：返回每个回边对应的环（节点 id 列表，按环序）；无环 → []。

    对整张图（任意桩位的边）做检测；畸形 graph / 非法端点一律忽略。
    """
    graph = graph if isinstance(graph, dict) else {}
    node_ids = [str(n.get("id")) for n in _safe_list(graph.get("nodes"))
                if isinstance(n, dict) and n.get("id")]
    known = set(node_ids)
    adj: dict = {}
    for e in _safe_list(graph.get("edges")):
        if not isinstance(e, dict):
            continue
        src, dst = str(e.get("from", "")), str(e.get("to", ""))
        if src in known and dst in known:
            adj.setdefault(src, []).append(dst)
    return _collect_cycles(node_ids, adj)


def _canon_nodes(graph: dict) -> list:
    out = []
    for n in _safe_list(graph.get("nodes")):
        if not isinstance(n, dict):
            continue
        params = n.get("params")
        out.append([str(n.get("id", "")), str(n.get("key", "")),
                    round(float(n.get("x", 0.0) or 0.0), 3),
                    round(float(n.get("y", 0.0) or 0.0), 3),
                    sorted((k, json.dumps(v, sort_keys=True, ensure_ascii=False, default=str))
                           for k, v in params.items()) if isinstance(params, dict) else [],
                    # R183 W2-ST UI 键进签名：折叠/禁用/子图变动 → 触发落盘与撤销判定
                    bool(n.get("collapsed")), bool(n.get("disabled")),
                    [str(c) for c in n.get("children")] if isinstance(n.get("children"), list) else []])
    return sorted(out, key=lambda t: t[0])


def _canon_edges(graph: dict) -> list:
    out = []
    for e in _safe_list(graph.get("edges")):
        if not isinstance(e, dict):
            continue
        out.append([str(e.get("id", "")), str(e.get("from", "")),
                    str(e.get("from_port", "")), str(e.get("to", "")),
                    str(e.get("to_port", ""))])
    return sorted(out, key=lambda t: t[0])


def _canon_frames(graph: dict) -> list:
    """Backdrop 分组框的 canonical 形态（拖动/改名/改色/增删都进签名 → 触发落盘）。"""
    out = []
    for f in _safe_list(graph.get("frames")):
        if not isinstance(f, dict):
            continue
        try:
            x, y = round(float(f.get("x", 0.0) or 0.0), 3), round(float(f.get("y", 0.0) or 0.0), 3)
            w, h = round(float(f.get("w", 0.0) or 0.0), 3), round(float(f.get("h", 0.0) or 0.0), 3)
        except (TypeError, ValueError):
            x = y = w = h = 0.0
        out.append([str(f.get("id", "")), str(f.get("title", "")),
                    str(f.get("color", "")), x, y, w, h])
    return sorted(out, key=lambda t: t[0])


def graph_signature(graph: Any) -> str:
    """图 → 稳定短签名（防抖 300ms 变更检测用）：内容不变 ⇒ 签名不变。

    节点/边/分组框各自按 id 排序后 canonical JSON 再取 md5；位置 x/y 保留 3 位小数。
    """
    graph = graph if isinstance(graph, dict) else {}
    payload = json.dumps({"n": _canon_nodes(graph), "e": _canon_edges(graph),
                          "f": _canon_frames(graph)},
                         sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), default=str)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()
