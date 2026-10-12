"""Ordered exact sparse addition and copied full512 completion ownership."""
import numpy as np

from .native_binding import borrowed_pointer
from .native_trajectory import evaluate
from .native_trajectory_types import B, Event, Time, TimeOut, pointer, request, rows
from .native_trajectory_values import (attach_pool, decode_integer, encode,
                                       output_pool, raise_semantic)


def time_value(clip, position, stages):
    payload = request()
    start = float(getattr(clip, "start_time", 0.) or 0.)
    duration = float(getattr(clip, "duration", 0.) or 0.)
    rate, source_duration = 0., 0.
    if stages & 2:
        rate = float(getattr(clip, "playback_rate", 1.) or 1.)
        source_duration = float(getattr(clip, "source_duration", 0.) or duration)
    times = rows(Time, [Time(start=start, duration=duration, rate=rate,
                             source_duration=source_duration, position=position,
                             stages=stages)])
    result = rows(TimeOut, [TimeOut()])
    payload.times, payload.time_count = pointer(times, Time), 1
    payload.time_out = pointer(result, TimeOut)
    evaluate(0, payload, (clip, times, result))
    return bool(result[0].active) if stages == 1 else result[0].source


def _events(frames):
    universes, items, pool = [], [], bytearray()
    table = {}
    for frame in frames:
        for universe, channels in frame.items():
            universe = int(universe)
            if universe not in table:
                table[universe] = len(universes)
                universes.append(universe)
            for offset, value in channels.items():
                items.append(Event(universe=table[universe], offset=offset,
                                   value=encode(int(value), pool)))
    return universes, rows(Event, items), pool


def combine(frames):
    universes, events, pool = _events(frames)
    if not events:
        return {universe: {} for frame in frames for universe in frame}
    payload = request()
    output_events = rows(Event, [Event() for _ in events])
    payload.events, payload.event_count = pointer(events, Event), len(events)
    payload.event_out, payload.event_capacity = pointer(output_events, Event), len(events)
    integers = attach_pool(payload, pool)
    largest = max(event.value.size for event in events)
    output = output_pool(payload, len(events) * (largest + 132) + 1)
    report = evaluate(3, payload, (frames, events, output_events, integers, output))
    combined = {universe: {} for universe in universes}
    for event in output_events[:report.event_count]:
        combined[universes[event.universe]][event.offset] = decode_integer(event.value, output)
    return combined


def _base(entry):
    if entry is None:
        return np.zeros(512, np.uint8), np.zeros(512, bool)
    return (np.array(entry[0], dtype=np.uint8, copy=True),
            np.array(entry[1], dtype=bool, copy=True))


def _merge_universe(entry, channels):
    values, active = _base(entry)
    _, events, pool = _events([{0: channels}])
    payload = request()
    payload.events, payload.event_count = pointer(events, Event), len(events)
    integers = attach_pool(payload, pool)
    payload.frame, payload.mask = borrowed_pointer(values, type(payload.frame)), borrowed_pointer(
        active, type(payload.mask))
    report = evaluate(4, payload, (entry, channels, events, integers, values, active))
    offending = list(channels.values())[report.index] if report.semantic else None
    raise_semantic(report.semantic, offending)
    return values, active


def merge(outputs, combined):
    for universe, channels in combined.items():
        outputs[universe] = _merge_universe(outputs.get(universe), channels)
