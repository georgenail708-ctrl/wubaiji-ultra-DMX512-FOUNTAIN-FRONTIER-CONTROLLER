"""Row range metadata/callback order around native dense numerical batches."""
from .native_programmer_row_payload import make
from .native_programmer_row_queries import converted, one, quantized
from .native_programmer_row_qualification import numeric_layers, parsed

MODES = ("flat", "one_way", "two_way")
DIRECTIONS = ("low_to_high", "high_to_low", "high_low", "low_high", "center_out", "edges_in")


def _mode(value):
    return MODES.index(value) if value in MODES else 0


def _wing(host):
    from src.utils.wing_block import normalize_wing_direction
    value = normalize_wing_direction(getattr(host, "_value_wing_direction", "low_to_high"))
    return DIRECTIONS.index(value) if value in DIRECTIONS else 0


def _range_batch(context, host, slots, mode, base, target, fine):
    flags = _wing(host) | (8 if fine else 0)
    items = [dict(value_offset=0, value_count=0, number_offset=0, number_count=2,
                  index=index, total=total, mode=_mode(mode), flags=flags)
             for _fixture, index, total in slots]
    result = context.native.run(make("RANGE", (), items, dict(numbers=(base, target))))
    return result


def factor(context, host, arguments, spatial=False):
    index, total = arguments["logical_index"], arguments["total"]
    mode = arguments.get("mode", "flat")
    if spatial:
        modifiers, qt = arguments["modifiers"], context.panel.Qt
        mode = "two_way" if modifiers & qt.KeyboardModifier.ShiftModifier else (
            "one_way" if modifiers & qt.KeyboardModifier.ControlModifier else "flat")
    flags = 16 | (0 if spatial else _wing(host))
    item = dict(value_offset=0, value_count=2, number_offset=0, number_count=2,
                flags=flags, mode=_mode(mode))
    result = context.native.run(make("RANGE", (index, total), [item], dict(numbers=(0., 0.))))
    result.raise_semantic()
    return result.numbers()[0]


def set_state(context, host, channel, mode, base, target):
    panel = context.panel
    state = host._get_range_state(channel)
    state["mode"] = mode if mode in MODES else "flat"
    items = [dict(value_offset=index, value_count=1, mode=3) for index in range(2)]
    result = context.native.run(make("QUANTIZE", (base, target), items, dict(parse_policy="int")))
    for key, value in zip(("base", "target"), result.values()):
        state[key] = value
    result.raise_semantic()
    strip = host._range_strips.get(channel)
    if panel._qobject_alive(strip):
        strip.set_state(state["mode"], state["base"], state["target"])
    elif strip is not None:
        host._range_strips.pop(channel, None)
    for strips in (getattr(host, "_ctrl_fader_strips", {}), getattr(host, "_ctrl_vslider_strips", {}),
                   getattr(host, "_ctrl_joy_strips", {})):
        strip = strips.get(channel)
        if panel._qobject_alive(strip) and hasattr(strip, "set_state"):
            strip.set_state(state["mode"], state["base"], state["target"])
        elif strip is not None:
            strips.pop(channel, None)
    panel._r183_set_range_readout(host, channel, state)
    label = host._wing_block_labels.get(channel) if hasattr(host, "_wing_block_labels") else None
    if panel._qobject_alive(label):
        label.setText(host._wing_block_label_text())
    elif label is not None:
        host._wing_block_labels.pop(channel, None)


def _commit(context, host, channel, layer, fixture, value, rounded, quiet):
    panel, fid = context.panel, fixture.id
    layer_map = host._layers[layer]
    if fid not in layer_map:
        layer_map[fid] = {}
    layer_map[fid][channel] = value
    if panel._is_light_channel(channel):
        host._bump_vd_color_version()
    if not quiet:
        combined = host._compute_merged_value(fid, channel)
        rounded = one(context.native, "ROUND_TYPED", (combined,)).values()[0]
    values = host._values.get(fid)
    if values is None:
        values = host._values[fid] = {}
    if values.get(channel) != rounded:
        values[channel] = rounded
        if host._expanded and host._body.isVisible():
            host._refresh_body_cell(fid, channel, rounded)
    panel._mark_cell_edited(host, fid, channel, rounded)


