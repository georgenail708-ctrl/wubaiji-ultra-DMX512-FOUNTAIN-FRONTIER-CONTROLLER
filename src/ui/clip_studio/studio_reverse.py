# -*- coding: utf-8 -*-
"""Clip Studio 反向编译与推荐（R172 设计 §3.3「反向编译」/ B8「推荐」）—— 纯逻辑。

* `reverse_compile(clip)`：clip 非默认字段 → 建议卡节点列表（"已应用"高亮，
  同菜单绿亮语义）。字段 → 卡映射用 `CardSpec.field_map` 反查（`_field_origin`）。
  已有图时反向编译只做提示不覆盖用户图（职责在 UI 波次 W1d）。
* `recommendations(sheet, last_added_key)`：静态 `CardSpec.recommends` ∩ 上下文规则
  （已有方向卡→组数/批数卡；有模块→第二方向卡；方向卡→应用边卡置于其前提示；
  主控卡→时长卡），去重已存在，最多 6 条。

注意：开关灯卡的 `__light_suppressed__` 是会话级全局（见 studio_compile 文档），
不在 clip 字段上 → 反向编译**无法**还原，永不产生开关灯卡建议。
（由 W1c 任务书从 studio_compile 拆出：单文件 ≤500 行架构门禁；studio_compile
对二者 re-export，UI 仍可 `from src.ui.clip_studio.studio_compile import ...`。）
"""
from __future__ import annotations

from src.core.clip_postprocess import POSTPROCESS_FIELD_NAMES
from src.core.group_selection import DIRECTION_VALUES, normalize_direction_overrides
from src.ui.clip_studio.studio_cards import (
    CARD_CATALOG,
    GROUP_DIRECTION,
    GROUP_MASTER,
    GROUP_PARAM,
    PORT_IN,
    PORT_OUT,
    keys_in_group,
)
from src.ui.clip_studio.studio_graph import graph_parts, node_group
from . import studio_smoothing

__all__ = ["reverse_compile", "recommendations", "wire_reverse_edges"]

#: 反向编译跳过泛型索引的字段（由专用 handler / 非卡片语义处理）。
_SPECIAL_FIELDS = frozenset({
    "smooth_preset", "smooth_preset_name", "smooth_custom_params", "fade_in_direction", "fade_out_direction",
    "fade_direction_edge", "fade_group_direction_overrides", "light_tracking_enabled",
    "playback_rate", "fade_type_ratio_in", "fade_type_ratio_out",
    "transition_curve_in", "transition_curve_out",
    "__light_suppressed__",
})

_ORIGIN_CACHE: dict | None = None
_CLIP_DEFAULTS: dict | None = None


def _field_origin() -> dict:
    """field → (card_key, param_key)（目录序首个命中者胜）；卡片级/特殊字段不在内。"""
    global _ORIGIN_CACHE
    if _ORIGIN_CACHE is None:
        origin: dict = {}
        for key, spec in CARD_CATALOG.items():
            for param_key, fld in getattr(spec, "field_map", ()):
                if not param_key or fld in _SPECIAL_FIELDS or fld in origin:
                    continue
                origin[fld] = (key, param_key)
        _ORIGIN_CACHE = origin
    return _ORIGIN_CACHE


def _clip_defaults() -> dict:
    """Clip 字段默认值表（懒加载 models；失败 → 空 → 退化为真值判定）。"""
    global _CLIP_DEFAULTS
    if _CLIP_DEFAULTS is None:
        try:
            from src.core.models import Clip
            names = set(POSTPROCESS_FIELD_NAMES) | {"playback_rate"}
            _CLIP_DEFAULTS = {n: Clip.__dataclass_fields__[n].default
                              for n in names if n in Clip.__dataclass_fields__}
        except Exception:
            _CLIP_DEFAULTS = {}
    return _CLIP_DEFAULTS


def _is_non_default(clip, name: str) -> bool:
    """字段是否非默认（默认值取自 models.Clip；取不到时按真值判定，宁多勿漏）。"""
    val = getattr(clip, name, None)
    defaults = _clip_defaults()
    if name not in defaults:
        return bool(val)
    default = defaults[name]
    if callable(default) or default.__class__.__name__ == "_MISSING_TYPE":
        return bool(val)
    return val != default


