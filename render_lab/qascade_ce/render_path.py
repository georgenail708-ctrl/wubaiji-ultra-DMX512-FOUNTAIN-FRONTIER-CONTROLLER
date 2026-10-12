"""P4b 替代渲染路径 — ce_render_position（简路径 C 融合 + 原版回退）。

★ R178（2026-09-28）退役：ce_render_position 不再接入生产（CLAUDE.md 10.3
「明确回退原版」—— 本复刻未跟上 Python `_render_position` 的 10 处语义改动,
示例工程 实时 A/B 30 帧数据 / 806 帧活跃掩码漂移）。保留仅供探针对照; C 同步后
重新接入须过门1/门2 + 示例工程 实时逐帧 A/B。P7 `ce_indexed_active_clip_items`
（本文件末尾）仍在生产使用, 不受影响。

与原版 PlaybackEngine._render_position（playback_engine.py:3275）字节等价：
- 前置检查：所有活跃 clip 满足简路径条件（无 fade/方向/顺序/电机/灯跟踪）
  → 走 C 融合（每 (clip,universe) 一次 ce_render_clip_frame）
- 任一不满足 → 委托原版方法（字节不变）
- 主循环/优先级/_resolve_candidate_layers 语义与原版一致（本文件为复刻，
  与原版同步修订；三扇门每轮验证漂移）
"""
from __future__ import annotations

import ctypes
import time
from typing import Any, Dict, List, Tuple

import numpy as np

_F = ctypes.POINTER(ctypes.c_float)
_D = ctypes.POINTER(ctypes.c_double)
_U = ctypes.POINTER(ctypes.c_uint8)

# P6 A/B 对比帧计数器（QASCADE_CE_P6=1 时每帧 +1）
_AB_FRAME = [0]
# P6 注册计数器（诊断: 注册应 ≈ 每 (clip,universe) 一次; 若每帧增长 = id(compiled) 不稳定）
_REG_CNT = [0]
# 管线第二批 hook 计数（诊断: 每帧增量 = hook 真实生效）
_ANCHOR_CNT = [0]
_RESOLVE_CNT = [0]
# T5-9 融合解析执行计数（诊断: QASCADE_CE_RESOLVE_MANY=1 时每帧 +1 = 融合路径
# 真实执行, 非静默回退旧链; 探针 ce_probe_hook_activation.py 检查）
_FUSED_CNT = [0]# 活性探针全扫计数（诊断: 每项 >0 = hook 真实执行, 非静默回退; 断言见
# bench/ce_probe_hook_activation.py）
_P6_BATCH_CNT = [0]     # P6: ce_render_frame 批量（candidate_clip_layers 直写）
_P8_CNT = [0]           # P8: ce_render_frame_parallel（Win32 持久线程池）
# A3: 全融合内核执行计数（诊断: QASCADE_CE_FUSED=1 时每帧 +1 = 融合路径
# 真实执行, 非静默回退旧链; 探针 bench/ce_fuzz_fused.py / 活性探针检查）
_A3_CNT = [0]
# H1: C 内核异常回退失败计数（AUDIT_BUGS_ACTIVE.md H1）——每个 hook 的
# except Exception（C 失败 → 回退原版）路径 +1; 探针断言 ==0（>0 = 静默
# 假死被抓住, 历史 T3/T4 类假死若重演将直接 FAIL 合入门禁）。
_CE_EXC_CNT = [0]
import os as _os
# 管线 A/B 开关（QASCADE_CE_PIPELINE_AB=1 时 resolve/anchor C vs 原版双算对比,
# 仅探针用——双倍计算成本, 生产勿开; 同 QASCADE_CE_P6_AB 纪律）
_PIPE_AB = _os.environ.get("QASCADE_CE_PIPELINE_AB", "0") == "1"


def _ce_clip_frame_attrs(self, clip):
    """帧级 per-clip 属性缓存（numpy 调用风暴/重复属性解析合并）。

    同一 clip 在 _can_use_simple_path + 任务构建 + _render_one + 批量循环中
    每帧被重复解析 fade/seq/motor/方向/rate 等属性（探针场景 438 次/帧
    方向规范化 + 292 次/帧 seq 详情）。播放期间属性不变 → 帧内一次算完
    缓存, ce_render_position 每帧重置。失败 → ("fail",)（调用方回退原逻辑）。
    """
    cache = getattr(self, "_ce_frame_attrs", None)
    if cache is None:
        cache = {}
        self._ce_frame_attrs = cache
    cid = id(clip)
    ent = cache.get(cid)
    if ent is None:
        try:
            fade_in_dur = max(0.0, float(getattr(clip, "fade_in", 0.0) or 0.0))
            fade_out_dur = max(0.0, float(getattr(clip, "fade_out", 0.0) or 0.0))
            duration_c = max(0.0, float(getattr(clip, "duration", 0.0)))
            fade_in_dir = self._get_clip_fade_in_direction(clip)
            fade_out_dir = self._get_clip_fade_out_direction(clip)
            seq_delay_in_c = max(0.0, float(getattr(clip, "seq_delay_in", 0.0) or 0.0))
            seq_delay_out_c = max(0.0, float(getattr(clip, "seq_delay_out", 0.0) or 0.0))
            seq_enabled = bool(self._clip_sequential_group_timing(clip)[0])
            motor_phase = float(self._clip_motor_phase_amount(clip))
            light_tracking = bool(getattr(clip, "light_tracking_enabled", False))
            record_source = bool(getattr(clip, "record_output_source_enabled", False))
            rate = float(self._clip_playback_rate(clip))
            precharge = bool(getattr(clip, "precharge_enabled", False))
        except Exception:
            ent = ("fail",)
        else:
            ent = (
                fade_in_dur, fade_out_dur, duration_c,
                fade_in_dir, fade_out_dir,
                seq_delay_in_c, seq_delay_out_c,
                seq_enabled, motor_phase,
                light_tracking, record_source, rate, precharge,
            )
        cache[cid] = ent
    return ent


def _can_use_simple_path(self, active_clip_items: list) -> bool:
    """简路径条件：允许无方向 fade（mode 0/1/2）；拒绝方向/顺序/电机相位/灯跟踪/录制源。

    属性解析走 _ce_clip_frame_attrs 帧级缓存（原每任务重复解析, 438 次/帧
    方向规范化 → 每 clip 一次; 判定条件逐字等价）。
    """
    # ── R14 保持消费语义 + M2 帧级门(2026-08-13)── 消费标记/扣除只发生在
    # 原版 Python 每项循环(_render_position)里, CE 简路径不经该循环, 故两类
    # 帧必须整帧回退原版(回退不改变输出 —— CE 本就是逐字节镜像, 只影响
    # 执行路径):
    #   ① 本帧存在「真实 hold 项且其消费掩码非空」(引擎侧帧级谓词
    #      _hold_consumed_active_in_items, 经 self 渲染上下文传入; 真实 hold
    #      判定与 playback_engine _dr_is_real_hold 同式, 桶 seam 项钉在
    #      duration-1e-4 天然排除): 该帧 Python 侧必对 hold item 扣已消费
    #      通道, CE 若接管则复活帧原样输出。消费为空时谓词 O(1) 短路 ——
    #      从未消费的帧恒 False, 路径与改动前逐字节相同(零影响铁律)。
    #   ② 真实 hold 项与其他活跃项同帧共存: 该帧正是覆写竞争帧, 必须在
    #      Python 侧产生消费标记(consumed |= active & ~effective); CE 若接管
    #      则标记永缺, 覆盖方离开后 CE 镜像仍输出复活帧(死灰复燃在 CE
    #      挂载下重演)。标记先于 resolve —— 标记帧输出逐字节不变, 扣除
    #      次帧生效; 次帧起 hold 项若仍活跃由门①(帧级)接管回退, 若不活跃
    #      掩码惰性, CE 安全接管。
    if self._hold_consumed_active_in_items(active_clip_items):
        return False
    _real_hold_count = 0
    for _start_t, _track_order, _track, clip, _lt, _st, _hold, _g, _unis in active_clip_items:
        ent = _ce_clip_frame_attrs(self, clip)
        if ent is None or ent[0] == "fail":
            return False
        if ent[3] != "none":
            return False
        if ent[4] != "none":
            return False
        if ent[5] > 1e-4:
            return False
        if ent[6] > 1e-4:
            return False
        if ent[7]:
            return False
        if ent[8] > 1e-6:
            return False
        if ent[9]:
            return False
        if ent[10]:
            return False
        if _hold and float(_lt) >= ent[2] - 1e-9:
            _real_hold_count += 1
    if _real_hold_count and len(active_clip_items) > 1:
        return False
    return True


