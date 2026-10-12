# -*- coding: utf-8 -*-
"""R141 实测数据：「**内核按归一化 0~1 消费**」的角度类参数（勿手改，见再生成命令）。

背景：`EffectEngine._reg` 里有一条自动加宽规则::

    if p.key == "angle_range" or "角度" in p.label:
        p.max_val = max(p.max_val, 360.0)

它按**标签文本**判定"这是角度参数"，于是任何标签含「角度」的参数都被声明成 0~360
—— 包括那些**内核其实按归一化 0~1 消费**的（`rad = 127.0 * clamp01(angle)` 这类
"展开量/幅度"，不是真角度）。后果：UI 滑条显示 0~360、用户拉到 90°，
内核把它 `min(1.0, ...)` 夹回 1.0 —— 用户看到的是"设 90° 等于设 1°"（实报）。

修复取**声明侧**（见下方理由），本名单是判据（**实测生成**）：内核把该参数当
归一化用（K=1.0 与 K=360.0 输出逐位相同）⇒ 不该被加宽到 360，跳过加宽即可
—— 声明回到内核真实语义，UI 不再提供会被静默夹掉的 0~360 量程。

为什么改声明侧而不是引擎读取侧（`angle/360` 归一化）：
  · 读取侧要改 **8 处内核**（4 个预设模块各 2-3 处 `params.get("angle")`），且
    这些内核的 `angle` 是**幅度/半径**（`rad = 127*angle`）而非周期角度 —— 把
    它当角度归一化会让「0 与 360 等价」的期望在幅度语义上不成立；
  · 读取侧归一化会**改已存工程的渲染结果**（工程里存的 0.5 会变成 0.5/360≈0），
    而声明侧只改 UI 量程：任何已存值渲染输出**逐位不变**（内核本来就夹到 1.0）。

重新生成::

    venv/Scripts/python.exe _offline_render_work/probe_r141_angle_gen_data.py

实测快照（2026-09-21，12 台线阵、2s@100fps、全通道逐位比较）：
  · 被加宽到 360 的角度类参数条目 234 个；
  · 其中**归一化语义**（本名单）206 个；真的按度消费 28 个（保留加宽）。
"""

