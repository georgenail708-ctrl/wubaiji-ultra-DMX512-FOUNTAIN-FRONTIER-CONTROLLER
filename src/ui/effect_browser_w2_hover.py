# -*- coding: utf-8 -*-
"""src/ui/effect_browser_w2_hover.py — R183 W2-FX #11：悬停即时试听。

交互：鼠标在卡片上停留 **400ms** → 临时播放该预设（浏览像逛采样器）；
移出卡片/移出列表 → 立即停止。**不落参数**：试听状态用独立的 track_type
（``w2hover``）注入 ``_external_states``，参数用预设默认值 —— 界面上正在编辑
的参数一个字节都不改；停止时若没有其它驱动在跑，收口到既有 ``stop_external``
（清层停表），因此与 A 批的召回过渡、切片、音频桥都零冲突。

默认**关闭**（工具栏「试听」开关，偏好持久化在用户档）：现场误触发比少一个
功能更糟 —— 鼠标扫过列表就把台面上的效果换掉是不可接受的。

参考: 采样器浏览语义（Vital/Serum 的预设浏览器即"点一下试听"）；实现不引任何
      第三方组件，直接复用本仓 external 播放路径（``_tick_play`` 25ms 定时器）。
"""
from __future__ import annotations

import logging
import time
from typing import Optional, Tuple

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer

from . import effect_browser_w2_base as _base
from . import effect_browser_w2_store as _store

logger = logging.getLogger(__name__)

#: 停留阈值（毫秒）
HOVER_MS = 400
#: 试听参与设备上限（防大工程 hover 卡顿）
HOVER_FIXTURES = 32
#: 试听专用 track_type（与手动/外部/调度台驱动键空间隔离）
HOVER_TRACK = "w2hover"


class HoverWatcher(QObject):
    """列表视口 hover 监听：停留满阈值试听；移出/移开即停。"""

    def __init__(self, panel):
        super().__init__(panel)
        self._panel = panel

    def eventFilter(self, obj, event):                     # noqa: N802 (Qt 命名)
        try:
            etype = event.type()
            if etype == QEvent.Type.MouseMove:
                self._on_move(event)
            elif etype in (QEvent.Type.Leave, QEvent.Type.Hide):
                hover_stop(self._panel)
        except Exception:
            logger.debug("effect_browser_w2_hover: filter failed", exc_info=True)
        return False

    def _on_move(self, event) -> None:
        view = getattr(self._panel, "_preset_list", None)
        if view is None:
            return
        index = view.indexAt(event.position().toPoint())
        preset_id = str(index.data(Qt.ItemDataRole.UserRole) or "") if index.isValid() else ""
        if preset_id:
            hover_enter(self._panel, preset_id)
        else:
            hover_stop(self._panel)


def install(panel) -> None:
    """把 hover 监听装到列表视口（鼠标跟踪开启；面板销毁随之销毁）。"""
    watcher = HoverWatcher(panel)
    view = getattr(panel, "_preset_list", None)
    if view is not None:
        view.viewport().installEventFilter(watcher)
        view.viewport().setMouseTracking(True)
    panel._w2_hover_watcher = watcher


def _timer(panel) -> QTimer:
    """停留计时器（单例挂面板）。"""
    timer = getattr(panel, "_w2_hover_timer", None)
    if timer is not None:
        return timer
    timer = QTimer(panel)
    timer.setSingleShot(True)
    timer.setInterval(HOVER_MS)
    timer.timeout.connect(lambda: _fire(panel))
    panel._w2_hover_timer = timer
    return timer


def hover_enter(panel, preset_id: str) -> None:
    """停到某卡片上：重启停留计时（同一卡片且计时中 → 不重排）。"""
    try:
        if not _store.pref("hover_preview", False):
            return
        pid = str(preset_id or "")
        if pid == str(getattr(panel, "_w2_hover_id", "") or ""):
            if getattr(panel, "_w2_hover_timer_active", False):
                return
        if _busy_with_other(panel):
            return
        panel._w2_hover_id = pid
        panel._w2_hover_timer_active = True
        _timer(panel).start()
    except Exception:
        logger.debug("effect_browser_w2_hover: enter failed", exc_info=True)


def hover_stop(panel) -> None:
    """移出/停止：取消计时并撤掉试听（不落参数）。"""
    try:
        timer = getattr(panel, "_w2_hover_timer", None)
        if timer is not None:
            timer.stop()
        panel._w2_hover_timer_active = False
        if getattr(panel, "_w2_hover_active", False):
            panel._w2_hover_active = False
            drop_state(panel)
    except Exception:
        logger.debug("effect_browser_w2_hover: stop failed", exc_info=True)


def set_enabled(panel, on: bool) -> None:
    """开/关试听（关掉即刻停止进行中的试听）。"""
    _store.set_pref("hover_preview", bool(on))
    if not on:
        hover_stop(panel)


