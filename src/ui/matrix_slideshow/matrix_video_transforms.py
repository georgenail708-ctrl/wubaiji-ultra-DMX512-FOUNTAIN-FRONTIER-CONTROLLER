# -*- coding: utf-8 -*-
"""matrix_video_transforms.py — 矩阵采样源变换纯逻辑（深查②三.Matrix #2/#3/#11）。

  * **四角透视变形（corner pin，#2 P0）**：`cv2.getPerspectiveTransform` +
    `warpPerspective`，把源帧的 4 个归一化角点拉直为整幅画面，实拍画面正对矩阵。
  * **图片掩码（#3 P0）**：掩码图亮度 → 0..1 权重，与采样亮度图逐格相乘
    （黑=不亮；灰=按比例，是布尔 AND 的超集）。
  * **视频走带参数规整（#11 P2）**：in/out 点、方向枚举、速率的钳制/归一。

全部纯函数（numpy/OpenCV，无 Qt），engine 的 `_frame_to_video_maps` 在采样前
调用 warp、在输出亮度图上调用 mask。
"""
from __future__ import annotations

import logging
from typing import List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

#: 默认四角（归一化 TL,TR,BR,BL）= 不变形
DEFAULT_CORNERS: Tuple[Tuple[float, float], ...] = (
    (0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0),
)

#: 视频走带方向枚举（engine.video_direction 取值）
VIDEO_DIR_FORWARD = "forward"
VIDEO_DIR_REVERSE = "reverse"
VIDEO_DIR_PINGPONG = "pingpong"
VIDEO_DIRECTIONS = (VIDEO_DIR_FORWARD, VIDEO_DIR_REVERSE, VIDEO_DIR_PINGPONG)

#: 视频速率档位（engine.video_rate 取值）
VIDEO_RATES = (0.25, 0.5, 1.0, 2.0)


def normalize_corners(corners) -> Optional[np.ndarray]:
    """任意角点容器 → float32 (4,2) 归一化角点；非法/默认 → None（不变形）。

    接受长度 4 的 (x, y) 序列，值域钳到 [0,1]。与默认角点逐位一致时返回
    None（调用方跳过 warp，零开销）。
    """
    if corners is None:
        return None
    try:
        pts = [(float(c[0]), float(c[1])) for c in corners]
    except (TypeError, ValueError, IndexError):
        return None
    if len(pts) != 4:
        return None
    pts = [(min(1.0, max(0.0, x)), min(1.0, max(0.0, y))) for x, y in pts]
    for (ax, ay), (bx, by) in zip(pts, DEFAULT_CORNERS):
        if abs(ax - bx) > 1e-6 or abs(ay - by) > 1e-6:
            arr = np.asarray(pts, dtype=np.float32)
            return arr
    return None


def corners_to_payload(corners) -> Optional[List[List[float]]]:
    """角点 → 可 JSON 序列化的 4x2 列表（工程文件存储用）。"""
    arr = normalize_corners(corners)
    if arr is None:
        return None
    return [[float(x), float(y)] for x, y in arr.tolist()]


def corners_from_payload(payload) -> Optional[np.ndarray]:
    """工程文件角点列表 → 角点数组（与 normalize_corners 同规整）。"""
    return normalize_corners(payload)


def warp_frame_bgr(frame_bgr: Optional[np.ndarray],
                   corners) -> Optional[np.ndarray]:
    """按 4 归一化角点对源帧做透视变形（保持输出尺寸不变）。

    corners 为 None/非法 = 不变形原样返回；cv2 不可用/帧无效也原样返回。
    """
    if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
        return frame_bgr
    pts = normalize_corners(corners)
    if pts is None:
        return frame_bgr
    try:
        import cv2  # type: ignore
    except Exception:
        return frame_bgr
    h, w = frame_bgr.shape[:2]
    if h <= 1 or w <= 1:
        return frame_bgr
    try:
        src = pts * np.float32([w - 1, h - 1])
        dst = np.asarray(DEFAULT_CORNERS, dtype=np.float32) * np.float32([w - 1, h - 1])
        mat = cv2.getPerspectiveTransform(src.astype(np.float32), dst)
        return cv2.warpPerspective(
            frame_bgr, mat, (w, h),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0))
    except Exception:
        logger.debug("matrix: warp frame failed", exc_info=True)
        return frame_bgr


def load_mask_array(path: str, max_side: int = 1024) -> Optional[np.ndarray]:
    """掩码图片 → float32 (H,W) 0..1（亮度即通过率；失败 None）。"""
    if not path:
        return None
    try:
        import cv2  # type: ignore
    except Exception:
        return None
    try:
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    except Exception:
        img = None
    if img is None or getattr(img, "size", 0) == 0:
        return None
    h, w = img.shape[:2]
    scale = max(h, w) / float(max(1, max_side))
    if scale > 1.0:
        try:
            img = cv2.resize(img, (max(1, int(w / scale)), max(1, int(h / scale))),
                             interpolation=cv2.INTER_AREA)
        except Exception:
            pass
    return img.astype(np.float32) / 255.0