def _jitter(context, value, percentage):
    limits = context.native.run(make("JITTER_BOUNDS", (), [dict(number_offset=0, number_count=1)],
                                     dict(numbers=(percentage,))))
    limits.raise_semantic()
    low, high = limits.numbers()
    noise = context.panel._random.uniform(low, high)
    result = context.native.run(make("JITTER_ADD", (), [dict(number_offset=0, number_count=3, flags=1)],
                                     dict(numbers=(value, percentage, noise))))
    result.raise_semantic()
    return result.numbers()[0]


def _round_rows(context, values):
    items = [dict(value_offset=index, value_count=1) for index in range(len(values))]
    result = context.native.run(make("ROUND_TYPED", values, items))
    result.raise_semantic()
    return result.values()


def _apply_slots(context, host, channel, mode, base, target, layer, slots, percentage):
    quiet = host._layer_modes.get(layer, "override") != "multiply" and not any(
        host._layers[name] for name in ("fader", "joystick", "audio", "matrix") if name != layer)
    if not slots:
        return {}
    fine = channel in context.panel.MOTOR_FINE_CHANNELS
    batch, index, direction = {}, 0, _wing(host)
    result = _range_batch(context, host, slots, mode, base, target, fine)
    result.raise_semantic()
    values, numbers = result.values(), result.numbers()
    rounded = _round_rows(context, values) if fine and quiet else values
    for offset, (fixture, _index, _total) in enumerate(slots):
        current = _wing(host)
        if current != direction:
            result = _range_batch(context, host, slots[offset:], mode, base, target, fine)
            result.raise_semantic()
            values, numbers, index, direction = result.values(), result.numbers(), 0, current
            rounded = _round_rows(context, values) if fine and quiet else values
        value, merged_value = values[index], rounded[index]
        if percentage > 0.:
            raw = _jitter(context, numbers[2 * index + 1], percentage)
            merged_value = one(context.native, "ROUND_TYPED", (raw,)).values()[0]
            value = raw if fine else merged_value
        _commit(context, host, channel, layer, fixture, value, merged_value, quiet)
        batch.setdefault(fixture.id, {})[channel] = value
        index += 1
    return batch


def apply(context, host, channel, mode, base, target, layer):
    panel = context.panel
    values, bounds = quantized(context.native, (base, target), mode=1)
    base_f, target_f = bounds
    host._set_range_state(channel, mode, *values)
    slots = host._wing_block_distribution_slots(host._ordered_fixtures_for_channel(channel))
    if channel == "dimmer" or panel._is_light_channel(channel):
        slots = host._collapse_package_light_slots(slots)
    percentage = panel._r183_prepare_range_write(host, channel, slots)
    host._is_refreshing = True
    try:
        batch = _apply_slots(context, host, channel, mode, base_f, target_f, layer, slots, percentage)
        host.value_changed.emit(channel, values[1])
    finally:
        host._is_refreshing = False
    panel.emit_range_batch_status(host, batch, layer, channel)
    if channel == "motor_z" and slots:
        result = context.native.run(make("MOTOR_Z_FINAL", (), [dict(number_offset=0, number_count=1)],
                                        dict(numbers=(target_f,))))
        result.raise_semantic()
        command = result.numbers()[0] if result.indices()[0] else None
        host.sync_z_command_changed.emit(command, [fixture.id for fixture, _i, _n in slots])


def _debounced(context, host, now, field):
    previous = getattr(host, field, 0.) or 0.
    if type(now) not in (bool, int, float) or not parsed(previous):
        context.statistics.compatibility("unsupported")
        return now - float(previous) < .033
    return one(context.native, "DEBOUNCE", (now, previous)).indices()[0]


