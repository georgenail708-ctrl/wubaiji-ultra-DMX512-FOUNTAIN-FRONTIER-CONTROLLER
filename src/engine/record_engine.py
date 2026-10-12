"""
DMXTimeline — Record Engine  (v3)
统一录制路径：接收 ProgrammerPanel.dmx_out(universe, data) → 写入 Clip。

★ 核心改变：
  - 不再监听 NetworkManager.frame_in（那是原始网络输入，不含编程值）
  - 改为通过 connect_to_signal() 连接 ProgrammerPanel.dmx_out
  - _on_frame 签名改为 (universe: int, data: np.ndarray)
  - 内部自行构造 DMXFrame，不再依赖外部 frame.apply_filter()

★ 录制流程：
  1. MainWindow._start_record → RecordEngine.start()
  2. ProgrammerPanel 混合所有层数据
  3. ProgrammerPanel.dmx_out.emit(universe, final_data)
  4. RecordEngine._on_frame → 构造 DMXFrame → 写入 Clip
  5. MainWindow._stop_record → RecordEngine.stop()
"""

from __future__ import annotations

import copy
import logging
import time
from typing import Dict, List, Optional

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from ..core.models import Clip, DMXFrame, FilterRule, RecordState, Track
from ..core.constants import BASE_LAYERS, SHAPE_LAYERS
from ..core.recorded_channel_masks import record_mask, shift_clip_masks

log = logging.getLogger(__name__)


def _zero_recording_clip_frames(recording_clips) -> None:
    """录制帧从绝对时钟转为按钮起点相对时间（模块级：stop 行数基线）。

    主路 timeline_controller.stop_recording 同款契约——start_position>0 起录
    时引擎按 clip 本地时间查帧（local_t ∈ [0, duration)）。首采样之前的
    等待属于录制，不从第一帧截掉；主路本身已经是相对时间。
    """
    for _clip in (recording_clips or {}).values():
        _frames = getattr(_clip, "frames", None) or []
        if _frames:
            _first = max(0., float(getattr(_clip, "start_time", 0.0) or 0.0))
            shift_clip_masks(_clip, _first if _first > 0.0001 else 0.)
            if _first > 0.0001:
                for _f in _frames:
                    _f.timestamp = max(0.0, float(_f.timestamp) - _first)


