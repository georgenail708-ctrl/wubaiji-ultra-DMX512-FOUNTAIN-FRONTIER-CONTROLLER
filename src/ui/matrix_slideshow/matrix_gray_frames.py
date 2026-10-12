# -*- coding: utf-8 -*-
"""matrix_gray_frames.py — 矩阵灰度帧纯逻辑（深查②三.Matrix#1 P0 灰度帧）。

背景：`MatrixSlide.states` 原为 `{fid: bool}` 纯 0/1，图案阶调/呼吸层次受限。
本模块把值域扩到 **0-255 灰度**，并提供两条兼容规则：

  * **消费侧兼容**（`state_level`）：bool 视为 1.0/0.0（旧语义 ON/OFF 逐字节不变），
    int/float 视为 v/255；生成器/面板等既有 bool 写入点无需改动即可继续工作。
  * **存储侧规整**（`normalize_states`）：from_dict/导入边界把任意值规整成
    int 0-255（bool True→255），落盘即灰度语义。

另含图片夹批量导入：多张图片 → 亮度 → 按设备归一化坐标采样 → 每图一帧灰度
帧（Qt 对话框在函数内部延迟导入，纯逻辑路径不依赖 Qt）。
"""
from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

#: 灰度值域上限
GRAY_MAX = 255

#: 支持的图片扩展名（OpenCV 能读的常见格式）
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff")


def state_level(v) -> float:
    """单帧状态值 → 0..1 输出权重。

    bool → 1.0/0.0（旧工程语义逐位不变）；数值 → v/255（负数/NaN → 0）。
    """
    if isinstance(v, (bool, np.bool_)):
        return 1.0 if v else 0.0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f != f:  # NaN
        return 0.0
    if f <= 0.0:
        return 0.0
    if f >= GRAY_MAX:
        return 1.0
    return f / float(GRAY_MAX)


def normalize_state_value(v) -> int:
    """任意状态值 → int 0-255（bool True=255，保留旧工程 ON 语义）。"""
    if isinstance(v, (bool, np.bool_)):
        return GRAY_MAX if v else 0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    if f != f:  # NaN
        return 0
    return max(0, min(GRAY_MAX, int(round(f))))


def normalize_states(states) -> Dict[str, int]:
    """{fid: any} → {fid: int 0-255}（存储/导入边界统一灰度语义）。"""
    return {str(k): normalize_state_value(v) for k, v in (states or {}).items()}


def has_grayscale(states) -> bool:
    """帧里是否出现中间灰度（非 0/满值）——过渡/缩略图渲染分支用。"""
    for v in (states or {}).values():
        if isinstance(v, (bool, np.bool_)):
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if 0.0 < f < float(GRAY_MAX):
            return True
    return False


def _get_cv2():
    try:
        import cv2  # type: ignore
        return cv2
    except Exception:
        return None


def load_image_gray(path: str) -> Optional[np.ndarray]:
    """读图片 → float32 亮度 0..1（失败返回 None）。"""
    cv2 = _get_cv2()
    if cv2 is None or not path:
        return None
    try:
        # ★ R193/P2-17：4 倍降采样解码仅用于大文件（>1MB）——小素材（图标级）
        #   降采样会丢掉有效分辨率；归一化坐标最近邻采样对分辨率不敏感。
        import os as _os
        _flag = (cv2.IMREAD_REDUCED_GRAYSCALE_4
                 if _os.path.getsize(str(path)) > 1_000_000
                 else cv2.IMREAD_GRAYSCALE)
        img = cv2.imread(str(path), _flag)
    except Exception:
        img = None
    if img is None or getattr(img, "size", 0) == 0:
        return None
    return img.astype(np.float32) / 255.0


def _sample_pixel(gray: np.ndarray, f: Dict, gain: float) -> int:
    """单设备采样（最近邻 + 边缘钳制；helper 压 CC）。"""
    h, w = int(gray.shape[0]), int(gray.shape[1])
    nx = min(1.0, max(0.0, float(f.get("pos_x", 0.5) or 0.0)))
    ny = min(1.0, max(0.0, float(f.get("pos_y", 0.5) or 0.0)))
    px = min(w - 1, int(round(nx * (w - 1))))
    py = min(h - 1, int(round(ny * (h - 1))))
    val = float(gray[py, px]) * gain
    return max(0, min(GRAY_MAX, int(round(val * float(GRAY_MAX)))))


