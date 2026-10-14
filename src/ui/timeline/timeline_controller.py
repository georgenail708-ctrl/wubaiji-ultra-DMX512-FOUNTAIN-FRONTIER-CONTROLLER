"""
Qascade V1.0 — Timeline Controller  [FIXED v7]
★ v5: 录制时间戳量化 + 增强去重
★ v6: 同时间戳合并而不丢弃 + 跳过 universe 过滤
★ v7: 多宇宙录制元数据 + 稳定排序 + 同 tick 按 universe 合并
"""

from __future__ import annotations

import copy
import time
import uuid
from typing import Optional

import numpy as np
from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal

from ...core.models import Clip, DMXFrame, FilterRule, Track
from ...core.clip_smoothing_source import split_frames
from .clip_conflict import build_plan as _build_conflict_plan
from . import timeline_group_transform as _group_plan

try:
    from ...core.project_manager import DOMAIN_TIMELINE, ProjectManager as _PM
except Exception:
    _PM = None  # type: ignore
    DOMAIN_TIMELINE = frozenset()  # type: ignore


TICK_INTERVAL_MS = 23


class TimelineController(QObject):
    """统一管理 Session 中所有 Track / Clip 的增删改查与播放。"""

    tracks_changed        = pyqtSignal()
    clip_changed          = pyqtSignal(str)
    zoom_changed          = pyqtSignal(float)
    playback_tick         = pyqtSignal(float)  # Tier 0: EVERY frame (~43Hz) — output critical
    playback_tick_tier1   = pyqtSignal(float)  # Tier 1: Every 2nd frame (~21Hz) — visual feedback
    playback_tick_tier2   = pyqtSignal(float)  # Tier 2: Every 4th frame (~10Hz) — pure UI refresh
    playback_started      = pyqtSignal(float)
    playback_stopped      = pyqtSignal(float)
    remap_requested       = pyqtSignal(str, str)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self._session = session
        self.px_per_sec: float = 100.0

        self._playing      = False
        self._play_pos     = 0.0
        self._play_t0      = 0.0
        self._loop_enabled = False
        self._loop_start   = 0.0
        self._loop_end     = 30.0

        self._recording        = False
        self._rec_start_time   = 0.0
        self._rec_start_play_pos = 0.0
        self._rec_frames: list[DMXFrame] = []
        self._rec_frame_index: dict[tuple[int, int], DMXFrame] = {}
        self._rec_active_masks: dict[int, np.ndarray] = {}
        self._rec_mask_samples = {}
        self._rec_base_order_fixture_ids: list[str] = []
        self._rec_target_track: Optional[Track] = None
        self._rec_fps: float = 40.0

        # ★ R135：剪贴板 = **列表**（支持多选复制/粘贴）。每项是
        #   `(相对起点的偏移秒, clip)` —— 粘贴时按偏移铺开，保持相对间距。
        #   旧形态是单个 Clip；`has_clip_clipboard()` 语义不变（非空即 True）。
        self._clipboard: Optional[list] = None
        self._track_clipboard: Optional[Track] = None  # 轨道剪贴板

        # B3: GIL fleet effect elimination — frame counter for tiered tick dispatch
        self._tick_frame_counter: int = 0

        self._timer = QTimer(self)
        # ★ PreciseTimer 必设（2026-09-13 修）：Qt 默认 CoarseTimer 把 interval
        #   向下取整到 15.625ms 的倍数 —— 23ms 实际被调度成 31.57ms（29Hz 而非
        #   43Hz，慢 37%）。实测 interval 扫描：
        #     Coarse  23→31.57 / 25→31.68 / 33→47.52 ms
        #     Precise 23→23.00 / 25→25.02 / 33→33.03 ms
        #   这是"CE 数学很快（单帧 5.4ms）却整体卡"的关键一环：主时钟被静默
        #   降频，且与渲染无关（空载 gap 亦为 31.4ms）。
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(TICK_INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)

        self._ensure_independent_clip_lists()

    def _ensure_independent_clip_lists(self):
        seen_ids = set()
        for track in self._session.tracks:
            list_id = id(track.clips)
            if list_id in seen_ids:
                track.clips = list(track.clips)
            else:
                seen_ids.add(list_id)

    # ══════════════════════════════════════════════════════════════════════════
    #  录制
    # ══════════════════════════════════════════════════════════════════════════

    def start_recording(self, target_track_id: Optional[str] = None,
                        base_order_fixture_ids: Optional[list] = None):
        if self._recording:
            return

        self._ensure_independent_clip_lists()

        track = self._find_track(target_track_id)
        if track is not None and bool(getattr(track, "is_folder", False)):
            track = None
        if track is None:
            track = next(
                (t for t in getattr(self._session, "tracks", []) or []
                 if not bool(getattr(t, "is_folder", False))),
                None,
            )
            if track is None:
                return

        self._rec_frames       = []
        self._rec_frame_index  = {}
        self._rec_active_masks = {}
        self._rec_mask_samples = {}
        self._rec_base_order_fixture_ids = self._unique_fixture_id_list(base_order_fixture_ids or [])
        self._rec_start_time   = time.monotonic()
        self._rec_start_play_pos = max(0.0, float(
            self._rec_start_time - self._play_t0 if self._playing else self._play_pos))
        self._recording        = True
        self._rec_target_track = track

    def feed_dmx_frame(self, universe: int, data: np.ndarray, active_mask: Optional[np.ndarray] = None):
        """保留显式通道占用；按 (tick, universe) 合并主动刷新与 Main 轮询帧。"""
        if not self._recording or self._rec_target_track is None:
            return

        elapsed = time.monotonic() - self._rec_start_time
        tick_index = int(round(elapsed * self._rec_fps))
        quantized_time = tick_index / self._rec_fps
        try:
            universe = int(universe)
        except Exception:
            universe = 1

        if isinstance(data, np.ndarray) and data.dtype == np.uint8 and data.shape == (512,):
            raw_data = data
        else:
            raw_data = np.asarray(data).reshape(-1)
        if raw_data.shape[0] != 512:
            fixed = np.zeros(512, dtype=np.uint8)
            n = min(raw_data.shape[0], 512)
            if n > 0:
                fixed[:n] = np.clip(raw_data[:n], 0, 255).astype(np.uint8, copy=False)
            raw_data = fixed
        elif raw_data.dtype != np.uint8:
            raw_data = np.clip(raw_data, 0, 255).astype(np.uint8, copy=False)

        if active_mask is None:
            mask = raw_data != 0
        else:
            mask = np.asarray(active_mask, dtype=bool).reshape(-1)
            if mask.shape[0] != 512:
                fixed_mask = np.zeros(512, dtype=bool)
                n = min(mask.shape[0], 512)
                if n > 0:
                    fixed_mask[:n] = mask[:n]
                mask = fixed_mask

        from src.core.recorded_channel_masks import record_mask
        record_mask(self._rec_mask_samples, universe, quantized_time, active_mask)
        if np.any(mask):
            prev_mask = self._rec_active_masks.get(universe)
            if prev_mask is None:
                self._rec_active_masks[universe] = mask.copy()
            else:
                prev_mask |= mask

        key = (tick_index, universe)
        existing = self._rec_frame_index.get(key)
        if existing is not None:
            if np.array_equal(existing.data, raw_data):
                return
            existing.data = raw_data.copy()
            return

        frame = DMXFrame(
            timestamp=quantized_time,
            data=raw_data.copy(),
            universe=universe,
        )
        self._rec_frames.append(frame)
        self._rec_frame_index[key] = frame

    def stop_recording(self) -> Optional[Clip]:
        if not self._recording:
            return None
        duration = max(1.0 / self._rec_fps, time.monotonic() - self._rec_start_time)
        self._recording = False

        frames = sorted(
            list(self._rec_frames),
            key=lambda f: (float(getattr(f, "timestamp", 0.0)), int(getattr(f, "universe", 1))),
        )
        recorded_universes = sorted({int(getattr(f, "universe", 1)) for f in frames})
        if not recorded_universes:
            recorded_universes = list(getattr(self._rec_target_track, "universes", []) or [getattr(self._rec_target_track, 'universe', 1)])

        clip_id = str(uuid.uuid4())
        start_time = max(0.0, float(getattr(self, "_rec_start_play_pos", self._play_pos)))
        clip = Clip(
            id=clip_id,
            track_id=getattr(self._rec_target_track, "id", ""),
            label=f"REC {clip_id[:4]}",
            start_time=start_time,
            duration=duration,
            playback_rate=1.0,
            source_duration=duration,
            frames=frames,
            clip_universes=recorded_universes,
            active_channels_by_universe={
                int(uni): [int(i) + 1 for i, flag in enumerate(mask) if bool(flag)]
                for uni, mask in (self._rec_active_masks or {}).items()
                if np.any(mask)
            },
            fade_in=0.0,
            fade_out=0.0,
        )
        clip.base_order_fixture_ids = self._record_base_order_for_clip(recorded_universes)
        from src.core.recorded_channel_masks import normalized_masks
        clip.recorded_channel_masks = normalized_masks(self._rec_mask_samples)

        # ★ R131：录制目标轨道必须**回查**（原引用可能已被删/随撤销游离）。见下方函数。
        #   IfExp 承接"无可用轨道"（不计门禁 CC），把挂载交给 `_attach_recorded_clip`。
        track = self._resolve_rec_target_track()
        self._clear_rec_buffers()
        return (self._attach_recorded_clip(track, clip) if track is not None else None)

    def _attach_recorded_clip(self, track, clip):
        """把录好的 clip 挂到回查后的轨道上（并广播变更）。"""
        if hasattr(track, "add_clip"):
            track.add_clip(clip)
        else:
            clip.track_id = getattr(track, "id", "")
            track.clips.append(clip)
            track.clips.sort(key=lambda c: float(getattr(c, "start_time", 0.0) or 0.0))
        self.clip_changed.emit(clip.id)
        self.tracks_changed.emit()
        return clip

    def _resolve_rec_target_track(self):
        """录制目标轨道**回查**（原引用可能已被删除或随撤销换成了游离对象）。

        ★★ R131：录制期间 `delete_track` 无守卫、撤销会整体换 Project 对象 → 裸引用
        会指向游离对象。这里按 id 在**当前** `_session` 里查；查不到退回第一条非
        文件夹轨道（录制内容仍可见可编辑，宁可落错轨也不静默丢失）。
        """
        want = str(getattr(self._rec_target_track, "id", "") or "")
        track = self._find_track(want) if want else None
        if track is not None and not bool(getattr(track, "is_folder", False)):
            return track
        return next((t for t in getattr(self._session, "tracks", []) or []
                     if not bool(getattr(t, "is_folder", False))), None)

    def _clear_rec_buffers(self) -> None:
        """清空录制累积缓冲（成功收尾与目标失效丢弃两条路径共用）。"""
        self._rec_frames = []
        self._rec_frame_index = {}
        self._rec_active_masks = {}
        self._rec_mask_samples = {}
        self._rec_base_order_fixture_ids = []
        self._rec_start_play_pos = 0.0
        self._rec_target_track = None

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def is_playing(self) -> bool:
        """当前是否由播放按钮进入连续播放态；拖动指针 seek 不属于播放态。"""
        return bool(self._playing)

    # ══════════════════════════════════════════════════════════════════════════
    #  跨轨拖拽
    # ══════════════════════════════════════════════════════════════════════════

    def _snapshot_for_undo(self):
        """改数据前保存 undo 快照（R49 轻快照；R141 只序列化时间线域）。

        ★ 2026-09-19 用户实报：撤销只应回滚时间线字段 → 标记 DOMAIN_TIMELINE。
        R141 策略见 `timeline_perf.timeline_gesture_snapshot`（旧实现 dump 整工程：
        真工程单次手势 740ms，78MB 载荷里 74MB 是 drag 不碰的域）。
        """
        if _PM is None or not hasattr(_PM, "instance"):
            return
        from .timeline_perf import timeline_gesture_snapshot
        try:
            timeline_gesture_snapshot(_PM.instance(), DOMAIN_TIMELINE)  # R141
        except Exception:
            pass

    def handle_cross_track_drag_preview(
        self, clip_id: str, global_y: int, new_start: float, track_y_map: dict
    ):
        _, clip = self._find_track_and_clip(clip_id)
        if clip is None:
            return
        clip.start_time = max(0.0, new_start)
        self.clip_changed.emit(clip_id)

    def handle_cross_track_drag(
        self, clip_id: str, global_y: int, new_start: float, track_y_map: dict,
        snapshot: bool = True,
    ):
        """跨轨拖拽提交。snapshot=False：手势起点已拍过 pre-state 快照
        （clip_view 首次 move 时，模型=拖前状态），提交点不再重复拍——
        旧实现提交点快照发生在预览变异之后，undo 只能回到拖后位置（坏语义）。
        """
        src_track, clip = self._find_track_and_clip(clip_id)
        if src_track is None or clip is None:
            return
        if snapshot:
            self._snapshot_for_undo()
        dst_track_id = None
        for tid, (y_top, y_bot) in track_y_map.items():
            if y_top <= global_y < y_bot:
                dst_track_id = tid
                break

        clip.start_time = max(0.0, new_start)

        if dst_track_id is None or dst_track_id == src_track.id:
            self.clip_changed.emit(clip_id)
            return

        dst_track = self._find_track(dst_track_id)
        if dst_track is None:
            return

        # ★ 文件夹轨道不允许接收 Clip
        if getattr(dst_track, "is_folder", False):
            self.clip_changed.emit(clip_id)
            return

        self.remap_clip_to_track(clip_id, dst_track_id, snapshot=False)

    def remap_clip_to_track(self, clip_id: str, dst_track_id: str, snapshot: bool = True):
        src_track, clip = self._find_track_and_clip(clip_id)
        dst_track = self._find_track(dst_track_id)
        if not src_track or not clip or not dst_track:
            return
        if snapshot:
            self._snapshot_for_undo()
        # ★ 文件夹轨道不允许接收 Clip（双重防护）
        if getattr(dst_track, "is_folder", False):
            return

        clip.track_id = dst_track.id

        src_track.clips = [c for c in src_track.clips if c.id != clip_id]
        dst_track.clips.append(clip)
        try:
            dst_track.clips.sort(key=lambda c: float(getattr(c, "start_time", 0.0) or 0.0))
        except Exception:
            pass

        self.clip_changed.emit(clip_id)
        self.tracks_changed.emit()

    # ══════════════════════════════════════════════════════════════════════════
    #  标准操作
    # ══════════════════════════════════════════════════════════════════════════

    def move_clip(self, clip_id: str, new_start: float):
        _, clip = self._find_track_and_clip(clip_id)
        if clip:
            self._snapshot_for_undo()
            clip.start_time = max(0.0, new_start)
            self.clip_changed.emit(clip_id)

    def resize_clip(self, clip_id: str, new_duration: float, snapshot: bool = True):
        # snapshot=False 供多选批量拉伸收尾使用: undo 快照已由 clip_view
        # 手势首次确认拖动时拍过一次(模型=拖前状态), 逐 clip 收尾不再各自
        # 快照, 否则 N 个快照且首快照已含预览期改写 → undo 回不到拖前。
        _, clip = self._find_track_and_clip(clip_id)
        if clip:
            if snapshot:
                self._snapshot_for_undo()
            apply_clip_resize(clip, new_duration)
            self.clip_changed.emit(clip_id)

    def resolve_clip_conflicts(self, clip_ids: list[str], mode: str,
                               crossfade: bool = True) -> int:
        """多 clip 冲突处理：对给定 clip 集应用「过渡 / 合成」策略。

        - transition：为后接管 clip 设 fade_in（crossfade 时前者设 fade_out），
          复用引擎 fade_in 分支的「从上一动作输出位置平滑接管」。
        - composite：清掉参与冲突 clip 的过渡淡入淡出，让其满输出，交给引擎
          已有的同时段合成（不同通道叠加 / 相同通道 LTP）。

        只改 Clip.fade_in / fade_out 既有字段，不触碰引擎；返回被修改的 clip 数。
        """
        ids = [str(cid) for cid in (clip_ids or []) if str(cid)]
        if len(ids) < 2:
            return 0
        clip_map: dict[str, Clip] = {}
        for cid in ids:
            _, clip = self._find_track_and_clip(cid)
            if clip is not None and cid not in clip_map:
                clip_map[cid] = clip
        clips = list(clip_map.values())
        if len(clips) < 2:
            return 0

        plan = _build_conflict_plan(clips, mode, crossfade=crossfade)
        if not plan:
            return 0

        self._snapshot_for_undo()
        changed = 0
        for cid, edit in plan.items():
            clip = clip_map.get(cid)
            if clip is None:
                continue
            duration = max(0.0, float(getattr(clip, "duration", 0.0) or 0.0))
            touched = False
            if "fade_in" in edit:
                new_fi = max(0.0, min(float(edit["fade_in"]), duration))
                if abs(new_fi - float(getattr(clip, "fade_in", 0.0) or 0.0)) > 1e-6:
                    clip.fade_in = new_fi
                    touched = True
                else:
                    clip.fade_in = new_fi
            if "fade_out" in edit:
                # 保持模式下淡出无意义，沿用既有约定不强加
                if not bool(getattr(clip, "hold_last_frame", False)):
                    new_fo = max(0.0, min(float(edit["fade_out"]), duration))
                    if abs(new_fo - float(getattr(clip, "fade_out", 0.0) or 0.0)) > 1e-6:
                        clip.fade_out = new_fo
                        touched = True
                    else:
                        clip.fade_out = new_fo
            if touched:
                changed += 1
            self.clip_changed.emit(cid)
        if changed:
            self.tracks_changed.emit()
        return changed

    def delete_clip(self, clip_id: str):
        self._snapshot_for_undo()
        self._remove_clip_globally(clip_id)
        self.tracks_changed.emit()

    def duplicate_clip(self, clip_id: str, snapshot: bool = True) -> Optional[Clip]:
        """复制片段。`snapshot=True`（默认）= 自动压一条撤销快照。
        ★ R141 修 bug ⑦：多选循环会压 N 条；`duplicate_many` 先快照一次后循环。"""
        track, clip = self._find_track_and_clip(clip_id)
        if track is None or clip is None:
            return None
        if snapshot:
            self._snapshot_for_undo()
        new_clip = copy.deepcopy(clip)
        new_clip.id = str(uuid.uuid4())
        new_clip.track_id = track.id
        # ★ R172 §2.4：复制不继承 sheet 绑定（避免两个 clip 隐式共享同一后处理页，
        #   Studio 的防抖应用会同时打到两者）；新 clip 需显式绑定或从菜单新建。
        new_clip.process_sheet_id = ""
        new_clip.start_time = clip.start_time + clip.duration + 0.1
        new_clip.label = (clip.label or "Clip") + " (copy)"
        if hasattr(track, "add_clip"):
            track.add_clip(new_clip)
        else:
            track.clips.append(new_clip)
            track.clips.sort(key=lambda c: float(getattr(c, "start_time", 0.0) or 0.0))
        self.clip_changed.emit(new_clip.id)
        self.tracks_changed.emit()
        return new_clip

    def copy_clip(self, clip_id):
        """Copy original payloads with relative time and receiving-track offsets."""
        _group_plan.copy_to_clipboard(self, clip_id)

    def _collect_clips_for_clipboard(self, clip_ids) -> list:
        """把传入的 id（单个/列表）解析成 `[(原轨, clip)]`，按起点排序（无则空）。"""
        ids = [clip_ids] if isinstance(clip_ids, str) else list(clip_ids or [])
        out = []
        for cid in ids:
            try:
                track, clip = self._find_track_and_clip(str(cid))
            except Exception:
                continue
            if clip is not None:
                out.append((track, clip))
        out.sort(key=lambda it: float(getattr(it[1], "start_time", 0.0) or 0.0))
        return out

    def paste_to_track(self, track_id: str, time_pos: float):
        return _group_plan.paste_to_track(self, track_id, time_pos)

    def _clipboard_items(self) -> list:
        """剪贴板条目 `[(偏移, clip)]`（**兼容旧的单 Clip 形态**，防外部直接赋值）。"""
        cb = self._clipboard
        if cb is None:
            return []
        if isinstance(cb, list):
            return [(float(o), c) for o, c in cb]
        return [(0.0, cb)]      # 旧形态：单个 Clip → 偏移 0

    def paste_clip_at_cursor(self, time_pos: float) -> bool:
        """把剪贴板 clip 粘到**当前选中**轨道（无选中则第一条非文件夹轨道）。

        ★ R131：Ctrl+V 原先把目标写死成 `tracks[0]`，既忽略用户选中，又可能落在
        文件夹轨道上（生成看不见/删不掉的死 clip）。收口到本方法后，两条粘贴入口
        共用同一套"目标合法性"判定。
        返回是否真的粘了（供 UI 决定要不要提示）。
        """
        if self._clipboard is None:
            return False
        tracks = list(getattr(self._session, "tracks", []) or [])
        pool = [t for t in tracks if not bool(getattr(t, "is_folder", False))]
        if not pool:
            return False
        sel = str(getattr(self, "_selected_track_id", "") or "")
        target = next((t for t in pool if str(getattr(t, "id", "")) == sel), pool[0])
        return self.paste_to_track(str(getattr(target, "id", "")), float(time_pos)) is not False

    def cut_to_clipboard(self, clip_id):
        """Cut as one original PM transaction and keep relative placement."""
        _group_plan.copy_to_clipboard(self, clip_id, cut=True)

    def razor_cut_clip(self, clip_id: str, cut_time: float):
        """在绝对时间 cut_time 处把 clip 切成左右两个片段。

        修复点：旧实现只按时间戳简单分组，右片段可能没有 0 秒起始帧，且无右侧帧时
        right_dur 会误用左片段时长。新实现为每个 universe 复制切点处的“当前帧”作为
        右片段 0 秒边界帧，保证切开后从切点继续播放，不会出现右半段空白或跳变。
        """
        track, clip = self._find_track_and_clip(clip_id)
        if track is None or clip is None:
            return
        self._snapshot_for_undo()
        original_start = max(0.0, float(getattr(clip, "start_time", 0.0)))
        original_duration = max(0.0, float(getattr(clip, "duration", 0.0)))
        rel_cut = float(cut_time) - original_start
        if rel_cut <= 0.05 or rel_cut >= original_duration - 0.05:
            return
        playback_rate = max(0.001, float(getattr(clip, "playback_rate", 1.0) or 1.0))
        original_source_duration = max(
            0.05,
            float(getattr(clip, "source_duration", 0.0) or 0.0) or (original_duration * playback_rate),
        )
        source_cut = min(original_source_duration, max(0.0, rel_cut * playback_rate))

        frames = getattr(clip, "frames", []) or []
        universes = sorted({int(getattr(f, "universe", 1)) for f in frames})
        if not universes:
            universes = list(getattr(clip, "clip_universes", []) or getattr(track, "universes", []) or [getattr(track, "universe", 1)])

        left_frames, right_frames = split_frames(frames, source_cut)
        left_source, right_source = split_frames(clip.smooth_source_frames, source_cut)

        original_label = getattr(clip, "label", None) or "Clip"
        original_fade_in = max(0.0, float(getattr(clip, "fade_in", 0.0) or 0.0))
        original_fade_out = max(0.0, float(getattr(clip, "fade_out", 0.0) or 0.0))
        right_dur = max(0.05, original_duration - rel_cut)
        left_dur = max(0.05, rel_cut)
        left_source_dur = max(0.05, source_cut)
        right_source_dur = max(0.05, original_source_duration - source_cut)

        clip.duration = left_dur
        clip.source_duration = left_source_dur
        clip.playback_rate = playback_rate
        clip.frames = left_frames
        clip.smooth_source_frames = left_source
        clip.label = original_label + "-L"
        clip.fade_in = min(original_fade_in, left_dur)
        fade_out_start = original_duration - original_fade_out
        clip.fade_out = min(original_fade_out, left_dur) if rel_cut > fade_out_start else 0.0

        right_clip = copy.deepcopy(clip)
        right_clip.id = str(uuid.uuid4())
        right_clip.track_id = track.id
        right_clip.label = original_label + "-R"
        right_clip.start_time = original_start + rel_cut
        right_clip.duration = right_dur
        right_clip.frames = right_frames
        right_clip.smooth_source_frames = right_source
        right_clip.clip_universes = list(getattr(clip, "clip_universes", []) or universes)
        right_clip.fade_in = (
            min(max(0.0, original_fade_in - rel_cut), right_dur)
            if rel_cut < original_fade_in else 0.0
        )
        right_clip.fade_out = min(original_fade_out, right_dur)
        right_clip.playback_rate = playback_rate
        right_clip.source_duration = right_source_dur
        from src.core.recorded_channel_masks import split_clip_masks
        split_clip_masks(clip, right_clip, source_cut)
        idx = track.clips.index(clip)
        track.clips.insert(idx + 1, right_clip)

        self.clip_changed.emit(clip_id)
        self.clip_changed.emit(right_clip.id)
        self.tracks_changed.emit()

    # ══════════════════════════════════════════════════════════════════════════
    #  轨道管理
    # ══════════════════════════════════════════════════════════════════════════

    def copy_track(self, track_id: str):
        """将轨道（含所有 Clip）复制到轨道剪贴板。"""
        t = self._find_track(track_id)
        if t is not None:
            self._track_clipboard = copy.deepcopy(t)

    def paste_track_at_pos(self, cursor_time: float):
        """将轨道剪贴板中的轨道粘贴为新轨道，所有 Clip 平移到 cursor_time 开始。

        平移规则：找到最早的 Clip start_time，将其对齐到 cursor_time，
        其余 Clip 保持相对间距不变。
        """
        if self._track_clipboard is None:
            return
        new_track = copy.deepcopy(self._track_clipboard)
        new_track.id = str(uuid.uuid4())
        new_track.name = (new_track.name or "Track") + " (paste)"
        # 重新生成所有 Clip ID
        for clip in new_track.clips:
            clip.id = str(uuid.uuid4())
            clip.track_id = new_track.id
        # 将所有 Clip 平移到 cursor_time
        if new_track.clips:
            earliest = min(c.start_time for c in new_track.clips)
            offset = max(0.0, cursor_time) - earliest
            for clip in new_track.clips:
                clip.start_time = max(0.0, clip.start_time + offset)
        self._session.tracks.append(new_track)
        self.tracks_changed.emit()

    def has_track_clipboard(self) -> bool:
        return self._track_clipboard is not None

    def has_clip_clipboard(self) -> bool:
        return self._clipboard is not None

    def add_track(self, track: Track):
        # ★ R131：轨道级操作原先**全都不拍快照** → 删轨道连 Ctrl+Z 都救不回
        #   （见 delete_track）。这里补齐，使"轨道增删改"与 clip 操作同等可撤销。
        self._snapshot_for_undo()
        if not isinstance(track.clips, list) or any(
            track.clips is t.clips for t in self._session.tracks
        ):
            track.clips = list(track.clips) if track.clips else []
        self._session.tracks.append(track)
        self.tracks_changed.emit()

    def add_track_after(self, track: Track, after_id: str = ""):
        """将新轨道插入到 after_id 指定轨道的正后方。
        若 after_id 为空或找不到，则默认 append 到末尾。"""
        self._snapshot_for_undo()
        if not isinstance(track.clips, list) or any(
            track.clips is t.clips for t in self._session.tracks
        ):
            track.clips = list(track.clips) if track.clips else []
        if after_id:
            idx = next((i for i, t in enumerate(self._session.tracks)
                        if t.id == after_id), None)
            if idx is not None:
                self._session.tracks.insert(idx + 1, track)
                self.tracks_changed.emit()
                return
        self._session.tracks.append(track)
        self.tracks_changed.emit()

    def delete_track(self, track_id: str):
        # ★★ R131 修复（录制中删目标轨道 → 整段录制静默丢失）：原先无任何录制态
        #   守卫，删掉正在录的那条轨道后 `stop_recording` 仍会把 clip 挂到**游离
        #   对象**上（谁都看不见），而 `_stop_record` 照样弹"已保存"+ 标脏。
        #   这里直接拒绝：宁可让用户先停录，也不能让它悄悄丢。
        rec_target = str(getattr(getattr(self, "_rec_target_track", None), "id", "") or "")
        if self._recording and rec_target and rec_target == str(track_id):
            return
        self._snapshot_for_undo()
        self._session.tracks = [t for t in self._session.tracks if t.id != track_id]
        self.tracks_changed.emit()

    def rename_track(self, track_id: str, name: str):
        t = self._find_track(track_id)
        if t:
            self._snapshot_for_undo()
            t.name = name
            self.tracks_changed.emit()

    def move_track(self, track_id: str, new_idx: int):
        tracks = self._session.tracks
        idx = next((i for i, t in enumerate(tracks) if t.id == track_id), None)
        if idx is None:
            return
        self._snapshot_for_undo()
        track = tracks.pop(idx)
        tracks.insert(max(0, new_idx), track)
        self.tracks_changed.emit()

    def set_track_color(self, track_id: str, color: str):
        t = self._find_track(track_id)
        if t:
            t.color = color
            self.tracks_changed.emit()

    def set_track_mute(self, track_id: str, muted: bool):
        t = self._find_track(track_id)
        if not t:
            return

        target_ids = {track_id}
        if bool(getattr(t, "is_folder", False)):
            target_ids.update(self._descendant_track_ids(track_id))

        for track in self._session.tracks:
            if getattr(track, "id", "") in target_ids:
                track.muted = muted
        self.tracks_changed.emit()

    def set_track_solo(self, track_id: str, soloed: bool):
        target = self._find_track(track_id)
        if not target:
            return

        target_ids = {track_id}
        if bool(getattr(target, "is_folder", False)):
            target_ids.update(self._descendant_track_ids(track_id))

        for t in self._session.tracks:
            if getattr(t, "id", "") in target_ids:
                t.solo = bool(soloed)
        self.tracks_changed.emit()

    # ══════════════════════════════════════════════════════════════════════════
    #  播放 / 缩放
    # ══════════════════════════════════════════════════════════════════════════

    def play(self):
        from .timeline_sntc_control import perform_local
        return perform_local(self, '_play_local')

    def _play_local(self):
        if self._playing:
            return
        self._playing = True
        self._play_t0 = time.monotonic() - self._play_pos
        self._timer.start()
        self.playback_started.emit(self._play_pos)

    def pause(self):
        from .timeline_sntc_control import perform_local
        return perform_local(self, '_pause_local')

    def _pause_local(self):
        self._playing = False
        self._timer.stop()
        self.playback_stopped.emit(self._play_pos)

    def stop(self):
        from .timeline_sntc_control import perform_local
        return perform_local(self, '_stop_local')

    def stop_for_release(self):
        from .timeline_sntc_control import release_external
        release_external(self)
        self._stop_local()

    def _stop_local(self):
        self._playing = False
        self._play_pos = 0.0
        self._timer.stop()
        self.playback_tick.emit(0.0)
        self.playback_stopped.emit(0.0)
        # B3: playhead redraw lives on Tier 2 (see _on_tick). Discrete stops don't
        # run the tick loop, so emit Tier 2 explicitly or the playhead never resets to 0.
        # Stop media before this UI reset can seek a still-running decoder.
        self.playback_tick_tier2.emit(0.0)

    def seek(self, pos: float):
        from .timeline_sntc_control import perform_local
        return perform_local(self, '_seek_local', pos)

    def _seek_local(self, pos: float):
        self._play_pos = max(0.0, pos)
        if self._playing:
            self._play_t0 = time.monotonic() - self._play_pos
        self.playback_tick.emit(self._play_pos)
        # B3: playhead redraw lives on Tier 2. A drag/scrub seek while not playing
        # never emits Tier 2 via the tick loop, so the ruler needle (moved locally by
        # its own mouseMove) and the clip-area overlay would split apart. Emit Tier 2
        # here so both needles track every discrete seek, not just during playback.
        self.playback_tick_tier2.emit(self._play_pos)

    def set_zoom(self, px_per_sec: float):
        # ★ R183 T4：上下限唯一真源在 timeline_zoom（修 500 vs 2000 自相矛盾）。
        from .timeline_zoom import clamp_zoom
        self.px_per_sec = clamp_zoom(px_per_sec)
        self.zoom_changed.emit(self.px_per_sec)

    def set_loop_range(self, start: float, end: float, enabled: bool = True):
        """时间线循环范围（由 ruler.loop_range_changed 驱动）；存起止 + 启用循环。

        ★ R183 T2/T6：#4 循环区持久化 —— `enabled` 供工程装载回填时**原样恢复**
          开关（用户关掉循环后保存，再打开不该自己又循环上），默认 True 保持既有
          ruler 拖动路径语义不变。
        ★ 审计⑦B P1-6：清零调用（`start == end`）必须**原样接受**（写出
          `(0.0, 0.0, False)`），否则 `end` 被抬到 `start+0.1` → 换工程后残留
          0.1s 的"幽灵循环区"（虽然 enabled=False 时无行为影响，但状态不干净、
          且 `loop_range()` 读出假区间）。
        """
        try:
            s = max(0.0, float(start))
            e = float(end)
        except (TypeError, ValueError):
            return
        if e <= s:
            # 清零/非法 → 关掉循环且区间归零（不伪造 +0.1 的假区间）
            self._loop_start = s
            self._loop_end = s
            self._loop_enabled = False
            return
        self._loop_start = s
        self._loop_end = e
        self._loop_enabled = bool(enabled)

    def loop_range(self) -> tuple:
        """当前循环区 `(start, end, enabled)`（持久化/撤销回填用）。"""
        return (float(self._loop_start), float(self._loop_end),
                bool(self._loop_enabled))

    def _on_tick(self):
        from .timeline_sntc_control import external_tick
        if not external_tick(self):
            self._on_tick_local()

    def _on_tick_local(self):
        self._play_pos = time.monotonic() - self._play_t0
        if self._loop_enabled and self._play_pos >= self._loop_end:
            self.seek(self._loop_start)
            return

        self._tick_frame_counter += 1
        fc = self._tick_frame_counter
        # Tier 0: EVERY frame (43Hz) — output critical (clip render); transport 位置显示已降 Tier 2（R173）
        # Tier 0: EVERY frame (43Hz) — output critical (clip render + transport)
        self.playback_tick.emit(self._play_pos)

        # Tier 1: Every 2nd frame (~21Hz) — visual feedback (programmer, audio, matrix)
        if fc % 2 == 0:
            self.playback_tick_tier1.emit(self._play_pos)

        # Tier 2: Every 4th frame (~10Hz) — pure UI refresh (timeline redraw, channel controller)
        if fc % 4 == 0:
            self.playback_tick_tier2.emit(self._play_pos)

    # ══════════════════════════════════════════════════════════════════════════
    #  混音输出
    # ══════════════════════════════════════════════════════════════════════════

    def render_mix(self, universe: int, start: float, end: float, fps: float = 44.0) -> list[DMXFrame]:
        n_frames = max(1, int((end - start) * fps))
        mix = np.zeros((n_frames, 512), dtype=np.float32)
        universe = int(universe)

        tracks = list(getattr(self._session, "tracks", []) or [])
        any_solo = any(
            self._track_or_ancestor_soloed(t)
            for t in tracks
            if not bool(getattr(t, "is_folder", False)) and not self._track_or_ancestor_muted(t)
        )
        for track in tracks:
            if bool(getattr(track, "is_folder", False)):
                continue
            track_universes = set(getattr(track, "universes", []) or [getattr(track, "universe", 1)])
            if self._track_or_ancestor_muted(track) or universe not in track_universes:
                continue
            if any_solo and not self._track_or_ancestor_soloed(track):
                continue
            for clip in track.clips:
                clip_universes = set(getattr(clip, "clip_universes", []) or track_universes)
                if universe not in clip_universes:
                    continue
                if clip.start_time >= end or clip.start_time + clip.duration <= start:
                    continue
                for frame in (clip.frames or []):
                    if int(getattr(frame, "universe", universe)) != universe:
                        continue
                    abs_t = clip.start_time + frame.timestamp
                    fi = int((abs_t - start) * fps)
                    if 0 <= fi < n_frames:
                        mix[fi] = np.maximum(mix[fi], frame.data.astype(np.float32))

        dt = 1.0 / fps
        return [
            DMXFrame(timestamp=start + i * dt, data=np.clip(mix[i], 0, 255).astype(np.uint8), universe=universe)
            for i in range(n_frames)
        ]

    # ══════════════════════════════════════════════════════════════════════════
    #  内部工具
    # ══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _unique_fixture_id_list(values) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for value in values or []:
            fid = str(value or "")
            if fid and fid not in seen:
                out.append(fid)
                seen.add(fid)
        return out

    def _fixture_channel_span(self, fi, fixture_defs_by_id: Optional[dict],
                              ordered_fixtures: Optional[list] = None,
                              index: int = -1) -> tuple[int, int]:
        start = int(getattr(fi, "start_channel", 1) or 1) - 1
        if not (0 <= start < 512):
            return start, start
        fixture_def = None
        if fixture_defs_by_id is not None:
            fixture_def = fixture_defs_by_id.get(str(getattr(fi, "def_id", "")))
        ch_count = 0
        if fixture_def is not None:
            try:
                ch_count = int(getattr(fixture_def, "channel_count", 0) or 0)
            except Exception:
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

    def _recorded_fixture_ids_from_active_masks(self, recorded_universes: list[int]) -> list[str]:
        fixtures = list(getattr(self._session, "fixtures", []) or [])
        if not fixtures:
            return []
        fixture_defs_by_id = {
            str(getattr(fd, "id", "")): fd
            for fd in (getattr(self._session, "fixture_defs", []) or [])
            if getattr(fd, "id", None)
        }
        active_by_universe: dict[int, np.ndarray] = {
            int(uni): np.asarray(mask, dtype=bool).reshape(-1)[:512]
            for uni, mask in (self._rec_active_masks or {}).items()
            if mask is not None
        }
        if not active_by_universe:
            for frame in self._rec_frames or []:
                try:
                    uni = int(getattr(frame, "universe", 1) or 1)
                    raw = np.asarray(getattr(frame, "data", np.zeros(512, dtype=np.uint8)), dtype=np.uint8)
                    mask = np.zeros(512, dtype=bool)
                    n = min(raw.size, 512)
                    if n > 0:
                        mask[:n] = raw.reshape(-1)[:n] != 0
                    prev = active_by_universe.get(uni)
                    active_by_universe[uni] = mask if prev is None else (prev | mask)
                except Exception:
                    continue
        universe_filter = {int(u) for u in (recorded_universes or [])}
        by_universe: dict[int, list] = {}
        for fi in fixtures:
            try:
                uni = int(getattr(fi, "universe", 1) or 1)
            except Exception:
                uni = 1
            if universe_filter and uni not in universe_filter:
                continue
            by_universe.setdefault(uni, []).append(fi)

        recorded: list[str] = []
        seen: set[str] = set()
        for uni in sorted(by_universe):
            ordered = sorted(by_universe[uni], key=lambda fi: int(getattr(fi, "start_channel", 1) or 1))
            active = active_by_universe.get(int(uni))
            if active is None or not np.any(active):
                continue
            if active.shape[0] != 512:
                fixed = np.zeros(512, dtype=bool)
                n = min(active.size, 512)
                if n > 0:
                    fixed[:n] = active.reshape(-1)[:n]
                active = fixed
            for idx, fi in enumerate(ordered):
                fid = str(getattr(fi, "id", "") or "")
                if not fid or fid in seen:
                    continue
                start, end = self._fixture_channel_span(fi, fixture_defs_by_id, ordered, idx)
                if 0 <= start < end <= 512 and bool(np.any(active[start:end])):
                    recorded.append(fid)
                    seen.add(fid)
        return recorded

    def _record_base_order_for_clip(self, recorded_universes: list[int]) -> list[str]:
        frozen = self._unique_fixture_id_list(self._rec_base_order_fixture_ids)
        recorded = self._recorded_fixture_ids_from_active_masks(recorded_universes)
        if recorded:
            recorded_set = set(recorded)
            ordered = [fid for fid in frozen if fid in recorded_set]
            seen = set(ordered)
            ordered.extend(fid for fid in recorded if fid not in seen)
            return ordered
        if frozen:
            return frozen
        fixtures = list(getattr(self._session, "fixtures", []) or [])
        universe_filter = {int(u) for u in (recorded_universes or [])}
        ordered_fixtures = sorted(
            [
                fi for fi in fixtures
                if not universe_filter or int(getattr(fi, "universe", 1) or 1) in universe_filter
            ],
            key=lambda fi: (
                int(getattr(fi, "universe", 1) or 1),
                int(getattr(fi, "start_channel", 1) or 1),
                str(getattr(fi, "id", "") or ""),
            ),
        )
        return self._unique_fixture_id_list(str(getattr(fi, "id", "") or "") for fi in ordered_fixtures)

    def _find_track(self, track_id: Optional[str]) -> Optional[Track]:
        if track_id is None:
            return None
        return next((t for t in self._session.tracks if t.id == track_id), None)

    def _descendant_track_ids(self, track_id: str) -> set[str]:
        children_by_parent: dict[str, list[Track]] = {}
        for track in getattr(self._session, "tracks", []) or []:
            children_by_parent.setdefault(getattr(track, "parent_id", "") or "", []).append(track)

        result: set[str] = set()
        stack = [track_id]
        seen = {track_id}
        while stack:
            parent_id = stack.pop()
            for child in children_by_parent.get(parent_id, []):
                child_id = getattr(child, "id", "") or ""
                if not child_id or child_id in seen:
                    continue
                seen.add(child_id)
                result.add(child_id)
                stack.append(child_id)
        return result

    def _track_or_ancestor_muted(self, track: Track) -> bool:
        if bool(getattr(track, "muted", False)):
            return True

        track_by_id = {
            getattr(t, "id", ""): t
            for t in getattr(self._session, "tracks", []) or []
            if getattr(t, "id", "")
        }
        seen: set[str] = set()
        parent_id = getattr(track, "parent_id", "") or ""
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = track_by_id.get(parent_id)
            if parent is None:
                break
            if bool(getattr(parent, "muted", False)):
                return True
            parent_id = getattr(parent, "parent_id", "") or ""
        return False

    def _track_or_ancestor_soloed(self, track: Track) -> bool:
        if bool(getattr(track, "solo", False)):
            return True

        track_by_id = {
            getattr(t, "id", ""): t
            for t in getattr(self._session, "tracks", []) or []
            if getattr(t, "id", "")
        }
        seen: set[str] = set()
        parent_id = getattr(track, "parent_id", "") or ""
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = track_by_id.get(parent_id)
            if parent is None:
                break
            if bool(getattr(parent, "solo", False)):
                return True
            parent_id = getattr(parent, "parent_id", "") or ""
        return False

    def _find_track_and_clip(self, clip_id: str) -> tuple[Optional[Track], Optional[Clip]]:
        for track in self._session.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    return track, clip
        return None, None

    def _remove_clip_globally(self, clip_id: str):
        for track in self._session.tracks:
            before = len(track.clips)
            track.clips = [c for c in track.clips if c.id != clip_id]
            if len(track.clips) < before:
                break


