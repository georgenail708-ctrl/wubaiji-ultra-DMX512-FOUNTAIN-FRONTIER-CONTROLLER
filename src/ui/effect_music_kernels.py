"""Small, read-only musical kernels using the existing Effect compute contract.

Design references: Chaser's separate fade/hold timings and 串口固件's per-
segment history. No upstream source is copied; the selected AudioAnalyzer is
the sole producer. Generic application direction/width remains in the engine.
"""
from dataclasses import dataclass, field
import math

from .effect_music_history import (
    MusicHistoryState, band_envelope, beat_count, last_change, onset_events,
    recent, sample_at_age, spectral_changes,
)
from .effect_presets_audio_true import _AudioRead


def c01(value):
    return max(0.0, min(1.0, float(value)))


def ease(value, curve="平滑"):
    value = c01(value)
    return value * value * (3 - 2 * value) if curve == "平滑" else value


def segment(t, params):
    attack, hold, release = params["attack"], params["hold"], params["release"]
    if t < 0:
        return 0.0
    if t < attack:
        return ease(t / max(0.02, attack), params.get("curve", "平滑"))
    if t < attack + hold:
        return 1.0
    return 1 - ease((t - attack - hold) / max(0.02, release), params.get("curve", "平滑"))


@dataclass
class MusicFrame:
    t: float
    count: int
    params: dict
    audio: object
    history: object
    events: list
    features: dict = field(default_factory=dict)


def phrase(frame, pos, index):
    p = frame.params
    cycle = p["attack"] + p["hold"] + p["release"] + p["rest"]
    return segment(frame.t % max(0.02, cycle), p)