class RecordEngine(QObject):
    """旁路录制引擎：从 ProgrammerPanel.dmx_out 接收最终 DMX 数据并写入 Clip。"""

    state_changed    = pyqtSignal(RecordState)       # 录制状态变化
    clip_finished    = pyqtSignal(str, object)        # track_id, Clip
    frame_count_tick = pyqtSignal(int)                # 每 40 帧 tick 一次

    def __init__(self, network_manager, project_manager, parent=None):
        """
        Args:
            network_manager: NetworkManager 实例（保留兼容，v3 不再直接监听其信号）
            project_manager: ProjectManager 实例
            parent: Qt parent
        """
        super().__init__(parent)
        self._nm = network_manager   # 保留但不直接监听
        self._pm = project_manager
        self._state = RecordState.IDLE
        self._start_time: float = 0.0
        self._start_position: float = 0.0  # ★ 新增：记录起始时间线位置

        self._recording_clips: Dict[str, Clip] = {}  # track_id → Clip
        self._active_track_ids: List[str] = []
        self._base_order_fixture_ids: List[str] = []
        self._active_masks_by_track: Dict[str, Dict[int, np.ndarray]] = {}
        self._record_tick_cache: dict = {}
        self._frame_count = 0

        # ★ v3: 需要连接的信号源（外部通过 connect_to_signal 设置）
        self._signal_source = None
        self._record_source_provider = None
        # ★ 调度台·控件轨迹（鸭子注入，零 import 依赖）：ControlTrajectoryBuffer
        self._traj_buffer = None

    # ── 属性 ────────────────────────────────────────────────────────────────

    @property
    def state(self) -> RecordState:
        return self._state

    @property
    def is_recording(self) -> bool:
        return self._state == RecordState.RECORDING

    @property
    def frame_count(self) -> int:
        return self._frame_count

    # ── 连接外部信号 ────────────────────────────────────────────────────────

    def set_traj_buffer(self, buffer) -> None:
        """注入控件轨迹 Buffer（processing.dispatch.ControlTrajectoryBuffer）。"""
        self._traj_buffer = buffer

    def _begin_traj_buffer(self) -> None:
        """★ R193/P0-2（XS-07）：轨迹缓冲开启失败必须留痕——否则本段录制静默没有轨迹数据。"""
        if self._traj_buffer is None:
            return
        try:
            self._traj_buffer.begin()
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "轨迹缓冲 begin() 失败：本次录制将没有轨迹数据")

    def now_position(self) -> float:
        """当前录制时间轴位置（与帧 timestamp 同一公式：start_position + elapsed）。"""
        if self._state == RecordState.RECORDING:
            # ★ R193/DM-03：monotonic——录制中系统校时不再使帧戳跳变/倒挂
            return self._start_position + max(0.0, time.monotonic() - self._start_time)
        return float(getattr(self, "_start_position", 0.0) or 0.0)

    def begin_traj_only(self, start_position: float = 0.0) -> None:
        """★ R193/DM-01：主路（TimelineController）录制只回编控件轨迹层。

        RECORDING 闸门只由本引擎 start()（兜底路径专用）打开，主路从不走它
        ——不接线则 record_traj_event 全部被静默丢弃（审计 DM-01 死路）。
        本方法不启动帧录制，仅打开轨迹闸门、begin 缓冲并固定窗口起点；
        事件戳 = start_position + 单调 elapsed，与主路 clip.start_time 同基。
        """
        self._traj_armed = True
        self._traj_t0 = float(start_position)
        self._traj_clock = time.monotonic()
        self._begin_traj_buffer()

    def end_traj_only(self, clip=None) -> None:
        """★ R193/DM-01：主路停止——轨迹流按本次录制窗口切片进录出的 clip。"""
        if not getattr(self, "_traj_armed", False):
            return
        self._traj_armed = False
        buf = self._traj_buffer
        if buf is None or clip is None:
            return
        t0 = float(getattr(self, "_traj_t0", 0.0) or 0.0)
        t1 = t0 + max(0.0, time.monotonic() - float(
            getattr(self, "_traj_clock", time.monotonic())))
        try:
            buf.dump_to_clips([(clip, t0, t1)])
        except Exception:
            log.debug("traj-only dump failed", exc_info=True)

    def record_traj_event(self, key: str, value) -> None:
        """UI 信号汇口：录制中才收（值抽稀在 Buffer 内）。非录制态零成本。"""
        try:
            if self._traj_buffer is None:
                return
            if self._state == RecordState.RECORDING:
                self._traj_buffer.record(self.now_position(), key, value)
            elif getattr(self, "_traj_armed", False):
                # ★ R193/DM-01：主路 traj-only 时间基（与帧录制状态解耦）
                ts = float(getattr(self, "_traj_t0", 0.0) or 0.0) + max(
                    0.0, time.monotonic()
                    - float(getattr(self, "_traj_clock", time.monotonic())))
                self._traj_buffer.record(ts, key, value)
        except Exception:
            # ★ R193/P0-2：首错告警+计数——录制数据静默丢失必须可诊断
            if not getattr(self, "_traj_err_logged", False):
                self._traj_err_logged = True
                log.exception("轨迹事件写入失败（首次告警；同类错误只计数不再刷屏）")
            self._traj_err_count = getattr(self, "_traj_err_count", 0) + 1

    def connect_to_signal(self, signal):
        """
        ★ v3 新增：连接到数据源信号。

        典型用法：
            record_engine.connect_to_signal(programmer_panel.dmx_out)

        信号签名应为: signal(int, np.ndarray)  → (universe, data)
        """
        # 先断开旧连接（如果有）
        if self._signal_source is not None:
            try:
                self._signal_source.disconnect(self._on_frame)
            except (TypeError, RuntimeError):
                pass

        self._signal_source = signal
        try:
            signal.connect(self._on_frame)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "RecordEngine.connect_to_signal failed: %s", e)

    def disconnect_signal(self):
        """断开当前信号连接"""
        if self._signal_source is not None:
            try:
                self._signal_source.disconnect(self._on_frame)
            except (TypeError, RuntimeError):
                pass
            self._signal_source = None

    def set_record_source_provider(self, provider):
        """Provide opt-in clip outputs for recording-side TrackLinker sampling."""
        self._record_source_provider = provider

    def _track_or_ancestor_muted(self, track, track_by_id: dict) -> bool:
        """与 playback_engine._track_or_ancestor_muted 同构: 祖先链静音判定。"""
        if bool(getattr(track, "muted", False)):
            return True
        seen: set = set()
        parent_id = getattr(track, "parent_id", "") or ""
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = track_by_id.get(str(parent_id))
            if parent is None:
                break
            if bool(getattr(parent, "muted", False)):
                return True
            parent_id = getattr(parent, "parent_id", "") or ""
        return False

    def _track_or_ancestor_soloed(self, track, track_by_id: dict) -> bool:
        """与 playback_engine._track_or_ancestor_soloed 同构: 祖先链单播判定。"""
        if bool(getattr(track, "solo", False)):
            return True
        seen: set = set()
        parent_id = getattr(track, "parent_id", "") or ""
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = track_by_id.get(str(parent_id))
            if parent is None:
                break
            if bool(getattr(parent, "solo", False)):
                return True
            parent_id = getattr(parent, "parent_id", "") or ""
        return False

    def _recordable_track_ids(self, tracks: list) -> list:
        """2026-08-13 R13: 录制轨道排除语义与播放侧对齐 —— 原仅直接 muted,
        漏祖先链/单播/文件夹(用户报「M/S 排除不彻底」)。文件夹不录、祖先静音
        不录、存在 solo 时只录 solo 链(静音优先于 solo, 与播放侧一致)。"""
        track_by_id = {
            str(getattr(t, "id", "") or ""): t for t in tracks if getattr(t, "id", None)
        }
        any_solo = any(
            self._track_or_ancestor_soloed(t, track_by_id)
            for t in tracks
            if not bool(getattr(t, "is_folder", False))
            and not self._track_or_ancestor_muted(t, track_by_id)
        )
        out = []
        for t in tracks:
            if bool(getattr(t, "is_folder", False)):
                continue
            if self._track_or_ancestor_muted(t, track_by_id):
                continue
            if any_solo and not self._track_or_ancestor_soloed(t, track_by_id):
                continue
            out.append(getattr(t, "id", ""))
        return out

    # ── 录制控制 ────────────────────────────────────────────────────────────

    def start(self, track_ids: Optional[List[str]] = None,
              start_time: float = 0.0, start_position: float = 0.0,
              base_order_fixture_ids: Optional[List[str]] = None):
        """
        开始录制。

        Args:
            track_ids: 要录制到的轨道 ID 列表（None = 所有轨道）
            start_time: 录制开始的 wall-clock 时间偏移（秒）
            start_position: ★ 时间线起始位置（秒），用于计算帧时间戳
        """
        if self._state == RecordState.RECORDING:
            import logging
            logging.getLogger(__name__).warning(
                "RecordEngine.start() called while already RECORDING — ignored")
            return

        # 重置状态
        # ★ R193/DM-03：monotonic 时基（与主路 timeline_controller 一致）
        self._start_time = time.monotonic()
        self._start_position = float(start_position)
        self._base_order_fixture_ids = self._unique_fixture_id_list(base_order_fixture_ids or [])
        self._frame_count = 0

        # ★ 通知 NetworkManager 开始录制（如果有对应方法）
        if hasattr(self._nm, "set_record_start"):
            self._nm.set_record_start(self._start_time)
        # 也可以通过私有属性标记录制状态（NetworkManager._dispatch_frame 会检查）
        if hasattr(self._nm, "_recording"):
            try:
                setattr(self._nm, "_recording", True)
            except Exception:
                log.debug("Failed to set _nm._recording to True on start")
                pass

        # 确定要录制的轨道
        # 2026-08-13 R13: 排除语义与播放侧一致(祖先链/单播/文件夹), 原仅直接
        # muted。显式 track_ids 必须先对全列表过滤再求交 —— 帧循环的允许集
        # 是对全列表计算的, 若子集内单独过滤, 子集外的 solo/静音祖先会把
        # any_solo/祖先链判定带偏: start 入列的轨道在帧循环被整帧跳过,
        # 静默录出 0 帧(LOW-2 不对称)。
        proj = self._pm.project
        all_tracks = list(getattr(proj, "tracks", []) or [])
        recordable = self._recordable_track_ids(all_tracks)
        if track_ids is None:
            self._active_track_ids = recordable
        else:
            wanted = set(track_ids)
            self._active_track_ids = [tid for tid in recordable if tid in wanted]

        # 为每个轨道创建空 Clip
        self._recording_clips = {}
        self._active_masks_by_track = {}
        self._record_tick_cache = {}
        for tid in self._active_track_ids:
            clip = Clip(
                track_id=tid,
                start_time=start_position,  # ★ 使用时间线位置
                label=f"Recording",
            )
            self._recording_clips[tid] = clip
            self._active_masks_by_track[tid] = {}

        # ★ v3: 不再连接 self._nm.frame_in
        # 调用者必须在 start() 之前通过 connect_to_signal() 设置好信号源

        self._begin_traj_buffer()
        self._state = RecordState.RECORDING
        self.state_changed.emit(self._state)

        import logging
        log = logging.getLogger(__name__)
        log.info(
            "[Record] Started — tracks: %s, start_position: %.2f",
            [self._get_track_name(tid) for tid in self._active_track_ids],
            start_position,
        )

    def stop(self):
        """停止录制，将 Clip 添加到项目。"""
        if self._state == RecordState.IDLE:
            return

        # 断开信号
        self.disconnect_signal()

        # ★ 清除 NetworkManager 录制标记
        if hasattr(self._nm, "_recording"):
            try:
                setattr(self._nm, "_recording", False)
            except Exception:
                log.debug("Failed to clear _nm._recording on stop")
                pass

        # 保存所有 Clip
        proj = self._pm.project; clips_saved = 0
        # stop 时状态可能已离开 RECORDING —— 显式用同一公式取终点（不依赖状态）
        stop_ts = float(getattr(self, "_start_position", 0.0) or 0.0) + max(0.0, time.monotonic() - float(getattr(self, "_start_time", 0.0) or 0.0))  # ★ R193/DM-03
        # ★ 控件轨迹：按各 clip 时间窗切片（v2.1 §5.2），先统一 dump
        if self._traj_buffer is not None:
            try:
                def _clip_t0(c):
                    # ★ 0.0 是合法时间点（falsy 坑）：start_time 为 None 才回退 stop_ts
                    st = getattr(c, "start_time", None)
                    return float(st) if st is not None else stop_ts
                self._traj_buffer.dump_to_clips([
                    (clip, _clip_t0(clip), stop_ts)
                    for clip in (self._recording_clips or {}).values()
                ])
            except Exception:
                log.debug("trajectory dump failed", exc_info=True)
        _zero_recording_clip_frames(self._recording_clips)  # ★ R193/DM-02（dump 后：窗口仍用绝对基）
        for tid, clip in self._recording_clips.items():
            frame_count = clip.frame_count()
            has_traj = bool(getattr(clip, "trajectories", None))
            if frame_count == 0 and not has_traj:
                continue

            # 保留按钮的完整录制窗口，首帧之前和最后采样之后的等待不裁掉。
            clip.duration = max(0.05, stop_ts - float(clip.start_time))
            clip.playback_rate = 1.0
            clip.source_duration = max(0.05, float(getattr(clip, "duration", 0.05) or 0.05))
            clip.clip_universes = sorted({int(frame.universe) for frame in clip.frames}) or list(
                getattr(self._get_track(tid), 'universes', None) or [1])
            active_by_universe = self._active_masks_by_track.get(tid, {}) or {}
            clip.active_channels_by_universe = {
                int(uni): [int(i) + 1 for i, flag in enumerate(mask) if bool(flag)]
                for uni, mask in active_by_universe.items()
                if mask is not None and np.any(mask)
            }
            clip.base_order_fixture_ids = self._base_order_for_clip(clip)

            # 设置 label
            track_name = self._get_track_name(tid)
            clip.label = f"Rec - {track_name} ({self._frame_count}f)"

            # 添加到项目轨道
            track = proj.get_track(tid) if hasattr(proj, "get_track") else None
            if track is not None:
                try:
                    self._pm.add_clip(tid, clip)
                    clips_saved += 1
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(
                        "RecordEngine.stop() — add_clip failed for track %s: %s",
                        tid, e,
                    )
            else:
                # 兜底：直接追加到 track.clips
                for t in proj.tracks:
                    if t.id == tid:
                        if not hasattr(t, "clips"):
                            t.clips = []
                        t.clips.append(clip)
                        clips_saved += 1
                        break

            # 通知外部
            self.clip_finished.emit(tid, clip)

        # 清理
        self._recording_clips = {}
        self._active_track_ids = []
        self._base_order_fixture_ids = []
        self._active_masks_by_track = {}
        self._record_tick_cache = {}
        self._state = RecordState.IDLE
        self.state_changed.emit(self._state)

        import logging
        log = logging.getLogger(__name__)
        log.info(
            "[Record] Stopped — %d frames, %d clips saved",
            self._frame_count, clips_saved,
        )

    def pause(self):
        """暂停录制（保持 Clip 数据，断开信号）。"""
        if self._state == RecordState.RECORDING:
            self._state = RecordState.PAUSED
            self.state_changed.emit(self._state)
            self.disconnect_signal()

            import logging
            logging.getLogger(__name__).info(
                "[Record] Paused at frame %d", self._frame_count)

    def resume(self):
        """恢复录制（重新连接信号）。"""
        if self._state == RecordState.PAUSED:
            self._state = RecordState.RECORDING

            # 重新连接信号
            if self._signal_source is not None:
                try:
                    self._signal_source.connect(self._on_frame)
                except (TypeError, RuntimeError):
                    pass

            self.state_changed.emit(self._state)

            import logging
            logging.getLogger(__name__).info("[Record] Resumed")

    def cancel(self):
        """取消录制（丢弃所有已录制的帧）。"""
        if self._state == RecordState.IDLE:
            return

        self.disconnect_signal()

        if hasattr(self._nm, "_recording"):
            try:
                setattr(self._nm, "_recording", False)
            except Exception:
                log.debug("Failed to clear _nm._recording on cancel")
                pass

        frame_count = self._frame_count
        self._recording_clips = {}
        self._active_track_ids = []
        self._base_order_fixture_ids = []
        self._active_masks_by_track = {}
        self._record_tick_cache = {}
        self._frame_count = 0
        self._state = RecordState.IDLE
        self.state_changed.emit(self._state)

        import logging
        logging.getLogger(__name__).info(
            "[Record] Cancelled — %d frames discarded", frame_count)

    # ── 帧接收（★ v3 核心：接收 universe + ndarray）─────────────────────────

    def _on_frame(self, universe: int, data: np.ndarray):
        """接收 Programmer dmx_out；采集实际编辑混音及逐帧通道占用。"""
        if self._state != RecordState.RECORDING:
            return

        proj = self._pm.project
        fallback_data = self._coerce_frame_data(data)
        record_sources = self._record_sources_for_current_frame()
        self._refresh_track_linker_layer(record_sources=record_sources)
        data, active_mask = self._record_editor_frame_and_active(
            int(universe),
            fallback_data,
        )
        data, active_mask = self._merge_record_sources_into_frame(
            int(universe),
            data,
            active_mask,
            record_sources,
        )

        if log.isEnabledFor(logging.DEBUG) and self._frame_count % 40 == 0:
            data_sum = int(np.sum(data))
            active_count = int(np.count_nonzero(active_mask))
            log.debug(
                "REC FRAME %s (frame #%s) sum=%s active=%s",
                universe, self._frame_count, data_sum, active_count,
            )

        # 计算时间戳（相对于录制开始的时间线位置）
        elapsed = max(0.0, time.monotonic() - self._start_time)  # ★ R193/DM-03
        timestamp = self._start_position + elapsed

        # 构造 DMXFrame
        frame = DMXFrame(
            universe=universe,
            timestamp=timestamp,
            data=data.copy(),  # 深拷贝，避免后续修改影响
        )

        # 分发到各轨道（每个轨道可能有自己的 universe 过滤）
        # 2026-08-13 R13: 排除与播放侧一致 —— 原仅直接 muted, 漏祖先链/单播/
        # 文件夹(「排除不彻底」)。每帧重算允许集, 录制中途切换 M/S 当帧生效。
        proj = self._pm.project
        allowed_ids = set(self._recordable_track_ids(list(getattr(proj, "tracks", []) or [])))
        for tid in self._active_track_ids:
            if tid not in allowed_ids:
                continue
            track = self._get_track(tid)
            if track is None:
                continue

            # 检查 universe 过滤
            target_universes = getattr(
                track, "universes",
                [getattr(track, "universe", 1)]
            )
            if universe not in target_universes:
                continue

            # 应用通道过滤（如果需要）
            filtered_frame = self._apply_track_filter(track, frame)
            filtered_active = self._apply_track_active_mask(track, int(universe), active_mask)

            # 写入 Clip
            clip = self._recording_clips.get(tid)
            if clip is not None:
                clip.frames.append(filtered_frame)
                record_mask(clip.recorded_channel_masks, universe, timestamp, filtered_active)
                if np.any(filtered_active):
                    per_uni = self._active_masks_by_track.setdefault(tid, {})
                    prev = per_uni.get(int(universe))
                    if prev is None:
                        per_uni[int(universe)] = filtered_active.copy()
                    else:
                        prev |= filtered_active

        # 更新帧计数
        self._frame_count += 1
        if self._frame_count % 40 == 0:
            self.frame_count_tick.emit(self._frame_count)

    # ── 辅助方法 ────────────────────────────────────────────────────────────

    @staticmethod
    def _unique_fixture_id_list(values) -> List[str]:
        out: List[str] = []
        seen: set[str] = set()
        for value in values or []:
            fid = str(value or "")
            if fid and fid not in seen:
                out.append(fid)
                seen.add(fid)
        return out

    @staticmethod
    def _coerce_frame_data(data) -> np.ndarray:
        raw = np.asarray(data, dtype=np.uint8).reshape(-1)
        if raw.shape[0] == 512:
            return raw.copy()
        fixed = np.zeros(512, dtype=np.uint8)
        n = min(raw.shape[0], 512)
        if n > 0:
            fixed[:n] = raw[:n]
        return fixed

    def _record_editor_frame_and_active(
        self,
        universe: int,
        fallback_data: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        nm = getattr(self, "_nm", None)
        if nm is not None and hasattr(nm, "compute_universe_snapshots_now"):
            cache = getattr(self, "_record_tick_cache", {}) or {}
            cache_universes = set(cache.get("universes", set()) or set())
            try:
                bucket = int(time.monotonic() * 100.0)
            except Exception:
                log.debug("Failed to compute bucket from time.monotonic()")
                bucket = 0
            if int(universe) not in cache_universes or cache.get("bucket") != bucket:
                universes = {int(universe)}
                try:
                    for tid in getattr(self, "_active_track_ids", []) or []:
                        track = self._get_track(tid)
                        if track is None:
                            continue
                        universes.update(
                            int(u) for u in (
                                getattr(track, "universes", None)
                                or [getattr(track, "universe", 1)]
                            )
                        )
                except Exception:
                    log.debug("Failed to collect universes from track %s", tid if 'tid' in dir() else "unknown")
                    pass
                try:
                    frames, active_maps = nm.compute_universe_snapshots_now(
                        universes=universes,
                        exclude_layers=["clip_playback", "clip_preview", "timeline"],
                        apply_output_smoothing=False,
                        apply_xy_reference=True,
                        active_layers=BASE_LAYERS + SHAPE_LAYERS,
                    )
                    cache = {
                        "bucket": bucket,
                        "universes": {int(u) for u in universes},
                        "frames": frames,
                        "active": active_maps,
                    }
                    self._record_tick_cache = cache
                except Exception:
                    log.debug("Failed to compute universe snapshots, clearing cache")
                    cache = {}
            frames = cache.get("frames", {}) if isinstance(cache, dict) else {}
            active_maps = cache.get("active", {}) if isinstance(cache, dict) else {}
            frame = frames.get(int(universe))
            active = active_maps.get(int(universe))
            if frame is not None and active is not None:
                return self._coerce_frame_data(frame), np.asarray(active, dtype=bool).reshape(-1)[:512].copy()
        data = self._editor_frame_data(int(universe), fallback_data)
        active_mask = self._editor_active_mask(int(universe), data, refresh_linker=False)
        return data, active_mask

    def _refresh_track_linker_layer(self, record_sources=None):
        nm = getattr(self, "_nm", None)
        if nm is None:
            return
        try:
            from ..processing.track_linker_engine import get_linker_engine
        except Exception:
            log.debug("Failed to import get_linker_engine from ..processing.track_linker_engine, trying src.processing")
            try:
                from src.processing.track_linker_engine import get_linker_engine
            except Exception:
                log.debug("Failed to import get_linker_engine from src.processing.track_linker_engine, aborting")
                return
        try:
            linker = get_linker_engine()
            if hasattr(linker, "set_network_manager") and getattr(linker, "_nm", None) is not nm:
                linker.set_network_manager(nm)
            exclude_layers = {"clip_playback", "clip_preview", "timeline"}
            if hasattr(linker, "process_once"):
                linker.process_once(exclude_layers=exclude_layers, extra_sources=record_sources)
            elif hasattr(linker, "_tick"):
                linker._tick()
        except Exception:
            log.debug("Failed to tick linker engine")
            pass

    def _record_sources_for_current_frame(self):
        provider = getattr(self, "_record_source_provider", None)
        if not callable(provider):
            return {}
        try:
            elapsed = time.monotonic() - float(getattr(self, "_start_time", 0.0) or 0.0)  # ★ R193/DM-03
            pos = float(getattr(self, "_start_position", 0.0) or 0.0) + max(0.0, elapsed)
        except Exception:
            log.debug("Failed to calculate record source position, defaulting to 0.0")
            pos = 0.0
        try:
            return provider(pos) or {}
        except TypeError:
            try:
                return provider() or {}
            except Exception:
                log.debug("Failed to call provider() (no-arg fallback)")
                return {}
        except Exception:
            log.debug("Failed to call provider(pos)")
            return {}

    @staticmethod
    def _coerce_record_source_entry(entry) -> tuple[np.ndarray, np.ndarray]:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            data_raw, active_raw = entry[0], entry[1]
        else:
            data_raw, active_raw = entry, None

        data = np.asarray(data_raw, dtype=np.uint8).reshape(-1)
        if data.shape[0] != 512:
            fixed = np.zeros(512, dtype=np.uint8)
            n = min(int(data.shape[0]), 512)
            if n > 0:
                fixed[:n] = data[:n]
            data = fixed

        if active_raw is None:
            active = data != 0
        else:
            active = np.asarray(active_raw, dtype=bool).reshape(-1)
            if active.shape[0] != 512:
                fixed_active = np.zeros(512, dtype=bool)
                n = min(int(active.shape[0]), 512)
                if n > 0:
                    fixed_active[:n] = active[:n]
                active = fixed_active
        return data, active

    def _merge_record_sources_into_frame(self, universe: int, data: np.ndarray,
                                         active_mask: np.ndarray, record_sources=None):
        frame = self._coerce_frame_data(data)
        active = np.asarray(active_mask, dtype=bool).reshape(-1)
        if active.shape[0] != 512:
            fixed_active = np.zeros(512, dtype=bool)
            n = min(int(active.shape[0]), 512)
            if n > 0:
                fixed_active[:n] = active[:n]
            active = fixed_active

        source_entry = (record_sources or {}).get(int(universe))
        if source_entry is None:
            return frame, active
        try:
            source_data, source_active = self._coerce_record_source_entry(source_entry)
        except Exception:
            log.debug("Failed to coerce record source entry for universe %s", int(universe) if 'universe' in dir() else "unknown")
            return frame, active
        source_active = source_active & ~active
        if not np.any(source_active):
            return frame, active

        merged = frame.copy()
        merged[source_active] = source_data[source_active]
        active = active | source_active
        return merged, active

    def _editor_active_mask(self, universe: int, fallback_data: np.ndarray,
                            refresh_linker: bool = True) -> np.ndarray:
        nm = getattr(self, "_nm", None)
        if refresh_linker:
            self._refresh_track_linker_layer()
        if nm is not None and hasattr(nm, "compute_editor_active_mask"):
            try:
                mask = np.asarray(nm.compute_editor_active_mask(int(universe)), dtype=bool).reshape(-1)
                if mask.shape[0] != 512:
                    fixed = np.zeros(512, dtype=bool)
                    n = min(mask.shape[0], 512)
                    if n > 0:
                        fixed[:n] = mask[:n]
                    mask = fixed
                return mask
            except Exception:
                log.debug("Failed to compute editor active mask for universe %s", universe)
                pass
        return np.asarray(fallback_data, dtype=np.uint8) != 0

    def _editor_frame_data(self, universe: int, fallback_data: np.ndarray) -> np.ndarray:
        nm = getattr(self, "_nm", None)
        if nm is not None and hasattr(nm, "compute_editor_universe_now"):
            try:
                return self._coerce_frame_data(nm.compute_editor_universe_now(
                    int(universe),
                    apply_output_smoothing=False,
                    apply_xy_reference=True,
                ))
            except Exception:
                log.debug("Failed to compute editor frame data for universe %s", universe)
                pass
        return self._coerce_frame_data(fallback_data)

    def _apply_track_active_mask(self, track, universe: int, active: np.ndarray) -> np.ndarray:
        mask = np.asarray(active, dtype=bool).reshape(-1)
        if mask.shape[0] != 512:
            fixed = np.zeros(512, dtype=bool)
            n = min(mask.shape[0], 512)
            if n > 0:
                fixed[:n] = mask[:n]
            mask = fixed
        rule = None
        if hasattr(track, "universe_filters"):
            rule = track.universe_filters.get(universe)
        if rule is None:
            rule = getattr(track, "filter_rule", None)
        if rule and not rule.is_empty():
            filtered = np.zeros(512, dtype=bool)
            for ch in getattr(rule, "channels", set()) or set():
                idx = int(ch) - 1
                if 0 <= idx < 512:
                    filtered[idx] = bool(mask[idx])
            return filtered
        return mask.copy()

    def _fixture_channel_span(self, fi, fixture_defs_by_id: Optional[dict],
                              ordered_fixtures: Optional[list] = None,
                              index: int = -1) -> tuple[int, int]:
        start = int(getattr(fi, "start_channel", 1) or 1) - 1
        if not (0 <= start < 512):
            return start, start
        fixture_def = fixture_defs_by_id.get(str(getattr(fi, "def_id", ""))) if fixture_defs_by_id else None
        ch_count = 0
        if fixture_def is not None:
            try:
                ch_count = int(getattr(fixture_def, "channel_count", 0) or 0)
            except Exception:
                log.debug("Failed to get channel_count from fixture_def for %s", getattr(fixture_def, "id", "unknown"))
                ch_count = 0
            if ch_count <= 0:
                channels = getattr(fixture_def, "channels", None)
                if isinstance(channels, (list, tuple)):
                    ch_count = len(channels)
        if ch_count <= 0 and ordered_fixtures is not None and 0 <= index + 1 < len(ordered_fixtures):
            next_start = int(getattr(ordered_fixtures[index + 1], "start_channel", start + 2) or (start + 2)) - 1
            ch_count = max(1, next_start - start)
        if ch_count <= 0:
            ch_count = 1
        return start, min(512, start + max(1, ch_count))

    def _clip_active_mask(self, clip: Clip, universe: int) -> np.ndarray:
        mask = np.zeros(512, dtype=bool)
        for frame in getattr(clip, "frames", []) or []:
            try:
                if int(getattr(frame, "universe", 1) or 1) != int(universe):
                    continue
                raw = np.asarray(getattr(frame, "data", np.zeros(512, dtype=np.uint8)), dtype=np.uint8)
                n = min(raw.size, 512)
                if n > 0:
                    mask[:n] |= raw.reshape(-1)[:n] != 0
            except Exception:
                log.debug("Failed to process frame in clip_active_mask")
                continue
        return mask

    def _base_order_for_clip(self, clip: Clip) -> List[str]:
        proj = getattr(self._pm, "project", None)
        frozen = self._unique_fixture_id_list(self._base_order_fixture_ids)
        if proj is None:
            return frozen
        fixtures = list(getattr(proj, "fixtures", []) or [])
        fixture_defs_by_id = {
            str(getattr(fd, "id", "")): fd
            for fd in (getattr(proj, "fixture_defs", []) or [])
            if getattr(fd, "id", None)
        }
        recorded: List[str] = []
        seen: set[str] = set()
        by_universe: dict[int, list] = {}
        for fi in fixtures:
            try:
                uni = int(getattr(fi, "universe", 1) or 1)
            except Exception:
                log.debug("Failed to get universe from fixture, defaulting to 1")
                uni = 1
            by_universe.setdefault(uni, []).append(fi)
        for uni in sorted(by_universe):
            active = self._clip_active_mask(clip, uni)
            if not np.any(active):
                continue
            ordered = sorted(by_universe[uni], key=lambda fi: int(getattr(fi, "start_channel", 1) or 1))
            for idx, fi in enumerate(ordered):
                fid = str(getattr(fi, "id", "") or "")
                if not fid or fid in seen:
                    continue
                start, end = self._fixture_channel_span(fi, fixture_defs_by_id, ordered, idx)
                if 0 <= start < end <= 512 and bool(np.any(active[start:end])):
                    recorded.append(fid)
                    seen.add(fid)
        if recorded:
            allowed = set(recorded)
            ordered = [fid for fid in frozen if fid in allowed]
            present = set(ordered)
            ordered.extend(fid for fid in recorded if fid not in present)
            return ordered
        return frozen

    def _get_track(self, track_id: str):
        """根据 ID 获取轨道"""
        try:
            proj = self._pm.project
            if hasattr(proj, "get_track"):
                return proj.get_track(track_id)
            for t in proj.tracks:
                if t.id == track_id:
                    return t
        except Exception:
            log.debug("Failed to get track %s", track_id)
            pass
        return None

    def _get_track_name(self, track_id: str) -> str:
        """获取轨道名称"""
        track = self._get_track(track_id)
        if track is not None:
            return getattr(track, "name", track_id)
        return str(track_id)[:8]

    def _apply_track_filter(self, track, frame: DMXFrame) -> DMXFrame:
        """
        应用轨道的通道过滤规则。
        如果轨道有 universe_filters，使用对应 universe 的规则。
        """
        rule = None
        if hasattr(track, "universe_filters"):
            rule = track.universe_filters.get(frame.universe)
        if rule is None:
            rule = getattr(track, "filter_rule", None)

        if rule and not rule.is_empty():
            # 应用 FilterRule
            return frame.apply_filter(rule)
        else:
            # 无过滤，直接返回副本
            return frame.copy()

    # ── 兼容性方法（保留旧接口，便于过渡）─────────────────────────────────

    def connect_to_network_manager(self):
        """
        ★ 已废弃 (v3)：
        旧版本通过此方法连接 NetworkManager.frame_in，
        v3 不再需要，保留仅为兼容。
        """
        import logging
        logging.getLogger(__name__).warning(
            "RecordEngine.connect_to_network_manager() is deprecated in v3. "
            "Use connect_to_signal() instead."
        )
        # 尝试兼容旧调用：连接 NetworkManager.frame_in
        if self._nm and hasattr(self._nm, "frame_in"):
            try:
                self._nm.frame_in.disconnect(self._on_frame_compat)
            except Exception:
                log.debug("Failed to disconnect frame_in signal in connect_to_network_manager")
                pass
            self._nm.frame_in.connect(self._on_frame_compat)

    def _on_frame_compat(self, frame: DMXFrame):
        """
        ★ 兼容旧签名 (DMXFrame)：
        如果外部仍使用 connect_to_network_manager() 连接 frame_in，
        此方法将 DMXFrame 转换为 (universe, data) 格式。
        """
        if self._state != RecordState.RECORDING:
            return
        self._on_frame(frame.universe, frame.data)
