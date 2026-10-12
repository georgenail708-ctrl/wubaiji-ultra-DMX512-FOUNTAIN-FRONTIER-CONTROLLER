# -*- coding: utf-8 -*-
"""effect 空间语义统一层（R118）。

本模块收口两件**跨全部预设**的空间语义，两者都是"按测量结果固化"的真源，
不是手写猜测：

──────────────────────────────────────────────────────────────────────
一、相位推进方向（`PHASE_ADVANCE_PRESETS`）
──────────────────────────────────────────────────────────────────────
内核有两族时间/空间相位写法，**方向语义相反**：

  · **推进族**：`phase = t*speed + f(seq_i)`（f 随设备序号递增）
    → 相位峰值出现在 `f(seq_i) = P - t*speed`，随时间**减小**
    → 波峰朝**设备序号减小**方向跑。即"左到右"实际跑成"右到左"。
  · **滞后族**：`phase = f(seq_i) - t*speed`（如水景泵 `(pos - ph) % 1`）
    → 波峰朝序号增大方向跑 = 与 clip 过渡方向、用户预期一致。

用户实报（2026-09-20）::

    "effect的通用类型效果存在巨大的方向问题…全选所有百变，然后开到顺逆时针，
     左右，前后等等效果全部都是乱的。但是比如你马上调一个水景的泵专用效果，
     这个效果又是正确的方向。"

实测（FFT 相位梯度探针，24 台等距线阵，`_offline_render_work/probe_dir_family.py`）：
推进族 `corr(φ, x) = +1.0`（波向 −x，**反**）、水景泵族 `−1.0`（波向 +x，正确）。
全量 1133 个预设：**217 个推进族（反）**、264 个滞后族（正确）、652 个无空间相位。

修法：仅对推进族预设，在引擎入口把**几何方向名映射成镜像名**
（ltr↔rtl / ftb↔btf / 四对角互换 / cw↔ccw / ctr↔edge）。这样
  · 列表序（`apply_effect_direction`）与相位源（`_inject_matrix_coords`）**同时**翻转
    → 两种坐标系（整体/各自）一次性对齐；
  · 滞后族与无空间相位预设**逐字节不变**（映射表为空 → 零回归）。

★ 名单是**测量得到**的，不是人工枚举。重新生成：
  ``venv/Scripts/python _offline_render_work/probe_dir_family.py``
  → 以输出的 FLIP 列表覆盖下方集合。`tests/test_effect_direction_family_r118.py`
  会用同一探针对抽样预设复测，名单漂移会失败。

──────────────────────────────────────────────────────────────────────
二、效果宽窄（`narrowed_params`）
──────────────────────────────────────────────────────────────────────
用户澄清（2026-09-20）::

    "效果宽窄调小只是蛇的长度减少，它还是要跑完整个迷宫，而不是你现在这样
     只让他跑一半…这个效果还是涉及全部设备，只不过这个波峰或者说效果的单元
     设备减少，波峰承载的设备少。"

  · 旧实现（**错**，2026-09-19 版）：`_effect_width_subset` 把设备序列截成
    中间 M 台 → 效果**只跑中间一段**，两侧设备完全不参与。
  · 本实现：**不改设备序列**，只按比例收窄"波峰剖面"参数——
    相位步进类参数 × 1/width（波峰变窄），剖面宽度类参数 × width。
    时间项不动 → 波峰仍从序列头跑到尾（跑完整个迷宫）；
    默认 width ≥ 1 时**不碰任何参数**（零回归）。

参数分类（逐个核对过内核消费方式，见 tests 断言）：
  · 逆比（每设备相位步进）：phase_step / phase_diff / fan_phase / fan_phase_diff /
    spatial_freq
  · 正比（剖面占序列比例）：width / pulse_width / density
"""
from __future__ import annotations

from typing import Any, Dict

