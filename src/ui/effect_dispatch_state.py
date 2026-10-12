"""Scene bindings and editor restoration around the original Effect APIs.

Editor restoration does not create a running owner. Existing transport owners
retain identity and clocks; pure playback continues through the original path.
"""
from contextlib import contextmanager
from copy import deepcopy


_BINDINGS_KEY = "effect_param_bindings"
_OWNER_BINDINGS_KEY = "_dispatch_binding_rules"
_RECALL_MARKER = "_dispatch_recalled"
_RUNTIME_FIELDS = (
    "_external_states", "_external_state", "_is_playing", "_play_start_time",
    "_virtual_t", "_last_real_t", "_global_speed_factor",
)


def _parameter_keys(entry):
    params = entry.get("params", {}) or {}
    keys = set(params)
    for form in params.get("_channel_forms", []) or []:
        keys.update((form.get("params", {}) or {}).keys())
    return keys


def _owns_rule(entry, rule):
    if rule.preset_id and rule.preset_id != entry.get("preset_id"):
        return False
    if rule.channel and rule.channel not in (entry.get("ch_types", []) or []):
        return False
    return rule.param_key in _parameter_keys(entry)


def _binding_rows(manager, entries):
    rows = []
    for rule in getattr(manager, "_bindings", {}).values():
        if rule.target_kind != "effect_param":
            continue
        owners = [[entry.get("track_type", ""), entry.get("group_key", "")]
                  for entry in entries if _owns_rule(entry, rule)]
        if owners:
            rows.append(dict(rule=rule.to_dict(), owners=owners))
    return rows


def capture_state(panel, state):
    data = dict(state)
    data[_BINDINGS_KEY] = _binding_rows(getattr(panel, "_joy_mgr", None),
                                       data.get("active_effects", []) or [])
    return data


def _restore_rule(manager, payload):
    from .joystick_control_binding import JoystickControlBindingRule
    rule = JoystickControlBindingRule.from_dict(deepcopy(payload))
    if rule.target_kind != "effect_param":
        return
    restored = manager.add_effect_param_binding(
        rule.channel, rule.param_key, rule.param_label,
        physical_axis=rule.physical_axis, invert=rule.invert, deadzone=rule.deadzone,
        range_lo=rule.range_lo, range_hi=rule.range_hi, curve=rule.curve,
        gamma=rule.gamma, steps=rule.steps, preset_id=rule.preset_id)
    restored.sub_axis = rule.sub_axis
    restored.space_mode = rule.space_mode
    restored.target_def_id = rule.target_def_id
    restored.ch_type = rule.ch_type


def _restore_bindings(panel, node, state):
    manager = getattr(panel, "_joy_mgr", None)
    if manager is None:
        return
    key = [node.get("track_type", ""), node.get("group_key", "")]
    for row in (state or {}).get(_BINDINGS_KEY, []) or []:
        if key in (row.get("owners", []) or []):
            _restore_rule(manager, row.get("rule", {}) or {})
    panel._refresh_param_bind_indicators()


def bind_recalled_owner(panel, node, state):
    """Keep source ownership beside the short-lived running state, outside params."""
    key = (node.get("track_type", ""), node.get("group_key", ""))
    owner = panel._external_states.get(key)
    manager = getattr(panel, "_joy_mgr", None)
    if owner is None or manager is None:
        return
    rows = [deepcopy(row["rule"]) for row in (state or {}).get(_BINDINGS_KEY, [])
            if list(key) in (row.get("owners", []) or [])]
    owner[_OWNER_BINDINGS_KEY] = rows
    for payload in rows:
        _restore_rule(manager, payload)


def _matching_rule(payload, rule):
    if rule is None:
        return False
    fields = ("preset_id", "channel", "param_key", "physical_axis", "invert",
              "deadzone", "range_lo", "range_hi", "curve", "gamma", "steps")
    current = rule.to_dict()
    return all(payload.get(field) == current.get(field) for field in fields)


def _float_slider_value(spec, value):
    from .effect_browser import _float_ui_range, _param_display_semantics
    _suffix, scale = _param_display_semantics(spec)
    step = abs(float(getattr(spec, "step", .01) or .01)) * scale
    decimals = 0
    while step < .99 and decimals < 4:
        step *= 10
        decimals += 1
    factor = scale * 10 ** decimals
    lo, hi = _float_ui_range(spec)
    return max(lo, min(hi, round(value * factor) / factor))


def mapped_parameter_value(spec, mapped):
    """Use ParamWidget's 255 mapping and its original integer/slider quantization."""
    if spec is None or spec.type not in ("float", "int"):
        return None
    lo = float(getattr(spec, "min_val", 0.0) or 0.0)
    hi = float(getattr(spec, "max_val", 255.0) or 255.0)
    if hi <= lo:
        return None
    value = lo + max(0, min(255, int(mapped))) / 255 * (hi - lo)
    if spec.type == "int":
        return int(round(value))
    return _float_slider_value(spec, value)


def _owner_bound_value(owner, payload, mapped):
    from .effect_engine import get_engine
    preset = get_engine().get_preset(owner.get("preset_id", ""))
    if preset is None:
        return None
    key = payload.get("param_key")
    spec = next((item for item in preset.params if item.key == key), None)
    return mapped_parameter_value(spec, mapped)


