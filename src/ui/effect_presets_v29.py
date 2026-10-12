"""
effect_presets_v29.py — 空间编组触发矩阵效果 v29
=================================================
核心设计：在矩阵空间中随机触发"形状组"，每个组是一段序列动画帧。
  - 形状单元：三角形、菱形、方形、星形、十字、L形、螺旋臂、六边形等
  - 序列动画：每个组有完整的出现→扩散→消退动画过程（多帧）
  - 音频联动：rms→触发强度/亮度，beat_phase→触发时机，bpm→跑动速度
  - max_groups 参数：手动控制同时存在的最大组数

约60个效果变式，灯光和泵均适用。
"""
from __future__ import annotations
import math
import random
from typing import TYPE_CHECKING, List, Tuple, Dict, Any
if TYPE_CHECKING:
    from .effect_engine import EffectEngine, EffectPreset, EffectFrame, ParamSpec


# ═══════════════════════════════════════════════════════════════════════
#  全局状态管理（每个效果实例独立）
# ═══════════════════════════════════════════════════════════════════════

_STATES: Dict[str, Any] = {}

def _state_key(effect_id: str, cols: int, rows: int, params: Any) -> str:
    """状态命名空间键：`<id>_<cols>_<rows>`（+ `#tag` 子集尾缀）。"""
    key = f"{effect_id}_{cols}_{rows}"
    try:
        tag = str((params or {}).get("_subset_state_tag", "") or "")
    except Exception:
        tag = ""
    return _group_state_key(f"{key}#{tag}" if tag else key, params)


def _group_state_key(key, params):
    group = (params or {}).get("_effect_group_key")
    return f"{key}#group:{group!r}" if group is not None else key


def _rewind_state(st: dict, t: Any) -> None:
    """t 回退（新一轮播放/重烘焙/循环回绕）→ 状态清零重开。

    基准 max_t 随复位（否则后续帧反复触发清空）；单调推进分支只推进基准。
    """
    try:
        t_f = float(t)
    except (TypeError, ValueError):
        return
    if t_f < st["max_t"]:
        st["groups"] = []
        st["last_beat"] = -1.0
        st["rng"] = random.Random(42)
        st["max_t"] = t_f          # 基准随倒带复位，否则后续帧反复触发清空
    elif t_f > st["max_t"]:
        st["max_t"] = t_f


def _get_state(effect_id: str, cols: int, rows: int, params: Any = None,
               t: Any = None) -> dict:
    """取该效果的状态字典（`groups` / `last_beat` / `rng`）。

    ★★ R170（子集 delay 相位）：本族内核是**有状态状态机**（按 `t` 推进 `last_beat`
      与活跃 `groups` 列表）。子集相位要按子集**分别**跑内核（各自时间不同），
      若两组共用一份状态，两次调用会互相推进时间线 → 动画整体漂移
      （实测：开子集后**未选中**的设备也全变，整段动画与不开时对不上）。
      故 `params["_subset_state_tag"]`（由引擎在按子集/按单元调用时注入）
      成为**状态命名空间的尾缀**：每个子集各自一条连贯时间线、彼此只差一个相位。
      无该键（默认）→ 键与改前**逐字节相同**（零回归）。
      尾缀用 `#` 而非 `_` 分隔，使 `_clear_state` 的 `startswith(effect_id + "_")` 仍命中。

    ★★ R181（倒带复位）：t **回退** → 新一轮播放（见 `_rewind_state`）。单调
      推进（实时逐帧/烘焙顺序扫）永不触发 → 输出与改前**逐位相同**；
      收益 = 同参重放逐位可复现（烘焙缓存/回归测试前提）+ 循环每圈一致。
    """
    key = _state_key(effect_id, cols, rows, params)
    if key not in _STATES:
        _STATES[key] = {
            "groups": [],      # 当前活跃的组列表
            "last_beat": -1.0, # 上次触发的节拍
            "rng": random.Random(42),
            "max_t": float("-inf"),
        }
    st = _STATES[key]
    _rewind_state(st, t)
    return st

def _clear_state(effect_id: str):
    keys = [k for k in _STATES if k.startswith(effect_id + "_")]
    for k in keys:
        del _STATES[k]


# ═══════════════════════════════════════════════════════════════════════
#  形状生成函数（返回相对坐标列表）
# ═══════════════════════════════════════════════════════════════════════

def _shape_triangle_up(size=2):
    """向上的三角形"""
    cells = []
    for r in range(size + 1):
        for c in range(-r, r + 1):
            cells.append((c, size - r))
    return cells

