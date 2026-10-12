"""Cold timing stages over the installed CE; original parse and errors remain."""
import math
import sys

PLAIN = (bool, int, float, str, bytes, type(None))


def timing():
    return sys.modules.get("qascade_ce.native_render_timing")


def note_unsupported():
    module = timing()
    if module is not None:
        module.COUNTERS["cold_unsupported"] += 1


def parse_number(value):
    if type(value) not in PLAIN:
        note_unsupported()
    return float(value or 0.0)


def _call(operation, flags, values):
    module = timing()
    if module is not None and module.available():
        try:
            return module.invoke(operation, flags, values)
        except RuntimeError:
            module.note_fallback()
    return _COMPATIBILITY[operation](flags, values)


def normalized_duration(value):
    return _call(4, (), (value,))[0]


def positive_duration(value):
    return bool(_call(6, (), (0.0, 0.0, value))[0])


def span(start, duration, fade, hold):
    result = _call(7, (hold,), (start, duration, fade))
    return result[0], result[1]


def _bucket_error(semantic):
    if semantic == 1:
        raise ValueError("cannot convert float NaN to integer")
    if semantic == 2:
        raise OverflowError("cannot convert float infinity to integer")
    if semantic == 3:
        raise ZeroDivisionError("float division by zero")


def buckets(start, end, size):
    result = _call(8, (), (start, end, size))
    _bucket_error(result[23] if len(result) > 23 else 0)
    return int(result[0]), int(result[1])


def elapsed(last, now, interval):
    if not _elapsed_safe(last, now, interval):
        note_unsupported()
        return last < 0.0 or (now - last) >= interval
    return bool(_call(9, (), (float(last), float(now), float(interval)))[0])


def _elapsed_safe(last, now, interval):
    if not all(type(value) in (bool, int, float) for value in (last, now, interval)):
        return False
    if type(interval) is int and interval.bit_length() > 53:
        return False
    try:
        tuple(float(value) for value in (last, now, interval))
    except OverflowError:
        return False
    return True


def _normal_python(flags, values):
    return (max(0.0, values[0]),)


def _positive_python(flags, values):
    return (values[1] <= values[0] < values[2],)


def _span_python(flags, values):
    start, duration, fade = values
    base = start + duration
    end = base
    if not flags[0]:
        fade = max(0.0, fade)
        if fade > duration + 1e-9:
            end = start + fade
    return end, base


def _buckets_python(flags, values):
    start, end, size = values
    return math.floor(start / size), math.floor(max(start, end - 1e-9) / size)


def _elapsed_python(flags, values):
    last, now, interval = values
    return (last < 0.0 or (now - last) >= interval,)


_COMPATIBILITY = {4: _normal_python, 6: _positive_python, 7: _span_python,
                  8: _buckets_python, 9: _elapsed_python}
