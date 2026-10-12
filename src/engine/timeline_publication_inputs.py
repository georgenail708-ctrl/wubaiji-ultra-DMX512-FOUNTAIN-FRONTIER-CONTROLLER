"""Publish same-content shells without weakening original mutable-byte gates.

Original inputs_current stays frozen. A foreign caller callback owns its exact
single return/exception. Only original stock input helpers may recover an
identity-only rejection by reading the original complete content signature.
Source metadata uses the existing scalar guard CPython fingerprint discipline;
there is no input memo, token rewrite, save flag, or new renderer eligibility.
"""
from __future__ import annotations

import hashlib
import struct
from types import CodeType, FunctionType

from . import timeline_bake_inputs as _inputs

_EXPECTED = {'inputs_current': 'adda4bedb5c0b119a6038d159f576577d0fd2077c176165ee6d54159772ab248', 'check_cancel': '4a96cf65378c0f89d58ffed66c416eb916879b09514fd306eef9337c648aeb34', '_frames_identity_sig': 'bd9e481897e376875f4a34b000f1d3b5d8ab269c061ea9aa5ef5397c33fd9610', 'input_signature': '0b6be18714f42f1d1302bc0035c987c31e38c74b8b7f7a82b69df807aa50b6ef', '_all_clips': 'd0dd52882f680885358a4cb1ec7a605026ec9c223dc2919d6f13bed109e77035', '_track_clips': '10928ce40f27e91618a7593683ed2daa7263b835369ced15bf4fcaa9224d859d', '_digest_frames': '86554dbc893cc776fbbd12ee7c242244a8d68ac1124cc3acb5daf409833942fd', '_new_content_digest': 'e3cbec9c7621492fff566daaa95abf4009c3a471945c55f0d7a591159b4b66fe', 'fingerprint_metadata': 'ca5d5afe9dbe6de4f09ec1a320fd17d19bda612b042d2ae867563d23092ae0a1'}


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


def _capture(name):
    function = vars(_inputs).get(name)
    if type(function) is not FunctionType or function.__globals__ is not vars(_inputs):
        return None
    digest = hashlib.sha256(repr(_signature(function.__code__)).encode("utf-8")).hexdigest()
    if digest != _EXPECTED[name] or function.__defaults__ is not None or function.__kwdefaults__ is not None:
        return None
    return function, function.__code__


_SOURCE = {name: _capture(name) for name in _EXPECTED}


def _stock_current(current):
    for name, captured in _SOURCE.items():
        if captured is None:
            return False
        function, code = captured
        if vars(_inputs).get(name) is not function or function.__code__ is not code:
            return False
        if function.__defaults__ is not None or function.__kwdefaults__ is not None:
            return False
    return current is _SOURCE["inputs_current"][0]


def publication_inputs_current(engine, inputs, opts, *, current):
    result = current(engine, inputs, opts)
    if result is True or not _stock_current(current):
        return result
    if _inputs._frames_identity_sig(inputs["source"]) == inputs.get("id_sig"):
        return result
    _inputs.check_cancel(opts)
    if engine._pm.project is not inputs["source"]:
        return False
    if engine._timeline_render_runtime_signature_value() != inputs["runtime"]:
        return False
    return _inputs.input_signature(inputs["source"], opts) == inputs["token"]
