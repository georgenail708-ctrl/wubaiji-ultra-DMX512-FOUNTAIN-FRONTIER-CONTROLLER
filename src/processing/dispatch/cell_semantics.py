# -*- coding: utf-8 -*-
"""调度台单元格的**语义化显示**（纯函数，无 Qt、无 IO、无 UI 依赖）。

背景（用户实报）
----------------
旧监视器把**任何**取值不一致的聚合格（``spread > 1``）一律显示「混合」。
但"多台设备 DMX 值不同"是**正常现象**（条带/扇面/渐变），并不是"多种效果"。
用户要求按格子的**语义内容**显示：

* **programmer（编程器）格** → 显示条带（strip）的**起止相对值 + 翼数/块大小**，
  例如某条带从低到高、翼数 4、块 2，应显示 ``"128-255 W4 B2"``（而不是「混合」）。
* **effect / matrix 格** → 显示**该通道上正在编辑的效果名**。
* 「混合」**只应**在**真正存在多个不同效果**（或同一效果有**多个不同设置**）时出现，
  **不能**仅仅因为 DMX 数值不同就显示。

用户原话（决策依据，逐字引用）::

    「混合」must ONLY appear when there really are multiple different effects,
    or the same effect with multiple different settings (not merely differing
    DMX values).

本模块只做"一格 → (显示文本, 状态码)"，供 ``table_model`` / 渲染层调用。
为**避免导入环**（``table_model`` 会 import 本模块），本文件**不** import
``table_model``；其中 ``format_value`` 的格式在此**逐字复刻**（见 ``_format``）。

显示文本格式
------------
1. **条带格**（``panel == "programmer"`` 且带条带信息、非单一平值）::

       {base}-{target}[ W{wings}][ B{block}][ {mode}]

   * ``{base}`` / ``{target}`` 用当前单位格式化（dmx/pct/hex，同 ``format_value``）。
   * ``W{wings}`` 仅当 ``wings > 1`` 时出现；``B{block}`` 仅当 ``block > 1`` 时出现。
   * 模式提示：``one_way`` → ``↑``（单向/斜坡）；``two_way`` → ``↕``（双向/往返）。
     用**紧凑箭头**而非 ``1way``/``2way``，与工程内既有箭头风格一致、占位更小。
   * 例：``"128-255 W4 B2 ↑"``。缺项自动省略（如 ``"0-255 ↕"``）。
   * ``strip_mode == "flat"``（或起止相同且无翼无块）视作**平值**，落回普通取值显示。

2. **效果格**：单一效果名 → 直接显示该名；多个不同效果 → ``"{n} 效果"`` + 状态 ``mixed``。

3. **普通通道格**：按单位显示 DMX 值；``None`` → 空。

状态码
------
``"value"`` / ``"zero"`` / ``"empty"`` / ``"mixed"`` / ``"na"``，含义与
``table_model.cell_display`` 一致（渲染层据此选色）。

关于 ``spread``（★ 重要决策）
-----------------------------
``spread > 1``（聚合格内取值种类数）**单独不构成「混合」**：多台设备数值不同是
正常输出（条带/扇面），本函数默认仍显示**数值**。仅当调用方显式传入
``legacy_spread_mixed=True`` 时才回退到旧行为（``spread > 1`` → 「混合」），
以兼容尚未迁移的调用点。
"""
from __future__ import annotations

from typing import Any, Optional, Tuple

# ── 状态码（与 table_model.cell_display 保持一致）──────────────────────────
ST_VALUE = "value"
ST_ZERO = "zero"
ST_EMPTY = "empty"
ST_MIXED = "mixed"
ST_NA = "na"

#: 「混合」文本（等价 ``table_model.MIXED_LABEL``；此处本地定义以免导入环）
MIXED_LABEL = "混合"

#: 条带模式提示符（紧凑可读；见 _strip_display）
_STRIP_MODE_HINTS = {"one_way": "↑", "two_way": "↕"}

#: 条带模式 → 紧凑箭头提示
_MODE_HINTS = {"one_way": "↑", "two_way": "↕"}

