"""Shared per-channel mask math without a core-to-engine dependency.

An already installed CE owns the math. Public state and container lifecycles
remain in their original modules; native failures retain the same fallback.
"""
import sys

import numpy as np

_DEFAULT_RESTORATION_RULE = None


def domain():
    return sys.modules.get("qascade_ce.native_output_domain")


def _fallback():
    module = domain()
    if module is not None:
        module.note_fallback()


def _mask_python(operation, left, right):
    active = np.asarray(left, bool)
    other = None if right is None else np.asarray(right, bool)
    result = _mask_value(operation, active, other, (left, right))
    count = np.count_nonzero(other & ~active) if operation == 4 else np.count_nonzero(result)
    return result, int(count)


def _mask_value(operation, active, other, original):
    if operation == 0:
        return active.copy()
    if operation == 1:
        return active & other
    if operation in (2, 4):
        return active | other
    if operation == 3:
        return active & ~other
    if operation == 5:
        return other & (active if active.any() else other)
    return np.asarray(original[0]) != np.asarray(original[1])


def mask_math(operation, left, right=None):
    module = domain()
    if module is not None and module.NATIVE is not None:
        try:
            return module.NATIVE.mask(operation, left, right)
        except Exception:
            _fallback()
    return _mask_python(operation, left, right)


def mask_count(value):
    module = domain()
    if module is not None and module.NATIVE is not None:
        try:
            return module.NATIVE.mask_count(value)
        except Exception:
            _fallback()
    return _mask_python(0, value, None)[1]


def selected_mask_rows(values):
    rows = tuple(values)
    module = domain()
    if module is not None and module.NATIVE is not None:
        try:
            return module.NATIVE.mask_counts(rows)[1]
        except Exception:
            _fallback()
    return _selected_rows_python(rows)


def _selected_rows_python(rows):
    return np.asarray([index for index, value in enumerate(rows)
                       if np.count_nonzero(np.asarray(value, dtype=bool))], dtype=np.int32)


def mask_and(left, right):
    return mask_math(1, left, right)[0]


def mask_or(left, right):
    return mask_math(2, left, right)[0]


def mask_without(active, consumed):
    return mask_math(3, active, consumed)[0]


def merge_consumed(previous, added):
    return mask_math(4, previous, added)


def filtered_active(active, rule, explicit):
    return mask_math(1 if explicit else 5, active, rule)[0]


def default_restoration_rule(function):
    """Register only the explicitly decorated public default, by strong ref."""
    global _DEFAULT_RESTORATION_RULE
    _DEFAULT_RESTORATION_RULE = function
    return function


def is_default_restoration_rule(function):
    underlying = getattr(function, "__func__", function)
    return _DEFAULT_RESTORATION_RULE is not None and underlying is _DEFAULT_RESTORATION_RULE


def metadata_group_ids(chains):
    groups, identities = {}, []
    for chain in chains:
        identities.append(groups.setdefault(tuple(chain or ()), len(groups)))
    return np.asarray(identities, np.int32), list(groups)


def restorable_takeover(loser_chain, winner_chain):
    groups, _chains = metadata_group_ids((loser_chain, winner_chain))
    module = domain()
    if module is not None and module.NATIVE is not None:
        try:
            return module.NATIVE.restorable(groups)
        except Exception:
            _fallback()
    return bool(groups[0] != groups[1])
