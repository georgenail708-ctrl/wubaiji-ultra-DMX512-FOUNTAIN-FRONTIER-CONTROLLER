"""Immutable inputs for Matrix's existing fixture compilation cache.

Reuse the original fixture projection, definition serializer and config. The
key never serializes tracks/recordings, and retains no mutable project objects.
Show identity and output retirement stay with the existing stage/scope owners.

R194/P2-18 性能定案：payload 里 `definitions` / `output_definitions` 原本对
**全部**设备定义各做一遍 `to_dict()` + 整表 json.dumps —— 每次 TopView 选择
变化都全额支付（4000 台实测 10-50ms 级）。现改为：

  · `project_channels.def_content_token(fd)`（覆盖 to_dict 全字段，patch 面板
    就地改字段也能判变）为键，**模块级缓存每个 def 的 to_dict 序列化片段**
    （FIFO 容量上限，进程级复用）；
  · 签名输出字符串**逐字节不变**：json(sort_keys) 已定，列表序列化 =
    `"[" + ",".join(逐项序列化) + "]"`、对象同理（见 `_dump_payload_with_defs`
    的等价性证明），缓存命中与现算完全等价。
"""
from __future__ import annotations

import json

from ..project_channels import def_content_token, defs_of

_POSITION_FIELDS = ("id", "name", "universe", "start_channel", "pos_x", "pos_y", "def_id", "channels")

#: per-def to_dict **序列化片段**缓存（键 = def_content_token）。FIFO 上限：
#: 4000 台工程 ≈ 几十个 def（多实例共享同 def），1024 足够且防换工程后无界增长。
_DEFDUMP_CACHE: dict = {}
_DEFDUMP_CAP = 1024


def _dump_definition(definition) -> str:
    """单个定义的 to_dict JSON 片段（与整表 dumps 中它出现的形式逐字节相同）。"""
    return json.dumps(definition.to_dict(), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"))


def _definition_json(definition) -> str:
    """带缓存的 `_dump_definition`；token 命中即零 `to_dict()`、零序列化。

    token 覆盖 to_dict 全部字段（见 `def_content_token`）→ 缓存**命中必等价、
    字段被改必失效**。定义畸形（token/to_dict 抛）时退回直序列化：与旧实现
    同一异常面，且不把坏结果写进缓存。
    """
    try:
        token = def_content_token(definition)
    except Exception:
        return _dump_definition(definition)
    hit = _DEFDUMP_CACHE.get(token)
    if hit is not None:
        return hit
    frag = _dump_definition(definition)
    _DEFDUMP_CACHE[token] = frag
    if len(_DEFDUMP_CACHE) > _DEFDUMP_CAP:
        for k in list(_DEFDUMP_CACHE)[: len(_DEFDUMP_CACHE) - _DEFDUMP_CAP]:
            _DEFDUMP_CACHE.pop(k, None)
    return frag


def _definitions_json(definitions) -> str:
    """定义列表 → JSON 数组文本（逐片段拼接，与整表 dumps 逐字节等价）。"""
    return "[" + ",".join(_definition_json(d) for d in (definitions or [])) + "]"


def _dump_payload_with_defs(payload: dict, def_frags: dict) -> str:
    """payload + **预序列化**的定义片段 → 最终签名字符串。

    等价性（对 `json.dumps({**payload, **解析后的 def_frags}, ensure_ascii=False,
      sort_keys=True, separators=(",", ":"))` 逐字节成立）：
      · sort_keys 按**键字符串**字典序排列 —— 这里对同一批键 sorted() 结果相同；
      · 对象序列化 = `"{" + ",".join(键序列化 + ":" + 值序列化) + "}"`，键的
        序列化与值递归选项一致（嵌套 dict 同样 sort_keys）；
      · 数组序列化 = `"[" + ",".join(逐项序列化) + "]"` —— `_definitions_json`
        的拼接正是它。
    """
    parts = []
    for key in sorted(list(payload.keys()) + list(def_frags.keys())):
        frag = def_frags.get(key)
        if frag is None:
            frag = json.dumps(payload[key], ensure_ascii=False,
                              sort_keys=True, separators=(",", ":"))
        parts.append(json.dumps(str(key), ensure_ascii=False) + ":" + frag)
    return "{" + ",".join(parts) + "}"


def _same_defs(a: list, b: list) -> bool:
    """两个定义列表是否**逐元素同一对象**（O(n) 指针比较，首个不同即短路）。"""
    return len(a) == len(b) and all(x is y for x, y in zip(a, b))


def fixture_group_signature(panel, group, fixture_ids, pm, positions):
    """Include both supplied positions and the original PM enrichment fallback.

    R194/P2-18：两个定义表不再各自 `to_dict()` 全量序列化，走 per-def 片段
      缓存（`_definition_json`）；两表元素同一对象时（同工程常态：pm.project 与
      `_project_for_matrix()` 是同一份 defs）只序列化一遍、两键复用。
      签名输出与旧实现逐字节相同（见模块头）。
    """
    source = pm or getattr(panel, "_pm", None)
    project = getattr(source, "project", None) or getattr(source, "_project", None) or source
    config = panel._engine.config
    payload = {
        "group": group,
        "ids": [str(fid) for fid in (fixture_ids or [])],
        "skip_base_order": bool(getattr(panel, "_skip_base_order", False)),
        "positions": [_position_input(row) for row in (positions or [])],
        "fallback": panel._positions_from_pm(pm),
        "channel_profiles": config.channel_profiles,
        "output_gamma": config.output_gamma,
    }
    defs_in = defs_of(project)
    defs_out = defs_of(panel._project_for_matrix())
    frag_in = _definitions_json(defs_in)
    frag_out = frag_in if _same_defs(defs_in, defs_out) \
        else _definitions_json(defs_out)
    return _dump_payload_with_defs(payload, {
        "definitions": frag_in,
        "output_definitions": frag_out,
    })


def _position_input(row):
    # Absence matters: load_fixture_group fills only absent patch/definition
    # fields, and fills channels only when the supplied list is empty.
    return {key: row[key] for key in _POSITION_FIELDS if key in row}
