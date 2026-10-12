# -*- coding: utf-8 -*-
"""§8.2 首期内核能力表种子 — 5 内核的 CPU/GPU 实现包装 + 注册。

独立于 scheduler.py 核心（探测/路由/遥测）存放实现包装, 保持各文件
≤500 行。每个包装统一签名 fn(args: dict) -> int（0 成功 / 负值错误）;
args 为关键字参数字典, 输出缓冲由调用方预分配（同 ctypes_api 纪律）。

位级能力: mix 族 bit_exact=True（要求设备 CE_GPU_CAP_EXACT_MIX）;
wave/chase 族 bit_exact=False（GPU 超越函数 ≤1 LSB, §6.2 容差层）;
full_mix/mix_gains 当前 GPU 无实现 → 恒 CPU（缺实现 = 自动回退, 不静默）。
"""
from __future__ import annotations

import ctypes

import numpy as np

from .ctypes_api import load_engine
from . import scheduler


def _wrap_mix_clips_cpu(engine):
    """ce_gpu_mix_clips CPU 实现: 每域一次 ce_mix_clips（U=1 同路径）。

    args: vals/acts/anchors [U][lmax][512], nlays[U], lmax, mod_gain,
    out/act_any [U][512]。"""
    _F = ctypes.POINTER(ctypes.c_float)
    _U = ctypes.POINTER(ctypes.c_uint8)
    fn = getattr(engine, "ce_mix_clips", None)

    def run(args: dict) -> int:
        if fn is None:
            return -1
        vals, acts, anchors = args["vals"], args["acts"], args["anchors"]
        nlays = args["nlays"]
        out, act_any = args["out"], args["act_any"]
        mg = float(args["mod_gain"])
        for u in range(int(len(nlays))):
            nlay = int(nlays[u])
            fn(np.ascontiguousarray(vals[u][:nlay], np.float32).ctypes.data_as(_F),
               np.ascontiguousarray(acts[u][:nlay], np.uint8).ctypes.data_as(_U),
               np.ascontiguousarray(anchors[u][:nlay], np.float32).ctypes.data_as(_F),
               nlay, mg,
               np.ascontiguousarray(out[u], np.uint8).ctypes.data_as(_U),
               np.ascontiguousarray(act_any[u], np.uint8).ctypes.data_as(_U))
        return 0
    return run


def _wrap_mix_clips_gpu():
    """ce_gpu_mix_clips GPU 实现: 域×512 通道批（ce_gpu_mix_batch）。"""

    def run(args: dict) -> int:
        if scheduler._gpu_dev is None:
            return -2   # 设备丢失
        return scheduler._gpu_dev.mix_batch(args)
    return run


def _wrap_wave_cpu(engine):
    """ce_gpu_wave CPU 实现: ce_wave_effect（单任务, n 设备）。

    args: t/speed/phase_step/lo/hi/wave_fn/n + out[n]。"""
    _U = ctypes.POINTER(ctypes.c_uint8)
    fn = getattr(engine, "ce_wave_effect", None)

    def run(args: dict) -> int:
        if fn is None:
            return -1
        fn(float(args["t"]), float(args["speed"]), float(args["phase_step"]),
           float(args["lo"]), float(args["hi"]), int(args["wave_fn"]),
           int(args["n"]), np.ascontiguousarray(args["out"], np.uint8).ctypes.data_as(_U))
        return 0
    return run


def _wrap_wave_gpu():
    """ce_gpu_wave GPU 实现: 每 (task, device) 一个 work-item（ce_gpu_wave_batch）。"""

    def run(args: dict) -> int:
        if scheduler._gpu_dev is None:
            return -2
        return scheduler._gpu_dev.wave_batch(args)
    return run


