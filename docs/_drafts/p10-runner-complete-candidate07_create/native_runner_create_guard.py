"""Precise constructor/edit successors retain unchanged original source proofs."""
from types import FunctionType
from . import native_runner_edit_guard as previous
from . import native_runner_scope_guard as scope
from . import native_runner_create_signatures as stamps


def canonical(module):
    name = module.__name__.rsplit('.',1)[-1]
    expected,defaults = stamps.ROLES.get(name),stamps.DEFAULTS.get(name)
    rows = scope.old.functions(module)
    return (expected is not None and defaults is not None and set(key for key,_ in rows) == set(expected)
        and not any(key in vars(module) for key in scope.old.BUILTINS)
        and scope._definitions_current(rows,expected,defaults))


def canonical_classes(module):
    key = module.__name__.rsplit('.',1)[-1]
    wanted = stamps.CLASSES.get(key)
    classes = {value.__name__:value for value in vars(module).values()
        if type(value) is type and value.__module__ == module.__name__}
    return wanted is not None and set(classes) == set(wanted) and all(
        classes[name].__bases__ == bases and set(vars(classes[name])) == set(fields)
        for name,(bases,fields) in wanted.items())


def layouts_current(module):
    wanted = stamps.LAYOUTS.get(module.__name__.rsplit('.',1)[-1])
    return type(wanted) is dict and set(wanted) == {'Request','Report'} and all(
        previous._layout(vars(module).get(name),saved) for name,saved in wanted.items())


def providers_current(expected=None):
    rows = stamps.PROVIDERS
    return (expected is None or rows is expected) and type(rows) is tuple and bool(rows) and all(
        vars(namespace).get(name) is value for namespace,name,value in rows)


class Guard(previous.Guard):
    record_replacement = previous.Guard.record_replacement
    record_addition = previous.Guard.record_addition
    record_host = previous.Guard.record_host
    _old_functions = previous.Guard._old_functions
    _old_function_current = previous.Guard._old_function_current
    _old_classes = previous.Guard._old_classes
    _normalized_class = previous.Guard._normalized_class
    _new_functions_current = previous.Guard._new_functions_current
    _new_classes_current = previous.Guard._new_classes_current
    _new_sources = previous.Guard._new_sources
    current = previous.Guard.current
    _owner_current = previous.Guard._owner_current
    _old_dependencies = previous.Guard._old_dependencies
    _old_namespace_current = previous.Guard._old_namespace_current
    require = previous.Guard.require
    apply = previous.Guard.apply
    seal = previous.Guard.seal

    def _capture(self):
        super()._capture()
        self.providers = stamps.PROVIDERS
        self.globals = [(namespace,name,self if name == '_GUARD'
            and namespace.get('__name__','').endswith('.native_runner_create_install') else value)
            for namespace,name,value in self.globals]


def make_train_admit(guard,binding):
    rows,globals_rows = guard.rows,guard.globals
    fields = ()
    methods = tuple(name for name,value in vars(type(guard)).items() if type(value) is FunctionType)
    train_type = guard.base.module._Train
    def admit(train):
        if not scope._admission_globals(globals_rows) or not scope._admission_functions(rows):
            raise scope.ScopeRejected('Train constructor supplier changed before admission')
        if not scope._admission_instance(guard,methods,fields) or type(train) is not train_type:
            raise scope.ScopeRejected('Train constructor source/owner qualification refused')
        if not _train_sources(guard,binding):
            raise scope.ScopeRejected('Train constructor actual native/source qualification refused')
    def freeze():
        nonlocal fields
        fields = tuple(vars(guard).items())
    return admit,freeze


def _train_sources(guard,binding):
    return (providers_current(guard.providers) and guard._old_functions() and guard._old_classes()
        and guard._new_sources() and guard._old_dependencies() and binding.current())
