"""
matrix_slideshow_panel.py — 矩阵 ON/OFF PPT 编辑器面板
=======================================================
面板布局（三区分割）：
┌──────────────────────────────────────────────────────────────────────┐
│  工具栏：设备组 | 通道类型 | 内置序列 | 播放控制 | 模式切换           │
├──────────────────────┬───────────────────────┬───────────────────────┤
│  幻灯片缩略图列表     │  俯视图设备点选画布    │  像素效果参数面板      │
│  (纵向滚动)          │  ● ○ ● ○ ●           │  (可滚动)             │
│  [帧1][帧2][帧3]...  │                       │                       │
│  帧操作按钮          │  全选 / 清空 / 反选    │                       │
├──────────────────────┴───────────────────────┴───────────────────────┤
│  帧参数：名称 | 停留 | 过渡 | 过渡模式 | ON值 | OFF值                 │
└──────────────────────────────────────────────────────────────────────┘

两种工作模式：
  PPT 模式    — 手动编辑 ON/OFF 帧，幻灯片播放
  像素效果模式 — 实时像素效果引擎驱动（音频FFT/屏幕采样/动态效果）
"""
from __future__ import annotations

from . import matrix_wing_flags as _wing_flags

import logging
import math
import os
import time
from typing import Dict, List, Optional, Set, Tuple
import numpy as np
from src.core.fixture_selection import ordered_members

logger = logging.getLogger(__name__)

#: 调度台用：记录本面板实例上次上报过的窗格身份键（id(panel) → set）。
#: 仅在"本面板窗格减少时删掉消失的键"时使用，避免误删其它面板/实例的窗格。

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton,
    QLabel, QSlider, QComboBox, QGroupBox, QScrollArea, QFrame,
    QSizePolicy, QSpinBox, QDoubleSpinBox, QToolButton,
    QCheckBox, QDialog, QDialogButtonBox, QLineEdit, QMessageBox,
    QTabWidget, QButtonGroup, QRadioButton, QStackedWidget, QColorDialog,
    QAbstractItemView, QFileDialog
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize, QRect, QPoint, QRectF, QPointF
from PyQt6.QtGui import (
    QColor, QPainter, QBrush, QPen, QFont, QLinearGradient,
    QRadialGradient, QPainterPath, QFontMetrics, QPixmap, QImage,
    QKeySequence, QShortcut, QIcon
)
from ..effect_icon_library import (
    icon_badge_for_text,
    make_tabler_effect_icon,
    matrix_icon_name_for_mode,
)

from .matrix_slide_engine import (
    MatrixSlide, MatrixSlideshowConfig, MatrixSlideEngine,
    TRANS_MODES, PLAY_MODES, PLAY_LOOP, PLAY_ONCE, PLAY_PINGPONG, PLAY_RANDOM,
    TRANS_CUT, TRANS_FADE, TRANS_SEQ_ON, TRANS_SEQ_OFF, TRANS_SEQ_BOTH,
    TRANS_WIPE_LR, TRANS_WIPE_RL, TRANS_WIPE_TB, TRANS_WIPE_BT,
    TRANS_WIPE_OUT, TRANS_WIPE_IN,
    CH_PUMP, CH_VALVE, CH_DIMMER,
    BUILTIN_SEQUENCES,
)
from .output_shaper import (
    CURVE_CHOICES, OUTPUT_SHAPE_DEFAULTS, OutputShaper,
    normalize_output_shape_dict, params_from_dict,
)
from .matrix_video_source_ui import (
    build_camera_row, build_split_preview_toggle, build_transport_row,
    sync_mask_widgets, sync_video_transport_widgets, sync_warp_widgets,
)
from .matrix_pixel_output_sample import refresh_paused_video_canvas
from .matrix_target_state import (base_order_for_targets, remember_target_order,
                                  save_group_scope as _save_group_scope, set_coordinate_order)
from .matrix_fixture_signature import fixture_group_signature
from .matrix_channel_controls import (
    append_definition_channels, finish_channel_rebuild, first_channel_offset,
    populate_channel_choices, restore_channel_type, restore_runtime_channel,
)
from .matrix_channel_targets import (
    encode_map_values, fixture_output, install_panel_maps, resolve_output_target,
)
from .matrix_channel_profiles import profile_map_values
from .matrix_output_scope import (
    prepare_output_scope, rebuild_output_scope, write_arrays, write_batch,
    write_frame, write_grouped, write_sparse,
)
from .matrix_channel_nodes import enrich_matrix_nodes, recall_edit_state, restore_node_authoring_scope
from .matrix_ppt_composition_controls import (
    load_composition_options, record_applied_composition,
    restore_composition_state as _restore_ppt_composition_state,
    save_composition_state as _save_ppt_composition_state,
)
from . import matrix_w2_ui   #  W2：剩余 8 项的面板接线（重逻辑在各纯逻辑模块）

# 矩阵通道下拉的常见通道中文标签（工程实际通道回退到类型名本身）。
def _matrix_project_defs(panel) -> list:
    """面板当前工程的 fixture_defs（清单真源见 `src/ui/project_channels.py`）。"""
    try:
        from ..project_channels import defs_of
        return defs_of(panel._project_for_matrix())
    except Exception:
        return []


def _reset_slot_mirrors(panel) -> None:
    """全量复位所有 direct_map 的 last_values/last_out 槽位镜像（256=未发过）。

    R194/P2-15：槽位镜像属于"某个 NM 层"的语义，层切换/整体复位时必须
    失效，否则换入层 desired == 旧层镜像 的槽位被误判不脏 → 换层后黑场。
    """
    for direct_map in (getattr(panel, "_matrix_vector_maps", {}) or {}).values():
        try:
            n = len(direct_map.get("fixture_idx", []))
            direct_map["last_values"] = np.full(n, 256, dtype=np.uint16)
            direct_map["last_out"] = np.full(n, 256, dtype=np.uint16)
        except Exception:
            logger.debug("复位矩阵槽位镜像失败（可能漏发全量输出）", exc_info=True)


def _ensure_last_out(direct_map) -> np.ndarray:
    """direct_map 槽位级 uint16 输出镜像（256=哨兵=未发过）；R194/P2-15。

    槽位与 `fixture_idx` 一一对应（每行一个 (uni,addr) 通道）。脏检查由
    逐项 `output_cache.get(uni, {}).get(addr)` Python 循环改为
    `values.astype(uint16) != last_out[...]` 一次向量比较。
    """
    last_out = direct_map.get("last_out")
    n = len(direct_map["fixture_idx"])
    if last_out is None or len(last_out) != n:
        last_out = np.full(n, 256, dtype=np.uint16)
        direct_map["last_out"] = last_out
    return last_out


def _mirror_last_out(direct_map, slots, changed_mask, values) -> None:
    """写回后统一更新 last_out；并同镜像到 `last_values`（arrays 路径的镜像）。

    单写者结论（R194/P2-15，详见测试与批次报告）：同一 direct_map 的
    (uni,addr) 槽位**并非单写者**——arrays 快路径（`_output_pixel_values_arrays`，
    自带 `last_values` 镜像）、states 逐灯回退路径、两条 vectorized 路径都可能写。
    因此这里把本路径刚发的值同时镜像进 `last_values`（槽位对齐相同），使
    arrays 路径的脏检查不至于因本路径写入而漏发（该跨路径陈旧原本只靠
    ~1s 周期的 force_full_output 兜底）；反之 arrays 路径写入只更新
    output_cache/last_values，若与本镜像短暂失真，方向是"多发"（无害），
    且 `_reset_matrix_output_state`/`_clear_matrix_output_layers` 在所有
    模式切换/停止边界同时复位两镜像与 output_cache。
    `slots=None` 表示全槽位对齐（values 无 entry_mask 过滤）。
    """
    last_out = direct_map.get("last_out")
    last_values = direct_map.get("last_values")
    picked = values[changed_mask].astype(np.uint16, copy=False)
    target_idx = changed_mask if slots is None else slots[changed_mask]
    for mirror in (last_out, last_values):
        if mirror is not None and len(mirror) == len(direct_map["fixture_idx"]):
            mirror[target_idx] = picked


_MATRIX_CH_LABELS = {
    "r": "红", "g": "绿", "b": "蓝", "w": "白",
    "motor_x": "电机 X", "motor_y": "电机 Y", "motor_z": "电机 Z",
    "motor_x_fine": "电机 X Fine", "motor_y_fine": "电机 Y Fine",
    "motor_z_fine": "电机 Z Fine",
}
from .matrix_pixel_engine import (
    MatrixPixelEngine, PIXEL_MODES,
    MODE_AUDIO_FFT, MODE_AUDIO_WAVEFORM, MODE_AUDIO_ENERGY,
    MODE_AUDIO_BAND_MATRIX, MODE_AUDIO_BAND_REVERSE, MODE_AUDIO_BAND_CENTER,
    MODE_AUDIO_BAND_DOTS, MODE_AUDIO_VU_METER, MODE_AUDIO_EQ_BARS,
    MODE_AUDIO_EQ_SOFT, MODE_AUDIO_EQ_DOTS, MODE_AUDIO_EQ_CENTER,
    MODE_AUDIO_EQ_SCAN,
    MODE_SCREEN_SAMPLE, MODE_VIDEO_SAMPLE,
    MODE_EFFECT_RIPPLE, MODE_EFFECT_CHASE, MODE_EFFECT_SPIRAL,
    MODE_EFFECT_HEARTBEAT, MODE_EFFECT_EXPLODE, MODE_EFFECT_RAIN,
    MODE_EFFECT_SNAKE, MODE_EFFECT_RANDOM, MODE_EFFECT_FIRE, MODE_EFFECT_WAVE,
)
from ...utils.wing_block import (
    WING_DIRECTION_LABELS,
    WING_DIRECTION_LOW_TO_HIGH,
    WING_DIRECTION_MODES,
    WING_DIRECTION_SYMBOLS,
    normalize_wing_direction,
)
try:
    from .matrix_pixel_engine_ext import PIXEL_MODES_EXT
    PIXEL_MODES.update(PIXEL_MODES_EXT)
except ImportError:
    pass
from .matrix_external_sources import register_external_modes
register_external_modes(PIXEL_MODES)
# ── 颜色常量（统一映射自 qascade_theme 主题令牌，本模块唯一样式来源）──
from ..output_surface_tokens import (
    OUTPUT_BG_ROOT, OUTPUT_TEXT_SECONDARY, OUTPUT_TEXT_PRIMARY,
)
from .. import qascade_theme as _th
from ..canvas_materials import selection_fill, tone
from ..qascade_theme import (          # noqa: E402
    BG_ROOT as _THEME_BG_ROOT,
    BG_EDITOR as _THEME_BG_EDITOR,
    BG_BOX as _THEME_BG_BOX,
    BG_BUTTON as _THEME_BG_BUTTON,
    BG_BUTTON_HOVER as _THEME_BG_BUTTON_HOVER,
    BG_PANEL_SUB as _THEME_BG_PANEL_SUB,
    SELECT_BLUE as _THEME_SELECT_BLUE,
    FOCUS_BLUE as _THEME_FOCUS_BLUE,
    BRIGHT_SUCCESS as _THEME_BRIGHT_SUCCESS,
    BRIGHT_ERROR as _THEME_BRIGHT_ERROR,
    TEXT_PRIMARY as _THEME_TEXT_PRIMARY,
    TEXT_SECONDARY as _THEME_TEXT_SECONDARY,
    TEXT_TERTIARY as _THEME_TEXT_TERTIARY,
)

BG_DARK   = _THEME_BG_ROOT        # #1D1D1D 根底
BG_PANEL  = _THEME_BG_EDITOR      # #303030 面板/卡片基底
BG_CARD   = _THEME_BG_BOX         # #2D2D2D 嵌套盒
BG_SEL    = _THEME_SELECT_BLUE    # #4772B3 列表/行选中填充
FG_LIGHT  = _THEME_TEXT_PRIMARY   # #E6E6E6 正文
FG_DIM    = _THEME_TEXT_SECONDARY # #999999 弱化说明
ACCENT    = _THEME_FOCUS_BLUE     # #71A8FF 描边/高亮文字
GREEN     = _THEME_BRIGHT_SUCCESS # #56B65B 播放态绿(保语义)
RED       = _THEME_BRIGHT_ERROR   # #E36855 危险红(描边/文字)

# 补齐按钮面/次级面板令牌（此前 SlideThumbnail 引用 BG_BUTTON/BG_PANEL_SUB
# 但常量未定义，paintEvent 每 4 帧抛 NameError，缩略图样式失效）。
BG_BUTTON       = _THEME_BG_BUTTON              # 标准按钮面（§1）
BG_BUTTON_HOVER = _THEME_BG_BUTTON_HOVER  # #656565 按钮 hover
BG_PANEL_SUB    = _THEME_BG_PANEL_SUB     # #363636 抬升面板次级区
FG_TER          = _THEME_TEXT_TERTIARY    # #CCCCCC 列表次级文字

# ON/OFF 颜色
COLOR_ON  = QColor("#44FF88")   # 亮绿 = ON
COLOR_OFF = QColor("#1A1A3A")   # 深蓝黑 = OFF
COLOR_SEL = QColor("#4a90d9")   # 蓝色 = 选中
COLOR_PREVIEW_ON  = QColor("#22AA55")  # 预览 ON（较暗）
COLOR_PREVIEW_OFF = QColor("#111122")  # 预览 OFF（更暗）


def _ui_font(pixel_size: int, bold: bool = False) -> QFont:
    """统一 UI 字体（Inter→Segoe UI→雅黑 链），像素字号下限 10px。"""
    f = QFont()
    f.setFamilies(["Inter", "Segoe UI", "Microsoft YaHei UI"])
    f.setPixelSize(max(10, int(pixel_size)))
    f.setBold(bold)
    return f


#: 容器柔和描边（与全局 QSS 的条目视图边框一致）
_BORDER_SOFT = "rgba(255,255,255,17)"

# widget_kit 标准件（D 波骨架组件库）
from ..widget_kit import (  # noqa: E402
    PanelSection, prop_row, NumSlider, ClickSlider, apply_dialog_chrome)
# R181 空态引导件（图标+文案+入口按钮；空态文案/按钮色令牌全在该模块）
from .matrix_empty_guide import MatrixEmptyGuide  # noqa: E402
from ..tabler_button import tabler_button  # noqa: E402


def _load_tabler(name: str, size: int, color: str) -> QIcon:
    """tabler glyph 加载（失败返回空 QIcon，由调用方回退 make_main_icon）。"""
    try:
        from ..main_icon_library import load_tabler_icon
        return load_tabler_icon(name, size, color)
    except Exception:
        return QIcon()


# ─────────────────────────────────────────────────────────────────────────────
#  俯视图设备点选画布
# ─────────────────────────────────────────────────────────────────────────────

def _matrix_pixel_mode_icon(mode_id, label, category) -> QIcon:
    identity = f"{mode_id}|{label}|{category}"
    return make_tabler_effect_icon(
        matrix_icon_name_for_mode(mode_id, label, category),
        identity,
        size=34,
        badge=icon_badge_for_text(identity),
    )


def _matrix_builtin_sequence_icon(name: str) -> QIcon:
    label = str(name or "")
    category = "内置序列"
    if "非对称" in label:
        category = "非对称运动"
    elif "粒子" in label:
        category = "粒子物理"
    elif "形状" in label or "点阵" in label:
        category = "形状变形"
    elif "节奏" in label or "闪" in label or "心跳" in label or "呼吸" in label:
        category = "节奏编排"
    elif "空间" in label:
        category = "空间点阵"
    elif "波" in label or "涟漪" in label:
        category = "波形"
    elif "跑" in label or "追" in label or "蛇" in label:
        category = "追逐"
    elif "开" in label or "关" in label:
        category = "开合"
    identity = f"builtin-sequence|{label}|{category}"
    return make_tabler_effect_icon(
        matrix_icon_name_for_mode(label, label, category),
        identity,
        size=34,
        badge=icon_badge_for_text(identity),
    )


_LAZY_ICONS = str(os.environ.get("QASCADE_MATRIX_LAZY_ICONS", "1")).strip().lower() \
    not in ("0", "false", "off", "no")


