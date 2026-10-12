# -*- coding: utf-8 -*-
"""Draw 大页面板（R182 用户定形态）—— 左栏源卡库 + 画布 + 顶栏（应用/关闭）。

布局：
    ┌──────────────────────────────────────────────────────┐
    │ 顶栏：标题 + 摘要（源卡/规则/连线数）+ 全览 + 应用 + 关闭 │
    ├────────────────┬─────────────────────────────────────┤
    │ 左栏源卡库      │ 卡片画布（世界坐标/缩放/平移/连线）    │
    └────────────────┴─────────────────────────────────────┘
数据流：`set_context(state, scope_channels)` 注入（state → 图 = decompile）；
「应用」= compile_graph → `apply_requested(payload)` → main_window 既有
`_on_audio_mod_edited` 提交链（与底部编辑器共享 Project.audio_modulation_state）。
"""
from __future__ import annotations

import logging
from typing import Callable, List, Optional

from PyQt6.QtCore import QPointF
from PyQt6.QtWidgets import QWidget
from ..workspace_owner import find_project_window
from .draw_graph import default_rule_types
from .draw_palette import source_feature_of, template_spec_of
from . import draw_workspace_ui as _workspace

logger = logging.getLogger(__name__)

class AudioModDrawPanel(QWidget):
    """调制 Draw 大页（卡片画布 + 连线；图 ↔ 调制状态双向往返）。"""

    #: 应用（图编译后的 payload）—— main_window 接 _on_audio_mod_edited 同链
    applied: Optional[Callable] = None
    #: 关闭页请求（main_window 接 dock.hide）
    close_requested = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state: dict = {}
        self._scope_track_id: str = ""
        self._scope_channels: List[str] = []
        self._scope_fixture_count: int = 0   # 轮⑦：scope 真灯数（0=未知→8 示意）
        self._rule_types: List[str] = []
        self._open_at_sec: float = 0.0     # M1：双击秒位沿链留存（set_context 写）
        self._dirty_hint: bool = False     # 画布与已应用态不一致（撤销后也置位）
        self._build_ui()

    # ── UI ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        _workspace.build_workspace(self)

    def _build_top_bar(self) -> QWidget:
        return _workspace.build_top_bar(self)

    def _canvas_fit(self) -> None:
        self._canvas.zoom_to_fit()

    def _on_undo_state(self, can_undo: bool, can_redo: bool) -> None:
        """栈状态 → 顶栏撤销/重做按钮置灰（QUndoStack canUndoChanged 联动）。"""
        self._undo_btn.setEnabled(bool(can_undo))
        self._redo_btn.setEnabled(bool(can_redo))

    def _on_graph_changed(self) -> None:
        """图变更（含 undo/redo 回放）→ 置「未应用」脏提示 + 刷新摘要。"""
        self._dirty_hint = True
        self._refresh_summary()

    # ── 数据注入 / 提交 ─────────────────────────────────────────────────
    def set_context(self, state: dict, scope_channels: List[str],
                    at_sec: float = 0.0, *, project=None) -> None:
        """注入调制状态 + scope 识别通道；state → 画布图（decompile）。

        M1：`at_sec` = 双击秒位沿链留存（`_open_at_sec`，供定位/未来游标联动，
        不参与编译 payload）。
        轮⑦：`project` = 计算 scope 真灯数用（缺省沿父链定位工程主窗，
        undo 回灌链不传也能取到）；结果存 `_scope_fixture_count` 并推给画布
        （`canvas._scope_fixture_count`）→ spread 轨画真灯数相位点（0=未知回退
        8 示意，见 draw_phase_viz）。
        """
        from ...engine.audio_modulation import normalize_state
        from .draw_decompile import graph_from_state as decompile_state
        self._open_at_sec = max(0.0, float(at_sec or 0.0))
        raw = state if isinstance(state, dict) else {}
        # ★ R182 审计修：scope_track_id 不在 normalize schema（会被丢弃），
        # 打开时从原始 state 单独留存 → 应用时回带（对齐映射页做法）。
        self._scope_track_id = str(raw.get("scope_track_id", "") or "")
        self._state = normalize_state(raw)
        if self._scope_track_id:
            self._state["scope_track_id"] = self._scope_track_id
        self._scope_channels = [str(c) for c in (scope_channels or []) if str(c)]
        if project is None:   # 浮动dock的window是dock；工程仍由原主窗持有。
            try:
                project = getattr(find_project_window(self), "_project", lambda: None)()
            except Exception:
                project = None
        self._scope_fixture_count = self._compute_scope_fixture_count(project)
        self._canvas._scope_fixture_count = self._scope_fixture_count
        self._rule_types = self._merged_rule_types()
        self._canvas._target_types = tuple(self._rule_types)
        self._palette.set_rule_types(self._rule_types)
        self._canvas.set_graph(decompile_state(self._state))
        self._processor_draft.reset(raw)
        self._processor_chain.set_feedback(None)
        self._processor_chain.refresh()
        if self._processor_draft.mode == "chain":
            self._workspace_tabs.setCurrentWidget(self._processor_chain)
        else:
            self._workspace_tabs.setCurrentWidget(self._workspace_split)
        self._dirty_hint = False   # 文档重注入 = 与应用态一致
        self._apply_error, self._apply_note = "", ""
        self._refresh_summary()

    def _compute_scope_fixture_count(self, project) -> int:
        """scope 真灯数（轮⑦）：scope 通道类型 → 地址并集 → 命中设备数（0=未知）。

        全程复用现成件（只读）：通道类型 = main_window 注入的 `scope_channels`
        （`collect_scope_channel_types` 口径）；地址收集 = driver
        `_ch_type_addr_map`（与调制驱动同源，weakref+patch_rev 缓存）；scope 内
        计数 = spread `fixture_rows` + `_hit_fixture_ords` 序位表去重数（与
        `address_ordinals` 返回的 scope 内设备数同一表达式）。异常 → 0（识别
        只是增强，不阻断）。
        """
        try:
            if project is None or not self._scope_channels:
                return 0
            from ...engine.audio_mod_driver import _ch_type_addr_map
            from ...engine.audio_mod_spread import (_hit_fixture_ords,
                                                    fixture_rows)
            ch_map = _ch_type_addr_map(project)
            # ★ 驱动同源口径：`_ch_type_addr_map` 的键已 lower（driver
            #   `_fixture_channel_addrs`），查表前必须 strip+lower（与
            #   `_scope_index` 的 `ch_map.get(str(ct).strip().lower())` 一致），
            #   否则大写通道类型（真工程 def 里的 R/G/B）漏计整批灯。
            scope = {a for ct in self._scope_channels
                     for a in (ch_map.get(str(ct).strip().lower()) or set())}
            if not scope:
                return 0
            ords = _hit_fixture_ords(fixture_rows(project), scope)
            return len({v for v in ords.values()})
        except Exception:
            logger.debug("scope fixture count failed", exc_info=True)
            return 0

    def _merged_rule_types(self) -> List[str]:
        """规则卡可选类型：scope 识别/全量目录 ∪ 已有规则的通道（回显保底）。"""
        return _workspace.merged_rule_types(self)

    def current_graph(self) -> dict:
        return self._canvas.graph()

    def compile_payload(self) -> dict:
        """画布图 → 提交 payload（含 scope_track_id 回带，走共享状态真源）。"""
        from .draw_compile import compile_payload
        base = dict(self._state)
        base.update(self._processor_draft.payload())
        return compile_payload(self._canvas.graph(), base)

    def apply_now(self) -> None:
        """应用：编译 → `applied` 回调（main_window 提交链）+ 回灌共享调制轨。

        回灌必要性同编辑页（`audio_mod_editor_page._sync_share_track`）：调制轨
        widget 持有陈旧 _state，下一次画笔提交会用旧状态覆盖本页编译的 rules。
        """
        _workspace.apply_graph(self)

    def _sync_share_track(self, payload: dict) -> Optional[bool]:
        """把编译结果回灌时间线的共享调制轨 widget（窗口/属性缺失时安全跳过）。"""
        return _workspace.sync_share_track(self, payload)

    # ── 摘要 ────────────────────────────────────────────────────────────
    def _refresh_summary(self) -> None:
        _workspace.refresh_summary(self)

    # ── 建卡（点击 / 拖入）──────────────────────────────────────────────
    def _center_world(self) -> tuple:
        c = self._canvas._s2w(QPointF(self._canvas.width() / 2.0,
                                      self._canvas.height() / 2.0))
        return (c.x(), c.y())

    def _on_entry_clicked(self, payload: str) -> None:
        """左栏点击：画布中心建卡（邻近自动避让）。"""
        self.add_payload_at(payload, *self._center_world())

    def add_payload_at(self, payload: str, wx: float, wy: float):
        """按 payload 在画布点建卡；返回节点或 None（未知 payload → None）。

        R182：`fx:<effect_key>` = 效果卡（目标通道默认全部勾选 scope 识别
        通道——用户可点 chip 增删）；`rule:<ch>` 旧形态兼容（单目标效果卡）。
        """
        from . import draw_graph as _dc
        from .draw_palette import effect_key_of_payload
        graph = self._canvas.graph()
        x, y = _dc.free_spot(graph, wx, wy)
        head, _, tail = str(payload or "").partition(":")
        if head == "source":
            feat = source_feature_of(tail)
            return self._canvas.add_source(feat, x, y)
        if head == "template":
            feat, phase = template_spec_of(tail)
            # phase 并入建卡一次性字段（extra）→ 入 undo 快照，重做后不丢相位
            return self._canvas.add_source(feat or "beat_strength", x, y, tail,
                                           extra={"phase": float(phase)})
        fx = effect_key_of_payload(payload)
        if fx:
            return self._canvas.add_effect(
                fx, x, y, ch_types=self._default_targets())
        if head == "rule":
            return self._canvas.add_rule(tail, x, y)
        return None

    def _default_targets(self) -> list:
        """新效果卡默认目标：scope 识别通道（空则全量目录前 4 项）。"""
        chans = [str(c) for c in (self._scope_channels or []) if str(c)]
        if chans:
            return chans
        from .draw_cards import DEFAULT_CH_TYPES
        return list(DEFAULT_CH_TYPES[:4])

    def _on_drag_started(self, _payload: str) -> None:
        """拖拽开始（画布 dragEnter 会 accept MIME；此处无需额外状态）。"""

    # ── 用户预设库（M4；存储纯逻辑在 engine.audio_mod_preset_store）──────
    def _refresh_presets(self) -> None:
        """预设库列表 ← 存储层（库损坏/缺失 → 空表不炸）。"""
        try:
            from ...engine.audio_mod_preset_store import list_presets
            self._palette.set_presets(list_presets())
        except Exception:
            logger.debug("preset list failed", exc_info=True)

    def _on_preset_save(self, name: str) -> None:
        """保存当前画布组合为命名预设（未应用的画布也按当前态存）。"""
        try:
            from ...engine.audio_mod_preset_store import save_preset
            state = self.compile_payload()
            ok = save_preset(name, state)
        except Exception:
            logger.warning("preset save failed", exc_info=True)
            ok = False
        if ok:
            self._refresh_presets()
        self._preset_feedback("已保存预设" if ok else "保存失败", name)

    def _on_preset_rename(self, old: str, suggested: str) -> None:
        """重命名预设（QInputDialog 预填旧名；照 scene_menu 改名范式）。"""
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self, "重命名预设", f"「{old}」的新名称：", text=suggested)
        if not ok:
            return
        try:
            from ...engine.audio_mod_preset_store import rename_preset
            ok = rename_preset(old, text)
        except Exception:
            logger.warning("preset rename failed", exc_info=True)
            ok = False
        if ok:
            self._refresh_presets()
            self._preset_feedback("已重命名", text)
        elif text.strip() == old.strip():
            self._refresh_presets()
            self._preset_feedback("名称未变化", old)   # 同名=无操作（非错误）
        else:
            self._preset_feedback("重命名失败（新名为空/占用/写入失败）", text)

    def _on_preset_delete(self, name: str) -> None:
        """删除预设（QMessageBox 确认；照调度台卡片删除范式）。"""
        from PyQt6.QtWidgets import QMessageBox
        if QMessageBox.question(
                self, "删除预设", f"删除预设「{name}」？"
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            from ...engine.audio_mod_preset_store import delete_preset
            ok = delete_preset(name)
        except Exception:
            logger.warning("preset delete failed", exc_info=True)
            ok = False
        if ok:
            self._refresh_presets()
        self._preset_feedback("已删除预设" if ok else "删除失败", name)

    def load_preset(self, name: str) -> None:
        """加载命名预设到画布（不自动应用——顶栏「应用」才提交；置脏）。"""
        try:
            from ...engine.audio_mod_preset_store import load_preset
            state = load_preset(name)
        except Exception:
            logger.warning("preset load failed", exc_info=True)
            state = None
        if not isinstance(state, dict):
            self._preset_feedback("预设读取失败", name)
            return
        self._load_state_into_canvas(state)
        self._preset_feedback("已载入预设", name)

    def _load_state_into_canvas(self, state: dict) -> None:
        """预设 state → 画布图（保留当前 scope 关联；mark 未应用）。"""
        from .draw_decompile import graph_from_state
        try:
            graph = graph_from_state(state)
        except Exception:
            logger.warning("preset decompile failed", exc_info=True)
            return
        self._canvas.set_graph(graph)
        if "processors" in state or "processor_mode" in state:
            self._processor_draft.reset(state)
            self._processor_chain.refresh()
        self._dirty_hint = True
        self._refresh_summary()

    def _on_preset_import(self) -> None:
        """导入 JSON 交换文件（许可/版本门槛 + 合并模式在存储层）。"""
        from PyQt6.QtWidgets import QFileDialog
        path, _sel = QFileDialog.getOpenFileName(
            self, "导入音频调制预设", "", "JSON 预设 (*.json);;全部文件 (*)")
        if not path:
            return
        try:
            from ...engine.audio_mod_preset_store import import_json
            report = import_json(path, merge=True)
        except Exception:
            logger.warning("preset import failed", exc_info=True)
            self._preset_feedback("导入失败", "")
            return
        if report.get("ok"):
            self._refresh_presets()
            self._preset_feedback(
                f"已导入 {len(report.get('imported') or [])} 条", "")
        else:
            self._preset_feedback(f"导入失败：{report.get('error', '')}", "")

    def _on_preset_export(self) -> None:
        """导出全部预设到 JSON 交换文件。"""
        from PyQt6.QtWidgets import QFileDialog
        path, _sel = QFileDialog.getSaveFileName(
            self, "导出音频调制预设", "audio_mod_presets.json",
            "JSON 预设 (*.json)")
        if not path:
            return
        try:
            from ...engine.audio_mod_preset_store import export_json
            report = export_json(path)
        except Exception:
            logger.warning("preset export failed", exc_info=True)
            self._preset_feedback("导出失败", "")
            return
        if report.get("ok"):
            self._preset_feedback(f"已导出 {report.get('count', 0)} 条", "")
        else:
            self._preset_feedback(f"导出失败：{report.get('error', '')}", "")

    def _preset_feedback(self, text: str, name: str) -> None:
        """预设操作回执（追加进摘要行；不弹窗，静默优先）。"""
        tag = f"「{name}」" if name else ""
        self._refresh_summary()
        self._summary.setText(f"{self._summary.text()} · {text}{tag}")
