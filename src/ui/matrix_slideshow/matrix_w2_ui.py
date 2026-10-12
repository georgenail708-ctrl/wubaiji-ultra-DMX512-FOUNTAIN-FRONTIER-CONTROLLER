# -*- coding: utf-8 -*-
"""matrix_w2_ui.py — Matrix Wave2 剩余项的面板接线（深查②三.Matrix #4/#6/#10/#12/#13/#14/#16/#17）。

设计纪律（承 R183 前批）：**新逻辑放本模块**，宿主面板/`PixelEffectParamPanel`
只留 1-2 行调用点；每个控件都是薄回调（引擎属性读写 / 弹层打开），
重逻辑在各自纯逻辑模块：
  * #4/#6  → `matrix_external_sources`（find_spec 探测 + 帧桥）
  * #10    → `matrix_slide_engine` 的 `effective_hold_time`（已有）+ 本模块控件
  * #12    → `matrix_frame_recorder`
  * #13    → `matrix_level_window`
  * #14    → `matrix_frame_library`
  * #16    → `matrix_param_random`
  * #17    → `matrix_serial_bridge`
"""
from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QVBoxLayout, QWidget,
)

logger = logging.getLogger(__name__)

_DIM = "color:#888;font-size:10px;"


def _emit_params(panel) -> None:
    """参数变更广播（signal 缺失/异常静默；helper 压 CC）。"""
    emit = getattr(panel, "params_changed", None)
    if emit is None:
        return
    try:
        emit.emit()
    except Exception:
        logger.debug("matrix: emit params_changed failed", exc_info=True)


def _btn(panel, text: str, tip: str = "") -> QPushButton:
    """统一风格的小按钮（面板 _mk_btn 可用时优先；helper 压 CC）。"""
    mk = getattr(panel, "_mk_btn", None)
    if callable(mk):
        try:
            btn = mk(text, "#2D2D2D", "#71A8FF")
            if tip:
                btn.setToolTip(tip)
            return btn
        except Exception:
            logger.debug("matrix: _mk_btn failed, fallback", exc_info=True)
    btn = QPushButton(text)
    if tip:
        btn.setToolTip(tip)
    return btn


# ── #4/#6 外部纹理源（Spout / NDI 登记位） ────────────────────────────────────

def apply_mode_selection(panel, mode_id: str) -> str:
    """像素模式下拉选中值 → 引擎真实模式 id（外部登记位归一到视频采样 + 启用帧源）。

    返回应传给 ``engine.set_mode`` / ``show_mode_params`` 的模式 id；顺带同步下拉
    的选中项与帧源 intent（无库时只落盘意图，装机后自动可用）。
    """
    from .matrix_external_sources import apply_external_source, resolve_pixel_mode
    eng = getattr(panel, "_engine", None)
    if eng is None:
        return str(mode_id or "")
    real_mode, ext_kind = resolve_pixel_mode(mode_id)
    if not ext_kind:
        return real_mode
    _, msg = apply_external_source(eng, ext_kind)
    if hasattr(panel, "_ext_source_combo"):
        idx = panel._ext_source_combo.findData(ext_kind)
        if idx >= 0 and panel._ext_source_combo.currentIndex() != idx:
            panel._ext_source_combo.blockSignals(True)
            panel._ext_source_combo.setCurrentIndex(idx)
            panel._ext_source_combo.blockSignals(False)
    if hasattr(panel, "_ext_source_lbl"):
        panel._ext_source_lbl.setText(msg)
    return real_mode


