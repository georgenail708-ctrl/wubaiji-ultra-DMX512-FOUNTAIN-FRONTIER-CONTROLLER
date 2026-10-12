# -*- coding: utf-8 -*-
"""K2 离线时间线 C 渲染上下文（ce_timeline_frame 的预建/绑定/调用侧）。

职责:
  * 幂等装载: TimelineCtx 持有独立 ce_ctx 实例（与实时 P6 持久 ctx 分离,
    互不污染）; 槽 = (clip_cache_id, universe) 编译产物（ce_clip_register
    注册期算锚点, base_active & rule_active 语义同 _get_clip_anchor 冷缓存）。
  * 静态 CSR 任务表: task_off[frame+1] 偏移 + 全表任务序数组
    （clip_slot/universe/start_t/rate/src_dur/tie, [n_tasks]）; record 子集
    同构第二表（record_output_source_enabled clips, 独立 resolve 组）。
  * 调用侧: 每帧 1 次 ce_timeline_frame（预分配输出缓冲 + ascontiguousarray
    data_as 零拷贝直传）; 返回码 <0 → None（调用方回退原路径）; 符号缺失
    （绑定 None / 引擎未加载）→ 恒回退（available=False）。
  * env 门控: QASCADE_CE_TIMELINE_RANGE（默认 "0" 关闭, 不 flip — 变更区
    门控纪律: 须等 Fast-Path 合入 + probe_timeline_k2_bytecheck.py
    7149 帧逐字节 0 diff 通过后才 flip 默认开）。
  * LT env 门控: QASCADE_CE_LT（默认 "0" 关闭, 不 flip — 灯跟踪用户实测
    是唯一验证者; probe_lt_smoke.py 12 角 + 120 帧 0 diff 已过, 仍须真机
    验证后才单独决策 flip）。

不依赖 src/（离线探针/字节对拍框架直用）; 引用保留 numpy 数组防 GC 释放。
"""

from __future__ import annotations

import ctypes
import os
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from .audio_mod_gate import requires_python_audio_scope, requires_python_clip_time

# 双轨导入（2026-08-10 打包根治）: 顶层名 qascade_ce.* 为 exe 主轨（PyInstaller
# 按真实文件路径归一化去重, 同文件只产出 qascade_ce.* 键, render_lab.* 无论
# 如何 hidden-import 都不进 PYZ — 见 tools/verify_ce_pack.py）; render_lab 前缀
# 仅 dev 仓库根解析兜底。仅顶层名在 exe 内稳定可达 → 先顶层后前缀。
try:
    from qascade_ce.ctypes_api import load_or_none
except Exception:  # dev: 仓库根在 sys.path 时经 render_lab 命名空间解析
    from render_lab.qascade_ce.ctypes_api import load_or_none

__all__ = ["timeline_range_enabled", "lt_enabled", "TimelineCtx", "tie_value",
           "DirRowBuilder"]

_UNI_SIZE = 512

_PC_I = ctypes.POINTER(ctypes.c_int)
_PC_I32 = ctypes.POINTER(ctypes.c_int32)
_PC_U8 = ctypes.POINTER(ctypes.c_uint8)
_PC_F = ctypes.POINTER(ctypes.c_float)
_PC_D = ctypes.POINTER(ctypes.c_double)


class CeDirBlock(ctypes.Structure):
    """K7 方向 fade 任务块 — 与 ce_engine.c ce_dir_block :2889-2976 严格同序。

    布局: 23×c_double + 25×c_int + 34×c_void_p = 184+100+4pad+272 = 560 字节。
    数组字段经 data_as 赋值（probe 侧保证 dtype/长度与 C 契约一致）。
    """

    _fields_ = [
        # ── f64 标量（与 C 注释行号一一对应）──
        ("dur", ctypes.c_double),          # :8157
        ("fi_raw", ctypes.c_double),       # :3768
        ("fo_raw", ctypes.c_double),       # :3769
        ("fi", ctypes.c_double),           # :8161
        ("fo", ctypes.c_double),           # :8162
        ("sdi", ctypes.c_double),          # :8168
        ("sdo", ctypes.c_double),          # :8169
        ("sdi_raw", ctypes.c_double),      # :3644
        ("sdo_raw", ctypes.c_double),      # :3645
        ("single", ctypes.c_double),       # :2780
        ("single_raw", ctypes.c_double),   # :8167
        ("pg_b_in", ctypes.c_double),      # :7941
        ("pg_b_out", ctypes.c_double),
        ("pause_sum", ctypes.c_double),    # :2787
        ("seq_span", ctypes.c_double),     # :3647-3650
        ("sequence_span", ctypes.c_double),  # :8187-8195
        ("per_group", ctypes.c_double),    # :7602-7608
        ("out_target", ctypes.c_double),   # :7637
        ("out_span", ctypes.c_double),     # :7638
        ("out_scale", ctypes.c_double),    # :7639
        ("out_per_group", ctypes.c_double),  # :7640
        ("phase_amount", ctypes.c_double),  # :2947/:3052
        ("src_dur", ctypes.c_double),
        # ── int 标量 ──
        ("dir_in_active", ctypes.c_int),
        ("dir_out_active", ctypes.c_int),
        ("groups", ctypes.c_int),          # :8163
        ("batch_count", ctypes.c_int),     # :2771
        ("te", ctypes.c_int),              # :5174
        ("curve_fi", ctypes.c_int),
        ("curve_fo", ctypes.c_int),
        ("strategy_fi", ctypes.c_int),
        ("strategy_fo", ctypes.c_int),
        ("path", ctypes.c_int),            # :8223/:8294
        ("K", ctypes.c_int),
        ("seq_gains", ctypes.c_int),       # :8242/:7968
        ("seq_enabled", ctypes.c_int),     # :2773
        ("ntypes", ctypes.c_int),          # :8171-8180
        ("nentries", ctypes.c_int),
        ("max_step_in", ctypes.c_int),     # :7871
        ("max_step_out", ctypes.c_int),
        ("batch_count_b", ctypes.c_int),   # :7939-7940
        ("n_copy_rows", ctypes.c_int),
        ("remap_enabled", ctypes.c_int),   # :3396
        ("remapped_source", ctypes.c_int),  # :3388
        ("seed_fallback", ctypes.c_int),   # :3389
        ("remap_universe_ok", ctypes.c_int),  # :2960
        ("motor_phase_enabled", ctypes.c_int),  # :2944-2946
        ("parallel_remap", ctypes.c_int),  # :2949-2956
        # ── 指针（数组字段; None → 保持 NULL）──
        ("ease_lut", ctypes.c_void_p),
        ("anchor", ctypes.c_void_p),
        ("target", ctypes.c_void_p),
        ("spans", ctypes.c_void_p),
        ("gi_in", ctypes.c_void_p),
        ("gp_in", ctypes.c_void_p),
        ("gs_in", ctypes.c_void_p),
        ("gi_out", ctypes.c_void_p),
        ("gp_out", ctypes.c_void_p),
        ("gs_out", ctypes.c_void_p),
        ("ratio_in", ctypes.c_void_p),
        ("ratio_out", ctypes.c_void_p),
        ("entries", ctypes.c_void_p),
        ("channels", ctypes.c_void_p),
        ("pos_in", ctypes.c_void_p),
        ("pos_out", ctypes.c_void_p),
        ("gppos_in", ctypes.c_void_p),
        ("gppos_out", ctypes.c_void_p),
        ("gidx_in", ctypes.c_void_p),
        ("gpos_in", ctypes.c_void_p),
        ("gsize_in", ctypes.c_void_p),
        ("gidx_out", ctypes.c_void_p),
        ("gpos_out", ctypes.c_void_p),
        ("gsize_out", ctypes.c_void_p),
        ("bo_b_in", ctypes.c_void_p),
        ("bo_b_out", ctypes.c_void_p),
        ("gidx_c", ctypes.c_void_p),
        ("copy_rows", ctypes.c_void_p),
        ("copy_f", ctypes.c_void_p),
        ("end_raw", ctypes.c_void_p),
        ("base_order_mask", ctypes.c_void_p),
        ("offsets", ctypes.c_void_p),
        ("bo_a", ctypes.c_void_p),
        ("bo_a_out", ctypes.c_void_p),
    ]


