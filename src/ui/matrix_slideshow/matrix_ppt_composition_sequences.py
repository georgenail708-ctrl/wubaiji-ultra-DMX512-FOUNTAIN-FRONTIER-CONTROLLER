"""PPT-M03 finite editable compositions and backward-compatible option adapter.

Reference: Collection independent member functions and ChaserStep timing,
Apache-2.0; reviewed sources and prototype comparisons: r189-library-ppt.md.
Reuse MatrixSlide/wing units/gray frame factory, with no new runtime clock.
"""
from __future__ import annotations

import math

from ...utils.wing_block import build_wing_block_units

DUAL_CLOCK_ID = "r189_dual_clock"
OVERLAP_WEAVE_ID = "r189_overlap_weave"
EUCLIDEAN_PHRASE_ID = "r189_euclidean_phrase"
DAMPED_BOUNCE_ID = "r189_damped_bounce"

SEQUENCE_OPTION_DEFAULTS = {"clock_ratio": 2, "meter_steps": 16, "rhythm_pulses": 5, "damping": .75}
SEQUENCE_OPTION_FIELDS = {
    DUAL_CLOCK_ID: ("clock_ratio",), OVERLAP_WEAVE_ID: (),
    EUCLIDEAN_PHRASE_ID: ("meter_steps", "rhythm_pulses"), DAMPED_BOUNCE_ID: ("damping",),
}


def _bounded_number(value, default, low, high, *, integer=False):
    try:
        number = float(value) if not isinstance(value, bool) else float(default)
    except (TypeError, ValueError, OverflowError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    number = min(high, max(low, number))
    return int(number) if integer else round(number, 3)


def normalize_sequence_options(raw=None) -> dict:
    values = raw if isinstance(raw, dict) else {}
    steps = _bounded_number(values.get("meter_steps", 16), 16, 8, 32, integer=True)
    return {"clock_ratio": _bounded_number(values.get("clock_ratio", 2), 2, 2, 4, integer=True),
            "meter_steps": steps,
            "rhythm_pulses": _bounded_number(values.get("rhythm_pulses", 5), 5, 1, steps, integer=True),
            "damping": _bounded_number(values.get("damping", .75), .75, .5, .9)}


def consumed_sequence_options(key, raw=None) -> dict:
    values = normalize_sequence_options(raw)
    return {field: values[field] for field in SEQUENCE_OPTION_FIELDS.get(key, ())}


def _composition_frame(mk, options, units, levels, name, *, hold_ratio=1.0, trans_ratio=1.0):
    from .matrix_ppt_detail_sequences import _gray_frame
    return _gray_frame(mk, options, units, levels, name,
                       hold_ratio=hold_ratio, trans_ratio=trans_ratio)


def _composition_units(options):
    from .matrix_ppt_detail_sequences import _tail_width
    units = build_wing_block_units(*options["order"])
    return units, _tail_width(len(units), options["density"])


def _dual_clock(mk, options):
    units, width = _composition_units(options)
    n, radius = len(units), max(1, width / 2)
    ratio = normalize_sequence_options(options.get("sequence_options"))["clock_ratio"]
    slides = []
    for step in range(n):
        heads = (step, (n // 2 + ratio * step) % n)
        levels = _dual_peak_levels(n, heads, radius)
        slides.append(_composition_frame(mk, options, units, levels, f"双速交汇 {step + 1}"))
    return slides


def _dual_peak_levels(count, heads, radius):
    levels = []
    for index in range(count):
        distances = [min((index - head) % count, (head - index) % count) for head in heads]
        peaks = [amplitude * (.5 + .5 * math.cos(math.pi * distance / radius))
                 if distance < radius else 0 for amplitude, distance in zip((.95, .70), distances)]
        levels.append(max(peaks))
    return levels


def _overlap_weave(mk, options):
    units, width = _composition_units(options)
    n = len(units)
    starts = (0, n // 3, 2 * n // 3)
    slides = []
    for step in range(12):
        envelopes = [amplitude * (.5 + .5 * math.cos(2 * math.pi * (step - offset) / 12))
                     for amplitude, offset in zip((.8, .6, .4), (0, 4, 8))]
        levels = [min(1.0, sum(level for start, level in zip(starts, envelopes)
                              if (index - start) % n < width)) for index in range(n)]
        slides.append(_composition_frame(mk, options, units, levels, f"三层交叠 {step + 1}"))
    return slides


def _euclidean_phrase(mk, options):
    units, width = _composition_units(options)
    values = normalize_sequence_options(options.get("sequence_options"))
    steps, pulses = values["meter_steps"], values["rhythm_pulses"]
    n, hit, slides = len(units), 0, []
    for step in range(steps):
        if (step + 1) * pulses // steps > step * pulses // steps:
            amplitude = (1.0, .55, .7)[hit % 3]
            levels = [amplitude if (index - hit) % n < width else 0 for index in range(n)]
            hit += 1
        else:
            levels = [0] * n
        slides.append(_composition_frame(mk, options, units, levels, f"均布切分 {step + 1}",
                                          trans_ratio=.5))
    return slides


def _damped_bounce(mk, options):
    units, width = _composition_units(options)
    damping = normalize_sequence_options(options.get("sequence_options"))["damping"]
    n, slides = len(units), []
    for step in range(8):
        amplitude = damping ** step
        center = round((n - 1) * (.5 + (-1) ** step * .5 * amplitude))
        levels = [amplitude if abs(index - center) <= width / 2 else 0 for index in range(n)]
        slides.append(_composition_frame(mk, options, units, levels, f"弹落 {step + 1}",
                                          hold_ratio=.25, trans_ratio=.25))
        slides.append(_composition_frame(mk, options, units, [0] * n, f"间歇 {step + 1}",
                                          hold_ratio=1 / amplitude, trans_ratio=.25))
    return slides


COMPOSITION_SEQUENCE_GENERATORS = {
    DUAL_CLOCK_ID: _dual_clock, OVERLAP_WEAVE_ID: _overlap_weave,
    EUCLIDEAN_PHRASE_ID: _euclidean_phrase, DAMPED_BOUNCE_ID: _damped_bounce,
}


def load_sequence_frames(engine, key, *, hold, trans, wings, block_size,
                         wing_direction, density, sequence_options,
                         wing_mirror=False, wing_reverse=False):
    """Reuse the original loader's frame generation and transport-reset semantics."""
    from .matrix_slide_engine import BuiltinSequenceGenerator
    engine.slides = BuiltinSequenceGenerator.generate(
        key, engine._sorted_ids, hold, trans, wings=wings, block_size=block_size,
        wing_direction=wing_direction, density=density, sequence_options=sequence_options,
        wing_mirror=wing_mirror, wing_reverse=wing_reverse)
    engine._current_idx = 0
    engine._frame_elapsed = 0.0
    engine._in_transition = False
