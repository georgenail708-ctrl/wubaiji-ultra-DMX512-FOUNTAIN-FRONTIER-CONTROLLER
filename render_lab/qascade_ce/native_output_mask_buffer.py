"""Mask-only byte qualification; public/foreign conversions stay unchanged."""
import numpy as np


def mask_operand(value, operation, convert):
    if (type(operation) is int and 0 <= operation <= 5
            and type(value) is np.ndarray and value.dtype == np.bool_):
        return np.ascontiguousarray(value.view(np.uint8))
    return convert(value, np.uint8)
