"""Cache only derived group attribution; current bus reports retain ownership.

The original resolver remains authoritative. Cache lifetime is bounded by the
current snapshot; effect parameters and scene metadata are never cached here.
"""
from __future__ import annotations

from copy import copy


_GROUP_FIELDS = ("group_id", "group_name", "parent_group_id", "parent_group_name")


def _ids(values) -> tuple:
    return tuple(map(str, values or ()))


def _groups_key(groups) -> tuple:
    return tuple((str(group.get("id", "") or ""),
                  str(group.get("name", "") or ""),
                  _ids(group.get("fixture_ids")),
                  _ids(group.get("base_order_fixture_ids")))
                 for group in groups if isinstance(group, dict))


def _node_key(node) -> tuple:
    return (_ids(getattr(node, "fixture_ids", ())),
            tuple(str(getattr(node, field, "") or "") for field in _GROUP_FIELDS))


def resolve_cells_cached(owner, cells, groups, resolve_fn):
    """Reuse group fields on copies of the current nodes, without stale values."""
    if not groups:
        owner._group_resolution_cache = None
        return cells
    key = _groups_key(groups)
    previous = getattr(owner, "_group_resolution_cache", None)
    cached = previous[1] if previous is not None and previous[0] == key else {}
    current, result = {}, []
    for node in cells:
        resolved = copy(node)
        node_key = _node_key(node)
        fields = cached.get(node_key)
        if fields is None:
            resolve_fn(resolved, groups)
            fields = tuple(getattr(resolved, field, "") for field in _GROUP_FIELDS)
        else:
            for field, value in zip(_GROUP_FIELDS, fields):
                setattr(resolved, field, value)
        current[node_key] = fields
        result.append(resolved)
    owner._group_resolution_cache = (key, current)
    return result
