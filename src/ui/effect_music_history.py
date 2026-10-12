"""Bounded snapshots for musical effects; no audio acquisition or saved state."""
from collections import deque
from bisect import bisect_left


AUDIO_MUSIC_IDS = frozenset("r189_audio_" + suffix for suffix in (
    "peak_memory", "band_envelope", "onset_relay", "onset_echo", "spectrum_memory",
    "interval_open", "chord_wipe", "bar_steps", "phrase_rest", "band_dialogue",
    "dynamic_focus", "spectral_change",
))


class MusicHistoryState:
    def __init__(self):
        self.samples = deque(maxlen=256)
        self.effect_time = None
        self.beats = 0
        self.dialogue = {}

    def reset(self):
        self.samples.clear()
        self.effect_time = None
        self.beats = 0
        self.dialogue.clear()

    def begin_frame(self, t):
        if self.effect_time is not None and t < self.effect_time:
            self.reset()
        self.effect_time = t

    def advance(self, audio, timestamp):
        if not audio.ok:
            self.reset()
            return
        if self.samples and timestamp < self.samples[-1][0]:
            self.reset()
        if not self.samples or timestamp != self.samples[-1][0]:
            if self.samples and audio.beat_phase < self.samples[-1][1].beat_phase - 0.4:
                self.beats += 1
            self.samples.append((timestamp, audio))


def recent(state, seconds):
    if not state.samples:
        return []
    now = state.samples[-1][0]
    return [(now - t, a) for t, a in state.samples if now - t <= seconds]


def onset_events(state, threshold):
    events, previous = [], 0.0
    now = state.samples[-1][0] if state.samples else 0.0
    for t, audio in state.samples:
        if audio.onset >= threshold and previous < threshold:
            events.append((now - t, audio.onset))
        previous = audio.onset
    return events


def last_change(state, field):
    previous, change = None, None
    now = state.samples[-1][0] if state.samples else 0.0
    for t, audio in state.samples:
        value = field(audio)
        if previous is not None and value is not None and value != previous:
            change = (now - t, previous, value)
        previous = value
    return change


def sample_at_age(state, age):
    if not state.samples:
        return None
    target = state.samples[-1][0] - age
    index = min(len(state.samples) - 1,
                bisect_left(state.samples, target, key=lambda sample: sample[0]))
    before = max(0, index - 1)
    selected = min((before, index), key=lambda i: abs(state.samples[i][0] - target))
    return state.samples[selected][1]


def beat_count(state):
    return state.beats


def band_envelope(state, params):
    value, previous_t = 0.0, None
    band = int(params.get("band", 0))
    for t, audio in state.samples:
        target = audio.band(band)
        dt = max(0.0, t - previous_t) if previous_t is not None else 0.025
        tau = params["attack"] if target > value else params["release"]
        value += (target - value) * min(1.0, dt / max(0.02, tau))
        previous_t = t
    return value


def spectral_changes(state, threshold):
    output, previous = [], None
    now = state.samples[-1][0] if state.samples else 0.0
    for t, audio in state.samples:
        if previous is not None:
            delta = [max(0.0, v - old - threshold)
                     for v, old in zip(audio.bands, previous.bands)]
            output.append((now - t, delta))
        previous = audio
    return output
