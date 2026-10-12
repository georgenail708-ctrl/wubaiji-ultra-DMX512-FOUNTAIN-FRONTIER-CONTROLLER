"""M03 parameter regions; original Pixel factories remain the numeric authority.

References: 编辑器分区/Properties; Qt Widgets layout APIs (API use only).
Existing PanelSection/ClickSlider and Tabler (MIT) are reused, not reimplemented.
Adoption and exact ranges: r185-matrix-pixel-redesign-preflight.md, sections 0/2.
"""
from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSlot
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QGridLayout, QLabel, QPushButton, QScrollArea,
    QSizePolicy, QSplitter, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import qascade_theme as theme
from ..widget_kit import PanelSection


class _PropertiesLayoutContext(QObject):
    """Adapt the original property regions, with no polling or value writes."""

    def __init__(self, panel):
        super().__init__(panel)
        self._panel = panel
        self.split = None
        self.stack = None
        panel.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self._panel and event.type() == QEvent.Type.Resize:
            self.configure()
        return False

    @pyqtSlot(int)
    def configure(self, _index=0):
        if self.split is None:
            return
        media = self.stack.currentWidget() is self._panel._screen_page
        narrow = self._panel.width() < (760 if media else 560)
        orientation = Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal
        if self.split.orientation() == orientation:
            return
        self.split.setOrientation(orientation)
        size = self._panel.height() if narrow else self._panel.width()
        self.split.setSizes([max(200, size - 200), 200])


def page_layout():
    """Create a parameter page without moving items from an old layout."""
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(4, 4, 4, 4)
    layout.setSpacing(4)
    return page, layout


def section_grid(title, parent=None):
    section = PanelSection(title, parent=parent)
    body = QWidget(section)
    grid = QGridLayout(body)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(4)
    grid.setColumnStretch(1, 1)
    section.add_widget(body)
    return section, grid


def number_row(panel, grid, row, spec, stacked=False):
    """Present the original factory's slider/spin pair; no new value mapping."""
    field, label, attr, lo, hi, initial, step, decimals, fine, options = spec
    caption = QLabel(label)
    setattr(panel, field + "_caption", caption)
    caption.setWordWrap(True)
    caption.setStyleSheet(f"color:{theme.TEXT_SECONDARY};")
    caption.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
    caption.setMinimumWidth(64)
    if stacked:
        grid.addWidget(caption, row, 1, 1, 2)
        row += 1
    else:
        caption.setMaximumWidth(96)
        grid.addWidget(caption, row, 0)
    factory = panel._make_fine_low_slider_spin if fine else panel._make_slider_spin
    slider, spin = factory(grid, row, label, attr, lo, hi, initial,
                           step, decimals, **options)
    slider.setMinimumWidth(32)
    setattr(panel, field + "_slider", slider)
    setattr(panel, field + "_spin", spin)


def combo_row(panel, grid, row, spec):
    field, label, attr, choices = spec
    caption = QLabel(label)
    setattr(panel, field + "_caption", caption)
    caption.setWordWrap(True)
    caption.setMaximumWidth(96)
    caption.setStyleSheet(f"color:{theme.TEXT_SECONDARY};")
    grid.addWidget(caption, row, 0)
    combo = QComboBox()
    for text, data in choices:
        combo.addItem(text, data)
    setattr(panel, field, combo)
    combo.currentIndexChanged.connect(
        lambda _index: panel._apply_combo_param(attr, combo))
    grid.addWidget(combo, row, 1, 1, 2)
    return combo


def _common_parameters(panel):
    page, layout = page_layout()
    section, grid = section_grid("通用参数", page)
    number_row(panel, grid, 0, (
        "_threshold", "ON/OFF 阈值", "threshold", 0.0, 1.0, 0.5,
        0.01, 3, False, {}), stacked=True)
    panel._speed_caption = QLabel("效果速度")
    grid.addWidget(panel._speed_caption, 2, 1, 1, 2)
    panel._speed_slider, panel._speed_spin = panel._make_speed_slider_spin(grid, 3)
    panel._speed_slider.setMinimumWidth(32)
    panel._invert_cb = QCheckBox("反转 ON/OFF")
    panel._invert_cb.toggled.connect(panel._on_invert_changed)
    grid.addWidget(panel._invert_cb, 4, 1, 1, 2)
    panel._audio_reactive_cb = QCheckBox("音频驱动（关闭=自走）")
    panel._audio_reactive_cb.setChecked(True)
    panel._audio_reactive_cb.toggled.connect(panel._on_audio_reactive_changed)
    grid.addWidget(panel._audio_reactive_cb, 5, 1, 1, 2)
    grid.addWidget(QLabel("矩阵颜色"), 6, 1, 1, 2)
    panel._matrix_color_btn = QPushButton()
    panel._matrix_color_btn.setFixedSize(96, 24)
    panel._matrix_color_btn.setToolTip(
        "矩阵效果自己的颜色；Programmer 颜色不会影响矩阵效果颜色")
    panel._matrix_color_btn.clicked.connect(panel._on_matrix_color_pick)
    grid.addWidget(panel._matrix_color_btn, 7, 1, 1, 2)
    panel._update_matrix_color_button()
    layout.addWidget(section)
    note = QLabel("播放与输出由 Matrix 主窗控制。\n"
                  "采样 Gamma、条形 Gamma 与输出 Gamma 分别配置。")
    note.setWordWrap(True)
    note.setStyleSheet(f"color:{theme.TEXT_SECONDARY};font-size:11px;")
    layout.addWidget(note)
    layout.addStretch()
    return page


