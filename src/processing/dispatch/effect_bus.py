# -*- coding: utf-8 -*-
"""调度台后端：效果制作**统一总线**（只读聚合，纯数据，无 Qt 依赖）。

存在理由（用户需求）：各效果制作面板（Programmer / Effect / Linker / Matrix /
音频 / 跑动编排 / 摇杆）各自持有"当前对哪些设备、在做什么效果"，但这些状态
互相不可见，也没有一个地方能回答"现在到底有哪些效果在跑、各对哪些设备"。
调度台要在左侧监控区把它们显示成窗格、并据此保存/还原，所以需要这一层聚合。

**它不是第二条输出总线**：输出真源仍是 NetworkManager 的层 `_layers`
（见 constants.ALL_LAYERS）。本模块只存"描述"（哪个面板/哪组设备/什么效果/
差异摘要/是否在跑），不碰层、不碰 DMX 数值 —— 这是刻意的单一真源纪律，
避免出现"两套总线说不一致"的新债。

★★ 身份契约（不可违反，用户明确要求）──────── 见 effect_bus.cell_key()
    "即使微小的差异都算做两种效果"：同一效果作用到不同设备、或仅仅角度/速度/
    时差不同，都必须是**两个独立窗格**。
    因此身份键 = (panel, track_type, group_key)，其中 group_key 是按设备 id
    内容寻址的（"grp:id1|id2|..." 或 "__all__"）。**任何按 effect_id /
    effect_name / category 的合并都是 bug**；category 只用于着色，不参与身份。

使用方式（各面板只加一行，失败不影响出光）：
    from ..processing.dispatch import EffectBus, EffectNode
    EffectBus.instance().report(EffectNode(...))
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# 身份键类型：(panel, track_type, group_key)
CellKey = Tuple[str, str, str]


def _str_list(raw) -> List[str]:
    """非空字符串列表；**容忍畸形输入**（None/标量/dict → 尽力取，绝不抛）。

    ★ 2026-09-14 深查修正（打开损坏工程即崩）：原为
      `[str(x) for x in (d.get("ch_types", []) or []) if str(x)]` —— 字段若是标量
      （`ch_types: 42`）会 TypeError: 'int' object is not iterable，
      而节点来自工程/场景 JSON（用户可能手改过或半写入）→ 打开工程直接抛异常。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw else []
    if isinstance(raw, dict):
        raw = list(raw.keys())
    try:
        return [str(x) for x in raw if str(x)]
    except TypeError:
        return [str(raw)] if str(raw) else []


def _dict_or_empty(raw) -> dict:
    """dict 或空 dict；**容忍畸形输入**（非 dict 不抛，退回空 dict）。

    ★ 深查修正：原为 `dict(d.get("extra", {}) or {})` —— 字段若是标量/列表会
      ValueError/TypeError（`dict("x")`、`dict(1)`）→ 打开工程即崩。
    """
    return dict(raw) if isinstance(raw, dict) else {}


def _as_float(raw, default: float) -> float:
    """宽容浮点解析（坏数据回落默认值，不抛）。"""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _node_content_sig(n: EffectNode) -> tuple:
    """节点的**廉价内容签名**（R194/P2-04 revision 判变用）。

    = (panel, effect_name, fixture_ids, ch_types)，与调度台富化指纹
    `cell_enrich._one_node_sig` 同口径同量级：快表消费的"效果名×设备×通道
    作用域"任一变化都能被抓住，而 summary/level 等纯展示字段不进来
    （它们变化不值得让三块监控视图整体重建）。**刻意不含 updated_at** ——
    面板 4Hz 重报时 report 会刷新它，含它等于永远判变。
    """
    return (
        str(n.panel),
        str(n.effect_name),
        tuple(map(str, n.fixture_ids or ())),
        tuple(str(x) for x in (n.ch_types or ())),
    )