# 指针字段 → (ctypes 指针类型, numpy dtype)（serialize 强制 dtype 防错位）
_DIR_PTR_FIELDS = {
    "ease_lut": (_PC_F, np.float32),
    "anchor": (_PC_F, np.float32),
    "target": (_PC_F, np.float32),
    "spans": (_PC_I32, np.int32),
    "gi_in": (_PC_F, np.float32),
    "gp_in": (_PC_F, np.float32),
    "gs_in": (_PC_F, np.float32),
    "gi_out": (_PC_F, np.float32),
    "gp_out": (_PC_F, np.float32),
    "gs_out": (_PC_F, np.float32),
    "ratio_in": (_PC_D, np.float64),
    "ratio_out": (_PC_D, np.float64),
    "entries": (_PC_I32, np.int32),
    "channels": (_PC_I32, np.int32),
    "pos_in": (_PC_F, np.float32),
    "pos_out": (_PC_F, np.float32),
    "gppos_in": (_PC_F, np.float32),
    "gppos_out": (_PC_F, np.float32),
    "gidx_in": (_PC_F, np.float32),
    "gpos_in": (_PC_F, np.float32),
    "gsize_in": (_PC_F, np.float32),
    "gidx_out": (_PC_F, np.float32),
    "gpos_out": (_PC_F, np.float32),
    "gsize_out": (_PC_F, np.float32),
    "bo_b_in": (_PC_F, np.float32),
    "bo_b_out": (_PC_F, np.float32),
    "gidx_c": (_PC_I32, np.int32),
    "copy_rows": (_PC_I32, np.int32),
    "copy_f": (_PC_F, np.float32),
    "end_raw": (_PC_U8, np.uint8),
    "base_order_mask": (_PC_U8, np.uint8),
    "offsets": (_PC_D, np.float64),
    "bo_a": (_PC_F, np.float32),
    "bo_a_out": (_PC_F, np.float32),
}

_DIR_SCALAR_DOUBLE = [
    "dur", "fi_raw", "fo_raw", "fi", "fo", "sdi", "sdo",
    "sdi_raw", "sdo_raw", "single", "single_raw", "pg_b_in", "pg_b_out",
    "pause_sum", "seq_span", "sequence_span", "per_group", "out_target",
    "out_span", "out_scale", "out_per_group", "phase_amount", "src_dur",
]
_DIR_SCALAR_INT = [
    "dir_in_active", "dir_out_active", "groups", "batch_count", "te",
    "curve_fi", "curve_fo", "strategy_fi", "strategy_fo", "path", "K",
    "seq_gains", "seq_enabled", "ntypes", "nentries", "max_step_in",
    "max_step_out", "batch_count_b", "n_copy_rows", "remap_enabled",
    "remapped_source", "seed_fallback", "remap_universe_ok",
    "motor_phase_enabled", "parallel_remap",
]

# ── K7 方向 fade: dir 行构建（自 probe_fade_dir_bytecheck.py 移植 — 生产
#    _k2_build_tables 逐帧平行构建 per_frame_dirs/record_frame_dirs, 行与
#    任务行一一对应; 探针从此处导入, 与生产同源单份实现）───────────────────────
_CURVE_IDX = {"linear": 0, "easein": 1, "easeout": 2, "easeinout": 3, "bezier": 4}
_STRAT_IDX = {"standard_fade": 0, "immediate": 1, "decelerate": 2,
              "hold_decay": 3, "step_down": 4, "smooth_ease": 5,
              "delay_then_cut": 6}


