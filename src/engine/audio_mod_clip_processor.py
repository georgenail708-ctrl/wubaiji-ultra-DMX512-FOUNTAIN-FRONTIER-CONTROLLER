"""Existing Clip audio processing seam; original filters/mixer/output stay owners.

AM21 references/contracts are recorded in R185 execution map and the original
preview report. Reuses original folder audibility, active masks and output safety.
No Clip creation, analyzer, sender, timer or independent playback state.
"""
from __future__ import annotations

from weakref import WeakKeyDictionary
import numpy as np

from src.core.project_frame_origin import frame_project
from .clip_output_filters import apply_clip_output_filters, finalize_clip_output

_FRAMES = WeakKeyDictionary()
_SCOPES = WeakKeyDictionary()
_FEEDBACK = WeakKeyDictionary()


def _raw_state(project):
    state = getattr(project, "audio_modulation_state", None)
    return state if isinstance(state, dict) else {}


def _chain(project):
    return _raw_state(project).get("processor_mode") == "chain"


def _track_rows(project):
    return [{"id": str(getattr(t, "id", "")),
             "parent_id": str(getattr(t, "parent_id", "") or ""),
             "is_folder": bool(getattr(t, "is_folder", False)),
             "muted": bool(getattr(t, "muted", False)),
             "solo": bool(getattr(t, "solo", False))}
            for t in getattr(project, "tracks", ())]


def _scope_ids(engine, project, folder):
    from .audio_mod_folder_scope import _folder_subtree_track_ids, _audible_track_ids
    rows = _track_rows(project)
    key = (id(project), folder, tuple(tuple(row.items()) for row in rows))
    cached = _SCOPES.get(engine)
    if cached is not None and cached[0] == key:
        return cached[1]
    wanted = _folder_subtree_track_ids(rows, folder) & _audible_track_ids(rows)
    _SCOPES[engine] = (key, wanted)
    return wanted


def _enabled(rows):
    return any(r["enabled"] and r["wet"] > 0.0 and r["targets"] for r in rows)


def _context_values(engine, project, pos, raw, rows):
    from .native_audio_bridge import context_math
    native = context_math(engine, project, float(pos), raw, rows)
    if native is not None:
        return native
    from .audio_modulation import normalize_state, _active_envelope_norm
    from .audio_mod_feature_view import feature_view
    env = _active_envelope_norm(normalize_state(raw), float(pos)) if raw.get("clips") else 1.0
    if env <= 0.0:
        return env, rows, None
    rows = [dict(row, wet=row["wet"] * env) for row in rows]
    offline = bool(getattr(engine, "_timeline_render_cache_building_lock", False))
    return env, rows, feature_view(project, float(pos), raw, offline=offline)


def _make_context(engine, project, pos):
    from .audio_mod_lifetime import suppressed
    from .audio_mod_processor_schema import normalize_processors, processor_key
    from .audio_mod_processor_targets import build_target_plan
    raw = _raw_state(project)
    if suppressed(project, raw):
        return None
    rows = normalize_processors(raw.get("processors"))
    if not _enabled(rows):
        return None
    folder = str(raw.get("scope_folder_id", "") or "")
    wanted = _scope_ids(engine, project, folder)
    if not wanted:
        return None
    env, rows, features = _context_values(engine, project, pos, raw, rows)
    if env <= 0.0:
        return None
    token = (processor_key(rows), features["epoch"], features["ready"], features["has_audio"],
             tuple(features[key] for key in ("energy", "beat_strength", "band_low", "band_mid",
                                            "band_high", "onset_flux", "beat_phase")))
    return {"project": project, "pos": float(pos), "tracks": wanted, "rows": rows,
            "features": features, "plan": build_target_plan(project), "token": token}


def begin_audio_frame(engine, project, pos, layer_name):
    """Called once at the original per-frame project point, not once per channel."""
    _FRAMES.pop(engine, None)
    _FEEDBACK.pop(engine, None)
    if layer_name != "clip_playback" or not _chain(project):
        return project
    context = _make_context(engine, project, pos)
    if context is not None:
        _FRAMES[engine] = context
        _FEEDBACK[engine] = dict(context["features"], input_peak=0., output_peak=0., processed_clips=0)
    return project


