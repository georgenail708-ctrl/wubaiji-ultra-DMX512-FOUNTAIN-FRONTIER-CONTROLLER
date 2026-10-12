"""Compile Matrix's exact fixture scope into typed, paired DMX address maps.

No transport, timer or UI ownership lives here. Public project channel identities
and fixture-table address normalization are reused. Compilation runs when the
existing fixture cache rebuilds; the output path only indexes compiled arrays.
"""
from __future__ import annotations

import numpy as np

from ...core.dmx_channel_values import motor_8bit_to_16bit_pair
from ...processing.dispatch.fixture_table import build_fixture_info, channel_addrs_for
from ..project_channels import (
    channel_key, collect_channel_items, defs_of, make_scoped, split_scope,
)
from .matrix_channel_profiles import (
    compile_profile_maps, profile_map_values, scalar_profile_value,
)

_FINE_BYTES = np.asarray([motor_8bit_to_16bit_pair(i)[1] for i in range(256)], dtype=np.uint8)


def normalized_identity(value) -> str:
    """Normalize the channel key while preserving the exact definition ID."""
    plain, definition = split_scope(getattr(value, "value", value))
    return make_scoped(channel_key(plain), definition)


def _fixture_info(fixtures, definitions):
    defs = {str(getattr(fd, "id", "")): fd for fd in definitions}
    info = build_fixture_info(fixtures, defs)
    # Independent engines may have complete fixture channels but no project.
    for fixture in fixtures:
        definition = str(fixture.get("def_id", "") or "")
        if definition not in defs:
            fallback = {definition: {"channels": fixture.get("channels", [])}}
            info.update(build_fixture_info([fixture], fallback))
    return info


def _paired_addresses(info, key):
    coarse = channel_addrs_for(info, key)
    fine = [] if key.endswith("_fine") else channel_addrs_for(info, key + "_fine")
    rows = []
    for index, (universe, address) in enumerate(coarse):
        paired = index < len(fine)
        rows.append((universe, address, int(paired)))
        if paired:
            rows.append((*fine[index], 2))
    return rows


def _physical_maps(info):
    rows, fixture_maps = {}, {}
    for fid, fixture in info.items():
        fmap = {}
        for key in fixture["channel_addrs"]:
            addresses = _paired_addresses(fixture, key)
            scoped = make_scoped(key, fixture["def_id"])
            fmap[key] = addresses
            fmap[scoped] = addresses
            rows.setdefault(key, []).extend((fid, *address) for address in addresses)
            if scoped != key:
                rows.setdefault(scoped, []).extend((fid, *address) for address in addresses)
        fixture_maps[fid] = fmap
    return rows, fixture_maps


def _universe_groups(universes, addresses):
    groups = []
    for universe in np.unique(universes):
        indices = np.flatnonzero(universes == int(universe)).astype(np.int32, copy=False)
        groups.append((int(universe), indices, addresses[indices]))
    return groups


def _vector_map(rows, fixture_order):
    rows = [row for row in rows if row[0] in fixture_order]
    universes = np.asarray([row[1] for row in rows], dtype=np.int32)
    addresses = np.asarray([row[2] for row in rows], dtype=np.int32)
    return {
        "fixture_idx": np.asarray([fixture_order[row[0]] for row in rows], dtype=np.int32),
        "universe": universes,
        "addr": addresses,
        "byte_role": np.asarray([row[3] for row in rows], dtype=np.uint8),
        "universe_groups": _universe_groups(universes, addresses),
        "last_values": np.full(len(rows), 256, dtype=np.uint16),
    }


def _take_map(direct_map, mask):
    result = {}
    for key in ("fixture_idx", "universe", "addr", "base_values", "color_channel", "byte_role"):
        if key in direct_map:
            result[key] = np.asarray(direct_map[key])[mask].copy()
    result["universe_groups"] = _universe_groups(result["universe"], result["addr"])
    result["last_values"] = np.full(len(result["addr"]), 256, dtype=np.uint16)
    result["virtual_dimmer"] = direct_map.get("virtual_dimmer", False)
    return result


def _pair_dimmer(direct_map, physical_rows, fixture_order):
    """Retain the existing virtual dimmer map and append any physical fine bytes."""
    result = _take_map(direct_map, slice(None))
    result["byte_role"] = np.zeros(len(result["addr"]), dtype=np.uint8)
    physical = {(fixture_order[fid], uni, addr): role
                for fid, uni, addr, role in physical_rows if role == 1}
    for index, position in enumerate(zip(result["fixture_idx"], result["universe"], result["addr"])):
        result["byte_role"][index] = physical.get(position, 0)
    extra = [row for row in physical_rows if row[3] == 2]
    if not extra:
        return result
    fine = _vector_map(extra, fixture_order)
    for key in ("fixture_idx", "universe", "addr", "byte_role"):
        result[key] = np.concatenate((result[key], fine[key]))
    _extend_dimmer_bases(result, len(extra))
    result["universe_groups"] = _universe_groups(result["universe"], result["addr"])
    result["last_values"] = np.full(len(result["addr"]), 256, dtype=np.uint16)
    return result


