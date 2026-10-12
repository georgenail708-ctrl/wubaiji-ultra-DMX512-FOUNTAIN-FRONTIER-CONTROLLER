"""Window-normalized band RMS from an existing, already gained RFFT magnitude.

The one-sided periodogram scale follows SciPy 1.17.0 (BSD-3):
integrated power is sum(one_sided * abs(FFT)**2) / (N * sum(window**2)).
This adapter does not take gain, compute an FFT, normalize each frame to its
own peak, or create signal energy during silence. File, mic and CE use it.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np


FREQ_RANGES = ((20, 60), (60, 250), (250, 500), (500, 2000),
               (2000, 4000), (4000, 6000), (6000, 12000), (12000, 20000))


@lru_cache(maxsize=16)
def hann_window(size: int) -> np.ndarray:
    """Shared immutable analysis window; at most sixteen sizes are retained."""
    window = np.hanning(size)
    window.setflags(write=False)
    return window


@lru_cache(maxsize=32)
def _layout(rate: float, size: int):
    frequencies = np.fft.rfftfreq(size, 1.0 / rate)
    spans = tuple((int(np.searchsorted(frequencies, lo)),
                   int(np.searchsorted(frequencies, hi))) for lo, hi in FREQ_RANGES)
    denominator = float(size * np.sum(hann_window(size) ** 2))
    return spans, max(denominator, np.finfo(float).tiny)


def normalized_bands(spectrum, rate: float, size: int) -> np.ndarray:
    """Return eight band RMS values per input row, clipped only to 0..1.

Accept one magnitude spectrum or a batch with spectra on the final axis.
One-sided doubling excludes DC and, for even sample counts, Nyquist.
The input is never changed. Non-finite magnitudes contribute no energy.
"""
    magnitude = np.asarray(spectrum, dtype=np.float64)
    if size < 2 or rate <= 0 or magnitude.ndim not in (1, 2):
        raise ValueError("invalid audio spectrum dimensions or sample rate")
    if magnitude.shape[-1] != size // 2 + 1:
        raise ValueError("RFFT size does not match the analysis window")
    magnitude = np.where(np.isfinite(magnitude) & (magnitude >= 0), magnitude, 0.0)
    power = np.square(magnitude)
    power[..., 1:(-1 if size % 2 == 0 else None)] *= 2.0
    spans, denominator = _layout(float(rate), int(size))
    energy = np.stack([power[..., start:end].sum(axis=-1)
                       for start, end in spans], axis=-1)
    return np.clip(np.sqrt(energy / denominator), 0.0, 1.0)
