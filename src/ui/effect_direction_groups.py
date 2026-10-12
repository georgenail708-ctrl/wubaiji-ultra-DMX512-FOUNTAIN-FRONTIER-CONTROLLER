"""Adapt the kernel's existing shape blocks for the shared direction layer.

Logical units keep their existing direction contract. Without units, direction
uses the same spatial/project group ownership as the kernel, including overlap
priority and the trailing block of ungrouped fixtures.
"""
from src.utils.identity_cache import fixture_ids_signature


def direction_groups(engine, ordered, params):
    from .effect_engine import normalize_spatial_scope

    if normalize_spatial_scope(params.get("spatial_scope")) != "per_shape":
        return None
    units = params.get("_unit_groups")
    if isinstance(units, (list, tuple)) and units:
        return units
    blocks = engine._per_shape_unit_blocks(ordered, params)
    if blocks is None:
        return None
    return [[str(item[1].id) for item in block] for block in blocks]


def groups_signature(params):
    """Membership/order content matters even when container identity is stable."""
    from .effect_engine import _per_shape_group_member_lists

    return tuple(
        tuple(fixture_ids_signature(group) for group in
              _per_shape_group_member_lists(params.get(key)))
        for key in ("_unit_groups", "_output_spatial_groups", "_fixture_groups")
    )
