"""Qascade CE — 隔离 C 渲染引擎测试版（render_lab）。

隔离纪律：
  - 不修改 src/ 任何文件；替换点经 monkeypatch 注入，仅 QASCADE_CT_ENGINE=1 且
    DLL 存在时生效；否则自动回退原路径，逐字节不变。
  - 合入正式版 = 搬 render_lab/qascade_ce/ + csrc/ 进 src/engine/，改本 install()
    为默认安装；回滚 = 删目录。

用法（探针侧，两行）：
    import sys; sys.path.insert(0, "render_lab")
    from qascade_ce import install; install()
"""
from __future__ import annotations

import ctypes
import logging
import math
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
from .native_legacy_parallel import wrap_parallel_library

log = logging.getLogger(__name__)

# 仓库根（render_lab/qascade_ce/ 的上级上级）——探针可能在 sys.path 就绪前调用 install
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

_installed = False
_engine = None
# 契约校验结果（安装时写入；见 hook_contract / install() 尾部）。
# 结构: {"attached":[...], "not_attached":[...], "missing_target":[...]}
# 用途: 启动日志 + boot_trace 落盘 + tests/test_ce_contract.py 断言。
_CE_CONTRACT_REPORT: dict = {}
# 泵预充电 C 数学调用计数（诊断: >0 = C 路径真实执行, 非静默回退原版）
_PRECHARGE_CNT = [0]
# 心跳 C 数学段执行计数（诊断: >0 = ce_heartbeat_* 真实执行, 非静默回退原版）
_HEARTBEAT_CNT = [0]
# T10 TopView 同步/编组 C 路径调用计数（诊断: >0 = C 路径真实执行, 非静默回退）
_TOPVIEW_CNT = {"catmull": 0, "angle": 0, "ransac": 0, "assign": 0,
                "cf3": 0, "axis": 0, "window": 0}
# ── 活性探针全扫计数（诊断: 每项 >0 = hook 真实执行, 非静默回退; 全扫断言见
#    bench/ce_probe_hook_activation.py）──────────────────────────────────
_K0_CNT = [0]            # K0: ce_mix_clips 内核调用（_get_render_kernels 替换）
_T3_MIX_CNT = [0]        # T3: _mix_one_universe → ce_full_mix 直连执行
_T5B_FLUSH_CNT = [0]     # T5b: flush_universes_now → ce_full_mix_many 批量执行
_FLUSH_TIMING_CNT = [0]  # T5: flush 计时包装执行（诊断 hook 本身活性）
_K3_BAKE_CNT = [0]       # K3: compute() 批量 → ce_bake_sequence_omp 离线烘焙
_XYREF_CNT = [0]         # H3-1: build_xy_reference_map → ce_xy_ref_build
_SMOOTHER_CNT = [0]      # H3-2: OutputSmoother.process → ce_smoother_apply
_INTERLOCK_CNT = [0]     # H3-3: SafetyInterlock.process → ce_interlock_process
_AUDIO_CNT = {"energy": 0, "bands": 0, "normalized_bands": 0, "flatness": 0}  # H3-4 音频串行内核
_AUDIO_BATCH_CNT = [0]   # H3-4 离线整曲烘焙批量预取帧服务计数
# H1: C 内核异常回退失败计数（AUDIT_BUGS_ACTIVE.md H1）——每个 hook 的
# except Exception（C 失败 → 回退原版）路径 +1; 探针断言 ==0（>0 = 静默
# 假死被抓住, 历史 T3/T4 类假死若重演将直接 FAIL 合入门禁）。
_CE_EXC_CNT = [0]
# K2 离线时间线整帧融合 / K5/K6 灯跟踪 C 路径执行计数（诊断: >0 = C 路径真实
# 执行, 非静默回退原版）; FALLBACK = 整段/单次回退原版计数（对齐 H1 纪律）。
_TIMELINE_RANGE_CNT = [0]
_TIMELINE_RANGE_FALLBACK_CNT = [0]
_LT_APPLY_CNT = [0]
_LT_APPLY_FALLBACK_CNT = [0]
# T12 关联器（track linker）C 路径计数（诊断: >0 = C 路径真实执行, 非静默
# 回退原版）。HIT = 规则整条由 ce_linker_rule 完成; MISS = 契约外/内核拒绝/
# 异常 → 该规则回退 Python 原版（同一 tick 内其余规则不受影响）;
# FALLBACK = MISS 中真正调用了原版 _process_rule 的次数（1:1 于 MISS 的
# 可执行部分; PRESKIP=规则本就不产生写入的短路不计）。
_LINKER_HIT_CNT = [0]
_LINKER_MISS_CNT = [0]
# T13 矩阵像素引擎（matrix slideshow pixel engine）C 路径计数（诊断: >0 = C
# 路径真实执行, 非静默回退原版）。HIT = 整帧由 ce_mtx_pattern/ce_mtx_particle
# 完成; MISS = 无网格/长度不符/数组非常规/内核拒绝/异常 → 该帧回退 Python
# 原版（输出逐字节不变）。
_MTX_HIT_CNT = [0]
_MTX_MISS_CNT = [0]
# T14 effect `_tick_play` 写回层（effect_browser 脚手架）C 路径计数
# （诊断: >0 = C 路径真实执行, 非静默回退原版）。HIT = 本次逐域写整段由
# ce_eff_write_direct/ce_eff_write_rgbw 完成; MISS = 契约拒绝（值域外/
# 下标越界/dtype 不符/电机路径地址重复）/内核拒绝/异常 → 本次调用回退 Python
# 原版（输出逐字节不变）。
_EFF_WRITE_HIT_CNT = [0]
_EFF_WRITE_MISS_CNT = [0]
# T13 热路径指针类型（模块级预取 —— 热路径纪律: argtypes 预注册, 不逐帧构造）
_MTX_I32P = ctypes.POINTER(ctypes.c_int32)
_MTX_F64P = ctypes.POINTER(ctypes.c_double)
# v29 有状态矩阵（effect_presets_v29._make_shape_effect 注册的 51 个预设）:
# preset id → C shape_idx（SHAPES 字典序 0..14, 同 csrc ce_engine.c _ce_mxs_shape）。
# anim_name 是死参数——组动画由 beat_idx%8 决定（_render_groups :305 variant）,
# C 内核内部按 CE_MXS_ANIM_N=8 复刻, 无需 per-preset 映射。
_V29_SHAPE_IDX = {
    "matrix_tri_up_ripple_v29": 0, "matrix_tri_up_fill_v29": 0,
    "matrix_tri_up_explode_v29": 0, "matrix_tri_up_pulse_v29": 0,
    "matrix_tri_down_ripple_v29": 1, "matrix_tri_down_implode_v29": 1,
    "matrix_diamond_ripple_v29": 2, "matrix_diamond_fill_v29": 2,
    "matrix_diamond_explode_v29": 2, "matrix_diamond_implode_v29": 2,
    "matrix_diamond_breathe_v29": 2, "matrix_diamond_strobe_v29": 2,
    "matrix_square_ripple_v29": 3, "matrix_square_fill_v29": 3,
    "matrix_square_explode_v29": 3, "matrix_square_wave_v29": 3,
    "matrix_square_breathe_v29": 3,
    "matrix_cross_ripple_v29": 4, "matrix_cross_fill_v29": 4,
    "matrix_cross_explode_v29": 4, "matrix_cross_pulse_v29": 4,
    "matrix_star_ripple_v29": 5, "matrix_star_fill_v29": 5,
    "matrix_star_explode_v29": 5, "matrix_star_breathe_v29": 5,
    "matrix_star_strobe_v29": 5,
    "matrix_ring_ripple_v29": 6, "matrix_ring_pulse_v29": 6,
    "matrix_ring_wave_v29": 6,
    "matrix_l_ripple_v29": 7, "matrix_l_fill_v29": 7,
    "matrix_l_explode_v29": 7,
    "matrix_spiral_ripple_v29": 8, "matrix_spiral_fill_v29": 8,
    "matrix_spiral_breathe_v29": 8,
    "matrix_hex_ripple_v29": 9, "matrix_hex_fill_v29": 9,
    "matrix_hex_explode_v29": 9, "matrix_hex_breathe_v29": 9,
    "matrix_arrow_ripple_v29": 10, "matrix_arrow_fill_v29": 10,
    "matrix_arrow_wave_v29": 10,
    "matrix_v_ripple_v29": 11, "matrix_v_fill_v29": 11,
    "matrix_v_explode_v29": 11,
    "matrix_tri_group_ripple_v29": 13, "matrix_tri_group_fill_v29": 13,
    "matrix_tri_group_pulse_v29": 13,
    "matrix_waveline_ripple_v29": 14, "matrix_waveline_fill_v29": 14,
    "matrix_waveline_breathe_v29": 14,
}
# v29 有状态矩阵 C ctx 缓存。键 = (pid, cols, rows), 对齐 Python _STATES 的
# f"{effect_id}_{cols}_{rows}"（rows = max(1, ceil(n/cols)) —— n 变化即新状态,
# 同 _get_state 语义）。Python 侧 _STATES 进程级常驻、_clear_state 仅探针调用
# （src/ 无清理路径）→ ctx 同样常驻, 生命周期对齐; 未注册/未命中的 pid 不在
# _V29_SHAPE_IDX 内 → 永不入缓存。
_V29_CTX = {}

# ── C 内核契约守卫（对抗审查 M6/M7 加固; 供 bench 探针直接引用）─────────
_CE_ENV_MAX_PTS = 256  # csrc ce_engine.c CE_ENV_MAX_PTS（mode=1 控制点截断上限）


def _ce_gain_arrays_ok(K, *arrays):
    """P5a/P5b C 路径守卫: 分组数组为空或短于 K → C 内核按 K 越界读（UB）
    → hook 回退原版（字节不变）。None（该方向未启用）恒放行。"""
    for a in arrays:
        if a is not None and int(getattr(a, "size", -1)) < K:
            return False
    return True


def _ce_env_arrays(env, mode):
    """控制点 → 两个 f64 连续数组; 提取失败或超出 C 点数上限 → (None, None)
    （调用方走原版求值）。"""
    try:
        if mode == 0:
            pts = getattr(getattr(env, "curve", None), "points", None) or []
            xs = [float(p.x) for p in pts]
            ys = [float(p.y) for p in pts]
        else:
            pts = getattr(env, "points", None) or []
            xs = [float(p[0]) for p in pts]
            ys = [float(p[1]) for p in pts]
        if not xs:
            return None, None
        if mode == 1 and len(xs) > _CE_ENV_MAX_PTS:
            # C 内核 mode=1 截断至前 256 点（ce_engine.c CE_ENV_MAX_PTS）,
            # Python 全量稳定排序求值 → 静默分歧 → 单绑定回退原版（字节不变）。
            return None, None
        return (np.ascontiguousarray(xs, np.float64),
                np.ascontiguousarray(ys, np.float64))
    except Exception:
        return None, None


def _ce_env_python_ref(env, t, lo, hi, is_int):
    """原版单绑定管线（_apply_param_envelopes :1204-1231）——A/B 参照。"""
    norm = max(0.0, min(1.0, float(env.value_at(t))))
    val = lo + norm * (hi - lo)
    if is_int:
        val = int(round(val))
    return float(val)


def _hook_k0(engine):
    """K0: 替换 PlaybackEngine 的 C 内核句柄（_get_render_kernels 返回值）。

    仅当原内核在场（QASCADE_C_KERNELS 未强制关闭）时替换为 CE 内核；
    原 _mix_clips 的 try/except 回退 numpy 路径保留——CE 调用失败自动降级，字节不变。
    """
    from src.engine import playback_engine as pe

    orig_get = pe._get_render_kernels

    class _CEKernel:
        """模拟 Cython kernel 对象的 mix_clips_kernel 接口。"""

        __slots__ = ("_ce",)

        def __init__(self, ce):
            self._ce = ce

        def mix_clips_kernel(self, vals, acts, anchors, mod_gain):
            # M3: 内核缺失（DLL 未导出）→ 抛错, 上游 _mix_clips try/except
            # 回退 numpy 路径（原版行为, 字节不变）
            if getattr(self._ce, "ce_mix_clips", None) is None:
                raise RuntimeError("CE 内核 ce_mix_clips 缺失（DLL 未重建）")
            vals = np.ascontiguousarray(vals, dtype=np.float32)
            acts = np.ascontiguousarray(acts, dtype=np.uint8)
            anchors = np.ascontiguousarray(anchors, dtype=np.float32)
            n = int(vals.shape[0])
            out = np.empty(512, dtype=np.uint8)
            act_any = np.empty(512, dtype=np.uint8)
            # 实测: 真实 nlay 1-4 时 SIMD 单线程初始化/收尾开销 > 收益（纯mix 79.4→138ms），
            # 用标量路径；提速杠杆在 ce_mix_clips_batch（批量 40×，P4 接入调用侧）。
            self._ce.ce_mix_clips(
                vals.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                acts.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
                anchors.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                n, float(mod_gain),
                out.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
                act_any.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
            )
            _K0_CNT[0] += 1
            return out, act_any

    ce_kernel = _CEKernel(engine)

    def _patched_get_render_kernels():
        orig = orig_get()
        if orig is None:
            return None          # 原路径已关闭内核 → 保持一致（走 numpy）
        return ce_kernel

    pe._get_render_kernels = _patched_get_render_kernels
    log.info("[CE] K0 已接入: PlaybackEngine._mix_clips → ce_mix_clips (n,512) f32")


def _hook_direction_fade(engine):
    """P5a: _compute_parallel_fixture_gains → C（分组数据缓存命中, 数学全 C）。

    ease_lut 非 None 时回退原版（LUT 插值）；transitions_optimized=False 时
    过渡 = smoothstep（curve=3, strategy=0）与 _clip_transition_gains 语义一致。
    """
    from src.engine import playback_engine as pe

    orig = pe.PlaybackEngine._compute_parallel_fixture_gains
    CURVE_CODE = {"linear": 0, "easein": 1, "easeout": 2, "easeinout": 3, "bezier": 4}
    STRAT_CODE = {"standard_fade": 0, "immediate": 1, "decelerate": 2,
                  "hold_decay": 3, "step_down": 4, "smooth_ease": 5,
                  "delay_then_cut": 6}

    def ce_parallel(self, clip, local_fixtures, fade_in, fade_out, duration,
                    local_t, in_direction, out_direction, groups,
                    is_fading_in, is_fading_out, track=None, proj=None,
                    seq_delay_in=0.0, seq_delay_out=0.0, ease_lut=None,
                    fade_in_scale=1.0, fade_out_scale=1.0):
        _hc = getattr(self, "_ce_hook_counts", None)
        if _hc is None:
            # L6: 不含 "t4" 残留键（t4 是 EffectEngine 计数, 此 dict 永不
            # 自增 → sweep 跨对象 update 合并会以 0 覆盖 ee 的真实计数）
            _hc = {"p5a": 0, "p5b": 0}
            self._ce_hook_counts = _hc
        _hc["p5a"] += 1
        if getattr(engine, "ce_parallel_fixture_gains", None) is None:
            # M3: 内核缺失（DLL 未导出）→ 显式回退原版（避免 None 调用炸渲染链）
            return orig(self, clip, local_fixtures, fade_in, fade_out, duration,
                        local_t, in_direction, out_direction, groups,
                        is_fading_in, is_fading_out, track=track, proj=proj,
                        seq_delay_in=seq_delay_in, seq_delay_out=seq_delay_out,
                        ease_lut=ease_lut, fade_in_scale=fade_in_scale,
                        fade_out_scale=fade_out_scale)
        if ease_lut is not None:
            return orig(self, clip, local_fixtures, fade_in, fade_out, duration,
                        local_t, in_direction, out_direction, groups,
                        is_fading_in, is_fading_out, track=track, proj=proj,
                        seq_delay_in=seq_delay_in, seq_delay_out=seq_delay_out,
                        ease_lut=ease_lut, fade_in_scale=fade_in_scale,
                        fade_out_scale=fade_out_scale)
        K = len(local_fixtures or [])
        if K <= 0:
            return np.ones(0, dtype=np.float32)
        pos_in = sizes_in = pos_out = sizes_out = None
        if is_fading_in:
            _gi, pos_in, sizes_in = self._sequential_fixture_group_data(
                clip, local_fixtures, in_direction, groups, track=track, proj=proj)
        if is_fading_out:
            _go, pos_out, sizes_out = self._sequential_fixture_group_data(
                clip, local_fixtures, out_direction, groups, track=track, proj=proj)
        # M6 守卫: 分组数组为空/短于 K → C 内核按 K 越界读（UB）→ 回退原版
        if not _ce_gain_arrays_ok(K, pos_in, sizes_in, pos_out, sizes_out):
            return orig(self, clip, local_fixtures, fade_in, fade_out, duration,
                        local_t, in_direction, out_direction, groups,
                        is_fading_in, is_fading_out, track=track, proj=proj,
                        seq_delay_in=seq_delay_in, seq_delay_out=seq_delay_out,
                        ease_lut=ease_lut, fade_in_scale=fade_in_scale,
                        fade_out_scale=fade_out_scale)
        if not self._clip_transition_enabled(clip):
            curve, strategy = 3, 0
        else:
            curve = CURVE_CODE.get(
                self._clip_transition_curve(clip, fade_in=True).strip().lower(), 0)
            strategy = STRAT_CODE.get(
                self._clip_transition_strategy(clip, fade_in=True).strip().lower(), 0)
        gains = np.empty(K, np.float32)
        try:
            engine.ce_parallel_fixture_gains(
                (np.ascontiguousarray(pos_in, np.float32).ctypes.data_as(ctypes.POINTER(ctypes.c_float))
                 if pos_in is not None else None),
                (np.ascontiguousarray(sizes_in, np.float32).ctypes.data_as(ctypes.POINTER(ctypes.c_float))
                 if sizes_in is not None else None),
                (np.ascontiguousarray(pos_out, np.float32).ctypes.data_as(ctypes.POINTER(ctypes.c_float))
                 if pos_out is not None else None),
                (np.ascontiguousarray(sizes_out, np.float32).ctypes.data_as(ctypes.POINTER(ctypes.c_float))
                 if sizes_out is not None else None),
                K, float(fade_in), float(fade_out), float(duration), float(local_t),
                1 if is_fading_in else 0, 1 if is_fading_out else 0,
                float(seq_delay_in), float(seq_delay_out),
                float(fade_in_scale), float(fade_out_scale),
                curve, strategy, gains.ctypes.data_as(ctypes.POINTER(ctypes.c_float)))
        except Exception:
            # H1: C 内核异常 → 失败计数（异常照常上抛, 语义不变; 计数暴露
            # 静默回退, 探针断言 ==0）
            _CE_EXC_CNT[0] += 1
            raise
        return gains

    pe.PlaybackEngine._compute_parallel_fixture_gains = wrap_parallel_library(ce_parallel, engine)
    log.info("[CE] P5a 已接入: _compute_parallel_fixture_gains → ce_parallel_fixture_gains")

    # ── P5b: 顺序分组增益 → C ──────────────────────────────────────
    orig_seq = pe.PlaybackEngine._compute_sequential_fixture_gains

    def ce_sequential(self, clip, local_fixtures, fade_in, fade_out, duration,
                      local_t, in_direction, out_direction, groups, group_pause,
                      single_duration, is_fading_in, is_fading_out,
                      track=None, proj=None, seq_delay_in=0.0, seq_delay_out=0.0,
                      ease_lut=None, fade_in_scale=1.0, fade_out_scale=1.0,
                      stagger_override=None):
        _hc = getattr(self, "_ce_hook_counts", None)
        if _hc is None:
            _hc = {"p5a": 0, "p5b": 0}
            self._ce_hook_counts = _hc
        _hc["p5b"] += 1
        if getattr(engine, "ce_sequential_fixture_gains", None) is None:
            # M3: 内核缺失（DLL 未导出）→ 显式回退原版
            return orig_seq(self, clip, local_fixtures, fade_in, fade_out, duration,
                            local_t, in_direction, out_direction, groups, group_pause,
                            single_duration, is_fading_in, is_fading_out,
                            track=track, proj=proj, seq_delay_in=seq_delay_in,
                            seq_delay_out=seq_delay_out, ease_lut=ease_lut,
                            fade_in_scale=fade_in_scale, fade_out_scale=fade_out_scale)
        if ease_lut is not None:
            return orig_seq(self, clip, local_fixtures, fade_in, fade_out, duration,
                            local_t, in_direction, out_direction, groups, group_pause,
                            single_duration, is_fading_in, is_fading_out,
                            track=track, proj=proj, seq_delay_in=seq_delay_in,
                            seq_delay_out=seq_delay_out, ease_lut=ease_lut,
                            fade_in_scale=fade_in_scale, fade_out_scale=fade_out_scale)
        # ★★★ R156（用户："每个设备自己的单独的过渡柔和是靠过渡翼的长度决定的，
        #   现在…过渡翼没有应用到设备"）：C 内核 `ce_sequential_fixture_gains` 的
        #   淡入分支把**组内每台**的窗口按台数摊薄
        #   （`win = pg_in / gsize`，见 csrc/ce_engine.c:1066），于是 8 台一组时
        #   每台只有过渡翼的 1/8 —— 真工程 `d4381eb3`（过渡翼 1.192s）实测各台
        #   实际时长 1.00/0.74/0.56/0.38/0.20/0.10s，**越晚起步越被压缩**。
        #
        #   该内核在**另一个会话**维护的 DLL 里（本目录 DLL 属其产物），本会话不得
        #   改动它 → 在这里**让淡入分支走 Python 原版**（R156 已修正为"每台窗口 =
        #   过渡翼、不因组内名次摊薄"）。淡出分支与其它路径仍走 C（性能不受影响：
        #   带翼 clip 的淡入只在过渡窗口内命中，窗口外依旧直通）。
        if is_fading_in or stagger_override is not None:
            return orig_seq(self, clip, local_fixtures, fade_in, fade_out, duration,
                            local_t, in_direction, out_direction, groups, group_pause,
                            single_duration, is_fading_in, is_fading_out,
                            track=track, proj=proj, seq_delay_in=seq_delay_in,
                            seq_delay_out=seq_delay_out, ease_lut=ease_lut,
                            fade_in_scale=fade_in_scale, fade_out_scale=fade_out_scale,
                            stagger_override=stagger_override)
        K = len(local_fixtures or [])
        if K <= 0:
            return np.ones(0, dtype=np.float32)
        # per_group / batch_count / pauses / offsets（Python 轻量预计算）
        #
        # ★★ R154 修正（2026-09-22，用户实报"淡入和延迟都没有真的生效…卡128"）：
        #   旧实现把 `per_group` **同时**当作"单台窗口"和"组起点间隔"用：
        #       batch_offsets = self._clip_sequential_offsets(per_group, pauses, batch_count)
        #   而 `per_group` 在启用扫掠时 = 过渡翼（单台窗口）。于是**延迟翼被完全忽略**，
        #   起点间隔变成"过渡翼"，后面的组被排到 far beyond clip 长度之外：
        #   真工程 `d4381eb3`（时长 3.913s）实测起点 =
        #       [0, 1.192, 2.383, 3.575, 4.767, 5.958, 7.15, 8.341]
        #   → 第 4 组起（3.575s）以后根本来不及启动 → 那些设备**停在上一个 clip 的
        #   末尾值（电机 128）**，正是用户看到的"卡 128"。
        #   Python 侧 R152 已把两个量拆开（`single`=过渡翼窗口、`step`=延迟翼错峰），
        #   但本 hook 自己重算了一遍 prelude、没跟着改 → 修复没进到真实运行路径。
        #   现与 Python 侧同源：错峰步长单独取 `_clip_sequential_timing_detail[5]`。
        #
        # ★★★ R178（2026-09-28）：prelude 不再手抄，**直接调 Python 原版 helper**：
        #   · `_seq_window_and_step`（R152/R158：启用扫掠时单台窗口 = 存储的单组时长）；
        #   · `_sequential_global_gain`（R157：分组窗口已完成淡出时**不再叠整体淡出**）。
        #   旧手抄版漏同步 R157 → C 路径淡出仍是"四组之外又多一个整体大过渡"
        #   （示例工程 调用级 A/B：C 0.9945 vs Python 1.0，渲染 341 帧 u13-u28 不一致）。
        #   本分支 is_fading_in 恒为 False（淡入已在上方回退原版）→ skip_in=False。
        _stagger, per_group = pe._seq_window_and_step(
            self, clip, single_duration, seq_in=seq_delay_in, seq_out=seq_delay_out,
            fade_in=fade_in, fade_out=fade_out, duration=duration, override=None)
        batch_count = self._clip_sequential_batch_count(clip, groups)
        pauses = self._clip_sequential_pauses(clip, groups, group_pause)
        batch_offsets = np.asarray(
            self._clip_sequential_offsets(_stagger, pauses, batch_count),
            dtype=np.float32)
        _out_covered = bool(is_fading_out and (fade_out > 1e-4 or seq_delay_out > 1e-4))
        global_gain = pe._sequential_global_gain(
            self, clip, local_t, skip_in=False, skip_out=_out_covered)
        # ★ R154：淡出跨度同淡入公式（错峰间隔×波次 + 单台窗口 + 停顿）
        out_span = max(1e-6, float(_stagger) * max(0, batch_count - 1)
                       + per_group + float(sum(pauses)))
        gi_in = gp_in = gs_in = gi_out = gp_out = gs_out = None
        if is_fading_in:
            gi_in, gp_in, gs_in = self._sequential_fixture_group_data(
                clip, local_fixtures, in_direction, groups, track=track, proj=proj)
        if is_fading_out and (fade_out > 1e-4 or seq_delay_out > 1e-4):
            gi_out, gp_out, gs_out = self._sequential_fixture_group_data(
                clip, local_fixtures, out_direction, groups, track=track, proj=proj)
        # M6 守卫: 分组数组为空/短于 K 或 batch_offsets 不足 batch_count →
        # C 内核越界读（UB）→ 回退原版
        if (not _ce_gain_arrays_ok(K, gi_in, gp_in, gs_in, gi_out, gp_out, gs_out)
                or batch_offsets.size < batch_count):
            return orig_seq(self, clip, local_fixtures, fade_in, fade_out, duration,
                            local_t, in_direction, out_direction, groups, group_pause,
                            single_duration, is_fading_in, is_fading_out,
                            track=track, proj=proj, seq_delay_in=seq_delay_in,
                            seq_delay_out=seq_delay_out, ease_lut=ease_lut,
                            fade_in_scale=fade_in_scale, fade_out_scale=fade_out_scale)
        if not self._clip_transition_enabled(clip):
            curve, strategy = 3, 0
        else:
            curve = CURVE_CODE.get(
                self._clip_transition_curve(clip, fade_in=True).strip().lower(), 0)
            strategy = STRAT_CODE.get(
                self._clip_transition_strategy(clip, fade_in=True).strip().lower(), 0)
        _F = ctypes.POINTER(ctypes.c_float)
        gains = np.empty(K, np.float32)
        try:
            engine.ce_sequential_fixture_gains(
                (np.ascontiguousarray(gi_in, np.float32).ctypes.data_as(_F) if gi_in is not None else None),
                (np.ascontiguousarray(gp_in, np.float32).ctypes.data_as(_F) if gp_in is not None else None),
                (np.ascontiguousarray(gs_in, np.float32).ctypes.data_as(_F) if gs_in is not None else None),
                (np.ascontiguousarray(gi_out, np.float32).ctypes.data_as(_F) if gi_out is not None else None),
                (np.ascontiguousarray(gp_out, np.float32).ctypes.data_as(_F) if gp_out is not None else None),
                (np.ascontiguousarray(gs_out, np.float32).ctypes.data_as(_F) if gs_out is not None else None),
                np.ascontiguousarray(batch_offsets, np.float32).ctypes.data_as(_F),
                int(batch_count), K,
                float(per_group), float(global_gain), float(out_span),
                float(fade_in), float(fade_out), float(duration), float(local_t),
                1 if is_fading_in else 0, 1 if is_fading_out else 0,
                float(seq_delay_in), float(seq_delay_out),
                float(fade_in_scale), float(fade_out_scale),
                curve, strategy, gains.ctypes.data_as(_F))
        except Exception:
            # H1: C 内核异常 → 失败计数（异常照常上抛, 语义不变）
            _CE_EXC_CNT[0] += 1
            raise
        return gains

    pe.PlaybackEngine._compute_sequential_fixture_gains = ce_sequential
    log.info("[CE] P5b 已接入: _compute_sequential_fixture_gains → ce_sequential_fixture_gains")

    # ── T3: _mix_one_universe → ce_full_mix（direct 模式; soft 回退）──
    from src.network import network_manager as nm_mod
    orig_mix_one = nm_mod.NetworkManager._mix_one_universe
    _BASE_N = ("fader", "programmer", "tester", "linker")
    _SHAPE_N = ("effect", "effect_auto", "matrix", "matrix_audio", "audio", "joystick")
    _CLIP_N = ("timeline", "clip_playback", "clip_preview")

    def ce_mix_one(self, snapshots, uni, update_anchor=True, exclude_ltp_editor_layers=None,
                   return_clip_mask=False):
        if return_clip_mask:
            # bd0ebca(EDGE-05 回放冻结按通道拆分)为原版 _mix_one_universe
            # 新增 return_clip_mask —— C 内核 ce_full_mix 不产出 clip_win
            # (按通道接管掩码), mask 路径显式回退原版(逐字节不变);
            # 否则 TypeError 被 flush 层 except 吞掉 → 该域输出静默丢失。
            return orig_mix_one(self, snapshots, uni, update_anchor=update_anchor,
                                exclude_ltp_editor_layers=exclude_ltp_editor_layers,
                                return_clip_mask=return_clip_mask)
        if getattr(engine, "ce_full_mix", None) is None:
            # M3: 内核缺失（DLL 未导出）→ 显式回退原版
            return orig_mix_one(self, snapshots, uni, update_anchor=update_anchor,
                                exclude_ltp_editor_layers=exclude_ltp_editor_layers)
        # ce_full_mix has no per-layer gains; reuse the original scene fade mixer.
        if self._shape_blend_mode != "direct" or self._layer_gains:
            return orig_mix_one(self, snapshots, uni, update_anchor=update_anchor,
                                exclude_ltp_editor_layers=exclude_ltp_editor_layers)
        _mt = getattr(self, "_ce_mix_timing", None)
        if os.environ.get("QASCADE_CE_TIMING", "0") == "1":
            if _mt is None:
                _mt = {"n": 0, "sum": 0.0}
                self._ce_mix_timing = _mt
            _t0 = time.perf_counter()
        else:
            _mt = None
            _t0 = 0.0
        try:
            _F8 = ctypes.POINTER(ctypes.c_uint8)
            _FD = ctypes.POINTER(ctypes.c_double)
            _FF = ctypes.POINTER(ctypes.c_float)
            C = ctypes
            # 层指针缓存（T5: 每帧 73 域 × 13 层 × 2 的 ascontiguousarray+data_as ≈ 2ms/帧;
            # 层数据对象不变时指针总指向最新内存（原地写也正确）→ 对象判等后复用。
            # 非连续/非 u8 数组 → ascontiguousarray 拷贝出新对象 → 判等失败 → 重算（与旧路径一致）。
            _ptrc = getattr(self, "_ce_layer_ptr_cache", None)
            if _ptrc is None:
                _ptrc = {}
                self._ce_layer_ptr_cache = _ptrc
            # 组装层指针（NULL 表示层缺席）
            # T5-2: snapshots 参数本就是 _layers 引用的拷贝（flush/_mix_and_send_locked
            # 均为 snapshots[name][uni] = layer.get(uni)）→ 直接查 _layers 完全等价，
            # 省每帧 13×73 次 dict 查询链 + snapshots 构建。三扇门验证等价。
            # P9（stage C）: 异步 flush 传入捕获帧快照（snapshots ≠ 当前 _layers）——
            # 优先读 snapshots 参数（串行路径 snapshots[name] ≡ _layers[name], 不变;
            # 异步路径 = 该帧捕获输入, 字节序保持）。
            _layers = getattr(self, "_layers", {}) or {}
            def _layer(entries, name):
                e = entries.get(uni) if entries is not None else _layers.get(name, {}).get(uni)
                if e is None:
                    return (None, None)
                d, a = e
                ckey = (name, uni)
                c = _ptrc.get(ckey)
                if c is not None and c[2] is d and c[3] is a:
                    return (c[0], c[1])
                dp = np.ascontiguousarray(d, np.uint8).ctypes.data_as(_F8)
                ap = np.ascontiguousarray(a, np.uint8).ctypes.data_as(_F8)
                _ptrc[ckey] = (dp, ap, d, a)
                return (dp, ap)
            bd = []; ba = []; sd = []; sa = []; cd = []; ca = []
            for n in _BASE_N:
                d, a = _layer(snapshots.get(n) if snapshots is not None else None, n)
                bd.append(d); ba.append(a)
            for n in _SHAPE_N:
                d, a = _layer(snapshots.get(n) if snapshots is not None else None, n)
                sd.append(d); sa.append(a)
            for n in _CLIP_N:
                d, a = _layer(snapshots.get(n) if snapshots is not None else None, n)
                cd.append(d); ca.append(a)
            # LTP 时间戳（13 层: base4 + shape6 + clip3）——指针缓存（对象判等）
            ltp_ptrs = []
            for n in _BASE_N + _SHAPE_N + _CLIP_N:
                t = self._ltp_editor_layer_time.get(n, {}).get(uni)
                if t is None:
                    ltp_ptrs.append(None)
                    continue
                ck = ("ltp", n, uni)
                c = _ptrc.get(ck)
                if c is not None and c[1] is t:
                    ltp_ptrs.append(c[0])
                    continue
                tp = np.ascontiguousarray(t, np.float64).ctypes.data_as(_FD)
                _ptrc[ck] = (tp, t)
                ltp_ptrs.append(tp)
            def_entry = self._channel_defaults.get(int(uni))
            def_data = def_active = None
            has_def = 0
            if def_entry is not None:
                ck = ("def", uni)
                c = _ptrc.get(ck)
                if c is not None and c[2] is def_entry:
                    def_data, def_active = c[0], c[1]
                else:
                    def_data = np.ascontiguousarray(def_entry[0], np.uint8).ctypes.data_as(_F8)
                    def_active = np.ascontiguousarray(def_entry[1], np.uint8).ctypes.data_as(_F8)
                    _ptrc[ck] = (def_data, def_active, def_entry)
                has_def = 1
            anchor = self._direct_anchor_by_universe.get(int(uni), self._shape_default_anchor)
            if anchor is not None:
                ck = ("anchor", uni)
                c = _ptrc.get(ck)
                if c is not None and c[1] is anchor:
                    anchor_ptr = c[0]
                else:
                    anchor_ptr = np.ascontiguousarray(anchor, np.float32).ctypes.data_as(_FF)
                    _ptrc[ck] = (anchor_ptr, anchor)
            else:
                anchor_ptr = None
            clip_t = self._ltp_clip_time.get(uni)
            if clip_t is not None:
                ck = ("clip_t", uni)
                c = _ptrc.get(ck)
                if c is not None and c[1] is clip_t:
                    clip_t_ptr = c[0]
                else:
                    clip_t_ptr = np.ascontiguousarray(clip_t, np.float64).ctypes.data_as(_FD)
                    _ptrc[ck] = (clip_t_ptr, clip_t)
            else:
                clip_t_ptr = None
            editor_t = self._editor_ltp_time_for_mix(uni, exclude_ltp_editor_layers)
            if editor_t is not None:
                ck = ("editor_t", uni)
                c = _ptrc.get(ck)
                if c is not None and c[1] is editor_t:
                    editor_t_ptr = c[0]
                else:
                    editor_t_ptr = np.ascontiguousarray(editor_t, np.float64).ctypes.data_as(_FD)
                    _ptrc[ck] = (editor_t_ptr, editor_t)
            else:
                editor_t_ptr = None
            ck = ("out", uni)
            c = _ptrc.get(ck)
            if c is None or c[0] is None:
                _ptrc[ck] = (np.empty(512, np.uint8), None)
            out = _ptrc[ck][0]
            # ★ R93 修（真实 bug, 实测复现）: 上面这个 `out` 是**逐域复用的共享
            #   缓冲**（`_ptrc[("out", uni)]["0"]`）, 而原版 `_mix_one_universe`
            #   返回的是**当次新建**的数组（`out.astype(np.uint8)`）。
            #   只调一次看不出差别; 但同一域被**连续取两次快照**时（T12 关联器的
            #   `_get_snapshot` 主快照 + 停放判定用的 motion 快照 = 同一批 universe
            #   的两次 `_mix_one_universe`）, 第二次混音会**就地覆盖第一次返回的
            #   数组** → 第一次的结果静默变成第二次的值（实测: fader 通道在 motion
            #   快照取完后从 108/103/25 变 0/0/0, 关联器读到全 0 → CE 开/关输出
            #   53 个通道不一致）。
            #   这里在返回前拷一份（等价于原版的"当次新建"语义; 512B 拷贝 ≈0.2µs,
            #   相对 C 混音本体 2~30µs 可忽略）; 内部仍复用同一缓冲供内核写入。
            # T5-3 修复: (C.POINTER(_F8)*N) 的元素类型是双层指针——含真实指针时
            # 数组构建/传参必然 TypeError → ce_mix_one 自 T3 起全走 Python 回退,
            # K2 C 化从未生效! 正确: 元素类型 _F8(单层) + cast 为 LP_LP_c_ubyte。
            if _mt is not None:
                _c0 = time.perf_counter()
            _T3_MIX_CNT[0] += 1
            engine.ce_full_mix(
                C.cast((_F8 * 4)(*bd), C.POINTER(_F8)),
                C.cast((_F8 * 4)(*ba), C.POINTER(_F8)),
                C.cast((_F8 * 6)(*sd), C.POINTER(_F8)),
                C.cast((_F8 * 6)(*sa), C.POINTER(_F8)),
                C.cast((_F8 * 3)(*cd), C.POINTER(_F8)),
                C.cast((_F8 * 3)(*ca), C.POINTER(_F8)),
                C.cast((_FD * 13)(*ltp_ptrs), C.POINTER(_FD)), 13,
                def_data, def_active, has_def,
                float(self._shape_gain),
                anchor_ptr, clip_t_ptr, editor_t_ptr,
                out.ctypes.data_as(_F8))
            # 见上方 R93 注释: 拷出「当次结果」再返回（共享 scrub 缓冲留给下一域/下一次）
            out_ret = out.copy()
            if _mt is not None:
                _dt = time.perf_counter() - _t0
                _mt["sum"] += _dt
                _mt["c"] = _mt.get("c", 0.0) + (time.perf_counter() - _c0)
                _mt["n"] += 1
            return out_ret
        except Exception as _e:
            _CE_EXC_CNT[0] += 1   # H1: C 内核异常 → 失败计数（回退原版）
            if os.environ.get("QASCADE_CE_TIMING", "0") == "1":
                import traceback as _tb
                print(f"[CE-MIX-EXC] uni={uni} {type(_e).__name__}: {_e}", flush=True)
                _tb.print_exc(limit=3)
            return orig_mix_one(self, snapshots, uni, update_anchor=update_anchor,
                                exclude_ltp_editor_layers=exclude_ltp_editor_layers)

    nm_mod.NetworkManager._mix_one_universe = ce_mix_one
    log.info("[CE] T3 已接入: _mix_one_universe → ce_full_mix（direct; soft 回退）")

    # ── T4: EffectEngine compute_at wave 分支 → C ────────────────────
    from src.ui import effect_engine as ee_mod
    orig_compute_at = ee_mod.EffectEngine.compute_at
    _WAVE_IDX = {"sine": 0, "square": 1, "sawtooth": 2, "triangle": 3,
                 "random": 4, "cosine": 5, "bounce": 6, "expo": 7}

    # ── D1: 参数包络绑定求值 → C 内核（未绑定快路径保留 Python）────────
    # _ce_env_arrays / _ce_env_python_ref 已提为模块级（bench 探针直引）;
    # _ce_env_arrays 含 mode=1 控制点 >256 回退守卫（M7）。
    _PD = ctypes.POINTER(ctypes.c_double)
    _PIPE_AB = os.environ.get("QASCADE_CE_PIPELINE_AB", "0") == "1"

    def _ce_apply_param_envelopes(self, preset_id, t, params):
        """D1 包络绑定求值 → ce_param_envelope_map（未绑定快路径保留 Python）。

        单条绑定管线（原版 :1204-1231）: norm=value_at(t) → clamp → 线性映射
        lo/hi → int 参数银行家舍入。C 内核 fuzz 10 万次位级一致（ce_fuzz_env.py）;
        控制点提取失败/内核异常 → 该绑定回退原版求值（字节不变）。
        """
        try:
            from src.ui.param_envelope import get_param_envelope_store
            store = get_param_envelope_store()
            bindings = store.get_bindings(str(preset_id or ""))
        except Exception:
            return params
        if not bindings:
            return params
        preset = self._presets.get(preset_id)
        spec_by_key = {}
        if preset is not None:
            for sp in preset.params:
                spec_by_key[getattr(sp, "key", None)] = sp
        out = dict(params)
        for param_key, env_id in bindings.items():
            env = store.get(env_id)
            if env is None:
                continue
            sp = spec_by_key.get(param_key)
            if sp is not None:
                lo = float(getattr(sp, "min_val", 0.0))
                hi = float(getattr(sp, "max_val", 1.0))
                try:
                    rng = store.get_binding_range(str(preset_id or ""), param_key)
                except Exception:
                    rng = None
                if rng is not None:
                    r_lo, r_hi = rng
                    if r_lo is not None:
                        lo = float(r_lo)
                    if r_hi is not None:
                        hi = float(r_hi)
                is_int = 1 if str(getattr(sp, "type", "")) == "int" else 0
                mode = 1
                curve = getattr(env, "curve", None)
                if curve is not None and hasattr(curve, "evaluate"):
                    mode = 0
                xs, ys = _ce_env_arrays(env, mode)
                if xs is not None:
                    try:
                        val = engine.ce_param_envelope_map(
                            float(t),
                            float(getattr(env, "duration", 4.0) or 4.0),
                            xs.ctypes.data_as(_PD), ys.ctypes.data_as(_PD),
                            int(xs.size), mode, lo, hi, is_int)
                        _hc = getattr(self, "_ce_hook_counts", None)
                        if _hc is not None:
                            _hc["env"] = _hc.get("env", 0) + 1
                        if _PIPE_AB:
                            ref = _ce_env_python_ref(env, t, lo, hi, is_int)
                            if float(val) != ref:
                                print(f"[AB-DIFF] envelope {preset_id} "
                                      f"{param_key} C={val} py={ref}",
                                      flush=True)
                        if is_int:
                            val = int(val)
                        out[param_key] = val
                        continue
                    except Exception:
                        _CE_EXC_CNT[0] += 1   # H1: D1 包络 C 内核异常 → 失败计数
                        pass
            # 回退: 原版单绑定求值（异常 → 跳过该绑定, 同原版）
            try:
                norm = float(env.value_at(t))
            except Exception:
                continue
            norm = max(0.0, min(1.0, norm))
            if sp is not None:
                val = lo + norm * (hi - lo)
                if str(getattr(sp, "type", "")) == "int":
                    val = int(round(val))
                out[param_key] = val
            else:
                out[param_key] = norm
        return out

    def ce_compute_at(self, preset_id, t, params, fixtures, fixture_defs,
                      positions, fps, sort_mode, base_order_ids):
        # W1 GIL 活锁修复（2026-08-10 实证）: effect 线程 Python 持锁段
        # ~180µs < 5ms switch interval → 从不主动让出 GIL; 早期 sleep(0)/
        # ctypes 的释放窗口仅 ~1-10µs, 远小于 tick 被唤醒的 condvar 延迟
        # 10-60µs → tick 永久抢不回 GIL（静默 0 帧/150s; CPU 铁证: effect
        # 98s vs tick 3.6s; 真并发复现 tick 499 帧后饿死）。sleep(1ms) 的
        # 释放窗口 >> 唤醒延迟 → tick 可公平竞争（实证: S1/S2/S3 全 PASS
        # + 逐帧哈希一致 + 门1 73d9e1c2 不变 + 422 pytest）。代价: 每次
        # compute_at +1ms（真实 UI 25ms/帧周期无感, 探针 effect 吞吐
        # 117k→7k iters/30s 仅调度占比变化, 不改数值）。
        time.sleep(0.001)
        _hc = getattr(self, "_ce_hook_counts", None)
        if _hc is None:
            # L6: 不含 p5a/p5b 残留键（PlaybackEngine 计数, 此 dict 永不
            # 自增 → sweep 跨对象合并会以 0 覆盖 eng 的真实计数）
            _hc = {"t4": 0,
                   "t4_chase": 0, "t4_fader": 0, "t4_audio_wave": 0,
                   "t4_v29": 0, "t4_grad": 0, "t4_color": 0,
                   "t4_ramp": 0, "t4_fancue": 0, "env": 0,
                   "t4_cur_scalar": 0, "t4_cur_env": 0, "t4_cur_chase": 0,
                   "t4_cur_rand": 0, "t4_cur_breathe": 0, "t4_cur_color": 0,
                   "t4_cur_randrun": 0, "t4_cur_varyspeed": 0,
                   "t4_cur_fcdc": 0,
                   "t4_rainbow": 0, "t4_dual": 0}
            self._ce_hook_counts = _hc
        pid = str(preset_id or "")
        # ★ R118 方向镜像同步（2026-09-26 CE 对齐修复）: 原版 compute_at 入口
        #   （effect_engine.py:3231）effective_params 把推进族预设
        #   （PHASE_ADVANCE_PRESETS, 含 chase_strobe_random/wave_* 全体）的
        #   _app_direction 镜像后才走排序链/内核; 本 hook 副本早于 R118
        #   （aac7cc2 2026-08-03 < 173fb3e 2026-09-20）从未同步 → 带方向时
        #   _ce_sorted_fx 用未镜像方向, 设备链序与原版相反（对齐门禁
        #   ce_probe_compute_at_ab.py 26 差异实锤: chase_strobe_random 25 +
        #   wave_expo 1）。此处复刻同款入口镜像（同一 ee_mod 真源, 非推进族
        #   返回原对象零拷贝零回归）; 回退路径传 _params_unmirrored —— 原版
        #   入口会再做一次镜像, 镜像非对合（reverse→ltr→rtl）, 传镜像值会
        #   二次翻转破坏字节等价。
        _params_unmirrored = params
        params = ee_mod.effective_params(pid, params)
        if pid.startswith("wave_") and pid[5:] in _WAVE_IDX:
            _hc["t4"] += 1
            try:
                params2 = _ce_apply_param_envelopes(self, pid, t, params)
                if (not _ce_gated(params2)
                        and not _ce_matrix_proj_active(params2, positions,
                                                       sort_mode)):
                    return _ce_wave_compute(self, pid, t, params2,
                                            fixtures, positions,
                                            sort_mode, base_order_ids)
            except _CeFallback:
                # H1: 重复 orig_i（虚拟单元/逻辑设备 merge-max 语义）→ C 无法
                # 逐位复刻 → 直接回退原版（手写重算缺 apply_spatial_scope/
                # apply_effect_direction/包络参数/merge-max → 静默错值）
                return orig_compute_at(self, preset_id, t, _params_unmirrored,
                                       fixtures, fixture_defs, positions, fps,
                                       sort_mode, base_order_ids)
            except Exception as _e:
                _CE_EXC_CNT[0] += 1   # H1: T4 wave C 内核异常 → 失败计数
                if os.environ.get("QASCADE_CE_TIMING", "0") == "1":
                    print(f"[CE-WAVE-EXC] {type(_e).__name__}: {_e}", flush=True)
            if _ce_per_shape_gated(params):
                # 477181a per_shape 时钟同步: 原版逐单元块执行（单元内 seq 从
                # 0 重启、n=m）, 扁平 C 内核（ce_wave_effect/ce_phase_spread,
                # seq 0..n-1）不复刻 → 回退原版（字节不变）。_ce_gated 已拦
                # _ce_wave_compute, 此处拦本直跑回退路径（phase_spread 分支的
                # _ce_spread_ok 未含 per_shape, 同样须拦）。
                # ★ 2026-08-05 T5b: per_shape + phase_spread>0 → 逐块 C 内核
                #   ce_phase_spread_blocks（块内 k/denom 同构, 位级一致, 复用
                #   _per_shape_unit_blocks 缓存）; 无 spread 或门控不满足 →
                #   原回退不变。
                try:
                    _ps_spread = max(0.0, min(1.0,
                        float(params.get("phase_spread", 0.0) or 0.0)))
                except Exception:
                    _ps_spread = 0.0
                if _ps_spread > 1e-9:
                    _ps_ch = str(params.get("ch_type", "dimmer") or "dimmer")
                    _ps_p2 = _ce_apply_param_envelopes(self, pid, t, params)
                    if (_ce_spread_ok(_ps_p2, allow_per_shape=True)
                            and not _ce_matrix_proj_active(_ps_p2, positions,
                                                           sort_mode)):
                        _ps_sfx = _ce_sorted_fx(self, fixtures, positions,
                                                sort_mode, base_order_ids,
                                                _ps_p2)
                        if _ps_sfx and _ce_spread_unique_orig(_ps_sfx):
                            _ps_blocks = self._per_shape_unit_blocks(
                                _ps_sfx, _ps_p2)
                            if _ps_blocks:
                                _ps_n = len(_ps_sfx)
                                _ps_meta = {}
                                for _b in _ps_blocks:
                                    _m = len(_b)
                                    for _k, (_oi, _fi) in enumerate(_b):
                                        _ps_meta[str(_fi.id)] = (_k, _m)
                                if all(str(_fi.id) in _ps_meta
                                       for _oi, _fi in _ps_sfx):
                                    _ps_within = np.empty(_ps_n, np.int32)
                                    _ps_mi = np.empty(_ps_n, np.int32)
                                    for _i, (_oi, _fi) in enumerate(_ps_sfx):
                                        _km = _ps_meta[str(_fi.id)]
                                        _ps_within[_i] = _km[0]
                                        _ps_mi[_i] = _km[1]
                                    _ps_buf = np.empty(_ps_n, np.uint8)
                                    try:
                                        engine.ce_phase_spread_blocks(
                                            float(t), _ps_spread,
                                            float(_ps_p2.get("speed", 1.0)),
                                            float(_ps_p2.get("phase_step",
                                                             0.1)),
                                            int(_ps_p2.get("min", 0)),
                                            int(_ps_p2.get("max", 255)),
                                            _WAVE_IDX[pid[5:]], _ps_n,
                                            _ps_within.ctypes.data_as(
                                                ctypes.POINTER(
                                                    ctypes.c_int32)),
                                            _ps_mi.ctypes.data_as(
                                                ctypes.POINTER(
                                                    ctypes.c_int32)),
                                            _ps_buf.ctypes.data_as(
                                                ctypes.POINTER(
                                                    ctypes.c_uint8)))
                                    except Exception:
                                        # H1: C 内核异常 → 失败计数
                                        _CE_EXC_CNT[0] += 1
                                        raise
                                    _ps_ef = ee_mod.EffectFrame(t=float(t))
                                    _ps_max_i = 0
                                    for _oi, _fi in _ps_sfx:
                                        if int(_oi) > _ps_max_i:
                                            _ps_max_i = int(_oi)
                                    _ps_values = [{} for _ in
                                                  range(_ps_max_i + 1)]
                                    for _i, (_oi, _fi) in enumerate(_ps_sfx):
                                        _ps_values[int(_oi)][_ps_ch] = int(
                                            _ps_buf[_i])
                                    _ps_ef.values = _ps_values
                                    _hc["phase_spread_blocks"] = (
                                        _hc.get("phase_spread_blocks", 0) + 1)
                                    return _ps_ef
                return orig_compute_at(self, preset_id, t, _params_unmirrored,
                                       fixtures, fixture_defs, positions, fps,
                                       sort_mode, base_order_ids)
            # ── H3: phase_spread>0 → ce_phase_spread（原版 _compute_unit_values
            #    :821 O(N²) 虚拟单元时间相位层 → C 对角求值）。门控在
            #    _ce_spread_ok 内重查（wings/block/scope/width/xy/joy/per_shape）;
            #    read_indices[k]=k 语义（sorted 后 orig_i 恒等）; 21689ea 的
            #    _matrix_coords 全局矩阵投影生效时不复刻 → 一律回退原版
            #    （Python O(N²)/虚拟广播路径, 字节不变）。──
            ch = str(params.get("ch_type", "dimmer") or "dimmer")
            try:
                phase_spread = max(0.0, min(1.0,
                    float(params.get("phase_spread", 0.0) or 0.0)))
            except Exception:
                phase_spread = 0.0
            if phase_spread > 1e-9:
                params2 = _ce_apply_param_envelopes(self, pid, t, params)
                if (_ce_spread_ok(params2)
                        and not _ce_matrix_proj_active(params2, positions,
                                                       sort_mode)):
                    sfx = _ce_sorted_fx(self, fixtures, positions,
                                        sort_mode, base_order_ids, params2)
                    if _ce_spread_identity(sfx):
                        n = len(sfx)
                        buf = np.empty(n, np.uint8)
                        try:
                            engine.ce_phase_spread(
                                float(t), phase_spread,
                                float(params2.get("speed", 1.0)),
                                float(params2.get("phase_step", 0.1)),
                                int(params2.get("min", 0)),
                                int(params2.get("max", 255)),
                                _WAVE_IDX[pid[5:]], n,
                                buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)))
                        except Exception:
                            # H1: C 内核异常 → 失败计数（异常照常上抛, 语义不变）
                            _CE_EXC_CNT[0] += 1
                            raise
                        ef = ee_mod.EffectFrame(t=float(t))
                        max_i = 0
                        for i, (orig_i, _fi) in enumerate(sfx):
                            if int(orig_i) > max_i:
                                max_i = int(orig_i)
                        values = [{} for _ in range(max_i + 1)]
                        for i, (orig_i, _fi) in enumerate(sfx):
                            values[int(orig_i)][ch] = int(buf[i])
                        ef.values = values
                        _hc["phase_spread"] = _hc.get("phase_spread", 0) + 1
                        return ef
            # 门控命中/矩阵投影生效/身份不恒等 → 回退原版（下方最终
            # return orig_compute_at）。RC3: 旧直跑路径（门控命中仍跑
            # ce_wave_effect 静默错值, efb0fe2 修复被 08-04 凌晨 merge
            # 复活）已整体删除。
        # ── T4续: chase/fader/audio_wave 分支 → C ─────────────────────
        # 仅 hook「已注册」的 preset id（compute_at 可达, 字节等价可对拍）;
        # 未 hook（死代码, 注册函数从未被调用——hook 会让 compute_at 从 None
        # 变成真帧, 破坏字节等价, 同 _register_matrix_presets 理由）:
        #   joystick_circle/figure8/scan/wave_scan  (_register_joystick_presets)
        #   audio_bpm_flash/chase/wave, audio_energy, audio_color_beat,
        #   audio_section                         (_register_audio_presets)
        if pid in _CHASE_MODE:
            _hc["t4_chase"] += 1
            try:
                params2 = _ce_apply_param_envelopes(self, pid, t, params)
                if not _ce_gated(params2):
                    return _ce_chase_compute(self, pid, t, params2, fixtures,
                                             positions, sort_mode,
                                             base_order_ids)
            except _CeFallback:
                pass
            except Exception as _e:
                _CE_EXC_CNT[0] += 1   # H1: T4 chase C 内核异常 → 失败计数
                if os.environ.get("QASCADE_CE_TIMING", "0") == "1":
                    print(f"[CE-CHASE-EXC] {type(_e).__name__}: {_e}", flush=True)
        if pid in _FADER_KIND:
            _hc["t4_fader"] += 1
            try:
                params2 = _ce_apply_param_envelopes(self, pid, t, params)
                if not _ce_gated(params2):
                    return _ce_fader_compute(self, pid, t, params2, fixtures,
                                             positions, sort_mode,
                                             base_order_ids)
            except _CeFallback:
                pass
            except Exception as _e:
                _CE_EXC_CNT[0] += 1   # H1: T4 fader C 内核异常 → 失败计数
                if os.environ.get("QASCADE_CE_TIMING", "0") == "1":
                    print(f"[CE-FADER-EXC] {type(_e).__name__}: {_e}", flush=True)
        if pid in _AUDIO_WAVE_KIND:
            _hc["t4_audio_wave"] += 1
            try:
                params2 = _ce_apply_param_envelopes(self, pid, t, params)
                if not _ce_gated(params2):
                    return _ce_audio_wave_compute(self, pid, t, params2,
                                                  fixtures, positions,
                                                  sort_mode, base_order_ids)
            except _CeFallback:
                pass
            except Exception as _e:
                _CE_EXC_CNT[0] += 1   # H1: T4 audio_wave C 内核异常 → 失败计数
                if os.environ.get("QASCADE_CE_TIMING", "0") == "1":
                    print(f"[CE-AUDIO-WAVE-EXC] {type(_e).__name__}: {_e}",
                          flush=True)
        if pid in _V29_SHAPE_IDX:
            _hc["t4_v29"] += 1
            try:
                params2 = _ce_apply_param_envelopes(self, pid, t, params)
                if not _ce_gated(params2):
                    return _ce_v29_compute(self, pid, t, params2,
                                           fixtures, positions, sort_mode,
                                           base_order_ids)
            except _CeFallback:
                pass
            except Exception as _e:
                _CE_EXC_CNT[0] += 1   # H1: T4 v29 C 内核异常 → 失败计数
                if os.environ.get("QASCADE_CE_TIMING", "0") == "1":
                    print(f"[CE-V29-EXC] {type(_e).__name__}: {_e}",
                          flush=True)
        # ── T4续2: gradient/color_gradient/ramp/fancue 族 → C ────────
        # 仅 hook「已注册」的 preset id（同 chase/fader 纪律）; 族表见
        # _GRAD_KIND/_COLOR_GRAD_KIND/_RAMP_KIND/_FAN_CUE_KIND（fuzz 参考
        # ce_fuzz_curve2.py / ce_fuzz_curve3.py, 各 ≥10 万次位级一致）。
        # 2026-08-13: 补活性守卫 pid in self._compute_fns（此前注释声称
        # 该纪律但代码缺守卫——R8 剪除 color_gradient_rainbow 后族表仍含
        # 该 id, hook 让死预设复活出帧, 门禁 AB 1019 差异实锤）。
        if pid in _GRAD_KIND and pid in self._compute_fns:
            _hc["t4_grad"] += 1
            try:
                params2 = _ce_apply_param_envelopes(self, pid, t, params)
                if not _ce_gated(params2):
                    return _ce_grad_compute(self, pid, t, params2,
                                            fixtures, positions, sort_mode,
                                            base_order_ids)
            except _CeFallback:
                pass
            except Exception as _e:
                _CE_EXC_CNT[0] += 1   # H1: T4 grad C 内核异常 → 失败计数
                if os.environ.get("QASCADE_CE_TIMING", "0") == "1":
                    print(f"[CE-GRAD-EXC] {type(_e).__name__}: {_e}",
                          flush=True)
        if pid in _COLOR_GRAD_KIND and pid in self._compute_fns:
            _hc["t4_color"] += 1
            try:
                params2 = _ce_apply_param_envelopes(self, pid, t, params)
                if not _ce_gated(params2):
                    return _ce_color_grad_compute(self, pid, t, params2,
                                                  fixtures, positions,
                                                  sort_mode, base_order_ids)
            except _CeFallback:
                pass
            except Exception as _e:
                _CE_EXC_CNT[0] += 1   # H1: T4 color C 内核异常 → 失败计数
                if os.environ.get("QASCADE_CE_TIMING", "0") == "1":
                    print(f"[CE-COLOR-EXC] {type(_e).__name__}: {_e}",
                          flush=True)
        if pid in _RAMP_KIND and pid in self._compute_fns:
            _hc["t4_ramp"] += 1
            try:
                params2 = _ce_apply_param_envelopes(self, pid, t, params)
                if not _ce_gated(params2):
                    return _ce_ramp_compute(self, pid, t, params2,
                                            fixtures, positions, sort_mode,
                                            base_order_ids)
            except _CeFallback:
                pass
            except Exception as _e:
                _CE_EXC_CNT[0] += 1   # H1: T4 ramp C 内核异常 → 失败计数
                if os.environ.get("QASCADE_CE_TIMING", "0") == "1":
                    print(f"[CE-RAMP-EXC] {type(_e).__name__}: {_e}",
                          flush=True)
        if pid in _FAN_CUE_KIND and pid in self._compute_fns:
            _hc["t4_fancue"] += 1
            try:
                params2 = _ce_apply_param_envelopes(self, pid, t, params)
                if not _ce_gated(params2):
                    return _ce_fan_cue_compute(self, pid, t, params2,
                                               fixtures, positions,
                                               sort_mode, base_order_ids)
            except _CeFallback:
                pass
            except Exception as _e:
                _CE_EXC_CNT[0] += 1   # H1: T4 fancue C 内核异常 → 失败计数
                if os.environ.get("QASCADE_CE_TIMING", "0") == "1":
                    print(f"[CE-FANCUE-EXC] {type(_e).__name__}: {_e}",
                          flush=True)
        # ── T4续3: 精炼库 6 内核 + wave_rainbow/wave_dual（239 preset id）──
        # 仅 hook「已注册」的 preset id（compute_at 可达）; 死代码 id
        # （pumptex 8 个: 精炼库注册但 _compute_fns 无 → compute_at 恒
        # None）放行原版, 字节等价不破坏。族表由 build_families() 惰性
        # 构建（install 时 src 可能未就绪 → try/except 兜底空表 = 不 hook）。
        _cf = _ce_cur_families()
        _ce = _cf.get(pid) if _cf else None
        if _ce is not None and pid in self._compute_fns:
            _kernel, _variant = _ce
            _try = None
            if _kernel == "scalar":
                _shape = _CUR_SHAPE_IDX.get(_variant)
                if _shape is not None:
                    _hc["t4_cur_scalar"] += 1
                    _try = _ce_cur_scalar_compute
            elif _kernel == "env":
                _kind = _CUR_ENV_KIND.get(_variant)
                if _kind is not None:
                    _hc["t4_cur_env"] += 1
                    _try = _ce_cur_env_compute
            elif _kernel == "chase":
                _kind = _CUR_CHASE_KIND.get(_variant)
                if _kind is not None:
                    _hc["t4_cur_chase"] += 1
                    _try = _ce_cur_chase_compute
            elif _kernel == "rand":
                _kind = _CUR_RAND_KIND.get(_variant)
                if _kind is not None:
                    _hc["t4_cur_rand"] += 1
                    _try = _ce_cur_rand_compute
            elif _kernel == "breathe":
                _kind = _CUR_BREATHE_KIND.get(_variant)
                if _kind is not None:
                    _hc["t4_cur_breathe"] += 1
                    _try = _ce_cur_breathe_compute
            elif _kernel == "color":
                _kind = _CUR_COLOR_KIND.get(_variant)
                if _kind is not None:
                    _hc["t4_cur_color"] += 1
                    _try = _ce_cur_color_compute
            elif _kernel == "randrun":
                _kind = _CUR_RANDRUN_KIND.get(_variant)
                if _kind is not None:
                    _hc["t4_cur_randrun"] += 1
                    _try = _ce_cur_randrun_compute
            elif _kernel == "varyspeed":
                _kind = _CUR_VSPEED_KIND.get(_variant)
                if _kind is not None:
                    _hc["t4_cur_varyspeed"] += 1
                    _try = _ce_cur_varyspeed_compute
            elif _kernel == "foldcascade":
                _kind = _CUR_FCDC_KIND.get(_variant)
                if _kind is not None:
                    _hc["t4_cur_fcdc"] += 1
                    _try = _ce_cur_fcdc_compute
            if _try is not None:
                try:
                    params2 = _ce_apply_param_envelopes(self, pid, t, params)
                    if not _ce_gated(params2):
                        return _try(self, pid, t, params2, fixtures,
                                    positions, sort_mode, base_order_ids,
                                    _variant)
                except _CeFallback:
                    pass
                except Exception as _e:
                    _CE_EXC_CNT[0] += 1   # H1: T4 cur_* C 内核异常 → 失败计数
                    if os.environ.get("QASCADE_CE_TIMING", "0") == "1":
                        print(f"[CE-CUR-EXC] {pid} {type(_e).__name__}: {_e}",
                              flush=True)
        if pid == "wave_rainbow" and pid in self._compute_fns:
            _hc["t4_rainbow"] += 1
            try:
                params2 = _ce_apply_param_envelopes(self, pid, t, params)
                if (not _ce_gated(params2)
                        and not _ce_matrix_proj_active(params2, positions,
                                                       sort_mode)):
                    return _ce_rainbow_compute(self, pid, t, params2,
                                               fixtures, positions,
                                               sort_mode, base_order_ids)
            except _CeFallback:
                pass
            except Exception as _e:
                _CE_EXC_CNT[0] += 1   # H1: T4 rainbow C 内核异常 → 失败计数
                if os.environ.get("QASCADE_CE_TIMING", "0") == "1":
                    print(f"[CE-RAINBOW-EXC] {type(_e).__name__}: {_e}",
                          flush=True)
        if pid == "wave_dual" and pid in self._compute_fns:
            _hc["t4_dual"] += 1
            try:
                params2 = _ce_apply_param_envelopes(self, pid, t, params)
                if (not _ce_gated(params2)
                        and not _ce_matrix_proj_active(params2, positions,
                                                       sort_mode)):
                    return _ce_dual_compute(self, pid, t, params2,
                                            fixtures, positions,
                                            sort_mode, base_order_ids)
            except _CeFallback:
                pass
            except Exception as _e:
                _CE_EXC_CNT[0] += 1   # H1: T4 dual C 内核异常 → 失败计数
                if os.environ.get("QASCADE_CE_TIMING", "0") == "1":
                    print(f"[CE-DUAL-EXC] {type(_e).__name__}: {_e}",
                          flush=True)
        return orig_compute_at(self, preset_id, t, _params_unmirrored,
                               fixtures, fixture_defs, positions, fps,
                               sort_mode, base_order_ids)

    ee_mod.EffectEngine.compute_at = ce_compute_at
    log.info("[CE] T4 已接入: compute_at wave 分支 → ce_wave_effect")

    # ── T4续: chase/fader/audio_wave 分支的族表与辅助函数 ───────────────
    _CHASE_MODE = {"chase_single": 0, "chase_triple": 1, "chase_dual": 2,
                   "chase_color": 3, "chase_strobe_random": 4,
                   "chase_strobe_global": 5, "chase_alternate": 6,
                   "chase_fade_tail": 7, "valve_sequential": 9,
                   "motor_chase_target": 10}
    _FADER_KIND = {"fader_linear": 0, "fader_sequential": 1,
                   "fader_breath": 2, "fader_curve": 3}
    _AUDIO_WAVE_KIND = {"audio_beat_fader": 2}
    _CURVE_IDX = {"Linear": 0, "EaseIn": 1, "EaseOut": 2, "EaseInOut": 3,
                  "Bounce": 4, "Elastic": 5}
    # T4续3: 精炼库(cur_*) 族表变体 → C 内核索引（对齐 ce_engine.c 各 case 序）
    _CUR_SHAPE_IDX = {"sine": 0, "tri": 1, "saw_up": 2, "saw_down": 3,
                      "square": 4, "pulse": 5, "expo": 6, "log": 7,
                      "bounce": 8, "fold": 9, "notch": 10, "twin": 11,
                      "trapz": 12, "gauss": 13, "rectsin": 14, "skewtri": 15,
                      "dpulse": 16, "flattop": 17, "stairsin": 18}
    _CUR_EASE_IDX = {"linear": 0, "smooth": 1, "in": 2, "out": 3}
    _CUR_ENV_KIND = {"adsr": 0, "sh": 1, "step": 2, "ramp_hold": 3,
                     "slew": 4, "pluck": 5, "swell": 6, "multistrike": 7}
    _CUR_CHASE_KIND = {"comet": 0, "dot": 1, "scan": 2, "wipe": 3,
                       "wave-saw-tool": 4, "bounce": 5, "marquee": 6,
                       "kitt": 7, "build": 8, "dissolve": 9, "jumpseq": 10,
                       "collide": 11, "multihead": 12, "levelbar": 13,
                       "mirror": 14}
    _CUR_DIR_IDX = {"正向": 0, "反向": 1, "中心向外": 2, "两侧向中": 3}
    _CUR_RAND_KIND = {"sparkle": 0, "twinkle": 1, "jitter": 2, "perlin": 3,
                      "popcorn": 4, "glitch": 5, "lightning": 6, "ripple": 7}
    _CUR_BREATHE_KIND = {"breathe": 0, "heartbeat": 1, "tide": 2}
    _CUR_COLOR_KIND = {"rainbow": 0, "hue_rot": 1, "colortemp": 2, "plasma": 3,
                       "perlin_color": 4, "fire": 5, "warmcool": 6,
                       "dualtone": 7, "tricolor": 8, "colorpulse": 9,
                       "colorwave": 10, "starfield": 11, "sparkles": 12,
                       "ocean": 13}
    # R172 W2b 三族新效果 kind 枚举（全量对齐 src/ui/effect_presets_fam_data
    # + E1 扩容 fam_data2 —— R173 精编后在库 58 条 + 下架 v1 旧 id 16 条
    # (legacy_fam_ids, compute-only 兼容) —— 缺一即派发不命中静默回退,
    # tests/test_ce_families_r172.py 抓）。R173 删除的 E1 kind 不重编号, 留空号。
    _CUR_RANDRUN_KIND = {"sparkwalk": 0, "hopall": 1, "dualrace": 2,
                         "tritrace": 3, "edgehop": 4, "centerhop": 5,
                         "shower": 6, "fireflyd": 7, "pulsepoint": 8,
                         "sidehop": 9, "runjetrand": 10, "flamespark": 11,
                         "twinklerand": 12, "fallstar": 13, "grandhop": 14,
                         # E1 扩容（kind 15-30 = C _ce_randrun_rr2 分发序）
                         "dualzone": 15, "shufwalk": 16,
                         "siteripple": 18, "burst": 20,
                         "converge": 22,
                         "parit": 24, "denswave": 25,
                         "wavewalk": 30}
    _CUR_VSPEED_KIND = {"wave_sine": 0, "wave_tri": 1, "wave_saw": 2,
                        "chase_comet": 3, "chase_dot": 4, "chase_kitt": 5,
                        "breathe_soft": 6, "breathe_heart": 7,
                        "breathe_tide": 8, "twinkle_drift": 9,
                        "twinkle_grid": 10, "pumpwave": 11,
                        "pumpwave_tide": 12, "arch": 13, "arch_wide": 14,
                        "pendulum": 15, "pendulum_soft": 16,
                        "windmill": 17, "windmill_dual": 18,
                        # E1 扩容（look 8-22 = C _ce_vs_look 扩容序）
                        "wavepkt": 22, "string": 23,
                        "marquee": 25, "levelbar": 26, "buildup": 27,
                        "pluck": 28, "swell": 29, "stair": 30,
                        "fountain": 33}
    _CUR_FCDC_KIND = {"fold": 0, "slide": 1, "bounce": 2, "chasef": 3,
                      "surge": 4, "layer": 5, "arch": 6, "ripple": 7,
                      "fold3": 8, "bloomc": 9, "bloomedge": 10, "dual": 11,
                      "pingpong": 12,
                      # E1 扩容（kind 13-25 = C _ce_fc_fronts/_fc_device 序）
                      "trianchor": 13, "meetbloom": 14, "decay": 15,
                      "echofol": 17,
                      "elevator": 19, "trough": 20,
                      "wallpush": 22, "interleave": 23, "ringfold": 24,
                      "microbloom": 25}
    _I32P = ctypes.POINTER(ctypes.c_int32)

    def _ce_cur_families():
        """精炼库 preset id → (kernel, variant) 惰性族表（仅构建一次）。

        install 时 src 可能未就绪（打包/测试环境）→ try/except 兜底空表 =
        不 hook 任何 cur_* id（字节等价零风险）。hook 侧另加
        `pid in self._compute_fns` 守卫排除死代码 id（pumptex 8 个）。
        """
        if _CE_CUR_FAMILIES:
            return _CE_CUR_FAMILIES
        try:
            from src.ui.effect_presets_curated_data import build_families
            for _f in build_families():
                _CE_CUR_FAMILIES[_f["id"]] = (_f["kernel"], _f["variant"])
        except Exception:
            pass
        # R172 W2b/E1: 三族新效果（fam_data v1 + fam_data2 扩容, R173 精编后 58 条）
        # + R173 下架 v1 旧 id（compute-only 兼容, 旧快照/自定义效果仍走 C）
        try:
            from src.ui.effect_presets_fam_data import (
                build_fam_presets, legacy_fam_ids)
            for _f in build_fam_presets():
                _CE_CUR_FAMILIES[_f["id"]] = (_f["kernel"], _f["variant"])
            for _pid, _kern, _var in legacy_fam_ids():
                _CE_CUR_FAMILIES[_pid] = (_kern, _var)
        except Exception:
            pass
        return _CE_CUR_FAMILIES

    _CE_CUR_FAMILIES = {}

    # ── R172 W2b: 三族变体参数槽（单真源 = W2a 的 _RANDRUN_KINDS/_VS_KINDS/
    # _FC_KIND_CFG, 惰性归一化为 C 参数槽元组; 失败 → 空表 = 派发回退 Python
    # 字节安全）。kind 枚举序与 _CUR_<FAM>_KIND 一一对应（C 侧无变体分支表,
    # 变体差异全在本表解析出的参数/标志槽）。─────────────────────────────
    _CE_DMODE_IDX = {"跟随": 0, "交替": 1}          # 其余（含随机/未知）→ 2
    _CE_RR_BIAS_IDX = {"edge": 1, "center": 2}      # 其余 → 0
    _CE_VS_LOOK_IDX = {"wave": 0, "chase": 1, "breathe": 2, "twinkle": 3,
                       "pumpwave": 4, "arch": 5, "pendulum": 6, "windmill": 7,
                       # E1 扩容 look（对齐 C _ce_vs_look case 8-22; R173 删号留空）
                       "wavepkt": 11, "string": 12,
                       "marquee": 14, "levelbar": 15, "buildup": 16,
                       "pluck": 17, "swell": 18, "stair": 19,
                       "fountain": 22}
    _CE_VS_LV_IDX = {("chase", "comet"): 0, ("chase", "dot"): 1,
                     ("chase", "kitt"): 2, ("breathe", "breathe"): 0,
                     ("breathe", "heartbeat"): 1, ("breathe", "tide"): 2,
                     ("pumpwave", "wave"): 0, ("pumpwave", "tide"): 1,
                     ("pendulum", "std"): 0, ("pendulum", "soft"): 1,
                     ("windmill", "std"): 0, ("windmill", "dual"): 1}
    _CE_VS_SSHAPE_IDX = {"三角": 1, "阶梯": 2, "顿挫": 3, "呼吸": 4, "心跳": 5,
                         # E1 扩容两形状（6=单循环闭式; 7=游走累积表）
                         "单循环": 6, "游走": 7}
    _CE_FC_FRONT_IDX = {"exp": 0, "band": 1, "sharp": 2, "cos": 3}
    _CE_FC_HANDOFF_IDX = {"硬接": 0, "淡接": 1, "花开": 2, "回卷": 3}  # 其余→4
    _CE_FAM_CFG = {}

    def _ce_fam_cfg():
        """R172 三族 variant → C 参数槽（惰性单次; 键 "kernel|variant"）。

        randrun → (tick, rs, prox, gap, decay, dmode_idx, bias_idx,
                   soft, flick, slide, mirror, punch, heads)
        varyspeed → (look_idx, lv_idx)
        foldcascade → (front_idx, arch, ret)
        """
        if _CE_FAM_CFG:
            return _CE_FAM_CFG
        try:
            from src.ui import effect_presets_curated as _cur
            for _k, _c in _cur._RANDRUN_KINDS.items():
                _CE_FAM_CFG["randrun|" + _k] = (
                    int(_c.get("tick", 1)), int(_c.get("rs", 2)),
                    float(_c.get("prox", 0.6)), float(_c.get("gap", 0.5)),
                    float(_c.get("decay", 0.4)),
                    _CE_DMODE_IDX.get(_c.get("dmode", "随机"), 2),
                    _CE_RR_BIAS_IDX.get(_c.get("bias"), 0),
                    1 if _c.get("soft") else 0, 1 if _c.get("flick") else 0,
                    1 if _c.get("slide") else 0, 1 if _c.get("mirror") else 0,
                    1 if _c.get("punch") else 0, int(_c.get("heads", 1)))
            for _k, _lv in _cur._VS_KINDS.items():
                _look, _lvar = _lv
                _CE_FAM_CFG["varyspeed|" + _k] = (
                    _CE_VS_LOOK_IDX.get(_look, 0),
                    _CE_VS_LV_IDX.get((_look, _lvar), 0))
                if _look == "wave":   # R173: wave 载体默认 = kind 自身 look 变体
                    _CE_FAM_CFG["vswave|" + _k] = _lvar
            for _k, _c in _cur._FC_KIND_CFG.items():
                _CE_FAM_CFG["foldcascade|" + _k] = (
                    _CE_FC_FRONT_IDX.get(_c.get("front", "exp"), 0),
                    1 if _c.get("arch") else 0, 1 if _c.get("ret") else 0)
        except Exception:
            pass
        return _CE_FAM_CFG
    _P8 = ctypes.POINTER(ctypes.c_uint8)

    class _CeFallback(Exception):
        """C 路径无法逐位复刻的原版语义（重复 orig_i 等）→ 静默回退原版。"""

    def _ce_f_opt(p, key, default):
        """与 preset 函数体一致的参数提取（float; 坏值抛错 → 分支回退原版）。"""
        return float(p.get(key, default))

    def _ce_clamp01(x):
        """复刻 effect_presets_curated._clamp01 :28（R172 三族权值域）。"""
        return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)

    def _ce_i_opt(p, key, default):
        return int(p.get(key, default))

    def _ce_gate_f(p, key, default):
        """门控安全解析（复刻 _run_compute_fn 的 try/except → 默认 语义）。"""
        try:
            v = p.get(key, default)
            return default if v is None else float(v)
        except Exception:
            return default

    def _ce_gate_i(p, key, default):
        try:
            v = p.get(key, default)
            return default if v is None else int(v)
        except Exception:
            return default

    def _ce_per_shape_gated(p):
        """单元、TopView 空间组和工程组均有各自独立的时钟/序列。"""
        return ee_mod._order_execution.has_independent_groups(p)

    def _ce_subset_gated(p):
        """★★★ R169/R170：**子集 delay 相位** —— 扁平 C 内核不复刻 → 回退 Python 原版。

        用户实报："无论怎么选，子集状态调子集 delay 根本没变化，整个效果没变化"
        （要"电机子集奇数调 180 度，跟之前的一起形成交叉摆"）。

        真因：子集相位是在 `_run_compute_fn` 里**按子集分组重跑内核**实现的
        （`_subset_group_frames`：组内同一时间、组间相差 `subset_dt`；
        `_per_unit_frames`：逐单元 `t_k += 子集序号×dt`）。C 侧的
        `ce_wave_effect`/`ce_phase_spread` 只认 seq_i 相位，**根本没有子集概念** →
        参数设了也白设（输出与未开子集完全一致）。

        ★★ R170 判据（与 `effect_engine.subset_split_enabled` **同源**）：相位 ≠ 0
          且 TopView 有选中的子集。旧写法看 `subset_count > 1`（该参数已被用户否掉删除）。
          默认（相位 0 / 无子集）→ 本门恒 False，**C 路径逐字节不变**（零回归）。
        """
        try:
            deg = abs(float(p.get("subset_delay_phase", 0.0) or 0.0))
        except Exception:
            deg = 0.0
        if deg <= 1e-12:
            return False
        return bool(p.get("_subset_ids"))

    def _ce_gated(p):
        """原版 compute_at 链中 C 路径不复刻的特性 → True 时回退原版。"""
        if p.get("wing_mirror") or p.get("wing_reverse"):
            return True
        if (ee_mod._order_execution.requires_python_finish(p)
                or ee_mod._order_execution.per_shape_phase_enabled(p)):
            return True
        if _ce_per_shape_gated(p):
            return True
        if _ce_subset_gated(p):      # ★ R169 子集 delay 相位（C 内核不复刻）
            return True
        if p.get("joy_amplitude_x") is not None or p.get("joy_amplitude_y") is not None:
            return True
        if ee_mod.normalize_effect_application_scope(p.get("_app_scope")) != "both":
            return True
        # HOOK_T4_AUDIO §6: per_shape + _unit_groups → 原版按单元块独立跑 fn
        # （单元内 seq 重启/极角排序）; 扁平 C 内核复刻不了 → 回退原版。
        if (ee_mod.normalize_spatial_scope(p.get("spatial_scope")) == "per_shape"
                and p.get("_unit_groups")):
            return True
        if _ce_gate_f(p, "phase_spread", 0.0) > 1e-9:
            return True
        if _ce_gate_i(p, "wings", 1) > 1 or _ce_gate_i(p, "block_size", 1) > 1:
            return True
        if _ce_gate_f(p, "effect_width", 1.0) < 0.999:
            return True
        if (str(p.get("xy_envelope_source") or "").strip()
                and _ce_gate_f(p, "xy_envelope_gain", 0.0) > 1e-9):
            return True
        return False

    def _ce_matrix_proj_active(p, positions, sort_mode):
        """只在真实坐标相位路径回退；默认编号序保留 C 内核。"""
        return (sort_mode != "__route_animator__" and bool(positions)
                and ee_mod._order_execution.coordinate_phase_enabled(p))

    def _ce_spread_ok(p, allow_per_shape=False):
        """H3 ce_phase_spread 门控: _ce_gated 去掉 phase_spread 项 —— 直跑路径
        （wings/block=1, scope both, width 满, 无 xy 包络, 无摇杆幅度）条件;
        per_shape 单元（同 _ce_gated, 单元块语义不复刻）——allow_per_shape=True
        时放行（T5b ce_phase_spread_blocks 逐块内核, 位级一致）。"""
        if p.get("wing_mirror") or p.get("wing_reverse"):
            return False
        if (ee_mod._order_execution.requires_python_finish(p)
                or ee_mod._order_execution.per_shape_phase_enabled(p)):
            return False
        if ee_mod.normalize_effect_application_scope(p.get("_app_scope")) != "both":
            return False
        if not allow_per_shape and _ce_per_shape_gated(p):
            return False
        if _ce_gate_i(p, "wings", 1) > 1 or _ce_gate_i(p, "block_size", 1) > 1:
            return False
        if _ce_subset_gated(p):      # ★ R169 子集 delay 相位（本内核亦不复刻）
            return False
        if _ce_gate_f(p, "effect_width", 1.0) < 0.999:
            return False
        if (str(p.get("xy_envelope_source") or "").strip()
                and _ce_gate_f(p, "xy_envelope_gain", 0.0) > 1e-9):
            return False
        if p.get("joy_amplitude_x") is not None or p.get("joy_amplitude_y") is not None:
            return False
        return True

    def _ce_spread_unique_orig(sorted_fx):
        """per_shape 散写防歧义: orig_i 全唯一（重复时 C 链序散写 ≠ 原版块序
        散写, 回退原版）。"""
        seen = set()
        for orig_i, _fi in sorted_fx:
            if int(orig_i) in seen:
                return False
            seen.add(int(orig_i))
        return True

    def _ce_spread_identity(sorted_fx):
        """read_indices[k]=k 语义: 排序链后 orig_i 恒等（0..n-1）→ C 对角值
        直接对应各单元; 方向/聚块重排或稀疏设备集 → False 回退原版。"""
        for k, (orig_i, _fi) in enumerate(sorted_fx):
            if int(orig_i) != k:
                return False
        return True

    def _ce_sorted_fx(self, fixtures, positions, sort_mode, base_order_ids, p):
        """与 Python 共用编号、跳过编号、组内方向及内容失效缓存。"""
        return ee_mod._order_execution.ordered_fixtures(
            self, fixtures, positions, sort_mode=sort_mode,
            base_order_ids=base_order_ids, params=p)

    def _ce_make_ef(t, sorted_fx, bufs, chan_specs):
        """wave 分支同款 max_i 模式散点; chan_specs=[(buf_idx, ch), ...]。"""
        ef = ee_mod.EffectFrame(t=float(t))
        max_i = 0
        for orig_i, _fi in sorted_fx:
            if int(orig_i) > max_i:
                max_i = int(orig_i)
        values = [{} for _ in range(max_i + 1)]
        for i, (orig_i, _fi) in enumerate(sorted_fx):
            d = values[int(orig_i)]
            for b_idx, ch in chan_specs:
                d[ch] = int(bufs[b_idx][i])
        ef.values = values
        return ef

    def _ce_check_unique(sorted_fx):
        """重复 orig_i → 原版走虚拟单元合并路径（merge max 语义）→ 回退原版。"""
        seen = set()
        for orig_i, _fi in sorted_fx:
            oi = int(orig_i)
            if oi in seen:
                raise _CeFallback()
            seen.add(oi)

    def _ce_chase_compute(self, pid, t, p, fixtures, positions,
                          sort_mode, base_order_ids):
        mode = _CHASE_MODE[pid]
        sorted_fx = _ce_sorted_fx(self, fixtures, positions, sort_mode,
                                  base_order_ids, p)
        if not sorted_fx:
            return None
        _ce_check_unique(sorted_fx)
        n = len(sorted_fx)
        ch = str(p.get("ch_type", "dimmer") or "dimmer")
        lo = _ce_i_opt(p, "off_val", 0)
        hi = _ce_i_opt(p, "on_val", 255)
        bufs = (np.empty(n, np.uint8), np.empty(n, np.uint8),
                np.empty(n, np.uint8))
        ptrs = [b.ctypes.data_as(_P8) for b in bufs]
        if mode == 0:
            engine.ce_chase_effect(float(t), _ce_f_opt(p, "speed", 2.0),
                                   0.0, 0.0, mode, n, lo, hi, 0, 0, 0,
                                   0.0, 0.0, 0.0, 0, *ptrs)
            return _ce_make_ef(t, sorted_fx, bufs, [(0, ch)])
        if mode in (1, 2):
            engine.ce_chase_effect(
                float(t), _ce_f_opt(p, "speed", 2.0), 0.0, 0.0, mode, n,
                lo, hi, _ce_i_opt(p, "width", 3 if mode == 1 else 2),
                0, 0, 0.0, 0.0, 0.0, 0, *ptrs)
            return _ce_make_ef(t, sorted_fx, bufs, [(0, ch)])
        if mode == 3:
            engine.ce_chase_effect(float(t), _ce_f_opt(p, "speed", 1.5),
                                   0.0, 0.0, mode, n, 0, 0, 0, 0,
                                   _ce_i_opt(p, "n_colors", 3),
                                   0.0, 0.0, 0.0, 0, *ptrs)
            return _ce_make_ef(t, sorted_fx, bufs,
                               [(0, "R"), (1, "G"), (2, "B")])
        if mode == 4:
            engine.ce_chase_effect(float(t), 0.0, _ce_f_opt(p, "rate", 5.0),
                                   0.0, mode, n, lo, hi, 0, 0, 0,
                                   _ce_f_opt(p, "density", 0.3), 0.0, 0.0, 0,
                                   *ptrs)
            return _ce_make_ef(t, sorted_fx, bufs, [(0, ch)])
        if mode == 5:
            engine.ce_chase_effect(float(t), 0.0, _ce_f_opt(p, "rate", 8.0),
                                   0.0, mode, n, lo, hi, 0, 0, 0, 0.0,
                                   _ce_f_opt(p, "duty", 0.5), 0.0, 0, *ptrs)
            return _ce_make_ef(t, sorted_fx, bufs, [(0, ch)])
        if mode == 6:
            engine.ce_chase_effect(float(t), 0.0, _ce_f_opt(p, "rate", 4.0),
                                   0.0, mode, n, lo, hi, 0, 0, 0,
                                   0.0, 0.0, 0.0, 0, *ptrs)
            return _ce_make_ef(t, sorted_fx, bufs, [(0, ch)])
        if mode == 7:
            engine.ce_chase_effect(float(t), _ce_f_opt(p, "speed", 1.5),
                                   0.0, 0.0, mode, n, lo, hi, 0,
                                   _ce_i_opt(p, "tail", 4), 0, 0.0, 0.0, 0.0,
                                   0, *ptrs)
            return _ce_make_ef(t, sorted_fx, bufs, [(0, ch)])
        if mode == 9:
            engine.ce_chase_effect(
                float(t), 0.0, _ce_f_opt(p, "rate", 1.0), 0.0, mode, n,
                _ce_i_opt(p, "close_val", 0), _ce_i_opt(p, "open_val", 255),
                0, 0, 0, 0.0, 0.0, 0.0,
                1 if bool(p.get("overlap", False)) else 0, *ptrs)
            return _ce_make_ef(t, sorted_fx, bufs,
                               [(0, str(p.get("ch_type", "valve") or "valve"))])
        # mode 10: motor_chase_target（固定 motor_x/motor_y 双通道）
        engine.ce_chase_effect(float(t), _ce_f_opt(p, "speed", 0.5), 0.0,
                               _ce_f_opt(p, "delay", 0.1), mode, n,
                               0, 0, 0, 0, 0, 0.0, 0.0, 0.0, 0, *ptrs)
        return _ce_make_ef(t, sorted_fx, bufs,
                           [(0, "motor_x"), (1, "motor_y")])

    def _ce_fader_compute(self, pid, t, p, fixtures, positions,
                          sort_mode, base_order_ids):
        kind = _FADER_KIND[pid]
        sorted_fx = _ce_sorted_fx(self, fixtures, positions, sort_mode,
                                  base_order_ids, p)
        if not sorted_fx:
            return None
        _ce_check_unique(sorted_fx)
        n = len(sorted_fx)
        ch = str(p.get("ch_type", "dimmer") or "dimmer")
        val_a = _ce_i_opt(p, "val_a", 0)
        val_b = _ce_i_opt(p, "val_b", 255)
        buf = np.empty(n, np.uint8)
        ptr = buf.ctypes.data_as(_P8)
        if kind == 0:
            engine.ce_fader_effect(float(t), kind, 0, val_a, val_b,
                                   _ce_f_opt(p, "duration", 4.0),
                                   0.0, 0.0, 0.0, n, ptr)
        elif kind == 1:
            engine.ce_fader_effect(float(t), kind, 0, val_a, val_b,
                                   0.0, _ce_f_opt(p, "delay_per", 0.2),
                                   _ce_f_opt(p, "fade_time", 0.5), 0.0, n,
                                   ptr)
        elif kind == 2:  # breath: val_a=min(lo), val_b=max(hi)
            engine.ce_fader_effect(float(t), kind, 0,
                                   _ce_i_opt(p, "min", 10),
                                   _ce_i_opt(p, "max", 255),
                                   0.0, 0.0, 0.0,
                                   _ce_f_opt(p, "speed", 0.5), n, ptr)
        else:  # kind == 3: curve
            engine.ce_fader_effect(
                float(t), kind,
                _CURVE_IDX.get(p.get("curve", "EaseInOut"), 0),
                val_a, val_b, _ce_f_opt(p, "duration", 4.0),
                0.0, 0.0, 0.0, n, ptr)
        return _ce_make_ef(t, sorted_fx, (buf,), [(0, ch)])

    def _ce_audio_wave_compute(self, pid, t, p, fixtures, positions,
                               sort_mode, base_order_ids):
        # 唯一已注册的 kind2 预设: audio_beat_fader（对齐 _audio_beat_fader :3449）
        sorted_fx = _ce_sorted_fx(self, fixtures, positions, sort_mode,
                                  base_order_ids, p)
        if not sorted_fx:
            return None
        _ce_check_unique(sorted_fx)
        n = len(sorted_fx)
        ch = str(p.get("ch_type", "dimmer") or "dimmer")
        buf = np.empty(n, np.uint8)
        engine.ce_audio_wave_effect(
            float(t), _ce_f_opt(p, "bpm", 120.0), 2,
            0.0, _ce_f_opt(p, "offset_per", 0.0),
            _ce_f_opt(p, "decay", 4.0),
            float(_ce_i_opt(p, "base", 0)), float(_ce_i_opt(p, "peak", 255)),
            n, buf.ctypes.data_as(_P8))
        return _ce_make_ef(t, sorted_fx, (buf,), [(0, ch)])

    def _ce_wave_compute(self, pid, t, p, fixtures, positions,
                         sort_mode, base_order_ids):
        """wave_* 8 预设（对齐 _wave_generic :1444）: 全排序链 + 空集 None。

        speed/phase_step 用 _ce_f_opt（0/负值原样传入, 不再 or 默认吞 0）;
        min/max 用 _ce_i_opt（int() 截断, 同原版 int(params.get("min", 0))）;
        重复 orig_i/门控参数 → _CeFallback 回退原版（_run_compute_fn 虚拟单元
        merge-max 语义不复刻）。
        """
        sorted_fx = _ce_sorted_fx(self, fixtures, positions, sort_mode,
                                  base_order_ids, p)
        if not sorted_fx:
            return None
        _ce_check_unique(sorted_fx)
        n = len(sorted_fx)
        ch = str(p.get("ch_type", "dimmer") or "dimmer")
        lo = _ce_i_opt(p, "min", 0)
        hi = _ce_i_opt(p, "max", 255)
        buf = np.empty(n, np.uint8)
        engine.ce_wave_effect(
            float(t), _ce_f_opt(p, "speed", 1.0),
            _ce_f_opt(p, "phase_step", 0.1),
            float(lo), float(hi), _WAVE_IDX[pid[5:]],
            n, buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)))
        return _ce_make_ef(t, sorted_fx, (buf,), [(0, ch)])

    def _ce_v29_compute(self, pid, t, p, fixtures, positions,
                        sort_mode, base_order_ids):
        """v29 有状态矩阵（对齐 effect_presets_v29._make_shape_effect._fn :389
        + _render_groups :276, fuzz ce_fuzz_matrix_stateful.py 19.7 万帧零差异）:
          - ctx 按 (pid, cols, rows) 键管理 —— 同 Python _STATES
            f"{effect_id}_{cols}_{rows}"（rows = max(1, ceil(n/cols)),
            n 变化 → 新键新状态, 同 _get_state）; 生命周期进程级常驻, 对齐
            Python（src/ 无 _STATES 清理路径）。
          - 回退（_CeFallback → 原版）:
              重复 orig_i（虚拟单元 merge-max 语义不复刻）;
              lo ∉ [0,255]（C 的 (uint8_t)lo 初值回绕 ≠ Python 无限整型）;
              group_duration ≤ 0（t < start_t 时 Python ZeroDivisionError）;
              参数提取抛错（同 chase/fader 分支, 原版同样抛错 → 行为一致）。
        """
        sorted_fx = _ce_sorted_fx(self, fixtures, positions, sort_mode,
                                  base_order_ids, p)
        if not sorted_fx:
            return None
        _ce_check_unique(sorted_fx)
        n = len(sorted_fx)
        cols = max(1, _ce_i_opt(p, "cols", 4))
        rows = max(1, (n + cols - 1) // cols)
        key = (pid, cols, rows)
        ctx = _V29_CTX.get(key)
        if ctx is None:
            ctx = engine.ce_matrix_stateful_create(cols, n)
            if not ctx:
                raise _CeFallback()
            _V29_CTX[key] = ctx
        ch = p.get("ch_type", "dimmer")
        lo = _ce_i_opt(p, "lo", 0)
        hi = _ce_i_opt(p, "hi", 255)
        if not (0 <= lo <= 255):
            raise _CeFallback()
        group_duration = _ce_f_opt(p, "group_duration", 2.0)
        if group_duration <= 0.0:
            raise _CeFallback()
        buf = np.empty(n, np.uint8)
        engine.ce_matrix_stateful_apply(
            ctx, float(t), n, _V29_SHAPE_IDX[pid],
            max(1, _ce_i_opt(p, "max_groups", 3)),
            group_duration,
            _ce_f_opt(p, "trigger_bpm", 60.0),
            max(1, _ce_i_opt(p, "shape_size", 2)),
            lo, hi, buf.ctypes.data_as(_P8))
        return _ce_make_ef(t, sorted_fx, (buf,), [(0, ch)])

    # ── T4续2: gradient/color_gradient/ramp/fan_cue 族表与辅助函数 ───────
    # 参考实现: render_lab/bench/ce_fuzz_curve2.py（渐变 5 类×10 预设 +
    # 颜色渐变 4 类×4 预设）/ ce_fuzz_curve3.py（ramp 2 + fan_cue 2 预设）。
    # 每个内核 fuzz ≥10 万次位级一致; 本段只做「preset id → kind + 参数槽」
    # 映射（参数提取与预设函数体逐行同构）。
    #
    # 仅 hook「线上注册」的 id（同 chase/fader 纪律）: 以下 8 个 id 虽在
    # fuzz 覆盖集内, 但被精炼库剪除（effect_prune_list.PRUNE_IDS /
    # effect_audit_patch.EXTRA_PRUNE_IDS）——compute_at 恒 None, hook 会让
    # 其变成真帧 → 破坏字节等价 → 排除:
    #   gradient: fountain_height_gradient / light_gradient_breath_v28 /
    #             pump_gradient_rise_v28 / pump_multi_gradient_v31 /
    #             pump_stair_gradient_v31 / motor_multi_gradient_v32 /
    #             motor_stair_gradient_v32
    #   fan_cue:  motor_cue_speed_ramp_v36
    # （对应的 C 内核 kind 3/4 与 fan_cue kind 1 分支仍可整段复用, 如需
    # 恢复注册只需把 id 加回表内。）
    _GRAD_KIND = {
        "light_multi_gradient_v30": 0,
        "light_brightness_stair_v30": 1,
        "light_center_gradient_v30": 2,
    }
    _GRAD_SPEED_DEFAULT = {"light_center_gradient_v30": 1.0}
    _GRAD_STEPS_DEFAULT = {"light_brightness_stair_v30": 8.0}
    _COLOR_GRAD_KIND = {
        "color_gradient_rainbow": 0, "light_gradient_chase": 1,
        "light_color_gradient_wave_v26": 2, "light_color_matrix_gradient_v26": 3,
    }
    _COLOR_SPEED_DEFAULT = {1: 0.3, 3: 0.3}      # hue_chase / matrix2d
    _V26_PALETTE = {"rainbow": 0, "warm": 1, "cool": 2, "fire": 3}
    _RAMP_KIND = {"strobe_ramp": 0, "light_breath_curves_v25": 1}
    _V25_CURVE = {"sine": 0, "ease": 1, "exponential": 2, "square_breath": 3}
    _V25_DELAY = {"none": 0, "cascade": 1, "reverse": 2, "center": 3}
    _FAN_CUE_KIND = {"motor_fan_gradient_open_v35": 0}
    _V35_WAVE = {"正弦": 0, "三角": 1, "锯齿上": 2, "锯齿下": 3,
                 "弹跳": 4, "脉冲": 5}
    _P16 = ctypes.c_double * 16

    def _ce_grad_compute(self, pid, t, p, fixtures, positions,
                         sort_mode, base_order_ids):
        """gradient 族 3 预设（kind 0-2, ce_gradient_effect; v30 线上集）。

        a 参数按预设: multi=segments / stair=steps / center=0（未用）。
        kind 3（phase_breath）/4（direction_grad）对应预设已被精炼库剪除
        （见族表注释）——需要时按 fuzz 参考补齐参数槽即可。
        输出 C/Python 同为 int() 截断 + clamp 0..255 → 任意 lo/hi 域安全。
        """
        kind = _GRAD_KIND[pid]
        sorted_fx = _ce_sorted_fx(self, fixtures, positions, sort_mode,
                                  base_order_ids, p)
        if not sorted_fx:
            return None
        _ce_check_unique(sorted_fx)
        n = len(sorted_fx)
        if kind == 0:  # multi_gradient: segments
            a = float(max(2, _ce_i_opt(p, "segments", 4)))
            lo = _ce_i_opt(p, "lo", 0)
            hi = _ce_i_opt(p, "hi", 255)
        elif kind == 1:  # stair_gradient: steps
            a = float(max(2, _ce_i_opt(p, "steps",
                                       int(_GRAD_STEPS_DEFAULT.get(pid, 8.0)))))
            lo = _ce_i_opt(p, "lo", 0)
            hi = _ce_i_opt(p, "hi", 255)
        else:  # kind 2: center_gradient
            a = 0.0
            lo = _ce_i_opt(p, "lo", 0)
            hi = _ce_i_opt(p, "hi", 255)
        speed = _ce_f_opt(p, "speed", _GRAD_SPEED_DEFAULT.get(pid, 0.5))
        buf = np.empty(n, np.uint8)
        engine.ce_gradient_effect(kind, float(t), speed, a, lo, hi, n,
                                  buf.ctypes.data_as(_P8))
        ch = p.get("ch_type", "dimmer")
        return _ce_make_ef(t, sorted_fx, (buf,), [(0, ch)])

    def _ce_color_grad_compute(self, pid, t, p, fixtures, positions,
                               sort_mode, base_order_ids):
        """color_gradient 族 4 预设（kind 0-3, ce_color_gradient_effect）,
        R/G/B 三通道输出。p[3] 参数槽: rainbow=repeats/saturation/brightness,
        hue_chase=hue_range, wave=色板码, matrix2d=cols。"""
        kind = _COLOR_GRAD_KIND[pid]
        sorted_fx = _ce_sorted_fx(self, fixtures, positions, sort_mode,
                                  base_order_ids, p)
        if not sorted_fx:
            return None
        _ce_check_unique(sorted_fx)
        n = len(sorted_fx)
        speed = _ce_f_opt(p, "speed", _COLOR_SPEED_DEFAULT.get(kind, 0.5))
        if kind == 0:  # rainbow: repeats / saturation / brightness
            pp = [float(_ce_i_opt(p, "repeats", 1)),
                  _ce_f_opt(p, "saturation", 1.0),
                  _ce_f_opt(p, "brightness", 1.0)]
        elif kind == 1:  # hue_chase: hue_range
            pp = [_ce_f_opt(p, "hue_range", 1.0), 1.0, 1.0]
        elif kind == 2:  # wave: 色板码（未知 → rainbow）
            pp = [float(_V26_PALETTE.get(p.get("palette", "rainbow"), 0)),
                  1.0, 1.0]
        else:  # kind 3: matrix2d: cols
            pp = [float(max(1, _ce_i_opt(p, "cols", 4))), 1.0, 1.0]
        p_arr = (ctypes.c_double * 3)(*pp)
        buf_r = np.empty(n, np.uint8)
        buf_g = np.empty(n, np.uint8)
        buf_b = np.empty(n, np.uint8)
        engine.ce_color_gradient_effect(
            kind, float(t), speed, p_arr, n,
            buf_r.ctypes.data_as(_P8), buf_g.ctypes.data_as(_P8),
            buf_b.ctypes.data_as(_P8))
        return _ce_make_ef(t, sorted_fx, (buf_r, buf_g, buf_b),
                           [(0, "R"), (1, "G"), (2, "B")])

    def _ce_ramp_compute(self, pid, t, p, fixtures, positions,
                         sort_mode, base_order_ids):
        """ramp 族 2 预设（ce_ramp_effect）。

        kind 0 = strobe_ramp（全体同值, 无 clamp）: C 的 (uint8_t)hi/lo 按
        256 回绕 ≠ Python 无限整型原样写值 → lo/hi ∉ [0,255] 回退原版;
        period == 0.0 → Python t % 0.0 抛 ZeroDivisionError, C 返回全 0 →
        回退原版（异常路径一致）。
        """
        kind = _RAMP_KIND[pid]
        sorted_fx = _ce_sorted_fx(self, fixtures, positions, sort_mode,
                                  base_order_ids, p)
        if not sorted_fx:
            return None
        _ce_check_unique(sorted_fx)
        n = len(sorted_fx)
        if kind == 0:
            hi = _ce_i_opt(p, "hi", 255)
            lo = _ce_i_opt(p, "lo", 0)
            if not (0 <= hi <= 255 and 0 <= lo <= 255):
                raise _CeFallback()
            if _ce_f_opt(p, "period", 4.0) == 0.0:
                raise _CeFallback()
            pp = [_ce_f_opt(p, "freq_lo", 2.0), _ce_f_opt(p, "freq_hi", 20.0),
                  _ce_f_opt(p, "period", 4.0), float(hi), float(lo)]
        else:  # kind 1: light_breath_curves: speed / curve / delay_mode
            pp = [_ce_f_opt(p, "speed", 0.5),
                  float(_V25_CURVE.get(p.get("curve", "sine"), 0)),
                  float(_V25_DELAY.get(p.get("delay_mode", "none"), 0))]
        arr = _P16(*(list(pp) + [0.0] * (16 - len(pp))))
        buf = np.empty(n, np.uint8)
        engine.ce_ramp_effect(kind, float(t), arr, n,
                              buf.ctypes.data_as(_P8))
        ch = p.get("ch_type", "dimmer")
        return _ce_make_ef(t, sorted_fx, (buf,), [(0, ch)])

    def _ce_fan_cue_compute(self, pid, t, p, fixtures, positions,
                            sort_mode, base_order_ids):
        """fan_cue 族 1 预设（ce_fan_cue_effect kind 0 = motor_fan_gradient_open;
        参数槽 fans/speed/fan_angle/min_amp/max_amp/fan_phase_diff/wave 码）。
        kind 1（motor_cue_speed_ramp）对应预设已被精炼库剪除（见族表注释）。
        输出 C/Python 同为 int(round()) 半偶 + clamp 0..255 → 域安全。
        """
        kind = _FAN_CUE_KIND[pid]
        sorted_fx = _ce_sorted_fx(self, fixtures, positions, sort_mode,
                                  base_order_ids, p)
        if not sorted_fx:
            return None
        _ce_check_unique(sorted_fx)
        n = len(sorted_fx)
        pp = [float(_ce_i_opt(p, "fans", 4)),
              _ce_f_opt(p, "speed", 0.5),
              _ce_f_opt(p, "fan_angle", 60.0),
              _ce_f_opt(p, "min_amp", 0.1),
              _ce_f_opt(p, "max_amp", 1.0),
              _ce_f_opt(p, "fan_phase_diff", 0.0),
              float(_V35_WAVE.get(p.get("wave", "正弦"), 0))]
        arr = _P16(*(list(pp) + [0.0] * (16 - len(pp))))
        buf = np.empty(n, np.uint8)
        engine.ce_fan_cue_effect(kind, float(t), arr, n,
                                 buf.ctypes.data_as(_P8))
        ch = p.get("ch", "motor_x")
        return _ce_make_ef(t, sorted_fx, (buf,), [(0, ch)])

    # ── T4续3: 精炼库(cur_*) 6 内核 + wave_rainbow/wave_dual compute ──
    # 对齐 effect_presets_curated.py 各 kernel fn 的参数提取（lo/hi 用
    # _ce_i_opt int 截断, 其余 _ce_f_opt float）; 输出 int32 缓冲（原版
    # int() 直写, 不 clamp）→ _ce_make_ef int() 原样写回与 Python 逐位一致。
    def _ce_cur_scalar_compute(self, pid, t, p, fixtures, positions,
                               sort_mode, base_order_ids, variant):
        shape_idx = _CUR_SHAPE_IDX[variant]
        sorted_fx = _ce_sorted_fx(self, fixtures, positions, sort_mode,
                                  base_order_ids, p)
        if not sorted_fx:
            return None
        _ce_check_unique(sorted_fx)
        n = len(sorted_fx)
        ch = str(p.get("ch_type", "dimmer") or "dimmer")
        ease_idx = _CUR_EASE_IDX.get(str(p.get("ease", "linear") or "linear"),
                                     0)
        buf = np.empty(n, np.int32)
        engine.ce_cur_scalar(
            shape_idx, ease_idx, float(t),
            _ce_f_opt(p, "speed", 1.0), _ce_f_opt(p, "spatial_freq", 1.0),
            _ce_f_opt(p, "duty", 0.5),
            float(_ce_i_opt(p, "lo", 0)), float(_ce_i_opt(p, "hi", 255)),
            n, buf.ctypes.data_as(_I32P))
        return _ce_make_ef(t, sorted_fx, (buf,), [(0, ch)])

    def _ce_cur_env_compute(self, pid, t, p, fixtures, positions,
                            sort_mode, base_order_ids, variant):
        kind = _CUR_ENV_KIND[variant]
        sorted_fx = _ce_sorted_fx(self, fixtures, positions, sort_mode,
                                  base_order_ids, p)
        if not sorted_fx:
            return None
        _ce_check_unique(sorted_fx)
        n = len(sorted_fx)
        ch = str(p.get("ch_type", "dimmer") or "dimmer")
        buf = np.empty(n, np.int32)
        engine.ce_cur_env(
            kind, float(t),
            float(_ce_i_opt(p, "lo", 0)), float(_ce_i_opt(p, "hi", 255)),
            _ce_f_opt(p, "speed", 1.0),
            _ce_f_opt(p, "attack", 0.1), _ce_f_opt(p, "decay", 0.3),
            _ce_i_opt(p, "steps", 4), _ce_f_opt(p, "spread", 0.0),
            _ce_i_opt(p, "seed", 0), n, buf.ctypes.data_as(_I32P))
        return _ce_make_ef(t, sorted_fx, (buf,), [(0, ch)])

    def _ce_cur_chase_compute(self, pid, t, p, fixtures, positions,
                              sort_mode, base_order_ids, variant):
        kind = _CUR_CHASE_KIND[variant]
        dir_idx = _CUR_DIR_IDX.get(str(p.get("direction", "正向") or "正向"),
                                   0)
        sorted_fx = _ce_sorted_fx(self, fixtures, positions, sort_mode,
                                  base_order_ids, p)
        if not sorted_fx:
            return None
        _ce_check_unique(sorted_fx)
        n = len(sorted_fx)
        ch = str(p.get("ch_type", "dimmer") or "dimmer")
        buf = np.empty(n, np.int32)
        engine.ce_cur_chase(
            kind, dir_idx, float(t),
            float(_ce_i_opt(p, "lo", 0)), float(_ce_i_opt(p, "hi", 255)),
            _ce_f_opt(p, "speed", 1.0),
            _ce_f_opt(p, "width", 0.18), _ce_f_opt(p, "tail", 0.5),
            _ce_i_opt(p, "heads", 3), n, buf.ctypes.data_as(_I32P))
        return _ce_make_ef(t, sorted_fx, (buf,), [(0, ch)])

    def _ce_cur_rand_compute(self, pid, t, p, fixtures, positions,
                             sort_mode, base_order_ids, variant):
        kind = _CUR_RAND_KIND[variant]
        sorted_fx = _ce_sorted_fx(self, fixtures, positions, sort_mode,
                                  base_order_ids, p)
        if not sorted_fx:
            return None
        _ce_check_unique(sorted_fx)
        n = len(sorted_fx)
        ch = str(p.get("ch_type", "dimmer") or "dimmer")
        buf = np.empty(n, np.int32)
        engine.ce_cur_rand(
            kind, float(t),
            float(_ce_i_opt(p, "lo", 0)), float(_ce_i_opt(p, "hi", 255)),
            _ce_f_opt(p, "speed", 1.0), _ce_f_opt(p, "density", 0.3),
            _ce_i_opt(p, "seed", 0), n, buf.ctypes.data_as(_I32P))
        return _ce_make_ef(t, sorted_fx, (buf,), [(0, ch)])

    def _ce_cur_breathe_compute(self, pid, t, p, fixtures, positions,
                                sort_mode, base_order_ids, variant):
        kind = _CUR_BREATHE_KIND[variant]
        sorted_fx = _ce_sorted_fx(self, fixtures, positions, sort_mode,
                                  base_order_ids, p)
        if not sorted_fx:
            return None
        _ce_check_unique(sorted_fx)
        n = len(sorted_fx)
        ch = str(p.get("ch_type", "dimmer") or "dimmer")
        buf = np.empty(n, np.int32)
        engine.ce_cur_breathe(
            kind, float(t),
            float(_ce_i_opt(p, "lo", 0)), float(_ce_i_opt(p, "hi", 255)),
            _ce_f_opt(p, "speed", 0.5), _ce_f_opt(p, "spread", 0.0),
            n, buf.ctypes.data_as(_I32P))
        return _ce_make_ef(t, sorted_fx, (buf,), [(0, ch)])

    def _ce_cur_color_compute(self, pid, t, p, fixtures, positions,
                              sort_mode, base_order_ids, variant):
        kind = _CUR_COLOR_KIND[variant]
        sorted_fx = _ce_sorted_fx(self, fixtures, positions, sort_mode,
                                  base_order_ids, p)
        if not sorted_fx:
            return None
        _ce_check_unique(sorted_fx)
        n = len(sorted_fx)
        bufs = (np.empty(n, np.int32), np.empty(n, np.int32),
                np.empty(n, np.int32))
        engine.ce_cur_color(
            kind, float(t),
            _ce_f_opt(p, "speed", 0.3), _ce_f_opt(p, "saturation", 1.0),
            _ce_f_opt(p, "brightness", 1.0),
            _ce_f_opt(p, "spatial_freq", 1.0),
            _ce_f_opt(p, "spread", 0.0), _ce_i_opt(p, "seed", 0),
            _ce_f_opt(p, "k_lo", 2700.0), _ce_f_opt(p, "k_hi", 6500.0),
            _ce_f_opt(p, "hue_a", 0.0), _ce_f_opt(p, "hue_b", 0.55),
            n, bufs[0].ctypes.data_as(_I32P),
            bufs[1].ctypes.data_as(_I32P), bufs[2].ctypes.data_as(_I32P))
        return _ce_make_ef(t, sorted_fx, bufs, [(0, "R"), (1, "G"), (2, "B")])

    # ── R172 W2b: 三族新效果 compute（对齐 effect_presets_curated.py W2a 三
    # 内核 fn 的参数提取——lo/hi 用 _ce_i_opt int 截断, 其余 _ce_f_opt float;
    # 变体默认值来自 _ce_fam_cfg（W2a 单真源表）。输出 int32 缓冲（原版
    # int() 直写, 权值域 _clamp01 后无 clamp）→ _ce_make_ef 逐位写回。───
    def _ce_cur_randrun_compute(self, pid, t, p, fixtures, positions,
                                sort_mode, base_order_ids, variant):
        kind = _CUR_RANDRUN_KIND.get(variant)
        cfg = _ce_fam_cfg().get("randrun|" + variant)
        if kind is None or cfg is None:
            raise _CeFallback()
        (tick, rs_d, prox_d, gap_d, decay_d, dmode_d, bias, soft, flick,
         slide, mirror, punch, heads) = cfg
        sorted_fx = _ce_sorted_fx(self, fixtures, positions, sort_mode,
                                  base_order_ids, p)
        if not sorted_fx:
            return None
        _ce_check_unique(sorted_fx)
        n = len(sorted_fx)
        ch = str(p.get("ch_type", "dimmer") or "dimmer")
        speed = max(0.01, _ce_f_opt(p, "speed", 2.0))
        rate = speed * tick
        prox = _ce_clamp01(_ce_f_opt(p, "proximity", prox_d))
        gap = _ce_clamp01(_ce_f_opt(p, "phase_gap", gap_d))
        decay = _ce_clamp01(_ce_f_opt(p, "decay", decay_d))
        run_size = _ce_i_opt(p, "run_size", rs_d)
        seed = _ce_i_opt(p, "seed", 0)
        if "direction_mode" in p:
            dmode = _CE_DMODE_IDX.get(str(p.get("direction_mode")), 2)
        else:
            dmode = dmode_d
        # E1 扩容: kind ≥ 15 走 C RR2 分发（st.get 逐位语义）——
        # proximity 原值 + 在场标志（RR2 各 fn 的 st.get 默认值各异）;
        # density/mod_depth 仅 burst/denswave 读取（wrapper 已 _clamp01,
        # 同 Python _clamp01(float(st.get(默认)))）。
        has_prox = 1 if "proximity" in p else 0
        prox_raw = float(p["proximity"]) if has_prox else 0.0
        density = (_ce_clamp01(_ce_f_opt(p, "density", 0.6))
                   if kind == 20 else 0.0)
        mod_depth = (_ce_clamp01(_ce_f_opt(p, "mod_depth", 0.75))
                     if kind == 25 else 0.0)
        buf = np.empty(n, np.int32)
        engine.ce_cur_randrun(
            kind, float(t), float(_ce_i_opt(p, "lo", 0)),
            float(_ce_i_opt(p, "hi", 255)),
            rate, prox, gap, decay, run_size, seed, dmode, bias,
            soft, flick, slide, mirror, punch, heads,
            density, mod_depth, prox_raw, has_prox,
            n, buf.ctypes.data_as(_I32P))
        return _ce_make_ef(t, sorted_fx, (buf,), [(0, ch)])

    def _ce_cur_varyspeed_compute(self, pid, t, p, fixtures, positions,
                                  sort_mode, base_order_ids, variant):
        fam = _ce_fam_cfg()
        cfg = fam.get("varyspeed|" + variant)
        if cfg is None:
            raise _CeFallback()
        look, lv = cfg
        sorted_fx = _ce_sorted_fx(self, fixtures, positions, sort_mode,
                                  base_order_ids, p)
        if not sorted_fx:
            return None
        _ce_check_unique(sorted_fx)
        n = len(sorted_fx)
        ch = str(p.get("ch_type", "dimmer") or "dimmer")
        center = max(1e-6, _ce_f_opt(p, "speed", 1.0))
        swing = _ce_clamp01(_ce_f_opt(p, "swing", 0.5))
        vfloor = min(0.85, max(0.0, _ce_f_opt(p, "floor", 0.0)))
        cycle = max(0.2, _ce_f_opt(p, "cycle", 4.0))
        uni = _ce_clamp01(_ce_f_opt(p, "uniformity", 0.3))
        sshape = _CE_VS_SSHAPE_IDX.get(str(p.get("vs_shape", "正弦")), 0)
        wshape = _CUR_SHAPE_IDX.get(
            str(p.get("vs_wave", fam.get("vswave|" + variant, "sine"))), 0)
        # 载体 look 的参数提取（各 look 的 st.get 默认/钳制语义逐位对齐）:
        # v1 look: windmill 空间频率默认 2.0, 其余 1.0; width 钳 0.01。
        if look <= 7:
            freq = _ce_f_opt(p, "spatial_freq", 2.0 if look == 7 else 1.0)
            duty = _ce_f_opt(p, "duty", 0.5)
            width = max(0.01, _ce_f_opt(p, "width", 0.18))
            tail = _ce_f_opt(p, "tail", 0.5)
            spread = _ce_f_opt(p, "spread", 0.0)
            density = _ce_f_opt(p, "density", 0.3)
        elif look == 8:      # meteor: width 默认 0.12 钳 0.01
            freq = 0.0
            duty = 0.5
            width = max(0.01, _ce_f_opt(p, "width", 0.12))
            tail = 0.0
            spread = 0.0
            density = 0.0
        elif look == 11:     # wavepkt: spatial_freq 默认 1.0 钳 0.5
            freq = max(0.5, _ce_f_opt(p, "spatial_freq", 1.0))
            duty = 0.5
            width = 0.0
            tail = 0.0
            spread = 0.0
            density = 0.0
        elif look == 12:     # string: spatial_freq 默认 1.5 钳 0.5
            freq = max(0.5, _ce_f_opt(p, "spatial_freq", 1.5))
            duty = 0.5
            width = 0.0
            tail = 0.0
            spread = 0.0
            density = 0.0
        elif look == 14:     # marquee: width 默认 0.12 钳 0.02
            freq = 0.0
            duty = 0.5
            width = max(0.02, _ce_f_opt(p, "width", 0.12))
            tail = 0.0
            spread = 0.0
            density = 0.0
        elif look == 18:     # swell: spread 默认 0.35
            freq = 0.0
            duty = 0.5
            width = 0.0
            tail = 0.0
            spread = _ce_f_opt(p, "spread", 0.35)
            density = 0.0
        elif look == 22:     # fountain: width 默认 0.18（runjet 内钳 0.01）
            freq = 0.0
            duty = 0.5
            width = _ce_f_opt(p, "width", 0.18)
            tail = 0.0
            spread = 0.0
            density = 0.0
        else:                # tideflat/pulse/soliton/levelbar/buildup/
            freq = 0.0       # pluck/stair/skewtri/dpulse: 无参数读取
            duty = 0.5
            width = 0.0
            tail = 0.0
            spread = 0.0
            density = 0.0
        seed = _ce_i_opt(p, "seed", 0)
        buf = np.empty(n, np.int32)
        engine.ce_cur_varyspeed(
            look, lv, wshape, float(t),
            float(_ce_i_opt(p, "lo", 0)), float(_ce_i_opt(p, "hi", 255)),
            center, swing, cycle, uni, vfloor, sshape,
            freq, duty, width, tail, spread, density, seed,
            n, buf.ctypes.data_as(_I32P))
        return _ce_make_ef(t, sorted_fx, (buf,), [(0, ch)])

    def _ce_cur_fcdc_compute(self, pid, t, p, fixtures, positions,
                             sort_mode, base_order_ids, variant):
        kind = _CUR_FCDC_KIND[variant]
        cfg = _ce_fam_cfg().get("foldcascade|" + variant)
        if cfg is None:
            raise _CeFallback()
        front, arch, ret = cfg
        sorted_fx = _ce_sorted_fx(self, fixtures, positions, sort_mode,
                                  base_order_ids, p)
        if not sorted_fx:
            return None
        _ce_check_unique(sorted_fx)
        n = len(sorted_fx)
        ch = str(p.get("ch_type", "dimmer") or "dimmer")
        speed = max(0.01, _ce_f_opt(p, "speed", 0.5))
        pause = max(0.0, _ce_f_opt(p, "pause", 0.12))
        width = max(0.02, _ce_f_opt(p, "width", 0.14))
        accel = _ce_clamp01(_ce_f_opt(p, "accel", 0.35))
        handoff = _CE_FC_HANDOFF_IDX.get(str(p.get("handoff", "硬接")), 4)
        oneway = 1 if str(p.get("direction", "往返")) == "单向" else 0
        seed = _ce_i_opt(p, "seed", 0)
        groups = max(1, _ce_i_opt(p, "groups", 1))
        buf = np.empty(n, np.int32)
        engine.ce_cur_fcdc(
            kind, front, arch, ret, float(t),
            float(_ce_i_opt(p, "lo", 0)), float(_ce_i_opt(p, "hi", 255)),
            speed, pause, width, accel, handoff, oneway, seed, groups,
            n, buf.ctypes.data_as(_I32P))
        return _ce_make_ef(t, sorted_fx, (buf,), [(0, ch)])

    def _ce_rainbow_compute(self, pid, t, p, fixtures, positions,
                            sort_mode, base_order_ids):
        sorted_fx = _ce_sorted_fx(self, fixtures, positions, sort_mode,
                                  base_order_ids, p)
        if not sorted_fx:
            return None
        _ce_check_unique(sorted_fx)
        n = len(sorted_fx)
        bufs = (np.empty(n, np.int32), np.empty(n, np.int32),
                np.empty(n, np.int32))
        engine.ce_wave_rainbow(
            float(t), _ce_f_opt(p, "speed", 0.5),
            _ce_f_opt(p, "phase_step", 0.08),
            _ce_f_opt(p, "brightness", 1.0),
            n, bufs[0].ctypes.data_as(_I32P),
            bufs[1].ctypes.data_as(_I32P), bufs[2].ctypes.data_as(_I32P))
        return _ce_make_ef(t, sorted_fx, bufs, [(0, "R"), (1, "G"), (2, "B")])

    def _ce_dual_compute(self, pid, t, p, fixtures, positions,
                         sort_mode, base_order_ids):
        sorted_fx = _ce_sorted_fx(self, fixtures, positions, sort_mode,
                                  base_order_ids, p)
        if not sorted_fx:
            return None
        _ce_check_unique(sorted_fx)
        n = len(sorted_fx)
        ch = str(p.get("ch_type", "dimmer") or "dimmer")
        buf = np.empty(n, np.int32)
        engine.ce_wave_dual(
            float(t), _ce_f_opt(p, "speed", 1.0),
            _ce_f_opt(p, "phase_step", 0.15),
            float(_ce_i_opt(p, "min", 0)), float(_ce_i_opt(p, "max", 255)),
            n, buf.ctypes.data_as(_I32P))
        return _ce_make_ef(t, sorted_fx, (buf,), [(0, ch)])

    log.info("[CE] T4续 已接入: compute_at chase/fader/audio_wave/v29 分支 → C")
    log.info("[CE] T4续2 已接入: compute_at gradient/color_gradient/ramp"
             "/fan_cue 分支 → C")
    log.info("[CE] T4续3 已接入: compute_at 精炼库 cur_scalar/cur_env/"
             "cur_chase/cur_rand/cur_breathe/cur_color + wave_rainbow/"
             "wave_dual 分支 → C")

    # ── T6续: compute() 批量序列求值 → ce_compute_sequence ──────────────
    # 对齐 compute() :1237-1296 帧循环 + _run_compute_fn 简路径（wings=1/
    # block_size=1/_app_scope=both/无 phase_spread/无重复 orig_i）下
    # _wave_generic :1444-1456 的逐设备求值。C 一次调用求整段值矩阵
    # [n_frames × n]（行主序）, 帧对象构造（EffectFrame / values[orig_i][ch]
    # / t=f/fps 同原版 float 除法）留 Python。不满足纯波形条件（音频/包络/
    # 门控参数/域外值）→ 回退原版。
    orig_compute = ee_mod.EffectEngine.compute
    _F53 = 2 ** 53

    def _ce_seq_has_env(pid):
        """preset 有包络绑定 → 每帧 params 随 t 变 → 批量 C 无法复刻 → 回退。"""
        try:
            from src.ui.param_envelope import get_param_envelope_store
            return bool(get_param_envelope_store().get_bindings(str(pid or "")))
        except Exception:
            return False

    def _ce_seq_nan(p):
        """门控数值参数为 NaN → 原版 float 运算会传播出 ValueError → 回退原版。"""
        for k in ("speed", "phase_step", "phase_spread", "effect_width",
                  "xy_envelope_gain"):
            try:
                v = float(p.get(k, 0.0))
            except Exception:
                continue
            if v != v:
                return True
        return False

    def _ce_compute_seq_gated(pid, params, fps, positions, sort_mode):
        """compute() 批量 C 路径门控: True → 回退原版（字节不变）。

        音频分支（_audio_required 早退静音帧 / _audio_provider 逐帧参数）、
        包络绑定、_ce_gated 特性（摇杆/_app_scope≠both/phase_spread/wings/
        block_size/effect_width/xy_envelope/per_shape 单元）→ 原版;
        21689ea 的 _matrix_coords 全局矩阵投影生效（wave 族 spatial_phase_index
        → 全局 x 投影相位）→ 原版; fps≤0 与 NaN → 原版（异常路径一致）;
        |min|/|max|/|hi-lo| ≥ 2^53（Python 任意精度 int 与 C double 逐位
        分歧域）→ 原版。
        """
        if not isinstance(params, dict):
            return True
        if not (fps > 0.0):
            return True
        if params.get("_audio_required") or callable(params.get("_audio_provider")):
            return True
        if _ce_seq_has_env(pid):
            return True
        if _ce_seq_nan(params):
            return True
        if _ce_matrix_proj_active(params, positions, sort_mode):
            return True
        if _ce_gated(params):
            return True
        lo = _ce_i_opt(params, "min", 0)
        hi = _ce_i_opt(params, "max", 255)
        if abs(lo) >= _F53 or abs(hi) >= _F53 or abs(hi - lo) >= _F53:
            return True
        return False

    def _ce_compute_seq(self, pid, params, fixtures, positions, duration,
                        fps, sort_mode, base_order_ids):
        """对齐 compute() 帧循环 + _wave_generic 的 C 批量求值。

        排序链 = _ce_sorted_fx（sort + 空间聚块 + 方向, 与原版同函数）;
        值矩阵 = ce_compute_sequence 一次调用; 帧构造逐帧复刻原版
        （ef.values = [{} for _ in range(len(fixtures))], 按 orig_i 写入）。
        重复 orig_i（虚拟单元 merge-max 语义）→ _CeFallback 回退原版。
        """
        sorted_fx = _ce_sorted_fx(self, fixtures, positions, sort_mode,
                                  base_order_ids, params)
        n = len(sorted_fx)
        if n == 0:
            return []
        _ce_check_unique(sorted_fx)
        n_frames = max(1, int(duration * fps))
        ch = params.get("ch_type", "dimmer")
        lo = _ce_i_opt(params, "min", 0)
        hi = _ce_i_opt(params, "max", 255)
        buf = np.empty(n_frames * n, np.uint8)
        ptr = buf.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8))
        # K3 离线烘焙并行: QASCADE_CE_BAKE_THREADS>1 时走 ce_bake_sequence_omp
        # （(frame,chunk) 任务 OpenMP, 逐字节 == ce_compute_sequence）; 默认
        # 0 = 走 T6b 自门控 OpenMP 帧并行（小任务自动走串行同路径）。
        n_threads = int(os.environ.get("QASCADE_CE_BAKE_THREADS", "0") or "0")
        if n_threads > 1 and getattr(engine, "ce_bake_sequence_omp", None) is not None:
            _K3_BAKE_CNT[0] += 1
            engine.ce_bake_sequence_omp(
                float(fps), _ce_f_opt(params, "speed", 1.0),
                _ce_f_opt(params, "phase_step", 0.1),
                float(lo), float(hi), _WAVE_IDX[pid[5:]],
                n_frames, n, ptr, n_threads, 1000)
        else:
            # T6b: OpenMP 帧并行版（自门控: 小任务自动走与串行版逐字相同的
            # 路径; 大任务 n_frames>=8 且 >=200K 求值 且 omp>=2 线程才并行）。
            engine.ce_compute_sequence_omp(
                float(fps), _ce_f_opt(params, "speed", 1.0),
                _ce_f_opt(params, "phase_step", 0.1),
                float(lo), float(hi), _WAVE_IDX[pid[5:]],
                n_frames, n, ptr)
        frames = []
        for f in range(n_frames):
            ef = ee_mod.EffectFrame(t=f / fps)
            ef.values = [{} for _ in range(len(fixtures))]
            row = f * n
            for i, (orig_i, _fi) in enumerate(sorted_fx):
                ef.values[int(orig_i)][ch] = int(buf[row + i])
            frames.append(ef)
        return frames

    def ce_compute(self, preset_id, params, fixtures, fixture_defs,
                   positions, duration=4.0, fps=40.0, sort_mode="index",
                   base_order_ids=None):
        pid = str(preset_id or "")
        if pid.startswith("wave_") and pid[5:] in _WAVE_IDX:
            _hc = getattr(self, "_ce_hook_counts", None)
            if _hc is None:
                # L6: 不含 p5a/p5b 残留键（PlaybackEngine 计数）
                _hc = {"t4": 0,
                       "t4_chase": 0, "t4_fader": 0, "t4_audio_wave": 0,
                       "t4_v29": 0, "t4_grad": 0, "t4_color": 0,
                       "t4_ramp": 0, "t4_fancue": 0, "env": 0, "t6_seq": 0}
                self._ce_hook_counts = _hc
            try:
                if not _ce_compute_seq_gated(pid, params, fps, positions,
                                             sort_mode):
                    _hc["t6_seq"] = _hc.get("t6_seq", 0) + 1
                    # ★ R118 方向镜像同步（同 ce_compute_at 入口注）: 原版
                    #   compute() 入口（effect_engine.py:3316）effective_params
                    #   镜像后进排序链; _ce_compute_seq 的 _ce_sorted_fx 须用
                    #   镜像方向才与原版同序。orig_compute 回退传原 params
                    #   （原版入口自行镜像; 镜像非对合不可叠加）。
                    return _ce_compute_seq(self, pid,
                                           ee_mod.effective_params(pid, params),
                                           fixtures, positions, duration, fps,
                                           sort_mode, base_order_ids)
            except _CeFallback:
                pass
            except Exception as _e:
                _CE_EXC_CNT[0] += 1   # H1: T6b/K3 C 内核异常 → 失败计数
                if os.environ.get("QASCADE_CE_TIMING", "0") == "1":
                    print(f"[CE-SEQ-EXC] {type(_e).__name__}: {_e}", flush=True)
        return orig_compute(self, preset_id, params, fixtures, fixture_defs,
                            positions, duration=duration, fps=fps,
                            sort_mode=sort_mode, base_order_ids=base_order_ids)

    ee_mod.EffectEngine.compute = ce_compute
    log.info("[CE] T6续 已接入: compute() wave 批量 → ce_compute_sequence")


def _hook_frame_path_p4b(engine):
    """P4b（R178 2026-09-28 退役 = CLAUDE.md 10.3「明确回退原版」）：不再替换任何符号。

    原替代版 render_path.ce_render_position（连同其内的 P6 批量 / P8 线程池 /
    融合调用）在 R150-R177 期间未跟上 Python `_render_position` 的 10 处语义
    改动 → 示例工程 实时逐帧 A/B 30 帧数据 + 806 帧活跃掩码与 Python 不一致（全部
    CE 漂移的唯一来源）。C 未同步 → 按 10.3 回退原版：实时渲染一律走 Python
    `_render_position`（其内 `_mix_clips` 仍经 K0 C 内核）。性能由 R178 掩码
    帧准备缓存补回（纯 Python 实时 示例工程 65.0s → 38.7s，均值 36.5 → 21.7ms）。
    render_path 模块保留：P7 `ce_indexed_active_clip_items` 仍在用；
    ce_render_position 仅供探针对照，C 同步后重新接入须过门1/门2 + 实时 A/B。
    """
    del engine
    log.info("[CE] P4b 已退役（R178 明确回退原版）: _render_position 保持 Python 原版")


def _hook_indexed_holds(engine):
    """P7: _indexed_active_clip_items → 优化版（hold 覆盖率检查 O(H×U×512)→增量计数）。

    原版播放索引活跃 clip 扫描的 hold 段每帧从最新 hold 反向扫描, 每个 useful
    hold 末尾对全部 scope 域重算覆盖率 all(...) → O(全时间线hold数 × 域数 × 512)。
    优化版（render_path.ce_indexed_active_clip_items）扫描顺序/选中判定/break 时机
    逐字不变, 仅把覆盖率检查换成剩余未覆盖计数（归零 ⟺ 全部 scope 全覆盖, 与原版
    判定逐点等价）。位级等价: 探针 ce_probe_hold_scan.py 多时间线 A/B deep-equal。
    失败 → 回退原版方法（字节不变）。
    """
    from src.engine import playback_engine as pe
    from . import render_path

    orig = pe.PlaybackEngine._indexed_active_clip_items

    def ce_indexed(self, now, tracks, ordered_tracks, track_by_id, any_solo,
                   timer_interval_s):
        try:
            return render_path.ce_indexed_active_clip_items(
                self, now, tracks, ordered_tracks, track_by_id, any_solo,
                timer_interval_s)
        except Exception:
            _CE_EXC_CNT[0] += 1   # H1: P7 C 路径异常 → 失败计数（回退原版）
            return orig(self, now, tracks, ordered_tracks, track_by_id,
                        any_solo, timer_interval_s)

    pe.PlaybackEngine._indexed_active_clip_items = ce_indexed
    log.info("[CE] P7 已接入: _indexed_active_clip_items hold 覆盖率检查 → 增量计数")


def _hook_precharge(engine):
    """T6: 泵预充电调度器纯数学 → C 内核（调度器状态机保留 Python）。

    register/tick/_fire_event 的数学调用点（lead_s/trigger_position/value/
    channel/should_fire/build_frame）换 C; 状态机（_pending 列表、fired/
    cancelled 标记、按触发窗口剪枝）逐字保留。C 内核 fuzz 10 万次位级一致
    （ce_fuzz_precharge.py）。任一 C 调用失败 → 回退原版方法（字节不变）。
    QASCADE_CE_PIPELINE_AB=1 时逐值对比 C vs 原版数学（仅探针）。
    """
    from src.engine import pump_precharge as pp

    orig_register = pp.PrechargeScheduler.register
    orig_tick = pp.PrechargeScheduler.tick
    orig_fire = pp.PrechargeScheduler._fire_event
    _UP = ctypes.POINTER(ctypes.c_uint8)
    _ab = os.environ.get("QASCADE_CE_PIPELINE_AB", "0") == "1"

    def _ab_dump(tag, *parts):
        if _ab:
            print(f"[AB-DIFF] precharge {tag} " + " ".join(str(p) for p in parts),
                  flush=True)

    def ce_register(self, clip, pump_channels):
        clip_id = str(getattr(clip, "id", "") or "")
        if not clip_id:
            return
        if not bool(getattr(clip, "precharge_enabled", False)):
            return
        if clip_id in self._scheduled_clip_ids:
            return  # Already registered
        try:
            lead_ms = float(getattr(clip, "precharge_lead_ms", 1000.0) or 1000.0)
            lead_s = engine.ce_precharge_lead_s(lead_ms)
            trigger_position = engine.ce_precharge_trigger_position(
                float(clip.start_time), lead_s)
            clip_start = float(clip.start_time)
            level = engine.ce_precharge_value(
                float(getattr(clip, "precharge_level", 13) or 0))
            channels = [engine.ce_precharge_channel(float(ch_1based))
                        for _uni, ch_1based in pump_channels]
            if _ab:
                try:
                    _py_lead = max(0.0, lead_ms / 1000.0)
                    _py_trig = max(0.0, clip_start - lead_s)
                    _py_level = max(
                        0, min(255, int(getattr(clip, "precharge_level", 13) or 0)))
                    _py_chans = [
                        max(0, min(511, int(ch_1b) - 1))
                        for _u, ch_1b in pump_channels]
                    if (lead_s != _py_lead or trigger_position != _py_trig
                            or level != _py_level or channels != _py_chans):
                        _ab_dump("register", clip_id,
                                 f"lead={lead_s}!={_py_lead}",
                                 f"trig={trigger_position}!={_py_trig}",
                                 f"lvl={level}!={_py_level}",
                                 f"ch={channels}!={_py_chans}")
                except Exception:
                    pass
        except Exception:
            _CE_EXC_CNT[0] += 1   # H1: T6 register C 内核异常 → 失败计数
            return orig_register(self, clip, pump_channels)
        _PRECHARGE_CNT[0] += 1
        for (universe, _ch_1based), channel in zip(pump_channels, channels):
            event = pp.PrechargeEvent(
                clip_id=clip_id,
                universe=int(universe),
                channel=channel,
                level=level,
                trigger_position=trigger_position,
                clip_start=clip_start,
            )
            self._pending.append(event)
        self._scheduled_clip_ids.add(clip_id)
        log.debug(
            "Precharge registered: clip=%s, channels=%d, trigger=%.3fs, level=%d",
            clip_id, len(pump_channels), trigger_position, level,
        )

    def ce_tick(self, current_position):
        now = max(0.0, float(current_position))
        prune = []
        for event in self._pending:
            if event.fired or event.cancelled:
                if event.fired and now >= event.clip_start:
                    prune.append(event)
                continue
            try:
                fire = bool(engine.ce_precharge_should_fire(
                    now, event.trigger_position, event.clip_start))
                _PRECHARGE_CNT[0] += 1
                if _ab:
                    try:
                        py_fire = bool(event.trigger_position <= now
                                       < event.clip_start)
                        if fire != py_fire:
                            _ab_dump("tick", event.clip_id,
                                     f"u={event.universe} ch={event.channel}",
                                     f"now={now} trig={event.trigger_position}",
                                     f"start={event.clip_start}",
                                     f"C={fire} py={py_fire}")
                    except Exception:
                        pass
            except Exception:
                _CE_EXC_CNT[0] += 1   # H1: T6 tick C 内核异常 → 失败计数
                fire = bool(event.trigger_position <= now < event.clip_start)
            if fire:
                self._fire_event(event)
        if prune:
            for event in prune:
                try:
                    self._pending.remove(event)
                except ValueError:
                    pass

    def ce_fire_event(self, event):
        if self._nm is None:
            log.warning("Precharge fire skipped: no network manager")
            return
        try:
            data = np.empty(512, np.uint8)
            snapshot = getattr(self._nm, "_universe_snapshot", {})
            snap = snapshot.get(event.universe)
            if snap is not None:
                snap_a = np.ascontiguousarray(snap, np.uint8)
                if snap_a.shape != (512,):
                    raise ValueError("snapshot shape != 512")
                engine.ce_precharge_build_frame(
                    snap_a.ctypes.data_as(_UP), 1,
                    int(event.channel), int(event.level),
                    data.ctypes.data_as(_UP))
            else:
                engine.ce_precharge_build_frame(
                    None, 0, int(event.channel), int(event.level),
                    data.ctypes.data_as(_UP))
            _PRECHARGE_CNT[0] += 1
            if _ab:
                try:
                    ref = np.zeros(512, np.uint8)
                    if snap is not None:
                        ref[:] = snap
                    ref[int(event.channel)] = int(event.level)
                    if not np.array_equal(data, ref):
                        _d = np.nonzero(data != ref)[0][:8]
                        _ab_dump("fire", event.clip_id,
                                 f"u={event.universe} ch={event.channel}",
                                 f"d={_d.tolist()}")
                except Exception:
                    pass
            self._nm.send_raw(event.universe, data)
            event.fired = True
            log.debug(
                "Precharge fired: universe=%d ch=%d level=%d",
                event.universe, event.channel, event.level,
            )
        except Exception:
            _CE_EXC_CNT[0] += 1   # H1: T6 fire C 内核异常 → 失败计数
            log.exception("Precharge fire failed for universe=%d ch=%d",
                          event.universe, event.channel)

    pp.PrechargeScheduler.register = ce_register
    pp.PrechargeScheduler.tick = ce_tick
    pp.PrechargeScheduler._fire_event = ce_fire_event
    log.info("[CE] T6 已接入: PrechargeScheduler 纯数学 → ce_precharge_* 内核")




def _hook_flush_timing(engine):
    """T5 诊断: flush_universes_now 分段计时（QASCADE_CE_TIMING=1 时打印, 不改语义）。"""
    import time
    from src.network import network_manager as nm_mod

    orig_flush = nm_mod.NetworkManager.flush_universes_now

    def ce_flush(self, universes=None):
        t0 = time.perf_counter()
        _FLUSH_TIMING_CNT[0] += 1
        try:
            return orig_flush(self, universes)
        finally:
            dt = (time.perf_counter() - t0) * 1000.0
            if os.environ.get("QASCADE_CE_TIMING", "0") == "1":
                _mt = getattr(self, "_ce_mix_timing", None)
                _mstr = ""
                if _mt is not None:
                    _cavg = _mt.get("c", 0.0) / max(1, _mt["n"]) * 1e6
                    _mstr = (f" mix×{_mt['n']} 累计={_mt['sum']*1000:.1f}ms "
                             f"({_mt['sum']*1000.0/max(1,_mt['n'])*1000:.1f}µs/次, "
                             f"C内核 {_cavg:.1f}µs, 包装 {_mt['sum']*1e6/max(1,_mt['n'])-_cavg:.1f}µs "
                             f"blend={getattr(self, '_shape_blend_mode', '?')})")
                print(f"[TIMING] flush_universes_now 整体 {dt:.2f}ms{_mstr}", flush=True)

    nm_mod.NetworkManager.flush_universes_now = ce_flush
    log.info("[CE] T5 计时 hook 已接入: flush_universes_now")


def _hook_flush_batch(engine):
    """T5b: flush_universes_now 批量混合 → ce_full_mix_many（OpenMP 按域并行）。

    73 域 flush 每帧串行 73 次 ce_full_mix（C 内核 ~68µs + Python 包装
    ~35µs ≈ 8-11ms/帧）; 域间零共享（每域独立输出）→ 一次 C 调用传
    13 组指针数组（Python 层指针缓存填充 ~1.5-3.5ms, 与串行包装同成本）
    + C 内按域并行（0.45ms vs 5.0ms 串行）。实测 flush 中位 5.5ms vs
    串行 13.0ms（~2.4x, 负载环境）。
    仅替换混合段; _apply_xy_reference/联锁/快照/发送 保留原版逐域处理
    （字节不变）; 小集（<8 域）或任何异常 → 原版 flush 整体回退
    （其内 ce_mix_one 同样回退, 字节不变）。
    """
    from src.network import network_manager as nm_mod
    orig_flush = nm_mod.NetworkManager.flush_universes_now
    _P8 = ctypes.POINTER(ctypes.c_uint8)
    _PD = ctypes.POINTER(ctypes.c_double)
    _PF = ctypes.POINTER(ctypes.c_float)
    _C = ctypes
    _BASE_N = ("fader", "programmer", "tester", "linker")
    _SHAPE_N = ("effect", "effect_auto", "matrix", "matrix_audio", "audio",
                "joystick")
    _CLIP_N = ("timeline", "clip_playback", "clip_preview")
    _ALL_N = _BASE_N + _SHAPE_N + _CLIP_N

    def _ce_batch_ptrs(self, universes, _ptrc):
        """持久指针缓冲(按 n 缓存): 每次 _ce_batch_fill 原地重填后返回
        12 个 cast 指针直传 C 内核 —— 免每 tick 重建 12 个 ctypes 数组
        + 975 次 list.append(实测组装 ~0.46ms/tick 中占比过半)。
        层对象判等缓存语义不变(原地写也正确); 缺席槽位 memset 归 NULL,
        与旧版 None 平铺逐位相同。"""
        n = len(universes)
        buf = _ptrc.get(("_batchbuf", n))
        if buf is None:
            bd = (_P8 * (4 * n))(); ba = (_P8 * (4 * n))()
            sd = (_P8 * (6 * n))(); sa = (_P8 * (6 * n))()
            cd = (_P8 * (3 * n))(); ca = (_P8 * (3 * n))()
            lt = (_PD * (13 * n))()
            def_d = (_P8 * n)(); def_a = (_P8 * n)()
            anchor = (_PF * n)(); clip_t = (_PD * n)(); editor_t = (_PD * n)()
            raw = (bd, ba, sd, sa, cd, ca, lt, def_d, def_a, anchor,
                   clip_t, editor_t)
            buf = (
                _C.cast(bd, _C.POINTER(_P8)), _C.cast(ba, _C.POINTER(_P8)),
                _C.cast(sd, _C.POINTER(_P8)), _C.cast(sa, _C.POINTER(_P8)),
                _C.cast(cd, _C.POINTER(_P8)), _C.cast(ca, _C.POINTER(_P8)),
                _C.cast(lt, _C.POINTER(_PD)),
                _C.cast(def_d, _C.POINTER(_P8)), _C.cast(def_a, _C.POINTER(_P8)),
                _C.cast(anchor, _C.POINTER(_PF)),
                _C.cast(clip_t, _C.POINTER(_PD)), _C.cast(editor_t, _C.POINTER(_PD)),
                raw,
            )
            _ptrc[("_batchbuf", n)] = buf
        _ce_batch_fill(self, universes, _ptrc, buf[12])
        return buf[:12]

    def _ce_batch_fill(self, universes, _ptrc, bufs):
        """持久指针缓冲原地填充: 全 memset NULL 后按存在分支写入,
        与旧版 [None]*n 平铺/逐域查表输出逐位相同(行主序布局不变:
        层组 = uni-major, ltp = name-major)。
        E-1: name-major 迭代 + bound get 局部绑定 + 空层 dict 跳域循环
        —— 写入序列/判等缓存语义逐位不变(纯查找重构)。"""
        bd, ba, sd, sa, cd, ca, lt, def_d, def_a, anchor, clip_t, editor_t = bufs
        n = len(universes)
        for arr in bufs:
            _C.memset(arr, 0, _C.sizeof(arr))
        _layers = getattr(self, "_layers", {}) or {}
        ptrc_get = _ptrc.get
        for names, dbuf, abuf in ((_BASE_N, bd, ba), (_SHAPE_N, sd, sa),
                                  (_CLIP_N, cd, ca)):
            nlen = len(names)
            for j, nm in enumerate(names):
                layer = _layers.get(nm)
                if not layer:
                    continue
                lg = layer.get
                for k, uni in enumerate(universes):
                    e = lg(uni)
                    if e is None:
                        continue
                    d, a = e
                    idx = k * nlen + j
                    c = ptrc_get((nm, uni))
                    if c is not None and c[2] is d and c[3] is a:
                        dbuf[idx] = c[0]
                        abuf[idx] = c[1]
                    else:
                        dp = np.ascontiguousarray(d, np.uint8).ctypes.data_as(_P8)
                        ap = np.ascontiguousarray(a, np.uint8).ctypes.data_as(_P8)
                        _ptrc[(nm, uni)] = (dp, ap, d, a)
                        dbuf[idx] = dp
                        abuf[idx] = ap
        _ltp_layers = getattr(self, "_ltp_editor_layer_time", {}) or {}
        if _ltp_layers:
            for j, nm in enumerate(_ALL_N):
                layer = _ltp_layers.get(nm, {})
                if not layer:
                    continue
                lg = layer.get
                for k, uni in enumerate(universes):
                    obj = lg(uni)
                    if obj is None:
                        continue
                    c = ptrc_get(("ltp", nm, uni))
                    if c is not None and c[1] is obj:
                        lt[j * n + k] = c[0]
                    else:
                        p = np.ascontiguousarray(obj, np.float64).ctypes.data_as(_PD)
                        _ptrc[("ltp", nm, uni)] = (p, obj)
                        lt[j * n + k] = p
        _defaults = getattr(self, "_channel_defaults", {}) or {}
        if _defaults:
            dg = _defaults.get
            for k, uni in enumerate(universes):
                de = dg(int(uni))
                if de is None:
                    continue
                c = ptrc_get(("def", uni))
                if c is not None and c[2] is de:
                    def_d[k] = c[0]; def_a[k] = c[1]
                else:
                    ddp = np.ascontiguousarray(de[0], np.uint8).ctypes.data_as(_P8)
                    dap = np.ascontiguousarray(de[1], np.uint8).ctypes.data_as(_P8)
                    _ptrc[("def", uni)] = (ddp, dap, de)
                    def_d[k] = ddp; def_a[k] = dap
        _anchors = getattr(self, "_direct_anchor_by_universe", {}) or {}
        _shape_anchor = getattr(self, "_shape_default_anchor", None)
        if _anchors:
            ag = _anchors.get
            for k, uni in enumerate(universes):
                obj = ag(int(uni), _shape_anchor)
                if obj is None:
                    continue
                c = ptrc_get(("anchor", uni))
                if c is not None and c[1] is obj:
                    anchor[k] = c[0]
                else:
                    p = np.ascontiguousarray(obj, np.float32).ctypes.data_as(_PF)
                    _ptrc[("anchor", uni)] = (p, obj)
                    anchor[k] = p
        elif _shape_anchor is not None:
            c = ptrc_get(("anchor", -1))
            if c is not None and c[1] is _shape_anchor:
                p = c[0]
            else:
                p = np.ascontiguousarray(_shape_anchor, np.float32).ctypes.data_as(_PF)
                _ptrc[("anchor", -1)] = (p, _shape_anchor)
            for k in range(n):
                anchor[k] = p
        _clip_times = getattr(self, "_ltp_clip_time", {}) or {}
        if _clip_times:
            cg = _clip_times.get
            for k, uni in enumerate(universes):
                obj = cg(uni)
                if obj is None:
                    continue
                c = ptrc_get(("clip_t", uni))
                if c is not None and c[1] is obj:
                    clip_t[k] = c[0]
                else:
                    p = np.ascontiguousarray(obj, np.float64).ctypes.data_as(_PD)
                    _ptrc[("clip_t", uni)] = (p, obj)
                    clip_t[k] = p
        _editor_times = getattr(self, "_ltp_editor_time", {}) or {}
        if _editor_times:
            eg = _editor_times.get
            for k, uni in enumerate(universes):
                obj = eg(uni)
                if obj is None:
                    continue
                c = ptrc_get(("editor_t", uni))
                if c is not None and c[1] is obj:
                    editor_t[k] = c[0]
                else:
                    p = np.ascontiguousarray(obj, np.float64).ctypes.data_as(_PD)
                    _ptrc[("editor_t", uni)] = (p, obj)
                    editor_t[k] = p

    def ce_flush_batch(self, universes=None):
        if getattr(engine, "ce_full_mix_many", None) is None:
            return orig_flush(self, universes)
        if getattr(self, "_shape_blend_mode", "direct") != "direct":
            return orig_flush(self, universes)
        if os.environ.get("QASCADE_CE_FLUSH_BATCH", "1") != "1":
            # A/B 诊断闸门: 强制逐域原路径（同进程内对比 flush 耗时）
            return orig_flush(self, universes)
        try:
            with self._state_lock:
                if universes is None:
                    universe_set = set()
                    for layer in self._layers.values():
                        universe_set.update(layer.keys())
                    universes = sorted(universe_set)
                else:
                    universes = sorted({int(u) for u in universes})
                universe_set = {int(u) for u in universes}
                if not universes:
                    self._dirty_universes.difference_update(universe_set)
                    return
                n = len(universes)
                if n < 8:
                    # 小集: OpenMP 团队开销 > 并行收益 → 原版逐域
                    return orig_flush(self, universes)
                _t0 = time.perf_counter()
                _ptrc = getattr(self, "_ce_layer_ptr_cache", None)
                if _ptrc is None:
                    _ptrc = {}
                    self._ce_layer_ptr_cache = _ptrc
                casted = _ce_batch_ptrs(self, universes, _ptrc)
                _ta = time.perf_counter()
                out = np.empty(n * 512, np.uint8)
                _t1 = time.perf_counter()
                _T5B_FLUSH_CNT[0] += 1
                engine.ce_full_mix_many(
                    casted[0], casted[1], casted[2], casted[3],
                    casted[4], casted[5], casted[6], n,
                    float(self._shape_gain),
                    casted[7], casted[8], casted[9], casted[10], casted[11],
                    out.ctypes.data_as(_P8))
                _t2 = time.perf_counter()
                if os.environ.get("QASCADE_CE_TIMING", "0") == "1":
                    _bt = getattr(self, "_ce_batch_timing", None)
                    if _bt is None:
                        _bt = [0, 0.0, 0.0]
                        self._ce_batch_timing = _bt
                    _bt[0] += 1
                    _bt[1] += (_t1 - _t0) * 1000.0
                    _bt[2] += (_t2 - _t1) * 1000.0
                    print("[TIMING] flush-batch n=%d 组装=%.2fms(查表=%.2f "
                          "cast=%.2f) C并行=%.2fms 合计=%.2fms"
                          % (n, (_bt[1] / _bt[0]),
                             ((_ta - _t0) * 1000.0),
                             ((_t1 - _ta) * 1000.0),
                             _bt[2] / _bt[0],
                             (_bt[1] + _bt[2]) / _bt[0]),
                          flush=True)
                for i, uni in enumerate(universes):
                    try:
                        virtual_out = out[i * 512:(i + 1) * 512]
                        physical_out = self._apply_xy_reference(int(uni),
                                                                virtual_out)
                        try:
                            from ..engine.safety_interlock import get_interlock as _get_ilock
                            _ilock = _get_ilock()
                            if _ilock.is_armed:
                                physical_out = _ilock.process(int(uni),
                                                              physical_out)
                        except Exception:
                            pass
                        if physical_out is virtual_out:
                            self._universe_snapshot[int(uni)] = physical_out.copy()
                            self._universe_virtual_snapshot[int(uni)] = self._universe_snapshot[int(uni)]
                        else:
                            self._universe_virtual_snapshot[int(uni)] = virtual_out.copy()
                            self._universe_snapshot[int(uni)] = physical_out.copy()
                        if self._running:
                            from src.network.nm_output_delivery import accept_single_source
                            accept_single_source(self, int(uni), physical_out)
                    except Exception:
                        log.exception("flush_universes_now() failed for universe %s",
                                      uni)
                self._dirty_universes.difference_update(universe_set)
        except Exception:
            _CE_EXC_CNT[0] += 1   # H1: T5b C 内核异常 → 失败计数（回退原版 flush）
            log.exception("flush_universes_now() failed")
            try:
                return orig_flush(self, universes)
            except Exception:
                log.exception("flush_universes_now() failed (fallback)")
                return None

    nm_mod.NetworkManager.flush_universes_now = ce_flush_batch
    log.info("[CE] T5b 已接入: flush_universes_now → ce_full_mix_many（OpenMP 按域并行）")


def _hook_heartbeat(engine):
    """T9: _mix_and_send_locked 空闲态心跳轮转纯数学段 → C 内核三件套。

    替换点 :1522-1535（空闲态心跳轮转）:
      到期判定 ce_heartbeat_due / 周期计划 ce_heartbeat_plan（interval/hb
      钳制 + round 半偶 + ceil 均分）/ 批取轮转 ce_heartbeat_rotate ——
      fuzz 217 万次位级一致（ce_fuzz_heartbeat.py）。队列装载的排序/去重
      （sorted(set(·))）留 Python; 方法其余部分逐字复刻原版（同 T3
      ce_mix_one 模式）; C 块任何异常 → except 内原版纯 Python 块逐字
      重放（字节不变）。C 调用全部成功后才统一写回状态 —— 失败时状态
      未动, 回退块与原版行为完全一致。到期 tick 的批取 = 重载全量队列前
      batch 项（同原版 :1533-1534）; 剩余队列由 C 轮转写入
      _heartbeat_queue（≡ 原版 queue[batch:]）。
    """
    from src.network import network_manager as nm_mod

    orig = nm_mod.NetworkManager._mix_and_send_locked
    _DP = ctypes.POINTER(ctypes.c_double)
    _I64P = ctypes.POINTER(ctypes.c_int64)
    _ALL_LAYERS = nm_mod.ALL_LAYERS

    def ce_mix_and_send_locked(self):
        t0 = time.perf_counter()
        self._flush_scheduled = False
        if not self._running:
            return []
        self._drain_pending_layer_updates_locked()
        output_unis = self._output_universe_allowlist()
        output_unis.update(int(u) for u in getattr(self, "_channel_defaults", {}).keys())

        now = time.monotonic()
        dirty_unis = set(getattr(self, "_dirty_universes", set()))
        if dirty_unis:
            self._dirty_universes.difference_update(dirty_unis)
            all_unis = dirty_unis
        else:
            # 空闲态:心跳到期时把需保活的全部 universe 装入轮转队列;每个 tick 只取一批,
            # 按"一个心跳周期内的 tick 数"均分,保证整批在下次心跳前刚好混完(每路刷新频率
            # 不变),但单次持锁只混一小批,锁占用大幅缩短。
            # T9: 三件套（到期判定/周期计划/批取轮转）→ C 内核; 队列装载
            # （sorted/set 排序去重）留 Python; C 失败 → except 原版块（字节不变）。
            try:
                _HEARTBEAT_CNT[0] += 1
                if engine.ce_heartbeat_due(
                        now, getattr(self, "_last_heartbeat_send", 0.0),
                        getattr(self, "_heartbeat_interval", 1.0)):
                    queue = sorted(set(output_unis) | set(self._universe_snapshot.keys()))
                    _oi = np.empty(1, np.float64)
                    _oh = np.empty(1, np.float64)
                    _ot = np.empty(1, np.int64)
                    _ob = np.empty(1, np.int64)
                    engine.ce_heartbeat_plan(
                        float(getattr(self, "_mix_interval_sec", 0.025)),
                        float(getattr(self, "_heartbeat_interval", 0.10)),
                        len(queue),
                        _oi.ctypes.data_as(_DP), _oh.ctypes.data_as(_DP),
                        _ot.ctypes.data_as(_I64P), _ob.ctypes.data_as(_I64P))
                    _batch = int(_ob[0])
                    if not queue:
                        # 同原版 :1531-1532: 空队列 → 无批可取（batch=0, 队列清空）
                        self._last_heartbeat_send = now
                        self._heartbeat_batch = _batch
                        self._heartbeat_queue = []
                        return []
                    _qa = np.asarray(queue, np.int64)
                    _rem = engine.ce_heartbeat_rotate(
                        _qa.ctypes.data_as(_I64P), len(queue), _batch)
                    # 全部 C 调用成功后统一写回（失败 → except → 原版块, 状态未动）
                    self._last_heartbeat_send = now
                    self._heartbeat_batch = _batch
                    # C 轮转结果 ≡ 原版 queue[batch:]（剩余队列, 供后续 tick 取用）
                    self._heartbeat_queue = (_qa[:_rem].tolist() if _rem > 0 else [])
                    # 本 tick 批取 = 重载全量队列前 batch 项（原版 :1533-1534 语义）
                    all_unis = set(queue[:_batch])
                else:
                    if not self._heartbeat_queue:
                        return []
                    batch = self._heartbeat_batch if self._heartbeat_batch > 0 else len(self._heartbeat_queue)
                    all_unis = set(self._heartbeat_queue[:batch])
                    self._heartbeat_queue = self._heartbeat_queue[batch:]
            except Exception:
                _CE_EXC_CNT[0] += 1   # H1: T9 heartbeat C 内核异常 → 失败计数
                # 原版纯 Python 心跳块（与 :1522-1535 逐字一致; C 失败回退, 字节不变）
                if now - getattr(self, "_last_heartbeat_send", 0.0) >= getattr(self, "_heartbeat_interval", 1.0):
                    self._last_heartbeat_send = now
                    queue = sorted(set(output_unis) | set(self._universe_snapshot.keys()))
                    self._heartbeat_queue = queue
                    interval = max(0.001, float(getattr(self, "_mix_interval_sec", 0.025)))
                    hb = max(interval, float(getattr(self, "_heartbeat_interval", 0.10)))
                    ticks_per_cycle = max(1, int(round(hb / interval)))
                    # 每 tick 批量 = ceil(队列长 / 周期内tick数);至少 1。
                    self._heartbeat_batch = max(1, -(-len(queue) // ticks_per_cycle)) if queue else 0
                if not self._heartbeat_queue:
                    return []
                batch = self._heartbeat_batch if self._heartbeat_batch > 0 else len(self._heartbeat_queue)
                all_unis = set(self._heartbeat_queue[:batch])
                self._heartbeat_queue = self._heartbeat_queue[batch:]

        # 持锁期间 _layers 只读稳定，直接引用免重建 layer×uni 快照字典。
        snapshots = self._layers

        # 获取 OutputSmoother 实例（懒加载，失败时无平滑）
        smoother = self._get_output_smoother()
        smoother_bypass = True
        if smoother is not None:
            try:
                smoother_bypass = bool(getattr(smoother, "is_bypass", lambda: False)())
            except Exception:
                smoother_bypass = True

        # 循环不变量提出：联锁/增益融合每帧只取一次。
        ilock = None
        try:
            from src.engine.safety_interlock import get_interlock as _get_ilock
            ilock = _get_ilock()
        except Exception:
            ilock = None
        ilock_armed = bool(getattr(ilock, "is_armed", False)) if ilock is not None else False
        guard_fuse = None
        try:
            from src.core import _guard as _g
            guard_fuse = getattr(_g, "fuse_output", None)
        except Exception:
            guard_fuse = None

        # Phase 2：direct 模式 + 门控开启时，一次性批量混完所有 universe（削 GIL）。
        # 计算等价于逐 universe 的 _mix_one_universe(direct 路径)；后续 smoother/xy/融合/
        # 快照仍逐 universe 走原逻辑，保证输出与旧路径逐字节一致。
        batch_mixed: Optional[Dict[int, np.ndarray]] = None
        if (
            self._batch_mix_enabled
            and self._shape_blend_mode == "direct"
            and all_unis
        ):
            try:
                batch_mixed = self._mix_universes_batch(snapshots, list(all_unis))
            except Exception:
                nm_mod.log.exception("批量混音失败，回退逐 universe")
                batch_mixed = None

        packets: List[Tuple[int, np.ndarray]] = []
        all_uni_list = [int(u) for u in all_unis]
        for uni in all_uni_list:
            if batch_mixed is not None:
                mixed = batch_mixed.get(uni)
                if mixed is None:
                    mixed = self._mix_one_universe(snapshots, uni)
            else:
                mixed = self._mix_one_universe(snapshots, uni)
            # ★ 用户输出平滑处理：仅在非旁通预设时生效。
            # 与 effect/shape 混音逻辑分离，旁通时严格输出原始混音结果。
            if not smoother_bypass:
                try:
                    mixed = smoother.process(uni, mixed)
                except Exception:
                    pass
            # ★ SafetyInterlock：输出安全联锁（硬件保护，在 smoother 之后、
            #   _send_to_hw 之前。根据每设备约束钳位或停机。）
            if ilock_armed and ilock is not None:
                try:
                    mixed = ilock.process(uni, mixed)
                except Exception:
                    pass
            virtual_out = mixed
            physical_out = self._apply_xy_reference(uni, virtual_out)
            # 校验点 #3：核心增益融合（armed+正确种子→恒等；否则破坏输出）。
            # 这是出图本身的一部分，非可删除的拦截分支。
            if guard_fuse is not None:
                try:
                    physical_out = guard_fuse(physical_out)
                except Exception:
                    pass
            # ★ 性能：无 xy refs/联锁/融合(恒等)时 physical_out 即 virtual_out，
            #   快照只拷贝一次（读者全部只读，get_virtual_universe_data 返回 .copy()）；
            #   packets 保持独立 copy（_send_to_hw 隔离语义不变）。
            if physical_out is virtual_out:
                self._universe_snapshot[uni] = physical_out.copy()
                self._universe_virtual_snapshot[uni] = self._universe_snapshot[uni]
            else:
                self._universe_virtual_snapshot[uni] = virtual_out.copy()
                self._universe_snapshot[uni] = physical_out.copy()
            packets.append((uni, physical_out.copy()))

        # 校验点 #3a：混音器燃料脉冲（唯一硬件输出路径 = 权威燃料表）。
        if packets:
            try:
                from src.core import _guard as _g
                _g.pulse_fuel()
            except Exception:
                pass

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self._record_perf_stats(len(all_unis), elapsed_ms, 0.0)
        if elapsed_ms >= self._perf_slow_mix_ms:
            now_log = time.monotonic()
            if now_log - self._perf_last_mix_log >= 2.0:
                self._perf_last_mix_log = now_log
                nm_mod.log.info(
                    "DMX mix/send slow frame: %.2f ms, universes=%s, dirty=%s",
                    elapsed_ms, len(all_unis), len(dirty_unis),
                )
        return packets

    nm_mod.NetworkManager._mix_and_send_locked = ce_mix_and_send_locked
    log.info("[CE] T9 已接入: _mix_and_send_locked 空闲态心跳轮转"
             " → ce_heartbeat_due/plan/rotate")


def _hook_xy_ref_build(engine):
    """H3-1: build_xy_reference_map 纯数值映射段 → ce_xy_ref_build（T8 内核,
    fuzz 10 万次位级一致, ce_fuzz_xyref.py）。

    注意: 任务书中的 ce_xy_reference（_apply_xy_reference 变换内核）不存在——
    DLL/C 源只有 ce_xy_ref_build（参考表构建内核）。flush 路径的旋转变换
    （_apply_xy_reference_rotation: 旋转/静止指针/16bit 写）无 C 内核, 保持
    Python（"无 xy refs 返回同对象"快路径原样保留）。本 hook 替换的是构建侧
    核心数学段: 地址/fine/角度分支/y 符号算术; Python 边界（mode 归一化/
    通道别名表/_safe_int/_safe_float/键存在性/XYReference 对象模型）保留。
    base_angle_deg 为变换所需的独立基准分量, C 不输出 → Python 按角度分支
    同式复算（fuzz 未覆盖该字段, 本 hook 的 A/B 探针全字段直比）。
    2026-08-04 同步（原版 3e1eaf0/dbe8922）: C 内核无 motor_z/uniform_world
    列 → Z 扇面字段（z_addr/z_fine_addr/motor_ranges→z_min/z_max/
    z_angle_deg）与 uniform_world 统一化（uniform 台 angle_deg = 0+偏移+
    全局, 不含 base）在 Python 侧逐位复刻原版语义（至冬7.0 265/265 全字段
    直比一致, ce_probe_h3_xyref_audio_ab.py）。
    """
    from src.engine import xy_reference as xyr
    from src.network import network_manager as nm_mod

    orig = nm_mod.build_xy_reference_map

    def ce_build_xy_reference_map(project, topview_state=None):
        try:
            state = (topview_state if isinstance(topview_state, dict)
                     else xyr._as_attr(project, "topview_state", {}) or {})
            raw_refs = state.get("xy_reference_by_fixture", {}) if isinstance(state, dict) else {}
            if not isinstance(raw_refs, dict):
                raw_refs = {}
            # ★ 性能(2026-08-05, 与原版 build_xy_reference_map 同步): 未配置任何
            #   同步(空 map)时, 全量工程(至冬7.0=11382 台)逐台扫行建 C 输入是纯
            #   浪费(实测 ~163ms/次, 拖动提交链每提交 2 次)——空 map 下每台
            #   cfg 恒为空 → mode=0 → C 内核 out_valid 全 0 → 输出恒为 {} ,
            #   直接短路, 逐字节不变。
            if not raw_refs:
                return {}
            g = (xyr._safe_float(state.get("xy_reference_global_angle_deg", 0.0), 0.0)
                 if isinstance(state, dict) else 0.0)
            defs = {str(xyr._as_attr(fd, "id", "")): fd
                    for fd in (xyr._as_attr(project, "fixture_defs", []) or [])
                    if xyr._as_attr(fd, "id", None)}
            rows = []
            for fi in (xyr._as_attr(project, "fixtures", []) or []):
                fid = str(xyr._as_attr(fi, "id", ""))
                if not fid:
                    continue
                cfg = raw_refs.get(fid, {})
                if not isinstance(cfg, dict):
                    continue
                fd = defs.get(str(xyr._as_attr(fi, "def_id", "")))
                channels = list(xyr._as_attr(fd, "channels", []) or []) if fd is not None else []
                offsets = xyr._channel_offsets(channels)
                rows.append((
                    fid,
                    xyr._safe_int(xyr._as_attr(fi, "universe", 1), 1),
                    xyr._safe_int(xyr._as_attr(fi, "start_channel", 1), 1),
                    1 if xyr.normalize_reference_mode(cfg.get("mode")) == xyr.SYNC_MODE else 0,
                    1 if xyr.MOTOR_X in offsets else 0,
                    1 if xyr.MOTOR_Y in offsets else 0,
                    offsets.get(xyr.MOTOR_X, -1),
                    offsets.get(xyr.MOTOR_Y, -1),
                    offsets.get(xyr.MOTOR_X_FINE, -1),
                    offsets.get(xyr.MOTOR_Y_FINE, -1),
                    1 if ("physical_angle_deg" in cfg or "compensation_angle_deg" in cfg) else 0,
                    1 if ("base_angle_deg" in cfg or "angle" in cfg) else 0,
                    1 if "angle_deg" in cfg else 0,
                    1 if "angle_offset_deg" in cfg else 0,
                    xyr._safe_float(cfg.get("physical_angle_deg", cfg.get("compensation_angle_deg", 0.0)), 0.0),
                    xyr._safe_float(cfg.get("base_angle_deg", cfg.get("angle", 0.0)), 0.0),
                    xyr._safe_float(cfg.get("angle_deg"), 0.0),
                    xyr._safe_float(cfg.get("angle_offset_deg", 0.0), 0.0),
                    xyr._safe_float(cfg.get("y_axis_sign", 1.0), 1.0),
                    # ★ 2026-08-04 Z 扇面 + 圆排统一化列（Python 侧, 不进 C）:
                    offsets.get(xyr.MOTOR_Z, -1),
                    offsets.get(xyr.MOTOR_Z_FINE, -1),
                    cfg.get("uniform_world_angle", False),
                    cfg.get("z_angle_deg", 0.0),
                    xyr._as_attr(fd, "motor_ranges", {}) or {},
                    # ★ 尖点 X-only 一对一硬修（2026-08-05 cda41ce 原版语义
                    #   同步）：落叶头尖点对（域25 ch464 / 域2 ch331, 5ch 全能
                    #   卡一维仅 motor_x）由同步显式加入（cfg x_only_align）。
                    #   C 内核 ok 条件要求 has_y → 该两台 out_valid=0 被静默
                    #   丢弃 → CE 挂载时修复失效。加列供 Python 侧补建
                    #   （y_addr=x_addr 自映射, 变换侧 dy 恒 0, 只旋转 motor_x）。
                    1 if bool(cfg.get("x_only_align", False)) else 0,
                    # ★ rot_y 列（2026-08-09 6746eaa 引入）：2D XY 曾按
                    #   install_angle=fixture.rot_y 分流, 用户实测「同步跟不同步
                    #   完全一样」后 673e9f1 回退（2D XY 与 3D 同用 cfg base=
                    #   锚点分簇切线角, 「原始基准跟三维一模一样」）——本列仅
                    #   为 C 内核列索引兼容保留, Python 侧不再消费。
                    xyr._safe_float(xyr._as_attr(fi, "rot_y", 0.0), 0.0),
                ))
            n = len(rows)
            if n == 0:
                return orig(project, topview_state)
            _I4P = ctypes.POINTER(ctypes.c_int32)
            _I8P = ctypes.POINTER(ctypes.c_int64)
            _D8P = ctypes.POINTER(ctypes.c_double)
            uni = np.array([r[1] for r in rows], np.int32)
            start = np.array([r[2] for r in rows], np.int64)
            mode = np.array([r[3] for r in rows], np.int32)
            hasx = np.array([r[4] for r in rows], np.int32)
            hasy = np.array([r[5] for r in rows], np.int32)
            xoff = np.array([r[6] for r in rows], np.int32)
            yoff = np.array([r[7] for r in rows], np.int32)
            xfoff = np.array([r[8] for r in rows], np.int32)
            yfoff = np.array([r[9] for r in rows], np.int32)
            flags = np.array([r[10] | (r[11] << 1) | (r[12] << 2) | (r[13] << 3)
                              for r in rows], np.int32)
            vmain = np.array([r[14] for r in rows], np.float64)
            vbase = np.array([r[15] for r in rows], np.float64)
            vang = np.array([r[16] for r in rows], np.float64)
            voff = np.array([r[17] for r in rows], np.float64)
            ysraw = np.array([r[18] for r in rows], np.float64)
            out_valid = np.zeros(n, np.int32)
            out_x = np.zeros(n, np.int32)
            out_y = np.zeros(n, np.int32)
            out_xf = np.zeros(n, np.int32)
            out_yf = np.zeros(n, np.int32)
            out_ang = np.zeros(n, np.float64)
            out_ys = np.zeros(n, np.float64)
            engine.ce_xy_ref_build(
                uni.ctypes.data_as(_I4P), start.ctypes.data_as(_I8P),
                mode.ctypes.data_as(_I4P), hasx.ctypes.data_as(_I4P),
                hasy.ctypes.data_as(_I4P),
                xoff.ctypes.data_as(_I4P), yoff.ctypes.data_as(_I4P),
                xfoff.ctypes.data_as(_I4P), yfoff.ctypes.data_as(_I4P),
                flags.ctypes.data_as(_I4P),
                vmain.ctypes.data_as(_D8P), vbase.ctypes.data_as(_D8P),
                vang.ctypes.data_as(_D8P), voff.ctypes.data_as(_D8P),
                ysraw.ctypes.data_as(_D8P),
                ctypes.c_double(g), n,
                out_valid.ctypes.data_as(_I4P), out_x.ctypes.data_as(_I4P),
                out_y.ctypes.data_as(_I4P), out_xf.ctypes.data_as(_I4P),
                out_yf.ctypes.data_as(_I4P),
                out_ang.ctypes.data_as(_D8P), out_ys.ctypes.data_as(_D8P),
            )
            _XYREF_CNT[0] += 1
            by_universe = {}
            for j in range(n):
                if not out_valid[j]:
                    continue
                f1, f2, f3 = rows[j][10], rows[j][11], rows[j][12]
                if f1:
                    base_ang = vmain[j]
                elif f2:
                    base_ang = vbase[j]
                elif f3:
                    base_ang = vang[j]
                else:
                    base_ang = 0.0
                # ★ Z 扇面基准 + 圆排统一化（2026-08-04 原版 3e1eaf0/dbe8922
                #   同步, 逐位复刻原版语义）:
                #   - uniform 台 angle_deg = 0 + 偏移 + 全局（不含 base——
                #     静止波束 = 安装 + (目标−安装) = 目标, 全组同一世界方向;
                #     C 内核无 uniform 语义 → 仅 f1/f2 分支需 Python 覆写,
                #     f64 相加左序与原版 (0.0 if uniform else base)+off+g 一致）
                #   - z_addr/z_fine_addr = start-1 + motor_z 偏移（0..511 边界）
                #   - z_min/z_max 取 fd.motor_ranges[MOTOR_Z]（缺省 ±180）
                #   - z_angle_deg = state 扇面角（缺省 cfg 快照） + 全局角
                z_addr = None
                z_fine_addr = None
                if rows[j][19] >= 0:
                    addr = (rows[j][2] - 1) + rows[j][19]
                    if 0 <= addr < 512:
                        z_addr = addr
                    if rows[j][20] >= 0:
                        addr = (rows[j][2] - 1) + rows[j][20]
                        if 0 <= addr < 512:
                            z_fine_addr = addr
                # ★ uniform 守卫（2026-08-10 63993c0 原版语义同步）: Python 版
                #   uniform_world = cfg_flag AND (z_addr is not None or x_only),
                #   x_only = MOTOR_Y 不在 offsets AND cfg x_only_align
                #   （= rows[5]==0 AND rows[24]）—— 2-Axis Jet（无 motor_z、
                #   非 x_only）即使 cfg uniform_world_angle 误为 True 也强制走
                #   非统一分支（rad = base + θ, base = −真实安装 → 世界 = θ
                #   全组统一）; 三维扇面（z_addr 非 None）/尖点 X-only（rows[24]
                #   且无 motor_y）保持 cfg 原判定零变化。
                uniform = bool(rows[j][21]) and (
                    z_addr is not None or (not rows[j][5] and bool(rows[j][24])))
                ang = float(out_ang[j])
                if uniform and (f1 or f2):
                    ang = 0.0 + rows[j][17] + g
                z_min_deg = xyr.DEFAULT_Z_MIN_DEG
                z_max_deg = xyr.DEFAULT_Z_MAX_DEG
                if z_addr is not None:
                    m_ranges = rows[j][23]
                    rng = m_ranges.get(xyr.MOTOR_Z) if isinstance(m_ranges, dict) else None
                    if rng:
                        lo = xyr._safe_float(rng[0], xyr.DEFAULT_Z_MIN_DEG)
                        hi = xyr._safe_float(rng[1], xyr.DEFAULT_Z_MAX_DEG)
                        if hi > lo:
                            z_min_deg, z_max_deg = lo, hi
                z_angle_deg = xyr._safe_float(
                    state.get("z_angle_deg", rows[j][22]), 0.0) + g
                # ★ 静态旋转参数预计算（2026-08-12 同步 Python 版候选点#12）:
                #   mode_is_sync/cos_rad/sin_rad/y_sign_eff 纯函数于 frozen 字段,
                #   变换侧（Python _apply_xy_reference_rotation）优先用缓存,
                #   None 回退旧公式。公式与 Python build_xy_reference_map
                #   逐字同式（uniform: rad=base−angle / 否则 rad=angle）。
                # ★ 非有限角度守卫（2026-08-12, 与 Python 版同式）: cfg 角度可
                #   为 NaN/±inf（_safe_float 原样放行）, math.cos(radians(±inf))
                #   抛 ValueError → 角度任一非有限时 4 缓存字段全 None
                #   （变换侧回退旧公式, 与 Python 版行为一致）。
                _ce_ang_finite = math.isfinite(float(base_ang)) \
                    and math.isfinite(float(ang))
                if uniform:
                    _ce_rad = math.radians(float(base_ang) - float(ang))
                else:
                    _ce_rad = math.radians(float(ang))
                if _ce_ang_finite:
                    _ce_mode_is_sync = xyr.normalize_reference_mode(xyr.SYNC_MODE) == xyr.SYNC_MODE
                    _ce_cos_rad = math.cos(_ce_rad)
                    _ce_sin_rad = math.sin(_ce_rad)
                    _ce_y_sign_eff = float(out_ys[j])
                else:
                    _ce_mode_is_sync = None
                    _ce_cos_rad = None
                    _ce_sin_rad = None
                    _ce_y_sign_eff = None
                by_universe.setdefault(int(uni[j]), []).append(xyr.XYReference(
                    fixture_id=rows[j][0], universe=int(uni[j]),
                    x_addr=int(out_x[j]), y_addr=int(out_y[j]),
                    x_fine_addr=None if int(out_xf[j]) < 0 else int(out_xf[j]),
                    y_fine_addr=None if int(out_yf[j]) < 0 else int(out_yf[j]),
                    angle_deg=ang,
                    base_angle_deg=float(base_ang),
                    y_axis_sign=float(out_ys[j]),
                    mode=xyr.SYNC_MODE,
                    z_addr=z_addr,
                    z_fine_addr=z_fine_addr,
                    z_min_deg=z_min_deg,
                    z_max_deg=z_max_deg,
                    z_angle_deg=z_angle_deg,
                    uniform_world=uniform,
                    mode_is_sync=_ce_mode_is_sync,
                    cos_rad=_ce_cos_rad,
                    sin_rad=_ce_sin_rad,
                    y_sign_eff=_ce_y_sign_eff,
                ))
            # ★ 尖点 X-only 补建（2026-08-05 cda41ce 原版语义同步）：C 内核
            #   ok 条件 = mode && has_x && has_y → x_only 行 out_valid=0 被
            #   丢弃。原版 build_xy_reference_map 对 x_only_align 设备接受进
            #   同步表（y_addr=x_addr 自映射, y_fine_addr=x_fine_addr），此处
            #   逐位复刻补建（仅命中 x_only_align 行, 其余零变化; 数量 ≤2,
            #   无性能影响）。
            for j in range(n):
                if out_valid[j]:
                    continue
                if not rows[j][24] or not rows[j][3] or not rows[j][4]:
                    continue
                x_addr = (rows[j][2] - 1) + rows[j][6]
                if not 0 <= x_addr < 512:
                    continue
                x_fine = None
                if rows[j][8] >= 0:
                    x_fine = (rows[j][2] - 1) + rows[j][8]
                    if not 0 <= x_fine < 512:
                        x_fine = None
                f1, f2, f3 = rows[j][10], rows[j][11], rows[j][12]
                if f1:
                    base_ang = rows[j][14]
                elif f2:
                    base_ang = rows[j][15]
                elif f3:
                    base_ang = rows[j][16]
                else:
                    base_ang = 0.0
                # ★ uniform 守卫等价性（2026-08-10 63993c0 同步）：本循环只
                #   命中 rows[24]=1 且 rows[5]=0（x_only_align 且无 motor_y）
                #   行 → x_only 恒真 → uniform = cfg 原判定, 与带守卫版本
                #   逐位等价。
                uniform = bool(rows[j][21])
                ang = (0.0 if uniform else base_ang) + rows[j][17] + g
                z_addr = None
                z_fine_addr = None
                if rows[j][19] >= 0:
                    z_addr = (rows[j][2] - 1) + rows[j][19]
                    if 0 <= z_addr < 512:
                        if rows[j][20] >= 0:
                            z_fine_addr = (rows[j][2] - 1) + rows[j][20]
                            if not 0 <= z_fine_addr < 512:
                                z_fine_addr = None
                    else:
                        z_addr = None
                z_min_deg = xyr.DEFAULT_Z_MIN_DEG
                z_max_deg = xyr.DEFAULT_Z_MAX_DEG
                if z_addr is not None:
                    m_ranges = rows[j][23]
                    rng = m_ranges.get(xyr.MOTOR_Z) if isinstance(m_ranges, dict) else None
                    if rng:
                        lo = xyr._safe_float(rng[0], xyr.DEFAULT_Z_MIN_DEG)
                        hi = xyr._safe_float(rng[1], xyr.DEFAULT_Z_MAX_DEG)
                        if hi > lo:
                            z_min_deg, z_max_deg = lo, hi
                z_angle_deg = xyr._safe_float(
                    state.get("z_angle_deg", rows[j][22]), 0.0) + g
                # ★ 非有限角度守卫（2026-08-12, 与 Python 版同式）: cfg 角度可
                #   为 NaN/±inf, math.cos(radians(±inf)) 抛 ValueError → 角度
                #   任一非有限时 4 缓存字段全 None（变换侧回退旧公式）。
                _ce_ang_finite = math.isfinite(float(base_ang)) \
                    and math.isfinite(float(ang))
                if uniform:
                    _ce_rad = math.radians(float(base_ang) - float(ang))
                else:
                    _ce_rad = math.radians(float(ang))
                if _ce_ang_finite:
                    _ce_mode_is_sync = xyr.normalize_reference_mode(xyr.SYNC_MODE) == xyr.SYNC_MODE
                    _ce_cos_rad = math.cos(_ce_rad)
                    _ce_sin_rad = math.sin(_ce_rad)
                    _ce_y_sign_eff = (-1.0 if float(rows[j][18]) < 0.0 else 1.0)
                else:
                    _ce_mode_is_sync = None
                    _ce_cos_rad = None
                    _ce_sin_rad = None
                    _ce_y_sign_eff = None
                by_universe.setdefault(int(rows[j][1]), []).append(xyr.XYReference(
                    fixture_id=rows[j][0], universe=int(rows[j][1]),
                    x_addr=x_addr, y_addr=x_addr,
                    x_fine_addr=x_fine, y_fine_addr=x_fine,
                    angle_deg=ang,
                    base_angle_deg=float(base_ang),
                    # ★ y_axis_sign 归一化（2026-08-10 与 Python 版 L287 对齐）:
                    #   原版 -1.0 if cfg<0 else 1.0（{-1,1} 归一, 主循环经 C 内核
                    #   out_ys 已一致）; x-only 补建原用原始值, cfg 不在 {-1,1}
                    #   时发散 → 同式归一化。
                    y_axis_sign=(-1.0 if float(rows[j][18]) < 0.0 else 1.0),
                    mode=xyr.SYNC_MODE,
                    z_addr=z_addr,
                    z_fine_addr=z_fine_addr,
                    z_min_deg=z_min_deg,
                    z_max_deg=z_max_deg,
                    z_angle_deg=z_angle_deg,
                    uniform_world=uniform,
                    mode_is_sync=_ce_mode_is_sync,
                    cos_rad=_ce_cos_rad,
                    sin_rad=_ce_sin_rad,
                    y_sign_eff=_ce_y_sign_eff,
                ))
            return by_universe
        except Exception:
            _CE_EXC_CNT[0] += 1   # H1: H3-1 xyref C 内核异常 → 失败计数
            return orig(project, topview_state)

    nm_mod.build_xy_reference_map = ce_build_xy_reference_map
    log.info("[CE] H3 已接入: build_xy_reference_map → ce_xy_ref_build"
             "（_apply_xy_reference 变换无 C 内核, 保持 Python）")


def _hook_smoother(engine):
    """H3-2: OutputSmoother.process → ce_smoother_apply（P7 内核, 状态全在 C）。

    - 状态迁移到 C: ce_smoother_create(1024) 懒创建, ce_smoother_apply 每帧
      逐位复刻 numpy 向量化语义（fuzz ≥10 万步全状态逐位一致, ce_fuzz_smoother.py）;
      reset_states/reset_universe → C 复位（同时清 Python _states 保持回退干净）。
    - 首帧语义（返回 raw 原样拷贝, ret=0）逐位保持; 旁通 is_bypass() 快路径
      保留 Python（不进 C）; 失败（ret<0/异常）→ 销毁 C ctx 回退原版。
    - process_single（UI 预览路径）: 无任何调用者（grep 全仓仅定义）; C 模式下
      用 ce_smoother_dump_state 读 C 状态 + 原版标量公式（f64, 与 Python
      process_single 同式）, 不写回 C 状态（无状态写 API; 写回需整帧 apply,
      deadband=0 时 rint 目标会产生 ±0.5 目标漂移污染其他通道 → 不做）。
      状态单调性差异（多次调用不推进时间）在零调用者下不可观测。
    """
    import time as _t
    from src.engine import output_smoother as osm

    orig_process = osm.OutputSmoother.process
    orig_single = osm.OutputSmoother.process_single
    orig_reset = osm.OutputSmoother.reset_states
    orig_reset_uni = osm.OutputSmoother.reset_universe
    _P8 = ctypes.POINTER(ctypes.c_uint8)
    _P32 = ctypes.POINTER(ctypes.c_float)
    _P64 = ctypes.POINTER(ctypes.c_double)
    _CURVE_IDS = {"linear": 0, "ease_in": 1, "ease_out": 2,
                  "ease_in_out": 3, "exponential": 4}

    def _ce_ctx_for(self):
        ctx = getattr(self, "_ce_smoother_ctx", None)
        if ctx is not None:
            return ctx
        if getattr(self, "_ce_smoother_failed", False):
            return None
        try:
            ctx = engine.ce_smoother_create(1024)
            if not ctx:
                self._ce_smoother_failed = True
                return None
            self._ce_smoother_ctx = ctx
            self._ce_smoother_touched = set()
            return ctx
        except Exception:
            _CE_EXC_CNT[0] += 1   # H1: H3-2 smoother C 内核异常 → 失败计数
            self._ce_smoother_failed = True
            return None

    def _ce_smoother_disable(self, ctx):
        try:
            engine.ce_smoother_destroy(ctx)
        except Exception:
            pass
        self._ce_smoother_ctx = None
        self._ce_smoother_failed = True

    def ce_process(self, universe, raw_data):
        if self.is_bypass():
            return raw_data
        ctx = _ce_ctx_for(self)
        if ctx is None:
            return orig_process(self, universe, raw_data)
        try:
            if (raw_data.dtype != np.uint8 or raw_data.ndim != 1
                    or raw_data.shape[0] != 512):
                return orig_process(self, universe, raw_data)
            attack_s = max(0.001, self.get_param("attack_ms") / 1000.0)
            release_s = max(0.001, self.get_param("release_ms") / 1000.0)
            smoothing = float(self.get_param("smoothing"))
            inertia = float(self.get_param("inertia"))
            overshoot = float(self.get_param("overshoot"))
            deadband = int(self.get_param("deadband"))
            curve = _CURVE_IDS.get(
                getattr(self._preset, "curve", "linear"), 0)
            raw = np.ascontiguousarray(raw_data, np.uint8)
            out = np.empty(512, np.uint8)
            ret = engine.ce_smoother_apply(
                ctx, int(universe), _t.monotonic(),
                raw.ctypes.data_as(_P8),
                attack_s, release_s, smoothing, inertia, overshoot, deadband,
                curve, out.ctypes.data_as(_P8))
            if ret < 0:
                return orig_process(self, universe, raw_data)
            _SMOOTHER_CNT[0] += 1
            try:
                getattr(self, "_ce_smoother_touched").add(int(universe))
            except Exception:
                pass
            return out
        except Exception:
            _CE_EXC_CNT[0] += 1   # H1: H3-2 smoother C 内核异常 → 失败计数
            _ce_smoother_disable(self, ctx)
            return orig_process(self, universe, raw_data)

    def ce_reset_states(self):
        ctx = getattr(self, "_ce_smoother_ctx", None)
        if ctx is not None:
            try:
                engine.ce_smoother_reset(ctx)
            except Exception:
                _CE_EXC_CNT[0] += 1   # H1: H3-2 smoother reset C 内核异常 → 失败计数
                _ce_smoother_disable(self, ctx)
        orig_reset(self)

    def ce_reset_universe(self, universe):
        ctx = getattr(self, "_ce_smoother_ctx", None)
        if ctx is not None:
            try:
                engine.ce_smoother_reset_universe(ctx, int(universe))
            except Exception:
                _CE_EXC_CNT[0] += 1   # H1: H3-2 smoother reset_uni C 内核异常 → 失败计数
                _ce_smoother_disable(self, ctx)
        orig_reset_uni(self, universe)

    def ce_process_single(self, universe, channel, target):
        if not self._enabled or getattr(self._preset, "id", "bypass") == "bypass":
            return target
        ctx = _ce_ctx_for(self)
        if ctx is None:
            return orig_single(self, universe, channel, target)
        try:
            now = _t.monotonic()
            uni = int(universe)
            touched = getattr(self, "_ce_smoother_touched", set())
            cur = np.zeros(512, np.float32)
            tgt = np.zeros(512, np.float32)
            vel = np.zeros(512, np.float32)
            lu = np.full(512, now, np.float64)
            ltc = np.full(512, now, np.float64)
            if uni in touched:
                engine.ce_smoother_dump_state(
                    ctx, uni,
                    cur.ctypes.data_as(_P32), tgt.ctypes.data_as(_P32),
                    vel.ctypes.data_as(_P32), lu.ctypes.data_as(_P64),
                    ltc.ctypes.data_as(_P64))
            touched.add(uni)
            ch = int(channel)
            deadband = int(self.get_param("deadband"))
            if abs(target - float(tgt[ch])) > deadband:
                ltc[ch] = now
                tgt[ch] = float(target)
            dt = max(0.001, now - float(lu[ch]))
            lu[ch] = now
            diff = float(tgt[ch]) - float(cur[ch])
            if abs(diff) < 0.5:
                cur[ch] = tgt[ch]
                return max(0, min(255, int(round(float(cur[ch])))))
            going_up = diff > 0
            attack_s = max(0.001, self.get_param("attack_ms") / 1000.0)
            release_s = max(0.001, self.get_param("release_ms") / 1000.0)
            time_const = attack_s if going_up else release_s
            smoothing = self.get_param("smoothing")
            if smoothing > 0:
                alpha = 1.0 - smoothing
                new_val = float(cur[ch]) + alpha * diff * min(1.0, dt / time_const)
            else:
                new_val = float(cur[ch]) + diff * min(1.0, dt / time_const)
            new_val = max(0.0, min(255.0, new_val))
            return max(0, min(255, int(round(new_val))))
        except Exception:
            _CE_EXC_CNT[0] += 1   # H1: H3-2 smoother single C 内核异常 → 失败计数
            _ce_smoother_disable(self, ctx)
            return orig_single(self, universe, channel, target)

    osm.OutputSmoother.process = ce_process
    osm.OutputSmoother.process_single = ce_process_single
    osm.OutputSmoother.reset_states = ce_reset_states
    osm.OutputSmoother.reset_universe = ce_reset_universe
    log.info("[CE] H3 已接入: OutputSmoother.process → ce_smoother_apply（状态全 C）")


def _hook_interlock(engine):
    """H3-3: SafetyInterlock.process → ce_interlock_process（T6 内核, 状态数组
    in/out 由调用方持有; fuzz 逐帧全字段位级一致, ce_fuzz_interlock.py）。

    - is_armed=False（DISARMED 直通原对象）/ BLACKOUT 全零 快路径保留 Python。
    - 约束剖面数组（min/max/rate/safe_default）与 states/counts/has_prev/
      prev_len/prevs 全部由 hook 组装; C 就地更新后同步回 _states/
      _violation_count/_last_values（prev 存 C 侧 scratch 缓冲, 与 Python
      每帧 .copy() 语义一致）。帧长变化/零通道/越界 start 语义与 Python 相同。
    - 每帧 warning 日志（2s 节流）在 C 路径不产生（观测性差异, 非字节语义）;
      BLACKOUT critical 日志保留。
    """
    from src.engine import safety_interlock as si

    orig = si.SafetyInterlock.process
    _PF = ctypes.POINTER(ctypes.c_float)
    _PI = ctypes.POINTER(ctypes.c_int)
    _PU = ctypes.POINTER(ctypes.c_uint8)

    def _ce_profiles_in_domain(constraints):
        """C 路径门控: 约束剖面全部落在 round_half_even 安全域（|v| < 2^63）。

        ce_engine.c 终链 (int64_t)floor(v) 对 |v| ≥ 2^63 / NaN / inf 是 UB
        （MSVC 产出 0, numpy 产出 255）——ce_fuzz_interlock.py 现跑即败,
        该域外 C 不可等价。生产管线 data 恒 uint8（0..255）且剖面为有限值 →
        域内 C 逐位等价（探针验证）; 域外回退原版。
        """
        for c in constraints:
            for arr in (c.min, c.max, c.safe_default, c.max_change_rate):
                a = np.asarray(arr, np.float32)
                if a.size and not np.all(np.abs(a) < 9.2e18):
                    return False
        return True

    def ce_process(self, universe, data):
        if self._global_state == si.InterlockState.DISARMED:
            return data
        if self._global_state == si.InterlockState.BLACKOUT:
            return np.zeros_like(data)
        try:
            if data.ndim != 1 or data.dtype != np.uint8:
                return orig(self, universe, data)
            # 只取当前 universe 的约束，避免每帧对全量约束做 domain 扫描和数组组装。
            relevant = self._constraints_by_universe.get(int(universe), ())
            if not relevant:
                return orig(self, universe, data)
            if not _ce_profiles_in_domain([c for _, c in relevant]):
                return orig(self, universe, data)
            frame_len = int(data.size)
            orig_indices = [idx for idx, _c in relevant]
            constraints = [c for _idx, c in relevant]
            n = len(constraints)
            scratch_map = getattr(self, "_ce_interlock_scratch_by_uni", None)
            if scratch_map is None:
                scratch_map = {}
                self._ce_interlock_scratch_by_uni = scratch_map
            scratch = scratch_map.get(int(universe))
            if scratch is None or len(scratch) != n:
                scratch = [np.empty(max(1, int(c.num_channels)), np.float32)
                           for c in constraints]
                scratch_map[int(universe)] = scratch
            cu = np.array([c.universe for c in constraints], np.int32)
            cs = np.array([c.start_channel for c in constraints], np.int32)
            # HOOK_INTERLOCK.md HIGH（安全关键）: 负 start_channel → C 内核
            # 越界读写（slice = result + start 指向前方内存）→ 堆破坏进程崩溃
            # （rc=0xC0000374, 3/3 复现）; 原版 ValueError 被生产 except 吞 =
            # 安全失败, C 版变崩溃失败 → 门控回退原版（安全失败语义一致）。
            if np.any(cs < 0):
                return orig(self, universe, data)
            cn = np.array([c.num_channels for c in constraints], np.int32)
            states_before = [int(self._states.get(oi, 0)) for oi in orig_indices]
            states = np.array(states_before, np.int32)
            counts_before = [int(self._violation_count.get(oi, 0))
                             for oi in orig_indices]
            counts = np.array(counts_before, np.int32)
            has_prev = np.zeros(n, np.int32)
            prev_len = np.zeros(n, np.int32)
            p_min = (_PF * n)()
            p_max = (_PF * n)()
            p_rate = (_PF * n)()
            p_def = (_PF * n)()
            p_prev = (_PF * n)()
            for i, c in enumerate(constraints):
                p_min[i] = np.ascontiguousarray(c.min, np.float32).ctypes.data_as(_PF)
                p_max[i] = np.ascontiguousarray(c.max, np.float32).ctypes.data_as(_PF)
                p_rate[i] = np.ascontiguousarray(c.max_change_rate, np.float32).ctypes.data_as(_PF)
                p_def[i] = np.ascontiguousarray(c.safe_default, np.float32).ctypes.data_as(_PF)
                pv = self._last_values.get(orig_indices[i])
                if pv is not None:
                    has_prev[i] = 1
                    prev_len[i] = int(pv.size)
                    if pv.size:
                        scratch[i][:pv.size] = pv.astype(np.float32, copy=False)
                p_prev[i] = scratch[i].ctypes.data_as(_PF)
            result = np.ascontiguousarray(data.astype(np.float32))
            out = np.empty(frame_len, np.uint8)
            blackout = ctypes.c_int(0)
            engine.ce_interlock_process(
                result.ctypes.data_as(_PF), frame_len, int(universe),
                n, cu.ctypes.data_as(_PI), cs.ctypes.data_as(_PI),
                cn.ctypes.data_as(_PI),
                p_min, p_max, p_rate, p_def,
                states.ctypes.data_as(_PI), counts.ctypes.data_as(_PI),
                has_prev.ctypes.data_as(_PI), prev_len.ctypes.data_as(_PI),
                p_prev,
                int(self._warning_threshold), int(self._softlimit_threshold),
                int(self._hardstop_threshold),
                float(self._blackout_device_ratio),
                ctypes.byref(blackout), out.ctypes.data_as(_PU))
            _INTERLOCK_CNT[0] += 1
            for i in range(n):
                oi = orig_indices[i]
                if (cu[i] == universe and states_before[i] != 0
                        and cs[i] < frame_len):
                    self._states[oi] = si.InterlockState(int(states[i]))
                    cc = int(counts[i])
                    cb = counts_before[i]
                    # 原版写入规则: 违规(+1)/衰减(-1, 可为 0)才写; 0→0 不写
                    # （_violation_count 键缺失与存 0 语义不同, 探针已覆盖）。
                    if cc == cb + 1 or cc == cb - 1:
                        self._violation_count[oi] = cc
                    else:
                        self._violation_count.pop(oi, None)
                    plen = int(prev_len[i])
                    self._last_values[oi] = scratch[i][:plen].copy()
            # 全局 Blackout 判定必须与 Python 参考一致：Python 用「全部约束」
            # 的 HARD_STOP 占比，而 C 只拿到当前 universe 的约束子集，直接用
            # C 的 blackout 标志会在多 universe 时比例算错（1ebb924 后暴露）。
            total = len(self._constraints)
            if total > 0:
                hs_count = sum(
                    1 for s in self._states.values()
                    if s >= si.InterlockState.HARD_STOP
                )
                if hs_count / total >= self._blackout_device_ratio:
                    self._global_state = si.InterlockState.BLACKOUT
                    si._log.critical(
                        "SafetyInterlock: BLACKOUT triggered (%d/%d devices in HARD_STOP)",
                        hs_count, total)
                    return np.zeros_like(data)
            return out
        except Exception:
            _CE_EXC_CNT[0] += 1   # H1: H3-3 interlock C 内核异常 → 失败计数
            return orig(self, universe, data)

    si.SafetyInterlock.process = ce_process
    log.info("[CE] H3 已接入: SafetyInterlock.process → ce_interlock_process")


def _hook_audio(engine):
    """Use C frame energy with the shared current spectrum/onset contract.

    Legacy ce_audio_bands remains an unchanged public C ABI, but its mean-bin
    units are not used by current audio presets. normalized_bands counts real
    frames converted by the common helper; bands counts only the legacy C API.
    File prefetch retains one bounded pure-feature window. Onset/BPM/pitch are
    consumed in caller order, including explicit frame_at positions.
    """
    from src.audio import music_structure_analyzer as msa
    from src.ui import audio_driver as ad
    from src.audio.audio_spectrum import hann_window, normalized_bands
    from src.audio.audio_onset_runtime import analyze_onset, onset_source_snapshot

    import threading as _threading

    orig_file = ad.AudioAnalyzer._analyze_file_at_position
    orig_mic = ad.AudioAnalyzer._analyze_mic_buffer
    orig_flat = msa._spectral_flatness
    _FP = ctypes.POINTER(ctypes.c_float)
    _DP = ctypes.POINTER(ctypes.c_double)

    _BATCH_ON = os.environ.get("QASCADE_CE_AUDIO_BATCH", "1") != "0"
    _BATCH_MIN_STEP = 0.1     # 离线扫描步长 0.25; 实时 tick 步长 ≈ 0.025 不触发
    _BATCH_WINDOW = 128       # 每预取窗口帧数（预取 32s 步长, 主线程远追不上）
    def _ce_frame_energy(buf, gain):
        """buf f32 1-D → (rms, peak) float; 不满足 C 契约返回 None（走原版）。

        C 契约 = NEP50 弱标量语义（f32 缓冲 × python float/int 或 np.float32
        标量 → f32 运算）; np.float64 标量会使原版上提升为 f64 数学, C 不可
        等价 → 回退原版。生产路径 set_gain 恒存 python float ✓ C 生效。
        """
        try:
            if buf.ndim != 1 or buf.dtype != np.float32:
                return None
            if not (type(gain) is float or type(gain) is int
                    or type(gain) is np.float32):
                return None
            n = int(buf.size)
            if n <= 0:
                return None
            b = np.ascontiguousarray(buf, np.float32)
            rms = ctypes.c_double(0.0)
            peak = ctypes.c_double(0.0)
            engine.ce_audio_frame_energy(
                b.ctypes.data_as(_FP), n, float(gain),
                ctypes.byref(rms), ctypes.byref(peak))
            _AUDIO_CNT["energy"] += 1
            return float(rms.value), float(peak.value)
        except Exception:
            _CE_EXC_CNT[0] += 1   # H1: H3-4 音频 energy C 内核异常 → 失败计数
            return None

    def _current_bands(fft, sr, size):
        bands = normalized_bands(fft, sr, size)
        _AUDIO_CNT["normalized_bands"] += 1 if fft.ndim == 1 else len(fft)
        return bands

    def _batch_compatible(buf, gain):
        return buf.ndim == 1 and buf.dtype == np.float32 and type(gain) in (float, int, np.float32)

    def _build_frame(self, pos, rms, peak, bands, *, fft, sample_source):
        """串行/批量共用帧组装（逐行同构原版: 起音→BPM→音高→AudioFrame）。"""
        onset_strength, is_beat = analyze_onset(
            self, fft, rms, pos, self._file_sr, sample_source=sample_source)
        pm, pconf, croot, cqual = self._lookup_pitch(pos)
        return ad.AudioFrame(
            t=pos, rms=min(1.0, rms), peak=min(1.0, peak),
            bands=bands, bpm=self._bpm_detector.bpm,
            beat_phase=self._bpm_detector.beat_phase,
            is_beat=is_beat, onset_strength=onset_strength,
            has_audio=True,
            pitch_midi=pm, pitch_confidence=pconf,
            chord_root=croot, chord_quality=cqual,
        )

    # ── 离线整曲烘焙批量预取 ──────────────────────────────────────────
    def _prefetch_worker(self, st, pos, step, gkey, *, gain, source):
        """Cache at most 128 pure frames; caller consumes onset in time order.

        The worker captures data/rate/gain/window at scheduling time. It never
        reads a replacement source or runs the stateful onset/BPM detector.
        """
        try:
            reference = st["caches"].get(gkey)
            if reference is None:
                return
            ckey = reference[1]
            file_data, sr, chunk = source
            starts = [int((pos + step * i) * sr) for i in range(1, _BATCH_WINDOW + 1)
                      if int((pos + step * i) * sr) < len(file_data)]
            nf = len(starts)
            if not nf:
                return
            buf2d = np.zeros((nf, chunk), np.float32)
            for j, start in enumerate(starts):
                segment = file_data[start:min(start + chunk, len(file_data))]
                buf2d[j, :len(segment)] = segment
            rms_a, peak_a = np.zeros(nf, np.float64), np.zeros(nf, np.float64)
            engine.ce_audio_frame_energy_batch(
                buf2d.ctypes.data_as(_FP), nf, chunk, float(gain),
                rms_a.ctypes.data_as(_DP), peak_a.ctypes.data_as(_DP))
            _AUDIO_CNT["energy"] += 1  # one real C batch invocation
            fft2d = np.abs(np.fft.rfft((buf2d * gain) * hann_window(chunk), axis=1))
            bands = _current_bands(fft2d, sr, chunk)
            if onset_source_snapshot(self, sr) != gkey:
                return
            reference = st["caches"].get(gkey)
            if reference is None or reference[1] is not ckey:
                return
            ckey.clear()
            for j in range(nf):
                ckey[pos + step * (j + 1)] = (
                    float(rms_a[j]), float(peak_a[j]), bands[j].tolist(), fft2d[j].copy())
        except Exception:
            _CE_EXC_CNT[0] += 1  # failures remain observable; caller uses serial


    def _batch_try(self, pos, sample_source):
        """批量预取调度: 缓存命中 → AudioFrame; 否则 → None（串行路径）。

        副作用: 模式检测状态 + 预取窗口调度（恒定步长 ≥ 0.1 连续两次激活;
        缓存键 = 原音源身份与 generation；文件对象保留至窗口释放）。
        """
        try:
            st = getattr(self, "_ce_batch_state", None)
            if st is None:
                st = {"last_pos": None, "step": None, "active": False,
                      "caches": {}, "next_prefetch": None, "worker": None}
                self._ce_batch_state = st
            delta = None if st["last_pos"] is None else pos - st["last_pos"]
            st["last_pos"] = pos
            if delta is not None and delta > 0 and delta >= _BATCH_MIN_STEP:
                if st["step"] is None:
                    st["step"] = delta
                    st["active"] = False
                elif abs(delta - st["step"]) < 1e-9:
                    if not st["active"]:
                        st["active"] = True   # 两次同步长 → 批量激活
                        st["next_prefetch"] = None
                else:
                    st["step"] = delta
                    st["active"] = False
            else:
                st["step"] = None
                st["active"] = False
            if not st["active"]:
                return None
            gain = self._gain
            if not _batch_compatible(self._file_data, gain):
                return None
            gkey = sample_source
            ref = st["caches"].get(gkey)
            if ref is None:
                st["caches"] = {}  # one source/window, no retired-file retention
                st["next_prefetch"] = None
                st["caches"][gkey] = (self._file_data, {})
                ref = st["caches"][gkey]
            entry = ref[1].pop(pos, None)
            if entry is not None:
                rms, peak, bands, fft = entry
                return _build_frame(self, pos, rms, peak, bands, fft=fft, sample_source=sample_source)
            # 未命中 → 追平水印则起新窗口（worker 未结束时等它收尾, 串行兜底）
            npf = st.get("next_prefetch")
            worker = st.get("worker")
            if npf is None or pos >= npf:
                if worker is None or not worker.is_alive():
                    st["next_prefetch"] = pos + _BATCH_WINDOW * st["step"]
                    st["worker"] = _threading.Thread(
                        target=_prefetch_worker,
                        args=(self, st, pos, st["step"], gkey),
                        kwargs={"gain": gain, "source": (self._file_data, self._file_sr, self._chunk)},
                        daemon=True, name="ce-audio-batch")
                    st["worker"].start()
            return None
        except Exception:
            return None

    def ce_file(self, pos=None):
        """_analyze_file_at_position 全方法复刻, C 能量 + 公共归一频谱/起音;
        离线烘焙恒定大步长 → 批量预取路径（输出逐位 = 串行）。"""
        try:
            sample_source = onset_source_snapshot(self, self._file_sr)
            pos = self._timeline_pos if pos is None else float(pos)
            if self._file_data is None or pos < 0:
                return ad.AudioFrame(t=pos)
            sample_start = int(pos * self._file_sr)
            sample_end = sample_start + self._chunk
            if sample_start >= len(self._file_data):
                return ad.AudioFrame(t=pos)
            if _BATCH_ON and engine.ce_audio_frame_energy_batch is not None:
                frame = _batch_try(self, pos, sample_source)
                if frame is not None:
                    _AUDIO_BATCH_CNT[0] += 1
                    return frame
            buf = self._file_data[sample_start:min(sample_end, len(self._file_data))]
            if len(buf) < self._chunk:
                buf = np.pad(buf, (0, self._chunk - len(buf)))
            ce = _ce_frame_energy(buf, self._gain)
            if ce is None:
                return orig_file(self, pos)
            rms, peak = ce
            # 原版能量段就地乘 gain —— FFT 频段计算用增益后缓冲（逐行同构）
            buf = buf * self._gain
            window = hann_window(len(buf))
            fft = np.abs(np.fft.rfft(buf * window))
            bands = _current_bands(fft, self._file_sr, len(buf)).tolist()
            return _build_frame(self, pos, rms, peak, bands, fft=fft, sample_source=sample_source)
        except Exception:
            _CE_EXC_CNT[0] += 1   # H1: H3-4 音频 file C 内核异常 → 失败计数
            return orig_file(self, pos)

    def ce_mic(self, t):
        """_analyze_mic_buffer 全方法复刻, C 能量 + 公共归一频谱/起音。"""
        try:
            sample_source = onset_source_snapshot(self, self._sr)
            with self._mic_lock:
                buf = self._mic_buffer.copy() if self._mic_buffer is not None else np.zeros(self._chunk)
            ce = _ce_frame_energy(buf, self._gain)
            if ce is None:
                return orig_mic(self, t)
            rms, peak = ce
            # 原版能量段就地乘 gain —— FFT 频段计算用增益后缓冲（逐行同构）
            buf = buf * self._gain
            fft = np.abs(np.fft.rfft(buf * hann_window(len(buf))))
            bands = _current_bands(fft, self._sr, len(buf)).tolist()
            onset_strength, is_beat = analyze_onset(
                self, fft, rms, t, self._sr, sample_source=sample_source)
            return ad.AudioFrame(
                t=t, rms=min(1.0, rms), peak=min(1.0, peak),
                bands=bands, bpm=self._bpm_detector.bpm,
                beat_phase=self._bpm_detector.beat_phase,
                is_beat=is_beat, onset_strength=onset_strength,
                has_audio=True,
            )
        except Exception:
            _CE_EXC_CNT[0] += 1   # H1: H3-4 音频 mic C 内核异常 → 失败计数
            return orig_mic(self, t)

    def ce_flatness(bands):
        try:
            n = len(bands)
            if n == 0:
                return 0.0
            arr = np.ascontiguousarray([float(b) for b in bands], np.float64)
            if arr.size != n or arr.ndim != 1:
                return orig_flat(bands)
            v = engine.ce_audio_spectral_flatness(
                arr.ctypes.data_as(_DP), n)
            _AUDIO_CNT["flatness"] += 1
            return float(v)
        except Exception:
            _CE_EXC_CNT[0] += 1   # H1: H3-4 音频 flatness C 内核异常 → 失败计数
            return orig_flat(bands)

    ad.AudioAnalyzer._analyze_file_at_position = ce_file
    ad.AudioAnalyzer._analyze_mic_buffer = ce_mic
    msa._spectral_flatness = ce_flatness
    log.info("[CE] H3 audio: C energy / shared normalized spectrum + onset / C flatness")


def _hook_audio_msa(engine):
    """T-recon2: MSA 结构分析纯数学段 → C（_on_frame 热路径深化）。

    profile 数据（probe_audio_chain_prof.py + 同进程交错 A/B）: 纯 Python
    _on_frame ~935us/帧, 其中 numpy mean ~19 处 / sorted(600) / 乐器得分
    6×sum 占大头。本批只保留**实测更快**的批量 C 路径（probe_audio_msa_ab.py
    整链 hooked 450.9us vs pure 935.4us, -51.8% → 乐器批后 ~-60%）:

      ★ 环形镜像（主路径）: deque 内容以 f64 环形缓冲常驻（ce_on_frame
        追加时同步, 逐位=deque 逻辑序）, C 零转换直读 —— 消除逐帧
        list→array 转换 + ctypes 往返的 Python 开销。内核:
        ce_ring_mean_f64 / ce_ring_sorted_low_mean_f64 /
        ce_ring_energy_means_f64 / ce_ring_classify_means_f64 /
        ce_ring_buildup_means_f64（每方法一次 ctypes 调用）
      ★ ce_instrument_scores_f64 ← _compute_instruments_v26 内 6×
        _weighted_band_score 合并为一次 C 调用（bands 转换 1 次; 权重
        矩阵常量一次常驻）。逐次 ctypes 封装实测反慢 3.7×（15.6 vs
        4.3us/次, 转换+往返开销 > C 计算）——故标量小函数
        _spectral_flux/_compute_centroid/_compute_novelty 不做逐次 C
        封装, 保留 Python 原版（DLL 内核仍在, 供批量/未来用）。
      ce_mean_f64 ← 列表路径兜底（_bar_rms_accum, 非 deque 窗口）

    方法级替换（_on_frame/_compute_instruments_v26/_classify_section_v26/
    _detect_buildup_v26/_detect_drop_v26）= 逐行同构副本: 原版逻辑逐字
    照抄, mean/sorted/乐器得分行换批量调用 + 契约失败（镜像缺失/失步/
    NaN 排序/异常）回退原表达式（输出字节不变）。位级对拍:
    ce_fuzz_audio_msa.py（单核 10 万 + 复合/环形 2 万 + 乐器批 2 万,
    NaN/±inf/±0/次正规/满环随机 idx/暖机/短窗钳制 0 差异）+
    ce_fuzz_msa_state.py（6240 帧 × 全状态字段逐位一致）。新路径自门控:
    QASCADE_CE_AUDIO_MSA=0 关闭本批（H3 音频特征批仍在）。
    """
    from src.audio import music_structure_analyzer as msa

    if os.environ.get("QASCADE_CE_AUDIO_MSA", "1") == "0":
        return
    _DP = ctypes.POINTER(ctypes.c_double)
    _MEAN = engine.ce_mean_f64
    _INST = engine.ce_instrument_scores_f64
    _RM = engine.ce_ring_mean_f64
    _RSLM = engine.ce_ring_sorted_low_mean_f64
    _RENG = engine.ce_ring_energy_means_f64
    _RCLS = engine.ce_ring_classify_means_f64
    _RBLD = engine.ce_ring_buildup_means_f64
    if _MEAN is None or _INST is None or _RM is None or _RSLM is None \
            or _RENG is None or _RCLS is None or _RBLD is None:
        log.info("[CE] T-recon2 跳过: DLL 缺 MSA 内核符号（旧 DLL, 原路径不变）")
        return

    # ── 内核封装（契约失败 → None, 调用方回退原版表达式）─────────────
    def _ce_mean_list(vals, start=0, end=None):
        """np.mean(vals[start:end]) 位级等价（numpy f64 pairwise 复刻）。
        负索引按 Python 切片语义归一; 空切片 → None（原版 np.mean(空) 语义）。"""
        try:
            n = int(len(vals))
            s = 0 if start is None else int(start)
            e = n if end is None else int(end)
            if s < 0:
                s = max(0, n + s)
            if e < 0:
                e = max(0, n + e)
            if s > n:
                s = n
            if e > n:
                e = n
            if e <= s:
                return None
            arr = np.ascontiguousarray(vals, np.float64)
            if arr.size != n or arr.ndim != 1:
                return None
            return float(_MEAN(arr.ctypes.data_as(_DP), s, e))
        except Exception:
            return None

    # 乐器模板权重（_INSTRUMENT_PROFILES 固定常量）→ (6,8) f64 矩阵一次
    # 转换常驻（同值同序 → 位级不变）。6 得分一次 C 调用:
    #   ce_instrument_scores_f64(bands, 8, w6, 6, out6)
    # 契约失败（bands 非 8 频段/转换失败/异常）→ None, 调用方逐乐器回退
    # _orig_wbs（输出字节不变）。逐行 = ce_weighted_band_score_f64 同算法
    # （Neumaier）, 由 ce_fuzz_audio_msa.py fuzz_instrument_scores 对拍。
    _W6 = np.ascontiguousarray(
        [list(prof[0]) for prof in msa._INSTRUMENT_PROFILES.values()],
        np.float64)
    _W6_PTR = _W6.ctypes.data_as(_DP)

    def _ce_instrument_batch(bands):
        try:
            n = int(len(bands))
            if n != 8:          # MSA 频段恒 8; 其余长度契约外 → 回退
                return None
            ab = np.ascontiguousarray(bands, np.float64)
            if ab.size != n or ab.ndim != 1:
                return None
            out = np.zeros(6, np.float64)
            rc = _INST(ab.ctypes.data_as(_DP), n, _W6_PTR, 6,
                       out.ctypes.data_as(_DP))
            if rc != 0:
                return None
            return [float(v) for v in out]
        except Exception:
            _CE_EXC_CNT[0] += 1   # H1: T-recon2 乐器批 C 内核异常 → 失败计数
            return None

    # ── 环形镜像（deque 内容的 f64 常驻缓冲, C 零转换直读; 状态对拍排除
    #    _ce_ 键）─────────────────────────────────────────────────────
    # 镜像项 = [buf(f64[maxlen]), filled, idx, buf 的 ctypes 指针缓存]
    _MIR_MAXLEN = {
        "rms_short": 12, "rms_medium": 60, "rms_long": 200,
        "rms_very_long": 600, "centroid_hist": 200, "flux_hist": 120,
        "bpm_history": 320,
    }

    def _mir_append(self, key, v):
        m = getattr(self, "_ce_mir", None)
        if m is None:
            m = {}
            self._ce_mir = m
        e = m.get(key)
        if e is None:
            maxlen = _MIR_MAXLEN[key]
            buf = np.zeros(maxlen, np.float64)
            e = [buf, 0, 0, buf.ctypes.data_as(_DP)]
            m[key] = e
        buf, filled, idx = e[0], e[1], e[2]
        buf[idx] = v
        idx += 1
        if idx == e[0].size:
            idx = 0
        if filled < e[0].size:
            filled += 1
        e[1] = filled
        e[2] = idx

    def _mir_ok(self, key, deque_ref):
        """镜像存在且 filled == deque 长度（镜像与 deque 严格同步才可用）。"""
        m = getattr(self, "_ce_mir", None)
        e = m.get(key) if m else None
        if e is None:
            return False
        return e[0].size == _MIR_MAXLEN[key] and e[1] == len(deque_ref)

    def _ring_mean(self, key, deque_ref, start, end):
        """环形窗口 mean; 镜像缺失/失步 → None（调用方回退原版）。"""
        try:
            if not _mir_ok(self, key, deque_ref):
                return None
            e = self._ce_mir[key]
            return float(_RM(e[3], e[0].size, e[1], e[2], start, end))
        except Exception:
            return None

    def _ring_sorted_low(self, key, deque_ref, low_count):
        try:
            if not _mir_ok(self, key, deque_ref):
                return None
            e = self._ce_mir[key]
            out = ctypes.c_double(0.0)
            rc = _RSLM(e[3], e[0].size, e[1], e[2], int(low_count),
                       ctypes.byref(out))
            if rc != 0:
                return None
            return float(out.value)
        except Exception:
            return None

    def _ring_energy(self, dq_s, dq_m, dq_l):
        """e_short/e_medium/e_long 三窗口均值一次 C 调用; 失败 → None。"""
        try:
            if not (_mir_ok(self, "rms_short", dq_s)
                    and _mir_ok(self, "rms_medium", dq_m)
                    and _mir_ok(self, "rms_long", dq_l)):
                return None
            e1 = self._ce_mir["rms_short"]
            e2 = self._ce_mir["rms_medium"]
            e3 = self._ce_mir["rms_long"]
            out = np.zeros(3, np.float64)
            _RENG(e1[3], e1[0].size, e1[1], e1[2],
                  e2[3], e2[0].size, e2[1], e2[2],
                  e3[3], e3[0].size, e3[1], e3[2],
                  out.ctypes.data_as(_DP))
            return [float(v) for v in out]
        except Exception:
            return None

    def _ring_classify(self, dq_ch, dq_fh, dq_rms):
        """classify 7 处切片均值一次 C 调用（环形直读）; 失败 → None。"""
        try:
            if not (_mir_ok(self, "centroid_hist", dq_ch)
                    and _mir_ok(self, "flux_hist", dq_fh)
                    and _mir_ok(self, "rms_long", dq_rms)):
                return None
            e1 = self._ce_mir["centroid_hist"]
            e2 = self._ce_mir["flux_hist"]
            e3 = self._ce_mir["rms_long"]
            out = np.zeros(7, np.float64)
            _RCLS(e1[3], e1[0].size, e1[1], e1[2],
                  e2[3], e2[0].size, e2[1], e2[2],
                  e3[3], e3[0].size, e3[1], e3[2],
                  out.ctypes.data_as(_DP))
            return [float(v) for v in out]
        except Exception:
            return None

    def _ring_buildup(self, dq_ch, dq_rs, dq_fh):
        """buildup 6 处窗口均值一次 C 调用（环形直读）; 失败 → None。"""
        try:
            if not (_mir_ok(self, "centroid_hist", dq_ch)
                    and _mir_ok(self, "rms_long", dq_rs)
                    and _mir_ok(self, "flux_hist", dq_fh)):
                return None
            e1 = self._ce_mir["centroid_hist"]
            e2 = self._ce_mir["rms_long"]
            e3 = self._ce_mir["flux_hist"]
            out = np.zeros(6, np.float64)
            _RBLD(e1[3], e1[0].size, e1[1], e1[2],
                  e2[3], e2[0].size, e2[1], e2[2],
                  e3[3], e3[0].size, e3[1], e3[2],
                  out.ctypes.data_as(_DP))
            return [float(v) for v in out]
        except Exception:
            return None

    # ── 原版快照（方法级回退用）──────────────────────────────────────
    _orig_wbs = msa._weighted_band_score
    _orig_on_frame = msa.MusicStructureAnalyzer._on_frame
    _orig_classify = msa.MusicStructureAnalyzer._classify_section_v26
    _orig_buildup = msa.MusicStructureAnalyzer._detect_buildup_v26
    _orig_drop = msa.MusicStructureAnalyzer._detect_drop_v26

    # ── _compute_instruments_v26 逐行同构副本（仅 6×得分行换批量）──────
    def ce_instruments_v26(self, bands, onset, e_short, t):
        """
        v26增强的乐器在场度计算：
        - 频谱包络匹配（加权频段得分）
        - 滑动窗口平滑（避免闪烁）
        - 自适应阈值（根据整体能量调整）
        - 持续帧计数（sustain类乐器需要连续多帧才确认）
        """
        # ★ v26修复：能量极低时直接返回全零，避免噪声底限产生虚假乐器识别
        if e_short < 1e-5 and max(bands) < 1e-5:
            # 让平滑值快速衰减到0
            for name in list(self._inst_smooth.keys()):
                self._inst_smooth[name] = self._inst_smooth[name] * 0.5
                self._inst_history[name].append(0.0)
                self._inst_sustained_frames[name] = 0
            return {name: 0.0 for name in msa._INSTRUMENT_PROFILES}

        # T-recon2: 6 乐器得分一次 C 调用（bands 转换 1 次; 契约失败 →
        # 逐乐器回退 _orig_wbs, 输出字节不变）
        scores6 = _ce_instrument_batch(bands)

        out = {}
        energy_factor = 1.0
        if e_short < 0.1:
            energy_factor = 1.5
        elif e_short > 0.5:
            energy_factor = 0.8

        for idx, (name, (weights, mode, base_thr, decay,
                         min_frames)) in enumerate(
                             msa._INSTRUMENT_PROFILES.items()):
            if scores6 is not None:
                score = scores6[idx]
            else:
                score = _orig_wbs(bands, weights)

            if mode == "onset":
                trigger = onset * score * 1.5 + score * 0.3
            else:
                trigger = score * 1.2

            adaptive_thr = base_thr / (self._sens_instrument * energy_factor)
            raw = max(0.0, (trigger - adaptive_thr) / max(0.3, 1.0 - adaptive_thr))
            raw = min(1.0, raw * self._sens_instrument)

            if mode == "sustain":
                if raw > 0.2:
                    self._inst_sustained_frames[name] = min(
                        min_frames + 2,
                        self._inst_sustained_frames[name] + 1
                    )
                else:
                    self._inst_sustained_frames[name] = max(
                        0, self._inst_sustained_frames[name] - 1)
                if self._inst_sustained_frames[name] < min_frames:
                    raw *= self._inst_sustained_frames[name] / max(1, min_frames)

            if mode == "onset":
                alpha = 0.5
            else:
                alpha = 0.25

            prev_smooth = self._inst_smooth.get(name, 0.0)
            smoothed = alpha * raw + (1 - alpha) * prev_smooth

            if raw < prev_smooth:
                smoothed = prev_smooth * decay

            self._inst_smooth[name] = smoothed
            self._inst_history[name].append(smoothed)
            out[name] = float(min(1.0, max(0.0, smoothed)))

        return out

    # ── _on_frame 逐行同构副本（仅 mean 行换 C, 其余逐字照抄）─────────
    def ce_on_frame(self, frame, t_override=None):
        if msa._msa_frame_gate(self, frame, t_override):
            return

        # ★ 离线烘焙支持：t_override 不为 None 时用调用方给的时间戳
        if t_override is not None:
            t = float(t_override)
        else:
            t = time.monotonic() - self._t0
        self._frame_count += 1

        rms = getattr(frame, "rms", 0.0)
        peak = getattr(frame, "peak", 0.0)
        bands = list(getattr(frame, "bands", [0.0] * 8))
        bpm = float(getattr(frame, "bpm", 120.0))
        beat_phase = float(getattr(frame, "beat_phase", 0.0))
        is_beat = bool(getattr(frame, "is_beat", False))

        # ── 多时间尺度能量追踪 ──
        self._rms_short.append(rms)
        self._rms_medium.append(rms)
        self._rms_long.append(rms)
        self._rms_silence.append(rms)
        self._rms_very_long.append(rms)
        # T-recon2 环形镜像同步（deque 内容 f64 常驻, C 直读零转换）
        _mir_append(self, "rms_short", rms)
        _mir_append(self, "rms_medium", rms)
        _mir_append(self, "rms_long", rms)
        _mir_append(self, "rms_very_long", rms)

        # ── 频谱分析（v26增强）──
        alpha_bands = 0.3
        for i in range(min(8, len(bands))):
            self._bands_smooth[i] = (alpha_bands * bands[i] +
                                     (1 - alpha_bands) * self._bands_smooth[i])

        # C2 自适应分位归一（opt-in）: 默认关时不计算、不影响原逻辑
        if self._adaptive_band_norm:
            for i in range(min(8, len(bands))):
                hist = self._band_hist[i]
                hist.append(bands[i])
                if len(hist) >= 16:
                    s = sorted(hist)
                    q05 = s[max(0, int(0.05 * (len(s) - 1)))]
                    q95 = s[min(len(s) - 1, int(0.95 * (len(s) - 1)))]
                    rng = q95 - q05
                    self._bands_norm[i] = (max(0.0, min(1.0, (bands[i] - q05) / rng))
                                           if rng > 1e-9 else self._bands_smooth[i])
                else:
                    # 暖机：窗未满，回退平滑值（不除零）
                    self._bands_norm[i] = self._bands_smooth[i]

        centroid = self._compute_centroid(bands)
        self._centroid_hist.append(centroid)
        _mir_append(self, "centroid_hist", centroid)

        flux = msa._spectral_flux(bands, self._bands_prev)
        self._flux_hist.append(flux)
        _mir_append(self, "flux_hist", flux)

        flatness = msa._spectral_flatness(bands)
        self._flatness_hist.append(flatness)

        novelty = self._compute_novelty(bands)
        self._novelty_hist.append(novelty)
        self._bands_prev = list(bands)

        # ── 自适应噪声底（sorted+mean → C 环形直读, NaN/失步回退原版）──
        if len(self._rms_very_long) >= 40:
            low_count = max(4, len(self._rms_very_long) // 10)
            m_floor = _ring_sorted_low(self, "rms_very_long",
                                       self._rms_very_long, low_count)
            if m_floor is None:
                sorted_rms = sorted(self._rms_very_long)
                m_floor = float(np.mean(sorted_rms[:low_count]))
            self._noise_floor_adaptive = max(
                self._noise_floor,
                m_floor * 1.5
            )

        # ── 能量计算（np.mean → C 环形一次调用, 契约失败逐式回退原版）──
        if self._rms_short or self._rms_medium or self._rms_long:
            m3 = _ring_energy(self, self._rms_short, self._rms_medium,
                              self._rms_long)
        else:
            m3 = None
        if self._rms_short:
            if m3 is not None:
                e_short = m3[0]
            else:
                e_short = float(np.mean(list(self._rms_short)))
        else:
            e_short = max(rms, 1e-9)
        if self._rms_medium:
            if m3 is not None:
                e_medium = m3[1]
            else:
                e_medium = float(np.mean(list(self._rms_medium)))
        else:
            e_medium = e_short
        if self._rms_long:
            if m3 is not None:
                e_long = m3[2]
            else:
                e_long = float(np.mean(list(self._rms_long)))
        else:
            e_long = max(rms, 1e-9)

        # ── 乐器在场度计算（v26增强）──
        onset = novelty + flux * 0.5
        instruments = self._compute_instruments_v26(bands, onset, e_short, t)

        # ── 节奏密度计算 ──
        if novelty > 0.15 or flux > 0.3:
            self._onset_times.append(t)
        recent_onsets = [ot for ot in self._onset_times if (t - ot) < 2.0]
        self._onset_density = len(recent_onsets) / 2.0

        # ── 节拍强度追踪 ──
        if is_beat:
            self._beat_strength_hist.append(rms)

        # ── 小节/节拍追踪 ──
        bar_changed = False
        if is_beat:
            if self._last_beat_t > 0:
                interval = t - self._last_beat_t
                if 0.15 < interval < 2.0:
                    self._beat_intervals.append(interval)
            self._last_beat_t = t
            self._beat_count += 1
            old_bar = self._bar_number
            self._beat_in_bar = (self._beat_count - 1) % self._beats_per_bar
            self._bar_number = (self._beat_count - 1) // self._beats_per_bar
            self._bar_rms_accum.append(rms)
            if self._bar_number != old_bar:
                bar_changed = True
                if self._bar_rms_accum:
                    v = _ce_mean_list(self._bar_rms_accum)
                    self._bar_energy = (v if v is not None
                                        else float(np.mean(self._bar_rms_accum)))
                self._bar_rms_accum = [rms]
        else:
            self._bar_rms_accum.append(rms)

        # ── 速度变化检测 ──
        self._bpm_history.append(bpm)
        _mir_append(self, "bpm_history", bpm)
        tempo_changed_flag = False
        if len(self._bpm_history) >= 40:
            half = len(self._bpm_history) // 2
            v = _ring_mean(self, "bpm_history", self._bpm_history, 0, half)
            bpm_early = v if v is not None else float(np.mean(
                list(self._bpm_history)[:half]))
            v = _ring_mean(self, "bpm_history", self._bpm_history, half,
                           len(self._bpm_history))
            bpm_late = v if v is not None else float(np.mean(
                list(self._bpm_history)[half:]))
            self._tempo_delta = bpm_late - bpm_early
            if (abs(self._tempo_delta) > 5.0
                    and (t - self._last_tempo_change_t) > self._tempo_change_cooldown):
                tempo_changed_flag = True
                self._last_tempo_change_t = t

        # ── 段落分类（v26多维度置信度评分）──
        prev_section = self._section_label
        new_label = self._classify_section_v26(
            rms, e_short, e_medium, e_long, centroid, flux, flatness,
            novelty, instruments, t
        )
        section_changed = self._update_section_state_v26(new_label, t)

        # ── drop/buildup检测（v26增强）──
        is_drop = self._detect_drop_v26(rms, e_short, e_long, flux, instruments, t)
        buildup_change = self._detect_buildup_v26(e_short, e_medium, centroid, flux, t)

        # ── 构建事件 ──
        ev = msa.MusicStructureEvent(
            t=t, event_type="tick",
            rms=rms, peak=peak, bands=bands,
            bpm=bpm, beat_phase=beat_phase, is_beat=is_beat,
            instruments=instruments,
            section_label=self._section_label,
            section_age=max(0.0, t - self._section_started_t),
            energy_short=e_short, energy_long=e_long,
            spectral_centroid=centroid, novelty=novelty,
            bar_number=self._bar_number, beat_in_bar=self._beat_in_bar,
            beats_per_bar=self._beats_per_bar,
            tempo_delta=self._tempo_delta, bar_energy=self._bar_energy,
            has_audio=True,
            extras={
                # 兼容旧键与深度分析器期望的新键
                "buildup": self._buildup_intensity,
                "buildup_intensity": self._buildup_intensity,
                "buildup_active": bool(self._buildup_active),
                "onset": onset,
                "flux": flux,
                "spectral_flux": flux,
                "flatness": flatness,
                "spectral_flatness": flatness,
                "onset_density": self._onset_density,
                "rhythmic_density": self._onset_density,
                "confidence": self._section_confidence,
                "section_confidence": self._section_confidence,
                "energy_ratio": float(e_short / max(e_long, 1e-6)),
                "bass_energy": float(sum(bands[:2]) / 2.0) if bands else 0.0,
                "mid_energy": float(sum(bands[2:5]) / 3.0) if len(bands) >= 5 else 0.0,
                "high_energy": float(sum(bands[5:8]) / 3.0) if len(bands) >= 8 else 0.0,
            },
        )

        # ── 发射事件 ──
        if is_drop:
            ev_drop = self._clone_event(ev, event_type="drop")
            self.structure_event.emit(ev_drop)
            self.drop_detected.emit(rms)

        if section_changed:
            ev_sc = self._clone_event(ev, event_type="section_change",
                                      prev_section=prev_section)
            self.structure_event.emit(ev_sc)
            self.section_changed.emit(prev_section, self._section_label)

        if buildup_change == "start":
            self.structure_event.emit(self._clone_event(ev, event_type="buildup_start"))
        elif buildup_change == "end":
            self.structure_event.emit(self._clone_event(ev, event_type="buildup_end"))

        if bar_changed:
            ev_bar = self._clone_event(ev, event_type="bar_change")
            self.structure_event.emit(ev_bar)
            self.bar_changed.emit(self._bar_number, self._beat_in_bar)

        if tempo_changed_flag:
            ev_tempo = self._clone_event(ev, event_type="tempo_change")
            self.structure_event.emit(ev_tempo)
            self.tempo_changed.emit(bpm, self._tempo_delta)

        # ── 乐器onset事件（v26: 扩展到所有乐器）──
        for inst in msa.INSTRUMENTS:
            level = instruments.get(inst, 0.0)
            prev_lvl = self._inst_prev.get(inst, 0.0)
            last_t = self._inst_last_onset_t.get(inst, -10.0)
            cooldown = 0.08 if inst in ("kick", "snare", "hihat") else 0.15
            if level > 0.4 and prev_lvl < 0.25 and (t - last_t) > cooldown:
                self._inst_last_onset_t[inst] = t
                ev_io = self._clone_event(
                    ev, event_type="instrument_onset", onset_instrument=inst)
                self.structure_event.emit(ev_io)
                self.instrument_onset.emit(inst, level)

        self._inst_prev = dict(instruments)
        self.analysis_tick.emit(ev)
        return ev

    # ── _classify_section_v26 逐行同构副本 ────────────────────────────
    def ce_classify(self, rms, e_short, e_medium, e_long,
                    centroid, flux, flatness, novelty,
                    instruments, t):
        """v26多维度段落分类：为每个可能的段落计算置信度得分，选择最高分。"""
        # ★ v26修复：绝对静音检测（rms极低时直接返回当前标签，不触发变化）
        ABSOLUTE_SILENCE = 1e-5  # 真正的零信号阈值
        if rms < ABSOLUTE_SILENCE and e_short < ABSOLUTE_SILENCE:
            return self._section_label  # 保持当前标签，不触发任何变化

        # 相对静音检测（信号低于噪声底限）
        if e_short < self._noise_floor_adaptive:
            if self._section_label in (msa.SectionLabel.CHORUS, msa.SectionLabel.DROP,
                                       msa.SectionLabel.BUILD):
                return msa.SectionLabel.BREAKDOWN
            if self._section_label in (msa.SectionLabel.UNKNOWN, msa.SectionLabel.INTRO):
                return msa.SectionLabel.INTRO
            return msa.SectionLabel.OUTRO

        # ── 计算各维度特征 ──
        ratio_short_long = e_short / max(self._noise_floor_adaptive, e_long, 1e-6)
        ratio_medium_long = e_medium / max(self._noise_floor_adaptive, e_long, 1e-6)

        m_out = _ring_classify(self, self._centroid_hist, self._flux_hist,
                               self._rms_long)
        centroid_rising = 0.0
        centroid_level = centroid
        if len(self._centroid_hist) >= 12:
            third = max(3, len(self._centroid_hist) // 3)
            if m_out is not None:
                head = m_out[0]
                tail = m_out[1]
            else:
                ch = list(self._centroid_hist)
                head = float(np.mean(ch[:third]))
                tail = float(np.mean(ch[-third:]))
            centroid_rising = tail - head

        if self._flux_hist:
            if m_out is not None:
                avg_flux = m_out[2]
            else:
                avg_flux = float(np.mean(list(self._flux_hist)))
        else:
            avg_flux = 0.0
        flux_rising = 0.0
        if len(self._flux_hist) >= 12:
            third = max(3, len(self._flux_hist) // 3)
            if m_out is not None:
                flux_rising = m_out[3] - m_out[4]
            else:
                fh = list(self._flux_hist)
                flux_rising = float(np.mean(fh[-third:])) - float(np.mean(fh[:third]))

        energy_rising = 0.0
        if len(self._rms_long) >= 20:
            third = max(5, len(self._rms_long) // 3)
            if m_out is not None:
                energy_rising = m_out[5] - m_out[6]
            else:
                rms_list = list(self._rms_long)
                energy_rising = (float(np.mean(rms_list[-third:])) -
                                 float(np.mean(rms_list[:third])))

        kick = instruments.get("kick", 0)
        snare = instruments.get("snare", 0)
        hihat = instruments.get("hihat", 0)
        bass = instruments.get("bass", 0)
        melody = instruments.get("melody", 0)
        pad = instruments.get("pad", 0)

        rhythm_total = kick + snare + hihat
        melodic_total = melody + pad
        low_freq = bass + kick
        age = t - self._section_started_t

        # ── 各段落置信度评分 ──
        scores = {}
        section_thr = self._sens_section

        # --- INTRO ---
        score_intro = 0.0
        if age < 3.0 and self._section_label == msa.SectionLabel.UNKNOWN:
            score_intro += 0.5
        if ratio_short_long < 0.6:
            score_intro += 0.2
        if rhythm_total < 0.3:
            score_intro += 0.15
        if melodic_total > rhythm_total:
            score_intro += 0.1
        scores[msa.SectionLabel.INTRO] = score_intro

        # --- VERSE ---
        score_verse = 0.0
        if 0.4 < ratio_short_long < (1.0 + section_thr * 0.5):
            score_verse += 0.35
        if melody > 0.2 or pad > 0.15:
            score_verse += 0.15
        if rhythm_total < 0.6:
            score_verse += 0.1
        if kick > 0.2 and kick < 0.5:
            score_verse += 0.1
        if centroid_level < 0.5:
            score_verse += 0.1
        if self._onset_density < 8.0:
            score_verse += 0.1
        scores[msa.SectionLabel.VERSE] = score_verse

        # --- BUILD ---
        score_build = 0.0
        if centroid_rising > 0.03:
            score_build += 0.25
        if energy_rising > 0.02:
            score_build += 0.25
        if flux_rising > 0.05:
            score_build += 0.15
        if self._buildup_intensity > 0.3:
            score_build += 0.2
        if hihat > 0.3 and centroid_rising > 0.02:
            score_build += 0.1
        if self._onset_density > 6.0 and energy_rising > 0.01:
            score_build += 0.1
        scores[msa.SectionLabel.BUILD] = score_build

        # --- CHORUS ---
        score_chorus = 0.0
        if ratio_short_long > (1.0 + section_thr * 0.6):
            score_chorus += 0.25
        if melody > 0.35:
            score_chorus += 0.2
        if rhythm_total > 0.4:
            score_chorus += 0.15
        if centroid_level > 0.35:
            score_chorus += 0.1
        if snare > 0.25:
            score_chorus += 0.1
        if self._onset_density > 5.0:
            score_chorus += 0.1
        if kick > 0.6 and bass > 0.5:
            score_chorus -= 0.15
        scores[msa.SectionLabel.CHORUS] = score_chorus

        # --- DROP ---
        score_drop = 0.0
        if ratio_short_long > (1.0 + section_thr):
            score_drop += 0.2
        if kick > 0.45:
            score_drop += 0.2
        if bass > 0.4:
            score_drop += 0.2
        if low_freq > 0.8:
            score_drop += 0.15
        if centroid_level < 0.4 and e_short > 0.3:
            score_drop += 0.1
        if self._onset_density > 8.0:
            score_drop += 0.1
        if self._section_label == msa.SectionLabel.BUILD:
            score_drop += 0.15
        scores[msa.SectionLabel.DROP] = score_drop

        # --- BREAKDOWN ---
        score_breakdown = 0.0
        if ratio_short_long < 0.5 and e_short > self._noise_floor_adaptive:
            score_breakdown += 0.25
        if rhythm_total < 0.2:
            score_breakdown += 0.2
        if pad > 0.2 and kick < 0.15:
            score_breakdown += 0.15
        if self._section_label in (msa.SectionLabel.DROP, msa.SectionLabel.CHORUS):
            if ratio_short_long < 0.7:
                score_breakdown += 0.15
        if self._onset_density < 3.0 and e_short > self._noise_floor_adaptive:
            score_breakdown += 0.1
        scores[msa.SectionLabel.BREAKDOWN] = score_breakdown

        # --- OUTRO ---
        score_outro = 0.0
        if age > 20.0 and energy_rising < -0.02:
            score_outro += 0.3
        if ratio_short_long < 0.4 and age > 15.0 and energy_rising < -0.01:
            score_outro += 0.2
        if rhythm_total < 0.2 and age > 10.0 and energy_rising < 0.0:
            score_outro += 0.15
        if energy_rising < -0.03:
            score_outro += 0.15
        scores[msa.SectionLabel.OUTRO] = score_outro

        # ── 段落惯性 ──
        current_bonus = 0.12 if age < 4.0 else 0.06
        if self._section_label in scores:
            scores[self._section_label] += current_bonus

        # ── 选择最高分 ──
        self._last_section_scores = scores
        best_label = max(scores, key=scores.get)
        self._section_confidence = scores[best_label]

        if scores[best_label] < 0.25:
            return self._section_label

        return best_label

    # ── _detect_drop_v26 逐行同构副本 ────────────────────────────────
    def ce_drop(self, rms, e_short, e_long, flux, instruments, t):
        if (t - self._last_drop_t) < self._drop_cooldown_s:
            return False

        # 方式1: 传统静音→爆发
        recent = list(self._rms_silence)
        if len(recent) >= 6:
            threshold_silence = max(self._noise_floor_adaptive * 1.5, 0.03)
            had_silence = min(recent[:max(3, len(recent) // 3)]) < threshold_silence
            if had_silence and e_long > 1e-6:
                if (rms / max(e_long, 1e-6)) >= self._sens_drop and rms > 0.12:
                    self._last_drop_t = t
                    return True

        # 方式2: 频谱通量突增
        fh_n = len(self._flux_hist)
        if fh_n >= 20:
            v = _ring_mean(self, "flux_hist", self._flux_hist, 0, fh_n - 5)
            avg_flux = v if v is not None else float(
                np.mean(list(self._flux_hist)[:-5]))
            v = _ring_mean(self, "flux_hist", self._flux_hist, fh_n - 3, fh_n)
            recent_flux = v if v is not None else float(
                np.mean(list(self._flux_hist)[-3:]))
            if recent_flux > avg_flux * 3.0 and rms > 0.2:
                kick = instruments.get("kick", 0)
                bass = instruments.get("bass", 0)
                if kick > 0.4 or bass > 0.4:
                    self._last_drop_t = t
                    return True

        # 方式3: buildup结束后的能量跳变
        if self._buildup_active and self._buildup_intensity > 0.6:
            if rms > e_long * 1.5 and rms > 0.2:
                kick = instruments.get("kick", 0)
                if kick > 0.35:
                    self._last_drop_t = t
                    self._buildup_active = False
                    self._buildup_intensity = 0.0
                    return True

        return False

    # ── _detect_buildup_v26 逐行同构副本 ─────────────────────────────
    def ce_buildup(self, e_short, e_medium, centroid, flux, t):
        if len(self._centroid_hist) < 20 or len(self._rms_long) < 20:
            return ""

        m_out = _ring_buildup(self, self._centroid_hist, self._rms_long,
                              self._flux_hist)

        if m_out is not None:
            ch_rise = m_out[0] - m_out[1]
            rs_rise = m_out[2] - m_out[3]
            flux_rise = 0.0
            if len(self._flux_hist) >= 6:
                flux_rise = m_out[4] - m_out[5]
        else:
            win = max(12, int(2.5 * 40))
            ch = list(self._centroid_hist)
            rs = list(self._rms_long)
            fh = list(self._flux_hist)
            ch_win = ch[-win:]
            rs_win = rs[-win:]
            fh_win = fh[-min(win, len(fh)):]
            third = max(3, len(ch_win) // 3)
            ch_rise = float(np.mean(ch_win[-third:]) - np.mean(ch_win[:third]))
            rs_rise = float(np.mean(rs_win[-third:]) - np.mean(rs_win[:third]))
            flux_rise = 0.0
            if len(fh_win) >= 6:
                f_third = max(2, len(fh_win) // 3)
                flux_rise = float(np.mean(fh_win[-f_third:])
                                  - np.mean(fh_win[:f_third]))

        buildup_score = 0.0
        if ch_rise > 0.03:
            buildup_score += min(0.4, ch_rise * 4.0)
        if rs_rise > 0.02:
            buildup_score += min(0.3, rs_rise * 5.0)
        if flux_rise > 0.03:
            buildup_score += min(0.3, flux_rise * 3.0)

        if buildup_score > 0.3:
            self._buildup_intensity = min(1.0,
                self._buildup_intensity * 0.9 + buildup_score * 0.3)
        else:
            self._buildup_intensity *= 0.95

        is_buildup = buildup_score > 0.35 and e_short > self._noise_floor_adaptive * 2

        if is_buildup and not self._buildup_active:
            self._buildup_active = True
            self._buildup_started_t = t
            return "start"

        if (not is_buildup) and self._buildup_active:
            if buildup_score < 0.15 or (t - self._buildup_started_t) > 15.0:
                self._buildup_active = False
                self._buildup_intensity = 0.0
                return "end"

        return ""

    # ── 挂载（自门控: DLL 缺符号 → 上述早退, 原路径不变）────────────
    msa.MusicStructureAnalyzer._on_frame = ce_on_frame
    msa.MusicStructureAnalyzer._compute_instruments_v26 = ce_instruments_v26
    msa.MusicStructureAnalyzer._classify_section_v26 = ce_classify
    msa.MusicStructureAnalyzer._detect_buildup_v26 = ce_buildup
    msa.MusicStructureAnalyzer._detect_drop_v26 = ce_drop
    log.info("[CE] T-recon2 已接入: MSA 纯数学段 → ce_mean_f64 / "
             "ce_ring_* 镜像均值 / ce_instrument_scores_f64 乐器批")


def _hook_topview(engine):
    """T10: TopView 同步/编组纯数学段 → C 内核（ce_fuzz_sync 10 万 +
    ce_fuzz_topview 10.3 万次位级一致）。

    接入点:
      A1 ce_catmull_rom_tangent    ← topview_panel._catmull_rom_tangent
           （交错 pts/ts 数组按 (id(pts),id(ordered),id(ts),n,closed) 缓存,
             循环内每次仅 1 次 ctypes 调用; 缓存上限 16 条防内存膨胀）
      A2 ce_sync_angle_from_tangent ← 同步角计算段三处: _spline_cluster_angles
           阶段2/单台贴线 + _compute_xy_reference_angles 圆分支与 CR 自拟合分支
           （邻域弦/cos·sin 留 Python; 纯数学段 C; 失败回退 Python 原式, 字节不变）
      B1 ce_ransac_circles         ← topview_autogroup._detect_circles_ransac
           （RANSAC 决策: 接入——C 内 MT19937 逐位复刻
           random.Random(seed&0x7FFFFFFF) 流（fuzz S9 含负种子零差异 + 12.2 万次
           randrange 探针先行验证）; 门控: 非有限坐标 / seed 超 C long long 域 /
           非 int min_pts·iters → 原版）
      B2 ce_assign_shapes          ← topview_autogroup._assign_to_shapes
      B3 ce_circle_from_3          ← topview_autogroup._circle_from_3
      B4 ce_circle_axis_order      ← topview_autogroup.AutoGroupEngine._circle_axis_order
      B5 ce_circle_window_order    ← topview_autogroup.AutoGroupEngine._members_in_window
           （返回 C 序三元组 (fid, ang, off); 下游 _arc_chain/_phase_row 的
             win.sort((t[2],t[1])) 稳定排序对 C 序恒为 no-op）

    门控回退（字节不变）: 内核缺失 / 非有限坐标 / 越界索引 / 精确数值类型
    （Decimal/Fraction, float() 舍入不可等价）/ 形状解析异常 / 任何 C 调用
    异常 → 委托原版。

    变更区门控（2026-08-08）: topview_panel.py 同步角函数持续改动而 C 侧
    未同步（bd80ece 新增 _circle_anchor_cluster_angles, C 零引用）→ 默认
    关闭永不接管, 恒回退 Python; C 重新同步后置 QASCADE_CE_T10=1 恢复。
    """
    # 门控: QASCADE_CE_T10=1 启用（2026-08-14 代码默认对齐矩阵 flip, 直调
    # install 路径与生产 _mount_ce 矩阵一致默认开）。
    if os.environ.get("QASCADE_CE_T10", "1") != "1":
        log.info("[CE] T10 门控关闭 (QASCADE_CE_T10=1 启用)")
        return
    try:
        from src.ui import topview_panel as tp
        from src.ui import topview_autogroup as tag
    except Exception as _e:
        log.info("[CE] T10 跳过: topview 模块导入失败 %s（保持 Python 路径）", _e)
        return
    _DP = ctypes.POINTER(ctypes.c_double)
    _IP32 = ctypes.POINTER(ctypes.c_int32)
    _NUM = (float, int, np.floating, np.integer)

    # ── A1: 弦长参数化 Catmull-Rom 切向 → ce_catmull_rom_tangent ────────
    orig_catmull = tp._catmull_rom_tangent
    _catmull_cache = {}

    def ce_catmull_rom_tangent(pts, ordered, idx, ts, closed=False):
        n = len(ordered)
        if n <= 1:
            return (1.0, 0.0)
        if idx < 0 or idx >= n:
            # 原版对越界 idx 经分支钳制（idx<=0 端点 / idx>=n-1 端点）与闭合
            # 回绕（mod n）仍返回切向, 不抛错; C 内核分支逻辑相同, 但保守
            # 委托原版保证逐位一致
            return orig_catmull(pts, ordered, idx, ts, closed)
        if engine.ce_catmull_rom_tangent is None:
            return orig_catmull(pts, ordered, idx, ts, closed)
        key = (id(pts), id(ordered), id(ts), n, 1 if closed else 0)
        arrs = _catmull_cache.get(key)
        if arrs is None:
            try:
                pts_arr = np.empty(2 * n, np.float64)
                for i, fid in enumerate(ordered):
                    x, y = pts[fid]
                    pts_arr[2 * i] = float(x)
                    pts_arr[2 * i + 1] = float(y)
                if not np.isfinite(pts_arr).all():
                    return orig_catmull(pts, ordered, idx, ts, closed)
                ts_arr = np.asarray(ts, np.float64)
                if ts_arr.size != n or not np.isfinite(ts_arr).all():
                    return orig_catmull(pts, ordered, idx, ts, closed)
            except Exception:
                return orig_catmull(pts, ordered, idx, ts, closed)
            if len(_catmull_cache) >= 16:
                _catmull_cache.clear()
            # 指针一并缓存: numpy ctypes.data_as 实测 ~4.4µs/次（11382 台循环
            # 65ms 纯开销）; 裸 ctypes 调用 ~1.36µs/次
            _catmull_cache[key] = (pts_arr, ts_arr,
                                   pts_arr.ctypes.data_as(_DP),
                                   ts_arr.ctypes.data_as(_DP))
            arrs = _catmull_cache[key]
        try:
            dx = ctypes.c_double()
            dy = ctypes.c_double()
            engine.ce_catmull_rom_tangent(
                arrs[2], arrs[3], n, idx, 1 if closed else 0,
                ctypes.byref(dx), ctypes.byref(dy))
            _TOPVIEW_CNT["catmull"] += 1
            return (dx.value, dy.value)
        except Exception:
            return orig_catmull(pts, ordered, idx, ts, closed)

    tp._catmull_rom_tangent = ce_catmull_rom_tangent
    log.info("[CE] T10 A1 已接入: _catmull_rom_tangent → ce_catmull_rom_tangent")

    # ── A2: 切向→同步角公共段 → ce_sync_angle_from_tangent ───────────────
    def _ce_tangent_to_angle(tx, ty, ndx, ndy, has_nd, flip_roty):
        """纯数学段 C; 内核缺失/异常 → None（调用方回退 Python 原式, 字节不变）。"""
        if engine.ce_sync_angle_from_tangent is None:
            return None
        try:
            val = float(engine.ce_sync_angle_from_tangent(
                float(tx), float(ty), float(ndx), float(ndy),
                1 if has_nd else 0, 1 if flip_roty else 0))
            _TOPVIEW_CNT["angle"] += 1
            return val
        except Exception:
            return None

    # ── A2a: _spline_cluster_angles 同构复刻（阶段1 逐字; 阶段2 数学段 C）──
    orig_spline_cluster = tp._spline_cluster_angles

    def ce_spline_cluster_angles(pts, splines, ordered, return_assign=False):
        from src.ui.geometry_splines import spline_sample
        import numpy as _np
        out: dict = {}
        rest = list(ordered)
        # 阶段 1：每台设备归「全局最近且贴合（≤跨度×1%）」的弧线
        assign: dict = {}
        best_d2: dict = {}
        prepared: list = []
        for sp in splines:
            sp_pts, sp_ang = spline_sample(sp["points"], mode=sp.get("mode", "cubic"))
            # 跨度 = 包围盒对角线（闭合折线首尾重合，用首尾距会得 0 而误跳过星形）
            spx = [p[0] for p in sp_pts]
            spz = [p[1] for p in sp_pts]
            span = math.hypot(max(spx) - min(spx), max(spz) - min(spz))
            has_nan = not all(math.isfinite(p[0]) and math.isfinite(p[1]) for p in sp_pts)
            prepared.append((sp_pts, sp_ang, span,
                             min(spx), max(spx), min(spz), max(spz), has_nan))
        X = _np.asarray([pts[fid][0] for fid in rest], dtype=float)
        Z = _np.asarray([pts[fid][1] for fid in rest], dtype=float)
        for si, (sp_pts, _ang, span, bx0, bx1, bz0, bz1, has_nan) in enumerate(prepared):
            if span <= 1.0:
                continue
            tol = span * 0.01
            tol2 = tol * tol
            if has_nan:
                # 罕见 NaN/inf 采样点: 回退逐点全扫 (numpy argmin 对 NaN 语义不同)
                for idx in range(len(rest)):
                    x, z = pts[rest[idx]]
                    best = float("inf")
                    j = 0
                    for jj, (sx, sz) in enumerate(sp_pts):
                        d2 = (x - sx) * (x - sx) + (z - sz) * (z - sz)
                        if d2 < best:
                            best = d2
                            j = jj
                    if best <= tol2 and best < best_d2.get(rest[idx], float("inf")):
                        best_d2[rest[idx]] = best
                        assign[rest[idx]] = (si, j)
                continue
            mask = ((X >= bx0 - tol) & (X <= bx1 + tol)
                    & (Z >= bz0 - tol) & (Z <= bz1 + tol))
            idxs = _np.flatnonzero(mask)
            if idxs.size == 0:
                continue
            sx_arr = _np.asarray([p[0] for p in sp_pts], dtype=float)
            sz_arr = _np.asarray([p[1] for p in sp_pts], dtype=float)
            dx = X[idxs, None] - sx_arr[None, :]
            dz = Z[idxs, None] - sz_arr[None, :]
            d2 = dx * dx + dz * dz
            js = _np.argmin(d2, axis=1)
            bests = d2[_np.arange(idxs.size), js]
            for idx, best, jj in zip(idxs.tolist(), bests.tolist(), js.tolist()):
                fid = rest[idx]
                if best <= tol2 and best < best_d2.get(fid, float("inf")):
                    best_d2[fid] = best
                    assign[fid] = (si, jj)
        # 阶段 2：按弧线分组算角度（弧线序 = match 贴合度升序，先算最贴合的）
        for si in sorted({a[0] for a in assign.values()}):
            sp_pts, sp_ang, _span = prepared[si][0], prepared[si][1], prepared[si][2]
            hit = [(fid, j) for fid, (s2, j) in assign.items() if s2 == si]
            if len(hit) < 2:
                # 单台贴线也取该弧线切向（2026-08-03 agent F：上弧线 2 台贴线
                # 距离 1.9/2.2 却因无邻居被丢，兜底角偏差 43°+）
                for fid, j in hit:
                    tx = math.cos(math.radians(sp_ang[j]))
                    ty = math.sin(math.radians(sp_ang[j]))
                    _ang = _ce_tangent_to_angle(tx, ty, 0.0, 0.0, False, True)
                    if _ang is None:
                        _ang = tp._normalize_angle_deg(
                            -math.degrees(math.atan2(ty, tx)))
                        if _ang > 90.0:
                            _ang -= 180.0
                        elif _ang < -90.0:
                            _ang += 180.0
                    out[fid] = _ang
                    rest = [f for f in rest if f != fid]
                continue
            _o2t = tp._spline_order_ts([f for f, _ in hit], pts, sp_pts)
            hit_sorted = sorted(hit, key=lambda t: _o2t[t[0]])
            for idx, (fid, j) in enumerate(hit_sorted):
                tx = math.cos(math.radians(sp_ang[j]))
                ty = math.sin(math.radians(sp_ang[j]))
                prev_id = hit_sorted[idx - 1][0] if idx > 0 else None
                next_id = hit_sorted[idx + 1][0] if idx < len(hit_sorted) - 1 else None
                if prev_id is not None and next_id is not None:
                    px, py = pts[prev_id]
                    nx, ny = pts[next_id]
                    ndx, ndy = nx - px, ny - py
                    _has_nd = True
                elif next_id is not None:
                    nx, ny = pts[next_id]
                    ndx, ndy = nx - pts[fid][0], ny - pts[fid][1]
                    _has_nd = True
                elif prev_id is not None:
                    px, py = pts[prev_id]
                    ndx, ndy = pts[fid][0] - px, pts[fid][1] - py
                    _has_nd = True
                else:
                    ndx, ndy = tx, ty
                    _has_nd = False
                _ang = _ce_tangent_to_angle(tx, ty, ndx, ndy, _has_nd, True)
                if _ang is None:
                    if tx * ndx + ty * ndy < 0.0:
                        tx, ty = -tx, -ty
                    _ang = tp._normalize_angle_deg(
                        -math.degrees(math.atan2(ty, tx)))
                    if _ang > 90.0:
                        _ang -= 180.0
                    elif _ang < -90.0:
                        _ang += 180.0
                out[fid] = _ang
            rest = [f for f in rest if f not in out]
            if not rest:
                break
        if return_assign:
            return out, rest, assign
        return out, rest

    tp._spline_cluster_angles = ce_spline_cluster_angles
    log.info("[CE] T10 A2 已接入: _spline_cluster_angles 同步角段 → C")

    # ── A2b: _compute_xy_reference_angles 同构复刻（圆/CR 分支数学段 C）──
    orig_compute_xy = tp.TopViewPanel._compute_xy_reference_angles

    def ce_compute_xy_reference_angles(self, ids, return_cluster=False):
        # ★ 同步基准的目标是包内基底电机（成员不在页可见集，只画代表点）——
        #   过滤必须放行全部真实设备，否则 ordered 恒空 → 基准角度全 0 →
        #   「同步没效果」（2026-08-03 用户实测：底弧线 120 台位置有效成弧线，
        #   但 _compute_xy_reference_angles 返回 0 台）。
        # ★ return_cluster=True 时返回 (angles, 圆簇设备集)——圆排多设备统一化
        #   （2026-08-04 dbe8922 + 2026-08-05 对称双翼, 原版语义同步）：
        #   调用方（topview_panel.py:3730/:3770 无 try/except 直调）据此写
        #   uniform_world_angle; 缺参数 → TypeError 生产 sync 整体不可用（T10）。
        _cluster: set = set()

        def _ret(out_dict):
            return (out_dict, _cluster) if return_cluster else out_dict

        valid = set(self.fixtures_by_id().keys())
        ordered = [str(fid) for fid in ids or [] if str(fid) in valid]
        if not ordered:
            return _ret({})
        # 位置优先用真实世界坐标（工程场地坐标，沿父链变换）——包内局部/画布
        # 坐标受包 RotY 旋转影响，切向方向会失真；world 缺失时回退画布坐标。
        # ★ 包锚点（2026-08-04 A1 终验补齐, 原版语义同步）：生产 targets = 包内
        #   基底电机 fid, 其 world 坐标含包内局部偏移（落叶头 73/73 台恒 9.4 单位）
        #   → P2 翼判定/PCA 排序失真 → 生产真机中位 73.84°;终验复刻/探针用包
        #   锚点（world_xy(top_package)）达 98.5%/94.8%。由页逻辑设备 local_offsets
        #   （画布偏移 = csv_to_canvas 原始差值,与导入一致）反算包锚点世界坐标;
        #   散台/无偏移成员保持自身（与复刻口径一致）。
        pts: dict = {}
        off_by_fid: dict = {}
        try:
            for page in (self._pages if isinstance(self._pages, list) else []):
                if not isinstance(page, dict):
                    continue
                for ld in (page.get("logical_devices", []) or []):
                    _offs = ld.get("local_offsets") or {}
                    for m in (ld.get("member_fixture_ids", []) or []):
                        off = _offs.get(str(m))
                        if off:
                            off_by_fid.setdefault(
                                str(m), (float(off[0]), float(off[1])))
        except Exception:
            off_by_fid = {}
        for fid in ordered:
            fi = self.fixtures_by_id().get(str(fid))
            wx = tp._safe_float(getattr(fi, "world_x", 0.0), 0.0)
            wz = tp._safe_float(getattr(fi, "world_z", 0.0), 0.0)
            if abs(wx) > 1.0 or abs(wz) > 1.0:
                off = off_by_fid.get(str(fid))
                if off:
                    try:
                        from src.ui.import_patch_csv import (
                            DEFAULT_IMPORT_SCALE, Y_AXIS_SIGN)
                        pts[fid] = (wx - off[0] / DEFAULT_IMPORT_SCALE,
                                    wz - off[1] / (DEFAULT_IMPORT_SCALE * Y_AXIS_SIGN))
                    except Exception:
                        pts[fid] = (wx, wz)
                else:
                    pts[fid] = (wx, wz)
            else:
                pts[fid] = self.fixture_pos(str(fid))
        point_list = [pts[fid] for fid in ordered]
        try:
            # ★ 样条读提前（2026-08-05）：叶形页门控/整页基准回退需要全工程样条;
            #   读取失败 = 空列表, 与旧路径失败语义一致。
            _all_splines = list(getattr(self._current_project(), "splines", []) or [])
        except Exception:
            _all_splines = []
        out: dict = {}
        # ★ 落叶头页子集 → 整页全量基准（2026-08-05 原版语义同步, 复现探针
        #   wt_probe_subset_sync/wt_probe_debug_top16 实锤）: 子集（任意台数
        #   1..72）直接取所属叶形页整页计算值——子集参与计算不改变设备世界方向
        #   语义。整页 73 台计算值 = 该设备真实方向（全选验证正确）→ 子集方向
        #   与全集一致。全选 73 台 len(_leaf_targets)==len(ordered) → 不触发,
        #   全选路径零变化; 非叶形页设备不在 _leaf_targets → 零变化。
        try:
            _leaf_targets = set(self._leaf_page_targets_for_subset(
                ordered, _all_splines))
        except Exception:
            _leaf_targets = set()
        _leaf_sub = [f for f in ordered if f in _leaf_targets]
        if _leaf_sub and len(_leaf_targets) > len(ordered):
            _full_ang, _full_clu = self._compute_xy_reference_angles(
                list(_leaf_targets), return_cluster=True)
            _out_leaf = {f: _full_ang[f] for f in _leaf_sub if f in _full_ang}
            _cluster |= set(_leaf_sub)
            _rest = [f for f in ordered if f not in _leaf_targets]
            if _rest:
                _rest_ang, _rest_clu = self._compute_xy_reference_angles(
                    _rest, return_cluster=True)
                _out_leaf = {**_out_leaf, **_rest_ang}
                _cluster |= _rest_clu
            return _ret(_out_leaf)
        # ★ 对称双翼统一化（2026-08-05 原版语义同步）：镜像签名门控（P2 同款,
        #   全设备集 PCA 序）命中率 ≥75% 的设备组 = 对称双翼（至冬7.0 实测：
        #   落叶头 91.8% 命中 → 全组 uniform_world_angle; 底弧线 5.0%/上弧线
        #   13.9% 不命中 → 零变化）。命中设备全部写入圆簇（_cluster）。
        # ★ 落叶头子集统一化（2026-08-05 F4/F5, 与 aef81d2 原版语义同步）：
        #   子集（顶部 16 台等）镜像签名门控不足 75%（V4 实测 0%）→ 不统一 →
        #   逐台切向世界发散（37° 偏/邻居翻转 180°）。子集设备所属页整页点集
        #   落在「闭合非星形样条」（vesica 叶形, 落叶头/落叶头气爆页）上 →
        #   子集同属叶形页 → 整子集统一世界方向（与全选 73 台对称双翼统一化
        #   同机制: 推 X 世界 ≈0°、推 Y ≈90°、两翼同向、Z 扇面水平）。上弧线
        #   （盒探出样条盒 ~6000）/底弧线/百变/副池恒 False → 零变化; 下方
        #   57 台 P2=89% ≥75% 走上方短路不触发。
        if len(ordered) >= 4:
            try:
                _ax0, _ay0, _ = tp._pca_axis(point_list)
                _ord0 = sorted(
                    ordered, key=lambda f: pts[f][0] * _ax0 + pts[f][1] * _ay0)
                _sym_ratio, _sym_hits, _sym_total = tp._leaf_p2_gate_ratio(
                    pts, _ord0)
                if _sym_ratio >= 0.75:
                    _cluster |= set(ordered)
                # ★ 落叶头子集统一化（2026-08-05 原版语义同步 F4/F5）：子集镜像
                #   签名门控不足 75% → 不统一 → 逐台切向世界发散。子集设备所属
                #   页整页点集落在「闭合非星形样条」（vesica 叶形）上 → 子集同属
                #   叶形页 → 整子集统一世界方向。
                elif self._subset_pages_leaf_match(ordered, _all_splines):
                    _cluster |= set(ordered)
            except Exception:
                pass
        if len(ordered) == 1:
            return _ret(self._flip_sync_angles({ordered[0]: 0.0}, ordered))

        # ★ 工程样条 = 均匀参数自然三次样条（2026-08-03 对拍 CreateSpline3D 行为
        #   + 120 台设备实测残差 ≤0.19 世界单位，即渲染弧线本身）：弧线重合时切向
        #   直接取渲染弧线解析导数；仍须检查与设备重合，不重合不用（退回设备
        #   定位点自拟合 Catmull-Rom）。
        #   性能（2026-08-03）：全选 275 台时 deviation/param/最近点三个 Python
        #   全扫共 ~660ms（且全工程设备对单条弧线判定必拒绝，白算）——批量 numpy
        #   IEEE 逐位等价（对照 sync_angles_baseline.json 验证）。
        order_key: dict = {}
        # ★ 分簇归属（2026-08-03 用户实测：一页设备可沿多条弧线/星形排列——
        #   副池=星形+Spline 分支；只取单条弧线会让其余分支投影错误
        #   「各自为战」）。每台设备归最近且贴合（≤跨度×1%）的弧线取渲染
        #   样条解析切向；未归属设备退回设备自拟合 Catmull-Rom。
        out_arc: dict = {}
        _assign: dict = {}
        _full_ordered_before_cluster = list(ordered)
        _subset_page_fallback = False
        try:
            # ★ 分簇样条输入 = 页匹配（2026-08-04 A1 终验修正, 原版语义同步）：
            #   全工程样条会让落叶头 26 台错归属 7 条别的样条（距离 ≤1% 跨度但
            #   方向错）→ 气爆 P2 门控 71.6%<75% 不启用 → 落叶头 65.8%/气爆
            #   41.7%；页匹配（match_splines_to_page 只返回落叶头页的 7 点
            #   Spline）→ 98.5%/94.8%（P2 探针 _tmp_leaf_p2_final.py 基线）。
            #   参数与原版一致（min_overlap_ratio=0.5、min_fit_ratio=0.0 默认）。
            #   ★ 子集页级回退（2026-08-05 F2-L1 原版语义同步）：子集点集包围盒
            #   只盖叶形样条一部分 → 页匹配空 → 旧路径回退全工程样条 → 错归属
            #   别的样条（V4 实测 37° 偏）。改按子集设备所属页的整页设备集匹配
            #   （各页取并集）, 仍空才回退全样条（防御）。
            from src.ui.geometry_splines import match_splines_to_page
            _matched = match_splines_to_page(point_list, _all_splines)
            if _matched:
                _splines = _matched
            else:
                _page_matched = self._match_splines_for_subset_pages(
                    ordered, _all_splines)
                _subset_page_fallback = bool(_page_matched)
                _splines = _page_matched or _all_splines
            if _splines:
                out_arc, ordered, _assign = tp._spline_cluster_angles(
                    pts, _splines, ordered, return_assign=True)
        except Exception:
            out_arc, ordered = out_arc, ordered
        # ★ 星形样条外顶点圆簇 → 圆切向覆盖（2026-08-04 用户授权「自动加圆」）：
        #   副池三维扇子两套各 5 台散台在星形外顶点上（圆拟合 rms≈0），但摆放
        #   沿圆切向，星形切向错 72°——只在「整簇归属同一颗星形样条且紧贴一个
        #   圆」时覆盖（_star_circle_override 门控），弧线归属页面零变化。
        _circle_ov: dict = {}
        if out_arc and _assign:
            try:
                _circle_ov = tp._star_circle_override(pts, _assign, _splines)
                if _circle_ov:
                    out_arc = {**out_arc, **_circle_ov}
            except Exception:
                pass
            # ★ 圆排多设备统一化（2026-08-04 dbe8922 return_cluster 语义）：
            #   圆簇成员 = 星形外顶点圆覆盖命中设备（_star_circle_override
            #   门控）。调用方据此写 uniform_world_angle —— 弧线（底弧线/
            #   上弧线等非星形样条）恒不命中，零变化。|= 并集保留对称双翼
            #   统一化（2026-08-05）已加入的设备。
            _cluster |= set((_circle_ov or {}).keys())
            # ★ 弧线归属尖点方向修复（2026-08-04, 原版语义同步）：落叶头头部
            #   （顶部叶尖区）同步方向与底部正确方向不一致——vesica 双翼在顶部
            #   叶尖/底部叶基的汇聚点设备贴 7 点 Spline 但样条在尖点处切向与
            #   设备实际摆放方向系统性不符 19°-114°。仅「闭合非星形样条」
            #   （vesica 叶形）的弧线归属设备参与; 开放弧线/星形（底弧线/
            #   上弧线/副池/百变的 Spline 2）切向与设备一致, 零触发零变化。
            #   ★ 子集页级回退时邻居 = 整子集（2026-08-05 F2-L1 原版语义同步）：
            #   回退分支退回集空 → 旧调用邻居为空 → 尖点对不修复; 回退分支用
            #   整子集找邻居; 非回退分支保持现状（rest 邻居）。
            try:
                # ★ 子集页级回退时尖点修复邻居 = 整子集（2026-08-05 F2-L1,
                #   与 aef81d2 原版语义同步）：落叶头顶部 16 台全贴叶形样条 →
                #   退回集空 → 旧调用邻居为空 → 尖点对（锚点重合）不修复 →
                #   base 取样条尖点切向（与安装朝向差 19°-114°）→ 推 X/Y 世界
                #   偏。回退分支用整子集找邻居（翼尾邻居 ~400 不受同弧线 <300
                #   排除影响, 与全选 73 台同口径）; 非回退分支保持现状
                #   （rest 邻居）。
                _tip_fix = tp._arc_tip_direction_fix(
                    pts, out_arc,
                    _full_ordered_before_cluster if _subset_page_fallback else ordered,
                    off_by_fid, _assign, _splines)
                if _tip_fix:
                    out_arc = {**out_arc, **_tip_fix}
            except Exception:
                pass
        if not ordered:
            # ★ 落叶头顶部电机 ±90° 摆正（2026-08-05 用户实测, 原版语义同步）:
            #   全样条归属时在 out_arc 上应用; 门控与主路径同（_top_motor_align）。
            return _ret(self._flip_sync_angles(
                tp._top_motor_align(out_arc, pts, ids, _leaf_sub, _leaf_targets),
                ids))
        point_list = [pts[fid] for fid in ordered]

        if tp._is_grid_like(point_list):
            return _ret(self._flip_sync_angles(
                {**out_arc, **{fid: 0.0 for fid in ordered}}, ordered))

        # ★ 落叶头子集圆拟合排除（2026-08-05 F3 原版语义同步）：顶部 16 台子集
        #   点云近似圆弧段（r=2256 span=145° rms=174 通过门限）→ 伪圆拟合接管
        #   → 圆切向世界方向乱 + 邻居翻转 180°。子集落在「闭合非星形样条」
        #   （叶形）上 → 排除圆拟合走 CR 自拟合; 副池真圆/星形外顶点等不命中
        #   （副池 z>20000 远在叶形样条盒外）→ 圆拟合路径逐字节不变。
        if tp._subset_on_closed_leaf_spline(pts, ordered, _all_splines):
            fit = None
        else:
            fit = tp._fit_circle(point_list)
        if fit is not None:
            cx, cy, radius, rms = fit
            min_radius = min((math.hypot(x - cx, y - cy) for x, y in point_list), default=0.0)
            span = tp._angular_span_deg(point_list, cx, cy)
            if span >= 25.0 and min_radius >= max(5.0, radius * 0.35) and rms <= max(6.0, radius * 0.08):
                circular = span >= 300.0 and len(ordered) >= 4
                if circular:
                    angle_order = sorted(
                        ordered,
                        key=lambda item: math.atan2(pts[item][1] - cy, pts[item][0] - cx)
                    )
                    ordered_index = {fid: idx for idx, fid in enumerate(angle_order)}
                else:
                    angle_order = ordered
                    ordered_index = {fid: idx for idx, fid in enumerate(ordered)}
                for fid in ordered:
                    idx = ordered_index.get(fid, 0)
                    x, y = pts[fid]
                    tx, ty = -(y - cy), x - cx
                    if abs(tx) <= 1e-9 and abs(ty) <= 1e-9:
                        tx, ty = 1.0, 0.0
                    if circular:
                        prev_id = angle_order[(idx - 1) % len(angle_order)]
                        next_id = angle_order[(idx + 1) % len(angle_order)]
                    else:
                        prev_id = ordered[idx - 1] if idx > 0 else None
                        next_id = ordered[idx + 1] if idx < len(ordered) - 1 else None
                    if prev_id and next_id:
                        px, py = pts[prev_id]
                        nx, ny = pts[next_id]
                        ndx, ndy = nx - px, ny - py
                        _has_nd = True
                    elif next_id:
                        nx, ny = pts[next_id]
                        ndx, ndy = nx - x, ny - y
                        _has_nd = True
                    elif prev_id:
                        px, py = pts[prev_id]
                        ndx, ndy = x - px, y - py
                        _has_nd = True
                    else:
                        ndx, ndy = tx, ty
                        _has_nd = False
                    _ang = _ce_tangent_to_angle(tx, ty, ndx, ndy, _has_nd, False)
                    if _ang is None:
                        if tx * ndx + ty * ndy < 0.0:
                            tx, ty = -tx, -ty
                        _ang = tp._normalize_angle_deg(
                            -math.degrees(math.atan2(ty, tx)))
                    out[fid] = _ang
                return _ret(self._flip_sync_angles({**out_arc, **out}, ordered))

        # ★ 主池圆页逐圆切线（2026-08-08 bd80ece 原版语义同步）：整页点云含
        #   9 个同心放大环圆 → 整页 _fit_circle rms 巨大（1677 vs 门限 530）
        #   失败、样条分簇空（设备在 1.25~1.65R 放大环上贴样条失败）→ 旧路径
        #   CR 把多圆混成一条线 → 等差伪切线乱角。按整圆/星形锚点分簇
        #   （_circle_anchor_centers），每环单独切线角（与上方圆拟合分支同公式
        #   +邻居方向修正）。调用方 = TopViewPanel._circle_anchor_cluster_angles
        #   Python 原版方法（锚点分簇纯 Python 逻辑, 无 C 内核, 胶水直调——
        #   与方法体内 _star_circle_override/_arc_tip_direction_fix 同模式）。
        #   无条件执行（2026-08-08 修正）：气爆页 out_arc=30 非空时旧条件跳过
        #   本分支 → 318 台仍走 CR 伪切线。输入用 _full_ordered_before_cluster
        #   （样条分簇会过滤 ordered——气爆 30 台被接管后其余 318 台从列表消失,
        #   必须用原始全量）。副池扇子等已有 _star_circle_override 正确覆盖的
        #   路径:锚点分簇与圆切向同公式, 结果一致; 环带外设备保留 out_arc 值。
        #   try/except 与原版同式（含 _DummyPanel 无此方法的 fuzz 路径 → 空）。
        try:
            _circ_anchor = self._circle_anchor_cluster_angles(
                pts, _full_ordered_before_cluster, _all_splines)
        except Exception:
            _circ_anchor = {}
        if _circ_anchor:
            _merged_anchor = {**_circ_anchor}
            for _f in out_arc:
                if _f not in _merged_anchor:
                    _merged_anchor[_f] = out_arc[_f]
            # ★ 二维数控页统一世界方向（2026-08-09 5e87030 原版语义同步）：
            #   锚点分簇环上的 2D XY 设备（def 含 motor_x+motor_y 且无
            #   motor_z——改版至冬1 = 二维数控页 240 台 2-Axis Jet）并入圆簇 →
            #   uniform_world_angle=True → 引擎输出统一世界方向（本地 X 沿世界
            #   横坐标、Y 沿世界纵坐标, 随全局角整体旋转）。三维设备（Nozzle
            #   Fan 含 motor_z）不入簇 → 逐台切线现状逐位不变。
            _cluster |= {_f for _f in _merged_anchor
                         if self._fixture_is_2d_xy(str(_f))}
            return _ret(self._flip_sync_angles(
                _merged_anchor, _full_ordered_before_cluster))

        # ★ 设备定位点自拟合平滑曲线（用户指正：折线≠弧线）：弦长参数化
        #   Catmull-Rom 过每台设备，导数 = 安装切向。
        # ★ 闭合环检测（2026-08-03 agent E 实测：落叶头 73 台=平滑闭合环线）。
        #   ★ 2026-08-04 修正（原版语义同步）：原「选择序首尾距」判定依赖输入
        #   序，而生产页面序受 set 遍历 hash 种子影响每进程不同 → 首尾距随机 →
        #   闭环漏判/误判 → P2 偶用坏序输出乱角。闭环检测统一在 PCA 投影序上
        #   做（只依赖坐标，确定性）；P2 门控/apply 恒在 PCA 序上算（探针
        #   _tmp_leaf_p2_final.py 同口径 98.5%/94.8%）；闭环 CR 用绕质心角度
        #   环序（平滑切线，确定性）。
        closed_loop = False
        if not order_key and len(ordered) >= 4:
            axis_x, axis_y, _ = tp._pca_axis(point_list)
            for fid in ordered:
                order_key[fid] = pts[fid][0] * axis_x + pts[fid][1] * axis_y
            _pc = sorted(ordered, key=lambda fid: order_key[fid])
            x0, y0 = pts[_pc[0]]
            x1, y1 = pts[_pc[-1]]
            d_fl = math.hypot(x1 - x0, y1 - y0)
            gaps = sorted(
                math.hypot(pts[_pc[k + 1]][0] - pts[_pc[k]][0],
                           pts[_pc[k + 1]][1] - pts[_pc[k]][1])
                for k in range(len(_pc) - 1))
            if gaps and d_fl <= gaps[len(gaps) // 2] * 1.5:
                closed_loop = True
        if not order_key:
            # 退化防御（2-3 台子集, 与旧 hook 语义一致）：原版闭环块仅
            # len>=4 时填充 order_key, <4 台直接 sorted 空键表 → KeyError。
            # 补 PCA 投影序（确定性, 仅坐标依赖）——>=4 台路径恒不触发,
            # 编排段与原版逐位一致不受影响。
            axis_x, axis_y, _ = tp._pca_axis(point_list)
            for fid in ordered:
                order_key[fid] = pts[fid][0] * axis_x + pts[fid][1] * axis_y
        ordered_sorted = sorted(ordered, key=lambda fid: order_key[fid])
        cr_order = ordered_sorted
        if closed_loop and len(ordered) >= 4:
            _cx = sum(pts[f][0] for f in ordered) / len(ordered)
            _cy = sum(pts[f][1] for f in ordered) / len(ordered)
            cr_order = sorted(
                ordered,
                key=lambda f: math.atan2(pts[f][1] - _cy, pts[f][0] - _cx))
        # ★ 落叶头双翼 P2 修复（2026-08-04, 探针 _tmp_leaf_p2_final.py 固化）：
        #   双翼 zigzag 交错序下 ±1 邻居 = 对侧翼方向、±2 邻居 = 同侧翼——
        #   现状 CR 自拟合用 ±1 邻居弦向, 顶部收拢区方向差 19°-147°。
        #   镜像签名门控（每台 circ_mod180(t1+t2)<20° 命中, 占比 ≥75%）通过
        #   才启用 P2（V4 实测：落叶头 91%/气爆 93%, 百变 35% 不启用 →
        #   门控不通过时保持现状 CR 零变化）。
        p2_map = None
        if len(ordered_sorted) >= 4:
            try:
                _p2_ratio, _p2_hits, _p2_total = tp._leaf_p2_gate_ratio(
                    pts, ordered_sorted)
                if _p2_ratio >= 0.75:
                    p2_map = tp._leaf_p2_apply(pts, ordered_sorted)
            except Exception:
                p2_map = None
        ts = [0.0]
        for k in range(1, len(cr_order)):
            x0, y0 = pts[cr_order[k - 1]]
            x1, y1 = pts[cr_order[k]]
            ts.append(ts[-1] + math.hypot(x1 - x0, y1 - y0))
        out = {}
        for idx, fid in enumerate(cr_order):
            if p2_map is not None:
                # P2 门控通过：±2 邻居（同侧翼）弦向 + 几何翼判定, 已含
                # ±90 钳位与画布镜像（探针逐台对拍一致）
                out[fid] = p2_map.get(fid, 0.0)
                continue
            dx, dy = tp._catmull_rom_tangent(pts, cr_order, idx, ts,
                                             closed=closed_loop)
            _ang = _ce_tangent_to_angle(dx, dy, 0.0, 0.0, False, True)
            if _ang is None:
                _ang = tp._normalize_angle_deg(-math.degrees(math.atan2(dy, dx)))
                if _ang > 90.0:
                    _ang -= 180.0
                elif _ang < -90.0:
                    _ang += 180.0
            out[fid] = _ang
        _merged = {**out_arc, **out}
        # ★ 落叶头子集方向门（2026-08-05 F2-L2 原版语义同步, 仅子集页级回退
        #   分支）：顶部中心区左右翼交错 → 序邻居弦向近水平 → base 偏 22°;
        #   最近邻弦向（翼方向）修正, 非回退分支零变化。
        if _subset_page_fallback and _merged:
            _merged = tp._leaf_subset_direction_gate(
                pts, _full_ordered_before_cluster, _merged)
        # ★ 落叶头顶部电机同步基准硬修正（2026-08-05 用户实测 90° 错位, 原版
        #   语义同步）: 整页叶形基准（全选 73 / 子集回退递归）顶部 22 台 ±90°
        #   摆正, 与底部全朝右对齐; 其他页/底部/中部零变化（门控见
        #   _top_motor_align, 与 topview_panel 原版同函数）。
        _merged = tp._top_motor_align(_merged, pts, ids, _leaf_sub, _leaf_targets)
        return _ret(self._flip_sync_angles(_merged, ordered))

    tp.TopViewPanel._compute_xy_reference_angles = ce_compute_xy_reference_angles
    log.info("[CE] T10 A2b 已接入: _compute_xy_reference_angles 同步角段 → C"
             "（return_cluster 透传 2026-08-04/05 圆簇语义）")

    # ── B1: RANSAC 多圆贪心提取 → ce_ransac_circles ──────────────────────
    orig_ransac = tag._detect_circles_ransac

    def ce_detect_circles_ransac(positions, seed=0, min_pts=5, iters=400):
        if engine.ce_ransac_circles is None:
            return orig_ransac(positions, seed=seed, min_pts=min_pts, iters=iters)
        try:
            pts_all = [(str(fid), float(x), float(y)) for fid, (x, y) in positions.items()]
        except Exception:
            return orig_ransac(positions, seed=seed, min_pts=min_pts, iters=iters)
        n = len(pts_all)
        if n < min_pts:
            return []
        try:
            s = int(seed)
        except Exception:
            return orig_ransac(positions, seed=seed, min_pts=min_pts, iters=iters)
        if not (-(2 ** 63) <= s < 2 ** 63):
            # C long long 域外 → Python 任意精度 & 0x7FFFFFFF 不可等价 → 原版
            return orig_ransac(positions, seed=seed, min_pts=min_pts, iters=iters)
        if not isinstance(min_pts, int) or not isinstance(iters, int):
            # 原版 range(iters) 对 float iters 抛 TypeError; int() 会吞掉 → 原版
            return orig_ransac(positions, seed=seed, min_pts=min_pts, iters=iters)
        try:
            xs = np.fromiter((p[1] for p in pts_all), np.float64, n)
            ys = np.fromiter((p[2] for p in pts_all), np.float64, n)
            if not (np.isfinite(xs).all() and np.isfinite(ys).all()):
                # NaN/inf 坐标: C min/max/qsort 语义分歧域 → 原版
                return orig_ransac(positions, seed=seed, min_pts=min_pts, iters=iters)
            out_cx = np.zeros(max(1, n), np.float64)
            out_cy = np.zeros(max(1, n), np.float64)
            out_r = np.zeros(max(1, n), np.float64)
            out_cnt = np.zeros(max(1, n), np.int32)
            out_midx = np.zeros(max(1, n), np.int32)
            out_mang = np.zeros(max(1, n), np.float64)
            nc = engine.ce_ransac_circles(
                xs.ctypes.data_as(_DP), ys.ctypes.data_as(_DP), n,
                ctypes.c_longlong(s), int(min_pts), int(iters),
                out_cx.ctypes.data_as(_DP), out_cy.ctypes.data_as(_DP),
                out_r.ctypes.data_as(_DP), out_cnt.ctypes.data_as(_IP32),
                out_midx.ctypes.data_as(_IP32), out_mang.ctypes.data_as(_DP))
            if nc < 0:
                # C 分配失败 → 原版
                return orig_ransac(positions, seed=seed, min_pts=min_pts, iters=iters)
            circles = []
            member_total = 0
            for i in range(nc):
                cnt = int(out_cnt[i])
                members = []
                for t in range(cnt):
                    pidx = int(out_midx[member_total + t])
                    members.append((pts_all[pidx][0],
                                    float(out_mang[member_total + t])))
                member_total += cnt
                circles.append(tag.CircleInfo(
                    cx=float(out_cx[i]), cy=float(out_cy[i]), r=float(out_r[i]),
                    members=members))
            _TOPVIEW_CNT["ransac"] += 1
            return circles
        except Exception:
            return orig_ransac(positions, seed=seed, min_pts=min_pts, iters=iters)

    tag._detect_circles_ransac = ce_detect_circles_ransac
    log.info("[CE] T10 B1 已接入: _detect_circles_ransac → ce_ransac_circles")

    # ── B2: 已知圆/椭圆归属聚类 → ce_assign_shapes ───────────────────────
    orig_assign = tag._assign_to_shapes

    def ce_assign_to_shapes(positions, circle_shapes):
        if engine.ce_assign_shapes is None:
            return orig_assign(positions, circle_shapes)
        try:
            glued = []
            for s in circle_shapes or []:
                cx = float(s.get("cx"))
                cy = float(s.get("cy"))
                rx = abs(float(s.get("rx", s.get("r", 0.0))))
                ry = abs(float(s.get("ry", rx)))
                glued.append((cx, cy, rx, ry))
        except Exception:
            return orig_assign(positions, circle_shapes)
        try:
            items = list(positions.items())
            for _k, v in items:
                x, y = v
                if not (isinstance(x, _NUM) and isinstance(y, _NUM)):
                    # Decimal/Fraction 等精确类型: float() 舍入不可逐位等价 → 原版
                    return orig_assign(positions, circle_shapes)
            n_pts = len(items)
            xs = np.fromiter((float(v[0]) for _, v in items), np.float64, n_pts)
            ys = np.fromiter((float(v[1]) for _, v in items), np.float64, n_pts)
            n_shapes = len(glued)
            if n_shapes == 0:
                return []
            scx = np.fromiter((g[0] for g in glued), np.float64, n_shapes)
            scy = np.fromiter((g[1] for g in glued), np.float64, n_shapes)
            srx = np.fromiter((g[2] for g in glued), np.float64, n_shapes)
            sry = np.fromiter((g[3] for g in glued), np.float64, n_shapes)
            out_cx = np.zeros(n_shapes, np.float64)
            out_cy = np.zeros(n_shapes, np.float64)
            out_r = np.zeros(n_shapes, np.float64)
            out_cnt = np.zeros(n_shapes, np.int32)
            out_midx = np.zeros(max(1, n_pts), np.int32)
            out_mang = np.zeros(max(1, n_pts), np.float64)
            ng = engine.ce_assign_shapes(
                xs.ctypes.data_as(_DP), ys.ctypes.data_as(_DP), n_pts,
                scx.ctypes.data_as(_DP), scy.ctypes.data_as(_DP),
                srx.ctypes.data_as(_DP), sry.ctypes.data_as(_DP), n_shapes,
                out_cx.ctypes.data_as(_DP), out_cy.ctypes.data_as(_DP),
                out_r.ctypes.data_as(_DP), out_cnt.ctypes.data_as(_IP32),
                out_midx.ctypes.data_as(_IP32), out_mang.ctypes.data_as(_DP))
            if ng < 0:
                return orig_assign(positions, circle_shapes)
            circles = []
            member_total = 0
            for i in range(ng):
                cnt = int(out_cnt[i])
                if cnt <= 0:
                    continue
                members = []
                for t in range(cnt):
                    pidx = int(out_midx[member_total + t])
                    # 原版成员 fid = str(fid)（原始键 str 化, 同 RANSAC 语义）
                    members.append((str(items[pidx][0]),
                                    float(out_mang[member_total + t])))
                member_total += cnt
                circles.append(tag.CircleInfo(
                    cx=float(out_cx[i]), cy=float(out_cy[i]), r=float(out_r[i]),
                    members=members))
            _TOPVIEW_CNT["assign"] += 1
            return circles
        except Exception:
            return orig_assign(positions, circle_shapes)

    tag._assign_to_shapes = ce_assign_to_shapes
    log.info("[CE] T10 B2 已接入: _assign_to_shapes → ce_assign_shapes")

    # ── B3: 三点定圆 → ce_circle_from_3 ──────────────────────────────────
    orig_cf3 = tag._circle_from_3

    def ce_circle_from_3(p1, p2, p3):
        if engine.ce_circle_from_3 is None:
            return orig_cf3(p1, p2, p3)
        try:
            a0, a1 = p1
            b0, b1 = p2
            c0, c1 = p3
        except Exception:
            return orig_cf3(p1, p2, p3)
        if not all(isinstance(v, _NUM) for v in (a0, a1, b0, b1, c0, c1)):
            return orig_cf3(p1, p2, p3)
        try:
            out_cx = ctypes.c_double()
            out_cy = ctypes.c_double()
            out_r = ctypes.c_double()
            rc = engine.ce_circle_from_3(
                float(a0), float(a1), float(b0), float(b1),
                float(c0), float(c1),
                ctypes.byref(out_cx), ctypes.byref(out_cy), ctypes.byref(out_r))
        except Exception:
            return orig_cf3(p1, p2, p3)
        if rc == 0:
            return None
        _TOPVIEW_CNT["cf3"] += 1
        return (out_cx.value, out_cy.value, out_r.value)

    tag._circle_from_3 = ce_circle_from_3
    log.info("[CE] T10 B3 已接入: _circle_from_3 → ce_circle_from_3")

    # ── B4: 圆主轴排序 → ce_circle_axis_order ────────────────────────────
    orig_axis = tag.AutoGroupEngine._circle_axis_order

    def ce_circle_axis_order(self):
        if engine.ce_circle_axis_order is None:
            return orig_axis(self)
        cs = self.circles
        n = len(cs)
        try:
            cxa = np.fromiter((c.cx for c in cs), np.float64, n)
            cya = np.fromiter((c.cy for c in cs), np.float64, n)
        except Exception:
            return orig_axis(self)
        if not (np.isfinite(cxa).all() and np.isfinite(cya).all()):
            # NaN 圆心: C qsort 比较器无全序（UB 序）→ 原版（稳定排序语义）
            return orig_axis(self)
        try:
            out = np.zeros(n, np.int32)
            rc = engine.ce_circle_axis_order(
                cxa.ctypes.data_as(_DP), cya.ctypes.data_as(_DP), n,
                out.ctypes.data_as(_IP32))
        except Exception:
            return orig_axis(self)
        if rc != n:
            return orig_axis(self)
        _TOPVIEW_CNT["axis"] += 1
        return out.tolist()

    tag.AutoGroupEngine._circle_axis_order = ce_circle_axis_order
    log.info("[CE] T10 B4 已接入: _circle_axis_order → ce_circle_axis_order")

    # ── B5: 单圆相位窗口取成员 → ce_circle_window_order ──────────────────
    orig_window = tag.AutoGroupEngine._members_in_window

    def ce_members_in_window(self, circle, center_phase, half_width):
        if engine.ce_circle_window_order is None:
            return orig_window(self, circle, center_phase, half_width)
        try:
            cp_f = float(center_phase)
        except Exception:
            return orig_window(self, circle, center_phase, half_width)
        if not isinstance(center_phase, _NUM) or not math.isfinite(cp_f):
            # 非有限 center_phase: C fmod 与 Python % 的 inf/NaN 语义分歧域 → 原版
            return orig_window(self, circle, center_phase, half_width)
        try:
            members = self._scoped_members(circle)
            n = len(members)
            if n == 0:
                return []
            angs = np.fromiter((float(a) for _, a in members), np.float64, n)
        except Exception:
            return orig_window(self, circle, center_phase, half_width)
        if not np.isfinite(angs).all():
            return orig_window(self, circle, center_phase, half_width)
        try:
            out = np.zeros(n, np.int32)
            cnt = engine.ce_circle_window_order(
                angs.ctypes.data_as(_DP), None, n,
                float(center_phase), float(half_width), out.ctypes.data_as(_IP32))
        except Exception:
            return orig_window(self, circle, center_phase, half_width)
        if cnt < 0:
            return orig_window(self, circle, center_phase, half_width)
        _TOPVIEW_CNT["window"] += 1
        res = []
        for t in range(cnt):
            i = int(out[t])
            fid, ang = members[i]
            off = tag._ang_diff(ang, center_phase)
            res.append((fid, ang, off))
        return res

    tag.AutoGroupEngine._members_in_window = ce_members_in_window
    log.info("[CE] T10 B5 已接入: _members_in_window → ce_circle_window_order")
    log.info("[CE] T10 全部接入完成（QASCADE_CT_ENGINE=1 生效; 失败逐点回退原版）")


# ═══ K2: 离线时间线整帧融合（QASCADE_CE_TIMELINE_RANGE=1, 2026-08-10 flip）═══
from .native_source_motion_types import CURVES as _K2_CURVE_IDX
_K2_STRAT_IDX = {"standard_fade": 0, "immediate": 1, "decelerate": 2,
                 "hold_decay": 3, "step_down": 4, "smooth_ease": 5,
                 "delay_then_cut": 6}


class _K2SortKey:
    """= playback_engine._ClipSortKey 比较器（结构缓存序）:
    **严格后发接管** —— start_time 降序 → track_order 降序。
    ★ 2026-09-19 用户定案：层级不参与接管，只决定恢复（见 `_is_restorable_takeover`）。"""

    __slots__ = ("chain", "item", "start", "track_order")

    def __init__(self, item, folder_chain_fn):
        self.start = float(item[0])
        self.track_order = int(item[1])
        self.chain = folder_chain_fn(item[2])
        self.item = item

    def __lt__(self, other):
        # 严格后发接管（与 Python 源逐条同式）：层级不参与，只按时间/轨道序。
        if self.start != other.start:
            return self.start > other.start
        return self.track_order > other.track_order


class _K2FrameCacheNetworkProxy:
    """= playback_engine._render_timeline_frame_range 内局部类逐字复刻
    （:691-746; 局部类不可导入, 必须复刻才能与原版逐字节同行为）。"""

    def __init__(self, target, build_mode: bool = False):
        self._target = target
        # 构建期(build_mode=True)跳过变化检测: 逐帧渲染时 changed_universes
        # 的唯一消费者 _flush_timeline_output_now → proxy flush_universes_now
        # 是 no-op, 两次 np.array_equal(34.9 次/帧) 属死工作。实时回放路径
        # 用真 NM, 不经过本 proxy, 不受影响。
        self._build_mode = bool(build_mode)
        self._layers = {
            "clip_playback": {},
            "clip_preview": {},
            "timeline": {},
        }

    def __getattr__(self, name):
        return getattr(self._target, name)

    def update_layer(self, layer_name, universe, data, active=None, *args, **kwargs):
        layer = self._layers.setdefault(str(layer_name), {})
        data_arr = np.asarray(data, dtype=np.uint8)
        if data_arr.shape != (512,):
            fixed = np.zeros(512, dtype=np.uint8)
            n = min(data_arr.size, 512)
            if n > 0:
                fixed[:n] = data_arr.reshape(-1)[:n]
            data_arr = fixed
        if active is None:
            active_arr = np.ones(512, dtype=bool)
        else:
            active_arr = np.asarray(active, dtype=bool)
            if active_arr.shape != (512,):
                fixed_active = np.zeros(512, dtype=bool)
                n = min(active_arr.size, 512)
                if n > 0:
                    fixed_active[:n] = active_arr.reshape(-1)[:n]
                active_arr = fixed_active
        prev = layer.get(int(universe))
        layer[int(universe)] = (data_arr.copy(), active_arr.copy())
        if self._build_mode:
            return True
        if (prev is not None and np.array_equal(prev[0], data_arr)
                and np.array_equal(prev[1], active_arr)):
            return False
        return True

    def clear_layer(self, layer_name, universe=None, *args, **kwargs):
        layer = self._layers.setdefault(str(layer_name), {})
        if universe is None:
            layer.clear()
        else:
            layer.pop(int(universe), None)

    def flush_universes_now(self, *args, **kwargs):
        return None

    def notify_clip_takeover(self, *args, **kwargs):
        return None


class _K2WholeSegmentFallback(Exception):
    """K2 帧缓存整段回退信号（C 不可用/内核拒绝/异常 → 恢复状态后委托原版）。"""


def _frozen_render_lab_shim():
    """exe 内 render_lab 命名空间垫片（2026-08-10 冻结风险修复）。

    timeline_ctx.py:32 模块级 `from render_lab.qascade_ce.ctypes_api import
    load_or_none` 依赖 render_lab 命名空间。打包构建只加 --paths render_lab/
    （tools/ce_pack_manifest.py 以顶层名 qascade_ce.* 收闭包, 不产出 render_lab
    包）→ exe 内该导入必炸 → 不垫片则 K2 hook 整体跳过、flip 在 exe 内静默
    失效（与 8-05 CE 事故同型的静默回退, 只是本链无害）。垫片把
    render_lab → 当前 qascade_ce 模块（ctypes_api 子模块经真实 __path__ 再载,
    load_or_none 仅作 TimelineCtx(engine=None) 兜底, 本 hook 恒传 engine,
    第二副本零副作用）。仅当 render_lab 未被任何模块导入时插入 —— dev 环境
    经仓库根正常解析, 零影响; 打包侧根治（--paths ROOT）合入后同样自动跳过。
    """
    if "render_lab" in sys.modules:
        return
    try:
        import qascade_ce as _qce
    except Exception:
        return
    import types as _types
    _fake = _types.ModuleType("render_lab")
    _fake.__path__ = []
    _fake.qascade_ce = _qce
    sys.modules["render_lab"] = _fake
    sys.modules["render_lab.qascade_ce"] = _qce


def _k2_lt_nm_live(self, uni, exclude_clip):
    """K2 LT 束装配: nm/live 读取（同 _hook_lt 的 _lt_live_fn/_lt_nm_fn）。"""
    nm0 = getattr(self, "_nm", None)
    if nm0 is None:
        return None
    try:
        if exclude_clip:
            return nm0.compute_universe_now(
                int(uni), exclude_layers=("clip_playback", "clip_preview",
                                          "timeline"))
        return nm0.compute_universe_now(int(uni))
    except Exception:
        return None


def _k2_lt_clip_frames(seen_clips, ck, self, u):
    """K2 LT 束装配: 本 clip 帧列表（_clip_frame_index_for_universe 同源）。"""
    clip = seen_clips.get(str(ck))
    if clip is None:
        return []
    return self._clip_frame_index_for_universe(clip, int(u))


def _k2_lt_find_pump(seen_clips, self, suni, ck):
    """K2 LT 束装配: 泵帧查找（_lt_find_pump_frames 语义, 返回 (帧, t0)/None）。"""
    clip = seen_clips.get(str(ck))
    if clip is None:
        return None
    return self._lt_find_pump_frames(int(suni), clip)


def _k2_build_lt_context(self, proj, lt_frame_tasks):
    """K2-2026-08-11: LT 束上下文 — 对全部 LT 任务 (clip,u) 构建 LtScope
    （与 ce_apply_clip_lt 的 _skey 缓存 + state_slots/color_slots 同源）,
    scopes/state 跨帧持有。无 LT 任务 → None。scope 空（None）任务不入束。

    返回 (scopes, st, params_by_key, defs_by_id, fx_by_id, all_fx,
    seen_clips) 或 None:
      scopes: {(clip_key, u): LtScope} — 束装配 scope_of 用
      st: LtEngineState — C 侧 LT 状态槽（跨帧连续演进, 每帧 back_write）
      params_by_key: {(clip_key, u): LtParams} — 束装配复用
      fx_by_id / defs_by_id / all_fx: build_lt_scope 参数复用
    """
    if not lt_frame_tasks:
        return None
    try:
        from qascade_ce import lt_path as _ltp
    except Exception:
        try:
            from render_lab.qascade_ce import lt_path as _ltp
        except Exception:
            return None
    try:
        from src.engine.playback_engine import (
            DEFAULT_LIGHT_TRACKING_ATTACK, DEFAULT_LIGHT_TRACKING_RELEASE)
    except Exception:
        DEFAULT_LIGHT_TRACKING_ATTACK = 0.08
        DEFAULT_LIGHT_TRACKING_RELEASE = 0.22
    from src.engine.light_tracking_structure import structure_memo
    memo, rebuilt = structure_memo(self, proj)
    struct_sig, all_fx = memo[0], memo[4]
    if rebuilt:
        try:
            self._lt_pkg_src_to_lights = self._lt_ensure_pkg_map(
                proj, memo[2], "pump")
        except Exception:
            self._lt_pkg_src_to_lights = {}
    state_slots = getattr(self, "_lt_c_state_slots", None)
    if state_slots is None:
        state_slots = {}
        self._lt_c_state_slots = state_slots
    color_slots = getattr(self, "_lt_c_color_slots", None)
    if color_slots is None:
        color_slots = {}
        self._lt_c_color_slots = color_slots
    scope_cache = getattr(self, "_lt_c_scope_cache", None)
    if scope_cache is None:
        scope_cache = {}
        self._lt_c_scope_cache = scope_cache

    def _params_for(clip, source):
        threshold = max(0, min(255, int(getattr(
            clip, "light_tracking_threshold", 128) or 128)))
        attack = max(0.01, float(getattr(
            clip, "light_tracking_attack", DEFAULT_LIGHT_TRACKING_ATTACK)
            or DEFAULT_LIGHT_TRACKING_ATTACK))
        release = max(0.01, float(getattr(
            clip, "light_tracking_release", DEFAULT_LIGHT_TRACKING_RELEASE)
            or DEFAULT_LIGHT_TRACKING_RELEASE))
        color = str(getattr(clip, "light_tracking_color", "#FFFFFF")
                    or "#FFFFFF").strip().upper()
        color_is_white = (color.lstrip("#") in ("FFFFFF", "ffffff", "")
                          or len(color.lstrip("#")) != 6)
        if color.startswith("#"):
            color = color[1:]
        if len(color) != 6:
            color = "FFFFFF"
        try:
            cr = int(color[0:2], 16)
            cg = int(color[2:4], 16)
            cb = int(color[4:6], 16)
        except Exception:
            cr, cg, cb = 255, 255, 255
        return _ltp.LtParams(source, threshold, attack, release,
                             color_is_white, cr, cg, cb,
                             getattr(clip, "light_tracking_max_brightness", None))

    scopes = {}
    params_by_key = {}
    seen_clips = {}
    for idx, tasks in lt_frame_tasks.items():
        for clip, u, _start_t in tasks:
            key = (str(getattr(clip, "id", "") or id(clip)), int(u))
            if key in scopes:
                continue
            source = str(getattr(clip, "light_tracking_source", "pump")
                         or "pump").strip().lower()
            if source not in {"pump", "valve"}:
                source = "pump"
            params = _params_for(clip, source)
            params_by_key[key] = params
            clip_key = key[0]
            _skey = (clip_key, int(u), struct_sig, source,
                     params.threshold, params.attack, params.release,
                     params.color_is_white, params.cr, params.cg, params.cb,
                     params.max_brightness,
                     self._clip_static_signature(clip))
            scope = scope_cache.get(_skey)
            if scope is None:
                light_mask = np.asarray(self._clip_light_tracking_active_mask(
                    clip, int(u), proj), dtype=np.uint8)
                src_active_mask = np.asarray(
                    self._light_tracking_active_source_mask(
                        clip, int(u), 512), dtype=np.uint8)
                clip_unis = [int(x) for x in (clip.clip_universes or [])]
                scope = _ltp.build_lt_scope(
                    clip_key, int(u), all_fx, memo[2],
                    getattr(self, "_lt_pkg_src_to_lights", None) or {},
                    source, light_mask, src_active_mask, clip_unis, params,
                    state_slots, color_slots,
                    memo[3] if len(memo) > 3 else {})
                if len(scope_cache) > 8192:
                    scope_cache.clear()
                if scope is not None:
                    scope_cache[_skey] = scope
            if scope is not None:
                scopes[key] = scope
            seen_clips[clip_key] = clip
    if not scopes:
        return None
    n_state = len(state_slots)
    n_color = len(color_slots)
    st = getattr(self, "_lt_c_state", None)
    if _ltp.lt_state_needs_rebuild(st, n_state, n_color, self._light_tracking_state,
                                   self._light_tracking_time, self._light_tracking_color_cache):
        st = _ltp.LtEngineState(state_slots, color_slots)
        st.seed_from(self._light_tracking_state,
                     self._light_tracking_time,
                     self._light_tracking_color_cache)
        self._lt_c_state = st
    return (scopes, st, params_by_key, memo[3] if len(memo) > 3 else {},
            memo[2], all_fx, seen_clips)


def _hook_timeline_range(engine):
    """K2: _render_timeline_frame_range 整段 → TimelineCtx 静态表 + ce_timeline_frame
    （每帧 1 次 ctypes 调用覆盖 fetch+resolve+mix 全链）。

    接入点: PlaybackEngine._render_timeline_frame_range（:671-866; 生产版
    probe_timeline_k2_bytecheck.py build_task_tables — 示例工程 7149 帧逐字节
    0 diff + probe_record_bytecheck.py record 子集同构）。语义:
      * 整段串行 [0, frame_count) 才走 C; 并行 worker 分段/越界段 → 原版。
      * 任务表与 _render_position 同源: _indexed_active_clip_items +
        _ClipSortKey 序（= 本模块 _K2SortKey 序）; 帧内任一任务不过
        clip_eligible_fast_path（hold_ext / 非 Fast-Path 条件）→ 整帧回退
        _render_position（逐帧 Python, 输出逐字节同）。K7 方向 fade 经
        DirRowBuilder 平行表（per_frame_dirs/record_frame_dirs）接管 — 方向
        clip 不再排除, dir 行与任务行一一对应（探针 dir 字节对拍 0 diff）。
      * 帧缓存输出格式与原版一致: {u: (data512, act512)}; record_source_cache
        逐 idx 无条件写（record 子集表经 render_frame(record=True), 恒无 LT）。
      * 整段回退: ctx 不可用 / build_tables 失败 / 任一帧内核返回码 <0 /
        异常 → 恢复状态后委托原版（_CE_EXC_CNT + FALLBACK 计数）。
      * C 帧状态模拟: _position/_last_render_time = pos（后续 Python 帧 dt
        公式链不断裂）, _lt_active_clips 防御性快照（= :3617 同源）; 不触碰
        _last_output_universes_by_layer / _last_output_universes（须等于 proxy
        内容, C 帧不写 proxy — 层映射勘误见 flip_wiring_report.md）; 跳过
        _precharge_scheduler.tick / _render_lock（探针 7149 帧实证对
        Fast-Path 帧无输出影响）。
    """
    if os.environ.get("QASCADE_CE_TIMELINE_RANGE", "0") != "1":
        log.info("[CE] K2 门控关闭 (QASCADE_CE_TIMELINE_RANGE=1 启用)")
        return
    try:
        from src.engine import playback_engine as _pe
        from src.engine.light_tracking_scope import lt_clips_with_gap_reset as _lt_gap_reset
    except Exception as _e:
        log.info("[CE] K2 跳过: playback_engine 导入失败 %s（保持 Python 路径）", _e)
        return
    if engine is None or getattr(engine, "ce_timeline_frame", None) is None:
        log.info("[CE] K2 跳过: ce_timeline_frame 符号缺失（保持 Python 路径）")
        return
    # 冻结态导入链: exe 内 qascade_ce 顶层包优先, render_lab 命名空间兜底;
    # 双失败 → 不装 hook（Python 路径安全）。timeline_ctx.py:32 的
    # render_lab.* 模块级导入在 exe 内不可解析 → 先 _frozen_render_lab_shim
    # 垫片恢复（详见该函数注释; 打包侧根治 = --paths ROOT, 见报告）。
    _frozen_render_lab_shim()
    try:
        from qascade_ce import timeline_ctx as _tctx
    except Exception:
        try:
            from render_lab.qascade_ce import timeline_ctx as _tctx
        except Exception as _e2:
            log.info("[CE] K2 跳过: timeline_ctx 导入失败 %s（保持 Python 路径）", _e2)
            return

    orig = _pe.PlaybackEngine._render_timeline_frame_range

    def _k2_prepare_checkpoint(ctx, idx, callback):
        """Bound table preparation and honor the original bake cancellation."""
        if idx % 32:
            return
        try:
            time.sleep(0.001)
            if callback is not None:
                callback()
        except BaseException:
            try:
                ctx.destroy()
            except Exception:
                pass
            raise

    def _k2_build_tables(self, proj, tracks, frame_count, interval, end,
                         *, progress_callback=None):
        """生产版 build_task_tables（= 探针同源; 返回 (ctx, eligible, lt_lists)）。

        eligible: set[int] — 全部任务过 Fast-Path 判定的帧;
        lt_lists: {idx: [clip...]} — 帧排序后 clip 列表（_lt_active_clips 快照）。
        失败抛异常 → 调用方整段回退原版。
        """
        from .prepared_timeline import prepared_tables, table_render_cadence
        from . import native_source_motion_clock as clock
        from .retained_frame_scalars import table_scalars
        from src.core.native_mask_math import mask_and, mask_count, filtered_active
        prepared = prepared_tables(self, (engine, proj, frame_count), _tctx)
        from .prepared_prepass import eligible, finish, _METHODS
        from .prepared_prepass_guard import default_methods
        from src.engine.timeline_owned_reporter import default_owned_progress as default_progress
        if (prepared is not None and eligible(self, tracks)
                and default_methods(self, _METHODS) and default_progress(progress_callback)):
            return finish(self, tracks, frame_count, interval=interval, end=end,
                          prepared=prepared, checkpoint=_k2_prepare_checkpoint,
                          callback=progress_callback)
        ctx = prepared[0] if prepared is not None else _tctx.TimelineCtx(
            engine=engine, max_uni=_tctx.k2_max_uni(proj))
        if not ctx.available:
            raise RuntimeError("TimelineCtx 不可用")
        # ★ 2026-08-11: 每段构建前清空 ce_indexed 实例级 mask/scope 缓存
        #   （跨段内容可能陈旧 — 编辑期 base_order 替换; 段内项目静止则安全）
        try:
            self._ce_indexed_mask_cache = {}
        except Exception:
            pass
        try:
            self._ce_scope_cache = {}
        except Exception:
            pass
        _struct = self._render_struct_for_tracks(tracks)
        ordered_tracks = _struct["ordered_tracks"]
        track_by_id = _struct["track_by_id"]
        track_parent_map = _struct["track_parent_map"]
        any_solo = _struct["any_solo"]
        folder_chain_cache = _struct["folder_chain_cache"]

        def folder_chain(track):
            tid = str(getattr(track, "id", "") or "")
            cached = folder_chain_cache.get(tid)
            if cached is not None:
                return cached
            chain_rev = []
            seen = set()
            if bool(getattr(track, "is_folder", False)) and tid:
                chain_rev.append(tid)
            pid = str(getattr(track, "parent_id", "") or "")
            while pid and pid not in seen:
                seen.add(pid)
                chain_rev.append(pid)
                pid = track_parent_map.get(pid, "") or ""
            chain = tuple(reversed(chain_rev))
            if tid:
                folder_chain_cache[tid] = chain
            return chain

        per_frame = [[] for _ in range(frame_count)]
        rec_frame = [[] for _ in range(frame_count)]
        per_frame_fades = [[] for _ in range(frame_count)]
        rec_frame_fades = [[] for _ in range(frame_count)]
        per_frame_dirs = [[] for _ in range(frame_count)]
        rec_frame_dirs = [[] for _ in range(frame_count)]
        eligible_frames = set()
        lt_lists = {}
        # ★ K2-2026-08-11: LT 任务收集（帧 → [(clip, u, start_t)]）与
        #   帧活跃 clip 快照（_lt_find_pump_frames 依赖 _lt_active_clips）。
        lt_frame_tasks = {}
        lt_frame_all = {}
        lt_all_clips = {}
        # K7: 方向 fade 平行表（dir 行与任务行一一对应; DirRowBuilder 自
        # timeline_ctx 移植, 行缓存按 (clip,u) 跨帧复用）
        dir_builder = _tctx.DirRowBuilder(self, proj)
        dir_builder.attach(ctx)
        # K7a: fade 目标按域单次取（_get_fade_out_target 每次 copy, 复用同一
        # 对象防内存爆炸; 锚由引擎缓存返回同一对象, 按 id() 去重持有）
        _fade_targets = {}
        # ★ K2-2026-08-11: 构建期本地缓存 — 同一 (clip, u, track) 跨帧重复
        #   计算（_fade_tuple 全字段与 _compiled_clip_universe 均不依赖渲染
        #   时刻 pos; 锚/目标为引擎缓存返回的只读对象, C 表序列化只读值）。
        #   示例工程 7149 帧 278503 任务行 → 实际唯一组合仅数百:
        #   _get_fade_in_anchor 177678 次(3.1s) + _compiled_clip_universe
        #   559178 次(3.5s) → 缓存后各 ~数百次。build 期间项目/轨道引用
        #   恒定, id() 安全; 引擎级缓存语义不变, 零逻辑差异。
        _build_compiled_cache = {}
        _build_fade_cache = {}
        # R56e: 动态语义 clip 排除缓存 + 精化 active 表池码缓存。
        # ★ 精化默认关（QASCADE_CE_REFINED_ACTIVE=1 启用）：精化行已把
        #   mini 对齐到 720/721、全量到 8/7149 残差（epoch 语义），但未达
        #   逐字节铁律 → 生产保持与历史 exe 逐字节一致; CE 侧补全脏 memo
        #   的 epoch 内重算触发语义并通过 A/B 后再 flip。
        # R56g 定案：精化默认关——同源 epoch 检测已把残差压到极小
        # （mini 720/721、全量 8/7149，数据 100% 一致仅静默通道掩码位差），
        # 但 uni 级 memo 的复合复用语义未 100% 建模, 逐字节铁律下不 flip。
        # CE 侧跑 _bench_mini_repro + _bench_xy_elig_ab 全 PASS 后改默认 1。
        _refined_on = (os.environ.get("QASCADE_CE_REFINED_ACTIVE", "0")
                       .strip().lower() in ("1", "true", "on", "yes"))
        _build_dyn_cache = {}
        _build_refined_cache = {} if _refined_on else None
        _build_ft_cache = {}
        _epoch_last_k = {}
        _epoch_row_of = {}
        frame_meta = {}
        _frame_meta_acc = []
        frame_hits = {}
        _frame_hits_acc = []
        frame_sigs = {}
        _frame_sigs_acc = []
        _hold_any_cache = {}
        ctx._slot_rule = {}
        _hold_mode_cache = {}
        _build_fdi_cache = {}
        _build_dirin_cache = {}
        _dr_xy_sigs = {}
        # clip_eligible_fast_path 判定条件全静态（compiled/run_mode/seq/motor
        # profile/引擎级 LT/XY 表 — 渲染段内不变）→ 按 (id(clip), u) 缓存;
        # False 结果同样缓存（get 返回 False ≠ None → 命中）。
        _build_eligible_cache = {}

        def _fade_tuple(clip, u, track, scalars):
            fade_dur = scalars["duration"]
            fade_in = scalars["fade_in"]
            fade_out = scalars["fade_out"]
            te = 1 if self._clip_transition_enabled(clip) else 0
            cfi = _K2_CURVE_IDX[self._clip_transition_curve(clip, fade_in=True)]
            cfo = _K2_CURVE_IDX[self._clip_transition_curve(clip, fade_in=False)]
            sfi = _K2_STRAT_IDX[self._clip_transition_strategy(clip, fade_in=True)]
            sfo = _K2_STRAT_IDX[self._clip_transition_strategy(clip, fade_in=False)]
            anchor = self._get_fade_in_anchor(clip, u, track=track, proj=proj)
            target = _fade_targets.get(u)
            if target is None:
                target = self._get_fade_out_target(u, proj=proj)
                _fade_targets[u] = target
            return (fade_dur, fade_in, fade_out, te, cfi, cfo, sfi, sfo,
                    anchor, target)

        for idx in range(frame_count):
            _k2_prepare_checkpoint(ctx, idx, progress_callback)
            _frame_meta_acc = []
            _frame_hits_acc = []
            _frame_sigs_acc = []
            pos = clock.range_position(idx, interval, end)
            # 逐帧 timer_interval_s（:3518-3534 同式; _last_now 本地模拟,
            # 起始 None = 原版 :782 帧缓存构建期重置 — 离线恒 0.025）
            _timer_interval_s = table_render_cadence(self, pos)
            active_items = self._indexed_active_clip_items(
                pos, tracks, ordered_tracks, track_by_id, any_solo,
                _timer_interval_s)
            if prepared is not None:
                continue
            if not active_items:
                continue
            # priority: 与 :3593-3601 同式（排序键复刻）
            indexed = [(i, _K2SortKey(item, folder_chain))
                       for i, item in enumerate(active_items)]
            indexed.sort(key=lambda t: t[1])
            sorted_items = [active_items[i] for i, _k in indexed]
            total_priority = len(sorted_items)
            rows = []
            rec_rows = []
            fades = []
            rec_fades = []
            dirs = []
            rec_dirs = []
            frame_eligible = True
            for sidx, item in enumerate(sorted_items):
                start_t, track_order, track, clip, _local_t, _source_t, \
                    hold_ext, _gain, target_universes = item
                # ★ K2-2026-08-11: hold 扩展帧不再整帧回退 — 照常注册任务,
                #   fade tuple 全零编码（C 内核 _ce_clip_fade_gain64 dur<=0 →
                #   gain 恒 1.0 + source_t 天然钳 src_dur-1e-4 = Python hold 帧
                #   :1862-1868 同式）+ dir=None（hold 帧无方向增益, Python
                #   :3655 not hold_extension 守卫同式）。
                is_hold = bool(hold_ext)
                clip_id = str(getattr(clip, "id", "") or id(clip))
                priority = total_priority - sidx
                _fade_out_dir = self._get_clip_fade_out_direction(clip)
                _fid_ = _build_dirin_cache.get(id(clip))
                if _fid_ is None:
                    _fid_ = self._get_clip_fade_in_direction(clip)
                    _build_dirin_cache[id(clip)] = _fid_
                _scalars = table_scalars(clip,
                    (priority, track_order, start_t, _local_t, is_hold, total_priority),
                    _fid_, _fade_out_dir)
                tie, duration = _scalars["tie"], _scalars["duration"]
                rate = self._clip_playback_rate(clip)
                src_dur = self._clip_source_duration(clip, duration)
                for u in (target_universes or []):
                    u = int(u)
                    _elig = _build_eligible_cache.get((id(clip), int(u)))
                    if _elig is None:
                        from src.core.recorded_channel_masks import ce_source_mask_eligible
                        _elig = ce_source_mask_eligible(_tctx.clip_eligible_fast_path, clip, u, self)
                        from src.engine.transition_anchor_causality import legacy_anchor_allowed
                        _elig = _elig and legacy_anchor_allowed(self, clip, u, track=track, proj=proj)
                        _build_eligible_cache[(id(clip), int(u))] = _elig
                    if not _elig:
                        frame_eligible = False
                        continue
                    # R56e: 动态语义 clip 排除——顺序组时序 / 电机相位窗口 /
                    # clip 级灯跟踪的 raw 变换与掩码公式不在精化表语义内
                    # （LT raw 走 warp, seq 走 mask|raw_mask, 电机相位改写
                    # source 帧）→ 含此类 clip 的帧整帧回退 Python 原路径。
                    _dyn = _build_dyn_cache.get(id(clip))
                    if _dyn is None:
                        try:
                            _seq_on = bool(self._clip_sequential_group_timing(clip)[0])
                        except Exception:
                            _seq_on = False
                        try:
                            _ph_on = (float(self._clip_motor_phase_amount(clip) or 0.0) > 1e-6
                                      and self._get_clip_fade_in_direction(clip) != "none")
                        except Exception:
                            _ph_on = False
                        _dyn = (_seq_on or _ph_on
                                or bool(getattr(clip, "light_tracking_enabled", False)))
                        _build_dyn_cache[id(clip)] = _dyn
                    if _dyn:
                        frame_eligible = False
                        continue
                    _ckey = (id(clip), int(u), id(track))
                    compiled = _build_compiled_cache.get(_ckey)
                    if compiled is None:
                        _fk = (id(clip), u, id(track))
                        _fast = self._clip_compiled_fast.get(_fk)
                        if _fast is not None and _fast[0] is clip and _fast[1] is track:
                            compiled = _fast[2]
                        else:
                            # R56F: 与渲染循环完全同参（playback_engine:4083
                            # 传 fixture_defs_by_id=global 索引; 缺它 compiled
                            # 的 frame_times 可不同 → fi 差 1 → epoch 错位）。
                            _fdi = _build_fdi_cache.get("idx")
                            if _fdi is None:
                                _fdi = self._fixture_defs_index(proj)
                                _build_fdi_cache["idx"] = _fdi
                            compiled = self._compiled_clip_universe(
                                clip, u, track=track, proj=proj,
                                fixture_defs_by_id=_fdi)
                        _build_compiled_cache[_ckey] = compiled
                    slot = ctx.register_compiled(clip_id, u, compiled)
                    if slot not in getattr(ctx, "_slot_rule", {}):
                        try:
                            ctx._slot_rule[slot] = compiled.get("rule_active")
                        except Exception:
                            ctx._slot_rule[slot] = None
                    # R56e: 精化 active 表（base&((cur>0)|邻帧变化), rule 交集
                    # 同式折叠; hold 表 = Python hold 语义静态掩码平铺）。每
                    # (clip,u) 一次, 向量化 O(n×512); 行第 7 元素 = 池码, hold
                    # 行传 -1（内核用 base, 同 Python hold_extension=True）。
                    rcode = (_build_refined_cache.get((id(clip), int(u)))
                             if _build_refined_cache is not None else -1)
                    if (rcode is None and _build_refined_cache is not None
                            and not is_hold):
                        # R56i：占位基码，行由逐帧 memo 模型追加（见下）。
                        try:
                            if (compiled.get("frame_data") is not None
                                    and compiled.get("base_active") is not None):
                                rcode = ctx.begin_refined_table()
                            else:
                                rcode = -1
                        except Exception:
                            rcode = -1
                        _build_refined_cache[(id(clip), int(u))] = rcode
                    # R56i：精确复刻渲染循环 item memo 语义（playback_engine
                    # :4195-4240 + 慢路径 :4387）。核心规则：
                    #   a) sig 全字段相等 且 已有存储 → 复用存储行（mask 不调）；
                    #   b) 重算后 any=False → 【不更新存储】（store 在
                    #      `if not np.any(active): continue` 之后——实测 mini
                    #      2ce9e4ef 每帧重算的根因）；
                    #   c) 非 fast_ok（任一过渡/延迟窗口活跃）→ 慢路径每帧
                    #      重算且不存储；
                    #   d) 掩码同参：current_raw = fd[frame_index]（raw 链同源）。
                    _rcode_out = -1
                    _fi = 0
                    # R57: 窗口布尔含 (not hold_extension) 因子（PY :4126 同式，
                    # hold 帧windows全 False）
                    _w_fin = _scalars["window_in"]
                    _w_fout = _scalars["window_out"]
                    _w_dly = _scalars["window_delay"]
                    # LT clip 已被 _dyn 排除 → 此处恒 False
                    _w_lt = False
                    _fast_ok = _scalars["fast_ok"]
                    _sig_f = (_fi, _fi, bool(is_hold), float(_gain),
                              _w_fin, _w_fout, False, _w_dly, False, _w_lt,
                              (), int(priority))
                    _rk = (id(clip), int(u))
                    _hit = False
                    if not is_hold and rcode >= 0:
                        _fi = self._compiled_clip_frame_index(
                            compiled, float(_source_t))
                        if _fi < 0:
                            _fi = 0
                        _sig_f = (_fi, _fi, False, float(_gain),
                                  _w_fin, _w_fout, False, _w_dly, False, _w_lt,
                                  (), int(priority))
                        _prev_sig = _epoch_last_k.get(_rk)
                        _reuse = (_fast_ok and _prev_sig is not None
                                  and _prev_sig == _sig_f)
                        if _reuse:
                            _rcode_out = rcode + _epoch_row_of[_rk]
                        else:
                            _fdA = compiled.get("frame_data")
                            _cur_raw = None
                            if _fdA is not None:
                                _fdA = np.asarray(_fdA)
                                if _fdA.ndim == 1:
                                    _fdA = _fdA.reshape(-1, 512)
                                if _fi < _fdA.shape[0]:
                                    _cur_raw = _fdA[_fi].copy()
                            _row = self._clip_current_channel_active_mask(
                                clip, int(u), float(_source_t),
                                np.asarray(compiled.get("base_active"),
                                           dtype=bool).reshape(-1),
                                current_raw=_cur_raw,
                                hold_extension=False)
                            _rule = compiled.get("rule_active")
                            if _rule is not None:
                                _rule = np.asarray(_rule, dtype=bool).reshape(-1)
                                _row = filtered_active(_row, _rule, False)
                            ctx.append_refined_row(rcode, np.asarray(_row, dtype=bool))
                            _ridx = ctx.refined_row_count(rcode) - 1
                            if _fast_ok and mask_count(_row):
                                # 仅 any=True 才更新存储（规则 b）
                                _epoch_last_k[_rk] = _sig_f
                                _epoch_row_of[_rk] = _ridx
                            _rcode_out = rcode + _ridx
                    elif is_hold:
                        # R57: hold 行同样走 PY memo（fast_ok 恒真；active=
                        # base&rule 恒定）——存储 sig 供 uni-skip hit 判定
                        _prev_sig = _epoch_last_k.get(_rk)
                        _hit = (_fast_ok and _prev_sig is not None
                                and _prev_sig == _sig_f)
                        if _fast_ok and not _hit:
                            _anyk = _hold_any_cache.get(_rk)
                            if _anyk is None:
                                _ba = compiled.get("base_active")
                                _ru = compiled.get("rule_active")
                                _effb = None
                                if _ba is not None:
                                    _effb = np.asarray(_ba, dtype=bool).reshape(-1)
                                    if _ru is not None:
                                        _effb = mask_and(_effb, np.asarray(
                                            _ru, dtype=bool).reshape(-1))
                                _anyk = bool(mask_count(_effb)) if _effb is not None else False
                                _hold_any_cache[_rk] = _anyk
                            if _anyk:
                                _epoch_last_k[_rk] = _sig_f
                    # R56g：第 8 元素 = sig_item（与渲染循环 :4200 逐字段同式，
                    # 供宇宙级 memo 的 sig 集合比对——uni memo skip 条件 =
                    # 全部 item 命中且 sig 集合 frozenset 不变）。
                    _xy_sig_u = _dr_xy_sigs.get(int(u))
                    _sig_item = (
                        _fi, _fi, bool(is_hold), float(_gain),
                        _w_fin, _w_fout, False, _w_dly, False, False,
                        _xy_sig_u if _xy_sig_u is not None else (),
                        int(priority),
                    )
                    row = (slot, u, float(start_t), rate, float(src_dur), tie,
                           _rcode_out, _sig_item)
                    rows.append(row)
                    _hm_key = _hold_mode_cache.get(id(clip))
                    if _hm_key is None:
                        _hm_key = (self._clip_hold_mode(clip) != "none")
                        _hold_mode_cache[id(clip)] = _hm_key
                    _real_hold = _scalars["real_hold"]
                    _frame_meta_acc.append(
                        (str(getattr(clip, "id", "") or id(clip)),
                         folder_chain(track),
                         bool(_w_fin or _w_fout),
                         _real_hold, bool(is_hold), _hm_key))
                    _frame_hits_acc.append(bool(_hit))
                    _frame_sigs_acc.append(_sig_f)
                    if is_hold:
                        fades.append((0.0, 0.0, 0.0, 0, 0, 0, 0, 0,
                                      None, None))
                        dirs.append(None)
                    else:
                        frow = _build_fade_cache.get((id(clip), int(u), id(track)))
                        if frow is None:
                            frow = _fade_tuple(clip, u, track, _scalars)
                            _build_fade_cache[(id(clip), int(u), id(track))] = frow
                        fades.append(frow)
                        # K7: dir 行与任务行一一对应（无方向 fade → None → C
                        # 旧行为; 非 None → C 方向内核, 与 Python 方向增益
                        # 逐字节等价）
                        drow = dir_builder(clip, u, track, compiled,
                                           target_universes)
                        dirs.append(drow)
                    # ★ K2-2026-08-11: clip 级灯跟踪任务收集 → 每帧 LT 束
                    #   （build_lt_bundle 装配; ce_timeline_frame 串行 LT 阶段
                    #   fetch→LT→fade 时序同 Python :3821）。引擎级
                    #   light_tracking_enabled 仍由 clip_eligible_fast_path 拒绝。
                    if bool(getattr(clip, "light_tracking_enabled", False)):
                        lt_frame_tasks.setdefault(idx, []).append(
                            (clip, u, float(start_t)))
                    # ★ K2-2026-08-11 LT 束全表装配铁律: C 端 lt_scope_off[ti] 按
                    #   全表任务行索引（ti = 帧内局部行 = 全表 [n_tasks]）;
                    #   build_lt_bundle 对 scope None/empty 行 → scope_off=-1
                    #   （lt_path.py scope_of）。只传 LT 子集 → 束行数 < 全表
                    #   → ti 越界读 → C 崩溃 → 整段回退 Python（示例工程 生产实测
                    #   38.7s ≈ 参考 39s）。全表收集 (clip,u,start_t) 与探针
                    #   frame_all_tasks 同源。
                    lt_frame_all.setdefault(idx, []).append(
                        (clip, u, float(start_t)))
                    if bool(getattr(clip, "record_output_source_enabled", False)):
                        rec_rows.append(row)
                        if is_hold:
                            rec_fades.append((0.0, 0.0, 0.0, 0, 0, 0, 0, 0,
                                              None, None))
                            rec_dirs.append(None)
                        else:
                            rec_fades.append(frow)
                            rec_dirs.append(dirs[-1] if dirs else None)
            per_frame[idx] = rows
            # R56g: 帧任务元数据（接管消费标记用; 与 rows 同序）
            frame_meta[idx] = _frame_meta_acc
            frame_hits[idx] = _frame_hits_acc
            frame_sigs[idx] = _frame_sigs_acc
            rec_frame[idx] = rec_rows
            per_frame_fades[idx] = fades
            rec_frame_fades[idx] = rec_fades
            per_frame_dirs[idx] = dirs
            rec_frame_dirs[idx] = rec_dirs
            lt_all_clips[idx] = [item[3] for item in sorted_items]
            if frame_eligible and rows:
                eligible_frames.add(idx)
                lt_lists[idx] = lt_all_clips[idx]
        if prepared is not None:
            return prepared
        ctx.build_tables(frame_count, per_frame, rec_frame,
                         per_frame_fades, rec_frame_fades,
                         per_frame_dirs=per_frame_dirs,
                         record_frame_dirs=rec_frame_dirs)
        # R56F：压实同源 epoch 精化表（可增长 → 连续表 + 行指针）
        ctx.finalize_refined_tables()
        lt_ctx = _k2_build_lt_context(self, proj, lt_frame_tasks)
        return ctx, eligible_frames, lt_lists, lt_ctx, lt_frame_tasks, \
            lt_all_clips, lt_frame_all, frame_meta, frame_hits, frame_sigs

    def ce_render_timeline_frame_range(self, proj, start_idx, stop_idx, interval,
                                       end, frame_count,
                                       progress_callback=None,
                                       progress_start=0.0,
                                       progress_end=1.0):
        from . import native_source_motion_clock as clock
        # 仅整段串行 [0, frame_count) 走 C; 并行 worker 分段/越界段 → 原版
        if int(start_idx) != 0 or int(stop_idx) != int(frame_count):
            return orig(self, proj, start_idx, stop_idx, interval, end,
                        frame_count, progress_callback=progress_callback,
                        progress_start=progress_start,
                        progress_end=progress_end)
        # ── 状态保存（与原版 :749-774 逐项一致）──
        real_nm = self._nm
        old_locked = bool(getattr(self, "_timeline_render_cache_locked", False))
        old_building = bool(getattr(self, "_timeline_render_cache_building_lock",
                                    False))
        old_position = float(getattr(self, "_position", 0.0) or 0.0)
        old_suppress_position_emit = bool(
            getattr(self, "_suppress_position_emit", False))
        old_last_render_time = getattr(self, "_last_render_time", None)
        old_pending = bool(getattr(self, "_ltp_takeover_pending", False))
        old_pending_layer = getattr(self, "_ltp_takeover_layer", "")
        old_last_record = getattr(self, "_last_record_source_outputs_by_universe",
                                  {})
        old_layer_map = {
            str(layer): set(values or set())
            for layer, values in (getattr(self, "_last_output_universes_by_layer",
                                          {}) or {}).items()
        }
        old_last_universes = set(getattr(self, "_last_output_universes",
                                         set()) or set())
        from src.engine.timeline_incremental_checkpoint import capture_range_state
        initial_range_state = capture_range_state(self)
        from .incremental_range import TimelineFrameStore
        frame_cache = TimelineFrameStore()
        record_source_cache = TimelineFrameStore()
        self._timeline_render_cache_locked = False
        self._timeline_render_cache_building_lock = True
        self._nm = _K2FrameCacheNetworkProxy(real_nm, build_mode=True)
        self._suppress_position_emit = True
        self._last_render_time = None
        self._ltp_takeover_pending = False
        last_progress_emit = 0.0
        fell_back = False
        ctx = None
        incremental = None

        def prepare_progress():
            self._report_timeline_render_progress(
                progress_callback, "prepare", 0, frame_count,
                "正在准备时间线帧任务表...", progress_start)

        try:
            tracks = list(getattr(proj, "tracks", []) or [])
            from .incremental_range import begin_range, seed_consumed
            incremental, frame_cache, record_source_cache = begin_range(self, frame_count, interval, end, frames=frame_cache, records=record_source_cache, reporter=prepare_progress)
            # 静态任务表每调用重建（与原版恒重建语义一致; 不跨调用缓存 —
            # 帧数/interval/end 相同但内容变化（布局签名变, 如 fade 编辑）
            # 时缓存必陈旧, 探针级对拍无法覆盖此类错配）
            ctx, eligible, lt_lists, lt_ctx, lt_frame_tasks, lt_all_clips, \
                lt_frame_all, frame_meta, frame_hits, frame_sigs = \
                _k2_build_tables(self, proj, tracks, frame_count, interval, end,
                                 progress_callback=prepare_progress)
            if not hasattr(ctx, "_slot_rule"):
                ctx._slot_rule = {}
            rec_gate = (ctx.rec_off is not None
                        and getattr(ctx, "_rec_slot", None) is not None
                        and int(getattr(ctx._rec_slot, "size", 0) or 0) > 0)
            # LT 束上下文（scopes/state 跨帧; 无 LT 任务 → None）
            _ltp = None
            lt_bundle_ctx = None
            if lt_ctx is not None:
                try:
                    from qascade_ce import lt_path as _ltp
                except Exception:
                    try:
                        from render_lab.qascade_ce import lt_path as _ltp
                    except Exception:
                        _ltp = None
                if _ltp is not None:
                    lt_bundle_ctx = lt_ctx
            _uni_prev_sigs = {}   # u → 上一帧该域全部 item sig 的 frozenset
            _uni_prev_out = {}    # u → 上一帧该域输出 (data, active)
            _dr_xy_sigs_frame = {}
            # R56g: 接管消费（Python `_takeover_consumed_masks` 的内核镜像
            # + 逐帧标记循环）。初始状态从引擎同步（部分构建续跑语义一致）。
            _cons_slots = seed_consumed(self, ctx)
            _dump_n = max((len(v) for v in frame_meta.values()), default=1)
            _dump_out = np.zeros((_dump_n, 512), dtype=np.uint8)
            _dump_act = np.zeros((_dump_n, 512), dtype=np.uint8)
            _dump_sc = np.zeros((_dump_n, 512), dtype=np.float64)
            _is_restorable = self._is_restorable_takeover
            start_idx, _cons_slots, _uni_prev_sigs, _uni_prev_out = incremental.restore(ctx, _cons_slots, _uni_prev_sigs, _uni_prev_out)
            for idx in range(start_idx, stop_idx):
                pos = clock.range_position(idx, interval, end)
                layer_outputs = {}
                if idx in eligible:
                    _TIMELINE_RANGE_CNT[0] += 1
                    # C 帧状态模拟（见 docstring; 层映射不触碰）
                    self._position = pos
                    self._last_render_time = pos
                    # ★ K2-2026-08-11: LT 束每帧装配 — 任务序 = C 表序;
                    #   _lt_active_clips 先于束装配设置（_lt_find_pump_frames
                    #   依赖每帧活跃 clip 列表, :3617 同源）。
                    self._lt_active_clips = _lt_gap_reset(self, lt_all_clips.get(idx) or [])  # R173 断档清
                    lt = None
                    if lt_bundle_ctx is not None and idx in lt_frame_all:
                        scopes, st, params_by_key, fx_by_id, defs_by_id, \
                            all_fx, seen_clips = lt_bundle_ctx
                        # ★ 2026-08-11 全表装配（非 LT 行 scope 缺 → build_lt_bundle
                        #   scope_of 返回 None → scope_off=-1 → C 跳过）; 不再
                        #   用 lt_frame_tasks 子集 — 子集 → 束行数 < C 全表行数
                        #   → lt_scope_off[ti] 越界 → C 崩溃 → 整段回退 Python。
                        tasks = [{"clip_key": str(getattr(clip, "id", "")
                                                  or id(clip)),
                                  "universe": int(u),
                                  "start_t": float(start_t)}
                                 for clip, u, start_t in lt_frame_all[idx]]
                        if tasks:
                            lt = _ltp.build_lt_bundle(
                                tasks, scopes, st, float(pos),
                                lambda uni, exclude_clip=True:
                                    _k2_lt_nm_live(self, uni, exclude_clip),
                                lambda uni: _k2_lt_nm_live(self, uni, False),
                                lambda ck, u: _k2_lt_clip_frames(
                                    seen_clips, ck, self, u),
                                lambda suni, ck, pos_:
                                    _k2_lt_find_pump(seen_clips, self,
                                                     suni, ck))
                            st.back_write(self._light_tracking_state,
                                          self._light_tracking_time,
                                          self._light_tracking_color_cache)
                    _meta = frame_meta.get(idx) or []
                    _use_dump = bool(_meta)
                    if _use_dump:
                        _nn = len(_meta)
                        _fd_out = _dump_out[:_nn]
                        _fd_act = _dump_act[:_nn]
                        _fd_sc = _dump_sc[:_nn]
                        out = ctx.render_frame(idx, pos, mod_gain=1.0, lt=lt,
                                               dump=(_fd_out, _fd_act, _fd_sc))
                        _dump_out2, _dump_act2, _dump_sc2 = _fd_out, _fd_act, _fd_sc
                    else:
                        _dump_out2 = _dump_act2 = _dump_sc2 = None
                        out = ctx.render_frame(idx, pos, mod_gain=1.0, lt=lt)
                    if out is None:
                        _TIMELINE_RANGE_FALLBACK_CNT[0] += 1
                        _CE_EXC_CNT[0] += 1
                        raise _K2WholeSegmentFallback()
                    # ── R57: 精确 uni memo skip（= 渲染循环 `skip_unis` 同式）──
                    # PY 条件：该域【全部任务】的 item memo 命中（fast_ok 且
                    # stored sig == 本帧 sig，stored 仅 any=True 时更新）且
                    # sig 集合 frozenset == 上次计算帧的集合。命中时整域复用
                    # 上次输出（不 resolve/mix、不标记）。
                    _uni_skip = set()
                    _uni_sig_now = {}
                    _hits = ()
                    _sigs = ()
                    if _use_dump and _meta:
                        _t0u = int(ctx.task_off[idx])
                        _t1u = int(ctx.task_off[idx + 1])
                        _unis_u = ctx._uni[_t0u:_t1u]
                        _hits = frame_hits.get(idx) or []
                        _sigs = frame_sigs.get(idx) or []
                        if (len(_hits) == len(_meta) and len(_sigs) == len(_meta)
                                and _unis_u.shape[0] == len(_meta)):
                            _by_u3 = {}
                            for _i in range(len(_meta)):
                                _by_u3.setdefault(int(_unis_u[_i]), []).append(_i)
                            for _uu0, _idx0 in _by_u3.items():
                                _sigset = frozenset(_sigs[_i] for _i in _idx0)
                                _uni_sig_now[_uu0] = _sigset
                                _allhit = all(bool(_hits[_i]) for _i in _idx0)
                                _prev_sig = _uni_prev_sigs.get(_uu0)
                                if (_allhit and _prev_sig is not None
                                        and _prev_sig == _sigset
                                        and _uu0 in _uni_prev_out):
                                    _uni_skip.add(_uu0)
                                    _pout = _uni_prev_out[_uu0]
                                    out[int(_uu0)] = (
                                        np.asarray(_pout[0], dtype=np.uint8).copy(),
                                        np.asarray(_pout[1], dtype=bool).copy())
                    # ── R56g: 接管消费标记（= 渲染循环 :4585-4615 同式）──
                    # ★ 按宇宙分组（resolve 域 = 单宇宙; 跨宇宙任务不竞争）。
                    # eff/winner = _resolve_candidate_layers 同式（best 含 tie
                    # 与 PAR-1 提升、eff ≥ best-1e-12、winner 首达）。标记写入
                    # engine 字典并回推内核槽。
                    from src.engine.native_ownership import apply_legacy_consumption, mask_count
                    if _use_dump and len(_meta) >= 2 and not apply_legacy_consumption(
                            self, ctx, (_dump_sc2, _dump_act2, _dump_out2), _meta,
                            (idx, _cons_slots, _uni_skip)):
                        _t0f = int(ctx.task_off[idx])
                        _t1f = int(ctx.task_off[idx + 1]) if (idx + 1) < ctx.task_off.shape[0] else int(ctx.task_off[idx])
                        _nn = len(_meta)
                        if _t1f < _t0f:
                            _t1f = _t0f
                        _sc = _dump_sc2
                        _ac = _dump_act2.astype(bool)
                        _ou = _dump_out2
                        _ties = ctx._tie[_t0f:_t1f]
                        _unis = ctx._uni[_t0f:_t1f]
                        _slots = ctx._slot[_t0f:_t1f]
                        if _ties.shape[0] == _nn:
                            _v = _sc + _ties[:, None]
                            for _i in range(_nn):
                                if _meta[_i][2]:
                                    _v[_i] += (float(_nn) + 1.0) * 1e9
                            _masks = np.empty((_nn, 512), dtype=bool)
                            for _i in range(_nn):
                                _mi = _ac[_i]
                                _ru = ctx._slot_rule.get(int(_slots[_i]))
                                if _ru is not None:
                                    _mi = _mi & np.asarray(
                                        _ru, dtype=bool).reshape(-1)
                                _masks[_i] = _mi
                            # 按宇宙分组的任务下标 + 每宇宙 sig 集（uni memo skip 用）
                            _by_u = {}
                            for _i in range(_nn):
                                _by_u.setdefault(int(_unis[_i]), []).append(_i)
                            for _uu, _idxs in _by_u.items():
                                if _uu in _uni_skip:
                                    continue  # uni memo skip 帧不标记（PY 同）
                                if len(_idxs) < 2:
                                    continue  # 单任务宇宙无败者
                                _sub_v = _v[_idxs]
                                _sub_m = _masks[_idxs]
                                _sub_o = _ou[_idxs]
                                _cand = _sub_m & np.isfinite(_sub_v)
                                _best = np.max(np.where(_cand, _sub_v, -np.inf),
                                               axis=0)
                                _eff = _cand & (_sub_v >= _best - 1e-12)
                                _has_w = _eff.any(axis=0)
                                if not bool(_has_w.any()):
                                    continue
                                _winner = np.argmax(_eff, axis=0)
                                _w_out = np.take_along_axis(
                                    _sub_o, _winner[None, :], axis=0)[0]
                                # ── R14 hold 消费（registry 语义= 真 hold 项;
                                # best 看全场候选含 hold 自己; lost = cact & ~eff）
                                _hold_li = [j for j, i in enumerate(_idxs)
                                             if _meta[i][3]]
                                if _hold_li:
                                    _eff_all = _sub_m & np.isfinite(_sub_v) & (
                                        _sub_v >= _best - 1e-12)
                                    for _hli in _hold_li:
                                        _ck, _ch, _bd, _real_hold, _is_hold, _hm = (
                                            _meta[_idxs[_hli]])
                                        _lost = _sub_m[_hli] & ~_eff_all[_hli]
                                        if not bool(_lost.any()):
                                            continue
                                        _hk = _ck
                                        _cm_eng = getattr(
                                            self, "_hold_consumed_masks", None)
                                        _cm = (_cm_eng.get(_hk) if _cm_eng
                                               else None)
                                        _arr = None if _cm is None else _cm.get(_uu)
                                        _merged = (_lost.copy() if _arr is None
                                                   else (_lost | _arr))
                                        if _cm_eng is not None:
                                            _cm_eng.setdefault(_hk, {})[_uu] = (
                                                _merged.copy())
                                        _sl = ctx.slot_of(_ck, _uu)
                                        if _sl is not None and bool(_merged.any()):
                                            _prev = _cons_slots.get(_sl)
                                            _push = (_merged if _prev is None
                                                     else (_merged | _prev))
                                            _cons_slots[_sl] = _push
                                            ctx.set_consumed(_sl, _push)
                                for _li, _ti in enumerate(_idxs):
                                    _lost = _sub_m[_li] & _has_w & (_winner != _li)
                                    if not bool(_lost.any()):
                                        continue
                                    _chs = np.nonzero(_lost)[0]
                                    _ck, _ch, _bd, _real_hold, _is_hold, _hm = (
                                        _meta[_ti])
                                    _new = np.zeros(512, dtype=bool)
                                    for _j in range(_chs.shape[0]):
                                        _c = int(_chs[_j])
                                        _w = int(_winner[_c])
                                        if _w == _li:
                                            continue
                                        if _is_restorable(
                                                _ch, _meta[_idxs[_w]][1]):
                                            continue
                                        if _sub_o[_li][_c] != _w_out[_c]:
                                            _new[_c] = True
                                    if not bool(_new.any()):
                                        continue
                                    _sl = ctx.slot_of(_ck, _uu)
                                    if _sl is None:
                                        continue
                                    _prev = _cons_slots.get(_sl)
                                    if _prev is not None:
                                        _new |= _prev
                                    _cons_slots[_sl] = _new
                                    ctx.set_consumed(_sl, _new)
                                    _eng = getattr(
                                        self, "_takeover_consumed_masks", None)
                                    if _eng is not None:
                                        _e = _eng.setdefault(_ck, {})
                                        _e[_uu] = _new.copy()
                    # R56e: presence 语义对齐 Python——原版 `_render_timeline_
                    # frame_range` 对 all-inactive 宇宙不产出入层条目（not
                    # np.any(active) → skip）; 精化 active 后淡入起点/静默帧
                    # 的域会变全 inactive → 必须同步剔除, 否则多发空包。
                    for _u in list(out.keys()):
                        if not mask_count(out[_u][1]):
                            del out[_u]
                    # render_frame 返回的数组已是新分配副本（timeline_ctx
                    # render_frame :1138 .copy() + astype(bool)）→ 直接引用,
                    # 免双重 copy（值逐字节不变; 引擎共享 dict 来源仍须 copy）。
                    layer_outputs = out
                    for _uu0, _sigset0 in _uni_sig_now.items():
                        if _uu0 in out:
                            _uni_prev_sigs[_uu0] = _sigset0
                            _uni_prev_out[_uu0] = (
                                np.asarray(out[_uu0][0], dtype=np.uint8).copy(),
                                np.asarray(out[_uu0][1], dtype=bool).copy())
                        else:
                            _uni_prev_sigs.pop(_uu0, None)
                            _uni_prev_out.pop(_uu0, None)
                    if rec_gate:
                        rres = ctx.render_frame(idx, pos, mod_gain=1.0,
                                                record=True)
                        if rres is None:
                            _TIMELINE_RANGE_FALLBACK_CNT[0] += 1
                            _CE_EXC_CNT[0] += 1
                            raise _K2WholeSegmentFallback()
                        record_source_cache[int(idx)] = rres or {}
                else:
                    self._render_position(pos, layer_name="clip_playback")
                    layers = getattr(self._nm, "_layers", {}) or {}
                    playback_layer = (layers.get("clip_playback", {})
                                      if isinstance(layers, dict) else {})
                    for universe, entry in (playback_layer or {}).items():
                        try:
                            data, active = entry
                            active_arr = np.asarray(active, dtype=bool)
                            if active_arr.shape != (512,):
                                fixed_active = np.zeros(512, dtype=bool)
                                n = min(active_arr.size, 512)
                                if n > 0:
                                    fixed_active[:n] = active_arr.reshape(-1)[:n]
                                active_arr = fixed_active
                            from src.core.native_mask_math import mask_count
                            if not mask_count(active_arr):
                                continue
                            data_arr = np.asarray(data, dtype=np.uint8)
                            if data_arr.shape != (512,):
                                fixed_data = np.zeros(512, dtype=np.uint8)
                                n = min(data_arr.size, 512)
                                if n > 0:
                                    fixed_data[:n] = data_arr.reshape(-1)[:n]
                                data_arr = fixed_data
                            layer_outputs[int(universe)] = (
                                data_arr.copy(), active_arr.copy())
                        except Exception:
                            continue
                if not (idx in eligible and rec_gate):
                    record_source_cache[int(idx)] = {
                        int(u): (np.asarray(data, dtype=np.uint8).copy(),
                                 np.asarray(active, dtype=bool).copy())
                        for u, (data, active) in (getattr(
                            self, "_last_record_source_outputs_by_universe",
                            {}) or {}).items()
                    }
                frame_cache[int(idx)] = layer_outputs
                incremental.after_frame(ctx, idx, consumed=_cons_slots, signatures=_uni_prev_sigs, outputs=_uni_prev_out,
                                        native_lt_state=(lt_bundle_ctx[1] if idx in eligible and lt_bundle_ctx is not None else None))
                time.sleep(0.001)  # Same cooperative boundary as the Python range.
                now_emit = time.monotonic()
                done_in_range = idx - start_idx + 1
                range_total = max(1, stop_idx - start_idx)
                if (
                    progress_callback is not None
                    and (
                        idx == start_idx
                        or idx + 1 >= stop_idx
                        or now_emit - last_progress_emit >= 0.10
                    )
                ):
                    last_progress_emit = now_emit
                    frame_percent = float(done_in_range) / max(1.0, float(range_total))
                    overall = float(progress_start) + (
                        max(0.0, float(progress_end) - float(progress_start))
                        * frame_percent
                    )
                    self._report_timeline_render_progress(
                        progress_callback,
                        "render",
                        idx + 1,
                        frame_count,
                        f"正在渲染帧缓存 {idx + 1}/{frame_count}  位置 {pos:.2f}s/{end:.2f}s",
                        overall,
                    )
        except _K2WholeSegmentFallback:
            fell_back = True
        except _pe.TimelineBakeAbort:
            from src.engine.timeline_ready_range import seal_owned_prefix
            sealed = seal_owned_prefix(self, incremental, prepare_progress)
            if not sealed:
                from .timeline_store_lifecycle import clear_failed_range
                clear_failed_range(frame_cache, record_source_cache, incremental)
            raise
        except Exception as _e:
            import traceback as _tb
            log.warning("[CE-K2] 帧缓存渲染异常, 整段回退原版: %s", _e)
            _TIMELINE_RANGE_FALLBACK_CNT[0] += 1
            _CE_EXC_CNT[0] += 1
            fell_back = True
        finally:
            # ── 状态恢复（与原版 :843-864 逐项一致）──
            self._nm = real_nm
            self._timeline_render_cache_building_lock = old_building
            self._timeline_render_cache_locked = old_locked
            self._position = old_position
            self._suppress_position_emit = old_suppress_position_emit
            self._last_render_time = old_last_render_time
            self._ltp_takeover_pending = old_pending
            self._ltp_takeover_layer = old_pending_layer
            self._last_record_source_outputs_by_universe = old_last_record
            self._last_output_universes_by_layer = old_layer_map or {
                "clip_playback": set(),
                "clip_preview": set(),
                "timeline": set(),
            }
            self._last_output_universes = old_last_universes
            if ctx is not None:
                try:
                    ctx.destroy()
                except Exception:
                    pass
        from .incremental_range import finish_range
        finish_range(incremental, fell_back, engine=self, initial_state=initial_range_state)
        if fell_back:
            from .timeline_store_lifecycle import clear_failed_range
            clear_failed_range(frame_cache, record_source_cache, incremental)
            return orig(self, proj, 0, stop_idx, interval, end,
                        frame_count, progress_callback=progress_callback,
                        progress_start=progress_start,
                        progress_end=progress_end)
        return frame_cache, record_source_cache

    _pe.PlaybackEngine._render_timeline_frame_range = ce_render_timeline_frame_range
    # K7 生产接线探针访问点: probe_timeline_k2_bytecheck 的接线断言直接调用
    # （不经 ce_render_timeline_frame_range, 避免整段渲染）; 仅门控开启装
    # hook 时挂载, 生产调用链不触碰该属性。
    _pe.PlaybackEngine._k2_build_tables = _k2_build_tables
    log.info("[CE] K2 已接入: _render_timeline_frame_range → ce_timeline_frame 静态表")


# ═══ T12: 关联器 C 计算层（QASCADE_CE_LINKER=1; 2026-09-18 接入）═══════════

def _hook_linker(engine):
    """T12: TrackLinkerEngine._process_rule → ce_linker_rule（关联器整段 C 化）。

    接入点（用户指令 2026-09-18「新功能的计算层应该及时更新到 CE 里面并
    通过它完成计算调用, 保证全命中」）: `src/processing/track_linker_engine.py`
    的每规则热路径 `_process_rule`（:2419）。实测归因见
    `render_lab/bench/probe_linker_tick_profile.py`: 40 台/120 规则场景
    tick 9.07ms, 其中 `_process_rule` 占 **82.1%**（7.452ms）—— 内部
    cProfile tottime 首位为 886 次/tick 的 np.clip 标量调用（`_write_dst`
    L2629/L2637 各 22.6%, `_color_whiten_writes` L246 18.1%,
    `_light_track_writes` L264 18.1%）, 其次 dict 查找 132420 次/30 tick。

    替换语义（逐分支对齐 :2419-2798）:
      * mode 判定次序 = XY_MULTI(use_xy_vector) → PAIRED → WHITEN → LIGHT
        → BROADCAST; C 侧 XY_MULTI「一条都没写」时落回 BROADCAST, 与
        Python 的 `_write_multi_dst_xy_by_fixture()` 返回 False 后的走向一致。
      * 低值覆盖决策树（`_write_dst`）: 目标无基础值（含"泵不开启"）/
        电机全停放（写当前值, 保留 linker 占用）/ 取 min(cur,result)。
        16bit 展开（coarse+fine 拆分）与 fine 回退读值与 xy_reference 同式。
      * 撤销条件（任一 → 该规则委托原版, 字节不变）: 有 stagger/spray/
        spacing 时序; 预设无 KIND 映射或 `preset_def.fn` 非内置同一函数;
        参数不可 float 化; 地址/universe 出界或不可 int 往返; 同规则 dst
        地址重叠; 表越界; C 返回码 != 0。

    状态: `_ce_linker_prep_cache`（规则静态表: 类型码/地址/配对/参数数组）
    随既有 `_rule_prep_cache` 同链失效（`_invalidate_rule_caches` 包装清空,
    亦随 `rules_changed` —— 引擎既有信号链不变）; `_ce_linker_tick`
    （本 tick 的 pending 镜像: 与 `writes` dict 同身份判定 tick 边界,
    跨规则「先写泵再压泵」的规则链语义与 Python 完全一致）。
    失败/异常 → `_LINKER_MISS_CNT`+1 并委托原版; 门控 `QASCADE_CE_LINKER=1`,
    env=0 恒原版（逃生口）。
    """
    if os.environ.get("QASCADE_CE_LINKER", "0") != "1":
        log.info("[CE] 关联器门控关闭 (QASCADE_CE_LINKER=1 启用)")
        return False
    try:
        from src.processing import track_linker_engine as _tle
    except Exception as _e:
        log.info("[CE] 关联器跳过: track_linker_engine 导入失败 %s", _e)
        return False
    if engine is None or getattr(engine, "ce_linker_rule", None) is None:
        log.info("[CE] 关联器跳过: ce_linker_rule 符号缺失（保持 Python 路径）")
        return False
    try:
        from qascade_ce import linker_path as _lp
    except Exception:
        try:
            from render_lab.qascade_ce import linker_path as _lp
        except Exception as _e2:
            log.info("[CE] 关联器跳过: linker_path 导入失败 %s", _e2)
            return False

    _TE = _tle.TrackLinkerEngine
    _orig_process_rule = _TE._process_rule
    _orig_invalidate = _TE._invalidate_rule_caches
    _P8 = ctypes.POINTER(ctypes.c_uint8)
    _P8P = ctypes.POINTER(_P8)
    _I32 = ctypes.POINTER(ctypes.c_int32)
    _F64 = ctypes.POINTER(ctypes.c_double)
    _T = ctypes.c_int64

    # kind → 内置 fn 对象**集合**（身份守卫: 用户的同名预设若换了 fn 即回退原版）。
    # ★ R93 修: 原为 `setdefault` 单对象 → kind 16 的首个内置是 `_passthrough`,
    #   于是 6 个用 `_channel_linear` 的预设（dimmer_to_rgb_mix / r_to_gb_tint /
    #   b_to_rg_depth / dimmer_to_white / pump_to_valve_linear /
    #   valve_to_pump_linear）身份判定恒 False → 永远回退 Python（实测 700 台
    #   场景 6/13 规则恒 miss）。二者数值同为 `src*scale+offset`（C: base 槽）。
    #   改为集合: 仍拒绝任何非内置 fn（用户改写同名预设的安全网保留）。
    _FN_BY_KIND: dict = {}
    for _pd in _tle.BUILTIN_LINK_PRESETS:
        _k = _lp.KIND.get(_pd.id)
        if _k is not None:
            _FN_BY_KIND.setdefault(_k, set()).add(_pd.fn)

    def _raw_ptr(arr, want_u8=True):
        if arr is None:
            return None
        try:
            return arr.ctypes.data_as(_P8)
        except Exception:
            return None

    def _sync_pend(tick, unis, writes):
        """把 writes dict 的值同步进本 tick 的 pending 镜像（仅列出的域）。"""
        for u in unis:
            ui = int(u)
            arrs = tick[1].get(ui)
            if arrs is None:
                continue
            pend, has = arrs
            ch = writes.get(u)
            if not ch:
                continue
            for addr, val in ch.items():
                try:
                    ai = int(addr)
                    if 0 <= ai < 512:
                        pend[ai] = int(val) & 0xFF
                        has[ai] = 1
                except Exception:
                    continue

    def _tick_state(self, writes):
        st = getattr(self, "_ce_linker_tick", None)
        if st is None or st[0] is not writes:
            # ★ R93 修: 必须是**可变容器** —— `_ce_try` / `ce_process_rule`
            #   会对 st[2]（dirty 标志）赋值; 旧代码是 tuple → 每次 tick 抛
            #   `'tuple' object does not support item assignment`（被兜底
            #   except 吞掉）→ T12 恒回退、hit 恒 0。
            st = [writes, {}, True]     # [writes 身份, {uni: (pend,has)}, dirty]
            self._ce_linker_tick = st
        return st

    def _pend_arrays(tick, unis):
        for u in unis:
            ui = int(u)
            if ui not in tick[1]:
                tick[1][ui] = (np.zeros(512, np.uint8), np.zeros(512, np.uint8))
        return tick[1]

    def _ce_build_static(self, rule, preset_def, kind):
        """规则静态表（规则/配接不变 → 只建一次）。None = 不可 C 化。"""
        try:
            prep = self._rule_prep_cache.get(id(rule))
            if prep is None or prep[0] is not rule:
                prep = (rule,) + self._build_rule_prep(rule, preset_def)
                self._rule_prep_cache[id(rule)] = prep
            key_of, fine_of, src_channels, is_motor_pump = prep[1:]
        except Exception:
            return None
        n_src = len(src_channels)
        n_dst = len(rule.dst_channels)
        if n_src > _lp.MAXN or n_dst > _lp.MAXN:
            return None

        # ★ R93 修: 内核的 `src_desc/dst_desc` 第 0 列是**域在掩码表里的索引**
        #   （csrc/_ce_linker_kernel.c.inc 顶部契约: "u = 域在 snap/act/...
        #   表中的索引, 由 Python 侧建立"; 内核用它直接 `snap[u]`/`pend[u]`）。
        #   旧代码写的是 universe **编号** → 只要编号 ≥ 域数（常态: u=1/2/3,
        #   n_uni=1~3 且编号从不小于索引）内核一律 `u >= n_uni` → rc=-1,
        #   T12 恒回退（A/B 实测 100% 回退）。这里先建域表，再按表序写索引。
        unis = []
        _seen_u = set()
        for r in list(src_channels) + list(rule.dst_channels):
            u = r.universe
            if int(u) not in _seen_u:
                _seen_u.add(int(u))
                unis.append(u)
        if not unis:
            return None
        _u_index = {int(u): i for i, u in enumerate(unis)}

        def _desc(refs):
            out = np.zeros((len(refs), 4), np.int32)
            for i, r in enumerate(refs):
                u = getattr(r, "universe", None)
                try:
                    ui = int(u)
                except Exception:
                    return None
                if ui != u:                       # "1"/1.5/None 等 → dict 查找
                    return None                    # 语义不可复刻
                ti = _u_index.get(ui)
                if ti is None:                     # 不在本规则域表内 → 拒绝
                    return None
                a = getattr(r, "dmx_addr", None)
                try:
                    ai = int(a)
                except Exception:
                    return None
                if not (0 <= ai < 512):
                    return None
                t = _lp.type_code(getattr(r, "ch_type", ""))
                f = fine_of.get(id(r))
                fi = -1
                if f is not None:
                    try:
                        fi = int(f)
                    except Exception:
                        return None
                    if not (0 <= fi < 512):
                        return None
                out[i] = (ti, ai, t, fi)
            return out

        src_desc = _desc(src_channels)
        dst_desc = _desc(rule.dst_channels)
        if src_desc is None or dst_desc is None:
            return None
        # 类型码必须与 Python 的 canonical key 双射（偏色分支以 key 为 dict 键）
        _keys = [_tle._channel_type_key(getattr(r, "ch_type", ""))
                 for r in list(src_channels) + list(rule.dst_channels)]
        _codes = [int(x) for x in list(src_desc[:, 2]) + list(dst_desc[:, 2])]
        if len(set(_keys)) != len(set(_codes)):
            return None
        # 同规则 dst 地址与 fine 地址两两不重叠（重叠 → 后写二义, 让 Python 管）
        seen = set()
        for i in range(n_dst):
            u, a, t, f = (int(dst_desc[i, 0]), int(dst_desc[i, 1]),
                          int(dst_desc[i, 2]), int(dst_desc[i, 3]))
            addrs = [a]
            if t in (1, 2, 3) and f >= 0:      # MOTOR_FINE_CHANNELS（粗电机）
                addrs.append(f)
            for x in addrs:
                if (u, x) in seen:
                    return None
                seen.add((u, x))
        # 参数数组（缺 key 用 fn 硬默认补齐; 不可 float 化 → 回退）
        try:
            params = _lp.pack_params(rule.preset_id, dict(rule.params or {}))
        except Exception:
            return None
        # mode 判定（次序严格同 :2742-2798）
        src_keys = {key_of.get(id(r)) for r in src_channels}
        use_xy = bool(is_motor_pump) and {"motor_x", "motor_y"} <= src_keys
        if use_xy and n_dst > 1:
            mode = _lp.MODE_XY_MULTI
        elif n_src == n_dst and n_src > 1 and not use_xy:
            mode = _lp.MODE_PAIRED
        elif preset_def.id == "motor_color_whiten":
            mode = _lp.MODE_WHITEN
        elif str(preset_def.id).startswith("pump_light_track"):
            mode = _lp.MODE_LIGHT
        else:
            mode = _lp.MODE_BROADCAST
        # XY_MULTI 配对表（原版 _write_multi_dst_xy_by_fixture 的 pair 构造）
        pair = None
        if mode == _lp.MODE_XY_MULTI:
            by_fixture = {}
            ordered = []
            cur_pair = {}
            for idx, r in enumerate(src_channels):
                sk = key_of.get(id(r))
                if sk not in ("motor_x", "motor_y"):
                    continue
                fid = str(getattr(r, "fixture_id", "") or "")
                if fid:
                    by_fixture.setdefault(fid, {})[sk] = idx
                if sk == "motor_x" and cur_pair:
                    ordered.append(cur_pair)
                    cur_pair = {}
                cur_pair[sk] = idx
                if "motor_x" in cur_pair and "motor_y" in cur_pair:
                    ordered.append(dict(cur_pair))
                    cur_pair = {}
            if cur_pair:
                ordered.append(dict(cur_pair))
            pair = np.full(n_dst, -1, np.int32)
            for idx, dref in enumerate(rule.dst_channels):
                pr = by_fixture.get(str(getattr(dref, "fixture_id", "") or ""))
                if pr is None and idx < len(ordered):
                    pr = ordered[idx]
                if not pr:
                    continue
                xi = int(pr.get("motor_x", 0xFFFF))
                yi = int(pr.get("motor_y", 0xFFFF))
                if xi == -1:
                    xi = 0xFFFF
                if yi == -1:
                    yi = 0xFFFF
                if xi >= n_src:
                    xi = 0xFFFF
                if yi >= n_src:
                    yi = 0xFFFF
                pair[idx] = ((xi & 0xFFFF) << 16) | (yi & 0xFFFF)
        return (n_src, src_desc, n_dst, dst_desc, int(mode), params, pair,
                unis, bool(is_motor_pump))

    def _ce_static_for(self, rule, preset_def, kind):
        cache = self.__dict__.get("_ce_linker_prep_cache")
        if cache is None:
            cache = {}
            self._ce_linker_prep_cache = cache
        ent = cache.get(id(rule))
        if ent is not None and ent[0] is rule:
            return ent[1]
        st = _ce_build_static(self, rule, preset_def, kind)
        if st is not None:
            cache[id(rule)] = (rule, st)
        return st

    # 定义签名必须是 (self_, ...) —— 调用点是 `_ce_try(self, rule, ...)`
    # （R93 修: 曾多留一个位置参数 `self`, 全部调用都 TypeError → T12 恒回退,
    #  hit 恒 0; 这与 T12 文档里"全命中"的承诺直接矛盾）。
    def _ce_try(self_, rule, snapshot, writes, motion_snapshot,
                source_active, motion_active, parked_src):
        """C 路径；返回 True = 已处理（含正常零写入）, False = 调用方回退原版。"""
        if not rule.src_channels or not rule.dst_channels:
            return False
        preset_def = _tle.PRESET_BY_ID.get(rule.preset_id)
        if preset_def is None:
            return False
        kind = _lp.KIND.get(preset_def.id)
        if kind is None or preset_def.fn not in (_FN_BY_KIND.get(kind) or ()):
            return False
        # ★ R93 守卫**已摘除**（2026-09-19）：偏色预设曾因 C 分支把 `params[8]`
        #   （= CE_LP_INVERT）当 white 用而整批回退。根因有两层:
        #     ① 槽位写错（应为 CE_LP_WHITE=10）;
        #     ② **`ce_engine.c` 里内联抄了一份内核, 与 `_ce_linker_kernel.c.inc`
        #        双真源** —— 只改 .inc 编不进去, 于是「修完仍复现」。
        #   现 ② 已改为 `#include "_ce_linker_kernel.c.inc"`（同 CE_MTX/CE_EFFW）,
        #   两处根因一并消除 → 恢复 100% C 命中, 由
        #   `tests/test_ce_new_hooks_r93.py` 的 whiten 命中+字节一致双断言守门。
        # 时序分支（stagger/spray/spacing）留在 Python（跨 tick 激活状态）
        if (float(getattr(rule, "spray_delay_ms", 0.0) or 0.0) > 0.0
                or float(getattr(rule, "stagger_delay_ms", 0.0) or 0.0) > 0.0
                or str(getattr(rule, "spacing_profile", "uniform")
                       or "uniform") != "uniform"):
            return False
        st = _ce_static_for(self_, rule, preset_def, kind)
        if st is None:
            return False
        (n_src, src_desc, n_dst, dst_desc, mode, params, pair, unis,
         is_motor_pump) = st
        uni_keys = [int(u) for u in unis]

        tick = _tick_state(self_, writes)
        arrs = _pend_arrays(tick, unis)
        if tick[2]:
            for u in unis:
                a = arrs.get(int(u))
                if a is not None:
                    a[0].fill(0)
                    a[1].fill(0)
            _sync_pend(tick, unis, writes)
            tick[2] = False
        n_uni = len(unis)

        # 指针表（7 张: snap/motion/act/motact/parked/pend/pend_has）
        def _tab(src):
            row = (_P8 * n_uni)()
            for i, u in enumerate(uni_keys):
                arr = src.get(u) if src is not None else None
                if arr is None and src is not None:
                    try:
                        arr = src.get(unis[i])
                    except Exception:
                        arr = None
                p = _raw_ptr(arr)
                if p is not None:
                    row[i] = p
            return row

        tab_snap = _tab(snapshot)
        tab_motion = _tab(motion_snapshot)
        tab_act = _tab(source_active)
        tab_motact = _tab(motion_active)
        tab_parked = _tab(parked_src)
        tab_pend = (_P8 * n_uni)()
        tab_pend_has = (_P8 * n_uni)()
        for i, u in enumerate(uni_keys):
            a = arrs.get(u)
            if a is not None:
                tab_pend[i] = a[0].ctypes.data_as(_P8)
                tab_pend_has[i] = a[1].ctypes.data_as(_P8)

        out_cap = n_dst * 4 + 8
        out_u = np.zeros(out_cap, np.int32)
        out_a = np.zeros(out_cap, np.int32)
        out_v = np.zeros(out_cap, np.int32)
        out_n = np.zeros(1, np.int32)
        rc = engine.ce_linker_rule(
            int(kind), int(mode),
            params.ctypes.data_as(_F64),
            pair.ctypes.data_as(_I32) if pair is not None else None,
            int(n_src), src_desc.ctypes.data_as(_I32),
            int(n_dst), dst_desc.ctypes.data_as(_I32),
            int(n_uni),
            ctypes.cast(tab_snap, _P8P), ctypes.cast(tab_motion, _P8P),
            ctypes.cast(tab_act, _P8P), ctypes.cast(tab_motact, _P8P),
            ctypes.cast(tab_parked, _P8P),
            ctypes.cast(tab_pend, _P8P), ctypes.cast(tab_pend_has, _P8P),
            out_u.ctypes.data_as(_I32), out_a.ctypes.data_as(_I32),
            out_v.ctypes.data_as(_I32), out_n.ctypes.data_as(_I32),
            int(out_cap))
        if rc != 0 or int(out_n[0]) > out_cap:
            tick[2] = True          # pend 可能已被就地改写 → 下次重同步
            return False
        # 写回 writes（键 = 原 universe 对象 → 与 Python 写回的 dict 恒同）
        for j in range(int(out_n[0])):
            u = unis[int(out_u[j])]
            writes.setdefault(u, {})[int(out_a[j])] = int(out_v[j])
        _LINKER_HIT_CNT[0] += 1
        return True

    def ce_process_rule(self, rule, snapshot, writes,
                        motion_snapshot=None, source_active=None,
                        motion_active=None, parked_src=None):
        if getattr(self, "_ce_linker_failed", False):
            return _orig_process_rule(
                self, rule, snapshot, writes,
                motion_snapshot=motion_snapshot, source_active=source_active,
                motion_active=motion_active, parked_src=parked_src)
        try:
            if _ce_try(self, rule, snapshot, writes, motion_snapshot,
                       source_active, motion_active, parked_src):
                return None
        except Exception as _e:
            _CE_EXC_CNT[0] += 1
            log.debug("[CE-T12] 关联器 C 路径异常回退原版: %s", _e)
        _LINKER_MISS_CNT[0] += 1
        try:
            _tick_state(self, writes)[2] = True
        except Exception:
            pass
        return _orig_process_rule(
            self, rule, snapshot, writes,
            motion_snapshot=motion_snapshot, source_active=source_active,
            motion_active=motion_active, parked_src=parked_src)

    def ce_invalidate_rule_caches(self):
        """规则/配接变更 → C 侧静态表与 pending 镜像同链失效。"""
        try:
            self.__dict__.pop("_ce_linker_prep_cache", None)
            self.__dict__.pop("_ce_linker_tick", None)
        except Exception:
            pass
        return _orig_invalidate(self)

    try:
        engine.ce_linker_rule.argtypes = [
            ctypes.c_int, ctypes.c_int, _F64, _I32,
            ctypes.c_int, _I32, ctypes.c_int, _I32, ctypes.c_int,
            _P8P, _P8P, _P8P, _P8P, _P8P, _P8P, _P8P,
            _I32, _I32, _I32, _I32, ctypes.c_int]
        engine.ce_linker_rule.restype = ctypes.c_int
    except Exception:
        pass
    _TE._process_rule = ce_process_rule
    _TE._invalidate_rule_caches = ce_invalidate_rule_caches
    log.info("[CE] T12 已接入: TrackLinkerEngine._process_rule → ce_linker_rule"
             "（关联器整段 C 计算; env=0 一键回退）")
    return True


# ═══ T13: 矩阵像素引擎 C 计算（QASCADE_CE_MATRIX_PIXEL=1）═════════════════

def _hook_matrix_pixel(engine):
    """T13: 矩阵像素引擎的 v40 计算族 → ce_mtx_pattern / ce_mtx_particle。

    接入点（用户指令 2026-09-18「新功能的计算层应该及时更新到 CE 里面并
    通过它完成计算调用, 保证全命中」）: `matrix_pixel_engine_ext_v40` 的
    两个模块级计算体 —— `_EXT_COMPUTE_MAP` 里的 lambda 在**调用时**按模块
    全局名查找, 故替换模块属性即覆盖 v40 全部 964 个模式（pattern 784 +
    particle 180; `patch_matrix_pixel_engine` 生成的 `_compute_<mode_id>`
    类属性走同一条链）。

    实测归因（`render_lab/bench/probe_matrix_pixel_profile.py`,
    `_ui_validation/probe_v40_segments.py`; 1356 台设备）:
      · matrix_v40_particle_physics  27.79 ms/帧（tick 总耗时 27.79ms,
        逐设备×逐粒子 exp 核占 82.9%）—— 单帧吃掉 40ms 预算的 70%
      · matrix_v40_asym_motion        6.46 ms/帧（coords 49.3% / metric
        18.9% / style+dict 31.9%）
      · 对照: 内置 numpy 数组族（effect_ripple）0.061 ms/帧 —— **不 C 化**
        （收益 < 5%, 见模块 docstring）

    替换语义（逐分支对齐 v40 `_compute_pattern` :251 / `_compute_particle`
    :354; 96 族度量 `_metric_family` :113 与 16 种粒子轨道
    `_particle_sources` :289 逐条转录）:
      * 参数由 Python 侧 `_params()` 算好传入 ⇒ 输入逐位一致（音频包络 /
        节拍判定留 Python, 不重复实现）。
      * `math.hypot` 用 Dekker double-double 复刻（CPython vector_norm ≠
        ucrtbase hypot; 实证 20 万×8 域 0 失配）, 其余超越函数走 ucrtbase。
      * 整数哈希 `_hash01` 用 int64 回绕复刻 Python 任意精度（40 万样本 0 失配）。
      * 契约不满足（无网格/长度不符/数组非常规/内核拒绝）→ 该帧回退原版,
        输出逐字节不变。
    """
    if os.environ.get("QASCADE_CE_MATRIX_PIXEL", "0") != "1":
        log.info("[CE] 矩阵像素引擎门控关闭 (QASCADE_CE_MATRIX_PIXEL=1 启用)")
        return False
    if (engine is None or getattr(engine, "ce_mtx_pattern", None) is None
            or getattr(engine, "ce_mtx_particle", None) is None):
        log.info("[CE] 矩阵像素引擎跳过: ce_mtx_pattern/ce_mtx_particle "
                 "符号缺失（保持 Python 路径）")
        return False
    try:
        from src.ui.matrix_slideshow import matrix_pixel_engine_ext_v40 as _v40
    except Exception as _e:
        log.info("[CE] 矩阵像素引擎跳过: ext_v40 导入失败 %s", _e)
        return False
    try:
        from qascade_ce import matrix_pixel_path as _mp
    except Exception:
        try:
            from render_lab.qascade_ce import matrix_pixel_path as _mp
        except Exception as _e2:
            log.info("[CE] 矩阵像素引擎跳过: matrix_pixel_path 导入失败 %s", _e2)
            return False

    _orig_pattern = _v40._compute_pattern
    _orig_particle = _v40._compute_particle
    _failed = [False]
    _gt = [0, 0]        # [连续缺失/异常计数, 连续拒绝计数]（用于一次性告警）

    def _warn_once(kind, exc):
        if _gt[kind] == 1:
            log.warning("[CE-T13] 矩阵像素引擎 %s 回退原版（首次）: %s",
                        "异常" if kind == 0 else "契约拒绝", exc)

    def ce_compute_pattern(engine_, _mode_id, variant, family_offset=0):
        if not _failed[0]:
            try:
                params = _v40._params(engine_, variant)
                pack = _mp.pattern_arrays(engine_, variant, family_offset, params)
                if pack is None:
                    _gt[1] += 1
                    _warn_once(1, "无网格/长度不符/数组非常规")
                elif pack == {}:
                    _MTX_HIT_CNT[0] += 1        # 原版同样返回 {fid: 0.0}
                    return {}
                else:
                    (col, row, cols, rows, width, twist, mix, t, phase, freq,
                     soft, bass, mid, high, rms, var, off) = pack
                    ids = engine_._sorted_ids
                    out = np.empty(len(ids), np.float64)
                    rc = engine.ce_mtx_pattern(
                        len(ids),
                        col.ctypes.data_as(_MTX_I32P),
                        row.ctypes.data_as(_MTX_I32P),
                        cols, rows, width, twist, mix, t, phase, freq, soft,
                        bass, mid, high, rms, var, off,
                        out.ctypes.data_as(_MTX_F64P))
                    if rc == 0:
                        _MTX_HIT_CNT[0] += 1
                        return _mp.results_to_dict(ids, out)
                    _gt[1] += 1
                    _warn_once(1, "内核返回码 %d" % rc)
            except Exception as _e:
                _CE_EXC_CNT[0] += 1
                _gt[0] += 1
                _warn_once(0, _e)
                _failed[0] = True      # 异常 = 接线缺陷, 本会话不再重试
        _MTX_MISS_CNT[0] += 1
        return _orig_pattern(engine_, _mode_id, variant, family_offset)

    def ce_compute_particle(engine_, _mode_id, variant):
        if not _failed[0]:
            try:
                params = _v40._params(engine_, variant)
                pack = _mp.particle_arrays(engine_, variant, params)
                if pack is None:
                    _gt[1] += 1
                    _warn_once(1, "无网格/长度不符/数组非常规")
                elif pack == {}:
                    _MTX_HIT_CNT[0] += 1
                    return {}
                else:
                    (col, row, cols, rows, density, width, twist, mix, t, phase,
                     soft, bass, mid, high, rms, beat, var) = pack
                    ids = engine_._sorted_ids
                    out = np.empty(len(ids), np.float64)
                    rc = engine.ce_mtx_particle(
                        len(ids),
                        col.ctypes.data_as(_MTX_I32P),
                        row.ctypes.data_as(_MTX_I32P),
                        cols, rows, density, width, twist, mix, t, phase, soft,
                        bass, mid, high, rms, beat, var,
                        out.ctypes.data_as(_MTX_F64P))
                    if rc == 0:
                        _MTX_HIT_CNT[0] += 1
                        return _mp.results_to_dict(ids, out)
                    _gt[1] += 1
                    _warn_once(1, "内核返回码 %d" % rc)
            except Exception as _e:
                _CE_EXC_CNT[0] += 1
                _gt[0] += 1
                _warn_once(0, _e)
                _failed[0] = True
        _MTX_MISS_CNT[0] += 1
        return _orig_particle(engine_, _mode_id, variant)

    _v40._compute_pattern = ce_compute_pattern
    _v40._compute_particle = ce_compute_particle
    log.info("[CE] T13 已接入: matrix_pixel_engine_ext_v40._compute_pattern"
             "/_compute_particle → ce_mtx_pattern/ce_mtx_particle（矩阵像素"
             "引擎整段 C 计算; env=0 一键回退）")
    return True


# ═══ T14: effect `_tick_play` 写回层 C 计算（QASCADE_CE_EFFECT_WRITE=1）══════

def _hook_effect_write(engine):
    """T14: effect_browser 脚手架写回层 → ce_eff_write_direct / ce_eff_write_rgbw。

    接入点（用户指令 2026-09-18「新功能的计算层应该及时更新到 CE 里面并
    通过它完成计算调用, 保证全命中」）: `src/ui/effect_browser.py` 的
    `EffectBrowserWidget._write_direct_channel_vectorized` (:7139) /
    `._write_rgbw_channel_vectorized` (:7160)。效果预设的 compute 已在 CE
    （T4 全族）, 但 `_tick_play` 脚手架写回层仍是纯 Python。

    实测归因（`render_lab/bench/probe_effect_tick_profile.py`, CE 开,
    1356 台 × 5 组预设/通道）:
      · `_write_channel_vectorized` 3.886 ms/帧 = 整帧 **31.3%** ← 本 hook
        ├ direct 0.641 ms/帧（5.2%）
        └ rgbw   3.023 ms/帧（24.4%; dimmer→RGBW 档单档 15.09ms = 61.9%）
      · 对照: `_effect_addr_maps_for_fixtures` / `_collect_channel_structure`
        / `_build_positions` 命中 R38 静态缓存后 **0.00~0.07 ms/帧（<1%）**
        → **刻意不 C 化**（收益 ~0, 见 qascade_ce/effect_write_path.py 文档）。
      · `_compute_channel_frames` 的 57.1% 属效果引擎链（sort/空间/方向 +
        T4 C 内核）, 不在本 hook 范围。

    替换语义（逐分支对齐 :7139-7252）:
      * 直接通道: 电机 **覆盖写**（`arr[a] = v`）/ 非电机 **HTP max**;
        `acts[uni][a] = True`; 空 addr 向量**不建 bufs 条目**（保 `current_unis`
        集合不变 → NM 写入集合逐字不变）。
      * RGBW: `sums` = 四通道 bincount 部分和按 r→g→b→w 次序相加（浮点结合
        序逐字复刻）; `default` = sums==0 ? 255（W 且 has_rgb 时 0）: base[addr];
        `out = rint(default * values[fx]/255)`; 四通道一律 max 写 + act=1。
      * `current`（底色调色）仍由 Python 侧 `compute_universe_now` 取得 ——
        C 无法回调 Python; 由于该值在**调用方**算好放入 compute_cache,
        本 hook 需要时按同款缓存在包装层复算（见 `_ce_eff_current`）。
      * 内核拒绝（值域外 nan/inf/越界、下标越界、dtype/连续不符、电机路径
        地址重复、buf/act 形状异常）→ 本次调用回退原版, 输出逐字节不变。

    计数: `_EFF_WRITE_HIT_CNT`（C 完成的整段写）/ `_EFF_WRITE_MISS_CNT`
    （回退次数）。门控 `QASCADE_CE_EFFECT_WRITE=1`; env=0 恒原版（逃生口）。
    """
    if os.environ.get("QASCADE_CE_EFFECT_WRITE", "0") != "1":
        log.info("[CE] effect 写回层门控关闭 (QASCADE_CE_EFFECT_WRITE=1 启用)")
        return False
    if (engine is None or getattr(engine, "ce_eff_write_direct", None) is None
            or getattr(engine, "ce_eff_write_rgbw", None) is None):
        log.info("[CE] effect 写回层跳过: ce_eff_write_direct/rgbw 符号缺失"
                 "（保持 Python 路径）")
        return False
    try:
        from src.ui import effect_browser as _eb
    except Exception as _e:
        log.info("[CE] effect 写回层跳过: effect_browser 导入失败 %s", _e)
        return False
    try:
        from qascade_ce import effect_write_path as _ew
    except Exception:
        try:
            from render_lab.qascade_ce import effect_write_path as _ew
        except Exception as _e2:
            log.info("[CE] effect 写回层跳过: effect_write_path 导入失败 %s", _e2)
            return False

    _W = _eb.EffectBrowserWidget
    _orig_direct = _W._write_direct_channel_vectorized
    _orig_rgbw = _W._write_rgbw_channel_vectorized
    _orig_invalidate = _W._invalidate_tick_caches
    _failed = [False]
    _gt = [0, 0]        # [连续异常, 连续契约拒绝]（一次性告警）

    def _warn_once(kind, exc):
        if _gt[kind] == 1:
            log.warning("[CE-T14] effect 写回层 %s 回退原版（首次）: %s",
                        "异常" if kind == 0 else "契约拒绝", exc)

    def _plans(self_):
        return _ew.ctx_for(self_)

    def ce_write_direct(self, bufs, acts, direct_uni, values_arr, is_motor):
        if not _failed[0]:
            try:
                if not _ew._f64_ok(values_arr):
                    _gt[1] += 1
                    _warn_once(1, "values_arr 非 float64 连续一维")
                else:
                    ctx = _plans(self)
                    plan = _ew.plan_for(ctx["direct"], direct_uni,
                                        _ew.direct_plan, direct_uni)
                    if plan is None:
                        _gt[1] += 1
                        _warn_once(1, "direct_uni 结构不符契约")
                    else:
                        table, unis = plan
                        n_uni = len(unis)
                        if n_uni == 0:
                            _EFF_WRITE_HIT_CNT[0] += 1
                            return 0
                        arena = _ew.arena_for(ctx, bufs, acts)
                        ok = True
                        for i, uni in enumerate(unis):
                            ent = arena.slot(uni)
                            if ent is None:
                                ok = False
                                break
                            table[i * 5 + 3] = ent[1]
                            table[i * 5 + 4] = ent[3]
                        if not ok:
                            _gt[1] += 1
                            _warn_once(1, "buf/act 形状或 dtype 异常")
                        else:
                            rc = engine.ce_eff_write_direct(
                                n_uni, _ew._data_ptr(table),
                                int(values_arr.size),
                                _ew._data_ptr(values_arr),
                                1 if is_motor else 0)
                            if rc >= 0:
                                _EFF_WRITE_HIT_CNT[0] += 1
                                return rc
                            _gt[1] += 1
                            _warn_once(1, "内核返回码 %d" % rc)
            except Exception as _e:
                _CE_EXC_CNT[0] += 1
                _gt[0] += 1
                _warn_once(0, _e)
                _failed[0] = True
        _EFF_WRITE_MISS_CNT[0] += 1
        return _orig_direct(self, bufs, acts, direct_uni, values_arr, is_motor)

    def ce_write_rgbw(self, bufs, acts, rgbw_uni, values_arr, compute_cache):
        if not _failed[0]:
            try:
                if not _ew._f64_ok(values_arr):
                    _gt[1] += 1
                    _warn_once(1, "values_arr 非 float64 连续一维")
                else:
                    ctx = _plans(self)
                    plan = _ew.plan_for(ctx["rgbw"], rgbw_uni,
                                        _ew.rgbw_plan, rgbw_uni)
                    if plan is None:
                        _gt[1] += 1
                        _warn_once(1, "rgbw_uni 结构不符契约")
                    else:
                        table, unis = plan
                        n_uni = len(unis)
                        if n_uni == 0:
                            _EFF_WRITE_HIT_CNT[0] += 1
                            return 0
                        # ★★ 2026-09-19 守卫**已摘除**：该守卫是 R93 为绕过 C 侧
                        #   "`sums`/`part` scratch 同址别名"缺陷而临时加的（大批量
                        #   RGBW 时 base_off 落在 6144 附近会让内核自我清零）。
                        #   缺陷本身已在内核修好（`_ce_eff_kernel.c.inc` 改用独立
                        #   `part_buf`），并经 fuzz 逐字节对拍确认 0 失配 → 不再需要
                        #   Python 侧绕行，恢复 100% C 命中。
                        #   （`rgbw_scratch_alias_unsafe` 保留为诊断工具，不再拦截。）
                        arena = _ew.arena_for(ctx, bufs, acts)
                        zero_base = _ew.zeros512()
                        pz = _ew._data_ptr(zero_base)
                        ok = True
                        for i, uni in enumerate(unis):
                            ent = arena.slot(uni)
                            if ent is None:
                                _gt[1] += 1
                                _warn_once(1, "buf/act 形状或 dtype 异常")
                                ok = False
                                break
                            # ★ 底色调色: 逐字复刻原版 compute_universe_now
                            #   语义（compute_cache 跨通道类型复用; C 无法
                            #   回调 Python）
                            ok_base, cur, p_cur = _ew.current_ptr(
                                self._nm, uni, compute_cache, ctx)
                            if not ok_base:
                                _gt[1] += 1
                                _warn_once(1, "compute_universe_now 返回形态不符")
                                ok = False
                                break
                            table[i * 21 + 2] = pz if cur is None else p_cur
                            table[i * 21 + 19] = ent[1]
                            table[i * 21 + 20] = ent[3]
                        if ok:
                            rc = engine.ce_eff_write_rgbw(
                                n_uni, _ew._data_ptr(table),
                                int(values_arr.size),
                                _ew._data_ptr(values_arr))
                            if rc >= 0:
                                _EFF_WRITE_HIT_CNT[0] += 1
                                return rc
                            _gt[1] += 1
                            _warn_once(1, "内核返回码 %d" % rc)
            except Exception as _e:
                _CE_EXC_CNT[0] += 1
                _gt[0] += 1
                _warn_once(0, _e)
                _failed[0] = True
        _EFF_WRITE_MISS_CNT[0] += 1
        return _orig_rgbw(self, bufs, acts, rgbw_uni, values_arr, compute_cache)

    def ce_invalidate_tick_caches(self, *a, **k):
        """TopView/换工程/设备变化 → 静态结构与本 hook 的内核计划同链失效。"""
        try:
            self.__dict__.pop("_ce_eff_plan_cache", None)
        except Exception:
            pass
        return _orig_invalidate(self, *a, **k)

    _W._write_direct_channel_vectorized = ce_write_direct
    _W._write_rgbw_channel_vectorized = ce_write_rgbw
    _W._invalidate_tick_caches = ce_invalidate_tick_caches
    log.info("[CE] T14 已接入: EffectBrowserWidget._write_direct_channel_"
             "vectorized/_write_rgbw_channel_vectorized → ce_eff_write_direct"
             "/ce_eff_write_rgbw（_tick_play 写回层 C 计算; env=0 一键回退）")
    return True


# ═══ K5/K6: 灯跟踪 C 应用（QASCADE_CE_LT=1, 2026-08-10 flip）═══════════════

def _hook_lt(engine):
    """K5/K6: _apply_clip_light_tracking → ce_lt_apply（lt_path 束, 单任务直调）。

    接入点: PlaybackEngine._apply_clip_light_tracking（:4150-4650 全语义对拍,
    probe_lt_smoke.py 12 角 + 120 帧 0 diff）。C 侧 16 参:
    (scope blob / raw / out / local_t / nm_rgb / fx_live / cross 三表 /
    state 六表)。语义等价: scope None → 原样; rc==0 → 原样; rc>0 → out 拷贝
    （帧缓冲池拷贝语义）。引擎级状态槽（_lt_c_state / _lt_c_state_slots /
    _lt_c_color_slots / _lt_c_scope_cache, K6 Part 2 模式: 一次性 seed_from +
    每调用 back_write）随结构签名 / 槽容量增长 / 字典清空重建（:1209-1211）。
    失败（异常 / 内核拒绝）→ 委托原版, 字节不变。QASCADE_CE_LT=1 门控;
    env=0 恒原版 = 用户实测逃生口。
    """
    if os.environ.get("QASCADE_CE_LT", "0") != "1":
        log.info("[CE] LT 门控关闭 (QASCADE_CE_LT=1 启用)")
        return
    try:
        from src.engine import playback_engine as _pe
    except Exception as _e:
        log.info("[CE] LT 跳过: playback_engine 导入失败 %s（保持 Python 路径）", _e)
        return
    if engine is None or getattr(engine, "ce_lt_apply", None) is None:
        log.info("[CE] LT 跳过: ce_lt_apply 符号缺失（保持 Python 路径）")
        return
    try:
        from qascade_ce import lt_path as _ltp
    except Exception:
        try:
            from render_lab.qascade_ce import lt_path as _ltp
        except Exception as _e2:
            log.info("[CE] LT 跳过: lt_path 导入失败 %s（保持 Python 路径）", _e2)
            return
    try:
        from src.engine.playback_engine import (
            DEFAULT_LIGHT_TRACKING_ATTACK, DEFAULT_LIGHT_TRACKING_RELEASE)
    except Exception:
        DEFAULT_LIGHT_TRACKING_ATTACK = 0.08
        DEFAULT_LIGHT_TRACKING_RELEASE = 0.22
    _PC_U8 = ctypes.POINTER(ctypes.c_uint8)
    _PC_I = ctypes.POINTER(ctypes.c_int)
    _PC_D = ctypes.POINTER(ctypes.c_double)
    _PC_V = ctypes.POINTER(ctypes.c_void_p)
    _UNI = 512

    orig = _pe.PlaybackEngine._apply_clip_light_tracking
    from qascade_ce import native_lt_prepare as _lt_prepare
    _lt_prepare_fn = _lt_prepare.public_prepare
    _lt_prepare_meta = (_lt_prepare_fn.__code__, _lt_prepare_fn.__defaults__, _lt_prepare_fn.__kwdefaults__)
    _lt_owner = None

    def _lt_live_fn(self):
        nm0 = self._nm

        def live_fn(u, exclude_clip=True):
            if nm0 is None:
                return None
            try:
                return nm0.compute_universe_now(
                    int(u), exclude_layers=("clip_playback", "clip_preview",
                                            "timeline"))
            except Exception:
                return None
        return live_fn

    def _lt_nm_fn(self):
        nm0 = self._nm

        def nm_fn(u):
            if nm0 is None:
                return None
            try:
                return nm0.compute_universe_now(int(u))
            except Exception:
                return None
        return nm_fn

    def ce_apply_clip_lt(self, clip, raw, universe, local_t, project):
        if _lt_owner is None or project is None or type(self) is not _pe.PlaybackEngine:
            return orig(self, clip, raw, universe, local_t, project)
        if _lt_prepare.public_prepare is not _lt_prepare_fn or any(a is not b for a, b in zip(
                (_lt_prepare_fn.__code__, _lt_prepare_fn.__defaults__, _lt_prepare_fn.__kwdefaults__), _lt_prepare_meta)):
            _lt_prepare.COUNTERS['foreign_fallback'] += 1
            return orig(self, clip, raw, universe, local_t, project)
        try:
            params, semantic = _lt_prepare_fn(_lt_owner, clip)
        except Exception as _e:
            _LT_APPLY_FALLBACK_CNT[0] += 1
            _CE_EXC_CNT[0] += 1
            log.warning("[CE-LT] C 路径异常回退原版: %s", _e)
            return orig(self, clip, raw, universe, local_t, project)
        if semantic is not None:
            _warn_key = "%s: %s" % (type(semantic).__name__, semantic)
            if _warn_key not in self._lt_warned:
                self._lt_warned.add(_warn_key)
                _pe.log.warning("clip light tracking failed: %s", semantic)
            return raw
        if params is None:
            return orig(self, clip, raw, universe, local_t, project)
        try:
            source = params.source
            # 结构 memo（与 :4183-4212 同式; memo[3] = id→fixture 查找表,
            # 供 build_lt_scope 的 fx_by_id O(1) 路径）
            from src.engine.light_tracking_structure import structure_memo
            memo, rebuilt = structure_memo(self, project)
            struct_sig, all_fx = memo[0], memo[4]
            if rebuilt:
                try:
                    self._lt_pkg_src_to_lights = self._lt_ensure_pkg_map(
                        project, memo[2], source)
                except Exception:
                    self._lt_pkg_src_to_lights = {}
            # 单域 C scope（编译期; None/空 → 原样, 与 Python 空 scope/空跨域
            # 对同结果）
            # M1: _skey 需含 clip 内容签名——clip_key=str(clip.id) 在编辑后不变,
            #     否则 scope_cache 命中陈旧 light_mask/src_active_mask(审查 M1)。
            #     _clip_static_signature 按 clip_id 缓存, 编辑经引擎
            #     invalidate_clip_cache 失效, 命中即内容级新鲜。
            clip_key = self._clip_cache_id(clip)
            fx_by_id = memo[3] if len(memo) > 3 else {}
            state_slots = getattr(self, "_lt_c_state_slots", None)
            if state_slots is None:
                state_slots = {}
                self._lt_c_state_slots = state_slots
            color_slots = getattr(self, "_lt_c_color_slots", None)
            if color_slots is None:
                color_slots = {}
                self._lt_c_color_slots = color_slots
            scope_cache = getattr(self, "_lt_c_scope_cache", None)
            if scope_cache is None:
                scope_cache = {}
                self._lt_c_scope_cache = scope_cache
            _skey = (clip_key, int(universe), struct_sig, source,
                     params.threshold, params.attack, params.release,
                     params.color_is_white, params.cr, params.cg, params.cb,
                     params.max_brightness,
                     self._clip_static_signature(clip))
            scope = scope_cache.get(_skey)
            if scope is None:
                light_mask = np.asarray(self._clip_light_tracking_active_mask(
                    clip, int(universe), project), dtype=np.uint8)
                src_active_mask = np.asarray(
                    self._light_tracking_active_source_mask(
                        clip, int(universe), len(raw)), dtype=np.uint8)
                clip_unis = [int(x) for x in (clip.clip_universes or [])]
                scope = _ltp.build_lt_scope(
                    clip_key, int(universe), all_fx, memo[2],
                    getattr(self, "_lt_pkg_src_to_lights", None) or {},
                    source, light_mask, src_active_mask, clip_unis, params,
                    state_slots, color_slots, fx_by_id)
                if len(scope_cache) > 8192:
                    scope_cache.clear()
                scope_cache[_skey] = scope
            if scope is None:
                return raw
            # 引擎级 LT 状态（K6 Part 2: 常驻 seed + 每帧 back_write; 重建 =
            # 结构变/容量增长/字典清空守卫, seed 自三字典无损续接）
            st = getattr(self, "_lt_c_state", None)
            n_state = len(state_slots)
            n_color = len(color_slots)
            if _ltp.lt_state_needs_rebuild(st, n_state, n_color, self._light_tracking_state,
                                           self._light_tracking_time, self._light_tracking_color_cache):
                st = _ltp.LtEngineState(state_slots, color_slots)
                st.seed_from(self._light_tracking_state,
                             self._light_tracking_time,
                             self._light_tracking_color_cache)
                self._lt_c_state = st
            # 单任务束: start_t=0.0 + pos=local_t → 束内 local_t'=pos-0=local_t
            # （IEEE 精确, 无浮点往返误差; ce_lt_apply 直接收 local_t 不用
            # start_t, 0.0 仅为对齐束内计算式）
            task = {"clip_key": clip_key, "universe": int(universe),
                    "start_t": 0.0}
            bundle = _ltp.build_lt_bundle(
                [task], {(clip_key, int(universe)): scope}, st,
                float(local_t), _lt_live_fn(self), _lt_nm_fn(self),
                lambda _ck, _u: self._clip_frame_index_for_universe(
                    clip, int(_u)),
                lambda _suni, _ck, _pos: self._lt_find_pump_frames(
                    int(_suni), clip))
            raw_c = np.ascontiguousarray(raw, dtype=np.uint8)
            out = np.zeros(_UNI, dtype=np.uint8)
            has_cross = int(scope.n_cross) > 0
            rc = engine.ce_lt_apply(
                bundle.scope_ptrs[0],
                raw_c.ctypes.data_as(_PC_U8),
                out.ctypes.data_as(_PC_U8),
                float(local_t),
                ctypes.cast(bundle.nm_ptrs[0], _PC_U8),
                ctypes.cast(bundle.fx_ptrs[0], _PC_I),
                ctypes.cast(bundle.cross_tbl_ptrs[0], _PC_V) if has_cross else None,
                ctypes.cast(bundle.cross_t0_ptrs[0], _PC_D) if has_cross else None,
                ctypes.cast(bundle.cross_live_ptrs[0], _PC_I) if has_cross else None,
                bundle.state.ctypes.data_as(_PC_D),
                bundle.state_t.ctypes.data_as(_PC_D),
                bundle.state_exists.ctypes.data_as(_PC_U8),
                bundle.color.ctypes.data_as(_PC_U8),
                bundle.color_valid.ctypes.data_as(_PC_U8),
                bundle.touched.ctypes.data_as(_PC_U8),
                bundle.touched_color.ctypes.data_as(_PC_U8),
                int(bundle.n_state), int(bundle.n_color))
            _LT_APPLY_CNT[0] += 1
            st.back_write(self._light_tracking_state,
                          self._light_tracking_time,
                          self._light_tracking_color_cache)
            if rc < 0:
                _LT_APPLY_FALLBACK_CNT[0] += 1
                _CE_EXC_CNT[0] += 1
                return orig(self, clip, raw, universe, local_t, project)
            if rc == 0:
                return raw
            return out
        except Exception as _e:
            _LT_APPLY_FALLBACK_CNT[0] += 1
            _CE_EXC_CNT[0] += 1
            log.warning("[CE-LT] C 路径异常回退原版: %s", _e)
            return orig(self, clip, raw, universe, local_t, project)

    _lt_owner = _lt_prepare.install(engine, orig, ce_apply_clip_lt, _ltp.LtParams)
    _pe.PlaybackEngine._apply_clip_light_tracking = ce_apply_clip_lt
    log.info("[CE] LT 已接入: _apply_clip_light_tracking → ce_lt_apply")


def install():
    """门控安装：QASCADE_CT_ENGINE=1 且 DLL 可用时启用 CE 替换点。

    替换点（逐步接入）:
      P1: PlaybackEngine._mix_clips 的 C 内核句柄（K0 帧合成）
      P3: EffectEngine._run_compute_fn     (K1 波形求值)
      P4: NetworkManager._mix_one_universe (K2 全链路混合)
    """
    global _installed, _engine
    if _installed:
        return
    if os.environ.get("QASCADE_CT_ENGINE", "0") != "1":
        return
    # T5b: OpenMP 工作线程默认 PASSIVE（空闲睡眠而非自旋）——实测 73 域批量
    # flush 期间 16 线程自旋 → 本进程 CPU 686%; PASSIVE → 93% 且 flush 更快
    # （自旋在负载下反噬主线程）。显式设置 OMP_WAIT_POLICY=ACTIVE 可覆盖。
    os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
    # ★ 契约快照（2026-09-13 加）：装前记录所有替换目标对象，装完比对。
    #   目的 = 把"src/ 改名 → CE 静默回退"变成显式警告 —— 这是 R56 型事故
    #   （渲染 11s→61s 而门1 哈希不变）的根治手段。
    _contract_before: dict = {}
    try:
        from . import hook_contract as _hc
        _contract_before = _hc.snapshot_targets()
    except Exception as _hce:
        log.info("[CE] 契约快照跳过: %s", _hce)
    try:
        if _REPO_ROOT not in sys.path:
            sys.path.insert(0, _REPO_ROOT)
        from .backend import detect_backend
        from .ctypes_api import load_or_none
        engine = load_or_none()
        if engine is None:
            log.info("[CE] DLL 不可用，回退原渲染路径（字节不变）")
            _installed = True   # 会话内不再重试
            return
        _engine = engine
        backend = detect_backend()
        log.info("[CE] 已启用 C 渲染引擎: %s", backend)
        # P6c: OpenMP 并行区等待策略 — PASSIVE（worker 空闲即睡, 不自旋抢核;
        # ACTIVE 实测 spin 燃烧: idle500ms 进程CPU +281ms, 门3 帧 18→45ms）。
        # 线程数=物理核 8（SMT 16 线程对 3-60µs 小任务负收益）。必须在本进程
        # 首个并行区之前设置（vcomp 首次进入 parallel 时读环境）; setdefault
        # 尊重外部显式配置。
        os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
        os.environ.setdefault("OMP_NUM_THREADS", "8")
        _hook_k0(engine)
        _hook_frame_path_p4b(engine)
        _hook_indexed_holds(engine)
        _hook_direction_fade(engine)
        _hook_precharge(engine)
        # T5b: flush 批量混合（先于计时 hook 注册——计时包住批量路径）
        _hook_flush_batch(engine)
        _hook_flush_timing(engine)
        # T9: 空闲态心跳轮转纯数学段 → C 内核三件套（到期判定/周期计划/批取轮转）
        _hook_heartbeat(engine)
        # H3 第三批: xy 参考表构建 / 输出平滑器 / 安全联锁 / 音频特征 / 相位铺开
        _hook_xy_ref_build(engine)
        _hook_smoother(engine)
        _hook_interlock(engine)
        _hook_audio(engine)
        # T-recon2: MSA 结构分析纯数学段 → C（_on_frame 热路径深化）
        _hook_audio_msa(engine)
        # T10: TopView 同步/编组纯数学段 → C（同步角/编组 11382 台全选提速）
        _hook_topview(engine)
        # K2: 离线时间线整帧融合（QASCADE_CE_TIMELINE_RANGE=1, 2026-08-10 flip）
        _hook_timeline_range(engine)
        from .prepared_timeline import install as install_prepared_frames
        install_prepared_frames(engine)
        # K5/K6: 灯跟踪 C 应用（QASCADE_CE_LT=1, 2026-08-10 flip;
        # 用户实测唯一验证者 — env=0 恒原版逃生口）
        _hook_lt(engine)
        # T12: 关联器整段 C 计算（QASCADE_CE_LINKER=1, 2026-09-18 接入;
        # 25ms tick 热点 _process_rule 实测占 82.1%, 见 probe_linker_tick_profile.py;
        # 逐规则契约守卫 + 内核拒绝 → 该规则回退原版, env=0 全量逃生口）
        try:
            _hook_linker(engine)
        except Exception as _elk:
            log.info("[CE] T12 关联器安装异常, 保持原版: %s", _elk)
        # T13: 矩阵像素引擎 v40 计算族 C 计算（QASCADE_CE_MATRIX_PIXEL=1;
        # 用户指令「新功能的计算层应该及时更新到 CE 里面并通过它完成计算
        # 调用, 保证全命中」; 1356 台实测 particle 27.8ms/帧 / pattern 6.5ms/帧,
        # 见 _ui_validation/probe_v40_segments.py; 契约拒绝 → 该帧回退原版,
        # env=0 一键回退逃生口）
        try:
            _hook_matrix_pixel(engine)
        except Exception as _emx:
            log.info("[CE] T13 矩阵像素引擎安装异常, 保持原版: %s", _emx)
        # T14: effect `_tick_play` 脚手架写回层 C 计算
        # （QASCADE_CE_EFFECT_WRITE=1; 同一用户指令。效果预设 compute 已在
        # CE T4 全族, 写回层没在 —— 1356 台实测 _write_channel_vectorized 占
        # 整帧 31.3%（dimmer→RGBW 档 61.9%）, 见 probe_effect_tick_profile.py;
        # 契约拒绝/内核拒绝 → 本次调用回退原版, env=0 一键回退逃生口）
        try:
            _hook_effect_write(engine)
        except Exception as _eew:
            log.info("[CE] T14 effect 写回层安装异常, 保持原版: %s", _eew)
        # P9: 发送流水线（stage C 流水线重叠 — 蓝图 §3.3/§3.4）
        # 常驻发送线程 + C 侧发送环（QASCADE_CE_SEND_RING=1）+ 异步 flush
        # （QASCADE_CE_FLUSH_ASYNC=1）; 新路径默认关, 显式 env 开启。
        try:
            from . import send_pipeline
            send_pipeline.install(engine)
        except Exception as _pe:
            log.warning("[CE-P9] 发送流水线初始化失败: %s", _pe)
        # T11: TopView 交互几何剩余热点 → numpy 批量/C（QASCADE_CE_T11=1 门控）
        try:
            from qascade_ce.topview_interact import install_t11
            install_t11(engine)
        except Exception as _e11:
            log.info("[CE] T11 安装失败, 保持原版: %s", _e11)
        # PACK: 输出路由器批量打包（QASCADE_CE_PACK=1 门控, 默认关）
        try:
            _install_pack_hook(engine)
        except Exception as _ep:
            log.info("[CE-PACK] 安装异常, 保持原版: %s", _ep)
        # K3-GPU: 离线烘焙 GPU 分派（2026-08-09 用户指令: 离线渲染充分调用
        # 算力, OpenCL/CUDA 自动识别; QASCADE_CE_GPU gate, 代码默认未设 =
        # 禁用（纯 CPU）, exe 内经 _mount_ce 挂载为 auto）。
        # 回调注册 = C 侧 ce_bake_sequence_omp 分派点优先; GPU 失败/缺失
        # 自动回退 OpenMP/串行（逐字节不变）。懒加载: 探测在首次烘焙才发生。
        try:
            from . import gpu_bake as _ce_gpu_bake
            _ce_gpu_bake.install_bake_gpu(engine)
        except Exception as _eg:
            log.info("[CE-K3] GPU 烘焙分派注册跳过: %s", _eg)
        from .native_domains import install as install_native_domains
        install_native_domains(engine)
        # ★ 契约校验（装完）：确认每个契约目标**真的被替换**。
        #   未替换 / 目标缺失 = 该 hook 已脱靶（src/ 改名或门控关闭），
        #   显式写 warning 并落盘，避免"性能悄悄变慢而无人察觉"。
        try:
            _rep = _hc.verify_installed(_contract_before)
            global _CE_CONTRACT_REPORT
            _CE_CONTRACT_REPORT = _rep
            _miss = _rep.get("missing_target") or []
            _not = _rep.get("not_attached") or []
            if _miss:
                log.warning("[CE] ★ 契约脱靶 %d 项（目标缺失，多为 src/ 改名）：%s",
                            len(_miss), [m["hook"] for m in _miss])
                for _m in _miss[:6]:
                    log.warning("[CE]    %s → %s : %s",
                                _m["hook"], _m["target"], _m["why"])
            if _not:
                log.info("[CE] 未替换 %d 项（门控关闭或符号缺失）：%s",
                         len(_not), [n["hook"] for n in _not])
            log.info("[CE] 契约校验：已替换 %d 项 / 契约 %d 项",
                     len(_rep.get("attached") or []), len(_hc.HOOKS))
        except Exception as _cv:
            log.info("[CE] 契约校验跳过: %s", _cv)
        _installed = True
    except Exception as e:
        log.warning("[CE] 初始化失败，回退原路径: %s", e)
        _installed = True


def _install_pack_hook(engine):
    """PACK: 输出路由器批量打包（QASCADE_CE_PACK=1 门控, 默认关）。

    OutputRouter 排空循环逐包 send_dmx → 批量 ce_*_pack_batch 一次打包
    全部域（ArtNet 2.8x / sACN 6.5x, fuzz 位级等价 2 万轮已证）后仍逐包
    sendto（UDP 发送保持 Python, _send_to_hw 边界不破）。零 src/ 改动
    （补丁自 qascade_ce 侧）。符号缺失/异常/非 512 帧 → 回退逐包（语义
    不变）。gate 关闭时补丁不装（零变化）。
    """
    import os as _os
    if _os.environ.get("QASCADE_CE_PACK", "0") != "1":
        return
    try:
        from src.network.network_manager import (NetworkManager,
                                                 _to_artnet_universe)
        from src.network.artnet_node import ArtNetSender
        import src.network.sacn_node as _sn
        from .pack_sender_batch import send_artnet_batch, send_sacn_batch
    except Exception as _e:
        log.info("[CE-PACK] 导入失败, 不装: %s", _e)
        return
    _art = getattr(engine, "ce_artnet_pack_batch", None)
    _sac = getattr(engine, "ce_sacn_pack_batch", None)
    if _art is None and _sac is None:
        log.info("[CE-PACK] DLL 无批量打包符号, 不装")
        return

    def _art_batch(self, frames):
        return send_artnet_batch(self, frames, _art)

    def _sac_batch(pool, frames):
        return send_sacn_batch(pool, frames, _sac)

    try:
        ArtNetSender.send_dmx_batch = _art_batch
        _sn.SACNSender.send_dmx_batch = staticmethod(_sac_batch)
        # Router owns pacing, attempt deadlines and keepalive for both paths.

        def _direct_batch(self, frames):
            if self._artnet_tx:
                self._artnet_tx.send_dmx_batch(
                    {_to_artnet_universe(int(u)): d
                     for u, d in frames.items()})
            else:
                return bool(_sn.SACNSender.send_dmx_batch(
                    self._sacn_tx_pool, frames))
            return True

        NetworkManager.send_dmx_batch = _direct_batch
        log.info("[CE-PACK] 批量打包已装 (QASCADE_CE_PACK=1)")
    except Exception as _e:
        log.info("[CE-PACK] 安装失败, 保持原版: %s", _e)


def is_active() -> bool:
    return _installed and os.environ.get("QASCADE_CT_ENGINE", "0") == "1"


# ── Route Runner CE 覆盖内核高层封装 (懒加载) ──────────────────────────
# runner_engine 经 getattr(qascade_ce, "ce_route_coverage") 取用; 本 import
# 只做轻量模块级 import (ctypes/DLL 加载在首次 CERouteCoverage._ensure 才发生)。
try:
    from .ce_route import CERouteCoverage, ce_route_coverage
except Exception:  # pragma: no cover - 导入失败则不暴露
    CERouteCoverage = None
    ce_route_coverage = None
