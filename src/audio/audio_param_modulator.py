"""
audio_param_modulator.py — 音频参数自动调制引擎
=================================================
根据实时音频分析数据（BPM、RMS力度、乐器在场度、频段能量等）
自动调整当前播放效果的各项参数（速度、相位差、幅度、最大/最小值等）。

设计原则：
  - 完全独立模块，可单独启用/禁用，不影响桥接器主逻辑
  - 每种效果类型可以独立配置调制规则
  - 支持多种音频源参数（BPM/RMS/乐器/频段）映射到多种效果参数
  - 支持平滑过渡，避免参数突变导致的视觉/机械抖动
"""
from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  音频源类型
# ─────────────────────────────────────────────────────────────────────────────
AUDIO_SOURCES = {
    "rms":         "整体力度（RMS）",
    "peak":        "峰值电平",
    "bpm":         "节拍速度（BPM）",
    "beat_phase":  "节拍相位（0~1）",
    "band_sub":    "超低频（0~80Hz）",
    "band_bass":   "低频（80~250Hz）",
    "band_mid":    "中频（250~2kHz）",
    "band_high":   "高频（2k~8kHz）",
    "band_air":    "超高频（8k+）",
    "kick":        "踢鼓在场度",
    "snare":       "军鼓在场度",
    "hihat":       "踩镲在场度",
    "bass":        "贝斯在场度",
    "melody":      "旋律在场度",
    "pad":         "铺底在场度",
    "flux":       "频谱变化率",
    "onset_density": "节奏密度",
    "section_confidence": "段落置信度",
    "buildup":    "Build-up强度",
    "bar_energy": "小节平均能量",
    "bar_progress": "小节进度",
    "phrase_tension": "乐句紧张度",
    "phrase_release": "乐句释放度",
    "phrase_progress": "乐句进度",
    "texture_density": "织体密度",
    "global_energy": "全曲能量位置",
    "complexity":  "AI建议复杂度",
    "device_density": "AI建议设备密度",
    "transition_urgency": "AI建议过渡紧迫度",
}

# 可调制的效果参数键名（通用）
MODULATABLE_PARAMS = {
    "speed":       "速度（Hz/圈速）",
    "phase_step":  "相位差",
    "min":         "最小值（0~255）",
    "max":         "最大值（0~255）",
    "brightness":  "亮度",
    "on_val":      "亮值",
    "off_val":     "暗值",
    "width":       "宽度",
    "rate":        "频率",
    "density":     "密度",
    "size":        "尺寸",
    "phase":       "相位偏移",
    "attack":      "启动/起音强度",
    "decay":       "衰减速度",
    "spread":      "空间扩散",
    "complexity":  "效果复杂度",
}


# ─────────────────────────────────────────────────────────────────────────────
#  单条调制规则
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ModRule:
    """一条参数调制规则：将一个音频源映射到一个效果参数。"""
    source: str          # AUDIO_SOURCES 中的键
    param_key: str       # 效果参数键名
    out_min: float       # 输出范围下限
    out_max: float       # 输出范围上限
    in_min: float = 0.0  # 输入范围下限（归一化后）
    in_max: float = 1.0  # 输入范围上限（归一化后）
    smooth: float = 0.15 # 平滑系数（0=无平滑, 1=完全锁定）
    enabled: bool = True
    curve: str = "linear"  # "linear" | "exp" | "log" | "step"
    smooth_mode: str = "ema"  # "ema"(一阶指数,默认) | "fir5"(加权核 1,2,3,2,1/9,抗尖峰)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "param_key": self.param_key,
            "out_min": self.out_min,
            "out_max": self.out_max,
            "in_min": self.in_min,
            "in_max": self.in_max,
            "smooth": self.smooth,
            "enabled": self.enabled,
            "curve": self.curve,
            "smooth_mode": self.smooth_mode,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ModRule":
        return cls(
            source=str(d.get("source", "rms")),
            param_key=str(d.get("param_key", "speed")),
            out_min=float(d.get("out_min", 0.5)),
            out_max=float(d.get("out_max", 4.0)),
            in_min=float(d.get("in_min", 0.0)),
            in_max=float(d.get("in_max", 1.0)),
            smooth=float(d.get("smooth", 0.15)),
            enabled=bool(d.get("enabled", True)),
            curve=str(d.get("curve", "linear")),
            smooth_mode=str(d.get("smooth_mode", "ema")),
        )

    def map_value(self, raw: float) -> float:
        """将原始音频值映射为效果参数值。"""
        # 归一化到 [0, 1]
        in_range = self.in_max - self.in_min
        if abs(in_range) < 1e-9:
            t = 0.5
        else:
            t = (raw - self.in_min) / in_range
        t = max(0.0, min(1.0, t))

        # 曲线变换
        if self.curve == "exp":
            t = t * t
        elif self.curve == "log":
            t = math.sqrt(t)
        elif self.curve == "step":
            t = 1.0 if t >= 0.5 else 0.0
        # else linear

        # 映射到输出范围
        return self.out_min + t * (self.out_max - self.out_min)


