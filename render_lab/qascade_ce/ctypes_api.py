"""Qascade CE ctypes 桥 — DLL 加载（cpuid 选变体）+ 函数指针预取 + argtypes。

热路径纪律：所有函数指针模块级预取、argtypes 预注册；缓冲由调用方预分配，
本模块不持有缓冲。DLL 加载失败/变体不兼容 → 返回 None，调用方静默回退原路径。
M3（HOOK_AUDIT 待办#1）: 全部绑定经 _bind 可选化——任一符号缺失 = None 而非
整引擎假死；缺失列表在 __init__ 末尾审计并写入 warning（含重建提示）。
"""
from __future__ import annotations

import ctypes
import logging
import os

log = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))

# P2a 导出名映射（语义名→DLL 实际导出名）。
# 顶层 import（mix-str/bcc 兼容——函数体内延迟相对导入才是坑）；
# 工件缺失/未改名 DLL → 恒等回退。
try:
    from .ce_api_map import CE_API_MAP as _CE_API_MAP
except Exception:
    _CE_API_MAP = {}

# 已尝试绑定的属性名注册表（M3 审计: 缺失符号 → warning 列全名, 不再整引擎假死）
_KNOWN_SYMBOLS: set = set()
_UNSET = object()


class CE_Route_Train(ctypes.Structure):
    """Route Runner 火车参数 (与 ce_engine.c 的 ce_route_train 布局逐字节一致)。

    契约见 ai-memory/_audit_r1/_route_runner_spec.md §CE 内核契约 ce_route_coverage。
    """
    _fields_ = [
        ("path_id", ctypes.c_int),
        ("nose_s", ctypes.c_double),
        ("car_unit", ctypes.c_double),
        ("car_count", ctypes.c_int),
        ("radius", ctypes.c_double),
    ]


def _bind(dll, name, argtypes=_UNSET, restype=_UNSET, attr=None):
    """可选绑定: 符号缺失 → None（使用该内核的 hook 在调用点判 None 回退原版）。

    M3 修复（HOOK_AUDIT 待办#1）: 旧实现 `self.ce_x = dll.ce_x` 任一符号缺失
    → AttributeError → load_or_none 吞掉 → 全部 hook 静默回退 Python（无诊断）。
    现与 ce_mix_clips_avx2 同款约定——缺失符号 = None; __init__ 末尾审计把
    缺失列表写进 warning。

    P2a 导出名混淆: DLL 实际导出为不透明名，先经 CE_API_MAP 翻译再绑定；
    翻译失败回退语义名（兼容未改名 DLL——dev/历史工件双轨）。
    """
    _KNOWN_SYMBOLS.add(attr if attr is not None else name)
    real = _CE_API_MAP.get(name, name)
    fn = getattr(dll, real, None) if real != name else None
    if fn is None:
        fn = getattr(dll, name, None)
    if fn is not None:
        if argtypes is not _UNSET:
            fn.argtypes = argtypes
        if restype is not _UNSET:
            fn.restype = restype
    return fn


def apply_semantic_aliases(dll):
    """把语义名属性别名挂回 dll 对象（P2a 导出混淆后兼容层）。

    DLL 实际导出为不透明名；经 engine.dll 直连 `dll.ce_xxx` 的消费端
    （__init__ 的 XML hook 段等）零改动兼容。幂等；对不含这些导出的
    DLL（GPU 家族）逐项 AttributeError 静默跳过，无副作用。
    """
    for _sem, _opq in _CE_API_MAP.items():
        try:
            if _sem not in dll.__dict__:
                setattr(dll, _sem, getattr(dll, _opq))
        except AttributeError:
            pass
    return dll


