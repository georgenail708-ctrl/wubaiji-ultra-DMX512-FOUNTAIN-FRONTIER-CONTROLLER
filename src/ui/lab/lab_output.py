"""实验室输出驱动 —— 独立播放时钟 + 逐帧写 `ml_lab` 混音层。

三条与主系统的隔离契约（用户明确要求"除真实可输出外不参与正常衔接"）：
  1) 音频独立：用实验室自己的 QMediaPlayer 播放**本次导入的音频**，
     绝不动 Project.audio_path / AudioRefTrack / 全局 AudioAnalyzer。
  2) 时钟独立：帧号 = 自有播放位置 × fps，不依赖 TimelineController。
  3) 层隔离：只写 `ml_lab` 层；停止/切设备/关窗必定 clear_layer，
     不留残值（否则旧值会持续输出到真实设备）。

安全（对齐真实执行器语义，不是"艺术意图"）：
  * 泵/电机通道按设备 `max_change_rate` 逐帧限速（来自模块 dmx_semantics，
    即主系统 device_type_registry 的搬运），灯具通道直通。
  * 泵预充：clip 起点前向泵通道发一个短促预充值（VFD 就绪频率），
    在真实硬件上避免"该出水时慢半拍"。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer

LAYER_NAME = "ml_lab"
DEFAULT_FPS = 30.0

# 泵通道每帧最大变化（DMX 量纲）。对齐模块 dmx_semantics：
#   max_change_rate/255 × 40fps ≈ 每秒归一化变化；此处换算成"每帧 DMX 值"。
_PUMP_MAX_RATE = 5          # 普通泵/高喷（dmx_semantics: pump=5）
_PUMP_RATE_FAST = 3         # 变频泵（variable_pump=3，更慢更稳）
_PUMP_CH = ("pump",)
_MOTOR_CH = ("motor_x", "motor_y", "motor_z")
MOTOR_MAX_RATE = 8          # 电机（dmx_semantics: motor_x/y=8）

PRECHARGE_LEVEL = 13        # VFD 就绪频率（同主系统 pump_precharge 默认）
PRECHARGE_LEAD_S = 1.0      # 预充提前量（秒）


def _slew_step(max_change_rate: int, fps: float) -> float:
    """每帧最大变化量（DMX 0..255 量纲）。

    模块侧语义（mlstage/dmx_semantics.py 注释）：`max_change_rate` 是
    **每帧**最大变化量，且以主系统 40fps 采样率为准。
    ⇒ 每秒最大变化 = rate × 40；集成播放 fps 下的每帧步长 = rate × 40 / fps。

    ★ 下限 1.0 是必须的：步长若 <1 且下游用 int() 截断，通道会永远停在原值
      （实测 bug：rate=5 时旧式算出 0.17 → 泵整段不动）。
    """
    return max(1.0, float(max_change_rate) * 40.0 / max(fps, 1e-6))


@dataclass
class LabPlayback:
    """一段待播放的实验室编排。"""
    field: object                          # np.ndarray [T, 256*ch] 0..1
    spec: object                           # LayoutSpec
    res: int = 16
    ch: int = 4
    fps: float = DEFAULT_FPS
    white: float = 0.0
    audio_path: str = ""
    precharge_points: list[float] = field(default_factory=list)  # 秒（clip 起点）
    duration: float = 0.0

    @property
    def frames(self) -> int:
        try:
            return int(self.field.shape[0])
        except Exception:
            return 0


class LabOutput(QObject):
    """播放 + 逐帧写层。窗口持有，闭窗必须 stop()。"""

    frame_changed = pyqtSignal(int, int)      # (当前帧, 总帧数)
    position_changed = pyqtSignal(float)      # 秒
    state_changed = pyqtSignal(bool)          # 播放中?

    def __init__(self, network_manager=None, parent=None):
        super().__init__(parent)
        self._nm = network_manager
        self._play: Optional[LabPlayback] = None
        self._player_impl = None              # FieldPlayer（采样+映射缓存）
        self._audio: Optional[QMediaPlayer] = None
        self._audio_out: Optional[QAudioOutput] = None
        self._timer = QTimer(self)
        self._timer.setInterval(33)           # ~30fps
        self._timer.timeout.connect(self._tick)
        self._last_written: dict[int, dict[int, int]] = {}   # 逐通道 slew 状态（universe→addr→值）
        self._fired_precharge: set[int] = set()
        self._frame = 0
        self._muted = False                   # 仅预览不出真实设备

    # ── 生命周期 ──────────────────────────────────────────────────
    def set_network_manager(self, nm) -> None:
        self._nm = nm

    @property
    def fps(self) -> float:
        return self._play.fps if self._play else DEFAULT_FPS

    @property
    def playing(self) -> bool:
        return self._timer.isActive()

    @property
    def frame(self) -> int:
        return self._frame

    @property
    def total_frames(self) -> int:
        return self._play.frames if self._play else 0

    @property
    def duration(self) -> float:
        if not self._play:
            return 0.0
        if self._play.duration > 0:
            return self._play.duration
        return self._play.frames / max(self._play.fps, 1e-6)

    def set_muted(self, muted: bool) -> None:
        """静音 = 只驱动 3D 预览，不写真实输出层。"""
        self._muted = bool(muted)
        if self._muted:
            self._clear_layer()

    # ── 装载 ──────────────────────────────────────────────────────
    def load(self, play: LabPlayback) -> None:
        self.stop()
        from .field_mapper import FieldPlayer
        self._play = play
        self._player_impl = FieldPlayer(play.spec, play.field, play.res, play.ch)
        self._frame = 0
        self._last_written = {}
        self._fired_precharge = set()
        if play.audio_path:
            self._ensure_audio(play.audio_path)

    def _ensure_audio(self, path: str) -> None:
        if self._audio is None:
            self._audio = QMediaPlayer(self)
            self._audio_out = QAudioOutput(self)
            self._audio_out.setVolume(1.0)
            self._audio.setAudioOutput(self._audio_out)
        self._audio.setSource(QUrl.fromLocalFile(path))
        try:
            self._audio.setPosition(0)
        except Exception:
            pass

    # ── 传输 ──────────────────────────────────────────────────────
    def play(self, from_frame: int | None = None) -> None:
        if self._play is None:
            return
        if from_frame is not None:
            self._frame = max(0, min(int(from_frame), max(0, self.total_frames - 1)))
            if self._audio is not None:
                try:
                    self._audio.setPosition(int(self._frame / self.fps * 1000))
                except Exception:
                    pass
        if self._audio is not None:
            self._audio.play()
        self._timer.start()
        self.state_changed.emit(True)

    def pause(self) -> None:
        self._timer.stop()
        if self._audio is not None:
            self._audio.pause()
        self.state_changed.emit(False)

    def stop(self, clear: bool = True) -> None:
        self._timer.stop()
        if self._audio is not None:
            try:
                self._audio.stop()
                self._audio.setPosition(0)
            except Exception:
                pass
        if clear:
            self._clear_layer()
        self._frame = 0
        self._fired_precharge = set()
        self.state_changed.emit(False)

    def seek(self, seconds: float) -> None:
        if self._play is None:
            return
        f = int(max(0.0, seconds) * self.fps)
        self._frame = max(0, min(f, max(0, self.total_frames - 1)))
        if self._audio is not None:
            try:
                self._audio.setPosition(int(max(0.0, seconds) * 1000))
            except Exception:
                pass
        self._tick()

    # ── 每帧 ──────────────────────────────────────────────────────
    def _tick(self) -> None:
        if self._play is None or self._player_impl is None:
            return
        total = self.total_frames
        if total <= 0:
            return
        # 帧号：优先跟音频真实位置（音画同步）；无音频用内部计数
        if self._audio is not None and self._audio.mediaStatus() not in (
                QMediaPlayer.MediaStatus.NoMedia,):
            pos_ms = self._audio.position()
            if pos_ms > 0:
                self._frame = min(total - 1, int(pos_ms / 1000.0 * self.fps))
            else:
                self._frame = min(total - 1, self._frame + 1)
        else:
            self._frame = min(total - 1, self._frame + 1)

        # 音频播完 → 停止（不循环，避免无意持续输出）
        if self._audio is not None:
            st = self._audio.mediaStatus()
            if st == QMediaPlayer.MediaStatus.EndOfMedia:
                self.stop()
                return

        self._maybe_precharge(self._frame)
        mf = self._player_impl.frame(self._frame, white=self._play.white)
        if not self._muted and self._nm is not None:
            self._write_layer(mf.sparse)
        self.frame_changed.emit(self._frame, total)
        self.position_changed.emit(self._frame / self.fps)

        if self._frame >= total - 1:
            # 末帧保持一小段时间再停，避免尾巴被截
            self._hold_end = getattr(self, "_hold_end", 0) + 1
            if self._hold_end > int(self.fps * 0.4):
                self._hold_end = 0
                self.stop()

    def _maybe_precharge(self, frame: int) -> None:
        """泵预充：clip 起点前 PRECHARGE_LEAD_S 秒，向该设备泵通道发预充值一次。"""
        if not self._play.precharge_points or self._nm is None:
            return
        t = frame / max(self.fps, 1e-6)
        for i, t0 in enumerate(self._play.precharge_points):
            if i in self._fired_precharge:
                continue
            if t0 - PRECHARGE_LEAD_S <= t < t0:
                self._fired_precharge.add(i)
                self._send_precharge()

    def _send_precharge(self) -> None:
        sparse: dict[int, dict[int, int]] = {}
        for f in self._play.spec.fixtures:
            for off, name in enumerate(getattr(f, "channels", []) or []):
                if str(name).strip().lower() in _PUMP_CH:
                    addr = int(f.start_channel) - 1 + off
                    if 0 <= addr < 512:
                        sparse.setdefault(int(f.universe), {})[addr] = PRECHARGE_LEVEL
        if sparse:
            self._write_layer(sparse, slew=False)   # 预充不参与限速（要的就是立刻到位）

    # ── 写层 ──────────────────────────────────────────────────────
    def _write_layer(self, sparse: dict, slew: bool = True) -> None:
        if self._nm is None or not sparse:
            return
        out: dict[int, dict[int, int]] = {}
        for uni, chans in sparse.items():
            prev = self._last_written.get(uni) or {}
            slot: dict[int, int] = {}
            for addr, val in chans.items():
                v = int(max(0, min(255, val)))
                if slew:
                    lim = self._limit_for(uni, addr)
                    if lim is not None:
                        old = prev.get(addr)
                        if old is not None:
                            step = _slew_step(lim, self.fps)
                            if v > old:
                                v = min(v, int(old + step))
                            else:
                                v = max(v, int(old - step))
                slot[addr] = v
            self._last_written[uni] = slot
            out[uni] = slot
        try:
            self._nm.update_layer_sparse_batch(LAYER_NAME, out, touch_ltp=True)
        except Exception:
            # 回退：逐域写（老版本无 batch API 时）
            try:
                for uni, slot in out.items():
                    self._nm.update_layer_sparse(LAYER_NAME, uni, slot, touch_ltp=True)
            except Exception:
                pass

    def _limit_for(self, universe: int, addr: int) -> Optional[int]:
        """该地址对应的设备通道是否限速；返回 max_change_rate 或 None（灯具直通）。"""
        spec = self._play.spec if self._play else None
        if spec is None:
            return None
        for f in spec.fixtures:
            if int(f.universe) != int(universe):
                continue
            base = int(f.start_channel) - 1
            off = addr - base
            chs = getattr(f, "channels", []) or []
            if 0 <= off < len(chs):
                n = str(chs[off]).strip().lower()
                if n in _PUMP_CH:
                    # 变频泵（只有 pump 一个通道）用更保守的速率
                    return _PUMP_RATE_FAST if len(chs) == 1 else _PUMP_MAX_RATE
                if n in _MOTOR_CH:
                    return MOTOR_MAX_RATE
                return None
        return None

    def _clear_layer(self) -> None:
        if self._nm is None:
            return
        try:
            self._nm.clear_layer(LAYER_NAME)
        except Exception:
            pass
        self._last_written = {}
