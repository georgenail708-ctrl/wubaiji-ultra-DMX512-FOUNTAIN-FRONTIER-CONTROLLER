# -*- coding: utf-8 -*-
"""MonitorPanel W2 行为层（R183 W2；深查② §四 #3/#5/#6/#7/#8/#11/#13/#14/#16）。

从 monitor_panel.py 拆出（文件 ≤500 行门禁）：冻结、标注档、组折叠刷新、
电平/统计/录制/sparkline/对照视图的刷新推进，导出、blink、反馈文本。

约束：本层任何异常都不得拖垮 20Hz 刷新链（``_refresh_extras`` 整体 try 包）；
所有对 ``_channel_grid``/``_nm``/``_pm`` 的访问都做存在性守卫（offscreen
测试会喂 stub）。
"""
from __future__ import annotations

import time

from .monitor_annotations import annotation_spans
from .monitor_blink import BlinkController
from .monitor_export import export_current_domain
from .monitor_groups import row_groups_for_universe
from .monitor_recorder import FrameRecorder
from .monitor_stats import frame_stats, format_stats, stats_due
from .monitor_vu import meter_value

ANNOT_OFF, ANNOT_NAME, ANNOT_ADDR = 0, 1, 2
REC_SLIDER_INTERVAL = 0.5     # 滑条量程刷新节流（秒）