def _extend_dimmer_bases(direct_map, count):
    for key, default, dtype in (("base_values", 255, np.uint16), ("color_channel", -1, np.int16)):
        if key in direct_map:
            direct_map[key] = np.concatenate((direct_map[key], np.full(count, default, dtype=dtype)))


def _scoped_dimmers(vector_maps, info, fixture_order):
    direct_map = vector_maps.get("dimmer")
    if not direct_map:
        return
    for definition in {row["def_id"] for row in info.values() if row["def_id"]}:
        members = [fixture_order[fid] for fid, row in info.items() if row["def_id"] == definition]
        mask = np.isin(direct_map["fixture_idx"], members)
        if np.any(mask):
            vector_maps[make_scoped("dimmer", definition)] = _take_map(direct_map, mask)


def _legacy_resolutions(info, definitions):
    typed = {split_scope(key)[0] for _label, key in collect_channel_items(definitions)
             if split_scope(key)[1]}
    typed.update(key for row in info.values() for key in row["channels"] if key.startswith("function_"))
    resolutions = {}
    for key in typed:
        owners = {row["def_id"] for row in info.values() if row["channel_addrs"].get(key)}
        resolutions[key] = make_scoped(key, next(iter(owners))) if len(owners) == 1 and "" not in owners else ""
    return resolutions


def install_panel_maps(panel, vector_maps):
    """Expand the existing cache once; its virtual dimmer color contract survives."""
    project = panel._project_for_matrix()
    definitions = defs_of(project)
    info = _fixture_info(panel._fixtures, definitions)
    rows, fixture_maps = _physical_maps(info)
    order = panel._matrix_fixture_order
    physical_maps = {key: _vector_map(entries, order) for key, entries in rows.items()}
    if vector_maps.get("dimmer"):
        physical_maps["dimmer"] = _pair_dimmer(vector_maps["dimmer"], rows.get("dimmer", []), order)
    _scoped_dimmers(physical_maps, info, order)
    panel._matrix_channel_fixture_info = info
    panel._matrix_channel_roles = fixture_maps
    panel._matrix_legacy_targets = _legacy_resolutions(info, definitions)
    panel._matrix_profile_definitions = {str(fd.id): fd for fd in definitions}
    set_engine_profile_definitions(panel._engine, definitions)
    panel._channel_addr_cache = {key: [(fid, uni, addr) for fid, uni, addr, _role in entries]
                                 for key, entries in rows.items()}
    panel._fixture_addr_cache = {fid: {key: [(uni, addr) for uni, addr, _role in entries]
                                       for key, entries in fmap.items()}
                                 for fid, fmap in fixture_maps.items()}
    # Match the existing scalar virtual dimmer branch's empty physical entry.
    for fid, row in info.items():
        if any(row["channel_addrs"].get(key) for key in ("r", "g", "b", "w")):
            panel._fixture_addr_cache[fid].setdefault("dimmer", [])
    panel._matrix_raw_vector_maps = physical_maps
    return _compiled_owner_profiles(panel, physical_maps)


def resolve_output_target(owner, value) -> str:
    identity = normalized_identity(value)
    plain, definition = split_scope(identity)
    if definition:
        return identity
    return (getattr(owner, "_matrix_legacy_targets", {}) or {}).get(plain, identity)


def encode_map_values(values, direct_map, entry_mask=None):
    """Encode already quantized values using the shared Programmer byte contract."""
    values = np.asarray(values, dtype=np.uint8)
    roles = direct_map.get("byte_role")
    if roles is None:
        return values
    roles = np.asarray(roles)
    if entry_mask is not None:
        roles = roles[entry_mask]
    return np.where(roles == 2, _FINE_BYTES[values], values).astype(np.uint8, copy=False)


