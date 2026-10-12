"""Pure native transition math; public callers retain clocks and state order."""
import sys

import numpy as np


def _module():
    return sys.modules.get("qascade_ce.native_transition")


def _fallback(module):
    if module is not None:
        module.COUNTERS["fallbacks"] += 1


def blend_frame(raw, gain, base=None):
    module = _module()
    prepared = sys.modules.get("qascade_ce.prepared_timeline")
    native = getattr(prepared, "NATIVE", None)
    gains = np.full(512, gain, np.float32) if np.isscalar(gain) else np.asarray(gain, np.float32)
    if native is not None:
        row = dict(raw=np.asarray(raw, np.uint8), gains=gains,
                   active=np.zeros(512, np.uint8), changes=np.zeros(512, np.float32),
                   mode=int(base is not None), base=base)
        try:
            output = native.float_values([row])[0]
        except Exception:
            if module is not None:
                module.COUNTERS["errors"] += 1
        else:
            if module is not None:
                module.COUNTERS["blend_calls"] += 1
                module.COUNTERS["blend_values"] += 512
            return output
    _fallback(module)
    values = np.asarray(raw, np.float32)
    return values * gains if base is None else base + gains * (values - base)


def update_anchor(anchor, best, candidate, tie):
    data, active, scores = candidate
    module = _module()
    if getattr(module, "NATIVE", None) is not None:
        arrays = (np.ascontiguousarray(data, np.float32), np.ascontiguousarray(active, np.uint8),
                  np.ascontiguousarray(scores, np.float64))
        try:
            code = module.NATIVE(*(row.ctypes.data for row in arrays), float(tie),
                                 anchor.ctypes.data, best.ctypes.data)
            if code < 0:
                raise RuntimeError("Native transition anchor rejected its inputs")
        except Exception:
            module.COUNTERS["errors"] += 1
        else:
            module.COUNTERS["anchor_calls"] += 1
            module.COUNTERS["anchor_values"] += 512
            return code
    _fallback(module)
    values = scores + tie
    take = active & np.isfinite(values) & (values >= best - 1e-12)
    anchor[take], best[take] = data[take], values[take]
    return int(np.count_nonzero(take))
