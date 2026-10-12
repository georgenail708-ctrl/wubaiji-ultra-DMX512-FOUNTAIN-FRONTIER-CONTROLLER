"""Manual slicing gates independent of the page's multi-channel transport."""
from PyQt6.QtCore import Qt


def externally_driven(panel):
    """AudioBridge marks its track external:; page playback uses manual:."""
    if panel._external_state is not None:
        return True
    return any(str(key[0]).startswith("external:")
               for key in panel._external_states if key)


def focus_blocks_slice(key, input_active):
    """Keep typing/navigation keys with editors, permit a bound function key."""
    if key is None:
        return input_active
    function_key = int(Qt.Key.Key_F1) <= int(key) <= int(Qt.Key.Key_F35)
    return input_active and not function_key


def _active_channel_types(panel):
    if not panel._has_channel_forms():
        return panel._get_selected_ch_types()
    form = panel._active_channel_form()
    return [panel._channel_form_type(form)] if form is not None else []


def _current_manual_owner(panel, state, key):
    if state.get("recalled") or state.get("preset_id") != panel._selected_id:
        return False
    if key[1] != panel._current_live_selection_key():
        return False
    channels = _active_channel_types(panel)
    expected_track = panel._manual_effect_track_type(panel._selected_id, channels)
    return key[0] == expected_track and list(state.get("ch_types", [])) == channels


def _slice_active_for_owner(panel, state, key):
    forcing = bool(getattr(panel, "_manual_slice_force_motion", False))
    holding = (bool(getattr(panel, "_manual_slice_latched", False))
               and bool(getattr(panel, "_manual_slice_frame_cache", None)))
    if not (forcing or holding):
        return False
    return _current_manual_owner(panel, state, key)


def advance_state_clock(panel, state, now, speed, key):
    """Ordinary owners keep the original clock; held manual slice stays still."""
    virtual = float(state.get("virtual_t", 0.0) or 0.0)
    if not _slice_active_for_owner(panel, state, key):
        last_real = float(state.get("last_real_t", 0.0) or 0.0)
        if last_real <= 0.0:
            last_real = now
        virtual += max(0.0, now - last_real) * speed * panel._global_speed_factor
        state["virtual_t"] = virtual
    state["last_real_t"] = now
    return virtual


def state_channel_frames(panel, engine, preset_id, t, params, *, fixtures,
                         definitions, positions, fps, sort_mode, base_order,
                         channel_types, owner, owner_key):
    """Use the original compute once, rotating only the selected manual owner."""
    manual = _slice_active_for_owner(panel, owner, owner_key)
    if manual:
        override = getattr(panel, "_manual_slice_t_override", None)
        t = float(override) if override is not None else 0.0
        params = panel._prepare_manual_slice_params(params)
    frames = panel._compute_channel_frames(
        engine, preset_id, t, params, fixtures, definitions, positions,
        fps, sort_mode, base_order, channel_types, runtime_owner=owner)
    for frame, allowed_types, fx_used in frames:
        if manual:
            frame.values = panel._rotate_manual_slice_values(
                frame.values, fx_used, positions, sort_mode, base_order, params)
        yield frame, allowed_types, fx_used
