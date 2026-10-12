"""
src.audio
=========
音乐深度分析与自动 effect 调度子系统。

本包中的所有模块都是"非侵入式"的：
  - 不修改 src/ui/audio_driver.py
  - 不修改 src/ui/effect_engine.py
  - 仅订阅现有 AudioAnalyzer.frame_ready，输出更高层的结构信号

设计要点：
  AudioAnalyzer (老)
       │ frame_ready(AudioFrame)
       ▼
  MusicStructureAnalyzer (本包/接口2)
       │ structure_event(MusicStructureEvent)
       │ analysis_tick(MusicStructureEvent)   ← 每帧轻量推送
       ▼
  MusicToEffectBridge (本包) + AudioParamModulator (本包)
       │ effect_apply_request(track_type, preset_id, params, ch_types, sort_mode)
       │   ↑ 路由到 MainWindow._on_effect_applied → EffectBrowserWidget
       ▼
  EffectEngine (老，不动)
"""

from .music_structure_analyzer import (
    MusicStructureAnalyzer,
    MusicStructureEvent,
    SectionLabel,
    INSTRUMENTS,
    get_global_structure_analyzer,
)
from .music_to_effect_bridge import (
    MusicToEffectBridge,
    EffectMapping,
    PresetPoolEntry,
    SectionPresetSet,
    TRACK_TYPES,
    DEFAULT_SECTION_MAPPINGS,
    get_default_preset_sets,
    get_global_bridge,
)
from .audio_param_modulator import (
    AudioParamModulator,
    ModRule,
    TypeModConfig,
    AUDIO_SOURCES,
    MODULATABLE_PARAMS,
    get_default_mod_config,
    get_global_modulator,
)

__all__ = [
    "MusicStructureAnalyzer",
    "MusicStructureEvent",
    "SectionLabel",
    "INSTRUMENTS",
    "get_global_structure_analyzer",
    "MusicToEffectBridge",
    "EffectMapping",
    "PresetPoolEntry",
    "SectionPresetSet",
    "TRACK_TYPES",
    "DEFAULT_SECTION_MAPPINGS",
    "get_default_preset_sets",
    "get_global_bridge",
    "AudioParamModulator",
    "ModRule",
    "TypeModConfig",
    "AUDIO_SOURCES",
    "MODULATABLE_PARAMS",
    "get_default_mod_config",
    "get_global_modulator",
]
