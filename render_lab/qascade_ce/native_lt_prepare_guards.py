"""Whole-module code/default/global/cell metadata; no model/numeric execution."""
import hashlib
import struct
import dis
from types import CodeType, FunctionType


def _constant(value):
    if isinstance(value, CodeType):
        return shape(value)
    if type(value) is float:
        return ('float', struct.pack('=d', value).hex())
    if type(value) is tuple:
        return ('tuple', tuple(_constant(item) for item in value))
    if type(value) is frozenset:
        return ('frozenset', tuple(sorted((_constant(item) for item in value), key=repr)))
    return (type(value).__name__, value)


def shape(code):
    return (code.co_code.hex(), tuple(_constant(value) for value in code.co_consts),
            code.co_names, code.co_varnames, code.co_freevars, code.co_cellvars,
            code.co_argcount, code.co_posonlyargcount, code.co_kwonlyargcount,
            code.co_flags, code.co_exceptiontable.hex())


def digest(function):
    return hashlib.sha256(repr(shape(function.__code__)).encode()).hexdigest()


def locate(module, qualified):
    parts = qualified.split('.')
    target = module
    for part in parts[:-1]:
        target = vars(target)[part]
    return target, parts[-1], vars(target).get(parts[-1])


def globals_names(code):
    names = {instruction.argval for instruction in dis.get_instructions(code) if instruction.opname == 'LOAD_GLOBAL'}
    for child in code.co_consts:
        if isinstance(child, CodeType):
            names.update(globals_names(child))
    return names


def stamp(function, *, owner_cell=False):
    keyword = function.__kwdefaults__
    globals_ = function.__globals__
    cells = tuple((name, cell, cell.cell_contents) for name, cell in
                  zip(function.__code__.co_freevars, function.__closure__ or ())
                  if not owner_cell or name != '_lt_owner')
    return (function, function.__code__, function.__defaults__, keyword,
            None if keyword is None else tuple(keyword.items()), globals_,
            tuple((name, globals_.get(name, MISSING)) for name in globals_names(function.__code__) if name != '_OWNER'), cells)


MISSING = object()


def same(function, captured):
    if type(function) is not FunctionType:
        return False
    if not all(a is b for a, b in zip((function, function.__code__, function.__defaults__,
                                      function.__kwdefaults__), captured[:4])):
        return False
    keyword = function.__kwdefaults__
    if keyword is not None:
        if len(keyword) != len(captured[4]):
            return False
        if any(name not in keyword or keyword[name] is not value for name, value in captured[4]):
            return False
    return (function.__globals__ is captured[5]
            and all(function.__globals__.get(name, MISSING) is value for name, value in captured[6])
            and all(cell.cell_contents is value for _name, cell, value in captured[7]))


def defaults(function, expected):
    keyword = function.__kwdefaults__
    items = None if keyword is None else tuple(keyword.items())
    return _constant(function.__defaults__) == expected[0] and _constant(items) == expected[1]