class MonitorPanelExtrasMixin:
    """MonitorPanel 的 W2 扩展行为（状态字段在 ``_init_extras`` 建立）。"""

    # ── 初始化 ───────────────────────────────────

    def _init_extras(self) -> None:
        self._manual_frozen = False
        self._scrubbing = False
        self._annot_mode = ANNOT_OFF
        self._groups_on = False
        self._group_sig = None
        self._record_on = True
        self._recorder = FrameRecorder()
        self._input_frame = None
        self._input_universe = 0
        self._stats_ts = float("-inf")
        self._rec_slider_ts = float("-inf")
        self._blink = None
        self._spark = None

    def _wire_extras(self) -> None:
        """装配后接线（``_setup_ui`` 末尾调用）。"""
        self._freeze_btn.toggled.connect(self._on_freeze_toggled)
        self._annot_btn.clicked.connect(self._cycle_annotation)
        self._group_btn.toggled.connect(self._on_groups_toggled)
        self._rec_btn.toggled.connect(self._on_record_toggled)
        self._timeline.valueChanged.connect(self._on_timeline)
        self._export_btn.clicked.connect(lambda: self._do_export_csv())
        self._blink_timer.timeout.connect(self._tick_blink)
        # ★ 清查⑧A P1-5：面板销毁时释放 blink 写入的 tester 层（修前 stop()
        #   从不被调到 → 255 残留 active=True 直到手动清层；与 chase 的
        #   teardown_chase 对称）。捕获自身引用，不回查已删面板。
        try:
            self.destroyed.connect(lambda _obj=None, _s=self: _teardown_blink(_s))
        except Exception:
            pass
        grid = self._channel_grid
        grid.set_context_menu_callback(self._on_grid_context_menu)
        self._sync_recorder_widgets()

    # ── 冻结（#5）───────────────────────────────

    def _on_freeze_toggled(self, checked: bool) -> None:
        self._manual_frozen = bool(checked)
        self._apply_freeze_state()

    def _apply_freeze_state(self) -> None:
        """定格 = 手动冻结 ∪ 回放拖动；角标文案区分两种定格来源。"""
        frozen = self._manual_frozen or self._scrubbing
        self._channel_grid.set_freeze_label(
            "回放" if self._scrubbing else "冻结")
        self._channel_grid.set_frozen(frozen)
        if not self._scrubbing and self._freeze_btn.isChecked() != frozen:
            self._freeze_btn.setChecked(frozen)
        self._set_feedback("画面已定格（继续收数）" if frozen else "恢复实时刷新")

    # ── 标注（#13）──────────────────────────────

    def _cycle_annotation(self) -> None:
        """标注档循环：关 → 灯具名 → 地址 → 关。"""
        self._annot_mode = (self._annot_mode + 1) % 3
        self._apply_annotations()
        self._set_feedback(("标注: 关", "标注: 灯具名", "标注: 地址")[self._annot_mode])

    def _apply_annotations(self) -> None:
        if self._annot_mode == ANNOT_OFF:
            self._channel_grid.set_annotations({})
            return
        fixtures, defs = self._project_lists()
        spans = annotation_spans(fixtures, defs, self._current_universe,
                                 use_address=(self._annot_mode == ANNOT_ADDR))
        self._channel_grid.set_annotations(
            spans, use_address=(self._annot_mode == ANNOT_ADDR))

    # ── 组折叠（#6）─────────────────────────────

    def _on_groups_toggled(self, checked: bool) -> None:
        self._groups_on = bool(checked)
        self._group_sig = None
        self._refresh_group_view(force=True)
        self._set_feedback("按灯具分组：开" if checked else "按灯具分组：关")

    def _project_lists(self):
        """(fixtures, fixture_defs)；无工程/stub 返回空表对。"""
        pm = getattr(self, "_pm", None)
        proj = getattr(pm, "project", None) if pm is not None else None
        if proj is None:
            return [], []
        return (getattr(proj, "fixtures", None) or [],
                getattr(proj, "fixture_defs", None) or [])

    def _current_group_sig(self, fixtures) -> tuple:
        pm = getattr(self, "_pm", None)
        proj = getattr(pm, "project", None) if pm is not None else None
        return (int(self._current_universe),
                int(getattr(proj, "patch_rev", 0) or 0), len(fixtures))

    def _refresh_group_view(self, force: bool = False) -> None:
        """按需重算组表（配接/域变化才重算；未开分组直接退出）。"""
        if not self._groups_on:
            if force:
                self._channel_grid.set_row_groups({})
            return
        fixtures, defs = self._project_lists()
        sig = self._current_group_sig(fixtures)
        if not force and sig == self._group_sig:
            return
        self._group_sig = sig
        self._channel_grid.set_row_groups(
            row_groups_for_universe(fixtures, defs, self._current_universe))

    # ── 刷新推进（20Hz 链）──────────────────────

    def _refresh_extras(self, live, shown, raw) -> None:
        """扩展区推进（异常全吞，绝不拖垮监视器主链）。

        帧口径：``live``=峰值保持后的实时流（电平条/录制/sparkline 要实时；
        定格时照旧记录现场），``shown``=当前显示帧（统计跟画面一致，
        回放定格时统计的就是被定格/回放的那一帧）。
        """
        try:
            self._update_vu(live, raw)
            now = time.monotonic()
            self._update_stats(shown, now)
            self._refresh_group_view()
            self._recorder_tick(live, now)
            if self._spark is not None:
                self._spark.maybe_push(live)
            self._update_io_view(live)
        except Exception:
            pass

    def _update_vu(self, data, raw) -> None:
        grid = self._channel_grid
        chans = grid.get_selected_channels()
        cursor = int(getattr(grid, "_cursor", 0) or 0)
        src = raw if raw is not None else data
        self._vu.set_level(meter_value(src, chans, cursor),
                           meter_value(data, chans, cursor))

    def _update_stats(self, data, now: float) -> None:
        if not stats_due(self._stats_ts, now):
            return
        self._stats_ts = now
        self._stats_label.setText(format_stats(frame_stats(data)))

    def _update_io_view(self, data) -> None:
        has_in = (self._input_frame is not None
                  and self._input_universe == self._current_universe)
        self._io_view.set_frames(
            self._input_frame if has_in else None, data, has_input=has_in)

    # ── 录制 / 回放（#14）───────────────────────

    def _recorder_tick(self, data, now: float) -> None:
        if self._record_on:
            self._recorder.push(data)
        if now - self._rec_slider_ts < REC_SLIDER_INTERVAL:
            return
        self._rec_slider_ts = now
        self._sync_recorder_widgets()

    def _sync_recorder_widgets(self) -> None:
        """滑条量程/启用态与缓冲同步；拖动中不打断当前值。"""
        top = self._recorder.slider_max()
        self._timeline.setEnabled(top > 0)
        if self._timeline.maximum() != top:
            self._timeline.setRange(0, top)
        if not self._scrubbing:
            self._timeline.setValue(top)
        self._rec_label.setText(
            f"{self._recorder.duration_seconds():.1f}s")

    def _on_record_toggled(self, checked: bool) -> None:
        self._record_on = bool(checked)
        self._set_feedback("录制中" if checked else "录制已暂停")

    def _on_timeline(self, value: int) -> None:
        """拖动回放：定格显示该帧；拖到最右端恢复实时。

        只在"拖动↔回实时"切换点才改冻结态与反馈——程序化 setValue（量程
        同步）也会打到本槽，若无条件调 ``_apply_freeze_state`` 会把导出/录制
        等反馈文本周期性刷掉（0.5s 一次）。
        """
        if self._recorder.count <= 0:
            return
        at_end = int(value) >= self._recorder.slider_max()
        if (not at_end) == self._scrubbing:
            return                      # 状态未变：不重复定格/不刷反馈
        self._scrubbing = not at_end
        if not at_end:
            self._channel_grid.show_frame(self._recorder.frame_at(int(value)))
        self._apply_freeze_state()
        self._rec_label.setText(
            f"{int(value) / 20.0:.1f}s" if self._recorder.count else "0.0s")

    def _on_raw_in(self, universe, data) -> None:
        """入网原始帧缓存（供输入/输出对照；只存当前域，防无界增长）。"""
        try:
            if int(universe) == int(self._current_universe):
                self._input_frame = data
                self._input_universe = int(universe)
        except Exception:
            pass

    # ── 导出（#8）───────────────────────────────

    def _do_export_csv(self, path: str = "") -> str:
        """导出当前域 CSV；返回写盘路径（取消/无数据返回空串）。"""
        data = self._channel_grid.get_data()
        if data is None or len(data) == 0:
            self._set_feedback("导出失败：无可导出帧")
            return ""
        try:
            out = export_current_domain(
                self, data, self._current_universe,
                snapshot=getattr(self._channel_grid, "_snapshot", None),
                path=path)
        except Exception as exc:                       # noqa: BLE001
            self._set_feedback(f"导出失败: {exc}")
            return ""
        self._set_feedback(f"已导出 512 行 → {out}" if out else "导出已取消")
        return out

    # ── blink（#10）─────────────────────────────

    def _make_blink(self, channels):
        """创建 blink 控制器（未启动）；无 NM/未接入写口返回 None。"""
        nm = getattr(self, "_nm", None)
        if nm is None or not hasattr(nm, "update_layer"):
            return None
        try:
            return BlinkController(nm, self._current_universe, channels)
        except Exception:
            return None

    def _blink_started(self, ctrl) -> None:
        """启动定时推进；2s 后由 ``_tick_blink`` 自动停。"""
        self._blink_timer.start()
        self._set_feedback(
            f"Blink {len(ctrl.channels)} 通道（2s 自动停）")

    def _tick_blink(self) -> None:
        ctrl = self._blink
        if ctrl is None:
            self._blink_timer.stop()
            return
        try:
            if not ctrl.tick():
                self._blink_timer.stop()
                self._set_feedback("Blink 结束")
        except Exception:
            self._blink_timer.stop()

    # ── 右键菜单 / 选择联动 ─────────────────────

    def _on_grid_context_menu(self, grid, global_pos, channels) -> None:
        from .monitor_actions import build_context_menu
        build_context_menu(self, grid, global_pos)

    def _on_selection_extras(self, channels) -> None:
        """选择变化 → sparkline 观察通道（无选择回退键盘游标）。"""
        if self._spark is None:
            return
        pick = max(channels) if channels else int(
            getattr(self._channel_grid, "_cursor", 0) or 0)
        self._spark.select(pick)

    def _set_feedback(self, msg: str) -> None:
        label = getattr(self, "_action_label", None)
        if label is not None:
            label.setText(str(msg))


def _teardown_blink(panel) -> None:
    """面板销毁时释放 blink（tester 层 255 残留清干净；全吞防已删访问）。

    ★ 清查⑧A P1-5：blink 的 stop() 是唯一释放路径——面板销毁时定时器随
      parent 停，但停止路径永不执行 → tester 层 255/active=True 永久残留。
    """
    try:
        timer = getattr(panel, "_blink_timer", None)
        if timer is not None:
            timer.stop()
        ctrl = getattr(panel, "_blink", None)
        if ctrl is not None:
            ctrl.stop()
    except Exception:
        pass