def _wave_parameters(panel, parent):
    section, grid = section_grid("对称波动参数", parent)
    specs = (
        ("_wave_density", "波纹密度", "matrix_wave_density", 0.01, 12.0,
         1.0, 0.01, 3, True, {}),
        ("_wave_width", "波纹宽度", "matrix_wave_width", 0.005, 2.0,
         0.12, 0.005, 3, True, {"scale": 1000}),
        ("_wave_twist", "旋转/扭曲", "matrix_wave_twist", -8.0, 8.0,
         0.0, 0.01, 3, False, {}),
        ("_symmetry_mix", "对称混合", "matrix_symmetry_mix", 0.0, 1.0,
         0.5, 0.01, 3, False, {}),
    )
    for row, spec in enumerate(specs):
        number_row(panel, grid, row, spec)
    return section


def _audio_mod_parameters(panel, parent):
    section, grid = section_grid("图案音频调制", parent)
    combo_row(panel, grid, 0, (
        "_audio_mod_target_combo", "调制目标", "matrix_audio_mod_target", (
            ("关闭", "off"), ("亮度", "brightness"), ("速度", "speed"),
            ("亮度+速度", "brightness_speed"), ("密度/纹理", "density"),
            ("全部", "all"))))
    combo_row(panel, grid, 1, (
        "_audio_mod_source_combo", "音频来源", "matrix_audio_mod_source", (
            ("整体 RMS", "rms"), ("低频", "bass"), ("中频", "mid"),
            ("高频", "treble"), ("节拍", "beat"))))
    specs = (
        ("_audio_mod_amount", "调制强度", "matrix_audio_mod_amount", 0.0, 4.0,
         0.6, 0.01, 3, True, {}),
        ("_audio_mod_floor", "亮度下限", "matrix_audio_mod_floor", 0.0, 1.0,
         0.15, 0.01, 3, False, {}),
        ("_audio_mod_attack", "Attack", "matrix_audio_mod_attack_ms", 1.0, 1000.0,
         35.0, 1.0, 0, True, {"scale": 1, "suffix": "ms"}),
        ("_audio_mod_release", "Release", "matrix_audio_mod_release_ms", 1.0, 3000.0,
         220.0, 1.0, 0, True, {"scale": 1, "suffix": "ms"}),
    )
    for row, spec in enumerate(specs, 2):
        number_row(panel, grid, row, spec)
    return section


def _effect_page(panel):
    page, layout = page_layout()
    note = QLabel("动态效果使用通用速度、阈值、翼、块控制。\n"
                  "翼表示重复的同形波形；对称波动类效果可在下方细调。")
    note.setWordWrap(True)
    note.setStyleSheet(f"color:{theme.TEXT_SECONDARY};font-size:11px;")
    layout.addWidget(note)
    layout.addWidget(_wave_parameters(panel, page))
    layout.addWidget(_audio_mod_parameters(panel, page))
    layout.addStretch()
    return page


