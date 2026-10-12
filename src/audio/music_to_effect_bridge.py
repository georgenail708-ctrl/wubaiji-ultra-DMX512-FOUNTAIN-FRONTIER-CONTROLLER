"""
music_to_effect_bridge.py — 音乐结构 → effect 自动调度桥 (v27)
================================================================
v24 重构：
  1. 移除单一 track_type，改为多效果类型并行：
     每种效果类型（light/pump/motor/valve/color）独立启用/禁用
  2. 每种效果类型独立维护当前预设状态
  3. 结构变化 → 同时为所有启用的类型切换预设
  4. 小节变化 → 同时为所有启用的类型切换具体预设
  5. 每种类型发出独立的 effect_apply_request 信号
  6. 向下兼容旧版 to_dict/load_dict
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

from .music_structure_analyzer import (
    MusicStructureEvent, MusicStructureAnalyzer, SectionLabel,
)

# ─────────────────────────────────────────────────────────────────────────────
#  轨道类型定义
# ─────────────────────────────────────────────────────────────────────────────
TRACK_TYPES = {
    "light":  {"ch_types": ["dimmer", "R", "G", "B", "W"],
               "label": "灯光", "icon": "L"},
    "pump":   {"ch_types": ["pump"],
               "label": "水泵/变频", "icon": "P"},
    "motor":  {"ch_types": ["motor_x", "motor_y"],
               "label": "数控电机", "icon": "M"},
    "valve":  {"ch_types": ["valve"],
               "label": "电磁阀", "icon": "V"},
    "color":  {"ch_types": ["R", "G", "B"],
               "label": "颜色", "icon": "C"},
}


# ─────────────────────────────────────────────────────────────────────────────
#  数据结构
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class EffectMapping:
    """一条映射规则（兼容旧版）。"""
    trigger: str
    preset_id: str = ""
    ch_types: List[str] = field(default_factory=list)
    sort_mode: str = "index"
    params_override: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    min_interval_s: float = 1.5
    priority: float = 1.0

    def to_dict(self) -> dict:
        return {
            "trigger": self.trigger,
            "preset_id": self.preset_id,
            "ch_types": list(self.ch_types),
            "sort_mode": self.sort_mode,
            "params_override": dict(self.params_override),
            "enabled": bool(self.enabled),
            "min_interval_s": float(self.min_interval_s),
            "priority": float(self.priority),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EffectMapping":
        return cls(
            trigger=str(d.get("trigger", "")),
            preset_id=str(d.get("preset_id", "")),
            ch_types=list(d.get("ch_types", []) or []),
            sort_mode=str(d.get("sort_mode", "index")),
            params_override=dict(d.get("params_override", {}) or {}),
            enabled=bool(d.get("enabled", True)),
            min_interval_s=float(d.get("min_interval_s", 1.5)),
            priority=float(d.get("priority", 1.0)),
        )


@dataclass
class PresetPoolEntry:
    """预设池中的一个条目。"""
    preset_id: str
    weight: float = 1.0
    params_override: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SectionPresetSet:
    """一个结构段落对应的预设套。"""
    section: str = ""
    track_type: str = "light"
    section_presets: List[PresetPoolEntry] = field(default_factory=list)
    bar_presets: List[PresetPoolEntry] = field(default_factory=list)
    ch_types: List[str] = field(default_factory=list)
    sort_mode: str = "index"
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "section": self.section,
            "track_type": self.track_type,
            "section_presets": [
                {"preset_id": p.preset_id, "weight": p.weight,
                 "params_override": dict(p.params_override)}
                for p in self.section_presets
            ],
            "bar_presets": [
                {"preset_id": p.preset_id, "weight": p.weight,
                 "params_override": dict(p.params_override)}
                for p in self.bar_presets
            ],
            "ch_types": list(self.ch_types),
            "sort_mode": self.sort_mode,
            "enabled": bool(self.enabled),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SectionPresetSet":
        sp = [PresetPoolEntry(
            preset_id=p.get("preset_id", ""),
            weight=float(p.get("weight", 1.0)),
            params_override=dict(p.get("params_override", {}))
        ) for p in d.get("section_presets", [])]
        bp = [PresetPoolEntry(
            preset_id=p.get("preset_id", ""),
            weight=float(p.get("weight", 1.0)),
            params_override=dict(p.get("params_override", {}))
        ) for p in d.get("bar_presets", [])]
        return cls(
            section=str(d.get("section", "")),
            track_type=str(d.get("track_type", "light")),
            section_presets=sp, bar_presets=bp,
            ch_types=list(d.get("ch_types", []) or []),
            sort_mode=str(d.get("sort_mode", "index")),
            enabled=bool(d.get("enabled", True)),
        )


# ─────────────────────────────────────────────────────────────────────────────
#  默认预设套
# ─────────────────────────────────────────────────────────────────────────────
def _default_light_preset_sets():
    return [
        SectionPresetSet(section=SectionLabel.INTRO, track_type="light",
            section_presets=[PresetPoolEntry("fader_breath"), PresetPoolEntry("smooth_breathe")],
            bar_presets=[PresetPoolEntry("fader_breath"), PresetPoolEntry("wave_sine")],
            ch_types=["dimmer"], sort_mode="linear_x"),
        SectionPresetSet(section=SectionLabel.VERSE, track_type="light",
            section_presets=[PresetPoolEntry("atx_energy_floor"), PresetPoolEntry("wave_sine")],
            bar_presets=[
                PresetPoolEntry("atx_energy_floor"), PresetPoolEntry("chase_single"),
                PresetPoolEntry("wave_triangle"),
            ],
            ch_types=["dimmer"], sort_mode="index"),
        SectionPresetSet(section=SectionLabel.BUILD, track_type="light",
            section_presets=[PresetPoolEntry("chase_single"), PresetPoolEntry("atx_beat_chase_sequential")],
            bar_presets=[
                PresetPoolEntry("chase_single"), PresetPoolEntry("staircase"),
                PresetPoolEntry("atx_beat_chase_sequential"),
            ],
            ch_types=["dimmer"], sort_mode="linear_x"),
        SectionPresetSet(section=SectionLabel.CHORUS, track_type="light",
            # R173: audio_color_beat/color_explosion 下架，按 R/G/B 角色拆成 6 条 atx_* 真音频
            # 单通道替代（atx_* 每实例只写一个 ch_type，无法像旧版一次性写 R+G+B，见音频
            # 精编报告"架构缺口"说明）；beat_pulse/offbeat/band_high 对应 audio_color_beat
            # 的逐拍变色性格，peak_flash/onset_ripple/beat_euclid_ring 对应 color_explosion
            # 的峰值迸发性格；wave_rainbow 保留，覆盖"同时真彩虹"场景。
            section_presets=[
                PresetPoolEntry("atx_beat_pulse", params_override={"ch_type": "R"}),
                PresetPoolEntry("atx_beat_offbeat", params_override={"ch_type": "G"}),
                PresetPoolEntry("atx_band_high", params_override={"ch_type": "B"}),
                PresetPoolEntry("atx_peak_flash", params_override={"ch_type": "R"}),
                PresetPoolEntry("atx_onset_ripple", params_override={"ch_type": "G"}),
                PresetPoolEntry("atx_beat_euclid_ring", params_override={"ch_type": "B"}),
                PresetPoolEntry("wave_rainbow"),
            ],
            bar_presets=[
                PresetPoolEntry("atx_beat_pulse", params_override={"ch_type": "R"}),
                PresetPoolEntry("atx_beat_offbeat", params_override={"ch_type": "G"}),
                PresetPoolEntry("atx_band_high", params_override={"ch_type": "B"}),
                PresetPoolEntry("atx_peak_flash", params_override={"ch_type": "R"}),
                PresetPoolEntry("atx_onset_ripple", params_override={"ch_type": "G"}),
                PresetPoolEntry("atx_beat_euclid_ring", params_override={"ch_type": "B"}),
                PresetPoolEntry("chase_strobe_global"), PresetPoolEntry("wave_rainbow"),
            ],
            ch_types=["R", "G", "B"], sort_mode="index"),
        SectionPresetSet(section=SectionLabel.DROP, track_type="light",
            # R173: color_explosion 下架；本行 ch_types=dimmer（非 RGB），用 atx_peak_flash
            # 承接"鼓点峰值迸发"性格，无需 R/G/B 拆分。
            section_presets=[PresetPoolEntry("chase_strobe_global"), PresetPoolEntry("atx_peak_flash")],
            bar_presets=[PresetPoolEntry("chase_strobe_global"), PresetPoolEntry("dimmer_pulse"), PresetPoolEntry("strobe_duty")],
            ch_types=["dimmer"], sort_mode="index"),
        SectionPresetSet(section=SectionLabel.BREAKDOWN, track_type="light",
            section_presets=[PresetPoolEntry("fader_breath"), PresetPoolEntry("smooth_breathe")],
            bar_presets=[PresetPoolEntry("fader_breath"), PresetPoolEntry("wave_sine")],
            ch_types=["dimmer"], sort_mode="linear_y"),
        SectionPresetSet(section=SectionLabel.OUTRO, track_type="light",
            section_presets=[PresetPoolEntry("fader_linear"), PresetPoolEntry("fader_breath")],
            bar_presets=[PresetPoolEntry("fader_linear")],
            ch_types=["dimmer"], sort_mode="index"),
    ]

def _default_pump_preset_sets():
    return [
        SectionPresetSet(section=SectionLabel.INTRO, track_type="pump",
            section_presets=[PresetPoolEntry("pump_pressure_wave")],
            bar_presets=[PresetPoolEntry("pump_pressure_wave")],
            ch_types=["pump"], sort_mode="index"),
        SectionPresetSet(section=SectionLabel.VERSE, track_type="pump",
            section_presets=[PresetPoolEntry("pump_pressure_wave"), PresetPoolEntry("fountain_height_gradient")],
            bar_presets=[PresetPoolEntry("pump_pressure_wave"), PresetPoolEntry("fountain_running")],
            ch_types=["pump"], sort_mode="index"),
        SectionPresetSet(section=SectionLabel.CHORUS, track_type="pump",
            section_presets=[PresetPoolEntry("fountain_burst"), PresetPoolEntry("fountain_tidal")],
            bar_presets=[PresetPoolEntry("fountain_burst"), PresetPoolEntry("fountain_running"), PresetPoolEntry("fountain_tidal")],
            ch_types=["pump"], sort_mode="index"),
        SectionPresetSet(section=SectionLabel.DROP, track_type="pump",
            section_presets=[PresetPoolEntry("fountain_burst")],
            # R173: audio_beat_valve 下架 → atx_beat_strobe（同为拍内前段开/后段关的硬切
            # 频闪，duty=0.3 对齐旧 open_ratio 默认值），ch_type 沿用 pump 角色。
            bar_presets=[
                PresetPoolEntry("fountain_burst"),
                PresetPoolEntry("atx_beat_strobe", params_override={"ch_type": "pump", "duty": 0.3}),
            ],
            ch_types=["pump"], sort_mode="index"),
        SectionPresetSet(section=SectionLabel.BUILD, track_type="pump",
            section_presets=[PresetPoolEntry("fountain_height_gradient")],
            bar_presets=[PresetPoolEntry("fountain_height_gradient"), PresetPoolEntry("pump_pressure_wave")],
            ch_types=["pump"], sort_mode="index"),
        SectionPresetSet(section=SectionLabel.BREAKDOWN, track_type="pump",
            section_presets=[PresetPoolEntry("pump_pressure_wave")],
            bar_presets=[PresetPoolEntry("pump_pressure_wave")],
            ch_types=["pump"], sort_mode="index"),
        SectionPresetSet(section=SectionLabel.OUTRO, track_type="pump",
            section_presets=[PresetPoolEntry("pump_pressure_wave")],
            bar_presets=[PresetPoolEntry("pump_pressure_wave")],
            ch_types=["pump"], sort_mode="index"),
    ]

def _default_motor_preset_sets():
    return [
        SectionPresetSet(section=SectionLabel.INTRO, track_type="motor",
            section_presets=[PresetPoolEntry("cur_ms_circles1")],
            bar_presets=[PresetPoolEntry("cur_ms_circles1")],
            ch_types=["motor_x", "motor_y"], sort_mode="index"),
        SectionPresetSet(section=SectionLabel.VERSE, track_type="motor",
            section_presets=[PresetPoolEntry("cur_ms_circles1"), PresetPoolEntry("cur_ms_spirals1")],
            bar_presets=[PresetPoolEntry("cur_ms_circles1"), PresetPoolEntry("cur_ms_spirals1")],
            ch_types=["motor_x", "motor_y"], sort_mode="index"),
        SectionPresetSet(section=SectionLabel.CHORUS, track_type="motor",
            # R173: audio_beat_joystick 下架 → cur_audio_motor_audio_shake（非 atx_*：atx
            # 族每实例只写单一 ch_type，没有能同时驱动 motor_x+motor_y 的真音频等价物；
            # shake 是唯一保留、经真实音频重验证为强拍触发随机抖动+衰减回位的双轴电机效果，
            # 是本角色现存最接近的真音频替代，见音频精编报告"架构缺口"说明）。
            section_presets=[PresetPoolEntry("cur_ms_spirals1"), PresetPoolEntry("cur_audio_motor_audio_shake")],
            bar_presets=[
                PresetPoolEntry("cur_ms_spirals1"), PresetPoolEntry("cur_audio_motor_audio_shake"),
                PresetPoolEntry("motor_random_jump"),
            ],
            ch_types=["motor_x", "motor_y"], sort_mode="index"),
        SectionPresetSet(section=SectionLabel.DROP, track_type="motor",
            section_presets=[PresetPoolEntry("cur_audio_motor_audio_shake")],
            bar_presets=[PresetPoolEntry("cur_audio_motor_audio_shake"), PresetPoolEntry("motor_random_jump")],
            ch_types=["motor_x", "motor_y"], sort_mode="index"),
        SectionPresetSet(section=SectionLabel.BUILD, track_type="motor",
            section_presets=[PresetPoolEntry("cur_ms_spirals1")],
            bar_presets=[PresetPoolEntry("cur_ms_spirals1"), PresetPoolEntry("cur_ms_circles1")],
            ch_types=["motor_x", "motor_y"], sort_mode="index"),
        SectionPresetSet(section=SectionLabel.BREAKDOWN, track_type="motor",
            section_presets=[PresetPoolEntry("cur_ms_circles1")],
            bar_presets=[PresetPoolEntry("cur_ms_circles1")],
            ch_types=["motor_x", "motor_y"], sort_mode="index"),
        SectionPresetSet(section=SectionLabel.OUTRO, track_type="motor",
            section_presets=[PresetPoolEntry("cur_ms_circles1")],
            bar_presets=[PresetPoolEntry("cur_ms_circles1")],
            ch_types=["motor_x", "motor_y"], sort_mode="index"),
    ]

def _default_valve_preset_sets():
    return [
        SectionPresetSet(section=SectionLabel.INTRO, track_type="valve",
            section_presets=[PresetPoolEntry("valve_pulse")],
            bar_presets=[PresetPoolEntry("valve_pulse")],
            ch_types=["valve"], sort_mode="index"),
        SectionPresetSet(section=SectionLabel.VERSE, track_type="valve",
            section_presets=[PresetPoolEntry("valve_sequential"), PresetPoolEntry("valve_pulse")],
            bar_presets=[PresetPoolEntry("valve_sequential"), PresetPoolEntry("valve_pulse")],
            ch_types=["valve"], sort_mode="index"),
        SectionPresetSet(section=SectionLabel.CHORUS, track_type="valve",
            # R173: audio_beat_valve 下架 → atx_beat_strobe（duty=0.3 对齐旧 open_ratio），
            # ch_type 沿用 valve 角色。
            section_presets=[
                PresetPoolEntry("valve_sequential"),
                PresetPoolEntry("atx_beat_strobe", params_override={"ch_type": "valve", "duty": 0.3}),
            ],
            bar_presets=[
                PresetPoolEntry("valve_sequential"),
                PresetPoolEntry("atx_beat_strobe", params_override={"ch_type": "valve", "duty": 0.3}),
                PresetPoolEntry("valve_pulse"),
            ],
            ch_types=["valve"], sort_mode="index"),
        SectionPresetSet(section=SectionLabel.DROP, track_type="valve",
            section_presets=[
                PresetPoolEntry("atx_beat_strobe", params_override={"ch_type": "valve", "duty": 0.3}),
            ],
            bar_presets=[
                PresetPoolEntry("atx_beat_strobe", params_override={"ch_type": "valve", "duty": 0.3}),
                PresetPoolEntry("valve_pulse", params_override={"rate": 4.0}),
            ],
            ch_types=["valve"], sort_mode="index"),
        SectionPresetSet(section=SectionLabel.BUILD, track_type="valve",
            section_presets=[PresetPoolEntry("valve_sequential")],
            bar_presets=[PresetPoolEntry("valve_sequential"), PresetPoolEntry("valve_pulse")],
            ch_types=["valve"], sort_mode="index"),
        SectionPresetSet(section=SectionLabel.BREAKDOWN, track_type="valve",
            section_presets=[PresetPoolEntry("valve_pulse")],
            bar_presets=[PresetPoolEntry("valve_pulse")],
            ch_types=["valve"], sort_mode="index"),
        SectionPresetSet(section=SectionLabel.OUTRO, track_type="valve",
            section_presets=[PresetPoolEntry("valve_pulse")],
            bar_presets=[PresetPoolEntry("valve_pulse")],
            ch_types=["valve"], sort_mode="index"),
    ]

def get_default_preset_sets(track_type):
    factories = {
        "light": _default_light_preset_sets,
        "pump": _default_pump_preset_sets,
        "motor": _default_motor_preset_sets,
        "valve": _default_valve_preset_sets,
    }
    fn = factories.get(track_type)
    return fn() if fn else []


# 兼容旧版
DEFAULT_SECTION_MAPPINGS: List[EffectMapping] = []


# ─────────────────────────────────────────────────────────────────────────────
#  桥 (v24 - 多效果类型并行)
# ─────────────────────────────────────────────────────────────────────────────
class MusicToEffectBridge(QObject):
    """v27 音乐→效果桥接器：多效果类型并行 + 深度音乐语义上下文。"""

    # ★ v24: 信号增加 track_type 参数，让下游知道是哪种效果类型
    effect_apply_request = pyqtSignal(str, str, dict, list, str)
    # (track_type, preset_id, params, ch_types, sort_mode)

    effect_param_modulate = pyqtSignal(str, str, float)
    status_text = pyqtSignal(str)
    current_preset_changed = pyqtSignal(str, str)  # (track_type, preset_id)
    mapping_triggered = pyqtSignal(object)
    # 保留兼容信号
    track_type_changed = pyqtSignal(str)
    section_preset_applied = pyqtSignal(str, str)
    bar_preset_applied = pyqtSignal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._enabled = False
        self._takeover_mode = True

        # ★ v24: 每种效果类型独立启用状态
        self._active_types: Dict[str, bool] = {
            "light": True,
            "pump": False,
            "motor": False,
            "valve": False,
            "color": False,
        }

        # 保留 _track_type 用于兼容旧接口
        self._track_type = "light"

        # 每种效果类型的预设套
        self._preset_sets: Dict[str, List[SectionPresetSet]] = {}
        for tt in TRACK_TYPES:
            self._preset_sets[tt] = get_default_preset_sets(tt)

        self._mappings: List[EffectMapping] = []

        # ★ v24: 每种效果类型独立维护当前预设
        self._current_presets: Dict[str, str] = {tt: "" for tt in TRACK_TYPES}

        self._current_section = SectionLabel.UNKNOWN
        self._current_bar = -1
        self._last_section_change_t = -10.0
        self._last_bar_change_t = -10.0
        self._last_trigger_t: Dict[str, float] = {}
        self._t0 = time.monotonic()
        self._attached_to = None

        self._switch_speed = 1.0
        self._min_section_interval = 2.0
        self._min_bar_interval = 2.0

        self._modulate_param_key = ""
        self._modulate_scale = 1.0
        self._modulate_floor = 0.0
        self._modulate_min_dt = 1.0 / 30.0
        self._last_modulate_t = 0.0
        # ★ v24: 音频参数自动调制引擎引用（懒加载）
        self._modulator = None
        # ★ v27: 深度音乐语义分析器引用（懒加载），用于 AI 编曲级别的乐句/情绪/密度上下文。
        self._deep_analyzer = None
        # 效果引擎单例懒加载缓存（get_engine 是进程级单例，重复 import 无意义）
        self._engine_ref = None
        self._last_music_context = None

        # ── C3: 设备组公共合法输出区间（opt-in，默认关闭=旧行为不变）──
        # 关闭时 _apply_common_range 直接返回原 modulated，逐字节与历史一致。
        self._common_range_enabled = False
        # {device_id: DeviceCapabilityProfile 或具有 out_lo/out_hi 的对象}
        self._cap_profiles: Dict[str, Any] = {}
        # 缓存：{track_type: (lo, hi)}，设备集变化才失效重算
        self._common_range_cache: Dict[str, tuple] = {}
        # 缓存有效性指纹（设备集合 + 各设备区间快照）
        self._common_range_sig = None

    # ── C3: 0..255 域内、应参与公共区间重映射的参数键 ──
    _COMMON_RANGE_KEYS = (
        "min", "max", "brightness", "on_val", "off_val",
        "low", "high", "level", "value", "base", "amplitude",
    )

    def attach(self, msa):
        self.detach()
        try:
            msa.structure_event.connect(self._on_structure_event)
            msa.analysis_tick.connect(self._on_analysis_tick)
            self._attached_to = msa
            self._get_deep_analyzer()
        except Exception:
            logger.debug("music_to_effect_bridge: failed to attach to MusicStructureAnalyzer")
            self._attached_to = None

    def detach(self):
        if self._attached_to is None:
            return
        try:
            self._attached_to.structure_event.disconnect(self._on_structure_event)
        except Exception:
            logger.debug("music_to_effect_bridge: failed to disconnect structure_event")
            pass
        try:
            self._attached_to.analysis_tick.disconnect(self._on_analysis_tick)
        except Exception:
            logger.debug("music_to_effect_bridge: failed to disconnect analysis_tick")
            pass
        self._attached_to = None

    def set_enabled(self, enabled):
        was = self._enabled
        self._enabled = bool(enabled)
        if not self._enabled and was:
            self.status_text.emit("Bridge: 已禁用")
            self.clear_current_presets()

    @property
    def enabled(self):
        return self._enabled

    @property
    def current_preset_id(self):
        """兼容旧接口：返回当前主轨道类型的预设。"""
        return self._current_presets.get(self._track_type, "")

    def get_current_preset(self, track_type: str) -> str:
        """获取指定效果类型的当前预设ID。"""
        return self._current_presets.get(track_type, "")

    @property
    def track_type(self):
        return self._track_type

    # ★ v24: 效果类型启用/禁用
    def set_type_active(self, track_type: str, active: bool):
        """启用或禁用某种效果类型。"""
        if track_type in TRACK_TYPES:
            self._active_types[track_type] = bool(active)
            if not active:
                # 禁用时清空该类型的当前预设
                self._current_presets[track_type] = ""
                self.current_preset_changed.emit(track_type, "")

    def clear_current_presets(self):
        """Stop all effect outputs currently owned by the bridge."""
        for tt in TRACK_TYPES:
            if self._current_presets.get(tt):
                self._current_presets[tt] = ""
                self.current_preset_changed.emit(tt, "")

    def is_type_active(self, track_type: str) -> bool:
        """查询某种效果类型是否启用。"""
        return self._active_types.get(track_type, False)

    def get_active_types(self) -> List[str]:
        """返回所有启用的效果类型列表。"""
        return [tt for tt, active in self._active_types.items() if active]

    def set_track_type(self, tt):
        """兼容旧接口。"""
        if tt in TRACK_TYPES and tt != self._track_type:
            self._track_type = tt
            self.track_type_changed.emit(tt)
            self.status_text.emit(f"主轨道类型: {TRACK_TYPES[tt]['label']}")

    def get_preset_sets(self, track_type=None):
        tt = track_type or self._track_type
        return self._preset_sets.get(tt, [])

    def replace_preset_sets(self, track_type, sets):
        self._preset_sets[track_type] = [
            SectionPresetSet.from_dict(s.to_dict()) for s in sets
        ]

    @property
    def switch_speed(self):
        return self._switch_speed

    def set_switch_speed(self, speed):
        self._switch_speed = float(max(0.05, min(16.0, speed)))
        self._min_section_interval = 1.5 / self._switch_speed
        self._min_bar_interval = 1.0 / self._switch_speed

    def get_mappings(self):
        return self._mappings

    def replace_mappings(self, mappings):
        self._mappings = [EffectMapping(**m.to_dict()) for m in mappings]
        self._last_trigger_t.clear()

    def set_modulation(self, param_key="", scale=1.0, floor=0.0):
        self._modulate_param_key = str(param_key or "")
        self._modulate_scale = float(scale)
        self._modulate_floor = float(floor)

    # ── C3: 设备组公共合法输出区间 ──
    def set_common_range_enabled(self, enabled: bool):
        """开启/关闭设备组公共合法区间重映射（opt-in，默认关闭=旧行为）。"""
        self._common_range_enabled = bool(enabled)

    @property
    def common_range_enabled(self) -> bool:
        return self._common_range_enabled

    def set_capability_profiles(self, profiles):
        """
        注入设备能力档案 {device_id: profile}。
        profile 需具备 out_lo/out_hi 与 get_active_channels()（缺失则按全域处理）。
        设备集变化时缓存自动失效。
        """
        self._cap_profiles = dict(profiles or {})
        self._common_range_cache.clear()
        self._common_range_sig = None

    def _channels_for_type(self, track_type: str) -> Set[str]:
        """该 track_type 关心的通道类型集合。"""
        info = TRACK_TYPES.get(track_type, {})
        return set(info.get("ch_types", []))

    def _profiles_signature(self):
        """设备集 + 各设备区间的指纹，用于判定缓存是否需要重算。"""
        try:
            return tuple(sorted(
                (str(did),
                 int(getattr(p, "out_lo", 0)),
                 int(getattr(p, "out_hi", 255)))
                for did, p in self._cap_profiles.items()
            ))
        except Exception:
            logger.debug("music_to_effect_bridge: failed to build capability profile signature")
            # 任意异常都不应影响下发：返回 None 触发安全回落
            return None

    def _compute_common_range(self, track_type: str) -> tuple:
        """
        计算该 track_type 下激活设备组的公共合法区间。
        lo = max(各设备 out_lo)，hi = min(各设备 out_hi)（交集）。
        拿不到任何相关 profile 时返回 (0, 255) → 行为与现状一致。
        结果按设备集指纹缓存，设备集变化才重算。
        """
        if not self._cap_profiles:
            return (0, 255)

        sig = self._profiles_signature()
        if sig is None:
            return (0, 255)
        if sig != self._common_range_sig:
            # 设备集（或其区间）变化：整表缓存失效
            self._common_range_cache.clear()
            self._common_range_sig = sig

        cached = self._common_range_cache.get(track_type)
        if cached is not None:
            return cached

        want = self._channels_for_type(track_type)
        lo, hi = 0, 255
        found = False
        for prof in self._cap_profiles.values():
            # 仅纳入与该 track_type 通道相关的设备
            try:
                chans = set(prof.get_active_channels())
            except Exception:
                logger.debug("music_to_effect_bridge: failed to get active channels from profile")
                chans = set()
            if want and chans and not (chans & want):
                continue
            p_lo = int(getattr(prof, "out_lo", 0))
            p_hi = int(getattr(prof, "out_hi", 255))
            lo = max(lo, p_lo)
            hi = min(hi, p_hi)
            found = True

        if not found:
            result = (0, 255)
        elif lo >= hi:
            # 区间退化（交集为空或反转）：安全回落到全域，不做破坏性压缩
            result = (0, 255)
        else:
            result = (lo, hi)

        self._common_range_cache[track_type] = result
        return result

    def _apply_common_range(self, track_type: str, modulated: dict) -> dict:
        """
        将 0..255 域参数 clamp 并重映射到设备组公共区间：
            v' = lo + (v/255) * (hi - lo)
        关闭开关或区间为全域 (0,255) 时原样返回（行为与现状完全一致）。
        """
        if not self._common_range_enabled:
            return modulated
        if not isinstance(modulated, dict) or not modulated:
            return modulated

        lo, hi = self._compute_common_range(track_type)
        if lo <= 0 and hi >= 255:
            # 全域：无需任何改动
            return modulated

        span = hi - lo
        out = dict(modulated)
        for key in self._COMMON_RANGE_KEYS:
            if key not in out:
                continue
            v = out[key]
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            # clamp 到 0..255 再线性重映射到 [lo, hi]
            vc = 0.0 if v < 0 else (255.0 if v > 255 else float(v))
            mapped = lo + (vc / 255.0) * span
            out[key] = int(round(mapped)) if isinstance(v, int) else mapped
        return out

    # ── 主入口 ──
    def _on_structure_event(self, ev):
        if not self._enabled:
            return
        # ★ v26修复：无音频时不处理任何结构事件
        if not getattr(ev, 'has_audio', True):
            return
        et = ev.event_type
        now = ev.t

        if et == "section_change":
            if (now - self._last_section_change_t) < self._min_section_interval:
                return
            self._last_section_change_t = now
            self._current_section = ev.section_label
            self._apply_section_preset_all(ev)
            return

        if et == "bar_change":
            if (now - self._last_bar_change_t) < self._min_bar_interval:
                return
            self._last_bar_change_t = now
            self._current_bar = ev.bar_number
            self._apply_bar_preset_all(ev)
            return

        if et == "tempo_change":
            self.status_text.emit(
                f"速度变化: BPM={ev.bpm:.0f} delta={ev.tempo_delta:+.1f}")
            return

        if et == "drop":
            self._apply_dramatic_event_all(ev, "drop")
            return
        if et == "buildup_start":
            self._apply_dramatic_event_all(ev, "buildup_start")
            return
        if et == "buildup_end":
            self._apply_dramatic_event_all(ev, "buildup_end")
            return

        if et == "instrument_onset":
            self._handle_legacy_mapping(ev)

    def _on_analysis_tick(self, ev):
        if not self._enabled:
            return
        # ★ v26修复：无音频时不发出任何调制请求，防止效果引擎持续写入通道
        if not getattr(ev, 'has_audio', True):
            return
        now = ev.t
        ev = self._augment_event_with_music_context(ev)
        if (now - self._last_modulate_t) < self._modulate_min_dt:
            return
        self._last_modulate_t = now

        # ★ v24: 使用调制引擎为每种活跃类型计算调制参数并发送
        mod = self._get_modulator()
        if mod is not None and mod.global_enabled:
            for tt in self.get_active_types():
                if not mod.is_type_enabled(tt):
                    continue
                pid = self._current_presets.get(tt, "")
                if not pid:
                    continue
                # 获取当前预设的基础参数
                base_params = self._build_params(pid)
                # 计算调制后的参数
                modulated = mod.compute_modulated_params(tt, base_params, ev)
                # ── C3: 落在设备组公共合法区间（opt-in，关闭时原样返回）──
                modulated = self._apply_common_range(tt, modulated)
                # 获取当前预设套的ch_types和sort_mode
                section = self._current_section
                pset = self._find_preset_set(section, tt)
                ch_types = list(pset.ch_types) if pset else self._default_ch_types_for(pid, tt)
                sort_mode = pset.sort_mode if pset else "index"
                # 发送调制后的参数请求（不切换预设，只更新参数）
                self.effect_apply_request.emit(tt, pid, modulated, ch_types, sort_mode)
            return

        # 兼容旧版单参数调制
        key = self._modulate_param_key
        if not key:
            return
        v = self._modulate_floor + ev.rms * self._modulate_scale
        for tt in self.get_active_types():
            pid = self._current_presets.get(tt, "")
            if pid:
                self.effect_param_modulate.emit(pid, key, float(v))

    def _get_modulator(self):
        """懒加载调制引擎。"""
        if self._modulator is None:
            try:
                from .audio_param_modulator import get_global_modulator
                self._modulator = get_global_modulator()
            except Exception:
                logger.debug("music_to_effect_bridge: failed to lazy-load global modulator")
                pass
        return self._modulator

    def _get_deep_analyzer(self):
        """懒加载深度音乐分析器。"""
        if self._deep_analyzer is None:
            get_deep_music_analyzer = None
            try:
                from src.engine.deep_music_analyzer import get_deep_music_analyzer
            except Exception:
                logger.debug("music_to_effect_bridge: failed to import deep_music_analyzer from src.engine")
                try:
                    from engine.deep_music_analyzer import get_deep_music_analyzer
                except Exception:
                    logger.debug("music_to_effect_bridge: failed to import deep_music_analyzer from engine")
                    get_deep_music_analyzer = None
            if get_deep_music_analyzer is not None:
                try:
                    self._deep_analyzer = get_deep_music_analyzer()
                except Exception:
                    logger.debug("music_to_effect_bridge: failed to call get_deep_music_analyzer")
                    self._deep_analyzer = None
        return self._deep_analyzer

    def _augment_event_with_music_context(self, ev):
        """把实时 MSA 事件送入深度分析器，并将 AI 编曲语义上下文挂到事件上。"""
        deep = self._get_deep_analyzer()
        if deep is None:
            return ev
        try:
            deep.on_msa_event(ev)
            ctx = deep.query(float(getattr(ev, "t", 0.0)))
            setattr(ev, "music_context", ctx)
            self._last_music_context = ctx
            extras = getattr(ev, "extras", None)
            if isinstance(extras, dict):
                extras["ai_complexity"] = float(ctx.suggested_complexity_multiplier())
                extras["ai_device_density"] = float(ctx.suggested_device_density())
                extras["ai_transition_urgency"] = float(ctx.suggested_transition_urgency())
        except Exception:
            logger.debug("music_to_effect_bridge: failed to augment event with music context")
            pass
        return ev

    def get_modulator(self):
        """公开接口：获取调制引擎实例。"""
        return self._get_modulator()

    # ── ★ v24: 多类型并行预设应用 ──
    def _apply_section_preset_all(self, ev):
        """为所有启用的效果类型应用段落预设。"""
        active_types = self.get_active_types()
        if not active_types:
            self.status_text.emit(f"段落 {ev.section_label}: 无启用的效果类型")
            return
        for tt in active_types:
            self._apply_section_preset_for_type(ev, tt)

    def _apply_bar_preset_all(self, ev):
        """为所有启用的效果类型应用小节预设。"""
        for tt in self.get_active_types():
            self._apply_bar_preset_for_type(ev, tt)

    def _apply_dramatic_event_all(self, ev, event_type):
        """为所有启用的效果类型应用戏剧性事件。"""
        for tt in self.get_active_types():
            self._apply_dramatic_event_for_type(ev, event_type, tt)

    def _find_preset_set(self, section, track_type):
        """在指定效果类型的预设套中查找匹配的段落。"""
        sets = self._preset_sets.get(track_type, [])
        for s in sets:
            if s.section == section and s.enabled:
                return s
        return None

    def _pick_from_pool(self, pool):
        if not pool:
            return None
        valid = [p for p in pool if p.preset_id]
        if not valid:
            return None
        if len(valid) == 1:
            return valid[0]
        total = sum(p.weight for p in valid)
        if total <= 0:
            return random.choice(valid)
        r = random.uniform(0, total)
        cumulative = 0.0
        for p in valid:
            cumulative += p.weight
            if r <= cumulative:
                return p
        return valid[-1]

    def _apply_section_preset_for_type(self, ev, track_type):
        """为指定效果类型应用段落预设。"""
        pset = self._find_preset_set(ev.section_label, track_type)
        if not pset:
            return
        entry = self._pick_from_pool(pset.section_presets)
        if not entry:
            return
        params = self._build_params(entry.preset_id, entry.params_override)
        ch_types = list(pset.ch_types) or self._default_ch_types_for(entry.preset_id, track_type)
        sort_mode = pset.sort_mode or "index"
        self.effect_apply_request.emit(track_type, entry.preset_id, params, ch_types, sort_mode)
        self._current_presets[track_type] = entry.preset_id
        self.current_preset_changed.emit(track_type, entry.preset_id)
        self.section_preset_applied.emit(ev.section_label, entry.preset_id)
        tt_label = TRACK_TYPES.get(track_type, {}).get("label", track_type)
        self.status_text.emit(f"[结构][{tt_label}] {ev.section_label} → {entry.preset_id}")

    def _apply_bar_preset_for_type(self, ev, track_type):
        """为指定效果类型应用小节预设。"""
        section = self._current_section or ev.section_label
        pset = self._find_preset_set(section, track_type)
        if not pset or not pset.bar_presets:
            return
        entry = self._pick_from_pool(pset.bar_presets)
        if not entry:
            return
        cur_pid = self._current_presets.get(track_type, "")
        if entry.preset_id == cur_pid and len(pset.bar_presets) > 1:
            entry2 = self._pick_from_pool(pset.bar_presets)
            if entry2 and entry2.preset_id != cur_pid:
                entry = entry2
        params = self._build_params(entry.preset_id, entry.params_override)
        ch_types = list(pset.ch_types) or self._default_ch_types_for(entry.preset_id, track_type)
        sort_mode = pset.sort_mode or "index"
        self.effect_apply_request.emit(track_type, entry.preset_id, params, ch_types, sort_mode)
        self._current_presets[track_type] = entry.preset_id
        self.current_preset_changed.emit(track_type, entry.preset_id)
        self.bar_preset_applied.emit(ev.bar_number, entry.preset_id)
        tt_label = TRACK_TYPES.get(track_type, {}).get("label", track_type)
        self.status_text.emit(f"[小节#{ev.bar_number}][{tt_label}] {section} → {entry.preset_id}")

    def _apply_dramatic_event_for_type(self, ev, event_type, track_type):
        """为指定效果类型应用戏剧性事件。"""
        if event_type == "drop":
            pset = self._find_preset_set(SectionLabel.DROP, track_type)
            if pset and pset.section_presets:
                entry = self._pick_from_pool(pset.section_presets)
                if entry:
                    params = self._build_params(entry.preset_id, entry.params_override)
                    ch_types = list(pset.ch_types) or self._default_ch_types_for(entry.preset_id, track_type)
                    self.effect_apply_request.emit(track_type, entry.preset_id, params, ch_types, pset.sort_mode or "index")
                    self._current_presets[track_type] = entry.preset_id
                    self.current_preset_changed.emit(track_type, entry.preset_id)
                    tt_label = TRACK_TYPES.get(track_type, {}).get("label", track_type)
                    self.status_text.emit(f"[DROP][{tt_label}] → {entry.preset_id}")
                    return
        if event_type == "buildup_start":
            pset = self._find_preset_set(SectionLabel.BUILD, track_type)
            if pset and pset.section_presets:
                entry = self._pick_from_pool(pset.section_presets)
                if entry:
                    params = self._build_params(entry.preset_id, entry.params_override)
                    ch_types = list(pset.ch_types) or self._default_ch_types_for(entry.preset_id, track_type)
                    self.effect_apply_request.emit(track_type, entry.preset_id, params, ch_types, pset.sort_mode or "index")
                    self._current_presets[track_type] = entry.preset_id
                    self.current_preset_changed.emit(track_type, entry.preset_id)
                    tt_label = TRACK_TYPES.get(track_type, {}).get("label", track_type)
                    self.status_text.emit(f"[BUILDUP][{tt_label}] → {entry.preset_id}")

    def _handle_legacy_mapping(self, ev):
        trigger_keys = []
        if ev.event_type == "instrument_onset":
            trigger_keys.append(f"onset:{ev.onset_instrument}")
        if not trigger_keys:
            return
        now = ev.t
        candidates = []
        for m in self._mappings:
            if not m.enabled or m.trigger not in trigger_keys:
                continue
            last = self._last_trigger_t.get(m.trigger, -1e9)
            if (now - last) < m.min_interval_s:
                continue
            if not m.preset_id:
                continue
            candidates.append(m)
        if not candidates:
            return
        candidates.sort(key=lambda mm: -mm.priority)
        winner = candidates[0]
        self._last_trigger_t[winner.trigger] = now
        params = self._build_params(winner.preset_id, winner.params_override)
        ch_types = list(winner.ch_types or self._default_ch_types_for(winner.preset_id, self._track_type))
        # legacy mapping 使用主轨道类型
        self.effect_apply_request.emit(self._track_type, winner.preset_id, params, ch_types, winner.sort_mode or "index")
        if winner.preset_id != self._current_presets.get(self._track_type, ""):
            self._current_presets[self._track_type] = winner.preset_id
            self.current_preset_changed.emit(self._track_type, winner.preset_id)
        self.status_text.emit(f"[{ev.event_type}] {winner.trigger} → {winner.preset_id}")
        self.mapping_triggered.emit(winner)

    def _engine(self):
        if self._engine_ref is not None:
            return self._engine_ref
        try:
            from src.ui.effect_engine import get_engine
            self._engine_ref = get_engine()
            return self._engine_ref
        except Exception:
            logger.debug("music_to_effect_bridge: failed to import engine from src.ui.effect_engine")
            try:
                from ui.effect_engine import get_engine
                self._engine_ref = get_engine()
                return self._engine_ref
            except Exception:
                logger.debug("music_to_effect_bridge: failed to import engine from ui.effect_engine")
                return None

    def _build_params(self, preset_id, overrides=None):
        eng = self._engine()
        params = {}
        if eng is not None:
            try:
                preset = eng.get_preset(preset_id)
                if preset is not None:
                    params.update(preset.default_params())
            except Exception:
                logger.debug("music_to_effect_bridge: failed to build params for %s", preset_id)
                pass
        if overrides:
            params.update(overrides)
        return params

    def _default_ch_types_for(self, preset_id, track_type):
        """根据效果类型返回默认通道类型。"""
        tt_info = TRACK_TYPES.get(track_type, {})
        default = tt_info.get("ch_types", ["dimmer"])
        eng = self._engine()
        if eng is None:
            return default
        try:
            preset = eng.get_preset(preset_id)
            if preset is None or not preset.target_channels:
                return default
            tc = list(preset.target_channels)
            # 检查预设的目标通道是否与当前效果类型匹配
            type_chs = set(tt_info.get("ch_types", []))
            matching = [c for c in tc if c in type_chs]
            if matching:
                return matching
            return default
        except Exception:
            logger.debug("music_to_effect_bridge: failed to get default ch_types for %s %s", preset_id, track_type)
            return default

    # 兼容旧接口
    def _default_ch_types(self, preset_id):
        return self._default_ch_types_for(preset_id, self._track_type)

    def to_dict(self):
        return {
            "enabled": self._enabled,
            "track_type": self._track_type,
            "active_types": dict(self._active_types),
            "switch_speed": self._switch_speed,
            "takeover_mode": self._takeover_mode,
            "preset_sets": {
                tt: [s.to_dict() for s in sets]
                for tt, sets in self._preset_sets.items()
            },
            "mappings": [m.to_dict() for m in self._mappings],
            "modulate_param_key": self._modulate_param_key,
            "modulate_scale": self._modulate_scale,
            "modulate_floor": self._modulate_floor,
        }

    def load_dict(self, d):
        self._enabled = bool(d.get("enabled", False))
        tt = str(d.get("track_type", "light"))
        if tt in TRACK_TYPES:
            self._track_type = tt
        # ★ v24: 加载多类型启用状态
        at = d.get("active_types", {})
        if isinstance(at, dict):
            for k, v in at.items():
                if k in TRACK_TYPES:
                    self._active_types[k] = bool(v)
        elif not at:
            # 兼容旧版：只有 track_type 启用
            for k in TRACK_TYPES:
                self._active_types[k] = (k == self._track_type)
        self.set_switch_speed(float(d.get("switch_speed", 1.0)))
        self._takeover_mode = bool(d.get("takeover_mode", True))
        ps = d.get("preset_sets", {})
        for tt_key, sets_data in ps.items():
            if tt_key in TRACK_TYPES and isinstance(sets_data, list):
                self._preset_sets[tt_key] = [SectionPresetSet.from_dict(s) for s in sets_data]
        mappings_data = d.get("mappings", [])
        self._mappings = [EffectMapping.from_dict(m) for m in mappings_data]
        self._modulate_param_key = str(d.get("modulate_param_key", ""))
        self._modulate_scale = float(d.get("modulate_scale", 1.0))
        self._modulate_floor = float(d.get("modulate_floor", 0.0))
        self._last_music_context = None


# ─────────────────────────────────────────────────────────────────────────────
#  全局单例
# ─────────────────────────────────────────────────────────────────────────────
_global_bridge = None

def get_global_bridge():
    global _global_bridge
    if _global_bridge is not None:
        try:
            _ = _global_bridge.objectName()
        except RuntimeError:
            _global_bridge = None
    if _global_bridge is None:
        _global_bridge = MusicToEffectBridge()
        try:
            from .music_structure_analyzer import get_global_structure_analyzer
            msa = get_global_structure_analyzer()
            _global_bridge.attach(msa)
        except Exception:
            logger.debug("music_to_effect_bridge: failed to attach global bridge to MSA")
            pass
    return _global_bridge
