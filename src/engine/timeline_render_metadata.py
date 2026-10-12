"""Render dependency projection, separate from complete show persistence.

Reuse original model serializers. final-cache/input separation and
Kdenlive's semantic preview invalidation are design references only (R214
upstream-research.md); no GPL implementation is copied.
"""
from __future__ import annotations

import hashlib
import json


_PROJECT_DISPLAY = (
    "name", "created_at", "markers", "loop", "audio_bridge_state",
    "matrix_slideshow_state", "audio_driver_state", "effect_browser_state",
    "processor_state", "route_order_state", "programmer_cues_data",
    "baked_arrangement_data", "route_animator_state", "dispatch_state",
    "programmer_state", "clip_studio_state",
)
_PROGRAM_DISPLAY = ("name", "created_at", "updated_at")
_TRACK_DISPLAY = ("name", "color", "height", "folder_expanded")
_CLIP_DISPLAY = ("label", "preset_name", "smooth_preset_name")
_NETWORK_TRANSPORT = (
    "protocol", "input_mode", "input_ip", "input_universes", "input_enabled",
    "output_mode", "output_ip", "output_universes", "output_port", "sacn_priority",
)
_EMPTY_TOPVIEW = {
    "pages": [{"id": "page-1", "name": "Sheet1", "fixtures": {},
               "cad_shapes": [], "pattern_matrices": [],
               "layout_group_spacing_percent": 100.0}],
    "active_page": 0, "z_angle_deg": 0.0, "selected_ids": [],
    "selection_order_ids": [], "active_group_id": "",
    "live_selection_ids": [], "live_base_ids": [],
    "live_selection_name": "TopView实时选择", "xy_reference_by_fixture": {},
    "xy_reference_global_angle_deg": 0.0,
}
_TOPVIEW_EDITOR = (
    "selected_ids", "selection_order_ids", "active_group_id",
    "live_selection_ids", "live_base_ids", "live_selection_name",
)
_PAGE_AUTHORING = ("layout_group_spacing_percent", "layout_group_spacing_px")


# Exact empty schemas emitted by the original normalizers/stores on Save.
# These literals are eligibility certificates, not a second normalization path:
# nondefault, partial, unfamiliar and differently typed inputs stay dependent.
_EMPTY_AUDIO_MODULATION = {
    "version": 1, "audio_sha": "", "scope_folder_id": "", "target_ch_types": [],
    "amount": 1.0, "attack": 0.05, "release": 0.2, "feature": "beat_strength",
    "default_wave": "", "spread_deg": 0.0, "phase_from_deg": 0.0,
    "phase_to_deg": 0.0, "clips": [], "sources": [], "rules": [],
    "draw_graph": {"nodes": [], "edges": []},
}
_EMPTY_SAVE_LIBRARIES = {
    "param_envelopes_data": {"version": 2, "envelopes": [], "bindings": {}},
    "trajectory_curves_data": {"version": 1, "curves": []},
}


def _without(data, names):
    return {key: value for key, value in data.items() if key not in names}


def _topview_metadata(state):
    state = _without(state, _TOPVIEW_EDITOR)
    if type(state.get("pages")) is list:
        state["pages"] = [_without(page, _PAGE_AUTHORING) if type(page) is dict else page
                          for page in state["pages"]]
    return state


def standard_sender_filter(rule):
    """Only the original final-send rule; unfamiliar extensions stay dependent."""
    from src.core.models import FilterRule
    return (rule is None or (type(rule) is FilterRule
            and set(vars(rule)) == {"channels", "universe"}
            and type(rule.channels) is set and type(rule.universe) is int))


def render_network_inputs(network):
    """Preserve numerical inputs; exclude the original final hardware filter."""
    import numpy as np
    result = dict(network)
    mask = result.get("_output_filter_mask")
    if (standard_sender_filter(result.get("_output_filter"))
            and (mask is None or (type(mask) is np.ndarray
                 and mask.shape == (512,) and mask.dtype == np.bool_))):
        result.pop("_output_filter", None)
        result.pop("_output_filter_mask", None)
    return result