def _parallel_group_positions(direction_pos: np.ndarray,
                              group_count: int) -> tuple[np.ndarray, int]:
    """复刻 _compute_group_gains_vectorized 内嵌 :7869-7897。"""
    K = int(len(direction_pos))
    group_count = max(1, min(int(group_count or 1), max(1, K)))
    if K <= 1:
        return np.zeros(K, dtype=np.float32), 1
    max_step_count = int(np.ceil(K / float(group_count)))
    group_idx = np.zeros(K, dtype=np.int32)
    max_group_size = 1
    for element_index in range(K):
        group_idx[element_index] = min(group_count - 1, element_index // max_step_count)
    group_pos = np.zeros(K, dtype=np.float32)
    for group in range(group_count):
        indices = np.flatnonzero(group_idx == group)
        if indices.size <= 0:
            continue
        max_group_size = max(max_group_size, int(indices.size))
        if indices.size <= 1:
            continue
        local_order = indices[np.argsort(direction_pos[indices], kind="stable")]
        denom = float(max(1, indices.size - 1))
        for rank, element_index in enumerate(local_order):
            group_pos[int(element_index)] = float(rank) / denom
    if max_group_size <= 1:
        return np.zeros(K, dtype=np.float32), 1
    return group_pos, max_group_size


def _sequential_group_positions(direction_pos: np.ndarray,
                                group_count: int) -> tuple[np.ndarray, np.ndarray, int, np.ndarray]:
    """复刻 _compute_group_gains_vectorized 内嵌 :7899-7935。"""
    K = int(len(direction_pos))
    group_count = max(1, min(int(group_count or 1), max(1, K)))
    if K <= 1:
        return (np.zeros(K, dtype=np.float32), np.zeros(K, dtype=np.float32),
                1, np.ones(K, dtype=np.float32))
    max_step_count = int(np.ceil(K / float(group_count)))
    group_idx = np.zeros(K, dtype=np.int32)
    element_group_size = np.ones(K, dtype=np.float32)
    for element_index in range(K):
        group = min(group_count - 1, element_index // max_step_count)
        group_start = group * max_step_count
        group_end = min(K, group_start + max_step_count)
        group_size = max(1, group_end - group_start)
        group_idx[element_index] = group
        element_group_size[element_index] = float(group_size)
    group_pos = np.zeros(K, dtype=np.float32)
    for group in range(group_count):
        indices = np.flatnonzero(group_idx == group)
        if indices.size <= 1:
            continue
        local_order = indices[np.argsort(direction_pos[indices], kind="stable")]
        denom = float(max(1, indices.size - 1))
        for rank, element_index in enumerate(local_order):
            group_pos[int(element_index)] = float(rank) / denom
    return (group_idx.astype(np.float32), group_pos, group_count,
            element_group_size)


class DirRowBuilder:
    """按 (clip, universe) 构建 CeDirBlock 行 dict（引擎方法 + 嵌套函数复刻）。

    行内容与 local_t 无关（全部字段 = clip 静态数据/缓存几何）; 同一 (clip,u)
    跨帧复用同一 dict（_serialize_dir_block 每行新建块实例）。attach(ctx) 由
    _k2_build_tables 在建表前调用（注册 sample 槽需要 ctx）。自探针移植后
    生产与探针共用单份实现, 行数 = 任务数 → dir 表不再恒 NULL。
    """

    def __init__(self, engine, proj):
        self.engine = engine
        self.proj = proj
        self.ctx = None
        self.cache = {}

    def attach(self, ctx) -> None:
        self.ctx = ctx

    def __call__(self, clip, universe, track, compiled, target_universes):
        key = (id(clip), int(universe))
        # ★ 2026-08-11: None 行（dur<=0 / 无方向 fade → _build 返回 None）也
        #   必须缓存 — 旧 `get → None → 重建` 让 None 行每帧重跑 _build
        #   （示例工程 非方向 fade clip 占多数 → 149305 次/段 ≈ 3.5s 纯浪费）。
        #   in 判定区分「未缓存」与「缓存了 None」。
        if key in self.cache:
            return self.cache[key]
        row = self._build(clip, int(universe), track, compiled,
                          list(target_universes or []))
        self.cache[key] = row
        return row

    # ── 行构建 ────────────────────────────────────────────────────────
    def _build(self, clip, u, track, compiled, target_universes):
        e = self.engine
        dur = max(0.0, float(getattr(clip, "duration", 0.0) or 0.0))
        if dur <= 0.0:
            return None
        fi_raw = max(0.0, float(getattr(clip, "fade_in", 0.0) or 0.0))
        fo_raw = max(0.0, float(getattr(clip, "fade_out", 0.0) or 0.0))
        if not e._clip_has_directional_fade(clip):
            return None
        fi = min(fi_raw, dur)
        fo = min(fo_raw, dur)
        sdi_raw = max(0.0, float(getattr(clip, "seq_delay_in", 0.0) or 0.0))
        sdo_raw = max(0.0, float(getattr(clip, "seq_delay_out", 0.0) or 0.0))
        sdi = min(sdi_raw, dur)
        sdo = min(sdo_raw, dur)
        single_raw = max(0.0, float(getattr(clip, "fade_group_single_duration",
                                            0.0) or 0.0))
        groups = max(1, int(getattr(clip, "fade_groups", 1) or 1))
        seq_mode = str(getattr(clip, "fade_group_sequence_mode", "parallel")
                       or "parallel").strip().lower()
        if seq_mode not in ("parallel", "sequential"):
            seq_mode = "parallel"
        in_dir = e._get_clip_fade_in_direction(clip)
        out_dir = e._get_clip_fade_out_direction(clip)
        # R152 起权威 6 元组（末项 step = 延迟翼错峰步长，None → 退回 single）
        seq_enabled, _g, pauses_d, single_d, batch_count, seq_step = \
            e._clip_sequential_timing_detail(clip)
        offset_step = single_d if seq_step is None else seq_step
        _seq_en2, _seq_groups, seq_pause, seq_single = \
            e._clip_sequential_group_timing(clip)
        group_pause = max(0.0, float(getattr(clip, "fade_group_pause", 0.0) or 0.0))
        seq_span = (e._clip_sequential_span(clip, _seq_groups, seq_pause,
                                            seq_single) if seq_enabled else 0.0)
        is_sequential = seq_mode == "sequential" and groups > 1 and single_raw > 1e-4
        sequence_span = (e._clip_sequential_span(clip, groups, group_pause,
                                                 single_raw)
                         if is_sequential else 0.0)
        src_dur = e._clip_source_duration(clip, dur)
        phase_amount = e._clip_motor_phase_amount(clip)
        motor_phase_enabled = bool(getattr(clip, "motor_transition_phase_enabled",
                                           False))
        row = {
            "dur": dur, "fi_raw": fi_raw, "fo_raw": fo_raw, "fi": fi, "fo": fo,
            "sdi": sdi, "sdo": sdo, "sdi_raw": sdi_raw, "sdo_raw": sdo_raw,
            "single": single_d, "single_raw": single_raw,
            "pause_sum": float(sum(pauses_d)),
            "seq_span": seq_span, "sequence_span": sequence_span,
            "phase_amount": phase_amount, "src_dur": src_dur,
            "dir_in_active": 1 if in_dir != "none" else 0,
            "dir_out_active": 1 if out_dir != "none" else 0,
            "groups": groups, "batch_count": batch_count,
            "te": 1 if e._clip_transition_enabled(clip) else 0,
            "curve_fi": _CURVE_IDX[e._clip_transition_curve(clip, fade_in=True)],
            "curve_fo": _CURVE_IDX[e._clip_transition_curve(clip, fade_in=False)],
            "strategy_fi": _STRAT_IDX[e._clip_transition_strategy(clip, fade_in=True)],
            "strategy_fo": _STRAT_IDX[e._clip_transition_strategy(clip, fade_in=False)],
            "seq_gains": 1 if (seq_mode == "sequential" and groups > 1) else 0,
            "seq_enabled": 1 if seq_enabled else 0,
            "pg_b_in": single_raw if single_raw > 1e-4 else max(1e-6, fi),
            "pg_b_out": single_raw if single_raw > 1e-4 else max(1e-6, fo),
            "motor_phase_enabled": 1 if motor_phase_enabled else 0,
            "parallel_remap": 1 if ((not seq_enabled) and motor_phase_enabled) else 0,
        }
        u_fixtures = list(compiled.get("u_fixtures", []) or [])
        path = 0 if u_fixtures else 1
        row["path"] = path
        ratio_in = {e._norm_ratio_channel_type(k): float(v)
                    for k, v in (getattr(clip, "fade_type_ratio_in", {}) or {}).items()
                    if abs(float(v) - 1.0) > 1e-3}
        ratio_out = {e._norm_ratio_channel_type(k): float(v)
                     for k, v in (getattr(clip, "fade_type_ratio_out", {}) or {}).items()
                     if abs(float(v) - 1.0) > 1e-3}
        ease_lut = e._seq_delay_ease_lut(getattr(clip, "seq_delay_curve", None))
        if ease_lut is not None:
            row["ease_lut"] = ease_lut
        anchor = e._get_fade_in_anchor(clip, u, track=track, proj=self.proj)
        if anchor is not None:
            row["anchor"] = anchor
        target = e._get_fade_out_target(u, proj=self.proj)
        if target is not None:
            row["target"] = target
        row["offsets"] = np.asarray(
            e._clip_sequential_offsets(offset_step, pauses_d, batch_count),
            dtype=np.float64)
        bom = e._clip_base_order_channel_mask(clip, u)
        if bom is not None:
            row["base_order_mask"] = bom
        end_raw = e._compiled_clip_frame_data(compiled, max(0.0, src_dur - 1e-4))
        if end_raw is not None:
            row["end_raw"] = end_raw
        frame_universes = e._clip_frame_universes(clip)
        row["remap_universe_ok"] = 0 if (frame_universes and u not in frame_universes) else 1

        if path == 0:
            self._build_path_a(row, e, clip, u, track, compiled, groups,
                               batch_count, group_pause, single_raw, sdi, sdo,
                               fi, fo, dur, in_dir, out_dir, ratio_in, ratio_out)
        else:
            self._build_path_b(row, e, clip, u, track, groups, group_pause,
                               single_raw, sdi, sdo, fi, fo, in_dir, out_dir)
        self._fill_remap(row, e, clip, u, track, compiled, target_universes,
                         seq_enabled, row["parallel_remap"], in_dir, src_dur)
        return row

    def _build_path_a(self, row, e, clip, u, track, compiled, groups, batch_count,
                      group_pause, single_raw, sdi, sdo, fi, fo, dur,
                      in_dir, out_dir, ratio_in, ratio_out):
        sorted_fixtures = list(compiled.get("sorted_fixtures", []) or [])
        spans = list(compiled.get("spans", []) or [])
        K = len(sorted_fixtures)
        row["K"] = K
        if K <= 0:
            return
        row["spans"] = np.asarray(spans, dtype=np.int32)
        if in_dir != "none":
            gi, gp, gs = e._sequential_fixture_group_data(
                clip, sorted_fixtures, in_dir, groups, track=track, proj=self.proj)
            row["gi_in"], row["gp_in"], row["gs_in"] = gi, gp, gs
        if out_dir != "none":
            gi, gp, gs = e._sequential_fixture_group_data(
                clip, sorted_fixtures, out_dir, groups, track=track, proj=self.proj)
            row["gi_out"], row["gp_out"], row["gs_out"] = gi, gp, gs
        pauses_raw = e._clip_sequential_pauses(clip, groups, group_pause)
        if single_raw > 1e-4:
            per_group = single_raw
        elif sdi > 1e-4 or sdo > 1e-4:
            per_group = max(sdi, sdo)
        else:
            per_group = max(fi, fo, dur)
        per_group = max(1e-6, float(per_group))
        row["per_group"] = per_group
        bo_a = np.asarray(e._clip_sequential_offsets(per_group, pauses_raw,
                                                     batch_count),
                          dtype=np.float32)
        row["bo_a"] = bo_a
        out_target = sdo if sdo > 1e-4 else fo
        out_span = max(1e-6, per_group * batch_count + float(sum(pauses_raw)))
        out_scale = max(1e-6, out_target / out_span)
        row["out_target"] = out_target
        row["out_span"] = out_span
        row["out_scale"] = out_scale
        row["out_per_group"] = max(1e-6, per_group * out_scale)
        row["bo_a_out"] = np.asarray(bo_a * out_scale, dtype=np.float32)
        row["gidx_c"] = e._sequential_group_indices_for_fixtures(
            clip, sorted_fixtures, groups, track=track, proj=self.proj,
            direction=in_dir)
        if ratio_in or ratio_out:
            defs_by_id = e._fixture_defs_index(self.proj)
            type_map = e._clip_fixture_type_index_map(sorted_fixtures, spans,
                                                      defs_by_id)
            present = set()
            for m in type_map:
                present |= set(m.keys())
            types_sorted = sorted(present)
            row["ntypes"] = len(types_sorted)
            if types_sorted:
                row["ratio_in"] = np.asarray([ratio_in.get(ct, 1.0)
                                              for ct in types_sorted], np.float64)
                row["ratio_out"] = np.asarray([ratio_out.get(ct, 1.0)
                                               for ct in types_sorted], np.float64)
                entries = []
                for fi_i, m in enumerate(type_map):
                    for ti, ct in enumerate(types_sorted):
                        for off in m.get(ct, []):
                            entries.append((fi_i, ti, int(off)))
                row["nentries"] = len(entries)
                if entries:
                    row["entries"] = np.asarray(entries, dtype=np.int32)
            else:
                row["nentries"] = 0

    def _build_path_b(self, row, e, clip, u, track, groups, group_pause,
                      single_raw, sdi, sdo, fi, fo, in_dir, out_dir):
        active_channels = e._get_clip_active_channels(clip, u, track=track)
        K = len(active_channels)
        row["K"] = K
        if K <= 0:
            return
        row["channels"] = np.asarray(active_channels, dtype=np.int32)
        pos_in = e._compute_direction_positions(in_dir, K)
        pos_out = e._compute_direction_positions(out_dir, K)
        row["pos_in"] = pos_in
        row["pos_out"] = pos_out
        gppos_in, step_in = _parallel_group_positions(pos_in, groups)
        gppos_out, step_out = _parallel_group_positions(pos_out, groups)
        row["gppos_in"], row["max_step_in"] = gppos_in, step_in
        row["gppos_out"], row["max_step_out"] = gppos_out, step_out
        gidx_i, gpos_i, gc_i, gsize_i = _sequential_group_positions(pos_in, groups)
        gidx_o, gpos_o, gc_o, gsize_o = _sequential_group_positions(pos_out, groups)
        row["gidx_in"], row["gpos_in"], row["gsize_in"] = gidx_i, gpos_i, gsize_i
        row["gidx_out"], row["gpos_out"], row["gsize_out"] = gidx_o, gpos_o, gsize_o
        bc_b = e._clip_sequential_batch_count(clip, gc_i)
        row["batch_count_b"] = bc_b
        pauses_b = e._clip_sequential_pauses(clip, gc_i, group_pause)
        row["bo_b_in"] = np.asarray(e._clip_sequential_offsets(
            row["pg_b_in"], pauses_b, bc_b), dtype=np.float32)
        pauses_b2 = e._clip_sequential_pauses(clip, gc_o, group_pause)
        row["bo_b_out"] = np.asarray(e._clip_sequential_offsets(
            row["pg_b_out"], pauses_b2, bc_b), dtype=np.float32)
        row["gidx_c"] = e._sequential_group_indices_for_positions(pos_in, groups)

    def _fill_remap(self, row, e, clip, u, track, compiled, target_universes,
                    seq_enabled, parallel_remap, in_dir, src_dur):
        """填充重映射缓存 + copy_rows/copy_f/slot 注册（BUG B 有效值）。"""
        if not ((seq_enabled or parallel_remap) and in_dir != "none"):
            return
        clip_id = str(getattr(clip, "id", "") or id(clip))
        compiled_by_universe = {}
        _fast_cache = e._clip_compiled_fast
        for _u2 in (target_universes or []):
            _uni2 = int(_u2)
            _fk2 = (id(clip), _uni2, id(track) if track is not None else None)
            _fast2 = _fast_cache.get(_fk2)
            if _fast2 is not None and _fast2[0] is clip and _fast2[1] is track:
                compiled_by_universe[_uni2] = _fast2[2]
            else:
                compiled_by_universe[_uni2] = e._compiled_clip_universe(
                    clip, _uni2, track=track, proj=self.proj)
        fallback = np.zeros(512, dtype=np.uint8)
        e._clip_sequential_remapped_source_frame(
            clip, 0.0, 0.0, src_dur, u, fallback, track=track, proj=self.proj,
            compiled=compiled,
            compiled_by_universe=compiled_by_universe,
            motor_phase_only=bool(parallel_remap))
        cached = e._clip_sequential_remap_cache.get(
            f"{clip_id}::seq_runtime::{int(u)}")
        if cached is None:
            return
        _sig, *rest = cached
        if len(rest) == 1:
            # 引擎早退分支 (:3091-3093, :3107-3109, :3131-3133) 存
            # (fast_sig, tuple()) 2 元组 — copy_map 空, 无重映射
            copy_map, remapped_source, seed_fallback = rest[0], False, False
        else:
            copy_map, remapped_source, seed_fallback = rest
        copy_map = list(copy_map or [])
        row["n_copy_rows"] = len(copy_map)
        row["remap_enabled"] = 1 if copy_map else 0
        row["remapped_source"] = 1 if (bool(remapped_source) and copy_map) else 0
        row["seed_fallback"] = 1 if seed_fallback else 0
        if not copy_map or self.ctx is None:
            return
        rows6 = []
        rows2 = []
        for r in copy_map:
            target_start, source_start, count, sample_universe, target_group, \
                rel_pos, group_size = r[:7]
            phase_source = int(bool(r[7])) if len(r) >= 8 else 0
            su = int(sample_universe)
            sample_compiled = compiled_by_universe.get(su)
            if sample_compiled is None:
                _fk3 = (id(clip), su, id(track) if track is not None else None)
                _fast3 = e._clip_compiled_fast.get(_fk3)
                if _fast3 is not None and _fast3[0] is clip and _fast3[1] is track:
                    sample_compiled = _fast3[2]
                else:
                    sample_compiled = e._compiled_clip_universe(
                        clip, su, track=track, proj=self.proj)
            slot = self.ctx.register_compiled(clip_id, su, sample_compiled)
            rows6.append((int(target_start), int(source_start), int(count),
                          slot, int(target_group), phase_source))
            rows2.append((float(rel_pos), float(group_size)))
        row["copy_rows"] = np.asarray(rows6, dtype=np.int32)
        row["copy_f"] = np.asarray(rows2, dtype=np.float32)


def timeline_range_enabled() -> bool:
    """QASCADE_CE_TIMELINE_RANGE 门控（默认 "0" 关闭; 变更区纪律: 不 flip）。"""
    v = os.environ.get("QASCADE_CE_TIMELINE_RANGE", "0").strip().lower()
    return v not in ("", "0", "false", "off", "no")


# render_frame 参数缓存哨兵（区分「未初始化」与「缓存了含 None 的合法结果」）
_ARGS_CACHE_UNSET = object()


def lt_enabled() -> bool:
    """QASCADE_CE_LT 门控（默认 "0" 关闭; 灯跟踪用户实测=唯一验证者, 不 flip）。

    调用侧在 render_frame(lt=...) 前按此门控决定是否构建 LtFrameBundle。
    """
    v = os.environ.get("QASCADE_CE_LT", "0").strip().lower()
    return v not in ("", "0", "false", "off", "no")


def tie_value(priority: float, track_order: float, start_t: float) -> float:
    """与 playback_engine.py :3909 逐字节同式的完整优先级 tie 值。"""
    return (float(priority) * 1e9
            + (float(track_order) + 1.0) * 1e-3
            + float(start_t) * 1e-4)


def k2_max_uni(proj, floor: int = 64) -> int:
    """K2 域容量 = max(floor, 项目最大配接域 + 1)。

    C 内核按 univ_off[u] / univ_out[组] 直接寻址且只查 u < 0（ce_engine.c
    组构建段）；容量须覆盖全部任务域, 越界域由 register_compiled 拒绝
    → 整段回退 Python 原路径。
    """
    top = 0
    for fx in (getattr(proj, "fixtures", None) or []):
        try:
            top = max(top, int(getattr(fx, "universe", 1) or 1))
        except (TypeError, ValueError):
            continue
    return max(int(floor), top + 1)


def clip_eligible_fast_path(clip, universe: int, engine) -> bool:
    """纯回放 Fast-Path 判定（fast_path_design.md 条件集 + K7/K7a 放宽; 只读）。

    条件: frames 非空 + run_mode normal + seq 关
      （seq_delay ≤1e-6 且 sequential_offset_enabled 关）+ motor 全关
      （_motor_force_profile 为 None）+ light_tracking_enabled ==False
      （引擎级）+ XY 参考表空。
    K7: 方向性淡入淡出不再排除 — 由 K7 方向内核（ce_dir_block）接管
      （_clip_channel_fade_gains :8144 全语义, 含 seq 组/延迟扫掠/重映射）;
    K2-2026-08-11: hold_mode 不再排除 — K2 任务表对 hold 扩展帧以
      fade_dur=0 编码（C 内核 _ce_clip_fade_gain64 dur<=0 → gain 恒 1.0 +
      source_t 天然钳制 src_dur-1e-4 = Python hold 帧 :1862-1868 同式）;
      clip 级 light_tracking_enabled 不再排除 — K2 LT 束接线（build_lt_bundle
      每帧装配 + ce_timeline_frame 串行 LT 阶段 fetch→LT→fade 时序同 Python
      :3821）。排除项仅剩真正非 Fast-Path 的: frames 空 / run_mode /
      clip.seq_delay / sequential_offset_enabled / motor force profile /
      引擎级 light_tracking / XY 参考表。
    """
    _uni = int(universe)
    _fk = (id(clip), _uni, None)
    _fast = engine._clip_compiled_fast.get(_fk)
    if _fast is not None and _fast[0] is clip and _fast[1] is None:
        compiled = _fast[2]
    else:
        compiled = engine._compiled_clip_universe(clip, _uni)
    if compiled.get("frame_times", np.zeros(0)).size <= 0:
        return False
    if str(getattr(clip, "run_mode", "normal") or "normal").lower() != "normal":
        return False
    if float(getattr(clip, "seq_delay", 0.0) or 0.0) > 1e-6:
        return False
    if bool(getattr(clip, "sequential_offset_enabled", False)):
        return False
    if engine._clip_motor_force_profile(clip) is not None:
        return False
    if bool(getattr(engine, "light_tracking_enabled", False)):
        return False
    # XY 参考表挂在 NetworkManager 上（playback_engine.py:2441 同源）。
    # ★ R56d A/B 实证此门必须保留（见 _bench_xy_elig_ab_20260828.py +
    #   _xyab_cmp.log）：移除后 K2 首次跑 示例工程，
    #   7149 帧中 4206 帧数据差 / 2886 帧 presence 差 / 4263 帧 active 差
    #   （横跨宇宙 1-28，非仅 xy 域）——K2 C 表对该工程内容与 Python 原版
    #   路径不等价（其门禁基准未覆盖此类 clip 内容；xy 工程此前被本门
    #   挡在 K2 外故从未暴露）。等价性修复前，快速但不同输出的路径
    #   不可上生产（灯光输出改变）。P0 已移交 CE 侧。
    _nm = getattr(engine, "_nm", None)
    if _nm is not None and len(getattr(_nm, "_xy_reference_by_universe", None) or {}) > 0:
        return False
    return not any((requires_python_audio_scope(engine), requires_python_clip_time(engine, clip)))


def _as_c_contig(arr: np.ndarray, dtype: np.dtype, name: str) -> np.ndarray:
    """ascontiguousarray + 值域零拷贝保证（模板 B: 零拷贝直传）。"""
    out = np.ascontiguousarray(arr, dtype=dtype)
    if not out.flags.c_contiguous:
        raise ValueError(f"{name} 非 C 连续")
    return out


class TimelineCtx:
    """离线时间线 K2 上下文: 槽注册 + CSR 任务表 + 每帧 1 次内核调用。"""

    def __init__(self, engine=None, max_uni: int = 64, max_slots: int = 0):
        self.engine = engine if engine is not None else load_or_none()
        self.max_uni = int(max_uni)
        self.available = False
        self._ctx = None
        self._slot_by_key: Dict[Tuple[str, int], int] = {}
        self._next_slot = 0
        self._key_by_slot: Dict[int, Tuple[str, int]] = {}
        # CSR 任务表（引用持有防 GC; build_tables 后可用）
        self.n_frames = None
        self.task_off = None
        self.rec_off = None
        self._slot = None          # [n] int32
        self._uni = None           # [n] int32
        self._start = None         # [n] f64
        self._rate = None          # [n] f64
        self._dur = None           # [n] f64
        self._tie = None           # [n] f64
        self._rec_slot = None
        self._rec_uni = None
        self._rec_start = None
        self._rec_rate = None
        self._rec_dur = None
        self._rec_tie = None
        # R56e: 逐帧精化 active 表池（code>=0 → pool[code] = n*512 uint8 表;
        # 表内存由 _refined_tables_gc 持有防 GC; ptr 存整数地址便于逐帧组装）
        self._refined_idx = None  # [n] int32 任务行池码（-1 = 用槽 base_active）
        self._refined_tables_gc: list = []
        self._refined_pool_ptrs: list = []
        self._refined_pool_arr = None
        self._refined_growable: list = []   # R56f: [base, [bool512 行]] 可增长表
        # K7a fade 平行表（[n_tasks]; 主表与 rec 表各一套; None = 无 fade → C 旧行为）
        self._fade_dur = None
        self._fade_in = None
        self._fade_out = None
        self._fade_te = None
        self._fade_curve_fi = None
        self._fade_curve_fo = None
        self._fade_strategy_fi = None
        self._fade_strategy_fo = None
        self._fade_anchor = None      # ctypes POINTER(c_float)×n（指针数组）
        self._fade_target = None
        self._rec_fade_dur = None
        self._rec_fade_in = None
        self._rec_fade_out = None
        self._rec_fade_te = None
        self._rec_fade_curve_fi = None
        self._rec_fade_curve_fo = None
        self._rec_fade_strategy_fi = None
        self._rec_fade_strategy_fo = None
        self._rec_fade_anchor = None
        self._rec_fade_target = None
        # 锚/目标数组持有（防 GC）; 按 id() 去重（同 (clip,u,track) 任务共享同一数组）
        self._fade_anchor_hold: Dict[int, np.ndarray] = {}
        self._fade_target_hold: Dict[int, np.ndarray] = {}
        # ★ 2026-08-11: render_frame 参数指针惰性缓存 — build_tables 后全部
        #   数组对象固定, 每帧 data_as/cast 纯重复（示例工程 段内 ~40 万次
        #   data_as + fade/dir 尾段重建）→ 首次 render 时构建一次。
        #   每次 _k2_build_tables 新建实例 → 缓存随实例生命周期, 天然安全。
        self._frame_ptr_cache = None
        # 哨兵值: fade/dir 表可能不存在（_fade_args 返回 (None,)*10）,
        # 不能用 "元组首元素 is None" 判定未初始化（None 也是合法缓存值）。
        self._fade_args_cache = _ARGS_CACHE_UNSET
        self._dir_args_cache = _ARGS_CACHE_UNSET
        # K7 dir 平行表（[n_tasks] 每任务 ce_dir_block* 或 NULL; None = 无方向任务）
        self._dir_ptrs = None
        self._rec_dir_ptrs = None
        self._dir_hold: List[ctypes.Structure] = []
        self._dir_arr_hold: Dict[int, np.ndarray] = {}
        # 输出缓冲（域主序 [max_uni*1024], 每域 out[512]+act[512];
        # _univ_act 为同一块内存的后半视图 → 各持有独立 ctypes 指针）
        # ★ 2026-08-11: 活跃域组装 — build_tables 后从任务表 _uni/_rec_uni
        #   惰性收集实际出现的域, 组装只遍历活跃域（示例工程 25 域 vs 64 槽,
        #   空槽 off=-1 纯空转; 覆盖全部任务域 → 输出逐字节不变）。
        self._active_unis: Optional[list] = None
        self._univ_out = np.zeros(self.max_uni * _UNI_SIZE * 2, dtype=np.uint8)
        self._univ_act = self._univ_out[self.max_uni * _UNI_SIZE:].view(np.uint8)
        self._univ_off = np.full(self.max_uni + 1, -1, dtype=np.int32)
        if self.engine is None:
            return
        fn = getattr(self.engine, "ce_timeline_frame", None)
        if fn is None or getattr(self.engine, "ce_clip_register", None) is None:
            return
        cap = max(4096, int(max_slots))
        ctx = self.engine.ce_ctx_create(cap)
        if not ctx:
            return
        self._ctx = ctx
        self.available = True

    # ── 槽注册 ──────────────────────────────────────────────────────────
    def register_compiled(self, clip_cache_id: str, universe: int,
                          compiled: dict) -> int:
        """(clip, universe) 编译产物 → ce_clip_register 槽（幂等; 重复注册覆盖）。"""
        if not 0 <= int(universe) < self.max_uni:
            raise ValueError(f"universe {universe} 超出 K2 容量 [0, {self.max_uni})")
        key = (str(clip_cache_id), int(universe))
        if key in self._slot_by_key:
            return self._slot_by_key[key]
        slot = self._next_slot
        self._next_slot += 1
        ft = _as_c_contig(compiled["frame_times"], np.float64, "frame_times")
        fd = _as_c_contig(compiled["frame_data"].reshape(-1), np.uint8, "frame_data")
        lc = compiled.get("last_change_time")
        lc_arr = None
        if lc is not None and lc.size > 0:
            lc_arr = _as_c_contig(lc.reshape(-1), np.float32, "last_change_time")
        ba = compiled.get("base_active")
        # ★ K6-2026-08-11: LT clip 灯通道靠 light_mask 才 active — Python
        #   渲染循环 :3885-3889 `clip_active | light_mask` 同式。C 注册侧
        #   只传 base_active → 灯通道不参与 resolve/mix → 被其它 clip 主导
        #   （示例工程 frame 5034 u=2/3 实测 diff, C 输出=非 LT clip 的 fade_out
        #   渐变）。OR 进注册 base_active 后, C 的 active 行与 mix 锚
        #   (e->anchor 注册期 ce_clip_anchor 输入) 均与 py 一致。
        lm = compiled.get("light_mask")
        if lm is not None:
            lma = np.asarray(lm, dtype=np.uint8)
            if np.any(lma):
                ba = np.asarray(ba, dtype=np.uint8) | lma
        ba_arr = None
        if ba is not None:
            ba_arr = _as_c_contig(np.asarray(ba, dtype=np.uint8).reshape(-1),
                                  np.uint8, "base_active")
        ra = compiled.get("rule_active")
        ra_arr = None
        has_rule = 0
        if ra is not None:
            ra_arr = _as_c_contig(np.asarray(ra, dtype=np.uint8).reshape(-1),
                                  np.uint8, "rule_active")
            has_rule = 1
        n = int(ft.shape[0])
        rc = self.engine.ce_clip_register(
            self._ctx, slot,
            ft.ctypes.data_as(_PC_D), n,
            fd.ctypes.data_as(_PC_U8),
            lc_arr.ctypes.data_as(_PC_F) if lc_arr is not None else None,
            ba_arr.ctypes.data_as(_PC_U8) if ba_arr is not None else None,
            has_rule,
            ra_arr.ctypes.data_as(_PC_U8) if ra_arr is not None else None,
        )
        if rc < 0:
            raise RuntimeError(f"ce_clip_register 失败: {clip_cache_id}::{universe}")
        # 引用持有: 注册复制进 C, numpy 数组可释放——仅保留 key 映射
        self._slot_by_key[key] = slot
        self._key_by_slot[slot] = key
        return slot

    def slot_of(self, clip_cache_id: str, universe: int) -> Optional[int]:
        return self._slot_by_key.get((str(clip_cache_id), int(universe)))

    def set_consumed(self, slot: int, mask: np.ndarray) -> None:
        """R56g: 推送接管消费掩码到内核槽（mask=None 清除）。"""
        if mask is None:
            self.engine.ce_clip_set_consumed(self._ctx, int(slot), None)
            return
        m = np.ascontiguousarray(np.asarray(mask, dtype=bool).reshape(-1).astype(np.uint8))
        self.engine.ce_clip_set_consumed(self._ctx, int(slot),
                                         m.ctypes.data_as(_PC_U8))

    def begin_refined_table(self) -> int:
        """R56f: 开一个可增长的精化表（同源 epoch 检测模式）。

        返回基码；行经 append_refined_row 逐 epoch 追加（builder 循环内实测
        最近帧切换 → 调引擎掩码函数算该 epoch 行——与渲染循环完全同源）。
        finalize_refined_tables 在 build_tables 末尾压实成连续 uint8 表并
        展开行指针。行 r 的池码 = 基码 + r。
        """
        base = len(self._refined_pool_ptrs)
        self._refined_growable.append([base, []])
        return base

    def append_refined_row(self, base: int, row: np.ndarray) -> None:
        """向基码 base 的可增长表追加一行（bool 512）。"""
        for ent in self._refined_growable:
            if ent[0] == base:
                ent[1].append(np.asarray(row, dtype=bool).reshape(-1).copy())
                return
        self._refined_growable.append([base, [np.asarray(row, dtype=bool).reshape(-1).copy()]])

    def refined_row_count(self, base: int) -> int:
        for ent in self._refined_growable:
            if ent[0] == base:
                return len(ent[1])
        return 0

    def finalize_refined_tables(self) -> None:
        """压实全部可增长表 → 连续 uint8 (m,512) 表 + 按行展开指针。"""
        for base, rows in self._refined_growable:
            if not rows:
                # 空表（从未触发 epoch）→ 挂一行全零防悬空（行码不应指向此处，
                # 防御性处理）
                rows.append(np.zeros(512, dtype=bool))
            t = np.ascontiguousarray(np.stack(rows).astype(np.uint8))
            self._refined_tables_gc.append(t)
            addr = int(t.ctypes.data)
            for r in range(t.shape[0]):
                self._refined_pool_ptrs.append(addr + r * 512)
        self._refined_growable = []

    def _refined_frame_args(self, frame_idx: int) -> tuple:
        """按帧组装 (task_refined 池码 ptr, refined_pool 表指针数组)。

        池指针数组惰性构建一次（全表地址, 与池码配合由内核二级索引）。
        无池 → (None, None) = 旧行为（槽 base_active）。"""
        if (self._refined_idx is None or not self._refined_pool_ptrs
                or self.task_off is None):
            return (None, None)
        if self._refined_pool_arr is None:
            pool_arr = (ctypes.c_void_p * len(self._refined_pool_ptrs))(
                *self._refined_pool_ptrs)
            self._refined_pool_arr = pool_arr
        t0 = int(self.task_off[frame_idx])
        t1 = int(self.task_off[frame_idx + 1])
        codes = self._refined_idx[t0:t1]
        # int32 切片是连续视图 → data_as 指向首元素 ✓
        return (codes.ctypes.data_as(_PC_I),
                ctypes.cast(self._refined_pool_arr,
                            ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8))))

    # ── CSR 任务表 ──────────────────────────────────────────────────────
    def build_tables(self, n_frames: int,
                     per_frame_tasks: Sequence[Sequence[Tuple[int, int, float, float, float, float]]],
                     record_tasks: Optional[Sequence[Sequence[Tuple[int, int, float, float, float, float]]]] = None,
                     per_frame_fades: Optional[Sequence[Sequence[tuple]]] = None,
                     record_frame_fades: Optional[Sequence[Sequence[tuple]]] = None,
                     per_frame_dirs: Optional[Sequence[Sequence[Optional[dict]]]] = None,
                     record_frame_dirs: Optional[Sequence[Sequence[Optional[dict]]]] = None,
                     ) -> None:
        """构建主表 + record 子集表（均 CSR: off[frame+1] + 全表任务序数组）。

        任务元组 = (slot, universe, start_t, rate, src_dur, tie)。
        K7a: per_frame_fades[i] 与 per_frame_tasks[i] 平行, 每行 fade 元组 =
          (fade_dur, fade_in, fade_out, te, curve_fi, curve_fo, strategy_fi,
           strategy_fo, anchor, target) — dur/fi/fo 为 f64 值(调用方已
           max(0,...) clamp), te 为 0/1, curve/strategy 为 int 索引(与 C
           _ce_transition_gain64 同表), anchor/target 为 512×f32 ndarray 或
           None(缺失 → C 按 fmode 0 处理, 同 Python prev/target None 守卫)。
        未传 fade 表 = 无 fade(→ C 传 NULL, 旧行为)。
        K7: per_frame_dirs[i] 与任务行平行, 每行 = dir 行 dict（键 = CeDirBlock
          字段名, 见 _serialize_dir_block）或 None（无方向 fade → C 旧行为）。
          None 数组 → 整表 NULL（旧行为）。dir 行在调用方按 (clip,u) 缓存,
          此处块实例按 id() 持有防 GC（_dir_hold/_dir_arr_hold）。
        """
        # ★ 2026-09-27 R173: 修复两个 P0 内存安全 bug:
        #   1. STALE CACHE: build_tables 重建数组后必须使指针缓存失效,
        #      否则 render_frame 会使用指向已释放内存的野指针。
        #   2. ID-REUSE: id() 键字典必须在重建时清空, 否则当旧数组释放、
        #      新数组复用相同 id 时会产生错误的缓存命中。
        self._fade_args_cache = _ARGS_CACHE_UNSET
        self._dir_args_cache = _ARGS_CACHE_UNSET
        self._fade_anchor_hold.clear()
        self._fade_target_hold.clear()
        self._dir_hold.clear()
        self._dir_arr_hold.clear()
        self._active_unis = None  # 依赖任务表, 同步失效

        per_frame_tasks = list(per_frame_tasks)
        if len(per_frame_tasks) != n_frames:
            raise ValueError(f"per_frame_tasks 长度 {len(per_frame_tasks)} != {n_frames}")
        self.n_frames = int(n_frames)
        self.task_off, self._slot, self._uni, self._start, self._rate, \
            self._dur, self._tie, self._refined_idx = \
            self._build_one_table(per_frame_tasks)
        if per_frame_fades is not None:
            (self._fade_dur, self._fade_in, self._fade_out,
             self._fade_te, self._fade_curve_fi, self._fade_curve_fo,
             self._fade_strategy_fi, self._fade_strategy_fo,
             self._fade_anchor, self._fade_target) = \
                self._build_fade_table(per_frame_fades)
        else:
            self._fade_dur = self._fade_in = self._fade_out = None
            self._fade_te = self._fade_curve_fi = self._fade_curve_fo = None
            self._fade_strategy_fi = self._fade_strategy_fo = None
            self._fade_anchor = self._fade_target = None
        if record_tasks is not None:
            self.rec_off, self._rec_slot, self._rec_uni, self._rec_start, \
                self._rec_rate, self._rec_dur, self._rec_tie, _rec_refined = \
                self._build_one_table(list(record_tasks))
            del _rec_refined   # record 表恒不取精化（send 侧语义, 保持原状）
        else:
            self.rec_off = np.zeros(n_frames + 1, dtype=np.int32)
            self._rec_slot = np.zeros(0, dtype=np.int32)
            self._rec_uni = np.zeros(0, dtype=np.int32)
            self._rec_start = np.zeros(0, dtype=np.float64)
            self._rec_rate = np.zeros(0, dtype=np.float64)
            self._rec_dur = np.zeros(0, dtype=np.float64)
            self._rec_tie = np.zeros(0, dtype=np.float64)
        if record_frame_fades is not None:
            (self._rec_fade_dur, self._rec_fade_in, self._rec_fade_out,
             self._rec_fade_te, self._rec_fade_curve_fi, self._rec_fade_curve_fo,
             self._rec_fade_strategy_fi, self._rec_fade_strategy_fo,
             self._rec_fade_anchor, self._rec_fade_target) = \
                self._build_fade_table(record_frame_fades)
        else:
            self._rec_fade_dur = self._rec_fade_in = self._rec_fade_out = None
            self._rec_fade_te = self._rec_fade_curve_fi = self._rec_fade_curve_fo = None
            self._rec_fade_strategy_fi = self._rec_fade_strategy_fo = None
            self._rec_fade_anchor = self._rec_fade_target = None
        # K7: dir 平行表（行数 = 任务数, 错配 = ValueError 显式失败）
        self._dir_ptrs = self._build_dir_table(per_frame_dirs,
                                               n_task=self._slot.size)
        self._rec_dir_ptrs = self._build_dir_table(
            record_frame_dirs,
            n_task=self._rec_slot.size if self.rec_off is not None else 0)

    @staticmethod
    def _build_one_table(per_frame_tasks):
        n_frames = len(per_frame_tasks)
        n = int(sum(len(r) for r in per_frame_tasks))
        off = np.zeros(n_frames + 1, dtype=np.int32)
        slot = np.zeros(n, dtype=np.int32)
        uni = np.zeros(n, dtype=np.int32)
        start = np.zeros(n, dtype=np.float64)
        rate = np.zeros(n, dtype=np.float64)
        dur = np.zeros(n, dtype=np.float64)
        tie = np.zeros(n, dtype=np.float64)
        # R56e: 第 7 元素 = 精化 active 表池码（-1 = 无/hold/排除 → 槽 base_active）
        refined = np.full(n, -1, dtype=np.int32)
        p = 0
        for i, rows in enumerate(per_frame_tasks):
            off[i] = p
            for r in rows:
                sl, u, st, rt, sd, tv = r[0], r[1], r[2], r[3], r[4], r[5]
                rv = int(r[6]) if len(r) > 6 else -1
                slot[p] = int(sl)
                uni[p] = int(u)
                start[p] = float(st)
                rate[p] = float(rt)
                dur[p] = float(sd)
                tie[p] = float(tv)
                refined[p] = rv
                p += 1
        off[n_frames] = p
        return off, slot, uni, start, rate, dur, tie, refined

    def _build_fade_table(self, per_frame_fades):
        """fade 平行表: 8 个 [n] 数组 + 2 个指针数组（anchor/target 512×f32）。

        anchor/target 元素为 None → 空指针; 数组对象按 id() 去重持有
        （_fade_anchor_hold/_fade_target_hold）防 GC —— 同一 (clip,u,track)
        任务共享同一数组对象, 指针数组只存指针, 否则每任务 2KB 级内存爆炸。
        """
        # ★ 2026-08-11: 原逐行 numpy 标量赋值（278503 行 × 8 ≈ 1.1s）→
        #   先展平再批量转换（np.array 从 list of tuples C 层批量, 快 ~10x）;
        #   anchor/target 指针按 id() 缓存（同一 (clip,u,track) 共享同一数组,
        #   data_as 结果恒同 → 数百次而非每行）。
        flat = [t for rows in per_frame_fades for t in rows]
        n = len(flat)
        fdur = np.zeros(n, dtype=np.float64)
        fin = np.zeros(n, dtype=np.float64)
        fout = np.zeros(n, dtype=np.float64)
        fte = np.zeros(n, dtype=np.int32)
        cf = np.zeros(n, dtype=np.int32)
        co = np.zeros(n, dtype=np.int32)
        sf = np.zeros(n, dtype=np.int32)
        so = np.zeros(n, dtype=np.int32)
        anchor_ptrs = (ctypes.POINTER(ctypes.c_float) * n)()
        target_ptrs = (ctypes.POINTER(ctypes.c_float) * n)()
        if n:
            fcols = np.array([(r[0], r[1], r[2]) for r in flat],
                             dtype=np.float64)
            fdur[:] = fcols[:, 0]
            fin[:] = fcols[:, 1]
            fout[:] = fcols[:, 2]
            icols = np.array([(r[3], r[4], r[5], r[6], r[7]) for r in flat],
                             dtype=np.int32)
            fte[:] = icols[:, 0]
            cf[:] = icols[:, 1]
            co[:] = icols[:, 2]
            sf[:] = icols[:, 3]
            so[:] = icols[:, 4]
        _anchor_ptr_cache = {}
        _target_ptr_cache = {}
        for p, r in enumerate(flat):
            anchor = r[8]
            if anchor is not None:
                k = id(anchor)
                ap = _anchor_ptr_cache.get(k)
                if ap is None:
                    self._fade_anchor_hold[k] = anchor
                    ap = anchor.ctypes.data_as(
                        ctypes.POINTER(ctypes.c_float))
                    _anchor_ptr_cache[k] = ap
                anchor_ptrs[p] = ap
            target = r[9]
            if target is not None:
                k = id(target)
                tp = _target_ptr_cache.get(k)
                if tp is None:
                    self._fade_target_hold[k] = target
                    tp = target.ctypes.data_as(
                        ctypes.POINTER(ctypes.c_float))
                    _target_ptr_cache[k] = tp
                target_ptrs[p] = tp
        return (fdur, fin, fout, fte, cf, co, sf, so, anchor_ptrs, target_ptrs)

    def _build_dir_table(self, per_frame_dirs, n_task: int):
        """dir 平行表 → (c_void_p × n_task) 指针数组（每任务块指针或 NULL）。

        行 dict 键 = CeDirBlock 字段名; None 行 → NULL（C 走旧行为）。
        返回 None = 整表 NULL（C 旧行为）。块实例/数组/指针数组均持有防 GC。
        行数 ≠ 任务数 → ValueError（防 C 侧越界读）。
        """
        if per_frame_dirs is None:
            return None
        n = int(sum(len(r) for r in per_frame_dirs))
        if n == 0:
            return None
        if n != int(n_task):
            raise ValueError(
                f"dir 表行数 {n} != 任务数 {int(n_task)}")
        ptrs = (ctypes.c_void_p * n)()
        # ★ 2026-08-11: 同一 row dict（DirRowBuilder 跨帧缓存对象）重复序列化
        #   → 28922 次 → 按 id(row) 缓存块（块只读, C 只读指针; _dir_hold 保活;
        #   本表构建期内有效, 每次 _build_dir_table 新建缓存）。
        _blk_cache = {}
        p = 0
        for rows in per_frame_dirs:
            for row in rows:
                if row is not None:
                    blk = _blk_cache.get(id(row))
                    if blk is None:
                        blk = self._serialize_dir_block(row)
                        _blk_cache[id(row)] = blk
                    self._dir_hold.append(blk)
                    ptrs[p] = ctypes.cast(ctypes.byref(blk), ctypes.c_void_p)
                p += 1
        return ptrs

    def _serialize_dir_block(self, row: dict) -> CeDirBlock:
        """dir 行 dict → CeDirBlock 实例（标量 setattr + 数组 data_as）。

        数组按 id() 持有于 _dir_arr_hold（C 只读指针, numpy 对象须保活）。
        缺失键 = 0/NULL（C 侧每字段有默认语义, 见内核空指针守卫）。
        """
        blk = CeDirBlock()
        for name, (ptype, dtype) in _DIR_PTR_FIELDS.items():
            arr = row.get(name)
            if arr is None:
                continue
            a = _as_c_contig(np.asarray(arr, dtype=dtype), dtype, name)
            self._dir_arr_hold[id(a)] = a
            setattr(blk, name, ctypes.cast(
                a.ctypes.data_as(ptype), ctypes.c_void_p))
        for name in _DIR_SCALAR_DOUBLE:
            v = row.get(name)
            if v is not None:
                setattr(blk, name, float(v))
        for name in _DIR_SCALAR_INT:
            v = row.get(name)
            if v is not None:
                setattr(blk, name, int(v))
        return blk

    # ── 每帧调用 ────────────────────────────────────────────────────────
    _NO_LT_ARGS = (None, None, None, None, None, None, None,
                   None, None, None, None, None, None, None, 0, 0)

    _PP_U8 = ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8))
    _PP_I = ctypes.POINTER(ctypes.POINTER(ctypes.c_int))
    _PP_D = ctypes.POINTER(ctypes.POINTER(ctypes.c_double))
    _PP_V = ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
    _PP_F = ctypes.POINTER(ctypes.POINTER(ctypes.c_float))

    def _fade_args(self, rec: bool) -> tuple:
        """K7a fade 尾段 10 参（无 fade 表 → 全 None = C 旧行为）。"""
        if rec:
            fd = self._rec_fade_dur
            fi, fo = self._rec_fade_in, self._rec_fade_out
            te = self._rec_fade_te
            cf, co = self._rec_fade_curve_fi, self._rec_fade_curve_fo
            sf, so = self._rec_fade_strategy_fi, self._rec_fade_strategy_fo
            ap, tp = self._rec_fade_anchor, self._rec_fade_target
        else:
            fd = self._fade_dur
            fi, fo = self._fade_in, self._fade_out
            te = self._fade_te
            cf, co = self._fade_curve_fi, self._fade_curve_fo
            sf, so = self._fade_strategy_fi, self._fade_strategy_fo
            ap, tp = self._fade_anchor, self._fade_target
        if fd is None or int(fd.size) == 0:
            return (None,) * 10
        return (
            fd.ctypes.data_as(_PC_D),
            fi.ctypes.data_as(_PC_D),
            fo.ctypes.data_as(_PC_D),
            te.ctypes.data_as(_PC_I),
            cf.ctypes.data_as(_PC_I),
            co.ctypes.data_as(_PC_I),
            sf.ctypes.data_as(_PC_I),
            so.ctypes.data_as(_PC_I),
            ctypes.cast(ap, TimelineCtx._PP_F),
            ctypes.cast(tp, TimelineCtx._PP_F),
        )

    def _dir_args(self, rec: bool) -> tuple:
        """K7 dir 尾段 1 参（无 dir 表 → (None,) = C 旧行为）。"""
        dp = self._rec_dir_ptrs if rec else self._dir_ptrs
        if dp is None:
            return (None,)
        return (ctypes.cast(dp, ctypes.POINTER(ctypes.c_void_p)),)

    @staticmethod
    def _lt_args(lt) -> tuple:
        """lt 束（lt_path.LtFrameBundle）→ ce_timeline_frame 尾段 16 参。

        None = 无 LT 任务（K2 行为, 字节不变）。每任务段指针数组
        （c_void_p×n_tasks）由 lt_path 构建并保活; 此处按绑定 argtypes
        逐类 cast（c_void_p 数组 → LP_LP_* 元素为指针数组的指针）。
        """
        if lt is None:
            return TimelineCtx._NO_LT_ARGS
        return (
            (lt.scope_off.ctypes.data_as(_PC_I) if lt.scope_off is not None else None),
            lt.scope_ptrs,
            ctypes.cast(lt.nm_ptrs, TimelineCtx._PP_U8),
            ctypes.cast(lt.fx_ptrs, TimelineCtx._PP_I),
            ctypes.cast(lt.cross_tbl_ptrs, TimelineCtx._PP_V),
            ctypes.cast(lt.cross_t0_ptrs, TimelineCtx._PP_D),
            ctypes.cast(lt.cross_live_ptrs, TimelineCtx._PP_I),
            lt.state.ctypes.data_as(_PC_D),
            lt.state_t.ctypes.data_as(_PC_D),
            lt.state_exists.ctypes.data_as(_PC_U8),
            lt.color.ctypes.data_as(_PC_U8),
            lt.color_valid.ctypes.data_as(_PC_U8),
            lt.touched.ctypes.data_as(_PC_U8),
            lt.touched_color.ctypes.data_as(_PC_U8),
            int(lt.n_state), int(lt.n_color),
        )

    def render_frame(self, frame_idx: int, pos: float, mod_gain: float = 1.0,
                     record: bool = False,
                     lt=None, dump=None) -> Optional[Dict[int, Tuple[np.ndarray, np.ndarray]]]:
        """dump=(out, act, sc) 预分配 numpy 数组（(n,512)）时转储任务行
        （接管消费标记用; R56g）。n 必须等于本帧任务数。"""
        """单帧: 1 次 ce_timeline_frame 调用 → {u: (data[512], active[512])}。

        返回 None = 内核拒绝（调用方回退原路径）; 空 dict = 本帧无输出域。
        record=True 用 record 子集表（独立 resolve 组）; record 表恒无 LT
        （Python 参考循环 record 输出 = 主渲染的投影, LT 只应用一次 — K6
        spec §4.3; LT 任务只在主表调用运行）。
        lt = LtFrameBundle（lt_path.py）或 None（无 LT 任务, K2 原行为）。
        QASCADE_CE_LT != 1 时（门控关）lt 束被忽略 → 恒 K2 行为（LT 任务
        当普通任务走 OpenMP 阶段; 调用方应自行按 lt_enabled() 决定构建束）。
        """
        if not self.available or self.task_off is None:
            return None
        if self.n_frames is not None and (frame_idx < 0 or frame_idx >= self.n_frames):
            raise ValueError(
                f"frame_idx {frame_idx} out of bounds [0, {self.n_frames})")
        if lt is not None and not lt_enabled():
            lt = None
        if self._active_unis is None:
            _uni_set: set = set()
            if self._uni is not None:
                _uni_set.update(int(x) for x in self._uni)
            if self._rec_uni is not None and self._rec_uni.size:
                _uni_set.update(int(x) for x in self._rec_uni)
            # Filter out universe numbers exceeding max_uni to prevent IndexError
            # when accessing _univ_off[u] (sized max_uni+1, valid indices 0..max_uni)
            _uni_set = {u for u in _uni_set if 0 <= u <= self.max_uni}
            self._active_unis = sorted(_uni_set)
            if not self._active_unis:
                self._active_unis = list(range(self.max_uni))
        fn = self.engine.ce_timeline_frame
        self._univ_off[:] = -1
        # 惰性参数缓存: 数组对象 build 后固定 → data_as/cast 结果一次构建
        pc = self._frame_ptr_cache
        if pc is None:
            pc = self._frame_ptr_cache = (
                self.task_off.ctypes.data_as(_PC_I),
                self._slot.ctypes.data_as(_PC_I),
                self._uni.ctypes.data_as(_PC_I),
                self._start.ctypes.data_as(_PC_D),
                self._rate.ctypes.data_as(_PC_D),
                self._dur.ctypes.data_as(_PC_D),
                self._tie.ctypes.data_as(_PC_D),
                self._univ_out.ctypes.data_as(_PC_U8),
                self._univ_act.ctypes.data_as(_PC_U8),
                self._univ_off.ctypes.data_as(_PC_I),
                (self.rec_off.ctypes.data_as(_PC_I)
                 if self.rec_off is not None else None),
                (self._rec_slot.ctypes.data_as(_PC_I)
                 if self._rec_slot is not None else None),
                (self._rec_uni.ctypes.data_as(_PC_I)
                 if self._rec_uni is not None else None),
                (self._rec_start.ctypes.data_as(_PC_D)
                 if self._rec_start is not None else None),
                (self._rec_rate.ctypes.data_as(_PC_D)
                 if self._rec_rate is not None else None),
                (self._rec_dur.ctypes.data_as(_PC_D)
                 if self._rec_dur is not None else None),
                (self._rec_tie.ctypes.data_as(_PC_D)
                 if self._rec_tie is not None else None),
            )
        if self._fade_args_cache is _ARGS_CACHE_UNSET:
            self._fade_args_cache = (self._fade_args(False),
                                     self._fade_args(True))
        if self._dir_args_cache is _ARGS_CACHE_UNSET:
            self._dir_args_cache = (self._dir_args(False),
                                    self._dir_args(True))
        if record:
            if self.rec_off is None:
                return {}
            rc = fn(self._ctx, pc[10],
                    int(frame_idx), float(pos),
                    pc[11], pc[12], pc[13], pc[14], pc[15], pc[16],
                    float(mod_gain),
                    pc[7], pc[8], pc[9],
                    *self._NO_LT_ARGS,
                    *self._fade_args_cache[1],
                    *self._dir_args_cache[1],
                    None, None, None, None, None)
        else:
            _ref_args = self._refined_frame_args(frame_idx)
            if dump is not None:
                _d_ptrs = (dump[0].ctypes.data_as(_PC_U8),
                           dump[1].ctypes.data_as(_PC_U8),
                           dump[2].ctypes.data_as(_PC_D))
            else:
                _d_ptrs = (None, None, None)
            rc = fn(self._ctx, pc[0],
                    int(frame_idx), float(pos),
                    pc[1], pc[2], pc[3], pc[4], pc[5], pc[6],
                    float(mod_gain),
                    pc[7], pc[8], pc[9],
                    *self._lt_args(lt),
                    *self._fade_args_cache[0],
                    *self._dir_args_cache[0],
                    *_ref_args, *_d_ptrs)
        if rc < 0:
            return None
        out: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}
        for u in self._active_unis:
            off = int(self._univ_off[u])
            if off < 0:
                continue
            data = self._univ_out[off:off + _UNI_SIZE].copy()
            act = self._univ_act[off:off + _UNI_SIZE]
            out[int(u)] = (data, act.astype(bool))
        return out

    def render_range(self, n_frames: int, interval: float, end: float,
                     mod_gain: float = 1.0, record: bool = False):
        """帧区间: pos = fmin(end, idx*interval)（Python :667-668 同式）。

        返回 (frame_cache, record_cache): frame_cache[idx] = {u: (data, act)}
        空帧 = {}; record_cache 仅存 any(active) 域非空的帧（record 表调用）。
        """
        frame_cache: Dict[int, dict] = {}
        record_cache: Dict[int, dict] = {}
        for idx in range(int(n_frames)):
            pos = min(float(end), float(idx) * float(interval))
            res = self.render_frame(idx, pos, mod_gain=mod_gain, record=False)
            if res is None:
                return None, None
            frame_cache[int(idx)] = res
            if record and self.rec_off is not None:
                rres = self.render_frame(idx, pos, mod_gain=mod_gain, record=True)
                if rres is None:
                    return None, None
                if rres:
                    record_cache[int(idx)] = rres
        return frame_cache, record_cache

    def destroy(self) -> None:
        """释放 C 上下文资源（幂等，可安全重复调用）。"""
        if self._ctx is not None and self.engine is not None:
            try:
                self.engine.ce_ctx_destroy(self._ctx)
            except Exception:
                pass
        self._ctx = None
        self.available = False

    def __del__(self) -> None:
        """析构时自动释放 C 资源（防泄漏）。"""
        self.destroy()

    def __enter__(self):
        """支持 with 语句的上下文管理器协议。"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文时自动释放资源。"""
        self.destroy()
        return False
