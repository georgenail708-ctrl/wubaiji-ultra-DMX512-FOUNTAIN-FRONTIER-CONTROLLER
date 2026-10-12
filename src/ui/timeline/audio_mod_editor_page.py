# -*- coding: utf-8 -*-
"""调制编辑大页（R174#7；用户规格 2026-09-29：调制 clip 左双击唤起，Studio 式
独立页面，内容 = linker 式「识别到的通道类型 → 效果映射」编辑）。

布局（照 track_linker_panel 的映射编辑语义 + studio_panel 的页面骨架）：
  ┌──────────────────────────────────────────────────────────┐
  │ 顶栏：scope 摘要（文件夹/识别通道数/区段数） + 关闭            │
  ├──────────────────────┬───────────────────────────────────┤
  │ 左列：识别到的通道类型  │ 右列：全局参数（量/特征/淡入/淡出）    │
  │ （scope 自动识别，只读  │      + 每通道目标行（类型→波形→频）    │
  │  列表，勾选=启用调制）  │                                    │
  └──────────────────────┴───────────────────────────────────┘

非独立窗口（QtWidget 嵌 main_window 的 dock/stack，Studio 同款懒建模式）。
数据流：set_context(project, mod_state) 注入；edited 信号回 main_window
（与 AudioModTrack.edit_committed 同一条 _on_audio_mod_edited 链）。
"""
from __future__ import annotations

import logging
from typing import Callable, List, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

from ...engine import audio_modulation as _mod
from .. import qascade_theme as _th
from . import audio_mod_mapping_ui as _mapping_ui
from ..workspace_owner import find_project_window

logger = logging.getLogger(__name__)

# R183 成熟化轮③：从 draw_cards.WAVE_CHOICES 派生（单一来源；修前 8 旧项，
# "S&H" 等新波形在本页显示"恒定"，一动下拉即静默改写）
from ..audio_mod_draw.draw_cards import WAVE_CHOICES as _DRAW_WAVE_CHOICES
WAVES = [v for _l, v in _DRAW_WAVE_CHOICES]

#: 参数改动防抖窗口（ms）：连点/连滚只提交一次（照 effect_browser._search_debounce）
DEBOUNCE_MS = 250


def _state_is_blank(state: dict) -> bool:
    """状态是否"无内容"（无 scope/目标/区段/波形 → 可被共享轨实时态覆盖）。"""
    return not ("processors" in state or "processor_mode" in state
                or str(state.get("scope_folder_id", "") or "")
                or state.get("target_ch_types") or state.get("clips")
                or str(state.get("default_wave", "") or ""))


def _live_scope_channels(owner, folder_id: str) -> List[str]:
    """生产兜底：从所属工程自采 scope 识别通道（R181）。

    main_window 注入的 channels 走 ch_types-only 口径；真实工程 clip 帧未
    水合、业务对象无 ch_types 字段时恒空（审计 B3 双层死链）。此处按全量
    口径（ACU 地址 → fixtures 反查）自算一次，纯读、异常给空。
    """
    if not folder_id:
        return []
    try:
        project = _owner_project(owner)
        if project is None:
            return []
        from ...engine.audio_mod_scope import collect_scope_channels_from_project
        return collect_scope_channels_from_project(project, folder_id)
    except Exception:
        logger.debug("live scope channel collect failed", exc_info=True)
        return []


def _owner_project(owner):
    """编辑页所属工程：父窗口时间线 session 优先，其次 ProjectManager 单例。"""
    try:
        tw = getattr(find_project_window(owner), "_timeline_widget", None)
        proj = getattr(tw, "_session", None)
        if proj is not None:
            return proj
    except Exception:
        logger.debug("timeline session read failed", exc_info=True)
    try:
        from ...core.project_manager import ProjectManager
        pm = ProjectManager.instance()
        return getattr(pm, "project", None) if pm is not None else None
    except Exception:
        logger.debug("project fallback read failed", exc_info=True)
        return None