def fixture_output(panel, fid, value, target):
    target = resolve_output_target(panel, target)
    plain, definition = split_scope(target)
    info = getattr(panel, "_matrix_channel_fixture_info", {}).get(fid, {})
    if not target or (definition and info.get("def_id") != definition):
        return []
    value = scalar_profile_value(panel, fid, value, target)
    if value is None:
        return []
    fmap = panel._fixture_addr_cache.get(fid, {})
    if plain == "dimmer" and not fmap.get("dimmer"):
        gain = max(0.0, min(1.0, float(value) / 255.0))
        base = panel._matrix_base_color(fid, fmap)
        return [(int(uni), int(addr), int(round(base.get(key, 0) * gain)))
                for key in ("r", "g", "b", "w") for uni, addr in fmap.get(key, [])]
    entries = getattr(panel, "_matrix_channel_roles", {}).get(fid, {}).get(target, [])
    coarse, fine = motor_8bit_to_16bit_pair(value)
    return [(int(uni), int(addr), fine if role == 2 else coarse if role == 1 else int(value))
            for uni, addr, role in entries]


def install_engine_maps(engine):
    definitions = list(getattr(engine, "_matrix_profile_definitions", {}).values())
    info = _fixture_info(engine._fixtures, definitions)
    rows, _fixture_maps = _physical_maps(info)
    engine._fid_index = {fid: index for index, fid in enumerate(engine._sorted_ids)}
    engine._matrix_vector_maps = {key: _vector_map(entries, engine._fid_index) for key, entries in rows.items()}
    engine._matrix_channel_fixture_info = info
    engine._matrix_fixture_order = engine._fid_index
    engine._matrix_raw_vector_maps = engine._matrix_vector_maps
    engine._matrix_legacy_targets = _legacy_resolutions(info, definitions)
    engine._channel_addr_cache = {key: [(fid, uni, addr) for fid, uni, addr, _role in entries]
                                  for key, entries in rows.items()}
    engine._channel_addr_arrays = {key: (direct["fixture_idx"], direct["universe"], direct["addr"])
                                   for key, direct in engine._matrix_vector_maps.items()}
    refresh_profile_maps(engine)


def set_engine_profile_definitions(engine, definitions):
    if definitions is not None:
        definitions = definitions.values() if isinstance(definitions, dict) else definitions
        engine._matrix_profile_definitions = {str(fd.id): fd for fd in definitions}


def _compiled_owner_profiles(owner, raw_maps):
    config = owner._engine.config if hasattr(owner, "_engine") else owner.config
    context = (owner._matrix_channel_fixture_info, owner._matrix_fixture_order,
               getattr(owner, "_matrix_profile_definitions", {}))
    maps, rules = compile_profile_maps(raw_maps, context, config.channel_profiles,
                                      lambda key: resolve_output_target(owner, key), slice_map=_take_map)
    owner._matrix_profile_rules = rules
    return maps


def refresh_profile_maps(owner):
    raw = getattr(owner, "_matrix_raw_vector_maps", None)
    if raw is not None:
        owner._matrix_vector_maps = _compiled_owner_profiles(owner, raw)


def refresh_panel_profiles(panel):
    definitions = defs_of(panel._project_for_matrix())
    panel._matrix_profile_definitions = {str(fd.id): fd for fd in definitions}
    set_engine_profile_definitions(panel._engine, definitions)
    refresh_profile_maps(panel)
    refresh_profile_maps(panel._engine)
    target = resolve_output_target(panel, panel._ch_type)
    if target in getattr(panel, "_matrix_profile_rules", {}) and panel._matrix_profile_rules[target] is None:
        panel._clear_matrix_output_layers()


def write_engine_weights(engine, weights, on_value, off_value):
    target = resolve_output_target(engine, engine.config.ch_type)
    direct = getattr(engine, "_matrix_vector_maps", {}).get(target)
    if not direct:
        return
    indices = direct["fixture_idx"]
    levels = np.fromiter((weights.get(fid, 0.0) for fid in engine._sorted_ids),
                         dtype=np.float32, count=len(engine._sorted_ids))
    logical = np.clip(np.floor(off_value + (on_value - off_value) * levels[indices]), 0, 255)
    values = encode_map_values(profile_map_values(logical, direct), direct)
    if hasattr(engine._nm, "update_layer_sparse_arrays"):
        engine._nm.update_layer_sparse_arrays("matrix", direct["universe"], direct["addr"], values)
        return
    _write_engine_sparse(engine._nm, direct, values, weights, engine._sorted_ids)


def _write_engine_sparse(nm, direct_map, values, weights, sorted_ids):
    uni_channels = {}
    for index, universe, address, value in zip(direct_map["fixture_idx"], direct_map["universe"],
                                               direct_map["addr"], values):
        if sorted_ids[int(index)] in weights:
            uni_channels.setdefault(int(universe), {})[int(address)] = int(value)
    if hasattr(nm, "update_layer_sparse"):
        for universe, channel_map in uni_channels.items():
            nm.update_layer_sparse("matrix", universe, channel_map)