def _ce_get_clip_anchor(self, clip, universe, ch_active, compiled):
    """T5: clip 锚点 → ce_clip_anchor（缓存语义与原版一致, min/max 走 C）。

    原版 _get_clip_anchor（playback_engine.py:7442）; C 内核 fuzz 10 万次
    位级一致（ce_fuzz_anchor.py）。compiled["frame_data"] 即 universe 过滤
    后的帧集, 与原版 clip.frames 逐帧过滤等价（min/max 与顺序无关）。
    失败/无引擎 → 回退原版方法（字节不变）。
    """
    cache_key = f"{clip.id}::{universe}"
    anchor = self._clip_anchor_cache.get(cache_key)
    if anchor is not None:
        return anchor
    _ce = getattr(self, "_ce_engine", None)
    if (_ce is not None and compiled is not None
            and getattr(_ce, "ce_clip_anchor", None) is not None):
        try:
            fd = compiled.get("frame_data")
            n = 0 if fd is None else int(fd.shape[0])
            anchor = np.empty(512, np.float32)
            _ce.ce_clip_anchor(
                (np.ascontiguousarray(fd, np.uint8).ctypes.data_as(_U)
                 if n > 0 else None),
                n,
                np.ascontiguousarray(ch_active, np.uint8).ctypes.data_as(_U),
                anchor.ctypes.data_as(_F))
            if _PIPE_AB:
                _ANCHOR_CNT[0] += 1
                ref = self._get_clip_anchor(clip, universe, ch_active)
                if not np.array_equal(anchor, ref):
                    _d = np.nonzero(anchor != ref)[0][:8]
                    print(f"[AB-DIFF] anchor clip={getattr(clip, 'label', clip)!r} "
                          f"u={universe} d={_d.tolist()}", flush=True)
                return ref      # 原版已写缓存, 值与 C 逐位相同
            self._clip_anchor_cache[cache_key] = anchor
            if len(self._clip_anchor_cache) > self._anchor_cache_cap:
                self._evict_oldest(self._clip_anchor_cache)
            return anchor
        except Exception:
            _CE_EXC_CNT[0] += 1   # H1: anchor C 内核异常 → 失败计数（回退原版）
            pass
    return self._get_clip_anchor(clip, universe, ch_active)


def _ce_resolve_python(u, candidates, clip_layers):
    """原版候选解析（isfinite 缓存版）——C 路径回退与 A/B 参照共用。"""
    best_scores = np.full(512, -np.inf, dtype=np.float64)
    _parsed = []
    for _data, active, _anchor, scores in candidates:
        fin = np.isfinite(scores)
        mask = active & fin
        if np.any(mask):
            np.maximum(best_scores, scores, out=best_scores, where=mask)
        _parsed.append((_data, active, _anchor, scores, fin))
    from src.engine.playback_engine import _frame_buffer_pool
    for data, active, anchor, scores, fin in _parsed:
        effective_active = active & fin & (scores >= best_scores - 1e-12)
        if not np.any(effective_active):
            continue
        data_eff = _frame_buffer_pool.copy_from(data)
        data_eff[~effective_active] = 0
        clip_layers.setdefault(int(u), []).append((data_eff, effective_active, anchor))


