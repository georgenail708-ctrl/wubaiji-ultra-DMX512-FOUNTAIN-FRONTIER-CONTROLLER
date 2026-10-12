# -*- coding: utf-8 -*-
"""Clip Studio 编译器（R172 设计 §3.3）—— sheet 图 → clip 后处理字段；无 Qt API。

**字段键白名单（W1c 规则 e）**：`POSTPROCESS_FIELD_NAMES ∪ {"playback_rate",
"__light_suppressed__"}`；白名单外键一律警告并丢弃。
**grep 结论（2026-09-26，供后续波次遵循，勿凭印象改）**：`playback_rate` /
`motor_speed_limit_dps` / `motor_limit_scale` / `pump_master_scale` 是 models.py Clip
的**真实字段**（models.py ≈:721-723；`playback_rate` 刻意不在 POSTPROCESS_FIELDS
白名单——跨 clip 套用会破坏时长），按 W1c 契约显式放行。**开关灯卡的编译目标
`__light_suppressed__` 不是 clip 字段**：`set_light_suppressed` 置位的是模块级全局
`_LIGHT_SUPPRESSED`（进程级+会话级，不写入工程/任何 clip）——应用层（W1d）必须
转调它，**不得 setattr 到 clip**；反向编译无法还原全局态 → 开关卡永不反查。
**生效规则（用户定案 2026-09-26）**：拖入 sheet 区即生效——每张效果卡独立编译自身字段；
接线只用于 参数供给 / 边作用域 / 复合模块收束 / 链序消歧；clip级参数卡独立生效；
同链字段冲突 → 按链序后者胜出 + 警告。（反向编译 / 推荐在 `studio_reverse.py`，此处 re-export。）
**接线语义定案（用户 2026-09-29，R183 A6）**：仅过渡方式相关卡片需接线，其余拖入即生效；
过渡套卡**接多少就执行多少**——未接部分只跳过自身并给提示级 V1（不再全图 error）。
**R183 W2-ST**：#3 便签伪卡 / #17 禁用卡的编译期剔除在 `studio_compile_bypass`
（本文件守 500 行门禁）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.core.clip_postprocess import POSTPROCESS_FIELD_NAMES
from src.core.group_selection import group_indices_for_spec, normalize_group_spec, spec_label
from src.ui.clip_studio import studio_compile_bypass as _bypass
from src.ui.clip_studio import studio_compile_params as _params
from src.ui.clip_studio import studio_master, studio_force
from src.ui.clip_studio import studio_smoothing
from src.ui.clip_studio.studio_cards import (
    CARD_CATALOG,
    GROUP_DIRECTION,
    GROUP_LOGIC,
    GROUP_SMOOTH,
)
from src.ui.clip_studio.studio_graph import (
    card_targets, edge_index, find_cycles, graph_parts, node_group, param_value,
)
from src.ui.clip_studio.studio_reverse import recommendations, reverse_compile  # noqa: F401

__all__ = ["Issue", "CompileResult", "compile_sheet", "validate", "recommendations", "reverse_compile",
           "LIGHT_SUPPRESS_KEY", "ALLOWED_FIELD_KEYS"]

#: 开关灯卡的引擎滤网特殊键（会话级全局，见模块 docstring）。
LIGHT_SUPPRESS_KEY = "__light_suppressed__"
EXTRA_FIELD_KEYS = frozenset({"playback_rate", LIGHT_SUPPRESS_KEY})
ALLOWED_FIELD_KEYS = frozenset(POSTPROCESS_FIELD_NAMES) | EXTRA_FIELD_KEYS

#: 参与"链序/参数供给"的效果大类（参数类/逻辑类不是效果卡）。
EFFECT_GROUPS = frozenset({"direction", "smooth", "batch", "tracking", "master", "optimizer"})
_LEVEL_ORDER = {"error": 0, "warning": 1, "hint": 2}


@dataclass
class Issue:
    """校验问题：level=error|warning|hint；node/edge 定位；text 中文文案；code=V1-V8。"""
    level: str
    code: str = ""
    node_id: str = ""
    edge_id: str = ""
    text: str = ""


@dataclass
class CompileResult:
    """compile_sheet 输出：fields 键全部 ∈ ALLOWED_FIELD_KEYS；overrides 见设计 §4。"""
    fields: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    overrides: list = field(default_factory=list)   # [{"spec": {...}, "direction": "ltr"}]

    @property
    def issues(self) -> list:
        return self.errors + self.warnings


#: 反向验证钩子：置 False = 短路 V1（未收束不提示、覆盖照给）；关断语义 R183 保持不动。
_V1_GATE = True


def _err(res, code, node_id, text) -> None:
    res.errors.append(Issue("error", code, node_id, "", text))


def _warn(res, code, node_id, text, edge_id="") -> None:
    res.warnings.append(Issue("warning", code, node_id, edge_id, text))


def _hint(res, code, node_id, text) -> None:
    res.warnings.append(Issue("hint", code, node_id, "", text))


def _direction_value(key) -> str:
    """卡 key → 方向词汇（`dir_ltr` → `ltr`）。"""
    return str(key)[4:] if str(key).startswith("dir_") else "none"


# ── 校验规则（V2/V4/V5/V7；V1/V3/V8 见模块收集与覆盖展开）───────────────────

def _apply_cycle_rules(graph, res) -> set:
    """V4 环路：每个环一条错误；返回环内节点集合（其字段/模块全不计）。"""
    ids: set = set()
    for cyc in find_cycles(graph):
        ids.update(cyc)
        _err(res, "V4", cyc[0], f"检测到环路 {' → '.join(cyc)}：环上卡片全部不生效")
    return ids


def _find_mains(nodes, idx, cycle_ids, res) -> list:
    """主方向卡 = 复合入桩收到收束边的方向卡；多个竞争 → V2（R183：返回全部候选供上游判定）。"""
    mains = []
    for n in nodes:
        nid = str(n.get("id"))
        if nid in cycle_ids or node_group(n) != GROUP_DIRECTION:
            continue
        comp_in = [e for e in idx["dst"].get(nid, []) if str(e.get("to_port", "")) == "composite"
                   and str(e.get("from", "")) not in cycle_ids]
        if comp_in:
            mains.append(nid)
    if len(mains) > 1:
        for nid in mains:
            _err(res, "V2", nid, "多个主方向卡竞争复合收束：复合覆盖不计，请只保留一个")
    return mains


def _head_modules(head, spec, gb_id, idx, res) -> list:
    """一个模块头方向卡 → 模块记录；未收束 → V1 提示级（R183：只跳过本模块）。"""
    hid = str(head.get("id"))
    converged = bool(card_targets(idx, hid, of_group=GROUP_DIRECTION, to_port="composite"))
    if not converged and _V1_GATE:
        dname = _direction_value(head.get("key"))
        _hint(res, "V1", hid, f"复合模块（{spec_label(spec)}·{dname}）未收束进主方向卡："
                             "该模块覆盖不计（其余已接部分照常生效）")
    return [{"spec": spec, "direction": _direction_value(head.get("key")),
             "head": hid, "gb": gb_id, "converged": converged}]


def _collect_modules(nodes, idx, cycle_ids, res) -> list:
    """模块 = 指定组卡（spec_enabled）→ 方向卡 串联；未串联/未收束 → V1 提示级，
    R183 起门开时只从结果剔除自身（不再连坐吞掉其余已接部分）。"""
    modules: list = []
    for n in nodes:
        nid = str(n.get("id"))
        if (nid in cycle_ids or str(n.get("key")) != "group_batch"
                or not bool(param_value(n, "spec_enabled"))):
            continue
        spec = normalize_group_spec(param_value(n, "spec"))
        heads = card_targets(idx, nid, of_group=GROUP_DIRECTION, to_port="in")
        if not heads:
            if _V1_GATE:
                _hint(res, "V1", nid, "指定组卡未串联方向卡：模块不成立，该模块覆盖不计（其余已接部分照常生效）")
            modules.append({"spec": spec, "direction": "", "head": "",
                            "gb": nid, "converged": False})
            continue
        for _e, head in heads:
            modules.extend(_head_modules(head, spec, nid, idx, res))
    # R183：门开时只留下可展开模块（未收束者已提示，只跳过自身）；门关照给（反向验证）。
    return [m for m in modules if m["converged"]] if _V1_GATE else modules


def _scope_for_card(n, idx, state, res) -> None:
    """应用边卡 → 方向卡：登记边作用域（后者胜出并警告）；连无关卡 → V5。"""
    edge_val = str(param_value(n, "edge") or "both")
    if edge_val not in ("both", "in", "out"):
        edge_val = "both"
    targets = card_targets(idx, str(n.get("id")), to_port="in")
    if not targets:
        state["standalone"].append((edge_val, str(n.get("id"))))
        return
    for e, dst in targets:
        if node_group(dst) != GROUP_DIRECTION:
            name = getattr(CARD_CATALOG.get(str(dst.get("key"))), "name", "?")
            _warn(res, "V5", str(n.get("id")),
                  f"应用边卡连到了与边无关的卡片「{name}」（该连线不生效）", str(e.get("id", "")))
            continue
        did = str(dst.get("id"))
        if did in state["by_dir"] and state["by_dir"][did] != edge_val:
            _warn(res, "", str(n.get("id")),
                  "同一方向卡上有多个应用边卡：按连线顺序后者生效", str(e.get("id", "")))
        state["by_dir"][did] = edge_val


def _resolve_scopes(nodes, idx, cycle_ids, state, res) -> None:
    """遍历应用边卡：上游=边作用域；独立=全局 fade_direction_edge；无关卡=V5 警告。"""
    for n in nodes:
        nid = str(n.get("id"))
        if nid in cycle_ids or str(n.get("key")) != "edge_scope":
            continue
        _scope_for_card(n, idx, state, res)


def _warn_scope_conflict(state, res) -> None:
    """独立应用边卡与作用到具体方向卡的应用边目标边不一致 → 提醒（并存时语义易混）。"""
    scoped = sorted({v for v in state["by_dir"].values()})
    for edge_val, nid in state["standalone"]:
        if scoped and any(v != edge_val for v in scoped):
            _warn(res, "", nid, f"独立应用边卡（{edge_val}）与作用到方向卡的应用边"
                                f"（{'/'.join(scoped)}）目标边不一致：连线卡按各自应用边"
                                "生效，独立卡仅写全局应用边，请注意区分")


def _apply_v7(nodes, cycle_ids, res) -> None:
    """V7：灯跟踪卡与开关灯卡（禁用灯输出）同时启用 → 冲突警告。"""
    track = next((str(n.get("id")) for n in nodes
                  if str(n.get("key")) == "light_track" and str(n.get("id")) not in cycle_ids), "")
    switch_off = next((str(n.get("id")) for n in nodes if str(n.get("key")) == "light_switch"
                       and str(n.get("id")) not in cycle_ids
                       and str(param_value(n, "mode")) == "off"), "")
    if track and switch_off:
        _warn(res, "V7", track, "灯跟踪卡与开关灯卡（禁用灯输出）同时启用：灯跟踪将被全局禁用压制")
        _warn(res, "V7", switch_off, "开关灯卡（禁用灯输出）与灯跟踪卡冲突：请二选一")


def _direction_fields(node, scope: str) -> dict:
    """方向卡 → 淡入/淡出方向字段（应用边卡收窄落点边）。"""
    val = _direction_value(node.get("key"))
    if scope == "in":
        return {"fade_in_direction": val}
    if scope == "out":
        return {"fade_out_direction": val}
    return {"fade_in_direction": val, "fade_out_direction": val}


def _effect_fields(node) -> dict:
    """非方向效果/逻辑卡 → 字段 dict；专有编译器优先，其余按 field_map 直映射（值经
    ParamSpec 收敛）；卡片级字段由 `_CARD_LEVEL_FIELDS` 补齐；方向卡另走 `_direction_fields`。"""
    key = str(node.get("key"))
    if node_group(node) == GROUP_SMOOTH:
        return _comp_smooth(node)
    fn = _EFFECT_SPECIAL.get(key)
    if fn is not None:
        return fn(node)
    spec = CARD_CATALOG.get(key)
    fields = {fld: param_value(node, pk) for pk, fld in spec.field_map if pk} if spec else {}
    fields.update(_CARD_LEVEL_FIELDS.get(key, {}))
    return fields


def _comp_smooth(node) -> dict:
    return studio_smoothing.compile_node(node)


def _comp_batch(node) -> dict:
    fields = {}
    if int(param_value(node, "batches")) > 0:
        fields["fade_group_sequence_batches"] = int(param_value(node, "batches"))
    mode = str(param_value(node, "run_mode"))
    if mode in ("parallel", "sequential"):
        fields["fade_group_sequence_mode"] = mode
    return fields


def _comp_light_switch(node) -> dict:
    """开关灯卡：mode=off → 引擎滤网特殊键（全局会话级，见模块 docstring）。"""
    return {LIGHT_SUPPRESS_KEY: str(param_value(node, "mode")) == "off"}


def _comp_trans_opt(node) -> dict:
    """过渡优化卡：预设名非空才落字段（空 = 未应用）。"""
    preset = str(param_value(node, "preset"))
    return {"transitions_optimized": preset} if preset else {}


#: 卡片级字段（卡片在场即写入，不经参数）：灯跟踪卡 = 整套预设在场即启用。
_CARD_LEVEL_FIELDS = {"light_track": {"light_tracking_enabled": True}}


def _comp_transition_mode(node) -> dict:
    """过渡方式卡：「跟随统一」→ 空串（clip 语义：空 = 回退统一策略）。"""
    def one(which):
        return "" if str(param_value(node, which)) == "follow" else str(param_value(node, which))
    fields = {"transition_strategy_in": one("strategy_in"),
              "transition_strategy_out": one("strategy_out")}
    if any(fields.values()):
        fields["transitions_optimized"] = "studio"
    return fields


#: 有专有编译逻辑的效果/逻辑卡（其余走 field_map 直映射 / _CARD_LEVEL_FIELDS）。
#: 主控过渡卡（R182）唯一实现在 `studio_master.compile_fields`（行表/曲线归一 +
#: 语义 docstring：曲线 clip 级、比例逐通道类型、行删除后由会话层差量复位）——
#: 直接入表，不包一层无逻辑的转发函数。
_EFFECT_SPECIAL = {
    "batch_seq": _comp_batch, "light_switch": _comp_light_switch,
    "trans_opt": _comp_trans_opt, "transition_mode": _comp_transition_mode,
    "master_transition": studio_master.compile_fields,
    "motor_force": studio_force.compile_fields,
}


def _chain_order(nodes, idx, cycle_ids) -> list:
    """效果卡应用顺序：链（effect→effect 的 in 边）上游先行的确定性拓扑序。"""
    effect_ids = [str(n.get("id")) for n in nodes
                  if node_group(n) in EFFECT_GROUPS
                  and str(n.get("id")) not in cycle_ids]
    upstream = {nid: set() for nid in effect_ids}
    for e in [e for lst in idx["src"].values() for e in lst]:
        src, dst = str(e.get("from", "")), str(e.get("to", ""))
        if dst in upstream and src in upstream and str(e.get("to_port", "")) == "in":
            upstream[dst].add(src)
    ordered, emitted, remaining = [], set(), list(effect_ids)
    while remaining:
        pick = next((nid for nid in remaining if upstream[nid] <= emitted), None)
        pick = pick or remaining[0]
        ordered.append(pick)
        emitted.add(pick)
        remaining.remove(pick)
    return ordered


def _put_fields(res, fields, origin, nid, fdict, *, silent=False) -> None:
    """合并一张卡的字段：白名单外丢弃；非供给来源的重复定义 → 链序后者胜出+警告。"""
    for fname, value in fdict.items():
        if fname not in ALLOWED_FIELD_KEYS:
            _warn(res, "", nid, f"字段「{fname}」不在后处理白名单，已忽略")
            continue
        if not silent and fname in origin and origin[fname] != nid:
            _warn(res, "", nid, f"字段「{fname}」重复定义：按链序后者生效")
        fields[fname] = value
        origin[fname] = nid


def _total_groups(fields, total_hint) -> int:
    try:
        return int(fields.get("fade_groups") or total_hint or 0)
    except (TypeError, ValueError):
        return 0


def _v8_no_groups(res, mains, modules) -> None:
    """V8：模块存在但全图（含 hint）无组数配套。"""
    if mains:
        _err(res, "V8", mains[0], "主方向卡缺组数配套而存在指定组模块（无法展开覆盖）")
        return
    head = next((m["head"] for m in modules if m["head"]), modules[0]["gb"])
    _err(res, "V8", head or "", "指定组模块缺主方向卡与组数配套（无法展开覆盖）")


def _module_override(m, total, res):
    """单模块 → 覆盖项；区间越组（V3）/展开为空（V3）→ 报错（裁剪后仍收）。"""
    if not m["direction"]:
        return None
    s = m["spec"]
    if s["mode"] == "range" and (s["end"] > total or s["start"] > total):
        _err(res, "V3", m["head"] or m["gb"],
             f"指定组区间 {spec_label(s)} 超出当前组数 {total}")
    if not group_indices_for_spec(s, total):
        _err(res, "V3", m["head"] or m["gb"],
             f"指定组 {spec_label(s)} 在当前组数 {total} 下展开为空")
        return None
    return {"spec": dict(s), "direction": m["direction"]}


def _compile_overrides(res, modules, mains, fields, total_hint) -> list:
    """复合过渡覆盖（设计 §4）：展开 spec → 组号校验（V3/V8）→ 确定性排序输出。

    `modules` 已由 `_collect_modules` 过滤为可展开集合（R183），此处无连坐判断。
    """
    if not modules:
        return []
    if len(mains) > 1:
        return []                      # V2 多个主方向卡竞争：复合覆盖不计
    total = _total_groups(fields, total_hint)
    if total < 1:
        _v8_no_groups(res, mains, modules)
        return []
    out = [o for o in (_module_override(m, total, res) for m in modules) if o is not None]
    out.sort(key=lambda o: (o["spec"]["start"], o["spec"]["end"],
                            o["spec"]["take"], o["spec"]["gap"], o["direction"]))
    return out


def _apply_logic_cards(res, fields, origin, nodes) -> None:
    """逻辑卡（除应用边卡，其语义在 _resolve_scopes）= clip 级设置，拖入即生效。"""
    for n in nodes:
        if node_group(n) == GROUP_LOGIC and str(n.get("key")) != "edge_scope":
            _put_fields(res, fields, origin, str(n.get("id")), _effect_fields(n))


def compile_sheet(sheet, total_groups_hint=None) -> CompileResult:
    """sheet → `CompileResult`；拖入即生效（效果卡独立编译），环路（V4）整链不计。

    R183 W2-ST：注释便签卡（#3）与禁用卡（#17）先从全部通道剔除（含边的作用域/
    供给/环路判定），禁用卡另给 V9 提示级 issue（可定位、不阻断）。实现点唯一在
    `studio_compile_bypass`（本文件守 500 行门禁）。
    """
    res = CompileResult()
    nodes, edges = graph_parts(sheet)
    _bypass.warn_disabled(res, nodes, _hint)
    nodes, edges = _bypass.filter_graph_parts(nodes, edges)
    idx = edge_index(nodes, edges)
    cycle_ids = _apply_cycle_rules({"nodes": nodes, "edges": edges}, res)
    mains = _find_mains(nodes, idx, cycle_ids, res)
    modules = _collect_modules(nodes, idx, cycle_ids, res)
    state = {"by_dir": {}, "standalone": []}
    _resolve_scopes(nodes, idx, cycle_ids, state, res)
    _warn_scope_conflict(state, res)
    contrib, supplies = _params.param_outputs(nodes, idx, cycle_ids, res, _hint)
    # 收束模块头只以覆盖形式生效（设计 §4）；未收束头已从 modules 剔除 → 照写方向字段
    module_heads = {m["head"] for m in modules if m["head"]}
    fields: dict = {}
    origin: dict = {}
    for nid in _chain_order(nodes, idx, cycle_ids):
        card_node = idx["by_id"][nid]
        if node_group(card_node) == GROUP_DIRECTION:
            if nid in module_heads:
                continue          # 模块头方向卡 → 只以覆盖形式生效（设计 §4）
            scope = state["by_dir"].get(nid, "both")
            _put_fields(res, fields, origin, nid, _direction_fields(card_node, scope))
        else:
            _put_fields(res, fields, origin, nid, _effect_fields(card_node))
    for nid, fdict in contrib:
        _put_fields(res, fields, origin, nid, fdict)
    for edge_val, nid in state["standalone"]:
        _put_fields(res, fields, origin, nid, {"fade_direction_edge": edge_val})
    _apply_logic_cards(res, fields, origin, nodes)
    for nid, fdict in supplies.items():
        _put_fields(res, fields, origin, nid, fdict, silent=True)  # 供给覆盖自身参数（非冲突）
    res.fields = fields
    res.overrides = _compile_overrides(res, modules, mains, fields, total_groups_hint)
    _apply_v7(nodes, cycle_ids, res)
    return res


def validate(sheet) -> list:
    """V1-V8 全量校验：返回 Issue 列表（error → warning → hint 排序）。"""
    res = compile_sheet(sheet)
    issues = res.errors + res.warnings
    issues.sort(key=lambda i: (_LEVEL_ORDER.get(i.level, 3), i.code, i.node_id, i.text))
    return issues
