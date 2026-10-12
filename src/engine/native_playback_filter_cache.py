"""Borrow only readonly P7 filter masks from fresh, plain pairing metadata.

Public filter calls retain their original owning results. No clip, frame, scope
or count is memoized. Reference: R214-C5 actual65/33.
design (design only), existing strong playback-index ownership.
"""
from contextvars import ContextVar

import numpy as np

MISSING = object()
_BORROW = ContextVar("qascade_p7_filter_borrow", default=None)
_GUARD = None


def configure(guard):
    global _GUARD
    _GUARD = guard


def consume(engine, track, universe, index):
    guard = _GUARD
    if guard is None or not guard.entry_current(engine):
        return engine._track_filter_mask_for_universe(track, universe)
    token = _BORROW.set((engine, track, universe, index))
    try:
        return engine._track_filter_mask_for_universe(track, universe)
    finally:
        _BORROW.reset(token)


def _fields(owner, names):
    state = object.__getattribute__(owner, "__dict__")
    cls = type(owner)
    if any(hasattr(type(vars(cls).get(name)), "__get__") for name in names):
        return None
    if not all(name in state for name in names):
        return None
    return tuple(state[name] for name in names)


def _definitions(project, guard):
    raw = _fields(project, ("fixture_defs",))
    if raw is None or type(raw[0]) not in (list, tuple):
        return None
    values, owners = [], [raw[0]]
    for owner in raw[0]:
        if type(owner) is not guard.definition:
            return None
        row = _fields(owner, ("id", "channels"))
        if row is None or type(row[0]) not in (str, type(None)) or type(row[1]) not in (list, tuple):
            return None
        if not all(type(value) is str for value in row[1]):
            return None
        values.append((row[0], tuple(row[1])))
        owners.extend((owner, row[1]))
    return tuple(values), owners


def _fixtures(project, guard):
    raw = _fields(project, ("fixtures",))
    if raw is None or type(raw[0]) not in (list, tuple):
        return None
    values, owners = [], [raw[0]]
    for owner in raw[0]:
        if type(owner) is not guard.fixture:
            return None
        row = _fields(owner, ("universe", "start_channel", "def_id"))
        if row is None or any(type(value) is not int for value in row[:2]) or type(row[2]) is not str:
            return None
        values.append(row)
        owners.append(owner)
    return tuple(values), owners


def _rule_channels(rule):
    raw = _fields(rule, ("channels",))
    if raw is None or type(raw[0]) not in (set, frozenset):
        return None
    if not all(type(value) is int for value in raw[0]):
        return None
    return raw[0] if any(1 <= value <= 512 for value in raw[0]) else None


def _snapshot(engine, track, universe, rule, project):
    guard = _GUARD
    if guard is None or not guard.inputs_current(engine, track, rule):
        return None
    channels = _rule_channels(rule)
    # Invalid-only filters return None before the original project lookup.
    if channels is None:
        return None
    if project is None:
        project = guard.project_of(engine)
    if type(project) is not guard.project:
        return None
    definitions, fixtures = _definitions(project, guard), _fixtures(project, guard)
    if definitions is None or fixtures is None:
        return None
    key = (frozenset(channels), definitions[0], fixtures[0])
    owners = (track, rule, channels, project, *definitions[1], *fixtures[1])
    return project, key, owners


def lookup(engine, track, universe, rule, project):
    scope = _BORROW.get()
    if scope is None or scope[0] is not engine or scope[1] is not track or scope[2] != universe:
        return MISSING, None
    current = _snapshot(engine, track, universe, rule, project)
    if current is None:
        return MISSING, None
    project, key, owners = current
    state = getattr(engine, "_ce_indexed_filter_cache", None)
    if state is None or state[0] is not scope[3] or state[1] is not project:
        state = (scope[3], project, {})
        engine._ce_indexed_filter_cache = state
    slot = (id(track), universe)
    cached = state[2].get(slot)
    if cached is not None and cached[0] is track and cached[1] == key:
        return cached[3], None
    return MISSING, (state, slot, key, owners, (engine, track, universe, rule, project))


def publish(token, value):
    if token is None:
        return value
    state, slot, key, owners, arguments = token
    current = _snapshot(*arguments)
    if current is None or current[0] is not state[1] or current[1] != key:
        return value
    stored = np.asarray(value, dtype=bool).copy()
    stored.setflags(write=False)
    state[2][slot] = (owners[0], key, owners, stored)
    if len(state[2]) > 4096:
        try:
            state[2].pop(next(iter(state[2])))
        except (KeyError, StopIteration, RuntimeError):
            pass
    return stored