def _wrap_full_mix_cpu(engine):
    """ce_gpu_full_mix CPU 实现: ce_full_mix（K2 全链路混合, 逐域调用）。

    args: base/shape/clip data+active 层列表（u8[512] 或 None）, ltp_time
    (f64[512] 或 None, ≤13), defaults/default_active (u8[512] 或 None),
    has_defaults, shape_gain, direct_anchor (f32[512] 或 None),
    clip_t/editor_t (f64[512] 或 None), out (u8[512])。"""
    fn = getattr(engine, "ce_full_mix", None)

    def run(args: dict) -> int:
        if fn is None:
            return -1
        C = ctypes
        _F8 = ctypes.POINTER(ctypes.c_uint8)
        _FD = ctypes.POINTER(ctypes.c_double)
        _FF = ctypes.POINTER(ctypes.c_float)

        def layer_list(key, n):
            items = args.get(key) or []
            arr = (_F8 * n)()
            for i in range(n):
                a = items[i] if i < len(items) else None
                arr[i] = (np.ascontiguousarray(a, np.uint8).ctypes.data_as(_F8)
                          if a is not None else None)
            return C.cast(arr, C.POINTER(_F8))
        ltp = args.get("ltp_time") or []
        ltp_arr = (_FD * 13)()
        for i in range(13):
            t = ltp[i] if i < len(ltp) else None
            ltp_arr[i] = (np.ascontiguousarray(t, np.float64).ctypes.data_as(_FD)
                          if t is not None else None)
        defaults = args.get("defaults")
        def_active = args.get("default_active")
        anchor = args.get("direct_anchor")
        clip_t = args.get("clip_t")
        editor_t = args.get("editor_t")
        fn(layer_list("base_data", 4), layer_list("base_active", 4),
           layer_list("shape_data", 6), layer_list("shape_active", 6),
           layer_list("clip_data", 3), layer_list("clip_active", 3),
           C.cast(ltp_arr, C.POINTER(_FD)), 13,
           (np.ascontiguousarray(defaults, np.uint8).ctypes.data_as(_F8)
            if defaults is not None else None),
           (np.ascontiguousarray(def_active, np.uint8).ctypes.data_as(_F8)
            if def_active is not None else None),
           int(args.get("has_defaults", 0)),
           float(args.get("shape_gain", 1.0)),
           (np.ascontiguousarray(anchor, np.float32).ctypes.data_as(_FF)
            if anchor is not None else None),
           (np.ascontiguousarray(clip_t, np.float64).ctypes.data_as(_FD)
            if clip_t is not None else None),
           (np.ascontiguousarray(editor_t, np.float64).ctypes.data_as(_FD)
            if editor_t is not None else None),
           np.ascontiguousarray(args["out"], np.uint8).ctypes.data_as(_F8))
        return 0
    return run


def _wrap_chase_cpu(engine):
    """ce_gpu_chase CPU 实现: ce_chase_effect（单任务, n 设备）。

    args: t/speed/rate/delay/mode/n/lo/hi/width/tail/n_colors/density/duty/
    bpm/overlap + out/out2/out3 (u8[n])。"""
    _U = ctypes.POINTER(ctypes.c_uint8)
    fn = getattr(engine, "ce_chase_effect", None)

    def run(args: dict) -> int:
        if fn is None:
            return -1
        g = lambda k, d: args.get(k, d)  # noqa: E731
        fn(float(g("t", 0.0)), float(g("speed", 0.0)),
           float(g("rate", 0.0)), float(g("delay", 0.0)),
           int(g("mode", 0)), int(args["n"]),
           int(g("lo", 0)), int(g("hi", 255)),
           int(g("width", 0)), int(g("tail", 0)), int(g("n_colors", 0)),
           float(g("density", 0.0)), float(g("duty", 0.0)),
           float(g("bpm", 0.0)), int(g("overlap", 0)),
           np.ascontiguousarray(args["out"], np.uint8).ctypes.data_as(_U),
           np.ascontiguousarray(args["out2"], np.uint8).ctypes.data_as(_U),
           np.ascontiguousarray(args["out3"], np.uint8).ctypes.data_as(_U))
        return 0
    return run


def _wrap_gains_cpu(engine):
    """ce_gpu_mix_gains CPU 实现: ce_transition_gains（过渡曲线, n 系数）。

    args: tt (f32[n]), n, fade_in, curve, strategy + out (f32[n])。"""
    _F = ctypes.POINTER(ctypes.c_float)
    fn = getattr(engine, "ce_transition_gains", None)

    def run(args: dict) -> int:
        if fn is None:
            return -1
        fn(np.ascontiguousarray(args["tt"], np.float32).ctypes.data_as(_F),
           int(args["n"]), int(args.get("fade_in", 1)),
           int(args.get("curve", 0)), int(args.get("strategy", 0)),
           np.ascontiguousarray(args["out"], np.float32).ctypes.data_as(_F))
        return 0
    return run


def seed_kernel_table(engine=None) -> list:
    """§8.2 首期 5 内核能力表种子（CPU 实现来自 engine 的 C 内核）。

    未提供 engine 或符号缺失 → 该实现 = 缺失 → schedule 自动回退语义
    （cpu_fn 缺失且 GPU 不可用 → 返回负值错误, 不假死）。
    """
    if engine is None:
        engine = load_engine()
    scheduler.register_kernel("ce_gpu_full_mix",
                              cpu_fn=_wrap_full_mix_cpu(engine), bit_exact=True)
    scheduler.register_kernel("ce_gpu_mix_clips",
                              cpu_fn=_wrap_mix_clips_cpu(engine),
                              opencl=_wrap_mix_clips_gpu(),
                              cuda=_wrap_mix_clips_gpu(), bit_exact=True)
    scheduler.register_kernel("ce_gpu_wave", cpu_fn=_wrap_wave_cpu(engine),
                              opencl=_wrap_wave_gpu(), cuda=_wrap_wave_gpu(),
                              bit_exact=False)
    scheduler.register_kernel("ce_gpu_chase", cpu_fn=_wrap_chase_cpu(engine),
                              bit_exact=False)
    scheduler.register_kernel("ce_gpu_mix_gains",
                              cpu_fn=_wrap_gains_cpu(engine), bit_exact=True)
    return list(scheduler._REGISTRY)