def _rev_add(out: list, seen: set, key: str, params: dict, role: str = "solo") -> None:
    """追加建议卡；同 key 已存在 → 合并参数（先到者的键优先）。

    R178：`role` 标记卡的模块身份（main=全局方向卡 / head=复合模块成员 /
    solo=独立卡），供 `wire_reverse_edges` 精确补边；同 key 双身份时 main 优先
    （全局方向身份压过模块成员）。"""
    if key not in CARD_CATALOG:
        return
    if key in seen:
        for item in out:
            if item["key"] == key:
                for k, v in params.items():
                    item["params"].setdefault(k, v)
                if role == "main":
                    item["role"] = "main"
                return
        return
    seen.add(key)
    out.append({"key": key, "params": dict(params), "role": role})


def reverse_compile(clip) -> list:
    """clip 非默认字段 → 建议卡节点列表 `[{"key", "params"}, ...]`（顺序稳定）。"""
    out: list = []
    seen: set = set()
    if clip is None:
        return out
    _rev_directions(clip, out, seen)
    _rev_edge_scope(clip, out, seen)
    _rev_smooth(clip, out, seen)
    _rev_overrides(clip, out, seen)
    _rev_tracking(clip, out, seen)
    _rev_playback(clip, out, seen)
    _rev_master_transition(clip, out, seen)
    _rev_generic(clip, out, seen)
    return out


def _rev_directions(clip, out, seen) -> None:
    """淡入/淡出方向（非 none）→ 对应方向卡。"""
    for attr in ("fade_in_direction", "fade_out_direction"):
        val = str(getattr(clip, attr, "none") or "none")
        if val in DIRECTION_VALUES and val != "none":
            _rev_add(out, seen, f"dir_{val}", {}, role="main")


def _rev_edge_scope(clip, out, seen) -> None:
    """应用边非 both → 应用边卡（独立形态）。"""
    edge = str(getattr(clip, "fade_direction_edge", "both") or "both")
    if edge in ("in", "out"):
        _rev_add(out, seen, "edge_scope", {"edge": edge})


def _rev_smooth(clip, out, seen) -> None:
    """Restore the preset and its actual controls without processing frames."""
    pid = str(getattr(clip, "smooth_preset", "") or "")
    if pid:
        _rev_add(out, seen, f"smooth_{pid}", studio_smoothing.reverse_params(clip))


def _rev_overrides(clip, out, seen) -> None:
    """复合方向覆盖 → 模块建议（方向卡 + 开了指定组的组数/批数卡）。"""
    raw = getattr(clip, "fade_group_direction_overrides", [])
    for ov in normalize_direction_overrides(raw):
        _rev_add(out, seen, f"dir_{ov['direction']}", {}, role="head")
        _rev_add(out, seen, "group_batch",
                 {"spec_enabled": True, "spec": dict(ov["spec"])}, role="head")


def _rev_tracking(clip, out, seen) -> None:
    """灯跟踪启用 → 灯跟踪卡（整套参数回填）。"""
    if not _is_non_default(clip, "light_tracking_enabled"):
        return
    _rev_add(out, seen, "light_track", {
        "source": str(getattr(clip, "light_tracking_source", "pump") or "pump"),
        "threshold": int(getattr(clip, "light_tracking_threshold", 128) or 0),
        "attack": float(getattr(clip, "light_tracking_attack", 0.08) or 0.0),
        "release": float(getattr(clip, "light_tracking_release", 0.22) or 0.0),
        "color": str(getattr(clip, "light_tracking_color", "#FFFFFF") or "#FFFFFF"),
        "max_brightness": int(getattr(clip, "light_tracking_max_brightness", 255) or 0),
    })


def _rev_playback(clip, out, seen) -> None:
    """速度 ≠100% → 百分比卡（落点=速度）。"""
    try:
        rate = float(getattr(clip, "playback_rate", 1.0) or 1.0)
    except (TypeError, ValueError):
        return
    if abs(rate - 1.0) > 1e-9:
        _rev_add(out, seen, "percent", {"target": "playback_rate", "value": rate * 100.0})


