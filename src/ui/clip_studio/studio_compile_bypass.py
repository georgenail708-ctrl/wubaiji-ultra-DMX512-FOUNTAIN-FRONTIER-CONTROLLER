# -*- coding: utf-8 -*-
"""编译期 bypass/伪卡过滤（R183 W2-ST #3/#17）—— 从 `studio_compile` 拆出（500 行门禁）。

两条过滤都在 `compile_sheet` 的**最上游**执行（连边一并剔除），语义：

* **禁用卡（#17）**：节点 ``disabled`` = 编译层 bypass —— 卡仍在图里（连线/布局/
  落盘照旧），但字段与参与资格全不计。字段「消失」后的复位仍走会话层 prev/差量
  机制（与删卡同一通道），故本模块不需要复位逻辑。禁用卡另发 **V9 提示级** issue
  （进 issues 通道 → 画布状态条可定位、卡描边可标记，不阻断编译）。
* **便签伪卡（#3）**：`studio_note` 的无桩伪卡不参与任何效果/参数/逻辑通道，
  连边也一并剔除（否则会出现"悬空边"式的假 V5/V6 判定）。

**为什么不用现成**：无库可借（这是本仓 sheet schema 的语义过滤）；拆分的直接动机
是 `studio_compile.py` 贴 500 行门禁，且两过滤是独立可测的纯函数。
"""
from __future__ import annotations

__all__ = ["bypass_ids", "filter_graph_parts", "warn_disabled"]


def bypass_ids(nodes) -> set:
    """需从编译通道剔除的卡 id 集：禁用卡 ∪ 注释便签卡。"""
    from src.ui.clip_studio.studio_note import NOTE_KEY
    out = set()
    for n in nodes or ():
        if not isinstance(n, dict):
            continue
        if n.get("disabled") or str(n.get("key", "")) == NOTE_KEY:
            out.add(str(n.get("id")))
    return out


def filter_graph_parts(nodes, edges) -> tuple:
    """剔除 bypass 卡及其相连边 → ``(nodes, edges)``（无 bypass → 入参原样返回）。"""
    skip = bypass_ids(nodes)
    if not skip:
        return nodes, edges
    keep_n = [n for n in nodes if str(n.get("id")) not in skip]
    keep_e = [e for e in edges
              if str(e.get("from")) not in skip and str(e.get("to")) not in skip]
    return keep_n, keep_e


def warn_disabled(res, nodes, hint) -> None:
    """禁用卡逐张给 V9 提示级 issue（`hint` = `studio_compile._hint`，避免循环 import）。"""
    for n in nodes or ():
        if not isinstance(n, dict) or not n.get("disabled"):
            continue
        hint(res, "V9", str(n.get("id")),
             f"卡片「{_card_name(n)}」已禁用（bypass）：其编译结果不计")


def _card_name(node) -> str:
    """卡显示名（目录名缺失 → key 原文）。"""
    from src.ui.clip_studio.studio_cards import CARD_CATALOG
    spec = CARD_CATALOG.get(str(node.get("key", "")))
    return str(getattr(spec, "name", "") or node.get("key", ""))
