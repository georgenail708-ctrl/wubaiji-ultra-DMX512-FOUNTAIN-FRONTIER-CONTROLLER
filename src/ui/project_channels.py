# -*- coding: utf-8 -*-
"""工程通道清单（所有面板共用的**唯一真源**）：通道键、真名标签、下拉项。

★ 为什么要有这个模块（2026-09-19 用户实报）::

    "effect和matrix的效果，尤其是通用类型的效果应该自动识别该项目所有的通道，
     包括功能通道（传真实名字，不要还在function1）能应用到所有类型的通道上。"

  · **matrix 侧本来就对**：通道下拉按工程实际 fixture_defs 填充、引擎写地址
    映射也是逐通道建的（`_rebuild_channel_addr_cache` 遍历 def 的全部通道），
    功能通道既能选也能写。
  · **effect 侧是坏的**：`ch_type` 的候选恒为内置 `CH_TYPES` 常量表 → 工程里的
    功能通道（function_1…）既不出现在下拉里，也进不了写地址映射
    （`_effect_addr_maps_for_fixtures` 的 `candidate_types` 只遍历 CH_TYPES）
    → **即便选了也静默不输出**（实测：`_resolve_ch_offsets` 能解析 function_1
    偏移=3，但地址映射的键里根本没有它）。

  本模块把「工程有哪些通道、各自显示什么名字」收敛到一处，供 effect_browser /
  matrix_slideshow_panel / track_linker_panel 共用，避免出现第三份实现。

★★ 关键事实与定案（2026-09-19 用户第二次修正）::

    `function_N` 是**按设备类型各自编号的槽位**——同一个 `function_1` 在 Jet 是
    Motor、在 Vario 是 Zoom、在调压气爆是 泵模式。用户原话::

        "既没有完整的提取所有现有的通道类型，又没有正确显示名称，
         还把几种通道全部混到一起了，都没分开。"

    即：**把多个类型的同名槽位合并成一条是错的**（选一条等于同时动了所有类型，
    语义上是把 Zoom 的值写给 Turn）。正确做法 = **每（设备类型 × 通道）一条独立
    条目**，显示 `类型 · 真名`，并且选中它时**只作用于该类型的设备**
    （作用域编码进值：`键@类型id`，见 `make_scoped` / `split_scope`）。

    只有**跨类型语义相同**的通用通道（dimmer/R/G/B/W/motor_x/y/z/pump/valve）
    才保留单条全局条目。

★ 契约方向（2026-09-18 曾因此翻车）: `collect_channel_items` 产出
  **[(标签, 键)]**；要按键查标签必须走 `label_map()`，不得 `dict(items)`
  （那会把方向反过来，查表恒 miss → 所有标签退化成裸键名）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ..core.constants import CH_COLOR_ALIASES, CHANNEL_LABELS

log = logging.getLogger(__name__)

#: 通道键 → 显示名。基底取 `core.constants.CHANNEL_LABELS`（单一真源，与
#: Programmer / 调度台同名），此处只补它没登记、而面板确需的合成/别名键。
CH_LABELS: Dict[str, str] = dict(CHANNEL_LABELS)
CH_LABELS.setdefault("motor_xy", "电机 XY（自动合成）")
CH_LABELS.setdefault("level", "强度（亮度/泵/阀）")

#: 这些键是**内部机制**、不是可选题材：精调通道由粗通道合并驱动，
#: placeholder 是导入占位。展示与选择一律排除。
HIDDEN_SUFFIX = "_fine"
HIDDEN_KEYS = frozenset({"placeholder"})


def channel_key(ch_type: Any) -> str:
    """通道 → 归一化键（小写 + 颜色/强度别名），与本项目引擎口径一致。"""
    raw = getattr(ch_type, "value", ch_type)
    raw = getattr(raw, "ch_type", raw)
    raw = getattr(raw, "type", raw)
    key = str(raw or "").strip().lower()
    return CH_COLOR_ALIASES.get(key, key)


def is_selectable(key: str) -> bool:
    """该通道键是否可作为效果/输出的目标（排除精调与占位）。"""
    k = str(key or "")
    return bool(k) and not k.endswith(HIDDEN_SUFFIX) and k not in HIDDEN_KEYS


def def_channel_keys(fd: Any) -> List[str]:
    """单个设备定义的通道键列表（保序、归一、跳过空项）。"""
    out: List[str] = []
    for ch in (getattr(fd, "channels", None) or ()):
        key = channel_key(ch)
        if key:
            out.append(key)
    return out


def defs_of(project: Any) -> List[Any]:
    """工程 → fixture_defs 列表（dict / list / 缺失都容错）。"""
    if project is None:
        return []
    raw = getattr(project, "fixture_defs", None)
    if raw is None:
        raw = getattr(project, "_fixture_defs", None)
    if isinstance(raw, dict):
        return list(raw.values())
    return list(raw or [])


def _fd_names(fd: Any) -> Dict[str, str]:
    """设备定义的功能通道真名表（非 dict 时返回空表）。"""
    names = getattr(fd, "function_channel_names", None) or {}
    return names if isinstance(names, dict) else {}


def channel_owners(defs: Any) -> Tuple[Dict[str, List[Tuple[str, str]]], List[str]]:
    """通道键 → [(类型名, 真名), ...]，并返回首见顺序的键列表。"""
    owners: Dict[str, List[Tuple[str, str]]] = {}
    order: List[str] = []
    for fd in (defs or ()):
        tname = str(getattr(fd, "name", "") or "")
        names = _fd_names(fd)
        for key in def_channel_keys(fd):
            if key not in owners:
                owners[key] = []
                order.append(key)
            owners[key].append((tname, str(names.get(key, "") or "").strip()))
    return owners, order


def _short_type(name: str) -> str:
    """类型名缩写（下拉标签用）：去掉型号/尺寸尾缀，保留可辨识前缀。"""
    s = str(name or "").strip()
    for sep in (" - ", "  -", "- "):
        if sep in s:
            s = s.split(sep)[0]
            break
    return s.strip() or "?"


def ch_label(key: str) -> str:
    """内置通道显示名（本地补表 → constants → 原键）。"""
    k = str(key or "")
    return CH_LABELS.get(k) or CH_LABELS.get(k.upper()) or k


def channel_item_label(key: str, typed_names: Optional[List[Tuple[str, str]]] = None) -> str:
    """单个下拉项的显示标签（功能通道附真名与键，冲突时逐类型列出）。"""
    lst = [(t, r) for t, r, *_ in ((list(x) + [""] * 3)[:3]
                                   for x in (typed_names or [])) if r]
    if len(lst) <= 1:
        label = lst[0][1] if lst else ch_label(key)
    else:
        label = "｜".join(f"{_short_type(t)}: {r}" for t, r in lst[:3])
        if len(lst) > 3:
            label += f"｜…共{len(lst)}种"
    # 功能通道把键附在真名后（真名可能重复，键才是引擎口径）
    return f"{label} [{key}]" if str(key).startswith("function_") else label


# ── 类型作用域（"这条通道属于哪个设备类型"）────────────────────────────────
#: 作用域分隔符：值形如 `function_1@<def_id>` = "只作用于该设备类型的 function_1"。
#: 无分隔 = 全局（跨类型语义相同的通用通道，如 R/泵/电机）。
SCOPE_SEP = "@"


def make_scoped(key: str, def_id: str) -> str:
    """`键@类型id`（def_id 为空 → 退回全局键）。"""
    k = str(key or "")
    d = str(def_id or "")
    return f"{k}{SCOPE_SEP}{d}" if k and d else k


def split_scope(value: Any) -> Tuple[str, str]:
    """`键@类型id` → (键, 类型id)；无作用域 → (键, "")。"""
    v = str(value or "")
    if SCOPE_SEP in v:
        k, _sep, d = v.partition(SCOPE_SEP)
        return k, d
    return v, ""


def is_scoped(value: Any) -> bool:
    return bool(split_scope(value)[1])


def is_type_specific(key: str, typed_names=None) -> bool:
    """该通道键是否**按类型各义**（必须分开列）。

    判据：`function_*` 槽位，或在任一类型里有工程真名（如 Water Flame 的
    `valve#2` = Gas）。这类键在不同类型里语义不同，合并会串味。
    """
    k = str(key or "")
    if k.startswith("function_"):
        return True
    return any(str(r or "").strip() for _t, r in (typed_names or []))


def _fd_entries(fd: Any) -> List[Tuple[str, str, str]]:
    """单个设备类型 → [(通道键, 类型名, 真名)]（同类型内按通道去重、保序）。"""
    tname = str(getattr(fd, "name", "") or "")
    tid = str(getattr(fd, "id", "") or "")
    names = _fd_names(fd)
    seen: set = set()
    out: List[Tuple[str, str, str]] = []
    for key in def_channel_keys(fd):
        if key in seen:
            continue
        seen.add(key)
        # (通道键, 类型名, 真名) —— 键必须留着，调用方按它建 owners
        out.append((key, tname, str(names.get(key, "") or "").strip()))
    return out


def typed_channel_owners(defs: Any):
    """通道键 → [(类型名, 真名, 类型id), ...]（同类型内去重、保首见顺序）。"""
    owners: Dict[str, List[Tuple[str, str, str]]] = {}
    order: List[str] = []
    for fd in (defs or ()):
        tid = str(getattr(fd, "id", "") or "")
        for key, tname, real in _fd_entries(fd):
            owners.setdefault(key, []).append((tname, real, tid))
            if len(owners[key]) == 1:
                order.append(key)
    return owners, order


def collect_channel_items(defs: Any) -> List[Tuple[str, str]]:
    """工程设备定义 → 下拉项 **[(显示标签, 通道键)]**（隐藏精调/占位）。

    ★ 通用通道（跨类型同义）保持单条全局键；**类型各义的通道逐类型各成一条**，
    值为 `键@类型id`（选中即只作用于该类型设备）。见模块头定案。
    """
    owners, order = typed_channel_owners(defs)
    out: List[Tuple[str, str]] = []
    for key in order:
        if not is_selectable(key):
            continue
        typed = owners.get(key) or []
        if is_type_specific(key, [(t, r) for t, r, _d in typed]):
            for tname, real, tid in typed:
                label = f"{_short_type(tname)} · {real or key}"
                out.append((label, make_scoped(key, tid)))
        else:
            out.append((ch_label(key), key))
    return out


def collect_channel_items_plain(defs: Any) -> List[Tuple[str, str]]:
    """同 `collect_channel_items`，但**不展开类型作用域**（裸键一条）。

    给"选择本身已绑定具体设备"的调用方用 —— 关联器的规则里每条通道引用都带
    `fixture_id`，作用域由设备决定，键只作引擎匹配用（且引擎必须收到裸键）。
    类型各义的通道在此**取首个有真名的类型**显示（如 "Zoom"），多类型时附注。
    """
    owners, order = typed_channel_owners(defs)
    out: List[Tuple[str, str]] = []
    for key in order:
        if not is_selectable(key):
            continue
        typed = [(t, r, d) for t, r, d in (owners.get(key) or [])]
        label = channel_item_label(key, typed)
        out.append((label, key))
    return out


def label_map(items: Any) -> Dict[str, str]:
    """[(标签, 键)] → {键: 标签}（**契约方向的唯一判定处**）。"""
    out: Dict[str, str] = {}
    for item in (items or ()):
        try:
            label, key = item
        except Exception:
            continue
        out[str(key)] = label
    return out


# ── 工程级缓存（defs 内容令牌失效，供每帧/每次重建 UI 的调用方使用）──────────

_CACHE: Dict[Any, List[Tuple[str, str]]] = {}
_CACHE_CAP = 64


def def_content_token(fd: Any) -> tuple:
    """**单个**设备定义的内容令牌（R194/P2-18：覆盖 `to_dict()` 全部字段）。

    为什么必须"全字段"：`matrix_fixture_signature` 按 token 缓存每 def 的
    `to_dict()` 序列化片段——token 漏哪个字段，哪个字段被 patch 面板就地改了
    （patch_panel.py:1326 就地 append channels、:496/:561 同对象改
    channels/channel_count/function_channel_defaults）片段就**陈旧**，签名不变
    → 矩阵编译缓存吃到旧定义。故在旧四元（id/name/通道内容/真名）之上补齐：
    channel_count、**原始通道值**、function_channel_defaults、channel_modes、
    motor_ranges。
    通道只取**原始值 repr**，不再过 `def_channel_keys` 归一化：归一键是原始
      通道的派生值，任何影响序列化的通道改动必然改变原始值 repr（超集判变），
      而归一化逐通道走 getattr/别名表，是令牌构建的最大开销。
    两个投影取舍（channel_modes/motor_ranges）：
      · 一律 `repr`（不用 str/float 归一）：`0` 与 `0.0`、`1` 与 `"1"` 序列化
        结果不同，令牌把它们合并会造成片段缓存**错误命中**（签名陈旧）；
      · **不排序**：正确性只需要"内容变 → 令牌变"。dict 键序不影响序列化
        （sort_keys），同内容不同键序 → 令牌不同 = 多付一次重算（良性 miss）。
    实现刻意**拍平内联**（无子函数调用）：本函数在签名热路径按 (选择变化 ×
    def 数) 调用，实测 ~3μs/def，对 to_dict+整段序列化（~6.7μs/def）才有
    净收益（分层+排序版 4.7μs，收益近零）。代价 O(通道数+模式数) 次轻量 repr。
    """
    names = getattr(fd, "function_channel_names", None)
    if not isinstance(names, dict):
        names = {}
    defaults = getattr(fd, "function_channel_defaults", None)
    if not isinstance(defaults, dict):
        defaults = {}
    modes = getattr(fd, "channel_modes", None)
    ranges = getattr(fd, "motor_ranges", None)
    return (
        repr(getattr(fd, "id", None)),
        repr(getattr(fd, "name", None)),
        repr(getattr(fd, "channel_count", None)),
        tuple(map(repr, getattr(fd, "channels", None) or ())),
        tuple((repr(k), repr(v)) for k, v in names.items()),
        tuple((repr(k), repr(v)) for k, v in defaults.items()),
        (() if not isinstance(modes, dict) else
         tuple((repr(k),
                tuple((tuple((repr(kk), repr(vv)) for kk, vv in m.items())
                       if isinstance(m, dict) else (repr(m),))
                      for m in (lst or ())))
               for k, lst in modes.items())),
        (() if not isinstance(ranges, dict) else
         tuple((repr(k), repr(v)) for k, v in ranges.items())),
    )


def _defs_token(defs: List[Any]) -> tuple:
    """设备定义内容的廉价令牌（通道表/真名/类型名/**模式与电机范围**变了即失效）。

    R194/P2-18：改由 `def_content_token` 逐 def 组成（原版只含 id/name/
      通道键/真名，缺 channel_modes/motor_ranges —— 而 patch 面板对**同一
      def 对象**就地改这些字段，id/身份键方案已被证伪，必须按内容判变）。
      令牌变严 = `project_items` 缓存失效更准（原来漏字段时是错误命中）。
    """
    return tuple(def_content_token(fd) for fd in defs)


def project_items(project: Any) -> List[Tuple[str, str]]:
    """工程全部可选题材的 [(标签, 键)]（带令牌缓存）。"""
    defs = defs_of(project)
    if not defs:
        return []
    token = _defs_token(defs)
    hit = _CACHE.get(token)
    if hit is not None:
        return hit
    items = collect_channel_items(defs)
    _CACHE[token] = items
    if len(_CACHE) > _CACHE_CAP:
        for k in list(_CACHE)[: len(_CACHE) - _CACHE_CAP]:
            _CACHE.pop(k, None)
    return items


def project_keys(project: Any) -> List[str]:
    """工程全部可选题材的通道键（保序）。"""
    return [k for _lbl, k in project_items(project)]


def project_label_map(project: Any) -> Dict[str, str]:
    """{通道键或作用域值: 显示名}（内置键也给标签，便于下拉统一渲染）。

    ★ 作用域值（`function_1@类型id`）**原样作为键**返回 —— 下拉用它取值，
    显示用它取标签，两者同一张表，不会再出现"契约方向反过来"的老坑。
    """
    out = dict(label_map(project_items(project)))
    for k in list(out):
        out.setdefault(k, ch_label(split_scope(k)[0]))
    return out


def extra_keys(project: Any, known: Any) -> List[str]:
    """工程里**不在 `known` 内**的通道键（= CH_TYPES 之外的功能通道/自定义通道）。

    写地址映射只需补这批：内置键本来就在候选表里，重复遍历纯属浪费。
    """
    known_norm = {channel_key(k) for k in (known or ())}
    seen = set()
    out: List[str] = []
    for k in project_keys(project):
        # 地址映射按**纯通道键**收集（作用域只决定写到哪些设备，见 effect 写路径）
        plain = split_scope(k)[0]
        if plain in known_norm or plain in seen:
            continue
        seen.add(plain)
        out.append(plain)
    return out


def target_covers_channel(target_channels: Any, ch: str) -> bool:
    """预设是否覆盖 `ch`（**通用预设覆盖任意工程通道**）。

    通用/万能效果的 `target_channels` 就是内置全集（`CH_TYPES`），它天然适用于
    工程里任何真实存在的通道（含功能通道）；若只按字面 `ch in target_channels`
    过滤，选中功能通道时会把所有通用效果筛没。
    """
    tc = {channel_key(c) for c in (target_channels or ()) if str(c)}
    if not tc:
        return False
    key = channel_key(ch)
    if key in tc:
        return True
    # 通用预设：内置全集 ⊇ 判据（且确实声明了不止一两个通道）
    builtin = {channel_key(c) for c in _BUILTIN_CHANNEL_KEYS}
    return len(tc) >= len(builtin) and builtin <= tc


#: 内置通道全集（与 effect_engine.CH_TYPES 同源的键名，此处只做集合判定，
#: 避免 UI 工具模块反向依赖效果引擎）。
_BUILTIN_CHANNEL_KEYS = (
    "dimmer", "R", "G", "B", "W", "motor_x", "motor_y", "motor_z",
    "valve", "pump",
)


__all__ = [
    "CH_LABELS", "_fd_names", "channel_key", "is_selectable", "def_channel_keys", "defs_of",
    "def_content_token",
    "channel_owners", "typed_channel_owners", "ch_label", "channel_item_label",
    "collect_channel_items", "collect_channel_items_plain", "make_scoped", "split_scope", "is_scoped",
    "is_type_specific", "SCOPE_SEP",
    "label_map", "project_items", "project_keys", "project_label_map",
    "extra_keys", "target_covers_channel",
]
