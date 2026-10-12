# -*- coding: utf-8 -*-
"""matrix_video_source_ui.py — 像素参数面板「视频/摄像头/变形/掩码」扩展 UI。

深查②三.Matrix 落地块：
  * #5 P1 摄像头直通（设备号 + 热插拔守卫在 engine 侧）
  * #2 P0 四角透视变形参数子页（数值输入；拖拽 UX 后置）
  * #3 P0 掩码图片导入/清除
  * #11 P2 视频入出点 / pingpong / 半速
  * #15 P2 拆分预览开关

设计纪律：本模块只建控件 + 薄回调（引擎属性读写），重逻辑在
`matrix_video_transforms.py` / `MatrixPixelEngine`；宿主
`PixelEffectParamPanel._build_screen_page` 只保留几行调用。
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import QSignalBlocker, Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

logger = logging.getLogger(__name__)

_WARP_TAB_KEY = "warp"


def _cv2_available() -> bool:
    try:
        import cv2  # noqa: F401  # type: ignore
        return True
    except Exception:
        return False


# ── 视频页扩展：摄像头 + 走带（在 _build_screen_page 的视频 GroupBox 里追加行）──

def build_camera_row(panel) -> QWidget:
    """摄像头直通行：设备号 + 开启/停用。`panel` 为 PixelEffectParamPanel。"""
    host = QWidget()
    row = QGridLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    row.addWidget(QLabel("摄像头:"), 0, 0)
    panel._camera_device_spin = QSpinBox()
    panel._camera_device_spin.setRange(0, 8)
    panel._camera_device_spin.setValue(0)
    panel._camera_device_spin.setFixedWidth(46)
    panel._camera_device_spin.setToolTip("OpenCV 摄像头设备号（0 = 默认摄像头）")
    row.addWidget(panel._camera_device_spin, 0, 1)
    panel._camera_start_btn = QPushButton("开启摄像头")
    panel._camera_start_btn.setToolTip("用 cv2.VideoCapture 直通摄像头画面（替代视频文件）")
    panel._camera_start_btn.clicked.connect(lambda: on_camera_toggle(panel, True))
    row.addWidget(panel._camera_start_btn, 0, 2)
    panel._camera_stop_btn = QPushButton("停用")
    panel._camera_stop_btn.setEnabled(False)
    panel._camera_stop_btn.clicked.connect(lambda: on_camera_toggle(panel, False))
    row.addWidget(panel._camera_stop_btn, 0, 3)
    panel._camera_state_lbl = QLabel("")
    panel._camera_state_lbl.setStyleSheet("color:#888;font-size:10px;")
    panel._camera_state_lbl.setWordWrap(True)
    row.addWidget(panel._camera_state_lbl, 1, 0, 1, 4)
    return host


def build_transport_row(panel) -> QWidget:
    """视频走带行：入点/出点（=当前帧）+ 清除 + 方向 + 速率。"""
    host = QWidget()
    row = QGridLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    panel._video_in_btn = QPushButton("入点=当前")
    panel._video_in_btn.setToolTip("把视频入点设为进度条当前帧（播放只从入点后开始/循环）")
    panel._video_in_btn.clicked.connect(lambda: on_set_video_point(panel, "in"))
    row.addWidget(panel._video_in_btn, 0, 0)
    panel._video_out_btn = QPushButton("出点=当前")
    panel._video_out_btn.setToolTip("把视频出点设为进度条当前帧（到点即回入点）")
    panel._video_out_btn.clicked.connect(lambda: on_set_video_point(panel, "out"))
    row.addWidget(panel._video_out_btn, 0, 1)
    panel._video_pts_clear_btn = QPushButton("清除点")
    panel._video_pts_clear_btn.setToolTip("清除入出点（回到整段循环）")
    panel._video_pts_clear_btn.clicked.connect(lambda: on_set_video_point(panel, "clear"))
    row.addWidget(panel._video_pts_clear_btn, 0, 2)

    panel._video_dir_combo = QComboBox()
    for label, data in (("正向", "forward"), ("反向", "reverse"), ("乒乓", "pingpong")):
        panel._video_dir_combo.addItem(label, data)
    panel._video_dir_combo.setToolTip("播放方向：正向 / 反向（逐帧 seek 较慢）/ 乒乓往复")
    panel._video_dir_combo.currentIndexChanged.connect(
        lambda _i: on_transport_changed(panel))
    row.addWidget(panel._video_dir_combo, 1, 0)

    panel._video_rate_combo = QComboBox()
    for label, data in (("0.25x", 0.25), ("0.5x", 0.5), ("1x", 1.0), ("2x", 2.0)):
        panel._video_rate_combo.addItem(label, data)
    panel._video_rate_combo.setCurrentIndex(2)
    panel._video_rate_combo.setToolTip("播放速率（半速=每 2 tick 推 1 帧）")
    panel._video_rate_combo.currentIndexChanged.connect(
        lambda _i: on_transport_changed(panel))
    row.addWidget(panel._video_rate_combo, 1, 1)

    panel._video_pts_lbl = QLabel("")
    panel._video_pts_lbl.setStyleSheet("color:#888;font-size:10px;")
    panel._video_pts_lbl.setWordWrap(True)
    row.addWidget(panel._video_pts_lbl, 1, 2)
    return host


def _engine_of(panel):
    eng = getattr(panel, "_engine", None)
    return eng


def _emit_params_changed(panel) -> None:
    """参数变更广播（helper；signal 缺失/异常时静默）。"""
    try:
        panel.params_changed.emit()
    except Exception:
        pass


def _start_camera(panel, eng, idx: int) -> bool:
    """开启摄像头直通（含失败文案）；返回是否成功。"""
    ok = False
    try:
        ok = bool(eng.load_camera(idx))
    except Exception:
        logger.debug("matrix: load_camera failed", exc_info=True)
    if not ok:
        panel._camera_state_lbl.setText("开启失败（设备占用/不存在/无 cv2）")
        return False
    eng.video_playing = False   # 与视频导入同语义：由顶部统一播放键启动
    panel._camera_state_lbl.setText(f"摄像头 {idx} 就绪（顶部播放键启动）")
    if hasattr(panel, "_video_path_lbl"):
        panel._video_path_lbl.setText(f"摄像头 {idx} 直通中；删除视频/停用可退出。")
    return True


def _stop_camera(panel, eng) -> None:
    """停用摄像头直通（helper）。"""
    try:
        eng.stop_camera()
    except Exception:
        logger.debug("matrix: stop_camera failed", exc_info=True)
    panel._camera_state_lbl.setText("")
    if hasattr(panel, "_video_path_lbl"):
        panel._video_path_lbl.setText(
            "未导入视频；视频像素采样使用视频完整画面，不设置采样区域；"
            "使用顶部统一播放键启动预览与矩阵输出。")


def _sync_camera_buttons(panel, eng) -> None:
    """按引擎 video_source 刷新开/停按钮可用态（helper）。"""
    is_cam = getattr(eng, "video_source", "file") == "camera"
    panel._camera_stop_btn.setEnabled(is_cam)
    panel._camera_start_btn.setEnabled(not is_cam)


def on_camera_toggle(panel, enable: bool) -> None:
    """开启/停用摄像头直通（热插拔守卫在 engine._read_next_video_frame）。"""
    eng = _engine_of(panel)
    if eng is None:
        return
    if enable:
        idx = int(panel._camera_device_spin.value())
        if not _start_camera(panel, eng, idx):
            return
    else:
        _stop_camera(panel, eng)
    _sync_camera_buttons(panel, eng)
    _emit_params_changed(panel)


def _slider_frame(panel) -> int:
    """进度条当前帧（无进度条控件时 0）。"""
    if hasattr(panel, "_video_pos_slider"):
        return int(panel._video_pos_slider.value())
    return 0


def _apply_video_point(panel, eng, which: str) -> None:
    """写入单个入/出点（helper；逐行搬移原语义）。"""
    cur = _slider_frame(panel)
    total = int(getattr(eng, "_video_total_frames", 0) or 0)
    if total > 0 and cur >= total - 1 and which == "out":
        cur = total - 1
    if which == "in":
        eng.video_in_point = max(0, cur)
    else:
        eng.video_out_point = max(0, cur)


def on_set_video_point(panel, which: str) -> None:
    """入点/出点 = 进度条当前帧；clear = 复位。"""
    eng = _engine_of(panel)
    if eng is None:
        return
    if which == "clear":
        eng.video_in_point = 0
        eng.video_out_point = 0
    else:
        _apply_video_point(panel, eng, which)
    _sync_transport_labels(panel, eng)
    _emit_params_changed(panel)


def on_transport_changed(panel) -> None:
    """方向/速率下拉 → 引擎属性。"""
    eng = _engine_of(panel)
    if eng is None:
        return
    eng.video_direction = str(panel._video_dir_combo.currentData() or "forward")
    try:
        eng.video_rate = float(panel._video_rate_combo.currentData() or 1.0)
    except Exception:
        eng.video_rate = 1.0
    _sync_transport_labels(panel, eng)
    _emit_params_changed(panel)


def _sync_transport_points(panel, eng) -> None:
    """入出点信息标签（helper）。"""
    in_pt = int(getattr(eng, "video_in_point", 0) or 0)
    out_pt = int(getattr(eng, "video_out_point", 0) or 0)
    if in_pt <= 0 and out_pt <= 0:
        panel._video_pts_lbl.setText("入出点：整段")
    else:
        panel._video_pts_lbl.setText(f"入点 {in_pt} / 出点 {out_pt or '末尾'}")


def _sync_transport_combos(panel, eng) -> None:
    """方向/速率下拉回填（helper；blockSignals 防回调环）。"""
    d = str(getattr(eng, "video_direction", "forward") or "forward")
    idx = max(0, panel._video_dir_combo.findData(d))
    if panel._video_dir_combo.currentIndex() != idx:
        panel._video_dir_combo.blockSignals(True)
        panel._video_dir_combo.setCurrentIndex(idx)
        panel._video_dir_combo.blockSignals(False)
    r = float(getattr(eng, "video_rate", 1.0) or 1.0)
    ridx = max(0, panel._video_rate_combo.findData(r))
    if ridx > 0 and panel._video_rate_combo.currentIndex() != ridx:
        panel._video_rate_combo.blockSignals(True)
        panel._video_rate_combo.setCurrentIndex(ridx)
        panel._video_rate_combo.blockSignals(False)


def _sync_transport_labels(panel, eng) -> None:
    _sync_transport_points(panel, eng)
    _sync_transport_combos(panel, eng)


def sync_video_transport_widgets(panel) -> None:
    """工程还原后按引擎值刷新走带控件（_sync_pixel_param_widgets 调用）。"""
    eng = _engine_of(panel)
    if eng is None or not hasattr(panel, "_video_pts_lbl"):
        return
    _sync_transport_labels(panel, eng)
    is_cam = getattr(eng, "video_source", "file") == "camera"
    if hasattr(panel, "_camera_stop_btn"):
        panel._camera_stop_btn.setEnabled(is_cam)
    if hasattr(panel, "_camera_start_btn"):
        panel._camera_start_btn.setEnabled(not is_cam)


# ── 变形子页（四角透视） ──────────────────────────────────────────────────────

_PERMILLE = 1000.0
_CORNER_LABELS = ("左上", "右上", "右下", "左下")


def build_warp_tab(panel) -> QWidget:
    """四角透视参数页：启用 + 4 角 x/y 千分数值 + 复位。"""
    from .matrix_video_transforms import DEFAULT_CORNERS, corners_from_payload, describe_warp
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(6, 10, 6, 6)
    lay.setSpacing(4)

    panel._warp_enable_chk = QCheckBox("启用四角透视变形（采样前对源帧 warp）")
    panel._warp_enable_chk.setToolTip(
        "把源画面的 4 个角拉伸到整幅画面（梯形校正），实拍投影正对矩阵。\n"
        "数值为归一化角点 ×1000（千分比）；默认 (0,0)(1000,0)(1000,1000)(0,1000)=不变形。")
    lay.addWidget(panel._warp_enable_chk)

    grid = QGridLayout()
    grid.setSpacing(3)
    panel._warp_spins = []
    defaults = DEFAULT_CORNERS
    for i, label in enumerate(_CORNER_LABELS):
        dx, dy = defaults[i]
        grid.addWidget(QLabel(f"{label} x:"), i, 0)
        sx = QSpinBox()
        sx.setRange(0, 1000)
        sx.setValue(int(round(dx * _PERMILLE)))
        sx.setFixedWidth(64)
        grid.addWidget(sx, i, 1)
        grid.addWidget(QLabel("y:"), i, 2)
        sy = QSpinBox()
        sy.setRange(0, 1000)
        sy.setValue(int(round(dy * _PERMILLE)))
        sy.setFixedWidth(64)
        grid.addWidget(sy, i, 3)
        panel._warp_spins.append((sx, sy))
    lay.addLayout(grid)

    panel._warp_info_lbl = QLabel("")
    panel._warp_info_lbl.setStyleSheet("color:#888;font-size:10px;")
    lay.addWidget(panel._warp_info_lbl)

    def _apply(*_a) -> None:
        eng = _engine_of(panel)
        if eng is None:
            return
        if not panel._warp_enable_chk.isChecked():
            eng.video_warp_corners = None
            panel._warp_info_lbl.setText("未启用（默认矩形）")
        else:
            corners = [(sx.value() / _PERMILLE, sy.value() / _PERMILLE)
                       for sx, sy in panel._warp_spins]
            eng.video_warp_corners = corners
            panel._warp_info_lbl.setText(describe_warp(corners))
        _emit_params_changed(panel)

    for sx, sy in panel._warp_spins:
        sx.valueChanged.connect(_apply)
        sy.valueChanged.connect(_apply)
    panel._warp_enable_chk.toggled.connect(_apply)
    panel._warp_apply = _apply

    reset_btn = QPushButton("复位四角")
    reset_btn.setFixedHeight(24)
    reset_btn.setToolTip("恢复为不变形（默认矩形）")

    def _reset() -> None:
        for (sx, sy), (dx, dy) in zip(panel._warp_spins, DEFAULT_CORNERS):
            sx.setValue(int(round(dx * _PERMILLE)))
            sy.setValue(int(round(dy * _PERMILLE)))
        panel._warp_enable_chk.setChecked(False)
        _apply()

    reset_btn.clicked.connect(_reset)
    lay.addWidget(reset_btn)
    lay.addStretch()
    return page


def sync_warp_widgets(panel) -> None:
    """工程还原后按引擎角点刷新变形页（_sync_pixel_param_widgets 调用）。"""
    eng = _engine_of(panel)
    if eng is None or not hasattr(panel, "_warp_spins"):
        return
    from .matrix_video_transforms import corners_from_payload, describe_warp
    pts = corners_from_payload(getattr(eng, "video_warp_corners", None))
    enabled = pts is not None
    with QSignalBlocker(panel._warp_enable_chk):
        panel._warp_enable_chk.setChecked(enabled)
        if enabled:
            for (sx, sy), (fx, fy) in zip(panel._warp_spins, pts.tolist()):
                with QSignalBlocker(sx), QSignalBlocker(sy):
                    sx.setValue(int(round(float(fx) * _PERMILLE)))
                    sy.setValue(int(round(float(fy) * _PERMILLE)))
    panel._warp_info_lbl.setText(describe_warp(pts) if enabled else "未启用（默认矩形）")


# ── 掩码子页 ─────────────────────────────────────────────────────────────────

def build_mask_tab(panel) -> QWidget:
    """掩码页：导入掩码图片 + 清除 + 信息标签。"""
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(6, 10, 6, 6)
    lay.setSpacing(4)
    note = QLabel("掩码黑色区域 = 对应灯不亮；灰 = 按比例压暗。\n掩码自动缩放到矩阵网格。")
    note.setStyleSheet("color:#888;font-size:10px;")
    note.setWordWrap(True)
    lay.addWidget(note)

    row = QHBoxLayout()
    panel._mask_import_btn = QPushButton("导入掩码图片")
    panel._mask_import_btn.setToolTip("读取图片亮度作为掩码（与采样输出逐帧相乘）")
    panel._mask_import_btn.clicked.connect(lambda: on_mask_import(panel))
    row.addWidget(panel._mask_import_btn)
    panel._mask_clear_btn = QPushButton("清除掩码")
    panel._mask_clear_btn.setEnabled(False)
    panel._mask_clear_btn.clicked.connect(lambda: on_mask_clear(panel))
    row.addWidget(panel._mask_clear_btn)
    lay.addLayout(row)

    panel._mask_info_lbl = QLabel("未设置掩码")
    panel._mask_info_lbl.setStyleSheet("color:#888;font-size:10px;")
    lay.addWidget(panel._mask_info_lbl)
    lay.addStretch()
    return page


def on_mask_import(panel) -> None:
    eng = _engine_of(panel)
    if eng is None:
        return
    path, _ = QFileDialog.getOpenFileName(
        panel, "导入掩码图片", "",
        "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp);;所有文件 (*.*)")
    if not path:
        return
    from .matrix_video_transforms import load_mask_array
    mask = load_mask_array(path)
    if mask is None:
        panel._mask_info_lbl.setText("掩码读取失败（cv2 不可用或文件无效）")
        return
    eng.sample_mask = mask
    eng.sample_mask_path = path
    h, w = mask.shape[:2]
    panel._mask_info_lbl.setText(f"掩码 {w}×{h} 已加载：{path}")
    panel._mask_clear_btn.setEnabled(True)
    try:
        panel.params_changed.emit()
    except Exception:
        pass


def on_mask_clear(panel) -> None:
    eng = _engine_of(panel)
    if eng is None:
        return
    eng.sample_mask = None
    eng.sample_mask_path = ""
    panel._mask_info_lbl.setText("未设置掩码")
    panel._mask_clear_btn.setEnabled(False)
    try:
        panel.params_changed.emit()
    except Exception:
        pass


def sync_mask_widgets(panel) -> None:
    eng = _engine_of(panel)
    if eng is None or not hasattr(panel, "_mask_info_lbl"):
        return
    mask = getattr(eng, "sample_mask", None)
    path = str(getattr(eng, "sample_mask_path", "") or "")
    if mask is None:
        panel._mask_info_lbl.setText("未设置掩码")
        panel._mask_clear_btn.setEnabled(False)
    else:
        h, w = mask.shape[:2]
        panel._mask_info_lbl.setText(f"掩码 {w}×{h} 已加载：{path or '(内存)'}")
        panel._mask_clear_btn.setEnabled(True)


# ── 拆分预览开关 ──────────────────────────────────────────────────────────────

def build_split_preview_toggle(panel) -> QWidget:
    """「源画面 | 网格采样」并排预览开关（挂在实时预览组里）。"""
    host = QWidget()
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    panel._split_preview_chk = QCheckBox("并排预览（左源画面 / 右网格采样）")
    panel._split_preview_chk.setToolTip("对角校准：左半显示源帧，右半显示采样后的亮度网格")
    panel._split_preview_chk.toggled.connect(lambda v: on_split_preview_toggled(panel, v))
    row.addWidget(panel._split_preview_chk)
    row.addStretch()
    return host


def on_split_preview_toggled(panel, on: bool) -> None:
    canvas = getattr(panel, "_screen_preview_canvas", None)
    if canvas is not None and hasattr(canvas, "set_split_mode"):
        canvas.set_split_mode(bool(on))
