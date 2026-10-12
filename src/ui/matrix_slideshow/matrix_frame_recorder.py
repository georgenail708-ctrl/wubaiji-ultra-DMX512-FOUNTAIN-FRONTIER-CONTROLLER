# -*- coding: utf-8 -*-
"""matrix_frame_recorder.py — 把矩阵输出帧流录制并 bake 成时间线 clip（深查②三.Matrix #12）。

接口核实结论（2026-09-30）：
  * ``src/ui/_clip_baker.py`` 的 ``bake_scene_frames(scene, duration,
    addr_map, wave_luts, fps)`` **不可直接复用**——它吃的是 .cfo
    ``Scene.items``（每项要 eval_item 求值），而本项要录的是**已经算好的
    输出帧流**（无需求值），输入契约不匹配；强行套用要先伪造 Scene 与 wave LUT，
    纯属绕路。
  * 真正可复用的是**录制通道**：时间线的
    ``TimelineController.start_recording()/feed_dmx_frame()/stop_recording()``
    （它自带帧时间量化、active mask 归并、Clip 组装与挂轨）；
    无时间线时退回本模块自建 ``Clip`` 并挂到工程第一条非文件夹轨道。
  * 输出取值来自面板自己的矩阵层写值缓存 ``_matrix_layer_cache()``（即真正
    下发 NM 的值，含分区 gamma 后的结果），不是面板控件近似值。

纯函数（``frames_for_tick`` / ``build_clip``）供门禁与单测黄金数比对；
Qt 定时器只在录制期间跑（未录制 = 零开销）。
"""
from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot
from src.core.recorded_channel_masks import record_mask, normalized_masks

logger = logging.getLogger(__name__)

#: 录制帧率（与时间线控制器默认 40fps 解耦：矩阵计时器 25fps，取 25）
REC_FPS = 25
#: 录制时长上下限（秒）
MIN_SECONDS, MAX_SECONDS = 1.0, 600.0


def _coerce_channel(ch, val):
    """(通道, 值) → (0基下标, 0-255) 或 None（非法项跳过；helper）。"""
    try:
        idx = int(ch)
        v = int(round(float(val)))
    except (TypeError, ValueError):
        return None
    if 0 <= idx < 512:
        return idx, max(0, min(255, v))
    return None


def _frame_for_universe(universe, chans, timestamp: float):
    """Valid domain writes → frame and explicit mask; empty/invalid → None."""
    from src.core.models import DMXFrame
    data = np.zeros(512, dtype=np.uint8)
    active = np.zeros(512, dtype=bool)
    wrote = False
    for ch, val in chans.items():
        pair = _coerce_channel(ch, val)
        if pair is None:
            continue
        data[pair[0]] = pair[1]
        active[pair[0]] = True
        wrote = True
    if not wrote:
        return None
    return DMXFrame(universe=int(universe), timestamp=float(timestamp), data=data), active


def frames_for_tick(layer_cache: Dict[int, Dict[int, int]],
                    timestamp: float) -> List:
    """某一刻的矩阵层写值缓存 → [DMXFrame]（每 universe 一帧；纯函数）。

    ``layer_cache`` 形如 ``{universe: {0基通道: 0-255}}``（面板
    ``_matrix_layer_cache`` 的原始结构）；空层 → 空列表（不产生空帧）。
    """
    return [frame for frame, _mask in _frames_and_masks_for_tick(layer_cache, timestamp)]


def _frames_and_masks_for_tick(layer_cache, timestamp: float) -> list:
    """Reuse one validation/data pass, preserving active writes with value zero."""
    out = []
    for universe, chans in (layer_cache or {}).items():
        if not isinstance(chans, dict) or not chans:
            continue
        frame = _frame_for_universe(universe, chans, timestamp)
        if frame is not None:
            out.append(frame)
    return out


def active_channels_by_universe(frames) -> Dict[int, List[int]]:
    """帧列表 → {universe: [1 基通道...]}（任意帧非零即算活动；纯函数）。"""
    active: Dict[int, set] = {}
    for frame in frames or []:
        try:
            universe = int(getattr(frame, "universe", 1))
            data = np.asarray(getattr(frame, "data", []))
        except (TypeError, ValueError):
            continue
        if data.size == 0:
            continue
        idx = np.flatnonzero(data.astype(np.int16, copy=False) != 0)
        if idx.size:
            active.setdefault(universe, set()).update(int(i) + 1 for i in idx)
    return {u: sorted(chs) for u, chs in active.items()}


def _frame_ts(frame, default: float = 0.0) -> float:
    """帧时间戳（缺失/非法 → default；helper）。"""
    try:
        return float(getattr(frame, "timestamp", default) or 0.0)
    except (TypeError, ValueError):
        return float(default)


