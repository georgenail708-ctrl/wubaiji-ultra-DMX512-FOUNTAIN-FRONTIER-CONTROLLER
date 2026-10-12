"""K2 eligibility for the original post-mix AudioMod consumer.

The current C table has no scoped clip/rule modulation stage. Reuse the same
Project-frame source and state normalization as Python; keep its exact bytes
until that stage is implemented and checked. No CE/environment switch is set.
"""
from __future__ import annotations


def requires_python_audio_scope(engine):
    from src.core.project_frame_origin import frame_project
    project = frame_project(engine)
    raw = getattr(project, "audio_modulation_state", None)
    if not raw:
        return False
    from src.engine.audio_modulation import normalize_state
    state = normalize_state(raw)
    return bool(state.get("scope_folder_id") and state.get("processor_mode") != "chain"
                and (state.get("clips") or state.get("rules")))
def requires_python_clip_time(engine, clip):
    """Native clamp cannot represent the original repeated source of a stretched clip."""
    from src.engine.playback_engine import _clip_extends_past_source
    return bool(getattr(clip, "resize_cycle", True)) and _clip_extends_past_source(engine, clip)


