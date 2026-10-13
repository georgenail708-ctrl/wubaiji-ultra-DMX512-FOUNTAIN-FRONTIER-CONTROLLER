"""Qualified public target wrapper; original method remains exact __wrapped__."""
from functools import wraps

import numpy as np

from . import native_fade_target as math


def _signature(data, active):
    result = math.signature(data,active)
    if result is None:
        result = (data.size,active.size,int(np.sum(data[:512].astype(np.uint32))),
            int(np.count_nonzero(active[:512])))
    return result


def _content(data, active):
    # Fixed <=1KiB byte identity, not numeric hashing or a whole-project scan.
    return (data.size,active.size,data.dtype.str,active.dtype.str,
        data[:512].tobytes(),active[:512].tobytes())


def _project_rows(proj):
    return ((),()) if proj is None else (proj.fixtures,proj.fixture_defs)


def _retire_changed_motor(owner,universe,project_rows,token):
    proj,fixtures,definitions = project_rows
    key = ('__fade_target_defaults_c03__',universe,id(proj))
    previous = owner._clip_fade_geometry_cache.get(key)
    same = (previous is not None and previous[0] is proj and previous[1] is fixtures
        and previous[2] is definitions and previous[3] == token)
    if same:
        return
    # Missing proof cannot certify a retained weak row after FIFO eviction.
    motor_key = ('__motor_channel_mask__',universe,id(proj),len(fixtures),len(definitions))
    owner._clip_fade_geometry_cache.pop(motor_key,None)
    owner._store_clip_fade_geometry_cache(key,(proj,fixtures,definitions,token))


def _default_frame(entry):
    values,default_active = np.zeros(512,np.float32),np.zeros(512,bool)
    if entry is not None:
        raw,active = entry
        data = np.asarray(raw,dtype=np.float32)
        size = min(data.size,512)
        if size:
            values[:size] = data.reshape(-1)[:size]
        active = np.asarray(active,dtype=bool)
        size = min(active.size,512)
        if size:
            default_active[:size] = active.reshape(-1)[:size]
    return values,default_active


def _target(owner,universe,proj,entry):
    values,default_active = _default_frame(entry)
    motor = owner._build_motor_channel_mask(universe,proj)
    result = math.target(values,default_active,motor)
    if result is None:
        result = np.zeros(512,np.float32)
        result[default_active] = values[default_active]
        if motor.any():
            result[motor & ~default_active] = 128.0
    return result


def render(owner,universe,proj,entry):
    """Only admitted plain input enters here; foreign input uses the old body."""
    universe = int(universe)
    fixtures,definitions = _project_rows(proj)
    signature,token = None,None
    if entry is not None:
        raw,active = entry
        data_sig = np.asarray(raw,dtype=np.uint8).reshape(-1)
        active_sig = np.asarray(active,dtype=bool).reshape(-1)
        signature,token = _signature(data_sig,active_sig),_content(data_sig,active_sig)
    key = ('__fade_out_target_c03__',universe,id(proj),len(fixtures),len(definitions),signature,token)
    _retire_changed_motor(owner,universe,(proj,fixtures,definitions),token)
    cached = owner._clip_fade_geometry_cache.get(key)
    if (cached is not None and cached[0] is proj and cached[1] is fixtures
            and cached[2] is definitions):
        math.COUNTERS['cache'] += 1
        return np.asarray(cached[3],dtype=np.float32).copy()
    result = _target(owner,universe,proj,entry)
    owner._store_clip_fade_geometry_cache(key,
        (proj,fixtures,definitions,tuple(float(value) for value in result)))
    return result


def wrap(original):
    @wraps(original)
    def fade_target(owner,universe,proj=None):
        from .native_fade_target_install import guard_for
        guard = guard_for(owner,universe,proj)
        if guard is None:
            from . import native_fade_target as native
            native.COUNTERS['foreign'] += 1
            return original(owner,universe,proj=proj)
        entry = None if owner._nm is None else owner._nm._channel_defaults.get(int(universe))
        return render(owner,universe,proj,entry)
    return fade_target
