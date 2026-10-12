"""One optional original-call scope, without queues, payload copies or sending.

Uses CPython 3.13 ContextVar set/token/reset semantics (PSF documentation);
no upstream implementation copied. Adoption: r185-nm-final-output-tap-plan §9.
None is the original direct-call path and never accesses the ContextVar.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
import sys
from typing import Callable


@dataclass(frozen=True, slots=True, eq=False, kw_only=True)
class AttemptInvocation:
    """Opaque identity per call; equality of record/data cannot merge calls."""

    record: object
    universe_1based: object
    data: object = field(repr=False)
    entered: bool = field(default=False, init=False)


_CURRENT_INVOCATION: ContextVar[AttemptInvocation | None] = ContextVar(
    "qascade_output_attempt_invocation", default=None,
)


def current_invocation() -> AttemptInvocation | None:
    """Read the current call only; no retained last-attempt history."""
    return _CURRENT_INVOCATION.get()


def _mark_entered(invocation: AttemptInvocation, entered: bool) -> None:
    object.__setattr__(invocation, "entered", entered)


def _enter_optional(factory, invocation):
    try:
        manager = factory(invocation)
        manager.__enter__()
        return manager
    except Exception:
        # Optional creation/entry errors cannot skip the original body.
        return None


def _exit_optional(manager, exception_info) -> None:
    try:
        manager.__exit__(*exception_info)
    except Exception:
        # Never retry the body or replace its exception/return value.
        pass


def _restore_current(token, previous) -> None:
    try:
        _CURRENT_INVOCATION.reset(token)
    except Exception:
        # A broken optional context must not mask an original send exception.
        try:
            _CURRENT_INVOCATION.set(previous)
        except Exception:
            pass


def _run_unqualified(body):
    """Construction failure cannot borrow an already-entered outer call."""
    previous, entered = None, False
    try:
        previous = current_invocation()
        if previous is not None:
            entered = previous.entered
            _mark_entered(previous, False)
    except Exception:
        previous = None
    try:
        return body()
    finally:
        if previous is not None:
            _mark_entered(previous, entered)


def invoke_attempt(body: Callable, factory, *, record, universe_1based, data):
    """Run the original zero-arg body once, preserving its return/exception.

    A failed factory/enter leaves this invocation current but unentered. A
    nested body therefore cannot borrow its outer scope. Exit errors are
    isolated, and even a truthy __exit__ never suppresses the body's exception.
    """
    if factory is None:
        return body()
    try:
        invocation = AttemptInvocation(record=record,
                                       universe_1based=universe_1based, data=data)
        previous = current_invocation()
        token = _CURRENT_INVOCATION.set(invocation)
    except Exception:
        return _run_unqualified(body)
    try:
        manager = _enter_optional(factory, invocation)
        _mark_entered(invocation, manager is not None)
        try:
            return body()
        finally:
            _mark_entered(invocation, False)
            if manager is not None:
                _exit_optional(manager, sys.exc_info())
    finally:
        _restore_current(token, previous)
