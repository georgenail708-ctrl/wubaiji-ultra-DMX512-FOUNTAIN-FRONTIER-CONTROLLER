"""Bounded onset adapter for the existing unique AudioAnalyzer.

Reuse its original FFT; SciPy 1.17.1 maximum_filter1d/find_peaks confirm spectral
changes. Sensitivity is a normalized positive spectral-change threshold (0..1),
not input loudness or a Matrix output threshold. No capture, FFT, worker, cache
preparation or pitch analysis is created here. Peak confirmation waits one real
analysis sample, normally 25ms. The original BPMDetector API estimates tempo.

References: librosa 0.11.0 onset.py (ISC); SciPy v1.17.1 peak finding (BSD-3).
"""
from __future__ import annotations

from collections import deque
import math
from threading import RLock

import numpy as np


class OnsetRuntime:
    """One private state per Analyzer; at most one spectrum and 32 samples."""

    def __init__(self):
        self.guard = RLock()
        self.generation = 0
        self.source = None
        self.spectrum = None
        self.samples = deque(maxlen=32)
        self.last_timestamp = None
        self.last_peak_time = -math.inf


def _source_identity(analyzer, rate):
    mode = analyzer._mode
    if mode == "file":
        source = (analyzer._file_path, id(analyzer._file_data), analyzer._load_token)
    else:
        source = (getattr(analyzer, "_mic_device", None), id(analyzer._mic_stream))
    return mode, source, analyzer._gain, rate, analyzer._chunk


def onset_source_snapshot(analyzer, rate):
    """Capture before the original FFT; reject a calculation retired by a setter."""
    runtime = analyzer._onset_runtime
    with runtime.guard:
        return runtime.generation, _source_identity(analyzer, rate)


def _clear_locked(analyzer, position):
    runtime, detector = analyzer._onset_runtime, analyzer._bpm_detector
    runtime.generation += 1
    runtime.source = None
    runtime.spectrum = None
    runtime.samples.clear()
    runtime.last_timestamp = None
    runtime.last_peak_time = -math.inf
    detector._beat_times.clear()
    detector._last_onset = -math.inf
    detector._last_beat_t = position
    detector._bpm = 120.0
    detector._phase = 0.0


def set_onset_position(analyzer, position):
    runtime = analyzer._onset_runtime
    with runtime.guard:
        if position < analyzer._timeline_pos - 1e-4:
            _clear_locked(analyzer, position)
        analyzer._timeline_pos = position


def set_onset_mode(analyzer, mode):
    runtime = analyzer._onset_runtime
    with runtime.guard:
        if mode != analyzer._mode:
            _clear_locked(analyzer, analyzer._timeline_pos)
        analyzer._mode = mode


def set_onset_gain(analyzer, gain):
    gain = max(0.1, min(5.0, gain))
    runtime = analyzer._onset_runtime
    with runtime.guard:
        if gain != analyzer._gain:
            _clear_locked(analyzer, analyzer._timeline_pos)
        analyzer._gain = gain


def _adopt_source(analyzer, identity, timestamp):
    runtime = analyzer._onset_runtime
    if identity != runtime.source:
        _clear_locked(analyzer, timestamp)
        runtime.source = identity
    if runtime.last_timestamp is not None and timestamp < runtime.last_timestamp - 1e-4:
        _clear_locked(analyzer, timestamp)
        runtime.source = identity


def _spectral_strength(runtime, spectrum, rms, maximum_filter):
    if rms <= 1e-5:
        runtime.spectrum = np.zeros_like(spectrum)
        return 0.0
    previous = runtime.spectrum
    runtime.spectrum = spectrum
    if previous is None:
        return 0.0
    reference = maximum_filter(previous, size=3)
    positive = float(np.maximum(spectrum - reference, 0.0).sum())
    return min(1.0, max(0.0, positive / max(float(spectrum.sum()), np.finfo(float).tiny)))


def _confirmed_peak(runtime, threshold, find_peaks):
    if len(runtime.samples) < 3:
        return None
    samples = tuple(runtime.samples)
    strengths = np.fromiter((value for _timestamp, value in samples), dtype=float)
    peaks, _properties = find_peaks(strengths, height=threshold,
                                    prominence=threshold * 0.5, distance=1)
    if not len(peaks):
        return None
    timestamp, strength = samples[int(peaks[-1])]
    if timestamp <= runtime.last_peak_time:
        return None
    runtime.last_peak_time = timestamp
    return timestamp, strength


def _evaluate_locked(analyzer, spectrum, rms, timestamp, kernels):
    runtime = analyzer._onset_runtime
    if timestamp == runtime.last_timestamp:
        return 0.0, False
    strength = _spectral_strength(runtime, spectrum, rms, kernels[0])
    runtime.samples.append((timestamp, strength))
    runtime.last_timestamp = timestamp
    peak = _confirmed_peak(runtime, analyzer._sensitivity, kernels[1])
    is_beat = False
    if peak is not None and rms > 1e-5:
        is_beat = analyzer._bpm_detector.update(peak[0], peak[1], analyzer._sensitivity)
        if is_beat:
            strength = peak[1]
    analyzer._bpm_detector.update(timestamp, 0.0, analyzer._sensitivity)
    return strength, is_beat


def analyze_onset(analyzer, spectrum, rms, timestamp, rate, *, sample_source):
    """Consume existing FFT under a short guard; never start an audio service."""
    # Lazy cold import belongs to the original analysis caller, outside the guard.
    from scipy.ndimage import maximum_filter1d
    from scipy.signal import find_peaks

    runtime = analyzer._onset_runtime
    with runtime.guard:
        identity = _source_identity(analyzer, rate)
        if sample_source != (runtime.generation, identity):
            return 0.0, False
        _adopt_source(analyzer, identity, timestamp)
        return _evaluate_locked(analyzer, spectrum, rms, timestamp,
                                (maximum_filter1d, find_peaks))