def _shape_triangle_down(size=2):
    """向下的三角形"""
    cells = []
    for r in range(size + 1):
        for c in range(-r, r + 1):
            cells.append((c, r - size))
    return cells

def _shape_diamond(size=2):
    """菱形"""
    cells = []
    for r in range(-size, size + 1):
        for c in range(-size, size + 1):
            if abs(r) + abs(c) <= size:
                cells.append((c, r))
    return cells

def _shape_square(size=2):
    """方形"""
    cells = []
    for r in range(-size, size + 1):
        for c in range(-size, size + 1):
            cells.append((c, r))
    return cells

def _shape_cross(size=2):
    """十字形"""
    cells = []
    for i in range(-size, size + 1):
        cells.append((i, 0))
        cells.append((0, i))
    return list(set(cells))

def _shape_star(size=2):
    """星形（十字+对角）"""
    cells = _shape_cross(size)
    for i in range(-size, size + 1):
        if i != 0:
            cells.append((i, i))
            cells.append((i, -i))
    return list(set(cells))

def _shape_ring(size=2):
    """环形"""
    cells = []
    for r in range(-size, size + 1):
        for c in range(-size, size + 1):
            dist = abs(r) + abs(c)
            if dist == size:
                cells.append((c, r))
    return cells

def _shape_l_shape(size=2):
    """L形"""
    cells = []
    for i in range(size + 1):
        cells.append((0, i))
    for i in range(1, size + 1):
        cells.append((i, 0))
    return cells

def _shape_spiral_arm(size=2):
    """螺旋臂（3臂）"""
    cells = [(0, 0)]
    for i in range(1, size + 1):
        # 3个臂，120度间隔
        for arm in range(3):
            angle = arm * 2 * math.pi / 3
            r = round(i * math.sin(angle))
            c = round(i * math.cos(angle))
            cells.append((c, r))
    return list(set(cells))

def _shape_hexagon(size=2):
    """六边形（近似）"""
    cells = []
    for r in range(-size, size + 1):
        for c in range(-size, size + 1):
            # 六边形近似：使用轴坐标
            if abs(r) <= size and abs(c) <= size and abs(r + c) <= size:
                cells.append((c, r))
    return cells

def _shape_arrow_right(size=2):
    """向右箭头"""
    cells = []
    for i in range(-size, size + 1):
        cells.append((0, i))  # 竖线
    for i in range(1, size + 1):
        cells.append((i, i))   # 上斜
        cells.append((i, -i))  # 下斜
    return list(set(cells))

def _shape_v_shape(size=2):
    """V形"""
    cells = []
    for i in range(size + 1):
        cells.append((-i, i))
        cells.append((i, i))
    return list(set(cells))

def _shape_zigzag(size=3):
    """锯齿形"""
    cells = []
    for i in range(size):
        cells.append((i * 2, 0))
        cells.append((i * 2 + 1, 1))
    return cells

def _shape_triangle_group(size=1):
    """三角形组（前1后2，如喷头布局）"""
    # 前排1个，后排2个
    cells = [(0, 0), (-1, 1), (1, 1)]
    if size >= 2:
        cells += [(0, 2), (-2, 2), (2, 2)]
    return cells

def _shape_wave_line(size=3):
    """波浪线"""
    cells = []
    for i in range(size * 2 + 1):
        r = round(math.sin(i * math.pi / 2))
        cells.append((i - size, r))
    return cells

# 形状字典
SHAPES = {
    "triangle_up": _shape_triangle_up,
    "triangle_down": _shape_triangle_down,
    "diamond": _shape_diamond,
    "square": _shape_square,
    "cross": _shape_cross,
    "star": _shape_star,
    "ring": _shape_ring,
    "l_shape": _shape_l_shape,
    "spiral_arm": _shape_spiral_arm,
    "hexagon": _shape_hexagon,
    "arrow_right": _shape_arrow_right,
    "v_shape": _shape_v_shape,
    "zigzag": _shape_zigzag,
    "triangle_group": _shape_triangle_group,
    "wave_line": _shape_wave_line,
}


# ═══════════════════════════════════════════════════════════════════════
#  序列动画帧系统
# ═══════════════════════════════════════════════════════════════════════

# 动画序列类型：每种序列定义一个函数，输入(frame_progress 0~1, frame_idx, total_frames)，
# 返回每个单元格的亮度因子(0~1)

def _anim_ripple(progress, cell_dist, max_dist):
    """涟漪：从中心向外扩散"""
    wave_pos = progress * (max_dist + 1)
    dist_from_wave = abs(cell_dist - wave_pos)
    return max(0.0, 1.0 - dist_from_wave * 1.5)

