"""
DMXTimeline — TrackLinkerEngine  v1.0
轨道关联器底层引擎

职责：
  1. 持有若干 LinkRule（关联规则），每条规则描述：
       - 源通道类型（如 motor_x）所在的设备组/设备实例 → 读取当前 DMX 值
       - 目标通道类型（如 pump）所在的设备组/设备实例 → 写入计算结果
       - 关联预设函数（LinkPreset）
  2. 每 25ms 由 QTimer 触发 _tick()：
       - 从 NetworkManager 当前混合输出快照读取源通道值
       - 经预设函数计算目标值
       - 通过 NM.update_layer_sparse("linker", ...) 写入专用层
  3. 支持启用/停用单条规则，以及全局启用/停用
  4. 支持序列化/反序列化（to_dict / from_dict）

关联预设（LinkPreset）：
  每个预设是一个 Python 函数对象，签名：
    fn(src_val: float, params: dict) -> float
  src_val 范围 0~255，返回值也是 0~255（引擎会 clip）
  内置预设见 BUILTIN_LINK_PRESETS 列表
"""
from __future__ import annotations

import math
import time as _time
import uuid
import logging
import numpy as np
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import Qt, QObject, QTimer, pyqtSignal

from ..engine.xy_reference import (
    logical_to_16bit_pair,
    read_axis_logical_value,
)
from ..core.constants import (  # 常量单源(2026-09-12)
    ALL_LAYERS, CH_COLOR_ALIASES, MOTOR_FINE_CHANNELS, MOTOR_FINE_TO_COARSE,
    MOTOR_SRC_TYPES, STATIC_EDITOR_LAYERS,
)

log = logging.getLogger(__name__)



def _channel_type_key(ch_type: Any) -> str:
    raw = getattr(ch_type, "value", ch_type)
    raw = getattr(raw, "ch_type", raw)
    raw = getattr(raw, "type", raw)
    s = str(raw or "").strip()
    key = s.lower()
    return CH_COLOR_ALIASES.get(key, key)


def _canonical_ch_type(ch_type: Any) -> str:
    key = _channel_type_key(ch_type)
    if key == "motor_xy":
        return "motor_xy"
    if key in {"r", "g", "b", "w"}:
        return key.upper()
    return key


#: 电机源"停放/未驱动"读数归一阈值（逻辑单位）：距 0 不到 1 个逻辑单位视为停放。
#: 取值理由：与 xy_reference 既有的静止判据同口径（`math.hypot(dx, dy) < 1.0`，
#: 注释为"半个 8bit 步进"）——8bit 停放 0 → 归 128（行为不变）；16bit 粗 0 +
#: 细残量合成 0.5~0.996（如 0/128 → 0.5）同为停放 → 归 128；真实运动读数
#: （≥1 逻辑单位，粗≥1 的任何组合）不受影响。
MOTOR_PARKED_READ_EPS = 1.0


def _normalize_parked_motor_read(val: float) -> float:
    """电机源"停放/未驱动"读数归一为逻辑归中 128.0（见 MOTOR_PARKED_READ_EPS）。

    2026-08-11 修复只兜底精确 `val == 0.0`（8bit 形态）；16bit 电机
    （motor_x + motor_x_fine）停放时 read_axis_logical_value 合成 0.5 ≠ 0.0，
    旧判据漏判 → 0.5 被当真实偏离（|0.5−128| = 127.5 ≥ cutoff）→ 泵
    cosine 结果 0 → 低值覆盖 min 分支把泵写 0（三维设备"一开关联器就不出水"）。
    """
    return 128.0 if 0.0 <= val < MOTOR_PARKED_READ_EPS else val


def _ref_parked_in_mask(mask: Optional[np.ndarray], addr: Any) -> bool:
    """单个 DMX 地址是否处于"停放"掩码中（None/越界/异常 → False）。"""
    if mask is None or addr is None:
        return False
    try:
        a = int(addr)
    except Exception:
        return False
    return 0 <= a < 512 and bool(mask[a])


def _motor_ref_parked(mask: Optional[np.ndarray], coarse_addr: Any, fine_addr: Any) -> bool:
    """电机引用是否"停放"（16bit 感知）：粗地址或细地址任一在停放掩码中即停放。

    形态与 _is_active 的 fine 回退对称（粗地址先查、未命中再回退细地址）——
    只查粗地址会漏判细字节停放的 16bit 电机（两处判据不对称）。
    fine_addr 为 None（8bit 电机/无细通道）时退化为只查粗地址，行为不变。
    """
    if _ref_parked_in_mask(mask, coarse_addr):
        return True
    return _ref_parked_in_mask(mask, fine_addr)


#: 灯跟踪分支的通道 → 参数键映射（泵→灯：按目标通道类型取对应颜色/亮度参数）。
_LIGHT_TRACK_CH_MAP = {
    "dimmer": "dimmer", "intensity": "dimmer",
    "R": "R", "r": "R", "red": "R",
    "G": "G", "g": "G", "green": "G",
    "B": "B", "b": "B", "blue": "B",
    "W": "W", "w": "W", "white": "W",
}


def _classify_units(units, unit_scan, sel_src_type, sel_dst_type, rules, *,
                    preset_id, params, group_label, src_canon, dst_canon,
                    is_color_preset, make_rule):
    """逐单元选源/目标子设备：双全 → 直接一条规则；否则归入纯源/纯目标池。

    返回 (src_only, dst_only, rules)。自关联（同一单元同时有源与目标）在此
    直接成规则（映射到真实子设备通道，不是包代表点）。
    """
    src_only: List[tuple] = []   # (unit, src_cand)
    dst_only: List[tuple] = []   # (unit, dst_cand)
    for unit, (src_cands, dst_cands) in zip(units, unit_scan):
        src_sel = _pick_cand(src_cands, sel_src_type)
        dst_sel = _pick_cand(dst_cands, sel_dst_type)
        if src_sel is not None and dst_sel is not None:
            rules.append(_self_link_rule(
                src_sel, dst_sel, preset_id=preset_id, params=params,
                group_label=group_label, src_canon=src_canon,
                dst_canon=dst_canon, is_color_preset=is_color_preset,
                make_rule=make_rule))
        elif src_sel is not None:
            src_only.append((unit, src_sel))
        elif dst_sel is not None:
            dst_only.append((unit, dst_sel))
    return src_only, dst_only, rules


def _pick_cand(cands, want_type):
    """按类型挑候选（want_type=None = 不过滤，取首个）。"""
    return next((c for c in cands
                 if want_type is None or c["type_name"] == want_type), None)


def _self_link_rule(src_sel, dst_sel, *, preset_id, params, group_label,
                    src_canon, dst_canon, is_color_preset, make_rule):
    """自关联规则：同一单元既有源又有目标子设备（src → dst 一条）。

    电机→颜色预设的目标 = 该子设备**全部色彩通道**（RGBW），故走 refs 列表。
    """
    d_refs = (dst_sel.get("refs") if is_color_preset else None) or [dst_sel["ref"]]
    d_label = "RGBW" if is_color_preset else dst_canon
    return make_rule(
        name=(f"{getattr(src_sel['inst'], 'name', 'Fixture')} {src_canon}→"
              f"{getattr(dst_sel['inst'], 'name', 'Fixture')} {d_label}"),
        src_refs=src_sel["refs"], dst_refs=d_refs,
        preset_id=preset_id, params=params, group_label=group_label,
        type_name=str(src_sel.get("type_name", "") or ""))


def _emit_paired_or_broadcast(rules, src_only, dst_only, *, preset_id, params,
                              group_label, src_canon, dst_canon,
                              is_color_preset, make_rule) -> None:
    """纯源池 × 纯目标池：数量相等按序一一配对；不等则一条组内广播规则。

    数量不等时生成 1 条广播规则（所有源通道平均 → 所有目标通道），与既有权
    重语义一致；防泵关停由调用方保证（无有效源/目标的单元不会进池）。
    """
    if not (src_only and dst_only):
        return
    ctx = {"preset_id": preset_id, "params": params, "group_label": group_label,
           "src_canon": src_canon, "dst_canon": dst_canon,
           "is_color_preset": is_color_preset, "make_rule": make_rule}
    if len(src_only) == len(dst_only):
        for (_, src_sel), (_, dst_sel) in zip(src_only, dst_only):
            rules.append(_self_link_rule(src_sel, dst_sel, **ctx))
        return
    rules.append(_broadcast_link_rule(src_only, dst_only, **ctx))


def _broadcast_link_rule(src_only, dst_only, *, preset_id, params, group_label,
                         src_canon, dst_canon, is_color_preset,
                         make_rule, **_) -> "LinkRule":
    """数量不等时的组内广播规则（全部源 → 全部目标）。"""
    src_refs = [ref for (_, cand) in src_only for ref in cand["refs"]]
    dst_refs = [r for (_, cand) in dst_only
                for r in ((cand.get("refs") if is_color_preset else None)
                          or [cand["ref"]])]
    bcast_type = str((src_only[0][1] or {}).get("type_name", "") or "") if src_only else ""
    return make_rule(
        name=(f"{src_canon}→{dst_canon}（组内广播 "
              f"{len(src_only)}源单元→{len(dst_only)}目标单元）"),
        src_refs=src_refs, dst_refs=dst_refs,
        preset_id=preset_id, params=params, group_label=group_label,
        type_name=bcast_type)


def _color_whiten_writes(rule, preset_def, src_channels, *, snapshot, writes,
                         read_src, key_of) -> None:
    """电机→颜色 偏色分支（用户定案 2026-09-17）：读当前混音输出 → 向白混合。

    语义：读**当前混音输出**的 RGB（快照已排除 linker 自身 → 无自激），按
    "偏色进度"把当前颜色原地推向白色：
      正向：进度 = |src−center|/span → 归中=0（原色）→ 偏离=1（白）
      反转(invert)：归中 = 白，偏离 → 回归当前生效颜色
    ★ 本预设是**绝对写入**（不受"低值覆盖"限制）：往白偏需要**抬高**非主色
      通道，低值覆盖只减不加做不到。故此处直接写目标值，不经 _write_dst。
    """
    vals = [v for v in (read_src(ref) for ref in src_channels) if v is not None]
    if not vals:
        return
    src_val = sum(vals) / len(vals)
    params = dict(rule.params)
    prog = float(np.clip(preset_def.fn(src_val, params), 0.0, 1.0))
    white = float(np.clip(params.get("white", 255.0), 0.0, 255.0))
    cur: Dict[str, float] = {}
    for ref in rule.dst_channels:
        uni_data = snapshot.get(int(ref.universe))
        try:
            cur[key_of.get(id(ref))] = (0.0 if uni_data is None
                                        else float(uni_data[int(ref.dmx_addr)]))
        except Exception:
            cur[key_of.get(id(ref))] = 0.0
    for ref in rule.dst_channels:
        key = key_of.get(id(ref))
        base = float(cur.get(key, 0.0))
        # 亮度通道不参与偏白（白化只作用于色彩通道）
        out_val = base if key == "dimmer" else base + (white - base) * prog
        writes.setdefault(ref.universe, {})[int(ref.dmx_addr)] = int(
            np.clip(out_val, 0.0, 255.0))


def _light_track_writes(rule, preset_def, src_channels, *, read_src, stagger,
                        n_dst) -> None:
    """灯跟踪分支：泵→灯（按目标通道类型输出对应颜色值）。

    识别标志：preset_id 以 pump_light_track 开头。fn 给出 0~1 增益系数，
    目标通道按类型取各自参数（dimmer/R/G/B/W）后乘以增益。
    """
    vals = [v for v in (read_src(ref) for ref in src_channels) if v is not None]
    if not vals:
        return
    params = dict(rule.params)
    gain = float(np.clip(preset_def.fn(sum(vals) / len(vals), params), 0.0, 1.0))
    for i, dst_ref in enumerate(rule.dst_channels):
        key = _LIGHT_TRACK_CH_MAP.get(_canonical_ch_type(dst_ref.ch_type))
        target = float(params.get(key if key is not None else "dimmer", 255.0))
        stagger(dst_ref, float(np.clip(target * gain, 0.0, 255.0)), i, n_dst)


def _is_motor_to_pump_preset(preset_id: str) -> bool:
    preset_key = str(preset_id or "")
    return preset_key == "motor_xy_pump" or preset_key.startswith("motor_pump_")


def _rule_signature(rule: "LinkRule") -> tuple:
    """规则身份指纹：预设 + 源通道集 + 目标通道集（顺序无关）。

    仅用于「同一类型组内重复批生成」的幂等判定 —— 参数/名称改变也算新规则
    （用户可能确实想用不同参数再叠一层），故不进指纹。
    """
    src = tuple(sorted((int(r.universe), int(r.dmx_addr))
                       for r in getattr(rule, "src_channels", ()) or ()))
    dst = tuple(sorted((int(r.universe), int(r.dmx_addr))
                       for r in getattr(rule, "dst_channels", ()) or ()))
    return (str(getattr(rule, "preset_id", "") or ""), src, dst)


def _dedupe_against_group(group, incoming: List["LinkRule"],
                          existing: List["LinkRule"]) -> tuple:
    """把 incoming 中与组内**已有**规则同指纹的条目剔除（返回 keep/skipped）。

    只对「已在组内」的规则去重（防手滑重复生成同一批）；**同批内部不折叠**
    ——同一预设两条规则若真指向同源同目标，那是生成层的决定，UI 不替它裁决
    （实测：内部折叠会吞掉合法的「同预设多实例」规则）。
    """
    seen = {_rule_signature(r) for r in (existing or ())}
    keep: List["LinkRule"] = []
    skipped: List["LinkRule"] = []
    for r in (incoming or ()):
        if _rule_signature(r) in seen:
            skipped.append(r)
        else:
            keep.append(r)
    return keep, skipped


def _has_pump_dst(dst_channels: List["ChannelRef"]) -> bool:
    return any(_channel_type_key(getattr(ref, "ch_type", "")) == "pump" for ref in (dst_channels or []))


def _xy_deviation(src: float, p: dict) -> float:
    center_x = float(p.get("center_x", p.get("center", 128.0)))
    center_y = float(p.get("center_y", p.get("center", 128.0)))
    aux = p.get("_aux_val", None)
    if aux is None:
        return abs(float(src) - center_x)
    dx = float(src) - center_x
    dy = float(aux) - center_y
    return math.sqrt(dx * dx + dy * dy)

# ─────────────────────────────────────────────────────────────────────────────
#  内置关联预设定义
# ─────────────────────────────────────────────────────────────────────────────

class LinkPresetDef:
    """一个关联预设的完整定义。

    ★★ 2026-09-18 用户定案（"预设里有相关冲突的地方都自己优化掉"）：
      部分预设的目标**不是单个通道**，而是"设备上某一整组通道"——
        · `motor_color_whiten`：目标 = 设备**全部色彩通道**（RGBW 一起偏色）；
        · `pump_light_track*`：目标 = 该设备**所有灯通道**（dimmer+R+G+B+W）。
      这类预设不该再让用户去选"某一个通道"（选了也做不出效果——偏白必须
      同时写 R/G/B/W）。`dst_group` 就是该声明：预设自己说了算，
      UI 隐藏目标通道下拉、引擎按声明展开通道集合。

    取值（`dst_group`）：
      ""            — 单通道（默认，行为与历史完全一致）
      "device_rgbw" — 该设备的全部色彩通道（R/G/B/W，按 def 顺序）
      "device_light"— 该设备的全部灯通道（dimmer/R/G/B/W）
    """
    def __init__(
        self,
        id: str,
        name: str,
        category: str,
        desc: str,
        src_type_hint: str,   # 建议的源通道类型，如 "motor_x"
        dst_type_hint: str,   # 建议的目标通道类型，如 "pump"
        params_def: List[dict],  # 参数定义列表
        fn: Callable[[float, dict], float], *,
        dst_group: str = "",  # 目标组声明（见类文档）；"" = 单通道
    ):
        self.id            = id
        self.name          = name
        self.category      = category
        self.desc          = desc
        self.src_type_hint = src_type_hint
        self.dst_type_hint = dst_type_hint
        self.params_def    = params_def   # [{"key","label","min","max","default","scale","unit"}]
        self.fn            = fn
        self.dst_group     = str(dst_group or "")


#: `dst_group` → 该组包含的通道键（顺序 = 写入顺序，UI 预览也用它）。
DST_GROUP_CHANNELS: Dict[str, tuple] = {
    "device_rgbw":  ("r", "g", "b", "w"),
    "device_light": ("dimmer", "r", "g", "b", "w"),
}


def preset_dst_group(preset_id: str) -> str:
    """预设声明的目标组（"" = 单通道）。找不到预设时按单通道处理。"""
    pdef = PRESET_BY_ID.get(str(preset_id or ""))
    return str(getattr(pdef, "dst_group", "") or "") if pdef is not None else ""


def preset_targets_group(preset_id: str) -> bool:
    """该预设的目标是否为"整组通道"（UI 据此隐藏目标通道选择）。"""
    return bool(preset_dst_group(preset_id))

# ── 预设函数实现 ──────────────────────────────────────────────────────────────

def _motor_pump_cosine(src: float, p: dict) -> float:
    """电机偏差→泵输出（余弦平方衰减）。
    src=128 时泵最大，偏离 cutoff 后泵=0。
    """
    cutoff   = max(1.0, p.get("cutoff", 64.0))
    max_pump = p.get("max_pump", 255.0)
    deviation = _xy_deviation(src, p)
    if deviation >= cutoff:
        return 0.0
    ratio = deviation / cutoff
    return max_pump * math.cos(ratio * math.pi / 2) ** 2

def _motor_pump_linear(src: float, p: dict) -> float:
    """电机偏差→泵输出（线性衰减）。"""
    cutoff   = max(1.0, p.get("cutoff", 64.0))
    max_pump = p.get("max_pump", 255.0)
    deviation = _xy_deviation(src, p)
    if deviation >= cutoff:
        return 0.0
    return max_pump * (1.0 - deviation / cutoff)