def apply_clip_resize(clip, new_duration: float) -> None:
    """把"拖右缘改时长"落到 clip 上（**controller 与 main_window 共用同一份语义**）。

    ★ R12(2026-08-13)：拖右缘只改内容时长（duration）；顺序分组结构（单组窗口/
      扫掠/停顿）由引擎按过渡翼派生、旧工程由渲染侧 duration 缩放，不再强制
      span==duration、不再联动改写 single/rate/pauses。

    ★★ 2026-09-14 用户定案（**不改速**）：拖右缘**不再**把时长折算成播放速度。
      "默认拖动 clip 的长度，不论缩小还是放大，都是不变速的，只是改变效果的时长，
       也就是如果是往后拉，按照周期自动扩展效果往后，以及如果是缩小，就自动裁剪
       效果时长一样，不再去加速它了。"
      → 默认保持 `playback_rate` 不变：拉长由引擎按周期补全（超源跨度的部分回绕），
        缩短由引擎 `end_t` 自然裁剪。
      → 只有**显式**走过右击菜单「速度 / 持续时间」的 clip（`resize_cycle=False`）
        才继续按旧语义用 rate 折算时长（保持"速度读数"不变）。

    ★★★ R163(2026-09-23) 用户定案（**翼是唯一权威**）：
      "过渡翼和延迟翼是一切效果延迟的最终管理"、"clip 总长决定效果淡入最终时间"
      （用户报的灰色地带）。故本函数**只改** `duration`，四个翼字段一律**不动** ——
      既不按比例缩放（R144 的 `_scale_sequence_wing`，实测在真实拖拽里因
      `ratio == 1.0` 早退而从不执行），也不钳到新时长（旧 `_clamp_fades`）。
      翼装不下时由引擎渲染期临时压紧（不改字段，拉长自动恢复）。
    """
    duration = max(0.05, float(new_duration or 0.05))
    clip.source_duration = _resolve_source_duration(clip)
    clip.duration = duration
    if not bool(getattr(clip, "resize_cycle", True)):
        # 显式变速过的 clip：保持"速度读数"（用 rate 折算时长）
        clip.playback_rate = max(0.001, min(100.0, clip.source_duration / duration))
    _clamp_fades(clip, duration)