def _anim_fill_expand(progress, cell_dist, max_dist):
    """填充扩展：从中心向外逐渐填满"""
    threshold = progress * (max_dist + 1)
    if cell_dist <= threshold:
        return 1.0
    elif cell_dist <= threshold + 1:
        return threshold + 1 - cell_dist
    return 0.0

def _anim_pulse_fade(progress, cell_dist, max_dist):
    """脉冲消退：整体出现后逐渐消退"""
    appear = min(1.0, progress * 4)  # 快速出现
    fade = max(0.0, 1.0 - (progress - 0.5) * 2) if progress > 0.5 else 1.0
    return appear * fade

def _anim_explode(progress, cell_dist, max_dist):
    """爆炸：从中心向外爆炸，然后消退"""
    wave_pos = progress * (max_dist + 2)
    dist_from_wave = abs(cell_dist - wave_pos)
    brightness = max(0.0, 1.0 - dist_from_wave)
    fade = max(0.0, 1.0 - progress * 1.5)
    return brightness * fade + (1.0 - progress) * 0.3

def _anim_implode(progress, cell_dist, max_dist):
    """内爆：从外向内收缩"""
    wave_pos = (1.0 - progress) * (max_dist + 1)
    dist_from_wave = abs(cell_dist - wave_pos)
    return max(0.0, 1.0 - dist_from_wave * 1.5)

def _anim_strobe_appear(progress, cell_dist, max_dist):
    """频闪出现：出现时频闪"""
    appear_threshold = progress * (max_dist + 1)
    if cell_dist > appear_threshold:
        return 0.0
    strobe = 1.0 if int(progress * 10) % 2 == 0 else 0.5
    return strobe

def _anim_wave_sweep(progress, cell_dist, max_dist):
    """波浪扫过：波浪从一端到另一端"""
    wave_pos = progress * (max_dist * 2)
    dist_from_wave = abs(cell_dist - wave_pos)
    return max(0.0, 1.0 - dist_from_wave)

def _anim_breathe_in_out(progress, cell_dist, max_dist):
    """呼吸进出：整体呼吸"""
    v = math.sin(progress * math.pi)
    return v

ANIMATIONS = {
    "ripple": _anim_ripple,
    "fill_expand": _anim_fill_expand,
    "pulse_fade": _anim_pulse_fade,
    "explode": _anim_explode,
    "implode": _anim_implode,
    "strobe_appear": _anim_strobe_appear,
    "wave_sweep": _anim_wave_sweep,
    "breathe": _anim_breathe_in_out,
}


# ═══════════════════════════════════════════════════════════════════════
#  核心渲染引擎
# ═══════════════════════════════════════════════════════════════════════