def _other_keys(panel) -> list:
    """非试听的活跃驱动键（真实播放/调度台）。"""
    states = getattr(panel, "_external_states", None)
    if not isinstance(states, dict):
        return []
    return [k for k in list(states) if k[0] != HOVER_TRACK]


def _busy_with_other(panel) -> bool:
    return bool(_other_keys(panel))


def _drop_keys(panel) -> None:
    states = getattr(panel, "_external_states", None)
    if not isinstance(states, dict):
        return
    for key in [k for k in list(states) if k[0] == HOVER_TRACK]:
        states.pop(key, None)


def drop_state(panel) -> None:
    """撤掉试听状态：无其它驱动 → 走 ``stop_external``（清层停表）。"""
    _drop_keys(panel)
    if _other_keys(panel):
        try:
            panel._refresh_quick_effect_lists()
        except Exception:
            logger.debug("effect_browser_w2_hover: refresh quick lists failed")
        return
    try:
        panel.stop_external()
    except Exception:
        logger.debug("effect_browser_w2_hover: stop_external failed", exc_info=True)


def _fire(panel) -> None:
    """停留满阈值：建临时 external 状态，让既有 ``_tick_play`` 渲染它。"""
    panel._w2_hover_timer_active = False
    if not _store.pref("hover_preview", False):
        return
    if _busy_with_other(panel):
        return
    preset_id = str(getattr(panel, "_w2_hover_id", "") or "")
    made = hover_state(panel, preset_id)
    if made is None:
        return
    try:
        key, state = made
        states = panel._external_states
        _drop_keys(panel)
        states[key] = state
        panel._is_playing = True
        panel._w2_hover_active = True
        if not panel._play_timer.isActive():
            panel._play_timer.start()
        _base.set_status(panel, f"试听：{_base.display_name(panel, preset_id)}")
    except Exception:
        logger.debug("effect_browser_w2_hover: fire failed", exc_info=True)


def hover_state(panel, preset_id: str) -> Optional[Tuple[tuple, dict]]:
    """构造 ``(key, state)``；预设/设备/输出域备不齐 → None。"""
    preset = _preset(panel, preset_id)
    fixtures = _fixtures(panel)
    if preset is None or not fixtures:
        return None
    params = _default_params(panel, preset)
    group_ids = _group_ids(panel, params)
    if not group_ids:
        return None
    return ((HOVER_TRACK, group_key(group_ids)),
            _state_dict(panel, preset, preset_id, params, group_ids))


def _preset(panel, preset_id: str):
    try:
        return _base.engine().get_preset(preset_id)
    except Exception:
        logger.debug("effect_browser_w2_hover: preset lookup failed")
        return None


def _fixtures(panel) -> list:
    try:
        return list(panel._get_active_fixtures() or [])[:HOVER_FIXTURES]
    except Exception:
        logger.debug("effect_browser_w2_hover: fixtures failed")
        return []


def _default_params(panel, preset) -> dict:
    """预设默认参数 + 与界面一致的三个作用域开关（试听不读界面参数值）。"""
    params = dict(preset.default_params() or {})
    try:
        params["_app_direction"] = panel._get_app_direction()
        params["_app_scope"] = panel._get_app_scope()
        params["spatial_scope"] = panel._get_spatial_scope()
    except Exception:
        logger.debug("effect_browser_w2_hover: scope params failed")
    return params


def _group_ids(panel, params) -> list:
    try:
        return list(panel._resolve_output_group_ids(params) or [])
    except Exception:
        logger.debug("effect_browser_w2_hover: group ids failed")
        return []


def _state_dict(panel, preset, preset_id: str, params: dict, group_ids: list) -> dict:
    """external state（结构与 ``play_preset_for_type`` 同形）。"""
    ch_types = [str(c) for c in (getattr(preset, "target_channels", None) or ())]
    try:
        base_ids = list(panel._resolve_output_base_order_ids(params, group_ids)
                        or group_ids)
    except Exception:
        base_ids = list(group_ids)
    return {"preset_id": preset_id, "params": params, "ch_types": ch_types,
            "sort_mode": "index", "group_ids": list(group_ids),
            "base_order_ids": base_ids, "speed_factor": 1.0,
            "virtual_t": 0.0, "last_real_t": time.monotonic(),
            "_ltp_takeover_pending": True, "recalled": False}


def group_key(group_ids) -> str:
    """与主面板同源的设备组键（复用 ``_group_key_from_ids``）。"""
    try:
        from .effect_browser import _group_key_from_ids
        return str(_group_key_from_ids(group_ids))
    except Exception:
        return "grp:" + "|".join(str(g) for g in (group_ids or ()))


__all__ = ["HOVER_FIXTURES", "HOVER_MS", "HOVER_TRACK", "HoverWatcher",
           "drop_state", "group_key", "hover_enter", "hover_state", "hover_stop",
           "install", "set_enabled"]
