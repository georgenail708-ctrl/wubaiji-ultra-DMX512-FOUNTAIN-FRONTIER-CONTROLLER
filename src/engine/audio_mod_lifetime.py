"""Retain timeline authorship after the last audio span or its owner is deleted."""
from __future__ import annotations


def timeline_gated(state):
    return bool(state.get("timeline_gated") or state.get("clips"))


def suppressed(project, state=None):
    if state is None:
        state = getattr(project, "audio_modulation_state", None) or {}
    if timeline_gated(state) and not state.get("clips"):
        return True
    owner = str(state.get("scope_track_id", "") or "")
    return bool(owner and not any(str(track.id) == owner
                                  for track in getattr(project, "tracks", ())))


def runtime_signature(project, original):
    state = getattr(project, "audio_modulation_state", None) or {}
    owner = str(state.get("scope_track_id", "") or "")
    gated = timeline_gated(state)
    if not owner and not gated:
        return original
    return (original, "audio_author", owner, gated, suppressed(project, state))