#: 效果面板（这些面板的格子应显示效果名）
_EFFECT_PANELS = frozenset({"effect", "matrix"})


def _format(value: Any, unit: str = "dmx") -> str:
    """DMX 值（0-255）按单位格式化——**逐字复刻** ``fixture_table.format_value``。

    - dmx : 原值 0-255（整数字符串）
    - pct : 0-100 四舍五入
    - hex : 两位大写十六进制（与 一致）
    - None / 非法 → 破折号 "—"

    复刻而非 import：``table_model`` 依赖本模块，反向 import 会成环。
    """
    if value is None:
        return "—"
    try:
        v = int(value)
    except (TypeError, ValueError):
        return "—"
    v = max(0, min(255, v))
    if unit == "pct":
        return str(int(round(v * 100.0 / 255.0)))
    if unit == "hex":
        return f"{v:02X}"
    return str(v)


def _getattr_safe(obj: Any, name: str, default: Any) -> Any:
    """安全 getattr：属性缺失 / 抛异常 / 为 None 时返回 ``default``。"""
    try:
        val = getattr(obj, name, default)
    except Exception:
        return default
    return default if val is None else val


def _as_int(value: Any, default: int = -1) -> int:
    """尽力转 int；失败返回 ``default``（不抛）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = -1.0) -> float:
    """尽力转 float；失败返回 ``default``（不抛）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _raw_effect_names(cell: Any) -> Tuple[str, ...]:
    """从 `effect_names` / `effect_name` 取原始名字（顺序保留，允许重复）。"""
    raw: list = []
    try:
        many = _getattr_safe(cell, "effect_names", None) or ()
        raw.extend(str(x) for x in many if str(x or "").strip())
    except Exception:
        pass
    try:
        one = str(_getattr_safe(cell, "effect_name", "") or "").strip()
        if one:
            raw.append(one)
    except Exception:
        pass
    return tuple(raw)


def _distinct_effect_names(cell: Any) -> Tuple[str, ...]:
    """该格涉及的**不同**效果名（去重保序）。>1 个 = 真·多效果。"""
    out: list = []
    for nm in _raw_effect_names(cell):
        if nm not in out:
            out.append(nm)
    return tuple(out)


def _strip_is_meaningful(mode: str, base: int, target: int,
                         wings: int, block: int) -> bool:
    """是否值得显示为"条带"。

    ★ `flat` = 全部设备同一值（无展开）→ **一律不显示条带**，回落普通数值显示
      （即使残存 base/target/翼块字段也不该把它当条带——那是脏状态）。
    ★ 模式为空/未知时，只有真的"有展开"（起点≠终点，或翼/块 >1）才算条带。
    """
    if mode == "flat":
        return False
    if mode in ("one_way", "two_way"):
        return True
    if base >= 0 and target >= 0 and base != target:
        return True
    return wings > 1 or block > 1


def _strip_display(cell: Any, unit: str) -> Optional[Tuple[str, str]]:
    """programmer 条带显示：`起点-终点 [W翼] [B块] [模式]`；无条带 → None。

    例：`128-255 W4 B2 ↑`（one_way）/ `0-255 ↕`（two_way）/ `0-255 ↑`。
    纯 flat（起点=终点、无翼块）返回 None，让调用方回落普通数值显示。
    """
    try:
        mode = str(_getattr_safe(cell, "strip_mode", "") or "")
        base = _as_int(_getattr_safe(cell, "strip_base", -1), -1)
        target = _as_int(_getattr_safe(cell, "strip_target", -1), -1)
        wings = _as_int(_getattr_safe(cell, "strip_wings", 0), 0)
        block = _as_int(_getattr_safe(cell, "strip_block", 0), 0)
    except Exception:
        return None
    if not _strip_is_meaningful(mode, base, target, wings, block):
        return None
    # 端点是 DMX 值 → 按当前单位显示（pct/hex 下也必须是同一读法）
    parts = [f"{_format(base, unit)}-{_format(target, unit)}"]
    if wings > 1:
        parts.append(f"W{wings}")
    if block > 1:
        parts.append(f"B{block}")
    hint = _STRIP_MODE_HINTS.get(mode, "")
    if hint:
        parts.append(hint)
    return (" ".join(parts), ST_VALUE)



