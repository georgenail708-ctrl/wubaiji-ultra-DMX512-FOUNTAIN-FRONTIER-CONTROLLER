"""
music_structure_analyzer.py — 音乐结构深度分析器（v26 精度增强版）
================================================================
v26 重大改进：
  - 多维度段落判定：能量比值 + 频谱复杂度 + 节奏密度 + 乐器组合特征
  - 乐器在场度精度提升：滑动窗口平滑 + 自适应阈值 + 频谱包络匹配
  - 段落转换置信度评分系统：多个特征加权投票
  - 更精细的频谱变化率分析（spectral flux）
  - 节奏密度追踪（onset density）
  - 频谱复杂度（spectral flatness近似）
  - 段落历史记忆：避免频繁来回切换
  - buildup检测增强：渐进式能量+频率上升
  - drop检测增强：不仅依赖静音→爆发，还检测频谱突变
  - 更短的pending_hold允许更快响应
  - 乐器组合模式识别（如kick+bass=drop特征）
"""
from __future__ import annotations
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger(__name__)

INSTRUMENTS = ["kick", "snare", "hihat", "bass", "melody", "pad"]


class SectionLabel:
    INTRO     = "intro"
    VERSE     = "verse"
    BUILD     = "build"
    CHORUS    = "chorus"
    DROP      = "drop"
    BREAKDOWN = "breakdown"
    OUTRO     = "outro"
    UNKNOWN   = "unknown"
    ALL = [INTRO, VERSE, BUILD, CHORUS, DROP, BREAKDOWN, OUTRO, UNKNOWN]


@dataclass
class MusicStructureEvent:
    t: float = 0.0
    event_type: str = "tick"
    rms: float = 0.0
    peak: float = 0.0
    bands: List[float] = field(default_factory=lambda: [0.0] * 8)
    bpm: float = 120.0
    beat_phase: float = 0.0
    is_beat: bool = False
    instruments: Dict[str, float] = field(
        default_factory=lambda: {k: 0.0 for k in INSTRUMENTS})
    section_label: str = SectionLabel.UNKNOWN
    section_age: float = 0.0
    energy_short: float = 0.0
    energy_long: float = 0.0
    spectral_centroid: float = 0.0
    novelty: float = 0.0
    prev_section: str = ""
    onset_instrument: str = ""
    bar_number: int = 0
    beat_in_bar: int = 0
    beats_per_bar: int = 4
    tempo_delta: float = 0.0
    bar_energy: float = 0.0
    has_audio: bool = False
    extras: Dict[str, float] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
#  乐器频谱特征模板（v26增强版）
# ─────────────────────────────────────────────────────────────────────────────
# 格式: (频段权重[8], 检测模式, 基础阈值, 衰减速率, 最小持续帧数)
_INSTRUMENT_PROFILES: Dict[str, Tuple[List[float], str, float, float, int]] = {
    "kick":   ([1.0, 0.7, 0.15, 0.0, 0.0, 0.0, 0.0, 0.0], "onset", 0.10, 0.85, 2),
    "snare":  ([0.1, 0.25, 0.5, 0.7, 0.55, 0.25, 0.05, 0.0], "onset", 0.12, 0.80, 2),
    "hihat":  ([0.0, 0.0, 0.0, 0.05, 0.25, 0.55, 0.8, 1.0], "onset", 0.14, 0.75, 2),
    "bass":   ([0.85, 1.0, 0.55, 0.15, 0.0, 0.0, 0.0, 0.0], "sustain", 0.10, 0.92, 4),
    "melody": ([0.0, 0.1, 0.35, 0.65, 1.0, 0.8, 0.4, 0.1], "sustain", 0.08, 0.90, 4),
    "pad":    ([0.1, 0.2, 0.4, 0.5, 0.5, 0.4, 0.3, 0.2], "sustain", 0.06, 0.93, 6),
}


def _weighted_band_score(bands, weights):
    n = min(len(bands), len(weights))
    if n == 0:
        return 0.0
    s = sum(b * w for b, w in zip(bands[:n], weights[:n]))
    w_sum = sum(weights[:n])
    return float(s / max(w_sum, 1e-6))


def _spectral_flatness(bands):
    """频谱平坦度近似：几何均值/算术均值，值越高越像噪声"""
    arr = [max(b, 1e-10) for b in bands]
    n = len(arr)
    if n == 0:
        return 0.0
    geo_mean = math.exp(sum(math.log(x) for x in arr) / n)
    arith_mean = sum(arr) / n
    return float(geo_mean / max(arith_mean, 1e-10))


def _spectral_flux(bands, prev_bands):
    """频谱通量：当前帧与前帧的频谱差异（只计算正向变化）"""
    if prev_bands is None or len(prev_bands) != len(bands):
        return 0.0
    flux = sum(max(0.0, b - p) for b, p in zip(bands, prev_bands))
    return float(flux)


