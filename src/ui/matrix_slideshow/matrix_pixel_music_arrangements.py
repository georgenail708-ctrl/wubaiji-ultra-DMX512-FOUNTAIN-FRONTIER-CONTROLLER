"""Music-to-space arrangements consume the existing unique audio service.

External firmware frequency/history mapping and elapsed-time history informed the
contracts; their implementation code is not copied. NumPy/grid APIs are reused.
"""
import math

import numpy as np

from .matrix_pixel_audio_modes import _frame_dt, _mapped_levels
from .matrix_pixel_music_catalog import MUSIC_DEFAULTS


def _parameter(engine, key):
    return float(getattr(engine, key, MUSIC_DEFAULTS[key]))


def _source_token(engine):
    from .matrix_pixel_music_events import music_source_token
    return music_source_token(engine)


def _new_state(mode, token):
    return dict(mode=mode, source=token, elapsed=0.0, last_audio_time=None,
                beat_latched=False, beat_count=0, events=[], points=[],
                last_point_time=-1.0, terrain=np.zeros(8, dtype=np.float32))


def _state(engine, mode):
    token = _source_token(engine)
    state = engine._effect_state.get("music_arrangement")
    frame = getattr(engine, "_last_audio_frame", None)
    timestamp = getattr(frame, "t", None)
    if state is None or state["mode"] != mode or state["source"] != token:
        state = _new_state(mode, token)
    previous = state["last_audio_time"]
    if timestamp is not None and previous is not None and timestamp < previous - 1e-4:
        state = _new_state(mode, token)
    if state["last_audio_time"] is None:
        for key in tuple(engine._effect_state):
            if str(key).startswith("audio_bar_env_music_"):
                engine._effect_state.pop(key, None)
    state["last_audio_time"] = timestamp
    state["elapsed"] += _frame_dt(engine)
    engine._effect_state["music_arrangement"] = state
    return state


def _coordinates(engine):
    x, y = engine._grid.norm_x_np, engine._grid.norm_y_np
    direction = engine._audio_bar_direction()
    if direction in {"left", "right"}:
        return (x if direction == "left" else 1.0 - x), y
    return (1.0 - y if direction == "bottom" else y), x


def _beat_event(engine, state):
    from .matrix_pixel_music_events import take_music_beats
    events = take_music_beats(engine, state)
    if events is not None:
        for timestamp, bpm in events:
            age = max(0.0, float(state["last_audio_time"]) - timestamp)
            _append_beat(engine, state, state["elapsed"] - age, bpm)
        state["beat_latched"] = bool(engine._get_is_beat())
        return
    beat = bool(engine._get_is_beat())
    triggered = beat and not state["beat_latched"]
    state["beat_latched"] = beat
    if triggered:
        frame = getattr(engine, "_last_audio_frame", None)
        bpm = max(30.0, min(240.0, float(getattr(frame, "bpm", 120.0))))
        _append_beat(engine, state, state["elapsed"], bpm)


def _append_beat(engine, state, timestamp, bpm):
    strength = min(1.0, max(0.01, float(engine.audio_amplitude)) * 0.5)
    state["events"].append((timestamp, state["beat_count"], strength, bpm))
    state["beat_count"] += 1
    state["events"] = state["events"][-16:]


def _history(engine):
    return max(0.2, min(8.0, _parameter(engine, "music_history_s")))


def _steps(engine):
    return max(2, min(16, int(round(_parameter(engine, "music_steps")))))


def _width(engine):
    return max(0.02, min(0.8, _parameter(engine, "music_line_width")))


def _speed(engine):
    return max(0.05, min(10.0, float(engine.speed)))


def _line(coord, center, width):
    return np.exp(-0.5 * ((coord - center) / width) ** 2)


def _beat_relay(engine, state):
    _beat_event(engine, state)
    along, _cross = _coordinates(engine)
    values = np.zeros_like(along)
    duration, count, speed = _history(engine), _steps(engine), _speed(engine)
    for time, index, strength, _bpm in state["events"]:
        age = state["elapsed"] - time
        center = (index * speed % count) / (count - 1)
        values = np.maximum(values, _line(along, center, _width(engine)) * strength * max(0.0, 1.0 - age / duration))
    state["events"] = [event for event in state["events"] if state["elapsed"] - event[0] < duration]
    return values


