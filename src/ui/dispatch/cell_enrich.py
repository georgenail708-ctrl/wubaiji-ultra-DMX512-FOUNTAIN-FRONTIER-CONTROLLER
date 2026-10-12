# -*- coding: utf-8 -*-
"""监控区格子的**语义富化**（把"谁在编辑什么"写到格上，供显示层细化「混合」）。

用户 2026-09-14 定案：
  "如果是 programmer 页面，那么混合应该变成显示条带的起点和终点的设置相对比例和翼块值。
   如果是 effect，那应该显示 effect 名称。matrix 也是，显示该通道正在编辑的对应的效果
   名称，而不是都用一个混合去敷衍我，'混合'是真的存在多种不同效果或者同一效果多种不同
   设置才出现的。"

数据来源（都已在运行时可达，无需新增真源）：
  · **效果名**：`EffectBus` 节点的 `effect_name`（按"设备 ∈ node.fixture_ids"匹配）。
    一个格里匹配到**多个不同**效果名 → 真·多效果（这才是「混合」）。
  · **programmer 条带/翼块**：`ProgrammerPanel` 的 `_collect_range_states()`（按通道类型
    给 base/target/mode）+ `dispatch_capture_state()["rows"]`（按行给 wings/block_size）。

本模块为**纯函数**（不 import Qt，不持状态），便于单测；副作用（读面板/总线）由调用方
以参数注入，保持依赖单向。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def _row_for_ch_type(programmer_panel: Any, ch_type: str):
    """定位"拥有该通道类型"的 Programmer 行（沿用面板内部 ch_type in row._ch_types 惯例）。"""
    try:
        rows = list(getattr(getattr(programmer_panel, "_ch_list", None), "_rows", []) or [])
    except Exception:
        return None
    for r in rows:
        try:
            if ch_type in (getattr(r, "_ch_types", []) or []):
                return r
        except Exception:
            continue
    return None


def strip_info_for_ch_type(programmer_panel: Any, ch_type: str) -> Dict[str, Any]:
    """`ch_type` → 条带/翼块信息 `{mode, base, target, wings, block}`（取不到给空）。

    programmer 的条带状态**按通道类型**存在行上（`ChannelTypeRow._range_states`、
    `_value_wings/_value_block_size`）；本函数只做"ch_type → 该行"的定位与读取。
    """
    if programmer_panel is None or not ch_type:
        return {}
    row = _row_for_ch_type(programmer_panel, ch_type)
    if row is None:
        return {}
    return {**_range_of(row, ch_type), **_wings_of(row)}


def _range_of(row, ch_type: str) -> Dict[str, Any]:
    """条带起止与模式（缺失给空 dict）。"""
    try:
        st = (getattr(row, "_range_states", None) or {}).get(ch_type) or {}
        return {"mode": str(st.get("mode", "") or ""),
                "base": int(st.get("base", -1) or -1),
                "target": int(st.get("target", -1) or -1)}
    except Exception:
        return {}


def _wings_of(row) -> Dict[str, Any]:
    """翼数与块大小（缺失给空 dict）。"""
    try:
        return {"wings": int(getattr(row, "_value_wings", 0) or 0),
                "block": int(getattr(row, "_value_block_size", 0) or 0)}
    except Exception:
        return {}


_EFFECT_PANELS = ("effect", "matrix", "route", "audio")

#: 索引里"该设备**全部**通道"的桶名（节点没声明通道作用域时用；旧行为）。
_ALL_CH = "*"


def _norm_ch(ch) -> str:
    """通道名归一化（**索引与查询两侧必须同用**）。

    ★★ 2026-09-14 深查修正（我引入的显示回归）：格的 `ch_type` 走
      `fixture_table.norm_channel_type` → **小写**（`r/g/b/w/dimmer`）；而节点上报的
      `ch_types` 来自 effect/programmer 的 `CH_TYPES`/`PARAM_GROUPS` → **大写**
      （`R/G/B`）。两侧不归一，索引按 `R` 建桶、查询问 `r` → 查不到，
      连**颜色效果名都会整体消失**（比修之前更糟）。
      这里统一复用既有真源，任何一侧的写法差异都不再影响匹配。
    """
    try:
        from ...processing.dispatch.fixture_table import norm_channel_type
        return str(norm_channel_type(ch) or "")
    except Exception:
        return str(ch or "").strip().lower()


def _ch_types_of(n) -> tuple:
    """节点的**通道作用域**（去重保序；空 = 未声明 → 应用到该设备的全部通道）。

    ★ 2026-09-14 用户实报（显示错误，且"很普遍"）：一个效果明明只对 `pump` 应用，
      监控区却在**所有通道**上显示该效果名。根因是索引只按设备建桶、不按通道，于是
      给该台设备的每个通道格都贴上了效果名。本函数取节点的通道集供按通道过滤。
    """
    try:
        raw = _attr(n, "ch_types", None) or ()
    except Exception:
        return ()
    out: List[str] = []
    for x in raw:
        s = _norm_ch(x)
        if s and s not in out:
            out.append(s)
    return tuple(out)


def nodes_signature(nodes) -> tuple:
    """节点序列的**廉价指纹**（用于跨 tick 复用索引，避免每 tick 重建）。

    只取与"效果名 × 设备集 × 通道集"有关的字段：节点数 + 每个效果节点的
    (效果名, 设备数, 通道集)。设备集合变化（首批导入/换组）或**同尺寸换设备**
    即失效。

    ★ 2026-09-14 修正：原指纹只有 `(面板, 名字, 设备数)` ——
      "4 台 pump 换成另外 4 台"这种**同尺寸换设备**不会让指纹变化 → 索引残留指向
      旧设备，效果名会贴到已经不在作用域的设备上（用户看到的"显示错误"的第二条路径）。
      通道集同理：同一效果改绑通道也必须失效。
    """
    return tuple(x for x in (_one_node_sig(n) for n in (nodes or ())) if x)


def _one_node_sig(n) -> Optional[tuple]:
    """单节点签名；非效果面板或无名 → None（不计入指纹）。"""
    panel = str(_attr(n, "panel", "") or "")
    if panel not in _EFFECT_PANELS:
        return None
    name = _effect_name_of(n)
    if not name:
        return None
    fids = tuple(map(str, _attr(n, "fixture_ids", None) or ()))
    return (panel, name, fids, _ch_types_of(n))


def build_effect_index(nodes) -> Dict[str, Dict[str, Tuple[str, ...]]]:
    """`{设备 id: {通道名 或 "*": (效果名, ...)}}` —— 一次遍历，供逐格 O(1) 查询。

    ★ 为什么要按通道分桶（用户 2026-09-14 实报）：节点自带 `ch_types` 通道作用域；
      只按设备建桶会让"pump 效果"糊到同台设备的 dimmer/R/G/B 等所有通道格上。
      未声明通道（`ch_types` 空）的节点落 `"*"` 桶 = 该设备全部通道（旧行为兼容）。

    ★ 性能（实测踩坑两轮）：
      ① 逐格遍历全部节点匹配 → 2000 台工程 tick 从 16ms 涨到 97ms（格×节点双循环）；
      ② 建索引后仍慢（181ms）：**索引本身**在"20 效果节点 × 2000 台"（4 万次插入）
         下就不廉价，每 tick 重建不可接受。
      → 最终由 `CellEnricher` 按指纹缓存，只在节点集变化时重建。
    """
    idx: Dict[str, Dict[str, List[str]]] = {}
    for n in (nodes or ()):
        try:
            if str(_attr(n, "panel", "") or "") not in _EFFECT_PANELS:
                continue
            _index_one_node(idx, n)
        except Exception:
            continue
    return {fid: {ch: tuple(v) for ch, v in per_ch.items()}
            for fid, per_ch in idx.items()}


def _index_one_node(idx: Dict[str, Dict[str, List[str]]], n) -> None:
    """把单节点的效果名写进倒排索引（同设备同通道同名只记一次）。"""
    name = _effect_name_of(n)
    if not name:
        return
    chs = _ch_types_of(n) or (_ALL_CH,)
    for fid in (_attr(n, "fixture_ids", None) or ()):
        sf = str(fid)
        if not sf:
            continue
        per_ch = idx.setdefault(sf, {})
        for ch in chs:
            bucket = per_ch.setdefault(str(ch), [])
            if name not in bucket:
                bucket.append(name)


def _effect_name_of(n) -> str:
    """节点上的效果名（effect_name 优先，回落 track_type；都空返回 ""）。"""
    try:
        return (str(_attr(n, "effect_name", "") or "").strip()
                or str(_attr(n, "track_type", "") or "").strip())
    except Exception:
        return ""


class CellEnricher:
    """带指纹缓存的富化器（跨 tick 复用索引；`programmer` 条带按 ch_type 缓存）。

    ★ 为什么需要缓存：详见 `build_effect_index` 的两轮实测。索引重建是 O(节点×设备)，
      而 tick 是 10~25Hz —— 必须只在输入变化时重建。
    """

    def __init__(self) -> None:
        self._sig = None
        self._index: Dict[str, Tuple[str, ...]] = {}
        #: 设备集 → 效果名 的记忆表（id 键 + 强引用，见 names_for_cell）
        self._names_memo: Dict[tuple, Tuple[str, ...]] = {}
        self._memo_refs: Dict[tuple, Any] = {}

    def index_for(self, nodes) -> Dict[str, Tuple[str, ...]]:
        """取（必要时重建）节点倒排索引；节点集变化时**同时清**记忆表。"""
        try:
            sig = nodes_signature(nodes)
        except Exception:
            return self._index
        if sig != self._sig:
            self._index = build_effect_index(nodes)
            self._sig = sig
            self._names_memo.clear()
            self._memo_refs.clear()
        return self._index

    def names_for_cell(self, index, fids, ch_type: str = "") -> Tuple[str, ...]:
        """该格设备集**在指定通道上**的效果名（按身份 + 通道记忆化，跨 tick 复用）。

        ★ 为什么按对象身份而不是内容：监控区每 tick 重建表格行时，同一行的
          `fids` 列表是**同一个对象**（要么来自节点、要么来自上次的缓存行），
          用 `id()` 命中即可 O(1)；节点集变化时 `index_for` 会清空记忆表。
        ★ 2026-09-14：记忆键**必须含 ch_type** —— 同一行的不同通道列共享同一个
          `fids` 对象，不含通道就会把"第一个通道算出的结果"复用给整行所有通道
          （这正是"pump 效果糊满所有通道"的第二条传播路径）。
        ★ 强引用：项目纪律——`id()` 键必须同时持对象强引用，否则 id 会被复用
          （本模块用 `_memo_refs` 持有，见 architecture-quick-fix-map 的 id 铁律）。
        """
        if not fids or not index:
            return ()
        ck = _norm_ch(ch_type)
        key = (id(fids), len(fids), ck)
        hit = self._names_memo.get(key)
        if hit is not None:
            return hit
        out = effect_names_for_fixtures(index, fids, ck)
        self._names_memo[key] = out
        self._memo_refs[key] = fids          # 强引用（防 id 复用）
        if len(self._names_memo) > 4096:     # 上限保护（换工程/大批新行时）
            self._names_memo.clear()
            self._memo_refs.clear()
        return out


def effect_names_for_fixtures(index, fixture_ids, ch_type: str = "") -> Tuple[str, ...]:
    """这些设备**在该通道上**正在编辑的效果名（去重保序）。`index` = `build_effect_index()`。

    `ch_type` 空 = 不按通道过滤（取该设备全部通道的名字；供无通道语境的调用方）。

    ★ 性能（第三轮实测）：监控区一行 = 一个**设备类型**，其格覆盖的设备可达数千台
      （聚合取样）。逐格遍历全部 fid 会让 50 格 × 2000 台 = 10 万次查表/次 tick
      （实测 `_enrich` 单独就 162ms）。→ 热路径请走 `CellEnricher.names_for_cell`
      （按"设备集对象身份"记忆化），本函数保留为纯函数供单测/一次性使用。
    """
    wanted = [str(x) for x in (fixture_ids or ()) if str(x)]
    if not wanted or not index:
        return ()
    out: List[str] = []
    for fid in wanted:
        _collect_names_for_fixture(out, index.get(fid), str(ch_type or ""))
    return tuple(out)


def _collect_names_for_fixture(out: List[str], per_ch, ck: str) -> None:
    """把单台设备在该通道上的效果名并入 `out`（保序去重）。

    · `ck` 非空：只取"该通道" + "未声明通道（`*` = 全通道）"两个桶；
    · `ck` 为空：不按通道过滤，取该设备全部桶（供无通道语境的调用方）。

    ★ `ck` 在此**归一化**：这是所有查询路径的公共落点（`effect_names_for_fixtures`
      与 `CellEnricher.names_for_cell` 都汇到这里），归一化放这里才不会漏 ——
      格的 `ch_type` 是小写（`r/g/b`），节点的 `ch_types` 是大写（`R/G/B`），
      不归一化会导致**颜色效果名整体消失**（比不修更糟）。
    """
    if not per_ch:
        return
    ck = _norm_ch(ck)
    if ck:
        buckets = (per_ch.get(ck, ()), per_ch.get(_ALL_CH, ()))
    else:
        buckets = tuple(per_ch.values())
    for bucket in buckets:
        for nm in bucket:
            if nm not in out:
                out.append(nm)


def apply_strip(cell: Any, info: Optional[dict]) -> None:
    """把条带/翼块信息写到格上（`info` 为空则不动；异常吞掉）。"""
    if not info:
        return
    try:
        cell.strip_mode = str(info.get("mode", "") or "")
        cell.strip_base = int(info.get("base", -1))
        cell.strip_target = int(info.get("target", -1))
        cell.strip_wings = int(info.get("wings", 0) or 0)
        cell.strip_block = int(info.get("block", 0) or 0)
    except Exception:
        return


def apply_names(cell: Any, effect_index, memo: Any = None) -> None:
    """把该格设备集**在该格通道上**的效果名写到格上（异常吞掉）。

    ★ 2026-09-14 用户实报修正：必须按 `cell.ch_type` 过滤 —— 否则一个只作用于
      `pump` 的效果会贴到同一行**所有**通道格上（用户："这个问题很普遍"）。

    `memo` 传 `CellEnricher` 时走**记忆化**（热路径必需；否则聚合格的
    数千台设备会被逐台扫描，实测单格语义富化可达 162ms/次 tick）。
    """
    try:
        fids = getattr(cell, "fids", None) or ()
        ch_type = str(getattr(cell, "ch_type", "") or "")
        if memo is not None:
            names = memo.names_for_cell(effect_index, fids, ch_type)
        else:
            names = effect_names_for_fixtures(effect_index, fids, ch_type)
        cell.effect_names = tuple(names)
        cell.effect_name = names[0] if len(names) == 1 else ""
        if names:
            if not hasattr(cell, "_effect_name_base_panel"):
                cell._effect_name_base_panel = getattr(cell, "panel", "")
            cell.panel = "effect"
        elif hasattr(cell, "_effect_name_base_panel"):
            cell.panel = cell._effect_name_base_panel
            del cell._effect_name_base_panel
    except Exception:
        return


def enrich_cell(cell: Any, *, programmer_panel: Any = None, effect_index=None,
                is_programmer: bool = True) -> None:
    """一次补齐两种语义（便捷入口；热路径请分别调 `apply_strip`/`apply_names`）。"""
    if is_programmer:
        try:
            ch_type = str(getattr(cell, "ch_type", "") or "")
            apply_strip(cell, strip_info_for_ch_type(programmer_panel, ch_type))
        except Exception:
            pass
    apply_names(cell, effect_index)


def _attr(obj: Any, name: str, default: Any) -> Any:
    """取属性；支持 dataclass 对象与 `to_dict()` 后的普通 dict。"""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


__all__ = ["strip_info_for_ch_type", "nodes_signature", "build_effect_index",
           "CellEnricher",
           "effect_names_for_fixtures", "apply_strip", "apply_names",
           "enrich_cell"]
