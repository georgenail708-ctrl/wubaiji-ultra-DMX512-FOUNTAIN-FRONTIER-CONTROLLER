"""
screen_capture_engine.py — 屏幕实时采样引擎（完整实现）
=========================================================
功能：
  - 实时读取屏幕指定区域（或全屏）的像素数据
  - 将采样区域分成 cols×rows 的逻辑网格
  - 对每个网格格子计算亮度（灰度）、R/G/B 各分量、饱和度、运动量
  - 输出归一化 [0.0, 1.0] 的多维亮度矩阵
  - 支持时间平滑滤波（防止输出抖动）
  - 支持对比度拉伸、亮度偏移、Gamma 校正
  - 提供实时预览图（QPixmap）供 UI 显示
  - 后台线程采样，主线程通过 get_latest() 获取最新结果

截图后端优先级：
  1. mss（最快，推荐安装：pip install mss）
  2. PIL.ImageGrab（Pillow 内置，跨平台降级方案）

线程模型：
  ScreenCaptureEngine 在后台线程中持续采样，
  每次采样完成后更新内部缓存（线程安全），
  主线程（UI tick）调用 get_latest_weights() 获取最新结果。

使用方式：
    engine = ScreenCaptureEngine()
    engine.set_region({"left": 0, "top": 0, "width": 1920, "height": 1080})
    engine.set_grid(cols=16, rows=9)
    engine.set_params(brightness=1.2, contrast=1.5, gamma=1.0,
                      smooth=0.7, channel="luminance")
    engine.start()
    # 每帧：
    weights = engine.get_latest_weights()   # Dict[int, float]  (row*cols+col → weight)
    preview_pixmap = engine.get_preview_pixmap(w, h)  # QPixmap 预览图
    engine.stop()
"""
from __future__ import annotations

import math
import threading
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

# ─── 采样通道模式 ─────────────────────────────────────────────────────────────
CHANNEL_LUMINANCE  = "luminance"   # 亮度（灰度，综合 RGB）
CHANNEL_RED        = "red"         # 红色分量
CHANNEL_GREEN      = "green"       # 绿色分量
CHANNEL_BLUE       = "blue"        # 蓝色分量
CHANNEL_SATURATION = "saturation"  # 饱和度
CHANNEL_MOTION     = "motion"      # 运动量（帧差）
CHANNEL_HUE        = "hue"         # 色调（0=红, 0.33=绿, 0.67=蓝）

CHANNEL_MODES = [
    (CHANNEL_LUMINANCE,  "亮度（灰度）"),
    (CHANNEL_RED,        "红色分量"),
    (CHANNEL_GREEN,      "绿色分量"),
    (CHANNEL_BLUE,       "蓝色分量"),
    (CHANNEL_SATURATION, "饱和度"),
    (CHANNEL_MOTION,     "运动检测"),
    (CHANNEL_HUE,        "色调"),
]

# ─── 截图后端标识 ─────────────────────────────────────────────────────────────
_BACKEND_MSS = "mss"
_BACKEND_PIL = "pil"
_BACKEND_NONE = "none"


def _detect_backend() -> str:
    """检测可用的截图后端，返回后端标识字符串。"""
    try:
        import mss  # noqa
        return _BACKEND_MSS
    except ImportError:
        pass
    try:
        from PIL import ImageGrab  # noqa
        return _BACKEND_PIL
    except ImportError:
        pass
    return _BACKEND_NONE