def _render_groups(ef, t, sorted_fx, params, state, shape_name, anim_name,
                   ch="dimmer", lo=0, hi=255, cols=4):
    """核心渲染函数：渲染所有活跃的组到设备"""
    n = len(sorted_fx)
    if n == 0: return
    rows = max(1, (n + cols - 1) // cols)

    # 获取形状和动画函数
    shape_fn = SHAPES.get(shape_name, _shape_diamond)
    anim_fn = ANIMATIONS.get(anim_name, _anim_ripple)

    # 参数
    max_groups = max(1, int(params.get("max_groups", 3)))
    group_duration = float(params.get("group_duration", 2.0))
    trigger_bpm = float(params.get("trigger_bpm", 60.0))
    shape_size = max(1, int(params.get("shape_size", 2)))
    rng = state["rng"]

    # 节拍触发
    beat_phase = (t * trigger_bpm / 60.0) % 1.0
    beat_idx = int(t * trigger_bpm / 60.0)

    if beat_idx != state["last_beat"] and beat_phase < 0.3:
        state["last_beat"] = beat_idx
        if len(state["groups"]) < max_groups:
            # 随机选择中心位置
            cx = rng.randint(0, cols - 1)
            cy = rng.randint(0, rows - 1)
            # 随机选择序列中的变体（连续几个组效果不一样）
            variant = beat_idx % len(ANIMATIONS)
            anim_variants = list(ANIMATIONS.keys())
            state["groups"].append({
                "cx": cx, "cy": cy,
                "start_t": t,
                "anim_name": anim_variants[variant],
                "shape_cells": shape_fn(shape_size),
            })

    # 移除过期的组
    state["groups"] = [g for g in state["groups"]
                       if t - g["start_t"] < group_duration]

    # 初始化所有设备为低值
    for orig_i, fi in sorted_fx:
        ef.values[orig_i][ch] = lo

    # 渲染每个活跃的组
    for group in state["groups"]:
        progress = (t - group["start_t"]) / group_duration
        progress = max(0.0, min(1.0, progress))
        cx, cy = group["cx"], group["cy"]
        cells = group["shape_cells"]
        group_anim = ANIMATIONS.get(group["anim_name"], anim_fn)

        # 计算形状中最大距离（用于归一化）
        max_dist = max((abs(dc) + abs(dr) for dc, dr in cells), default=1)

        for dc, dr in cells:
            col = cx + dc
            row = cy + dr
            if 0 <= col < cols and 0 <= row < rows:
                seq_i = row * cols + col
                if seq_i < n:
                    orig_i, fi = sorted_fx[seq_i]
                    cell_dist = abs(dc) + abs(dr)
                    brightness = group_anim(progress, cell_dist, max_dist)
                    val = _clamp_v29(lo + brightness * (hi - lo))
                    # 取最大值（多组叠加）
                    current = ef.values[orig_i][ch]
                    ef.values[orig_i][ch] = max(current, val)


def _clamp_v29(v, lo=0, hi=255):
    return max(lo, min(hi, int(v)))


# ═══════════════════════════════════════════════════════════════════════
#  注册函数
# ═══════════════════════════════════════════════════════════════════════

def _register_v29_presets(engine: "EffectEngine"):
    """注册 v29 空间编组触发矩阵效果预设"""
    from .effect_engine import EffectPreset, ParamSpec
    reg = engine._reg

    # 通用参数列表
    def _common_params(ch_choices=None):
        if ch_choices is None:
            ch_choices = ["dimmer", "R", "G", "B", "W", "level", "speed"]
        return [
            ParamSpec("ch_type", "通道", "choice", "dimmer", choices=ch_choices,
                tooltip="选择效果作用的通道类型。阀门控制水流量、水泵控制水压、调光器控制灯光、电机控制摆头位置"),
            ParamSpec("cols", "列数", "int", 4, 2, 16, 1,
                tooltip="控制矩阵的列数。值越大每行设备越多"),
            ParamSpec("max_groups", "最大同时组数", "int", 3, 1, 20, 1,
                tooltip="控制同时活跃的最大分组数。值越高并行运行的组越多"),
            ParamSpec("group_duration", "组持续时间(秒)", "float", 2.0, 0.3, 10.0, 0.1,
                tooltip="控制每个分组的持续时间。值越大每组运行的时间越长"),
            ParamSpec("trigger_bpm", "触发BPM", "float", 60.0, 10.0, 300.0, 5.0,
                tooltip="控制触发效果的音乐节拍 BPM 值。值越大触发节奏越快"),
            ParamSpec("shape_size", "形状大小", "int", 2, 1, 5, 1,
                tooltip="控制形状的尺寸大小。值越大形状的覆盖范围越广"),
            ParamSpec("lo", "低值", "int", 0, 0, 255, 1,
                tooltip="控制输出范围的下限/低值。与高值配合定义动态区间，值越小弱端输出越低"),
            ParamSpec("hi", "高值", "int", 255, 0, 255, 1,
                tooltip="控制输出范围的上限/峰值。与低值配合定义动态区间，值越大输出顶值越高"),
        ]

    # ─── 三角形系列 ───────────────────────────────────────────────────

    def _make_shape_effect(shape_name, anim_name, effect_id, name, desc, icon, tags):
        _state_key = effect_id

        def _fn(ef, t, sorted_fx, fdefs, params, fps):
            cols = max(1, int(params.get("cols", 4)))
            n = len(sorted_fx)
            rows = max(1, (n + cols - 1) // cols)
            state = _get_state(_state_key, cols, rows, params, t=t)
            ch = params.get("ch_type", "dimmer")
            lo = int(params.get("lo", 0))
            hi = int(params.get("hi", 255))
            _render_groups(ef, t, sorted_fx, params, state, shape_name, anim_name,
                           ch=ch, lo=lo, hi=hi, cols=cols)

        reg(EffectPreset(
            id=effect_id, name=name, category="矩阵/空间编组",
            description=desc,
            target_channels=["dimmer", "R", "G", "B", "W", "level", "speed"],
            icon=icon, tags=tags,
            params=_common_params(),
        ), _fn)

    # 三角形 × 各种动画
    _make_shape_effect("triangle_up", "ripple",
        "matrix_tri_up_ripple_v29", "三角涟漪", "向上三角形组随机触发，涟漪扩散动画", "△≈",
        ["matrix","triangle","ripple","group","spatial"])

    _make_shape_effect("triangle_up", "fill_expand",
        "matrix_tri_up_fill_v29", "三角填充", "向上三角形组随机触发，从中心向外填充", "△▶",
        ["matrix","triangle","fill","expand","group"])

    _make_shape_effect("triangle_up", "explode",
        "matrix_tri_up_explode_v29", "三角爆炸", "向上三角形组随机触发，爆炸消退动画", "△💥",
        ["matrix","triangle","explode","burst","group"])

    _make_shape_effect("triangle_up", "pulse_fade",
        "matrix_tri_up_pulse_v29", "三角脉冲", "向上三角形组随机触发，脉冲消退动画", "△∿",
        ["matrix","triangle","pulse","fade","group"])

    _make_shape_effect("triangle_down", "ripple",
        "matrix_tri_down_ripple_v29", "倒三角涟漪", "向下三角形组随机触发，涟漪扩散动画", "▽≈",
        ["matrix","triangle","ripple","down","group"])

    _make_shape_effect("triangle_down", "implode",
        "matrix_tri_down_implode_v29", "倒三角内爆", "向下三角形组随机触发，内爆收缩动画", "▽◀",
        ["matrix","triangle","implode","down","group"])

    _make_shape_effect("triangle_group", "ripple",
        "matrix_tri_group_ripple_v29", "喷头三角组涟漪", "前1后2三角形喷头组随机触发，涟漪扩散", "△△△≈",
        ["matrix","triangle","group","nozzle","ripple","spatial"])

    _make_shape_effect("triangle_group", "fill_expand",
        "matrix_tri_group_fill_v29", "喷头三角组填充", "前1后2三角形喷头组随机触发，填充扩展", "△△△▶",
        ["matrix","triangle","group","nozzle","fill"])

    _make_shape_effect("triangle_group", "pulse_fade",
        "matrix_tri_group_pulse_v29", "喷头三角组脉冲", "前1后2三角形喷头组随机触发，脉冲消退", "△△△∿",
        ["matrix","triangle","group","nozzle","pulse"])

    # 菱形系列
    _make_shape_effect("diamond", "ripple",
        "matrix_diamond_ripple_v29", "菱形涟漪", "菱形组随机触发，涟漪从中心向外扩散", "◇≈",
        ["matrix","diamond","ripple","group","spatial"])

    _make_shape_effect("diamond", "fill_expand",
        "matrix_diamond_fill_v29", "菱形填充", "菱形组随机触发，从中心向外逐渐填满", "◇▶",
        ["matrix","diamond","fill","expand","group"])

    _make_shape_effect("diamond", "explode",
        "matrix_diamond_explode_v29", "菱形爆炸", "菱形组随机触发，爆炸消退动画", "◇💥",
        ["matrix","diamond","explode","burst","group"])

    _make_shape_effect("diamond", "implode",
        "matrix_diamond_implode_v29", "菱形内爆", "菱形组随机触发，内爆收缩动画", "◇◀",
        ["matrix","diamond","implode","group"])

    _make_shape_effect("diamond", "breathe",
        "matrix_diamond_breathe_v29", "菱形呼吸", "菱形组随机触发，整体呼吸动画", "◇∿",
        ["matrix","diamond","breathe","group"])

    _make_shape_effect("diamond", "strobe_appear",
        "matrix_diamond_strobe_v29", "菱形频闪出现", "菱形组随机触发，频闪出现动画", "◇✦",
        ["matrix","diamond","strobe","appear","group"])

    # 方形系列
    _make_shape_effect("square", "ripple",
        "matrix_square_ripple_v29", "方形涟漪", "方形组随机触发，涟漪从中心向外扩散", "□≈",
        ["matrix","square","ripple","group","spatial"])

    _make_shape_effect("square", "fill_expand",
        "matrix_square_fill_v29", "方形填充", "方形组随机触发，从中心向外逐渐填满", "□▶",
        ["matrix","square","fill","expand","group"])

    _make_shape_effect("square", "explode",
        "matrix_square_explode_v29", "方形爆炸", "方形组随机触发，爆炸消退动画", "□💥",
        ["matrix","square","explode","burst","group"])

    _make_shape_effect("square", "wave_sweep",
        "matrix_square_wave_v29", "方形波扫", "方形组随机触发，波浪从一端扫过", "□〜",
        ["matrix","square","wave","sweep","group"])

    _make_shape_effect("square", "breathe",
        "matrix_square_breathe_v29", "方形呼吸", "方形组随机触发，整体呼吸动画", "□∿",
        ["matrix","square","breathe","group"])

    # 十字形系列
    _make_shape_effect("cross", "ripple",
        "matrix_cross_ripple_v29", "十字涟漪", "十字形组随机触发，涟漪从中心向外扩散", "✚≈",
        ["matrix","cross","ripple","group","spatial"])

    _make_shape_effect("cross", "fill_expand",
        "matrix_cross_fill_v29", "十字填充", "十字形组随机触发，从中心向外填充", "✚▶",
        ["matrix","cross","fill","expand","group"])

    _make_shape_effect("cross", "explode",
        "matrix_cross_explode_v29", "十字爆炸", "十字形组随机触发，爆炸消退动画", "✚💥",
        ["matrix","cross","explode","burst","group"])

    _make_shape_effect("cross", "pulse_fade",
        "matrix_cross_pulse_v29", "十字脉冲", "十字形组随机触发，脉冲消退动画", "✚∿",
        ["matrix","cross","pulse","fade","group"])

    # 星形系列
    _make_shape_effect("star", "ripple",
        "matrix_star_ripple_v29", "星形涟漪", "星形组随机触发，涟漪从中心向外扩散", "★≈",
        ["matrix","star","ripple","group","spatial"])

    _make_shape_effect("star", "fill_expand",
        "matrix_star_fill_v29", "星形填充", "星形组随机触发，从中心向外填充", "★▶",
        ["matrix","star","fill","expand","group"])

    _make_shape_effect("star", "explode",
        "matrix_star_explode_v29", "星形爆炸", "星形组随机触发，爆炸消退动画", "★💥",
        ["matrix","star","explode","burst","group"])

    _make_shape_effect("star", "breathe",
        "matrix_star_breathe_v29", "星形呼吸", "星形组随机触发，整体呼吸动画", "★∿",
        ["matrix","star","breathe","group"])

    _make_shape_effect("star", "strobe_appear",
        "matrix_star_strobe_v29", "星形频闪", "星形组随机触发，频闪出现动画", "★✦",
        ["matrix","star","strobe","appear","group"])

    # 环形系列
    _make_shape_effect("ring", "ripple",
        "matrix_ring_ripple_v29", "环形涟漪", "环形组随机触发，涟漪扩散动画", "○≈",
        ["matrix","ring","ripple","group","spatial"])

    _make_shape_effect("ring", "pulse_fade",
        "matrix_ring_pulse_v29", "环形脉冲", "环形组随机触发，脉冲消退动画", "○∿",
        ["matrix","ring","pulse","fade","group"])

    _make_shape_effect("ring", "wave_sweep",
        "matrix_ring_wave_v29", "环形波扫", "环形组随机触发，波浪扫过动画", "○〜",
        ["matrix","ring","wave","sweep","group"])

    # L形系列
    _make_shape_effect("l_shape", "fill_expand",
        "matrix_l_fill_v29", "L形填充", "L形组随机触发，从中心向外填充", "L▶",
        ["matrix","l-shape","fill","expand","group"])

    _make_shape_effect("l_shape", "ripple",
        "matrix_l_ripple_v29", "L形涟漪", "L形组随机触发，涟漪扩散动画", "L≈",
        ["matrix","l-shape","ripple","group"])

    _make_shape_effect("l_shape", "explode",
        "matrix_l_explode_v29", "L形爆炸", "L形组随机触发，爆炸消退动画", "L💥",
        ["matrix","l-shape","explode","group"])

    # 螺旋臂系列
    _make_shape_effect("spiral_arm", "ripple",
        "matrix_spiral_ripple_v29", "螺旋臂涟漪", "三臂螺旋组随机触发，涟漪扩散动画", "✳≈",
        ["matrix","spiral","ripple","group","spatial"])

    _make_shape_effect("spiral_arm", "fill_expand",
        "matrix_spiral_fill_v29", "螺旋臂填充", "三臂螺旋组随机触发，从中心向外填充", "✳▶",
        ["matrix","spiral","fill","expand","group"])

    _make_shape_effect("spiral_arm", "breathe",
        "matrix_spiral_breathe_v29", "螺旋臂呼吸", "三臂螺旋组随机触发，整体呼吸动画", "✳∿",
        ["matrix","spiral","breathe","group"])

    # 六边形系列
    _make_shape_effect("hexagon", "ripple",
        "matrix_hex_ripple_v29", "六边形涟漪", "六边形组随机触发，涟漪扩散动画", "⬡≈",
        ["matrix","hexagon","ripple","group","spatial"])

    _make_shape_effect("hexagon", "fill_expand",
        "matrix_hex_fill_v29", "六边形填充", "六边形组随机触发，从中心向外填充", "⬡▶",
        ["matrix","hexagon","fill","expand","group"])

    _make_shape_effect("hexagon", "explode",
        "matrix_hex_explode_v29", "六边形爆炸", "六边形组随机触发，爆炸消退动画", "⬡💥",
        ["matrix","hexagon","explode","group"])

    _make_shape_effect("hexagon", "breathe",
        "matrix_hex_breathe_v29", "六边形呼吸", "六边形组随机触发，整体呼吸动画", "⬡∿",
        ["matrix","hexagon","breathe","group"])

    # 箭头系列
    _make_shape_effect("arrow_right", "ripple",
        "matrix_arrow_ripple_v29", "箭头涟漪", "箭头形组随机触发，涟漪扩散动画", "→≈",
        ["matrix","arrow","ripple","group"])

    _make_shape_effect("arrow_right", "fill_expand",
        "matrix_arrow_fill_v29", "箭头填充", "箭头形组随机触发，从中心向外填充", "→▶",
        ["matrix","arrow","fill","group"])

    _make_shape_effect("arrow_right", "wave_sweep",
        "matrix_arrow_wave_v29", "箭头波扫", "箭头形组随机触发，波浪扫过动画", "→〜",
        ["matrix","arrow","wave","sweep","group"])

    # V形系列
    _make_shape_effect("v_shape", "ripple",
        "matrix_v_ripple_v29", "V形涟漪", "V形组随机触发，涟漪扩散动画", "V≈",
        ["matrix","v-shape","ripple","group"])

    _make_shape_effect("v_shape", "fill_expand",
        "matrix_v_fill_v29", "V形填充", "V形组随机触发，从中心向外填充", "V▶",
        ["matrix","v-shape","fill","group"])

    _make_shape_effect("v_shape", "explode",
        "matrix_v_explode_v29", "V形爆炸", "V形组随机触发，爆炸消退动画", "V💥",
        ["matrix","v-shape","explode","group"])

    # 波浪线系列
    _make_shape_effect("wave_line", "ripple",
        "matrix_waveline_ripple_v29", "波浪线涟漪", "波浪线形组随机触发，涟漪扩散动画", "〜≈",
        ["matrix","wave-line","ripple","group"])

    _make_shape_effect("wave_line", "fill_expand",
        "matrix_waveline_fill_v29", "波浪线填充", "波浪线形组随机触发，从中心向外填充", "〜▶",
        ["matrix","wave-line","fill","group"])

    _make_shape_effect("wave_line", "breathe",
        "matrix_waveline_breathe_v29", "波浪线呼吸", "波浪线形组随机触发，整体呼吸动画", "〜∿",
        ["matrix","wave-line","breathe","group"])

    # ─── 混合序列：连续几个组效果不一样 ─────────────────────────────

#     def _matrix_mixed_sequence(ef, t, sorted_fx, fdefs, params, fps):
#         """混合序列：每次触发的组使用不同的动画，产生连续变化的效果"""
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         state = _get_state("matrix_mixed_seq_v29", cols, rows)
#         ch = params.get("ch_type", "dimmer")
#         lo = int(params.get("lo", 0))
#         hi = int(params.get("hi", 255))
#         shape_names = list(SHAPES.keys())
        # 随机形状
#         state_rng = state["rng"]
        # 使用随机形状
#         _render_groups(ef, t, sorted_fx, params, state,
#                        shape_names[int(t * 0.3) % len(shape_names)],
#                        "ripple", ch=ch, lo=lo, hi=hi, cols=cols)
# 
#     reg(EffectPreset(
#         id="matrix_mixed_sequence_v29", name="混合序列", category="矩阵/空间编组",
#         description="每次触发随机选择形状和动画，产生多变的空间编组效果",
#         target_channels=["dimmer", "R", "G", "B", "W", "level", "speed"],
#         icon="✦✦✦", tags=["matrix","mixed","sequence","random","group"],
#         params=_common_params(),
#     ), _matrix_mixed_sequence)

    # ─── 多形状同时触发 ──────────────────────────────────────────────

#     def _matrix_multi_shape(ef, t, sorted_fx, fdefs, params, fps):
#         """多形状同时触发：同时有三角、菱形、方形三种形状的组"""
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         ch = params.get("ch_type", "dimmer")
#         lo = int(params.get("lo", 0))
#         hi = int(params.get("hi", 255))
# 
        # 三种形状各自有独立状态
#         for shape_name in ["triangle_up", "diamond", "square"]:
#             state = _get_state(f"matrix_multi_{shape_name}_v29", cols, rows)
#             _render_groups(ef, t, sorted_fx, params, state, shape_name, "ripple",
#                            ch=ch, lo=lo, hi=hi, cols=cols)
# 
#     reg(EffectPreset(
#         id="matrix_multi_shape_v29", name="多形状混合触发", category="矩阵/空间编组",
#         description="三角形、菱形、方形三种形状同时随机触发，产生丰富的空间编组效果",
#         target_channels=["dimmer", "R", "G", "B", "W", "level", "speed"],
#         icon="△◇□", tags=["matrix","multi","shape","mixed","group"],
#         params=_common_params(),
#     ), _matrix_multi_shape)

    # ─── 音频响应版本 ────────────────────────────────────────────────

#     def _matrix_audio_diamond(ef, t, sorted_fx, fdefs, params, fps):
#         """音频响应菱形：音频强度影响触发密度和亮度"""
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         state = _get_state("matrix_audio_diamond_v29", cols, rows)
#         ch = params.get("ch_type", "dimmer")
#         lo = int(params.get("lo", 0))
#         hi = int(params.get("hi", 255))
# 
        # 音频影响：rms影响亮度，beat_phase影响触发
        # （通过修改params中的hi值来模拟音频响应）
#         audio_hi = hi  # 实际项目中可以从ctx获取rms
#         _render_groups(ef, t, sorted_fx, params, state, "diamond", "ripple",
#                        ch=ch, lo=lo, hi=audio_hi, cols=cols)
# 
#     reg(EffectPreset(
#         id="matrix_audio_diamond_v29", name="音频响应菱形", category="矩阵/空间编组",
#         description="菱形组随机触发，音频强度影响触发密度和亮度",
#         target_channels=["dimmer", "R", "G", "B", "W", "level", "speed"],
#         icon="◇♪", tags=["matrix","diamond","audio","responsive","group"],
#         params=_common_params(),
#     ), _matrix_audio_diamond)

#     def _matrix_audio_star(ef, t, sorted_fx, fdefs, params, fps):
#         """音频响应星形"""
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         state = _get_state("matrix_audio_star_v29", cols, rows)
#         ch = params.get("ch_type", "dimmer")
#         lo = int(params.get("lo", 0))
#         hi = int(params.get("hi", 255))
#         _render_groups(ef, t, sorted_fx, params, state, "star", "explode",
#                        ch=ch, lo=lo, hi=hi, cols=cols)
# 
#     reg(EffectPreset(
#         id="matrix_audio_star_v29", name="音频响应星形爆炸", category="矩阵/空间编组",
#         description="星形组随机触发爆炸动画，音频强度影响触发密度",
#         target_channels=["dimmer", "R", "G", "B", "W", "level", "speed"],
#         icon="★♪", tags=["matrix","star","audio","explode","group"],
#         params=_common_params(),
#     ), _matrix_audio_star)

    # ─── 快速连发版本 ────────────────────────────────────────────────

#     def _matrix_rapid_fire(ef, t, sorted_fx, fdefs, params, fps):
#         """快速连发：高BPM触发，产生密集的形状组"""
#         cols = max(1, int(params.get("cols", 4)))
#         n = len(sorted_fx)
#         rows = max(1, (n + cols - 1) // cols)
#         state = _get_state("matrix_rapid_fire_v29", cols, rows)
#         ch = params.get("ch_type", "dimmer")
#         lo = int(params.get("lo", 0))
#         hi = int(params.get("hi", 255))
        # 快速触发，短持续时间
#         fast_params = dict(params)
#         fast_params["trigger_bpm"] = float(params.get("trigger_bpm", 120.0)) * 2
#         fast_params["group_duration"] = float(params.get("group_duration", 2.0)) * 0.5
#         _render_groups(ef, t, sorted_fx, fast_params, state, "cross", "pulse_fade",
#                        ch=ch, lo=lo, hi=hi, cols=cols)
# 
#     reg(EffectPreset(
#         id="matrix_rapid_fire_v29", name="快速连发十字", category="矩阵/空间编组",
#         description="十字形组快速连发，产生密集的空间编组效果",
#         target_channels=["dimmer", "R", "G", "B", "W", "level", "speed"],
#         icon="✚✚✚", tags=["matrix","cross","rapid","fire","dense","group"],
#         params=_common_params(),
#     ), _matrix_rapid_fire)
