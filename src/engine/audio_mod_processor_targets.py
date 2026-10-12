"""AM21 typed patch plan, reused by all strict-scope Clip processor calls.

Geometry is not synthesized: addresses use original fixture expansion, motor
values use original logical DMX centers. No physical degree/height assumptions.
"""
from __future__ import annotations

import weakref
from src.core.constants import ABSOLUTE_HOME_MOTOR_CHANNELS, MOTOR_CENTER_HOME, MOTOR_FINE_CHANNELS
from .audio_mod_driver import _fixture_channel_addrs
from .xy_reference import channel_type_key

_CACHE = {"ref": None, "key": None, "plan": None}


def _valid(address):
    return 0 <= address < 512


def _axis_home(kind):
    return 0.0 if kind in ABSOLUTE_HOME_MOTOR_CHANNELS else float(MOTOR_CENTER_HOME)


def _add_axis(plan, channels, definition, kind):
    coarse = channels.get(kind)
    if coarse is None:
        return
    fine = channels.get(MOTOR_FINE_CHANNELS[kind])
    if not _valid(coarse):
        return
    if fine is not None and not _valid(fine):
        return
    plan["axes"].append({"type": kind, "coarse": coarse, "fine": fine,
                         "home": _axis_home(kind)})


def _add_fixture(result, fixture, definition):
    rows = _fixture_channel_addrs(fixture, definition)
    if not rows:
        return
    universe = rows[0][1][0]
    plan = result.setdefault(universe, {"channels": {}, "axes": [], "rgb": []})
    channels = {channel_type_key(kind): address[1] for kind, address in rows}
    for kind, address in channels.items():
        if _valid(address):
            plan["channels"].setdefault(kind, set()).add(address)
    for kind in MOTOR_FINE_CHANNELS:
        _add_axis(plan, channels, definition, kind)
    rgb = tuple(channels.get(kind, -1) for kind in ("r", "g", "b"))
    if all(_valid(address) for address in rgb):
        plan["rgb"].append(rgb)


def _build(project):
    definitions = {str(getattr(value, "id", "")): value
                   for value in getattr(project, "fixture_defs", ())}
    result = {}
    for fixture in getattr(project, "fixtures", ()):
        definition = definitions.get(str(getattr(fixture, "def_id", "")))
        if definition is not None:
            _add_fixture(result, fixture, definition)
    for plan in result.values():
        plan["channels"] = {key: tuple(sorted(values)) for key, values in plan["channels"].items()}
        plan["axes"] = tuple(plan["axes"])
        plan["rgb"] = tuple(plan["rgb"])
    return result


def _identity(project):
    fixtures = getattr(project, "fixtures", ())
    definitions = getattr(project, "fixture_defs", ())
    return (int(getattr(project, "patch_rev", 0)), id(fixtures), len(fixtures),
            id(definitions), len(definitions))


def build_target_plan(project):
    """Return {universe: channels/axes/rgb}; strict Clip/folder membership is caller-owned."""
    if project is None:
        return {}
    key = _identity(project)
    reference = _CACHE["ref"]
    if reference is not None and reference() is project and key == _CACHE["key"]:
        return _CACHE["plan"]
    result = _build(project)
    try:
        reference = weakref.ref(project)
    except TypeError:
        return result
    _CACHE.update(ref=reference, key=key, plan=result)
    return result