def _motor_pump_step(src: float, p: dict) -> float:
    """电机偏差→泵输出（阶跃：在范围内全开，超出全关）。"""
    cutoff   = max(1.0, p.get("cutoff", 64.0))
    max_pump = p.get("max_pump", 255.0)
    return max_pump if _xy_deviation(src, p) < cutoff else 0.0

def _motor_pump_gaussian(src: float, p: dict) -> float:
    """电机偏差→泵输出（高斯曲线，中心最大，两侧平滑衰减）。"""
    sigma    = max(1.0, p.get("sigma", 40.0))
    max_pump = p.get("max_pump", 255.0)
    deviation = _xy_deviation(src, p)
    return max_pump * math.exp(-(deviation ** 2) / (2 * sigma ** 2))

def _motor_valve_linear(src: float, p: dict) -> float:
    """电机角度→阀门开度（线性映射，可设反向）。"""
    center  = p.get("center", 128.0)
    scale   = p.get("scale", 2.0)   # 每单位偏差对应的阀门变化量
    invert  = p.get("invert", 0.0)  # 1=反向
    base    = p.get("base", 128.0)  # 阀门基础值（归中时）
    deviation = (src - center) * scale
    result = base + deviation
    if invert > 0.5:
        result = 255.0 - result
    return result

def _motor_valve_cosine(src: float, p: dict) -> float:
    """电机角度→阀门开度（余弦映射，归中时最大）。"""
    cutoff  = max(1.0, p.get("cutoff", 64.0))
    max_val = p.get("max_val", 255.0)
    center  = p.get("center", 128.0)
    deviation = abs(src - center)
    if deviation >= cutoff:
        return 0.0
    ratio = deviation / cutoff
    return max_val * math.cos(ratio * math.pi / 2) ** 2

def _pump_dimmer_linear(src: float, p: dict) -> float:
    """泵强度→灯光亮度（线性映射）。"""
    scale  = p.get("scale", 1.0)
    offset = p.get("offset", 0.0)
    return src * scale + offset

def _pump_dimmer_sqrt(src: float, p: dict) -> float:
    """泵强度→灯光亮度（平方根，低泵值时亮度更高）。"""
    scale = p.get("scale", 1.0)
    return math.sqrt(max(0.0, src / 255.0)) * 255.0 * scale

def _pump_dimmer_power(src: float, p: dict) -> float:
    """泵强度→灯光亮度（幂次映射）。"""
    gamma = max(0.01, p.get("gamma", 2.0))
    return (src / 255.0) ** gamma * 255.0

def _motor_color_hue(src: float, p: dict) -> float:
    """电机角度→颜色通道（将角度映射到色相的某个分量）。
    用于 R/G/B 各通道分别配置相位偏移，实现彩虹效果。
    """
    phase  = p.get("phase", 0.0)    # 0~360 度相位偏移
    scale  = p.get("scale", 1.0)    # 角度→色相的缩放
    center = p.get("center", 128.0)
    angle  = (src - center) * scale + phase
    # 转换为 0~1 的色相值，再映射到 0~255
    hue_norm = (math.sin(math.radians(angle)) + 1.0) / 2.0
    return hue_norm * 255.0

def _intensity_pump_linear(src: float, p: dict) -> float:
    """亮度通道→泵强度（线性）。"""
    scale  = p.get("scale", 1.0)
    offset = p.get("offset", 0.0)
    return src * scale + offset

def _intensity_pump_threshold(src: float, p: dict) -> float:
    """亮度通道→泵强度（阈值：亮度超过阈值才开泵）。"""
    threshold = p.get("threshold", 128.0)
    max_pump  = p.get("max_pump", 255.0)
    if src < threshold:
        return 0.0
    return (src - threshold) / max(1.0, 255.0 - threshold) * max_pump

def _passthrough(src: float, p: dict) -> float:
    """直通（源值直接输出到目标）。"""
    scale  = p.get("scale", 1.0)
    offset = p.get("offset", 0.0)
    return src * scale + offset

def _invert(src: float, p: dict) -> float:
    """反转（255 - src）。"""
    return 255.0 - src

def _scale_offset(src: float, p: dict) -> float:
    """缩放+偏移（通用线性变换）。"""
    scale  = p.get("scale", 1.0)
    offset = p.get("offset", 0.0)
    return src * scale + offset

def _clamp_range(src: float, p: dict) -> float:
    """限幅到指定范围。"""
    lo = p.get("lo", 0.0)
    hi = p.get("hi", 255.0)
    return max(lo, min(hi, src))

def _motor_xy_to_pump(src: float, p: dict) -> float:
    """电机 XY 归中保持泵，二维偏离越大泵越低。
    """
    cutoff = max(1.0, float(p.get("cutoff", math.sqrt(2) * 127.0)))
    max_pump = float(p.get("max_pump", 255.0))
    deviation = _xy_deviation(src, p)
    if deviation >= cutoff:
        return 0.0
    return max_pump * (1.0 - deviation / cutoff)

def _sine_modulate(src: float, p: dict) -> float:
    """正弦调制（src 作为相位输入，输出正弦波形）。"""
    freq   = p.get("freq", 1.0)
    depth  = p.get("depth", 64.0)
    center = p.get("center", 128.0)
    return center + depth * math.sin(src / 255.0 * math.pi * 2 * freq)

def _motor_pump_bidir(src: float, p: dict) -> float:
    """双向电机→泵（左偏/右偏分别控制不同泵，此函数控制正向泵）。"""
    center   = p.get("center", 128.0)
    cutoff   = max(1.0, p.get("cutoff", 64.0))
    max_pump = p.get("max_pump", 255.0)
    deviation = src - center  # 正值=右偏
    if deviation <= 0:
        return 0.0
    if deviation >= cutoff:
        return max_pump
    return max_pump * math.cos((1.0 - deviation / cutoff) * math.pi / 2) ** 2

def _motor_pump_bidir_neg(src: float, p: dict) -> float:
    """双向电机→泵（负向泵，左偏时激活）。"""
    center   = p.get("center", 128.0)
    cutoff   = max(1.0, p.get("cutoff", 64.0))
    max_pump = p.get("max_pump", 255.0)
    deviation = center - src  # 正值=左偏
    if deviation <= 0:
        return 0.0
    if deviation >= cutoff:
        return max_pump
    return max_pump * math.cos((1.0 - deviation / cutoff) * math.pi / 2) ** 2

# ── 百变（Vario/百变，zoom 通道）→水 ─────────────────────────────────────────
def _vario_pump_map(src: float, p: dict, gamma=None) -> float:
    """百变→水共用映射：zoom/电机 行程 → 泵输出（含反转开关 + 可选非线性刻度）。

    默认关系（用户定案）：**反相关** —— 百变电机（常配接为名为 Zoom 的功能通道）
    取最小值时泵最大；电机取最大值时泵减到最小。
    `invert` ≥ 0.5 → 反转为**正相关**（电机最小 → 泵最小；电机最大 → 泵最大）。

    行程端点不硬编码：`src_min`/`src_max` = 电机通道在此工程里的实际端点
    （配接端点各异，默认 0/255）；`pump_max`/`pump_min` = 泵输出端点（默认 255/0），
    因此同一预设对任意工程/设备刻度都成立。

    gamma（非线性"欧姆刻度"，仅曲线预设暴露）：对**泵行程进度** u 施加
    u' = u**gamma（u=0 → 泵最小端，u=1 → 泵最大端）。
      - gamma < 1：抬升行程前段 —— 补偿"泵对小 DMX 值迟钝、大值才明显"的
        非线性响应，推电机时泵更早、更均匀地起反应；
      - gamma = 1：线性；
      - gamma > 1：强调行程后段。
    """
    src_min = float(p.get("src_min", 0.0))
    src_max = float(p.get("src_max", 255.0))
    out_max = float(p.get("pump_max", 255.0))
    out_min = float(p.get("pump_min", 0.0))
    span = src_max - src_min
    if abs(span) < 1e-6:
        span = 1.0
    t = max(0.0, min(1.0, (float(src) - src_min) / span))
    invert = float(p.get("invert", 0.0) or 0.0) >= 0.5
    u = t if invert else 1.0 - t          # u = 泵行程进度（1 = 泵最大）
    g = float(p.get("gamma", 1.0) or 1.0) if gamma is None else float(gamma)
    if g > 0.0 and abs(g - 1.0) > 1e-9:
        u = u ** g
    return out_min + (out_max - out_min) * u


def _vario_pump_linear(src: float, p: dict) -> float:
    """百变→水（线性）：默认反相关，`invert` 可切换为正相关。"""
    return _vario_pump_map(src, p, gamma=1.0)


def _vario_pump_curve(src: float, p: dict) -> float:
    """百变→水（非线性/欧姆刻度）：gamma 非均匀分布，补偿泵小值迟钝。"""
    return _vario_pump_map(src, p, gamma=None)


# ── 电机（XYZ）→ 颜色 偏色 ────────────────────────────────────────────────────
def _motor_color_whiten(src: float, p: dict) -> float:
    """电机偏离程度 → 往**白色**偏色的混合系数（0~1，仅作预设计算用）。

    用户定案（2026-09-17）：
      · 电机完全归中（不偏离原点）→ 系数 0：**原样保留设备当前正在生效的颜色**；
      · 偏离越大 → 系数越大：按偏离程度把当前颜色**推向白色**。
      · `invert` ≥ 0.5（反转模式）：归中位**必为白色**，偏离时往当前正在生效的
        颜色回归（系数 1 表示完全回到原色）。

    注意：本函数只算"混合进度"。真正的颜色混合需要读**当前混音输出**的 RGB
    （含 programmer/effect/clip 全部生效层），由引擎的偏色分支按该进度原地混合
    —— 预设函数本身拿不到其他通道值（与 pump_light_track 同构：fn 只给比例）。
    """
    center = float(p.get("center", 128.0))
    span = max(1.0, float(p.get("span", 128.0)))
    t = max(0.0, min(1.0, abs(float(src) - center) / span))
    if float(p.get("invert", 0.0) or 0.0) >= 0.5:
        return 1.0 - t                      # 反转：归中=白(0 进度) → 偏离=原色
    return t                                # 正向：归中=原色 → 偏离=白


# ── 内置预设列表 ──────────────────────────────────────────────────────────────


# ── 泵→灯跟踪 ─────────────────────────────────────────────────────────────────
def _pump_light_track(src: float, p: dict) -> float:
    """泵→灯跟踪：泵值超过阈值时输出增益（0~1），低于阈值输出 0。
    返回值是增益系数（0.0~1.0），由 _process_rule 的灯跟踪分支
    乘以各通道颜色值后写入目标。
    """
    threshold = float(p.get("threshold", 30.0))
    smooth    = float(p.get("smooth", 20.0))   # 平滑过渡区宽度
    gain      = float(p.get("gain", 1.0))
    if src <= threshold:
        return 0.0
    if smooth <= 0:
        return min(1.0, gain)
    t = min(1.0, (src - threshold) / smooth)
    # 平滑上升（S曲线）
    t = t * t * (3.0 - 2.0 * t)
    return min(1.0, t * gain)

def _pump_light_track_linear(src: float, p: dict) -> float:
    """泵→灯跟踪（线性）：泵值从阈值线性增长到最大值。"""
    threshold = float(p.get("threshold", 30.0))
    max_pump  = float(p.get("max_pump", 255.0))
    gain      = float(p.get("gain", 1.0))
    if src <= threshold:
        return 0.0
    t = min(1.0, (src - threshold) / max(1.0, max_pump - threshold))
    return min(1.0, t * gain)

def _pump_light_track_step(src: float, p: dict) -> float:
    """泵→灯跟踪（阶跃）：泵超过阈值立即全开，低于阈值全关。"""
    threshold = float(p.get("threshold", 30.0))
    gain      = float(p.get("gain", 1.0))
    return min(1.0, gain) if src > threshold else 0.0

def _pump_light_track_pulse(src: float, p: dict) -> float:
    """泵→灯跟踪（脉冲）：泵在窗口范围内灯亮，超出范围灯灭。
    适合做"泵到特定位置时灯亮"的精确触发。
    """
    center    = float(p.get("center", 200.0))
    width     = float(p.get("width", 40.0))
    gain      = float(p.get("gain", 1.0))
    deviation = abs(src - center)
    if deviation >= width:
        return 0.0
    t = 1.0 - deviation / width
    t = t * t * (3.0 - 2.0 * t)  # S曲线
    return min(1.0, t * gain)

# ── 设备通道↔设备通道 通用映射函数（XYZ联动/颜色联动/泵↔阀/电机↔亮度） ──

def _channel_linear(src: float, p: dict) -> float:
    """线性映射：src*scale + offset。"""
    scale  = float(p.get("scale", 1.0))
    offset = float(p.get("offset", 0.0))
    return src * scale + offset

def _threshold_map(src: float, p: dict) -> float:
    """阈值映射：低于阈值输出 0，高于阈值按比例线性抬升。"""
    threshold = float(p.get("threshold", 30.0))
    maxv      = float(p.get("max", 255.0))
    if src <= threshold:
        return 0.0
    return maxv * min(1.0, (src - threshold) / max(1.0, 255.0 - threshold))

def _center_scale_map(src: float, p: dict) -> float:
    """以归中值为基准的线性联动：center + (src-center)*scale。"""
    center = float(p.get("center", 128.0))
    scale  = float(p.get("scale", 1.0))
    return center + (src - center) * scale

def _mirror_map(src: float, p: dict) -> float:
    """镜像映射：以归中值为对称轴反转（255 - src 可归中微调）。"""
    center = float(p.get("center", 128.0))
    return (255.0 - src) * center / 128.0 if center != 128.0 else 255.0 - src

def _deviation_map(src: float, p: dict) -> float:
    """偏差映射：偏离归中越远输出越低（归中时最大）。"""
    center = float(p.get("center", 128.0))
    cutoff = max(1.0, float(p.get("cutoff", 64.0)))
    maxv   = float(p.get("max", 255.0))
    deviation = abs(src - center)
    if deviation >= cutoff:
        return 0.0
    return maxv * (1.0 - deviation / cutoff)

def _home_drive(src: float, p: dict) -> float:
    """归中驱动：低于阈值归中，超过阈值按比例远离归中位。"""
    threshold = float(p.get("threshold", 60.0))
    center    = float(p.get("center", 128.0))
    max_speed = float(p.get("max_speed", 255.0))
    if src <= threshold:
        return center
    return center + max_speed * min(1.0, (src - threshold) / max(1.0, 255.0 - threshold))

