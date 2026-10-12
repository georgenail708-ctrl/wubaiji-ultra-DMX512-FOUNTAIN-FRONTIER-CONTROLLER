"""Metadata and original callbacks around native Read/default/motor operations."""
from .native_programmer_row_queries import converted, one
from .native_programmer_row_qualification import parsed


def read(context, host):
    panel, automation = context.panel, context.automation
    try:
        auto = getattr(host, "_traj_auto", None)
        if not automation.automation_is_read(auto) or not getattr(host, "_traj_rec_active", False):
            return
        channel = auto.master_channel()
        if not channel:
            return
        trajectory = host._traj_selected()
        if trajectory is None:
            return
        time = converted(context.native, host._traj_play_t)
        raw = trajectory.value_at(time)
        if not parsed(raw):
            context.statistics.compatibility("unsupported")
            value = max(0., min(1., float(raw)))
            row_value, byte = value * 255., int(round(value * 255.))
        else:
            result = one(context.native, "READ", (raw,))
            byte, row_value = result.values()[0], result.numbers()[0]
        row = host._find_joystick_target_row(channel, "", "")
        if row is None:
            return
        host.controlTrajectoryEvent.emit(f"programmer|master|{channel}", byte)
        row._on_master_changed(channel, row_value, layer="fader")
    except Exception:
        pass


def motor(context, value):
    return tuple(one(context.native, "MOTOR", (value,)).indices())


def default(context, host, fid, channel):
    cache = getattr(host, "_traj_default_cache", None)
    if cache is None:
        cache = host._traj_default_cache = {}
    key = (str(fid), str(channel))
    if key in cache:
        return cache[key]
    fixtures = getattr(host, "_traj_fx_by_id", None)
    if fixtures is None:
        fixtures = host._traj_fx_by_id = {
            str(getattr(fixture, "id", "")): fixture for fixture in (host._current_fixtures or [])}
    value = 0.
    try:
        fixture = fixtures.get(str(fid))
        if fixture is not None:
            definitions = getattr(host, "_fixture_defs_cache", {}) or {}
            definition = definitions.get(getattr(fixture, "def_id", None))
            defaults = getattr(definition, "function_channel_defaults", {}) or {} if definition else {}
            raw = defaults.get(channel)
            if raw is None and channel in context.panel.MOTOR_FINE_TO_COARSE:
                raw = defaults.get(context.panel.MOTOR_FINE_TO_COARSE[channel])
            if raw is not None:
                if not parsed(raw):
                    context.statistics.compatibility("unsupported")
                    value = max(0., min(1., float(raw) / 255.))
                else:
                    value = one(context.native, "DEFAULT", (raw,)).numbers()[0]
    except Exception:
        value = 0.
    cache[key] = value
    return value
