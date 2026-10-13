"""Resolve only a strongly owned, fixed-source filter wrapper for old guards."""
import sys
from types import FunctionType


def original_filter_function(name, function):
    module = sys.modules.get('qascade_ce.native_filter_prepare_owner')
    retained = None if module is None else vars(module).get('SCOPE_RESOLVER')
    if type(retained) is not tuple or len(retained) != 6:
        return function
    resolver, code, defaults, keyword, owner, admission = retained
    if not _resolver_current(module, retained):
        return function
    if not _admission_current(admission, owner):
        return function
    return resolver(name, function)


def _resolver_current(module, retained):
    resolver, code, defaults, keyword, owner, admission = retained
    if type(resolver) is not FunctionType or vars(module).get('original_for') is not resolver:
        return False
    if resolver.__code__ is not code or resolver.__defaults__ is not defaults or resolver.__kwdefaults__ is not keyword:
        return False
    return resolver.__globals__ is vars(module) and vars(module).get('_OWNER') is owner


def _admission_current(captured, owner):
    scope = sys.modules.get('qascade_ce.native_filter_prepare_scope')
    if type(captured) is not tuple or len(captured) != 4:
        return False
    function, code, defaults, keyword = captured
    if type(function) is not FunctionType or scope is None or vars(scope).get('owner_current') is not function:
        return False
    if function.__code__ is not code or function.__defaults__ is not defaults or function.__kwdefaults__ is not keyword:
        return False
    return function.__globals__ is vars(scope) and function(owner)
