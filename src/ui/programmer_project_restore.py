"""Restore an explicit project snapshot without reusing its old manual sum.

Project opening supersedes pending manual gestures for restored row channels.
Ordinary recall and undo retain their existing behavior. Original Programmer
scope, channel projection, sparse caches and public NM channel release are reused.
References/adoption: r185-programmer-b1-native-preflight sections 17/20 (Qt/).
"""
from __future__ import annotations

from collections import defaultdict

from PyQt6 import sip


def _output_addresses(panel, fid, channel, value):
    cache = panel._addr_cache.get(fid, {})
    projected = panel._precompiled_channel_output(
        fid, channel, value, cache, cache.get(channel, ()))
    return {(int(universe), int(address)) for universe, address, _ in projected
            if 0 <= int(address) < 512}


def _snapshot_values(state):
    from .programmer_panel import _recall_node_from_state, _recall_scope_ids, _scope_static_values
    node = _recall_node_from_state(state)
    scope = _recall_scope_ids(node.get("fixture_ids"))
    values = node["static_values"]
    return _scope_static_values(values, scope) if scope else values


def _row_cells(panel, row, values, fixtures, definitions):
    from .programmer_panel import _fixture_supports_logical_channel
    cells = []
    for fixture_id, channels in values.items():
        fixture = fixtures.get(str(fixture_id))
        if fixture is None or fixture.def_id != row._def_id:
            continue
        definition = definitions.get(fixture.def_id)
        for channel in row._ch_types:
            if channel not in channels or not _fixture_supports_logical_channel(definition, channel):
                continue
            # Validate before any deletion, using the original restore conversion.
            value = int(round(float(channels[channel])))
            old = row._layers.get("joystick", {}).get(fixture.id, {}).get(channel)
            addresses = _output_addresses(panel, fixture.id, channel, value)
            cells.append((row, fixture.id, channel, old is not None, addresses))
    return cells


def _restore_plan(panel, state):
    project = panel._pm.project
    fixtures = {str(item.id): item for item in project.fixtures}
    definitions = {str(item.id): item for item in project.fixture_defs}
    values = _snapshot_values(state)
    cells = []
    for row in panel._ch_list._rows:
        if sip.isdeleted(row):
            continue
        cells.extend(_row_cells(panel, row, values, fixtures, definitions))
    return cells


def _consume_pending(panel, row, fixture_id, channel, addresses):
    from .programmer_panel import _drop_one_store
    for layer in ("fader", "joystick"):
        row._pending_master.pop((channel, layer), None)
        _drop_one_store(panel._pending_layer_batch.get(layer, {}), fixture_id, channel)
    pending = row._pending_strip
    if pending is not None and pending[0] == channel:
        row._pending_strip = None
    # Legacy fader pending is also superseded for precisely this restored cell.
    for universe, address in addresses:
        pending = panel._pending_live.get(universe, {})
        pending.pop(address, None)
        if not pending:
            panel._pending_live.pop(universe, None)


def _forget_joystick(panel, row, fixture_id, channel, addresses):
    from .programmer_panel import _drop_addrs_from_layer_cache, _drop_one_store
    _drop_one_store(row._layers["joystick"], fixture_id, channel)
    _drop_addrs_from_layer_cache(panel, "joystick", addresses)
    keepalive = panel._keepalive_data.get("joystick", {})
    for universe, address in addresses:
        keepalive.get(universe, {}).pop(address, None)
    row._bump_vd_color_version()


def restore_project_state(panel, state):
    """Consume prior manual contributions, then call the original loader once.

The snapshot contains the aggregate manual value. Loading it into the original
fader must not add the old joystick contribution again. Live audio/matrix,
other cells, controller settings, and playback ownership are preserved.
"""
    loader = getattr(panel, "load_state", None)
    if not callable(loader):
        return None
    plan = _restore_plan(panel, state)
    released = defaultdict(set)
    changed = False
    for row, fixture_id, channel, has_joystick, addresses in plan:
        _consume_pending(panel, row, fixture_id, channel, addresses)
        if has_joystick:
            _forget_joystick(panel, row, fixture_id, channel, addresses)
            changed = True
            for universe, address in addresses:
                released[universe].add(address)
    if changed:
        panel._layer_sparse_version += 1
    network = getattr(panel, "_nm_ref", None)
    if network is not None:
        for universe, addresses in released.items():
            network.clear_layer_channels("joystick", universe, sorted(addresses))
    return loader(state)
