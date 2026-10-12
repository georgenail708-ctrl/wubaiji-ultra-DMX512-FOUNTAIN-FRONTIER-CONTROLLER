# -*- coding: utf-8 -*-
"""调度台表格视图的**纯函数**支撑层（无 Qt、无副作用、可单测）。

存在理由（用户需求 2026-09-13）：
  调度台监控区要**照 的表格视图重做**——行 = 设备，列 = 值类型
  （通道类型/效果槽），格 = 该设备该值类型的当前数值，格内 `S` = 已标记保存。
  要把"设备"渲染成行、把"值类型"渲染成列，就必须能回答三个纯数据问题：

    1. 一台设备的某个值类型（如 R / pump / dimmer）落在哪些 (universe, 通道)？
       → `build_fixture_info()` + `channel_addrs_for()` / `channel_address_at()`
    2. 该设备集上出现过哪些值类型、顺序与中文名？
       → `build_columns()`（分组顺序取自 `core.constants.CHANNEL_PARAM_GROUPS`）
    3. 一个 0-255 的 DMX 值按当前单位（%/DMX/HEX）该显示成什么串？
       → `format_value()`

  ★ 这里刻意与 `programmer_panel._rebuild_addr_cache` **不复用代码**：那份缓存是
    面板私有的、带指纹失效与造型曲线耦合的重型结构；调度台只需要"设备→通道地址"
    这一最小投影。共用会把调度台绑到 Programmer 的内部实现上（改一处崩两处）。
    两者都遵循同一份 `FixtureDefinition.channels` 顺序语义，结果天然一致。

身份契约（沿用 effect_bus）：本模块只做**数据投影**，不参与"一格 = 一个效果"的身份
判定（那是 `EffectNode.cell_key()` 的事）。任何合并/去重都是 bug。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...core.constants import (
    CH_COLOR_ALIASES,
    CHANNEL_LABELS,
    CHANNEL_PARAM_GROUPS,
)

#: 单位标识（与 dispatch_panel 的工具栏分段控件一致）
UNIT_DMX = "dmx"
UNIT_PCT = "pct"
UNIT_HEX = "hex"
UNITS: Tuple[str, ...] = (UNIT_DMX, UNIT_PCT, UNIT_HEX)
UNIT_LABELS: Dict[str, str] = {UNIT_DMX: "DMX", UNIT_PCT: "%", UNIT_HEX: "HEX"}

#: 颜色通道（虚拟亮度 dimmer 的取样来源，与 programmer 的 `_fixture_has_light_channels` 同集）
LIGHT_CHANNELS: Tuple[str, ...] = ("R", "G", "B", "W")

#: 参数分组名（未归组的值类型落这里）
GROUP_OTHER = "其它"


def norm_channel_type(ch_type: Any) -> str:
    """通道类型归一化：枚举/字符串 → 小写串（与 programmer 的 `_norm_channel_type` 同语义）。

    `CH_COLOR_ALIASES` 把 red/green/blue/white/intensity 归到 r/g/b/w/dimmer，
    与 `core.constants` 的通道配色别名共用一份真源（不新造别名表）。
    """
    raw = getattr(ch_type, "value", ch_type)
    if not raw:
        return ""
    key = str(raw).strip().lower()
    return CH_COLOR_ALIASES.get(key, key)


def _g(obj: Any, key: str, default: Any = None) -> Any:
    """字典/对象双取（工程模型是 dataclass，测试/导入路径常是 dict）。"""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def build_fixture_info(fixtures: Sequence[Any], defs: Dict[str, Any]) -> Dict[str, dict]:
    """把工程设备表投影成调度台表格需要的**最小设备信息表**。

    参数:
        fixtures: FixtureInstance 列表（或含 id/universe/start_channel/def_id 的字典）
        defs:     {def_id: FixtureDefinition}，提供 `channels` 顺序与功能通道名

    返回:
        {fixture_id: {
            "id", "name", "universe", "start_channel", "def_id",
            "channels": [归一化通道类型...],           # 与设备定义同序 = DMX 物理顺序
            "channel_index": {归一化类型: 首个偏移},    # O(1) 求首地址
            "channel_addrs": {归一化类型: [(uni,addr), ...]},  # 全部出现（重复类型多条）
            "def_names": {归一化类型: 功能通道自定义名},  # function_channel_names 归一化
        }}

    ★ 为什么同时给 `channel_index` 与 `channel_addrs`：一台设备可能有**重复的通道
      类型**（气爆/跑泉类设备一台含多组 R/G/B/W，见 `device_type_registry`）。只保留
      首地址会静默丢掉后面的物理通道；表格要为重复类型生成多列（R#1/R#2），因此必须
      保存**地址列表**。`channel_index` 仅为兼容既有调用点保留。
    """
    out: Dict[str, dict] = {}
    for fi in fixtures or []:
        fid = str(_g(fi, "id", "") or "")
        if not fid:
            continue
        fd = (defs or {}).get(str(_g(fi, "def_id", "") or ""))
        ch_list = list(_g(fd, "channels", []) or [])
        universe = int(_g(fi, "universe", 1) or 1)
        start = int(_g(fi, "start_channel", 1) or 1)
        channels: List[str] = [norm_channel_type(c) for c in ch_list]
        index: Dict[str, int] = {}
        addrs: Dict[str, List[Tuple[int, int]]] = {}
        for offset, cname in enumerate(channels):
            if not cname:
                continue
            addr = start - 1 + offset
            if not (0 <= addr < 512):
                continue
            index.setdefault(cname, offset)
            addrs.setdefault(cname, []).append((universe, addr))
        # 设备定义里的功能通道自定义名（如 function_1 → "气爆阀"），按归一化键索引
        raw_names = dict(_g(fd, "function_channel_names", {}) or {})
        def_names = {norm_channel_type(k): str(v) for k, v in raw_names.items() if v}
        out[fid] = {
            "id": fid,
            "name": str(_g(fi, "name", "") or "") or fid[:8],
            "universe": universe,
            "start_channel": start,
            "def_id": str(_g(fi, "def_id", "") or ""),
            "channels": channels,
            "channel_index": index,
            "channel_addrs": addrs,
            "def_names": def_names,
        }
    return out


def channel_addrs_for(info: Optional[dict], ch_type: Any) -> List[Tuple[int, int]]:
    """设备 + 值类型 → **全部** (universe, 0-based 通道)（重复类型返回多条）。

    地址语义：`start_channel` 是 1-based（DMX 惯例），快照数组是 0-based，
    因此 `addr = start_channel - 1 + offset`（与 programmer/网络层一致）。
    """
    if not info:
        return []
    return list((info.get("channel_addrs") or {}).get(norm_channel_type(ch_type)) or [])


def channel_addr_for(info: Optional[dict], ch_type: Any) -> Optional[Tuple[int, int]]:
    """设备 + 值类型 → 首个 (universe, 0-based 通道)；无对应通道返回 None。

    等价于 `channel_address_at(info, ch_type)`（occ 默认 0）——保留本名以兼容既有调用点。
    """
    return channel_address_at(info, ch_type)


def channel_address_at(info: Optional[dict], ch_type: Any,
                       occ: int = 0) -> Optional[Tuple[int, int]]:
    """第 occ 个（0-based）该值类型的地址——表格"重复类型多列/多子行"用。

    ★ `occ` 默认 0：绝大多数调用只要"第一个地址"，强制传参会让调用方写出
      `channel_address_at(info, ct, 0)` 或误用 2 参而触发 TypeError——实测在
      `refs_from_cells` 的取址回调里被 `except` 吞掉，**静默变成无地址**（格子
      全空但不报错），是极难查的坑。故给默认值，让"首个"成为自然写法。

    该设备没有第 occ 条时返回 None（该格渲染成 N/A 斜纹）。
    """
    addrs = channel_addrs_for(info, ch_type)
    idx = int(occ or 0)
    if 0 <= idx < len(addrs):
        return addrs[idx]
    return None


def is_virtual_dimmer(info: Optional[dict], ch_type: Any) -> bool:
    """该设备的这个值类型是否为**虚拟通道**（无物理 DMX 地址）。

    仅 `dimmer` 会虚拟：里 TFeature_Intensity 映射到 dimmer，但纯 RGB/W
    灯具没有独立调光通道——Programmer 用它做"亮度增益"（见 `_expand_channel_output`：
    dimmer 无地址时按 gain 缩放 R/G/B/W）。表格据此把 dimmer 列显示为
    **该设备灯色的最大输出**（操作员扫表时最直观的"这台多亮"读数）。
    """
    if norm_channel_type(ch_type) != "dimmer":
        return False
    if channel_addrs_for(info, "dimmer"):
        return False
    chans = set(info.get("channels") or []) if info else set()
    # ★ 两侧都归一化后比较：`channels` 已归一化为小写（'r'），而 LIGHT_CHANNELS 是
    #   显示用大写（"R"）——直接 `in` 比较会永远为假（曾实际踩到：纯 RGB 灯被判成
    #   非虚拟，dimmer 列凭空消失）。
    return any(norm_channel_type(c) in {norm_channel_type(x) for x in LIGHT_CHANNELS}
               for c in chans)


def dimmer_source_addrs(info: Optional[dict]) -> List[Tuple[int, int]]:
    """虚拟 dimmer 的取样地址集（该设备的 R/G/B/W 全部地址）。"""
    out: List[Tuple[int, int]] = []
    if not info:
        return out
    for ct in LIGHT_CHANNELS:
        out.extend(channel_addrs_for(info, ct))
    return out


def channel_label(ch_type: Any, def_names: Optional[dict] = None) -> str:
    """值类型 → 中文显示名。

    优先设备定义的功能通道自定义名（`function_channel_names`），其次
    `core.constants.CHANNEL_LABELS`（唯一真源，与 Programmer 页同名），
    最后回退归一化后的原串——保证任何自定义通道都有可读名字。
    """
    key = str(ch_type or "")
    norm = norm_channel_type(ch_type)
    if def_names:
        hit = def_names.get(norm)
        if hit:
            return str(hit)
    # CHANNEL_LABELS 的键有大小写混合（R/G/B/W 大写），先精确后归一化查找
    if key in CHANNEL_LABELS:
        return CHANNEL_LABELS[key]
    for k, v in CHANNEL_LABELS.items():
        if norm_channel_type(k) == norm:
            return v
    return key


#: 归一化值类型 → 参数分组名（由 CHANNEL_PARAM_GROUPS 派生，避免第二份顺序）
def _group_lookup() -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for group_name, members in CHANNEL_PARAM_GROUPS:
        for m in members:
            lookup[norm_channel_type(m)] = group_name
    return lookup


_GROUP_OF: Dict[str, str] = _group_lookup()


def group_of_channel(ch_type: Any) -> str:
    """值类型 → 参数分组名（Intensity / Color / Motor XYZ / Valve / Pump / 其它）。

    顺序真源 = `core.constants.CHANNEL_PARAM_GROUPS`；未登记的类型归 `其它`。
    """
    return _GROUP_OF.get(norm_channel_type(ch_type), GROUP_OTHER)


def group_order_index(group_name: str) -> int:
    """分组显示次序（未登记分组排最后）——列头分组条排序用。"""
    for i, (name, _members) in enumerate(CHANNEL_PARAM_GROUPS):
        if name == group_name:
            return i
    return len(CHANNEL_PARAM_GROUPS)


@dataclass(frozen=True)
class ColSpec:
    """表格一列 = 一个值类型（**唯一**，不因重复而分裂）。

    ★ 为什么一个类型只占一列（而不是 R#1/R#2…）：表格的行 = 设备 /
      **子设备(sub-fixture)**，列 = 参数类型。一台含多组灯的复合设备（气爆/跑泉
      一台 4 组 R/G/B）在 里展开成**多行**，而不是多列——否则同一台设备
      的 R 会散成 R/R#2/R#3/R#4 四列，列宽爆炸且多数格是空的（实测 3 台设备就
      产生 12 列）。故：列 = 类型去重；重复出现次数记录在 `count`，由表格模型
      据此把该设备展开成 `count` 行子设备。

    `key`    列身份 = 归一化值类型（如 `r` / `pump` / `dimmer`）
    `ch_type` 归一化值类型
    `count`  该类型在该设备集内的**最大出现次数**（≥1；>1 表示有复合设备需拆行）
    `label`  显示名（中文）
    `group`  参数分组名（列头分组条）
    `sort`   排序键（分组序, 首次出现序）
    """
    key: str
    ch_type: str
    count: int
    label: str
    group: str
    sort: Tuple[int, int]
    occ: int = 0


def build_columns(fixture_infos: Sequence[dict], extra_types: Sequence[str] = (),
                  extra_labels: Optional[dict] = None) -> List[ColSpec]:
    """设备集 + 额外值类型 → 有序列定义（**每类型一列**）。

    列的来源 = 各设备 `channels` 的**有序并集**（保持首次出现顺序）+ `extra_types`
    （面板上报的 track_type——Programmer 静态通道可能不落在设备定义里，如虚拟 dimmer）。
    `count` = 该类型在任一设备上出现的最大次数（>1 → 表格把该设备拆成多行子设备）。

    排序：先按参数分组次序（`CHANNEL_PARAM_GROUPS`），组内按首次出现序。
    """
    max_occ: Dict[str, int] = {}
    order: Dict[str, int] = {}
    labels: Dict[str, str] = {}

    def _note(ch_type: str, names: Optional[dict], n: int = 1) -> None:
        ct = norm_channel_type(ch_type)
        if not ct:
            return
        if ct not in order:
            order[ct] = len(order)
        if not labels.get(ct) and names:
            hit = names.get(ct)
            if hit:
                labels[ct] = str(hit)
        if n > max_occ.get(ct, 0):
            max_occ[ct] = max(int(n), 1)

    for info in fixture_infos or []:
        names = info.get("def_names") or {}
        per_fixture: Dict[str, int] = {}
        for ct in (info.get("channels") or []):
            per_fixture[ct] = per_fixture.get(ct, 0) + 1
        for ct, n in per_fixture.items():
            _note(ct, names, n)
        # 虚拟 dimmer 也算一列（纯 RGB/W 设备也要有亮度列，与 Programmer 一致）
        if "dimmer" not in per_fixture and any(c in per_fixture for c in
                                               ("r", "g", "b", "w")):
            _note("dimmer", names, 1)

    for ct in (extra_types or ()):
        _note(ct, extra_labels, 1)

    cols: List[ColSpec] = []
    for ct, first_idx in order.items():
        grp = group_of_channel(ct)
        cols.append(ColSpec(
            key=ct,
            ch_type=ct,
            count=max(int(max_occ.get(ct, 1)), 1),
            label=labels.get(ct) or channel_label(ct),
            group=grp,
            sort=(group_order_index(grp), first_idx),
        ))
    cols.sort(key=lambda c: c.sort)
    return cols


def format_value(value: Optional[int], unit: str = UNIT_DMX) -> str:
    """DMX 值（0-255）按单位格式化为显示串；None → "—"。

    - DMX : 原值 0-255
    - %   : 0-100 四舍五入
    - HEX : 两位大写十六进制（与 一致）
    """
    if value is None:
        return "—"
    try:
        v = int(value)
    except (TypeError, ValueError):
        return "—"
    v = max(0, min(255, v))
    if unit == UNIT_PCT:
        return str(int(round(v * 100.0 / 255.0)))
    if unit == UNIT_HEX:
        return f"{v:02X}"
    return str(v)


def level_to_dmx(level: float) -> int:
    """效果强度 0..1 → DMX 0-255（效果槽没有直接通道时的回退显示值）。"""
    try:
        lv = float(level)
    except (TypeError, ValueError):
        return 0
    return max(0, min(255, int(round(lv * 255.0))))


__all__ = [
    "UNIT_DMX", "UNIT_PCT", "UNIT_HEX", "UNITS", "UNIT_LABELS",
    "LIGHT_CHANNELS", "GROUP_OTHER", "ColSpec",
    "norm_channel_type", "build_fixture_info",
    "channel_addrs_for", "channel_addr_for", "channel_address_at",
    "is_virtual_dimmer", "dimmer_source_addrs",
    "channel_label", "group_of_channel", "group_order_index", "build_columns",
    "format_value", "level_to_dmx",
]
