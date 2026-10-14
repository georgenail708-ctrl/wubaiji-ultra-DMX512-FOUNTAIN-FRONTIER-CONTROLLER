"""SNTC receiver/view binding to the existing timeline and render owners.

Numerical decoding, freshness and consumer transitions are CE-owned. This new
source has not received native or real-output acceptance under the RAM hold.
"""
from __future__ import annotations

import ctypes as ct
import sys
import time

from PyQt6.QtCore import QObject, QTimer, Qt

from ..network.artnet_node import make_sntc_input_socket
from ..network.sntc_input import SntcInput, NativeClockError, ShutdownIncomplete
from .sntc_sync_dialog import SntcSyncDialog, SntcViewState
from .timeline import timeline_sntc_control
from .timeline import sntc_reference_audio
from ..engine import timeline_external_clock
from ..engine.timeline_local_render_owner import local_render_transaction


class SntcTimelineBridge(QObject):
    def __init__(self, window, controller, engine):
        super().__init__(window)
        from qascade_ce.native_sntc_v1 import Binding, Clock, Snapshot, ConsumerPlan
        # Initial SntcInput.init checks the complete current source set. Load
        # this pure typed declaration module before that first admission.
        from qascade_ce.native_sntc_media import Planner
        from qascade_ce.native_sntc_local_clock import LocalClockBinding
        from .timeline import audio_ref_track  # Complete feature supplier is loaded before admission.
        installed = sys.modules.get('qascade_ce')
        if installed is None:
            raise RuntimeError('SNTC需要已加载的CE计算引擎')
        self._window, self._controller, self._engine = window, controller, engine
        self._native = Binding(vars(installed).get('_engine'))
        self._local_clock = LocalClockBinding(self._native)
        self._plan_type = ConsumerPlan
        self._ui_previous = self._render_previous = self._render_pending = None
        self._last_view = None
        self._error = ''
        self._reference_audio = None
        self._receiver = SntcInput(native=self._native, clock_type=Clock, snapshot_type=Snapshot,
            source_current=self.source_current, socket_factory=make_sntc_input_socket,
            current_playing=self.current_playing, initial_position=controller._play_pos,
            initial_playing=controller._playing, retirement_current=self._native.source_current)
        self._dialog = SntcSyncDialog(window)
        self._dialog.enabled_requested.connect(self._requested)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._poll)
        self._ruler = getattr(window._timeline_widget, '_ruler', None)

    def _attach_consumers(self):
        from qascade_ce.native_sntc_media import Planner
        track = vars(self._window._timeline_widget).get('_audio_track')
        for owner, name in ((self._controller,'_sntc_bridge'),(self._engine,'_sntc_bridge')):
            existing = vars(owner).get(name)
            if existing is not None and existing is not self:
                raise RuntimeError('时间线已有其它SNTC接管者')
        if self._ruler is not None:
            existing = vars(self._ruler).get('_sntc_controller')
            if existing is not None and existing is not self._controller:
                raise RuntimeError('时间指针已有其它SNTC接管者')
        if track is not None:
            if vars(track).get('_sntc_reference') is not None:
                raise RuntimeError('参考音频已有其它SNTC接管者')
            self._reference_audio = sntc_reference_audio.SelectedReferenceAudio(self,track,Planner(self._native))
            track._sntc_reference = self._reference_audio
        # The generator clock reads the controller link. Publish it last; the
        # actual render lock already excludes frame consumers during this edit.
        self._engine._sntc_bridge = self
        if self._ruler is not None:
            self._ruler._sntc_controller = self._controller
        self._controller._sntc_bridge = self

    def _detach_consumers(self):
        # Remove the clock publication first. Disabled local control/audio then
        # use their ordinary routes without optional receiver qualification.
        if vars(self._controller).get('_sntc_bridge') is self:
            self._controller._sntc_bridge = None
        if vars(self._engine).get('_sntc_bridge') is self:
            self._engine._sntc_bridge = None
        if self._ruler is not None and vars(self._ruler).get('_sntc_controller') is self._controller:
            self._ruler._sntc_controller = None
        if self._reference_audio is not None:
            track = self._reference_audio._track
            if vars(track).get('_sntc_reference') is self._reference_audio:
                track._sntc_reference = None
            self._reference_audio = None

    def source_current(self):
        from ..engine.timeline_sntc_source_guard import binding_current
        return (binding_current(self) and self._native.source_current()
                and vars(self._window).get('_engine') is self._engine
                and getattr(self._window._timeline_widget, 'ctrl', None) is self._controller)

    def current_playing(self):
        # Called on receiver thread under ClockLock; no Qt method/property.
        return vars(self._controller).get('_playing')

    def _plan(self, view, previous):
        result = self._plan_type()
        status = self._native.consumer_plan(ct.byref(previous) if previous is not None else None,
            ct.byref(view), previous is not None, ct.byref(result))
        if status != 0:
            raise NativeClockError('consumer_plan', status)
        return result

    def local_action(self, apply):
        # Native rejection peek has no callbacks and cannot grant permission.
        # Fresh external control rejects before waiting for the physical lock.
        view = self._receiver.native_snapshot()
        if view.local_allowed != 1:
            self._controller.playback_tick_tier2.emit(self._controller._play_pos)
            return False
        with local_render_transaction(self._engine, self):
            return self._local_action_owned(apply)

    def _local_action_owned(self, apply):
        if not self._receiver.is_enabled():
            apply()
            return True
        def original(_view):
            # Rebase at the currently held needle before ordinary local Play.
            self._controller._play_t0 = self._native.origin(time.monotonic(), self._controller._play_pos)
            apply()
        result = self._receiver.local_takeover(self._controller._play_pos,
            self._controller._playing, original)
        if result.status == 10:
            self._controller.playback_tick_tier2.emit(self._controller._play_pos)
            return False
        if result.status != 0:
            raise NativeClockError('local_takeover', result.status)
        self._ui_previous = None
        return True

    def generator_clock(self):
        view = self._receiver.native_snapshot()
        if view.selected != 1:
            return None
        return view.playing == 1, view.position

    def prepare_generator_frame(self):
        return self._receiver.ordinary_generator_plan(self._local_clock, self._controller)

    def holds_external(self):
        return self._receiver.native_snapshot().selected == 1

    def keep_generator(self):
        return self._receiver.is_enabled()

    def prepare_render_frame(self, ordinary_position):
        """Called inside PlaybackEngine's actual owned render lock."""
        view = self._receiver.native_snapshot()
        if view.selected != 1:
            self._render_previous = None
            return ordinary_position
        plan = self._plan(view, self._render_previous)
        if plan.discrete_seek:
            # Only first source, explicit JUMP or actual backward consumption.
            # Existing seek resets dynamic ownership, not the completed cache.
            self._engine.seek(plan.position)
        self._render_pending = view
        return plan.position

    def finish_render_frame(self, succeeded):
        if succeeded and self._render_pending is not None:
            self._render_previous = self._render_pending
        self._render_pending = None

    def controller_tick(self):
        view, epoch = self._receiver.media_snapshot()
        if view.selected != 1:
            return False
        self._adopt_controller(view, epoch)
        return True

    def _view_still_owned(self, view, epoch):
        # Qt signals can synchronously turn synchronization off or perform a
        # permitted local takeover. Recheck that same native consumer lease.
        current, current_epoch = self._receiver.media_snapshot()
        if current_epoch != epoch:
            return False
        changes = self._plan(current, view)
        return not (changes.selection_changed or changes.position_changed
                    or changes.playing_changed or changes.discrete_seek)

    def _adopt_controller(self, view, epoch):
        if not self._view_still_owned(view, epoch):
            return False
        plan = self._plan(view, self._ui_previous)
        if not (plan.position_changed or plan.playing_changed or plan.selection_changed or plan.discrete_seek):
            self._ui_previous = view
            self._last_view = view
            return True
        controller = self._controller
        controller._sntc_applying = True
        try:
            controller._play_pos = plan.position
            if plan.playing_changed:
                controller._playing = plan.playing == 1
                if controller._playing:
                    controller._timer.start()
                    controller.playback_started.emit(plan.position)
                else:
                    controller._timer.stop()
                    controller.playback_stopped.emit(plan.position)
                if not self._view_still_owned(view, epoch):
                    return False
            controller.playback_tick.emit(plan.position)
            if not self._view_still_owned(view, epoch):
                return False
            controller.playback_tick_tier1.emit(plan.position)
            if not self._view_still_owned(view, epoch):
                return False
            controller.playback_tick_tier2.emit(plan.position)
            if not self._view_still_owned(view, epoch):
                return False
            self._ui_previous = view
            self._last_view = view
            return True
        finally:
            controller._sntc_applying = False

    def _present(self, view=None):
        if view is None:
            view = self._receiver.native_snapshot()
        self._last_view = view
        diagnostic = self._receiver.diagnostics()
        error = diagnostic['error']
        media_error = self._reference_audio._error if self._reference_audio is not None else ''
        display_error = self._error or (error[1] if error else '') or media_error
        if not view.enabled:
            status = '同步已关闭'
        elif not view.selected:
            status = '等待外部时间码，可自行操作时间线'
        elif not view.fresh:
            status = '时间码暂时停更，保持输出，可自行操作时间线'
        elif view.playing:
            status = '外部播放，时间线由外部接管'
        else:
            status = '外部停止，保持当前帧'
        source, position, rate = '—', '—', '—'
        if view.selected:
            peer, sender = self._receiver.sender_peer(view)
            source = peer + ' · ' + sender.decode('latin-1')
            position, rate = f'{view.position:.3f} 秒', f'{view.fps} fps'
        self._dialog.present(SntcViewState(view.enabled == 1, status, source, position,
                                         rate, display_error))

    def _poll(self):
        try:
            view, epoch, changed = self._receiver.poll_media()
            if changed:
                if view.selected:
                    if not self._adopt_controller(view, epoch):
                        return
            if not self._view_still_owned(view, epoch):
                return
            if self._reference_audio is not None:
                self._reference_audio.consume(view, epoch)
            if not self._view_still_owned(view, epoch):
                return
            if changed or self._dialog.isVisible():
                self._present(view)
            if changed:
                self._receiver.acknowledge_ui(view)
        except Exception as error:
            self._error = str(error)[:256]
            enabled = self._last_view.enabled == 1 if self._last_view is not None else True
            self._dialog.present(SntcViewState(enabled, '同步接收出现问题，保持当前输出',
                error=self._error))

    def set_enabled(self, enabled):
        self._receiver._reject_local_lifecycle()
        physical = self._engine._render_lock
        if not physical.acquire(timeout=0.2):
            raise RuntimeError('时间线正在处理一帧，请稍后再试')
        try:
            self._set_enabled_owned(enabled)
        finally:
            physical.release()
        if enabled and bool(vars(self._engine).get('_gen_thread_enabled')):
            self._engine.start_gen_thread()
        elif not enabled and not self._controller._playing:
            self._engine.stop_gen_thread()
        self._present()

    def _set_enabled_owned(self, enabled):
        if enabled:
            if not self._receiver.is_enabled():
                self._receiver.init(self._controller._play_pos, self._controller._playing)
                self._ui_previous = self._render_previous = None
                try:
                    self._attach_consumers()
                    self._receiver.enable()
                except BaseException:
                    # Preserve links if the actual owned worker/native clock
                    # failed to retire; the existing OFF action can retry it.
                    report = self._receiver.disable()
                    if (not report.worker_stopped or not report.socket_closed
                            or report.native_result is None or report.native_result.status != 0):
                        raise ShutdownIncomplete('SNTC启动失败且未能完整关闭：' + str(report.error))
                    self._detach_consumers()
                    raise
            self._error = ''
            self._timer.start()
        else:
            previous = self._receiver.native_snapshot()
            report = self._receiver.disable()
            if (not report.worker_stopped or not report.socket_closed
                    or report.native_result is None or report.native_result.status != 0):
                raise ShutdownIncomplete('SNTC接收未能完整关闭：' + str(report.error))
            if previous.selected:
                released = report.native_result.snapshot
                self._controller._play_pos = released.position
                self._controller._playing = released.playing == 1
                self._controller._play_t0 = self._native.origin(time.monotonic(), released.position)
                if self._controller._playing:
                    self._controller._timer.start()
                else:
                    self._controller._timer.stop()
            self._ui_previous = self._render_previous = None
            self._timer.stop()
            self._error = ''
            self._detach_consumers()

    def _requested(self, enabled):
        try:
            self.set_enabled(enabled)
        except Exception as error:
            self._error = str(error)[:256]
            try:
                self._present()
            except Exception:
                self._dialog.present(SntcViewState(status='同步接收出现问题，保持当前输出',
                                                  error=self._error))

    def show(self):
        self._present()
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()

    def force_close(self):
        self._timer.stop()
        report = self._receiver.force_close()
        if (not report.worker_stopped or not report.socket_closed
                or report.native_result is None or report.native_result.status != 0):
            raise ShutdownIncomplete('SNTC接收未能完整退出：' + str(report.error))
        self._detach_consumers()


def open_sntc_sync(window):
    bridge = vars(window).get('_sntc_bridge')
    try:
        timeline = getattr(window, '_timeline_widget', None)
        controller, engine = getattr(timeline, 'ctrl', None), getattr(window, '_engine', None)
        if bridge is not None and (vars(bridge).get('_controller') is not controller
                or vars(bridge).get('_engine') is not engine or vars(bridge._receiver).get('_closed') is True):
            bridge.force_close()
            window._sntc_bridge = bridge = None
        if bridge is None:
            if controller is None or engine is None:
                raise RuntimeError('请先打开可播放的时间线工程')
            bridge = SntcTimelineBridge(window, controller, engine)
            window._sntc_bridge = bridge
        bridge.show()
        # Bind/start errors stay visible in the same operator window so the
        # explicit enable switch and diagnostics remain reachable.
        bridge._requested(True)
    except Exception as error:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(window, 'SNTC 时间码同步', str(error)[:256])
