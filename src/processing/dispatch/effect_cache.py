# -*- coding: utf-8 -*-
"""调度台后端：**效果缓存**（可追溯快照 + 持久化，纯数据，无 Qt 依赖）。

一条 EffectCache = 「当时那一组窗格的全部制作状态」的可还原快照。

为什么能"精确到还原所有控件参数"：
    每条 node 里的 `state` 就是各效果面板**既有 save_state() 的原样输出**
    （EffectBrowser.save_state / MatrixSlideshow.save_state / ...）。
    本模块不定义第二套序列化 —— 面板将来加了参数，缓存自动覆盖，
    这是"不复杂化 + 永不漂移"的关键取舍。

★ 不去重（用户硬要求）：框选 N 个窗格就存 N 个 node，即使它们参数完全相同、
  甚至只是时差/角度/速度的微差。`save_from_nodes()` 不做任何按效果合并。

持久化：`to_dict()/from_dict()` 往返无损，写进 `Project.dispatch_state`
（沿用项目既有的"不透明 dict 字段"范式，老存档读成空库，向前兼容）。

还原调用链由上层（MainWindow）注入：
    capture_state(panel) -> dict            # 取该面板当前完整状态
    recall_state(panel, node, state) -> bool # 还原该面板状态与设备作用域
后端只负责"存/取/列/删"，不 import 任何 UI —— 因此可在无 GUI 下单测。
"""
from __future__ import annotations

import json
from copy import deepcopy

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .effect_bus import EffectNode
from .effect_cache_labels import EffectCacheLabels


