# -*- coding: utf-8 -*-
"""AM23-F read-only audio controls; reuse original analysis/cache, never capture.

Source changes are explicit owner events. File preparation belongs to source/Apply
or bake setup, not render. Live input is one immutable scalar snapshot published
by the existing analyzer binding; offline live input is deliberately unavailable.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
import math
import time
from types import MappingProxyType

from src.audio.audio_feature_cache import file_stamp, get_features, peek_features, feature_status

CONTROL_KEYS = ("energy", "beat_strength", "band_low", "band_mid", "band_high", "onset_flux")
_SOURCE = ("file", "", 0)
_LIVE = MappingProxyType({})
_LIVE_MAX_AGE = 0.5


def _number(value, default=0.0):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _unit(value):
    return min(1.0, max(0.0, _number(value)))


def set_source(mode: str, audio_path: str = "") -> int:
    """Pure source/epoch setter; never starts an analyzer or loads a file."""
    global _SOURCE, _LIVE
    mode = mode if mode in ("file", "microphone", "simulate") else "off"
    _SOURCE = (mode, str(audio_path or ""), _SOURCE[2] + 1)
    _LIVE = MappingProxyType({})
    return _SOURCE[2]


def source_epoch() -> int:
    return _SOURCE[2]


def publish_live_snapshot(values, *, epoch: int) -> bool:
    """Publish primitive scalars only; late frames from retired bindings are rejected."""
    global _LIVE
    mode, _, current = _SOURCE
    if epoch != current or mode not in ("microphone", "simulate"):
        return False
    result = {key: _unit(values.get(key, 0.0)) for key in CONTROL_KEYS}
    result.update(epoch=current, source=mode, time=_number(values.get("time")),
                  bpm=max(1.0, _number(values.get("bpm"), 120.0)),
                  beat_phase=_unit(values.get("beat_phase")),
                  has_audio=bool(values.get("has_audio", False)),
                  reason="ready" if values.get("has_audio", False) else "silence",
                  ready=True, received_at=time.monotonic())
    _LIVE = MappingProxyType(result)
    return True


def retire_source(epoch: int) -> None:
    if epoch == source_epoch():
        set_source("off")


def prepare_feature_file(path: str, *, background: bool = True, use_neural: bool = False):
    """Explicit source/Apply/bake preparation; uses the existing deduplicated worker."""
    return get_features(path, use_neural=use_neural, background_on_miss=background) if path else None


def _empty(pos: float, mode: str, epoch) -> dict:
    result = dict.fromkeys(CONTROL_KEYS, 0.0)
    result.update(ready=False, has_audio=False, source=mode, epoch=epoch, reason="live_waiting",
                  time=pos, bpm=120.0, beat_phase=0.0)
    return result


def _interpolate(times, values, pos, *, column=None):
    if not times or len(values) != len(times):
        return 0.0
    index = min(bisect_left(times, pos), len(times) - 1)
    previous = max(0, index - 1)
    a, b = values[previous], values[index]
    if column is not None:
        a, b = a[column], b[column]
    width = times[index] - times[previous]
    amount = min(1.0, max(0.0, (pos - times[previous]) / width)) if width > 0.0 else 0.0
    return _number(a) + amount * (_number(b) - _number(a))


def _beat_controls(features, pos: float):
    beats = features.beat_times
    if not beats:
        return 0.0, (pos * features.bpm / 60.0) % 1.0
    index = max(0, bisect_right(beats, pos) - 1)
    period = (beats[index + 1] - beats[index]) if index + 1 < len(beats) else 60.0 / max(1.0, features.bpm)
    phase = ((pos - beats[index]) / max(1e-9, period)) % 1.0
    strength = features.beat_strength[index] if index < len(features.beat_strength) else 0.0
    return _unit(strength), phase


def _file_view(path: str, pos: float, result: dict) -> dict:
    result["epoch"] = (result["epoch"], path, file_stamp(path))
    features = peek_features(path)
    if features is None or not features.curve_times:
        result["reason"] = feature_status(path) if features is None else "features_unavailable"
        return result
    result["ready"] = True
    result["reason"] = "outside_audio"
    if pos < 0.0 or pos >= features.duration:
        return result
    times = features.curve_times
    result["energy"] = _unit(_interpolate(times, features.energy_curve, pos))
    result["onset_flux"] = _unit(_interpolate(times, features.onset_curve, pos))
    result["has_audio"] = _interpolate(times, features.rms_curve, pos) > 1e-5
    result["reason"] = "ready" if result["has_audio"] else "silence"
    for column, key in enumerate(("band_low", "band_mid", "band_high")):
        result[key] = _unit(_interpolate(times, features.band_curve, pos, column=column))
    result["bpm"] = max(1.0, _number(features.bpm, 120.0))
    result["beat_strength"], result["beat_phase"] = _beat_controls(features, pos)
    return result


def feature_view(project, pos: float, state=None, *, offline: bool = False):
    """One frame context reused by all clips; no Qt, FFT, file hash or worker spawn.

    ``offline=True`` rejects microphone/simulation snapshots. File controls use
    deterministic cached curves at timeline seconds; missing curves remain dry.
    The state argument matches the processor seam, not a second source selector.
    """
    mode, path, epoch = _SOURCE
    pos = _number(pos)
    result = _empty(pos, mode, epoch)
    if mode == "file":
        path = path or str(getattr(project, "audio_path", "") or "")
        return MappingProxyType(_file_view(path, pos, result))
    live = _LIVE
    result["reason"] = "offline_live" if offline else "live_waiting"
    if offline or live.get("epoch") != epoch:
        return MappingProxyType(result)
    if time.monotonic() - live.get("received_at", 0.0) > _LIVE_MAX_AGE:
        result["reason"] = "live_expired"
        return MappingProxyType(result)
    result.update({key: live[key] for key in result})
    return MappingProxyType(result)
