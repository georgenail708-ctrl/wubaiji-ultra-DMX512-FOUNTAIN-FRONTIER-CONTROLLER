"""M03 media presentation using the existing media factories and slots.

References: Effects Matrix / 编辑器分区; Qt Widgets APIs only.
Tabler glyphs (MIT) and the existing video/warp/mask widgets are reused.
The source, sampling, transport and output algorithms remain in their owners.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QTabWidget, QVBoxLayout,
)

from .. import qascade_theme as theme
from ..widget_kit import PanelSection
from . import matrix_w2_ui
from .matrix_pixel_properties_ui import number_row, page_layout
from .matrix_video_source_ui import (
    build_camera_row, build_mask_tab, build_split_preview_toggle,
    build_transport_row, build_warp_tab,
)


def _host():
    from . import matrix_slideshow_panel
    return matrix_slideshow_panel


def _note(text, *, mono=False):
    label = QLabel(text)
    label.setWordWrap(True)
    font = "font-family:Consolas;" if mono else ""
    label.setStyleSheet(f"color:{theme.TEXT_SECONDARY};font-size:11px;{font}")
    return label


def _group(title):
    group = QGroupBox(title)
    layout = QVBoxLayout(group)
    layout.setContentsMargins(4, 8, 4, 4)
    layout.setSpacing(4)
    return group, layout


def build_video_group(panel):
    """Build each original input control once, under its semantic section."""
    group, layout = _group("视频导入 / 实时输出")
    source = PanelSection("视频文件与摄像头", parent=group)
    buttons = QHBoxLayout()
    buttons.setSpacing(4)
    panel._video_import_btn = QPushButton("导入视频")
    panel._video_import_btn.clicked.connect(panel._on_video_import)
    buttons.addWidget(panel._video_import_btn)
    panel._video_clear_btn = QPushButton("删除视频")
    panel._video_clear_btn.clicked.connect(panel._on_video_clear)
    panel._video_clear_btn.setEnabled(False)
    buttons.addWidget(panel._video_clear_btn)
    source.add_layout(buttons)
    source.add_widget(build_camera_row(panel))
    panel._video_path_lbl = _note(
        "未导入视频；视频像素采样使用视频完整画面，不设置采样区域；"
        "使用顶部统一播放键启动预览与矩阵输出。")
    source.add_widget(panel._video_path_lbl)
    layout.addWidget(source)
    transport = PanelSection("走带与入出点", parent=group)
    transport.add_widget(build_transport_row(panel))
    panel._video_pos_slider = _host().ClickSlider(Qt.Orientation.Horizontal)
    panel._video_pos_slider.setRange(0, 0)
    panel._video_pos_slider.sliderReleased.connect(panel._on_video_seek_released)
    transport.add_widget(panel._video_pos_slider)
    panel._video_pos_lbl = _note("帧 0 / 0   00:00.00", mono=True)
    transport.add_widget(panel._video_pos_lbl)
    layout.addWidget(transport)
    external = PanelSection("外部输入源", parent=group)
    external.add_widget(matrix_w2_ui.build_external_source_row(panel))
    layout.addWidget(external)
    layout.addStretch()
    return group


def _sampling_preview(panel):
    group, layout = _group("实时采样预览")
    panel._screen_preview_canvas = _host()._ScreenPreviewCanvas()
    panel._screen_preview_canvas.setMinimumHeight(100)
    panel._screen_preview_canvas.setMaximumHeight(160)
    layout.addWidget(panel._screen_preview_canvas)
    panel._screen_stats_lbl = _note(
        "就绪: min=0.00  max=0.00  mean=0.00", mono=True)
    layout.addWidget(panel._screen_stats_lbl)
    layout.addWidget(build_split_preview_toggle(panel))
    panel._sample_preview_grp = group
    return group


def _region_page(panel):
    group, layout = _group("采样区域")
    monitor = QHBoxLayout()
    monitor.setSpacing(4)
    monitor.addWidget(QLabel("显示器:"))
    panel._monitor_combo = QComboBox()
    panel._monitor_combo.addItem("主显示器", 1)
    panel._monitor_combo.currentIndexChanged.connect(panel._on_monitor_changed)
    monitor.addWidget(panel._monitor_combo, 1)
    layout.addLayout(monitor)
    overlay = _host().tabler_button(
        "打开采样框（可拖动/缩放）", "crop", theme.TEXT_PRIMARY)
    overlay.clicked.connect(panel._open_screen_overlay)
    layout.addWidget(overlay)
    panel._region_info_lbl = _note("区域：全屏", mono=True)
    layout.addWidget(panel._region_info_lbl)
    layout.addWidget(_note("手动输入坐标（全部留 0 = 全屏）："))
    manual = QGridLayout()
    manual.setSpacing(4)
    panel._region_spins = {}
    for index, (label, key) in enumerate((
            ("左:", "left"), ("上:", "top"),
            ("宽:", "width"), ("高:", "height"))):
        manual.addWidget(QLabel(label), index // 2, (index % 2) * 2)
        spin = QSpinBox()
        spin.setRange(0, 9999)
        spin.setValue(0)
        spin.setFixedWidth(68)
        panel._region_spins[key] = spin
        manual.addWidget(spin, index // 2, (index % 2) * 2 + 1)
    apply = QPushButton("应用区域")
    apply.setFixedHeight(24)
    apply.clicked.connect(panel._on_region_changed)
    manual.addWidget(apply, 2, 0, 1, 4)
    layout.addLayout(manual)
    layout.addStretch()
    return group


def _sampling_page(panel):
    group = QGroupBox("采样参数")
    grid = QGridLayout(group)
    grid.setContentsMargins(4, 8, 4, 4)
    grid.setSpacing(4)
    grid.setColumnStretch(1, 1)
    grid.addWidget(QLabel("采样通道:"), 0, 0)
    panel._channel_combo = QComboBox()
    try:
        from .screen_capture_engine import CHANNEL_MODES
        for channel_id, label in CHANNEL_MODES:
            panel._channel_combo.addItem(label, channel_id)
    except ImportError:
        panel._channel_combo.addItem("亮度（灰度）", "luminance")
    panel._channel_combo.currentIndexChanged.connect(panel._on_channel_changed)
    grid.addWidget(panel._channel_combo, 0, 1, 1, 2)
    specs = (
        ("_brightness", "亮度增益", "screen_brightness_boost", 0.01, 20.0,
         1.0, 0.01, 3, True, {}),
        ("_contrast", "对比度", "screen_contrast", 0.01, 20.0,
         1.0, 0.01, 3, True, {}),
        ("_gamma", "采样 Gamma", "screen_gamma", 0.05, 10.0,
         1.0, 0.01, 3, True, {}),
        ("_smooth", "时间平滑", "screen_smooth", 0.0, 0.995,
         0.6, 0.001, 3, False, {"scale": 1000}),
        ("_thr_lo", "阈值下限", "screen_threshold_low", 0.0, 1.0,
         0.0, 0.001, 3, False, {}),
        ("_thr_hi", "阈值上限", "screen_threshold_high", 0.0, 1.0,
         1.0, 0.001, 3, False, {}),
    )
    for row, spec in enumerate(specs, 1):
        number_row(panel, grid, row, spec)
    grid.addWidget(QLabel("采样帧率:"), 7, 0)
    panel._screen_fps_spin = QSpinBox()
    panel._screen_fps_spin.setRange(1, 240)
    panel._screen_fps_spin.setValue(25)
    panel._screen_fps_spin.setFixedWidth(74)
    panel._screen_fps_spin.valueChanged.connect(panel._on_screen_fps_changed)
    grid.addWidget(panel._screen_fps_spin, 7, 1, 1, 2)
    grid.setRowStretch(8, 1)
    return group


def build_screen_page(panel):
    """Preserve original tab identities/order and the original sole preview."""
    page, layout = page_layout()
    panel._screen_config_tabs = QTabWidget()
    panel._screen_config_tabs.setTabPosition(QTabWidget.TabPosition.North)
    panel._screen_config_tabs.setDocumentMode(True)
    layout.addWidget(panel._screen_config_tabs, 1)
    panel._screen_video_tab = panel._build_video_group()
    panel._screen_config_tabs.addTab(panel._screen_video_tab, "视频")
    panel._warp_tab = build_warp_tab(panel)
    panel._mask_tab = build_mask_tab(panel)
    layout.addWidget(_sampling_preview(panel))
    panel._screen_region_tab = _region_page(panel)
    panel._screen_config_tabs.addTab(panel._screen_region_tab, "区域")
    panel._screen_params_tab = _sampling_page(panel)
    panel._screen_config_tabs.addTab(panel._screen_params_tab, "采样")
    panel._screen_overlay = None
    return page
