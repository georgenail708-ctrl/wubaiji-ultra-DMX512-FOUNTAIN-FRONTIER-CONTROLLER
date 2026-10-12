"""Actual bridge applications and per-track scene targets using existing APIs."""
from copy import deepcopy
import logging


_STATE_KEY = "audio_applied_effects"


def _applications(panel):
    data = getattr(panel, "_dispatch_audio_applied", None)
    if data is None:
        data = panel._dispatch_audio_applied = {}
    return data


def _recall_scopes(panel):
    data = getattr(panel, "_dispatch_audio_recalls", None)
    if data is None:
        data = panel._dispatch_audio_recalls = {}
    return data


def scope_for_apply(panel, event):
    track, preset, params, channels, sort_mode = event
    ids, base = panel._get_output_fixture_order()
    recalled = _recall_scopes(panel).get(str(track))
    if recalled is not None:
        ids, base = list(recalled["fixture_ids"]), list(recalled["base_order_ids"])
    actual_ids = list(ids or params.get("_output_fixture_ids", []) or [])
    actual_base = list(base or params.get("_output_base_order_ids", []) or actual_ids)
    if preset:
        _applications(panel)[str(track)] = dict(
            preset_id=str(preset), params=deepcopy(params), ch_types=list(channels or []),
            sort_mode=str(sort_mode or "index"), fixture_ids=actual_ids,
            base_order_ids=actual_base, recalled=recalled is not None)
    else:
        _applications(panel).pop(str(track), None)
        _recall_scopes(panel).pop(str(track), None)
    return ids, base, output_params(panel, track, params)


def output_params(panel, track, params):
    data = dict(params)
    if str(track) in _recall_scopes(panel):
        data["_dispatch_recalled"] = True
    return data


def _current_order(panel):
    try:
        ids, base = panel._get_output_fixture_order()
    except Exception:
        ids = panel._get_output_fixture_ids() or []
        base = ids
    return [str(item) for item in (ids or []) if str(item)], list(base or ids or [])


def _node_details(panel, bridge, track):
    preset = str(bridge.get_current_preset(track) or "")
    applied = _applications(panel).get(track, {})
    if applied.get("preset_id") == preset:
        return applied
    ids, base = _current_order(panel)
    return dict(preset_id=preset, fixture_ids=ids, base_order_ids=base,
                ch_types=[], recalled=False)


def _audio_node(panel, bridge, track, enabled):
    from ...processing.dispatch import EffectNode
    data = _node_details(panel, bridge, track)
    ids = list(data["fixture_ids"])
    preset = data["preset_id"]
    layer = "matrix_audio" if track in ("pump", "motor", "valve") else "audio"
    return EffectNode(
        panel="audio", track_type=track, group_key=panel._dispatch_group_key(ids),
        group_name=f"输出设备组（{len(ids)} 台）" if ids else "全部设备",
        fixture_ids=ids, effect_id=preset, effect_name=preset or f"音频类型 {track}",
        category="", layers=[layer], ch_types=list(data.get("ch_types", [])),
        summary=f"类型 {track} · 预设 {preset or '--'} · {len(ids)} 台", live=enabled,
        extra=dict(recalled=bool(data.get("recalled"))))


def bus_nodes(panel):
    nodes = []
    try:
        bridge = getattr(panel, "_bridge", None)
        if bridge is None or not getattr(bridge, "_enabled", False):
            return nodes
        for track in bridge.get_active_types() or []:
            nodes.append(_audio_node(panel, bridge, str(track), True))
    except Exception:
        logging.getLogger(__name__).debug("audio_bridge: _bus_nodes failed", exc_info=True)
    return nodes


def capture_state(panel, state):
    data = dict(state)
    data[_STATE_KEY] = deepcopy(_applications(panel))
    return data


def _saved_application(panel, node, state):
    track = str(node.get("track_type", "") or "")
    saved = deepcopy((state.get(_STATE_KEY, {}) or {}).get(track, {}))
    preset = str(node.get("effect_id", "") or saved.get("preset_id", ""))
    params = saved.get("params")
    if params is None:
        params = panel._bridge._build_params(preset)
    ids = list(node.get("fixture_ids", []) or [])
    base = list(saved.get("base_order_ids", []) or node.get("base_ids", []) or ids)
    channels = list(node.get("ch_types", []) or saved.get("ch_types", []) or [])
    return track, dict(preset_id=preset, params=params, fixture_ids=ids,
                       base_order_ids=base, ch_types=channels,
                       sort_mode=saved.get("sort_mode", "index"), recalled=True)


def _refresh_editor(panel):
    refresh = getattr(panel, "_refresh_all_preset_ui", None)
    if callable(refresh):
        refresh()


def restore_editor(panel, node, state):
    """Restore configuration without changing bridge enablement or emitting output."""
    bridge = panel._bridge
    data = deepcopy(state)
    data["enabled"] = bridge._enabled
    bridge.load_dict(data)
    track, saved = _saved_application(panel, node, state)
    bridge._current_presets[track] = saved["preset_id"]
    bridge.current_preset_changed.emit(track, saved["preset_id"])
    _refresh_editor(panel)


def restore_node(panel, node, state, *, for_edit=False):
    if for_edit:
        restore_editor(panel, node, state)
        return
    bridge = panel._bridge
    bridge.load_dict(deepcopy(state))
    track, saved = _saved_application(panel, node, state)
    _recall_scopes(panel)[track] = dict(fixture_ids=saved["fixture_ids"],
                                      base_order_ids=saved["base_order_ids"])
    _applications(panel)[track] = saved
    bridge._current_presets[track] = saved["preset_id"]
    bridge.current_preset_changed.emit(track, saved["preset_id"])
    panel._on_bridge_apply(track, saved["preset_id"], saved["params"],
                           saved["ch_types"], saved["sort_mode"])


def append_output_group(panel, group_id):
    panel._output_group_ids.append(group_id)
    _recall_scopes(panel).clear()


def remove_output_group(panel, group_id):
    panel._output_group_ids.remove(group_id)
    _recall_scopes(panel).clear()


def release_node(panel, node, bridge):
    track = str(node.get("track_type", "") or "")
    _applications(panel).pop(track, None)
    _recall_scopes(panel).pop(track, None)
    bridge.set_enabled(False)