@dataclass
class EffectNode:
    """一个效果制作节点 = 调度台监控区里的**一个窗格**。

    panel       : 面板标识 "effect"/"programmer"/"linker"/"matrix"/"audio"/
                  "route"/"joystick"（用于分组显示与还原路由）
    track_type  : 同一组上的效果槽标识（允许同组并存多个效果 → 多格）
    group_key   : 设备集身份（内容寻址："grp:id1|id2|..." / "__all__"）；
                  **临时选中与已存组在这里等价**（都是按 id 算出来的 key）
    group_name  : 显示用设备名（"全部设备"/组名/""=临时选中）
    fixture_ids : 该效果作用的设备 id 列表（临时选中时也完整记录）
    effect_id   : 效果/预设 id（可为空，如 Programmer 静态通道）
    effect_name : 显示名
    category    : 规范分类（仅用于**着色**，不参与身份）
    layers      : 它实际写的混音层（取 constants.ALL_LAYERS 的子集，只读展示）
    summary     : 差异摘要（面板用**自己的既有规则**生成的可读短串，
                  例如 "空间 45° · 时序 1.5× · φ0.25"；默认参数可省略）
    live        : 是否正在输出
    extra       : 面板自定义只读附加信息（不参与身份与持久化语义）
    updated_at  : 最近上报时间
    """

    panel: str = ""
    track_type: str = ""
    group_key: str = ""
    group_name: str = ""
    fixture_ids: List[str] = field(default_factory=list)
    effect_id: str = ""
    effect_name: str = ""
    category: str = ""
    layers: List[str] = field(default_factory=list)
    summary: str = ""
    live: bool = True
    # ── 设备组身份（2026-09-13 加：调度台"子母文件夹"分类显示）──────────────
    # ★ 为什么需要：`group_key` 是**按设备 id 内容寻址**的（临时选中与已创建
    #   组若设备集合相同就得到同一个 key），总线上**无法区分两者**。用户要求
    #   "区分已创建的组与临时选中的组，且临时组隶属于某个已创建大组之下"，
    #   因此必须额外携带组身份：
    #     group_id          已创建组 id（"" = 非创建组：临时选中/全部设备/规则名）
    #     parent_group_id   临时选中**隶属**的已创建组 id（由设备集合包含关系解析）
    #     parent_group_name 其显示名（供文件夹标题与 tooltip）
    #   面板若有 `_current_group_id` 应直接回填；未回填时由调度台按设备集合反查。
    group_id: str = ""
    parent_group_id: str = ""
    parent_group_name: str = ""
    #: ★★★ R165：效果作用域里「母集合基准序」与「子集标记」。
    #:   为什么需要：`fixture_ids = group_ids or base_ids`（见 effect_browser._effect_node）
    #:   在 group_ids 非空时**把母集合丢掉**，于是"母集合 N 台里子集错相"这类效果
    #:   到监控区/调度台上只剩子集，父/子分组关系看不出来，存档也无法还原该语义。
    #:   纯展示 + 存档用途，不参与 `cell_key()` 身份。
    base_ids: List[str] = field(default_factory=list)
    subset_ids: List[str] = field(default_factory=list)
    #: 该格是否为"当前选中"（clip 选中高亮用；其它面板可留 False）。
    selected: bool = False
    #: 顶栏类别标签（监控区窗格 top-bar）。空 = 视图按面板自动推导：
    #: programmer→track_type（通道类型，多种通道即"混合"），其余→effect_name。
    tag: str = ""
    #: 强度/数值（0..1，监控区窗格底部强度条）。<0 = 未知 → 活跃画满格、停止画空。
    level: float = -1.0
    #: ★★ 2026-09-14 用户实报（显示错误，普遍）：
    #:   "一个效果明明对 pump 应用，但是却在监控页面看到**所有通道**都有波浪 effect 显示。"
    #:   真因：节点只带"设备作用域"（`fixture_ids`），**不带通道作用域** → 监控区富化按
    #:   "设备 ∈ fixture_ids"给该台设备的**每一个通道格**都打上效果名 → 效果名糊满整行。
    #:   通道作用域本来就在（effect 面板 state 的 `ch_types`），只是没带上总线。
    #:   空 = 未声明 → 富化侧按"应用到该设备全部通道"处理（旧行为，供不声明通道的面板）。
    ch_types: List[str] = field(default_factory=list)
    extra: Dict[str, object] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)

    # ── 身份 ──────────────────────────────────────────────────────────────
    def cell_key(self) -> CellKey:
        """窗格身份键 = (panel, track_type, group_key)。

        ★ 不要在这里加 effect_id/effect_name/category —— 那会把"不同设备上的
        同名效果"错误合并成一格，违反用户"微差即两种效果"的硬要求。
        """
        return (str(self.panel), str(self.track_type), str(self.group_key))

    def fixture_count(self) -> int:
        return len(self.fixture_ids)

    def display_tag(self) -> str:
        """监控区顶栏标签：显式 tag 优先；否则按面板推导。

        - programmer：track_type = 通道类型（dimmer/motor_z/...）；
          用户要求"多种通道显示成混合状态"由面板侧把 track_type 写成 "mixed"
          （或直接填 tag="混合通道"）表达，视图不再聚合（聚合会破坏身份契约）。
        - 其余面板：效果名（视图侧再做缩写/省略）。
        """
        if str(self.tag or "").strip():
            return str(self.tag).strip()
        if str(self.panel) == "programmer":
            tt = str(self.track_type or "").strip()
            if tt == "mixed":
                return "混合通道"
            return tt or "静态通道"
        return str(self.effect_name or "")

    # ── 序列化 ────────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "panel": self.panel,
            "track_type": self.track_type,
            "group_key": self.group_key,
            "group_name": self.group_name,
            "fixture_ids": list(self.fixture_ids),
            "effect_id": self.effect_id,
            "effect_name": self.effect_name,
            "category": self.category,
            "layers": list(self.layers),
            "summary": self.summary,
            "live": bool(self.live),
            "group_id": self.group_id,
            "parent_group_id": self.parent_group_id,
            "parent_group_name": self.parent_group_name,
            "base_ids": [str(x) for x in (self.base_ids or []) if str(x)],
            "subset_ids": [str(x) for x in (self.subset_ids or []) if str(x)],
            "selected": bool(self.selected),
            "tag": self.tag,
            "level": float(self.level),
            "ch_types": [str(x) for x in (self.ch_types or []) if str(x)],
            "extra": dict(self.extra or {}),
            "updated_at": float(self.updated_at),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EffectNode":
        d = d or {}
        try:
            updated = float(d.get("updated_at", time.time()))
        except (TypeError, ValueError):
            updated = time.time()
        return cls(
            panel=str(d.get("panel", "") or ""),
            track_type=str(d.get("track_type", "") or ""),
            group_key=str(d.get("group_key", "") or ""),
            group_name=str(d.get("group_name", "") or ""),
            fixture_ids=[str(x) for x in (d.get("fixture_ids", []) or []) if str(x)],
            effect_id=str(d.get("effect_id", "") or ""),
            effect_name=str(d.get("effect_name", "") or ""),
            category=str(d.get("category", "") or ""),
            layers=[str(x) for x in (d.get("layers", []) or []) if str(x)],
            summary=str(d.get("summary", "") or ""),
            live=bool(d.get("live", True)),
            group_id=str(d.get("group_id", "") or ""),
            parent_group_id=str(d.get("parent_group_id", "") or ""),
            parent_group_name=str(d.get("parent_group_name", "") or ""),
            base_ids=_str_list(d.get("base_ids")),
            subset_ids=_str_list(d.get("subset_ids")),
            selected=bool(d.get("selected", False)),
            tag=str(d.get("tag", "") or ""),
            level=_as_float(d.get("level"), -1.0),
            ch_types=_str_list(d.get("ch_types")),
            extra=_dict_or_empty(d.get("extra")),
            updated_at=updated,
        )