class CEEngine:
    """已加载的 CE DLL 句柄与函数指针。"""

    def __init__(self, dll: ctypes.CDLL, variant: str):
        apply_semantic_aliases(dll)
        self.dll = dll
        self.variant = variant
        # ── 函数指针预取 ─────────────────────────────────────────
        self.version = _bind(dll, "ce_version", restype=ctypes.c_char_p, attr="version")
        self.cpu_has_avx2 = _bind(dll, "ce_cpu_has_avx2", restype=ctypes.c_int, attr="cpu_has_avx2")
        self.cpu_cores = _bind(dll, "ce_cpu_cores", restype=ctypes.c_int, attr="cpu_cores")
        # ── K0: 帧合成 ────────────────────────────────────────────
        self.ce_mix_clips = _bind(dll, "ce_mix_clips", [
            ctypes.POINTER(ctypes.c_float),   # vals (n,512) f32
            ctypes.POINTER(ctypes.c_uint8),   # acts (n,512) u8 0/1
            ctypes.POINTER(ctypes.c_float),   # anchors (n,512) f32
            ctypes.c_int,                     # nlay
            ctypes.c_float,                   # mod_gain
            ctypes.POINTER(ctypes.c_uint8),   # out [512]
            ctypes.POINTER(ctypes.c_uint8),   # act_any [512]
        ], None)
        # ── P2: AVX2 并行 SIMD 变体（仅 avx2 DLL 导出）──────────────
        self.ce_mix_clips_avx2 = _bind(
            dll, "ce_mix_clips_avx2",
            (self.ce_mix_clips.argtypes
             if self.ce_mix_clips is not None else _UNSET),
            None)
        # ── P2: 批量帧合成（OpenMP 按 universe 并行）───────────────
        self.ce_mix_clips_batch = _bind(dll, "ce_mix_clips_batch", [
            ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),   # vals[] 指针数组
            ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),   # acts[] 指针数组
            ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),   # anchors[] 指针数组
            ctypes.POINTER(ctypes.c_int),                     # nlays[n_univ]
            ctypes.c_int,                                     # n_univ
            ctypes.c_float,                                   # mod_gain
            ctypes.POINTER(ctypes.c_uint8),                   # out [n_univ*512]
            ctypes.POINTER(ctypes.c_uint8),                   # act_any [n_univ*512]
        ], None)
        # ── P4b: 帧准备大融合 ───────────────────────────────────────
        self.ce_render_clip_frame = _bind(dll, "ce_render_clip_frame", [
            ctypes.POINTER(ctypes.c_double),   # frame_times [n] f64 已排序
            ctypes.c_int,                      # n_frames
            ctypes.POINTER(ctypes.c_uint8),    # frame_data [n,512] u8
            ctypes.POINTER(ctypes.c_float),    # last_change [n,512] f32 或 NULL
            ctypes.c_double,                   # source_t
            ctypes.c_double,                   # clip_start
            ctypes.c_double,                   # rate
            ctypes.c_double,                   # timeline_now
            ctypes.c_int,                      # hold_extension 0/1
            ctypes.c_float,                    # gain
            ctypes.POINTER(ctypes.c_uint8),    # base_active [512]
            ctypes.c_int,                      # fade_mode 0/1/2
            ctypes.POINTER(ctypes.c_float),    # prev [512] 或 NULL
            ctypes.POINTER(ctypes.c_float),    # target [512] 或 NULL
            ctypes.POINTER(ctypes.c_uint8),    # out [512]
            ctypes.POINTER(ctypes.c_uint8),    # active_out [512]
            ctypes.POINTER(ctypes.c_double),   # scores [512]
        ], ctypes.c_int)
        # ── P5d: 批量帧准备（C 内 OpenMP 按任务并行）──────────────────
        _FP = ctypes.POINTER(ctypes.c_float)
        _DP = ctypes.POINTER(ctypes.c_double)
        _UP = ctypes.POINTER(ctypes.c_uint8)
        self.ce_render_clip_frame_batch = _bind(dll, "ce_render_clip_frame_batch", [
            ctypes.POINTER(_DP), ctypes.POINTER(ctypes.c_int),   # frame_times[], n_frames[]
            ctypes.POINTER(_UP), ctypes.POINTER(_FP),            # frame_data[], last_change[]
            ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double),  # source_t[], clip_start[]
            ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double),  # rate[], timeline_now[]
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_float),      # hold_ext[], gain[]
            ctypes.POINTER(_UP), ctypes.POINTER(ctypes.c_int),   # base_active[], fade_mode[]
            ctypes.POINTER(_FP), ctypes.POINTER(_FP),            # prev[], target[]
            ctypes.c_int,                                        # n_tasks
            ctypes.POINTER(_UP), ctypes.POINTER(_UP), ctypes.POINTER(_DP),  # out[], active_out[], scores[]
            ctypes.POINTER(ctypes.c_int),                        # out_idx[]
        ], None)
        # ── P6: C 持久上下文（数据所有权在 C）────────────────────────
        self.ce_ctx_create = _bind(dll, "ce_ctx_create", [ctypes.c_int], ctypes.c_void_p)
        self.ce_ctx_destroy = _bind(dll, "ce_ctx_destroy", [ctypes.c_void_p], None)
        # ── M2: 槽位复用（ce_clip_unregister 释放槽数据; ce_ctx_cap 容量）──
        self.ce_clip_unregister = _bind(dll, "ce_clip_unregister",
                                        [ctypes.c_void_p, ctypes.c_int], ctypes.c_int)
        self.ce_ctx_cap = _bind(dll, "ce_ctx_cap", [ctypes.c_void_p], ctypes.c_int)
        self.ce_clip_register = _bind(dll, "ce_clip_register", [
            ctypes.c_void_p, ctypes.c_int,          # ctx, idx
            ctypes.POINTER(ctypes.c_double), ctypes.c_int,  # frame_times, n
            ctypes.POINTER(ctypes.c_uint8),          # frame_data
            ctypes.POINTER(ctypes.c_float),          # last_change
            ctypes.POINTER(ctypes.c_uint8),          # base_active
            ctypes.c_int,                            # has_rule
            ctypes.POINTER(ctypes.c_uint8),          # rule_active
        ], ctypes.c_int)
        self.ce_clip_register_fade = _bind(dll, "ce_clip_register_fade", [
            ctypes.c_void_p, ctypes.c_int,          # ctx, idx
            ctypes.POINTER(ctypes.c_float),          # prev [512] 或 NULL
            ctypes.POINTER(ctypes.c_float),          # target [512] 或 NULL
        ], ctypes.c_int)
        self.ce_render_frame = _bind(dll, "ce_render_frame", [
            ctypes.c_void_p,                         # ctx
            ctypes.POINTER(ctypes.c_int),            # clip_idx[n]
            ctypes.POINTER(ctypes.c_int),            # universe[n]
            ctypes.POINTER(ctypes.c_double),         # source_t[n]
            ctypes.POINTER(ctypes.c_double),         # start_t[n]
            ctypes.POINTER(ctypes.c_double),         # rate[n]
            ctypes.POINTER(ctypes.c_double),         # timeline_now[n]
            ctypes.POINTER(ctypes.c_int),            # hold[n]
            ctypes.POINTER(ctypes.c_float),          # gain[n]
            ctypes.POINTER(ctypes.c_int),            # fade_mode[n]
            ctypes.c_int,                            # n_tasks
            ctypes.POINTER(ctypes.c_int),            # out_idx[n]
            ctypes.POINTER(ctypes.c_uint8),          # out[n*512]
            ctypes.POINTER(ctypes.c_uint8),          # act[n*512]
            ctypes.POINTER(ctypes.c_double),         # scores[n*512]
        ], None)
        # ── P8: Win32 持久线程池（自建 worker, 可控 spin, 会话级）────
        # 池 API 通用（任务 = fn+arg; 单提交者; tasks 数组在 submit 返回
        # 前不得复用）; ce_render_frame_parallel 与 ce_render_frame 同签名,
        # 内部用池并行 146 任务（每任务独立输出缓冲 → 位级等价）。
        self.ce_pool_create = _bind(dll, "ce_pool_create",
                                    [ctypes.c_int], ctypes.c_void_p)
        self.ce_pool_destroy = _bind(dll, "ce_pool_destroy",
                                     [ctypes.c_void_p], None)

        # ★ 2026-08-05 阶段B: condvar 常驻池契约(§4.3)替换旧 spin 池 —
        #   submit 签名变更为 (pool, fn, arg); 新增 submit_batch/wait/selftest。
        _CE_TASK_FN = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int)
        self.ce_pool_submit = _bind(dll, "ce_pool_submit", [
            ctypes.c_void_p,                     # pool
            _CE_TASK_FN,                         # fn
            ctypes.c_void_p,                     # arg
        ], ctypes.c_int)
        self.ce_pool_submit_batch = _bind(dll, "ce_pool_submit_batch", [
            ctypes.c_void_p,                     # pool
            ctypes.POINTER(_CE_TASK_FN),         # fns[n]
            ctypes.POINTER(ctypes.c_void_p),     # args[n]
            ctypes.c_int,                        # n
        ], ctypes.c_int)
        self.ce_pool_wait = _bind(dll, "ce_pool_wait",
                                  [ctypes.c_void_p], None)
        self.ce_pool_selftest = _bind(dll, "ce_pool_selftest",
                                      [], ctypes.c_int)
        self.ce_render_frame_parallel = _bind(
            dll, "ce_render_frame_parallel",
            (self.ce_render_frame.argtypes
             if self.ce_render_frame is not None else _UNSET),
            None)
        # ── A3: 全融合帧内核（消费+解析+混合+打包 一次 C 调用）──────
        # 输入同 ce_render_frame + ties[n]（任务序 priority tie）;
        # 输出: univ_out/univ_act 域主序连续缓冲（每域 out[512]+act[512]
        #   相邻, 组序 = universe 首现序）; univ_off[max_u+1] 调用方预置
        #   -1, 返回后 univ_off[u] = 域 u 字节偏移或 -1（无输出）。
        # 返回: 有输出域数（<0 = 内核拒绝, 调用方回退旧链）。
        self.ce_render_frame_fused = _bind(dll, "ce_render_frame_fused", [
            ctypes.c_void_p,                         # ctx
            ctypes.POINTER(ctypes.c_int),            # clip_idx[n]
            ctypes.POINTER(ctypes.c_int),            # universe[n]
            ctypes.POINTER(ctypes.c_double),         # source_t[n]
            ctypes.POINTER(ctypes.c_double),         # start_t[n]
            ctypes.POINTER(ctypes.c_double),         # rate[n]
            ctypes.POINTER(ctypes.c_double),         # timeline_now[n]
            ctypes.POINTER(ctypes.c_int),            # hold[n]
            ctypes.POINTER(ctypes.c_float),          # gain[n]
            ctypes.POINTER(ctypes.c_int),            # fade_mode[n]
            ctypes.c_int,                            # n_tasks
            ctypes.POINTER(ctypes.c_double),         # ties[n]（NULL=全 0）
            ctypes.c_double,                         # mod_gain
            ctypes.POINTER(ctypes.c_uint8),          # univ_out [n*1024]
            ctypes.POINTER(ctypes.c_uint8),          # univ_act [n*1024]
            ctypes.POINTER(ctypes.c_int),            # univ_off [max_u+1] 初值 -1
        ], ctypes.c_int)
        # ── K2: 离线时间线整帧融合（每帧 1 次调用; 纯回放 Fast-Path 条件）
        # 输入: task_off[frame+1] CSR 偏移（任务区间）+ 全表任务序数组
        #   [n_tasks]; pos = fmin(end, idx*interval) 由 Python 预算传入;
        #   tie[n] 为完整优先级值（priority*1e9+(track_order+1)*1e-3
        #   +start_t*1e-4, 调用方预算, 与 Python :3909 逐字节同式）。
        # 输出: univ_out/univ_act 域主序 [max_u*1024]（每域 out[512]+act[512]）;
        #   univ_off[max_u+1] 调用方预置 -1 → 返回后字节偏移或 -1。
        # 返回: 有输出域数; <0 = 内核拒绝 → 调用方回退原路径。
        # K6: lt_scope_off == NULL → 全部非 LT 任务（行为同旧）; 否则每任务
        #   lt_scope_off[t] >= 0 → scope 句柄, 走 ce_render_clip_frame_lt。
        #   每任务指针数组（n_cross==0 的任务段可传 NULL）; lt_state/lt_color
        #   引擎级槽（wrapper 按 lt_touched 增量回写 dict）。
        _PP_U8 = ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8))
        _PP_I = ctypes.POINTER(ctypes.POINTER(ctypes.c_int))
        _PP_D = ctypes.POINTER(ctypes.POINTER(ctypes.c_double))
        _PP_V = ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        _PP_F = ctypes.POINTER(ctypes.POINTER(ctypes.c_float))
        self.ce_timeline_frame = _bind(dll, "ce_timeline_frame", [
            ctypes.c_void_p,                         # ctx
            ctypes.POINTER(ctypes.c_int),            # task_off[frame+1]
            ctypes.c_int,                            # frame_idx
            ctypes.c_double,                         # pos
            ctypes.POINTER(ctypes.c_int),            # clip_slot[n]
            ctypes.POINTER(ctypes.c_int),            # universe[n]
            ctypes.POINTER(ctypes.c_double),         # start_t[n]
            ctypes.POINTER(ctypes.c_double),         # rate[n]
            ctypes.POINTER(ctypes.c_double),         # src_dur[n]
            ctypes.POINTER(ctypes.c_double),         # tie[n]（NULL=全 0）
            ctypes.c_double,                         # mod_gain
            ctypes.POINTER(ctypes.c_uint8),          # univ_out [max_u*1024]
            ctypes.POINTER(ctypes.c_uint8),          # univ_act [max_u*1024]
            ctypes.POINTER(ctypes.c_int),            # univ_off [max_u+1] 初值 -1
            # ── K6: 灯跟踪参数（lt_scope_off 可 NULL = 无 LT 任务）──
            ctypes.POINTER(ctypes.c_int),            # lt_scope_off[n]（-1=无）
            ctypes.POINTER(ctypes.c_void_p),         # lt_scope[n_scopes]
            _PP_U8,                                  # lt_nm_rgb[n]（每任务段）
            _PP_I,                                   # lt_fx_live[n]
            _PP_V,                                   # lt_cross_tbl[n]
            _PP_D,                                   # lt_cross_t0[n]
            _PP_I,                                   # lt_cross_live[n]
            ctypes.POINTER(ctypes.c_double),         # lt_state[n_state]
            ctypes.POINTER(ctypes.c_double),         # lt_state_t[n_state]
            ctypes.POINTER(ctypes.c_uint8),          # lt_state_exists[n_state]
            ctypes.POINTER(ctypes.c_uint8),          # lt_color[3*n_color]
            ctypes.POINTER(ctypes.c_uint8),          # lt_color_valid[n_color]
            ctypes.POINTER(ctypes.c_uint8),          # lt_touched[n_state]
            ctypes.POINTER(ctypes.c_uint8),          # lt_touched_color[n_color]
            ctypes.c_int,                            # n_lt_state
            ctypes.c_int,                            # n_lt_color
            # ── K7a: fade 任务参数（fade_dur == NULL → 无 fade, 行为同旧）──
            ctypes.POINTER(ctypes.c_double),         # fade_dur[n]（max(0,duration)）
            ctypes.POINTER(ctypes.c_double),         # fade_in[n]（max(0,原值)）
            ctypes.POINTER(ctypes.c_double),         # fade_out[n]
            ctypes.POINTER(ctypes.c_int),            # fade_te[n]（过渡启用=1）
            ctypes.POINTER(ctypes.c_int),            # fade_curve_fi[n]
            ctypes.POINTER(ctypes.c_int),            # fade_curve_fo[n]
            ctypes.POINTER(ctypes.c_int),            # fade_strategy_fi[n]
            ctypes.POINTER(ctypes.c_int),            # fade_strategy_fo[n]
            _PP_F,                                   # fade_anchor[n]（512×f32 每任务）
            _PP_F,                                   # fade_target[n]
            # ── K7: 方向 fade 任务参数（dir_blocks == NULL → 无方向任务）──
            ctypes.POINTER(ctypes.c_void_p),         # dir_blocks[n]（ce_dir_block*）
            # ── R56e: 逐帧精化 active（双 NULL = 全部用槽 base_active 旧行为）──
            ctypes.POINTER(ctypes.c_int),            # task_refined[ti]（-1=无）
            ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),  # refined_pool[code]
            # ── R56g: 任务行转储（接管消费标记；NULL=不转储）──
            ctypes.POINTER(ctypes.c_uint8),          # dump_out [n*512]
            ctypes.POINTER(ctypes.c_uint8),          # dump_act [n*512]
            ctypes.POINTER(ctypes.c_double),         # dump_sc  [n*512]
        ], ctypes.c_int)
        # R56g: 接管消费掩码（槽级; mask=NULL 清除）
        self.ce_clip_set_consumed = _bind(dll, "ce_clip_set_consumed", [
            ctypes.c_void_p, ctypes.c_int,          # ctx, slot
            ctypes.POINTER(ctypes.c_uint8),          # mask [512] 或 NULL
        ], ctypes.c_int)
        # ── K5: 灯跟踪单任务内核（每 (clip, universe) 一次调用）──────────
        # scope = lt_scope 扁平块（K4 编译）; raw[512] → out[512]（=raw 拷贝
        #   + LT 改写）; nm_rgb [3*(n_fx+n_pumps)] / fx_live [n_fx+n_pumps] /
        #   cross_tbl/t0/live [n_cross] 每帧预算; state/state_t/state_exists
        #   [n_state]、color/color_valid [n_color] 引擎级 in/out; touched/
        #   touched_color 本调用写入位（wrapper 增量回写 dict）。
        # 返回: 1 = 有修改（found_source）; 0 = 未修改（调用方回 raw）。
        self.ce_lt_apply = _bind(dll, "ce_lt_apply", [
            ctypes.c_void_p,                         # scope 扁平块
            ctypes.POINTER(ctypes.c_uint8),          # raw [512]
            ctypes.POINTER(ctypes.c_uint8),          # out [512]
            ctypes.c_double,                         # local_t
            ctypes.POINTER(ctypes.c_uint8),          # nm_rgb 或 NULL
            ctypes.POINTER(ctypes.c_int),            # fx_live 或 NULL
            ctypes.POINTER(ctypes.c_void_p),         # cross_tbl[n_cross] 或 NULL
            ctypes.POINTER(ctypes.c_double),         # cross_t0[n_cross] 或 NULL
            ctypes.POINTER(ctypes.c_int),            # cross_live[n_cross] 或 NULL
            ctypes.POINTER(ctypes.c_double),         # state[n_state]
            ctypes.POINTER(ctypes.c_double),         # state_t[n_state]
            ctypes.POINTER(ctypes.c_uint8),          # state_exists[n_state]
            ctypes.POINTER(ctypes.c_uint8),          # color[3*n_color]
            ctypes.POINTER(ctypes.c_uint8),          # color_valid[n_color]
            ctypes.POINTER(ctypes.c_uint8),          # touched[n_state]
            ctypes.POINTER(ctypes.c_uint8),          # touched_color[n_color]
            ctypes.c_int,                            # n_state
            ctypes.c_int,                            # n_color
        ], ctypes.c_int)
        # ── P9: C 侧发送环（stage C 流水线重叠; 常驻发送线程的字节通道）──
        _U16P = ctypes.POINTER(ctypes.c_uint16)
        self.ce_send_ring_create = _bind(dll, "ce_send_ring_create",
                                         [], ctypes.c_void_p)
        self.ce_send_ring_destroy = _bind(dll, "ce_send_ring_destroy",
                                          [ctypes.c_void_p], None)
        self.ce_send_ring_push = _bind(dll, "ce_send_ring_push", [
            ctypes.c_void_p, ctypes.c_uint16,
            ctypes.POINTER(ctypes.c_uint8),   # data [512]
        ], ctypes.c_int)
        self.ce_send_ring_pop = _bind(dll, "ce_send_ring_pop", [
            ctypes.c_void_p,
            _U16P,                            # out_uni
            ctypes.POINTER(ctypes.c_uint8),   # out_data [512]
        ], ctypes.c_int)
        self.ce_send_ring_pending = _bind(dll, "ce_send_ring_pending",
                                          [ctypes.c_void_p], ctypes.c_int)
        self.ce_send_ring_stop = _bind(dll, "ce_send_ring_stop",
                                       [ctypes.c_void_p], ctypes.c_int)
        # ── P5a-1: 过渡曲线 ─────────────────────────────────────────
        self.ce_transition_gains = _bind(dll, "ce_transition_gains", [
            ctypes.POINTER(ctypes.c_float),   # tt [n] f32
            ctypes.c_int,                     # n
            ctypes.c_int,                     # fade_in 0/1
            ctypes.c_int,                     # curve 0-4
            ctypes.c_int,                     # strategy 0-6
            ctypes.POINTER(ctypes.c_float),   # out [n]
        ], None)
        # ── P5a-2: 平行分组逐设备增益 ───────────────────────────────
        self.ce_parallel_fixture_gains = _bind(dll, "ce_parallel_fixture_gains", [
            ctypes.POINTER(ctypes.c_float),   # pos_in [K] 或 NULL
            ctypes.POINTER(ctypes.c_float),   # sizes_in [K] 或 NULL
            ctypes.POINTER(ctypes.c_float),   # pos_out [K] 或 NULL
            ctypes.POINTER(ctypes.c_float),   # sizes_out [K] 或 NULL
            ctypes.c_int,                     # K
            ctypes.c_double, ctypes.c_double,   # fade_in/out（f64, 同原版标量）
            ctypes.c_double, ctypes.c_double,   # duration, local_t
            ctypes.c_int, ctypes.c_int,       # is_fading_in/out
            ctypes.c_double, ctypes.c_double,   # seq_delay_in/out
            ctypes.c_double, ctypes.c_double,   # fade_in/out_scale
            ctypes.c_int, ctypes.c_int,       # curve, strategy
            ctypes.POINTER(ctypes.c_float),   # gains [K]
        ], None)
        # ── P5b: 顺序分组逐设备增益 ─────────────────────────────────
        self.ce_sequential_fixture_gains = _bind(dll, "ce_sequential_fixture_gains", [
            ctypes.POINTER(ctypes.c_float),   # gi_in [K] 或 NULL
            ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float),  # gp_in, gs_in
            ctypes.POINTER(ctypes.c_float),   # gi_out 或 NULL
            ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float),  # gp_out, gs_out
            ctypes.POINTER(ctypes.c_float),   # batch_offsets [batch_count]
            ctypes.c_int, ctypes.c_int,       # batch_count, K
            ctypes.c_double, ctypes.c_double, ctypes.c_double,  # per_group, global_gain, out_span
            ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double,  # fade_in/out, duration, local_t
            ctypes.c_int, ctypes.c_int,       # is_fading_in/out
            ctypes.c_double, ctypes.c_double,   # seq_delay_in/out
            ctypes.c_double, ctypes.c_double,   # fade_in/out_scale
            ctypes.c_int, ctypes.c_int,       # curve, strategy
            ctypes.POINTER(ctypes.c_float),   # gains [K]
        ], None)
        # ── T3: K2 全链路混合 ───────────────────────────────────────
        _P8 = ctypes.POINTER(ctypes.c_uint8)
        _PD = ctypes.POINTER(ctypes.c_double)
        self.ce_full_mix = _bind(dll, "ce_full_mix", [
            ctypes.POINTER(_P8), ctypes.POINTER(_P8),   # base_data[4], base_active[4]
            ctypes.POINTER(_P8), ctypes.POINTER(_P8),   # shape_data[6], shape_active[6]
            ctypes.POINTER(_P8), ctypes.POINTER(_P8),   # clip_data[3], clip_active[3]
            ctypes.POINTER(_PD), ctypes.c_int,          # ltp_time[13], n_ltp_layers
            ctypes.POINTER(ctypes.c_uint8), ctypes.POINTER(ctypes.c_uint8), ctypes.c_int,  # defaults, default_active, has_defaults
            ctypes.c_float, ctypes.POINTER(ctypes.c_float),  # shape_gain, direct_anchor
            ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double),  # clip_t, editor_t
            ctypes.POINTER(ctypes.c_uint8),             # out[512]
        ], None)
        # ── T4: K1 波形求值 ─────────────────────────────────────────
        self.ce_wave_effect = _bind(dll, "ce_wave_effect", [
            ctypes.c_double, ctypes.c_double, ctypes.c_double,  # t, speed, phase_step
            ctypes.c_double, ctypes.c_double,                   # lo, hi
            ctypes.c_int, ctypes.c_int,                         # wave_fn_idx, n
            ctypes.POINTER(ctypes.c_uint8),                     # out[n]
        ], None)
        # Cold numerical table; nonnegative result carries cast warning bits.
        self.ce_cold_clip_change_times_v1 = _bind(dll, "ce_cold_clip_change_times_v1", [
            _P8, ctypes.POINTER(ctypes.c_double), _P8,
            ctypes.c_int64, ctypes.c_int64, ctypes.c_int64, ctypes.c_int64,
            _P8, ctypes.c_int64, ctypes.POINTER(ctypes.c_float), ctypes.c_int64,
        ], ctypes.c_int)
        # ── T5: Fader 预设逐设备求值 ──────────────────────────────────
        self.ce_fader_effect = _bind(dll, "ce_fader_effect", [
            ctypes.c_double,                     # t
            ctypes.c_int,                        # kind 0=linear 1=sequential 2=breath 3=curve
            ctypes.c_int,                        # curve_idx 0-5（仅 kind=3）
            ctypes.c_double, ctypes.c_double,    # val_a, val_b（breath 中 = min/max）
            ctypes.c_double, ctypes.c_double,    # duration, delay_per
            ctypes.c_double, ctypes.c_double,    # fade_time, speed
            ctypes.c_int,                        # n
            ctypes.POINTER(ctypes.c_uint8),      # out[n]
        ], None)
        # ── T4b: K1 Joystick 类别求值 ─────────────────────────────────
        self.ce_joystick_effect = _bind(dll, "ce_joystick_effect", [
            ctypes.c_double, ctypes.c_double, ctypes.c_double,  # t, speed, phase_step
            ctypes.c_int,                                       # mode 0=circle 1=figure8 2=scan 3=wave_scan
            ctypes.POINTER(ctypes.c_double),                    # p[8] 参数槽（见 ce_engine.c）
            ctypes.c_int,                                       # n
            ctypes.POINTER(ctypes.c_uint8),                     # out_x [n]
            ctypes.POINTER(ctypes.c_uint8),                     # out_y [n]
        ], None)
        # ── T4c: K1 Audio 类别求值 ───────────────────────────────────
        self.ce_audio_effect = _bind(dll, "ce_audio_effect", [
            ctypes.c_double, ctypes.c_int,                      # t, mode 0=flash 1=chase 2=wave 3=energy 4=color_beat 5=section
            ctypes.POINTER(ctypes.c_double),                    # p[8] 参数槽（见 ce_engine.c）
            ctypes.c_int,                                       # n
            ctypes.POINTER(ctypes.c_uint8),                     # out [n]; mode4 时 [3n] = R,G,B 三联
        ], None)
        # ── T4 续: Audio 反应波形（bpm_wave/energy/beat_fader 三种节拍包络）──
        self.ce_audio_wave_effect = _bind(dll, "ce_audio_wave_effect", [
            ctypes.c_double,                     # t
            ctypes.c_double,                     # bpm
            ctypes.c_int,                        # kind 0=bpm_wave 1=energy 2=beat_fader
            ctypes.c_double, ctypes.c_double,    # phase_step, offset_per
            ctypes.c_double,                     # decay
            ctypes.c_double, ctypes.c_double,    # lo, hi
            ctypes.c_int,                        # n
            ctypes.POINTER(ctypes.c_uint8),      # out[n]
        ], None)
        # ── T4续: chase 效果族 ───────────────────────────────────────
        self.ce_chase_effect = _bind(dll, "ce_chase_effect", [
            ctypes.c_double, ctypes.c_double, ctypes.c_double, ctypes.c_double,
            # t, speed, rate, delay
            ctypes.c_int, ctypes.c_int,             # mode, n
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            # lo, hi, width, tail, n_colors
            ctypes.c_double, ctypes.c_double, ctypes.c_double,  # density, duty, bpm
            ctypes.c_int,                           # overlap
            ctypes.POINTER(ctypes.c_uint8),         # out[n]
            ctypes.POINTER(ctypes.c_uint8),         # out2[n] (color G / motor_y)
            ctypes.POINTER(ctypes.c_uint8),         # out3[n] (color B)
        ], None)
        # ── T3b: K2 批量并行 ────────────────────────────────────────
        _P8 = ctypes.POINTER(ctypes.c_uint8)
        _PD = ctypes.POINTER(ctypes.c_double)
        self.ce_full_mix_batch = _bind(dll, "ce_full_mix_batch", [
            _P8, _P8, _P8, _P8, _P8, _P8,      # base/shape/clip data+active [n*L*512]
            _PD, ctypes.c_int,                  # ltp_time [n*13*512], n
            ctypes.c_float, ctypes.POINTER(ctypes.c_float),  # shape_gain, direct_anchor [n*512]
            _P8, _P8,                           # defaults, default_active [n*512] 或 NULL
            _PD, _PD,                           # clip_t, editor_t [n*512] 或 NULL
            ctypes.c_int, ctypes.c_int,         # has_clip_t, has_editor_t
            _P8,                                # out [n*512]
        ], None)
        # ── T5b: flush 批量混合（指针数组 + OpenMP 按域并行）──────────────
        self.ce_full_mix_many = _bind(dll, "ce_full_mix_many", [
            ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
            # base_data[4n], base_active[4n] 域行主序指针数组
            ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
            # shape_data[6n], shape_active[6n]
            ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
            # clip_data[3n], clip_active[3n]
            ctypes.POINTER(ctypes.POINTER(ctypes.c_double)),
            # ltp_time[13n] 或 NULL
            ctypes.c_int,                       # n
            ctypes.c_float,                     # shape_gain
            ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
            # defaults[n], default_active[n] 或 NULL
            ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),
            # direct_anchor[n] 或 NULL
            ctypes.POINTER(ctypes.POINTER(ctypes.c_double)),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_double)),
            # clip_t[n], editor_t[n] 或 NULL
            ctypes.POINTER(ctypes.c_uint8),     # out [n*512]
        ], None)
        # ── T5: 候选解析 ────────────────────────────────────────────
        self.ce_resolve_candidates = _bind(dll, "ce_resolve_candidates", [
            ctypes.c_int,                     # n 候选层数
            _P8,                              # data  [n*512] u8
            _P8,                              # active [n*512] u8 0/1
            _PD,                              # scores [n*512] f64
            _P8,                              # effective [n*512] u8 输出
            _P8,                              # data_eff [n*512] u8 输出
            ctypes.POINTER(ctypes.c_int),     # effective_counts [n] 输出
            _PD,                              # best_out [512] f64 或 None
        ], None)
        # ── 烘焙批量候选解析（一帧全部域一次调用; 逐域与标量版逐位一致）───
        self.ce_resolve_batch = _bind(dll, "ce_resolve_batch", [
            ctypes.POINTER(ctypes.c_int),     # starts [n_univ] 每域起始行
            ctypes.POINTER(ctypes.c_int),     # counts [n_univ] 每域层数
            ctypes.c_int,                     # n_univ
            _PD,                              # ties [total] 每行 priority tie
            _P8, _P8, _PD,                    # data/active/scores [total*512]
            _P8, _P8,                         # effective/data_eff 输出
            ctypes.POINTER(ctypes.c_int),     # effective_counts [total] 输出
        ], None)
        # ── T5-9: 帧级融合候选解析（P6 批量路径消费循环+候选解析一次调用;
        #     act'=act&rule, sc=sc0+tie, best/eff/de/count 全 C 内; fuzz
        #     ce_fuzz_resolve_many.py 10 万+ 次位级一致）──────────────
        self.ce_resolve_candidates_many = _bind(dll, "ce_resolve_candidates_many", [
            ctypes.c_int,                     # n_groups
            ctypes.POINTER(ctypes.c_int),     # group_off [n_groups+1] 组前缀和
            ctypes.POINTER(ctypes.c_int),     # flat_idx [n_total] 组序任务→池行号
            _P8, _P8, _PD,                    # data/act/scores 批量池 [n_batch*512]
            ctypes.POINTER(ctypes.c_double),  # ties [n_total] 或 NULL
            ctypes.POINTER(_P8),              # rules [n_total] 或 NULL（元素可 NULL）
            _P8, _P8,                         # effective/data_eff [n_total*512] 输出
            ctypes.POINTER(ctypes.c_int),     # counts [n_total] 输出
        ], None)
        # ── 烘焙 u8 层数据混合（vals 直读 u8; 位级 = astype→f32 路径）──────
        self.ce_mix_clips_u8 = _bind(dll, "ce_mix_clips_u8", [
            _P8,                              # vals [n*512] u8
            _P8,                              # acts [n*512] u8 0/1
            ctypes.POINTER(ctypes.c_float),   # anchors [n*512] f32
            ctypes.c_int,                     # nlay
            ctypes.c_float,                   # mod_gain
            _P8, _P8,                         # out / act_any [512]
        ], None)
        self.ce_mix_clips_u8_batch = _bind(dll, "ce_mix_clips_u8_batch", [
            ctypes.POINTER(_P8),              # vals[] u8 指针数组
            ctypes.POINTER(_P8),              # acts[] u8 指针数组
            ctypes.POINTER(ctypes.POINTER(ctypes.c_float)),  # anchors[] f32
            ctypes.POINTER(ctypes.c_int),     # nlays[n_univ]
            ctypes.c_int,                     # n_univ
            ctypes.c_float,                   # mod_gain
            _P8, _P8,                         # out / act_any [n_univ*512]
        ], None)
        # ── T5: matrix 效果逐设备求值 ────────────────────────────────────
        self.ce_matrix_effect = _bind(dll, "ce_matrix_effect", [
            ctypes.c_int,                      # kind 0-9
            ctypes.c_double, ctypes.c_double, ctypes.c_double,  # t, speed, rate
            ctypes.c_int, ctypes.c_int, ctypes.c_int,          # cols, lo, hi
            ctypes.c_int,                                      # n
            ctypes.POINTER(ctypes.c_uint8),                    # cells 平铺图案或 NULL
            ctypes.c_int, ctypes.c_int, ctypes.c_int,          # pat_rows, pat_cols, flat_len
            ctypes.POINTER(ctypes.c_uint8),                    # cells2 alternate 第二图案或 NULL
            ctypes.c_int,                                      # flat_len2
            ctypes.POINTER(ctypes.c_uint8),                    # out [n]
        ], None)
        # ── T6: 泵预充电调度纯数学（对齐 src/engine/pump_precharge.py）─────
        self.ce_precharge_value = _bind(dll, "ce_precharge_value", [ctypes.c_double], ctypes.c_int)
        self.ce_precharge_channel = _bind(dll, "ce_precharge_channel", [ctypes.c_double], ctypes.c_int)
        self.ce_precharge_lead_s = _bind(dll, "ce_precharge_lead_s", [ctypes.c_double], ctypes.c_double)
        self.ce_precharge_trigger_position = _bind(dll, "ce_precharge_trigger_position", [
            ctypes.c_double, ctypes.c_double,   # start_time, lead_s
        ], ctypes.c_double)
        self.ce_precharge_should_fire = _bind(dll, "ce_precharge_should_fire", [
            ctypes.c_double, ctypes.c_double, ctypes.c_double,
            # now, trigger_position, clip_start
        ], ctypes.c_int)
        self.ce_precharge_build_frame = _bind(dll, "ce_precharge_build_frame", [
            ctypes.POINTER(ctypes.c_uint8),   # snapshot [512] 或 NULL
            ctypes.c_int,                     # snapshot_valid 0/1
            ctypes.c_int,                     # channel 0-511
            ctypes.c_int,                     # level 0-255
            ctypes.POINTER(ctypes.c_uint8),   # out [512]
        ], None)
        # ── T6: compute() 批量序列求值 ────────────────────────────────────
        self.ce_compute_sequence = _bind(dll, "ce_compute_sequence", [
            ctypes.c_double, ctypes.c_double, ctypes.c_double,  # fps, speed, phase_step
            ctypes.c_double, ctypes.c_double,                   # lo, hi（int() 截断后）
            ctypes.c_int,                                       # wave_fn_idx 0-7
            ctypes.c_int, ctypes.c_int,                         # n_frames, n_fixtures
            ctypes.POINTER(ctypes.c_uint8),                     # out [n_frames*n_fixtures]
        ], None)
        # ── T6b: compute() 批量序列求值 OpenMP 帧并行版（离线批量, 同签名）──
        self.ce_compute_sequence_omp = _bind(
            dll, "ce_compute_sequence_omp",
            (self.ce_compute_sequence.argtypes
             if self.ce_compute_sequence is not None else _UNSET),
            None)
        # ── K3: 离线烘焙 — OpenMP 并行版 + GPU 分派预留 ────────────────
        _BAKE_FN = ctypes.CFUNCTYPE(
            ctypes.c_int,                       # 返回 1=已烘焙 0=未处理
            ctypes.c_double, ctypes.c_double, ctypes.c_double,  # fps, speed, phase_step
            ctypes.c_double, ctypes.c_double,                   # lo, hi
            ctypes.c_int,                                       # wave_fn_idx
            ctypes.c_int, ctypes.c_int,                         # n_frames, n_fixtures
            ctypes.POINTER(ctypes.c_uint8),                     # out [n_frames*n_fixtures]
        )
        self.ce_gpu_bake_sequence_register = _bind(
            dll, "ce_gpu_bake_sequence_register", [_BAKE_FN], _BAKE_FN)
        self.ce_bake_sequence_omp = _bind(dll, "ce_bake_sequence_omp", [
            ctypes.c_double, ctypes.c_double, ctypes.c_double,  # fps, speed, phase_step
            ctypes.c_double, ctypes.c_double,                   # lo, hi（int() 截断后）
            ctypes.c_int,                                       # wave_fn_idx 0-7
            ctypes.c_int, ctypes.c_int,                         # n_frames, n_fixtures
            ctypes.POINTER(ctypes.c_uint8),                     # out [n_frames*n_fixtures]
            ctypes.c_int,                                       # n_threads (<=1=串行)
            ctypes.c_int,                                       # chunk_size (<=0=1000)
        ], None)
        # ── T5: clip 锚点 ─────────────────────────────────────────────
        self.ce_clip_anchor = _bind(dll, "ce_clip_anchor", [
            _P8,                              # frames [n_frames*512] u8
            ctypes.c_int,                     # n_frames（universe 过滤后）
            _P8,                              # ch_active [512] u8 0/1
            ctypes.POINTER(ctypes.c_float),   # anchor [512] f32 输出
        ], None)
        # ── T6: 效果引擎设备排序（sort_fixtures 纯数学内核）────────────────
        self.ce_sort_fixtures = _bind(dll, "ce_sort_fixtures", [
            ctypes.POINTER(ctypes.c_double),   # xs[n]（缺失坐标调用方补 0.0）
            ctypes.POINTER(ctypes.c_double),   # ys[n]
            ctypes.POINTER(ctypes.c_int64),    # base_rank[n]（rank 或 -1=未收录）
            ctypes.c_int,                      # n_base_rank（len(base_rank) dict）
            ctypes.c_int,                      # n
            ctypes.c_int,                      # mode 0=index 1=linear_x 2=linear_y
                                               #       3=radial 4=grid_row 5=grid_col
            ctypes.POINTER(ctypes.c_int32),    # out[n] 排序后原索引
        ], None)
        # ── T5: phase_spread 相位铺开（O(N²) 时间相位层）─────────────────
        self.ce_phase_spread = _bind(dll, "ce_phase_spread", [
            ctypes.c_double, ctypes.c_double,   # effect_t, phase_spread
            ctypes.c_double, ctypes.c_double,   # speed, phase_step
            ctypes.c_double, ctypes.c_double,   # lo, hi
            ctypes.c_int, ctypes.c_int,         # wave_fn_idx, n
            _P8,                                # out[n]
        ], None)
        # ── T5b: phase_spread 逐块（per_shape 时钟同步）──────────────────
        self.ce_phase_spread_blocks = _bind(dll, "ce_phase_spread_blocks", [
            ctypes.c_double, ctypes.c_double,   # effect_t, phase_spread
            ctypes.c_double, ctypes.c_double,   # speed, phase_step
            ctypes.c_double, ctypes.c_double,   # lo, hi
            ctypes.c_int, ctypes.c_int,         # wave_fn_idx, n
            ctypes.POINTER(ctypes.c_int32),     # within[n]
            ctypes.POINTER(ctypes.c_int32),     # m_i[n]
            _P8,                                # out[n]
        ], None)
        # ── T5: dragon 空间相位家族（reverse/gradient 龙摆）──────────────
        self.ce_dragon_phase = _bind(dll, "ce_dragon_phase", [
            ctypes.c_double, ctypes.c_double,   # t, speed
            ctypes.c_double, ctypes.c_double,   # phase_spread, amplitude
            ctypes.c_int, ctypes.c_int,         # reverse, gradient
            ctypes.c_int,                       # n
            _P8,                                # out[n]
        ], None)
        # ── T5: 音频分析层 ─────────────────────────────────────────
        self.ce_audio_frame_energy = _bind(dll, "ce_audio_frame_energy", [
            ctypes.POINTER(ctypes.c_float),   # buf [n] f32 采样
            ctypes.c_int,                     # n
            ctypes.c_double,                  # gain（Python float）
            ctypes.POINTER(ctypes.c_double),  # out_rms（f32 值加宽为 f64）
            ctypes.POINTER(ctypes.c_double),  # out_peak
        ], None)
        self.ce_audio_spectral_flatness = _bind(dll, "ce_audio_spectral_flatness", [
            ctypes.POINTER(ctypes.c_double),  # bands [n] f64
            ctypes.c_int,                     # n
        ], ctypes.c_double)
        # ── T-recon: 8 频段均值（FFT 之后; 单帧串行 + 批量 OpenMP）──
        self.ce_audio_frame_energy_batch = _bind(dll, "ce_audio_frame_energy_batch", [
            ctypes.POINTER(ctypes.c_float),   # bufs [n_frames*n] f32 行主序
            ctypes.c_int,                     # n_frames
            ctypes.c_int,                     # n
            ctypes.c_double,                  # gain
            ctypes.POINTER(ctypes.c_double),  # out_rms [n_frames]
            ctypes.POINTER(ctypes.c_double),  # out_peak [n_frames]
        ], None)
        self.ce_audio_bands = _bind(dll, "ce_audio_bands", [
            ctypes.POINTER(ctypes.c_double),  # fft [n_bins] f64 幅值谱
            ctypes.c_int,                     # n_bins
            ctypes.c_double,                  # sr
            ctypes.c_int,                     # n（FFT 长度, rfftfreq 用）
            ctypes.c_double,                  # gain
            ctypes.POINTER(ctypes.c_double),  # out8[8] f64
        ], None)
        self.ce_audio_bands_batch = _bind(dll, "ce_audio_bands_batch", [
            ctypes.POINTER(ctypes.c_double),  # ffts [n_frames*n_bins] 行主序
            ctypes.c_int,                     # n_bins
            ctypes.c_int,                     # n_frames
            ctypes.c_double,                  # sr
            ctypes.c_int,                     # n
            ctypes.c_double,                  # gain
            ctypes.POINTER(ctypes.c_double),  # out8n [n_frames*8] 行主序
        ], None)
        # ── T-recon2: MSA 结构分析纯数学段（每 25ms 帧频 + 离线烘焙共用）──
        self.ce_mean_f64 = _bind(dll, "ce_mean_f64", [
            ctypes.POINTER(ctypes.c_double),  # vals [n] f64
            ctypes.c_int,                     # start（含）
            ctypes.c_int,                     # end（不含）
        ], ctypes.c_double)
        self.ce_sorted_low_mean_f64 = _bind(dll, "ce_sorted_low_mean_f64", [
            ctypes.POINTER(ctypes.c_double),  # vals [n] f64
            ctypes.c_int,                     # n
            ctypes.c_int,                     # k（取最小 k 个的均值）
            ctypes.POINTER(ctypes.c_double),  # out（NaN 输入 → 返回 1 无效）
        ], ctypes.c_int)
        self.ce_weighted_band_score_f64 = _bind(dll, "ce_weighted_band_score_f64", [
            ctypes.POINTER(ctypes.c_double),  # bands [n] f64
            ctypes.POINTER(ctypes.c_double),  # weights [n] f64
            ctypes.c_int,                     # n
        ], ctypes.c_double)
        self.ce_spectral_flux_f64 = _bind(dll, "ce_spectral_flux_f64", [
            ctypes.POINTER(ctypes.c_double),  # bands [n] f64
            ctypes.POINTER(ctypes.c_double),  # prev [n] f64
            ctypes.c_int,                     # n
        ], ctypes.c_double)
        self.ce_centroid_f64 = _bind(dll, "ce_centroid_f64", [
            ctypes.POINTER(ctypes.c_double),  # bands [n] f64
            ctypes.c_int,                     # n
        ], ctypes.c_double)
        self.ce_novelty_f64 = _bind(dll, "ce_novelty_f64", [
            ctypes.POINTER(ctypes.c_double),  # bands [n] f64
            ctypes.POINTER(ctypes.c_double),  # prev [n] f64
            ctypes.c_int,                     # n
        ], ctypes.c_double)
        # ── T-recon2 复合: 每方法一次调用的切片均值批（槽位算法=单核同款）──
        self.ce_classify_means_f64 = _bind(dll, "ce_classify_means_f64", [
            ctypes.POINTER(ctypes.c_double),  # ch [ch_n]
            ctypes.c_int, ctypes.POINTER(ctypes.c_double),  # fh [fh_n]
            ctypes.c_int, ctypes.POINTER(ctypes.c_double),  # rms [rms_n]
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_double),  # out7 [head_ch,tail_ch,avg_fh,
                                              #       tail_fh,head_fh,
                                              #       tail_rms,head_rms]
        ], None)
        self.ce_buildup_means_f64 = _bind(dll, "ce_buildup_means_f64", [
            ctypes.POINTER(ctypes.c_double),  # ch [ch_n]
            ctypes.c_int, ctypes.POINTER(ctypes.c_double),  # rs [rs_n]
            ctypes.c_int, ctypes.POINTER(ctypes.c_double),  # fh [fh_n]
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_double),  # out6 [tail_ch,head_ch,
                                              #       tail_rs,head_rs,
                                              #       tail_fh,head_fh]
        ], None)
        self.ce_energy_means_f64 = _bind(dll, "ce_energy_means_f64", [
            ctypes.POINTER(ctypes.c_double),  # s [s_n]
            ctypes.c_int, ctypes.POINTER(ctypes.c_double),  # m [m_n]
            ctypes.c_int, ctypes.POINTER(ctypes.c_double),  # l [l_n]
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_double),  # out3 [mean_s, mean_m, mean_l]
        ], None)
        # ── T-recon2 环形镜像: 窗口常驻 f64 环形缓冲, hook 零转换直读 ──
        # 每环参数 = (buf, buf_n, filled, idx); 逻辑序约定见 ce_engine.c
        self.ce_ring_mean_f64 = _bind(dll, "ce_ring_mean_f64", [
            ctypes.POINTER(ctypes.c_double), ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ], ctypes.c_double)
        self.ce_ring_sorted_low_mean_f64 = _bind(
            dll, "ce_ring_sorted_low_mean_f64", [
                ctypes.POINTER(ctypes.c_double), ctypes.c_int, ctypes.c_int,
                ctypes.c_int, ctypes.c_int,
                ctypes.POINTER(ctypes.c_double),
            ], ctypes.c_int)
        self.ce_ring_energy_means_f64 = _bind(
            dll, "ce_ring_energy_means_f64", [
                ctypes.POINTER(ctypes.c_double), ctypes.c_int, ctypes.c_int,
                ctypes.c_int,                        # s 环
                ctypes.POINTER(ctypes.c_double), ctypes.c_int, ctypes.c_int,
                ctypes.c_int,                        # m 环
                ctypes.POINTER(ctypes.c_double), ctypes.c_int, ctypes.c_int,
                ctypes.c_int,                        # l 环
                ctypes.POINTER(ctypes.c_double),     # out3
            ], None)
        self.ce_ring_classify_means_f64 = _bind(
            dll, "ce_ring_classify_means_f64", [
                ctypes.POINTER(ctypes.c_double), ctypes.c_int, ctypes.c_int,
                ctypes.c_int,                        # ch 环
                ctypes.POINTER(ctypes.c_double), ctypes.c_int, ctypes.c_int,
                ctypes.c_int,                        # fh 环
                ctypes.POINTER(ctypes.c_double), ctypes.c_int, ctypes.c_int,
                ctypes.c_int,                        # rms 环
                ctypes.POINTER(ctypes.c_double),     # out7
            ], None)
        # ── T-recon2b: 6 乐器得分一次调用（bands[8] × 权重矩阵 w6[6][8]）──
        self.ce_instrument_scores_f64 = _bind(
            dll, "ce_instrument_scores_f64", [
                ctypes.POINTER(ctypes.c_double),  # bands [n_b] f64
                ctypes.c_int,                     # n_b（行宽, MSA 恒 8）
                ctypes.POINTER(ctypes.c_double),  # w6 [n_i*n_b] f64
                ctypes.c_int,                     # n_i（乐器数, 恒 6）
                ctypes.POINTER(ctypes.c_double),  # out6 [n_i]
            ], ctypes.c_int)
        self.ce_ring_buildup_means_f64 = _bind(
            dll, "ce_ring_buildup_means_f64", [
                ctypes.POINTER(ctypes.c_double), ctypes.c_int, ctypes.c_int,
                ctypes.c_int,                        # ch 环
                ctypes.POINTER(ctypes.c_double), ctypes.c_int, ctypes.c_int,
                ctypes.c_int,                        # rs 环
                ctypes.POINTER(ctypes.c_double), ctypes.c_int, ctypes.c_int,
                ctypes.c_int,                        # fh 环
                ctypes.POINTER(ctypes.c_double),     # out6
            ], None)
        # ── T6: D1 参数包络求值 ────────────────────────────────────────
        self.ce_param_envelope = _bind(dll, "ce_param_envelope", [
            ctypes.c_double,                     # t
            ctypes.c_double,                     # duration
            ctypes.POINTER(ctypes.c_double),     # xs[n] 控制点相位
            ctypes.POINTER(ctypes.c_double),     # ys[n] 控制点值
            ctypes.c_int,                        # n
            ctypes.c_int,                        # mode 0=curve 1=points
        ], ctypes.c_double)
        self.ce_param_envelope_map = _bind(dll, "ce_param_envelope_map", [
            ctypes.c_double, ctypes.c_double,      # t, duration
            ctypes.POINTER(ctypes.c_double),       # xs[n]
            ctypes.POINTER(ctypes.c_double),       # ys[n]
            ctypes.c_int, ctypes.c_int,            # n, mode
            ctypes.c_double, ctypes.c_double,      # lo, hi
            ctypes.c_int,                          # is_int 0/1
        ], ctypes.c_double)
        # ── T7: ArtNet ArtDMX 打包（对齐 artnet_node._build_artdmx）────────
        self.ce_artnet_pack = _bind(dll, "ce_artnet_pack", [
            ctypes.c_uint16,                  # universe
            ctypes.c_uint8,                   # sequence
            _P8,                              # data [length] 或 NULL
            ctypes.c_int,                     # length 0..512
            _P8,                              # out [18+length] 输出
        ], ctypes.c_int)  # 总帧长 18+length
        # ── T8: 批量协议打包（一次 C 调用生成全部包, 帧数据 [n_univ*512]）──
        _U16P = ctypes.POINTER(ctypes.c_uint16)
        _I32P8 = ctypes.POINTER(ctypes.c_int32)
        self.ce_artnet_pack_batch = _bind(dll, "ce_artnet_pack_batch", [
            ctypes.c_int,                     # n_univ 域数
            _U16P,                            # universes[n_univ]
            _P8,                              # sequences[n_univ]
            _I32P8,                           # lengths[n_univ] 0..512
            _P8,                              # frame [n_univ*512] 域主序
            _P8,                              # out 输出缓冲
            _I32P8,                           # offsets[n_univ] 每包偏移 (可 NULL)
        ], ctypes.c_int)  # 总字节数
        self.ce_sacn_pack = _bind(dll, "ce_sacn_pack", [
            _P8,                              # cid[16]
            _P8,                              # name[64]
            ctypes.c_uint16,                  # universe
            ctypes.c_uint8,                   # sequence
            ctypes.c_uint8,                   # priority
            _P8,                              # data [length] 或 NULL
            ctypes.c_int,                     # length 0..512
            _P8,                              # out [126+length] 输出
        ], ctypes.c_int)  # 总帧长 126+length
        self.ce_sacn_pack_batch = _bind(dll, "ce_sacn_pack_batch", [
            ctypes.c_int,                     # n_univ 域数
            _P8,                              # cid[16]
            _P8,                              # name[64]
            _U16P,                            # universes[n_univ]
            _P8,                              # sequences[n_univ]
            _P8,                              # priorities[n_univ]
            _I32P8,                           # lengths[n_univ] 0..512
            _P8,                              # frame [n_univ*512] 域主序
            _P8,                              # out 输出缓冲
            _I32P8,                           # offsets[n_univ] 每包偏移 (可 NULL)
        ], ctypes.c_int)  # 总字节数
        # ── T6: 空间聚块 / 方向变换（apply_spatial_scope / apply_effect_direction）──
        self.ce_effect_direction = _bind(dll, "ce_effect_direction", [
            ctypes.c_int,                      # n 条目数
            ctypes.c_int,                      # mode 0=恒等 1=反向 2=center_out 3=edge_in
            ctypes.POINTER(ctypes.c_int32),    # out[n] 原索引排列
        ], None)
        self.ce_spatial_scope = _bind(dll, "ce_spatial_scope", [
            ctypes.POINTER(ctypes.c_int64),    # item_fids[n]（假值 id → -1 哨兵）
            ctypes.c_int,                      # n
            ctypes.POINTER(ctypes.c_int64),    # pos_fids[m] positions 键
            ctypes.POINTER(ctypes.c_double),   # pos_x[m]
            ctypes.POINTER(ctypes.c_double),   # pos_y[m]
            ctypes.c_int,                      # m
            ctypes.POINTER(ctypes.c_int64),    # group_fids[总]（已去空串）
            ctypes.POINTER(ctypes.c_int32),    # group_lens[g]（过滤后长度）
            ctypes.c_int,                      # g
            ctypes.POINTER(ctypes.c_int32),    # out[n] 原索引排列
        ], None)
        # ── P7: OutputSmoother 输出平滑器（C 持久状态, 每 universe 每 tick）───
        # 24a428c 回退后 ce_engine.c 已无 smoother/joystick 内核（待逐个重做）;
        # 此处改 getattr 可选, 否则 DLL 加载即失败 → 全部 CE hook 假死。
        self.ce_npy_sinf = getattr(dll, "ce_npy_sinf", None)
        if self.ce_npy_sinf is not None:
            self.ce_npy_sinf.argtypes = [ctypes.c_float]
            self.ce_npy_sinf.restype = ctypes.c_float
        self.ce_smoother_create = getattr(dll, "ce_smoother_create", None)
        if self.ce_smoother_create is not None:
            self.ce_smoother_create.argtypes = [ctypes.c_int]
            self.ce_smoother_create.restype = ctypes.c_void_p
        self.ce_smoother_destroy = getattr(dll, "ce_smoother_destroy", None)
        if self.ce_smoother_destroy is not None:
            self.ce_smoother_destroy.argtypes = [ctypes.c_void_p]
            self.ce_smoother_destroy.restype = None
        self.ce_smoother_reset = getattr(dll, "ce_smoother_reset", None)
        if self.ce_smoother_reset is not None:
            self.ce_smoother_reset.argtypes = [ctypes.c_void_p]
            self.ce_smoother_reset.restype = None
        self.ce_smoother_reset_universe = getattr(dll, "ce_smoother_reset_universe", None)
        if self.ce_smoother_reset_universe is not None:
            self.ce_smoother_reset_universe.argtypes = [ctypes.c_void_p, ctypes.c_int]
            self.ce_smoother_reset_universe.restype = None
        self.ce_smoother_apply = getattr(dll, "ce_smoother_apply", None)
        if self.ce_smoother_apply is not None:
            self.ce_smoother_apply.argtypes = [
                ctypes.c_void_p,                 # ctx
                ctypes.c_int,                    # uni（0..cap-1）
                ctypes.c_double,                 # now（time.monotonic）
                ctypes.POINTER(ctypes.c_uint8),  # raw [512]
                ctypes.c_double,                 # attack_s（max(0.001, ms/1000)）
                ctypes.c_double,                 # release_s
                ctypes.c_double,                 # smoothing
                ctypes.c_double,                 # inertia
                ctypes.c_double,                 # overshoot
                ctypes.c_int,                    # deadband
                ctypes.c_int,                    # curve 0=linear 1=ease_in 2=ease_out 3=ease_in_out 4=exponential
                ctypes.POINTER(ctypes.c_uint8),  # out [512]
            ]
            self.ce_smoother_apply.restype = ctypes.c_int
        self.ce_smoother_dump_state = getattr(dll, "ce_smoother_dump_state", None)
        if self.ce_smoother_dump_state is not None:
            self.ce_smoother_dump_state.argtypes = [
                ctypes.c_void_p,                 # ctx
                ctypes.c_int,                    # uni
                ctypes.POINTER(ctypes.c_float),  # current [512]
                ctypes.POINTER(ctypes.c_float),  # target [512]
                ctypes.POINTER(ctypes.c_float),  # velocity [512]
                ctypes.POINTER(ctypes.c_double), # last_update [512]
                ctypes.POINTER(ctypes.c_double), # last_target_change [512]
            ]
            self.ce_smoother_dump_state.restype = None
        # ── T7: 摇杆轴处理纯数学（deadzone/normalize/curve/全链合成）────────────
        # 同上: 24a428c 回退后内核待重做, getattr 可选防 DLL 加载失败。
        self.ce_joystick_deadzone = getattr(dll, "ce_joystick_deadzone", None)
        if self.ce_joystick_deadzone is not None:
            self.ce_joystick_deadzone.argtypes = [
                ctypes.c_double, ctypes.c_double,   # value, deadzone
            ]
            self.ce_joystick_deadzone.restype = ctypes.c_double
        self.ce_joystick_normalize = getattr(dll, "ce_joystick_normalize", None)
        if self.ce_joystick_normalize is not None:
            self.ce_joystick_normalize.argtypes = [
                ctypes.c_double, ctypes.c_double, ctypes.c_int,  # raw, deadzone, invert
            ]
            self.ce_joystick_normalize.restype = ctypes.c_double
        self.ce_joystick_curve = getattr(dll, "ce_joystick_curve", None)
        if self.ce_joystick_curve is not None:
            self.ce_joystick_curve.argtypes = [
                ctypes.c_double, ctypes.c_int,      # normalized, curve_id 0-5
                ctypes.c_double, ctypes.c_int,      # gamma, steps
            ]
            self.ce_joystick_curve.restype = ctypes.c_double
        self.ce_joystick_axis = getattr(dll, "ce_joystick_axis", None)
        if self.ce_joystick_axis is not None:
            self.ce_joystick_axis.argtypes = [
                ctypes.c_double, ctypes.c_double, ctypes.c_int,  # raw, deadzone, invert
                ctypes.c_int, ctypes.c_double, ctypes.c_int,     # curve_id, gamma, steps
                ctypes.c_int, ctypes.c_int,                      # range_lo, range_hi
            ]
            self.ce_joystick_axis.restype = ctypes.c_int
        # ── T12: 关联器（track linker）整段 C 计算 ────────────────────
        # 见 csrc/ce_engine.c 的「关联器计算内核」节 + qascade_ce/linker_path.py。
        # 契约: 表指针数组每项 NULL 或指向 ≥512B 缓冲; 非 0 返回 = 该规则回退原版。
        _UP = ctypes.POINTER(ctypes.c_uint8)
        _UPP = ctypes.POINTER(_UP)
        _I32P = ctypes.POINTER(ctypes.c_int32)
        _F64P = ctypes.POINTER(ctypes.c_double)
        self.ce_linker_preset_eval = _bind(dll, "ce_linker_preset_eval", [
            ctypes.c_int,          # kind（CE_LK_*）
            _F64P,                 # params[>=33]
            ctypes.c_int,          # n
            _F64P,                 # src[n]
            _F64P,                 # aux[n]（NULL / NaN = 无辅助值）
            _F64P,                 # out[n]
        ], ctypes.c_int)
        self.ce_linker_rule = _bind(dll, "ce_linker_rule", [
            ctypes.c_int, ctypes.c_int,   # kind, mode（CE_LM_*）
            _F64P,                        # params[40]
            _I32P,                        # pair[n_dst]（仅 XY_MULTI; 否则 NULL）
            ctypes.c_int, _I32P,          # n_src, src_desc[n_src*4]
            ctypes.c_int, _I32P,          # n_dst, dst_desc[n_dst*4]
            ctypes.c_int,                 # n_uni（表长）
            _UPP, _UPP, _UPP, _UPP, _UPP,  # snap/motion/act/motact/parked
            _UPP, _UPP,                    # pend, pend_has（就地更新）
            _I32P, _I32P, _I32P, _I32P,    # out_u/out_a/out_v/out_n
            ctypes.c_int,                  # out_cap
        ], ctypes.c_int)
        # ── T13: 矩阵像素引擎（matrix slideshow pixel engine）──────────
        # 见 csrc/_ce_mtx_kernel.c.inc 的「矩阵像素引擎计算内核」节 +
        # qascade_ce/matrix_pixel_path.py。契约: col/row 为 n 元素 int32
        # 连续数组, out 为 n 元素 float64; 非 0 返回 = 该帧回退 Python 原版。
        self.ce_mtx_pattern = _bind(dll, "ce_mtx_pattern", [
            ctypes.c_int,                  # n
            _I32P, _I32P,                  # col[n], row[n]
            ctypes.c_int, ctypes.c_int,    # cols, rows
            ctypes.c_double,               # width
            ctypes.c_double,               # twist
            ctypes.c_double,               # mix
            ctypes.c_double,               # t
            ctypes.c_double,               # phase
            ctypes.c_double,               # freq
            ctypes.c_double,               # soft
            ctypes.c_double,               # bass
            ctypes.c_double,               # mid
            ctypes.c_double,               # high
            ctypes.c_double,               # rms
            ctypes.c_int,                  # variant
            ctypes.c_int,                  # family_offset
            _F64P,                         # out[n]
        ], ctypes.c_int)
        self.ce_mtx_particle = _bind(dll, "ce_mtx_particle", [
            ctypes.c_int,                  # n
            _I32P, _I32P,                  # col[n], row[n]
            ctypes.c_int, ctypes.c_int,    # cols, rows
            ctypes.c_double,               # density
            ctypes.c_double,               # width
            ctypes.c_double,               # twist
            ctypes.c_double,               # mix
            ctypes.c_double,               # t
            ctypes.c_double,               # phase
            ctypes.c_double,               # soft
            ctypes.c_double,               # bass
            ctypes.c_double,               # mid
            ctypes.c_double,               # high
            ctypes.c_double,               # rms
            ctypes.c_int,                  # beat
            ctypes.c_int,                  # variant
            _F64P,                         # out[n]
        ], ctypes.c_int)
        # ── T14: effect `_tick_play` 写回层（逐域地址散写 / RGBW 亮度模拟）──
        # 见 csrc/_ce_eff_kernel.c.inc 的「effect 写回内核」节 +
        # qascade_ce/effect_write_path.py。批式接口: 形态表（int64 行, 指针以
        # int64 值携带）一次传全部域 —— pass1 校验全部域, pass2 才写 ⇒
        # 任一域非法 = 整批零副作用拒绝（非 0 返回 = 回退 Python 原版）。
        # ★ 指针参数用 c_void_p: 热路径传 `arr.__array_interface__["data"][0]`
        #   的裸 int（省 ~1.5µs/次的 ctypes 对象构造）。
        self.ce_eff_write_direct = _bind(dll, "ce_eff_write_direct", [
            ctypes.c_int,                  # n_uni
            ctypes.c_void_p,               # shape[n_uni*5]（int64）
            ctypes.c_int,                  # n_val
            ctypes.c_void_p,               # values[n_val]（f64）
            ctypes.c_int,                  # is_motor（1 = 覆盖写, 0 = HTP max）
        ], ctypes.c_int)
        self.ce_eff_write_rgbw = _bind(dll, "ce_eff_write_rgbw", [
            ctypes.c_int,                  # n_uni
            ctypes.c_void_p,               # shape[n_uni*21]（int64）
            ctypes.c_int,                  # n_val
            ctypes.c_void_p,               # values[n_val]（f64）
        ], ctypes.c_int)
        # ── T6: 安全联锁（逐帧状态机，状态数组 in/out 由调用方持有）──────
        _FP = ctypes.POINTER(ctypes.c_float)
        _IP = ctypes.POINTER(ctypes.c_int)
        self.ce_interlock_process = _bind(dll, "ce_interlock_process", [
            _FP, ctypes.c_int, ctypes.c_int,          # result[frame_len], frame_len, universe
            ctypes.c_int, _IP, _IP, _IP,              # n, cu[], cs[], cn[]
            ctypes.POINTER(_FP), ctypes.POINTER(_FP), # cmin[][], cmax[][]
            ctypes.POINTER(_FP), ctypes.POINTER(_FP), # crate[][], cdef[][]
            _IP, _IP, _IP, _IP, ctypes.POINTER(_FP),  # states, counts, has_prev, prev_len, prevs[][]
            ctypes.c_int, ctypes.c_int, ctypes.c_int, # warning_t, softlimit_t, hardstop_t
            ctypes.c_double, _IP, _UP,                # blackout_ratio, blackout(out), out[frame_len]
        ], None)
        # ── T7: 工程 XML 快速扫描（工程包 → project.xml 字节扫描）──
        self.ce_xml_scan = _bind(dll, "ce_xml_scan", [
            ctypes.c_void_p,                       # xml 字节（零拷贝, 显式长度）
            ctypes.c_int64,                        # xml_len
            ctypes.POINTER(ctypes.c_char_p),       # tags[] 标签名
            ctypes.POINTER(ctypes.c_int),          # tag_lens[]
            ctypes.c_int,                          # n_tags
            ctypes.POINTER(ctypes.c_char_p),       # attrs[] 属性名（空串=content 模式）
            ctypes.POINTER(ctypes.c_int),          # attr_lens[]
            ctypes.c_int,                          # n_attrs
            ctypes.POINTER(ctypes.c_int),          # pair_tags[] 请求对
            ctypes.POINTER(ctypes.c_int),          # pair_attrs[]
            ctypes.c_int,                          # n_pairs
            ctypes.POINTER(ctypes.c_int64),        # out_el_off[] 元素 '<' 偏移
            ctypes.POINTER(ctypes.c_int64),        # out_off[] 值区间起
            ctypes.POINTER(ctypes.c_int64),        # out_len[] 值区间长
            ctypes.POINTER(ctypes.c_int),          # out_pair[] 请求下标
            ctypes.c_int,                          # max_hits
        ], ctypes.c_int)
        # ── T7续: 电机 16bit 粗/精拆分与合并（对齐 src/engine/xy_reference.py）──
        self.ce_split_16bit = _bind(dll, "ce_split_16bit", [
            ctypes.c_double,                   # value（0..255 逻辑电机值, 任意 float）
            ctypes.POINTER(ctypes.c_uint8),    # out_coarse [1]
            ctypes.POINTER(ctypes.c_uint8),    # out_fine [1]
        ], None)
        self.ce_merge_16bit = _bind(dll, "ce_merge_16bit", [
            ctypes.c_int,                      # coarse 0..255
            ctypes.c_int,                      # fine 0..255（无 fine 通道传 0）
        ], ctypes.c_double)
        # ── T8: fade 几何排序（对齐 playback_engine.py
        #       _linear_direction_positions_for_fixture_ids :5818 /
        #       _order_fixture_ids_for_direction :6009）──────────────
        _F32P = ctypes.POINTER(ctypes.c_float)
        _F64P = ctypes.POINTER(ctypes.c_double)
        _I32P = ctypes.POINTER(ctypes.c_int32)
        self.ce_fade_dir_positions = _bind(dll, "ce_fade_dir_positions", [
            _F64P, _F64P, ctypes.c_int,   # xs, ys, n（设备坐标）
            _F64P, _F64P, ctypes.c_int,   # sxs, sys, sn（scope 坐标; NULL/0 → 回退 ids）
            ctypes.c_int,                 # mode 0=ltr 1=rtl 2=ftb 3=btf 4=ctr 5=rtc
            _F32P,                        # out [n] f32 位置系数
        ], ctypes.c_int)  # 1 成功 / 0 无效(回退)
        self.ce_ordered_fixtures = _bind(dll, "ce_ordered_fixtures", [
            _F32P, ctypes.c_int,          # positions [n] f32
            _I32P,                        # out [n] 排序后原索引
        ], None)
        # ── T8: 音乐→效果桥接纯数学（audio_param_modulator map_value/_smooth
        #    + music_to_effect_bridge _apply_common_range）─────────────────────
        self.ce_music_map = _bind(dll, "ce_music_map", [
            ctypes.c_double, ctypes.c_double, ctypes.c_double,  # raw, in_min, in_max
            ctypes.c_double, ctypes.c_double,                   # out_min, out_max
            ctypes.c_int,                    # curve_idx 0=linear 1=exp 2=log 3=step
        ], ctypes.c_double)
        self.ce_music_smooth_ema = _bind(dll, "ce_music_smooth_ema", [
            ctypes.c_double, ctypes.c_double, ctypes.c_double,  # cur, target, alpha
        ], ctypes.c_double)
        self.ce_music_smooth_fir5 = _bind(dll, "ce_music_smooth_fir5", [
            ctypes.POINTER(ctypes.c_double),  # hist[5] in/out（初值=首个 target）
            ctypes.c_double,                  # target
        ], ctypes.c_double)
        self.ce_music_common_range = _bind(dll, "ce_music_common_range", [
            ctypes.c_double,                  # v
            ctypes.c_int, ctypes.c_int,       # lo, hi
            ctypes.c_int,                     # is_int 0/1
            ctypes.POINTER(ctypes.c_longlong),  # out_int（is_int 时输出 rint 结果）
        ], ctypes.c_double)
        # ── T8: auto_arrange 自动编曲纯数学（对齐 src/engine/auto_arrange）────
        self.ce_arrange_dyn_params = _bind(dll, "ce_arrange_dyn_params", [
            ctypes.c_double,                     # section_energy
            ctypes.c_int, ctypes.c_int,          # is_first, is_last
            ctypes.POINTER(ctypes.c_double),     # out8[8]
        ], None)
        self.ce_arrange_dyn_level = _bind(dll, "ce_arrange_dyn_level", [
            ctypes.c_double, ctypes.c_double, ctypes.c_double,  # attack, peak, release
            ctypes.c_double,                     # peak_position
            ctypes.c_int, ctypes.c_int,          # attack_shape, decay_shape 0-3
            ctypes.c_double,                     # t_rel
        ], ctypes.c_double)
        self.ce_arrange_dyn_speed = _bind(dll, "ce_arrange_dyn_speed", [
            ctypes.c_int,                        # has_cadence 0/1
            ctypes.c_double,                     # ritardando_factor
            ctypes.c_double, ctypes.c_double,    # t_rel, base_speed
        ], ctypes.c_double)
        self.ce_arrange_dyn_level_full = _bind(dll, "ce_arrange_dyn_level_full", [
            ctypes.c_double,                     # section_energy
            ctypes.c_int, ctypes.c_int,          # is_first, is_last
            ctypes.c_double,                     # t_rel
        ], ctypes.c_double)
        self.ce_arrange_dyn_speed_full = _bind(dll, "ce_arrange_dyn_speed_full", [
            ctypes.c_double,                     # section_energy
            ctypes.c_int, ctypes.c_int,          # is_first, is_last
            ctypes.c_double, ctypes.c_double,    # t_rel, base_speed
        ], ctypes.c_double)
        self.ce_arrange_dyn_section_weight = _bind(dll, "ce_arrange_dyn_section_weight", [
            ctypes.c_int, ctypes.c_int, ctypes.c_int,  # peak_idx, n, section_idx
        ], ctypes.c_double)
        self.ce_arrange_case_sim = _bind(dll, "ce_arrange_case_sim", [
            _DP, _DP, _DP,                       # feat[nf], cfeat[nf], w[nf]
            ctypes.c_int,                        # nf
            _DP, _DP,                            # out_dist, out_weight
        ], None)
        # ── T8续: auto_arrange 总指挥/喷泉编导数值（arrangement_director.py +
        #    fountain_arranger.py 纯计算段, 不接 hook）─────────────────────
        self.ce_orch_speed_band = _bind(dll, "ce_orch_speed_band", [
            ctypes.c_double, ctypes.c_double, ctypes.c_double,  # energy, perc, melodic
            ctypes.c_double, ctypes.c_double, ctypes.c_double,  # speed_lo/mid/hi
            ctypes.c_double,                                    # speed_peak
            ctypes.c_int, ctypes.c_int,                         # resp_perc, resp_mel 0/1
        ], ctypes.c_double)
        self.ce_orch_group_select = _bind(dll, "ce_orch_group_select", [
            _IP, _IP, _IP, _IP,               # shapes, has_center, counts, has_id [n]
            ctypes.c_int,                     # n_groups
            ctypes.c_int,                     # mode 0=lead 1=support
            _IP,                              # out_idx[2]
        ], ctypes.c_int)
        self.ce_rank_devices = _bind(dll, "ce_rank_devices", [
            ctypes.c_double, ctypes.c_double, ctypes.c_double,  # energy, percussive, melodic
            _IP,                                                # out_order[6] 设备下标序
        ], ctypes.c_int)
        # ── T8: XY 参考表构建（对齐 src/engine/xy_reference.py :117 纯数值段）──
        _I4P = ctypes.POINTER(ctypes.c_int32)
        _I8P = ctypes.POINTER(ctypes.c_int64)
        _D8P = ctypes.POINTER(ctypes.c_double)
        self.ce_xy_ref_build = _bind(dll, "ce_xy_ref_build", [
            _I4P, _I8P, _I4P, _I4P, _I4P,    # uni_in, start_ch_in, mode_code, has_x, has_y
            _I4P, _I4P, _I4P, _I4P, _I4P,    # x_off, y_off, xf_off, yf_off, ang_flags
            _D8P, _D8P, _D8P, _D8P, _D8P,    # v_main, v_base, v_ang, v_off, y_sign_raw
            ctypes.c_double,                 # global_adjust_deg
            ctypes.c_int,                    # n
            _I4P, _I4P, _I4P, _I4P, _I4P,    # out_valid, out_x_addr, out_y_addr, out_xf_addr, out_yf_addr
            _D8P, _D8P,                      # out_angle_deg, out_y_sign
        ], None)
        # ── T8: 虚拟单元 merge-max 合并（对齐 effect_engine.py _merge_values :853）──
        self.ce_merge_max = _bind(dll, "ce_merge_max", [
            ctypes.POINTER(ctypes.c_int64),   # unit_orig [n_units] 目标设备下标
            ctypes.POINTER(ctypes.c_int64),   # unit_chan [n_units*n_chan]（<0=跳过）
            ctypes.POINTER(ctypes.c_double),  # unit_val [n_units*n_chan]
            ctypes.POINTER(ctypes.c_uint8),   # merge_kind [max_chan] 0=覆盖 1=merge-max
            ctypes.c_int,                     # n_units
            ctypes.c_int,                     # n_chan
            ctypes.c_int,                     # n_dev
            ctypes.c_int,                     # max_chan
            ctypes.POINTER(ctypes.c_double),  # prev_val [n_dev*max_chan] 或 NULL
            ctypes.POINTER(ctypes.c_uint8),   # prev_has [n_dev*max_chan] 或 NULL
            ctypes.POINTER(ctypes.c_double),  # out_val [n_dev*max_chan]（未写=NaN 哨兵）
            ctypes.POINTER(ctypes.c_uint8),   # out_has [n_dev*max_chan]
        ], None)
        # ── T8: 工程对象构建 — _ln/_at 批量内核 ──
        self.ce_local_name_batch = _bind(dll, "ce_local_name_batch", [
            ctypes.c_char_p,                     # blob 标签字节连接
            ctypes.POINTER(ctypes.c_int32),      # lens[] 每标签字节长
            ctypes.c_int,                        # n
            ctypes.POINTER(ctypes.c_int32),      # out_beg[] 最后 '}' 后下标
        ], None)
        self.ce_attr_lookup_batch = _bind(dll, "ce_attr_lookup_batch", [
            ctypes.c_char_p,                     # kblob 键字节连接
            ctypes.POINTER(ctypes.c_int32),      # klen[] 每键字节长
            ctypes.c_char_p,                     # vblob 值字节连接
            ctypes.POINTER(ctypes.c_int32),      # vlen[] 每值字节长
            ctypes.POINTER(ctypes.c_int32),      # attr_beg[] 元素属性区间 [n_elem+1]
            ctypes.c_int,                        # n_elem
            ctypes.POINTER(ctypes.c_char_p),     # want_keys[] 目标键
            ctypes.POINTER(ctypes.c_int32),      # want_lens[]
            ctypes.c_int,                        # n_keys
            ctypes.POINTER(ctypes.c_int32),      # out_idx [n_elem*n_keys] 全局属性下标/-1
            ctypes.POINTER(ctypes.c_int64),      # out_start [n_elem*n_keys] 值起始
            ctypes.POINTER(ctypes.c_int32),      # out_len [n_elem*n_keys] 值长
        ], None)
        # ── T4续: v29 有状态矩阵效果（状态持久化在 C, 对齐 effect_presets_v29.py）──
        self.ce_matrix_stateful_create = _bind(dll, "ce_matrix_stateful_create", [
            ctypes.c_int, ctypes.c_int,             # cols, n（rows 由 C 推导）
        ], ctypes.c_void_p)
        self.ce_matrix_stateful_destroy = _bind(dll, "ce_matrix_stateful_destroy", [ctypes.c_void_p], None)
        self.ce_matrix_stateful_apply = _bind(dll, "ce_matrix_stateful_apply", [
            ctypes.c_void_p,                        # ctx
            ctypes.c_double, ctypes.c_int,          # t, n
            ctypes.c_int, ctypes.c_int,             # shape_idx(0-14), max_groups
            ctypes.c_double, ctypes.c_double,       # group_duration, trigger_bpm
            ctypes.c_int, ctypes.c_int, ctypes.c_int,  # shape_size, lo, hi
            _P8,                                    # out[n]
        ], None)
        self.ce_matrix_stateful_export = _bind(dll, "ce_matrix_stateful_export", [
            ctypes.c_void_p,                        # ctx
            ctypes.POINTER(ctypes.c_uint32),        # mt[624] 输出
            ctypes.POINTER(ctypes.c_int),           # index（字消耗）输出
            ctypes.POINTER(ctypes.c_double),        # last_beat 输出
            ctypes.POINTER(ctypes.c_int),           # n_groups 输出
        ], None)
        self.ce_matrix_stateful_group_get = _bind(dll, "ce_matrix_stateful_group_get", [
            ctypes.c_void_p, ctypes.c_int,          # ctx, gi
            ctypes.POINTER(ctypes.c_double),        # start_t 输出
            ctypes.POINTER(ctypes.c_int),           # cx 输出
            ctypes.POINTER(ctypes.c_int),           # cy 输出
            ctypes.POINTER(ctypes.c_int),           # anim_idx 输出
            ctypes.POINTER(ctypes.c_int),           # n_cells 输出
            ctypes.POINTER(ctypes.c_int16),         # dc[n_cells] 输出
            ctypes.POINTER(ctypes.c_int16),         # dr[n_cells] 输出
        ], ctypes.c_int)
        # ── T9: 网络心跳/保活纯数学（对齐 src/network/network_manager.py
        #    _mix_and_send_locked :1522-1535 空闲态心跳轮转三件套）──────────
        _I64P = ctypes.POINTER(ctypes.c_int64)
        self.ce_heartbeat_due = _bind(dll, "ce_heartbeat_due", [
            ctypes.c_double, ctypes.c_double, ctypes.c_double,  # now, last, hb_interval
        ], ctypes.c_int)
        self.ce_heartbeat_plan = _bind(dll, "ce_heartbeat_plan", [
            ctypes.c_double,                     # mix_interval
            ctypes.c_double,                     # hb_interval
            ctypes.c_longlong,                   # queue_len
            _DP,                                 # out_interval
            _DP,                                 # out_hb
            _I64P,                               # out_ticks
            _I64P,                               # out_batch
        ], ctypes.c_longlong)
        self.ce_heartbeat_rotate = _bind(dll, "ce_heartbeat_rotate", [
            _I64P,                               # q [qlen] 原地轮转
            ctypes.c_longlong,                   # qlen
            ctypes.c_longlong,                   # batch
        ], ctypes.c_longlong)
        # ── T8续: 音乐结构分析纯数学（对齐 src/audio/music_structure_analyzer.py
        #    _spectral_flux :107-112 / _compute_novelty :552-560 /
        #    _compute_centroid :540-550; prev 缺失/长度不等守卫留在调用方）──
        self.ce_music_spectral_delta = _bind(dll, "ce_music_spectral_delta", [
            ctypes.POINTER(ctypes.c_double),   # bands[n] f64
            ctypes.POINTER(ctypes.c_double),   # prev[n] f64（等长; 守卫不过 → n=0）
            ctypes.c_int,                      # n
            ctypes.POINTER(ctypes.c_double),   # out_flux 输出
            ctypes.POINTER(ctypes.c_double),   # out_novelty 输出
        ], None)
        self.ce_music_centroid = _bind(dll, "ce_music_centroid", [
            ctypes.POINTER(ctypes.c_double),   # bands[n] f64
            ctypes.c_int,                      # n
        ], ctypes.c_double)
        # ── T7续: update_layer 批量稀疏写入（对齐 src/network/network_manager.py
        #    update_layer_sparse_arrays/grouped_arrays 每 universe 纯数组块）──
        self.ce_layer_sparse_write = _bind(dll, "ce_layer_sparse_write", [
            ctypes.POINTER(ctypes.c_int32),         # addrs[n]
            ctypes.POINTER(ctypes.c_uint8),         # vals[n]
            ctypes.c_int64,                         # n
            ctypes.POINTER(ctypes.c_uint8),         # data[512] in-place
            ctypes.POINTER(ctypes.c_uint8),         # active[512] in-place
            ctypes.c_int,                           # use_fast_skip (0/1)
            ctypes.POINTER(ctypes.c_uint8),         # touch[512] 输出（先清零）
            ctypes.POINTER(ctypes.c_int),           # out_changed 输出（1=写入路径）
        ], ctypes.c_int)  # n_valid
        # ── Programmer 层数值 C 化（纯计算段，不接 hook；合入由主分支统一做）────
        self.ce_programmer_layer_mix = _bind(dll, "ce_programmer_layer_mix", [
            ctypes.c_double, ctypes.c_int32,   # fader 层值/模式
            ctypes.c_double, ctypes.c_int32,   # joystick 层值/模式
            ctypes.c_double, ctypes.c_int32,   # audio 层值/模式
            ctypes.c_double, ctypes.c_int32,   # matrix 层值/模式
        ], ctypes.c_double)
        self.ce_programmer_motor_16bit = _bind(dll, "ce_programmer_motor_16bit", [
            ctypes.c_double,                   # 8bit 逻辑位
        ], ctypes.c_uint32)
        # ── T8: voice_model 声部决策数值核心（对齐 voice_model.py
        #    decide_voice :299 纯数值段 + arrange_voices :530 织体上限）────
        self.ce_voice_decide = _bind(dll, "ce_voice_decide", [
            ctypes.c_int,                        # vc 0=motor 1=pump 2=valve
                                                 #    3=matrix 4=fountain 5=dimmer
            ctypes.c_double, ctypes.c_double,    # e, perc
            ctypes.c_int, ctypes.c_int, ctypes.c_int,  # has_mel, is_build, is_drop
            ctypes.c_double,                     # seg_dur
            _DP, _DP,                            # speed_band[3], dur_band[3]
            ctypes.c_double, ctypes.c_double,    # fade_in_base, fade_out_base
            ctypes.c_double, ctypes.c_double,    # seq_tendency, motor_phase_tendency
            ctypes.c_double,                     # global_soft
            ctypes.c_longlong,                   # key_kh（voice.key 0xFFFF 哈希）
            _DP,                                 # out_num[7]
            ctypes.POINTER(ctypes.c_int),        # out_flags[8]
        ], None)
        self.ce_voice_texture_cap = _bind(dll, "ce_voice_texture_cap", [
            ctypes.c_int,                        # n_voices
            ctypes.c_double,                     # e
            ctypes.c_int,                        # is_drop 0/1
        ], ctypes.c_int)
        # ── T10: 同步模式几何内核（对齐 topview_panel.py / geometry_splines.py）─
        _D2 = ctypes.POINTER(ctypes.c_double)
        self.ce_spline_param = _bind(dll, "ce_spline_param", [
            ctypes.c_double, ctypes.c_double,   # x, z 设备坐标
            _D2,                                # cp [n_cp*2] 交错 (x,z) 控制点
            ctypes.c_int,                       # n_cp
        ], ctypes.c_double)
        self.ce_catmull_rom_tangent = _bind(dll, "ce_catmull_rom_tangent", [
            _D2,                                # pts [n*2] 交错
            _D2,                                # ts [n] 弦长累积
            ctypes.c_int, ctypes.c_int, ctypes.c_int,  # n, idx, closed
            ctypes.POINTER(ctypes.c_double),    # out_dx
            ctypes.POINTER(ctypes.c_double),    # out_dy
        ], None)
        self.ce_sync_tangent_angles = _bind(dll, "ce_sync_tangent_angles", [
            _D2, ctypes.c_int, ctypes.c_int,    # pts [n*2], n, closed
            _D2,                                # out_deg [n]
        ], None)
        self.ce_spline_sample = _bind(dll, "ce_spline_sample", [
            _D2, ctypes.c_int, ctypes.c_int, ctypes.c_int,  # cp, n_cp, per_seg, mode
            _D2, _D2,                           # out_xy [n_out*2], out_ang [n_out]
        ], ctypes.c_int)
        self.ce_sync_angle_from_tangent = _bind(dll, "ce_sync_angle_from_tangent", [
            ctypes.c_double, ctypes.c_double,   # tx, ty 切向
            ctypes.c_double, ctypes.c_double,   # ndx, ndy 邻域弦（has_nd=0 忽略）
            ctypes.c_int, ctypes.c_int,         # has_nd, flip_roty
        ], ctypes.c_double)
        # ── T9续: 曲线/渐变族逐设备求值（纯计算段，不接 hook；合入由主分支统一做）──
        self.ce_gradient_effect = _bind(dll, "ce_gradient_effect", [
            ctypes.c_int,                      # kind 0-4（见 ce_engine.c）
            ctypes.c_double, ctypes.c_double,  # t, speed
            ctypes.c_double,                   # a（segments/steps/phase_offset/direction）
            ctypes.c_int, ctypes.c_int,        # lo, hi（输出值域参数）
            ctypes.c_int,                      # n
            _P8,                               # out[n]
        ], None)
        self.ce_color_gradient_effect = _bind(dll, "ce_color_gradient_effect", [
            ctypes.c_int,                      # kind 0-3（见 ce_engine.c）
            ctypes.c_double, ctypes.c_double,  # t, speed
            ctypes.POINTER(ctypes.c_double),   # p[3] 参数槽（见 ce_engine.c）
            ctypes.c_int,                      # n
            _P8,                               # out_r [n]
            _P8,                               # out_g [n]
            _P8,                               # out_b [n]
        ], None)
        # ── T8: 工程保存序列化（JSON 字节生成; 对齐 Project.save → json.dump
        #    indent=2, ensure_ascii=False; token 流见 ce_engine.c T8 节）────
        self.ce_fmt_double = _bind(dll, "ce_fmt_double", [
            ctypes.c_double,                     # x
            ctypes.c_char_p,                     # buf（≥40B, 写入 repr）
            ctypes.c_size_t,                     # cap
        ], ctypes.c_int)  # 字节数, -1 = cap 不足
        _P8 = ctypes.POINTER(ctypes.c_uint8)
        _I64P = ctypes.POINTER(ctypes.c_int64)
        self.ce_json_measure = _bind(dll, "ce_json_measure", [
            _P8, _I64P, _P8,                     # kinds[n], offs[n+1], payload
            ctypes.c_int,                        # n_tok
            ctypes.c_int,                        # indent（0..64）
        ], ctypes.c_int64)  # 总字节数, -1 = 非法流
        self.ce_json_encode = _bind(dll, "ce_json_encode", [
            _P8, _I64P, _P8,                     # kinds[n], offs[n+1], payload
            ctypes.c_int,                        # n_tok
            ctypes.c_int,                        # indent
            ctypes.c_char_p,                     # out（容量 ≥ measure）
        ], ctypes.c_int64)  # 写入字节数, -1 = 失败
        # ── T10续: 曲线族遗留 4 预设逐设备求值（v22 strobe_ramp / v25
        #    light_breath_curves / v35 motor_fan_gradient_open /
        #    v36 motor_cue_speed_ramp; 纯计算段, 不接 hook; 合入由主分支统一做）──
        self.ce_ramp_effect = _bind(dll, "ce_ramp_effect", [
            ctypes.c_int,                      # kind 0=strobe_ramp 1=light_breath
            ctypes.c_double,                   # t
            ctypes.POINTER(ctypes.c_double),   # p[16] 参数槽（见 ce_engine.c）
            ctypes.c_int,                      # n
            _P8,                               # out[n]
        ], None)
        self.ce_fan_cue_effect = _bind(dll, "ce_fan_cue_effect", [
            ctypes.c_int,                      # kind 0=fan_gradient_open 1=cue_speed_ramp
            ctypes.c_double,                   # t
            ctypes.POINTER(ctypes.c_double),   # p[16] 参数槽（见 ce_engine.c）
            ctypes.c_int,                      # n
            _P8,                               # out[n]
        ], None)
        # ── T4续3: 精炼库(cur_*) 6 内核 + wave_rainbow/wave_dual ────────
        # 输出 int32（原版无 clamp 直写 int → 值域可能越 0-255; hook 侧
        # 原样写回 dict 与 Python int() 逐位一致）。
        _I32P = ctypes.POINTER(ctypes.c_int32)
        self.ce_cur_scalar = _bind(dll, "ce_cur_scalar", [
            ctypes.c_int,                      # shape_idx 0-18
            ctypes.c_int,                      # ease_idx 0-3
            ctypes.c_double, ctypes.c_double,  # t, speed
            ctypes.c_double, ctypes.c_double,  # freq, duty
            ctypes.c_double, ctypes.c_double,  # lo, hi
            ctypes.c_int,                      # n
            _I32P,                             # out[n]
        ], None)
        self.ce_cur_env = _bind(dll, "ce_cur_env", [
            ctypes.c_int,                      # kind 0-8
            ctypes.c_double,                   # t
            ctypes.c_double, ctypes.c_double,  # lo, hi
            ctypes.c_double,                   # speed
            ctypes.c_double, ctypes.c_double,  # attack, decay
            ctypes.c_int,                      # steps
            ctypes.c_double,                   # spread
            ctypes.c_int,                      # seed
            ctypes.c_int,                      # n
            _I32P,                             # out[n]
        ], None)
        self.ce_cur_chase = _bind(dll, "ce_cur_chase", [
            ctypes.c_int,                      # kind 0-14
            ctypes.c_int,                      # dir_idx 0-3
            ctypes.c_double,                   # t
            ctypes.c_double, ctypes.c_double,  # lo, hi
            ctypes.c_double,                   # speed
            ctypes.c_double, ctypes.c_double,  # width, tail
            ctypes.c_int,                      # heads（仅 multihead）
            ctypes.c_int,                      # n
            _I32P,                             # out[n]
        ], None)
        self.ce_cur_rand = _bind(dll, "ce_cur_rand", [
            ctypes.c_int,                      # kind 0-8
            ctypes.c_double,                   # t
            ctypes.c_double, ctypes.c_double,  # lo, hi
            ctypes.c_double,                   # speed
            ctypes.c_double,                   # density
            ctypes.c_int,                      # seed
            ctypes.c_int,                      # n
            _I32P,                             # out[n]
        ], None)
        self.ce_cur_breathe = _bind(dll, "ce_cur_breathe", [
            ctypes.c_int,                      # kind 0-2
            ctypes.c_double,                   # t
            ctypes.c_double, ctypes.c_double,  # lo, hi
            ctypes.c_double,                   # speed
            ctypes.c_double,                   # spread
            ctypes.c_int,                      # n
            _I32P,                             # out[n]
        ], None)
        self.ce_cur_color = _bind(dll, "ce_cur_color", [
            ctypes.c_int,                      # kind 0-13
            ctypes.c_double,                   # t
            ctypes.c_double, ctypes.c_double,  # speed, sat
            ctypes.c_double,                   # bright
            ctypes.c_double, ctypes.c_double,  # freq, spread
            ctypes.c_int,                      # seed
            ctypes.c_double, ctypes.c_double,  # k_lo, k_hi
            ctypes.c_double, ctypes.c_double,  # hue_a, hue_b
            ctypes.c_int,                      # n
            _I32P, _I32P, _I32P,               # out_r, out_g, out_b
        ], None)
        # R172 W2b 三族新效果（对齐 ce_engine.c ce_cur_randrun/varyspeed/fcdc）
        self.ce_cur_randrun = _bind(dll, "ce_cur_randrun", [
            ctypes.c_int,                      # kind（0-14 v1; 15-30 E1 RR2）
            ctypes.c_double, ctypes.c_double, ctypes.c_double,  # t, lo, hi
            ctypes.c_double, ctypes.c_double, ctypes.c_double,
            ctypes.c_double,                   # rate, proximity, phase_gap, decay
            ctypes.c_int, ctypes.c_int,        # run_size, seed
            ctypes.c_int, ctypes.c_int,        # dmode 0/1/2, bias 0/1/2
            ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                               # soft, flick, slide, mirror, punch, heads
            ctypes.c_double, ctypes.c_double,  # density, mod_depth（E1）
            ctypes.c_double, ctypes.c_int,     # prox_raw, has_prox（E1 RR2 链）
            ctypes.c_int,                      # n
            _I32P,                             # out[n]
        ], None)
        self.ce_cur_varyspeed = _bind(dll, "ce_cur_varyspeed", [
            ctypes.c_int, ctypes.c_int, ctypes.c_int,  # look, lv, wshape
            ctypes.c_double, ctypes.c_double, ctypes.c_double,  # t, lo, hi
            ctypes.c_double, ctypes.c_double, ctypes.c_double,
            ctypes.c_double, ctypes.c_double, ctypes.c_int,
                                               # center, swing, cycle, uni, vfloor, sshape
            ctypes.c_double, ctypes.c_double, ctypes.c_double,
            ctypes.c_double, ctypes.c_double, ctypes.c_double,
            ctypes.c_int,                      # freq, duty, width, tail, spread, density, seed
            ctypes.c_int,                      # n
            _I32P,                             # out[n]
        ], None)
        self.ce_cur_fcdc = _bind(dll, "ce_cur_fcdc", [
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                               # kind, front, arch, ret
            ctypes.c_double, ctypes.c_double, ctypes.c_double,  # t, lo, hi
            ctypes.c_double, ctypes.c_double, ctypes.c_double,
            ctypes.c_double,                   # speed, pause, width, accel
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                               # handoff, oneway, seed, groups
            ctypes.c_int,                      # n
            _I32P,                             # out[n]
        ], None)
        self.ce_wave_rainbow = _bind(dll, "ce_wave_rainbow", [
            ctypes.c_double, ctypes.c_double,  # t, speed
            ctypes.c_double,                   # phase_step
            ctypes.c_double,                   # brightness
            ctypes.c_int,                      # n
            _I32P, _I32P, _I32P,               # out_r, out_g, out_b
        ], None)
        self.ce_wave_dual = _bind(dll, "ce_wave_dual", [
            ctypes.c_double, ctypes.c_double,  # t, speed
            ctypes.c_double,                   # phase_step
            ctypes.c_double, ctypes.c_double,  # lo, hi
            ctypes.c_int,                      # n
            _I32P,                             # out[n]
        ], None)
        # ── T10c: voice_clip_renderer 离线渲染数值内核（纯计算段, 不接 hook）──
        _IP = ctypes.POINTER(ctypes.c_int)
        self.ce_vc_clamp8 = _bind(dll, "ce_vc_clamp8", [ctypes.c_double], ctypes.c_int)
        self.ce_vc_round_even = _bind(dll, "ce_vc_round_even", [ctypes.c_double], ctypes.c_int64)
        self.ce_vc_envelope = _bind(dll, "ce_vc_envelope", [
            ctypes.c_double,          # t_frac
            ctypes.c_int,             # mode 0=none 1=pulse 2=swell
            ctypes.c_double,          # rate
        ], ctypes.c_double)
        self.ce_vc_speed_to_cycles = _bind(dll, "ce_vc_speed_to_cycles", [
            ctypes.c_double,          # speed_dmx
            ctypes.c_double,          # duration
        ], ctypes.c_double)
        self.ce_vc_pulse_rate = _bind(dll, "ce_vc_pulse_rate", [ctypes.c_double], ctypes.c_double)
        self.ce_vc_hsv_to_rgb = _bind(dll, "ce_vc_hsv_to_rgb", [
            ctypes.c_double, ctypes.c_double, ctypes.c_double,  # h, s, v
            _IP, _IP, _IP,                                     # r, g, b 输出
        ], None)
        self.ce_vc_rgb_frame = _bind(dll, "ce_vc_rgb_frame", [
            ctypes.c_double, ctypes.c_int, ctypes.c_int,   # level, v, color_mode
            ctypes.c_double, ctypes.c_double, ctypes.c_double,  # base_hue, hue_span, t_frac
            ctypes.c_int, ctypes.c_int,                    # seq_i, nseq
            _IP, _IP, _IP,                                  # r, g, b 输出
        ], None)
        # ── T8续: 编曲器 preset 池选择（voice_model._pick_preset_from_pool
        #    纯数学段; 不接 hook, 合入由主分支统一做）──
        self.ce_preset_key_hash = _bind(dll, "ce_preset_key_hash", [
            ctypes.POINTER(ctypes.c_uint32), ctypes.c_int,  # key_cp, key_len
            ctypes.c_int,                    # tier 0/1/2（能量档）
            ctypes.c_longlong,               # block_ordinal
        ], ctypes.c_longlong)
        self.ce_preset_pool_pick = _bind(dll, "ce_preset_pool_pick", [
            ctypes.POINTER(ctypes.c_uint32), ctypes.c_int,  # key_cp, key_len
            ctypes.c_int,                    # tier
            ctypes.c_longlong,               # block_ordinal
            ctypes.c_int,                    # skel_n（骨架数）
            _P8,                             # pool_valid [pool_len]
            _P8,                             # pool_skel [pool_len]
            _P8,                             # pool_prev [pool_len]
            ctypes.c_int,                    # pool_len
        ], ctypes.c_int)
        # ── T10: TopView 几何数学（对拍 src/ui/topview_autogroup.py 纯数学段）──
        _DP = ctypes.POINTER(ctypes.c_double)
        _IP32 = ctypes.POINTER(ctypes.c_int32)
        self.ce_circle_from_3 = _bind(dll, "ce_circle_from_3", [
            ctypes.c_double, ctypes.c_double,   # p1 (ax, ay)
            ctypes.c_double, ctypes.c_double,   # p2 (bx, by)
            ctypes.c_double, ctypes.c_double,   # p3 (cx, cy)
            _DP, _DP, _DP,                      # 输出 ux, uy, r
        ], ctypes.c_int)
        self.ce_ang_norm = _bind(dll, "ce_ang_norm", [ctypes.c_double], ctypes.c_double)
        self.ce_ang_diff = _bind(dll, "ce_ang_diff", [ctypes.c_double, ctypes.c_double], ctypes.c_double)
        self.ce_ellipse_angle = _bind(dll, "ce_ellipse_angle", [
            ctypes.c_double, ctypes.c_double,   # 点 (x, y)
            ctypes.c_double, ctypes.c_double,   # 圆心 (cx, cy)
            ctypes.c_double, ctypes.c_double,   # 半径 (rx, ry)
        ], ctypes.c_double)
        self.ce_assign_shapes = _bind(dll, "ce_assign_shapes", [
            _DP, _DP, ctypes.c_int,             # xs, ys, n_pts
            _DP, _DP, _DP, _DP, ctypes.c_int,   # scx, scy, srx, sry, n_shapes
            _DP, _DP, _DP, _IP32, _IP32, _DP,   # 输出 cx/cy/r/count/midx/mang
        ], ctypes.c_int)
        self.ce_ransac_circles = _bind(dll, "ce_ransac_circles", [
            _DP, _DP, ctypes.c_int,             # xs, ys, n
            ctypes.c_longlong,                  # seed（核内 & 0x7FFFFFFF）
            ctypes.c_int, ctypes.c_int,         # min_pts, iters
            _DP, _DP, _DP, _IP32, _IP32, _DP,   # 输出 cx/cy/r/count/midx/mang
        ], ctypes.c_int)
        self.ce_circle_axis_order = _bind(dll, "ce_circle_axis_order", [_DP, _DP, ctypes.c_int, _IP32], ctypes.c_int)
        self.ce_circle_window_order = _bind(dll, "ce_circle_window_order", [
            _DP,                                # 成员角度 angs[]
            ctypes.POINTER(ctypes.c_uint8),     # scope_flags[] 或 NULL
            ctypes.c_int,                       # n_members
            ctypes.c_double, ctypes.c_double,   # center_phase, half_width
            _IP32,                              # 输出成员索引
        ], ctypes.c_int)

        # P2a: 直连访问统一改走映射（导出已混淆为不透明名；_bind 兼容双轨）
        self.ce_defaults_base = _bind(dll, "ce_defaults_base", [
            _P8,                              # data [n_univ*512] u8
            _P8,                              # active [n_univ*512] u8 0/1
            ctypes.POINTER(ctypes.c_float),   # base_val [n_univ*512] f32 输出
            _P8,                              # base_active [n_univ*512] u8 输出
            _P8,                              # default_mask [n_univ*512] u8 输出
            ctypes.c_int,                     # n_univ
        ], None)
        # ── T8: _channel_defaults 通道默认值表（构建 + BASE LTP 消费）──────
        self.ce_defaults_build = _bind(dll, "ce_defaults_build", [
            _DP,                              # addrs [n] f64
            _DP,                              # vals [n] f64
            ctypes.c_int,                     # n
            _P8,                              # out_data [512] u8
            _P8,                              # out_active [512] u8 0/1
        ], ctypes.c_int)   # 1=有激活通道

    def _audit_missing(self):
        """M3 审计: 缺失符号 → warning 列全名（不再整引擎静默假死）。

        由 load_engine 在选定最终引擎上调用——sse2 探测实例不审计
        （其缺 ce_mix_clips_avx2 属设计, 非故障）。
        """
        # A1 修复（静态扫描 AUDIT_BUGS_SCAN.md）: 以下绑定曾在 return 后
        # 不可达（ce_pulse_effect/ce_stair_comet_effect 从未存在, 引用了
        # 未定义的 dll）→ 已移回可达位置、走 _bind 可选绑定（self.dll）。
        # 顺序铁律: 审计必须在全部绑定之后执行, 否则延迟绑定符号
        # （pulse/comet）在审计时尚未赋属性 → 恒误报缺失。
        self.ce_pulse_effect = _bind(self.dll, "ce_pulse_effect", [
            ctypes.c_double,                     # t
            ctypes.c_double,                     # speed (=rate)
            ctypes.c_int,                        # kind 0=dimmer_pulse 1=valve_pulse 2=pulse_burst
            ctypes.c_double, ctypes.c_double,    # width（脉宽/duty）, peak（峰值/open_val）
            ctypes.c_double,                     # base（基础值/close_val）
            ctypes.c_int,                        # pulses（仅 kind 2, max(1,·)）
            ctypes.c_double, ctypes.c_double,    # lo, hi（仅 kind 2）
            ctypes.c_int,                        # n
            ctypes.POINTER(ctypes.c_uint8),      # out[n]
        ], None)
        # ── T4续: K1 阶梯/彗星追逐族（对齐 effect_presets_v30.py
        #    _light_staircase_chase :252 / _light_comet_chase :291 /
        #    _light_dual_comet :329）──────────────────────────────
        self.ce_stair_comet_effect = _bind(self.dll, "ce_stair_comet_effect", [
            ctypes.c_double,                     # t
            ctypes.c_double,                     # speed
            ctypes.c_int,                        # kind 0=staircase 1=comet 2=dual_comet
            ctypes.c_int,                        # steps（仅 kind 0, max(2,·)）
            ctypes.c_double,                     # tail（仅 kind 1/2）
            ctypes.c_double, ctypes.c_double,    # lo, hi
            ctypes.c_int,                        # n
            ctypes.POINTER(ctypes.c_uint8),      # out[n]
        ], None)
        # ── Route Runner CE 覆盖内核 (ce_route_coverage) ─────────────
        # ctx = ce_route_ctx_create/destroy; paths_set 每路径累积弧长;
        # devices_project 每设备每路径 (最近弧长 s, 横向距离 d) 投影;
        # frame 每帧按 trains 输出 out_active[dev] uint8 强度 (按设备索引)。
        self.ce_route_ctx_create = _bind(self.dll, "ce_route_ctx_create",
                                         [], ctypes.c_void_p)
        self.ce_route_ctx_destroy = _bind(self.dll, "ce_route_ctx_destroy",
                                          [ctypes.c_void_p], None)
        self.ce_route_paths_set = _bind(self.dll, "ce_route_paths_set", [
            ctypes.c_void_p,                       # ctx
            ctypes.POINTER(ctypes.c_double),       # path_x [total_verts]
            ctypes.POINTER(ctypes.c_double),       # path_y [total_verts]
            ctypes.POINTER(ctypes.c_int),          # path_off [n_paths+1]
            ctypes.c_int,                          # n_paths
        ], ctypes.c_int)
        self.ce_route_devices_project = _bind(self.dll, "ce_route_devices_project", [
            ctypes.c_void_p,                       # ctx
            ctypes.POINTER(ctypes.c_double),       # dev_x [n_dev]
            ctypes.POINTER(ctypes.c_double),       # dev_y [n_dev]
            ctypes.c_int,                          # n_dev
        ], ctypes.c_int)
        self.ce_route_devices_get = _bind(self.dll, "ce_route_devices_get", [
            ctypes.c_void_p,                       # ctx
            ctypes.POINTER(ctypes.c_double),       # s_out [n_dev*max(1,n_paths)]
            ctypes.POINTER(ctypes.c_double),       # d_out [n_dev*max(1,n_paths)]
        ], ctypes.c_int)
        self.ce_route_frame = _bind(self.dll, "ce_route_frame", [
            ctypes.c_void_p,                       # ctx
            ctypes.POINTER(CE_Route_Train),        # trains [n_train]
            ctypes.c_int,                          # n_train
            ctypes.POINTER(ctypes.c_uint8),        # out_active [n_dev]
        ], ctypes.c_int)
        self.missing_symbols = tuple(
            sorted(n for n in _KNOWN_SYMBOLS if getattr(self, n, None) is None))
        if self.missing_symbols:
            log.warning("[CE] %s DLL 缺失符号 %d 个（使用该内核的 hook 将回退"
                        "原版, 请先 build.py all 重建）: %s",
                        self.variant, len(self.missing_symbols),
                        ", ".join(self.missing_symbols))
        return self.missing_symbols


    def __repr__(self):
        try:
            ver = self.version().decode("ascii") if self.version is not None else "?"
        except Exception:
            ver = "?"
        return f"<CEEngine variant={self.variant} ver={ver}>"