# ─────────────────────────────────────────────────────────────────────────────
#  每种效果类型的调制配置
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class TypeModConfig:
    """某种效果类型的调制配置。"""
    track_type: str
    enabled: bool = False
    rules: List[ModRule] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "track_type": self.track_type,
            "enabled": self.enabled,
            "rules": [r.to_dict() for r in self.rules],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TypeModConfig":
        return cls(
            track_type=str(d.get("track_type", "light")),
            enabled=bool(d.get("enabled", False)),
            rules=[ModRule.from_dict(r) for r in d.get("rules", [])],
        )


# ─────────────────────────────────────────────────────────────────────────────
#  默认调制配置
# ─────────────────────────────────────────────────────────────────────────────
def _default_light_mod_config() -> TypeModConfig:
    return TypeModConfig(
        track_type="light",
        enabled=False,
        rules=[
            ModRule(source="rms",       param_key="max",        out_min=80,  out_max=255, smooth=0.12, curve="log"),
            ModRule(source="bpm",       param_key="speed",      out_min=0.5, out_max=6.0, in_min=60, in_max=180, smooth=0.05, curve="linear"),
            ModRule(source="beat_phase",param_key="phase_step", out_min=0.0, out_max=0.3, smooth=0.0, curve="linear"),
            ModRule(source="kick",      param_key="min",        out_min=0,   out_max=60,  smooth=0.25, curve="exp"),
            ModRule(source="complexity",param_key="density",    out_min=0.25,out_max=1.0, smooth=0.20, curve="log"),
            ModRule(source="phrase_tension", param_key="phase", out_min=0.0, out_max=1.0, smooth=0.25, curve="linear"),
            ModRule(source="transition_urgency", param_key="rate", out_min=0.5, out_max=8.0, smooth=0.10, curve="log"),
        ]
    )

def _default_pump_mod_config() -> TypeModConfig:
    return TypeModConfig(
        track_type="pump",
        enabled=False,
        rules=[
            ModRule(source="rms",   param_key="max",   out_min=100, out_max=255, smooth=0.10, curve="log"),
            ModRule(source="bpm",   param_key="speed", out_min=0.3, out_max=4.0, in_min=60, in_max=180, smooth=0.08, curve="linear"),
            ModRule(source="bass",  param_key="min",   out_min=0,   out_max=80,  smooth=0.20, curve="exp"),
            ModRule(source="buildup", param_key="max", out_min=120, out_max=255, smooth=0.18, curve="log"),
            ModRule(source="device_density", param_key="density", out_min=0.25, out_max=1.0, smooth=0.20, curve="linear"),
        ]
    )

def _default_motor_mod_config() -> TypeModConfig:
    return TypeModConfig(
        track_type="motor",
        enabled=False,
        rules=[
            ModRule(source="rms",     param_key="speed",      out_min=0.2, out_max=3.0, smooth=0.15, curve="linear"),
            ModRule(source="bpm",     param_key="speed",      out_min=0.5, out_max=4.0, in_min=60, in_max=180, smooth=0.06, curve="linear"),
            ModRule(source="melody",  param_key="phase_step", out_min=0.0, out_max=0.25, smooth=0.20, curve="linear"),
            ModRule(source="phrase_tension", param_key="phase_step", out_min=0.02, out_max=0.35, smooth=0.20, curve="log"),
            ModRule(source="transition_urgency", param_key="speed", out_min=0.4, out_max=4.5, smooth=0.12, curve="log"),
        ]
    )

def _default_valve_mod_config() -> TypeModConfig:
    return TypeModConfig(
        track_type="valve",
        enabled=False,
        rules=[
            ModRule(source="kick",  param_key="rate",  out_min=0.5, out_max=8.0, smooth=0.10, curve="exp"),
            ModRule(source="snare", param_key="max",   out_min=100, out_max=255, smooth=0.15, curve="linear"),
            ModRule(source="bpm",   param_key="speed", out_min=0.5, out_max=6.0, in_min=60, in_max=180, smooth=0.05, curve="linear"),
            ModRule(source="onset_density", param_key="density", out_min=0.2, out_max=1.0, in_min=0.0, in_max=8.0, smooth=0.10, curve="log"),
            ModRule(source="bar_progress", param_key="phase", out_min=0.0, out_max=1.0, smooth=0.0, curve="linear"),
        ]
    )

