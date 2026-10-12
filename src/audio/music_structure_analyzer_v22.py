"""
music_structure_analyzer.py — 音乐结构深度分析器（接口2 v22）
==========================================================
v22 增强：
  - 小节(bar)变化检测：基于BPM和拍号估算小节边界
  - 速度(tempo)变化检测：滑动窗口BPM差分，检测加速/减速
  - 节拍精度提升：更精细的beat_phase追踪
  - 新增事件类型：bar_change / tempo_change
  - 结构分类更粗略（减少误切换），小节检测更精细
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
    extras: Dict[str, float] = field(default_factory=dict)

_INSTRUMENT_PROFILES: Dict[str, Tuple[List[float], str, float]] = {
    "kick":   ([1.0, 0.6, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0], "onset", 0.15),
    "snare":  ([0.1, 0.3, 0.5, 0.7, 0.5, 0.2, 0.0, 0.0], "onset", 0.18),
    "hihat":  ([0.0, 0.0, 0.0, 0.1, 0.3, 0.6, 0.8, 1.0], "onset", 0.20),
    "bass":   ([0.8, 1.0, 0.5, 0.1, 0.0, 0.0, 0.0, 0.0], "sustain", 0.15),
    "melody": ([0.0, 0.1, 0.3, 0.6, 1.0, 0.8, 0.4, 0.1], "sustain", 0.12),
    "pad":    ([0.1, 0.2, 0.4, 0.5, 0.5, 0.4, 0.3, 0.2], "sustain", 0.10),
}

def _weighted_band_score(bands, weights):
    n = min(len(bands), len(weights))
    if n == 0:
        return 0.0
    s = sum(b * w for b, w in zip(bands[:n], weights[:n]))
    w_sum = sum(weights[:n])
    return float(s / max(w_sum, 1e-6))

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
        self._rms_short = deque(maxlen=int(0.4 * 40))
        self._rms_long = deque(maxlen=int(4.0 * 40))
        self._rms_silence = deque(maxlen=int(1.2 * 40))
        self._centroid_hist = deque(maxlen=int(4.0 * 40))
        self._bands_prev = None
        self._section_label = SectionLabel.UNKNOWN
        self._section_started_t = 0.0
        self._pending_label = SectionLabel.UNKNOWN
        self._pending_since = 0.0
        self._pending_hold_s = 1.2
        self._was_silent = False
        self._silent_since_t = 0.0
        self._buildup_active = False
        self._buildup_started_t = 0.0
        self._last_drop_t = -10.0
        self._drop_cooldown_s = 4.0
        self._inst_prev = {k: 0.0 for k in INSTRUMENTS}
        self._inst_last_onset_t = {k: -10.0 for k in INSTRUMENTS}
        self._sens_section = 0.55
        self._sens_instrument = 1.0
        self._sens_drop = 2.6
        self._noise_floor = 0.02
        self._beats_per_bar = 4
        self._beat_count = 0
        self._bar_number = 0
        self._beat_in_bar = 0
        self._last_beat_t = -1.0
        self._beat_intervals = deque(maxlen=32)
        self._bar_rms_accum = []
        self._bar_energy = 0.0
        self._bpm_history = deque(maxlen=int(8.0 * 40))
        self._last_tempo_change_t = -10.0
        self._tempo_change_cooldown = 2.0
        self._tempo_delta = 0.0
        self._enabled = True
        self._attached_to = None
        self._t0 = time.monotonic()

    def attach(self, analyzer):
        self.detach()
        try:
            analyzer.frame_ready.connect(self._on_frame)
            self._attached_to = analyzer
        except Exception:
            logger.debug("music_structure_analyzer_v22: failed to attach analyzer")
            self._attached_to = None

    def detach(self):
        if self._attached_to is None:
            return
        try:
            self._attached_to.frame_ready.disconnect(self._on_frame)
        except Exception:
            logger.debug("music_structure_analyzer_v22: failed to disconnect frame_ready")
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
            self._sens_section = float(max(0.05, min(2.0, section)))
        if instrument is not None:
            self._sens_instrument = float(max(0.1, min(4.0, instrument)))
        if drop is not None:
            self._sens_drop = float(max(1.2, min(8.0, drop)))
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
        self._beats_per_bar = max(1, min(16, n))

    def reset(self):
        self._rms_short.clear()
        self._rms_long.clear()
        self._rms_silence.clear()
        self._centroid_hist.clear()
        self._bands_prev = None
        self._section_label = SectionLabel.UNKNOWN
        self._section_started_t = 0.0
        self._pending_label = SectionLabel.UNKNOWN
        self._pending_since = 0.0
        self._buildup_active = False
        self._last_drop_t = -10.0
        self._inst_prev = {k: 0.0 for k in INSTRUMENTS}
        self._inst_last_onset_t = {k: -10.0 for k in INSTRUMENTS}
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

    def _on_frame(self, frame):
        if not self._enabled:
            return
        t = time.monotonic() - self._t0
        rms = getattr(frame, "rms", 0.0)
        peak = getattr(frame, "peak", 0.0)
        bands = list(getattr(frame, "bands", [0.0] * 8))
        bpm = float(getattr(frame, "bpm", 120.0))
        beat_phase = float(getattr(frame, "beat_phase", 0.0))
        is_beat = bool(getattr(frame, "is_beat", False))

        self._rms_short.append(rms)
        self._rms_long.append(rms)
        self._rms_silence.append(rms)
        centroid = self._compute_centroid(bands)
        self._centroid_hist.append(centroid)
        novelty = self._compute_novelty(bands)
        self._bands_prev = list(bands)

        e_short = float(np.mean(list(self._rms_short))) if self._rms_short else rms
        e_long = float(np.mean(list(self._rms_long))) if self._rms_long else rms
        onset = novelty
        instruments = self._compute_instruments(bands, onset, e_short, t)

        # 小节/节拍追踪
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

        # 速度变化检测
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

        prev_section = self._section_label
        new_label = self._classify_section(rms, e_short, e_long, centroid, instruments, t)
        section_changed = self._update_section_state(new_label, t)
        is_drop = self._detect_drop(rms, e_long, t)
        buildup_change = self._detect_buildup(e_short, centroid, t)

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
            extras={"buildup": 1.0 if self._buildup_active else 0.0, "onset": onset},
        )

        if is_drop:
            ev_drop = self._clone_event(ev, event_type="drop")
            self.structure_event.emit(ev_drop)
            self.drop_detected.emit(rms)
        if section_changed:
            ev_sc = self._clone_event(ev, event_type="section_change", prev_section=prev_section)
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

        for inst in ("kick", "snare"):
            level = instruments.get(inst, 0.0)
            prev_lvl = self._inst_prev.get(inst, 0.0)
            last_t = self._inst_last_onset_t.get(inst, -10.0)
            if level > 0.45 and prev_lvl < 0.30 and (t - last_t) > 0.10:
                self._inst_last_onset_t[inst] = t
                ev_io = self._clone_event(ev, event_type="instrument_onset", onset_instrument=inst)
                self.structure_event.emit(ev_io)
                self.instrument_onset.emit(inst, level)
        self._inst_prev = dict(instruments)
        self.analysis_tick.emit(ev)
        return ev

    def _compute_centroid(self, bands):
        s = sum(bands)
        if s <= 1e-6:
            return 0.0
        n = len(bands)
        if n <= 1:
            return 0.0
        return float(sum(i * b for i, b in enumerate(bands)) / (s * (n - 1)))

    def _compute_novelty(self, bands):
        prev = self._bands_prev
        if prev is None or len(prev) != len(bands):
            return 0.0
        d = sum(abs(a - b) for a, b in zip(bands, prev))
        return float(min(1.0, d / max(1.0, len(bands))))

    def _compute_instruments(self, bands, onset, e_short, t):
        out = {}
        for name, (weights, mode, thr) in _INSTRUMENT_PROFILES.items():
            score = _weighted_band_score(bands, weights)
            trigger = onset * score if mode == "onset" else score
            v = max(0.0, (trigger - thr) * 2.0) * self._sens_instrument
            out[name] = float(min(1.0, v))
        return out

    def _classify_section(self, rms, e_short, e_long, centroid, instruments, t):
        if e_short < self._noise_floor:
            if self._section_label in (SectionLabel.CHORUS, SectionLabel.DROP, SectionLabel.BUILD):
                return SectionLabel.BREAKDOWN
            if self._section_label in (SectionLabel.UNKNOWN, SectionLabel.INTRO):
                return SectionLabel.INTRO
            return SectionLabel.OUTRO
        ratio = e_short / max(self._noise_floor, e_long)
        ch = list(self._centroid_hist)
        rising = 0.0
        if len(ch) >= 8:
            head = float(np.mean(ch[:max(2, len(ch)//3)]))
            tail = float(np.mean(ch[-max(2, len(ch)//3):]))
            rising = tail - head
        section_thr = self._sens_section
        if ratio > (1.0 + section_thr) and centroid > 0.45:
            kick = instruments.get("kick", 0)
            bass = instruments.get("bass", 0)
            if kick > 0.5 and bass > 0.4:
                return SectionLabel.DROP
            return SectionLabel.CHORUS
        if rising > 0.08 and ratio > 0.8:
            return SectionLabel.BUILD
        if ratio > 0.5:
            return SectionLabel.VERSE
        age = t - self._section_started_t
        if age > 30.0:
            return SectionLabel.OUTRO
        if self._section_label == SectionLabel.UNKNOWN:
            return SectionLabel.INTRO
        if self._section_label != SectionLabel.UNKNOWN:
            return self._section_label
        return SectionLabel.INTRO

    def _update_section_state(self, candidate, t):
        if candidate == self._section_label:
            self._pending_label = self._section_label
            self._pending_since = t
            return False
        if candidate != self._pending_label:
            self._pending_label = candidate
            self._pending_since = t
            return False
        if (t - self._pending_since) >= self._pending_hold_s:
            self._section_label = candidate
            self._section_started_t = t
            return True
        return False

    def _detect_drop(self, rms, e_long, t):
        if (t - self._last_drop_t) < self._drop_cooldown_s:
            return False
        recent = list(self._rms_silence)
        if len(recent) < 8:
            return False
        threshold_silence = max(self._noise_floor * 1.5, 0.04)
        had_silence = min(recent[:max(4, len(recent)//3)]) < threshold_silence
        if not had_silence:
            return False
        if e_long < 1e-4:
            return False
        if (rms / e_long) >= self._sens_drop and rms > 0.15:
            self._last_drop_t = t
            return True
        return False

    def _detect_buildup(self, e_short, centroid, t):
        ch = list(self._centroid_hist)
        rs = list(self._rms_long)
        if len(ch) < 16 or len(rs) < 16:
            return ""
        win = max(8, int(3.0 * 40))
        ch_win = ch[-win:]
        rs_win = rs[-win:]
        third = max(2, len(ch_win) // 3)
        ch_rise = float(np.mean(ch_win[-third:]) - np.mean(ch_win[:third]))
        rs_rise = float(np.mean(rs_win[-third:]) - np.mean(rs_win[:third]))
        is_buildup = (ch_rise > 0.05 and rs_rise > 0.04 and e_short > 0.05)
        if is_buildup and not self._buildup_active:
            self._buildup_active = True
            self._buildup_started_t = t
            return "start"
        if (not is_buildup) and self._buildup_active:
            self._buildup_active = False
            return "end"
        return ""

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
            extras=dict(ev.extras),
        )
        for k, v in overrides.items():
            if hasattr(new, k):
                setattr(new, k, v)
        return new

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
            logger.debug("music_structure_analyzer_v22: failed to import audio_driver from src.ui")
            try:
                from ui.audio_driver import get_audio_driver
            except Exception:
                logger.debug("music_structure_analyzer_v22: failed to import audio_driver from ui")
                get_audio_driver = None
        if get_audio_driver is not None:
            try:
                an = get_audio_driver()
                _global_msa.attach(an)
            except Exception:
                logger.debug("music_structure_analyzer_v22: failed to attach global MSA to audio driver")
                pass
    return _global_msa
