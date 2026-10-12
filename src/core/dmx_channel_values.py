"""Shared logical 8-bit to physical coarse/fine encoding.

The scale is the existing Programmer contract, including its full-scale endpoint.
Encoding a quantized Matrix value does not add fractional calculation precision.
"""
from __future__ import annotations


def motor_8bit_to_16bit_pair(value: float) -> tuple[int, int]:
    """Map a logical 0-255 motor position to DMX coarse/fine channels.

    Integer positions keep the coarse byte unchanged; fractional positions fill
    the fine byte: 128.5 -> (128, 128).
    """
    logical = max(0.0, min(255.0, float(value)))
    if logical >= 255.0:
        v16 = 65535
    else:
        v16 = int(round(logical * 256.0))
    v16 = max(0, min(65535, v16))
    return (v16 >> 8) & 0xFF, v16 & 0xFF
