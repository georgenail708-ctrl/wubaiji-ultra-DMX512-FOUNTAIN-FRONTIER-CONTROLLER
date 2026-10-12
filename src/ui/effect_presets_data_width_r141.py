# -*- coding: utf-8 -*-
"""R141 实测数据：**参数路径真的生效**的预设名单（勿手改，见下方再生成命令）。

背景：`effect_width`（效果宽窄）是**通用**参数（`EffectEngine._reg` 给每个预设
都补了声明），但引擎的收窄有两级：
  ① **参数路径**（R118）：`effect_spatial_semantics.narrowed_params()` 把
     `phase_step/phase_diff/fan_phase/fan_phase_diff/spatial_freq` × 1/w、
     `width/pulse_width/density` × w —— 只对**内核真的读这些键**的预设有效；
  ② **剖面路径**（R141）：`effect_engine._effect_width_profile_apply()` 在结果
     剖面上做 1/w 重采样 —— 补 ① 覆盖不到的预设。

没有一个静态特征能可靠判定"内核是否真的消费了宽度参数"（实测：`inspect.getsource`
拿到的是外层工厂函数的源码，会把**同工厂其它预设**读的键也算进来 —— 166 个"声明了
宽度参数"的预设里有 108 个其实是装饰性声明，改它没有任何输出变化）。因此本名单是
**实测生成**的：禁用 ② 后逐预设比较 `width=0.5` 与 `width=8.0` 的全通道输出矩阵，
不同 ⇒ 记录在此 ⇒ 该预设继续走 ①（`tests/test_r141_prog_effect_width.py` 会用
同一探针复测，名单漂移即失败）。

重新生成::

    venv/Scripts/python.exe _offline_render_work/probe_r141_width_gen_data.py

实测快照（2026-09-21，设备集 12 台、2s@100fps、全部通道参与比较）：
  · 全量预设 1185；本名单 382 个（参数路径生效，逐位零回归）；
  · 其余 803 个里 **457 个由剖面路径接管**（拖宽窄**现在有反应**）；
  · 确定性集合（1134 个，剔除 51 个状态化/随机内核）里：
    修复前 331 个有反应 → **修复后 788 个**；仍不响应的 346 个中
    232 个"整组同值"（每帧各设备取值相同 → 没有波峰可收窄，语义豁免）、
    其余为序列 <4 台 / per_shape 单元块场景。
"""