def build_audio_page(panel):
    page, layout = page_layout()
    section, grid = section_grid("音频输入", page)
    grid.addWidget(QLabel("音频设备"), 0, 0)
    panel._audio_device_combo = QComboBox()
    panel._audio_device_combo.addItem("默认设备", -1)
    panel._audio_device_combo.currentIndexChanged.connect(panel._on_audio_device_changed)
    grid.addWidget(panel._audio_device_combo, 0, 1, 1, 2)
    number_row(panel, grid, 1, (
        "_amplitude", "振幅灵敏度", "audio_amplitude", 0.01, 20.0,
        2.0, 0.01, 3, True, {}))
    number_row(panel, grid, 2, (
        "_norm_offset", "低频补偿", "audio_norm_offset", -5.0, 5.0,
        0.0, 0.01, 3, False, {}))
    layout.addWidget(section)
    panel._audio_style_section = _audio_style_parameters(panel, page)
    panel._audio_peak_section = _audio_peak_parameters(panel, page)
    layout.addWidget(panel._audio_style_section)
    layout.addWidget(panel._audio_peak_section)
    from .matrix_pixel_music_controls import build_music_controls
    layout.addWidget(build_music_controls(panel, page))
    layout.addStretch()
    return page


def _audio_style_parameters(panel, parent):
    section, grid = section_grid("条形表现", parent)
    grid.addWidget(QLabel("柱状图方向"), 0, 0)
    panel._bar_dir_combo = QComboBox()
    panel._bar_dir_combo.addItems(["从底部向上", "从顶部向下", "从左向右", "从右向左"])
    panel._bar_dir_combo.currentIndexChanged.connect(panel._on_bar_dir_changed)
    grid.addWidget(panel._bar_dir_combo, 0, 1, 1, 2)
    combo_row(panel, grid, 1, (
        "_bar_style_combo", "柱状样式", "audio_bar_style", (
            ("实心", "solid"), ("柔边", "soft"), ("点阵", "dots"),
            ("中心镜像", "center"), ("扫描纹理", "scan"))))
    combo_row(panel, grid, 2, (
        "_band_layout_combo", "频段布局", "audio_band_layout", (
            ("线性低到高", "linear"), ("反向高到低", "reverse"),
            ("中心低频", "center_low"), ("边缘低频", "edge_low"),
            ("阶梯展开", "stair"), ("随机洗牌", "random"))))
    specs = (
        ("_bar_gamma", "条形 Gamma", "audio_bar_gamma", 0.05, 3.0,
         0.85, 0.01, 3, True, {}),
        ("_bar_soft", "柔边宽度", "audio_bar_softness", 0.0, 0.5,
         0.08, 0.005, 3, False, {"scale": 1000}),
        ("_bar_smooth", "条形平滑", "audio_bar_smoothing", 0.0, 0.95,
         0.35, 0.01, 3, False, {}),
        ("_bar_motion", "纹理运动", "audio_bar_motion", 0.0, 8.0,
         1.0, 0.01, 3, True, {}),
    )
    for row, spec in enumerate(specs, 3):
        number_row(panel, grid, row, spec)
    return section


def _audio_peak_parameters(panel, parent):
    section, grid = section_grid("峰值保持", parent)
    number_row(panel, grid, 0, (
        "_peak_hold", "峰值保持", "audio_peak_hold", 0.0, 3.0,
        0.45, 0.01, 3, True, {"suffix": "s"}))
    number_row(panel, grid, 1, (
        "_peak_decay", "峰值下落", "audio_peak_decay", 0.001, 0.3,
        0.035, 0.001, 3, True, {"scale": 1000}))
    return section


def _scroll(widget, parent):
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setWidget(widget)
    return scroll


def build_pixel_parameters(panel):
    """Keep original page order and factories, split details from common values."""
    context = _PropertiesLayoutContext(panel)
    root = QVBoxLayout(panel)
    root.setContentsMargins(0, 0, 0, 0)
    root.setSpacing(4)
    common = _common_parameters(panel)
    panel._stack = QStackedWidget()
    panel._stack.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    panel._audio_page = panel._build_audio_page()
    panel._stack.addWidget(panel._audio_page)
    panel._screen_page = panel._build_screen_page()
    panel._stack.addWidget(panel._screen_page)
    panel._effect_page = _effect_page(panel)
    panel._stack.addWidget(panel._effect_page)
    for page in (panel._audio_page, panel._screen_page, panel._effect_page):
        page.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    split = QSplitter(Qt.Orientation.Horizontal, panel)
    split.setHandleWidth(4)
    split.setChildrenCollapsible(False)
    details = _scroll(panel._stack, split)
    details.setMinimumWidth(220)
    shared = _scroll(common, split)
    shared.setMinimumWidth(180)
    split.addWidget(details)
    split.addWidget(shared)
    split.setStretchFactor(0, 1)
    split.setStretchFactor(1, 0)
    split.setSizes([420, 200])
    root.addWidget(split, 1)
    panel._pixel_properties_splitter = split
    context.split, context.stack = split, panel._stack
    panel._stack.currentChanged.connect(context.configure)
    context.configure()
    panel._pixel_properties_layout_context = context
