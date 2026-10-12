"""Bind continuous Programmer producers independently of the editor selection.

The existing curve/audio engines and sparse output writer remain authoritative.
These bindings live only for the producer's explicit start/stop lifetime.
"""
from __future__ import annotations


def audio_fixtures(row):
    fixtures = getattr(row, "_audio_bound_fixtures", None)
    if fixtures is None:
        fixtures = list(row._fixtures)
        row._audio_bound_fixtures = fixtures
        row._audio_bound_fixture_ids = frozenset(f.id for f in fixtures)
    return fixtures


def realtime_fixture_ids(row, layer):
    """Reuse the start-time audio membership set in every high-rate frame."""
    if layer == "audio" and row._audio_active:
        audio_fixtures(row)
        return row._audio_bound_fixture_ids
    ids = getattr(row, "_fixture_id_set", None)
    if ids is None:
        ids = row._fixture_id_set = {f.id for f in row._fixtures}
    return ids


def start_audio(row):
    row._audio_bound_fixtures = None
    audio_fixtures(row)
    row._spectrum_sorted_cache.clear()
    row._spectrum_env_by_slot.clear()


def stop_audio(row):
    row._audio_active = False
    _release_audio_output(row)
    row._audio_bound_fixtures = None
    row._audio_bound_fixture_ids = None
    driver = getattr(row, "_audio_widget_ref", None)
    if driver is not None:
        driver.stop()
    button = getattr(row, "_audio_start_btn_ref", None)
    if button is not None:
        button.setChecked(False)
        button.setText("启动音频驱动")


def _release_audio_output(row):
    """Release only this row's written audio cells, including queued frames."""
    values = getattr(row, "_layers", {}).get("audio", {})
    if not values:
        return
    row._layers["audio"] = {}
    from .programmer_panel import _panel_of, _drop_addrs_from_layer_cache
    panel = _panel_of(row)
    if panel is None:
        return
    _cancel_audio_cells(row, panel, values)
    addresses = _audio_output_addresses(panel, values)
    _drop_addrs_from_layer_cache(panel, "audio", addresses)
    keepalive = panel._keepalive_data.get("audio", {})
    by_universe = {}
    for universe, address in addresses:
        keepalive.get(universe, {}).pop(address, None)
        by_universe.setdefault(universe, []).append(address)
    panel._layer_sparse_version += 1
    if panel._nm_ref is not None:
        for universe, channels in by_universe.items():
            panel._nm_ref.clear_layer_channels("audio", universe, channels)


def _cancel_audio_cells(row, panel, values):
    """Cancel queued audio writes and restore each cell's remaining layer value."""
    pending = panel._pending_layer_batch.get("audio", {})
    for fid, channels in values.items():
        for channel in channels:
            pending.get(fid, {}).pop(channel, None)
            row._values.setdefault(fid, {})[channel] = row._compute_merged_value(fid, channel)
        if not pending.get(fid):
            pending.pop(fid, None)


def _audio_output_addresses(panel, values):
    """Reuse the writer's physical expansion for coarse/fine and virtual dimmer."""
    addresses = set()
    for fid, channels in values.items():
        fixture_cache = panel._addr_cache.get(fid, {})
        for channel in channels:
            outputs = panel._precompiled_channel_output(
                fid, channel, 0, fixture_cache, fixture_cache.get(channel, []))
            addresses.update((universe, address) for universe, address, _ in outputs)
    return addresses


def bind_trajectory(panel, curve):
    groups = {str(target.get("group_id", "")) for target in curve.targets}
    scopes = {"follow", "global", "per_shape"}
    scopes.update(segment.spatial_scope for segment in (*curve.segments, *curve.boundaries))
    resolved = {(group, scope): panel._traj_resolve_group_segments(group, scope)
                for group in groups for scope in scopes}
    panel._trajectory_target_binding = (curve.id, resolved,
                                       dict(getattr(panel, "_fixture_positions", {}) or {}))


def evaluate_trajectory(panel, curve, time):
    binding = getattr(panel, "_trajectory_target_binding", None)
    if panel._traj_playing and binding is not None and binding[0] == curve.id:
        resolver = lambda group, scope: binding[1].get((group, scope), [])
        positions = binding[2]
    else:
        resolver = panel._traj_resolve_group_segments
        positions = getattr(panel, "_fixture_positions", None)
    return curve.evaluate(time, resolver, positions=positions,
                          resolve_default=panel._traj_resolve_default)
