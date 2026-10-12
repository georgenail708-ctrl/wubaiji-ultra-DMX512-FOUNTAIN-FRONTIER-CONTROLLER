# -*- coding: utf-8 -*-
"""src/ui/effect_browser_w2_codec.py — R183 W2-FX 预设包（#9）的**纯编解码**。

把「我的效果」打包成可分享的 JSON 文档，以及反向解析/合并规划 —— 全部是纯函数
（无 Qt、无 IO），便于定测直接对拍；写盘与对话框在 ``effect_browser_w2_pack``。

文档形态::

    {"kind": "qascade.effect_w2_state.preset_pack", "version": 1,
     "exported": <unix ts>, "meta": {...}, "presets": [{"name", "params",
                                                        "id", "category",
                                                        "description", "tags"}]}

★ 何时不用现成方案：预设交换没有通用格式标准（GDTF 面向灯具定义而非参数快照），
  本仓 ``EffectPreset.to_dict`` 只给默认值不含快照 —— 故自建这层极薄的 JSON 编解码。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 档 schema 标识（与用户档同前缀；``.preset_pack`` 后缀区分"包"与"档"）
PACK_KIND = "qascade.effect_w2_state.preset_pack"
#: 包版本
PACK_VERSION = 1
#: 标签约束（与用户档一致；防病态包）
MAX_TAGS_PER_PRESET = 32
MAX_TAG_LEN = 24


def clean_tags(tags) -> List[str]:
    """标签规范化：去空白/去重/限长限量（保序，绝不抛）。"""
    out: List[str] = []
    seen = set()
    for tag in (tags or []):
        text = str(tag or "").strip()[:MAX_TAG_LEN]
        if text and text not in seen:
            seen.add(text)
            out.append(text)
        if len(out) >= MAX_TAGS_PER_PRESET:
            break
    return out


def entry_parts(entry) -> Optional[tuple]:
    """条目的 ``(名称, 参数)``；非 dict 或名称/参数缺失 → None。"""
    if not isinstance(entry, dict):
        return None
    params = entry.get("params")
    if not isinstance(params, dict):
        return None
    name = str(entry.get("name", "") or "").strip()
    return (name, params) if name else None


def pack_entry(entry) -> Optional[dict]:
    """一条包条目（名称/参数缺失 → None = 丢弃）。"""
    parts = entry_parts(entry)
    if parts is None:
        return None
    name, params = parts
    rec = {"name": name, "params": dict(params),
           "id": str(entry.get("id", "") or ""),
           "category": str(entry.get("category", "") or ""),
           "description": str(entry.get("description", "") or "")}
    tags = clean_tags(entry.get("tags"))
    if tags:
        rec["tags"] = tags
    return rec


def pack_payload(entries: Sequence, meta: Optional[dict] = None) -> dict:
    """构造预设包文档（非法条目静默丢弃 —— 导出绝不因一条脏数据整体失败）。"""
    out = [rec for rec in (pack_entry(e) for e in (entries or [])) if rec]
    doc = {"kind": PACK_KIND, "version": PACK_VERSION,
           "exported": float(time.time()), "presets": out}
    if isinstance(meta, dict):
        doc["meta"] = {str(k): str(v) for k, v in meta.items()}
    return doc


def parse_pack(raw) -> Tuple[List[dict], List[str]]:
    """解析预设包文档 → (条目表, 错误表)；整档非法 → ([], [原因])。"""
    if not isinstance(raw, dict):
        return [], ["不是 JSON 对象"]
    items = raw.get("presets")
    if not isinstance(items, list):
        return [], ["缺少 presets 列表"]
    out: List[dict] = []
    errors: List[str] = []
    for i, entry in enumerate(items):
        rec = pack_entry(entry)
        if rec is None:
            errors.append(f"第 {i + 1} 项不是对象或缺少名称/参数")
            continue
        rec.setdefault("tags", [])
        out.append(rec)
    return out, errors


def unique_name(base: str, taken) -> str:
    """在 ``taken`` 里找一个未占用的名字（``base`` 可用则原样返回）。"""
    if base not in taken:
        return base
    return _suffixed(base, taken)


def _suffixed(base: str, taken) -> str:
    """按 ``(导入N)`` 递增找一个未占用名（N 从 1 起）。"""
    n = _first_free_index(base, taken)
    return f"{base} (导入{n})"


def _first_free_index(base: str, taken) -> int:
    n = 1
    while base in taken and f"{base} (导入{n})" in taken:
        n += 1
    return n


def plan_merge(entries: Sequence, existing_names: Sequence,
               existing_ids: Sequence = ()) -> List[Tuple[dict, str]]:
    """合并计划：返回 ``[(条目, 目标名)]``；``目标名 == ""`` 表示跳过。

    - 同 ``id`` 已在库 → 跳过（同一预设重复导入不做覆盖，保守侧）；
    - 同名已占用 → 追加 " (导入1)" / " (导入2)" … 直到不冲突。
    """
    names = {str(n) for n in (existing_names or [])}
    ids = {str(i) for i in (existing_ids or []) if str(i)}
    plan: List[Tuple[dict, str]] = []
    for entry in (entries or []):
        if _skip_entry(entry, names, ids):
            plan.append((entry, ""))
            continue
        name = unique_name(str(entry.get("name", "") or "").strip(), names)
        names.add(name)
        plan.append((entry, name))
    return plan


def _skip_entry(entry, names, ids) -> bool:
    """该条目是否跳过（无名 / 同 id 已在库）。"""
    base = str(entry.get("name", "") or "").strip()
    if not base or base in names and False:
        return True
    eid = str(entry.get("id", "") or "")
    return bool(eid) and eid in ids


__all__ = ["MAX_TAGS_PER_PRESET", "MAX_TAG_LEN", "PACK_KIND", "PACK_VERSION",
           "clean_tags", "pack_entry", "pack_payload", "parse_pack",
           "plan_merge", "unique_name"]
_ = Any, Dict