def build_external_source_row(panel) -> QWidget:
    """视频页「外部纹理源」行：下拉 + 状态标签（无库 → 装机提示，零开销）。"""
    from .matrix_external_sources import (
        EXTERNAL_MODES, MODE_EXT_NDI, MODE_EXT_SPOUT, describe_slot,
    )
    host = QWidget()
    row = QGridLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    row.addWidget(QLabel("外部纹理:"), 0, 0)
    panel._ext_source_combo = QComboBox()
    panel._ext_source_combo.addItem("内部源（文件 / 摄像头）", "")
    for mode_id in (MODE_EXT_SPOUT, MODE_EXT_NDI):
        meta = EXTERNAL_MODES.get(mode_id, {})
        panel._ext_source_combo.addItem(str(meta.get("label", mode_id)),
                                        str(meta.get("kind", "")))
        if meta.get("future"):
            item = panel._ext_source_combo.model().item(panel._ext_source_combo.count() - 1)
            item.setFlags(item.flags() & ~(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable))
    panel._ext_source_combo.setToolTip(
        "Spout：接 外部 VJ 软件的共享纹理（需 pip install SpoutGL，\n"
        "未安装时本项仅登记、零开销）；NDI 当前不提供接收，仅保留旧工程回显。\n"
        "选择后仍用视频像素采样模式出光，四角透视/掩码/色调链照旧生效。")
    panel._ext_source_combo.currentIndexChanged.connect(
        lambda _i: on_external_source_changed(panel))
    row.addWidget(panel._ext_source_combo, 0, 1)
    panel._ext_source_lbl = QLabel(describe_slot(""))
    panel._ext_source_lbl.setStyleSheet(_DIM)
    panel._ext_source_lbl.setWordWrap(True)
    row.addWidget(panel._ext_source_lbl, 1, 0, 1, 2)
    return host


def _align_engine_to_video(panel, eng) -> None:
    """选择外部源时把模式/下拉/参数页对齐到视频采样（helper 压 CC）。"""
    try:
        from .matrix_pixel_engine import MODE_VIDEO_SAMPLE
        if str(getattr(eng, "_mode", "")) == MODE_VIDEO_SAMPLE:
            return
        eng.set_mode(MODE_VIDEO_SAMPLE)
        combo = getattr(panel, "_pixel_effect_combo", None)
        if combo is not None:
            idx = combo.findData(MODE_VIDEO_SAMPLE)
            if idx >= 0 and combo.currentIndex() != idx:
                combo.blockSignals(True)
                combo.setCurrentIndex(idx)
                combo.blockSignals(False)
        show = getattr(panel, "show_mode_params", None)
        if callable(show):
            show(MODE_VIDEO_SAMPLE)
    except Exception:
        logger.debug("matrix: align engine to video mode failed", exc_info=True)


def on_external_source_changed(panel) -> None:
    """下拉 → 引擎帧源意图（无库也落盘意图：装机后自动可用）。

    选择外部源时若引擎没在视频采样模式，一并对齐（否则帧进了采样链也不出光）。
    """
    from .matrix_external_sources import apply_external_source
    eng = getattr(panel, "_engine", None)
    if eng is None:
        return
    kind = str(panel._ext_source_combo.currentData() or "")
    _ok, msg = apply_external_source(eng, kind)
    panel._ext_source_lbl.setText(msg)
    if kind:
        _align_engine_to_video(panel, eng)
    _emit_params(panel)


def sync_external_source_widgets(panel) -> None:
    """工程还原后按引擎 ``video_source`` 回填下拉 + 状态（_sync_pixel_param_widgets 调用）。"""
    from .matrix_external_sources import (
        describe_slot, normalize_source_kind, sync_external_source,
    )
    if not hasattr(panel, "_ext_source_combo"):
        return
    eng = getattr(panel, "_engine", None)
    kind = normalize_source_kind(getattr(eng, "video_source", "file"))
    idx = panel._ext_source_combo.findData(kind)
    if idx < 0:
        idx = 0
    panel._ext_source_combo.blockSignals(True)
    panel._ext_source_combo.setCurrentIndex(idx)
    panel._ext_source_combo.blockSignals(False)
    panel._ext_source_lbl.setText(describe_slot(kind))
    if eng is not None:
        sync_external_source(eng)


def external_source_selected(engine) -> bool:
    """引擎当前帧源是否为外部纹理（spout/ndi）——播放前置检查用。"""
    from .matrix_external_sources import (
        EXTERNAL_SOURCE_KINDS, normalize_source_kind,
    )
    return normalize_source_kind(getattr(engine, "video_source", "file")) in EXTERNAL_SOURCE_KINDS


def sync_w2_widgets(panel) -> None:
    """工程还原尾部统一同步：BPM 控件 + 外部纹理源（load_state 经 _sync_output_gamma 调用）。"""
    sync_slide_bpm_widgets(panel)
    sync_external_source_widgets(panel)


# ── #10 BPM 量化播放（按 BPM×N 切帧） ─────────────────────────────────────────

