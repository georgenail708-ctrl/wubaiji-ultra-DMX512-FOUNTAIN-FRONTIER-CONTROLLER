"""Native scalar timeline derivation and the original cache identity keys."""
import numpy as np

from .native_source_bridge import modules


def _timestamp(frame):
    try:
        return float(getattr(frame, "timestamp", 0.) or 0.)
    except (ValueError, TypeError):
        return None


def recorded_cycle(engine, clip):
    _, clock = modules()
    key = engine._clip_cache_id(clip)
    cache = engine._clip_recorded_cycle_cache
    cached = cache.get(key)
    if cached is not None:
        return cached or None
    frames = getattr(clip, "frames", None) or []
    times = [_timestamp(frame) for frame in frames]
    times = [t for t in times if t is not None]
    output = clock.invoke(11, [len(times)], [], times)[0]
    cycle = (float(output[0]), float(output[1]), float(output[2])) if output[3] else None
    cache[key] = cycle
    return cycle


def span(engine, request):
    _, clock = modules()
    args, clip = request["args"], request["args"]["clip"]
    batches = engine._clip_sequential_batch_count(clip, args["groups"])
    pauses = engine._clip_sequential_pauses(clip, args["groups"], args["pause"])
    step = args["step"]
    if step is None:
        step = engine._clip_sequential_timing_detail(clip)[5]
        step = args["single"] if step is None else step
    return float(clock.invoke(10, [len(pauses), batches], [step, args["single"]], pauses)[0][0])


def group_timing(engine, request):
    _, clock = modules()
    enabled, groups, pauses, single, batches, step = engine._clip_sequential_timing_detail(request["args"]["clip"])
    values = clock.invoke(10, [len(pauses), batches], [single if step is None else step, single], pauses)[0]
    return enabled, groups, float(values[1]), single


def completed_batch(engine, request):
    _, clock = modules()
    args = request["args"]
    enabled, _, pauses, single, batches, step = engine._clip_sequential_timing_detail(args["clip"])
    if not enabled:
        return -1
    values = clock.invoke(2, [batches, len(pauses)],
                         [args["local_t"], single, single if step is None else step], pauses)[0]
    return int(values[3])


def recorded_span(engine, request):
    _, clock = modules()
    args, clip = request["args"], request["args"]["clip"]
    key = engine._clip_cache_id(clip)
    cache = engine._clip_recorded_source_span_cache
    previous = cache.get(key)
    times = []
    if previous is None:
        times = [_timestamp(frame) for frame in getattr(clip, "frames", []) or []]
        times = [time for time in times if time is not None]
    values = clock.invoke(13, [len(times), int(previous is not None)],
                         [0. if previous is None else previous, args["source_duration"]], times)[0]
    cache[key] = float(values[1])
    return float(values[0])
