"""
matrix_pattern_panel.py — 幻灯片式空间图案序列编辑器 v3
==========================================================
核心设计：
1. 图案基于 TopView 相对坐标（归一化 XY），不强制格网格式
2. 音频实时调制：RMS→密度/亮度，beat_phase→相位，频谱→图案切换
3. 摇杆实时调制：XY→图案中心偏移/旋转/缩放
4. 幻灯片序列：多帧、循环/乒乓/随机/定格，帧间平滑过渡
5. 融入 Programmer 叠加层体系（写入 matrix 层）
6. 图案预设也注册到 EffectEngine，可在 Programmer 效果预设中使用
"""
from __future__ import annotations
import logging
import math
import random
import copy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

from PyQt6.QtWidgets import (
  QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QComboBox,
  QScrollArea, QCheckBox, QDialog, QDialogButtonBox, QLineEdit, QTabWidget,
)
from PyQt6.QtCore import (Qt, pyqtSignal, QTimer, QSize, QRect, QRectF,
             QPoint)
from PyQt6.QtGui import (QColor, QPainter, QBrush, QPen, QFont, QGradient,
             QLinearGradient, QRadialGradient, QPainterPath,
             QFontMetrics, QShortcut, QKeySequence, QPixmap)

from . import qascade_theme as _th
from .widget_kit import PanelSection, NumSlider, prop_row, apply_dialog_chrome, scope_qss
from .hidpi import hidpi_pixmap
from .canvas_materials import gradient, paint_bed, selection_fill, tone

logger = logging.getLogger(__name__)


# ── 图案计算纯函数（模块级）──────────────────────────────────────────────────
# ★ 性能：原在 PatternGenerator._compute 内每设备定义 6 个嵌套闭包
#  （11382 台 × 每帧 ≈ 6.8 万次闭包创建）；其中 4 个不依赖逐设备状态，
#  提升为模块级纯函数，数值逐位一致。
def _pat_clamp01(v: float) -> float:
  return max(0.0, min(1.0, v))


def _pat_line(value: float, target: float, width: float) -> float:
  width = max(0.006, width)
  return _pat_clamp01(1.0 - abs(value - target) / width)


def _pat_periodic_line(value: float, period: float, width: float,
            offset: float = 0.0) -> float:
  period = max(0.01, period)
  p = (value + offset) / period
  d = abs(p - round(p)) * period
  return _pat_clamp01(1.0 - d / max(0.006, width))


def _pat_band(bands, i: int) -> float:
  if not bands:
    return 0.0
  return max(0.0, min(1.0, float(bands[max(0, min(7, i))])))


# ── 相位量化常数 ─────────────────────────────────────────────────────────────
# ★ 播放路径性能门（_tick 循环/单次/乒乓/随机分支）：
#  图案输出为 8 位整数 DMX；get_pattern 是 (name, fixtures, params) 的纯函数，
#  params 只经 相位/摇杆/音频 进入。把相位按 int(phase*K) 量化成桶：
#  桶不变 → 输出不变 → 跳过全量重算（O(设备数) 图案求值 + DMX 输出）；
#  桶边界用当前真实相位重算（该瞬时与未量化流逐位一致）。
#  K=32 经 render_lab/bench/ce_probe_matrix_tick.py 实测校准（_tick 相位按
#  phase_speed/tick 推进）：生产预设速度 ps≈0.01 → ~68% tick 跳过（默认 0.02 →
#  ~36%，ps*K≥1 时自然退化为逐 tick）；连续图案(Wave/Ripple/Firework/Chroma)
#  桶内最大偏差 8~23 DMX——与原始流自身的逐 tick 步进（8~23 DMX/tick @ ps 0.01）
#  同级，肉眼近似；Lattice 壳线/层切跳变最多滞后 1 个桶（≤3.1 tick），位置序列
#  不变。ps*K 过快时门退化为每次重算（与未改动行为一致）。
MATRIX_PHASE_QUANT_K = 32


# ── 颜色常量（统一引用 主题设计令牌，见 qascade_theme.py）────────────────
BG_DARK  = _th.BG_ROOT    # #1D1D1D 页面底
BG_PANEL = _th.BG_EDITOR   # #303030 面板基底
BG_CARD  = _th.BG_BOX     # #2D2D2D 卡片/嵌套盒（原蓝紫 #2a2a3a）
BG_SEL  = _th.SELECT_BLUE  # #4772B3 选中填充（原 #1a3a6b）
FG_LIGHT = _th.TEXT_PRIMARY  # #E6E6E6
FG_DIM  = _th.TEXT_SECONDARY # #999999
ACCENT  = _th.FOCUS_BLUE   # #71A8FF 强调文字/描边（原 #4a90d9）
GREEN   = _th.BRIGHT_SUCCESS
ORANGE  = _th.BRIGHT_WARN
RED    = _th.BRIGHT_ERROR

# 播放按钮语义态（R54 色彩治理：按钮一律 中性面 #545454，不再用绿填充；
# 播放中态保留 bright_warn 黄字提示，画布内 ON 语义仍走 §4 保护色）
_PLAY_IDLE_QSS = (
  f"QPushButton{{background:#545454;border:1px solid #3D3D3D;"
  f"color:{_th.TEXT_PRIMARY};padding:4px 12px;border-radius:4px;}}"
  "QPushButton:hover{background:#656565;}"
  "QPushButton:pressed{background:#4772B3;color:#FFFFFF;}")
_PLAY_ACTIVE_QSS = (
  f"QPushButton{{background:#3A3A1A;border:1px solid #5A5A2A;"
  f"color:{_th.BRIGHT_WARN};padding:4px 12px;border-radius:4px;}}"
  "QPushButton:hover{background:#5A5A2A;}"
  "QPushButton:pressed{background:#4772B3;color:#FFFFFF;}")


def _ui_font(pixel_size: int, bold: bool = False) -> QFont:
  """统一 UI 字体（Inter→Segoe UI→雅黑 链），像素字号下限 10px（缓存）。"""
  return _th.cached_font(max(10, int(pixel_size)), bold,
              families=("Inter", "Segoe UI", "Microsoft YaHei UI"))


def _mono_font(pixel_size: int) -> QFont:
  """等宽字体（时间码/通道地址/数值）（缓存）。"""
  return _th.cached_font(max(10, int(pixel_size)),
              families=("Consolas", "DejaVu Sans Mono"))


_DIM_LABEL_QSS = f"color:{_th.TEXT_SECONDARY};font-size:11px;background:transparent;"

# ══════════════════════════════════════════════════════════════════════════════
# 图案类型定义（按空间感知分类）
# ══════════════════════════════════════════════════════════════════════════════
PATTERN_CATEGORIES: Dict[str, List[str]] = {
  "基础形状": [
    "All_On", "All_Off",
    "Circle", "Ring", "Diamond", "InvDiamond",
    "Cross", "XShape", "Star5", "Star6",
    "Arrow_L", "Arrow_R", "Arrow_U", "Arrow_D",
    "Triangle_U", "Triangle_D", "Triangle_L", "Triangle_R",
    "HalfL", "HalfR", "HalfT", "HalfB",
    "QuadTL", "QuadTR", "QuadBL", "QuadBR",
  ],
  "网格图案": [
    "Checkerboard", "Checkerboard_Fine",
    "Stripes_H", "Stripes_V", "Stripes_D45", "Stripes_D135",
    "Dots_Grid", "Dots_Hex",
    "Border", "Border_Inner",
    "Cross_Grid", "Zigzag_H", "Zigzag_V",
    "Grid_Sparse", "Grid_Dense",
  ],
  "动态波形": [
    "Wave_H", "Wave_V", "Wave_D",
    "Wave_Radial", "Ripple", "Ripple_Fast",
    "Spiral_CW", "Spiral_CCW",
    "Tornado", "Vortex",
    "Sine_Fade", "Cosine_Fade",
    "Pulse_Out", "Pulse_In",
    "Scan_H", "Scan_V", "Scan_Radial",
  ],
  "追逐序列": [
    "Chase_L2R", "Chase_R2L", "Chase_T2B", "Chase_B2T",
    "Chase_In", "Chase_Out",
    "Chase_Spiral_CW", "Chase_Spiral_CCW",
    "SingleDot", "DoubleDot", "TripleDot",
    "GroupChase", "GroupAlternate", "GroupTriple",
    "BounceH", "BounceV",
    "Snake_H", "Snake_V",
  ],
  "特殊效果": [
    "Firework", "Firework_Multi",
    "Heartbeat", "Breathe",
    "Lightning", "Meteor", "Meteor_Shower",
    "Sparkle", "Sparkle_Dense",
    "Noise", "Noise_Smooth",
    "Starburst", "Kaleidoscope",
    "Cascade_T", "Cascade_B",
    "Wipe_L2R", "Wipe_R2L",
  ],
  "喷泉专用": [
    "Fountain_Rise", "Fountain_Fall",
    "Fountain_Fan", "Fountain_Fan_Wide",
    "Fountain_Center", "Fountain_Sides",
    "Fountain_Wave", "Fountain_Ripple",
    "Fountain_Chase", "Fountain_Alternate",
    "Fountain_Spiral", "Fountain_Random",
    "Fountain_Heartbeat", "Fountain_Breathe",
    "Fountain_Burst",
  ],
  "坐标艺术": [
    "Lissajous",    # 李萨如图形
    "Mandala",     # 曼陀罗
    "Fractal_Tree",   # 分形树
    "Moire",      # 莫尔条纹
    "Interference",   # 干涉图案
    "Voronoi",     # 泰森多边形
    "Gradient_Radial", # 径向渐变
    "Gradient_Linear", # 线性渐变
    "Concentric",    # 同心圆
    "Pinwheel",     # 风车
  ],
  "空间点阵序列": [
    "Lattice_Diamond_Layers", "Lattice_Diamond_Ribs", "Lattice_Diamond_Hollow",
    "Lattice_Circle_Rings", "Lattice_Circle_Sectors", "Lattice_Circle_Hollow",
    "Lattice_Cross_Axis", "Lattice_X_Axis", "Lattice_Symmetry_Axis",
    "Lattice_Quadrant_Mirror", "Lattice_Corner_Bloom", "Lattice_Sector_Mirror",
    "Lattice_Window_Flower", "Lattice_Mandala_Branches", "Lattice_Mandala_Fill",
    "Lattice_PaperCut_Rings", "Lattice_Topology_Out", "Lattice_Topology_In",
    "Lattice_Diamond_Circle", "Lattice_Mirror_Chase", "Lattice_Sparse_Stars",
    "Lattice_Snowflake", "Lattice_Spiral_Arms", "Lattice_Zigzag_Sym",
    "Lattice_Grid_Sparse", "Lattice_Petal_Orbit", "Lattice_Layer_Cascade",
    "Lattice_Spectrum_Mirror", "Lattice_Spectrum_Diamond", "Lattice_Spectrum_Rings",
    "Lattice_Spectrum_Quadrants", "Lattice_Spectrum_Flower", "Lattice_Spectrum_LowMidHigh",
    "Lattice_Spectrum_Breath", "Lattice_Spectrum_Cascade", "Lattice_Spectrum_Pulse",
    "Lattice_Spectrum_Spiral",
  ],
  "Chroma效果": [
    "Chroma_Wave",   # 彩虹波浪（横向色带流动）
    "Chroma_Ripple",  # 彩虹涟漪（径向色环扩散）
    "Chroma_Spiral",  # 彩虹螺旋（顺时针旋转色带）
    "Chroma_Scan",   # 彩虹扫描（单色光柱扫过）
    "Chroma_Explode",  # 爆炸扩散（中心向外爆发）
    "Chroma_Rain",   # 矩阵雨（竖向绿色字符流）
    "Chroma_Fire",   # 火焰效果（底部向上燃烧）
    "Chroma_Aurora",  # 极光效果（多色水平漂移）
    "Chroma_Neon_Chase",# 霓虹追逐（高饱和色点追逐）
    "Chroma_Starfield", # 星空闪烁（随机亮点闪烁）
    "Chroma_Plasma",  # 等离子（多波叠加彩色噪声）
    "Chroma_Comet",   # 彗星拖尾（带余晖的扫描点）
    "Chroma_Heartbeat", # 彩虹心跳（脉冲+色相旋转）
    "Chroma_Snake",   # 彩虹蛇形（蛇形路径+色相偏移）
    "Chroma_Vortex",  # 彩虹漩涡（旋转+径向色相）
  ],
}

ALL_PATTERNS: List[str] = [p for ps in PATTERN_CATEGORIES.values() for p in ps]

# ══════════════════════════════════════════════════════════════════════════════
# Chroma 效果中文显示名映射
# ──────────────────────────────────────────────────────────────────────────────
# 说明：下面这些英文键（Chroma_Wave 等）同时是「派发键」——_compute 里用
# `elif name == "Chroma_Wave"` 分支、`name.startswith("Chroma_")` 判断、以及
# 已保存项目里引用的图案名，全都依赖这些原始英文字符串，绝不能改动。
# 这里只建立「英文键 → 中文显示名」的映射：UI 下拉框用中文显示，userData 仍
# 存原英文键，读取时用 currentData() 取回英文键再派发，派发链完全不受影响。
# ══════════════════════════════════════════════════════════════════════════════
_CHROMA_ZH: Dict[str, str] = {
  "Chroma_Wave":    "彩虹波浪",
  "Chroma_Ripple":   "彩虹涟漪",
  "Chroma_Spiral":   "彩虹螺旋",
  "Chroma_Scan":    "彩虹扫描",
  "Chroma_Explode":  "爆炸扩散",
  "Chroma_Rain":    "矩阵雨",
  "Chroma_Fire":    "火焰",
  "Chroma_Aurora":   "极光",
  "Chroma_Neon_Chase": "霓虹追逐",
  "Chroma_Starfield": "星空闪烁",
  "Chroma_Plasma":   "等离子",
  "Chroma_Comet":   "彗星拖尾",
  "Chroma_Heartbeat": "彩虹心跳",
  "Chroma_Snake":   "彩虹蛇形",
  "Chroma_Vortex":   "彩虹漩涡",
}


def _pattern_display_name(key: str) -> str:
  """返回图案键对应的显示名：Chroma 键显示中文，其余键原样返回。"""
  return _CHROMA_ZH.get(key, key)


def _populate_pattern_combo(combo, keys: List[str]) -> None:
  """填充图案下拉框：显示名用中文（仅 Chroma），userData 始终存原英文键。"""
  for key in keys:
    combo.addItem(_pattern_display_name(key), key)


def _select_pattern_in_combo(combo, key: str) -> None:
  """按英文键选中下拉项（通过 userData 匹配，不依赖显示文本）。"""
  idx = combo.findData(key)
  if idx >= 0:
    combo.setCurrentIndex(idx)


