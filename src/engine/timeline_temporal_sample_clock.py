"""Actual original tick scope and current Main clock metadata.

No UI import, clock invocation, controller property invocation, renderer clone,
new setting or numeric policy. Sample time remains original cache_index(now).
"""
from contextvars import ContextVar
import builtins
import hashlib
import sys
import threading
from types import BuiltinFunctionType, FunctionType

_TICK_OWNER = ContextVar("qascade_original_tick_sample", default=None)
_TIME = sys.modules["time"]
_MONOTONIC = vars(_TIME).get("monotonic")
_BUILTINS = {name: vars(builtins)[name] for name in ("bool", "getattr", "float", "max")}
_CLOCK_SOURCE = {'MainWindow._connect_timeline_transport.<locals>._clock_source': '6ba1d4b7a15b7b87929f256b58d6a186254433306fab3a7ac355303e30787a6f'}
_CONTROLLER_SOURCE = {'play': 'a0e89149edd7c518e5600fffa92f46f3e458840fde34448ff6baa9d6ab3e2bc7', 'pause': 'ee113d264a33572bc5879ebe4fddcf5c287f656b1a58ce147f043186638848b4', 'stop': '122dc59cf3e01c741cae1964cab52e1ed549b9cc50ba0d3d12b52b2e8de98f7a', 'seek': '02912202796b1b4166a066fe657d6e071ebd8969438b924b4f0c9357b0dd9929', '_on_tick': '538e7f45943520375ee3de79089a9abc7983f366bd199dd23292e34eed5a4c27'}


def _monotonic_current():
    return (type(_MONOTONIC) is BuiltinFunctionType and _MONOTONIC.__module__ == "time"
            and _MONOTONIC.__name__ == "monotonic" and vars(_TIME).get("monotonic") is _MONOTONIC)


def _cells(callback):
    try:
        return dict(zip(callback.__code__.co_freevars, (cell.cell_contents for cell in callback.__closure__ or ())))
    except ValueError:
        return {}


def _clock_globals_current(callback):
    return all(callback.__globals__.get(name, vars(builtins).get(name)) is original
               for name, original in _BUILTINS.items())


def _source_digest_current(function, wanted, namespace):
    if type(function) is not FunctionType or function.__globals__ is not namespace:
        return False
    try:
        from qascade_ce.native_curve_scalar_guard import _signature
    except ModuleNotFoundError as error:
        if error.name in ("qascade_ce", "qascade_ce.native_curve_scalar_guard"):
            return False  # Optional CE qualification unavailable; original Python clock remains assigned.
        raise
    return hashlib.sha256(repr(_signature(function.__code__)).encode()).hexdigest() == wanted


def _source_original(callback):
    module = sys.modules.get("src.ui.main_window")
    if type(callback) is not FunctionType or module is None:
        return False
    return (_source_digest_current(callback, _CLOCK_SOURCE.get(callback.__code__.co_qualname), vars(module))
            and _cells(callback).get("_t") is _TIME and callback.__kwdefaults__ is None
            and _clock_globals_current(callback) and _monotonic_current())


def _controller(callback):
    module = sys.modules.get("src.ui.timeline.timeline_controller")
    owner = vars(module).get("TimelineController") if module is not None else None
    defaults = callback.__defaults__
    if owner is None or type(defaults) is not tuple or len(defaults) != 1 or type(defaults[0]) is not owner:
        return None
    return defaults[0], module


def _controller_fields_current(controller):
    blocked = ("_playing", "_play_t0", "__getattribute__", "__getattr__")
    owner = type(controller)
    if any(name in vars(owner) for name in blocked):
        return False  # Never invoke a late property/foreign attribute reader.
    return (type(vars(controller).get("_play_t0")) is float
            and not any(name in vars(controller) for name in _CONTROLLER_SOURCE))


def _ordinary_controller(controller):
    return _controller_fields_current(controller) and vars(controller).get("_playing") is True


def _controller_qualified(controller, module):
    from qascade_ce.native_curve_scalar_guard import function_metadata
    rows = []
    for name, wanted in _CONTROLLER_SOURCE.items():
        function = vars(type(controller)).get(name)
        if not _source_digest_current(function, wanted, vars(module)):
            return None
        if function.__defaults__ is not None or function.__kwdefaults__ is not None:
            return None
        rows.append((vars(type(controller)), name, function))
        rows.extend(function_metadata(function))
    return rows


def _metadata_current(rows):
    return all(source.get(name) is captured for source, name, captured in rows)


def _clock_metadata(callback):
    return callback.__code__, callback.__defaults__, callback.__kwdefaults__, callback.__closure__


def register_sample_clock_source(engine, callback):
    """Capture exact Main callback/controller identity at original registration."""
    engine._timeline_sample_clock_guard = None
    if not _source_original(callback):
        return
    found = _controller(callback)
    if found is None or not _controller_fields_current(found[0]):
        return
    rows = _controller_qualified(*found)
    if rows is not None:
        engine._timeline_sample_clock_guard = callback, _clock_metadata(callback), found[0], rows


def _qualification(engine, callback, controller, module):
    certificate = getattr(engine, "_timeline_sample_clock_guard", None)
    return certificate if certificate is not None and certificate[0] is callback else None


def _main_clock_current(engine, callback):
    if type(callback) is not FunctionType:
        return False
    found = _controller(callback)
    if found is None or not _ordinary_controller(found[0]):
        return False
    certificate = _qualification(engine, callback, *found)
    if certificate is None:
        return False
    return (certificate[2] is found[0] and _metadata_current(certificate[3])
            and all(actual is saved for actual, saved in zip(_clock_metadata(callback), certificate[1]))
            and _cells(callback).get("_t") is _TIME
            and _clock_globals_current(callback) and _monotonic_current())


def _original_tick_current(engine):
    module = sys.modules.get(type(engine).__module__)
    claim = _TICK_OWNER.get()
    return (claim is not None and claim[0] is engine and claim[1] == threading.get_ident()
            and module is not None and vars(module).get("time") is _TIME and _monotonic_current())


def sample_clock_current(engine):
    from .playback_engine import PlaybackState
    if engine._state is PlaybackState.PLAYING and _original_tick_current(engine):
        return True
    owner = getattr(engine, "_gen_thread", None)
    if not isinstance(owner, threading.Thread) or owner is not threading.current_thread():
        return False
    if type(engine._gen_stop) is not threading.Event or engine._gen_stop.is_set() or not engine._gen_thread_enabled:
        return False
    return _main_clock_current(engine, engine._gen_clock_source)


def render_tick_sample(engine, actual_now):
    """Only the fixed original _tick reaches this scoped original math call."""
    token = _TICK_OWNER.set((engine, threading.get_ident()))
    try:
        return engine._render_position(actual_now, layer_name="clip_playback")
    finally:
        _TICK_OWNER.reset(token)
