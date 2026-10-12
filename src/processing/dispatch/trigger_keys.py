# -*- coding: utf-8 -*-
"""按键名归一化的**单一真源**（数据层与 UI 层共用）。

为什么独立成模块：`EffectCacheStore.set_key_binding`/`from_dict` 需要校验它，
UI 层（`src/ui/dispatch/scene_keys.py`）也需要同一个判据 —— 数据层不得 import ui，
故真源必须落在 processing 层；UI 侧只额外做"Qt 键码 → 键名"的转换。
"""
from __future__ import annotations

from typing import FrozenSet

#: 不可绑定的键（保留给系统/文本编辑）
RESERVED_KEYS: FrozenSet[str] = frozenset({
    "TAB", "ESC", "ESCAPE", "RETURN", "ENTER", "SPACE", "BACKSPACE", "DELETE",
    "UP", "DOWN", "LEFT", "RIGHT", "HOME", "END", "PAGEUP", "PAGEDOWN",
    "CTRL", "ALT", "SHIFT", "META", "WIN", "CAPSLOCK", "INSERT",
    "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
})

#: 键名最大长度（防误绑整段文本）
KEY_MAX_LEN = 24


def _strip_qt_prefix(name: str) -> str:
    """去掉 Qt 键名前缀（`KEY_P` → `P`）。"""
    return name[4:] if name.startswith("KEY_") else name


def _is_bindable(name: str) -> bool:
    """单字符字母数字 / F13+ 功能键 = 可绑定。"""
    if len(name) == 1 and name.isalnum():
        return True
    return bool(name.startswith("F") and name[1:].isdigit())


def normalize_key(key) -> str:
    """按键归一化；返回 "" = 不可绑定（空/超长/保留键/非字母数字）。"""
    raw = _strip_qt_prefix(str(key or "").strip().upper())
    if not raw or len(raw) > KEY_MAX_LEN or raw in RESERVED_KEYS:
        return ""
    return raw if _is_bindable(raw) else ""


__all__ = ["RESERVED_KEYS", "KEY_MAX_LEN", "normalize_key"]
