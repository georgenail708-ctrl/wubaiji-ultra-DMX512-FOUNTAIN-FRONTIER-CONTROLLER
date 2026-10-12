"""Retire only addresses successfully written by this Matrix panel.

Reuse the compiled channel/profile maps and NetworkManager's channel retirement
API. Scope compilation runs on target changes; unchanged output frames do not
scan fixture definitions or whole DMX frames. Foreign addresses stay untouched.
"""
from __future__ import annotations

import numpy as np

from .matrix_channel_targets import resolve_output_target


def rebuild_output_scope(panel, finish_rebuild):
    finish_rebuild(panel)
    prepare_output_scope(panel)


def _same_scope(previous, maps, target, network):
    return previous is not None and previous[0] is maps and previous[1] == target and previous[2] is network


def _allowed_addresses(panel, maps, target):
    direct = (maps or {}).get(target)
    if direct is not None:
        rows = zip(direct["universe"], direct["addr"])
    else:
        rows = ((universe, address) for fixture in panel._fixtures
                for universe, address, _value in panel._matrix_fixture_output(fixture["id"], 0, target))
    allowed = {}
    for universe, address in rows:
        allowed.setdefault(int(universe), set()).add(int(address))
    return allowed


def _retire_addresses(panel, allowed):
    owned = getattr(panel, "_matrix_owned_addresses", {})
    for layer, universes in owned.items():
        for universe, addresses in universes.items():
            retired = addresses - allowed.get(universe, set())
            if retired:
                panel._nm_ref.clear_layer_channels(layer, universe, sorted(retired))
                addresses.difference_update(retired)


def prepare_output_scope(panel):
    network = getattr(panel, "_nm_ref", None)
    if network is None:
        return False
    maps = getattr(panel, "_matrix_vector_maps", None)
    target = resolve_output_target(panel, panel._ch_type)
    previous = getattr(panel, "_matrix_owned_scope", None)
    if not _same_scope(previous, maps, target, network):
        if previous is not None and previous[2] is not network:
            panel._matrix_owned_addresses = {}
        allowed = _allowed_addresses(panel, maps, target)
        _retire_addresses(panel, allowed)
        panel._matrix_allowed_addresses = allowed
        panel._matrix_owned_complete = set()
        panel._matrix_owned_scope = (maps, target, network)
    return bool(panel._fixtures)


def _remember_addresses(panel, layer, rows):
    complete = panel.__dict__.setdefault("_matrix_owned_complete", set())
    if layer in complete:
        return
    owned = panel.__dict__.setdefault("_matrix_owned_addresses", {}).setdefault(layer, {})
    for universe, addresses in rows:
        owned.setdefault(int(universe), set()).update(int(address) for address in addresses if 0 <= int(address) < 512)
    allowed = getattr(panel, "_matrix_allowed_addresses", {})
    if all(addresses.issubset(owned.get(universe, set())) for universe, addresses in allowed.items()):
        complete.add(layer)


def write_grouped(panel, layer, grouped, **kwargs):
    result = panel._nm_ref.update_layer_sparse_grouped_arrays(layer, grouped, **kwargs)
    _remember_addresses(panel, layer, ((universe, addresses) for universe, addresses, _values in grouped))
    return result


def write_arrays(panel, layer, universes, addresses, values, **kwargs):
    result = panel._nm_ref.update_layer_sparse_arrays(layer, universes, addresses, values, **kwargs)
    _remember_addresses(panel, layer, ((universe, [address]) for universe, address in zip(universes, addresses)))
    return result


def write_batch(panel, layer, updates, **kwargs):
    result = panel._nm_ref.update_layer_sparse_batch(layer, updates, **kwargs)
    _remember_addresses(panel, layer, updates.items())
    return result


def write_sparse(panel, layer, universe, updates, **kwargs):
    result = panel._nm_ref.update_layer_sparse(layer, universe, updates, **kwargs)
    _remember_addresses(panel, layer, [(universe, updates)])
    return result


def write_frame(panel, layer, universe, data, **kwargs):
    result = panel._nm_ref.update_layer(layer, universe, data, **kwargs)
    _remember_addresses(panel, layer, [(universe, np.flatnonzero(kwargs["active"]))])
    return result
