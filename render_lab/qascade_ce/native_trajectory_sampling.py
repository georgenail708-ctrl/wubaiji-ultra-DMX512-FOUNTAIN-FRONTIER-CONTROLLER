"""Sample rows preserve original ordering and selectors; C computes all math."""
from .native_trajectory import evaluate
from .native_trajectory_types import D, Result, Sample, Value, pointer, request, rows
from .native_trajectory_values import attach_pool, encode, raise_semantic


def sample(seq, position):
    if not seq:
        return None
    pool = bytearray()
    originals = [value for _, value in seq]
    timestamps = rows(D, [time for time, _ in seq])
    points = rows(Value, [encode(value, pool, i) for i, value in enumerate(originals)])
    samples = rows(Sample, [Sample(offset=0, count=len(seq), position=position)])
    results = rows(Result, [Result()])
    payload = request()
    payload.samples, payload.sample_count = pointer(samples, Sample), 1
    payload.timestamps, payload.points = pointer(timestamps, D), pointer(points, Value)
    payload.point_count, payload.results = len(seq), pointer(results, Result)
    integers = attach_pool(payload, pool)
    evaluate(1, payload, (seq, originals, timestamps, points, samples, results, integers))
    result = results[0]
    raise_semantic(result.semantic)
    if result.kind == 1:
        return originals[result.selector]
    return result.number if result.kind == 2 else None
