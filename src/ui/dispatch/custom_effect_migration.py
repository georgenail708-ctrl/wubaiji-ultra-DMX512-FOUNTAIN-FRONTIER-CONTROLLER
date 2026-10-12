# -*- coding: utf-8 -*-
"""**自定义效果 → 调度台场景卡**的一次性迁移器（R178 D：先迁移再删库）。

为什么需要：自定义效果库（`src/ui/custom_effect_store.py`，已删除）与调度台
场景卡（`EffectCache`）语义高度重叠 —— 都是"把当时的 effect 配置快照存下来、
随时调用"。R178 定案删除前者，历史数据由本模块**无损搬进**调度台。

★★ 迁移器必须读 `project.custom_effects_data` 的**原始 dict**：
  `CustomEffectStore.from_dict` 会静默丢掉"无 base_preset_id"的条目（场景快照型
  恰恰 base_preset_id 为空）—— 走它就把最值钱的场景快照全丢了。

两种条目的搬法（都落成 effect 面板的 node+state 六元组结构）：
  · 普通型（base_preset_id 非空）：一格 node，state.active_effects 一条
    （preset_id=base_preset_id，params/ch_types/sort_mode/speed/base_order 全带）。
  · 场景快照型（snapshot_states 非空）：每个快照条目一格 node（_key 即
    track_type/group_key），state.active_effects 按条重建 —— recall 时经
    `effect_browser.dispatch_recall_state` 完整回灌 `_external_states`。

幂等：目标名 = "[自定义]" + 原名；库里已有同名卡则跳过（重复跑不重复搬）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

#: 迁移卡名前缀（幂等判定 + 用户可一眼认出迁移来源）
MIGRATED_PREFIX = "[自定义]"


@dataclass
class MigrationReport:
    """一次迁移的结果账。"""
    migrated: int = 0            # 新搬进的场景卡数
    skipped: int = 0             # 幂等跳过（已有同名 [自定义] 卡）
    scene_snapshots: int = 0     # 其中场景快照型卡数
    plain: int = 0               # 其中普通型卡数
    failures: int = 0            # 无法重建的条目数（既非快照也无 base）
    names: List[str] = field(default_factory=list)   # 本次新搬进的卡名

    def summary(self) -> str:
        if not (self.migrated or self.skipped or self.failures):
            return "没有可迁移的自定义效果"
        parts = []
        if self.migrated:
            parts.append(f"已迁移 {self.migrated} 条到调度台"
                         f"（普通 {self.plain} · 场景快照 {self.scene_snapshots}）")
        if self.skipped:
            parts.append(f"跳过 {self.skipped} 条（此前已迁移）")
        if self.failures:
            parts.append(f"{self.failures} 条无法重建（缺 base_preset_id 且非场景快照）")
        return "；".join(parts)


def _float_or(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _str_list(v) -> List[str]:
    try:
        return [str(x) for x in (v or []) if str(x)]
    except Exception:
        return []


def _group_key_of(fixture_ids: List[str]) -> str:
    """设备集 → group_key（与 EffectBus 的内容寻址同构："grp:id1|id2…"）。"""
    ids = [x for x in fixture_ids if x]
    return "grp:" + "|".join(ids) if ids else "__all__"


def _snapshot_key(s: dict) -> tuple:
    """快照 _key 二元组（缺省 ("","")）→ (track_type, group_key)。"""
    key = s.get("_key") or ["", ""]
    return str(key[0] or ""), str(key[1] or "")


def _snapshot_cell(s: dict) -> dict:
    """场景快照的一条 → active_effects 条目（六元组全带）。"""
    params = dict(s.get("params") or {})
    group_ids = _str_list(s.get("group_ids"))
    track_type, group_key = _snapshot_key(s)
    base_order = (_str_list(s.get("base_order_ids"))
                  or _str_list(params.get("_output_base_order_ids")) or group_ids)
    return {
        "track_type": track_type,
        "group_key": group_key,
        "preset_id": str(s.get("preset_id") or ""),
        "params": params,
        "ch_types": _str_list(s.get("ch_types")),
        "group_ids": group_ids,
        "base_order_ids": base_order,
        "speed_factor": _float_or(s.get("speed_factor"), 1.0),
        "virtual_t": _float_or(s.get("virtual_t"), 0.0),
        "sort_mode": str(s.get("sort_mode") or "index"),
    }


def _plain_cell(entry: dict) -> dict:
    """普通型条目 → active_effects 条目（参数里带输出设备集则沿用）。"""
    params = dict(entry.get("params") or {})
    fixture_ids = _str_list(params.get("_output_fixture_ids")) \
        or _str_list(params.get("_output_base_order_ids"))
    base_order = _str_list(params.get("_output_base_order_ids")) or fixture_ids
    speed = _float_or(params.get("_state_speed_factor"),
                      _float_or(params.get("_global_speed_factor"), 1.0))
    return {
        "track_type": "",
        "group_key": _group_key_of(fixture_ids),
        "preset_id": str(entry.get("base_preset_id") or ""),
        "params": params,
        "ch_types": _str_list(entry.get("ch_types")),
        "group_ids": fixture_ids,
        "base_order_ids": base_order,
        "speed_factor": speed,
        "virtual_t": 0.0,
        "sort_mode": str(entry.get("sort_mode") or "index"),
    }


def _node_of(cell: dict, effect_name: str) -> dict:
    """active_effects 条目 → 同构的监控区节点（panel=effect，key 与条目一致）。"""
    return {
        "panel": "effect",
        "track_type": str(cell.get("track_type") or ""),
        "group_key": str(cell.get("group_key") or ""),
        "group_name": "",
        "fixture_ids": _str_list(cell.get("group_ids")),
        "effect_id": str(cell.get("preset_id") or ""),
        "effect_name": str(effect_name or ""),
        "category": "",
        "layers": ["effect"],
        "summary": "",
    }


def _existing_names(store) -> set:
    """库里已有卡名集合（幂等判定）。"""
    try:
        return {str(getattr(c, "name", "")) for c in store.list_caches()}
    except Exception:
        return set()


def _free_pos(store) -> int:
    """下一个空槽（位置唯一性收口在 scene_grid.free_position）。"""
    try:
        from ...processing.dispatch.scene_grid import free_position
        return int(free_position(store.positions()))
    except Exception:
        pos = store.positions() or []
        return (max(pos) + 1) if pos else 0


def _append_cache(store, name: str, note: str, nodes: List[dict],
                  state_by_cell: List[dict]) -> None:
    """把"一格一条 node+state"的迁移卡写进库（state = 整面板 active_effects）。

    state 是**整面板**快照格式（同 EB save_state）：每格节点带同一份
    active_effects，`_archived_cell_state` 按 track_type+group_key 各取各的。
    """
    from ...processing.dispatch.effect_cache import EffectCache
    active = [dict(c) for c in state_by_cell]
    entries = []
    for nd in nodes:
        entries.append({"node": dict(nd),
                        "state": {"version": 1, "active_effects": active,
                                  "global_speed": 1.0, "effects_playing": True}})
    store.add(EffectCache(name=name, note=note, nodes=entries,
                          position=_free_pos(store)))


def migrate_custom_effects(project, store=None) -> MigrationReport:
    """把 `project.custom_effects_data`（原始 dict）迁成调度台场景卡。幂等。"""
    from ...processing.dispatch import get_effect_cache_store
    if store is None:
        store = get_effect_cache_store()
    rep = MigrationReport()
    raw = getattr(project, "custom_effects_data", None)
    entries = raw.get("effects", []) or [] if isinstance(raw, dict) else []
    if not entries:
        return rep
    known = _existing_names(store)
    for entry in entries:
        _migrate_one(store, entry, known, rep)
    return rep


def _migrate_one(store, entry, known, rep) -> None:
    """单条迁移（循环体拆出降 CC）：坏条目计 failures，重名跳过，成功入 rep。"""
    if not isinstance(entry, dict):
        rep.failures += 1
        return
    name = MIGRATED_PREFIX + str(entry.get("name") or "自定义效果")
    if name in known:
        rep.skipped += 1
        return
    try:
        cells = _cells_of(entry)
        if not cells:
            rep.failures += 1
            return
        note = f"custom_effect:{entry.get('id', '')}"
        nodes = [_node_of(c, str(entry.get("name") or "")) for c in cells]
        _append_cache(store, name, note, nodes, cells)
        known.add(name)
        rep.names.append(name)
        rep.migrated += 1
        if _is_scene_snapshot(entry):
            rep.scene_snapshots += 1
        else:
            rep.plain += 1
    except Exception:
        rep.failures += 1


def _is_scene_snapshot(entry: dict) -> bool:
    """该条目是否场景快照型（snapshot_states 非空）。"""
    return bool(entry.get("snapshot_states"))


def _cells_of(entry: dict) -> List[dict]:
    """一条原始条目 → active_effects 单元列表（空 = 无法重建）。"""
    if _is_scene_snapshot(entry):
        cells = [_snapshot_cell(s) for s in (entry.get("snapshot_states") or [])
                 if isinstance(s, dict) and s.get("preset_id")]
        return cells
    if str(entry.get("base_preset_id") or ""):
        return [_plain_cell(entry)]
    return []


__all__ = ["MigrationReport", "migrate_custom_effects", "MIGRATED_PREFIX"]


def run_migration_ui(win, new_proj) -> None:
    """装载点迁移 + 面板刷新 + 结果弹窗（main_window._load_project 调用，压基线行数）。"""
    rep = migrate_custom_effects(new_proj)
    if not rep.migrated:
        return
    w = getattr(win, "_dispatch_widget", None)
    if w is not None and hasattr(w, "refresh_saved"):
        w.refresh_saved()   # 库已变，卡片列表重画（面板未建则懒建自会读）
    from PyQt6.QtWidgets import QMessageBox
    QMessageBox.information(
        win, "调度台迁移",
        "自定义效果已迁移到调度台场景卡：\n\n" + rep.summary()
        + "\n\n之后请用调度台存储区保存/调用（右键可改模式/淡化/优先级）。")