# ── 反序列化取值助手（老工程/坏数据一律安全回落，不抛）──────────────────────
def _as_float(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_int(v, default: int, minimum: Optional[int] = None) -> int:
    try:
        out = int(v or 0)
    except (TypeError, ValueError):
        return default
    if minimum is not None and out < minimum:
        return default
    return out


def _as_mode(v) -> str:
    """触发模式：未知/缺失一律回落 latch（保守默认，见 EffectCache.mode）。"""
    m = str(v or "latch").lower()
    return m if m in EffectCache.MODES else "latch"


def _clamp_priority(v) -> int:
    """优先级入档归一化：非法/越界夹到 [0, PRIORITY_MAX]（0 = 未设）。"""
    try:
        return max(0, min(int(PRIORITY_MAX), int(v or 0)))
    except (TypeError, ValueError):
        return 0


def _as_chase(v) -> dict:
    """跑灯序列字段归一化（★ R183 T6）—— 实现单一真源在 `chase_runner.normalize_chase`。

    本函数只是"数据层的入口名"（`from_dict` 读它），校验逻辑不在两处各写一份：
    坏数据一律回落**空 dict** = 普通静态场景。
    """
    from .chase_runner import normalize_chase
    return normalize_chase(v)


from .effect_cache_labels import FADE_MAX as FADE_MAX  # noqa: F811

#: 场景优先级档位上限（1..5 档，5 最高；0 = 未设）
from .effect_cache_labels import PRIORITY_MAX as PRIORITY_MAX  # noqa: F811


from .scene_state_pool import pool_states, restore_nodes
from .trigger_keys import RESERVED_KEYS, normalize_key  # noqa: F401

@dataclass
class EffectCache(EffectCacheLabels):
    """一条效果缓存（可调用 / 可编辑 / 可追溯）。

    name  : 显示名（用户可改）；note : 备注（变式溯源）
    nodes : [{"node":..., "state":...}, ...]，长度 = 保存时框选的窗格数（不去重）
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "效果缓存"
    created_at: float = field(default_factory=lambda: time.time())
    note: str = ""
    nodes: List[dict] = field(default_factory=list)
    version: int = 1
    #: 槽位序号（0-based）。旧工程缺 → 默认 0，加载后 `scene_grid.uniquify_positions` 唯一化。
    position: int = 0
    #: 触发模式（用户 2026-09-14 定案）：latch 自锁（点一下保持、再点关闭）/
    #: flash 点动（按住才应用、松手即释放）。
    #:  旧工程缺此字段 → 默认 latch；老工程的 "hold" 由 `effective_mode()` 回落 latch。
    mode: str = "latch"
    #: **键盘绑定**（用户 2026-09-14："每个 scene 可以绑定键盘上的按键…随项目持久化"）：
    #:  单个键名（如 "P"），空 = 未绑定。触发走**与鼠标完全相同**的逻辑。
    key_binding: str = ""
    #: **自动淡入淡出秒数**（用户 2026-09-14："跟哪种模式无关，flash 还是 latch 都
    #: 可以生效"；0 = 瞬时）。触发时该场景的层在 fade_in 秒内 0→1，释放时 1→0。
    fade_in: float = 0.0
    #: **场景优先级**（R178 革新）：0 = 未设（默认，后发接管不变）；1..5 档，
    #: 5 最高。同层通道已被**更高优先级**的激活场景 stamp 过时，低优先级场景
    #: 点亮**跳过 stamp**（不抢通道）——见 `scene_takeover` 的压制逻辑。
    priority: int = 0
    #: ★ R183 T7：**场景页**（VCFrame 分页 / 主流控台 executor 页语义）。
    #: 存储区按页显示，键绑定按页生效；0 = 第一页。旧工程缺该键 → 0（真工程
    #: To The Moon 存储区 0 场景 → 零迁移负担）。
    page: int = 0
    #: ★ R183 T6：**跑灯序列**（Chaser 语义）。空 dict = 普通静态场景；
    #: 非空时本卡是"序列卡"，调用它走 `chase_runner` 步进其 `steps` 里的场景。
    #: 形如 `{"steps":[场景id,…], "order":"loop"|"single"|"pingpong"|"random",
    #: "step_seconds": 1.0}`；坏数据安全回落空 dict（= 退回普通场景）。
    chase: dict = field(default_factory=dict)

    #: 合法触发模式（校验用）。新增模式只改这里 + 消费方分支。
    MODES: Tuple[str, ...] = ("latch", "flash")


    def copy_as_variant(self, new_name: str) -> "EffectCache":
        """复制为变式（新 id/时间/内容）——避免覆盖原效果。

        ★ M6：必须带上触发模式/键盘绑定/淡化时间（原只复制 nodes → 变式丢设置）。
        ★ R183 T6/T7：页号**不带**（变式落在当前页，由 `duplicate()` 落空槽），
          跑灯序列**带**（变式本就是"这条序列的另一版"）。
        """
        dup = EffectCache(name=str(new_name or (self.name + " 副本")),
                          created_at=time.time(), note=self.note,
                          nodes=[dict(e) for e in self.nodes], version=self.version)
        dup.mode = str(self.effective_mode())
        dup.key_binding = str(self.effective_key() or "")
        dup.fade_in = float(self.fade_seconds())
        dup.priority = int(self.priority_level())
        dup.chase = dict(getattr(self, "chase", {}) or {})
        return dup

    # ── 序列化 ────────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        """序列化；★ R140 起把 `state` **池化去重**（体积优化）。

        ★★ R140 用户实报（"项目做多了保存文件特别大"）：一条场景的 `nodes` 里每个节点
          都**完整复制一份面板 state**，而同一场景内绝大多数节点是同面板同状态
          —— 真工程实测：385 个节点只对应 **21 个唯一 state**（18 个被重复引用 382 次），
          79.4 MB 里 **95%** 是重复副本。

        修：`states` = 唯一 state 列表（按内容寻址），`nodes[i]["s"]` = 该节点在池中的
        下标；`state` 键**不再逐节点内联**。读侧 `from_dict` 还原（见那里）。
        兼容：读侧同时认旧格式（逐节点内联 `state`）与新格式（`s` 下标 + `states` 池）。
        """
        entries, pool = pool_states(self.nodes)
        return {"id": self.id, "name": self.name, "created_at": float(self.created_at),
                "note": self.note, "nodes": entries, "states": pool,
                "version": int(self.version), "position": int(self.position),
                "mode": str(self.mode or "latch"),
                "key_binding": self.effective_key(),
                "fade_in": float(self.fade_seconds()),
                "priority": int(self.priority_level()),
                # ★ R183 T7/T6：页号 + 跑灯序列（老工程缺键 → 读侧默认 0/空）
                "page": max(0, int(getattr(self, "page", 0) or 0)),
                "chase": dict(getattr(self, "chase", {}) or {})}

    @classmethod
    def from_dict(cls, d: dict) -> "EffectCache":
        d = d or {}
        states = d.get("states")
        nodes = restore_nodes(d.get("nodes", []),
                              states if isinstance(states, list) else [],
                              EffectNode.from_dict)
        return cls(
            id=str(d.get("id") or uuid.uuid4()),
            name=str(d.get("name", "效果缓存")),
            created_at=_as_float(d.get("created_at"), time.time()),
            note=str(d.get("note", "") or ""),
            nodes=nodes,
            version=_as_int(d.get("version"), 1, minimum=1),
            position=max(0, _as_int(d.get("position"), 0)),   # ★ 负值→卡不可达
            mode=_as_mode(d.get("mode")),
            key_binding=str(d.get("key_binding", "") or ""),
            fade_in=_as_float(d.get("fade_in"), 0.0),
            priority=_clamp_priority(d.get("priority")),
            # ★ R183 T7/T6：页号 + 跑灯序列（缺键/坏数据 → 0 / 空 dict，零迁移）
            page=max(0, _as_int(d.get("page"), 0)),
            chase=_as_chase(d.get("chase")),
        )


class EffectCacheStore:
    """效果缓存列表容器（纯数据 + 增删改查 + 持久化）。

    ★ 线程契约：调用点在 UI 线程与项目加载回调，但保存前收集与自动保存共用同一
      store → 用 `RLock` 保护列表，读取一律返回**独立列表快照**。
    `save_from_nodes` 可注入 `capture_state(panel)->dict`（缺省时只存描述不存状态）。
    """

    _SINGLETON: Optional["EffectCacheStore"] = None
    _SINGLETON_LOCK = threading.Lock()

    def __init__(self) -> None:
        self._items: List[EffectCache] = []
        self._lock = threading.RLock()

    # ── 查询 ──────────────────────────────────────────────────────────────
    def list_caches(self) -> List[EffectCache]:
        with self._lock:
            return list(self._items)

    def count(self) -> int:
        with self._lock:
            return len(self._items)

    def get(self, cache_id: str) -> Optional[EffectCache]:
        cid = str(cache_id or "")
        with self._lock:
            for c in self._items:
                if c.id == cid:
                    return c
        return None

    def search(self, text: str) -> List[EffectCache]:
        """按名/备注/面板/效果名过滤（前端搜索框）。空串=全部。"""
        q = str(text or "").strip().lower()
        items = self.list_caches()
        if not q:
            return items
        out = []
        for c in items:
            hay = [c.name, c.note] + c.panels()
            for e in c.nodes:
                nd = e.get("node", {}) or {}
                hay.append(str(nd.get("effect_name", "") or ""))
                hay.append(str(nd.get("group_name", "") or ""))
            if any(q in str(h).lower() for h in hay):
                out.append(c)
        return out

    # ── 增 ────────────────────────────────────────────────────────────────
    @staticmethod
    def _build_entries(nodes: List[EffectNode],
                       capture_state: Optional[Callable[[str], dict]] = None
                       ) -> List[dict]:
        """把窗格节点转成缓存条目（逐节点、**不去重**）。

        同一面板只取一次 state（save_state 是整面板状态），但每个节点都保留
        自己的 node（含各自的设备作用域），所以多节点还原时能各归各位。
        """
        entries: List[dict] = []
        state_by_panel: Dict[str, dict] = {}
        for n in (nodes or []):
            if n is None:
                continue
            panel = str(n.panel)
            if panel not in state_by_panel and capture_state is not None:
                try:
                    st = capture_state(panel)
                    state_by_panel[panel] = deepcopy(st) if isinstance(st, dict) else {}
                except Exception:
                    state_by_panel[panel] = {}
            entries.append({
                "node": n.to_dict(),
                "state": dict(state_by_panel.get(panel, {})),
            })
        return entries

    def save_from_nodes(self, name: str,
                        nodes: List[EffectNode],
                        capture_state: Optional[Callable[[str], dict]] = None,
                        note: str = "",
                        position: Optional[int] = None) -> EffectCache:
        """把（框选出的）窗格节点存成一条缓存/场景。

        ★ 逐节点存，**不按效果/设备去重**（用户硬要求：微差即两种效果）。
        `position` 非空 = 存进指定槽位（存储区点空槽新建）；为空 = 追加末尾。

        ★★ 深查修正（场景被吞）：显式 `position` 原先盲信传入值，槽位已占时会造出
          两个同 position 的场景 → 视图映射（后写覆盖）令先存者不可见也点不到。
          位置唯一性收口在 `scene_grid.free_position`（见该函数说明）。
        """
        with self._lock:
            from .scene_grid import free_position
            pos = free_position([int(getattr(c, "position", 0) or 0) for c in self._items], position)
            cache = EffectCache(
                name=str(name or "效果缓存"),
                note=str(note or ""),
                nodes=self._build_entries(nodes, capture_state),
                position=pos,
            )
            self._items.append(cache)
        return cache

    def _next_position(self) -> int:
        """第一个空槽（与 scene_grid.next_free_position 同语义，避免两套算术）。"""
        from .scene_grid import next_free_position
        with self._lock:
            return int(next_free_position([int(getattr(c, "position", 0) or 0)
                                           for c in self._items]))

    def positions(self) -> List[int]:
        with self._lock:
            return [int(getattr(c, "position", 0) or 0) for c in self._items]

    def ordered(self) -> List[EffectCache]:
        """按槽位序号排序（存储区编号网格用）。"""
        with self._lock:
            items = list(self._items)
        items.sort(key=lambda c: (int(getattr(c, "position", 0) or 0), str(c.name)))
        return items

    def move(self, cache_id: str, position: int) -> bool:
        """移到指定槽位；该槽已被占用则**与占用者互换**（场景页拖拽语义）。

        ★ 复用 `scene_grid.move_position` 的纯算术，不在这里再写一遍交换逻辑
          （"位置语义"只有一处实现，改不会漏）。
        """
        from .scene_grid import move_position
        with self._lock:
            me = next((c for c in self._items if c.id == str(cache_id)), None)
            if me is None:
                return False
            changes = move_position(int(getattr(me, "position", 0) or 0), int(position),
                                    [int(getattr(c, "position", 0) or 0)
                                     for c in self._items])
            if not changes:
                return False
            for c in self._items:
                p_old = int(getattr(c, "position", 0) or 0)
                if p_old in changes:
                    c.position = int(changes[p_old])
            return True

    def add(self, cache: EffectCache) -> EffectCache:
        if cache is not None:
            with self._lock: self._items.append(cache)
        return cache

    # ── 改 ────────────────────────────────────────────────────────────────
    def rename(self, cache_id: str, new_name: str) -> bool:
        name = str(new_name or "").strip()
        if not name:
            return False
        c = self.get(cache_id)
        if c is None:
            return False
        c.name = name
        return True

    def set_note(self, cache_id: str, note: str) -> bool:
        c = self.get(cache_id)
        if c is None:
            return False
        c.note = str(note or "")
        return True

    def set_mode(self, cache_id: str, mode: str) -> bool:
        """设置触发模式（latch 开关 / hold 触发）；非法值拒绝并返回 False。"""
        m = str(mode or "").lower()
        if m not in EffectCache.MODES:
            return False
        c = self.get(cache_id)
        if c is None:
            return False
        c.mode = m
        return True

    def set_key_binding(self, cache_id: str, key: str) -> bool:
        """设置/清除键盘绑定（`key` 归一化后存；空 = 解绑）。"""
        c = self.get(cache_id)
        if c is None:
            return False
        c.key_binding = normalize_key(key)
        return True

    def set_fade(self, cache_id: str, seconds: float) -> bool:
        """设置自动淡入淡出秒数（夹到 [0, FADE_MAX]；0 = 瞬时）。"""
        c = self.get(cache_id)
        if c is None:
            return False
        try:
            c.fade_in = max(0.0, min(float(FADE_MAX), float(seconds or 0.0)))
        except (TypeError, ValueError):
            return False
        return True

    def set_priority(self, cache_id: str, level: int) -> bool:
        """设置场景优先级（夹到 [0, PRIORITY_MAX]；0 = 未设/清除）。"""
        c = self.get(cache_id)
        if c is None:
            return False
        try:
            c.priority = max(0, min(int(PRIORITY_MAX), int(level or 0)))
        except (TypeError, ValueError):
            return False
        return True

    def set_position(self, cache_id: str, position: int) -> bool:
        """写入槽位序号（存进指定空槽；带占用则拒绝，由调用方决定是否互换）。"""
        c = self.get(cache_id)
        if c is None:
            return False
        try:
            c.position = int(position)
        except (TypeError, ValueError):
            return False
        return True

    def update_from_nodes(self, cache_id: str, nodes: List[EffectNode],
                          capture_state: Optional[Callable[[str], dict]] = None) -> bool:
        """用当前窗格覆盖某条缓存（"编辑后存回"）。

        ★ R183 审计⑦B P2-9：内容被替换后必须**通报 stamp 台账摘账**（旧 nodes 的
          地址记账会让低优先级场景被幽灵压制）。通报点刻意留在 **UI 层**
          （`panel_library._on_update_cache`）：本包不得 import `src.ui`
          （CLAUDE §10.2 反向依赖禁令），且台账本就是 UI 侧运行期状态。
        """
        c = self.get(cache_id)
        if c is None:
            return False
        c.nodes = self._build_entries(nodes, capture_state)
        c.created_at = time.time()
        return True

    def duplicate(self, cache_id: str, new_name: str = "") -> Optional[EffectCache]:
        c = self.get(cache_id)
        if c is None:
            return None
        dup = c.copy_as_variant(new_name or (c.name + " 副本"))
        try: dup.position = int(self._next_position())   # ★ M6：落空槽，不压首卡
        except Exception: pass
        with self._lock: self._items.append(dup)
        return dup
    # ── 删 ────────────────────────────────────────────────────────────────
    def remove(self, cache_id: str) -> bool:
        cid = str(cache_id or "")
        with self._lock:
            before = len(self._items)
            self._items = [c for c in self._items if c.id != cid]
            return len(self._items) != before

    def remove_many(self, cache_ids: List[str]) -> int:
        """多选删除（框选/多选后删）。返回删除数。"""
        ids = {str(x) for x in (cache_ids or [])}
        with self._lock:
            before = len(self._items)
            self._items = [c for c in self._items if c.id not in ids]
            return before - len(self._items)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    # ── 持久化 ────────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {"version": 1, "caches": [c.to_dict() for c in self.list_caches()]}

    def from_dict(self, d: dict) -> None:
        """整体替换为存档内容（往返无损；老工程空 dict → 空库）。"""
        items: List[EffectCache] = []
        if isinstance(d, dict):
            for item in (d.get("caches", []) or []):
                if isinstance(item, dict):
                    try:
                        items.append(EffectCache.from_dict(item))
                    except Exception: continue   # 单条坏数据不拖垮整库
        # ★ R131：旧工程 position 全落 0 → 视图 by_pos 后写覆盖 → 只剩一张可见可点
        try:
            from .scene_grid import uniquify_positions as _uq; _uq(items)
        except Exception: pass
        with self._lock: self._items = items

# ── 进程内单例 ────────────────────────────────────────────────────────────
def get_effect_cache_store() -> EffectCacheStore:
    """返回进程内唯一的效果缓存库实例（首次创建加锁，避免并发造出两个）。"""
    if EffectCacheStore._SINGLETON is None:
        with EffectCacheStore._SINGLETON_LOCK:
            if EffectCacheStore._SINGLETON is None:
                EffectCacheStore._SINGLETON = EffectCacheStore()
    return EffectCacheStore._SINGLETON