def _normalize_timestamps(frames) -> Tuple[List, float]:
    """时间戳整体归零（首帧 = 0）；返回 (帧列表, 末帧时间)（helper）。"""
    first = min(_frame_ts(f) for f in frames)
    last = 0.0
    for frame in frames:
        ts = max(0.0, _frame_ts(frame) - first)
        try:
            frame.timestamp = ts
        except Exception:
            logger.debug("matrix: shift frame timestamp failed", exc_info=True)
        last = max(last, ts)
    return frames, last


def build_clip(frames, *, label: str = "矩阵录制", fps: int = REC_FPS,
               start_time: float = 0.0, active_channels: dict | None = None):
    """帧列表 → ``Clip``（时间戳归零、duration 由末帧推出；纯函数）。"""
    from src.core.models import Clip
    import uuid
    items = list(frames or [])
    if not items:
        return None
    shifted, last_ts = _normalize_timestamps(items)
    duration = max(0.5, last_ts + 1.0 / max(1, int(fps)))
    universes = sorted({int(getattr(f, "universe", 1)) for f in shifted})
    return Clip(
        id=str(uuid.uuid4()),
        track_id="",
        label=str(label or "矩阵录制")[:40],
        start_time=max(0.0, float(start_time or 0.0)),
        duration=duration,
        playback_rate=1.0,
        source_duration=duration,
        frames=shifted,
        clip_universes=universes or [1],
        active_channels_by_universe=(active_channels_by_universe(shifted)
            if active_channels is None else {int(u): list(chs) for u, chs in active_channels.items()}),
        fade_in=0.0,
        fade_out=0.0,
    )


def _layer_name(panel) -> str:
    """面板当前矩阵输出层名（helper）。"""
    pick = getattr(panel, "_matrix_output_layer", None)
    try:
        return str(pick()) if callable(pick) else "matrix"
    except Exception:
        return "matrix"


def _nm_layer_channels(panel, layer_name: str) -> Dict[int, Dict[int, int]]:
    """NM 真实层（`(data, active)` 结构）→ {universe: {0基通道: 值}}（helper）。"""
    nm = getattr(panel, "_nm_ref", None)
    layers = getattr(nm, "_layers", None)
    if not isinstance(layers, dict):
        return {}
    raw = layers.get(layer_name)
    if not isinstance(raw, dict):
        return {}
    out: Dict[int, Dict[int, int]] = {}
    for universe, entry in raw.items():
        try:
            data, active = entry[0], entry[1]
            data = np.asarray(data)
            active = np.asarray(active, dtype=bool)
        except Exception:
            continue
        if data.size == 0:
            continue
        idx = np.flatnonzero(active) if active.size == data.size else np.arange(data.size)
        if idx.size == 0:
            continue
        out[int(universe)] = {int(i): int(data[i]) for i in idx if 0 <= int(i) < 512}
    return out


def snapshot_layer(panel) -> Dict[int, Dict[int, int]]:
    """面板**真实下发**的矩阵输出层 → {universe: {0基通道: 值}}。

    取值优先 NM 的矩阵层（`_layers[layer]` 的 (data, active) 结构，含稀疏活动掩码），
    面板自己的 `_matrix_output_cache` 仅作无 NM 时的回退；两层都空 → {}。
    """
    layer_name = _layer_name(panel)
    out = _nm_layer_channels(panel, layer_name)
    if out:
        return out
    getter = getattr(panel, "_matrix_layer_cache", None)
    if not callable(getter):
        return {}
    try:
        layer = getter(layer_name)
    except Exception:
        logger.debug("matrix: read output layer failed", exc_info=True)
        return {}
    for universe, chans in (layer or {}).items():
        if isinstance(chans, dict) and chans:
            out[int(universe)] = {int(k): int(v) for k, v in chans.items()}
    return out


