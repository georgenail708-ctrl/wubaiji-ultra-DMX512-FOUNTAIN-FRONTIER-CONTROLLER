# -*- coding: utf-8 -*-
"""调度台表格视图的**表格模型**（纯数据，无 Qt，可无 GUI 单测）。

职责边界（可维护性）：把"调度台总线上的效果节点 + 工程设备表 + 当前 DMX 值"
投影成一份**可直接绘制的表格结构**（分区 → 母/子组 → 列头 + 行 + 格）。
它**不做任何 IO、不碰 Qt、不调 NetworkManager**——取值与归属由调用方以
`ValueSource` 协议注入（UI 层用 nm 实现，测试用假实现），因此本模块可在
无 GUI 环境下完整单测。

★★ 身份契约（沿用 effect_bus，不可违反）
    "即使微小的差异都算做两种效果"：一个调度台节点 = 一个独立**效果槽列**。
    同组并存两效果 → 两列；同效果不同设备 → 行不同而非合并。任何按
    effect_id / effect_name / category 合并都是 bug。

两种列（同一张表里混排，这是本项目的专属特色——只有单一参数表）：
    · **通道列（channel）**：Programmer 类节点，值是设备通道（R/pump/motor_x…），
      按 `core.constants.CHANNEL_PARAM_GROUPS` 分组、同组设备共享。
    · **效果槽列（effect）**：效果库/矩阵/关联器等节点，值是该效果的强度
      （无物理通道时），一格 = 节点 × 设备（取节点 `fixture_ids`）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .cell_semantics import resolve_cell_display as _resolve_cell_display
from .fixture_table import (
    ColSpec,
    build_columns,
    channel_address_at,
    dimmer_source_addrs,
    format_value,
    is_virtual_dimmer,
    norm_channel_type,
)

#: 列类型
COL_CHANNEL = "channel"     # 设备通道列（Programmer 类）
COL_EFFECT = "effect"       # 效果槽列（效果/矩阵/关联器/音频…）
COL_CLIP = "clip"           # 时间线 clip 列（平铺，无分组）

#: 面板 → 是否按"通道列"渲染（否则按效果槽列）。与 PANEL_LABELS 的 id 集一致。
CHANNEL_PANELS = frozenset({"programmer"})
#: clip 面板单独平铺（用户明确"clip 除外"，不参与设备组文件夹）
CLIP_PANEL = "clip"
#: **汇总分区**（用户点名："因为它设计出来就是天然汇总的"）：把**所有**面板写到
#:  设备通道上的值汇到一起看。行 = 被任一节点影响的设备，列 = 值类型，格 = 实时
#:  混合输出（因此 effect / matrix / 音频 / 摇杆 对某通道写的值都会在这里出现）。
SUMMARY_PANEL = "__summary__"
SUMMARY_TITLE = "汇总 · 全部来源（设备通道）"

#: **选择表分区**（2026-09-14 用户定案）：监控区**只反映"当前选中设备"**——
#:  与工程全量设备无关、与哪些设备正在编辑无关、不含 clip。行 = 设备**类型**
#:  （同类型折叠成一行），列 = 值类型（通道）。空选择 → 无分区（调度台清空）。
SELECTION_PANEL = "__selection__"
SELECTION_TITLE = "监控 · 当前选中设备"

#: 混音层 → 面板 id（"这格的值是谁写的"；供来源角标与双击路由）。
LAYER_TO_PANEL: Dict[str, str] = {
    "fader": "programmer", "programmer": "programmer",
    "tester": "programmer", "linker": "linker",
    "effect": "effect", "effect_auto": "effect",
    "matrix": "matrix", "matrix_audio": "matrix",
    "audio": "audio", "joystick": "joystick",
    "clip_traj": "clip", "ml_lab": "effect",
    "clip_playback": "clip", "clip_preview": "clip", "timeline": "clip",
}


def panel_for_layer(layer: str) -> str:
    """混音层名 → 面板 id（未知层返回 ""）。"""
    return LAYER_TO_PANEL.get(str(layer or ""), "")

#: 行范围（工具栏开关）
SCOPE_EFFECT_ONLY = "effect_only"   # 只显示本区节点覆盖到的设备（默认，表短聚焦）
SCOPE_GROUP_ALL = "group_all"       # 整组全部设备（无值行显示"—"）

# ── 行排布模式（用户点名：同种设备必须折叠，禁止"每盏灯一行"）──────────────
#: **按组整合**（默认）：同一创建组或同一**设备类型**的相同"灯位"合成一行，
#:  行内取值相同则显示该值、不同则显示「混合」。大组（2000+ 台）用它才可读。
ROW_MODE_GROUPS = "groups"
#: 只显示本区节点**覆盖到**的设备类型（同样折叠成行，只是行数更少、聚焦本区效果）。
ROW_MODE_EFFECT_ONLY = "effect_only"

#: ★ 2026-09-13 用户定案：**取消"全部设备"逐台展开模式**。
#:   理由：①调度台本来就能靠俯视图选设备，不必在这里逐台陈列；
#:   ②逐台展开在近 2000 台工程下让整页 UI 极卡（每台一行 = 数千行）。
#:   现在行永远按「组/设备类型」折叠，两种模式只差"列哪些类型"。
ROW_MODES: Tuple[str, ...] = (ROW_MODE_GROUPS, ROW_MODE_EFFECT_ONLY)
ROW_MODE_LABELS: Dict[str, str] = {
    ROW_MODE_GROUPS: "按组整合",
    ROW_MODE_EFFECT_ONLY: "仅有效果",
}

# ── 列分组 / 展示分组（用户 2026-09-14 定案）────────────────────────────────
#: **合并**（可选，**非默认**）：整张表只有一份——列 = 全部通道类型 + 全部效果槽，行 = 设备类型。
#:  **不按来源（面板）分区**，也不显示来源角标（左上小圆）。
#:  用户原话："不应该区分不同的来源…仅按照设备不同类型区分，其它的都不分，都默认合并了"。
COL_GROUP_MERGE = "merge"
#: **按来源分开**（★ 默认）：每个面板来源一个分区（无此物，是本项目特色）。
#:  ★ 2026-09-14 用户定案：**默认按来源分区** —— 否则"时间线 Clip"等分区标题会消失、
#:    该列被挤到屏外看不见。合并视图保留为**可选**（工具栏「分组」按钮切）。
COL_GROUP_SOURCE = "source"

COL_GROUPINGS: Tuple[str, ...] = (COL_GROUP_MERGE, COL_GROUP_SOURCE)
COL_GROUP_LABELS: Dict[str, str] = {
    COL_GROUP_MERGE: "合并",
    COL_GROUP_SOURCE: "按来源",
}
#: 「混合」标记：组内同一位的值不一致时，该格显示的文本
MIXED_LABEL = "混合"


@dataclass
class TableCell:
    """一个格子。

    `ch_type` 值类型（通道列有意义；效果列空）
    `occ`     该类型在该设备上的第几次出现（子设备行用）
    `tag`     通道地址标签（"U1:012"）或效果槽说明——悬停提示用
    `virtual` True = 虚拟通道（无物理地址，如纯 RGB 灯的 dimmer 亮度）
    """
    fixture_id: str
    col_key: str
    col_ref: str = ""
    ch_type: str = ""
    occ: int = 0
    tag: str = ""
    virtual: bool = False
    effect_id: str = ""
    #: 虚拟通道的取样地址（如虚拟 dimmer 取该设备 R/G/B/W 的地址）——
    #  值由消费方按"这些地址输出的最大值"算出（Programmer 的 dimmer 增益语义）。
    #: 单设备格: (universe, addr) 二元组；聚合格: (device_index, universe, addr) 三元组。
    src_addrs: Tuple[Tuple[int, ...], ...] = ()
    #: True = 聚合格（代表多台设备的同一灯位；组内取值不一致时显示「混合」）
    agg: bool = False
    #: 聚合格覆盖的设备 id（消费方按它批量设值/清空）
    fids: List[str] = field(default_factory=list)
    #: 效果槽的**强度**（0..1，节点上报的 level）与是否在跑。
    #  ★ 效果槽没有物理 DMX 地址，它的"格值"只能来自节点 level（×255）；
    #    通道列才从 NetworkManager 读实际通道值。消费方据此决定取值来源。
    level: float = -1.0
    live: bool = True

    # ── ★ 2026-09-14 用户要求"细化混合"所需的语义字段 ──────────────────────
    #  用户原话："如果是 programmer 页面，那么混合应该变成显示条带的起点和终点的
    #  设置相对比例和翼块值。如果是 effect，那应该显示 effect 名称。matrix 也是，
    #  显示该通道正在编辑的对应的效果名称，而不是都用一个混合去敷衍我，'混合'是真的
    #  存在多种不同效果或者同一效果多种不同设置才出现的。"
    #  · 效果类：`effect_name` = 该格**唯一**效果名；`effect_names` = 该格涉及的
    #    **不同**效果名集合（len>1 才是真·多效果）。
    #  · programmer 条带：起止值与翼块（无条带时 strip_mode 为空）。
    effect_name: str = ""
    effect_names: Tuple[str, ...] = ()
    strip_mode: str = ""
    strip_base: int = -1
    strip_target: int = -1
    strip_wings: int = 0
    strip_block: int = 0
    #: 该格所属面板（"programmer"/"effect"/"matrix"/""），供语义显示分派。
    panel: str = ""

    def key(self) -> Tuple[str, str]:
        return (str(self.fixture_id), str(self.col_key))


@dataclass
class TableRow:
    """一行 = 一台设备 / 一台设备的**一个子设备** / 一个**设备组（整合行）**。

    `row_id`    行身份（设备 id、`设备id::子序号`、或整合行的 `grp<组键>`）
    `label`     行头显示
    `sub`       子设备序号（0-based）；`sub_total` 该设备共几个子设备
    `cells`     本行格子（按列序）
    `fids`      本行代表的**设备 id 列表**：单设备行 = [该设备]；
                整合行 = 该组（该灯位）的全部设备 —— 用于"组内取值相同/混合"判定，
                以及右键动作（设值/清空）批量作用到整组。
    `agg`       True = 整合行（按组）；False = 具体设备行
    `agg_count` 整合行覆盖的设备台数（行头显示 "N 台"）
    """
    row_id: str
    fixture_id: str
    label: str
    universe: int = 1
    start_channel: int = 1
    sub: int = 0
    sub_total: int = 1
    cells: List[TableCell] = field(default_factory=list)
    fids: List[str] = field(default_factory=list)
    agg: bool = False
    agg_count: int = 1
    #: `cell_of` 的 O(1) 索引（惰性构建，见 cell_of 的性能说明）
    _cell_index: Optional[Dict[str, "TableCell"]] = field(default=None, repr=False)
    #: 该行是否为"当前选中"（由覆盖它的节点透传；clip 选中高亮用，见 table_view）。
    #  与表格自身的"框选/点选"（视图 `_selected`）是**两回事**，故分开存。
    selected: bool = False

    def is_sub_row(self) -> bool:
        return self.sub_total > 1

    def is_agg_row(self) -> bool:
        return bool(self.agg)

    def fixture_ids(self) -> List[str]:
        """本行覆盖的设备 id（单设备行即自身；整合行是整组）。"""
        if self.fids:
            return list(self.fids)
        return [self.fixture_id] if self.fixture_id else []

    def cell_of(self, col_key: str) -> Optional[TableCell]:
        """按列取格：接受**格键**（含子序号，如 `r@2`）或**列键**（`r`）。

        两种都必须支持：渲染层用格键（同一列的不同子行是不同格），而"取该行在某列
        的格"这种查询（右键/清空/断言）只给列键。

        ★ 性能：本方法在**每次绘制**里被每个格调用。原实现线性扫描 `self.cells`
          （一屏 25 列 × 24 行 = 600 次 × 25 比较），是隐性热点。现建 O(1) 索引，
          首次调用时惰性构建（构建 O(格数)，之后全命中）。
        """
        want = str(col_key)
        idx = getattr(self, "_cell_index", None)
        if idx is None:
            idx = {}
            for c in self.cells:
                idx.setdefault(str(c.col_key), c)
                if c.col_ref:
                    idx.setdefault(str(c.col_ref), c)   # 列键 → 该行该列首个格
            self._cell_index = idx
        return idx.get(want)


@dataclass
class TableSection:
    """一个模块来源分区（用户专属特色：按不同模块子窗口来源分类）。

    `key`       分区身份 = panel id
    `folder`    组分类（Clip 分档 / 编程 / 巡演）
    `columns`   本区列（通道列或效果槽列）
    `rows`      本区行（设备）
    `depth`     0 = 普通分区；>0 = 作为子窗口嵌在父分区内（如矩阵的像素效果子窗）
    """
    key: str
    title: str
    folder: str = ""
    columns: List[ColSpec] = field(default_factory=list)
    rows: List[TableRow] = field(default_factory=list)
    depth: int = 0

    def default_col_key(self) -> str:
        return self.columns[0].key if self.columns else ""


@dataclass
class TableModel:
    """整张表（分区序列 + 取列/取格辅助）。"""
    sections: List[TableSection] = field(default_factory=list)
    unit: str = "dmx"
    #: 本模型用的行排布模式（ROW_MODE_*）。**行永远折叠**（按组/设备类型），
    #:  此字段只表示"列哪些类型"（整合全部 / 仅本区覆盖）。
    row_mode: str = ROW_MODE_GROUPS
    #: 列/展示分组（COL_GROUP_*）。**默认合并**（一份表、不按来源分区、不画来源角标）；
    #: `source` 时按面板来源分区并显示角标（用户可切）。
    col_grouping: str = COL_GROUP_SOURCE

    def section(self, key: str) -> Optional[TableSection]:
        for s in self.sections:
            if s.key == key:
                return s
        return None

    def cell(self, section_key: str, fixture_id: str, col_key: str) -> Optional[TableCell]:
        """按列键取格；`col_key` 可用 `列键@子序号`（如 `r@2`）定位复合设备的子行。"""
        sec = self.section(section_key)
        if sec is None:
            return None
        want_sub, want_col = _split_col_key(col_key)
        for r in sec.rows:
            if r.fixture_id != fixture_id:
                continue
            if want_sub is not None and r.sub != want_sub:
                continue
            return r.cell_of(want_col)
        return None


def _split_col_key(col_key: str) -> Tuple[Optional[int], str]:
    """把 `列键@子序号` 拆成 (子序号|None, 列键)。无 `@` 时子序号为 None。"""
    s = str(col_key or "")
    if "@" in s:
        head, _, tail = s.rpartition("@")
        try:
            return int(tail), head
        except (TypeError, ValueError):
            return None, s
    return None, s


def row_key(fixture_id: str, sub: int = 0, sub_total: int = 1) -> str:
    """行身份：单子设备 = 设备 id；复合设备 = `设备id::子序号`（稳定、可跨刷新对齐）。"""
    if int(sub_total) <= 1:
        return str(fixture_id)
    return f"{fixture_id}::{int(sub)}"


def col_key_for(col_key: str, sub: int, sub_total: int) -> str:
    """格身份键：复合设备的格带 `@子序号`（同一列在不同子行是不同的格）。"""
    if int(sub_total) <= 1:
        return str(col_key)
    return f"{col_key}@{int(sub)}"


class ValueReader:
    """取值的极简协议实现（UI 层用 NetworkManager 包一层；测试用假实现）。

    方法只依赖两个原始查询，避免把 nm 的类型泄漏进 processing 层：
        raw(universe, addr)      -> int | None   该地址当前混合输出值
        owned(universe, addr)    -> bool         该地址是否被任一混音层控制
        layer_raw(layer, uni, a) -> int | None   某个混成层在该地址的原始值（可缺省）
    """

    def __init__(self, raw=None, owned=None, layer_raw=None) -> None:
        self._raw = raw
        self._owned = owned
        self._layer_raw = layer_raw

    def raw(self, universe: int, addr: int) -> Optional[int]:
        if self._raw is None:
            return None
        return self._raw(int(universe), int(addr))

    def owned(self, universe: int, addr: int) -> bool:
        if self._owned is None:
            return True
        return bool(self._owned(int(universe), int(addr)))

    def layer_raw(self, layer: str, universe: int, addr: int) -> Optional[int]:
        if self._layer_raw is None:
            return None
        return self._layer_raw(str(layer), int(universe), int(addr))

def _effect_slot_display(cell: TableCell, unit: str) -> Tuple[str, str]:
    """效果槽（无物理地址）的显示：格值来自节点强度 level。

    调用方没给 value（通道读取路径对它不适用）时按 level 兜底，否则整列效果会全空
    （实测踩到：圆环列无值）。
    """
    if not cell.live and float(getattr(cell, "level", -1.0)) < 0:
        return ("", "empty")
    lv = float(getattr(cell, "level", -1.0))
    dmx = 255 if lv < 0 else int(round(max(0.0, min(1.0, lv)) * 255))
    return (format_value(dmx, unit), "value" if dmx > 0 else "zero")


def _channel_value_display(cell: TableCell, value: Optional[int],
                           owned: bool, unit: str) -> Tuple[str, str]:
    """通道格显示：**只按实际输出值**（用户定案）。

    ★★ 2026-09-14 用户定案："区分 0 的来源与否**不影响任何输出/显示逻辑**"，
      按最早的设计来 —— 显示只反映**实际输出值**：
        · 有值（含 0）→ 照常显示该值（0 显示 "0"）；
        · value is None（无数据/无此通道）→ 空。
      `owned`（是否被混音层归属）**不再参与显示判定**：它是"这值谁写的"的辅助信息，
      不该让同一输出值在不同来源下显示不同（那正是旧实现把默认值 128 显示成"空"、
      又把 0 显示成灰底"已清空"的根因）。参数保留仅为 API 兼容。
    """
    if value is None:
        return ("", "empty")
    return (format_value(value, unit), "value" if int(value) > 0 else "zero")


def cell_display(cell: Optional[TableCell], value: Optional[int],
                 owned: bool, unit: str = "dmx", spread: Optional[int] = None, *,
                 legacy_spread_mixed: bool = False) -> Tuple[str, str]:
    """一格 → (显示串, 状态码)。

    状态码供渲染层选色（**延续监视器"值0 vs 真清空"纪律**）：
        "value"  值 > 0（**含无归属的默认值**，如电机中位 128）→ 正常玻璃 + 数字
        "zero"   有归属但值 == 0       → 微亮玻璃 + 显示 "0"（有效果且暗）
        "empty"  **值为 0 且无归属**（已清空）→ 灰底、**不显示数字**
        "mixed"  **真·多效果**（同格存在多种不同效果）→ 显示"{n} 效果"
        "na"     无此值类型/无地址      → 斜纹

    ★★ 判"空"必须同时满足"无归属 **且** 值为 0"（2026-09-13 用户实报修正）：
      有默认值的设备由 NetworkManager `_channel_defaults` 供值，**不属任何混音层**
      （`owned=False`），但那是**真实输出**。只看 `owned` 会把 128 默认值显示成"空"，
      表现为"本来有默认值的设备全不输出值了"。

    ★★ 2026-09-14 用户定案（**细化"混合"**）：
      "如果是 programmer 页面，那么混合应该变成显示条带的起点和终点的设置相对比例和
       翼块值。如果是 effect，那应该显示 effect 名称。matrix 也是，显示该通道正在编辑
       的对应的效果名称，而不是都用一个混合去敷衍我，'混合'是真的存在多种不同效果或者
       同一效果多种不同设置才出现的。"

      → 语义分派收敛到 `cell_semantics.resolve_cell_display`（纯函数、可单测）：
        · programmer 有非平值条带 → 显示 "起点-终点 W翼 B块"（如 `128-255 W4 B2 ↑`）；
        · effect/matrix 有唯一效果名 → 显示该效果名；
        · **多个不同效果名** → `"{n} 效果"` + "mixed"（这才是真·混合）；
        · 同名多设置 → 仍显示名字（不报混合）；
        · 仅"取值不一致"（spread>1）→ **不再**报混合（多台设备数值不同是正常输出，
          例如扇形/渐变展开）。需要旧行为时传 `legacy_spread_mixed=True`。

    `spread`：聚合格内不同取值的个数。单设备格传 None。
    `legacy_spread_mixed`：兼容开关，True 时恢复旧"spread>1 → 混合"行为。
    """
    try:
        return _resolve_cell_display(cell, value, owned, unit,
                                     spread=spread,
                                     legacy_spread_mixed=legacy_spread_mixed)
    except Exception:
        return ("", "na")


__all__ = [
    "COL_CHANNEL", "COL_EFFECT", "COL_CLIP",
    "CHANNEL_PANELS", "CLIP_PANEL",
    "SCOPE_EFFECT_ONLY", "SCOPE_GROUP_ALL",
    "ROW_MODE_GROUPS", "ROW_MODE_EFFECT_ONLY",
    "COL_GROUP_MERGE", "COL_GROUP_SOURCE", "COL_GROUPINGS", "COL_GROUP_LABELS",
    "ROW_MODES", "ROW_MODE_LABELS", "MIXED_LABEL",
    "SUMMARY_PANEL", "SUMMARY_TITLE", "LAYER_TO_PANEL", "panel_for_layer",
    "TableCell", "TableRow", "TableSection", "TableModel", "ValueReader",
    "build_table", "cell_display", "row_key", "col_key_for", "build_group_index",
]