def build_slide_bpm_row(panel) -> QWidget:
    """BPM 切帧行：开关 + BPM + 拍数 + 每帧时长回显（写 slideshow config）。"""
    host = QWidget()
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    panel._bpm_enable_chk = QCheckBox("BPM 切帧")
    panel._bpm_enable_chk.setToolTip(
        "按节拍切帧：每帧停留时长 = 60/BPM × 拍数（与音频心跳同步同一节拍口径）；\n"
        "关闭 = 用每帧自己的停留时长（旧行为）。")
    panel._bpm_enable_chk.toggled.connect(lambda _v: on_bpm_changed(panel))
    row.addWidget(panel._bpm_enable_chk)
    panel._bpm_spin = QDoubleSpinBox()
    panel._bpm_spin.setRange(1.0, 600.0)
    panel._bpm_spin.setDecimals(1)
    panel._bpm_spin.setSingleStep(1.0)
    panel._bpm_spin.setValue(120.0)
    panel._bpm_spin.setSuffix(" BPM")
    panel._bpm_spin.setFixedWidth(92)
    panel._bpm_spin.valueChanged.connect(lambda _v: on_bpm_changed(panel))
    row.addWidget(panel._bpm_spin)
    panel._bpm_beats_spin = QSpinBox()
    panel._bpm_beats_spin.setRange(1, 64)
    panel._bpm_beats_spin.setValue(1)
    panel._bpm_beats_spin.setSuffix(" 拍")
    panel._bpm_beats_spin.setFixedWidth(64)
    panel._bpm_beats_spin.valueChanged.connect(lambda _v: on_bpm_changed(panel))
    row.addWidget(panel._bpm_beats_spin)
    panel._bpm_info_lbl = QLabel("")
    panel._bpm_info_lbl.setStyleSheet(_DIM)
    row.addWidget(panel._bpm_info_lbl, 1)
    return host


def attach_slide_bpm_row(panel, slide_param_panel=None) -> Optional[QWidget]:
    """把 BPM 行插进「幻灯片参数」面板（末尾 stretch 之前）；无控件 → None。

    ``slide_param_panel`` 可显式传入（测试/多宿主复用）；缺省取
    ``panel._slide_param_panel``（面板构造期已建好）。
    """
    sp = slide_param_panel if slide_param_panel is not None else \
        getattr(panel, "_slide_param_panel", None)
    lay = sp.layout() if sp is not None else None
    if lay is None:
        return None
    host = build_slide_bpm_row(panel)
    lay.insertWidget(max(0, lay.count() - 1), host)
    sync_slide_bpm_widgets(panel)
    return host


def on_bpm_changed(panel) -> None:
    """BPM 控件 → slideshow config（即时生效；播放中下一帧起算）。"""
    eng = getattr(panel, "_engine", None)
    cfg = getattr(eng, "config", None)
    if cfg is None or not hasattr(panel, "_bpm_enable_chk"):
        return
    cfg.bpm_enabled = bool(panel._bpm_enable_chk.isChecked())
    try:
        cfg.bpm = float(panel._bpm_spin.value())
        cfg.bpm_beats = int(panel._bpm_beats_spin.value())
    except Exception:
        logger.debug("matrix: write bpm config failed", exc_info=True)
    _refresh_bpm_info(panel, cfg)
    try:
        from .matrix_slideshow_panel import _mark_panel_dirty
        _mark_panel_dirty(panel)
    except Exception:
        logger.debug("matrix: mark dirty after bpm change failed", exc_info=True)


def _refresh_bpm_info(panel, cfg) -> None:
    """每帧时长回显（helper）。"""
    if not hasattr(panel, "_bpm_info_lbl"):
        return
    en = bool(getattr(cfg, "bpm_enabled", False))
    bpm = float(getattr(cfg, "bpm", 120.0) or 120.0)
    beats = max(1, int(getattr(cfg, "bpm_beats", 1) or 1))
    if not en:
        panel._bpm_info_lbl.setText("未启用（按每帧停留时长）")
        return
    hold = (60.0 / bpm) * beats if bpm > 0 else 0.0
    panel._bpm_info_lbl.setText(f"每帧 {hold:0.3f}s（{beats}/{bpm:g} 拍）")


