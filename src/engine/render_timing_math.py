"""Render timing metadata and original state order, installed math owned by CE."""
import math
import sys


def _domain():
    return sys.modules.get("qascade_ce.native_render_timing")


def _execute(operation, flags, values):
    module = _domain()
    if module is not None and module.available():
        try:
            return module.invoke(operation, flags, values)
        except RuntimeError:
            pass
    if module is not None:
        module.note_fallback()
    return _COMPATIBILITY[operation](flags, values)


def render_now(value):
    return float(_execute(4, (), (float(value),))[0])


def render_cadence(engine, position):
    module = _domain()
    if module is not None and module.available():
        try:
            return module.cadence(engine, position)
        except RuntimeError:
            pass
    if module is not None:
        module.note_fallback()
    return _python_cadence(engine, position)


def active_bucket(now, size):
    result = _execute(5, (), (float(now), float(size)))
    semantic = int(result[23]) if len(result) > 23 else 0
    if semantic == 1:
        raise ValueError("cannot convert float NaN to integer")
    if semantic == 2:
        raise OverflowError("cannot convert float infinity to integer")
    if semantic == 3:
        raise ZeroDivisionError("float division by zero")
    return int(result[0])


def active_admitted(now, start, end):
    return bool(_execute(6, (), (float(now), float(start), float(end)))[0])


def _known_hold_key(clip, order):
    raw = getattr(clip, "id", "")
    memo = getattr(clip, "_clip_cache_id_memo", None)
    if memo is not None and raw is memo[0]:
        return f"{memo[1]}::{order}"
    if type(raw) is str and raw:
        return f"{raw}::{order}"
    return None


def active_local(engine, clip, context):
    now, start, duration, interval, mode, order = context
    key = _known_hold_key(clip, order) if mode != "none" else None
    latched = bool(engine._hold_boundary_latch.get(key, False)) if key is not None else False
    values = (float(now), float(start), float(duration), float(interval))
    result = _execute(0, (mode != "none", latched), values)
    action = int(result[2])
    if action != -1:
        actual_key = f"{engine._clip_cache_id(clip)}::{order}"
        actual_latch = bool(engine._hold_boundary_latch.get(actual_key, False))
        if actual_key != key and actual_latch != latched:
            result = _execute(0, (mode != "none", actual_latch), values)
            action = int(result[2])
        _write_latch(engine, actual_key, action)
    return float(result[0]), bool(result[1])


def _write_latch(engine, key, action):
    if action == 1:
        engine._hold_boundary_latch[key] = True
    elif action == 0:
        engine._hold_boundary_latch.pop(key, None)


def active_source(context):
    source, source_duration, local, duration, fade_in, hold, mode = context
    result = _execute(1, (hold, mode == "none", mode == "last_cycle"),
                      (float(source), float(source_duration), float(local), float(duration), float(fade_in)))
    return float(result[0]), bool(result[1]), float(result[2])


def held_source(context):
    now, start, duration, source_duration, rate, mode = context
    result = _execute(2, (mode == "last_cycle",),
                      (float(now), float(start), float(duration), float(source_duration), float(rate)))
    return float(result[1]), float(result[2]), float(result[3])


def clip_windows(clip, local, context):
    hold, seq, in_dir, out_dir, proj, light, span, phase, priority, speed = context
    flags = (bool(hold), bool(seq), in_dir != "none", out_dir != "none", proj is not None, bool(light))
    values = (float(local), float(getattr(clip, "fade_in", 0.) or 0.),
              float(getattr(clip, "fade_out", 0.) or 0.), float(getattr(clip, "duration", 0.)),
              float(getattr(clip, "seq_delay_in", 0.) or 0.), float(getattr(clip, "seq_delay_out", 0.) or 0.),
              float(span), float(phase), float(priority), float(speed))
    result = _execute(3, flags, values)
    return (*result[:5], *(bool(value) for value in result[5:13]), float(result[13]), bool(result[14]))


