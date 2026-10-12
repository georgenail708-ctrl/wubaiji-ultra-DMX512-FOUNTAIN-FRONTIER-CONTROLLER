"""Matrix node contribution T, authoring S and exact scene stamp addresses.

Reuse the existing target-state parser, fixture table and compiled output maps.
No node identity, timer, transport, recorder or non-Matrix stamp policy changes.
"""
from __future__ import annotations

import numpy as np

from ...processing.dispatch.fixture_table import channel_addrs_for, dimmer_source_addrs, is_virtual_dimmer
from ..project_channels import split_scope
from .matrix_channel_targets import normalized_identity, resolve_output_target
from .matrix_target_state import restore_group_scope, target_ids_from_state


def _ids(value):
    return list(dict.fromkeys(str(fid) for fid in value if str(fid))) if isinstance(value, (list, tuple)) else []


def authoring_ids(node, state=None):
    extra = node.get("extra") or {}
    if isinstance(extra.get("matrix_authoring_fixture_ids"), (list, tuple)):
        return _ids(extra["matrix_authoring_fixture_ids"])
    saved = target_ids_from_state(state)
    return saved if saved is not None else _ids(node.get("fixture_ids", []))


def restore_node_authoring_scope(panel, node, state=None):
    saved = dict(state or {})
    saved["fixture_ids"] = authoring_ids(node, state)
    saved.setdefault("group_id", node.get("group_id") or node.get("group_key", ""))
    saved.setdefault("group_name", node.get("group_name", ""))
    restore_group_scope(panel, saved)


def recall_edit_state(node, state=None):
    """Clone the input and apply reliable authoring metadata to the one S key."""
    saved = dict(state or {})
    extra = node.get("extra") or {}
    if isinstance(extra.get("matrix_authoring_fixture_ids"), (list, tuple)):
        saved["fixture_ids"] = _ids(extra["matrix_authoring_fixture_ids"])
    return saved


def enrich_matrix_nodes(panel, nodes):
    identity = str(panel._ch_type or "")
    target = resolve_output_target(panel, identity)
    direct = getattr(panel, "_matrix_vector_maps", {}).get(target)
    if not direct or len(direct["fixture_idx"]) == 0:
        return []
    source_ids = [str(row["id"]) for row in panel._fixtures if row.get("id")]
    indices = set(int(index) for index in direct["fixture_idx"])
    order = panel._matrix_fixture_order
    contributing = [fid for fid in source_ids if order.get(fid) in indices]
    plain, _definition = split_scope(target)
    channels = [plain]
    if np.any(direct.get("byte_role", np.zeros(0)) == 2):
        channels.append(plain + "_fine")
    for node in nodes:
        node.fixture_ids = list(contributing)
        node.ch_types = list(channels)
        node.extra.update(matrix_channel_id=identity, matrix_authoring_fixture_ids=source_ids)
    return nodes


def _node_identity(node, state):
    extra = node.get("extra") or {}
    saved = state if isinstance(state, dict) else {}
    config = (saved.get("slideshow_engine") or {}).get("config") or {}
    identity = extra.get("matrix_channel_id") or saved.get("ch_type") or config.get("ch_type")
    channels = node.get("ch_types") or []
    return normalized_identity(identity or (channels[0] if channels else ""))


def _resolve_legacy_identity(info, selected, identity):
    plain, definition = split_scope(identity)
    if definition or not _is_typed(info, plain):
        return identity
    owners = {info[fid]["def_id"] for fid in selected
              if fid in info and channel_addrs_for(info[fid], plain)}
    if len(owners) != 1 or "" in owners:
        return ""
    return plain + "@" + next(iter(owners))


def _is_typed(info, plain):
    return plain.startswith("function_") or any(row.get("def_names", {}).get(plain) for row in info.values())


def matrix_scope_addresses(info, node, state=None):
    source = authoring_ids(node, state)
    identity = _resolve_legacy_identity(info, source, _node_identity(node, state))
    if not identity:
        return set()
    plain, definition = split_scope(identity)
    addresses = set()
    for fid in source:
        row = info.get(fid)
        if row is None or (definition and row["def_id"] != definition):
            continue
        addresses.update(_logical_addresses(row, plain))
    return addresses


def _logical_addresses(row, plain):
    coarse = channel_addrs_for(row, plain)
    if is_virtual_dimmer(row, plain):
        return set(dimmer_source_addrs(row))
    fine = [] if plain.endswith("_fine") else channel_addrs_for(row, plain + "_fine")
    return set(coarse) | set(fine[:len(coarse)])
