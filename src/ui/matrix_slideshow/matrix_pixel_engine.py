"""
matrix_pixel_engine.py — 矩阵像素效果引擎
==========================================
将矩阵型设备组当作像素矩阵驱动，提供三大效果模式：

1. 音频 FFT 模式（对标 Razer Chroma Audio Visualizer）
   - 实时 FFT 频谱分析，将频率分箱映射到矩阵行/列
   - 支持：频谱柱状图、波形线、能量阈值触发 ON/OFF

2. 屏幕像素映射模式（对标 iCUE Screen Sampler / Ambilight）
   - 截取屏幕指定区域，缩放到矩阵分辨率
   - 每个矩阵格对应屏幕区域的平均亮度 → ON/OFF

3. 内置动态效果（对标 Razer Chroma 内置效果）
   - 波纹、追逐、螺旋、心跳、爆炸、随机闪烁等
   - 全部输出 ON/OFF 二值（适合泵/阀矩阵）

输出格式：{fixture_id: bool}  — 与 MatrixSlide.states 格式一致
"""
from __future__ import annotations

import logging
import math
import queue
import random
import time
import threading
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .matrix_pixel_audio_modes import initialize_audio_controls
from .matrix_pixel_audio_contract import prepare_array_frame, restore_array_frame

from ...utils.wing_block import (
    WING_DIRECTION_LOW_TO_HIGH,
    build_wing_block_units,
    normalize_wing_direction,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  效果模式常量
# ─────────────────────────────────────────────────────────────────────────────
MODE_AUDIO_FFT      = "audio_fft"       # 音频 FFT 频谱
MODE_AUDIO_WAVEFORM = "audio_waveform"  # 音频波形
MODE_AUDIO_ENERGY   = "audio_energy"    # 音频能量阈值
MODE_SCREEN_SAMPLE  = "screen_sample"   # 屏幕像素采样
MODE_VIDEO_SAMPLE   = "video_sample"    # 视频像素采样
MODE_EFFECT_RIPPLE  = "effect_ripple"   # 波纹扩散
MODE_EFFECT_CHASE   = "effect_chase"    # 追逐跑
MODE_EFFECT_SPIRAL  = "effect_spiral"   # 螺旋
MODE_EFFECT_HEARTBEAT = "effect_heartbeat"  # 心跳
MODE_EFFECT_EXPLODE = "effect_explode"  # 爆炸扩散
MODE_EFFECT_RAIN    = "effect_rain"     # 矩阵雨
MODE_EFFECT_SNAKE   = "effect_snake"    # 贪吃蛇
MODE_EFFECT_RANDOM  = "effect_random"   # 随机闪烁
MODE_EFFECT_FIRE    = "effect_fire"     # 火焰
MODE_EFFECT_WAVE    = "effect_wave"     # 波浪
# ── v25 新增像素效果模式 ──────────────────────────────────────────────────────
MODE_AUDIO_BEAT_PULSE  = "audio_beat_pulse"   # 节拍脉冲
MODE_AUDIO_BAND_MATRIX = "audio_band_matrix"  # 频段矩阵
MODE_AUDIO_BASS_WAVE   = "audio_bass_wave"    # 低频波浪
MODE_AUDIO_REACTIVE    = "audio_reactive"     # 音频反应式
MODE_AUDIO_SPECTRUM_RING = "audio_spectrum_ring"  # 频谱环
MODE_EFFECT_PLASMA     = "effect_plasma"      # 等离子体
MODE_EFFECT_CONWAY     = "effect_conway"      # 生命游戏
MODE_EFFECT_METEOR     = "effect_meteor"      # 流星雨
MODE_EFFECT_SCAN       = "effect_scan"        # 扫描线
MODE_EFFECT_BREATHE    = "effect_breathe"     # 呼吸脉动
MODE_EFFECT_FIREWORK   = "effect_firework"    # 烟花
MODE_EFFECT_TETRIS     = "effect_tetris"      # 俄罗斯方块

# ── v26 新增音频响应效果 ─────────────────────────────────────────────────────
MODE_AUDIO_WAVE_FIRE      = "audio_wave_fire"       # 音频响应火焰
MODE_AUDIO_RAIN_DROPS     = "audio_rain_drops"      # 音频响应雨滴
MODE_AUDIO_RIPPLE_POND    = "audio_ripple_pond"     # 音频水面涟漪
MODE_AUDIO_FLAME_WALL     = "audio_flame_wall"      # 音频火焰墙
MODE_AUDIO_WATERFALL      = "audio_waterfall"       # 音频瀑布
MODE_AUDIO_HEARTBEAT_SYNC = "audio_heartbeat_sync"  # BPM同步心跳
MODE_AUDIO_STROBE_MULTI   = "audio_strobe_multi"    # 多区域频闪
MODE_AUDIO_SPIRAL_PULSE   = "audio_spiral_pulse"    # 音频螺旋脉冲
MODE_AUDIO_DIAMOND_WAVE   = "audio_diamond_wave"    # 音频菱形波
MODE_AUDIO_VU_METER       = "audio_vu_meter"        # VU音量表
MODE_AUDIO_EQ_BARS        = "audio_eq_bars"         # 均衡器柱（带峰值保持）
MODE_AUDIO_WAVEFORM_MIRROR = "audio_waveform_mirror" # 镜像波形
MODE_AUDIO_PARTICLE_BURST = "audio_particle_burst"  # 音频粒子爆发
MODE_AUDIO_GRAVITY_DROPS  = "audio_gravity_drops"   # 重力音频滴
MODE_AUDIO_CROSS_WAVE     = "audio_cross_wave"      # 交叉波
MODE_AUDIO_CONSTELLATION  = "audio_constellation"   # 音频星座
MODE_AUDIO_DNA_HELIX      = "audio_dna_helix"       # DNA双螺旋

# 音频条形扩展预设：复用高性能条形计算管线，只切换频段布局和柱体形态。
MODE_AUDIO_EQ_SOFT        = "audio_eq_soft"
MODE_AUDIO_EQ_DOTS        = "audio_eq_dots"
MODE_AUDIO_EQ_CENTER      = "audio_eq_center"
MODE_AUDIO_EQ_SCAN        = "audio_eq_scan"
MODE_AUDIO_BAND_REVERSE   = "audio_band_reverse"
MODE_AUDIO_BAND_CENTER    = "audio_band_center"
MODE_AUDIO_BAND_DOTS      = "audio_band_dots"


PIXEL_MODES = {
    "音频频段条 / Razer Chroma 风格": [
        (MODE_AUDIO_EQ_BARS,       "雷蛇频段条（峰值保持）"),
        (MODE_AUDIO_EQ_SOFT,       "柔边频段条"),
        (MODE_AUDIO_EQ_DOTS,       "点阵频段条"),
        (MODE_AUDIO_EQ_CENTER,     "中心镜像频段条"),
        (MODE_AUDIO_EQ_SCAN,       "扫描纹理频段条"),
        (MODE_AUDIO_BAND_MATRIX,   "频段矩阵柱"),
        (MODE_AUDIO_BAND_REVERSE,  "反向频段矩阵"),
        (MODE_AUDIO_BAND_CENTER,   "中心低频矩阵"),
        (MODE_AUDIO_BAND_DOTS,     "点阵频段矩阵"),
        (MODE_AUDIO_VU_METER,      "VU 音量条"),
        (MODE_AUDIO_FFT,           "FFT 频谱柱"),
        (MODE_AUDIO_WAVEFORM,      "音频波形条"),
        (MODE_AUDIO_ENERGY,        "整体能量触发"),
    ],
    "屏幕与视频采样": [
        (MODE_SCREEN_SAMPLE,  "屏幕像素采样"),
        (MODE_VIDEO_SAMPLE,   "视频像素采样"),
    ],
}



# ─────────────────────────────────────────────────────────────────────────────
#  矩阵坐标系辅助
# ─────────────────────────────────────────────────────────────────────────────

class MatrixGrid:
    """
    将设备列表（带 pos_x/pos_y）映射到逻辑网格坐标。
    支持任意排布（不要求严格矩形），使用归一化坐标。
    """

    def __init__(self, fixtures: List[Dict], wings: int = 1, block_size: int = 1):
        self.fixtures = fixtures
        self.n = len(fixtures)
        if self.n == 0:
            self.norm_x: List[float] = []
            self.norm_y: List[float] = []
            self.norm_x_np = np.zeros(0, dtype=np.float32)
            self.norm_y_np = np.zeros(0, dtype=np.float32)
            self.col_np = np.zeros(0, dtype=np.int32)
            self.row_np = np.zeros(0, dtype=np.int32)
            self.unit_np = np.zeros(0, dtype=np.int32)
            self.unit_count = 0
            self.cols = 0
            self.rows = 0
            return

        xs = [float(f.get("pos_x", 0.0) or 0.0) for f in fixtures]
        ys = [float(f.get("pos_y", 0.0) or 0.0) for f in fixtures]
        mn_x, mx_x = min(xs), max(xs)
        mn_y, mx_y = min(ys), max(ys)
        rx = max(1e-6, mx_x - mn_x)
        ry = max(1e-6, mx_y - mn_y)
        raw_x = np.asarray([(x - mn_x) / rx for x in xs], dtype=np.float32)
        raw_y = np.asarray([(y - mn_y) / ry for y in ys], dtype=np.float32)

        wings = max(1, min(int(wings or 1), self.n))
        block_size = max(1, int(block_size or 1))
        wing_size = max(1, int(math.ceil(self.n / wings)))
        unit_ids = np.zeros(self.n, dtype=np.int32)
        unit_ranges: List[Tuple[int, int]] = []
        unit = 0
        for wing_idx in range(wings):
            wing_start = wing_idx * wing_size
            wing_end = min(self.n, wing_start + wing_size)
            if wing_start >= wing_end:
                continue
            for start in range(wing_start, wing_end, block_size):
                end = min(wing_end, start + block_size)
                unit_ids[start:end] = unit
                unit_ranges.append((start, end))
                unit += 1
        if not unit_ranges:
            unit_ranges = [(0, self.n)]
            unit_ids[:] = 0
            unit = 1
        self.unit_np = unit_ids
        self.unit_count = max(1, unit)

        unit_x = np.zeros(self.unit_count, dtype=np.float32)
        unit_y = np.zeros(self.unit_count, dtype=np.float32)
        for unit_idx, (start, end) in enumerate(unit_ranges):
            unit_x[unit_idx] = float(np.mean(raw_x[start:end]))
            unit_y[unit_idx] = float(np.mean(raw_y[start:end]))

        norm_x_np = unit_x[unit_ids]
        norm_y_np = unit_y[unit_ids]
        self.norm_x = [float(v) for v in norm_x_np]
        self.norm_y = [float(v) for v in norm_y_np]
        self.norm_x_np = np.asarray(self.norm_x, dtype=np.float32)
        self.norm_y_np = np.asarray(self.norm_y, dtype=np.float32)

        # 块=1 时每台设备都是独立效果单元；块>N 时每块共享一个逻辑采样点。
        unit_x_keys = [round(float(v), 5) for v in unit_x]
        unit_y_keys = [round(float(v), 5) for v in unit_y]
        unique_x = sorted(set(unit_x_keys))
        unique_y = sorted(set(unit_y_keys))
        grid_product = len(unique_x) * len(unique_y)
        if grid_product > max(self.unit_count * 4, 4096):
            self.cols = self.unit_count
            self.rows = 1
            self.col_np = unit_ids.astype(np.int32, copy=True)
            self.row_np = np.zeros(self.n, dtype=np.int32)
        else:
            x_map = {key: i for i, key in enumerate(unique_x)}
            y_map = {key: i for i, key in enumerate(unique_y)}
            self.cols = max(1, len(unique_x))
            self.rows = max(1, len(unique_y))
            unit_col = np.asarray([x_map[key] for key in unit_x_keys], dtype=np.int32)
            unit_row = np.asarray([y_map[key] for key in unit_y_keys], dtype=np.int32)
            self.col_np = unit_col[unit_ids]
            self.row_np = unit_row[unit_ids]

    def get_norm_pos(self, idx: int) -> Tuple[float, float]:
        """返回第 idx 个设备的归一化坐标 (nx, ny)，范围 [0, 1]"""
        if 0 <= idx < self.n:
            return self.norm_x[idx], self.norm_y[idx]
        return 0.5, 0.5

    def get_col_row(self, idx: int) -> Tuple[int, int]:
        """返回第 idx 个设备的逻辑列/行（0-indexed）"""
        if 0 <= idx < self.n:
            return int(self.col_np[idx]), int(self.row_np[idx])
        return 0, 0

    def distance_to_center(self, idx: int) -> float:
        """返回设备到中心的归一化距离"""
        nx, ny = self.get_norm_pos(idx)
        return math.sqrt((nx - 0.5) ** 2 + (ny - 0.5) ** 2) * math.sqrt(2)


# ─────────────────────────────────────────────────────────────────────────────
#  音频 FFT 采集（跨平台，优先 sounddevice，降级到 pyaudio）
# ─────────────────────────────────────────────────────────────────────────────

class AudioCapture:
    """实时音频采集和 FFT 分析"""

    SAMPLE_RATE = 44100
    CHUNK_SIZE  = 1024
    FFT_SIZE    = 2048

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._fft_data: np.ndarray = np.zeros(self.FFT_SIZE // 2)
        self._rms: float = 0.0
        self._waveform: np.ndarray = np.zeros(self.CHUNK_SIZE)
        self._backend: str = "none"
        self._stream = None

        # 低通滤波状态
        self._filtered_fft: np.ndarray = np.zeros(self.FFT_SIZE // 2)

        # 尝试导入音频库
        try:
            import sounddevice as sd
            self._backend = "sounddevice"
        except ImportError:
            try:
                import pyaudio
                self._backend = "pyaudio"
            except ImportError:
                self._backend = "none"

    def start(self, device_index: int = -1):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, args=(device_index,),
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False; t = self._thread  #  R193/P2-16：照 ScreenCaptureEngine 停止模板——先 join 采集线程再关流
        if t is not None: t.join(timeout=1.0); self._thread = None  # 引用置 None 前先 join
        if self._stream is not None:
            try:
                self._stream.stop(); self._stream.close()
            except Exception:
                pass
            self._stream = None

    def get_fft(self) -> np.ndarray:
        with self._lock:
            return self._filtered_fft.copy()

    def get_rms(self) -> float:
        with self._lock:
            return self._rms

    def get_waveform(self) -> np.ndarray:
        with self._lock:
            return self._waveform.copy()

    def _run(self, device_index: int):
        if self._backend == "sounddevice":
            self._run_sounddevice(device_index)
        elif self._backend == "pyaudio":
            self._run_pyaudio(device_index)

    def _process_chunk(self, data: np.ndarray, filter_const: float = 0.7):
        """处理音频块：计算 FFT 和 RMS"""
        if data.ndim > 1:
            data = data[:, 0]  # 取单声道
        # 计算均方根（RMS）
        rms = float(np.sqrt(np.mean(data ** 2)))
        # 快速傅里叶变换（Hanning 窗）
        n = min(len(data), self.FFT_SIZE)
        chunk = data[:n]
        if len(chunk) < self.FFT_SIZE:
            chunk = np.pad(chunk, (0, self.FFT_SIZE - len(chunk)))
        window = np.hanning(self.FFT_SIZE)
        fft = np.abs(np.fft.rfft(chunk * window))[:self.FFT_SIZE // 2]
        # 低通滤波（平滑）
        with self._lock:
            self._filtered_fft = self._filtered_fft * filter_const + fft * (1 - filter_const)
            self._rms = rms
            self._waveform = data[:self.CHUNK_SIZE].copy() if len(data) >= self.CHUNK_SIZE else np.pad(data, (0, self.CHUNK_SIZE - len(data)))

    def _run_sounddevice(self, device_index: int):
        try:
            import sounddevice as sd
            kwargs = {"samplerate": self.SAMPLE_RATE, "channels": 1,
                      "dtype": "float32", "blocksize": self.CHUNK_SIZE}
            if device_index >= 0:
                kwargs["device"] = device_index
            with sd.InputStream(**kwargs) as stream:
                self._stream = stream
                while self._running:
                    data, _ = stream.read(self.CHUNK_SIZE)
                    self._process_chunk(data.flatten())
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("AudioCapture sounddevice error: %s", e)

    def _run_pyaudio(self, device_index: int):
        try:
            import pyaudio
            p = pyaudio.PyAudio()
            kwargs = {"format": pyaudio.paFloat32, "channels": 1,
                      "rate": self.SAMPLE_RATE, "input": True,
                      "frames_per_buffer": self.CHUNK_SIZE}
            if device_index >= 0:
                kwargs["input_device_index"] = device_index
            stream = p.open(**kwargs)
            self._stream = stream
            while self._running:
                raw = stream.read(self.CHUNK_SIZE, exception_on_overflow=False)
                data = np.frombuffer(raw, dtype=np.float32)
                self._process_chunk(data)
            stream.stop_stream()
            stream.close()
            p.terminate()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("AudioCapture pyaudio error: %s", e)

    def list_devices(self) -> List[Dict]:
        """列出可用音频输入设备"""
        devices = []
        if self._backend == "sounddevice":
            try:
                import sounddevice as sd
                for i, d in enumerate(sd.query_devices()):
                    if d["max_input_channels"] > 0:
                        devices.append({"index": i, "name": d["name"]})
            except Exception:
                pass
        elif self._backend == "pyaudio":
            try:
                import pyaudio
                p = pyaudio.PyAudio()
                for i in range(p.get_device_count()):
                    d = p.get_device_info_by_index(i)
                    if d["maxInputChannels"] > 0:
                        devices.append({"index": i, "name": d["name"]})
                p.terminate()
            except Exception:
                pass
        return devices


# ─────────────────────────────────────────────────────────────────────────────
#  屏幕采样器
# ─────────────────────────────────────────────────────────────────────────────

class ScreenSampler:
    """
    屏幕区域像素采样包装器。
    内部委托给 ScreenCaptureEngine 实现完整的采样逻辑（多通道、平滑滤波等）。
    保留此类以兼容旧接口。
    """

    def __init__(self):
        try:
            from .screen_capture_engine import ScreenCaptureEngine
            self._engine = ScreenCaptureEngine()
            self._available = self._engine.available
        except Exception:
            self._engine = None
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    @property
    def engine(self):
        """直接访问底层 ScreenCaptureEngine 实例。"""
        return self._engine

    def sample(self, region: Optional[Dict] = None,
               grid_w: int = 10, grid_h: int = 6) -> np.ndarray:
        """
        采样屏幕区域，返回 (grid_h, grid_w) 归一化亮度矩阵 [0.0, 1.0]。
        region: {"left": int, "top": int, "width": int, "height": int}
                None = 全屏
        """
        if not self._available or self._engine is None:
            return np.zeros((grid_h, grid_w))
        try:
            self._engine.set_region(region)
            self._engine.set_grid(grid_w, grid_h)
            return self._engine.sample_once()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("ScreenSampler error: %s", e)
            return np.zeros((grid_h, grid_w))

    def get_monitors(self) -> List[Dict]:
        """列出可用显示器"""
        if self._engine is None:
            return []
        return self._engine.list_monitors()


# ─────────────────────────────────────────────────────────────────────────────
#  主像素效果引擎
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
#  R194/P1-8+HF-02 视频解码后台线程（照 screen_capture_engine 先例：
#    threading.Lock + daemon thread；锁外做全部重计算，锁内单次写回三元组）
# ─────────────────────────────────────────────────────────────────────────────
_VIDEO_TICK_WAIT = 0.008     # tick 等帧就绪上限：慢解码只丢帧不拖 tick（p99<10ms）
_VIDEO_JOIN_TIMEOUT = 0.5    # stop join 上限；超时挂僵尸列表由 start 排空（R193 P2-20）
_VIDEO_IDLE_POLL = 0.2       # worker 空闲轮询（无请求低耗待命）
_VIDEO_REQ_BATCH_CAP = 8     # 单唤醒最多消费的推进请求数（与速率门单拍上限一致）
_VIDEO_CMD_TIMEOUT = 0.25    # GUI 同步命令（seek/refresh）等待上限


def _rgb_preview_of(frame_bgr, cv2):
    """BGR→RGB 预览转换（纯函数；失败回退通道反转；原 _update_preview_raw 语义）。"""
    try:
        return cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB).astype(np.uint8, copy=False)
    except Exception:
        return frame_bgr[:, :, ::-1].copy() if frame_bgr.ndim == 3 else frame_bgr


def _advance_video_once(eng) -> None:
    """消费一条推进请求：复用旧走带路径读帧并发布（worker/内联共用入口）。"""
    try:
        ok, frame = eng._read_next_video_frame()
    except Exception:
        logger.debug("matrix: video advance read failed", exc_info=True)
        return
    if ok and frame is not None:
        eng._frame_to_video_maps(frame)   # warp/转换在解码线程（锁外算，锁内写回）


def _drain_video_advance(eng) -> None:
    """批量消费推进请求：每请求恰一次旧 tick 推进；超载按上限丢弃保 tick。"""
    with eng._video_decode_lock:
        n = min(int(eng._video_pending_reqs), _VIDEO_REQ_BATCH_CAP)
        eng._video_pending_reqs -= n
    for _ in range(n):
        _advance_video_once(eng)
    with eng._video_frame_ready:
        eng._video_done_seq += n          # 按请求数回执：等帧方逐拍醒来（等价关键）
        eng._video_frame_ready.notify_all()


def _drain_external_frame(eng) -> None:
    """外部源（Spout）原始帧槽：latest-wins；warp/转换统一在解码线程（HF-02）。"""
    with eng._video_decode_lock:
        frame, eng._video_ext_frame = eng._video_ext_frame, None
    if frame is None:
        return
    try:
        eng._frame_to_video_maps(frame)
    except Exception:
        logger.debug("matrix: external frame decode failed", exc_info=True)


def _h_video_seek(eng, frame_idx) -> bool:
    return _seek_video_frame_body(eng, frame_idx)


def _h_video_refresh(eng, reset_history) -> bool:
    return _refresh_video_maps_body(eng, bool(reset_history))


_VIDEO_CMD_HANDLERS = {"seek": _h_video_seek, "refresh": _h_video_refresh}


def _drain_video_commands(eng) -> None:
    """消费同步命令队列（seek/refresh；结果槽回执给 GUI 等待方）。"""
    while True:
        try:
            kind, payload, done, slot = eng._video_cmd_queue.get_nowait()
        except queue.Empty:
            return
        try:
            slot["ok"] = bool(_VIDEO_CMD_HANDLERS[kind](eng, payload))
        except Exception:
            logger.debug("matrix: video cmd %s failed", kind, exc_info=True)
            slot["ok"] = False
        if done is not None:
            done.set()


def _drop_video_commands(eng) -> None:
    """排空命令队列（停线程防跨代命令落地；被丢命令的等待方按失败返回）。"""
    while True:
        try:
            _kind, _payload, done, _slot = eng._video_cmd_queue.get_nowait()
        except queue.Empty:
            return
        if done is not None:
            done.set()


def _video_worker_call(eng, kind: str, payload,
                       timeout: float = _VIDEO_CMD_TIMEOUT) -> bool:
    """worker 存活时同步委派命令；worker 内重入或无 worker → 内联执行。"""
    if threading.current_thread() is eng._video_thread or eng._video_thread is None:
        return bool(_VIDEO_CMD_HANDLERS[kind](eng, payload))
    done = threading.Event()
    slot = {}
    eng._video_cmd_queue.put((kind, payload, done, slot))
    eng._video_wake.set()
    if not done.wait(timeout):
        return False                      # 超时：命令仍在队列会落地；按失败回报
    return bool(slot.get("ok", False))


def _drain_video_zombies(eng) -> None:
    """start 前排空上一代僵尸解码线程（R193 P2-20/CC-01 同款守卫）。"""
    zombies = [t for t in eng._video_zombies if t.is_alive()]
    eng._video_zombies = []
    for thread in zombies:
        thread.join(timeout=_VIDEO_JOIN_TIMEOUT)
        if thread.is_alive():
            eng._video_zombies.append(thread)  # 仍活：引用不清自然消亡，拒绝复活


def _ensure_video_worker(eng) -> bool:
    """惰性启动解码线程；僵尸未退拒启（返回 False=本拍保持上一帧）。"""
    _drain_video_zombies(eng)
    thread = eng._video_thread
    if thread is not None and thread.is_alive():
        return True
    if eng._video_zombies:
        return False                      # 上一代未退：不复活成双解码线程
    eng._video_worker_stop.clear()
    eng._video_worker_gen += 1
    gen = eng._video_worker_gen
    eng._video_thread = threading.Thread(
        target=_video_worker_main, args=(eng, gen), name="matrix-video-decode",
        daemon=True)
    eng._video_thread.start()
    return True


def _stop_video_worker(eng) -> None:
    """停解码线程：信号 + join 超时 + 僵尸兜底（引用不清、start 排空）。"""
    thread = eng._video_thread
    eng._video_worker_gen += 1            # 代际号：被停/被接替的旧线程不碰新句柄
    if thread is None:
        _drop_video_commands(eng)
        return
    eng._video_worker_stop.set()
    eng._video_wake.set()
    if thread is not threading.current_thread():
        thread.join(timeout=_VIDEO_JOIN_TIMEOUT)
    if thread.is_alive():
        eng._video_zombies.append(thread)  # join 超时：挂僵尸列表，不阻塞调用方
    eng._video_thread = None
    with eng._video_decode_lock:
        eng._video_pending_reqs = 0        # 旧请求不跨代
    _drop_video_commands(eng)


def _close_video_handle(eng) -> None:
    """仅释放 cv2 句柄（热插拔守卫/停机兜底共用；不碰解码线程生命周期）。"""
    cap = eng._video_cap
    eng._video_cap = None
    if cap is None:
        return
    try:
        cap.release()
    except Exception:
        # R181 P2-3：句柄释放失败 = 文件句柄泄漏（下次打开同一视频可能失败）
        logger.debug("matrix: release video cap failed", exc_info=True)


def _video_worker_main(eng, my_gen: int) -> None:
    """解码线程入口（代际号启动时定格；被接替的旧线程退出时不碰新句柄）。"""
    try:
        _video_worker_run(eng)
    except Exception:
        logger.error("matrix: video decode thread crashed", exc_info=True)
    finally:
        if my_gen == eng._video_worker_gen:
            _close_video_handle(eng)       # 正常停机：worker 自关句柄


def _video_worker_run(eng) -> None:
    """解码线程主循环：命令 + 外部帧 + 推进请求（重计算全在锁外）。"""
    while not eng._video_worker_stop.is_set():
        eng._video_wake.wait(_VIDEO_IDLE_POLL)
        eng._video_wake.clear()
        _drain_video_commands(eng)
        _drain_external_frame(eng)
        _drain_video_advance(eng)


def _seek_video_frame_body(eng, frame_idx) -> bool:
    """seek 主体（worker 命令/无 worker 内联共用；原 seek_video_frame 逐行搬移）。"""
    if not eng._ensure_video_capture():
        return False
    cv2 = eng._get_cv2()
    if cv2 is None or eng._video_cap is None:
        return False
    total = eng._video_total_frames
    if total > 0:
        frame_idx = max(0, min(int(frame_idx), total - 1))
    else:
        frame_idx = max(0, int(frame_idx))
    try:
        eng._video_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame = eng._video_cap.read()
    except Exception:
        ok, frame = False, None
    if not ok or frame is None:
        return False
    eng._video_current_frame = frame_idx
    eng._frame_to_video_maps(frame)       # 发布：锁外重算、锁内写回三元组
    # read() 后 OpenCV 的内部指针已在下一帧；实时播放从拖动位置之后继续。
    return True


def _refresh_video_maps_body(eng, reset_history: bool) -> bool:
    """缓存帧→maps 重算主体（matrix_video_cache/tick 快照回退共用）。"""
    with eng._video_decode_lock:
        frame = eng._video_frame_cache
    if frame is None:
        return False
    if reset_history:
        eng._video_preview_grid = None    # 收编旧直写点（motion 历史一并清）
        eng._video_prev_grid = None
    eng._frame_to_video_maps(frame)
    return True


class MatrixPixelEngine:
    """
    矩阵像素效果引擎

    使用方式：
        engine = MatrixPixelEngine()
        engine.set_fixtures(fixtures)   # 设置设备列表（含位置）
        engine.set_mode(MODE_AUDIO_FFT) # 选择效果模式
        engine.start()                  # 开始运行
        states = engine.tick(dt)        # 每帧调用，返回 {fid: bool}
        engine.stop()                   # 停止
    """

    def __init__(self):
        self._fixtures: List[Dict] = []
        self._grid: Optional[MatrixGrid] = None
        self._sorted_ids: List[str] = []
        self._sorted_ids_array: np.ndarray = np.asarray([], dtype=object)

        self._mode: str = MODE_EFFECT_RIPPLE
        self._running: bool = False

        # 子系统
        self._audio = AudioCapture()
        self._screen = ScreenSampler()

        # 效果参数（通用）
        self.threshold: float = 0.5        # ON/OFF 阈值 [0, 1]
        self.speed: float = 1.0            # 效果速度倍率
        self.invert: bool = False          # 反转 ON/OFF

        # 翼/块参数（后处理映射）
        self.wings: int = 1       # 翼数（1~8），N 个完全一致的同向波形复制
        self.block_size: int = 1  # 块大小（1~16），N个设备共享同一权重
        self.wing_direction: str = WING_DIRECTION_LOW_TO_HIGH
        self.wing_mirror = False
        self.wing_reverse = False

        # 音频/自走双模式参数
        # True 时优先使用 AudioAnalyzer/AudioCapture；False 或无音频输入时由效果内部生成自走包络。
        self.pixel_audio_reactive: bool = True
        initialize_audio_controls(self)  # Missing saved version still restores v0.
        self.matrix_audio_mod_target: str = "brightness_speed"  # off/brightness/speed/brightness_speed/density/all
        self.matrix_audio_mod_source: str = "rms"               # rms/bass/mid/treble/beat
        self.matrix_audio_mod_amount: float = 0.6
        self.matrix_audio_mod_floor: float = 0.15
        self.matrix_audio_mod_attack_ms: float = 35.0
        self.matrix_audio_mod_release_ms: float = 220.0

        # 对称波动类效果的全局造型参数（由像素参数面板控制）
        self.matrix_wave_density: float = 1.0      # 波纹密度/重复次数
        self.matrix_wave_width: float = 0.12       # 波纹宽度/柔硬边
        self.matrix_wave_twist: float = 0.0        # 旋转、扭曲和相位偏转
        self.matrix_symmetry_mix: float = 0.5      # 菱形/圆形、轴线/径向的混合权重

        # 音频参数
        self.audio_amplitude: float = 2.0  # 振幅灵敏度
        self.audio_decay: float = 0.8      # 衰减率（低通滤波）
        self.audio_norm_offset: float = 0.0
        self.audio_device: int = -1        # -1 = 默认设备
        self.audio_bar_direction: str = "bottom"  # bottom/top/left/right
        self.audio_bar_style: str = "solid"        # solid/soft/dots/center/scan
        self.audio_band_layout: str = "linear"     # linear/reverse/center_low/edge_low/stair/random
        self.audio_bar_gamma: float = 0.85
        self.audio_bar_softness: float = 0.08
        self.audio_bar_smoothing: float = 0.35
        self.audio_bar_motion: float = 1.0
        self.audio_peak_hold: float = 0.45
        self.audio_peak_decay: float = 0.035
        self.matrix_effect_color: Tuple[int, int, int] = (255, 255, 255)

        # 屏幕采样参数
        self.screen_region: Optional[Dict] = None  # None = 全屏
        self.screen_monitor: int = 1
        self.screen_brightness_boost: float = 1.0
        self.screen_contrast: float = 1.0
        # 屏幕采样扩展参数（新增）
        self.screen_channel: str = "luminance"   # 采样通道：luminance/red/green/blue/saturation/motion/hue
        self.screen_smooth: float = 0.6           # 时间平滑系数 [0=无平滑, 0.98=极强平滑]
        self.screen_gamma: float = 1.0            # Gamma 校正
        self.screen_threshold_low: float = 0.0   # 输入阈值下限
        self.screen_threshold_high: float = 1.0  # 输入阈值上限
        self.screen_fps: float = 25.0             # 采样帧率
        self._screen_engine_started: bool = False  # 后台采样引擎是否已启动

        # 视频采样参数/状态（OpenCV 按帧读取，播放即输出）
        self.video_path: str = ""
        self.video_playing: bool = False
        self._manual_slice_tick: bool = False
        self._video_cap = None
        self._video_total_frames: int = 0
        self._video_current_frame: int = 0
        self._video_fps: float = 25.0
        self._video_frame_cache: Optional[np.ndarray] = None  # 当前 BGR 帧
        self._video_preview_raw: Optional[np.ndarray] = None  # 当前 RGB 预览帧
        self._video_preview_grid: Optional[np.ndarray] = None # 当前亮度网格
        self._video_prev_grid: Optional[np.ndarray] = None    # motion 通道缓存
        #  深查②三#5/#2/#3/#11：摄像头/变形/掩码/走带 + 分区 gamma 输出状态
        self._init_video_ext_state()

        # 动态效果内部状态
        self._t: float = 0.0              # 时间累积
        self._effect_state: Dict = {}     # 效果专用状态

        # 缓存的 FFT 数据（用于平滑）
        self._fft_cache: np.ndarray = np.zeros(512)

        #  时间线 AudioAnalyzer 注入（优先于本地 AudioCapture）
        # 通过 set_audio_analyzer() 注入后，音频效果模式将使用
        # 时间线参考轨的音频而非麦克风/音频设备输入。
        self._analyzer = None         # Optional[AudioAnalyzer]
        self._last_audio_frame = None # Optional[AudioFrame]（最新分析帧缓存）

    def _init_video_ext_state(self) -> None:
        """视频扩展运行状态（构造期调用一次；helper 压 __init__ 行数）。"""
        #  深查②三#5 P1 摄像头直通（cv2.VideoCapture；热插拔守卫在读取处）
        self.video_source: str = "file"     # "file" | "camera"
        self.camera_device: int = 0
        self._camera_fail_count: int = 0
        #  深查②三#2 P0 四角透视变形（归一化角点 (4,2) 或 None=不变形）
        self.video_warp_corners = None
        #  深查②三#3 P0 图片掩码（float32 (H,W) 0..1，采样输出逐帧 AND）
        self.sample_mask: Optional[np.ndarray] = None
        self.sample_mask_path: str = ""
        #  深查②三#11 P2 视频走带：入出点 / 方向 / 速率
        self.video_in_point: int = 0        # 0 = 开头
        self.video_out_point: int = 0       # 0 = 末尾
        self.video_direction: str = "forward"  # forward/reverse/pingpong
        self.video_rate: float = 1.0
        self._video_play_dir: int = 1
        self._video_rate_acc: float = 0.0
        #  深查②三#8 P2 每分区输出 gamma（值数组链；None/空 = 不校正）
        self._output_gamma_cfg = None
        self._output_gamma_luts = []
        self._output_gamma_zone_arr: Optional[np.ndarray] = None
        self._output_gamma_fid_zone: Dict[str, int] = {}
        # R194/P1-8+HF-02 视频解码后台线程状态（共享三元组 + 推进请求协议）
        self._video_decode_lock = threading.Lock()
        self._video_frame_ready = threading.Condition(self._video_decode_lock)
        self._video_req_seq = 0            # GUI tick 已发的推进请求数
        self._video_done_seq = 0           # 解码线程已回执的请求数（等帧就绪谓词）
        self._video_pending_reqs = 0       # 待消费请求（worker 单唤醒批量消费）
        self._video_frame_seq = 0          # 共享三元组代际号（发布即递增）
        self._video_ext_frame = None       # 外部源原始帧槽（latest-wins，锁内读写）
        self._video_cmd_queue: "queue.Queue" = queue.Queue()
        self._video_wake = threading.Event()
        self._video_worker_stop = threading.Event()
        self._video_thread = None
        self._video_zombies: list = []     # join 超时挂账；start 排空（R193 模板）
        self._video_worker_gen = 0         # 线程代际号（被接替的旧线程不碰新句柄）

    # ── 分区输出 Gamma（ 深查②三#8 P2；enabled=False = 逐字节旧行为） ────────

    def set_output_gamma(self, cfg) -> None:
        """设置分区 gamma 配置（任意 dict；内部规整，None/关闭=不校正）。"""
        from .matrix_output_lut import normalize_zone_gamma_dict
        self._output_gamma_cfg = normalize_zone_gamma_dict(cfg)
        self._rebuild_output_gamma_zone()

    def _rebuild_output_gamma_zone(self) -> None:
        """按当前设备 pos_y 重建分区号缓存（设备/配置变化时调用）。"""
        from .matrix_output_lut import build_zone_luts, zone_assignment
        cfg = self._output_gamma_cfg
        enabled = bool(cfg and cfg.get("enabled"))
        self._output_gamma_luts = build_zone_luts(cfg["gammas"]) if enabled else []
        self._output_gamma_fid_zone = {}
        self._output_gamma_zone_arr = None
        if not enabled:
            return
        zone_arr, fid_zone = zone_assignment(
            self._fixtures, int(cfg.get("zone_count", 2)))
        self._output_gamma_zone_arr = zone_arr
        self._output_gamma_fid_zone = fid_zone

    def apply_output_gamma_values(self, values: np.ndarray) -> np.ndarray:
        """uint8 值数组按分区查表（未启用/形状不符/异常 → 原样返回）。"""
        luts = self._output_gamma_luts
        zarr = self._output_gamma_zone_arr
        if not luts or zarr is None or values is None:
            return values
        try:
            from .matrix_output_lut import apply_zone_gamma_values
            return apply_zone_gamma_values(values, zarr, luts)
        except Exception:
            logger.debug("matrix: zone gamma value apply failed", exc_info=True)
            return values

    def apply_output_gamma_weights(self, weights: Dict[str, float]) -> Dict[str, float]:
        """预览权重 dict 按分区查表（与值数组同曲线）。"""
        luts = self._output_gamma_luts
        if not luts or not weights:
            return weights
        try:
            from .matrix_output_lut import apply_zone_gamma_weights
            return apply_zone_gamma_weights(
                weights, self._output_gamma_fid_zone, luts)
        except Exception:
            logger.debug("matrix: zone gamma weight apply failed", exc_info=True)
            return weights

    def _ensure_shared_audio_analyzer(self) -> bool:
        """Use the global AudioAnalyzer so Programmer and Matrix share one FFT."""
        if self._analyzer is not None:
            return True
        try:
            from ..audio_driver import _ensure_valid_analyzer
            analyzer = _ensure_valid_analyzer()
        except Exception:
            analyzer = None
        if analyzer is None:
            return False
        try:
            self.set_audio_analyzer(analyzer)
            return True
        except Exception:
            return False

    def audio_source_active(self) -> bool:
        """True only when audio-reactive modes have a real audio source.

         R131 修复（非音频像素效果在大工程里被"模拟音频"再次掐黑）：原先只判
        "分析器存在且出过帧"，**不看帧的 `has_audio`** —— 而 `AudioFrame.has_audio`
        正是本项目为此设的契约（`audio_driver._simulate_frame` 明确 `has_audio=False`，
        `effect_browser` 也已按它判"无音频"）。后果：音频来源选「模拟」时分析器
        40Hz 持续出帧 → 本函数恒 True → `is_audio_reactive_active()` 的门允许通过，
        但叠加的音频调制（默认 target=brightness_speed/amount=0.6/floor=0.15）把
        权重一律乘 0.49 → 恰低于 `threshold=0.5` → **所有非音频效果熄灭**。
        修：有帧时以帧的 `has_audio` 为准（缺该属性才退回旧行为，兼容老分析器）。
        """
        frame = self._last_audio_frame
        if self._analyzer is not None and frame is not None:
            return bool(getattr(frame, "has_audio", True))
        audio = getattr(self, "_audio", None)
        if audio is None or not bool(getattr(audio, "_running", False)):
            return False
        #  本地 AudioCapture 路径：**运行中但无信号**（模拟/静音）不算"有源"。
        #   否则音频调制会在无信号时把权重压到阈值以下 → 非音频效果整片熄灭。
        try:
            return float(audio.get_rms()) > 1e-4
        except Exception:
            return True

    # ── 设备管理 ──────────────────────────────────────────────────────────────

    def set_fixtures(self, fixtures: List[Dict]):
        from .matrix_video_cache import refresh_video_sample
        self._fixtures = list(fixtures)
        self._sorted_ids = [f["id"] for f in self._fixtures if "id" in f]
        self._sorted_ids_array = np.asarray([str(fid) for fid in self._sorted_ids], dtype=object)
        #  性能+正确性：翼/块分组缓存按 (n,wings,block,direction) 键——设备
        #   顺序变化而数量不变时必须失效，否则旧索引映射到新序设备（审查发现）。
        try:
            self._wing_block_cache = {}
        except Exception:
            pass
        self._grid = MatrixGrid(self._fixtures,
            wings=max(1, int(getattr(self, "wings", 1) or 1)),
            block_size=max(1, int(getattr(self, "block_size", 1) or 1)))
        self._rebuild_output_gamma_zone()   # 设备坐标变化 → 分区映射失效
        self._effect_state = {}  # 重置效果状态
        refresh_video_sample(self)

    def refresh_grid_layout(self):
        from .matrix_video_cache import refresh_video_sample
        if not self._fixtures:
            self._grid = MatrixGrid([])
        else:
            self._grid = MatrixGrid(self._fixtures,
                wings=max(1, int(getattr(self, "wings", 1) or 1)),
                block_size=max(1, int(getattr(self, "block_size", 1) or 1)))
        refresh_video_sample(self)

    # ── 时间线音频注入 ────────────────────────────────────────────────────────

    def set_audio_analyzer(self, analyzer):
        """注入来自时间线参考轨的 AudioAnalyzer，取代本地 AudioCapture。

        analyzer 为 None 时回退到 AudioCapture（麦克风/设备模式）。
        注入后，所有音频效果模式（audio_fft / audio_waveform / audio_energy）
        将读取分析器的 frame_ready 帧，而非麦克风输入。
        """
        # 断开旧连接
        if self._analyzer is not None:
            try:
                self._analyzer.frame_ready.disconnect(self._on_audio_frame)
            except Exception:
                pass
        self._analyzer = analyzer
        self._last_audio_frame = None
        if analyzer is not None:
            try:
                analyzer.frame_ready.connect(self._on_audio_frame)
            except Exception:
                pass

    def _on_audio_frame(self, frame):
        """缓存 AudioAnalyzer 最新帧，供音频效果计算使用。"""
        self._last_audio_frame = frame
        from .matrix_pixel_music_events import capture_music_frame
        capture_music_frame(self, frame)

    def _all_audio_modes(self) -> tuple:
        """**必须真读音频**才"不出光"的模式集合（内置的 `audio_*` 系）。

         R131 修复（1485 个像素效果点了全黑）：原先这里**并入
        `get_all_ext_modes()`**（= `_EXT_COMPUTE_MAP` 的全部 1615 个扩展模式）。
        后果：`is_audio_reactive_active()` 对**所有**扩展模式恒真 → `tick_values`
        的第一道门 `if is_audio_reactive_active() and not audio_source_active():
        return 全 0` 直接吞掉输出。用户点 `matrix_sw_diamond_rings` /
        `matrix_rapid_fire_v29` 这类**名字都不含 audio** 的空间/图形波，看到的
        是**全黑**且没有任何提示（实测：扩展模式 1615/1615 全被判音频，无音频时
        非零 0/6）。

        判据修正：扩展模式自带**自走包络兜底**（`matrix_pixel_engine_ext.py` 的
        `_sym_audio_env_v35`：无音频输入时改用 `t` 驱动的合成包络），因此它们
        **不需要**"音频源活跃"就能出光 —— 正确语义是"跟着音频走，没音频也自走"。
        只有内置 `audio_*` 系（MODE_AUDIO_*，它们直接读 FFT/能量，无音频即无意义）
        才该被门拦住。字符串启发式（`bass/vocal/spectral/metered` 等）保留：那批
        名字本身就表达"必须音频"。
        """
        cached = getattr(self, "_audio_modes_cache", None)
        if cached is not None:
            return cached
        modes = (
            MODE_AUDIO_FFT, MODE_AUDIO_WAVEFORM, MODE_AUDIO_ENERGY,
            MODE_AUDIO_BEAT_PULSE, MODE_AUDIO_BAND_MATRIX,
            MODE_AUDIO_BAND_REVERSE, MODE_AUDIO_BAND_CENTER, MODE_AUDIO_BAND_DOTS,
            MODE_AUDIO_BASS_WAVE, MODE_AUDIO_REACTIVE,
            MODE_AUDIO_SPECTRUM_RING,
            MODE_AUDIO_WAVE_FIRE, MODE_AUDIO_RAIN_DROPS,
            MODE_AUDIO_RIPPLE_POND, MODE_AUDIO_FLAME_WALL,
            MODE_AUDIO_WATERFALL, MODE_AUDIO_HEARTBEAT_SYNC,
            MODE_AUDIO_STROBE_MULTI, MODE_AUDIO_SPIRAL_PULSE,
            MODE_AUDIO_DIAMOND_WAVE, MODE_AUDIO_VU_METER,
            MODE_AUDIO_EQ_BARS, MODE_AUDIO_EQ_SOFT, MODE_AUDIO_EQ_DOTS,
            MODE_AUDIO_EQ_CENTER, MODE_AUDIO_EQ_SCAN, MODE_AUDIO_WAVEFORM_MIRROR,
            MODE_AUDIO_PARTICLE_BURST, MODE_AUDIO_GRAVITY_DROPS,
            MODE_AUDIO_CROSS_WAVE, MODE_AUDIO_CONSTELLATION,
            MODE_AUDIO_DNA_HELIX,
        )
        #  R131：**刻意不并入**扩展模式（它们有自走包络兜底，见 docstring）
        self._audio_modes_cache = modes
        return modes

    def is_audio_mode(self) -> bool:
        mode = str(getattr(self, "_mode", "") or "")
        return self._is_audio_mode_value(mode)

    def _is_audio_mode_value(self, mode: str) -> bool:
        mode = str(mode or "")
        if mode.startswith("matrix_v38_audio_bar_") or mode.startswith("matrix_v40_audio_bar_"):
            return True
        if mode in self._all_audio_modes():
            return True
        return (
            mode.startswith("audio_") or
            "_audio" in mode or
            mode.startswith("matrix_audio") or
            "spectral" in mode or
            "bass" in mode or
            "vocal" in mode or
            "echo" in mode or
            "metered" in mode
        )

    def _is_source_audio_mode_value(self, mode: str) -> bool:
        """Audio modes replace the base pattern; modulation only shapes non-source effects."""
        return self._is_audio_mode_value(mode)

    def is_audio_reactive_active(self) -> bool:
        return self.is_audio_mode() and bool(getattr(self, "pixel_audio_reactive", True))

    def _mode_resource_kind(self, mode: Optional[str] = None) -> str:
        mode = str(self._mode if mode is None else mode or "")
        if mode == MODE_SCREEN_SAMPLE:
            return "screen"
        if mode == MODE_VIDEO_SAMPLE:
            return "video"
        if self._is_audio_mode_value(mode) and bool(getattr(self, "pixel_audio_reactive", True)):
            return "audio"
        return "effect"

    def _needs_audio_mod_resource(self, mode: Optional[str] = None) -> bool:
        mode = str(self._mode if mode is None else mode or "")
        return (
            bool(getattr(self, "pixel_audio_reactive", True))
            and not self._is_source_audio_mode_value(mode)
            and str(getattr(self, "matrix_audio_mod_target", "off") or "off").strip().lower()
            not in {"", "off", "none", "关闭"}
        )

    def manual_page_count(self) -> int:
        n = len(getattr(self, "_sorted_ids", []) or [])
        if n <= 0:
            return 1
        wings = max(1, min(int(getattr(self, "wings", 1) or 1), n))
        block_size = max(1, int(getattr(self, "block_size", 1) or 1))
        wing_size = max(1, int(math.ceil(n / wings)))
        return max(1, int(math.ceil(wing_size / block_size)))

    # ── 模式管理 ──────────────────────────────────────────────────────────────

    def set_mode(self, mode: str):
        if mode == self._mode:
            return
        old_mode = self._mode
        old_kind = self._mode_resource_kind(old_mode)
        new_kind = self._mode_resource_kind(mode)
        was_running = bool(getattr(self, "_running", False))
        if old_kind != new_kind:
            self._pause_mode_resources(old_mode)
        self._mode = mode
        self._effect_state = {}
        self._t = 0.0
        if was_running:
            self._start_mode_resources(reset_audio_frame=True)

    @property
    def mode(self) -> str:
        return self._mode

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    def start(self, reset_state: bool = True):
        self._running = True
        if reset_state:
            self._t = 0.0
            self._effect_state = {}
        self._start_mode_resources(reset_audio_frame=reset_state)

    def pause(self):
        self._running = False
        if self._mode == MODE_VIDEO_SAMPLE:
            self.video_playing = False

    def refresh_runtime_resources(self):
        from .matrix_video_cache import refresh_pixel_resources
        refresh_pixel_resources(self)

    def stop(self, full_release: bool = True):
        self.pause()
        from .matrix_pixel_music_events import clear_music_events
        clear_music_events(self)
        if full_release:
            # 仅停止本地 AudioCapture；AudioAnalyzer 由外部（时间线）生命周期管理
            self._audio.stop()
            self._stop_screen_engine()
            self._release_video_capture()

    def _start_mode_resources(self, reset_audio_frame: bool = False):
        kind = self._mode_resource_kind()
        if kind == "audio":
            self._ensure_shared_audio_analyzer()
            if self._analyzer is not None:
                # 确保 analyzer 已启动（文件模式下可能未 start）
                try:
                    if not self._analyzer._running:
                        self._analyzer.start()
                except Exception:
                    pass
            else:
                self._audio.start(self.audio_device)
            if reset_audio_frame:
                self._last_audio_frame = None
        elif kind == "screen":
            self._start_screen_engine()
        elif kind == "video":
            self.video_playing = True
        if self._needs_audio_mod_resource():
            try:
                self._ensure_shared_audio_analyzer()
                if self._analyzer is not None:
                    if not self._analyzer._running:
                        self._analyzer.start()
                else:
                    self._audio.start(self.audio_device)
            except Exception:
                pass
            self._ensure_video_capture()

    def _pause_mode_resources(self, mode: Optional[str] = None):
        kind = self._mode_resource_kind(mode)
        if kind == "screen":
            self._stop_screen_engine()
        elif kind == "video":
            self.video_playing = False

    def _start_screen_engine(self):
        """(内部) 启动屏幕采样后台引擎，并同步当前参数。"""
        eng = self._screen.engine
        if eng is None:
            return
        # 同步参数
        eng.set_region(self.screen_region)
        eng.set_monitor(self.screen_monitor)
        if self._grid is not None:
            eng.set_grid(max(1, self._grid.cols), max(1, self._grid.rows))
        eng.set_channel(self.screen_channel)
        eng.set_fps(self.screen_fps)
        eng.set_params(
            brightness=self.screen_brightness_boost,
            contrast=self.screen_contrast,
            gamma=self.screen_gamma,
            smooth=self.screen_smooth,
            threshold_low=self.screen_threshold_low,
            threshold_high=self.screen_threshold_high,
        )
        if not eng.is_running():
            eng.start()
        self._screen_engine_started = True

    def _stop_screen_engine(self):
        """(内部) 停止屏幕采样后台引擎。"""
        eng = self._screen.engine
        if eng is not None and self._screen_engine_started:
            eng.stop()
            self._screen_engine_started = False

    def _sync_screen_params(self):
        """(内部) 将当前屏幕采样参数同步到后台引擎（实时生效）。"""
        eng = self._screen.engine
        if eng is None:
            return
        eng.set_region(self.screen_region)
        eng.set_monitor(self.screen_monitor)
        if self._grid is not None:
            eng.set_grid(max(1, self._grid.cols), max(1, self._grid.rows))
        eng.set_channel(self.screen_channel)
        eng.set_fps(self.screen_fps)
        eng.set_params(
            brightness=self.screen_brightness_boost,
            contrast=self.screen_contrast,
            gamma=self.screen_gamma,
            smooth=self.screen_smooth,
            threshold_low=self.screen_threshold_low,
            threshold_high=self.screen_threshold_high,
        )

    # ── 主 tick ───────────────────────────────────────────────────────────────

    def tick(self, dt: float) -> Dict[str, bool]:
        """
        每帧调用，返回 {fixture_id: bool} 的 ON/OFF 状态字典
        dt: 距上次调用的秒数
        """
        if not self._running:
            return {}
        # 视频采样模式：即使 _fixtures 为空也要推进帧（保持视频解码连续性）
        if not self._fixtures and self._mode != MODE_VIDEO_SAMPLE:
            return {}

        if self.is_audio_reactive_active() and not self.audio_source_active():
            return {fid: False for fid in self._sorted_ids}

        self._prepare_audio_mod_frame(dt)
        self._t += dt * self.speed * self._audio_mod_time_scale()

        if self._mode == MODE_AUDIO_FFT:
            weights = self._compute_audio_fft()
        elif self._mode == MODE_AUDIO_WAVEFORM:
            weights = self._compute_audio_waveform()
        elif self._mode == MODE_AUDIO_ENERGY:
            weights = self._compute_audio_energy()
        elif self._mode == MODE_SCREEN_SAMPLE:
            weights = self._compute_screen_sample()
        elif self._mode == MODE_VIDEO_SAMPLE:
            weights = self._compute_video_sample()
        elif self._mode == MODE_EFFECT_RIPPLE:
            weights = self._compute_ripple()
        elif self._mode == MODE_EFFECT_CHASE:
            weights = self._compute_chase()
        elif self._mode == MODE_EFFECT_SPIRAL:
            weights = self._compute_spiral()
        elif self._mode == MODE_EFFECT_HEARTBEAT:
            weights = self._compute_heartbeat()
        elif self._mode == MODE_EFFECT_EXPLODE:
            weights = self._compute_explode()
        elif self._mode == MODE_EFFECT_RAIN:
            weights = self._compute_rain()
        elif self._mode == MODE_EFFECT_SNAKE:
            weights = self._compute_snake()
        elif self._mode == MODE_EFFECT_RANDOM:
            weights = self._compute_random()
        elif self._mode == MODE_EFFECT_FIRE:
            weights = self._compute_fire()
        elif self._mode == MODE_EFFECT_WAVE:
            weights = self._compute_wave()
        elif self._mode == MODE_AUDIO_BEAT_PULSE:
            weights = self._compute_audio_beat_pulse()
        elif self._mode in {MODE_AUDIO_BAND_MATRIX, MODE_AUDIO_BAND_REVERSE,
                            MODE_AUDIO_BAND_CENTER, MODE_AUDIO_BAND_DOTS}:
            weights = self._compute_audio_band_matrix()
        elif self._mode == MODE_AUDIO_BASS_WAVE:
            weights = self._compute_audio_bass_wave()
        elif self._mode == MODE_AUDIO_REACTIVE:
            weights = self._compute_audio_reactive()
        elif self._mode == MODE_AUDIO_SPECTRUM_RING:
            weights = self._compute_audio_spectrum_ring()
        elif self._mode == MODE_EFFECT_PLASMA:
            weights = self._compute_plasma()
        elif self._mode == MODE_EFFECT_CONWAY:
            weights = self._compute_conway()
        elif self._mode == MODE_EFFECT_METEOR:
            weights = self._compute_meteor()
        elif self._mode == MODE_EFFECT_SCAN:
            weights = self._compute_scan()
        elif self._mode == MODE_EFFECT_BREATHE:
            weights = self._compute_breathe()
        elif self._mode == MODE_EFFECT_FIREWORK:
            weights = self._compute_firework()
        elif self._mode == MODE_EFFECT_TETRIS:
            weights = self._compute_tetris()
        # ── v26 新增音频响应效果 ──
        elif self._mode == MODE_AUDIO_WAVE_FIRE:
            weights = self._compute_audio_wave_fire()
        elif self._mode == MODE_AUDIO_RAIN_DROPS:
            weights = self._compute_audio_rain_drops()
        elif self._mode == MODE_AUDIO_RIPPLE_POND:
            weights = self._compute_audio_ripple_pond()
        elif self._mode == MODE_AUDIO_FLAME_WALL:
            weights = self._compute_audio_flame_wall()
        elif self._mode == MODE_AUDIO_WATERFALL:
            weights = self._compute_audio_waterfall()
        elif self._mode == MODE_AUDIO_HEARTBEAT_SYNC:
            weights = self._compute_audio_heartbeat_sync()
        elif self._mode == MODE_AUDIO_STROBE_MULTI:
            weights = self._compute_audio_strobe_multi()
        elif self._mode == MODE_AUDIO_SPIRAL_PULSE:
            weights = self._compute_audio_spiral_pulse()
        elif self._mode == MODE_AUDIO_DIAMOND_WAVE:
            weights = self._compute_audio_diamond_wave()
        elif self._mode == MODE_AUDIO_VU_METER:
            weights = self._compute_audio_vu_meter()
        elif self._mode in {MODE_AUDIO_EQ_BARS, MODE_AUDIO_EQ_SOFT, MODE_AUDIO_EQ_DOTS,
                            MODE_AUDIO_EQ_CENTER, MODE_AUDIO_EQ_SCAN}:
            weights = self._compute_audio_eq_bars()
        elif self._mode == MODE_AUDIO_WAVEFORM_MIRROR:
            weights = self._compute_audio_waveform_mirror()
        elif self._mode == MODE_AUDIO_PARTICLE_BURST:
            weights = self._compute_audio_particle_burst()
        elif self._mode == MODE_AUDIO_GRAVITY_DROPS:
            weights = self._compute_audio_gravity_drops()
        elif self._mode == MODE_AUDIO_CROSS_WAVE:
            weights = self._compute_audio_cross_wave()
        elif self._mode == MODE_AUDIO_CONSTELLATION:
            weights = self._compute_audio_constellation()
        elif self._mode == MODE_AUDIO_DNA_HELIX:
            weights = self._compute_audio_dna_helix()
        else:
            # ── 尝试扩展效果模式（v27-v32移植 + 编舞引擎混合）──────────────
            try:
                from .matrix_pixel_engine_ext import _EXT_COMPUTE_MAP
                if self._mode in _EXT_COMPUTE_MAP:
                    weights = _EXT_COMPUTE_MAP[self._mode](self)
                else:
                    weights = {fid: 0.0 for fid in self._sorted_ids}
            except ImportError:
                weights = {fid: 0.0 for fid in self._sorted_ids}

        # 翼/块后处理映射（在阈值转换前应用）
        if self.wings > 1 or self.block_size > 1 or self.wing_mirror or self.wing_reverse:
            weights = self._apply_wings_blocks(weights)
        weights = self._apply_audio_modulation_dict(weights)

        # 应用阈值，转换为 ON/OFF
        result = {}
        for fid, w in weights.items():
            on = w >= self.threshold
            result[fid] = (not on) if self.invert else on
        return result

    def tick_values(self, dt: float) -> Optional[np.ndarray]:
        """Fast path for realtime output: return uint8 values in fixture order.

        The existing tick() API stays authoritative for unsupported modes. This
        method only handles modes where the array calculation matches the old
        per-fixture dictionary logic.
        """
        if not self._running:
            return np.zeros(len(self._sorted_ids), dtype=np.uint8)
        if not self._fixtures and self._mode != MODE_VIDEO_SAMPLE:
            return np.zeros(0, dtype=np.uint8)
        if self.is_audio_reactive_active() and not self.audio_source_active():
            return np.zeros(len(self._sorted_ids), dtype=np.uint8)

        previous_frame = prepare_array_frame(self, dt)
        weights = self._compute_weights_array_fast()
        if weights is None:
            restore_array_frame(self, previous_frame)
            return None
        from .matrix_pixel_output_sample import pixel_output_values
        return pixel_output_values(self, weights)

    def weights_to_dict(self, values: np.ndarray) -> Dict[str, float]:
        ids = getattr(self, "_sorted_ids", []) or []
        if values is None or len(ids) == 0:
            return {}
        vals = np.asarray(values, dtype=np.float32)
        if vals.size != len(ids):
            vals = np.resize(vals, len(ids))
        return {fid: float(vals[i]) / 255.0 for i, fid in enumerate(ids)}

    def states_to_dict(self, values: np.ndarray) -> Dict[str, bool]:
        ids = getattr(self, "_sorted_ids", []) or []
        if values is None or len(ids) == 0:
            return {}
        vals = np.asarray(values)
        if vals.size != len(ids):
            vals = np.resize(vals, len(ids))
        return {fid: bool(vals[i] >= 128) for i, fid in enumerate(ids)}

    def _apply_wings_blocks_array(self, weights: np.ndarray) -> np.ndarray:
        vals = np.asarray(weights, dtype=np.float32)
        n = int(vals.size)
        if n <= 0:
            return vals
        wings = max(1, int(getattr(self, "wings", 1) or 1))
        block_size = max(1, int(getattr(self, "block_size", 1) or 1))
        mirror = bool(getattr(self, "wing_mirror", False))
        reverse = bool(getattr(self, "wing_reverse", False))
        if wings <= 1 and block_size <= 1 and not mirror and not reverse:
            return vals
        direction = getattr(self, "wing_direction", WING_DIRECTION_LOW_TO_HIGH)
        key = (n, wings, block_size, str(direction), mirror, reverse)
        cache = getattr(self, "_wing_block_cache", None)
        if cache is None:
            cache = self._wing_block_cache = {}
        mapping = cache.get(key)
        if mapping is None:
            indices = list(range(n))
            sources = build_wing_block_units(indices, wings, block_size, direction)
            targets = build_wing_block_units(indices, wings, block_size, direction,
                                             mirror=mirror, reverse=reverse)
            mapping = [(np.asarray(src, dtype=np.int32), np.asarray(dst, dtype=np.int32))
                       for src, dst in zip(sources, targets)]
            if len(cache) >= 32:
                cache.pop(next(iter(cache)))
            cache[key] = mapping
        out = vals.copy()
        for source, target in mapping:
            out[target] = float(vals[source].max())
        return out

    def _audio_mod_enabled(self) -> bool:
        target = str(getattr(self, "matrix_audio_mod_target", "off") or "off").strip().lower()
        amount = abs(float(getattr(self, "matrix_audio_mod_amount", 0.0) or 0.0))
        return (
            bool(getattr(self, "pixel_audio_reactive", True))
            and amount > 1e-6
            and target not in {"", "off", "none", "关闭"}
            and not self._is_source_audio_mode_value(str(getattr(self, "_mode", "") or ""))
            #  R131 修复（非音频模式"点了全黑"的第二道门）：没有音频输入时必须
            #   **完全透明**。调制是"跟着音频起伏"，无音频就无可跟随 —— 而默认参数
            #   （target=brightness_speed / amount=0.6 / floor=0.15）在 env≈0 时仍算出
            #   `brightness = 0.4 + 0.6*0.15 = 0.49`，把权重一律乘 0.49 → 恰在
            #   `threshold=0.5` 之下 → **所有非音频效果熄灭**（实测 raw 权重 1.0 →
            #   调制后 0.49 → tick 全 False）。这属于"亮度调制改变了通断判定"的次序
            #   错误；无信号即不参与，既修黑屏又不影响有音频时的表现。
            and self.audio_source_active()
        )

    def _prepare_audio_mod_frame(self, dt: float) -> None:
        self._effect_state["_audio_mod_dt"] = max(0.001, min(0.2, float(dt or 0.04)))
        self._effect_state.pop("_audio_mod_frame_env", None)
        if not self._audio_mod_enabled():
            self._effect_state["_audio_mod_frame_env"] = 0.0
            return
        self._effect_state["_audio_mod_frame_env"] = self._audio_mod_value()

    def _audio_mod_value(self) -> float:
        cached = self._effect_state.get("_audio_mod_frame_env", None)
        if cached is not None:
            return max(0.0, min(1.0, float(cached)))
        source = str(getattr(self, "matrix_audio_mod_source", "rms") or "rms").strip().lower()
        try:
            bands = np.asarray(self._get_audio_bands(), dtype=np.float32)
        except Exception:
            bands = np.zeros(8, dtype=np.float32)
        if bands.size == 0:
            bands = np.zeros(8, dtype=np.float32)
        try:
            rms = float(self._get_audio_rms())
        except Exception:
            rms = 0.0
        amp = float(getattr(self, "audio_amplitude", 1.0) or 1.0)
        if source == "bass":
            raw = float(np.mean(bands[:max(1, min(2, bands.size))])) * amp
        elif source == "mid":
            start = min(2, max(0, bands.size - 1))
            end = min(bands.size, max(start + 1, 5))
            raw = float(np.mean(bands[start:end])) * amp
        elif source == "treble":
            raw = float(np.mean(bands[max(0, bands.size - 2):])) * amp
        elif source == "beat":
            raw = 1.0 if self._get_is_beat() else rms * amp * 0.35
        else:
            raw = rms * amp
        raw = max(0.0, min(1.0, raw))
        state = self._effect_state
        prev = float(state.get("_audio_mod_env", 0.0) or 0.0)
        dt = max(0.001, min(0.2, float(state.get("_audio_mod_dt", 0.04) or 0.04)))
        attack = max(1.0, float(getattr(self, "matrix_audio_mod_attack_ms", 35.0) or 35.0)) / 1000.0
        release = max(1.0, float(getattr(self, "matrix_audio_mod_release_ms", 220.0) or 220.0)) / 1000.0
        coeff = math.exp(-dt / (attack if raw >= prev else release))
        env = prev * coeff + raw * (1.0 - coeff)
        state["_audio_mod_env"] = env
        return max(0.0, min(1.0, env))

    def _audio_mod_time_scale(self) -> float:
        if not self._audio_mod_enabled():
            return 1.0
        target = str(getattr(self, "matrix_audio_mod_target", "") or "").strip().lower()
        if target not in {"speed", "brightness_speed", "all", "速度", "亮度+速度", "亮度+速度+密度"}:
            return 1.0
        env = max(0.0, min(1.0, float(self._effect_state.get("_audio_mod_frame_env", 0.0) or 0.0)))
        amount = max(0.0, min(4.0, float(getattr(self, "matrix_audio_mod_amount", 0.6) or 0.0)))
        return max(0.05, 1.0 + (env - 0.35) * amount * 2.0)

    def _audio_mod_factors(self) -> tuple:
        if not self._audio_mod_enabled():
            return 0.0, 1.0, False, False
        env = self._audio_mod_value()
        amount = max(0.0, min(4.0, float(getattr(self, "matrix_audio_mod_amount", 0.6) or 0.0)))
        floor = max(0.0, min(1.0, float(getattr(self, "matrix_audio_mod_floor", 0.15) or 0.0)))
        target = str(getattr(self, "matrix_audio_mod_target", "brightness_speed") or "").strip().lower()
        brightness = (1.0 - amount) + amount * max(floor, env)
        brightness = max(0.0, min(3.0, brightness))
        add_pulse = target in {"all", "threshold", "pulse", "亮度+速度+密度"}
        spatial = target in {"density", "width", "all", "亮度+速度+密度"}
        if target in {"brightness", "亮度"}:
            spatial = False
        return env, brightness, spatial, add_pulse

    def _apply_audio_modulation_array(self, weights: np.ndarray) -> np.ndarray:
        if not self._audio_mod_enabled():
            return weights
        vals = np.asarray(weights, dtype=np.float32)
        env, brightness, spatial, add_pulse = self._audio_mod_factors()
        out = np.clip(vals * brightness, 0.0, 1.0)
        if add_pulse:
            out = np.maximum(out, np.float32(env * 0.85))
        if spatial and self._grid is not None and out.size == len(self._sorted_ids):
            density = 1.0 + env * max(0.0, float(getattr(self, "matrix_audio_mod_amount", 0.6) or 0.0)) * 2.5
            x = self._grid.norm_x_np.astype(np.float32, copy=False)
            y = self._grid.norm_y_np.astype(np.float32, copy=False)
            phase = (x + y) * np.pi * 2.0 * density - float(self._t) * (1.0 + env * 4.0)
            texture = 0.72 + 0.28 * np.sin(phase)
            out = np.clip(out * texture.astype(np.float32), 0.0, 1.0)
        return out.astype(np.float32, copy=False)

    def _apply_audio_modulation_dict(self, weights: Dict[str, float]) -> Dict[str, float]:
        if not self._audio_mod_enabled() or not weights:
            return weights
        arr = np.asarray([float(weights.get(fid, 0.0) or 0.0) for fid in self._sorted_ids], dtype=np.float32)
        arr = self._apply_audio_modulation_array(arr)
        return {fid: float(arr[i]) for i, fid in enumerate(self._sorted_ids)}

    def _compute_weights_array_fast(self) -> Optional[np.ndarray]:
        mode = self._mode
        try:
            if mode == MODE_AUDIO_ENERGY:
                return self._compute_audio_energy_array()
            if mode == MODE_AUDIO_FFT:
                return self._compute_audio_fft_array()
            if mode == MODE_AUDIO_WAVEFORM:
                return self._compute_audio_waveform_array()
            if mode in {MODE_AUDIO_BAND_MATRIX, MODE_AUDIO_BAND_REVERSE,
                        MODE_AUDIO_BAND_CENTER, MODE_AUDIO_BAND_DOTS}:
                return self._compute_audio_band_matrix_array()
            if mode == MODE_AUDIO_BASS_WAVE:
                return self._compute_audio_bass_wave_array()
            if mode == MODE_AUDIO_REACTIVE:
                return self._compute_audio_reactive_array()
            if mode == MODE_AUDIO_SPECTRUM_RING:
                return self._compute_audio_spectrum_ring_array()
            if mode == MODE_AUDIO_BEAT_PULSE:
                return self._compute_audio_beat_pulse_array()
            if mode == MODE_AUDIO_VU_METER:
                return self._compute_audio_vu_meter_array()
            if mode in {MODE_AUDIO_EQ_BARS, MODE_AUDIO_EQ_SOFT, MODE_AUDIO_EQ_DOTS,
                        MODE_AUDIO_EQ_CENTER, MODE_AUDIO_EQ_SCAN}:
                return self._compute_audio_eq_bars_array()
            if mode == MODE_AUDIO_WAVEFORM_MIRROR:
                return self._compute_audio_waveform_mirror_array()
            if mode == MODE_SCREEN_SAMPLE:
                return self._compute_screen_sample_array()
            if mode == MODE_VIDEO_SAMPLE:
                return self._compute_video_sample_array()
            if mode == MODE_EFFECT_RIPPLE:
                return self._compute_ripple_array()
            if mode == MODE_EFFECT_CHASE:
                return self._compute_chase_array()
            if mode == MODE_EFFECT_SPIRAL:
                return self._compute_spiral_array()
            if mode == MODE_EFFECT_HEARTBEAT:
                return self._compute_heartbeat_array()
            if mode == MODE_EFFECT_EXPLODE:
                return self._compute_explode_array()
            if mode == MODE_EFFECT_SNAKE:
                return self._compute_snake_array()
            if mode == MODE_EFFECT_RANDOM:
                return self._compute_random_array()
            if mode == MODE_EFFECT_FIRE:
                return self._compute_fire_array()
            if mode == MODE_EFFECT_WAVE:
                return self._compute_wave_array()
        except Exception:
            return None
        return None

    def manual_slice_step(self, slices: Optional[int] = None) -> Dict[str, bool]:
        if self.is_audio_reactive_active():
            return {}
        was_running = bool(getattr(self, "_running", False))
        self._running = True
        self._effect_state = self._effect_state or {}
        slice_count = max(1, int(slices or self.manual_page_count()))
        old_speed = float(getattr(self, "speed", 1.0) or 0.0)
        old_video_playing = bool(getattr(self, "video_playing", False))
        if self._mode == MODE_VIDEO_SAMPLE and getattr(self, "video_path", ""):
            try:
                self.seek_video_frame(self.get_video_current_frame() + 1)
            except Exception:
                pass
            self.video_playing = False
        self._manual_slice_tick = True
        self.speed = 1.0 if abs(old_speed) <= 1e-6 else old_speed
        try:
            return self.tick(1.0 / float(slice_count))
        finally:
            self._manual_slice_tick = False
            self.speed = old_speed
            if self._mode == MODE_VIDEO_SAMPLE:
                self.video_playing = old_video_playing
            self._running = was_running

    def _apply_wings_blocks(self, weights: Dict[str, float]) -> Dict[str, float]:
        """翼/块后处理：将计算出的权重按翼/块规则重新分配。

        block_size > 1：每 block_size 个设备共享同一权重（取组内最大值）。
        wings > 1：将设备列表分为 N 翼，每一翼都按同一方向复制第 0 翼模板；
                   例如 2 翼就是两段完全一致、同向展开的波形。
        """
        ids = self._sorted_ids
        n = len(ids)
        if n == 0:
            return weights

        args = (ids, max(1, int(getattr(self, "wings", 1) or 1)),
                max(1, int(getattr(self, "block_size", 1) or 1)),
                getattr(self, "wing_direction", WING_DIRECTION_LOW_TO_HIGH))
        sources = build_wing_block_units(*args)
        targets = build_wing_block_units(*args,
            mirror=bool(getattr(self, "wing_mirror", False)),
            reverse=bool(getattr(self, "wing_reverse", False)))
        result = dict(weights)
        for source, target in zip(sources, targets):
            value = max(float(weights.get(fid, 0.0) or 0.0) for fid in source)
            for fid in target:
                result[fid] = value
        return result

    # ─────────────────────────────────────────────────────────────────────────
    #  音频效果计算
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_audio_fft(self) -> Dict[str, float]:
        """音频 FFT 频谱 → 矩阵权重

        优先使用注入的 AudioAnalyzer（时间线音频，8 频段能量）；
        无 analyzer 时使用本地 AudioCapture（全 FFT）。
        """
        n = len(self._sorted_ids)
        if n == 0:
            return {}

        # ── 路径 A：使用 AudioAnalyzer 的 8 频段数据 ──────────────────────────
        if not bool(getattr(self, 'pixel_audio_reactive', True)):
            return self._compute_wave()

        if self._analyzer is not None and self._last_audio_frame is not None:
            bands = list(getattr(self._last_audio_frame, 'bands', [0.0] * 8))
            fft = np.array(bands, dtype=np.float32) * self.audio_amplitude
            if self.audio_norm_offset != 0:
                offsets = np.linspace(self.audio_norm_offset, 0, len(fft))
                fft = fft + offsets
            fft = np.clip(fft, 0, None)
            return self._fft_to_weights(fft, normalize=False)

        # ── 路径 B：使用本地 AudioCapture（麦克风/设备）──────────────────────
        fft = self._audio.get_fft()
        if fft is None or len(fft) == 0:
            return {fid: 0.0 for fid in self._sorted_ids}

        fft = fft * self.audio_amplitude
        fft = np.clip(fft, 0, None)
        if self.audio_norm_offset != 0:
            offsets = np.linspace(self.audio_norm_offset, 0, len(fft))
            fft = fft + offsets
        return self._fft_to_weights(fft)

    def _audio_fft_values(self) -> Optional[np.ndarray]:
        if not bool(getattr(self, 'pixel_audio_reactive', True)):
            return None
        if self._analyzer is not None and self._last_audio_frame is not None:
            bands = list(getattr(self._last_audio_frame, 'bands', [0.0] * 8))
            fft = np.asarray(bands, dtype=np.float32) * float(self.audio_amplitude)
        else:
            fft = self._audio.get_fft()
            if fft is None or len(fft) == 0:
                return np.zeros(len(self._sorted_ids), dtype=np.float32)
            fft = np.asarray(fft, dtype=np.float32) * float(self.audio_amplitude)
        if self.audio_norm_offset != 0:
            fft = fft + np.linspace(float(self.audio_norm_offset), 0.0, len(fft), dtype=np.float32)
        return np.clip(fft, 0.0, None)

    def _audio_bar_direction(self) -> str:
        direction = str(getattr(self, "audio_bar_direction", "bottom") or "bottom").strip().lower()
        return direction if direction in {"bottom", "top", "left", "right"} else "bottom"

    def _audio_bar_style(self) -> str:
        mode = str(getattr(self, "_mode", "") or "")
        if mode in {MODE_AUDIO_EQ_SOFT}:
            return "soft"
        if mode in {MODE_AUDIO_EQ_DOTS, MODE_AUDIO_BAND_DOTS}:
            return "dots"
        if mode in {MODE_AUDIO_EQ_CENTER}:
            return "center"
        if mode in {MODE_AUDIO_EQ_SCAN}:
            return "scan"
        style = str(getattr(self, "audio_bar_style", "solid") or "solid").strip().lower()
        return style if style in {"solid", "soft", "dots", "center", "scan"} else "solid"

    def _audio_band_layout(self) -> str:
        mode = str(getattr(self, "_mode", "") or "")
        if mode == MODE_AUDIO_BAND_REVERSE:
            return "reverse"
        if mode == MODE_AUDIO_BAND_CENTER:
            return "center_low"
        layout = str(getattr(self, "audio_band_layout", "linear") or "linear").strip().lower()
        aliases = {
            "right_to_left": "reverse",
            "center": "center_low",
            "middle_low": "center_low",
            "edges_low": "edge_low",
            "edge": "edge_low",
            "shuffle": "random",
        }
        layout = aliases.get(layout, layout)
        return layout if layout in {"linear", "reverse", "center_low", "edge_low", "stair", "random"} else "linear"

    @staticmethod
    def _audio_axis_levels(values: Any, count: int, normalize: bool = False) -> np.ndarray:
        count = max(1, int(count or 1))
        arr = np.asarray(values if values is not None else [], dtype=np.float32).reshape(-1)
        if arr.size == 0:
            return np.zeros(count, dtype=np.float32)
        if arr.size >= count:
            from .matrix_pixel_audio_axis import downsample_audio_axis
            out = downsample_audio_axis(arr, count)
        elif arr.size == 1:
            out = np.full(count, float(arr[0]), dtype=np.float32)
        else:
            x_src = np.linspace(0.0, 1.0, arr.size, dtype=np.float32)
            x_dst = np.linspace(0.0, 1.0, count, dtype=np.float32)
            out = np.interp(x_dst, x_src, arr).astype(np.float32)
        if normalize:
            max_v = float(np.max(out))
            if max_v > 1e-6:
                out = out / max_v
        return np.clip(out, 0.0, 1.0).astype(np.float32, copy=False)

    def _audio_bar_levels(self, values: Any, count: int, normalize: bool = False,
                          layout: Optional[str] = None, state_key: str = "bar") -> np.ndarray:
        count = max(1, int(count or 1))
        base = self._audio_axis_levels(values, count, normalize=normalize)
        layout = layout or self._audio_band_layout()
        if count > 1 and layout != "linear":
            idx = np.arange(count, dtype=np.float32)
            if layout == "reverse":
                mapped = (count - 1 - idx).astype(np.int32)
            elif layout == "center_low":
                center = (count - 1) * 0.5
                dist = np.abs(idx - center) / max(1.0, center)
                mapped = np.minimum(count - 1, np.round(dist * (count - 1))).astype(np.int32)
            elif layout == "edge_low":
                center = (count - 1) * 0.5
                dist = np.abs(idx - center) / max(1.0, center)
                mapped = np.minimum(count - 1, np.round((1.0 - dist) * (count - 1))).astype(np.int32)
            elif layout == "stair":
                t = idx / max(1.0, float(count - 1))
                mapped = np.minimum(count - 1, np.round(np.sqrt(t) * (count - 1))).astype(np.int32)
            elif layout == "random":
                key = f"audio_bar_shuffle_{count}"
                mapped_list = self._effect_state.get(key)
                if not isinstance(mapped_list, list) or len(mapped_list) != count:
                    mapped_list = list(range(count))
                    random.shuffle(mapped_list)
                    self._effect_state[key] = mapped_list
                mapped = np.asarray(mapped_list, dtype=np.int32)
            else:
                mapped = idx.astype(np.int32)
            base = base[mapped]
        gamma = max(0.05, float(getattr(self, "audio_bar_gamma", 0.85) or 0.85))
        if abs(gamma - 1.0) > 1e-6:
            base = np.power(np.clip(base, 0.0, 1.0), gamma).astype(np.float32)
        smooth = max(0.0, min(0.95, float(getattr(self, "audio_bar_smoothing", 0.35) or 0.0)))
        key = f"audio_bar_env_{state_key}_{count}_{layout}"
        prev = self._effect_state.get(key)
        if smooth > 1e-6 and isinstance(prev, np.ndarray) and prev.shape == base.shape:
            base = (prev.astype(np.float32) * smooth + base * (1.0 - smooth)).astype(np.float32)
        self._effect_state[key] = base.copy()
        return np.clip(base, 0.0, 1.0).astype(np.float32, copy=False)

    def _audio_bar_axes_array(self, cols: int, rows: int, col: np.ndarray,
                              row: np.ndarray, direction: Optional[str] = None,
                              style: Optional[str] = None) -> tuple:
        direction = direction or self._audio_bar_direction()
        style = style or self._audio_bar_style()
        if direction in {"left", "right"}:
            axis_count = max(1, int(rows))
            axis_idx = np.minimum(row, axis_count - 1).astype(np.int32, copy=False)
            coord = col.astype(np.float32) / max(1, int(cols) - 1)
            threshold = np.abs(coord - 0.5) * 2.0 if style == "center" else (
                coord if direction == "left" else (1.0 - coord))
            tolerance = 1.0 / max(1, int(cols))
        else:
            axis_count = max(1, int(cols))
            axis_idx = np.minimum(col, axis_count - 1).astype(np.int32, copy=False)
            coord = row.astype(np.float32) / max(1, int(rows) - 1)
            threshold = np.abs(coord - 0.5) * 2.0 if style == "center" else (
                (1.0 - coord) if direction == "bottom" else coord)
            tolerance = 1.0 / max(1, int(rows))
        return axis_count, axis_idx, threshold.astype(np.float32, copy=False), float(tolerance)

    def _audio_bar_fill_from_energy(self, energy: np.ndarray, threshold: np.ndarray,
                                    axis_idx: np.ndarray, tolerance: float,
                                    style: Optional[str] = None) -> np.ndarray:
        style = style or self._audio_bar_style()
        energy = np.asarray(energy, dtype=np.float32)
        threshold = np.asarray(threshold, dtype=np.float32)
        if style in {"soft", "scan", "center"}:
            softness = max(float(tolerance), float(getattr(self, "audio_bar_softness", 0.08) or 0.0))
            out = np.clip((energy - threshold + softness) / max(1e-6, softness), 0.0, 1.0)
        else:
            out = (energy >= threshold).astype(np.float32)
        if style == "dots":
            period = max(2.0, 5.0 - float(getattr(self, "audio_bar_motion", 1.0) or 1.0))
            stripe = (np.floor(threshold / max(float(tolerance), 1e-4) / period + (axis_idx % 2)) % 2) == 0
            out = out * stripe.astype(np.float32)
        elif style == "scan":
            scan = 0.55 + 0.45 * np.sin(
                threshold * 18.0 + axis_idx.astype(np.float32) * 0.73
                - float(self._t) * float(getattr(self, "audio_bar_motion", 1.0) or 1.0) * 8.0)
            out = out * np.clip(scan, 0.0, 1.0).astype(np.float32)
        return np.clip(out, 0.0, 1.0).astype(np.float32, copy=False)

    def _audio_bar_array_from_levels(self, levels: Any, cols: int, rows: int,
                                     col: np.ndarray, row: np.ndarray,
                                     direction: Optional[str] = None,
                                     normalize: bool = False,
                                     style: Optional[str] = None,
                                     layout: Optional[str] = None,
                                     state_key: str = "bar") -> np.ndarray:
        style = style or self._audio_bar_style()
        axis_count, axis_idx, threshold, _tol = self._audio_bar_axes_array(
            cols, rows, col, row, direction, style=style)
        axis_levels = self._audio_bar_levels(
            levels, axis_count, normalize=normalize, layout=layout, state_key=state_key)
        energy = axis_levels[axis_idx]
        return self._audio_bar_fill_from_energy(energy, threshold, axis_idx, _tol, style=style)

    def _audio_bar_weight_from_levels(self, levels: Any, cols: int, rows: int,
                                      col: int, row: int,
                                      direction: Optional[str] = None,
                                      normalize: bool = False,
                                      style: Optional[str] = None,
                                      layout: Optional[str] = None,
                                      state_key: str = "bar") -> float:
        direction = direction or self._audio_bar_direction()
        style = style or self._audio_bar_style()
        if direction in {"left", "right"}:
            axis_count = max(1, int(rows))
            axis_idx = min(max(0, int(row)), axis_count - 1)
            coord = float(col) / max(1, int(cols) - 1)
            threshold = abs(coord - 0.5) * 2.0 if style == "center" else (
                coord if direction == "left" else 1.0 - coord)
            tolerance = 1.0 / max(1, int(cols))
        else:
            axis_count = max(1, int(cols))
            axis_idx = min(max(0, int(col)), axis_count - 1)
            coord = float(row) / max(1, int(rows) - 1)
            threshold = abs(coord - 0.5) * 2.0 if style == "center" else (
                1.0 - coord if direction == "bottom" else coord)
            tolerance = 1.0 / max(1, int(rows))
        axis_levels = self._audio_bar_levels(
            levels, axis_count, normalize=normalize, layout=layout, state_key=state_key)
        energy = np.asarray([float(axis_levels[axis_idx])], dtype=np.float32)
        out = self._audio_bar_fill_from_energy(
            energy, np.asarray([threshold], dtype=np.float32),
            np.asarray([axis_idx], dtype=np.int32), tolerance, style=style)
        return float(out[0])

    def _update_audio_eq_peaks(self, levels: np.ndarray) -> np.ndarray:
        levels = np.asarray(levels, dtype=np.float32)
        count = int(levels.size)
        if count <= 0:
            return np.zeros(0, dtype=np.float32)
        state = self._effect_state
        key = f"eq_peaks_{count}"
        age_key = f"eq_peak_age_{count}"
        peaks = np.asarray(state.get(key, np.zeros(count, dtype=np.float32)), dtype=np.float32)
        peak_age = np.asarray(state.get(age_key, np.zeros(count, dtype=np.float32)), dtype=np.float32)
        if peaks.size != count:
            peaks = np.zeros(count, dtype=np.float32)
            peak_age = np.zeros(count, dtype=np.float32)
        rising = levels >= peaks
        peaks[rising] = levels[rising]
        peak_age[rising] = 0.0
        peak_age[~rising] += 0.04
        hold = max(0.0, float(getattr(self, "audio_peak_hold", 0.45) or 0.0))
        decay = max(0.0, float(getattr(self, "audio_peak_decay", 0.035) or 0.0))
        falling = (~rising) & (peak_age > hold)
        peaks[falling] = np.maximum(levels[falling], peaks[falling] - decay)
        state[key] = peaks.astype(np.float32, copy=True)
        state[age_key] = peak_age.astype(np.float32, copy=True)
        return peaks.astype(np.float32, copy=False)

    def _fft_to_array(self, fft: np.ndarray, *, normalize: bool = True) -> np.ndarray:
        n = len(self._sorted_ids)
        if n <= 0:
            return np.zeros(0, dtype=np.float32)
        if fft is None or len(fft) == 0:
            return np.zeros(n, dtype=np.float32)
        fft = np.asarray(fft, dtype=np.float32)
        grid = self._grid
        if grid is None:
            idx = np.minimum((np.arange(n, dtype=np.float32) * len(fft) / max(1, n)).astype(np.int32), len(fft) - 1)
            levels = self._audio_axis_levels(fft, len(fft), normalize=normalize)
            return levels[idx]

        cols = max(1, int(grid.cols))
        rows = max(1, int(grid.rows))
        col = np.minimum(grid.col_np, cols - 1)
        row = np.minimum(grid.row_np, rows - 1)
        return self._audio_bar_array_from_levels(
            fft, cols, rows, col, row, direction=self._audio_bar_direction(),
            normalize=normalize)

    def _compute_audio_fft_array(self) -> Optional[np.ndarray]:
        fft = self._audio_fft_values()
        if fft is None:
            return None
        normalize = self._analyzer is None or self._last_audio_frame is None
        return self._fft_to_array(fft, normalize=normalize)

    def _fft_to_weights(self, fft: np.ndarray, *, normalize: bool = True) -> Dict[str, float]:
        """将 fft 数组（任意长度）映射为设备权重字典。"""
        n = len(self._sorted_ids)
        weights = {}
        grid = self._grid

        if grid is None:
            levels = self._audio_axis_levels(fft, len(fft), normalize=normalize)
            for i, fid in enumerate(self._sorted_ids):
                bin_idx = int(i * len(fft) / n)
                bin_idx = min(bin_idx, len(fft) - 1)
                weights[fid] = float(levels[bin_idx])
        else:
            cols = max(1, grid.cols)
            rows = max(1, grid.rows)
            direction = self._audio_bar_direction()

            for i, fid in enumerate(self._sorted_ids):
                col, row = grid.get_col_row(i)
                weights[fid] = self._audio_bar_weight_from_levels(
                    fft, cols, rows, col, row, direction=direction, normalize=normalize)

        return weights

    def _compute_audio_waveform(self) -> Dict[str, float]:
        """音频波形 → 矩阵权重

        优先使用注入的 AudioAnalyzer（将 8 频段插值为列幅度）；
        无 analyzer 时使用本地 AudioCapture（原始波形）。
        """
        n = len(self._sorted_ids)
        if n == 0:
            return {}

        if not bool(getattr(self, 'pixel_audio_reactive', True)):
            return self._compute_wave()

        # ── 路径 A：AudioAnalyzer 的频段数据 ─────────────────────────────────
        if self._analyzer is not None and self._last_audio_frame is not None:
            bands = list(getattr(self._last_audio_frame, 'bands', [0.0] * 8))
            bands_arr = np.array(bands, dtype=np.float32) * self.audio_amplitude
            grid = self._grid
            weights = {}
            if grid is None or grid.cols <= 1:
                for i, fid in enumerate(self._sorted_ids):
                    bidx = int(i * len(bands_arr) / n) % len(bands_arr)
                    weights[fid] = min(1.0, float(bands_arr[bidx]))
            else:
                cols, rows = grid.cols, grid.rows
                # 将 8 频段插值到 cols 列（线性插值）
                x_dst = np.linspace(0, len(bands_arr) - 1, cols)
                col_samples = np.interp(x_dst, np.arange(len(bands_arr)), bands_arr)
                direction = self._audio_bar_direction()
                for i, fid in enumerate(self._sorted_ids):
                    col, row = grid.get_col_row(i)
                    weights[fid] = self._audio_bar_weight_from_levels(
                        col_samples, cols, rows, col, row, direction=direction)
            return weights

        # ── 路径 B：本地 AudioCapture 原始波形 ───────────────────────────────
        waveform = self._audio.get_waveform()
        grid = self._grid
        weights = {}

        if grid is None or grid.cols <= 1:
            for i, fid in enumerate(self._sorted_ids):
                sample_idx = int(i * len(waveform) / n)
                sample_idx = min(sample_idx, len(waveform) - 1)
                w = abs(float(waveform[sample_idx])) * self.audio_amplitude
                weights[fid] = min(1.0, w)
        else:
            cols = grid.cols
            rows = grid.rows
            col_samples = []
            for c in range(cols):
                sample_idx = int(c * len(waveform) / cols)
                sample_idx = min(sample_idx, len(waveform) - 1)
                col_samples.append(abs(float(waveform[sample_idx])) * self.audio_amplitude)

            direction = self._audio_bar_direction()
            for i, fid in enumerate(self._sorted_ids):
                col, row = grid.get_col_row(i)
                weights[fid] = self._audio_bar_weight_from_levels(
                    col_samples, cols, rows, col, row, direction=direction)

        return weights

    def _compute_audio_waveform_array(self) -> Optional[np.ndarray]:
        n = len(self._sorted_ids)
        if n <= 0:
            return np.zeros(0, dtype=np.float32)
        if not bool(getattr(self, 'pixel_audio_reactive', True)):
            return None
        grid = self._grid
        if self._analyzer is not None and self._last_audio_frame is not None:
            bands_arr = np.asarray(list(getattr(self._last_audio_frame, 'bands', [0.0] * 8)),
                                   dtype=np.float32) * float(self.audio_amplitude)
            if grid is None or grid.cols <= 1:
                idx = np.minimum((np.arange(n, dtype=np.float32) * len(bands_arr) / n).astype(np.int32),
                                 len(bands_arr) - 1)
                return np.clip(bands_arr[idx], 0.0, 1.0)
            cols, rows = max(1, grid.cols), max(1, grid.rows)
            x_dst = np.linspace(0, len(bands_arr) - 1, cols, dtype=np.float32)
            col_samples = np.interp(x_dst, np.arange(len(bands_arr)), bands_arr).astype(np.float32)
        else:
            waveform = self._audio.get_waveform()
            if waveform is None or len(waveform) == 0:
                return np.zeros(n, dtype=np.float32)
            waveform = np.asarray(waveform, dtype=np.float32)
            if grid is None or grid.cols <= 1:
                idx = np.minimum((np.arange(n, dtype=np.float32) * len(waveform) / n).astype(np.int32),
                                 len(waveform) - 1)
                return np.clip(np.abs(waveform[idx]) * float(self.audio_amplitude), 0.0, 1.0)
            cols, rows = max(1, grid.cols), max(1, grid.rows)
            idx = np.minimum((np.arange(cols, dtype=np.float32) * len(waveform) / cols).astype(np.int32),
                             len(waveform) - 1)
            col_samples = np.abs(waveform[idx]) * float(self.audio_amplitude)

        col = np.minimum(grid.col_np, cols - 1)
        row = np.minimum(grid.row_np, rows - 1)
        return self._audio_bar_array_from_levels(
            col_samples, cols, rows, col, row, direction=self._audio_bar_direction())

    def _compute_audio_energy(self) -> Dict[str, float]:
        """音频能量阈值触发：RMS 超过阈值时全部 ON

        优先使用注入的 AudioAnalyzer（timeline 音频 rms）；
        无 analyzer 时使用本地 AudioCapture。
        """
        if not bool(getattr(self, 'pixel_audio_reactive', True)):
            return self._compute_wave()
        if self._analyzer is not None and self._last_audio_frame is not None:
            rms = float(getattr(self._last_audio_frame, 'rms', 0.0))
        else:
            rms = self._audio.get_rms()
        energy = min(1.0, rms * self.audio_amplitude)
        return {fid: energy for fid in self._sorted_ids}

    def _compute_audio_energy_array(self) -> Optional[np.ndarray]:
        if not bool(getattr(self, 'pixel_audio_reactive', True)):
            return None
        if self._analyzer is not None and self._last_audio_frame is not None:
            rms = float(getattr(self._last_audio_frame, 'rms', 0.0))
        else:
            rms = self._audio.get_rms()
        energy = max(0.0, min(1.0, float(rms) * float(self.audio_amplitude)))
        return np.full(len(self._sorted_ids), energy, dtype=np.float32)

    # ─────────────────────────────────────────────────────────────────────────
    #  屏幕采样效果
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_screen_sample(self) -> Dict[str, float]:
        """
        屏幕像素采样 → 矩阵权重。

        优先使用后台 ScreenCaptureEngine 的缓存结果（已经经过平滑/对比度等处理）；
        后台引擎未启动时降级为同步采样。
        """
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}

        cols = max(1, grid.cols)
        rows = max(1, grid.rows)

        # 同步参数到后台引擎（实时生效）
        self._sync_screen_params()

        # 优先读取后台引擎的缓存结果
        eng = self._screen.engine
        if eng is not None and self._screen_engine_started and eng.is_running():
            # 后台引擎已经处理好了平滑、对比度、Gamma 等，直接使用其权重结果
            brightness_map = eng.get_latest_weights()
            if brightness_map.shape != (rows, cols):
                # 网格尺寸不匹配，重新设置
                eng.set_grid(cols, rows)
                brightness_map = eng.get_latest_weights()
        else:
            # 后台引擎未启动，尝试启动它
            if not self._screen_engine_started:
                self._start_screen_engine()
            # 降级：同步采样一次（不经平滑）
            brightness_map = self._screen.sample(
                region=self.screen_region, grid_w=cols, grid_h=rows)

        # 将亮度矩阵映射到设备 ID
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            col, row = grid.get_col_row(i)
            if brightness_map.shape[0] > 0 and brightness_map.shape[1] > 0:
                col = min(col, brightness_map.shape[1] - 1)
                row = min(row, brightness_map.shape[0] - 1)
                weights[fid] = float(brightness_map[row, col])
            else:
                weights[fid] = 0.0

        return weights

    def _latest_screen_brightness_map(self) -> Optional[np.ndarray]:
        grid = self._grid
        if grid is None:
            return None
        cols = max(1, grid.cols)
        rows = max(1, grid.rows)
        self._sync_screen_params()
        eng = self._screen.engine
        if eng is not None and self._screen_engine_started and eng.is_running():
            brightness_map = eng.get_latest_weights()
            if brightness_map.shape != (rows, cols):
                eng.set_grid(cols, rows)
                brightness_map = eng.get_latest_weights()
        else:
            if not self._screen_engine_started:
                self._start_screen_engine()
            brightness_map = self._screen.sample(
                region=self.screen_region, grid_w=cols, grid_h=rows)
        arr = np.asarray(brightness_map, dtype=np.float32)
        if arr.shape != (rows, cols):
            arr = np.zeros((rows, cols), dtype=np.float32)
        return arr

    def _map_grid_to_fixture_array(self, brightness_map: Optional[np.ndarray]) -> np.ndarray:
        grid = self._grid
        n = len(self._sorted_ids)
        if grid is None or brightness_map is None:
            return np.zeros(n, dtype=np.float32)
        arr = np.asarray(brightness_map, dtype=np.float32)
        if arr.ndim != 2 or arr.size == 0:
            return np.zeros(n, dtype=np.float32)
        rows, cols = arr.shape
        col = np.minimum(grid.col_np, cols - 1)
        row = np.minimum(grid.row_np, rows - 1)
        return np.clip(arr[row, col], 0.0, 1.0).astype(np.float32, copy=False)

    def _compute_screen_sample_array(self) -> np.ndarray:
        return self._map_grid_to_fixture_array(self._latest_screen_brightness_map())

    def get_screen_preview_grid(self) -> Optional[np.ndarray]:
        """
        获取屏幕采样的实时预览网格（未经阈值处理的原始亮度值）。
        返回 (rows, cols) float32 数组，或 None。
        """
        eng = self._screen.engine
        if eng is None:
            return None
        return eng.get_preview_grid()

    def get_screen_preview_raw(self) -> Optional[np.ndarray]:
        """
        获取屏幕采样的原始 RGB 预览图（缩略图）。
        返回 (H, W, 3) uint8 数组，或 None。
        """
        eng = self._screen.engine
        if eng is None:
            return None
        return eng.get_preview_raw()

    def get_screen_stats(self) -> Dict:
        """获取屏幕采样的统计信息（min/max/mean/std）。"""
        eng = self._screen.engine
        if eng is None:
            return {"min": 0.0, "max": 0.0, "mean": 0.0, "std": 0.0}
        return eng.get_stats()

    # ─────────────────────────────────────────────────────────────────────────
    #  视频采样效果（OpenCV）
    # ─────────────────────────────────────────────────────────────────────────

    def _get_cv2(self):
        """延迟导入 OpenCV，避免未使用视频功能时影响启动。"""
        try:
            import cv2  # type: ignore
            return cv2
        except Exception:
            return None

    def _release_video_capture(self):
        """线程安全关停：信号+join 超时+僵尸兜底（R193 P2-20 模板），再清句柄。

         R194：全部调用点（stop/stop_camera/load_camera 失败/clear_video）
        经此统一走"停解码线程 → 关 cv2 句柄"，GUI 不再与 worker 抢句柄。
        """
        _stop_video_worker(self)
        _close_video_handle(self)

    def _ensure_video_capture(self) -> bool:
        """确保视频/摄像头句柄可用；按 video_source 分支打开。"""
        if self._video_cap is not None:
            try:
                if self._video_cap.isOpened():
                    return True
            except Exception:
                pass
        cv2 = self._get_cv2()
        if cv2 is None:
            return False
        if getattr(self, "video_source", "file") == "camera":
            return self._open_camera_capture(cv2)
        return self._open_file_capture(cv2)

    def _open_camera_capture(self, cv2) -> bool:
        """打开摄像头句柄（helper；无总帧数概念）。"""
        try:
            cap = cv2.VideoCapture(int(self.camera_device or 0))
        except Exception:
            return False
        if not cap.isOpened():
            return False
        self._video_cap = cap
        self._video_total_frames = 0
        return True

    def _open_file_capture(self, cv2) -> bool:
        """打开视频文件句柄并回填总帧数/帧率/定位（helper）。"""
        if not self.video_path:
            return False
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            return False
        self._video_cap = cap
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        self._video_total_frames = max(0, total)
        self._video_fps = fps if fps > 0 else 25.0
        cur = max(0, min(self._video_current_frame, max(0, self._video_total_frames - 1)))
        if cur > 0:
            try:
                cap.set(cv2.CAP_PROP_POS_FRAMES, cur)
            except Exception:
                pass
        return True

    def load_camera(self, device_index: int = 0) -> bool:
        """ 深查②三#5 P1：切换到摄像头直通源（替换当前视频）。"""
        self.clear_video()
        self.video_source = "camera"
        self.camera_device = max(0, int(device_index or 0))
        ok = self._ensure_video_capture()
        if not ok:
            # 开不起来就回到文件源语义，避免残留半开状态
            self.video_source = "file"
            return False
        # 试读一帧：验证设备真的出图（部分后端 isOpened 但 read 阻塞/失败）
        try:
            ok_f, _frame = self._video_cap.read()
        except Exception:
            ok_f = False
        if not ok_f:
            self._release_video_capture()
            self.video_source = "file"
            return False
        return True

    def stop_camera(self):
        """停用摄像头，回到文件源语义（不清空 video_path）。"""
        self._release_video_capture()
        self.video_source = "file"
        self._camera_fail_count = 0
        self._video_current_frame = 0
        self.video_playing = False

    def load_video(self, path: str) -> bool:
        """加载视频文件，并缓存第一帧用于小预览与进度条初始化。"""
        self.clear_video()
        self.video_path = path or ""
        if not self.video_path or not self._ensure_video_capture():
            return False
        return self.seek_video_frame(0)

    def clear_video(self):
        """清除当前视频/摄像头源与全部缓存状态，供 UI 删除视频和重新导入前复位使用。"""
        self.video_playing = False
        self._release_video_capture()
        self.video_path = ""
        self.video_source = "file"
        self._camera_fail_count = 0
        self._video_total_frames = 0
        self._video_current_frame = 0
        self._video_fps = 25.0
        self._video_frame_cache = None
        self._video_preview_raw = None
        self._video_preview_grid = None
        self._video_prev_grid = None
        self._video_play_dir = 1
        self._video_rate_acc = 0.0

    def seek_video_frame(self, frame_idx: int) -> bool:
        """跳转到指定帧并立即刷新预览缓存；拖动进度条时调用。

         R194：worker 存活时命令化（解码线程统一碰句柄）；无 worker 内联。
        """
        thread = self._video_thread
        if (thread is not None and thread.is_alive()
                and thread is not threading.current_thread()):
            return _video_worker_call(self, "seek", int(frame_idx))
        return _seek_video_frame_body(self, int(frame_idx))

    # ── 走带推进（ 深查②三#11 P2 入出点/乒乓/半速 + #5 摄像头守卫） ──────────

    def _video_should_advance(self) -> bool:
        """原布尔速率门兼容入口；文件 tick 直接消费同一 helper 的完整步数。"""
        from .matrix_video_transport import video_advance_steps
        return bool(video_advance_steps(self))

    def _read_next_video_frame(self):
        """播放中推进一帧，返回 (ok, frame)。

        摄像头源：直接 read()；连败 5 次释放句柄（热插拔守卫，下个 tick 重开）。
        文件源：速率门 + 入出点/方向走带；正向顺序播放保持纯 read()（不逐帧
        seek，避免每帧关键帧搜索）；反向/跳段用 CAP_PROP_POS_FRAMES 定位。
        """
        if self._video_cap is None and not self._ensure_video_capture():
            return False, None
        if getattr(self, "video_source", "file") == "camera":
            return self._read_camera_frame()
        return self._read_file_playhead_frame(self._get_cv2())

    def _read_capture_frame(self):
        """单次句柄 read()（helper；句柄缺失/异常 → (False, None)）。"""
        if self._video_cap is None:
            return False, None
        try:
            return self._video_cap.read()
        except Exception:
            return False, None

    def _read_camera_frame(self):
        """摄像头 tick 读帧 + 热插拔守卫（连败 5 次释放句柄，下 tick 重开）。"""
        ok, frame = self._read_capture_frame()
        if not ok or frame is None:
            self._camera_fail_count += 1
            if self._camera_fail_count >= 5:
                # 热插拔守卫（R194 保留）：只关句柄不停解码线程，下次 tick 重开
                _close_video_handle(self)
                self._camera_fail_count = 0
            return False, None
        self._camera_fail_count = 0
        self._video_current_frame += 1
        return True, frame

    def _seek_video_to(self, nxt: int, cv2) -> None:
        """非顺序推进时按帧号定位（helper；异常静默）。"""
        sequential = (nxt == self._video_current_frame + 1)
        if sequential or cv2 is None or self._video_cap is None:
            return
        try:
            self._video_cap.set(cv2.CAP_PROP_POS_FRAMES, int(nxt))
        except Exception:
            pass

    def _loop_read_from_in_point(self, cv2):
        """读失败后回入点重读（helper）；返回 (ok, frame, nxt)，失败 nxt=None。"""
        in_pt = max(0, int(self.video_in_point or 0))
        if cv2 is None or self._video_cap is None:
            return False, None, None
        try:
            self._video_cap.set(cv2.CAP_PROP_POS_FRAMES, in_pt)
            ok, frame = self._video_cap.read()
            self._video_prev_grid = None
        except Exception:
            return False, None, None
        if not ok or frame is None:
            return False, None, None
        return True, frame, in_pt

    def _read_file_playhead_frame(self, cv2):
        """文件源 tick 读帧：速率门 + 走带推进 + 回环守卫（helper）。"""
        from .matrix_video_transport import read_file_video_frame
        return read_file_video_frame(self, cv2)

    def _warp_source_frame(self, frame_bgr):
        """ #2：按四归一化角点对源帧做透视变形（未启用/失败原样返回）。"""
        try:
            from .matrix_video_transforms import warp_frame_bgr
            warped = warp_frame_bgr(frame_bgr, getattr(self, "video_warp_corners", None))
            if warped is not None:
                return warped
        except Exception:
            logger.debug("matrix: video warp failed", exc_info=True)
        return frame_bgr

    @staticmethod
    def _sample_hue_sat(resized, channel, cv2):
        """色相/饱和度通道采样（helper 压 CC）。"""
        hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV).astype(np.float32)
        if channel == 'saturation':
            return hsv[:, :, 1] / 255.0
        return hsv[:, :, 0] / 179.0

    def _sample_channels(self, resized, cv2):
        """抽样通道 → 亮度图（luminance/red/green/blue/saturation/hue/motion）。"""
        b = resized[:, :, 0].astype(np.float32) / 255.0
        g = resized[:, :, 1].astype(np.float32) / 255.0
        r = resized[:, :, 2].astype(np.float32) / 255.0
        channel = getattr(self, 'screen_channel', 'luminance')
        if channel == 'red':
            return r
        if channel == 'green':
            return g
        if channel == 'blue':
            return b
        if channel in ('saturation', 'hue'):
            return self._sample_hue_sat(resized, channel, cv2)
        if channel == 'motion':
            luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
            if self._video_prev_grid is None or self._video_prev_grid.shape != luma.shape:
                brightness_map = np.zeros_like(luma)
            else:
                brightness_map = np.abs(luma - self._video_prev_grid) * 4.0
            self._video_prev_grid = luma.copy()
            return brightness_map
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    def _apply_screen_tone(self, brightness_map):
        """采样参数链：输入阈值/对比度/亮度/Gamma/时间平滑（逐行搬移）。"""
        lo = float(getattr(self, 'screen_threshold_low', 0.0))
        hi = float(getattr(self, 'screen_threshold_high', 1.0))
        if hi <= lo:
            hi = lo + 1e-6
        brightness_map = np.clip((brightness_map - lo) / (hi - lo), 0.0, 1.0)
        contrast = float(getattr(self, 'screen_contrast', 1.0))
        brightness = float(getattr(self, 'screen_brightness_boost', 1.0))
        gamma = max(0.05, float(getattr(self, 'screen_gamma', 1.0)))
        brightness_map = (brightness_map - 0.5) * contrast + 0.5
        brightness_map = np.clip(brightness_map * brightness, 0.0, 1.0)
        if abs(gamma - 1.0) > 1e-3:
            brightness_map = np.power(brightness_map, 1.0 / gamma)
        smooth = float(getattr(self, 'screen_smooth', 0.0))
        smooth = max(0.0, min(0.98, smooth))
        if (self._video_preview_grid is not None and
                self._video_preview_grid.shape == brightness_map.shape and
                smooth > 0.0):
            brightness_map = self._video_preview_grid * smooth + brightness_map * (1.0 - smooth)
        return brightness_map

    def _apply_sample_mask(self, brightness_map):
        """ #3：掩码 AND（黑区不亮；灰区按比例）——最后一步硬闸。"""
        mask = getattr(self, 'sample_mask', None)
        if mask is None:
            return brightness_map
        try:
            from .matrix_video_transforms import apply_mask_to_map
            return apply_mask_to_map(brightness_map, mask)
        except Exception:
            logger.debug("matrix: mask apply failed", exc_info=True)
            return brightness_map

    def _frame_to_video_maps(self, frame_bgr: np.ndarray) -> np.ndarray:
        """BGR 帧→亮度网格，并发布共享三元组（R194：锁外重算、锁内单次写回）。

         深查②三#2 P0：采样前按四归一化角点做透视变形（未启用=原样）；
         深查②三#3 P0：输出亮度图与掩码逐格相乘（黑区不亮）。
        """
        grid_map, preview_raw = self._build_video_maps(frame_bgr)
        with self._video_decode_lock:
            self._video_frame_cache = frame_bgr     # 发布即写回（BGR 源帧）
            if preview_raw is not None:
                self._video_preview_raw = preview_raw
            self._video_preview_grid = grid_map     # None=空网格清图（旧语义）
            self._video_frame_seq += 1              # 代际号：消费方可验新旧
        return (grid_map if grid_map is not None
                else np.zeros((0, 0), dtype=np.float32))

    def _build_video_maps(self, frame_bgr):
        """锁外全链重计算：warp→预览RGB→抽样→色调→掩码（不碰共享态）。"""
        grid = self._grid
        if grid is None:
            return None, None                       # 旧语义：空网格只清亮度图
        cv2 = self._get_cv2()
        cols, rows = max(1, grid.cols), max(1, grid.rows)
        if cv2 is None or frame_bgr is None or frame_bgr.size == 0:
            return np.zeros((rows, cols), dtype=np.float32), None
        frame_bgr = self._warp_source_frame(frame_bgr)   #  四角透视（未启用=原样）
        preview_raw = _rgb_preview_of(frame_bgr, cv2)
        resized = cv2.resize(frame_bgr, (cols, rows), interpolation=cv2.INTER_AREA)
        brightness_map = self._sample_channels(resized, cv2)
        brightness_map = self._apply_screen_tone(brightness_map)
        brightness_map = self._apply_sample_mask(brightness_map)
        return np.clip(brightness_map, 0.0, 1.0).astype(np.float32), preview_raw

    def _compute_video_sample(self) -> Dict[str, float]:
        """视频像素采样 → 矩阵权重；R194：tick 只发推进请求+取快照，不再解码。"""
        grid = self._grid
        # _grid 为 None 或 fixtures 为空时仍要发推进请求（保持解码连续性）
        if grid is None or grid.n == 0:
            if self.video_playing:
                self._post_video_advance()
            return {fid: 0.0 for fid in self._sorted_ids}
        if self.video_playing:
            self._post_video_advance()
        brightness_map = self._snapshot_video_grid(max(1, grid.rows), max(1, grid.cols))
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            col, row = grid.get_col_row(i)
            col = min(col, brightness_map.shape[1] - 1)
            row = min(row, brightness_map.shape[0] - 1)
            weights[fid] = float(brightness_map[row, col])
        return weights

    def _latest_video_brightness_map(self) -> Optional[np.ndarray]:
        """tick 消费方：锁内取最新三元组快照；decode/warp 全在解码线程（R194）。"""
        grid = self._grid
        if grid is None or grid.n == 0:
            if self.video_playing:
                self._post_video_advance()    # 空网格也逐拍推进（解码连续性）
            return None
        if self.video_playing:
            self._post_video_advance()        # 每拍一条推进请求（等帧就绪有界）
        return self._snapshot_video_grid(max(1, grid.rows), max(1, grid.cols))

    def _post_video_advance(self) -> None:
        """GUI tick 每拍恰一条推进请求；有界等帧就绪（慢解码只丢帧不拖 tick）。"""
        if not _ensure_video_worker(self):
            return                            # 僵尸未退拒启：本拍保持上一帧
        with self._video_frame_ready:
            self._video_pending_reqs += 1     # 解码线程按请求同步推进（复用旧走带）
            self._video_req_seq += 1
            seq = self._video_req_seq
            self._video_wake.set()
            if self._video_done_seq < seq:
                self._video_frame_ready.wait(_VIDEO_TICK_WAIT)

    def _snapshot_video_grid(self, rows: int, cols: int) -> np.ndarray:
        """锁内取最新三元组快照（只读引用+代际号；发布侧永不原地改写）。"""
        brightness_map = self._peek_video_grid()
        if brightness_map is None or brightness_map.shape != (rows, cols):
            self._refresh_video_maps(False)   # 尺寸漂移/缺图：经引擎接口重算一次
            brightness_map = self._peek_video_grid()
        if brightness_map is None or brightness_map.shape != (rows, cols):
            brightness_map = np.zeros((rows, cols), dtype=np.float32)
            with self._video_decode_lock:
                self._video_preview_grid = brightness_map
        return brightness_map

    def _peek_video_grid(self) -> Optional[np.ndarray]:
        """锁内读共享亮度图快照（worker 存活时即解码线程最新发布值；helper）。"""
        with self._video_decode_lock:
            return self._video_preview_grid

    def _refresh_video_maps(self, reset_history: bool) -> bool:
        """缓存帧重算入口（worker 存活→命令化；否则内联；matrix_video_cache 调用）。"""
        with self._video_decode_lock:
            has_frame = self._video_frame_cache is not None
        if not has_frame:
            return False
        thread = self._video_thread
        if (thread is not None and thread.is_alive()
                and thread is not threading.current_thread()):
            return _video_worker_call(self, "refresh", reset_history)
        return _refresh_video_maps_body(self, reset_history)

    def submit_external_video_frame(self, frame) -> bool:
        """HF-02：外部源（Spout 桥）投递原始 BGR 帧（latest-wins）。

        warp/RGB 转换由解码线程统一做；与 cv2 句柄生命周期完全无关。
        """
        if frame is None or getattr(frame, "size", 0) == 0:
            return False
        if not _ensure_video_worker(self):
            return False
        with self._video_decode_lock:
            self._video_ext_frame = frame     # 只留最新，旧的直接丢弃
        self._video_wake.set()
        return True

    def _compute_video_sample_array(self) -> np.ndarray:
        return self._map_grid_to_fixture_array(self._latest_video_brightness_map())

    def get_video_preview_grid(self) -> Optional[np.ndarray]:
        """返回当前视频帧映射后的亮度网格。"""
        return self._video_preview_grid

    def get_video_preview_raw(self) -> Optional[np.ndarray]:
        """返回当前视频帧 RGB 预览图。"""
        return self._video_preview_raw

    def get_video_stats(self) -> Dict:
        """获取视频采样当前亮度网格统计信息。"""
        grid = self._video_preview_grid
        if grid is None or grid.size == 0:
            return {"min": 0.0, "max": 0.0, "mean": 0.0, "std": 0.0}
        return {
            "min": float(np.min(grid)),
            "max": float(np.max(grid)),
            "mean": float(np.mean(grid)),
            "std": float(np.std(grid)),
        }

    def get_video_total_frames(self) -> int:
        return int(self._video_total_frames)

    def get_video_current_frame(self) -> int:
        return int(self._video_current_frame)

    def get_video_fps(self) -> float:
        return float(self._video_fps)

    # ─────────────────────────────────────────────────────────────────────────
    #  内置动态效果
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_ripple(self) -> Dict[str, float]:
        """波纹扩散：从中心向外扩散的圆形波纹"""
        t = self._t
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            d = self._grid.distance_to_center(i) if self._grid else (i / max(1, len(self._sorted_ids)))
            # 波纹：sin 函数，相位随距离变化
            phase = d * 6.0 - t * 3.0
            w = (math.sin(phase) + 1.0) / 2.0
            weights[fid] = w
        return weights

    def _compute_chase(self) -> Dict[str, float]:
        """追逐跑：按位置顺序依次点亮"""
        n = len(self._sorted_ids)
        if n == 0:
            return {}
        # 追逐窗口大小（约 1/4 设备数）
        window = max(1, n // 4)
        pos = (self._t * 2.0) % n  # 当前位置
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            dist = (i - pos) % n
            w = 1.0 if dist < window else 0.0
            # 软边缘
            if dist < 1.0:
                w = dist
            elif dist > window - 1:
                w = max(0.0, window - dist)
            weights[fid] = w
        return weights

    def _compute_spiral(self) -> Dict[str, float]:
        """螺旋效果：按角度+距离组合的螺旋波纹"""
        t = self._t
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            if self._grid:
                nx, ny = self._grid.get_norm_pos(i)
                cx, cy = nx - 0.5, ny - 0.5
                angle = math.atan2(cy, cx)
                dist = math.sqrt(cx**2 + cy**2)
                phase = angle + dist * 4.0 - t * 2.0
            else:
                phase = i * 0.5 - t * 2.0
            w = (math.sin(phase) + 1.0) / 2.0
            weights[fid] = w
        return weights

    def _compute_heartbeat(self) -> Dict[str, float]:
        """心跳：周期性全亮/全灭，带快速衰减"""
        # 心跳节律：快速上升，慢速衰减
        period = 1.0  # 1秒一次心跳
        phase = (self._t % period) / period
        if phase < 0.1:
            w = phase / 0.1  # 快速上升
        elif phase < 0.3:
            w = 1.0 - (phase - 0.1) / 0.2  # 快速下降
        elif phase < 0.4:
            w = (phase - 0.3) / 0.1 * 0.5  # 第二次小跳
        elif phase < 0.5:
            w = 0.5 - (phase - 0.4) / 0.1 * 0.5  # 小跳衰减
        else:
            w = 0.0  # 静止
        return {fid: w for fid in self._sorted_ids}

    def _compute_explode(self) -> Dict[str, float]:
        """爆炸扩散：从中心向外扩散，然后收缩"""
        period = 2.0
        phase = (self._t % period) / period
        # 扩散半径
        if phase < 0.5:
            radius = phase * 2.0  # 0→1 扩散
        else:
            radius = 1.0 - (phase - 0.5) * 2.0  # 1→0 收缩

        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            d = self._grid.distance_to_center(i) if self._grid else 0.5
            # 环形：在 radius 附近的设备点亮
            dist_to_ring = abs(d - radius)
            w = max(0.0, 1.0 - dist_to_ring * 4.0)
            weights[fid] = w
        return weights

    def _compute_rain(self) -> Dict[str, float]:
        """矩阵雨：随机列从上到下滴落"""
        state = self._effect_state
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}

        cols = grid.cols
        rows = grid.rows

        # 初始化雨滴状态
        if "drops" not in state:
            state["drops"] = {}  # {col: (row_float, speed)}
            for c in range(cols):
                if random.random() < 0.3:
                    state["drops"][c] = (random.uniform(-rows, 0), random.uniform(0.5, 2.0))

        # 更新雨滴位置
        drops = state["drops"]
        dt_approx = 0.04  # 假设 25fps
        for c in list(drops.keys()):
            row, speed = drops[c]
            row += speed * self.speed * dt_approx * 10
            if row > rows + 2:
                # 重置
                if random.random() < 0.7:
                    drops[c] = (random.uniform(-rows * 0.5, 0), random.uniform(0.5, 2.0))
                else:
                    del drops[c]
            else:
                drops[c] = (row, speed)

        # 随机新增雨滴
        for c in range(cols):
            if c not in drops and random.random() < 0.02:
                drops[c] = (0.0, random.uniform(0.5, 2.0))

        # 计算权重
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            col, row = grid.get_col_row(i)
            w = 0.0
            if col in drops:
                drop_row, _ = drops[col]
                dist = abs(row - drop_row)
                if dist < 1.5:
                    w = max(0.0, 1.0 - dist / 1.5)
                    # 头部最亮，拖尾渐暗
                    if row < drop_row:
                        w *= max(0.0, 1.0 - (drop_row - row) * 0.5)
            weights[fid] = w
        return weights

    def _compute_snake(self) -> Dict[str, float]:
        """贪吃蛇：蛇形路径上的追逐光点"""
        n = len(self._sorted_ids)
        if n == 0:
            return {}
        # 蛇头位置（按排序索引）
        head = int(self._t * 3.0 * self.speed) % n
        tail_len = max(1, n // 5)
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            dist = (head - i) % n
            if dist < tail_len:
                w = 1.0 - dist / tail_len
            else:
                w = 0.0
            weights[fid] = w
        return weights

    def _compute_random(self) -> Dict[str, float]:
        """随机闪烁：每帧随机点亮部分设备"""
        state = self._effect_state
        # 每隔 0.1s 更新一次随机状态
        if "last_update" not in state:
            state["last_update"] = 0.0
            state["weights"] = {fid: 0.0 for fid in self._sorted_ids}

        if self._t - state["last_update"] > 0.1 / self.speed:
            state["last_update"] = self._t
            new_weights = {}
            for fid in self._sorted_ids:
                # 随机决定是否点亮，概率约 30%
                if random.random() < 0.3:
                    new_weights[fid] = random.uniform(0.5, 1.0)
                else:
                    # 保留部分旧状态（衰减）
                    old = state["weights"].get(fid, 0.0)
                    new_weights[fid] = old * 0.5
            state["weights"] = new_weights

        return dict(state.get("weights", {}))

    def _compute_fire(self) -> Dict[str, float]:
        """火焰效果：底部随机点亮，向上传播衰减"""
        state = self._effect_state
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}

        cols = grid.cols
        rows = grid.rows

        # 初始化火焰网格
        if "grid" not in state or state.get("cols") != cols or state.get("rows") != rows:
            state["grid"] = np.zeros((rows, cols))
            state["cols"] = cols
            state["rows"] = rows

        fire_grid = state["grid"]

        # 底部随机点火
        for c in range(cols):
            fire_grid[rows - 1, c] = random.uniform(0.8, 1.0) if random.random() < 0.7 else 0.0

        # 向上传播（冷却）
        new_grid = np.zeros((rows, cols))
        for r in range(rows - 1):
            for c in range(cols):
                # 取下方和周围的平均值，稍微向左偏移（模拟火焰飘动）
                below = fire_grid[r + 1, c]
                left  = fire_grid[r + 1, max(0, c - 1)]
                right = fire_grid[r + 1, min(cols - 1, c + 1)]
                avg = (below * 2 + left + right) / 4.0
                # 冷却
                new_grid[r, c] = max(0.0, avg - random.uniform(0.05, 0.15))
        new_grid[rows - 1] = fire_grid[rows - 1]
        state["grid"] = new_grid

        # 映射到设备
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            col, row = grid.get_col_row(i)
            col = min(col, cols - 1)
            row = min(row, rows - 1)
            weights[fid] = float(new_grid[row, col])
        return weights

    def _compute_wave(self) -> Dict[str, float]:
        """波浪：横向正弦波"""
        t = self._t
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            if self._grid:
                nx, ny = self._grid.get_norm_pos(i)
                phase = nx * 4.0 * math.pi - t * 2.0
                # 波峰位置决定点亮
                wave_y = (math.sin(phase) + 1.0) / 2.0  # [0, 1]
                # 设备 y 坐标与波峰的距离
                dist = abs(ny - wave_y)
                w = max(0.0, 1.0 - dist * 3.0)
            else:
                phase = i * 0.3 - t * 2.0
                w = (math.sin(phase) + 1.0) / 2.0
            weights[fid] = w
        return weights

    def _compute_ripple_array(self) -> np.ndarray:
        n = len(self._sorted_ids)
        if n <= 0:
            return np.zeros(0, dtype=np.float32)
        if self._grid is not None:
            cx = self._grid.norm_x_np - 0.5
            cy = self._grid.norm_y_np - 0.5
            d = np.sqrt(cx * cx + cy * cy) * np.sqrt(2.0)
        else:
            d = np.arange(n, dtype=np.float32) / max(1, n)
        phase = d * 6.0 - float(self._t) * 3.0
        return ((np.sin(phase) + 1.0) * 0.5).astype(np.float32, copy=False)

    def _compute_chase_array(self) -> np.ndarray:
        n = len(self._sorted_ids)
        if n <= 0:
            return np.zeros(0, dtype=np.float32)
        window = max(1, n // 4)
        pos = (float(self._t) * 2.0) % n
        idx = np.arange(n, dtype=np.float32)
        dist = (idx - pos) % n
        out = (dist < window).astype(np.float32)
        edge_head = dist < 1.0
        out[edge_head] = dist[edge_head]
        edge_tail = dist > (window - 1)
        out[edge_tail] = np.maximum(0.0, window - dist[edge_tail])
        return np.clip(out, 0.0, 1.0).astype(np.float32, copy=False)

    def _compute_spiral_array(self) -> np.ndarray:
        n = len(self._sorted_ids)
        if n <= 0:
            return np.zeros(0, dtype=np.float32)
        if self._grid is not None:
            cx = self._grid.norm_x_np - 0.5
            cy = self._grid.norm_y_np - 0.5
            angle = np.arctan2(cy, cx)
            dist = np.sqrt(cx * cx + cy * cy)
            phase = angle + dist * 4.0 - float(self._t) * 2.0
        else:
            phase = np.arange(n, dtype=np.float32) * 0.5 - float(self._t) * 2.0
        return ((np.sin(phase) + 1.0) * 0.5).astype(np.float32, copy=False)

    def _compute_heartbeat_array(self) -> np.ndarray:
        phase = (float(self._t) % 1.0)
        if phase < 0.1:
            w = phase / 0.1
        elif phase < 0.3:
            w = 1.0 - (phase - 0.1) / 0.2
        elif phase < 0.4:
            w = (phase - 0.3) / 0.1 * 0.5
        elif phase < 0.5:
            w = 0.5 - (phase - 0.4) / 0.1 * 0.5
        else:
            w = 0.0
        return np.full(len(self._sorted_ids), max(0.0, min(1.0, w)), dtype=np.float32)

    def _compute_explode_array(self) -> np.ndarray:
        n = len(self._sorted_ids)
        if n <= 0:
            return np.zeros(0, dtype=np.float32)
        phase = (float(self._t) % 2.0) / 2.0
        radius = phase * 2.0 if phase < 0.5 else 1.0 - (phase - 0.5) * 2.0
        if self._grid is not None:
            cx = self._grid.norm_x_np - 0.5
            cy = self._grid.norm_y_np - 0.5
            d = np.sqrt(cx * cx + cy * cy) * np.sqrt(2.0)
        else:
            d = np.full(n, 0.5, dtype=np.float32)
        return np.maximum(0.0, 1.0 - np.abs(d - radius) * 4.0).astype(np.float32, copy=False)

    def _compute_snake_array(self) -> np.ndarray:
        n = len(self._sorted_ids)
        if n <= 0:
            return np.zeros(0, dtype=np.float32)
        head = int(float(self._t) * 3.0 * float(self.speed)) % n
        tail_len = max(1, n // 5)
        idx = np.arange(n, dtype=np.int32)
        dist = (head - idx) % n
        out = np.zeros(n, dtype=np.float32)
        mask = dist < tail_len
        out[mask] = 1.0 - dist[mask].astype(np.float32) / float(tail_len)
        return out

    def _compute_random_array(self) -> np.ndarray:
        n = len(self._sorted_ids)
        if n <= 0:
            return np.zeros(0, dtype=np.float32)
        state = self._effect_state
        current = state.get("weights_array")
        if current is None or len(current) != n:
            current = np.zeros(n, dtype=np.float32)
            state["weights_array"] = current
            state["last_update"] = 0.0
        if float(self._t) - float(state.get("last_update", 0.0) or 0.0) > 0.1 / max(0.01, float(self.speed)):
            state["last_update"] = float(self._t)
            rnd = np.random.random(n)
            bright = 0.5 + np.random.random(n).astype(np.float32) * 0.5
            current = np.where(rnd < 0.3, bright, current.astype(np.float32) * 0.5).astype(np.float32)
            state["weights_array"] = current
        return np.asarray(current, dtype=np.float32)

    def _compute_fire_array(self) -> np.ndarray:
        grid = self._grid
        n = len(self._sorted_ids)
        if grid is None:
            return np.zeros(n, dtype=np.float32)
        cols = max(1, grid.cols)
        rows = max(1, grid.rows)
        state = self._effect_state
        if "grid" not in state or state.get("cols") != cols or state.get("rows") != rows:
            state["grid"] = np.zeros((rows, cols), dtype=np.float32)
            state["cols"] = cols
            state["rows"] = rows
        fire_grid = np.asarray(state["grid"], dtype=np.float32)
        fire_grid[rows - 1, :] = np.where(
            np.random.random(cols) < 0.7,
            0.8 + np.random.random(cols).astype(np.float32) * 0.2,
            0.0,
        )
        new_grid = np.zeros((rows, cols), dtype=np.float32)
        if rows > 1:
            below = fire_grid[1:, :]
            left = np.concatenate((below[:, :1], below[:, :-1]), axis=1)
            right = np.concatenate((below[:, 1:], below[:, -1:]), axis=1)
            avg = (below * 2.0 + left + right) * 0.25
            cool = 0.05 + np.random.random((rows - 1, cols)).astype(np.float32) * 0.10
            new_grid[:-1, :] = np.maximum(0.0, avg - cool)
        new_grid[rows - 1, :] = fire_grid[rows - 1, :]
        state["grid"] = new_grid
        col = np.minimum(grid.col_np, cols - 1)
        row = np.minimum(grid.row_np, rows - 1)
        return np.clip(new_grid[row, col], 0.0, 1.0).astype(np.float32, copy=False)

    def _compute_wave_array(self) -> np.ndarray:
        n = len(self._sorted_ids)
        if n <= 0:
            return np.zeros(0, dtype=np.float32)
        if self._grid is not None:
            nx = self._grid.norm_x_np
            ny = self._grid.norm_y_np
            phase = nx * 4.0 * np.pi - float(self._t) * 2.0
            wave_y = (np.sin(phase) + 1.0) * 0.5
            return np.maximum(0.0, 1.0 - np.abs(ny - wave_y) * 3.0).astype(np.float32, copy=False)
        phase = np.arange(n, dtype=np.float32) * 0.3 - float(self._t) * 2.0
        return ((np.sin(phase) + 1.0) * 0.5).astype(np.float32, copy=False)

    # ── 辅助方法 ──────────────────────────────────────────────────────────────

    def get_audio_backend(self) -> str:
        return self._audio._backend

    def list_audio_devices(self) -> List[Dict]:
        return self._audio.list_devices()

    def list_monitors(self) -> List[Dict]:
        return self._screen.get_monitors()

    def is_screen_available(self) -> bool:
        return self._screen.available
    # ─────────────────────────────────────────────────────────────────────────
    #  v25 新增音频高级效果
    # ─────────────────────────────────────────────────────────────────────────

    def _simulated_audio_bands(self) -> List[float]:
        t = float(getattr(self, "_t", 0.0) or 0.0)
        return [
            max(0.0, min(1.0, 0.5 + 0.45 * math.sin(t * (1.1 + i * 0.17) * 2 * math.pi + i * 0.73)))
            for i in range(8)
        ]

    def _simulated_audio_rms(self) -> float:
        t = float(getattr(self, "_t", 0.0) or 0.0)
        return max(0.0, min(1.0, 0.45 + 0.35 * math.sin(t * 2.0 * math.pi)))

    def _simulated_is_beat(self) -> bool:
        phase = float(getattr(self, "_t", 0.0) or 0.0) % 1.0
        return phase < 0.08

    def _get_audio_bands(self) -> List[float]:
        """获取音频频段数据（兼容 analyzer 和本地 AudioCapture）"""
        if not bool(getattr(self, 'pixel_audio_reactive', True)):
            return self._simulated_audio_bands()
        if self._analyzer is not None and self._last_audio_frame is not None:
            return list(getattr(self._last_audio_frame, 'bands', [0.0] * 8))
        fft = self._audio.get_fft()
        if fft is None or len(fft) == 0:
            return [0.0] * 8
        # 将 FFT 分成 8 频段
        n = len(fft)
        bands = []
        for i in range(8):
            start = int(i * n / 8)
            end = int((i + 1) * n / 8)
            bands.append(float(np.mean(fft[start:end])) * self.audio_amplitude)
        return bands

    def _get_audio_rms(self) -> float:
        if not bool(getattr(self, 'pixel_audio_reactive', True)):
            return self._simulated_audio_rms()
        if self._analyzer is not None and self._last_audio_frame is not None:
            return float(getattr(self._last_audio_frame, 'rms', 0.0))
        return self._audio.get_rms()

    def _get_is_beat(self) -> bool:
        if not bool(getattr(self, 'pixel_audio_reactive', True)):
            return self._simulated_is_beat()
        if self._analyzer is not None and self._last_audio_frame is not None:
            return bool(getattr(self._last_audio_frame, 'is_beat', False))
        return False

    def _compute_audio_beat_pulse(self) -> Dict[str, float]:
        """节拍脉冲：每次节拍全亮然后快速衰减，带空间扩散"""
        n = len(self._sorted_ids)
        if n == 0:
            return {}
        is_beat = self._get_is_beat()
        rms = self._get_audio_rms()
        state = self._effect_state
        if 'pulse_level' not in state:
            state['pulse_level'] = 0.0
            state['pulse_center'] = n // 2
        if is_beat or rms > 0.6:
            state['pulse_level'] = 1.0
            state['pulse_center'] = random.randint(0, n - 1)
        else:
            state['pulse_level'] *= 0.85  # 衰减
        level = state['pulse_level']
        center = state['pulse_center']
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            dist = abs(i - center) / max(1, n)
            # 脉冲从中心向外扩散并衰减
            w = level * max(0.0, 1.0 - dist * 3.0)
            weights[fid] = min(1.0, w)
        return weights

    def _compute_audio_beat_pulse_array(self) -> np.ndarray:
        n = len(self._sorted_ids)
        if n <= 0:
            return np.zeros(0, dtype=np.float32)
        is_beat = self._get_is_beat()
        rms = self._get_audio_rms()
        state = self._effect_state
        if 'pulse_level' not in state:
            state['pulse_level'] = 0.0
            state['pulse_center'] = n // 2
        if is_beat or rms > 0.6:
            state['pulse_level'] = 1.0
            state['pulse_center'] = random.randint(0, n - 1)
        else:
            state['pulse_level'] *= 0.85
        level = float(state['pulse_level'])
        center = int(state['pulse_center'])
        idx = np.arange(n, dtype=np.float32)
        dist = np.abs(idx - float(center)) / max(1, n)
        return np.clip(level * np.maximum(0.0, 1.0 - dist * 3.0), 0.0, 1.0)

    def _compute_audio_band_matrix(self) -> Dict[str, float]:
        """频段矩阵：每列对应一个频段，高度表示能量"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        cols = max(1, grid.cols)
        rows = max(1, grid.rows)
        weights = {}
        bands = self._get_audio_bands()
        direction = self._audio_bar_direction()
        style = self._audio_bar_style()
        layout = self._audio_band_layout()
        for i, fid in enumerate(self._sorted_ids):
            col, row = grid.get_col_row(i)
            weights[fid] = self._audio_bar_weight_from_levels(
                bands, cols, rows, col, row, direction=direction, style=style,
                layout=layout, state_key="band_matrix")
        return weights

    def _compute_audio_band_matrix_array(self) -> np.ndarray:
        grid = self._grid
        n = len(self._sorted_ids)
        if grid is None:
            return np.zeros(n, dtype=np.float32)
        bands = np.asarray(self._get_audio_bands(), dtype=np.float32)
        if bands.size == 0:
            return np.zeros(n, dtype=np.float32)
        cols = max(1, grid.cols)
        rows = max(1, grid.rows)
        col = np.minimum(grid.col_np, cols - 1)
        row = np.minimum(grid.row_np, rows - 1)
        return self._audio_bar_array_from_levels(
            bands, cols, rows, col, row, direction=self._audio_bar_direction(),
            style=self._audio_bar_style(), layout=self._audio_band_layout(),
            state_key="band_matrix")

    def _compute_audio_bass_wave(self) -> Dict[str, float]:
        """低频波浪：低频能量驱动波浪的振幅和速度"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        bands = self._get_audio_bands()
        bass = min(1.0, (bands[0] + bands[1]) * self.audio_amplitude)
        t = self._t
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            nx, ny = grid.get_norm_pos(i)
            # 波浪振幅由低频驱动
            wave = math.sin(2 * math.pi * (nx * 3.0 - t * 2.0)) * bass
            weights[fid] = max(0.0, min(1.0, (wave + 1.0) / 2.0))
        return weights

    def _compute_audio_bass_wave_array(self) -> np.ndarray:
        grid = self._grid
        n = len(self._sorted_ids)
        if grid is None:
            return np.zeros(n, dtype=np.float32)
        bands = self._get_audio_bands()
        bass = min(1.0, (bands[0] + bands[1]) * self.audio_amplitude) if len(bands) >= 2 else 0.0
        wave = np.sin(2.0 * np.pi * (grid.norm_x_np * 3.0 - float(self._t) * 2.0)) * float(bass)
        return np.clip((wave + 1.0) / 2.0, 0.0, 1.0).astype(np.float32, copy=False)

    def _compute_audio_reactive(self) -> Dict[str, float]:
        """音频反应式：综合 RMS + 频段 + 节拍的复合反应"""
        n = len(self._sorted_ids)
        if n == 0:
            return {}
        bands = self._get_audio_bands()
        rms = self._get_audio_rms()
        is_beat = self._get_is_beat()
        t = self._t
        state = self._effect_state
        if 'beat_flash' not in state:
            state['beat_flash'] = 0.0
        if is_beat:
            state['beat_flash'] = 1.0
        else:
            state['beat_flash'] *= 0.9
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            progress = i / max(1, n - 1)
            # 频段映射
            band_idx = min(7, int(progress * 8))
            band_val = min(1.0, bands[band_idx] * self.audio_amplitude)
            # 节拍闪烁
            beat_val = state['beat_flash'] * (1.0 - abs(progress - 0.5) * 2)
            # 能量基底
            energy_base = rms * 0.3
            weights[fid] = min(1.0, max(band_val, beat_val, energy_base))
        return weights

    def _compute_audio_reactive_array(self) -> np.ndarray:
        n = len(self._sorted_ids)
        if n <= 0:
            return np.zeros(0, dtype=np.float32)
        bands = np.asarray(self._get_audio_bands(), dtype=np.float32)
        if bands.size == 0:
            bands = np.zeros(8, dtype=np.float32)
        rms = float(self._get_audio_rms())
        is_beat = self._get_is_beat()
        state = self._effect_state
        if 'beat_flash' not in state:
            state['beat_flash'] = 0.0
        if is_beat:
            state['beat_flash'] = 1.0
        else:
            state['beat_flash'] *= 0.9
        progress = np.arange(n, dtype=np.float32) / max(1, n - 1)
        band_idx = np.minimum(bands.size - 1, (progress * bands.size).astype(np.int32))
        band_val = np.clip(bands[band_idx] * float(self.audio_amplitude), 0.0, 1.0)
        beat_val = float(state['beat_flash']) * (1.0 - np.abs(progress - 0.5) * 2.0)
        energy_base = rms * 0.3
        return np.clip(np.maximum.reduce([band_val, beat_val, np.full(n, energy_base, dtype=np.float32)]),
                       0.0, 1.0)

    def _compute_audio_spectrum_ring(self) -> Dict[str, float]:
        """频谱环：频段能量映射到环形/径向分布"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        bands = self._get_audio_bands()
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            nx, ny = grid.get_norm_pos(i)
            # 计算角度和距离
            cx, cy = nx - 0.5, ny - 0.5
            angle = (math.atan2(cy, cx) / (2 * math.pi) + 0.5) % 1.0
            dist = math.sqrt(cx**2 + cy**2) * 2
            # 角度映射到频段
            band_idx = min(7, int(angle * 8))
            energy = min(1.0, bands[band_idx] * self.audio_amplitude)
            # 距离作为阈值
            weights[fid] = 1.0 if energy > dist else 0.0
        return weights

    def _compute_audio_spectrum_ring_array(self) -> np.ndarray:
        grid = self._grid
        n = len(self._sorted_ids)
        if grid is None:
            return np.zeros(n, dtype=np.float32)
        bands = np.asarray(self._get_audio_bands(), dtype=np.float32)
        if bands.size == 0:
            return np.zeros(n, dtype=np.float32)
        cx = grid.norm_x_np - 0.5
        cy = grid.norm_y_np - 0.5
        angle = (np.arctan2(cy, cx) / (2.0 * np.pi) + 0.5) % 1.0
        dist = np.sqrt(cx * cx + cy * cy) * 2.0
        band_idx = np.minimum(bands.size - 1, (angle * bands.size).astype(np.int32))
        energy = np.clip(bands[band_idx] * float(self.audio_amplitude), 0.0, 1.0)
        return (energy > dist).astype(np.float32)

    # ─────────────────────────────────────────────────────────────────────────
    #  v25 新增动态效果
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_plasma(self) -> Dict[str, float]:
        """等离子体：多层正弦波叠加产生有机流动图案"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        t = self._t
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            nx, ny = grid.get_norm_pos(i)
            v1 = math.sin(nx * 10 + t)
            v2 = math.sin(ny * 10 + t * 1.3)
            v3 = math.sin((nx + ny) * 5 + t * 0.7)
            v4 = math.sin(math.sqrt((nx - 0.5)**2 + (ny - 0.5)**2) * 8 - t * 2)
            val = (v1 + v2 + v3 + v4) / 4.0 * 0.5 + 0.5
            weights[fid] = max(0.0, min(1.0, val))
        return weights

    def _compute_conway(self) -> Dict[str, float]:
        """生命游戏：Conway's Game of Life"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        cols, rows = grid.cols, grid.rows
        state = self._effect_state
        if 'cells' not in state or state.get('gen_t', 0) + 0.3 < self._t:
            if 'cells' not in state:
                # 随机初始化
                state['cells'] = [[random.random() > 0.6 for _ in range(cols)] for _ in range(rows)]
            else:
                # 演化一代
                old = state['cells']
                new_cells = [[False] * cols for _ in range(rows)]
                for r in range(rows):
                    for c in range(cols):
                        neighbors = 0
                        for dr in [-1, 0, 1]:
                            for dc in [-1, 0, 1]:
                                if dr == 0 and dc == 0:
                                    continue
                                nr, nc = (r + dr) % rows, (c + dc) % cols
                                if old[nr][nc]:
                                    neighbors += 1
                        if old[r][c]:
                            new_cells[r][c] = neighbors in (2, 3)
                        else:
                            new_cells[r][c] = neighbors == 3
                state['cells'] = new_cells
                # 如果全死或太稳定，重新随机
                alive = sum(sum(row) for row in new_cells)
                if alive == 0 or alive > rows * cols * 0.8:
                    state['cells'] = [[random.random() > 0.6 for _ in range(cols)] for _ in range(rows)]
            state['gen_t'] = self._t
        cells = state['cells']
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            col, row = grid.get_col_row(i)
            row = min(row, len(cells) - 1)
            col = min(col, len(cells[0]) - 1) if cells else 0
            weights[fid] = 1.0 if cells[row][col] else 0.0
        return weights

    def _compute_meteor(self) -> Dict[str, float]:
        """流星雨：随机生成从顶部落下的流星，带尾迹"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        cols, rows = grid.cols, grid.rows
        state = self._effect_state
        if 'meteors' not in state:
            state['meteors'] = []
        # 随机生成新流星
        if random.random() < 0.1:
            state['meteors'].append({
                'col': random.randint(0, cols - 1),
                'row': -1.0,
                'speed': random.uniform(0.5, 2.0),
                'tail': random.randint(2, 5),
            })
        # 更新流星位置
        new_meteors = []
        for m in state['meteors']:
            m['row'] += m['speed'] * 0.2
            if m['row'] < rows + m['tail']:
                new_meteors.append(m)
        state['meteors'] = new_meteors
        # 渲染
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            col, row = grid.get_col_row(i)
            w = 0.0
            for m in state['meteors']:
                if col == m['col']:
                    dist = m['row'] - row
                    if 0 <= dist < m['tail']:
                        w = max(w, 1.0 - dist / m['tail'])
            weights[fid] = min(1.0, w)
        return weights

    def _compute_scan(self) -> Dict[str, float]:
        """扫描线：水平/垂直扫描线来回移动"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        t = self._t
        # 扫描位置 (0~1 来回)
        scan_pos = abs((t * 0.5) % 2.0 - 1.0)
        scan_width = 0.15
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            nx, ny = grid.get_norm_pos(i)
            # 水平扫描
            dist = abs(nx - scan_pos)
            w = max(0.0, 1.0 - dist / scan_width)
            weights[fid] = w
        return weights

    def _compute_breathe(self) -> Dict[str, float]:
        """呼吸脉动：从中心向外的呼吸式脉动"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        t = self._t
        # 呼吸相位
        breath = (math.sin(t * 1.5) + 1.0) / 2.0
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            dist = grid.distance_to_center(i) if grid else 0.5
            # 呼吸半径
            radius = breath * 1.2
            w = max(0.0, 1.0 - abs(dist - radius) * 4)
            weights[fid] = min(1.0, w + breath * 0.2)
        return weights

    def _compute_firework(self) -> Dict[str, float]:
        """烟花：随机爆炸点，粒子向外扩散并衰减"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        state = self._effect_state
        if 'explosions' not in state:
            state['explosions'] = []
        # 随机生成爆炸
        if random.random() < 0.05:
            state['explosions'].append({
                'cx': random.uniform(0.2, 0.8),
                'cy': random.uniform(0.2, 0.8),
                'age': 0.0,
                'max_age': random.uniform(1.0, 2.5),
            })
        # 更新爆炸
        new_exp = []
        for e in state['explosions']:
            e['age'] += 0.04
            if e['age'] < e['max_age']:
                new_exp.append(e)
        state['explosions'] = new_exp
        # 渲染
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            nx, ny = grid.get_norm_pos(i)
            w = 0.0
            for e in state['explosions']:
                dist = math.sqrt((nx - e['cx'])**2 + (ny - e['cy'])**2)
                progress = e['age'] / e['max_age']
                ring_radius = progress * 0.6
                ring_width = 0.1 * (1.0 - progress)
                if abs(dist - ring_radius) < ring_width:
                    w = max(w, 1.0 - progress)
            weights[fid] = min(1.0, w)
        return weights

    def _compute_tetris(self) -> Dict[str, float]:
        """俄罗斯方块：随机方块从顶部落下并堆叠"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        cols, rows = grid.cols, grid.rows
        state = self._effect_state
        if 'board' not in state:
            state['board'] = [[False] * cols for _ in range(rows)]
            state['piece_col'] = random.randint(0, max(0, cols - 2))
            state['piece_row'] = -1.0
            state['clear_timer'] = 0
        # 移动方块
        state['piece_row'] += 0.15
        pr = int(state['piece_row'])
        pc = state['piece_col']
        # 检查着陆
        if pr >= rows - 1 or (pr >= 0 and pr < rows - 1 and state['board'][pr + 1][pc]):
            if 0 <= pr < rows:
                state['board'][pr][pc] = True
                if pc + 1 < cols:
                    state['board'][pr][pc + 1] = True
            state['piece_col'] = random.randint(0, max(0, cols - 2))
            state['piece_row'] = -1.0
            # 检查满行清除
            for r in range(rows):
                if all(state['board'][r]):
                    state['board'].pop(r)
                    state['board'].insert(0, [False] * cols)
            # 如果堆满了，清空
            if any(state['board'][0]):
                state['board'] = [[False] * cols for _ in range(rows)]
        # 渲染
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            col, row = grid.get_col_row(i)
            row = min(row, rows - 1)
            col = min(col, cols - 1)
            w = 0.0
            if state['board'][row][col]:
                w = 1.0
            # 当前下落方块
            if row == pr and col in (pc, pc + 1 if pc + 1 < cols else pc):
                w = 1.0
            weights[fid] = w
        return weights


    # ─────────────────────────────────────────────────────────────────────────
    #  v26 新增音频响应效果
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_audio_wave_fire(self) -> Dict[str, float]:
        """音频响应火焰：火焰高度和强度随音频频段实时变化。
        低频驱动火焰底部强度，高频驱动火焰尖端闪烁。"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        bands = self._get_audio_bands()
        rms = self._get_audio_rms()
        cols, rows = max(1, grid.cols), max(1, grid.rows)
        state = self._effect_state
        t = self._t

        # 初始化/更新火焰网格
        if 'fire_grid' not in state or state.get('fc') != cols or state.get('fr') != rows:
            state['fire_grid'] = np.zeros((rows, cols), dtype=np.float32)
            state['fc'], state['fr'] = cols, rows

        fg = state['fire_grid']
        # 底部点火：每列的火焰强度由对应频段驱动
        for c in range(cols):
            band_idx = min(len(bands) - 1, int(c * len(bands) / cols))
            base_heat = min(1.0, bands[band_idx] * self.audio_amplitude * 1.5)
            # 加入随机抖动
            fg[rows - 1, c] = base_heat * random.uniform(0.7, 1.0)

        # 向上传播 + 冷却
        new_fg = np.zeros_like(fg)
        for r in range(rows - 1):
            for c in range(cols):
                below = fg[r + 1, c]
                left = fg[r + 1, max(0, c - 1)]
                right = fg[r + 1, min(cols - 1, c + 1)]
                avg = (below * 2.0 + left + right) / 4.0
                # 冷却率随高度增加，高频增加尖端闪烁
                hi_freq = min(1.0, (bands[-1] + bands[-2]) * self.audio_amplitude) if len(bands) >= 2 else 0.0
                cool = random.uniform(0.06, 0.18) - hi_freq * 0.03
                flicker = math.sin(t * 15 + c * 2.7 + r * 1.3) * 0.05 * rms
                new_fg[r, c] = max(0.0, avg - cool + flicker)
        new_fg[rows - 1] = fg[rows - 1]
        state['fire_grid'] = new_fg

        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            col, row = grid.get_col_row(i)
            col = min(col, cols - 1)
            row = min(row, rows - 1)
            weights[fid] = float(np.clip(new_fg[row, col], 0.0, 1.0))
        return weights

    def _compute_audio_rain_drops(self) -> Dict[str, float]:
        """音频响应雨滴：雨滴密度和速度随音频能量变化。
        节拍时产生密集雨滴，安静时雨滴稀疏。"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        rms = self._get_audio_rms()
        is_beat = self._get_is_beat()
        bands = self._get_audio_bands()
        cols, rows = grid.cols, grid.rows
        state = self._effect_state

        if 'drops' not in state:
            state['drops'] = []  # [{col, row, speed, brightness}]

        # 生成新雨滴：概率随RMS增加
        spawn_prob = 0.02 + rms * 0.3
        if is_beat:
            spawn_prob = 0.6  # 节拍时大量生成
        for c in range(cols):
            if random.random() < spawn_prob:
                band_idx = min(len(bands) - 1, int(c * len(bands) / cols))
                speed = 0.3 + bands[band_idx] * self.audio_amplitude * 2.0
                state['drops'].append({
                    'col': c, 'row': -random.uniform(0, 2),
                    'speed': speed,
                    'brightness': 0.5 + rms * 0.5,
                })

        # 更新雨滴位置
        new_drops = []
        for d in state['drops']:
            d['row'] += d['speed'] * 0.15
            if d['row'] < rows + 3:
                new_drops.append(d)
        state['drops'] = new_drops[-200:]  # 限制最大数量

        # 渲染
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            col, row = grid.get_col_row(i)
            w = 0.0
            for d in state['drops']:
                if d['col'] == col:
                    dist = abs(row - d['row'])
                    if dist < 2.0:
                        trail = max(0.0, 1.0 - dist / 2.0)
                        w = max(w, trail * d['brightness'])
            weights[fid] = min(1.0, w)
        return weights

    def _compute_audio_ripple_pond(self) -> Dict[str, float]:
        """音频水面涟漪：节拍时在随机位置产生涟漪，3-5个设备组成涟漪中心。
        涟漪扩散速度和强度随RMS变化。"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        rms = self._get_audio_rms()
        is_beat = self._get_is_beat()
        state = self._effect_state
        n = len(self._sorted_ids)

        if 'ripples' not in state:
            state['ripples'] = []  # [{cx, cy, age, max_age, strength}]

        # 节拍或高能量时产生新涟漪
        if is_beat or (rms > 0.5 and random.random() < 0.15):
            # 随机选3-5个相邻设备作为涟漪中心
            center_idx = random.randint(0, max(0, n - 1))
            cx, cy = grid.get_norm_pos(center_idx)
            state['ripples'].append({
                'cx': cx, 'cy': cy,
                'age': 0.0,
                'max_age': 1.5 + rms * 2.0,
                'strength': 0.5 + rms * 0.5,
                'freq': 6.0 + random.uniform(-1, 1),
            })

        # 更新涟漪
        new_ripples = []
        for r in state['ripples']:
            r['age'] += 0.04
            if r['age'] < r['max_age']:
                new_ripples.append(r)
        state['ripples'] = new_ripples[-15:]  # 限制最大涟漪数

        # 渲染
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            nx, ny = grid.get_norm_pos(i)
            w = 0.0
            for rip in state['ripples']:
                dist = math.sqrt((nx - rip['cx'])**2 + (ny - rip['cy'])**2)
                progress = rip['age'] / rip['max_age']
                ring_r = progress * 0.8
                # 涟漪环
                ring_dist = abs(dist - ring_r)
                ring_width = 0.08 * (1.0 - progress)
                if ring_dist < ring_width:
                    wave = math.cos(ring_dist / max(0.001, ring_width) * math.pi * 0.5)
                    w += wave * rip['strength'] * (1.0 - progress)
            weights[fid] = min(1.0, max(0.0, w))
        return weights

    def _compute_audio_flame_wall(self) -> Dict[str, float]:
        """音频火焰墙：底部火焰高度严格按频段分列，形成均衡器式火焰墙。"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        bands = self._get_audio_bands()
        rms = self._get_audio_rms()
        cols, rows = max(1, grid.cols), max(1, grid.rows)
        t = self._t

        # 每列火焰高度由频段决定
        col_heights = []
        for c in range(cols):
            band_idx = min(len(bands) - 1, int(c * len(bands) / cols))
            h = min(1.0, bands[band_idx] * self.audio_amplitude * 1.2)
            # 加入微小波动
            h += math.sin(t * 8 + c * 1.7) * 0.05 * rms
            col_heights.append(max(0.0, h))

        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            col, row = grid.get_col_row(i)
            col = min(col, cols - 1)
            norm_row = 1.0 - row / max(1, rows - 1)  # 0=顶部, 1=底部
            flame_h = col_heights[col]
            if norm_row <= flame_h:
                # 越接近火焰顶部越暗（渐变）
                gradient = 1.0 - (flame_h - norm_row) / max(0.01, flame_h) * 0.4
                flicker = random.uniform(0.85, 1.0)
                w = gradient * flicker
            else:
                w = 0.0
            weights[fid] = min(1.0, max(0.0, w))
        return weights

    def _compute_audio_waterfall(self) -> Dict[str, float]:
        """音频瀑布：频谱数据从顶部向下流动，形成瀑布效果。
        每帧将当前频谱推入顶行，历史数据向下滚动。"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        bands = self._get_audio_bands()
        cols, rows = max(1, grid.cols), max(1, grid.rows)
        state = self._effect_state

        if 'waterfall' not in state or state.get('wc') != cols or state.get('wr') != rows:
            state['waterfall'] = np.zeros((rows, cols), dtype=np.float32)
            state['wc'], state['wr'] = cols, rows

        wf = state['waterfall']
        # 向下滚动
        wf[1:, :] = wf[:-1, :]
        # 顶行填入当前频谱
        for c in range(cols):
            band_idx = min(len(bands) - 1, int(c * len(bands) / cols))
            wf[0, c] = min(1.0, bands[band_idx] * self.audio_amplitude)

        # 自然衰减
        wf *= 0.97

        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            col, row = grid.get_col_row(i)
            col = min(col, cols - 1)
            row = min(row, rows - 1)
            weights[fid] = float(np.clip(wf[row, col], 0.0, 1.0))
        return weights

    def _compute_audio_heartbeat_sync(self) -> Dict[str, float]:
        """BPM同步心跳：心跳节律锁定到检测到的BPM。
        从中心向外扩散的脉冲，速率与音乐节拍同步。"""
        rms = self._get_audio_rms()
        is_beat = self._get_is_beat()
        state = self._effect_state
        grid = self._grid

        if 'hb_phase' not in state:
            state['hb_phase'] = 0.0
            state['hb_intensity'] = 0.0

        # 节拍触发心跳
        if is_beat:
            state['hb_phase'] = 0.0
            state['hb_intensity'] = 0.8 + rms * 0.2
        else:
            state['hb_phase'] += 0.04

        phase = state['hb_phase']
        intensity = state['hb_intensity']

        # 双峰心跳曲线
        if phase < 0.1:
            beat_val = phase / 0.1 * intensity
        elif phase < 0.25:
            beat_val = intensity * (1.0 - (phase - 0.1) / 0.15)
        elif phase < 0.35:
            beat_val = (phase - 0.25) / 0.1 * intensity * 0.6
        elif phase < 0.5:
            beat_val = intensity * 0.6 * (1.0 - (phase - 0.35) / 0.15)
        else:
            beat_val = 0.0

        # 空间扩散：从中心向外
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            if grid:
                d = grid.distance_to_center(i)
            else:
                d = abs(i / max(1, len(self._sorted_ids) - 1) - 0.5) * 2
            # 脉冲环扩散
            ring_pos = phase * 1.5
            ring_dist = abs(d - ring_pos)
            spatial = max(0.0, 1.0 - ring_dist * 5.0)
            w = beat_val * (0.3 + 0.7 * spatial)
            weights[fid] = min(1.0, max(0.0, w))
        return weights

    def _compute_audio_strobe_multi(self) -> Dict[str, float]:
        """多区域频闪：将矩阵分成多个区域，每个区域响应不同频段。
        高能量频段对应区域快速频闪。"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        bands = self._get_audio_bands()
        t = self._t
        n_zones = min(8, len(bands))

        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            nx, ny = grid.get_norm_pos(i)
            # 将空间分成区域（按角度）
            cx, cy = nx - 0.5, ny - 0.5
            angle = (math.atan2(cy, cx) / (2 * math.pi) + 0.5) % 1.0
            zone = min(n_zones - 1, int(angle * n_zones))
            energy = min(1.0, bands[zone] * self.audio_amplitude)
            # 频闪：能量越高频率越快
            strobe_freq = 5.0 + energy * 20.0
            strobe = (math.sin(t * strobe_freq + zone * 1.5) + 1.0) / 2.0
            w = energy * strobe
            weights[fid] = min(1.0, w)
        return weights

    def _compute_audio_spiral_pulse(self) -> Dict[str, float]:
        """音频螺旋脉冲：螺旋速度随BPM，半径随RMS脉动。"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        rms = self._get_audio_rms()
        is_beat = self._get_is_beat()
        bands = self._get_audio_bands()
        t = self._t
        state = self._effect_state

        if 'sp_pulse' not in state:
            state['sp_pulse'] = 0.0
        if is_beat:
            state['sp_pulse'] = 1.0
        else:
            state['sp_pulse'] *= 0.92

        pulse = state['sp_pulse']
        bass = min(1.0, (bands[0] + bands[1]) * self.audio_amplitude) if len(bands) >= 2 else rms

        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            nx, ny = grid.get_norm_pos(i)
            cx, cy = nx - 0.5, ny - 0.5
            angle = math.atan2(cy, cx)
            dist = math.sqrt(cx**2 + cy**2)
            # 螺旋相位：角度 + 距离 - 时间
            spiral = angle + dist * 6.0 - t * (2.0 + rms * 3.0)
            wave = (math.sin(spiral) + 1.0) / 2.0
            # 脉冲调制半径
            radius_mod = 0.3 + pulse * 0.4 + bass * 0.3
            radial = max(0.0, 1.0 - abs(dist - radius_mod) * 4.0)
            w = wave * 0.5 + radial * 0.5 * pulse
            weights[fid] = min(1.0, max(0.0, w))
        return weights

    def _compute_audio_diamond_wave(self) -> Dict[str, float]:
        """音频菱形波：从中心扩散的菱形波纹，beat触发新波。"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        is_beat = self._get_is_beat()
        rms = self._get_audio_rms()
        state = self._effect_state

        if 'diamonds' not in state:
            state['diamonds'] = []  # [{age, strength}]

        if is_beat or (rms > 0.6 and random.random() < 0.1):
            state['diamonds'].append({'age': 0.0, 'strength': 0.6 + rms * 0.4})

        new_d = []
        for d in state['diamonds']:
            d['age'] += 0.04
            if d['age'] < 2.0:
                new_d.append(d)
        state['diamonds'] = new_d[-8:]

        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            nx, ny = grid.get_norm_pos(i)
            w = 0.0
            for d in state['diamonds']:
                # 菱形距离 = |dx| + |dy|
                diamond_dist = abs(nx - 0.5) + abs(ny - 0.5)
                progress = d['age'] / 2.0
                ring_r = progress * 1.2
                ring_w_size = 0.12 * (1.0 - progress)
                if abs(diamond_dist - ring_r) < ring_w_size:
                    w += d['strength'] * (1.0 - progress)
            weights[fid] = min(1.0, max(0.0, w))
        return weights

    def _compute_audio_vu_meter(self) -> Dict[str, float]:
        """VU音量表：经典左右对称音量表，中间为零点，向两侧延伸。
        高度表示频段，宽度表示能量。"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        bands = self._get_audio_bands()
        rms = self._get_audio_rms()
        cols, rows = max(1, grid.cols), max(1, grid.rows)

        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            col, row = grid.get_col_row(i)
            # 行映射到频段
            band_idx = min(len(bands) - 1, int(row * len(bands) / rows))
            energy = min(1.0, bands[band_idx] * self.audio_amplitude)
            # 列：从中心向两侧
            center_col = cols / 2.0
            col_dist = abs(col - center_col) / max(1, center_col)
            # VU表：能量越大，点亮越远
            w = 1.0 if col_dist <= energy else 0.0
            # 软边缘
            if col_dist > energy * 0.8 and col_dist <= energy:
                w = (energy - col_dist) / max(0.01, energy * 0.2)
            weights[fid] = min(1.0, max(0.0, w))
        return weights

    def _compute_audio_vu_meter_array(self) -> np.ndarray:
        grid = self._grid
        n = len(self._sorted_ids)
        if grid is None:
            return np.zeros(n, dtype=np.float32)
        bands = np.asarray(self._get_audio_bands(), dtype=np.float32)
        if bands.size == 0:
            return np.zeros(n, dtype=np.float32)
        cols, rows = max(1, grid.cols), max(1, grid.rows)
        col = np.minimum(grid.col_np, cols - 1).astype(np.float32)
        row = np.minimum(grid.row_np, rows - 1)
        band_idx = np.minimum(bands.size - 1, (row.astype(np.float32) * bands.size / rows).astype(np.int32))
        energy = np.clip(bands[band_idx] * float(self.audio_amplitude), 0.0, 1.0)
        center_col = cols / 2.0
        col_dist = np.abs(col - center_col) / max(1.0, center_col)
        w = (col_dist <= energy).astype(np.float32)
        soft = (col_dist > energy * 0.8) & (col_dist <= energy)
        w[soft] = (energy[soft] - col_dist[soft]) / np.maximum(0.01, energy[soft] * 0.2)
        return np.clip(w, 0.0, 1.0)

    def _compute_audio_eq_bars(self) -> Dict[str, float]:
        """均衡器柱状图：每列一个频段，带峰值保持和缓慢下落。"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        cols, rows = max(1, grid.cols), max(1, grid.rows)
        col_np = np.arange(cols * rows, dtype=np.int32) % cols
        row_np = np.arange(cols * rows, dtype=np.int32) // cols
        axis_count, _, _, tol = self._audio_bar_axes_array(
            cols, rows, col_np, row_np, self._audio_bar_direction(), style=self._audio_bar_style())
        levels = self._audio_bar_levels(
            self._get_audio_bands(), axis_count, layout=self._audio_band_layout(),
            state_key="eq_energy")
        peaks = self._update_audio_eq_peaks(levels)
        style = self._audio_bar_style()
        direction = self._audio_bar_direction()
        state = self._effect_state
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            col, row = grid.get_col_row(i)
            axis_count, axis_idx_arr, threshold_arr, _tol = self._audio_bar_axes_array(
                cols, rows, np.asarray([col], dtype=np.int32), np.asarray([row], dtype=np.int32),
                direction, style=style)
            axis_idx = int(axis_idx_arr[0])
            threshold = float(threshold_arr[0])
            energy = float(levels[min(axis_idx, len(levels) - 1)])
            body = float(self._audio_bar_fill_from_energy(
                np.asarray([energy], dtype=np.float32),
                np.asarray([threshold], dtype=np.float32),
                np.asarray([axis_idx], dtype=np.int32), _tol, style=style)[0])
            peak = float(peaks[min(axis_idx, len(peaks) - 1)])
            w = max(body * 0.9, 1.0 if abs(threshold - peak) < max(tol, _tol) else 0.0)
            weights[fid] = min(1.0, max(0.0, w))
        return weights

    def _compute_audio_eq_bars_array(self) -> np.ndarray:
        grid = self._grid
        n = len(self._sorted_ids)
        if grid is None:
            return np.zeros(n, dtype=np.float32)
        bands = np.asarray(self._get_audio_bands(), dtype=np.float32)
        if bands.size == 0:
            return np.zeros(n, dtype=np.float32)
        cols, rows = max(1, grid.cols), max(1, grid.rows)
        col = np.minimum(grid.col_np, cols - 1)
        row = np.minimum(grid.row_np, rows - 1)
        style = self._audio_bar_style()
        axis_count, axis_idx, threshold, tol = self._audio_bar_axes_array(
            cols, rows, col, row, self._audio_bar_direction(), style=style)
        levels = self._audio_bar_levels(
            bands, axis_count, layout=self._audio_band_layout(), state_key="eq_energy")
        peaks = self._update_audio_eq_peaks(levels)
        energy = levels[axis_idx]
        body = self._audio_bar_fill_from_energy(energy, threshold, axis_idx, tol, style=style)
        peak = np.asarray(peaks, dtype=np.float32)[axis_idx]
        peak_mask = np.abs(threshold - peak) < tol
        out = np.maximum(body * 0.9, peak_mask.astype(np.float32))
        return np.clip(out, 0.0, 1.0)

    def _compute_audio_waveform_mirror(self) -> Dict[str, float]:
        """镜像波形：上下镜像的波形显示，中线为零点。
        波形振幅随音频实时变化。"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        bands = self._get_audio_bands()
        rms = self._get_audio_rms()
        cols, rows = max(1, grid.cols), max(1, grid.rows)
        t = self._t

        # 每列的振幅
        col_amp = []
        for c in range(cols):
            band_idx = min(len(bands) - 1, int(c * len(bands) / cols))
            amp = min(0.5, bands[band_idx] * self.audio_amplitude * 0.5)
            # 加入波动
            amp *= (1.0 + 0.3 * math.sin(t * 4 + c * 0.8))
            col_amp.append(max(0.0, amp))

        weights = {}
        mid = 0.5  # 中线
        for i, fid in enumerate(self._sorted_ids):
            col, row = grid.get_col_row(i)
            col = min(col, cols - 1)
            norm_row = row / max(1, rows - 1)
            amp = col_amp[col]
            # 到中线的距离
            dist_to_mid = abs(norm_row - mid)
            if dist_to_mid <= amp:
                w = 1.0 - dist_to_mid / max(0.01, amp) * 0.3
            else:
                w = 0.0
            weights[fid] = min(1.0, max(0.0, w))
        return weights

    def _compute_audio_waveform_mirror_array(self) -> np.ndarray:
        grid = self._grid
        n = len(self._sorted_ids)
        if grid is None:
            return np.zeros(n, dtype=np.float32)
        bands = np.asarray(self._get_audio_bands(), dtype=np.float32)
        if bands.size == 0:
            return np.zeros(n, dtype=np.float32)
        cols, rows = max(1, grid.cols), max(1, grid.rows)
        cidx = np.arange(cols, dtype=np.float32)
        band_idx = np.minimum(bands.size - 1, (cidx * bands.size / cols).astype(np.int32))
        col_amp = np.clip(bands[band_idx] * float(self.audio_amplitude) * 0.5, 0.0, 0.5)
        col_amp *= (1.0 + 0.3 * np.sin(float(self._t) * 4.0 + cidx * 0.8))
        col_amp = np.maximum(0.0, col_amp).astype(np.float32)
        col = np.minimum(grid.col_np, cols - 1)
        row = np.minimum(grid.row_np, rows - 1)
        norm_row = row.astype(np.float32) / max(1, rows - 1)
        amp = col_amp[col]
        dist_to_mid = np.abs(norm_row - 0.5)
        out = np.zeros(n, dtype=np.float32)
        active = dist_to_mid <= amp
        out[active] = 1.0 - dist_to_mid[active] / np.maximum(0.01, amp[active]) * 0.3
        return np.clip(out, 0.0, 1.0)

    def _compute_audio_particle_burst(self) -> Dict[str, float]:
        """音频粒子爆发：beat时从随机点爆发粒子，粒子向外扩散并衰减。"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        rms = self._get_audio_rms()
        is_beat = self._get_is_beat()
        state = self._effect_state

        if 'particles' not in state:
            state['particles'] = []  # [{x, y, vx, vy, life, max_life}]

        # beat时爆发粒子
        if is_beat:
            cx = random.uniform(0.2, 0.8)
            cy = random.uniform(0.2, 0.8)
            n_particles = random.randint(8, 20)
            for _ in range(n_particles):
                angle = random.uniform(0, 2 * math.pi)
                speed = random.uniform(0.1, 0.5) * (0.5 + rms)
                state['particles'].append({
                    'x': cx, 'y': cy,
                    'vx': math.cos(angle) * speed,
                    'vy': math.sin(angle) * speed,
                    'life': 0.0,
                    'max_life': random.uniform(0.8, 2.0),
                })

        # 更新粒子
        new_particles = []
        for p in state['particles']:
            p['x'] += p['vx'] * 0.04
            p['y'] += p['vy'] * 0.04
            p['vx'] *= 0.97  # 阻力
            p['vy'] *= 0.97
            p['life'] += 0.04
            if p['life'] < p['max_life']:
                new_particles.append(p)
        state['particles'] = new_particles[-150:]

        # 渲染
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            nx, ny = grid.get_norm_pos(i)
            w = 0.0
            for p in state['particles']:
                dist = math.sqrt((nx - p['x'])**2 + (ny - p['y'])**2)
                if dist < 0.08:
                    fade = 1.0 - p['life'] / p['max_life']
                    w = max(w, fade * (1.0 - dist / 0.08))
            weights[fid] = min(1.0, w)
        return weights

    def _compute_audio_gravity_drops(self) -> Dict[str, float]:
        """重力音频滴：频段能量转化为从顶部掉落的滴，带重力加速度和弹跳。"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        bands = self._get_audio_bands()
        cols, rows = max(1, grid.cols), max(1, grid.rows)
        state = self._effect_state

        if 'gdrops' not in state:
            state['gdrops'] = []  # [{col, row, vy, brightness}]

        # 每列根据频段能量生成新滴
        for c in range(cols):
            band_idx = min(len(bands) - 1, int(c * len(bands) / cols))
            energy = bands[band_idx] * self.audio_amplitude
            if energy > 0.3 and random.random() < energy * 0.15:
                state['gdrops'].append({
                    'col': c, 'row': 0.0, 'vy': 0.0,
                    'brightness': min(1.0, energy),
                })

        # 更新：重力加速度
        gravity = 0.015
        new_drops = []
        for d in state['gdrops']:
            d['vy'] += gravity
            d['row'] += d['vy']
            # 底部弹跳
            if d['row'] >= rows - 1:
                d['row'] = rows - 1
                d['vy'] = -d['vy'] * 0.5  # 弹跳衰减
                d['brightness'] *= 0.7
            if d['brightness'] > 0.05:
                new_drops.append(d)
        state['gdrops'] = new_drops[-100:]

        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            col, row = grid.get_col_row(i)
            w = 0.0
            for d in state['gdrops']:
                if d['col'] == col:
                    dist = abs(row - d['row'])
                    if dist < 1.5:
                        w = max(w, d['brightness'] * (1.0 - dist / 1.5))
            weights[fid] = min(1.0, w)
        return weights

    def _compute_audio_cross_wave(self) -> Dict[str, float]:
        """交叉波：X形交叉的两组波，频率和振幅随音频变化。"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        bands = self._get_audio_bands()
        rms = self._get_audio_rms()
        t = self._t

        bass = min(1.0, (bands[0] + bands[1]) * self.audio_amplitude) if len(bands) >= 2 else rms
        treble = min(1.0, (bands[-1] + bands[-2]) * self.audio_amplitude) if len(bands) >= 2 else rms

        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            nx, ny = grid.get_norm_pos(i)
            # 对角线1：左上→右下
            d1 = nx - ny
            wave1 = (math.sin(d1 * 8.0 * math.pi - t * (2.0 + bass * 4.0)) + 1.0) / 2.0
            # 对角线2：右上→左下
            d2 = nx + ny - 1.0
            wave2 = (math.sin(d2 * 8.0 * math.pi - t * (2.0 + treble * 4.0)) + 1.0) / 2.0
            # 叠加
            w = max(wave1 * bass, wave2 * treble)
            weights[fid] = min(1.0, max(0.0, w))
        return weights

    def _compute_audio_constellation(self) -> Dict[str, float]:
        """音频星座：随机星点亮度随频段脉动，beat时所有星闪烁。"""
        n = len(self._sorted_ids)
        if n == 0:
            return {}
        bands = self._get_audio_bands()
        rms = self._get_audio_rms()
        is_beat = self._get_is_beat()
        state = self._effect_state
        t = self._t

        # 初始化星座：每个设备分配一个频段和基础亮度
        if 'stars' not in state:
            state['stars'] = []
            for i in range(n):
                state['stars'].append({
                    'band': random.randint(0, min(7, len(bands) - 1)),
                    'base': random.uniform(0.1, 0.4),
                    'phase': random.uniform(0, 2 * math.pi),
                    'twinkle_speed': random.uniform(1.0, 4.0),
                })
            state['flash'] = 0.0

        if is_beat:
            state['flash'] = 1.0
        else:
            state['flash'] *= 0.85

        flash = state['flash']
        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            star = state['stars'][i] if i < len(state['stars']) else {'band': 0, 'base': 0.2, 'phase': 0, 'twinkle_speed': 2.0}
            band_energy = min(1.0, bands[star['band']] * self.audio_amplitude)
            # 闪烁
            twinkle = (math.sin(t * star['twinkle_speed'] + star['phase']) + 1.0) / 2.0
            w = star['base'] + band_energy * 0.5 * twinkle + flash * 0.4
            weights[fid] = min(1.0, max(0.0, w))
        return weights

    def _compute_audio_dna_helix(self) -> Dict[str, float]:
        """DNA双螺旋：两条螺旋线沿矩阵纵向旋转，间距和速度随音频变化。"""
        grid = self._grid
        if grid is None:
            return {fid: 0.0 for fid in self._sorted_ids}
        bands = self._get_audio_bands()
        rms = self._get_audio_rms()
        cols, rows = max(1, grid.cols), max(1, grid.rows)
        t = self._t

        bass = min(1.0, (bands[0] + bands[1]) * self.audio_amplitude) if len(bands) >= 2 else rms
        mid_freq = min(1.0, (bands[3] + bands[4]) * self.audio_amplitude) if len(bands) >= 5 else rms

        weights = {}
        for i, fid in enumerate(self._sorted_ids):
            col, row = grid.get_col_row(i)
            norm_row = row / max(1, rows - 1)
            norm_col = col / max(1, cols - 1)

            # 螺旋1的x位置
            helix1_x = 0.5 + 0.3 * math.sin(norm_row * 4.0 * math.pi - t * (1.5 + bass * 2.0))
            # 螺旋2的x位置（相位偏移π）
            helix2_x = 0.5 + 0.3 * math.sin(norm_row * 4.0 * math.pi - t * (1.5 + bass * 2.0) + math.pi)

            # 间距随中频变化
            width = 0.08 + mid_freq * 0.06

            dist1 = abs(norm_col - helix1_x)
            dist2 = abs(norm_col - helix2_x)

            w1 = max(0.0, 1.0 - dist1 / width) if dist1 < width else 0.0
            w2 = max(0.0, 1.0 - dist2 / width) if dist2 < width else 0.0

            # 连接横档（每隔几行）
            bar_period = max(2, rows // 4)
            if row % bar_period == 0:
                bar_left = min(helix1_x, helix2_x)
                bar_right = max(helix1_x, helix2_x)
                if bar_left <= norm_col <= bar_right:
                    w1 = max(w1, 0.6 * rms)

            weights[fid] = min(1.0, max(w1, w2))
        return weights
