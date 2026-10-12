"""
audio_driver.py — DMXTimeline 通用音频分析模块 (v2)
================================================
提供实时音频分析能力，驱动 Programmer 中的所有控制器（推杆/滑块/摇杆）。

架构：
  AudioDriver（单例）
    ├─ AudioAnalyzer（后台线程，实时分析麦克风/文件输入）
    │    ├─ 节拍检测（BPM + 节拍相位）
    │    ├─ 频谱分析（8频段能量）
    │    └─ 整体能量（RMS）
    └─ AudioDriverWidget（UI 控件，嵌入 Programmer 通道行）
         ├─ 波形/能量实时显示
         ├─ BPM 显示 + 手动 Tap Tempo
         ├─ 频段映射配置（哪个频段驱动哪个通道）
         └─ 灵敏度/增益调节

★ v2 改变：
  AudioDriverWidget 不再直接写入 NetworkManager。
  改为发出 dmx_data_changed(np.ndarray) 信号，
  由 ProgrammerPanel 统一收集后混入 dmx_out。

信号：
  AudioDriver.frame_ready(AudioFrame)     — 每帧发出，供 UI 刷新
  AudioDriver.unified_value(float)        — 统一归一化输出 0..1
  AudioDriverWidget.dmx_data_changed(ndarray)  — ★ 新增：完整 DMX 数据（512 uint8）
"""
from __future__ import annotations

import math
import sys
import time
import threading
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any

logger = logging.getLogger(__name__)


def _park_audio_zombie(an, thread) -> None:
    """★ R193/P2-20（AUD-04）：join 超时不丢线程引用——挂僵尸由 start 排空
    （模块级：stop 行数/CC 基线）。引用被清会让 alive 守卫失效并复活成双循环。"""
    if thread is not None and thread.is_alive():
        an._zombie_thread = thread

import numpy as np
from src.audio.audio_spectrum import FREQ_RANGES, hann_window, normalized_bands
from src.audio.audio_onset_runtime import (
    OnsetRuntime, analyze_onset, onset_source_snapshot,
    set_onset_gain, set_onset_mode, set_onset_position,
)
from PyQt6.QtCore import QObject, pyqtSignal, QTimer, Qt
from PyQt6.QtGui import QPainter, QColor, QPen, QFont, QBrush
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QSlider, QDoubleSpinBox, QComboBox,
    QGroupBox, QSizePolicy, QFrame,
)

# ─────────────────────────────────────────────────────────────────────────────
#  数据结构
# ─────────────────────────────────────────────────────────────────────────────

class AudioMappingMode:
    """音频映射模式常量"""
    ENERGY  = "energy"       # 整体能量（RMS）驱动
    BEAT    = "beat"         # 节拍驱动（每拍触发一次）
    BAND    = "band"         # 指定频段能量驱动
    BPM     = "bpm"          # BPM 同步驱动
    PEAK    = "peak"         # 峰值能量驱动
    ENVELOPE    = "envelope"     # 包络跟随
    GATE        = "gate"         # 门限触发
    DUCKING     = "ducking"      # 闪避
    SIDECHAIN   = "sidechain"    # 侧链压缩
    MULTI_BAND  = "multi_band"   # 多频段混合
    BEAT_STEP   = "beat_step"    # 节拍步进
    LFO_SYNC    = "lfo_sync"     # LFO同步BPM
    ONSET       = "onset"        # 音头检测

MAPPING_MODE_LABELS = {
    "energy": "整体能量", "beat": "节拍衰减", "band": "频段能量",
    "bpm": "BPM同步", "peak": "峰值能量", "envelope": "包络跟随",
    "gate": "门限触发", "ducking": "闪避(反向)", "sidechain": "侧链压缩",
    "multi_band": "多频段混合", "beat_step": "节拍步进",
    "lfo_sync": "LFO同步BPM", "onset": "音头检测",
}


FREQ_BANDS = ["Sub", "Bass", "LowMid", "Mid", "HighMid", "Presence", "Brilliance", "Air"]

@dataclass
class AudioFrame:
    """一帧音频分析结果"""
    t: float = 0.0                          # 时间戳（秒）
    rms: float = 0.0                        # 整体能量 (0-1)
    peak: float = 0.0                       # 峰值 (0-1)
    bands: List[float] = field(default_factory=lambda: [0.0] * 8)  # 8频段能量 (0-1)
    bpm: float = 120.0                      # 估算 BPM
    beat_phase: float = 0.0                 # 节拍相位 (0-1)
    is_beat: bool = False                   # 是否为节拍瞬间
    onset_strength: float = 0.0            # 起音强度 (0-1)
    has_audio: bool = False                 # ★ 是否有真实音频输入（False=无音频/静音）
    # ── 真实音高/和声（C5，opt-in；默认未检测）──
    pitch_midi: float = -1.0                # 真实基频(MIDI 音高)，-1=无音高/未检测
    pitch_confidence: float = 0.0           # 音高置信度 0-1
    chord_root: int = -1                    # 和弦根音 0-11(C..B)，-1=未知
    chord_quality: str = ""                 # "major"/"minor"/""


# ─────────────────────────────────────────────────────────────────────────────
#  BPM 检测器（轻量级，基于能量峰值间隔）
# ─────────────────────────────────────────────────────────────────────────────

def _mode_bpm(values: List[float]) -> float:
    """BPM 候选的平滑众数：1-BPM 桶直方图，±1 桶平滑后取峰（无候选回 120）。"""
    if not values:
        return 120.0
    lo, hi = 60, 200
    bins = [0.0] * (hi - lo + 1)
    for v in values:
        i = int(round(v)) - lo
        if 0 <= i < len(bins):
            bins[i] += 1.0
    smooth = [bins[max(0, i - 1)] + bins[i] + bins[min(len(bins) - 1, i + 1)]
              for i in range(len(bins))]
    peak = max(range(len(smooth)), key=lambda i: smooth[i])
    if smooth[peak] <= 0:
        return 120.0
    # 峰桶内做加权平均细化（避免 1-BPM 量化台阶）
    num = den = 0.0
    for k in (peak - 1, peak, peak + 1):
        if 0 <= k < len(bins) and bins[k] > 0:
            w = bins[k]
            num += (lo + k) * w
            den += w
    return num / den if den > 0 else float(lo + peak)


def _fold_to_range(bpm: float) -> float:
    """倍频折叠到 [70,180]（八度歧义消解）。"""
    while bpm < 70.0:
        bpm *= 2.0
    while bpm > 180.0:
        bpm *= 0.5
    return bpm


def _hist_mode_bpm(values: List[float]) -> float:
    """1-BPM 桶直方图 ±1 平滑取峰 + 峰桶加权细化（无候选回 120）。"""
    lo = 60
    bins = [0.0] * 141
    for v in values:
        i = int(round(v)) - lo
        if 0 <= i < len(bins):
            bins[i] += 1.0
    smooth = [bins[max(0, i - 1)] + bins[i] + bins[min(len(bins) - 1, i + 1)]
              for i in range(len(bins))]
    peak = max(range(len(smooth)), key=lambda i: smooth[i])
    if smooth[peak] <= 0:
        return 120.0
    num = den = 0.0
    for k in (peak - 1, peak, peak + 1):
        if 0 <= k < len(bins) and bins[k] > 0:
            num += (lo + k) * bins[k]
            den += bins[k]
    return num / den if den > 0 else float(lo + peak)


def _estimate_bpm(intervals: List[float]) -> float:
    """间隔序列 → BPM：逐个折叠到 [70,180] 后取直方图平滑众数（R178 修：
    旧均值+八度对齐路径依赖；细分拍混入折叠后聚到同簇，众数即真 BPM）。"""
    folded = [_fold_to_range(60.0 / iv) for iv in intervals if iv > 0]
    return _hist_mode_bpm(folded) if folded else 120.0