def sync_slide_bpm_widgets(panel) -> None:
    """工程还原后按 config 回填 BPM 控件（load_state 尾部调用）。"""
    cfg = getattr(getattr(panel, "_engine", None), "config", None)
    if cfg is None or not hasattr(panel, "_bpm_enable_chk"):
        return
    panel._bpm_enable_chk.blockSignals(True)
    panel._bpm_spin.blockSignals(True)
    panel._bpm_beats_spin.blockSignals(True)
    panel._bpm_enable_chk.setChecked(bool(getattr(cfg, "bpm_enabled", False)))
    panel._bpm_spin.setValue(float(getattr(cfg, "bpm", 120.0) or 120.0))
    panel._bpm_beats_spin.setValue(max(1, int(getattr(cfg, "bpm_beats", 1) or 1)))
    panel._bpm_enable_chk.blockSignals(False)
    panel._bpm_spin.blockSignals(False)
    panel._bpm_beats_spin.blockSignals(False)
    _refresh_bpm_info(panel, cfg)


# ── #12 输出录制为时间线 clip ────────────────────────────────────────────────

def build_record_controls(panel) -> QWidget:
    """录制控件：秒数 + 录制/停止按钮 + 状态标签（主界面底部行挂载）。"""
    from .matrix_frame_recorder import recorder_of
    host = QWidget()
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    btn = _btn(panel, "录制输出",
               "把矩阵当前输出帧流录 N 秒，停止即 bake 成时间线 clip\n"
               "（有注入的时间线则直接走其录制通道；否则自建 clip 挂到首条轨道）")
    btn.clicked.connect(lambda: on_record_clicked(panel))
    panel._record_btn = btn
    row.addWidget(btn)
    spin = QDoubleSpinBox()
    spin.setRange(1.0, 600.0)
    spin.setDecimals(1)
    spin.setSingleStep(1.0)
    spin.setValue(5.0)
    spin.setSuffix(" s")
    spin.setFixedWidth(70)
    spin.setToolTip("录制时长（秒；到时自动停止并 bake）")
    panel._record_seconds_spin = spin
    row.addWidget(spin)
    lbl = QLabel("未录制")
    lbl.setStyleSheet(_DIM)
    panel._record_state_lbl = lbl
    row.addWidget(lbl)
    rec = recorder_of(panel)
    rec.state_changed.connect(lbl.setText)
    from .matrix_record_state import bind_record_button
    bind_record_button(rec, btn)
    return host


def on_record_clicked(panel) -> None:
    """录制/停止切换 + 状态回显 + 置脏（产出 clip 也算工程改动）。"""
    from .matrix_frame_recorder import recorder_of, toggle_recording
    rec = recorder_of(panel)
    seconds = float(getattr(panel, "_record_seconds_spin", None).value()) \
        if hasattr(panel, "_record_seconds_spin") else 5.0
    ok, msg = toggle_recording(panel, seconds)
    _ = ok
    if hasattr(panel, "_record_state_lbl"):
        panel._record_state_lbl.setText(rec.state_text() if rec.recording else msg)
    if hasattr(panel, "_record_btn"):
        panel._record_btn.setText("停止录制" if rec.recording else "录制输出")
    try:
        from .matrix_slideshow_panel import _mark_panel_dirty
        _mark_panel_dirty(panel)
    except Exception:
        logger.debug("matrix: mark dirty after record failed", exc_info=True)


# ── #13 输出电平侧窗 ─────────────────────────────────────────────────────────

def open_level_window_for(panel) -> None:
    """打开「矩阵 · 输出电平」侧窗（20Hz、pyqtgraph 柱图）。"""
    from .matrix_level_window import open_level_window
    open_level_window(panel)


# ── #14 帧库导出/导入 ────────────────────────────────────────────────────────

def extend_frame_ops_with_library(panel, lay) -> None:
    """向「帧操作」行追加帧库导出/导入两个按钮（跨工程分享帧序列模板）。"""
    for label, tip, slot in (
        ("导出帧库", "把当前帧序列导出为 .json 帧库（可跨工程/跨机器分享）",
         lambda: _library_export(panel)),
        ("导入帧库", "导入 .json 帧库并**追加**到当前帧序列（按设备 id 匹配）",
         lambda: _library_import(panel)),
    ):
        btn = _btn(panel, label, tip)
        btn.setFixedHeight(22)
        btn.clicked.connect(slot)
        lay.addWidget(btn)


def _library_export(panel) -> None:
    from .matrix_frame_library import export_frame_library
    n = export_frame_library(panel)
    _library_toast(panel, n, "导出", "帧库已导出")


def _library_import(panel) -> None:
    from .matrix_frame_library import import_frame_library
    n = import_frame_library(panel)
    _library_toast(panel, n, "导入", "帧库已导入")


