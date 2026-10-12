"""
Programmer CUE 库 —— 纯数据层（无 Qt 依赖）。

只储存「静态位置信息」的快照：每台设备每个逻辑通道当前的整数值
（dimmer / 泵 pump / 电机角度 motor_x/y/z / 阀 valve / 功能通道等）。
不涉及任何时间线、效果、动态曲线——那些由 effect / timeline 体系独立持有。

结构：
    ProgrammerCue        单条 CUE：id / name / created_at / values 快照
    ProgrammerCueStore   CUE 列表的增删改查 + to_dict/from_dict
    get_programmer_cue_store()   进程内单例访问器

持久化（写进 Project 存档）由上层统一接入：本模块只提供
to_dict()/from_dict()，保证往返无损。

★★ R182：静态位置 CUE 的**前端**（Programmer 工具栏 Store 按钮 / CUE 库子页 /
CueGrid 静态卡片）已按用户指令删除；本数据层**保留不动**——旧工程的
`programmer_cues_data` 字段仍照常往返，调度台静态值消费走
`ProgrammerPanel.snapshot_static_values()`。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _coerce_value(raw) -> int:
    """把任意输入收敛为 0-255 的整数 DMX 静态值。"""
    try:
        v = int(round(float(raw)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(255, v))


def _coerce_values(values) -> Dict[str, Dict[str, int]]:
    """把 {fixture_id: {ch_type: value}} 规整为干净的纯数据 dict。"""
    out: Dict[str, Dict[str, int]] = {}
    if not isinstance(values, dict):
        return out
    for fid, channels in values.items():
        if not isinstance(channels, dict):
            continue
        fid_s = str(fid)
        clean: Dict[str, int] = {}
        for ch_type, raw in channels.items():
            clean[str(ch_type)] = _coerce_value(raw)
        if clean:
            out[fid_s] = clean
    return out


def _coerce_range_states(states) -> Dict[str, Any]:
    """把 {ch_type: {mode, base, target}} 规整为干净的条带形状表（0-255 夹紧）。"""
    out: Dict[str, Any] = {}
    if not isinstance(states, dict):
        return out
    for ct, st in states.items():
        if not isinstance(st, dict):
            continue
        mode = str(st.get("mode", "flat") or "flat")
        if mode not in ("flat", "one_way", "two_way"):
            mode = "flat"
        out[str(ct)] = {
            "mode": mode,
            "base": _coerce_value(st.get("base", 0)),
            "target": _coerce_value(st.get("target", 0)),
        }
    return out


@dataclass
class ProgrammerCue:
    """一条静态位置 CUE。

    values          : {fixture_id: {ch_type: int(0-255)}}  仅静态位置量。
    source_group_ids: 存快照时活跃的设备组 fixture_id 列表（用于调用时自动恢复该组）。
    base_order_ids  : 存快照时的设备顺序（base_order，控件显示顺序）。
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "CUE"
    created_at: float = field(default_factory=lambda: time.time())
    values: Dict[str, Dict[str, int]] = field(default_factory=dict)
    source_group_ids: List[str] = field(default_factory=list)
    base_order_ids: List[str] = field(default_factory=list)
    #: ★ 2026-09-14 补（与场景存储同源的漏存点）：**条带形状** {ch_type: {mode, base, target}}。
    #:   不加这条，CUE 调回操作台时 `restore_static_snapshot` 只能按均值压成 flat ——
    #:   用户存的 `128-255 W4 B2 ↑` 条带退化成"平均值"（真实数据丢失，非显示问题）。
    range_states: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.values = _coerce_values(self.values)
        try:
            self.range_states = _coerce_range_states(self.range_states)
        except Exception:
            self.range_states = {}

    def fixture_count(self) -> int:
        return len(self.values)

    def channel_count(self) -> int:
        return sum(len(v) for v in self.values.values())

    def created_label(self) -> str:
        try:
            return time.strftime("%m-%d %H:%M", time.localtime(self.created_at))
        except Exception:
            return ""

    def copy_values(self) -> Dict[str, Dict[str, int]]:
        return {fid: dict(chs) for fid, chs in self.values.items()}

    def copy_range_states(self) -> Dict[str, Any]:
        """条带形状的深拷贝（调用方不得就地改内部状态）。"""
        return {str(ct): dict(st) for ct, st in (self.range_states or {}).items()
                if isinstance(st, dict)}

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "created_at": float(self.created_at),
            "values": {fid: dict(chs) for fid, chs in self.values.items()},
        }
        if self.source_group_ids:
            d["source_group_ids"] = list(self.source_group_ids)
        if self.base_order_ids:
            d["base_order_ids"] = list(self.base_order_ids)
        if self.range_states:
            d["range_states"] = self.copy_range_states()   # ★ 条带形状随工程往返
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ProgrammerCue":
        d = d or {}
        try:
            created = float(d.get("created_at", time.time()))
        except (TypeError, ValueError):
            created = time.time()
        return cls(
            id=str(d.get("id") or uuid.uuid4()),
            name=str(d.get("name", "CUE")),
            created_at=created,
            values=_coerce_values(d.get("values", {})),
            source_group_ids=list(d.get("source_group_ids", []) or []),
            base_order_ids=list(d.get("base_order_ids", []) or []),
            range_states=_coerce_range_states(d.get("range_states", {})),
        )


