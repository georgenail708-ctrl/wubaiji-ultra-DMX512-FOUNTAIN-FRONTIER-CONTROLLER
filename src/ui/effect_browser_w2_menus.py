# -*- coding: utf-8 -*-
"""src/ui/effect_browser_w2_menus.py — R183 W2-FX：右键菜单扩展（卡片 + 参数）。

两个既有菜单的**增量区块**，由主文件各加一行调用：
  · 卡片菜单（``_show_preset_menu``）：#3 编辑标签 / #10 使用次数（只读显示）；
  · 参数菜单（``_on_param_bind_menu``）：#13 跟随音频（写/清） / #8 分配到宏。

为什么做成"往传入的 QMenu 追加"而不是自己 ``exec``：既有菜单里已经有批 A 的
另存/收藏/重命名/删除与调度台释放项；再开一个菜单会出现"右键弹出两个框"，
追加区块能保证动作顺序与单一弹窗语义。
"""
from __future__ import annotations

import logging
from typing import List

from . import effect_browser_w2_audio as _audio
from . import effect_browser_w2_base as _base
from . import effect_browser_w2_dialogs as _dlg
from . import effect_browser_w2_macro as _macro
from . import effect_browser_w2_store as _store
from . import effect_browser_w2_tags as _tags

logger = logging.getLogger(__name__)

#: 宏旋钮对单参数的行程倍数上下限（对话框范围）
SCALE_RANGE = (-4.0, 4.0)


# ── 卡片菜单（#3 / #10）──────────────────────────────────────────────────

def extend_card_menu(menu, panel, preset_id: str) -> None:
    """卡片右键菜单追加 W2 区块（编辑标签 + 用户标签/使用次数只读行）。"""
    pid = str(preset_id or "")
    if not pid:
        return
    try:
        menu.addSeparator()
        act = menu.addAction("编辑标签…")
        act.triggered.connect(lambda: _tags.edit_tags_dialog(panel, pid))
        _add_readonly(menu, _tags.current_tags(pid), "用户标签：")
        _add_readonly(menu, f"使用次数：{_store.usage_count(pid)}")
    except Exception:
        logger.debug("effect_browser_w2_menus: extend card menu failed",
                     exc_info=True)


def _add_readonly(menu, value, prefix: str = "") -> None:
    """加一条不可点的信息行（空值不加）。"""
    if isinstance(value, list):
        text = prefix + "、".join(str(v) for v in value) if value else ""
    else:
        text = f"{prefix}{value}"
    if not text:
        return
    action = menu.addAction(str(text))
    action.setEnabled(False)


# ── 参数菜单（#13 / #8）──────────────────────────────────────────────────

def append_param_actions(menu, panel, key: str, label: str = "") -> None:
    """参数右键菜单追加「跟随音频 / 分配到宏 / 从宏移除」。"""
    param_key = str(key or "")
    if not param_key:
        return
    try:
        menu.addSeparator()
        _add_audio_action(menu, panel, param_key)
        macro_act = menu.addAction("分配到宏…")
        macro_act.triggered.connect(
            lambda: assign_macro_dialog(panel, param_key, label))
        if _used_by_macro(panel, param_key):
            clear = menu.addAction("从宏移除")
            clear.triggered.connect(lambda: _macro.clear_macro(panel, param_key))
    except Exception:
        logger.debug("effect_browser_w2_menus: param actions failed", exc_info=True)


def _add_audio_action(menu, panel, param_key: str) -> None:
    """跟随音频的两种文案（已绑定 → 取消；未绑定 → 打开绑定对话框）。"""
    if _audio.follow_audio_enabled(panel, param_key):
        act = menu.addAction("取消跟随音频")
        act.triggered.connect(lambda: _audio.clear_audio_follow(panel, param_key))
        return
    act = menu.addAction("跟随音频…")
    act.triggered.connect(lambda: bind_audio_follow_dialog(panel, param_key))


def _used_by_macro(panel, param_key: str) -> bool:
    """该参数是否已在任一宏槽（决定是否显示"从宏移除"）。"""
    return any(param_key in _macro.mapping_for(panel, m)
               for m in _store.MACRO_KEYS)


# ── 两步问答入口 ─────────────────────────────────────────────────────────

def bind_audio_follow_dialog(panel, param_key: str) -> bool:
    """两步问答：选频段 → 给深度；返回是否写入绑定。"""
    band = _dlg.ask_choice(panel, "跟随音频",
                           f"「{param_key}」跟随哪个音频量？", _audio.follow_bands())
    if not band:
        return False
    depth = _dlg.ask_double(panel, "跟随音频",
                            f"「{param_key}」跟随「{band}」的深度 0..1：",
                            value=0.5, lo=0.0, hi=1.0)
    return _audio.bind_audio_follow(panel, param_key, band,
                                    0.5 if depth is None else depth)


def assign_macro_dialog(panel, param_key: str, label: str = "") -> bool:
    """两步问答：选宏槽 → 给行程倍数；返回是否写入映射。"""
    slots: List[str] = [m.upper() for m in _store.MACRO_KEYS]
    slot, scale = _dlg.ask_choice_and_double(
        panel, "分配到宏",
        f"把「{label or param_key}」分配到哪个宏？", slots,
        value=1.0, lo=SCALE_RANGE[0], hi=SCALE_RANGE[1])
    if not slot or scale is None:
        return False
    return _macro.assign_macro(panel, param_key, slot.lower(), scale)


__all__ = ["SCALE_RANGE", "append_param_actions", "assign_macro_dialog",
           "bind_audio_follow_dialog", "extend_card_menu"]
_ = _base