class AudioModEditorPage(QWidget):
    """调制编辑大页（linker 式通道映射 + 全局参数）。"""

    #: 状态提交 (state_dict)——main_window 接 _on_audio_mod_edited 同链
    edited: Optional[Callable] = None
    #: 关闭页请求
    close_requested = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: dict = _mod.normalize_state({})
        self._scope_channels: List[str] = []
        self._scope_folder_id: str = ""
        self._scope_track_id: str = ""
        self._open_at_sec: float = 0.0     # M1：双击秒位沿链留存（set_context 写）
        self._build_ui()
        # ★ R181：参数改动防抖（连点/连滚只提交一次；照 effect_browser 先例）
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.setInterval(DEBOUNCE_MS)
        self._commit_timer.timeout.connect(self._flush_commit)

    # ── UI ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        _mapping_ui.build_page(self)

    def _build_left_column(self) -> QVBoxLayout:
        """左列：scope 识别到的通道类型（勾选=启用为调制目标）。"""
        return _mapping_ui.build_left_column(self)

    def _build_right_column(self) -> QVBoxLayout:
        """右列：全局参数 + 每通道映射行。"""
        return _mapping_ui.build_right_column(self)

    # ── 数据注入 ────────────────────────────────────────────────────────
    def set_context(self, state: dict, scope_channels: List[str],
                    at_sec: float = 0.0) -> None:
        """注入调制状态 + scope 识别通道（每次打开/范围变化时调用）。

        R181 回显修复：scope 识别为空时用工程实时口径兜底（`_live_scope_channels`），
        并把已存 target_ch_types/clip targets 并入列表——关闭重开后勾选/波形
        映射不因识别暂时为空而整页清空。
        M1：`at_sec` = 双击秒位沿链留存（`_open_at_sec`，不参与提交 payload）。
        """
        self._open_at_sec = max(0.0, float(at_sec or 0.0))
        self._commit_timer.stop()   # 重开注入属外部状态，取消未落地的旧编辑
        _mapping_ui.discard_global_form(self)
        src = self._resolve_context_state(state)
        self._state = _mod.normalize_state(src)
        # scope_track_id 不在 state schema 内（normalize 会丢）——单独留存并在提交时
        # 回带，否则编辑页一次提交就把调制轨关联擦掉（旧档误判/绑定丢失）。
        self._scope_track_id = str(src.get("scope_track_id", "") or "")
        self._scope_folder_id = str(self._state.get("scope_folder_id", "") or "")
        self._scope_channels = self._merge_channels(
            list(scope_channels or []), self._live_scope_fallback())
        self._refresh_summary()
        self._refresh_channel_list()
        self._refresh_target_rows()
        _mapping_ui.load_global_form(self)

    def _live_scope_fallback(self) -> List[str]:
        """已绑 scope 时按工程实时口径补识别（未绑定时无 scope 可识别）。"""
        if not self._scope_folder_id:
            return []
        return _live_scope_channels(self, self._scope_folder_id)

    def _resolve_context_state(self, state) -> dict:
        """注入 state 与共享调制轨实时态择完整者（R181 回显链路自愈）。

        生产路径的工程 state 可能暂时为空/缺 scope（编辑监听未接、加载竞态、
        探针直接改 widget 态）；共享轨 widget（timeline 持有）的 state 更接近
        用户实际操作，缺内容时以它为准、缺 scope 绑定则补上。
        """
        src = dict(state) if isinstance(state, dict) else {}
        live = self._live_track_state()
        if not live or _state_is_blank(src):
            return live or src
        for key in ("scope_folder_id", "scope_track_id"):
            if not str(src.get(key, "") or ""):
                src[key] = str(live.get(key, "") or "")
        return src

    def _live_track_state(self) -> dict:
        """父窗口时间线里的共享调制轨 state（找不到给空 dict，绝不抛）。"""
        try:
            tw = getattr(find_project_window(self), "_timeline_widget", None)
            track = getattr(tw, "_audio_mod_track", None)
            if track is None or not hasattr(track, "state"):
                return {}
            return dict(track.state() or {})
        except Exception:
            logger.debug("live track state read failed", exc_info=True)
            return {}

    def _merge_channels(self, supplied: List[str], fallback: List[str]) -> List[str]:
        """识别通道合并：外部注入优先，再并已存显式目标，最后兜底识别。"""
        out: List[str] = []
        for group in (supplied, self._explicit_targets(), fallback):
            for ct in group:
                if ct and ct not in out:
                    out.append(ct)
        return out

    def _refresh_summary(self):
        n_clips = len(self._state.get("clips") or [])
        fid = str(self._state.get("scope_folder_id", "") or "")
        self._summary.setText(
            f"范围: 文件夹 {fid or '（未绑定）'} · 识别通道 {len(self._scope_channels)} "
            f"· 调制区段 {n_clips}")

    def _refresh_channel_list(self):
        """识别通道列表：勾选态 = 在 target_ch_types（或 clip targets 并集）中。"""
        enabled = set(self._enabled_targets())
        self._channel_list.blockSignals(True)
        self._channel_list.clear()
        for ct in self._scope_channels:
            item = QListWidgetItem(ct)
            item.setData(Qt.ItemDataRole.UserRole, ct)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if ct in enabled
                               else Qt.CheckState.Unchecked)
            self._channel_list.addItem(item)
        self._channel_list.blockSignals(False)

    def _explicit_targets(self) -> List[str]:
        """显式目标 = 全局 target_ch_types ∪ 各 clip targets（有序去重）。"""
        out = [str(t) for t in (self._state.get("target_ch_types") or [])]
        for c in self._state.get("clips") or []:
            for t in c.get("targets") or []:
                if t not in out:
                    out.append(t)
        return out

    def _enabled_targets(self) -> List[str]:
        """生效目标 = 显式目标；显式全空 → 回退 scope 识别通道（R181）。

        回退语义与调制驱动一致（audio_mod_driver._scope_addresses 同口径）：
        没有显式勾选时，范围内识别到的通道类型全部是调制对象。
        """
        explicit = self._explicit_targets()
        return explicit if explicit else list(self._scope_channels)

    def _refresh_target_rows(self):
        """每通道映射行：类型 → 波形下拉 + 频率（linker 式逐行编辑）。"""
        _mapping_ui.refresh_target_rows(self)

    # ── 编辑回调 ────────────────────────────────────────────────────────
    def _wave_of(self, ch_type: str) -> str:
        """通道的当前波形：clip targets 命中该通道的任一 clip 波形，否则全局。"""
        for c in self._state.get("clips") or []:
            if ch_type in (c.get("targets") or []) and c.get("wave"):
                return str(c["wave"])
        return str(self._state.get("default_wave", "") or "")

    def _on_param(self, key: str, value):
        self._state[key] = float(value) if key != "feature" else str(value)
        self._schedule_commit()

    def _on_wave(self, ch_type: str, wave: str):
        """linker 式映射：该通道的波形写入全部含它的 clip + 全局 default_wave。"""
        for c in self._state.get("clips") or []:
            targets = c.get("targets") or []
            if not targets or ch_type in targets:
                c["wave"] = str(wave or "")
        self._state["default_wave"] = str(wave or "")
        _mapping_ui.sync_wave_rows(self)
        self._schedule_commit()

    def _on_channel_toggled(self, item: QListWidgetItem):
        """勾选/取消通道 → 更新全局 target_ch_types（隐式全选落成显式清单）。"""
        ct = str(item.data(Qt.ItemDataRole.UserRole) or item.text())
        targets = self._explicit_targets()
        if not targets:
            targets = list(self._scope_channels)   # 隐式全选 → 显式化再应用本次改动
        if item.checkState() == Qt.CheckState.Checked:
            if ct not in targets:
                targets.append(ct)
        else:
            targets = [t for t in targets if t != ct]
        self._state["target_ch_types"] = targets
        self._refresh_target_rows()
        self._schedule_commit()

    def _schedule_commit(self):
        """防抖调度：改动先落 _state，静默 DEBOUNCE_MS 后才真正提交（R181）。"""
        self._commit_timer.start()

    def _flush_commit(self):
        """防抖到期/页面隐藏：执行一次真正的提交（编辑落工程+置脏）。"""
        self._commit_timer.stop()
        self._commit()

    def hideEvent(self, ev):
        """关闭（dock hide）时冲出待提交编辑，防最后一次改动丢失。"""
        try:
            _mapping_ui.flush_global_form(self)
            if self._commit_timer.isActive():
                self._flush_commit()
        finally:
            super().hideEvent(ev)

    def _commit(self):
        self._state = _mod.normalize_state(self._state)
        self._refresh_summary()
        payload = dict(self._state)
        if self._scope_track_id:
            payload["scope_track_id"] = self._scope_track_id
        if callable(getattr(self, "edited", None)):
            try:
                self.edited(payload)
            except Exception:
                logger.warning("audio mod editor commit failed", exc_info=True)
        self._sync_share_track(payload)

    def _sync_share_track(self, payload: dict) -> None:
        """同源回灌：把提交回共享调制轨 widget（否则其陈旧 _state 会覆盖映射编辑）。

        编辑页与调制轨共享 Project.audio_modulation_state；页面提交只写工程，
        调制轨 widget 若不同步，下一次画笔提交就用旧状态覆盖回工程（targets
        丢失——审计 B3 回显链的翻版）。窗口/属性缺失时安全跳过（单测独立构造）。
        """
        try:
            tw = getattr(find_project_window(self), "_timeline_widget", None)
            track = getattr(tw, "_audio_mod_track", None)
            if track is not None and hasattr(track, "absorb_state"):
                track.absorb_state(payload)
        except Exception:
            logger.debug("audio mod editor track sync failed", exc_info=True)
