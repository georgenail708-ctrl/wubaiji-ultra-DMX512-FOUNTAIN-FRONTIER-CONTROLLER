"""Bounded axis aggregation; retain the original downsample reductions exactly."""
import math

import numpy as np


def downsample_audio_axis(arr, count):
    if arr.size == count:
        # Singleton mean starts its sum at +0; retain signed-zero float32 bytes.
        return np.add(arr, np.float32(0.0), dtype=np.float32)
    edges = np.linspace(0.0, float(arr.size), count + 1, dtype=np.float32)
    out = np.zeros(count, dtype=np.float32)
    for i in range(count):
        start = int(math.floor(float(edges[i])))
        end = int(math.floor(float(edges[i + 1])))
        if end <= start:
            idx = min(arr.size - 1, start)
            out[i] = float(arr[idx])
        else:
            out[i] = float(np.mean(arr[start:min(end, arr.size)]))
    return out
