"""Validated user tool preferences; show content remains with its original owner."""
from __future__ import annotations

import math

from PyQt6.QtCore import QSettings


def settings(page, parent=None):
    store = QSettings("Qascade", "OperatingPreferences")
    if parent is not None:
        store.setParent(parent)
    store.setFallbacksEnabled(False)
    store.beginGroup("v1/" + page)
    return store


def text_value(store, key, default=""):
    value = store.value(key, default)
    return value if isinstance(value, str) else default


def choice_value(store, key, choices, default):
    value = text_value(store, key, default)
    return value if value in choices else default


def bool_value(store, key, default):
    value = store.value(key, default)
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        parsed = {"true": True, "false": False, "1": True, "0": False}
        return parsed.get(value.strip().lower(), default)
    return default


def number_value(store, key, default, minimum, maximum):
    value = store.value(key, default)
    if isinstance(value, bool):
        return default
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(value) or not minimum <= value <= maximum:
        return default
    if isinstance(default, int):
        return int(value) if value.is_integer() else default
    return value