#: 相位**推进**族预设（`t*speed + f(seq_i)`）——方向名需镜像。
#: 由 `_offline_render_work/probe_dir_family.py` 的 FFT 相位梯度探针实测生成。
PHASE_ADVANCE_PRESETS: frozenset = frozenset((
    "chase_strobe_random", "color_aurora", "color_fire", "color_ocean",
    "combo_breathe_chase", "cur_adv_ca110s1", "cur_adv_ca184s1", "cur_adv_firewalls1",
    "cur_adv_geysers1", "cur_adv_shepards1", "cur_adv_tidemodels1", "cur_adv_turings1",
    "cur_adv_voronois1", "cur_amb_fire_s42", "cur_amb_fire_s43", "cur_arr_breathe_grps141",
    "cur_arr_breathe_grps143", "cur_arr_cascades141", "cur_arr_cascades143", "cur_breathe_breathe_s19",
    "cur_color2_colorpulse_s83", "cur_color2_sparkles_s86", "cur_color2_starfield_s85", "cur_color_fire_s29",
    "cur_fan_fan_steps1", "cur_fnt2_tide_s103", "cur_form_bronze_bells_y", "cur_form_compass_rose_y",
    "cur_form_compass_roses1", "cur_form_dnas1", "cur_form_dualwave_y", "cur_form_fireworks_rings1",
    "cur_form_flutter_y", "cur_form_funnels1", "cur_form_galaxy_y", "cur_form_galaxys1",
    "cur_form_glissando_y", "cur_form_hexagon_breath_y", "cur_form_hexagon_breaths1", "cur_form_horns1",
    "cur_form_jellyfishs1", "cur_form_moon_phasess1", "cur_form_mountains_y", "cur_form_mountainss1",
    "cur_form_peacock_closes1", "cur_form_phyllo_y", "cur_form_phyllos1", "cur_form_satellite_y",
    "cur_form_satellites1", "cur_form_school_y", "cur_form_schools1", "cur_form_silk_ribbon_y",
    "cur_form_silk_ribbons1", "cur_form_spiral_in_y", "cur_form_spiral_ins1", "cur_form_spreads1",
    "cur_form_star_trails_y", "cur_form_star_trailss1", "cur_form_tides1", "cur_form_turns1",
    "cur_form_water_wheel_y", "cur_form_water_wheels1", "cur_form_waterfall_y", "cur_form_waterfalls1",
    "cur_form_weaves1", "cur_form_windmill_y", "cur_form_windmills1", "cur_fseq_bloom_seq",
    "cur_fseq_bloom_seq_slow", "cur_fseq_butterfly_love", "cur_fseq_butterfly_love_slow", "cur_fseq_celestial",
    "cur_fseq_celestial_slow", "cur_fseq_daybreak", "cur_fseq_daybreak_slow", "cur_fseq_four_seasons",
    "cur_fseq_four_seasons_slow", "cur_fseq_galaxy_flow", "cur_fseq_galaxy_flow_slow", "cur_fseq_hundred_birds",
    "cur_fseq_hundred_birds_slow", "cur_fseq_kaleido", "cur_fseq_kaleido_slow", "cur_fseq_nine_heavens",
    "cur_fseq_star_odyssey", "cur_fseq_star_odyssey_slow", "cur_fseq_tornado", "cur_fseq_tornado_slow",
    "cur_fseq_wings", "cur_fseq_wings_slow", "cur_motor2_precess_fan", "cur_motor2_rwalk_fan",
    "cur_motor2_square_path_fan", "cur_motor2_star_fan", "cur_motor_ballyhoo_fan", "cur_motor_circle_fan",
    "cur_motor_ellipse_fan", "cur_motor_fig8_fan", "cur_motor_lissajous_fan", "cur_motor_lissajous_fan_85",
    "cur_motor_sweep_fan", "cur_mphys_damped_X", "cur_mphys_damped_Y", "cur_mphys_inertia_X",
    "cur_mphys_inertia_Y", "cur_mphys_overshoot_X", "cur_mphys_overshoot_Y", "cur_nadv_fracbands1",
    "cur_nadv_wavepackets1", "cur_nch_jumpseqs147", "cur_nch_jumpseqs148", "cur_nch_marquees147",
    "cur_nch_marquees148", "cur_nrand_lightnings152", "cur_ov_notch", "cur_pf_crowns1",
    "cur_pf_geysers1", "cur_pf_peakss1", "cur_pf_stair_waters1", "cur_pf_willows1",
    "cur_r8_noise_flow", "cur_r8_plasma", "cur_r8a_afterglow", "cur_r8a_bandmap",
    "cur_r8a_bassdrive", "cur_r8a_breath", "cur_r8c_huetour", "cur_r8c_moire",
    "cur_r8c_spectral", "cur_r8w_gallop", "cur_r8w_rings", "cur_r8x_quadrant",
    "cur_r8x_spiral", "cur_rand2_sparkle_s129", "cur_seq_color_journey", "cur_valve2_step_water_s128",
    "cur_vg_euclids1", "cur_vg_probgates1", "cur_vg_sparkles1", "cur_wc_crowns153",
    "cur_wc_crowns154", "cur_wc_geysers153", "cur_wc_geysers154", "cur_ws_lotus_water",
    "cur_ws_lotus_water_slow", "cur_ws_overture", "cur_ws_overture_slow", "cur_ws_peacock_show",
    "cur_ws_peacock_show_slow", "dynamic_bpm_divider", "dynamic_crescendo", "joystick_wave_scan",
    "matrix_scroll_v", "motor_accel_dragon_v26", "motor_breath_layer_v25", "motor_breath_open_close_v26",
    "motor_caterpillar_dragon_v27", "motor_circle_chase_v35", "motor_cross44_fan_v35", "motor_cross_half_split_v26",
    "motor_cross_interleave_v26", "motor_cross_progressive_v26", "motor_cross_quad_v26", "motor_cross_wave_v25",
    "motor_dragon_rotate_v26", "motor_dual_axis_dragon_v26", "motor_elastic_bounce_v28", "motor_elastic_dragon_v26",
    "motor_ellipse_chase_v35", "motor_fan_spread_gather_v35", "motor_figure8_chase_v35", "motor_gradient_dragon_v26",
    "motor_matrix_noise_v32", "motor_matrix_row_wave_v32", "motor_matrix_surge_v32", "motor_multi_fan_v26",
    "motor_open_close_screen", "motor_spiral_decay_v26", "motor_spiral_rise_v25", "motor_spiral_spread_v35",
    "motor_swan_lake_v25", "motor_triple_dragon_v26", "motor_xy_alt_open_v35", "pos_heart",
    "pos_linear_scan", "pump_matrix_noise_v31", "pump_matrix_plaid_v28", "pump_matrix_row_wave_v31",
    "pump_matrix_surge_v31", "pump_pressure_wave", "random_sparkle", "wave_bounce",
    "wave_cosine", "wave_expo", "wave_random", "wave_sawtooth",
    "wave_sine", "wave_square", "wave_triangle",
))

