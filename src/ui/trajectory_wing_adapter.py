"""Trajectory segment storage and execution use the same public wing phases."""
from ..utils.wing_block import build_wing_block_units


def from_storage(data):
    return dict(wings=int(data.get("wings", 1) or 1),
                block=int(data.get("block", 1) or 1),
                wing_mirror=bool(data.get("wing_mirror", False)),
                wing_reverse=bool(data.get("wing_reverse", False)))


def units(items, segment):
    return build_wing_block_units(items, wings=max(1, segment.wings),
        block_size=max(1, segment.block), direction="low_to_high",
        mirror=segment.wing_mirror, reverse=segment.wing_reverse)
