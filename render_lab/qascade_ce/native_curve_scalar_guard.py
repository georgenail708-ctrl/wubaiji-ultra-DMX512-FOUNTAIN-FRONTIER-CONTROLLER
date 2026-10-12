"""Installation-only source metadata guard for the frozen scalar helpers.

These CPython 3.13 fingerprints exclude locations and docstrings. Preparation
never invokes a helper. Hot dispatch retains identity checks against the real
functions; a foreign body/globals present before installation stays compatible.
The frozen reference/source review is scalar-supplement-freeze01.json (R214).
"""
import hashlib
import struct
from types import CodeType, FunctionType

_BLOCKED = object()
_EXPECTED = {
    "_pt_out_ctrl": "c8e8025ba4d55310317ead24d87d72647e0c6b324a2abeb96d590101e9cfdd91",
    "_pt_in_ctrl": "6ad184fd49374b0597f641b5051d12371931a2f53095c6e8c332a139bafca698",
    "_seg_ctrls": "992011947414734ffaabd5ce517800764ae2e704f2956c2694094207f869f2b3",
    "_mono_ctrl_x": "b33850c930b6ca8264e9151c8405eb23461df0b58847ea3462e606e45fe5a0bb",
    "_bezier_coord": "0cb795458f3982ba984d0d743ffc4d2f52d7fe356671533b41456d7a35eeb437",
    "_eval_segment": "3b599761f21b73ca780d58dc17a9a4b4256c621f056644a9c59b9de6d5167d9a",
    "_clamp01": "f6ee3c594e1e5e0975191893a045f26121d174df3ceb9b77ee0c4aa2489e1298",
    "evaluate": "234451d6c2677c4bb638c1806a415509591edc84831764fa0263ef06f9d922e6",
    "phase_at": "26b474fb35eed091a44af4499e1664037ccde52159c51e80408e2a71f4028de9",
    "_eval_phase": "8bc6e4c148bead7f691777fbc2d73dc38603d4caf2f17d70d2d102a3135157f5",
    "value_at": "b221034fc5cf6913666a8d83e743bac3a6954ec9b17d8b9477f17ce61523b68f",
}


def _normalize(value):
    if isinstance(value, CodeType):
        return _signature(value)
    if type(value) is float:
        return ("float", struct.pack("=d", value).hex())
    if type(value) is tuple:
        return ("tuple", tuple(_normalize(item) for item in value))
    return (type(value).__name__, value)


def _signature(code):
    constants = tuple(_normalize(value) for value in code.co_consts[1:])
    return (code.co_code.hex(), constants, code.co_names, code.co_varnames,
            code.co_freevars, code.co_cellvars, code.co_argcount,
            code.co_posonlyargcount, code.co_kwonlyargcount, code.co_exceptiontable.hex())


def _canonical(function, namespace, name):
    if type(function) is FunctionType and function.__globals__ is namespace:
        digest = hashlib.sha256(repr(_signature(function.__code__)).encode("utf-8")).hexdigest()
        if digest == _EXPECTED[name] and function.__defaults__ is None and function.__kwdefaults__ is None:
            return True
    return False


def dependency(namespace, name):
    function = namespace.get(name)
    return namespace, name, function if _canonical(function, namespace, name) else _BLOCKED


def entry_dependency(namespace, name, function):
    captured = {name: function}
    return captured, name, function if _canonical(function, namespace, name) else _BLOCKED


class _FunctionMetadata:
    __slots__ = ("function",)

    def __init__(self, function):
        self.function = function

    def get(self, name):
        return getattr(self.function, name)


def function_metadata(function):
    if type(function) is not FunctionType:
        return []
    source = _FunctionMetadata(function)
    return [(source, name, getattr(function, name)) for name in
            ("__code__", "__defaults__", "__kwdefaults__")]
