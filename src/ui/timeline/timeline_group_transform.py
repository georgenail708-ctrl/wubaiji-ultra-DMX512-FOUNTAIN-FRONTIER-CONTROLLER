"""R24 bounded group plans and the original Controller clipboard adapter.

Reference: Kdenlive v25.08.0 group-move/paste contracts (GPL, no copied code),
existing Clip deepcopy/R172 unbound process sheet and PM timeline snapshots.
Pure plans touch no frames, UI, player, cache or output. Evidence: r189-library-pixel.md.
"""
from copy import deepcopy
from dataclasses import dataclass
import math
import uuid


@dataclass(frozen=True)
class Member:
    clip: object
    track: object
    index: int
    start: float
    duration: float


def clip_tracks(session):
    return [track for track in session.tracks
            if not getattr(track, "is_folder", False) and not getattr(track, "is_audio_mod", False)]


def members(session, ids):
    wanted = set(ids)
    rows = [Member(clip, track, index, float(clip.start_time), float(clip.duration))
            for index, track in enumerate(clip_tracks(session))
            for clip in track.clips if clip.id in wanted]
    if {row.clip.id for row in rows} != wanted:
        return None
    if not all(math.isfinite(row.start) and math.isfinite(row.duration) for row in rows):
        return None
    return rows


def _index(tracks, track_id):
    return next((index for index, track in enumerate(tracks) if track.id == track_id), None)


def move_plan(rows, tracks, anchor_id, start, *, track_id=None):
    anchor = next((row for row in rows if row.clip.id == anchor_id), None)
    if anchor is None or not math.isfinite(start):
        return None
    target = anchor.index if track_id is None else _index(tracks, track_id)
    if target is None:
        return None
    offset = target - anchor.index
    delta = max(start - anchor.start, -min(row.start for row in rows))
    if any(row.index + offset < 0 or row.index + offset >= len(tracks) for row in rows):
        return None
    return [(row.clip, tracks[row.index + offset], row.start + delta) for row in rows]


def resize_plan(rows, anchor_id, requested):
    anchor = next((row for row in rows if row.clip.id == anchor_id), None)
    if anchor is None or not math.isfinite(requested):
        return None
    if any(bool(getattr(row.clip, "hold_last_frame", False)) for row in rows):
        return None
    delta = max(requested - anchor.duration, .1 - min(row.duration for row in rows))
    return [(row.clip, row.duration + delta) for row in rows]


def _copy_offsets(session, picked):
    tracks = clip_tracks(session)
    positions = {track.id: index for index, track in enumerate(tracks)}
    if any(track.id not in positions for track, _clip in picked):
        return None
    base = min(positions[track.id] for track, _clip in picked)
    return {clip.id: positions[track.id] - base for track, clip in picked}


def copy_to_clipboard(ctrl, ids, *, cut=False):
    picked = ctrl._collect_clips_for_clipboard(ids)
    if not picked:
        return False
    offsets = _copy_offsets(ctrl._session, picked)
    if offsets is None:
        return False
    base = min(float(clip.start_time) for _track, clip in picked)
    copied = [(float(clip.start_time) - base, deepcopy(clip)) for _track, clip in picked]
    if cut:
        ctrl._snapshot_for_undo()
    ctrl._clipboard = copied
    ctrl._clipboard_group_offsets = offsets
    ctrl._clipboard_group_owner = copied
    if cut:
        _cut_members(ctrl, picked)
    return True


def _cut_members(ctrl, picked):
    doomed = {id(clip) for _track, clip in picked}
    for track, _clip in picked:
        track.clips = [clip for clip in track.clips if id(clip) not in doomed]
    ctrl.tracks_changed.emit()


def _paste_destinations(ctrl, track_id, items):
    tracks = clip_tracks(ctrl._session)
    target = _index(tracks, track_id)
    if target is None:
        return None
    offsets = getattr(ctrl, "_clipboard_group_offsets", {})
    owned = getattr(ctrl, "_clipboard_group_owner", None) is ctrl._clipboard
    mapped = [target + int(offsets.get(clip.id, 0) if owned else 0) for _time, clip in items]
    if any(index < 0 or index >= len(tracks) for index in mapped):
        return None
    return [tracks[index] for index in mapped]


def _paste_copies(items, destinations, position):
    start = max(0.0, float(position))
    output = []
    for (offset, source), track in zip(items, destinations):
        clone = deepcopy(source)
        clone.id, clone.track_id = str(uuid.uuid4()), track.id
        clone.process_sheet_id = ""  # Original R172 contract: independent processing page.
        clone.start_time = start + float(offset)
        output.append((track, clone))
    return output


def paste_to_track(ctrl, track_id, position):
    items = ctrl._clipboard_items()
    if not items or not math.isfinite(float(position)):
        return False
    destinations = _paste_destinations(ctrl, track_id, items)
    if destinations is None:
        return False
    copies = _paste_copies(items, destinations, position)
    ctrl._snapshot_for_undo()
    for track, clone in copies:
        track.clips.append(clone)
    ctrl.tracks_changed.emit()
    return True
