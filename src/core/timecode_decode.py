# SPDX-License-Identifier: MIT
"""ArtTimeCode validation and zero-based physical seconds, without Qt state.

Frame-number formula adapted from timecode 1.4.1, tc_to_frames:299-306:
https://github.com/eoyilmaz/timecode/blob/34f8d4efd918f692c876b5ab042cb4758b28a602/timecode/__init__.py
Type/ranges: Art-Net V1.4 Rev1.4dp, ArtTimeCode p54-55.
https://art-net.org.uk/downloads/art-net.pdf
Project FPS remains the editing grid; it is not an input-decoder argument.

The MIT License (MIT)

Copyright (c) 2014 Joshua Banton and PyTimeCode developers

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""
from __future__ import annotations

_FRAME_LIMITS = (24, 25, 30, 30)


def _valid_ranges(hours: int, minutes: int, seconds: int, frames: int, tc_type: int) -> bool:
    if not 0 <= tc_type < len(_FRAME_LIMITS):
        return False
    limits = (24, 60, 60, _FRAME_LIMITS[tc_type])
    return all(0 <= value < limit for value, limit in zip((hours, minutes, seconds, frames), limits))


def is_valid_artnet_timecode(hours: int, minutes: int, seconds: int, frames: int, tc_type: int) -> bool:
    """Accept wire integers and valid labels; never coerce fractional values."""
    fields = (hours, minutes, seconds, frames, tc_type)
    if not all(type(value) is int for value in fields):
        return False
    if not _valid_ranges(hours, minutes, seconds, frames, tc_type):
        return False
    # DF skips ;00 and ;01 at every minute except multiples of ten.
    return not (tc_type == 2 and seconds == 0 and minutes % 10 != 0 and frames < 2)


def artnet_timecode_seconds(hours: int, minutes: int, seconds: int, frames: int, tc_type: int) -> float | None:
    """Return elapsed source seconds from 00:00:00:00, or None for invalid input."""
    if not is_valid_artnet_timecode(hours, minutes, seconds, frames, tc_type):
        return None
    nominal_fps = _FRAME_LIMITS[tc_type]
    frame_index = ((hours * 60 + minutes) * 60 + seconds) * nominal_fps + frames
    if tc_type == 2:
        total_minutes = hours * 60 + minutes
        frame_index -= 2 * (total_minutes - total_minutes // 10)
        return frame_index * 1001 / 30000
    return frame_index / nominal_fps
