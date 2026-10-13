"""C03 canonical preflight before binding, fixed ABI and dependency snapshots."""
import ctypes as ct
import functools
import inspect
import sys
from types import FunctionType

import numpy as np
from numpy._core import multiarray,numeric,fromnumeric,numerictypes

from . import native_binding as binding
from . import native_curve_scalar_guard as scalar

SOURCE_BUILTINS = ('min','len','int','bool','getattr','id','float','str','tuple')
MODULE_BUILTINS = SOURCE_BUILTINS + ('type','isinstance','dict','all','any','set','vars','hasattr','zip')


def modules():
    from . import native_fade_target as math,native_fade_target_runtime as runtime
    from . import native_fade_target_guard as guard,native_fade_target_install as installer
    return math,runtime,guard,installer,sys.modules[__name__]


def arguments():
    u8,f32,i64 = ct.POINTER(ct.c_uint8),ct.POINTER(ct.c_float),ct.POINTER(ct.c_int64)
    return {'signature':(u8,ct.c_int32,u8,ct.c_int32,i64,i64),
        'target':(f32,ct.c_int32,u8,ct.c_int32,u8,f32,i64)}


def declarations(math):
    expected = arguments()
    return (math.U8 is expected['signature'][0] and math.F32 is expected['target'][0]
        and math.I64 is expected['signature'][-1] and math.INT32_MAX == 2**31-1
        and type(math.SIGNATURES) is dict and math.SIGNATURES == {
            'signature':('ce_fade_target_signature',list(expected['signature'])),
            'target':('ce_fade_target_frame',list(expected['target']))})


def arguments_current(function,expected,result):
    actual = function.argtypes
    return (type(actual) in (list,tuple) and len(actual) == len(expected)
        and all(left is right for left,right in zip(actual,expected)) and function.restype is result)


def binding_rows(dll,functions):
    from .native_fade_target_guard import MISSING
    if type(dll) is not ct.CDLL or type(binding.CE_API_MAP) is not dict:
        return None
    rows = []
    for key,name in (('signature','ce_fade_target_signature'),('target','ce_fade_target_frame')):
        symbol = binding.CE_API_MAP.get(name,name)
        if type(symbol) is not str or vars(dll).get(symbol) is not functions[key]:
            return None
        rows.extend(((vars(dll),symbol,functions[key]),
            (binding.CE_API_MAP,name,binding.CE_API_MAP.get(name,MISSING))))
    return rows


def bound(engine,math):
    if type(math.NATIVE) is not dict or set(math.NATIVE) != {'signature','target'}:
        return False
    expected = arguments()
    return (binding_rows(vars(engine).get('dll'),math.NATIVE) is not None
        and all(isinstance(function,ct._CFuncPtr)
            and arguments_current(function,expected[name],ct.c_int)
            for name,function in math.NATIVE.items()))


def aliases(context):
    math,runtime,guard,installer,schema = context
    return all((math.np is np,math.ct is ct,runtime.np is np,runtime.math is math,
        runtime.wraps is functools.wraps,guard.np is np,guard.ct is ct,
        guard._signature is scalar._signature,binding.ct is ct,
        math.bind_native is binding.bind_native,math.borrowed_pointer is binding.borrowed_pointer,
        schema.ct is ct,schema.np is np,np is sys.modules['numpy'],ct is sys.modules['ctypes']))


def original_function(module,name,expected,defaults):
    from .native_fade_target_guard import digest
    function = vars(module).get(name)
    return (type(function) is FunctionType and function.__globals__ is vars(module)
        and function.__defaults__ == defaults and function.__kwdefaults__ is None
        and digest(function) == expected)


def pointer_suppliers():
    from .native_fade_target_guard import EXPECTED
    return (all(original_function(binding,name,EXPECTED['native_binding'][name],None)
                for name in ('bind_native','borrowed_pointer'))
        and original_function(ct,'cast',EXPECTED['ctypes']['cast'],None)
        and original_function(functools,'wraps',EXPECTED['functools']['wraps'],
            (functools.WRAPPER_ASSIGNMENTS,functools.WRAPPER_UPDATES))
        and all(original_function(scalar,name,EXPECTED['scalar'][name],None)
            for name in ('_signature','_normalize'))
        and isinstance(ct._cast,ct._CFuncPtr)
        and arguments_current(ct._cast,(ct.c_void_p,ct.py_object,ct.py_object),ct.py_object))


def source_methods():
    from src.engine import playback_engine
    from .native_fade_target_guard import ORIGINAL,MISSING,digest
    namespace,owner = vars(playback_engine),playback_engine.PlaybackEngine
    for name,expected in ORIGINAL.items():
        function = inspect.getattr_static(owner,name)
        if type(function) is not FunctionType or function.__globals__ is not namespace:
            return False
        default = (None,) if name == '_get_fade_out_target' else None
        if (function.__defaults__ != default or function.__kwdefaults__ is not None
                or digest(function) != expected):
            return False
    return (namespace.get('np') is np
        and all(namespace.get(name,MISSING) is MISSING for name in SOURCE_BUILTINS))


def numpy_function(module,name):
    from .native_fade_target_guard import EXPECTED,digest
    function = getattr(module,name)
    original = getattr(function,'__wrapped__',None)
    if type(original) is not FunctionType or original.__globals__ is not vars(module):
        return False
    defaults = (None,None,None,np._NoValue,np._NoValue,np._NoValue) if name == 'sum' else (None,)
    keywords = None if name == 'sum' else {'keepdims':False}
    return (getattr(np,name) is function and original.__defaults__ == defaults
        and original.__kwdefaults__ == keywords and digest(original) == EXPECTED['numpy'][name])


def numpy_suppliers():
    return (numpy_function(fromnumeric,'sum') and numpy_function(numeric,'count_nonzero')
        and all(getattr(np,name) is getattr(multiarray,name)
            for name in ('asarray','ascontiguousarray','empty','zeros','ndarray'))
        and all(getattr(np,name) is getattr(numerictypes,name)
            for name in ('float32','uint8','bool_','uint32')))


def preflight(engine):
    from .native_fade_target_guard import supplier
    context = modules()
    return (aliases(context) and declarations(context[0]) and source_methods()
        and pointer_suppliers() and numpy_suppliers()
        and all(supplier(module) for module in context)
        and all(not any(name in vars(module) for name in MODULE_BUILTINS) for module in context)
        and type(vars(engine).get('dll')) is ct.CDLL)


def dependency_rows():
    from .native_fade_target_guard import metadata,MISSING
    fields = ((binding,('bind_native','borrowed_pointer')), (ct,('cast',)),
        (functools,('wraps',)), (scalar,('_signature','_normalize')))
    functions = [(vars(module),name,metadata(vars(module)[name]))
        for module,names in fields for name in names]
    names = ((binding,('ct','CE_API_MAP')), (ct,('_cast','_cast_addr','POINTER','_CFuncPtr',
        'c_int','c_int32','c_int64','c_uint8','c_float','CDLL','py_object','c_void_p')),
        (scalar,('struct','CodeType','_signature','_normalize')),
        (functools,('partial','update_wrapper','WRAPPER_ASSIGNMENTS','WRAPPER_UPDATES')))
    values = [(vars(module),name,vars(module).get(name,MISSING))
        for module,keys in names for name in keys]
    return functions,values