class ProgrammerCueStore:
    """Programmer 静态位置 CUE 列表的容器。"""

    def __init__(self):
        self._cues: List[ProgrammerCue] = []

    # ── 查询 ──────────────────────────────────────────────────────────────
    def list_cues(self) -> List[ProgrammerCue]:
        return list(self._cues)

    def count(self) -> int:
        return len(self._cues)

    def get(self, cue_id: str) -> Optional[ProgrammerCue]:
        for c in self._cues:
            if c.id == cue_id:
                return c
        return None

    # ── 增 ────────────────────────────────────────────────────────────────
    def add(self, cue: ProgrammerCue) -> ProgrammerCue:
        self._cues.append(cue)
        return cue

    def add_snapshot(self, name: str, values: Dict[str, Dict[str, int]],
                     source_group_ids: Optional[List[str]] = None,
                     base_order_ids: Optional[List[str]] = None, *,
                     range_states: Optional[Dict[str, Any]] = None) -> ProgrammerCue:
        """从当前条带快照新建一条 CUE，携带设备组、顺序与**条带形状**信息。

        `range_states` 是关键字专属参数（保持位置参数 ≤5 的架构纪律）。
        """
        cue = ProgrammerCue(
            name=str(name or "CUE"),
            values=_coerce_values(values),
            source_group_ids=list(source_group_ids or []),
            base_order_ids=list(base_order_ids or []),
            range_states=_coerce_range_states(range_states or {}),
        )
        return self.add(cue)

    # ── 改 ────────────────────────────────────────────────────────────────
    def rename(self, cue_id: str, new_name: str) -> bool:
        cue = self.get(cue_id)
        if cue is None:
            return False
        name = str(new_name or "").strip()
        if not name:
            return False
        cue.name = name
        return True

    def update_values(self, cue_id: str, values: Dict[str, Dict[str, int]],
                      range_states: Optional[Dict[str, Any]] = None) -> bool:
        """用新的快照覆盖已存 CUE 的静态值（重新抓取当前条带时使用）。

        ★ 覆盖时条带形状要一起换（传 None 表示"不提供"→ 保留旧形状；传 {} 表示清空）。
        """
        cue = self.get(cue_id)
        if cue is None:
            return False
        cue.values = _coerce_values(values)
        if range_states is not None:
            cue.range_states = _coerce_range_states(range_states)
        cue.created_at = time.time()
        return True

    # ── 删 ────────────────────────────────────────────────────────────────
    def remove(self, cue_id: str) -> bool:
        before = len(self._cues)
        self._cues = [c for c in self._cues if c.id != cue_id]
        return len(self._cues) != before

    def clear(self) -> None:
        self._cues.clear()

    # ── 持久化 ────────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {"cues": [c.to_dict() for c in self._cues]}

    def from_dict(self, d: dict) -> None:
        """整体替换为存档内容（往返无损）。"""
        self._cues = []
        if not isinstance(d, dict):
            return
        for item in d.get("cues", []) or []:
            if isinstance(item, dict):
                self._cues.append(ProgrammerCue.from_dict(item))


# ── 进程内单例 ────────────────────────────────────────────────────────────
_STORE_SINGLETON: Optional[ProgrammerCueStore] = None


def get_programmer_cue_store() -> ProgrammerCueStore:
    """返回进程内唯一的 Programmer CUE 库实例。"""
    global _STORE_SINGLETON
    if _STORE_SINGLETON is None:
        _STORE_SINGLETON = ProgrammerCueStore()
    return _STORE_SINGLETON
