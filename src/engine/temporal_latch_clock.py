"""Research-only grid latch exits using original CE timing, before table walk.

The original canonical owned pass starts with empty latches. Admitted samples
are monotonic, so each unique hold key changes from absent to True once. Locate
that sample with original active_local/native range_position, not copied seam
formulae. Foreign methods/nonordinary clips/nonzero origins are rejected.
Real timestamp and actual live-history parity remain mandatory admission tests.
"""
from __future__ import annotations

from bisect import bisect_right
from types import SimpleNamespace


def _canonical_source(engine, tracks):
    from qascade_ce.prepared_prepass import eligible, _METHODS
    from qascade_ce.prepared_prepass_guard import default_methods
    from qascade_ce import native_source_motion_clock, native_render_timing
    return (eligible(engine, tracks) and default_methods(engine, _METHODS)
            and native_source_motion_clock.available() and native_render_timing.available())


def _sample(index, interval, end):
    from qascade_ce import native_source_motion_clock
    return native_source_motion_clock.range_position(index, interval, end)


def _first_at_or_after(count, interval, end, start):
    low, high = 0, count
    while low < high:
        mid = (low + high) // 2
        if _sample(mid, interval, end) < start:
            low = mid + 1
        else:
            high = mid
    return low


def _probe(engine, item, index, *, interval, end):
    from src.engine import render_timing_math as timing
    origin, order, _track, clip, duration, mode, _universes = item
    position = _sample(index, interval, end)
    previous = None if index == 0 else _sample(index - 1, interval, end)
    owner = SimpleNamespace(_hold_boundary_latch={}, _clip_cache_id=engine._clip_cache_id,
                            _timer=engine._timer, _last_render_time=previous)
    timing.active_local(owner, clip,
        (position, origin, duration, timing.render_cadence(owner, position), mode, order))
    return owner._hold_boundary_latch


def _first_latch(engine, event, count, *, interval, end):
    from qascade_ce.prepared_prepass import _last_sample
    start, stop, item = event
    low = _first_at_or_after(count, interval, end, start)
    high = _last_sample(count, interval, end, stop)
    if low > high:
        return None
    final = _probe(engine, item, high, interval=interval, end=end)
    if not final:
        return None
    while low < high:
        mid = (low + high) // 2
        if _probe(engine, item, mid, interval=interval, end=end):
            high = mid
        else:
            low = mid + 1
    return low, tuple(final)


def _admitted_events(engine, tracks):
    structure = engine._render_struct_for_tracks(tracks)
    ordered, by_id = structure["ordered_tracks"], structure["track_by_id"]
    active = engine._playback_index_for_tracks(tracks, ordered)["active_events"]
    for event in active:
        _start, _stop, item = event
        _origin, _order, track, _clip, _duration, mode, _universes = item
        if mode == "none" or engine._track_or_ancestor_muted(track, by_id):
            continue
        if structure["any_solo"] and not engine._track_or_ancestor_soloed(track, by_id):
            continue
        yield event


class LatchTimeline:
    """Pure event schedule; construction only on the original owned thread."""

    def __init__(self, events):
        self.events = tuple(sorted(events))
        self.indices = tuple(index for index, _keys in self.events)
        self.head, self.consumed, self.last = {}, 0, -1

    @classmethod
    def from_owned_zero(cls, engine, tracks, *, count, interval, end, opts):
        if not _canonical_source(engine, tracks):
            return None
        from src.engine.timeline_bake_inputs import check_cancel
        events = []
        for event in _admitted_events(engine, tracks):
            check_cancel(opts)
            change = _first_latch(engine, event, count, interval=interval, end=end)
            if change is not None:
                events.append(change)
        return cls(events)

    def at(self, index):
        """Read legal index only; earlier targets never receive a future seal."""
        stop = bisect_right(self.indices, index)
        if index < self.last:
            self.head, self.consumed = {}, 0
        for _first, keys in self.events[self.consumed:stop]:
            self.head.update((key, True) for key in keys)
        self.consumed, self.last = stop, index
        return self.head