class _LazyIconComboBox(QComboBox):
    """QComboBox that builds item icons on demand instead of up front.

    The matrix panel lists ~1900 effect/sequence entries. Rendering every
    thumbnail eagerly cost ~750ms at panel construction. Here each row stores a
    zero-arg icon factory; icons are rendered only when the popup opens and the
    row is near the visible range, then cached on the item. Gated by
    QASCADE_MATRIX_LAZY_ICONS (default on).
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._icon_factories: Dict[int, object] = {}
        self._scroll_hooked: bool = False

    def add_lazy_item(self, factory, text: str, data=None) -> None:
        """Add a row whose icon is produced by ``factory()`` on first display."""
        index = self.count()
        if data is None:
            self.addItem(text)
        else:
            self.addItem(text, data)
        if factory is not None:
            self._icon_factories[index] = factory

    def _render_range(self, first: int, last: int) -> None:
        first = max(0, first)
        last = min(self.count() - 1, last)
        for index in range(first, last + 1):
            factory = self._icon_factories.pop(index, None)
            if factory is None:
                continue
            try:
                self.setItemIcon(index, factory())
            except Exception:
                logger.debug("lazy matrix icon render failed", exc_info=True)

    def showPopup(self) -> None:
        # Render the first screenful before showing, then wire the scrollbar so
        # further rows fill in as the user scrolls.
        visible = max(1, int(self.maxVisibleItems() or 1))
        current = max(0, self.currentIndex())
        self._render_range(0, visible)
        self._render_range(current - 2, current + visible)
        view = self.view()
        bar = view.verticalScrollBar() if view is not None else None
        if bar is not None and not self._scroll_hooked:
            bar.valueChanged.connect(self._on_scroll)
            self._scroll_hooked = True
        super().showPopup()
        self._on_scroll()

    def _on_scroll(self, *_args) -> None:
        if not self._icon_factories:
            return
        view = self.view()
        if view is None:
            return
        first = view.indexAt(view.rect().topLeft())
        last = view.indexAt(view.rect().bottomLeft())
        first_row = first.row() if first.isValid() else 0
        last_row = last.row() if last.isValid() else (first_row + int(self.maxVisibleItems() or 1))
        self._render_range(first_row - 2, last_row + 4)


class FixtureCanvas(QWidget):
    """
    俯视图设备点选画布
    - 显示设备按 TopView 相对位置排布
    - 左键点击切换 ON/OFF
    - 右键框选
    - 实时预览当前帧的 ON/OFF 状态
    - 支持像素效果模式的实时预览
    """

    selection_changed = pyqtSignal()  # ON/OFF 状态变化

    # 设备节点尺寸
    NODE_R = 14   # 节点半径（像素）
    MARGIN = 30   # 画布边距

    def __init__(self, parent=None):
        super().__init__(parent)
        # 画布本体保留足够可视面积；外层面板用 QScrollArea 控制高度，
        # 避免 TopView 设备很多时把整个矩阵页面撑得过长。
        self.setMinimumSize(520, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)

        self._fixtures: List[Dict] = []          # 设备列表（含归一化坐标）
        self._fixture_ids: List[str] = []
        self._fixture_points: List[Tuple[Dict, QPoint]] = []
        #  灰度帧：值域 0-255（bool True/False 亦合法，写入时规整为 int）
        self._states: Dict[str, int] = {}         # {fid: 0-255}
        self._brush_level: int = 255              # 画笔力度（点选/框选写入的灰度）
        self._pixel_weights: Dict[str, float] = {}  # 像素效果权重
        self._pixel_values: Optional[np.ndarray] = None
        self._pixel_mode: bool = False            # 是否在像素效果模式
        self._threshold: float = 0.5
        self._layout_width_scale: float = 1.0
        self._layout_height_scale: float = 1.0
        self._preview_zoom: float = 1.0
        self._large_draw_threshold: int = 360
        self._point_radius: int = self.NODE_R

        self._hovered: Optional[str] = None       # 悬停设备 ID
        self._drag_start: Optional[QPoint] = None
        self._drag_rect: Optional[QRect] = None
        self._drag_set_on: bool = True            # 框选时设置为 ON 还是 OFF
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.timeout.connect(self.update)
        self._last_pixel_signature = None

        self.setStyleSheet(f"background:{OUTPUT_BG_ROOT};border:1px solid #3D3D3D;border-radius:4px;")

    def _request_repaint(self, immediate: bool = False):
        if immediate:
            self._repaint_timer.stop()
            self.update()
            return
        if not self._repaint_timer.isActive():
            self._repaint_timer.start(66)

    def set_fixtures(self, fixtures: List[Dict]):
        self._fixtures = list(fixtures)
        self._fixture_ids = [str(f.get("id", "")) for f in self._fixtures]
        self._point_radius = self._node_radius_for_count(len(self._fixtures))
        self._rebuild_fixture_points()
        # 初始化所有设备为 OFF
        self._states = {f["id"]: False for f in fixtures if "id" in f}
        self._pixel_values = None
        self._last_pixel_signature = None
        self._request_repaint(True)

    def set_layout_width_scale(self, scale: float):
        try:
            scale = float(scale)
        except Exception:
            scale = 1.0
        scale = max(0.25, min(6.0, scale))
        if abs(scale - self._layout_width_scale) < 1e-6:
            return
        self._layout_width_scale = scale
        self._rebuild_fixture_points()
        self._request_repaint(True)

    def set_layout_height_scale(self, scale: float):
        try:
            scale = float(scale)
        except Exception:
            scale = 1.0
        scale = max(0.25, min(6.0, scale))
        if abs(scale - self._layout_height_scale) < 1e-6:
            return
        self._layout_height_scale = scale
        self._rebuild_fixture_points()
        self._request_repaint(True)

    def _rebuild_fixture_points(self):
        self._fixture_points = [(f, self._fixture_pos(f)) for f in self._fixtures]

    def set_preview_zoom(self, zoom: float):
        try:
            zoom = float(zoom)
        except Exception:
            zoom = 1.0
        zoom = max(0.35, min(2.5, zoom))
        if abs(zoom - self._preview_zoom) < 1e-6:
            return
        self._preview_zoom = zoom
        base = self._node_radius_for_count(len(self._fixtures))
        self._point_radius = max(1, int(round(base * zoom)))
        self._rebuild_fixture_points()
        self._request_repaint(True)

    @staticmethod
    def _node_radius_for_count(count: int) -> int:
        if count >= 1200:
            return 2
        if count >= 540:
            return 3
        if count >= 240:
            return 4
        return FixtureCanvas.NODE_R

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rebuild_fixture_points()

    def set_states(self, states: Dict[str, bool]):
        """设置帧状态（ 灰度帧：bool/int 均规整为 int 0-255 显示）"""
        normalized = {}
        for fid, on in (states or {}).items():
            if isinstance(on, (bool, np.bool_)):
                normalized[str(fid)] = 255 if on else 0
            else:
                try:
                    normalized[str(fid)] = max(0, min(255, int(round(float(on)))))
                except (TypeError, ValueError):
                    normalized[str(fid)] = 0
        if (not self._pixel_mode and normalized == self._states and
                not self._pixel_weights and self._pixel_values is None):
            return
        self._states = normalized
        self._pixel_mode = False
        self._pixel_weights = {}
        self._pixel_values = None
        self._last_pixel_signature = None
        self._request_repaint()

    def get_states(self) -> Dict[str, int]:
        """当前画布状态 {fid: 0-255}（灰度帧语义）。"""
        return dict(self._states)

    def set_brush_level(self, level: int):
        """设置画笔力度（点选/框选写入的灰度 1-255）。"""
        try:
            level = int(level)
        except (TypeError, ValueError):
            level = 255
        self._brush_level = max(1, min(255, level))

    def brush_level(self) -> int:
        return int(self._brush_level)

    def set_all_on(self):
        for fid in self._states:
            self._states[fid] = 255
        self.selection_changed.emit()
        self.update()

    def set_all_off(self):
        for fid in self._states:
            self._states[fid] = 0
        self.selection_changed.emit()
        self.update()

    def invert_all(self):
        for fid in self._states:
            self._states[fid] = 0 if self._states[fid] else 255
        self.selection_changed.emit()
        self.update()

    def set_pixel_weights(self, weights: Dict[str, float], threshold: float = 0.5):
        """设置像素效果权重（用于实时预览）"""
        weights = weights or {}
        if len(weights) >= self._large_draw_threshold and self._fixture_ids:
            vals = np.zeros(len(self._fixture_ids), dtype=np.uint8)
            order = {fid: i for i, fid in enumerate(self._fixture_ids)}
            for fid, val in weights.items():
                idx = order.get(str(fid))
                if idx is not None:
                    vals[idx] = int(max(0.0, min(1.0, float(val))) * 255.0)
            self.set_pixel_values(vals, threshold)
            return
        signature = (
            round(float(threshold), 3),
            tuple(
                (str(fid), int(max(0.0, min(1.0, float(val))) * 255.0))
                for fid, val in weights.items()
            ),
        )
        if self._pixel_mode and signature == self._last_pixel_signature:
            return
        self._last_pixel_signature = signature
        self._pixel_weights = dict(weights)
        self._pixel_values = None
        self._threshold = threshold
        self._pixel_mode = True
        self._request_repaint()

    def set_pixel_values(self, values: np.ndarray, threshold: float = 0.5):
        vals = np.asarray(values, dtype=np.uint8).reshape(-1)
        n = len(self._fixture_ids)
        if n <= 0:
            return
        if vals.size < n:
            padded = np.zeros(n, dtype=np.uint8)
            padded[:vals.size] = vals
            vals = padded
        else:
            vals = vals[:n]
        threshold = float(threshold)
        if (self._pixel_mode and self._pixel_values is not None and
                abs(float(self._threshold) - threshold) < 1e-6 and
                np.array_equal(self._pixel_values, vals)):
            return
        self._pixel_values = vals.copy()
        self._pixel_weights = {}
        self._threshold = threshold
        self._pixel_mode = True
        self._last_pixel_signature = None
        self._request_repaint()

    def clear_pixel_mode(self):
        if not self._pixel_mode and not self._pixel_weights:
            return
        self._pixel_mode = False
        self._pixel_weights = {}
        self._pixel_values = None
        self._last_pixel_signature = None
        self._request_repaint(True)

    # ── 坐标映射 ──────────────────────────────────────────────────────────────

    def _fixture_pos(self, f: Dict) -> QPoint:
        """将设备归一化坐标映射到画布像素坐标"""
        w = self.width() - self.MARGIN * 2
        h = self.height() - self.MARGIN * 2
        nx = f.get("pos_x", 0.5)
        ny = f.get("pos_y", 0.5)
        x = int(self.MARGIN + float(nx) * w)
        y = int(self.MARGIN + float(ny) * h)
        return QPoint(x, y)

    def _fixture_at(self, pos: QPoint) -> Optional[str]:
        """返回鼠标位置下的设备 ID"""
        hit_r = max(int(getattr(self, "_point_radius", self.NODE_R)) + 4, 5)
        for f, fp in self._fixture_points:
            if (pos - fp).manhattanLength() <= hit_r:
                return f.get("id")
        return None

    # ── 绘制 ──────────────────────────────────────────────────────────────────

    def _node_visual(self, fid, idx, values, threshold_value):
        """单设备渲染量 (on, intensity 0..1)。

        像素值数组优先 → 像素权重 → 帧状态（ 灰度：非 0 即亮，亮度按值）。
        """
        if values is not None and idx < len(values):
            raw = int(values[idx])
            return raw >= threshold_value, raw / 255.0
        if self._pixel_mode and fid in self._pixel_weights:
            w = self._pixel_weights[fid]
            return w >= self._threshold, w
        raw = self._states.get(fid, 0) or 0
        return raw > 0, max(0.0, min(1.0, float(raw) / 255.0))

    def paintEvent(self, event):
        p = QPainter(self)
        fixture_count = len(self._fixture_points)
        lightweight = fixture_count >= self._large_draw_threshold
        p.setRenderHint(QPainter.RenderHint.Antialiasing, not lightweight)

        # 背景
        p.fillRect(self.rect(), tone(_th.CANVAS_OUTPUT_BG))

        if not self._fixtures:
            p.setPen(tone(_th.CANVAS_OUTPUT_TEXT))
            p.setFont(_ui_font(11))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "请先选择设备组\n(从设备组面板选择泵/阀设备组)")
            return

        # 绘制连接线（大矩阵禁用，避免预览本身拖慢实时输出）
        if not lightweight:
            p.setPen(QPen(tone(_th.CANVAS_OUTPUT_IDLE_BORDER, 55), 1, Qt.PenStyle.DotLine))
            for _f, fp in self._fixture_points:
                p.drawLine(fp.x(), self.MARGIN, fp.x(), self.height() - self.MARGIN)
                p.drawLine(self.MARGIN, fp.y(), self.width() - self.MARGIN, fp.y())

        # 绘制设备节点
        font = _ui_font(10)
        p.setFont(font)
        node_r = int(getattr(self, "_point_radius", self.NODE_R) or self.NODE_R)
        if lightweight:
            node_r = self._node_radius_for_count(fixture_count)
        values = self._pixel_values if self._pixel_mode else None
        threshold_value = int(max(0.0, min(1.0, float(self._threshold))) * 255.0)

        for idx, (f, fp) in enumerate(self._fixture_points):
            fid = f.get("id", "")
            name = f.get("name", fid)[:6]

            # 确定颜色（helper：像素值 → 像素权重 → 帧状态）
            on, intensity = self._node_visual(fid, idx, values, threshold_value)

            if on:
                # ON：亮绿色，有光晕
                if not lightweight:
                    glow = QRadialGradient(fp.x(), fp.y(), node_r * 2)
                    glow.setColorAt(0, QColor(68, 255, 136, 180))
                    glow.setColorAt(1, QColor(68, 255, 136, 0))
                    p.setBrush(QBrush(glow))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawEllipse(fp, node_r * 2, node_r * 2)

                # 节点本体
                node_color = QColor(
                    int(34 + intensity * 34),
                    int(200 + intensity * 55),
                    int(80 + intensity * 56)
                )
                p.setBrush(QBrush(node_color))
                p.setPen(QPen(QColor("#88FFAA"), 1 if lightweight else 2))
            else:
                # OFF：深色
                p.setBrush(QBrush(tone(_th.CANVAS_OUTPUT_IDLE)))
                p.setPen(QPen(tone(_th.CANVAS_OUTPUT_IDLE_BORDER), 1))

            # 悬停高亮
            if fid == self._hovered:
                p.setPen(QPen(tone(_th.BRAND_ORANGE), 2))

            p.drawEllipse(fp, node_r, node_r)

            # 设备名称
            if not lightweight:
                p.setPen(tone(_th.CANVAS_OUTPUT_TEXT))
                p.drawText(QRect(fp.x() - 20, fp.y() + node_r + 2, 40, 12),
                           Qt.AlignmentFlag.AlignCenter, name)

        # 框选矩形
        if self._drag_rect is not None:
            p.setPen(QPen(tone(_th.BRAND_ORANGE), 1, Qt.PenStyle.DashLine))
            p.setBrush(QBrush(selection_fill(40)))
            p.drawRect(self._drag_rect)

        # 统计信息
        if values is not None:
            on_count = int(np.count_nonzero(values[:fixture_count] >= threshold_value))
        else:
            on_count = sum(1 for v in self._states.values() if v)
        total = max(len(self._states), fixture_count)
        p.setPen(tone(_th.CANVAS_OUTPUT_TEXT))
        p.setFont(_ui_font(10))
        p.drawText(QRect(4, 4, 200, 16), Qt.AlignmentFlag.AlignLeft,
                   f"ON: {on_count} / {total}")

    # ── 鼠标事件 ──────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if self._pixel_mode:
            return
        pos = event.pos()
        fid = self._fixture_at(pos)
        if fid is not None:
            # 点击切换单个设备（ 灰度：写画笔力度而非恒 255）
            self._states[fid] = 0 if self._states.get(fid, 0) else self._brush_level
            self.selection_changed.emit()
            self.update()
        else:
            # 开始框选
            self._drag_start = pos
            self._drag_rect = QRect(pos, pos)
            # 右键框选设为 OFF，左键框选设为 ON（灰度=画笔力度）
            self._drag_set_on = event.button() != Qt.MouseButton.RightButton

    def mouseMoveEvent(self, event):
        pos = event.pos()
        hid = self._fixture_at(pos)
        if self._drag_start is not None:
            rect = QRect(self._drag_start, pos).normalized()
            if rect != self._drag_rect: self._drag_rect = rect; self.update()  #  R193/P2-14：选区未变不重绘
        elif hid != self._hovered:
            self._hovered = hid; self.update()  #  R193/P2-14：hover 未变不重绘

    def mouseReleaseEvent(self, event):
        if self._drag_rect is not None and self._drag_start is not None:
            # 框选完成：将框内设备设置为对应状态（ 灰度=画笔力度）
            changed = False
            drag_val = self._brush_level if self._drag_set_on else 0
            for f, fp in self._fixture_points:
                fid = f.get("id", "")
                if self._drag_rect.contains(fp):
                    self._states[fid] = drag_val
                    changed = True
            if changed:
                self.selection_changed.emit()
            self._drag_start = None
            self._drag_rect = None
            self.update()

    def leaveEvent(self, event):
        self._hovered = None
        self.update()


# ─────────────────────────────────────────────────────────────────────────────
#  幻灯片缩略图
# ─────────────────────────────────────────────────────────────────────────────

class SlideThumbnail(QWidget):
    """单张幻灯片缩略图"""

    THUMB_W = 90
    THUMB_H = 70
    NODE_R  = 3

    clicked = pyqtSignal(int)
    double_clicked = pyqtSignal(int)

    def __init__(self, idx: int, slide: MatrixSlide,
                 fixtures: List[Dict], parent=None):
        super().__init__(parent)
        self.setFixedSize(self.THUMB_W + 4, self.THUMB_H + 28)
        self._idx = idx
        self._slide = slide
        self._fixtures = self._sample_fixtures(fixtures)
        self._selected = False
        self._playing = False
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    @staticmethod
    def _sample_fixtures(fixtures: List[Dict], limit: int = 120) -> List[Dict]:
        fixtures = list(fixtures or [])
        n = len(fixtures)
        if n <= limit:
            return fixtures
        step = (n - 1) / float(max(1, limit - 1))
        return [fixtures[min(n - 1, int(round(i * step)))] for i in range(limit)]

    @staticmethod
    def _lerp_color(a: QColor, b: QColor, t: float) -> QColor:
        """两色线性插值（t 钳到 0..1；灰度帧缩略图用）。"""
        t = max(0.0, min(1.0, float(t)))
        return QColor(
            int(a.red() + (b.red() - a.red()) * t),
            int(a.green() + (b.green() - a.green()) * t),
            int(a.blue() + (b.blue() - a.blue()) * t),
        )

    def _paint_node(self, p, x: int, y: int, fid: str) -> None:
        """单设备节点（ 灰度帧按 state_level 在 OFF→ON 色间插值）。"""
        level = self._slide.state_level(fid)
        if level > 0.0:
            p.setBrush(QBrush(self._lerp_color(COLOR_OFF, COLOR_ON, level)))
            p.setPen(QPen(QColor("#66FFAA"), 1))
        else:
            p.setBrush(QBrush(COLOR_OFF))
            p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPoint(x + 2, y + 2), self.NODE_R, self.NODE_R)

    def set_selected(self, v: bool):
        self._selected = v
        self.update()

    def set_playing(self, v: bool):
        #  R193/P1-7（MX-01）：值不变早退——缩略图条每 tick 全量调本方法，
        # 无守卫时 N 个缩略图 25fps 全量重绘（空闲也在烧）。
        if v == self._playing:
            return
        self._playing = v
        self.update()

    def update_slide(self, slide: MatrixSlide):
        self._slide = slide
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 扁平卡片：BG_CARD 底 + 4px 圆角 + 1px 边（hover 提亮，选中 select_blue）
        if self._selected:
            border_color = QColor(_th.BRAND_ORANGE_INK)
            fill = QColor(_th.BRAND_ORANGE_SELECTED)
        elif self._playing:
            border_color = QColor(GREEN)
            fill = QColor(BG_CARD)
        else:
            border_color = QColor(_th.BRAND_ORANGE_INK if self._hover else _th.BORDER_NORMAL)
            fill = QColor(BG_PANEL_SUB) if self._hover else QColor(BG_CARD)
        p.setPen(QPen(border_color, 2 if (self._selected or self._playing)
                      else (1 if self._hover else 1)))
        p.setBrush(QBrush(fill))
        p.drawRoundedRect(2, 2, self.THUMB_W, self.THUMB_H, 4, 4)

        # 绘制设备节点
        if self._fixtures:
            inner_w = self.THUMB_W - 12
            inner_h = self.THUMB_H - 12
            for f in self._fixtures:
                fid = f.get("id", "")
                nx = f.get("pos_x", 0.5)
                ny = f.get("pos_y", 0.5)
                x = int(8 + nx * inner_w)
                y = int(8 + ny * inner_h)
                self._paint_node(p, x, y, fid)

        # 播放指示器（自绘三角承担播放态语义 → 保留形状仅精修线条）
        if self._playing:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor(GREEN)))
            tri = QPainterPath()
            tri.moveTo(6, 6)
            tri.lineTo(14, 10)
            tri.lineTo(6, 14)
            tri.closeSubpath()
            p.drawPath(tri)

        # 底部标签（选中=select_blue 上白字；名称 11px / 统计 10px）
        if self._selected:
            p.fillRect(2, self.THUMB_H + 4, self.THUMB_W, 20, QColor(_th.BRAND_ORANGE_SELECTED))
        name_fg = (QColor(_th.TEXT_PRIMARY) if self._selected
                   else QColor(FG_LIGHT if self._hover else FG_DIM))
        p.setPen(name_fg)
        p.setFont(_ui_font(11))
        name = self._slide.name or f"帧 {self._idx + 1}"
        on_cnt = self._slide.on_count()
        label = f"{self._idx + 1}. {name[:8]}"
        p.drawText(QRect(2, self.THUMB_H + 4, self.THUMB_W, 12),
                   Qt.AlignmentFlag.AlignCenter, label)
        p.setPen(QColor(GREEN if on_cnt > 0 else FG_DIM))
        p.setFont(_ui_font(10))
        p.drawText(QRect(2, self.THUMB_H + 16, self.THUMB_W, 12),
                   Qt.AlignmentFlag.AlignCenter,
                   f"ON:{on_cnt}  {self._slide.hold_time:.1f}s")

    def mousePressEvent(self, event):
        self.clicked.emit(self._idx)

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit(self._idx)


# ─────────────────────────────────────────────────────────────────────────────
#  屏幕采样实时预览画布
# ─────────────────────────────────────────────────────────────────────────────

class _ScreenPreviewCanvas(QWidget):
    """
    屏幕采样实时预览画布。
    全幅显示网格亮度热力图，每个格子显示数值。
    支持实时显示 ON/OFF 状态叠加层。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("background:#1D1D1D;border:1px solid #3D3D3D;border-radius:3px;")
        self._grid: Optional[np.ndarray] = None   # (rows, cols) float32
        self._raw: Optional[np.ndarray] = None    # (H, W, 3) uint8 RGB，视频预览用
        self._on_off: Optional[Dict[str, bool]] = None  # ON/OFF 状态
        self._threshold: float = 0.5
        self._split_mode: bool = False            #  #15：并排预览开关
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.timeout.connect(self.update)
        self._last_on_off_signature = None

    def _request_repaint(self, immediate: bool = False):
        if immediate:
            self._repaint_timer.stop()
            self.update()
            return
        if not self._repaint_timer.isActive():
            self._repaint_timer.start(83)

    def set_data(self, raw: Optional[np.ndarray], grid: Optional[np.ndarray]):
        """设置原始 RGB 预览帧与矩阵亮度热力图。"""
        same_raw = raw is self._raw
        same_grid = (
            (grid is self._grid) or
            (grid is not None and self._grid is not None and
             grid.shape == self._grid.shape and np.array_equal(grid, self._grid))
        )
        if same_raw and same_grid:
            return
        self._raw = raw
        self._grid = grid
        self._request_repaint()

    def set_on_off(self, states: Optional[Dict], threshold: float = 0.5):
        """(可选) 设置 ON/OFF 状态用于叠加显示。"""
        signature = None
        if isinstance(states, dict):
            signature = (
                round(float(threshold), 3),
                tuple(sorted((str(fid), bool(on)) for fid, on in states.items())),
            )
        if signature == self._last_on_off_signature:
            return
        self._last_on_off_signature = signature
        self._on_off = states
        self._threshold = threshold
        self._request_repaint()

    def set_split_mode(self, on: bool):
        """ #15 P2：并排预览（左半源画面 / 右半网格采样）。"""
        on = bool(on)
        if on == self._split_mode:
            return
        self._split_mode = on
        self._request_repaint(True)

    def split_mode(self) -> bool:
        return bool(self._split_mode)

    @staticmethod
    def _val_to_color(val: float) -> QColor:
        """热力图颜色映射：暗蓝→蓝→青→绿→黄→橙→红"""
        val = max(0.0, min(1.0, val))
        if val < 0.2:
            t = val / 0.2
            return QColor(0, 0, int(40 + t * 140))
        elif val < 0.4:
            t = (val - 0.2) / 0.2
            return QColor(0, int(t * 160), int(180 - t * 40))
        elif val < 0.6:
            t = (val - 0.4) / 0.2
            return QColor(int(t * 80), int(160 + t * 60), int(140 - t * 140))
        elif val < 0.8:
            t = (val - 0.6) / 0.2
            return QColor(int(80 + t * 175), int(220 - t * 100), 0)
        else:
            t = (val - 0.8) / 0.2
            return QColor(255, int(120 - t * 120), 0)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        W, H = self.width(), self.height()
        p.fillRect(0, 0, W, H, QColor(OUTPUT_BG_ROOT))

        if self._grid is not None and self._grid.size > 0:
            if self._split_mode:
                self._paint_split(p, W, H)
            else:
                self._paint_stacked(p, W, H)
        else:
            p.setPen(QColor(OUTPUT_TEXT_SECONDARY))
            p.drawText(QRect(0, 0, W, H),
                       Qt.AlignmentFlag.AlignCenter,
                       "屏幕采样未启动\n请先选择效果并点击播放")
        p.end()

    def _paint_stacked(self, p, W: int, H: int) -> None:
        """旧布局：顶部源帧 + 下方亮度网格（逐行搬移，布局数值不变）。"""
        raw_h = self._paint_raw_top(p, W, H)
        self._paint_cells(p, x0=0, y0=raw_h, w=W, h=H - raw_h)

    def _paint_split(self, p, W: int, H: int) -> None:
        """并排布局：左半源画面 / 右半网格采样（ #15 P2）。"""
        half = max(1, W // 2)
        self._paint_raw_into(p, QRect(2, 2, max(0, half - 4), max(0, H - 4)))
        self._paint_cells(p, x0=half + 2, y0=2,
                          w=max(0, W - half - 4), h=max(0, H - 4))

    def _paint_raw_top(self, p, W: int, H: int) -> int:
        """顶部源帧预览；返回占用高度（0=未绘制）。"""
        if self._raw is None or getattr(self._raw, "size", 0) == 0:
            return 0
        try:
            raw = np.ascontiguousarray(self._raw)
            h0, w0 = raw.shape[:2]
            qimg = QImage(raw.data, w0, h0, raw.strides[0], QImage.Format.Format_RGB888)
            target_h = max(34, min(int(H * 0.42), H - 54))
            target = QRect(2, 2, W - 4, target_h)
            p.drawImage(target, qimg, QRect(0, 0, w0, h0))
            return target_h + 4
        except Exception:
            return 0

    def _paint_raw_into(self, p, rect: QRect) -> None:
        """把源帧缩放绘制进指定矩形（并排预览用）。"""
        if self._raw is None or getattr(self._raw, "size", 0) == 0:
            return
        try:
            raw = np.ascontiguousarray(self._raw)
            h0, w0 = raw.shape[:2]
            qimg = QImage(raw.data, w0, h0, raw.strides[0], QImage.Format.Format_RGB888)
            p.drawImage(rect, qimg, QRect(0, 0, w0, h0))
        except Exception:
            pass

    def _ordered_states(self):
        """ON/OFF 覆盖状态按值序列表（无覆盖/异常 → None）。"""
        if not isinstance(self._on_off, dict):
            return None
        try:
            return list(self._on_off.values())
        except Exception:
            return None

    def _paint_cells(self, p, *, x0: int, y0: int, w: int, h: int) -> None:
        """网格单元 + 底部信息行（helper；旧布局数值逐位保持）。"""
        grid = self._grid
        rows, cols = grid.shape
        cell_w = w / max(1, cols)
        cell_h = max(1.0, (h - 16) / max(1, rows))  # 留底部标题行
        p.setFont(_ui_font(10))
        ordered = self._ordered_states()
        ctx = (x0, y0, cell_w, cell_h)
        for r in range(rows):
            for c in range(cols):
                self._paint_cell(p=p, grid=grid, r=r, c=c, ordered=ordered, ctx=ctx)
        self._paint_grid_info(p, x0=x0, y0=y0, w=w, h=h,
                              rows=rows, cols=cols, ordered=ordered)

    def _paint_cell(self, *, p, grid, r: int, c: int, ordered, ctx) -> None:
        """单个网格单元（helper 压 CC；绘制语义与旧实现逐位一致）。"""
        val, override_on = self._cell_values(grid, r, c, ordered)
        x0, y0, cell_w, cell_h = ctx
        x = int(x0 + c * cell_w)
        y = int(y0 + r * cell_h)
        cw = max(1, int(cell_w) - 1)
        ch = max(1, int(cell_h) - 1)
        if override_on is not None:
            p.fillRect(x, y, cw, ch, QColor("#4a6a3a" if override_on else "#1D1D1D"))
        else:
            p.fillRect(x, y, cw, ch, self._val_to_color(val))
        is_on = override_on if override_on is not None else (val >= self._threshold)
        show_text = cell_w > 24 and cell_h > 14
        self._paint_cell_deco(p, QRect(x, y, cw, ch), is_on, override_on,
                              text=f"{val:.2f}", show_text=show_text)

    @staticmethod
    def _cell_values(grid, r: int, c: int, ordered):
        """单格 (val, override_on)（helper）。"""
        val = float(grid[r, c])
        if ordered is None:
            return val, None
        cell_idx = r * grid.shape[1] + c
        if cell_idx < len(ordered):
            return val, bool(ordered[cell_idx])
        return val, None

    @staticmethod
    def _paint_cell_deco(p, rect: QRect, is_on, override_on, *, text: str,
                         show_text: bool) -> None:
        """单元边框指示 + 数值文本（helper 压 CC）。"""
        if is_on:
            p.setPen(QPen(QColor("#44FF88"), 1))
            p.drawRect(rect.x(), rect.y(), rect.width() - 1, rect.height() - 1)
        elif override_on is not None:
            p.setPen(QPen(QColor("#3D3D3D"), 1))
            p.drawRect(rect.x(), rect.y(), rect.width() - 1, rect.height() - 1)
        if show_text:
            p.setPen(QColor("#FFFFFF" if is_on else "#8B8B8B"))
            if override_on is not None:
                label = "ON" if is_on else "OFF"
            else:
                label = text
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

    def _paint_grid_info(self, p, *, x0: int, y0: int, w: int, h: int,
                         rows: int, cols: int, ordered) -> None:
        """底部信息行（helper）。"""
        if ordered is not None:
            on_count = sum(1 for v in ordered[:rows * cols] if bool(v))
        else:
            on_count = int(np.sum(self._grid >= self._threshold))
        total = rows * cols
        p.setPen(QColor("#8B8B8B"))
        p.setFont(_ui_font(10))
        p.drawText(QRect(x0 + 2, y0 + h - 14, w - 4, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"亮度热力图  {cols}×{rows}  ON: {on_count}/{total}  "
                   f"阈值: {self._threshold:.2f}")


# ─────────────────────────────────────────────────────────────────────────────
#  像素效果参数面板
# ─────────────────────────────────────────────────────────────────────────────

class PixelEffectParamPanel(QWidget):
    """像素效果参数控制面板"""

    params_changed = pyqtSignal()
    color_changed = pyqtSignal()
    video_cleared = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._engine: Optional[MatrixPixelEngine] = None
        self._last_preview_stats_text: str = ""
        self._slider_value_mappers: Dict[int, tuple] = {}
        self._build_ui()

    def _make_slider_spin(self, layout: QGridLayout, row: int, label: str,
                          attr: str, minimum: float, maximum: float,
                          default: float, step: float, decimals: int = 2,
                          scale: int = 100, suffix: str = ""):
        _ = label
        slider = ClickSlider(Qt.Orientation.Horizontal)
        slider.setRange(int(round(minimum * scale)), int(round(maximum * scale)))
        slider.setValue(int(round(default * scale)))
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setSingleStep(step)
        spin.setValue(default)
        if suffix:
            spin.setSuffix(suffix)
        spin.setFixedWidth(74)

        def _from_slider(v, _spin=spin, _attr=attr, _scale=scale):
            value = v / float(_scale)
            _spin.blockSignals(True)
            _spin.setValue(value)
            _spin.blockSignals(False)
            self._apply_numeric_param(_attr, value)

        def _from_spin(value, _slider=slider, _attr=attr, _scale=scale):
            _slider.blockSignals(True)
            _slider.setValue(int(round(value * _scale)))
            _slider.blockSignals(False)
            self._apply_numeric_param(_attr, float(value))

        slider.valueChanged.connect(_from_slider)
        spin.valueChanged.connect(_from_spin)
        layout.addWidget(slider, row, 1)
        layout.addWidget(spin, row, 2)
        return slider, spin

    def _make_fine_low_slider_spin(self, layout: QGridLayout, row: int, label: str,
                                   attr: str, minimum: float, maximum: float,
                                   default: float, step: float, decimals: int = 3,
                                   scale: int = 100, suffix: str = "", low_bias: float = 2.4):
        _ = label
        minimum = float(minimum)
        maximum = float(maximum)
        low_bias = max(1.05, float(low_bias or 2.4))

        def value_to_slider(value: float) -> int:
            v = max(minimum, min(maximum, float(value)))
            if maximum <= minimum:
                return 0
            if minimum > 0:
                ratio = math.log(v / minimum) / math.log(maximum / minimum)
                return int(round(max(0.0, min(1.0, ratio)) * 1000.0))
            ratio = (v - minimum) / (maximum - minimum)
            ratio = max(0.0, min(1.0, ratio))
            return int(round((ratio ** (1.0 / low_bias)) * 1000.0))

        def slider_to_value(pos: int) -> float:
            ratio = max(0.0, min(1.0, float(pos) / 1000.0))
            if maximum <= minimum:
                return minimum
            if minimum > 0:
                return minimum * ((maximum / minimum) ** ratio)
            return minimum + (maximum - minimum) * (ratio ** low_bias)

        slider = ClickSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 1000)
        slider.setValue(value_to_slider(default))
        slider.setToolTip("滑块使用低值精调曲线；数值框仍可输入完整范围。")
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setSingleStep(step)
        spin.setValue(default)
        if suffix:
            spin.setSuffix(suffix)
        spin.setFixedWidth(74)

        self._slider_value_mappers[id(slider)] = (value_to_slider, slider_to_value)

        def _from_slider(v):
            value = slider_to_value(int(v))
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
            self._apply_numeric_param(attr, value)

        def _from_spin(value):
            slider.blockSignals(True)
            slider.setValue(value_to_slider(float(value)))
            slider.blockSignals(False)
            self._apply_numeric_param(attr, float(value))

        slider.valueChanged.connect(_from_slider)
        spin.valueChanged.connect(_from_spin)
        layout.addWidget(slider, row, 1)
        layout.addWidget(spin, row, 2)
        return slider, spin

    def _speed_to_slider(self, value: float) -> int:
        try:
            value = float(value)
        except Exception:
            value = 1.0
        value = max(0.0, min(16.0, value))
        if value <= 1.0:
            return int(round(value * 700.0))
        if value <= 4.0:
            return 700 + int(round((value - 1.0) / 3.0 * 200.0))
        return 900 + int(round((value - 4.0) / 12.0 * 100.0))

    def _slider_to_speed(self, pos: int) -> float:
        pos = max(0, min(1000, int(pos)))
        if pos <= 700:
            return pos / 700.0
        if pos <= 900:
            return 1.0 + (pos - 700) / 200.0 * 3.0
        return 4.0 + (pos - 900) / 100.0 * 12.0

    def _make_speed_slider_spin(self, layout: QGridLayout, row: int):
        slider = ClickSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 1000)
        slider.setValue(self._speed_to_slider(1.0))
        slider.setToolTip("滑块前 70% 对应 0-1x，适合常用低速精调；数值框仍可输入 0-16x。")

        spin = QDoubleSpinBox()
        spin.setRange(0.0, 16.0)
        spin.setDecimals(3)
        spin.setSingleStep(0.005)
        spin.setValue(1.0)
        spin.setSuffix("x")
        spin.setFixedWidth(74)
        spin.setToolTip("效果速度倍率；低速可直接输入 0.005、0.02、0.1 等精确值。")

        def _from_slider(v):
            value = self._slider_to_speed(v)
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
            self._apply_numeric_param("speed", value)

        def _from_spin(value):
            slider.blockSignals(True)
            slider.setValue(self._speed_to_slider(float(value)))
            slider.blockSignals(False)
            self._apply_numeric_param("speed", float(value))

        slider.valueChanged.connect(_from_slider)
        spin.valueChanged.connect(_from_spin)
        layout.addWidget(slider, row, 1)
        layout.addWidget(spin, row, 2)
        return slider, spin

    def _set_slider_spin_value(self, slider: QSlider, spin: QDoubleSpinBox,
                               value: float, scale: int = 100):
        slider.blockSignals(True)
        spin.blockSignals(True)
        spin.setValue(float(value))
        mapper = getattr(self, "_slider_value_mappers", {}).get(id(slider))
        if mapper is not None:
            slider.setValue(int(mapper[0](float(value))))
        else:
            slider.setValue(int(round(float(value) * scale)))
        spin.blockSignals(False)
        slider.blockSignals(False)

    def _set_speed_value(self, value: float):
        self._speed_slider.blockSignals(True)
        self._speed_spin.blockSignals(True)
        self._speed_spin.setValue(float(value))
        self._speed_slider.setValue(self._speed_to_slider(float(value)))
        self._speed_spin.blockSignals(False)
        self._speed_slider.blockSignals(False)

    def _apply_numeric_param(self, attr: str, value: float):
        if self._engine:
            setattr(self._engine, attr, float(value))
        self.params_changed.emit()

    def _set_combo_data(self, combo: QComboBox, value):
        combo.blockSignals(True)
        try:
            idx = combo.findData(value)
            if idx < 0:
                idx = combo.findData(str(value))
            if idx >= 0:
                combo.setCurrentIndex(idx)
        finally:
            combo.blockSignals(False)

    def _apply_combo_param(self, attr: str, combo: QComboBox):
        if self._engine:
            data = combo.currentData()
            setattr(self._engine, attr, data if data is not None else combo.currentText())
        self.params_changed.emit()

    def _matrix_effect_color(self) -> tuple:
        if self._engine is None:
            return (255, 255, 255)
        value = getattr(self._engine, "matrix_effect_color", (255, 255, 255))
        try:
            r, g, b = value
            return (
                max(0, min(255, int(r))),
                max(0, min(255, int(g))),
                max(0, min(255, int(b))),
            )
        except Exception:
            return (255, 255, 255)

    def _update_matrix_color_button(self):
        if not hasattr(self, "_matrix_color_btn"):
            return
        r, g, b = self._matrix_effect_color()
        self._matrix_color_btn.setStyleSheet(
            f"QPushButton{{background:rgb({r},{g},{b});border:1px solid #545454;"
            "border-radius:3px;min-height:20px;}"
            "QPushButton:hover{border-color:#71A8FF;}"
        )

    def _on_matrix_color_pick(self):
        if self._engine is None:
            return
        r, g, b = self._matrix_effect_color()
        color = QColorDialog.getColor(QColor(r, g, b), self, "矩阵效果颜色")
        if not color.isValid():
            return
        self._engine.matrix_effect_color = (color.red(), color.green(), color.blue())
        self._update_matrix_color_button()
        self.color_changed.emit()
        self.params_changed.emit()

    def set_engine(self, engine: MatrixPixelEngine):
        self._engine = engine
        self._refresh_device_lists()
        self._sync_pixel_param_widgets()
        if not hasattr(self, "_random_info_lbl"):    #  W2 #16：随机参数行（只挂一次）
            matrix_w2_ui.add_pixel_randomize_row(self, self.layout())

    def _sync_pixel_param_widgets(self):
        """把引擎参数回填到控件，避免加载工程后 UI 与运行参数不一致。"""
        if self._engine is None:
            return
        try:
            self._set_slider_spin_value(
                self._threshold_slider, self._threshold_spin,
                getattr(self._engine, 'threshold', 0.5))

            self._set_speed_value(getattr(self._engine, 'speed', 1.0))

            self._invert_cb.blockSignals(True)
            self._invert_cb.setChecked(bool(getattr(self._engine, 'invert', False)))
            self._invert_cb.blockSignals(False)

            self._audio_reactive_cb.blockSignals(True)
            self._audio_reactive_cb.setChecked(bool(getattr(self._engine, 'pixel_audio_reactive', True)))
            self._audio_reactive_cb.blockSignals(False)
            self._update_matrix_color_button()

            self._set_slider_spin_value(
                self._wave_density_slider, self._wave_density_spin,
                getattr(self._engine, 'matrix_wave_density', 1.0))
            self._set_slider_spin_value(
                self._wave_width_slider, self._wave_width_spin,
                getattr(self._engine, 'matrix_wave_width', 0.12))
            self._set_slider_spin_value(
                self._wave_twist_slider, self._wave_twist_spin,
                getattr(self._engine, 'matrix_wave_twist', 0.0))
            self._set_slider_spin_value(
                self._symmetry_mix_slider, self._symmetry_mix_spin,
                getattr(self._engine, 'matrix_symmetry_mix', 0.5))
            self._set_slider_spin_value(
                self._amplitude_slider, self._amplitude_spin,
                getattr(self._engine, 'audio_amplitude', 2.0))
            self._set_slider_spin_value(
                self._norm_offset_slider, self._norm_offset_spin,
                getattr(self._engine, 'audio_norm_offset', 0.0))
            self._sync_audio_param_widgets()
            self._set_slider_spin_value(
                self._brightness_slider, self._brightness_spin,
                getattr(self._engine, 'screen_brightness_boost', 1.0))
            self._set_slider_spin_value(
                self._contrast_slider, self._contrast_spin,
                getattr(self._engine, 'screen_contrast', 1.0))
            self._set_slider_spin_value(
                self._gamma_slider, self._gamma_spin,
                getattr(self._engine, 'screen_gamma', 1.0))
            self._set_slider_spin_value(
                self._smooth_slider, self._smooth_spin,
                getattr(self._engine, 'screen_smooth', 0.6), scale=1000)
            self._set_slider_spin_value(
                self._thr_lo_slider, self._thr_lo_spin,
                getattr(self._engine, 'screen_threshold_low', 0.0))
            self._set_slider_spin_value(
                self._thr_hi_slider, self._thr_hi_spin,
                getattr(self._engine, 'screen_threshold_high', 1.0))
            self._screen_fps_spin.blockSignals(True)
            self._screen_fps_spin.setValue(int(round(getattr(self._engine, 'screen_fps', 25.0))))
            self._screen_fps_spin.blockSignals(False)
            #  R183：走带/摄像头/变形/掩码控件按引擎值回填（工程还原后同步）
            sync_video_transport_widgets(self)
            sync_warp_widgets(self)
            sync_mask_widgets(self)
            matrix_w2_ui.sync_external_source_widgets(self)   #  W2 #4/#6
        except Exception:
            pass

    def _sync_audio_param_widgets(self) -> None:
        """音频页控件回填（helper；从 _sync_pixel_param_widgets 拆出，逐行搬移）。"""
        sync_audio_device(self)
        dirs = {"bottom": 0, "top": 1, "left": 2, "right": 3}
        self._bar_dir_combo.blockSignals(True)
        self._bar_dir_combo.setCurrentIndex(dirs.get(str(getattr(self._engine, 'audio_bar_direction', 'bottom')), 0))
        self._bar_dir_combo.blockSignals(False)
        self._set_combo_data(self._bar_style_combo, getattr(self._engine, 'audio_bar_style', 'solid'))
        self._set_combo_data(self._band_layout_combo, getattr(self._engine, 'audio_band_layout', 'linear'))
        self._set_slider_spin_value(
            self._bar_gamma_slider, self._bar_gamma_spin,
            getattr(self._engine, 'audio_bar_gamma', 0.85))
        self._set_slider_spin_value(
            self._bar_soft_slider, self._bar_soft_spin,
            getattr(self._engine, 'audio_bar_softness', 0.08), scale=1000)
        self._set_slider_spin_value(
            self._bar_smooth_slider, self._bar_smooth_spin,
            getattr(self._engine, 'audio_bar_smoothing', 0.35))
        self._set_slider_spin_value(
            self._bar_motion_slider, self._bar_motion_spin,
            getattr(self._engine, 'audio_bar_motion', 1.0))
        self._set_slider_spin_value(
            self._peak_hold_slider, self._peak_hold_spin,
            getattr(self._engine, 'audio_peak_hold', 0.45))
        self._set_slider_spin_value(
            self._peak_decay_slider, self._peak_decay_spin,
            getattr(self._engine, 'audio_peak_decay', 0.035), scale=1000)
        self._set_combo_data(self._audio_mod_target_combo, getattr(self._engine, 'matrix_audio_mod_target', 'brightness_speed'))
        self._set_combo_data(self._audio_mod_source_combo, getattr(self._engine, 'matrix_audio_mod_source', 'rms'))
        self._set_slider_spin_value(
            self._audio_mod_amount_slider, self._audio_mod_amount_spin,
            getattr(self._engine, 'matrix_audio_mod_amount', 0.6))
        self._set_slider_spin_value(
            self._audio_mod_floor_slider, self._audio_mod_floor_spin,
            getattr(self._engine, 'matrix_audio_mod_floor', 0.15))
        self._set_slider_spin_value(
            self._audio_mod_attack_slider, self._audio_mod_attack_spin,
            getattr(self._engine, 'matrix_audio_mod_attack_ms', 35.0), scale=1)
        self._set_slider_spin_value(
            self._audio_mod_release_slider, self._audio_mod_release_spin,
            getattr(self._engine, 'matrix_audio_mod_release_ms', 220.0), scale=1)
        from .matrix_pixel_music_controls import sync_music_controls
        sync_music_controls(self)

    def _build_ui(self):
        """M03 pure presentation delegate; original factories and slots remain."""
        from .matrix_pixel_properties_ui import build_pixel_parameters
        build_pixel_parameters(self)

    def _build_audio_page(self) -> QWidget:
        """M03 pure presentation delegate; original factories and slots remain."""
        from .matrix_pixel_properties_ui import build_audio_page
        return build_audio_page(self)

    def _build_video_group(self) -> QGroupBox:
        """M03 pure presentation delegate; original factories and slots remain."""
        from .matrix_pixel_media_workspace_ui import build_video_group
        return build_video_group(self)

    def _build_screen_page(self) -> QWidget:
        """M03 pure presentation delegate; original factories and slots remain."""
        from .matrix_pixel_media_workspace_ui import build_screen_page
        return build_screen_page(self)

    def _on_video_import(self) -> str:
        """导入视频文件，导入后立刻刷新第一帧预览。

        返回值（R181 空态入口据此区分"用户取消"与"导入失败"，避免静默）：
        ``"ok"`` / ``"cancelled"`` / ``"failed"``；作 Qt 槽用时返回值被忽略。
        """
        if self._engine is None:
            return "failed"
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入视频",
            "",
            "视频文件 (*.mp4 *.mov *.avi *.mkv *.webm *.m4v);;所有文件 (*.*)")
        if not path:
            return "cancelled"
        ok = False
        try:
            ok = self._engine.load_video(path)
        except Exception as e:
            QMessageBox.warning(self, "视频导入失败", str(e))
            return "failed"
        if not ok:
            QMessageBox.warning(self, "视频导入失败", "无法打开该视频文件，请确认 OpenCV 支持此格式。")
            return "failed"
        self._video_path_lbl.setText(path)
        # 导入视频只更新文件和首帧预览；真正播放/输出由顶部统一播放键控制。
        self._engine.video_playing = False
        total = max(0, int(self._engine.get_video_total_frames()))
        self._video_pos_slider.setRange(0, max(0, total - 1))
        if hasattr(self, '_video_clear_btn'):
            self._video_clear_btn.setEnabled(True)
        self.update_screen_preview(self._engine)
        self.params_changed.emit()
        return "ok"

    def _on_video_clear(self):
        """删除当前导入的视频并清空预览/进度状态；下一次导入不继承旧帧。"""
        if self._engine is None:
            return
        try:
            if hasattr(self._engine, 'clear_video'):
                self._engine.clear_video()
            else:
                self._engine.load_video('')
        except Exception:
            # R181 P2-3：删除视频失败 → 引擎仍持旧视频（下次导入继承旧帧），留痕
            logger.debug("删除当前视频失败（引擎可能仍持旧视频源）", exc_info=True)
        self._video_path_lbl.setText("未导入视频；视频像素采样使用视频完整画面，不设置采样区域；使用顶部统一播放键启动预览与矩阵输出。")
        if hasattr(self, '_video_pos_slider'):
            self._video_pos_slider.setRange(0, 0)
            self._video_pos_slider.setValue(0)
        if hasattr(self, '_video_pos_lbl'):
            self._video_pos_lbl.setText("帧 0 / 0   00:00.00")
        if hasattr(self, '_video_clear_btn'):
            self._video_clear_btn.setEnabled(False)
        try:
            self.update_screen_preview(self._engine)
        except Exception:
            pass
        self.params_changed.emit()
        self.video_cleared.emit()

    def _on_video_seek_released(self):
        """拖动进度条后立即跳帧，预览和下一次输出使用同一帧缓存。"""
        if self._engine is None:
            return
        frame = int(self._video_pos_slider.value())
        try:
            self._engine.seek_video_frame(frame)
            self.update_screen_preview(self._engine)
        except Exception:
            pass
        self.params_changed.emit()

    def _format_video_time(self, frame: int, fps: float) -> str:
        fps = fps if fps and fps > 0 else 25.0
        seconds = max(0.0, frame / fps)
        mm = int(seconds // 60)
        ss = seconds - mm * 60
        return f"{mm:02d}:{ss:05.2f}"

    def _open_screen_overlay(self):
        """打开可拖动/缩放的屏幕采样框。"""
        try:
            from .screen_capture_overlay import ScreenCaptureOverlay
        except ImportError:
            import logging
            logging.getLogger(__name__).warning(
                "screen_capture_overlay.py not found; using manual input only")
            return

        if self._screen_overlay is None:
            self._screen_overlay = ScreenCaptureOverlay()
            self._screen_overlay.region_confirmed.connect(self._on_overlay_region)

        # 注入当前设备网格尺寸（供叠加显示）
        if self._engine is not None:
            grid = getattr(self._engine, '_grid', None)
            if grid is not None:
                self._screen_overlay.set_grid(
                    max(1, getattr(grid, 'cols', 1)),
                    max(1, getattr(grid, 'rows', 1)))

        self._screen_overlay.show()
        self._screen_overlay.raise_()

    def _on_overlay_region(self, region: dict):
        """采样框确认后回调：更新引擎区域 + 手动 spinbox。"""
        if self._engine:
            self._engine.screen_region = region
        # 同步到手动输入框
        for key, spin in self._region_spins.items():
            spin.setValue(region.get(key, 0))
        # 更新信息标签
        self._region_info_lbl.setText(
            f"区域：x={region.get('left',0)}  y={region.get('top',0)}"
            f"  {region.get('width',0)}×{region.get('height',0)} px")
        self.params_changed.emit()

    def _configure_screen_tabs_for_mode(self, mode: str):
        """按采样模式重建子页面：屏幕采样与视频采样不混用冗余面板。"""
        tabs = getattr(self, "_screen_config_tabs", None)
        if tabs is None:
            return
        video_tab = getattr(self, "_screen_video_tab", None)
        region_tab = getattr(self, "_screen_region_tab", None)
        params_tab = getattr(self, "_screen_params_tab", None)
        preview_grp = getattr(self, "_sample_preview_grp", None)
        tabs.blockSignals(True)
        while tabs.count() > 0:
            tabs.removeTab(0)
        if mode == MODE_VIDEO_SAMPLE:
            self._add_video_mode_tabs(tabs, video_tab, preview_grp)
        elif mode == MODE_SCREEN_SAMPLE:
            if region_tab is not None:
                tabs.addTab(region_tab, "屏幕区域")
            if params_tab is not None:
                tabs.addTab(params_tab, "屏幕采样参数")
            if preview_grp is not None:
                preview_grp.setTitle("屏幕采样预览 / 矩阵映射")
        else:
            if params_tab is not None:
                tabs.addTab(params_tab, "采样参数")
            if preview_grp is not None:
                preview_grp.setTitle("实时采样预览")
        if tabs.count() > 0:
            tabs.setCurrentIndex(0)
        tabs.blockSignals(False)

    def _add_video_mode_tabs(self, tabs, video_tab, preview_grp) -> None:
        """视频模式子页：视频源 + 变形（#2）+ 掩码（#3）（helper）。"""
        if video_tab is not None:
            tabs.addTab(video_tab, "视频源")
        for tab, title in ((getattr(self, "_warp_tab", None), "变形"),
                           (getattr(self, "_mask_tab", None), "掩码")):
            if tab is not None:
                tabs.addTab(tab, title)
        if preview_grp is not None:
            preview_grp.setTitle("视频采样预览 / 矩阵映射")

    def show_mode_params(self, mode: str):
        """根据模式切换参数面板。"""
        if mode in (MODE_SCREEN_SAMPLE, MODE_VIDEO_SAMPLE):
            self._stack.setCurrentWidget(self._screen_page)
            self._configure_screen_tabs_for_mode(mode)
        else:
            from .matrix_pixel_audio_modes import show_audio_mode_params
            show_audio_mode_params(self, mode)

    def _refresh_device_lists(self):
        if self._engine is None:
            return
        # 刷新音频设备列表
        refresh_audio_devices(self)
        # 刷新显示器列表
        self._monitor_combo.clear()
        for m in self._engine.list_monitors():
            self._monitor_combo.addItem(m["name"], m["index"])
        if self._monitor_combo.count() == 0:
            self._monitor_combo.addItem("主显示器", 1)

    def _on_threshold_changed(self, v: int):
        if hasattr(self, "_threshold_spin"):
            return
        val = v / 100.0
        self._threshold_lbl.setText(f"{val:.2f}")
        if self._engine:
            self._engine.threshold = val
        self.params_changed.emit()

    def _on_speed_changed(self, v: int):
        if hasattr(self, "_speed_spin"):
            return
        val = v / 100.0
        self._speed_lbl.setText(f"{val:.1f}x")
        if self._engine:
            self._engine.speed = val
        self.params_changed.emit()

    def _on_invert_changed(self, v: bool):
        if self._engine:
            self._engine.invert = v
        self.params_changed.emit()

    def _on_audio_reactive_changed(self, v: bool):
        if self._engine:
            self._engine.pixel_audio_reactive = bool(v)
        self.params_changed.emit()

    def _on_wave_density_changed(self, v: int):
        if hasattr(self, "_wave_density_spin"):
            return
        val = v / 100.0
        self._wave_density_lbl.setText(f"{val:.2f}")
        if self._engine:
            self._engine.matrix_wave_density = val
        self.params_changed.emit()

    def _on_wave_width_changed(self, v: int):
        if hasattr(self, "_wave_width_spin"):
            return
        val = v / 100.0
        self._wave_width_lbl.setText(f"{val:.2f}")
        if self._engine:
            self._engine.matrix_wave_width = val
        self.params_changed.emit()

    def _on_wave_twist_changed(self, v: int):
        if hasattr(self, "_wave_twist_spin"):
            return
        val = v / 100.0
        self._wave_twist_lbl.setText(f"{val:.2f}")
        if self._engine:
            self._engine.matrix_wave_twist = val
        self.params_changed.emit()

    def _on_symmetry_mix_changed(self, v: int):
        if hasattr(self, "_symmetry_mix_spin"):
            return
        val = v / 100.0
        self._symmetry_mix_lbl.setText(f"{val:.2f}")
        if self._engine:
            self._engine.matrix_symmetry_mix = val
        self.params_changed.emit()

    def _on_amplitude_changed(self, v: int):
        if hasattr(self, "_amplitude_spin"):
            return
        val = v / 100.0
        self._amplitude_lbl.setText(f"{val:.1f}")
        if self._engine:
            self._engine.audio_amplitude = val
        self.params_changed.emit()

    def _on_norm_offset_changed(self, v: int):
        if hasattr(self, "_norm_offset_spin"):
            return
        val = v / 100.0
        self._norm_offset_lbl.setText(f"{val:.2f}")
        if self._engine:
            self._engine.audio_norm_offset = val
        self.params_changed.emit()

    def _on_bar_dir_changed(self, idx: int):
        dirs = ["bottom", "top", "left", "right"]
        if self._engine and 0 <= idx < len(dirs):
            self._engine.audio_bar_direction = dirs[idx]
        self.params_changed.emit()

    def _on_audio_device_changed(self, idx: int):
        if self._engine:
            device = self._audio_device_combo.itemData(idx)
            select_audio_device(self._engine, device)
        self.params_changed.emit()

    def _on_monitor_changed(self, idx: int):
        if self._engine:
            monitor = self._monitor_combo.itemData(idx)
            if monitor is not None:
                self._engine.screen_monitor = int(monitor)
        self.params_changed.emit()

    def _on_brightness_changed(self, v: int):
        if hasattr(self, "_brightness_spin"):
            return
        val = v / 100.0
        self._brightness_lbl.setText(f"{val:.1f}")
        if self._engine:
            self._engine.screen_brightness_boost = val
        self.params_changed.emit()

    def _on_contrast_changed(self, v: int):
        if hasattr(self, "_contrast_spin"):
            return
        val = v / 100.0
        self._contrast_lbl.setText(f"{val:.1f}")
        if self._engine:
            self._engine.screen_contrast = val
        self.params_changed.emit()

    def _on_gamma_changed(self, v: int):
        if hasattr(self, "_gamma_spin"):
            return
        val = v / 100.0
        self._gamma_lbl.setText(f"{val:.2f}")
        if self._engine:
            self._engine.screen_gamma = val
        self.params_changed.emit()

    def _on_smooth_changed(self, v: int):
        if hasattr(self, "_smooth_spin"):
            return
        val = v / 100.0
        self._smooth_lbl.setText(f"{val:.2f}")
        if self._engine:
            self._engine.screen_smooth = val
        self.params_changed.emit()

    def _on_thr_lo_changed(self, v: int):
        if hasattr(self, "_thr_lo_spin"):
            return
        val = v / 100.0
        self._thr_lo_lbl.setText(f"{val:.2f}")
        if self._engine:
            self._engine.screen_threshold_low = val
        self.params_changed.emit()

    def _on_thr_hi_changed(self, v: int):
        if hasattr(self, "_thr_hi_spin"):
            return
        val = v / 100.0
        self._thr_hi_lbl.setText(f"{val:.2f}")
        if self._engine:
            self._engine.screen_threshold_high = val
        self.params_changed.emit()

    def _on_screen_fps_changed(self, v: int):
        if self._engine:
            self._engine.screen_fps = float(v)
        self.params_changed.emit()

    def _on_channel_changed(self, idx: int):
        if self._engine:
            channel = self._channel_combo.itemData(idx)
            if channel:
                self._engine.screen_channel = str(channel)
        self.params_changed.emit()

    def _on_region_changed(self):
        if self._engine:
            l = self._region_spins["left"].value()
            t = self._region_spins["top"].value()
            w = self._region_spins["width"].value()
            h = self._region_spins["height"].value()
            if w > 0 and h > 0:
                region = {"left": l, "top": t, "width": w, "height": h}
                self._engine.screen_region = region
                self._region_info_lbl.setText(
                    f"区域：x={l}  y={t}  {w}×{h} px")
            else:
                self._engine.screen_region = None
                self._region_info_lbl.setText("区域：全屏")
        self.params_changed.emit()

    def update_screen_preview(self, engine):
        """屏幕/视频采样模式下，由主面板 tick 调用，刷新预览和统计信息。"""
        if not hasattr(self, '_screen_preview_canvas'):
            return
        mode = getattr(engine, '_mode', '') if engine else ''
        if mode == MODE_VIDEO_SAMPLE:
            raw = engine.get_video_preview_raw() if engine else None
            grid = engine.get_video_preview_grid() if engine else None
            stats = engine.get_video_stats() if engine else {}
            cur = int(engine.get_video_current_frame()) if engine else 0
            total = int(engine.get_video_total_frames()) if engine else 0
            fps = float(engine.get_video_fps()) if engine else 25.0
            self._screen_preview_canvas.set_data(raw, grid)
            if hasattr(self, '_video_pos_slider') and total > 0 and not self._video_pos_slider.isSliderDown():
                max_pos = max(0, total - 1)
                if self._video_pos_slider.maximum() != max_pos:
                    self._video_pos_slider.setRange(0, max_pos)
                slider_val = max(0, min(cur, max_pos))
                if self._video_pos_slider.value() != slider_val:
                    self._video_pos_slider.setValue(slider_val)
            if hasattr(self, '_video_pos_lbl'):
                self._video_pos_lbl.setText(
                    f"帧 {cur + 1 if total else 0} / {total}   {self._format_video_time(cur, fps)}")
        else:
            raw = None
            grid = engine.get_screen_preview_grid() if engine else None
            stats = engine.get_screen_stats() if engine else {}
            self._screen_preview_canvas.set_data(raw, grid)
        # 同步阈值到预览画布
        threshold = getattr(engine, 'threshold', 0.5)
        self._screen_preview_canvas._threshold = threshold
        stats_text = (
            f"min={stats.get('min', 0):.2f}  "
            f"max={stats.get('max', 0):.2f}  "
            f"mean={stats.get('mean', 0):.2f}  "
            f"std={stats.get('std', 0):.2f}")
        if stats_text != getattr(self, "_last_preview_stats_text", ""):
            self._last_preview_stats_text = stats_text
            self._screen_stats_lbl.setText(stats_text)


# ─────────────────────────────────────────────────────────────────────────────
#  帧参数编辑面板
# ─────────────────────────────────────────────────────────────────────────────

class SlideParamPanel(QWidget):
    """单帧参数编辑面板"""

    params_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._slide: Optional[MatrixSlide] = None
        self._updating = False
        self._build_ui()

    def _build_ui(self):
        from .matrix_slide_properties_ui import build_slide_properties
        build_slide_properties(self)

    def load_slide(self, slide: MatrixSlide):
        self._slide = slide
        self._updating = True
        self._name_edit.setText(slide.name)
        self._hold_spin.setValue(slide.hold_time)
        self._trans_spin.setValue(slide.transition_time)
        # 找到过渡模式索引
        for i in range(self._trans_mode_combo.count()):
            if self._trans_mode_combo.itemData(i) == slide.transition_mode:
                self._trans_mode_combo.setCurrentIndex(i)
                break
        self._on_value_spin.setValue(slide.on_value)
        self._off_value_spin.setValue(slide.off_value)
        self._updating = False
        self._ppt_properties_context.refresh()

    def _on_name_changed(self):
        if self._slide and not self._updating:
            self._slide.name = self._name_edit.text()
            self.params_changed.emit()

    def _on_hold_changed(self, v: float):
        if self._slide and not self._updating:
            self._slide.hold_time = v
            self.params_changed.emit()

    def _on_trans_changed(self, v: float):
        if self._slide and not self._updating:
            self._slide.transition_time = v
            self.params_changed.emit()

    def _on_trans_mode_changed(self, idx: int):
        if self._slide and not self._updating:
            self._slide.transition_mode = self._trans_mode_combo.itemData(idx)
            self.params_changed.emit()

    def _on_value_changed(self, v: int):
        if self._slide and not self._updating:
            self._slide.on_value = v
            self.params_changed.emit()

    def _on_off_value_changed(self, v: int):
        if self._slide and not self._updating:
            self._slide.off_value = v
            self.params_changed.emit()


# ─────────────────────────────────────────────────────────────────────────────
#  主面板
# ─────────────────────────────────────────────────────────────────────────────

def _pixel_node(ctx, running, recalled):
    """构造 matrix 像素模式的一格节点。

     `extra["recalled"]`：只被场景调用带活的 → 保存新场景时排除（用户 2026-09-14）。
    """
    from ...processing.dispatch import EffectNode
    mid, label, cat, ids, group_key, group_name, gid, layer = ctx
    return EffectNode(
        panel="matrix", track_type=mid or "pixel", group_key=group_key,
        group_name=group_name, group_id=gid, fixture_ids=ids, effect_id=mid,
        effect_name=label or "像素效果", category=cat, layers=[layer],
        summary=f"像素 {label or mid} · {len(ids)} 台", live=running,
        extra={"recalled": recalled})


def _ppt_node(ctx, playing, recalled):
    """构造 matrix 幻灯片模式的一格节点（`extra["recalled"]` 同 `_pixel_node`）。"""
    from ...processing.dispatch import EffectNode
    name, idx, ids, group_key, group_name, gid = ctx
    return EffectNode(
        panel="matrix", track_type="ppt", group_key=group_key,
        group_name=group_name, group_id=gid, fixture_ids=ids, effect_id="",
        effect_name=name or "幻灯片", category="", layers=["matrix"],
        summary=f"幻灯片 {name or idx} · {len(ids)} 台", live=playing,
        extra={"recalled": recalled})


def _recall_scope(panel, node: dict, state=None) -> None:
    """恢复该矩阵节点的设备作用域（缺失设备时不抛错，保持原语义）。"""
    try:
        restore_node_authoring_scope(panel, node, state)
    except Exception:
        logger.debug("matrix: dispatch recall group switch failed", exc_info=True)


def _ids_of(positions) -> set:
    """顶视图位置行里的设备 id 集（坏数据安全）。"""
    try:
        return {str(f.get("id", "")) for f in (positions or [])}
    except Exception:
        return set()


def _restore_group(panel, state: dict) -> None:
    """还原面板记录的设备组（`save_state` 写了 group_id/group_name 但原先从不读）。

     深查修正：工程往返后矩阵设备组丢失（用户："我存的组没了"）。
      仅在状态确实带值时覆盖（缺失=保持现状，不用空串清掉用户当前选择）。
    """
    from .matrix_target_state import restore_group_scope
    restore_group_scope(panel, state or {})


def _pget(params: dict, key: str, obj, default):
    """取 `params[key]`；**缺失时回落 `obj` 的当前值**（而非硬编码默认）。

     深查修正（部分状态清空实况）：矩阵像素参数还原原先一律 `params.get(key, 硬默认)`
      → 任何缺该 key 的（旧工程/部分场景）状态都会把**实况值重置为默认**
      （实测：只带 threshold 的状态把 speed/gamma 打回 1.0）。
      改为"没有这个 key 就不动当前值"，对象确实无该属性时才用硬默认兜底。
    """
    try:
        if key in params:
            return params[key]
    except Exception:
        pass
    return getattr(obj, key, default)


def _mark_panel_dirty(panel) -> None:
    """矩阵面板**用户主动改动** → 置工程脏（防"改完关窗不提示、改动丢失"）。

     R181 修复（弹层改参数不标 dirty → 保存不落盘）：`save_state` 只在保存/
      自动保存时被拉取，而弹层（重命名/输出平滑）与帧序列/像素参数改动此前
      **不置脏** → 用户改完直接关窗既无未保存提示、改动也不进盘。
      机制：`ProjectManager._mark_dirty()` 发 `dirty_changed`，主窗经
      `_bridge_pm_dirty` 桥到 `MainWindow._dirty`（closeEvent 只读后者）。
    """
    pm = getattr(panel, "_pm", None)
    if pm is None:
        return
    try:
        if getattr(pm, "project", None) is None:
            return
        pm._mark_dirty()
    except Exception:
        logger.debug("matrix: mark project dirty failed", exc_info=True)


def _bake_preset_into_engine(panel, name: str, *, wings, block_size,
                             wing_direction, density) -> None:
    """内置序列预设 → 引擎帧序列 + 缩略图重建 + 置脏。

    R181：抽自 `_load_preset`（该函数已在门禁基线内、只许缩不许长），
    并把"预设载入 = 用户编辑 → 置脏"补上（此前载入序列后关窗无未保存提示）。
    """
    engine = getattr(panel, "_engine", None)
    if engine is None:
        return
    from .matrix_ppt_catalog import snapshot_sequence_edit, store_sequence_edit
    sequence_options = load_composition_options(panel, name)
    snapshot_sequence_edit(panel)
    engine.load_preset(name, wings=wings, block_size=block_size,
                       wing_direction=wing_direction, density=density, sequence_options=sequence_options,
                       **_wing_flags.controls.values(panel, "_"))
    record_applied_composition(panel, name, sequence_options)
    panel._rebuild_thumbs()
    store_sequence_edit(panel)
    _mark_panel_dirty(panel)


#  2026-09-21：清台 / 运行态还原的辅助搬进 `matrix_stage_helpers`（本文件只许减行，
#   见 CLAUDE.md §10.2 与架构门禁；两边语义一致，此处仅转发 import）。
from . import matrix_running_outputs as _running_outputs
from .matrix_pixel_music_controls import refresh_pitch_status
from .matrix_pixel_audio_contract import refresh_audio_devices, select_audio_device, sync_audio_device
from .matrix_stage_helpers import (            # noqa: E402  (首个 import 需在上述工具函数之后)
    _apply_pixel_runtime_params, _clear_matrix_stage, _init_stage_identity,
    _maybe_clear_stage_on_project_switch, _quiesce_other_transport,
    _restore_pixel_params, _sync_video_ext_to_config,
)


def _restore_panel_mode(panel, state: dict) -> None:
    """还原面板工作模式（ppt / 像素）与对应按钮。

     2026-09-14 深查修正（存了白存）：`save_state` 一直写 `state['mode']`，但
      `load_state` **从未读它** → 在像素模式存的场景，若调用时面板处于 ppt 模式，
      `self._mode` 仍是 "ppt"，`_tick` 走 ppt 分支、**像素输出永不写** →
      "存好的像素场景调回来是死的"。`blockSignals` 防止 toggled 回调再次停/放。
    """
    want = str((state or {}).get('mode', '') or '')
    if want not in ("ppt", "pixel"):
        return
    panel._mode = want
    try:
        for btn, on in ((getattr(panel, '_ppt_mode_btn', None), want == "ppt"),
                        (getattr(panel, '_pixel_mode_btn', None), want == "pixel")):
            if btn is None:
                continue
            btn.blockSignals(True)
            btn.setChecked(bool(on))
            btn.blockSignals(False)
    except Exception:
        logger.debug("matrix: sync mode buttons failed", exc_info=True)


class MatrixSlideshowPanel(QWidget):
    #:  2026-09-14 用户："其它页面选中的设备应该也先传给 topview 再传给调度台。
    #:   比如 matrix 的选中设备的列表…" —— 矩阵的**设备选中回传**出口，与 Programmer
    #:   的 `fixture_selection_changed` 同款：→ MainWindow
    #:   `_sync_fixture_selection_to_topview`（TopView 统一漏斗）→ 调度台监控区。
    #:   此前矩阵只**接收** TopView 选择、从不**回传**（用户说的"残缺"）。
    fixture_selection_changed = pyqtSignal(list)

    """
    矩阵 ON/OFF PPT 编辑器主面板

    两种工作模式：
      PPT 模式    — 手动编辑 ON/OFF 帧，幻灯片播放
      像素效果模式 — 实时像素效果引擎驱动
    """

    pattern_updated = pyqtSignal(dict)  # {fixture_id: {ch_type: val}} 兼容旧接口

    def __init__(self, project_manager, parent=None):
        super().__init__(parent)
        self._pm = project_manager
        self._fixtures: List[Dict] = []
        self._current_group_id: str = ""
        #: 由 TopView 反向驱动的加载期间抑制回传，避免"写 TopView → 再回传"绕圈
        #: （与 programmer._suppress_topview_selection_emit 同款约定）。
        self._suppress_topview_selection_emit: bool = False
        self._current_group_name: str = ""
        self._all_topview_positions: List[Dict] = []
        self._filter_group_id: Optional[str] = None  # 设备组过滤选中的组 ID
        self._ch_type: str = CH_PUMP  # 当前通道类型
        # 大量设备性能缓存：{ch_type: [(fixture_id, universe, dmx_addr0)]}
        self._channel_addr_cache: Dict[str, List[tuple]] = {}
        self._fixture_addr_cache: Dict[str, Dict[str, List[tuple]]] = {}
        self._matrix_vector_maps: Dict[str, Dict[str, np.ndarray]] = {}
        self._matrix_output_cache: Dict[str, Dict[int, Dict[int, int]]] = {}
        self._matrix_pattern_cache: Dict[str, Dict[str, int]] = {}
        self._matrix_pattern_pending: Dict[str, Dict[str, int]] = {}
        self._last_matrix_pattern_emit: float = 0.0
        self._matrix_pattern_emit_interval: float = 0.12
        self._large_matrix_pattern_emit_interval: float = 0.50
        self._last_matrix_full_output_emit: float = 0.0
        self._matrix_full_output_interval: float = 1.0
        self._force_matrix_full_output: bool = True
        self._matrix_ltp_takeover_pending: bool = True
        self._last_pixel_preview_update: float = 0.0
        self._perf_slow_matrix_output_ms: float = 12.0
        self._perf_last_matrix_output_log: float = 0.0
        self._large_fixture_preview_threshold: int = 540

        # 引擎
        self._engine = MatrixSlideEngine()
        self._engine.on_output = self._on_engine_output

        self._pixel_engine = MatrixPixelEngine()
        _init_stage_identity(self)   # 清台基线 + 工程身份指纹（见模块函数）

        # NM 引用（由 main_window 注入）
        self._nm_ref = None
        self._timeline_ctrl_ref = None
        self._topview_ref = None  # TopView 引用：用于实时位置/选择同步

        # 工作模式
        self._mode: str = "ppt"  # "ppt" or "pixel"

        #  2026-09-17：面板隐藏标记（出光中保留主循环时，用它跳过 UI 预览刷新）。
        self._ui_hidden: bool = False

        # 像素效果运行状态
        self._pixel_running: bool = False
        self._pixel_last_t: float = 0.0
        self._pixel_paused: bool = False
        self._pixel_transition_busy: bool = False
        self._pending_pixel_mode_id: Optional[str] = None
        self._pixel_effect_apply_timer = QTimer(self)
        self._pixel_effect_apply_timer.setSingleShot(True)
        self._pixel_effect_apply_timer.timeout.connect(self._apply_pending_pixel_effect)
        self._pixel_resource_release_timer = QTimer(self)
        self._pixel_resource_release_timer.setSingleShot(True)
        self._pixel_resource_release_timer.timeout.connect(self._release_idle_pixel_resources)
        self._slice_key: Optional[int] = None
        self._slice_key_modifiers: int = 0
        self._capturing_slice_key: bool = False
        self._manual_pixel_slice_index: int = 0
        self._manual_slice_latched: bool = False
        self._manual_slice_states: Optional[Dict[str, bool]] = None
        self._last_slice_key_time: float = 0.0
        self._slice_shortcut: Optional[QShortcut] = None
        _wing_flags.initialize(self)
        #  2026-08-11 #18：跳过编号排序（只看坐标按应用方向排序，不看设备组选择序）
        self._skip_base_order: bool = False

        # 主定时器（ 不在此 start：交给 showEvent，面板可见才跑 25fps）
        self._timer = QTimer(self)
        self._timer.setInterval(40)  # 25fps
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)

        # 调度台上报门槛：只有用户真做过效果（写过输出/播放过）才上报，
        # 防"打开 Matrix 页就在调度台显示有效果"（2026-09-13 修）。
        self._bus_user_touched: bool = False

        self._build_ui()
        self._apply_style()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        #  兜底：从 ProjectManager 直接拉 fixtures 自填充画布。这样即使
        # TopView 还没懒加载、用户也没点过设备组，打开 Matrix 面板就有内容。
        # 之后 TopView 加载或用户选组都会通过 set_fixtures / load_fixture_group
        # 用更精确的坐标覆盖。
        self._thumbs = []  # 防止 _rebuild_thumbs 之前访问报 AttributeError
        self._bootstrap_from_pm()
        try:
            if hasattr(self._pm, "project_changed"):
                self._pm.project_changed.connect(self._on_project_changed_for_matrix)
        except Exception:
            pass
        self.refresh_filter_group_combo()

    # ── 样式 ──────────────────────────────────────────────────────────────────

    def _matrix_output_active(self) -> bool:
        """面板当前是否有**出光中**的活动（隐藏时据此决定是否保留主循环）。"""
        try:
            if _running_outputs.has_runs(self):
                return True
            if str(getattr(self, "_mode", "")) == "ppt":
                eng = getattr(self, "_engine", None)
                if eng is not None and bool(getattr(eng, "is_playing", False)):
                    return True
            if bool(getattr(self, "_pixel_running", False)):
                return True
        except Exception:
            pass
        return False

    def showEvent(self, event):
        super().showEvent(event)
        # Visibility preserves live S; constructor/project_changed own fixture loading.
        #  可见性门控（2026-09-13；R183 升级为多窗）：主窗显示时恢复 25fps。
        #   原构造即 start() → dock 隐藏后仍常驻 25fps 抢主线程，是
        #   "什么都没干也卡"的来源之一；R183 后判定交给统一门控函数
        #   （主窗或任一独立子窗可见即跑）。
        self._sync_visibility_gate()

    def hideEvent(self, event):
        """隐藏处理（2026-09-17 用户定案：**隐藏不等于释放**；R183 多窗语义）。

        旧实现无条件停 25fps 主循环 —— 而 `_tick` 同时承担**出光**（PPT 引擎
        推进 / pixel 逐帧写 NM）与 UI 预览两部分，于是吸附成 tab（非激活页
        hide）/点关闭按钮都会让正在跑的效果停住，用户看到"一关页面就 release"。

        新语义：**出光中（PPT 播放 / pixel 运行）时保留主循环**，只置
        `_ui_hidden` 让 `_tick` 跳过不可见的 UI 预览刷新；确实空闲时才停表
        （保留 2026-09-13「空闲隐藏不烧 CPU」的收益）。
        R183：`_ui_hidden` 与停表判定改为**「主窗或任一子窗可见」**——关掉 dock
        但幻灯片/像素独立窗还开着时，预览照刷、循环照跑。
        真正停下出光仍只由用户主动动作触发（停止按钮 / 全局释放 / 场景关闭）。
        """
        super().hideEvent(event)
        self._sync_visibility_gate()

    def settle_hidden_timer(self) -> None:
        """外部释放/停止输出后调用：全窗不可见且已空闲时收掉 25fps 主循环。

         2026-09-17 深查修正（隐藏面板空转 25fps）：`hideEvent` 只在隐藏那一刻
          判定一次「是否出光中」。若隐藏时在出光（保留主循环），之后由**外部**路径
          释放（全清空 / 关闭场景 / 播放自然结束），没有任何地方重新判定 ——
          隐藏面板会以 PreciseTimer 25fps 空转到会话结束。
          各释放路径（`_stop_running_output` / `_clear_matrix_layers` / 全清空）
          调用本方法做一次收口。可见时无操作（保持原有 show/hide 语义）。
          R183：可见性判定同 `_sync_visibility_gate`（任一子窗可见即视为可见）。
        """
        # 判定逻辑**不包在 try 里**：这里曾经整段 try/except 把 AttributeError
        # 一起吞掉（探针"没停表"却无任何提示）——与仓库「兜底 except 吞掉自己
        # 新引入的 bug」的教训一致。只有 Qt 计时器调用需要容错。
        if self._any_window_visible():
            return
        if self._matrix_output_active():
            return
        t = getattr(self, "_timer", None)
        if t is None:
            return
        try:
            if t.isActive():
                t.stop()
        except Exception:
            logger.debug("matrix: settle hidden timer failed", exc_info=True)

    def _apply_style(self):
        # 原 QWidget/QPushButton/QComboBox/QSpinBox/QLineEdit/QSlider/QTabBar
        # 通配规则已删除——它们打穿了全局主题的四态样式（规范 §3）。
        # 页面底色由 app 暗色调色板(QPalette.Window=#1D1D1D)承接。
        self.setStyleSheet("")

    # ── UI 构建 ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        """主界面区留在 dock 内；幻灯片 / 像素效果各自**独立顶层窗口**（R183）。

         拆窗重做只动**布局与控件归属**：三个区共享同一引擎与预览控件
          （主界面的画布、引擎实例在面板生命周期内唯一），开关窗口不重建。
          两个窗口初始隐藏，由主界面顶部两个切换按钮打开并置前；关闭=隐藏
          （`MatrixChildWindow`），重开状态/尺寸保留。
        """
        from .matrix_zone_subpages import (
            build_main_zone, build_ppt_window, build_pixel_window,
        )
        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        # 主界面：顶部通用行 + 中央预览（占主位）+ 底部通用控件
        self._main_zone = build_main_zone(self)
        main_lay.addWidget(self._main_zone)
        # 幻灯片独立窗口：帧列表 + 过渡设置 + 幻灯片参数 + 帧操作
        self._ppt_window = build_ppt_window(self)
        bpm_row = matrix_w2_ui.attach_slide_bpm_row(self)    # W2原实例/原信号
        self._slide_param_panel._ppt_properties_context.adopt_bpm_row(bpm_row)
        # 像素效果独立窗口：模式下拉 + 像素参数 + 视频/屏幕源
        self._pixel_window = build_pixel_window(self)

    # ── 独立窗口开合与可见性门控（R183） ─────────────────────────────────────

    def show_ppt_window(self) -> None:
        """打开（或置前）幻灯片独立窗口；已开则只 raise。"""
        self._show_child_window("_ppt_window")

    def show_pixel_window(self) -> None:
        """打开（或置前）像素效果独立窗口；已开则只 raise。"""
        self._show_child_window("_pixel_window")

    def _show_child_window(self, attr: str) -> None:
        """show + raise + activate：关闭过的窗口重新显示（不重建，状态保留）。"""
        w = getattr(self, attr, None)
        if w is None:
            return
        try:
            if not w.isVisible():
                w.show()
            w.raise_()
            w.activateWindow()
        except Exception:
            logger.debug("matrix: show child window %s failed", attr, exc_info=True)

    def _child_windows(self) -> tuple:
        """两个独立子窗（构造期/销毁期可能缺失，故用 getattr 兜底）。"""
        return (getattr(self, "_ppt_window", None),
                getattr(self, "_pixel_window", None))

    def _any_window_visible(self) -> bool:
        """主面板或任一独立子窗可见 → UI 预览需要刷新（R183 多窗语义）。"""
        try:
            if self.isVisible():
                return True
            return any(w is not None and w.isVisible() for w in self._child_windows())
        except Exception:
            # 判定失败按「可见」处理：宁可多刷一帧，不可让用户看着死的预览
            logger.debug("matrix: visibility probe failed", exc_info=True)
            return True

    def _sync_visibility_gate(self) -> None:
        """按「主窗或任一子窗可见」重算 `_ui_hidden` 与 25fps 主循环（R183）。

        旧实现是**单窗假设**（只看宿主面板是否可见）：幻灯片/像素变成独立
        窗口后，关掉 dock 而子窗还开着时会被误判成「全不可见」→ 正在跑的
        预览/缩略图高亮被停掉。showEvent/hideEvent（宿主与子窗）都回调本方法。
        """
        visible = self._any_window_visible()
        self._ui_hidden = not visible
        t = getattr(self, "_timer", None)
        if t is None:
            return
        try:
            if visible:
                if not t.isActive():
                    t.start()
            elif t.isActive() and not self._matrix_output_active():
                t.stop()
        except Exception:
            logger.debug("matrix: visibility gate timer sync failed", exc_info=True)


    def _make_wing_direction_toolbar(self) -> QWidget:
        widget = QWidget()
        widget.setStyleSheet("background:transparent;border:none;")
        lay = QHBoxLayout(widget)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        for mode in WING_DIRECTION_MODES:
            btn = QToolButton()
            btn.setText(WING_DIRECTION_SYMBOLS.get(mode, "/"))
            btn.setCheckable(True)
            btn.setProperty("wing_direction", mode)
            btn.setToolTip(WING_DIRECTION_LABELS.get(mode, mode))
            btn.setFixedSize(20, 20)
            btn.setStyleSheet(
                "QToolButton{background:#1D1D1D;color:#CCCCCC;border:1px solid #3D3D3D;"
                "border-radius:3px;padding:0;font-size:11px;font-weight:bold;}"
                "QToolButton:hover{background:#363636;}"
                "QToolButton:checked{background:#4772B3;border-color:#71A8FF;color:#FFFFFF;}"
            )
            btn.clicked.connect(lambda _checked=False, m=mode: self._set_wing_direction(m))
            lay.addWidget(btn)
            self._wing_direction_buttons.append(btn)
        self._sync_wing_direction_buttons()
        return widget

    def _set_wing_direction(self, mode: str):
        normalized = normalize_wing_direction(mode)
        if normalized == getattr(self, "_wing_direction", WING_DIRECTION_LOW_TO_HIGH):
            self._sync_wing_direction_buttons()
            return
        self._wing_direction = normalized
        self._sync_wing_direction_buttons()
        self._sync_wing_block_to_pixel_engine()
        self._refresh_current_matrix_output()

    def _sync_wing_direction_buttons(self):
        current = normalize_wing_direction(getattr(self, "_wing_direction", WING_DIRECTION_LOW_TO_HIGH))
        live = []
        for btn in list(getattr(self, "_wing_direction_buttons", []) or []):
            try:
                mode = normalize_wing_direction(btn.property("wing_direction"))
                btn.blockSignals(True)
                btn.setChecked(mode == current)
                btn.blockSignals(False)
                live.append(btn)
            except RuntimeError:
                continue
            except Exception:
                continue
        self._wing_direction_buttons = live

    def _set_play_button_text(self, playing: Optional[bool] = None):
        """Keep the play button label in one place to avoid stale garbled text."""
        if not hasattr(self, "_play_btn"):
            return
        if playing is None:
            playing = bool(self._play_btn.isChecked())
        self._play_btn.setText("暂停" if playing else "播放")

    def _on_matrix_fps_changed(self, v: int):
        fps = max(1, min(60, int(v or 25)))
        if len(getattr(self, "_fixtures", []) or []) >= int(getattr(self, "_large_fixture_preview_threshold", 1000)):
            fps = min(fps, 30)
            try:
                if hasattr(self, "_fps_spin") and self._fps_spin.value() != fps:
                    self._fps_spin.blockSignals(True)
                    self._fps_spin.setValue(fps)
                    self._fps_spin.blockSignals(False)
            except Exception:
                pass
        self._timer.setInterval(max(1, int(1000 / fps)))

    def _build_canvas_panel(self) -> QWidget:
        from .matrix_workspace_ui import build_canvas_panel
        return build_canvas_panel(self)

    def _set_layout_width_scale(self, scale: float):
        try:
            scale_f = max(0.25, min(6.0, float(scale)))
        except Exception:
            scale_f = 1.0
        try:
            if hasattr(self, "_canvas"):
                self._canvas.set_layout_width_scale(scale_f)
                zoom = float(getattr(self, "_preview_zoom_spin", None).value()) if hasattr(self, "_preview_zoom_spin") else 1.0
                base_w = 520
                new_w = max(160, int(round(base_w * scale_f * zoom)))
                self._canvas.setMinimumWidth(new_w)
                self._canvas.resize(new_w, max(self._canvas.height(), self._canvas.minimumHeight()))
        except Exception:
            pass

    def _set_layout_height_scale(self, scale: float):
        try:
            scale_f = max(0.25, min(6.0, float(scale)))
        except Exception:
            scale_f = 1.0
        try:
            if hasattr(self, "_canvas"):
                self._canvas.set_layout_height_scale(scale_f)
                zoom = float(getattr(self, "_preview_zoom_spin", None).value()) if hasattr(self, "_preview_zoom_spin") else 1.0
                base_h = 300
                new_h = max(120, int(round(base_h * scale_f * zoom)))
                self._canvas.setMinimumHeight(new_h)
                self._canvas.resize(max(self._canvas.width(), self._canvas.minimumWidth()), new_h)
        except Exception:
            pass

    def _set_preview_zoom(self, zoom: float):
        try:
            zoom_f = max(0.35, min(2.5, float(zoom)))
        except Exception:
            zoom_f = 1.0
        try:
            if hasattr(self, "_canvas"):
                self._canvas.set_preview_zoom(zoom_f)
            self._set_layout_width_scale(self._layout_width_spin.value())
            self._set_layout_height_scale(self._layout_height_spin.value())
        except Exception:
            pass

    def _auto_expand_matrix_preview(self):
        count = len(getattr(self, "_fixtures", []) or [])
        if count < 360:
            try:
                self._set_layout_width_scale(self._layout_width_spin.value())
                self._set_layout_height_scale(self._layout_height_spin.value())
            except Exception:
                pass
            return
        auto = min(6.0, max(2.25, math.sqrt(count / 90.0)))
        for spin_name, setter in (
            ("_layout_width_spin", self._set_layout_width_scale),
            ("_layout_height_spin", self._set_layout_height_scale),
        ):
            spin = getattr(self, spin_name, None)
            try:
                current = float(spin.value()) if spin is not None else 1.0
            except Exception:
                current = 1.0
            target = max(current, auto)
            try:
                if spin is not None and abs(float(spin.value()) - target) > 1e-6:
                    spin.blockSignals(True)
                    spin.setValue(target)
                    spin.blockSignals(False)
            except Exception:
                pass
            setter(target)

    def _set_topview_compact(self, compact: bool):
        """切换 TopView 紧凑高度；内容不裁剪，由滚动条浏览。"""
        try:
            #  R182：非紧凑 = 预览占主位（不限高）；紧凑 = 190 上限。
            h = 190 if compact else 16777215
            if hasattr(self, '_canvas_scroll'):
                self._canvas_scroll.setMaximumHeight(h)
                self._canvas_scroll.setMinimumHeight(150 if compact else 170)
            if hasattr(self, '_topview_compact_btn'):
                self._topview_compact_btn.setText("展开" if compact else "缩小")
        except Exception:
            pass

    @staticmethod
    def _norm6(h: str) -> str:
        """3位hex归一化为6位（"#888"→"#888888"），拼 alpha 后缀前必须调用。"""
        if isinstance(h, str) and len(h) == 4 and h.startswith("#"):
            return "#" + "".join(ch * 2 for ch in h[1:])
        return h

    @staticmethod
    def _mk_btn(text: str, bg: str, fg: str, icon_key: str = "") -> QPushButton:
        """扁平工具栏按钮：16px tabler glyph + 11px 文字（§10.5）。"""
        btn = QPushButton(text)
        btn.setFixedHeight(26)
        fn = MatrixSlideshowPanel._norm6(fg)
        btn.setStyleSheet(
            f"QPushButton{{"
            f"background:{bg};color:{fg};"
            f"border:1px solid {BG_CARD};border-radius:4px;"
            f"padding:2px 10px;font-size:11px;font-weight:normal;"
            f"}}"
            f"QPushButton:hover{{"
            f"background:#656565;border-color:{ACCENT};"
            f"}}"
            f"QPushButton:pressed{{"
            f"background:{BG_SEL};color:#FFFFFF;border-color:{ACCENT};"
            f"}}"
            f"QPushButton:checked{{"
            f"background:{BG_SEL};color:#FFFFFF;border-color:{ACCENT};"
            f"}}"
        )
        if icon_key:
            # 优先直接按语义名加载 tabler glyph（图标色随文字 fg），
            # 失败再回退 make_main_icon 的既有映射链。
            ic = _load_tabler(icon_key, 16, fn)
            if ic.isNull():
                try:
                    from ..main_icon_library import make_main_icon
                    ic = make_main_icon(icon_key, 16)
                except Exception:
                    ic = QIcon()
            if not ic.isNull():
                btn.setIcon(ic)
                btn.setIconSize(QSize(16, 16))
        return btn

    @staticmethod
    def _mk_sep() -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("background: rgba(255,255,255,21);")
        sep.setFixedWidth(1)
        return sep

    # ── 设备组加载（与旧接口兼容） ────────────────────────────────────────────

    def _get_pm_fixtures(self, pm=None) -> list:
        """从 ProjectManager/Project 可靠读取设备列表，兼容多种对象形态。"""
        src = pm or self._pm
        if src is None:
            return []
        for attr in ("get_fixtures", "list_fixtures"):
            fn = getattr(src, attr, None)
            if callable(fn):
                try:
                    val = fn() or []
                    return list(val.values()) if isinstance(val, dict) else list(val)
                except Exception:
                    pass
        for obj in (src, getattr(src, "project", None), getattr(src, "_project", None)):
            if obj is None:
                continue
            try:
                val = getattr(obj, "fixtures", None)
                if val:
                    return list(val.values()) if isinstance(val, dict) else list(val)
            except Exception:
                pass
        return []

    def _positions_from_pm(self, pm=None) -> list:
        """将 ProjectManager 中的 Fixture 对象转换为矩阵面板需要的位置字典。"""
        positions = []
        for fi in self._get_pm_fixtures(pm):
            fid = getattr(fi, "id", "")
            if not fid:
                continue
            def_id = getattr(fi, "def_id", "")
            fdef = self._get_fixture_def(pm, def_id)
            positions.append({
                "id": fid,
                "name": getattr(fi, "name", fid),
                "universe": getattr(fi, "universe", 1),
                "start_channel": getattr(fi, "start_channel", 1),
                "pos_x": getattr(fi, "pos_x", 0.0),
                "pos_y": getattr(fi, "pos_y", 0.0),
                "def_id": def_id,
                "channels": list(getattr(fdef, "channels", []) or []) if fdef is not None else [],
            })
        return positions

    def _on_skip_base_order_toggled(self, checked: bool):
        set_coordinate_order(self, checked)

    @_running_outputs.selection_change
    def load_fixture_group(self, group_id: str, fixture_ids: list,
                           group_name: str, pm, topview_positions: list):
        """接收 FixtureGroupPanel 的设备组选择"""
        # 完整原输入包括 patch/定义/模式/profile/gamma，安全复用同工程编译结果。
        _sig = fixture_group_signature(self, (group_id, group_name), fixture_ids, pm, topview_positions)
        if _sig == getattr(self, "_last_load_group_sig", None):
            return
        _running_outputs.before_selection(self, fixture_ids)
        self._last_load_group_sig = _sig
        self._current_group_id = group_id
        self._current_group_name = group_name
        self._all_topview_positions = topview_positions or []

        ordered_ids = remember_target_order(self, fixture_ids)
        pos_by_id = {str(f.get("id")): f for f in (topview_positions or []) if f.get("id")}
        #  2026-08-11 #18 跳过编号排序：只看坐标（y 主 x 次，与 matrix_pattern
        #   _slice_spatial_groups 同序）按应用方向排序，不看设备组选择编号序。
        #   sorted 稳定 → 无坐标设备保持原序；默认 False = 原逻辑零影响。
        if bool(getattr(self, "_skip_base_order", False)) and len(ordered_ids) > 1:
            if all(fid in pos_by_id for fid in ordered_ids):
                ordered_ids = sorted(
                    ordered_ids,
                    key=lambda fid: (
                        float(pos_by_id[fid].get("pos_y", 0.0) or 0.0),
                        float(pos_by_id[fid].get("pos_x", 0.0) or 0.0),
                    ),
                )
        group_fixtures = [pos_by_id[fid] for fid in ordered_ids if fid in pos_by_id]

        if not group_fixtures:
            try:
                fi_map = {getattr(fi, 'id', ''): fi for fi in self._get_pm_fixtures(pm)}
                group_fixtures = []
                for fid in ordered_ids:
                    fi = fi_map.get(fid)
                    if fi:
                        _def_id = getattr(fi, 'def_id', '')
                        _fdef = self._get_fixture_def(pm, _def_id)
                        group_fixtures.append({
                            "id": getattr(fi, 'id', fid),
                            "name": getattr(fi, 'name', fid),
                            "universe": getattr(fi, 'universe', 1),
                            "start_channel": getattr(fi, 'start_channel', 1),
                            "pos_x": getattr(fi, 'pos_x', 0.0),
                            "pos_y": getattr(fi, 'pos_y', 0.0),
                            "def_id": _def_id,
                            "channels": list(getattr(_fdef, 'channels', []) or []) if _fdef is not None else [],
                        })
            except Exception:
                pass

        # 补全 def_id / universe / start_channel
        if group_fixtures and pm is not None:
            try:
                fi_map = {getattr(fi, 'id', ''): fi for fi in self._get_pm_fixtures(pm)}
                enriched = []
                for f in group_fixtures:
                    nf = dict(f)
                    fi = fi_map.get(f.get("id", ""))
                    if fi is not None:
                        _def_id = getattr(fi, 'def_id', '')
                        nf.setdefault("def_id", _def_id)
                        nf.setdefault("universe", getattr(fi, 'universe', 1))
                        nf.setdefault("start_channel", getattr(fi, 'start_channel', 1))
                        if not nf.get("channels"):
                            _fdef = self._get_fixture_def(pm, _def_id)
                            nf["channels"] = list(getattr(_fdef, "channels", []) or []) if _fdef is not None else []
                    enriched.append(nf)
                group_fixtures = enriched
            except Exception:
                pass

        # 归一化坐标
        if group_fixtures:
            xs = [f.get("pos_x", 0.0) for f in group_fixtures]
            ys = [f.get("pos_y", 0.0) for f in group_fixtures]
            x_min, x_max = min(xs), max(xs)
            y_min, y_max = min(ys), max(ys)
            x_range = max(x_max - x_min, 1e-6)
            y_range = max(y_max - y_min, 1e-6)
            normalized = []
            grid_fallback = x_range <= 1e-5 or y_range <= 1e-5
            grid_cols = max(1, int(math.ceil(math.sqrt(len(group_fixtures)))))
            grid_rows = max(1, int(math.ceil(len(group_fixtures) / grid_cols)))
            for i, f in enumerate(group_fixtures):
                nf = dict(f)
                if grid_fallback:
                    col = i % grid_cols
                    row = i // grid_cols
                    nf["pos_x"] = col / max(1, grid_cols - 1)
                    nf["pos_y"] = row / max(1, grid_rows - 1)
                else:
                    nf["pos_x"] = (f.get("pos_x", 0.0) - x_min) / x_range
                    nf["pos_y"] = (f.get("pos_y", 0.0) - y_min) / y_range
                normalized.append(nf)
            self._fixtures = normalized
        else:
            self._fixtures = []

        # 更新引擎
        self._rebuild_channel_addr_cache()
        self._engine.set_fixtures(self._fixtures)
        self._pixel_engine.set_fixtures(self._fixtures)

        _running_outputs.after_selection(self)
        # 更新 UI
        self._group_lbl.setText(group_name or "未命名组")
        self._dev_lbl.setText(f"设备: {len(self._fixtures)}")
        self._canvas.set_fixtures(self._fixtures)
        self._auto_expand_matrix_preview()
        self._sync_slides_to_current_fixtures(); self._rebuild_thumbs()
        if self._engine.slides:
            _running_outputs.show_selected_frame(self)
        self._emit_fixture_selection_to_topview(ordered_ids)

    def _emit_fixture_selection_to_topview(self, ids: list) -> None:
        """把本次加载的设备集回传（MainWindow 收到后写 TopView → 调度台）。

         2026-09-14 用户要求"其它页面选中的设备应该也先传给 topview 再传给调度台"。
          守卫与 Programmer 一致：由 TopView 反向驱动的加载（`_suppress_...`）不回传，
          否则会"TopView → 面板 → TopView"绕圈。
        """
        if bool(getattr(self, "_suppress_topview_selection_emit", False)):
            return
        try:
            self.fixture_selection_changed.emit([str(x) for x in (ids or [])])
        except Exception:
            logger.debug("matrix: emit fixture_selection_changed failed", exc_info=True)

    def get_live_selected_fixture_ids(self) -> list:
        """当前面板正在编辑的设备 id（供 MainWindow 首次同步到 TopView）。"""
        try:
            return [str(f.get("id")) for f in (self._fixtures or []) if f.get("id")]
        except Exception:
            return []

    def set_fixtures(self, topview_positions: list):
        """从 TopView 接收设备位置列表。

        若用户尚未点过设备组（_current_group_id 为空），则把当前 TopView
        位置当作 "__all__" 组立即加载，让面板"打开就能看到内容"。

         修复历史：原实现只缓存到 _all_topview_positions，**不**调用
          load_fixture_group，画布永远空白；只有用户去 FixtureGroupPanel
          手动点一下某组才会触发渲染。
        """
        self._all_topview_positions = topview_positions or []
        if not self._current_group_id and topview_positions:
            all_ids = [f.get("id") for f in topview_positions if f.get("id")]
            if all_ids:
                try:
                    self.load_fixture_group(
                        "__all__", all_ids, "全部设备",
                        self._pm, topview_positions,
                    )
                except Exception:
                    import logging
                    logging.getLogger(__name__).debug(
                        "MatrixSlideshowPanel set_fixtures default-all failed",
                        exc_info=True)

    def _bootstrap_from_pm(self):
        """从 ProjectManager 直接拉取 fixtures 自填充画布。

        当用户先打开 Matrix 面板、TopView 尚未懒加载时，set_fixtures 永远
        不会被外部调用。这里在 __init__ 末尾兜底——直接用 fi 自带的
        pos_x/pos_y 把所有设备渲染上去；之后 TopView 加载后会通过
        refresh_from_topview 用更精确的坐标覆盖。
        """
        if self._fixtures:  # 已经有内容了，跳过
            return
        if self._pm is None:
            return
        # 把 Fixture 对象转换成 panel 期望的 dict 格式
        positions: List[Dict] = self._positions_from_pm(self._pm)
        all_ids: List[str] = [f.get("id") for f in positions if f.get("id")]
        if not all_ids:
            return
        try:
            self.load_fixture_group(
                "__all__", all_ids, "全部设备",
                self._pm, positions,
            )
        except Exception:
            import logging
            logging.getLogger(__name__).debug(
                "MatrixSlideshowPanel bootstrap failed", exc_info=True)

    def refresh_from_topview(self, topview_positions: list):
        """TopView 布局变化时刷新坐标。

         修复：原实现只更新 _all_topview_positions 缓存，**不刷新画布**——
          甚至有 `pass  # 下次 load_fixture_group 时会自动刷新` 这种自欺
          欺欺人的注释。现在：
            - 若当前是 "__all__" 组，用新坐标重新加载（含全设备同步）
            - 若当前是某具体组，用新坐标重新加载该组（保持选择不变）
        """
        self._all_topview_positions = topview_positions or []
        if not topview_positions:
            return
        if self._current_group_id == "__all__":
            all_ids = [f.get("id") for f in topview_positions if f.get("id")]
            if all_ids:
                try:
                    self.load_fixture_group(
                        "__all__", all_ids, "全部设备",
                        self._pm, topview_positions,
                    )
                except Exception:
                    pass
        elif self._current_group_id:
            # 保留具体组选择，仅刷新坐标——重用当前 fixture_ids
            try:
                fids = base_order_for_targets(self)
                if fids:
                    self.load_fixture_group(
                        self._current_group_id, fids,
                        self._current_group_name,
                        self._pm, topview_positions,
                    )
            except Exception:
                pass

    def _on_project_changed_for_matrix(self):
        """工程或设备组导入变化后刷新矩阵面板的设备与设备组下拉框。

         修复：无论是否有 TopView 全量坐标缓存，都强制重建设备列表。
        设备配接后应立即识别，不应要求先拖入 TopView。

         2026-09-21（数据串台）：这里同时是**换工程**的唯一公共入口
          （`project_changed` 在新工程对象就绪后发出），故顺带做"工程身份变化 → 清台"。
          判据是 `created_at + name` 指纹，普通配接改动（同一工程）不会误清。
        """
        from .matrix_stage_helpers import refresh_project_fixture_scope
        refresh_project_fixture_scope(self, _restore_panel_mode)
        self.refresh_filter_group_combo()
        self._populate_ch_type_combo()

    # ── 设备组过滤 ──────────────────────────────────────────────────────────────────────────

    def _get_fixture_groups_list(self) -> list:
        """从 ProjectManager 读取 fixture_groups，兼容多种存储位置。"""
        pm = self._pm
        if pm is None:
            return []
        groups = getattr(pm, 'fixture_groups', None)
        if groups is None:
            groups = getattr(pm, '_fixture_groups', None)
        if groups is None:
            proj = getattr(pm, 'project', None) or getattr(pm, '_project', None)
            if proj is not None:
                groups = getattr(proj, 'fixture_groups', None)
        return list(groups) if groups else []

    def _append_fd_channels_for_matrix(self, combo, fd, seen) -> None:
        """把一个设备定义的通道按工程真名追加进通道下拉（精调/占位跳过）。"""
        append_definition_channels(combo, fd, seen)

    def _project_for_matrix(self):
        """当前工程对象（PM 的 project/_project 两代口径都试）。"""
        pm = getattr(self, "_pm", None)
        if pm is None:
            return None
        return getattr(pm, "project", None) or getattr(pm, "_project", None)

    def _populate_ch_type_combo(self):
        """工程真名、类型身份与当前选择适用状态；刷新保留稳定偏好。"""
        populate_channel_choices(self)

    def refresh_filter_group_combo(self):
        """刷新设备组过滤下拉框（项目加载或设备组变化时调用）。"""
        combo = getattr(self, '_filter_group_combo', None)
        if combo is None:
            return
        prev_id = self._filter_group_id
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("全部设备", None)
        restore_idx = 0
        for i, gd in enumerate(self._get_fixture_groups_list()):
            gid = gd.get('id', '') if isinstance(gd, dict) else getattr(gd, 'id', '')
            name = gd.get('name', 'Group') if isinstance(gd, dict) else getattr(gd, 'name', 'Group')
            fids = gd.get('fixture_ids', []) if isinstance(gd, dict) else getattr(gd, 'fixture_ids', [])
            combo.addItem(f"{name}（{len(fids)}台）", gid)
            if gid == prev_id:
                restore_idx = i + 1
        combo.setCurrentIndex(restore_idx)
        combo.blockSignals(False)
        #  R193/P0-1（MX-04）：刷新路径不再自动重载设备组——工程刷新/配接后
        #   以 refresh_project_fixture_scope 恢复的精确 scope 为准；曾选过的组
        #   名仅作下拉显示恢复。用户意图只经显式下拉操作生效。
        #   旧逻辑在此按恢复索引自动重载，会把精确子集静默扩成整组并回写
        #   TopView。

    def _on_filter_group_changed(self, index: int):
        """用户切换设备组过滤下拉框。"""
        self._apply_filter_group(index)

    def _apply_filter_group(self, index: int):
        """根据选中的设备组，调用 load_fixture_group 将对应组的设备赋给 self._fixtures。"""
        combo = getattr(self, '_filter_group_combo', None)
        if combo is None:
            return
        gid = combo.itemData(index)
        self._filter_group_id = gid
        if gid is None:
            # 选择“全部设备”：优先使用 TopView 全量位置；没有 TopView 时直接从 ProjectManager 重建，避免沿用当前组子集。
            topview = self._all_topview_positions or self._positions_from_pm(self._pm)
            all_ids = [f.get("id") for f in topview if f.get("id")]
            if all_ids:
                self.load_fixture_group("__all__", all_ids, "全部设备", self._pm, list(topview))
            else:
                self.set_fixtures(list(topview))
        else:
            for gd in self._get_fixture_groups_list():
                g_id = gd.get('id', '') if isinstance(gd, dict) else getattr(gd, 'id', '')
                if g_id == gid:
                    name = gd.get('name', '') if isinstance(gd, dict) else getattr(gd, 'name', '')
                    fids = list(gd.get('fixture_ids', []) if isinstance(gd, dict)
                                else getattr(gd, 'fixture_ids', []))
                    base = (gd.get('base_order_fixture_ids', []) if isinstance(gd, dict)
                            else getattr(gd, 'base_order_fixture_ids', []))
                    topview = self._all_topview_positions or self._positions_from_pm(self._pm) or self._fixtures
                    self.load_fixture_group(gid, ordered_members(fids, base), name, self._pm, list(topview))
                    break

    def _rebuild_channel_addr_cache(self):
        cache: Dict[str, List[tuple]] = {}
        fixture_cache: Dict[str, Dict[str, List[tuple]]] = {}
        fixture_order: Dict[str, int] = {}
        for f in self._fixtures:
            fid = f.get("id", "")
            if not fid:
                continue
            fixture_order[str(fid)] = len(fixture_order)
            universe = int(f.get("universe", 1) or 1)
            start_ch = int(f.get("start_channel", 1) or 1)
            channels = list(f.get("channels", []) or [])
            if not channels:
                fi_def = self._get_fixture_def(self._pm, f.get("def_id", ""))
                channels = list(getattr(fi_def, "channels", []) or []) if fi_def is not None else []
            has_light_channels = any(
                self._normalize_ch_type(ch) in ("r", "red", "g", "green", "b", "blue", "w", "white")
                for ch in channels
            )
            fmap: Dict[str, List[tuple]] = {}
            for offset, ch in enumerate(channels):
                ch_norm = self._normalize_ch_type(ch)
                addr = start_ch + offset - 1
                if ch_norm and 0 <= addr < 512:
                    cache.setdefault(ch_norm, []).append((fid, universe, addr))
                    fmap.setdefault(ch_norm, []).append((universe, addr))
                    if ch_norm in ("r", "red"):
                        fmap.setdefault("r", []).append((universe, addr))
                    elif ch_norm in ("g", "green"):
                        fmap.setdefault("g", []).append((universe, addr))
                    elif ch_norm in ("b", "blue"):
                        fmap.setdefault("b", []).append((universe, addr))
                    elif ch_norm in ("w", "white"):
                        fmap.setdefault("w", []).append((universe, addr))
            if any(fmap.get(ct) for ct in ("r", "g", "b", "w")):
                fmap.setdefault("dimmer", [])
            for key, entries in list(fmap.items()):
                if len(entries) > 1:
                    fmap[key] = list(dict.fromkeys(entries))
            fixture_cache[fid] = fmap
        self._channel_addr_cache = cache
        self._fixture_addr_cache = fixture_cache
        vector_maps: Dict[str, Dict[str, np.ndarray]] = {}
        for ch_type, entries in cache.items():
            idxs = []
            universes = []
            addrs = []
            for fid, uni, addr in entries:
                if fid in fixture_order:
                    idxs.append(int(fixture_order[fid]))
                    universes.append(int(uni))
                    addrs.append(int(addr))
            if idxs:
                uni_arr = np.asarray(universes, dtype=np.int32)
                addr_arr = np.asarray(addrs, dtype=np.int32)
                universe_groups = []
                for uni in np.unique(uni_arr):
                    group_idx = np.flatnonzero(uni_arr == int(uni)).astype(np.int32, copy=False)
                    universe_groups.append((int(uni), group_idx, addr_arr[group_idx]))
                vector_maps[str(ch_type).lower()] = {
                    "fixture_idx": np.asarray(idxs, dtype=np.int32),
                    "universe": uni_arr,
                    "addr": addr_arr,
                    "universe_groups": universe_groups,
                    "last_values": np.full(len(idxs), 256, dtype=np.uint16),
                }
        dimmer_idxs = []
        dimmer_universes = []
        dimmer_addrs = []
        dimmer_base = []
        dimmer_dynamic_base = []
        for fid, fmap in fixture_cache.items():
            if fid not in fixture_order:
                continue
            if fmap.get("dimmer"):
                continue
            has_rgb = any(fmap.get(ct) for ct in ("r", "g", "b"))
            has_w = bool(fmap.get("w"))
            if not has_rgb and not has_w:
                continue
            for ct in ("r", "g", "b", "w"):
                entries = fmap.get(ct, []) or []
                if not entries:
                    continue
                for uni, addr in entries:
                    dimmer_idxs.append(int(fixture_order[fid]))
                    dimmer_universes.append(int(uni))
                    dimmer_addrs.append(int(addr))
                    dimmer_base.append(0 if (ct == "w" and has_rgb) else 255)
                    if ct == "w" and has_rgb:
                        dimmer_dynamic_base.append(-1)
                    else:
                        dimmer_dynamic_base.append({"r": 0, "g": 1, "b": 2}.get(ct, 3))
        if dimmer_idxs:
            existing = vector_maps.get("dimmer")
            if existing:
                existing_len = len(existing.get("fixture_idx", []))
                idx_arr = np.concatenate((
                    np.asarray(existing.get("fixture_idx", []), dtype=np.int32),
                    np.asarray(dimmer_idxs, dtype=np.int32),
                ))
                uni_arr = np.concatenate((
                    np.asarray(existing.get("universe", []), dtype=np.int32),
                    np.asarray(dimmer_universes, dtype=np.int32),
                ))
                addr_arr = np.concatenate((
                    np.asarray(existing.get("addr", []), dtype=np.int32),
                    np.asarray(dimmer_addrs, dtype=np.int32),
                ))
                existing_base = existing.get("base_values")
                if existing_base is None:
                    existing_base = np.full(existing_len, 255, dtype=np.uint16)
                base_arr = np.concatenate((
                    np.asarray(existing_base, dtype=np.uint16),
                    np.asarray(dimmer_base, dtype=np.uint16),
                ))
                existing_dynamic = existing.get("color_channel")
                if existing_dynamic is None:
                    existing_dynamic = np.full(existing_len, -1, dtype=np.int16)
                dynamic_arr = np.concatenate((
                    np.asarray(existing_dynamic, dtype=np.int16),
                    np.asarray(dimmer_dynamic_base, dtype=np.int16),
                ))
            else:
                idx_arr = np.asarray(dimmer_idxs, dtype=np.int32)
                uni_arr = np.asarray(dimmer_universes, dtype=np.int32)
                addr_arr = np.asarray(dimmer_addrs, dtype=np.int32)
                base_arr = np.asarray(dimmer_base, dtype=np.uint16)
                dynamic_arr = np.asarray(dimmer_dynamic_base, dtype=np.int16)
            universe_groups = []
            for uni in np.unique(uni_arr):
                group_idx = np.flatnonzero(uni_arr == int(uni)).astype(np.int32, copy=False)
                universe_groups.append((int(uni), group_idx, addr_arr[group_idx]))
            vector_maps["dimmer"] = {
                "fixture_idx": idx_arr,
                "universe": uni_arr,
                "addr": addr_arr,
                "base_values": base_arr,
                "color_channel": dynamic_arr,
                "universe_groups": universe_groups,
                "last_values": np.full(len(idx_arr), 256, dtype=np.uint16),
                "virtual_dimmer": True,
            }
        self._matrix_fixture_order = fixture_order
        self._matrix_fixture_reverse_order = None
        self._matrix_vector_maps = install_panel_maps(self, vector_maps)
        rebuild_output_scope(self, finish_channel_rebuild)
        try:
            self._on_matrix_fps_changed(self._fps_spin.value())
        except Exception:
            pass

    # ── NM 注入 ──────────────────────────────────────────────────────────────────────────

    def set_nm(self, nm):
        self._nm_ref = nm
        self._engine.set_nm(nm)

    def _reset_matrix_output_state(self, clear_pattern: bool = False):
        self._matrix_output_cache = {}
        self._matrix_pattern_pending = {}
        self._last_matrix_full_output_emit = 0.0
        self._force_matrix_full_output = True
        self._matrix_ltp_takeover_pending = True
        self._last_matrix_output_layer = None
        if clear_pattern:
            self._matrix_pattern_cache = {}
        for direct_map in (getattr(self, "_matrix_vector_maps", {}) or {}).values():
            try:
                n = len(direct_map.get("fixture_idx", []))
                direct_map["last_values"] = np.full(n, 256, dtype=np.uint16)
                direct_map["last_out"] = np.full(n, 256, dtype=np.uint16)  # R194/P2-15：槽位镜像同复位
            except Exception:
                # R181 P2-3：脏值跟踪缓存复位失败 → 复位后可能漏发全量输出
                logger.debug("复位矩阵脏值跟踪缓存失败（可能漏发全量输出）", exc_info=True)

    def _matrix_output_layer(self) -> str:
        try:
            if self._mode == "pixel" and hasattr(self._pixel_engine, "is_audio_reactive_active"):
                if self._pixel_engine.is_audio_reactive_active():
                    return "matrix_audio"
        except Exception:
            pass
        return "matrix"

    def _matrix_layer_cache(self, layer_name: Optional[str] = None) -> Dict[int, Dict[int, int]]:
        layer_name = str(layer_name or self._matrix_output_layer())
        cache = getattr(self, "_matrix_output_cache", {})
        if not isinstance(cache, dict):
            self._matrix_output_cache = {}
            cache = self._matrix_output_cache
        return cache.setdefault(layer_name, {})

    def _clear_matrix_output_layers(self):
        nm = getattr(self, "_nm_ref", None)
        if nm is not None and hasattr(nm, "clear_layer"):
            for layer_name in ("matrix", "matrix_audio"):
                try:
                    _running_outputs.clear_current(self, (layer_name,))
                except Exception:
                    # R181 P2-3：矩阵层清不掉 = 关闭/停止后残留输出（灯不灭），留痕
                    logger.debug("清空矩阵输出层失败 layer=%s（可能残留输出）",
                                 layer_name, exc_info=True)
        self._matrix_output_cache = {}
        for direct_map in (getattr(self, "_matrix_vector_maps", {}) or {}).values():
            try:
                n = len(direct_map.get("fixture_idx", []))
                direct_map["last_values"] = np.full(n, 256, dtype=np.uint16)
                direct_map["last_out"] = np.full(n, 256, dtype=np.uint16)  # R194/P2-15：槽位镜像同复位
            except Exception:
                # R181 P2-3：脏值跟踪缓存复位失败 → 清层后可能漏发全量输出
                logger.debug("复位矩阵脏值跟踪缓存失败（可能漏发全量输出）", exc_info=True)
        self._force_matrix_full_output = True

    def _clear_inactive_matrix_layer(self, active_layer: str, force: bool = False):
        previous = getattr(self, "_last_matrix_output_layer", None)
        self._last_matrix_output_layer = str(active_layer)
        if not force and previous in (None, active_layer):
            return
        inactive = "matrix_audio" if active_layer == "matrix" else "matrix"
        nm = getattr(self, "_nm_ref", None)
        if nm is not None and hasattr(nm, "clear_layer"):
            try:
                _running_outputs.clear_current(self, (inactive,))
            except Exception:
                # R181 P2-3：非活动层清不掉 → 两种矩阵层可能同时输出（画面叠加）
                logger.debug("清空非活动矩阵层失败 layer=%s（可能双层叠加输出）",
                             inactive, exc_info=True)
        try:
            self._matrix_output_cache.pop(inactive, None)
        except Exception:
            pass
        _reset_slot_mirrors(self)  # R194/P2-15：层切换 → 槽位镜像全量失效（换入层需整帧重发）

    def _flush_matrix_output(self, universes: Optional[Set[int]] = None):
        """Ask NetworkManager to recompute universes touched by this panel."""
        nm = self._nm_ref
        if nm is None:
            return
        try:
            target_universes = set(universes or [])
            if not target_universes:
                for entries in getattr(self, "_channel_addr_cache", {}).values():
                    for _fid, uni, _addr in entries:
                        target_universes.add(int(uni))
            for uni in target_universes:
                try:
                    if hasattr(nm, "_mark_dirty_universe"):
                        nm._mark_dirty_universe(int(uni))
                except Exception:
                    logger.debug("标记脏域失败 uni=%s", uni, exc_info=True)  # XS-05
        except Exception:
            pass

    def _matrix_base_color(self, fid: str, fmap: Dict[str, List[tuple]]) -> Dict[str, int]:
        color = getattr(self._pixel_engine, "matrix_effect_color", (255, 255, 255))
        try:
            r, g, b = (max(0, min(255, int(v))) for v in color[:3])
        except Exception:
            r, g, b = 255, 255, 255
        white = int(round(0.2126 * r + 0.7152 * g + 0.0722 * b))
        has_rgb = any(fmap.get(ct) for ct in ("r", "g", "b"))
        return {
            "r": r if fmap.get("r") else 0,
            "g": g if fmap.get("g") else 0,
            "b": b if fmap.get("b") else 0,
            "w": (0 if has_rgb else white) if fmap.get("w") else 0,
        }

    def _matrix_dynamic_base_values(self, direct_map: Dict[str, np.ndarray]) -> Optional[np.ndarray]:
        color_channel = np.asarray(direct_map.get("color_channel", []), dtype=np.int16)
        if color_channel.size == 0 or not np.any(color_channel >= 0):
            return None
        base_values = np.asarray(direct_map.get("base_values", np.zeros(color_channel.size, dtype=np.uint16)),
                                 dtype=np.uint16).copy()
        if base_values.size != color_channel.size:
            base_values = np.resize(base_values, color_channel.size).astype(np.uint16, copy=False)
        color = getattr(self._pixel_engine, "matrix_effect_color", (255, 255, 255))
        try:
            r, g, b = (max(0, min(255, int(v))) for v in color[:3])
        except Exception:
            r, g, b = 255, 255, 255
        white = int(round(0.2126 * r + 0.7152 * g + 0.0722 * b))
        channel_values = np.asarray([r, g, b, white], dtype=np.uint16)
        mask = (color_channel >= 0) & (color_channel < 4)
        if np.any(mask):
            base_values[mask] = channel_values[color_channel[mask]]
        return base_values

    def _matrix_fixture_output(self, fid: str, value: int, ch_type: str) -> List[tuple]:
        return fixture_output(self, fid, value, ch_type)

    def _output_pixel_states_vectorized(
        self,
        states: Dict[str, bool],
        target: str,
        ch_type: str,
    ) -> Optional[tuple[Dict[int, Dict[int, int]], Set[int], Dict[str, Dict[str, int]]]]:
        direct_map = getattr(self, "_matrix_vector_maps", {}).get(target)
        if not direct_map:
            return None

        fixture_order = getattr(self, "_matrix_fixture_order", {}) or {}
        if not fixture_order:
            return None
        state_vals = np.zeros(len(fixture_order), dtype=np.uint8)
        changed_state: Dict[str, int] = {}
        for fid, on in states.items():
            idx = fixture_order.get(str(fid))
            if idx is None:
                continue
            value = _state_value(on)          #  灰度帧：非 bool 值原样（int 0-255）
            state_vals[int(idx)] = value
            changed_state[str(fid)] = value
        if not changed_state:
            return {}, set(), {}

        idxs = direct_map["fixture_idx"]
        universes = direct_map["universe"]
        addrs = direct_map["addr"]
        present_mask = np.zeros(len(fixture_order), dtype=bool)
        for fid in changed_state:
            idx = fixture_order.get(fid)
            if idx is not None:
                present_mask[int(idx)] = True
        entry_mask = present_mask[idxs]
        if not np.any(entry_mask):
            return {}, set(), {}
        slots = np.flatnonzero(entry_mask)  # R194/P2-15：direct_map 槽位（last_out 对齐）
        idxs = idxs[entry_mask]
        universes = universes[entry_mask]
        addrs = addrs[entry_mask]
        values = profile_map_values(state_vals[idxs], direct_map, entry_mask)
        base_values = direct_map.get("base_values")
        if base_values is not None:
            dynamic_base = self._matrix_dynamic_base_values(direct_map)
            if dynamic_base is not None:
                base_values = dynamic_base
            base_values = np.asarray(base_values, dtype=np.uint16)[entry_mask]
            values = ((values * base_values + 127) // 255).astype(np.uint8, copy=False)
        values = encode_map_values(values, direct_map, entry_mask)
        uni_data: Dict[int, Dict[int, int]] = {}
        touched_universes: Set[int] = set()
        pattern_payload: Dict[str, Dict[str, int]] = {}

        # R194/P2-15：脏检查向量化（原为逐项 output_cache.get Python 循环）。
        #   last_out 槽位镜像语义 = "本槽位上次实发值"，256 哨兵 = 未发过；
        #   复位挂在 _reset_matrix_output_state/_clear_matrix_output_layers。
        changed_mask = values.astype(np.uint16, copy=False) != _ensure_last_out(direct_map)[slots]
        if not np.any(changed_mask):
            return {}, set(), {}

        for uni, addr, val in zip(universes[changed_mask], addrs[changed_mask], values[changed_mask]):
            uni_i = int(uni)
            addr_i = int(addr)
            val_i = int(val)
            uni_data.setdefault(uni_i, {})[addr_i] = val_i
            touched_universes.add(uni_i)
        _mirror_last_out(direct_map, slots, changed_mask, values)  #  P2-15：写回后统一更新镜像

        changed_fixture_idxs = set(int(i) for i in idxs[changed_mask])
        reverse_order = self.__dict__.get("_matrix_fixture_reverse_order")
        if reverse_order is None or len(reverse_order) != len(fixture_order):
            reverse_order = [None] * len(fixture_order)
            for fid, idx in fixture_order.items():
                if 0 <= int(idx) < len(reverse_order):
                    reverse_order[int(idx)] = fid
            self._matrix_fixture_reverse_order = reverse_order
        for idx in changed_fixture_idxs:
            if 0 <= idx < len(reverse_order):
                fid = reverse_order[idx]
                if fid is not None and fid in changed_state:
                    pattern_payload.setdefault(fid, {})[str(ch_type or target)] = int(changed_state[fid])
        return uni_data, touched_universes, pattern_payload

    def _output_pixel_values_vectorized(
        self,
        values: np.ndarray,
        target: str,
        ch_type: str,
        emit_pattern: bool = True,
    ) -> Optional[tuple[Dict[int, Dict[int, int]], Set[int], Dict[str, Dict[str, int]]]]:
        direct_map = getattr(self, "_matrix_vector_maps", {}).get(target)
        if not direct_map:
            return None
        fixture_order = getattr(self, "_matrix_fixture_order", {}) or {}
        if not fixture_order:
            return None
        vals = np.asarray(values, dtype=np.uint8)
        if vals.size < len(fixture_order):
            return None
        vals = vals[:len(fixture_order)]

        idxs = direct_map["fixture_idx"]
        universes = direct_map["universe"]
        addrs = direct_map["addr"]
        mapped_values = profile_map_values(vals[idxs], direct_map)
        base_values = direct_map.get("base_values")
        if base_values is not None:
            dynamic_base = self._matrix_dynamic_base_values(direct_map)
            if dynamic_base is not None:
                base_values = dynamic_base
            base_values = np.asarray(base_values, dtype=np.uint16)
            mapped_values = ((mapped_values * base_values + 127) // 255).astype(np.uint8, copy=False)
        mapped_values = encode_map_values(mapped_values, direct_map)
        if mapped_values.size == 0:
            return {}, set(), {}

        # R194/P2-15：脏检查向量化（原为逐项 output_cache.get Python 循环）。
        #   本函数无 entry_mask 过滤 → 槽位与 fixture_idx 直接对齐（slots=None）。
        changed_mask = mapped_values.astype(np.uint16, copy=False) != _ensure_last_out(direct_map)
        if not np.any(changed_mask):
            return {}, set(), {}

        uni_data: Dict[int, Dict[int, int]] = {}
        touched_universes: Set[int] = set()
        for uni, addr, val in zip(universes[changed_mask], addrs[changed_mask], mapped_values[changed_mask]):
            uni_i = int(uni)
            addr_i = int(addr)
            val_i = int(val)
            uni_data.setdefault(uni_i, {})[addr_i] = val_i
            touched_universes.add(uni_i)
        _mirror_last_out(direct_map, None, changed_mask, mapped_values)  #  P2-15：写回后统一更新镜像

        pattern_payload: Dict[str, Dict[str, int]] = {}
        if emit_pattern:
            changed_fixture_idxs = set(int(i) for i in idxs[changed_mask])
            reverse_order = self.__dict__.get("_matrix_fixture_reverse_order")
            if reverse_order is None or len(reverse_order) != len(fixture_order):
                reverse_order = [None] * len(fixture_order)
                for fid, idx in fixture_order.items():
                    if 0 <= int(idx) < len(reverse_order):
                        reverse_order[int(idx)] = fid
                self._matrix_fixture_reverse_order = reverse_order
            for idx in changed_fixture_idxs:
                if 0 <= idx < len(reverse_order):
                    fid = reverse_order[idx]
                    if fid is not None:
                        pattern_payload.setdefault(fid, {})[str(ch_type or target)] = int(vals[idx])
        return uni_data, touched_universes, pattern_payload

    def _output_pixel_values_arrays(
        self,
        values: np.ndarray,
        target: str,
        force: bool = False,
    ) -> Optional[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list]]:
        direct_map = getattr(self, "_matrix_vector_maps", {}).get(target)
        fixture_order = getattr(self, "_matrix_fixture_order", {}) or {}
        if not direct_map or not fixture_order:
            return None
        vals = np.asarray(values, dtype=np.uint8)
        if vals.size < len(fixture_order):
            return None
        idxs = direct_map["fixture_idx"]
        universes = direct_map["universe"]
        addrs = direct_map["addr"]
        mapped_values = profile_map_values(vals[:len(fixture_order)][idxs], direct_map)
        base_values = direct_map.get("base_values")
        if base_values is not None:
            dynamic_base = self._matrix_dynamic_base_values(direct_map)
            if dynamic_base is not None:
                base_values = dynamic_base
            base_values = np.asarray(base_values, dtype=np.uint16)
            mapped_values = ((mapped_values * base_values + 127) // 255).astype(np.uint8, copy=False)
        mapped_values = encode_map_values(mapped_values, direct_map)
        if mapped_values.size == 0:
            empty = np.zeros(0, dtype=np.int32)
            return empty, empty, np.zeros(0, dtype=np.uint8), empty, []

        last_values = direct_map.get("last_values")
        if last_values is None or len(last_values) != mapped_values.size:
            last_values = np.full(mapped_values.size, 256, dtype=np.uint16)
            direct_map["last_values"] = last_values
        if force:
            changed_mask = np.ones(mapped_values.shape, dtype=bool)
        else:
            changed_mask = last_values != mapped_values.astype(np.uint16, copy=False)
        if not np.any(changed_mask):
            empty = np.zeros(0, dtype=np.int32)
            return empty, empty, np.zeros(0, dtype=np.uint8), empty, []
        last_values[changed_mask] = mapped_values[changed_mask].astype(np.uint16, copy=False)
        grouped = []
        universe_groups = direct_map.get("universe_groups") or []
        if universe_groups:
            for uni, group_idx, group_addrs in universe_groups:
                local_mask = changed_mask[group_idx]
                if np.any(local_mask):
                    grouped.append((
                        int(uni),
                        group_addrs[local_mask].astype(np.int32, copy=False),
                        mapped_values[group_idx][local_mask].astype(np.uint8, copy=False),
                    ))
        return (
            universes[changed_mask].astype(np.int32, copy=False),
            addrs[changed_mask].astype(np.int32, copy=False),
            mapped_values[changed_mask].astype(np.uint8, copy=False),
            idxs[changed_mask].astype(np.int32, copy=False),
            grouped,
        )

    # ── v25: 项目保存/加载接口 ────────────────────────────────────────────────
    def save_state(self) -> dict:
        """保存面板完整状态到项目文件（含幻灯片帧序列）。"""
        state = {}
        # 当前模式
        state['mode'] = self._mode
        state['slice_key'] = self._slice_key
        state['slice_key_modifiers'] = int(getattr(self, '_slice_key_modifiers', 0) or 0)
        state['manual_pixel_slice_index'] = int(getattr(self, '_manual_pixel_slice_index', 0) or 0)
        state['ppt_density'] = float(getattr(self, '_ppt_density_spin', None) and self._ppt_density_spin.value() or 0.35)
        state['wing_direction'] = normalize_wing_direction(getattr(self, '_wing_direction', WING_DIRECTION_LOW_TO_HIGH))
        state.update(_wing_flags.controls.values(self, '_'))
        #  2026-08-12 持久化缺口修复: 「跳过编号排序」此前纯内存状态, 保存重开丢失 (#18 统一审查)
        state['skip_base_order'] = bool(getattr(self, '_skip_base_order', False))
        # 当前设备组
        state['group_id'] = getattr(self, '_current_group_id', '')
        _save_group_scope(self, state)
        #  幻灯片引擎完整状态（帧序列 + 配置）
        if self._engine is not None:
            _sync_video_ext_to_config(self)   #  R183：视频扩展字段先回写 config 再落盘
            state['slideshow_engine'] = self._engine.to_dict()
        # 像素效果参数
        if self._pixel_engine:
            state['pixel_params'] = _collect_pixel_params(self._pixel_engine, self)
        # 通道类型
        state['ch_type'] = getattr(self, '_ch_type', 'pump')
        _save_ppt_composition_state(self, state)
        return _running_outputs.save_others(self, state)

    def load_state(self, state: dict):
        """从项目文件恢复面板状态（含幻灯片帧序列）。

         2026-09-21 用户实报（数据串台）：`state` 为空时**必须清台**，否则引擎里
          还留着上一个工程的帧序列 —— 打开工程 A 做几帧 → 打开/新建工程 B（B 没有
          `matrix_slideshow_state`）→ Matrix 页里仍是 A 的帧序列，在 B 里一保存就
          写进 B。原实现 `if not state: return` 是"静默保留旧内容"的来源。
        """
        _restore_ppt_composition_state(self, state)
        if not state or not isinstance(state, dict):
            _clear_matrix_stage(self, _restore_panel_mode)
            return
        _restore_panel_mode(self, state)   #  原 load 从不读 state['mode']
        _restore_group(self, state)        #  原 save 写了 group_id/name 但从不读
        #  恢复幻灯片引擎状态（帧序列 + 配置）
        engine_data = state.get('slideshow_engine')
        if engine_data and isinstance(engine_data, dict) and self._engine is not None:
            self._engine.from_dict(engine_data)
            # 刷新缩略图列表
            try:
                self._rebuild_thumbs()
            except Exception:
                pass
        # 恢复通道类型
        restore_channel_type(self, state.get('ch_type', ''))
        try:
            key = state.get('slice_key', None)
            self._slice_key = int(key) if key is not None else None
            self._slice_key_modifiers = int(state.get('slice_key_modifiers', 0) or 0)
            self._manual_pixel_slice_index = int(state.get('manual_pixel_slice_index', 0) or 0)
            if hasattr(self, '_ppt_density_spin'):
                self._ppt_density_spin.setValue(float(state.get('ppt_density', self._ppt_density_spin.value())))
            _wing_flags.controls.restore(self, state, '_')
            self._wing_direction = normalize_wing_direction(state.get('wing_direction', self._wing_direction))
            self._sync_wing_direction_buttons()
            #  2026-08-12 持久化缺口修复: 恢复跳过编号排序(flag + UI 同步, blockSignals
            #   防 _on_skip_base_order_toggled 提前重载; 后续设备组加载按 sig 含 skip 生效)
            self._skip_base_order = bool(state.get('skip_base_order', False))
            if hasattr(self, '_skip_base_order_chk'):
                self._skip_base_order_chk.blockSignals(True)
                self._skip_base_order_chk.setChecked(self._skip_base_order)
                self._skip_base_order_chk.blockSignals(False)
            self._capturing_slice_key = False
            self._update_slice_key_label()
        except Exception:
            self._slice_key = None
            self._slice_key_modifiers = 0
            self._update_slice_key_label()
        # 像素效果参数
        pixel_params = state.get('pixel_params', {})
        if pixel_params and self._pixel_engine:
            _restore_pixel_params(self, pixel_params, state)
        #  R183：分区 gamma 随 config 一起还原 → 下推像素引擎/重建幻灯片分区缓存
        self._sync_output_gamma()

        _running_outputs.restore_saved(self, state)

    # ══════════════════════════════════════════════════════════════════════════
    #  调度台：矩阵效果总线上报（唯一出口）
    #
    #  为什么在这里：矩阵面板的"效果"就是**当前设备组上的某一模式**
    #  （ppt 幻灯片 / pixel 像素效果）。一个 (模式, 设备集) 就是一个窗格。
    #
    #   身份契约：一窗格 = (panel, track_type, group_key)。track_type = 模式 id，
    #  group_key 由设备 id 内容寻址 → "同模式不同设备""同设备不同模式"天然两格；
    #  **绝不按 effect_id/名称/分类合并**（用户：微差即两种效果）。
    # ══════════════════════════════════════════════════════════════════════════

    #: 上报节流（秒）：主定时器 25fps，总线只需人眼刷新率级别
    _BUS_REPORT_INTERVAL = 0.25

    @staticmethod
    def _dispatch_group_key(ids) -> str:
        """设备 id 内容寻址的设备集身份（与 EffectBrowser._group_key_from_ids 同规则）。"""
        out, seen = [], set()
        for fid in (ids or []):
            sfid = str(fid)
            if sfid and sfid not in seen:
                seen.add(sfid)
                out.append(sfid)
        return "grp:" + "|".join(out) if out else "__all__"

    def _bus_has_content(self) -> bool:
        """该面板当前是否**真有内容**可上报（防"打开页面即报有效果"）。

        判据（任一成立即算有内容）：
          · ppt 模式：有 slides 帧序列 或 正在播放
          · pixel 模式：正在运行 或 有实际像素值（非全零/未初始化）
          · 用户显式改动过（`_bus_user_touched`）
        默认（面板刚构建、空引擎、什么都没动）→ False。
        """
        try:
            if bool(getattr(self, "_bus_user_touched", False)):
                return True
            mode = str(getattr(self, "_mode", "ppt") or "ppt")
            if mode == "pixel":
                eng = getattr(self, "_pixel_engine", None)
                if eng is None:
                    return False
                if bool(getattr(self, "_pixel_running", False)):
                    return True
                # 无运行、但已有非零像素值也算有内容（例如用户设了静态图案）
                vals = getattr(eng, "_values", None)
                if vals is None:
                    return False
                try:
                    import numpy as _np
                    return bool(_np.any(_np.asarray(vals) != 0))
                except Exception:
                    return bool(vals)
            
            eng = getattr(self, "_engine", None)
            if eng is None:
                return False
            if bool(getattr(eng, "is_playing", False)):
                return True
            slides = getattr(eng, "slides", None)
            return bool(slides)
        except Exception:
            return False

    def _bus_nodes(self) -> list:
        """把当前矩阵模式转成调度台窗格（一模式一节点，不去重）。

         上报门槛（2026-09-13 修）：**面板存在 ≠ 用户制作了效果**。
          原先只要面板被构建（打开 Matrix 页即触发 25fps `_tick`）就无条件
          生成本节点 → 调度台立刻显示"有效果"，而用户什么都没做。
          现在加两道门槛，任一满足才算"真有内容"：
            ① 有实际帧内容（ppt：有 slides 或正在播放；pixel：正在运行/有值）
            ② 用户改动过（`_bus_user_touched`，由绘制/播放/参数变更置位）
          否则返回空列表 —— 总线不落格、调度台不显示。
        """
        from ...processing.dispatch import EffectNode
        nodes: List[EffectNode] = []
        retained = _running_outputs.bus_nodes(self)
        try:
            if not self._bus_has_content() or _running_outputs.passive(self):
                return retained
            fixtures = list(getattr(self, "_fixtures", []) or [])
            ids = [str(f.get("id", "")) for f in fixtures if f.get("id")]
            group_key = self._dispatch_group_key(ids)
            group_name = (str(getattr(self, "_current_group_name", "") or "")
                          or ("临时设备集" if ids else "全部设备"))
            mode = str(getattr(self, "_mode", "ppt") or "ppt")
            #  组身份回填（2026-09-13）：供调度台"子母文件夹"归类；伪 id
            #   （__all__/__topview_live__/…）由调度台侧 group_resolver 甄别清空。
            _cur_gid = str(getattr(self, "_current_group_id", "") or "")
            if mode == "pixel":
                mid = str(getattr(getattr(self, "_pixel_engine", None), "_mode", "") or "")
                label = cat = ""
                try:
                    for c, effects in PIXEL_MODES.items():
                        for m_id, m_name in effects:
                            if str(m_id) == mid:
                                label, cat = str(m_name), str(c)
                                break
                        if label:
                            break
                except Exception:
                    pass
                nodes.append(_pixel_node(
                    (mid, label, cat, ids, group_key, group_name, _cur_gid,
                     str(self._matrix_output_layer())),
                    bool(getattr(self, "_pixel_running", False)),
                    bool(getattr(self, "_recalled_play", False))))
            else:
                eng = getattr(self, "_engine", None)
                idx = int(getattr(eng, "current_idx", 0) or 0)
                slides = list(getattr(eng, "slides", []) or [])
                name = ""
                if 0 <= idx < len(slides):
                    sl = slides[idx]
                    name = str(getattr(sl, "name", "") or (sl.get("name", "") if isinstance(sl, dict) else "") or "")
                nodes.append(_ppt_node(
                    (name, idx, ids, group_key, group_name, _cur_gid),
                    bool(getattr(eng, "is_playing", False)),
                    bool(getattr(self, "_recalled_play", False))))
        except Exception:
            logger.debug("matrix: _bus_nodes failed", exc_info=True)
        return retained + enrich_matrix_nodes(self, nodes)

    def report_to_bus(self, force: bool = False) -> None:
        """上报当前矩阵窗格到总线（节流；失败绝不影响出光）。"""
        try:
            now = time.monotonic()
            if not force and (now - getattr(self, "_bus_last_report", 0.0)) < self._BUS_REPORT_INTERVAL:
                return
            self._bus_last_report = now
            from ...processing.dispatch import EffectBus
            bus = EffectBus.instance()
            nodes = self._bus_nodes()
            # 逐键 upsert + 差集删除：模式切换/清空设备时只删自己的格，不整体 clear_panel
            live_keys = {(n.panel, n.track_type, n.group_key) for n in nodes}
            for n in nodes:
                bus.report(n)
            for key in list(getattr(self, "_bus_seen_keys", ())):
                if key not in live_keys:
                    bus.remove(*key)
            self._bus_seen_keys = live_keys
        except Exception:
            pass

    def dispatch_capture_state(self) -> dict:
        """给调度台捕获可完整还原的面板状态（复用面板既有 save_state）。"""
        try:
            return dict(self.save_state())
        except Exception:
            return {}

    def dispatch_release_state(self, node: dict, state: Optional[dict] = None) -> bool:
        """**释放**某调度台矩阵格（关闭场景 / 取消激活时调用）。

         2026-09-14 用户实报："effect 和 matrix 等页面制作的效果被存储区存了之后
          也没有正确 release 并同步到相应效果页面。"
        真因：关闭场景原实现只清 NetworkManager 层，但**矩阵面板的播放定时器仍在每帧
        重写 matrix/matrix_audio 层** → 清掉的层下一帧被灌回，表现"消不掉、也没同步
        回面板"。真正的释放必须**停掉面板自己的输出**（停定时器 + 清自己的输出态）。

        语义：停播放/像素模式并清矩阵层。

         关键：不能只靠 `_stop()` —— 它按面板**当前 `_mode`** 分派 ppt/pixel 两条
          路径；若节点来源与当前模式不一致（例：节点是 pixel、面板 `_mode` 还停在
          ppt），会走错分支 → 实际在跑的那条输出**没被停**（实测：调用后
          `_pixel_running` 仍为 True，定时器继续重写层 → "消不掉"）。
          故这里按"**实际上在跑什么**"直接停（`_stop_running_output`）。
        """
        return _running_outputs.release_node(self, node or {})

    def _stop_running_output(self) -> None:
        """停掉**实际在跑**的输出（像素/幻灯片各收一遍，不依赖 `_mode` 分派）。"""
        if bool(getattr(self, "_pixel_running", False)):
            try:
                self._stop_pixel_mode(full_release=False, clear_output=True)
            except Exception:
                logger.debug("matrix: stop pixel mode failed", exc_info=True)
            try:
                self._pixel_running = False     # 兜底：标志必须归零
            except Exception:
                pass
        try:
            playing = bool(self._play_btn.isChecked())
        except Exception:
            playing = False
        if playing:
            try:
                self._stop()
            except Exception:
                logger.debug("matrix: _stop failed in release", exc_info=True)
        # 隐藏中释放 → 让 25fps 主循环收掉（否则空转到会话结束）
        self.settle_hidden_timer()

    def _clear_matrix_layers(self) -> None:
        """清空矩阵输出层（matrix / matrix_audio）——释放的落层动作。"""
        nm = getattr(self, "_nm_ref", None)
        if nm is None:
            self.settle_hidden_timer()
            return
        for layer_name in ("matrix", "matrix_audio"):
            try:
                _running_outputs.clear_current(self, (layer_name,))
            except Exception:
                logger.debug("matrix: clear layer %s failed", layer_name, exc_info=True)
        self.settle_hidden_timer()

    _recall_scope = _recall_scope

    def dispatch_recall_state(self, node: dict, state: Optional[dict] = None,
                              *, for_edit: bool = False) -> bool:
        """按调度台节点还原该矩阵格（先恢复设备作用域，再套用面板状态）。

        for_edit： 2026-09-18 用户定案（问题9 调用/编辑双语义）——
          · False（**调用/播放**）：只恢复作用域并跑起来，**不套用整面板状态**
            （`load_state` 会重建缩略图/参数控件/画布，那是编辑语义的动作）；
          · True（**编辑/调入监控区**）：额外 `load_state` 还原控件供继续编辑。
        """
        try:
            node = dict(node or {})
            state = _running_outputs.state_for_node(self, node, state)
            _recall_scope(self, node, state)   # 1) 制作S与实际贡献T分开还原
            if for_edit and _running_outputs.running(self):
                return True  # 调入已在运行的原对象，不重置其时钟或引擎。
            _running_outputs.apply_recall_recipe(self, node, state, for_edit)
            #  深查修正（标记自我抵消）：跑帧必须在**落标记之前** —— `_tick()` →
            #   `_output_pixel_values()` 会写输出。且输出路径**不再**碰标记
            #   （见 `_output_pixel_values`），标记只由明确的用户动作清除，
            #   否则 40ms 定时器会紧接着把它清掉、等同没打标记。
            try:
                self._tick()      # 立刻跑一帧：NM 在返回前即持有调用后的值（去延迟）
            except Exception:
                logger.debug("matrix: immediate tick after recall failed", exc_info=True)
            #  2026-09-18 回访修正（R88/R89 漏项）：面板**隐藏**时 25fps 主循环是
            #   停的（构造不 start，`hideEvent`/`settle_hidden_timer` 空闲即停表）。
            #   纯播放 recall 不走 `showEvent`，于是"上面这一帧写完就没有下一帧"。
            #   实测（dock 关闭状态下点场景）：`_pixel_running=True`（补启动成功）但
            #   `_timer.isActive()` 仍为 False，层值 1 秒内恒定不变（首帧 t≈0 常为全 0
            #   → 用户看到"点了没反应"），手动 `_tick()` 才写出 255。
            #   修法：召回后只要面板在出光就确保主循环跑起来（与 `hideEvent`
            #   "出光中保留主循环"同一语义；空闲面板不会因此常驻烧 CPU）。
            self._ensure_output_loop()
            self._recalled_play = True
            self.report_to_bus(force=True)     # 立即反映到调度台
            return True
        except Exception:
            logger.debug("matrix: dispatch_recall_state failed", exc_info=True)
            return False

    def _ensure_output_loop(self) -> bool:
        """出光中且主循环未跑时把它拉起来（隐藏态 recall 的唯一补启动点）。

        `_matrix_output_active()` 判"是否真在出光"（ppt 播放中 / pixel 运行中）；
        空闲面板不动，保持"隐藏即 0 CPU"的既有收益。
        """
        try:
            if not self._matrix_output_active():
                return False
            t = getattr(self, "_timer", None)
            if t is None or t.isActive():
                return bool(t is not None)
            t.start()
            return True
        except Exception:
            logger.debug("matrix: ensure output loop failed", exc_info=True)
            return False

    # ── 主定时器 ──────────────────────────────────────────────────────────────

    def _tick_ui_previews(self, values, states, now: float) -> None:
        """像素模式的**纯 UI** 预览刷新（面板隐藏时整体跳过，出光不受影响）。

        2026-09-17 隐藏加载语义：抽成独立方法，既让 `_tick` 保持复杂度门内，
        也让「隐藏时只跳过 UI、不跳过输出」这条边界一处可见。
        """
        if bool(getattr(self, "_ui_hidden", False)):
            return
        refresh_pitch_status(self._pixel_param_panel)
        self._tick_canvas_preview(values, states, now)
        self._tick_screen_preview(now)

    def _tick_canvas_preview(self, values, states, now: float) -> None:
        """像素画布预览刷新（间隔按设备数自适应；隐藏时由调用方跳过）。"""
        last_preview = float(getattr(self, "_last_pixel_preview_update", 0.0) or 0.0)
        big = len(getattr(self, "_fixtures", []) or []) >= int(
            getattr(self, "_large_fixture_preview_threshold", 1000))
        if now - last_preview < (0.20 if big else 0.10):
            return
        self._last_pixel_preview_update = now
        if values is not None and hasattr(self._canvas, "set_pixel_values"):
            self._canvas.set_pixel_values(values, self._pixel_engine.threshold)
        else:
            weights = {fid: (1.0 if on else 0.0)
                       for fid, on in (states or {}).items()}
            #  #8：预览权重与 NM 输出同一条分区 gamma 曲线（未启用=透传）
            try:
                weights = self._pixel_engine.apply_output_gamma_weights(weights)
            except Exception:
                pass
            self._canvas.set_pixel_weights(weights, self._pixel_engine.threshold)

    def _tick_screen_preview(self, now: float) -> None:
        """屏幕/视频采样模式的预览图与统计刷新（仅这两种模式；纯 UI）。"""
        if getattr(self._pixel_engine, '_mode', '') not in ('screen_sample', 'video_sample'):
            return
        last_screen = float(getattr(self, "_last_screen_preview_update", 0.0) or 0.0)
        if now - last_screen < 0.10:
            return
        self._last_screen_preview_update = now
        try:
            self._pixel_param_panel.update_screen_preview(self._pixel_engine)
        except Exception:
            pass

    def _tick(self):
        #  2026-09-21 用户实报（切片模式下矩阵存不进调度台）：本函数是**唯一**的
        #   `report_to_bus` 调用点，而切片模式一进来就 return → 只要用户点过一次
        #   "绑定"再按一个键（`_slice_key is not None` 从此永久为真），25fps 主循环里
        #   再没有任何上报/差集删除 → 切片模式下在矩阵做的东西存不进调度台（存下来是
        #   空的），画布/播放按钮的 UI 刷新也一起停掉。
        #   修法：上报提到切片早退**之前**（上报自带节流，切片早退不产生额外开销）。
        if not getattr(self, "_matrix_bound_run", False):
            _running_outputs.tick(self)
            self.report_to_bus()
        if self._slice_mode_enabled():
            return
        if getattr(self, "_pixel_transition_busy", False):
            return
        dt = 0.04  # 固定 dt（与 timer 间隔对应）
        now = time.monotonic()

        if self._mode == "ppt":
            self._engine.tick(dt)
            # 更新播放中的缩略图高亮（纯 UI：隐藏时跳过，出光不受影响）
            if not getattr(self, "_ui_hidden", False):
                self._update_playing_thumb()
        elif self._mode == "pixel" and self._pixel_running:
            actual_dt = now - self._pixel_last_t if self._pixel_last_t > 0 else dt
            self._pixel_last_t = now
            values = None
            states = None
            if hasattr(self._pixel_engine, "tick_values"):
                try:
                    values = self._pixel_engine.tick_values(actual_dt)
                except Exception:
                    values = None
            if values is None:
                states = self._pixel_engine.tick(actual_dt)
            # UI 预览（可跳过）——下方的输出段必须照常执行
            self._tick_ui_previews(values, states, now)
            # 输出到 NM
            if values is not None:
                self._output_pixel_values(values)
            else:
                self._output_pixel_states(states or {})
        # 调度台上报（节流在 report_to_bus 内部；失败不影响出光）—— 已在函数开头做

    def _key_modifiers_value(self, modifiers) -> int:
        try:
            return int(modifiers.value)
        except Exception:
            try:
                return int(modifiers)
            except Exception:
                return 0

    def _event_key_parts(self, event) -> tuple[int, int]:
        key = int(event.key())
        mods = self._key_modifiers_value(event.modifiers())
        allowed = (
            Qt.KeyboardModifier.ShiftModifier |
            Qt.KeyboardModifier.ControlModifier |
            Qt.KeyboardModifier.AltModifier |
            Qt.KeyboardModifier.MetaModifier
        )
        return key, mods & self._key_modifiers_value(allowed)

    def _format_slice_key(self, key: Optional[int] = None,
                          modifiers: Optional[int] = None) -> str:
        key = self._slice_key if key is None else key
        modifiers = self._slice_key_modifiers if modifiers is None else modifiers
        if key is None:
            return "未绑定"
        try:
            text = QKeySequence(int(key) | int(modifiers or 0)).toString(
                QKeySequence.SequenceFormat.NativeText)
            if text:
                return text
        except Exception:
            pass
        return f"Key {int(key)}"

    def _update_slice_key_label(self):
        if hasattr(self, "_slice_key_lbl"):
            self._slice_key_lbl.setText(self._format_slice_key())
        if hasattr(self, "_slice_key_bind_btn"):
            self._slice_key_bind_btn.setText("绑定")
        self._refresh_slice_shortcut()

    def _refresh_slice_shortcut(self):
        from .matrix_slice_shortcut import retire_shortcut
        retire_shortcut(self._slice_shortcut)
        self._slice_shortcut = None
        if self._slice_key is None:
            return
        try:
            seq = QKeySequence(int(self._slice_key) | int(self._slice_key_modifiers or 0))
            shortcut = QShortcut(seq, self)
            shortcut.setAutoRepeat(False)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(self._on_slice_shortcut_activated)
            self._slice_shortcut = shortcut
        except Exception:
            logger.exception("创建切片快捷键失败: slice_key=%s", self._slice_key)
            self._slice_shortcut = None

    def _on_slice_shortcut_activated(self):
        if self._capturing_slice_key or self._focused_text_input_active():
            return
        now = time.monotonic()
        if now - float(getattr(self, "_last_slice_key_time", 0.0) or 0.0) < 0.08:
            return
        self._last_slice_key_time = now
        self._advance_keyed_slice()

    def _slice_mode_enabled(self) -> bool:
        return self._slice_key is not None

    def _slice_output_enabled(self) -> bool:
        try:
            return bool(self._play_btn.isChecked())
        except Exception:
            return False

    def _sync_sample_preview_on_off(self, states: Optional[Dict[str, bool]] = None):
        """Keep the screen/video sample preview aligned with manual keyed slices."""
        panel = getattr(self, "_pixel_param_panel", None)
        canvas = getattr(panel, "_screen_preview_canvas", None)
        if canvas is None:
            return
        try:
            canvas.set_on_off(states, getattr(self._pixel_engine, "threshold", 0.5))
        except Exception:
            pass

    def _begin_slice_key_capture(self):
        self._capturing_slice_key = True
        self.setFocus(Qt.FocusReason.ShortcutFocusReason)
        if hasattr(self, "_slice_key_lbl"):
            self._slice_key_lbl.setText("按键...")
        if hasattr(self, "_slice_key_bind_btn"):
            self._slice_key_bind_btn.setText("等待")

    def _clear_slice_key_binding(self):
        """解绑切片键：清绑定 + **落层**（取消切片模式不能把灯停在最后一步的值）。

         2026-09-21 用户实报（清绑后灯不灭）：原实现只清 `_slice_key` /
        `_manual_slice_*` 与画布，**不清 NM 层、也不停输出**（且 `_slice_output_enabled()`
        看的是播放按钮勾选态，勾着就直接"重新播放"）→ 设备保持最后一次切片的通道值，
        直到用户手动点"停止"（实测清绑后仍 `{2:255,4:255,6:255}`）。
        修法：先走与 `_stop()` 同款的落层动作（清 matrix/matrix_audio 层 + 复位输出
        缓存），再按播放按钮状态决定要不要回到正常播放。
        """
        self._slice_key = None
        self._slice_key_modifiers = 0
        self._capturing_slice_key = False
        self._manual_slice_latched = False
        self._manual_slice_states = None
        self._sync_sample_preview_on_off(None)
        self._update_slice_key_label()
        resume = self._slice_output_enabled()
        try:
            self._clear_matrix_output_layers()
            self._reset_matrix_output_state(clear_pattern=True)
        except Exception:
            logger.debug("matrix: clear layers on slice unbind failed", exc_info=True)
        if resume:
            if self._mode == "ppt":
                self._engine.play()
            elif self._mode == "pixel":
                self._start_pixel_mode()

    def _is_modifier_key(self, key: int) -> bool:
        return key in (
            int(Qt.Key.Key_Shift), int(Qt.Key.Key_Control),
            int(Qt.Key.Key_Alt), int(Qt.Key.Key_Meta),
            int(Qt.Key.Key_AltGr),
        )

    def _focused_text_input_active(self) -> bool:
        fw = self.focusWidget()
        return isinstance(fw, (QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox))

    def keyPressEvent(self, event):
        if self._handle_slice_key_event(event):
            return
        super().keyPressEvent(event)

    def _handle_slice_key_event(self, event) -> bool:
        if event.isAutoRepeat():
            return False
        key, mods = self._event_key_parts(event)
        if self._capturing_slice_key:
            if self._is_modifier_key(key):
                event.accept()
                return True
            self._slice_key = key
            self._slice_key_modifiers = mods
            self._capturing_slice_key = False
            self._manual_slice_latched = True
            self._manual_slice_states = None
            self._update_slice_key_label()
            event.accept()
            return True
        if self._slice_key is None:
            return False
        if self._focused_text_input_active():
            return False
        if key == int(self._slice_key) and mods == int(self._slice_key_modifiers or 0):
            self._last_slice_key_time = time.monotonic()
            self._advance_keyed_slice()
            event.accept()
            return True
        return False

    def _advance_keyed_slice(self):
        if not self._slice_output_enabled() and self._mode != "pixel":
            return
        if self._mode == "ppt":
            self._advance_ppt_key_slice()
        elif self._mode == "pixel":
            self._advance_pixel_key_slice()

    def _output_current_keyed_slice(self):
        states = getattr(self, "_manual_slice_states", None)
        if states:
            if self._mode == "ppt":
                self._canvas.set_states(states)
            else:
                weights = {fid: (1.0 if on else 0.0) for fid, on in states.items()}
                self._canvas.set_pixel_weights(weights, self._pixel_engine.threshold)
            self._sync_sample_preview_on_off(states)
            self._output_pixel_states(states)
            return
        if self._mode == "ppt":
            self._advance_ppt_key_slice()
        elif self._mode == "pixel":
            self._advance_pixel_key_slice()

    def _load_current_ppt_key_slice(self):
        if self._mode != "ppt":
            return
        if not self._engine.slides:
            return
        try:
            self._engine.pause()
        except Exception:
            pass
        idx = max(0, min(self._engine.current_idx, len(self._engine.slides) - 1))
        states = self._engine.slides[idx].states
        self._manual_slice_states = dict(states)
        self._canvas.set_states(states)
        self._sync_sample_preview_on_off(states)
        self._output_pixel_states(states)
        self._update_thumb_selection()

    def _load_current_pixel_key_slice(self):
        if self._mode != "pixel":
            return
        self._manual_slice_states = None
        self._sync_wing_block_to_pixel_engine()
        if hasattr(self._pixel_engine, "manual_slice_step"):
            states = self._pixel_engine.manual_slice_step(
                self._pixel_engine.manual_page_count()
                if hasattr(self._pixel_engine, "manual_page_count") else None
            )
        else:
            states = self._pixel_engine.tick(0.0)
        if not states:
            return
        self._manual_slice_states = dict(states)
        weights = {fid: (1.0 if on else 0.0) for fid, on in states.items()}
        self._canvas.set_pixel_weights(weights, self._pixel_engine.threshold)
        self._sync_sample_preview_on_off(states)
        self._output_pixel_states(states)

    def _load_current_keyed_slice(self):
        if self._mode == "ppt":
            self._load_current_ppt_key_slice()
        elif self._mode == "pixel":
            self._load_current_pixel_key_slice()

    def _advance_ppt_key_slice(self):
        if not self._engine.slides:
            return
        self._manual_slice_states = None
        try:
            self._engine.pause()
        except Exception:
            pass
        next_idx = (self._engine.current_idx + 1) % len(self._engine.slides)
        self._engine.goto(next_idx)
        self._update_thumb_selection()
        if 0 <= next_idx < len(self._engine.slides):
            states = self._engine.slides[next_idx].states
            self._manual_slice_states = dict(states)
            self._canvas.set_states(states)
            self._sync_sample_preview_on_off(states)
            self._output_pixel_states(states)
            self._manual_slice_latched = True

    def _advance_pixel_key_slice(self):
        if not self._fixtures and getattr(self._pixel_engine, '_mode', '') != MODE_VIDEO_SAMPLE:
            return
        if (hasattr(self._pixel_engine, "is_audio_reactive_active") and
                self._pixel_engine.is_audio_reactive_active()):
            return
        self._manual_slice_states = None
        self._sync_wing_block_to_pixel_engine()
        if hasattr(self._pixel_engine, "manual_page_count"):
            slice_count = self._pixel_engine.manual_page_count()
        else:
            sorted_ids = getattr(self._pixel_engine, "_sorted_ids", []) or []
            wings = max(1, int(getattr(self._pixel_engine, "wings", 1) or 1))
            block_size = max(1, int(getattr(self._pixel_engine, "block_size", 1) or 1))
            slice_count = max(1, math.ceil(len(sorted_ids or self._fixtures or [1]) / max(1, wings * block_size)))
        if hasattr(self._pixel_engine, "manual_slice_step"):
            states = self._pixel_engine.manual_slice_step(slice_count)
        else:
            old_speed = float(getattr(self._pixel_engine, "speed", 1.0) or 0.0)
            self._pixel_engine.speed = 1.0 if abs(old_speed) <= 1e-6 else old_speed
            try:
                states = self._pixel_engine.tick(1.0 / float(slice_count))
            finally:
                self._pixel_engine.speed = old_speed
        self._manual_pixel_slice_index = (self._manual_pixel_slice_index + 1) % slice_count
        if not states:
            return
        self._manual_slice_states = dict(states)
        weights = {fid: (1.0 if on else 0.0) for fid, on in states.items()}
        self._canvas.set_pixel_weights(weights, self._pixel_engine.threshold)
        self._sync_sample_preview_on_off(states)
        if getattr(self._pixel_engine, '_mode', '') in ('screen_sample', 'video_sample'):
            try:
                self._pixel_param_panel.update_screen_preview(self._pixel_engine)
                self._sync_sample_preview_on_off(states)
            except Exception:
                pass
        self._output_pixel_states(states)
        self._manual_slice_latched = True

    def _update_playing_thumb(self):
        """更新播放中缩略图的高亮状态"""
        thumbs = getattr(self, '_thumbs', None)
        if not thumbs:
            return
        playing_idx = self._engine.current_idx
        for i, thumb in enumerate(thumbs):
            thumb.set_playing(i == playing_idx and self._engine.is_playing)

    # ── 引擎输出回调 ──────────────────────────────────────────────────────────

    def _on_engine_output(self, weights: Dict[str, float],
                          on_value: int = 255, off_value: int = 0):
        """MatrixSlideEngine output callback: preview the actual PPT output."""
        if not _running_outputs.accepts_ppt_output(self):
            return
        try:
            weights = _running_outputs.preview_ppt(self, weights)
        except Exception:
            pass
        try:
            fixture_order = getattr(self, "_matrix_fixture_order", {}) or {}
            if not fixture_order:
                self._rebuild_channel_addr_cache()
                fixture_order = getattr(self, "_matrix_fixture_order", {}) or {}
            values = np.full(len(fixture_order), int(off_value), dtype=np.uint8)
            delta = int(on_value) - int(off_value)
            for fid, idx in fixture_order.items():
                w = max(0.0, min(1.0, float((weights or {}).get(str(fid), 0.0) or 0.0)))
                values[int(idx)] = max(0, min(255, int(round(int(off_value) + delta * w))))
            self._output_pixel_values(values)
        except Exception:
            states = {str(fid): bool(float(val or 0.0) >= 0.5)
                      for fid, val in (weights or {}).items()}
            self._output_pixel_states(states)

    def _output_pixel_values(self, values: np.ndarray):
        if getattr(self, "_matrix_selecting", False) or not prepare_output_scope(self):
            return
        # 真写输出 = 面板确实在做效果（供 _bus_has_content 门槛判定）。
        # 此处**不再**清 `_recalled_play`：输出既可能来自用户、也可能来自回灌
        # （40ms 定时器紧随调用又跑一帧），无法区分 → 标记改由明确的用户动作清除。
        _running_outputs.activate(self)
        t0 = time.perf_counter()
        try:
            uni_data: Dict[int, Dict[int, int]] = {}
            touched_universes: Set[int] = set()
            pattern_payload: Dict[str, Dict[str, int]] = {}
            ch_type = self._ch_type
            target = resolve_output_target(self, ch_type)
            if not getattr(self, "_fixture_addr_cache", None):
                self._rebuild_channel_addr_cache()
            self._matrix_base_frame_cache = {}

            now_emit = time.monotonic()
            interval = float(getattr(self, "_matrix_pattern_emit_interval", 0.12) or 0.12)
            if len(getattr(self, "_fixtures", []) or []) >= int(getattr(self, "_large_fixture_preview_threshold", 1000)):
                interval = max(interval, float(getattr(self, "_large_matrix_pattern_emit_interval", 0.50) or 0.50))
            emit_pattern = (
                now_emit - float(getattr(self, "_last_matrix_pattern_emit", 0.0) or 0.0) >= interval
            )
            full_interval = float(getattr(self, "_matrix_full_output_interval", 1.0) or 1.0)
            force_full_output = bool(getattr(self, "_force_matrix_full_output", False)) or (
                now_emit - float(getattr(self, "_last_matrix_full_output_emit", 0.0) or 0.0) >= full_interval
            )
            if force_full_output:
                self._force_matrix_full_output = False
            matrix_touch_ltp = bool(getattr(self, "_matrix_ltp_takeover_pending", False))
            matrix_layer = self._matrix_output_layer()
            self._clear_inactive_matrix_layer(matrix_layer)
            vector_result = None
            try:
                if hasattr(self._nm_ref, "update_layer_sparse_arrays"):
                    array_result = self._output_pixel_values_arrays(
                        values, target, force=force_full_output)
                    if array_result is not None:
                        universes, addrs, mapped_values, changed_fixture_idxs, grouped = array_result
                        if mapped_values.size:
                            if grouped and hasattr(self._nm_ref, "update_layer_sparse_grouped_arrays"):
                                changed_universes = write_grouped(
                                    self, matrix_layer, grouped, touch_ltp=matrix_touch_ltp)
                            else:
                                changed_universes = write_arrays(
                                    self, matrix_layer, universes, addrs, mapped_values,
                                    touch_ltp=matrix_touch_ltp)
                            touched_universes.update(int(u) for u in (changed_universes or []))
                            if emit_pattern and changed_fixture_idxs.size:
                                fixture_order = getattr(self, "_matrix_fixture_order", {}) or {}
                                reverse_order = self.__dict__.get("_matrix_fixture_reverse_order")
                                if reverse_order is None or len(reverse_order) != len(fixture_order):
                                    reverse_order = [None] * len(fixture_order)
                                    for fid, idx in fixture_order.items():
                                        if 0 <= int(idx) < len(reverse_order):
                                            reverse_order[int(idx)] = fid
                                    self._matrix_fixture_reverse_order = reverse_order
                                vals = np.asarray(values, dtype=np.uint8)
                                for idx in set(int(i) for i in changed_fixture_idxs):
                                    if 0 <= idx < len(reverse_order):
                                        fid = reverse_order[idx]
                                        if fid is not None:
                                            pattern_payload.setdefault(fid, {})[str(ch_type or target)] = int(vals[idx])
                            if force_full_output:
                                self._last_matrix_full_output_emit = now_emit
                        vector_result = (None, touched_universes, pattern_payload)
                if vector_result is None:
                    vector_result = self._output_pixel_values_vectorized(values, target, ch_type, emit_pattern=emit_pattern)
            except Exception:
                vector_result = None
            if vector_result is not None:
                maybe_uni_data, touched_universes, pattern_payload = vector_result
                if maybe_uni_data is None:
                    uni_data = {}
                else:
                    uni_data = maybe_uni_data
            else:
                if hasattr(self._pixel_engine, "states_to_dict"):
                    self._output_pixel_states(self._pixel_engine.states_to_dict(values))
                return

            batch_written = False
            if uni_data and hasattr(self._nm_ref, "update_layer_sparse_batch"):
                try:
                    changed_universes = write_batch(
                        self, matrix_layer, uni_data, touch_ltp=matrix_touch_ltp)
                    touched_universes.update(int(u) for u in (changed_universes or []))
                    output_cache = self._matrix_layer_cache(matrix_layer)
                    for universe, ch_map in uni_data.items():
                        uni_cache = output_cache.setdefault(int(universe), {})
                        for ch, val in ch_map.items():
                            uni_cache[int(ch)] = int(val)
                    batch_written = True
                except Exception:
                    batch_written = False

            if not batch_written:
                for universe, ch_map in uni_data.items():
                    if not ch_map:
                        continue
                    try:
                        if hasattr(self._nm_ref, "update_layer_sparse"):
                            write_sparse(
                                self, matrix_layer, universe, ch_map, touch_ltp=matrix_touch_ltp)
                        else:
                            data = bytearray(512)
                            active_list = [False] * 512
                            for ch, val in ch_map.items():
                                if 0 <= ch < 512:
                                    data[ch] = val
                                    active_list[ch] = True
                            write_frame(
                                self, matrix_layer, universe, bytes(data), active=active_list,
                                touch_ltp=matrix_touch_ltp)
                        touched_universes.add(int(universe))
                        output_cache = self._matrix_layer_cache(matrix_layer)
                        uni_cache = output_cache.setdefault(int(universe), {})
                        for ch, val in ch_map.items():
                            uni_cache[int(ch)] = int(val)
                    except Exception:
                        pass
                self._flush_matrix_output(touched_universes)

            if touched_universes and matrix_touch_ltp:
                self._matrix_ltp_takeover_pending = False

            if pattern_payload:
                self._matrix_pattern_pending.update(pattern_payload)
            try:
                if self._matrix_pattern_pending:
                    now = time.monotonic()
                    interval = float(getattr(self, "_matrix_pattern_emit_interval", 0.12) or 0.12)
                    if len(getattr(self, "_fixtures", []) or []) >= int(getattr(self, "_large_fixture_preview_threshold", 1000)):
                        interval = max(interval, float(getattr(self, "_large_matrix_pattern_emit_interval", 0.50) or 0.50))
                    if now - float(getattr(self, "_last_matrix_pattern_emit", 0.0) or 0.0) >= interval:
                        pending = dict(getattr(self, "_matrix_pattern_pending", {}) or {})
                        self._matrix_pattern_pending = {}
                        self._last_matrix_pattern_emit = now
                        if pending:
                            self.pattern_updated.emit(pending)
            except Exception:
                pass
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("MatrixSlideshowPanel fast output error: %s", e)
        finally:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            slow_ms = float(getattr(self, "_perf_slow_matrix_output_ms", 12.0) or 12.0)
            if elapsed_ms >= slow_ms:
                now_log = time.monotonic()
                if now_log - float(getattr(self, "_perf_last_matrix_output_log", 0.0) or 0.0) >= 2.0:
                    self._perf_last_matrix_output_log = now_log
                    import logging
                    logging.getLogger(__name__).info(
                        "Matrix fast output slow frame: %.2f ms, fixtures=%s, universes=%s",
                        elapsed_ms,
                        int(np.asarray(values).size) if values is not None else 0,
                        sum(len(v or {}) for v in (getattr(self, "_matrix_output_cache", {}) or {}).values()),
                    )
            self._matrix_base_frame_cache = {}

    def _output_pixel_states(self, states: Dict[str, bool]):
        """ON/OFF 状态输出到 NM matrix 层；pattern_updated 信号推给 Programmer
        的 ChannelList（历史死链路已接通）。"""
        if getattr(self, "_matrix_selecting", False) or not prepare_output_scope(self):
            return
        t0 = time.perf_counter()
        states = _shape_pixel_states(self, states)   #  M4 统一出口（helper 压基线）
        states = _apply_zone_gamma_states(self, states)   #  #8：与输出同曲线
        try:
            uni_data: Dict[int, Dict[int, int]] = {}
            touched_universes: Set[int] = set()
            pattern_payload: Dict[str, Dict[str, int]] = {}
            ch_type = self._ch_type
            target = resolve_output_target(self, ch_type)
            if not getattr(self, "_fixture_addr_cache", None):
                self._rebuild_channel_addr_cache()
            self._matrix_base_frame_cache = {}
            used_vector_path = False
            matrix_layer = self._matrix_output_layer()
            try:
                vector_result = self._output_pixel_states_vectorized(
                    states,
                    target,
                    ch_type,
                )
                if vector_result is not None:
                    uni_data, touched_universes, pattern_payload = vector_result
                    used_vector_path = True
            except Exception:
                used_vector_path = False

            if not used_vector_path:
                for fid, on in states.items():
                    if fid not in self._fixture_addr_cache:
                        continue
                    value = _state_value(on)      #  灰度帧：int 0-255 原样
                    mapped = self._matrix_fixture_output(fid, value, target)
                    if not mapped:
                        continue
                    changed_fixture = False
                    for universe, dmx_ch, mapped_value in mapped:
                        prev_val = self._matrix_layer_cache(matrix_layer).get(universe, {}).get(dmx_ch)
                        if prev_val == mapped_value:
                            continue
                        uni_data.setdefault(universe, {})[dmx_ch] = mapped_value
                        changed_fixture = True
                    if changed_fixture:
                        pattern_payload.setdefault(fid, {})[str(ch_type or target)] = value

            # 写入 NM matrix 层（稀疏写入：只标记被本面板控制的通道为 active，
            # 其它通道保持 NM 中既有状态，互不污染）
            batch_written = False
            matrix_touch_ltp = bool(getattr(self, "_matrix_ltp_takeover_pending", False))
            self._clear_inactive_matrix_layer(matrix_layer)
            if uni_data and hasattr(self._nm_ref, "update_layer_sparse_batch"):
                try:
                    changed_universes = write_batch(
                        self, matrix_layer, uni_data, touch_ltp=matrix_touch_ltp)
                    touched_universes.update(int(u) for u in (changed_universes or []))
                    output_cache = self._matrix_layer_cache(matrix_layer)
                    for universe, ch_map in uni_data.items():
                        uni_cache = output_cache.setdefault(int(universe), {})
                        for ch, val in ch_map.items():
                            uni_cache[int(ch)] = int(val)
                    batch_written = True
                except Exception:
                    batch_written = False

            if not batch_written:
                for universe, ch_map in uni_data.items():
                    if not ch_map:
                        continue
                    try:
                        if hasattr(self._nm_ref, "update_layer_sparse"):
                            write_sparse(
                                self, matrix_layer, universe, ch_map, touch_ltp=matrix_touch_ltp)
                        else:
                            # 极端兜底：构造完整 active mask（512 bool 列表）
                            data = bytearray(512)
                            active_list = [False] * 512
                            for ch, val in ch_map.items():
                                if 0 <= ch < 512:
                                    data[ch] = val
                                    active_list[ch] = True
                            write_frame(
                                self, matrix_layer, universe, bytes(data), active=active_list,
                                touch_ltp=matrix_touch_ltp)
                        touched_universes.add(int(universe))
                        output_cache = self._matrix_layer_cache(matrix_layer)
                        uni_cache = output_cache.setdefault(int(universe), {})
                        for ch, val in ch_map.items():
                            uni_cache[int(ch)] = int(val)
                    except Exception:
                        pass

                self._flush_matrix_output(touched_universes)

            if touched_universes and matrix_touch_ltp:
                self._matrix_ltp_takeover_pending = False

            #  反馈给 Programmer，让其 ChannelList 也能看到当前 matrix 推的值
            if pattern_payload:
                try:
                    for fid, channels in pattern_payload.items():
                        self._matrix_pattern_cache.setdefault(fid, {}).update(channels)
                        self._matrix_pattern_pending.setdefault(fid, {}).update(channels)
                    now = time.monotonic()
                    if now - float(getattr(self, "_last_matrix_pattern_emit", 0.0) or 0.0) >= float(getattr(self, "_matrix_pattern_emit_interval", 0.12) or 0.12):
                        pending = dict(getattr(self, "_matrix_pattern_pending", {}) or {})
                        self._matrix_pattern_pending = {}
                        self._last_matrix_pattern_emit = now
                        if pending:
                            self.pattern_updated.emit(pending)
                except Exception:
                    pass
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("MatrixSlideshowPanel output error: %s", e)
        finally:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            slow_ms = float(getattr(self, "_perf_slow_matrix_output_ms", 12.0) or 12.0)
            if elapsed_ms >= slow_ms:
                now_log = time.monotonic()
                if now_log - float(getattr(self, "_perf_last_matrix_output_log", 0.0) or 0.0) >= 2.0:
                    self._perf_last_matrix_output_log = now_log
                    import logging
                    logging.getLogger(__name__).info(
                        "Matrix output slow frame: %.2f ms, fixtures=%s, universes=%s",
                        elapsed_ms,
                        len(states or {}),
                        sum(len(v or {}) for v in (getattr(self, "_matrix_output_cache", {}) or {}).values()),
                    )
            self._matrix_base_frame_cache = {}

    def _get_fixture_def(self, pm, def_id: str):
        if not pm or not def_id:
            return None
        try:
            if hasattr(pm, 'get_fixture_def'):
                return pm.get_fixture_def(def_id)
            defs = getattr(pm, 'fixture_defs', None)
            if isinstance(defs, dict):
                return defs.get(def_id)
            if defs is None and hasattr(pm, 'project'):
                defs = getattr(pm.project, 'fixture_defs', None)
            for fd in defs or []:
                if getattr(fd, 'id', '') == def_id:
                    return fd
        except Exception:
            return None
        return None

    def _normalize_ch_type(self, ch) -> str:
        raw = getattr(ch, 'value', ch)
        raw = getattr(raw, 'type', raw)
        raw = getattr(raw, 'ch_type', raw)
        return str(raw or '').strip().lower()

    def _get_ch_offset(self, fixture: Dict) -> Optional[int]:
        """获取当前通道类型在设备中的偏移（0-indexed）。未找到时返回 None，避免误写 CH1。"""
        return first_channel_offset(self, fixture)

    # ── 幻灯片列表管理 ────────────────────────────────────────────────────────

    def _rebuild_thumbs(self):
        """重建幻灯片缩略图列表"""
        # 清空旧缩略图
        self._thumbs: List[SlideThumbnail] = []
        while self._thumb_lay.count() > 1:
            item = self._thumb_lay.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

        slides = self._engine.slides

        # 空态：无帧时显示引导 + 入口按钮（R181：文案与可点的下一步）
        if not slides:
            self._thumb_lay.insertWidget(0, self._make_empty_guide())
        else:
            for i, slide in enumerate(slides):
                thumb = SlideThumbnail(i, slide, self._fixtures)
                thumb.clicked.connect(self._on_thumb_clicked)
                thumb.double_clicked.connect(self._on_thumb_double_clicked)
                self._thumb_lay.insertWidget(i, thumb)
                self._thumbs.append(thumb)

            # 高亮当前选中帧
            self._update_thumb_selection()

    def _make_empty_guide(self) -> MatrixEmptyGuide:
        """空态引导件：无设备 → 载入设备；有设备无帧 → 建首帧 / 载入媒体。"""
        if not self._fixtures:
            return MatrixEmptyGuide(
                "尚未载入设备",
                "矩阵出光需要设备：可一键载入工程全部设备，或在设备组面板选择一组。",
                "layout-grid",
                (("载入全部设备", self._on_empty_load_fixtures, "select_all", True),
                 ("添加第一帧", self._add_slide, "plus", False)))
        return MatrixEmptyGuide(
            "暂无幻灯片帧",
            "添加第一帧后用「设备排布」画 ON/OFF，或导入视频作为像素效果媒体源。",
            "layout-grid",
            (("添加第一张幻灯片", self._add_slide, "plus", True),
             ("载入媒体（视频）", self._on_empty_load_media, "video", False)))

    def _on_empty_load_fixtures(self) -> None:
        """空态入口：从工程载入全部设备（失败时给出原因，不静默）。"""
        self._bootstrap_from_pm()
        self._rebuild_thumbs()
        if not self._fixtures:
            QMessageBox.information(
                self, "尚未载入设备",
                "工程内没有可载入的设备；请先在 TopView 或设备组面板添加设备。")

    def _on_empty_load_media(self) -> None:
        """空态入口：导入视频 → 切像素模式并选「视频像素采样」效果。

        用户取消导入时给一次说明（空态点击后什么都不发生 = 像坏了）；
        导入失败已在 `_on_video_import` 内弹警告，这里不重复。
        """
        result = self._pixel_param_panel._on_video_import()
        if result == "cancelled":
            QMessageBox.information(
                self, "载入媒体",
                "未选择任何视频文件；矩阵像素采样支持 mp4/mov/avi/mkv/webm/m4v。")
            return
        if result != "ok":
            return
        # 切像素模式 + 打开/置前像素独立窗口（R183）
        self._pixel_mode_btn.setChecked(True)      # 触发 _on_pixel_mode_toggled → 开窗
        self.show_pixel_window()                   # 已是像素模式时补一次置前
        idx = self._pixel_effect_combo.findData(MODE_VIDEO_SAMPLE)
        if idx >= 0:
            self._pixel_effect_combo.setCurrentIndex(idx)

    def _sync_slides_to_current_fixtures(self):
        """Keep existing PPT frames keyed to the currently selected fixture group."""
        if not getattr(self, "_engine", None):
            return
        current_ids = [f.get("id", "") for f in self._fixtures if f.get("id")]
        if not current_ids:
            return
        current_set = set(current_ids)
        for slide in self._engine.slides:
            old_states = dict(getattr(slide, "states", {}) or {})
            old_ids = list(old_states.keys())
            if set(old_ids) == current_set:
                #  灰度帧：原样保留 int 0-255（bool 旧工程也原样保留）
                slide.states = {fid: old_states.get(fid, False) for fid in current_ids}
                continue
            old_values = [old_states.get(fid, False) for fid in old_ids]
            if not old_values:
                slide.states = {fid: False for fid in current_ids}
                continue
            remapped = {}
            old_len = len(old_values)
            new_len = len(current_ids)
            for i, fid in enumerate(current_ids):
                src_idx = min(old_len - 1, int(round(i * max(0, old_len - 1) / max(1, new_len - 1))))
                remapped[fid] = old_values[src_idx]
            slide.states = remapped

    def _update_thumb_selection(self):
        idx = self._engine.current_idx
        for i, thumb in enumerate(self._thumbs):
            thumb.set_selected(i == idx)

    def _on_thumb_clicked(self, idx: int):
        if self._slice_mode_enabled():
            if 0 <= idx < len(self._engine.slides):
                self._engine._current_idx = idx
                self._engine._frame_elapsed = 0.0
                self._engine._in_transition = False
        else:
            self._engine.goto(idx)
        self._update_thumb_selection()
        # 更新画布显示
        if 0 <= idx < len(self._engine.slides):
            slide = self._engine.slides[idx]
            if self._slice_mode_enabled():
                self._manual_slice_states = dict(slide.states)
            self._canvas.set_states(slide.states)
            self._slide_param_panel.load_slide(slide)
            if self._slice_mode_enabled() and self._slice_output_enabled():
                self._output_pixel_states(slide.states)

    def _on_thumb_double_clicked(self, idx: int):
        """双击缩略图：将当前画布状态更新到该帧"""
        if 0 <= idx < len(self._engine.slides):
            slide = self._engine.slides[idx]
            slide.states = dict(self._canvas.get_states())
            _mark_panel_dirty(self)          # R181：帧内容改动 → 置脏
            self._thumbs[idx].update_slide(slide)
            self._thumbs[idx].update()

    # ── 幻灯片操作 ────────────────────────────────────────────────────────────

    def _on_import_image_frames(self) -> int:
        """ R183 灰度帧：图片夹批量导入（每图一帧 0-255 灰度）；返回新增帧数。"""
        from .matrix_gray_frames import import_images_as_slides
        added = import_images_as_slides(self)
        if added:
            self._rebuild_thumbs()
        return int(added or 0)

    def _on_detect_grid(self):
        """ R183 网格检测：聚类识别行列，可选吸附坐标；返回检测结果或 None。"""
        from .matrix_grid_detect import detect_and_apply_grid
        result = detect_and_apply_grid(self)
        if result is not None:
            self._rebuild_thumbs()
        return result

    def _add_frame_ext_buttons(self, lay) -> None:
        """帧操作扩展：画笔力度（灰度帧）+ 图片帧导入 + 网格检测（ R183）。

        由 `matrix_zone_subpages._build_frame_ops_section` 调用（布局模块保持
        ≤500 行，控件构造留在面板侧）。
        """
        lay.addWidget(QLabel("画笔:"))
        brush = QSpinBox()
        brush.setRange(1, 255)
        brush.setValue(int(self._canvas.brush_level()))
        brush.setFixedWidth(52)
        brush.setToolTip("点选/左键框选写入的灰度（1-255）；右键框选仍为 0（灭）")
        brush.valueChanged.connect(self._canvas.set_brush_level)
        lay.addWidget(brush)
        for label, slot, tip, icon_key in (
                ("导入图片帧", self._on_import_image_frames,
                 "多选图片或选文件夹：按设备坐标采样亮度，每张图生成一帧灰度帧", "image"),
                ("网格检测", self._on_detect_grid,
                 "按设备坐标聚类识别列×行网格；可选把坐标吸附到规整网格", "layout_grid")):
            btn = self._mk_btn(label, BG_CARD, FG_DIM, icon_key=icon_key)
            btn.setFixedHeight(22)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            lay.addWidget(btn)
        #  W2 #14：帧库导出 / 导入 .json（跨工程分享帧序列模板）
        matrix_w2_ui.extend_frame_ops_with_library(self, lay)

    def _add_slide(self):
        """添加空帧"""
        from .matrix_sequence_edit_commands import add_blank_frame
        add_blank_frame(self)

    def _save_as_new_slide(self):
        """将当前画布状态保存为新幻灯片"""
        states = self._canvas.get_states()
        on_count = sum(1 for v in states.values() if v)
        slide = MatrixSlide(
            name=f"帧 {len(self._engine.slides) + 1} (ON:{on_count})",
            states=dict(states),
        )
        idx = self._engine.add_slide(slide)
        _mark_panel_dirty(self)              # R181：帧序列改动 → 置脏
        self._rebuild_thumbs()
        self._on_thumb_clicked(idx)

    def _update_current_slide(self):
        """将当前画布状态更新到当前选中帧"""
        idx = self._engine.current_idx
        if 0 <= idx < len(self._engine.slides):
            slide = self._engine.slides[idx]
            slide.states = dict(self._canvas.get_states())
            _mark_panel_dirty(self)          # R181：帧内容改动 → 置脏
            if idx < len(self._thumbs):
                self._thumbs[idx].update_slide(slide)
                self._thumbs[idx].update()

    def _dup_slide(self):
        from .matrix_sequence_edit_commands import copy_frame
        copy_frame(self)

    def _del_slide(self):
        from .matrix_sequence_edit_commands import delete_frame
        delete_frame(self)

    def _move_slide(self, direction: int):
        from .matrix_sequence_edit_commands import move_frame
        move_frame(self, direction)

    def _rename_slide(self):
        idx = self._engine.current_idx
        if idx < 0 or idx >= len(self._engine.slides):
            return
        slide = self._engine.slides[idx]
        dlg = QDialog(self)
        dlg.setWindowTitle("重命名幻灯片")
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(12, 12, 12, 10)
        edit = QLineEdit(slide.name)
        lay.addWidget(edit)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        cancel_btn = btns.button(QDialogButtonBox.StandardButton.Cancel)
        if ok_btn is not None:
            ok_btn.setObjectName("primary")
        if cancel_btn is not None:
            cancel_btn.setProperty("role", "cancel")
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        lay.addWidget(btns)
        apply_dialog_chrome(dlg)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            from .matrix_sequence_edit_commands import rename_frame
            rename_frame(self, edit.text())

    def _apply_global_hold(self):
        """将全局停留时长应用到所有帧。"""
        hold = float(self._global_hold_spin.value())
        for slide in self._engine.slides:
            slide.hold_time = hold
        _mark_panel_dirty(self)
        self._rebuild_thumbs()
        idx = self._engine.current_idx
        if 0 <= idx < len(self._engine.slides):
            self._slide_param_panel.load_slide(self._engine.slides[idx])

    def _apply_global_transition(self):
        """将全局过渡时长应用到所有帧。"""
        trans = float(self._global_trans_spin.value())
        for slide in self._engine.slides:
            slide.transition_time = trans
        _mark_panel_dirty(self)
        self._rebuild_thumbs()
        idx = self._engine.current_idx
        if 0 <= idx < len(self._engine.slides):
            self._slide_param_panel.load_slide(self._engine.slides[idx])

    def _load_preset(self):
        """加载内置序列预设"""
        from .matrix_ppt_catalog import selected_sequence_key
        name = selected_sequence_key(self._preset_combo)
        if name.startswith("──"):
            return
        if not self._fixtures:
            QMessageBox.warning(self, "提示", "请先选择设备组")
            return
        wings = int(getattr(self, '_wings_spin', None) and self._wings_spin.value() or 1)
        block_size = int(getattr(self, '_block_spin', None) and self._block_spin.value() or 1)
        wing_direction = normalize_wing_direction(getattr(self, '_wing_direction', WING_DIRECTION_LOW_TO_HIGH))
        density = float(getattr(self, '_ppt_density_spin', None) and self._ppt_density_spin.value() or 0.35)
        _bake_preset_into_engine(self, name, wings=wings, block_size=block_size,
                                 wing_direction=wing_direction, density=density)
        if self._engine.slides:
            self._on_thumb_clicked(0)
        else:
            QMessageBox.warning(self, "内置序列",
                                f"序列「{name}」无法生成，请确认已选择设备组且设备数量大于0。")

    # ── 画布事件 ──────────────────────────────────────────────────────────────

    def _on_canvas_changed(self):
        """画布 ON/OFF 状态变化 → 实时输出（如果在 PPT 模式且未播放）。

         2026-09-18 回访修正（门槛漏一半 → ppt 手绘存不进场景）：本路径经
          `_output_pixel_states` 写输出，而只有 `_output_pixel_values` 那条实现
          置 `_bus_user_touched` → ppt 模式下**用户在画布上画**会真的写 DMX
          （实测 matrix 层 addr 2/10/18 = 255），但 `_bus_has_content()` 为假
          （无 slides、未播放）→ `_bus_nodes()` 返回空 → 调度台看不到这一格，
          **存不进场景**（实测 `_current_effect_nodes()` 为空 → 静默丢创作）。
          这里在"用户确实改了画布"这个入口补上门槛标记（与"用户主动播放"同款语义）。
        """
        try:
            self._bus_user_touched = True
        except Exception:
            pass
        if self._slice_mode_enabled():
            states = self._canvas.get_states()
            self._manual_slice_states = dict(states)
            self._sync_sample_preview_on_off(states)
            if self._slice_output_enabled():
                self._output_pixel_states(states)
            return
        if self._mode == "ppt" and not self._engine.is_playing:
            # 实时预览：直接输出当前画布状态
            states = self._canvas.get_states()
            self._sync_sample_preview_on_off(states)
            self._output_pixel_states(states)

    # ── 播放控制 ──────────────────────────────────────────────────────────────

    def _on_play_toggled(self, checked: bool):
        if getattr(self, "_pixel_transition_busy", False) and self._mode == "pixel":
            return
        # 用户主动播放 = 真有内容（供调度台上报门槛判定）
        if checked:
            _running_outputs.activate(self)
        if self._slice_mode_enabled():
            if checked:
                if self._mode == "ppt":
                    try:
                        self._engine.pause()
                    except Exception:
                        pass
                    self._load_current_keyed_slice()
                elif self._mode == "pixel":
                    self._pause_pixel_mode(clear_output=False)
                    self._load_current_keyed_slice()
            else:
                if self._mode == "ppt":
                    try:
                        self._engine.pause()
                    except Exception:
                        pass
                elif self._mode == "pixel":
                    self._pause_pixel_mode(clear_output=False)
            self._sync_sample_preview_on_off(None)
            self._set_play_button_text(bool(self._play_btn.isChecked()))
            return
        if checked:
            if self._mode == "ppt":
                self._reset_matrix_output_state()
                self._engine.play()
            elif self._mode == "pixel":
                self._start_pixel_mode()
        else:
            if self._mode == "ppt":
                self._engine.pause()
            elif self._mode == "pixel":
                self._pause_pixel_mode(clear_output=False)
        self._set_play_button_text(bool(self._play_btn.isChecked()))

    def _stop(self):
        if self._slice_mode_enabled():
            self._play_btn.setChecked(False)
            if self._mode == "ppt":
                try:
                    self._engine.pause()
                except Exception:
                    pass
            elif self._mode == "pixel":
                self._pause_pixel_mode(clear_output=False)
            self._set_play_button_text(False)
            return
        self._manual_slice_latched = False
        self._sync_sample_preview_on_off(None)
        self._play_btn.setChecked(False)
        if self._mode == "ppt":
            _running_outputs.stop_slide(self)
            self._reset_matrix_output_state(clear_pattern=True)
        elif self._mode == "pixel":
            self._stop_pixel_mode(full_release=False, clear_output=True)
        self._set_play_button_text(False)

    def _add_help_button(self, toolbar) -> None:
        """工具栏「?」帮助按钮（分隔符 + 按钮）；由主界面底部通用控件行调用
        （R182 拆页后该行在 `matrix_zone_subpages._build_main_bottom_row`）。"""
        toolbar.addWidget(self._mk_sep())
        help_btn = self._mk_btn("?", "#2D2D2D", ACCENT, icon_key="help")
        help_btn.setFixedWidth(32)
        help_btn.setToolTip("矩阵幻灯片帮助（过渡模式说明）")
        help_btn.clicked.connect(self._show_help)
        toolbar.addWidget(help_btn)
        #  R182：输出平滑（Matrix 输出全局淡入淡出整形）弹层入口——
        #   与帮助按钮同挂在主界面底部通用控件行（见 matrix_zone_subpages）。
        shape_btn = self._mk_btn("输出平滑", "#2D2D2D", ACCENT)
        shape_btn.setToolTip("矩阵输出全局平滑：淡入(attack)/淡出(release) 秒数与曲线，\n"
                             "预览画面与实际输出同步整形")
        shape_btn.clicked.connect(self._open_output_shape_dialog)
        toolbar.addWidget(shape_btn)
        #  R183 #8：每分区输出 Gamma LUT 弹层入口（弹层在 matrix_output_lut）
        gamma_btn = self._mk_btn("分区 Gamma", "#2D2D2D", ACCENT)
        gamma_btn.setToolTip("按设备高度分区各挂一张 gamma 查表（混装灯一致性）；\n"
                             "预览与 NM 输出同一曲线，改动即时生效")
        gamma_btn.clicked.connect(self._open_output_gamma_dialog)
        toolbar.addWidget(gamma_btn)
        #  W2：录制输出(#12) / 输出电平侧窗(#13) / 串口桥接说明(#17)
        matrix_w2_ui.add_main_ext_buttons(self, toolbar)

    def _open_output_gamma_dialog(self) -> None:
        """打开「输出 Gamma」弹层（非模态；关闭/改动由 matrix_output_lut 处理）。"""
        from .matrix_output_lut import open_gamma_dialog
        open_gamma_dialog(self)
        self._sync_output_gamma()

    def _sync_output_gamma(self) -> None:
        """把 config.output_gamma 下推到幻灯片/像素两引擎的分区缓存（存储读回也走此）。

         W2：本方法也是 `load_state` 的尾部统一同步点（BPM 切帧控件与外部纹理源
        下拉按引擎/config 回填，幂等）。
        """
        cfg = getattr(getattr(self, "_engine", None), "config", None)
        gamma_cfg = getattr(cfg, "output_gamma", None) if cfg is not None else None
        px = getattr(self, "_pixel_engine", None)
        if px is not None and hasattr(px, "set_output_gamma"):
            try:
                px.set_output_gamma(gamma_cfg)
            except Exception:
                logger.debug("matrix: pixel engine gamma sync failed", exc_info=True)
        try:
            if self._engine is not None:
                self._engine._rebuild_output_gamma_zone()
        except Exception:
            logger.debug("matrix: slide engine gamma rebuild failed", exc_info=True)
        try:
            matrix_w2_ui.sync_w2_widgets(self)      #  W2：BPM / 外部纹理源控件回填
        except Exception:
            logger.debug("matrix: w2 widget sync failed", exc_info=True)

    def _show_help(self) -> None:
        """显示矩阵幻灯片帮助对话框（过渡模式详解）。"""
        from src.ui.help_dialogs import show_help_dialog, MATRIX_SLIDESHOW_HELP_HTML
        show_help_dialog(self, "矩阵幻灯片帮助", MATRIX_SLIDESHOW_HELP_HTML)

    # ── R178 输出平滑：Matrix 输出全局淡入淡出整形（预览 == NM 输出） ─────────

    def _shape_output_weights(self, weights):
        """目标权重 → 平滑后权重（enabled=False 时原样透传，逐字节行为不变）。

        预览画布与 NM 写层共用同一 ``_output_shaper`` 实例 → 同一视觉；
        dt 取相邻两次引擎输出的墙钟差（钳到 0.1s，防暂停后一帧走完全程）。
        """
        try:
            shaper = getattr(self, "_output_shaper", None)
            if shaper is None:
                shaper = OutputShaper()
                self._output_shaper = shaper
            now = time.monotonic()
            last = getattr(self, "_output_shaper_ts", None)
            self._output_shaper_ts = now
            dt = 0.0 if last is None else min(max(now - last, 0.0), 0.1)
            p = params_from_dict(getattr(self._engine.config, "output_shape", None))
            return shaper.feed(weights or {}, dt, p)   #  L1：禁用透传/reset 语义在 feed 内
        except Exception:
            return weights

    def _open_output_shape_dialog(self) -> None:
        """「输出平滑」设置弹层：启用勾选 + attack/release（0–3s）+ 曲线下拉 + 恢复默认。

        非模态、改动即时生效（信号直写 ``config.output_shape``，下一帧输出即吃到）；
        持久化走面板既有 save_state/load_state（slideshow_engine.config blob）。
        """
        from PyQt6.QtWidgets import QFormLayout
        old = getattr(self, "_osh_dialog", None)
        if old is not None and old.isVisible():
            old.raise_()
            old.activateWindow()
            return
        cfg = normalize_output_shape_dict(getattr(self._engine.config, "output_shape", None))
        dlg = QDialog(self)
        dlg.setWindowTitle("输出平滑（矩阵全局淡入淡出）")
        dlg.setModal(False)
        lay = QVBoxLayout(dlg)
        chk = QCheckBox("启用输出平滑（预览与输出同步整形）")
        chk.setChecked(bool(cfg.get("enabled")))
        form = QFormLayout()
        attack = QDoubleSpinBox()
        attack.setRange(0.0, 3.0)
        attack.setDecimals(2)
        attack.setSingleStep(0.05)
        attack.setSuffix(" s")
        attack.setValue(float(cfg.get("attack", 0.3)))
        attack.setToolTip("淡入时间：权重从 0 爬到目标的秒数（0=即时）")
        release = QDoubleSpinBox()
        release.setRange(0.0, 3.0)
        release.setDecimals(2)
        release.setSingleStep(0.05)
        release.setSuffix(" s")
        release.setValue(float(cfg.get("release", 0.6)))
        release.setToolTip("淡出时间：权重落回 0 的秒数（0=即时）")
        curve = QComboBox()
        for key, label in CURVE_CHOICES:
            curve.addItem(label, key)
        curve.setCurrentIndex(max(0, curve.findData(str(cfg.get("curve")))))
        curve.setToolTip("爬升/衰减的进度曲线（与全局输出平滑同一套缓动函数）")
        form.addRow("淡入 attack:", attack)
        form.addRow("淡出 release:", release)
        form.addRow("曲线:", curve)
        reset_btn = self._mk_btn("恢复默认", "#2D2D2D", ACCENT)
        reset_btn.setToolTip("恢复默认：关闭 + 淡入 0.30s + 淡出 0.60s + 缓入缓出")
        hint = QLabel("对 Matrix 输出整体做全局平滑淡入淡出；\n"
                      "预览画布显示的就是实际写出的平滑值。")
        hint.setStyleSheet(f"color:{FG_DIM};font-size:10px;")
        lay.addWidget(chk)
        lay.addLayout(form)
        lay.addWidget(hint)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(reset_btn)
        lay.addLayout(btn_row)
        self._osh_widgets = {"enabled": chk, "attack": attack,
                             "release": release, "curve": curve}
        chk.toggled.connect(self._osh_apply_ui_to_config)
        attack.valueChanged.connect(self._osh_apply_ui_to_config)
        release.valueChanged.connect(self._osh_apply_ui_to_config)
        curve.currentIndexChanged.connect(self._osh_apply_ui_to_config)
        reset_btn.clicked.connect(self._osh_reset_defaults)
        dlg.finished.connect(lambda _result: setattr(self, "_osh_dialog", None))
        dlg.finished.connect(dlg.deleteLater)   #  审计 L3：防弹层实例累积
        self._osh_dialog = dlg
        dlg.show()

    def _osh_apply_ui_to_config(self, *_) -> None:
        """弹层控件值 → ``engine.config.output_shape``（改动即时生效）。"""
        w = getattr(self, "_osh_widgets", None)
        if not w:
            return
        cfg = normalize_output_shape_dict(getattr(self._engine.config, "output_shape", None))
        cfg["enabled"] = bool(w["enabled"].isChecked())
        cfg["attack"] = round(float(w["attack"].value()), 3)
        cfg["release"] = round(float(w["release"].value()), 3)
        cfg["curve"] = str(w["curve"].currentData() or "linear")
        self._engine.config.output_shape = cfg
        _mark_panel_dirty(self)              # R181：弹层改参数 → 置脏（否则存盘落不下）

    def _osh_reset_defaults(self) -> None:
        """恢复默认参数（控件信号会连带把默认值写回 config）。"""
        w = getattr(self, "_osh_widgets", None)
        if not w:
            return
        w["enabled"].setChecked(bool(OUTPUT_SHAPE_DEFAULTS["enabled"]))
        w["attack"].setValue(float(OUTPUT_SHAPE_DEFAULTS["attack"]))
        w["release"].setValue(float(OUTPUT_SHAPE_DEFAULTS["release"]))
        idx = w["curve"].findData(str(OUTPUT_SHAPE_DEFAULTS["curve"]))
        w["curve"].setCurrentIndex(max(0, idx))

    def _step_frame(self, direction: int):
        if not self._slice_mode_enabled():
            self._manual_slice_latched = False
        if self._mode == "ppt":
            if self._slice_mode_enabled():
                if self._engine.slides:
                    if direction > 0:
                        idx = (self._engine.current_idx + 1) % len(self._engine.slides)
                    else:
                        idx = (self._engine.current_idx - 1) % len(self._engine.slides)
                    self._engine._current_idx = idx
                    self._engine._frame_elapsed = 0.0
                    self._engine._in_transition = False
            else:
                if direction > 0:
                    self._engine.step_next()
                else:
                    self._engine.step_prev()
            idx = self._engine.current_idx
            self._update_thumb_selection()
            if 0 <= idx < len(self._engine.slides):
                states = self._engine.slides[idx].states
                if self._slice_mode_enabled():
                    self._manual_slice_states = dict(states)
                self._canvas.set_states(states)
                self._sync_sample_preview_on_off(states if self._slice_mode_enabled() else None)
                if not self._slice_mode_enabled() or self._slice_output_enabled():
                    self._output_pixel_states(states)

    def _on_play_mode_changed(self, idx: int):
        mode = self._play_mode_combo.itemData(idx)
        if mode:
            self._engine.config.play_mode = mode

    # ── 工作模式切换（R183：按钮 = 开窗 + 置前） ─────────────────────────────

    def _on_ppt_btn_clicked(self, _checked: bool = False) -> None:
        """幻灯片按钮点击：打开/置前独立窗口（工作模式由 toggled 回调负责）。"""
        self.show_ppt_window()

    def _on_pixel_btn_clicked(self, _checked: bool = False) -> None:
        """像素效果按钮点击：打开/置前独立窗口。"""
        self.show_pixel_window()

    def _on_ppt_mode_toggled(self, checked: bool):
        if checked:
            was_pixel = self._mode == "pixel"
            #  R183 开窗语义：切回 PPT 模式 → 打开/置前「幻灯片」独立窗口
            #   （像素窗口不自动关：两窗可同时在场，各自独立布局）。
            self.show_ppt_window()
            self._manual_slice_states = None
            self._sync_sample_preview_on_off(None)
            try:
                self._pixel_mode_btn.blockSignals(True)
                self._pixel_mode_btn.setChecked(False)
            finally:
                self._pixel_mode_btn.blockSignals(False)
            self._mode = "ppt"
            self._canvas.clear_pixel_mode()
            self._stop_pixel_mode(full_release=True, clear_output=was_pixel)
            self._set_play_button_text(False)

    def _on_pixel_mode_toggled(self, checked: bool):
        if not checked and self._mode == "pixel":
            # Clicking the current mode reopens its editor without retiring output.
            from PyQt6.QtCore import QSignalBlocker
            with QSignalBlocker(self._pixel_mode_btn):
                self._pixel_mode_btn.setChecked(True)
            return
        # 当前 Pixel 入口重复点击只开窗；PPT 按钮负责真正切换模式。
        if checked:
            self.show_pixel_window()
        else:
            self.show_ppt_window()
        if checked:
            self._manual_slice_states = None
            self._sync_sample_preview_on_off(None)
            try:
                self._ppt_mode_btn.blockSignals(True)
                self._ppt_mode_btn.setChecked(False)
            finally:
                self._ppt_mode_btn.blockSignals(False)
            self._mode = "pixel"
            self._recalled_play = False      # 用户切换模式 = 编辑
            if self._slice_mode_enabled():
                self._engine.pause()
            else:
                self._engine.stop()
        else:
            self._stop_pixel_mode(full_release=True, clear_output=True)
        self._set_play_button_text(False)

    def _start_pixel_mode(self):
        # 用户启动像素模式 = 真有内容（供调度台上报门槛判定）
        self._bus_user_touched = True
        self._recalled_play = False      # 用户主动播放 = 编辑（可被保存）
        try:
            self._pixel_resource_release_timer.stop()
        except Exception:
            pass
        _is_video_mode = getattr(self._pixel_engine, '_mode', '') == MODE_VIDEO_SAMPLE
        # 视频采样模式下允许在设备为空时先播放视频（设备加载后会自动同步）
        if not self._fixtures and not _is_video_mode:
            QMessageBox.warning(self, "提示", "请先选择设备组")
            self._play_btn.setChecked(False)
            return
        if _is_video_mode:
            _ext_src = matrix_w2_ui.external_source_selected(self._pixel_engine)
            if not _ext_src and not getattr(self._pixel_engine, 'video_path', ''):
                QMessageBox.warning(self, "提示", "请先导入视频（或选择外部纹理源）")
                self._play_btn.setChecked(False)
                return
            self._pixel_engine.video_playing = True
        self._sync_wing_block_to_pixel_engine()
        self._reset_matrix_output_state()
        was_paused = bool(getattr(self, "_pixel_paused", False))
        self._pixel_running = True
        self._pixel_paused = False
        self._pixel_last_t = time.monotonic()
        try:
            self._pixel_engine.start(reset_state=not was_paused)
        except TypeError:
            self._pixel_engine.start()

    def _pause_pixel_mode(self, clear_output: bool = False):
        self._pixel_running = False
        self._pixel_paused = True
        try:
            if hasattr(self._pixel_engine, "pause"):
                self._pixel_engine.pause()
            else:
                self._pixel_engine.stop()
        except Exception:
            pass
        if not clear_output:
            try:
                self._pixel_resource_release_timer.start(1200)
            except Exception:
                pass
            return
        if self._slice_mode_enabled():
            return
        self._canvas.clear_pixel_mode()
        # 清空 NM matrix 层
        if self._nm_ref is not None:
            for layer_name in ("matrix", "matrix_audio"):
                try:
                    _running_outputs.clear_current(self, (layer_name,))
                except Exception:
                    pass
        self._reset_matrix_output_state(clear_pattern=True)

    def _stop_pixel_mode(self, full_release: bool = True, clear_output: bool = True):
        try:
            self._pixel_resource_release_timer.stop()
        except Exception:
            pass
        self._pixel_running = False
        self._pixel_paused = False
        try:
            self._pixel_effect_apply_timer.stop()
        except Exception:
            pass
        self._pending_pixel_mode_id = None
        try:
            self._pixel_engine.stop(full_release=full_release)
        except TypeError:
            self._pixel_engine.stop()
        except Exception:
            pass
        if not full_release:
            try:
                self._pixel_resource_release_timer.start(1200)
            except Exception:
                pass
        if not clear_output or self._slice_mode_enabled():
            return
        self._canvas.clear_pixel_mode()
        # 清空 NM matrix 层
        if self._nm_ref is not None:
            for layer_name in ("matrix", "matrix_audio"):
                try:
                    _running_outputs.clear_current(self, (layer_name,))
                except Exception:
                    pass
        self._reset_matrix_output_state(clear_pattern=True)

    def _release_idle_pixel_resources(self):
        if self._mode != "pixel" or bool(getattr(self, "_pixel_running", False)):
            return
        try:
            if hasattr(self._pixel_engine, "stop"):
                self._pixel_engine.stop(full_release=True)
        except TypeError:
            try:
                self._pixel_engine.stop()
            except Exception:
                # R181 P2-3：闲置释放失败 → 像素资源未归还（视频句柄等），留痕
                logger.debug("闲置释放像素引擎失败（旧签名回退路径）", exc_info=True)
        except Exception:
            # R181 P2-3：同上（full_release 路径）
            logger.debug("闲置释放像素引擎失败（full_release=True）", exc_info=True)

    def _sync_wing_block_to_pixel_engine(self):
        _wing_flags.sync_pixel(self)

    def _on_pixel_effect_changed(self, idx: int):
        mode_id = self._pixel_effect_combo.itemData(idx)
        if not mode_id:
            return
        self._pending_pixel_mode_id = str(mode_id)
        try:
            self._pixel_effect_apply_timer.start(30)
        except Exception:
            self._apply_pending_pixel_effect()

    def _apply_pending_pixel_effect(self):
        mode_id = self._pending_pixel_mode_id
        self._pending_pixel_mode_id = None
        if not mode_id:
            return
        if getattr(self, "_pixel_transition_busy", False):
            self._pending_pixel_mode_id = mode_id
            try:
                self._pixel_effect_apply_timer.start(30)
            except Exception:
                pass
            return
        self._pixel_transition_busy = True
        was_running = bool(getattr(self, "_pixel_running", False))
        try:
            self._sync_wing_block_to_pixel_engine()
            #  W2 #4/#6：外部纹理登记位（ext_spout/ext_ndi）→ 视频采样 + 帧源
            mode_id = matrix_w2_ui.apply_mode_selection(self._pixel_param_panel, mode_id)
            self._pixel_engine.audio_controls_version = 1
            self._pixel_engine.set_mode(mode_id)
            self._pixel_param_panel.show_mode_params(mode_id)
            self._reset_matrix_output_state()
            self._last_pixel_preview_update = 0.0
            self._last_screen_preview_update = 0.0
            if was_running:
                self._pixel_last_t = time.monotonic()
                try:
                    if hasattr(self._pixel_engine, "refresh_runtime_resources"):
                        self._pixel_engine.refresh_runtime_resources()
                except Exception:
                    pass
        finally:
            self._pixel_transition_busy = False

    # ── 参数变化 ──────────────────────────────────────────────────────────────

    def _on_slide_params_changed(self):
        _mark_panel_dirty(self)              # R181：帧参数改动 → 置脏
        idx = self._engine.current_idx
        if 0 <= idx < len(self._thumbs):
            self._thumbs[idx].update_slide(self._engine.slides[idx])
            self._thumbs[idx].update()

    def _on_pixel_params_changed(self):
        self._pixel_engine.audio_controls_version = 1
        from .matrix_pixel_audio_modes import configure_bar_controls
        configure_bar_controls(self._pixel_param_panel)
        _mark_panel_dirty(self)              # R181：像素参数改动 → 置脏
        if self._slice_mode_enabled():
            self._manual_slice_states = None
        self._reset_matrix_output_state()
        if self._mode == "pixel":
            try:
                if hasattr(self._pixel_engine, "refresh_runtime_resources"):
                    self._pixel_engine.refresh_runtime_resources()
            except Exception:
                pass
        self._refresh_current_matrix_output()

    def _on_matrix_effect_color_changed(self):
        self._clear_matrix_output_layers()
        self._refresh_current_matrix_output()

    def _on_pixel_video_cleared(self):
        """视频源被删除时，停止统一播放并清空视频采样输出状态。"""
        try:
            if getattr(self._pixel_engine, '_mode', '') == MODE_VIDEO_SAMPLE:
                if getattr(self, '_pixel_running', False):
                    self._stop_pixel_mode()
                if hasattr(self, '_play_btn'):
                    self._play_btn.setChecked(False)
                    self._set_play_button_text(False)
        except Exception:
            # R181 P2-3：视频删除后没停住 → 采样输出可能继续（停不掉），留痕
            logger.debug("视频源删除后的停止/清状态失败（输出可能未停）", exc_info=True)

    def _on_ch_type_changed(self, idx: int):
        restore_channel_type(self, self._ch_type_combo.itemData(idx))
        if not getattr(self, '_channel_addr_cache', None):
            self._rebuild_channel_addr_cache()
        self._clear_matrix_output_layers()
        self._reset_matrix_output_state()
        self._refresh_current_matrix_output()

    def _refresh_current_matrix_output(self):
        """Re-emit the current matrix frame after output-affecting parameter changes."""
        if self._nm_ref is None or not self._fixtures:
            return
        if self._slice_mode_enabled() and not self._slice_output_enabled():
            return
        try:
            if self._mode == "ppt":
                states = self._canvas.get_states()
                self._sync_sample_preview_on_off(states if self._slice_mode_enabled() else None)
                self._output_pixel_states(states)
                return
            if self._mode != "pixel":
                return
            refresh_paused_video_canvas(self)
            if getattr(self._canvas, "_pixel_mode", False):
                values = getattr(self._canvas, "_pixel_values", None)
                if values is not None:
                    self._output_pixel_values(np.asarray(values, dtype=np.uint8))
                    return
                weights = getattr(self._canvas, "_pixel_weights", {}) or {}
                if weights:
                    fixture_order = getattr(self, "_matrix_fixture_order", {}) or {}
                    vals = np.zeros(len(fixture_order), dtype=np.uint8)
                    for fid, idx in fixture_order.items():
                        w = max(0.0, min(1.0, float(weights.get(str(fid), 0.0) or 0.0)))
                        vals[int(idx)] = int(round(w * 255.0))
                    self._output_pixel_values(vals)
                    return
            if not bool(getattr(self, "_pixel_running", False)):
                return
            values = self._pixel_engine.tick_values(0.0) if hasattr(self._pixel_engine, "tick_values") else None
            if values is not None:
                self._canvas.set_pixel_values(values, self._pixel_engine.threshold)
                self._output_pixel_values(values)
            else:
                states = self._pixel_engine.tick(0.0) or {}
                self._canvas.set_pixel_weights(
                    {fid: (1.0 if on else 0.0) for fid, on in states.items()},
                    self._pixel_engine.threshold,
                )
                self._output_pixel_states(states)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Matrix current output refresh error: %s", e)
    # ── 状态持久化 ────────────────────────────────────────────

    def to_dict(self):
        return {
            "engine": self._engine.to_dict(),
            "ch_type": self._ch_type,
            "mode": self._mode,
            "slice_key": self._slice_key,
            "slice_key_modifiers": int(getattr(self, '_slice_key_modifiers', 0) or 0),
            "manual_pixel_slice_index": int(getattr(self, '_manual_pixel_slice_index', 0) or 0),
        }

    def from_dict(self, d: Dict):
        self._engine.from_dict(d.get("engine", {}))
        restore_channel_type(self, d.get("ch_type", CH_PUMP))
        self._mode = d.get("mode", "ppt")
        try:
            key = d.get("slice_key", None)
            self._slice_key = int(key) if key is not None else None
            self._slice_key_modifiers = int(d.get("slice_key_modifiers", 0) or 0)
            self._manual_pixel_slice_index = int(d.get("manual_pixel_slice_index", 0) or 0)
            self._update_slice_key_label()
        except Exception:
            pass
        self._rebuild_thumbs()

    # ── 时间线联动 ────────────────────────────────────────────────────────────

    def on_timeline_play(self, *args):
        """时间线开始播放——矩阵播放完全独立，不随时间线联动。"""
        pass

    def on_timeline_stop(self, *args):
        """时间线停止——矩阵播放完全独立，不随时间线停止。"""
        pass

    def apply_managed_pixel(self, mode: Optional[str]):
        """供"自动编曲"托管驱动：切到像素效果模式 + 设引擎模式 + 播放/停。
        mode=None → 停止矩阵像素输出。mode=有效像素模式常量 → 启动。
        通过真实可勾选按钮触发既有受测路径，避免直接动私有状态。"""
        try:
            if mode is None:
                if self._mode == "pixel" and self._play_btn.isChecked():
                    self._play_btn.setChecked(False)   # 触发 _on_play_toggled→暂停/停止
                return
            # 切到像素模式（若尚未）——R183：带动画窗 + 置前，用户能看见在跑什么
            if not self._pixel_mode_btn.isChecked():
                self._pixel_mode_btn.setChecked(True)  # 触发 _on_pixel_mode_toggled
            self.show_pixel_window()
            # 设引擎效果模式
            if hasattr(self._pixel_engine, "set_mode"):
                self._pixel_engine.set_mode(mode)
            # 启动播放
            if not self._play_btn.isChecked():
                self._play_btn.setChecked(True)        # 触发 _on_play_toggled→_start_pixel_mode
        except Exception:
            pass

    def link_timeline_audio(self, path: str, dur: float = 0.0):
        """接收时间线参考轨音频路径，加载到共享 AudioAnalyzer 并注入像素引擎。

        调用来源：main_window 在 at.audio_loaded 信号触发时转发。
        """
        if not path:
            return
        try:
            from ..audio_driver import _ensure_valid_analyzer
            analyzer = _ensure_valid_analyzer()
            if analyzer is None:
                return
            analyzer.load_audio_file(path)      # 加载文件，自动切换到 file 模式
            if not analyzer._running:
                analyzer.start()
            # 注入像素引擎，替代 AudioCapture（麦克风/设备）
            self._pixel_engine.set_audio_analyzer(analyzer)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "MatrixSlideshowPanel.link_timeline_audio: %s", e)

    def on_timeline_tick(self, pos: float):
        """时间线播放位置实时转发 → AudioAnalyzer 文件模式跟指针读取片段。"""
        try:
            analyzer = getattr(self._pixel_engine, '_analyzer', None)
            if analyzer is not None:
                analyzer.set_timeline_position(float(pos))
        except Exception:
            pass


#  2026-09-18 声明理解 `for_edit` 语义位（dispatch_flow 据此决定是否传参）。
#   必须在**类定义之后**：在类体内写 `dispatch_recall_state` 时该名字尚未存在
#   （实测报 NameError），这也是本行此前写错位置的原因。
MatrixSlideshowPanel.dispatch_recall_state.__accepts_for_edit__ = True


def _apply_zone_gamma_states(panel, states):
    """帧状态 {fid: 0-255/bool} → 分区 gamma 查表后的同型 dict（未启用=原样）。

     #8：让画布/切片直出的 states 路径与值数组路径同一曲线；默认关闭时
    第一个判断即返回原对象，零开销、旧行为逐位不变。
    """
    eng = getattr(panel, "_pixel_engine", None)
    if eng is None or not getattr(eng, "_output_gamma_luts", None) or not states:
        return states
    try:
        weights = {fid: (_state_value(st) / 255.0) for fid, st in states.items()}
        shaped = eng.apply_output_gamma_weights(weights)
        return {fid: int(round(max(0.0, min(1.0, float(v))) * 255.0))
                for fid, v in shaped.items()}
    except Exception:
        return states


def _shape_pixel_states(panel, states):
    """像素模式输出的平滑整形（R178 审计 M4；从 _output_pixel_states 拆出）。

    像素/手动/切片模式的全部 7 个调用点都经 _output_pixel_states，故在此统一
    整形即可覆盖全部模式（PPT 路径经 _on_engine_output 的同款整形）。
     灰度帧：入参可为 int 0-255，整形后按比例回写 0-255（bool 旧语义 255/0
      数值逐位不变）。
    """
    try:
        from .output_shaper import params_from_dict
        p = params_from_dict(getattr(panel._engine.config, "output_shape", None))
        if p.enabled:
            weights = {fid: (_state_value(st) / 255.0)
                       for fid, st in (states or {}).items()}
            shaped = panel._shape_output_weights(weights)
            return {fid: int(round(max(0.0, min(1.0, float(v))) * 255.0))
                    for fid, v in shaped.items()}
    except Exception:
        pass
    return states


def _state_value(on) -> int:
    """帧状态 → DMX 值（ 灰度帧：bool True=255/False=0 逐位不变；int 0-255 原样）。

    helper 供面板侧 states→值 的两条路径复用（切片/像素 FAST 通路）。
    """
    if isinstance(on, (bool, np.bool_)):
        return 255 if on else 0
    try:
        return max(0, min(255, int(round(float(on)))))
    except (TypeError, ValueError):
        return 0


def _serialize_corners(corners):
    """四角透视角点 → JSON 可存列表（默认矩形/非启用 → None，不写多余键）。"""
    try:
        from .matrix_video_transforms import corners_to_payload
        return corners_to_payload(corners)
    except Exception:
        return None


def _collect_pixel_params(eng, panel) -> dict:
    """像素引擎 → save_state 的 `pixel_params` 字典（ R183 含视频扩展字段）。

    逐键与拆出前同口径（无关键一律 getattr + 硬默认，不用 `or` 短路以免
    误吞 0 值）；`panel` 仅用于 wing_direction 的面板回退。
    """
    from .matrix_pixel_music_catalog import collect_music_parameters
    return {**collect_music_parameters(eng),
        'threshold': eng.threshold,
        'speed': eng.speed,
        'invert': eng.invert,
        'pixel_audio_reactive': getattr(eng, 'pixel_audio_reactive', True),
        'audio_controls_version': getattr(eng, 'audio_controls_version', 0),
        'matrix_wave_density': getattr(eng, 'matrix_wave_density', 1.0),
        'matrix_wave_width': getattr(eng, 'matrix_wave_width', 0.12),
        'matrix_wave_twist': getattr(eng, 'matrix_wave_twist', 0.0),
        'matrix_symmetry_mix': getattr(eng, 'matrix_symmetry_mix', 0.5),
        'mode': getattr(eng, '_mode', ''),
        'screen_region': eng.screen_region,
        'screen_brightness_boost': eng.screen_brightness_boost,
        'screen_contrast': eng.screen_contrast,
        'screen_channel': getattr(eng, 'screen_channel', 'luminance'),
        'screen_smooth': getattr(eng, 'screen_smooth', 0.6),
        'screen_gamma': getattr(eng, 'screen_gamma', 1.0),
        'screen_threshold_low': getattr(eng, 'screen_threshold_low', 0.0),
        'screen_threshold_high': getattr(eng, 'screen_threshold_high', 1.0),
        'screen_fps': getattr(eng, 'screen_fps', 25.0),
        'video_path': getattr(eng, 'video_path', ''),
        #  R183 视频扩展：摄像头源/入出点/方向/速率/四角（掩码只存路径）
        'video_source': getattr(eng, 'video_source', 'file'),
        'camera_device': int(getattr(eng, 'camera_device', 0) or 0),
        'video_in_point': int(getattr(eng, 'video_in_point', 0) or 0),
        'video_out_point': int(getattr(eng, 'video_out_point', 0) or 0),
        'video_direction': getattr(eng, 'video_direction', 'forward'),
        'video_rate': float(getattr(eng, 'video_rate', 1.0) or 1.0),
        'video_warp_corners': _serialize_corners(
            getattr(eng, 'video_warp_corners', None)),
        'sample_mask_path': str(getattr(eng, 'sample_mask_path', '') or ''),
        'audio_amplitude': eng.audio_amplitude,
        'audio_device': eng.audio_device,
        'audio_norm_offset': getattr(eng, 'audio_norm_offset', 0.0),
        'audio_bar_direction': eng.audio_bar_direction,
        'audio_bar_style': getattr(eng, 'audio_bar_style', 'solid'),
        'audio_band_layout': getattr(eng, 'audio_band_layout', 'linear'),
        'audio_bar_gamma': getattr(eng, 'audio_bar_gamma', 0.85),
        'audio_bar_softness': getattr(eng, 'audio_bar_softness', 0.08),
        'audio_bar_smoothing': getattr(eng, 'audio_bar_smoothing', 0.35),
        'audio_bar_motion': getattr(eng, 'audio_bar_motion', 1.0),
        'audio_peak_hold': getattr(eng, 'audio_peak_hold', 0.45),
        'audio_peak_decay': getattr(eng, 'audio_peak_decay', 0.035),
        'matrix_audio_mod_target': getattr(eng, 'matrix_audio_mod_target', 'brightness_speed'),
        'matrix_audio_mod_source': getattr(eng, 'matrix_audio_mod_source', 'rms'),
        'matrix_audio_mod_amount': getattr(eng, 'matrix_audio_mod_amount', 0.6),
        'matrix_audio_mod_floor': getattr(eng, 'matrix_audio_mod_floor', 0.15),
        'matrix_audio_mod_attack_ms': getattr(eng, 'matrix_audio_mod_attack_ms', 35.0),
        'matrix_audio_mod_release_ms': getattr(eng, 'matrix_audio_mod_release_ms', 220.0),
        'matrix_effect_color': list(getattr(eng, 'matrix_effect_color', (255, 255, 255))),
        **_wing_flags.controls.values(eng),
        'wing_direction': normalize_wing_direction(
            getattr(eng, 'wing_direction',
                    getattr(panel, '_wing_direction', WING_DIRECTION_LOW_TO_HIGH))),
    }
