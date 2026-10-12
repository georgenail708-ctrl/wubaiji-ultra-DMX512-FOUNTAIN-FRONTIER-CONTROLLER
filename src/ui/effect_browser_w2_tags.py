# -*- coding: utf-8 -*-
"""src/ui/effect_browser_w2_tags.py — R183 W2-FX #3：标签编辑器。

背景：``EffectPreset.tags`` 早已存在且进检索 hay（``effect_categories``），
但全库**没有任何编辑入口** —— 用户无法给自己的常用效果打"快速/收尾/副歌"这类
自有语义标记。

本模块：卡片右键 →「编辑标签…」→ 既有词表逐项确认 + 自定义新增 → 写用户标签表
（``effect_w2_state.json`` 的 tags 表，与内置 tags 分开存 → 不动库资产，
卸载/换机删档即回原状）。写入后标签**立即进检索 hay**（``_extra_tags`` 钩子），
因此无需重建预设即可搜到。

参考: Surge XT 的 tag 管理语义（GPLv3，仅借鉴交互：词表多选 + 自由新增，
      不引库、不抄代码）；本仓既有词表 = 全库 ``preset.tags`` ∪ 用户标签。
"""
from __future__ import annotations

import logging
from typing import List

from . import effect_browser_w2_base as _base
from . import effect_browser_w2_dialogs as _dlg
from . import effect_browser_w2_store as _store

logger = logging.getLogger(__name__)


def tag_vocabulary() -> List[str]:
    """全库既有标签词表（内置 ∪ 用户，去重保序）。"""
    vocab: List[str] = []
    _add_all(vocab, _builtin_tags())
    _add_all(vocab, _user_tags())
    return vocab


def _add_all(vocab: List[str], tags) -> None:
    for tag in (tags or ()):
        text = str(tag or "").strip()
        if text and text not in vocab:
            vocab.append(text)


def _builtin_tags() -> List[str]:
    """全库内置标签（引擎不可用 → 空表）。"""
    try:
        presets = _base.engine().all_presets()
    except Exception:
        logger.debug("effect_browser_w2_tags: builtin tags failed", exc_info=True)
        return []
    return [str(t) for preset in presets
            for t in (getattr(preset, "tags", None) or ()) if str(t)]


def _user_tags() -> List[str]:
    """用户标签表里的全部标签。"""
    return [str(t) for tags in _store.tags_map().values() for t in tags]


def current_tags(preset_id) -> List[str]:
    """该预设的用户标签（排序后返回，便于显示/断言）。"""
    return sorted(_store.user_tags(preset_id))


def _pick_new_tags(panel, pname: str, current, vocab) -> List[str]:
    """逐项询问词表里还没有的标签 → 返回被选中的标签。"""
    picked: List[str] = []
    for tag in vocab:
        if tag in current:
            continue
        text = (f"「{pname}」添加标签「{tag}」？\n"
                f"当前：{'、'.join(sorted(current)) or '无'}")
        if _dlg.ask_yes_no(panel, "编辑标签", text):
            picked.append(tag)
    return picked


def _ask_extra(panel, pname: str) -> str:
    """自定义新增标签（词表外的自由输入；空 → ""）。"""
    extra = _dlg.ask_text(panel, "编辑标签",
                          f"给「{pname}」加自定义标签（留空跳过）：")
    return str(extra or "").strip()


def _merge_tags(current, picked, extra: str) -> List[str]:
    """旧标签 ∪ 新选标签 ∪ 自定义标签（去重、排序）。"""
    merged = set(current) | set(picked)
    if extra:
        merged.add(extra)
    return sorted(merged)


def edit_tags_dialog(panel, preset_id: str) -> bool:
    """标签编辑器入口；返回是否有改动（取消/无新标签 → False）。"""
    pid = str(preset_id or "")
    if not pid:
        return False
    pname = _base.display_name(panel, pid)
    current = set(_store.user_tags(pid))
    picked = _pick_new_tags(panel, pname, current, tag_vocabulary())
    merged = _merge_tags(current, picked, _ask_extra(panel, pname))
    if merged == sorted(current):
        return False
    if not _store.set_user_tags(pid, merged):
        return False
    _base.refresh_list(panel)
    return True


def set_tags(panel, preset_id, tags) -> bool:
    """直接覆盖写标签（导入/脚本/定测入口）；返回是否有改动。"""
    if not _store.set_user_tags(preset_id, tags):
        return False
    _base.refresh_list(panel)
    return True


__all__ = ["current_tags", "edit_tags_dialog", "set_tags", "tag_vocabulary"]