def _rev_master_transition(clip, out, seen) -> None:
    """R182 主控过渡卡：比例表 / 过渡曲线任一非默认 → 还原本卡（曲线+各行比例）。

    ★ 与旧「通道延迟比例卡（ch_ratio）」的分工：本卡是主控类（拖入即生效、
    含曲线）；ch_ratio 仍是参数类供值卡（只写比例、需连线）。比例 dict 的
    **反查归本卡**（ch_ratio 的 field_map 是 param 级 in_/out_ 展开，不再参与
    反向——避免同一字段产出两张卡）。
    """
    rows = _ratio_rows(clip)
    curves = (str(getattr(clip, "transition_curve_in", "") or ""),
              str(getattr(clip, "transition_curve_out", "") or ""))
    if not rows and not any(curves):
        return
    params = {"rows": rows}
    if curves[0]:
        params["curve_in"] = curves[0]
    if curves[1]:
        params["curve_out"] = curves[1]
    _rev_add(out, seen, "master_transition", params)


def _ratio_rows(clip) -> list:
    """clip 的两个比例 dict → 合并行表（0..200 整数；两向缺项按 100=原样）。

    ★ R182 三轮审计修：正向量程 0-200%（RATIO_MAX=2.0），反向钳位同步 200
    ——修前 _pct 上限 100，>100% 已存比例经反查建议卡一次即被压平成 100。
    """
    din = getattr(clip, "fade_type_ratio_in", {}) or {}
    dout = getattr(clip, "fade_type_ratio_out", {}) or {}
    types = [str(k) for k in list(din) + list(dout) if str(k)]
    rows = []
    for ctype in dict.fromkeys(types):
        rows.append({"type": ctype,
                     "ratio_in": _pct(din.get(ctype)),
                     "ratio_out": _pct(dout.get(ctype))})
    return rows


def _pct(value) -> int:
    try:
        return max(0, min(200, int(round(float(value) * 100.0))))
    except (TypeError, ValueError):
        return 100


def _rev_generic(clip, out, seen) -> None:
    """泛型反查：field_map（param 级）直映射（比例/曲线字典归 `_rev_master_transition`）。"""
    grouped: dict = {}
    for fld, (key, param) in _field_origin().items():
        if fld in _REVERSE_SKIP_FIELDS or not _is_non_default(clip, fld):
            continue
        grouped.setdefault(key, {})[param] = getattr(clip, fld, None)
    for key, params in grouped.items():
        _rev_add(out, seen, key, params)


#: 泛型反查跳过：由专用 handler 产出卡（主控过渡卡等）。
_REVERSE_SKIP_FIELDS = frozenset({"fade_type_ratio_in", "fade_type_ratio_out"})


# ── 推荐（B8）───────────────────────────────────────────────────────────────

def wire_reverse_edges(placed: list) -> list:
    """R178：反向卡自动补模块边（修「页面一进来就报错」）。

    `reverse_compile` 产出的 指定组卡（spec_enabled）+ 模块头方向卡是**一个复合
    模块**，但平铺建卡不带边 → compile_sheet 立刻判 V1（模块不成立/未收束），
    用户打开 Studio 即见报错。补三类边（compile 语义）：

      1. 指定组卡 --out→in--> 模块头方向卡（role="head"）——模块串联；
      2. 模块头方向卡 --out→composite--> 全局方向卡（role="main"）——模块收束
         （无 main 卡收不了的场合不硬连，V1 照实提示用户补主方向卡）；
      3. solo 参数卡 --out→in--> 主效果卡——clip 里本就生效的参数卡不该悬空报 V6。

    :param placed: `[{"id","key","params","role"}, ...]`（`_place_reverse_nodes` 落卡记录）。
    :return: 新建边描述 `{"from","to","to_port"}` 列表（宿主转 new_edge；测试断言用）。
    """
    wires = _wire_module_edges(placed)
    wires += _wire_solo_params(placed, _wire_main_id(placed))
    return wires


def _spec_ports(key: str) -> tuple:
    """卡 key → 桩位元组（未知卡 → 空）。"""
    spec = CARD_CATALOG.get(key)
    return tuple(getattr(spec, "ports", ())) if spec is not None else ()


def _placed_ids(placed: list, key_filter, role: str = "") -> list:
    """按 key 谓词 + 可选 role 过滤落卡记录的 id（保持落卡顺序）。"""
    return [str(p["id"]) for p in placed
            if key_filter(str(p.get("key", "")))
            and (not role or p.get("role") == role)]