def apply_mask_to_map(brightness_map: np.ndarray, mask: Optional[np.ndarray]) -> np.ndarray:
    """掩码 AND：亮度图 (rows,cols) × 掩码（自动缩放到同形）。黑区直接归零。"""
    if mask is None or brightness_map is None or getattr(brightness_map, "size", 0) == 0:
        return brightness_map
    m = np.asarray(mask, dtype=np.float32)
    rows, cols = brightness_map.shape[0], brightness_map.shape[1]
    if m.size == 0:
        return brightness_map
    if m.shape != (rows, cols):
        try:
            import cv2  # type: ignore
            m = cv2.resize(m, (cols, rows), interpolation=cv2.INTER_AREA)
        except Exception:
            ys = (np.linspace(0, m.shape[0] - 1, rows)).astype(np.int32)
            xs = (np.linspace(0, m.shape[1] - 1, cols)).astype(np.int32)
            m = m[np.ix_(ys, xs)]
    return (np.clip(brightness_map, 0.0, 1.0) * np.clip(m, 0.0, 1.0)).astype(np.float32)


def clamp_video_in_out(in_point: int, out_point: int, total: int) -> Tuple[int, int]:
    """in/out 点规整（out<=0 视为末帧；保证 in<=out）。"""
    total = max(0, int(total or 0))
    in_pt = max(0, int(in_point or 0))
    out_pt = int(out_point or 0)
    if total > 0:
        if out_pt <= 0 or out_pt >= total:
            out_pt = total - 1
        out_pt = min(total - 1, max(out_pt, in_pt))
        in_pt = min(in_pt, out_pt)
    else:
        in_pt = 0
        out_pt = 0
    return in_pt, out_pt


def _advance_reverse(cur: int, in_pt: int, out_pt: int,
                     play_dir: int) -> Tuple[int, int]:
    """反向走带：到入点后跳回出点（helper 压 CC）。"""
    nxt = cur - 1
    if nxt < in_pt:
        nxt = out_pt
    return nxt, play_dir


def _advance_pingpong(cur: int, in_pt: int, out_pt: int,
                      play_dir: int) -> Tuple[int, int]:
    """乒乓走带：触边反向（helper 压 CC）。"""
    nxt = cur + (1 if play_dir >= 0 else -1)
    if nxt > out_pt:
        return max(in_pt, cur - 1), -1
    if nxt < in_pt:
        return min(out_pt, cur + 1), 1
    return nxt, play_dir


def _advance_forward(cur: int, in_pt: int, out_pt: int,
                     total: int, play_dir: int) -> Tuple[int, int]:
    """正向走带：到出点后回入点（total<=0 时沿用旧行为只递增）。"""
    nxt = cur + 1
    if total > 0 and nxt > out_pt:
        nxt = in_pt
    return nxt, play_dir


def next_video_playhead(current: int, *, play_dir: int, direction: str,
                        in_point: int, out_point: int,
                        total: int) -> Tuple[int, int]:
    """走带推进：返回 (下一帧号, 新方向)。

    forward: 到 out 后回 in（与旧行为一致：out<=0 = 末帧回 0）；
    reverse: 到 in 后跳 out；
    pingpong: 触边反向。
    """
    direction = str(direction or VIDEO_DIR_FORWARD)
    in_pt, out_pt = clamp_video_in_out(in_point, out_point, total)
    cur = int(current)
    if direction == VIDEO_DIR_REVERSE:
        return _advance_reverse(cur, in_pt, out_pt, play_dir)
    if direction == VIDEO_DIR_PINGPONG:
        return _advance_pingpong(cur, in_pt, out_pt, play_dir)
    return _advance_forward(cur, in_pt, out_pt, total, play_dir)


def normalize_video_direction(v) -> str:
    d = str(v or VIDEO_DIR_FORWARD).strip().lower()
    return d if d in VIDEO_DIRECTIONS else VIDEO_DIR_FORWARD


#: 帧源 kind：file/camera 为本仓原生，spout/ndi 为外部纹理登记位
#: （见 matrix_external_sources；归一写在这里避免 slide_engine 反向依赖 Qt）。
VIDEO_SOURCE_KINDS = ("file", "camera", "spout", "ndi")


def normalize_video_source_kind(v) -> str:
    """任意值 → file/camera/spout/ndi（未知/空 → file，旧档行为不变）。"""
    k = str(v or "").strip().lower()
    return k if k in VIDEO_SOURCE_KINDS else "file"


def normalize_video_rate(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 1.0
    if f != f or f <= 0.0:
        return 1.0
    return min(8.0, max(0.05, f))


def describe_warp(corners) -> str:
    """角点 → 单行人类可读（UI 信息标签用）。"""
    pts = normalize_corners(corners)
    if pts is None:
        return "未启用（默认矩形）"
    labels = ("左上", "右上", "右下", "左下")
    parts = [f"{lb}({x:.2f},{y:.2f})" for lb, (x, y) in zip(labels, pts)]
    return "  ".join(parts)