def master(context, host, channel, value, layer):
    panel = context.panel
    if getattr(host, "_is_refreshing", False):
        return
    values, numbers = quantized(context.native, (value,), mode=1)
    value_i, value_f = values[0], numbers[0]
    if hasattr(host, "_val_label"):
        host._val_label.setText(panel._ptool.format_value_dual(value_i))
    now = panel._time.monotonic()
    host._pending_master[(channel, layer)] = (channel, value_f, layer)
    if _debounced(context, host, now, "_last_master_t"):
        if not host._master_timer.isActive():
            host._master_timer.start()
        return
    host._last_master_t = now
    host._pending_master.pop((channel, layer), None)
    modifiers = panel.QApplication.keyboardModifiers()
    state = host._get_range_state(channel)
    shift = bool(modifiers & panel.Qt.KeyboardModifier.ShiftModifier)
    ctrl = bool(modifiers & panel.Qt.KeyboardModifier.ControlModifier)
    flag = 1 if shift else (2 if ctrl else 0)
    if flag:
        fallback = host._get_merged_value(channel)
        base = converted(context.native, state.get("base", fallback))
        target, mode = value_f, "flat"
    else:
        base = converted(context.native, state.get("base", value_f))
        target = converted(context.native, state.get("target", value_f))
        mode = state.get("mode", "flat")
    # The original preserves unknown non-flat mode metadata while still
    # translating its endpoints; only literal flat bypasses that translation.
    item = dict(number_offset=0, number_count=3, mode=0 if mode == "flat" else 1, flags=flag)
    result = context.native.run(make("MASTER_RANGE", (), [item], dict(numbers=(value_f, base, target))))
    result.raise_semantic()
    mode = MODES[result.indices()[0]] if flag else mode
    host._apply_channel_range(channel, mode, *result.numbers(), layer=layer)
    host._update_mode_checks(channel)


def strip(context, host, channel, mode, base, target):
    panel = context.panel
    if getattr(host, "_is_refreshing", False):
        return
    try:
        base_f = one(context.native, "CONVERT_FLOAT", (base,)).numbers()[0]
        target_f = one(context.native, "CONVERT_FLOAT", (target,)).numbers()[0]
        host.controlTrajectoryEvent.emit(f"programmer|range|{channel}", (str(mode), base_f, target_f))
    except Exception:
        pass
    now = panel._time.monotonic()
    host._pending_strip = (channel, mode, base, target)
    if _debounced(context, host, now, "_last_strip_t"):
        if not host._strip_timer.isActive():
            host._strip_timer.start()
        return
    host._last_strip_t = now
    host._apply_channel_range(channel, mode, base, target, layer="fader")


def dispatch(context, name, arguments, original):
    host = arguments["self"]
    if name in ("_range_factor", "_spatial_factor"):
        if not all(type(arguments[key]) in (bool, int, float) for key in ("logical_index", "total")):
            return original()
        return factor(context, host, arguments, name == "_spatial_factor")
    keys = ("base", "target") if name in ("_apply_channel_range", "_set_range_state", "_on_range_strip_changed") else ("value",)
    if not all(parsed(arguments[key]) for key in keys):
        return original()
    channel = arguments["ch_type"]
    if name == "_set_range_state":
        return set_state(context, host, channel, arguments["mode"], arguments["base"], arguments["target"])
    if name == "_on_range_strip_changed":
        return strip(context, host, channel, arguments["mode"], arguments["base"], arguments["target"])
    if name == "_on_master_changed":
        return master(context, host, channel, arguments["value"], arguments["layer"])
    if not numeric_layers(host):
        return original()
    if not context.hooks.canonical_helper(host, "_range_factor"):
        return original("override")
    return apply(context, host, channel, arguments["mode"], arguments["base"], arguments["target"], arguments["layer"])