class BPMDetector:
    """
    轻量级 BPM 检测器。
    通过跟踪能量峰值（onset）的时间间隔来估算 BPM。
    """
    def __init__(self, history_len: int = 32):
        self._beat_times: List[float] = []
        self._history_len = history_len
        self._last_onset = 0.0
        self._min_interval = 0.25   # 最小节拍间隔（对应 240 BPM）
        self._bpm: float = 120.0
        self._phase: float = 0.0
        self._last_beat_t: float = 0.0
        self._octave_fold: bool = True   # BPM 倍频/半频折叠到 [70,180]，消八度歧义

    def set_octave_fold(self, enabled: bool):
        self._octave_fold = bool(enabled)

    def _fold_bpm(self, bpm: float) -> float:
        """把 BPM 折叠到 [70,180] 消除倍频/半频歧义，并与上次值做最近八度对齐防抖。"""
        if not self._octave_fold or bpm <= 0:
            return bpm
        while bpm < 70.0:
            bpm *= 2.0
        while bpm > 180.0:
            bpm /= 2.0
        prev = self._bpm
        if prev > 0:
            best = bpm
            for cand in (bpm * 0.5, bpm, bpm * 2.0):
                if 50.0 <= cand <= 250.0 and abs(cand - prev) < abs(best - prev):
                    best = cand
            bpm = best
        return bpm

    def update(self, t: float, onset_strength: float, threshold: float = 0.3) -> bool:
        """
        更新节拍检测。
        返回 True 表示检测到节拍。
        """
        is_beat = False
        if onset_strength > threshold and (t - self._last_onset) > self._min_interval:
            self._last_onset = t
            self._beat_times.append(t)
            if len(self._beat_times) > self._history_len:
                self._beat_times.pop(0)
            if len(self._beat_times) >= 4:
                intervals = [self._beat_times[i+1] - self._beat_times[i]
                             for i in range(len(self._beat_times)-1)]
                self._bpm = max(40.0, min(300.0,
                                self._fold_bpm(_estimate_bpm(intervals))))
            self._last_beat_t = t
            is_beat = True

        # 计算节拍相位
        beat_period = 60.0 / max(1.0, self._bpm)
        self._phase = ((t - self._last_beat_t) % beat_period) / beat_period
        return is_beat

    @property
    def bpm(self) -> float:
        return self._bpm

    @property
    def beat_phase(self) -> float:
        return self._phase

    def tap(self, t: float):
        """手动 Tap Tempo"""
        self._beat_times.append(t)
        if len(self._beat_times) > 8:
            self._beat_times.pop(0)
        if len(self._beat_times) >= 2:
            intervals = [self._beat_times[i+1] - self._beat_times[i]
                         for i in range(len(self._beat_times)-1)]
            avg = sum(intervals) / len(intervals)
            # 手动 Tap 是字面意图，不做八度折叠
            self._bpm = max(40.0, min(300.0, 60.0 / avg))
        self._last_beat_t = t
        self._phase = 0.0


# ─────────────────────────────────────────────────────────────────────────────
#  音频分析器（后台线程）
# ─────────────────────────────────────────────────────────────────────────────

