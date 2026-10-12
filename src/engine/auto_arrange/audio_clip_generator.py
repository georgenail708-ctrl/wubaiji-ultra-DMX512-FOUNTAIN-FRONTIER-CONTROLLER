"""
audio_clip_generator — 音频分析结果 → 时间线 Clip 生成器

7种生成模式，全部基于 DeepAnalysis（已离线算好）：
  1. segment_markers  — 段落标记轨（彩色标签，dimmer=0）
  2. beat_bursts      — 鼓点→气爆sequential追逐 clip
  3. energy_dimmer    — 能量包络→dimmer clip
  4. melody_motor     — 旋律音高→电机轨迹 clip
  5. harmony_rgb      — 和声色彩→RGB颜色过渡 clip
  6. onset_pulse      — onset → 泵短脉冲 clip
  7. bpm_wave         — BPM对齐水帘 clip
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


def _load_models():
    try:
        from core.models import Clip, DMXFrame
    except ImportError:
        try:
            from src.core.models import Clip, DMXFrame
        except ImportError:
            from ...core.models import Clip, DMXFrame
    return Clip, DMXFrame


def _frame_idx(timeline, t: float) -> int:
    hop = int(getattr(timeline, "hop", 512))
    sr  = int(getattr(timeline, "sr",  44100))
    n   = int(getattr(timeline, "n_frames", 0))
    idx = int(round(t * sr / hop))
    return max(0, min(n - 1, idx))


def _t_at(timeline, idx: int) -> float:
    times = getattr(timeline, "times", None)
    if times is not None and idx < len(times):
        return float(times[idx])
    hop = int(getattr(timeline, "hop", 512))
    sr  = int(getattr(timeline, "sr", 44100))
    return idx * hop / sr


def _smooth(arr: np.ndarray, win: int) -> np.ndarray:
    if win <= 1 or len(arr) == 0:
        return arr
    kernel = np.ones(win, dtype=np.float32) / win
    return np.convolve(arr.astype(np.float32), kernel, mode="same")


def _peak_indices(arr: np.ndarray, threshold: float, min_dist: int) -> List[int]:
    """候选判定向量化（R183 3.3 计算层批）：旧实现逐样本 Python 循环 O(n)，
    现把「平台极大 + 阈值」谓词交给 numpy 一次算完，再只对候选峰做贪心
    min_dist 过滤。语义逐位一致（谓词同一比较、贪心同序）：
    - 平台（相等值）每个点仍是候选，min_dist 过滤后保留**首个**；
    - min_dist 为先到先得贪心，不是 scipy find_peaks 的「小峰先删」优先级——
      故不用 scipy.signal.find_peaks（其平台取中点 + 距离优先级会改峰位，
      金标对拍不可能一致，调研结论见 ai-memory/_references/compute-adoption.md）。
    """
    a = np.asarray(arr)
    n = a.shape[0]
    if n < 3:
        return []
    mid = a[1:-1]
    cand = np.flatnonzero((mid >= a[:-2]) & (mid >= a[2:]) & (mid >= threshold))
    cand = (cand + 1).tolist()
    peaks: List[int] = []
    last = -min_dist
    for i in cand:
        if i - last >= min_dist:
            peaks.append(i)
            last = i
    return peaks


def _chroma_to_rgb(chroma_vec: np.ndarray) -> Tuple[int, int, int]:
    hues = [i * 30.0 for i in range(12)]
    total = float(np.sum(chroma_vec)) or 1.0
    weights = chroma_vec / total
    sin_sum = sum(math.sin(math.radians(h)) * w for h, w in zip(hues, weights))
    cos_sum = sum(math.cos(math.radians(h)) * w for h, w in zip(hues, weights))
    hue = math.degrees(math.atan2(sin_sum, cos_sum)) % 360.0
    saturation = min(1.0, float(np.max(chroma_vec)) / (float(np.mean(chroma_vec)) + 1e-6) * 0.4)
    h60 = hue / 60.0
    hi = int(h60) % 6
    f  = h60 - int(h60)
    p = 1.0 - saturation
    q = 1.0 - saturation * f
    t = 1.0 - saturation * (1.0 - f)
    rgb_map = [(1,t,p),(q,1,p),(p,1,t),(p,q,1),(t,p,1),(1,p,q)]
    r, g, b = rgb_map[hi]
    return int(r * 255), int(g * 255), int(b * 255)


def _seg_bounds(seg, default_dur: float = 4.0) -> Tuple[float, float]:
    """读取段落 (起, 止) 秒。CharacterSegment 用 t_start/t_end；
    兼容旧式 .start/.end 兜底类。"""
    t0 = getattr(seg, "t_start", None)
    if t0 is None:
        t0 = getattr(seg, "start", 0.0)
    t1 = getattr(seg, "t_end", None)
    if t1 is None:
        t1 = getattr(seg, "end", None)
    try:
        t0 = float(t0 or 0.0)
    except Exception:
        t0 = 0.0
    try:
        t1 = float(t1) if t1 is not None else t0 + default_dur
    except Exception:
        t1 = t0 + default_dur
    if t1 <= t0:
        t1 = t0 + default_dur
    return t0, t1


def _make_clip(start: float, duration: float, label: str = "",
               fade_in: float = 0.0, fade_out: float = 0.0,
               groups: int = 1, seq_mode: str = "parallel",
               motor_transition: bool = False) -> Any:
    Clip, _ = _load_models()
    c = Clip(start_time=float(start), duration=float(max(0.05, duration)))
    c.label = str(label)
    c.fade_in  = float(fade_in)
    c.fade_out = float(fade_out)
    if groups > 1:
        c.fade_groups = int(groups)
        c.fade_group_sequence_mode = str(seq_mode)
    if motor_transition:
        c.motor_transition_phase_enabled = True
    return c


def _fixture_universe_channel(project, fid: str, ch_type: str) -> Optional[Tuple[int, int]]:
    """返回某设备某通道类型对应的 (universe, 0-based DMX 通道地址)。

    与 voice_clip_renderer.build_patch 同口径:fixture_defs 里的 def_id 映射到
    channels(List[str]) 的下标, start_channel 为 1-based,
    地址 = start_channel + 通道下标 - 1(0-based)。
    """
    if project is None:
        return None
    defs: Dict[str, List[str]] = {}
    for fd in getattr(project, "fixture_defs", []) or []:
        did = getattr(fd, "id", None) or (fd.get("id") if isinstance(fd, dict) else None)
        chs = getattr(fd, "channels", None)
        if chs is None and isinstance(fd, dict):
            chs = fd.get("channels", [])
        types = [str(c.get("type") if isinstance(c, dict) else c) for c in (chs or [])]
        if did is not None:
            defs[did] = types
    for fx in getattr(project, "fixtures", []) or []:
        fxfid = getattr(fx, "id", None) or (fx.get("id") if isinstance(fx, dict) else None)
        if fxfid is None or str(fxfid) != str(fid):
            continue
        did = (getattr(fx, "def_id", None) or getattr(fx, "fixture_def_id", None)
               or (fx.get("def_id") if isinstance(fx, dict) else None))
        types = defs.get(did) if did is not None else None
        if not types:
            continue
        try:
            idx = types.index(str(ch_type))
        except ValueError:
            continue
        univ = int(getattr(fx, "universe", None)
                   or (fx.get("universe", 1) if isinstance(fx, dict) else 1) or 1)
        base = int(getattr(fx, "start_channel", None)
                   or (fx.get("start_channel", 1) if isinstance(fx, dict) else 1) or 1)
        return univ, base + idx - 1
    return None


def _group_fixture_ids(project, group_id: str) -> List[str]:
    if not group_id or project is None:
        return []
    for grp in getattr(project, "fixture_groups", []) or []:
        try:
            if str(grp.get("id", "")) == group_id:
                return [str(x) for x in (grp.get("fixture_ids") or [])]
        except Exception:
            pass
    return []


# ── 7种模式 ──────────────────────────────────────────────────────────────────

def generate_segment_markers(deep_analysis, project, config: dict) -> List[Any]:
    segments = list(getattr(deep_analysis, "segments", []) or [])
    if not segments:
        return []
    COLOR_MAP = {
        "intro": "#2255AA", "verse": "#228855", "chorus": "#AA2233",
        "bridge": "#AA8833", "outro": "#556677", "break": "#7733AA",
        "buildup": "#DD6611",
    }
    clips = []
    for seg in segments:
        t_start, t_end = _seg_bounds(seg)
        label   = str(getattr(seg, "label", "") or getattr(seg, "character", "") or "")
        c = _make_clip(t_start, t_end - t_start, label=label)
        c._marker_color = COLOR_MAP.get(label.lower(), "#445566")
        clips.append(c)
    return clips


def generate_beat_bursts(deep_analysis, project, config: dict) -> List[Any]:
    tl = getattr(deep_analysis, "timeline", None)
    if tl is None:
        return []

    group_id     = str(config.get("group_id", "") or "")
    clip_dur     = float(config.get("clip_duration", 1.5))
    beat_divisor = int(config.get("beat_divisor", 1))
    seq_batches  = int(config.get("seq_batches", 4))
    fade_out     = float(config.get("fade_out", 0.5))

    events = list(getattr(deep_analysis, "events", []) or [])
    beat_times = [float(ev["t"]) for ev in events
                  if isinstance(ev, dict) and ev.get("type") == "beat"]
    beat_times = beat_times[::max(1, beat_divisor)]

    dur = float(getattr(tl, "duration", 0.0))
    clips = []
    for bt in beat_times:
        if bt + clip_dur > dur + 1.0:
            break
        c = _make_clip(bt, clip_dur, label="气爆", fade_in=0.0, fade_out=fade_out,
                       groups=seq_batches, seq_mode="sequential")
        clips.append(c)
    return clips


def generate_energy_dimmer(deep_analysis, project, config: dict) -> List[Any]:
    tl = getattr(deep_analysis, "timeline", None)
    if tl is None:
        return []
    Clip, DMXFrame = _load_models()

    group_id  = str(config.get("group_id", "") or "")
    smooth_s  = float(config.get("smooth_seconds", 0.5))
    min_level = float(config.get("min_level", 0.05))
    fps       = int(config.get("fps", 20))
    ch_type   = str(config.get("channel_type", "dimmer"))

    hop = int(getattr(tl, "hop", 512))
    sr  = int(getattr(tl, "sr", 44100))
    rms = np.asarray(getattr(tl, "rms", []), dtype=np.float32)
    if len(rms) == 0:
        return []

    win = max(1, int(smooth_s * sr / hop))
    rms_smooth = _smooth(rms, win)
    rms_norm = rms_smooth / (float(np.max(rms_smooth)) + 1e-9)

    fids = _group_fixture_ids(project, group_id)
    segments = list(getattr(deep_analysis, "segments", []) or [])
    if not segments:
        dur_total = float(getattr(tl, "duration", 60.0))
        class _S:
            start = 0.0; end = dur_total; label = "all"
        segments = [_S()]

    clips = []
    for seg in segments:
        t0, t1 = _seg_bounds(seg)
        cdur = max(0.1, t1 - t0)
        c = _make_clip(t0, cdur, label="能量调光",
                       fade_in=min(0.3, cdur * 0.1), fade_out=min(0.3, cdur * 0.1))
        dt = 1.0 / fps
        t = t0
        while t <= t1:
            fi = _frame_idx(tl, t)
            level = max(min_level, float(rms_norm[fi]) if fi < len(rms_norm) else 0.0)
            val = int(level * 255)
            rel_t = t - t0
            for fid in fids:
                uc = _fixture_universe_channel(project, fid, ch_type)
                if uc is None:
                    continue
                univ, ch = uc
                data = np.zeros(512, dtype=np.uint8)
                data[ch] = val
                c.frames.append(DMXFrame(universe=univ, timestamp=rel_t, data=data.copy()))
                if univ not in c.active_channels_by_universe:
                    c.active_channels_by_universe[univ] = set()
                c.active_channels_by_universe[univ].add(ch + 1)
            t += dt
        clips.append(c)
    return clips


def generate_melody_motor(deep_analysis, project, config: dict) -> List[Any]:
    tl = getattr(deep_analysis, "timeline", None)
    if tl is None:
        return []
    Clip, DMXFrame = _load_models()

    group_id      = str(config.get("group_id", "") or "")
    voiced_thresh = float(config.get("voiced_threshold", 0.55))
    midi_lo       = float(config.get("midi_lo", 48.0))
    midi_hi       = float(config.get("midi_hi", 84.0))
    axis          = str(config.get("axis", "x"))
    fps           = int(config.get("fps", 20))
    fade_in       = float(config.get("fade_in", 0.5))

    f0_midi = np.asarray(getattr(tl, "f0_midi", []),    dtype=np.float32)
    voiced  = np.asarray(getattr(tl, "voiced_prob", []),dtype=np.float32)
    if len(f0_midi) == 0:
        return []

    dur = float(getattr(tl, "duration", 60.0))
    fids = _group_fixture_ids(project, group_id)

    voiced_regions = []
    in_region = False; reg_start = 0
    for i, vp in enumerate(voiced):
        if float(vp) >= voiced_thresh and not in_region:
            in_region = True; reg_start = i
        elif float(vp) < voiced_thresh and in_region:
            in_region = False
            t0 = _t_at(tl, reg_start); t1 = _t_at(tl, i)
            if t1 - t0 >= 0.5:
                voiced_regions.append((reg_start, i, t0, t1))
    if in_region:
        t0 = _t_at(tl, reg_start)
        if dur - t0 >= 0.5:
            voiced_regions.append((reg_start, len(f0_midi)-1, t0, dur))

    clips = []
    for (i0, i1, t0, t1) in voiced_regions:
        cdur = max(0.1, t1 - t0)
        c = _make_clip(t0, cdur, label="旋律电机",
                       fade_in=fade_in, fade_out=0.3, motor_transition=True)
        dt = 1.0 / fps
        t = t0
        while t <= t1:
            fi = _frame_idx(tl, t)
            midi = float(f0_midi[fi]) if fi < len(f0_midi) else 60.0
            norm = max(0.0, min(1.0, (midi - midi_lo) / max(1.0, midi_hi - midi_lo)))
            val  = int(norm * 255)
            rel_t = t - t0
            for fid in fids:
                uc = _fixture_universe_channel(project, fid, f"motor_{axis}")
                if uc is None:
                    continue
                univ, ch = uc
                data = np.zeros(512, dtype=np.uint8)
                data[ch] = val
                c.frames.append(DMXFrame(universe=univ, timestamp=rel_t, data=data.copy()))
                if univ not in c.active_channels_by_universe:
                    c.active_channels_by_universe[univ] = set()
                c.active_channels_by_universe[univ].add(ch + 1)
            t += dt
        clips.append(c)
    return clips


def generate_harmony_rgb(deep_analysis, project, config: dict) -> List[Any]:
    tl = getattr(deep_analysis, "timeline", None)
    if tl is None:
        return []
    Clip, DMXFrame = _load_models()

    group_id = str(config.get("group_id", "") or "")
    fps      = int(config.get("fps", 20))

    chroma = np.asarray(getattr(tl, "chroma", []), dtype=np.float32)
    if chroma.ndim != 2 or chroma.shape[0] != 12:
        return []

    segments = list(getattr(deep_analysis, "segments", []) or [])
    if not segments:
        dur_total = float(getattr(tl, "duration", 60.0))
        class _S:
            start = 0.0; end = dur_total; label = "all"
        segments = [_S()]

    fids = _group_fixture_ids(project, group_id)
    clips = []
    for seg in segments:
        t0, t1 = _seg_bounds(seg)
        cdur = max(0.1, t1 - t0)
        c = _make_clip(t0, cdur, label="和声色彩",
                       fade_in=min(0.5, cdur * 0.15), fade_out=min(0.5, cdur * 0.15))
        dt = 1.0 / fps
        t = t0
        while t <= t1:
            fi  = _frame_idx(tl, t)
            cvec = chroma[:, min(fi, chroma.shape[1]-1)] if fi < chroma.shape[1] else np.zeros(12)
            r, g, b = _chroma_to_rgb(cvec)
            rel_t = t - t0
            for fid in fids:
                data = np.zeros(512, dtype=np.uint8)
                for ch_type, val in (("R", r), ("G", g), ("B", b)):
                    uc = _fixture_universe_channel(project, fid, ch_type)
                    if uc is None:
                        continue
                    univ, ch = uc
                    data[ch] = val
                    if univ not in c.active_channels_by_universe:
                        c.active_channels_by_universe[univ] = set()
                    c.active_channels_by_universe[univ].add(ch + 1)
                uc0 = _fixture_universe_channel(project, fid, "R")
                if uc0:
                    c.frames.append(DMXFrame(universe=uc0[0], timestamp=rel_t, data=data.copy()))
            t += dt
        clips.append(c)
    return clips


def generate_onset_pulse(deep_analysis, project, config: dict) -> List[Any]:
    tl = getattr(deep_analysis, "timeline", None)
    if tl is None:
        return []

    band        = int(config.get("band", 3))
    sensitivity = float(config.get("sensitivity", 0.6))
    pulse_dur   = float(config.get("pulse_duration", 0.3))
    fade_out    = float(config.get("fade_out", 0.2))
    min_gap_s   = float(config.get("min_gap_seconds", 0.15))

    onset_bands = np.asarray(getattr(tl, "onset_bands", []), dtype=np.float32)
    if onset_bands.ndim != 2 or onset_bands.shape[0] < band + 1:
        return []

    hop = int(getattr(tl, "hop", 512))
    sr  = int(getattr(tl, "sr", 44100))
    arr = onset_bands[band]
    arr_norm = arr / (float(np.max(arr)) + 1e-9)
    min_dist = max(1, int(min_gap_s * sr / hop))
    peak_idxs = _peak_indices(arr_norm, sensitivity, min_dist)

    clips = []
    for pi in peak_idxs:
        t_start = _t_at(tl, pi)
        strength = float(arr_norm[pi])
        c = _make_clip(t_start, pulse_dur, label="气爆脉冲",
                       fade_in=0.02, fade_out=fade_out)
        c._level_hint = strength
        clips.append(c)
    return clips


def generate_bpm_wave(deep_analysis, project, config: dict) -> List[Any]:
    tl = getattr(deep_analysis, "timeline", None)
    if tl is None:
        return []

    beats_per_bar = int(config.get("beats_per_bar", 4))
    n_bars        = int(config.get("bars_per_clip", 1))
    fade_in       = float(config.get("fade_in", 0.1))
    seq_groups    = int(config.get("seq_groups", 1))

    tempo = float(getattr(deep_analysis, "tempo", 0.0) or 120.0)
    beat_dur = 60.0 / max(30.0, tempo)
    bar_dur  = beat_dur * beats_per_bar
    clip_dur = bar_dur * n_bars
    total    = float(getattr(tl, "duration", 60.0))

    events = list(getattr(deep_analysis, "events", []) or [])
    beat_times_all = [float(ev["t"]) for ev in events
                      if isinstance(ev, dict) and ev.get("type") == "beat"]
    downbeats = beat_times_all[::beats_per_bar] if beat_times_all else \
                [i * bar_dur for i in range(int(total / bar_dur) + 1)]

    clips = []
    for db in downbeats:
        if db + clip_dur > total + 1.0:
            break
        c = _make_clip(db, clip_dur, label="水帘",
                       fade_in=fade_in, fade_out=fade_in,
                       groups=seq_groups if seq_groups > 1 else 1,
                       seq_mode="sequential")
        clips.append(c)
    return clips


# ── 统一入口 ──────────────────────────────────────────────────────────────────

_MODE_FN = {
    "segment_markers": generate_segment_markers,
    "beat_bursts":     generate_beat_bursts,
    "energy_dimmer":   generate_energy_dimmer,
    "melody_motor":    generate_melody_motor,
    "harmony_rgb":     generate_harmony_rgb,
    "onset_pulse":     generate_onset_pulse,
    "bpm_wave":        generate_bpm_wave,
}


def generate(mode: str, deep_analysis, project, config: dict) -> List[Any]:
    fn = _MODE_FN.get(str(mode))
    if fn is None:
        raise ValueError(f"未知生成模式: {mode!r}")
    return fn(deep_analysis, project, config) or []
