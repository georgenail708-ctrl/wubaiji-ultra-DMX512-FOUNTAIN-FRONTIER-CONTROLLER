"""Explicit Matrix output-mode mapping, backed by actual definition references.

Reference: existing FixtureDefinition.channel_modes / Programmer mode actions;
capabilities distinguish continuous ranges and discrete endpoints. Raw is
the absent-profile default. Saved frames, gamma, timing and shaping stay intact.
"""
from __future__ import annotations

import math
import numpy as np

from ..project_channels import channel_key, make_scoped, split_scope


def _identity(value):
    plain, definition = split_scope(value)
    return make_scoped(channel_key(plain), definition)


def _byte(value):
    try:
        number = float(value) if not isinstance(value, bool) else math.nan
    except (TypeError, ValueError, OverflowError):
        return None
    return int(number) if math.isfinite(number) and number.is_integer() and 0 <= number <= 255 else None


def _mode_reference(raw):
    mode = raw if isinstance(raw, dict) else {}
    return {"name": str(mode.get("name", "") or ""),
            "dmx_min": _byte(mode.get("dmx_min")), "dmx_max": _byte(mode.get("dmx_max"))}


def _profile(raw):
    profile = raw if isinstance(raw, dict) else {}
    modes = profile.get("modes")
    return {"kind": str(profile.get("kind", "") or ""),
            "definition_id": str(profile.get("definition_id", "") or ""),
            "modes": [_mode_reference(mode) for mode in modes] if isinstance(modes, list) else []}


def normalize_channel_profiles(raw=None):
    state = raw if isinstance(raw, dict) else {}
    values = state.get("by_channel")
    values = values if isinstance(values, dict) else {}
    return {"version": state.get("version", 1),
            "by_channel": {_identity(key): _profile(profile) for key, profile in values.items()
                           if isinstance(key, str) and key}}


def _actual_modes(definition, plain):
    modes = getattr(definition, "channel_modes", {}) or {}
    result = []
    for key, records in modes.items():
        if channel_key(key) == plain and isinstance(records, list):
            result.extend(_mode_reference(record) for record in records)
    return [mode for mode in result if _valid_mode(mode)]


def _valid_mode(mode):
    low, high = mode["dmx_min"], mode["dmx_max"]
    return bool(mode["name"]) and low is not None and high is not None and low <= high


def validate_profile(key, profile, definitions):
    """None means an explicit saved reference is invalid and must pause output."""
    plain, scoped = split_scope(_identity(key))
    definition_id = profile["definition_id"]
    definition = definitions.get(definition_id)
    if definition is None or (scoped and scoped != definition_id):
        return None
    declared = _actual_modes(definition, plain)
    selected = profile["modes"]
    if not selected or any(mode not in declared for mode in selected):
        return None
    return _profile_rule(profile["kind"], definition_id, selected)


def _profile_rule(kind, definition_id, modes):
    if kind == "range" and len(modes) == 1:
        return (1, definition_id, modes[0]["dmx_min"], modes[0]["dmx_max"])
    if kind != "switch" or len(modes) != 2:
        return None
    if any(mode["dmx_min"] != mode["dmx_max"] for mode in modes):
        return None
    low, high = (mode["dmx_min"] for mode in modes)
    return (2, definition_id, low, high) if low != high else None


def profile_choices(key, definitions):
    """Exact declared modes, with an explicit switch between two point modes."""
    plain, scoped = split_scope(_identity(key))
    choices = []
    for definition in definitions.values():
        definition_id = str(getattr(definition, "id", "") or "")
        if scoped and definition_id != scoped:
            continue
        choices.extend(_definition_choices(definition, definition_id, plain))
    return choices


def _definition_choices(definition, definition_id, plain):
    modes = _actual_modes(definition, plain)
    name = str(getattr(definition, "name", "") or definition_id)
    choices = [(f"{name} · {mode['name']} · {mode['dmx_min']}–{mode['dmx_max']}",
                {"kind": "range", "definition_id": definition_id, "modes": [mode]}) for mode in modes]
    points = sorted((mode for mode in modes if mode["dmx_min"] == mode["dmx_max"]),
                    key=lambda mode: mode["dmx_min"])
    if len(points) == 2 and points[0]["dmx_min"] != points[1]["dmx_min"]:
        label = f"{name} · {points[0]['name']} / {points[1]['name']}"
        choices.append((label, {"kind": "switch", "definition_id": definition_id, "modes": points}))
    return choices


def compile_profile_maps(raw_maps, context, state, resolve, *, slice_map):
    """Compile validity and entry roles on changes; hot output never reads defs."""
    info, fixture_order, definitions = context
    profiles = normalize_channel_profiles(state)
    rules = {resolve(key): validate_profile(key, profile, definitions)
             for key, profile in profiles["by_channel"].items() if resolve(key)}
    if profiles["version"] != 1:
        rules = dict.fromkeys(rules)
    result = dict(raw_maps)
    reverse = {index: info[fid]["def_id"] for fid, index in fixture_order.items()}
    for key, rule in rules.items():
        direct = raw_maps.get(key)
        if direct is not None:
            result[key] = _compile_direct_profile(direct, rule, reverse, slice_map)
    return result, rules


def _compile_direct_profile(direct, rule, reverse, slice_map):
    valid = np.ones(len(direct["addr"]), dtype=bool) if rule is not None else np.zeros(len(direct["addr"]), dtype=bool)
    result = slice_map(direct, valid)
    if rule is None:
        return result
    kind, definition_id, low, high = rule
    applies = np.fromiter((reverse[index] == definition_id for index in result["fixture_idx"]),
                          dtype=bool, count=len(result["addr"]))
    result["profile_kind"] = np.where(applies, kind, 0).astype(np.uint8)
    result["profile_low"] = np.full(len(applies), low, dtype=np.uint16)
    result["profile_high"] = np.full(len(applies), high, dtype=np.uint16)
    return result


def profile_map_values(values, direct, entry_mask=None):
    values = np.asarray(values, dtype=np.uint16)
    kind = direct.get("profile_kind")
    if kind is None:
        return values
    low, high = direct["profile_low"], direct["profile_high"]
    if entry_mask is not None:
        kind, low, high = kind[entry_mask], low[entry_mask], high[entry_mask]
    continuous = np.rint(low.astype(float) + values.astype(float) * (high.astype(float) - low) / 255)
    switched = np.where(values >= 128, high, low)
    return np.where(kind == 1, continuous, np.where(kind == 2, switched, values)).astype(np.uint16)


def scalar_profile_value(owner, fid, value, target):
    rules = getattr(owner, "_matrix_profile_rules", {})
    if target not in rules:
        return value
    rule = rules[target]
    if rule is None:
        return None
    kind, definition_id, low, high = rule
    info = getattr(owner, "_matrix_channel_fixture_info", {}).get(fid, {})
    if info.get("def_id") != definition_id:
        return value
    if kind == 1:
        return int(round(low + float(value) * (high - low) / 255))
    return high if value >= 128 else low


def profile_is_valid(owner, key):
    rules = getattr(owner, "_matrix_profile_rules", {})
    return key not in rules or rules[key] is not None