BUILTIN_LINK_PRESETS: List[LinkPresetDef] = [

    # ── 电机 → 颜色（偏色）────────────────────────────────────────────────
    # 用户定案（2026-09-17）：适用"含 motor_x/motor_y/motor_z 至少一个"的设备
    # （含百变这类以功能通道充当 zoom 电机的类型）。
    #   正向（默认）：电机归中 → 保持当前生效颜色；偏离越大 → 越往白色偏。
    #   反转：归中位必为白色；一旦偏离 → 往当前生效颜色回归。
    # 颜色基准 = **当前混音输出**（programmer/effect/clip 等全部生效层的结果），
    # 由引擎偏色分支原地读取并做绝对写入（本预设不受"低值覆盖"限制）。
    LinkPresetDef(
        id="motor_color_whiten",
        name="电机→颜色（偏离偏白）",
        category="电机→颜色",
        desc="电机归中→保持当前生效颜色；偏离越大→越往白色偏（读当前混音输出）。"
             "invert=反转：归中必为白，偏离则回到当前生效颜色。",
        src_type_hint="motor_x",
        dst_type_hint="R",
        params_def=[
            {"key": "center", "label": "归中值",   "min": 0, "max": 255, "default": 128, "scale": 1.0, "unit": ""},
            {"key": "span",   "label": "满偏偏差", "min": 1, "max": 255, "default": 128, "scale": 1.0, "unit": ""},
            {"key": "white",  "label": "白色强度", "min": 0, "max": 255, "default": 255, "scale": 1.0, "unit": ""},
            {"key": "invert", "label": "反转关系", "min": 0, "max": 1,   "default": 0,   "scale": 1.0, "unit": ""},
        ],
        fn=_motor_color_whiten,
        dst_group="device_rgbw",   # ★ 目标 = 整组色彩通道（用户定案：不再问单通道）
    ),

    # ── 百变→水（zoom/电机 → 泵）──────────────────────────────────────────
    # 用户定案：百变电机（常配接为名为 Zoom 的功能通道）最小 → 泵最大；
    # 电机最大 → 泵最小（反相关为默认）。两条预设分别给线性与非线性的
    # "欧姆刻度"；两条都带 invert 开关可反转成正相关。基础输出逻辑
    # （批量规则统一低值覆盖）完全复用，预设只决定映射曲线。
    LinkPresetDef(
        id="vario_pump_linear",
        name="百变→水（线性）",
        category="百变→水",
        desc="百变电机（Zoom/电机通道）最小→泵最大、最大→泵最小（默认反相关）；"
             "invert 可反转为正相关。src_min/max 与 pump_max/min 可对任意工程刻度。",
        src_type_hint="function_1",
        dst_type_hint="pump",
        params_def=[
            {"key": "src_min",  "label": "电机最小端", "min": 0, "max": 255, "default": 0,   "scale": 1.0, "unit": ""},
            {"key": "src_max",  "label": "电机最大端", "min": 0, "max": 255, "default": 255, "scale": 1.0, "unit": ""},
            {"key": "pump_max", "label": "泵最大值",   "min": 0, "max": 255, "default": 255, "scale": 1.0, "unit": ""},
            {"key": "pump_min", "label": "泵最小值",   "min": 0, "max": 255, "default": 0,   "scale": 1.0, "unit": ""},
            {"key": "invert",   "label": "反转关系",   "min": 0, "max": 1,   "default": 0,   "scale": 1.0, "unit": ""},
        ],
        fn=_vario_pump_linear,
    ),
    LinkPresetDef(
        id="vario_pump_curve",
        name="百变→水（非线性·欧姆刻度）",
        category="百变→水",
        desc="同默认反相关，但按 gamma 非均匀分布行程：gamma<1 抬升前段，"
             "补偿泵对小 DMX 值迟钝（小值不敏感、大值才明显）；invert 可反转。",
        src_type_hint="function_1",
        dst_type_hint="pump",
        params_def=[
            {"key": "src_min",  "label": "电机最小端", "min": 0,   "max": 255, "default": 0,   "scale": 1.0,  "unit": ""},
            {"key": "src_max",  "label": "电机最大端", "min": 0,   "max": 255, "default": 255, "scale": 1.0,  "unit": ""},
            {"key": "pump_max", "label": "泵最大值",   "min": 0,   "max": 255, "default": 255, "scale": 1.0,  "unit": ""},
            {"key": "pump_min", "label": "泵最小值",   "min": 0,   "max": 255, "default": 0,   "scale": 1.0,  "unit": ""},
            {"key": "gamma",    "label": "曲线指数",   "min": 30,  "max": 300, "default": 50,  "scale": 0.01, "unit": ""},
            {"key": "invert",   "label": "反转关系",   "min": 0,   "max": 1,   "default": 0,   "scale": 1.0,  "unit": ""},
        ],
        fn=_vario_pump_curve,
    ),

    # ── 电机→泵 ──────────────────────────────────────────────────────────
    LinkPresetDef(
        id="motor_pump_cosine",
        name="电机→泵（余弦衰减）",
        category="电机→泵",
        desc="电机归中时泵最大，偏离截止角度后泵归零，余弦平方曲线",
        src_type_hint="motor_x",
        dst_type_hint="pump",
        params_def=[
            {"key": "center",   "label": "归中值",   "min": 0,   "max": 255, "default": 128, "scale": 1.0, "unit": ""},
            {"key": "cutoff",   "label": "截止偏差", "min": 1,   "max": 127, "default": 64,  "scale": 1.0, "unit": ""},
            {"key": "max_pump", "label": "最大泵值", "min": 0,   "max": 255, "default": 255, "scale": 1.0, "unit": ""},
        ],
        fn=_motor_pump_cosine,
    ),
    LinkPresetDef(
        id="motor_pump_linear",
        name="电机→泵（线性衰减）",
        category="电机→泵",
        desc="电机归中时泵最大，偏离截止角度后泵线性衰减至零",
        src_type_hint="motor_x",
        dst_type_hint="pump",
        params_def=[
            {"key": "center",   "label": "归中值",   "min": 0,   "max": 255, "default": 128, "scale": 1.0, "unit": ""},
            {"key": "cutoff",   "label": "截止偏差", "min": 1,   "max": 127, "default": 64,  "scale": 1.0, "unit": ""},
            {"key": "max_pump", "label": "最大泵值", "min": 0,   "max": 255, "default": 255, "scale": 1.0, "unit": ""},
        ],
        fn=_motor_pump_linear,
    ),
    LinkPresetDef(
        id="motor_pump_step",
        name="电机→泵（阶跃）",
        category="电机→泵",
        desc="电机在截止范围内泵全开，超出后泵全关",
        src_type_hint="motor_x",
        dst_type_hint="pump",
        params_def=[
            {"key": "center",   "label": "归中值",   "min": 0,   "max": 255, "default": 128, "scale": 1.0, "unit": ""},
            {"key": "cutoff",   "label": "截止偏差", "min": 1,   "max": 127, "default": 64,  "scale": 1.0, "unit": ""},
            {"key": "max_pump", "label": "最大泵值", "min": 0,   "max": 255, "default": 255, "scale": 1.0, "unit": ""},
        ],
        fn=_motor_pump_step,
    ),
    LinkPresetDef(
        id="motor_pump_gaussian",
        name="电机→泵（高斯曲线）",
        category="电机→泵",
        desc="高斯分布衰减，中心最大，两侧平滑衰减，无硬截止",
        src_type_hint="motor_x",
        dst_type_hint="pump",
        params_def=[
            {"key": "center",   "label": "归中值",   "min": 0,   "max": 255, "default": 128, "scale": 1.0, "unit": ""},
            {"key": "sigma",    "label": "标准差σ",  "min": 1,   "max": 127, "default": 40,  "scale": 1.0, "unit": ""},
            {"key": "max_pump", "label": "最大泵值", "min": 0,   "max": 255, "default": 255, "scale": 1.0, "unit": ""},
        ],
        fn=_motor_pump_gaussian,
    ),
    LinkPresetDef(
        id="motor_pump_bidir_pos",
        name="电机→泵（双向正向）",
        category="电机→泵",
        desc="电机右偏时激活此泵，左偏无输出（配合负向泵使用）",
        src_type_hint="motor_x",
        dst_type_hint="pump",
        params_def=[
            {"key": "center",   "label": "归中值",   "min": 0,   "max": 255, "default": 128, "scale": 1.0, "unit": ""},
            {"key": "cutoff",   "label": "最大偏差", "min": 1,   "max": 127, "default": 64,  "scale": 1.0, "unit": ""},
            {"key": "max_pump", "label": "最大泵值", "min": 0,   "max": 255, "default": 255, "scale": 1.0, "unit": ""},
        ],
        fn=_motor_pump_bidir,
    ),
    LinkPresetDef(
        id="motor_pump_bidir_neg",
        name="电机→泵（双向负向）",
        category="电机→泵",
        desc="电机左偏时激活此泵，右偏无输出（配合正向泵使用）",
        src_type_hint="motor_x",
        dst_type_hint="pump",
        params_def=[
            {"key": "center",   "label": "归中值",   "min": 0,   "max": 255, "default": 128, "scale": 1.0, "unit": ""},
            {"key": "cutoff",   "label": "最大偏差", "min": 1,   "max": 127, "default": 64,  "scale": 1.0, "unit": ""},
            {"key": "max_pump", "label": "最大泵值", "min": 0,   "max": 255, "default": 255, "scale": 1.0, "unit": ""},
        ],
        fn=_motor_pump_bidir_neg,
    ),
    # ── 电机→阀门 ────────────────────────────────────────────────────────
    LinkPresetDef(
        id="motor_valve_linear",
        name="电机→阀门（线性映射）",
        category="电机→阀门",
        desc="电机偏差线性映射到阀门开度，可设反向",
        src_type_hint="motor_x",
        dst_type_hint="valve",
        params_def=[
            {"key": "center",  "label": "归中值",   "min": 0,   "max": 255, "default": 128, "scale": 1.0, "unit": ""},
            {"key": "scale",   "label": "映射系数", "min": -500, "max": 500, "default": 200, "scale": 0.01, "unit": ""},
            {"key": "base",    "label": "基础阀值", "min": 0,   "max": 255, "default": 128, "scale": 1.0, "unit": ""},
            {"key": "invert",  "label": "反向(0/1)","min": 0,   "max": 1,   "default": 0,   "scale": 1.0, "unit": ""},
        ],
        fn=_motor_valve_linear,
    ),
    LinkPresetDef(
        id="motor_valve_cosine",
        name="电机→阀门（余弦，归中最大）",
        category="电机→阀门",
        desc="电机归中时阀门全开，偏离截止角度后阀门关闭",
        src_type_hint="motor_x",
        dst_type_hint="valve",
        params_def=[
            {"key": "center",  "label": "归中值",   "min": 0,   "max": 255, "default": 128, "scale": 1.0, "unit": ""},
            {"key": "cutoff",  "label": "截止偏差", "min": 1,   "max": 127, "default": 64,  "scale": 1.0, "unit": ""},
            {"key": "max_val", "label": "最大阀值", "min": 0,   "max": 255, "default": 255, "scale": 1.0, "unit": ""},
        ],
        fn=_motor_valve_cosine,
    ),

    # ── 泵→灯光 ──────────────────────────────────────────────────────────
    LinkPresetDef(
        id="pump_dimmer_linear",
        name="泵→亮度（线性）",
        category="泵→灯光",
        desc="泵强度线性映射到灯光亮度（水柱越高灯越亮）",
        src_type_hint="pump",
        dst_type_hint="dimmer",
        params_def=[
            {"key": "scale",  "label": "系数",   "min": 0,   "max": 200, "default": 100, "scale": 0.01, "unit": ""},
            {"key": "offset", "label": "偏移量", "min": -255, "max": 255, "default": 0,   "scale": 1.0,  "unit": ""},
        ],
        fn=_pump_dimmer_linear,
    ),
    LinkPresetDef(
        id="pump_dimmer_sqrt",
        name="泵→亮度（平方根）",
        category="泵→灯光",
        desc="低泵值时亮度较高，高泵值时亮度增长放缓",
        src_type_hint="pump",
        dst_type_hint="dimmer",
        params_def=[
            {"key": "scale", "label": "系数", "min": 0, "max": 200, "default": 100, "scale": 0.01, "unit": ""},
        ],
        fn=_pump_dimmer_sqrt,
    ),
    LinkPresetDef(
        id="pump_dimmer_power",
        name="泵→亮度（幂次）",
        category="泵→灯光",
        desc="幂次映射，γ>1 时低泵值亮度低，γ<1 时低泵值亮度高",
        src_type_hint="pump",
        dst_type_hint="dimmer",
        params_def=[
            {"key": "gamma", "label": "γ 指数", "min": 10, "max": 500, "default": 200, "scale": 0.01, "unit": ""},
        ],
        fn=_pump_dimmer_power,
    ),

    # ── 电机→颜色 ────────────────────────────────────────────────────────
    LinkPresetDef(
        id="motor_color_hue",
        name="电机→颜色（色相映射）",
        category="电机→颜色",
        desc="电机角度映射到颜色通道，配合 R/G/B 三条规则实现彩虹效果",
        src_type_hint="motor_x",
        dst_type_hint="R",
        params_def=[
            {"key": "center", "label": "归中值",   "min": 0,   "max": 255, "default": 128, "scale": 1.0, "unit": ""},
            {"key": "scale",  "label": "角度系数", "min": -500, "max": 500, "default": 100, "scale": 0.01, "unit": ""},
            {"key": "phase",  "label": "相位偏移", "min": 0,   "max": 360, "default": 0,   "scale": 1.0,  "unit": "°"},
        ],
        fn=_motor_color_hue,
    ),

    # ── 亮度→泵 ──────────────────────────────────────────────────────────
    LinkPresetDef(
        id="intensity_pump_linear",
        name="亮度→泵（线性）",
        category="亮度→泵",
        desc="灯光亮度通道驱动泵强度，亮度越高泵越强",
        src_type_hint="dimmer",
        dst_type_hint="pump",
        params_def=[
            {"key": "scale",  "label": "系数",   "min": 0,   "max": 200, "default": 100, "scale": 0.01, "unit": ""},
            {"key": "offset", "label": "偏移量", "min": -255, "max": 255, "default": 0,   "scale": 1.0,  "unit": ""},
        ],
        fn=_intensity_pump_linear,
    ),
    LinkPresetDef(
        id="intensity_pump_threshold",
        name="亮度→泵（阈值触发）",
        category="亮度→泵",
        desc="亮度超过阈值后才开泵，低于阈值泵为零",
        src_type_hint="dimmer",
        dst_type_hint="pump",
        params_def=[
            {"key": "threshold", "label": "触发阈值", "min": 0,   "max": 255, "default": 128, "scale": 1.0, "unit": ""},
            {"key": "max_pump",  "label": "最大泵值", "min": 0,   "max": 255, "default": 255, "scale": 1.0, "unit": ""},
        ],
        fn=_intensity_pump_threshold,
    ),

    # ── 通用变换 ──────────────────────────────────────────────────────────
    LinkPresetDef(
        id="passthrough",
        name="直通（缩放+偏移）",
        category="通用",
        desc="源值乘以系数加偏移后输出到目标",
        src_type_hint="",
        dst_type_hint="",
        params_def=[
            {"key": "scale",  "label": "系数",   "min": -500, "max": 500, "default": 100, "scale": 0.01, "unit": ""},
            {"key": "offset", "label": "偏移量", "min": -255, "max": 255, "default": 0,   "scale": 1.0,  "unit": ""},
        ],
        fn=_passthrough,
    ),
    LinkPresetDef(
        id="invert",
        name="反转（255 - 源值）",
        category="通用",
        desc="将源值反转后输出",
        src_type_hint="",
        dst_type_hint="",
        params_def=[],
        fn=_invert,
    ),
    LinkPresetDef(
        id="clamp_range",
        name="限幅到范围",
        category="通用",
        desc="将源值限制在指定范围内后输出",
        src_type_hint="",
        dst_type_hint="",
        params_def=[
            {"key": "lo", "label": "下限", "min": 0, "max": 255, "default": 0,   "scale": 1.0, "unit": ""},
            {"key": "hi", "label": "上限", "min": 0, "max": 255, "default": 255, "scale": 1.0, "unit": ""},
        ],
        fn=_clamp_range,
    ),
    LinkPresetDef(
        id="sine_modulate",
        name="正弦调制",
        category="通用",
        desc="以源值为相位，输出正弦波形（可用于周期性联动）",
        src_type_hint="",
        dst_type_hint="",
        params_def=[
            {"key": "freq",   "label": "频率",   "min": 1,   "max": 1000, "default": 100, "scale": 0.01, "unit": "Hz"},
            {"key": "depth",  "label": "深度",   "min": 0,   "max": 255,  "default": 64,  "scale": 1.0,  "unit": ""},
            {"key": "center", "label": "中心值", "min": 0,   "max": 255,  "default": 128, "scale": 1.0,  "unit": ""},
        ],
        fn=_sine_modulate,
    ),
    # ── 电机 XYZ 联动（motor_x/motor_y/motor_z 通道互相联动） ──────────────
    LinkPresetDef(
        id="motor_x_to_y_rotate",
        name="X→Y（平移联动旋转）",
        category="电机XYZ联动",
        desc="X 轴偏移按比例驱动 Y 轴，scale=1 时完全同步，scale<1 时衰减",
        src_type_hint="motor_x",
        dst_type_hint="motor_y",
        params_def=[
            {"key": "center", "label": "归中值", "min": 0, "max": 255, "default": 128, "scale": 1.0, "unit": ""},
            {"key": "scale",  "label": "联动系数", "min": -500, "max": 500, "default": 100, "scale": 0.01, "unit": ""},
        ],
        fn=_center_scale_map,
    ),
    LinkPresetDef(
        id="motor_y_to_x_mirror",
        name="Y→X（镜像联动）",
        category="电机XYZ联动",
        desc="Y 轴镜像到 X 轴（255-src），配合归中值微调镜像轴",
        src_type_hint="motor_y",
        dst_type_hint="motor_x",
        params_def=[
            {"key": "center", "label": "归中值", "min": 0, "max": 255, "default": 128, "scale": 1.0, "unit": ""},
        ],
        fn=_mirror_map,
    ),
    LinkPresetDef(
        id="motor_z_to_xy_sync",
        name="Z→XY（旋转驱动平移）",
        category="电机XYZ联动",
        desc="Z 轴旋转按比例同步驱动 X/Y 轴，实现三维联动",
        src_type_hint="motor_z",
        dst_type_hint="motor_x",
        params_def=[
            {"key": "center", "label": "归中值", "min": 0, "max": 255, "default": 128, "scale": 1.0, "unit": ""},
            {"key": "scale",  "label": "联动系数", "min": -500, "max": 500, "default": 100, "scale": 0.01, "unit": ""},
        ],
        fn=_center_scale_map,
    ),
    LinkPresetDef(
        id="motor_xy_to_z",
        name="XY→Z（归中抬升）",
        category="电机XYZ联动",
        desc="X/Y 偏离归中位越远 Z 越低（回归中心时 Z 最高）",
        src_type_hint="motor_x",
        dst_type_hint="motor_z",
        params_def=[
            {"key": "center", "label": "归中值", "min": 0, "max": 255, "default": 128, "scale": 1.0, "unit": ""},
            {"key": "cutoff", "label": "截止偏差", "min": 1, "max": 127, "default": 64, "scale": 1.0, "unit": ""},
            {"key": "max",    "label": "最大Z值", "min": 0, "max": 255, "default": 255, "scale": 1.0, "unit": ""},
        ],
        fn=_deviation_map,
    ),

    # ── 颜色联动（dimmer/R/G/B/W 通道互相联动） ────────────────────────────
    LinkPresetDef(
        id="dimmer_to_rgb_mix",
        name="亮度→颜色（强度混合）",
        category="颜色联动",
        desc="亮度强度按比例映射到颜色通道，scale/offset 控制混合比例",
        src_type_hint="dimmer",
        dst_type_hint="R",
        params_def=[
            {"key": "scale",  "label": "系数",   "min": 0,   "max": 200, "default": 100, "scale": 0.01, "unit": ""},
            {"key": "offset", "label": "偏移量", "min": -255, "max": 255, "default": 0,   "scale": 1.0,  "unit": ""},
        ],
        fn=_channel_linear,
    ),
    LinkPresetDef(
        id="r_to_gb_tint",
        name="红→绿/蓝（暖色调）",
        category="颜色联动",
        desc="红色强度驱动绿/蓝通道，实现暖色渐变（两条规则分别配 G/B）",
        src_type_hint="R",
        dst_type_hint="G",
        params_def=[
            {"key": "scale",  "label": "系数",   "min": 0,   "max": 200, "default": 60,  "scale": 0.01, "unit": ""},
            {"key": "offset", "label": "偏移量", "min": -255, "max": 255, "default": 0,   "scale": 1.0,  "unit": ""},
        ],
        fn=_channel_linear,
    ),
    LinkPresetDef(
        id="b_to_rg_depth",
        name="蓝→红/绿（冷色调）",
        category="颜色联动",
        desc="蓝色强度驱动红/绿通道，实现冷色渐变（两条规则分别配 R/G）",
        src_type_hint="B",
        dst_type_hint="R",
        params_def=[
            {"key": "scale",  "label": "系数",   "min": 0,   "max": 200, "default": 60,  "scale": 0.01, "unit": ""},
            {"key": "offset", "label": "偏移量", "min": -255, "max": 255, "default": 0,   "scale": 1.0,  "unit": ""},
        ],
        fn=_channel_linear,
    ),
    LinkPresetDef(
        id="dimmer_to_white",
        name="亮度→白（W 跟随）",
        category="颜色联动",
        desc="亮度通道同步驱动白通道，scale 控制白色占比",
        src_type_hint="dimmer",
        dst_type_hint="W",
        params_def=[
            {"key": "scale",  "label": "系数",   "min": 0,   "max": 200, "default": 100, "scale": 0.01, "unit": ""},
            {"key": "offset", "label": "偏移量", "min": -255, "max": 255, "default": 0,   "scale": 1.0,  "unit": ""},
        ],
        fn=_channel_linear,
    ),

    # ── 泵↔阀（pump 与 valve 通道互相联动） ───────────────────────────────
    LinkPresetDef(
        id="pump_to_valve_linear",
        name="泵→阀门（线性开度）",
        category="泵↔阀",
        desc="泵强度线性映射到阀门开度，泵越强阀开越大",
        src_type_hint="pump",
        dst_type_hint="valve",
        params_def=[
            {"key": "scale",  "label": "系数",   "min": 0,   "max": 200, "default": 100, "scale": 0.01, "unit": ""},
            {"key": "offset", "label": "偏移量", "min": -255, "max": 255, "default": 0,   "scale": 1.0,  "unit": ""},
        ],
        fn=_channel_linear,
    ),
    LinkPresetDef(
        id="pump_to_valve_pulse",
        name="泵→阀门（阈值脉冲）",
        category="泵↔阀",
        desc="泵超过阈值后按比例开阀，脉冲越高开度越大",
        src_type_hint="pump",
        dst_type_hint="valve",
        params_def=[
            {"key": "threshold", "label": "触发阈值", "min": 0, "max": 255, "default": 30, "scale": 1.0, "unit": ""},
            {"key": "max",       "label": "最大开度", "min": 0, "max": 255, "default": 255, "scale": 1.0, "unit": ""},
        ],
        fn=_threshold_map,
    ),
    LinkPresetDef(
        id="valve_to_pump_linear",
        name="阀门→泵（开阀供水）",
        category="泵↔阀",
        desc="阀门开度线性映射到泵强度，开阀越大供水越强",
        src_type_hint="valve",
        dst_type_hint="pump",
        params_def=[
            {"key": "scale",  "label": "系数",   "min": 0,   "max": 200, "default": 100, "scale": 0.01, "unit": ""},
            {"key": "offset", "label": "偏移量", "min": -255, "max": 255, "default": 0,   "scale": 1.0,  "unit": ""},
        ],
        fn=_channel_linear,
    ),

    # ── 电机↔亮度（motor 与 dimmer 通道互相联动） ─────────────────────────
    LinkPresetDef(
        id="motor_to_dimmer_follow",
        name="电机→亮度（位置跟随）",
        category="电机↔亮度",
        desc="电机位置按比例映射到亮度，scale 控制联动灵敏度",
        src_type_hint="motor_x",
        dst_type_hint="dimmer",
        params_def=[
            {"key": "center", "label": "归中值", "min": 0, "max": 255, "default": 128, "scale": 1.0, "unit": ""},
            {"key": "scale",  "label": "联动系数", "min": -500, "max": 500, "default": 100, "scale": 0.01, "unit": ""},
        ],
        fn=_center_scale_map,
    ),
    LinkPresetDef(
        id="motor_deviation_dimmer",
        name="电机偏差→亮度（越偏越亮）",
        category="电机↔亮度",
        desc="电机偏离归中位越远灯越亮，回归中心时熄灭（追光效果）",
        src_type_hint="motor_x",
        dst_type_hint="dimmer",
        params_def=[
            {"key": "center", "label": "归中值", "min": 0, "max": 255, "default": 128, "scale": 1.0, "unit": ""},
            {"key": "cutoff", "label": "截止偏差", "min": 1, "max": 127, "default": 64,  "scale": 1.0, "unit": ""},
            {"key": "max",    "label": "最大亮度", "min": 0, "max": 255, "default": 255, "scale": 1.0, "unit": ""},
        ],
        fn=_deviation_map,
    ),
    LinkPresetDef(
        id="dimmer_to_motor_home",
        name="亮度→电机（归中驱动）",
        category="电机↔亮度",
        desc="亮度超过阈值时驱动电机远离归中位，亮度低时归中",
        src_type_hint="dimmer",
        dst_type_hint="motor_x",
        params_def=[
            {"key": "threshold", "label": "触发阈值", "min": 0, "max": 255, "default": 60, "scale": 1.0, "unit": ""},
            {"key": "center",    "label": "归中值",   "min": 0, "max": 255, "default": 128, "scale": 1.0, "unit": ""},
            {"key": "max_speed", "label": "最大偏移", "min": 0, "max": 255, "default": 255, "scale": 1.0, "unit": ""},
        ],
        fn=_home_drive,
    ),

    # ── 泵→灯跟踪（整合 dimmer/R/G/B/W 五通道） ─────────────────────────────
    LinkPresetDef(
        id="pump_light_track",
        name="泵→灯跟踪（平滑）",
        category="泵→灯",
        desc=(
            "泵超过阈值时按选定颜色点亮灯光。\n"
            "支持 dimmer / R / G / B / W 五通道，\n"
            "目标通道类型自动匹配对应颜色值。\n"
            "平滑过渡区让开灯更自然。\n"
            "使用方法：源=pump，目标同时添加 dimmer/R/G/B/W 通道。"
        ),
        src_type_hint="pump",
        dst_type_hint="dimmer",
        params_def=[
            {"key": "threshold", "label": "触发阈值",    "min": 0,   "max": 255, "default": 30,  "scale": 1.0,  "unit": ""},
            {"key": "smooth",    "label": "平滑宽度",    "min": 0,   "max": 128, "default": 20,  "scale": 1.0,  "unit": ""},
            {"key": "gain",      "label": "增益",        "min": 0,   "max": 100, "default": 100, "scale": 0.01, "unit": ""},
            {"key": "dimmer",    "label": "Dimmer",      "min": 0,   "max": 255, "default": 255, "scale": 1.0,  "unit": ""},
            {"key": "R",         "label": "R（红）",     "min": 0,   "max": 255, "default": 255, "scale": 1.0,  "unit": ""},
            {"key": "G",         "label": "G（绿）",     "min": 0,   "max": 255, "default": 255, "scale": 1.0,  "unit": ""},
            {"key": "B",         "label": "B（蓝）",     "min": 0,   "max": 255, "default": 255, "scale": 1.0,  "unit": ""},
            {"key": "W",         "label": "W（白）",     "min": 0,   "max": 255, "default": 0,   "scale": 1.0,  "unit": ""},
        ],
        fn=_pump_light_track,
        dst_group="device_light",  # ★ 目标 = 该设备全部灯通道（用户定案）
    ),
    LinkPresetDef(
        id="pump_light_track_linear",
        name="泵→灯跟踪（线性）",
        category="泵→灯",
        desc=(
            "泵从阈值线性增长到最大值时点亮灯光。\n"
            "支持 dimmer / R / G / B / W 五通道。\n"
            "使用方法：源=pump，目标同时添加 dimmer/R/G/B/W 通道。"
        ),
        src_type_hint="pump",
        dst_type_hint="dimmer",
        params_def=[
            {"key": "threshold", "label": "触发阈值",    "min": 0,   "max": 255, "default": 30,  "scale": 1.0,  "unit": ""},
            {"key": "max_pump",  "label": "满量程泵值",  "min": 1,   "max": 255, "default": 255, "scale": 1.0,  "unit": ""},
            {"key": "gain",      "label": "增益",        "min": 0,   "max": 100, "default": 100, "scale": 0.01, "unit": ""},
            {"key": "dimmer",    "label": "Dimmer",      "min": 0,   "max": 255, "default": 255, "scale": 1.0,  "unit": ""},
            {"key": "R",         "label": "R（红）",     "min": 0,   "max": 255, "default": 255, "scale": 1.0,  "unit": ""},
            {"key": "G",         "label": "G（绿）",     "min": 0,   "max": 255, "default": 255, "scale": 1.0,  "unit": ""},
            {"key": "B",         "label": "B（蓝）",     "min": 0,   "max": 255, "default": 255, "scale": 1.0,  "unit": ""},
            {"key": "W",         "label": "W（白）",     "min": 0,   "max": 255, "default": 0,   "scale": 1.0,  "unit": ""},
        ],
        fn=_pump_light_track_linear,
        dst_group="device_light",  # ★ 目标 = 该设备全部灯通道（用户定案）
    ),
    LinkPresetDef(
        id="pump_light_track_step",
        name="泵→灯跟踪（阶跃）",
        category="泵→灯",
        desc=(
            "泵超过阈值立即全开灯，低于阈值立即全关。\n"
            "支持 dimmer / R / G / B / W 五通道。\n"
            "使用方法：源=pump，目标同时添加 dimmer/R/G/B/W 通道。"
        ),
        src_type_hint="pump",
        dst_type_hint="dimmer",
        params_def=[
            {"key": "threshold", "label": "触发阈值",    "min": 0,   "max": 255, "default": 30,  "scale": 1.0,  "unit": ""},
            {"key": "gain",      "label": "增益",        "min": 0,   "max": 100, "default": 100, "scale": 0.01, "unit": ""},
            {"key": "dimmer",    "label": "Dimmer",      "min": 0,   "max": 255, "default": 255, "scale": 1.0,  "unit": ""},
            {"key": "R",         "label": "R（红）",     "min": 0,   "max": 255, "default": 255, "scale": 1.0,  "unit": ""},
            {"key": "G",         "label": "G（绿）",     "min": 0,   "max": 255, "default": 255, "scale": 1.0,  "unit": ""},
            {"key": "B",         "label": "B（蓝）",     "min": 0,   "max": 255, "default": 255, "scale": 1.0,  "unit": ""},
            {"key": "W",         "label": "W（白）",     "min": 0,   "max": 255, "default": 0,   "scale": 1.0,  "unit": ""},
        ],
        fn=_pump_light_track_step,
        dst_group="device_light",  # ★ 目标 = 该设备全部灯通道（用户定案）
    ),
    LinkPresetDef(
        id="pump_light_track_pulse",
        name="泵→灯跟踪（脉冲窗口）",
        category="泵→灯",
        desc=(
            "泵在指定中心值±宽度范围内灯亮，超出范围灯灭。\n"
            "适合做'泵到特定位置时灯亮'的精确触发。\n"
            "支持 dimmer / R / G / B / W 五通道。\n"
            "使用方法：源=pump，目标同时添加 dimmer/R/G/B/W 通道。"
        ),
        src_type_hint="pump",
        dst_type_hint="dimmer",
        params_def=[
            {"key": "center",    "label": "触发中心值",  "min": 0,   "max": 255, "default": 200, "scale": 1.0,  "unit": ""},
            {"key": "width",     "label": "触发宽度",    "min": 1,   "max": 128, "default": 40,  "scale": 1.0,  "unit": ""},
            {"key": "gain",      "label": "增益",        "min": 0,   "max": 100, "default": 100, "scale": 0.01, "unit": ""},
            {"key": "dimmer",    "label": "Dimmer",      "min": 0,   "max": 255, "default": 255, "scale": 1.0,  "unit": ""},
            {"key": "R",         "label": "R（红）",     "min": 0,   "max": 255, "default": 255, "scale": 1.0,  "unit": ""},
            {"key": "G",         "label": "G（绿）",     "min": 0,   "max": 255, "default": 255, "scale": 1.0,  "unit": ""},
            {"key": "B",         "label": "B（蓝）",     "min": 0,   "max": 255, "default": 255, "scale": 1.0,  "unit": ""},
            {"key": "W",         "label": "W（白）",     "min": 0,   "max": 255, "default": 0,   "scale": 1.0,  "unit": ""},
        ],
        fn=_pump_light_track_pulse,
        dst_group="device_light",  # ★ 目标 = 该设备全部灯通道（用户定案）
    ),
]

