# -*- coding: utf-8 -*-
"""
effect_retire — R173 效果库精编"下架"机制：从效果库列表隐藏，但保留按 id 解析与计算。

与 effect_prune_list / effect_audit_patch 的"剪除"（_presets 与 _compute_fns 一并删除）不同：
已发行过的效果可能被旧场景快照、自定义效果（base_preset_id）、音频桥接池按 id 引用，
下架后这些引用必须照常工作、输出逐位不变。故下架 = 把 EffectPreset 从 `_presets`
挪到 `_retired_presets`：
  · 列表类接口（presets / all_presets / get_by_category / 浏览器）看不到；
  · 按 id 解析（get_preset / compute_at / 目标通道回退 / 包络绑定）照常可达。

各精编批次的下架 id 分表登记（互不改动对方的表，便于并行合入）。
"""
from __future__ import annotations

import logging

from .effect_catalog_curation import REPLACEMENTS

# R189: hide only; the old registry objects, parameters and compute stay intact.
RETIRED_R189_EFFECT: tuple = tuple(REPLACEMENTS)

#: R173 电机精编下架（死通道 / 静态轴 / 参数克隆）。见
#: ai-memory 效果库精编记录：7 个矩阵预设写入不存在的 "speed" 通道（死通道，
#: 从不被输出管线读取）；其余为与既有预设数值等价的克隆（同参数或经三角恒等式
#: 证明全参数域等价），克隆推理详见交接报告。
RETIRED_R173_MOTOR: tuple = (
    # 死通道：仅写 ef.values[i]["speed"]，非真实 DMX 通道，从未被读取。
    "motor_matrix_scan_line_v32",
    "motor_matrix_noise_v32",
    "motor_matrix_rotate_scan_v32",
    "motor_matrix_checker_breath_v32",
    "motor_matrix_row_wave_v32",
    "motor_matrix_double_helix_v32",
    "motor_matrix_surge_v32",
    # 克隆：Y 分量公式与 cur_form_weave_y 逐字节相同。
    "cur_form_checker_y",
    # 克隆：默认参数下与 motor_cross_nn_v35(group_size=2) 逐字节相同。
    "motor_cross22_v35",
    # 克隆：direction 双重取反抵消，恒为 +1，与 motor_cross22_v35 逐字节相同。
    "motor_cross22_rev_v35",
    # 克隆：同上双重取反 bug，与 motor_cross44_v35 逐字节相同。
    "motor_cross44_rev_v35",
    # 克隆：相位偏移法与 motor_fan_reverse_open_v35 的取反法对正弦波恒等。
    "motor_fan_alt_open_v35",
    # 克隆：默认参数下相位公式与 motor_fan_wave_open_v35 相同。
    "motor_fan_chase_v35",
    # 克隆：默认参数下 Y = -X，与 motor_xy_cross_wave_v35 逐字节相同。
    "motor_xy_cross_dragon_v35",
    # 克隆：fold 波形与 tri 波形在 [0,1) 上恒等（_waveshape 数学证明，全参数域成立）。
    "cur_msweep_fold_X",
    "cur_msweep_fold_Y",
)


#: R173 音频精编下架（非真音频驱动 / 死配置 / 克隆）。全部经真实 mp3 解码音频实测
#: （非合成驱动）复核，证据见音频精编收尾记忆。
RETIRED_R173_AUDIO: tuple = (
    # 1) 旧"音频反应"族：bpm 锁定/固定周期合成场冒充音乐律动——bpm 钉住实测下
    #    song1/song2/静音三源逐帧位级相同，证明零消费 rms/bands/onset/is_beat。
    "audio_bpm_flash", "audio_bpm_chase", "audio_bpm_wave", "audio_energy",
    "audio_color_beat", "audio_section", "color_explosion", "audio_spectrum_map",
    "audio_beat_fader", "audio_beat_joystick", "audio_beat_valve", "music_accent",
    # 2) "段落驱动"三效果：消费的 params["_audio_analysis"] 全项目零生产者，
    #    分支永不可达，逐帧退化为普通 a_rms 回声，与已保留的基础 rms 效果冗余。
    #    第四个同族 cur_audio_structure_onset_chase 不依赖该键、实测在真实歌曲上
    #    确有非零脉冲触发，判定为「设计上稀发」而非死代码，保留不下架。
    "cur_audio_structure_energy", "cur_audio_structure_build_drop", "cur_audio_struct_music_water",
    # 3) atx_* 跨帧记忆类效果：p.setdefault(...) 依赖的状态字典在引擎
    #    compute() 音频实时分支里每帧 dict(params) 重新浅拷贝，setdefault 的默认值
    #    每帧被重新求值——状态从未跨帧存活。这两个的退化形恰好与 atx_energy_floor
    #    (纯 rms 线性响应) 数学等价，真实音频实测相关系数 0.9965 / 0.9995+，确认
    #    为真克隆。同病根的 atx_melody_contour_track / atx_transient_density_heat /
    #    atx_pitch_stability_hold 退化形并不等价于任何其它效果（真实音频相关系数
    #    0.19-0.32），未达"克隆"下架标准，保留、标注为已知内部缺陷待后续引擎级修复
    #    （修复需改 effect_engine.compute()，本轮不在权限范围）。
    "atx_chord_change_accent", "atx_energy_slope_detector",
)


def retired_ids() -> tuple:
    """全部登记的下架 id（注册链尾统一下架；三族旧 id 由 fam_data 自行下架）。"""
    return RETIRED_R173_MOTOR + RETIRED_R173_AUDIO + RETIRED_R189_EFFECT


def retire_presets(engine, ids) -> int:
    """把 ids 从 engine._presets 挪到 engine._retired_presets（compute 保留），返回实际下架数。

    id 不在 _presets（未注册 / 已被剪除）时跳过——下架表不得成为"复活"入口。
    """
    store = engine._retired_presets
    n = 0
    for pid in ids:
        preset = engine._presets.pop(pid, None)
        if preset is None:
            continue
        store[pid] = preset
        n += 1
    return n


def apply_retire(engine) -> int:
    """注册链尾：下架登记表中的全部 id；log 实际下架数。"""
    ids = retired_ids()
    n = retire_presets(engine, ids)
    logging.getLogger(__name__).info("r173 retired presets: %s / listed %s", n, len(ids))
    return n
