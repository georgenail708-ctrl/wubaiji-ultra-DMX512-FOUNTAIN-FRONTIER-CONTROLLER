"""Public sampling uses the mounted native clock and original standalone math."""
import sys

import numpy as np


def endpoint(duration):
    clock = sys.modules.get("qascade_ce.native_source_motion_clock")
    if clock is not None and clock.available():
        return clock.sample_endpoint(duration)
    return max(0.0, duration - 1e-4)


def floor_index(timestamps, source_t):
    clock = sys.modules.get("qascade_ce.native_source_motion_clock")
    if clock is not None and clock.available():
        return clock.sample_floor(timestamps, source_t)
    return max(0, int(np.searchsorted(timestamps, max(0., float(source_t)) + 1e-9,
                                     side="right")) - 1)
