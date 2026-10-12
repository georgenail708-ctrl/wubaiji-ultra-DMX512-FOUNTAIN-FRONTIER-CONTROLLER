"""
experience_table — 编曲经验知识层

数据来源：对真实 .dmxt 工程（含 To The Moon 标杆项目）的挖掘提炼。
两部分经验：
  1. SECTION_PRESET：段落×设备类型 → 常用 preset 候选（从 audio_bridge_state.preset_sets 挖出）
  2. SECTION_ROLE：段落 → 各子系统的编排角色基线（主奏/陪衬/留白）+ 专业参数基线

这是"按真实项目经验调参"的内置默认；托管也可在运行时从当前项目学习覆盖（learn_from_project）。
纯 core，无 UI 依赖。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


# 段落规范名
SECTIONS = ["intro", "verse", "build", "chorus", "drop", "breakdown", "outro"]

# 子系统
SUB_LIGHT = "light"
SUB_PUMP = "pump"
SUB_MOTOR = "motor"
SUB_VALVE = "valve"
SUBSYSTEMS = [SUB_LIGHT, SUB_PUMP, SUB_MOTOR, SUB_VALVE]

# 编排角色
ROLE_LEAD = "lead"        # 主奏（solo / 强效果，占舞台中心）
ROLE_SUPPORT = "support"  # 陪衬（铺底，压低音量）
ROLE_SILENT = "silent"    # 留白（静默）

# 陪衬级输出系数（主奏=1.0，陪衬压到这个，留白=0）
SUPPORT_LEVEL = 0.35


# ── 1. 段落×设备 → 常用 preset（从真实项目挖出，已验证）──────────────────
# 每项是候选列表，权重靠前优先。这是你真实配置过的手法。
SECTION_PRESET: Dict[str, Dict[str, List[str]]] = {
    "intro": {
        SUB_LIGHT: ["fader_breath", "smooth_breathe", "wave_sine"],
        SUB_PUMP:  ["pump_pressure_wave"],
        SUB_MOTOR: ["cur_ms_circles1"],
        SUB_VALVE: ["valve_pulse"],
    },
    "verse": {
        # R173: audio_energy 下架 → atx_energy_floor（真音频地板跟随，语义对应）。
        SUB_LIGHT: ["atx_energy_floor", "wave_sine", "chase_single"],
        SUB_PUMP:  ["pump_pressure_wave", "fountain_height_gradient", "fountain_running"],
        SUB_MOTOR: ["cur_ms_circles1", "cur_ms_spirals1"],
        SUB_VALVE: ["valve_sequential", "valve_pulse"],
    },
    "build": {
        # R173: audio_bpm_chase 下架 → atx_beat_chase_sequential（真音频节拍追逐，语义对应）。
        SUB_LIGHT: ["chase_single", "atx_beat_chase_sequential", "staircase"],
        SUB_PUMP:  ["fountain_height_gradient", "pump_pressure_wave"],
        SUB_MOTOR: ["cur_ms_spirals1", "cur_ms_circles1"],
        SUB_VALVE: ["valve_sequential", "valve_pulse"],
    },
    "chorus": {
        # R173: audio_color_beat/color_explosion 下架。本表每槽只是单一 preset id（无
        # 类似 bridge PresetPoolEntry 的 ch_type 覆盖机制），故不做 R/G/B 拆分，各取一个
        # 代表性 atx_* 真音频替代：beat_pulse 对应逐拍变色性格，peak_flash 对应峰值迸发
        # 性格，二者默认写 dimmer 通道（与本表其余 SUB_LIGHT 项一致）。
        SUB_LIGHT: ["atx_beat_pulse", "atx_peak_flash", "wave_rainbow"],
        SUB_PUMP:  ["fountain_burst", "fountain_tidal", "fountain_running"],
        # R173: audio_beat_joystick 下架 → cur_audio_motor_audio_shake（无 atx 双轴等价物，
        # 见 music_to_effect_bridge 同批改动的架构缺口说明）。
        SUB_MOTOR: ["cur_ms_spirals1", "cur_audio_motor_audio_shake", "motor_random_jump"],
        # R173: audio_beat_valve 下架 → atx_beat_strobe（真音频节拍频闪，duty 默认 0.35
        # 接近旧 open_ratio=0.3）。
        SUB_VALVE: ["valve_sequential", "atx_beat_strobe", "valve_pulse"],
    },
    "drop": {
        SUB_LIGHT: ["chase_strobe_global", "atx_peak_flash", "dimmer_pulse"],
        SUB_PUMP:  ["fountain_burst", "atx_beat_strobe"],
        SUB_MOTOR: ["cur_audio_motor_audio_shake", "motor_random_jump"],
        SUB_VALVE: ["atx_beat_strobe", "valve_pulse"],
    },
    "breakdown": {
        SUB_LIGHT: ["fader_breath", "smooth_breathe", "wave_sine"],
        SUB_PUMP:  ["pump_pressure_wave"],
        SUB_MOTOR: ["cur_ms_circles1"],
        SUB_VALVE: ["valve_pulse"],
    },
    "outro": {
        SUB_LIGHT: ["fader_linear", "fader_breath"],
        SUB_PUMP:  ["pump_pressure_wave"],
        SUB_MOTOR: ["cur_ms_circles1"],
        SUB_VALVE: ["valve_pulse"],
    },
}


# ── 2. 段落 → 编排角色基线 + 参数 ────────────────────────────────────────
# 体现"混合编曲"哲学：真实编曲不是每类设备都齐上，而是主奏/陪衬/留白分配。
# speed/能量是该段的强度基线（0..1），供参数调节缩放。
@dataclass
class SectionPlan:
    energy: float = 0.5            # 该段整体强度基线 0..1
    speed_scale: float = 1.0      # 该段效果速度基线
    # 各子系统默认角色（具体还会被项目画像微调）
    roles: Dict[str, str] = field(default_factory=dict)
    # 该段倾向的"主奏"子系统优先序（决定 solo 给谁）
    lead_pref: List[str] = field(default_factory=list)
    # 是否允许大面积留白
    allow_silence: bool = False
    note: str = ""


# 经验基线：体现真人编曲的段落动态
SECTION_PLAN: Dict[str, SectionPlan] = {
    "intro": SectionPlan(
        energy=0.25, speed_scale=0.6,
        roles={SUB_LIGHT: ROLE_LEAD, SUB_PUMP: ROLE_SUPPORT, SUB_MOTOR: ROLE_SILENT, SUB_VALVE: ROLE_SILENT},
        lead_pref=[SUB_LIGHT, SUB_PUMP], allow_silence=True,
        note="开场克制：灯光呼吸主奏，泵轻陪衬，电机/阀留白"),
    "verse": SectionPlan(
        energy=0.45, speed_scale=0.85,
        roles={SUB_PUMP: ROLE_LEAD, SUB_LIGHT: ROLE_SUPPORT, SUB_MOTOR: ROLE_SUPPORT, SUB_VALVE: ROLE_SILENT},
        lead_pref=[SUB_PUMP, SUB_MOTOR], allow_silence=True,
        note="主歌：泵或电机主奏走旋律，灯陪衬铺底"),
    "build": SectionPlan(
        energy=0.7, speed_scale=1.2,
        roles={SUB_LIGHT: ROLE_LEAD, SUB_PUMP: ROLE_LEAD, SUB_MOTOR: ROLE_SUPPORT, SUB_VALVE: ROLE_SUPPORT},
        lead_pref=[SUB_LIGHT, SUB_PUMP], allow_silence=False,
        note="推进：能量渐涨，灯+泵齐进，电机/阀陪衬蓄势"),
    "chorus": SectionPlan(
        energy=0.9, speed_scale=1.3,
        roles={SUB_LIGHT: ROLE_LEAD, SUB_PUMP: ROLE_LEAD, SUB_MOTOR: ROLE_LEAD, SUB_VALVE: ROLE_LEAD},
        lead_pref=[SUB_PUMP, SUB_LIGHT, SUB_MOTOR], allow_silence=False,
        note="副歌：全员齐上，多子系统主奏"),
    "drop": SectionPlan(
        energy=1.0, speed_scale=1.5,
        roles={SUB_LIGHT: ROLE_LEAD, SUB_PUMP: ROLE_LEAD, SUB_MOTOR: ROLE_LEAD, SUB_VALVE: ROLE_LEAD},
        lead_pref=[SUB_PUMP, SUB_MOTOR, SUB_LIGHT], allow_silence=False,
        note="高潮炸点：全力齐发，强节拍"),
    "breakdown": SectionPlan(
        energy=0.3, speed_scale=0.7,
        roles={SUB_MOTOR: ROLE_LEAD, SUB_LIGHT: ROLE_SUPPORT, SUB_PUMP: ROLE_SILENT, SUB_VALVE: ROLE_SILENT},
        lead_pref=[SUB_MOTOR, SUB_LIGHT], allow_silence=True,
        note="间奏/break：电机路径solo独白，其余留白"),
    "outro": SectionPlan(
        energy=0.2, speed_scale=0.5,
        roles={SUB_LIGHT: ROLE_LEAD, SUB_PUMP: ROLE_SUPPORT, SUB_MOTOR: ROLE_SILENT, SUB_VALVE: ROLE_SILENT},
        lead_pref=[SUB_LIGHT], allow_silence=True,
        note="收尾：灯光渐弱主奏，余者退场"),
}


def get_section_plan(section: str) -> SectionPlan:
    return SECTION_PLAN.get(section, SECTION_PLAN["verse"])


def get_preset_candidates(section: str, subsystem: str) -> List[str]:
    return SECTION_PRESET.get(section, {}).get(subsystem, [])


def learn_from_project(project: Any) -> Dict[str, Dict[str, List[str]]]:
    """从当前项目的 audio_bridge_state.preset_sets 学习段落×设备 preset 偏好，
    返回可覆盖 SECTION_PRESET 的 dict（不修改全局，调用方决定是否合并）。"""
    learned: Dict[str, Dict[str, List[str]]] = {}
    abs_state = getattr(project, "audio_bridge_state", None) or {}
    if isinstance(project, dict):
        abs_state = project.get("audio_bridge_state", {})
    ps = abs_state.get("preset_sets", {}) if isinstance(abs_state, dict) else {}
    for tt, rows in (ps or {}).items():
        if tt not in SUBSYSTEMS:
            continue
        for row in rows or []:
            sec = row.get("section", "")
            if not sec:
                continue
            pids = []
            for p in (row.get("section_presets", []) or []) + (row.get("bar_presets", []) or []):
                pid = p.get("preset_id")
                if pid and pid not in pids:
                    pids.append(pid)
            if pids:
                learned.setdefault(sec, {}).setdefault(tt, [])
                for pid in pids:
                    if pid not in learned[sec][tt]:
                        learned[sec][tt].append(pid)
    return learned