WIDTH_PARAM_PATH_PRESETS: frozenset = frozenset((
    "audio_bpm_wave", "chase_dual", "chase_strobe_random",
    "chase_triple", "color_hsv_scan", "color_rainbow_v21",
    "cur_adv_drifts1", "cur_adv_fbms1", "cur_adv_gravitys1",
    "cur_adv_harmonics1", "cur_adv_interferences1", "cur_adv_lzwaves1",
    "cur_adv_magnets1", "cur_adv_moires1", "cur_adv_pheromones1",
    "cur_adv_raindropss1", "cur_adv_resonants1", "cur_adv_ripplefields1",
    "cur_adv_standings1", "cur_adv_swarms1", "cur_adv_turings1",
    "cur_adv_voronois1", "cur_adv_vortexs1", "cur_amb_plasma_s45",
    "cur_chase_bounces1", "cur_chase_comets1", "cur_chase_comets1_26",
    "cur_chase_dots1", "cur_chase_scans1", "cur_chase_wave-saw-tools1",
    "cur_color2_colorpulse_s83", "cur_color2_colorwave_s84", "cur_color2_dualtone_s81",
    "cur_color2_ocean_s87", "cur_color_plasma_s27", "cur_color_rainbow_s22",
    "cur_color_rainbow_s23", "cur_colordir_rainbows1", "cur_fnt_bloom_s64",
    "cur_fnt_chase_s61", "cur_gate_notch_s80", "cur_gate_pulse_s77",
    "cur_gate_pulse_s78", "cur_gate_pulse_s79", "cur_gate_square_s74",
    "cur_gate_square_s75", "cur_gate_square_s76", "cur_lb_bounce_s35",
    "cur_lb_expo_s34", "cur_lb_fold_s37", "cur_lb_sine_s30",
    "cur_lb_tri_s31", "cur_lb_twin_s36", "cur_lc_comets1",
    "cur_lc_dots1", "cur_lc_scans1", "cur_lc_wave-saw-tools1",
    "cur_lf_jitter_s41", "cur_lf_sparkle_s38", "cur_lf_sparkle_s39",
    "cur_lf_twinkle_s40", "cur_mphys_resonant_X", "cur_mphys_resonant_Y",
    "cur_msweep_bounce_X", "cur_msweep_bounce_Y", "cur_msweep_fold_X",
    "cur_msweep_fold_Y", "cur_msweep_saw_up_X", "cur_msweep_saw_up_Y",
    "cur_msweep_sine_X", "cur_msweep_sine_Y", "cur_msweep_tri_X",
    "cur_msweep_tri_Y", "cur_nadv_contours1", "cur_nadv_dopplers1",
    "cur_nadv_solitons1", "cur_nadv_vortexstreets1", "cur_nadv_warps1",
    "cur_nadv_wavepackets1", "cur_nch_collides147", "cur_nch_collides148",
    "cur_nch_jumpseqs147", "cur_nch_jumpseqs148", "cur_nch_kitts147",
    "cur_nch_kitts148", "cur_nch_marquees147", "cur_nch_marquees148",
    "cur_nch_mirrors147", "cur_nch_mirrors148", "cur_nch_multiheads147",
    "cur_nch_multiheads148", "cur_nrand_glitchs151", "cur_nrand_glitchs152",
    "cur_nrand_lightnings151", "cur_nrand_popcorns151", "cur_nrand_popcorns152",
    "cur_nw_dpulses144", "cur_nw_dpulses145", "cur_nw_dpulses146",
    "cur_nw_flattops144", "cur_nw_flattops145", "cur_nw_flattops146",
    "cur_nw_gausss144", "cur_nw_gausss145", "cur_nw_gausss146",
    "cur_nw_rectsins144", "cur_nw_rectsins145", "cur_nw_rectsins146",
    "cur_nw_skewtris144", "cur_nw_skewtris145", "cur_nw_skewtris146",
    "cur_nw_stairsins144", "cur_nw_stairsins145", "cur_nw_stairsins146",
    "cur_nw_trapzs144", "cur_nw_trapzs145", "cur_nw_trapzs146",
    "cur_ov_bounce", "cur_ov_fold", "cur_ov_notch",
    "cur_ov_twin", "cur_pf_crowns1", "cur_pf_runjet_multis1",
    "cur_pf_runjets1", "cur_pf_wavewalls1", "cur_r8_mirror_ripple",
    "cur_r8_noise_flow", "cur_r8_plasma", "cur_r8_surge_breaker",
    "cur_r8a_pulsecluster", "cur_r8c_huetour", "cur_r8c_moire",
    "cur_r8c_oilfilm", "cur_r8c_tempband", "cur_r8s_boomerang",
    "cur_r8s_cross", "cur_r8s_dualsweep", "cur_r8s_pingpong",
    "cur_r8w_chop", "cur_r8w_crown", "cur_r8w_curtain",
    "cur_r8w_gallop", "cur_r8w_polka", "cur_r8w_tulip",
    "cur_r8x_diag", "cur_r8x_spiral", "cur_r8x_windmill",
    "cur_rand2_jitter_s132", "cur_rand2_sparkle_s129", "cur_rand2_twinkle_s130",
    "cur_rand_jitter_s14", "cur_rand_sparkle_s11", "cur_rand_sparkle_s12",
    "cur_rand_twinkle_s13", "cur_rgbchase_comets1", "cur_rgbchase_dots1",
    "cur_rgbchase_scans1", "cur_rgbchase_wave-saw-tools1", "cur_scene_s105",
    "cur_scene_s106", "cur_scene_s107", "cur_scene_s108",
    "cur_scene_s109", "cur_scene_s110", "cur_scene_s111",
    "cur_scene_s112", "cur_scene_s113", "cur_scene_s114",
    "cur_scene_s115", "cur_scene_s116", "cur_seq_ambient_drift",
    "cur_seq_build_drop", "cur_seq_calm_storm", "cur_seq_climax",
    "cur_seq_dream_seq", "cur_seq_festival", "cur_seq_finale",
    "cur_seq_fire_seq", "cur_seq_groove", "cur_seq_heartbeat_run",
    "cur_seq_intro_arc", "cur_seq_march_seq", "cur_seq_night_day",
    "cur_seq_ocean_seq", "cur_seq_pulse_run", "cur_seq_rise_fall",
    "cur_seq_spark_flow", "cur_seq_static_dyn", "cur_seq_wave_suite",
    "cur_valve2_bloom_s127", "cur_valve2_chase_s125", "cur_valve_chase_s69",
    "cur_vg_chop_fasts1", "cur_vg_headons1", "cur_vg_probgates1",
    "cur_vg_runners1", "cur_vg_sparkles1", "cur_vg_wavewindows1",
    "cur_wave2_bounces71", "cur_wave2_bounces72", "cur_wave2_bounces73",
    "cur_wave2_expos71", "cur_wave2_expos72", "cur_wave2_expos73",
    "cur_wave2_folds71", "cur_wave2_folds72", "cur_wave2_folds73",
    "cur_wave2_logs71", "cur_wave2_logs72", "cur_wave2_logs73",
    "cur_wave2_notchs71", "cur_wave2_notchs72", "cur_wave2_notchs73",
    "cur_wave2_pulses71", "cur_wave2_pulses72", "cur_wave2_pulses73",
    "cur_wave2_saw_downs71", "cur_wave2_saw_downs72", "cur_wave2_saw_downs73",
    "cur_wave2_saw_ups71", "cur_wave2_saw_ups72", "cur_wave2_saw_ups73",
    "cur_wave2_sines71", "cur_wave2_sines72", "cur_wave2_sines73",
    "cur_wave2_squares71", "cur_wave2_squares72", "cur_wave2_squares73",
    "cur_wave2_tris71", "cur_wave2_tris72", "cur_wave2_tris73",
    "cur_wave2_twins71", "cur_wave2_twins72", "cur_wave2_twins73",
    "cur_wave_bounces1", "cur_wave_expos1", "cur_wave_folds1",
    "cur_wave_logs1", "cur_wave_notchs1", "cur_wave_pulses1",
    "cur_wave_saw_downs1", "cur_wave_saw_ups1", "cur_wave_sines1",
    "cur_wave_squares1", "cur_wave_tris1", "cur_wave_twins1",
    "cur_wc_crowns153", "cur_wc_crowns154", "cur_wc_runners153",
    "cur_wc_runners154", "cur_wc_wavewalls153", "cur_wc_wavewalls154",
    "cur_ws_celebr_water", "cur_ws_celebr_water_slow", "cur_ws_dragon_water",
    "cur_ws_dragon_water_slow", "cur_ws_finale_water", "cur_ws_finale_water_slow",
    "cur_ws_geyser_show", "cur_ws_geyser_show_slow", "cur_ws_lotus_water",
    "cur_ws_lotus_water_slow", "cur_ws_music_water", "cur_ws_music_water_slow",
    "cur_ws_ode_water", "cur_ws_ode_water_slow", "cur_ws_overture",
    "cur_ws_overture_slow", "cur_ws_peacock_show", "cur_ws_peacock_show_slow",
    "cur_ws_runner_show", "cur_ws_runner_show_slow", "cur_ws_storm_water",
    "cur_ws_storm_water_slow", "cur_ws_tide_song", "cur_ws_tide_song_slow",
    "cur_ws_walldance", "cur_ws_walldance_slow", "cur_zmotor_bounce",
    "cur_zmotor_sine", "cur_zmotor_tri", "dimmer_pulse",
    "joystick_wave_scan", "light_accel_chase_v30", "light_bounce_chase_v30",
    "light_dual_chase_v30", "light_plain_wide_pulse_v33", "light_random_chase_v28",
    "light_random_strobe_v30", "light_sawtooth_chase_v30", "light_triple_chase_v30",
    "matrix_arrow_fill_v29", "matrix_arrow_ripple_v29", "matrix_arrow_wave_v29",
    "matrix_cross_explode_v29", "matrix_cross_fill_v29", "matrix_cross_pulse_v29",
    "matrix_cross_ripple_v29", "matrix_diamond_breathe_v29", "matrix_diamond_explode_v29",
    "matrix_diamond_fill_v29", "matrix_diamond_implode_v29", "matrix_diamond_ripple_v29",
    "matrix_diamond_strobe_v29", "matrix_hex_breathe_v29", "matrix_hex_explode_v29",
    "matrix_hex_fill_v29", "matrix_hex_ripple_v29", "matrix_l_explode_v29",
    "matrix_l_fill_v29", "matrix_l_ripple_v29", "matrix_ring_pulse_v29",
    "matrix_ring_ripple_v29", "matrix_ring_wave_v29", "matrix_spiral_breathe_v29",
    "matrix_spiral_fill_v29", "matrix_spiral_ripple_v29", "matrix_square_breathe_v29",
    "matrix_square_explode_v29", "matrix_square_fill_v29", "matrix_square_ripple_v29",
    "matrix_square_wave_v29", "matrix_star_breathe_v29", "matrix_star_explode_v29",
    "matrix_star_fill_v29", "matrix_star_ripple_v29", "matrix_star_strobe_v29",
    "matrix_tri_down_implode_v29", "matrix_tri_down_ripple_v29", "matrix_tri_group_fill_v29",
    "matrix_tri_group_pulse_v29", "matrix_tri_group_ripple_v29", "matrix_tri_up_explode_v29",
    "matrix_tri_up_fill_v29", "matrix_tri_up_pulse_v29", "matrix_tri_up_ripple_v29",
    "matrix_v_explode_v29", "matrix_v_fill_v29", "matrix_v_ripple_v29",
    "matrix_waveline_breathe_v29", "matrix_waveline_fill_v29", "matrix_waveline_ripple_v29",
    "motor_circle_chase_v35", "motor_dual_axis_dragon_v26", "motor_dual_axis_wave_v28",
    "motor_ellipse_chase_v35", "motor_fan_breath_v35", "motor_fan_position_v35",
    "motor_fan_pulse_open_v35", "motor_figure8_chase_v35", "motor_lissajous_v25",
    "motor_matrix_rotate_scan_v32", "motor_matrix_scan_line_v32", "motor_spiral_spread_v35",
    "motor_xy_butterfly_v35", "motor_xy_center_chase_v35", "motor_xy_cross_dragon_v35",
    "motor_xy_cross_wave_v35", "motor_xy_dual_chase_v35", "motor_xy_dual_freq_wave_v35",
    "motor_xy_spiral_dragon_v35", "pump_matrix_cross_v26", "pump_matrix_rotate_scan_v31",
    "pump_matrix_scan_line_v31", "pump_pressure_wave", "random_sparkle",
    "seq_十字轴线拓扑_frame1", "seq_双模式对照_frame1", "seq_稀疏网格_frame1",
    "seq_窗花曼陀罗_frame1", "seq_象限镜像_frame1", "seq_音频频谱级联_frame1",
    "wave_bounce", "wave_cosine", "wave_dual",
    "wave_expo", "wave_rainbow", "wave_random",
    "wave_sawtooth", "wave_sine", "wave_square",
    "wave_triangle",
))