def _resolve_source_duration(clip) -> float:
    """100% 速度下的源时长（缺失时由 旧时长×旧速率 推出），下限 0.05。"""
    src = max(0.0, float(getattr(clip, "source_duration", 0.0) or 0.0))
    if src <= 0.0:
        old_duration = max(0.05, float(getattr(clip, "duration", 0.05) or 0.05))
        old_rate = max(0.001, float(getattr(clip, "playback_rate", 1.0) or 1.0))
        src = old_duration * old_rate
    return max(0.05, src)


def _clamp_fades(clip, duration: float) -> None:
    """只保证四个翼**非负**；**不再**把它们裁到新时长（R163 用户定案）。

    用户规则："过渡翼和延迟翼是一切效果延迟的最终管理" —— 时长与翼互相独立，
    翼是过渡/相位的权威。旧写法 `min(翼, duration)` 会在**缩短**时**永久**截短翼
    （拉长不恢复），于是"clip 总长决定效果淡入最终时间"（用户报的灰色地带）。

    翼装不下时由引擎渲染期临时压紧（`_clamp_window_to_duration`：不改字段、
    只压缩当次扫掠跨度，并打 `[SEQ]` 警告），拉长后自动恢复。

    `duration` 参数保留是为了让调用点签名不变（且便于将来加"下界提示"）。
    """
    del duration  # 语义上不再参与钳制（见 docstring）
    for attr in ("fade_in", "fade_out", "seq_delay_in", "seq_delay_out"):
        try:
            cur = float(getattr(clip, attr, 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if cur < 0.0:
            setattr(clip, attr, 0.0)