def relay(frame, pos, index):
    p = frame.params
    size = max(1, int(p["group_size"]))
    groups = max(1, math.ceil(frame.count / size))
    cycle = groups * p["stagger"] + p["attack"] + p["hold"] + p["release"]
    local = (frame.t - (index // size) * p["stagger"]) % max(0.02, cycle)
    return segment(local, p)


def dual_clock(frame, pos, index):
    p = frame.params
    a = float((frame.t / p["cycle_a"]) % 1 < p["duty_a"])
    b = float((frame.t / p["cycle_b"]) % 1 < p["duty_b"])
    primary, other = (a, b) if index % 2 == 0 else (b, a)
    return primary * (1 - p["mix"]) + other * p["mix"]


def accent_cycle(frame, pos, index):
    p = frame.params
    step = int(frame.t * p["rate"]) % int(p["steps"])
    level = p["accent_level"] if step % int(p["accent_every"]) == 0 else p["weak_level"]
    gate = (frame.t * p["rate"]) % 1 < p["duty"]
    return level if gate and index % int(p["steps"]) == step else 0.0


def peak_memory(frame, pos, index):
    peak = frame.features["value"]
    return peak if pos <= peak else 0.0


def _peak_value(frame):
    p = frame.params
    values = [a.peak * c01(1 - max(0.0, age - p["hold"]) / p["release"])
              for age, a in recent(frame.history, p["hold"] + p["release"])]
    return max(values, default=0.0)


def band_response(frame, pos, index):
    return frame.features["value"]


def onset_relay(frame, pos, index):
    p = frame.params
    waves = [strength * math.exp(-((pos - age / p["travel"]) / p["width"]) ** 2)
             for age, strength in frame.events if age <= p["travel"] * 1.15]
    return max(waves, default=0.0)


def onset_echo(frame, pos, index):
    p = frame.params
    waves = [strength * (p["decay"] ** echo) * math.exp(-max(0, age - echo * p["echo_delay"]) * 15)
             for age, strength in frame.events for echo in range(int(p["echo_count"]))
             if 0 <= age - echo * p["echo_delay"] < 0.3
             and index % int(p["echo_count"]) == echo]
    return max(waves, default=0.0)


def spectrum_memory(frame, pos, index):
    p = frame.params
    past = sample_at_age(frame.history, pos * p["history_seconds"])
    return c01(past.band(index % int(p["band_count"])) * p["contrast"]) if past else 0.0


def interval_open(frame, pos, index):
    p = frame.params
    event = frame.features["value"]
    if event is None:
        return 0.0
    age, before, after = event
    extent = min(1.0, abs(after - before) / p["interval_scale"]) * c01(1 - age / p["hold"])
    distance = abs(pos - 0.5) * 2
    return c01((extent - distance) * 6) if after >= before else c01((distance - (1 - extent)) * 6)


def chord_wipe(frame, pos, index):
    root = frame.audio.chord_tone()
    quality = frame.audio.chord_quality
    if root < 0 or quality not in {"major", "minor"}:
        return 0.0
    p = frame.params
    event = frame.features["value"]
    progress = min(1.0, event[0] / p["transition"]) if event else 1.0
    third = 3 if quality == "minor" else 4
    selected = index % 12 in {root, (root + third) % 12, (root + 7) % 12}
    return 1.0 if selected and pos <= progress else p["background"]


def bar_steps(frame, pos, index):
    p = frame.params
    step = frame.features["value"] % int(p["beats_per_bar"])
    target = step / max(1, int(p["beats_per_bar"]) - 1)
    level = 1.0 if step == 0 else p["accent"]
    return level * math.exp(-((pos - target) / p["width"]) ** 2)


def phrase_rest(frame, pos, index):
    p = frame.params
    age = min((age for age, _s in frame.events), default=frame.t)
    opening = c01(1 - max(0.0, age - p["rest_seconds"]) / p["release"])
    return opening if abs(pos - 0.5) * 2 <= opening else 0.0


def band_dialogue(frame, pos, index):
    p = frame.params
    side = frame.features["value"]
    active = pos >= 0.5 if side else pos < 0.5
    return (0.5 + frame.audio.rms * 0.5) if active else p["background"]


def dynamic_focus(frame, pos, index):
    p = frame.params
    average = frame.features["value"]
    contrast = c01(0.5 + (frame.audio.rms - average) * p["contrast"])
    return contrast * math.exp(-((pos - contrast) / p["width"]) ** 2)


def spectral_change(frame, pos, index):
    p = frame.params
    return frame.features["value"][index % 8]


def _band_side(frame):
    margin, state = frame.params["margin"], frame.history
    side = state.dialogue.get(margin, False)
    if abs(frame.audio.high - frame.audio.low) > margin:
        side = frame.audio.high >= frame.audio.low
    if margin not in state.dialogue and len(state.dialogue) >= 16:
        del state.dialogue[next(iter(state.dialogue))]
    state.dialogue[margin] = side
    return side


def _average(frame):
    values = [a.rms for _age, a in recent(frame.history, frame.params["window"])]
    return sum(values) / max(1, len(values))


def _spectral_values(frame):
    p = frame.params
    changes = spectral_changes(frame.history, p["threshold"])
    return [max((delta[band] * p["gain"] * math.exp(-age / p["decay"])
                 for age, delta in changes if band < len(delta)), default=0.0)
            for band in range(8)]


PREPARERS = {
    "audio_peak_memory": _peak_value,
    "audio_band_envelope": lambda f: band_envelope(f.history, f.params),
    "audio_interval_open": lambda f: last_change(f.history, lambda a: a.pitch if a.pitch_conf > 0 else None),
    "audio_chord_wipe": lambda f: last_change(f.history, lambda a: (a.chord_root, a.chord_quality) if a.chord_root >= 0 else None),
    "audio_bar_steps": lambda f: beat_count(f.history),
    "audio_band_dialogue": _band_side, "audio_dynamic_focus": _average,
    "audio_spectral_change": _spectral_values,
}


KERNELS = {
    "phrase_envelope": phrase, "group_overlap_relay": relay,
    "dual_clock_weave": dual_clock, "accent_step_cycle": accent_cycle,
    "audio_peak_memory": peak_memory, "audio_band_envelope": band_response,
    "audio_onset_relay": onset_relay, "audio_onset_echo": onset_echo,
    "audio_spectrum_memory": spectrum_memory, "audio_interval_open": interval_open,
    "audio_chord_wipe": chord_wipe, "audio_bar_steps": bar_steps,
    "audio_phrase_rest": phrase_rest, "audio_band_dialogue": band_dialogue,
    "audio_dynamic_focus": dynamic_focus, "audio_spectral_change": spectral_change,
}


def _frame_context(variant, t, count, params):
    audio = _AudioRead(params.get("_audio_data") or {})
    musical = variant.startswith("audio_")
    live = not musical or (audio.ok and callable(params.get("_audio_provider")))
    history = params.get("_audio_runtime") or MusicHistoryState()
    timestamp = float((params.get("_audio_data") or {}).get("t", t))
    if musical:
        history.advance(audio, timestamp)
    events = onset_events(history, params.get("threshold", 0.35)) if musical else []
    frame = MusicFrame(float(t), count, params, audio, history, events)
    return _prepare_features(variant, frame, live), live


def _prepare_features(variant, frame, live):
    prepare = PREPARERS.get(variant)
    if prepare is not None and live:
        frame.features["value"] = prepare(frame)
    return frame


def _render(variant, args, defaults):
    ef, t, sorted_fx, _defs, params, _fps = args
    params = defaults | params
    frame, live = _frame_context(variant, t, len(sorted_fx), params)
    lo, hi, ch = int(params["lo"]), int(params["hi"]), params["ch_type"]
    for i, (original, _fixture) in enumerate(sorted_fx):
        weight = KERNELS[variant](frame, i / max(1, len(sorted_fx) - 1), i) if live else 0.0
        ef.values[original][ch] = int(lo + c01(weight) * (hi - lo))


def kernel_for(variant, defaults):
    def compute(*args):
        _render(variant, args, defaults)
    return compute
