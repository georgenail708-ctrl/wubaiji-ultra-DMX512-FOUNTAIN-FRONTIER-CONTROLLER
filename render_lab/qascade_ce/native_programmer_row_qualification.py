"""Passive qualification only; callbacks and ordinary arithmetic remain ordered."""
import inspect

from .native_programmer_row_values import ordinary


def parsed(value):
    return ordinary(value) or type(value) in (str, bytes, type(None))


def plain_field(owner, name):
    value = inspect.getattr_static(owner, name)
    return not hasattr(type(value), "__get__")


def plain_clock(host, trajectory, tick=True):
    fields = ("_traj_last_tick", "_traj_play_t") if tick else ()
    if not all(plain_field(host, name) for name in fields):
        return False
    if not all(ordinary(getattr(host, name)) for name in fields):
        return False
    return plain_field(trajectory, "duration") and parsed(trajectory.duration)


def numeric_curve(arguments):
    raw, _name, low, high, gamma, steps = arguments
    return ordinary(raw) and parsed(low) and parsed(high) and ordinary(gamma) and ordinary(steps)


def numeric_layers(row):
    if type(row._layers) is not dict or type(row._layer_modes) is not dict:
        return False
    for layer in row._layers.values():
        if type(layer) is not dict:
            return False
        for channels in layer.values():
            if type(channels) is not dict:
                return False
            if not all(value is None or ordinary(value) for value in channels.values()):
                return False
    return True


def numeric_universes(values):
    if values is None:
        return True
    if type(values) is not dict:
        return False
    for universe, channels in values.items():
        if not parsed(universe) or type(channels) not in (dict, type(None)):
            return False
        for address, value in (channels or {}).items():
            if not parsed(address) or not parsed(value):
                return False
    return True
