"""Original control trajectories at the live/cache output completion boundary.

The baked frame remains an immutable base. Release and current track audibility
are live inputs, shared by both playback routes. Reuses the original appliers,
release registry, track structure and output owner; no independent renderer.
Reference: R192-P01 audit; Kdenlive v25.08.0 preview/overlay ownership (no copy).
"""
from __future__ import annotations

import logging

import numpy as np

from ..core.project_frame_origin import frame_project
from ..processing.dispatch import get_release_registry
from ..processing.dispatch.trajectory_replay import build_clip_traj_frame
from .native_trajectory_overlay import combined_frame, domain

log = logging.getLogger(__name__)


def _in_span(clip, position):
    if not getattr(clip, "trajectories", None):
        return False
    native = domain()
    if native is not None:
        return native.span(clip, position)
    start = float(getattr(clip, "start_time", 0.) or 0.)
    duration = float(getattr(clip, "duration", 0.) or 0.)
    return duration > 0. and start <= position < start + duration


def _allowed(engine, track, structure):
    by_id = structure["track_by_id"]
    if bool(getattr(track, "is_folder", False)):
        return False
    if engine._track_or_ancestor_muted(track, by_id):
        return False
    return not structure["any_solo"] or engine._track_or_ancestor_soloed(track, by_id)


def _active_clips(engine, project, position):
    tracks = getattr(project, "tracks", ()) or ()
    candidates = [(track, clip) for track in tracks for clip in track.clips or ()
                  if _in_span(clip, position)]
    if not candidates:
        return []
    structure = engine._render_struct_for_tracks(tracks)
    return [clip for track, clip in candidates if _allowed(engine, track, structure)]


def _fixture_maps(engine, project):
    from .playback_engine import _stable_project_fixtures
    fixtures = _stable_project_fixtures(engine, "_traj_fixtures_cache", project)
    definitions = {str(item.id): item for item in project.fixture_defs or ()}
    types, universes, starts = {}, {}, {}
    for fixture in fixtures:
        fixture_id = str(fixture.id)
        definition = definitions.get(str(fixture.def_id))
        if definition is not None:
            types[fixture_id] = [str(value) for value in definition.channels or ()]
        universes[fixture_id] = int(fixture.universe or 1)
        starts[fixture_id] = int(fixture.start_channel or 1)
    return types, universes, starts


def _source_time(clip, position):
    native = domain()
    if native is not None:
        return native.source(clip, position)
    start = float(getattr(clip, "start_time", 0.) or 0.)
    duration = float(getattr(clip, "duration", 0.) or 0.)
    rate = max(.001, float(getattr(clip, "playback_rate", 1.) or 1.))
    source_duration = max(.05, float(getattr(clip, "source_duration", 0.) or duration))
    return ((position - start) * rate) % source_duration


def _combined_frame(clips, position, mappings):
    native = domain()
    if native is not None and native.accepts_clips(clips):
        return combined_frame(clips, position, mappings)
    registry = get_release_registry()
    combined = {}
    for clip in clips:
        released = registry.released_for_clip(str(clip.id))
        frame = build_clip_traj_frame(clip, _source_time(clip, position), *mappings, released)
        for universe, channels in frame.items():
            destination = combined.setdefault(int(universe), {})
            for offset, value in channels.items():
                destination[offset] = min(255, int(destination.get(offset, 0)) + int(value))
    return combined


def _merge_frame(outputs, combined):
    native = domain()
    if native is not None:
        return native.merge(outputs, combined)
    for universe, channels in combined.items():
        entry = outputs.get(universe)
        if entry is None:
            values, active = np.zeros(512, np.uint8), np.zeros(512, bool)
        else:
            values, active = (np.array(entry[0], dtype=np.uint8, copy=True),
                              np.array(entry[1], dtype=bool, copy=True))
        offsets = list(channels)
        values[offsets] = np.fromiter(channels.values(), dtype=np.uint8, count=len(offsets))
        active[offsets] = True
        outputs[universe] = values, active


def apply_trajectory_overlay(engine, outputs, position):
    project = frame_project(engine)
    if project is None:
        return
    clips = _active_clips(engine, project, float(position))
    if clips:
        _merge_frame(outputs, _combined_frame(clips, float(position), _fixture_maps(engine, project)))


def finish_realtime_frame(engine, outputs, record_outputs, layer_name, position):
    """Retain original record-source semantics; runtime trajectories remain live."""
    engine._last_record_source_outputs_by_universe = record_outputs
    if layer_name != "clip_playback" or engine._timeline_render_cache_building_lock:
        return
    try:
        engine._maybe_apply_clip_traj_overlay(outputs, float(position))
    except Exception:
        # ★ R193/P2-02：首错全栈告警+计数——坏配接此前会每帧刷 traceback
        if not getattr(engine, "_traj_overlay_err_logged", False):
            engine._traj_overlay_err_logged = True
            log.exception("clip_traj overlay failed（首次告警；同类只计数）")
        engine._traj_overlay_err_count = getattr(engine, "_traj_overlay_err_count", 0) + 1
