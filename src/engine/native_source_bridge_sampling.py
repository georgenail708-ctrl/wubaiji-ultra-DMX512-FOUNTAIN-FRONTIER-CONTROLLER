"""Public sampling metadata and exact CE clocks/curves, without new state."""
import numpy as np

from .native_source_bridge import modules


class NativeSourceMathError:
    def __init__(self, error):
        self.error = error


def nearest(engine, request):
    _, clock = modules()
    args = request["args"]
    table = args["compiled"]
    times = table.get("ftl")
    if times is None:
        times = table.get("frame_times", ())
    elif len(times) <= 1:
        times = np.zeros(len(times), np.float64)
    return clock.sample_nearest(times, args["source_t"])


def _hold_timestamp(frame):
    try:
        return float(getattr(frame, "timestamp", 0.) or 0.)
    except Exception:
        return 0.


def auto_hold(engine, request):
    _, clock = modules()
    args = request["args"]
    frames = getattr(args["clip"], "frames", None) or []
    times = [_hold_timestamp(frame) for frame in frames] if len(frames) > 1 else []
    return clock.auto_hold(times, args["local_t"])


def _transition_payload(engine, args):
    from qascade_ce.native_source_motion_types import CURVES, STRATEGIES
    clip, fade_in = args["clip"], args["fade_in"]
    enabled = engine._clip_transition_enabled(clip)
    curve = engine._clip_transition_curve(clip, fade_in=fade_in) if enabled else "easeinout"
    strategy = engine._clip_transition_strategy(clip, fade_in=fade_in) if enabled else "standard_fade"
    return dict(enabled=int(enabled), curve=CURVES.get(curve, 0),
        strategy=STRATEGIES.get(strategy, 0), fade_in=int(fade_in))


def _scalar_report(report):
    if report[1]:
        message = "cannot convert float NaN to integer" if report[1] == 2 else "math domain error"
        return NativeSourceMathError(ValueError(message))
    return float(report[0])


def scalar_curve(engine, request):
    _, clock = modules()
    args = request["args"]
    payload = _transition_payload(engine, args)
    payload["progress"] = args["progress"]
    return _scalar_report(clock.transition_scalar(payload))


def whole_fade(engine, request):
    motion, clock = modules()
    args = request["args"]
    clip = args["clip"]
    codes = motion.transition_codes(engine, clip)
    payload = dict(zip(("enabled", "curve_in", "curve_out", "strategy_in", "strategy_out"),
                       codes, strict=True))
    payload.update(duration=float(getattr(clip, "duration", 0.)),
        fade_in=float(getattr(clip, "fade_in", 0.) or 0.),
        fade_out=float(getattr(clip, "fade_out", 0.) or 0.), local_t=args["local_t"])
    return _scalar_report(clock.fade_report(payload))


def vector_curve(engine, request):
    _, clock = modules()
    args = request["args"]
    payload = _transition_payload(engine, args)
    payload.update(progress=args["tt"], lut=args["ease_lut"])
    result, error = clock.transition_vector(payload)
    if error is not None:
        index, rows = error
        message = f"index {index} is out of bounds for axis 0 with size {rows}"
        return NativeSourceMathError(IndexError(message))
    return result
