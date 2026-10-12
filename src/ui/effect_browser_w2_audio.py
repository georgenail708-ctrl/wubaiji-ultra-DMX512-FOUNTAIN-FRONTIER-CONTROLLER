# -*- coding: utf-8 -*-
"""src/ui/effect_browser_w2_audio.py — R183 W2-FX #13：静态参数 → 跟随音频。

用户价值：库里大量参数是"静态值"（亮度/速度/幅度），现场想让它们跟着音乐动，
但只有少数预设内建了音频反应。本模块给**任意参数**加一层"跟随音频"绑定：
    参数右键 →「跟随音频…」→ 选频段（low/mid/high/rms/peak/beat_phase）+ 深度。

折算（纯函数 ``audio_factor``）：``倍数 = 1 + depth * (level - 0.5) * 2`` ——
电平 0.5 为中性（倍数=1.0，与未绑定等价），高电平放大、低电平收缩，depth 控幅度；
结果再乘到参数当前值上，**不改参数控件**（绑定是渲染期折算，不污染用户参数）。

落点：``_params_with_audio`` 末尾一行调用（该管道已经注入 ``_audio_data``，
见 effect_browser:5387）→ 渲染与预览两条路径同时生效，零新增写通道。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from . import effect_browser_w2_base as _base
from . import effect_browser_w2_store as _store

logger = logging.getLogger(__name__)

#: 中性电平（音频帧的 rms/peak/band 归一值域 0..1）
NEUTRAL_LEVEL = 0.5


def audio_factor(level, depth) -> float:
    """纯函数：音频电平 → 参数倍数（level=0.5 为 1.0；非法输入 → 1.0）。"""
    lvl = _base.num(level)
    dep = _base.num(depth)
    if lvl is None or dep is None:
        return 1.0
    depth_v = max(0.0, min(1.0, dep))
    level_v = max(0.0, min(1.0, lvl))
    return max(0.0, 1.0 + depth_v * (level_v - NEUTRAL_LEVEL) * 2.0)


def expand_audio_follow(panel, preset_id, params):
    """渲染前折算：绑定参数按音频帧调制；**未绑定 → 原对象返回**（零开销）。"""
    binds = _audio_binds(preset_id)
    frame = _audio_frame_of(params)
    if not binds or frame is None:
        return params
    out = None
    for key, rec in binds.items():
        if key not in params:
            continue
        value = _modulated(params[key], rec, frame)
        if value is None:
            continue
        if out is None:
            out = dict(params)
        out[key] = value
    return params if out is None else out


def _audio_binds(preset_id) -> dict:
    try:
        return dict(_store.audio_follow(preset_id))
    except Exception:
        logger.debug("effect_browser_w2_audio: read binds failed", exc_info=True)
        return {}


def _audio_frame_of(params) -> Optional[dict]:
    if not isinstance(params, dict):
        return None
    frame = params.get("_audio_data")
    return frame if isinstance(frame, dict) else None


def _modulated(cur, rec, frame) -> Any:
    """单个参数折算结果（非数值/绑定非法 → None）。"""
    value = _base.num(cur)
    if value is None:
        return None
    factor = audio_factor(frame.get(str(rec.get("band", ""))), rec.get("depth"))
    return value * factor


def bind_audio_follow(panel, param_key: str, band: str = "",
                      depth: float = 0.5) -> bool:
    """写绑定（band 非白名单 → 清该参数的绑定）；纯逻辑入口。"""
    return bool(_store.set_audio_follow(_base.preset_id_of(panel),
                                        str(param_key or ""), band, depth))


def clear_audio_follow(panel, param_key: str) -> bool:
    """取消该参数的跟随音频绑定。"""
    return bind_audio_follow(panel, param_key, "", 0.5)


def follow_audio_enabled(panel, param_key: str) -> bool:
    """该参数是否已绑定（菜单文案/勾选态用）。"""
    binds = _audio_binds(_base.preset_id_of(panel))
    return str(param_key or "") in binds


def follow_bands() -> list:
    """可选频段（与 ``_audio_frame_dict`` 的键一致）。"""
    return list(_store.AUDIO_BANDS)


__all__ = ["NEUTRAL_LEVEL", "audio_factor", "bind_audio_follow",
           "clear_audio_follow", "expand_audio_follow", "follow_audio_enabled",
           "follow_bands"]
