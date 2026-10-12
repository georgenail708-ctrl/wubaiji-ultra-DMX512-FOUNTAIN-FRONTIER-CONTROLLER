"""Cold P7 metadata/state order retained; scoped CE time/mask operations only."""
from . import native_playback_index_math as math
from .native_playback_index_masks import accumulate_hold, source


class Rows:
    def __init__(self):
        self.starts = []
        self.active = []
        self.held = []
        self.scopes = {}


def _clip_times(clip):
    try:
        start = math.parse_number(getattr(clip, "start_time", 0.0))
        duration = math.normalized_duration(math.parse_number(getattr(clip, "duration", 0.0)))
    except Exception:
        return None
    if not math.positive_duration(duration):
        return None
    return start, duration


def _clip_span(clip, times, mode):
    start, duration = times
    if type(mode) is not str:
        math.note_unsupported()
    is_none = bool(mode == "none")
    fade = math.parse_number(getattr(clip, "fade_in", 0.0)) if is_none else 0.0
    return math.span(start, duration, fade, not is_none)


def _append_clip(engine, order, track, clip, rows):
    times = _clip_times(clip)
    if times is None:
        return
    start, duration = times
    mode = engine._clip_hold_mode(clip)
    targets = engine._target_universes_for_clip(clip, track)
    item = (start, order, track, clip, duration, mode, targets)
    active_end, hold_start = _clip_span(clip, times, mode)
    rows.active.append((start, active_end, item))
    rows.starts.append(start)
    if mode != "none":
        rows.held.append((hold_start, item))
        accumulate_hold(engine, track, clip, targets, rows.scopes)


def _track_order(ordered):
    return {str(getattr(track, "id", "") or id(track)): int(order)
            for order, track in enumerate(ordered or [])}


def _collect(engine, ordered):
    rows, order_ids = Rows(), _track_order(ordered)
    for order, track in enumerate(ordered or []):
        if bool(getattr(track, "is_folder", False)):
            continue
        order = order_ids.get(str(getattr(track, "id", "") or id(track)), int(order))
        for clip in getattr(track, "clips", []) or []:
            _append_clip(engine, order, track, clip, rows)
    rows.active.sort(key=lambda item: (float(item[0]), float(item[1])))
    rows.held.sort(key=lambda item: (float(item[0]), int(item[1][1])))
    return rows


def _buckets(events, size):
    result = {}
    for event in events:
        start, end, _item = event
        first, last = math.buckets(float(start), float(end), size)
        for bucket in range(first, last + 1):
            result.setdefault(bucket, []).append(event)
    return result


def build_index(engine, tracks, ordered):
    rows = _collect(engine, ordered)
    bucket_size = 0.5
    buckets = _buckets(rows.active, bucket_size)
    return {"track_state": engine._track_state_signature(tracks),
            "clip_layout": engine._project_clip_layout_signature(ordered),
            "active_events": rows.active,
            "active_starts": [float(item[0]) for item in rows.active],
            "active_bucket_size": bucket_size,
            "active_buckets": buckets,
            "hold_events": rows.held,
            "hold_starts": [float(item[0]) for item in rows.held],
            "hold_scope_by_universe": rows.scopes}


def _monotonic():
    try:
        return source().time.monotonic()
    except Exception:
        return 0.0


def _warm_current(engine, index, tracks, ordered):
    if index.get("track_state") != engine._track_state_signature(tracks):
        return False
    now = _monotonic()
    last = engine._playback_index_check_time
    if math.elapsed(last, now, engine._playback_index_check_interval):
        engine._playback_index_check_time = now
        layout = engine._project_clip_layout_signature(ordered)
        if index.get("clip_layout") != layout:
            return False
    return True


def index_for_tracks(engine, tracks, ordered):
    index = engine._playback_active_index
    have_index = not engine._playback_active_index_dirty and isinstance(index, dict)
    if have_index and _warm_current(engine, index, tracks, ordered):
        return index
    index = engine._build_playback_active_index(tracks, ordered)
    engine._playback_active_index = index
    engine._playback_active_index_dirty = False
    engine._playback_index_check_time = _monotonic()
    return index
