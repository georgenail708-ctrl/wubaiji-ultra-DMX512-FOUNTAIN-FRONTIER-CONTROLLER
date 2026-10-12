"""
voice_model — 声部编舞模型(L2)

用户核心纠正:编曲单位不是"设备能力(4固定子系统)",而是 **Voice=(设备type × 通道用法)**,
每声部独立速度带、独立音乐触发、独立输出机制,绝不死板锁死。

声部从项目画像枚举(矩阵RGB 与 喷泉RGB 严格分离)。每声部带"弹性画像"(速度/时长/淡入
分位带,来自标杆学到的 .codex_voice_lib.json;新项目用内置默认)。

decide_voice:给定一段音乐性格(CharacterSegment),每声部独立决定本段:
  开不开 / 用哪种输出机制(冻结静态 / 流动effect / 矩阵) / 选什么空间形状+拓扑子选 /
  速度取弹性带哪一档(柔美偏lo,激烈偏hi) / 淡入淡出 / 是否solo。
arrange_voices:合奏总调度(织体上限 / 留白 / 全局降速)。

纯 core:仅依赖 project_profile + topology/shape_engine,无 Qt。
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

from .project_profile import (
    ProjectProfile, GroupProfile,
    CAP_MOTOR, CAP_PUMP, CAP_VALVE, CAP_RGB, CAP_DIMMER,
)

# ── 用法常量(中文,对齐 voice_lib 键 + 逆向分析)──────────────────────────
USAGE_MOTOR = "数控"
USAGE_PUMP = "泵"
USAGE_VALVE = "阀"
USAGE_RGB = "RGB"
USAGE_DIMMER = "调光"

# ── 声部类(voice_class):决定弹性默认 + 矩阵/喷泉RGB 分离键 ──────────────
VC_MOTOR = "motor"
VC_PUMP = "pump"
VC_VALVE = "valve"
VC_FOUNTAIN_RGB = "fountain_rgb"   # 喷泉自带RGB(中速染色)
VC_MATRIX_RGB = "matrix_rgb"       # 大规模矩阵LED(极快变色画布)—— 与喷泉RGB严格分离
VC_DIMMER = "dimmer"

# 输出机制
MECH_STATIC = "static"     # Programmer 赋固定值,冻结形状,缓开保持
MECH_EFFECT = "effect"     # 动态 effect 流动
MECH_MATRIX = "matrix"     # 矩阵像素引擎

# 角色
ROLE_LEAD = "lead"
ROLE_SUPPORT = "support"
ROLE_SILENT = "silent"

# 能力 → 用法
_CAP_USAGE = {
    CAP_MOTOR: USAGE_MOTOR,
    CAP_PUMP: USAGE_PUMP,
    CAP_VALVE: USAGE_VALVE,
    CAP_RGB: USAGE_RGB,
    CAP_DIMMER: USAGE_DIMMER,
}


# ── 数据结构 ───────────────────────────────────────────────────────────────
@dataclass
class Voice:
    """一个声部 = (设备type × 用法)。"""
    device_type: str = ""          # def 名(如 "HJC 二维")
    usage: str = ""                # USAGE_*
    voice_class: str = ""          # VC_*(决定弹性默认+矩阵分离)
    cap: str = ""                  # 对应能力 CAP_*
    key: str = ""                  # f"{device_type}|{usage}"
    groups: List[GroupProfile] = field(default_factory=list)
    count: int = 0                 # 该声部涉及设备总数

    def to_dict(self) -> dict:
        return {"device_type": self.device_type, "usage": self.usage,
                "voice_class": self.voice_class, "cap": self.cap, "key": self.key,
                "count": self.count, "n_groups": len(self.groups)}


@dataclass
class VoiceProfile:
    """声部弹性画像(分位带,保留灵活范围,非僵硬规则)。"""
    speed_band: Tuple[float, float, float] = (60.0, 100.0, 180.0)  # p10/median/p90 DMX/s
    dur_band: Tuple[float, float, float] = (2.0, 6.0, 16.0)
    fade_in: float = 0.8
    fade_out: float = 0.8
    seq_tendency: float = 0.0      # 序列分组倾向 0-1
    motor_phase_tendency: float = 0.0
    n: int = 0                     # 样本数=置信度(0=内置默认)


@dataclass
class VoiceDecision:
    """某声部在某段的决策。"""
    voice_key: str = ""
    active: bool = False
    role: str = ROLE_SILENT
    mechanism: str = MECH_EFFECT   # static / effect / matrix
    shape: str = "flat"            # shape_engine 形状名 或 effect 路径
    topo_kind: str = "whole"       # topology 子选类型
    level: float = 0.0             # 输出幅度 0-1
    speed_dmx: float = 0.0         # 目标速度 DMX/s(来自弹性带)
    clip_dur: float = 0.0
    fade_in: float = 0.8
    fade_out: float = 0.8
    is_solo: bool = False
    solo_path: bool = False        # 电机走路径solo
    use_sequence: bool = False     # 分组追逐过渡
    use_motor_phase: bool = False
    direction: str = "L2R"         # 方向性
    color_mode: str = "white"      # RGB配色: white(默认) / pure(纯色) / color(彩色,仅高潮)
    base_hue: float = 0.0          # pure/color 的基色相 0..1
    preset_id: str = ""            # 真effect库 preset(real_effect机制时由手法池选定)
    preset_params: dict = field(default_factory=dict)   # 该 preset 的参数覆盖
    note: str = ""

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "voice_key", "active", "role", "mechanism", "shape", "topo_kind",
            "level", "speed_dmx", "clip_dur", "fade_in", "fade_out",
            "is_solo", "solo_path", "use_sequence", "use_motor_phase", "direction",
            "color_mode", "base_hue", "preset_id", "preset_params", "note")}


# ── 内置默认弹性画像(新项目无标杆数据时用,n=0低置信)───────────────────
# 速度带来自标杆实测(.codex_voice_lib.json)的代表值,作为同类设备的合理先验。
DEFAULT_VOICE_PROFILES: Dict[str, VoiceProfile] = {
    VC_MOTOR:        VoiceProfile((57.0, 100.0, 182.0), (2.0, 4.5, 16.0), 0.9, 0.7, 0.27, 0.27),
    VC_PUMP:         VoiceProfile((100.0, 200.0, 360.0), (2.0, 7.5, 16.0), 0.6, 0.6, 0.10, 0.0),
    VC_VALVE:        VoiceProfile((85.0, 100.0, 120.0), (1.3, 4.0, 15.0), 0.6, 0.7, 0.50, 0.0),
    VC_FOUNTAIN_RGB: VoiceProfile((70.0, 230.0, 400.0), (4.0, 10.0, 18.0), 0.6, 0.5, 0.0, 0.0),
    VC_MATRIX_RGB:   VoiceProfile((2800.0, 8000.0, 8700.0), (5.0, 15.0, 24.0), 1.1, 1.5, 0.0, 0.0),
    VC_DIMMER:       VoiceProfile((50.0, 150.0, 400.0), (2.0, 8.0, 16.0), 0.8, 0.8, 0.0, 0.0),
}


# ── 矩阵判定:仅真正的矩阵/水膜/水幕屏设备 → matrix_rgb,否则 fountain_rgb ────
# ★重要:喷泉设备(HJC/气爆)的 RGB 是"喷泉染色",绝不是矩阵画布!
#   矩阵只认"名字明确是矩阵/水膜/水幕/LED屏/网格屏"的设备。
#   不靠台数阈值/area形态判矩阵——那会把大规模散布的喷泉误判成矩阵(用户明确纠正)。
_MATRIX_KEYWORDS = (
    "矩阵", "水膜", "水幕", "网格屏", "格栅屏", "像素屏", "LED屏", "灯幕", "光幕",
    "matrix", "Matrix", "MATRIX", "grid", "Grid", "screen", "Screen",
    "pixel", "Pixel", "panel wall", "videowall", "ledwall",
)


def _is_matrix_rgb(device_type: str, groups: List[GroupProfile], total_count: int) -> bool:
    name = device_type or ""
    if any(k in name for k in _MATRIX_KEYWORDS):
        return True
    for g in groups:
        if any(t == "matrix" for t in (g.semantic_tags or [])):
            return True
        if any(k in (g.name or "") for k in _MATRIX_KEYWORDS):
            return True
    # ★正确判据=能力:有泵/电机/阀的是喷泉(RGB=喷泉染色,非矩阵);
    #   纯RGB设备(无泵/电机/阀)才是灯光画布/矩阵。这样绝不误伤喷泉。
    #   纯RGB + 规模较大(画布感)→ 矩阵;纯RGB但很少 → 当普通喷泉灯处理。
    has_fountain_cap = False
    for g in groups:
        gc = set(g.caps or [])
        if gc & {CAP_PUMP, CAP_MOTOR, CAP_VALVE}:
            has_fountain_cap = True
            break
    if not has_fountain_cap and total_count >= 200:
        return True   # 纯RGB大规模画布 = 矩阵(如 LED灯组960台)
    return False


def _voice_class_for(usage: str, device_type: str, groups: List[GroupProfile], count: int) -> str:
    if usage == USAGE_MOTOR:
        return VC_MOTOR
    if usage == USAGE_PUMP:
        return VC_PUMP
    if usage == USAGE_VALVE:
        return VC_VALVE
    if usage == USAGE_DIMMER:
        return VC_DIMMER
    if usage == USAGE_RGB:
        return VC_MATRIX_RGB if _is_matrix_rgb(device_type, groups, count) else VC_FOUNTAIN_RGB
    return VC_FOUNTAIN_RGB


# ── 枚举声部 ───────────────────────────────────────────────────────────────
def enumerate_voices(profile: ProjectProfile) -> List[Voice]:
    """从项目画像枚举所有真实存在的声部 (设备type × 用法)。

    一台多能力设备(如 HJC 电机+泵+RGB)展开成多个声部;矩阵RGB 与喷泉RGB 分两类。
    """
    # 按 device_type 聚合组(GroupProfile 有 caps;device_types 有 count/caps)
    # 组没有直接的 device_type 字段,用 caps 反推该组属于哪些用法,设备type用画像键。
    voices: List[Voice] = []
    dtypes = profile.device_types or {}
    # 每个 device_type 的 caps + count
    for dtype, info in dtypes.items():
        caps = list(info.get("caps", []) or [])
        count = int(info.get("count", 0) or 0)
        # 该 device_type 关联的组:精确按 group.device_type 匹配(不用cap重叠,避免跨类型串组)
        dgroups = [g for g in profile.groups if getattr(g, "device_type", "") == dtype]
        # 兜底:旧画像无 device_type 字段时退回 cap 交集(尽量不串)
        if not dgroups:
            dgroups = [g for g in profile.groups
                       if not getattr(g, "device_type", "") and set(g.caps or []) & set(caps)]
        for cap in caps:
            usage = _CAP_USAGE.get(cap)
            if not usage:
                continue
            # 该用法相关的组(组具备该 cap)
            ugroups = [g for g in dgroups if cap in (g.caps or [])]
            vc = _voice_class_for(usage, dtype, ugroups or dgroups, count)
            voices.append(Voice(
                device_type=dtype, usage=usage, voice_class=vc, cap=cap,
                key=f"{dtype}|{usage}", groups=ugroups or dgroups, count=count))
    return voices


def scope_voices(voices: List[Voice], scope_ids: Optional[set]) -> List[Voice]:
    """把声部限定到选中设备范围:过滤每个声部的组成员到 scope_ids 内,
    重算 count;成员全被过滤掉的声部丢弃。scope_ids 为空/None → 原样返回(全部)。

    用户要求:自动编曲只对选中的设备编,而非整页全部。
    """
    if not scope_ids:
        return voices
    scope = set(str(x) for x in scope_ids)
    out: List[Voice] = []
    for v in voices:
        new_groups = []
        total = 0
        for g in v.groups:
            kept = [m for m in (g.member_ids or []) if str(m) in scope]
            if not kept:
                continue
            # 复制组,成员限定到选中
            import copy as _copy
            ng = _copy.copy(g)
            ng.member_ids = kept
            ng.count = len(kept)
            new_groups.append(ng)
            total += len(kept)
        if total <= 0:
            continue   # 该声部无选中设备 → 不编
        nv = Voice(device_type=v.device_type, usage=v.usage, voice_class=v.voice_class,
                   cap=v.cap, key=v.key, groups=new_groups, count=total)
        out.append(nv)
    return out


# ── 加载弹性画像(标杆学到的优先,缺则内置默认)──────────────────────────
def load_voice_profiles(voices: List[Voice],
                        voice_lib_path: Optional[str] = None) -> Dict[str, VoiceProfile]:
    """为每个声部加载弹性画像。voice_lib(标杆学到)命中则用,否则用 voice_class 内置默认。"""
    lib: Dict[str, dict] = {}
    if voice_lib_path and os.path.isfile(voice_lib_path):
        try:
            with open(voice_lib_path, "r", encoding="utf-8") as f:
                lib = json.load(f)
        except Exception:
            lib = {}
    out: Dict[str, VoiceProfile] = {}
    for v in voices:
        ent = lib.get(v.key)
        sp = ent.get("speed") if isinstance(ent, dict) else None
        du = ent.get("dur") if isinstance(ent, dict) else None
        ok = (isinstance(ent, dict) and isinstance(sp, (list, tuple)) and len(sp) >= 3
              and isinstance(du, (list, tuple)) and len(du) >= 3)
        if ok:
            out[v.key] = VoiceProfile(
                speed_band=(float(sp[0]), float(sp[1]), float(sp[2])),
                dur_band=(float(du[0]), float(du[1]), float(du[2])),
                fade_in=float(ent.get("fade_in", 0.8)),
                fade_out=float(ent.get("fade_out", 0.8)),
                seq_tendency=float(ent.get("seq_frac", 0.0)),
                motor_phase_tendency=float(ent.get("motor_phase_frac", 0.0)),
                n=int(ent.get("n", 0)))
        else:
            base = DEFAULT_VOICE_PROFILES.get(v.voice_class, DEFAULT_VOICE_PROFILES[VC_FOUNTAIN_RGB])
            out[v.key] = VoiceProfile(
                speed_band=base.speed_band, dur_band=base.dur_band,
                fade_in=base.fade_in, fade_out=base.fade_out,
                seq_tendency=base.seq_tendency, motor_phase_tendency=base.motor_phase_tendency,
                n=0)
    return out


# ── 弹性带分段插值:把 0-1 的"强度位置"映射到 [p10,median,p90] ──────────────
def piecewise_band(band: Tuple[float, float, float], t: float) -> float:
    """t∈[0,1]:0→p10, 0.5→median, 1→p90。中位落在 t=0.5(非线性区间正确)。"""
    p10, med, p90 = band
    t = max(0.0, min(1.0, t))
    if t <= 0.5:
        return p10 + (med - p10) * (t / 0.5)
    return med + (p90 - med) * ((t - 0.5) / 0.5)


# ── 单声部决策 ─────────────────────────────────────────────────────────────
def decide_voice(voice: Voice, profile_band: VoiceProfile,
                 char: Any, global_soft: float = 1.0,
                 block_ordinal: int = 0, prev_preset: str = "") -> VoiceDecision:
    """给定声部 + 其弹性画像 + 一段音乐性格 char(CharacterSegment),决定本段表现。

    char 需有:energy_rank, perc_density, has_melody, is_build, is_drop, rising,
              dominant_chord_quality, t_start, t_end(用 getattr 容错)。
    global_soft:全局降速系数(柔美曲<1),作用于速度。
    """
    d = VoiceDecision(voice_key=voice.key)
    e = float(getattr(char, "energy_rank", 0.5) or 0.5)
    perc = float(getattr(char, "perc_density", 0.0) or 0.0)
    has_mel = bool(getattr(char, "has_melody", False))
    is_build = bool(getattr(char, "is_build", False))
    is_drop = bool(getattr(char, "is_drop", False))
    seg_dur = float(getattr(char, "t_end", 0.0) or 0.0) - float(getattr(char, "t_start", 0.0) or 0.0)
    vc = voice.voice_class

    # 强度位置 drive:每声部对不同音乐特征敏感(灵活,非锁死)
    if vc == VC_MOTOR:
        # 电机="演员舞蹈":有旋律时强主奏;无旋律也是常驻表现声部(中能即上场,
        # 跟音乐律动起舞)。标杆里电机是用得最多的声部,不能只在检测到旋律时才动。
        drive = 0.55 * e + (0.45 if has_mel else 0.30) + (0.10 if is_build else 0.0)
        drive = min(1.0, drive)
    elif vc == VC_PUMP:
        drive = 0.5 * e + 0.5 * min(1.0, perc)              # 泵跟能量+打击
    elif vc == VC_VALVE:
        drive = min(1.0, perc)                              # 阀跟打击节奏
    elif vc == VC_MATRIX_RGB:
        drive = e                                           # 矩阵画布跟能量
    elif vc == VC_FOUNTAIN_RGB:
        drive = 0.6 * e + 0.4 * (1.0 if has_mel else 0.5)   # 喷泉染色跟和声
    else:  # dimmer
        drive = e

    # 速度:弹性带按 drive 插值,再乘全局降速
    d.speed_dmx = piecewise_band(profile_band.speed_band, drive) * float(global_soft)
    # 时长:低能段更长(舒缓),高能段更短(切碎),但夹在段时长内
    dur_t = 1.0 - 0.5 * drive
    d.clip_dur = min(max(piecewise_band(profile_band.dur_band, dur_t), 1.0),
                     seg_dur if seg_dur > 0.5 else profile_band.dur_band[1])
    d.fade_in = profile_band.fade_in * (1.3 if e < 0.4 else 1.0)   # 柔美更长淡入
    d.fade_out = profile_band.fade_out * (1.3 if e < 0.4 else 1.0)
    d.level = max(0.15, min(1.0, 0.4 + 0.6 * drive))

    # ── 输出机制 ──
    # ★大多数 programmer 静态,动态是少数,但不能掐死(对标标杆泵≈63%静态/37%动态):
    #   泵默认静态山形缓开,中高能或强打击其一即可动态突发(OR,非AND,否则永不触发);
    #   喷泉自带RGB默认静态染色,中高能才流动;矩阵走matrix;电机/阀按能量。
    if vc == VC_PUMP:
        d.mechanism = MECH_EFFECT if (e >= 0.55 or perc >= 0.45) else MECH_STATIC
    elif vc == VC_MATRIX_RGB:
        d.mechanism = MECH_MATRIX
    elif vc == VC_MOTOR:
        d.mechanism = MECH_EFFECT     # 电机=演员舞蹈,本就是动态运动
    elif vc == VC_FOUNTAIN_RGB:
        # 喷泉自带灯:默认静态染色(跟泵),中高能才流动变色
        d.mechanism = MECH_EFFECT if e >= 0.55 else MECH_STATIC
    elif vc == VC_VALVE:
        d.mechanism = MECH_EFFECT if (perc >= 0.4 or e >= 0.6) else MECH_STATIC
    else:
        d.mechanism = MECH_STATIC     # 调光等:默认静态

    # ── RGB 配色(对标标杆:白61%+纯色22%+彩色仅16%,气爆白70%)──
    #   默认白色;中能可纯色;只有高能/高潮(is_drop 或 e≥0.78)才用彩色。
    #   电机/泵/阀不涉及配色(非RGB声部)。
    if vc in (VC_FOUNTAIN_RGB, VC_MATRIX_RGB, VC_DIMMER):
        # 色相由 声部key + 段落能量 派生(确定性,不同段不同色,无需seed)。
        # 用稳定哈希(非内置hash,后者按进程随机化会破坏可复现)。
        _kh = 0
        for _ch in voice.key:
            _kh = (_kh * 131 + ord(_ch)) & 0xFFFF
        d.base_hue = ((_kh + int(round(e * 360))) % 360) / 360.0
        if is_drop or e >= 0.78:
            d.color_mode = "color"      # 高潮才彩色流转
        elif e >= 0.5:
            d.color_mode = "pure"       # 中能纯色
        else:
            d.color_mode = "white"      # 低能白色(克制)
        # 矩阵画布高潮更鲜艳,低能也偏白
        if vc == VC_MATRIX_RGB and e < 0.45:
            d.color_mode = "white"
    else:
        d.color_mode = "white"          # 非RGB声部无所谓,占位

    # ── 空间形状 ──
    if vc == VC_PUMP:
        if d.mechanism == MECH_STATIC:
            d.shape = "mountain" if e >= 0.35 else "gradient_x"   # 山形/横向渐变
        else:
            d.shape = "multi_wave"                                # 多波叠加,空间更丰富
    elif vc == VC_VALVE:
        d.shape = "two_high_two_low" if perc > 0.6 else "odd_even"
    elif vc == VC_MOTOR:
        d.shape = "circle" if e >= 0.5 else "breath"             # 高能转圈,柔美呼吸摆
    elif vc == VC_MATRIX_RGB:
        d.shape = "multi_wave" if e >= 0.5 else "gradient_x"      # 高能多波画布
    else:
        d.shape = "multi_wave"   # 喷泉RGB/调光:多波叠加更丰富

    # ── 拓扑子选 ──
    if is_drop or e >= 0.85:
        d.topo_kind = "whole"                  # 高潮全开
    elif vc == VC_VALVE and perc > 0.5:
        d.topo_kind = "odd_even"               # 阀奇偶呼应
    elif e < 0.4:
        d.topo_kind = "arc"                    # 柔美只开一段弧
    else:
        d.topo_kind = "circle"

    # ── 电机 solo / 幅度 / 过渡 ──
    if vc == VC_MOTOR:
        d.solo_path = has_mel and e < 0.6      # 旋律段电机路径solo
        d.is_solo = d.solo_path
        d.use_motor_phase = (profile_band.motor_phase_tendency > 0.15) or is_build
    # ── 序列分组(阀/泵突发倾向追逐)──
    d.use_sequence = (profile_band.seq_tendency > 0.15) or (vc == VC_VALVE and perc > 0.5)
    # ── 方向 ──
    d.direction = "L2R" if (int(round(e * 10)) % 2 == 0) else "R2L"

    # 角色暂置 support,由 arrange_voices 统一定 lead/silent
    d.role = ROLE_SUPPORT
    d.active = True
    d.note = f"{voice.usage}·{getattr(char,'character','')}"

    # ── 多样化选 preset(真effect库):动态非矩阵声部从手法池选 preset_id,
    #    走 real_effect 机制(解锁1611真preset,变化丰富)。静态/矩阵/电机不走此路。
    #    选择确定性(吃 voice.key+能量档+block_ordinal),情感块内连贯、相邻块避重复。
    if d.mechanism == MECH_EFFECT and vc != VC_MATRIX_RGB:
        pid = _pick_preset_from_pool(voice, vc, e, block_ordinal, prev_preset)
        if pid:
            d.preset_id = pid
            d.mechanism = "real_effect"
    return d


def _pick_preset_from_pool(voice: Any, vc: str, energy: float,
                           block_ordinal: int, prev_preset: str) -> str:
    """从声部手法池确定性加权选 preset_id。骨架优先;能量档影响;相邻块避重复。
    池不可用时返回 ''(调用方回退自制 shape)。"""
    try:
        from .preset_pools import get_preset_pools
        pp = get_preset_pools()
    except Exception:
        return ""
    pool = pp.pool_for(vc)
    if not pool:
        return ""
    skel_n = pp.skeleton_count(vc)
    # 能量档:低能偏骨架前段(干净柔美),高能可取池更靠后(激烈/填充)
    tier = 0 if energy < 0.4 else (1 if energy < 0.7 else 2)
    # 确定性 rng:吃 voice.key + tier + block_ordinal(同情感块同 preset=连贯)
    kh = 0
    for ch in (voice.key + "|" + str(tier) + "|" + str(block_ordinal)):
        kh = (kh * 131 + ord(ch)) & 0x7FFFFFFF
    import random as _r
    rng = _r.Random(kh)
    # 候选范围:骨架(权重高)+按 tier 扩展填充
    skel = pool[:skel_n] if skel_n > 0 else pool[:min(8, len(pool))]
    if tier == 0:
        cands = skel or pool[:8]
    elif tier == 1:
        cands = (skel + pool[skel_n:skel_n + 40]) or pool[:40]
    else:
        cands = (skel + pool[skel_n:skel_n + 120]) or pool[:120]
    cands = [c for c in cands if c] or pool
    # 加权:骨架×2;相邻块用过的同 preset 强避(×0.1)
    weights = []
    for c in cands:
        w = 2.0 if c in skel else 1.0
        if c == prev_preset:
            w *= 0.1
        weights.append(w)
    total = sum(weights) or 1.0
    r = rng.random() * total
    acc = 0.0
    for c, w in zip(cands, weights):
        acc += w
        if r <= acc:
            return c
    return cands[-1]


# ── 合奏总调度 ─────────────────────────────────────────────────────────────
def arrange_voices(voices: List[Voice], profiles: Dict[str, VoiceProfile],
                   char: Any, global_soft: float = 1.0,
                   block_ordinal: int = 0,
                   prev_presets: Optional[Dict[str, str]] = None) -> Dict[str, VoiceDecision]:
    """对一段音乐,决定所有声部的协同:谁主奏/陪衬/留白 + 织体上限 + 留白。

    体现"混合编曲":不是全员齐上,按能量定同时活跃声部数;低能多留白。
    block_ordinal/prev_presets:情感块序号 + 上一块各声部用过的preset(多样化选择用)。
    """
    e = float(getattr(char, "energy_rank", 0.5) or 0.5)
    is_drop = bool(getattr(char, "is_drop", False))
    has_mel = bool(getattr(char, "has_melody", False))
    prev_presets = prev_presets or {}

    # 先给每声部出基础决策
    decs: Dict[str, VoiceDecision] = {}
    for v in voices:
        decs[v.key] = decide_voice(v, profiles[v.key], char, global_soft,
                                   block_ordinal=block_ordinal,
                                   prev_preset=prev_presets.get(v.key, ""))

    # ── 检索式模仿(软先验,按相似度门控)──────────────────────────────
    # 找标杆里音乐最像的案例,若够像(weight高)→ 把"你当时开了的声部类"作为先验
    # 抬高对应声部的 level(更可能活跃/被选);若不像(不同风格,weight趋0)→ 几乎不影响,
    # 由音乐自身驱动。绝不硬覆盖,保证不同风格不雷同。
    case_classes: set = set()
    case_w = 0.0
    try:
        from .case_retrieval import get_case_library, char_to_feat
        lib = get_case_library()
        if lib.available:
            case, case_w = lib.nearest_weighted(char_to_feat(char))
            if case and case_w > 0.12:   # 太不像就不参考
                case_classes = set((case.get("voices", {}) or {}).keys())
    except Exception:
        case_classes = set(); case_w = 0.0

    if case_classes and case_w > 0.12:
        for v in voices:
            # 该声部类在相似标杆案例里活跃 → 软抬 level(权重∝相似度);否则不动
            if v.voice_class in case_classes:
                d = decs[v.key]
                # 最多抬 0.3,且乘相似度权重 → 不像的音乐几乎无影响
                d.level = min(1.0, d.level + 0.30 * case_w)

    # 织体上限:低能→少声部(留白),高能→多声部齐奏
    n_voices = len(voices)
    if is_drop or e >= 0.85:
        max_active = n_voices                      # tutti
    elif e >= 0.55:
        max_active = max(2, int(round(n_voices * 0.7)))
    elif e >= 0.35:
        max_active = max(1, int(round(n_voices * 0.45)))
    else:
        max_active = max(1, int(round(n_voices * 0.3)))   # 大量留白

    # 声部优先级:按 drive(=该声部本段强度)排序,取前 max_active 个活跃,其余留白
    def _drive(v: Voice) -> float:
        return decs[v.key].level
    ranked = sorted(voices, key=_drive, reverse=True)
    active_keys = set(v.key for v in ranked[:max_active])

    # 主奏:电机="演员舞蹈",在中低能段(舒缓/抒情)优先做主奏独白;
    # 高能/drop 段让快速画布(矩阵/RGB)或泵突发主奏。其余活跃=陪衬。
    lead_key = None
    motors = [v for v in ranked if v.voice_class == VC_MOTOR and v.key in active_keys]
    if motors and (has_mel or e < 0.6):
        lead_key = motors[0].key      # 旋律段 或 中低能段 → 电机领舞
    if lead_key is None and ranked:
        for v in ranked:
            if v.key in active_keys:
                lead_key = v.key
                break

    for v in voices:
        dec = decs[v.key]
        if v.key not in active_keys:
            dec.active = False
            dec.role = ROLE_SILENT
            dec.level = 0.0
        elif v.key == lead_key:
            dec.role = ROLE_LEAD
            dec.level = max(dec.level, 0.85)
        else:
            dec.role = ROLE_SUPPORT
            dec.level = min(dec.level, 0.55)   # 陪衬压低

    # ★★设备内通道协同(泵为锚):同一台设备的泵/电机/自带灯是"一个动作的三面"
    #   (泵喷水·电机瞄方向·灯染这股水),物理上必须协同。标杆实测39/47 HJC clip
    #   是 RGB+电机+泵 三者共现。规则:
    #   - 该设备有泵声部 → 泵的活跃态/拓扑/方向 作为锚,组内电机/RGB 跟随:
    #     泵活跃则它们也倾向活跃并继承泵的拓扑子选+方向(同一股水);泵静止则克制。
    #   - 例外:纯灯设备(无泵)→ 灯独立编,不受此约束。
    by_device: Dict[str, List[Voice]] = {}
    for v in voices:
        by_device.setdefault(v.device_type, []).append(v)
    for dtype, dvoices in by_device.items():
        pump_v = next((v for v in dvoices if v.voice_class == VC_PUMP), None)
        if pump_v is None:
            continue   # 纯灯/无泵设备:组内各声部独立,不协同
        pump_dec = decs[pump_v.key]
        for v in dvoices:
            if v.key == pump_v.key:
                continue
            dec = decs[v.key]
            if v.voice_class not in (VC_MOTOR, VC_FOUNTAIN_RGB):
                continue   # 只协同同设备的电机和自带喷泉灯
            if pump_dec.active:
                # 泵活跃 → 电机/灯跟随同一股水:继承泵的拓扑子选+方向(协同),
                #   若原本被留白则被泵带起来活跃(陪衬级),形成三面一体的动作。
                dec.topo_kind = pump_dec.topo_kind
                dec.direction = pump_dec.direction
                if not dec.active:
                    dec.active = True
                    dec.role = ROLE_SUPPORT
                    dec.level = max(0.3, pump_dec.level * 0.7)
            else:
                # 泵静止 → 同设备电机/灯克制(不喧宾夺主,除非自己是主奏)
                if dec.role != ROLE_LEAD:
                    dec.level = min(dec.level, 0.35)
    return decs