def _effect_slot_display(cell: Any, unit: str) -> Tuple[str, str]:
    """效果槽（无物理通道）→ 用节点强度 ``level`` 兜底为 DMX 值。

    与 ``table_model._effect_slot_display`` 同判据：未在跑且 ``level < 0`` → 空；
    ``level < 0``（未知，但在跑）→ 满值 255（保留旧可见性）。
    """
    live = bool(_getattr_safe(cell, "live", True))
    lv = _as_float(_getattr_safe(cell, "level", -1.0), -1.0)
    if not live and lv < 0:
        return ("", ST_EMPTY)
    dmx = 255 if lv < 0 else int(round(max(0.0, min(1.0, lv)) * 255))
    return (_format(dmx, unit), ST_VALUE if dmx > 0 else ST_ZERO)


def resolve_cell_display(cell, value, owned, unit: str = "dmx",
                         spread: Optional[int] = None, *,
                         legacy_spread_mixed: bool = False) -> Tuple[str, str]:
    """一格 → (显示文本, 状态码)。**纯函数，绝不抛异常**（内部 try/except 兜底）。

    分派顺序（见各子函数，本函数只做编排 —— 便于门禁与单测）：
      ① None → na；② legacy 开关；③ programmer 条带；④ 效果名；⑤ 普通取值。
    """
    try:
        if cell is None:
            return ("", ST_NA)
        if legacy_spread_mixed and _as_int(spread, 0) > 1:
            return (MIXED_LABEL, ST_MIXED)
        by_panel = _display_by_panel(cell, unit)
        if by_panel is not None:
            return by_panel
        return _display_by_value(cell, value, unit)
    except Exception:
        return _fallback_display(value, unit)


def _display_by_panel(cell, unit: str) -> Optional[Tuple[str, str]]:
    """面板语义优先：programmer 条带 → effect/matrix 效果名。不适用返回 None。"""
    panel = str(_getattr_safe(cell, "panel", "") or "")
    if panel == "programmer":
        strip = _strip_display(cell, unit)
        if strip is not None:
            return strip
    names = _distinct_effect_names(cell)
    if panel in _EFFECT_PANELS or names:
        return _effect_name_display(names)
    return None


def _effect_name_display(names: Tuple[str, ...]) -> Optional[Tuple[str, str]]:
    """效果名显示：多名 → "{n} 效果"+mixed（真·多效果）；单名 → 该名；无名 → None。"""
    if len(names) > 1:
        return (f"{len(names)} 效果", ST_MIXED)
    if len(names) == 1:
        # 同名多设置也走这里 → 显示名字、不报混合。
        return (names[0], ST_VALUE)
    return None


def _display_by_value(cell, value, unit: str) -> Tuple[str, str]:
    """无面板语义时的普通取值显示。"""
    ch_type = str(_getattr_safe(cell, "ch_type", "") or "")
    virtual = bool(_getattr_safe(cell, "virtual", False))
    if value is None:
        if not ch_type and not virtual:
            return _effect_slot_display(cell, unit)
        return ("", ST_EMPTY)
    try:
        positive = int(value) > 0
    except (TypeError, ValueError):
        positive = False
    return (_format(value, unit), ST_VALUE if positive else ST_ZERO)


def _fallback_display(value, unit: str) -> Tuple[str, str]:
    """兜底：任何意外都不允许冒泡到绘制循环。"""
    try:
        if value is None:
            return ("", ST_EMPTY)
        return (_format(value, unit), ST_VALUE)
    except Exception:
        return ("", ST_NA)


__all__ = [
    "ST_VALUE", "ST_ZERO", "ST_EMPTY", "ST_MIXED", "ST_NA",
    "MIXED_LABEL", "resolve_cell_display",
]
