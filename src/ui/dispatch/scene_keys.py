# -*- coding: utf-8 -*-
"""场景**键盘绑定**（按键 → 复用鼠标触发逻辑；MA 控台式即时创作）。

用户 2026-09-14：
  "每个 scene 可以绑定键盘上的按键，按键通过**模拟鼠标点击的逻辑**去跟鼠标一样实现，
   比如一个 scene 处于 flash 模式，绑定一个按键 P，我按下 P，则触发一次。
   绑定键盘的映射随项目持久化，入口就加在每个 scene 的右击页面…
   同时这个也支持框选一批 scene 去同时映射多个效果到同一按键。"

设计要点（可维护性）：
  · **不重写触发语义** —— 按下/松开分别调调度台既有的 `_on_store_pressed` /
    `_on_store_released`（flash）与 `_on_store_activated`（latch），与鼠标完全同一条路；
  · **一个按键可映射多个 scene**（用户明确要求）→ 建索引 `{键: [cid, ...]}`，
    按下时逐个触发；
  · 本模块只做"键 → 场景"的解析与规范化（纯函数 + 小类），Qt 事件过滤在 panel 侧。
"""
from __future__ import annotations

from typing import Dict, List

#: 允许绑定的键名最大长度（防误绑整段文本）
KEY_MAX_LEN = 24
from ...processing.dispatch.effect_cache import (  # noqa: E402
    RESERVED_KEYS,
)


def normalize_key(key) -> str:
    """按键名归一化：委托 **processing 层**的 `effect_cache.normalize_key`（单一真源）。

    ★ 为什么委托：数据层（`EffectCacheStore.set_key_binding`、`from_dict` 校验）
      也必须归一化，两处各写一份必然漂移。UI 侧的额外职责只有"Qt 键码 → 键名"
      （见 `key_name_from_qt`）与保留键拦截。
    返回 "" = 不可绑定。
    """
    try:
        from ...processing.dispatch.effect_cache import normalize_key as _norm
        return _norm(key)
    except Exception:
        return ""


def key_name_from_qt(key_code) -> str:
    """Qt 键码 → 键名（`Qt.Key_P` → "P"；功能键 → "F5"）；取不到返回 ""。"""
    try:
        from PyQt6.QtCore import Qt
        return str(Qt.Key(int(key_code)).name)
    except Exception:
        return ""


def bindings_of(caches) -> Dict[str, List[str]]:
    """`{键: [场景 id, ...]}` —— 由场景列表反查（一个键可绑多个场景）。"""
    out: Dict[str, List[str]] = {}
    for c in (caches or ()):
        try:
            key = _scene_key(c)
        except Exception:
            continue
        cid = str(getattr(c, "id", "") or "")
        if key and cid:
            out.setdefault(key, []).append(cid)
    return out


def scenes_for_key(caches, key) -> List[str]:
    """该键当前映射到的场景 id 列表（保序）。"""
    return list(bindings_of(caches).get(normalize_key(key), ()))


def key_of_scene(caches, cid: str) -> str:
    """该场景绑定的键（未绑定返回 ""）。"""
    for c in (caches or ()):
        if str(getattr(c, "id", "") or "") == str(cid or ""):
            return _scene_key(c)
    return ""


def _scene_key(c) -> str:
    """场景的绑定键（优先用其 `effective_key()` 归一化读口）。"""
    fn = getattr(c, "effective_key", None)
    if callable(fn):
        try:
            return str(fn() or "")
        except Exception:
            return ""
    try:
        return normalize_key(getattr(c, "key_binding", ""))
    except Exception:
        return ""


def describe(bindings: Dict[str, List[str]]) -> str:
    """绑定表 → 一行可读摘要（状态栏用）。"""
    if not bindings:
        return "未绑定按键"
    parts = [f"{k}×{len(v)}" for k, v in sorted(bindings.items())]
    return "已绑定 " + "、".join(parts)


__all__ = ["normalize_key", "key_name_from_qt", "bindings_of", "scenes_for_key",
           "key_of_scene", "describe", "RESERVED_KEYS"]