# 按 ID 索引
PRESET_BY_ID: Dict[str, LinkPresetDef] = {p.id: p for p in BUILTIN_LINK_PRESETS}
_MOTOR_XY_PUMP_COMPAT_PRESET = LinkPresetDef(
    id="motor_xy_pump",
    name="电机XY偏离→泵下降（兼容）",
    category="电机→泵",
    desc="兼容旧项目；新建规则请使用普通电机→泵预设，源会自动补齐 X/Y。",
    src_type_hint="motor_x",
    dst_type_hint="pump",
    params_def=[
        {"key": "center",   "label": "归中值",   "min": 0, "max": 255, "default": 128, "scale": 1.0, "unit": ""},
        {"key": "cutoff",   "label": "最大偏差", "min": 1, "max": 180, "default": 90,  "scale": 1.0, "unit": ""},
        {"key": "max_pump", "label": "最大泵值", "min": 0, "max": 255, "default": 255, "scale": 1.0, "unit": ""},
    ],
    fn=_motor_xy_to_pump,
)
PRESET_BY_ID.setdefault("motor_xy_pump", _MOTOR_XY_PUMP_COMPAT_PRESET)
if not any(p.id == "motor_xy_pump" for p in BUILTIN_LINK_PRESETS):
    _insert_at = 0
    for _idx, _preset in enumerate(BUILTIN_LINK_PRESETS):
        if str(getattr(_preset, "category", "")) == "电机→泵":
            _insert_at = _idx + 1
    BUILTIN_LINK_PRESETS.insert(_insert_at, PRESET_BY_ID["motor_xy_pump"])

