"""Reversible binding metadata transaction using CPython ExitStack semantics."""
from contextlib import ExitStack
from dataclasses import dataclass

MISSING = object()


def read(target, name):
    fields = target if isinstance(target, dict) else vars(target)
    return fields.get(name, MISSING)


def write(target, name, value):
    if isinstance(target, dict):
        if value is MISSING:
            target.pop(name, None)
        else:
            target[name] = value
        return
    if value is MISSING:
        delattr(target, name)
    else:
        setattr(target, name, value)


@dataclass(frozen=True)
class Change:
    target: object
    name: str
    before: object
    after: object


def change(target, name, after):
    return Change(target, name, read(target, name), after)


class RollbackFailure(RuntimeError):
    """The owner must stay explicitly unavailable if restoration itself failed."""


def validate(changes):
    keys = [(id(row.target), row.name) for row in changes]
    if len(set(keys)) != len(keys):
        raise RuntimeError("Prepared install writes a metadata slot twice")
    if any(read(row.target, row.name) is not row.before for row in changes):
        raise RuntimeError("Prepared install no longer matches the original descriptors")


def restore(row, errors):
    try:
        if read(row.target, row.name) is not row.before:
            write(row.target, row.name, row.before)
    except BaseException as error:
        errors.append(error)


def commit(changes, setter=write):
    """Caller holds the single install lock and has already prepared all hooks."""
    changes = tuple(changes)
    validate(changes)
    undo, errors, touched = ExitStack(), [], []
    try:
        for row in changes:
            # Register BEFORE the write, including a setter which writes then fails.
            undo.callback(restore, row, errors)
            touched.append(row)
            setter(row.target, row.name, row.after)
    except BaseException:
        undo.close()
        failed = [row for row in touched if read(row.target, row.name) is not row.before]
        if errors or failed:
            raise RollbackFailure("Install metadata rollback did not restore every original slot")
        raise
    else:
        undo.pop_all()
