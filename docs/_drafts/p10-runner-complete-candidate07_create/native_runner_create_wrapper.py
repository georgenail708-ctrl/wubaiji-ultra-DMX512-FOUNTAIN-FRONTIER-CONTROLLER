"""Keyword-compatible exact successor; refuse changed suppliers before dispatch."""
import builtins
from functools import wraps
from . import native_runner_scope_guard as scope
from . import native_runner_create_guard as guard_module


def wrapper(context,original,operation,constructor=False):
    all_ok = builtins.all
    globals_rows,function_rows = context[0].globals,context[0].rows
    providers = context[0].providers
    admit = context[3] if constructor else context[2]
    admit_code,admit_defaults,admit_keywords,admit_closure = (
        admit.__code__,admit.__defaults__,admit.__kwdefaults__,admit.__closure__)
    error = scope.ScopeRejected
    @wraps(original)
    def call(owner,*args,**kwargs):
        if not all_ok(namespace.get(name,scope.old.MISSING) is value for namespace,name,value in globals_rows):
            raise error('Complete create globals changed before public dispatch')
        if not all_ok(function.__code__ is record[0] and function.__defaults__ is record[1]
                and function.__kwdefaults__ is record[2] for _,_,function,record in function_rows):
            raise error('Complete create function changed before public dispatch')
        if not (admit.__code__ is admit_code and admit.__defaults__ is admit_defaults
                and admit.__kwdefaults__ is admit_keywords and admit.__closure__ is admit_closure):
            raise error('Complete create admission changed before public dispatch')
        if not guard_module.providers_current(providers):
            raise error('Complete create canonical builtin/provider changed before dispatch')
        admit(owner)
        return operation(context,owner,(args,kwargs),original)
    return call