class AudioAnalyzer(QObject):
    """
    后台音频分析线程。
    支持三种输入模式：
      1. 实时麦克风输入（需要 sounddevice）
      2. 模拟模式（基于 BPM 生成模拟音频帧，无需硬件）
      3. 文件模式（加载音频文件，根据时间线播放位置实时分析）
    """
    frame_ready     = pyqtSignal(object)   # AudioFrame
    energy_updated  = pyqtSignal(float)    # rms (0-1)
    beat_detected   = pyqtSignal(float)    # bpm（设计预留, 无消费方）
    # ★ 统一归一化输出（0-1）。任何想接入"信号合成"的模块都订阅此信号；
    #   不再按通道映射不同的 0-255 值，确保所有订阅者拿到完全一致的形状基准。
    unified_value   = pyqtSignal(float)    # 统一归一化输出 0..1（设计预留, 无消费方）
    file_loaded     = pyqtSignal(str)      # 音频文件整曲读盘完成 (path)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._bpm_detector = BPMDetector()
        self._onset_runtime = OnsetRuntime()
        self._mode = "simulate"   # "simulate" | "microphone" | "file"
        self._simulate_bpm = 120.0
        self._gain = 1.0
        self._sensitivity = 0.3
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer)
        self._stop_event = threading.Event()
        self._start_t = time.time()
        self._mic_buffer: Optional[np.ndarray] = None
        self._mic_lock = threading.Lock()
        self._mic_stream = None
        self._sr = 44100
        self._chunk = 1024
        # 文件模式相关
        self._file_data: Optional[np.ndarray] = None  # 单声道音频数据
        self._file_sr: int = 44100                     # 文件采样率
        self._file_duration: float = 0.0               # 文件时长（秒）
        self._file_path: str = ""
        self._file_loading: bool = False               # 后台整曲读盘进行中
        self._load_token: int = 0                      # 失效旧的后台加载线程
        self._timeline_pos: float = 0.0                # 当前时间线播放位置（秒）
        self._timeline_playing: bool = False           # 时间线是否在播放
        self._last_frame: AudioFrame = AudioFrame()    # 最近一次分析结果，供效果引擎同步读取
        # ★ 统一输出参数（共享给所有订阅者）
        self._unified_source: str = "rms"  # "rms" | "peak" | "beat" | "band:<idx>"
        self._unified_band_idx: int = 1    # 默认 Bass 频段
        # ── C5 真实音高/和弦离线分析缓存（opt-in，默认关）──
        self._pitch_enabled: bool = False
        self._pitch_ready: bool = False
        self._pitch_times: Optional[np.ndarray] = None      # 帧时间(秒)
        self._pitch_midi_arr: Optional[np.ndarray] = None   # MIDI 音高(NaN=无声)
        self._pitch_conf_arr: Optional[np.ndarray] = None   # 置信度 0-1
        self._chord_root_arr: Optional[np.ndarray] = None   # 0-11
        self._chord_qual_arr: Optional[list] = None         # "major"/"minor"
        self._pitch_thread: Optional[threading.Thread] = None
        self._pitch_token: int = 0                          # 失效旧分析线程
        self._pitch_rerun_pending: bool = False             # 分析中被重触发→收尾后再跑一次
        # 文件整曲读盘完成 → 主线程收尾（如按需启动音高分析）
        self.file_loaded.connect(self._on_file_loaded)

    def set_pitch_detection(self, enabled: bool):
        """开关真实音高检测(C5)。开启且已载文件时后台分析整曲基频+和弦。"""
        enabled = bool(enabled)
        self._pitch_enabled = enabled
        if enabled and self._file_data is not None and not self._pitch_ready:
            self._start_pitch_analysis()
        elif not enabled:
            self._pitch_ready = False
            # ★ 保险1：递增 token 使在途 worker 完成时 token 校验失败、不再写回结果，
            #   并清空缓存数组，避免“关了又被后台自己开回去”。
            self._pitch_token += 1
            self._pitch_times = None
            self._pitch_midi_arr = None
            self._pitch_conf_arr = None
            self._chord_root_arr = None
            self._chord_qual_arr = None

    def is_pitch_enabled(self) -> bool:
        return self._pitch_enabled

    def pitch_status(self) -> str:
        """返回 'off' / 'no-file' / 'analyzing' / 'ready'，供 UI 显示。"""
        if not self._pitch_enabled:
            return "off"
        if self._pitch_ready:
            return "ready"
        if self._file_data is None:
            return "no-file"
        return "analyzing"

    def _start_pitch_analysis(self):
        """后台线程离线计算整曲音高/和弦，避免阻塞 UI。

        ★ 并发守卫：librosa.pyin 全曲分析是重 CPU、持 GIL 的操作，且不可取消。
        过去本方法每次被调用（多个消费者各自 load_audio_file + 托管开 pitch）都
        无条件 spawn 一个 _work 线程，旧线程不会停（token 只废弃结果），导致最多
        堆叠 7 个 _work 线程并发跑 pyin → GIL 饱和 → 主线程混音器(QTimer)饿死 →
        整个 UI/DMX 冻结。现在严格限制同时只有 1 个分析线程在跑；若分析期间又被
        触发（换文件等），只置 _pitch_rerun_pending，由收尾的线程再起一次。
        """
        self._pitch_ready = False
        self._pitch_token += 1
        token = self._pitch_token
        data = self._file_data
        sr = int(self._file_sr)
        if data is None or len(data) < sr // 2:
            return

        # 已有分析线程在跑 → 不再 spawn，标记待重跑即可（避免线程堆叠饱和 GIL）。
        existing = getattr(self, "_pitch_thread", None)
        if existing is not None and existing.is_alive():
            self._pitch_rerun_pending = True
            return

        def _work(buf, sr, token):
            try:
                import librosa
                # 降采样到 22050 足够覆盖 fmax(C7≈2093Hz)，pyin 提速一倍
                target_sr = 22050
                y = librosa.resample(buf.astype(np.float32), orig_sr=sr, target_sr=target_sr) \
                    if sr != target_sr else buf.astype(np.float32)
                hop = 512
                fmin = librosa.note_to_hz('C2')
                fmax = librosa.note_to_hz('C7')
                f0, voiced_flag, voiced_prob = librosa.pyin(
                    y, fmin=fmin, fmax=fmax, sr=target_sr,
                    frame_length=2048, hop_length=hop)
                times = librosa.times_like(f0, sr=target_sr, hop_length=hop)
                midi = librosa.hz_to_midi(np.where(voiced_flag, f0, np.nan))
                conf = np.nan_to_num(voiced_prob, nan=0.0)
                # 和弦：chroma_cqt 取根音 + 大/小三度能量比定性质
                chroma = librosa.feature.chroma_cqt(y=y, sr=target_sr, hop_length=hop)
                roots = np.argmax(chroma, axis=0).astype(np.int16)
                quals = []
                for fi in range(chroma.shape[1]):
                    r = int(roots[fi])
                    maj3 = chroma[(r + 4) % 12, fi]
                    min3 = chroma[(r + 3) % 12, fi]
                    quals.append("major" if maj3 >= min3 else "minor")
                if token != self._pitch_token:
                    return  # 已被新文件/新分析取代
                # chroma 与 pyin 帧数可能差 1，按较短对齐
                n = min(len(times), len(midi), len(roots))
                self._pitch_times = np.asarray(times[:n])
                self._pitch_midi_arr = np.asarray(midi[:n])
                self._pitch_conf_arr = np.asarray(conf[:n])
                self._chord_root_arr = np.asarray(roots[:n])
                self._chord_qual_arr = quals[:n]
                self._pitch_ready = True
            except ImportError:
                print("[AudioAnalyzer] 音高检测需要 librosa")
            except Exception as e:
                print(f"[AudioAnalyzer] 音高分析失败: {e}")
            finally:
                # 收尾：若分析期间被重新触发过，且仍开启检测，则再起一次（串行，不堆叠）。
                self._pitch_thread = None
                if getattr(self, "_pitch_rerun_pending", False) and self._pitch_enabled:
                    self._pitch_rerun_pending = False
                    self._start_pitch_analysis()

        self._pitch_thread = threading.Thread(
            target=_work, args=(data, sr, token), daemon=True)
        self._pitch_thread.start()

    def _lookup_pitch(self, pos: float):
        """按播放位置查表返回 (pitch_midi, confidence, chord_root, chord_quality)。"""
        # ★ 保险2：运行时硬闸——关闭后即使 ready/缓存有残留也绝不返回音高。
        if not self._pitch_enabled:
            return (-1.0, 0.0, -1, "")
        if not (self._pitch_ready and self._pitch_times is not None and len(self._pitch_times)):
            return (-1.0, 0.0, -1, "")
        idx = int(np.searchsorted(self._pitch_times, pos))
        idx = max(0, min(idx, len(self._pitch_times) - 1))
        m = self._pitch_midi_arr[idx]
        if not np.isfinite(m):
            return (-1.0, float(self._pitch_conf_arr[idx]), int(self._chord_root_arr[idx]),
                    self._chord_qual_arr[idx])
        return (float(m), float(self._pitch_conf_arr[idx]),
                int(self._chord_root_arr[idx]), self._chord_qual_arr[idx])

    def set_mode(self, mode: str):
        """切换模式：simulate / microphone / file
        注意：切换不会清除已加载的音频文件数据（_file_data），
        切回 file 模式后可继续从时间线位置识别。"""
        was_running = self._running
        if was_running:
            self.stop()
        set_onset_mode(self, mode)
        if was_running:
            self.start()

    def set_mic_device(self, device_index: Optional[int]):
        """设置麦克风设备索引（None 表示使用系统默认设备）。
        如果当前正在使用麦克风模式，会重启麦克风流以应用新设备。"""
        self._mic_device = device_index
        if self._running and self._mode == "microphone":
            if self._mic_stream is not None:
                try:
                    self._mic_stream.stop()
                    self._mic_stream.close()
                except Exception:
                    logger.debug("停止并关闭麦克风流时发生异常")
                    pass
                self._mic_stream = None
            self._start_microphone()

    def get_mic_device(self) -> Optional[int]:
        """获取当前麦克风设备索引（None 表示系统默认）。"""
        return getattr(self, '_mic_device', None)

    @staticmethod
    def list_mic_devices() -> list:
        """枚举所有可用的输入设备，返回 [(index, name), ...] 列表。"""
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            result = []
            for i, d in enumerate(devices):
                if d.get('max_input_channels', 0) > 0:
                    result.append((i, d['name']))
            return result
        except Exception:
            logger.debug("列举音频输入设备时失败")
            return []

    def load_audio_file(self, path: str):
        """加载音频文件用于文件模式分析。

        使用流式分块读取以支持长音频文件（>30分钟）。

        ★ 主线程防卡顿：先快速读元数据（采样率/时长）立即返回，整曲采样读盘
        放后台线程完成，通过 file_loaded 信号回主线程收尾；调用方若本身在后台
        线程（如离线编曲烘焙 worker），则同步读盘以获得立即可用的数据。

        ★ 幂等守卫：同一文件已加载或正在后台加载时直接早退——不重读、不重分析。
        """
        try:
            import os as _os
            abspath = _os.path.abspath(_os.fspath(path)) if path else ""
            cur_abspath = (_os.path.abspath(_os.fspath(self._file_path))
                           if self._file_path else "")
        except Exception:
            logger.debug("校验同一文件路径时发生异常（非严重）")
            abspath = ""
            cur_abspath = ""
        if abspath and abspath == cur_abspath:
            if self._file_data is not None:
                # 已加载同文件：仅保证 file 模式，不重读不重分析。
                if self._mode != "file":
                    self.set_mode("file")
                return
            if self._file_loading:
                # 正在后台加载同文件：避免重复 spawn。
                return

        meta = self._read_file_meta(path)
        if meta is None:
            print("[AudioAnalyzer] 无法读取音频文件元数据")
            return
        sr, duration = meta

        # 先落元数据，保证 UI 立即看到时长/文件已选中；整曲数据随后后台补齐。
        self._file_sr = sr
        self._file_duration = duration
        self._file_path = path
        self._file_data = None
        self._file_loading = True
        self._pitch_ready = False
        self.set_mode("file")

        # 整曲采样读盘放后台线程；完成信号 file_loaded 回主线程收尾。
        # 非主线程调用方（如离线编曲烘焙 worker）需要同步拿到 _file_data，直接同步读。
        if threading.current_thread() is threading.main_thread():
            self._load_token += 1
            token = self._load_token
            t = threading.Thread(target=self._load_file_worker, args=(path, token), daemon=True)
            t.start()
        else:
            self._apply_file_result(path, self._read_file_data(path))

    def _read_file_meta(self, path: str):
        """快速读取音频元数据（采样率、时长），不读整曲采样。失败返回 None。"""
        try:
            import soundfile as sf
            info = sf.info(path)
            sr = int(info.samplerate)
            duration = float(info.frames) / max(1, sr)
            return (sr, duration)
        except Exception:
            pass
        try:
            import librosa
            duration = float(librosa.get_duration(path=path))
            # 采样率无法廉价获取：先用默认值，后台整曲读取时会校正。
            return (44100, duration)
        except Exception:
            pass
        return None

    def _read_file_data(self, path: str):
        """整曲读取音频数据（后台线程调用）。成功返回 (mono_data, sr, duration)，失败返回 None。"""
        try:
            import soundfile as sf
        except ImportError:
            sf = None
        if sf is not None:
            try:
                info = sf.info(path)
                sr = info.samplerate
                total_frames = info.frames
                channels = info.channels
                mono_data = np.empty(total_frames, dtype=np.float32)
                block_size = 1024 * 1024  # 每次读取 ~1M 采样点
                offset = 0
                with sf.SoundFile(path, 'r') as f:
                    while offset < total_frames:
                        n_read = min(block_size, total_frames - offset)
                        chunk = f.read(n_read, dtype='float32', always_2d=True)
                        if chunk.shape[0] == 0:
                            break
                        if channels > 1:
                            mono_data[offset:offset + chunk.shape[0]] = chunk.mean(axis=1)
                        else:
                            mono_data[offset:offset + chunk.shape[0]] = chunk[:, 0]
                        offset += chunk.shape[0]
                return (mono_data[:offset], int(sr), float(offset / max(1, sr)))
            except Exception as e:
                print(f"[AudioAnalyzer] 加载音频文件失败: {e}")
                return None
        try:
            import librosa
            y, sr = librosa.load(path, sr=None, mono=True)
            return (y, int(sr), float(len(y) / max(1, sr)))
        except ImportError:
            print("[AudioAnalyzer] 需要 soundfile 或 librosa 来加载音频文件")
            return None
        except Exception as e:
            print(f"[AudioAnalyzer] 加载音频文件失败: {e}")
            return None

    def _load_file_worker(self, path: str, token: int):
        """后台线程：整曲读盘并落地结果。旧线程完成时按 token 失效。"""
        result = self._read_file_data(path)
        if token != self._load_token:
            return  # 已被更新的加载请求取代
        self._apply_file_result(path, result)

    def _apply_file_result(self, path: str, result):
        """落地后台读盘结果并发完成信号（信号跨线程 → 主线程收尾）。"""
        self._file_loading = False
        if result is None:
            return
        data, sr, duration = result
        self._file_sr = sr
        self._file_duration = duration
        self._file_data = data
        self.file_loaded.emit(path)

    def _on_file_loaded(self, path: str):
        """文件加载完成（回主线程收尾）：按需启动音高后台分析。"""
        self._pitch_ready = False
        if self._pitch_enabled and self._file_data is not None:
            self._start_pitch_analysis()

    def set_timeline_position(self, pos: float):
        """接收时间线播放位置（秒），供文件模式使用"""
        set_onset_position(self, pos)

    def set_timeline_playing(self, playing: bool):
        """设置时间线播放状态"""
        self._timeline_playing = playing
        if playing and self._mode == "file":
            # 文件模式下时间线开始播放时启动分析
            if not self._running:
                self.start()

    def set_simulate_bpm(self, bpm: float):
        self._simulate_bpm = max(40.0, min(300.0, bpm))

    def set_gain(self, gain: float):
        set_onset_gain(self, gain)

    def set_sensitivity(self, sensitivity: float):
        self._sensitivity = max(0.05, min(1.0, sensitivity))

    def tap_tempo(self):
        self._bpm_detector.tap(time.time() - self._start_t)

    def start_simulation(self):
        """显式开启音频模拟输出（用于驱动数值）"""
        self._simulation_enabled = True

    def stop_simulation(self):
        """显式关闭音频模拟输出（防止数值乱跳）"""
        self._simulation_enabled = False

    def start(self):
        if self._running:
            return
        # ★ R193/P2-20（AUD-04）：排空停止超时的僵尸分析线程——它的退出条件
        #   `_running`/`_stop_event` 马上要在下面复位，不排空会复活成双 40Hz 循环。
        # ★ R194/NB-04：仅在确认死亡后才清引用（R193 版先清后查，守卫单次失效）。
        _zombie = getattr(self, "_zombie_thread", None)
        if _zombie is not None:
            if _zombie.is_alive():
                _zombie.join(timeout=0.5)
            if _zombie.is_alive():
                logger.warning("上一音频分析线程停止超时仍存活，拒绝重启")
                return
            self._zombie_thread = None
        self._running = True
        self._stop_event.clear()
        self._start_t = time.time()
        if self._mode == "microphone":
            self._start_microphone()
        self._thread = threading.Thread(target=self._run_analysis_loop, name="AudioAnalyzer", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._stop_event.set()
        try:
            self._timer.stop()
        except Exception:
            logger.debug("停止分析器定时器时发生异常")
            pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            try:
                thread.join(timeout=0.2)
            except Exception:
                logger.debug("等待分析线程退出时发生异常")
                pass
        # ★ R193/P2-20：join 超时不清引用防复活——挂僵尸引用由 start 排空
        _park_audio_zombie(self, thread)
        self._thread = None
        if self._mic_stream is not None:
            try:
                self._mic_stream.stop()
                self._mic_stream.close()
            except Exception:
                logger.debug("停止麦克风输入流时发生异常")
                pass
            self._mic_stream = None

    def _start_microphone(self):
        """尝试启动麦克风输入（支持指定设备索引）"""
        try:
            import sounddevice as sd
            device = getattr(self, '_mic_device', None)
            def callback(indata, frames, time_info, status):
                with self._mic_lock:
                    self._mic_buffer = indata[:, 0].copy()
            kwargs = dict(
                samplerate=self._sr,
                channels=1,
                blocksize=self._chunk,
                callback=callback,
            )
            if device is not None:
                kwargs['device'] = device
            self._mic_stream = sd.InputStream(**kwargs)
            self._mic_stream.start()
        except Exception as e:
            print(f"[AudioAnalyzer] 麦克风启动失败: {e}，回退到模拟模式")
            # 麦克风不可用，回退到模拟模式
            self._mode = "simulate"

    def _on_timer(self):
        """每 25ms 计算一帧音频分析结果"""
        if not self._running:
            return
        t = time.time() - self._start_t
        frame = self._compute_frame(t)
        self._emit_frame(frame)

    def _run_analysis_loop(self):
        """Background analysis loop; keeps FFT work off the Qt UI thread."""
        next_t = time.perf_counter()
        interval = 0.025
        while self._running and not self._stop_event.is_set():
            # ★★ 段错误根治的**最硬一道闸**（2026-09-19）：
            #   `sys.is_finalizing()` 是解释器"正在关闭"的权威信号。daemon 线程
            #   在关闭窗口期**仍会执行 Python 代码**（daemon 只保证退出时被杀，
            #   不保证不再运行）→ 此时 `_compute_frame` / emit 触碰已销毁的
            #   C++ 对象或已回收的模块 = C 级 access violation。
            #   实测崩溃栈正是本函数（`audio_driver.py:679`，sleep 唤醒后继续跑）。
            #   这里一旦检测到关闭立即 return（不 join、不 emit，什么都不碰）。
            # ★ 关闭窗口期两道判据都要查：① 解释器正在关闭；② 本对象的 C++ 侧
            #   已被销毁（Qt 父对象析构 / deleteLater 生效）——后者会让
            #   `_compute_frame` 触碰已释放成员、`_emit_frame` 打到已销毁接收者，
            #   实测崩溃栈正是本循环。任一命中立即收手（什么都不碰）。
            if sys.is_finalizing() or not _qt_obj_alive(self):
                return
            now = time.perf_counter()
            if now < next_t:
                # 用纯 time.sleep 替代 Event.wait 睡眠: Event.wait 内部走
                # Condition.wait -> 2 次 lock.acquire, 40Hz 下 ~400 次锁操作/秒,
                # GIL 饥饿时被拉长成主线程拖动卡顿的一部分。纯 sleep 释放 GIL 且
                # 无锁机制开销, 节奏(25ms)与结果帧完全相同。
                time.sleep(min(interval, next_t - now))
                continue
            t = time.time() - self._start_t
            try:
                frame = self._compute_frame(t)
                self._emit_frame(frame)
            except Exception:
                logger.debug("音频分析循环中计算帧失败")
                pass
            next_t += interval
            if next_t < now - interval:
                next_t = now + interval

    def _emit_frame(self, frame: AudioFrame):
        self._last_frame = frame
        # ★ 退出期安全闸（见模块顶部说明）：分析线程在解释器/Qt 清理期间仍会跑到
        #   这里，此时 emit 打到已销毁的接收者 = C 级 access violation。先查自身
        #   存活；不可用就直接丢弃本帧（进程正在退出，丢帧无任何副作用）。
        if not _qt_obj_alive(self):
            return
        self.frame_ready.emit(frame)
        self.energy_updated.emit(float(frame.rms))
        if frame.is_beat:
            self.beat_detected.emit(float(self._bpm_detector.bpm))
        # ★ 统一归一化输出（与频段/通道无关，所有订阅者拿到完全相同的形状）
        try:
            uval = self._compute_unified(frame)
            if self.unified_value.receivers():
                self.unified_value.emit(uval)
        except Exception:
            logger.debug("发出统一音频值信号时失败")
            pass

    def current_frame(self) -> AudioFrame:
        """Return the latest audio analysis frame without requiring a Qt signal subscriber."""
        return self._last_frame

    def frame_at(self, pos: float) -> AudioFrame:
        """Synchronously analyze the loaded timeline audio at a specific second."""
        # ★ R193/P2-21（AUD-06）：位置经参数传递不再改共享 `_timeline_pos`
        #（旧实现临时改字段且异常路径不恢复，与分析线程竞态读错窗口）
        try:
            if self._mode == "file" and self._file_data is not None:
                frame = self._analyze_file_at_position(float(pos))
            elif self._mode == "microphone":
                frame = self._last_frame
            else:
                frame = self._simulate_frame(float(pos))
            self._last_frame = frame
            return frame
        except Exception:
            logger.debug("同步分析时间线音频帧时失败，回退到上次帧")
            return self._last_frame

    def set_unified_source(self, source: str, band_idx: int = 1):
        """配置统一输出的采样源。
        source: "rms" | "peak" | "beat" | "band"
        band_idx: 当 source=="band" 时使用的频段索引（0..7）
        """
        if source in ("rms", "peak", "beat", "band"):
            self._unified_source = source
            self._unified_band_idx = max(0, min(7, int(band_idx)))

    def _compute_unified(self, frame: AudioFrame) -> float:
        """从一帧音频计算 0..1 的统一形状值。"""
        src = self._unified_source
        if src == "rms":
            v = frame.rms
        elif src == "peak":
            v = frame.peak
        elif src == "beat":
            # 节拍衰减形状：刚击中=1，相位 1=0
            v = math.exp(-frame.beat_phase * 4.0)
        elif src == "band":
            idx = self._unified_band_idx
            v = frame.bands[idx] if idx < len(frame.bands) else 0.0
        else:
            v = frame.rms
        return max(0.0, min(1.0, float(v)))

    def _compute_frame(self, t: float) -> AudioFrame:
        """计算当前帧的音频分析结果"""
        if self._mode == "file" and self._file_data is not None:
            return self._analyze_file_at_position()
        elif self._mode == "microphone" and self._mic_buffer is not None:
            return self._analyze_mic_buffer(t)
        else:
            return self._simulate_frame(t)

    def _analyze_file_at_position(self, pos: float = None) -> AudioFrame:
        """根据时间线播放位置分析音频文件对应片段

        ★ R193/P2-21：pos 可显式传入（frame_at 参数化，不再临时改共享
        `_timeline_pos`）；缺省仍读字段（分析线程原路径不变）。"""
        onset_source = onset_source_snapshot(self, self._file_sr)
        pos = self._timeline_pos if pos is None else float(pos)
        if self._file_data is None or pos < 0:
            return AudioFrame(t=pos)

        # 计算对应的采样点范围
        sample_start = int(pos * self._file_sr)
        sample_end = sample_start + self._chunk

        if sample_start >= len(self._file_data):
            return AudioFrame(t=pos)  # 超出文件范围

        # 提取音频片段
        buf = self._file_data[sample_start:min(sample_end, len(self._file_data))]
        if len(buf) < self._chunk:
            buf = np.pad(buf, (0, self._chunk - len(buf)))

        buf = buf * self._gain
        rms = float(np.sqrt(np.mean(buf ** 2)))
        peak = float(np.max(np.abs(buf)))

        # FFT 频谱分析
        window = hann_window(len(buf))
        fft = np.abs(np.fft.rfft(buf * window))
        bands = normalized_bands(fft, self._file_sr, len(buf)).tolist()

        # 起音检测
        onset_strength, is_beat = analyze_onset(self, fft, rms, pos, self._file_sr, sample_source=onset_source)

        pm, pconf, croot, cqual = self._lookup_pitch(pos)
        return AudioFrame(
            t=pos, rms=min(1.0, rms), peak=min(1.0, peak),
            bands=bands, bpm=self._bpm_detector.bpm,
            beat_phase=self._bpm_detector.beat_phase,
            is_beat=is_beat, onset_strength=onset_strength,
            has_audio=True,  # ★ 文件模式有真实音频
            pitch_midi=pm, pitch_confidence=pconf,
            chord_root=croot, chord_quality=cqual,
        )

    def _analyze_mic_buffer(self, t: float) -> AudioFrame:
        """分析麦克风缓冲区"""
        onset_source = onset_source_snapshot(self, self._sr)
        with self._mic_lock:
            buf = self._mic_buffer.copy() if self._mic_buffer is not None else np.zeros(self._chunk)

        buf = buf * self._gain
        rms = float(np.sqrt(np.mean(buf ** 2)))
        peak = float(np.max(np.abs(buf)))

        # FFT 频谱分析
        fft = np.abs(np.fft.rfft(buf * hann_window(len(buf))))
        bands = normalized_bands(fft, self._sr, len(buf)).tolist()

        # 起音强度（能量突变）
        onset_strength, is_beat = analyze_onset(self, fft, rms, t, self._sr, sample_source=onset_source)
        return AudioFrame(
            t=t, rms=min(1.0, rms), peak=min(1.0, peak),
            bands=bands, bpm=self._bpm_detector.bpm,
            beat_phase=self._bpm_detector.beat_phase,
            is_beat=is_beat, onset_strength=onset_strength,
            has_audio=True,  # ★ 麦克风模式有真实音频
        )

    def _simulate_frame(self, t: float) -> AudioFrame:
        """模拟音频帧（无需硬件，基于 BPM 生成）"""
        # 核心修复：只有在显式开启 simulation_enabled 时才产生数据。
        # 默认设为 False，防止 Programmer 在空闲时因自激噪声或模拟逻辑导致数值乱跳。
        if not getattr(self, "_simulation_enabled", False):
            return AudioFrame(
                t=t, rms=0.0, peak=0.0, bands=[0.0]*8,
                bpm=self._simulate_bpm, beat_phase=0.0,
                is_beat=False, onset_strength=0.0,
                has_audio=False  # ★ 无音频标记
            )

        bpm = self._simulate_bpm
        beat_period = 60.0 / bpm
        beat_phase = (t % beat_period) / beat_period
        beat_energy = math.exp(-beat_phase * 5.0)
        rng = np.random.RandomState(int(t * 40))
        bands = []
        for i in range(8):
            base = beat_energy * (1.0 - i * 0.1) * self._gain
            noise = rng.random() * 0.1
            bands.append(min(1.0, max(0.0, base + noise)))

        rms = beat_energy * 0.8 * self._gain
        peak = beat_energy * self._gain

        # 检测节拍
        onset_strength = beat_energy
        is_beat = beat_phase < 0.1  # 简易节拍模拟
        return AudioFrame(
            t=t, rms=min(1.0, rms), peak=min(1.0, peak),
            bands=bands, bpm=bpm, beat_phase=beat_phase,
            is_beat=is_beat, onset_strength=onset_strength,
            has_audio=True  # ★ 模拟模式有音频
        )


# ─────────────────────────────────────────────────────────────────────────────
#  颜色常量
# ─────────────────────────────────────────────────────────────────────────────

# 值统一映射自 qascade_theme 的 主题令牌；控件级 QSS 因嵌入行需紧凑尺寸而保留，
# 数值与全局主题对齐（不再使用玫红/藏蓝等旧局部色）。
from .qascade_theme import (
    BG_ROOT as _T_BG_ROOT,
    BG_EDITOR as _T_BG_PANEL,
    BORDER_NORMAL as _T_BORDER,
    BG_BUTTON as _T_BG_BUTTON,
    BG_BUTTON_HOVER as _T_BG_BUTTON_HOVER,
    SELECT_BLUE as _T_SELECT_BLUE,
    ACTIVE_ORANGE as _T_ACTIVE_ORANGE,
    FOCUS_BLUE as _T_FOCUS,
    BRIGHT_SUCCESS as _T_GREEN,
    BRIGHT_WARN as _T_WARN,
    TEXT_PRIMARY as _T_FG,
    TEXT_SECONDARY as _T_FG_DIM,
)

BG_MAIN   = _T_BG_ROOT        # #1D1D1D
BG_PANEL  = _T_BG_PANEL       # #303030
FG_MAIN   = _T_FG             # #E6E6E6
FG_DIM    = _T_FG_DIM         # #999999
FG_ACCENT = _T_FOCUS          # #71A8FF
FG_GREEN  = _T_GREEN          # #56B65B
FG_ORANGE = _T_WARN           # #E6B455
ACCENT    = _T_ACTIVE_ORANGE  # 节拍闪光激活橙（原玫红 b85a6e 已收敛）

_BTN = (
    f"QPushButton{{background:{_T_BG_BUTTON};color:{_T_FG};border:1px solid {_T_BORDER};"
    "border-radius:4px;padding:3px 8px;font-size:11px;}"
    f"QPushButton:hover{{background:{_T_BG_BUTTON_HOVER};}}"
    f"QPushButton:pressed{{background:{_T_SELECT_BLUE};color:#FFFFFF;}}"
)
_BTN_ACCENT = (
    f"QPushButton{{background:{_T_ACTIVE_ORANGE};color:#1D1D1D;border:none;"
    "border-radius:4px;padding:4px 10px;font-size:11px;font-weight:bold;}"
    "QPushButton:hover{background:#FFB35C;}"     # 激活橙提亮一档
)
_COMBO = (
    f"QComboBox{{background:#282828;color:{_T_FG};border:1px solid {_T_BORDER};"
    "border-radius:4px;padding:2px 5px;font-size:11px;}"
    "QComboBox::drop-down{border:none;width:16px;}"
    f"QComboBox QAbstractItemView{{background:#282828;color:{_T_FG};"
    f"selection-background-color:{_T_SELECT_BLUE};}}"
)
_SPIN = (
    f"QDoubleSpinBox{{background:#1D1D1D;color:{_T_FG};"
    f"border:1px solid {_T_BORDER};border-radius:4px;padding:2px;font-size:11px;}}"
)
_SLIDER = (
    "QSlider::groove:horizontal{background:#242424;height:3px;border-radius:2px;}"
    f"QSlider::handle:horizontal{{background:#8A8A8A;width:10px;height:10px;"
    "margin:-4px 0;border-radius:5px;}"
    f"QSlider::sub-page:horizontal{{background:{_T_SELECT_BLUE};border-radius:2px;}}"
)

# 8 频段识别色：保持各频段 hue 不变，仅统一饱和度/明度（§4.D 数据编码语义）
_SPECTRUM_COLORS = ["#D95252", "#D98352", "#D99C52", "#D9C052", "#83D952", "#52D983", "#529CD9", "#8352D9"]



# ─────────────────────────────────────────────────────────────────────────────
#  能量条显示控件
# ─────────────────────────────────────────────────────────────────────────────

class EnergyMeter(QWidget):
    """实时能量条（8频段 + RMS）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bands: List[float] = [0.0] * 8
        self._rms: float = 0.0
        self._beat_flash: float = 0.0
        self.setMinimumSize(160, 48)
        self.setMaximumHeight(56)
        self.setStyleSheet(f"background:{BG_PANEL};border-radius:4px;")

    def update_frame(self, frame: AudioFrame):
        self._bands = list(frame.bands)
        self._rms = frame.rms
        if frame.is_beat:
            self._beat_flash = 1.0
        else:
            self._beat_flash = max(0.0, self._beat_flash - 0.15)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # 背景
        p.fillRect(0, 0, w, h, QColor(BG_PANEL))

        # 节拍闪光
        if self._beat_flash > 0:
            flash_color = QColor(ACCENT)
            flash_color.setAlphaF(self._beat_flash * 0.3)
            p.fillRect(0, 0, w, h, flash_color)

        # 8频段能量条
        n = len(self._bands)
        bar_w = (w - 4) / n
        colors = _SPECTRUM_COLORS
        for i, energy in enumerate(self._bands):
            x = 2 + i * bar_w
            bar_h = max(2, int(energy * (h - 8)))
            color = QColor(colors[i % len(colors)])
            color.setAlphaF(0.7 + energy * 0.3)
            p.fillRect(int(x), h - 4 - bar_h, int(bar_w - 1), bar_h, color)

        # RMS 整体能量线
        rms_x = int(self._rms * (w - 4)) + 2
        p.setPen(QPen(QColor(_T_FG), 1))
        p.drawLine(rms_x, h - 4, rms_x, 4)

        # 精修：外圈 1px 发丝线（不改变电平语义）
        p.setPen(QPen(QColor(_T_BORDER), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(0, 0, w - 1, h - 1)
        p.end()


# ─────────────────────────────────────────────────────────────────────────────
#  音频驱动控件（嵌入 Programmer 通道行）
# ─────────────────────────────────────────────────────────────────────────────

class AudioDriverWidget(QWidget):
    """
    音频驱动控件，嵌入到 Programmer 的通道类型行中。
    提供：
      - 模式选择（模拟/麦克风）
      - BPM 显示 + Tap Tempo
      - 频段能量显示
      - 增益/灵敏度调节
      - 频段→通道映射配置

    ★ v2: 不再直接写入 NetworkManager。
           改为发出 dmx_data_changed(np.ndarray) 信号，
           由 ProgrammerPanel 统一收集。
    """
    # ★ 新增：完整 DMX 数据信号（替代直接写 NetworkManager）
    dmx_data_changed = pyqtSignal(np.ndarray)

    # 发出映射后的通道值 (0-255)，供 ChannelController 兼容旧版
    channel_value_changed = pyqtSignal(str, int)   # (ch_type, value)
    value_changed         = pyqtSignal(int)        # 单值别名，兼容通用控制器接口

    def __init__(self, ch_type: str = "dimmer", parent=None):
        super().__init__(parent)
        self._ch_type = ch_type
        # 获取全局分析器，如果 C++ 对象已失效则重建
        self._analyzer = _ensure_valid_analyzer()
        self._band_idx = 0   # 使用哪个频段驱动此通道
        self._mapping = "energy"   # "energy" | "beat" | "band"
        self._lo = 0
        self._hi = 255
        self._last_frame: Optional[AudioFrame] = None
        self._build_ui()
        self._analyzer.frame_ready.connect(self._on_frame)

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        # 顶部：模式选择 + BPM + Tap
        top = QHBoxLayout()
        top.setSpacing(4)

        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["模拟", "麦克风", "时间线音频"])
        self._mode_combo.setStyleSheet(_COMBO)
        self._mode_combo.setFixedWidth(90)
        self._mode_combo.setToolTip("音频来源：内置模拟 / 麦克风 / 时间线已导入的音频文件。")
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        top.addWidget(self._mode_combo)

        self._bpm_label = QLabel("120.0 BPM")
        self._bpm_label.setStyleSheet(f"color:{FG_ACCENT};font-size:11px;font-weight:bold;")
        top.addWidget(self._bpm_label)

        tap_btn = QPushButton("Tap")
        tap_btn.setStyleSheet(_BTN_ACCENT)
        tap_btn.setFixedWidth(36)
        tap_btn.setFixedHeight(26)   # 主工具栏按钮高度档（§1）
        tap_btn.setToolTip("手动测速：跟着节拍连点几次，自动算出 BPM。")
        tap_btn.clicked.connect(self._on_tap)
        top.addWidget(tap_btn)

        # BPM 手动设置（模拟模式）
        self._bpm_spin = QDoubleSpinBox()
        self._bpm_spin.setRange(40.0, 300.0)
        self._bpm_spin.setValue(120.0)
        self._bpm_spin.setSingleStep(1.0)
        self._bpm_spin.setDecimals(1)
        self._bpm_spin.setFixedWidth(68)
        self._bpm_spin.setStyleSheet(_SPIN)
        self._bpm_spin.setToolTip("模拟模式下的节拍速度（BPM）。")
        self._bpm_spin.valueChanged.connect(self._analyzer.set_simulate_bpm)
        top.addWidget(self._bpm_spin)

        lay.addLayout(top)

        # 中部：能量条
        self._meter = EnergyMeter()
        lay.addWidget(self._meter)

        # 底部：映射配置
        bot = QHBoxLayout()
        bot.setSpacing(4)

        bot.addWidget(self._mk_label("映射:"))

        self._map_combo = QComboBox()
        for mode_id, mode_label in MAPPING_MODE_LABELS.items():
            self._map_combo.addItem(mode_label, mode_id)
        self._map_combo.setStyleSheet(_COMBO)
        self._map_combo.setFixedWidth(80)
        self._map_combo.setToolTip("选择音频→通道值的映射模式（节拍/能量/频段/包络…）。")
        self._map_combo.currentIndexChanged.connect(self._on_map_changed)
        bot.addWidget(self._map_combo)

        self._band_combo = QComboBox()
        self._band_combo.addItems(FREQ_BANDS)
        self._band_combo.setStyleSheet(_COMBO)
        self._band_combo.setFixedWidth(80)
        self._band_combo.setEnabled(False)
        self._band_combo.setToolTip("仅「频段能量/多频段混合」模式生效：选用哪个频段驱动。")
        self._band_combo.currentIndexChanged.connect(lambda i: setattr(self, "_band_idx", i))
        bot.addWidget(self._band_combo)

        # 增益滑块
        bot.addWidget(self._mk_label("增益:"))
        gain_slider = QSlider(Qt.Orientation.Horizontal)
        gain_slider.setRange(1, 50)
        gain_slider.setValue(10)
        gain_slider.setStyleSheet(_SLIDER)
        gain_slider.setFixedWidth(60)
        gain_slider.setToolTip("输入增益（×0.1 ~ ×5.0）。")
        gain_slider.valueChanged.connect(lambda v: self._analyzer.set_gain(v / 10.0))
        bot.addWidget(gain_slider)
        # v25: Attack/Release/Threshold
        bot.addWidget(self._mk_label("Atk:"))
        self._attack_slider = QSlider(Qt.Orientation.Horizontal)
        self._attack_slider.setRange(1, 100)
        self._attack_slider.setValue(10)
        self._attack_slider.setStyleSheet(_SLIDER)
        self._attack_slider.setFixedWidth(45)
        self._attack_slider.setToolTip("起音时间（ms）：值越小跟随越快。")
        bot.addWidget(self._attack_slider)
        bot.addWidget(self._mk_label("Rel:"))
        self._release_slider = QSlider(Qt.Orientation.Horizontal)
        self._release_slider.setRange(10, 2000)
        self._release_slider.setValue(200)
        self._release_slider.setStyleSheet(_SLIDER)
        self._release_slider.setFixedWidth(45)
        self._release_slider.setToolTip("释放时间（ms）：值越大回落越慢。")
        bot.addWidget(self._release_slider)
        bot.addWidget(self._mk_label("Thr:"))
        self._threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self._threshold_slider.setRange(0, 100)
        self._threshold_slider.setValue(30)
        self._threshold_slider.setStyleSheet(_SLIDER)
        self._threshold_slider.setFixedWidth(45)
        self._threshold_slider.setToolTip("门限阈值（%）：低于此能量的输入不触发。")
        bot.addWidget(self._threshold_slider)

        lay.addLayout(bot)

        # v25: output + spectrum
        self._output_lbl = QLabel("输出: 0")
        self._output_lbl.setStyleSheet(f"color:{FG_GREEN};font-size:10px;font-weight:bold;")
        lay.addWidget(self._output_lbl)
        self._spectrum = SpectrumBar()
        self._spectrum.setMaximumHeight(22)
        lay.addWidget(self._spectrum)
        self._envelope_val = 0.0
        self._beat_step_val = 0
        self._onset_prev_rms = 0.0
        self._last_ui_frame_t = 0.0
        self._ui_frame_interval = 1.0 / 15.0
        self.setStyleSheet(f"background:{BG_PANEL};border-radius:6px;")

    def _mk_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        return lbl

    def _on_tap(self):
        self._analyzer.tap_tempo()

    def _on_mode_changed(self, idx: int):
        """模式切换：0=模拟, 1=麦克风, 2=时间线音频"""
        modes = ["simulate", "microphone", "file"]
        self._analyzer.set_mode(modes[idx])

    def link_timeline(self, audio_path: str):
        """将时间线导入的音频文件加载到分析器"""
        self._analyzer.load_audio_file(audio_path)
        # 自动切换到时间线音频模式
        self._mode_combo.blockSignals(True)
        self._mode_combo.setCurrentIndex(2)
        self._mode_combo.blockSignals(False)

    def on_timeline_tick(self, pos: float):
        """接收时间线播放位置更新（由 Programmer 面板转发）"""
        self._analyzer.set_timeline_position(pos)

    def on_timeline_play(self, pos: float):
        """时间线开始播放"""
        self._analyzer.set_timeline_playing(True)
        self._analyzer.set_timeline_position(pos)

    def on_timeline_stop(self):
        """时间线停止播放"""
        self._analyzer.set_timeline_playing(False)

    def _on_map_changed(self, idx: int):
        mode_id = self._map_combo.itemData(idx) or "energy"
        self._mapping = mode_id
        self._band_combo.setEnabled(mode_id in ("band", "multi_band"))

    def _on_frame(self, frame: AudioFrame):
        """每帧音频数据到达时的处理（v26 增强版）。"""
        self._last_frame = frame
        now_ui = time.monotonic()
        update_ui = now_ui - float(getattr(self, "_last_ui_frame_t", 0.0) or 0.0) >= float(
            getattr(self, "_ui_frame_interval", 1.0 / 15.0) or (1.0 / 15.0))
        if update_ui:
            self._last_ui_frame_t = now_ui
            self._meter.update_frame(frame)
            if hasattr(self, '_spectrum'):
                self._spectrum.update_frame(frame)
            self._bpm_label.setText(f"{frame.bpm:.1f} BPM")

        # ★ v26修复：无音频时不发出任何通道值信号，防止抖动
        if not getattr(frame, 'has_audio', False):
            if not hasattr(self, '_envelope_val'):
                self._envelope_val = 0.0
            # 让包络值衰减到0（release模式）
            release_ms = self._release_slider.value() if hasattr(self, '_release_slider') else 200
            release_coeff = min(1.0, 1000.0 / (release_ms * 25.0 + 1.0))
            self._envelope_val = max(0.0, self._envelope_val * (1.0 - release_coeff))
            if update_ui and hasattr(self, '_output_lbl'):
                self._output_lbl.setText("输出: -- (无音频)")
            return  # ★ 无音频时直接返回，不发出信号

        raw = self._compute_raw_mapping(frame)
        attack_ms = self._attack_slider.value() if hasattr(self, '_attack_slider') else 10
        release_ms = self._release_slider.value() if hasattr(self, '_release_slider') else 200
        attack_coeff = min(1.0, 1000.0 / (attack_ms * 25.0 + 1.0))
        release_coeff = min(1.0, 1000.0 / (release_ms * 25.0 + 1.0))
        if not hasattr(self, '_envelope_val'):
            self._envelope_val = 0.0
        if raw > self._envelope_val:
            self._envelope_val += (raw - self._envelope_val) * attack_coeff
        else:
            self._envelope_val += (raw - self._envelope_val) * release_coeff
        val = int(self._lo + max(0.0, min(1.0, self._envelope_val)) * (self._hi - self._lo))
        val = max(0, min(255, val))
        if update_ui and hasattr(self, '_output_lbl'):
            self._output_lbl.setText(f"输出: {val}")
        self.channel_value_changed.emit(self._ch_type, val)
        self.value_changed.emit(val)

    def _compute_raw_mapping(self, frame: AudioFrame) -> float:
        """根据当前映射模式计算原始值 (0~1)。"""
        threshold = (self._threshold_slider.value() if hasattr(self, '_threshold_slider') else 30) / 100.0
        if self._mapping == "energy":
            return min(1.0, frame.rms * 2.0)
        elif self._mapping == "beat":
            # ★ v26修复：乘以能量因子，无音频时不产生输出
            energy_gate = min(1.0, frame.rms * 5.0)
            return math.exp(-frame.beat_phase * 4.0) * energy_gate
        elif self._mapping == "band":
            return min(1.0, frame.bands[self._band_idx] if self._band_idx < len(frame.bands) else 0.0)
        elif self._mapping == "peak":
            return min(1.0, getattr(frame, 'peak', frame.rms) * 1.5)
        elif self._mapping == "envelope":
            return min(1.0, frame.rms * 2.0)
        elif self._mapping == "gate":
            return 1.0 if frame.rms > threshold else 0.0
        elif self._mapping == "ducking":
            # ★ v26修复：无音频时输出0而非1（原来rms=0时输出1.0）
            if not getattr(frame, 'has_audio', False):
                return 0.0
            return max(0.0, 1.0 - frame.rms * 3.0)
        elif self._mapping == "sidechain":
            return max(0.0, 1.0 - math.exp(-frame.beat_phase * 3.0))
        elif self._mapping == "multi_band":
            return min(1.0, sum(frame.bands) / max(1, len(frame.bands)) * 2.0) if frame.bands else 0.0
        elif self._mapping == "beat_step":
            if not hasattr(self, '_beat_step_val'):
                self._beat_step_val = 0
            if frame.is_beat:
                self._beat_step_val = (self._beat_step_val + 1) % 8
            return self._beat_step_val / 7.0
        elif self._mapping == "lfo_sync":
            # ★ v26修复：无音频时输出0
            if not getattr(frame, 'has_audio', False):
                return 0.0
            return (math.sin(2 * math.pi * frame.beat_phase) + 1.0) / 2.0
        elif self._mapping == "onset":
            if not hasattr(self, '_onset_prev_rms'):
                self._onset_prev_rms = 0.0
            delta = frame.rms - self._onset_prev_rms
            self._onset_prev_rms = frame.rms
            return min(1.0, max(0.0, delta * 8.0))
        elif self._mapping == "bpm":
            # ★ v26修复：无音频时输出0
            if not getattr(frame, 'has_audio', False):
                return 0.0
            return (math.sin(2 * math.pi * frame.beat_phase * 2) + 1.0) / 2.0
        else:
            return min(1.0, frame.rms * 2.0)

    def _compute_value(self, frame: AudioFrame) -> int:
        """旧版：按各 widget 自己的映射模式输出。新版兼容保留以备外部调用。"""
        if self._mapping == "energy":
            raw = frame.rms
        elif self._mapping == "beat":
            raw = math.exp(-frame.beat_phase * 4.0)
        else:  # band
            raw = frame.bands[self._band_idx] if self._band_idx < len(frame.bands) else 0.0
        val = int(self._lo + raw * (self._hi - self._lo))
        return max(0, min(255, val))

    def set_channel_type(self, ch_type: str):
        self._ch_type = ch_type

    def set_range(self, lo: int, hi: int):
        self._lo = lo
        self._hi = hi

    def start(self):
        self._analyzer.start()
        try:
            self._analyzer.frame_ready.disconnect(self._on_frame)
        except TypeError:
            pass
        self._analyzer.frame_ready.connect(self._on_frame)

    def stop(self):
        # 只断开自己的连接，不影响其他 widget
        try:
            self._analyzer.frame_ready.disconnect(self._on_frame)
        except TypeError:
            pass

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
#  全局音频分析器单例
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
#  进程退出期的线程安全闸（★ 2026-09-19 段错误根治）
# ─────────────────────────────────────────────────────────────────────────────
#  现象（长期既存、此前被误判为"面板/预热自身的隐患"）：`pytest tests/` 全量跑
#  **概率性** rc=139 access violation，崩溃栈落在本模块 `_run_analysis_loop`
#  （纯 HEAD 版同样崩）。真因 = 全局分析器是**后台线程**，它在解释器/Qt 清理
#  期间仍在 `_emit_frame` 里 emit Qt 信号 → 信号槽指向的对象已被销毁 → C 级崩溃。
#  修法两道闸：① emit 前查自身 C++ 对象是否仍存活；② atexit 兜底停线程。

def _resolve_sip_module():
    """一次性解析 sip 模块（PyQt6 的 sip 在 `PyQt6.sip`，裸 `sip` 通常不存在）。

    ★ R141 性能修复：原实现把 `import sip` 放在 `_qt_obj_alive` 里，而本环境裸 `sip`
      **不可导入**；失败的导入**不进 `sys.modules` 缓存** → 每次调用都重跑整套
      import machinery（11 个 finder × `os.stat`）。实测每 21 次调用约 77ms，
      占一次 effect `load_state` 耗时的一半。解析结果只与解释器环境有关，
      故在模块导入期定型。
    """
    try:
        from PyQt6 import sip as _s
        return _s
    except Exception:
        pass
    try:
        import sip as _s
        return _s
    except Exception:
        return None


_SIP_MODULE = _resolve_sip_module()


def _qt_obj_alive(obj) -> bool:
    """该 QObject 的 C++ 侧是否仍存活（销毁/清理中一律视为不可用）。"""
    s = _SIP_MODULE
    if s is not None:
        try:
            return not s.isdeleted(obj)
        except Exception:
            pass          # 判不出 → 退回弱判据（与旧实现同语义）
    # 无 sip（非 PyQt 环境）时退化为"能取到 objectName"的弱判据
    try:
        obj.objectName()
        return True
    except Exception:
        return False


_global_analyzer: Optional[AudioAnalyzer] = None

def _stop_global_analyzer_at_exit() -> None:
    """atexit 兜底：进程退出前停掉全局分析线程并等它收手。

    ★ 只在 atexit 阶段调用（比 Qt 对象销毁更早），join 超时短（0.3s）不拖退出。
    """
    global _global_analyzer
    an = _global_analyzer
    _global_analyzer = None
    if an is None:
        return
    try:
        an._running = False
        an._stop_event.set()
        th = getattr(an, "_thread", None)
        if th is not None and th is not threading.current_thread():
            th.join(timeout=0.3)
    except Exception:
        pass


def _get_global_analyzer() -> AudioAnalyzer:
    global _global_analyzer
    if _global_analyzer is None:
        _global_analyzer = AudioAnalyzer()
        # ★ 段错误根治第二道闸：注册一次 atexit，在解释器清理**之前**把后台
        #   分析线程停掉（否则它在清理期间 emit Qt 信号 → rc=139）。
        import atexit
        atexit.register(_stop_global_analyzer_at_exit)
        _global_analyzer.start()
    return _global_analyzer

def _retire_analyzer(an) -> None:
    """**停掉并退休**一个待废弃的分析器实例（★ 2026-09-19 段错误根治）。

    为什么必须显式退休：旧实现检测到 C++ 对象失效时只做 `_global_analyzer = None`
    —— 但该实例的**后台线程仍持有 self 在 40Hz 循环里跑**（线程引用 → 对象不会被
    回收），而它的 Qt 信号接收者已随 C++ 对象销毁 → **每 25ms 触碰已销毁对象**
    → 进程级 access violation（实测崩溃栈正是 `_run_analysis_loop`）。
    且此路径在测试里反复触发（每个用例新建/销毁 QApplication）→ 泄漏的僵尸分析器
    越积越多，必然崩。
    """
    try:
        an._running = False
        an._stop_event.set()
    except Exception:
        pass
    try:
        th = getattr(an, "_thread", None)
        if th is not None and th is not threading.current_thread():
            th.join(timeout=0.3)
    except Exception:
        pass
    try:
        an._thread = None
        an.deleteLater()
    except Exception:
        pass


def _ensure_valid_analyzer() -> AudioAnalyzer:
    """获取全局分析器；C++ 对象已失效则**先退休旧的**再重建。"""
    global _global_analyzer
    if _global_analyzer is not None:
        try:
            _ = _global_analyzer.objectName()  # 测试 C++ 对象是否有效
        except RuntimeError:
            # ★ 关键：不能只丢引用（见 `_retire_analyzer` 说明）—— 必须先停线程，
            #   否则僵尸线程在解释器清理期继续跑 = rc=139。
            _retire_analyzer(_global_analyzer)
            _global_analyzer = None
    if _global_analyzer is None:
        _global_analyzer = AudioAnalyzer()
        import atexit
        atexit.register(_stop_global_analyzer_at_exit)
        _global_analyzer.start()
    return _global_analyzer

def get_audio_driver() -> AudioAnalyzer:
    """获取全局音频驱动器"""
    return _ensure_valid_analyzer()


# ─────────────────────────────────────────────────────────────────────────────
#  SpectrumBar：8 频段彩色能量条，供频谱分段映射模式嵌入使用
# ─────────────────────────────────────────────────────────────────────────────
class SpectrumBar(QWidget):
    """
    显示 8 个频段的彩色能量条，可通过 update_frame(AudioFrame) 刷新。
    高度固定 56px，宽度自适应。
    """
    _COLORS = _SPECTRUM_COLORS
    _LABELS = ["Sub", "Bass", "Low", "Mid", "Hi-M", "Pres", "Air", "Bril"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bands: list = [0.0] * 8
        self._beat_flash: float = 0.0
        self.setMinimumSize(180, 56)
        self.setMaximumHeight(72)
        self.setStyleSheet(f"background:{BG_MAIN};border-radius:4px;")

    def update_frame(self, frame):
        self._bands = list(frame.bands)
        if frame.is_beat:
            self._beat_flash = 1.0
        else:
            self._beat_flash = max(0.0, self._beat_flash - 0.12)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(BG_MAIN))
        if self._beat_flash > 0:
            # 节拍闪光与 EnergyMeter 同族：active_orange（节拍/Tap 语义色）
            fc = QColor(_T_ACTIVE_ORANGE)
            fc.setAlphaF(self._beat_flash * 0.25)
            p.fillRect(0, 0, w, h, fc)
        n = 8
        bar_w = (w - 4) / n
        label_h = 12
        bar_area_h = h - label_h - 6
        font = QFont()
        font.setPixelSize(10)
        p.setFont(font)
        for i, energy in enumerate(self._bands):
            x = 2 + i * bar_w
            bar_h = max(2, int(energy * bar_area_h))
            color = QColor(self._COLORS[i % len(self._COLORS)])
            color.setAlphaF(0.6 + energy * 0.4)
            p.fillRect(int(x), h - label_h - 4 - bar_h, int(bar_w - 1), bar_h, color)
            p.setPen(QPen(QColor(FG_DIM)))
            p.drawText(int(x), h - label_h - 2, int(bar_w - 1), label_h,
                       0x0004 | 0x0080,
                       self._LABELS[i])
        # 精修：外圈 1px 发丝线（频段 hue 编码不动，§4.D）
        p.setPen(QPen(QColor(_T_BORDER), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(0, 0, w - 1, h - 1)
        p.end()