def _library_toast(panel, count: int, verb: str, ok_text: str) -> None:
    """结果提示（0=取消静默；-1=失败已弹过；成功 → 状态标签/提示；helper）。"""
    if count <= 0:
        return
    msg = f"{ok_text}：{verb} {count} 帧"
    try:
        from .matrix_slideshow_panel import _mark_panel_dirty
        _mark_panel_dirty(panel)          # 帧序列变了 → 置脏
    except Exception:
        logger.debug("matrix: mark dirty after library op failed", exc_info=True)
    for attr in ("_frame_lib_lbl",):
        lbl = getattr(panel, attr, None)
        if lbl is not None:
            lbl.setText(msg)
            return
    logger.info("matrix: %s", msg)


# ── #16 像素效果参数随机化 ───────────────────────────────────────────────────

def add_pixel_randomize_row(pixel_panel, lay) -> None:
    """在像素参数面板顶部追加「随机参数」行（限幅扰动）。"""
    host = QWidget()
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    btn = _btn(pixel_panel, "随机参数",
               "只对数值参数做**限幅扰动**（在现有画面附近微调，不整域乱跳）\n"
               "范围与参数表在 matrix_param_random；写入走面板既有同步链")
    btn.clicked.connect(lambda: on_pixel_randomize(pixel_panel))
    row.addWidget(btn)
    lbl = QLabel("")
    lbl.setStyleSheet(_DIM)
    pixel_panel._random_info_lbl = lbl
    row.addWidget(lbl, 1)
    lay.addWidget(host)


def on_pixel_randomize(pixel_panel) -> None:
    """随机一次并把改动数回显（参数面板 params_changed 会广播给宿主）。"""
    from .matrix_param_random import randomize_pixel_params
    res = randomize_pixel_params(pixel_panel)
    lbl = getattr(pixel_panel, "_random_info_lbl", None)
    if lbl is not None:
        count = int(res.get("count", 0) or 0)
        keys = list((res.get("changed") or {}).keys())
        if count:
            tail = "…" if len(keys) > 4 else ""
            lbl.setText(f"已扰动 {count} 项：{', '.join(keys[:4])}{tail}")
        else:
            lbl.setText("无可扰动参数")


# ── #17 串口桥接说明 ────────────────────────────────────────────────────────

def open_serial_bridge_help(panel) -> None:
    """弹层展示 串口固件/WS2812 桥接说明（只读文档，不做串口协议栈）。"""
    from .matrix_serial_bridge import open_serial_bridge_info
    open_serial_bridge_info(panel)


# ── 主界面底部行：追加 W2 按钮（由面板 `_add_help_button` 调用一处） ─────────

def add_main_ext_buttons(panel, toolbar) -> None:
    """在底部通用控件行追加：录制控件 / 输出电平 / 说明。"""
    toolbar.addWidget(_sep(panel))
    toolbar.addWidget(build_record_controls(panel))
    toolbar.addWidget(_sep(panel))
    level_btn = _btn(panel, "输出电平", "打开输出电平侧窗（20Hz DMX 值分布/统计）")
    level_btn.clicked.connect(lambda: open_level_window_for(panel))
    toolbar.addWidget(level_btn)
    serial_btn = _btn(panel, "桥接", "WS2812/经 Art-Net 对接说明（不自写串口）")
    serial_btn.clicked.connect(lambda: open_serial_bridge_help(panel))
    toolbar.addWidget(serial_btn)


def _sep(panel) -> QWidget:
    """分隔符（面板 _mk_sep 可用时优先；helper）。"""
    mk = getattr(panel, "_mk_sep", None)
    if callable(mk):
        try:
            return mk()
        except Exception:
            logger.debug("matrix: _mk_sep failed, fallback", exc_info=True)
    sep = QWidget()
    sep.setFixedWidth(1)
    sep.setStyleSheet("background:#3D3D3D;")
    sep.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
    return sep


__all__ = [
    "add_main_ext_buttons", "add_pixel_randomize_row", "apply_mode_selection",
    "attach_slide_bpm_row", "build_external_source_row", "build_record_controls",
    "build_slide_bpm_row", "extend_frame_ops_with_library",
    "external_source_selected", "on_bpm_changed", "on_external_source_changed",
    "on_pixel_randomize", "on_record_clicked", "open_level_window_for",
    "open_serial_bridge_help", "sync_external_source_widgets", "sync_slide_bpm_widgets",
    "sync_w2_widgets",
]
