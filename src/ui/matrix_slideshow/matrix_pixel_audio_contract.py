"""Shared-source device events and transactional array fallback.

Reuse the original Analyzer owner, Qt signal blocker and Pixel RC envelope.
Attachment/parameter restore never configures the shared source. No DSP copied.
"""
from PyQt6.QtCore import QSignalBlocker


_AUDIO_FRAME_KEYS = ("_audio_mod_dt", "_audio_mod_env", "_audio_mod_frame_env")


def select_audio_device(engine, device):
    """Apply an explicit device event to the already selected microphone source."""
    engine.audio_device = -1 if device is None else int(device)
    if not engine._ensure_shared_audio_analyzer():
        return
    analyzer = engine._analyzer
    if analyzer._mode != "microphone":
        return
    requested = engine.audio_device if engine.audio_device >= 0 else None
    if analyzer.get_mic_device() != requested:
        analyzer.set_mic_device(requested)


def sync_audio_device(panel):
    """Reflect saved device metadata without making it a source selection event."""
    combo = panel._audio_device_combo
    row = combo.findData(panel._engine.audio_device)
    if row >= 0:
        with QSignalBlocker(combo):
            combo.setCurrentIndex(row)


def refresh_audio_devices(panel):
    """Rebuild the original device choices without dispatching implicit changes."""
    combo = panel._audio_device_combo
    with QSignalBlocker(combo):
        combo.clear()
        combo.addItem("默认设备", -1)
        for device in panel._engine.list_audio_devices():
            combo.addItem(device["name"], device["index"])
        sync_audio_device(panel)


def prepare_array_frame(engine, dt):
    """Preserve only the original envelope preparation fields before probing."""
    previous = (engine._t, {key: engine._effect_state[key]
                           for key in _AUDIO_FRAME_KEYS if key in engine._effect_state})
    engine._prepare_audio_mod_frame(dt)
    engine._t += dt * engine.speed * engine._audio_mod_time_scale()
    return previous


def restore_array_frame(engine, previous):
    """A natural unsupported array mode leaves preparation to its dict tick."""
    engine._t, envelope = previous
    for key in _AUDIO_FRAME_KEYS:
        if key in envelope:
            engine._effect_state[key] = envelope[key]
        else:
            engine._effect_state.pop(key, None)