def sample_gray_to_fixtures(gray: np.ndarray, fixtures: Sequence[Dict],
                            brightness: float = 1.0) -> Dict[str, int]:
    """亮度图按设备归一化坐标采样 → {fid: int 0-255}。

    设备 pos_x/pos_y ∈ [0,1] 直接映射到图片像素（最近邻 + 边缘钳制）；
    `brightness` 为导入增益（>1 提亮，供暗素材用）。
    """
    h, w = int(gray.shape[0]), int(gray.shape[1])
    if h <= 0 or w <= 0:
        return {}
    gain = max(0.0, float(brightness or 1.0))
    out: Dict[str, int] = {}
    for f in fixtures or []:
        fid = str(f.get("id", "") or "")
        if not fid:
            continue
        out[fid] = _sample_pixel(gray, f, gain)
    return out


def _scan_image_dir(folder: str) -> List[str]:
    """单目录扫描图片（helper，压嵌套深度）。"""
    try:
        return [os.path.join(folder, name) for name in sorted(os.listdir(folder))
                if name.lower().endswith(IMAGE_EXTS)
                and os.path.isfile(os.path.join(folder, name))]
    except OSError:
        return []


def list_image_files(paths: Sequence[str]) -> List[str]:
    """过滤出图片文件并按文件名排序（文件夹时展开一层）。"""
    files: List[str] = []
    for p in paths or []:
        p = str(p or "")
        if os.path.isdir(p):
            files.extend(_scan_image_dir(p))
        elif p.lower().endswith(IMAGE_EXTS) and os.path.isfile(p):
            files.append(p)
    return sorted(files)


def images_to_slides(paths: Sequence[str], fixtures: Sequence[Dict],
                     brightness: float = 1.0,
                     hold: float = 1.0, trans: float = 0.2) -> Tuple[List[object], List[str]]:
    """图片列表 → [(MatrixSlide), ...]（每图一帧灰度）+ 失败路径列表。

    返回的 slide 为 `MatrixSlide`（延迟导入避免模块环），states 已规整 int 0-255。
    """
    from .matrix_slide_engine import MatrixSlide
    slides: List[MatrixSlide] = []
    failed: List[str] = []
    files = list_image_files(paths)
    for fp in files:
        gray = load_image_gray(fp)
        if gray is None:
            failed.append(fp)
            continue
        states = sample_gray_to_fixtures(gray, fixtures, brightness=brightness)
        if not states:
            failed.append(fp)
            continue
        name = os.path.splitext(os.path.basename(fp))[0]
        slides.append(MatrixSlide(
            name=name[:40] or "图片帧",
            states=states,
            hold_time=float(hold),
            transition_time=float(trans),
        ))
    return slides, failed


def _select_image_paths(panel):
    """弹多选文件对话框（helper 压 CC）；返回路径列表或空。"""
    from PyQt6.QtWidgets import QFileDialog
    paths, _flt = QFileDialog.getOpenFileNames(
        panel, "导入图片帧（可多选；也支持选一个文件夹）", "",
        "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff);;所有文件 (*.*)")
    return list(paths or [])


def _append_slides_to_engine(panel, slides) -> int:
    """slides 逐帧入引擎 + 刷新缩略图；返回末帧索引或 -1。"""
    engine = getattr(panel, "_engine", None)
    if engine is None:
        return -1
    idx = -1
    for slide in slides:
        idx = engine.add_slide(slide)
    try:
        from .matrix_slideshow_panel import _mark_panel_dirty
        _mark_panel_dirty(panel)
    except Exception:
        logger.debug("matrix: mark dirty failed", exc_info=True)
    try:
        panel._rebuild_thumbs()
        panel._on_thumb_clicked(idx)
    except Exception:
        logger.debug("matrix: refresh thumbs failed", exc_info=True)
    return idx


def import_images_as_slides(panel) -> int:
    """面板交互入口：多选图片/文件夹 → 追加灰度帧。返回新增帧数。

    `panel` 为 MatrixSlideshowPanel（用其 _fixtures/_engine/_rebuild_thumbs 等
    既有能力）；Qt 对话框延迟导入，导入失败有 QMessageBox 提示。
    """
    from PyQt6.QtWidgets import QMessageBox
    fixtures = list(getattr(panel, "_fixtures", []) or [])
    if not fixtures:
        QMessageBox.warning(panel, "导入图片帧", "请先选择设备组（需要设备坐标来采样图片）。")
        return 0
    paths = _select_image_paths(panel)
    if not paths:
        return 0
    slides, failed = images_to_slides(paths, fixtures)
    if not slides:
        QMessageBox.warning(panel, "导入图片帧",
                            "没有可用的图片（OpenCV 读取失败或设备列表为空）。")
        return 0
    idx = _append_slides_to_engine(panel, slides)
    if idx < 0:
        return 0
    if failed:
        QMessageBox.warning(
            panel, "导入图片帧",
            f"已导入 {len(slides)} 帧；{len(failed)} 张图片读取失败已跳过。")
    return len(slides)