def _wire_module_edges(placed: list) -> list:
    """边 1+2：模块串联与收束（见 wire_reverse_edges 文档）。"""
    batch_ids = [str(p["id"]) for p in placed
                 if str(p.get("key")) == "group_batch"
                 and bool((p.get("params") or {}).get("spec_enabled"))]
    head_ids = _placed_ids(placed, lambda k: k.startswith("dir_"), role="head")
    main_ids = _placed_ids(placed, lambda k: k.startswith("dir_"), role="main")
    wires: list = []
    for gb in batch_ids:
        for hid in head_ids:
            wires.append({"from": gb, "to": hid, "to_port": "in"})
    for hid in head_ids:
        if hid in main_ids:
            continue
        main = next((m for m in main_ids if m != hid), "")
        if main:
            wires.append({"from": hid, "to": main, "to_port": "composite"})
    return wires


def _wire_main_id(placed: list) -> str:
    """solo 参数卡的供给目标：优先全局方向卡，否则首个带入桩的非参数效果卡。"""
    mains = _placed_ids(placed, lambda k: k.startswith("dir_"), role="main")
    if mains:
        return mains[0]
    return next((str(p["id"]) for p in placed
                 if p.get("role") == "solo"
                 and PORT_IN in _spec_ports(str(p.get("key", "")))
                 and node_group({"key": str(p.get("key", ""))}) != GROUP_PARAM), "")


def _wire_solo_params(placed: list, target: str) -> list:
    """边 3：solo 参数卡（时长/百分比/通道比例/独立组数）连到主效果卡入桩。"""
    if not target:
        return []
    wired: list = []
    for p in placed:
        key = str(p.get("key", ""))
        if p.get("role") != "solo" or node_group({"key": key}) != GROUP_PARAM:
            continue
        if PORT_OUT in _spec_ports(key):
            wired.append({"from": str(p["id"]), "to": target, "to_port": "in"})
    return wired


def _rec_push(present: set, out: list, key: str) -> None:
    """去重追加（目录内 + 图中未有 + 未推荐过）。"""
    if key in CARD_CATALOG and key not in present and key not in out:
        out.append(key)


def _rec_context(nodes) -> dict:
    """上下文判定：图里是否已有方向卡 / 指定组模块 / 主控卡。"""
    return {
        "direction": any(node_group(n) == GROUP_DIRECTION for n in nodes),
        "module": any(str(n.get("key")) == "group_batch"
                      and bool((n.get("params") or {}).get("spec_enabled"))
                      for n in nodes),
        "master": any(node_group(n) == GROUP_MASTER for n in nodes),
    }


def _rec_context_rules(present: set, out: list, last_added_key, ctx: dict) -> None:
    """上下文规则（优先级序）：组数/批数卡 → 应用边卡 → 第二方向卡 → 时长卡。"""
    if ctx["direction"]:
        _rec_push(present, out, "group_batch")        # 已有方向卡 → 首推组数/批数卡
    if str(last_added_key or "").startswith("dir_"):
        _rec_push(present, out, "edge_scope")         # 拖入方向卡 → 应用边卡（置于其前）
    if ctx["module"]:                                 # 有模块 → 第二方向卡（并联）
        for cand in ["dir_rtl"] + keys_in_group(GROUP_DIRECTION):
            if cand not in present:
                _rec_push(present, out, cand)
                break
    if ctx["master"]:
        _rec_push(present, out, "duration")           # 有主控卡 → 推荐时长卡


def _rec_static_rules(present: set, out: list, last_added_key) -> None:
    """静态规则：`CardSpec.recommends` 推荐后继。"""
    spec = CARD_CATALOG.get(str(last_added_key or ""))
    if spec is None:
        return
    for key in spec.recommends:
        _rec_push(present, out, key)


def recommendations(sheet, last_added_key) -> list:
    """推荐下一个可拖入的卡片 key 列表（上下文规则在前，最多 6 条）。"""
    nodes, _edges = graph_parts(sheet)
    present = {str(n.get("key")) for n in nodes}
    out: list = []
    _rec_context_rules(present, out, last_added_key, _rec_context(nodes))
    _rec_static_rules(present, out, last_added_key)
    return out[:6]