def render_clip_metadata(clip):
    """Keep all authored numeric parameters and unknown serialized fields."""
    data = clip if isinstance(clip, dict) else clip.to_dict(include_frames=False)
    return _without(data, _CLIP_DISPLAY)


def render_network_metadata(config):
    """Omit only ten audited routing keys; retain unfamiliar representations."""
    if type(config) is dict:
        return _without(config, _NETWORK_TRANSPORT)
    from src.core.models import NetworkConfig
    if type(config) is NetworkConfig:
        # The owned snapshot is already detached. vars preserves every unknown
        # object attribute, unlike to_dict's current ten-field serializer.
        return _without(vars(config), _NETWORK_TRANSPORT)
    return config


def project_tracks_metadata(tracks):
    """Use the same clip projection for current/legacy/inactive track containers."""
    return _projected_tracks([track.to_dict(include_frames=False) for track in tracks or []])


def _projected_tracks(tracks):
    result = []
    for track in tracks or []:
        data = _without(track, _TRACK_DISPLAY)
        if "clips" in data:
            data["clips"] = [render_clip_metadata(clip) for clip in data["clips"]]
        result.append(data)
    return result


def _program_metadata(program):
    data = _without(program, _PROGRAM_DISPLAY)
    data["tracks"] = _projected_tracks(data.get("tracks"))
    _canonical_audio_defaults(data)
    return data


def _matches_empty_default(value, default):
    """Exact schema only; retain unknown keys, types and float representations."""
    if type(value) is not type(default):
        return False
    if type(default) is dict:
        return (value.keys() == default.keys()
                and all(_matches_empty_default(value[key], item) for key, item in default.items()))
    if type(default) is list:  # Every certified default list is empty.
        return len(value) == 0
    if type(default) is float:
        return value.hex() == default.hex()
    return value == default


def _canonical_empty_libraries(data):
    for name, default in _EMPTY_SAVE_LIBRARIES.items():
        if _matches_empty_default(data.get(name), default):
            data[name] = {}


def _canonical_audio_defaults(data):
    # AudioRefTrack.set_audio_clip_state defaults these exact omitted fields
    # to zero; its live serializer writes them on opening/saving a program.
    # Preserve every nonzero and unfamiliar field, and complete persistence.
    if data.get("audio_clip_state") == {"start_time": 0., "fade_in": 0., "fade_out": 0.}:
        data["audio_clip_state"] = {}
    if _matches_empty_default(data.get("audio_modulation_state"), _EMPTY_AUDIO_MODULATION):
        data["audio_modulation_state"] = {}


def render_metadata(project):
    """Persistence/UI edits do not change authored DMX rendering inputs.

    Keep TopView geometry, page names (LT companion matching), logical-device
    membership and authored clip order. TopView's editor selection stays saved
    separately from captured clip members/order and bound output owners.
    """
    data = _without(project.to_dict(include_frames=False), _PROJECT_DISPLAY)
    if standard_sender_filter(project.output_filter):
        data.pop("output_filter", None)
    data["tracks"] = _projected_tracks(data.get("tracks"))
    data["programs"] = [_program_metadata(program) for program in data.get("programs", [])]
    _canonical_audio_defaults(data)
    _canonical_empty_libraries(data)
    if "network_config" in data:
        data["network_config"] = render_network_metadata(data["network_config"])
    # Detach serializer's nested live dicts before canonicalization/comparison.
    data = json.loads(json.dumps(data, ensure_ascii=False))
    state = data.get("topview_state")
    if isinstance(state, dict):
        state = _topview_metadata(state)
        data["topview_state"] = state
    # MainWindow hydrates an absent TopView into this exact default empty page
    # on a delayed startup timer. There is no authored geometry in either form.
    # Only the six audited editor keys are excluded; real page/XY and unknown
    # fields still prevent default-empty canonicalization.
    if state == _topview_metadata(_EMPTY_TOPVIEW):
        data["topview_state"] = {}
    return data


def metadata_signature(project):
    """No frame-array bytes are read; suitable for a UI edit notification."""
    data = json.dumps(render_metadata(project), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(data.encode("utf-8")).digest()
