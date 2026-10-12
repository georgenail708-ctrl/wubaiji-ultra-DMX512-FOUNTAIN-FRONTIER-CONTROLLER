"""R32 finite sequence adapters using existing MatrixSlide storage/output.

Reference: stripes/fillfromcenter/waves parameterized frame generators and
ChaserStep per-step durations, Apache-2.0. Local adapters reuse MatrixSlide and
wing units for editable gray compositions; evidence: r189-library-ppt.md.
"""
from __future__ import annotations

import math

from ...utils.wing_block import build_wing_block_units
from .matrix_ppt_composition_sequences import COMPOSITION_SEQUENCE_GENERATORS

GRAY_TRAIL_SEQUENCE_ID = "r189_gray_trail"
SOFT_PEAK_SEQUENCE_ID = "r189_soft_peak"
GATHER_RELEASE_SEQUENCE_ID = "r189_gather_release"
ECHO_PHRASE_SEQUENCE_ID = "r189_echo_phrase"


def _tail_width(count: int, density) -> int:
    value = float(density)
    if not math.isfinite(value):
        raise ValueError("尾迹密度必须是有限数值")
    return max(1, min(count, int(round(count * min(1.0, max(0.02, value))))))


def _gray_frame(mk, options, units, levels, name, *, hold_ratio=1.0, trans_ratio=1.0):
    slide = mk(name, [], options["hold"] * hold_ratio, options["trans"] * trans_ratio, "fade")
    for unit, level in zip(units, levels):
        gray = int(round(255 * max(0.0, min(1.0, level))))
        for physical_id in unit:
            slide.states[physical_id] = gray
    return slide


def _gray_trail(mk, options):
    units = build_wing_block_units(*options["order"])
    width = _tail_width(len(units), options["density"])
    slides = []
    for head in range(len(units)):
        levels = [max(0.0, 1.0 - ((head - i) % len(units)) / width)
                  for i in range(len(units))]
        slides.append(_gray_frame(mk, options, units, levels, f"灰度余辉 {head + 1}"))
    return slides


def _soft_peak(mk, options):
    units = build_wing_block_units(*options["order"])
    n = len(units)
    radius = max(1.0, _tail_width(n, options["density"]) / 2)
    slides = []
    for head in range(n):
        distances = [min((head - i) % n, (i - head) % n) for i in range(n)]
        levels = [.5 + .5 * math.cos(math.pi * d / radius) if d < radius else 0.0
                  for d in distances]
        slides.append(_gray_frame(mk, options, units, levels, f"柔峰推进 {head + 1}"))
    return slides


def _gather_release(mk, options):
    units = build_wing_block_units(*options["order"])
    n = len(units)
    width = _tail_width(n, options["density"])
    slides = []
    for head in range(n + width - 1):
        levels = [max(0.0, min(1.0, (head - i + 1) / width)) for i in range(n)]
        slides.append(_gray_frame(mk, options, units, levels, f"蓄势 {head + 1}",
                                   hold_ratio=.4, trans_ratio=.5))
    for label, level, ratio in (("齐放", 1.0, 2.0), ("余音", .25, .4), ("留白", 0.0, 1.0)):
        slides.append(_gray_frame(mk, options, units, [level] * n, label, hold_ratio=ratio))
    return slides


def _echo_phrase(mk, options):
    units = build_wing_block_units(*options["order"])
    n = len(units)
    split = (n + 1) // 2
    sector = _tail_width(split, options["density"])
    phrases = ((1.0, 0.0, 1.0), (.45, 0.0, .5), (.8, .55, 1.0), (0.0, .25, .5),
               (0.0, .44, 1.0), (.65, .65, 2.0), (0.0, 0.0, 1.0))
    slides = []
    for index, (left, right, ratio) in enumerate(phrases):
        levels = _echo_zone_levels(n, sector, index, (left, right))
        slides.append(_gray_frame(mk, options, units, levels, f"回声对答 {index + 1}",
                                   hold_ratio=ratio))
    return slides


def _echo_zone_levels(count, sector, step, amplitudes):
    """Each accent moves one logical block within its own region."""
    split = (count + 1) // 2
    left, right = amplitudes
    first = [left if (i - step) % split < sector else 0.0 for i in range(split)]
    remainder = count - split
    second = [right if (i - step) % remainder < min(sector, remainder) else 0.0
              for i in range(remainder)]
    return first + second


DETAIL_SEQUENCE_GENERATORS = {
    GRAY_TRAIL_SEQUENCE_ID: _gray_trail, SOFT_PEAK_SEQUENCE_ID: _soft_peak,
    GATHER_RELEASE_SEQUENCE_ID: _gather_release, ECHO_PHRASE_SEQUENCE_ID: _echo_phrase,
    **COMPOSITION_SEQUENCE_GENERATORS,
}


def generate_detail_sequence(preset_name, fids, mk, options):
    if preset_name == "间隔追逐":
        return [mk(f"间隔追{i + 1}", fids[i % 2::2], options["hold"], options["trans"])
                for i in range(len(fids))]
    generator = DETAIL_SEQUENCE_GENERATORS.get(preset_name)
    return generator(mk, options) if generator is not None else []