def _grab_pil(region: Optional[Dict]) -> np.ndarray:
    """
    使用 PIL.ImageGrab 截图，返回 BGRA 格式的 numpy 数组 (H, W, 4)。
    region: {"left": int, "top": int, "width": int, "height": int} 或 None（全屏）
    """
    from PIL import ImageGrab, Image
    if region is not None and region.get("width", 0) > 0 and region.get("height", 0) > 0:
        bbox = (
            int(region["left"]),
            int(region["top"]),
            int(region["left"]) + int(region["width"]),
            int(region["top"]) + int(region["height"]),
        )
    else:
        bbox = None
    img = ImageGrab.grab(bbox=bbox, all_screens=True)
    img_rgb = img.convert("RGB")
    rgb = np.array(img_rgb, dtype=np.uint8)
    # RGB → BGRA（与 mss 的 BGRA 格式保持一致）
    h, w = rgb.shape[:2]
    bgra = np.zeros((h, w, 4), dtype=np.uint8)
    bgra[:, :, 0] = rgb[:, :, 2]  # B
    bgra[:, :, 1] = rgb[:, :, 1]  # G
    bgra[:, :, 2] = rgb[:, :, 0]  # R
    bgra[:, :, 3] = 255           # A
    return bgra


class ScreenCaptureEngine:
    """
    屏幕实时采样引擎。
    后台线程持续采样，主线程通过 get_latest_weights() 获取。
    截图后端：优先 mss，降级到 PIL.ImageGrab。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 采样参数
        self._region: Optional[Dict] = None   # None = 全屏
        self._monitor_index: int = 1
        self._grid_cols: int = 16
        self._grid_rows: int = 9
        self._channel: str = CHANNEL_LUMINANCE
        self._fps: float = 25.0              # 目标采样帧率
        self._smooth: float = 0.6            # 时间平滑系数 [0=无平滑, 1=完全平滑]
        self._brightness: float = 1.0        # 亮度增益
        self._contrast: float = 1.0          # 对比度
        self._gamma: float = 1.0             # Gamma 校正
        self._threshold_low: float = 0.0     # 输入阈值下限（低于此值视为 0）
        self._threshold_high: float = 1.0    # 输入阈值上限（高于此值视为 1）
        self._invert: bool = False           # 反转输出

        # 内部状态
        self._weights: np.ndarray = np.zeros((self._grid_rows, self._grid_cols))
        self._prev_frame: Optional[np.ndarray] = None  # 上一帧（用于运动检测）
        self._motion_accum: Optional[np.ndarray] = None

        # 预览图缓存（原始采样图，未经阈值处理）
        self._preview_raw: Optional[np.ndarray] = None   # (H, W, 3) uint8 RGB
        self._preview_grid: Optional[np.ndarray] = None  # (rows, cols) float32

        # 可用性检测
        self._backend: str = _BACKEND_NONE
        self._available: bool = False
        self._error_msg: str = ""
        self._check_availability()

    def _check_availability(self):
        self._backend = _detect_backend()
        if self._backend != _BACKEND_NONE:
            self._available = True
            self._error_msg = ""
        else:
            self._available = False
            self._error_msg = "屏幕采样不可用：请安装 mss（pip install mss）或 Pillow（pip install Pillow）"

    @property
    def available(self) -> bool:
        return self._available

    @property
    def error_msg(self) -> str:
        return self._error_msg

    # ── 参数设置 ─────────────────────────────────────────────────────────────
    def set_region(self, region: Optional[Dict]):
        """设置采样区域。None = 全屏主显示器。"""
        with self._lock:
            self._region = region

    def set_monitor(self, index: int):
        """设置显示器索引（1=主显示器）。"""
        with self._lock:
            self._monitor_index = max(1, index)

    def set_grid(self, cols: int, rows: int):
        """设置逻辑网格尺寸。尺寸未变化时不重置权重缓存，避免每帧清零。"""
        new_cols = max(1, cols)
        new_rows = max(1, rows)
        with self._lock:
            if new_cols == self._grid_cols and new_rows == self._grid_rows:
                return  # 尺寸未变，不重置，保留已有权重
            self._grid_cols = new_cols
            self._grid_rows = new_rows
            self._weights = np.zeros((self._grid_rows, self._grid_cols))
            self._prev_frame = None
            self._motion_accum = None

    def set_channel(self, channel: str):
        """设置采样通道模式。"""
        with self._lock:
            self._channel = channel
            if channel != CHANNEL_MOTION:
                self._prev_frame = None
                self._motion_accum = None

    def set_fps(self, fps: float):
        """设置目标采样帧率。"""
        with self._lock:
            self._fps = max(1.0, min(60.0, fps))

    def set_params(self, brightness: float = None, contrast: float = None,
                   gamma: float = None, smooth: float = None,
                   threshold_low: float = None, threshold_high: float = None,
                   invert: bool = None):
        """批量设置参数。"""
        with self._lock:
            if brightness is not None:
                self._brightness = max(0.1, min(5.0, brightness))
            if contrast is not None:
                self._contrast = max(0.1, min(10.0, contrast))
            if gamma is not None:
                self._gamma = max(0.1, min(5.0, gamma))
            if smooth is not None:
                self._smooth = max(0.0, min(0.98, smooth))
            if threshold_low is not None:
                self._threshold_low = max(0.0, min(1.0, threshold_low))
            if threshold_high is not None:
                self._threshold_high = max(0.0, min(1.0, threshold_high))
            if invert is not None:
                self._invert = bool(invert)

    # ── 采样结果获取 ─────────────────────────────────────────────────────────
    def get_latest_weights(self) -> np.ndarray:
        """
        获取最新采样结果，返回 (rows, cols) 的 float32 数组，值域 [0.0, 1.0]。
        线程安全。
        """
        with self._lock:
            return self._weights.copy()

    def get_weight_at(self, row: int, col: int) -> float:
        """获取指定网格格子的权重值。"""
        with self._lock:
            r = max(0, min(self._grid_rows - 1, row))
            c = max(0, min(self._grid_cols - 1, col))
            return float(self._weights[r, c])

    def get_preview_raw(self) -> Optional[np.ndarray]:
        """获取原始采样图（RGB, uint8），可用于绘制预览。"""
        with self._lock:
            return self._preview_raw.copy() if self._preview_raw is not None else None

    def get_preview_grid(self) -> Optional[np.ndarray]:
        """获取网格亮度图（float32, rows×cols）。"""
        with self._lock:
            return self._preview_grid.copy() if self._preview_grid is not None else None

    def get_grid_size(self) -> Tuple[int, int]:
        """返回 (cols, rows)。"""
        with self._lock:
            return self._grid_cols, self._grid_rows

    def list_monitors(self) -> List[Dict]:
        """列出可用显示器。"""
        if self._backend == _BACKEND_MSS:
            try:
                import mss
                with mss.mss() as sct:
                    result = []
                    for i, m in enumerate(sct.monitors[1:], 1):
                        result.append({
                            "index": i,
                            "name": f"显示器 {i}  ({m['width']}×{m['height']})",
                            "left": m["left"], "top": m["top"],
                            "width": m["width"], "height": m["height"],
                        })
                    return result if result else [{"index": 1, "name": "主显示器",
                                                   "left": 0, "top": 0,
                                                   "width": 1920, "height": 1080}]
            except Exception:
                pass
        # PIL 后端或降级：返回主显示器占位
        return [{"index": 1, "name": "主显示器", "left": 0, "top": 0,
                 "width": 1920, "height": 1080}]

    # ── 生命周期 ─────────────────────────────────────────────────────────────
    def start(self):
        if not self._available:
            return
        thread = self._thread
        if thread is not None and thread.is_alive():
            self._running = True
            return
        self._thread = None
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=0.05)
            if not self._thread.is_alive():
                self._thread = None

    def is_running(self) -> bool:
        thread = self._thread
        return bool(self._running and thread is not None and thread.is_alive())

    # ── 后台采样线程 ─────────────────────────────────────────────────────────
    def _run(self):
        import logging
        log = logging.getLogger(__name__)
        if self._backend == _BACKEND_MSS:
            # mss 后端：复用 mss 上下文，性能最优
            try:
                import mss
                with mss.mss() as sct:
                    while self._running:
                        t0 = time.monotonic()
                        try:
                            self._do_sample_mss(sct)
                        except Exception as e:
                            log.warning("ScreenCaptureEngine(mss) sample error: %s", e)
                        elapsed = time.monotonic() - t0
                        with self._lock:
                            fps = self._fps
                        sleep_t = max(0.0, 1.0 / fps - elapsed)
                        if sleep_t > 0:
                            time.sleep(sleep_t)
            except Exception as e:
                log.error("ScreenCaptureEngine(mss) thread error: %s", e)
                self._running = False
        elif self._backend == _BACKEND_PIL:
            # PIL 后端：每帧重新截图
            while self._running:
                t0 = time.monotonic()
                try:
                    self._do_sample_pil()
                except Exception as e:
                    log.warning("ScreenCaptureEngine(pil) sample error: %s", e)
                elapsed = time.monotonic() - t0
                with self._lock:
                    fps = self._fps
                sleep_t = max(0.0, 1.0 / fps - elapsed)
                if sleep_t > 0:
                    time.sleep(sleep_t)

    def _do_sample_mss(self, sct):
        """使用 mss 执行一次采样并更新内部缓存。"""
        with self._lock:
            region = self._region
            monitor_idx = self._monitor_index
            cols = self._grid_cols
            rows = self._grid_rows
            channel = self._channel
            brightness = self._brightness
            contrast = self._contrast
            gamma = self._gamma
            smooth = self._smooth
            thr_lo = self._threshold_low
            thr_hi = self._threshold_high
            invert = self._invert
            prev_weights = self._weights.copy()
            prev_frame = self._prev_frame
            motion_accum = self._motion_accum

        # 确定采样区域
        if region is not None and region.get("width", 0) > 0 and region.get("height", 0) > 0:
            monitor = {
                "left": int(region["left"]),
                "top": int(region["top"]),
                "width": int(region["width"]),
                "height": int(region["height"]),
            }
        else:
            monitors = sct.monitors
            if monitor_idx < len(monitors):
                monitor = monitors[monitor_idx]
            else:
                monitor = monitors[1] if len(monitors) > 1 else monitors[0]

        # 截图
        screenshot = sct.grab(monitor)
        # BGRA → numpy
        arr = np.frombuffer(screenshot.bgra, dtype=np.uint8)
        arr = arr.reshape((screenshot.height, screenshot.width, 4))

        self._process_arr(arr, cols, rows, channel, brightness, contrast, gamma,
                          smooth, thr_lo, thr_hi, invert, prev_weights,
                          prev_frame, motion_accum)

    def _do_sample_pil(self):
        """使用 PIL.ImageGrab 执行一次采样并更新内部缓存。"""
        with self._lock:
            region = self._region
            cols = self._grid_cols
            rows = self._grid_rows
            channel = self._channel
            brightness = self._brightness
            contrast = self._contrast
            gamma = self._gamma
            smooth = self._smooth
            thr_lo = self._threshold_low
            thr_hi = self._threshold_high
            invert = self._invert
            prev_weights = self._weights.copy()
            prev_frame = self._prev_frame
            motion_accum = self._motion_accum

        arr = _grab_pil(region)
        self._process_arr(arr, cols, rows, channel, brightness, contrast, gamma,
                          smooth, thr_lo, thr_hi, invert, prev_weights,
                          prev_frame, motion_accum)

    def _process_arr(self, arr: np.ndarray, cols: int, rows: int,
                     channel: str, brightness: float, contrast: float,
                     gamma: float, smooth: float, thr_lo: float, thr_hi: float,
                     invert: bool, prev_weights: np.ndarray,
                     prev_frame, motion_accum):
        """
        将 BGRA numpy 数组处理为网格权重并写回缓存。
        arr: (H, W, 4) uint8，BGRA 格式
        """
        # 保存原始 RGB 预览（缩放到合理尺寸）
        preview_h = min(arr.shape[0], 120)
        preview_w = min(arr.shape[1], int(preview_h * arr.shape[1] / max(1, arr.shape[0])))
        try:
            from PIL import Image
            pil_rgb = Image.fromarray(arr[:, :, 2::-1])  # BGR → RGB
            pil_preview = pil_rgb.resize((preview_w, preview_h), Image.BILINEAR)
            preview_raw = np.array(pil_preview, dtype=np.uint8)
        except Exception:
            preview_raw = None

        # 计算通道图
        if channel == CHANNEL_LUMINANCE:
            # BT.601 亮度
            channel_map = (0.299 * arr[:, :, 2].astype(np.float32) +
                           0.587 * arr[:, :, 1].astype(np.float32) +
                           0.114 * arr[:, :, 0].astype(np.float32)) / 255.0
        elif channel == CHANNEL_RED:
            channel_map = arr[:, :, 2].astype(np.float32) / 255.0
        elif channel == CHANNEL_GREEN:
            channel_map = arr[:, :, 1].astype(np.float32) / 255.0
        elif channel == CHANNEL_BLUE:
            channel_map = arr[:, :, 0].astype(np.float32) / 255.0
        elif channel == CHANNEL_SATURATION:
            r = arr[:, :, 2].astype(np.float32) / 255.0
            g = arr[:, :, 1].astype(np.float32) / 255.0
            b = arr[:, :, 0].astype(np.float32) / 255.0
            cmax = np.maximum(np.maximum(r, g), b)
            cmin = np.minimum(np.minimum(r, g), b)
            delta = cmax - cmin
            channel_map = np.where(cmax > 1e-6, delta / (cmax + 1e-6), 0.0)
        elif channel == CHANNEL_HUE:
            r = arr[:, :, 2].astype(np.float32) / 255.0
            g = arr[:, :, 1].astype(np.float32) / 255.0
            b = arr[:, :, 0].astype(np.float32) / 255.0
            cmax = np.maximum(np.maximum(r, g), b)
            cmin = np.minimum(np.minimum(r, g), b)
            delta = cmax - cmin + 1e-8
            hue = np.zeros_like(r)
            mask_r = (cmax == r)
            hue[mask_r] = ((g[mask_r] - b[mask_r]) / delta[mask_r]) % 6.0
            mask_g = (cmax == g) & ~mask_r
            hue[mask_g] = (b[mask_g] - r[mask_g]) / delta[mask_g] + 2.0
            mask_b = ~mask_r & ~mask_g
            hue[mask_b] = (r[mask_b] - g[mask_b]) / delta[mask_b] + 4.0
            channel_map = (hue / 6.0) % 1.0
        elif channel == CHANNEL_MOTION:
            gray = (0.299 * arr[:, :, 2].astype(np.float32) +
                    0.587 * arr[:, :, 1].astype(np.float32) +
                    0.114 * arr[:, :, 0].astype(np.float32)) / 255.0
            if prev_frame is not None and prev_frame.shape == gray.shape:
                diff = np.abs(gray - prev_frame)
                if motion_accum is not None and motion_accum.shape == diff.shape:
                    motion_accum = motion_accum * 0.7 + diff * 0.3
                else:
                    motion_accum = diff
                channel_map = np.clip(motion_accum * 4.0, 0.0, 1.0)
            else:
                channel_map = np.zeros_like(gray)
                motion_accum = np.zeros_like(gray)
            with self._lock:
                self._prev_frame = gray
                self._motion_accum = motion_accum
        else:
            channel_map = np.zeros((arr.shape[0], arr.shape[1]), dtype=np.float32)

        # 缩放到网格尺寸
        try:
            from PIL import Image
            pil_map = Image.fromarray((channel_map * 255).astype(np.uint8), mode='L')
            pil_grid = pil_map.resize((cols, rows), Image.LANCZOS)
            grid_raw = np.array(pil_grid, dtype=np.float32) / 255.0
        except Exception:
            h, w = channel_map.shape
            grid_raw = np.zeros((rows, cols), dtype=np.float32)
            for r in range(rows):
                for c in range(cols):
                    y0 = int(r * h / rows)
                    y1 = int((r + 1) * h / rows)
                    x0 = int(c * w / cols)
                    x1 = int((c + 1) * w / cols)
                    grid_raw[r, c] = float(np.mean(channel_map[y0:y1, x0:x1]))

        # 应用亮度、对比度、Gamma
        grid = grid_raw.copy()
        grid = (grid - 0.5) * contrast + 0.5
        grid = grid * brightness
        if gamma != 1.0:
            grid = np.power(np.clip(grid, 0.0, 1.0), 1.0 / gamma)
        grid = np.clip(grid, 0.0, 1.0)
        if thr_hi > thr_lo:
            grid = (grid - thr_lo) / (thr_hi - thr_lo)
            grid = np.clip(grid, 0.0, 1.0)
        if invert:
            grid = 1.0 - grid
        # 仅在 prev_weights 非全零时才应用时间平滑，避免初始帧被全零压低导致输出全部 OFF
        if smooth > 0.0 and prev_weights.shape == grid.shape and np.any(prev_weights > 0.0):
            grid = prev_weights * smooth + grid * (1.0 - smooth)

        with self._lock:
            self._weights = grid.astype(np.float32)
            self._preview_raw = preview_raw
            # _preview_grid 存储处理后的权重（与 _weights 一致），供热力图预览使用
            self._preview_grid = grid.astype(np.float32)

    # ── 向后兼容：_do_sample(sct) 接口 ──────────────────────────────────────
    def _do_sample(self, sct):
        """向后兼容接口，委托给 _do_sample_mss。"""
        self._do_sample_mss(sct)

    # ── 单次同步采样（用于 UI tick 无后台线程时的降级模式）────────────────────
    def sample_once(self) -> np.ndarray:
        """
        同步采样一次，返回 (rows, cols) float32 权重矩阵。
        用于不启动后台线程的场景（如 UI 直接调用）。
        """
        if not self._available:
            with self._lock:
                return np.zeros((self._grid_rows, self._grid_cols), dtype=np.float32)
        if self._backend == _BACKEND_MSS:
            try:
                import mss
                with mss.mss() as sct:
                    self._do_sample_mss(sct)
            except Exception:
                pass
        elif self._backend == _BACKEND_PIL:
            try:
                self._do_sample_pil()
            except Exception:
                pass
        return self.get_latest_weights()

    # ── 工具方法 ─────────────────────────────────────────────────────────────
    def weights_to_fixture_map(self, fixtures: List[Dict]) -> Dict[str, float]:
        """
        将网格权重映射到设备 ID。
        fixtures: 含 pos_x, pos_y（归一化 [0,1]）和 id 字段的设备列表。
        返回 {fixture_id: weight} 字典。
        """
        with self._lock:
            weights = self._weights.copy()
            rows, cols = weights.shape
        result = {}
        for f in fixtures:
            fid = f.get("id", "")
            if not fid:
                continue
            nx = float(f.get("pos_x", 0.5))
            ny = float(f.get("pos_y", 0.5))
            col = min(cols - 1, int(nx * cols))
            row = min(rows - 1, int(ny * rows))
            result[fid] = float(weights[row, col])
        return result

    def get_stats(self) -> Dict:
        """获取当前采样统计信息。"""
        with self._lock:
            w = self._weights
            if w.size == 0:
                return {"min": 0.0, "max": 0.0, "mean": 0.0, "std": 0.0}
            return {
                "min":  float(np.min(w)),
                "max":  float(np.max(w)),
                "mean": float(np.mean(w)),
                "std":  float(np.std(w)),
            }