def _apply_owner_parameter(owner, payload, mapped):
    value = _owner_bound_value(owner, payload, mapped)
    if value is None:
        return
    params = owner["params"]
    channel, key = payload.get("channel", ""), payload["param_key"]
    forms = params.get("_channel_forms", []) or []
    for form in forms:
        if channel and form.get("ch_type") != channel:
            continue
        values = form.get("params", {})
        if key in values:
            values[key] = value
    if key in params:
        params[key] = value


def _route_owner_parameter(panel, channel, key, value):
    manager = getattr(panel, "_joy_mgr", None)
    rule = manager.get_effect_param_binding(channel, key) if manager is not None else None
    for owner in panel._external_states.values():
        for payload in owner.get(_OWNER_BINDINGS_KEY, []):
            if payload.get("channel", "") != channel or payload.get("param_key") != key:
                continue
            if _matching_rule(payload, rule):
                _apply_owner_parameter(owner, payload, value)
    return rule


def _editor_parameter_widget(panel, channel, key):
    forms = panel._channel_forms or []
    if channel:
        form = next((item for item in forms if panel._channel_form_type(item) == channel), None)
        return (form or {}).get("params", {}).get(key)
    widget = panel._param_widgets.get(key)
    if widget is not None:
        return widget
    return next((form["params"][key] for form in forms if key in form.get("params", {})), None)


def route_bound_value(panel, channel, key, value):
    rule = _route_owner_parameter(panel, channel, key, value)
    if rule is not None and rule.preset_id and rule.preset_id != panel._selected_id:
        return
    widget = _editor_parameter_widget(panel, channel, key)
    if widget is not None and hasattr(widget, "apply_external_value"):
        widget.apply_external_value(value)


def playback_options(params):
    """Consume routing metadata once; it never becomes an effect parameter."""
    return params.pop("_state_speed_factor", None), bool(params.pop(_RECALL_MARKER, False))


def remember_applied_preset(panel, preset_id, recalled):
    if not recalled:
        panel._remember_effect_history(preset_id)
        panel._selected_id = preset_id


def refresh_applied_lists(panel, recalled):
    if not recalled:
        panel._refresh_quick_effect_lists()


def _stop_form_flags(params):
    for form in params.get("_channel_forms", []) or []:
        form["playing"] = False
        form.get("params", {}).pop("playing", None)
    return params


def _editor_state(state):
    data = deepcopy(state or {})
    data["active_effects"] = []
    data["effects_playing"] = False
    _stop_form_flags(data.get("params", {}) or {})
    for group in (data.get("group_states", {}) or {}).values():
        _stop_form_flags(group.get("params", {}) or {})
    return data


@contextmanager
def _preserve_transport(panel):
    values = {name: getattr(panel, name) for name in _RUNTIME_FIELDS if hasattr(panel, name)}
    timer_active = panel._play_timer.isActive()
    panel._play_timer.stop()
    panel._external_states = {}
    panel._external_state = None
    panel._is_playing = False
    try:
        yield
    finally:
        for name, value in values.items():
            setattr(panel, name, value)
        if timer_active:
            panel._play_timer.start()
        else:
            panel._play_timer.stop()
        panel._update_transport_state()


def _canonical_legacy_channels(panel, channels):
    from .effect_browser import _module_channel_choices, _norm_channel_type
    choices = _module_channel_choices(panel)
    aliases = {_norm_channel_type(key): key for key in choices if "@" not in key}
    return [channel if channel in choices or "@" in str(channel)
            else aliases.get(_norm_channel_type(channel), channel) for channel in channels]


def _editor_cell_params(panel, params, channels, clock):
    """Restore legacy channels only when an explicit form list is absent."""
    from .effect_browser import _legacy_channel_forms
    params = _stop_form_flags(deepcopy(params))
    if "_channel_forms" in params or not channels:
        return params
    channels = _canonical_legacy_channels(panel, channels)
    forms = _legacy_channel_forms({**params, "ch_type": channels}, "ch_type")
    speed, virtual_t = clock
    for form in forms:
        form.update(playing=False, speed_factor=speed, virtual_t=virtual_t)
    params["_channel_forms"] = forms
    return params


def _restore_cell_controls(panel, node, state, helpers):
    scope, archived, sync_ui, rebind = helpers
    params, channels, _sort, speed, virtual_t, _base = archived(
        state, str(node.get("track_type", "")), str(node.get("group_key", "")))
    params = _editor_cell_params(panel, params, channels, (speed, virtual_t))
    ids = list(node.get("fixture_ids", []) or [])
    scope(panel, ids, node, True)
    sync_ui(panel, node["effect_id"], params, ids)
    saved_forms = [item for item in params.get("_channel_forms", []) or [] if isinstance(item, dict)]
    for form, saved in zip(panel._channel_forms, saved_forms):
        form["virtual_t"] = saved.get("virtual_t", virtual_t)
    form = panel._active_channel_form()
    if form and "_channel_forms" not in params:
        form["speed_factor"] = speed
        form["virtual_t"] = virtual_t
    if form:
        form["playing"] = False
        panel._sync_transport_controls_to_active_form()
    _restore_bindings(panel, node, state)
    rebind(panel, state)


def restore_editor(panel, node, state, helpers):
    with _preserve_transport(panel):
        panel.load_state(_editor_state(state))
        _restore_cell_controls(panel, node, state, helpers)
    panel.report_to_bus(force=True)
    return True