def _msa_frame_gate(msa, frame, t_override) -> bool:
    """★ R193/P1-12（AUD-02）：帧闸门（模块级：_on_frame 行数/CC 基线）。

    返回 True=跳过本帧：主开关关 / 无音频（v26 原语义）/ 实时路径隔帧
    ~20Hz（全分析链 queued 落 GUI 线程常驻；离线烘焙 t_override 不受限）。
    """
    if not getattr(msa, "_enabled", True):
        return True
    if not getattr(frame, "has_audio", True):  # 默认True兼容旧版
        return True
    if t_override is not None:
        return False
    msa._rt_frame_i = getattr(msa, "_rt_frame_i", 0) + 1
    return msa._rt_frame_i % 2 == 1


# ═══════════════════════════════════════════════════════════════════════════
#  音乐结构分析器主类
# ═══════════════════════════════════════════════════════════════════════════
class MusicStructureAnalyzer(QObject):
    analysis_tick   = pyqtSignal(object)
    structure_event = pyqtSignal(object)
    section_changed = pyqtSignal(str, str)
    drop_detected   = pyqtSignal(float)
    instrument_onset = pyqtSignal(str, float)
    bar_changed     = pyqtSignal(int, int)
    tempo_changed   = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fps_estimate = 40.0

        # ── 能量追踪（多时间尺度）──
        self._rms_short = deque(maxlen=int(0.3 * 40))
        self._rms_medium = deque(maxlen=int(1.5 * 40))
        self._rms_long = deque(maxlen=int(5.0 * 40))
        self._rms_silence = deque(maxlen=int(1.0 * 40))
        self._rms_very_long = deque(maxlen=int(15.0 * 40))

        # ── 频谱追踪 ──
        self._centroid_hist = deque(maxlen=int(5.0 * 40))
        self._flux_hist = deque(maxlen=int(3.0 * 40))
        self._flatness_hist = deque(maxlen=int(3.0 * 40))
        self._novelty_hist = deque(maxlen=int(3.0 * 40))
        self._bands_prev = None
        self._bands_smooth = [0.0] * 8
        # C2 自适应分位归一（opt-in，默认关→逐字节走原路径）
        self._adaptive_band_norm = False
        self._band_hist = [deque(maxlen=int(8.0 * 40)) for _ in range(8)]
        self._bands_norm = [0.0] * 8   # 归一后频段能量（开启时填充，供下游可选使用）

        # ── 乐器在场度追踪（v26增强）──
        self._inst_smooth = {k: 0.0 for k in INSTRUMENTS}
        self._inst_history = {k: deque(maxlen=int(2.0 * 40)) for k in INSTRUMENTS}
        self._inst_prev = {k: 0.0 for k in INSTRUMENTS}
        self._inst_last_onset_t = {k: -10.0 for k in INSTRUMENTS}
        self._inst_sustained_frames = {k: 0 for k in INSTRUMENTS}

        # ── 节奏密度追踪 ──
        self._onset_times = deque(maxlen=200)
        self._onset_density = 0.0
        self._beat_strength_hist = deque(maxlen=int(4.0 * 40))

        # ── 段落判定 ──
        self._section_label = SectionLabel.UNKNOWN
        self._section_started_t = 0.0
        self._pending_label = SectionLabel.UNKNOWN
        self._pending_since = 0.0
        self._pending_hold_s = 0.6
        self._section_confidence = 0.0
        self._section_history = deque(maxlen=20)
        self._last_section_scores = {}

        # ── buildup/drop检测 ──
        self._was_silent = False
        self._silent_since_t = 0.0
        self._buildup_active = False
        self._buildup_started_t = 0.0
        self._buildup_intensity = 0.0
        self._last_drop_t = -10.0
        self._drop_cooldown_s = 2.5

        # ── 灵敏度参数 ──
        self._sens_section = 0.45
        self._sens_instrument = 1.2
        self._sens_drop = 2.2
        self._noise_floor = 0.02
        self._noise_floor_adaptive = 0.02

        # ── 节拍追踪 ──
        self._beats_per_bar = 4
        self._beat_count = 0
        self._bar_number = 0
        self._beat_in_bar = 0
        self._last_beat_t = -1.0
        self._beat_intervals = deque(maxlen=32)
        self._bar_rms_accum = []
        self._bar_energy = 0.0

        # ── 速度追踪 ──
        self._bpm_history = deque(maxlen=int(8.0 * 40))
        self._last_tempo_change_t = -10.0
        self._tempo_change_cooldown = 2.0
        self._tempo_delta = 0.0

        # ── 全局状态 ──
        self._enabled = True
        self._attached_to = None
        self._t0 = time.monotonic()
        self._frame_count = 0

    def attach(self, analyzer):
        self.detach()
        try:
            analyzer.frame_ready.connect(self._on_frame)
            self._attached_to = analyzer
        except Exception:
            logger.debug("music_structure_analyzer: failed to attach analyzer")
            self._attached_to = None

    def detach(self):
        if self._attached_to is None:
            return
        try:
            self._attached_to.frame_ready.disconnect(self._on_frame)
        except Exception:
            logger.debug("music_structure_analyzer: failed to disconnect frame_ready")
            pass
        self._attached_to = None

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)

    @property
    def enabled(self):
        return self._enabled

    @property
    def section_label(self):
        return self._section_label

    def set_sensitivity(self, section=None, instrument=None, drop=None,
                        noise_floor=None, section_hold=None):
        if section is not None:
            self._sens_section = float(max(0.02, min(3.0, section)))
        if instrument is not None:
            self._sens_instrument = float(max(0.1, min(6.0, instrument)))
        if drop is not None:
            self._sens_drop = float(max(1.0, min(10.0, drop)))
        if noise_floor is not None:
            self._noise_floor = float(max(0.0, min(0.3, noise_floor)))
        if section_hold is not None:
            self._pending_hold_s = float(max(0.1, min(5.0, section_hold)))

    def get_sensitivity(self):
        return {
            "section": self._sens_section,
            "instrument": self._sens_instrument,
            "drop": self._sens_drop,
            "noise_floor": self._noise_floor,
            "section_hold": self._pending_hold_s,
        }

    def set_beats_per_bar(self, n):
        self._beats_per_bar = max(1, min(64, n))

    def reset(self):
        self._rms_short.clear()
        self._rms_medium.clear()
        self._rms_long.clear()
        self._rms_silence.clear()
        self._rms_very_long.clear()
        self._centroid_hist.clear()
        self._flux_hist.clear()
        self._flatness_hist.clear()
        self._novelty_hist.clear()
        self._bands_prev = None
        self._bands_smooth = [0.0] * 8
        self._section_label = SectionLabel.UNKNOWN
        self._section_started_t = 0.0
        self._pending_label = SectionLabel.UNKNOWN
        self._pending_since = 0.0
        self._section_confidence = 0.0
        self._section_history.clear()
        self._last_section_scores = {}
        self._buildup_active = False
        self._buildup_intensity = 0.0
        self._last_drop_t = -10.0
        self._inst_smooth = {k: 0.0 for k in INSTRUMENTS}
        for k in INSTRUMENTS:
            self._inst_history[k].clear()
        self._inst_prev = {k: 0.0 for k in INSTRUMENTS}
        self._inst_last_onset_t = {k: -10.0 for k in INSTRUMENTS}
        self._inst_sustained_frames = {k: 0 for k in INSTRUMENTS}
        self._onset_times.clear()
        self._onset_density = 0.0
        self._beat_strength_hist.clear()
        self._beat_count = 0
        self._bar_number = 0
        self._beat_in_bar = 0
        self._last_beat_t = -1.0
        self._beat_intervals.clear()
        self._bar_rms_accum.clear()
        self._bar_energy = 0.0
        self._bpm_history.clear()
        self._last_tempo_change_t = -10.0
        self._tempo_delta = 0.0
        self._t0 = time.monotonic()
        self._frame_count = 0

    # ─────────────────────────────────────────────────────────────────────
    #  核心帧处理
    # ─────────────────────────────────────────────────────────────────────
    def _on_frame(self, frame, t_override: float = None):
        # ★ v26 无音频早退 + R193/P1-12 实时隔帧：统一在 _msa_frame_gate
        if _msa_frame_gate(self, frame, t_override):
            return

        # ★ 离线烘焙支持：t_override 不为 None 时用调用方给的时间戳（按音频位置推进），
        #   而非 wall-clock。这样可以"快进喂整首歌"做离线分析，时序特征仍按真实
        #   播放时长展开（段落保持/节拍间隔/能量窗口都吃这个 t）。实时路径不传，
        #   逐字节走原 time.monotonic 逻辑。
        if t_override is not None:
            t = float(t_override)
        else:
            t = time.monotonic() - self._t0
            # ★ R193/P1-12：实时隔帧 ~20Hz 已在 _msa_frame_gate 统一处理
            #   （此处勿再计数——双闸门共享计数器会把实时分析归零）。
        self._frame_count += 1

        rms = getattr(frame, "rms", 0.0)
        peak = getattr(frame, "peak", 0.0)
        bands = list(getattr(frame, "bands", [0.0] * 8))
        bpm = float(getattr(frame, "bpm", 120.0))
        beat_phase = float(getattr(frame, "beat_phase", 0.0))
        is_beat = bool(getattr(frame, "is_beat", False))

        # ── 多时间尺度能量追踪 ──
        self._rms_short.append(rms)
        self._rms_medium.append(rms)
        self._rms_long.append(rms)
        self._rms_silence.append(rms)
        self._rms_very_long.append(rms)

        # ── 频谱分析（v26增强）──
        alpha_bands = 0.3
        for i in range(min(8, len(bands))):
            self._bands_smooth[i] = (alpha_bands * bands[i] +
                                     (1 - alpha_bands) * self._bands_smooth[i])

        # C2 自适应分位归一（opt-in）：用每频段 5%/95% 分位拉开动态，
        # 安静曲也能跑满、响曲不全顶 1.0。默认关时不计算、不影响原逻辑。
        if self._adaptive_band_norm:
            for i in range(min(8, len(bands))):
                hist = self._band_hist[i]
                hist.append(bands[i])
                if len(hist) >= 16:
                    s = sorted(hist)
                    q05 = s[max(0, int(0.05 * (len(s) - 1)))]
                    q95 = s[min(len(s) - 1, int(0.95 * (len(s) - 1)))]
                    rng = q95 - q05
                    self._bands_norm[i] = (max(0.0, min(1.0, (bands[i] - q05) / rng))
                                           if rng > 1e-9 else self._bands_smooth[i])
                else:
                    # 暖机：窗未满，回退平滑值（不除零）
                    self._bands_norm[i] = self._bands_smooth[i]

        centroid = self._compute_centroid(bands)
        self._centroid_hist.append(centroid)

        flux = _spectral_flux(bands, self._bands_prev)
        self._flux_hist.append(flux)

        flatness = _spectral_flatness(bands)
        self._flatness_hist.append(flatness)

        novelty = self._compute_novelty(bands)
        self._novelty_hist.append(novelty)
        self._bands_prev = list(bands)

        # ── 自适应噪声底 ──
        if len(self._rms_very_long) >= 40:
            sorted_rms = sorted(self._rms_very_long)
            low_count = max(4, len(sorted_rms) // 10)
            self._noise_floor_adaptive = max(
                self._noise_floor,
                float(np.mean(sorted_rms[:low_count])) * 1.5
            )

        # ── 能量计算 ──
        e_short = float(np.mean(list(self._rms_short))) if self._rms_short else max(rms, 1e-9)
        e_medium = float(np.mean(list(self._rms_medium))) if self._rms_medium else e_short
        e_long = float(np.mean(list(self._rms_long))) if self._rms_long else max(rms, 1e-9)

        # ── 乐器在场度计算（v26增强）──
        onset = novelty + flux * 0.5
        instruments = self._compute_instruments_v26(bands, onset, e_short, t)

        # ── 节奏密度计算 ──
        if novelty > 0.15 or flux > 0.3:
            self._onset_times.append(t)
        recent_onsets = [ot for ot in self._onset_times if (t - ot) < 2.0]
        self._onset_density = len(recent_onsets) / 2.0

        # ── 节拍强度追踪 ──
        if is_beat:
            self._beat_strength_hist.append(rms)

        # ── 小节/节拍追踪 ──
        bar_changed = False
        if is_beat:
            if self._last_beat_t > 0:
                interval = t - self._last_beat_t
                if 0.15 < interval < 2.0:
                    self._beat_intervals.append(interval)
            self._last_beat_t = t
            self._beat_count += 1
            old_bar = self._bar_number
            self._beat_in_bar = (self._beat_count - 1) % self._beats_per_bar
            self._bar_number = (self._beat_count - 1) // self._beats_per_bar
            self._bar_rms_accum.append(rms)
            if self._bar_number != old_bar:
                bar_changed = True
                if self._bar_rms_accum:
                    self._bar_energy = float(np.mean(self._bar_rms_accum))
                self._bar_rms_accum = [rms]
        else:
            self._bar_rms_accum.append(rms)

        # ── 速度变化检测 ──
        self._bpm_history.append(bpm)
        tempo_changed_flag = False
        if len(self._bpm_history) >= 40:
            recent = list(self._bpm_history)
            half = len(recent) // 2
            bpm_early = float(np.mean(recent[:half]))
            bpm_late = float(np.mean(recent[half:]))
            self._tempo_delta = bpm_late - bpm_early
            if (abs(self._tempo_delta) > 5.0
                    and (t - self._last_tempo_change_t) > self._tempo_change_cooldown):
                tempo_changed_flag = True
                self._last_tempo_change_t = t

        # ── 段落分类（v26多维度置信度评分）──
        prev_section = self._section_label
        new_label = self._classify_section_v26(
            rms, e_short, e_medium, e_long, centroid, flux, flatness,
            novelty, instruments, t
        )
        section_changed = self._update_section_state_v26(new_label, t)

        # ── drop/buildup检测（v26增强）──
        is_drop = self._detect_drop_v26(rms, e_short, e_long, flux, instruments, t)
        buildup_change = self._detect_buildup_v26(e_short, e_medium, centroid, flux, t)

        # ── 构建事件 ──
        ev = MusicStructureEvent(
            t=t, event_type="tick",
            rms=rms, peak=peak, bands=bands,
            bpm=bpm, beat_phase=beat_phase, is_beat=is_beat,
            instruments=instruments,
            section_label=self._section_label,
            section_age=max(0.0, t - self._section_started_t),
            energy_short=e_short, energy_long=e_long,
            spectral_centroid=centroid, novelty=novelty,
            bar_number=self._bar_number, beat_in_bar=self._beat_in_bar,
            beats_per_bar=self._beats_per_bar,
            tempo_delta=self._tempo_delta, bar_energy=self._bar_energy,
            has_audio=True,
            extras={
                # 兼容旧键与深度分析器期望的新键
                "buildup": self._buildup_intensity,
                "buildup_intensity": self._buildup_intensity,
                "buildup_active": bool(self._buildup_active),
                "onset": onset,
                "flux": flux,
                "spectral_flux": flux,
                "flatness": flatness,
                "spectral_flatness": flatness,
                "onset_density": self._onset_density,
                "rhythmic_density": self._onset_density,
                "confidence": self._section_confidence,
                "section_confidence": self._section_confidence,
                "energy_ratio": float(e_short / max(e_long, 1e-6)),
                "bass_energy": float(sum(bands[:2]) / 2.0) if bands else 0.0,
                "mid_energy": float(sum(bands[2:5]) / 3.0) if len(bands) >= 5 else 0.0,
                "high_energy": float(sum(bands[5:8]) / 3.0) if len(bands) >= 8 else 0.0,
            },
        )

        # ── 发射事件 ──
        if is_drop:
            ev_drop = self._clone_event(ev, event_type="drop")
            self.structure_event.emit(ev_drop)
            self.drop_detected.emit(rms)

        if section_changed:
            ev_sc = self._clone_event(ev, event_type="section_change",
                                      prev_section=prev_section)
            self.structure_event.emit(ev_sc)
            self.section_changed.emit(prev_section, self._section_label)

        if buildup_change == "start":
            self.structure_event.emit(self._clone_event(ev, event_type="buildup_start"))
        elif buildup_change == "end":
            self.structure_event.emit(self._clone_event(ev, event_type="buildup_end"))

        if bar_changed:
            ev_bar = self._clone_event(ev, event_type="bar_change")
            self.structure_event.emit(ev_bar)
            self.bar_changed.emit(self._bar_number, self._beat_in_bar)

        if tempo_changed_flag:
            ev_tempo = self._clone_event(ev, event_type="tempo_change")
            self.structure_event.emit(ev_tempo)
            self.tempo_changed.emit(bpm, self._tempo_delta)

        # ── 乐器onset事件（v26: 扩展到所有乐器）──
        for inst in INSTRUMENTS:
            level = instruments.get(inst, 0.0)
            prev_lvl = self._inst_prev.get(inst, 0.0)
            last_t = self._inst_last_onset_t.get(inst, -10.0)
            cooldown = 0.08 if inst in ("kick", "snare", "hihat") else 0.15
            if level > 0.4 and prev_lvl < 0.25 and (t - last_t) > cooldown:
                self._inst_last_onset_t[inst] = t
                ev_io = self._clone_event(
                    ev, event_type="instrument_onset", onset_instrument=inst)
                self.structure_event.emit(ev_io)
                self.instrument_onset.emit(inst, level)

        self._inst_prev = dict(instruments)
        self.analysis_tick.emit(ev)
        return ev

    # ─────────────────────────────────────────────────────────────────────
    #  频谱计算
    # ─────────────────────────────────────────────────────────────────────
    def _compute_centroid(self, bands):
        s = sum(bands)
        if s <= 1e-6:
            return 0.0
        n = len(bands)
        if n <= 1:
            return 0.0
        denom = s * max(1, n - 1)
        if denom <= 1e-12:
            return 0.0
        return float(sum(i * b for i, b in enumerate(bands)) / denom)

    def _compute_novelty(self, bands):
        prev = self._bands_prev
        if prev is None or len(prev) != len(bands):
            return 0.0
        n = len(bands)
        if n == 0:
            return 0.0
        d = sum(abs(a - b) for a, b in zip(bands, prev))
        return float(min(1.0, d / max(1.0, n * 0.3)))

    # ─────────────────────────────────────────────────────────────────────
    #  乐器在场度计算（v26增强版）
    # ─────────────────────────────────────────────────────────────────────
    def _compute_instruments_v26(self, bands, onset, e_short, t):
        """
        v26增强的乐器在场度计算：
        - 频谱包络匹配（加权频段得分）
        - 滑动窗口平滑（避免闪烁）
        - 自适应阈值（根据整体能量调整）
        - 持续帧计数（sustain类乐器需要连续多帧才确认）
        """
        # ★ v26修复：能量极低时直接返回全零，避免噪声底限产生虚假乐器识别
        if e_short < 1e-5 and max(bands) < 1e-5:
            # 让平滑值快速衰减到0
            for name in list(self._inst_smooth.keys()):
                self._inst_smooth[name] = self._inst_smooth[name] * 0.5
                self._inst_history[name].append(0.0)
                self._inst_sustained_frames[name] = 0
            return {name: 0.0 for name in _INSTRUMENT_PROFILES}

        out = {}
        energy_factor = 1.0
        if e_short < 0.1:
            energy_factor = 1.5
        elif e_short > 0.5:
            energy_factor = 0.8

        for name, (weights, mode, base_thr, decay, min_frames) in _INSTRUMENT_PROFILES.items():
            score = _weighted_band_score(bands, weights)

            if mode == "onset":
                trigger = onset * score * 1.5 + score * 0.3
            else:
                trigger = score * 1.2

            adaptive_thr = base_thr / (self._sens_instrument * energy_factor)
            raw = max(0.0, (trigger - adaptive_thr) / max(0.3, 1.0 - adaptive_thr))
            raw = min(1.0, raw * self._sens_instrument)

            if mode == "sustain":
                if raw > 0.2:
                    self._inst_sustained_frames[name] = min(
                        min_frames + 2,
                        self._inst_sustained_frames[name] + 1
                    )
                else:
                    self._inst_sustained_frames[name] = max(
                        0, self._inst_sustained_frames[name] - 1)
                if self._inst_sustained_frames[name] < min_frames:
                    raw *= self._inst_sustained_frames[name] / max(1, min_frames)

            if mode == "onset":
                alpha = 0.5
            else:
                alpha = 0.25

            prev_smooth = self._inst_smooth.get(name, 0.0)
            smoothed = alpha * raw + (1 - alpha) * prev_smooth

            if raw < prev_smooth:
                smoothed = prev_smooth * decay

            self._inst_smooth[name] = smoothed
            self._inst_history[name].append(smoothed)
            out[name] = float(min(1.0, max(0.0, smoothed)))

        return out

    # ─────────────────────────────────────────────────────────────────────
    #  段落分类（v26多维度置信度评分系统）
    # ─────────────────────────────────────────────────────────────────────
    def _classify_section_v26(self, rms, e_short, e_medium, e_long,
                              centroid, flux, flatness, novelty,
                              instruments, t):
        """
        v26多维度段落分类：为每个可能的段落计算置信度得分，选择最高分。
        """
        # ★ v26修复：绝对静音检测（rms极低时直接返回当前标签，不触发变化）
        ABSOLUTE_SILENCE = 1e-5  # 真正的零信号阈值
        if rms < ABSOLUTE_SILENCE and e_short < ABSOLUTE_SILENCE:
            return self._section_label  # 保持当前标签，不触发任何变化

        # 相对静音检测（信号低于噪声底限）
        if e_short < self._noise_floor_adaptive:
            if self._section_label in (SectionLabel.CHORUS, SectionLabel.DROP,
                                       SectionLabel.BUILD):
                return SectionLabel.BREAKDOWN
            if self._section_label in (SectionLabel.UNKNOWN, SectionLabel.INTRO):
                return SectionLabel.INTRO
            return SectionLabel.OUTRO

        # ── 计算各维度特征 ──
        ratio_short_long = e_short / max(self._noise_floor_adaptive, e_long, 1e-6)
        ratio_medium_long = e_medium / max(self._noise_floor_adaptive, e_long, 1e-6)

        ch = list(self._centroid_hist)
        centroid_rising = 0.0
        centroid_level = centroid
        if len(ch) >= 12:
            third = max(3, len(ch) // 3)
            head = float(np.mean(ch[:third]))
            tail = float(np.mean(ch[-third:]))
            centroid_rising = tail - head

        fh = list(self._flux_hist)
        avg_flux = float(np.mean(fh)) if fh else 0.0
        flux_rising = 0.0
        if len(fh) >= 12:
            third = max(3, len(fh) // 3)
            flux_rising = float(np.mean(fh[-third:])) - float(np.mean(fh[:third]))

        rms_list = list(self._rms_long)
        energy_rising = 0.0
        if len(rms_list) >= 20:
            third = max(5, len(rms_list) // 3)
            energy_rising = (float(np.mean(rms_list[-third:])) -
                           float(np.mean(rms_list[:third])))

        kick = instruments.get("kick", 0)
        snare = instruments.get("snare", 0)
        hihat = instruments.get("hihat", 0)
        bass = instruments.get("bass", 0)
        melody = instruments.get("melody", 0)
        pad = instruments.get("pad", 0)

        rhythm_total = kick + snare + hihat
        melodic_total = melody + pad
        low_freq = bass + kick
        age = t - self._section_started_t

        # ── 各段落置信度评分 ──
        scores = {}
        section_thr = self._sens_section

        # --- INTRO ---
        score_intro = 0.0
        if age < 3.0 and self._section_label == SectionLabel.UNKNOWN:
            score_intro += 0.5
        if ratio_short_long < 0.6:
            score_intro += 0.2
        if rhythm_total < 0.3:
            score_intro += 0.15
        if melodic_total > rhythm_total:
            score_intro += 0.1
        scores[SectionLabel.INTRO] = score_intro

        # --- VERSE ---
        score_verse = 0.0
        if 0.4 < ratio_short_long < (1.0 + section_thr * 0.5):
            score_verse += 0.35
        if melody > 0.2 or pad > 0.15:
            score_verse += 0.15
        if rhythm_total < 0.6:
            score_verse += 0.1
        if kick > 0.2 and kick < 0.5:
            score_verse += 0.1
        if centroid_level < 0.5:
            score_verse += 0.1
        if self._onset_density < 8.0:
            score_verse += 0.1
        scores[SectionLabel.VERSE] = score_verse

        # --- BUILD ---
        score_build = 0.0
        if centroid_rising > 0.03:
            score_build += 0.25
        if energy_rising > 0.02:
            score_build += 0.25
        if flux_rising > 0.05:
            score_build += 0.15
        if self._buildup_intensity > 0.3:
            score_build += 0.2
        if hihat > 0.3 and centroid_rising > 0.02:
            score_build += 0.1
        if self._onset_density > 6.0 and energy_rising > 0.01:
            score_build += 0.1
        scores[SectionLabel.BUILD] = score_build

        # --- CHORUS ---
        score_chorus = 0.0
        if ratio_short_long > (1.0 + section_thr * 0.6):
            score_chorus += 0.25
        if melody > 0.35:
            score_chorus += 0.2
        if rhythm_total > 0.4:
            score_chorus += 0.15
        if centroid_level > 0.35:
            score_chorus += 0.1
        if snare > 0.25:
            score_chorus += 0.1
        if self._onset_density > 5.0:
            score_chorus += 0.1
        if kick > 0.6 and bass > 0.5:
            score_chorus -= 0.15   # 纯低频轰鸣不是 chorus（drop 特征）
        scores[SectionLabel.CHORUS] = score_chorus

        # --- DROP ---
        score_drop = 0.0
        if ratio_short_long > (1.0 + section_thr):
            score_drop += 0.2
        if kick > 0.45:
            score_drop += 0.2
        if bass > 0.4:
            score_drop += 0.2
        if low_freq > 0.8:
            score_drop += 0.15
        if centroid_level < 0.4 and e_short > 0.3:
            score_drop += 0.1
        if self._onset_density > 8.0:
            score_drop += 0.1
        if self._section_label == SectionLabel.BUILD:
            score_drop += 0.15
        scores[SectionLabel.DROP] = score_drop

        # --- BREAKDOWN ---
        score_breakdown = 0.0
        if ratio_short_long < 0.5 and e_short > self._noise_floor_adaptive:
            score_breakdown += 0.25
        if rhythm_total < 0.2:
            score_breakdown += 0.2
        if pad > 0.2 and kick < 0.15:
            score_breakdown += 0.15
        if self._section_label in (SectionLabel.DROP, SectionLabel.CHORUS):
            if ratio_short_long < 0.7:
                score_breakdown += 0.15
        if self._onset_density < 3.0 and e_short > self._noise_floor_adaptive:
            score_breakdown += 0.1
        scores[SectionLabel.BREAKDOWN] = score_breakdown

        # --- OUTRO ---
        score_outro = 0.0
        # ★ R178 修：energy 下降是 outro 必要条件（高龄 verse 误判=抖动根源）
        if age > 20.0 and energy_rising < -0.02:
            score_outro += 0.3
        if ratio_short_long < 0.4 and age > 15.0 and energy_rising < -0.01:
            score_outro += 0.2
        if rhythm_total < 0.2 and age > 10.0 and energy_rising < 0.0:
            score_outro += 0.15
        if energy_rising < -0.03:
            score_outro += 0.15
        scores[SectionLabel.OUTRO] = score_outro

        current_bonus = 0.12 if age < 4.0 else 0.06   # 段落惯性
        if self._section_label in scores:
            scores[self._section_label] += current_bonus

        # ── 选择最高分 ──
        self._last_section_scores = scores
        best_label = max(scores, key=scores.get)
        self._section_confidence = scores[best_label]

        if scores[best_label] < 0.25:
            return self._section_label

        return best_label

    def _update_section_state_v26(self, candidate, t):
        """v26: 基于置信度的段落更新；R178 加回弹抑制。"""
        if candidate == self._section_label:
            self._pending_label = self._section_label
            self._pending_since = t
            return False

        if candidate != self._pending_label:
            self._pending_label = candidate
            self._pending_since = t
            return False

        # ★ R178 回弹抑制：2.5s 内禁再切（8.0 全窗实测压死真切换，已弃）
        if (t - self._section_started_t) < 2.5:
            self._pending_label = candidate
            self._pending_since = t
            return False

        confidence = self._section_confidence
        if confidence > 0.7:
            hold = self._pending_hold_s * 0.5
        elif confidence > 0.5:
            hold = self._pending_hold_s * 0.75
        else:
            hold = self._pending_hold_s

        if (t - self._pending_since) >= hold:
            self._section_label = candidate
            self._section_started_t = t
            self._section_history.append((t, candidate))
            return True
        return False

    # ─────────────────────────────────────────────────────────────────────
    #  Drop检测（v26增强版）
    # ─────────────────────────────────────────────────────────────────────
    def _detect_drop_v26(self, rms, e_short, e_long, flux, instruments, t):
        if (t - self._last_drop_t) < self._drop_cooldown_s:
            return False

        # 方式1: 传统静音→爆发
        recent = list(self._rms_silence)
        if len(recent) >= 6:
            threshold_silence = max(self._noise_floor_adaptive * 1.5, 0.03)
            had_silence = min(recent[:max(3, len(recent) // 3)]) < threshold_silence
            if had_silence and e_long > 1e-6:
                if (rms / max(e_long, 1e-6)) >= self._sens_drop and rms > 0.12:
                    self._last_drop_t = t
                    return True

        # 方式2: 频谱通量突增
        fh = list(self._flux_hist)
        if len(fh) >= 20:
            avg_flux = float(np.mean(fh[:-5]))
            recent_flux = float(np.mean(fh[-3:]))
            if recent_flux > avg_flux * 3.0 and rms > 0.2:
                kick = instruments.get("kick", 0)
                bass = instruments.get("bass", 0)
                if kick > 0.4 or bass > 0.4:
                    self._last_drop_t = t
                    return True

        # 方式3: buildup结束后的能量跳变
        if self._buildup_active and self._buildup_intensity > 0.6:
            if rms > e_long * 1.5 and rms > 0.2:
                kick = instruments.get("kick", 0)
                if kick > 0.35:
                    self._last_drop_t = t
                    self._buildup_active = False
                    self._buildup_intensity = 0.0
                    return True

        return False

    # ─────────────────────────────────────────────────────────────────────
    #  Buildup检测（v26增强版）
    # ─────────────────────────────────────────────────────────────────────
    def _detect_buildup_v26(self, e_short, e_medium, centroid, flux, t):
        ch = list(self._centroid_hist)
        rs = list(self._rms_long)
        fh = list(self._flux_hist)

        if len(ch) < 20 or len(rs) < 20:
            return ""

        win = max(12, int(2.5 * 40))
        ch_win = ch[-win:]
        rs_win = rs[-win:]
        fh_win = fh[-min(win, len(fh)):]

        third = max(3, len(ch_win) // 3)

        ch_rise = float(np.mean(ch_win[-third:]) - np.mean(ch_win[:third]))
        rs_rise = float(np.mean(rs_win[-third:]) - np.mean(rs_win[:third]))
        flux_rise = 0.0
        if len(fh_win) >= 6:
            f_third = max(2, len(fh_win) // 3)
            flux_rise = float(np.mean(fh_win[-f_third:]) - np.mean(fh_win[:f_third]))

        buildup_score = 0.0
        if ch_rise > 0.03:
            buildup_score += min(0.4, ch_rise * 4.0)
        if rs_rise > 0.02:
            buildup_score += min(0.3, rs_rise * 5.0)
        if flux_rise > 0.03:
            buildup_score += min(0.3, flux_rise * 3.0)

        if buildup_score > 0.3:
            self._buildup_intensity = min(1.0,
                self._buildup_intensity * 0.9 + buildup_score * 0.3)
        else:
            self._buildup_intensity *= 0.95

        is_buildup = buildup_score > 0.35 and e_short > self._noise_floor_adaptive * 2

        if is_buildup and not self._buildup_active:
            self._buildup_active = True
            self._buildup_started_t = t
            return "start"

        if (not is_buildup) and self._buildup_active:
            if buildup_score < 0.15 or (t - self._buildup_started_t) > 15.0:
                self._buildup_active = False
                self._buildup_intensity = 0.0
                return "end"

        return ""

    # ─────────────────────────────────────────────────────────────────────
    #  工具方法
    # ─────────────────────────────────────────────────────────────────────
    @staticmethod
    def _clone_event(ev, **overrides):
        new = MusicStructureEvent(
            t=ev.t, event_type=ev.event_type,
            rms=ev.rms, peak=ev.peak, bands=list(ev.bands),
            bpm=ev.bpm, beat_phase=ev.beat_phase, is_beat=ev.is_beat,
            instruments=dict(ev.instruments),
            section_label=ev.section_label, section_age=ev.section_age,
            energy_short=ev.energy_short, energy_long=ev.energy_long,
            spectral_centroid=ev.spectral_centroid, novelty=ev.novelty,
            prev_section=ev.prev_section, onset_instrument=ev.onset_instrument,
            bar_number=ev.bar_number, beat_in_bar=ev.beat_in_bar,
            beats_per_bar=ev.beats_per_bar,
            tempo_delta=ev.tempo_delta, bar_energy=ev.bar_energy,
            has_audio=getattr(ev, "has_audio", True),
            extras=dict(ev.extras),
        )
        for k, v in overrides.items():
            if hasattr(new, k):
                setattr(new, k, v)
        return new


# ─────────────────────────────────────────────────────────────────────────────
#  全局单例
# ─────────────────────────────────────────────────────────────────────────────
_global_msa = None


def get_global_structure_analyzer():
    global _global_msa
    if _global_msa is not None:
        try:
            _ = _global_msa.objectName()
        except RuntimeError:
            _global_msa = None
    if _global_msa is None:
        _global_msa = MusicStructureAnalyzer()
        try:
            from src.ui.audio_driver import get_audio_driver
        except Exception:
            logger.debug("music_structure_analyzer: failed to import audio_driver from src.ui")
            try:
                from ui.audio_driver import get_audio_driver
            except Exception:
                logger.debug("music_structure_analyzer: failed to import audio_driver from ui")
                get_audio_driver = None
        if get_audio_driver is not None:
            try:
                an = get_audio_driver()
                _global_msa.attach(an)
            except Exception:
                logger.debug("music_structure_analyzer: failed to attach global MSA to audio driver")
                pass
    return _global_msa