def _ce_resolve_candidates(self, u, candidates, clip_layers):
    """T5: 候选解析 → ce_resolve_candidates（C 内 best/掩码/清零, 一次调用）。

    原逻辑 _ce_resolve_python; C 内核 fuzz 10 万次位级一致（ce_fuzz_resolve.py）
    —— best 逐通道 max + effective 掩码 + data_eff 清零全部 C 内完成。
    任一失败 → 抛异常, 调用方回退原版。
    """
    _ce = getattr(self, "_ce_engine", None)
    if _ce is None or getattr(_ce, "ce_resolve_candidates", None) is None:
        # M3: 内核缺失 → 与引擎缺失同语义（调用方 try/except 回退原版解析）
        raise RuntimeError("CE 引擎缺失")
    n = len(candidates)
    if n <= 0:
        return
    # ── numpy 调用风暴合并: 原 3×np.stack+astype(每域 219 次/帧) + 2×empty +
    # 1×zeros(每域 146+73 次/帧) → 预分配池跨帧复用 + 行拷贝。值与
    # stack/astype 逐字节相同(uint8 直拷 / bool→0/1 / f64 直拷); C 内核
    # 同步读取, 域间/帧间复用安全(同 _mix_clips A3 预分配纪律)。
    cap = getattr(self, "_ce_res_cap", 0)
    if n > cap:
        self._ce_res_data = np.empty((n, 512), np.uint8)
        self._ce_res_act = np.empty((n, 512), np.uint8)
        self._ce_res_sc = np.empty((n, 512), np.float64)
        self._ce_res_cap = n
    data = self._ce_res_data[:n]
    active = self._ce_res_act[:n]
    scores = self._ce_res_sc[:n]
    for i, c in enumerate(candidates):
        data[i] = c[0]          # uint8 直拷(等价 astype(copy=False))
        active[i] = c[1]        # bool → 0/1(等价 astype(np.uint8))
        scores[i] = c[3]        # float64 直拷(等价 astype(copy=False))
    # C 输出缓冲不可池化: de 行视图被下游 clip_layers 跨域/跨帧持有,
    # 池复用会造成域间覆盖(门2 实证: 除最后域外全部全零); eff 经
    # astype(bool) 拷贝消费同样受覆盖影响 → 每域独立分配(与原语义一致);
    # cnts 同步消费, 独立分配最简。输入池化安全(C 内核同步读, 域间复用)。
    eff = np.empty((n, 512), np.uint8)
    de = np.empty((n, 512), np.uint8)
    cnts = np.zeros(n, np.int32)
    _ce.ce_resolve_candidates(
        n,
        data.ctypes.data_as(_U),
        active.ctypes.data_as(_U),
        scores.ctypes.data_as(_D),
        eff.ctypes.data_as(_U),
        de.ctypes.data_as(_U),
        cnts.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
        None)
    if _PIPE_AB:
        _RESOLVE_CNT[0] += 1
        _ref_layers: List[Tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        _ce_resolve_python(u, candidates, _ref_layers)
        _c_layers = [
            (de[l], eff[l].astype(bool))
            for l in range(n) if int(cnts[l]) > 0
        ]
        if len(_c_layers) != len(_ref_layers):
            print(f"[AB-DIFF] resolve u={u} 层数 C={len(_c_layers)} "
                  f"py={len(_ref_layers)}", flush=True)
        else:
            for l, ((c_data, c_act), (r_data, r_act, _r_anchor)) in enumerate(
                    zip(_c_layers, _ref_layers)):
                if not np.array_equal(c_data, r_data) or not np.array_equal(c_act, r_act):
                    _d = np.nonzero(c_data != r_data)[0][:8]
                    print(f"[AB-DIFF] resolve u={u} l={l} "
                          f"data_d={_d.tolist()} "
                          f"act_d={int((c_act != r_act).sum())}", flush=True)
    for l in range(n):
        if int(cnts[l]) <= 0:
            continue
        clip_layers.setdefault(int(u), []).append(
            (de[l], eff[l].astype(bool), candidates[l][2]))


def _ce_consume_batch_rows(self, batch, oi_arr, out_pool, act_pool, sc_pool):
    """P6 批量路径消费循环（候选构建, 逐位语义 = 原内联段 :548-565）。

    输出 candidate_clip_layers（universe → [(out_view, act_bool, anchor,
    scores+tie), ...]）; oi_arr[i]<0 与全 0 act 任务跳过（continue 语义）。
    T5-9 融合路径（_ce_consume_resolve_many）的 A/B 参照与回退共用。
    """
    candidate_clip_layers: Dict[int, List[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]] = {}
    n = len(batch)
    # ── numpy 调用风暴合并: 原每任务 astype(bool)+np.any(146+146 次/帧) →
    # 整池一次 view(bool)(0/1 字节 = bool 值, 零拷贝) + 一次行级 any(axis=1)。
    # 语义逐点一致: C 内核写 0/1; 任何非零 → True(与 astype(bool) 判真相同);
    # rule 合并(ra & act)只可能把 1 变 0, 故 row_any 为 0 的行必可跳过(与原
    # continue 一致), 为 1 的行仍需循环内 rule 后复检(与原分支一致)。
    act_all = act_pool.view(np.bool_).reshape(n, 512)
    row_any = act_all.any(axis=1)
    for i, (t, _idx, _fm) in enumerate(batch):
        if oi_arr[i] < 0:
            continue
        if not row_any[i]:
            continue
        clip, u, compiled = t[0], t[1], t[2]
        out = out_pool[i * 512:(i + 1) * 512]
        act = act_all[i]
        scores = sc_pool[i * 512:(i + 1) * 512].copy()
        ra = compiled.get("rule_active")
        if ra is not None:
            act = np.asarray(ra, dtype=bool) & act
            if not np.any(act):
                continue
        scores[act] = scores[act] + t[6] + t[12]
        anchor = _ce_get_clip_anchor(self, clip, u, act, compiled)
        candidate_clip_layers.setdefault(int(u), []).append(
            (out, act.copy(), anchor, scores))
    return candidate_clip_layers


def _ce_consume_resolve_many(self, batch, oi_arr, out_pool, act_pool, sc_pool,
                             clip_layers):
    """T5-9: P6 批量路径「消费循环+候选解析」全融合（一次 C 调用, 逐位一致）。

    ce_resolve_candidates_many 直读批量池行（out_pool/act_pool/sc_pool）:
      act' = act & rule（rule 字节非零即真, 同 np.asarray(rule, bool) & act）;
      sc   = scores + tie（f64 同序加法, 仅 act' 通道参与）;
      best = 逐通道 max(act' & isfinite(sc))（严格 >, 等值保持旧值）;
      eff  = act' & isfinite(sc) & (sc >= best - 1e-12);
      de   = eff ? data : 0;  count = popcount(eff)。
    组 = universe（首现序, 组内 = batch 扫描序 —— 与旧链候选插入序一致）;
    count==0 层跳过（同旧链 continue → 不 append）; anchor 用与旧链同值的
    act' 计算（缓存命中即同值; 未命中时同输入同输出）。
    与旧链（_ce_consume_batch_rows + _ce_resolve_candidates）逐位一致
    （fuzz render_lab/bench/ce_fuzz_resolve_many.py, 位级 10 万+ 次）。
    QASCADE_CE_PIPELINE_AB=1 时逐层对拍旧链（仅探针, 双倍计算成本）。
    任一失败 → 抛异常（调用方回退旧链）; 本函数全部计算完成后才写入
    clip_layers（异常不残留半途结果）。
    """
    _ce = getattr(self, "_ce_engine", None)
    if _ce is None or getattr(_ce, "ce_resolve_candidates_many", None) is None:
        # M3: 内核缺失 → 与引擎缺失同语义（调用方回退旧链）
        raise RuntimeError("CE 引擎缺失")
    C = ctypes
    n_batch = len(batch)
    if n_batch <= 0:
        return
    # ── 1) 组构建: universe 首现序, 组内 = batch 扫描序 ─────────────
    u_order: List[int] = []
    gi_of: Dict[int, int] = {}
    group_rows: List[List[int]] = []
    for i, (t, _idx, _fm) in enumerate(batch):
        if oi_arr[i] < 0:
            continue
        u = int(t[1])
        g = gi_of.get(u)
        if g is None:
            g = len(u_order)
            gi_of[u] = g
            u_order.append(u)
            group_rows.append([])
        group_rows[g].append(i)
    n_total = sum(len(g) for g in group_rows)
    if n_total <= 0:
        return
    flat_idx = [i for g in group_rows for i in g]
    ties_l = [float(batch[i][0][6]) + float(batch[i][0][12]) for i in flat_idx]
    # 规则指针缓存（规则对象常驻 → 指针稳定; 同 _ce_reg 缓存纪律）
    rule_cache = getattr(self, "_ce_rule_ptr_cache", None)
    if rule_cache is None:
        rule_cache = {}
        self._ce_rule_ptr_cache = rule_cache
    rules_l: List[ctypes.POINTER(ctypes.c_uint8)] = [None] * n_total
    for j, i in enumerate(flat_idx):
        compiled = batch[i][0][2]
        ra = compiled.get("rule_active") if compiled is not None else None
        if ra is not None:
            e = rule_cache.get(id(ra))
            if e is None or e[1] is not ra:
                rau8 = np.ascontiguousarray(ra, np.uint8)
                e = (rau8.ctypes.data_as(_U), rau8)
                rule_cache[id(ra)] = e
            rules_l[j] = e[0]
    group_off_l = [0]
    for g in group_rows:
        group_off_l.append(group_off_l[-1] + len(g))
    # ── 2) 输出缓冲（组序布局） + 一次 C 调用 ──────────────────────
    eff = np.empty((n_total, 512), np.uint8)
    de = np.empty((n_total, 512), np.uint8)
    cnts = np.zeros(n_total, np.int32)
    group_off = (C.c_int * len(group_off_l))(*group_off_l)
    flat = (C.c_int * n_total)(*flat_idx)
    ties = (C.c_double * n_total)(*ties_l)
    # T5-3 教训: 元素类型 = 单层指针 _U（LP_c_ubyte）, 数组整体按 POINTER(_U)
    # 传参（匹配 ce_resolve_candidates_many 的 rules 形参）; 双层指针必 TypeError
    rules = (_U * n_total)(*rules_l)
    _ce.ce_resolve_candidates_many(
        len(u_order), group_off, flat,
        out_pool.ctypes.data_as(_U), act_pool.ctypes.data_as(_U),
        sc_pool.ctypes.data_as(_D), ties, rules,
        eff.ctypes.data_as(_U), de.ctypes.data_as(_U),
        cnts.ctypes.data_as(ctypes.POINTER(ctypes.c_int)))
    # ── 3) 物化: count>0 行 → anchor（与旧链同 act' 值）+ append ───
    new_layers: Dict[int, List[Tuple[np.ndarray, np.ndarray, np.ndarray]]] = {}
    for g, u in enumerate(u_order):
        layers: List[Tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        for j in range(group_off_l[g], group_off_l[g + 1]):
            if int(cnts[j]) <= 0:
                continue
            i = flat_idx[j]
            t = batch[i][0]
            clip, compiled = t[0], t[2]
            act = act_pool[i * 512:(i + 1) * 512].astype(bool)
            ra = compiled.get("rule_active") if compiled is not None else None
            if ra is not None:
                act = np.asarray(ra, dtype=bool) & act
            anchor = _ce_get_clip_anchor(self, clip, u, act, compiled)
            layers.append((de[j], eff[j].astype(bool), anchor))
        if layers:
            new_layers[int(u)] = layers
    clip_layers.update(new_layers)
    # ── 4) 管线 A/B（仅探针）: 旧链逐层对拍 ─────────────────────────
    if _PIPE_AB:
        _RESOLVE_CNT[0] += 1
        ref: Dict[int, List[Tuple[np.ndarray, np.ndarray, np.ndarray]]] = {}
        _ref_cands = _ce_consume_batch_rows(
            self, batch, oi_arr, out_pool, act_pool, sc_pool)
        for u, cands in _ref_cands.items():
            try:
                _ce_resolve_candidates(self, u, cands, ref)
            except Exception:
                _ce_resolve_python(u, cands, ref)
        for u, c_layers in new_layers.items():
            r_layers = ref.get(u, [])
            if len(c_layers) != len(r_layers):
                print(f"[AB-DIFF] fused u={u} 层数 C={len(c_layers)} "
                      f"py={len(r_layers)}", flush=True)
                continue
            for (cd, ca, can), (rd, ra_, ran) in zip(c_layers, r_layers):
                if (not np.array_equal(cd, rd) or not np.array_equal(ca, ra_)
                        or not np.array_equal(can, ran)):
                    _d = np.nonzero(cd != rd)[0][:8]
                    print(f"[AB-DIFF] fused u={u} data_d={_d.tolist()} "
                          f"act_d={int((ca != ra_).sum())}", flush=True)
                    break
        for u in ref.keys():
            if u not in new_layers:
                print(f"[AB-DIFF] fused u={u} 旧链有层 C 无", flush=True)


def _ce_render_frame_fused_call(self, batch, ci_arr, un_arr, st_arr, cs_arr,
                                rt_arr, tn_arr, he_arr, ga_arr, fm_arr, n):
    """A3: 全融合 C 调用 —— 消费+解析+混合+打包一次 ctypes 完成。

    输入 = P6 注册循环产出的 batch（任务行）+ 已注册 ctx 槽; 输出 =
    outputs dict（universe → (out u8[512], act bool[512])）, 与旧链
    （P6 批量渲染 → 消费 → 融合解析 → 逐域 _mix_clips）逐字节一致:
      - 内核内部: ce_render_clip_frame 每任务 → 组序 resolve（rule 从 ctx
        槽取, tie 本函数传任务序数组）→ count>0 层压实 → ce_mix_clips_u8
        （锚点 = 注册期算入 ctx 槽, act' = base_active & rule 同值）;
      - 本函数: 1 次 ctypes + univ_off 偏移表 + 73 个 np.frombuffer 零拷贝
        视图（out/act 共用偏移, out[512]+act[512] 每域相邻）。
    tie 数组 = 任务序 priority tie（与 _ce_consume_resolve_many 的 ties_l
    同值: f64 逐位同序加法）。
    失败 → 抛异常（调用方回退旧链, _CE_EXC_CNT +1; 本函数在 C 调用全部
    完成前不写 outputs, 异常不残留半途结果）。
    """
    _ce = getattr(self, "_ce_engine", None)
    C = ctypes
    ties_arr = (C.c_double * n)(*[float(batch[i][0][6]) for i in range(n)])
    max_u = 0
    for (t, _idx, _fm) in batch:
        u = int(t[1])
        if u > max_u:
            max_u = u
    off_arr = (C.c_int * (max_u + 1))(*([-1] * (max_u + 1)))
    out_buf = np.empty(n * 1024, np.uint8)   # 每域 out[512]+act[512] 相邻
    act_buf = np.empty(n * 1024, np.uint8)
    mod_gain = float(self._modulation_gain)
    _ce.ce_render_frame_fused(
        getattr(_ce, "_ce_ctx", None),
        ci_arr, un_arr, st_arr, cs_arr, rt_arr, tn_arr,
        he_arr, ga_arr, fm_arr, n, ties_arr, mod_gain,
        out_buf.ctypes.data_as(_U), act_buf.ctypes.data_as(_U), off_arr)
    outputs: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}
    seen: set = set()
    for (t, _idx, _fm) in batch:
        u = int(t[1])
        if u in seen:
            continue
        seen.add(u)
        off = int(off_arr[u])
        if off < 0:
            continue
        outputs[u] = (
            np.frombuffer(out_buf, np.uint8, 512, offset=off),
            np.frombuffer(act_buf, np.uint8, 512, offset=off).view(np.bool_))
    return outputs


def ce_render_position(self, now: float, layer_name: str = "clip_playback", _orig=None):
    """P4b 替代版（R178 已退役, 不接入生产——见模块 docstring）。"""
    if not self._render_lock.acquire(blocking=False):
        return
    _lock_released = False
    _timing = _os.environ.get("QASCADE_CE_TIMING", "0") == "1"
    _seg = {}
    def _tick(name):
        if _timing:
            _seg[name] = time.perf_counter()
    def _seg_ms(a, b):
        return (b - a) * 1000.0 if _timing else 0.0
    try:
        # ★ 2026-08-06 修复: _fused_outputs 初始化上移到函数入口 —— 原初始化
        #   在 if batch: + P6 门控块(len(_tasks)>=2)内部, 单 clip/小任务时整块
        #   跳过 → 1024 行引用未初始化 → UnboundLocalError(cannot access local
        #   variable '_fused_outputs') → clip 渲染失败/预览消失/录制clip时间线
        #   卡死(用户实测 2026-08-06)。任何路径先置 None, 与 916/1024 引用安全。
        _fused_outputs = None
        # 帧级 per-clip 属性缓存: 每帧重建(clip 属性可能在两帧间被编辑);
        # _ce_clip_frame_attrs 惰性填充, 简路径检查/任务构建/渲染循环共享
        self._ce_frame_attrs = {}
        now = max(0.0, float(now))
        self._position = now
        if not bool(getattr(self, "_suppress_position_emit", False)):
            self.position_changed.emit(self._position)
        if self._render_from_timeline_frame_cache(now, layer_name):
            return

        pm = self._pm
        proj = getattr(pm, "project", None) or getattr(pm, "_project", None) if pm is not None else None
        tracks = list(getattr(proj, "tracks", []) or []) if proj is not None else []
        if not tracks or self._nm is None or not hasattr(self._nm, "update_layer"):
            self._clear_stale_timeline_layers(set(), layer_name=layer_name)
            self._last_record_source_outputs_by_universe = {}
            return

        _struct = self._render_struct_for_tracks(tracks)
        ordered_tracks = _struct["ordered_tracks"]
        track_by_id = _struct["track_by_id"]
        track_parent_map = _struct["track_parent_map"]
        render_tracks = list(ordered_tracks)
        any_solo = _struct["any_solo"]
        _folder_chain_cache = _struct["folder_chain_cache"]

        try:
            _timer_interval_s = float(self._timer.interval()) / 1000.0
        except Exception:
            _timer_interval_s = 0.0
        if _timer_interval_s < 1e-4:
            _timer_interval_s = 0.025
        _last_now = getattr(self, "_last_render_time", None)
        if _last_now is not None:
            try:
                _dt = float(now) - float(_last_now)
            except Exception:
                _dt = 0.0
            if 1e-4 <= _dt <= 0.2:
                _timer_interval_s = max(_timer_interval_s, _dt)
        _timer_interval_s = min(max(_timer_interval_s, 0.005), 0.08)
        _tick("start")
        _m2_items = getattr(self, "_m2_gate_items", None)
        if _m2_items is not None:
            # M2 帧级门否决时已构建活跃项(同 _indexed_active_clip_items/同
            # 输入 + 闩已推进), 复用免二次索引构造(CE P4b 与 Python 原版同)。
            self._m2_gate_items = None
            active_clip_items = _m2_items
        else:
            active_clip_items = self._indexed_active_clip_items(
                now, tracks, render_tracks, track_by_id, any_solo, _timer_interval_s)
        _tick("active_items")

        # ── P4b: 简路径前置检查（不满足 → 释放锁后原版）──────────────
        if not _can_use_simple_path(self, active_clip_items):
            _tick("simple_check")
            self._render_lock.release()
            _lock_released = True
            # R5: 已构建的 active_clip_items 直接传给原版 —— 原版会重复
            # 同一索引扫描(同函数/同输入, 结果逐项相同), 大时间线
            # hold 反向扫描 + 逐项 source/fade 链每 tick 双份纯浪费。
            return _orig(self, now, layer_name,
                         _prebuilt_active_items=active_clip_items)

        def _folder_chain(track) -> Tuple[str, ...]:
            tid = str(getattr(track, "id", "") or "")
            cached = _folder_chain_cache.get(tid)
            if cached is not None:
                return cached
            chain_rev: List[str] = []
            seen: set = set()
            if bool(getattr(track, "is_folder", False)) and tid:
                chain_rev.append(tid)
            pid = str(getattr(track, "parent_id", "") or "")
            while pid and pid not in seen:
                seen.add(pid)
                chain_rev.append(pid)
                p_track = track_by_id.get(pid)
                if p_track is None:
                    break
                pid = str(getattr(p_track, "parent_id", "") or "")
            chain = tuple(reversed(chain_rev))
            if tid:
                _folder_chain_cache[tid] = chain
            return chain

        from src.engine.playback_engine import _ClipSortKey
        _sort_sig = tuple(
            (float(_i[0]), int(_i[1]), id(_i[2]), tuple(_folder_chain(_i[2])))
            for _i in active_clip_items
        )
        # R178: 排序缓存三元组 (_active_sort_sig/_active_sort_cache/
        # _active_priority_cache) 与原版 playback_engine.py:4628-4647 同式维护。
        # 旧版只写前两项 → 其后原版回退帧命中签名时取到**别的活跃集**的
        # priority 表(缺键得 0) → tie 错 → 共享通道胜者翻转(示例工程 实测漂移)。
        total_priority = len(active_clip_items)
        if (getattr(self, "_active_sort_sig", None) == _sort_sig
                and self._active_sort_cache is not None
                and getattr(self, "_active_priority_cache", None) is not None):
            active_clip_items = [active_clip_items[i] for i in self._active_sort_cache]
            priority_by_clip = self._active_priority_cache
        else:
            _indexed = [
                (i, _ClipSortKey(item, _folder_chain))
                for i, item in enumerate(active_clip_items)
            ]
            _indexed.sort(key=lambda _t: _t[1])
            self._active_sort_sig = _sort_sig
            self._active_sort_cache = [i for i, _k in _indexed]
            active_clip_items = [active_clip_items[i] for i in self._active_sort_cache]
            priority_by_clip: Dict[Tuple[str, int, float], int] = {}
            for idx, item in enumerate(active_clip_items):
                # A2: id 字符串走引擎懒预缓存(值稳定, 免每帧 str 构建; 语义与
                # str(getattr(clip,"id","") or id(clip)) 逐字等价)
                clip_id = self._clip_cache_id(item[3])
                priority_by_clip[(clip_id, int(item[1]), float(item[0]))] = total_priority - idx
            self._active_priority_cache = priority_by_clip

        fixture_defs_by_id_global = self._fixture_defs_index(proj)
        self._precharge_scheduler.tick(now)

        candidate_clip_layers: Dict[int, List[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]] = {}
        _ce = getattr(self, "_ce_engine", None)

        # ── P5c: 帧级并行（ctypes 调用释放 GIL → C 内核真正多核并行）──
        # 146 个 (clip,universe) 任务完全独立（每任务独立输出缓冲），
        # 结果逐位相同（无共享可变状态），字节等价保持。
        _tick("simple_check")
        _tasks: List[tuple] = []
        for _start_t, _track_order, track, clip, local_t, source_t, hold_extension, gain, target_universes in active_clip_items:
            if bool(getattr(clip, "precharge_enabled", False)):
                if not self._precharge_scheduler.is_registered(str(getattr(clip, "id", "") or "")):
                    from src.engine.pump_precharge import find_pump_channels
                    pump_channels = find_pump_channels(clip, proj)
                    if pump_channels:
                        self._precharge_scheduler.register(clip, pump_channels)
            compiled_by_universe: dict = {}
            _fast_cache = self._clip_compiled_fast
            _clip_ref = clip
            _track_ref = track
            for _u in target_universes:
                _uni = int(_u)
                _fk = (id(_clip_ref), _uni, id(_track_ref) if _track_ref is not None else None)
                _fast = _fast_cache.get(_fk)
                if _fast is not None and _fast[0] is _clip_ref and _fast[1] is _track_ref:
                    compiled_by_universe[_uni] = _fast[2]
                else:
                    compiled_by_universe[_uni] = self._compiled_clip_universe(
                        _clip_ref, _uni, track=_track_ref, proj=proj,
                        fixture_defs_by_id=fixture_defs_by_id_global)
            clip_id = self._clip_cache_id(clip)
            priority = priority_by_clip.get((clip_id, int(_track_order), float(_start_t)), 0)
            tie = float(priority) * 1e9 + (float(_track_order) + 1.0) * 1e-3 + float(_start_t) * 1e-4
            # numpy 调用风暴合并: rate 走帧级 per-clip 缓存(简路径检查已填充,
            # 失败兜底回原函数 —— 值与原调用逐字节相同)
            _fa = _ce_clip_frame_attrs(self, clip)
            rate = (_fa[11] if _fa is not None and _fa[0] != "fail"
                    else self._clip_playback_rate(clip))
            # ★ 2026-08-13 PAR-1: 并发过渡得分提升镜像(Python b66b202)。
            # 淡入/淡出窗口内 clip 得分 +(total_priority+1)*1e9 → 共享通道
            # 上胜过非过渡并发 clip, 过渡帧不被 LTP winner-take-all 掩蔽
            # (修复前 CE 挂载下并发过渡帧丢失, 正是 b66b202 修过的用户可见
            # 缺陷)。判定与 playback_engine.py:3859/3868-3878 逐字同式
            # (_fa[0/1/2/4/6] = fade_in/fade_out/duration/fade_out_dir/
            # seq_delay_out 帧级缓存; hold_extension 对应 not hold_extension)。
            _boost = 0.0
            if _fa is not None and _fa[0] != "fail":
                _is_in_fade_in = (
                    (not hold_extension) and (_fa[0] > 1e-4)
                    and (local_t < _fa[0])
                )
                _is_in_fade_out = (
                    (not hold_extension) and (_fa[2] > 0.0)
                    and (
                        (_fa[1] > 1e-4 and local_t > _fa[2] - _fa[1])
                        or (
                            _fa[6] > 1e-4
                            and _fa[4] != "none"
                            and local_t > _fa[2] - (_fa[1] + _fa[6])
                        )
                    )
                )
                if _is_in_fade_in or _is_in_fade_out:
                    _boost = (float(total_priority) + 1.0) * 1e9
            for u in target_universes:
                _tasks.append((clip, int(u), compiled_by_universe.get(int(u)),
                               local_t, source_t, float(_start_t), tie, rate,
                               bool(hold_extension), float(gain), track, proj, _boost))

        _ce_fp = ctypes.POINTER(ctypes.c_float)
        _ce_up = ctypes.POINTER(ctypes.c_uint8)
        _ce_dp = ctypes.POINTER(ctypes.c_double)

        def _render_one(t):
            clip, u, compiled, local_t, source_t, start_t, tie, rate, hold_ext, gain, track, proj, boost = t
            if compiled is None:
                return None
            if getattr(_ce, "ce_render_clip_frame", None) is None:
                # M3: 内核缺失（DLL 未导出）→ 该任务无结果
                #（ce_render 顶部已整帧委托原版, 此为兜底）
                return None
            frame_times = compiled.get("frame_times")
            frame_data = compiled.get("frame_data")
            if frame_times is None or frame_data is None or len(frame_data) == 0:
                return None
            base_active = compiled.get("base_active")
            if base_active is None:
                base_active = self._clip_channel_active_mask(clip, u)
            base_active = np.ascontiguousarray(base_active, dtype=np.uint8)
            # 无方向 fade 的 mode 判定（对齐 :3467-3504 标量条件）
            # numpy 调用风暴合并: 时长属性走帧级 per-clip 缓存
            _fa = _ce_clip_frame_attrs(self, clip)
            if _fa is not None and _fa[0] != "fail":
                fade_in_dur, fade_out_dur, duration_c = _fa[0], _fa[1], _fa[2]
            else:
                fade_in_dur = max(0.0, float(getattr(clip, "fade_in", 0.0) or 0.0))
                fade_out_dur = max(0.0, float(getattr(clip, "fade_out", 0.0) or 0.0))
                duration_c = max(0.0, float(getattr(clip, "duration", 0.0)))
            fade_mode = 0
            prev_arr = target_arr = None
            if not hold_ext:
                if fade_in_dur > 1e-4 and local_t < fade_in_dur:
                    fade_mode = 1
                    prev_arr = self._get_fade_in_anchor(clip, u, track=track, proj=proj)
                elif (duration_c > 0.0 and fade_out_dur > 1e-4
                      and local_t > duration_c - fade_out_dur):
                    fade_mode = 2
                    target_arr = self._get_fade_out_target(u, proj=proj)
            # M5: anchor 缺失（_get_fade_in_anchor/_get_fade_out_target 返回
            # None）→ 按 fade_mode=0 处理（与 C 侧 ce_render_clip_frame
            # NULL 守卫一致: fade_mode=1/2 且 anchor NULL → v=r*gain）
            if (fade_mode == 1 and prev_arr is None) or (fade_mode == 2 and target_arr is None):
                fade_mode = 0
            out = np.empty(512, np.uint8)
            act = np.empty(512, np.uint8)
            scores = np.empty(512, np.float64)
            lc = compiled.get("last_change_time")
            idx = _ce.ce_render_clip_frame(
                np.ascontiguousarray(frame_times, np.float64).ctypes.data_as(_ce_dp),
                int(len(frame_data)),
                np.ascontiguousarray(frame_data, np.uint8).ctypes.data_as(_ce_up),
                (np.ascontiguousarray(lc, np.float32).ctypes.data_as(_ce_fp)
                 if lc is not None and len(lc) > 0 else None),
                float(source_t), float(start_t), float(rate), float(now),
                1 if hold_ext else 0, float(gain),
                base_active.ctypes.data_as(_ce_up),
                fade_mode,
                (np.ascontiguousarray(prev_arr, np.float32).ctypes.data_as(_ce_fp)
                 if prev_arr is not None else None),
                (np.ascontiguousarray(target_arr, np.float32).ctypes.data_as(_ce_fp)
                 if target_arr is not None else None),
                out.ctypes.data_as(_ce_up), act.ctypes.data_as(_ce_up),
                scores.ctypes.data_as(_ce_dp),
            )
            if idx < 0:
                return None
            active = act.astype(bool)
            if not np.any(active):
                return None
            rule_active = compiled.get("rule_active")
            if rule_active is not None:
                active = np.asarray(rule_active, dtype=bool) & active
                if not np.any(active):
                    return None
            return (u, out, active, scores, tie, boost, clip, compiled)

        # ── P6: C 持久上下文整帧调用（全部任务批量; P6b 起 fade 也并入）
        # 数据所有权在 C（ce_ctx 持有 clip 渲染数据 + fade anchor）; Python 每帧
        # 只传 ID 数组（~1KB）; 注册仅当 compiled/anchor 对象变化（id() 判等）;
        # C 内 OpenMP 按任务并行（P6c: install() 设 OMP_WAIT_POLICY=PASSIVE
        # 消除区域间自旋——ACTIVE 实测 spin 燃烧 +25-40ms/帧, PASSIVE 零燃烧）。
        # anchor 缺失的 fade 任务走 _render_one（与原版语义一致）。
        # 默认启用; QASCADE_CE_P6=0 可关（回 _render_one 串行路径）;
        # QASCADE_CE_P6_AB=1 时逐任务 A/B 对比（仅探针, 生产勿开——双倍渲染成本）。
        _results = []
        _ce_ctx = getattr(_ce, "_ce_ctx", None)
        _p6_on = _os.environ.get("QASCADE_CE_P6", "1") != "0"
        _p6_ab = _os.environ.get("QASCADE_CE_P6_AB", "0") == "1"
        # P8: Win32 持久线程池并行渲染（默认关, 门禁/探针显式开启;
        # 与 ce_render_frame 同签名同语义, 每任务独立输出缓冲 → 位级等价）
        _p8_render = (getattr(_ce, "ce_render_frame_parallel", None)
                      if _os.environ.get("QASCADE_CE_P8", "0") == "1"
                      else None)
        # P6 批量路径直接把结果写进 candidate_clip_layers（不进 _results）;
        # 该标志阻止下方 `if not _results:` 兜底对全部任务重跑（否则候选层双倍 → 哈希漂移）。
        _batch_consumed = False
        # T5-9: 融合解析输出（批量行先入; _results 兜底行后入, 域序/层序与旧链一致）
        clip_layers: Dict[int, List[Tuple[np.ndarray, np.ndarray, np.ndarray]]] = {}
        if (_p6_on and _ce_ctx is not None and len(_tasks) >= 2
                and getattr(_ce, "ce_clip_register", None) is not None
                and getattr(_ce, "ce_render_frame", None) is not None
                and getattr(_ce, "ce_clip_register_fade", None) is not None):
            C = ctypes
            _ce_reg = getattr(self, "_ce_clip_reg", None)
            if _ce_reg is None:
                _ce_reg = {}
                self._ce_clip_reg = _ce_reg
            # ── M2: 槽位复用状态（idx 自由表 + 单调分配游标 + 容量）────
            # 旧版 idx = len(_ce_reg) 只增不减: 删除 clip/换工程后旧键与 C 槽
            # 永久保留（每槽 ~405KB 会话驻留）; 唯一 (clip,u) 对超 1024 →
            # ce_clip_register 全量 -1 → 整帧回退 _render_one 性能悬崖。
            # 现在: 空闲槽优先复用（_ce_free_slots）; 内容变更同槽覆盖
            # （C 侧幂等覆盖语义, 不再新开槽泄漏旧槽）; 淘汰键在 C 侧
            # ce_clip_unregister 释放数据后回收 idx。
            _seq = getattr(self, "_ce_reg_seq", 0) + 1
            self._ce_reg_seq = _seq
            _free = getattr(self, "_ce_free_slots", None)
            if _free is None:
                _free = []
                self._ce_free_slots = _free
            _next_idx = getattr(self, "_ce_next_idx", 0)
            _cap = getattr(self, "_ce_ctx_cap", 0)
            if _cap <= 0:
                _cap = 1024
                if getattr(_ce, "ce_ctx_cap", None) is not None:
                    try:
                        _cap = int(_ce.ce_ctx_cap(_ce_ctx))
                    except Exception:
                        pass
                if _cap <= 0:
                    _cap = 1024
                self._ce_ctx_cap = _cap
            # M2: 周期淘汰（每 200 pass 一次, ~6.7s@30fps; 空闲键 = 近 2 pass
            # 未见的键 → 槽位回收, 会话内存有界于活动集 + 少量缓冲）
            if _seq % 200 == 0 and len(_ce_reg) > 0:
                _ce_sweep_slots(self, _seq)
            batch = []          # (task, clip_idx, fade_mode)
            for t in _tasks:
                clip, u, compiled, local_t, source_t, start_t, tie, rate, hold_ext, gain, track, proj, boost = t
                if compiled is None:
                    continue
                # numpy 调用风暴合并: 时长属性走帧级 per-clip 缓存
                _fa = _ce_clip_frame_attrs(self, clip)
                if _fa is not None and _fa[0] != "fail":
                    fade_in_dur, fade_out_dur, duration_c = _fa[0], _fa[1], _fa[2]
                else:
                    fade_in_dur = max(0.0, float(getattr(clip, "fade_in", 0.0) or 0.0))
                    fade_out_dur = max(0.0, float(getattr(clip, "fade_out", 0.0) or 0.0))
                    duration_c = max(0.0, float(getattr(clip, "duration", 0.0)))
                fm = 0
                prev_arr = target_arr = None
                if not hold_ext:
                    if fade_in_dur > 1e-4 and local_t < fade_in_dur:
                        fm = 1
                        prev_arr = self._get_fade_in_anchor(clip, u, track=track, proj=proj)
                    elif duration_c > 0.0 and fade_out_dur > 1e-4 and local_t > duration_c - fade_out_dur:
                        fm = 2
                        target_arr = self._get_fade_out_target(u, proj=proj)
                if fm != 0 and prev_arr is None and target_arr is None:
                    # anchor 缺失 → 走 _render_one（与原版语义一致, C 不访问 NULL）
                    _r = _render_one(t)
                    if _r is not None:
                        _results.append(_r)
                    continue
                key = (self._clip_cache_id(clip), u)
                ent = _ce_reg.get(key)
                cid = id(compiled)
                if ent is None or ent[1] != cid:
                    if ent is not None:
                        # M2: 内容变更（编译对象 id 变化）→ 同槽覆盖, 不再
                        # 新开槽（旧实现按 len(_ce_reg) 新开 → 旧槽永久泄漏）
                        idx = ent[0]
                    else:
                        idx = -1
                    ft = compiled.get("frame_times")
                    fd = compiled.get("frame_data")
                    if ft is None or fd is None or len(fd) == 0:
                        # M4: 注册数据缺失 → 该任务回退 _render_one
                        #（与 _render_one 的守卫对称, 不再让
                        #  ascontiguousarray(None) 异常逃出整帧）
                        _r = _render_one(t)
                        if _r is not None:
                            _results.append(_r)
                        continue
                    ft = np.ascontiguousarray(ft, np.float64)
                    fd = np.ascontiguousarray(fd, np.uint8)
                    lc = compiled.get("last_change_time")
                    ba = compiled.get("base_active")
                    if ba is None:
                        ba = self._clip_channel_active_mask(clip, u)
                    ra = compiled.get("rule_active")
                    if idx < 0:
                        # M2: 新键 → 空闲槽优先, 否则单调分配; 槽满先淘汰
                        if not _free and _next_idx >= _cap:
                            _ce_sweep_slots(self, _seq)
                        if _free:
                            idx = _free.pop()
                        elif _next_idx < _cap:
                            idx = _next_idx
                            _next_idx += 1
                            self._ce_next_idx = _next_idx
                        else:
                            # H2: 真并发 (clip,u) 对超槽（ce_ctx_create(cap)）
                            # → 该任务回退 _render_one（不注册不进 batch）;
                            # 不缓存负 idx（否则后续帧恒 idx=-1 → 任务静默丢失）。
                            _r = _render_one(t)
                            if _r is not None:
                                _results.append(_r)
                            continue
                    idx = _ce.ce_clip_register(
                        _ce_ctx, idx,
                        ft.ctypes.data_as(_D), int(ft.size),
                        fd.ctypes.data_as(_U),
                        (np.ascontiguousarray(lc, np.float32).ctypes.data_as(_F)
                         if lc is not None and len(lc) > 0 else None),
                        np.ascontiguousarray(ba, np.uint8).ctypes.data_as(_U),
                        1 if ra is not None else 0,
                        (np.ascontiguousarray(ra, np.uint8).ctypes.data_as(_U)
                         if ra is not None else None))
                    if idx < 0:
                        # H2: C 侧拒绝（越界/OOM）→ 回退 _render_one; 本轮
                        # 新分配的槽归还空闲池, 不泄漏。
                        if ent is None:
                            _free.append(idx)
                        _r = _render_one(t)
                        if _r is not None:
                            _results.append(_r)
                        continue
                    _ce_reg[key] = (idx, cid, _seq)
                    _REG_CNT[0] += 1
                else:
                    idx = ent[0]
                    if ent[2] != _seq:
                        _ce_reg[key] = (idx, cid, _seq)
                if fm != 0:
                    # P6b: fade anchor 注册（prev/target 缓存稳定 → id() 判等注册一次）
                    _anchor_reg = getattr(self, "_ce_anchor_reg", None)
                    if _anchor_reg is None:
                        _anchor_reg = {}
                        self._ce_anchor_reg = _anchor_reg
                    aent = _anchor_reg.get(key)
                    apid = id(prev_arr) if prev_arr is not None else None
                    atid = id(target_arr) if target_arr is not None else None
                    if aent is None or aent[0] != apid or aent[1] != atid:
                        _ce.ce_clip_register_fade(
                            _ce_ctx, idx,
                            (np.ascontiguousarray(prev_arr, np.float32).ctypes.data_as(_F)
                             if prev_arr is not None else None),
                            (np.ascontiguousarray(target_arr, np.float32).ctypes.data_as(_F)
                             if target_arr is not None else None))
                        _anchor_reg[key] = (apid, atid)
                batch.append((t, idx, fm))
            if batch:
                _batch_consumed = True
                n = len(batch)
                ci_arr = (C.c_int * n)(*[b[1] for b in batch])
                un_arr = (C.c_int * n)(*[b[0][1] for b in batch])
                st_arr = (C.c_double * n)(*[b[0][4] for b in batch])
                cs_arr = (C.c_double * n)(*[b[0][5] for b in batch])
                rt_arr = (C.c_double * n)(*[b[0][7] for b in batch])
                tn_arr = (C.c_double * n)(*([float(now)] * n))
                he_arr = (C.c_int * n)(*[1 if b[0][8] else 0 for b in batch])
                ga_arr = (C.c_float * n)(*[b[0][9] for b in batch])
                fm_arr = (C.c_int * n)(*[b[2] for b in batch])
                oi_arr = (C.c_int * n)()
                out_pool = np.empty(n * 512, np.uint8)
                act_pool = np.empty(n * 512, np.uint8)
                sc_pool = np.empty(n * 512, np.float64)
                _tick("tasks_build")
                # ── A3: 全融合内核（默认关, QASCADE_CE_FUSED=1 开启）──
                # 一次 C 调用完成「渲染+解析+混合+打包」: 消 ~300 次 numpy
                # 中间调用 + 73 次逐域 mix 内核 + 多段 ctypes; 输出域主序
                # 连续缓冲 + 73 个 frombuffer 零拷贝视图。
                # 门控: 需 P6 上下文与内核; 且无 _results 兜底行（fade
                # anchor 缺失 / M4 缺数据 / H2 槽溢出回退任务时禁用 ——
                # _mix_clips 非结合, 融合混合结果无法与兜底行再混, 混合
                # 不可分割）。失败 → 回退旧链（H1 失败计数）。
                _fused_outputs = None
                if (not _results
                        and _os.environ.get("QASCADE_CE_FUSED", "0") == "1"
                        and getattr(_ce, "ce_render_frame_fused", None) is not None):
                    try:
                        _fused_outputs = _ce_render_frame_fused_call(
                            self, batch, ci_arr, un_arr, st_arr, cs_arr,
                            rt_arr, tn_arr, he_arr, ga_arr, fm_arr, n)
                    except Exception:
                        # H1: A3 内核异常 → 失败计数（异常上抛语义不变,
                        # 回退旧链; 融合调用完成前不写 outputs, 无残留）
                        _CE_EXC_CNT[0] += 1
                        _fused_outputs = None
                    if _fused_outputs is not None:
                        _A3_CNT[0] += 1
                if _fused_outputs is None:
                    if _p8_render is not None:
                        try:
                            _p8_render(
                                _ce_ctx, ci_arr, un_arr, st_arr, cs_arr, rt_arr, tn_arr,
                                he_arr, ga_arr, fm_arr, n,
                                oi_arr, out_pool.ctypes.data_as(_U), act_pool.ctypes.data_as(_U),
                                sc_pool.ctypes.data_as(_D))
                        except Exception:
                            # H1: P8 C 内核异常 → 失败计数（异常照常上抛, 语义不变）
                            _CE_EXC_CNT[0] += 1
                            raise
                        _P8_CNT[0] += 1
                    else:
                        try:
                            _ce.ce_render_frame(
                                _ce_ctx, ci_arr, un_arr, st_arr, cs_arr, rt_arr, tn_arr,
                                he_arr, ga_arr, fm_arr, n,
                                oi_arr, out_pool.ctypes.data_as(_U), act_pool.ctypes.data_as(_U),
                                sc_pool.ctypes.data_as(_D))
                        except Exception:
                            # H1: P6 C 内核异常 → 失败计数（异常照常上抛, 语义不变）
                            _CE_EXC_CNT[0] += 1
                            raise
                        _P6_BATCH_CNT[0] += 1
                    _tick("ce_render")
                    if _p6_ab:
                        _AB_FRAME[0] += 1
                        for i, (t, _idx, _fm) in enumerate(batch):
                            r = _render_one(t)
                            if r is None:
                                continue
                            u2, out2, act2, sc2, tie2, _b2, clip2, _c2 = r
                            o1 = out_pool[i*512:(i+1)*512]
                            a1 = act_pool[i*512:(i+1)*512].astype(bool)
                            s1 = sc_pool[i*512:(i+1)*512]
                            if not np.array_equal(o1, out2) or not np.array_equal(a1, act2) or not np.array_equal(s1, sc2, equal_nan=True):
                                _d = np.nonzero(o1 != out2)[0][:8]
                                print(f"[AB-DIFF] frame={_AB_FRAME[0]} t={i} u={t[1]} clip={getattr(t[0], 'label', t[0])!r} out_d={_d.tolist()} out_b={[int(o1[j]) for j in _d]}!={[int(out2[j]) for j in _d]} act_d={int((a1!=act2).sum())} sc_d={int((s1!=sc2).sum())}", flush=True)
                    # M2 修复（溢出 A/B 根因, ce_probe_p6_overflow_ab 基线 FAIL）:
                    # 旧内联循环在 sc_pool 上原地 += tie（sc2 是视图）→ 消费路径
                    # （_ce_consume_batch_rows / 融合 C 内核）各自再加一次 tie →
                    # 批量行 2×tie vs 回退行/P6-off 1×tie → >1024 回退混合时逐通道
                    # 胜者翻转 → 输出漂移。tie 统一只由消费路径各加一次（sc_pool
                    # 保持原始分, 与 _render_one 的 scores[active]+=tie 同源）。
                    # 内联消费循环在非融合路径被 consume 结果整体替换（死代码）,
                    # 融合路径下与融合调用双重解析（clip_layers 双倍层）→ 删除;
                    # 融合/非融合二选一构建候选层, 语义与 P9 注释「批量行走融合,
                    # 主循环只处理 _results 兜底行」一致。
                    _batch_fused = False
                    if _os.environ.get("QASCADE_CE_RESOLVE_MANY", "0") == "1":
                        if getattr(_ce, "ce_resolve_candidates_many", None) is not None:
                            try:
                                _ce_consume_resolve_many(
                                    self, batch, oi_arr, out_pool, act_pool,
                                    sc_pool, clip_layers)
                                _batch_fused = True
                                _FUSED_CNT[0] += 1
                            except Exception:
                                _CE_EXC_CNT[0] += 1   # H1: 融合解析 C 内核异常 → 失败计数
                                _batch_fused = False
                    if not _batch_fused:
                        candidate_clip_layers = _ce_consume_batch_rows(
                            self, batch, oi_arr, out_pool, act_pool, sc_pool)
        else:
            for t in _tasks:
                r = _render_one(t) if _tasks else None
                if r is not None:
                    _results.append(r)
        if not _results and not _batch_consumed:
            _results = [(_render_one(t) if _tasks else None) for t in _tasks] if _tasks else []

        for r in _results:
            if r is None:
                continue
            u, out, active, scores, tie, boost, clip, compiled = r
            if active.dtype != np.bool_:
                active = active.astype(bool)
            if not np.any(active):
                continue
            scores[active] = scores[active] + tie + boost
            anchor = _ce_get_clip_anchor(self, clip, u, active, compiled)
            candidate_clip_layers.setdefault(int(u), []).append(
                (out, active.copy(), anchor, scores))

        # 注: 旧串行主循环已由 _render_one 并行化取代（P5c）；以下为原串行段保留参考。
        _tick("consume")
        # ── 候选解析 + 混合 + 写层（复刻 _resolve_candidate_layers）────
        # T5-6: isfinite 每候选层只算一次（原 best/effective 各算一次）;
        # np.maximum(where=) 语义与原掩码赋值逐位一致（out 保留 where=False 位）。
        # T5-7: C 内核 ce_resolve_candidates（一次调用完成 best/掩码/清零,
        # fuzz 10 万次位级一致）; 任一失败 → _ce_resolve_python 原版回退。
        # T5-9: 批量行已在融合路径（_ce_consume_resolve_many）解析 → 此处只
        # 处理 _results 兜底行的候选; 非融合时 candidate_clip_layers 含全部。
        for u, candidates in candidate_clip_layers.items():
            try:
                _ce_resolve_candidates(self, u, candidates, clip_layers)
            except Exception:
                _ce_resolve_python(u, candidates, clip_layers)
        _tick("resolve")

        # ── 帧合成（逐 universe 走 _mix_clips/CE 内核; A3 融合路径在 C 内
        # 已混合, outputs 直接物化）───────────────────────────────────
        # P4e 批量接入实测负优化（门3 中位 15→35ms）——OpenMP 并行区+指针数组
        # 构建开销 > 小负载收益（与 P2 合成场景差异: 真实每域独立数组 cache 不友好）。
        # 已回退；批量 API 保留（K2/离线烘焙场景再用）。
        # A3: 混合不可分割（_mix_clips 非结合, 融合结果无法与兜底行再混）→
        # 融合/旧链二选一, 不双跑。
        if _fused_outputs is not None:
            outputs = _fused_outputs
        else:
            outputs = {}
            for u, items in clip_layers.items():
                if not items:
                    continue
                out_data, out_active = self._mix_clips(items)
                outputs[u] = (out_data, out_active)
        _tick("mix_clips")

        self._last_record_source_outputs_by_universe = {}

        # ── 写层 + flush（复刻原版 :3655-3718，简路径无 record_source）────
        active_universes = set()
        changed_universes = set()
        for u, (data, active) in outputs.items():
            if not np.any(active):
                continue
            active_universes.add(int(u))

        queued_worker_frame = False
        prev_universes = set(
            (getattr(self, "_last_output_universes_by_layer", {}) or {}).get(layer_name, set())
        )
        stale_universes = prev_universes - active_universes
        if (
            layer_name == "clip_playback"
            and bool(getattr(self._nm, "_running", False))
            and bool(getattr(self._nm, "_output_worker_enabled", False))
            and not bool(getattr(self, "_timeline_render_cache_building_lock", False))
            and hasattr(self._nm, "queue_clip_layer_frame")
        ):
            try:
                changed_universes = set(
                    self._nm.queue_clip_layer_frame(
                        layer_name, outputs, clear_universes=stale_universes)
                    or set()
                )
                queued_worker_frame = True
            except Exception:
                queued_worker_frame = False

        if not queued_worker_frame:
            for u, (data, active) in outputs.items():
                # numpy 调用风暴合并: 活跃域已在上方循环经 np.any 判定入集,
                # 帧内数据不变 → 直接查集替代 73 次/帧重复 np.any
                if u not in active_universes:
                    continue
                try:
                    changed = self._nm.update_layer(layer_name, int(u), data, active=active)
                except TypeError:
                    changed = self._nm.update_layer(layer_name, int(u), data)
                if changed is not False:
                    changed_universes.add(int(u))
        _tick("write_layer")

        if getattr(self, "_ltp_takeover_pending", False):
            self._ltp_takeover_pending = False
            if hasattr(self._nm, "notify_clip_takeover"):
                for u, (data, active) in outputs.items():
                    if np.any(active):
                        try:
                            self._nm.notify_clip_takeover(int(u), active)
                        except Exception:
                            pass

        if queued_worker_frame:
            layer_map = getattr(self, "_last_output_universes_by_layer", {}) or {}
            layer_map[layer_name] = set(active_universes)
            self._last_output_universes_by_layer = layer_map
            self._last_output_universes = (set().union(*layer_map.values())
                                           if layer_map else set(active_universes))
        else:
            self._clear_stale_timeline_layers(active_universes, layer_name=layer_name)
        # P9（stage C）: 异步 flush 武装窗口 — 仅 CE 渲染路径的 flush 走异步
        # （QASCADE_CE_FLUSH_ASYNC=1 且 send_pipeline 已安装时, flush 类级包装
        # 检查该 attr; 未安装时无人读取, 设置无害）; 其它调用方（25ms 混音器/
        # 心跳）在窗口外调用 → 恒串行。帧戳（AB 探针打标）由探针设
        # nm._ce_flush_frame_no, 本路径不覆盖。
        _nm_f = self._nm
        _nm_f._ce_flush_async_armed = True
        try:
            self._flush_timeline_output_now(changed_universes, layer_name=layer_name)
        finally:
            _nm_f._ce_flush_async_armed = False
        self._last_render_time = now
        if _timing:
            _tick("flush")
            _s = _seg
            _t0 = _s.get("start", 0.0)
            _segs = [
                ("indexed_active", _s.get("active_items", _t0)),
                ("简路径检查", _s.get("simple_check", _s.get("active_items", _t0))),
                ("任务构建+打包", _s.get("tasks_build", _s.get("simple_check", _t0))),
                ("C批量渲染", _s.get("ce_render", _s.get("tasks_build", _t0))),
                ("消费循环", _s.get("consume", _s.get("ce_render", _t0))),
                ("候选解析", _s.get("resolve", _s.get("consume", _t0))),
                ("mix_clips", _s.get("mix_clips", _s.get("resolve", _t0))),
                ("update_layer", _s.get("write_layer", _s.get("mix_clips", _t0))),
                ("flush", _s.get("flush", _s.get("write_layer", _t0))),
            ]
            _prev = _t0
            _parts = []
            for _label, _ts in _segs:
                _parts.append(f"{_label}={_seg_ms(_prev, _ts):.2f}ms")
                _prev = _ts
            print(f"[TIMING] 整帧={_seg_ms(_t0, _s.get('flush', _t0)):.2f}ms | " + " | ".join(_parts), flush=True)
    finally:
        if not _lock_released:
            self._render_lock.release()


# ── M2: P6 注册槽复用 — 淘汰/释放/回收 ─────────────────────────
# 触发: (a) 新键注册且槽满（_ce_sweep_slots 在分配路径内调用, 先清后分配）;
#       (b) 周期（每 200 pass, 渲染块内调用）——删除的 clip/换工程后旧键
#       下次渲染即回收, 会话内存有界于「近 2 pass 活动集」。
# 淘汰规则: seen_seq < seq-2（近 2 次渲染 pass 未见的键）→ 删除键 + C 侧
# ce_clip_unregister 释放槽数据（~405KB/槽）+ idx 回收进 _ce_free_slots。
# 被淘汰键下次激活时重新注册（注册数据与 _render_one 同源 → 位级等价;
# 重注册成本 ≈ 一次 memcpy, 远低于 405KB×槽 的会话驻留）。
# 位级等价依据: 槽位分配/释放只影响「数据放哪个槽」, 每槽内容与渲染
# 路径完全不变（ce_clip_register 幂等覆盖 + ce_render_frame 只读槽）。
def _ce_sweep_slots(self, seq):
    reg = getattr(self, "_ce_clip_reg", None)
    if not reg:
        return
    _ce = getattr(self, "_ce_engine", None)
    ctx = getattr(_ce, "_ce_ctx", None) if _ce is not None else None
    anchor_reg = getattr(self, "_ce_anchor_reg", None)
    free = getattr(self, "_ce_free_slots", None)
    if free is None:
        free = []
        self._ce_free_slots = free
    unreg = getattr(_ce, "ce_clip_unregister", None) if _ce is not None else None
    cutoff = seq - 2
    dead = [k for k, v in reg.items() if v[2] < cutoff]
    for k in dead:
        idx = reg[k][0]
        del reg[k]
        if anchor_reg is not None:
            anchor_reg.pop(k, None)
        free.append(idx)
        if ctx is not None and unreg is not None:
            try:
                unreg(ctx, idx)
            except Exception:
                pass


# ── P7: hold 保持段扫描优化（对齐 playback_engine.py:1667 _indexed_active_clip_items）──
#
# 原版复杂度: 每帧从最新 hold 反向扫描, 每个 useful hold 末尾重算覆盖率检查
#   `all(np.all(covered.get(u, zeros)[scope]) for u, scope in hold_scope_by_universe ...)`
#   → O(H_useful × U_scope × 512)/帧（全时间线 hold 数 × 全 scope 域, 11382 台
#   大项目每帧重复扫描, 遗留审计项）。本版保持完全相同的扫描顺序/选中判定/
#   break 时机, 仅把覆盖率检查换成增量计数:
#     remaining = Σ scope 通道数; 每 hold 新覆盖的 scope 通道计数扣减; 归零即
#   break —— 与原版 all(...) 判定逐点等价（covered 单调累积, 归零 ⟺ 全部 scope
#   域全覆盖）, 检查成本 O(H×U_scope×512)/帧 → O(512×受影响域)/hold。
# 其余分支（active 桶扫描、hold 选中、hold_elapsed/源时间语义）逐字复刻原版。
# 位级等价: 探针 ce_probe_hold_scan.py 多时间线 A/B 逐项 deep-equal（含
# mute/solo/多域重叠/三种 hold 模式）。CE off 时本函数不安装（原版路径不变）。
import bisect as _bisect
import math as _math


def ce_indexed_active_clip_items(
    self,
    now: float,
    tracks: list,
    ordered_tracks: list,
    track_by_id: dict,
    any_solo: bool,
    timer_interval_s: float,
) -> list:
    from src.engine.native_ownership import mask_math, mask_count, mask_or
    from src.engine.native_playback_filter_cache import consume as _held_filter_mask
    from src.engine import render_timing_math as _timing
    index = self._playback_index_for_tracks(tracks, ordered_tracks)
    # Derived hold masks belong to this exact original playback index. Normal
    # author invalidation rebuilds that owner; retain it strongly to prevent a
    # bare clip/scope id from serving masks from an earlier author generation.
    if getattr(self, "_ce_indexed_cache_owner", None) is not index:
        self._ce_indexed_cache_owner = index
        self._ce_indexed_mask_cache = {}
        self._ce_scope_cache = {}
    _mask_cache = getattr(self, "_ce_indexed_mask_cache", None)
    if _mask_cache is None:
        _mask_cache = self._ce_indexed_mask_cache = {}
    bucket_size = float(index.get("active_bucket_size", 0.5) or 0.5)
    active_buckets = index.get("active_buckets", {}) or {}
    hold_events = index.get("hold_events", []) or []
    hold_starts = index.get("hold_starts", []) or []
    hold_scope_by_universe = index.get("hold_scope_by_universe", {}) or {}

    def _track_allowed(track) -> bool:
        if self._track_or_ancestor_muted(track, track_by_id):
            return False
        if any_solo and not self._track_or_ancestor_soloed(track, track_by_id):
            return False
        return True

    out: list = []
    bucket = _timing.active_bucket(now, bucket_size)
    for start_t, end_t, item in active_buckets.get(bucket, []) or []:
        if not _timing.active_admitted(now, start_t, end_t):
            continue
        _start_t, track_order, track, clip, duration, _hold_mode, target_universes = item
        if not _track_allowed(track):
            continue
        hold_mode = self._clip_hold_mode(clip)
        local_t, hold_extension = _timing.active_local(self, clip,
            (now, _start_t, duration, timer_interval_s, hold_mode, track_order))
        source_duration = self._clip_source_duration(clip, duration)
        source_t, _sequential_pause = self._clip_sequential_source_time(
            clip, local_t, source_duration)
        source_t, run_active = self._clip_run_mode_source_time(
            clip, source_t, source_duration
        )
        if not run_active and hold_mode == "none":
            continue
        _fade_in_raw = getattr(clip, "fade_in", 0.0) or 0.0 if hold_mode == "none" and not hold_extension else 0.0
        source_t, _long_fade, _long_progress = _timing.active_source(
            (source_t, source_duration, local_t, duration, _fade_in_raw, hold_extension, hold_mode))
        if not hold_extension and hold_mode == "last_cycle":
            source_t = self._clip_auto_hold_cycle_time(clip, source_t)
        if hold_extension:
            gain = 1.0
        elif hold_mode == "none":
            # 机制B续(2026-08-11): 延长窗口 [duration, fade_in] 内 _clip_fade_gain
            # 的 dur cap 使 gain 在 t=duration 封顶=1 → 值=末帧恒值(帧冻结,
            # 用户实测: 卡死一段后续接上)。正确语义 = 淡入按配置 fade_in 全程
            # 继续: 整窗 [0, fade_in] 用未封顶增益, t=duration 处连续,
            # t=fade_in 达 1.0 后窗口正常结束。仅 fix B 同类 clip 生效,
            # 其余(含 hold 系 clip)逐字节不变。P7 镜像须与 Python 侧逐字节同构。
            if _long_fade:
                gain = self._clip_transition_gain(
                    clip, _long_progress, fade_in=True)
            else:
                gain = self._clip_fade_gain(clip, local_t)
        else:
            gain = self._clip_fade_gain(clip, local_t)
        out.append((float(_start_t), int(track_order), track, clip, local_t, source_t, hold_extension, gain, target_universes))

    hold_upper = _bisect.bisect_right(hold_starts, float(now))
    if hold_upper > 0:
        covered: dict = {}
        selected = []
        # P7: scope 域预过滤 + 剩余未覆盖计数（原版检查的 all() 语义的增量形式）
        # ★ 2026-08-11: hold_scope_by_universe 段内静态 → 每帧重建
        #   np.asarray+np.any（~76 次/帧）纯浪费; 按对象 id 缓存
        #   （_ce_scope_cache, 与 _ce_indexed_mask_cache 同步清空）。
        _scope_cache = getattr(self, "_ce_scope_cache", None)
        if _scope_cache is None:
            _scope_cache = self._ce_scope_cache = {}
        scope_by_u = _scope_cache.get(id(hold_scope_by_universe))
        if scope_by_u is None:
            scope_by_u = {
                int(u): np.asarray(scope, dtype=bool)
                for u, scope in hold_scope_by_universe.items()
                if mask_count(scope)
            }
            _scope_cache[id(hold_scope_by_universe)] = scope_by_u
        remaining = sum(mask_count(scope) for scope in scope_by_u.values())
    for hold_idx in range(hold_upper - 1, -1, -1):
        _hold_start, item = hold_events[hold_idx]
        _start_t, track_order, track, clip, duration, _hold_mode, target_universes = item
        if not _track_allowed(track):
            continue
        hold_mode = _hold_mode
        useful = False
        for universe in target_universes:
            # ★ 2026-08-11: hold 循环每帧重复命中引擎 active_mask 缓存
            #   （命中路径仍 ~14µs × 10 万次/段）; 实例级 (id(clip), u) 缓存
            #   直接返回只读共享对象（调用方只读 &/np.any/copy — 引擎缓存
            #   :5372 同款只读语义）。_k2_build_tables 每段构建前清空
            #   （_ce_indexed_mask_cache）, 渲染段内项目静止 → 内容恒定。
            _u = int(universe)
            # E-3 Tier1: 缓存条目扩为 (base_active, has_any) —— 掩码段内
            #   静态(生命周期同 _ce_indexed_mask_cache), 免每帧重复 any。
            _cached_entry = _mask_cache.get((id(clip), _u))
            if _cached_entry is None:
                base_active = np.asarray(
                    self._clip_channel_active_mask(clip, _u), dtype=bool)
                has_any = bool(mask_count(base_active))
                _mask_cache[(id(clip), _u)] = (base_active, has_any)
            else:
                base_active, has_any = _cached_entry
            track_mask = _held_filter_mask(self, track, _u, index)
            if track_mask is not None:
                base_active, base_count = mask_math(1, base_active, np.asarray(track_mask, dtype=bool))
                if not base_count:
                    continue
            elif not has_any:
                continue
            already = covered.setdefault(int(universe), np.zeros(512, dtype=bool))
            newly, new_count = mask_math(3, base_active, already)
            if new_count:
                useful = True
                np.copyto(already, mask_or(already, base_active))
                scope = scope_by_u.get(int(universe))
                if scope is not None and remaining > 0:
                    _intersection, n_new = mask_math(1, newly, scope)
                    if n_new > 0:
                        remaining -= n_new
        if not useful:
            continue
        source_duration = self._clip_source_duration(clip, duration)
        _rate = self._clip_playback_rate(clip) if hold_mode == "last_cycle" else 1.0
        source_t, local_t, gain = _timing.held_source(
            (now, _start_t, duration, source_duration, _rate, hold_mode))
        if hold_mode == "last_cycle":
            source_t = self._clip_auto_hold_cycle_time(clip, source_t)
        selected.append((float(_start_t), int(track_order), track, clip, local_t, source_t, True, gain, target_universes))
        if covered and remaining <= 0:
            break
    if hold_upper > 0:
        out.extend(reversed(selected))
    return out