# ══════════════════════════════════════════════════════════════════════════════
# 坐标感知图案生成器
# ══════════════════════════════════════════════════════════════════════════════
class PatternGenerator:
  """
  空间图案生成器。
  输入：设备列表（含 pos_x/pos_y 归一化坐标）+ 参数字典
  输出：权重列表 [0.0, 1.0]，长度与设备数相同
  
  参数字典 params 支持：
   type    图案名称
   density   密度/阈值 [0,1]
   size    尺寸缩放 [0.1,5]
   phase    相位 [0,∞)
   group_size 分组大小
   cx, cy   图案中心偏移 [-0.5, 0.5]（摇杆控制）
   rotation  旋转角度 [0, 2π]（摇杆控制）
   audio_rms  音频 RMS [0,1]（音频调制）
   audio_beat 音频节拍相位 [0,1]
  """

  # ★ 性能：归一化坐标缓存。value 持有 fixtures 列表引用 → allocator 不会
  #  复用其 id（杜绝"新列表撞旧 id → 旧归一化坐标误用于新设备集"）；容量
  #  上限 8 条（每条 ≈180KB），超限整体清空（重建安全）。
  _PAT_NORM_CACHE: Dict[tuple, Tuple[List[Dict], List[float], List[float], float, float]] = {}

  @staticmethod
  def _normalize(fixtures: List[Dict]) -> Tuple[List[float], List[float], float, float]:
    """归一化设备坐标，返回 (nxs, nys, range_x, range_y)"""
    # ★ R193/P1-10（MX-05）：键从 id(list) 改为内容签名——EffectEngine 每帧
    #  传入新建 list，id 键 100% miss（大工程每帧重做归一化+缓存反复清填）。
    #  键含坐标：设备拖动后自动失效，不会陈旧命中。
    key = tuple(
      (f.get("id"), f.get("pos_x", f.get("x", 0.0)), f.get("pos_y", f.get("y", 0.0)))
      for f in fixtures
    )
    cached = PatternGenerator._PAT_NORM_CACHE.get(key)
    if cached is not None:
      return cached[1], cached[2], cached[3], cached[4]
    xs = [f.get("pos_x", f.get("x", 0.0)) for f in fixtures]
    ys = [f.get("pos_y", f.get("y", 0.0)) for f in fixtures]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    rx = max(0.001, max_x - min_x)
    ry = max(0.001, max_y - min_y)
    nxs = [(x - min_x) / rx for x in xs]
    nys = [(y - min_y) / ry for y in ys]
    if len(PatternGenerator._PAT_NORM_CACHE) >= 8:
      PatternGenerator._PAT_NORM_CACHE.clear()
    PatternGenerator._PAT_NORM_CACHE[key] = (None, nxs, nys, rx, ry)
    return nxs, nys, rx, ry

  @classmethod
  def get_pattern(cls, name: str, fixtures: List[Dict], params: Dict) -> List[float]:
    n = len(fixtures)
    if n == 0:
      return []

    density  = float(params.get("density",  0.8))
    phase   = float(params.get("phase",   0.0))
    size   = max(0.01, float(params.get("size", 1.0)))
    group_sz = max(1, int(params.get("group_size", 3)))
    # 摇杆偶移（归一化 [-0.5, 0.5]）
    cx_off  = float(params.get("cx", 0.0))
    cy_off  = float(params.get("cy", 0.0))
    rotation = float(params.get("rotation", 0.0))
    # 音频调制：audio_mode=False 时所有图案使用自走相位，完全忽略音频输入；
    # audio_mode=True 时使用已平滑/延迟后的慢包络，避免像素/泵输出闪烁。
    audio_mode = bool(params.get("audio_mode", False))
    audio_rms = float(params.get("audio_rms", 0.0)) if audio_mode else 0.0
    audio_beat = float(params.get("audio_beat", 0.0)) if audio_mode else 0.0
    audio_bands = list(params.get("audio_bands", [0.0] * 8) or [0.0] * 8) if audio_mode else [0.0] * 8
    if len(audio_bands) < 8:
      audio_bands = (audio_bands + [0.0] * 8)[:8]
    audio_bands = [max(0.0, min(1.0, float(v))) for v in audio_bands[:8]]
    shape_params = {
      "audio_mode": audio_mode,
      "layers": max(1, int(params.get("layers", 6))),
      "hollow": max(0.0, min(0.95, float(params.get("hollow", 0.0)))),
      "edge_width": max(0.05, min(12.0, float(params.get("edge_width", 1.0)))),
      "branch_count": max(1, min(96, int(params.get("branch_count", 8)))),
      "expand_dir": max(0, min(3, int(params.get("expand_dir", 0)))),
      "mirror_axis": max(0, min(3, int(params.get("mirror_axis", 3)))),
      "circle_diamond_mix": max(0.0, min(1.0, float(params.get("circle_diamond_mix", 0.5)))),
      "spectrum_zone_strength": max(0.0, min(8.0, float(params.get("spectrum_zone_strength", 0.7)))),
      "step_speed": max(0.0, min(16.0, float(params.get("step_speed", 1.0)))),
    }
    # Chroma 色相偏移（摇杆/音频可调制）
    hue_shift = float(params.get("hue_shift", 0.0))
    # 将 hue_shift 叠加到 phase，这样 Chroma 图案的色相计算中会自动包含偶移
    if hue_shift != 0.0 and name.startswith("Chroma_"):
      phase = phase + hue_shift

    nxs, nys, _, _ = cls._normalize(fixtures)
    weights: List[float] = []

    # ★ 性能：旋转 cos/sin 提出设备循环（每帧每台重复计算纯浪费）
    if rotation != 0.0:
      cos_r, sin_r = math.cos(rotation), math.sin(rotation)
    else:
      cos_r = sin_r = None

    for idx in range(n):
      nx0, ny0 = nxs[idx], nys[idx]
      f = fixtures[idx]

      # 应用摇杆中心偏移
      nx = nx0 - 0.5 - cx_off
      ny = ny0 - 0.5 - cy_off

      # 应用旋转
      if cos_r is not None:
        nx, ny = nx * cos_r - ny * sin_r, nx * sin_r + ny * cos_r

      # 极坐标
      dist = math.sqrt(nx * nx + ny * ny)
      angle = math.atan2(ny, nx) # [-π, π]
      # 归一化角度 [0, 1)
      a_norm = (angle + math.pi) / (2 * math.pi)

      # 线性进度（按设备索引）
      t = idx / max(1, n - 1)

      val = cls._compute(name, idx, n, nx, ny, nx0, ny0, dist, angle,
                a_norm, t, density, phase, size, group_sz,
                audio_rms, audio_beat, audio_bands, f, shape_params)
      weights.append(max(0.0, min(1.0, val)))

    return weights

  @staticmethod
  def _compute(name: str, idx: int, n: int,
         nx: float, ny: float, nx0: float, ny0: float,
         dist: float, angle: float, a_norm: float, t: float,
         density: float, phase: float, size: float, group_sz: int,
         audio_rms: float, audio_beat: float, audio_bands: List[float],
         f: Dict, shape_params: Optional[Dict[str, Any]] = None) -> float:
    """核心图案计算，返回 [0,1] 权重"""
    shape_params = shape_params or {}
    audio_mode = bool(shape_params.get("audio_mode", False))
    layers = max(1, int(shape_params.get("layers", 6)))
    hollow = max(0.0, min(0.95, float(shape_params.get("hollow", 0.0))))
    edge_width = max(0.05, min(12.0, float(shape_params.get("edge_width", 1.0))))
    branch_count = max(1, min(96, int(shape_params.get("branch_count", 8))))
    expand_dir = max(0, min(3, int(shape_params.get("expand_dir", 0))))
    mirror_axis = max(0, min(3, int(shape_params.get("mirror_axis", 3))))
    circle_diamond_mix = max(0.0, min(1.0, float(shape_params.get("circle_diamond_mix", 0.5))))
    spectrum_zone_strength = max(0.0, min(8.0, float(shape_params.get("spectrum_zone_strength", 0.7))))
    step_speed = max(0.0, min(16.0, float(shape_params.get("step_speed", 1.0))))

    # 音频增强密度：矩阵/喷泉泵不能瞬时大幅变化，因此只允许音频给密度做小幅、慢速的“呼吸”增益。
    eff_density = max(0.0, min(1.0, density + (audio_rms * 0.12 if audio_mode else 0.0)))
    # 音频节拍只做很轻的相位偏移，避免画面随 beat_phase 快速跳变。
    eff_phase = phase + (audio_beat * 0.12 if audio_mode else 0.0)

    # ★ 性能：_clamp01/_line/_periodic_line/_band 为不依赖逐设备状态的纯函数，
    #  已提升为模块级（_pat_*），此处不再每设备创建闭包；仅保留依赖
    #  逐设备值（nx0/ny0/idx/angle）的 _spatial_gate/_window_petals。
    def _spatial_gate(seed_extra: int = 0, min_keep: float = 0.10) -> float:
      # “开启密度”不是亮度，而是空间取点比例；用确定性哈希稀疏开点，避免所有喷头同时全开。
      keep = max(min_keep, min(0.92, eff_density))
      gx = int(round((nx0) * 24))
      gy = int(round((ny0) * 24))
      h = (gx * 73856093) ^ (gy * 19349663) ^ (idx * 83492791) ^ (seed_extra * 2654435761)
      h &= 0xffffffff
      return 1.0 if (h % 1000) < keep * 1000 else 0.0

    def _window_petals(sym: int, twist: float = 0.0) -> float:
      a = ((angle + math.pi) / (2 * math.pi) * sym + twist) % 1.0
      a = min(a, 1.0 - a)
      return _pat_line(a, 0.0, 0.10 + 0.04 * (1.0 - eff_density))

    low = (_pat_band(audio_bands, 0) + _pat_band(audio_bands, 1)) * 0.5
    mid = (_pat_band(audio_bands, 2) + _pat_band(audio_bands, 3) + _pat_band(audio_bands, 4)) / 3.0
    high = (_pat_band(audio_bands, 5) + _pat_band(audio_bands, 6) + _pat_band(audio_bands, 7)) / 3.0
    spec_avg = sum(audio_bands[:8]) / 8.0 if audio_bands else 0.0

    # ── 空间点阵序列 / 对称频谱 ─────────────────────────────────────────────
    if name.startswith("Lattice_"):
      man = abs(nx) + abs(ny)
      max_man = 1.02
      r = dist / 0.7072
      ph = (eff_phase * step_speed) % 1.0
      seq = ph
      width = ((0.026 + (1.0 - eff_density) * 0.018) * edge_width) / max(0.55, size)
      period = max(0.055, (0.20 / max(0.55, size)) / max(0.70, edge_width * 0.75))
      layer_count = max(1, layers)
      layer = int(seq * layer_count)
      gate = _spatial_gate(layer, min_keep=max(0.04, eff_density * 0.35))
      radial_metric = circle_diamond_mix * r + (1.0 - circle_diamond_mix) * (man / max_man)

      def _hollow_mask(metric: float) -> float:
        return 0.0 if metric < hollow else 1.0

      def _direction_mask(metric: float) -> float:
        if expand_dir == 1: # 水平优先
          return _pat_clamp01(0.45 + abs(nx) * 1.2)
        if expand_dir == 2: # 垂直优先
          return _pat_clamp01(0.45 + abs(ny) * 1.2)
        if expand_dir == 3: # 对角优先
          return _pat_clamp01(0.35 + abs(nx - ny) * 0.9 + abs(nx + ny) * 0.9)
        return 1.0

      def _mirror_mask() -> float:
        vals = []
        if mirror_axis in (0, 3):
          vals.append(_pat_line(nx, 0.0, width * 1.35))
        if mirror_axis in (1, 3):
          vals.append(_pat_line(ny, 0.0, width * 1.35))
        if mirror_axis in (2, 3):
          vals.append(max(_pat_line(nx - ny, 0.0, width * 1.8), _pat_line(nx + ny, 0.0, width * 1.8)))
        return max(vals) if vals else 0.0

      if name in ("Lattice_Diamond_Layers", "Lattice_Diamond_Rings"):
        target = (layer + 1) / (layer_count + 1.0) * 0.94
        return _pat_line(man, target, width * 1.8) * gate * _hollow_mask(man / max_man) * _direction_mask(man)
      elif name == "Lattice_Diamond_Ribs":
        ribs = max(_pat_line(abs(nx), abs(ny), width * 1.5), _pat_periodic_line(man, period, width * 1.25, -seq * 0.08))
        outer = _pat_line(man, 0.20 + 0.72 * seq, width * 2.0)
        return max(ribs * 0.65, outer) * gate * _hollow_mask(man / max_man)
      elif name == "Lattice_Diamond_Hollow":
        contour = _pat_periodic_line(man, max(0.08, max_man / (layer_count + 1)), width * 1.45, -seq * 0.06)
        edge = max(_pat_line(abs(nx), abs(ny), width * 1.25), _mirror_mask() * 0.55)
        return max(contour, edge) * gate * _hollow_mask(man / max_man)
      elif name == "Lattice_Circle_Rings":
        target = (layer + 1) / (layer_count + 1.0) * 0.96
        return _pat_line(r, target, width * 2.0) * gate * _hollow_mask(r)
      elif name == "Lattice_Circle_Sectors":
        sector = _window_petals(branch_count, seq * 0.25)
        ring = _pat_periodic_line(r, max(0.08, 0.86 / (layer_count + 1)), width * 1.6, -seq * 0.05)
        return max(sector * ring, _pat_line(r, 0.22 + 0.62 * seq, width * 1.8)) * gate * _hollow_mask(r)
      elif name == "Lattice_Circle_Hollow":
        shell = _pat_line(r, hollow + (1.0 - hollow) * (0.18 + 0.72 * seq), width * 2.2)
        ribs = _window_petals(branch_count, -seq * 0.18) * _pat_periodic_line(r, max(0.10, 0.80 / (layer_count + 1)), width * 1.3, seq * 0.05)
        return max(shell, ribs * 0.85) * gate * _hollow_mask(r)
      elif name == "Lattice_Cross_Axis":
        axis = max(_pat_line(nx, 0.0, width * 1.5), _pat_line(ny, 0.0, width * 1.5))
        rings = _pat_periodic_line(man, max(0.08, max_man / (layer_count + 1)), width * 1.3, -seq * 0.08)
        return max(axis, rings * 0.8) * gate * _hollow_mask(man / max_man)
      elif name == "Lattice_X_Axis":
        axis = max(_pat_line(nx - ny, 0.0, width * 2.0), _pat_line(nx + ny, 0.0, width * 2.0))
        rings = _pat_periodic_line(man, max(0.08, max_man / (layer_count + 1)), width * 1.3, -seq * 0.08)
        return max(axis, rings * 0.75) * gate * _hollow_mask(man / max_man)
      elif name == "Lattice_Symmetry_Axis":
        contour = _pat_periodic_line(radial_metric, max(0.07, 0.92 / (layer_count + 1)), width * 1.35, -seq * 0.06)
        return max(_mirror_mask(), contour * 0.85) * gate * _hollow_mask(radial_metric)
      elif name == "Lattice_Quadrant_Mirror":
        q = 0 if nx >= 0 and ny >= 0 else 1 if nx < 0 <= ny else 2 if nx < 0 and ny < 0 else 3
        active_q = {layer % 4, (layer + 2) % 4}
        contour = max(_pat_periodic_line(abs(nx), 0.18, width, -seq * 0.05), _pat_periodic_line(abs(ny), 0.18, width, -seq * 0.05))
        return contour * gate * _hollow_mask(radial_metric) if q in active_q else 0.0
      elif name == "Lattice_Corner_Bloom":
        dcorner = min(math.hypot(nx - sx * 0.5, ny - sy * 0.5) for sx in (-1, 1) for sy in (-1, 1))
        return _pat_line(dcorner, seq * 0.70, width * 2.2) * gate
      elif name == "Lattice_Sector_Mirror":
        sector = _window_petals(branch_count, seq * 0.20)
        sweep = _pat_line(radial_metric, 0.18 + 0.68 * seq, width * 2.0)
        return max(sector * _pat_periodic_line(radial_metric, 0.16, width * 1.35, -seq * 0.05), sweep * _mirror_mask()) * gate * _hollow_mask(radial_metric)
      elif name == "Lattice_Window_Flower":
        petals = _window_petals(branch_count, 0.0) * _pat_periodic_line(man, max(0.08, max_man / (layer_count + 1)), width * 1.3, -seq * 0.08)
        core = _pat_line(man, 0.10 + seq * 0.62, width * 2.0)
        axes = max(_pat_line(nx, 0.0, width), _pat_line(ny, 0.0, width), _pat_line(nx - ny, 0.0, width * 1.4), _pat_line(nx + ny, 0.0, width * 1.4))
        return max(petals, core, axes * 0.75) * gate * _hollow_mask(man / max_man)
      elif name == "Lattice_Mandala_Branches":
        branches = _window_petals(branch_count, seq * 0.18)
        rings = max(_pat_periodic_line(r, max(0.08, 0.90 / (layer_count + 1)), width * 1.4, -seq * 0.06), _pat_periodic_line(man, max(0.08, max_man / (layer_count + 1)), width, seq * 0.04))
        return branches * rings * gate * _hollow_mask(r)
      elif name == "Lattice_Mandala_Fill":
        petals = _window_petals(branch_count, -seq * 0.12)
        fill = _pat_clamp01(1.0 - abs(radial_metric - (0.18 + 0.58 * seq)) / max(width * 6.0, 0.08))
        outline = _pat_periodic_line(radial_metric, max(0.09, 0.86 / (layer_count + 1)), width * 1.5, seq * 0.04)
        return max(fill * petals * 0.75, outline * petals) * gate * _hollow_mask(radial_metric)
      elif name == "Lattice_PaperCut_Rings":
        diamond = _pat_periodic_line(man, max(0.08, max_man / (layer_count + 1)), width * 1.25, -seq * 0.05)
        circle = _pat_periodic_line(r, max(0.08, 0.90 / (layer_count + 1)), width * 1.35, seq * 0.04)
        cut = max(_window_petals(max(4, branch_count // 2), 0.0), _window_petals(branch_count, 0.25))
        return max(diamond * cut, circle * (1.0 - 0.35 * cut)) * gate * _hollow_mask(radial_metric)
      elif name == "Lattice_Topology_Out":
        target = seq * max_man
        skeleton = max(_pat_line(man, target, width * 2.0), _mirror_mask() * 0.75)
        branch = _window_petals(branch_count, 0.0) * (1.0 if man <= target + 0.08 else 0.0)
        return max(skeleton, branch * 0.8) * gate * _hollow_mask(man / max_man) * _direction_mask(man)
      elif name == "Lattice_Topology_In":
        target = (1.0 - seq) * max_man
        skeleton = max(_pat_line(man, target, width * 2.0), _mirror_mask() * 0.75)
        branch = _window_petals(branch_count, 0.0) * (1.0 if man >= target - 0.08 else 0.0)
        return max(skeleton, branch * 0.8) * gate * _hollow_mask(man / max_man) * _direction_mask(man)
      elif name == "Lattice_Diamond_Circle":
        return max(_pat_periodic_line(man, max(0.08, max_man / (layer_count + 1)), width * 1.2, -seq * 0.06), _pat_periodic_line(r, max(0.08, 0.92 / (layer_count + 1)), width * 1.25, seq * 0.05)) * gate * _hollow_mask(radial_metric)
      elif name == "Lattice_Mirror_Chase":
        pos = abs((seq * 2.0) % 2.0 - 1.0) * 0.5
        return max(_pat_line(abs(nx), pos, width * 2.0), _pat_line(abs(ny), pos, width * 2.0), _mirror_mask() * 0.55) * gate * _hollow_mask(radial_metric)
      elif name == "Lattice_Sparse_Stars":
        star = max(_window_petals(5, 0.0), _window_petals(10, 0.25)) * _pat_periodic_line(r, max(0.10, 0.92 / (layer_count + 1)), width * 1.35, -seq * 0.04)
        return star * _spatial_gate(layer + 91, min_keep=0.05) * _hollow_mask(r)
      elif name == "Lattice_Snowflake":
        petals = max(_window_petals(6, 0.0), _window_petals(12, seq * 0.12) * 0.75)
        branch = _pat_periodic_line(r, max(0.08, 0.90 / (layer_count + 1)), width * 1.35, -seq * 0.05)
        spine = max(_pat_line(nx, 0.0, width), _pat_line(nx * 0.5 + ny * 0.866, 0.0, width), _pat_line(nx * 0.5 - ny * 0.866, 0.0, width))
        return max(petals * branch, spine * 0.65) * gate * _hollow_mask(r)
      elif name == "Lattice_Spiral_Arms":
        arm = ((a_norm * branch_count + r * (1.8 + size) - seq * 1.2) % 1.0)
        arm = min(arm, 1.0 - arm)
        spiral = _pat_line(arm, 0.0, 0.06 + width * 1.2)
        rings = _pat_periodic_line(r, max(0.10, 0.84 / (layer_count + 1)), width * 1.1, -seq * 0.04)
        return spiral * max(0.35, rings) * gate * _hollow_mask(r)
      elif name == "Lattice_Zigzag_Sym":
        zig = abs(((nx + 0.5 + seq * 0.2) * branch_count) % 1.0 - 0.5) - abs(ny) * 0.55
        diag = max(_pat_line(nx - ny, 0.0, width * 1.4), _pat_line(nx + ny, 0.0, width * 1.4))
        return max(_pat_line(zig, 0.0, width * 2.0), diag * 0.55) * gate * _hollow_mask(radial_metric)
      elif name == "Lattice_Grid_Sparse":
        gx = _pat_periodic_line(abs(nx), max(0.07, 0.86 / (layer_count + 1)), width, -seq * 0.03)
        gy = _pat_periodic_line(abs(ny), max(0.07, 0.86 / (layer_count + 1)), width, seq * 0.03)
        return max(gx, gy) * _spatial_gate(layer + 43, min_keep=0.06) * _hollow_mask(radial_metric)
      elif name == "Lattice_Petal_Orbit":
        orbit = _pat_line(r, 0.20 + 0.55 * abs((seq * 2.0) % 2.0 - 1.0), width * 2.1)
        petals = _window_petals(branch_count, seq * 0.35)
        return max(orbit * petals, _pat_periodic_line(r, max(0.10, 0.9 / (layer_count + 1)), width * 1.2, -seq * 0.04) * petals) * gate * _hollow_mask(r)
      elif name == "Lattice_Layer_Cascade":
        metric = radial_metric if expand_dir == 0 else (abs(nx) if expand_dir == 1 else abs(ny) if expand_dir == 2 else man / max_man)
        target = (layer + 1) / (layer_count + 1.0)
        return _pat_line(metric, target, width * 2.0) * _spatial_gate(layer + 17, min_keep=0.06) * _hollow_mask(radial_metric)
      elif name == "Lattice_Spectrum_Mirror":
        band_pos = min(7, int(abs(nx) / 0.5 * 8.0))
        amp = _pat_band(audio_bands, 7 - band_pos) if audio_mode else (0.45 + 0.35 * math.sin((seq + band_pos / 8.0) * 2 * math.pi))
        amp *= spectrum_zone_strength
        height = 0.08 + _pat_clamp01(amp) * 0.42
        return _pat_line(abs(ny), height, width * 3.0) * _spatial_gate(band_pos + 200, min_keep=0.08)
      elif name == "Lattice_Spectrum_Diamond":
        amp = (low * 0.50 + mid * 0.35 + high * 0.15) if audio_mode else (0.45 + 0.35 * math.sin(seq * 2 * math.pi))
        amp = _pat_clamp01(amp * spectrum_zone_strength)
        target = 0.16 + amp * 0.74
        harmonic = _pat_periodic_line(man, 0.18 + (high if audio_mode else seq) * 0.08, width * 1.4, -seq * 0.04)
        return max(_pat_line(man, target, width * 2.2), harmonic * (0.4 + (mid if audio_mode else amp) * 0.6)) * gate
      elif name == "Lattice_Spectrum_Rings":
        amp = (low * 0.35 + mid * 0.35 + high * 0.30) if audio_mode else (0.45 + 0.35 * math.sin(seq * 2 * math.pi))
        amp = _pat_clamp01(amp * spectrum_zone_strength)
        low_eff = low if audio_mode else amp
        high_eff = high if audio_mode else 1.0 - amp * 0.35
        return max(_pat_line(r, 0.18 + low_eff * 0.55, width * 2.0), _pat_periodic_line(r, 0.18 + high_eff * 0.08, width * 1.25, -amp * 0.08)) * gate
      elif name == "Lattice_Spectrum_Quadrants":
        qv = [low, mid, high, (low + mid + high) / 3.0] if audio_mode else [0.35 + 0.45 * math.sin((seq + k * 0.25) * 2 * math.pi) for k in range(4)]
        q = 0 if nx >= 0 and ny >= 0 else 1 if nx < 0 <= ny else 2 if nx < 0 and ny < 0 else 3
        amp = _pat_clamp01(qv[q] * spectrum_zone_strength)
        return _pat_periodic_line(man, 0.18 + amp * 0.10, width * 1.35, -seq * 0.04) * _pat_clamp01(0.25 + amp) * gate
      elif name == "Lattice_Spectrum_Flower":
        amp = (low * 0.45 + mid * 0.35 + high * 0.20) if audio_mode else (0.45 + 0.35 * math.sin(seq * 2 * math.pi))
        amp = _pat_clamp01(amp * spectrum_zone_strength)
        petals = _window_petals(branch_count if (not audio_mode or high < 0.45) else max(branch_count, 12), seq * 0.12)
        rings = _pat_periodic_line(r, 0.18 + (mid if audio_mode else amp) * 0.08, width * 1.5, -amp * 0.06)
        return petals * rings * _pat_clamp01(0.35 + amp) * gate
      elif name == "Lattice_Spectrum_LowMidHigh":
        low_eff, mid_eff, high_eff = (low, mid, high) if audio_mode else (0.5 + 0.4 * math.sin(seq * 2 * math.pi), 0.5 + 0.4 * math.sin((seq + 0.33) * 2 * math.pi), 0.5 + 0.4 * math.sin((seq + 0.66) * 2 * math.pi))
        low_eff, mid_eff, high_eff = [_pat_clamp01(v * spectrum_zone_strength) for v in (low_eff, mid_eff, high_eff)]
        low_part = _pat_line(man, 0.18 + low_eff * 0.28, width * 2.1)
        mid_part = _pat_line(r, 0.38 + mid_eff * 0.22, width * 1.9)
        high_part = _window_petals(max(branch_count, 12), seq * 0.08) * _pat_line(r, 0.66 + high_eff * 0.20, width * 2.0)
        return max(low_part * low_eff, mid_part * mid_eff, high_part * high_eff) * gate
      elif name == "Lattice_Spectrum_Breath":
        amp = spec_avg if audio_mode else (0.5 + 0.5 * math.sin(seq * 2 * math.pi))
        amp = _pat_clamp01(amp * spectrum_zone_strength)
        breath = 0.5 - 0.5 * math.cos((0.18 + amp * 0.65) * 2 * math.pi)
        contour = max(_pat_periodic_line(man, max(0.08, max_man / (layer_count + 1)), width * 1.4, -breath * 0.08), _pat_periodic_line(r, max(0.08, 0.92 / (layer_count + 1)), width * 1.4, breath * 0.08))
        return contour * _pat_clamp01(0.28 + amp) * gate
      elif name == "Lattice_Spectrum_Cascade":
        bands = audio_bands[:8] if audio_mode else [0.5 + 0.45 * math.sin((seq + i / 8.0) * 2 * math.pi) for i in range(8)]
        band_idx = min(7, int(_pat_clamp01(radial_metric) * 8.0))
        amp = _pat_clamp01(bands[band_idx] * spectrum_zone_strength)
        cascade = _pat_line(radial_metric, (band_idx + 0.5) / 8.0 + (amp - 0.5) * 0.08, width * 2.0)
        return cascade * _pat_clamp01(0.25 + amp) * _spatial_gate(band_idx + layer + 300, min_keep=0.06)
      elif name == "Lattice_Spectrum_Pulse":
        amp = spec_avg if audio_mode else (0.5 + 0.5 * math.sin(seq * 2 * math.pi))
        amp = _pat_clamp01(amp * spectrum_zone_strength)
        pulse = _pat_line(radial_metric, 0.15 + amp * 0.72, width * 2.4)
        petal = _window_petals(branch_count, seq * 0.15)
        return max(pulse, petal * _pat_periodic_line(radial_metric, 0.18, width * 1.25, -amp * 0.06)) * gate
      elif name == "Lattice_Spectrum_Spiral":
        amp = (low * 0.40 + mid * 0.35 + high * 0.25) if audio_mode else (0.5 + 0.5 * math.sin(seq * 2 * math.pi))
        amp = _pat_clamp01(amp * spectrum_zone_strength)
        arm = ((a_norm * branch_count + r * (1.4 + amp) - seq * (0.8 + amp)) % 1.0)
        arm = min(arm, 1.0 - arm)
        return _pat_line(arm, 0.0, 0.055 + width * 1.2) * _pat_periodic_line(r, 0.16 + amp * 0.08, width * 1.4, -seq * 0.04) * gate

    # ── 基础形状 ────────────────────────────────────────────────────────
    if name == "All_On":
      return eff_density
    elif name == "All_Off":
      return 0.0
    elif name == "Circle":
      return 1.0 if dist < eff_density * 0.55 * size else 0.0
    elif name == "Ring":
      r_out = eff_density * 0.55 * size
      r_in = max(0, r_out - 0.12 * size)
      return 1.0 if r_in < dist < r_out else 0.0
    elif name == "Diamond":
      return 1.0 if (abs(nx) + abs(ny)) < eff_density * 0.7 * size else 0.0
    elif name == "InvDiamond":
      return 1.0 if (abs(nx) + abs(ny)) > (1.0 - eff_density) * 0.7 * size else 0.0
    elif name == "Cross":
      return 1.0 if (abs(nx) < eff_density * 0.12 * size or
              abs(ny) < eff_density * 0.12 * size) else 0.0
    elif name == "XShape":
      return 1.0 if abs(abs(nx) - abs(ny)) < eff_density * 0.12 * size else 0.0
    elif name == "Star5":
      r_star = 0.25 + 0.15 * math.cos((a_norm * 5 - round(a_norm * 5)) * 2 * math.pi)
      return 1.0 if dist < r_star * eff_density * size else 0.0
    elif name == "Star6":
      r_star = 0.25 + 0.15 * math.cos((a_norm * 6 - round(a_norm * 6)) * 2 * math.pi)
      return 1.0 if dist < r_star * eff_density * size else 0.0
    elif name == "Arrow_R":
      return 1.0 if (nx > -eff_density * 0.3 and abs(ny) < (nx + 0.5) * eff_density) else 0.0
    elif name == "Arrow_L":
      return 1.0 if (nx < eff_density * 0.3 and abs(ny) < (-nx + 0.5) * eff_density) else 0.0
    elif name == "Arrow_U":
      return 1.0 if (ny < eff_density * 0.3 and abs(nx) < (-ny + 0.5) * eff_density) else 0.0
    elif name == "Arrow_D":
      return 1.0 if (ny > -eff_density * 0.3 and abs(nx) < (ny + 0.5) * eff_density) else 0.0
    elif name == "Triangle_U":
      return 1.0 if ny < (0.5 - abs(nx)) * eff_density * 2 else 0.0
    elif name == "Triangle_D":
      return 1.0 if ny > -(0.5 - abs(nx)) * eff_density * 2 else 0.0
    elif name == "Triangle_L":
      return 1.0 if nx < (0.5 - abs(ny)) * eff_density * 2 else 0.0
    elif name == "Triangle_R":
      return 1.0 if nx > -(0.5 - abs(ny)) * eff_density * 2 else 0.0
    elif name == "HalfL":
      return 1.0 if nx < (eff_density - 0.5) else 0.0
    elif name == "HalfR":
      return 1.0 if nx > (0.5 - eff_density) else 0.0
    elif name == "HalfT":
      return 1.0 if ny < (eff_density - 0.5) else 0.0
    elif name == "HalfB":
      return 1.0 if ny > (0.5 - eff_density) else 0.0
    elif name == "QuadTL":
      return 1.0 if nx < 0 and ny < 0 else 0.0
    elif name == "QuadTR":
      return 1.0 if nx > 0 and ny < 0 else 0.0
    elif name == "QuadBL":
      return 1.0 if nx < 0 and ny > 0 else 0.0
    elif name == "QuadBR":
      return 1.0 if nx > 0 and ny > 0 else 0.0

    # ── 网格图案 ────────────────────────────────────────────────────────
    elif name == "Checkerboard":
      gx = int((nx + 0.5) * 6 * size)
      gy = int((ny + 0.5) * 6 * size)
      step = int(eff_phase * 4) % 4
      return 1.0 if (gx + gy + step) % 2 == 0 else 0.0
    elif name == "Checkerboard_Fine":
      gx = int((nx + 0.5) * 12 * size)
      gy = int((ny + 0.5) * 12 * size)
      step = int(eff_phase * 4) % 4
      return 1.0 if (gx + gy + step) % 2 == 0 else 0.0
    elif name == "Stripes_H":
      stripe = int((ny + 0.5) * 10 * size + eff_phase * 10)
      return 1.0 if stripe % 2 == 0 else 0.0
    elif name == "Stripes_V":
      stripe = int((nx + 0.5) * 10 * size + eff_phase * 10)
      return 1.0 if stripe % 2 == 0 else 0.0
    elif name == "Stripes_D45":
      stripe = int((nx + ny + 1.0) * 8 * size + eff_phase * 10)
      return 1.0 if stripe % 2 == 0 else 0.0
    elif name == "Stripes_D135":
      stripe = int((nx - ny + 1.0) * 8 * size + eff_phase * 10)
      return 1.0 if stripe % 2 == 0 else 0.0
    elif name == "Dots_Grid":
      gx = (nx + 0.5) * 8 * size
      gy = (ny + 0.5) * 8 * size
      return 1.0 if (abs(gx - round(gx)) < 0.25 and abs(gy - round(gy)) < 0.25) else 0.0
    elif name == "Dots_Hex":
      # 六边形点阵
      row = int((ny + 0.5) * 8 * size)
      offset = 0.5 if row % 2 == 0 else 0.0
      gx = (nx + 0.5 + offset) * 8 * size
      return 1.0 if abs(gx - round(gx)) < 0.3 else 0.0
    elif name == "Border":
      b = eff_density * 0.15 * size
      return 1.0 if (abs(nx) > 0.5 - b or abs(ny) > 0.5 - b) else 0.0
    elif name == "Border_Inner":
      b1 = eff_density * 0.2 * size
      b2 = b1 - 0.08 * size
      return 1.0 if (abs(nx) > 0.5 - b1 or abs(ny) > 0.5 - b1) and \
              not (abs(nx) > 0.5 - b2 or abs(ny) > 0.5 - b2) else 0.0
    elif name == "Cross_Grid":
      gx = (nx + 0.5) * 6 * size
      gy = (ny + 0.5) * 6 * size
      return 1.0 if (abs(gx - round(gx)) < 0.2 or abs(gy - round(gy)) < 0.2) else 0.0
    elif name == "Zigzag_H":
      zz = abs(((nx + 0.5) * 10 * size + eff_phase * 10) % 2.0 - 1.0)
      return 1.0 if abs((ny + 0.5) - zz * 0.5) < eff_density * 0.1 else 0.0
    elif name == "Zigzag_V":
      zz = abs(((ny + 0.5) * 10 * size + eff_phase * 10) % 2.0 - 1.0)
      return 1.0 if abs((nx + 0.5) - zz * 0.5) < eff_density * 0.1 else 0.0
    elif name == "Grid_Sparse":
      gx = int((nx + 0.5) * 4 * size)
      gy = int((ny + 0.5) * 4 * size)
      return 1.0 if (gx + gy) % 3 == 0 else 0.0
    elif name == "Grid_Dense":
      gx = int((nx + 0.5) * 10 * size)
      gy = int((ny + 0.5) * 10 * size)
      return 1.0 if (gx + gy) % 2 == 0 else 0.0

    # ── 动态波形 ────────────────────────────────────────────────────────
    elif name == "Wave_H":
      wave = (math.sin((ny + 0.5) * 10 * size + eff_phase * 2 * math.pi) + 1) / 2
      return wave * eff_density
    elif name == "Wave_V":
      wave = (math.sin((nx + 0.5) * 10 * size + eff_phase * 2 * math.pi) + 1) / 2
      return wave * eff_density
    elif name == "Wave_D":
      wave = (math.sin((nx + ny + 1.0) * 8 * size + eff_phase * 2 * math.pi) + 1) / 2
      return wave * eff_density
    elif name == "Wave_Radial":
      wave = (math.sin(dist * 20 * size - eff_phase * 2 * math.pi) + 1) / 2
      return wave * eff_density
    elif name == "Ripple":
      wave = (math.sin(dist * 28 * size - eff_phase * 4 * math.pi) + 1) / 2
      return wave * eff_density
    elif name == "Ripple_Fast":
      wave = (math.sin(dist * 40 * size - eff_phase * 6 * math.pi) + 1) / 2
      return wave * eff_density
    elif name == "Spiral_CW":
      sv = (a_norm + dist * 3 * size + eff_phase) % 1.0
      return 1.0 if sv < eff_density * 0.4 else 0.0
    elif name == "Spiral_CCW":
      sv = (a_norm - dist * 3 * size + eff_phase) % 1.0
      return 1.0 if sv < eff_density * 0.4 else 0.0
    elif name == "Tornado":
      sv = (a_norm + dist * 2 * size - eff_phase) % 1.0
      return 1.0 if sv < eff_density * 0.35 else 0.0
    elif name == "Vortex":
      sv = (a_norm + eff_phase) % 1.0
      r_norm = dist / 0.7
      return 1.0 if abs(sv - r_norm % 1.0) < eff_density * 0.2 else 0.0
    elif name == "Sine_Fade":
      return (math.sin(t * math.pi * 4 * size + eff_phase * 2 * math.pi) + 1) / 2 * eff_density
    elif name == "Cosine_Fade":
      return (math.cos(t * math.pi * 4 * size + eff_phase * 2 * math.pi) + 1) / 2 * eff_density
    elif name == "Pulse_Out":
      r_p = (eff_phase % 1.0) * 0.8 * size
      return max(0.0, 1.0 - abs(dist - r_p) * 10 / size) * eff_density
    elif name == "Pulse_In":
      r_p = (1.0 - eff_phase % 1.0) * 0.8 * size
      return max(0.0, 1.0 - abs(dist - r_p) * 10 / size) * eff_density
    elif name == "Scan_H":
      pos = (eff_phase % 1.0) - 0.5
      return max(0.0, 1.0 - abs(ny - pos) * 8 / size) * eff_density
    elif name == "Scan_V":
      pos = (eff_phase % 1.0) - 0.5
      return max(0.0, 1.0 - abs(nx - pos) * 8 / size) * eff_density
    elif name == "Scan_Radial":
      a_target = (eff_phase * 2 * math.pi) % (2 * math.pi) - math.pi
      a_diff = abs(((angle - a_target + math.pi) % (2 * math.pi)) - math.pi)
      return max(0.0, 1.0 - a_diff * 3 / size) * eff_density

    # ── 追逐序列 ────────────────────────────────────────────────────────
    elif name == "Chase_L2R":
      pos = (eff_phase % 1.0) - 0.5
      return max(0.0, 1.0 - abs(nx - pos) * 8 / size) * eff_density
    elif name == "Chase_R2L":
      pos = 0.5 - (eff_phase % 1.0)
      return max(0.0, 1.0 - abs(nx - pos) * 8 / size) * eff_density
    elif name == "Chase_T2B":
      pos = (eff_phase % 1.0) - 0.5
      return max(0.0, 1.0 - abs(ny - pos) * 8 / size) * eff_density
    elif name == "Chase_B2T":
      pos = 0.5 - (eff_phase % 1.0)
      return max(0.0, 1.0 - abs(ny - pos) * 8 / size) * eff_density
    elif name == "Chase_In":
      r_target = (0.5 - (eff_phase % 0.5)) * size
      return max(0.0, 1.0 - abs(dist - r_target) * 8) * eff_density
    elif name == "Chase_Out":
      r_target = (eff_phase % 0.5) * size
      return max(0.0, 1.0 - abs(dist - r_target) * 8) * eff_density
    elif name == "Chase_Spiral_CW":
      a_target = (eff_phase * 2 * math.pi) % (2 * math.pi) - math.pi
      a_diff = abs(((angle - a_target + math.pi) % (2 * math.pi)) - math.pi)
      return 1.0 if a_diff < eff_density * 0.6 * size else 0.0
    elif name == "Chase_Spiral_CCW":
      a_target = -(eff_phase * 2 * math.pi) % (2 * math.pi) - math.pi
      a_diff = abs(((angle - a_target + math.pi) % (2 * math.pi)) - math.pi)
      return 1.0 if a_diff < eff_density * 0.6 * size else 0.0
    elif name == "SingleDot":
      target = int(eff_phase * n) % n
      return max(0.0, 1.0 - abs(idx - target) * 2) * eff_density
    elif name == "DoubleDot":
      t1 = int(eff_phase * n) % n
      t2 = (t1 + n // 2) % n
      return max(0.0, max(1.0 - abs(idx - t1) * 2,
                 1.0 - abs(idx - t2) * 2)) * eff_density
    elif name == "TripleDot":
      t1 = int(eff_phase * n) % n
      t2 = (t1 + n // 3) % n
      t3 = (t1 + 2 * n // 3) % n
      return max(0.0, max(1.0 - abs(idx - t1) * 2,
                 1.0 - abs(idx - t2) * 2,
                 1.0 - abs(idx - t3) * 2)) * eff_density
    elif name == "GroupChase":
      total_g = max(1, (n + group_sz - 1) // group_sz)
      step = int(eff_phase * total_g) % total_g
      return 1.0 if idx // group_sz == step else 0.0
    elif name == "GroupAlternate":
      return 1.0 if (idx // group_sz + int(eff_phase * 10)) % 2 == 0 else 0.0
    elif name == "GroupTriple":
      pos_in_g = idx % group_sz
      g_idx = idx // group_sz
      if (g_idx + int(eff_phase * 10)) % 2 == 0:
        return 1.0 if pos_in_g == 0 else 0.0
      else:
        return 1.0 if pos_in_g > 0 else 0.0
    elif name == "BounceH":
      pos = abs(math.sin(eff_phase * math.pi)) - 0.5
      return max(0.0, 1.0 - abs(nx - pos) * 8 / size) * eff_density
    elif name == "BounceV":
      pos = abs(math.sin(eff_phase * math.pi)) - 0.5
      return max(0.0, 1.0 - abs(ny - pos) * 8 / size) * eff_density
    elif name == "Snake_H":
      row = int((ny + 0.5) * 4 * size)
      if row % 2 == 0:
        target_x = (eff_phase % 1.0) - 0.5
      else:
        target_x = 0.5 - (eff_phase % 1.0)
      return max(0.0, 1.0 - abs(nx - target_x) * 8 / size) * eff_density
    elif name == "Snake_V":
      col = int((nx + 0.5) * 4 * size)
      if col % 2 == 0:
        target_y = (eff_phase % 1.0) - 0.5
      else:
        target_y = 0.5 - (eff_phase % 1.0)
      return max(0.0, 1.0 - abs(ny - target_y) * 8 / size) * eff_density

    # ── 特殊效果 ────────────────────────────────────────────────────────
    elif name == "Firework":
      t_p = eff_phase % 1.0
      r_fw = t_p * 0.7 * size
      return max(0.0, 1.0 - abs(dist - r_fw) * 8) * (1.0 - t_p) * eff_density
    elif name == "Firework_Multi":
      t_p = eff_phase % 1.0
      # 多个爆炸中心
      centers = [(0.2, 0.2), (-0.2, 0.2), (0.0, -0.2)]
      best = 0.0
      for cx2, cy2 in centers:
        d2 = math.sqrt((nx - cx2) ** 2 + (ny - cy2) ** 2)
        r_fw = t_p * 0.5 * size
        best = max(best, max(0.0, 1.0 - abs(d2 - r_fw) * 8) * (1.0 - t_p))
      return best * eff_density
    elif name == "Heartbeat":
      beat = abs(math.sin(eff_phase * 2 * math.pi))
      return 1.0 if dist < beat * eff_density * 0.6 else 0.0
    elif name == "Breathe":
      return (math.sin(eff_phase * 2 * math.pi) + 1) / 2 * eff_density
    elif name == "Lightning":
      rng = random.Random(int(eff_phase * 30))
      bolt_x = rng.random() - 0.5
      return max(0.0, 1.0 - abs(nx - bolt_x) * 5) * eff_density
    elif name == "Meteor":
      m_x = (eff_phase * 1.5 % 1.5) - 0.75
      m_y = (eff_phase % 1.0) - 0.5
      d_m = math.sqrt((nx - m_x) ** 2 + (ny - m_y) ** 2)
      return max(0.0, 1.0 - d_m / max(0.01, eff_density * 0.3)) * eff_density
    elif name == "Meteor_Shower":
      best = 0.0
      for i in range(3):
        rng = random.Random(i * 137 + int(eff_phase * 20))
        m_x = rng.random() - 0.5
        m_y = (eff_phase * (0.8 + rng.random() * 0.4) % 1.0) - 0.5
        d_m = math.sqrt((nx - m_x) ** 2 + (ny - m_y) ** 2)
        best = max(best, max(0.0, 1.0 - d_m / max(0.01, 0.2)))
      return best * eff_density
    elif name == "Sparkle":
      rng = random.Random(hash(f.get("id", idx)) + int(eff_phase * 15))
      return 1.0 if rng.random() < eff_density * 0.3 else 0.0
    elif name == "Sparkle_Dense":
      rng = random.Random(hash(f.get("id", idx)) + int(eff_phase * 20))
      return 1.0 if rng.random() < eff_density * 0.6 else 0.0
    elif name == "Noise":
      rng = random.Random(hash(f.get("id", idx)) + int(eff_phase * 25))
      return rng.random() * eff_density
    elif name == "Noise_Smooth":
      # 平滑噪声：相邻帧连续
      rng1 = random.Random(hash(f.get("id", idx)) + int(eff_phase * 10))
      rng2 = random.Random(hash(f.get("id", idx)) + int(eff_phase * 10) + 1)
      alpha = (eff_phase * 10) % 1.0
      return (rng1.random() * (1 - alpha) + rng2.random() * alpha) * eff_density
    elif name == "Starburst":
      n_rays = max(2, int(eff_density * 12))
      ray_a = a_norm * n_rays
      return 1.0 if abs(ray_a - round(ray_a)) < 0.12 * size else 0.0
    elif name == "Kaleidoscope":
      # 万花筒：6 重对称
      a_sym = (a_norm * 6) % 1.0
      if a_sym > 0.5:
        a_sym = 1.0 - a_sym
      sv = (a_sym + dist * 3 * size + eff_phase) % 1.0
      return 1.0 if sv < eff_density * 0.4 else 0.0
    elif name == "Cascade_T":
      cascade = eff_phase % 1.0
      return 1.0 if abs(ny + 0.5 - cascade) < eff_density * 0.1 * size else 0.0
    elif name == "Cascade_B":
      cascade = 1.0 - eff_phase % 1.0
      return 1.0 if abs(ny + 0.5 - cascade) < eff_density * 0.1 * size else 0.0
    elif name == "Wipe_L2R":
      pos = (eff_phase % 1.0) - 0.5
      return 1.0 if nx < pos else 0.0
    elif name == "Wipe_R2L":
      pos = 0.5 - (eff_phase % 1.0)
      return 1.0 if nx > pos else 0.0

    # ── 喷泉专用 ────────────────────────────────────────────────────────
    elif name == "Fountain_Rise":
      rise = eff_phase % 1.0
      return 1.0 if ny + 0.5 < rise * eff_density * 2 else 0.0
    elif name == "Fountain_Fall":
      fall = 1.0 - eff_phase % 1.0
      return 1.0 if ny + 0.5 > fall * (2 - eff_density) else 0.0
    elif name == "Fountain_Fan":
      fan = abs(nx) / max(0.001, abs(ny) + 0.1)
      return 1.0 if fan < eff_density * 2 * size else 0.0
    elif name == "Fountain_Fan_Wide":
      fan = abs(nx) / max(0.001, abs(ny) + 0.05)
      return 1.0 if fan < eff_density * 3 * size else 0.0
    elif name == "Fountain_Center":
      return 1.0 if abs(nx) < eff_density * 0.2 * size else 0.0
    elif name == "Fountain_Sides":
      return 1.0 if abs(nx) > (1 - eff_density) * 0.4 * size else 0.0
    elif name == "Fountain_Wave":
      wave = (math.sin((nx + 0.5) * 8 * size + eff_phase * 2 * math.pi) + 1) / 2
      return wave * eff_density
    elif name == "Fountain_Ripple":
      wave = (math.sin(dist * 25 * size - eff_phase * 3 * math.pi) + 1) / 2
      return wave * eff_density
    elif name == "Fountain_Chase":
      target = int(eff_phase * n) % n
      return max(0.0, 1.0 - abs(idx - target) / max(1, n * eff_density * 0.3))
    elif name == "Fountain_Alternate":
      return 1.0 if (idx + int(eff_phase * n)) % 2 == 0 else 0.0
    elif name == "Fountain_Spiral":
      sv = (a_norm + dist * 2 * size + eff_phase * 2) % 1.0
      return max(0.0, 1.0 - abs(sv - 0.5) * 3) * eff_density
    elif name == "Fountain_Random":
      rng = random.Random(hash(f.get("id", idx)) + int(eff_phase * 8))
      return rng.random() * eff_density
    elif name == "Fountain_Heartbeat":
      beat = abs(math.sin(eff_phase * 2 * math.pi))
      return beat * eff_density
    elif name == "Fountain_Breathe":
      return (math.sin(eff_phase * 2 * math.pi) + 1) / 2 * eff_density
    elif name == "Fountain_Burst":
      t_p = eff_phase % 1.0
      r_b = t_p * 0.8 * size
      return max(0.0, 1.0 - abs(dist - r_b) * 6) * (1.0 - t_p * 0.5) * eff_density

    # ── 坐标艺术 ────────────────────────────────────────────────────────
    elif name == "Lissajous":
      a_freq, b_freq = 3, 2
      lx = math.sin(a_freq * eff_phase * 2 * math.pi)
      ly = math.sin(b_freq * eff_phase * 2 * math.pi + math.pi / 4)
      d_l = math.sqrt((nx - lx * 0.4) ** 2 + (ny - ly * 0.4) ** 2)
      return max(0.0, 1.0 - d_l * 6 / size) * eff_density
    elif name == "Mandala":
      n_sym = 8
      a_sym = (a_norm * n_sym) % 1.0
      if a_sym > 0.5:
        a_sym = 1.0 - a_sym
      wave = (math.sin(a_sym * math.pi * 4 + dist * 15 * size + eff_phase * 2 * math.pi) + 1) / 2
      return wave * eff_density
    elif name == "Fractal_Tree":
      # 简化分形：多层次波纹
      v = 0.0
      for k in range(1, 4):
        v += (math.sin(dist * 10 * k * size + eff_phase * 2 * math.pi * k) + 1) / (2 * k)
      return min(1.0, v / 1.5) * eff_density
    elif name == "Moire":
      stripe1 = int((nx + 0.5) * 10 * size + eff_phase * 5)
      stripe2 = int((nx + ny + 1.0) * 9 * size)
      return 1.0 if (stripe1 + stripe2) % 2 == 0 else 0.0
    elif name == "Interference":
      # 两个波源的干涉
      d1 = math.sqrt((nx + 0.3) ** 2 + ny ** 2)
      d2 = math.sqrt((nx - 0.3) ** 2 + ny ** 2)
      wave = (math.sin(d1 * 20 * size - eff_phase * 2 * math.pi) +
          math.sin(d2 * 20 * size - eff_phase * 2 * math.pi)) / 2
      return (wave + 1) / 2 * eff_density
    elif name == "Voronoi":
      # 简化 Voronoi：最近种子点
      rng = random.Random(42)
      seeds = [(rng.random() - 0.5, rng.random() - 0.5) for _ in range(5)]
      dists = sorted(math.sqrt((nx - sx) ** 2 + (ny - sy) ** 2) for sx, sy in seeds)
      edge = abs(dists[0] - dists[1])
      return 1.0 if edge < eff_density * 0.08 * size else 0.0
    elif name == "Gradient_Radial":
      return max(0.0, 1.0 - dist / (0.7 * size)) * eff_density
    elif name == "Gradient_Linear":
      return ((nx + 0.5) * math.cos(eff_phase * 2 * math.pi) +
          (ny + 0.5) * math.sin(eff_phase * 2 * math.pi)) * eff_density
    elif name == "Concentric":
      rings = int(dist * 8 * size + eff_phase * 8) % 2
      return 1.0 if rings == 0 else 0.0
    elif name == "Pinwheel":
      n_blades = max(2, int(eff_density * 8))
      blade_a = (a_norm * n_blades + dist * 2 * size + eff_phase) % 1.0
      return 1.0 if blade_a < 0.5 else 0.0

    # ── Chroma 效果（返回色相属性，由 PatternVisualizer 解码成 RGB）────────────────
    # 注意：Chroma 图案返回的是色相属性 [0,1)，而非亮度权重
    # 由于权重必须在 [0,1]，我们用 [0,1] 表示色相，并在 PatternVisualizer 中识别
    # 实际亮度由 eff_density 控制
    elif name == "Chroma_Wave":
      # 彩虹波浪：横向色带流动，摄杆X调制波长，音频加速
      hue = ((nx + 0.5) * size + eff_phase) % 1.0
      return hue * eff_density
    elif name == "Chroma_Ripple":
      # 彩虹涟漪：径向色环扩散，摔杆调制环宽
      hue = (dist * 4 * size - eff_phase * 2) % 1.0
      return hue * eff_density
    elif name == "Chroma_Spiral":
      # 彩虹螺旋：角度+半径共同决定色相
      hue = (a_norm + dist * 2 * size + eff_phase) % 1.0
      return hue * eff_density
    elif name == "Chroma_Scan":
      # 彩虹扫描：单色光柱扫过，光柱内色相渐变
      pos = (eff_phase % 1.0) - 0.5
      beam_w = 0.15 * size
      in_beam = abs(nx - pos) < beam_w
      if not in_beam:
        return 0.0
      hue = (eff_phase * 3) % 1.0
      return hue * eff_density
    elif name == "Chroma_Explode":
      # 爆炸扩散：中心向外爆发，每个波前色相不同
      r_p = (eff_phase % 1.0) * 0.8 * size
      ring_w = 0.12 * size
      in_ring = abs(dist - r_p) < ring_w
      if not in_ring:
        return 0.0
      hue = (a_norm + eff_phase * 2) % 1.0
      return hue * eff_density
    elif name == "Chroma_Rain":
      # 矩阵雨：竖向彩色字符流，每列独立速度和色相
      col_idx = int((nx + 0.5) * 12 * size)
      rng = random.Random(col_idx * 31337)
      col_speed = 0.5 + rng.random() * 1.5
      col_hue = rng.random()
      drop_y = (eff_phase * col_speed + rng.random()) % 1.0 - 0.5
      drop_len = 0.08 + rng.random() * 0.12
      in_drop = 0 < (ny - drop_y) < drop_len
      if not in_drop:
        return 0.0
      fade = 1.0 - (ny - drop_y) / drop_len
      return ((col_hue + fade * 0.1) % 1.0) * eff_density
    elif name == "Chroma_Fire":
      # 火焰效果：底部向上燃烧，高温区展示白色/黄色，低温区展示红色
      y_norm = ny + 0.5 # [0,1] 底部=0顶部=1
      rng = random.Random(int((nx + 0.5) * 20) + int(eff_phase * 15))
      noise = rng.random() * 0.25
      heat = max(0.0, 1.0 - y_norm * 1.2 + noise) * eff_density
      if heat < 0.05:
        return 0.0
      # 火焰色相：高温=黄白(0.1)，中温=橙红(0.05)，低温=深红(0.0)
      hue = heat * 0.12
      return hue * eff_density
    elif name == "Chroma_Aurora":
      # 极光：多色水平漂移带，摔杆Y调制带宽
      band = math.sin((ny + 0.5) * 6 * size + eff_phase * 2 * math.pi)
      band2 = math.sin((ny + 0.5) * 3 * size - eff_phase * math.pi + 1.2)
      intensity = (band + band2 + 2) / 4
      if intensity < 0.2:
        return 0.0
      hue = ((nx + 0.5) * 0.3 + eff_phase * 0.5 + ny * 0.2) % 1.0
      return hue * intensity * eff_density
    elif name == "Chroma_Neon_Chase":
      # 霓虹追逐：多个高饱和色点追逐，每个点色相不同
      best_hue = -1.0
      n_dots = max(2, int(eff_density * 6))
      for di in range(n_dots):
        dot_phase = (eff_phase + di / n_dots) % 1.0
        dot_x = math.cos(dot_phase * 2 * math.pi) * 0.4 * size
        dot_y = math.sin(dot_phase * 2 * math.pi) * 0.4 * size
        d_dot = math.sqrt((nx - dot_x) ** 2 + (ny - dot_y) ** 2)
        if d_dot < 0.12 * size:
          hue = (di / n_dots + eff_phase * 0.5) % 1.0
          best_hue = hue
          break
      if best_hue < 0:
        return 0.0
      return best_hue * eff_density
    elif name == "Chroma_Starfield":
      # 星空闪烁：随机亮点闪烁，每个星有独立色相
      rng = random.Random(hash(f.get("id", idx)) * 7919)
      star_hue = rng.random()
      flicker_speed = 0.5 + rng.random() * 3.0
      brightness = (math.sin(eff_phase * flicker_speed * 2 * math.pi + rng.random() * 6) + 1) / 2
      if brightness < 0.3:
        return 0.0
      return star_hue * brightness * eff_density
    elif name == "Chroma_Plasma":
      # 等离子：多波叠加彩色噪声
      v1 = math.sin((nx + 0.5) * 8 * size + eff_phase * 2 * math.pi)
      v2 = math.sin((ny + 0.5) * 8 * size + eff_phase * 2 * math.pi * 0.7)
      v3 = math.sin(dist * 12 * size - eff_phase * 3 * math.pi)
      v4 = math.sin((nx + ny + 1.0) * 6 * size + eff_phase * math.pi)
      plasma = (v1 + v2 + v3 + v4 + 4) / 8
      hue = plasma
      return hue * eff_density
    elif name == "Chroma_Comet":
      # 彗星拖尾：带余晖的扫描点，头部白色，尾部渐变色相
      pos = (eff_phase % 1.0) - 0.5
      d_head = abs(nx - pos)
      tail_len = 0.25 * size
      in_tail = 0 < (pos - nx) < tail_len
      if d_head < 0.04 * size: # 头部
        hue = (eff_phase * 4) % 1.0
        return hue * eff_density
      elif in_tail:
        fade = 1.0 - (pos - nx) / tail_len
        hue = ((eff_phase * 4 + (1 - fade) * 0.3) % 1.0)
        return hue * fade * eff_density
      return 0.0
    elif name == "Chroma_Heartbeat":
      # 彩虹心跳：脉冲扩散+色相随时间旋转
      beat = abs(math.sin(eff_phase * 2 * math.pi))
      in_beat = dist < beat * eff_density * 0.6
      if not in_beat:
        return 0.0
      hue = (eff_phase * 2 + a_norm * 0.3) % 1.0
      return hue * eff_density
    elif name == "Chroma_Snake":
      # 彩虹蛇形：蛇形路径+色相随路径偏移
      row_s = int((ny + 0.5) * 4 * size)
      if row_s % 2 == 0:
        target_x = (eff_phase % 1.0) - 0.5
      else:
        target_x = 0.5 - (eff_phase % 1.0)
      d_snake = abs(nx - target_x)
      if d_snake > 0.12 * size:
        return 0.0
      path_progress = (nx + 0.5 + row_s * 0.5) / (size * 2)
      hue = (path_progress + eff_phase) % 1.0
      return hue * eff_density
    elif name == "Chroma_Vortex":
      # 彩虹漩涡：旋转+径向色相
      sv = (a_norm + eff_phase) % 1.0
      r_norm = dist / 0.7
      in_vortex = abs(sv - r_norm % 1.0) < eff_density * 0.2
      if not in_vortex:
        return 0.0
      hue = (a_norm * 0.5 + dist * 2 * size + eff_phase * 0.3) % 1.0
      return hue * eff_density

    return 0.0


# ══════════════════════════════════════════════════════════════════════════════
# 图案帧数据类
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class PatternFrame:
  name: str = "新图案帧"
  pattern: str = "All_On"
  ch_type: str = "dimmer"
  density: float = 0.8
  size: float = 1.0
  phase_start: float = 0.0
  phase_speed: float = 0.02  # 0=静止
  duration: float = 2.0    # 0=无限
  transition: float = 0.5
  group_size: int = 3
  color: str = "#4A90E2"
  # 音频/非音频双模式与空间造型参数
  audio_mode: bool = False   # False=自走序列；True=使用慢速音频包络/频谱驱动
  layers: int = 6        # 空间层数/级数
  hollow: float = 0.0      # 空心比例 [0,0.95]
  edge_width: float = 1.0    # 边线宽度倍率
  branch_count: int = 8     # 分叉/花瓣/轴数
  expand_dir: int = 0      # 0=全向 1=水平 2=垂直 3=对角
  mirror_axis: int = 3     # 0=竖轴 1=横轴 2=对角 3=多轴
  circle_diamond_mix: float = 0.5
  spectrum_zone_strength: float = 0.7
  step_speed: float = 1.0
  # 摇杆调制目标参数
  joy_target: str = "phase"  # phase / density / cx / cy / rotation / size
  # ★ 设备 on/off 选择（None=全部输出；set=只输出选中的 fixture_id）
  _selected_fixture_ids: Optional[set] = field(default=None, repr=False)

  def to_params(self, phase: float, cx: float = 0.0, cy: float = 0.0,
         audio_rms: float = 0.0, audio_beat: float = 0.0,
         audio_bands: Optional[List[float]] = None) -> Dict:
    return {
      "type": self.pattern,
      "density": self.density,
      "size": self.size,
      "phase": phase,
      "group_size": self.group_size,
      "ch_type": self.ch_type,
      "cx": cx,
      "cy": cy,
      "audio_mode": self.audio_mode,
      "audio_rms": audio_rms,
      "audio_beat": audio_beat,
      "audio_bands": list(audio_bands or [0.0] * 8),
      "layers": self.layers,
      "hollow": self.hollow,
      "edge_width": self.edge_width,
      "branch_count": self.branch_count,
      "expand_dir": self.expand_dir,
      "mirror_axis": self.mirror_axis,
      "circle_diamond_mix": self.circle_diamond_mix,
      "spectrum_zone_strength": self.spectrum_zone_strength,
      "step_speed": self.step_speed,
    }


# ══════════════════════════════════════════════════════════════════════════════
# 内置预设序列
# ══════════════════════════════════════════════════════════════════════════════
BUILTIN_SEQUENCES: Dict[str, List[Dict]] = {
  # 精简后的高质量矩阵预设体系：只保留空间点阵、窗花拓扑、音频/非音频双模式相关预设。
  # 说明：branch_count 对应“翼”（波形数/分支数/对称瓣数），group_size 对应“块”（基本单元/分组粒度）。
  # 在矩阵主页面加载预设时，主页面已有“翼/块”控件会统一覆盖这些默认值。
  "中心环形层级": [
    {"name": "核心亮环", "pattern": "Lattice_Circle_Rings", "density": 0.28, "size": 1.0, "phase_speed": 0.010, "duration": 3.2, "transition": 1.0, "color": "#9EDBFF", "layers": 6, "hollow": 0.08, "edge_width": 1.0, "branch_count": 8, "group_size": 2},
    {"name": "空心环阵", "pattern": "Lattice_Circle_Hollow", "density": 0.30, "size": 1.0, "phase_speed": 0.009, "duration": 3.4, "transition": 1.0, "color": "#C8F5FF", "layers": 7, "hollow": 0.24, "edge_width": 1.2, "branch_count": 8, "group_size": 2},
    {"name": "扇区环门", "pattern": "Lattice_Circle_Sectors", "density": 0.30, "size": 1.0, "phase_speed": 0.011, "duration": 3.0, "transition": 0.9, "color": "#E8FCFF", "layers": 8, "hollow": 0.12, "edge_width": 1.0, "branch_count": 10, "group_size": 3},
  ],
  "菱形窗格结构": [
    {"name": "菱形层线", "pattern": "Lattice_Diamond_Rings", "density": 0.28, "size": 1.0, "phase_speed": 0.010, "duration": 3.0, "transition": 0.9, "color": "#FFD6A6", "layers": 7, "hollow": 0.10, "edge_width": 1.1, "branch_count": 8, "group_size": 2},
    {"name": "方圆叠合", "pattern": "Lattice_Diamond_Circle", "density": 0.30, "size": 1.0, "phase_speed": 0.010, "duration": 3.2, "transition": 1.0, "color": "#FFE4C8", "layers": 7, "hollow": 0.16, "edge_width": 1.1, "circle_diamond_mix": 0.55, "branch_count": 8, "group_size": 2},
    {"name": "对角门线", "pattern": "Lattice_X_Axis", "density": 0.26, "size": 1.0, "phase_speed": 0.011, "duration": 2.8, "transition": 0.9, "color": "#FFF0D8", "layers": 6, "hollow": 0.06, "edge_width": 1.2, "branch_count": 6, "group_size": 3},
  ],
  "十字轴线拓扑": [
    {"name": "横竖主轴", "pattern": "Lattice_Cross_Axis", "density": 0.26, "size": 1.0, "phase_speed": 0.010, "duration": 2.8, "transition": 0.9, "color": "#B8FFEA", "layers": 6, "hollow": 0.06, "edge_width": 1.1, "branch_count": 4, "group_size": 2},
    {"name": "多轴叠格", "pattern": "Lattice_Symmetry_Axis", "density": 0.28, "size": 1.0, "phase_speed": 0.010, "duration": 3.0, "transition": 1.0, "color": "#CCFFF1", "layers": 8, "hollow": 0.14, "edge_width": 1.0, "mirror_axis": 3, "branch_count": 8, "group_size": 2},
    {"name": "镜像追逐", "pattern": "Lattice_Mirror_Chase", "density": 0.26, "size": 1.0, "phase_speed": 0.011, "duration": 3.0, "transition": 1.0, "color": "#E8FFF7", "layers": 7, "hollow": 0.12, "edge_width": 1.2, "mirror_axis": 3, "branch_count": 8, "group_size": 3},
  ],
  "窗花曼陀罗": [
    {"name": "窗花枝杈", "pattern": "Lattice_Window_Flower", "density": 0.32, "size": 1.0, "phase_speed": 0.010, "duration": 3.6, "transition": 1.1, "color": "#D7A7FF", "layers": 8, "hollow": 0.10, "edge_width": 1.0, "branch_count": 12, "group_size": 2},
    {"name": "曼陀罗分支", "pattern": "Lattice_Mandala_Branches", "density": 0.30, "size": 1.0, "phase_speed": 0.010, "duration": 3.6, "transition": 1.1, "color": "#E0BBFF", "layers": 9, "hollow": 0.12, "edge_width": 1.1, "branch_count": 14, "group_size": 2},
    {"name": "剪纸边线", "pattern": "Lattice_PaperCut_Rings", "density": 0.30, "size": 1.0, "phase_speed": 0.009, "duration": 3.6, "transition": 1.1, "color": "#F0D6FF", "layers": 7, "hollow": 0.22, "edge_width": 1.3, "branch_count": 10, "group_size": 3},
  ],
  "雪花六轴": [
    {"name": "六轴骨架", "pattern": "Lattice_Snowflake", "density": 0.26, "size": 1.0, "phase_speed": 0.010, "duration": 3.4, "transition": 1.0, "color": "#E8FCFF", "layers": 7, "hollow": 0.10, "edge_width": 1.0, "branch_count": 6, "group_size": 2},
    {"name": "六角空心", "pattern": "Lattice_Circle_Hollow", "density": 0.28, "size": 1.0, "phase_speed": 0.009, "duration": 3.5, "transition": 1.0, "color": "#C8F5FF", "layers": 6, "hollow": 0.25, "edge_width": 1.2, "branch_count": 6, "group_size": 2},
    {"name": "雪片收束", "pattern": "Lattice_Topology_In", "density": 0.28, "size": 1.0, "phase_speed": 0.010, "duration": 3.2, "transition": 1.0, "color": "#FFFFFF", "layers": 8, "hollow": 0.14, "edge_width": 1.1, "branch_count": 6, "group_size": 3},
  ],
  "螺旋臂阵": [
    {"name": "四臂螺旋", "pattern": "Lattice_Spiral_Arms", "density": 0.26, "size": 1.0, "phase_speed": 0.009, "duration": 3.6, "transition": 1.1, "color": "#88FFCC", "layers": 7, "hollow": 0.12, "edge_width": 1.1, "branch_count": 4, "group_size": 2, "step_speed": 0.75},
    {"name": "八臂稀疏", "pattern": "Lattice_Spiral_Arms", "density": 0.30, "size": 1.0, "phase_speed": 0.010, "duration": 3.6, "transition": 1.1, "color": "#A8FFE0", "layers": 8, "hollow": 0.18, "edge_width": 1.0, "branch_count": 8, "group_size": 2, "step_speed": 0.70},
    {"name": "花瓣轨道", "pattern": "Lattice_Petal_Orbit", "density": 0.28, "size": 1.0, "phase_speed": 0.009, "duration": 3.4, "transition": 1.1, "color": "#CCFFEE", "layers": 6, "hollow": 0.20, "edge_width": 1.2, "branch_count": 8, "group_size": 3},
  ],
  "象限镜像": [
    {"name": "象限同步", "pattern": "Lattice_Quadrant_Mirror", "density": 0.28, "size": 1.0, "phase_speed": 0.012, "duration": 2.8, "transition": 0.9, "color": "#FFD8A8", "layers": 6, "hollow": 0.08, "edge_width": 1.1, "branch_count": 4, "group_size": 2},
    {"name": "扇区镜像", "pattern": "Lattice_Sector_Mirror", "density": 0.30, "size": 1.0, "phase_speed": 0.011, "duration": 3.2, "transition": 1.0, "color": "#FFE6C8", "layers": 8, "hollow": 0.18, "edge_width": 1.0, "branch_count": 8, "group_size": 2},
    {"name": "内外交替", "pattern": "Lattice_Topology_Out", "density": 0.28, "size": 1.0, "phase_speed": 0.010, "duration": 3.2, "transition": 1.0, "color": "#FFF2D8", "layers": 8, "hollow": 0.12, "edge_width": 1.2, "branch_count": 8, "group_size": 3},
  ],
  "稀疏网格": [
    {"name": "稀疏格线", "pattern": "Lattice_Grid_Sparse", "density": 0.22, "size": 1.0, "phase_speed": 0.010, "duration": 3.0, "transition": 0.9, "color": "#A8FFAA", "layers": 7, "hollow": 0.10, "edge_width": 0.9, "branch_count": 8, "group_size": 3},
    {"name": "锯齿对称", "pattern": "Lattice_Zigzag_Sym", "density": 0.26, "size": 1.0, "phase_speed": 0.012, "duration": 3.0, "transition": 0.9, "color": "#C6FFC8", "layers": 6, "hollow": 0.08, "edge_width": 1.1, "branch_count": 8, "group_size": 2},
    {"name": "稀疏星点", "pattern": "Lattice_Sparse_Stars", "density": 0.24, "size": 1.0, "phase_speed": 0.010, "duration": 3.2, "transition": 1.0, "color": "#E0FFE0", "layers": 8, "hollow": 0.16, "edge_width": 1.0, "branch_count": 10, "group_size": 4},
  ],
  "层级级联": [
    {"name": "外扩层级", "pattern": "Lattice_Layer_Cascade", "density": 0.30, "size": 1.0, "phase_speed": 0.010, "duration": 3.4, "transition": 1.0, "color": "#A8C8FF", "layers": 8, "hollow": 0.10, "edge_width": 1.1, "expand_dir": 0, "branch_count": 8, "group_size": 2},
    {"name": "水平扩展", "pattern": "Lattice_Layer_Cascade", "density": 0.28, "size": 1.0, "phase_speed": 0.011, "duration": 3.0, "transition": 0.9, "color": "#BCD6FF", "layers": 7, "hollow": 0.12, "edge_width": 1.1, "expand_dir": 1, "branch_count": 6, "group_size": 2},
    {"name": "垂直扩展", "pattern": "Lattice_Layer_Cascade", "density": 0.28, "size": 1.0, "phase_speed": 0.011, "duration": 3.0, "transition": 0.9, "color": "#D8E8FF", "layers": 7, "hollow": 0.12, "edge_width": 1.1, "expand_dir": 2, "branch_count": 6, "group_size": 3},
  ],
  "音频频谱级联": [
    {"name": "频谱层级", "pattern": "Lattice_Spectrum_Cascade", "density": 0.30, "size": 1.0, "phase_speed": 0.008, "duration": 4.0, "transition": 1.2, "color": "#66AAFF", "audio_mode": True, "layers": 8, "hollow": 0.10, "edge_width": 1.2, "branch_count": 8, "group_size": 2, "spectrum_zone_strength": 0.75, "step_speed": 0.55},
    {"name": "频谱脉冲", "pattern": "Lattice_Spectrum_Pulse", "density": 0.30, "size": 1.0, "phase_speed": 0.008, "duration": 4.0, "transition": 1.2, "color": "#88CCFF", "audio_mode": True, "layers": 7, "hollow": 0.16, "edge_width": 1.2, "branch_count": 10, "group_size": 2, "spectrum_zone_strength": 0.70, "step_speed": 0.50},
    {"name": "频谱螺旋", "pattern": "Lattice_Spectrum_Spiral", "density": 0.28, "size": 1.0, "phase_speed": 0.007, "duration": 4.2, "transition": 1.2, "color": "#AAE0FF", "audio_mode": True, "layers": 7, "hollow": 0.20, "edge_width": 1.1, "branch_count": 6, "group_size": 3, "spectrum_zone_strength": 0.70, "step_speed": 0.50},
  ],
  "音频低中高": [
    {"name": "低中高分层", "pattern": "Lattice_Spectrum_LowMidHigh", "density": 0.32, "size": 1.0, "phase_speed": 0.007, "duration": 4.0, "transition": 1.2, "color": "#FF99CC", "audio_mode": True, "layers": 8, "hollow": 0.12, "edge_width": 1.1, "branch_count": 12, "group_size": 2, "spectrum_zone_strength": 0.72},
    {"name": "镜像频带", "pattern": "Lattice_Spectrum_Mirror", "density": 0.28, "size": 1.0, "phase_speed": 0.007, "duration": 4.0, "transition": 1.2, "color": "#FFBBDD", "audio_mode": True, "layers": 8, "hollow": 0.06, "edge_width": 1.3, "branch_count": 8, "group_size": 2, "spectrum_zone_strength": 0.68},
    {"name": "花形频谱", "pattern": "Lattice_Spectrum_Flower", "density": 0.30, "size": 1.0, "phase_speed": 0.007, "duration": 4.2, "transition": 1.2, "color": "#FFD8EE", "audio_mode": True, "layers": 7, "hollow": 0.18, "edge_width": 1.1, "branch_count": 12, "group_size": 3, "spectrum_zone_strength": 0.70},
  ],
  "双模式对照": [
    {"name": "自走窗花", "pattern": "Lattice_Window_Flower", "density": 0.30, "size": 1.0, "phase_speed": 0.010, "duration": 3.4, "transition": 1.0, "color": "#B8E8FF", "audio_mode": False, "layers": 8, "hollow": 0.12, "branch_count": 10, "group_size": 2, "step_speed": 0.80},
    {"name": "音频窗花", "pattern": "Lattice_Spectrum_Flower", "density": 0.30, "size": 1.0, "phase_speed": 0.006, "duration": 4.0, "transition": 1.2, "color": "#FFC0E8", "audio_mode": True, "layers": 8, "hollow": 0.12, "branch_count": 10, "group_size": 2, "spectrum_zone_strength": 0.68, "step_speed": 0.45},
    {"name": "自走收束", "pattern": "Lattice_Topology_In", "density": 0.28, "size": 1.0, "phase_speed": 0.010, "duration": 3.4, "transition": 1.0, "color": "#D0F0FF", "audio_mode": False, "layers": 8, "hollow": 0.14, "branch_count": 8, "group_size": 3, "step_speed": 0.80},
  ],
}

# ══════════════════════════════════════════════════════════════════════════════
# 图案帧缩略图控件
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# 设备选择画布（底部 TopView 镜像，支持点击/框选高亮）
# ══════════════════════════════════════════════════════════════════════════════
class FixtureSelector(QWidget):
  """渲染 TopView 设备排布图，支持点击/框选选中设备。
  选中状态用高亮颜色显示，未选中设备显示为暗色。
  """
  selection_changed = pyqtSignal(set)  # 发射选中的 fixture id 集合

  FIXTURE_R = 14  # 设备圆点半径（像素）

  def __init__(self, parent=None):
    super().__init__(parent)
    self._fixtures: List[Dict] = []  # 归一化坐标 [0,1]
    self._selected_ids: set = set()
    self._hover_id: str = ""
    # ★ 性能：静态底图缓存（未选中点/名称标签/网格一次渲染），
    #  悬停/框选移动只重绘动态层，不再每次鼠标移动重画 11382 个点。
    self._base_pixmap = None
    self._base_key = None
    self._sel_version: int = 0
    # ★ 性能：屏幕像素桶格命中索引（fixture 变化/尺寸变化时重建，命中 O(1)）
    self._hit_grid_key = None
    self._hit_grid: Dict[tuple, List[tuple]] = {}
    # 框选状态
    self._rubber_start: Optional[QPoint] = None
    self._rubber_end: Optional[QPoint] = None
    self._rubber_active: bool = False
    self.setMinimumHeight(180)
    self.setMouseTracking(True)
    self.setCursor(Qt.CursorShape.CrossCursor)
    self.setStyleSheet(
      f"background:{BG_DARK};border:1px solid {_th.BORDER_NORMAL};border-radius:4px;")

  # ── 公共接口 ──────────────────────────────────────────────────────────────
  def set_fixtures(self, fixtures: List[Dict]):
    """更新设备列表（归一化坐标）。"""
    self._fixtures = fixtures or []
    self._selected_ids = set()
    self._base_key = None
    self._hit_grid_key = None
    self.update()

  def get_selected_ids(self) -> set:
    return set(self._selected_ids)

  def select_all(self):
    self._selected_ids = {f.get("id", "") for f in self._fixtures}
    self._invalidate_base()
    self.update()
    self.selection_changed.emit(self._selected_ids)

  def select_none(self):
    self._selected_ids.clear()
    self._invalidate_base()
    self.update()
    self.selection_changed.emit(self._selected_ids)

  def select_invert(self):
    all_ids = {f.get("id", "") for f in self._fixtures}
    self._selected_ids = all_ids - self._selected_ids
    self._invalidate_base()
    self.update()
    self.selection_changed.emit(self._selected_ids)

  # ── 坐标映射 ──────────────────────────────────────────────────────────────
  def _fixture_screen_pos(self, f: Dict):
    """将归一化坐标 [0,1] 映射到屏幕像素坐标。"""
    W, H = self.width(), self.height()
    margin = self.FIXTURE_R + 8
    pw = W - 2 * margin
    ph = H - 2 * margin
    px = margin + f.get("pos_x", 0.0) * pw
    py = margin + f.get("pos_y", 0.0) * ph
    return int(px), int(py)

  def _fixture_at(self, qp: QPoint) -> Optional[str]:
    """返回鼠标位置处的 fixture id，无则返回 None。

    ★ 性能：屏幕像素桶格索引（每格 2×命中半径），命中 O(1)。
    ★ 语义与原线性扫描逐点一致：命中候选取列表序最小的 fixture
      （重叠圆点时返回与原先完全相同的那一台）。
    """
    grid = self._ensure_hit_grid()
    if grid is None:
      return None
    R = self.FIXTURE_R + 4
    R2 = R * R
    cell = max(1, R * 2)
    qx, qy = qp.x(), qp.y()
    bx, by = qx // cell, qy // cell
    best = None
    best_idx = None
    for ox in (-1, 0, 1):
      for oy in (-1, 0, 1):
        for fid, sx, sy, idx in grid.get((bx + ox, by + oy), ()):
          if (qx - sx) ** 2 + (qy - sy) ** 2 <= R2:
            if best_idx is None or idx < best_idx:
              best_idx = idx
              best = fid
    return best

  def _ensure_hit_grid(self):
    """按屏幕像素桶格索引设备（fixture 列表或尺寸变化时重建）。"""
    if not self._fixtures:
      return None
    key = (id(self._fixtures), self.width(), self.height())
    if (self._hit_grid_key == key
        and getattr(self, "_hit_grid_fixtures", None) is self._fixtures):
      return self._hit_grid
    R = self.FIXTURE_R + 4
    cell = max(1, R * 2)
    grid: Dict[tuple, List[tuple]] = {}
    for idx, f in enumerate(self._fixtures):
      sx, sy = self._fixture_screen_pos(f)
      grid.setdefault((sx // cell, sy // cell), []).append(
        (f.get("id", ""), sx, sy, idx))
    self._hit_grid = grid
    self._hit_grid_key = key
    self._hit_grid_fixtures = self._fixtures
    return grid

  def _invalidate_base(self):
    """选中集变化 → 底图/命中索引失效（下次 paint 重建）。"""
    self._sel_version += 1
    self._base_key = None

  # ── 鼠标事件 ──────────────────────────────────────────────────────────────
  def mousePressEvent(self, e):
    if e.button() == Qt.MouseButton.LeftButton:
      fid = self._fixture_at(e.pos())
      if fid is not None:
        # 点击单个设备：切换选中状态
        if fid in self._selected_ids:
          self._selected_ids.discard(fid)
        else:
          self._selected_ids.add(fid)
        self._invalidate_base()
        self.update()
        self.selection_changed.emit(self._selected_ids)
      else:
        # 空白处开始框选
        self._rubber_start = e.pos()
        self._rubber_end = e.pos()
        self._rubber_active = True

  def mouseMoveEvent(self, e):
    if self._rubber_active:
      self._rubber_end = e.pos()
      self.update()
    else:
      fid = self._fixture_at(e.pos())
      if fid != self._hover_id:
        self._hover_id = fid or ""
        self.update()

  def mouseReleaseEvent(self, e):
    if e.button() == Qt.MouseButton.LeftButton and self._rubber_active:
      self._rubber_active = False
      if self._rubber_start and self._rubber_end:
        # 框选：将框内设备加入选中集合
        rx1 = min(self._rubber_start.x(), self._rubber_end.x())
        ry1 = min(self._rubber_start.y(), self._rubber_end.y())
        rx2 = max(self._rubber_start.x(), self._rubber_end.x())
        ry2 = max(self._rubber_start.y(), self._rubber_end.y())
        if rx2 - rx1 > 4 or ry2 - ry1 > 4:
          for f in self._fixtures:
            sx, sy = self._fixture_screen_pos(f)
            if rx1 <= sx <= rx2 and ry1 <= sy <= ry2:
              self._selected_ids.add(f.get("id", ""))
          self._invalidate_base()
          self.selection_changed.emit(self._selected_ids)
      self._rubber_start = None
      self._rubber_end = None
      self.update()

  # ── 绘制 ──────────────────────────────────────────────────────────────────
  def _ensure_base_pixmap(self):
    """静态底图：背景网格 + 全部设备点（未选中/选中）+ 名称标签。

    ★ 性能：只在 设备列表/尺寸/选中集 变化时重建一次；
    鼠标悬停/框选移动只叠加动态层，不再每 move 重画 11382 个点。
    """
    if not self._fixtures:
      return None
    W, H = self.width(), self.height()
    if W <= 0 or H <= 0:
      return None
    key = (id(self._fixtures), W, H, self._sel_version, self.devicePixelRatioF(),
        _th.CANVAS_REVISION)
    if (self._base_key == key and self._base_pixmap is not None
        and getattr(self, "_base_fixtures", None) is self._fixtures):
      return self._base_pixmap
    pm = hidpi_pixmap(W, H, self)  # C7：底图按 DPR 栅格化
    pm.fill(QColor(BG_DARK))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    paint_bed(p, self.rect())
    grid_pen = QPen(tone(_th.CANVAS_GRID_MINOR), 1)
    p.setPen(grid_pen)
    for gx in range(0, W, max(1, W // 10)):
      p.drawLine(gx, 0, gx, H)
    for gy in range(0, H, max(1, H // 8)):
      p.drawLine(0, gy, W, gy)
    R = self.FIXTURE_R
    for f in self._fixtures:
      fid = f.get("id", "")
      sx, sy = self._fixture_screen_pos(f)
      selected = fid in self._selected_ids
      if selected:
        glow = QRadialGradient(sx, sy, R * 2.5)
        glow.setColorAt(0, selection_fill(65))
        glow.setColorAt(1, selection_fill(0))
        p.setBrush(QBrush(glow))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(sx - R * 2, sy - R * 2, R * 4, R * 4)
        dot_color = QColor(_th.CANVAS_SELECTION_FILL)
        border_color = QColor(_th.CANVAS_SELECTION)
        border_w = 2
      else:
        dot_color = QColor(_th.CANVAS_CARD)
        border_color = QColor(_th.CANVAS_BORDER)
        border_w = 1
      p.setBrush(QBrush(dot_color))
      p.setPen(QPen(border_color, border_w))
      p.drawEllipse(sx - R, sy - R, R * 2, R * 2)
      # 设备名称标签（10px text_secondary，替代不可读的 Arial 6pt）
      name = f.get("name", "")
      if len(name) > 8:
        name = name[:7] + "…"
      p.setPen(QColor(FG_DIM))
      p.setFont(_ui_font(10))
      p.drawText(QRect(sx - 30, sy + R + 1, 60, 12),
            Qt.AlignmentFlag.AlignCenter, name)
    p.end()
    self._base_pixmap = pm
    self._base_key = key
    self._base_fixtures = self._fixtures
    return pm

  def paintEvent(self, event):
    p = QPainter(self)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    W, H = self.width(), self.height()

    base = self._ensure_base_pixmap()
    if base is None:
      # 无设备分支（背景+网格+提示，与原绘制一致）
      paint_bed(p, self.rect())
      grid_pen = QPen(tone(_th.CANVAS_GRID_MINOR), 1)
      p.setPen(grid_pen)
      for gx in range(0, W, max(1, W // 10)):
        p.drawLine(gx, 0, gx, H)
      for gy in range(0, H, max(1, H // 8)):
        p.drawLine(0, gy, W, gy)
      p.setPen(QColor(FG_DIM))
      p.setFont(_ui_font(11))
      p.drawText(QRect(0, 0, W, H), Qt.AlignmentFlag.AlignCenter,
            "暂无设备\n请先在 TopView 中摆放设备并选择设备组")
      p.end()
      return

    # ★ 性能：静态底图整幅 blit + 动态层（悬停/框选/状态栏）叠加
    p.drawPixmap(0, 0, base)
    R = self.FIXTURE_R
    # 悬停高亮（选中设备保持选中样式，与原逻辑一致）
    if self._hover_id and self._hover_id not in self._selected_ids:
      for f in self._fixtures:
        if f.get("id", "") == self._hover_id:
          sx, sy = self._fixture_screen_pos(f)
          p.setBrush(QBrush(tone(_th.CANVAS_SELECTION_FILL)))
          p.setPen(QPen(tone(_th.CANVAS_SELECTION), 1))
          p.drawEllipse(sx - R, sy - R, R * 2, R * 2)
          break

    # 框选矩形
    if self._rubber_active and self._rubber_start and self._rubber_end:
      rx1 = min(self._rubber_start.x(), self._rubber_end.x())
      ry1 = min(self._rubber_start.y(), self._rubber_end.y())
      rx2 = max(self._rubber_start.x(), self._rubber_end.x())
      ry2 = max(self._rubber_start.y(), self._rubber_end.y())
      p.setPen(QPen(tone(_th.CANVAS_SELECTION), 1, Qt.PenStyle.DashLine))
      p.setBrush(QBrush(selection_fill(30)))
      p.drawRect(rx1, ry1, rx2 - rx1, ry2 - ry1)

    # 底部状态栏
    sel_count = len(self._selected_ids)
    total = len(self._fixtures)
    p.fillRect(0, H - 18, W, 18, QColor(_th.BG_DEEP))
    p.setPen(QColor(FG_DIM))
    p.setFont(_ui_font(10))
    p.drawText(QRect(4, H - 16, W - 8, 14),
          Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
          f"已选 {sel_count} / 共 {total} 个设备 | 点击选中 · 拖拽框选")
    p.end()

class FrameThumb(QWidget):
  clicked = pyqtSignal(int)
  double_clicked = pyqtSignal(int)
  THUMB_W = 88
  THUMB_H = 72

  def __init__(self, idx: int, frame: PatternFrame, parent=None):
    super().__init__(parent)
    self._idx = idx
    self._frame = frame
    self._selected = False
    self._hover = False
    self._weights: List[float] = []
    self._fixtures: List[Dict] = []
    self.setFixedSize(self.THUMB_W, self.THUMB_H + 20)
    self.setCursor(Qt.CursorShape.PointingHandCursor)
    self.setMouseTracking(True)

  def set_selected(self, v: bool):
    self._selected = v
    self.update()

  def enterEvent(self, e):
    self._hover = True
    self.update()
    super().enterEvent(e)

  def leaveEvent(self, e):
    self._hover = False
    self.update()
    super().leaveEvent(e)

  def set_preview(self, fixtures: List[Dict], weights: List[float]):
    self._fixtures = fixtures
    self._weights = weights
    self.update()

  def mousePressEvent(self, e):
    self.clicked.emit(self._idx)

  def mouseDoubleClickEvent(self, e):
    self.double_clicked.emit(self._idx)

  def paintEvent(self, event):
    p = QPainter(self)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    W, H = self.THUMB_W, self.THUMB_H

    # 扁平卡片：BG_CARD 底 + 1px 边 + 4px 圆角；选中 select_blue 描边
    bg = QColor(_th.BG_PANEL_SUB if self._selected else BG_CARD)
    if self._hover and not self._selected:
      bg = QColor(_th.BG_PANEL)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.5, 0.5, W - 1, H - 1), 4, 4)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(bg))
    p.drawPath(path)
    border_c = QColor(_th.SELECT_BLUE if self._selected else _th.BORDER_NORMAL)
    p.setPen(QPen(border_c, 2 if self._selected else 1))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(1, 1, W - 3, H - 3)

    if self._fixtures and self._weights:
      xs = [f.get("pos_x", f.get("x", 0.0)) for f in self._fixtures]
      ys = [f.get("pos_y", f.get("y", 0.0)) for f in self._fixtures]
      min_x, max_x = min(xs), max(xs)
      min_y, max_y = min(ys), max(ys)
      rx = max(0.001, max_x - min_x)
      ry = max(0.001, max_y - min_y)
      margin = 6
      pw = W - 2 * margin
      ph = H - 2 * margin - 2
      for i, f in enumerate(self._fixtures):
        nx = (f.get("pos_x", f.get("x", 0.0)) - min_x) / rx
        ny = (f.get("pos_y", f.get("y", 0.0)) - min_y) / ry
        px = margin + nx * pw
        py = margin + ny * ph
        w = self._weights[i] if i < len(self._weights) else 0.0
        color = QColor(self._frame.color)
        color.setAlphaF(max(0.1, w))
        r = 1.5 + w * 3
        p.setBrush(QBrush(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(int(px - r), int(py - r), int(r * 2), int(r * 2))
    else:
      p.setPen(QColor(FG_DIM))
      p.setFont(_ui_font(10))
      p.drawText(QRect(2, 8, W - 4, H - 16),
            Qt.AlignmentFlag.AlignCenter, self._frame.pattern)

    # 底部标题带：选中 select_blue 上白字，普通 BG_DEEP 带正文色
    if self._selected:
      band = QColor(_th.SELECT_BLUE)
    elif self._hover:
      band = QColor(_th.BG_PANEL)
    else:
      band = QColor(_th.BG_DEEP)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(band))
    p.drawRect(0, H, W, 20)
    p.setPen(QColor(_th.TEXT_SELECTED_FG if self._selected
            else FG_LIGHT if self._hover else FG_DIM))
    p.setFont(_ui_font(10, bold=self._selected))
    name = self._frame.name
    if len(name) > 9:
      name = name[:8] + "…"
    p.drawText(QRect(2, H + 2, W - 4, 16),
          Qt.AlignmentFlag.AlignCenter, f"{self._idx + 1}. {name}")
    p.end()


# ══════════════════════════════════════════════════════════════════════════════
# 图案帧编辑对话框（按设备组分页 on/off 选择版）
# ══════════════════════════════════════════════════════════════════════════════
class FixtureGroupLayoutPage(QWidget):
  """单个设备组的布局页面：按 TopView 坐标排列设备方块，点击切换 on/off。"""
  selection_changed = pyqtSignal()

  CELL_W = 64  # 设备行宽度
  CELL_H = 24  # 设备行高（紧凑列表行高）
  MARGIN = 10  # 边距
  COLS  = 8   # 每行最多列数（超出自动换行）

  def __init__(self, group_name: str, fixtures: list, selected_ids: set, parent=None):
    """
    fixtures: list of dict {id, name, pos_x, pos_y, universe, start_channel}
    selected_ids: 当前选中的 fixture id 集合（可变，直接引用）
    """
    super().__init__(parent)
    self._fixtures = fixtures or []
    self._selected_ids = selected_ids # 直接引用外部集合
    self._group_name = group_name
    self._hover_idx = -1
    self.setMouseTracking(True)
    self._calc_layout()
    self.setMinimumHeight(self._total_h + 20)

  def _calc_layout(self):
    """按 TopView 坐标排序后，计算每个设备的方块位置。"""
    # 按 pos_y 主排序，pos_x 次排序（从上到下，从左到右）
    sorted_fx = sorted(self._fixtures,
              key=lambda f: (round(f.get("pos_y", 0.0) * 10),
                     f.get("pos_x", 0.0)))
    self._sorted_fixtures = sorted_fx
    # 计算行列布局
    n = len(sorted_fx)
    cols = min(self.COLS, max(1, n))
    rows = (n + cols - 1) // cols
    self._cols = cols
    self._rows_count = rows
    self._total_h = self.MARGIN * 2 + rows * (self.CELL_H + 4)
    total_w = self.MARGIN * 2 + cols * (self.CELL_W + 4)
    self.setMinimumWidth(total_w)

  def _cell_rect(self, idx: int):
    col = idx % self._cols
    row = idx // self._cols
    x = self.MARGIN + col * (self.CELL_W + 4)
    y = self.MARGIN + row * (self.CELL_H + 4)
    return QRect(x, y, self.CELL_W, self.CELL_H)

  def _idx_at(self, pos: QPoint) -> int:
    for i in range(len(self._sorted_fixtures)):
      if self._cell_rect(i).contains(pos):
        return i
    return -1

  def mousePressEvent(self, e):
    if e.button() == Qt.MouseButton.LeftButton:
      idx = self._idx_at(e.pos())
      if idx >= 0:
        fid = self._sorted_fixtures[idx].get("id", "")
        if fid in self._selected_ids:
          self._selected_ids.discard(fid)
        else:
          self._selected_ids.add(fid)
        self.selection_changed.emit()
        self.update()

  def mouseMoveEvent(self, e):
    idx = self._idx_at(e.pos())
    if idx != self._hover_idx:
      self._hover_idx = idx
      self.update()

  def leaveEvent(self, e):
    self._hover_idx = -1
    self.update()

  def paintEvent(self, event):
    p = QPainter(self)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    W, H = self.width(), self.height()
    p.fillRect(0, 0, W, H, QColor(BG_DARK))

    for i, f in enumerate(self._sorted_fixtures):
      fid = f.get("id", "")
      rect = self._cell_rect(i)
      selected = fid in self._selected_ids
      hovered = i == self._hover_idx

      if selected:
        # 选中行：select_blue 填充 + 白字 + 左竖条
        bg = QColor(_th.SELECT_BLUE)
        border = QColor(_th.SELECT_BLUE)
        text_c = QColor(_th.TEXT_SELECTED_FG)
        addr_c = QColor(255, 255, 255, 190)
        indicator = QColor(_th.BRIGHT_SUCCESS)
      elif hovered:
        bg = QColor(_th.BG_PANEL_SUB)
        border = QColor(_th.BG_BUTTON)
        text_c = QColor(FG_LIGHT)
        addr_c = QColor(FG_DIM)
        indicator = QColor(FG_DIM)
      else:
        bg = QColor(BG_CARD)
        border = QColor(_th.BORDER_NORMAL)
        text_c = QColor(FG_DIM)
        addr_c = QColor(_th.TEXT_DISABLED)
        indicator = QColor(_th.BG_BUTTON)

      p.setPen(Qt.PenStyle.NoPen)
      p.setBrush(QBrush(bg))
      p.drawRoundedRect(QRectF(rect.x() + 0.5, rect.y() + 0.5,
                   rect.width() - 1, rect.height() - 1), 4, 4)
      p.setBrush(Qt.BrushStyle.NoBrush)
      p.setPen(QPen(border, 1))
      p.drawRoundedRect(rect.adjusted(0, 0, -1, -1), 4, 4)

      # 选中左竖条（2px 焦点蓝）
      if selected:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(_th.FOCUS_BLUE)))
        p.drawRect(rect.x() + 2, rect.y() + 4, 2, rect.height() - 8)

      # on/off 指示灯（左上小圆点，ON 保 BRIGHT_SUCCESS hue）
      dot_r = 3
      dot_x = rect.x() + 7
      dot_y = rect.y() + rect.height() // 2
      p.setBrush(QBrush(indicator))
      p.setPen(Qt.PenStyle.NoPen)
      p.drawEllipse(QPoint(dot_x, dot_y), dot_r, dot_r)

      # 设备名（左侧，10-11px）
      name = f.get("name", f"#{i+1}")
      if len(name) > 5:
        name = name[:4] + "…"
      name_rect = QRect(rect.x() + 13, rect.y(), rect.width() - 48,
               rect.height())
      p.setFont(_ui_font(11, bold=selected))
      p.setPen(text_c)
      p.drawText(name_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            name)

      # 通道地址（右侧等宽 10px）
      uni = f.get("universe", 1)
      ch = f.get("start_channel", 1)
      addr_text = f"U{uni}·{ch}"
      addr_rect = QRect(rect.right() - 33, rect.y(), 30, rect.height())
      p.setFont(_mono_font(10))
      p.setPen(addr_c)
      p.drawText(addr_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            addr_text)

    p.end()


class FrameEditWithLayoutDialog(QDialog):
  """编辑图案帧：按设备组分页的 on/off 选择界面。
  - 每个设备组一个 Tab 页
  - 页面上显示 TopView 布局（按坐标排列的设备方块）
  - 点击方块切换 on/off（选中=有输出，未选中=不输出）
  - 具体输出值在矩阵主界面调试
  """
  def __init__(self, frame: PatternFrame, groups: list, topview_positions: list,
         parent=None):
    """
    frame: 当前 PatternFrame
    groups: list of FixtureGroup（含 id/name/fixture_ids）
    topview_positions: list of dict {id, name, pos_x, pos_y, universe, start_channel}
    """
    super().__init__(parent)
    self._frame = copy.deepcopy(frame)
    self._groups = groups or []
    self._topview_positions = topview_positions or []
    # 构建 fixture_id -> fixture_dict 映射
    self._fx_map = {f.get("id", ""): f for f in topview_positions}
    # 初始化选中集合（从 frame 的 _selected_fixture_ids 字段读取）
    self._selected_ids: set = set(
      getattr(frame, '_selected_fixture_ids', None) or [])
    self.setWindowTitle("编辑图案帧 — 设备选择")
    self.setMinimumSize(520, 360)
    # 对话框/QLabel/Tab 基础外观交由全局主题（原重复 QSS 已删除）
    self._build_ui()
    apply_dialog_chrome(self)

  def _build_ui(self):
    lay = QVBoxLayout(self)
    lay.setContentsMargins(12, 12, 12, 10)
    lay.setSpacing(8)

    # 顶部说明
    info_lbl = QLabel(
      "点击设备方块切换 ON/OFF（绿色=有输出，暗色=不输出）。"
      "具体输出值在矩阵主界面调试。")
    info_lbl.setStyleSheet(f"color:{FG_DIM};font-size:11px;")
    info_lbl.setWordWrap(True)
    lay.addWidget(info_lbl)

    # Tab 页：每个设备组一页
    self._tab = QTabWidget()
    self._pages: list = [] # FixtureGroupLayoutPage 页面列表

    # "全部设备"页
    all_fixtures = list(self._topview_positions)
    if all_fixtures:
      all_page = self._make_page("全部设备", all_fixtures)
      self._tab.addTab(self._wrap_scroll(all_page), "全部设备")
      self._pages.append(all_page)

    # 各设备组页
    for grp in self._groups:
      grp_ids = set(getattr(grp, 'fixture_ids', []))
      grp_fixtures = [f for f in self._topview_positions
              if f.get("id", "") in grp_ids]
      if not grp_fixtures:
        continue
      grp_name = getattr(grp, 'name', '未命名组')
      page = self._make_page(grp_name, grp_fixtures)
      self._tab.addTab(self._wrap_scroll(page), grp_name)
      self._pages.append(page)

    if not self._pages:
      empty_lbl = QLabel("暂无设备。请先在 TopView 中摆放设备。")
      empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
      empty_lbl.setStyleSheet(f"color:{FG_DIM};")
      self._tab.addTab(empty_lbl, "无设备")

    lay.addWidget(self._tab, 1)

    # 底部统计 + 全选/清空按钮
    bottom = QHBoxLayout()
    self._sel_lbl = QLabel(f"已选: {len(self._selected_ids)} 个设备")
    self._sel_lbl.setStyleSheet(f"color:{ACCENT};font-size:11px;")
    bottom.addWidget(self._sel_lbl)
    bottom.addStretch()
    for label, fn in [("全选", self._select_all),
              ("清空", self._select_none),
              ("反选", self._select_invert)]:
      btn = QPushButton(label)
      btn.setFixedHeight(24)
      btn.setFixedWidth(52)
      btn.clicked.connect(fn)
      bottom.addWidget(btn)
    lay.addLayout(bottom)

    # 确定/取消
    btns = QDialogButtonBox(
      QDialogButtonBox.StandardButton.Ok |
      QDialogButtonBox.StandardButton.Cancel)
    ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
    cancel_btn = btns.button(QDialogButtonBox.StandardButton.Cancel)
    if ok_btn is not None:
      ok_btn.setObjectName("primary")
    if cancel_btn is not None:
      cancel_btn.setProperty("role", "cancel")
    btns.accepted.connect(self.accept)
    btns.rejected.connect(self.reject)
    lay.addWidget(btns)

  def _make_page(self, group_name: str, fixtures: list) -> FixtureGroupLayoutPage:
    page = FixtureGroupLayoutPage(group_name, fixtures, self._selected_ids)
    page.selection_changed.connect(self._on_selection_changed)
    return page

  def _wrap_scroll(self, widget: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidget(widget)
    scroll.setWidgetResizable(True)
    scope_qss(scroll, f"background:{BG_DARK};border:none;")
    return scroll

  def _on_selection_changed(self):
    self._sel_lbl.setText(f"已选: {len(self._selected_ids)} 个设备")
    # 刷新所有页面（因为共享同一个 _selected_ids 集合）
    for page in self._pages:
      page.update()

  def _select_all(self):
    for f in self._topview_positions:
      self._selected_ids.add(f.get("id", ""))
    self._on_selection_changed()

  def _select_none(self):
    self._selected_ids.clear()
    self._on_selection_changed()

  def _select_invert(self):
    all_ids = {f.get("id", "") for f in self._topview_positions}
    self._selected_ids ^= all_ids
    self._on_selection_changed()

  def get_selected_ids(self) -> set:
    return set(self._selected_ids)


# ══════════════════════════════════════════════════════════════════════════════
# 图案帧编辑对话框
# ══════════════════════════════════════════════════════════════════════════════
class FrameEditDialog(QDialog):
  def __init__(self, frame: PatternFrame, parent=None):
    super().__init__(parent)
    self._frame = copy.deepcopy(frame)
    self.setWindowTitle("编辑图案帧")
    self.setMinimumWidth(380)
    # 对话框/输入类控件基础外观交由全局主题（原重复 QSS 已删除）
    self._build_ui()
    apply_dialog_chrome(self)

  def _numslider(self, lo: float, hi: float, val: float,
          dec: int, suffix: str = "") -> NumSlider:
    """极简数值滑杆，钳制初值到范围内。"""
    return NumSlider(lo, hi, max(lo, min(hi, float(val))),
             decimals=dec, suffix=suffix)

  def _build_ui(self):
    lay = QVBoxLayout(self)
    lay.setContentsMargins(12, 12, 12, 10)
    lay.setSpacing(8)

    def prow(section: PanelSection, label: str, widget: QWidget):
      section.add_widget(prop_row(label, widget))

    frame = self._frame

    # ── 基础 ────────────────────────────────────────────────────────────
    sec_basic = PanelSection("基础")
    self._name_edit = QLineEdit(frame.name)
    prow(sec_basic, "帧名称", self._name_edit)

    self._cat_combo = QComboBox()
    self._cat_combo.addItems(list(PATTERN_CATEGORIES.keys()))
    prow(sec_basic, "分类", self._cat_combo)

    self._pattern_combo = QComboBox()
    prow(sec_basic, "图案", self._pattern_combo)

    for cat, patterns in PATTERN_CATEGORIES.items():
      if frame.pattern in patterns:
        self._cat_combo.setCurrentText(cat)
        break
    self._on_cat_changed(self._cat_combo.currentText())
    # 按英文键选中（userData 匹配），不能用 setCurrentText：Chroma 项显示的是中文
    _select_pattern_in_combo(self._pattern_combo, frame.pattern)
    self._cat_combo.currentTextChanged.connect(self._on_cat_changed)

    self._density_spin = self._numslider(0.0, 1.0, frame.density, 2)
    prow(sec_basic, "开启密度", self._density_spin)

    self._size_spin = self._numslider(0.01, 20.0, frame.size, 3)
    prow(sec_basic, "尺寸", self._size_spin)
    lay.addWidget(sec_basic)

    # ── 运动 ────────────────────────────────────────────────────────────
    sec_motion = PanelSection("运动")
    # 相位起始以 % 显示与输入（0%=相位 0、100%=整周期），存储值仍为原相位数：
    # UI↔数据以 ×100 / ÷100 换算，写入 PatternFrame 的字段语义不变。
    self._phase_spin = self._numslider(
      -100.0, 100.0, float(frame.phase_start) * 100.0, 1, "%")
    self._phase_spin.setToolTip("相位起始（周期的百分比）；显示值 = 存储相位 × 100%。")
    prow(sec_motion, "相位起始", self._phase_spin)

    self._speed_spin = self._numslider(
      0.0, 4.0, frame.phase_speed, 3, "×")
    prow(sec_motion, "相位速度", self._speed_spin)

    self._dur_spin = self._numslider(0.0, 60.0, frame.duration, 1, "s")
    self._dur_spin.setToolTip("帧持续时间；0=无限停留。")
    prow(sec_motion, "持续", self._dur_spin)

    self._trans_spin = self._numslider(
      0.0, 60.0, frame.transition, 2, "s")
    prow(sec_motion, "过渡", self._trans_spin)

    self._joy_target_combo = QComboBox()
    self._joy_target_combo.addItems(["phase", "density", "cx", "cy", "rotation", "size"])
    self._joy_target_combo.setCurrentText(frame.joy_target)
    prow(sec_motion, "摇杆调制", self._joy_target_combo)
    lay.addWidget(sec_motion)

    # ── 颜色与通道 ──────────────────────────────────────────────────────
    sec_color = PanelSection("颜色与通道")
    self._ch_combo = QComboBox()
    self._ch_combo.addItems(["dimmer", "R", "G", "B", "valve", "pump",
                 "motor_x", "motor_y", "motor_z", "strobe"])
    self._ch_combo.setCurrentText(frame.ch_type)
    prow(sec_color, "通道", self._ch_combo)
    lay.addWidget(sec_color)

    # ── 空间分层 ────────────────────────────────────────────────────────
    sec_space = PanelSection("空间分层")
    self._layers_spin = self._numslider(
      1, 64, int(getattr(frame, "layers", 6)), 0)
    prow(sec_space, "层数", self._layers_spin)

    self._hollow_spin = self._numslider(
      0.0, 0.99, float(getattr(frame, "hollow", 0.0)), 3)
    prow(sec_space, "空心比例", self._hollow_spin)

    self._edge_spin = self._numslider(
      0.05, 12.0, float(getattr(frame, "edge_width", 1.0)), 3)
    prow(sec_space, "边线宽度", self._edge_spin)

    self._branch_spin = self._numslider(
      1, 96, int(getattr(frame, "branch_count", 8)), 0)
    prow(sec_space, "分叉/轴数", self._branch_spin)

    self._expand_combo = QComboBox()
    self._expand_combo.addItems(["全向", "水平", "垂直", "对角"])
    self._expand_combo.setCurrentIndex(int(getattr(frame, "expand_dir", 0)))
    prow(sec_space, "外展方向", self._expand_combo)

    self._mirror_combo = QComboBox()
    self._mirror_combo.addItems(["竖轴", "横轴", "对角", "多轴"])
    self._mirror_combo.setCurrentIndex(int(getattr(frame, "mirror_axis", 3)))
    prow(sec_space, "镜像轴", self._mirror_combo)

    self._mix_spin = self._numslider(
      0.0, 1.0, float(getattr(frame, "circle_diamond_mix", 0.5)), 2)
    prow(sec_space, "圆菱混合", self._mix_spin)

    self._spec_spin = self._numslider(
      0.0, 8.0, float(getattr(frame, "spectrum_zone_strength", 0.7)), 2)
    prow(sec_space, "频谱强度", self._spec_spin)

    self._step_spin = self._numslider(
      0.0, 16.0, float(getattr(frame, "step_speed", 1.0)), 3)
    prow(sec_space, "步进速度", self._step_spin)

    self._group_spin = self._numslider(
      1, 50, int(frame.group_size), 0)
    prow(sec_space, "分组大小", self._group_spin)
    lay.addWidget(sec_space)

    # ── 音频驱动 ────────────────────────────────────────────────────────
    sec_audio = PanelSection("音频驱动")
    self._audio_mode_check = QCheckBox("启用音频驱动（关闭时按相位/速度自走）")
    self._audio_mode_check.setChecked(bool(getattr(frame, "audio_mode", False)))
    sec_audio.add_widget(prop_row("双模式", self._audio_mode_check))
    lay.addWidget(sec_audio)

    btns = QDialogButtonBox(
      QDialogButtonBox.StandardButton.Ok |
      QDialogButtonBox.StandardButton.Cancel)
    ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
    cancel_btn = btns.button(QDialogButtonBox.StandardButton.Cancel)
    if ok_btn is not None:
      ok_btn.setObjectName("primary")
    if cancel_btn is not None:
      cancel_btn.setProperty("role", "cancel")
    btns.accepted.connect(self.accept)
    btns.rejected.connect(self.reject)
    lay.addWidget(btns)

  def _on_cat_changed(self, cat: str):
    self._pattern_combo.blockSignals(True)
    self._pattern_combo.clear()
    # 用「中文显示名 + 英文键(userData)」填充，Chroma 显示中文、其余原样
    _populate_pattern_combo(self._pattern_combo, PATTERN_CATEGORIES.get(cat, []))
    self._pattern_combo.blockSignals(False)

  def get_frame(self) -> PatternFrame:
    f = self._frame
    f.name = self._name_edit.text() or "图案帧"
    # currentData() 取回原英文键（Chroma 项显示中文，但 userData 仍是英文键）
    f.pattern = self._pattern_combo.currentData() or self._pattern_combo.currentText()
    f.ch_type = self._ch_combo.currentText()
    f.joy_target = self._joy_target_combo.currentText()
    f.density = self._density_spin.float_value()
    f.size = self._size_spin.float_value()
    # 相位起始 UI 为 % 显示（存储相位 × 100），写回时 ÷100 还原相位语义
    f.phase_start = self._phase_spin.float_value() / 100.0
    f.phase_speed = self._speed_spin.float_value()
    f.duration = self._dur_spin.float_value()
    f.transition = self._trans_spin.float_value()
    f.group_size = int(round(self._group_spin.float_value()))
    f.audio_mode = self._audio_mode_check.isChecked()
    f.layers = int(round(self._layers_spin.float_value()))
    f.hollow = self._hollow_spin.float_value()
    f.edge_width = self._edge_spin.float_value()
    f.branch_count = int(round(self._branch_spin.float_value()))
    f.expand_dir = self._expand_combo.currentIndex()
    f.mirror_axis = self._mirror_combo.currentIndex()
    f.circle_diamond_mix = self._mix_spin.float_value()
    f.spectrum_zone_strength = self._spec_spin.float_value()
    f.step_speed = self._step_spin.float_value()
    return f


# ══════════════════════════════════════════════════════════════════════════════
# 主面板
# ══════════════════════════════════════════════════════════════════════════════
# ==============================================================================
# 2026-09-13 删除：旧 MatrixPatternPanel 类（约 1885 行）
#
# 删除理由（用户 2026-09-13 要求整治散落的老旧效果存储接口）：
#  - 该类从未被实例化——主窗实例化的是 matrix_slideshow.MatrixSlideshowPanel；
#  - 它自带一套（保存为新幻灯片 / 内置序列）存储 UI，与现役面板重复，
#   是两套效果存储的典型冗余；
#  - 全仓仅 1 个测试用 __new__ 借用它的 _apply_control_group_weights，
#   而该方法在现役面板没有等价实现（现役走 effect_engine 的
#   follow -> global/per_shape 坐标系解析）；该测试已随之退役并改为
#   断言现役语义（见 tests/test_effect_control_semantics.py）。
#
# 保留：本模块的图案常量与生成器（PatternGenerator / PATTERN_CATEGORIES /
# BUILTIN_SEQUENCES / _CH_TYPE_COLORS）——effect_presets_v23 与下列模块仍在使用。
# ==============================================================================
#

# ══════════════════════════════════════════════════════════════════════════════
# 预览可视化控件（信号监视器风格，支持彩色 Chroma 模式）
# ══════════════════════════════════════════════════════════════════════════════

# ch_type 到基础颜色映射（用于非 Chroma 模式下的单色转换）
_CH_TYPE_COLORS: Dict[str, Tuple[int, int, int]] = {
  "dimmer":  (200, 200, 200),
  "R":    (255, 60, 60),
  "G":    ( 60, 220, 80),
  "B":    ( 60, 140, 255),
  "W":    (255, 255, 220),
  "A":    (255, 200, 100),  # 琥珀色
  "UV":    (160, 60, 255),
  "valve":  ( 80, 200, 255),
  "pump":   ( 60, 160, 255),
  "strobe":  (255, 255, 100),
  "motor_x": (100, 255, 200),
  "motor_y": (200, 100, 255),
  "motor_z": (100, 200, 100),
}


# ── D2-B1/B2：辉光画刷缓存（主监视器 11382 台×每帧；修前每台每帧新建
#  QRadialGradient+QBrush+3×QColor）。ObjectBoundingMode：渐变随绘制目标
#  包围盒取中心/半径，与像素位置无关 → 同 (色,α) 键的画刷可全局复用。
_GLOW_BRUSH: dict = {}


def _glow_brush(rr: int, gg: int, bb: int, a0: int, a1=None):
  """辐射辉光画刷（键=(色,内α,中α)；无则建，命中零构造）。"""
  key = (rr, gg, bb, a0, a1)
  b = _GLOW_BRUSH.get(key)
  if b is None:
    g = QRadialGradient(0.5, 0.5, 0.5)
    g.setCoordinateMode(QGradient.CoordinateMode.ObjectBoundingMode)
    g.setColorAt(0.0, QColor(rr, gg, bb, a0))
    if a1 is not None:
      g.setColorAt(0.5, QColor(rr, gg, bb, a1))
    g.setColorAt(1.0, QColor(0, 0, 0, 0))
    b = QBrush(g)
    if len(_GLOW_BRUSH) > 4096:
      _GLOW_BRUSH.clear()
    _GLOW_BRUSH[key] = b
  return b


def _hue_to_rgb(hue: float) -> Tuple[int, int, int]:
  """HSV转 RGB（S=1, V=1），输入 hue [0,1)，输出 (r,g,b) [0,255]"""
  h = (hue % 1.0) * 6.0
  i = int(h)
  f = h - i
  q = 1.0 - f
  p = 0.0
  if i == 0:  r, g, b = 1.0, f,  p
  elif i == 1: r, g, b = q,  1.0, p
  elif i == 2: r, g, b = p,  1.0, f
  elif i == 3: r, g, b = p,  q,  1.0
  elif i == 4: r, g, b = f,  p,  1.0
  else:    r, g, b = 1.0, p,  q
  return int(r * 255), int(g * 255), int(b * 255)


class PatternVisualizer(QWidget):
  """信号监视器风格预览控件
  • 普通图案：根据 ch_type 显示对应颜色，亮度映射到 weight
  • Chroma 图案：weight 被解析为色相，展示完整 HSV 彩虹色
  • 较大较亮的设备点展示辐射辉光效果
  • 底部显示信号监视器风格的电平表和标注
  """

  def __init__(self, parent=None):
    super().__init__(parent)
    self._fixtures: List[Dict] = []
    self._weights: List[float] = []
    self._ch_type: str = "dimmer"   # 当前帧的通道类型
    self._pattern_name: str = ""   # 当前图案名称（用于判断是否 Chroma）
    self._is_chroma: bool = False   # 是否处于 Chroma 彩色模式
    self._peak_history: List[float] = [] # 用于底部电平表
    self._peak_max: float = 0.0
    # ★ 性能：归一化几何缓存（set_fixtures 预计算，paint 不再每帧 min/max 全扫
    #  11382 台 ≈ 5-15ms）
    self._norm_geo = None
    # ★ 性能：100ms 合并重绘（25fps 权重流 → ≤10fps 重绘，视觉无感）
    self._update_timer = QTimer(self)
    self._update_timer.setSingleShot(True)
    self._update_timer.setInterval(100)
    self._update_timer.timeout.connect(self.update)
    self.setMinimumSize(200, 140)
    self.setStyleSheet(
      f"background:{BG_DARK};border:1px solid {_th.BORDER_NORMAL};border-radius:4px;")

  def showEvent(self, ev): # noqa: N802
    super().showEvent(ev)
    if not self._update_timer.isActive():
      self._update_timer.start()

  def hideEvent(self, ev): # noqa: N802
    self._update_timer.stop()
    super().hideEvent(ev)

  def set_fixtures(self, fixtures: List[Dict]):
    self._fixtures = fixtures
    # ★ 性能：预计算归一化几何（结果与原 paint 内公式逐位一致）
    try:
      xs = [f.get("pos_x", f.get("x", 0.0)) for f in fixtures]
      ys = [f.get("pos_y", f.get("y", 0.0)) for f in fixtures]
      min_x, max_x = min(xs), max(xs)
      min_y, max_y = min(ys), max(ys)
      rx = max(0.001, max_x - min_x)
      ry = max(0.001, max_y - min_y)
      self._norm_geo = (
        min_x, min_y, rx, ry,
        [(x - min_x) / rx for x in xs],
        [(y - min_y) / ry for y in ys],
      )
    except Exception:
      self._norm_geo = None
    self.update()

  def set_weights(self, weights: List[float]):
    self._weights = weights
    # 更新电平表历史
    if weights:
      avg = sum(weights) / len(weights)
      self._peak_history.append(avg)
      if len(self._peak_history) > 60:
        self._peak_history.pop(0)
      self._peak_max = max(self._peak_max * 0.98, avg) # 慢衰减
    # ★ 性能：100ms 合并重绘
    self._update_timer.start()

  def set_frame_info(self, ch_type: str, pattern_name: str):
    """MatrixPatternPanel 在 _render_current_frame 中调用，传递当前帧元数据"""
    self._ch_type = ch_type
    self._pattern_name = pattern_name
    self._is_chroma = pattern_name.startswith("Chroma_")
    self._update_timer.start()

  def paintEvent(self, event):
    p = QPainter(self)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    W, H = self.width(), self.height()

    # ── 背景渐变 ──
    p.fillRect(self.rect(), gradient(_th.CANVAS_OUTPUT_BG, _th.OUTPUT_BG_DEEP))

    # ── 网格线（信号监视器风格） ──
    grid_pen = QPen(tone(_th.CANVAS_OUTPUT_IDLE_BORDER, 40), 1)
    p.setPen(grid_pen)
    METER_H = 28  # 底部电平表高度
    plot_h = H - METER_H - 4
    for gx in range(0, W, max(1, W // 8)):
      p.drawLine(gx, 0, gx, plot_h)
    for gy in range(0, plot_h, max(1, plot_h // 4)):
      p.drawLine(0, gy, W, gy)

    if not self._fixtures:
      p.setPen(tone(_th.CANVAS_OUTPUT_TEXT))
      p.setFont(_ui_font(11))
      p.drawText(QRect(0, 0, W, plot_h), Qt.AlignmentFlag.AlignCenter,
            "无设备\n请在 TopView 中摆放设备")
      p.end()
      return

    # ★ 性能：归一化几何复用 set_fixtures 预计算结果（11382 台每帧全扫
    #  min/max + 列表重建 ≈ 5-15ms → 0）
    geo = self._norm_geo
    if geo is None:
      xs = [f.get("pos_x", f.get("x", 0.0)) for f in self._fixtures]
      ys = [f.get("pos_y", f.get("y", 0.0)) for f in self._fixtures]
      min_x, max_x = min(xs), max(xs)
      min_y, max_y = min(ys), max(ys)
      rx = max(0.001, max_x - min_x)
      ry = max(0.001, max_y - min_y)
      nxs = [(x - min_x) / rx for x in xs]
      nys = [(y - min_y) / ry for y in ys]
    else:
      min_x, min_y, rx, ry, nxs, nys = geo
    margin = 14
    pw = W - 2 * margin
    ph = plot_h - 2 * margin

    # ── 基础颜色（非 Chroma 模式） ──
    base_rgb = _CH_TYPE_COLORS.get(self._ch_type, (180, 180, 255))

    for i, f in enumerate(self._fixtures):
      nx = nxs[i]
      ny = nys[i]
      px = margin + nx * pw
      py = margin + ny * ph
      w = self._weights[i] if i < len(self._weights) else 0.0

      if self._is_chroma:
        # Chroma 模式：weight 即色相
        hue = w # [0,1)
        brightness = min(1.0, w * 1.5 + 0.1) if w > 0.01 else 0.0
        if brightness < 0.05:
          # 暗淡设备点
          dot_color = QColor(15, 15, 35)
          p.setBrush(QBrush(dot_color))
          p.setPen(QPen(QColor(25, 25, 50), 1))
          r = 2.0
          p.drawEllipse(int(px - r), int(py - r), int(r * 2), int(r * 2))
          continue
        rr, gg, bb = _hue_to_rgb(round(hue * 128.0) / 128.0)  # D2-B1 色相量化
        glow_r = int(6 + brightness * 18)
        p.setBrush(_glow_brush(rr, gg, bb,
                    int(brightness * 160), int(brightness * 60)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(int(px - glow_r), int(py - glow_r),
               glow_r * 2, glow_r * 2)
        # 点核心
        r = 2.5 + brightness * 5
        core_color = QColor(
          min(255, rr + int(brightness * 80)),
          min(255, gg + int(brightness * 80)),
          min(255, bb + int(brightness * 80)))
        p.setBrush(QBrush(core_color))
        p.setPen(QPen(QColor(rr // 2, gg // 2, bb // 2), 1))
        p.drawEllipse(int(px - r), int(py - r), int(r * 2), int(r * 2))
      else:
        # 普通模式：单色 + 亮度映射
        if w > 0.05:
          gr = int(4 + w * 16)
          p.setBrush(_glow_brush(base_rgb[0], base_rgb[1], base_rgb[2],
                      int(w * 130)))  # D2-B2
          p.setPen(Qt.PenStyle.NoPen)
          p.drawEllipse(int(px - gr), int(py - gr), gr * 2, gr * 2)

        r = 2.5 + w * 5
        bright_factor = w
        cr = min(255, int(base_rgb[0] * bright_factor + 15))
        cg = min(255, int(base_rgb[1] * bright_factor + 15))
        cb = min(255, int(base_rgb[2] * bright_factor + 15))
        if w < 0.05:
          cr, cg, cb = 18, 18, 38
        color = QColor(cr, cg, cb)
        p.setBrush(QBrush(color))
        p.setPen(QPen(QColor(cr // 2, cg // 2, cb // 2 + 20), 1))
        p.drawEllipse(int(px - r), int(py - r), int(r * 2), int(r * 2))

    # ── 底部电平表（信号监视器风格） ──
    meter_y = plot_h + 4
    p.fillRect(0, meter_y, W, METER_H, QColor(8, 8, 20))
    p.setPen(QPen(QColor(40, 40, 80), 1))
    p.drawLine(0, meter_y, W, meter_y)

    if self._peak_history:
      bar_w = max(1, W // max(1, len(self._peak_history)))
      for j, val in enumerate(self._peak_history):
        bx = j * bar_w
        bh = int(val * (METER_H - 4))
        if bh > 0:
          if self._is_chroma:
            hue_bar = (j / max(1, len(self._peak_history)) + val * 0.3) % 1.0
            rr, gg, bb = _hue_to_rgb(hue_bar)
            bar_color = QColor(rr, gg, bb, 180)
          else:
            br, bg, bbb = base_rgb
            bar_color = QColor(br, bg, bbb, int(100 + val * 120))
          p.fillRect(bx, meter_y + METER_H - bh - 2, bar_w - 1, bh, bar_color)

    # ── 通道类型和图案名标注（10-11px，等宽通道码） ──
    p.setFont(_ui_font(10))
    label_text = self._pattern_name if self._pattern_name else "--"
    if self._is_chroma:
      p.setPen(QColor(255, 180, 80))
      label_text = f"★ {label_text}"
    else:
      br, bg, bbb = base_rgb
      p.setPen(QColor(br, bg, bbb, 180))
    p.drawText(QRect(4, meter_y + 2, W - 8, 14),
          Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
          label_text)
    ch_label = self._ch_type.upper()
    p.setFont(_mono_font(10))
    p.drawText(QRect(4, meter_y + 2, W - 8, 14),
          Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
          ch_label)

    p.end()
