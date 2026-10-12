"""Optional presentation metadata; never supplies an output channel value.

Reference: existing FixtureInstance persistence and copy.deepcopy ownership.
Physical import consumes the already parsed source once, outside render ticks.
"""
from __future__ import annotations

from copy import deepcopy


def copy_preview_physical(value: object) -> dict:
    """Keep unknown dictionary fields; old/malformed optional blocks are empty."""
    if not isinstance(value, dict):
        return {}
    return deepcopy(value)
