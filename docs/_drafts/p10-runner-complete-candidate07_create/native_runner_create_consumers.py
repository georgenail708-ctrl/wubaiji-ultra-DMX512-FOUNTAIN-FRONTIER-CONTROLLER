"""Reviewed actual Panel source seam, separate from original engine stamps."""
from types import FunctionType
from . import native_runner_create_signatures as stamps


def current():
    rows = stamps.CONSUMERS
    if stamps.CONSUMER_OWNER is None or type(rows) is not tuple or not 0 < len(rows) <= 8:
        return False
    if not all(_current(row) for row in rows):
        return False
    return {'add_train','_default_base_speed'} <= {row[1] for row in rows}


def _current(row):
    if type(row) is not tuple or len(row) != 5:
        return False
    owner,name,function,record,globals_rows = row
    if owner is not stamps.CONSUMER_OWNER or type(function) is not FunctionType or type(record) is not tuple or len(record) != 4:
        return False
    actual = function.__code__,function.__defaults__,function.__kwdefaults__,function.__closure__
    if vars(owner).get(name) is not function or not all(a is b for a,b in zip(actual,record)):
        return False
    return type(globals_rows) is tuple and bool(globals_rows) and all(
        type(namespace) is dict and namespace.get(name) is value for namespace,name,value in globals_rows)
