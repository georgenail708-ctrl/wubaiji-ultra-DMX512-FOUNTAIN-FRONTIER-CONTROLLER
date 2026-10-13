"""Fixed prebinding admission and one full current check per public call."""
import sys
from types import FunctionType

from . import native_lt_prepare_metadata as metadata
from . import native_lt_prepare_guards as guards
from . import native_lt_prepare_identity as identity
from .native_lt_prepare_signatures import OWN, SUPPLIERS, ORIGINAL, CALLER

MODULES = ('native_lt_prepare', 'native_lt_prepare_types', 'native_lt_prepare_metadata',
           'native_lt_prepare_guards', 'native_lt_prepare_identity', 'native_lt_prepare_scope')


def _modules():
    return tuple(sys.modules[__package__ + '.' + name] for name in MODULES)


def _rows():
    rows = []
    for stem, qualified, code, expected_defaults in OWN + SUPPLIERS:
        module = sys.modules[__package__ + '.' + stem]
        target, name, function = guards.locate(module, qualified)
        if type(function) is not FunctionType or function.__globals__ is not vars(module):
            return None
        if guards.digest(function) != code or not guards.defaults(function, expected_defaults):
            return None
        rows.append((target, name, guards.stamp(function)))
    return rows


def _original(original, params_type):
    from src.engine import playback_engine as pe, clip_light_tracking_scope as public
    from . import lt_path
    rows = ((pe, original, 'PE'), (public, public.lt_intensity_params, 'intensity'),
            (public, public.lt_color_params, 'color'), (lt_path, params_type.__init__, 'params'))
    if pe.lt_intensity_params is not public.lt_intensity_params or pe.lt_color_params is not public.lt_color_params:
        return None
    for module, function, key in rows:
        if type(function) is not FunctionType or function.__globals__ is not vars(module):
            return None
        if guards.digest(function) != dict(ORIGINAL)[key]:
            return None
        expected = guards._constant(('pump', 128, .08, .22, True, 255, 255, 255, 255) if key == 'params' else None)
        if not guards.defaults(function, (expected, ('NoneType', None))):
            return None
    if type(pe.DEFAULT_LIGHT_TRACKING_ATTACK) is not float or type(pe.DEFAULT_LIGHT_TRACKING_RELEASE) is not float:
        return None
    if pe.DEFAULT_LIGHT_TRACKING_ATTACK != .08 or pe.DEFAULT_LIGHT_TRACKING_RELEASE != .22:
        return None
    return tuple((module, function, guards.stamp(function)) for module, function, _key in rows)


def capture(engine, original, caller, params_type):
    from src.core.models import Clip
    native = sys.modules[__package__]
    modules = _modules()
    rows = _rows()
    originals = _original(original, params_type)
    if rows is None or originals is None or not identity.aliases(modules) or not identity.layouts():
        return None
    if not identity.models(Clip, params_type) or engine is not getattr(native, '_engine', None):
        return None
    if type(caller) is not FunctionType or caller.__globals__ is not vars(native) or guards.digest(caller) != CALLER:
        return None
    cells = dict(zip(caller.__code__.co_freevars, (cell.cell_contents for cell in caller.__closure__ or ())))
    if cells.get('orig') is not original or cells.get('engine') is not engine:
        return None
    if cells.get('_lt_prepare_fn') is not modules[0].public_prepare or cells.get('_lt_owner') is not None:
        return None
    return Scope(modules, rows, originals, caller, Clip, params_type)


class Owner:
    def __init__(self, binding, original, caller, guard):
        self.binding, self.original, self.caller, self.guard = binding, original, caller, guard
        self.clip_type, self.params_type = guard.clip_type, guard.params_type
        self.attack_default, self.release_default = .08, .22
        self.admission = guard.current
        self.admission_metadata = guards.stamp(self.admission.__func__)


class Scope:
    def __init__(self, modules, rows, originals, caller, clip_type, params_type):
        from src.engine.playback_engine import PlaybackEngine
        self.modules, self.rows, self.originals = modules, rows, originals
        self.caller, self.caller_metadata = caller, guards.stamp(caller, owner_cell=True)
        self.clip_type, self.params_type = clip_type, params_type
        self.playback_type = PlaybackEngine
        self.aliases = tuple((module, name, vars(module)[name]) for module in modules
                             for name in ('guards', 'identity', 'metadata') if name in vars(module))
        self.owner = self.native = None
        self.missing = guards.MISSING

    def attach(self, owner):
        self.owner = owner
        self.native = identity.NativeIdentity(owner.binding)

    def current(self):
        from src.engine import playback_engine as pe
        from src.engine import clip_light_tracking_scope as public
        owner = self.owner
        if owner is None or owner.guard is not self or owner.original is not self.originals[0][1]:
            return False
        if pe.PlaybackEngine is not self.playback_type:
            return False
        if owner.caller is not self.caller or pe.PlaybackEngine._apply_clip_light_tracking is not self.caller:
            return False
        if owner.clip_type is not self.clip_type or owner.params_type is not self.params_type:
            return False
        if type(owner.attack_default) is not float or owner.attack_default != .08 or type(owner.release_default) is not float or owner.release_default != .22:
            return False
        for target, name, row in self.rows:
            function = vars(target).get(name)
            if type(function) is not FunctionType or function is not row[0] or function.__code__ is not row[1]:
                return False
            if function.__defaults__ is not row[2] or function.__kwdefaults__ is not row[3] or function.__globals__ is not row[5]:
                return False
            for key, value in row[6]:
                if function.__globals__.get(key, self.missing) is not value:
                    return False
        if not all(guards.same(vars(target).get(name), row) for target, name, row in self.rows):
            return False
        if not all(guards.same(function, row) for _module, function, row in self.originals):
            return False
        if public.lt_intensity_params is not self.originals[1][1] or public.lt_color_params is not self.originals[2][1]:
            return False
        if pe.lt_intensity_params is not self.originals[1][1] or pe.lt_color_params is not self.originals[2][1]:
            return False
        if self.params_type.__init__ is not self.originals[3][1]:
            return False
        if not guards.same(self.caller, self.caller_metadata):
            return False
        cells = dict(zip(self.caller.__code__.co_freevars, (cell.cell_contents for cell in self.caller.__closure__ or ())))
        return (cells.get('_lt_owner') is owner and all(vars(module).get(name) is value for module, name, value in self.aliases)
                and identity.models(self.clip_type, self.params_type) and identity.layouts()
                and self.native.current(owner))
