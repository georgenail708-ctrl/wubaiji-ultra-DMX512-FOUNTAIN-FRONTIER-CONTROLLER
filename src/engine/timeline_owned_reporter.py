"""Exact default AutoBaker RAW reporter qualification at the real range boundary.

The original prepare_prepass guard also remains authoritative for wrapped or
None progress. The real owned range receives the canonical RAW _cb. Unknown
callbacks, wrappers and foreign baker reporters retain the original veto.
Reference: existing fixed-source canonical/_cells/_baker_current contracts.
"""
from importlib import import_module


_PREPARE = '_hook_timeline_range.<locals>.ce_render_timeline_frame_range.<locals>.prepare_progress'
_RAW = 'TimelineAutoBaker._make_progress_cb.<locals>._cb'


def _default_raw(guard, reporter):
    if not guard.canonical(reporter):
        return False
    if reporter.__code__.co_qualname != _PREPARE:
        return False
    callback = guard._cells(reporter).get('progress_callback')
    if not guard.canonical(callback) or callback.__code__.co_qualname != _RAW:
        return False
    return guard._baker_current(guard._cells(callback).get('self'))


def default_owned_progress(reporter, package='qascade_ce'):
    guard = import_module(package + '.prepared_prepass_guard')
    return guard.default_progress(reporter) or _default_raw(guard, reporter)
