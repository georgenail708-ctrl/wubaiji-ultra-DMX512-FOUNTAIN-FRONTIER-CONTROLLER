# -*- coding: utf-8 -*-
"""Draw 大页画布图 → 调制状态编译 / 反编译 / 求值（R182，纯逻辑无 Qt）。

数据流（用户已定形态）：
    画布图 graph = {"nodes": [...], "edges": [...]}
      · 源卡   node.kind == "source"：feature（拍强度/BPM相位/低频/高频/频段比）
        + phase（相位微移，可选）
      · 规则卡 node.kind == "rule"：ch_type（泵/阀/dimmer/R/G/B/W/motor_x/y/z…）
        + wave（WAVE_FUNCS 全集）+ wave_freq + amount（0..1）
    连线 edges = [{from: 源卡id, to: 规则卡id}, ...]

`compile_graph()` 把图压成 `Project.audio_modulation_state` 的扩展字段：
    state["sources"] = [{id, feature, phase}]
    state["rules"]   = [{ch_type, wave, wave_freq, amount, source_ids}]
    state["target_ch_types"] = 全部规则卡 ch_type（有序去重）
`decompile_state()` 反编译回图（往返幂等：compile(decompile(s)) == 关键字段）。
旧档（无 rules）→ 空规则表 = 旧全局调制语义逐位不变（零回归红线）。

运行时求值：
    `rule_scale(state, ch_type, t)` = 该通道规则集在当前时刻的调制标量
    （Σ 规则包络×amount，含源卡特征/相位；无规则 → None = 沿用全局调制值）。
    `source_value(project_features, feature, bpm, t, phase)` 单个源卡信号。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..audio.audio_features import AudioFeatures

#: 源卡 feature 键 → 人话（下拉/摘要用；driver 求值走 feature_value 同口径）
SOURCE_KINDS = (
    ("beat_strength", "节拍强度"),
    ("bpm_phase", "BPM 相位"),
    ("band_low", "低频能量"),
    ("band_high", "高频能量"),
    ("band_ratio", "频段能量比"),
)

# R183 波5 清理：SOURCE_TEMPLATES 死数据移除——模板唯一真源 =
# `ui/audio_mod_draw/draw_cards.TEMPLATE_CATALOG`（含说明文案；
# draw_palette / template_choices / template_label 全消费它）。
_FEATURE_KEYS = {k for k, _ in SOURCE_KINDS}
_PHASE_MOD = "bpm_phase"
#: 求值别名（信号键 → audio_modulation 特征名；R182 扩信号集）
_SIGNAL_REMAP = {"onset": "onset_flux"}

# R183 M2 DSP 原语在 audio_mod_draw_dsp（文件行数门禁拆分，同
# draw_canvas_edit 先例；出处=m2-dsp-adoption.md）。此处按旧名再导出，
# driver/测试等既有消费方的导入路径逐字节不变。
from .audio_mod_draw_dsp import (_RULE_PREV_STATE, _apply_dynamics,   # noqa: F401
                                 _beats_per_cycle, _ema_step,
                                 _envelope_shape, _follow_step, _frame_dt,
                                 FAN_STRIDE, clear_caches, rules_ctx)

def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

def _clamp01(v) -> float:
    return min(1.0, max(0.0, _f(v, 0.0)))

def _nodes(graph) -> List[dict]:
    if not isinstance(graph, dict):
        return []
    raw = graph.get("nodes")
    return [n for n in raw if isinstance(n, dict)] if isinstance(raw, list) else []

def _edges(graph) -> List[dict]:
    if not isinstance(graph, dict):
        return []
    raw = graph.get("edges")
    return [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []

def _node_sources(graph) -> List[dict]:
    """图 → 源卡规格表（按画布顺序；非法 feature 回退 beat_strength）。"""
    out = []
    for n in _nodes(graph):
        if str(n.get("kind", "")) != "source":
            continue
        feat = str(n.get("feature", "") or "")
        out.append({
            "id": str(n.get("id", "") or ""),
            "feature": feat if feat in _FEATURE_KEYS else "beat_strength",
            "phase": _f(n.get("phase"), 0.0),
        })
    return out

def _rule_params(node: dict) -> dict:
    """效果卡参数表（R182 重构；缺省取规格默认，非法值钳位）。

    旧档（无 params）由 `_rules_of` 从 wave/amount 兼容展开；M2 扩容键缺省
    （时基=拍/旧连续随机/单阈值）与旧档行为逐位不变。
    """
    raw = node.get("params")
    p = dict(raw) if isinstance(raw, dict) else {}
    sig = str(p.get("signal", "beat_strength") or "beat_strength")
    from .audio_mod_draw_compile_spec import (FREQ_UNITS, RANDOM_MODES,
                                              SIGNAL_KEYS)
    if sig not in SIGNAL_KEYS:
        sig = "beat_strength"
    fu = str(p.get("freq_unit", "beat") or "beat")
    rm = str(p.get("random_mode", "") or "")
    out = {
        "signal": sig,
        "wave": str(p.get("wave", "") or ""),
        "freq": max(0.25, min(16.0, _f(p.get("freq"), 2.0))),
        "depth": _clamp01(p.get("depth", 1.0)),
        "attack": max(0.0, min(2.0, _f(p.get("attack"), 0.05))),
        "release": max(0.0, min(2.0, _f(p.get("release"), 0.2))),
        "phase": _f(p.get("phase"), 0.0),
        "invert": 1.0 if _f(p.get("invert"), 0.0) > 0.5 else 0.0,
        "threshold": _clamp01(p.get("threshold", 0.0)),
        "smoothing": _clamp01(p.get("smoothing", 0.0)),
        "freq_unit": fu if fu in FREQ_UNITS else "beat",
        "random_mode": rm if rm in RANDOM_MODES else "",
        "threshold_close": _clamp01(p.get("threshold_close", 0.0)),
        "hold_ms": max(0.0, min(2000.0, _f(p.get("hold_ms"), 0.0))),
        # 3.4b：卡片级 spread（compile_graph 取 max 聚合为 state 字段）
        "spread_deg": max(0.0, min(360.0, _f(p.get("spread_deg"), 0.0))),
    }
    return out

def _targets_of(node: dict) -> List[str]:
    """效果卡目标通道（ch_types 列表优先；旧档 ch_type 单值兼容）。"""
    raw = node.get("ch_types")
    out = [str(c) for c in (raw or []) if str(c)] if isinstance(raw, list) else []
    if not out:
        single = str(node.get("ch_type", "") or "").strip()
        out = [single] if single else []
    return out

def _rules_of(node: dict, src_ids: List[str]) -> Optional[dict]:
    """单张效果卡 → 规则行（无目标通道 → None 丢弃）。

    R182 重构：规则行携带效果参数表（params）+ 多目标（ch_types），并把
    首个目标写进旧字段 ch_type、深度写进 amount（旧消费方/指纹兼容）。
    R182 审计修：**删光连线 = 无源**（`source_ids=[]`，落到纯包络调制）。
    """
    targets = _targets_of(node)
    if not targets:
        return None
    links = node.get("source_ids")
    keep = [str(s) for s in links] if isinstance(links, list) and links else []
    params = _rule_params(node) if isinstance(node.get("params"), dict) else {
        # 旧档（无 params）：wave/amount 现场展开成等价参数
        # （R183 M2 新键取零行为缺省：时基=拍/单阈值/无保持/旧连续随机）
        "signal": "beat_strength", "wave": str(node.get("wave", "") or ""),
        "freq": max(0.25, min(16.0, _f(node.get("wave_freq"), 2.0))),
        "depth": _clamp01(node.get("amount", 1.0)),
        "attack": 0.05, "release": 0.2, "phase": 0.0, "invert": 0.0,
        "threshold": 0.0, "smoothing": 0.0,
        "freq_unit": "beat", "random_mode": "",
        "threshold_close": 0.0, "hold_ms": 0.0,
    }
    return {
        "ch_type": targets[0],               # 旧字段：首个目标（兼容）
        "ch_types": targets,                 # 新字段：全部目标
        "effect": str(node.get("effect", "") or ""),
        "params": params,
        "wave": str(params.get("wave", "")),
        "wave_freq": float(params.get("freq", 2.0)),
        "amount": float(params.get("depth", 1.0)),
        "source_ids": [s for s in keep if s],
    }

def _link_map(graph) -> Dict[str, List[str]]:
    """规则卡 id → 连入的源卡 id 列表（按边序去重）。"""
    out: Dict[str, List[str]] = {}
    for e in _edges(graph):
        src, dst = str(e.get("from", "") or ""), str(e.get("to", "") or "")
        if not src or not dst:
            continue
        got = out.setdefault(dst, [])
        if src not in got:
            got.append(src)
    return out

def _node_rules(graph, src_ids: List[str]) -> List[dict]:
    """图 → 规则行表（每张规则卡一行；连线为空的卡接全部源=自由卡）。"""
    linked = _link_map(graph)
    out = []
    for n in _nodes(graph):
        if str(n.get("kind", "")) != "rule":
            continue
        nid = str(n.get("id", "") or "")
        node = dict(n)
        if nid in linked:
            node["source_ids"] = linked[nid]
        row = _rules_of(node, src_ids)
        if row is not None:
            out.append(row)
    return out

def compile_graph(graph, base_state: Optional[dict] = None) -> dict:
    """画布图 → 调制状态（在 `base_state` 上覆写 Draw 字段；不改入参）。

    产物字段：sources / rules / target_ch_types / draw_graph（图快照=再编辑
    恢复用）。clips/amount/attack/release 等旧字段原样保留（底部编辑器与
    Draw 页共享同一状态真源）。

    ★ R182 审计修（旧档目标改写）：`target_ch_types` 只在用户确实编辑过画布时
    才被覆写——有规则卡按卡推导；图空且从未有过非空 draw_graph → 保留旧 targets
    （修前无条件覆写 []，驱动 scope 回退误调制）；删光 → 有意写空。
    ★ R182 三轮审计修：`edited_before` 判据必须看 draw_graph **内容**——
      normalize_state 无条件注入空 dict 键，「键存在」判据被 it 打穿
      （真实面板路径 100% 复现旧档 targets 被清）。
    """
    st = dict(base_state) if isinstance(base_state, dict) else {}
    srcs = _node_sources(graph)
    rules = _node_rules(graph, [s["id"] for s in srcs])
    st["sources"] = srcs
    st["rules"] = rules
    st["spread_deg"] = _aggregate_spread(rules)   # 3.4b（见 helper）
    has_graph = bool(_nodes(graph) or _edges(graph))
    saved = st.get("draw_graph")
    edited_before = (isinstance(saved, dict)
                     and bool(saved.get("nodes") or saved.get("edges")))
    if rules or has_graph or edited_before:
        st["target_ch_types"] = _unique(
            [ct for r in rules
             for ct in (r.get("ch_types") or [r.get("ch_type", "")])])
    # ★ R183 清查④②（P1）：params 必须深拷——修前浅拷与画布活节点共享
    #   params dict，应用后画布滑杆/撤销就地改写会实时穿透进"已应用"快照，
    #   自动保存把未应用编辑落盘（重开即复活为已应用态）。
    # ★ R183 清查④②（P1）：params 深拷（修前浅拷被画布滑杆/撤销就地改写
    #   实时穿透，自动保存把未应用编辑落盘）；legacy 无 params 节点不得注入
    #   空表（会翻到现代路径丢 legacy 字段——roundtrip 实测回归）。
    _snap_nodes = [dict(_n, **({"params": dict(_n["params"])}
                                if isinstance(_n.get("params"), dict) else {}))
                   for _n in _nodes(graph)]
    st["draw_graph"] = {"nodes": _snap_nodes,
                        "edges": [dict(e) for e in _edges(graph)]}
    return st

def _aggregate_spread(rules) -> float:
    """R183 3.4b：卡片级 spread → 状态级 max（全 0=引擎短路逐位兼容）。"""
    vals = [float((r.get("params") or {}).get("spread_deg") or 0.0) for r in rules]
    return max(vals) if vals else 0.0

def _unique(items) -> List[str]:
    out: List[str] = []
    for it in items:
        s = str(it or "")
        if s and s not in out:
            out.append(s)
    return out

def _rule_node(row: dict, index: int) -> dict:
    """规则行 → 效果卡节点（反编译；携带 params/多目标）。

    R182 重构：输出新格式（effect/params/ch_types），同时保留旧字段
    （ch_type/wave/wave_freq/amount）使旧测试/旧消费者继续可读。
    """
    targets = [str(c) for c in (row.get("ch_types")
                                or [row.get("ch_type", "")]) if str(c)]
    params = row.get("params") if isinstance(row.get("params"), dict) else {
        "signal": "beat_strength", "wave": row.get("wave", ""),
        "freq": _f(row.get("wave_freq"), 2.0),
        "depth": _clamp01(row.get("amount", 1.0)),
        "attack": 0.05, "release": 0.2, "phase": 0.0, "invert": 0.0,
        "threshold": 0.0, "smoothing": 0.0,
        "freq_unit": "beat", "random_mode": "",
        "threshold_close": 0.0, "hold_ms": 0.0, "spread_deg": 0.0,
    }
    return {
        "id": f"rule-{targets[0] if targets else 'x'}-{index}",
        "kind": "rule",
        "x": 460.0, "y": 80.0 + 172.0 * index,
        "effect": str(row.get("effect", "") or ""),
        "params": dict(params),
        "ch_types": targets,
        "ch_type": targets[0] if targets else "",
        "wave": str(params.get("wave", "")),
        "wave_freq": _f(params.get("freq"), 2.0),
        "amount": _clamp01(params.get("depth", 1.0)),
    }

def decompile_state(state: Optional[dict]) -> dict:
    """调制状态 → 画布图（旧档规则空 → 空图；draw_graph 存在则原样恢复）。

    `draw_graph` 是编辑态快照（含节点坐标），存在即优先——保证"打开→关闭→
    重开"画布布局不漂移；否则按 sources/rules 现场生成稳定排布。
    """
    st = state if isinstance(state, dict) else {}
    saved = st.get("draw_graph")
    if isinstance(saved, dict) and (saved.get("nodes") or saved.get("edges")):
        return {"nodes": [dict(n) for n in _nodes(saved)],
                "edges": [dict(e) for e in _edges(saved)]}
    nodes = _decompiled_source_nodes(st)
    edges = _decompiled_rule_edges(st, nodes)
    return {"nodes": nodes, "edges": edges}

def _decompiled_source_nodes(st: dict) -> List[dict]:
    """sources → 源卡节点（左列纵排；带模板徽标信息）。"""
    out = []
    for i, s in enumerate(st.get("sources") or []):
        if not isinstance(s, dict) or not s.get("id"):
            continue
        out.append({"id": str(s["id"]), "kind": "source",
                    "x": 80.0, "y": 80.0 + 132.0 * i,
                    "feature": str(s.get("feature", "beat_strength") or "beat_strength"),
                    "phase": _f(s.get("phase"), 0.0)})
    return out

def _decompiled_rule_edges(st: dict, nodes: List[dict]) -> List[dict]:
    """rules → 规则卡节点（追加进 nodes）+ 源卡连线；返回边表。"""
    ids = {str(n.get("id", "")) for n in nodes}
    edges: List[dict] = []
    for i, r in enumerate(st.get("rules") or []):
        if not isinstance(r, dict) or not str(r.get("ch_type", "")):
            continue
        node = _rule_node(r, i)
        nodes.append(node)
        for sid in (r.get("source_ids") or []):
            sid = str(sid)
            if sid in ids:
                edges.append({"id": f"e-{sid}-{node['id']}",
                              "from": sid, "to": node["id"]})
    return edges

# ── 运行时求值（driver 消费；纯函数）──────────────────────────────────────
def _phase_ramp(bpm: float, t: float, phase: float) -> float:
    """BPM 相位斜坡 0..1（相位微移）。"""
    b = max(1.0, _f(bpm, 120.0) or 120.0)
    return float((float(t) * b / 60.0 + _f(phase, 0.0)) % 1.0)

def source_value(features: Optional[AudioFeatures], feature: str, bpm: float,
                 t: float, phase: float = 0.0) -> float:
    """单个信号源在 t 时刻的值 0..1（无特征 → 1.0=纯规则包络调制）。

    R182 重构扩信号集：`bpm_phase` 相位斜坡；`band_ratio` 低/高频比；
    `onset` 瞬态冲击；`energy` 整体能量；`section` 段落位置；
    其余走 `audio_modulation.feature_value` 同口径。
    """
    if features is None or not getattr(features, "beat_times", None):
        return 1.0
    kind = str(feature or "")
    if kind == _PHASE_MOD:
        return _phase_ramp(bpm, t, phase)
    kind = _SIGNAL_REMAP.get(kind, kind)
    if kind == "band_ratio":
        return _band_ratio_value(features, float(t))
    if kind == "section":
        return _clamp01(_section_phase(features, float(t)))
    from .audio_modulation import feature_value
    return _clamp01(feature_value(features, kind, float(t)))

def _band_ratio_value(features, t: float) -> float:
    """低/高频能量比（0..1；无能量 → 0.5）。"""
    from .audio_modulation import feature_value
    lo = feature_value(features, "band_low", t)
    hi = feature_value(features, "band_high", t)
    return _clamp01(lo / max(1e-6, lo + hi))

def _section_phase(features, t: float) -> float:
    """段落位置 0..1（当前段落内的归一化进度；无边界 → 0.5）。"""
    bounds = [float(b) for b in (getattr(features, "section_boundaries", None) or [])]
    if not bounds:
        return 0.5
    total = float(getattr(features, "duration", 0.0) or 0.0) or (bounds[-1] or 1.0)
    starts = [0.0] + sorted(b for b in bounds if 0.0 < b < total)
    idx = 0
    for i, s in enumerate(starts):
        if t >= s:
            idx = i
    seg_start = starts[idx]
    seg_end = starts[idx + 1] if idx + 1 < len(starts) else total
    span = max(1e-6, seg_end - seg_start)
    return (float(t) - seg_start) / span

def _source_factor(features, signal: str, bpm: float, t: float,
                   sources: Dict[str, dict], *, rule: dict) -> float:
    """规则行 → 其绑定源卡的信号均值。

    R182 重构：signal 从 params 取（效果自身决定），多源卡时对其 feature
    求均值再与效果 signal 混合（各源卡平均）。无绑定 → 直接用效果 signal。
    """
    ids = [str(s) for s in (rule.get("source_ids") or []) if s]
    if not ids:
        return source_value(features, signal, bpm, t, 0.0)
    total = 0.0
    for sid in ids:
        spec = sources.get(sid)
        if not isinstance(spec, dict):
            total += source_value(features, signal, bpm, t, 0.0)
            continue
        total += source_value(features, spec.get("feature", "") or signal,
                              bpm, t, _f(spec.get("phase"), 0.0))
    return total / max(1, len(ids))

def scale_map(state: Optional[dict], ch_types, features: Optional[AudioFeatures],
              t: float, env: float, *, amount: float = 1.0) -> Dict[str, float]:
    """一批通道类型 → 各自在 t 时刻的规则调制标量（driver 逐帧单入口）。

    只返回**有规则命中**的通道（未命中者由调用方沿用全局调制值 = 旧档零
    回归）。规则标量 = env × amount × ⊕(规则值)（多规则概率合成后钳 0..1，
    R183 M2；单规则 = env × amount × 卡值与旧口径一致）；`amount`
    （keyword-only）= 全局「调制量」增益系数，
    ★ R182 审计修：修前规则路径不含全局 amount → 拉 0 关不掉规则通道。
    `ch_types` 传当前 scope 出现的类型集合；空/无规则 → 空表（零开销路径）。
    R183 M2：每帧先做 rules_key 指纹比对失效 per-rule 滤波状态并算帧间
    dt（`_frame_dt`），ctx 携带 dt/frames 供迟滞门/EMA/一阶跟随消费。
    R183 3.4b：多灯展开走 `audio_mod_fan`（相位偏移 0 的桶与本函数同槽同值）。
    """
    if not isinstance(state, dict) or not state.get("rules"):
        return {}
    wanted = {str(c) for c in (ch_types or ()) if str(c)}
    if not wanted:
        return {}
    return _accumulate_rules(state["rules"], wanted,
                             rules_ctx(state, features, t, env, amount))

def _rule_value(rule: dict, ctx: dict, idx: int = 0, *,
                phase_lift: float = 0.0) -> float:
    """单条规则在 t 时刻的值 = 源信号 × 波形形状 × 深度（0..1）。

    R182 重构（音频效果库）：参数化求值——`signal` 出驱动量；wave/freq/phase
    出周期形状（wave 空 = 恒 1 纯信号）；threshold/invert/smoothing 动态整形；
    `depth` = 效果深度。旧档（无 params）由 `_rules_of` 展开成等价参数同路径。
    R183 M2 真接线：`ctx["frames"][idx]` 为 per-rule 帧状态（平滑 y / 跟随
    fy / 迟滞门 gate），驱动迟滞门 + EMA + attack/release 一阶跟随；
    dt≤0（首帧/seek）时全部直通播种 = 旧无状态口径。
    R183 3.4b：`phase_lift`（keyword-only）= 该灯的相位偏移（周期比；0=同相
    旧口径）；`idx` 在多灯展开时传**复合槽位**（规则×桶），每灯状态独立。
    """
    p = rule.get("params") if isinstance(rule.get("params"), dict) else {}
    sig = _source_factor(ctx["features"], p.get("signal", "beat_strength"),
                         ctx["bpm"], ctx["t"], ctx["sources"], rule=rule)
    u = float(ctx["t"]) * max(1.0, ctx["bpm"]) / 60.0     # 拍位（波形时间基）
    shape = (_envelope_shape(p, u, ctx["bpm"], phase_lift)
             if str(p.get("wave", "")) else 1.0)
    dt = _f(ctx.get("dt"), 0.0)
    st = ctx["frames"].setdefault(idx, {})
    val, st["y"], st["gate"] = _apply_dynamics(
        sig * shape, p, st.get("y"), st.get("gate"), dt)
    fy = _follow_step(val, st.get("fy"), p, dt)
    st["fy"] = fy
    return _clamp01(fy * _clamp01(p.get("depth", 1.0)))

def _target_lut(wanted) -> Dict[str, str]:
    """wanted（目标集合）→ {lower 形式: 原样} 匹配表（清查⑦A P1-7 防大小写失配）。"""
    return {str(w).strip().lower(): str(w) for w in (wanted or ()) if str(w).strip()}


def _match_targets(targets, lut: Dict[str, str]) -> List[str]:
    """规则目标按 lut 命中 → **wanted 原样**键表（保序去重）。

    ★ 清查⑦A P1-7：`target_ch_types=["r"]` 而规则行 `ch_types=["R"]` 时此前
      精确匹配落空 → 规则静默失活（真工程 scope 识别出大写 R/G/B）。键统一用
      wanted 形式，与 `_ct_index`/`_overlay_ct_scales` 的键同源。
    """
    out: List[str] = []
    for t in targets:
        w = lut.get(str(t).strip().lower())
        if w is not None and w not in out:
            out.append(w)
    return out


def _accumulate_rules(rows, wanted: set, ctx: dict) -> Dict[str, float]:
    """规则行叠加（R183 M2：概率合成 `1−(1−a)(1−b)` 替代硬钳累加）。

    概率合成保律动（两条 0.6 规则 = 0.84 而非钳死的 1.0，r183-candidates
    §八建议）；0/1 吸收、单规则恒等 → 旧单规则档零回归。末尾仍乘 env 并
    钳 0..1。逐目标展开（ch_types 多目标；旧档 ch_type 单值兼容）。
    R183 3.4b：状态槽下标 = `idx × FAN_STRIDE`（展开路径的**桶 0**）——两路
    共用参考槽，且与展开桶槽（idx×STRIDE + b）互不重叠（多灯展开在
    `audio_mod_fan`，两侧同槽位口径）。
    """
    acc: Dict[str, float] = {}
    lut = _target_lut(wanted)      # 清查⑦A P1-7：大小写不敏感匹配表（循环外一次）
    for idx, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        targets = [str(c) for c in (r.get("ch_types")
                                    or [r.get("ch_type", "")]) if str(c)]
        hits = _match_targets(targets, lut)
        if not hits:
            continue
        v = _rule_value(r, ctx, idx * FAN_STRIDE)
        for ct in hits:
            prev = acc.get(ct, 0.0)
            acc[ct] = 1.0 - (1.0 - prev) * (1.0 - v)
    return {ct: min(1.0, max(0.0, ctx["env"] * v)) for ct, v in acc.items()}

#: 兼容别名（R182 早期探针口径；driver 现走 `scale_map` 单入口）
def rule_scale(state: dict, ch_type: str, features: Optional[AudioFeatures],
               t: float, env: float) -> Optional[float]:
    """单通道查询（薄包装 `scale_map`；无命中 → None）。"""
    got = scale_map(state, [str(ch_type)], features, t, env)
    return got.get(str(ch_type))
