"""Short-lived audio effect history, owned by a playback consumer, never a showfile.

Only the three repaired current kernels use this state. A single-frame query
gets an isolated snapshot; browser playback and offline sequences provide an
explicit owner. Sampling happens once before rendering any fixture/channel.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from .effect_music_history import AUDIO_MUSIC_IDS, MusicHistoryState


STATEFUL_IDS = frozenset({
    "atx_melody_contour_track", "atx_transient_density_heat",
    "atx_pitch_stability_hold", "atx_chord_change_accent", "atx_energy_slope_detector",
}) | AUDIO_MUSIC_IDS
STATEFUL_IMPLS = frozenset({
    "_f_melody_contour_track", "_f_transient_density_heat",
    "_f_pitch_stability_hold",
})
GROUP_IMPLS = frozenset({"_f_chord_change_accent", "_f_energy_slope_detector"})
GROUP_IDS = frozenset({"atx_chord_change_accent", "atx_energy_slope_detector"})


@dataclass
class AudioEffectState:
    effect_time: float | None = None
    audio_time: float | None = None
    melody: list[float] = field(default_factory=list)
    previous_pitch: float | None = None
    pitch_delta: float = 0.0
    onset_heat: float = 0.0
    last_onset: float = -1.0
    group_history: dict = field(default_factory=dict)

    def reset(self):
        self.effect_time = self.audio_time = None
        self.melody.clear()
        self.previous_pitch = None
        self.pitch_delta = self.onset_heat = 0.0
        self.last_onset = -1.0
        self.group_history.clear()

    def begin_frame(self, effect_time):
        if self.effect_time is not None and effect_time < self.effect_time:
            self.reset()
        self.effect_time = effect_time

    def advance(self, audio, audio_time):
        if not audio.ok:
            self.reset()
            return
        if self.audio_time is not None and audio_time < self.audio_time:
            self.reset()
        if self.audio_time == audio_time:
            return
        dt = max(0.0, audio_time - self.audio_time) if self.audio_time is not None else 0.0
        self.audio_time = audio_time
        self._record_pitch(audio)
        self.onset_heat = max(0.0, self.onset_heat - dt * 0.48)
        if audio.onset > 0.35 and audio_time - self.last_onset > 0.05:
            self.last_onset = audio_time
            self.onset_heat = min(1.0, self.onset_heat + 0.25)

    def _record_pitch(self, audio):
        valid = audio.pitch >= 0 and audio.pitch_conf > 0
        pitch = audio.pitch if valid else None
        self.pitch_delta = (abs(pitch - self.previous_pitch)
                            if pitch is not None and self.previous_pitch is not None else 0.0)
        self.previous_pitch = pitch
        self.melody.append(audio.pitch_norm() if valid else 0.0)
        del self.melody[:-16]


class AudioEffectRuntime:
    """Identity cache: each running instance owns a bounded, independent state."""
    def __init__(self, limit=128):
        self.limit = limit
        self.entries = OrderedDict()

    def acquire(self, owner, preset_id, source):
        key = (id(owner), preset_id)
        previous = self.entries.pop(key, None)
        state = (previous[2] if previous is not None and previous[0] is owner
                 and previous[1] == source else _new_state(preset_id))
        self.entries[key] = (owner, source, state)
        while len(self.entries) > self.limit:
            self.entries.popitem(last=False)
        return state

    def release(self, owner=None):
        if owner is None:
            self.entries.clear()
            return
        for key, entry in list(self.entries.items()):
            if entry[0] is owner:
                del self.entries[key]


def source_identity(panel):
    analyzer = getattr(panel, "_audio_analyzer", None)
    return (id(analyzer), getattr(analyzer, "_mode", None),
            id(getattr(analyzer, "_file_data", None)),
            getattr(analyzer, "_file_path", None), getattr(analyzer, "_mic_device", None),
            getattr(analyzer, "_simulation_enabled", None))


def bind_audio_runtime(panel, preset_id, params, t, owner=None):
    if preset_id not in STATEFUL_IDS:
        return params
    runtime = getattr(panel, "_effect_audio_runtime", None)
    if runtime is None:
        runtime = AudioEffectRuntime()
        panel._effect_audio_runtime = runtime
    state = runtime.acquire(panel if owner is None else owner, preset_id,
                            source_identity(panel))
    state.begin_frame(float(t))
    return dict(params, _audio_runtime=state)


def release_audio_runtime(panel, owner=None):
    runtime = getattr(panel, "_effect_audio_runtime", None)
    if runtime is not None:
        runtime.release(owner)


def release_stopped_audio(panel, owner):
    if owner is None:
        return
    release_audio_runtime(panel, owner)
    stopped = set(owner.get("ch_types", [])) if isinstance(owner, dict) else set()
    for form in list(getattr(panel, "_channel_forms", None) or []):
        if panel._channel_form_type(form) in stopped:
            release_audio_runtime(panel, form)


def pop_stopped_audio(panel, key):
    owner = panel._external_states.pop(key, None)
    release_stopped_audio(panel, owner)
    return owner


def offline_audio_params(preset_id, params):
    """A sequence gets fresh local state, even if input came from live playback."""
    return dict(params, _audio_runtime=_new_state(preset_id)) if preset_id in STATEFUL_IDS else params


def _new_state(preset_id):
    return MusicHistoryState() if preset_id in AUDIO_MUSIC_IDS else AudioEffectState()


def frame_audio_params(impl, audio, t, params):
    if getattr(impl, "__name__", "") in GROUP_IMPLS:
        return _group_frame_params(impl.__name__, audio, t, params)
    if getattr(impl, "__name__", "") not in STATEFUL_IMPLS:
        return params
    state = params.get("_audio_runtime") or AudioEffectState()
    timestamp = float((params.get("_audio_data") or {}).get("t", t))
    state.advance(audio, timestamp)
    return dict(params, _audio_runtime=state)


def _history_group(params):
    group = params.get("_effect_group_key")
    if group is not None:
        return group
    if params.get("_typed_width"):
        return ("typed-global", str(params.get("ch_type", "")))


def _group_frame_params(name, audio, t, params):
    state, group = params.get("_audio_runtime"), _history_group(params)
    if state is None or group is None:
        return params
    key = (group, str(params.get("_subset_state_tag", "")))
    if not audio.ok:
        state.group_history.pop(key, None)
        return params
    timestamp = float((params.get("_audio_data") or {}).get("t", t))
    history = _group_before(state.group_history, key, timestamp, (name, audio))
    return dict(params, **{field: dict(value) if isinstance(value, dict) else list(value)
                           for field, value in history.items()})


def _group_before(groups, key, timestamp, source):
    child = groups.setdefault(key, {"t": None, "current": {}, "before": {}})
    if child["t"] is not None and timestamp < child["t"]:
        child.update(t=None, current={}, before={})
    if timestamp != child["t"]:
        name, audio = source
        child["before"] = child["current"]
        child["current"] = _group_current(name, audio)
        child["t"] = timestamp
    return child["before"]


def _group_current(name, audio):
    if name == "_f_chord_change_accent":
        return {"_chord_state": {"prev": (audio.chord_tone(), audio.chord_quality)}}
    return {"_prev_rms": [audio.rms]}


def _owner_identity(owner):
    if isinstance(owner, dict):
        return owner.get("preset_id"), tuple(owner.get("ch_types", []))
    return owner[0], tuple(owner[2])


def transfer_audio_owner(panel, previous, current):
    """Only a known parameter-edit continuation can inherit these two histories."""
    identity = _owner_identity(current)
    runtime = getattr(panel, "_effect_audio_runtime", None)
    if runtime is None or identity[0] not in GROUP_IDS or _owner_identity(previous) != identity:
        return current
    key = (id(previous), identity[0])
    entry = runtime.entries.pop(key, None)
    if entry is not None and entry[0] is previous:
        runtime.entries[(id(current), identity[0])] = (current, entry[1], entry[2])
    return current


def selected_audio_frame(analyzer, t):
    """Consume the selected driver without enabling/changing any input source."""
    mode = getattr(analyzer, "_mode", "")
    if mode == "file":
        if getattr(analyzer, "_file_data", None) is None:
            return None
        frame = analyzer.current_frame()
        if frame is None or not getattr(frame, "has_audio", False):
            frame = analyzer.frame_at(float(getattr(analyzer, "_timeline_pos", 0.0)))
        return frame
    if mode == "simulate":
        return analyzer.frame_at(float(t))
    return (analyzer.current_frame() if hasattr(analyzer, "current_frame")
            else getattr(analyzer, "_last_frame", None))


def spectral_centroid_hz(bands):
    """Weighted Hz estimate from the driver's actual coarse frequency ranges."""
    from .audio_driver import FREQ_RANGES
    values = [(max(0.0, float(v)), (low + high) * 0.5)
              for v, (low, high) in zip(bands, FREQ_RANGES)]
    total = sum(v for v, _ in values)
    return sum(v * hz for v, hz in values) / total if total > 1e-9 else 0.0
