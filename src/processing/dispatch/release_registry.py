# -*- coding: utf-8 -*-
"""调用层释放注册表（纯数据，无 Qt）—— 叠加/释放语义的唯一真源。

键 = (clip_id, control_key)：两个 clip 都驱动同一控件时，释放必须按 clip 粒度
（设计 v2.1 §5.3）。released 的键在轨迹回放写层前被跳过；
"页面全部释放" = clear()。
持久化：随 dispatch_state 落盘（to_dict/from_dict 往返无损）。
"""
from __future__ import annotations

import threading
from typing import Dict, FrozenSet, Optional, Tuple

_ReleaseKey = Tuple[str, str]


class ControlReleaseRegistry:
    """(clip_id, control_key) → 已释放。线程安全（RLock + 快照读）。"""

    _SINGLETON: Optional["ControlReleaseRegistry"] = None
    _SINGLETON_LOCK = threading.Lock()

    def __init__(self) -> None:
        self._released: Dict[_ReleaseKey, bool] = {}
        self._lock = threading.RLock()

    # ── 查 ────────────────────────────────────────────────────────────────
    def is_released(self, clip_id: str, control_key: str) -> bool:
        cid, k = str(clip_id or ""), str(control_key or "")
        with self._lock:
            return bool(self._released.get((cid, k))
                        or self._released.get(("*", k)))

    def released_keys(self) -> FrozenSet[_ReleaseKey]:
        with self._lock:
            return frozenset(self._released.keys())

    def released_for_clip(self, clip_id: str) -> FrozenSet[str]:
        """该 clip 的有效释放集 = 专属对 + 通配对（控件级释放跨所有 clip）。"""
        cid = str(clip_id or "")
        with self._lock:
            own = {k for (c, k) in self._released if c == cid}
            wild = {k for (c, k) in self._released if c == "*"}
            return frozenset(own | wild)

    def count(self) -> int:
        with self._lock:
            return len(self._released)

    # ── 改 ────────────────────────────────────────────────────────────────
    def release(self, clip_id: str, control_key: str) -> bool:
        """释放该控件的调用层；返回是否新增（幂等）。存在即已释放。"""
        k = (str(clip_id or ""), str(control_key or ""))
        with self._lock:
            if k in self._released:
                return False
            self._released[k] = True
            return True

    def release_control(self, control_key: str) -> int:
        """按控件键释放（跨所有 clip）：存通配对 ("*", key)。返回新增数（0/1）。"""
        k = str(control_key or "")
        with self._lock:
            if ("*", k) in self._released:
                return 0
            self._released[("*", k)] = True
            return 1

    def release_clip(self, clip_id: str) -> int:
        """释放某 clip 的全部控件层。返回新增数。"""
        cid = str(clip_id or "")
        with self._lock:
            keys = [k for k in self._released if k[0] == cid]
            for k in keys:
                self._released[k] = True
            return len(keys)

    def release_all(self) -> int:
        """页面级"释放全部调用层"。返回本次新增数（幂等）。"""
        with self._lock:
            before = len(self._released)
            for k in list(self._released):
                self._released[k] = True
            return before - len([k for k, v in self._released.items() if v]) + len(self._released) - before                 if False else len(self._released) - before

    def restore(self, clip_id: str, control_key: str) -> bool:
        """撤销释放（重新叠加）。"""
        k = (str(clip_id or ""), str(control_key or ""))
        with self._lock:
            if self._released.get(k):
                self._released.pop(k, None)
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._released.clear()

    # ── 持久化 ────────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        with self._lock:
            return {"version": 1,
                    "released": [[c, k] for (c, k) in self._released.keys()]}

    def from_dict(self, d: dict) -> None:
        items: Dict[_ReleaseKey, bool] = {}
        if isinstance(d, dict):
            for pair in (d.get("released", []) or []):
                try:
                    if isinstance(pair, (list, tuple)) and len(pair) == 2:
                        items[(str(pair[0]), str(pair[1]))] = True
                except Exception:
                    continue
        with self._lock:
            self._released = items


def get_release_registry() -> ControlReleaseRegistry:
    """进程内单例（双检锁）。"""
    if ControlReleaseRegistry._SINGLETON is None:
        with ControlReleaseRegistry._SINGLETON_LOCK:
            if ControlReleaseRegistry._SINGLETON is None:
                ControlReleaseRegistry._SINGLETON = ControlReleaseRegistry()
    return ControlReleaseRegistry._SINGLETON