#: 几何方向 → 镜像方向。逆向（reverse）保持"与 default 相反"的原义，
#: 故推进族下映射到 ltr（default 的相位源是 pos_x 投影，即波向 −x）。
_MIRROR: Dict[str, str] = {
    "left_to_right": "right_to_left",
    "right_to_left": "left_to_right",
    "front_to_back": "back_to_front",
    "back_to_front": "front_to_back",
    "diag_tl_br": "diag_br_tl",
    "diag_br_tl": "diag_tl_br",
    "diag_bl_tr": "diag_tr_bl",
    "diag_tr_bl": "diag_bl_tr",
    "center_out": "edge_in",
    "edge_in": "center_out",
    "clockwise": "counterclockwise",
    "counterclockwise": "clockwise",
    "reverse": "left_to_right",
}

#: 相位步进类参数：× 1/width（值越大波峰越窄）。
_WIDTH_INVERSE_PARAMS = (
    "phase_step", "phase_diff", "fan_phase", "fan_phase_diff", "spatial_freq",
)

#: 剖面宽度类参数：× width（值越小波峰越窄）。
_WIDTH_DIRECT_PARAMS = ("width", "pulse_width", "density")


def is_phase_advance(preset_id: Any) -> bool:
    """该预设是否属于相位推进族（方向语义与其余相反）。"""
    return str(preset_id or "") in PHASE_ADVANCE_PRESETS