def _context(engine, project, pos):
    if not _chain(project):
        return None
    current = _FRAMES.get(engine)
    if current is not None and current["project"] is project and current["pos"] == float(pos):
        return current
    return _make_context(engine, project, pos)


def audio_item_key(engine, track, project, pos):
    context = _context(engine, project, pos)
    if context is None or str(getattr(track, "id", "")) not in context["tracks"]:
        return ()
    return context["token"]


def _input_active(engine, clip, universe, payload):
    from src.core.native_mask_math import mask_or, filtered_active
    _track, _project, _pos, compiled, raw, source_t, hold = payload[:7]
    base = compiled.get("base_active")
    if base is None:
        base = engine._clip_channel_active_mask(clip, universe)
    active = engine._clip_active_mask_for_rendered_raw(
        clip, universe, raw, base, source_t=source_t, hold_extension=hold)
    if bool(getattr(clip, "light_tracking_enabled", False)):
        active = mask_or(active, np.asarray(compiled.get("light_mask", np.zeros(512, dtype=bool)), dtype=bool))
    rule = compiled.get("rule_active")
    if rule is not None:
        rule = np.asarray(rule, dtype=bool)
        active = filtered_active(active, rule, explicit=False)
    return active


def _process(engine, clip, universe, data, payload):
    from .audio_mod_processors import apply_processors
    track, project, pos, _compiled, _raw, _source_t, _hold = payload[:7]
    context = _context(engine, project, pos)
    if context is None or str(getattr(track, "id", "")) not in context["tracks"]:
        return data, None
    active = _input_active(engine, clip, universe, payload)
    processed = apply_processors(data, active, context["rows"],
                                 context["plan"].get(int(universe), {}), context["features"])
    feedback = _FEEDBACK.get(engine)
    from .native_audio_bridge import update_feedback
    native_feedback = feedback is not None and update_feedback(feedback)
    if feedback is not None and not native_feedback and processed is not data and np.any(active):
        feedback["input_peak"] = max(feedback["input_peak"], float(np.max(data[active])))
        feedback["output_peak"] = max(feedback["output_peak"], float(np.max(processed[active])))
        feedback["processed_clips"] += 1
    return processed, active


def apply_clip_filters_with_audio(engine, clip, universe, data, payload):
    """Normal Clip path: existing audio processing precedes original safety once."""
    if _chain(payload[1]):
        data, _active = _process(engine, clip, universe, data, payload)
    return apply_clip_output_filters(engine, clip, universe, data,
                                     local_t=float(payload[7]), proj=payload[1])


def finalize_clip_with_audio(engine, clip, universe, data, payload, *, local_t):
    """Transition sample path; return input ownership so a gated zero stays owned."""
    active = None
    if _chain(payload[1]):
        data, active = _process(engine, clip, universe, data, payload)
    data = finalize_clip_output(engine, clip, universe, data, local_t=local_t, proj=payload[1])
    return data, active


def _source_token(project):
    from .audio_mod_feature_view import feature_view
    from .native_audio_bridge import feature_view as native_view
    view = native_view(project, 0.0, False)
    if view is None:
        view = feature_view(project, 0.0)
    return (view["source"], view["epoch"], view["ready"])


def audio_runtime_signature(engine, original):
    """Old projects keep the exact original token; new source changes invalidate bake."""
    project = frame_project(engine)
    from .audio_mod_lifetime import runtime_signature
    original = runtime_signature(project, original)
    if not _chain(project):
        return original
    from .audio_mod_processor_schema import processor_key
    raw = _raw_state(project)
    return (original, raw.get("scope_folder_id", ""), processor_key(raw.get("processors")),
            _source_token(project))


def audio_cache_allowed(engine, locked, *, consume=False):
    if not locked:
        return False
    project = frame_project(engine)
    if not _chain(project):
        return locked
    mode, _epoch, ready = _source_token(project)
    # R187-①：删 consume 主动 unlock——serve 门 False 已让本帧回退实时渲染
    # （调制实时生效）；主动 unlock 只会把锁定缓存反复作废，配合 autobake
    # 形成"播放→解锁→停止→重烘→再播放"循环（用户实测反复重渲染）。
    return mode == "file" and ready


def processor_feedback(engine):
    """Small existing-workspace feedback snapshot, no output readback/render."""
    value = _FEEDBACK.get(engine)
    return dict(value) if value is not None else {}