def get_default_mod_config(track_type: str) -> TypeModConfig:
    factories = {
        "light": _default_light_mod_config,
        "pump":  _default_pump_mod_config,
        "motor": _default_motor_mod_config,
        "valve": _default_valve_mod_config,
    }
    fn = factories.get(track_type)
    if fn:
        return fn()
    return TypeModConfig(track_type=track_type, enabled=False, rules=[])


# ─────────────────────────────────────────────────────────────────────────────
#  调制引擎
# ─────────────────────────────────────────────────────────────────────────────
class AudioParamModulator:
    """
    音频参数自动调制引擎。

    工作方式：
      1. 每帧收到 MusicStructureEvent（analysis_tick）
      2. 提取各音频源的当前值
      3. 对每种效果类型，遍历其启用的调制规则
      4. 计算目标参数值，应用平滑
      5. 将调制后的参数合并到当前效果参数中
    """

    def __init__(self):
        # 每种效果类型的调制配置
        self._configs: Dict[str, TypeModConfig] = {}
        from .music_to_effect_bridge import TRACK_TYPES
        for tt in TRACK_TYPES:
            self._configs[tt] = get_default_mod_config(tt)

        # 平滑状态：track_type -> param_key -> current_value
        self._smooth_state: Dict[str, Dict[str, float]] = {
            tt: {} for tt in TRACK_TYPES
        }
        # fir5 加权平滑历史：track_type -> param_key -> deque(maxlen=5)
        self._fir_state: Dict[str, Dict[str, deque]] = {
            tt: {} for tt in TRACK_TYPES
        }

        # 全局开关
        self._global_enabled: bool = False

        # 上次调制时间（节流）
        self._last_mod_t: float = 0.0
        self._min_dt: float = 1.0 / 40.0  # 40fps

    @property
    def global_enabled(self) -> bool:
        return self._global_enabled

    def set_global_enabled(self, enabled: bool):
        self._global_enabled = bool(enabled)
        if not enabled:
            # 清空平滑状态
            for tt in self._smooth_state:
                self._smooth_state[tt].clear()
            for tt in self._fir_state:
                self._fir_state[tt].clear()

    def get_config(self, track_type: str) -> TypeModConfig:
        return self._configs.get(track_type, TypeModConfig(track_type=track_type))

    def set_config(self, config: TypeModConfig):
        self._configs[config.track_type] = config

    def is_type_enabled(self, track_type: str) -> bool:
        cfg = self._configs.get(track_type)
        return self._global_enabled and cfg is not None and cfg.enabled

    def set_type_enabled(self, track_type: str, enabled: bool):
        cfg = self._configs.get(track_type)
        if cfg is not None:
            cfg.enabled = enabled

    def add_rule(self, track_type: str, rule: ModRule):
        cfg = self._configs.get(track_type)
        if cfg is not None:
            cfg.rules.append(rule)

    def remove_rule(self, track_type: str, index: int):
        cfg = self._configs.get(track_type)
        if cfg is not None and 0 <= index < len(cfg.rules):
            cfg.rules.pop(index)

    def _extract_sources(self, ev) -> Dict[str, float]:
        """从 MusicStructureEvent 中提取所有音频源值（含 AI 编曲语义上下文）。"""
        bands = list(getattr(ev, "bands", [0.0] * 8))
        while len(bands) < 8:
            bands.append(0.0)

        instruments = getattr(ev, "instruments", {})
        bpm = float(getattr(ev, "bpm", 120.0))
        extras = getattr(ev, "extras", {}) or {}
        ctx = getattr(ev, "music_context", None)

        def ctx_float(name: str, default: float = 0.0) -> float:
            if ctx is None:
                return float(default)
            try:
                return float(getattr(ctx, name, default))
            except Exception:
                logger.debug("audio_param_modulator: failed to get ctx float value, using default")
                return float(default)

        complexity = 0.5
        device_density = 0.5
        transition_urgency = 0.5
        if ctx is not None:
            try:
                complexity = float(ctx.suggested_complexity_multiplier()) / 2.0
            except Exception:
                logger.debug("audio_param_modulator: failed to get suggested_complexity_multiplier")
                complexity = 0.5
            try:
                device_density = float(ctx.suggested_device_density())
            except Exception:
                logger.debug("audio_param_modulator: failed to get suggested_device_density")
                device_density = 0.5
            try:
                transition_urgency = float(ctx.suggested_transition_urgency())
            except Exception:
                logger.debug("audio_param_modulator: failed to get suggested_transition_urgency")
                transition_urgency = 0.5

        return {
            "rms":        float(getattr(ev, "rms", 0.0)),
            "peak":       float(getattr(ev, "peak", 0.0)),
            "bpm":        bpm,
            "beat_phase": float(getattr(ev, "beat_phase", 0.0)),
            "band_sub":   bands[0],
            "band_bass":  bands[1],
            "band_mid":   (bands[2] + bands[3]) / 2.0,
            "band_high":  (bands[4] + bands[5]) / 2.0,
            "band_air":   (bands[6] + bands[7]) / 2.0,
            "kick":       float(instruments.get("kick", 0.0)),
            "snare":      float(instruments.get("snare", 0.0)),
            "hihat":      float(instruments.get("hihat", 0.0)),
            "bass":       float(instruments.get("bass", 0.0)),
            "melody":     float(instruments.get("melody", 0.0)),
            "pad":        float(instruments.get("pad", 0.0)),
            "flux":       float(extras.get("spectral_flux", extras.get("flux", getattr(ev, "novelty", 0.0)))),
            "onset_density": float(extras.get("onset_density", extras.get("rhythmic_density", 0.0))),
            "section_confidence": float(extras.get("section_confidence", extras.get("confidence", 0.0))),
            "buildup":    float(extras.get("buildup_intensity", extras.get("buildup", ctx_float("buildup_intensity", 0.0)))),
            "bar_energy": float(getattr(ev, "bar_energy", ctx_float("bar_energy", 0.0))),
            "bar_progress": ctx_float("bar_progress", 0.0),
            "phrase_tension": ctx_float("phrase_tension", 0.5),
            "phrase_release": ctx_float("phrase_release", 0.5),
            "phrase_progress": ctx_float("phrase_progress", 0.0),
            "texture_density": ctx_float("phrase_texture_density", 0.5),
            "global_energy": ctx_float("global_energy_position", 0.5),
            "complexity":  float(max(0.0, min(1.0, complexity))),
            "device_density": float(max(0.0, min(1.0, device_density))),
            "transition_urgency": float(max(0.0, min(1.0, transition_urgency))),
        }

    def _smooth(self, track_type: str, param_key: str,
                target: float, alpha: float, mode: str = "ema") -> float:
        """参数平滑。
        ema : 一阶指数(alpha=0 无平滑, alpha→1 完全锁定)——默认，逐字节兼容旧路径。
        fir5: 加权核 (1,2,3,2,1)/9，单帧尖峰最多只透 1/3，抗瞬时爆音、保留强拍。"""
        state = self._smooth_state.setdefault(track_type, {})
        if mode == "fir5":
            fstate = self._fir_state.setdefault(track_type, {})
            hist = fstate.get(param_key)
            if hist is None:
                hist = deque([target] * 5, maxlen=5)  # 初值=target，无启动暂态
                fstate[param_key] = hist
            hist.append(target)
            smoothed = (hist[0] + 2.0 * hist[1] + 3.0 * hist[2]
                        + 2.0 * hist[3] + hist[4]) / 9.0
            state[param_key] = smoothed
            return smoothed
        cur = state.get(param_key, target)
        smoothed = cur + (1.0 - alpha) * (target - cur)
        state[param_key] = smoothed
        return smoothed

    def compute_modulated_params(self, track_type: str,
                                  base_params: Dict[str, Any],
                                  ev) -> Dict[str, Any]:
        """
        根据音频事件计算调制后的参数字典。
        返回合并了调制值的新参数字典（不修改原始 base_params）。
        """
        if not self.is_type_enabled(track_type):
            return dict(base_params)

        cfg = self._configs.get(track_type)
        if cfg is None or not cfg.rules:
            return dict(base_params)

        sources = self._extract_sources(ev)
        result = dict(base_params)

        for rule in cfg.rules:
            if not rule.enabled:
                continue
            raw = sources.get(rule.source, 0.0)
            target = rule.map_value(raw)
            smoothed = self._smooth(track_type, rule.param_key, target, rule.smooth,
                                    getattr(rule, "smooth_mode", "ema"))
            result[rule.param_key] = smoothed

        return result

    def to_dict(self) -> dict:
        return {
            "global_enabled": self._global_enabled,
            "configs": {
                tt: cfg.to_dict()
                for tt, cfg in self._configs.items()
            }
        }

    def load_dict(self, d: dict):
        self._global_enabled = bool(d.get("global_enabled", False))
        configs_data = d.get("configs", {})
        for tt, cfg_data in configs_data.items():
            self._configs[tt] = TypeModConfig.from_dict(cfg_data)
        # 清空平滑状态
        for tt in self._smooth_state:
            self._smooth_state[tt].clear()
        for tt in self._fir_state:
            self._fir_state[tt].clear()


# ─────────────────────────────────────────────────────────────────────────────
#  全局单例
# ─────────────────────────────────────────────────────────────────────────────
_global_modulator: Optional[AudioParamModulator] = None

def get_global_modulator() -> AudioParamModulator:
    global _global_modulator
    if _global_modulator is None:
        _global_modulator = AudioParamModulator()
    return _global_modulator