class EffectBus:
    """效果制作总线：内存聚合，按身份键 upsert，**绝不按效果合并**。

    ★ 线程契约（必须遵守，否则并发 bug）：
      当前所有调用点都在 **UI 线程**（面板状态变化、面板关闭、调度台刷新），
      已核查引擎生成线程 / 网络输出线程 / 音频线程**都不改效果面板状态**
      （见 ai-memory/dispatch-console-design-2026-09-13.md 的线程模型一节）。
      但**不能依赖"碰巧没人跨线程调"**：本类用 `RLock` 保护内部 dict，
      使 report/remove/读取在任意线程下都不会损坏结构或迭代中崩溃。
      读取方拿到的始终是**独立列表快照**，可安全在别处迭代。
    """

    _SINGLETON: Optional["EffectBus"] = None
    #: 单例创建锁（两个线程同时首次 instance() 时不许造出两个总线）
    _SINGLETON_LOCK = threading.Lock()

    def __init__(self) -> None:
        self._nodes: Dict[CellKey, EffectNode] = {}
        self._lock = threading.RLock()
        #: ★★ R194/P2-04：结构-内容修订号（见 `revision()`）——全部写入口在锁内维护。
        self._revision: int = 0

    @classmethod
    def instance(cls) -> "EffectBus":
        if cls._SINGLETON is None:
            with cls._SINGLETON_LOCK:
                if cls._SINGLETON is None:
                    cls._SINGLETON = cls()
        return cls._SINGLETON

    # ── 写入（面板调用，一行）───────────────────────────────────────────────
    def report(self, node: EffectNode) -> None:
        """上报/更新一个窗格（按身份键 upsert）。

        同一 (panel, track_type, group_key) 的重复上报 = 该格参数更新；
        不同 group_key / track_type = 不同格（这是"微差即两效果"的落点）。

        ★★ R194/P2-04（revision 契约）：**新键**无条件 bump `revision()`；
          同键重复上报只比较**廉价内容元组** `_node_content_sig`
          （panel / effect_name / fixture_ids / ch_types，与调度台富化指纹
          `_one_node_sig` 同口径同量级），内容变了才 bump。
          为什么不能无条件递增：各效果面板以 ~4Hz `report_to_bus()` 重建节点
          并**同键**重报（节点是新建对象、内容往往一字未变）——若同键 upsert
          也递增，revision 会以 12Hz+ 空转，消费方"快表短路"（见 `revision()`）
          收益归零。注意 summary/level/live/tag 等纯展示字段变化**不**触发
          bump（快表只消费"效果名×设备×通道作用域"）。
        """
        if node is None:
            return
        try:
            node.updated_at = time.time()
            key = node.cell_key()
            sig = _node_content_sig(node)
            with self._lock:
                old = self._nodes.get(key)
                self._nodes[key] = node
                if old is None or _node_content_sig(old) != sig:
                    self._revision += 1
        except Exception:
            pass

    def remove(self, panel: str, track_type: str, group_key: str) -> bool:
        """某格停止输出时移除（返回是否确实删了）。

        ★ R194/P2-04：**确实删除**才 bump `revision()`（remove 一个不存在的键
          是无操作，总线内容没变，不该让消费方白重建一次）。
        """
        try:
            with self._lock:
                removed = self._nodes.pop(
                    (str(panel), str(track_type), str(group_key)), None) is not None
                if removed:
                    self._revision += 1
                return removed
        except Exception:
            return False

    def clear_panel(self, panel: str) -> int:
        """清掉某面板的全部格（面板整体停止/切换时用）。返回删除数。

        ★ R194/P2-04：删到东西才 bump（返回 0 = 总线没变）。
        """
        try:
            with self._lock:
                keys = [k for k in self._nodes if k[0] == str(panel)]
                for k in keys:
                    self._nodes.pop(k, None)
                if keys:
                    self._revision += 1
                return len(keys)
        except Exception:
            return 0

    def clear(self) -> None:
        """清空总线（换工程/关调度台时用）。★ R194/P2-04：无条件 bump。"""
        with self._lock:
            self._nodes.clear()
            self._revision += 1

    # ── 读取（调度台调用）───────────────────────────────────────────────────
    def revision(self) -> int:
        """★★ R194/P2-04：总线**修订号**——写内容变化即 +1，单调不回退。

        给"轮询读总线"的视图当**快表短路**用（panel_table_host 等消费方，
        本文件不实现视图侧）::

            rev = bus.revision()
            if rev != self._last_bus_rev:      # 没变 → 跳过重建
                self._last_bus_rev = rev
                nodes = bus.nodes()            # O(n) 排序快照，只在真变时付
                ...重建快表...

        契约边界（消费方必读）：
          · 三面板 ~4Hz 同键重报**同内容**节点 → 不 bump（这是本机制的存在理由）；
          · 新键 report / 内容变化 report（panel·effect_name·fixture_ids·
            ch_types 任一变）/ 确删 remove / 非空 clear_panel / clear → 必 bump；
          · summary/level/live/tag/selected 等纯展示字段变化**不保证** bump
            （与 `_one_node_sig` 同口径）；关心这些字段的视图不要只靠 revision
            短路，须保留自己的低频全量刷新。
          · 读取走锁（与写入口配对）；代价一次 RLock，4-10Hz 可忽略。
        """
        with self._lock:
            return self._revision

    def nodes(self) -> List[EffectNode]:
        """全部窗格（按 panel → group_key → track_type 稳定排序）的快照。"""
        with self._lock:
            snapshot = list(self._nodes.values())
        try:
            return sorted(
                snapshot,
                key=lambda n: (str(n.panel), str(n.group_key), str(n.track_type)))
        except Exception:
            return snapshot

    def cells(self) -> List[EffectNode]:
        """`nodes()` 的别名（语义："一节点=一窗格"）——给前端用的清晰名字。"""
        return self.nodes()

    def cells_by_panel(self) -> Dict[str, List[EffectNode]]:
        """按面板分组（前端左侧按"面板 → 设备组"两层排布时用）。"""
        out: Dict[str, List[EffectNode]] = {}
        for n in self.nodes():
            out.setdefault(str(n.panel), []).append(n)
        return out

    def count(self) -> int:
        with self._lock:
            return len(self._nodes)

    def live_count(self) -> int:
        with self._lock:
            return sum(1 for n in self._nodes.values() if n.live)