def _python_cadence(engine, position):
    try:
        interval = float(engine._timer.interval()) / 1000.
    except Exception:
        interval = 0.
    if interval < 1e-4:
        interval = .025
    previous = getattr(engine, "_last_render_time", None)
    if previous is not None:
        interval = _python_cadence_delta(interval, position, previous)
    return min(max(interval, .005), .08)


def _python_cadence_delta(interval, position, previous):
    try:
        delta = float(position) - float(previous)
    except Exception:
        delta = 0.
    return max(interval, delta) if 1e-4 <= delta <= .2 else interval


def _local_python(flags, values):
    now, start, duration, interval = values
    local, hold, action, enter, exit_ = now - start, False, -1, 0., 0.
    if flags[0] and duration > 0.:
        enter = min(duration * .25, max(1e-4, interval * 1.35))
        exit_ = min(duration * .25, max(1e-4, interval * 1.75))
        local, hold, action = _python_seam(local, duration, enter, exit_, flags[1])
    return local, hold, action, enter, exit_


def _python_seam(local, duration, enter, exit_, latched):
    if local >= duration - (exit_ if latched else enter):
        return max(0., duration - 1e-4), True, 1
    if latched and local >= duration - exit_:
        return max(0., duration - 1e-4), True, 2
    return local, False, 0


def _source_python(flags, values):
    source, source_duration, local, duration, fade_raw = values
    if flags[0]:
        return max(0., source_duration - 1e-4), False, 0.
    if not flags[2]:
        source = min(source, max(0., source_duration - 1e-4))
    fade = max(0., fade_raw)
    long_fade = bool(flags[1] and fade > duration + 1e-9)
    progress = min(1., max(0., local / fade)) if long_fade else 0.
    return source, long_fade, progress


def _held_python(flags, values):
    now, start, duration, source_duration, rate = values
    elapsed = max(0., now - (start + duration))
    source = max(0., source_duration - 1e-4 + elapsed * rate) if flags[0] else max(0., source_duration - 1e-4)
    return elapsed, source, duration, 1.


def _window_python(flags, values):
    local, fi, fo, dur, din, dout, span, phase, priority, speed = values
    fi, fo, dur, din, dout = (max(0., value) for value in (fi, fo, dur, din, dout))
    hold, seq, indir, outdir, project, light = flags
    real = bool(hold and local >= dur - 1e-9)
    is_in = bool(not hold and fi > 1e-4 and local < fi)
    is_out = _python_fade_out((hold, outdir), (local, dur, fo, dout))
    seq_in, delay_in = _python_input_windows((hold, seq, indir), (local, fi, din, span))
    motor = _python_motor((hold, seq, indir, project), phase)
    direction, transit = bool(is_in or seq_in or delay_in), bool(is_in or is_out or seq_in or delay_in)
    boost = (priority + 1.) * 1e9 if not hold and transit else 0.
    fast = _python_fast((is_in, is_out, seq_in, delay_in, motor, light), speed)
    return fi, fo, dur, din, dout, real, is_in, is_out, seq_in, delay_in, motor, direction, transit, boost, fast


def _python_fade_out(flags, values):
    hold, outdir = flags
    local, duration, fade, delay = values
    if hold or not duration > 0.:
        return False
    return bool((fade > 1e-4 and local > duration - fade) or
                (delay > 1e-4 and outdir and local > duration - (fade + delay)))


def _python_input_windows(flags, values):
    hold, seq, indir = flags
    local, fade, delay, span = values
    if hold or not indir:
        return False, False
    return bool(seq and local < max(1e-4, span)), bool(delay > 1e-4 and local < fade + delay)


def _python_motor(flags, phase):
    hold, seq, indir, project = flags
    return bool(not hold and not seq and phase > 1e-6 and indir and project)


def _python_fast(flags, speed):
    return not any(flags) and speed <= 1e-6


def _now_python(flags, values):
    return (max(0., values[0]),)


def _bucket_python(flags, values):
    return (math.floor(values[0] / values[1]),)


def _admission_python(flags, values):
    return (values[1] <= values[0] < values[2],)


_COMPATIBILITY = (_local_python, _source_python, _held_python, _window_python,
                  _now_python, _bucket_python, _admission_python)