def _load_variant(variant: str):
    path = os.path.join(_HERE, f"ce_engine_{variant}.dll")
    if not os.path.exists(path):
        return None
    try:
        return ctypes.CDLL(path)
    except OSError as e:
        log.warning("[CE] 加载 %s 失败: %s", os.path.basename(path), e)
        return None


def load_engine() -> CEEngine | None:
    """按 CPU 能力选变体加载：先 SSE2 探测 AVX2（安全），再换 AVX2 DLL。

    M3: cpu_has_avx2 缺失（旧变体）→ 视为无 AVX2, 直接用 SSE2 变体;
    最终引擎上执行缺失符号审计（warning 列全名）。
    """
    sse2 = _load_variant("sse2")
    if sse2 is None:
        return None
    probe = CEEngine(sse2, "sse2")
    if probe.cpu_has_avx2 is not None and probe.cpu_has_avx2():
        avx2 = _load_variant("avx2")
        if avx2 is not None:
            engine = CEEngine(avx2, "avx2")
            engine._audit_missing()
            return engine
    probe._audit_missing()
    return probe


def load_or_none() -> CEEngine | None:
    """供 install() 使用：加载失败自动回退（返回 None 即走原路径）。"""
    try:
        return load_engine()
    except Exception as e:
        log.warning("[CE] 引擎加载失败，回退原路径: %s", e)
        return None