class OutputRecorder(QObject):
    """矩阵输出录制器（面板单例；未录制时只有若干属性，无定时器）。"""

    state_changed = pyqtSignal(str)

    def __init__(self, panel, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._panel = panel
        self._seconds = 5.0
        self._started_at = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / REC_FPS))
        self._timer.timeout.connect(self._on_tick)
        self._stop_timer = QTimer(self)
        self._stop_timer.setSingleShot(True)
        self._stop_timer.timeout.connect(self._stop_automatically)
        self._ctrl = None
        self._local_frames: List = []
        self._local_masks: Dict[int, np.ndarray] = {}
        self._local_mask_samples = {}
        self._last_text = ""

    # ── 状态 ────────────────────────────────────────────────────────────────

    @property
    def recording(self) -> bool:
        return bool(self._timer.isActive())

    @property
    def seconds(self) -> float:
        return float(self._seconds)

    def state_text(self) -> str:
        """一行状态（录制中显示剩余秒数）。"""
        if not self.recording:
            return "未录制"
        left = max(0.0, self._seconds - (time.monotonic() - self._started_at))
        return f"录制中… 剩余 {left:0.1f}s"

    def _emit_state(self) -> None:
        text = self.state_text()
        if text != self._last_text:
            self._last_text = text
            try:
                self.state_changed.emit(text)
            except Exception:
                logger.debug("matrix: emit recorder state failed", exc_info=True)

    @pyqtSlot()
    def _stop_automatically(self) -> None:
        _ok, message = self.stop()
        self.state_changed.emit(message)

    # ── 起停 ────────────────────────────────────────────────────────────────

    def _begin_ctrl_recording(self, ctrl):
        """接管时间线录制；已被别人占用/启动失败 → None（退回自建；helper）。"""
        if bool(getattr(ctrl, "_recording", False)):
            return None
        try:
            ctrl.start_recording()
        except Exception:
            logger.debug("matrix: ctrl.start_recording failed", exc_info=True)
        if bool(getattr(ctrl, "_recording", False)):
            return ctrl
        return None

    def start(self, seconds: float = 5.0) -> Tuple[bool, str]:
        """开始录制 N 秒（自动停）。已有录制 → 拒绝（不打断别人的录制）。"""
        if self.recording:
            return False, "已在录制中"
        try:
            secs = min(MAX_SECONDS, max(MIN_SECONDS, float(seconds or 5.0)))
        except (TypeError, ValueError):
            secs = 5.0
        self._seconds = secs
        self._local_frames = []
        self._local_masks = {}
        self._local_mask_samples = {}
        ctrl = self._resolve_ctrl()
        self._ctrl = self._begin_ctrl_recording(ctrl) if ctrl is not None else None
        self._started_at = time.monotonic()
        self._timer.start()
        self._stop_timer.start(int(secs * 1000) + 30)
        self._emit_state()
        path = "时间线录制" if self._ctrl is not None else "本地帧缓存"
        return True, f"开始录制 {secs:0.1f}s（{path}，{REC_FPS}fps）"

    def stop(self) -> Tuple[bool, str]:
        """停止录制：时间线路径直接出 clip；本地路径 bake 后挂轨。"""
        if not self.recording:
            return False, "当前未录制"
        self._timer.stop()
        self._stop_timer.stop()
        self._on_tick()                     # 收尾补一帧，避免尾段缺帧
        if self._ctrl is not None:
            clip = None
            try:
                clip = self._ctrl.stop_recording()
            except Exception:
                logger.debug("matrix: ctrl.stop_recording failed", exc_info=True)
            self._ctrl = None
            self._local_frames = []
            self._local_masks = {}
            self._emit_state()
            if clip is None:
                return False, "录制结束但时间线未产出 clip（目标轨道可能已失效）"
            return True, f"已生成时间线 clip：{getattr(clip, 'label', '')}（{len(getattr(clip, 'frames', []) or [])} 帧）"
        return self._bake_local()

    def _bake_local(self) -> Tuple[bool, str]:
        """本地帧缓存 → Clip → 挂到工程第一条非文件夹轨道（helper）。"""
        frames = list(self._local_frames)
        offset = min((_frame_ts(frame) for frame in frames), default=0.)
        samples = normalized_masks(self._local_mask_samples, offset)
        self._local_mask_samples = {}
        self._local_frames = []
        active_channels = {u: (np.flatnonzero(mask) + 1).tolist()
                           for u, mask in self._local_masks.items()}
        self._local_masks = {}
        self._emit_state()
        clip = build_clip(frames, label=f"矩阵录制 {REC_FPS}fps", fps=REC_FPS,
                          active_channels=active_channels)
        if clip is None:
            return False, "未录到任何输出帧（录制期间矩阵没有出光）"
        clip.recorded_channel_masks = samples
        pm = getattr(self._panel, "_pm", None)
        track = _first_plain_track(pm)
        if track is None:
            return False, f"已录 {len(frames)} 帧，但工程里没有可用轨道（请先建一条轨道）"
        clip.track_id = str(getattr(track, "id", "") or "")
        try:
            if hasattr(track, "add_clip"):
                track.add_clip(clip)
            else:
                track.clips.append(clip)
                track.clips.sort(key=lambda c: float(getattr(c, "start_time", 0.0) or 0.0))
        except Exception:
            logger.debug("matrix: attach recorded clip failed", exc_info=True)
            return False, "clip 组装完成但挂轨失败"
        _notify_clip_added(pm, clip)
        return True, (f"已 bake 成 clip：{clip.label}（{len(frames)} 帧，"
                      f"{clip.duration:0.1f}s，{len(clip.clip_universes)} 域）")

    def _resolve_ctrl(self):
        """取时间线控制器（面板由 main_window 注入 `_timeline_ctrl_ref`）。"""
        ctrl = getattr(self._panel, "_timeline_ctrl_ref", None)
        if ctrl is None or not hasattr(ctrl, "start_recording"):
            return None
        if not hasattr(ctrl, "feed_dmx_frame") or not hasattr(ctrl, "stop_recording"):
            return None
        return ctrl

    def _on_tick(self) -> None:
        """定时器回调：抓一层写值 → 时间线 feed 或本地缓存。"""
        layer = snapshot_layer(self._panel)
        elapsed = max(0.0, time.monotonic() - float(self._started_at or 0.0))
        frames = _frames_and_masks_for_tick(layer, elapsed)
        from src.core.recorded_channel_masks import append_released_frames
        append_released_frames(frames, self._ctrl._rec_active_masks if self._ctrl is not None
                               else self._local_masks, elapsed)
        if not frames:
            self._emit_state()
            return
        if self._ctrl is not None:
            for frame, active in frames:
                try:
                    self._ctrl.feed_dmx_frame(int(frame.universe),
                                              frame.data,
                                              active)
                except Exception:
                    logger.debug("matrix: feed_dmx_frame failed", exc_info=True)
        else:
            for frame, active in frames:
                self._local_frames.append(frame)
                record_mask(self._local_mask_samples, frame.universe, frame.timestamp, active)
                previous = self._local_masks.get(frame.universe)
                if previous is None:
                    self._local_masks[frame.universe] = active
                else:
                    previous |= active
        self._emit_state()


