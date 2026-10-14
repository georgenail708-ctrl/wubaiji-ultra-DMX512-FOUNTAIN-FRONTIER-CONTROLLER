"""Retained public conversions/exception order and bounded foreign compatibility."""
from contextvars import ContextVar
from . import native_runner_create_args as arguments
from . import native_runner_create_runtime as runtime
from . import native_runner_create_types as t
from . import native_runner_create_consumers as consumers

_COMPAT = None
# Fixed-size accounting only. A compatibility entry never qualifies full CE.
_COMPAT_COUNTS = [0,0,0]


def compatibility_counts():
    return tuple(_COMPAT_COUNTS)


def _compatible(original,owner,call,operation):
    _COMPAT_COUNTS[operation] += 1
    return original(owner,*call[0],**call[1])


def prepare_compat():
    global _COMPAT
    if _COMPAT is not None:
        raise RuntimeError('Complete create compatibility context was already installed')
    _COMPAT = ContextVar('runner_complete_create_foreign_legacy',default=None)
    return _COMPAT


def discard_compat(expected):
    global _COMPAT
    if _COMPAT is expected:
        _COMPAT = None


def constructor(context,train,call,original):
    values = arguments.bound(call,t.DIRECT)
    if values is None or not arguments.ordinary(values,t.DIRECT):
        return _compatible(original,train,call,t.DIRECT)
    spec = _converted(values,t.DIRECT)
    if spec is None:
        return _compatible(original,train,call,t.DIRECT)
    spec.update(operation=t.DIRECT,points=[])
    runtime.initialize(context,train,spec)


def v2(context,owner,call,original):
    values = arguments.bound(call,t.V2)
    if values is None or not arguments.ordinary(values,t.V2):
        return _compatible(original,owner,call,t.V2)
    spec = _converted(values,t.V2)
    if spec is None:
        return _compatible(original,owner,call,t.V2)
    spec.update(operation=t.V2,points=[])
    lease = _COMPAT.get()
    if lease is not None and lease[0] is owner:
        spec['anticipated'] = lease[1]
    return runtime.create(context,owner,spec)


def legacy(context,owner,call,original):
    values = arguments.bound(call,t.LEGACY)
    if values is None:
        return _compatible(original,owner,call,t.LEGACY)
    count = arguments.path_budget(values['path_points'])
    if not arguments.ordinary(values,t.LEGACY) or not arguments.plain_points(values['path_points']):
        return _foreign(context,original,owner,call,count)
    spec = _converted(values,t.LEGACY)
    if spec is None:
        return _foreign(context,original,owner,call,count)
    points,error = arguments.points(values['path_points'])
    spec.update(operation=t.PARTIAL if error is not None else t.LEGACY,points=points)
    identity = runtime.create(context,owner,spec)
    if error is not None:
        raise error.with_traceback(error.__traceback__)
    return identity


def panel(context,owner,call,original):
    if not consumers.current():
        raise runtime.ledger.RetirementRefused('Actual Panel create consumer source is not qualified')
    args,keywords = call
    if len(args) != 2 or keywords or type(args[1]) not in (tuple,list) or len(args[1]) != 3:
        raise TypeError('add_train_materialized requires train_id and (unit, base_speed, color)')
    identity,options = args
    unit,speed,color = options
    if type(unit) not in (int,float,bool) or not all(type(value) in arguments.PLAIN for value in (identity,speed,color)):
        return _panel_compatible(context,owner,identity,options)
    identity,unit,speed,color = str(identity),float(unit),float(speed),str(color)
    spec = {'identity':identity,'count':4,'length':0.0,'radius':0.0,
        'speed':speed,'color':color,'unit':unit,'points':[],'operation':t.PANEL}
    return runtime.create(context,owner,spec)


def _panel_compatible(context,owner,identity,options):
    _COMPAT_COUNTS[t.V2] += 1
    unit,speed,color = options
    retained = context[0].replacements['_add_train_v2'][0]
    return retained(owner,identity,4,unit,unit*0.6,speed,color)


def _foreign(context,original,owner,call,count):
    methods = tuple(context[0].replacements[name][0] for name in
        ('_ensure_legacy_track','attach_train_to_track'))
    token = _COMPAT.set((owner,count,methods))
    try:
        return _compatible(original,owner,call,t.LEGACY)
    finally:
        _COMPAT.reset(token)


def ensure(context,owner,train_id,points):
    original = _compat_method(context,owner,'_ensure_legacy_track',0)
    if original is not None:
        return original(owner,train_id,points)
    return runtime.edit.ensure(context,owner,train_id,points)


def attach(context,owner,train_id,track_id,s0=0.0):
    original = _compat_method(context,owner,'attach_train_to_track',1)
    if original is not None:
        return original(owner,train_id,track_id,s0)
    return runtime.edit.attach(context,owner,train_id,track_id,s0)


def _compat_method(context,owner,name,index):
    lease = _COMPAT.get()
    if lease is None or lease[0] is not owner:
        return None
    original = context[0].replacements[name][0]
    if lease[2][index] is not original:
        raise runtime.ledger.RetirementRefused('Foreign legacy original supplier changed')
    context[2](owner)
    return original


def _converted(values,operation):
    try:
        return arguments.converted(values,operation)
    except (ValueError,TypeError,OverflowError):
        # Builtin conversion error is reproduced by the original once; no foreign
        # callback was executed by this attempt, and MemoryError is never hidden.
        return None