#: ``"<preset_id>|<param_key>"`` —— 内核按归一化 0~1 消费、**不得**被加宽到 360。
NORMALIZED_ANGLE_PARAMS: frozenset = frozenset((
    "cur_fan_fan_bidirs1|angle", "cur_fan_fan_breathes1|angle",
    "cur_fan_fan_converges1|angle", "cur_fan_fan_crosss1|angle",
    "cur_fan_fan_drifts1|angle", "cur_fan_fan_mirrors1|angle",
    "cur_fan_fan_relays1|angle", "cur_fan_fan_ripples1|angle",
    "cur_fan_fan_steps1|angle", "cur_fan_fan_sweeps1|angle",
    "cur_fan_fan_traverses1|angle", "cur_fan_fan_waves1|angle",
    "cur_form_arch_y|angle", "cur_form_archs1|angle",
    "cur_form_aurora_dance_y|angle", "cur_form_aurora_dances1|angle",
    "cur_form_bow_y|angle", "cur_form_bows1|angle",
    "cur_form_bronze_bells_y|angle", "cur_form_bronze_bellss1|angle",
    "cur_form_butterfly_y|angle", "cur_form_butterflys1|angle",
    "cur_form_cascade_y|angle", "cur_form_cascades1|angle",
    "cur_form_checker_y|angle", "cur_form_checkers1|angle",
    "cur_form_compass_rose_y|angle", "cur_form_compass_roses1|angle",
    "cur_form_constellation_y|angle", "cur_form_constellations1|angle",
    "cur_form_crown_y|angle", "cur_form_crowns1|angle",
    "cur_form_dandelion_y|angle", "cur_form_dandelions1|angle",
    "cur_form_dna_y|angle", "cur_form_dnas1|angle",
    "cur_form_domino_y|angle", "cur_form_dominos1|angle",
    "cur_form_dragon_y|angle", "cur_form_dragonfly_y|angle",
    "cur_form_dragonflys1|angle", "cur_form_dragons1|angle",
    "cur_form_dualwave_y|angle", "cur_form_dualwaves1|angle",
    "cur_form_eagle_y|angle", "cur_form_eagles1|angle",
    "cur_form_fan_open_y|angle", "cur_form_fan_opens1|angle",
    "cur_form_fireworks_ring_y|angle", "cur_form_fireworks_rings1|angle",
    "cur_form_fishtail_y|angle", "cur_form_fishtails1|angle",
    "cur_form_flutter_y|angle", "cur_form_flutters1|angle",
    "cur_form_fountain_y|angle", "cur_form_fountains1|angle",
    "cur_form_funnel_y|angle", "cur_form_funnels1|angle",
    "cur_form_galaxy_y|angle", "cur_form_galaxys1|angle",
    "cur_form_glissando_y|angle", "cur_form_glissandos1|angle",
    "cur_form_heart_y|angle", "cur_form_hearts1|angle",
    "cur_form_hexagon_breath_y|angle", "cur_form_hexagon_breaths1|angle",
    "cur_form_horn_y|angle", "cur_form_horns1|angle",
    "cur_form_jellyfish_y|angle", "cur_form_jellyfishs1|angle",
    "cur_form_kite_line_y|angle", "cur_form_kite_lines1|angle",
    "cur_form_meteor_rain_y|angle", "cur_form_meteor_rains1|angle",
    "cur_form_mirrordance_y|angle", "cur_form_mirrordances1|angle",
    "cur_form_moon_phases_y|angle", "cur_form_moon_phasess1|angle",
    "cur_form_mountains_y|angle", "cur_form_mountainss1|angle",
    "cur_form_peacock_close_y|angle", "cur_form_peacock_closes1|angle",
    "cur_form_peacock_y|angle", "cur_form_peacocks1|angle",
    "cur_form_pendarray_y|angle", "cur_form_pendarrays1|angle",
    "cur_form_phyllo_y|angle", "cur_form_phyllos1|angle",
    "cur_form_ripple_out_y|angle", "cur_form_ripple_outs1|angle",
    "cur_form_satellite_y|angle", "cur_form_satellites1|angle",
    "cur_form_school_y|angle", "cur_form_schools1|angle",
    "cur_form_scissors_y|angle", "cur_form_scissorss1|angle",
    "cur_form_seagull_y|angle", "cur_form_seagulls1|angle",
    "cur_form_seesaw_y|angle", "cur_form_seesaws1|angle",
    "cur_form_silk_ribbon_y|angle", "cur_form_silk_ribbons1|angle",
    "cur_form_snowfall_y|angle", "cur_form_snowfalls1|angle",
    "cur_form_spiral_in_y|angle", "cur_form_spiral_ins1|angle",
    "cur_form_spread_y|angle", "cur_form_spreads1|angle",
    "cur_form_star_trails_y|angle", "cur_form_star_trailss1|angle",
    "cur_form_swing_y|angle", "cur_form_swings1|angle",
    "cur_form_taiji_y|angle", "cur_form_taijis1|angle",
    "cur_form_tide_wash_y|angle", "cur_form_tide_washs1|angle",
    "cur_form_tide_y|angle", "cur_form_tides1|angle",
    "cur_form_turn_y|angle", "cur_form_turns1|angle",
    "cur_form_vee_y|angle", "cur_form_vees1|angle",
    "cur_form_vortex_form_y|angle", "cur_form_vortex_forms1|angle",
    "cur_form_water_wheel_y|angle", "cur_form_water_wheels1|angle",
    "cur_form_waterfall_y|angle", "cur_form_waterfalls1|angle",
    "cur_form_weave_y|angle", "cur_form_weaves1|angle",
    "cur_form_wheat_y|angle", "cur_form_wheats1|angle",
    "cur_form_whirlpool_y|angle", "cur_form_whirlpools1|angle",
    "cur_form_windchime_y|angle", "cur_form_windchimes1|angle",
    "cur_form_windmill_y|angle", "cur_form_windmills1|angle",
    "cur_form_zipper_y|angle", "cur_form_zippers1|angle",
    "cur_fseq_ballet|angle", "cur_fseq_ballet_slow|angle",
    "cur_fseq_birds_dawn|angle", "cur_fseq_birds_dawn_slow|angle",
    "cur_fseq_bloom_seq|angle", "cur_fseq_bloom_seq_slow|angle",
    "cur_fseq_butterfly_love|angle", "cur_fseq_butterfly_love_slow|angle",
    "cur_fseq_carnival|angle", "cur_fseq_carnival_slow|angle",
    "cur_fseq_celestial|angle", "cur_fseq_celestial_slow|angle",
    "cur_fseq_ceremony|angle", "cur_fseq_ceremony_slow|angle",
    "cur_fseq_courtship|angle", "cur_fseq_courtship_slow|angle",
    "cur_fseq_daybreak|angle", "cur_fseq_daybreak_slow|angle",
    "cur_fseq_fan_show|angle", "cur_fseq_fan_show_slow|angle",
    "cur_fseq_four_seasons|angle", "cur_fseq_four_seasons_slow|angle",
    "cur_fseq_galaxy_flow|angle", "cur_fseq_galaxy_flow_slow|angle",
    "cur_fseq_hundred_birds|angle", "cur_fseq_hundred_birds_slow|angle",
    "cur_fseq_kaleido|angle", "cur_fseq_kaleido_slow|angle",
    "cur_fseq_machine|angle", "cur_fseq_machine_slow|angle",
    "cur_fseq_march_wave|angle", "cur_fseq_march_wave_slow|angle",
    "cur_fseq_meditation|angle", "cur_fseq_meditation_slow|angle",
    "cur_fseq_nine_heavens|angle", "cur_fseq_nine_heavens_slow|angle",
    "cur_fseq_ocean|angle", "cur_fseq_ocean_slow|angle",
    "cur_fseq_parade|angle", "cur_fseq_parade_slow|angle",
    "cur_fseq_peacock_tour|angle", "cur_fseq_peacock_tour_slow|angle",
    "cur_fseq_phoenix|angle", "cur_fseq_phoenix_slow|angle",
    "cur_fseq_review|angle", "cur_fseq_review_slow|angle",
    "cur_fseq_ripple_sym|angle", "cur_fseq_ripple_sym_slow|angle",
    "cur_fseq_serpent|angle", "cur_fseq_serpent_slow|angle",
    "cur_fseq_shanhaijing|angle", "cur_fseq_shanhaijing_slow|angle",
    "cur_fseq_star_odyssey|angle", "cur_fseq_star_odyssey_slow|angle",
    "cur_fseq_storm|angle", "cur_fseq_storm_slow|angle",
    "cur_fseq_tide_cycle|angle", "cur_fseq_tide_cycle_slow|angle",
    "cur_fseq_tornado|angle", "cur_fseq_tornado_slow|angle",
    "cur_fseq_twin_dragons|angle", "cur_fseq_twin_dragons_slow|angle",
    "cur_fseq_victory|angle", "cur_fseq_victory_slow|angle",
    "cur_fseq_wings|angle", "cur_fseq_wings_slow|angle",
))
