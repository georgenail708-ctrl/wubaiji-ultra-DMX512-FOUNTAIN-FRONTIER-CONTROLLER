# -*- coding: utf-8 -*-
"""场景节点 `state` 的**池化去重**（R140 体积优化，从 `effect_cache` 拆出）。

用户实报（2026-09-21）::

    "一个项目节目做多了之后…clip 也比较多了，它的保存文件会特别大…我需要你优化
     项目保存形式，让我的软件保存的项目轻量化。"

## 实测（真工程 `E:\水仙十字.dmxt`，213.6 MB）

`dispatch_state` 占 **79.8 MB（37%）**：一条场景的 `nodes` 里每个节点都**完整复制一份
面板 state**，而同一场景内绝大多数节点是同面板同状态 ——
385 个节点只对应 **21 个唯一 state**（18 个被重复引用 382 次）→ **95% 是重复副本**。

## 设计

写：`pool_states()` 把唯一 state 提成 `states` 列表（内容寻址），
    `nodes[i]["s"]` = 池下标；`state` 键不再逐节点内联。
读：`resolve_state()` 同时认**旧格式**（内联 `state`）与**新格式**（`s` + `states`），
    且返回**独立副本**（`dict(...)`）—— 各节点互不共享可变对象，面板改自己那份不影响别人。
"""
from __future__ import annotations

import json
from typing import List, Tuple


def _pool_key(state) -> str:
    """state 的**内容寻址键**。非 dict → 空串（归到同一个空 state）。

    为什么用内容而不用 id：各节点持有的是**独立 dict 副本**，id 每次都不同；
    只有内容才能识别"这 300 个节点其实是同一个状态"。`sort_keys=True` 保证键序无关。
    """
    if not isinstance(state, dict):
        return ""
    try:
        return json.dumps(state, ensure_ascii=False, sort_keys=True)
    except Exception:
        return "noid-" + str(id(state))


def pool_states(nodes) -> Tuple[List[dict], List[dict]]:
    """`nodes` → `(entries, pool)`：entries 去掉内联 state、改带 `s` 下标。"""
    entries: List[dict] = []
    pool: List[dict] = []
    index: dict = {}
    for e in (nodes or []):
        item = {k: v for k, v in dict(e).items() if k != "state"}
        st = (e or {}).get("state")
        key = _pool_key(st)
        idx = index.get(key)
        if idx is None:
            idx = len(pool)
            index[key] = idx
            pool.append(st if isinstance(st, dict) else {})
        item["s"] = idx
        entries.append(item)
    return entries, pool


def resolve_state(entry: dict, pool) -> dict:
    """取该节点的 state（新格式按下标、旧格式内联）；始终返回**独立副本**。"""
    st = (entry or {}).get("state")
    if st is None and "s" in (entry or {}):
        try:
            i = int(entry.get("s"))
        except (TypeError, ValueError):
            return {}
        st = (pool[i] if isinstance(pool, list) and 0 <= i < len(pool) else None)
    return dict(st) if isinstance(st, dict) else {}


def restore_nodes(raw_nodes, states, node_from_dict) -> List[dict]:
    """存档 `nodes` → 运行期 `[{"node":..., "state":...}, ...]`（★ R140）。

    同时认两种格式：
      · 旧：逐节点内联 `state`
      · 新：`states` 池 + 节点 `s` 下标
    每个 state 都取**独立副本**；`node_from_dict` 由调用方注入（避免本模块
    依赖 dispatch 的效果节点模型 → 保持纯数据模块）。
    """
    out: List[dict] = []
    for e in (raw_nodes or []):
        if not isinstance(e, dict):
            continue
        node_d = e.get("node", {}) or {}
        st = resolve_state(e, states)
        out.append({"node": node_from_dict(node_d).to_dict(), "state": st})
    return out