def effective_direction(preset_id: Any, direction: Any) -> str:
    """预设 + 用户所选方向 → **实际**用于排序/注入的方向名。

    非推进族原样返回（零回归）；推进族镜像。未知/空方向原样返回。
    """
    d = str(direction or "default")
    if not is_phase_advance(preset_id):
        return d
    return _MIRROR.get(d, d)


def effective_params(preset_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
    """把 `_app_direction` 换成镜像后的有效方向（需要时才拷贝）。

    调用点：引擎 `compute` / `compute_at` 入口、浏览器预览排序链入口。
    非推进族**返回原对象**（零拷贝、零回归）。
    """
    if not isinstance(params, dict) or not is_phase_advance(preset_id):
        return params
    d = str(params.get("_app_direction", "default") or "default")
    mirrored = _MIRROR.get(d, d)
    if mirrored == d:
        return params
    out = dict(params)
    out["_app_direction"] = mirrored
    return out


def width_factor(params: Any) -> float:
    """读取「效果宽窄」比例；≥1（或缺失/非法）→ 1.0（全宽，零回归）。"""
    if not isinstance(params, dict):
        return 1.0
    try:
        w = float(params.get("effect_width", 1.0))
    except (TypeError, ValueError):
        return 1.0
    if w >= 0.999:
        return 1.0
    return max(0.001, w)


def narrowed_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """按「效果宽窄」收窄剖面参数（**不改设备序列**）。

    返回新 dict（不需要收窄时返回原对象）。只动空间剖面参数，
    时间项/速度/通道/低高值一概不动 → 效果仍跑完全部设备。
    """
    w = width_factor(params)
    if w >= 0.999 or not isinstance(params, dict):
        return params
    out = dict(params)
    inv = 1.0 / w
    for key in _WIDTH_INVERSE_PARAMS:
        try:
            out[key] = float(out[key]) * inv
        except (KeyError, TypeError, ValueError):
            pass
    for key in _WIDTH_DIRECT_PARAMS:
        try:
            out[key] = float(out[key]) * w
        except (KeyError, TypeError, ValueError):
            pass
    return out


#: **刻意不镜像**的预设（结构性对称/多向运动，无单一行进方向）。
#: 探针会把它们判成"反"，但逐个读内核确认是设计如此——镜像只会偏离作者意图：
#:   · cur_arr_dualflows141/143, cur_vg_headons1  —— 双向对涌，两端向中
#:   · cur_vg_opposings1                        —— 奇偶高低反相
#:   · cur_form_checker_y, cur_form_horn_y, cur_form_weave_y, cur_form_spread_y
#:                                              —— 号角张合/棋盘错动等对称形态
#:   · motor_split_half                         —— 对半分开（两半反向）
#:   · motor_cross{22,33,44,66,nn,44_xy,22_rev,44_rev}_v35 —— 分组交叉，方向互反
#:   · cur_r8s_boomerang                        —— 回旋镖（去而复返）
SYMMETRIC_NOT_MIRRORED: frozenset = frozenset((
    "cur_arr_dualflows141", "cur_arr_dualflows143", "cur_form_checker_y",
    "cur_form_horn_y", "cur_form_spread_y", "cur_form_weave_y",
    "cur_r8s_boomerang", "cur_vg_headons1", "cur_vg_opposings1",
    "motor_cross22_rev_v35", "motor_cross22_v35", "motor_cross33_v35",
    "motor_cross44_rev_v35", "motor_cross44_v35", "motor_cross44_xy_v35",
    "motor_cross66_v35", "motor_cross_nn_v35", "motor_split_half",
))


__all__ = ["PHASE_ADVANCE_PRESETS", "SYMMETRIC_NOT_MIRRORED", "is_phase_advance", "effective_direction",
           "effective_params", "width_factor", "narrowed_params"]
