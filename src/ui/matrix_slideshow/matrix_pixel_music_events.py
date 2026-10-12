"""Keep real transient beats until the existing Matrix effect tick consumes them.

Reuse CPython 3.13.13 deque(maxlen) and the unique Analyzer's source generation.
No capture, analysis, timer, thread, Tap-history access or feature calculation.
References: CPython v3.13.13 Modules/_collectionsmodule.c L328-375 (PSF-2).
"""
from collections import deque
import math


BEAT_MODES = ("audio_music_beat_relay", "audio_music_beat_response", "audio_music_beat_weave")
_INBOX = "music_input_events"


def music_source_token(engine):
    analyzer = getattr(engine, "_analyzer", None)
    runtime = getattr(analyzer, "_onset_runtime", None)
    return (getattr(engine, "audio_controls_version", 0), engine.pixel_audio_reactive,
            id(analyzer), getattr(analyzer, "_mode", ""), getattr(analyzer, "_file_path", ""),
            id(getattr(analyzer, "_file_data", None)), id(getattr(engine, "_audio", None)),
            getattr(runtime, "generation", 0))


def _enabled(engine):
    analyzer = getattr(engine, "_analyzer", None)
    return (engine._mode in BEAT_MODES and getattr(engine, "audio_controls_version", 0) == 1
            and engine.pixel_audio_reactive and getattr(analyzer, "_mode", "") != "simulate")


def _new_inbox(engine, timestamp, token):
    inbox = dict(mode=engine._mode, source=token, last_time=timestamp,
                 last_beat=-math.inf, pending=deque(maxlen=16))
    engine._effect_state[_INBOX] = inbox
    return inbox


def _inbox_for_frame(engine, timestamp):
    inbox = engine._effect_state.get(_INBOX)
    token = music_source_token(engine)
    if inbox is None:
        return _new_inbox(engine, timestamp, token)
    changed = inbox["mode"] != engine._mode or inbox["source"] != token
    rewound = timestamp < inbox["last_time"] - 1e-4
    if changed or rewound:
        engine._effect_state.pop("music_arrangement", None)
        return _new_inbox(engine, timestamp, token)
    inbox["last_time"] = timestamp
    return inbox


def capture_music_frame(engine, frame):
    """The latest continuous frame remains original; retain only real events."""
    if not _enabled(engine) or not engine._running:
        return
    if not bool(getattr(frame, "has_audio", False)):
        return
    timestamp = float(getattr(frame, "t", math.nan))
    if not math.isfinite(timestamp):
        return
    inbox = _inbox_for_frame(engine, timestamp)
    if bool(getattr(frame, "is_beat", False)) and timestamp > inbox["last_beat"]:
        bpm = max(30.0, min(240.0, float(getattr(frame, "bpm", 120.0))))
        inbox["pending"].append((timestamp, bpm))
        inbox["last_beat"] = timestamp


def take_music_beats(engine, state):
    """None keeps the legacy fallback; an empty list means no new real event."""
    if not _enabled(engine):
        return None
    inbox = engine._effect_state.get(_INBOX)
    if inbox is None or inbox["source"] != state["source"]:
        return None
    pending, events = inbox["pending"], []
    # One original source producer; append/popleft are native bounded deque
    # operations. Avoid iterating a mutable deque or an unbounded drain loop.
    for _index in range(16):
        if not pending:
            break
        events.append(pending.popleft())
    return events


def clear_music_events(engine):
    engine._effect_state.pop(_INBOX, None)
    if engine._mode in BEAT_MODES and getattr(engine, "audio_controls_version", 0) == 1:
        engine._effect_state.pop("music_arrangement", None)