def _beat_response(engine, state):
    _beat_event(engine, state)
    along, _cross = _coordinates(engine)
    values = np.zeros_like(along)
    duration, width = _history(engine), _width(engine)
    delay_beats = max(0.125, _parameter(engine, "music_delay_beats"))
    repeats = _steps(engine)
    for time, _index, strength, bpm in state["events"]:
        delay = delay_beats * 60.0 / bpm
        for repeat in range(repeats):
            age = state["elapsed"] - time - repeat * delay
            if 0.0 <= age < duration:
                drift = min(0.4, age / duration * 0.15 * _speed(engine))
                advance = (repeat // 2) / max(1, repeats // 2) * 0.75
                center = min(0.9, drift + advance) if repeat % 2 == 0 else max(0.1, 1.0 - drift - advance)
                gain = strength * 0.82 ** repeat * (1.0 - age / duration)
                values = np.maximum(values, _line(along, center, width) * gain)
    state["events"] = [event for event in state["events"]
                       if state["elapsed"] - event[0] < duration + repeats * delay_beats * 60.0 / event[3]]
    return values


def _beat_weave(engine, state):
    _beat_event(engine, state)
    along, cross = _coordinates(engine)
    values = np.zeros_like(along)
    duration, steps, width = _history(engine), _steps(engine), _width(engine)
    for time, index, strength, _bpm in state["events"]:
        age = state["elapsed"] - time
        center = (index * _speed(engine) % steps) / (steps - 1)
        axis = along if index % 2 == 0 else cross
        values = np.maximum(values, _line(axis, center, width) * strength * max(0.0, 1.0 - age / duration))
    state["events"] = [event for event in state["events"] if state["elapsed"] - event[0] < duration]
    return values


def _append_point(state, point):
    if state["elapsed"] - state["last_point_time"] >= 0.039:
        state["points"].append((state["elapsed"], *point))
        state["points"] = state["points"][-256:]
        state["last_point_time"] = state["elapsed"]


def _points_field(engine, state, pitch=False):
    from .matrix_pixel_music_pitch import pitch_distance
    along, cross = _coordinates(engine)
    values = np.zeros_like(along)
    duration, width = _history(engine), _width(engine)
    state["points"] = [point for point in state["points"] if state["elapsed"] - point[0] < duration]
    for time, x, y in state["points"]:
        age = state["elapsed"] - time
        drift = age / duration * _speed(engine)
        if pitch:
            distance = pitch_distance(engine, (along, cross), (x, y), (drift, width))
        else:
            location = min(1.0, x + drift * 0.15)
            distance = ((cross - y) / width) ** 2 + ((along - location) / width) ** 2
        values = np.maximum(values, np.exp(-0.5 * distance) * (1.0 - age / duration))
    return values


def _timbre_trail(engine, state):
    levels = _mapped_levels(engine, 8, state_key="music")
    if float(levels.sum()) > 1e-6:
        centroid = float(np.dot(levels, np.linspace(0.0, 1.0, 8)) / levels.sum())
        energy = min(1.0, max(0.0, engine._get_audio_rms() * engine.audio_amplitude))
        _append_point(state, (centroid, energy))
    return _points_field(engine, state)


def _spectral_terrain(engine, state):
    from .matrix_pixel_music_terrain import terrain_field, terrain_levels, terrain_release
    levels = terrain_levels(engine)
    dt, duration = _frame_dt(engine), _history(engine)
    previous = state["terrain"]
    attack = 1.0 - math.exp(-dt * _speed(engine) / 0.3)
    rising = previous + (levels - previous) * attack
    falling = terrain_release(engine, previous, levels, dt, duration)
    state["terrain"] = np.where(levels >= previous, rising, falling)
    return terrain_field(engine, *_coordinates(engine), state["terrain"], _width(engine))


def _pitch_steps(engine, state):
    from .matrix_pixel_music_pitch import pitch_point
    frame = getattr(engine, "_last_audio_frame", None)
    note = float(getattr(frame, "pitch_midi", -1.0))
    confidence = float(getattr(frame, "pitch_confidence", 0.0))
    lower, upper = _parameter(engine, "music_pitch_min"), _parameter(engine, "music_pitch_max")
    valid = math.isfinite(note) and lower <= note <= upper and upper > lower
    if valid and confidence >= _parameter(engine, "music_pitch_confidence"):
        _append_point(state, pitch_point(engine, (round(note) - lower) / (upper - lower)))
    return _points_field(engine, state, pitch=True)


_ALGORITHMS = {
    "audio_music_beat_relay": _beat_relay,
    "audio_music_beat_response": _beat_response,
    "audio_music_timbre_trail": _timbre_trail,
    "audio_music_beat_weave": _beat_weave,
    "audio_music_spectral_terrain": _spectral_terrain,
    "audio_music_pitch_steps": _pitch_steps,
}


def compute_music_arrangement(engine, mode):
    if engine._grid is None:
        return {fixture_id: 0.0 for fixture_id in engine._sorted_ids}
    state = _state(engine, mode)
    values = np.clip(_ALGORITHMS[mode](engine, state), 0.0, 1.0)
    return {fixture_id: float(values[index]) for index, fixture_id in enumerate(engine._sorted_ids)}