# ─────────────────────────────────────────────────────────────────────────────
#  关联规则数据模型
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RuleGroup:
    """规则分组（组条目）：一次批量生成 = 一个组，组内含全部通道映射规则。

    规则本身仍平铺在引擎 _rules 列表中（引擎消费不变），本组仅按 id 索引
    组内规则；组条目在 UI 中折叠/展开、整体编辑、删除（连同规则）。
    """
    id:        str = field(default_factory=lambda: str(uuid.uuid4()))
    name:      str = "未命名组"
    rule_ids:  List[str] = field(default_factory=list)   # 组内规则 id（有序）
    # ★ 2026-09-18 用户定案：**默认收起**（创建规则后不该默认展开整个列表 ——
    #   一次生成几百条映射时展开会把列表刷屏）。用户展开一次即持久化该状态。
    expanded:  bool = False                              # UI 折叠状态（持久化）

    def to_dict(self) -> dict:
        return {
            "id":       self.id,
            "name":     self.name,
            "rule_ids": list(self.rule_ids),
            "expanded": self.expanded,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RuleGroup":
        return cls(
            id=d.get("id", str(uuid.uuid4())),
            name=d.get("name", "未命名组"),
            rule_ids=list(d.get("rule_ids", [])),
            expanded=bool(d.get("expanded", False)),
        )


@dataclass
class ChannelRef:
    """对某个设备实例的某个通道类型的引用。"""
    fixture_id:  str = ""    # FixtureInstance.id
    ch_type:     str = ""    # ChannelType.value，如 "motor_x"
    universe:    int = 1
    dmx_addr:    int = 0     # 0-indexed DMX 地址（0~511）

    def to_dict(self) -> dict:
        return {
            "fixture_id": self.fixture_id,
            "ch_type":    self.ch_type,
            "universe":   self.universe,
            "dmx_addr":   self.dmx_addr,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChannelRef":
        return cls(
            fixture_id=d.get("fixture_id", ""),
            ch_type=d.get("ch_type", ""),
            universe=d.get("universe", 1),
            dmx_addr=d.get("dmx_addr", 0),
        )


@dataclass
class LinkRule:
    """一条关联规则：src → preset → dst。"""
    id:          str = field(default_factory=lambda: str(uuid.uuid4()))
    name:        str = "新规则"
    enabled:     bool = True

    # 源通道（可以是多个，取平均值）
    src_channels: List[ChannelRef] = field(default_factory=list)
    # 目标通道（可以是多个，同时写入）
    dst_channels: List[ChannelRef] = field(default_factory=list)

    # 关联预设
    preset_id:   str = "passthrough"
    params:      Dict[str, float] = field(default_factory=dict)

    # 混合模式：v1.2 起统一为低值覆盖（lo_override）——只减不加，
    # 作为已有目标值的遮罩；旧存档中的 override/add/multiply 值
    # 保留在字段中（序列化兼容），引擎一律按低值覆盖执行。
    blend_mode:  str = "lo_override"

    # 设备组标签（仅用于 UI 显示）
    group_label: str = ""

    # ★ 设备类型名（UI 按类型聚合分组用；引擎生成时填入源设备 def.name）
    type_name: str = ""

    # ── 时序控制（跟水参数） ─────────────────────────────────────────
    stagger_delay_ms: float = 0.0      # 时差：目标设备间的偏移延迟(ms)
    spacing_profile: str = "uniform"    # 紧距："uniform" | "linear_fade" | "gaussian"
    spray_delay_ms: float = 0.0        # 喷水延时：全局激活延迟(ms)

    def to_dict(self) -> dict:
        return {
            "id":           self.id,
            "name":         self.name,
            "enabled":      self.enabled,
            "src_channels": [c.to_dict() for c in self.src_channels],
            "dst_channels": [c.to_dict() for c in self.dst_channels],
            "preset_id":    self.preset_id,
            "params":       dict(self.params),
            "blend_mode":   self.blend_mode,
            "group_label":  self.group_label,
            "stagger_delay_ms": self.stagger_delay_ms,
            "spacing_profile":  self.spacing_profile,
            "spray_delay_ms":   self.spray_delay_ms,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LinkRule":
        r = cls(
            id=d.get("id", str(uuid.uuid4())),
            name=d.get("name", "规则"),
            enabled=d.get("enabled", True),
            preset_id=d.get("preset_id", "passthrough"),
            params=dict(d.get("params", {})),
            blend_mode=d.get("blend_mode", "lo_override"),
            group_label=d.get("group_label", ""),
            stagger_delay_ms=float(d.get("stagger_delay_ms", 0.0)),
            spacing_profile=str(d.get("spacing_profile", "uniform")),
            spray_delay_ms=float(d.get("spray_delay_ms", 0.0)),
        )
        r.src_channels = [ChannelRef.from_dict(c) for c in d.get("src_channels", [])]
        r.dst_channels = [ChannelRef.from_dict(c) for c in d.get("dst_channels", [])]
        return r


# ─────────────────────────────────────────────────────────────────────────────
#  TrackLinkerEngine
# ─────────────────────────────────────────────────────────────────────────────

class TrackLinkerEngine(QObject):
    """
    轨道关联器引擎。
    - 持有 LinkRule 列表
    - 每 25ms tick 一次，读取 NM 快照，计算关联值，写入 NM linker 层
    - 支持全局启用/停用
    """

    rules_changed = pyqtSignal()   # 规则列表变化时发射

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rules:   List[LinkRule] = []
        self._groups:  List[RuleGroup] = []
        self._enabled: bool = False
        self._nm = None   # NetworkManager 引用，延迟注入
        self._last_written_channels: Dict[int, Set[int]] = {}
        self._timer_suspend_count: int = 0
        # 上一轮 add_rules_to_type_group 因重复（同预设/同源/同目标）被跳过的规则
        self.last_group_add_skipped: List[LinkRule] = []

        # ── 时序控制状态 ─────────────────────────────────────────────
        # {rule_id: activation_timestamp} 记录每条规则源端最近一次激活的时间戳
        self._rule_activation_state: Dict[str, float] = {}
        # {f"{rule_id}:U{uni}:A{addr": last_write_timestamp} 记录每个目标通道最近触发时间
        self._device_activation_time: Dict[str, float] = {}

        # ── 热路径缓存（每 25ms tick 复用，规则变化时失效）──
        # 关联计算原来每 tick 都重建 enabled_rules 列表、重扫每条规则是否用电机源、
        # 并对每个电机-泵规则/fine 通道实时调 find_fixture_channel_addr(NM 遍历)。
        # 这些在规则不变、配接不变时全是常量，缓存后每 tick 只做真正随源值变化的计算。
        self._enabled_rules_cache: Optional[List[LinkRule]] = None
        self._uses_motion_cache: Optional[bool] = None
        # 所有 enabled 规则的源通道所在 universe 集合。关联只读源 universe 的混合值，
        # 目标 universe 只需写(稀疏输出)不需混合 → 把 _get_snapshot 限定到源 universe,
        # 避免每 tick 白白重混几十个与源无关的 universe(本项目 3 vs 25)。
        self._source_universes_cache: Optional[Set[int]] = None
        # (fixture_id, ch_type_key, universe) -> Optional[int] DMX 地址
        self._addr_resolve_cache: Dict[Tuple[str, str, Any], Optional[int]] = {}
        # 规则 id -> 该规则是否用电机源(规则/配接不变则恒定; 随 rules_changed 失效)
        self._uses_motion_by_rule_cache: Dict[str, bool] = {}
        # id(rule) -> (key_of, fine_of, src_channels, is_motor_pump_rule)
        # 规则静态派生数据; 规则/配接变化经 rules_changed → _invalidate_rule_caches 失效
        self._rule_prep_cache: Dict[int, tuple] = {}
        # 所有 enabled 规则的"读取"universe（源+目标）。_get_active_snapshot 只需
        # 计算这些域的活跃掩码（引擎只读 src/dst 通道的掩码），不用每 tick 全域重扫。
        self._read_universes_cache: Optional[Set[int]] = None

        self._timer = QTimer(self)
        # ★ PreciseTimer：Qt 默认 CoarseTimer 会把 interval 取整到
        #   15.625ms 倍数（25ms→31.68ms），主链路定时器必须精确。
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._tick)
        # 规则任何变更(增删改/清空/排序/加载)都经 rules_changed → 统一失效缓存
        self.rules_changed.connect(self._invalidate_rule_caches)

    def _invalidate_rule_caches(self):
        """规则或配接变化时清空热路径缓存，下一 tick 重建。"""
        self._enabled_rules_cache = None
        self._uses_motion_cache = None
        self._source_universes_cache = None
        self._read_universes_cache = None
        self._addr_resolve_cache = {}
        self._uses_motion_by_rule_cache.clear()
        self._rule_prep_cache.clear()
        self._rule_activation_state.clear()
        self._device_activation_time.clear()

    def _resolve_channel_addr_cached(
        self, fixture_id: Any, ch_type: str, universe: Any
    ) -> Optional[int]:
        """带缓存的 find_fixture_channel_addr：配接静态期间只查一次。"""
        key = (str(fixture_id), _channel_type_key(ch_type), universe)
        cache = self._addr_resolve_cache
        if key in cache:
            return cache[key]
        addr: Optional[int] = None
        try:
            if self._nm is not None and hasattr(self._nm, "find_fixture_channel_addr"):
                addr = self._nm.find_fixture_channel_addr(
                    fixture_id, ch_type, universe=universe,
                )
        except Exception:
            addr = None
        cache[key] = addr
        return addr

    # ── NM 注入 ──────────────────────────────────────────────────────────────

    def set_network_manager(self, nm):
        """注入 NetworkManager 实例。"""
        self._nm = nm
        # NM 换了(配接来源变了)→ 地址解析缓存作废
        self._invalidate_rule_caches()
        # 确保 NM 有 linker 层
        if nm is not None and hasattr(nm, '_layers'):
            if "linker" not in nm._layers:
                nm._layers["linker"] = {}
                # 同时注册到 BASE_LAYERS 优先级列表
                try:
                    from network.network_manager import BASE_LAYERS
                    if "linker" not in BASE_LAYERS:
                        BASE_LAYERS.append("linker")
                except Exception:
                    log.debug("track_linker: failed to register linker layer in BASE_LAYERS")
                    pass

    # ── 启停 ─────────────────────────────────────────────────────────────────

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        if enabled:
            if int(getattr(self, "_timer_suspend_count", 0) or 0) <= 0:
                self._timer.start()
        else:
            self._timer.stop()
            self._clear_linker_layer()

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def suspend_timer(self):
        self._timer_suspend_count = max(0, int(getattr(self, "_timer_suspend_count", 0) or 0)) + 1
        self._timer.stop()

    def resume_timer(self):
        count = max(0, int(getattr(self, "_timer_suspend_count", 0) or 0))
        self._timer_suspend_count = max(0, count - 1)
        if self._enabled and self._timer_suspend_count <= 0:
            self._timer.start()

    def _clear_linker_layer(self):
        """清空 linker 层。"""
        self._last_written_channels = {}
        if self._nm is not None and hasattr(self._nm, 'clear_layer'):
            try:
                self._nm.clear_layer("linker")
            except Exception:
                log.debug("track_linker: failed to clear linker layer")
                pass

    # ── 分组（组条目）管理 ───────────────────────────────────────────────────
    # 每个设备组条目 = 一个 RuleGroup，组内含该组全部通道映射规则（rule_ids）。
    # 规则本身仍在 _rules 平铺列表中（引擎逐条消费不变），组仅做索引/收纳。

    def find_group_by_name(self, name: str) -> Optional[RuleGroup]:
        """按名称查组条目（同名取首个）。"""
        key = str(name or "").strip()
        if not key:
            return None
        return next((g for g in self._groups if g.name == key), None)

    def add_rules_to_type_group(self, rules: List[LinkRule], group_name: str) -> str:
        """把规则加入「按设备类型」的组条目（不存在则新建），返回组 id。

        ★ 用户定案（2026-09-17）：
          ① 批量生成后不再一台设备一行平铺，而是**按一类设备聚合**成一个组条目
             （与调度台同观感：组名=设备类型名），展开才看该类型下的各条预设映射；
          ② 同一类型**可叠加多个不冲突的预设**（如先「XY 偏离→泵」，再加
             「电机→颜色」）：两者都进同一组，各自保留独立参数，互不覆盖。
          组名用设备类型名；同名组存在则**追加**（叠加语义），否则新建。

        幂等保护：与组内已有规则**完全同源同目标同预设**的重复批次会被跳过
        （防手滑重复生成导致同一条映射写两遍），跳过的规则存
        ``self.last_group_add_skipped`` 供 UI 提示；返回值恒为组 id（空规则→""）。
        """
        self.last_group_add_skipped = []
        if not rules:
            return ""
        group = self.find_group_by_name(group_name)
        if group is None:
            group = self.add_group(group_name)
        keep, skipped = _dedupe_against_group(
            group, rules, self.get_rules_in_group(group.id))
        if skipped:
            self.last_group_add_skipped = skipped
            log.info("关联器：类型组「%s」跳过 %d 条重复映射",
                     group_name, len(skipped))
        if keep:
            self.add_rules(keep, group_id=group.id)
        return group.id

    def overlapping_dst_in_group(self, group_name: str,
                                 rules: List[LinkRule]) -> set:
        """本批规则目标通道与**同名组内已有规则**重叠的通道键集合。

        用于批量生成后的"叠加不冲突"提示：同一目标通道被两种预设同时写
        （如「电机→颜色」的 RGBW 与另一预设的 dimmer）语义上可能冲突，
        面板据此在状态栏给出警示（不阻断——低值覆盖本身允许多源混合）。
        """
        group = self.find_group_by_name(group_name)
        if group is None:
            return set()
        have = {(ref.universe, ref.dmx_addr)
                for rule in self.get_rules_in_group(group.id)
                for ref in rule.dst_channels}
        return {(ref.universe, ref.dmx_addr)
                for rule in rules for ref in rule.dst_channels} & have

    def get_groups(self) -> List[RuleGroup]:
        return list(self._groups)

    def add_group(self, name: str, rule_ids: Optional[List[str]] = None) -> RuleGroup:
        """创建空组条目（不发射 rules_changed，由随后的 add_rules 触发刷新）。"""
        group = RuleGroup(name=name, rule_ids=list(rule_ids or []))
        self._groups.append(group)
        return group

    def rename_group(self, group_id: str, new_name: str):
        name = str(new_name or "").strip()
        if not name:
            return
        for g in self._groups:
            if g.id == group_id:
                g.name = name
                self.rules_changed.emit()
                return

    def remove_group(self, group_id: str, delete_rules: bool = False):
        """删除组条目；delete_rules=True 时连同组内规则一并删除。"""
        group = next((g for g in self._groups if g.id == group_id), None)
        if group is None:
            return
        self._groups = [g for g in self._groups if g.id != group_id]
        if delete_rules:
            removed = set(group.rule_ids)
            self._rules = [r for r in self._rules if r.id not in removed]
            self._clear_stale_linker_outputs({})
        self.rules_changed.emit()

    def set_group_expanded(self, group_id: str, expanded: bool):
        for g in self._groups:
            if g.id == group_id:
                g.expanded = bool(expanded)
                self.rules_changed.emit()
                return

    def get_rules_in_group(self, group_id: str) -> List[LinkRule]:
        group = next((g for g in self._groups if g.id == group_id), None)
        if group is None:
            return []
        by_id = {r.id: r for r in self._rules}
        return [by_id[rid] for rid in group.rule_ids if rid in by_id]

    def get_group_id_of_rule(self, rule_id: str) -> Optional[str]:
        for g in self._groups:
            if rule_id in g.rule_ids:
                return g.id
        return None

    def update_rules(self, rules: List[LinkRule]):
        """批量更新多条规则（组条目整体编辑）：一次发射 rules_changed。"""
        if not rules:
            return
        by_id = {r.id: r for r in rules}
        for i, r in enumerate(self._rules):
            if r.id in by_id:
                self._rules[i] = by_id[r.id]
        self._clear_stale_linker_outputs({})
        self.rules_changed.emit()

    # ── 规则管理 ─────────────────────────────────────────────────────────────

    def add_rule(self, rule: LinkRule, group_id: Optional[str] = None):
        self._rules.append(rule)
        if group_id:
            group = next((g for g in self._groups if g.id == group_id), None)
            if group is not None:
                group.rule_ids.append(rule.id)
        self.rules_changed.emit()

    def add_rules(self, rules: List[LinkRule], group_id: Optional[str] = None):
        """批量添加规则：一次性发射 rules_changed，避免 N 条规则 N 次全量刷新。
        group_id 非 None 时规则归入该组条目。
        """
        if not rules:
            return
        self._rules.extend(rules)
        if group_id:
            group = next((g for g in self._groups if g.id == group_id), None)
            if group is not None:
                group.rule_ids.extend(r.id for r in rules)
        self.rules_changed.emit()

    def remove_rule(self, rule_id: str):
        self._rules = [r for r in self._rules if r.id != rule_id]
        for g in self._groups:
            if rule_id in g.rule_ids:
                g.rule_ids = [rid for rid in g.rule_ids if rid != rule_id]
        self._clear_stale_linker_outputs({})
        self.rules_changed.emit()

    def update_rule(self, rule: LinkRule):
        for i, r in enumerate(self._rules):
            if r.id == rule.id:
                self._rules[i] = rule
                self._clear_stale_linker_outputs({})
                self.rules_changed.emit()
                return

    def get_rules(self) -> List[LinkRule]:
        return list(self._rules)

    def has_enabled_rules(self) -> bool:
        return any(bool(getattr(rule, "enabled", False)) for rule in self._rules)

    def clear_rules(self):
        self._rules.clear()
        self._groups.clear()
        self._clear_linker_layer()
        self.rules_changed.emit()

    def move_rule_up(self, rule_id: str):
        for i, r in enumerate(self._rules):
            if r.id == rule_id and i > 0:
                self._rules[i], self._rules[i - 1] = self._rules[i - 1], self._rules[i]
                # 同步组内顺序，保证 UI 显示顺序与引擎处理顺序一致
                for g in self._groups:
                    if rule_id in g.rule_ids:
                        gi = g.rule_ids.index(rule_id)
                        if gi > 0:
                            g.rule_ids[gi], g.rule_ids[gi - 1] = (
                                g.rule_ids[gi - 1], g.rule_ids[gi])
                        break
                self.rules_changed.emit()
                return

    def move_rule_down(self, rule_id: str):
        for i, r in enumerate(self._rules):
            if r.id == rule_id and i < len(self._rules) - 1:
                self._rules[i], self._rules[i + 1] = self._rules[i + 1], self._rules[i]
                for g in self._groups:
                    if rule_id in g.rule_ids:
                        gi = g.rule_ids.index(rule_id)
                        if gi < len(g.rule_ids) - 1:
                            g.rule_ids[gi], g.rule_ids[gi + 1] = (
                                g.rule_ids[gi + 1], g.rule_ids[gi])
                        break
                self.rules_changed.emit()
                return

    # ── 核心计算 ─────────────────────────────────────────────────────────────

    def process_once(self, exclude_layers: Optional[Set[str]] = None, extra_sources: Optional[dict] = None):
        """Synchronously compute linker output once.

        Recording uses this before sampling the editor mix so the "linker" layer
        is current even if the QTimer has not fired in this UI tick.
        """
        self._process_once(exclude_layers=exclude_layers, extra_sources=extra_sources)

    def _tick(self):
        """每 25ms 执行一次关联计算并写入 NM linker 层。"""
        self._process_once()

    def _process_once(self, exclude_layers: Optional[Set[str]] = None, extra_sources: Optional[dict] = None):
        if not self._enabled or self._nm is None:
            return
        if not self._rules:
            self._clear_linker_layer()
            return
        # 缓存 enabled_rules（规则不变时每 tick 免去列表重建）
        enabled_rules = self._enabled_rules_cache
        if enabled_rules is None:
            enabled_rules = [
                rule for rule in self._rules
                if bool(getattr(rule, "enabled", False))
            ]
            self._enabled_rules_cache = enabled_rules
            # 是否有规则用电机源也一并缓存（规则不变则恒定）
            self._uses_motion_cache = any(
                self._rule_uses_motion_source(rule) for rule in enabled_rules
            )
            # 收集所有规则源通道的 universe（只混这些即可读到源值）
            src_unis: Set[int] = set()
            for rule in enabled_rules:
                for ref in (getattr(rule, "src_channels", []) or []):
                    try:
                        src_unis.add(int(getattr(ref, "universe", 1) or 1))
                    except Exception:
                        log.debug("track_linker: failed to add source universe")
                        pass
            self._source_universes_cache = src_unis
            # 收集所有规则的"读取"universe：源通道 + 目标通道。_write_dst 的低值
            # 覆盖判定要读目标通道的活跃掩码，active 扫描必须覆盖目标域。
            read_unis = set(src_unis)
            for rule in enabled_rules:
                for ref in (getattr(rule, "dst_channels", []) or []):
                    try:
                        read_unis.add(int(getattr(ref, "universe", 1) or 1))
                    except Exception:
                        log.debug("track_linker: failed to add destination universe")
                        pass
            self._read_universes_cache = read_unis
        if not enabled_rules:
            self._clear_stale_linker_outputs({})
            return

        excluded = set(exclude_layers or set())
        excluded.add("linker")

        # 只混合源 universe：目标 universe 只写不读，无需参与 mix。
        # extra_sources 可能引入额外 universe（如摇杆/音频临时源）→ 并入。
        # ★ 2026-08-12 热点#20 实测(probe_rec_perf_20): 录制轮询的 extra_sources
        #   = 全部 record 源域(「允许接受输出」clip 常铺满数十域), 整体并入 mix
        #   集 = 每 tick 全量混合规则永不读取的域(25 域实测 ~3ms/tick)。规则只
        #   读 src/dst 通道所在域(_read_universes_cache): 仅并入被引用的 extra
        #   域, 其余 extra 域由 _overlay_extra_sources 补零帧+数据(键集合不变,
        #   无人消费), 规则读值逐字节一致。
        limit_unis: Optional[Set[int]] = None
        src_uni_set = self._read_universes_cache
        if src_uni_set is not None:
            limit_unis = set(src_uni_set)
            if extra_sources:
                limit_unis.update(self._limited_extra_source_universes(extra_sources))

        # 获取当前 NM 混合输出快照（universe → uint8[512]）
        snapshot = self._get_snapshot(excluded, limit_universes=limit_unis)
        snapshot = self._overlay_extra_sources(snapshot, extra_sources)
        if not snapshot:
            self._clear_stale_linker_outputs({})
            return
        # active 扫描限定到规则读取的 universe（源+目标+临时源），避免每 tick
        # 对全部 universe 重算掩码（大工程数十域，关联器只读其中几个）。掩码内容
        # 与扫描范围无关，引擎只按 src/dst 通道的 universe 查掩码，语义不变。
        # extra 域同样只并入被规则引用的部分（未被引用的掩码无人查询）。
        scan_unis = self._read_universes_cache
        if scan_unis is not None:
            scan_unis = set(scan_unis)
            if extra_sources:
                scan_unis.update(self._limited_extra_source_universes(extra_sources))
        source_active = self._get_active_snapshot(excluded, universes=scan_unis)
        source_active = self._overlay_extra_source_masks(source_active, extra_sources)

        motion_snapshot = snapshot
        motion_active = source_active
        if bool(self._uses_motion_cache):
            motion_excluded = set(excluded)
            motion_excluded.update(STATIC_EDITOR_LAYERS)
            # 动效掩码只用于源通道活性判定 → 扫描源域即可
            motion_scan = self._source_universes_cache
            if motion_scan is not None:
                motion_scan = set(motion_scan)
                if extra_sources:
                    motion_scan.update(self._limited_extra_source_universes(extra_sources))
                motion_active = self._get_active_snapshot(motion_excluded, universes=motion_scan)
            else:
                motion_active = self._get_active_snapshot(motion_excluded)
            motion_active = self._overlay_extra_source_masks(motion_active, extra_sources)
            if self._rules_have_active_motion_source(enabled_rules, motion_active):
                motion_snapshot = self._get_snapshot(motion_excluded, limit_universes=limit_unis)
                motion_snapshot = self._overlay_extra_sources(motion_snapshot, extra_sources)

        # 电机"停放"掩码（低值覆盖不得遮停放电机 → 泵被消掉的系统性根因，见
        # _write_dst 的 _rule_motors_parked 分支）。判据与既有两张掩码同源、零额外
        # 混合开销：
        #   parked = source_active（除 linker 外任意层 active）
        #            & ~motion_active（去除静态编辑器层后的动效 active）
        # 即：只有静态编辑器层（programmer/fader/tester）写值、无 clip/effect 运动。
        parked_src: dict[int, np.ndarray] | None = None
        if bool(self._uses_motion_cache):
            parked_src = {}
            for uni, mask in (source_active or {}).items():
                m = (motion_active or {}).get(uni)
                parked_src[int(uni)] = (
                    np.asarray(mask, dtype=bool)
                    if m is None
                    else (np.asarray(mask, dtype=bool) & ~np.asarray(m, dtype=bool))
                )

        # 按 universe 收集稀疏写入数据
        # {universe: {dmx_addr: value}}
        writes: Dict[int, Dict[int, int]] = {}

        for rule in enabled_rules:
            try:
                self._process_rule(
                    rule, snapshot, writes,
                    motion_snapshot=motion_snapshot,
                    source_active=source_active,
                    motion_active=motion_active,
                    parked_src=parked_src,
                )
            except Exception as e:
                log.debug("LinkRule %s 计算异常: %s", rule.id, e)

        # 写入 NM linker 层
        writes = {int(uni): ch_map for uni, ch_map in writes.items() if ch_map}
        self._clear_stale_linker_outputs(writes)
        if writes:
            try:
                if hasattr(self._nm, "update_layer_sparse_batch"):
                    self._nm.update_layer_sparse_batch("linker", writes)
                else:
                    for uni, ch_map in writes.items():
                        self._nm.update_layer_sparse("linker", uni, ch_map)
            except Exception as e:
                log.debug("写入 linker 层失败: %s", e)

    @staticmethod
    def _rule_uses_motion_source(rule: LinkRule) -> bool:
        motion_types = {
            "motor_x", "motor_y", "motor_z",
            "motor_x_fine", "motor_y_fine", "motor_z_fine", "motor_xy",
        }
        for ref in getattr(rule, "src_channels", []) or []:
            if _channel_type_key(getattr(ref, "ch_type", "")) in motion_types:
                return True
        return False

    def _rules_have_active_motion_source(
        self,
        rules: List[LinkRule],
        active_snapshot: Optional[Dict[int, np.ndarray]],
    ) -> bool:
        if not active_snapshot:
            return False
        motion_cache = self._uses_motion_by_rule_cache
        for rule in rules:
            uses_motion = motion_cache.get(rule.id)
            if uses_motion is None:
                uses_motion = self._rule_uses_motion_source(rule)
                motion_cache[rule.id] = uses_motion
            if not uses_motion:
                continue
            for ref in getattr(rule, "src_channels", []) or []:
                mask = (active_snapshot or {}).get(getattr(ref, "universe", 1))
                if mask is None:
                    continue
                try:
                    addr = int(getattr(ref, "dmx_addr", -1))
                except Exception:
                    continue
                if 0 <= addr < 512 and bool(mask[addr]):
                    return True
                if np.any(mask):
                    return True
        return False

    @staticmethod
    def _coerce_record_source_entry(entry) -> Tuple[np.ndarray, np.ndarray]:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            data_raw, active_raw = entry[0], entry[1]
        else:
            data_raw, active_raw = entry, None

        data = np.asarray(data_raw, dtype=np.uint8).reshape(-1)
        if data.shape[0] != 512:
            fixed = np.zeros(512, dtype=np.uint8)
            n = min(int(data.shape[0]), 512)
            if n > 0:
                fixed[:n] = data[:n]
            data = fixed

        if active_raw is None:
            active = data != 0
        else:
            active = np.asarray(active_raw, dtype=bool).reshape(-1)
            if active.shape[0] != 512:
                fixed_active = np.zeros(512, dtype=bool)
                n = min(int(active.shape[0]), 512)
                if n > 0:
                    fixed_active[:n] = active[:n]
                active = fixed_active
        return data, active

    def _overlay_extra_sources(self, snapshot: Dict[int, np.ndarray], extra_sources: Optional[dict]) -> Dict[int, np.ndarray]:
        """Overlay explicitly opt-in clip sources onto the linker input snapshot."""
        if not extra_sources:
            return snapshot

        out = {
            int(uni): np.asarray(data, dtype=np.uint8).copy()
            for uni, data in (snapshot or {}).items()
        }
        for uni, entry in (extra_sources or {}).items():
            try:
                uni_i = int(uni)
                data, active = self._coerce_record_source_entry(entry)
            except Exception:
                continue
            if not np.any(active):
                continue
            base = out.get(uni_i)
            if base is None:
                base = np.zeros(512, dtype=np.uint8)
                out[uni_i] = base
            base[active] = data[active]
        return out

    def _overlay_extra_source_masks(self, masks: Dict[int, np.ndarray], extra_sources: Optional[dict]) -> Dict[int, np.ndarray]:
        """Overlay active masks for explicitly opt-in clip sources."""
        if not extra_sources:
            return masks

        out = {
            int(uni): np.asarray(mask, dtype=bool).copy()
            for uni, mask in (masks or {}).items()
        }
        for uni, entry in (extra_sources or {}).items():
            try:
                uni_i = int(uni)
                _data, active = self._coerce_record_source_entry(entry)
            except Exception:
                continue
            if not np.any(active):
                continue
            base = out.get(uni_i)
            if base is None:
                base = np.zeros(512, dtype=bool)
                out[uni_i] = base
            base |= active
        return out

    def _clear_stale_linker_outputs(self, writes: Dict[int, Dict[int, int]]):
        """Release linker channels that were written by old/disabled rules."""
        current: Dict[int, Set[int]] = {}
        for uni, ch_map in (writes or {}).items():
            try:
                uni_i = int(uni)
            except Exception:
                uni_i = 1
            addrs: Set[int] = set()
            for addr in (ch_map or {}).keys():
                try:
                    addr_i = int(addr)
                except Exception:
                    continue
                if 0 <= addr_i < 512:
                    addrs.add(addr_i)
            if addrs:
                current[uni_i] = addrs
        stale_by_uni: Dict[int, Set[int]] = {}
        last_written = self._normalize_written_channels(
            getattr(self, "_last_written_channels", {}) or {})
        for uni, old_addrs in list(last_written.items()):
            stale = set(old_addrs) - set(current.get(int(uni), set()))
            if stale:
                stale_by_uni[int(uni)] = stale
        if stale_by_uni and self._nm is not None:
            for uni, addrs in stale_by_uni.items():
                try:
                    if hasattr(self._nm, "clear_layer_channels"):
                        self._nm.clear_layer_channels("linker", int(uni), sorted(addrs))
                    elif hasattr(self._nm, "clear_layer"):
                        self._nm.clear_layer("linker", int(uni))
                except Exception:
                    log.debug("track_linker: failed to clear stale linker channels")
                    pass
        self._last_written_channels = current

    @staticmethod
    def _normalize_written_channels(raw) -> Dict[int, Set[int]]:
        """Normalize legacy linker write tracking into {universe: {addr}}."""
        out: Dict[int, Set[int]] = {}

        def _add(uni, addr):
            try:
                uni_i = int(uni)
                addr_i = int(addr)
            except Exception:
                return
            if 0 <= addr_i < 512:
                out.setdefault(uni_i, set()).add(addr_i)

        if isinstance(raw, dict):
            for uni, addrs in raw.items():
                if isinstance(addrs, dict):
                    iterable = addrs.keys()
                elif isinstance(addrs, (list, tuple, set)):
                    iterable = addrs
                else:
                    iterable = [addrs]
                for addr in iterable:
                    _add(uni, addr)
            return out

        if isinstance(raw, (list, tuple, set)):
            for item in raw:
                if isinstance(item, dict):
                    for uni, addrs in item.items():
                        if isinstance(addrs, dict):
                            iterable = addrs.keys()
                        elif isinstance(addrs, (list, tuple, set)):
                            iterable = addrs
                        else:
                            iterable = [addrs]
                        for addr in iterable:
                            _add(uni, addr)
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    _add(item[0], item[1])
            return out

        return out

    @staticmethod
    def _extra_source_universes(extra_sources: Optional[dict]) -> Set[int]:
        """extra_sources 引入的额外 universe 集合（用于 snapshot 限定并集）。"""
        out: Set[int] = set()
        if not extra_sources:
            return out
        try:
            for u in (extra_sources or {}).keys():
                out.add(int(u))
        except Exception:
            log.debug("track_linker: failed to parse extra source universe")
            pass
        return out

    def _limited_extra_source_universes(self, extra_sources: Optional[dict]) -> Set[int]:
        """extra_sources 中仅被规则引用的 universe 子集（src/dst 通道所在域）。

        规则读取混合帧/活性掩码只发生在 src/dst 通道的 universe（_read_universes_cache）；
        extra 域超出该集合的部分并入 mix/scan 集也无人消费（纯浪费全量混合）。
        未被引用的 extra 域由 _overlay_extra_sources/_overlay_extra_source_masks
        补零帧+数据，键集合不变，规则读值逐字节一致。
        """
        all_extra = self._extra_source_universes(extra_sources)
        if not all_extra:
            return all_extra
        read_unis = self._read_universes_cache
        if read_unis is None:
            # 缓存未建立(理论不可达: 本函数只在校验过 enabled_rules 后调用) → 保守全量
            return all_extra
        return {int(u) for u in all_extra if int(u) in read_unis}

    def _get_snapshot(
        self,
        exclude_layers: Optional[Set[str]] = None,
        limit_universes: Optional[Set[int]] = None,
    ) -> Dict[int, np.ndarray]:
        """获取不含 linker 层的混合快照，避免反馈循环。

        limit_universes 非 None 时只混合这些 universe（关联只读源 universe，
        目标 universe 只写不读，无需参与 mix）。为 None 时保持原行为混合全部。

        ★ 修复（v1.1）：原实现自行重写了一套简化混合逻辑，与 NM._mix_one_universe
        存在以下差异，导致读到的源通道值（如 motor_x）与硬件实际输出不一致：

          1. 缺少 only_shape 分支：当源通道仅存在于 Effect/Timeline 等 SHAPE 层，
             而不在 programmer/fader/tester 任何 BASE 层时，正确输出应为
             shape_raw（效果原始值），但原实现输出 0 + (effect−anchor)。
             对于幅度 127、中心 128 的正弦波，正半周最大只能读到 127，
             负半周全被 clip 到 0 → motor_x 始终读不到真实位置。
             以 motor_pump_cosine(127, center=128, cutoff=64) 为例：
             deviation=1 → pump≈254.97 → 截断为 254，这正是"卡在 254"的根因。

          2. 缺少多形状层平均：多条 SHAPE 层叠加时 NM 会对每通道偏移求平均，
             原实现直接累加，导致读值被放大。

        修复方案：直接复用 NM._mix_one_universe（update_anchor=False，避免
        污染主循环 anchor 状态），并将 linker 层置空以切断反馈。
        """
        try:
            if self._nm is None or not hasattr(self._nm, '_layers'):
                return {}

            excluded = set(exclude_layers or {"linker"})
            ltp_excluded = set(excluded)
            # Linker rules must follow the actual moving shape/effect value.  If a
            # static programmer/fader timestamp is allowed to win LTP here, motor
            # sources read as the parked center value and pump links stay maxed.
            ltp_excluded.update(STATIC_EDITOR_LAYERS)

            # 收集所有需要混合的 universe
            all_unis: set = set()
            for name, layer_data in self._nm._layers.items():
                if name not in excluded:
                    all_unis.update(layer_data.keys())

            # 只保留调用方需要的源 universe（目标 universe 只写不读，无需混合）
            if limit_universes is not None:
                want = {int(u) for u in limit_universes}
                all_unis = {int(u) for u in all_unis if int(u) in want}

            if not all_unis:
                return {}

            # 复用 NM 混合逻辑（update_anchor=False 不污染主循环 anchor）。
            # ★ apply_xy_reference=True（v1.2 修复）：关联器读物理输出域（含同步
            #   基准角/全局角/扇面角旋转），基准变化后源通道值实时跟随新基准；
            #   此前 False 读到未变换虚拟值，基准变化时关联器输出偏离旧基准。
            #   无基准配置时 NM._apply_xy_reference 恒等返回，行为不变。
            result: Dict[int, np.ndarray] = {}
            if hasattr(self._nm, "compute_universe_snapshots_now"):
                frames, _active = self._nm.compute_universe_snapshots_now(
                    universes={int(u) for u in all_unis},
                    exclude_layers=list(excluded),
                    apply_output_smoothing=False,
                    apply_xy_reference=True,
                    exclude_ltp_editor_layers=ltp_excluded,
                )
                result.update(frames)
            elif hasattr(self._nm, "compute_virtual_universe_now"):
                for uni in all_unis:
                    mixed = self._nm.compute_virtual_universe_now(
                        uni,
                        exclude_layers=list(excluded),
                        exclude_ltp_editor_layers=ltp_excluded,
                    )
                    result[uni] = mixed  # 已经是 uint8[512]
            else:
                # 构造排除 linker 层的快照字典（与 NM._mix_and_send 保持相同结构）。
                snapshots: Dict[str, Dict[int, Any]] = {}
                for name, layer_data in self._nm._layers.items():
                    if name in excluded:
                        snapshots[name] = {}
                    else:
                        snapshots[name] = {
                            u: (d.copy(), a.copy())
                            for u, (d, a) in layer_data.items()
                        }
                for uni in all_unis:
                    mixed = self._nm._mix_one_universe(
                        snapshots,
                        uni,
                        update_anchor=False,
                        exclude_ltp_editor_layers=ltp_excluded,
                    )
                    result[uni] = mixed  # 已经是 uint8[512]

            return result
        except Exception:
            log.debug("_get_snapshot 异常", exc_info=True)
        return {}

    def _get_active_snapshot(
        self,
        exclude_layers: Optional[Set[str]] = None,
        universes: Optional[Set[int]] = None,
    ) -> Dict[int, np.ndarray]:
        """Return authored active channels by universe, excluding idle defaults.

        universes 非 None 时只计算这些域的掩码（引擎只读 src/dst 通道所在域）。
        """
        try:
            if self._nm is None or not hasattr(self._nm, '_layers'):
                return {}
            excluded = set(exclude_layers or {"linker"})
            if hasattr(self._nm, "compute_active_snapshot_now"):
                return self._nm.compute_active_snapshot_now(
                    universes=universes,
                    exclude_layers=list(excluded),
                    active_layers=ALL_LAYERS,
                )
            result: Dict[int, np.ndarray] = {}
            for name, layer_data in self._nm._layers.items():
                if name in excluded:
                    continue
                for uni, entry in (layer_data or {}).items():
                    if entry is None:
                        continue
                    if universes is not None and int(uni) not in universes:
                        continue
                    try:
                        _data, active = entry
                        active_arr = np.asarray(active, dtype=bool).reshape(-1)
                    except Exception:
                        continue
                    if active_arr.shape[0] != 512:
                        fixed = np.zeros(512, dtype=bool)
                        n = min(int(active_arr.shape[0]), 512)
                        if n > 0:
                            fixed[:n] = active_arr[:n]
                        active_arr = fixed
                    if not np.any(active_arr):
                        continue
                    uni_i = int(uni)
                    mask = result.get(uni_i)
                    if mask is None:
                        result[uni_i] = active_arr.copy()
                    else:
                        mask |= active_arr
            return result
        except Exception:
            log.debug("_get_active_snapshot 异常", exc_info=True)
        return {}

    def _build_rule_prep(
        self, rule: LinkRule, preset_def
    ) -> Tuple[Dict[int, str], Dict[int, Optional[int]], List[ChannelRef], bool]:
        """一次性构建规则静态派生数据(见 _process_rule 缓存说明)。

        返回 (key_of, fine_of, src_channels, is_motor_pump_rule)：
          - key_of/fine_of：id(ref) → 通道类型键 / fine 通道地址(无则 None)
          - src_channels：电机→泵单源自动补另一轴后的源引用列表
          - is_motor_pump_rule：是否电机→泵预设(决定低值覆盖语义)
        """
        is_motor_pump_rule = (
            _is_motor_to_pump_preset(preset_def.id)
            and _has_pump_dst(rule.dst_channels)
        )
        src_channels = self._prep_src_channels(rule, is_motor_pump_rule)
        key_of: Dict[int, str] = {}
        fine_of: Dict[int, Optional[int]] = {}
        for _ref in list(src_channels) + list(rule.dst_channels):
            _key = _channel_type_key(getattr(_ref, "ch_type", ""))
            key_of[id(_ref)] = _key
            _coarse = MOTOR_FINE_TO_COARSE.get(_key, _key)
            _fine_type = MOTOR_FINE_CHANNELS.get(_coarse)
            if (
                _fine_type
                and getattr(_ref, "fixture_id", None)
                and self._nm is not None
                and hasattr(self._nm, "find_fixture_channel_addr")
            ):
                fine_of[id(_ref)] = self._resolve_channel_addr_cached(
                    _ref.fixture_id, _fine_type, _ref.universe,
                )
            else:
                fine_of[id(_ref)] = None
        return key_of, fine_of, src_channels, is_motor_pump_rule

    def _prep_src_channels(self, rule: LinkRule, is_motor_pump_rule: bool) -> List[ChannelRef]:
        """电机→泵预设单源时自动补另一轴源引用(原 _source_refs_for_rule 逻辑)。"""
        refs = list(rule.src_channels)
        if not is_motor_pump_rule or len(refs) != 1:
            return refs

        ref = refs[0]
        src_type = _channel_type_key(ref.ch_type)
        if src_type not in {"motor_x", "motor_y"} or not ref.fixture_id:
            return refs

        other_type = "motor_y" if src_type == "motor_x" else "motor_x"
        if self._nm is None or not hasattr(self._nm, "find_fixture_channel_addr"):
            return refs
        other_addr = self._resolve_channel_addr_cached(
            ref.fixture_id, other_type, ref.universe,
        )
        if other_addr is None:
            return refs

        other_ref = ChannelRef(
            fixture_id=ref.fixture_id,
            ch_type=_canonical_ch_type(other_type),
            universe=ref.universe,
            dmx_addr=int(other_addr),
        )
        if src_type == "motor_x":
            return [ref, other_ref]
        return [other_ref, ref]

    def _process_rule(
        self,
        rule: LinkRule,
        snapshot: Dict[int, np.ndarray],
        writes: Dict[int, Dict[int, int]],
        motion_snapshot: Optional[Dict[int, np.ndarray]] = None,
        source_active: Optional[Dict[int, np.ndarray]] = None,
        motion_active: Optional[Dict[int, np.ndarray]] = None,
        parked_src: dict[int, np.ndarray] | None = None,
    ):
        """处理单条规则，将结果写入 writes。

        ★ v1.1 — 配对模式（Paired Mode）：
          当 src_channels 与 dst_channels 数量相同（N:N）时，按索引一一对应计算：
            dst_0 = f(src_0), dst_1 = f(src_1), ..., dst_N = f(src_N)
          而不是把所有 src 取平均再广播给所有 dst。

          典型场景：通过批量生成或手动添加了 N 台设备，每台设备的 motor_x_i 应
          独立驱动同一台设备的 pump_i，而不是 N 台电机的平均值驱动所有泵。

          当各 motor_x 通道相位不同（如正弦波追逐效果）时，平均后几乎恒为中位值
          128，导致 pump 被持续映射到最大值（约 254/255）并"卡住"，这是原版
          bug 的根本原因。

          保留旧行为的场景：
            - N:1  (多源→单目标)：取平均后输出到单目标（如 XY 合力→泵）
            - 1:M  (单源→多目标)：单源广播到所有目标（如一个主控→多台设备）
            - motor_xy 辅助值：src 有 2 个、dst 有 1 个，走旧路径，辅助值 = src[1]
        """
        if not rule.src_channels or not rule.dst_channels:
            return

        preset_def = PRESET_BY_ID.get(rule.preset_id)
        if preset_def is None:
            return

        # 规则静态派生数据缓存：源引用列表(电机→泵单源自动补另一轴)、通道类型
        # 键、fine 地址、是否电机泵规则——规则与配接不变则恒定。原实现每 tick 对
        # 同一 ref 重复 _channel_type_key 归一化 + _fine_addr_for_ref 查找 ~10 次
        # 并新建 ChannelRef(120 规则实测 ~1200+ 次/25ms，占 process_once 40%+)。
        # 键=id(rule)+身份校验，规则/配接变化经 rules_changed → _invalidate_rule_caches
        # 整体失效。
        prep = self._rule_prep_cache.get(id(rule))
        if prep is None or prep[0] is not rule:
            prep = (rule,) + self._build_rule_prep(rule, preset_def)
            self._rule_prep_cache[id(rule)] = prep
        key_of, fine_of, src_channels, is_motor_pump_rule = prep[1:]
        # 电机"停放"掩码（每 tick 由 _process_once 传入）——低值覆盖不遮停放电机。
        _parked_src = parked_src
        n_src = len(src_channels)
        n_dst = len(rule.dst_channels)
        src_type_keys = {key_of[id(ref)] for ref in src_channels}
        use_xy_vector = is_motor_pump_rule and {"motor_x", "motor_y"}.issubset(src_type_keys)

        def _rule_motors_parked() -> bool:
            """本规则的电机源是否全部为"停放"（静态层写值、无动效运动）。

            停放 → 电机并非在"运动"，低值覆盖不得据此把泵遮罩到 0（否则未播放/
            手拉滑块/切预设的瞬间都会把泵消除）。只要有一路电机在运动即按原
            遮罩语义执行。

            16bit 感知（与上方 _is_active 的 fine 回退同形态，见
            _motor_ref_parked）：粗地址或细地址任一处于停放掩码即视为该电机
            停放——只查粗地址会漏判细字节停放的 16bit 电机，两处判据不对称。
            """
            if not is_motor_pump_rule or parked_src is None:
                return False
            motors = [r for r in src_channels if key_of.get(id(r)) in MOTOR_SRC_TYPES]
            if not motors:
                return False
            for r in motors:
                mask = parked_src.get(r.universe)
                if not _motor_ref_parked(mask, r.dmx_addr, _fine_addr_for_ref(r)):
                    return False
            return True

        def _fine_addr_for_ref(ch_ref: "ChannelRef") -> Optional[int]:
            # 调用前已在上方 key_of/fine_of 一次算好；未注册的 ref 兜底走原逻辑
            cached = fine_of.get(id(ch_ref))
            if cached is not None or id(ch_ref) in fine_of:
                return cached
            ch_type_key = _channel_type_key(ch_ref.ch_type)
            coarse_type = MOTOR_FINE_TO_COARSE.get(ch_type_key, ch_type_key)
            fine_type = MOTOR_FINE_CHANNELS.get(coarse_type)
            if not fine_type or not ch_ref.fixture_id:
                return None
            if self._nm is not None and hasattr(self._nm, "find_fixture_channel_addr"):
                return self._resolve_channel_addr_cached(
                    ch_ref.fixture_id, fine_type, ch_ref.universe,
                )
            return None

        def _is_active(active_snapshot: Optional[Dict[int, np.ndarray]], ch_ref: "ChannelRef") -> bool:
            mask = (active_snapshot or {}).get(ch_ref.universe)
            if mask is None:
                return False
            try:
                addr = int(ch_ref.dmx_addr)
            except Exception:
                return False
            if 0 <= addr < 512 and bool(mask[addr]):
                return True
            src_type = key_of.get(id(ch_ref))
            if src_type in MOTOR_FINE_CHANNELS:
                fine_addr = _fine_addr_for_ref(ch_ref)
                if fine_addr is not None and 0 <= int(fine_addr) < 512:
                    return bool(mask[int(fine_addr)])
            return False

        def _read_src(ch_ref: "ChannelRef") -> Optional[float]:
            src_type = key_of.get(id(ch_ref))
            uni_data = snapshot.get(ch_ref.universe)
            if src_type in MOTOR_SRC_TYPES:
                if _is_active(motion_active, ch_ref):
                    motion_data = (motion_snapshot or {}).get(ch_ref.universe)
                    if motion_data is not None:
                        uni_data = motion_data
                elif not _is_active(source_active, ch_ref):
                    return None
            elif not _is_active(source_active, ch_ref):
                return None
            if uni_data is not None and 0 <= ch_ref.dmx_addr < 512:
                if src_type in MOTOR_FINE_CHANNELS:
                    val = read_axis_logical_value(
                        uni_data,
                        int(ch_ref.dmx_addr),
                        _fine_addr_for_ref(ch_ref),
                    )
                else:
                    val = float(uni_data[ch_ref.dmx_addr])
                if src_type in MOTOR_SRC_TYPES:
                    # ★ 2026-08-11 修复 + 2026-09-17 16bit 补全：0 帧/未驱动电机
                    #   统一按归中 128 解读（同步模式经 xy_reference 静止分支写
                    #   128、默认模式恒等保留 0 → 同一状态两种基准读数不一致，
                    #   电机→泵 result 在 255（低值覆盖失效）与 0（泵被掐断）间
                    #   跳变）。判据已含 16bit（粗 0 + 细残量合成 0.5~0.996 不再
                    #   是 0.0），阈值与理由见 _normalize_parked_motor_read。
                    val = _normalize_parked_motor_read(val)
                return val
            return None

        def _write_dst(dst_ref: "ChannelRef", result: float):
            if dst_ref.universe not in writes:
                writes[dst_ref.universe] = {}
            dst_type = key_of.get(id(dst_ref))
            if dst_type in MOTOR_FINE_TO_COARSE:
                writes[dst_ref.universe][dst_ref.dmx_addr] = int(np.clip(result, 0, 255))
                return
            def _pending_dst_value() -> Optional[float]:
                ch_map = writes.get(dst_ref.universe) or {}
                try:
                    addr = int(dst_ref.dmx_addr)
                except Exception:
                    return None
                if addr in ch_map:
                    return float(ch_map[addr])
                if dst_type in MOTOR_FINE_CHANNELS:
                    fine_addr = _fine_addr_for_ref(dst_ref)
                    if fine_addr is not None and int(fine_addr) in ch_map:
                        return read_axis_logical_value(
                            np.asarray(
                                [ch_map.get(i, 0) for i in range(512)],
                                dtype=np.uint8,
                            ),
                            addr,
                            int(fine_addr),
                        )
                return None
            def _current_dst_value(default: float) -> float:
                pending = _pending_dst_value()
                if pending is not None:
                    return pending
                uni_data = snapshot.get(dst_ref.universe)
                if uni_data is None:
                    return float(default)
                if dst_type in MOTOR_FINE_CHANNELS:
                    return read_axis_logical_value(
                        uni_data,
                        int(dst_ref.dmx_addr),
                        _fine_addr_for_ref(dst_ref),
                    )
                return float(uni_data[dst_ref.dmx_addr])
            # v1.2 起混合模式统一为低值覆盖：只减不加，作为已有目标值的遮罩。
            # 已有目标值包括本 tick 中前面关联规则已经写出的值，否则同一关联器内
            # “先生成泵、再用电机偏离压低泵”的规则链会被误判为空目标。
            # ★ 2026-08-11 用户定案：泵目标无任何基础值时不得直接开启——旧行为
            #   “直接写 result”（未驱动电机 result=255）会把规则内全部泵拉满
            #   （先全开再关联），违反低值覆盖“只降低已有泵值”语义。无基础值
            #   → 保持无输出，待 clip 层给出基础值后只作遮罩。
            #   （旧存档的 override/add/multiply 值不再区分，一律按低值覆盖执行）
            has_pending_dst = _pending_dst_value() is not None
            if not has_pending_dst and not _is_active(source_active, dst_ref):
                # 目标无基础值
                if dst_type == "pump":
                    # 泵无基础值 → 不开启（低值覆盖 = 只降低已有值，不生成值）
                    return
                # 非泵目标无基础值 → 直接写入计算结果（保持 v1.2 原行为）
                val = float(np.clip(result, 0, 255))
            elif dst_type == "pump" and _rule_motors_parked():
                # ★ 2026-09-12 系统性修复：泵有基础值，但本规则的电机源全部为
                #   "停放"（仅静态编辑器层写值、无动效运动）→ 电机并未在运动，
                #   低值覆盖不得据此压低泵。写**当前值**等价于"此规则不遮罩"，
                #   同时保留 linker 层占用（泵走 linker 值 = 基础值，保持低值
                #   覆盖的单一所有者语义）。若完全不写，linker 会释放该通道，
                #   一旦该通道在 clip 层不存在（如 programmer 手拉/仅编辑器源）
                #   就会回落为 0 —— 用户实测「一开关联器泵就被消掉」。
                val = float(np.clip(_current_dst_value(result), 0, 255))
            else:
                # 目标已有基础值 → 取当前值和计算结果中的较小值（只减不加）。
                cur = _current_dst_value(255.0)
                val = float(np.clip(min(cur, result), 0, 255))
            if dst_type in MOTOR_FINE_CHANNELS:
                fine_addr = _fine_addr_for_ref(dst_ref)
                if fine_addr is not None and 0 <= int(fine_addr) < 512:
                    coarse, fine = logical_to_16bit_pair(val)
                    writes[dst_ref.universe][dst_ref.dmx_addr] = coarse
                    writes[dst_ref.universe][int(fine_addr)] = fine
                    return
            writes[dst_ref.universe][dst_ref.dmx_addr] = int(np.clip(val, 0, 255))

        # ── 时序控制（跟水）：stagger / spacing / spray ─────────────
        now = _time.time()
        _has_timing = (
            rule.spray_delay_ms > 0
            or rule.stagger_delay_ms > 0
            or rule.spacing_profile != "uniform"
        )
        _wrote_this_tick = False

        def _finish_tick():
            """收尾：更新激活状态（**每个出口都必须调用**）。

            ★★ R131 修复（关联器"时差/喷水延时/紧距"在三个分支彻底失效 → 该规则
            完全不出值）：原先这段"写激活时间戳"只挂在**函数最末尾**，而配对 N:N
            （`n_src == n_dst > 1`）、XY 向量、灯跟踪三个分支都**提前 return** →
            `_rule_activation_state[rule.id]` 永远是 None → `_staggered_write` 第一道
            门 `activation_ts is None → return` 把每次写入都吞掉 → 该规则彻底熄灭
            （实测配对+stagger=100ms 跑 40 tick，写入 `{}`、tick=None）。
            抽成闭包后三条路径都走到它，与广播路径行为一致。
            """
            if not _has_timing:
                return
            if _wrote_this_tick:
                if rule.id not in self._rule_activation_state:
                    self._rule_activation_state[rule.id] = now
            else:
                self._rule_activation_state.pop(rule.id, None)

        def _staggered_write(
            dst_ref: "ChannelRef",
            value: float,
            dev_idx: int = 0,
            dev_count: int = 1,
        ):
            """Apply stagger delay, spacing profile, and spray delay to a write."""
            nonlocal _wrote_this_tick
            if value > 0:
                _wrote_this_tick = True

            if not _has_timing:
                _write_dst(dst_ref, value)
                return

            activation_ts = self._rule_activation_state.get(rule.id)

            # 未激活或时槽未到：低值覆盖不主动创建输出，直接跳过
            if activation_ts is None or activation_ts <= 0:
                return

            # Timing check: has this device's slot arrived?
            delay_s = (
                rule.spray_delay_ms / 1000.0
                + dev_idx * rule.stagger_delay_ms / 1000.0
            )
            if now < activation_ts + delay_s:
                return

            # Apply spacing profile
            spacing = 1.0
            if dev_count > 1 and rule.spacing_profile != "uniform":
                if rule.spacing_profile == "linear_fade":
                    spacing = max(0.0, 1.0 - dev_idx / (dev_count - 1))
                elif rule.spacing_profile == "gaussian":
                    c = (dev_count - 1) / 2.0
                    s = max(1.0, dev_count / 4.0)
                    spacing = math.exp(-((dev_idx - c) / s) ** 2)

            _write_dst(dst_ref, float(value * spacing))
            # Track per-device trigger timestamp
            dev_key = f"{rule.id}:U{dst_ref.universe}:A{dst_ref.dmx_addr}"
            self._device_activation_time[dev_key] = now

        def _xy_result_for_refs(x_ref: Optional["ChannelRef"], y_ref: Optional["ChannelRef"]) -> Optional[float]:
            params = dict(rule.params)
            center_x = float(params.get("center_x", params.get("center", 128.0)))
            center_y = float(params.get("center_y", params.get("center", 128.0)))
            x_val = _read_src(x_ref) if x_ref is not None else None
            y_val = _read_src(y_ref) if y_ref is not None else None
            if x_val is None and y_val is None:
                return None
            params["_aux_val"] = y_val if y_val is not None else center_y
            src_val = x_val if x_val is not None else center_x
            raw = preset_def.fn(src_val, params)
            return float(np.clip(raw, 0.0, 255.0))

        def _write_multi_dst_xy_by_fixture() -> bool:
            if not use_xy_vector or n_dst <= 1:
                return False
            by_fixture: Dict[str, Dict[str, ChannelRef]] = {}
            ordered_pairs: List[Dict[str, ChannelRef]] = []
            current_pair: Dict[str, ChannelRef] = {}
            for ref in src_channels:
                src_key = key_of.get(id(ref))
                if src_key not in {"motor_x", "motor_y"}:
                    continue
                fid = str(getattr(ref, "fixture_id", "") or "")
                if fid:
                    by_fixture.setdefault(fid, {})[src_key] = ref
                if src_key == "motor_x" and current_pair:
                    ordered_pairs.append(current_pair)
                    current_pair = {}
                current_pair[src_key] = ref
                if "motor_x" in current_pair and "motor_y" in current_pair:
                    ordered_pairs.append(current_pair)
                    current_pair = {}
            if current_pair:
                ordered_pairs.append(current_pair)

            wrote_any = False
            for idx, dst_ref in enumerate(rule.dst_channels):
                pair = by_fixture.get(str(getattr(dst_ref, "fixture_id", "") or ""))
                if pair is None and idx < len(ordered_pairs):
                    pair = ordered_pairs[idx]
                if not pair:
                    continue
                result = _xy_result_for_refs(pair.get("motor_x"), pair.get("motor_y"))
                if result is None:
                    continue
                _staggered_write(dst_ref, result, idx, n_dst)
                wrote_any = True
            return wrote_any

        if _write_multi_dst_xy_by_fixture():
            _finish_tick()
            return

        # ── 配对模式：N src == N dst，逐对独立计算 ──────────────────────────
        if n_src == n_dst and n_src > 1 and not use_xy_vector:
            for i, (src_ref, dst_ref) in enumerate(zip(src_channels, rule.dst_channels)):
                src_val = _read_src(src_ref)
                if src_val is None:
                    continue
                params = dict(rule.params)
                raw = preset_def.fn(src_val, params)
                _staggered_write(dst_ref, float(np.clip(raw, 0.0, 255.0)), i, n_dst)
            _finish_tick()
            return

        if preset_def.id == "motor_color_whiten":
            _color_whiten_writes(rule, preset_def, src_channels,
                                 snapshot=snapshot, writes=writes,
                                 read_src=_read_src, key_of=key_of)
            return

        if preset_def.id.startswith("pump_light_track"):
            _light_track_writes(rule, preset_def, src_channels, read_src=_read_src,
                                stagger=_staggered_write, n_dst=n_dst)
            _finish_tick()
            return

        # ── 广播模式：多源取平均 → 所有目标（含 motor_xy 辅助值路径）────
        src_vals = [_read_src(ref) for ref in src_channels]
        params = dict(rule.params)
        if use_xy_vector:
            if not any(val is not None for val in src_vals):
                return
            center_x = float(params.get("center_x", params.get("center", 128.0)))
            center_y = float(params.get("center_y", params.get("center", 128.0)))
            src_val = src_vals[0] if len(src_vals) >= 1 and src_vals[0] is not None else center_x
            if len(src_vals) >= 2:
                params["_aux_val"] = src_vals[1] if src_vals[1] is not None else center_y
        else:
            present_vals = [val for val in src_vals if val is not None]
            if not present_vals:
                return
            src_val = sum(present_vals) / len(present_vals)
            if len(present_vals) >= 2:
                params["_aux_val"] = present_vals[1]  # motor_xy 辅助值（第二轴）

        raw_result = preset_def.fn(src_val, params)
        result = float(np.clip(raw_result, 0.0, 255.0))

        for i, dst_ref in enumerate(rule.dst_channels):
            _staggered_write(dst_ref, result, i, n_dst)

        _finish_tick()

    # ── 序列化 ───────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "enabled": self._enabled,
            "rules":   [r.to_dict() for r in self._rules],
            "groups":  [g.to_dict() for g in self._groups],
        }

    def from_dict(self, d: dict):
        enabled = bool(d.get("enabled", False))
        self._rules = [LinkRule.from_dict(rd) for rd in d.get("rules", [])]
        self._groups = [RuleGroup.from_dict(gd) for gd in d.get("groups", [])]
        # 防御：归档中的组可能引用已不存在的规则（旧档/损坏）——剔除悬空 id
        valid_ids = {r.id for r in self._rules}
        for g in self._groups:
            g.rule_ids = [rid for rid in g.rule_ids if rid in valid_ids]
        self._clear_stale_linker_outputs({})
        self.set_enabled(enabled)
        self.rules_changed.emit()

    # ── 辅助：从设备信息批量生成规则 ────────────────────────────────────────

    @staticmethod
    def _scan_device_refs(
        inst, fdef, src_key: str, dst_key: str,
        src_canon: str, dst_canon: str, is_motor_xy_preset: bool,
    ):
        """扫描单台设备通道，返回 (src_refs, dst_ref_or_None)。

        src_refs 为源通道引用列表（motor→pump 预设自动补 X/Y 双轴）；
        dst_ref 为 None 表示该设备无目标通道。
        """
        channels = getattr(fdef, 'channels', [])
        try:
            start_ch = int(getattr(inst, 'start_channel', 1) or 1) - 1  # 转为 0-indexed
        except Exception:
            start_ch = 0
        universe  = getattr(inst, 'universe', 1)
        inst_id   = getattr(inst, 'id', '')

        src_addr = None
        motor_x_addr = None
        motor_y_addr = None
        dst_addr = None
        for i, ch_type in enumerate(channels):
            addr = start_ch + i
            if not (0 <= addr < 512):
                continue
            ch_key = _channel_type_key(ch_type)
            if ch_key == "motor_x":
                motor_x_addr = addr
            elif ch_key == "motor_y":
                motor_y_addr = addr
            if ch_key == src_key:
                src_addr = addr
            if ch_key == dst_key:
                dst_addr = addr

        src_refs: List[ChannelRef] = []
        if src_addr is not None:
            src_refs = [ChannelRef(inst_id, src_canon, universe, src_addr)]
        if is_motor_xy_preset and (motor_x_addr is not None or motor_y_addr is not None):
            xy_refs = []
            if motor_x_addr is not None:
                xy_refs.append(ChannelRef(inst_id, "motor_x", universe, motor_x_addr))
            if motor_y_addr is not None:
                xy_refs.append(ChannelRef(inst_id, "motor_y", universe, motor_y_addr))
            if xy_refs:
                src_refs = xy_refs

        dst_ref = None
        if dst_addr is not None:
            dst_ref = ChannelRef(inst_id, dst_canon, universe, dst_addr)
        return src_refs, dst_ref

    @staticmethod
    def _scan_group_dst_refs(inst, fdef, src_key, src_canon, *,
                             is_motor_xy_preset, dst_group):
        """组目标预设：收集该设备**整组通道**为目标（用户定案 2026-09-18）。

        目标集合由预设声明 `dst_group` 决定（见 `DST_GROUP_CHANNELS`）：
          · `device_rgbw`  → 全部色彩通道（偏色必须同时写 R/G/B/W）
          · `device_light` → 全部灯通道（dimmer + RGBW，泵→灯跟踪）
        源仍按 src_key 匹配（含百变以功能通道充当的 zoom——由调用方传 src_key）。
        """
        channels = getattr(fdef, "channels", [])
        try:
            start_ch = int(getattr(inst, "start_channel", 1) or 1) - 1
        except Exception:
            start_ch = 0
        universe = getattr(inst, "universe", 1)
        inst_id = getattr(inst, "id", "")
        want = set(DST_GROUP_CHANNELS.get(str(dst_group or ""), ()))
        src_refs: List[ChannelRef] = []
        dst_refs: List[ChannelRef] = []
        for i, ch_type in enumerate(channels):
            addr = start_ch + i
            if not (0 <= addr < 512):
                continue
            k = _channel_type_key(ch_type)
            if k == src_key:
                src_refs.append(ChannelRef(inst_id, src_canon, universe, addr))
            if k in want:
                dst_refs.append(ChannelRef(
                    inst_id, _canonical_ch_type(k), universe, addr))
        return src_refs, dst_refs

    @staticmethod
    def _make_group_rule(
        name: str, src_refs: List[ChannelRef], dst_refs: List[ChannelRef],
        preset_id: str, params: dict, group_label: str, *,
        type_name: str = "",
    ) -> LinkRule:
        """构造一条批量生成的规则（混合模式统一为低值覆盖）。"""
        return LinkRule(
            name=name,
            enabled=True,
            src_channels=src_refs,
            dst_channels=dst_refs,
            preset_id=preset_id,
            params=dict(params),
            group_label=group_label,
            type_name=type_name,
        )

    # ── 包单元：设备包（逻辑设备 kind=package）内子设备分组 ────────────────
    # 至冬7.0 等 工程的"包"= 一个逻辑设备，内含多个子设备（电机子设备/
    # 泵子设备/灯子设备）。批量生成（如 电机→泵）时若把包内每台子设备当独立设备
    # 全局配对，会把不同包的电机/泵错配：数量不等时生成一条全局广播规则，所有泵
    # 被同一值驱动（偏离归中即全关→"泵被关停"），且每台泵没有自己对应的电机
    # （"无关联"）。修复：按包分组，包内按通道类型选源/目标子设备，多候选询问。

    @staticmethod
    def _group_units(
        fixture_instances: list,
        logical_devices: Optional[list] = None,
    ) -> List[list]:
        """设备按包（逻辑设备 kind=package）分组：包成员一个单元、散台单台一单元。

        单元顺序 = 组内设备顺序（按单元首个成员出现位置），保证跨单元配对顺序确定。
        logical_devices 为 None 或空 → 每台设备一个单元（逐台旧行为）。
        """
        if not logical_devices:
            return [[f] for f in fixture_instances]
        pkg_members: Dict[str, Set[str]] = {}
        for ld in logical_devices:
            # ld 可能是 dict（工程存档/面板）或对象（外部 API）——统一取键
            if isinstance(ld, dict):
                kind = str(ld.get("kind", "") or "")
                members = [str(m) for m in (ld.get("member_fixture_ids", []) or [])]
            else:
                kind = str(getattr(ld, "kind", "") or "")
                members = [str(m) for m in
                           (getattr(ld, "member_fixture_ids", []) or [])]
            if kind != "package":
                continue
            if len(members) < 2:
                continue
            for m in members:
                pkg_members.setdefault(m, set()).update(members)
        if not pkg_members:
            return [[f] for f in fixture_instances]
        units: List[list] = []
        seen: Set[str] = set()
        for f in fixture_instances:
            fid = str(getattr(f, "id", "") or "")
            if not fid or fid in seen:
                continue
            group = pkg_members.get(fid)
            if not group:
                seen.add(fid)
                units.append([f])
                continue
            unit: list = []
            for f2 in fixture_instances:
                f2id = str(getattr(f2, "id", "") or "")
                if f2id in group and f2id not in seen:
                    unit.append(f2)
                    seen.add(f2id)
            if unit:
                units.append(unit)
        return units

    @staticmethod
    def _scan_unit_refs(
        unit: list,
        fixture_defs: dict,
        src_key: str,
        dst_key: str,
        src_canon: str,
        dst_canon: str,
        is_motor_xy_preset: bool, *,
        dst_group: str = "",
    ) -> Tuple[List[dict], List[dict]]:
        """扫描一个包单元，返回 (src_cands, dst_cands)。

        每条候选 = {"inst": FixtureInstance, "type_name": 设备类型名,
                    "refs": 源通道引用列表, "ref": 目标通道引用 or None}。
        src 候选按通道类型匹配（电机预设→含 motor_x 的子设备）；
        dst 候选同理（泵预设→含 pump 的子设备）。只收集通道真实存在的子设备。
        """
        src_cands: List[dict] = []
        dst_cands: List[dict] = []
        for inst in unit:
            fdef = fixture_defs.get(getattr(inst, "def_id", ""))
            if fdef is None:
                continue
            if dst_group:
                # ★ 组目标预设（预设自己声明 dst_group）：目标 = 该设备整组通道。
                #   源仍按 src_key 匹配（含百变以功能通道充当的 zoom）。
                src_refs, color_refs = TrackLinkerEngine._scan_group_dst_refs(
                    inst, fdef, src_key, src_canon,
                    is_motor_xy_preset=is_motor_xy_preset, dst_group=dst_group)
            else:
                src_refs, dst_ref = TrackLinkerEngine._scan_device_refs(
                    inst, fdef, src_key, dst_key, src_canon, dst_canon,
                    is_motor_xy_preset)
                color_refs = [dst_ref] if dst_ref is not None else []
            type_name = str(getattr(fdef, "name", "") or getattr(inst, "def_id", ""))
            if src_refs:
                src_cands.append({
                    "inst": inst, "type_name": type_name, "refs": src_refs,
                })
            if color_refs:
                dst_cands.append({
                    "inst": inst, "type_name": type_name,
                    "ref": color_refs[0], "refs": color_refs,
                })
        return src_cands, dst_cands

    @staticmethod
    def _pick_unit_types(
        src_types: List[str],
        dst_types: List[str],
        resolve_choice: Optional[Callable],
    ) -> Optional[Tuple[Optional[str], Optional[str]]]:
        """全局源/目标子设备类型选择：单候选自动；多候选询问用户。

        返回 (src_type, dst_type)；返回 None 表示用户取消（调用方中止生成）。
        无 resolve_choice（预览/无 UI 场景）时多候选取首个类型（数量与询问一致）。
        """
        src_sel = src_types[0] if src_types else None
        dst_sel = dst_types[0] if dst_types else None
        if resolve_choice is not None and (len(src_types) > 1 or len(dst_types) > 1):
            chosen = resolve_choice(src_types, dst_types)
            if chosen is None:
                return None
            src_sel, dst_sel = chosen
        return src_sel, dst_sel

    def build_rules_from_group(
        self,
        fixture_instances: list,
        fixture_defs: dict,
        preset_id: str,
        src_ch_type: str,
        dst_ch_type: str,
        params: dict,
        group_label: str = "",
        logical_devices: Optional[list] = None,
        resolve_choice: Optional[Callable] = None,
    ) -> List[LinkRule]:
        """
        根据设备组批量生成关联规则（v1.2 跨设备配对 / v1.3 包单元子设备选择）。

        1. 包单元：传 logical_devices（逻辑设备 dict 列表）时，包（kind=package）
           内全部子设备为一个单元，散台设备单台一个单元。源/目标子设备按通道
           类型匹配（电机预设→含 motor_x 的子设备；泵预设→含 pump 的子设备）：
           单候选自动选择；多候选（如全能卡包内 全能卡一维+MDD 多台电机）经
           resolve_choice 询问用户（取消→整体中止，绝不生成指向错误子设备的
           规则）。未传 logical_devices → 逐台旧行为。
        2. 自关联：同一单元同时选出源与目标子设备 → 一条规则（src → dst），
           映射到真实子设备通道（不是包代表点）。
        3. 跨单元配对：组内"纯源单元"与"纯目标单元"（如 全能卡包 ↔ 单通道
           水泵）数量相等时按序一一配对（每对一条规则，引擎 N:N 配对模式
           直接消费）；数量不等时生成 1 条广播规则（所有源通道平均 → 所有
           目标通道）。
        4. 防泵关停：只生成源/目标通道真实存在于所选子设备的规则——单元无法
           选出有效源或目标时跳过，绝不生成指向不存在通道的规则。

        返回规则列表（未自动添加，由调用方决定是否添加）。
        """
        rules: List[LinkRule] = []
        src_key = _channel_type_key(src_ch_type)
        dst_key = _channel_type_key(dst_ch_type)
        src_canon = _canonical_ch_type(src_ch_type)
        dst_canon = _canonical_ch_type(dst_ch_type)
        is_motor_xy_preset = (
            _is_motor_to_pump_preset(preset_id)
            and dst_key == "pump"
            and src_key in {"motor_x", "motor_y", "motor_xy"}
        )
        # ★ 组目标预设（用户定案 2026-09-18）：目标通道**由预设自己声明**
        #   （dst_group），UI 不再让用户选单通道；未声明的预设走单通道旧路径。
        dst_group = preset_dst_group(preset_id)
        is_color_preset = bool(dst_group)

        units = self._group_units(fixture_instances, logical_devices)
        unit_scan = [
            self._scan_unit_refs(u, fixture_defs, src_key, dst_key,
                                 src_canon, dst_canon, is_motor_xy_preset,
                                 dst_group=dst_group)
            for u in units
        ]
        # 类型选择仅在传了逻辑设备（包单元模式）时生效；未传时保持逐台
        # 旧行为（全部候选类型参与，不过滤）——向后兼容既有调用方。
        sel_src_type: Optional[str] = None
        sel_dst_type: Optional[str] = None
        if logical_devices is not None:
            src_types = sorted({c["type_name"] for sc, _ in unit_scan for c in sc})
            dst_types = sorted({c["type_name"] for _, dc in unit_scan for c in dc})
            picked = self._pick_unit_types(src_types, dst_types, resolve_choice)
            if picked is None:
                return []
            sel_src_type, sel_dst_type = picked

        src_only, dst_only, rules = _classify_units(
            units, unit_scan, sel_src_type, sel_dst_type, rules,
            preset_id=preset_id, params=params, group_label=group_label,
            src_canon=src_canon, dst_canon=dst_canon,
            is_color_preset=is_color_preset, make_rule=self._make_group_rule)
        _emit_paired_or_broadcast(
            rules, src_only, dst_only, preset_id=preset_id, params=params,
            group_label=group_label, src_canon=src_canon, dst_canon=dst_canon,
            is_color_preset=is_color_preset, make_rule=self._make_group_rule)
        return rules


# ── 全局单例 ──────────────────────────────────────────────────────────────────

_engine_instance: Optional[TrackLinkerEngine] = None

def get_linker_engine() -> TrackLinkerEngine:
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = TrackLinkerEngine()
    return _engine_instance