def _first_plain_track(pm):
    """工程里第一条非文件夹轨道（无工程/无轨道 → None）。"""
    project = None
    try:
        project = getattr(pm, "project", None) or getattr(pm, "_project", None)
    except Exception:
        return None
    for track in (getattr(project, "tracks", []) or []):
        if not bool(getattr(track, "is_folder", False)):
            return track
    return None


def _notify_clip_added(pm, clip) -> None:
    """广播 clip 变更（有信号就发，缺失静默；helper）。"""
    for sig_name in ("clip_added", "project_changed"):
        sig = getattr(pm, sig_name, None)
        if sig is None:
            continue
        try:
            if sig_name == "clip_added":
                sig.emit(clip.id)
            else:
                sig.emit()
        except Exception:
            logger.debug("matrix: notify %s failed", sig_name, exc_info=True)


def recorder_of(panel) -> OutputRecorder:
    """取面板上的录制器单例（首次调用才创建）。

    ★ 清查⑧A P1-4：创建时挂 `destroyed → stop`（捕获 rec 引用）——修前面板
      销毁后 25fps 定时器仍在跑，每 tick 抛"wrapped C++ object deleted"，
      且 stop() 自己也抛（录制停不下来）。照 scene_pages.wire_zone_teardown
      的既有纪律（"面板删了它们还在"必须在 destroyed 上摘干净）。
    """
    rec = getattr(panel, "_output_recorder", None)
    if rec is None:
        rec = OutputRecorder(panel)
        try:
            panel._output_recorder = rec
        except Exception:
            logger.debug("matrix: attach recorder failed", exc_info=True)
        try:
            panel.destroyed.connect(
                lambda _obj=None, _r=rec: _safe_stop(_r))
        except Exception:
            logger.debug("matrix: recorder destroy-hook attach failed", exc_info=True)
    return rec


def _safe_stop(rec) -> None:
    """面板销毁时停表（全吞：此时 C++ 侧可能已删，任何访问都可能抛 RuntimeError）。"""
    try:
        rec.stop()
    except Exception:
        logger.debug("matrix: recorder stop on destroy failed", exc_info=True)


def toggle_recording(panel, seconds: float = 5.0) -> Tuple[bool, str]:
    """UI 入口：录制中 → 停止；否则开始（返回 (ok, 文本)）。"""
    rec = recorder_of(panel)
    return rec.stop() if rec.recording else rec.start(seconds)


__all__ = ["MAX_SECONDS", "MIN_SECONDS", "OutputRecorder", "REC_FPS",
           "active_channels_by_universe", "build_clip", "frames_for_tick",
           "recorder_of", "snapshot_layer", "toggle_recording"]
