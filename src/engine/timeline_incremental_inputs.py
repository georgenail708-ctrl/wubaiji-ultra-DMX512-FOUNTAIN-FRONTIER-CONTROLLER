"""Complete owned-input comparison for one previous sequential generation.

Reuse A01 immutable frames and the original metadata serializer; hints never
prove validity. Temporal exits retain LT/AudioMod/limit history explicitly.
Reference: R191 audit-incremental; CPython hashlib/pickle, PSF-2.0.
"""
from __future__ import annotations

import hashlib
import json
import math
import pickle

from .timeline_bake_inputs import _digest_frames, fingerprint_metadata, check_cancel
from .timeline_render_metadata import (
    project_tracks_metadata, render_clip_metadata, render_network_metadata,
    render_network_inputs,
)

_NETWORK_KEYS = (
    "_config", "_output_filter", "_output_filter_mask", "_channel_defaults",
    "_xy_reference_by_universe", "_xy_reference_z_command_deg",
    "_xy_reference_z_command_ids", "_shape_blend_mode", "_shape_gain",
    "_layer_gains", "_direct_anchor_by_universe",
)
_SEED_KEYS = ("_audio_mod_signature", "_hold_boundary_latch",
              "_hold_consumed_masks", "_takeover_consumed_masks")


def _clip_fingerprint(clip, opts):
    data = render_clip_metadata(clip)
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode())
    _digest_frames(digest, clip.frames or [], opts)
    digest.update(b"smooth_source")
    _digest_frames(digest, getattr(clip, "smooth_source_frames", ()) or (), opts)
    return {"digest": digest.digest(), "start": float(clip.start_time)}


def _strip_current_tracks(data):
    for track in data:
        track["clips"] = [str(clip["id"]) for clip in track.get("clips", [])]


def _global_metadata(project, *, legacy=False):
    data = fingerprint_metadata(project)
    if legacy:
        data["tracks"] = project_tracks_metadata(project.tracks)
        data["programs"], data["current_program_id"] = [], ""
    _strip_current_tracks(data.get("tracks", []))
    for program in data.get("programs", []):
        program.pop("updated_at", None)
        if str(program["id"]) == str(project.current_program_id):
            _strip_current_tracks(program.get("tracks", []))
    return data


def _unsupported(project, clips):
    for clip in clips:
        if not math.isfinite(float(clip.start_time)):
            return "nonfinite_time"
    return ""


def _digest_inactive_programs(digest, project, opts):
    # Inactive programs remain global dependencies, including their real arrays.
    for program in project.programs or []:
        if str(program.id) == str(project.current_program_id):
            continue
        for track in program.tracks or []:
            for clip in track.clips or []:
                digest.update(_clip_fingerprint(clip, opts)["digest"])


def fingerprint_inputs(inputs, opts):
    project = inputs["project"]
    clips = [clip for track in project.tracks or [] for clip in track.clips or []]
    # The original factory gives legacy files a transient program UUID.
    # Source program absence is covered by A01's source-token freshness guard.
    global_data = _global_metadata(project, legacy=not bool(inputs["source"].programs))
    network = {key: inputs["network"].get(key) for key in _NETWORK_KEYS}
    network = render_network_inputs(network)
    network["_config"] = render_network_metadata(network["_config"])
    seed = {key: inputs["seed"].get(key) for key in _SEED_KEYS}
    digest = hashlib.sha256(json.dumps(global_data, sort_keys=True, ensure_ascii=False).encode())
    # Introducing/removing a temporal domain changes checkpoint prerequisites.
    # An edit inside an already active domain retains its prior legal exits.
    domains = tuple((str(clip.id), bool(getattr(clip, "light_tracking_enabled", False)),
                     float(getattr(clip, "motor_speed_limit_dps", 0.) or 0.) > 0.)
                    for clip in clips
                    if bool(getattr(clip, "light_tracking_enabled", False)) or
                    float(getattr(clip, "motor_speed_limit_dps", 0.) or 0.) > 0.)
    digest.update(pickle.dumps(domains, protocol=5))
    digest.update(pickle.dumps((network, seed, inputs["runtime"], inputs["interval"]), protocol=5))
    _digest_inactive_programs(digest, project, opts)
    check_cancel(opts)
    return {"global": digest.digest(), "clips": {
        str(clip.id): _clip_fingerprint(clip, opts) for clip in clips},
        "unsupported": _unsupported(project, clips)}


def dirty_start(previous, current):
    if current["unsupported"]:
        return 0., current["unsupported"]
    if previous is None:
        return 0., "first_generation"
    old = previous["inputs"]
    if old["global"] != current["global"]:
        return 0., "global_inputs_changed"
    changed = set(old["clips"]) | set(current["clips"])
    starts = []
    for key in changed:
        before, after = old["clips"].get(key), current["clips"].get(key)
        if before != after:
            starts.extend(value["start"] for value in (before, after) if value is not None)
    return (min(starts), "clip_inputs_changed") if starts else (math.inf, "unchanged_inputs")
