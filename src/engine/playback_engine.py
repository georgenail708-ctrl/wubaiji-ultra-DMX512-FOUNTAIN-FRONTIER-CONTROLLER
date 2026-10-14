"""
DMXTimeline — Playback Engine  (v4)
回放引擎：读取时间线片段 → 处理 → 发送

★ v4 核心改进：
  1. 淡入/淡出方向可分别设置（fade_in_direction / fade_out_direction）
  2. 方向性分组淡入淡出全面向量化，消除纯 Python for 循环导致的 UI 卡死
  3. _clip_fade_gain 方法补全，修复 AttributeError
  4. 活跃通道缓存机制，避免每 tick 重复扫描帧数据
"""

from __future__ import annotations

import gc
import time
import logging
import math
import os
import re
import bisect
import threading
import uuid
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
from src.core.recorded_channel_masks import filter_active_mask, source_mask_time, transition_source_mask_time, with_source_mask_token
from PyQt6.QtCore import Qt, QObject, QTimer, pyqtSignal

from ..core.models import (
    Clip, DMXFrame, PlaybackState, Track,
    DEFAULT_LIGHT_TRACKING_ATTACK, DEFAULT_LIGHT_TRACKING_RELEASE,
    normalize_group_pauses,
)
from ..core.project_frame_origin import frame_project, project_frame_scope
from .xy_reference import (
    apply_xy_reference_to_virtual,
)
from .buffer_pool import FrameBufferPool
from .clip_frame_evaluation import candidate_input, append_candidate
from .candidate_resolution import resolve_python_candidates
from .prepared_frame import begin_frame, finish_candidates, resolve_candidates, mix_candidates
from .transition_math import blend_frame, update_anchor
from .prepared_source_remap import bind_renderer
from ..core.native_mask_math import default_restoration_rule, mask_count, restorable_takeover
from .source_mask_memo import matches as source_memo_matches, remember as remember_source_memo
from . import render_timing_math
from .native_ownership import (mask_math, mask_count, mask_and, mask_or, mask_without,
                               merge_consumed, apply_hold_consumption, apply_takeover_consumption)
from .pump_precharge import PrechargeScheduler, find_pump_channels, register_due_clips
from .timeline_frame_store import TimelineFrameStore
from .fade_anchor_invalidation import evict_fade_anchor_cache
from .timeline_cache_read import MISSING_FRAME, read_cached_frame
from .timeline_ready_cache_source import (prepare_cached_frame, cache_source_value,
    flush_cached_frame, render_cached_frame, read_cached_pair, acquire_render_lock, release_render_lock)
from .timeline_clip_invalidation import begin_clip_invalidation, end_clip_invalidation
from .native_frame_clock import cache_index
from .timeline_frame_export import export_timeline_frames
from .clip_takeover import (
    reset_playback_consumption,
    reset_consumption_on_rewind,
    clip_pre_arrival_mask,
    apply_pre_arrival_boost,
    fade_in_branch_taken,
    transition_boost_mask,
)
from .clip_output_filters import finalize_clip_output, apply_clip_output_filters, output_filters_signature, clip_filter_params
from .audio_mod_clip_processor import (begin_audio_frame, audio_item_key, audio_runtime_signature,
                                       audio_cache_allowed, apply_clip_filters_with_audio, finalize_clip_with_audio)
from .clip_trajectory_overlay import apply_trajectory_overlay, finish_realtime_frame
from .fade_group_overrides import overrides_map_for_clip, overrides_signature_with_group_count  # R172 §4 复合过渡
from .light_tracking_scope import lt_clips_with_gap_reset
from .light_tracking_structure import structure_signature, structure_memo, retire_structure
from .clip_light_tracking_scope import (light_tracking_target_mask, lt_color_params,
                                        lt_intensity_params, owned_rgbw_indices,
                                        source_visit_exhausted, target_package_lights)
from .clip_mask_prep import mask_frames_cached
from ..utils.identity_cache import bounded_put
from ..utils.log_throttle import LogThrottle, format_suppressed

# Module-level frame buffer pool for zero-allocation hot path copies.
# Pre-allocates 256 x 512-byte uint8 arrays — sufficient for worst-case
# renders (many clips x many universes per frame tick).
_frame_buffer_pool = FrameBufferPool()

# Optional installed T5 reducer; the original warm return never calls it.
_ce_clip_anchor_reduce = None
_ce_clip_change_times = None


def _prewarm_transition_anchor(owner, clip, universe, *, track, proj):
    from .transition_anchor_causality import prewarm_transition_anchor
    return prewarm_transition_anchor(owner, clip, universe, track=track, proj=proj)


def _try_clip_change_times(owner, data, times, active, *, callback):
    if callback is None:
        return None
    return callback(owner, data, times, active)


def _numpy_clip_change_times(frames, frame_data, frame_times, base_active):
    if len(frames) > 0:
        changed_by_frame = np.zeros((len(frames), 512), dtype=bool)
        changed_by_frame[0] = base_active
        if len(frames) > 1:
            changed_by_frame[1:] = (frame_data[1:] != frame_data[:-1]) & base_active
        # D-4: last_change_time 向量化 —— 每通道最近变更帧下标 cummax
        # 后按帧时间戳取值, 与旧逐帧 Python 循环逐位等价(索引制:
        # NaN 时间戳不累计传播, 后续变更照常覆盖)。
        _fidx = np.arange(len(frames), dtype=np.int64)[:, None]
        _last_idx = np.maximum.accumulate(
            np.where(changed_by_frame, _fidx, -1), axis=0)
        last_change_time = np.where(
            _last_idx >= 0,
            frame_times[np.clip(_last_idx, 0, len(frames) - 1)],
            0.0,
        ).astype(np.float32)
    else:
        last_change_time = np.zeros((0, 512), dtype=np.float32)
    return last_change_time


def _gen_frame_begin(eng) -> None:
    """R193/P1-1（PB-01）：gen 线程每帧起点钩子（模块级：上帝类禁增方法）。

    默认模式（QASCADE_GEN_THREAD=1）下 _tick 不跑，帧缓冲池游标归零与播放头
    窗口预热必须挂这里——否则池 2048 槽在会话内耗尽后，所有 copy_from 永久
    退化为堆分配（buffer_pool 头注设计契约：每帧起点 reset），窗口预热死路。
    R195/P0：烘焙构建期（building_lock=True）不再拉预热线程——烘焙私有 gen
    每 2s 一条预热线程纯属浪费，且加剧 GIL 争抢（用户实机采样实证）。
    """
    _frame_buffer_pool.reset()
    if not getattr(eng, "_timeline_render_cache_building_lock", False):
        eng._maybe_window_precompile()


def _gen_clock_source_backoff(eng, exc: BaseException) -> None:
    """★ R193/P1-2（XS-01）：时钟源持续抛错不得全速忙等——退避并留异常栈，
    否则单核 100% 占 GIL 饿死 UI 且零日志。
    ★ R194/NB-08：LogThrottle 限频（同签名 5s 一条+抑制计数；签名变化立即落笔），
    持续故障时不再 ~43 行/秒全栈刷屏。"""
    ok, suppressed = _GEN_BACKOFF_THROTTLE.allow(
        "gen-clock", (type(exc).__name__, str(exc)))
    if ok:
        log.exception("gen 时钟源回调失败：跳过本帧并退避 23ms%s",
                      format_suppressed(suppressed))
    from .gen_thread_lifecycle import mix_while_waiting
    mix_while_waiting(eng)


def _drain_gen_zombie(eng) -> bool:
    """★ R193/CC-01：排空上一只停止超时的僵尸 gen 线程。

    它的唯一退出条件 `_gen_stop` 将在 start 里 clear()——不排空会把僵尸
    永久复活成双渲染线程并发 render+mix。返回 False=仍存活（拒绝重启）。
    """
    zombie = getattr(eng, "_gen_zombie", None)
    if zombie is None:
        return True
    if zombie.is_alive():
        zombie.join(timeout=0.5)
    if zombie.is_alive():
        log.warning("上一 gen 线程停止超时仍存活，拒绝重启以防双线程渲染")
        return False
    eng._gen_zombie = None
    return True


def _prepare_gen_thread_start(eng) -> bool:
    """★ R193/CC-01：start 预检（模块级：类方法数/函数 CC 基线）。

    返回 False = 拒绝启动（总开关关/时钟源缺失/上一代僵尸线程未退）。
    """
    if not eng._gen_thread_enabled or eng._gen_clock_source is None:
        return False
    return _drain_gen_zombie(eng)


def _freeze_gc_once(eng) -> None:
    """★ R193/CC-06：gc.freeze 只在首次真启动执行——每次 play 重复 freeze
    会把上一播放周期未被回收的环垃圾永久钉死（全仓无 unfreeze，慢性累积）。"""
    if getattr(eng, "_gc_frozen_once", False):
        return
    eng._gc_frozen_once = True
    gc.freeze()


def _traj_overlay_err(eng) -> None:
    """★ R193/P2-02（PB-03）：叠加失败首错告警+计数（两处调用点共用）——
    坏配接此前会每帧刷全栈 traceback（日志 IO 反噬性能）。"""
    if not getattr(eng, "_traj_overlay_err_logged", False):
        eng._traj_overlay_err_logged = True
        log.exception("clip_traj overlay failed（首次告警；同类只计数）")
    eng._traj_overlay_err_count = getattr(eng, "_traj_overlay_err_count", 0) + 1


log = logging.getLogger(__name__)

# R181 P2-2：过渡压缩诊断日志限频（同内容 5s 一条；见 utils/log_throttle.py）。
# 修前 [PAR]/[SEQ] 在渲染热路径逐帧刷同内容 → 真工程 2 分钟 ~100KB。
_PAR_LOG_THROTTLE = LogThrottle(interval=5.0)
_SEQ_LOG_THROTTLE = LogThrottle(interval=5.0)
# ★ R194/NB-08：gen 时钟源持续抛错时的退避日志限频（同签名 5s 一条）。
_GEN_BACKOFF_THROTTLE = LogThrottle(interval=5.0)

# 缓存哨兵：区分“该 key 未计算过”与“计算结果为空(None)”，使空结果也能被缓存命中。
_EMPTY_MASK_SENTINEL = object()

# 渲染 C 内核惰性加载：成功=模块对象；失败/关闭=False(区分“未尝试”的 None)。
_RENDER_KERNELS = None

# C-1: _compiled_clip_frame_index 默认参哨兵 —— 免热路径(~52 次/tick)
# 每调用一次 np.zeros(0) 分配; 只在 ftl 缺失的回退分支物化 times 数组。
_EMPTY_F64_ARRAY = np.zeros(0, dtype=np.float64)

# G-3: hold 全覆盖断言的共享空掩码 —— 只读默认(covered.get 缺省值),
# 免每 hold 项每域急切 np.zeros(512) 分配(同 _EMPTY_F64_ARRAY 先例)。
_EMPTY_BOOL_MASK_512 = np.zeros(512, dtype=bool)

# G-4: 方向别名表模块级常量 —— 原 _normalize_clip_direction 每次调用
# 重建 23 项 dict(实测 51 次/tick), 纯数据只读可直接共享。
_CLIP_DIRECTION_ALIASES: Dict[str, str] = {
    "default": "none",
    "left_to_right": "ltr",
    "front_to_back": "ftb",
    "front-to-back": "ftb",
    "front_back": "ftb",
    "f2b": "ftb",
    "diag_tl_br": "ltr",
    "diag_bl_tr": "ltr",
    "right_to_left": "rtl",
    "back_to_front": "btf",
    "back-to-front": "btf",
    "back_front": "btf",
    "b2f": "btf",
    "diag_br_tl": "rtl",
    "diag_tr_bl": "rtl",
    "reverse": "rtl",
    "clockwise": "cw",
    "counterclockwise": "ccw",
    "anti_clockwise": "ccw",
    "anticlockwise": "ccw",
    "center_out": "ctr",
    "edge_in": "rtc",
}


def _get_render_kernels():
    """惰性加载编译好的 Cython 渲染内核。未编译/无编译器/QASCADE_C_KERNELS=0 时返回 None,
    调用方回退纯 numpy。只尝试一次,结果缓存。"""
    global _RENDER_KERNELS
    if _RENDER_KERNELS is None:
        if str(os.environ.get("QASCADE_C_KERNELS", "1")).strip().lower() in ("0", "false", "off", "no"):
            _RENDER_KERNELS = False
            return None
        try:
            from .native import _render_kernels as _mod
            _RENDER_KERNELS = _mod
        except Exception:
            _RENDER_KERNELS = False
            log.info("渲染 C 内核未加载(未编译或不可用),使用 numpy 路径")
    return _RENDER_KERNELS or None


def _natural_number(value: object) -> int:
    text = str(value or "")
    matches = re.findall(r"\d+", text)
    if not matches:
        return 10**9
    try:
        return int(matches[-1])
    except Exception:
        return 10**9


def apply_engine_audio_mod_scope(engine, outputs: dict, pos: float) -> dict:
    """音频调制按目标通道地址缩放 clip 层输出（R174#7 P0 作用域真实现）。

    单点合并：worker 队列与直写两路共用的 outputs 定稿处调用（照
    _maybe_apply_clip_traj_overlay 先例）。行为：
      - 无启用调制轨 → outputs 原样返回（逐字节不变）；
      - 启用 → 仅目标通道地址被调制值缩放（包络×波形×特征），其它地址/通道
        零改动。返回新 dict（纯函数语义）；调用方负责回写（`_apply_audio_mod_scope`）。
    """
    try:
        proj = _project_of(engine)
        if proj is None:
            return outputs
        audio_path = getattr(proj, "audio_path", "") or ""
        from .audio_mod_driver import apply_channel_scope
        return apply_channel_scope(outputs, proj, audio_path, float(pos))
    except Exception:
        return outputs


def _apply_audio_mod_scope(engine, outputs, layer_name, pos) -> None:
    """R174#7 音频调制作用域：clip_playback 层实时 outputs 定稿点单点合并。

    目标通道地址缩放（包络×波形×特征）；无调制轨/烘焙缓存路径零改动。
    逻辑在 audio_mod_driver.apply_channel_scope；本壳只为把 try/if 移出
    冻结基线的 _render_position（行数/CC 棘轮）。

    R182 通道 B 落地修复：`apply_channel_scope` 是纯函数（**返回新 dict**），
    旧实现丢弃返回值 = 每帧算出 scoped 调制后即扔（scoped 调制恒不生效）。
    此处把返回值按 `dict.clear()/update()` 原地回写，使 `_render_position`
    的 outputs 消费方（worker 队列/直写/NM）看到调制后的字节。identity
    快速路径（无调制/全量）返回同一对象 → 不回写，零开销不变。

    调用点位于 `_render_position` 的**脏标记跳过域回填之后**（R182）：被
    skip 复用的域也在 outputs 全表内，调制必须最后统一作用（否则静态帧域
    永远拿不到 scoped 调制）。memo 存的是未调制帧 → 顺序无副作用。
    """
    if layer_name != "clip_playback":
        return
    try:
        ret = apply_engine_audio_mod_scope(engine, outputs, float(pos))
        if ret is not None and ret is not outputs:
            outputs.clear()
            outputs.update(ret)
    except Exception:
        log.exception("audio mod scope failed")


def set_engine_audio_mod_state(engine, signature: tuple) -> None:
    """R174#7：音频调制轨状态签名入引擎运行态指纹（上帝类冻结，模块级 setter）。

    `signature` 来自 engine.audio_modulation.signature(state, feature_key)；
    变化 → 下一拍 is_timeline_render_cache_current 判 False → 自动解锁重烘。
    """
    if engine is None:
        return
    sig = tuple(signature or ())
    # The original AudioMod signature for normalize_state({}) has no clips,
    # source, rule, target or feature binding. Opening the unchanged empty lane
    # hydrates this tuple; it is the same runtime input as the initial () state.
    if sig == ('', '', (), 1.0, .05, .2, 'beat_strength', 0., 0., 0., (), ((), ()), ''):
        sig = ()
    if sig != getattr(engine, "_audio_mod_signature", ()):
        engine._audio_mod_signature = sig


def _project_of(engine):
    """引擎当前工程（兼容 ProjectManager 的 `project` / `_project` 两种属性名）。"""
    return frame_project(engine)


def _accumulate_takeover_scope(engine, index: dict, track_by_id: dict) -> Dict[int, np.ndarray]:
    """A10 P0 helper：回放索引 → 节目级接管掩码并集（`_program_full_takeover_scope`
    的循环体；独立模块级函数防上帝类方法数增长、压 CC）。"""
    out: Dict[int, np.ndarray] = {}
    for _start, _end, item in (index.get("active_events") or []):
        _s, _order, track, clip, _dur, _hm, target_universes = item
        if track is None or engine._track_or_ancestor_muted(track, track_by_id):
            continue
        for universe in target_universes:
            mask = engine._clip_index_scope_mask(clip, int(universe))
            track_mask = engine._track_filter_mask_for_universe(track, int(universe))
            if track_mask is not None:
                mask = mask & np.asarray(track_mask, dtype=bool)
            if not (mask).any():
                continue
            acc = out.setdefault(int(universe), np.zeros(512, dtype=bool))
            acc |= mask
    return out


def _merge_takeover_outputs(outputs: dict, scope: dict) -> dict:
    """A10 P0 helper：打点掩码 = 当下 outputs ∪ 节目级 scope（两处打点块共用）。"""
    merged: dict = {}
    for universe, (_data, active) in outputs.items():
        m = np.asarray(active, dtype=bool)
        extra = scope.get(int(universe))
        if extra is not None:
            m = m | np.asarray(extra, dtype=bool)
        merged[int(universe)] = m
    for universe, extra in scope.items():
        if int(universe) not in merged:
            merged[int(universe)] = np.asarray(extra, dtype=bool)
    return merged


def _program_takeover_scope(engine) -> Dict[int, np.ndarray]:
    """A10 审计 P0：本节目全部 clip 的目标通道并集（{universe: bool[512]}）。

    ★ 为什么需要：LTP 接管时间戳只在 play/seek 打点，且打点掩码取**当下**
      `outputs`——播放起点处于空档时 outputs 为空 → 后面才开始的 clip 在
      「该通道曾被编辑器写过」的通道上 `clip_t(0) < editor_t` 被永久压制
      （整段无输出，监视器与硬件一致——用户报"有的 clip 没有该有的输出"
      的直接机制）。修法 = 打点语义改为"这段节目将接管这些通道"：按回放
      索引（`_clip_index_scope_mask` 同源口径：clip 精确掩码 ∩ 轨道滤网）
      取全节目并集。编辑器后续触碰仍可抢回（时间戳语义不变）。
    """
    try:
        proj = _project_of(engine)
        tracks = list(getattr(proj, "tracks", []) or []) if proj is not None else []
        ordered = engine._project_ordered_tracks(proj)
        index = engine._build_playback_active_index(tracks, ordered)
        track_by_id = {str(getattr(t, "id", "") or ""): t for t in tracks}
        return _accumulate_takeover_scope(engine, index, track_by_id)
    except Exception:
        log.exception("program_takeover_scope failed; outputs-only fallback")
        return {}


def _stable_fixtures(cache: dict, raw_fixtures) -> list:
    """「全项目设备」列表的稳定对象，避免 id(fixtures) 缓存键每次 miss。

    按「源容器身份 + 长度」判定设备表是否变化：不变则复用同一 list 对象，
    使下游所有 `id(fixtures)` 键缓存可命中。持有源容器引用 → 其 id 不会被
    回收复用，身份判定安全（设备增删改长度或换容器都会自动重建）。

    ★ R38 freeze root cause: `list(getattr(project, "fixtures", []))` 每次创建新对象
       → 所有 id(fixtures) 键缓存全 miss → 全项目设备每帧重算 → 卡死。
    """
    cached = cache.get("value")
    src = cache.get("src")
    if cached is not None and src is raw_fixtures and len(cached) == len(raw_fixtures):
        return cached
    out = list(raw_fixtures or [])
    cache["src"] = raw_fixtures
    cache["value"] = out
    return out


def _stable_project_fixtures(owner, attr: str, proj) -> list:
    """`_stable_fixtures` 的单行调用形式：缓存 dict 挂在 ``owner.<attr>`` 上（懒建）。"""
    cache = owner.__dict__.setdefault(attr, {})
    return _stable_fixtures(cache, getattr(proj, "fixtures", None) or [])


MOTOR_FINE_CHANNELS = {
    "motor_x": "motor_x_fine",
    "motor_y": "motor_y_fine",
    "motor_z": "motor_z_fine",
}
TRANSITION_STRATEGIES = {
    "standard_fade", "immediate", "decelerate", "hold_decay",
    "step_down", "smooth_ease", "delay_then_cut",
}
TRANSITION_CURVES = {"linear", "easein", "easeout", "easeinout", "bezier"}
CLIP_RUN_MODES = {"normal", "reverse", "pingpong", "loop", "once"}

# ── R16：过渡曲线词表扩展 = 5 旧名 + 缓动库全名（QEasingCurve 同名语义）──────
# 求值统一走 `route_animator.easing`（与 R1 油门斜坡同一张表、同一求值入口）；
# 5 旧名在本文件保留原闭式公式（逐字节等价旧行为），库名走 easing 的 LUT 路径。
# 仅新增**模块级**符号：PlaybackEngine 是冻结上帝类，不得新增独立方法。
from .route_animator.easing import (          # noqa: E402 - 历史常量区之后
    easing_names as _easing_names,
    transition_curve_gains as _lib_curve_gains,
    transition_curve_value as _lib_curve_value,
)

#: 缓动库全名（词表来源 = easing.py 主表键；与 R1 斜坡下拉同一份）
EASING_CURVE_NAMES = frozenset(_easing_names())


class _ClipSortKey:
    """Sort key with precomputed folder chain, avoiding cmp_to_key overhead.

    ★★ 2026-09-19 用户定案（改口径）::

        "同级是不恢复，遵循严格的后发接管。有高低层级的话，例如高层级被低层覆盖，
         低结束回到高，有恢复。同不同级别只影响恢复与否，不影响当时是否被后发接管"

      即 **接管 = 严格后发（latest wins），层级不参与**；层级只决定"接管方结束后
      是否恢复被压的那一方"（见 `_is_restorable_takeover`）。
      旧实现的 Rule 1「子文件夹优先」让**层级压过时间** → 后发的父层 clip 压不过
      先发的子层 hold clip（用户实测："保持模式拒绝被后发 clip 覆盖"）。
      现改为单一规则：**start_time 降序 → track_order 降序**（= 谁后发谁赢）。
    """
    __slots__ = ('start', 'track_order', 'chain', 'item')

    def __init__(self, item: tuple, folder_chain_fn: Callable) -> None:
        self.start: float = item[0]
        self.track_order: int = item[1]
        self.chain: tuple = folder_chain_fn(item[2])
        self.item: tuple = item

    def __lt__(self, other: '_ClipSortKey') -> bool:
        # 严格后发接管：start_time 越晚越优先；同 start 时 track_order 越晚越优先。
        if self.start != other.start:
            return self.start > other.start
        return self.track_order > other.track_order


def _same_level_cand_idx(cands, metas, hk_chain):
    """与 `hk_chain` **同层级**的候选下标（消费只发生在同层级压过时）。"""
    out = []
    for _k in range(min(len(cands or ()), len(metas or ()))):
        _m = metas[_k]
        _c = _m[1] if len(_m) > 1 else ()
        if tuple(_c or ()) == tuple(hk_chain or ()):
            out.append(_k)
    return out


def _same_level_others(cands, metas, hk_chain, self_cand):
    """同层级候选中**除自己以外**的下标 —— 自己不是"覆盖方"。

    ★★ 2026-09-19 二修（用户实报"clip 异常回到 128 的问题还在"）：
      上一版解除判据直接用 `_same_level_cand_idx`，而**保持项自己就在候选里**
      （chain 与自身相同）→ `same_chains` 恒非空 → 解除分支**从未执行** →
      被后发 clip 消费过的通道永久扣除：后发 clip 离开后保持项也回不来，
      输出落回设备默认值（实测 T3 hold 末帧 24，28s 起输出 128）。

      判据修正：只有**他人**（同层级、非本对象）才算覆盖方；
      他人全部离场 → 解除消费（`_accumulate_hold_consumed` 内处理）。
      `self_cand is None`（保持项本轮未进候选=被全消费）时全部同层级都算他人。
    """
    out = []
    for _k in _same_level_cand_idx(cands, metas, hk_chain):
        if self_cand is not None and cands[_k] is self_cand:
            continue
        out.append(_k)
    return out


def _same_level_lost(cand, cands, same_chains, best):
    """`cand` 中被**同层级候选**压过的通道（只对这些做消费）。

    ★ 2026-09-19 用户定案："同不同级别只影响恢复与否" —— 跨层级压过**不消费**
      （接管方离开后恢复）；同层级才消费（不恢复）。
    """
    _cs = cand[3]
    lost = cand[1] & ~(np.isfinite(_cs) & (_cs >= best - 1e-12))
    if not (lost).any():
        return lost
    out = np.zeros(512, dtype=bool)
    for _k in same_chains:
        _ka = np.asarray(cands[_k][1], dtype=bool)
        _ks = np.asarray(cands[_k][3], dtype=np.float64)
        out |= (lost & _ka & np.isfinite(_ks) & (_ks >= best - 1e-12))
    return out


def _accumulate_hold_consumed(table, key, universe, *,
                              cand, cands, metas, chain, best) -> bool:
    """更新某保持项的消费记账，返回是否**新增**消费（调用方据此解锁渲染缓存）。

    · **他人全部离场**（同层级除自己外无候选）→ 解除该 (key, universe) 消费，
      保持项重新占回自己的通道 —— 输出不再落回设备默认值（电机 128）。
    · 他人仍在场：只把"被他人压过"的通道并入消费（一次性消费语义）。
    """
    others = _same_level_others(cands, metas, chain, cand)
    if not others:
        _release_hold_consumed(table, key, universe)
        return False
    if cand is None:
        return False          # 保持项本轮未进候选（被全消费）且覆盖方仍在
    lost = _same_level_lost(cand, cands, others, best)
    if not (lost).any():
        return False
    _cm = table.get(key)
    _arr = None if _cm is None else _cm.get(int(universe))
    is_new = True if _arr is None else bool((lost & ~_arr).any())
    if _arr is None:
        _arr = lost.copy()
    else:
        _arr |= lost
    if _cm is None:
        _cm = table.setdefault(key, {})
    _cm[int(universe)] = _arr
    return is_new


def _release_hold_consumed(table, key, universe) -> None:
    """消费记账解除：**覆盖方已不在场**（无同层级候选）→ 保持项重新占回通道。

    ★★ 2026-09-19 用户定案："clip 保持跟普通 clip 唯一的区别就是可以停在最后
      一帧或者最后一个周期保持，其接管的逻辑跟普通 clip 完全一样。就是后发接管，
      除非遇到层级关系才恢复语义。"

      消费（"这些通道已被别人接管"）只该在接管方仍活跃时成立。覆盖方离开后，
      仍在保持的 clip 必须能重新写自己的通道 —— 否则输出落回设备默认值
      （电机 128）= 用户报的"非要归中"（实测 94ae98a2 结束后 hold 的 22/42
      通道仍被永久扣除）。
    """
    per_uni = table.get(key)
    if per_uni is not None:
        per_uni.pop(int(universe), None)


def _hold_score_cap(clip, clip_start):
    """保持项的仲裁分数封顶点 = **它进入保持的时刻**（不是"现在"）。

    ★★ 2026-09-19 用户实报修复（真实工程"hold → 带分组方向的**新** hold，
      新 clip 的淡入被完全跳过、直到旧 hold 结束才跳到新值"）：保持项的分数
      此前被 `minimum(scores, **当前时刻**)` 封顶 —— 于是它每一帧都等于"现在"，
      比任何后发的真 clip 都"新"，永远赢仲裁。保持的语义是"停在它自己的结束
      时刻不动"，不是"每帧都在更新"。
    """
    return float(clip_start) + max(
        0.0, float(getattr(clip, "duration", 0.0) or 0.0))



#: 配套页类别段（与 `ui/depence_groups.COMPANION_CATEGORY_SEGMENTS`、
#: `ui/topview_panel._COMPANION_PAIRS` 同源语义；engine 不得 import src.ui，故各自实现）
_COMPANION_CATS = ("设备包", "泵", "散台")


#: 类别段 → 要找的配套类别（与 `ui/topview_panel._COMPANION_PAIRS` 同源）
_COMPANION_WANT = {"设备包": ("泵", "散台"), "泵": ("设备包",), "散台": ("设备包",)}


def _companion_prefix(page_name: str):
    """页名 → `(同类前缀, 要找的类别元组)`；非配套页返回 `("", ())`。

    ★ 返回形态与 `ui/topview_panel._companion_match` **完全一致**（便于两侧对拍测试；
      engine 不得 import src.ui，故各自实现但语义同源）。
    `实用·设备包·气爆包` → `("实用", ("泵", "散台"))`；`实用·泵` → `("实用", ("设备包",))`。
    """
    nm = str(page_name or "")
    for _c in _COMPANION_CATS:
        _i = nm.find(f"·{_c}")
        if _i > 0:
            return nm[:_i], _COMPANION_WANT[_c]
    return "", ()


def _record_private_spatial_snapshot(engine, clip):
    from .transition_anchor_geometry import record_private_geometry
    record_private_geometry(engine, clip)


class PlaybackEngine(QObject):
    state_changed    = pyqtSignal(PlaybackState)
    position_changed = pyqtSignal(float)
    playback_started = pyqtSignal()
    playback_stopped = pyqtSignal()

    def __init__(self, network_manager, project_manager, parent=None):
        super().__init__(parent)
        self._nm = network_manager
        self._pm = project_manager
        self._state = PlaybackState.IDLE
        self._position: float = 0.0
        self._real_start: float = 0.0
        self._processor: Optional[Callable] = None

        # 防重入 (线程安全锁, 替代非原子 bool)
        self._render_lock = threading.Lock()
        self._suppress_position_emit: bool = False

        # 每 clip 的 anchor 缓存（懒加载，按 clip.id）：
        #   {clip_id: anchor_array(512, float32)}
        self._clip_anchor_cache: Dict[str, np.ndarray] = {}
        # 锚点缓存上限：默认 4096(覆盖大工程 clip×universe 工作集，~8MB)。
        # 随机跳播时 256 上限抖动导致每 tick 重算锚点(遍历整段 frames)是随机跳播卡顿主因。
        # QASCADE_ANCHOR_CACHE_CAP 可调；设 256 回退旧行为验证。
        try:
            self._anchor_cache_cap: int = max(
                256, int(os.environ.get("QASCADE_ANCHOR_CACHE_CAP", "4096")))
        except (TypeError, ValueError):
            self._anchor_cache_cap = 4096

        # 活跃通道缓存：{f"{clip.id}::{universe}": list[int]}
        # 避免每 tick 重复扫描帧数据
        self._clip_active_channels_cache: Dict[str, List[int]] = {}
        self._clip_frame_index_cache: Dict[str, Tuple[int, Dict[int, List[DMXFrame]]]] = {}
        self._clip_frame_universes_cache: Dict[str, tuple[int, tuple[int, ...]]] = {}
        self._clip_sequential_remap_cache: Dict[str, tuple] = {}
        self._clip_fade_geometry_cache: Dict[tuple, tuple] = {}
        self._clip_xy_reference_cache: Dict[tuple, tuple] = {}
        self._clip_render_cache: Dict[tuple, tuple] = {}
        self._clip_compiled_cache: Dict[tuple, dict] = {}
        # A2 任务构建瘦身: (id(clip), universe, id(track)) → compiled 一次命中直映射。
        # 值存 (clip, track, compiled) 并做 is 校验: id() 复用/换 track/失效后均失配重算,
        # 语义与 quick_key 完全一致(同一 compiled 对象); 失效随 invalidate_clip_cache /
        # invalidate_all_caches 整表清空(键含 id() 无法按 clip_id 字符串局部删除)。
        self._clip_compiled_fast: Dict[tuple, tuple] = {}
        self._clip_render_mask_cache: Dict[tuple, np.ndarray] = {}
        self._clip_render_mask_any_cache: Dict[tuple, bool] = {}
        self._clip_static_signature_cache: Dict[str, tuple] = {}
        self._clip_motor_force_profile_cache: Dict[
            str, Optional[tuple[float, float, float]]
        ] = {}
        self._clip_recorded_source_span_cache: Dict[str, float] = {}; self._clip_recorded_cycle_cache: Dict[str, tuple] = {}   # (first_ts,period,last_ts)
        # 过渡内容指纹缓存：键 clip_id -> (len(frames), signature)。
        # 该签名只用于构建过渡锚点时判定 clip 内容是否变化；clip 帧在播放期间不变，
        # 每帧重算 numpy 点积纯属浪费。与 _clip_static_signature_cache 同样的缓存思路，
        # 失效统一走 invalidate_clip_cache / invalidate_all_caches。
        self._clip_transition_content_sig_cache: Dict[str, tuple] = {}
        # ── 脏标记增量渲染(性能) memo ───────────────────────────────────
        # 值变化才重算: 静态场景(帧不变/hold/无淡入淡出窗口)跳过每帧求值风暴与重混音。
        # item memo: (layer, clip_id, start_t, track_order, universe) -> {sig,fast,data,active,scores}
        # uni  memo: layer -> {universe: {sig_set, outputs, record}}
        # 失效: invalidate_clip_cache / invalidate_all_caches / unlock_timeline_render_cache;
        # 签名含 XY 基准指纹, XY 变化自动失配走原求值路径。
        self._dirty_render_item_memo: Dict[tuple, dict] = {}
        self._dirty_render_uni_memo: Dict[str, Dict[int, dict]] = {}
        try:
            self._dirty_render_enabled: bool = str(
                os.environ.get("QASCADE_DIRTY_RENDER", "1") or "1") != "0"
        except Exception:
            self._dirty_render_enabled = True
        self._playback_active_index: Optional[dict] = None
        self._playback_active_index_dirty: bool = True

        # F4: numpy数组池 — 避免每tick重新分配512字节数组（病A级优化）
        # 池化常用shape/dtype组合，减少GC压力
        self._numpy_pool_u8_512: list = []       # np.zeros(512, dtype=np.uint8)
        self._numpy_pool_bool_512: list = []     # np.zeros(512, dtype=bool)
        self._numpy_pool_max_size: int = 16      # 每个池最多缓存16个数组

        self._precompile_dirty: bool = True
        self._precompile_signature: Optional[tuple] = None
        # Background precompile: daemon thread warms per-clip caches after a
        # program loads so the first playthrough is smooth without freezing UI.
        self._precompile_cancel: bool = False
        self._precompile_thread: Optional[threading.Thread] = None
        self._timeline_render_cache_locked: bool = False
        self._timeline_render_cache_building_lock: bool = False
        self._timeline_render_lock_signature: Optional[tuple] = None
        self._timeline_render_lock_misses: int = 0
        self._playback_start_frame_rendered: bool = False
        self._timeline_render_frame_cache: Dict[int, Dict[int, Tuple[np.ndarray, np.ndarray]]] = {}
        self._timeline_render_record_source_frame_cache: Dict[int, Dict[int, Tuple[np.ndarray, np.ndarray]]] = {}
        self._timeline_render_frame_cache_signature: Optional[tuple] = None
        self._timeline_render_frame_cache_interval: float = 0.023
        self._timeline_render_frame_cache_end: float = 0.0
        # 渲染缓存「运行态指纹」：除 clip 布局之外，还有一部分会改变 clip_playback
        # 烘焙结果、却不走 invalidate_clip_cache 的全局状态（XY 基准、多 clip 叠加增益）。
        # 这些变了必须判定缓存失效并解锁，否则会逐帧回放旧 XY 基底 / 旧增益的烘焙帧。
        self._timeline_render_runtime_signature: Optional[tuple] = None
        self._clip_layout_signature_cache_key: Optional[tuple] = None
        self._clip_layout_signature_cache: Optional[tuple] = None
        self._clip_layout_signature_dirty: bool = True
        # 布局签名「验证节流」墙钟：稳态播放期间布局不变(dirty 恒 False)，
        # 每 tick 重建 O(全部clip) 的对比 key 纯属浪费(实测占 render 34%)。
        # 真实失效走 invalidate_*(立刻置 dirty=True，下次调用即时重建)；
        # 这里只兜底「直接改 clip 属性却没走 invalidate」的拖拽态，120ms 墙钟自愈一次。
        self._clip_layout_sig_check_time: float = -1.0
        self._clip_layout_sig_check_interval: float = 0.12
        # 渲染结构缓存：轨道层级展开 / track_by_id / parent_map / any_solo / 文件夹链。
        # 这些只随轨道结构(增删/重排/改父/折叠/mute/solo)变化，播放期间逐帧不变，
        # 用「对象身份+结构态」做廉价键(O(tracks))避免逐帧重建多个 dict + 走祖先链。
        self._render_struct_cache_key: Optional[tuple] = None
        self._render_struct_cache: Optional[dict] = None
        # 设备定义索引缓存(id->def)：随 fixture_defs 列表对象身份变化。
        self._fixture_defs_index_cache_key: Optional[tuple] = None
        self._fixture_defs_index_cache: Optional[dict] = None
        # 设备实例索引缓存(id->fixture)：随 fixtures 列表对象身份变化。
        self._fixtures_by_id_index_cache_key: Optional[tuple] = None
        self._fixtures_by_id_index_cache: Optional[dict] = None
        # base_order_channel_mask 记忆化: key=(clip_cache_id, universe, base_order元组)。
        # 该 mask 仅取决于 clip 的 base_order 设备列表 + universe + 静态配接,批渲染期不变。
        self._clip_base_order_mask_cache: Dict[tuple, Optional[np.ndarray]] = {}
        # 顺序分组时序记忆化: key=clip_cache_id。仅取决于 clip 的 fade_group_* + duration
        # 静态属性,批渲染每帧 4× 以相同结果调用 → 缓存免去重复解析。
        self._clip_seq_timing_cache: Dict[str, tuple] = {}
        # 灯跟踪结构 memo：(签名, {universe:[fixtures]}, {def_id:def})。仅随工程结构变化，
        # 签名廉价(id+len)，命中即免去每 clip 每 tick 遍历全部设备重建。
        self._lt_struct_memo: Optional[tuple] = None
        # 灯跟踪每设备通道分类 memo：{(def_id, start_channel, source): (source_idx, rgbw_idx)}。
        # 纯结构(取决于 fd.channels + 起始地址 + source 类型)，逐帧复用免重复解析通道。
        self._lt_chan_idx_cache: Dict[tuple, tuple] = {}
        # 变更E: 灯跟踪 per-(clip, universe) 相关 fixture 预过滤缓存。
        # 键 = (clip_id, universe, struct_sig, source) — 结构/来源变化自动失效;
        # 值 = 主路径+泵路径相关 fixture 列表(分类与运行时同代码, 预填充 chan_idx_cache)。
        self._lt_scope_cache: Dict[tuple, list] = {}
        # ★ 2026-08-06 修复: clip 灯跟踪 active-mask 缓存 — 原每 tick 每
        #   universe 重建(含全部包×设备映射 = 1370万次迭代×73域) → 单个 clip
        #   灯跟踪卡死整个程序(用户实测)。键含结构签名+clip设置, 结构不变
        #   每 tick O(1) 命中。
        #   值 = (域mask字典, project, 依赖强引用, clip)，一次构建本clip所有目标域。
        self._lt_mask_cache: Dict[tuple, tuple] = {}
        # clip 布局签名「兜底校验」的节流：真实失效(invalidate_*)会立刻置 dirty 即时重建；
        # 这里只是为了捕捉「直接改 clip.duration/start_time 却没走 invalidate」的拖拽态，
        # 逐帧重算 O(全部clip) 的签名纯属浪费，限制为每 ~120ms 墙钟校验一次即可，
        # 拖拽期间最坏 120ms 自愈，肉眼无感，稳态播放开销骤降。
        self._playback_index_check_time: float = -1.0
        self._playback_index_check_interval: float = 0.12
        self._light_tracking_state: Dict[str, float] = {}
        self._light_tracking_time: Dict[str, float] = {}
        # 灯跟踪颜色缓存：{key: (r, g, b)} 首次渲染时从 NM 实际输出读取，避免每帧重读
        self._light_tracking_color_cache: Dict[str, tuple] = {}
        # 灯跟踪活动 clip 列表(渲染循环每帧重建): 泵/灯分 clip 录制时供
        # 跨域段从同时播放的其他 clip 读泵域帧(2026-08-07 用户实测修复)
        self._lt_active_clips: list = []
        # 灯跟踪异常日志去重: 无泵数据等稳态条件每 tick 抛同一异常会刷屏,
        # 记录已警告条件键(type+msg), 同条件仅 log.warning 一次; 换工程/
        # undo 全量失效时清空重新武装(见 invalidate_all_caches)。
        self._lt_warned: set = set()
        self._last_render_time: Optional[float] = None
        self._perf_slow_render_ms: float = 18.0
        self._perf_last_render_log: float = 0.0
        # Clip hold 边界滞回锁存：避免 end 边界处因 tick 抖动出现单帧来回切换
        self._hold_boundary_latch: Dict[str, bool] = {}
        # ★ R14 保持模式一次性消费语义(2026-08-13): 真实 hold 期(扫描路径)中
        #   被后来轨道/clip 覆写的通道记录于此, 之后从 hold item 的 active 永久
        #   扣除 —— 覆写方离开后旧保持值不得复活(死灰复燃根因修复)。
        #   键 = clip_cache_id(clip 全局唯一, 对重排/重父化稳定 —— M1 修复
        #   2026-08-13: 原 f"{clip_cache_id}::{track_order}" 的 track_order
        #   是每帧 DFS 派生的轨道位置, UI 拖轨重排/重父化只发 tracks_changed
        #   不 invalidate → 下一帧键漂移 → 已消费通道死灰复燃)。值 =
        #   {universe: np.bool_[512]}。清空点: stop() /
        #   invalidate_all_caches() / 活跃窗口重新进入(每项循环非扩展帧)。
        #   消费状态为空时所有渲染路径逐字节不变(零影响铁律)。
        #   注: _hold_boundary_latch 仍用 clip_id::track_order 键(单帧滞回闩,
        #   键漂移=重标记, 良性; 审计结论 2026-08-13, 保留不迁移)。
        self._hold_consumed_masks: Dict[str, Dict[int, np.ndarray]] = {}
        # 2026-08-22: 同级/非父子覆盖消费 —— 非可恢复接管（同级或无关分支）被
        # 覆写后，旧 clip 不得在新 clip 结束后复活。键/值/清空点同 hold 消费。
        self._takeover_consumed_masks: Dict[str, Dict[int, np.ndarray]] = {}
        # M2 帧级门(2026-08-13): 锁定帧缓存 serve 门否决时, 复用已构建的
        # 活跃项给主渲染循环(免二次 _indexed_active_clip_items); 每帧由
        # _render_from_timeline_frame_cache 起点清空, 非否决恒 None。
        self._m2_gate_items: Optional[list] = None

        # 形状叠加增益
        self._modulation_gain: float = 1.0

        # A3 热路径零分配：预分配 _mix_clips 复用缓冲，消每 tick np.stack×3 + 内核输出分配。
        # QASCADE_PREALLOC=1 开（默认开，A3 预分配消 np.stack 热路径分配）。容量不足时动态扩容并保留。
        self._prealloc_enabled: bool = os.environ.get(
            "QASCADE_PREALLOC", "1"
        ) not in ("0", "false", "False", "no", "")
        self._mix_cap_layers: int = 0
        self._mix_vals_buf: Optional[np.ndarray] = None      # (cap, 512) float32
        self._mix_acts_buf: Optional[np.ndarray] = None      # (cap, 512) uint8
        self._mix_anchors_buf: Optional[np.ndarray] = None   # (cap, 512) float32

        # ── 泵预充电调度器（A3）─────────────────────────────────────────────
        # 在泵 clip 开始前向泵通道发送预充电 DMX 值，消除 VFD 启动延迟。
        self._precharge_scheduler = PrechargeScheduler(network_manager)

        # 上一帧写过的 universe，按 clip 输出层分别记录，避免播放层与预览层互相清理。
        self._last_output_universes_by_layer: Dict[str, set[int]] = {
            "clip_playback": set(),
            "clip_preview": set(),
            "timeline": set(),
        }
        self._last_output_universes: set[int] = set()  # 旧字段兼容
        self._last_record_source_outputs_by_universe: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}

        self._timer = QTimer(self)
        # In TimelineWidget mode, rendering is driven by TimelineController ticks
        # and this timer may never be started. Keep a sane default interval so
        # seam calculations do not accidentally see 0ms quantum.
        # ★ PreciseTimer：Qt 默认 CoarseTimer 会把 interval 取整到
        #   15.625ms 倍数（25ms→31.68ms），主链路定时器必须精确。
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._tick)

        # ★ LTP 接管标记：记录最近一次「接管请求」对应的播放层和位置。
        # 点击播放 / 拖针落点时设为 True，_render_position 内处理一次后重置为 False。
        # 播放过程中的每帧 tick 不设置此标记，从而不刷新 clip 接管时间戳。
        self._ltp_takeover_pending: bool = False   # 是否待处理接管通知
        self._ltp_takeover_layer: str = ""          # 待处理的层名

        # ★ 过渡起点缓存：{cache_key -> float32[512]}
        # 在 clip 淡入开始时（local_t 刚进入 fade_in 窗口）拍下编辑器当前输出作为过渡起点。
        # 包含保持帧的上一个 clip 的输出会通过 LTP 层留在编辑器输出里，
        # 因此此处读到的就是正确的过渡起点。
        self._fade_in_anchor: Dict[object, np.ndarray] = {}
        self._fade_in_anchor_deps: Dict[tuple, set[str]] = {}

        # ── 第3步：统一生成线程（QASCADE_GEN_THREAD=1 开，默认关=旧的 UI 线程驱动路径）──
        # 目的：render 脱离 Qt 事件循环，自带 monotonic 时钟独立跑。
        #   ① 根治 GIL 车队：render 不再和 UI tick 挤 UI 线程(只剩生成线程+NM worker 两个真活)。
        #   ② 根治模态冻结：拖窗/开菜单冻结 Qt 事件循环时，生成线程照读时钟源、照 render → 灯不停。
        # 时钟源 _gen_clock_source() 返回 (is_playing: bool, pos: float)，由 UI 侧注入
        #   (读 ctrl.is_playing / ctrl._play_t0，都是普通属性，GIL 保证原子读，模态冻结不影响其值)。
        # 缓存竞态：门控开时 UI 编辑走 pending-invalidate 队列(只置标志入队)，生成线程帧起点 drain 清缓存
        #   → 缓存只被生成线程写，热路径无锁、无死锁。
        # 默认开(内置为常态)：根治模态冻结 + 稳态 43Hz。QASCADE_GEN_THREAD=0 一键回退旧路径。
        self._gen_thread_enabled: bool = os.environ.get("QASCADE_GEN_THREAD", "1") not in ("0", "false", "False", "no", "")
        self._gen_thread: Optional[threading.Thread] = None
        self._gen_stop = threading.Event()
        self._gen_clock_source: Optional[Callable[[], Tuple[bool, float]]] = None
        self._gen_interval_sec: float = 0.023
        # A4 自适应 4 级睡眠门控（默认关，验证后开）。开时按剩余预算分级 sleep，
        # 便宜 tick 多让 GIL 给 PyQt 重绘；耗时 tick 立即续帧。QASCADE_ADAPTIVE_SLEEP=1 开。
        self._adaptive_sleep_enabled: bool = os.environ.get(
            "QASCADE_ADAPTIVE_SLEEP", "0"
        ) not in ("0", "false", "False", "no", "")
        self._gen_layer_name: str = "clip_playback"
        # pending-invalidate 队列：门控开时 UI 线程置入，生成线程帧起点消费。
        self._gen_pending_invalidate_lock = threading.Lock()
        self._gen_pending_invalidate_clips: set[str] = set()
        self._gen_pending_invalidate_all: bool = False

    # ── 公共接口 ────────────────────────────────────────────────────────────

    @property
    def position(self) -> float:
        return self._position

    @property
    def state(self) -> PlaybackState:
        return self._state

    @property
    def is_playing(self) -> bool:
        return self._state == PlaybackState.PLAYING

    def set_processor(self, fn: Optional[Callable]):
        self._processor = fn

    def set_modulation_gain(self, g: float):
        self._modulation_gain = max(0.0, min(4.0, float(g)))

    def clear_record_source_outputs(self):
        self._last_record_source_outputs_by_universe = {}

    def is_timeline_render_cache_locked(self) -> bool:
        return bool(getattr(self, "_timeline_render_cache_locked", False))

    def _timeline_render_runtime_signature_value(self) -> tuple:
        """会改变 clip_playback 烘焙结果、但不经 invalidate_clip_cache 的全局运行态指纹。

        包含两类：
          1. XY 基准（_xy_reference_by_universe）：clip 帧按当前 XY 基准做逆变换后
             烘焙；TopView 改 XY 基准后 NM 会用新基准做正变换，若仍放旧帧物理输出会错。
          2. 多 clip 叠加增益 _modulation_gain：直接参与 _mix_clips 烘焙结果。
        二者都通过 set_xy_reference_map / set_modulation_gain 改动，不触发 clip 失效，
        故单独放进运行态指纹，变化即判定缓存失效并解锁。
        """
        # ★ R178 终修：_modulation_gain 移出指纹——它是每帧动态值（音频调制 tick
        #   实时更新），放指纹=无操作也跳闸重烘（用户实测"自己跳闸自己重烘"）。
        #   调制作用域已改为通道地址缩放（apply_channel_scope，实时路径），烘焙
        #   路径强制 gain=1.0（审计 P1-2），gain 不再影响烘焙字节。
        # ★ R174#7：音频调制轨状态（scope/amount/包络/特征/clip 集）任一变化 →
        #   烘焙失效（与 XY 基准同族口径；setter 见 set_audio_mod_state）。
        mod_sig = audio_runtime_signature(self, getattr(self, "_audio_mod_signature", ()))
        refs_by_universe = getattr(self._nm, "_xy_reference_by_universe", None) if self._nm is not None else None
        if not isinstance(refs_by_universe, dict) or not refs_by_universe:
            return (mod_sig, ())
        xy_items = []
        for universe, refs in sorted(refs_by_universe.items(), key=lambda kv: int(kv[0])):
            ref_sig = tuple(
                (
                    str(getattr(ref, "fixture_id", "")),
                    int(getattr(ref, "x_addr", -1) if getattr(ref, "x_addr", None) is not None else -1),
                    int(getattr(ref, "y_addr", -1) if getattr(ref, "y_addr", None) is not None else -1),
                    int(getattr(ref, "x_fine_addr", -1) if getattr(ref, "x_fine_addr", None) is not None else -1),
                    int(getattr(ref, "y_fine_addr", -1) if getattr(ref, "y_fine_addr", None) is not None else -1),
                    round(float(getattr(ref, "angle_deg", 0.0) or 0.0), 6),
                    str(getattr(ref, "mode", "")),
                )
                for ref in (refs or [])
            )
            xy_items.append((int(universe), ref_sig))
        return (mod_sig, tuple(xy_items))

    def is_timeline_render_cache_current(self, proj=None) -> bool:
        # R187-①：删 audio_cache_allowed 条件——source/epoch/ready 已在
        # runtime_sig 的 _source_token 内（真变化由签名比对失效）。修前此条件
        # 对"带调制链但源非 file-ready"工程恒 False → is_current 永假 →
        # autobake 每次停止都全量重烘（用户实测反复重渲染）。serve 逐帧门
        # （_render_from_timeline_frame_cache）继续用 allowed，语义不变。
        if proj is None:
            proj = _project_of(self)
        tracks = list(getattr(proj, "tracks", []) or []) if proj is not None else []
        signature = self._cached_project_clip_layout_signature(tracks)
        runtime_sig = self._timeline_render_runtime_signature_value()
        return (
            getattr(self, "_timeline_render_lock_signature", None) == signature
            and getattr(self, "_timeline_render_frame_cache_signature", None) == signature
            and getattr(self, "_timeline_render_runtime_signature", None) == runtime_sig
            and bool(getattr(self, "_timeline_render_frame_cache", {}) or {})
        )

    def unlock_timeline_render_cache(self) -> None:
        self._timeline_render_cache_locked = False
        from .timeline_ready_range import retire_serving
        retire_serving(self)
        self._timeline_render_lock_signature = None
        self._timeline_render_runtime_signature = None
        self._timeline_render_lock_misses = 0
        # 脏标记增量渲染: 烘焙↔实时切换后帧来源变更, 旧 memo 不可复用
        self._dirty_render_item_memo.clear()
        self._dirty_render_uni_memo.clear()

    def lock_timeline_render_cache(self, proj=None, progress_callback: Optional[Callable] = None) -> int:
        """Compile all current timeline clips once and disable lazy cache builds."""
        # R178 P1-2：锁定全程强制 `_modulation_gain = 1.0` —— 调制 gain 是实时
        # 播放的混音倍率，若锁定/烘焙期间残留播放调制值（≠1），会被"烘死"进缓存
        # 帧，停止后回放恒带错误倍率。强制 1.0 后：① 烘焙产物与手动锁定一致；
        # ② 锁定签名恒按 gain=1.0 记（与强制值一致）；③ 播放中实时 gain≠1 时
        # serve 门（运行态指纹比对）自然失配 → 回退实时渲染，调制值在实时路径
        # 生效。完比恢复现场（上帝类禁新增方法，正文原地包 try/finally）。
        saved_gain = float(getattr(self, "_modulation_gain", 1.0) or 1.0)
        self._modulation_gain = 1.0
        try:
            was_locked = bool(getattr(self, "_timeline_render_cache_locked", False))
            self._report_timeline_render_progress(
                progress_callback,
                "prepare",
                0,
                1,
                "准备整条时间线预渲染...",
                0.0,
            )
            self._timeline_render_cache_locked = False
            self._timeline_render_cache_building_lock = True
            try:
                self._precompile_dirty = True
                self._precompile_signature = None
                self.precompile_project_clips(
                    proj,
                    progress_callback=lambda current, total, _clip: self._report_timeline_render_progress(
                        progress_callback,
                        "compile",
                        current,
                        total,
                        f"正在编译片段缓存 {current}/{max(1, total)}",
                        0.05 + 0.20 * (float(current) / max(1.0, float(total))),
                    ),
                )
            finally:
                self._timeline_render_cache_building_lock = False
            if proj is None:
                proj = _project_of(self)
            tracks = list(getattr(proj, "tracks", []) or []) if proj is not None else []
            signature = self._cached_project_clip_layout_signature(tracks)
            self._report_timeline_render_progress(
                progress_callback,
                "render",
                0,
                1,
                "正在生成逐帧渲染缓存...",
                0.25,
            )
            self._build_timeline_render_frame_cache(
                proj,
                signature,
                progress_callback=progress_callback,
                progress_start=0.25,
                progress_end=0.98,
            )
            self._timeline_render_lock_signature = signature
            self._timeline_render_runtime_signature = self._timeline_render_runtime_signature_value()
            self._timeline_render_lock_misses = 0
            self._timeline_render_cache_locked = True
            if not was_locked:
                self._last_render_time = None
            self._report_timeline_render_progress(
                progress_callback,
                "complete",
                1,
                1,
                "时间线预渲染完成，缓存已锁定",
                1.0,
            )
            return len(getattr(self, "_clip_compiled_cache", {}) or {})
        finally:
            self._modulation_gain = saved_gain

    def _report_timeline_render_progress(
        self,
        progress_callback: Optional[Callable],
        phase: str,
        current: int,
        total: int,
        message: str,
        percent: Optional[float] = None,
    ) -> None:
        if progress_callback is None:
            return
        try:
            total = max(1, int(total))
            current = max(0, min(int(current), total))
            if percent is None:
                percent = float(current) / float(total)
            progress_callback({
                "phase": str(phase),
                "current": current,
                "total": total,
                "message": str(message),
                "percent": max(0.0, min(1.0, float(percent))),
            })
        except TimelineBakeAbort:
            # R178 P0-1：让位中断必须穿透本函数 —— autobake 的进度回调
            # （timeline_autobake._make_progress_cb）靠抛 TimelineBakeAbort 在
            # ≤100ms 内中断逐帧循环（播放开始让位）；此前被下面 except 一并吞
            # 掉 → 让位失效，烘焙继续吞实时输出帧。本类同模块 10498 行定义
            # （方法调用时模块已加载完毕，无需函数内 import）。
            raise
        except Exception:
            log.debug("timeline render progress callback failed", exc_info=True)

    def _project_timeline_end(self, proj=None) -> float:
        end = 0.0
        for track in (getattr(proj, "tracks", []) or []) if proj is not None else []:
            for clip in getattr(track, "clips", []) or []:
                try:
                    c_end = float(getattr(clip, "start_time", 0.0) or 0.0) + max(
                        0.0, float(getattr(clip, "duration", 0.0) or 0.0)
                    )
                except Exception:
                    continue
                end = max(end, c_end)
        return end

    def _build_timeline_render_frame_cache(
        self,
        proj=None,
        signature: Optional[tuple] = None,
        progress_callback: Optional[Callable] = None,
        progress_start: float = 0.0,
        progress_end: float = 1.0,
    ) -> int:
        tracks = list(getattr(proj, "tracks", []) or []) if proj is not None else []
        if signature is None:
            signature = self._cached_project_clip_layout_signature(tracks)
        # R178 P1-2：删除"签名命中即复用旧缓存"的早退分支 —— 该分支不看锁定态
        # 来源，会把跨 gain 状态/跨播放会话的旧缓存当最新（烘焙产物必须是
        # gain=1.0 基准的本次全量渲染）。保守正确：命中签名也重烘。
        if proj is None or self._nm is None:
            self._timeline_render_frame_cache = {}
            self._timeline_render_record_source_frame_cache = {}
            self._timeline_render_frame_cache_signature = signature
            self._timeline_render_frame_cache_end = 0.0
            self._report_timeline_render_progress(
                progress_callback,
                "render",
                1,
                1,
                "没有可渲染的时间线缓存",
                progress_end,
            )
            return 0

        interval = max(0.005, float(getattr(self, "_timer", None).interval() if getattr(self, "_timer", None) is not None else 23) / 1000.0)
        interval = min(max(interval, 0.015), 0.05)
        end = self._project_timeline_end(proj)
        frame_count = int(math.ceil(max(0.0, end) / interval)) + 1

        # ── 并行渲染:多进程各渲一段帧,合并。逐帧与串行同一代码路径(见
        # _render_timeline_frame_range),故逐字节等价。门控关或失败则回退串行。──
        use_parallel = self._read_parallel_render_enabled() and frame_count >= 400
        if use_parallel:
            parallel_result = self._try_parallel_frame_cache(
                proj, signature, interval, end, frame_count,
                progress_callback, progress_start, progress_end,
            )
            if parallel_result is not None:
                frame_cache, record_source_cache = parallel_result
                self._timeline_render_frame_cache = frame_cache
                self._timeline_render_record_source_frame_cache = record_source_cache
                self._timeline_render_frame_cache_signature = signature
                self._timeline_render_frame_cache_interval = interval
                self._timeline_render_frame_cache_end = end
                return len(frame_cache)
            # None = 并行不可用/失败 → 落到下方串行路径

        frame_cache, record_source_cache = _render_all_frames(
            self, proj, {"frame_count": frame_count, "interval": interval, "end": end,
                         "progress_callback": progress_callback,
                         "progress_start": progress_start,
                         "progress_end": progress_end})
        self._timeline_render_frame_cache = frame_cache
        self._timeline_render_record_source_frame_cache = record_source_cache
        self._timeline_render_frame_cache_signature = signature
        self._timeline_render_frame_cache_interval = interval
        self._timeline_render_frame_cache_end = end
        return len(frame_cache)

    @staticmethod
    def _read_parallel_render_enabled() -> bool:
        # ⚠️ 默认关：实测该引擎的逐帧渲染存在长程(可达25s/1024帧)反向历史依赖(淡入
        # seam 链),且标杆时间线零空闲帧无法按 gap 安全切分。固定 lead-in 会产生 13% 帧
        # 全尺度偏差(max 254/255),无法保证逐字节等价。此路径保留待未来"依赖感知切分/
        # 状态可预计算"重构后再启用。显式 QASCADE_PARALLEL_RENDER=1 才开(仅实验)。
        raw = os.environ.get("QASCADE_PARALLEL_RENDER")
        if raw is None:
            return False   # 默认关(未证明逐字节安全前不启用)
        return str(raw).strip().lower() not in ("0", "false", "off", "no", "")

    def _try_parallel_frame_cache(
        self,
        proj,
        signature,
        interval: float,
        end: float,
        frame_count: int,
        progress_callback: Optional[Callable] = None,
        progress_start: float = 0.0,
        progress_end: float = 1.0,
    ):
        """尝试多进程并行渲染帧缓存。成功返回 (frame_cache, record_source_cache)，
        不可用/失败返回 None（调用方回退串行）。"""
        try:
            from .parallel_render import run_parallel_render
            proj_dict = proj.to_dict()
            repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            result = run_parallel_render(
                proj_dict, interval, end, frame_count, repo_root,
                progress_callback=progress_callback,
                progress_start=progress_start,
                progress_end=progress_end,
            )
            if result is None:
                return None
            frame_cache, record_cache = result
            # 完整性校验：帧数必须等于 frame_count，否则视为失败回退串行
            if len(frame_cache) != frame_count:
                log.warning(
                    "并行渲染帧数不符(%s≠%s)，回退串行", len(frame_cache), frame_count
                )
                return None
            return frame_cache, record_cache
        except Exception:
            log.exception("并行渲染失败，回退串行")
            return None

    def _render_timeline_frame_range(
        self,
        proj,
        start_idx: int,
        stop_idx: int,
        interval: float,
        end: float,
        frame_count: int,
        progress_callback: Optional[Callable] = None,
        progress_start: float = 0.0,
        progress_end: float = 1.0,
    ) -> Tuple[Dict[int, Dict[int, Tuple[np.ndarray, np.ndarray]]],
               Dict[int, Dict[int, Tuple[np.ndarray, np.ndarray]]]]:
        """渲染 [start_idx, stop_idx) 区间的帧缓存并返回 (frame_cache, record_source_cache)。

        串行路径传整段 [0, frame_count);并行 worker 各传自己那一段。逐帧逻辑完全一致
        → 拼接结果与串行逐字节等价。方法内部自带 proxy NM 装填/状态保存恢复,可重入。
        """
        real_nm = self._nm

        class _FrameCacheNetworkProxy:
            def __init__(self, target, build_mode: bool = False):
                self._target = target
                # 构建期跳过变化检测: changed_universes 唯一消费者 flush_universes_now
                # 是 no-op, 两次 array_equal(34.9 次/帧) 属死工作；实时路径不经本 proxy。
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
                if prev is not None and np.array_equal(prev[0], data_arr) and np.array_equal(prev[1], active_arr):
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

        proxy_nm = _FrameCacheNetworkProxy(real_nm, build_mode=True)
        old_locked = bool(getattr(self, "_timeline_render_cache_locked", False))
        old_building = bool(getattr(self, "_timeline_render_cache_building_lock", False))
        old_position = float(getattr(self, "_position", 0.0) or 0.0)
        old_suppress_position_emit = bool(getattr(self, "_suppress_position_emit", False))
        old_last_render_time = getattr(self, "_last_render_time", None)
        old_pending = bool(getattr(self, "_ltp_takeover_pending", False))
        old_pending_layer = getattr(self, "_ltp_takeover_layer", "")
        old_last_record = getattr(self, "_last_record_source_outputs_by_universe", {})
        old_layer_map = {
            str(layer): set(values or set())
            for layer, values in (getattr(self, "_last_output_universes_by_layer", {}) or {}).items()
        }
        old_last_universes = set(getattr(self, "_last_output_universes", set()) or set())
        frame_cache = TimelineFrameStore()
        record_source_cache = TimelineFrameStore()
        self._timeline_render_cache_locked = False
        self._timeline_render_cache_building_lock = True
        self._nm = proxy_nm
        self._suppress_position_emit = True
        self._last_render_time = None
        self._ltp_takeover_pending = False
        last_progress_emit = 0.0
        try:
            for idx in range(start_idx, stop_idx):
                pos = min(end, float(idx) * interval)
                self._render_position(pos, layer_name="clip_playback")
                layer_outputs: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}
                layers = getattr(self._nm, "_layers", {}) or {}
                playback_layer = layers.get("clip_playback", {}) if isinstance(layers, dict) else {}
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
                        if not (active_arr).any():
                            continue
                        data_arr = np.asarray(data, dtype=np.uint8)
                        if data_arr.shape != (512,):
                            fixed_data = np.zeros(512, dtype=np.uint8)
                            n = min(data_arr.size, 512)
                            if n > 0:
                                fixed_data[:n] = data_arr.reshape(-1)[:n]
                            data_arr = fixed_data
                        layer_outputs[int(universe)] = (data_arr.copy(), active_arr.copy())
                    except Exception:
                        continue
                frame_cache[int(idx)] = layer_outputs
                record_source_cache[int(idx)] = {
                    int(u): (np.asarray(data, dtype=np.uint8).copy(), np.asarray(active, dtype=bool).copy())
                    for u, (data, active) in (getattr(self, "_last_record_source_outputs_by_universe", {}) or {}).items()
                }
                # R188-⑤：GIL 让出点（烘焙线程实测 100% wall 占用饿死 UI；
                # sleep(0) 让出时间片，零字节影响；先例 W1 GIL 活锁修复）。
                time.sleep(0.001)
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
                        max(0.0, float(progress_end) - float(progress_start)) * frame_percent
                    )
                    self._report_timeline_render_progress(
                        progress_callback,
                        "render",
                        idx + 1,
                        frame_count,
                        f"正在渲染帧缓存 {idx + 1}/{frame_count}  位置 {pos:.2f}s/{end:.2f}s",
                        overall,
                    )
        except BaseException:
            frame_cache.clear()
            record_source_cache.clear()
            raise
        finally:
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

        return frame_cache, record_source_cache

    def export_frames(self, path: str) -> int:
        """逐帧流式导出完整缓存，保留原格式、掩码和原子替换。"""
        return export_timeline_frames(self, path)

    def _maybe_apply_clip_traj_overlay(self, outputs: dict, pos: float) -> None:
        """同一运行态轨迹叠加；保留原控制轨迹/释放/混合语义。"""
        apply_trajectory_overlay(self, outputs, pos)

    def _render_from_timeline_frame_cache(self, now: float, layer_name: str) -> bool:
        # M2 帧级门: 每帧起点清空复用项(非否决恒 None); 否决时暂存活跃项
        # 供主渲染循环复用, 免二次 _indexed_active_clip_items。
        prepare_cached_frame(self, now, layer_name)
        if layer_name != "clip_playback":
            return False
        if not audio_cache_allowed(self, bool(cache_source_value(self, "_timeline_render_cache_locked", False)), consume=True):
            return False
        # R14 保持消费语义(防陈旧) + M2 帧级门(2026-08-13): 锁定烘焙帧可能是
        # 消费标记前渲染的(含复活帧), 且缓存服务路径不经每项循环(消费标记/
        # 扣除/窗口重入清除都无法更新)。原会话级门(消费非空即禁命中)把一次
        # 消费后的整个会话 K2 服务永久关闭; 帧级门只在「本帧存在真实 hold 项
        # 且其消费掩码非空」时禁命中(实时渲染该帧必扣已消费通道, 烘焙帧无
        # 扣除必复活)。消费为空时判定 O(1) 短路 —— 从未消费的帧路径与
        # 改动前逐字节相同(零影响铁律)。否决时活跃项已由本门构建,
        # 暂存 _m2_gate_items 供主渲染循环复用(免二次索引构造)。
        if cache_source_value(self, "_hold_consumed_masks"):
            _vetoed, _items = self._hold_consumed_active_this_frame(now)
            if _vetoed:
                self._m2_gate_items = _items
                return False
        cache = cache_source_value(self, "_timeline_render_frame_cache", {}) or {}
        if not cache:
            return False
        proj = _project_of(self)
        tracks = list(getattr(proj, "tracks", []) or []) if proj is not None else []
        current_signature = self._cached_project_clip_layout_signature(tracks)
        if current_signature != cache_source_value(self, "_timeline_render_frame_cache_signature", None):
            # 布局已变（理论上 invalidate 已解锁，此处兜底）。解锁后回退实时渲染，
            # 否则锁仍生效会让 _compiled_clip_universe 返回空帧 → 黑帧。
            self.unlock_timeline_render_cache()
            return False
        if (
            self._timeline_render_runtime_signature_value()
            != cache_source_value(self, "_timeline_render_runtime_signature", None)
        ):
            # XY 基准 / 叠加增益变了：烘焙帧的基底已过时，解锁回退实时渲染并重渲。
            self.unlock_timeline_render_cache()
            return False
        interval = float(cache_source_value(self, "_timeline_render_frame_cache_interval", 0.023) or 0.023)
        end = float(cache_source_value(self, "_timeline_render_frame_cache_end", 0.0) or 0.0)
        idx = cache_index(interval, end, float(now))
        frame = read_cached_pair(cache, cache_source_value(self, "_timeline_render_record_source_frame_cache", {}) or {}, idx, reader=read_cached_frame)
        if frame is MISSING_FRAME:
            return False
        outputs = dict(frame[0] or {})  # 轨迹替换条目不能污染烘焙基底。
        record_source_outputs = frame[1] or {}
        active_universes: set[int] = set()
        changed_universes: set[int] = set()
        queued_worker_frame = False
        # ★ 调度台·控件轨迹叠加层（clip_traj）：在本层输出定稿后单点合并
        #   （worker 队列与直写两路共用 outputs —— 单写者纪律，v2.1 §5.4）。
        #   仅 clip_playback 层（preview 不叠加；record-source 另路）。
        if layer_name == "clip_playback":
            try:
                self._maybe_apply_clip_traj_overlay(outputs, float(now))
            except Exception:
                _traj_overlay_err(self)  # ★ R193/P2-02：首错告警+计数

        for universe, (_data, active) in outputs.items():
            if mask_count(np.asarray(active, dtype=bool)):
                active_universes.add(int(universe))

        prev_universes = set(
            (getattr(self, "_last_output_universes_by_layer", {}) or {}).get(layer_name, set())
        )
        stale_universes = prev_universes - active_universes
        if (
            bool(getattr(self._nm, "_running", False))
            and bool(getattr(self._nm, "_output_worker_enabled", False))
            and not bool(getattr(self, "_timeline_render_cache_building_lock", False))
            and hasattr(self._nm, "queue_clip_layer_frame")
        ):
            try:
                changed_universes = set(
                    self._nm.queue_clip_layer_frame(
                        layer_name,
                        outputs,
                        clear_universes=stale_universes,
                    )
                    or set()
                )
                queued_worker_frame = True
            except Exception:
                log.exception("timeline queue_clip_layer_frame failed")
                queued_worker_frame = False

        if not queued_worker_frame:
            for universe, (data, active) in outputs.items():
                active_arr = np.asarray(active, dtype=bool)
                if not mask_count(active_arr):
                    continue
                try:
                    changed = self._nm.update_layer(layer_name, int(universe), data, active=active_arr)
                except TypeError:
                    changed = self._nm.update_layer(layer_name, int(universe), data)
                if changed is not False:
                    changed_universes.add(int(universe))
        if getattr(self, "_ltp_takeover_pending", False):
            self._ltp_takeover_pending = False
            if hasattr(self._nm, "notify_clip_takeover"):
                # ★★ A10 P0：掩码 = outputs ∪ 节目级 scope；缺失时行为同修前。
                for universe, mask in _merge_takeover_outputs(
                        outputs, getattr(self, "_ltp_takeover_scope", None) or {}).items():
                    if mask_count(mask):
                        try:
                            self._nm.notify_clip_takeover(int(universe), mask)
                        except Exception:
                            log.exception(   # C: 曾静默 → 接管不同步；仅 pending 一次
                                "notify_clip_takeover failed (universe=%s)", universe)
                self._ltp_takeover_scope = None
        self._last_record_source_outputs_by_universe = {
            int(u): (np.asarray(data, dtype=np.uint8).copy(), np.asarray(active, dtype=bool).copy())
            for u, (data, active) in record_source_outputs.items()
        }
        if queued_worker_frame:
            layer_map = getattr(self, "_last_output_universes_by_layer", {}) or {}
            layer_map[layer_name] = set(active_universes)
            self._last_output_universes_by_layer = layer_map
            self._last_output_universes = set().union(*layer_map.values()) if layer_map else set(active_universes)
        else:
            self._clear_stale_timeline_layers(active_universes, layer_name=layer_name)
        flush_cached_frame(self, changed_universes, layer_name)
        return True

    def record_source_outputs_snapshot(self) -> Dict[int, Tuple[np.ndarray, np.ndarray]]:
        return {
            int(u): (data.copy(), active.copy())
            for u, (data, active) in (getattr(self, "_last_record_source_outputs_by_universe", {}) or {}).items()
        }

    def refresh_record_source_outputs_at(self, pos: float) -> Dict[int, Tuple[np.ndarray, np.ndarray]]:
        """Render the current playback position so opt-in clips can feed recording."""
        if not self._project_has_record_output_sources():
            self._last_record_source_outputs_by_universe = {}
            return {}
        was_pending = bool(getattr(self, "_ltp_takeover_pending", False))
        pending_layer = getattr(self, "_ltp_takeover_layer", "")
        try:
            # This is a recording-side sample, not a user playback takeover.
            self._ltp_takeover_pending = False
            self._render_position(float(pos), layer_name="clip_playback")
        finally:
            self._ltp_takeover_pending = was_pending
            self._ltp_takeover_layer = pending_layer
        return self.record_source_outputs_snapshot()

    def _project_has_record_output_sources(self) -> bool:
        proj = _project_of(self)
        if proj is None:
            return False
        for track in getattr(proj, "tracks", []) or []:
            if bool(getattr(track, "is_folder", False)):
                continue
            for clip in getattr(track, "clips", []) or []:
                if bool(getattr(clip, "record_output_source_enabled", False)):
                    return True
        return False

    def invalidate_clip_cache(self, clip_id: str):
        """清除指定 clip 的活跃通道缓存和 anchor 缓存。

        当 clip 属性（如 hold_last_frame、fade_in/out、方向效果等）被外部修改后，
        必须调用此方法，否则旧缓存会导致残留效果叠加。
        """
        # 生成线程模式：非生成线程(UI线程)的失效请求转 pending 队列，由生成线程帧起点消费，
        # 保证缓存单一属主=生成线程，避免并发清缓存竞态。生成线程自己 drain 时直接执行(下面正文)。
        # R173 竞态修复：原子捕获线程引用，入队后校验线程未停止；若 stop_gen_thread 在
        # 检查与入队之间执行，thread.is_alive()=False 或 _gen_thread 已变 None，回退直接清理。
        lease = begin_clip_invalidation(self, retire_inputs=True)
        try:
            self._timeline_render_cache_locked = False  # Veto serving before a queued runtime purge.
            thread = self._gen_thread
            if (self._gen_thread_enabled
                    and thread is not None
                    and threading.current_thread() is not thread):
                self.gen_queue_invalidate_clip(clip_id)
                # R178 P1-6：入队同时置脏 —— 队列消费在 gen 线程帧起点（播放中）/
                # 非播放 10Hz 分支；置脏让 worker 混音等其他路径立即按脏重建，
                # 不致在消费延迟窗口内用旧预编译结构。
                self._precompile_dirty = True
                # 安全闸：验证线程仍活跃或引用未变。若 stop_gen_thread 在入队后执行
                # （join 等待线程结束），is_alive() 返回 False 或 _gen_thread 已设
                # None，必须回退直接清理。
                if thread is not self._gen_thread or not thread.is_alive():
                    self._invalidate_clip_caches_for_keys([str(clip_id)])
                return
            self._invalidate_clip_caches_for_keys([str(clip_id)])
        finally:
            end_clip_invalidation(lease)

    def invalidate_clip_cache_batch(self, clip_ids) -> None:
        """R57：批量 clip 缓存失效，单趟合并。

        UI 批量应用（框选 N clip 后右键设参数/批量 fade）旧路径逐 clip 调
        invalidate_clip_cache，每次对全部缓存字典做全表前缀扫描；
        本方法一次扫描按 key 集合清理全部目标，语义与逐个调用完全一致。
        生成线程模式下转为一次 pending 集合合并入队。
        """
        ids = [str(c) for c in (clip_ids or [])]
        if not ids:
            return
        lease = begin_clip_invalidation(self, retire_inputs=True)
        try:
            self._timeline_render_cache_locked = False
            # R173 竞态修复：与 invalidate_clip_cache 同理，原子捕获线程引用，入队后
            # 校验线程仍活跃。批量失效同样存在 stop_gen_thread 在检查与入队之间执行的竞态窗口。
            thread = self._gen_thread
            if (self._gen_thread_enabled
                    and thread is not None
                    and threading.current_thread() is not thread):
                self.gen_queue_invalidate_clips(ids)
                self._precompile_dirty = True  # 与单 clip 入队一致，立即通知预编译路径。
                if thread is not self._gen_thread or not thread.is_alive():
                    self._invalidate_clip_caches_for_keys(ids)
                return
            self._invalidate_clip_caches_for_keys(ids)
        finally:
            end_clip_invalidation(lease)

    def _invalidate_clip_caches_for_keys(self, keys: list) -> None:
        """invalidate_clip_cache / invalidate_clip_cache_batch 的单一实现体。

        keys 为 clip id 字符串列表；对单 key 与旧逐 clip 行为逐条等价
        （含空串 key 时 startswith('') 全清的历史语义）。
        """
        keyset = set(keys)
        prefixes = tuple(keys)
        self.unlock_timeline_render_cache()
        from .timeline_cache_notifications import clip_store_check_context, notify_project_change
        recheck = clip_store_check_context(self, keyset)
        _purge_lt_scope_caches(self, keyset)
        for cache in (self._clip_active_channels_cache,
                      self._clip_frame_index_cache,
                      self._clip_anchor_cache,
                      self._light_tracking_state,
                      self._light_tracking_time,
                      self._light_tracking_color_cache):
            for k in [k for k in cache if k.startswith(prefixes)]:
                del cache[k]
        # 同时清除灯跟踪状态，避免撤销后旧状态继续影响输出（含在上方字典组内）
        for clip_key in keys:
            self._clear_fade_in_anchor_cache_for_clip(clip_key)
        # 顺序分组/批次播放会缓存源设备重映射和空间变化检测。
        # 修改分组、批次或顺序后必须清掉，否则会继续按旧顺序播放。
        for k in [k for k in self._clip_sequential_remap_cache
                  if (str(k) in keyset
                      or any(str(k).startswith(f"{ck}::") or str(k).startswith(f"spatial::{ck}::")
                             for ck in keys))]:
            del self._clip_sequential_remap_cache[k]
        from .timeline_clip_invalidation import purge_clip_geometry_caches
        purge_clip_geometry_caches(self, keyset)
        # A2: 快表键含 id(clip) 无法按 clip_id 字符串局部删除 → 整表清空
        # (表小, 下一帧 quick_key 命中时顺手回填, 无重复编译)。
        self._clip_compiled_fast.clear()
        for k in [k for k in self._clip_render_mask_cache
                  if isinstance(k, tuple) and k and str(k[0]) in keyset]:
            del self._clip_render_mask_cache[k]
            self._clip_render_mask_any_cache.pop(k, None)
        from .timeline_incremental_checkpoint import invalidate_stores
        if recheck is None:
            invalidate_stores(self)
            self._timeline_render_frame_cache_signature = None
        for clip_key in keys:
            self._clip_static_signature_cache.pop(clip_key, None); self._clip_motor_force_profile_cache.pop(clip_key, None)
            self._clip_recorded_source_span_cache.pop(clip_key, None); self._clip_recorded_cycle_cache.pop(clip_key, None)
            self._clip_transition_content_sig_cache.pop(clip_key, None)
            self._clip_frame_universes_cache.pop(clip_key, None)
            # 顺序分组时序缓存(组数/批次数/单组时长/组间停顿)也必须随 clip 属性失效，
            # 否则播放中经右键菜单修改分组参数后，引擎继续按旧分组/旧总时长播放
            self._clip_seq_timing_cache.pop(clip_key, None)
        self._playback_active_index_dirty = True
        self._precompile_dirty = True
        self._clip_layout_signature_cache_key = None
        self._clip_layout_signature_cache = None
        self._clip_layout_signature_dirty = True
        self._last_record_source_outputs_by_universe = {}
        # 脏标记增量渲染: clip 内容/属性变化后旧签名可能仍命中 → 清该 clip 条目
        for k in [k for k in self._dirty_render_item_memo
                  if isinstance(k, tuple) and len(k) > 1 and str(k[1]) in keyset]:
            del self._dirty_render_item_memo[k]
        self._dirty_render_uni_memo.clear()
        if recheck is not None:
            notify_project_change(*recheck, engine=self)

    def _get_array_u8_512(self) -> np.ndarray:
        """从池中获取或创建 np.zeros(512, dtype=np.uint8) 数组。"""
        if self._numpy_pool_u8_512:
            arr = self._numpy_pool_u8_512.pop()
            arr.fill(0)  # 清零后返回
            return arr
        return np.zeros(512, dtype=np.uint8)

    def _get_array_bool_512(self) -> np.ndarray:
        """从池中获取或创建 np.zeros(512, dtype=bool) 数组。"""
        if self._numpy_pool_bool_512:
            arr = self._numpy_pool_bool_512.pop()
            arr.fill(False)
            return arr
        return np.zeros(512, dtype=bool)

    def _return_array_u8_512(self, arr: np.ndarray):
        """归还数组到池（如果池未满）。"""
        if len(self._numpy_pool_u8_512) < self._numpy_pool_max_size:
            self._numpy_pool_u8_512.append(arr)

    def _return_array_bool_512(self, arr: np.ndarray):
        """归还数组到池（如果池未满）。"""
        if len(self._numpy_pool_bool_512) < self._numpy_pool_max_size:
            self._numpy_pool_bool_512.append(arr)

    def invalidate_all_caches(self):
        """清除所有 clip 缓存。undo/redo/删除 clip 后调用，确保重新从 project 读取最新数据。

        同时清空 NetworkManager 的 timeline 层，避免停止状态下删除/撤销 clip 后
        旧帧数据留在层中被混音器持续输出。
        """
        # 生成线程模式：非生成线程的失效请求转 pending 队列(同 invalidate_clip_cache)。
        # R178 P2-12：入队后校验线程仍活跃 —— stop_gen_thread 在检查与入队之间
        # 执行时队列无人消费（is_alive()=False 或 _gen_thread 已设 None），回退
        # 直接清理（照 invalidate_clip_cache 同款安全闸）。正文拆模块级
        # `_invalidate_all_caches_impl`（上帝类禁新增方法；且不能递归调本方法
        # —— 死线程引用仍在时条件恒真会无限入队）。
        thread = self._gen_thread
        if (self._gen_thread_enabled
                and thread is not None
                and threading.current_thread() is not thread):
            self.gen_queue_invalidate_all()
            if thread is not self._gen_thread or not thread.is_alive():
                # 回退同时撤销刚入队的标志（gen 线程重启后不得重复消费；
                # 清理本身幂等，但队列须与实际已执行状态一致）。
                with self._gen_pending_invalidate_lock:
                    self._gen_pending_invalidate_all = False
                _invalidate_all_caches_impl(self)
            return
        _invalidate_all_caches_impl(self)

    # ── 第3步：统一生成线程 ────────────────────────────────────────────────
    def set_gen_clock_source(self, fn: Optional[Callable[[], Tuple[bool, float]]]) -> None:
        """注入播放时钟源。fn() → (is_playing, pos_seconds)。由 UI 侧提供
        (读 ctrl.is_playing / ctrl._play_t0)。生成线程据此独立推进 render。"""
        self._gen_clock_source = fn
        from .timeline_temporal_sample_clock import register_sample_clock_source
        register_sample_clock_source(self, fn)

    def gen_thread_active(self) -> bool:
        return bool(self._gen_thread_enabled and self._gen_thread is not None
                    and self._gen_thread.is_alive())

    def start_gen_thread(self) -> bool:
        """启动生成线程(仅当 QASCADE_GEN_THREAD 开且已注入时钟源)。幂等。"""
        if not _prepare_gen_thread_start(self):  # ★ R193/CC-01：预检+排僵尸
            return False
        if self._gen_thread is not None and self._gen_thread.is_alive():
            return True
        from .gen_thread_lifecycle import start_owned_generator
        start_owned_generator(self)
        _freeze_gc_once(self)  # ★ R193/CC-06
        log.info("统一生成线程已启动 (QASCADE_GEN_THREAD)，worker 混音已让路，gc.freeze() 已执行")
        return True

    def stop_gen_thread(self) -> None:
        thread = self._gen_thread
        if thread is None:
            return
        self._gen_stop.set()
        if thread is not threading.current_thread():
            thread.join(timeout=1.0)
        # ★ R193/CC-01：join 超时不清引用（僵尸由 start 排空），置 None 会被复活
        if thread.is_alive():
            self._gen_zombie = thread
        self._gen_thread = None
        # ★ R193：stop 收尾排空残留失效请求——invalidate 的"线程活→入队"检查
        #   与 stop 存在竞态窗口（test_playback_engine_race_condition 哨兵），
        #   线程死后队列必须就地消费，否则缓存永不失效。
        try:
            self._gen_drain_pending_invalidate()
        except Exception:
            log.exception("stop_gen_thread 收尾排空失效请求失败")
        from .gen_thread_lifecycle import return_worker_after_stop
        return_worker_after_stop(thread, self._nm)

    def _gen_loop(self) -> None:
        """生成线程主循环：自适应时钟节拍。

        用 time.perf_counter()（Windows 上封装 QPC）替代固定 interval。
        每帧测量实际渲染耗时，动态调整 sleep 时长：
          - 帧耗时 < 目标(23ms) → sleep 差值
          - 帧耗时 >= 目标 → 不 sleep，立即开始下一帧（CPU 忙时自动降帧率）
        配合 gc.freeze() 消除 GC 暂停，实现稳定 43Hz（实时负载下）。
        模态冻结冻住的是 UI 事件循环，本线程照跑 → 灯不停。
        """
        target_frame_s = max(0.001, float(self._gen_interval_sec))  # 0.023
        _probe = bool(os.environ.get("QASCADE_GEN_PROBE"))
        _p_gap = []       # 实际帧间隔(ms)
        _p_render = []    # render 单帧耗时(ms)
        _p_last = 0.0
        _p_next_log = time.monotonic() + 2.0
        _frame_start = 0.0  # 当前帧起始 perf_counter
        while not self._gen_stop.is_set():
            src = self._gen_clock_source
            if src is None:
                # 仍由同一 gen owner 混音其它实时层，再等待原帧间隔。
                from .gen_thread_lifecycle import mix_while_waiting
                if mix_while_waiting(self):
                    break
                continue
            try:
                playing, pos = src()
            except Exception as exc:
                _gen_clock_source_backoff(self, exc); continue  # ★ R193/P1-2 + R194/NB-08
            if not playing:
                # R178 P1-6：停止态也消费失效队列（防滞留到下次播放）+ 10Hz 降频
                if _gen_idle_poll(self):
                    break
                continue

            # 自适应节拍：在上帧结束时用 perf_counter 测总耗时
            if _frame_start > 0.0:
                if self._adaptive_sleep_enabled:
                    # A4 4 级睡眠：远离截止大睡(释 GIL 给 PyQt)，临近细睡精准唤醒，
                    # 最后 yield 消 Windows 定时器颗粒抖动。总睡眠≈剩余预算，保持 43Hz。
                    deadline = _frame_start + target_frame_s
                    while not self._gen_stop.is_set():
                        remaining = deadline - time.perf_counter()
                        if remaining <= 0.0:
                            break
                        if remaining > 0.015:
                            nap = 0.010
                        elif remaining > 0.006:
                            nap = 0.005
                        elif remaining > 0.0015:
                            nap = 0.001
                        else:
                            time.sleep(0)  # 让出时间片，精准逼近截止
                            continue
                        if self._gen_stop.wait(nap):
                            break
                    if self._gen_stop.is_set():
                        break
                else:
                    elapsed = time.perf_counter() - _frame_start
                    sleep_s = target_frame_s - elapsed
                    if sleep_s > 0.001:
                        if self._gen_stop.wait(sleep_s):
                            break
                    # 若 elapsed >= target_frame_s，不 sleep，立即下一帧
            _frame_start = time.perf_counter()

            # 帧起点：先消费 UI 线程排入的失效请求(缓存单一属主=本线程)
            self._gen_drain_pending_invalidate(); _gen_frame_begin(self)
            _t0 = time.perf_counter() if _probe else 0.0
            from .gen_thread_lifecycle import render_and_mix
            if not render_and_mix(self, pos):
                break
            if _probe:
                _tnow = time.perf_counter()
                if _p_last > 0.0:
                    _p_gap.append((_tnow - _p_last) * 1000.0)
                _p_last = _tnow
                _p_render.append((time.perf_counter() - _t0) * 1000.0)
                if _tnow >= _p_next_log and (_p_gap or _p_render):
                    def _pc(xs, q):
                        s = sorted(xs); return s[min(len(s) - 1, int(len(s) * q))] if s else 0.0
                    log.info(
                        "GEN LOOP ADAPTIVE: n=%d | 实际帧间隔 p50=%.1f p95=%.1f max=%.1f ms(目标%.0f) | "
                        "render p50=%.1f p95=%.1f max=%.1f ms",
                        len(_p_render),
                        _pc(_p_gap, .5), _pc(_p_gap, .95), max(_p_gap) if _p_gap else 0.0,
                        target_frame_s * 1000.0,
                        _pc(_p_render, .5), _pc(_p_render, .95), max(_p_render) if _p_render else 0.0,
                    )
                    _p_gap = []; _p_render = []; _p_next_log = _tnow + 2.0

    # ── pending-invalidate：门控开时 UI 线程不直接清缓存，只入队；生成线程帧起点消费 ──
    def gen_queue_invalidate_clip(self, clip_id: str) -> None:
        with self._gen_pending_invalidate_lock:
            self._gen_pending_invalidate_clips.add(str(clip_id))

    def gen_queue_invalidate_clips(self, clip_ids) -> None:
        """R57：批量入队（UI 批量应用路径，一次锁开销合并 N 个 id）。"""
        with self._gen_pending_invalidate_lock:
            for cid in (clip_ids or []):
                self._gen_pending_invalidate_clips.add(str(cid))

    def gen_queue_invalidate_all(self) -> None:
        with self._gen_pending_invalidate_lock:
            self._gen_pending_invalidate_all = True
            self._gen_pending_invalidate_clips.clear()

    def _gen_drain_pending_invalidate(self) -> None:
        lease = None
        with self._gen_pending_invalidate_lock:
            do_all = self._gen_pending_invalidate_all
            clips = self._gen_pending_invalidate_clips
            if do_all or clips:
                lease = begin_clip_invalidation(self)
            self._gen_pending_invalidate_all = False
            self._gen_pending_invalidate_clips = set()
        try:
            if do_all:
                self.invalidate_all_caches()
            elif clips:
                for cid in clips:
                    self.invalidate_clip_cache(cid)
        finally:
            end_clip_invalidation(lease)

    def _clip_cache_id(self, clip: Clip) -> str:
        # A2 任务构建瘦身: 结果懒预缓存到 clip 对象(免每帧 str 构建 + or 分支)。
        # 缓存 (原始 id 引用, 结果) 对; 命中时用 `is` 指针校验 —— clip.id 事后被
        # 重赋值(Clip.copy 换新 uuid)或 deepcopy 拷贝出独立 id 对象时旧缓存自动
        # 失配重算, 返回值与旧路径逐字节相同。设置失败(只读对象)回退每次计算。
        memo = getattr(clip, "_clip_cache_id_memo", None)
        if memo is not None:
            raw, cached = memo
            if getattr(clip, "id", "") is raw:
                return cached
        raw = getattr(clip, "id", "") or ""
        if not raw:
            # R178 P2-10：无 id clip 就地补 uuid —— 旧兜底 str(id(clip)) 是进程
            # 内地址，对象释放后地址复用 → 两代 clip 共用缓存键（跨代串台，旧
            # 缓存帧串进新 clip）。Clip 是 dataclass（id 可写）；只读对象回退
            # 旧兜底（罕见，日志可见）。
            try:
                clip.id = str(uuid.uuid4())
                raw = str(clip.id)
            except Exception:
                log.debug("clip 无 id 且就地补 uuid 失败，回退 id() 兜底",
                          exc_info=True)
        cached = str(raw or id(clip))
        try:
            clip._clip_cache_id_memo = (raw, cached)
        except Exception:
            pass
        return cached

    def _track_state_signature(self, tracks: list) -> tuple:
        return tuple(
            (
                str(getattr(track, "id", "") or id(track)),
                str(getattr(track, "parent_id", "") or ""),
                bool(getattr(track, "is_folder", False)),
                bool(getattr(track, "muted", False)),
                bool(getattr(track, "solo", False)),
            )
            for track in (tracks or [])
        )

    def _project_clip_layout_signature(self, tracks: list) -> tuple:
        out = []
        for order, track in enumerate(tracks or []):
            track_id = str(getattr(track, "id", "") or id(track))
            clips = []
            for clip in (getattr(track, "clips", []) or []):
                clips.append((
                    self._clip_cache_id(clip),
                    round(float(getattr(clip, "start_time", 0.0) or 0.0), 6),
                    round(float(getattr(clip, "duration", 0.0) or 0.0), 6),
                    self._clip_hold_mode(clip),
                    tuple(int(u) for u in (getattr(clip, "clip_universes", []) or [])),
                    len(getattr(clip, "frames", []) or []),
                ))
            # 2026-08-13 R13: 签名纳入轨道 M/S/父子链(_track_state_signature)。
            # 修复前签名只看 (order, id, clips) → 点击静音/单播不改变签名,
            # 锁定时间线渲染帧缓存继续放旧帧(REPRO-B「点了静音不静音」)。
            out.append((int(order), track_id, tuple(clips), self._track_state_signature([track])[0]))
        return tuple(out)

    def _cached_project_clip_layout_signature(self, tracks: list) -> tuple:
        # ── 验证节流快路径 ──────────────────────────────────────────────
        # 未 dirty 且距上次校验 < 120ms → 直接返回缓存签名，跳过 O(全部clip) 的 key 重建。
        # 真实编辑经 invalidate_* 置 dirty=True，立即失效即时重建；播放态无编辑，
        # dirty 恒 False、签名恒定，此路径 100% 正确且省下每 tick 的全量扫描。
        if (
            not self._clip_layout_signature_dirty
            and self._clip_layout_signature_cache is not None
        ):
            _now_wall = time.monotonic()
            _last = self._clip_layout_sig_check_time
            if _last >= 0.0 and (_now_wall - _last) < self._clip_layout_sig_check_interval:
                return self._clip_layout_signature_cache
            self._clip_layout_sig_check_time = _now_wall
        else:
            self._clip_layout_sig_check_time = time.monotonic()
        tracks = list(tracks or [])
        key_parts = []
        for order, track in enumerate(tracks):
            clips = getattr(track, "clips", []) or []
            key_parts.append((
                int(order),
                str(getattr(track, "id", "") or id(track)),
                # 2026-08-13 R13: 节流键同步纳入 M/S/父子链 —— 与
                # _project_clip_layout_signature 同构, 否则 mute 变化后节流
                # 快路径(120ms)仍返回旧签名, 锁定帧缓存继续放旧帧。
                self._track_state_signature([track])[0],
                tuple(
                    (
                        self._clip_cache_id(clip),
                        round(float(getattr(clip, "start_time", 0.0) or 0.0), 6),
                        round(float(getattr(clip, "duration", 0.0) or 0.0), 6),
                        self._clip_hold_mode(clip),
                        tuple(int(u) for u in (getattr(clip, "clip_universes", []) or [])),
                        len(getattr(clip, "frames", []) or []),
                    )
                    for clip in clips
                ),
            ))
        key = tuple(key_parts)
        if (
            self._clip_layout_signature_cache_key == key
            and self._clip_layout_signature_cache is not None
        ):
            # key 未变：清 dirty，让节流快路径下次生效(否则 invalidate 后布局没实际变化时
            # dirty 恒 True，快路径永远进不去，节流失效)。
            self._clip_layout_signature_dirty = False
            return self._clip_layout_signature_cache
        signature = self._project_clip_layout_signature(tracks)
        self._clip_layout_signature_cache_key = key
        self._clip_layout_signature_cache = signature
        self._clip_layout_signature_dirty = False
        return signature

    def _render_track_state(self, tracks: list) -> tuple[list, dict[str, object], bool]:
        by_parent = {}
        for track in tracks:
            by_parent.setdefault(getattr(track, "parent_id", "") or "", []).append(track)
        ordered_tracks = []

        def _walk(parent_id):
            for item in by_parent.get(parent_id, []):
                ordered_tracks.append(item)
                if bool(getattr(item, "is_folder", False)):
                    _walk(getattr(item, "id", ""))

        _walk("")
        seen = {id(item) for item in ordered_tracks}
        ordered_tracks.extend([item for item in tracks if id(item) not in seen])
        track_by_id = {
            str(getattr(track, "id", "")): track
            for track in tracks
            if getattr(track, "id", None)
        }

        def _muted(track):
            return self._track_or_ancestor_muted(track, track_by_id)

        def _soloed(track):
            return self._track_or_ancestor_soloed(track, track_by_id)

        any_solo = any(
            _soloed(track)
            for track in ordered_tracks
            if not bool(getattr(track, "is_folder", False)) and not _muted(track)
        )
        return ordered_tracks, track_by_id, bool(any_solo)

    def _render_struct_for_tracks(self, tracks: list) -> dict:
        """缓存版渲染结构：层级展开顺序 / track_by_id / parent_map / any_solo / 文件夹链。

        键 = 每条轨道的 (对象身份, parent_id, is_folder, muted, solo) 元组。任一改变
        (含增删轨/重排/改父/折叠/静音/独奏)都会换键触发重建；否则逐帧 O(1) 命中，
        不再每帧重建 by_parent/ordered/track_by_id/track_parent_map 与逐轨走祖先链。
        """
        tracks = list(tracks or [])
        key = tuple(
            (
                id(t),
                str(getattr(t, "parent_id", "") or ""),
                bool(getattr(t, "is_folder", False)),
                bool(getattr(t, "muted", False)),
                bool(getattr(t, "solo", False)),
            )
            for t in tracks
        )
        cached = self._render_struct_cache
        if cached is not None and self._render_struct_cache_key == key:
            return cached

        ordered_tracks, track_by_id, any_solo = self._render_track_state(tracks)
        track_parent_map: Dict[str, str] = {
            str(getattr(t, "id", "")): (getattr(t, "parent_id", "") or "")
            for t in tracks
            if getattr(t, "id", None)
        }
        # 预算所有轨道的文件夹链(跨帧持久)，供 clip 排序比较器零成本取用。
        folder_chain_cache: Dict[str, Tuple[str, ...]] = {}
        for t in ordered_tracks:
            tid = str(getattr(t, "id", "") or "")
            if not tid or tid in folder_chain_cache:
                continue
            chain_rev: List[str] = []
            seen: set[str] = set()
            if bool(getattr(t, "is_folder", False)):
                chain_rev.append(tid)
            pid = str(getattr(t, "parent_id", "") or "")
            while pid and pid not in seen:
                seen.add(pid)
                chain_rev.append(pid)
                pid = track_parent_map.get(pid, "") or ""
            folder_chain_cache[tid] = tuple(reversed(chain_rev))

        struct = {
            "ordered_tracks": ordered_tracks,
            "track_by_id": track_by_id,
            "track_parent_map": track_parent_map,
            "any_solo": bool(any_solo),
            "folder_chain_cache": folder_chain_cache,
        }
        self._render_struct_cache_key = key
        self._render_struct_cache = struct
        return struct

    def _fixture_defs_index(self, proj) -> dict:
        """缓存版 fixture_defs id->def 索引（随列表对象身份/长度变化重建）。"""
        defs = getattr(proj, "fixture_defs", None) if proj is not None else None
        if not defs:
            return {}
        key = (id(defs), len(defs))
        if (self._fixture_defs_index_cache is not None
                and self._fixture_defs_index_cache_key == key
                and getattr(self, "_fixture_defs_index_ref", None) is defs):
            return self._fixture_defs_index_cache
        index = {
            str(getattr(fd, "id", "")): fd
            for fd in defs
            if getattr(fd, "id", None)
        }
        self._fixture_defs_index_cache_key = key
        self._fixture_defs_index_cache = index
        self._fixture_defs_index_ref = defs
        return index

    def _fixtures_by_id_index(self, proj) -> dict:
        """缓存版 fixtures id->instance 索引（随列表对象身份/长度变化重建）。"""
        fixtures = getattr(proj, "fixtures", None) if proj is not None else None
        if not fixtures:
            return {}
        key = (id(fixtures), len(fixtures))
        if (self._fixtures_by_id_index_cache is not None
                and self._fixtures_by_id_index_cache_key == key
                and getattr(self, "_fixtures_by_id_index_ref", None) is fixtures):
            return self._fixtures_by_id_index_cache
        index = {
            str(getattr(fi, "id", "")): fi
            for fi in fixtures
            if str(getattr(fi, "id", "") or "")
        }
        self._fixtures_by_id_index_cache_key = key
        self._fixtures_by_id_index_cache = index
        self._fixtures_by_id_index_ref = fixtures
        return index

    def _target_universes_for_clip(self, clip: Clip, track) -> list[int]:
        try:
            track_universes = [
                int(u)
                for u in (getattr(track, "universes", None) or [getattr(track, "universe", 1)])
            ]
        except Exception:
            track_universes = [1]
        clip_universes = getattr(clip, "clip_universes", None) or []
        if clip_universes:
            source = clip_universes
        else:
            source = self._clip_frame_universes(clip) or track_universes
        return sorted({int(u) for u in source})

    def _clip_index_scope_mask(self, clip: Clip, universe: int) -> np.ndarray:
        """Cheap channel scope for playback index construction.

        The render path still computes the exact active mask.  This method avoids
        scanning frame payloads for every hold clip while building the timeline
        index at play start.
        """
        universe = int(universe)
        mask = self._get_array_bool_512()
        try:
            active_map = getattr(clip, "active_channels_by_universe", {}) or {}
            channels = active_map.get(universe) or active_map.get(str(universe))
            if channels:
                for ch in channels:
                    idx = int(ch) - 1
                    if 0 <= idx < 512:
                        mask[idx] = True
                if (mask).any():
                    return mask
        except Exception:
            # C: 曾静默 → 读 active_channels 失败后退化到全活跃掩码(过于保守,
            # 会让 LTP/接管范围变宽)。构建索引期为非热路径, 直接 exception。
            log.exception(
                "active_channels_by_universe read failed for clip %s",
                getattr(clip, "id", None))
        try:
            base_order_mask = self._clip_base_order_channel_mask(clip, universe)
            if base_order_mask is not None and (base_order_mask).any():
                return np.asarray(base_order_mask, dtype=bool).copy()
        except Exception:
            # C: 同上 —— 兜底掩码失败最终回退 mask[:]=True(全活跃)。可见即可。
            log.exception(
                "base_order channel mask lookup failed for clip %s",
                getattr(clip, "id", None))
        mask[:] = True
        return mask

    def _build_playback_active_index(self, tracks: list, ordered_tracks: list) -> dict:
        starts: list[float] = []
        active_events: list[tuple] = []
        hold_events: list[tuple] = []
        hold_scope_by_universe: dict[int, np.ndarray] = {}
        track_order_by_id = {
            str(getattr(track, "id", "") or id(track)): int(order)
            for order, track in enumerate(ordered_tracks or [])
        }
        for track_order, track in enumerate(ordered_tracks or []):
            if bool(getattr(track, "is_folder", False)):
                continue
            track_order = track_order_by_id.get(str(getattr(track, "id", "") or id(track)), int(track_order))
            for clip in (getattr(track, "clips", []) or []):
                try:
                    start_t = float(getattr(clip, "start_time", 0.0) or 0.0)
                    duration = max(0.0, float(getattr(clip, "duration", 0.0) or 0.0))
                except Exception:
                    continue
                if duration <= 0.0:
                    continue
                hold_mode = self._clip_hold_mode(clip)
                target_universes = self._target_universes_for_clip(clip, track)
                item = (start_t, track_order, track, clip, duration, hold_mode, target_universes)
                active_end = start_t + duration
                if hold_mode == "none":
                    # ★ 2026-08-11 短clip过渡帧中途消失修复: 淡入窗口 [0, fade_in]
                    # 未走完(fade_in > duration)时, clip 在 t=duration 退出活跃集 →
                    # _mix_clips 每帧从零重建 → 其通道瞬间归 0 = 「过渡到中途后续帧
                    # 消失」(同域其它clip非静态时逐帧重建, 用户实测; solo 无活跃项
                    # 层保持 → 单独播放正常)。延长活跃窗口至 fade_in: 窗口内
                    # _clip_fade_gain 封顶=1(值=末帧=淡入完成值), 平滑保持对齐
                    # solo 语义, 到 fade_in 后正常结束。
                    fade_in_raw = max(0.0, float(getattr(clip, "fade_in", 0.0) or 0.0))
                    if fade_in_raw > duration + 1e-9:
                        active_end = start_t + fade_in_raw
                active_events.append((start_t, active_end, item))
                starts.append(start_t)
                if hold_mode != "none":
                    hold_events.append((start_t + duration, item))
                    for universe in target_universes:
                        active = self._clip_index_scope_mask(clip, int(universe))
                        track_mask = self._track_filter_mask_for_universe(track, int(universe))
                        if track_mask is not None:
                            active = active & np.asarray(track_mask, dtype=bool)
                        if not (active).any():
                            continue
                        scope = hold_scope_by_universe.setdefault(int(universe), np.zeros(512, dtype=bool))
                        scope |= active
        active_events.sort(key=lambda item: (float(item[0]), float(item[1])))
        hold_events.sort(key=lambda item: (float(item[0]), int(item[1][1])))
        bucket_size = 0.5
        active_buckets: dict[int, list[tuple]] = {}
        for event in active_events:
            start_t, end_t, _item = event
            first_bucket = int(math.floor(float(start_t) / bucket_size))
            last_bucket = int(math.floor(max(float(start_t), float(end_t) - 1e-9) / bucket_size))
            for bucket in range(first_bucket, last_bucket + 1):
                active_buckets.setdefault(bucket, []).append(event)
        return {
            "track_state": self._track_state_signature(tracks),
            "clip_layout": self._project_clip_layout_signature(ordered_tracks),
            "active_events": active_events,
            "active_starts": [float(item[0]) for item in active_events],
            "active_bucket_size": bucket_size,
            "active_buckets": active_buckets,
            "hold_events": hold_events,
            "hold_starts": [float(item[0]) for item in hold_events],
            "hold_scope_by_universe": hold_scope_by_universe,
        }

    def _playback_index_for_tracks(self, tracks: list, ordered_tracks: list) -> dict:
        index = self._playback_active_index
        have_index = (not self._playback_active_index_dirty) and isinstance(index, dict)
        if have_index:
            # 廉价校验：轨道结构签名(O(tracks))一定查。clip 布局签名(O(全部clip))较贵，
            # 节流到每 ~120ms 才查一次——真实编辑都会经 invalidate_* 置 dirty 立即重建，
            # 此处仅为兜底未走 invalidate 的直接拖拽改 duration/start_time。
            track_state = self._track_state_signature(tracks)
            if index.get("track_state") != track_state:
                have_index = False
            else:
                try:
                    nowm = time.monotonic()
                except Exception:
                    nowm = 0.0
                last = self._playback_index_check_time
                if last < 0.0 or (nowm - last) >= self._playback_index_check_interval:
                    self._playback_index_check_time = nowm
                    # 与 _build_playback_active_index 构造分支同源: 直接用展开序
                    # 计算, 不走 _cached_project_clip_layout_signature 共享缓存 ——
                    # 该缓存的调用方(淡入锚点 ctx 等)传 proj.tracks 原序, 两序不同,
                    # 若共用缓存则 key 在展开序/原序间每 120ms 来回翻转, 造成:
                    # ① 锚点 ready_context 窗口边缘失配 → 边界锚点全量重建(实测
                    #    1~49 对/次); ② 本校验恒不等 → playback index 每 120ms
                    #    被全量重建(事件桶 O(clips) 级开销)。
                    clip_layout = self._project_clip_layout_signature(ordered_tracks)
                    if index.get("clip_layout") != clip_layout:
                        have_index = False
        if have_index:
            return index
        index = self._build_playback_active_index(tracks, ordered_tracks)
        self._playback_active_index = index
        self._playback_active_index_dirty = False
        try:
            self._playback_index_check_time = time.monotonic()
        except Exception:
            self._playback_index_check_time = 0.0
        return index

    def _indexed_active_clip_items(
        self,
        now: float,
        tracks: list,
        ordered_tracks: list,
        track_by_id: dict[str, object],
        any_solo: bool,
        timer_interval_s: float,
    ) -> list:
        index = self._playback_index_for_tracks(tracks, ordered_tracks)
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
        bucket = int(math.floor(float(now) / bucket_size))
        for start_t, end_t, item in list(active_buckets.get(bucket, []) or []):
            if not (float(start_t) <= float(now) < float(end_t)):
                continue
            _start_t, track_order, track, clip, duration, _hold_mode, target_universes = item
            if not _track_allowed(track):
                continue
            local_t = float(now) - float(_start_t)
            hold_mode = self._clip_hold_mode(clip)
            hold_extension = False
            if hold_mode != "none" and duration > 0.0:
                clip_id = self._clip_cache_id(clip)
                hold_key = f"{clip_id}::{track_order}"
                hold_seam_enter = min(duration * 0.25, max(1e-4, float(timer_interval_s) * 1.35))
                hold_seam_exit = min(duration * 0.25, max(1e-4, float(timer_interval_s) * 1.75))
                latched = bool(self._hold_boundary_latch.get(hold_key, False))
                if local_t >= (duration - (hold_seam_exit if latched else hold_seam_enter)):
                    hold_extension = True
                    self._hold_boundary_latch[hold_key] = True
                    local_t = max(0.0, duration - 1e-4)
                elif latched and local_t >= (duration - hold_seam_exit):
                    hold_extension = True
                    local_t = max(0.0, duration - 1e-4)
                else:
                    self._hold_boundary_latch.pop(hold_key, None)
            source_duration = self._clip_source_duration(clip, duration)
            source_t, _sequential_pause = self._clip_sequential_source_time(
                clip, local_t, source_duration)
            source_t, run_active = self._clip_run_mode_source_time(
                clip, source_t, source_duration
            )
            if not run_active and hold_mode == "none":
                continue
            if hold_extension:
                source_t = max(0.0, source_duration - 1e-4)
            elif hold_mode == "last_cycle":
                source_t = self._clip_auto_hold_cycle_time(clip, source_t)
            else:
                source_t = min(source_t, max(0.0, source_duration - 1e-4))
            if hold_extension:
                gain = 1.0
            elif hold_mode == "none":
                # 机制B续(2026-08-11): 延长窗口 [duration, fade_in] 内 _clip_fade_gain
                # 的 dur cap 使 gain 在 t=duration 封顶=1 → 值=末帧恒值(帧冻结,
                # 用户实测: 卡死一段后续接上)。正确语义 = 淡入按配置 fade_in 全程
                # 继续: 整窗 [0, fade_in] 用未封顶增益, t=duration 处连续,
                # t=fade_in 达 1.0 后窗口正常结束。仅 fix B 同类 clip 生效,
                # 其余(含 hold 系 clip)逐字节不变。
                _fade_in_raw = max(0.0, float(getattr(clip, "fade_in", 0.0) or 0.0))
                if _fade_in_raw > duration + 1e-9:
                    gain = self._clip_transition_gain(
                        clip, min(1.0, max(0.0, local_t / _fade_in_raw)), fade_in=True)
                else:
                    gain = self._clip_fade_gain(clip, local_t)
            else:
                gain = self._clip_fade_gain(clip, local_t)
            out.append((float(_start_t), int(track_order), track, clip, local_t, source_t, hold_extension, gain, target_universes))

        hold_upper = bisect.bisect_right(hold_starts, float(now))
        if hold_upper > 0:
            covered: dict[int, np.ndarray] = {}
            selected = []
            # G-3: 非空 scope 列表一次算(any(scope) 与 hold 事件无关),
            # 空掩码用模块级共享默认 —— 免每 hold 项每域急切 np.zeros(512)。
            _hold_scopes = [
                (int(u), s) for u, s in hold_scope_by_universe.items() if (s).any()
            ]
        for hold_idx in range(hold_upper - 1, -1, -1):
            _hold_start, item = hold_events[hold_idx]
            _start_t, track_order, track, clip, duration, _hold_mode, target_universes = item
            if not _track_allowed(track):
                continue
            hold_mode = _hold_mode
            useful = False
            _bo = getattr(clip, "base_order_fixture_ids", None) or ()
            for universe in target_universes:
                # ★ 2026-08-09: hold 扫描掩码编译期预计算缓存(键含 clip 静态签名
                #   同口径 + track filter 签名; 随 _clip_render_mask_cache 失效体系
                #   invalidate_clip/invalidate_all/配接重导入 一并清空)。命中免逐帧
                #   base_active 与 track_mask 的两次 512 位运算 + 键构建。
                hold_key = (
                    self._clip_cache_id(clip),
                    "hold_scan",
                    int(universe),
                    len(getattr(clip, "frames", []) or []),
                    id(_bo),
                    len(_bo),
                    self._track_filter_signature_for_universe(track, int(universe)),
                )
                base_active = self._clip_render_mask_cache.get(hold_key)
                if base_active is None:
                    base_active = np.asarray(
                        self._clip_channel_active_mask(clip, int(universe)), dtype=bool)
                    track_mask = self._track_filter_mask_for_universe(track, int(universe))
                    if track_mask is not None:
                        base_active = base_active & np.asarray(track_mask, dtype=bool)
                    base_active = self._store_clip_render_mask(hold_key, base_active)
                base_any = self._clip_render_mask_any_cache.get(hold_key)
                if base_any is None:
                    base_any = bool((base_active).any())
                    self._clip_render_mask_any_cache[hold_key] = base_any
                if not base_any:
                    continue
                already = covered.get(int(universe))
                if already is None:
                    # G-3: 惰性分配 —— setdefault 默认参每命中急切建 512 bool。
                    already = np.zeros(512, dtype=bool)
                    covered[int(universe)] = already
                if (base_active & ~already).any():
                    useful = True
                    already |= base_active
            if not useful:
                continue
            hold_elapsed = max(0.0, float(now) - (float(_start_t) + float(duration)))
            source_duration = self._clip_source_duration(clip, duration)
            if hold_mode == "last_cycle":
                source_t = max(0.0, source_duration - 1e-4 + hold_elapsed * self._clip_playback_rate(clip))
                source_t = self._clip_auto_hold_cycle_time(clip, source_t)
            else:
                source_t = max(0.0, source_duration - 1e-4)
            local_t = float(duration)
            gain = 1.0
            selected.append((float(_start_t), int(track_order), track, clip, local_t, source_t, True, gain, target_universes))
            if covered and all(
                np.all(covered.get(u, _EMPTY_BOOL_MASK_512)[s])
                for u, s in _hold_scopes
            ):
                break
        if hold_upper > 0:
            out.extend(reversed(selected))
        return out

    def _clear_fade_in_anchor_cache_for_clip(self, clip_id: str):
        from .fade_anchor_invalidation import invalidate_fade_anchors
        invalidate_fade_anchors(self, clip_id)

    def _evict_oldest(self, cache: dict) -> None:
        """Drop the oldest entry from a bounded cache, race-safe.

        The background precompile daemon (_start_background_precompile) and the
        gen/render thread both run this write-then-evict path concurrently.
        ``d.pop(next(iter(d)))`` is a non-atomic read-modify-write: two threads
        can race between ``iter`` and ``next`` (RuntimeError: dictionary changed
        size during iteration) or pop the same first key (KeyError). Insertions
        elsewhere keep the cache bounded, so swallowing a lost eviction race is
        harmless — at worst one extra entry survives until the next insert.
        """
        try:
            cache.pop(next(iter(cache)))
        except (KeyError, StopIteration, RuntimeError):
            pass

    def _store_clip_render_mask(self, key: tuple, mask: np.ndarray) -> np.ndarray:
        """Store a read-only clip render mask plus its any() flag, bounded."""
        _stored = np.asarray(mask, dtype=bool).copy()
        try:
            _stored.setflags(write=False)
        except Exception:
            pass
        self._clip_render_mask_cache[key] = _stored
        self._clip_render_mask_any_cache[key] = bool((_stored).any())
        if len(self._clip_render_mask_cache) > 4096:
            try:
                evicted = next(iter(self._clip_render_mask_cache))
                self._evict_oldest(self._clip_render_mask_cache)
                self._clip_render_mask_any_cache.pop(evicted, None)
            except (KeyError, StopIteration, RuntimeError):
                pass
        return _stored

    def _make_span_index_map(self, spans) -> Optional[np.ndarray]:
        """Map each 0..511 channel to its fixture index (or -1 when unmapped)."""
        if not spans:
            return None
        arr = np.full(512, -1, dtype=np.int32)
        for i, (start, end) in enumerate(spans):
            if 0 <= start < end <= 512:
                arr[start:end] = i
        return arr

    def precompile_clip(self, clip: Clip, track=None, proj=None) -> None:
        """Warm static playback data for a clip after recording or setting changes."""
        if clip is None:
            return
        if proj is None:
            proj = _project_of(self)
        self.freeze_clip_spatial_snapshot(clip, proj=proj)
        try:
            track_universes = [
                int(u) for u in (getattr(track, "universes", None) or [getattr(track, "universe", 1)])
            ]
        except Exception:
            track_universes = [1]
        frame_universes = self._clip_frame_universes(clip)
        clip_universes = getattr(clip, "clip_universes", None) or track_universes
        # 并集覆盖渲染路径实际请求的域: clip 声明域 + 帧域(录制帧可携带轨道声明
        # 之外的域, 如 uni 26-28) + 轨道默认域。旧 or 链在 clip 声明域非空时跳过
        # 帧域, 帧专有域的 clip 预编译永远漏掉 → 首接触冷构建。
        target_universes = sorted(set(
            int(u) for u in (
                list(clip_universes)
                + list(frame_universes)
                + track_universes
            )
        ))
        fixture_defs_by_id = {
            str(getattr(fd, "id", "")): fd
            for fd in (getattr(proj, "fixture_defs", []) or [])
            if getattr(fd, "id", None)
        } if proj is not None else {}
        compiled_by_universe: dict[int, dict] = {}
        for universe in target_universes:
            try:
                compiled_by_universe[int(universe)] = self._compiled_clip_universe(
                    clip,
                    int(universe),
                    track=track,
                    proj=proj,
                    fixture_defs_by_id=fixture_defs_by_id,
                )
            except Exception:
                log.debug("clip precompile failed", exc_info=True)
        for universe, compiled in compiled_by_universe.items():
            try:
                active = np.asarray(
                    compiled.get("base_active", self._clip_channel_active_mask(clip, int(universe))),
                    dtype=bool,
                )
                if (active).any():
                    self._get_clip_anchor(clip, int(universe), active)
            except Exception:
                log.debug("clip anchor precompile failed", exc_info=True)
        try:
            if (
                max(0.0, float(getattr(clip, "fade_in", 0.0) or 0.0)) > 1e-4
                or self._get_clip_fade_in_direction(clip) != "none"
            ):
                for universe in target_universes:
                    _prewarm_transition_anchor(self, clip, int(universe), track=track, proj=proj)
        except Exception:
            log.debug("clip fade-in anchor precompile failed", exc_info=True)
        try:
            enabled, groups, _pauses, _single, _batch_count, _step = self._clip_sequential_timing_detail(clip)
            direction = self._get_clip_fade_in_direction(clip)
            if enabled and direction != "none":
                source_duration = self._clip_source_duration(
                    clip,
                    max(0.0, float(getattr(clip, "duration", 0.0) or 0.0)),
                )
                for universe, compiled in compiled_by_universe.items():
                    raw = self._compiled_clip_frame_data(compiled, 0.0)
                    if raw is None:
                        raw = self._get_array_u8_512()
                    self._clip_sequential_remapped_source_frame(
                        clip,
                        0.0,
                        0.0,
                        source_duration,
                        int(universe),
                        raw,
                        track=track,
                        fixture_defs_by_id=fixture_defs_by_id,
                        proj=proj,
                        compiled=compiled,
                        compiled_by_universe=compiled_by_universe,
                    )
                    sorted_fixtures = list(compiled.get("sorted_fixtures", []) or [])
                    if sorted_fixtures:
                        self._sequential_fixture_group_data(
                            clip,
                            sorted_fixtures,
                            direction,
                            groups,
                            track=track,
                            proj=proj,
                        )
        except Exception:
            log.debug("clip sequential precompile failed", exc_info=True)

    def precompile_project_clips(self, proj=None, progress_callback: Optional[Callable] = None) -> None:
        """Warm playback caches for all clips in the current project."""
        if proj is None:
            proj = _project_of(self)
        signature = self._cached_project_clip_layout_signature(list(getattr(proj, "tracks", []) or []) if proj is not None else [])
        if (
            not bool(getattr(self, "_precompile_dirty", True))
            and getattr(self, "_precompile_signature", None) == signature
        ):
            return
        clip_items = []
        for track in (getattr(proj, "tracks", []) or []) if proj is not None else []:
            if bool(getattr(track, "is_folder", False)):
                continue
            for clip in (getattr(track, "clips", []) or []):
                clip_items.append((track, clip))
        total = len(clip_items)
        from .timeline_window_precompile import precompile_background_project_clip
        for index, (track, clip) in enumerate(clip_items, start=1):
            if self._precompile_cancel:
                # Cancelled (e.g. another program was loaded). Leave caches
                # partially warmed; the lazy render path fills any misses.
                return
            precompile_background_project_clip(self, clip, track=track, proj=proj)
            if progress_callback is not None:
                _report_precompile_progress(progress_callback, (index, total, clip))
        self._precompile_signature = signature
        self._precompile_dirty = False

    def _start_background_precompile(self, proj=None) -> None:
        """Warm all per-clip caches on a daemon thread so the first playthrough
        is smooth without freezing the UI.

        Thread safety: precompile writes to plain Python dicts (each op is
        GIL-atomic). The render path reads these caches lazily and falls back to
        the existing lazy-fill path on any miss, so a frame that the background
        thread has not yet reached simply computes on demand — no corruption.

        Gated behind QASCADE_BG_PRECOMPILE (default ON). Any prior run is
        cancelled first so loading a new program never stacks threads.
        """
        if str(os.environ.get("QASCADE_BG_PRECOMPILE", "1")).strip().lower() in ("0", "false", "off", "no"):
            return
        # Cancel and join any in-flight precompile before starting a new one.
        prev = self._precompile_thread
        if prev is not None and prev.is_alive():
            self._precompile_cancel = True
            prev.join(timeout=2.0)
        self._precompile_cancel = False

        def _worker() -> None:
            try:
                self.precompile_project_clips(proj)
            except Exception:
                log.debug("background precompile failed", exc_info=True)

        thread = threading.Thread(target=_worker, name="qascade-precompile", daemon=True)
        self._precompile_thread = thread
        thread.start()

    def precompile_window_clips(
        self,
        pos: float,
        proj=None,
        lookahead: float = 1.0,
        backfill: float = 0.5,
        max_clips: int = 16,
    ) -> None:
        """Warm only clips near the current playhead.

        Full-project precompile can be very expensive on large show files. This
        keeps play-start preparation bounded while preserving the existing lazy
        render path for clips outside the window.
        """
        if proj is None:
            proj = _project_of(self)
        if proj is None:
            return
        try:
            pos = max(0.0, float(pos))
        except Exception:
            pos = 0.0
        lookahead = max(0.0, float(lookahead or 0.0))
        backfill = max(0.0, float(backfill or 0.0))
        max_clips = max(1, int(max_clips or 1))

        tracks = list(getattr(proj, "tracks", []) or [])
        if not tracks:
            return
        try:
            ordered_tracks, _track_by_id, _any_solo = self._render_track_state(tracks)
            self._playback_index_for_tracks(tracks, ordered_tracks)
        except Exception:
            ordered_tracks = tracks

        window_start = max(0.0, pos - backfill)
        window_end = pos + lookahead
        current_items: list[tuple] = []
        upcoming_items: list[tuple] = []
        recent_hold_items: list[tuple] = []

        for track_order, track in enumerate(ordered_tracks):
            if bool(getattr(track, "is_folder", False)):
                continue
            for clip in (getattr(track, "clips", []) or []):
                try:
                    start_t = float(getattr(clip, "start_time", 0.0) or 0.0)
                    duration = max(0.0, float(getattr(clip, "duration", 0.0) or 0.0))
                except Exception:
                    continue
                if duration <= 0.0:
                    continue
                end_t = start_t + duration
                if start_t <= pos < end_t:
                    current_items.append((0.0, start_t, int(track_order), track, clip))
                elif end_t >= window_start and start_t <= window_end:
                    distance = min(abs(start_t - pos), abs(end_t - pos))
                    upcoming_items.append((float(distance), start_t, int(track_order), track, clip))
                elif self._clip_hold_mode(clip) != "none" and end_t <= pos:
                    recent_hold_items.append((pos - end_t, start_t, int(track_order), track, clip))

        current_items.sort(key=lambda item: (item[1], item[2]))
        upcoming_items.sort(key=lambda item: (item[0], item[1], item[2]))
        recent_hold_items.sort(key=lambda item: (item[0], -item[1], item[2]))

        seen: set[str] = set()
        selected: list[tuple[object, object]] = []
        for items in (current_items, recent_hold_items[:8], upcoming_items):
            for _distance, _start_t, _track_order, track, clip in items:
                key = f"{self._clip_cache_id(clip)}::{getattr(track, 'id', id(track))}"
                if key in seen:
                    continue
                seen.add(key)
                selected.append((track, clip))
                if len(selected) >= max_clips:
                    break
            if len(selected) >= max_clips:
                break

        from .timeline_window_precompile import precompile_owned_window_clip
        for track, clip in selected:
            precompile_owned_window_clip(self, clip, track=track, proj=proj)

    def freeze_clip_spatial_snapshot(self, clip: Clip, proj=None) -> None:
        """Store the TopView positions used by directional playback on the clip itself."""
        if clip is None:
            return
        if proj is None:
            proj = _project_of(self)
        if proj is None:
            return
        ids = self._unique_fixture_id_list(getattr(clip, "base_order_fixture_ids", []) or [])
        if not ids:
            try:
                all_fixtures = _stable_project_fixtures(self, "_freeze_fixtures_cache", proj)
                fdefs = {
                    str(getattr(fd, "id", "")): fd
                    for fd in (getattr(proj, "fixture_defs", []) or [])
                    if getattr(fd, "id", None)
                }
                ids = self._clip_base_order_fixture_ids(
                    clip,
                    proj=proj,
                    all_fixtures=all_fixtures,
                    fixture_defs_by_id=fdefs,
                )
            except Exception:
                ids = []
        if not ids:
            return
        existing = getattr(clip, "base_fixture_positions", {}) or {}
        if isinstance(existing, dict):
            complete = True
            for fid in ids:
                raw = existing.get(str(fid))
                if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                    complete = False
                    break
                try:
                    float(raw[0]); float(raw[1])
                except Exception:
                    complete = False
                    break
            if complete:
                return
        positions = self._topview_fixture_positions_for_ids(ids, proj=proj, use_clip_snapshot=False)
        if not positions:
            return
        setattr(
            clip,
            "base_fixture_positions",
            {
                str(fid): [float(pos[0]), float(pos[1])]
                for fid, pos in positions.items()
                if str(fid)
            },
        )
        _record_private_spatial_snapshot(self, clip)

    def _store_clip_fade_geometry_cache(self, key: tuple, value: tuple):
        self._clip_fade_geometry_cache[key] = value
        if len(self._clip_fade_geometry_cache) > 8192:
            self._evict_oldest(self._clip_fade_geometry_cache)

    def _clip_static_signature(self, clip: Clip) -> tuple:
        clip_id = self._clip_cache_id(clip)
        cached = self._clip_static_signature_cache.get(clip_id)
        if cached is not None:
            return cached
        active_items = []
        try:
            active_map = getattr(clip, "active_channels_by_universe", {}) or {}
            for raw_u, channels in active_map.items():
                try:
                    uni = int(raw_u)
                except Exception:
                    uni = int(str(raw_u))
                active_items.append((uni, tuple(sorted(int(ch) for ch in (channels or [])))))
        except Exception:
            active_items = []
        try:
            clip_universes = tuple(sorted(int(u) for u in (getattr(clip, "clip_universes", []) or [])))
        except Exception:
            clip_universes = ()
        signature = (
            len(getattr(clip, "frames", []) or []),
            clip_universes,
            tuple(self._unique_fixture_id_list(getattr(clip, "base_order_fixture_ids", []) or [])),
            tuple(
                (str(fid), round(float(pos[0]), 3), round(float(pos[1]), 3))
                for fid, pos in sorted((getattr(clip, "base_fixture_positions", {}) or {}).items())
                if isinstance(pos, (list, tuple)) and len(pos) >= 2
            ),
            tuple(sorted(active_items)),
        )
        self._clip_static_signature_cache[clip_id] = signature
        if len(self._clip_static_signature_cache) > 8192:
            self._evict_oldest(self._clip_static_signature_cache)
        return signature

    def _clip_transition_content_signature(self, clip: Clip) -> tuple:
        """Frame-value signature used only while building transition anchors.

        缓存：clip 帧数据在播放期间不变，每帧重算（对所有帧做 numpy 点积/求和）是
        热路径上的纯浪费。按 clip_id 缓存，用帧数做快速校验；任何 clip 编辑都会经
        invalidate_clip_cache 清掉对应项，保证内容变化后必然重算。
        """
        clip_id = self._clip_cache_id(clip)
        frames = getattr(clip, "frames", []) or []
        n_frames = len(frames)
        cached = self._clip_transition_content_sig_cache.get(clip_id)
        if cached is not None and cached[0] == n_frames:
            return cached[1]
        if not frames:
            self._clip_transition_content_sig_cache[clip_id] = (0, ())
            return ()
        # 向量化: 整帧栈一次构造 + 一次 uint64 乘加, 替代逐帧 asarray/astype/dot/sum
        # 循环(旧实现每 clip ~5.7ms, 是边界 tick 90-120ms 尖峰主因之一)。
        # 语义等价: 旧代码对前 n=min(size,512) 个值用权重 1..n 做 uint32 dot;
        # 单帧加权和 ≤ 255*512*512/2 ≈ 33.5M < 2^32 无回绕, uint64 累加结果逐位一致;
        # 不足 512 的帧补零, 零×权重=0 不影响结果。
        cols = []
        for frame in frames:
            raw = np.asarray(
                getattr(frame, "data", self._get_array_u8_512()),
                dtype=np.uint8,
            ).reshape(-1)
            if raw.size >= 512:
                cols.append(raw[:512])
            else:
                padded = np.zeros(512, dtype=np.uint8)
                padded[: raw.size] = raw
                cols.append(padded)
        stack = np.stack(cols, axis=0)
        vals64 = stack.astype(np.uint64)
        totals = vals64.sum(axis=1)
        weighteds = (vals64 * np.arange(1, 513, dtype=np.uint64)).sum(axis=1)
        out = [
            (
                int(getattr(frame, "universe", 1) or 1),
                round(float(getattr(frame, "timestamp", 0.0) or 0.0), 6),
                int(totals[i]),
                int(weighteds[i]),
            )
            for i, frame in enumerate(frames)
        ]
        signature = tuple(out)
        self._clip_transition_content_sig_cache[clip_id] = (n_frames, signature)
        if len(self._clip_transition_content_sig_cache) > 8192:
            self._clip_transition_content_sig_cache.pop(
                next(iter(self._clip_transition_content_sig_cache)))
        return signature

    def _prewarm_transition_caches(self, proj, budget_s: float = 3.0) -> None:
        """加载完成时一次性预热过渡链冷计算, 摊薄边界 tick 尖峰。

        边界 tick(新候选 clip 首接触)一次性冷构建过渡签名/compiled universe/锚点,
        造成 90-120ms 尖峰。复用既有 precompile_clip 单一真源(编译/双锚点/顺序
        缓存全覆盖), 本方法只做预算驱动: 限总预算逐个预热, 命中后边界 tick 的
        构建开销归零。预算内未完成的 clip 由 500ms 后台守护 precompile(既有机制)
        继续; 录制期新增 clip 由 main_window 直接 precompile_clip(既有调用)。
        渲染 tick 内不做预热 —— 单个重 clip 一次预热可 80ms+, 在 tick 内会反向
        制造尖峰, 与目的相悖。
        """
        # 加载期间 tracks 列表曾被重排(轨道定序/空轨前置), 而重排不走
        # invalidate 流程(dirty 恒 False) → _cached_project_clip_layout_signature
        # 的 120ms 节流快路径可能返回旧轨序签名。预热 ctx 若带上旧签名,
        # 渲染期节流窗口一过重建真实签名 → ctx 不匹配 → 边界锚点全量重建
        # (实测 49 对/次)。预热前强制置 dirty, 让缓存与 ctx 都与定序后
        # tracks 对齐, 一次 O(clip) 校验代价在加载期可忽略。
        self._clip_layout_signature_dirty = True
        t0 = time.perf_counter()
        for track in getattr(proj, "tracks", []) or []:
            if bool(getattr(track, "is_folder", False)):
                continue
            for clip in getattr(track, "clips", []) or []:
                # 预热纯 advisory: 失败不升级 —— 缺陷 clip 的异常仍会在真实
                # 渲染路径按旧语义暴露(激活时)。
                try:
                    self.precompile_clip(clip, track=track, proj=proj)
                    # 补 precompile_clip 不覆盖的锚点: 无淡入设置的 clip 渲染路径
                    # 仍会请求 fade_in_anchor(跨轨接缝继承), 全量预热避免边界重建。
                    # 有淡入的 clip 此处为 ready 命中(µs 级), 无淡入的才实际构建。
                    track_universes = [
                        int(u) for u in (getattr(track, "universes", None) or [getattr(track, "universe", 1)])
                    ]
                    frame_universes = self._clip_frame_universes(clip)
                    for uni in set(
                        int(u)
                        for u in (
                            list(getattr(clip, "clip_universes", None) or [])
                            + list(frame_universes)
                            + track_universes
                        )
                    ):
                        _prewarm_transition_anchor(self, clip, int(uni), track=track, proj=proj)
                        # 顺序 clip 的源空间变异检查(边界 tick 14 个×1.4ms=20ms;
                        # 缓存键与渲染路径同源: compiled["spans"]), 无 spans 零开销。
                        try:
                            compiled = self._compiled_clip_universe(clip, int(uni), track=track, proj=proj)
                            spans = list(compiled.get("spans", []) or []) if compiled else []
                            if spans:
                                self._clip_source_has_spatial_variation(clip, int(uni), spans)
                        except Exception:
                            log.debug("clip spatial variation prewarm failed", exc_info=True)
                except Exception as e:
                    log.debug("prewarm skip clip %s: %s", self._clip_cache_id(clip), e)
                if time.perf_counter() - t0 > budget_s:
                    return

    @staticmethod
    def _fixture_list_signature(fixtures: list) -> tuple:
        return tuple(
            (
                str(getattr(fi, "id", "") or ""),
                int(getattr(fi, "universe", 1) or 1),
                int(getattr(fi, "start_channel", 1) or 1),
                str(getattr(fi, "def_id", "") or ""),
            )
            for fi in (fixtures or [])
        )

    @staticmethod
    def _fixture_defs_signature(fixture_defs_by_id: Optional[dict]) -> tuple:
        out = []
        for fid, fd in (fixture_defs_by_id or {}).items():
            channels = getattr(fd, "channels", []) or []
            out.append((
                str(fid),
                int(getattr(fd, "channel_count", 0) or 0),
                tuple(str(ch) for ch in channels),
            ))
        return tuple(sorted(out))

    def _track_filter_signature_for_universe(self, track, universe: int) -> tuple:
        rule = self._track_filter_rule_for_universe(track, int(universe))
        if rule is None:
            return ("all",)
        try:
            if rule.is_empty() or rule.allows_all():
                return ("all",)
        except Exception:
            pass
        try:
            return tuple(sorted(int(ch) for ch in (getattr(rule, "channels", []) or [])))
        except Exception:
            return ("custom", id(rule))

    def _topview_shape_signature_for_ids(self, ids: list[str], proj=None) -> tuple:
        shapes = self._topview_circle_shapes_for_ids(ids, proj=proj)
        return tuple(
            (
                str(shape.get("id", "")),
                str(shape.get("type", "")),
                round(float(shape.get("cx", 0.0)), 3),
                round(float(shape.get("cy", 0.0)), 3),
                round(float(shape.get("rx", 0.0)), 3),
                round(float(shape.get("ry", 0.0)), 3),
                round(float(shape.get("angle", 0.0)), 3),
            )
            for shape in shapes
        )

    def _prepare_clip_frame_for_output_basis(self, clip: Clip, universe: int, data: np.ndarray, proj) -> np.ndarray:
        """Convert frozen clip DMX values to the current virtual output basis.

        Clip frames store the physical/output values that were recorded.  The
        NetworkManager always applies the current XY reference at final output,
        so playback writes the inverse-transformed values to the clip layer.
        This keeps already recorded clips unchanged when TopView's XY reference
        is changed later.
        """
        current_refs = getattr(self._nm, "_xy_reference_by_universe", {}) if self._nm is not None else {}
        return apply_xy_reference_to_virtual(data, current_refs, int(universe))

    def _prepare_compiled_clip_frame_for_output_basis(
        self,
        clip: Clip,
        universe: int,
        compiled: dict,
        frame_index: int,
        data: np.ndarray,
    ) -> np.ndarray:
        """回放冻结（2026-08-11 用户指令）：clip 帧存的是录制时的「当前输出
        值」（物理值），回放原样写入 clip 层——不再针对同步模式做逆变换判定
        （旧实现按当前 refs 逆变换 + 输出再 forward，录制/回放角度不一致时
        回放偏离录制轨迹 = 用户实测「同步模式录制回放=非同步轨迹」）。输出
        侧由 NetworkManager._apply_xy_reference 的 clip_freeze 冻结跳过
        forward，已录物理值直出。调用方全只读，直接返回原数组。
        """
        return data

    def render_at(self, pos: float):
        """旧兼容入口：默认作为拖针预览渲染到 clip_preview 层。"""
        self.render_preview_at(pos)

    def render_playback_at(self, pos: float):
        """点击播放态：渲染到 clip_playback 层，最终覆盖编辑器输出。

        ★ LTP：此入口由 TimelineController.play() 触发（点击播放按键），
        算作一次 clip 接管事件，会在本次 _render_position 中通知 NetworkManager
        更新 _ltp_clip_time。播放过程中 _tick 直接调用 _render_position 而不经过
        此方法，因此不会每帧刷新接管时间戳。
        ★★ A10 P0：接管掩码补全节目级范围（模块级 `_program_takeover_scope`），
        打点块内与 outputs 按域取并集——修"空档起步 → 后续 clip 被编辑器
        时间戳永久压制"。
        """
        self._ltp_takeover_pending = True
        self._ltp_takeover_layer = "clip_playback"
        self._ltp_takeover_scope = _program_takeover_scope(self)
        self._render_position(float(pos), layer_name="clip_playback")

    def render_preview_at(self, pos: float):
        """拖动指针预览态：渲染到 clip_preview 层，不进入播放态、不清空编辑器数据。

        ★ LTP：每次拖针落点算一次 clip 预览接管事件，更新 _ltp_clip_time。
        连续拖动过程中每次 seek 都会触发此方法，每次都记录一次接管时间。
        """
        self._ltp_takeover_pending = True
        self._ltp_takeover_layer = "clip_preview"
        self._render_position(float(pos), layer_name="clip_preview")

    def play(self, from_position: float = None):
        if from_position is not None:
            self._position = from_position
        self._real_start = time.monotonic() - self._position  # ★ R193/P2-03
        self._state = PlaybackState.PLAYING
        self._timer.start(25)
        self.state_changed.emit(self._state)
        self.playback_started.emit()

    def pause(self):
        self._timer.stop()
        self._state = PlaybackState.PAUSED
        self.state_changed.emit(self._state)
        self.playback_stopped.emit()
        # 暂停/拖针预听后必须保留 timeline 层作为已录动作基底，不能清空，
        # 否则 Programmer/Matrix/Effect 等编辑页会在连续编曲时出现断层。

    def stop(self):
        self._timer.stop()
        self._position = 0.0
        self._state = PlaybackState.IDLE
        self.state_changed.emit(self._state)
        self.position_changed.emit(0.0)
        self.playback_stopped.emit()
        # 停止只清理 clip 播放/预览/旧兼容层，不清空 Programmer/Matrix/Effect 等编辑器层数据。
        # 同时清理 linker 层：防止播放停止后关联器规则残留输出污染节目层
        if self._nm is not None and hasattr(self._nm, "clear_layer"):
            layer_map = getattr(self, "_last_output_universes_by_layer", {}) or {}
            for clip_layer in ("clip_playback", "clip_preview", "timeline", "linker"):
                known = set(layer_map.get(clip_layer, set())) | set(getattr(self, "_last_output_universes", set()))
                if known:
                    for u in known:
                        try:
                            self._nm.clear_layer(clip_layer, int(u))
                        except TypeError:
                            try:
                                self._nm.clear_layer(clip_layer)
                            except Exception:
                                pass
                        except Exception:
                            pass
                else:
                    try:
                        self._nm.clear_layer(clip_layer)
                    except Exception:
                        pass
                layer_map[clip_layer] = set()
            self._last_output_universes_by_layer = layer_map
        self._last_output_universes = set()
        self._last_record_source_outputs_by_universe = {}
        # R14: 停止清空保持消费状态(报告 4.1/风险6 —— stop 不清 _hold_boundary_latch,
        # 但消费状态若跨播放会话残留会压制新会话的 hold 输出)。
        reset_playback_consumption(self, clear_boundary=False)
        # 重置泵预充电调度器状态
        if hasattr(self, "_precharge_scheduler"):
            self._precharge_scheduler.reset()

    def seek(self, position: float):
        """跳转播放位置。★ 2026-09-19：跳转 = 新的渲染语境 → 必须清"消费"状态。

        用户实报："播放了一段带hold的clip，放完一次从头再放一次（拖指针回开头
        再播），则很多clip都无效了…必须要点击全清空才能正常预览。"
        根因：`seek()` 只改 `_position`，**不清** `_hold_consumed_masks` /
        `_takeover_consumed_masks`。拖指针回开头时，上一遍扫过整条时间线累积的
        "该通道已被消费/被接管" 仍然生效 → 对应 clip 在这些通道上被永久扣除 →
        整段无输出；点「全清空」会 `invalidate_all_caches()`（清这两张表）才恢复。
        消费语义是"本遍播放内的接管记账"，跨跳转必须重置（与 stop 同口径）。
        """
        self._position = max(0.0, position)
        self._real_start = time.monotonic() - self._position  # ★ R193/P2-03
        try:
            reset_playback_consumption(self)
        except Exception:
            pass
        self.position_changed.emit(self._position)

    def _clip_hold_mode(self, clip: Clip) -> str:
        mode = str(getattr(clip, "hold_mode", "") or "").strip().lower()
        if mode not in ("none", "last_frame", "last_cycle"):
            mode = "last_cycle" if bool(getattr(clip, "hold_last_frame", False)) else "none"
        return mode

    def _hold_consumed_active_for_item(
        self, hold_key: str, universe: int, active: np.ndarray
    ) -> np.ndarray:
        """R14: 真实 hold 项扣除已消费通道(一次性消费语义)。

        消费状态为空时原样返回(零分配, 零影响); 有消费时返回新数组
        (active & ~consumed), 不原地修改 —— memo 命中路径共享 memo 的
        active 数组, 原地改会污染 memo。
        """
        if not self._hold_consumed_masks:
            return active
        _cm = self._hold_consumed_masks.get(hold_key)
        if not _cm:
            return active
        _consumed = _cm.get(int(universe))
        if _consumed is None or not mask_count(_consumed):
            return active
        return mask_without(active, _consumed)

    def _takeover_consumed_active_for_item(
        self, clip_key: str, universe: int, active: np.ndarray
    ) -> np.ndarray:
        """2026-08-22: 同级/非父子覆盖消费 —— 扣除已被不可恢复接管吃掉的通道。"""
        if not self._takeover_consumed_masks:
            return active
        _cm = self._takeover_consumed_masks.get(clip_key)
        if not _cm:
            return active
        _consumed = _cm.get(int(universe))
        if _consumed is None or not mask_count(_consumed):
            return active
        return mask_without(active, _consumed)

    def _mark_takeover_consumed(self, clip_key: str, universe: int, mask: np.ndarray) -> None:
        """记录某 clip 在某 universe 被不可恢复接管吃掉的通道。"""
        mask = np.asarray(mask, dtype=bool)
        active_count = mask_count(mask)
        if not active_count:
            return
        _cm = self._takeover_consumed_masks.setdefault(clip_key, {})
        prev = _cm.get(int(universe))
        merged, added = (mask.copy(), active_count) if prev is None else merge_consumed(prev, mask)
        if not added:
            return
        _cm[int(universe)] = merged
        # R203: a newly surrendered channel invalidates both candidate and
        # universe memos; otherwise a static frame can retain its old owner.
        self._dirty_render_item_memo.clear()
        self._dirty_render_uni_memo.clear()

    @default_restoration_rule
    def _is_restorable_takeover(self, loser_chain: tuple, winner_chain: tuple) -> bool:
        """接管方结束后，被压的一方是否**恢复** —— ★ 2026-09-19 用户定案。

        用户原话::

            "同级是不恢复，遵循严格的后发接管。有高低层级的话，例如高层级被低层
             覆盖，低结束回到高，有恢复。同不同级别只影响恢复与否，不影响当时
             是否被后发接管"

        判据（**只看层级是否相同，不分深浅**）：
          · 同层级（chain 相同）→ **不恢复**（后发接管就是最终结果）；
          · 跨层级（一方在文件夹里、另一方不在；或互为祖孙）→ **恢复**
            （接管方离开后，被压的一方重新占回）。
        注意"接管本身"永远发生（见 `_ClipSortKey`：严格后发），本判据只管恢复。
        """
        return restorable_takeover(loser_chain, winner_chain)

    def _hold_consumed_active_in_items(self, active_clip_items) -> bool:
        """R14/M2 帧级门判定: 活跃项中是否存在「真实 hold 项且其消费掩码非空」。

        真实 hold 判定与 _dr_is_real_hold 同式(hold_extension 且
        local_t >= duration-1e-9; 桶 seam 项 local_t 钉在 duration-1e-4
        天然排除)。消费掩码为空时 O(1) 短路 —— 从未消费的帧恒 False,
        路径与改动前逐字节相同(零影响铁律)。CE 简路径门①与 K2 锁定帧
        缓存 serve 门共用本谓词(M2 帧级门, 替代会话级整帧禁用)。
        """
        if not self._hold_consumed_masks:
            return False
        for _start_t, _track_order, _track, clip, _lt, _st, _hold, _g, _unis in active_clip_items:
            if not _hold:
                continue
            duration_c = max(0.0, float(getattr(clip, "duration", 0.0)))
            if float(_lt) >= duration_c - 1e-9:
                _cm = self._hold_consumed_masks.get(self._clip_cache_id(clip))
                if _cm and any(bool((_m).any()) for _m in _cm.values()):
                    return True
        return False

    def _hold_consumed_active_this_frame(
        self, now: float
    ) -> Tuple[bool, Optional[list]]:
        """R14/M2: K2 锁定帧缓存 serve 门帧级判定(2026-08-13)。

        仅当消费掩码非空时做活跃项扫描(消费为空 → O(1) (False, None),
        零开销); 扫描与实时渲染路径同源(_indexed_active_clip_items + 同
        timer 区间推导), 判定复用 _hold_consumed_active_in_items。返回
        (vetoed, items):
          - vetoed=True: items=已构建活跃项, 且 _hold_boundary_latch 已推进
            (不恢复) —— 主渲染循环直接复用 items 免二次索引构造, 闩状态与
            「主循环自行构建」逐字节一致(扫描自 L0 推进至 L1, 主循环同)。
          - vetoed=False: items=None, 闩恢复快照 —— 缓存命中路径与「本门
            从未扫描」逐字节一致(真实 hold 项恒来自扫描路径, 与闩无关)。
        """
        if not self._hold_consumed_masks:
            return (False, None)
        proj = _project_of(self)
        tracks = list(getattr(proj, "tracks", []) or []) if proj is not None else []
        if not tracks:
            return (False, None)
        _struct = self._render_struct_for_tracks(tracks)
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
        _latch_before = dict(self._hold_boundary_latch)
        _vetoed = False
        try:
            _items = self._indexed_active_clip_items(
                float(now), tracks, _struct["ordered_tracks"],
                _struct["track_by_id"], _struct["any_solo"], _timer_interval_s)
            _vetoed = self._hold_consumed_active_in_items(_items)
            return (_vetoed, _items if _vetoed else None)
        finally:
            if not _vetoed:
                self._hold_boundary_latch.clear()
                self._hold_boundary_latch.update(_latch_before)

    def _clip_playback_rate(self, clip: Clip) -> float:
        try:
            return max(0.001, float(getattr(clip, "playback_rate", 1.0) or 1.0))
        except Exception:
            return 1.0

    def _clip_motor_force_profile(
        self, clip: Clip
    ) -> Optional[tuple[float, float, float]]:
        clip_id = self._clip_cache_id(clip)
        if clip_id in self._clip_motor_force_profile_cache:
            return self._clip_motor_force_profile_cache[clip_id]
        preset = str(getattr(clip, "motor_force_preset", "") or "").strip().lower()
        profile = None
        if preset in {"gentle", "soft", "natural", "strong", "blast", "custom"}:
            try:
                parts = (
                    max(1e-6, float(getattr(clip, "motor_force_accel", 0.2) or 0.0)),
                    max(1e-6, float(getattr(clip, "motor_force_cruise", 0.6) or 0.0)),
                    max(1e-6, float(getattr(clip, "motor_force_decel", 0.2) or 0.0)),
                )
                total = sum(parts)
                profile = tuple(part / total for part in parts)
            except (TypeError, ValueError):
                profile = None
        self._clip_motor_force_profile_cache[clip_id] = profile
        return profile

    @staticmethod
    def _trapezoid_position(
        progress: float, accel: float, cruise: float, decel: float
    ) -> float:
        progress = min(max(float(progress), 0.0), 1.0)
        peak_speed = 1.0 / (float(cruise) + 0.5 * (float(accel) + float(decel)))
        if progress < accel:
            return 0.5 * peak_speed * progress * progress / accel
        accel_distance = 0.5 * peak_speed * accel
        if progress < accel + cruise:
            return accel_distance + peak_speed * (progress - accel)
        elapsed = min(max(progress - accel - cruise, 0.0), decel)
        return accel_distance + peak_speed * cruise + peak_speed * (
            elapsed - 0.5 * elapsed * elapsed / decel
        )

    def _clip_motor_force_source_time(
        self, clip: Clip, source_t: float, source_duration: float
    ) -> float:
        profile = self._clip_motor_force_profile(clip)
        source_duration = max(0.0, float(source_duration))
        if profile is None or source_duration <= 1e-6:
            return max(0.0, float(source_t))
        progress = min(max(float(source_t) / source_duration, 0.0), 1.0)
        shaped = self._trapezoid_position(progress, *profile) * source_duration
        return min(max(0.0, shaped), max(0.0, source_duration - 1e-4))

    def _clip_motor_phase_amount(self, clip: Clip) -> float:
        manual = 0.0
        if bool(getattr(clip, "motor_transition_phase_enabled", False)):
            manual = max(0.0, float(getattr(clip, "motor_transition_phase_amount", 1.0) or 0.0))
        force = 0.0
        if self._clip_motor_force_profile(clip) is not None:
            force = max(0.0, float(getattr(clip, "motor_force_phase_amount", 0.0) or 0.0))
        return min(10.0, manual + force)

    def _clip_source_duration(self, clip: Clip, timeline_duration: float) -> float:
        try:
            source_duration = max(0.0, float(getattr(clip, "source_duration", 0.0) or 0.0))
        except Exception:
            source_duration = 0.0
        if source_duration > 0.0:
            return source_duration
        return max(0.0, float(timeline_duration) * self._clip_playback_rate(clip))

    @staticmethod
    def _clip_run_mode(clip: Clip) -> str:
        mode = str(getattr(clip, "run_mode", "normal") or "normal").strip().lower()
        return mode if mode in CLIP_RUN_MODES else "normal"

    def _clip_recorded_source_span(self, clip: Clip, source_duration: float) -> float:
        clip_id = self._clip_cache_id(clip)
        cached = self._clip_recorded_source_span_cache.get(clip_id)
        if cached is None:
            recorded = 0.0
            for frame in getattr(clip, "frames", []) or []:
                try:
                    timestamp = float(getattr(frame, "timestamp", 0.0) or 0.0)
                    recorded = max(recorded, max(0.0, timestamp))
                except (TypeError, ValueError):
                    continue
            self._clip_recorded_source_span_cache[clip_id] = recorded
            cached = recorded
        return min(max(0.0, cached), max(0.0, float(source_duration)))

    def _clip_run_mode_source_time(
        self, clip: Clip, source_t: float, source_duration: float
    ) -> tuple[float, bool]:
        mode = self._clip_run_mode(clip)
        source_t = max(0.0, float(source_t))
        source_duration = max(0.0, float(source_duration))
        end_t = max(0.0, source_duration - 1e-4)
        if mode == "normal" or source_duration <= 1e-6:
            return _normal_mode_time(self, clip, source_t, mode, end_t), True
        if mode in ("reverse", "pingpong"):
            return _back_and_forth_time(self, clip, source_t, source_duration, mode), True
        if mode == "loop":
            return source_t % source_duration, True
        recorded_span = self._clip_recorded_source_span(clip, source_duration)
        active = recorded_span <= 1e-6 or source_t <= recorded_span + 1e-9
        return min(source_t, min(end_t, recorded_span or end_t)), active

    def _clip_sequential_batch_count(self, clip: Clip, groups: Optional[int] = None) -> int:
        if groups is None:
            groups = max(1, int(getattr(clip, "fade_groups", 1) or 1))
        groups = max(1, int(groups or 1))
        try:
            raw_batches = int(getattr(clip, "fade_group_sequence_batches", 0) or 0)
        except Exception:
            raw_batches = 0
        if raw_batches <= 0:
            return groups
        return max(1, min(groups, raw_batches))

    def _clip_sequential_pauses(
        self,
        clip: Clip,
        groups: Optional[int] = None,
        fallback: Optional[float] = None,
    ) -> list[float]:
        if groups is None:
            groups = max(1, int(getattr(clip, "fade_groups", 1) or 1))
        if fallback is None:
            fallback = max(0.0, float(getattr(clip, "fade_group_pause", 0.0) or 0.0))
        batch_count = self._clip_sequential_batch_count(clip, groups)
        return normalize_group_pauses(
            getattr(clip, "fade_group_pauses", []),
            max(0, batch_count - 1),
            max(0.0, float(fallback or 0.0)),
        )

    def _clip_sequential_offsets(
        self,
        step: float,
        pauses: list[float],
        batch_count: Optional[int] = None,
    ) -> list[float]:
        """各批次（波次）的**起点时刻**。

        ★★ R152（2026-09-22）：第一个参数从"单组窗口 `single`"改为**起点间隔 `step`**。
        旧实现把两个翼先相加成 `total` 再均分给每个批次（`cursor += single`），
        于是 `延迟3+过渡1` 与 `延迟1+过渡3` 算出**完全相同**的起点序列（实测每台到位
        时刻都是 `[0.92,1.92,2.92,3.92]`）—— 这就是用户"拖哪个翼都没反应"的根因。
        现在起点间隔只由**延迟翼**决定（`step = 延迟翼/(批次数−1)`），与单台的
        **过渡翼**窗口解耦；`step == single` 时与旧公式逐字节相同（旧工程零回归）。
        """
        step = max(0.0, float(step or 0.0))
        pauses = [max(0.0, float(p or 0.0)) for p in (pauses or [])]
        if batch_count is None:
            batch_count = max(1, len(pauses) + 1)
        batch_count = max(1, int(batch_count or 1))
        offsets: list[float] = []
        cursor = 0.0
        for batch in range(batch_count):
            offsets.append(cursor)
            cursor += step
            if batch < batch_count - 1:
                cursor += pauses[batch] if batch < len(pauses) else 0.0
        return offsets

    def _clip_sequential_active_batch(
        self,
        local_t: float,
        single: float,
        pauses: list[float],
        batch_count: int,
        *,
        step: Optional[float] = None,
    ) -> tuple[int, float, bool]:
        """当前活跃批次 + 批内时间 + 是否处于批间停顿（R152）。

        批次**窗口** = `single`（过渡翼 = 每组自己的过渡时长）；批次**起点**由 `step`
        （延迟翼铺开的错峰间隔）决定。`step is None` → 退 `step = single`（旧工程不变）。
        两翼下 `step` 可能 < `single`（延迟翼短、过渡翼长）→ 相邻窗口**重叠**，
        故按"第一个窗口未结束的批次"判定，语义仍正确。
        """
        single = max(0.0, float(single or 0.0))
        step = single if step is None else step
        batch_count = max(1, int(batch_count or 1))
        local_t = max(0.0, float(local_t or 0.0))
        offsets = self._clip_sequential_offsets(step, pauses, batch_count)
        for batch, start in enumerate(offsets):
            if local_t <= start + single + 1e-9:
                return batch, min(max(0.0, local_t - start), single), False
            if batch < batch_count - 1 and local_t < offsets[batch + 1] - 1e-9:
                return batch, single, True
        return batch_count - 1, single, False

    def _clip_sequential_span(self, clip: Clip, groups: int, pause: float, single: float,
                              *, step: Optional[float] = None) -> float:
        """扫掠总跨度 = **最后一组起点 + 它自己的窗口**（R152 计划第 2 步）。

        `step`（起点间隔）缺省时向引擎取当前 clip 的错峰步长；未启用扫掠/无延迟翼时
        退回 `step = single` → `single×(批次−1) + single + 停顿 = single×批次 + 停顿`，
        与旧公式**逐字节相同**（旧工程零回归）。
        """
        batches = self._clip_sequential_batch_count(clip, groups)
        pauses = self._clip_sequential_pauses(clip, groups, pause)
        if step is None:
            step = self._clip_sequential_timing_detail(clip)[5]
            if step is None:
                step = single
        return max(0.0, float(step) * max(0, batches - 1)
                   + float(single) + float(sum(pauses)))

    def _clip_sequential_timing_detail(self, clip: Clip) -> tuple:
        """**权威 6 元组**：`(enabled, groups, pauses, single, batch_count, step)`。

        `single` = **过渡翼**（每组自己的过渡时长）；
        `step`   = **延迟翼错峰步长**（组起点间隔）；`None` = 未启用扫掠。

        ★★ R152（2026-09-22）—— 前 10 轮修不好的根因就藏在这个返回值的形状里：
        旧版返回**合并后的单一标量**（`total = 两翼相加` → `single = total/批次数`），
        两翼分工在那行被永久丢弃（可证伪判据：`延迟3+过渡1` 与 `延迟1+过渡3` 的每台
        到位时刻**完全相同**）。拆开后错峰间隔只由延迟翼定、单台时长只由过渡翼定。
        详见 `_sequential_single_window`。

        ★ 闸门（R149 定案）：**条带即权威、不看 `mode`**（真工程实测 9/10 条带 clip 的
        `mode` 都是 `parallel`，旧闸门要求 sequential → 它们拖的条带被整个忽略）。
        """
        _ck = self._clip_cache_id(clip)
        _cached = self._clip_seq_timing_cache.get(_ck)
        if _cached is not None and len(_cached) >= 6:
            en, gr, pz, sg, bc, st = _cached[:6]
            return en, gr, list(pz), sg, bc, st
        groups, single, seq_in, fade_in_c, dir_set, batch_count, pauses = \
            _sequential_gate_inputs(self, clip)
        # ★★★ R159 恢复 mode 闸门（用户 2026-09-23）::
        #
        #     "ee9al这个比如根本没有顺序分组，怎么还搞成依次两个组执行了。
        #      我认为是你改了算法之后这匹配错了"
        #     "目标是第一个节目最终表现跟前几天代码一样"
        #
        #   R149~R158 我把这个闸门改成了"只要拖了条带就按条带跑（不看 mode）"，
        #   依据是"真工程 9/10 条 mode=parallel 的条带被忽略"—— 那个测量**是错的**：
        #   mode=parallel 的 clip 走的是**并行路径**（`_compute_parallel_fixture_gains`），
        #   它本来就按用户语义处理两个翼：
        #       t_start = 设备在组内的位置 × 延迟翼     ← 延迟翼 = 错峰跨度
        #       win     = 过渡翼                        ← 过渡翼 = 每台的柔和时长
        #   我当年只量了**顺序路径**的跨度（parallel 恒为 0）就误判成"条带被忽略"，
        #   于是把 parallel 的 clip 强行塞进顺序扫掠 → 两个后果（都是用户实报）：
        #     · `ee9a7cc3`（parallel + 组=2）被做成"2 组依次执行"（用户："根本没有
        #       顺序分组，怎么还搞成依次两个组执行了"）；
        #     · 动态效果（`9cf436e5`）走顺序路径 → source_t 在批次边界**回卷** →
        #       抽动。
        #
        #   **mode 是"顺序 vs 并行"的唯一权威**，恢复 R148 的闸门语义：
        #     mode == "sequential" 才走顺序扫掠；parallel 走并行路径（各分组同时）。
        mode = str(getattr(clip, "fade_group_sequence_mode", "parallel")
                   or "parallel").strip().lower()
        #   "拖了条带"的判据（与 `_sequential_has_wing` 同源）：
        #     延迟翼 > 0、单组时长 > 0、或**过渡翼 > 0 且设了方向**（仅顺序模式）。
        wing_set = ((single > 1e-4) or (seq_in > 1e-4)
                    or (mode == "sequential" and fade_in_c > 1e-4 and dir_set))
        enabled = mode == "sequential" and groups > 1 and wing_set
        step: Optional[float] = None
        if enabled:
            single, pauses, step = _sequential_single_window(
                clip, single, pauses, batch_count, seq_in)
        self._clip_seq_timing_cache[_ck] = (enabled, groups, list(pauses), single, batch_count, step)
        return enabled, groups, pauses, single, batch_count, step

    def _clip_sequential_group_timing(self, clip: Clip) -> tuple[bool, int, float, float]:
        enabled, groups, pauses, single, _batch_count, _step = self._clip_sequential_timing_detail(clip)
        return enabled, groups, float(sum(pauses)), single

    def _clip_sequential_source_time(self, clip: Clip, local_t: float,
                                     source_duration: float) -> tuple[float, bool]:
        enabled, _groups, pauses, single, batch_count, step = self._clip_sequential_timing_detail(clip)
        if not enabled:
            return max(0.0, float(local_t) * self._clip_playback_rate(clip)), False

        # ★★ R153：**分组过渡跑完后停在完成态**（消掉"尾巴"）。
        #   用户实报："把这种尾巴平滑掉"。真因见 `_sequential_hold_end`。
        #   只对**带翼** clip（= 分组过渡）生效；无翼顺序内容仍走 R102 周期重放（零回归）。
        wing = _sequential_has_wing(clip)
        # ★★★ R154 边界帧修复（2026-09-22）：上界必须**始终**夹在记录末帧之内。
        #   旧写法只在 `local_t > span` 时才用 hold 末值，而 `local_t == span` 那一帧
        #   走正常路径：`active_t == single` → `source_t = source_duration`（3.9128），
        #   越过记录末帧（3.9060）→ `_resize_cycle_time` 回卷 → **值跳到源起始值**。
        #   真工程 `d4381eb3` 逐帧实测：t=2.479（= 跨度）ch15 = **67**（源首帧 62 附近），
        #   前后两帧都是 128（目标）—— 这就是用户看到的"到位置又跳一下"。
        #   修法：带翼 clip 的 source_t 上界**统一**取 `_sequential_hold_end`
        #   （= min(source_duration−ε, 记录末帧时刻)），边界帧与之后各帧一致。
        cap = max(0.0, float(source_duration) - 1e-4)
        # ★★★ R158（用户："我没有设置它电机参与过渡相位差…延迟翼只对非电机类型
        #   效果生效，它现在还是会抽动"）：**电机通道没有延迟翼语义** → 它的源时刻
        #   不能走"按波次窗口映射"的那条链（`active_t/single × source_duration`），
        #   否则波次一推进 `source_t` 就**回卷**（实测 t=2.00→32.0、t=2.05→11.467，
        #   在批次边界 210↔114 反复 = 抽动）。
        #   正确：电机**按自己的本地时间连续采样源**（见 `_motor_source_time`）。
        _mt = _motor_source_time(self, clip, local_t, source_duration, cap, wing=wing)
        if _mt is not None:
            return _mt

        if wing:
            cap = min(cap, _sequential_hold_end(self, clip, source_duration))
            span = _sequential_span_of(single, pauses, batch_count, step)
            if span > 1e-6 and float(local_t) >= span:
                return _sequential_after_span(self, clip, local_t, source_duration, cap)

        # ★ 2026-09-14 深查修正 B2：顺序扫掠的时长由"单组窗口"决定，与 clip duration
        #   解耦；超出整个扫掠周期时按周期重放（R102 定案）。R152 起"过渡适配 clip
        #   长度"由 `_sequential_single_window` 保证（扫掠跨度不超过 duration）。
        local_t = _sequential_fold_time(
            clip, local_t, single, pauses, batch_count, step=step)

        _batch, active_t, in_pause = self._clip_sequential_active_batch(
            local_t, single, pauses, batch_count, step=step)
        active_t = min(max(0.0, active_t), single)
        # 顺序分组每一组都应完整播放一次源 Clip。这里直接按
        # active_t/single 映射到 source_duration，避免旧工程里 playback_rate
        # 与 single_duration 不一致时把效果挤到结尾或提前截断。
        if single > 1e-6 and source_duration > 1e-6:
            source_t = (active_t / single) * float(source_duration)
        else:
            source_t = active_t * self._clip_playback_rate(clip)
        source_t = min(max(0.0, source_t), cap)
        return source_t, in_pause

    def _coerce_dmx_data(self, data) -> np.ndarray:
        raw = np.asarray(data)
        if raw.dtype.kind == 'f':
            raw = np.clip(raw, 0, 255).astype(np.uint8)
        else:
            raw = np.asarray(data, dtype=np.uint8)
        if raw.shape == (512,):
            return raw
        fixed = self._get_array_u8_512()
        n = min(raw.size, 512)
        if n > 0:
            fixed[:n] = raw.reshape(-1)[:n]
        return fixed

    def _clip_frame_data_at(self, clip: Clip, source_t: float, universe: int) -> Optional[np.ndarray]:
        frame = self._find_frame(clip, source_t, universe=int(universe))
        if frame is None:
            return None
        return self._coerce_dmx_data(getattr(frame, "data", self._get_array_u8_512()))

    def _clip_frame_universes(self, clip: Clip) -> set[int]:
        clip_id = self._clip_cache_id(clip)
        frames = getattr(clip, "frames", []) or []
        frame_count = len(frames)
        cached = self._clip_frame_universes_cache.get(clip_id)
        if cached is not None and int(cached[0]) == int(frame_count):
            return set(cached[1])
        universes: set[int] = set()
        for frame in frames:
            try:
                universes.add(int(getattr(frame, "universe", 1) or 1))
            except Exception:
                continue
        self._clip_frame_universes_cache[clip_id] = (int(frame_count), tuple(sorted(universes)))
        if len(self._clip_frame_universes_cache) > 8192:
            self._evict_oldest(self._clip_frame_universes_cache)
        return universes

    def _clip_active_mask_for_rendered_raw(
        self,
        clip: Clip,
        universe: int,
        raw: np.ndarray,
        base_active: np.ndarray,
        source_t: Optional[float] = None,
        hold_extension: bool = False,
    ) -> np.ndarray:
        mask = np.asarray(base_active, dtype=bool)
        if mask.shape != (512,):
            fixed = self._get_array_bool_512()
            n = min(mask.size, 512)
            if n > 0:
                fixed[:n] = mask.reshape(-1)[:n]
            mask = fixed
        enabled, _groups, _pause, _single = self._clip_sequential_group_timing(clip)
        if not enabled:
            # 区分“设置了 0”和“根本没设置”：非零值保持 active，零值只在
            # 该通道实际发生变化的帧保持 active（_clip_current_channel_active_mask）。
            if source_t is not None:
                return self._clip_current_channel_active_mask(
                    clip, universe, source_t, mask,
                    current_raw=raw, hold_extension=hold_extension)
            return mask
        raw_mask = mask_math(0, np.asarray(raw, dtype=np.uint8))[0]
        if raw_mask.shape != (512,):
            fixed = self._get_array_bool_512()
            n = min(raw_mask.size, 512)
            if n > 0:
                fixed[:n] = raw_mask.reshape(-1)[:n]
            raw_mask = fixed
        base_order_mask = self._clip_base_order_channel_mask(clip, universe)
        if base_order_mask is not None:
            mask = mask_and(mask, base_order_mask)
            raw_mask = mask_and(raw_mask, base_order_mask)
        # 顺序重映射会把源帧写到另一个组的通道上，不能再只按原始
        # active_channels_by_universe 过滤，否则目标组会被挡成全黑。
        return mask_or(mask, raw_mask)

    def _clip_source_has_spatial_variation(
        self,
        clip: Clip,
        universe: int,
        spans: list[tuple[int, int]],
    ) -> bool:
        if not spans:
            return False
        cache_key = f"spatial::{getattr(clip, 'id', '') or id(clip)}::{int(universe)}::{len(getattr(clip, 'frames', []) or [])}::{len(spans)}"
        cached = self._clip_sequential_remap_cache.get(cache_key)
        if cached is not None:
            return bool(cached[1])

        frames = [
            f for f in (getattr(clip, "frames", []) or [])
            if int(getattr(f, "universe", universe) or universe) == int(universe)
        ]
        if len(frames) <= 1:
            result = False
        else:
            frames.sort(key=lambda f: float(getattr(f, "timestamp", 0.0) or 0.0))
            step = max(1, len(frames) // 24)
            sample_indices = sorted(set(list(range(0, len(frames), step)) + [len(frames) - 1]))
            result = False
            # 向量化: span 起止合并成有序边界, reduceat 一次 ufunc 出全部
            # 段最大值 —— max 对 uint8 整值任意结合, span max = 其覆盖段
            # max 的最大; 免每 span 切片拷贝+reduce(最坏 tick 2376 次
            # np.max ≈ 21ms; 1000 组随机对拍 0 差异)。
            _starts = [s for s, _e in spans if 0 <= s < _e <= 512]
            _ends = [_e for s, _e in spans if 0 <= s < _e <= 512]
            if len(_starts) >= 2:
                _bounds = np.unique(np.concatenate([
                    np.asarray(_starts, dtype=np.int64),
                    np.asarray(_ends, dtype=np.int64)]))
                # reduceat 索引必须 < len(arr): 剔除尾部 512 边界,
                # seg 段坐标仍按完整 _bounds 检索(切片含 [511,512) 段)。
                _r_idx = _bounds[_bounds < 512]
                _lo = np.searchsorted(_bounds, _starts, side="left")
                _hi = np.searchsorted(_bounds, _ends, side="left")
                for frame_index in sample_indices:
                    raw = self._coerce_dmx_data(getattr(frames[frame_index], "data", np.zeros(512, dtype=np.uint8)))
                    if _segments_differ(raw, _r_idx, _lo, _hi, spans):
                        result = True
                        break

        self._clip_sequential_remap_cache[cache_key] = (None, result)
        if len(self._clip_sequential_remap_cache) > 128:
            self._evict_oldest(self._clip_sequential_remap_cache)
        return result

    def _clip_sequential_remapped_source_frame(
        self,
        clip: Clip,
        local_t: float,
        source_t: float,
        source_duration: float,
        universe: int,
        fallback_raw: np.ndarray,
        track=None,
        fixture_defs_by_id: Optional[dict] = None,
        proj=None,
        compiled: Optional[dict] = None,
        compiled_by_universe: Optional[dict[int, dict]] = None,
        motor_phase_only: bool = False,
    ) -> tuple[np.ndarray, bool]:
        """Replay the source clip inside the currently running sequential group.

        Sequential group mode is not just a channel mask. A generated chase clip
        already contains spatial timing across the full fixture order; each group
        must receive that same source pattern remapped to its own local order.
        """
        enabled, groups, pauses, single, batch_count, seq_step = self._clip_sequential_timing_detail(clip)
        direction = self._get_clip_fade_in_direction(clip)
        motor_force_enabled = self._clip_motor_force_profile(clip) is not None
        motor_phase_enabled = (
            bool(getattr(clip, "motor_transition_phase_enabled", False)) or motor_force_enabled
        )
        motor_phase_amount = self._clip_motor_phase_amount(clip)
        parallel_motor_phase = False
        if not enabled and bool(motor_phase_only) and motor_phase_enabled and direction != "none":
            duration = max(0.0, float(getattr(clip, "duration", 0.0) or 0.0))
            if duration > 1e-4:
                parallel_motor_phase = True
                groups = max(1, int(getattr(clip, "fade_groups", 1) or 1))
                pauses = []
                single = duration
                batch_count = 1
                seq_step = 0.0
        if (not enabled and not parallel_motor_phase) or direction == "none":
            return fallback_raw, False
        frame_universes = self._clip_frame_universes(clip)
        if frame_universes and int(universe) not in frame_universes:
            return fallback_raw, False
        if proj is None:
            proj = _project_of(self)
        if proj is None:
            return fallback_raw, False

        clip_id = str(getattr(clip, "id", "") or id(clip))
        spatial_scope = self._clip_spatial_scope(clip)
        _bo = getattr(clip, "base_order_fixture_ids", None) or ()
        fast_sig = (
            id(clip),
            len(getattr(clip, "frames", []) or []),
            direction,
            spatial_scope,
            bool(motor_phase_only),
            bool(parallel_motor_phase),
            motor_phase_enabled,
            round(float(motor_phase_amount), 6),
            int(groups),
            int(batch_count),
            tuple(round(float(p), 6) for p in pauses),
            int(getattr(clip, "fade_group_sequence_batches", 0) or 0),
            id(_bo),
            len(_bo),
            len(getattr(clip, "base_fixture_positions", {}) or {}),
        )

        def _render_copy_map(copy_map: tuple[tuple, ...], seed_fallback: bool = False) -> np.ndarray:
            local_now = max(0.0, float(local_t))
            # ★★ R152：波次闸门按各自起点判定（旧式在第 N 波未到时会跳过后续组）。
            _batch_starts = self._clip_sequential_offsets(seq_step, pauses, batch_count)
            end_source_t = max(0.0, float(source_duration) - 1e-4)
            # ★ R156：动态效果（往复循环）越过窗口后继续按源时间轴播放，
            #   否则会钉死在源末尾那一帧的固定角度（用户报的"抽动"）。
            _dyn_remap = _sequential_source_is_cyclic(self, clip, int(universe))
            frame_cache: dict[tuple[int, int, bool], Optional[np.ndarray]] = {}

            def _sample_frame(sample_t: float, sample_universe: int, interpolate: bool = False) -> Optional[np.ndarray]:
                sample_t = min(max(0.0, float(sample_t)), end_source_t)
                key = (int(round(sample_t * 10000.0)), int(sample_universe), bool(interpolate))
                if key not in frame_cache:
                    sample_compiled = None
                    if compiled_by_universe is not None:
                        sample_compiled = compiled_by_universe.get(int(sample_universe))
                    if interpolate and sample_compiled is not None:
                        frame_cache[key] = _interp_compiled_frame(
                            sample_compiled, sample_t)
                    elif sample_compiled is not None:
                        frame_cache[key] = self._compiled_clip_frame_data(sample_compiled, sample_t)
                    else:
                        frame_cache[key] = self._clip_frame_data_at(clip, sample_t, int(sample_universe))
                return frame_cache[key]

            out = np.asarray(fallback_raw, dtype=np.uint8).copy() if seed_fallback else np.zeros(512, dtype=np.uint8)
            for row in copy_map:
                if len(row) >= 8:
                    target_start, source_start, count, sample_universe, target_group, rel_pos, group_size, phase_source = row[:8]
                elif len(row) >= 7:
                    target_start, source_start, count, sample_universe, target_group, rel_pos, group_size = row[:7]
                    phase_source = False
                else:
                    target_start, source_start, count, sample_universe, target_group = row[:5]
                    rel_pos = 0.0
                    group_size = 1.0
                    phase_source = False
                target_batch = int(target_group) % batch_count
                # ★ R158：电机错峰间隔为 0（各台同时起步），只留过渡翼柔和。
                # ⚠ 必须与另两处**同一守卫**（带翼 + 源是往复循环的动态效果）——
                #   漏掉 `_dyn_remap` 会让所有电机行都从批次 0 起步 →
                #   **分组过渡整个消失**（用户实报"很多 clip 明明有分组过渡但却没反应"）。
                _motor_no_delay = (bool(phase_source)
                                   and float(motor_phase_amount) <= 1e-6
                                   and _dyn_remap)
                batch_start = 0.0 if _motor_no_delay else float(_batch_starts[target_batch])
                if local_now < batch_start - 1e-9:
                    continue                      # 该波次还没到自己的起步时刻
                group_t = local_now - batch_start   # 该波次自己的批内时间
                # ★ R158：电机的源时刻（各台同时起步、按本地时间连续播放）。
                if _motor_no_delay:
                    _sd = max(1e-6, float(source_duration))
                    sample_t = (local_now % _sd) if _dyn_remap else min(local_now, end_source_t)
                # ★ R156：动态效果不钉在源末尾；单次过渡才停完成态。
                elif group_t >= single and not _dyn_remap:
                    sample_t = end_source_t
                else:
                    if (bool(phase_source) and motor_phase_enabled
                            and single > 1e-6 and source_duration > 1e-6):
                        phase_span = max(0.0, float(source_duration)) * _motor_phase_fraction(
                            clip, float(motor_phase_amount))
                        phase_scale = 1.0 if parallel_motor_phase else (
                            1.0 - min(max(float(group_t) / max(float(single), 1e-6), 0.0), 1.0))
                        phase_offset = float(rel_pos) * phase_span * phase_scale
                        sample_t = max(0.0, float(source_t)) + phase_offset
                        sample_t = self._clip_motor_force_source_time(
                            clip, sample_t, source_duration
                        )
                        sample_t = min(max(0.0, sample_t), end_source_t)
                    else:
                        sample_t = max(0.0, float(source_t))
                    # ★ 动态效果：越过窗口后按源周期继续推进
                    if _dyn_remap and group_t >= single:
                        _period = float(source_duration) if source_duration > 1e-6 else 0.0
                        sample_t = (max(0.0, float(source_t)) + (group_t - single)) \
                            % _period if _period > 1e-6 else max(0.0, float(source_t))
                source_raw = _sample_frame(
                    sample_t,
                    sample_universe,
                    interpolate=bool(phase_source) and float(motor_phase_amount) > 1e-6,
                )
                if source_raw is None:
                    continue
                out[target_start:target_start + count] = source_raw[source_start:source_start + count]
            return out

        _render_copy_map = bind_renderer(self, _render_copy_map, clip, dict(
            universe=int(universe), raw=fallback_raw, local_t=local_t,
            source_t=source_t, duration=source_duration, single=single,
            step=single if seq_step is None else seq_step, pauses=pauses,
            batch_count=batch_count, phase_amount=motor_phase_amount,
            phase_enabled=int(motor_phase_enabled), parallel=int(parallel_motor_phase),
            motor_no_delay=1, delay_in=getattr(clip, "seq_delay_in", 0.0) or 0.0,
            fade_in=getattr(clip, "fade_in", 0.0) or 0.0,
            compiled_by_universe=compiled_by_universe))
        fast_key = f"{clip_id}::seq_runtime::{int(universe)}"
        fast_cached = self._clip_sequential_remap_cache.get(fast_key)
        if fast_cached is not None and fast_cached[0] == fast_sig:
            copy_map = fast_cached[1]
            if copy_map:
                remapped_source = bool(fast_cached[2]) if len(fast_cached) > 2 else True
                seed_fallback = bool(fast_cached[3]) if len(fast_cached) > 3 else False
                return _render_copy_map(copy_map, seed_fallback), remapped_source
            return fallback_raw, False

        fixture_defs_by_id = fixture_defs_by_id or {
            str(getattr(fd, "id", "")): fd
            for fd in (getattr(proj, "fixture_defs", []) or [])
            if getattr(fd, "id", None)
        }
        all_fixtures = _stable_project_fixtures(self, "_seq_remap_fixtures_cache", proj)
        if not all_fixtures:
            self._clip_sequential_remap_cache[fast_key] = (fast_sig, tuple())
            return fallback_raw, False

        if compiled is not None:
            local_fixtures = list(compiled.get("u_fixtures", []) or [])
            sorted_fixtures = list(compiled.get("sorted_fixtures", []) or [])
            target_spans = list(compiled.get("spans", []) or [])
        else:
            local_fixtures = [
                fi for fi in all_fixtures
                if int(getattr(fi, "universe", 1) or 1) == int(universe)
            ]
            sorted_fixtures, target_spans = self._clip_ordered_fixtures_for_fade(
                clip,
                int(universe),
                local_fixtures,
                track=track,
                fixture_defs_by_id=fixture_defs_by_id,
                proj=proj,
            )
        if not sorted_fixtures:
            self._clip_sequential_remap_cache[fast_key] = (fast_sig, tuple())
            return fallback_raw, False
        source_has_spatial_variation = self._clip_source_has_spatial_variation(
            clip, int(universe), target_spans)
        # ★ R155：三种非重排源（见各判据 docstring）。
        source_is_global_transition = _sequential_source_is_global_transition(
            self, clip, int(universe))
        source_has_time_variation = _sequential_source_time_variation(self, clip, int(universe))
        if (not source_has_spatial_variation or source_is_global_transition
                or not source_has_time_variation):
            self._clip_sequential_remap_cache[fast_key] = (fast_sig, tuple(), False, False)
            return fallback_raw, False
        ordered_ids, _global_index = self._clip_global_fixture_order_for_fade(
            clip, track=track, proj=proj)
        if frame_universes:
            ordered_ids = [
                str(fid) for fid in ordered_ids
                if str(fid) in {
                    str(getattr(fi, "id", ""))
                    for fi in all_fixtures
                    if int(getattr(fi, "universe", 1) or 1) in frame_universes
                }
            ]
        total = len(ordered_ids)
        if total <= 0:
            self._clip_sequential_remap_cache[fast_key] = (fast_sig, tuple())
            return fallback_raw, False
        topview_pos_sig = self._fixture_position_signature(ordered_ids, proj=proj, clip=clip)
        shape_sig = (
            self._topview_shape_signature_for_ids(ordered_ids, proj=proj)
            if spatial_scope != "global" and not self._clip_has_complete_spatial_snapshot(clip, ordered_ids)
            else ()
        )
        cache_sig = (
            len(getattr(clip, "frames", []) or []),
            tuple(str(fid) for fid in ordered_ids),
            tuple(sorted(int(u) for u in frame_universes)),
            self._fixture_list_signature(all_fixtures),
            self._fixture_defs_signature(fixture_defs_by_id),
            direction,
            spatial_scope,
            bool(motor_phase_only),
            bool(parallel_motor_phase),
            int(groups),
            int(batch_count),
            tuple(round(float(p), 6) for p in pauses),
            int(getattr(clip, "fade_group_sequence_batches", 0) or 0),
            topview_pos_sig,
            shape_sig,
        )
        cache_key = f"{clip_id}::seq_remap"
        cached = self._clip_sequential_remap_cache.get(cache_key)
        if cached is None or cached[0] != cache_sig:
            if spatial_scope == "global":
                order_segments = [ordered_ids]
            else:
                order_segments = self._shape_aware_order_segments_for_fade_cached(
                    clip, ordered_ids, proj=proj, spatial_scope=spatial_scope)
                if not order_segments:
                    order_segments = [ordered_ids]
            fixture_by_id = {
                str(getattr(fi, "id", "")): fi
                for fi in all_fixtures
                if getattr(fi, "id", None)
                and (not frame_universes or int(getattr(fi, "universe", 1) or 1) in frame_universes)
            }
            source_order: list[str] = []
            source_order_by_id: dict[str, list[str]] = {}
            for segment in order_segments:
                segment_ids = [str(fid) for fid in segment if str(fid) in fixture_by_id]
                if not segment_ids:
                    continue
                scope_ids = ordered_ids if spatial_scope == "global" else segment_ids
                segment_order = self._order_fixture_ids_for_direction(
                    segment_ids,
                    direction,
                    proj=proj,
                    clip=clip,
                    scope_ids=scope_ids,
                )
                segment_order = [fid for fid in segment_order if str(fid) in fixture_by_id]
                if not segment_order:
                    continue
                source_order.extend(segment_order)
                for fid in segment_ids:
                    source_order_by_id[str(fid)] = segment_order
            if not source_order:
                self._clip_sequential_remap_cache[fast_key] = (fast_sig, tuple())
                return fallback_raw, False

            fixtures_by_universe: dict[int, list] = {}
            for fi in all_fixtures:
                fi_universe = int(getattr(fi, "universe", 1) or 1)
                if frame_universes and fi_universe not in frame_universes:
                    continue
                fixtures_by_universe.setdefault(fi_universe, []).append(fi)
            span_by_id: dict[str, tuple[int, int]] = {}
            for _u, fixtures in fixtures_by_universe.items():
                ordered = sorted(
                    fixtures,
                    key=lambda fi: int(getattr(fi, "start_channel", 1) or 1),
                )
                for idx, fi in enumerate(ordered):
                    fid = str(getattr(fi, "id", ""))
                    if fid:
                        span_by_id[fid] = self._fixture_channel_span(
                            fi, fixture_defs_by_id, ordered, idx)
            source_universe_by_id: dict[str, int] = {}
            for fid, fi in fixture_by_id.items():
                try:
                    source_universe_by_id[str(fid)] = int(getattr(fi, "universe", 1) or 1)
                except Exception:
                    source_universe_by_id[str(fid)] = 1
            cached = (
                cache_sig,
                tuple(source_order),
                {
                    str(fid): tuple(str(item) for item in order)
                    for fid, order in source_order_by_id.items()
                },
                source_universe_by_id,
                span_by_id,
            )
            self._clip_sequential_remap_cache[cache_key] = cached
            if len(self._clip_sequential_remap_cache) > 128:
                self._evict_oldest(self._clip_sequential_remap_cache)

        _sig, source_order, source_order_by_id, source_universe_by_id, span_by_id = cached

        group_idx, group_pos, group_sizes = self._sequential_fixture_group_data(
            clip, sorted_fixtures, direction, groups, track=track, proj=proj)
        copy_map_key = (
            clip_id,
            "seq_copy_map",
            int(universe),
            cache_sig,
            tuple(str(getattr(fi, "id", "") or "") for fi in sorted_fixtures),
            tuple((int(start), int(end)) for start, end in target_spans),
            tuple(round(float(v), 6) for v in group_pos.tolist()),
            bool(source_has_spatial_variation),
            bool(motor_phase_only),
            bool(motor_phase_enabled),
            round(float(motor_phase_amount), 6),
        )
        copy_map = self._clip_fade_geometry_cache.get(copy_map_key)
        if copy_map is not None:
            # ★ 2026-08-06 修复: LRU 刷新(与 fade order 缓存同款, 防淘汰风暴)
            self._clip_fade_geometry_cache.pop(copy_map_key)
            self._clip_fade_geometry_cache[copy_map_key] = copy_map
        if copy_map is None:
            copy_rows: list[tuple[int, int, int, int, int, float, float, bool]] = []
            motor_mask_by_universe: dict[int, np.ndarray] = {}

            def _motor_phase_mask(mask_universe: int) -> np.ndarray:
                mask_universe = int(mask_universe)
                cached_mask = motor_mask_by_universe.get(mask_universe)
                if cached_mask is not None:
                    return cached_mask
                base_mask = self._build_motor_channel_mask(mask_universe, proj)
                expanded = self._expand_motor_pair_mask_for_universe(mask_universe, base_mask, proj=proj)
                motor_mask_by_universe[mask_universe] = expanded
                return expanded

            # ★ R158：跑分组过渡时就按电机掩码拆行（未勾选相位差也要能分辨电机）。
            _split_motor_rows = bool(direction != "none")

            def _append_copy_row_segments(
                target_start_i: int,
                source_start_i: int,
                count_i: int,
                source_universe_i: int,
                target_group_i: int,
                rel_pos_i: float,
                group_size_i: float,
            ) -> None:
                if source_has_spatial_variation:
                    # ★ R158：按电机掩码拆行（`phase_value=True` 的行 = 电机行）。
                    _span = (target_start_i, source_start_i, count_i, source_universe_i,
                             target_group_i, rel_pos_i, group_size_i)
                    if _append_motor_rows(
                            copy_rows, _motor_phase_mask, _span,
                            flags=(int(universe), int(source_universe_i), int(count_i),
                                   bool(motor_phase_enabled), _split_motor_rows,
                                   bool(motor_phase_only))):
                        return
                    _append_plain_row(copy_rows, _span)
                    return
                if not motor_phase_enabled:
                    return
                target_mask = _motor_phase_mask(int(universe))
                source_mask = _motor_phase_mask(int(source_universe_i))
                valid = (
                    target_mask[target_start_i:target_start_i + count_i]
                    & source_mask[source_start_i:source_start_i + count_i]
                )
                if valid.size <= 0 or not bool((valid).any()):
                    return
                run_start: Optional[int] = None
                for offset, is_valid in enumerate(valid.tolist() + [False]):
                    if is_valid and run_start is None:
                        run_start = int(offset)
                    elif not is_valid and run_start is not None:
                        run_len = int(offset) - run_start
                        if run_len > 0:
                            copy_rows.append((
                                int(target_start_i + run_start),
                                int(source_start_i + run_start),
                                int(run_len),
                                int(source_universe_i),
                                int(target_group_i),
                                float(rel_pos_i),
                                float(group_size_i),
                                True,
                            ))
                        run_start = None

            for idx, (target_fixture, (target_start, target_end)) in enumerate(
                zip(sorted_fixtures, target_spans)
            ):
                if not (0 <= target_start < target_end <= 512):
                    continue
                rel_pos = max(0.0, min(1.0, float(group_pos[idx])))
                target_id = str(getattr(target_fixture, "id", "") or "")
                local_source_order = source_order_by_id.get(target_id) or source_order
                if not local_source_order:
                    continue
                max_source_rank = max(0, len(local_source_order) - 1)
                source_rank = int(round(rel_pos * max_source_rank))
                source_rank = max(0, min(max_source_rank, source_rank))
                source_id = str(local_source_order[source_rank])
                source_span = span_by_id.get(source_id)
                if source_span is None:
                    continue
                source_start, source_end = source_span
                if not (0 <= source_start < source_end <= 512):
                    continue
                count = min(int(target_end) - int(target_start), int(source_end) - int(source_start))
                if count <= 0:
                    continue
                _append_copy_row_segments(
                    int(target_start),
                    int(source_start),
                    int(count),
                    int(source_universe_by_id.get(source_id, int(universe))),
                    int(group_idx[idx]),
                    float(group_pos[idx]),
                    float(group_sizes[idx]) if idx < len(group_sizes) else 1.0,
                )
            copy_map = tuple(copy_rows)
            self._store_clip_fade_geometry_cache(copy_map_key, copy_map)
        remapped_source = bool(source_has_spatial_variation and not motor_phase_only)
        seed_fallback = bool(motor_phase_enabled and (not source_has_spatial_variation or motor_phase_only))
        self._clip_sequential_remap_cache[fast_key] = (fast_sig, copy_map, remapped_source, seed_fallback)
        # ★ 性能：146+ 并发活跃 clip 超过旧上限 128 时每帧 evict 重编译；
        #   提到与 _clip_compiled_cache 一致的 4096（仅缓存容量，零逻辑变化）。
        if len(self._clip_sequential_remap_cache) > 4096:
            self._evict_oldest(self._clip_sequential_remap_cache)

        if not copy_map:
            return fallback_raw, False
        return _render_copy_map(copy_map, seed_fallback), remapped_source

    # ── 主循环 ──────────────────────────────────────────────────────────────

    def _maybe_window_precompile(self) -> None:
        """★ 性能：播放循环低频(≥2s)后台预热播放头前方 clip 缓存。

        大工程(成百上千 clip)首次激活的同步编译可达数百 ms/帧——把播放头
        窗口内 clip 的编译提前到后台线程（precompile_clip 只写 GIL 原子 dict，
        渲染路径懒填充兜底，输出字节不变）。
        """
        try:
            nowm = time.monotonic()
            if (getattr(self, "_win_precompile_busy", False)
                    or nowm - float(getattr(self, "_win_precompile_t", 0.0) or 0.0) < 2.0):
                return
            self._win_precompile_t = nowm
            self._win_precompile_busy = True
            proj = _project_of(self)
            if proj is None:
                self._win_precompile_busy = False
                return
            pos_now = time.monotonic() - getattr(self, "_real_start", time.monotonic())  # ★ R193/P2-03

            def _warm() -> None:
                try:
                    self.precompile_window_clips(
                        float(pos_now), proj=proj,
                        lookahead=2.0, backfill=0.5, max_clips=24,
                    )
                except Exception:
                    pass
                finally:
                    self._win_precompile_busy = False

            threading.Thread(target=_warm, daemon=True,
                             name="qascade-window-precompile").start()
        except Exception:
            # ★ 正确性(审查L5)：线程创建失败时复位 busy，避免本会话预热永久停用
            self._win_precompile_busy = False

    def _tick(self):
        _frame_buffer_pool.reset()
        self._maybe_window_precompile()
        # ★ R193/P2-03：monotonic——系统校时/NTP 步进不再使播放位置跳变
        from .timeline_temporal_sample_clock import render_tick_sample
        render_tick_sample(self, time.monotonic() - self._real_start)

    def _dirty_render_xy_ref_sig(self, universe: int):
        """XY 基准指纹(与 _prepare_compiled_clip_frame_for_output_basis 的 ref_sig 同构)。

        脏标记命中前提: XY 基准不变, 否则 prepared 帧字节会变; 指纹变化 → 签名失配
        → 走原求值路径, 保证同步十字线拖动期间输出实时正确。
        """
        refs = None
        nm = self._nm
        if nm is not None:
            try:
                refs = (getattr(nm, "_xy_reference_by_universe", {}) or {}).get(int(universe))
            except Exception:
                refs = None
        if not refs:
            return ()
        return tuple(
            (
                str(getattr(ref, "fixture_id", "")),
                int(getattr(ref, "x_addr", -1)),
                int(getattr(ref, "y_addr", -1)),
                int(getattr(ref, "x_fine_addr", -1) if getattr(ref, "x_fine_addr", None) is not None else -1),
                int(getattr(ref, "y_fine_addr", -1) if getattr(ref, "y_fine_addr", None) is not None else -1),
                round(float(getattr(ref, "angle_deg", 0.0) or 0.0), 6),
                str(getattr(ref, "mode", "")),
            )
            for ref in refs
        )

    @project_frame_scope
    def _render_position(self, now: float, layer_name: str = "clip_playback",
                         _prebuilt_active_items: Optional[list] = None):
        """根据时间位置渲染 clip 帧到指定 clip 层。

        _prebuilt_active_items: CE P4b 简路径检查前已构建的活跃 clip 列表
        (与本函数内部 _indexed_active_clip_items 输出逐项相同 —— 同一函数、
        同一输入); 非 None 时跳过重复扫描(P7 大时间线 hold 反向扫描 +
        逐项 source/fade 链原为每 tick 双份)。其余调用方不受影响。
        """
        if not acquire_render_lock(self):
            return
        t0 = time.perf_counter()
        render_now: Optional[float] = None
        external_frame_adopted = False
        try:
            from .timeline_external_clock import prepare_generator_frame
            generator_plan = prepare_generator_frame(self, layer_name)
            if generator_plan is not None:
                if not generator_plan.render:
                    # Skip before position/reset/cache/output mutations.
                    # Original finally still returns ready/physical ownership.
                    return
                now = generator_plan.position
            now = render_timing_math.render_now(now)
            from .timeline_external_clock import prepare_external_frame
            now = prepare_external_frame(self, now, layer_name)
            render_now = now
            reset_consumption_on_rewind(self, now)
            self._position = now
            if not bool(getattr(self, "_suppress_position_emit", False)):
                self.position_changed.emit(self._position)
            # (_prebuilt_active_items 非 None)跳过本门重查 —— 同帧同输入结果
            # 逐字相同, 免锁帧缓存否决路径二次扫描。
            if _prebuilt_active_items is None and render_cached_frame(self, now, layer_name):
                # ★ R141：烘焙帧服务路径不经活跃集循环 → 在此补一次泵预充
                #   触发/注册（前瞻注册只需播放索引；索引缺失时自动跳过，
                #   不影响任何输出字节）。
                register_due_clips(self, now, (), _project_of(self))
                external_frame_adopted = True
                return

            proj = begin_audio_frame(self, _project_of(self), now, layer_name)
            tracks = list(getattr(proj, "tracks", []) or []) if proj is not None else []
            if not tracks or self._nm is None or not hasattr(self._nm, "update_layer"):
                self._clear_stale_timeline_layers(set(), layer_name=layer_name)
                self._last_record_source_outputs_by_universe = {}
                return

            # 文件夹轨道本身只负责组织，不直接输出；渲染顺序按层级展开，
            # 使上级轨道先写入、子级轨道后写入，从而自然形成“子级覆盖上级，子级结束后恢复上级”的信号通路。
            # 该结构(展开顺序/track_by_id/parent_map/any_solo/文件夹链)随轨道结构变化才重建，
            # 播放期间逐帧命中缓存，避免每帧重建多个 dict + 逐轨走祖先链。
            _struct = self._render_struct_for_tracks(tracks)
            ordered_tracks = _struct["ordered_tracks"]
            track_by_id = _struct["track_by_id"]
            track_parent_map = _struct["track_parent_map"]
            render_tracks = list(ordered_tracks)
            any_solo = _struct["any_solo"]
            _folder_chain_cache = _struct["folder_chain_cache"]
            # universe -> list[(data_u8[512], active_mask[512], anchor_f32[512])]
            clip_layers: Dict[int, List[Tuple[np.ndarray, np.ndarray, np.ndarray]]] = {}
            candidate_clip_layers: Dict[int, List[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]] = {}
            candidate_clip_layers_meta: Dict[int, List[tuple]] = {}
            record_source_candidate_layers: Dict[int, List[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]] = {}
            # R14 保持消费语义: 真实 hold 项(扫描路径)候选登记表 —— (universe,
            # hold_key, 候选元组引用), 每项循环填充, resolve 前做逐帧增量
            # 消费标记(败选通道 |= 消费)。无 hold 项时恒空, 零开销。
            _hold_consume_registry: list = []
            _prepared_frame = begin_frame(self)

            # Build active clip candidates from the playback index, then apply channel takeover globally
            # by clip start_time (newer clip takes over older clip).
            active_clip_items: List[Tuple[float, int, Track, Clip, float, float, bool, float, List[int]]] = []
            if _prebuilt_active_items is not None:
                # CE P4b 已构建(同一 _indexed_active_clip_items/同一输入),
                # 跳过重复扫描 —— 大时间线 hold 反向扫描 + 逐项 source/fade
                # 链原为每 tick 双份。
                active_clip_items = list(_prebuilt_active_items)
            elif self._m2_gate_items is not None:
                # M2 帧级门否决时已构建(同 _indexed_active_clip_items/同输入
                # + 闩已推进), 直接复用免二次索引构造。
                active_clip_items = self._m2_gate_items
                self._m2_gate_items = None
            else:
                # Hold seam epsilon: keep the final rendering quantum consistent with hold-extension
                # to avoid one-frame drop/glitch on the clip -> hold boundary.
                _timer_interval_s = render_timing_math.render_cadence(self, now)
                active_clip_items = self._indexed_active_clip_items(
                    now,
                    tracks,
                    render_tracks,
                    track_by_id,
                    any_solo,
                    _timer_interval_s,
                )

            # Priority rule:
            # 1) parent/child hierarchy first: child overrides parent regardless of start_time
            # 2) among sibling tracks (same parent), newer clip start_time overrides older
            # 3) unrelated branches fallback to track order (later track keeps higher priority)
            # track_parent_map / _folder_chain_cache 取自缓存结构(随轨道结构变化才重建)。

            def _folder_chain(track: Track) -> Tuple[str, ...]:
                tid = str(getattr(track, "id", "") or "")
                cached = _folder_chain_cache.get(tid)
                if cached is not None:
                    return cached
                chain_rev: List[str] = []
                seen: set[str] = set()
                if bool(getattr(track, "is_folder", False)) and tid:
                    chain_rev.append(tid)
                pid = str(getattr(track, "parent_id", "") or "")
                while pid and pid not in seen:
                    seen.add(pid)
                    chain_rev.append(pid)
                    pid = track_parent_map.get(pid, "") or ""
                chain = tuple(reversed(chain_rev))
                if tid:
                    _folder_chain_cache[tid] = chain
                return chain

            # Precompute sort keys with cached folder chains —
            # avoids O(n log n) _folder_chain calls from cmp_to_key wrapper.
            # ★ 性能：排序键 (folder_chain, start, track_order) 全部静态，活跃集
            #   只在 bucket 边界/clip 起止/mute-solo 变化时改变——按 (bucket,
            #   成员序列指纹) 缓存排序【下标】，命中时对新帧列表重取（排序键
            #   不依赖 clip 对象本身，对象替换不影响顺序；缓存下标避免引用陈旧。
            #   ★ 含完整 folder_chain 结构指纹：轨道被移出文件夹/整个文件夹被
            #     拖入另一文件夹（祖父级变化、parent_id 不变）时签名必变、重排
            #     （审查发现1 + 审查L6）。
            _sort_sig = tuple(
                (float(_i[0]), int(_i[1]), id(_i[2]), _folder_chain(_i[2]))
                for _i in active_clip_items
            )
            total_priority = len(active_clip_items)
            # F-2b: priority 仅由排序后位置决定(与 sort sig 同失效域)——
            # 命中时复用上帧 dict, 免每帧 N 项重建 + 每 (clip,u) 查表。
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
                    clip = item[3]
                    clip_id = str(getattr(clip, "id", "") or id(clip))
                    priority_by_clip[(clip_id, int(item[1]), float(item[0]))] = total_priority - idx
                self._active_priority_cache = priority_by_clip

            fixture_defs_by_id_global = self._fixture_defs_index(proj)

            # ── 脏标记增量渲染(性能) ─────────────────────────────────────
            # 每 (clip, universe) 求值结果与每 universe 混合输出按「值变化才重算」缓存:
            # 静态场景(帧不变/hold/无淡入淡出窗口)整帧跳过求值风暴, 输出逐字节不变。
            # 签名含全部时间相关输入(frame_index/shaped_frame/gain/窗口标志/XY基准),
            # 任一变化 → 签名失配 → 走原求值路径; 结构变化经 invalidate_* 清 memo。
            _dirty_on = bool(getattr(self, "_dirty_render_enabled", True))
            uni_sig_sets: Dict[int, list] = {}
            uni_total: Dict[int, int] = {}
            uni_hit: Dict[int, int] = {}
            # F-2c: XY 基准签名跨帧缓存 —— _xy_reference_by_universe 唯一写者
            # set_xy_reference_map 为替换语义且递增 _xy_ref_map_version
            # (同 _xy_ref_compute_cache 版本键模式), 版本不变 → 签名逐位有效。
            _xy_ver_key = (
                id(self._nm) if self._nm is not None else 0,
                getattr(self._nm, "_xy_ref_map_version", 0) if self._nm is not None else 0,
            )
            if getattr(self, "_dr_xy_sig_cache_key", None) != _xy_ver_key:
                self._dr_xy_sig_cache_key = _xy_ver_key
                self._dr_xy_sig_cache = {}
            _dr_xy_sigs = self._dr_xy_sig_cache

            # ── 泵预充电：触发到期事件 + 注册该预充电的 clip（含起点**之前**的
            #   前瞻窗口 —— 修前只在活跃集里注册，而预充价值区间在 clip 起点之前，
            #   事件永远建不出来 → 探针实测 fired=0）。tick 判据不变。
            register_due_clips(self, now, active_clip_items, proj)
            # 灯跟踪活动 clip 快照(每帧重建): 泵/灯分 clip 录制时跨域段需从其他同时播放的 clip 读泵域帧(08-07);
            # R173 本帧无 LT clip(断档) → 清 LT 状态, 防旧泵记忆串进下一段(见 light_tracking_scope)。
            self._lt_active_clips = lt_clips_with_gap_reset(self, [item[3] for item in active_clip_items])
            for _start_t, _track_order, track, clip, local_t, source_t, hold_extension, gain, target_universes in active_clip_items:
                # 热路径内联 compiled 快表命中：每帧 146 次调用若全走函数，
                # 纯 Python 调用+tuple 构建约 4-5ms。这里直接查快表，未命中才回退。
                compiled_by_universe: dict[int, dict] = {}
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
                            _clip_ref,
                            _uni,
                            track=_track_ref,
                            proj=proj,
                            fixture_defs_by_id=fixture_defs_by_id_global,
                        )
                clip_fade_in_dir = self._get_clip_fade_in_direction(clip)
                clip_fade_out_dir = self._get_clip_fade_out_direction(clip)
                seq_enabled, seq_groups, seq_pause, seq_single = self._clip_sequential_group_timing(clip)
                # G-5b: 方向/顺序组已解出 → 传入免 _clip_has_directional_fade
                # 内部重解三条链(同 tick 同 clip 值逐字相同)。
                clip_has_directional_fade = self._clip_has_directional_fade(
                    clip, in_dir=clip_fade_in_dir, out_dir=clip_fade_out_dir,
                    seq_enabled=seq_enabled)
                # ★ 2026-08-09 hoist: 以下三个 clip 级属性不随 universe 变, 原在
                #   per-(clip,universe) 循环内重复求值(75.3 次/帧) — 提到 per-clip 块。
                _dr_clip_profile = self._clip_motor_force_profile(clip)
                _dr_clip_phase_amount = self._clip_motor_phase_amount(clip)
                seq_span = (
                    self._clip_sequential_span(clip, seq_groups, seq_pause, seq_single)
                    if seq_enabled else 0.0
                )
                # One actual native clip window; all universes share this frame-local answer.
                _dr_light_tracking = bool(getattr(clip, "light_tracking_enabled", False))
                (_dr_fade_in_dur, _dr_fade_out_dur, _dr_duration_c, seq_delay_in_c, seq_delay_out_c,
                 _dr_is_real_hold, _dr_is_in_fade_in, _dr_is_in_fade_out,
                 _dr_is_in_sequential_in, _dr_is_in_delay_in, _dr_is_motor_phase_active,
                 _dr_is_directional_in, _dr_in_transit, _dr_transition_boost, _dr_fast_ok) = (
                    render_timing_math.clip_windows(clip, local_t, (hold_extension, seq_enabled,
                        clip_fade_in_dir, clip_fade_out_dir, proj, _dr_light_tracking, seq_span,
                        _dr_clip_phase_amount, total_priority, clip_filter_params(clip)[1])))
                # ── R14 保持消费语义: 真实 hold 项判定 + 窗口重入清除 ──────
                # 真实 hold 项 = 扫描路径项(local_t = float(duration) 精确值;
                # 桶 seam 项 local_t 被钉在 duration-1e-4, 天然区分, 报告 4.2/4.6)。
                # 只对真实 hold 期做消费标记/扣除; clip 自身作者窗口内被覆盖
                # 不算消费(覆盖方在 clip 结束前离开不得误杀 hold)。
                _hold_key = None
                if _dr_is_real_hold:
                    # M1(2026-08-13): 键 = 纯 clip_cache_id(clip 全局唯一,
                    # 对重排/重父化稳定)。原含 track_order(每帧 DFS 派生),
                    # UI 重排/重父化不 invalidate → 键漂移 → 已消费通道复活。
                    _hold_key = self._clip_cache_id(clip)
                elif not hold_extension:
                    # Hold extension has not started yet. Takeover consumption
                    # is different: retain it until seek/stop/rewind, so this
                    # clip cannot reclaim surrendered channels on fade-out.
                    if self._clip_hold_mode(clip) != "none":
                        _ccid = self._clip_cache_id(clip)
                        self._hold_consumed_masks.pop(_ccid, None)
                _dr_record_source_enabled = bool(getattr(clip, "record_output_source_enabled", False))
                _dr_clip_id = str(getattr(clip, "id", "") or id(clip))
                _audio_key = audio_item_key(self, track, proj, now)
                _dr_chain = _folder_chain(track)
                _dr_source_duration = self._clip_source_duration(
                    clip, float(getattr(clip, "duration", 0.0) or 0.0))
                # G-1: priority 每 (clip,u) 同值 —— per-clip 一次算出, 慢路径复用
                _dr_priority = priority_by_clip.get(
                    (_dr_clip_id, int(_track_order), float(_start_t)), 0)
                # G-2: memo 键前缀(4 项与 u 无关)——免每 (clip,u) 重复 float/int 转换
                _dr_memo_prefix = (layer_name, _dr_clip_id, float(_start_t), int(_track_order))
                for u in target_universes:
                        compiled = compiled_by_universe.get(int(u))
                        if compiled is None:
                            continue
                        frame_index = self._compiled_clip_frame_index(compiled, source_t)
                        if frame_index < 0:
                            continue
                        frame_data = compiled.get("frame_data")
                        if frame_data is None or frame_index >= len(frame_data):
                            continue

                        if _dirty_on:
                            # ── 脏标记: 签名=全部时间相关输入; 命中 → 复用上帧结果 ──
                            _dr_xy_sig = _dr_xy_sigs.get(int(u))
                            if _dr_xy_sig is None:
                                _dr_xy_sig = self._dirty_render_xy_ref_sig(int(u))
                                _dr_xy_sigs[int(u)] = _dr_xy_sig
                            _dr_profile = _dr_clip_profile
                            if _dr_profile is not None:
                                _dr_shaped_t = self._clip_motor_force_source_time(
                                    clip, source_t, _dr_source_duration)
                                if abs(_dr_shaped_t - float(source_t)) <= 1e-7:
                                    _dr_frame_shaped = frame_index
                                else:
                                    _dr_frame_shaped = self._compiled_clip_frame_index(
                                        compiled, _dr_shaped_t)
                            else:
                                _dr_frame_shaped = frame_index
                            # R5 P-3: priority 入 sig —— memo 存 tie 后 scores,
                            # 优先级变化即 sig 失配 → 全量重算(免陈旧得分)。
                            # G-1: 值已在 per-clip 块算出(_dr_priority)。
                            sig_item = (
                                int(frame_index), int(_dr_frame_shaped),
                                bool(hold_extension), float(gain),
                                _dr_is_in_fade_in, _dr_is_in_fade_out,
                                _dr_is_in_sequential_in, _dr_is_in_delay_in,
                                _dr_is_motor_phase_active, _dr_light_tracking,
                                _dr_xy_sig, output_filters_signature(clip),   # R110 滤网
                                int(_dr_priority), _audio_key,
                            )
                            sig_item = with_source_mask_token(sig_item, clip, u, source_t)
                            uni_sig_sets.setdefault(int(u), []).append(sig_item)
                            uni_total[int(u)] = uni_total.get(int(u), 0) + 1
                            _memo_key = _dr_memo_prefix + (int(u),)
                            _item_memo = self._dirty_render_item_memo.get(_memo_key)
                            if (_item_memo is not None and _item_memo.get("fast")
                                    and _item_memo.get("sig") == sig_item
                                    and source_memo_matches(self, (_memo_key, clip, u, source_t, hold_extension), _item_memo)):
                                uni_hit[int(u)] = uni_hit.get(int(u), 0) + 1
                                data = _item_memo["data"]
                                active = _item_memo["active"]
                                # R14: memo 存未扣除版, 命中后按当前消费状态
                                # 扣除(新数组, 不污染 memo; sig 无需含消费状态
                                # —— 命中路径每帧重扣, 天然一致)。
                                if _hold_key is not None:
                                    active = self._hold_consumed_active_for_item(
                                        _hold_key, int(u), active)
                                active = self._takeover_consumed_active_for_item(
                                    _dr_clip_id, int(u), active)
                                # memo 写入前提 = np.any(active) 检查已过,
                                # 命中恒有活跃通道(扣除后可能为空 → 跳过);
                                # scores/anchor 与 data/active 同失效域(sig 校验),
                                # 直接共享(resolve/mix 只读 scores/anchor)。
                                if not mask_count(active):
                                    continue
                                scores = _item_memo["scores"]
                                anchor = _item_memo["anchor"]
                                _cands = candidate_clip_layers.setdefault(int(u), [])
                                _cands.append((data, active, anchor, scores))
                                if _prepared_frame is not None:
                                    _prepared_frame.note_memo()
                                candidate_clip_layers_meta.setdefault(int(u), []).append(
                                    (_dr_clip_id, _dr_chain))
                                if _hold_key is not None:
                                    _hold_consume_registry.append(
                                        (int(u), _hold_key, _cands[-1], _dr_chain))
                                if _dr_record_source_enabled:
                                    record_source_candidate_layers.setdefault(int(u), []).append(
                                        (data, active, anchor, scores.copy()))
                                continue

                        raw = self._prepare_compiled_clip_frame_for_output_basis(
                            clip,
                            int(u),
                            compiled,
                            frame_index,
                            np.asarray(frame_data[frame_index], dtype=np.uint8),
                        )
                        raw = self._apply_clip_motor_force_frame(
                            clip,
                            universe=int(u),
                            source_t=source_t,
                            source_duration=_dr_source_duration,
                            raw=raw,
                            compiled=compiled,
                            proj=proj,
                        )

                        # ★ 过渡起点逻辑：淡入时从上一个动作的输出位置插値，而不是从 0 插値。
                        # hold_extension 时强制 gain=1.0，跳过淡出计算，避免保持帧被淡出归零。
                        # F-2a: 与 per-clip 块 _dr_* 值逐字相同(clip 属性渲染路径
                        # 内不被修改), 直接复用免每 (clip,u) 重复 getattr。
                        fade_in_dur = _dr_fade_in_dur
                        fade_out_dur = _dr_fade_out_dur
                        duration_c = _dr_duration_c
                        is_in_fade_in = _dr_is_in_fade_in
                        is_in_sequential_in = _dr_is_in_sequential_in
                        # 延迟时间扫掠：方向已选 + seq_delay>0 时，整个 [0, seq_delay_in+fade_in] 区间都属于
                        # 方向性淡入，须走 anchor 插值分支，否则尾部设备越过 fade_in 会被普通分支硬切。
                        is_in_delay_in = _dr_is_in_delay_in
                        is_motor_phase_active = _dr_is_motor_phase_active
                        is_in_directional_in = _dr_is_directional_in
                        is_in_fade_out = _dr_is_in_fade_out
                        # ★ 2026-08-11 并发过渡帧丢失修复: 过渡窗口内的 clip 得分提升, 免被
                        # LTP 逐通道 winner-take-all 掩蔽。提升量 (total_priority+1)*1e9
                        # 大于任何 priority 差, 同时在内者等幅提升 → 相对顺序仍由 tie 决定。
                        #
                        # ★★ R151：保护须覆盖**整条扫掠在途**，不只是 fade_in/out —— 分组
                        # 过渡时长由条带决定（a93e 条带 5.39s 而 fade_in 仅 2.66s），旧条件让
                        # 条带后段失去保护 → 后起 clip 抢走通道 → "电机到 128 卡一下"。
                        _transition_boost = _dr_transition_boost
                        sequential_source_remapped = False
                        if (is_in_sequential_in or is_motor_phase_active) and proj is not None:
                            raw, sequential_source_remapped = self._clip_sequential_remapped_source_frame(
                                clip,
                                local_t,
                                source_t,
                                _dr_source_duration,
                                u,
                                raw,
                                track=track,
                                proj=proj,
                                compiled=compiled,
                                compiled_by_universe=compiled_by_universe,
                                motor_phase_only=bool(is_motor_phase_active),
                            )
                        # R57: LT 关闭时跳过调用（方法本身早退返回 raw，
                        # 构造性等价；省 CE 挂钩包装开销 14.5 万次/构建）
                        if bool(getattr(clip, "light_tracking_enabled", False)):
                            raw = self._apply_clip_light_tracking(
                                clip, raw, u, local_t, proj)
                        if _prepared_frame is not None:
                            append_candidate(self, _prepared_frame, candidate_input((
                                clip, u, track, proj, now, compiled, raw, source_t, hold_extension, local_t, gain,
                                clip_has_directional_fade, is_in_fade_out, is_in_directional_in, is_in_fade_in,
                                sequential_source_remapped, fixture_defs_by_id_global, _dr_source_duration,
                                _dr_light_tracking, _hold_key, _dr_clip_id, _start_t, _track_order, _dr_priority,
                                _transition_boost, total_priority, _dirty_on, _dr_fast_ok,
                                _memo_key if _dirty_on else None, sig_item if _dirty_on else None,
                                _dr_chain, _dr_record_source_enabled,
                            )), (candidate_clip_layers, candidate_clip_layers_meta,
                                 record_source_candidate_layers, _hold_consume_registry))
                            continue
                        _dir_gains_arrived = None
                        if hold_extension:
                            data = raw.astype(np.float32)
                        elif clip_has_directional_fade:
                            u_fixtures = list(compiled.get("u_fixtures", []) or [])
                            fixture_defs_by_id = fixture_defs_by_id_global
                            dir_gains = self._clip_channel_fade_gains(
                                clip, local_t, u, u_fixtures, track=track,
                                fixture_defs_by_id=fixture_defs_by_id,
                                proj=proj,
                                compiled=compiled)
                            _dir_gains_arrived = dir_gains > 1e-6
                            if is_in_fade_out:
                                target = self._get_fade_out_target(u, proj=proj)
                                data = target + dir_gains * (raw.astype(np.float32) - target)
                            elif is_in_directional_in:
                                if sequential_source_remapped:
                                    prev = self._get_fade_in_anchor(clip, u, track=track, proj=proj)
                                    data = prev + dir_gains * (raw.astype(np.float32) - prev)
                                else:
                                    # 淡入时：prev + gain*(clip - prev)
                                    prev = self._get_fade_in_anchor(clip, u, track=track, proj=proj)
                                    data = prev + dir_gains * (raw.astype(np.float32) - prev)
                                completed_mask = (
                                    np.zeros(512, dtype=bool)
                                    if sequential_source_remapped
                                    else self._clip_completed_sequential_channel_mask(
                                        clip, local_t, u, u_fixtures, track=track,
                                        fixture_defs_by_id=fixture_defs_by_id,
                                        proj=proj,
                                        compiled=compiled)
                                )
                                if (completed_mask).any():
                                    end_raw = self._compiled_clip_frame_data(
                                        compiled,
                                        max(0.0, _dr_source_duration - 1e-4),
                                    )
                                    if end_raw is not None:
                                        end_data = end_raw.astype(np.float32)
                                        data[completed_mask] = (
                                            prev[completed_mask]
                                            + dir_gains[completed_mask] * (end_data[completed_mask] - prev[completed_mask])
                                        )
                            else:
                                data = raw.astype(np.float32) * dir_gains
                        else:
                            if is_in_fade_in:
                                # 淡入时：prev + gain*(clip - prev)
                                prev = self._get_fade_in_anchor(clip, u, track=track, proj=proj)
                                data = prev + gain * (raw.astype(np.float32) - prev)
                            elif is_in_fade_out:
                                target = self._get_fade_out_target(u, proj=proj)
                                data = target + gain * (raw.astype(np.float32) - target)
                            else:
                                # 正常播放：保持原有直出逻辑
                                data = raw.astype(np.float32) * gain
                        data = apply_clip_filters_with_audio(self, clip, u, data, (track, proj, now, compiled, raw, source_t, hold_extension, local_t))
                        np.clip(data, 0.0, 255.0, out=data)
                        _data_f, data = data, data.astype(np.uint8)

                        clip_active = self._clip_active_mask_for_rendered_raw(
                            clip,
                            u,
                            raw,
                            compiled.get("base_active", self._clip_channel_active_mask(clip, u)),
                            source_t=source_t,
                            hold_extension=hold_extension,
                        )
                        if _dr_light_tracking:
                            clip_active = clip_active | np.asarray(
                                compiled.get("light_mask", np.zeros(512, dtype=bool)),
                                dtype=bool,
                            )
                        rule_active = compiled.get("rule_active")
                        if rule_active is not None:
                            rule_active = np.asarray(rule_active, dtype=bool)
                            active = filter_active_mask(clip, u, clip_active, rule_active)
                        else:
                            active = clip_active
                        # R14: 真实 hold 项扣除已消费通道(一次性消费语义)——
                        # 被覆写过的通道从 active 永久扣除, 覆写方离开后旧值
                        # 不复活; 扣除后 scores 只在剩余通道上参与竞争。
                        if _hold_key is not None:
                            active = self._hold_consumed_active_for_item(
                                _hold_key, int(u), active)
                        active = self._takeover_consumed_active_for_item(
                            _dr_clip_id, int(u), active)
                        # ── 逐设备实际到达接管（2026-08-22）────────────────
                        pre_arrival_mask = clip_pre_arrival_mask(
                            active, _dir_gains_arrived, is_in_directional_in)
                        if not (active).any():
                            continue

                        scores = self._compiled_clip_channel_change_scores(
                            clip, int(u), source_t, now, _start_t, active,
                            compiled,
                            hold_extension=hold_extension)
                        # G-1: per-clip 块已算出(慢路径/脏路径同值)。
                        priority = _dr_priority
                        tie = float(priority) * 1e9 + (float(_track_order) + 1.0) * 1e-3 + float(_start_t) * 1e-4
                        scores[active] = scores[active] + tie
                        if _transition_boost > 0.0:
                            # ★★ R143/R173：过渡加分只保护真在动的通道（停在默认值不挡后起 clip），判据见 clip_takeover。
                            _tb_mask = transition_boost_mask(
                                active, _data_f, self._get_fade_out_target(u, proj=proj), raw, fade_in_branch_taken(
                                    clip_has_directional_fade, is_in_directional_in, is_in_fade_in, is_in_fade_out))
                            scores[_tb_mask] = scores[_tb_mask] + _transition_boost
                        apply_pre_arrival_boost(
                            scores, pre_arrival_mask, total_priority)

                        anchor = self._get_clip_anchor(clip, u, active)
                        if _dirty_on and _dr_fast_ok:
                            # 脏标记: 存储 tie 后 scores + anchor(priority 已入
                            # sig_item, 变化即 miss 重算); 命中路径免 np.any/
                            # scores.copy/tie 重加/anchor 查表。fast_ok 时
                            # boost 恒 0(无淡入淡出窗口), scores 即最终得分;
                            # scores/anchor 下游只读, 直接共享。
                            self._dirty_render_item_memo[_memo_key] = {
                                "sig": sig_item,
                                "fast": True,
                                "data": np.asarray(data, dtype=np.uint8).copy(),
                                "active": np.asarray(active, dtype=bool).copy(),
                                "scores": scores,
                                "anchor": anchor,
                            }
                            remember_source_memo(self, (_memo_key, clip, u, source_t, hold_extension), self._dirty_render_item_memo[_memo_key])
                            if len(self._dirty_render_item_memo) > 8192:
                                self._dirty_render_item_memo.pop(next(iter(self._dirty_render_item_memo)))
                        _cands = candidate_clip_layers.setdefault(int(u), [])
                        _cands.append(
                            (_frame_buffer_pool.copy_from(data), active.copy(), anchor, scores))
                        candidate_clip_layers_meta.setdefault(int(u), []).append(
                            (_dr_clip_id, _dr_chain))
                        if _hold_key is not None:
                            _hold_consume_registry.append(
                                (int(u), _hold_key, _cands[-1], _dr_chain))
                        if _dr_record_source_enabled:
                            record_source_candidate_layers.setdefault(int(u), []).append(
                                (_frame_buffer_pool.copy_from(data), active.copy(), anchor, scores.copy()))

            finish_candidates(_prepared_frame)

            # ── R14 保持消费: 当帧败选通道=被后来 clip 覆写 → 一次性消费
            # (不可逆; 直到窗口重入清除/stop/invalidate)。best/effective 判定与
            # _resolve_candidate_layers 同式；标记先于 resolve（扣除此帧起生效）。
            if _hold_consume_registry and not apply_hold_consumption(
                    self, (candidate_clip_layers, candidate_clip_layers_meta), _hold_consume_registry):
                _reg_by_u: Dict[int, list] = {}
                for _u, _hk, _cand, _hk_chain in _hold_consume_registry:
                    _reg_by_u.setdefault(_u, []).append((_hk, _cand, _hk_chain))
                for _u, _entries in _reg_by_u.items():
                    _u_cands = candidate_clip_layers.get(_u)
                    if not _u_cands:
                        continue
                    _best = np.full(512, -np.inf, dtype=np.float64)
                    for _cdata, _cact, _canchor, _cscores in _u_cands:
                        _m = _cact & np.isfinite(_cscores)
                        _best[_m] = np.maximum(_best[_m], _cscores[_m])
                    # ★ 2026-09-19 定案：跨层级压过不消费（离开后恢复），同层级才消费。
                    #   二修：同层级**排除自己**——他人全离场 → 解除消费（见
                    #   `_accumulate_hold_consumed`），否则保持项被永久扣除落回默认 128。
                    _u_meta = (candidate_clip_layers_meta or {}).get(_u)
                    for _hk, _cand, _hk_chain in _entries:
                        if _accumulate_hold_consumed(
                                self._hold_consumed_masks, _hk, _u,
                                cand=_cand, cands=_u_cands, metas=_u_meta,
                                chain=_hk_chain, best=_best):
                            # 新消费标记(极稀疏事件) → 全清防陈旧; 本帧
                            # 随后 resolve 重算并重写 memo, skip 判定见
                            # 空 memo 必走实时路径。
                            self.unlock_timeline_render_cache()

            # ── 脏标记: 全静态 universe(全部 item 命中且签名集合一致)跳过 resolve+mix ──
            skip_unis: set[int] = set()
            memo_unis: Dict[int, dict] = {}
            if _dirty_on:
                memo_unis = self._dirty_render_uni_memo.setdefault(layer_name, {})
                for u, count in uni_total.items():
                    if count > 0 and uni_hit.get(u, 0) == count:
                        prev = memo_unis.get(u)
                        if prev is not None and prev.get("sig_set") == frozenset(uni_sig_sets[u]):
                            skip_unis.add(u)

            (clip_layers, clip_winner_idx), (record_source_layers, _record_winner_idx) = resolve_candidates(
                self, _prepared_frame,
                ({u: v for u, v in candidate_clip_layers.items() if u not in skip_unis},
                 {u: v for u, v in record_source_candidate_layers.items() if u not in skip_unis}),
                resolve_python_candidates,
            )

            # ── 2026-08-22: 同级/非父子覆盖消费 ─────────────────────────
            # 只有 winner 是 loser 的子孙（文件夹层级更深）时，winner 结束后才允许
            # loser 恢复；同级或无关分支被覆写的通道要永久消费，避免“不该恢复的
            # clip 又恢复”。
            if clip_winner_idx and candidate_clip_layers_meta and not apply_takeover_consumption(
                    self, (candidate_clip_layers, candidate_clip_layers_meta), skip_unis):
                for u, metas in candidate_clip_layers_meta.items():
                    if u in skip_unis:
                        continue
                    candidates = candidate_clip_layers.get(u)
                    winner_idx = clip_winner_idx.get(u)
                    if not candidates or winner_idx is None:
                        continue
                    for i, meta in enumerate(metas):
                        if i >= len(candidates):
                            break
                        clip_id, chain = meta
                        active = candidates[i][1]
                        lost = np.asarray(active, dtype=bool) & (winner_idx != i) & (winner_idx >= 0)
                        if not (lost).any():
                            continue
                        for j in np.unique(winner_idx[lost]):
                            if j < 0 or j >= len(metas):
                                continue
                            _w_clip_id, w_chain = metas[j]
                            if self._is_restorable_takeover(chain, w_chain):
                                continue
                            ch_mask = lost & (winner_idx == j)
                            # 只有 winner 的值真的与 loser 不同才算“接管”；
                            # 值相同只是巧合，不应消费掉 loser 的后续所有权。
                            if (ch_mask).any():
                                winner_data = candidates[j][0]
                                loser_data = candidates[i][0]
                                ch_mask = ch_mask & (winner_data != loser_data)
                            if (ch_mask).any():
                                self._mark_takeover_consumed(clip_id, int(u), ch_mask)

            outputs: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}
            for u, items in clip_layers.items():
                if not items:
                    continue
                out_data, out_active = mix_candidates(self, items)
                outputs[u] = (out_data, out_active)
                if _dirty_on:
                    memo_unis[u] = {
                        "sig_set": frozenset(uni_sig_sets.get(u, ())),
                        "outputs": (np.asarray(out_data, dtype=np.uint8).copy(),
                                    np.asarray(out_active, dtype=bool).copy()),
                        "record": None,
                    }
            for u in skip_unis:
                prev = memo_unis.get(u)
                if prev is not None:
                    outputs[u] = prev["outputs"]
            _apply_audio_mod_scope(self, outputs, layer_name, float(now))   # R174#7
            #   （须在跳过域回填之后；见 _apply_audio_mod_scope 文档）

            record_source_outputs: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}
            if layer_name == "clip_playback":
                for u, items in record_source_layers.items():
                    if not items:
                        continue
                    out_data, out_active = mix_candidates(self, items)
                    # D-1: resolve 只产出非空 effective 候选, _mix_clips 输出
                    # active = 输入 OR → out_active 恒非空, any 检查可证死代码。
                    record_source_outputs[int(u)] = (out_data.copy(), out_active.copy())
                    if _dirty_on and u in memo_unis:
                        memo_unis[u]["record"] = (out_data.copy(), out_active.copy())
                if _dirty_on:
                    for u in skip_unis:
                        prev = memo_unis.get(u)
                        if prev is not None and prev.get("record") is not None:
                            record_source_outputs[int(u)] = prev["record"]
            finish_realtime_frame(self, outputs, record_source_outputs, layer_name, now)

            active_universes = set()
            changed_universes = set()
            # D-1: outputs 全部由 resolve/memo 非空 active 构造
            # (4120 mix 前守卫 + 4130 memo 仅存非空), any 检查可证死代码。
            for u in outputs:
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
                            layer_name,
                            outputs,
                            clear_universes=stale_universes,
                        )
                        or set()
                    )
                    queued_worker_frame = True
                except Exception:
                    log.exception("timeline queue_clip_layer_frame failed")
                    queued_worker_frame = False

            if not queued_worker_frame:
                # D-1: 同 4154 —— outputs 各条目 active 恒非空, 检查可证死代码。
                for u, (data, active) in outputs.items():
                    try:
                        changed = self._nm.update_layer(layer_name, int(u), data, active=active)
                    except TypeError:
                        changed = self._nm.update_layer(layer_name, int(u), data)
                    if changed is not False:
                        changed_universes.add(int(u))

            # ★ LTP：点击播放 / 拖针落点时，通知 NetworkManager 更新各 universe 的 clip 接管时间戳。
            # 只在 _ltp_takeover_pending=True 时执行一次，每帧 tick 不执行。
            if getattr(self, "_ltp_takeover_pending", False):
                self._ltp_takeover_pending = False
                if hasattr(self._nm, "notify_clip_takeover"):
                    for u, mask in _merge_takeover_outputs(   # A10 P0: outputs∪scope
                            outputs, getattr(self, "_ltp_takeover_scope", None) or {}).items():
                        if (mask).any():
                            try:
                                self._nm.notify_clip_takeover(int(u), mask)
                            except Exception:
                                log.exception("notify_clip_takeover failed (universe=%s)", u)
                    self._ltp_takeover_scope = None

            if queued_worker_frame:
                layer_map = getattr(self, "_last_output_universes_by_layer", {}) or {}
                layer_map[layer_name] = set(active_universes)
                self._last_output_universes_by_layer = layer_map
                self._last_output_universes = set().union(*layer_map.values()) if layer_map else set(active_universes)
            else:
                self._clear_stale_timeline_layers(active_universes, layer_name=layer_name)
            self._flush_timeline_output_now(changed_universes, layer_name=layer_name)
            external_frame_adopted = True
        finally:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            if elapsed_ms >= self._perf_slow_render_ms:
                now_log = time.monotonic()
                if now_log - self._perf_last_render_log >= 2.0:
                    self._perf_last_render_log = now_log
                    log.info(
                        "Playback render slow frame: %.2f ms, layer=%s",
                        elapsed_ms, layer_name,
                    )
            if render_now is not None:
                self._last_render_time = render_now
            try:
                from .timeline_external_clock import finish_external_frame
                finish_external_frame(self, external_frame_adopted)
            finally:
                release_render_lock(self)


    def _lt_scope_for_universe(
        self,
        clip: Clip,
        universe: int,
        fixtures: list,
        struct_sig: tuple,
        source: str,
        fixture_defs_by_id: dict,
        ) -> Optional[list]:
        """灯跟踪相关 fixture 预过滤(变更E, 2026-08-09)。

        主循环每次调用都遍历该域全部设备并做通道分类(至冬每域 54 台 → 灯跟踪
        21.5% 热点)。相关判定与运行时逐字节一致:
          主路径: has source 且 has RGBW → 写亮度/颜色
          泵路径: has source 无 RGBW 且包内映射到灯 → 写同包灯(F5)
          其余(无 source / 纯灯 / 泵但无包灯) → 原循环 continue, 无任何输出/状态
        过滤后的 scope 与原路径输出逐字节相同(被过滤设备不写 out/_light_tracking_*)。

        缓存键 = (clip_id, universe, struct_sig, source): 结构/来源变化自动失效;
        通道分类预填充 chan_idx_cache(与运行时同代码, 命中免重算)。返回 None =
        clip 未启用灯跟踪 → 调用方回退全量(与原代码一致)。
        """
        if not bool(getattr(clip, "light_tracking_enabled", False)):
            return None
        scope_cache = self._lt_scope_cache
        scope_key = (self._clip_cache_id(clip), int(universe), struct_sig, source)
        cached = scope_cache.get(scope_key)
        if cached is not None:
            return cached
        pkg_map = getattr(self, "_lt_pkg_src_to_lights", None) or {}
        chan_idx_cache = self._lt_chan_idx_cache
        out = []
        for fi in fixtures:
            start = int(getattr(fi, "start_channel", 1) or 1) - 1
            ck = (str(getattr(fi, "def_id", "")), start, source)
            cached_idx = chan_idx_cache.get(ck)
            if cached_idx is None:
                fd = fixture_defs_by_id.get(str(getattr(fi, "def_id", "")))
                channels = list(getattr(fd, "channels", []) or []) if fd is not None else []
                src_idx = []
                rgbw = {"r": [], "g": [], "b": [], "w": []}
                for offset, ch_type in enumerate(channels):
                    idx = start + offset
                    if idx < 0 or idx >= 512:
                        continue
                    ch_value = getattr(ch_type, "value", ch_type)
                    ch_norm = str(ch_value).strip().lower()
                    if ch_norm == source:
                        src_idx.append(idx)
                    elif ch_norm in ("r", "red"):
                        rgbw["r"].append(idx)
                    elif ch_norm in ("g", "green"):
                        rgbw["g"].append(idx)
                    elif ch_norm in ("b", "blue"):
                        rgbw["b"].append(idx)
                    elif ch_norm in ("w", "white"):
                        rgbw["w"].append(idx)
                cached_idx = (src_idx, rgbw)
                chan_idx_cache[ck] = cached_idx
            source_indices, rgbw_indices = cached_idx
            if len(source_indices) <= 0:
                continue
            if any(len(indices) > 0 for indices in rgbw_indices.values()):
                out.append(fi)
            elif pkg_map.get(str(getattr(fi, "id", ""))):
                out.append(fi)
        bounded_put(scope_cache, scope_key, out, 8192)   # FIFO 逐出一条，不整表击穿
        return out

    def _apply_clip_light_tracking(self, clip: Clip, raw: np.ndarray, universe: int, local_t: float, project) -> np.ndarray:
        """按固定目标颜色合成 RGB/W，并让整体强度跟随泵或阀门输出。"""
        if not bool(getattr(clip, "light_tracking_enabled", False)):
            return raw
        if project is None:
            return raw

        try:
            source, threshold, attack, release, max_brightness = lt_intensity_params(
                clip, DEFAULT_LIGHT_TRACKING_ATTACK, DEFAULT_LIGHT_TRACKING_RELEASE)
            target_mask = self._clip_light_tracking_active_mask(clip, universe, project)
            if not target_mask.any():
                return raw
            cr, cg, cb, _color_is_white = lt_color_params(clip)

            # 结构性查找(按 universe 过滤设备 + defs 索引)只随工程结构变化，不随时间/帧变化。
            # 旧代码每次调用(每 clip 每 universe 每 tick,40×/render)都重建，遍历全部 1356 设备。
            # 用廉价结构签名 memo，结构不变时命中缓存，消除随机跳播主要重复成本。逐字节等价。
            memo, rebuilt = structure_memo(self, project)
            struct_sig = memo[0]
            if rebuilt:
                # ★ 2026-08-06 修复(F5): 包关联映射 —— 包内泵/阀成员 → 同包
                #   灯成员。原实现逐 fixture 判定(自身须同时有 source+RGBW),
                #   包内灯(无 pump/valve 通道)整条被跳过 → clip 级灯跟踪对设备包
                #   无效(用户实测)。映射供泵循环把增益应用到同包全部灯。
                try:
                    # ★ 2026-08-06 修复(用户实测): 包→灯映射统一走结构级共享
                    #   _lt_ensure_pkg_map(全部成员反算世界坐标 + 容差25, 与
                    #   TopView 配套机制同口径) — 旧逻辑用灯成员质心(局部坐标)
                    #   找不到同点散台泵(至冬 0/1204 包含泵)。
                    _pkg_src_to_lights = self._lt_ensure_pkg_map(
                        project, memo[2], source)
                    self._lt_pkg_src_to_lights = _pkg_src_to_lights
                except Exception:
                    self._lt_pkg_src_to_lights = {}
            fixtures = memo[1].get(int(universe), [])
            _lt_scope = self._lt_scope_for_universe(
                clip, int(universe), fixtures, struct_sig, source, memo[2])
            if _lt_scope is not None:
                fixtures = _lt_scope
            if not fixtures:
                # 变更E: scope 过滤后可能为空(原全量非空) — 跨域包泵段仍可能
                # 写本域灯(泵在其他域), 不能直接 return raw。索引缺失(None)时
                # 保守进入下方跨域段自行重建; 索引存在且该域无跨域对 → 与原
                # 代码(主循环全 continue + 空跨域段)同结果, return raw。
                _by_uni2_early = getattr(self, "_lt_pkg_map_by_light_uni", None)
                if _by_uni2_early is not None and not _by_uni2_early.get(int(universe), []):
                    return raw
            fixture_defs_by_id = memo[2]
            _fx_by_id = memo[3] if len(memo) > 3 else {}
            out = _frame_buffer_pool.copy_from(raw)
            found_source = False
            # #10 修复：用播放位置 local_t 作时钟，而非 wall-clock time.monotonic()。
            # 渲染烘焙时逐帧按固定间隔推进 local_t，使 attack/release 平滑的 dt=帧间隔，
            # 缓存逐帧确定可复现；实时播放时 local_t 同样随真实时间推进，行为不变。
            now_mono = float(local_t)

            chan_idx_cache = self._lt_chan_idx_cache
            for fi in fixtures:
                start = int(getattr(fi, "start_channel", 1) or 1) - 1
                # 通道分类纯结构(def_id+起始地址+source)，逐帧复用；命中免重复解析。
                ck = (str(getattr(fi, "def_id", "")), start, source)
                cached_idx = chan_idx_cache.get(ck)
                if cached_idx is None:
                    fd = fixture_defs_by_id.get(str(getattr(fi, "def_id", "")))
                    channels = list(getattr(fd, "channels", []) or []) if fd is not None else []
                    src_idx = []
                    rgbw = {"r": [], "g": [], "b": [], "w": []}
                    for offset, ch_type in enumerate(channels):
                        idx = start + offset
                        if idx < 0 or idx >= 512:
                            continue
                        ch_value = getattr(ch_type, "value", ch_type)
                        ch_norm = str(ch_value).strip().lower()
                        if ch_norm == source:
                            src_idx.append(idx)
                        elif ch_norm in ("r", "red"):
                            rgbw["r"].append(idx)
                        elif ch_norm in ("g", "green"):
                            rgbw["g"].append(idx)
                        elif ch_norm in ("b", "blue"):
                            rgbw["b"].append(idx)
                        elif ch_norm in ("w", "white"):
                            rgbw["w"].append(idx)
                    cached_idx = (src_idx, rgbw)
                    chan_idx_cache[ck] = cached_idx
                source_indices, rgbw_indices = cached_idx
                has_source = len(source_indices) > 0
                has_rgbw = any(len(indices) > 0 for indices in rgbw_indices.values())
                if has_rgbw:
                    # 未拥有的灯在颜色/泵记忆/平滑状态写入前排除。
                    rgbw_indices = owned_rgbw_indices(rgbw_indices, target_mask)
                    if rgbw_indices is None:
                        continue
                if not has_source or not has_rgbw:
                    # ★ 2026-08-06 修复(F5): 包内泵/阀(有 source 无 rgbw) → 把
                    #   增益应用到同包全部灯(灯成员无 pump/valve 通道, 原实现
                    #   continue 跳过 → clip 级灯跟踪对设备包无效, 用户实测)。
                    if has_source and not has_rgbw:
                        pkg_lights = getattr(
                            self, "_lt_pkg_src_to_lights", {}
                        ).get(str(getattr(fi, "id", "")), [])
                        _fx_by_id = memo[3] if len(memo) > 3 else {}
                        pkg_lights = target_package_lights(
                            pkg_lights, _fx_by_id, fixture_defs_by_id, universe, target_mask)
                        if pkg_lights:
                            try:
                                # ★ 2026-08-06 修复(探针实锤): 泵可能在其他域
                                #   (至冬: 泵域3, 包灯域44) — 原从当前域 raw 读
                                #   泵通道 → 恒 0 → 灯不亮。泵值改从泵所在域
                                #   的实时输出读取。
                                _pump_uni = int(getattr(fi, "universe", 1) or 1)
                                if _pump_uni != int(universe):
                                    source_level = 0
                                    _nm0 = self._nm
                                    if _nm0 is not None:
                                        try:
                                            _pump_raw = _nm0.compute_universe_now(
                                                _pump_uni,
                                                exclude_layers=("clip_playback",
                                                                "clip_preview",
                                                                "timeline"))
                                            if _pump_raw is not None:
                                                source_level = int(np.max(
                                                    np.asarray(_pump_raw, dtype=np.uint8)[
                                                        source_indices]))
                                        except Exception:
                                            source_level = 0
                                else:
                                    source_level = int(np.max(raw[source_indices]))
                                    # ★ 2026-08-07 修复(用户实测): 同域泵
                                    #   clip 帧值=0 时回退实时(效果/推杆驱动)
                                    if source_level <= 0 and self._nm is not None:
                                        try:
                                            _pump_raw = self._nm.compute_universe_now(
                                                _pump_uni,
                                                exclude_layers=("clip_playback",
                                                                "clip_preview",
                                                                "timeline"))
                                            if _pump_raw is not None:
                                                source_level = int(np.max(
                                                    np.asarray(_pump_raw, dtype=np.uint8)[
                                                        source_indices]))
                                        except Exception:
                                            pass
                                # ★ 2026-08-06 修复(探针实锤): 跨域泵的 active
                                #   判定不能依赖当前域 mask(泵通道不在当前域)
                                #   — 泵值 >0 即视为 active。
                                if _pump_uni != int(universe):
                                    source_is_active = source_level > 0
                                else:
                                    source_active_mask = self._light_tracking_active_source_mask(
                                        clip, int(universe), len(raw))
                                    source_is_active = bool(
                                        (source_active_mask[source_indices]).any())
                                # ★ 2026-08-06 修复(场景变体审计): 自行构造
                                #   src_key2 — 原引用 fixture_key(主路径 4204
                                #   才赋值) → 首个纯泵 fixture NameError 被
                                #   except 吞掉, F5 增益不生效(用户实测 clip 级
                                #   灯跟踪无响应)。
                                src_key2 = "lt:%d:%s:%s" % (
                                    int(universe),
                                    getattr(fi, "id", start),
                                    source)
                                if source_level > 0:
                                    self._light_tracking_state[src_key2] = source_level / 255.0
                                    self._light_tracking_time[src_key2] = now_mono
                                elif source_is_active:
                                    self._light_tracking_state[src_key2] = 0.0
                                    self._light_tracking_time[src_key2] = now_mono
                                elif src_key2 in self._light_tracking_state:
                                    source_level = int(round(
                                        float(self._light_tracking_state[src_key2]) * 255.0))
                                # ★ 2026-08-11 用户需求: 开强度与泵数值无关 —
                                #   超阈值即淡入到「最大亮度」设置, 低于阈值灭。
                                target_gain = (max_brightness / 255.0) if source_level >= threshold else 0.0
                                _last_t2 = float(self._light_tracking_time.get(
                                    src_key2, now_mono))
                                _dt2 = max(0.001, min(0.25, now_mono - _last_t2))
                                _tau2 = attack if target_gain > float(
                                    self._light_tracking_state.get(src_key2, target_gain)) else release
                                _alpha2 = min(1.0, _dt2 / max(0.001, _tau2))
                                _cur2 = float(self._light_tracking_state.get(src_key2, target_gain))
                                _cur2 = _cur2 + (target_gain - _cur2) * _alpha2
                                self._light_tracking_state[src_key2] = _cur2
                                self._light_tracking_time[src_key2] = now_mono
                                # 同包灯写(仅本 universe)
                                for _lfi in pkg_lights:
                                    _lobj = _fx_by_id.get(_lfi)
                                    if _lobj is None:
                                        continue
                                    if int(getattr(_lobj, "universe", 1) or 1) != int(universe):
                                        continue
                                    _lst = int(getattr(_lobj, "start_channel", 1) or 1) - 1
                                    _lfd = fixture_defs_by_id.get(
                                        str(getattr(_lobj, "def_id", "")))
                                    _lch = list(getattr(_lfd, "channels", []) or []) \
                                        if _lfd is not None else []
                                    _rgbw_idx = {"r": [], "g": [], "b": [], "w": []}
                                    for _off, _ct in enumerate(_lch):
                                        _i2 = _lst + _off
                                        if _i2 < 0 or _i2 >= 512 or not target_mask[_i2]:
                                            continue
                                        _cv = str(getattr(_ct, "value", _ct)).strip().lower()
                                        if _cv in ("r", "red"):
                                            _rgbw_idx["r"].append(_i2)
                                        elif _cv in ("g", "green"):
                                            _rgbw_idx["g"].append(_i2)
                                        elif _cv in ("b", "blue"):
                                            _rgbw_idx["b"].append(_i2)
                                        elif _cv in ("w", "white"):
                                            _rgbw_idx["w"].append(_i2)
                                    _r2 = _rgbw_idx.get("r", [])
                                    _g2 = _rgbw_idx.get("g", [])
                                    _b2 = _rgbw_idx.get("b", [])
                                    _w2 = _rgbw_idx.get("w", [])
                                    if not (_r2 or _g2 or _b2 or _w2):
                                        continue
                                    _cw = int(round(_cur2 * 255.0))
                                    for _i3 in _r2 + _g2 + _b2:
                                        out[_i3] = _cw
                                    if _w2:
                                        for _i3 in _w2:
                                            out[_i3] = _cw
                                    # ★ 2026-08-06 修复(场景变体审计): F5 成功
                                    #   写入包灯后置 found_source — 原不置位 →
                                    #   纯泵+包灯结构的 universe 输出被整体丢弃
                                    #   (return raw), clip 级灯跟踪完全无响应。
                                    found_source = True
                            except Exception:
                                pass
                    continue

                fixture_key = f"lt:{int(universe)}:{getattr(fi, 'id', start)}"
                color_key = fixture_key + ":color"
                source_key = fixture_key + ":" + source
                r_idx = rgbw_indices.get("r", [])
                g_idx = rgbw_indices.get("g", [])
                b_idx = rgbw_indices.get("b", [])
                w_idx = rgbw_indices.get("w", [])
                raw_r = int(raw[r_idx[0]]) if r_idx else 0
                raw_g = int(raw[g_idx[0]]) if g_idx else 0
                raw_b = int(raw[b_idx[0]]) if b_idx else 0
                raw_w = int(raw[w_idx[0]]) if w_idx else 0
                if raw_r or raw_g or raw_b:
                    self._light_tracking_color_cache[color_key] = (raw_r, raw_g, raw_b)
                elif raw_w:
                    self._light_tracking_color_cache[color_key] = (raw_w, raw_w, raw_w)

                found_source = True
                source_level = int(np.max(raw[source_indices]))
                source_active_mask = self._light_tracking_active_source_mask(
                    clip,
                    int(universe),
                    len(raw),
                )
                source_is_active = bool((source_active_mask[source_indices]).any())
                if source_level > 0:
                    self._light_tracking_state[source_key] = source_level / 255.0
                    self._light_tracking_time[source_key] = now_mono
                elif source_is_active:
                    self._light_tracking_state[source_key] = 0.0
                    self._light_tracking_time[source_key] = now_mono
                elif source_key in self._light_tracking_state:
                    source_level = int(round(float(self._light_tracking_state[source_key]) * 255.0))
                # ★ 2026-08-11 用户需求: 开的强度与被跟踪数值无关 — 阈值只作
                #   开关门限, 超阈值即淡入到「最大亮度」设置(默认255), 低于阈值灭。
                target = (max_brightness / 255.0) if source_level >= threshold else 0.0
                key = fixture_key + ":level:" + source
                # ★ 颜色沿用：当 clip 颜色为白色（默认）时，从 NM 当前输出读取灯具实际颜色。
                # 颜色缓存在首次渲染时建立，避免每帧重读 NM 输出。
                _use_cr, _use_cg, _use_cb = cr, cg, cb
                if not _color_is_white:
                    self._light_tracking_color_cache[color_key] = (_use_cr, _use_cg, _use_cb)
                if _color_is_white:
                    if color_key not in self._light_tracking_color_cache:
                        _nm = self._nm
                        _sampled_r, _sampled_g, _sampled_b = 255, 255, 255
                        if _nm is not None:
                            try:
                                _nm_out = _nm.compute_universe_now(int(universe))
                                if _nm_out is not None:
                                    _nm_arr = np.asarray(_nm_out, dtype=np.uint8)
                                    _r_idx = rgbw_indices.get("r", [])
                                    _g_idx = rgbw_indices.get("g", [])
                                    _b_idx = rgbw_indices.get("b", [])
                                    _w_idx = rgbw_indices.get("w", [])
                                    _sr = int(_nm_arr[_r_idx[0]]) if _r_idx else 255
                                    _sg = int(_nm_arr[_g_idx[0]]) if _g_idx else 255
                                    _sb = int(_nm_arr[_b_idx[0]]) if _b_idx else 255
                                    if not (_sr == 0 and _sg == 0 and _sb == 0):
                                        _sampled_r, _sampled_g, _sampled_b = _sr, _sg, _sb
                                    if _sr == 0 and _sg == 0 and _sb == 0 and _w_idx:
                                        _sw = int(_nm_arr[_w_idx[0]])
                                        _sampled_r = _sampled_g = _sampled_b = _sw
                            except Exception:
                                pass
                        self._light_tracking_color_cache[color_key] = (_sampled_r, _sampled_g, _sampled_b)
                    _use_cr, _use_cg, _use_cb = self._light_tracking_color_cache[color_key]
                current = float(self._light_tracking_state.get(key, target))
                last_t = float(self._light_tracking_time.get(key, now_mono))
                dt = max(0.001, min(0.25, now_mono - last_t))
                tau = attack if target > current else release
                alpha = min(1.0, dt / max(0.001, tau))
                current = current + (target - current) * alpha
                self._light_tracking_state[key] = current
                self._light_tracking_time[key] = now_mono

                # ★ 2026-08-11 用户需求: 补光优先 — 白色时 W 单珠亮度不够照亮。
                # RGBW 四灯珠全部按比例同时输出: RGB 承载完整彩色分量, W 承载
                # 颜色最大分量作补光, 最大化总光通量（白色 → 四通道同值全亮）。
                has_w = len(rgbw_indices["w"]) > 0
                target_values = {
                    "r": _use_cr,
                    "g": _use_cg,
                    "b": _use_cb,
                    "w": max(_use_cr, _use_cg, _use_cb) if has_w else 0,
                }
                has_rgb = any(len(rgbw_indices[name]) > 0 for name in ("r", "g", "b"))
                if not has_rgb:
                    # 仅 W 灯具无法表达彩色，只能用目标颜色最大分量合成灰度亮度。
                    target_values["w"] = max(_use_cr, _use_cg, _use_cb)

                for channel_name, indices in rgbw_indices.items():
                    if len(indices) == 0:
                        continue
                    value = int(round(max(0.0, min(255.0, target_values[channel_name] * current))))
                    out[indices] = np.uint8(value)

            # ★ 2026-08-06 修复(探针实锤): 跨域包泵处理 — 泵不在当前域
            #   (至冬: 泵域3, 包灯域44)时, 循环内 F5 分支永不执行(泵不在
            #   fixtures 列表)。此处独立处理: 对包映射中「泵在其他域」的源,
            #   读泵域实时输出 → 应用到当前域的映射灯。
            try:
                # 泵域实时输出缓存: 每帧每泵域一次合成(至冬 120 台泵同域
                #   共享一次), 避免跨域段对每台泵重复 compute_universe_now。
                _live_cache: Dict[int, np.ndarray] = {}
                _pkg_map2 = getattr(self, "_lt_pkg_src_to_lights", {}) or {}
                _by_uni2 = getattr(self, "_lt_pkg_map_by_light_uni", None)
                if _by_uni2 is None:
                    # 退化: 索引缺失时从映射重建一次
                    _by_uni2 = {}
                    for _src_fid2, _lights2 in _pkg_map2.items():
                        _sf2 = _fx_by_id.get(_src_fid2)
                        if _sf2 is None:
                            continue
                        _su2 = int(getattr(_sf2, "universe", 1) or 1)
                        for _lfid2 in _lights2:
                            _lo2 = _fx_by_id.get(_lfid2)
                            if _lo2 is None:
                                continue
                            _lu2 = int(getattr(_lo2, "universe", 1) or 1)
                            if _lu2 == _su2:
                                continue
                            _by_uni2.setdefault(_lu2, []).append(
                                (_src_fid2, _su2))
                    self._lt_pkg_map_by_light_uni = _by_uni2
                _cu_set = set(getattr(clip, "clip_universes", []) or [])
                _source_visits = {}
                for (_src_fid2, _suni2) in _by_uni2.get(int(universe), []):
                    _sf2 = _fx_by_id.get(_src_fid2)
                    if _sf2 is None:
                        continue
                    _lights2 = _pkg_map2.get(_src_fid2) or []
                    _lights2 = target_package_lights(
                        _lights2, _fx_by_id, fixture_defs_by_id, universe, target_mask)
                    if source_visit_exhausted(_source_visits, _src_fid2, _suni2, len(_lights2)):
                        continue  # 每个被排除的包灯也不能多推进一次共享泵状态。
                    # ★ 2026-08-07 修复(用户实测): 原 `_cu_set and _suni2 not in
                    #   _cu_set → continue` 在帧查找之前拦截 — 泵/灯分 clip 录制
                    #   时泵域不在灯 clip 的活跃域, 泵值永远读不到(灯=0)。
                    #   合并到下方 4383 判断: 仅当「泵域不在本 clip 且其他同时
                    #   播放 clip 也无泵帧」时才跳过。
                    _sstart2 = int(getattr(_sf2, "start_channel", 1) or 1) - 1
                    _ck2 = (str(getattr(_sf2, "def_id", "")), _sstart2, source)
                    _cidx2 = chan_idx_cache.get(_ck2)
                    if _cidx2 is None:
                        _sfd2 = fixture_defs_by_id.get(str(getattr(_sf2, "def_id", "")))
                        _sch2 = list(getattr(_sfd2, "channels", []) or []) \
                            if _sfd2 is not None else []
                        _src_idx2 = []
                        for _off2, _ct2 in enumerate(_sch2):
                            _i2 = _sstart2 + _off2
                            if _i2 < 0 or _i2 >= 512:
                                continue
                            if str(getattr(_ct2, "value", _ct2)).strip().lower() == source:
                                _src_idx2.append(_i2)
                        _cidx2 = (_src_idx2, None)
                        chan_idx_cache[_ck2] = _cidx2
                    _src_idx2 = _cidx2[0]
                    if not _src_idx2:
                        continue
                    # ★ 2026-08-07 修复(用户实测): 泵 clip 帧值=0(录制时泵
                    #   未动/泵由效果·推杆实时驱动)或泵域不在本 clip(泵/灯
                    #   分 clip 录制)时, 灯跟踪对泵亮度无响应。改为:
                    #   ① 泵域帧不在本 clip → 从同时播放的其他 clip 找(泵
                    #      clip 自身 start_time 对齐局部时间);
                    #   ② 帧值=0 → 回退读泵域实时输出(compute_universe_now
                    #      按泵域每帧缓存一次, 多台泵共享一次合成)。
                    _pt_local = local_t
                    _pframes2 = self._clip_frame_index_for_universe(
                        clip, _suni2)
                    if not _pframes2:
                        _alt = self._lt_find_pump_frames(_suni2, clip)
                        if _alt is not None:
                            _pframes2, _pt_local = _alt
                    if _cu_set and _suni2 not in _cu_set and not _pframes2:
                        continue
                    _pump_raw2 = None
                    if _pframes2:
                        try:
                            _sel = None
                            for _f2 in _pframes2:
                                if float(getattr(_f2, "timestamp", 0.0) or 0.0) > _pt_local:
                                    break
                                _sel = _f2
                            if _sel is None and _pframes2:
                                _sel = _pframes2[0]
                            if _sel is not None:
                                _pump_raw2 = np.asarray(
                                    _sel.data, dtype=np.uint8)
                        except Exception:
                            _pump_raw2 = None
                    _sl2 = 0
                    if _pump_raw2 is not None and _src_idx2:
                        _parr2 = np.asarray(_pump_raw2, dtype=np.uint8)
                        if _parr2.size > max(_src_idx2):
                            _sl2 = int(np.max(_parr2[_src_idx2]))
                    if _sl2 <= 0 and self._nm is not None:
                        try:
                            _live2 = _live_cache.get(_suni2)
                            if _live2 is None:
                                # ★ 2026-08-07 修复(探针实锤): 必须排除 clip
                                #   层 — 泵域 clip 帧=0 渲染后 clip_playback
                                #   以最高优先级覆盖 effect 层 → 混层读 0,
                                #   灯跟踪对实时驱动的泵无响应(用户实测)。
                                #   与录制路径同口径(exclude CLIP_LAYERS)。
                                _live2 = self._nm.compute_universe_now(
                                    _suni2,
                                    exclude_layers=("clip_playback",
                                                    "clip_preview",
                                                    "timeline"))
                                if _live2 is not None:
                                    _live2 = np.asarray(_live2, dtype=np.uint8)
                                _live_cache[_suni2] = _live2
                            if _live2 is not None and _src_idx2 and _live2.size > max(_src_idx2):
                                _sl2 = int(np.max(_live2[_src_idx2]))
                        except Exception:
                            pass
                    # ★ 2026-08-07 修复(用户实测): 泵=0 或低于阈值时写 0
                    #   (灭) — 原 `_sl2<=0 continue` / `_target2<=0 continue`
                    #   跳过 → 灯保持 clip 帧值, 不跟随泵亮度变化(用户实测
                    #   「灯没有跟踪泵的亮度变化」)。与主路径一致: 亮度遮罩
                    #   = 泵是唯一亮度源, 泵 0 → 灯 0。
                    _target2 = (max_brightness / 255.0) if _sl2 >= threshold else 0.0
                    _key2 = "lt:%d:%s:%s" % (_suni2, _src_fid2, source)
                    _cur2 = float(self._light_tracking_state.get(_key2, _target2))
                    _last2 = float(self._light_tracking_time.get(_key2, now_mono))
                    _dt2 = max(0.001, min(0.25, now_mono - _last2))
                    _tau2 = attack if _target2 > _cur2 else release
                    _cur2 = _cur2 + (_target2 - _cur2) * min(1.0, _dt2 / max(0.001, _tau2))
                    self._light_tracking_state[_key2] = _cur2
                    self._light_tracking_time[_key2] = now_mono
                    _cw2 = int(round(_cur2 * 255.0))
                    for _lfid2 in _lights2:
                        _lobj2 = _fx_by_id.get(_lfid2)
                        if _lobj2 is None:
                            continue
                        if int(getattr(_lobj2, "universe", 1) or 1) != int(universe):
                            continue
                        _lst2 = int(getattr(_lobj2, "start_channel", 1) or 1) - 1
                        _lfd2 = fixture_defs_by_id.get(str(getattr(_lobj2, "def_id", "")))
                        _lch2 = list(getattr(_lfd2, "channels", []) or []) \
                            if _lfd2 is not None else []
                        for _off3, _ct3 in enumerate(_lch2):
                            _i3 = _lst2 + _off3
                            if _i3 < 0 or _i3 >= 512 or not target_mask[_i3]:
                                continue
                            if str(getattr(_ct3, "value", _ct3)).strip().lower() \
                                    in ("r", "red", "g", "green", "b", "blue", "w", "white"):
                                out[_i3] = _cw2
                    found_source = True
            except Exception:
                pass
            return out if found_source else raw
        except Exception as exc:
            _warn_key = "%s: %s" % (type(exc).__name__, exc)
            if _warn_key not in self._lt_warned:
                self._lt_warned.add(_warn_key)
                log.warning("clip light tracking failed: %s", exc)
            return raw

    def _lt_find_pump_frames(self, pump_uni: int, cur_clip: Clip):
        """泵域不在当前 clip 时, 从同时播放的其他 clip 找泵域帧。

        返回 (frames列表, 泵clip局部时间) 或 None。
        ★ 2026-08-07 修复(用户实测): 泵/灯分 clip 录制时泵域帧在另一条
        同时播放的 clip 里 — 原只查当前 clip 帧 → 泵值恒 0 → 灯跟踪无
        响应。时间按泵 clip 自身 start_time 对齐(两 clip 起点可不同)。
        """
        cur_id = str(getattr(cur_clip, "id", "") or id(cur_clip))
        _now = float(getattr(self, "_position", 0.0) or 0.0)
        best = None
        for _ac in (getattr(self, "_lt_active_clips", None) or ()):
            if str(getattr(_ac, "id", "") or id(_ac)) == cur_id:
                continue
            _frs = self._clip_frame_index_for_universe(_ac, int(pump_uni))
            if not _frs:
                continue
            _t = max(0.0, float(_now) - float(getattr(_ac, "start_time", 0.0) or 0.0))
            if best is None or _t < best[1]:
                best = (_frs, _t)
        return best

    def _light_tracking_active_source_mask(self, clip: Clip, universe: int, raw_len: int) -> np.ndarray:
        cache_key = (
            self._clip_cache_id(clip),
            "light_source_active",
            int(universe),
            self._clip_static_signature(clip),
            int(raw_len),
        )
        cached = self._clip_render_cache.get(cache_key)
        if cached is not None:
            return cached
        mask = self._clip_channel_active_mask(clip, int(universe))
        self._clip_render_cache[cache_key] = mask
        if len(self._clip_render_cache) > 256:
            self._evict_oldest(self._clip_render_cache)
        return mask

    def _lt_ensure_pkg_map(self, project, fixture_defs_by_id, source) -> Dict[str, list]:
        """包→灯映射结构级缓存: {src_fid: [light_fid...]}。

        ★ 2026-08-06 修复: 原在 _clip_light_tracking_active_mask 每 universe
        重建(1204包×11382台 = 1370万次×73域) → 单个 clip 灯跟踪卡死。
        包映射只随工程结构变化 → 结构签名缓存, 一次构建全域复用。
        """
        try:
            _all_fx = getattr(project, "fixtures", []) or []
            _sig = (structure_signature(project), source)
            _cached = getattr(self, "_lt_pkg_map_cache", None)
            # B(R51 身份缓存纪律): 值里存 project/_all_fx 强引用, 命中处 `is` 复核,
            # 防对象回收后 id() 复用错误命中旧包映射。只加校验, 结果不变。
            if (_cached is not None and _cached[0] == _sig
                    and _cached[3] is project and _cached[4] is _all_fx):
                if len(_cached) > 2:
                    self._lt_pkg_map_by_light_uni = _cached[2]
                return _cached[1]
        except Exception:
            _sig = None
            _cached = None
        _fx_by = {}
        for _f in (getattr(project, "fixtures", []) or []):
            _fx_by[str(getattr(_f, "id", ""))] = _f
        _map: Dict[str, list] = {}
        # ★ 2026-08-06 修复(用户确认): 页级编号配对 — 泵页第 N 个泵 ↔ 包页
        #   第 N 个包(「同一个总设备下的各页面设备」语义, 泵3号↔包3号)。
        #   组/页成员与 logical_devices 成员 ID 不一致(交集0), 用页面位置序
        #   配对最可靠。
        # ★ 2026-08-06 修复(用户指令): **复用 TopView「同一总设备包↔泵」配套
        #   映射**(companion_pages_for_current / _companion_page_units /
        #   append_companion_devices 同口径) — 页名 `<前缀>·设备包` ↔ `<前缀>
        #   ·泵`/`<前缀>·散台` 构成配套页对; 包锚点 = 偏移≈0根成员世界坐标
        #   (兜底最小偏移成员反算), 泵 = 世界坐标; 容差25最近邻贪心, 每泵
        #   至多配一次(至冬实测: 底弧线/上弧线/落叶头 120/72/73 全配, 百变
        #   253/301 — 其余48泵坐标落气爆区页内无配套设备)。
        _pkg_pages: Dict[str, list] = {}
        _pump_pages: Dict[str, list] = {}
        # 顶层 import（2026-09-12）：缩放常量收敛到 core.constants 唯一真源，
        # 同时消除 engine→ui 的层反转（此前借 src.ui.import_patch_csv）。
        from ..core.constants import DEFAULT_IMPORT_SCALE, Y_AXIS_SIGN
        try:
            _pages = (getattr(project, "topview_state", {}) or {}).get("pages", [])
            for _pg in (_pages or []):
                if not isinstance(_pg, dict):
                    continue
                _pname = str(_pg.get("name", "") or "")
                # ★ R131：类别段用"**含段**"判定，不是 `endswith` —— R128 细化把页名
                #   改成 `<文件夹>·<类别>·<类型名>`，类别段夹在中间，`endswith` 全漏判
                #   → 包↔泵最近邻配对整条失效（用户 2026-08-06 定案的"泵 3 号↔包 3 号"）。
                _prefix, _want = _companion_prefix(_pname)
                if not _prefix:
                    continue
                if "泵" in _want:            # 本页是"设备包"页（want 里含泵/散台）
                    for _ld2 in (_pg.get("logical_devices", []) or []):
                        if isinstance(_ld2, dict):
                            _pkg_pages.setdefault(_prefix, []).append(_ld2)
                else:
                    for _fidk in (_pg.get("fixtures", {}) or {}):
                        _pfk = _fx_by.get(str(_fidk))
                        if _pfk is None:
                            continue
                        _pxk = float(getattr(_pfk, "world_x", 0.0) or 0.0)
                        _pzk = float(getattr(_pfk, "world_z", 0.0) or 0.0)
                        if abs(_pxk) > 1.0 or abs(_pzk) > 1.0:
                            _pump_pages.setdefault(_prefix, []).append(
                                (_pxk, _pzk, str(_fidk)))
        except Exception:
            pass
        try:
            for _ld in (getattr(project, "logical_devices", None) or []):
                if not isinstance(_ld, dict):
                    continue
                if str(_ld.get("kind", "")) not in ("package", "unit"):
                    continue
                _members = [str(m) for m in (_ld.get("member_fixture_ids", []) or [])]
                if not _members:
                    continue
                _src_m = []
                _light_m = []
                for _m in _members:
                    _fi = _fx_by.get(_m)
                    if _fi is None:
                        continue
                    _fd = fixture_defs_by_id.get(str(getattr(_fi, "def_id", "")))
                    _ch = list(getattr(_fd, "channels", []) or []) if _fd is not None else []
                    _hs = any(str(getattr(c, "value", c)).strip().lower() == source for c in _ch)
                    _hl = any(str(getattr(c, "value", c)).strip().lower()
                              in ("r", "red", "g", "green", "b", "blue", "w", "white")
                              for c in _ch)
                    if _hs and not _hl:
                        _src_m.append(_m)
                    elif _hl:
                        _light_m.append(_m)
                if not _src_m and _light_m:
                    # ★ 2026-08-06 修复(用户指令): **TopView 配套配对优先** —
                    #   该包在 `<前缀>·设备包` 页 → 同前缀 `<前缀>·泵` 页内
                    #   世界坐标容差25最近邻的泵即为其配套泵(每泵至多配一次)。
                    try:
                        _members_set = set(_members)
                        _prefix_hit = None
                        for _prefix2, _lds2 in _pkg_pages.items():
                            for _ld3 in _lds2:
                                _pm3 = set(str(m) for m in (
                                    _ld3.get("member_fixture_ids", []) or []))
                                if _pm3 and (_members_set & _pm3):
                                    _prefix_hit = _prefix2
                                    break
                            if _prefix_hit:
                                break
                        if _prefix_hit:
                            # 包锚点: 偏移≈0根成员世界坐标(即包锚点); 兜底
                            #   最小偏移成员按画布偏移反算 (topview_panel.
                            #   _logical_device_anchor_world 同式)。
                            _offs2 = _ld.get("local_offsets") or {}
                            _anchor = None
                            _fb2 = None
                            _bm2 = 1e18
                            for _m2 in _members:
                                _off2 = _offs2.get(_m2)
                                if _off2 is None:
                                    continue
                                try:
                                    _ox2 = float(_off2[0])
                                    _oy2 = float(_off2[1])
                                except Exception:
                                    continue
                                _lf2 = _fx_by.get(_m2)
                                if _lf2 is None:
                                    continue
                                _wx2 = float(getattr(_lf2, "world_x", 0.0) or 0.0)
                                _wz2 = float(getattr(_lf2, "world_z", 0.0) or 0.0)
                                if abs(_wx2) <= 1.0 and abs(_wz2) <= 1.0:
                                    continue
                                if abs(_ox2) < 1e-6 and abs(_oy2) < 1e-6:
                                    _anchor = (_wx2, _wz2)
                                    break
                                _mag2 = abs(_ox2) + abs(_oy2)
                                if _mag2 < _bm2:
                                    _bm2 = _mag2
                                    _fb2 = (_wx2, _wz2, _ox2, _oy2)
                            if _anchor is None and _fb2 is not None:
                                try:
                                    _wx2, _wz2, _ox2, _oy2 = _fb2
                                    _anchor = (_wx2 - _ox2 / DEFAULT_IMPORT_SCALE,
                                               _wz2 - _oy2 / (
                                                   DEFAULT_IMPORT_SCALE * Y_AXIS_SIGN))
                                except Exception:
                                    _anchor = None
                            if _anchor is not None:
                                _ax2, _az2 = _anchor
                                _best_i2 = -1
                                _best_d2 = 625.0
                                for _i3, (_px3, _pz3, _pfid3) in enumerate(
                                        _pump_pages.get(_prefix_hit, [])):
                                    _d3 = (_px3 - _ax2) ** 2 + (_pz3 - _az2) ** 2
                                    if _d3 < _best_d2:
                                        _best_d2 = _d3
                                        _best_i2 = _i3
                                if _best_i2 >= 0:
                                    _pf3 = _fx_by.get(
                                        _pump_pages[_prefix_hit][_best_i2][2])
                                    if _pf3 is not None:
                                        _pfd3 = fixture_defs_by_id.get(
                                            str(getattr(_pf3, "def_id", "")))
                                        _pch3 = list(getattr(
                                            _pfd3, "channels", []) or []) \
                                            if _pfd3 is not None else []
                                        if any(str(getattr(c, "value", c))
                                               .strip().lower() == source
                                               for c in _pch3):
                                            _src_m.append(
                                                _pump_pages[_prefix_hit][_best_i2][2])
                                            # 每泵至多配一次(与 TopView 同口径)
                                            _pump_pages[_prefix_hit].pop(_best_i2)
                    except Exception:
                        pass
                    if not _src_m:
                        # ★ 2026-08-06 修复(用户确认): 无配套页时退化 — 同点
                        #   泵匹配用「反算世界坐标」(成员 world - local_offset
                        #   反算, 与 TopView 追加配套设备同口径) — 包成员
                        #   world_x/z 是局部值(探针实测), 用其质心找不到泵。
                        #   匹配点 = **全部成员**的反算世界坐标(含电机/阀等
                        #   非灯成员) — 至冬泵与包内电机/阀成员同点。
                        try:
                            _offs = _ld.get("local_offsets") or {}
                            _pts = []
                            for _m in _members:
                                _lf = _fx_by.get(_m)
                                if _lf is None:
                                    continue
                                _wx = float(getattr(_lf, "world_x", 0.0) or 0.0)
                                _wz = float(getattr(_lf, "world_z", 0.0) or 0.0)
                                _off = _offs.get(_m)
                                if _off and len(_off) >= 2:
                                    try:
                                        _wx = _wx - float(_off[0]) / DEFAULT_IMPORT_SCALE
                                        _wz = _wz - float(_off[1]) / (
                                            DEFAULT_IMPORT_SCALE * Y_AXIS_SIGN)
                                    except Exception:
                                        pass
                                _pts.append((_wx, _wz))
                            _mset = set(_members)
                            _best_pump = None
                            _best_d2 = 625.0
                            for _pf in (getattr(project, "fixtures", []) or []):
                                if str(getattr(_pf, "id", "")) in _mset:
                                    continue
                                _pfd = fixture_defs_by_id.get(str(getattr(_pf, "def_id", "")))
                                _pch = list(getattr(_pfd, "channels", []) or [])                             if _pfd is not None else []
                                if not any(str(getattr(c, "value", c)).strip().lower() == source
                                           for c in _pch):
                                    continue
                                _px = float(getattr(_pf, "world_x", 0.0) or 0.0)
                                _pz = float(getattr(_pf, "world_z", 0.0) or 0.0)
                                for (_cx, _cz) in _pts:
                                    _dx = _px - _cx
                                    _dz = _pz - _cz
                                    _d2 = _dx * _dx + _dz * _dz
                                    if _d2 < _best_d2:
                                        _best_d2 = _d2
                                        _best_pump = str(getattr(_pf, "id", ""))
                            if _best_pump is not None:
                                _src_m.append(_best_pump)
                        except Exception:
                            pass
                for _s in _src_m:
                    _map.setdefault(_s, []).extend(_light_m)
        except Exception:
            pass
        # ★ 2026-08-06 修复(性能强化): 「灯域→(泵fid, 泵域)」跨域索引 —
        #   渲染应用时按当前灯域直接查候选(每 tick 每域 O(该域泵数)),
        #   替代全映射遍历(1194键×73域)。
        _by_uni: Dict[int, list] = {}
        try:
            for _s2, _ls2 in _map.items():
                _sfi2 = _fx_by.get(_s2)
                if _sfi2 is None:
                    continue
                _su2 = int(getattr(_sfi2, "universe", 1) or 1)
                for _l2 in _ls2:
                    _lfi2 = _fx_by.get(_l2)
                    if _lfi2 is None:
                        continue
                    _lu2 = int(getattr(_lfi2, "universe", 1) or 1)
                    if _lu2 == _su2:
                        continue    # 同域由循环内 F5 处理
                    _by_uni.setdefault(_lu2, []).append((_s2, _su2))
        except Exception:
            pass
        if _sig is not None:
            self._lt_pkg_map_cache = (_sig, _map, _by_uni, project, _all_fx)
        self._lt_pkg_map_by_light_uni = _by_uni
        return _map

    def _clip_light_tracking_active_mask(self, clip: Clip, universe: int, project) -> np.ndarray:
        """Python/CE 共同目标mask：只从当前clip真实拥有的设备推导RGB/W。"""
        return light_tracking_target_mask(self, clip, universe, project)

    # ── 淡入淡出核心 ──────────────────────────────────────────────────────────

    @staticmethod
    def _norm_ratio_channel_type(ch_type: str) -> str:
        """把通道类型归一到供「各通道类型延迟比例」使用的粗类型。

        fine 细分通道并入其基础类型（motor_x_fine→motor_x），使一个滑块即可同时
        控制粗调与微调通道。引擎与 UI 必须使用同一套归一规则。
        """
        ct = str(ch_type or "").strip().lower()
        if ct.endswith("_fine"):
            ct = ct[:-5]
        return ct

    def _seq_delay_ease_lut(self, curve):
        """把延迟函数曲线编译成 256 长 float32 LUT (0..1)。None→返回 None（用 smoothstep）。

        以控制点签名缓存，避免每帧重建。
        """
        if curve is None:
            return None
        try:
            pts = getattr(curve, "points", None) or []
            sig = (
                str(getattr(curve, "source", "")),
                str(getattr(curve, "expression", "")),
                tuple((round(float(p.x), 3), round(float(p.y), 3)) for p in pts),
            )
        except Exception:
            sig = None
        cache = getattr(self, "_seq_delay_lut_cache", None)
        if cache is None:
            cache = {}
            self._seq_delay_lut_cache = cache
        if sig is not None and sig in cache:
            return cache[sig]
        try:
            xs = np.arange(256, dtype=np.float64)
            ys = np.asarray(curve.evaluate_array(xs), dtype=np.float32)
            lut = np.clip(ys / 255.0, 0.0, 1.0).astype(np.float32)
        except Exception:
            return None
        if sig is not None:
            if len(cache) > 64:
                cache.clear()
            cache[sig] = lut
        return lut

    @staticmethod
    def _ease_apply(tt: np.ndarray, ease_lut) -> np.ndarray:
        """对归一进度 tt∈[0,1] 应用缓动：ease_lut 为 None 时回退到 smoothstep（逐字节等价旧行为）。"""
        tt = np.clip(tt, 0.0, 1.0)
        if ease_lut is None:
            return (tt * tt * (3.0 - 2.0 * tt)).astype(np.float32)
        idx = np.clip((tt * 255.0).astype(np.int32), 0, 255)
        return ease_lut[idx].astype(np.float32)

    @staticmethod
    def _clip_transition_enabled(clip: Clip) -> bool:
        return bool(str(getattr(clip, "transitions_optimized", "") or "").strip())

    def _clip_transition_strategy(self, clip: Clip, *, fade_in: bool) -> str:
        edge = "transition_strategy_in" if fade_in else "transition_strategy_out"
        strategy = str(getattr(clip, edge, "") or "").strip().lower()
        if not strategy:
            strategy = str(
                getattr(clip, "transition_strategy", "standard_fade") or "standard_fade"
            ).strip().lower()
        return strategy if strategy in TRANSITION_STRATEGIES else "standard_fade"

    def _clip_transition_curve(self, clip: Clip, *, fade_in: bool) -> str:
        edge = "transition_curve_in" if fade_in else "transition_curve_out"
        curve = str(getattr(clip, edge, "") or "").strip()
        if not curve:
            curve = str(getattr(clip, "transition_curve", "Linear") or "Linear")
            curve = curve.strip()
        low = curve.lower()
        if low in TRANSITION_CURVES:
            return low
        # R16：缓动库全名原样返回（求值走 easing；未知名静默回退 linear）
        return curve if curve in EASING_CURVE_NAMES else "linear"

    def _clip_transition_gain(self, clip: Clip, progress: float, *, fade_in: bool) -> float:
        progress = min(max(float(progress), 0.0), 1.0)
        if not self._clip_transition_enabled(clip):
            return progress * progress * (3.0 - 2.0 * progress)
        curve = self._clip_transition_curve(clip, fade_in=fade_in)
        if curve == "easein":
            gain = progress * progress
        elif curve == "easeout":
            gain = 1.0 - (1.0 - progress) * (1.0 - progress)
        elif curve == "easeinout":
            gain = progress * progress * (3.0 - 2.0 * progress)
        elif curve == "bezier":
            inv = 1.0 - progress
            gain = 0.3 * inv * inv * progress + 3.0 * inv * progress * progress
            gain += progress * progress * progress
        else:  # R16：linear 与缓动库全名同入口（linear/未知名恒等，零回归）
            gain = _lib_curve_value(curve, progress)
        strategy = self._clip_transition_strategy(clip, fade_in=fade_in)
        if strategy == "immediate":
            gain = 1.0
        elif strategy == "decelerate" and not fade_in:
            gain = math.sqrt(gain)
        elif strategy == "hold_decay" and not fade_in:
            gain = 1.0 if progress >= 0.6 else progress / 0.6
        elif strategy == "step_down":
            scaled = gain * 4.0
            gain = math.floor(scaled) / 4.0 if fade_in else math.ceil(scaled) / 4.0
        elif strategy == "smooth_ease":
            gain = gain * gain * gain * (gain * (gain * 6.0 - 15.0) + 10.0)
        elif strategy == "delay_then_cut" and not fade_in:
            gain = 1.0
        return min(max(gain, 0.0), 1.0)

    def _clip_transition_gains(
        self,
        clip: Clip,
        tt: np.ndarray,
        *,
        fade_in: bool,
        ease_lut=None,
    ) -> np.ndarray:
        tt = np.clip(np.asarray(tt, dtype=np.float32), 0.0, 1.0)
        if not self._clip_transition_enabled(clip):
            return self._ease_apply(tt, ease_lut)
        curve = self._clip_transition_curve(clip, fade_in=fade_in)
        if curve == "easein":
            gains = tt * tt
        elif curve == "easeout":
            gains = 1.0 - (1.0 - tt) * (1.0 - tt)
        elif curve == "easeinout":
            gains = tt * tt * (3.0 - 2.0 * tt)
        elif curve == "bezier":
            inv = 1.0 - tt
            gains = 0.3 * inv * inv * tt + 3.0 * inv * tt * tt + tt * tt * tt
        else:  # R16：linear 与缓动库全名同入口（linear/未知名恒等，零回归）
            gains = _lib_curve_gains(curve, tt)
        strategy = self._clip_transition_strategy(clip, fade_in=fade_in)
        if strategy == "immediate":
            gains = np.ones_like(tt)
        elif strategy == "decelerate" and not fade_in:
            gains = np.sqrt(gains)
        elif strategy == "hold_decay" and not fade_in:
            gains = np.where(tt >= 0.6, 1.0, np.clip(tt / 0.6, 0.0, 1.0))
        elif strategy == "step_down":
            scaled = gains * 4.0
            gains = np.floor(scaled) / 4.0 if fade_in else np.ceil(scaled) / 4.0
        elif strategy == "smooth_ease":
            gains = gains * gains * gains * (gains * (gains * 6.0 - 15.0) + 10.0)
        elif strategy == "delay_then_cut" and not fade_in:
            gains = np.ones_like(tt)
        return np.asarray(np.clip(gains, 0.0, 1.0), dtype=np.float32)

    def _clip_fixture_type_index_map(self, sorted_fixtures, spans, fixture_defs_by_id):
        """构建 [ {归一通道类型: [span 内偏移,...]} ] 列表，与 sorted_fixtures 一一对应。

        用于「各通道类型单独设置过渡时间比例」时按类型分别写入增益。
        """
        out = []
        defs = fixture_defs_by_id or {}
        for i, fi in enumerate(sorted_fixtures):
            m: dict[str, list[int]] = {}
            fd = getattr(fi, "fixture_def", None) or defs.get(getattr(fi, "def_id", None))
            chans = list(getattr(fd, "channels", []) or []) if fd is not None else []
            start, end = spans[i] if i < len(spans) else (0, 0)
            span_len = max(0, int(end) - int(start))
            for off, ct in enumerate(chans):
                if off >= span_len:
                    break
                m.setdefault(self._norm_ratio_channel_type(ct), []).append(off)
            out.append(m)
        return out

    def _clip_fade_gain(self, clip: Clip, local_t: float) -> float:
        """Return the combined fade-in/fade-out envelope for one clip."""
        duration = max(0.0, float(getattr(clip, "duration", 0.0)))
        if duration <= 0.0:
            return 1.0
        fade_in = max(
            0.0, min(float(getattr(clip, "fade_in", 0.0) or 0.0), duration)
        )
        fade_out = max(
            0.0, min(float(getattr(clip, "fade_out", 0.0) or 0.0), duration)
        )
        gain = 1.0
        if fade_in > 1e-6 and local_t < fade_in:
            progress = max(0.0, min(1.0, local_t / fade_in))
            gain = self._clip_transition_gain(clip, progress, fade_in=True)
        if fade_out > 1e-6 and local_t > duration - fade_out:
            progress = max(0.0, min(1.0, (duration - local_t) / fade_out))
            fade_out_gain = self._clip_transition_gain(clip, progress, fade_in=False)
            gain = min(gain, fade_out_gain)
        return gain

    def _clip_has_directional_fade(self, clip: Clip, *, in_dir=None,
                                   out_dir=None, seq_enabled=None) -> bool:
        """判断clip是否启用了方向性分组淡入淡出。

        新版支持淡入/淡出分别设置方向：
        - fade_in_direction: 淡入方向
        - fade_out_direction: 淡出方向
        - fade_direction: 旧版统一方向（向后兼容）
        只要任一方向不为 none，且对应的 fade 时长 > 0，就启用方向性淡入淡出。

        G-5b: in_dir/out_dir/seq_enabled 为 per-clip 块已解出的同 tick 值
        (同一 clip、同一渲染 tick 内属性不变), 传入即免内部重解
        (实测热路径 7 clip/tick, 每 clip 三链各重解一次)。
        """
        fade_in = float(getattr(clip, "fade_in", 0.0) or 0.0)
        fade_out = float(getattr(clip, "fade_out", 0.0) or 0.0)

        # 获取淡入/淡出各自的方向(未预解时按原路径求值)
        if in_dir is None:
            in_dir = self._get_clip_fade_in_direction(clip)
        if out_dir is None:
            out_dir = self._get_clip_fade_out_direction(clip)

        if seq_enabled is None:
            seq_enabled = self._clip_sequential_group_timing(clip)[0]
        seq_delay_in = float(getattr(clip, "seq_delay_in", 0.0) or 0.0)
        seq_delay_out = float(getattr(clip, "seq_delay_out", 0.0) or 0.0)

        # Sequential grouped playback owns its full group window. It must keep
        # working even when the ordinary fade-in handle is at 0.
        # 延迟时间（seq_delay）同理：选定方向后即使过渡时间为 0，也需走方向路径逐台执行。
        has_dir_in = (in_dir != "none" and (fade_in > 1e-6 or seq_enabled or seq_delay_in > 1e-6))
        has_dir_out = (out_dir != "none" and (fade_out > 1e-6 or seq_delay_out > 1e-6))
        result = has_dir_in or has_dir_out
        if result and log.isEnabledFor(logging.DEBUG):
            log.debug("[DIR_FADE] clip=%s in_dir=%s out_dir=%s fade_in=%.2f fade_out=%.2f -> has_dir=%s",
                      getattr(clip, 'id', '?')[:8], in_dir, out_dir, fade_in, fade_out, result)
        return result

    def _normalize_clip_direction(self, direction: str) -> str:
        direction = str(direction or "none").strip().lower()
        # G-4: 别名表改模块级常量(51 次/tick 免重建 23 项 dict)。
        return _CLIP_DIRECTION_ALIASES.get(direction, direction)

    def _clip_spatial_scope(self, clip: Clip) -> str:
        scope = str(getattr(clip, "fade_group_spatial_scope", "per_shape") or "per_shape").strip().lower()
        aliases = {
            "shape": "per_shape",
            "local": "per_shape",
            "group": "per_shape",
            "fixture_group": "per_shape",
            "overall": "global",
            "world": "global",
        }
        scope = aliases.get(scope, scope)
        # 哨兵 "follow"（或空）→ 跟随项目全局灯控模式的**坐标系语义**（联动所有页面）：
        # single=单控灯→per_shape(各自坐标系)；whole=整体控灯→global(整体坐标系)。
        # ★ 2026-08-04 概念澄清(两维正交)：坐标系是 effect 坐标空间，控灯粒度
        #   (包内灯当一个/逐个)是另一维度——本方法只解析坐标系；粒度由
        #   effect_browser 注入 _control_mode/_control_groups 独立消费。
        if scope in ("follow", "follow_global", ""):
            proj = _project_of(self)
            mode = str(getattr(proj, "light_control_mode", "whole") or "whole").strip().lower()
            return "global" if mode == "whole" else "per_shape"
        return scope if scope in ("per_shape", "global") else "per_shape"

    def _get_clip_fade_in_direction(self, clip: Clip) -> str:
        """获取 clip 的淡入方向。优先使用 fade_in_direction，回退到 fade_direction。"""
        in_dir = getattr(clip, "fade_in_direction", "") or ""
        if in_dir and in_dir != "none":
            return self._normalize_clip_direction(in_dir)
        # 回退到旧版统一方向
        return self._normalize_clip_direction(getattr(clip, "fade_direction", "none") or "none")

    def _get_clip_fade_out_direction(self, clip: Clip) -> str:
        """获取 clip 的淡出方向。

        淡出方向没有单独设置时默认跟随淡入方向；旧工程的 fade_direction
        仍作为兼容回退。
        """
        out_dir = getattr(clip, "fade_out_direction", "") or ""
        if out_dir and out_dir != "none":
            return self._normalize_clip_direction(out_dir)
        legacy = self._normalize_clip_direction(getattr(clip, "fade_direction", "none") or "none")
        if legacy != "none":
            return legacy
        return self._get_clip_fade_in_direction(clip)

    def _clip_channel_active_mask(self, clip: Clip, universe: int) -> np.ndarray:
        """返回512维布尔数组，标记 clip 中指定 universe 的有效输出通道。

        优先使用 active_channels_by_universe，因为电机/位置类通道的合法值可能是 0。
        旧工程缺少该字段时，按录制器语义推断：首帧非零通道加后续变化通道；
        这样既保留静态首帧输出，也能识别首帧为零、之后才启动的延迟通道。
        """
        universe = int(universe)
        # ★ 2026-08-06 性能攻坚: 缓存键瘦身 — 原每 call 重建
        #   tuple(str(fid) for base_order)(11382 台 = 11382 次字符串), 即使
        #   缓存命中。改用 (id, len) 签名(base_order 整体替换时 id 变 →
        #   失效正确; 帧数/内容变化由 len(frames) 覆盖)。
        # 空 base_order 用稳定 () 而非 []: [] 每次 BUILD_LIST 新对象 → id 每 call
        # 变 → 缓存键永不命中 → 400 clip 录制 372ms/tick。() 是常量单例, id 恒定。
        _bo = getattr(clip, "base_order_fixture_ids", None) or ()
        cache_key = (
            self._clip_cache_id(clip),
            "active_mask",
            universe,
            len(getattr(clip, "frames", []) or []),
            id(_bo),
            len(_bo),
        )
        cached = self._clip_render_mask_cache.get(cache_key)
        if cached is not None:
            # ★ 2026-08-09: 命中返回只读共享引用(仿 _clip_xy_reference_cache 模式)。
            #   35.45 次/帧 × 64B copy 为纯浪费; 全部调用方只读(& / | / np.any /
            #   切片取值 / .copy()), write=False 使意外原地写立即抛错。
            return cached
        mask = np.zeros(512, dtype=bool)
        try:
            active_map = getattr(clip, "active_channels_by_universe", {}) or {}
            channels = active_map.get(universe) or active_map.get(str(universe))
            if channels:
                for ch in channels:
                    idx = int(ch) - 1
                    if 0 <= idx < 512:
                        mask[idx] = True
                if mask.any():
                    return self._store_clip_render_mask(cache_key, mask)
        except Exception:
            pass
        base_order_mask = self._clip_base_order_channel_mask(clip, universe)
        previous = None
        for frame in getattr(clip, "frames", []) or []:
            if int(getattr(frame, "universe", 1)) != universe:
                continue
            raw = np.asarray(frame.data, dtype=np.uint8).reshape(-1)
            if raw.size == 0:
                continue
            current = np.zeros(512, dtype=np.uint8)
            n = min(raw.size, 512)
            current[:n] = raw[:n]
            if previous is None:
                mask |= current > 0
            else:
                mask |= current != previous
            previous = current
        if base_order_mask is not None:
            mask &= base_order_mask
        if not mask.any():
            if base_order_mask is not None and (base_order_mask).any():
                mask = base_order_mask.copy()
            else:
                mask[:] = True
        return self._store_clip_render_mask(cache_key, mask)

    def _clip_base_order_channel_mask(self, clip: Clip, universe: int) -> Optional[np.ndarray]:
        raw_ids = getattr(clip, "base_order_fixture_ids", []) or []
        fixture_ids = self._unique_fixture_id_list(raw_ids)
        if not fixture_ids:
            return None
        # 记忆化: 该 mask 仅取决于 (clip, universe, base_order 列表) + 静态配接。
        # 批渲染每帧多次以相同参数调用 → 缓存后免去重建 1356 设备 dict + 海量 getattr。
        cache_key = (self._clip_cache_id(clip), int(universe), tuple(fixture_ids))
        cached = self._clip_base_order_mask_cache.get(cache_key)
        if cached is not None:
            # None 用哨兵区分“未算”与“算出为空”
            return None if cached is _EMPTY_MASK_SENTINEL else cached
        proj = _project_of(self)
        if proj is None:
            return None
        fixture_defs_by_id = self._fixture_defs_index(proj)
        fixture_by_id = self._fixtures_by_id_index(proj)
        if not fixture_by_id:
            return None
        mask = np.zeros(512, dtype=bool)
        for fid in fixture_ids:
            fi = fixture_by_id.get(str(fid))
            if fi is None:
                continue
            try:
                if int(getattr(fi, "universe", 1) or 1) != int(universe):
                    continue
            except Exception:
                continue
            start, end = self._fixture_channel_span(fi, fixture_defs_by_id)
            if 0 <= start < end <= 512:
                mask[start:end] = True
        result = mask if (mask).any() else None
        self._clip_base_order_mask_cache[cache_key] = (
            _EMPTY_MASK_SENTINEL if result is None else result
        )
        return result

    def _expand_motor_pair_mask_for_universe(self, universe: int, mask: np.ndarray, proj=None) -> np.ndarray:
        """Keep 16-bit motor coarse/fine channels active as a pair for filtering."""
        expanded = np.asarray(mask, dtype=bool)
        if expanded.shape != (512,):
            fixed = np.zeros(512, dtype=bool)
            n = min(expanded.size, 512)
            if n > 0:
                fixed[:n] = expanded.reshape(-1)[:n]
            expanded = fixed
        else:
            expanded = expanded.copy()
        if not (expanded).any():
            return expanded

        if proj is None:
            proj = _project_of(self)
        if proj is None:
            return expanded

        try:
            fdefs = {
                str(getattr(fd, "id", "")): fd
                for fd in (getattr(proj, "fixture_defs", []) or [])
                if getattr(fd, "id", None)
            }
            for fi in (getattr(proj, "fixtures", []) or []):
                if int(getattr(fi, "universe", 1) or 1) != int(universe):
                    continue
                fd = fdefs.get(str(getattr(fi, "def_id", "")))
                by_type: Dict[str, List[int]] = {}
                start_idx = int(getattr(fi, "start_channel", 1) or 1) - 1
                for offset, ch_type in enumerate(getattr(fd, "channels", []) or []):
                    idx = start_idx + int(offset)
                    if 0 <= idx < 512:
                        by_type.setdefault(str(ch_type), []).append(idx)
                # ★ 通用 16bit 对：def 内任何 {X}/{X}_fine（含 function_N 对）同电机逻辑
                fine_pairs: Dict[str, List[str]] = {}
                for _ct in (getattr(fd, "channels", []) or []):
                    _ct_s = str(_ct)
                    if _ct_s.endswith("_fine") and _ct_s[:-5]:
                        fine_pairs.setdefault(_ct_s[:-5], []).append(_ct_s)
                for coarse_type, fine_list in fine_pairs.items():
                    pair = list(by_type.get(coarse_type, []) or [])
                    for _f in fine_list:
                        pair.extend(list(by_type.get(_f, []) or []))
                    if pair and mask_count(expanded[pair]):
                        expanded[pair] = True
        except Exception:
            pass
        return expanded

    def _track_filter_rule_for_universe(self, track, universe: int):
        """Return the effective filter rule for a track/universe pair."""
        if track is None:
            return None
        rule = None
        try:
            if hasattr(track, "get_filter_for_universe"):
                rule = track.get_filter_for_universe(universe)
            elif hasattr(track, "universe_filters"):
                rule = (getattr(track, "universe_filters", {}) or {}).get(universe)
            if rule is None:
                rule = getattr(track, "filter_rule", None)
        except Exception:
            rule = None
        return rule

    def _track_filter_mask_for_universe(self, track, universe: int, proj=None) -> Optional[np.ndarray]:
        """Return a 512-channel filter mask, or None when the track allows all."""
        rule = self._track_filter_rule_for_universe(track, universe)
        if rule is None:
            return None
        try:
            if rule.is_empty() or rule.allows_all():
                return None
        except Exception:
            pass
        mask = np.zeros(512, dtype=bool)
        for ch in getattr(rule, "channels", []) or []:
            try:
                idx = int(ch) - 1
            except Exception:
                continue
            if 0 <= idx < 512:
                mask[idx] = True
        if not (mask).any():
            return None
        return self._expand_motor_pair_mask_for_universe(universe, mask, proj=proj)

    def _compiled_clip_universe(
        self,
        clip: Clip,
        universe: int,
        track=None,
        proj=None,
        fixture_defs_by_id: Optional[dict] = None,
        ) -> dict:
        """Return immutable per-universe clip playback data built outside the hot path."""
        universe = int(universe)
        # A2 任务构建瘦身: id 三元组直映射一次命中, 免 quick_key 4 元 tuple + str 构建
        # + 2 次 dict 查(11382 台每帧 146 次调用 → 任务构建段 2.5-3.4ms 的单项大头)。
        # 值持 clip/track 引用并 is 校验: 对象回收后 id() 复用 / track 被替换 / 失效
        # 清表均不会命中旧编译结果 —— 语义与 quick_key 相同(同一 compiled 对象)。
        fast_key = (id(clip), universe, id(track) if track is not None else None)
        fast = self._clip_compiled_fast.get(fast_key)
        if fast is not None and fast[0] is clip and fast[1] is track:
            return fast[2]
        clip_cache_id = self._clip_cache_id(clip)
        track_cache_id = str(getattr(track, "id", "") or id(track) if track is not None else "")
        quick_key = (clip_cache_id, "compiled_universe_quick", universe, track_cache_id)
        cached = self._clip_compiled_cache.get(quick_key)
        if cached is not None:
            # 快表被失效清空后, 其余 clip 的 quick_key 仍命中 → 顺手回填, 免下帧
            # 重复走 tuple 构建路径。
            self._clip_compiled_fast[fast_key] = (clip, track, cached)
            return cached
        fixture_defs_by_id = fixture_defs_by_id or {
            str(getattr(fd, "id", "")): fd
            for fd in (getattr(proj, "fixture_defs", []) or [])
            if getattr(fd, "id", None)
        } if proj is not None else {}
        all_fixtures = list(getattr(proj, "fixtures", []) or []) if proj is not None else []
        u_fixtures = [
            fi for fi in all_fixtures
            if int(getattr(fi, "universe", 1) or 1) == universe
        ]
        track_filter_sig = self._track_filter_signature_for_universe(track, universe)
        cache_key = (
            clip_cache_id,
            "compiled_universe",
            universe,
            self._clip_static_signature(clip),
            self._fixture_list_signature(u_fixtures),
            self._fixture_defs_signature(fixture_defs_by_id),
            track_filter_sig,
            tuple(
                (str(k), tuple(int(vv) for vv in (v or [])))
                for k, v in sorted((getattr(clip, "active_channels_by_universe", {}) or {}).items(), key=lambda item: str(item[0]))
            ),
        )
        cached = self._clip_compiled_cache.get(cache_key)
        if cached is not None:
            # 与 quick_key 命中分支同理: 回填快表 + quick_key, 免下帧重复走
            # 全签名 tuple 构建(与重建路径的双键插入对称)。
            self._clip_compiled_fast[fast_key] = (clip, track, cached)
            self._clip_compiled_cache[quick_key] = cached
            return cached
        if bool(getattr(self, "_timeline_render_cache_locked", False)):
            # R187-②：锁定态 miss 只计数、回退下方实时编译。修前此处直接返回
            # 全空帧——M2 hold 消费否决帧 / 预热线程(precompile_window_clips) /
            # LRU 驱逐 / 局部 invalidate 都会合法走到 miss（真实工程实测播放期
            # 预热线程每秒吃空帧），"实时路径被否"即此：黑帧而非实时续播。
            self._timeline_render_lock_misses = int(getattr(self, "_timeline_render_lock_misses", 0) or 0) + 1

        # ★ 2026-08-06 修复: 复用 _clip_frame_index_cache(per-universe 索引)替代
        #   全帧过滤 — 原实现对每个 universe 全量扫描 clip.frames(至冬录制 clip
        #   876K 帧 × 73 域 = 64M 次迭代阻塞 UI 线程, 用户实测「1-2 个录制 clip
        #   卡死整个时间线」)。与 _find_frame 同一缓存(容量/失效同源)。
        _all_frames = getattr(clip, "frames", []) or []
        if _all_frames:
            _ckey = str(getattr(clip, "id", "") or id(clip))
            _fcnt = len(_all_frames)
            _cached = self._clip_frame_index_cache.get(_ckey)
            if _cached is None or _cached[0] != _fcnt:
                _by_u: Dict[int, List] = {}
                for _f in _all_frames:
                    try:
                        _u = int(getattr(_f, "universe", 1) or 1)
                    except Exception:
                        _u = 1
                    _by_u.setdefault(_u, []).append(_f)
                for _lst in _by_u.values():
                    _lst.sort(key=lambda f: float(getattr(f, "timestamp", 0.0) or 0.0))
                _cached = (_fcnt, _by_u)
                self._clip_frame_index_cache[_ckey] = _cached
                if len(self._clip_frame_index_cache) > 256:
                    self._evict_oldest(self._clip_frame_index_cache)
            frames = list(_cached[1].get(int(universe), []))
        else:
            frames = []
        frame_times = np.asarray(
            [float(getattr(f, "timestamp", 0.0) or 0.0) for f in frames],
            dtype=np.float64,
        )
        if frames:
            frame_data = np.stack(
                [self._coerce_dmx_data(getattr(f, "data", np.zeros(512, dtype=np.uint8))) for f in frames],
                axis=0,
            ).astype(np.uint8, copy=False)
        else:
            frame_data = np.zeros((0, 512), dtype=np.uint8)

        base_active = self._clip_channel_active_mask(clip, universe)
        track_mask = self._track_filter_mask_for_universe(track, universe, proj=proj)
        if track_mask is not None:
            rule_active = track_mask.copy()
        else:
            rule_active = None
        light_mask = self._clip_light_tracking_active_mask(clip, universe, proj)

        native_times = _try_clip_change_times(
            self, frame_data, frame_times, base_active, callback=_ce_clip_change_times)
        if native_times is None:
            last_change_time = _numpy_clip_change_times(frames, frame_data, frame_times, base_active)
        else:
            changed_by_frame, last_change_time = native_times

        sorted_fixtures: list = []
        spans: list[tuple[int, int]] = []
        if u_fixtures:
            sorted_fixtures, spans = self._clip_ordered_fixtures_for_fade(
                clip,
                universe,
                u_fixtures,
                track=track,
                fixture_defs_by_id=fixture_defs_by_id,
                proj=proj,
            )

        compiled = {
            "frame_times": frame_times,
            # B-2: Python list 副本供 bisect_left 快路径(searchsorted 对
            # NaN/未排序无保证, 仅全有限时启用; CE 仍读 frame_times 原
            # 数组不受影响)。
            "ftl": frame_times.tolist() if bool(np.isfinite(frame_times).all()) else None,
            "frame_data": frame_data,
            "base_active": np.asarray(base_active, dtype=bool).copy(),
            "rule_active": rule_active,
            "light_mask": np.asarray(light_mask, dtype=bool).copy(),
            "last_change_time": last_change_time,
            "sorted_fixtures": list(sorted_fixtures),
            "spans": list(spans),
            "span_idx": self._make_span_index_map(spans),
            "u_fixtures": list(u_fixtures),
        }
        self._clip_compiled_cache[cache_key] = compiled
        self._clip_compiled_cache[quick_key] = compiled
        self._clip_compiled_fast[fast_key] = (clip, track, compiled)
        if (
            len(self._clip_compiled_cache) > 4096
            and not bool(getattr(self, "_timeline_render_cache_building_lock", False))
        ):
            # 每次编译插入 2 键(quick_key + cache_key),但只淘汰 1 键会在超限后
            # 每个新对净增 ~1 键(EDGE_MEMORY 实测 8548→11999 条目/+179MB/pass)。
            # 批量淘汰直至回到上限内:稳态每编译淘汰最旧 2 键(一对),缓存恒有界。
            while len(self._clip_compiled_cache) > 4096:
                self._evict_oldest(self._clip_compiled_cache)
            # A2: 快表同界(键含对象引用, 上限淘汰防跨会话累积); 被淘汰键
            # 下次帧快表 miss → quick_key 兜底命中 → 顺手回填, 无额外编译。
            while len(self._clip_compiled_fast) > 4096:
                self._evict_oldest(self._clip_compiled_fast)
        return compiled

    @staticmethod
    def _compiled_clip_frame_index(compiled: dict, source_t: float) -> int:
        # C-1: ftl(list 副本)存在时全程只走 bisect, frame_times 数组
        # 仅在回退分支物化 —— 免每调用 np.zeros 默认参 + asarray 包装
        # (~2.5µs × 52 次/tick)。ftl 与 frame_times 编译期同源等长,
        # 恒可信任; 无 ftl(含非有限时间戳)走原 searchsorted 路径。
        _tl = compiled.get("ftl")
        if _tl is not None:
            count = len(_tl)
        else:
            times = np.asarray(compiled.get("frame_times", _EMPTY_F64_ARRAY),
                               dtype=np.float64)
            count = int(times.size)
        if count <= 0:
            return -1
        if count == 1:
            return 0
        source_t = max(0.0, float(source_t))
        # B-2: 编译期 per-universe 按 timestamp 排序 + 全有限门控 →
        # bisect_left 与 np.searchsorted(side="left") 逐位同判
        # (1000 组随机对拍 0 差异), 免每次 np.asarray 包装。
        if _tl is not None:
            idx = bisect.bisect_left(_tl, source_t)
        else:
            idx = int(np.searchsorted(times, source_t, side="left"))
        if idx <= 0:
            return 0
        if idx >= count:
            return count - 1
        if _tl is not None:
            prev_t = float(_tl[idx - 1])
            next_t = float(_tl[idx])
        else:
            prev_t = float(times[idx - 1])
            next_t = float(times[idx])
        return idx - 1 if source_t - prev_t < next_t - source_t else idx

    def _compiled_clip_frame_data(self, compiled: dict, source_t: float) -> Optional[np.ndarray]:
        idx = self._compiled_clip_frame_index(compiled, source_t)
        if idx < 0:
            return None
        data = compiled.get("frame_data")
        if data is None or idx >= len(data):
            return None
        return np.asarray(data[idx], dtype=np.uint8)

    def _apply_clip_motor_force_frame(
        self,
        clip: Clip,
        *,
        universe: int,
        source_t: float,
        source_duration: float,
        raw: np.ndarray,
        compiled: dict,
        proj,
    ) -> np.ndarray:
        shaped_t = self._clip_motor_force_source_time(clip, source_t, source_duration)
        if (
            self._clip_motor_force_profile(clip) is None
            or abs(shaped_t - float(source_t)) <= 1e-7
        ):
            return raw
        shaped = self._compiled_clip_frame_data(compiled, shaped_t)
        if shaped is None:
            return raw
        frame_index = self._compiled_clip_frame_index(compiled, shaped_t)
        shaped = self._prepare_compiled_clip_frame_for_output_basis(
            clip, int(universe), compiled, frame_index, np.asarray(shaped, dtype=np.uint8)
        )
        motor_mask = compiled.get("motor_force_mask")
        if motor_mask is None:
            motor_mask = self._build_motor_channel_mask(int(universe), proj)
            motor_mask = self._expand_motor_pair_mask_for_universe(
                int(universe), motor_mask, proj=proj
            )
            compiled["motor_force_mask"] = motor_mask
        if not (motor_mask).any():
            return raw
        out = np.asarray(raw, dtype=np.uint8).copy()
        out[motor_mask] = shaped[motor_mask]
        return out

    def _fixture_channel_span(self, fi, fixture_defs_by_id: Optional[dict],
                              ordered_fixtures: Optional[list] = None,
                              index: int = -1) -> tuple[int, int]:
        start = int(getattr(fi, "start_channel", 1) or 1) - 1
        if not (0 <= start < 512):
            return start, start

        fixture_def = getattr(fi, "fixture_def", None)
        if fixture_def is None and fixture_defs_by_id is not None:
            fixture_def = fixture_defs_by_id.get(str(getattr(fi, "def_id", "")))

        ch_count = 0
        if fixture_def is not None:
            ch_count = int(getattr(fixture_def, "channel_count", 0) or 0)
            if ch_count <= 0:
                channels_attr = getattr(fixture_def, "channels", None)
                if isinstance(channels_attr, (list, tuple)):
                    ch_count = len(channels_attr)
                else:
                    try:
                        ch_count = int(channels_attr or 0)
                    except Exception:
                        ch_count = 0
        if ch_count <= 0 and ordered_fixtures is not None and 0 <= index + 1 < len(ordered_fixtures):
            next_start = int(getattr(ordered_fixtures[index + 1], "start_channel", start + 2) or (start + 2)) - 1
            ch_count = max(1, next_start - start)
        if ch_count <= 0:
            ch_count = 1
        return start, min(512, start + max(1, ch_count))

    @staticmethod
    def _unique_fixture_id_list(values) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for value in values or []:
            fid = str(value or "")
            if fid and fid not in seen:
                out.append(fid)
                seen.add(fid)
        return out

    @staticmethod
    def _group_value(group, key: str, default=None):
        if isinstance(group, dict):
            return group.get(key, default)
        return getattr(group, key, default)

    def _fixtures_by_universe_index(self, proj, all_fixtures: list) -> dict:
        """工程级 universe→设备 预索引（一次性构建，引用持有防 id 复用）。

        ★ 性能：_clip_used_fixture_ids_for_order 原实现每 clip 全扫 11382 台
        （73 clip 首激活 ≈ 83 万次迭代 ≈ 0.4s）；索引构建一次后 O(universe数)。
        ★ 始终以 proj.fixtures 全量构建（调用方可能传入 frame_universes 过滤
        子集——子集内容逐 clip 不同，不能作为缓存键；universe 过滤由调用方
        在取用时做，结果与原逐台分组一致）。工程切换/设备增删时
        (id(proj), id(raw), len) 必变。
        """
        raw = getattr(proj, "fixtures", None) if proj is not None else None
        base = raw if raw is not None else all_fixtures
        sig = (id(proj), id(base), len(base))
        idx = getattr(self, "_fx_by_uni", None)
        # B(R51 身份缓存纪律): 值里存 base 强引用, 命中处 `is base` 复核 ——
        # docstring 声称的「引用持有防 id 复用」此前并未实现, 现补齐。
        if (idx is not None and getattr(self, "_fx_by_uni_sig", None) == sig
                and getattr(self, "_fx_by_uni_raw", None) is base):
            return idx
        fx_by_uni: dict[int, list] = {}
        for fi in base or []:
            try:
                u = int(getattr(fi, "universe", 1) or 1)
            except Exception:
                u = 1
            fx_by_uni.setdefault(u, []).append(fi)
        self._fx_by_uni = fx_by_uni
        self._fx_by_uni_raw = base
        self._fx_by_uni_sig = sig
        return fx_by_uni

    def _fixture_by_id_index(self, proj, all_fixtures: list) -> dict:
        """工程级 fid→设备 预索引（一次性构建，引用持有防 id 复用）。

        ★ 正确性(审查M1)：调用方可能传入 frame_universes 过滤子集——子集内容
        逐 clip 不同，缓存只对全量列表有效；子集时直接构建不缓存（量级
        ≈子集大小，远小于原实现的全量扫描+构建）。
        """
        raw = getattr(proj, "fixtures", None) if proj is not None else None
        if raw is None or len(all_fixtures) != len(raw):
            return {
                str(getattr(fi, "id", "")): fi
                for fi in all_fixtures
                if str(getattr(fi, "id", "") or "")
            }
        sig = (id(proj), id(raw), len(raw))
        idx = getattr(self, "_fx_by_id", None)
        # B(R51 身份缓存纪律): 值里存 raw 强引用, 命中处 `is raw` 复核
        # (docstring 已声明该纪律, 此前未实现)。
        if (idx is not None and getattr(self, "_fx_by_id_sig", None) == sig
                and getattr(self, "_fx_by_id_raw", None) is raw):
            return idx
        fx_by_id = {
            str(getattr(fi, "id", "")): fi
            for fi in raw
            if str(getattr(fi, "id", "") or "")
        }
        self._fx_by_id = fx_by_id
        # ★ 性能：valid_ids set 一并缓存（原每 clip 重建 11382 项 set ≈ 5ms）
        self._fx_valid_ids = set(fx_by_id)
        self._fx_by_id_raw = raw
        self._fx_by_id_sig = sig
        return fx_by_id

    def _global_fixture_order_index(self, proj, all_fixtures: list) -> list:
        """工程级全局设备排序索引（natural name/id/universe/start 五键，
        与原 fallback 排序完全一致；一次性构建，引用持有防 id 复用）。

        ★ 正确性(审查M1)：缓存只对全量列表有效——调用方传入 frame_universes
        过滤子集（内容逐 clip 不同）时直接排序不缓存（量级≈子集，原实现
        就是每 clip 全量排序，无回归）。
        """
        raw = getattr(proj, "fixtures", None) if proj is not None else None
        if raw is None or len(all_fixtures) != len(raw):
            return [
                str(getattr(fi, "id", "") or "")
                for fi in sorted(
                    all_fixtures,
                    key=lambda item: (
                        _natural_number(getattr(item, "name", "")),
                        _natural_number(getattr(item, "id", "")),
                        int(getattr(item, "universe", 1) or 1),
                        int(getattr(item, "start_channel", 1) or 1),
                        str(getattr(item, "id", "") or ""),
                    ),
                )
            ]
        sig = (id(proj), id(raw), len(raw))
        idx = getattr(self, "_global_order", None)
        # B(R51 身份缓存纪律): 值里存 raw 强引用, 命中处 `is raw` 复核
        # (docstring 已声明该纪律, 此前未实现)。
        if (idx is not None and getattr(self, "_global_order_sig", None) == sig
                and getattr(self, "_global_order_raw", None) is raw):
            return idx
        idx = [
            str(getattr(fi, "id", "") or "")
            for fi in sorted(
                raw,
                key=lambda item: (
                    _natural_number(getattr(item, "name", "")),
                    _natural_number(getattr(item, "id", "")),
                    int(getattr(item, "universe", 1) or 1),
                    int(getattr(item, "start_channel", 1) or 1),
                    str(getattr(item, "id", "") or ""),
                ),
            )
        ]
        self._global_order = idx
        self._global_order_raw = raw
        self._global_order_sig = sig
        return idx

    def _clip_used_fixture_ids_for_order(self, clip: Clip, all_fixtures: list,
                                         fixture_defs_by_id: Optional[dict],
                                         track=None, proj=None) -> set[str]:
        frame_universes = self._clip_frame_universes(clip)
        # ★ 性能：工程级预索引（构建一次），不再每 clip 全扫 11382 台。
        #   分组内容/顺序与原逐台 setdefault 完全一致（同 all_fixtures 遍历序）；
        #   frame_universes 过滤只影响参与分组的 universe，结果集合不变。
        fx_by_uni = self._fixtures_by_universe_index(proj, all_fixtures)
        if frame_universes:
            fixtures_by_universe = {
                u: fx_by_uni[u] for u in frame_universes if u in fx_by_uni
            }
        else:
            fixtures_by_universe = fx_by_uni

        used: set[str] = set()
        for universe, fixtures in fixtures_by_universe.items():
            ordered = sorted(
                fixtures,
                key=lambda fi: int(getattr(fi, "start_channel", 1) or 1),
            )
            active = self._clip_channel_active_mask(clip, int(universe))
            track_mask = self._track_filter_mask_for_universe(track, int(universe), proj=proj)
            if track_mask is not None:
                active = active & track_mask
            if not (active).any():
                continue
            for idx, fi in enumerate(ordered):
                fid = str(getattr(fi, "id", "") or "")
                if not fid:
                    continue
                start, end = self._fixture_channel_span(fi, fixture_defs_by_id, ordered, idx)
                if 0 <= start < end <= 512 and bool((active[start:end]).any()):
                    used.add(fid)
        return used

    def _clip_base_order_fixture_ids(self, clip: Clip, track=None, proj=None,
                                     all_fixtures: Optional[list] = None,
                                     fixture_defs_by_id: Optional[dict] = None) -> list[str]:
        if proj is None:
            proj = _project_of(self)
        all_fixtures = list(all_fixtures if all_fixtures is not None else (getattr(proj, "fixtures", []) or []))
        _bo = getattr(clip, "base_order_fixture_ids", None) or ()
        cache_key = (
            self._clip_cache_id(clip),
            "base_order_fixture_ids",
            str(getattr(track, "id", "") or id(track) if track is not None else ""),
            len(all_fixtures),
            len(getattr(proj, "fixture_groups", []) or []) if proj is not None else 0,
            len(getattr(clip, "frames", []) or []),
            id(_bo),
            len(_bo),
        )
        cached = self._clip_fade_geometry_cache.get(cache_key)
        if cached is not None:
            # ★ 2026-08-06 修复: LRU 刷新(get 命中 move_to_end) — 原插入序淘汰
            #   get 不刷新, 至冬 300 universe × 多 clip 工作集 > 8192 → 每 tick
            #   淘汰最旧 → 每 tick 重算 O(N×S)/O(N²) 几何 → 分组过渡开启即卡死
            #   (用户实测)。命中刷新保持热键存活。
            self._clip_fade_geometry_cache.pop(cache_key)
            self._clip_fade_geometry_cache[cache_key] = cached
            return list(cached)

        def _store_order(order: list[str]) -> list[str]:
            frozen = tuple(str(fid) for fid in (order or []) if str(fid))
            self._store_clip_fade_geometry_cache(cache_key, frozen)
            return list(frozen)

        # ★ 性能：工程级 fid→设备 预索引（构建一次，73 clip 首激活省 73 次
        #   11382 项 dict+set 构建）
        fixture_by_id = self._fixture_by_id_index(proj, all_fixtures)
        valid_ids = fixture_by_id  # 当前作用域真源；dict 查键免建 set，失效不串域
        clip_order = [
            fid for fid in self._unique_fixture_id_list(getattr(clip, "base_order_fixture_ids", []) or [])
            if fid in valid_ids
        ]
        if clip_order:
            return _store_order(clip_order)

        if proj is None or not all_fixtures:
            return _store_order([])
        fixture_defs_by_id = fixture_defs_by_id or {
            str(getattr(fd, "id", "")): fd
            for fd in (getattr(proj, "fixture_defs", []) or [])
            if getattr(fd, "id", None)
        }
        used_ids = self._clip_used_fixture_ids_for_order(
            clip, all_fixtures, fixture_defs_by_id, track=track, proj=proj)
        if not used_ids:
            return _store_order([])
        group_order: list[str] = []
        group_seen: set[str] = set()
        for group in getattr(proj, "fixture_groups", []) or []:
            group_ids = [
                str(fid) for fid in (
                    self._group_value(group, "base_order_fixture_ids", None)
                    or self._group_value(group, "fixture_ids", [])
                    or []
                )
            ]
            group_ids = [fid for fid in group_ids if fid in used_ids and fid not in group_seen]
            if not group_ids:
                continue
            for fid in group_ids:
                group_order.append(fid)
                group_seen.add(fid)
        if group_order:
            return _store_order(group_order)
        ordered: list[str] = []
        seen: set[str] = set()
        # ★ 性能：全局排序只依赖 all_fixtures（与 clip 无关）——索引缓存一次，
        #   每 clip fallback 不再对 11382 台全排序（73 clip 首激活 ≈ 0.3s → 0）。
        global_sorted = self._global_fixture_order_index(proj, all_fixtures)
        for fid in global_sorted:
            if fid in used_ids and fid not in seen:
                ordered.append(fid)
                seen.add(fid)
        return _store_order(ordered)

    def _clip_ordered_fixtures_for_fade(self, clip: Clip, universe: int, u_fixtures: list,
                                        track=None, fixture_defs_by_id: Optional[dict] = None,
                                        proj=None) -> tuple[list, list[tuple[int, int]]]:
        """Return only fixtures that belong to this clip, in the clip's base order.

        Directional fade must operate on the selected/recorded device set. Using every
        fixture in the universe changes group sizes and can turn a two-group sequence
        into extra visual wings.
        """
        universe = int(universe)
        cache_key = (
            self._clip_cache_id(clip),
            "ordered_fixtures",
            universe,
            self._clip_static_signature(clip),
            self._fixture_list_signature(list(u_fixtures or [])),
            self._fixture_defs_signature(fixture_defs_by_id),
            self._track_filter_signature_for_universe(track, universe),
        )
        cached = self._clip_fade_geometry_cache.get(cache_key)
        if cached is not None:
            return list(cached[0]), list(cached[1])

        raw_base_order = self._clip_base_order_fixture_ids(
            clip, track=track, proj=proj, fixture_defs_by_id=fixture_defs_by_id)
        rank = {fid: idx for idx, fid in enumerate(raw_base_order)}
        ordered = sorted(
            list(u_fixtures or []),
            key=lambda fi: (
                rank.get(str(getattr(fi, "id", "")), 10**9),
                int(getattr(fi, "start_channel", 1) or 1),
            ),
        )
        if not ordered:
            return [], []

        if rank:
            ordered = [fi for fi in ordered if str(getattr(fi, "id", "")) in rank]
            if not ordered:
                return [], []
            spans = [
                self._fixture_channel_span(fi, fixture_defs_by_id, ordered, i)
                for i, fi in enumerate(ordered)
            ]
            self._store_clip_fade_geometry_cache(cache_key, (tuple(ordered), tuple(spans)))
            return ordered, spans

        active_mask = self._clip_channel_active_mask(clip, universe)
        track_mask = self._track_filter_mask_for_universe(track, universe, proj=proj)
        use_mask = active_mask.copy()
        if track_mask is not None:
            use_mask &= track_mask

        if not (use_mask).any() or np.all(use_mask):
            spans = [
                self._fixture_channel_span(fi, fixture_defs_by_id, ordered, i)
                for i, fi in enumerate(ordered)
            ]
            self._store_clip_fade_geometry_cache(cache_key, (tuple(ordered), tuple(spans)))
            return ordered, spans

        filtered: list = []
        spans: list[tuple[int, int]] = []
        for i, fi in enumerate(ordered):
            start, end = self._fixture_channel_span(fi, fixture_defs_by_id, ordered, i)
            if 0 <= start < end <= 512 and (use_mask[start:end]).any():
                filtered.append(fi)
                spans.append((start, end))
        if filtered:
            self._store_clip_fade_geometry_cache(cache_key, (tuple(filtered), tuple(spans)))
            return filtered, spans
        spans = [
            self._fixture_channel_span(fi, fixture_defs_by_id, ordered, i)
            for i, fi in enumerate(ordered)
        ]
        self._store_clip_fade_geometry_cache(cache_key, (tuple(ordered), tuple(spans)))
        return ordered, spans

    def _clip_global_fixture_order_for_fade(self, clip: Clip, track=None, proj=None) -> tuple[list[str], dict[str, int]]:
        if proj is None:
            proj = _project_of(self)
        clip_cache_id = self._clip_cache_id(clip)
        track_cache_id = str(getattr(track, "id", "") or id(track) if track is not None else "")
        quick_key = (clip_cache_id, "global_order_quick", track_cache_id)
        cached = self._clip_fade_geometry_cache.get(quick_key)
        if cached is not None:
            ordered_ids = list(cached[0])
            return ordered_ids, dict(cached[1])
        all_fixtures = list(getattr(proj, "fixtures", []) or []) if proj is not None else []
        frame_universes = self._clip_frame_universes(clip)
        if frame_universes:
            all_fixtures = [
                fi for fi in all_fixtures
                if int(getattr(fi, "universe", 1) or 1) in frame_universes
            ]
        fixture_by_id = {str(getattr(fi, "id", "")): fi for fi in all_fixtures if getattr(fi, "id", None)}
        fixture_defs_by_id = {
            str(getattr(fd, "id", "")): fd
            for fd in (getattr(proj, "fixture_defs", []) or [])
            if getattr(fd, "id", None)
        } if proj is not None else {}
        relevant_universes = frame_universes or {
            int(getattr(fi, "universe", 1) or 1) for fi in all_fixtures
        }
        cache_key = (
            clip_cache_id,
            "global_order",
            self._clip_static_signature(clip),
            self._fixture_list_signature(all_fixtures),
            self._fixture_defs_signature(fixture_defs_by_id),
            tuple(
                (int(u), self._track_filter_signature_for_universe(track, int(u)))
                for u in sorted(relevant_universes)
            ),
        )
        cached = self._clip_fade_geometry_cache.get(cache_key)
        if cached is not None:
            ordered_ids = list(cached[0])
            self._store_clip_fade_geometry_cache(quick_key, cached)
            return ordered_ids, dict(cached[1])

        raw_base_order = self._clip_base_order_fixture_ids(
            clip,
            track=track,
            proj=proj,
            all_fixtures=all_fixtures,
            fixture_defs_by_id=fixture_defs_by_id,
        )

        frame_universes = self._clip_frame_universes(clip)

        def _clip_uses_fixture(fi) -> bool:
            try:
                universe = int(getattr(fi, "universe", 1) or 1)
                if frame_universes and universe not in frame_universes:
                    return False
                active = self._clip_channel_active_mask(clip, universe)
                universe_fixtures = [
                    f for f in all_fixtures
                    if int(getattr(f, "universe", 1) or 1) == universe
                ]
                universe_fixtures.sort(key=lambda f: int(getattr(f, "start_channel", 1) or 1))
                try:
                    fixture_index = universe_fixtures.index(fi)
                except ValueError:
                    fixture_index = -1
                start, end = self._fixture_channel_span(fi, fixture_defs_by_id, universe_fixtures, fixture_index)
                return 0 <= start < end <= 512 and bool((active[start:end]).any())
            except Exception:
                return False

        ordered_ids: list[str] = []
        seen: set[str] = set()

        def _fixture_natural_key(fi):
            return (
                _natural_number(getattr(fi, "name", "")),
                _natural_number(getattr(fi, "id", "")),
                int(getattr(fi, "universe", 1) or 1),
                int(getattr(fi, "start_channel", 1) or 1),
            )

        if raw_base_order:
            for fid in raw_base_order:
                if fid in fixture_by_id and fid not in seen:
                    ordered_ids.append(fid)
                    seen.add(fid)
        else:
            clip_fixtures = [fi for fi in all_fixtures if _clip_uses_fixture(fi)]
            if not clip_fixtures:
                clip_fixtures = all_fixtures
            for fi in sorted(
                clip_fixtures,
                key=_fixture_natural_key,
            ):
                fid = str(getattr(fi, "id", ""))
                if fid and fid not in seen:
                    ordered_ids.append(fid)
                    seen.add(fid)
        index = {fid: idx for idx, fid in enumerate(ordered_ids)}
        cached_value = (tuple(ordered_ids), tuple(index.items()))
        self._store_clip_fade_geometry_cache(cache_key, cached_value)
        self._store_clip_fade_geometry_cache(quick_key, cached_value)
        return ordered_ids, index

    def _fixture_direction_positions_for_fade(self, clip: Clip, local_fixtures: list,
                                              direction: str, track=None, proj=None) -> np.ndarray:
        ordered_ids, global_index = self._clip_global_fixture_order_for_fade(clip, track=track, proj=proj)
        total = len(ordered_ids)
        if total <= 0:
            return self._compute_direction_positions(direction, len(local_fixtures))
        global_positions = self._direction_positions_for_fixture_ids(
            ordered_ids,
            direction,
            proj=proj,
            clip=clip,
            scope_ids=ordered_ids,
        )
        positions = np.zeros(len(local_fixtures), dtype=np.float32)
        for i, fi in enumerate(local_fixtures):
            idx = global_index.get(str(getattr(fi, "id", "")))
            if idx is None or idx < 0 or idx >= len(global_positions):
                positions[i] = 1.0
            else:
                positions[i] = float(global_positions[idx])
        return positions

    def _sequential_group_indices_for_fixtures(self, clip: Clip, local_fixtures: list,
                                               group_count: int, track=None, proj=None,
                                               direction: str = "ltr") -> np.ndarray:
        try:
            group_idx, _group_pos, _group_sizes = self._sequential_fixture_group_data(
                clip, local_fixtures, direction, group_count, track=track, proj=proj)
            if len(group_idx) == len(local_fixtures):
                return group_idx.astype(np.int32)
        except Exception:
            pass
        ordered_ids, global_index = self._clip_global_fixture_order_for_fade(clip, track=track, proj=proj)
        total = len(ordered_ids)
        if total <= 0:
            return self._sequential_group_indices_for_positions(np.arange(len(local_fixtures)), group_count)
        group_count = max(1, min(int(group_count or 1), max(1, total)))
        max_step_count = int(np.ceil(total / float(group_count)))
        group_idx = np.zeros(len(local_fixtures), dtype=np.int32)
        for i, fi in enumerate(local_fixtures):
            global_i = global_index.get(str(getattr(fi, "id", "")))
            if global_i is None:
                return self._sequential_group_indices_for_positions(np.arange(len(local_fixtures)), group_count)
            group_idx[i] = min(group_count - 1, int(global_i) // max(1, max_step_count))
        return group_idx

    def _topview_circle_shapes_for_ids(self, ids: list[str], proj=None) -> list[dict]:
        if proj is None:
            proj = _project_of(self)
        state = getattr(proj, "topview_state", {}) if proj is not None else {}
        if not isinstance(state, dict):
            return []
        wanted = {str(fid) for fid in (ids or []) if str(fid)}
        pages = state.get("pages", [])
        if not isinstance(pages, list):
            return []
        shapes: list[dict] = []
        for page in pages:
            if not isinstance(page, dict):
                continue
            page_ids = self._topview_page_fixture_ids(page)
            if wanted and not (page_ids & wanted):
                continue
            for shape in list(page.get("cad_shapes", []) or []) + list(page.get("pattern_matrices", []) or []):
                if not isinstance(shape, dict):
                    continue
                typ = str(shape.get("type", "") or "")
                if typ not in ("circle", "ellipse"):
                    continue
                try:
                    rx = abs(float(shape.get("rx", 80.0) or 80.0))
                    ry = abs(float(shape.get("ry", rx) or rx))
                    if rx < 1.0 or ry < 1.0:
                        continue
                    shapes.append({
                        "id": str(shape.get("id", "") or f"shape-{len(shapes)}"),
                        "type": typ,
                        "cx": float(shape.get("cx", 0.0) or 0.0),
                        "cy": float(shape.get("cy", 0.0) or 0.0),
                        "rx": rx,
                        "ry": ry,
                        "angle": float(shape.get("angle", 0.0) or 0.0) if typ == "ellipse" else 0.0,
                    })
                except Exception:
                    continue
        return shapes

    @staticmethod
    def _topview_page_fixture_ids(page: dict) -> set[str]:
        if not isinstance(page, dict):
            return set()
        if "visible_fixture_ids" in page:
            raw_visible = page.get("visible_fixture_ids", []) or []
            if isinstance(raw_visible, (list, tuple, set)):
                return {str(fid) for fid in raw_visible if str(fid)}
            return set()
        raw_fixtures = page.get("fixtures", {}) or {}
        if isinstance(raw_fixtures, dict):
            return {str(fid) for fid in raw_fixtures.keys() if str(fid)}
        return set()

    @staticmethod
    def _rotate_xy_for_order(x: float, y: float, angle_deg: float) -> tuple[float, float]:
        rad = math.radians(float(angle_deg or 0.0))
        cos_a, sin_a = math.cos(rad), math.sin(rad)
        return x * cos_a - y * sin_a, x * sin_a + y * cos_a

    def _circle_shape_metric_for_fixture(self, shape: dict, pos: tuple[float, float]) -> tuple[float, float]:
        cx, cy = float(shape["cx"]), float(shape["cy"])
        rx, ry = max(1.0, float(shape["rx"])), max(1.0, float(shape["ry"]))
        dx, dy = float(pos[0]) - cx, float(pos[1]) - cy
        angle = float(shape.get("angle", 0.0) or 0.0)
        if abs(angle) > 0.001:
            dx, dy = self._rotate_xy_for_order(dx, dy, -angle)
        norm = math.hypot(dx / rx, dy / ry)
        edge_error = abs(norm - 1.0) * ((rx + ry) * 0.5)
        theta = math.atan2(dy / ry, dx / rx) % math.tau
        return edge_error, theta

    def _topview_fixture_positions_for_ids(
        self,
        ordered_ids: list[str],
        proj=None,
        clip: Optional[Clip] = None,
        use_clip_snapshot: bool = True,
    ) -> dict[str, tuple[float, float]]:
        ordered_ids = [str(fid) for fid in (ordered_ids or []) if str(fid)]
        if not ordered_ids:
            return {}
        if use_clip_snapshot and clip is not None:
            raw_snapshot = getattr(clip, "base_fixture_positions", {}) or {}
            if isinstance(raw_snapshot, dict):
                snapshot: dict[str, tuple[float, float]] = {}
                for fid in ordered_ids:
                    raw = raw_snapshot.get(str(fid))
                    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                        continue
                    try:
                        snapshot[str(fid)] = (float(raw[0]), float(raw[1]))
                    except Exception:
                        continue
                if snapshot and all(fid in snapshot for fid in ordered_ids):
                    return snapshot
        if proj is None:
            proj = _project_of(self)
        if proj is None:
            return {}
        fixture_by_id = {
            str(getattr(fi, "id", "")): fi
            for fi in (getattr(proj, "fixtures", []) or [])
            if str(getattr(fi, "id", ""))
        }
        positions: dict[str, tuple[float, float]] = {}
        for fid in ordered_ids:
            fi = fixture_by_id.get(fid)
            if fi is None:
                continue
            try:
                positions[fid] = (
                    float(getattr(fi, "pos_x", 0.0) or 0.0),
                    float(getattr(fi, "pos_y", 0.0) or 0.0),
                )
            except Exception:
                positions[fid] = (0.0, 0.0)

        state = getattr(proj, "topview_state", {}) or {}
        if isinstance(state, dict):
            pages = list(state.get("pages", []) or [])
            try:
                active_page = int(state.get("active_page", 0) or 0)
            except Exception:
                active_page = 0
            ordered_pages = [page for idx, page in enumerate(pages) if idx != active_page]
            if 0 <= active_page < len(pages):
                ordered_pages.append(pages[active_page])
            for page in ordered_pages:
                if not isinstance(page, dict):
                    continue
                page_ids = self._topview_page_fixture_ids(page)
                if not page_ids:
                    continue
                for fid, raw in (page.get("fixtures", {}) or {}).items():
                    sfid = str(fid)
                    if sfid not in positions or sfid not in page_ids or not isinstance(raw, dict):
                        continue
                    try:
                        positions[sfid] = (
                            float(raw.get("x", raw.get("pos_x", positions[sfid][0])) or 0.0),
                            float(raw.get("y", raw.get("pos_y", positions[sfid][1])) or 0.0),
                        )
                    except Exception:
                        pass
        return {fid: positions[fid] for fid in ordered_ids if fid in positions}

    @staticmethod
    def _clip_has_complete_spatial_snapshot(clip: Optional[Clip], ordered_ids: list[str]) -> bool:
        if clip is None:
            return False
        raw_snapshot = getattr(clip, "base_fixture_positions", {}) or {}
        if not isinstance(raw_snapshot, dict):
            return False
        for fid in [str(value) for value in (ordered_ids or []) if str(value)]:
            raw = raw_snapshot.get(fid)
            if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                return False
            try:
                float(raw[0])
                float(raw[1])
            except Exception:
                return False
        return bool(ordered_ids)

    def _fixture_position_signature(self, ordered_ids: list[str], proj=None,
                                    clip: Optional[Clip] = None) -> tuple:
        ordered_ids = [str(fid) for fid in (ordered_ids or []) if str(fid)]
        if clip is not None:
            raw_snapshot = getattr(clip, "base_fixture_positions", {}) or {}
            if isinstance(raw_snapshot, dict) and ordered_ids:
                cache_key = (
                    self._clip_cache_id(clip),
                    "clip_pos_sig",
                    tuple(ordered_ids),
                    len(raw_snapshot),
                )
                cached = self._clip_fade_geometry_cache.get(cache_key)
                if cached is not None:
                    return cached
                signature = []
                complete = True
                for fid in ordered_ids:
                    raw = raw_snapshot.get(fid)
                    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
                        complete = False
                        break
                    try:
                        signature.append((str(fid), round(float(raw[0]), 3), round(float(raw[1]), 3)))
                    except Exception:
                        complete = False
                        break
                if complete:
                    result = tuple(signature)
                    self._store_clip_fade_geometry_cache(cache_key, result)
                    return result
        topview_state = getattr(proj, "topview_state", {}) if proj is not None else {}
        topview_identity = id(topview_state)
        cache_key = (
            "__topview_pos_sig__",
            topview_identity,
            tuple(ordered_ids),
            len(getattr(proj, "fixtures", []) or []) if proj is not None else 0,
        )
        cached = self._clip_fade_geometry_cache.get(cache_key)
        if cached is not None:
            return cached
        positions = self._topview_fixture_positions_for_ids(ordered_ids, proj=proj, use_clip_snapshot=False)
        signature = tuple(
            (str(fid), round(float(positions[fid][0]), 3), round(float(positions[fid][1]), 3))
            for fid in (ordered_ids or [])
            if str(fid) in positions
        )
        self._store_clip_fade_geometry_cache(cache_key, signature)
        return signature

    def _fit_circle_reference_for_ids(self, ids: list[str],
                                      positions: dict[str, tuple[float, float]],
                                      proj=None,
                                      prefer_shapes: bool = True,
                                      allow_bbox_fallback: bool = True) -> Optional[dict]:
        ids = [str(fid) for fid in (ids or []) if str(fid) in positions]
        if len(ids) < 3:
            return None
        if prefer_shapes:
            best_shape: Optional[dict] = None
            best_score: tuple[int, float] = (-1, -999999999.0)
            for shape in self._topview_circle_shapes_for_ids(ids, proj=proj):
                errors: list[float] = []
                radius = (float(shape["rx"]) + float(shape["ry"])) * 0.5
                tolerance = max(24.0, radius * 0.22)
                for fid in ids:
                    try:
                        error, _angle = self._circle_shape_metric_for_fixture(shape, positions[fid])
                    except Exception:
                        continue
                    if error <= tolerance:
                        errors.append(float(error))
                if len(errors) < max(3, int(math.ceil(len(ids) * 0.45))):
                    continue
                avg_error = sum(errors) / max(1, len(errors))
                score = (len(errors), -avg_error)
                if score > best_score:
                    best_score = score
                    best_shape = dict(shape)
            if best_shape is not None:
                return best_shape

        pts = [positions[fid] for fid in ids]
        sx = sy = sx2 = sy2 = sxy = sx3 = sy3 = sx2y = sxy2 = 0.0
        for x, y in pts:
            x = float(x)
            y = float(y)
            x2, y2 = x * x, y * y
            sx += x
            sy += y
            sx2 += x2
            sy2 += y2
            sxy += x * y
            sx3 += x2 * x
            sy3 += y2 * y
            sx2y += x2 * y
            sxy2 += x * y2
        n = float(len(pts))
        matrix = [
            [sx2, sxy, sx, -(sx3 + sxy2)],
            [sxy, sy2, sy, -(sx2y + sy3)],
            [sx, sy, n, -(sx2 + sy2)],
        ]
        try:
            for col in range(3):
                pivot = max(range(col, 3), key=lambda r: abs(matrix[r][col]))
                if abs(matrix[pivot][col]) < 1e-9:
                    raise ValueError("singular circle fit")
                if pivot != col:
                    matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
                div = matrix[col][col]
                for j in range(col, 4):
                    matrix[col][j] /= div
                for r in range(3):
                    if r == col:
                        continue
                    factor = matrix[r][col]
                    if abs(factor) <= 1e-12:
                        continue
                    for j in range(col, 4):
                        matrix[r][j] -= factor * matrix[col][j]
            d, e, f = matrix[0][3], matrix[1][3], matrix[2][3]
            cx, cy = -d / 2.0, -e / 2.0
            r2 = cx * cx + cy * cy - f
            if r2 <= 1.0:
                raise ValueError("invalid circle fit")
            radius = math.sqrt(r2)
            errors = [abs(math.hypot(float(x) - cx, float(y) - cy) - radius) for x, y in pts]
            rms = math.sqrt(sum(err * err for err in errors) / max(1, len(errors)))
            if rms > max(24.0, radius * 0.22):
                raise ValueError("poor circle fit")
            return {"type": "circle", "cx": cx, "cy": cy, "rx": radius, "ry": radius, "angle": 0.0}
        except Exception:
            if not allow_bbox_fallback:
                return None
            xs = [float(x) for x, _y in pts]
            ys = [float(y) for _x, y in pts]
            span_x = max(xs) - min(xs)
            span_y = max(ys) - min(ys)
            return {
                "type": "ellipse",
                "cx": (min(xs) + max(xs)) * 0.5,
                "cy": (min(ys) + max(ys)) * 0.5,
                "rx": max(1.0, span_x * 0.5),
                "ry": max(1.0, span_y * 0.5),
                "angle": 0.0,
            }

    def _circle_direction_positions_for_fixture_ids(self, ids: list[str], direction: str,
                                                    proj=None, clip: Optional[Clip] = None,
                                                    scope_ids: Optional[list[str]] = None,
                                                    start_theta: Optional[float] = None) -> Optional[np.ndarray]:
        direction = self._normalize_clip_direction(direction)
        if direction not in ("cw", "ccw"):
            return None
        ids = [str(fid) for fid in (ids or []) if str(fid)]
        if len(ids) <= 1:
            return np.array([0.5], dtype=np.float32) if ids else np.zeros(0, dtype=np.float32)
        scope_ids = [str(fid) for fid in (scope_ids or ids) if str(fid)]
        all_ids = list(dict.fromkeys(scope_ids + ids))
        positions = self._topview_fixture_positions_for_ids(
            all_ids,
            proj=proj,
            clip=clip,
        )
        if any(fid not in positions for fid in ids):
            return None
        scope_with_pos = [fid for fid in scope_ids if fid in positions] or ids
        scope_coords = [
            (float(positions[fid][0]), float(positions[fid][1]))
            for fid in scope_with_pos
        ]
        if self._matrix_like_points(scope_coords):
            return None
        reference = self._fit_circle_reference_for_ids(
            scope_with_pos if len(scope_with_pos) >= 3 else ids,
            positions,
            proj=proj,
            prefer_shapes=(scope_ids == ids and not self._clip_has_complete_spatial_snapshot(clip, all_ids)),
            allow_bbox_fallback=False,
        )
        if reference is None:
            return None

        raw_angles: list[float] = []
        for fid in ids:
            try:
                _error, theta = self._circle_shape_metric_for_fixture(reference, positions[fid])
            except Exception:
                return None
            raw_angles.append(float(theta))
        # R11(2026-08-13): 多段(多圆)时调用方传入统一基准角(全局基准序首台的
        # 圆坐标角) → 所有圆从同一世界方向起扫, 跨圆同相; None = 单段/global
        # 旧行为(本段首台锚定), 逐字节不变。
        start_theta = raw_angles[0] if start_theta is None else float(start_theta) % math.tau
        if direction == "cw":
            deltas = np.array([(theta - start_theta) % math.tau for theta in raw_angles], dtype=np.float64)
        else:
            deltas = np.array([(start_theta - theta) % math.tau for theta in raw_angles], dtype=np.float64)
        order = np.lexsort((np.arange(len(ids), dtype=np.int64), deltas))
        pos = np.zeros(len(ids), dtype=np.float32)
        denom = float(max(1, len(ids) - 1))
        for rank, idx in enumerate(order):
            pos[int(idx)] = float(rank) / denom
        return pos

    @staticmethod
    def _axis_cluster_count(values: list[float]) -> int:
        vals = sorted(float(v) for v in values)
        if not vals:
            return 0
        span = max(vals) - min(vals)
        tol = max(1e-6, span * 0.025)
        count = 1
        prev = vals[0]
        for value in vals[1:]:
            if abs(value - prev) > tol:
                count += 1
                prev = value
        return count

    @classmethod
    def _matrix_like_points(cls, coords: list[tuple[float, float]]) -> bool:
        if len(coords) < 4:
            return False
        xs = [float(x) for x, _y in coords]
        ys = [float(y) for _x, y in coords]
        x_count = cls._axis_cluster_count(xs)
        y_count = cls._axis_cluster_count(ys)
        if x_count < 3 or y_count < 3:
            return False
        return (len(coords) / float(max(1, x_count * y_count))) >= 0.80

    @staticmethod
    def _matrix_ring_value(x: float, y: float, cx: float, cy: float,
                           span_x: float, span_y: float) -> float:
        nx = abs(float(x) - float(cx)) / max(span_x * 0.5, 1e-6)
        ny = abs(float(y) - float(cy)) / max(span_y * 0.5, 1e-6)
        return max(nx, ny)

    def _angle_direction_positions_from_virtual_coords(self, ids: list[str], direction: str,
                                                       proj=None,
                                                       clip: Optional[Clip] = None,
                                                       scope_ids: Optional[list[str]] = None) -> Optional[np.ndarray]:
        direction = self._normalize_clip_direction(direction)
        if direction not in ("cw", "ccw"):
            return None
        ids = [str(fid) for fid in (ids or []) if str(fid)]
        if len(ids) <= 1:
            return np.array([0.5], dtype=np.float32) if ids else np.zeros(0, dtype=np.float32)
        scope_ids = [str(fid) for fid in (scope_ids or ids) if str(fid)]
        all_ids = list(dict.fromkeys(scope_ids + ids))
        positions = self._topview_fixture_positions_for_ids(
            all_ids,
            proj=proj,
            clip=clip,
        )
        if any(fid not in positions for fid in ids):
            return None
        scope_with_pos = [fid for fid in scope_ids if fid in positions] or ids
        if len(scope_with_pos) < 2:
            return None
        cx = sum(float(positions[fid][0]) for fid in scope_with_pos) / float(len(scope_with_pos))
        cy = sum(float(positions[fid][1]) for fid in scope_with_pos) / float(len(scope_with_pos))
        if all(
            abs(float(positions[fid][0]) - cx) <= 1e-6
            and abs(float(positions[fid][1]) - cy) <= 1e-6
            for fid in ids
        ):
            return None
        raw_angles = [
            math.atan2(float(positions[fid][1]) - cy, float(positions[fid][0]) - cx) % math.tau
            for fid in ids
        ]
        start_theta = raw_angles[0]
        if direction == "cw":
            deltas = np.array([(theta - start_theta) % math.tau for theta in raw_angles], dtype=np.float64)
        else:
            deltas = np.array([(start_theta - theta) % math.tau for theta in raw_angles], dtype=np.float64)
        radii = np.array([
            math.hypot(float(positions[fid][0]) - cx, float(positions[fid][1]) - cy)
            for fid in ids
        ], dtype=np.float64)
        coords = [(float(positions[fid][0]), float(positions[fid][1])) for fid in ids]
        if self._matrix_like_points(coords):
            xs = [x for x, _y in coords]
            ys = [y for _x, y in coords]
            span_x = max(xs) - min(xs) if xs else 0.0
            span_y = max(ys) - min(ys) if ys else 0.0
            rings = np.array([
                self._matrix_ring_value(x, y, cx, cy, span_x, span_y)
                for x, y in coords
            ], dtype=np.float64)
            order = np.lexsort((np.arange(len(ids), dtype=np.int64), deltas, -rings))
        else:
            order = np.lexsort((np.arange(len(ids), dtype=np.int64), -radii, deltas))
        pos = np.zeros(len(ids), dtype=np.float32)
        denom = float(max(1, len(ids) - 1))
        for rank, idx in enumerate(order):
            pos[int(idx)] = float(rank) / denom
        return pos

    def _centroid_angle_direction_positions_for_fixture_ids(self, ids: list[str], direction: str,
                                                           proj=None,
                                                           clip: Optional[Clip] = None,
                                                           scope_ids: Optional[list[str]] = None) -> Optional[np.ndarray]:
        direction = self._normalize_clip_direction(direction)
        if direction not in ("cw", "ccw"):
            return None
        ids = [str(fid) for fid in (ids or []) if str(fid)]
        if len(ids) <= 1:
            return np.array([0.5], dtype=np.float32) if ids else np.zeros(0, dtype=np.float32)
        scope_ids = [str(fid) for fid in (scope_ids or ids) if str(fid)]
        all_ids = list(dict.fromkeys(scope_ids + ids))
        positions = self._topview_fixture_positions_for_ids(
            all_ids,
            proj=proj,
            clip=clip,
        )
        if any(fid not in positions for fid in ids):
            return None
        scope_with_pos = [fid for fid in scope_ids if fid in positions] or ids
        if len(scope_with_pos) < 2:
            return None
        cx = sum(float(positions[fid][0]) for fid in scope_with_pos) / float(len(scope_with_pos))
        cy = sum(float(positions[fid][1]) for fid in scope_with_pos) / float(len(scope_with_pos))
        raw_angles = [math.atan2(float(positions[fid][1]) - cy, float(positions[fid][0]) - cx) % math.tau for fid in ids]
        start_theta = raw_angles[0]
        if direction == "cw":
            deltas = np.array([(theta - start_theta) % math.tau for theta in raw_angles], dtype=np.float64)
        else:
            deltas = np.array([(start_theta - theta) % math.tau for theta in raw_angles], dtype=np.float64)
        radii = np.array([
            math.hypot(float(positions[fid][0]) - cx, float(positions[fid][1]) - cy)
            for fid in ids
        ], dtype=np.float64)
        coords = [(float(positions[fid][0]), float(positions[fid][1])) for fid in ids]
        if self._matrix_like_points(coords):
            xs = [x for x, _y in coords]
            ys = [y for _x, y in coords]
            span_x = max(xs) - min(xs) if xs else 0.0
            span_y = max(ys) - min(ys) if ys else 0.0
            rings = np.array([
                self._matrix_ring_value(x, y, cx, cy, span_x, span_y)
                for x, y in coords
            ], dtype=np.float64)
            order = np.lexsort((np.arange(len(ids), dtype=np.int64), deltas, -rings))
        else:
            order = np.lexsort((np.arange(len(ids), dtype=np.int64), -radii, deltas))
        pos = np.zeros(len(ids), dtype=np.float32)
        denom = float(max(1, len(ids) - 1))
        for rank, idx in enumerate(order):
            pos[int(idx)] = float(rank) / denom
        return pos

    def _circle_slot_order_for_virtual_positions(self, base_order: list[str],
                                                 actual_positions: dict[str, tuple[float, float]],
                                                 proj=None,
                                                 prefer_shapes: bool = True) -> list[str]:
        base_order = [str(fid) for fid in (base_order or []) if str(fid) in actual_positions]
        if len(base_order) <= 1:
            return list(base_order)
        coords = [(float(actual_positions[fid][0]), float(actual_positions[fid][1])) for fid in base_order]
        if self._matrix_like_points(coords):
            # Matrix fixtures are rectangular slots, not circular slots.  Keep
            # the hidden base-order replacement on a row/column grid, then let
            # the requested advanced direction (cw/ccw/ltr/...) read that grid.
            rank = {fid: idx for idx, fid in enumerate(base_order)}
            return sorted(
                base_order,
                key=lambda fid: (
                    float(actual_positions[fid][1]),
                    float(actual_positions[fid][0]),
                    rank.get(fid, 10**9),
                ),
            )
        reference = self._fit_circle_reference_for_ids(
            base_order,
            actual_positions,
            proj=proj,
            prefer_shapes=prefer_shapes,
            allow_bbox_fallback=False,
        )
        if reference is None:
            cx = sum(float(actual_positions[fid][0]) for fid in base_order) / float(len(base_order))
            cy = sum(float(actual_positions[fid][1]) for fid in base_order) / float(len(base_order))
            if all(
                abs(float(actual_positions[fid][0]) - cx) <= 1e-6
                and abs(float(actual_positions[fid][1]) - cy) <= 1e-6
                for fid in base_order
            ):
                return list(base_order)
            start_theta = math.atan2(
                float(actual_positions[base_order[0]][1]) - cy,
                float(actual_positions[base_order[0]][0]) - cx,
            ) % math.tau
            coords = [(float(actual_positions[fid][0]), float(actual_positions[fid][1])) for fid in base_order]
            matrix_like = self._matrix_like_points(coords)
            xs = [x for x, _y in coords]
            ys = [y for _x, y in coords]
            span_x = max(xs) - min(xs) if xs else 0.0
            span_y = max(ys) - min(ys) if ys else 0.0

            def _fallback_key(fid: str):
                x = float(actual_positions[fid][0])
                y = float(actual_positions[fid][1])
                delta = (math.atan2(y - cy, x - cx) - start_theta) % math.tau
                if matrix_like:
                    ring = self._matrix_ring_value(x, y, cx, cy, span_x, span_y)
                    return (-ring, delta, base_order.index(fid))
                return (
                    delta,
                    -math.hypot(x - cx, y - cy),
                    base_order.index(fid),
                )

            return sorted(
                base_order,
                key=_fallback_key,
            )
        try:
            _err, start_theta = self._circle_shape_metric_for_fixture(reference, actual_positions[base_order[0]])
        except Exception:
            start_theta = (-math.pi / 2.0) % math.tau

        def _key(fid: str) -> tuple[float, float, int]:
            try:
                error, theta = self._circle_shape_metric_for_fixture(reference, actual_positions[fid])
                delta = (float(theta) - float(start_theta)) % math.tau
                return (delta, float(error), base_order.index(fid))
            except Exception:
                return (0.0, 999999999.0, base_order.index(fid))

        return sorted(base_order, key=_key)

    def _virtual_topview_fixture_positions_for_ids(self, ordered_ids: list[str],
                                                   proj=None,
                                                   clip: Optional[Clip] = None,
                                                   scope_ids: Optional[list[str]] = None,
                                                   slot_mode: str = "linear") -> dict[str, tuple[float, float]]:
        ordered_ids = [str(fid) for fid in (ordered_ids or []) if str(fid)]
        if not ordered_ids:
            return {}
        scope_ids = [str(fid) for fid in (scope_ids or ordered_ids) if str(fid)]
        all_ids = list(dict.fromkeys(scope_ids + ordered_ids))
        actual = self._topview_fixture_positions_for_ids(all_ids, proj=proj, clip=clip)
        if any(fid not in actual for fid in ordered_ids):
            return {}
        # ★ 2026-08-11 #18：跳过编号排序层——设备直接用真实坐标参与方向排序
        #   （纯空间序），不做「身份按编号序分配到坐标槽」的虚拟层。默认 False
        #   = 原逻辑（先编号后坐标系）逐字节不变。
        if bool(getattr(clip, "fade_group_skip_base_order", False)):
            return {fid: actual[fid] for fid in ordered_ids if fid in actual}
        explicit_base_order = self._unique_fixture_id_list(
            getattr(clip, "base_order_fixture_ids", []) or []
        ) if clip is not None else []
        if clip is not None and not explicit_base_order:
            try:
                explicit_base_order = self._clip_base_order_fixture_ids(clip, proj=proj)
            except Exception:
                explicit_base_order = []
            if explicit_base_order:
                explicit_base_order = [fid for fid in explicit_base_order if fid in actual]
        if clip is not None and not explicit_base_order:
            return {fid: actual[fid] for fid in ordered_ids if fid in actual}

        base_order: list[str] = []
        for fid in scope_ids + ordered_ids:
            sfid = str(fid)
            if sfid in actual and sfid not in base_order:
                base_order.append(sfid)
        if len(base_order) <= 1:
            return {fid: actual[fid] for fid in ordered_ids if fid in actual}

        base_rank = {fid: idx for idx, fid in enumerate(base_order)}
        if str(slot_mode or "").lower() == "circle":
            slot_order = self._circle_slot_order_for_virtual_positions(
                base_order,
                actual,
                proj=proj,
                prefer_shapes=not self._clip_has_complete_spatial_snapshot(clip, all_ids),
            )
        else:
            slot_order = sorted(
                base_order,
                key=lambda fid: (
                    float(actual[fid][0]),
                    float(actual[fid][1]),
                    base_rank.get(fid, 10**9),
                ),
            )
        if len(slot_order) != len(base_order):
            return {fid: actual[fid] for fid in ordered_ids if fid in actual}

        # Hidden coordinate layer:
        # the real TopView coordinate slots stay spatial, but fixture identities
        # are assigned to those slots by the clip/selection base order first.
        virtual_positions = {
            fid: actual[slot_order[idx]]
            for idx, fid in enumerate(base_order)
        }
        return {fid: virtual_positions[fid] for fid in ordered_ids if fid in virtual_positions}

    def _linear_direction_positions_for_fixture_ids(self, ids: list[str], direction: str,
                                                   proj=None,
                                                   clip: Optional[Clip] = None,
                                                   scope_ids: Optional[list[str]] = None) -> Optional[np.ndarray]:
        direction = self._normalize_clip_direction(direction)
        if direction not in ("ltr", "rtl", "ftb", "btf", "ctr", "rtc"):
            return None
        ids = [str(fid) for fid in (ids or []) if str(fid)]
        if len(ids) <= 1:
            return np.array([0.5], dtype=np.float32) if ids else np.zeros(0, dtype=np.float32)
        scope_ids = [str(fid) for fid in (scope_ids or ids) if str(fid)]
        all_ids = list(dict.fromkeys(scope_ids + ids))
        positions = self._virtual_topview_fixture_positions_for_ids(
            all_ids,
            proj=proj,
            clip=clip,
            scope_ids=scope_ids,
            slot_mode="linear",
        )
        if any(fid not in positions for fid in ids):
            return None
        scope_with_pos = [fid for fid in scope_ids if fid in positions] or ids
        if not scope_with_pos:
            return None

        xs = [float(positions[fid][0]) for fid in scope_with_pos]
        ys = [float(positions[fid][1]) for fid in scope_with_pos]
        span_x = max(xs) - min(xs) if xs else 0.0
        span_y = max(ys) - min(ys) if ys else 0.0

        rank_values: list[float]
        if direction in ("ltr", "rtl", "ftb", "btf"):
            use_y_axis = direction in ("ftb", "btf")
            primary_span = span_y if use_y_axis else span_x
            fallback_span = span_x if use_y_axis else span_y
            if primary_span > 1e-6:
                axis_index = 1 if use_y_axis else 0
                axis_values = ys if use_y_axis else xs
                lo = min(axis_values)
                span = primary_span
                rank_values = [(float(positions[fid][axis_index]) - lo) / span for fid in ids]
            elif fallback_span > 1e-6:
                axis_index = 0 if use_y_axis else 1
                axis_values = xs if use_y_axis else ys
                lo = min(axis_values)
                span = fallback_span
                rank_values = [(float(positions[fid][axis_index]) - lo) / span for fid in ids]
            else:
                axis_index = 1 if use_y_axis else 0
                rank_values = [0.5 for _fid in ids]
            if direction in ("rtl", "btf"):
                rank_values = [1.0 - value for value in rank_values]
            tie_axis = 0 if axis_index == 1 else 1
            order = sorted(
                range(len(ids)),
                key=lambda idx: (
                    rank_values[idx],
                    float(positions[ids[idx]][tie_axis]),
                    idx,
                ),
            )
            values = np.zeros(len(ids), dtype=np.float32)
            denom = float(max(1, len(ids) - 1))
            for rank, idx in enumerate(order):
                values[int(idx)] = float(rank) / denom
            return values

        # Center/edge directions use the dominant TopView axis for the current
        # scope, so rotating a line-like layout keeps the order spatial instead
        # of falling back to the recorded fixture selection order.
        use_x_axis = span_x >= span_y
        axis_values = xs if use_x_axis else ys
        span = span_x if use_x_axis else span_y
        if span <= 1e-6:
            return np.full(len(ids), 0.5, dtype=np.float32)
        center = (min(axis_values) + max(axis_values)) * 0.5
        max_dist = max(abs(float(v) - center) for v in axis_values)
        if max_dist <= 1e-6:
            return np.full(len(ids), 0.5, dtype=np.float32)
        rank_values = []
        for fid in ids:
            coord = float(positions[fid][0] if use_x_axis else positions[fid][1])
            dist = abs(coord - center) / max_dist
            rank_values.append(dist if direction == "ctr" else 1.0 - dist)
        order = sorted(range(len(ids)), key=lambda idx: (rank_values[idx], idx))
        values = np.zeros(len(ids), dtype=np.float32)
        denom = float(max(1, len(ids) - 1))
        for rank, idx in enumerate(order):
            values[int(idx)] = float(rank) / denom
        return values

    def _spatial_base_order_indices_for_fixture_ids(self, ids: list[str], proj=None,
                                                    clip: Optional[Clip] = None) -> Optional[list[int]]:
        ids = [str(fid) for fid in (ids or []) if str(fid)]
        if len(ids) <= 1:
            return list(range(len(ids)))
        positions = self._topview_fixture_positions_for_ids(ids, proj=proj, clip=clip)
        if any(fid not in positions for fid in ids):
            return None
        return sorted(
            range(len(ids)),
            key=lambda i: (
                float(positions[ids[i]][0]),
                float(positions[ids[i]][1]),
                ids[i],
            ),
        )

    def _pattern_direction_positions_for_fixture_ids(self, ids: list[str], direction: str,
                                                    proj=None,
                                                    clip: Optional[Clip] = None) -> Optional[np.ndarray]:
        direction = self._normalize_clip_direction(direction)
        if direction in ("none", "", "ltr", "rtl", "ftb", "btf", "ctr", "rtc", "cw", "ccw"):
            return None
        ids = [str(fid) for fid in (ids or []) if str(fid)]
        if len(ids) <= 1:
            return np.array([0.5], dtype=np.float32) if ids else np.zeros(0, dtype=np.float32)
        # ★ R11 B4 2026-08-13: 模式类方向(alt/wave/cascade/zigzag/random…)与线性
        #   方向共用同一参照系: 默认(跳过编号排序关)沿 ids 序(编号序)原样套模式
        #   = 旧行为逐字节不变; 跳过编号排序开 → 先用真实坐标定序再套模式,
        #   与线性方向 skip 分支同坐标系(不再有「线性受控/模式类不受控」矛盾)。
        if bool(getattr(clip, "fade_group_skip_base_order", False)):
            order = self._spatial_base_order_indices_for_fixture_ids(ids, proj=proj, clip=clip)
        else:
            order = list(range(len(ids)))
        if order is None or len(order) != len(ids):
            return None
        ranked_values = self._compute_direction_positions(direction, len(ids))
        values = np.zeros(len(ids), dtype=np.float32)
        for rank, idx in enumerate(order):
            values[int(idx)] = float(ranked_values[int(rank)])
        return values

    def _direction_positions_for_fixture_ids(self, ids: list[str], direction: str,
                                             proj=None, clip: Optional[Clip] = None,
                                             scope_ids: Optional[list[str]] = None,
                                             start_theta: Optional[float] = None) -> np.ndarray:
        direction = self._normalize_clip_direction(direction)
        ids = [str(fid) for fid in (ids or []) if str(fid)]
        scope_ids = [str(fid) for fid in (scope_ids or ids) if str(fid)]
        cache_key = (
            self._clip_cache_id(clip) if clip is not None else "__project__",
            "direction_positions_quick",
            direction,
            tuple(ids),
            tuple(scope_ids),
            # ★ 2026-08-11 #18：skip 标志入缓存键（与 _order_fixture_ids_for_direction
            #   双层缓存同修，否则开/关跳过编号排序命中旧虚拟层结果）
            bool(getattr(clip, "fade_group_skip_base_order", False)),
            # ★ R11 2026-08-13: 统一基准角入缓存键(否则同 ids 无锚/有锚结果串缓存)
            float(start_theta) if start_theta is not None else None,
        )
        cached = self._clip_fade_geometry_cache.get(cache_key)
        if cached is not None:
            return np.asarray(cached, dtype=np.float32).copy()
        if direction in ("cw", "ccw"):
            positions = self._circle_direction_positions_for_fixture_ids(
                ids,
                direction,
                proj=proj,
                clip=clip,
                scope_ids=scope_ids,
                start_theta=start_theta,
            )
            if positions is not None and len(positions) == len(ids):
                self._store_clip_fade_geometry_cache(cache_key, tuple(float(v) for v in positions))
                return positions
            positions = self._angle_direction_positions_from_virtual_coords(
                ids,
                direction,
                proj=proj,
                clip=clip,
                scope_ids=scope_ids,
            )
            if positions is not None and len(positions) == len(ids):
                self._store_clip_fade_geometry_cache(cache_key, tuple(float(v) for v in positions))
                return positions
            positions = self._centroid_angle_direction_positions_for_fixture_ids(
                ids,
                direction,
                proj=proj,
                clip=clip,
                scope_ids=scope_ids,
            )
            if positions is not None and len(positions) == len(ids):
                self._store_clip_fade_geometry_cache(cache_key, tuple(float(v) for v in positions))
                return positions
            if direction == "ccw":
                fallback = self._compute_direction_positions("rtl", len(ids))
            else:
                fallback = np.linspace(0.0, 1.0, len(ids), dtype=np.float32)
            self._store_clip_fade_geometry_cache(cache_key, tuple(float(v) for v in fallback))
            return fallback
        positions = self._linear_direction_positions_for_fixture_ids(
            ids,
            direction,
            proj=proj,
            clip=clip,
            scope_ids=scope_ids,
        )
        if positions is not None and len(positions) == len(ids):
            self._store_clip_fade_geometry_cache(cache_key, tuple(float(v) for v in positions))
            return positions
        # ★ R11 B4 2026-08-13: 模式类方向(alt/wave/cascade/zigzag/random…)
        #   不再直落纯位置 fallback —— 接线原本孤儿的 pattern 路径, 使跳过
        #   编号排序开关对模式类方向同样生效(关=沿 ids 序套模式, 旧行为不变)。
        positions = self._pattern_direction_positions_for_fixture_ids(
            ids, direction, proj=proj, clip=clip)
        if positions is not None and len(positions) == len(ids):
            self._store_clip_fade_geometry_cache(cache_key, tuple(float(v) for v in positions))
            return positions
        fallback = self._compute_direction_positions(direction, len(ids))
        self._store_clip_fade_geometry_cache(cache_key, tuple(float(v) for v in fallback))
        return fallback

    def _order_fixture_ids_for_direction(self, ids: list[str], direction: str,
                                         proj=None, clip: Optional[Clip] = None,
                                         scope_ids: Optional[list[str]] = None,
                                         start_theta: Optional[float] = None) -> list[str]:
        ids = [str(fid) for fid in (ids or []) if str(fid)]
        if len(ids) <= 1:
            return ids
        direction = self._normalize_clip_direction(direction)
        if direction in ("none", ""):
            return ids
        scope_ids = [str(fid) for fid in (scope_ids or ids) if str(fid)]
        all_ids = list(dict.fromkeys(scope_ids + ids))
        cache_key = (
            self._clip_cache_id(clip) if clip is not None else "__project__",
            "direction_order",
            direction,
            tuple(ids),
            tuple(scope_ids),
            # ★ 2026-08-11 #18：skip 标志入缓存键——_clip_cache_id 仅含 id，
            #   缺此键则开/关「跳过编号排序」时排序结果被缓存串掉（白盒探针实证）
            bool(getattr(clip, "fade_group_skip_base_order", False)),
            self._fixture_position_signature(all_ids, proj=proj, clip=clip),
            (
                ()
                if self._clip_has_complete_spatial_snapshot(clip, all_ids)
                else self._topview_shape_signature_for_ids(all_ids, proj=proj)
            ),
            # ★ R11 2026-08-13: 统一基准角入缓存键(同 ids 无锚/有锚结果串缓存)
            float(start_theta) if start_theta is not None else None,
        )
        cached = self._clip_fade_geometry_cache.get(cache_key)
        if cached is not None:
            return list(cached)
        positions = self._direction_positions_for_fixture_ids(
            ids,
            direction,
            proj=proj,
            clip=clip,
            scope_ids=scope_ids,
            start_theta=start_theta,
        )
        order = sorted(
            range(len(ids)),
            key=lambda idx: (
                float(positions[int(idx)]) if int(idx) < len(positions) else 1.0,
                int(idx),
            ),
        )
        ordered = tuple(ids[int(idx)] for idx in order)
        self._store_clip_fade_geometry_cache(cache_key, ordered)
        return list(ordered)

    def _shape_aware_order_segments_for_fade_cached(
        self,
        clip: Optional[Clip],
        ordered_ids: list[str],
        proj=None,
        spatial_scope: Optional[str] = None,
    ) -> list[list[str]]:
        ordered_ids = [str(fid) for fid in (ordered_ids or []) if str(fid)]
        if len(ordered_ids) < 6:
            return []
        scope = str(spatial_scope or "per_shape")
        pos_sig = self._fixture_position_signature(ordered_ids, proj=proj, clip=clip)
        shape_sig = (
            ()
            if self._clip_has_complete_spatial_snapshot(clip, ordered_ids)
            else self._topview_shape_signature_for_ids(ordered_ids, proj=proj)
        )
        cache_key = (
            self._clip_cache_id(clip) if clip is not None else "__project__",
            "shape_segments",
            scope,
            tuple(ordered_ids),
            pos_sig,
            shape_sig,
        )
        cached = self._clip_fade_geometry_cache.get(cache_key)
        if cached is not None:
            return [list(segment) for segment in cached]
        segments = self._shape_aware_order_segments_for_fade(ordered_ids, proj=proj, clip=clip)
        frozen = tuple(tuple(str(fid) for fid in segment) for segment in segments)
        self._store_clip_fade_geometry_cache(cache_key, frozen)
        return [list(segment) for segment in frozen]

    def _shape_aware_order_segments_for_fade(self, ordered_ids: list[str], proj=None,
                                             clip: Optional[Clip] = None) -> list[list[str]]:
        ordered_ids = [str(fid) for fid in (ordered_ids or []) if str(fid)]
        if len(ordered_ids) < 6:
            return []
        if proj is None:
            proj = _project_of(self)
        if proj is None:
            return []
        positions: dict[str, tuple[float, float]] = self._topview_fixture_positions_for_ids(
            ordered_ids,
            proj=proj,
            clip=clip,
        )
        positions = {fid: positions[fid] for fid in ordered_ids if fid in positions}
        if len(positions) < 6:
            return []

        # ★ 单元级簇优先(用户定案 2026-08-02)：单控(per_shape)时 kind=unit 微单元
        #   (N 灯围一中心，导入时创建)的成员按单元成簇，中心=单元中心——每单元各自
        #   小圆 cw/ccw(各自坐标系)，不并入宏圆整体转。单元成员先分配，剩余设备
        #   再走显式 shape / 推断。整体控(global)时不做单元分组。
        # ★ 2026-08-04 气爆包(kind=package, 1 气爆+4 灯)并入同簇语义。
        spatial_scope = self._clip_spatial_scope(clip) if clip is not None else "per_shape"
        unit_clusters: list[dict] = []
        unit_assigned: set[str] = set()
        if spatial_scope == "per_shape" and proj is not None:
            for _ld in (getattr(proj, "logical_devices", None) or []):
                if not isinstance(_ld, dict) or str(_ld.get("kind", "")) not in ("unit", "package"):
                    continue
                _members = [str(m) for m in (_ld.get("member_fixture_ids", []) or [])
                            if str(m) in positions]
                if len(_members) < 3:
                    continue
                # ★ 中心优先取成员坐标均值(与 positions 快照一致)，而非 ld.pos_x/y：
                #   clip 录制后布局改动时旋转中心不漂移（审查 P3-6）
                _cx = sum(float(positions[m][0]) for m in _members) / len(_members)
                _cy = sum(float(positions[m][1]) for m in _members) / len(_members)
                unit_clusters.append({
                    "cx": _cx,
                    "cy": _cy,
                    "items": list(_members),
                })
                unit_assigned.update(_members)

        shapes = self._topview_circle_shapes_for_ids(ordered_ids, proj=proj)
        clusters: list[dict] = [dict(_uc) for _uc in unit_clusters]
        assigned: set[str] = set(unit_assigned)
        if shapes:
            # ★ R11 B5 2026-08-13: 同页多圆(To The Moon 8 圆/嵌套同心圆)时设备
            #   误配到相邻圆的容差收紧 max(16, r*0.15)—— 圆心错→角度错乱。
            #   单形状页维持旧容差 max(24, r*0.22)逐字节不变(零回归)。
            multi_shape = len(shapes) > 1
            cluster_by_shape = [
                {
                    "cx": float(shape["cx"]),
                    "cy": float(shape["cy"]),
                    "items": [],
                    "shape_index": shape_index,
                }
                for shape_index, shape in enumerate(shapes)
            ]
            for fid in ordered_ids:
                if fid not in positions or fid in assigned:
                    continue
                best_idx = -1
                best_error = 999999999.0
                for shape_index, shape in enumerate(shapes):
                    try:
                        error, _angle = self._circle_shape_metric_for_fixture(shape, positions[fid])
                    except Exception:
                        continue
                    if error < best_error:
                        best_idx = shape_index
                        best_error = error
                if best_idx < 0:
                    continue
                shape = shapes[best_idx]
                radius = (float(shape["rx"]) + float(shape["ry"])) * 0.5
                if best_error <= (max(16.0, radius * 0.15) if multi_shape
                                  else max(24.0, radius * 0.22)):
                    cluster_by_shape[best_idx]["items"].append(fid)
                    assigned.add(fid)
            clusters.extend(
                cluster for cluster in cluster_by_shape
                if len(cluster.get("items", [])) >= 3
            )

        if len(clusters) <= 1 and not unit_clusters:
            # 无单元簇时回退推断；单元簇存在(哪怕 1 个)时保留单元分组，
            # 防止单单元+散台场景被整体替换回宏圆吸收（审查 P3-2）
            clusters = self._infer_order_circle_segments(ordered_ids, positions)
        if len(clusters) <= 1:
            return []

        ordered_rank = {fid: idx for idx, fid in enumerate(ordered_ids)}
        for cluster in clusters:
            cluster["items"] = sorted(
                [fid for fid in cluster.get("items", []) if fid in ordered_rank],
                key=lambda fid: ordered_rank[fid],
            )
        clusters = [cluster for cluster in clusters if len(cluster.get("items", [])) >= 2]
        if len(clusters) <= 1:
            return []
        clusters.sort(key=lambda c: min(ordered_rank.get(fid, 10**9) for fid in c.get("items", [])))

        segmented: list[list[str]] = []
        used: set[str] = set()
        cluster_by_first = {
            min(ordered_rank.get(fid, 10**9) for fid in cluster.get("items", [])): cluster
            for cluster in clusters
        }
        index_to_cluster = dict(sorted(cluster_by_first.items()))
        i = 0
        while i < len(ordered_ids):
            cluster = index_to_cluster.get(i)
            if cluster is not None:
                items = [fid for fid in cluster.get("items", []) if fid not in used]
                if items:
                    segmented.append(items)
                    used.update(items)
                i += 1
                continue
            fid = ordered_ids[i]
            if fid not in used:
                segmented.append([fid])
                used.add(fid)
            i += 1
        for fid in ordered_ids:
            if fid not in used:
                segmented.append([fid])
        return segmented

    def _infer_order_circle_segments(self, ordered_ids: list[str],
                                     positions: dict[str, tuple[float, float]]) -> list[dict]:
        ids = [fid for fid in ordered_ids if fid in positions]
        if len(ids) < 10:
            return []
        xs = [positions[fid][0] for fid in ids]
        ys = [positions[fid][1] for fid in ids]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
        tolerance = max(18.0, span * 0.035)
        min_items = 5 if len(ids) < 24 else 6

        def _fit_circle(fid_list: list[str]) -> Optional[tuple[float, float, float, float]]:
            pts = [positions[fid] for fid in fid_list if fid in positions]
            if len(pts) < 3:
                return None
            sx = sy = sx2 = sy2 = sxy = sx3 = sy3 = sx2y = sxy2 = 0.0
            for x, y in pts:
                x = float(x); y = float(y)
                x2, y2 = x * x, y * y
                sx += x; sy += y; sx2 += x2; sy2 += y2; sxy += x * y
                sx3 += x2 * x; sy3 += y2 * y; sx2y += x2 * y; sxy2 += x * y2
            n = float(len(pts))
            matrix = [
                [sx2, sxy, sx, -(sx3 + sxy2)],
                [sxy, sy2, sy, -(sx2y + sy3)],
                [sx, sy, n, -(sx2 + sy2)],
            ]
            for col in range(3):
                pivot = max(range(col, 3), key=lambda r: abs(matrix[r][col]))
                if abs(matrix[pivot][col]) < 1e-9:
                    return None
                if pivot != col:
                    matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
                div = matrix[col][col]
                for j in range(col, 4):
                    matrix[col][j] /= div
                for r in range(3):
                    if r == col:
                        continue
                    factor = matrix[r][col]
                    if abs(factor) <= 1e-12:
                        continue
                    for j in range(col, 4):
                        matrix[r][j] -= factor * matrix[col][j]
            d, e, f = matrix[0][3], matrix[1][3], matrix[2][3]
            cx, cy = -d / 2.0, -e / 2.0
            r2 = cx * cx + cy * cy - f
            if r2 <= 1.0:
                return None
            radius = math.sqrt(r2)
            errors = [abs(math.hypot(x - cx, y - cy) - radius) for x, y in pts]
            rms = math.sqrt(sum(err * err for err in errors) / max(1, len(errors)))
            return cx, cy, radius, rms

        clusters: list[dict] = []
        current: list[str] = []
        for fid in ids:
            candidate = current + [fid]
            fit = _fit_circle(candidate)
            if current and len(candidate) >= min_items and fit is not None and fit[3] > max(tolerance, fit[2] * 0.10):
                if len(current) >= min_items:
                    prev_fit = _fit_circle(current)
                    if prev_fit is not None:
                        clusters.append({"cx": prev_fit[0], "cy": prev_fit[1], "items": list(current)})
                    else:
                        clusters.append({"cx": 0.0, "cy": 0.0, "items": list(current)})
                current = [fid]
            else:
                current = candidate
        if len(current) >= min_items:
            fit = _fit_circle(current)
            if fit is not None and fit[3] <= max(tolerance, fit[2] * 0.10):
                clusters.append({"cx": fit[0], "cy": fit[1], "items": list(current)})
            else:
                clusters.append({"cx": 0.0, "cy": 0.0, "items": list(current)})
        if len(clusters) <= 1:
            return []
        return clusters

    def _direction_unified_anchor_theta(self, first_segment: list[str], ordered_ids: list[str],
                                        proj=None, clip: Optional[Clip] = None) -> Optional[float]:
        """R11(2026-08-13): per_shape 多段(多圆)时 cw/ccw 的统一基准角。

        各段不再各自锚定本段首台(跨圆相位错乱 = 症状①), 统一取全局基准序
        首台在首段圆拟合下的圆坐标角: 所有圆从同一世界方向起扫, 跨圆同相。
        单段/global 不走此路径(逐字节不变)。fit 失败返回 None → 调用方回退
        各段独立锚定(旧行为)。
        """
        seg_ids = [str(fid) for fid in (first_segment or []) if str(fid)]
        if len(seg_ids) < 3:
            return None
        positions = self._topview_fixture_positions_for_ids(seg_ids, proj=proj, clip=clip)
        if not positions or any(fid not in positions for fid in seg_ids):
            return None
        scope_coords = [(float(positions[fid][0]), float(positions[fid][1])) for fid in seg_ids]
        if self._matrix_like_points(scope_coords):
            return None
        reference = self._fit_circle_reference_for_ids(
            seg_ids, positions, proj=proj,
            prefer_shapes=not self._clip_has_complete_spatial_snapshot(clip, seg_ids),
            allow_bbox_fallback=False,
        )
        if reference is None:
            return None
        anchor_set = set(seg_ids)
        anchor_fid = ordered_ids[0] if ordered_ids and ordered_ids[0] in anchor_set else seg_ids[0]
        try:
            _error, theta = self._circle_shape_metric_for_fixture(reference, positions[anchor_fid])
        except Exception:
            return None
        return float(theta)

    def _sequential_fixture_group_data(self, clip: Clip, local_fixtures: list,
                                       direction: str, group_count: int,
                                       track=None, proj=None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """R172 §4 复合过渡：主方向定 base order/组切块，覆盖方向定组内相位。

        （语义/缓存键/回退路径与「无方向」同级/CE 取证见 engine/fade_group_overrides.py）
        """
        direction = self._normalize_clip_direction(direction)
        fixture_ids = tuple(str(getattr(fi, "id", "") or "") for fi in (local_fixtures or []))
        spatial_scope = self._clip_spatial_scope(clip)
        # ★ R172b（bug 46f505d 同源）：几何签名先算并入 quick 键，防 TopView 拖设备后命中陈旧组相位
        ordered_ids, _global_index = self._clip_global_fixture_order_for_fade(clip, track=track, proj=proj)
        total = len(ordered_ids)
        topview_pos_sig = self._fixture_position_signature(ordered_ids, proj=proj, clip=clip) if total > 0 else ()
        shape_sig = (
            self._topview_shape_signature_for_ids(ordered_ids, proj=proj)
            if total > 0
            and spatial_scope != "global"
            and not self._clip_has_complete_spatial_snapshot(clip, ordered_ids)
            else ()
        )
        quick_key = (
            self._clip_cache_id(clip),
            "seq_group_data_quick",
            direction, int(group_count or 1), spatial_scope,
            overrides_signature_with_group_count(clip, group_count),  # §4：覆盖签名入键防串缓存
            fixture_ids,
            str(getattr(track, "id", "") or id(track) if track is not None else ""),
            topview_pos_sig,
            shape_sig,
        )
        cached = self._clip_fade_geometry_cache.get(quick_key)
        if cached is not None:
            return cached      # ★ 性能：quick 命中直接返回（调用方只读，存储端以 copy 入缓存）

        K = len(local_fixtures)
        cache_key = (
            self._clip_cache_id(clip),
            "seq_group_data",
            direction, int(group_count or 1), spatial_scope,
            overrides_signature_with_group_count(clip, group_count),
            fixture_ids,
            tuple(ordered_ids),
            topview_pos_sig,
            shape_sig,
        )
        cached = self._clip_fade_geometry_cache.get(cache_key)
        if cached is not None:
            self._store_clip_fade_geometry_cache(quick_key, cached)
            # ★ 性能：缓存命中直接返回（调用方只读，见上）
            return cached

        def _store_and_return(group_idx: np.ndarray, group_pos: np.ndarray, group_sizes: np.ndarray):
            group_idx = np.asarray(group_idx, dtype=np.float32)
            group_pos = np.asarray(group_pos, dtype=np.float32)
            group_sizes = np.asarray(group_sizes, dtype=np.float32)
            # ★ 契约加固：缓存命中直接返回共享引用（全部调用方只读）——
            #   入缓存副本置只读，任何未来调用方意外原地改写会立即抛错
            #   而非静默污染缓存（审查加固建议）。
            _c0, _c1, _c2 = group_idx.copy(), group_pos.copy(), group_sizes.copy()
            for _arr in (_c0, _c1, _c2):
                try:
                    _arr.setflags(write=False)
                except Exception:
                    pass
            self._store_clip_fade_geometry_cache(cache_key, (_c0, _c1, _c2))
            _q0, _q1, _q2 = group_idx.copy(), group_pos.copy(), group_sizes.copy()
            for _arr in (_q0, _q1, _q2):
                try:
                    _arr.setflags(write=False)
                except Exception:
                    pass
            self._store_clip_fade_geometry_cache(quick_key, (_q0, _q1, _q2))
            return group_idx, group_pos, group_sizes

        if total <= 0 or K <= 0:
            group_idx = self._sequential_group_indices_for_positions(np.arange(K), group_count)
            group_sizes = np.ones(K, dtype=np.float32)
            group_pos = np.zeros(K, dtype=np.float32)
            for group in range(max(1, int(group_count or 1))):
                indices = np.flatnonzero(group_idx == group)
                if indices.size <= 0:
                    continue
                group_sizes[indices] = float(indices.size)
                if indices.size > 1:
                    pos = self._compute_direction_positions(direction, int(indices.size))
                    for local_rank, element_index in enumerate(indices):
                        group_pos[int(element_index)] = float(pos[local_rank])
            return _store_and_return(group_idx, group_pos, group_sizes)

        group_count = max(1, min(int(group_count or 1), max(1, total)))
        # ★ R172 §4：复合方向覆盖表（空表={} → dict.get 恒回主方向，无覆盖路径逐字节现状）
        overrides_map = overrides_map_for_clip(clip, group_count)
        meta: dict[str, tuple[int, float, int]] = {}
        if spatial_scope == "global":
            segments = [ordered_ids]
        else:
            segments = self._shape_aware_order_segments_for_fade_cached(
                clip, ordered_ids, proj=proj, spatial_scope=spatial_scope)
            if not segments:
                segments = [ordered_ids]
        # R11(2026-08-13) 症状①: per_shape 多段(多圆)两处修复 ——
        # ①统一基准角: cw/ccw 各段不再各自锚定本段首台(跨圆相位错乱), 统一取
        #   全局基准序首台的圆坐标角, 所有圆从同一世界方向起扫;
        # ②组号全局连续: G 个组跨段等额摊派(余数给前段), 段 A 占组 0..k、
        #   段 B 占组 k+1.., 不再每段从 0 重启 → 「两圆各一组」= 圆 A 完成再圆 B
        #   (G < 段数时每段至少 1 组, 时序由 batch mod 波次兜底)。
        anchor_theta: Optional[float] = None
        if spatial_scope != "global" and len(segments) > 1:
            anchor_theta = self._direction_unified_anchor_theta(
                segments[0], ordered_ids, proj=proj, clip=clip)
        if spatial_scope == "global" or len(segments) <= 1:
            group_shares = [group_count]
        elif group_count < len(segments):
            group_shares = [1] * len(segments)
        else:
            base_share, share_rem = divmod(group_count, len(segments))
            group_shares = [base_share + (1 if _si < share_rem else 0)
                            for _si in range(len(segments))]
        group_start = 0
        for _si, segment in enumerate(segments):
            ids_in_segment = [str(fid) for fid in segment if str(fid) in ordered_ids]
            if not ids_in_segment:
                continue
            scope_ids = ordered_ids if spatial_scope == "global" else ids_in_segment
            ids_in_segment = self._order_fixture_ids_for_direction(
                ids_in_segment, direction, proj=proj, clip=clip,
                scope_ids=scope_ids, start_theta=anchor_theta,
            )
            local_group_count = max(1, min(group_shares[_si], len(ids_in_segment)))
            max_step_count = int(np.ceil(len(ids_in_segment) / float(local_group_count)))
            for _gi in range(local_group_count):
                start = _gi * max_step_count
                ids = ids_in_segment[start:min(len(ids_in_segment), start + max_step_count)]
                if not ids:
                    continue
                # ★ R172 §4：组内相位用覆盖方向（覆盖表空/组未覆盖 → 恒=主方向，
                #   与改动前同一实参，逐字节现状）；base order/切块仍按主方向。
                positions = self._direction_positions_for_fixture_ids(
                    ids, overrides_map.get(group_start + _gi, direction),
                    proj=proj, clip=clip, scope_ids=scope_ids,
                )
                for rank, fid in enumerate(ids):
                    meta[str(fid)] = (group_start + _gi, float(positions[rank]), len(ids))
            group_start += group_shares[_si]

        group_idx = np.zeros(K, dtype=np.float32)
        group_pos = np.zeros(K, dtype=np.float32)
        group_sizes = np.ones(K, dtype=np.float32)
        for i, fi in enumerate(local_fixtures):
            fixture_id = str(getattr(fi, "id", ""))
            if fixture_id not in meta:
                group_idx = self._sequential_group_indices_for_positions(np.arange(K), group_count)
                group_sizes = np.ones(K, dtype=np.float32)
                group_pos = np.zeros(K, dtype=np.float32)
                for group in range(group_count):
                    indices = np.flatnonzero(group_idx == group)
                    if indices.size <= 0:
                        continue
                    group_sizes[indices] = float(indices.size)
                    if indices.size > 1:
                        pos = self._compute_direction_positions(direction, int(indices.size))
                        for local_rank, element_index in enumerate(indices):
                            group_pos[int(element_index)] = float(pos[local_rank])
                return _store_and_return(group_idx, group_pos, group_sizes)
            group, pos, size = meta[fixture_id]
            group_idx[i] = float(group)
            group_pos[i] = float(pos)
            group_sizes[i] = float(max(1, size))
        return _store_and_return(group_idx, group_pos, group_sizes)

    def _compute_sequential_fixture_gains(self, clip: Clip, local_fixtures: list,
                                          fade_in: float, fade_out: float,
                                          duration: float, local_t: float,
                                          in_direction: str, out_direction: str,
                                          groups: int, group_pause: float,
                                          single_duration: float,
                                          is_fading_in: bool, is_fading_out: bool,
                                          track=None, proj=None,
                                          seq_delay_in: float = 0.0,
                                          seq_delay_out: float = 0.0,
                                          ease_lut=None,
                                          fade_in_scale: float = 1.0,
                                          fade_out_scale: float = 1.0,
                                          *, stagger_override: Optional[float] = None) -> np.ndarray:
        K = len(local_fixtures)
        gains = np.ones(K, dtype=np.float32)
        # ★ R152/R158：窗口(过渡翼)与错峰步长(延迟翼)独立；stagger_override=0.0
        #   = 不受延迟翼（电机语义）；未启用扫掠时退回旧三级回退。
        _stagger, per_group = _seq_window_and_step(
            self, clip, single_duration, seq_in=seq_delay_in, seq_out=seq_delay_out,
            fade_in=fade_in, fade_out=fade_out, duration=duration,
            override=stagger_override)
        batch_count = self._clip_sequential_batch_count(clip, groups)
        pauses = self._clip_sequential_pauses(clip, groups, group_pause)
        batch_offsets = np.asarray(
            self._clip_sequential_offsets(_stagger, pauses, batch_count), dtype=np.float32)
        # ★ R157：**哪个方向已由分组窗口处理，就不再叠该方向的整体增益**（否则
        #   过渡做两遍 —— 用户见「四组之外又多一个整体大过渡」）。
        #   只拖过渡翼（延迟翼=0）时旧式仍叠整体增益，正是该症状的成因。
        _in_covered = bool(is_fading_in)
        _out_covered = bool(is_fading_out and (fade_out > 1e-4 or seq_delay_out > 1e-4))
        global_gain = _sequential_global_gain(
            self, clip, local_t, skip_in=_in_covered, skip_out=_out_covered)

        if is_fading_in:
            group_idx, group_pos, group_sizes = self._sequential_fixture_group_data(
                clip, local_fixtures, in_direction, groups, track=track, proj=proj)
            batch_idx = np.mod(group_idx.astype(np.int32), batch_count)
            # ★ R156：**每台自己的过渡时长 = 过渡翼**（旧式 `per_group / group_sizes`
            #   按台数摊薄 → 真工程 d4381eb3 实测 1.00/0.74/0.56/0.38/0.20/0.10s 递减）。
            #   起点仍用旧式的组内铺开（对 a93e080c 等按原设计校准过）。
            win = np.maximum(1e-6, per_group * float(fade_in_scale))
            if _stagger > 1e-9:
                # ★★★ R162：延迟翼均分给各批次，**批次内部**再按组内名次铺开 ——
                #   批次之间与批次内部**两级相位差**都成立（见 helper docstring）。
                #   旧式 `group_pos × max(0, per_group − win)` 在 R156 把 win 改成
                #   整个过渡翼后**恒为 0** → 组内各台同时起步（用户实报"组内相位差
                #   没有恢复"）。
                _latency = (float(batch_offsets[-1]) if len(batch_offsets)
                            else float(_stagger) * max(0, int(batch_count) - 1))
                t_start = _seq_latency_start(
                    group_idx, group_pos, group_sizes, batch_count, _latency)
            else:
                t_start = batch_offsets[batch_idx]
            gains = np.minimum(gains, _ramp_gain(
                self, clip, local_t, t_start, win, fade_in=True, ease_lut=ease_lut))

        if is_fading_out and (fade_out > 1e-4 or seq_delay_out > 1e-4):
            gains = np.minimum(gains, _seq_fade_out_gains(
                self, clip, local_fixtures, groups, duration,
                out_direction=out_direction, local_t=local_t,
                prm=(fade_out, seq_delay_out, fade_out_scale, ease_lut, _stagger,
                     per_group, batch_count, pauses, batch_offsets, track, proj)))

        return (gains * global_gain).astype(np.float32)

    def _compute_parallel_fixture_gains(self, clip: Clip, local_fixtures: list,
                                        fade_in: float, fade_out: float,
                                        duration: float, local_t: float,
                                        in_direction: str, out_direction: str,
                                        groups: int,
                                        is_fading_in: bool, is_fading_out: bool,
                                        track=None, proj=None,
                                        seq_delay_in: float = 0.0,
                                        seq_delay_out: float = 0.0,
                                        ease_lut=None,
                                        fade_in_scale: float = 1.0,
                                        fade_out_scale: float = 1.0) -> np.ndarray:
        K = len(local_fixtures)
        gains = np.ones(K, dtype=np.float32)
        eff_fade_in = max(0.0, fade_in * float(fade_in_scale))
        eff_fade_out = max(0.0, fade_out * float(fade_out_scale))

        if is_fading_in and (eff_fade_in > 1e-4 or seq_delay_in > 1e-4):
            _group_idx, group_pos, group_sizes = self._sequential_fixture_group_data(
                clip, local_fixtures, in_direction, groups, track=track, proj=proj)
            t_start, win = _parallel_in_window(
                clip, duration, seq_delay_in, eff_fade_in, (group_pos, group_sizes))
            gains = np.minimum(gains, _group_ramp_gains(
                self, clip, local_t, t_start, win, fade_in=True, ease_lut=ease_lut))

        if is_fading_out and (eff_fade_out > 1e-4 or seq_delay_out > 1e-4):
            _group_idx, group_pos, group_sizes = self._sequential_fixture_group_data(
                clip, local_fixtures, out_direction, groups, track=track, proj=proj)
            t_start, win = _parallel_out_window(
                clip, duration, seq_delay_out, eff_fade_out, (group_pos, group_sizes))
            gains = np.minimum(gains, _group_ramp_gains(
                self, clip, local_t, t_start, win, fade_in=False, ease_lut=ease_lut))

        return gains

    def _clip_mask_frames_cached(self, clip: Clip, universe: int):
        """R56c：帧序列准备缓存（R178 实现搬至 clip_mask_prep.mask_frames_cached）。

        返回 (ts_arr f64[n], data_arr u8[n,512])；本域 ≤1 帧返回 None。
        """
        return mask_frames_cached(self, clip, universe)

    def _clip_current_channel_active_mask(
        self,
        clip: Clip,
        universe: int,
        source_t: float,
        base_active: np.ndarray,
        current_raw: Optional[np.ndarray] = None,
        hold_extension: bool = False,
    ) -> np.ndarray:
        """Return the channels that the sampled clip frame should own now.

        Recorded clips keep a clip-wide union of touched channels so that real
        zero values are serializable.  For moving effects that union is too
        broad: channels that are only silent in the current frame would still
        read as active zeroes and can drive linker low-override mappings to 0.
        Non-zero values stay active; zero values stay active only at the frame
        where the channel actually changes.
        """
        base = np.asarray(base_active, dtype=bool)
        if base.shape != (512,):
            fixed = np.zeros(512, dtype=bool)
            n = min(base.size, 512)
            if n > 0:
                fixed[:n] = base.reshape(-1)[:n]
            base = fixed
        if not mask_count(base):
            return base

        from src.core.recorded_channel_masks import recorded_mask_at
        recorded = recorded_mask_at(clip, universe, source_t)
        if recorded is not None:
            return mask_and(base, recorded)
        if hold_extension:
            return base

        # R56c：帧序列走准备缓存（过滤+排序+数据矩阵是 source_t 无关不变量；
        # 一次构建 + searchsorted 取帧）。R178：直播/录制路径同样走缓存——原
        # 逐次计算每调用全量过滤+排序+线性扫全部帧（TTM 实测占纯 Python 单帧
        # 83%）。缓存随 invalidate_clip_cache / invalidate_all_caches 与编译缓存
        # 同源失效（平滑等原地改帧必经失效），新鲜度与渲染数据本身一致；直播与
        # 离线同一取帧语义（自动烘焙读缓存 ≡ 实时渲染）。无缓存 = 该域 ≤1 帧，
        # 与原逐次计算的 `len(frames) <= 1 → base` 同义。
        prep = self._clip_mask_frames_cached(clip, universe)
        if prep is None:
            return base
        ts_arr, data_arr = prep
        n_frames = ts_arr.shape[0]

        source_t = max(0.0, float(source_t))
        # 与原线性扫等价：最后一个 ts <= source_t + 1e-9 的帧下标
        idx = int(np.searchsorted(ts_arr, source_t + 1e-9, side="right")) - 1
        if idx < 0:
            idx = 0

        if current_raw is None:
            cur = data_arr[idx]
        else:
            cur = np.asarray(current_raw, dtype=np.uint8)
            if cur.shape != (512,):
                fixed = np.zeros(512, dtype=np.uint8)
                n = min(cur.size, 512)
                if n > 0:
                    fixed[:n] = cur.reshape(-1)[:n]
                cur = fixed

        changed = np.zeros(512, dtype=bool)
        if idx > 0:
            changed = mask_or(changed, mask_math(6, cur, data_arr[idx - 1])[0])
        if idx + 1 < n_frames:
            changed = mask_or(changed, mask_math(6, cur, data_arr[idx + 1])[0])

        current = mask_or(mask_math(0, cur)[0], changed)
        return mask_and(base, current)

    def _compute_direction_positions(self, direction: str, K: int) -> np.ndarray:
        """根据方向类型计算 K 个元素的位置系数 [0,1]。"""
        if K <= 1:
            return np.array([0.5], dtype=np.float32)

        pos = np.linspace(0.0, 1.0, K, dtype=np.float32)

        if direction in ("ltr", "ftb"):
            pass
        elif direction in ("rtl", "btf"):
            pos = 1.0 - pos
        elif direction == "ctr":
            pos = np.abs(pos - 0.5) * 2.0
        elif direction == "rtc":
            pos = 1.0 - np.abs(pos - 0.5) * 2.0
        elif direction == "alt":
            pos = np.array([float(i % 2) for i in range(K)], dtype=np.float32)
        elif direction == "alt_block":
            pos = np.array([float((i // max(1, K // 4)) % 2) for i in range(K)], dtype=np.float32)
        elif direction == "random":
            rng = np.random.default_rng(seed=K * 42 + 7)
            order = np.arange(K, dtype=np.float32)
            rng.shuffle(order)
            pos = order / max(1, K - 1)
        elif direction == "wave":
            pos = (np.sin(pos * np.pi * 2.0 - np.pi / 2.0) + 1.0) / 2.0
        elif direction == "cascade":
            pos = pos ** 2.0
        elif direction == "rev_cascade":
            pos = 1.0 - (1.0 - pos) ** 2.0
        elif direction == "zigzag":
            quarter = K // 4 if K >= 4 else 1
            zigzag_pos = np.zeros(K, dtype=np.float32)
            for i in range(K):
                segment = i // quarter
                local_pos = (i % quarter) / max(1, quarter - 1)
                if segment % 2 == 0:
                    zigzag_pos[i] = local_pos
                else:
                    zigzag_pos[i] = 1.0 - local_pos
            mn, mx = zigzag_pos.min(), zigzag_pos.max()
            if mx > mn:
                pos = (zigzag_pos - mn) / (mx - mn)
            else:
                pos = np.zeros(K, dtype=np.float32)
        else:
            pass

        return pos

    def _compute_group_gains_vectorized(self, positions: np.ndarray, groups: int,
                                         fade_in: float, fade_out: float,
                                         duration: float, local_t: float,
                                         in_direction: str, out_direction: str,
                                         sequence_mode: str = "parallel",
                                         group_pause: float = 0.0,
                                         single_duration: float = 0.0,
                                         clip: Optional[Clip] = None,
                                         seq_delay_in: float = 0.0,
                                         seq_delay_out: float = 0.0,
                                         ease_lut=None) -> np.ndarray:
        """根据方向和并行分组数计算每个元素的淡入淡出增益。

        positions 语义：0.0 = 最先亮/最先灭，1.0 = 最后亮/最后灭。

        这里的 groups 采用舞台编排语义：**并行分组数**，而不是阶段数。
        例如 10 个喷头、groups=2、ltr 时，会把喷头视为两条并行组
        [1,2,3,4,5] 与 [6,7,8,9,10]，推进列为
        1+6 → 2+7 → 3+8 → 4+9 → 5+10。
        """
        import numpy as np
        K = len(positions)
        gains = np.ones(K, dtype=np.float32)
        sequence_mode = str(sequence_mode or "parallel").strip().lower()
        if sequence_mode not in ("parallel", "sequential"):
            sequence_mode = "parallel"
        group_pause = max(0.0, float(group_pause or 0.0))
        single_duration = max(0.0, float(single_duration or 0.0))

        def _parallel_group_positions(direction_pos: np.ndarray, group_count: int) -> tuple[np.ndarray, int]:
            """Split the current order into continuous groups that run together."""
            group_count = max(1, min(int(group_count or 1), max(1, K)))
            if K <= 1:
                return np.zeros(K, dtype=np.float32), 1

            max_step_count = int(np.ceil(K / float(group_count)))
            group_idx = np.zeros(K, dtype=np.int32)
            max_group_size = 1
            for element_index in range(K):
                group = min(group_count - 1, element_index // max_step_count)
                group_idx[element_index] = group

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

        def _sequential_group_positions(direction_pos: np.ndarray, group_count: int) -> tuple[np.ndarray, np.ndarray, int, np.ndarray]:
            """把方向排序转换为连续块分组。

            顺序分组里，group_count 只表示“把当前设备顺序切成几组”，不再
            参与方向排序。每组内部再按 direction_pos 推进。这样圆形队列
            设置 2 组时就是两个连续半圈，而不是被中心/对称方向切成四翼。
            """
            group_count = max(1, min(int(group_count or 1), max(1, K)))
            if K <= 1:
                return (
                    np.zeros(K, dtype=np.float32),
                    np.zeros(K, dtype=np.float32),
                    1,
                    np.ones(K, dtype=np.float32),
                )

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
            return group_idx.astype(np.float32), group_pos, group_count, element_group_size

        def _sequential_times(direction_pos: np.ndarray, fade_span: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
            group_idx, group_pos, group_count, group_sizes = _sequential_group_positions(direction_pos, groups)
            batch_count = self._clip_sequential_batch_count(clip, group_count)
            batch_idx = np.mod(group_idx.astype(np.int32), batch_count)
            per_group = single_duration if single_duration > 1e-4 else fade_span
            per_group = max(1e-6, float(per_group))
            pauses = (
                self._clip_sequential_pauses(clip, group_count, group_pause)
                if clip is not None
                else [group_pause] * max(0, batch_count - 1)
            )
            batch_offsets = np.asarray(
                self._clip_sequential_offsets(per_group, pauses, batch_count),
                dtype=np.float32,
            )
            win = per_group / np.maximum(group_sizes, 1.0)
            t_start = batch_offsets[batch_idx] + group_pos * np.maximum(0.0, per_group - win)
            t_end = t_start + win
            return t_start, t_end, win

        # ── 淡入 ──
        if (fade_in > 1e-4 and local_t < fade_in) or (seq_delay_in > 1e-4 and local_t < seq_delay_in + fade_in):
            in_pos = self._compute_direction_positions(in_direction, K) \
                if in_direction not in ("none", "") else positions

            if seq_delay_in > 1e-4:
                # 新解耦模型：扫掠 span=seq_delay_in，per-device 窗口=fade_in
                group_pos, _step_count = _parallel_group_positions(in_pos, groups)
                win_in = max(1e-6, fade_in)
                t_start = group_pos * seq_delay_in
                t_end = t_start + win_in
            elif sequence_mode == "sequential" and groups > 1:
                t_start, t_end, win_in = _sequential_times(in_pos, fade_in)
            else:
                group_pos, step_count = _parallel_group_positions(in_pos, groups)
                win_in = fade_in / float(max(1, step_count))
                t_start = group_pos * max(0.0, fade_in - win_in)
                t_end = t_start + win_in

            tt = np.clip((local_t - t_start) / np.maximum(win_in, 1e-6), 0.0, 1.0)
            fade_in_gains = self._clip_transition_gains(
                clip, tt, fade_in=True, ease_lut=ease_lut
            )
            fade_in_gains = np.where(local_t < t_start, 0.0, fade_in_gains)
            fade_in_gains = np.where(local_t >= t_end, 1.0, fade_in_gains)
            gains = np.minimum(gains, fade_in_gains)

        # ── 淡出 ──
        if (fade_out > 1e-4 and local_t > duration - fade_out) or (seq_delay_out > 1e-4 and local_t > duration - (seq_delay_out + fade_out)):
            out_pos = self._compute_direction_positions(out_direction, K) \
                if out_direction not in ("none", "") else positions

            if seq_delay_out > 1e-4:
                region = seq_delay_out + fade_out
                group_pos, _step_count = _parallel_group_positions(out_pos, groups)
                win_out = max(1e-6, fade_out)
                t_start = (duration - region) + group_pos * seq_delay_out
                t_end = t_start + win_out
            elif sequence_mode == "sequential" and groups > 1:
                rel_start, rel_end, win_out = _sequential_times(out_pos, fade_out)
                t_start = (duration - fade_out) + rel_start
                t_end = (duration - fade_out) + rel_end
            else:
                group_pos, step_count = _parallel_group_positions(out_pos, groups)
                win_out = fade_out / float(max(1, step_count))
                t_start = (duration - fade_out) + group_pos * max(0.0, fade_out - win_out)
                t_end = t_start + win_out

            tt = np.clip((t_end - local_t) / np.maximum(win_out, 1e-6), 0.0, 1.0)
            fade_out_gains = self._clip_transition_gains(
                clip, tt, fade_in=False, ease_lut=ease_lut
            )
            fade_out_gains = np.where(local_t >= t_end, 0.0, fade_out_gains)
            fade_out_gains = np.where(local_t < t_start, 1.0, fade_out_gains)
            gains = np.minimum(gains, fade_out_gains)

        return gains

    def _clip_completed_sequential_batch_index(self, clip: Clip, local_t: float) -> int:
        enabled, _groups, pauses, single, batch_count, step = self._clip_sequential_timing_detail(clip)
        if not enabled:
            return -1
        batch, group_t, _in_pause = self._clip_sequential_active_batch(
            local_t, single, pauses, batch_count, step=step)
        if group_t >= single - 1e-6:
            return batch
        return batch - 1

    def _clip_completed_sequential_group_index(self, clip: Clip, local_t: float) -> int:
        return self._clip_completed_sequential_batch_index(clip, local_t)

    def _sequential_group_indices_for_positions(self, direction_pos: np.ndarray,
                                                group_count: int) -> np.ndarray:
        K = len(direction_pos)
        group_count = max(1, min(int(group_count or 1), max(1, K)))
        if K <= 0:
            return np.zeros(0, dtype=np.int32)
        max_step_count = int(np.ceil(K / float(group_count)))
        group_idx = np.zeros(K, dtype=np.int32)
        for element_index in range(K):
            group_idx[element_index] = min(group_count - 1, element_index // max_step_count)
        return group_idx

    def _clip_completed_sequential_channel_mask(
        self,
        clip: Clip,
        local_t: float,
        universe: int,
        u_fixtures: list,
        track=None,
        fixture_defs_by_id: Optional[dict] = None,
        proj=None,
        compiled: Optional[dict] = None,
    ) -> np.ndarray:
        completed_batch = self._clip_completed_sequential_batch_index(clip, local_t)
        if completed_batch < 0:
            return np.zeros(512, dtype=bool)

        groups = max(1, int(getattr(clip, "fade_groups", 1) or 1))
        batch_count = self._clip_sequential_batch_count(clip, groups)
        direction = self._get_clip_fade_in_direction(clip)
        fixture_defs_by_id = fixture_defs_by_id or {}
        mask = np.zeros(512, dtype=bool)

        if u_fixtures:
            span_idx: Optional[np.ndarray] = None
            if compiled is not None:
                sorted_fixtures = list(compiled.get("sorted_fixtures", []) or [])
                spans = list(compiled.get("spans", []) or [])
                span_idx = compiled.get("span_idx")
            else:
                sorted_fixtures, spans = self._clip_ordered_fixtures_for_fade(
                    clip, universe, u_fixtures, track=track,
                    fixture_defs_by_id=fixture_defs_by_id,
                    proj=proj,
                )
            K = len(sorted_fixtures)
            if K <= 0:
                return mask
            group_idx = self._sequential_group_indices_for_fixtures(
                clip, sorted_fixtures, groups, track=track, proj=proj,
                direction=direction)
            if span_idx is not None:
                valid = span_idx >= 0
                mask[valid] = (
                    group_idx[span_idx[valid]] % batch_count <= completed_batch
                )
            else:
                for i, (_fi, (start, end)) in enumerate(zip(sorted_fixtures, spans)):
                    if int(group_idx[i]) % batch_count > completed_batch:
                        continue
                    if not (0 <= start < end <= 512):
                        continue
                    mask[start:end] = True
            return mask

        active_channels = self._get_clip_active_channels(clip, universe, track=track)
        K = len(active_channels)
        if K <= 0:
            return mask
        positions = self._compute_direction_positions(direction, K)
        group_idx = self._sequential_group_indices_for_positions(positions, groups)
        indices = np.asarray(active_channels, dtype=np.int32) - 1
        valid = (indices >= 0) & (indices < 512)
        mask[indices[valid]] = (
            group_idx[valid] % batch_count <= completed_batch
        )
        return mask

    def _get_clip_active_channels(self, clip: Clip, universe: int, track=None) -> List[int]:
        """获取 clip 在指定 universe 的活跃通道列表（带缓存）。"""
        rule_sig = "all"
        try:
            rule = self._track_filter_rule_for_universe(track, universe)
            if rule is not None and not rule.is_empty() and not rule.allows_all():
                rule_sig = ",".join(str(int(ch)) for ch in sorted(getattr(rule, "channels", []) or []))
        except Exception:
            rule_sig = "all"
        cache_key = f"{self._clip_cache_id(clip)}::{universe}::{rule_sig}"
        cached = self._clip_active_channels_cache.get(cache_key)
        if cached is not None:
            return cached

        active_channels = []

        # 优先使用轨道过滤规则中的通道
        if track is not None:
            rule_active = self._track_filter_mask_for_universe(track, universe)
            if rule_active is not None:
                active_channels = [ch + 1 for ch in range(512) if rule_active[ch]]

        # 其次使用显式 active_channels_by_universe（权威来源；电机/位置合法值可为 0）
        if not active_channels:
            active_map = getattr(clip, "active_channels_by_universe", {}) or {}
            channels = active_map.get(universe) or active_map.get(str(universe))
            if channels:
                active_channels = [
                    int(ch) for ch in channels
                    if 1 <= int(ch) <= 512
                ]

        # 最后才回退到 clip 帧中非零通道的并集
        # 注意：必须是所有帧的并集，不能在第一帧有数据后 break（否则会漏掉仅在后
        # 续帧点亮的通道）。全 512 通道已点亮时可提前退出。
        if not active_channels:
            mask = np.zeros(512, dtype=bool)
            for frame in getattr(clip, "frames", []) or []:
                if int(getattr(frame, "universe", 1)) != universe:
                    continue
                raw = np.asarray(frame.data, dtype=np.uint8)
                if raw.shape == (512,):
                    mask |= (raw > 0)
                elif raw.size > 0:
                    n = min(raw.size, 512)
                    mask[:n] |= (raw.flatten()[:n] > 0)
                if mask.all():
                    break
            if mask.any():
                active_channels = [ch + 1 for ch in range(512) if mask[ch]]

        # 缓存结果
        self._clip_active_channels_cache[cache_key] = active_channels
        # 缓存上限
        if len(self._clip_active_channels_cache) > 512:
            self._evict_oldest(self._clip_active_channels_cache)

        return active_channels

    def _clip_channel_fade_gains(self, clip: Clip, local_t: float,
                                  universe: int, u_fixtures: list,
                                  track=None,
                                  fixture_defs_by_id: Optional[dict] = None,
                                  proj=None,
                                  compiled: Optional[dict] = None) -> np.ndarray:
        """方向性分组淡入淡出（v4 重写版）。

        ★ 核心改进：
          1. 淡入/淡出方向分别获取
          2. 全面向量化计算，消除 Python for 循环卡死
          3. 活跃通道使用缓存，避免每 tick 重复扫描帧
        """
        duration = max(0.0, float(getattr(clip, "duration", 0.0)))
        if duration <= 0.0:
            return np.ones(512, dtype=np.float32)

        fade_in  = max(0.0, min(float(getattr(clip, "fade_in",  0.0) or 0.0), duration))
        fade_out = max(0.0, min(float(getattr(clip, "fade_out", 0.0) or 0.0), duration))
        groups   = max(1, int(getattr(clip, "fade_groups", 1) or 1))
        sequence_mode = str(getattr(clip, "fade_group_sequence_mode", "parallel") or "parallel").strip().lower()
        if sequence_mode not in ("parallel", "sequential"):
            sequence_mode = "parallel"
        single_duration = max(0.0, float(getattr(clip, "fade_group_single_duration", 0.0) or 0.0))
        seq_delay_in = max(0.0, min(float(getattr(clip, "seq_delay_in", 0.0) or 0.0), duration))
        seq_delay_out = max(0.0, min(float(getattr(clip, "seq_delay_out", 0.0) or 0.0), duration))
        _seq_enabled, _grp, _pz, _seq_single, _bc, _step = self._clip_sequential_timing_detail(clip)
        if _seq_enabled:
            single_duration = _seq_single
        ease_lut = self._seq_delay_ease_lut(getattr(clip, "seq_delay_curve", None))
        ratio_in = {
            self._norm_ratio_channel_type(k): float(v)
            for k, v in (getattr(clip, "fade_type_ratio_in", {}) or {}).items()
            if abs(float(v) - 1.0) > 1e-3
        }
        ratio_out = {
            self._norm_ratio_channel_type(k): float(v)
            for k, v in (getattr(clip, "fade_type_ratio_out", {}) or {}).items()
            if abs(float(v) - 1.0) > 1e-3
        }

        in_direction = self._get_clip_fade_in_direction(clip)
        out_direction = self._get_clip_fade_out_direction(clip)

        is_sequential = _seq_enabled
        sequence_span = (
            self._clip_sequential_span(
                clip,
                _grp,
                max(0.0, float(getattr(clip, "fade_group_pause", 0.0) or 0.0)),
                _seq_single,
                step=_step,
            )
            if is_sequential else 0.0
        )
        # 区间上界纳入延迟时间（seq_delay）：扫掠尾部设备须在 seq_delay+fade 处才完成，不能被旧的 fade 窗口裁断。
        is_fading_in = (
            in_direction != "none" and (
                (fade_in > 1e-4 and local_t < fade_in)
                or (seq_delay_in > 1e-4 and local_t < seq_delay_in + fade_in)
                or (is_sequential and local_t < max(1e-4, sequence_span))
            )
        )
        is_fading_out = (
            out_direction != "none" and (
                (fade_out > 1e-4 and local_t > duration - fade_out)
                or (seq_delay_out > 1e-4 and local_t > duration - (seq_delay_out + fade_out))
            )
        )
        if log.isEnabledFor(logging.DEBUG):
            log.debug("[DIR_FADE] clip=%s local_t=%.3f fade_in=%.2f fade_out=%.2f dur=%.2f in_dir=%s out_dir=%s is_fading_in=%s is_fading_out=%s",
                      getattr(clip, 'id', '?')[:8], local_t, fade_in, fade_out, duration, in_direction, out_direction, is_fading_in, is_fading_out)
        if not is_fading_in and not is_fading_out:
            # 即使方向设置了，但不在 fade 时间窗口内，仍需要应用普通淡入淡出
            g = self._clip_fade_gain(clip, local_t)
            return np.full(512, g, dtype=np.float32)

        gains = np.ones(512, dtype=np.float32)

        # 

        if u_fixtures:
            fixture_defs_by_id = fixture_defs_by_id or {}
            span_idx: Optional[np.ndarray] = None
            if compiled is not None:
                sorted_fixtures = list(compiled.get("sorted_fixtures", []) or [])
                spans = list(compiled.get("spans", []) or [])
                span_idx = compiled.get("span_idx")
            else:
                sorted_fixtures, spans = self._clip_ordered_fixtures_for_fade(
                    clip, universe, u_fixtures, track=track,
                    fixture_defs_by_id=fixture_defs_by_id,
                    proj=proj,
                )
            K = len(sorted_fixtures)
            if K <= 0:
                g = self._clip_fade_gain(clip, local_t)
                return np.full(512, g, dtype=np.float32)

            group_pause = max(0.0, float(getattr(clip, "fade_group_pause", 0.0) or 0.0))

            def _compute_fi(scale_in: float, scale_out: float) -> np.ndarray:
                if is_sequential:
                    return self._compute_sequential_fixture_gains(
                        clip, sorted_fixtures, fade_in, fade_out, duration, local_t,
                        in_direction, out_direction, _grp, group_pause, single_duration,
                        is_fading_in, is_fading_out, track=track, proj=proj,
                        seq_delay_in=seq_delay_in, seq_delay_out=seq_delay_out,
                        ease_lut=ease_lut, fade_in_scale=scale_in, fade_out_scale=scale_out)
                return self._compute_parallel_fixture_gains(
                    clip, sorted_fixtures, fade_in, fade_out, duration, local_t,
                    in_direction, out_direction, groups,
                    is_fading_in, is_fading_out, track=track, proj=proj,
                    seq_delay_in=seq_delay_in, seq_delay_out=seq_delay_out,
                    ease_lut=ease_lut, fade_in_scale=scale_in, fade_out_scale=scale_out)

            if not ratio_in and not ratio_out:
                # 快路径：无逐类型比例 → 单台一个增益（与改动前逐字节一致）
                fi_gains = _compute_fi(1.0, 1.0)
                if span_idx is not None:
                    valid = span_idx >= 0
                    gains[valid] = fi_gains[span_idx[valid]]
                else:
                    for i, (start, end) in enumerate(spans):
                        if not (0 <= start < end <= 512):
                            continue
                        gains[start:end] = fi_gains[i]
                _apply_motor_no_stagger_gains(
                    self, clip, gains, span_idx, spans,
                    ctx=(sorted_fixtures, universe, local_t, fade_in, fade_out, duration,
                         in_direction, out_direction, group_pause, is_fading_in,
                         is_fading_out, track, proj, _grp, single_duration, seq_delay_in,
                         seq_delay_out, ease_lut, is_sequential))
                # R173 性能：此分支（`if not ratio_in and not ratio_out:`）内
                # ratio_in/ratio_out 恒为空字典，原「逐通道类型比例」覆盖段
                # （_clip_fixture_type_index_map + gains_by_type + default_gains）
                # 对每个 ct 只会算出 _compute_fi(ratio_in.get(ct,1.0), ratio_out.get(ct,1.0))
                # == _compute_fi(1.0, 1.0) == fi_gains（逐字节恒等，default_gains 本身也是
                # 重复调用 _compute_fi(1.0, 1.0)），随后只是把上面刚写好的 gains 用同一批
                # 数值原样覆盖一遍 —— 数学上是纯粹的重复计算+重复写入，本分支内已删除
                # （见 git blame 70784744/0e0d022b/97e25763 确认无隐藏副作用；
                # _clip_fixture_type_index_map 本身是 6187 行的纯读取构建，无状态写入）。
                # ratio_in/ratio_out 非空时此 `if` 整体不成立，本分支从不执行（改前如此，
                # 改动不影响该路径——该路径下 _clip_fixture_type_index_map 从未被
                # Python 侧调用，是既有独立问题，不在本次性能任务范围内）。cProfile 中
                # _clip_fixture_type_index_map 4.0s/10.7s 热点即由本分支消除。
            if log.isEnabledFor(logging.DEBUG):
                log.debug("[DIR_FADE_A] K=%d groups=%d local_t=%.3f seq_delay_in=%.2f",
                          K, groups, local_t, seq_delay_in)
            return gains

        # 

        active_channels = self._get_clip_active_channels(clip, universe, track=track)
        if log.isEnabledFor(logging.DEBUG):
            log.debug("[DIR_FADE_B] clip=%s universe=%s active_ch=%d u_fixtures=%d",
                      getattr(clip, "id", "?")[:8], universe, len(active_channels), len(u_fixtures))

        # 如果没有找到活跃通道，回退到整体淡入淡出
        if not active_channels:
            if log.isEnabledFor(logging.DEBUG):
                log.debug("[DIR_FADE_B] FALLBACK: no active channels -> whole-clip gain")
            g = self._clip_fade_gain(clip, local_t)
            return np.full(512, g, dtype=np.float32)

        # 向量化计算
        K = len(active_channels)
        positions = self._compute_direction_positions(
            in_direction if is_fading_in else out_direction, K)
        ch_gains = self._compute_group_gains_vectorized(
            positions, groups, fade_in, fade_out, duration, local_t,
            in_direction, out_direction, sequence_mode,
            max(0.0, float(getattr(clip, "fade_group_pause", 0.0) or 0.0)),
            single_duration,
            clip=clip,
            seq_delay_in=seq_delay_in, seq_delay_out=seq_delay_out, ease_lut=ease_lut)

        # 向量化映射到 512 通道数组
        indices = np.array(active_channels, dtype=np.int32) - 1  # 转为 0-indexed
        valid = (indices >= 0) & (indices < 512)
        gains[indices[valid]] = ch_gains[valid]

        return gains

    # ── 辅助方法 ──────────────────────────────────────────────────────────────

    def _clear_stale_timeline_layers(self, active_universes: set[int], layer_name: str = "clip_playback"):
        """清理上一帧输出过、当前已无活动 clip 的指定 clip 层 universe。"""
        layer_map = getattr(self, "_last_output_universes_by_layer", {}) or {}
        prev = set(layer_map.get(layer_name, set()))
        stale = prev - set(active_universes)
        if self._nm is not None and hasattr(self._nm, "clear_layer"):
            for u in stale:
                try:
                    self._nm.clear_layer(layer_name, u)
                except TypeError:
                    try:
                        self._nm.clear_layer(layer_name)
                    except Exception:
                        # R181 P2-3：陈旧 clip 层清不掉 = 停止后残留输出，留痕
                        log.debug("清理陈旧时间线层失败 layer=%s（可能残留输出）",
                                  layer_name, exc_info=True)
                except Exception:
                    # R181 P2-3：同上（带 universe 的调用形式）
                    log.debug("清理陈旧时间线层失败 layer=%s uni=%s", layer_name, u,
                              exc_info=True)
        layer_map[layer_name] = set(active_universes)
        self._last_output_universes_by_layer = layer_map
        self._last_output_universes = set().union(*layer_map.values()) if layer_map else set(active_universes)

    def _flush_timeline_output_now(self, active_universes: set[int], layer_name: str = "clip_playback"):
        """写入 clip 播放/预览层后立即触发一次 DMX 输出。"""
        nm = self._nm
        if nm is None:
            return
        if (
            bool(getattr(nm, "_running", False))
            and bool(getattr(nm, "_output_worker_enabled", False))
        ):
            return
        universes = set(active_universes) | set(getattr(self, "_last_output_universes_by_layer", {}).get(layer_name, set()))
        if not universes:
            return
        if hasattr(nm, "flush_universes_now"):
            try:
                nm.flush_universes_now(sorted(universes))
                return
            except Exception:
                log.exception("timeline flush_universes_now failed")

    # ── clip 混合逻辑 ──────────────────────────────────────────────────────

    def _mix_clips(
        self,
        items: List[Tuple[np.ndarray, np.ndarray, np.ndarray]],
    ) -> Tuple[np.ndarray, np.ndarray]:
        """主形状 + 其它 clip 偏移叠加。"""
        n = len(items)
        if n == 1:
            v, a, _ = items[0]
            return _frame_buffer_pool.copy_from(v), a.copy()

        kernel = _get_render_kernels()

        # A3 零分配快路径：内核在场 + 门控开时，复用预分配缓冲填充 3 个 (n,512) 输入
        # 与 2 个 512 输出，消每 tick np.stack×3 + 内核内分配。逐字节等价(仅省分配)。
        if self._prealloc_enabled and kernel is not None:
            try:
                if n > self._mix_cap_layers:
                    cap = n
                    self._mix_vals_buf = np.empty((cap, 512), dtype=np.float32)
                    self._mix_acts_buf = np.empty((cap, 512), dtype=np.uint8)
                    self._mix_anchors_buf = np.empty((cap, 512), dtype=np.float32)
                    self._mix_cap_layers = cap
                vbuf = self._mix_vals_buf[:n]
                abuf = self._mix_acts_buf[:n]
                nbuf = self._mix_anchors_buf[:n]
                for i, it in enumerate(items):
                    np.copyto(vbuf[i], it[0], casting="unsafe")     # → float32(等价 astype)
                    np.copyto(abuf[i], it[1], casting="unsafe")     # bool → uint8(0/1)
                    np.copyto(nbuf[i], it[2], casting="unsafe")     # → float32
                out_u8, act_any = kernel.mix_clips_kernel(
                    vbuf, abuf, nbuf, float(self._modulation_gain),
                )
                # 仅复用**输入**缓冲(内核内同步读完即返回，跨 universe 复用安全)。
                # **输出**每次新分配(512B)：调用方把多 universe 结果累进 outputs 后统一消费，
                # 复用输出缓冲会造成 universe 间别名污染。输入 3×(n×512) 才是分配大头，已消。
                return out_u8, act_any.astype(bool)
            except Exception:
                log.exception("A3 预分配混音路径失败,回退标准路径")

        vals = np.stack([it[0].astype(np.float32) for it in items], axis=0)
        acts = np.stack([it[1] for it in items], axis=0)
        anchors = np.stack([it[2] for it in items], axis=0)

        # C 内核快路径(实测 2.53×,逐字节等价)。内核未编译/import 失败/环境变量关闭时
        # 回退下方纯 numpy 路径。QASCADE_C_KERNELS=0 可强制关闭。
        if kernel is not None:
            try:
                acts_u8 = acts.view(np.uint8) if acts.dtype == np.bool_ else acts.astype(np.uint8)
                out_u8, act_any = kernel.mix_clips_kernel(
                    vals, acts_u8, anchors, float(self._modulation_gain)
                )
                return out_u8, act_any.astype(bool)
            except Exception:
                log.exception("C 混音内核调用失败,回退 numpy")

        deltas = (vals - anchors)
        deltas[~acts] = 0.0

        abs_deltas = np.abs(deltas)
        primary_idx = np.argmax(abs_deltas, axis=0)

        ch_range = np.arange(512)
        primary_val    = vals[primary_idx, ch_range]
        primary_active = acts[primary_idx, ch_range]

        sum_delta   = deltas.sum(axis=0)
        primary_delta = deltas[primary_idx, ch_range]
        others_delta = (sum_delta - primary_delta) * self._modulation_gain

        active_any = acts.any(axis=0)

        result = np.zeros(512, dtype=np.float32)
        mask_a = primary_active
        result[mask_a] = primary_val[mask_a] + others_delta[mask_a]
        mask_b = (~primary_active) & active_any
        if (mask_b).any():
            sum_active = (vals * acts).sum(axis=0)
            cnt_active = acts.sum(axis=0)
            mean_val = np.zeros(512, dtype=np.float32)
            ok = cnt_active > 0
            mean_val[ok] = sum_active[ok] / cnt_active[ok]
            result[mask_b] = mean_val[mask_b]

        np.clip(result, 0.0, 255.0, out=result)
        return result.astype(np.uint8), active_any

    def _clip_channel_change_scores(
        self,
        clip: Clip,
        universe: int,
        source_t: float,
        timeline_now: float,
        clip_start: float,
        active: np.ndarray,
        hold_extension: bool = False,
    ) -> np.ndarray:
        """B3: vectorized — pre-extract frame data into numpy arrays."""
        scores = np.full(512, -np.inf, dtype=np.float64)
        active = np.asarray(active, dtype=bool)
        if active.shape != (512,):
            fixed = np.zeros(512, dtype=bool)
            n = min(active.size, 512)
            fixed[:n] = active.reshape(-1)[:n]
            active = fixed
        if not (active).any():
            return scores
        frames = [
            f for f in (getattr(clip, "frames", []) or [])
            if int(getattr(f, "universe", universe) or universe) == int(universe)
        ]
        if not frames:
            scores[active] = float(clip_start)
            return scores
        frames.sort(key=lambda f: float(getattr(f, "timestamp", 0.0) or 0.0))
        source_t = max(0.0, float(source_t))

        # Pre-extract timestamps and data into numpy arrays
        n = len(frames)
        timestamps = np.zeros(n, dtype=np.float64)
        data_stack = np.zeros((n, 512), dtype=np.uint8)
        for i, frame in enumerate(frames):
            timestamps[i] = float(getattr(frame, "timestamp", 0.0) or 0.0)
            raw = getattr(frame, "data", None)
            if raw is not None:
                d = np.asarray(raw, dtype=np.uint8).ravel()
                if d.shape[0] >= 512:
                    data_stack[i] = d[:512]
                elif d.shape[0] > 0:
                    data_stack[i, :d.shape[0]] = d

        # Limit to frames within source_t
        valid_idx = timestamps <= source_t + 1e-9
        if not (valid_idx).any():
            scores[active] = float(clip_start)
            return scores
        valid_n = int(valid_idx.sum())
        valid_ts = timestamps[:valid_n]
        valid_data = data_stack[:valid_n]

        # Vectorized: compute last change time per channel using diff
        last_change = np.zeros(512, dtype=np.float64)
        if valid_n > 0:
            # diff along frame axis to find where each channel changes
            changes = np.diff(valid_data, axis=0) != 0  # (valid_n-1, 512)
            # For each channel, find the last change index (or 0 if never changed)
            # Use argmax on reversed cumsum to find the last True per column
            # More direct: iterate over columns with a vectorized approach
            change_mask = np.zeros((valid_n, 512), dtype=bool)
            change_mask[0] = active.copy()  # First frame = "changed" relative to nothing
            if valid_n > 1:
                # Each subsequent frame = changed where data differs from prev
                diff_idx = np.where(changes)
                # Map diff rows (0..valid_n-2) to frame indices (1..valid_n-1)
                frame_idx = diff_idx[0] + 1
                ch_idx = diff_idx[1]
                change_mask[frame_idx, ch_idx] = True
            # Broadcast timestamps to each channel and mask by changes
            ts_broadcast = valid_ts[:, np.newaxis]  # (valid_n, 1)
            last_change = np.max(np.where(change_mask, ts_broadcast, 0.0), axis=0)

        rate = self._clip_playback_rate(clip)
        if rate <= 1e-9:
            rate = 1.0
        scores[active] = float(clip_start) + (last_change[active] / rate)
        if hold_extension:
            scores[active] = np.minimum(scores[active], _hold_score_cap(clip, clip_start))
        return scores

    def _compiled_clip_channel_change_scores(
        self,
        clip: Clip,
        universe: int,
        source_t: float,
        timeline_now: float,
        clip_start: float,
        active: np.ndarray,
        compiled: dict,
        hold_extension: bool = False,
    ) -> np.ndarray:
        scores = np.full(512, -np.inf, dtype=np.float64)
        active = np.asarray(active, dtype=bool)
        if active.shape != (512,):
            fixed = np.zeros(512, dtype=bool)
            n = min(active.size, 512)
            fixed[:n] = active.reshape(-1)[:n]
            active = fixed
        if not (active).any():
            return scores

        idx = self._compiled_clip_frame_index(compiled, source_t)
        if idx < 0:
            scores[active] = float(clip_start)
            return scores
        last_change = compiled.get("last_change_time")
        if last_change is None or idx >= len(last_change):
            return self._clip_channel_change_scores(
                clip,
                universe,
                source_t,
                timeline_now,
                clip_start,
                active,
                hold_extension=hold_extension,
            )
        rate = self._clip_playback_rate(clip)
        if rate <= 1e-9:
            rate = 1.0
        changes = np.asarray(last_change[idx], dtype=np.float64)
        scores[active] = float(clip_start) + (changes[active] / rate)
        if hold_extension:
            scores[active] = np.minimum(scores[active], _hold_score_cap(clip, clip_start))
        return scores

    # ── clip 锚点缓存 ──────────────────────────────────────────────────────

    def _clip_frame_index_for_universe(self, clip: Clip, universe: int) -> list:
        """clip 指定域帧列表(per-universe 索引缓存, 与编译/查找路径同源)。

        ★ 2026-08-06: 原多处全扫 clip.frames(至冬 876K帧×73域 = 64M 迭代,
        停止录制/锚点重建 4.5s 冻结)。统一走 _clip_frame_index_cache。
        """
        _all_frames = getattr(clip, "frames", []) or []
        if not _all_frames:
            return []
        _ckey = str(getattr(clip, "id", "") or id(clip))
        _fcnt = len(_all_frames)
        _cached = self._clip_frame_index_cache.get(_ckey)
        if _cached is None or _cached[0] != _fcnt:
            _by_u: Dict[int, list] = {}
            for _f in _all_frames:
                try:
                    _u = int(getattr(_f, "universe", 1) or 1)
                except Exception:
                    _u = 1
                _by_u.setdefault(_u, []).append(_f)
            for _lst in _by_u.values():
                _lst.sort(key=lambda f: float(getattr(f, "timestamp", 0.0) or 0.0))
            _cached = (_fcnt, _by_u)
            self._clip_frame_index_cache[_ckey] = _cached
            if len(self._clip_frame_index_cache) > 256:
                self._evict_oldest(self._clip_frame_index_cache)
        return _cached[1].get(int(universe), [])

    def _get_clip_anchor(
        self, clip: Clip, universe: int, ch_active: np.ndarray
    ) -> np.ndarray:
        """计算/读取 clip 的锚点（每通道一个值）。"""
        cache_key = f"{clip.id}::{universe}"
        anchor = self._clip_anchor_cache.get(cache_key)
        if anchor is not None:
            return anchor

        min_buf = np.full(512, 255, dtype=np.int16)
        max_buf = np.full(512,   0, dtype=np.int16)
        seen = np.zeros(512, dtype=bool)
        # ★ 2026-08-06 修复: 复用 _clip_frame_index_cache per-universe 索引 —
        #   原实现全扫 clip.frames(至冬 876K帧×73域 miss 重建 4.5s, 停止录制
        #   冻结, 用户实测)。与 _compiled_clip_universe/_find_frame 同源。
        _idx_frames = self._clip_frame_index_for_universe(clip, int(universe))
        anchor = (_ce_clip_anchor_reduce(self, clip, universe, ch_active, _idx_frames)
                  if _ce_clip_anchor_reduce is not None else None)
        if anchor is None:
            for f in _idx_frames:
                d = np.asarray(f.data, dtype=np.int16)
                if d.shape != (512,):
                    fixed = np.zeros(512, dtype=np.int16)
                    n = min(d.size, 512)
                    if n > 0:
                        fixed[:n] = d.reshape(-1)[:n]
                    d = fixed
                np.minimum(min_buf, d, out=min_buf)
                np.maximum(max_buf, d, out=max_buf)
                seen |= ch_active

            anchor = np.zeros(512, dtype=np.float32)
            idx = seen
            if (idx).any():
                anchor[idx] = (max_buf[idx].astype(np.float32) + min_buf[idx].astype(np.float32)) / 2.0
            anchor[~ch_active] = 0.0

        self._clip_anchor_cache[cache_key] = anchor
        # 锚点按 (clip,universe) 确定性缓存，且随 clip 失效/结构变更被显式清除(958/1069)，
        # 故大缓存只避免重算、绝不返回陈旧值。旧 256 上限在随机跳播时抖动(工作集≈clip×universe)，
        # 每次 miss 都要遍历整段 frames(np.asarray/帧)。上限提到覆盖真实工作集，消除抖动重算。
        if len(self._clip_anchor_cache) > self._anchor_cache_cap:
            self._evict_oldest(self._clip_anchor_cache)
        return anchor

    def _get_fade_out_target(self, universe: int, proj=None) -> np.ndarray:
        """Return the idle frame a clip should fade out toward for one universe."""
        universe = int(universe)
        default_entry = None
        nm = self._nm
        if nm is not None:
            try:
                default_entry = (getattr(nm, "_channel_defaults", {}) or {}).get(universe)
            except Exception:
                default_entry = None
        default_sig = None
        if default_entry is not None:
            try:
                data, active = default_entry
                data_arr = np.asarray(data, dtype=np.uint8).reshape(-1)
                active_arr = np.asarray(active, dtype=bool).reshape(-1)
                default_sig = (
                    int(data_arr.size),
                    int(active_arr.size),
                    int(np.sum(data_arr[:min(data_arr.size, 512)].astype(np.uint32))),
                    int(np.count_nonzero(active_arr[:min(active_arr.size, 512)])),
                )
            except Exception:
                default_sig = ("custom", id(default_entry))
        cache_key = (
            "__fade_out_target__",
            universe,
            id(proj),
            len(getattr(proj, "fixtures", []) or []) if proj is not None else 0,
            len(getattr(proj, "fixture_defs", []) or []) if proj is not None else 0,
            default_sig,
        )
        cached = self._clip_fade_geometry_cache.get(cache_key)
        if cached is not None:
            return np.asarray(cached, dtype=np.float32).copy()
        target = np.zeros(512, dtype=np.float32)
        default_active = np.zeros(512, dtype=bool)

        if default_entry is not None:
            try:
                data, active = default_entry
                data_arr = np.asarray(data, dtype=np.float32)
                if data_arr.shape != (512,):
                    fixed = np.zeros(512, dtype=np.float32)
                    n = min(data_arr.size, 512)
                    if n > 0:
                        fixed[:n] = data_arr.reshape(-1)[:n]
                    data_arr = fixed
                active_arr = np.asarray(active, dtype=bool)
                if active_arr.shape != (512,):
                    fixed_active = np.zeros(512, dtype=bool)
                    n = min(active_arr.size, 512)
                    if n > 0:
                        fixed_active[:n] = active_arr.reshape(-1)[:n]
                    active_arr = fixed_active
                target[active_arr] = data_arr[active_arr]
                default_active |= active_arr
            except Exception:
                pass

        motor_mask = self._build_motor_channel_mask(universe, proj)
        if (motor_mask).any():
            target[motor_mask & ~default_active] = 128.0
        self._store_clip_fade_geometry_cache(cache_key, tuple(float(v) for v in target))
        return target

    def _project_ordered_tracks(self, proj=None) -> list:
        tracks = list(getattr(proj, "tracks", []) or []) if proj is not None else []
        if not tracks:
            return []
        by_parent = {}
        for track in tracks:
            by_parent.setdefault(getattr(track, "parent_id", "") or "", []).append(track)
        ordered = []

        def _walk(parent_id):
            for track in by_parent.get(parent_id, []):
                ordered.append(track)
                if bool(getattr(track, "is_folder", False)):
                    _walk(getattr(track, "id", ""))

        _walk("")
        seen = {id(track) for track in ordered}
        ordered.extend([track for track in tracks if id(track) not in seen])
        return ordered

    def _track_or_ancestor_muted(self, track, track_by_id: dict[str, object]) -> bool:
        if bool(getattr(track, "muted", False)):
            return True
        seen: set[str] = set()
        parent_id = getattr(track, "parent_id", "") or ""
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = track_by_id.get(str(parent_id))
            if parent is None:
                break
            if bool(getattr(parent, "muted", False)):
                return True
            parent_id = getattr(parent, "parent_id", "") or ""
        return False

    def _track_or_ancestor_soloed(self, track, track_by_id: dict[str, object]) -> bool:
        if bool(getattr(track, "solo", False)):
            return True
        seen: set[str] = set()
        parent_id = getattr(track, "parent_id", "") or ""
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = track_by_id.get(str(parent_id))
            if parent is None:
                break
            if bool(getattr(parent, "solo", False)):
                return True
            parent_id = getattr(parent, "parent_id", "") or ""
        return False

    def _transition_anchor_tracks(self, proj=None) -> list[tuple[int, object]]:
        ordered_tracks = self._project_ordered_tracks(proj)
        track_by_id = {
            str(getattr(track, "id", "")): track
            for track in ordered_tracks
            if getattr(track, "id", None)
        }
        any_solo = any(
            self._track_or_ancestor_soloed(track, track_by_id)
            for track in ordered_tracks
            if not bool(getattr(track, "is_folder", False))
            and not self._track_or_ancestor_muted(track, track_by_id)
        )
        out: list[tuple[int, object]] = []
        for track_order, track in enumerate(ordered_tracks):
            if bool(getattr(track, "is_folder", False)):
                continue
            if self._track_or_ancestor_muted(track, track_by_id):
                continue
            if any_solo and not self._track_or_ancestor_soloed(track, track_by_id):
                continue
            out.append((int(track_order), track))
        return out

    def _fade_in_anchor_dependency_key(self, clip: Clip, universe: int) -> tuple:
        return (str(getattr(clip, "id", "") or id(clip)), int(universe))

    def _fade_in_anchor_ready_context(self, clip: Clip, universe: int, track=None, proj=None) -> tuple:
        tracks = []
        if proj is not None:
            try:
                tracks = list(getattr(proj, "tracks", []) or [])
            except Exception:
                tracks = []
        elif track is not None:
            tracks = [track]
        return (
            round(float(getattr(clip, "start_time", 0.0) or 0.0), 6),
            self._clip_static_signature(clip),
            self._track_filter_signature_for_universe(track, int(universe)),
            self._cached_project_clip_layout_signature(tracks),
        )

    def _transition_candidate_signature(self, candidate_info: list[tuple]) -> tuple:
        return tuple(
            (
                str(getattr(candidate, "id", "") or id(candidate)),
                int(track_order),
                round(float(start_t), 6),
                round(float(end_t), 6),
                self._clip_static_signature(candidate),
                self._clip_transition_content_signature(candidate),
                self._track_filter_signature_for_universe(track, int(universe)),
            )
            for candidate, track, track_order, start_t, end_t, universe in candidate_info
        )

    def _find_previous_transition_clips(
            self, clip: Clip, universe: int, track=None, proj=None,
    ) -> list[tuple[Clip, object, int, float, float, int]]:
        clip_start = float(getattr(clip, "start_time", 0.0) or 0.0)
        seam_tol = 1e-6
        candidates: list[tuple[Clip, object, int, float, float, int]] = []
        track_items = self._transition_anchor_tracks(proj)
        if not track_items and track is not None:
            track_items = [(0, track)]
        for track_order, source_track in track_items:
            try:
                track_universes = [
                    int(u)
                    for u in (
                        getattr(source_track, "universes", None)
                        or [getattr(source_track, "universe", 1)]
                    )
                ]
            except Exception:
                track_universes = [int(universe)]
            for candidate in (getattr(source_track, "clips", []) or []):
                if candidate is clip:
                    continue
                try:
                    c_start = float(getattr(candidate, "start_time", 0.0) or 0.0)
                    c_dur = max(0.0, float(getattr(candidate, "duration", 0.0) or 0.0))
                except Exception:
                    continue
                if c_dur <= 0.0 or c_start >= clip_start - 1e-9:
                    continue
                c_end = c_start + c_dur
                overlaps = c_start <= clip_start <= c_end + seam_tol
                # ★ 2026-09-19：hold 跨任意间隔持续输出 → 必须是淡入候选（旧判据
                #   只认紧邻，隔间隔就退回默认归位值：电机 128）。
                _hold_into_gap = (self._clip_hold_mode(candidate) != "none"
                                  and float(clip_start) - float(c_end) >= -seam_tol)
                if not overlaps and not _hold_into_gap:
                    continue
                clip_universes = getattr(candidate, "clip_universes", None) or track_universes
                frame_universes = sorted(self._clip_frame_universes(candidate))
                target_universes = sorted(set(int(u) for u in (clip_universes or frame_universes or track_universes)))
                if int(universe) not in target_universes:
                    continue
                candidates.append((candidate, source_track, int(track_order), c_start, c_end, int(universe)))
        return candidates

    def _build_cross_track_fade_in_anchor(
        self,
        clip: Clip,
        universe: int,
        track=None,
        proj=None,
        candidates: Optional[list[tuple[Clip, object, int, float, float, int]]] = None,
    ) -> tuple[np.ndarray, set[str]]:
        from .native_anchor_preparation import prepare
        universe = int(universe)
        clip_start = float(getattr(clip, "start_time", 0.0) or 0.0)
        # ★勿改 clip 首帧：静态内容锚点==内容值会使淡入恒等于 raw（06-19 实报）。
        anchor = self._get_fade_out_target(universe, proj=proj)
        best_scores = np.full(512, -np.inf, dtype=np.float64)
        deps: set[str] = set()
        candidates = candidates if candidates is not None else self._find_previous_transition_clips(clip, universe, track=track, proj=proj)
        for candidate, source_track, track_order, c_start, _c_end, _u in candidates:
            prev_data, prev_active = self._sample_transition_clip_output(
                candidate,
                universe,
                clip_start,
                track=source_track,
                proj=proj,
                ignore_fade_out=True,
            )
            if prev_data is None or prev_active is None or not mask_count(prev_active):
                continue
            deps.add(str(getattr(candidate, "id", "") or id(candidate)))
            try:
                source_duration = self._clip_source_duration(
                    candidate,
                    prepare(0, float(getattr(candidate, "duration", 0.0) or 0.0)),
                )
                source_t = prepare(1, source_duration)
            except Exception:
                source_t = prepare(2, float(_c_end), float(c_start))
            compiled = self._compiled_clip_universe(
                candidate,
                universe,
                track=source_track,
                proj=proj,
            )
            scores = self._compiled_clip_channel_change_scores(
                candidate,
                universe,
                source_t,
                clip_start,
                c_start,
                np.asarray(prev_active, dtype=bool),
                compiled,
                hold_extension=self._clip_hold_mode(candidate) != "none",
            )
            # ★★ 2026-09-20 修复：改用与渲染同序的 start 降序仲裁（原为候选列表下标×1e9，
            #   旧 hold 会抢走紧邻动作的通道 → "9572 进入时部分电机迅速到 128"）。R109。
            tie = prepare(3, float(c_start), float(track_order))
            update_anchor(anchor, best_scores, (prev_data, prev_active, scores), tie)
        return anchor, deps

    def _get_fade_in_anchor(
        self, clip: Clip, universe: int,
        track=None, proj=None
    ) -> np.ndarray:
        """Return the frame a clip fades in from.

        Adjacent clips must inherit the previous authored seam output for each
        channel, even when that output comes from another track.  The idle target
        is only a fallback for channels that no previous clip owns.
        """
        from .transition_anchor_causality import causal_anchor
        causal = causal_anchor(self, clip, universe, track=track, proj=proj)
        if causal is not None:
            return causal
        universe = int(universe)
        clip_id = str(getattr(clip, "id", "") or id(clip))
        ready_key = (clip_id, "fade_in_anchor_ready", int(universe))
        ready_context_key = (clip_id, "fade_in_anchor_ready_context", int(universe))
        ready_context = self._fade_in_anchor_ready_context(clip, universe, track=track, proj=proj)
        ready = self._fade_in_anchor.get(ready_key)
        if ready is not None:
            if self._fade_in_anchor.get(ready_context_key) == ready_context:
                return ready
            self._fade_in_anchor.pop(ready_key, None)
            self._fade_in_anchor.pop(ready_context_key, None)
        clip_start = float(getattr(clip, "start_time", 0.0) or 0.0)
        candidates = self._find_previous_transition_clips(clip, universe, track=track, proj=proj)
        candidate_sig = self._transition_candidate_signature(candidates)
        cache_key = (clip_id, "fade_in_anchor", int(universe), ready_context, candidate_sig)
        cached = self._fade_in_anchor.get(cache_key)
        if cached is not None:
            self._fade_in_anchor[ready_key] = cached
            self._fade_in_anchor[ready_context_key] = ready_context
            return cached

        anchor, deps = self._build_cross_track_fade_in_anchor(
            clip,
            universe,
            track=track,
            proj=proj,
            candidates=candidates,
        )
        self._fade_in_anchor[cache_key] = anchor
        self._fade_in_anchor[ready_key] = anchor
        self._fade_in_anchor[ready_context_key] = ready_context
        dep_key = self._fade_in_anchor_dependency_key(clip, universe)
        deps.add(clip_id)
        self._fade_in_anchor_deps[dep_key] = deps
        # ★ 性能：146+ clip × 1-2 universe 的淡入锚 ≈ 150-300 对，每对 3 键
        #   (cache/ready/context)。加载期预热全量建对后可达 430+ 对 = 1290+ 键，
        #   1024 会让预热自相淘汰(边界 tick 全部重建)。4096 覆盖真实工作集，
        #   内存 ≈ 1365×2KB ≈ 2.8MB 有界（仅容量，零逻辑变化）。
        evict_fade_anchor_cache(self)
        return anchor

    def _find_previous_transition_clip(
        self,
        clip: Clip,
        track,
        clip_start: float,
    ) -> Optional[tuple[Clip, float, float]]:
        if track is None:
            return None
        seam_tol = 1e-6
        best: Optional[tuple[Clip, float, float]] = None
        best_score: tuple[float, float] = (-float("inf"), -float("inf"))
        for candidate in (getattr(track, "clips", []) or []):
            if candidate is clip:
                continue
            try:
                c_start = float(getattr(candidate, "start_time", 0.0) or 0.0)
                c_dur = max(0.0, float(getattr(candidate, "duration", 0.0) or 0.0))
            except Exception:
                continue
            if c_dur <= 0.0 or c_start >= clip_start - 1e-9:
                continue
            c_end = c_start + c_dur
            overlaps = c_start <= clip_start <= c_end + seam_tol
            _hold_into_gap = (self._clip_hold_mode(candidate) != "none"
                              and float(clip_start) - float(c_end) >= -seam_tol)
            if not overlaps and not _hold_into_gap:
                continue
            score = (c_start, c_end)
            if score > best_score:
                best = (candidate, c_start, c_end)
                best_score = score
        return best

    def _sample_transition_clip_output(
        self,
        clip: Clip,
        universe: int,
        timeline_t: float,
        track=None,
        proj=None,
        ignore_fade_out: bool = False,
    ) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        duration = max(0.0, float(getattr(clip, "duration", 0.0) or 0.0))
        if duration <= 0.0:
            return None, None
        start_t = float(getattr(clip, "start_time", 0.0) or 0.0)
        local_t = max(0.0, float(timeline_t) - start_t)
        hold_mode = self._clip_hold_mode(clip)
        hold_extension = bool(hold_mode != "none" and local_t >= duration)
        if local_t > duration + 0.08 and not hold_extension:
            return None, None
        source_duration = self._clip_source_duration(clip, duration)
        sample_local_t = min(local_t, max(0.0, duration - 1e-4))
        source_t, _sequential_pause = self._clip_sequential_source_time(
            clip, sample_local_t, source_duration)
        source_t, run_active = self._clip_run_mode_source_time(
            clip, source_t, source_duration
        )
        if not run_active and hold_mode == "none":
            return None, None
        if hold_extension:
            hold_elapsed = max(0.0, local_t - duration)
            if hold_mode == "last_cycle":
                source_t = max(0.0, source_duration - 1e-4 + hold_elapsed * self._clip_playback_rate(clip))
                source_t = self._clip_auto_hold_cycle_time(clip, source_t)
            else:
                source_t = max(0.0, source_duration - 1e-4)
        else:
            source_t = min(source_t, max(0.0, source_duration - 1e-4))

        fixture_defs_by_id = {
            str(getattr(fd, "id", "")): fd
            for fd in (getattr(proj, "fixture_defs", []) or [])
            if getattr(fd, "id", None)
        } if proj is not None else {}
        compiled = self._compiled_clip_universe(
            clip,
            int(universe),
            track=track,
            proj=proj,
            fixture_defs_by_id=fixture_defs_by_id,
        )
        raw = self._compiled_clip_frame_data(compiled, source_t)
        if raw is None:
            return None, None
        frame_index = self._compiled_clip_frame_index(compiled, source_t)
        raw = self._prepare_compiled_clip_frame_for_output_basis(
            clip,
            int(universe),
            compiled,
            frame_index,
            np.asarray(raw, dtype=np.uint8),
        )
        raw = self._apply_clip_motor_force_frame(
            clip,
            universe=int(universe),
            source_t=source_t,
            source_duration=source_duration,
            raw=raw,
            compiled=compiled,
            proj=proj,
        )

        fade_local_t = min(local_t, duration)
        fade_out_dur = max(0.0, float(getattr(clip, "fade_out", 0.0) or 0.0))
        seq_delay_out_c = max(0.0, float(getattr(clip, "seq_delay_out", 0.0) or 0.0))
        out_region = fade_out_dur + (
            seq_delay_out_c if self._get_clip_fade_out_direction(clip) != "none" else 0.0
        )
        is_fade_out_window = (
            (not hold_extension)
            and out_region > 1e-4
            and fade_local_t > duration - out_region
        )
        is_fade_out = is_fade_out_window and not bool(ignore_fade_out)
        if is_fade_out:
            target = self._get_fade_out_target(int(universe), proj=proj)
            if self._clip_has_directional_fade(clip):
                gains = self._clip_channel_fade_gains(
                    clip,
                    fade_local_t,
                    int(universe),
                    list(compiled.get("u_fixtures", []) or []),
                    track=track,
                    fixture_defs_by_id=fixture_defs_by_id,
                    proj=proj,
                    compiled=compiled,
                )
                data = blend_frame(raw, gains, target)
            else:
                gain = self._clip_fade_gain(clip, fade_local_t)
                data = blend_frame(raw, gain, target)
        elif ignore_fade_out and is_fade_out_window:
            data = blend_frame(raw, 1.)
        elif hold_extension:
            data = blend_frame(raw, 1.)
        elif self._clip_has_directional_fade(clip):
            gains = self._clip_channel_fade_gains(
                clip,
                fade_local_t,
                int(universe),
                list(compiled.get("u_fixtures", []) or []),
                track=track,
                fixture_defs_by_id=fixture_defs_by_id,
                proj=proj,
                compiled=compiled,
            )
            data = blend_frame(raw, gains)
        else:
            data = blend_frame(raw, self._clip_fade_gain(clip, fade_local_t))
        data, audio_active = finalize_clip_with_audio(self, clip, int(universe), data, (track, proj, timeline_t, compiled, data, None, hold_extension), local_t=fade_local_t)

        active = self._clip_active_mask_for_rendered_raw(
            clip,
            int(universe),
            data.astype(np.uint8),
            compiled.get("base_active", self._clip_channel_active_mask(clip, int(universe))),
            source_t=transition_source_mask_time(clip, universe, source_t, local_t=local_t, duration=duration, hold_extension=hold_extension), hold_extension=hold_extension,
        )
        if bool(getattr(clip, "light_tracking_enabled", False)):
            active = active | np.asarray(
                compiled.get("light_mask", np.zeros(512, dtype=bool)),
                dtype=bool,
            )
        rule_active = compiled.get("rule_active")
        if rule_active is not None:
            rule_active = np.asarray(rule_active, dtype=bool)
            active = filter_active_mask(clip, universe, active, rule_active)
        return data.astype(np.float32), np.asarray(active if audio_active is None else audio_active, dtype=bool)

    def _build_motor_channel_mask(self, universe: int, proj) -> np.ndarray:
        """[LTP 过渡] 返回该 universe 下所有电机通道（motor_x/y/z）的 bool[512] 掉码。

        通过 proj.fixtures + proj.fixture_defs 计算每个 DMX 地址对应的通道类型。
        如果项目无设备信息，返回全 False 掩码（即所有通道起点为 0）。
        """
        universe = int(universe)
        # B(R51 身份缓存纪律): 旧 cache_key 只有 id(proj)+len, 连列表 id 都没有 —
        # 值里存 proj/fixtures/fixture_defs 强引用, 命中处 `is` 复核后使用。
        # （多台设备但列表被替换/对象回收后 id() 复用都不会再错误命中。）
        fixtures = getattr(proj, "fixtures", []) or []
        fixture_defs = getattr(proj, "fixture_defs", []) or []
        cache_key = (
            "__motor_channel_mask__",
            universe,
            id(proj),
            len(fixtures),
            len(fixture_defs),
        )
        cached = self._clip_fade_geometry_cache.get(cache_key)
        if (cached is not None and cached[0] is proj
                and cached[1] is fixtures and cached[2] is fixture_defs):
            return np.asarray(cached[3], dtype=bool).copy()
        MOTOR_TYPES = {"motor_x", "motor_y", "motor_z"}
        mask = np.zeros(512, dtype=bool)
        if proj is None:
            return mask
        defs_by_id = {str(getattr(fd, "id", "")): fd for fd in fixture_defs}
        for fi in fixtures:
            if int(getattr(fi, "universe", 1)) != universe:
                continue
            start = int(getattr(fi, "start_channel", 1)) - 1  # 转 0-indexed
            if not (0 <= start < 512):
                continue
            fd = defs_by_id.get(str(getattr(fi, "def_id", "")))
            if fd is None:
                continue
            ch_types: list = getattr(fd, "channels", []) or []
            for offset, ch_type in enumerate(ch_types):
                addr = start + offset
                if addr >= 512:
                    break
                if str(ch_type).lower() in MOTOR_TYPES:
                    mask[addr] = True
        self._store_clip_fade_geometry_cache(
            cache_key, (proj, fixtures, fixture_defs, tuple(bool(v) for v in mask)))
        return mask

    # ── Clip 自动保持/延长循环 ────────────────────────────────────────────────

    def _clip_auto_hold_cycle_time(self, clip: Clip, local_t: float) -> float:
        """把延长/保持区间映射回最后一个可用动作周期。

        旧逻辑在 Clip 被拉长或开启保持后，会在最后一帧之后一直返回最后一帧。
        这里改为：当 Clip 内存在多帧动态动作，并且播放位置已经超过实际记录帧的
        最后时间戳时，按记录帧覆盖的最后一段周期循环补全；如果只有单帧或所有帧
        时间戳相同，则仍然等价于固定帧保持，保持旧效果不变。
        """
        frames = getattr(clip, "frames", None) or []
        if len(frames) <= 1:
            return max(0.0, float(local_t))

        timestamps = []
        for frame in frames:
            try:
                ts = float(getattr(frame, "timestamp", 0.0) or 0.0)
            except Exception:
                ts = 0.0
            if ts >= 0.0:
                timestamps.append(ts)
        if len(timestamps) <= 1:
            return max(0.0, float(local_t))

        first_ts = min(timestamps)
        last_ts = max(timestamps)
        period = last_ts - first_ts
        if period <= 1e-6:
            return max(0.0, float(local_t))

        local_t = max(0.0, float(local_t))
        if local_t <= last_ts:
            return local_t

        # “最后一段周期”默认取当前 Clip 已记录动作的完整时间跨度。这样效果生成、
        # 摇杆录制等多帧动作在拉伸 Clip 或 Hold 延伸后会按周期自动补全；固定帧
        # 因无法形成周期，会自然回退到最后一帧保持。
        return first_ts + ((local_t - first_ts) % period)

    # ── 帧查找 ──────────────────────────────────────────────────────────────

    def _find_frame(self, clip: Clip, local_t: float, universe: Optional[int] = None) -> Optional[DMXFrame]:
        frames = getattr(clip, "frames", None) or []
        if universe is not None:
            universe = int(universe)
            cache_key = str(getattr(clip, "id", "") or id(clip))
            frame_count = len(frames)
            cached = self._clip_frame_index_cache.get(cache_key)
            if cached is None or cached[0] != frame_count:
                by_universe: Dict[int, List[DMXFrame]] = {}
                for f in frames:
                    try:
                        u = int(getattr(f, "universe", 1) or 1)
                    except Exception:
                        u = 1
                    by_universe.setdefault(u, []).append(f)
                for lst in by_universe.values():
                    lst.sort(key=lambda f: float(getattr(f, "timestamp", 0.0)))
                cached = (frame_count, by_universe)
                self._clip_frame_index_cache[cache_key] = cached
                if len(self._clip_frame_index_cache) > 256:
                    self._evict_oldest(self._clip_frame_index_cache)
            frames = cached[1].get(universe, [])
        if not frames:
            return None
        if len(frames) == 1:
            return frames[0]

        if universe is None:
            frames = sorted(frames, key=lambda f: float(getattr(f, "timestamp", 0.0)))
        lo, hi = 0, len(frames) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if frames[mid].timestamp < local_t:
                lo = mid + 1
            else:
                hi = mid

        if lo >= len(frames):
            return frames[-1]
        if lo == 0:
            return frames[0]

        prev_frame = frames[lo - 1]
        next_frame = frames[lo]
        if local_t - prev_frame.timestamp < next_frame.timestamp - local_t:
            return prev_frame
        return next_frame

    # ── 兼容老接口 ──────────────────────────────────────────────────────────



# ── 渲染期间的 GC 调优（模块级函数：便于单测，不占类体）────────────────────────
def _freeze_gc_for_render() -> dict:
    """渲染前冻结 GC 永久代并关分代 GC；返回原状态供收尾恢复。

    ★ 实测（2026-09-14，1992 缓存帧 / 7150 逐帧调用 / CE active）：
        不冻结 frames=48.51s → 冻结后 frames=**37.13s（-23%）**。
      原因：逐帧渲染是纯 Python 密集回退路径（带 XY 数据 → 资格否决 → K2 逐帧
      Python），每帧产生大量临时对象 → 分代 GC 反复全堆扫描；`freeze()` 把加载期
      留下的数十万长寿命对象移出扫描范围，`disable()` 期间不再回收（短时批处理，
      收尾统一恢复）。
    """
    state = {"was_enabled": True, "frozen": False}
    try:
        state["was_enabled"] = bool(gc.isenabled())
        gc.collect()          # 先回收可回收的，别把垃圾也冻成"永久对象"
        gc.freeze()
        state["frozen"] = True
        gc.disable()
    except Exception:
        pass
    return state


def _restore_gc_after_render(state: dict) -> None:
    """渲染收尾恢复 GC 原状态（失败静默 —— 绝不影响渲染结果返回）。"""
    try:
        if (state or {}).get("was_enabled", True):
            gc.enable()
        gc.collect()
    except Exception:
        pass


def _render_all_frames(engine, proj, opts: dict):
    """整段逐帧渲染（含**渲染期 GC 冻结** —— 实测 frames 48.51s → 37.13s）。

    冻结/恢复包在这里而不是逐帧循环里：职责清晰，`_render_timeline_frame_range`
    保持原样（并行 worker 各自调用它时也不受影响）。
    `opts` 收拢参数（满足门禁的位置参数 ≤5）。
    """
    n = int(opts.get("frame_count") or 0)
    # Background jobs share the GUI heap; a forced full collection stalls it.
    gc_state = None if getattr(engine, "_timeline_bake_owned", False) else _freeze_gc_for_render()
    try:
        return engine._render_timeline_frame_range(
            proj, 0, n, float(opts.get("interval") or 0.023), float(opts.get("end") or 0.0),
            n,
            progress_callback=opts.get("progress_callback"),
            progress_start=float(opts.get("progress_start") or 0.0),
            progress_end=float(opts.get("progress_end") or 1.0),
        )
    finally:
        if gc_state is not None:
            _restore_gc_after_render(gc_state)



# ── 结构失效正文（模块级函数：PlaybackEngine 是上帝类，禁新增方法）────────
def _purge_lt_scope_caches(engine, keyset) -> None:
    """按 clip id 清灯跟踪目标/作用域缓存（键首元素为 clip id）。"""
    for cache in (engine._lt_mask_cache, engine._lt_scope_cache):
        for key in [key for key in cache if str(key[0]) in keyset]:
            del cache[key]


def _invalidate_all_caches_impl(engine) -> None:
    """`invalidate_all_caches` 正文（R178 P2-12 从方法拆出；self→engine 纯搬运）。"""
    engine.unlock_timeline_render_cache()
    from .timeline_source_rebind import purge_check_context
    recheck = purge_check_context(engine)
    engine._clip_active_channels_cache.clear()
    # P2-2: 换工程/undo 残留已解码帧数据(per-universe 索引, 至冬 ~65MB)。
    # 写点按 clip_id+frame_count 惰性重建, 整表清空安全(与 active_channels 同源)。
    engine._clip_frame_index_cache.clear()
    getattr(engine, "_clip_mask_prep_cache", {}).clear()
    engine._clip_anchor_cache.clear()
    engine._light_tracking_state.clear()
    engine._light_tracking_time.clear()
    engine._light_tracking_color_cache.clear()
    engine._lt_warned.clear()
    engine._fade_in_anchor.clear()
    engine._fade_in_anchor_deps.clear()
    from .transition_anchor_causality import clear_anchor_state
    clear_anchor_state(engine)
    engine._clip_sequential_remap_cache.clear()
    engine._clip_fade_geometry_cache.clear()
    engine._clip_xy_reference_cache.clear()
    engine._clip_render_cache.clear()
    engine._clip_compiled_cache.clear()
    engine._clip_compiled_fast.clear()
    engine._clip_render_mask_cache.clear()
    engine._clip_render_mask_any_cache.clear()
    from .timeline_incremental_checkpoint import invalidate_stores
    if recheck is None:
        invalidate_stores(engine)
        engine._timeline_render_frame_cache_signature = None
        engine._timeline_render_frame_cache_end = 0.0
    engine._clip_static_signature_cache.clear()
    engine._clip_motor_force_profile_cache.clear()
    # ★ 正确性(审查A)：工程级预索引一并失效——R4 配接表重导入会就地修改
    #   fixture 对象(universe/start/def_id, 列表身份与长度不变)，签名无法
    #   检测，必须随结构失效清空重建。
    engine._fx_by_uni = None
    engine._fx_by_uni_sig = None
    engine._fx_by_id = None
    engine._fx_by_id_sig = None
    engine._fx_valid_ids = None
    engine._global_order = None
    engine._global_order_sig = None
    engine._clip_recorded_source_span_cache.clear()
    engine._clip_recorded_cycle_cache.clear()
    engine._clip_transition_content_sig_cache.clear()
    engine._clip_frame_universes_cache.clear()
    # 灯跟踪结构 memo + 通道分类缓存:配接/设备结构变更后必失效,否则原地改设备
    # universe/def_id/start_channel 后仍按旧结构分桶,灯跟踪写错地址(reviewer #1)。
    retire_structure(engine)
    engine._playback_active_index = None
    engine._playback_active_index_dirty = True
    engine._precompile_dirty = True
    engine._precompile_signature = None
    engine._clip_layout_signature_cache_key = None
    engine._clip_layout_signature_cache = None
    engine._clip_layout_signature_dirty = True
    engine._render_struct_cache_key = None
    engine._render_struct_cache = None
    engine._fixture_defs_index_cache_key = None
    engine._fixture_defs_index_cache = None
    engine._fixtures_by_id_index_cache_key = None
    engine._fixtures_by_id_index_cache = None
    engine._clip_base_order_mask_cache.clear()
    engine._clip_seq_timing_cache.clear()
    engine._playback_index_check_time = -1.0
    engine._hold_boundary_latch.clear()
    # R14: undo/redo/删除 clip 等全量结构失效 → 保持消费状态一并清空,
    # 否则被删/被改的覆写方残留消费压制新结构下的 hold(报告 4.6)。
    engine._hold_consumed_masks.clear()
    engine._takeover_consumed_masks.clear()
    engine._last_output_universes = set()
    engine._last_record_source_outputs_by_universe = {}
    # 脏标记增量渲染: 全量结构失效 → 旧 memo 全部不可复用
    engine._dirty_render_item_memo.clear()
    engine._dirty_render_uni_memo.clear()
    # 清空 clip 播放/预览/旧兼容层 + linker 层，防止暂停状态下旧帧/关联器残留持续输出
    if engine._nm is not None and hasattr(engine._nm, 'clear_layer'):
        for _layer in ('clip_playback', 'clip_preview', 'timeline', 'linker'):
            try:
                engine._nm.clear_layer(_layer)
            except Exception:
                # C: 清层失败曾静默 → 残留旧帧继续输出。结构失效为非热路径,
                # 直接 exception 可见。
                log.exception(
                    "clear_layer(%s) failed during structure invalidation", _layer)
    if recheck is not None:
        from .timeline_cache_notifications import notify_project_change
        notify_project_change(*recheck, engine=engine)
# ── R178 时间线后台自动烘焙（模块级函数：PlaybackEngine 是上帝类，禁新增方法）──
def _gen_idle_poll(engine) -> bool:
    """gen 线程非播放分支（R178 审计 P1-6，从 _gen_loop 拆出保行数门禁）。

    顺带消费 pending-invalidate 队列，并继续混音其它实时作者层。仍由本线程
    串行处理，等待原帧间隔；返回 True 表示收到停止请求。
    """
    from .gen_thread_lifecycle import mix_while_waiting
    engine._gen_drain_pending_invalidate()
    return mix_while_waiting(engine)


class TimelineBakeAbort(Exception):
    """autobake 让位中断（播放开始/编辑失效）；经烘焙进度回调抛出。

    `_render_timeline_frame_range` 的 finally 会完整恢复 NM/层/位置/锁状态
    （R167 崩溃中断已验证该恢复路径），半成品缓存保持解锁惰性态。
    """


def bake_timeline_now(engine, proj, opts: dict) -> str:
    """Original sequential bake in an owned context; live transport stays live."""
    from .timeline_bake_context import bake_in_owned_context
    if proj is None:
        proj = _project_of(engine)
    if proj is None:
        return "aborted"
    engine._timeline_render_cache_locked = False
    return bake_in_owned_context(engine, proj, opts)


def _report_precompile_progress(callback, info):
    try:
        callback(*info)
    except TimelineBakeAbort:
        raise
    except Exception:
        log.debug("project precompile progress callback failed", exc_info=True)


def _bake_timeline_frames_locked(engine, proj, opts: dict, signature: tuple,
                                 runtime: tuple) -> str:
    """Original full sequential build; automatic bake calls it on its owned engine."""
    progress_cb = opts.get("progress_callback")
    should_abort = opts.get("should_abort") or (lambda: False)
    engine._build_timeline_render_frame_cache(
        proj, signature,
        progress_callback=progress_cb,
        progress_start=0.25,
        progress_end=0.98,
    )
    if should_abort():
        return "aborted"
    tracks = list(getattr(proj, "tracks", []) or [])
    # 新鲜度闸门：烘焙期间发生编辑（invalidate 链置 dirty）或布局/运行态漂移
    # → 不上锁。缓存属性里留有的是构建产物，但保持解锁惰性态（服务门在
    # `_render_from_timeline_frame_cache` 入口判 locked=False 直接回退实时）。
    dirty = bool(getattr(engine, "_precompile_dirty", True))
    if dirty or engine._cached_project_clip_layout_signature(tracks) != signature:
        return "stale"
    if engine._timeline_render_runtime_signature_value() != runtime:
        return "stale"
    engine._timeline_render_lock_signature = signature
    engine._timeline_render_runtime_signature = runtime
    engine._timeline_render_lock_misses = 0
    engine._timeline_render_cache_locked = True
    if not bool(opts.get("was_locked", False)):
        engine._last_render_time = None
    engine._report_timeline_render_progress(
        progress_cb, "complete", 1, 1, "时间线自动预渲染完成，缓存已锁定", 1.0)
    return "done"


# ── 拖长"按周期扩展"（模块级函数：PlaybackEngine 是上帝类，禁新增方法）────────
def _resize_cycle_time(engine, clip, source_t: float):
    """拖长后在记录跨度之外**按周期**映射 source_t；未超出返回 None。

    用户 2026-09-14："如果是往后拉，按照周期自动扩展效果往后"、"不再去加速它了"。
    周期 = 记录帧的 `[first_ts, last_ts]` 跨度（与 `_clip_auto_hold_cycle_time` 同一定义，
    保证"拉伸/保持后按周期补全"在引擎里只有一套语义）。

    返回 None（调用方走原 clamp 路径，零行为变化）：
      · 帧数 ≤1 / 周期 ≤1e-6；· source_t 未超过记录的最后时间戳（正常播放段）。
    """
    span = _recorded_cycle(engine, clip)
    if span is None:
        return None
    first_ts, period, last_ts = span
    t = max(0.0, float(source_t))
    if t <= last_ts:
        return None
    return first_ts + ((t - first_ts) % period)


def _recorded_cycle(engine, clip):
    """`(first_ts, period, last_ts)`（带缓存）；无法构成周期返回 None。

    ★ id 键缓存同时持对象强引用（项目 id 铁律），并随 span 缓存的失效点一起清理。
    """
    clip_id = engine._clip_cache_id(clip)
    cached = engine._clip_recorded_cycle_cache.get(clip_id)
    if cached is None:
        cached = _compute_recorded_cycle(clip)
        engine._clip_recorded_cycle_cache[clip_id] = cached
    return cached or None


def _compute_recorded_cycle(clip):
    """从帧时间戳算 `(first_ts, period, last_ts)`；单帧/同刻返回 None。"""
    frames = getattr(clip, "frames", None) or []
    if len(frames) <= 1:
        return None
    lo, hi = _frame_ts_bounds(frames)
    if lo is None or hi is None:
        return None
    period = hi - lo
    return (lo, period, hi) if period > 1e-6 else None


def _frame_ts_bounds(frames):
    """帧时间戳的 `(min, max)`（忽略非法/负数）；无有效帧返回 `(None, None)`。"""
    lo = hi = None
    for frame in frames:
        try:
            ts = float(getattr(frame, "timestamp", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if ts < 0.0:
            continue
        lo = ts if lo is None else min(lo, ts)
        hi = ts if hi is None else max(hi, ts)
    return lo, hi


def _normal_cycle_time(engine, clip, source_t: float, mode: str,
                       source_duration: float):
    """normal 模式下"拖长按周期扩展"的包装；不适用返回 None（调用方走 clamp）。

    ★★ 2026-09-14 用户定案（拖长不变速）：超出**源记录跨度**的部分按周期补全，
    而不是冻结在最后一帧。背景：拖右缘曾把时长折算成 playback_rate（时间拉伸 =
    变速）；改为不变速后 rate=1 且 duration > 源跨度 → 后半段会撞上 end_t 冻结。
    · 只在 source_t 超过记录的最后时间戳时介入 → 正常播放（rate=1、时长==源跨度）
      走原路径逐字节不变；
    · `resize_cycle=False`（右击菜单显式变速过）保持旧语义。

    ★★ R173 修复（尾隙回卷）：只有 clip **真的被拖长**（时长×速率 > 源时长）才按周期
      扩展。修前判据只看"source_t 越过记录末帧"，而录制停在 clip 末端之前几毫秒是常态
      （三个真实工程所有 clip 的尾隙 0~0.11s）→ 没拖长的 clip 在自己最后 1~5 帧回卷到
      **首帧值**。实测水仙十字 bf69f97d@55.80：末帧 1.9236 < 时长 1.9675，整域 87 通道
      62→136→62 闪一帧。没拖长时尾隙改走 clamp（保持末帧）；拖长的 clip 逐字节不变。
    """
    if mode != "normal" or not bool(getattr(clip, "resize_cycle", True)):
        return None
    if not _clip_extends_past_source(engine, clip):
        return None
    return _resize_cycle_time(engine, clip, source_t)


# 录制轮询 25ms（main_window `_rec_poll_timer`）→ 自然尾隙 = 一个轮询间隔；留一倍余量
_UNSET_SOURCE_TAIL_TOL = 0.05


def _clip_extends_past_source(engine, clip) -> bool:
    """clip 的源时间范围（时长×速率）是否超出源时长 = 被拖长过（R173）。

    源时长未记录（`source_duration=0`，从没经过拖拽/变速写入）时只能拿记录末帧估：超出
    末帧不到 `_UNSET_SOURCE_TAIL_TOL` 算录制自然尾隙（TTM 7 条实测恰 0.025s），再多才算拖长
    （R141 用例：录 2s 拖到 8s 仍按周期补全）。
    """
    try:
        duration = max(0.0, float(getattr(clip, "duration", 0.0) or 0.0))
        source = max(0.0, float(getattr(clip, "source_duration", 0.0) or 0.0))
    except (TypeError, ValueError):
        return False
    extent = duration * engine._clip_playback_rate(clip)
    if source > 0.0:
        return extent > source + 1e-6
    span = _recorded_cycle(engine, clip)
    return span is not None and extent > span[2] + _UNSET_SOURCE_TAIL_TOL


def _legacy_group_window(single_duration: float, seq_delay_in: float, seq_delay_out: float,
                         *, fade_in: float, fade_out: float, duration: float) -> float:
    """未启用扫掠时的单组窗口（R12 旧三级回退，逐字节不变）。

    优先级：存储的单组时长 → 延迟翼(`seq_delay`) → `max(fade_in, fade_out, duration)`。
    R152 从 `_compute_sequential_fixture_gains` 拆出（控该热路径函数的圈复杂度）。
    """
    if single_duration > 1e-4:
        return max(1e-6, float(single_duration))
    if max(seq_delay_in, seq_delay_out) > 1e-4:
        return max(1e-6, float(max(seq_delay_in, seq_delay_out)))
    return max(1e-6, float(max(fade_in, fade_out, duration)))


def _interp_compiled_frame(compiled, sample_t: float):
    """在编译帧表的 `sample_t` 处**线性插值**取一帧（R152 从热路径拆出，控父函数体积）。

    与拆出前的内联逻辑逐字节等价：`searchsorted(left)` 定位区间 → 两端线性混合 →
    `rint` 取整；越界（`idx<=0` / `idx>=count`）与"两帧同刻"（`t1<=t0`）都退化为取端点帧。
    """
    times = np.asarray(compiled.get("frame_times", np.zeros(0, dtype=np.float64)),
                       dtype=np.float64)
    data = compiled.get("frame_data")
    count = int(times.size)
    if data is None or count <= 0:
        return None
    if count == 1:
        return np.asarray(data[0], dtype=np.uint8)
    idx = int(np.searchsorted(times, sample_t, side="left"))
    if idx <= 0:
        return np.asarray(data[0], dtype=np.uint8)
    if idx >= count:
        return np.asarray(data[count - 1], dtype=np.uint8)
    t0 = float(times[idx - 1])
    t1 = float(times[idx])
    if t1 <= t0 + 1e-9:
        return np.asarray(data[idx], dtype=np.uint8)
    ratio = min(max((sample_t - t0) / (t1 - t0), 0.0), 1.0)
    a = np.asarray(data[idx - 1], dtype=np.float32)
    b = np.asarray(data[idx], dtype=np.float32)
    mixed = a + (b - a) * float(ratio)
    np.clip(mixed, 0.0, 255.0, out=mixed)
    return np.rint(mixed).astype(np.uint8)


def _sequential_span_of(single: float, pauses: list, batch_count: int,
                        step: Optional[float]) -> float:
    """扫掠总跨度 = 错峰步长×(波次−1) + 单台窗口 + 停顿合计（R152 两翼拆开后的公式）。

    `step is None`（未走翼路径的旧工程）→ 退回 `step = single`，与旧式 `single×批次+停顿`
    等价。抽成模块级函数是因为 `_clip_sequential_source_time` 与 `_sequential_hold_end`
    都要用，而两者都必须压住类的方法数预算（PlaybackEngine 上帝类）。
    """
    s = float(single)
    st = s if step is None else float(step)
    return max(0.0, st * max(0, int(batch_count) - 1) + s + float(sum(pauses or ())))


def _sequential_has_wing(clip) -> bool:
    """该 clip 是否设了**分组过渡的翼**（= 用户拖过条带）。

    判据与 `_sequential_wing_total` 同源：延迟翼 `seq_delay_in > 0`，或
    过渡翼 `fade_in > 0` **且分组方向已设**（方向是"分组过渡已打开"的标记 ——
    没设方向的普通淡入不该被当成过渡）。
    """
    try:
        seq_in = max(0.0, float(getattr(clip, "seq_delay_in", 0.0) or 0.0))
    except (TypeError, ValueError):
        seq_in = 0.0
    if seq_in > 1e-4:
        return True
    direction = str(getattr(clip, "fade_in_direction", "none") or "none").strip().lower()
    if direction in ("", "none"):
        return False
    try:
        return max(0.0, float(getattr(clip, "fade_in", 0.0) or 0.0)) > 1e-4
    except (TypeError, ValueError):
        return False




def _seq_sampled_stack(engine, clip, universe: int, max_samples: int = 64):
    """把某 universe 的源帧**等间隔采样**堆成 `(stack, timestamps)`（R155）。

    `stack` 形状 `(n, 512)` int16；帧不足 2 或解析失败返回 `(None, None)`。
    抽成独立函数是为了压住两个判据函数的体积（架构门禁 ≤100 行 / CC ≤8）。
    """
    frames = [
        f for f in (getattr(clip, "frames", []) or [])
        if int(getattr(f, "universe", universe) or universe) == int(universe)
    ]
    if len(frames) < 2:
        return None, None
    frames.sort(key=lambda f: float(getattr(f, "timestamp", 0.0) or 0.0))
    step = max(1, len(frames) // max(2, int(max_samples)))
    idxs = sorted(set(list(range(0, len(frames), step)) + [len(frames) - 1]))
    try:
        stack = np.stack([
            engine._coerce_dmx_data(
                getattr(frames[i], "data", np.zeros(512, dtype=np.uint8)))
            for i in idxs
        ]).astype(np.int16)
        ts = np.asarray([float(getattr(frames[i], "timestamp", 0.0) or 0.0)
                         for i in idxs], dtype=np.float64)
    except Exception:
        return None, None
    return stack, ts


def _movement_span_fraction(stack, ts) -> float:
    """**最宽的单个通道**其"变化时刻跨度"占整条源时长的比例（R155）。

    1.0 = 该通道在整条源时长里都在动（整体同步过渡的特征）；
    接近 0 = 只在一小段里动（chase 里单台被点亮的特征）。
    """
    total = float(ts[-1] - ts[0]) if ts is not None and ts.size >= 2 else 0.0
    if total <= 1e-6:
        return 0.0
    moving = np.flatnonzero(stack.max(axis=0) != stack.min(axis=0))
    widest = 0.0
    for col in moving:
        v = stack[:, col]
        d = np.flatnonzero(v[1:] != v[:-1])
        if d.size:
            widest = max(widest, float(ts[d[-1] + 1] - ts[d[0]]))
    return widest / total


def _sequential_source_time_variation(engine, clip, universe: int) -> bool:
    """源在**时间维度**上是否有变化（= 不是静态姿态）。

    ★★★ R155（用户："我真实的输出始终是错的"）：真工程 10 条带翼 clip 里 **2 条**
      的源是**静态姿态**（每台一个固定角度、无时间运动）。对这它们做顺序重排会让
      每台采样到**别人的**值 —— 真工程 `a93e080c` 实测 ch22 源值恒为 60（该台自己
      的目标），重排后变成 **128** → 输出卡在 128 = 用户报的"卡 128"。
    """
    cache = engine._clip_sequential_remap_cache
    key = (f"timevar::{getattr(clip, 'id', '') or id(clip)}::{int(universe)}::"
           f"{len(getattr(clip, 'frames', []) or [])}")
    cached = cache.get(key)
    if cached is not None:
        return bool(cached[1])
    stack, _ts = _seq_sampled_stack(engine, clip, universe)
    result = bool(stack is not None
                  and (stack.max(axis=0) != stack.min(axis=0)).any())
    cache[key] = (None, result)
    if len(cache) > 128:
        engine._evict_oldest(cache)
    return result


def _movement_oscillation(stack) -> float:
    """每个运动通道**穿越自己取值中线的平均次数**（R156）。

    单次过渡（一路走到目标）≈ 1（最多 1 次穿越）；**周期性动态效果**（往复循环）
    ≫ 1。用它区分"该整体播放的单次过渡"与"该按自己周期播放的动态效果"。
    """
    moving = np.flatnonzero(stack.max(axis=0) != stack.min(axis=0))
    if moving.size == 0:
        return 0.0
    counts = []
    for col in moving[:48]:
        v = stack[:, col].astype(np.int32)
        mid = (int(v.max()) + int(v.min())) // 2
        counts.append(int(np.count_nonzero((v[1:] > mid) != (v[:-1] > mid))))
    return float(np.mean(counts)) if counts else 0.0


def _seq_dir_gain(engine, clip, local_t: float, duration: float, *, fade_in: bool) -> float:
    """单个方向的整体包络增益（R157 拆出，供 `_sequential_global_gain` 按方向跳过时用）。"""
    key = "fade_in" if fade_in else "fade_out"
    span = max(0.0, min(float(getattr(clip, key, 0.0) or 0.0), duration))
    if span <= 1e-6:
        return 1.0
    if fade_in:
        if local_t >= span:
            return 1.0
        progress = max(0.0, min(1.0, local_t / span))
    else:
        if local_t <= duration - span:
            return 1.0
        progress = max(0.0, min(1.0, (duration - local_t) / span))
    return float(engine._clip_transition_gain(clip, progress, fade_in=fade_in))


def _sequential_global_gain(engine, clip, local_t: float, *, skip_in: bool,
                            skip_out: bool) -> float:
    """整体淡入淡出增益，可**按方向跳过**已由分组窗口覆盖的部分（R157）。

    用户（2026-09-23）::

        "比如左到右现在是既左到右四组分组过渡了，又有一个整体从左到右一组的大过渡，
         这个大过渡可能是你引入的过渡翼的语义不清导致的，所有过渡都按照我设置的
         方式来，是左到右四组那么延迟和淡入都是四组不是你这样的"

    分组过渡启用时，淡入/淡出已由**每台自己的窗口**完成；再乘一遍整体包络等于
    把过渡做两遍（用户看到的"多一个整体大过渡"）。两个方向都没覆盖时退回原
    `_clip_fade_gain`（逐字节不变）。
    """
    if not skip_in and not skip_out:
        return float(engine._clip_fade_gain(clip, local_t))
    duration = max(0.0, float(getattr(clip, "duration", 0.0) or 0.0))
    if duration <= 0.0:
        return 1.0
    gain = 1.0
    if not skip_in:
        gain = min(gain, _seq_dir_gain(engine, clip, local_t, duration, fade_in=True))
    if not skip_out:
        gain = min(gain, _seq_dir_gain(engine, clip, local_t, duration, fade_in=False))
    return gain


def _emit_runs(copy_rows, phase_mask, *, phase_value: bool, span) -> None:
    """把一段布尔掩码切成连续 True 段，逐段追加一行（R158）。

    ★★ R167 修复（用户实报「这个项目文件打开之后渲染没有结果，其它文件正常」）：
      本行原写作 `tgt_start, src_start, src_universe, group, rel_pos, group_size = span`
      —— **漏解了 `count`**。`span` 是 7 元组
      `(tgt_start, src_start, count, src_universe, group, rel_pos, group_size)`
      （见调用方 `_append_copy_row_segments` 构造点与 `_append_plain_row` 的同款解包）。

      两个后果：
        · 直接抛 `ValueError: too many values to unpack (expected 6)` → 整条
          `lock_timeline_render_cache()` 中断、`_timeline_render_frame_cache` 保持为空
          → 用户看到"渲染时间线点了没反应/没有结果"；
        · 即便不抛，字段也会整体错位（`src_universe` 拿到 `count`…）→ 拷贝行地址错乱。

      为什么长期没被发现：这条路径要求「源**在空间上有变化**」+「命中电机行拆分
      （`direction != none` 且电机掩码有交集）」。参考工程 `水仙十字` 里唯一的顺序
      分组 clip 是**静态姿态源**（`source_has_spatial_variation=False`）恰好绕开；
      而 `喷泉编曲至冬7.0` 有满足条件的 clip → 必现。测试此前对该函数零覆盖。
    """
    tgt_start, src_start, _count, src_universe, group, rel_pos, group_size = span
    run_start = None
    for offset, is_valid in enumerate(phase_mask.tolist() + [False]):
        if is_valid and run_start is None:
            run_start = int(offset)
        elif not is_valid and run_start is not None:
            run_len = int(offset) - run_start
            if run_len > 0:
                copy_rows.append((
                    int(tgt_start + run_start), int(src_start + run_start), int(run_len),
                    int(src_universe), int(group), float(rel_pos), float(group_size),
                    bool(phase_value)))
            run_start = None


def _append_motor_rows(copy_rows, motor_mask_fn, span, *, flags) -> bool:
    """按电机掩码把一段拆成电机行/非电机行（R158）。返回 True = 已处理完。

    `flags = (universe, src_universe, count, phase_enabled, split_rows, phase_only)`。
    `phase_enabled or split_rows` 为真时才拆 —— 未勾选"电机参与过渡相位差"时也要
    能分辨电机行，好给电机"错峰间隔=0"的语义。`phase_value=True` 的行 = 电机行。
    """
    universe, src_universe, count, phase_enabled, split_rows, phase_only = flags
    if not (phase_enabled or split_rows):
        return False
    tgt_start, src_start, _c, _su, _g, _r, _gs = span
    motor_valid = (motor_mask_fn(int(universe))[int(tgt_start):int(tgt_start) + int(count)]
                   & motor_mask_fn(int(src_universe))[int(src_start):int(src_start) + int(count)])
    if motor_valid.size <= 0 or not (motor_valid).any():
        return bool(phase_only)
    if not phase_only:
        _emit_runs(copy_rows, ~motor_valid, phase_value=False, span=span)
    _emit_runs(copy_rows, motor_valid, phase_value=True, span=span)
    return True


def _append_plain_row(copy_rows, span) -> None:
    """追加一行非电机拆分（整段照搬）。

    `span = (tgt_start, src_start, count, src_universe, group, rel_pos, group_size)`。
    """
    tgt_start, src_start, count, src_universe, group, rel_pos, group_size = span
    copy_rows.append((
        int(tgt_start), int(src_start), int(count), int(src_universe), int(group),
        float(rel_pos), float(group_size), False))


def _seq_latency_start(group_idx, group_pos, group_sizes, batch_count, latency):
    """顺序扫掠的**每台起点**（R162 定案）。

    用户定案（2026-09-23）::

        "总时长是过渡翼加延迟翼的总时长，延迟翼是相位差拉开的时长，
         过渡翼是每个设备的延迟时长。"
        "分成多批次执行，但是每批次内部该有的相位差应该要有"

    → **所有设备**（批次之间 + 批次内部）按
      `[批次 → 批内组序 → 组内名次]` 排序后，**均匀铺在 `[0, 延迟翼]`** 上：

          设备起点 = 延迟翼 × rank / rank_max

    这样同时满足三条约束（缺一不可）：
      · **批次依次**：批 0 的设备整体在批 1 之前、批 1 在批 2 之前；
      · **批内/组内依次**：同批各组的各台依次错开（相位差存在）；
      · **总时长 = 延迟翼 + 过渡翼**：末台恰在 `延迟翼` 处起步，再走完自己的
        完整过渡翼。

    ⚠ 与 R155 的区别：R155 把每台的过渡窗口摊薄成 `过渡翼/组内台数` 来腾出铺开
      空间（用户报"过渡翼没应用到设备"）；本式**从延迟翼里取铺开空间**，
      每台过渡翼完整保留。

    ⚠ 与 R159 的区别：R159 的 `group_pos × max(0, 过渡翼 − 过渡翼)` 恒为 0
      → 同批各台同时起步（用户报"组内相位差没有恢复"）。

    `latency` = 延迟翼（= `batch_offsets[-1]`，已含组间停顿）。
    """
    latency = max(0.0, float(latency or 0.0))
    gi = np.asarray(group_idx, dtype=np.int64)
    gp = np.asarray(group_pos, dtype=np.float64)
    gs = np.asarray(group_sizes, dtype=np.float64)
    if latency <= 1e-9 or gi.size == 0:
        return np.zeros(gp.shape, dtype=np.float64)
    bc = max(1, int(batch_count or 1))
    batch_idx = np.mod(gi, bc)
    group_ord = gi // bc
    gmax = float(np.max(gs)) if gs.size else 1.0
    # 组内名次（0..size-1）；单台组 = 0（它是该组唯一成员，不额外偏移）
    rank_in = np.where(gs > 1.0, gp * np.maximum(1.0, gs - 1.0), 0.0)
    nb = int(np.max(group_ord)) + 1 if group_ord.size else 1
    rank = (batch_idx * nb + group_ord) * gmax + rank_in
    rmax = float(np.max(rank)) if rank.size else 0.0
    if rmax <= 1e-9:
        return np.zeros(gp.shape, dtype=np.float64)
    return latency * rank / rmax


def _seq_window_and_step(engine, clip, single_duration, *, seq_in, seq_out, fade_in,
                         fade_out, duration, override):
    """返回 `(错峰步长, 单台窗口)`（R152/R158；未启用扫掠 → 旧三级回退）。"""
    step = engine._clip_sequential_timing_detail(clip)[5]
    if step is not None and override is not None:
        step = max(0.0, float(override))
    if step is not None:
        return step, max(1e-6, float(single_duration))
    pg = _legacy_group_window(single_duration, seq_in, seq_out,
                              fade_in=fade_in, fade_out=fade_out, duration=duration)
    return pg, pg


def _seq_fade_out_gains(engine, clip, local_fixtures, groups, duration, *, out_direction,
                        local_t, prm) -> np.ndarray:
    """顺序扫掠的**淡出**增益（R152 公式：错峰间隔×波次 + 单台窗口 + 停顿）。

    `prm = (fade_out, seq_delay_out, fade_out_scale, ease_lut, _stagger, per_group,
    batch_count, pauses, batch_offsets, track, proj)`（位置参数上限 5 门禁）。
    """
    (fade_out, seq_delay_out, fade_out_scale, ease_lut, _stagger, per_group,
     batch_count, pauses, batch_offsets, track, proj) = prm
    group_idx, group_pos, group_sizes = engine._sequential_fixture_group_data(
        clip, local_fixtures, out_direction, groups, track=track, proj=proj)
    out_target = seq_delay_out if seq_delay_out > 1e-4 else fade_out
    out_span = max(1e-6, float(_stagger) * max(0, batch_count - 1)
                   + per_group + float(sum(pauses)))
    out_scale = max(1e-6, out_target / out_span)
    out_per_group = max(1e-6, per_group * out_scale)
    out_offsets = batch_offsets * out_scale
    batch_idx = np.mod(group_idx.astype(np.int32), batch_count)
    win = (out_per_group * float(fade_out_scale)) / np.maximum(group_sizes, 1.0)
    rel_start = out_offsets[batch_idx] + group_pos * np.maximum(0.0, out_per_group - win)
    t_start = (duration - out_target) + rel_start
    t_end = t_start + win
    tt = np.clip((t_end - local_t) / np.maximum(win, 1e-6), 0.0, 1.0)
    g = engine._clip_transition_gains(clip, tt, fade_in=False, ease_lut=ease_lut)
    g = np.where(local_t >= t_end, 0.0, g)
    g = np.where(local_t < t_start, 1.0, g)
    return g.astype(np.float32)


def _ramp_gain(engine, clip, local_t: float, t_start, win, *, fade_in: bool, ease_lut):
    """单台过渡窗口的增益曲线（两端外推：未开始=0、已结束=1）。"""
    tt = np.clip((local_t - t_start) / np.maximum(win, 1e-6), 0.0, 1.0)
    g = engine._clip_transition_gains(clip, tt, fade_in=fade_in, ease_lut=ease_lut)
    g = np.where(local_t < t_start, 0.0, g)
    g = np.where(local_t >= t_start + win, 1.0, g)
    return g.astype(np.float32)


def _motor_source_time(engine, clip, local_t: float, source_duration: float,
                       cap: float, *, wing: bool):
    """电机的源时刻（R158/R159）。不适用时返回 `None`（调用方走原路径）。

    生效范围：**带翼 + 源是往复循环的动态效果**。
      · 无翼顺序内容（波形/跑灯）→ R102 周期重放不变；
      · 单次过渡 → 停在完成态（R153 不变）。
    """
    if not wing or not _clip_motor_no_delay(engine, clip):
        return None
    if not _sequential_source_is_cyclic(engine, clip, _seq_primary_universe(clip)):
        return None
    sdl = max(1e-6, float(source_duration))
    return min(sdl - 1e-4, float(local_t) % sdl), False


def _seq_primary_universe(clip) -> int:
    """该 clip 的主 universe（用于取"源类型"判据）。"""
    for u in (getattr(clip, "clip_universes", None) or [1]):
        return int(u)
    return 1


def _clip_motor_no_delay(engine, clip) -> bool:
    """该 clip 的**电机通道是否没有延迟翼语义**（R158）。

    用户（2026-09-23）::

        "我没有设置它电机参与过渡相位差，这个含义就是电机没有延迟翼语义，
         延迟翼只对非电机类型效果生效"

    → 未勾选"电机参与过渡相位差"且无电机力剖面时，电机通道**不受延迟翼错峰**
      （各台同时起步，只保留各自的过渡翼柔和时长）。
    """
    try:
        if bool(getattr(clip, "motor_transition_phase_enabled", False)):
            return False
        if engine._clip_motor_force_profile(clip) is not None:
            return False
    except Exception:
        return False
    return True


def _apply_motor_no_stagger_gains(engine, clip, gains, span_idx, spans, *, ctx) -> None:
    """把**电机通道**的增益换成"无延迟翼"版本（R158/R159，原地改写 `gains`）。

    用户语义（2026-09-23）::

        "我没有设置它电机参与过渡相位差，这个含义就是电机没有延迟翼语义，
         延迟翼只对非电机类型效果生效"

    → 电机各台**不参与延迟翼错峰**（同时起步），只保留**过渡翼**柔和。
    两条路径都适用：顺序用 `stagger_override=0.0`；并行用 `seq_delay_in=0.0`。
    `ctx` 打包其余上下文（位置参数上限 5 门禁）。
    """
    (sorted_fixtures, universe, local_t, fade_in, fade_out, duration, in_direction,
     out_direction, group_pause, is_fading_in, is_fading_out, track, proj, grp,
     single_duration, seq_in, seq_out, ease_lut, is_seq) = ctx
    # 生效范围：带翼 + 源是往复循环的动态效果（与 `_motor_source_time` 一致）。
    if not (_clip_motor_no_delay(engine, clip)
            and _sequential_source_is_cyclic(
                engine, clip, _seq_primary_universe(clip))):
        return
    motor_mask = _fitted_motor_mask(engine, universe, proj)
    if motor_mask is None:
        return
    fi = _motor_nostag_fi(engine, clip, p=(
        sorted_fixtures, local_t, fade_in, fade_out, duration, in_direction,
        out_direction, grp, group_pause, single_duration, is_fading_in, is_fading_out,
        track, proj, seq_in, seq_out, ease_lut, is_seq))
    if fi is None:
        return
    _write_motor_gains(gains, fi, motor_mask, span_idx, spans)


def _write_motor_gains(gains, fi, motor_mask, span_idx, spans) -> None:
    """把电机通道的增益写回（两种索引方式）。"""
    if span_idx is not None:
        idx = np.asarray(span_idx)
        n = min(512, idx.size)
        valid = (idx[:n] >= 0) & (idx[:n] < fi.size) & motor_mask[:n]
        rows = np.flatnonzero(valid)
        gains[rows] = fi[idx[rows].astype(np.intp)]
        return
    for i, (s0, e0) in enumerate(spans or []):
        if not (0 <= s0 < e0 <= 512) or i >= fi.size:
            continue
        sel = np.flatnonzero(motor_mask[s0:e0]) + s0
        if sel.size:
            gains[sel] = fi[i]


def _fitted_motor_mask(engine, universe, proj):
    """512 长的电机通道掩码（越界补齐）；全空返回 None。"""
    m = np.asarray(engine._build_motor_channel_mask(int(universe), proj), dtype=bool)
    if m.shape != (512,):
        fixed = np.zeros(512, dtype=bool)
        n = min(int(m.size), 512)
        fixed[:n] = m.reshape(-1)[:n]
        m = fixed
    return m if (m).any() else None


def _motor_nostag_fi(engine, clip, *, p):
    """按路径算"电机免错峰"的单台增益（失败返回 None）。`p` = 打包参数元组。"""
    (fixtures, local_t, fade_in, fade_out, duration, in_dir, out_dir, grp, group_pause,
     single_duration, is_fading_in, is_fading_out, track, proj, seq_in, seq_out,
     ease_lut, is_seq) = p
    try:
        if is_seq:
            return np.asarray(engine._compute_sequential_fixture_gains(
                clip, fixtures, fade_in, fade_out, duration, local_t, in_dir, out_dir,
                grp, group_pause, single_duration, is_fading_in, is_fading_out,
                track=track, proj=proj, seq_delay_in=seq_in, seq_delay_out=seq_out,
                ease_lut=ease_lut, stagger_override=0.0), dtype=np.float32)
        return np.asarray(engine._compute_parallel_fixture_gains(
            clip, fixtures, fade_in, fade_out, duration, local_t, in_dir, out_dir,
            grp, is_fading_in, is_fading_out, track=track, proj=proj,
            seq_delay_in=0.0, seq_delay_out=0.0, ease_lut=ease_lut), dtype=np.float32)
    except Exception:
        return None
def _sequential_after_span(engine, clip, local_t: float, source_duration: float,
                           cap: float) -> tuple[float, bool]:
    """扫掠跨度跑完之后该给什么源时刻（R156）。

    ★ 用户实报："检查 REC 9CF4 这个效果被破坏了，它是一个动态的效果，但是你的延迟
      行为却反复让它计算一个静态的角度反复抽动。"

      · **往复循环的动态效果**（`9cf436e5`：源 32s 循环、每 ~10s 重复一遍）→
        继续按**源自己的时间轴**推进（`local_t % 源时长`），否则会钉死在源末尾那一帧
        的固定角度（实测 8s 起恒 199 = 用户看到的"抽动"）。
      · **单次过渡** → 停在完成态 `cap`（R153 语义不变）。

    返回 `(source_t, in_pause)`。
    """
    uni = 1
    for u in (getattr(clip, "clip_universes", None) or [1]):
        uni = int(u)
        break
    if _sequential_source_is_cyclic(engine, clip, uni):
        sdl = max(1e-6, float(source_duration))
        return min(sdl - 1e-4, float(local_t) % sdl), False
    return cap, False


def _sequential_source_is_cyclic(engine, clip, universe: int) -> bool:
    """源是否是**往复循环的动态效果**（= 越过扫掠窗口后应继续按源时间轴播放）。

    ★★★ R156（用户实报）::

        "检查 REC 9CF4 这个效果被破坏了，它是一个动态的效果，但是你的延迟行为却
         反复让它计算一个静态的角度反复抽动。"

      真工程 `9cf436e5` 的源是 **32 秒循环动态内容**（ch7 每 ~10s 重复一遍
      60→182→134→87→210→57，32s 跑 3 个周期，穿越中线 19.4 次/通道），
      而扫掠"单台窗口"只有 2.645s。旧式在窗口走完后直接取源**末尾那一帧**
      （一个固定角度）→ 设备停在死角度 → 表现为"反复算同一角度、抽动"。

    判据：穿越取值中线的平均次数 > 2.5（往复多 = 动态效果）；
    单次过渡（一路走到目标，`d4381eb3` 等）≈1.0 → 不受影响。
    """
    cache = engine._clip_sequential_remap_cache
    key = (f"cyclic::{getattr(clip, 'id', '') or id(clip)}::{int(universe)}::"
           f"{len(getattr(clip, 'frames', []) or [])}")
    cached = cache.get(key)
    if cached is not None:
        return bool(cached[1])
    stack, _ts = _seq_sampled_stack(engine, clip, universe)
    result = bool(stack is not None and _movement_oscillation(stack) > 2.5)
    cache[key] = (None, result)
    if len(cache) > 128:
        engine._evict_oldest(cache)
    return result


def _sequential_source_is_global_transition(engine, clip, universe: int) -> bool:
    """源是否是**单次整体过渡**（= 源自带完整时间轴、且只走一遍，不该做顺序重排）。

    ★★★ R155 提出、R156 收窄。判据三条同时满足:
      ① 变化跨度 ≥ 80% 源时长（所有通道一起在整条时长里都在动）；
      ② **周期性弱**（穿越中线平均次数 ≤ 2.5）—— 排除往复循环的动态效果；
      ③ **不是原地不动**（时间变化判据另管）。

    ★ 为什么必须有 ②（用户实报）::

        "检查 REC 9CF4 这个效果被破坏了，它是一个动态的效果，但是你的延迟行为却
         反复让它计算一个静态的角度反复抽动。"

      `9cf436e5` 的源是**循环往复的动态效果**（ch7 每 ~10s 重复一遍，32s 跑 3 个周期，
      穿越中线 19.4 次/通道）。只用 ① 会把它误判成"单次过渡" → 禁用重排 →
      每台反复算同一角度 → **抽动**。加上 ② 后它被正确排除，重排照常（= 各台依次
      执行这个动态效果的原本设计）。

      单次过渡（真工程 `d4381eb3`/`34a9720b` 等）穿越次数 ≈1.0 → 仍被正确识别。
    """
    cache = engine._clip_sequential_remap_cache
    key = (f"globtrans::{getattr(clip, 'id', '') or id(clip)}::{int(universe)}::"
           f"{len(getattr(clip, 'frames', []) or [])}")
    cached = cache.get(key)
    if cached is not None:
        return bool(cached[1])
    stack, ts = _seq_sampled_stack(engine, clip, universe)
    if stack is None:
        result = False
    else:
        wide = _movement_span_fraction(stack, ts) >= 0.8
        result = bool(wide and _movement_oscillation(stack) <= 2.5)
    cache[key] = (None, result)
    if len(cache) > 128:
        engine._evict_oldest(cache)
    return result


def _sequential_hold_end(engine, clip, source_duration: float) -> float:
    """分组过渡**跑完后应停住的源时刻** = 记录末帧时刻（R153）。

    ★★ 用户实报（2026-09-22）："把这种尾巴平滑掉"。真因（`tools/_probe_r153_tail.py`
      对真工程 `d4381eb3` 逐段追时间链实测）——**两处周期回卷叠加**：

      1. `_sequential_fold_time` 用 `local_t % span` 做**周期重放**：扫掠跨度 2.479s
         而 clip 时长 3.913s → 过渡跑完后**又从头再跑一遍**，末尾 1.4s 是多余动作。
      2. "完成态"的源时刻取 `source_duration`（3.9129），而**记录末帧时刻只有
         3.9060** —— 差 6.8ms 越过末帧 → `_resize_cycle_time`（拖长按周期扩展）把它
         回卷成 `0 + (3.9129 % 3.906) = 0.0068` → 采样到**源开头**那一帧，
         输出从目标值**跳回起始值**（用户看到的"到位置又跳一下"）。

    **过渡是一次性位移，不是循环动画** → 完成后必须**停住**；这与 R102"拖长按周期
    扩展"是给**内容**（波形/跑灯）的语义并不冲突（`_sequential_has_wing` 把两者分开）。

    返回值取 `min(source_duration−ε, 记录末帧时刻)`：**不越过末帧**，于是下游
    `_resize_cycle_time` 不再回卷（`t <= last_ts` → 返回 None），采样到的就是源末帧
    = 过渡的目标值。
    """
    end = max(0.0, float(source_duration) - 1e-4)
    rec = _recorded_cycle(engine, clip)
    if rec is not None:
        end = min(end, float(rec[2]))
    return max(0.0, end)


def _sequential_fold_time(clip, local_t: float, single: float, pauses: list,
                          batch_count: int, *, step: Optional[float] = None) -> float:
    """把超出扫掠跨度的 `local_t` 折回（R102 周期扩展）。

    ★ 2026-09-14 深查修正 B2：顺序扫掠（含过渡翼）的时长由"单组窗口"决定，与 clip
      duration 解耦（R12 定案）。于是把 clip 拖长后，扫掠在 span 处就走完，**剩余
      时间会冻结在最后一帧**（`_clip_sequential_active_batch` 到底后返回单组窗口末）。
      用户要的是"往后拉就按周期自动扩展效果往后" → 超出整个扫掠周期时**按周期重放**。
      正常运行（local_t ≤ span，即没拖长）取模是恒等 → 逐字节不变；
      显式变速过（resize_cycle=False）保持旧语义不动。

    ★★★ R152 撤回 R148（2026-09-22）::

        R148 我按"必须在这个 clip 的生命周期内把效果跑完"把带翼 clip 改成**一次跑完
        就钉在完成态**（`return span`）—— 那是我**误解**用户的意思。用户随后纠正::

            "不是这个意思，完全做错了，我的意思是 clip 之前可能根本跑不完，
             因为过渡时长过长，且没有遵循 clip 的实际长度。"

        真正的诉求是**跨度不得超过 clip 时长**（已在 `_sequential_single_window` 里
        按 clip 时长等比压缩解决），而不是"跑完就冻结"。跑完之后的剩余时间该按 R102
        原语义**周期扩展**（用户从没要求过冻结）。
        故此处恢复 R102：超出 span 一律 `local_t % span`。

    `step`（R152 错峰步长）只用于算跨度，缺省时退回 `single`（旧公式逐字节相同）。
    """
    local_t = max(0.0, float(local_t))
    if not bool(getattr(clip, "resize_cycle", True)):
        return local_t
    if step is None:
        step = single
    span = (float(step) * max(0, int(batch_count) - 1)
            + float(single) + float(sum(pauses)))
    if span <= 1e-6 or local_t <= span:
        return local_t
    return local_t % span


def _segments_differ(raw, r_idx, lo, hi, spans) -> bool:
    """各 span 段的**均值**是否不全相等（= 源里有"空间差异"）。

    ★★ R147 修正（真工程实测）：原判据用**段 max**，而绝大多数设备每个 span 里都含
      一个**恒为满值**的通道（功能通道 / RGBW 里的常亮通道）→ "段 max" 恒等于 255
      → 判据永远认为"源无空间差异" → `_clip_sequential_remapped_source_frame` 直接
      放弃重排 → 各设备拿不到属于自己的源值 → 顺序分组**从不挨个执行**
      （实测真工程 10/10 条带分组过渡的 clip 全被判 False）。

    用户实报："调两个翼长度尤其是延迟翼没反应。根本不依据我拉的时间来过渡啊"

    用**段均值**：① 不被单个满值通道淹没；② 与 span 长度无关
    （段和不满足此点：长 span 天然占优）。`np.add.reduceat` 一次出全部段和。
    """
    if len(spans) < 2:
        return False
    try:
        seg = np.add.reduceat(np.asarray(raw, dtype=np.int64), r_idx)
    except Exception:
        return False
    vals = [float(seg[_l:_h].sum()) / max(1, int(_e - _s))
            for _l, _h, (_s, _e) in zip(lo, hi, spans)]
    return bool(vals) and min(vals) != max(vals)


_R152_MIN_WINDOW = 1e-4
"""R152：只拖延迟翼时的**单台窗口**（极小值 = 视为瞬变）。

用户语义"延迟翼 = 错峰跨度、过渡翼 = 单台时长"：没拖过渡翼就意味着每台自己**瞬间**
到位（只有错峰在铺开），而不是把延迟翼摊成每台慢慢走 —— 旧实现正是后者
（`single = total/批次数`），于是"拖延迟翼没反应"。
"""


def _sequential_gate_inputs(engine, clip) -> tuple:
    """`_clip_sequential_timing_detail` 的读参+派生部分（为控 CC 拆出）。

    返回 `(groups, single, seq_in, fade_in_c, dir_set, batch_count, pauses)`：
    `single` 是**存储的**单组时长字段（尚未被翼派生替换），`fade_in_c` 是过渡翼，
    `dir_set` 表示分组方向已设（= 用户真的开了"分组过渡"）。
    """
    groups = max(1, int(getattr(clip, "fade_groups", 1) or 1))
    pause = max(0.0, float(getattr(clip, "fade_group_pause", 0.0) or 0.0))
    single = max(0.0, float(getattr(clip, "fade_group_single_duration", 0.0) or 0.0))
    seq_in = max(0.0, float(getattr(clip, "seq_delay_in", 0.0) or 0.0))
    batch_count = engine._clip_sequential_batch_count(clip, groups)
    pauses = engine._clip_sequential_pauses(clip, groups, pause)
    try:
        fade_in_c = max(0.0, float(getattr(clip, "fade_in", 0.0) or 0.0))
    except (TypeError, ValueError):
        fade_in_c = 0.0
    dir_set = (
        str(getattr(clip, "fade_in_direction", "none") or "none").strip().lower()
        not in ("", "none")
    )
    return groups, single, seq_in, fade_in_c, dir_set, batch_count, pauses


def _sequential_wing_total(clip, seq_in: float) -> float:
    """顺序扫掠的**总过渡时长 = 延迟翼 + 过渡翼**（R145 用户定案）。

    用户原话（2026-09-22，含一次纠正）::

        "过渡翼就是用来表示挨个执行的意思，延迟翼是用来表示整体执行的意思，
         两个分别拉时长，加和。"
        "比如从左到右两组过渡，左到右每个设备依次过渡这个依次的总时长归过渡翼管，
         然后整个效果大整体的淡入淡出延迟归延迟翼管"
        "延迟是黄色条带，过渡是底下那个蓝色把手"     ← 纠正视觉归属

    **两个翼的字段 ↔ 视觉 ↔ 语义**（`clip_view` 里逐条核对过）：

    | 翼 | 视觉（用户叫法） | 字段 | 语义 |
    |---|---|---|---|
    | **延迟翼** | **黄色条带**（`ACTIVE_ORANGE` 底 + `BRIGHT_WARN` 顶条，宽度 = `din`） | `seq_delay_in/out` | 整体执行（整体淡入淡出延迟） |
    | **过渡翼** | **底部蓝色把手**（位置 = `fade_in`） | `fade_in/out` | 挨个执行（依次的总时长） |

    已存值的**历史数据**也印证这个归属：真工程里 `seq_delay_in>0` 的 4 个 clip
    全都是 `fade_groups=4/8` 且 `fade_in_direction=ltr`（"整体延迟 + 依次过渡"），
    没有一个是"只拖了过渡翼却没设方向"的。

    只有在**分组方向已设**（`fade_in_direction != "none"`）时过渡翼才算进来 ——
    那正是"分组过渡"被打开的标记（`_on_clip_dir_fade_set` 写它）。
    否则普通淡入会被误当成"挨个执行"，把一段普通淡入 clip 判成顺序扫掠
    （静默变质）。

    ★ 这个加和还修了用户原话里的"每一次执行过渡都没有按照我的过渡翼语义来"：
      旧闸门只认 `single/seq_in`，于是**只拖了过渡翼**（`seq_delay_in==0`）的 clip
      被判"没启用顺序扫掠" → 过渡翼整个不参与。
    """
    try:
        fade_in = max(0.0, float(getattr(clip, "fade_in", 0.0) or 0.0))
    except (TypeError, ValueError):
        fade_in = 0.0
    dir_set = str(getattr(clip, "fade_in_direction", "none") or "none").strip().lower()
    if dir_set in ("", "none"):
        fade_in = 0.0
    total = max(0.0, float(seq_in or 0.0)) + fade_in
    return total


def _motor_phase_fraction(clip, amount: float) -> float:
    """电机相位差跨度占源时长的**比例**（R145：让两个翼参与进来）。

    ★★ 用户定案（2026-09-22）::

        "分组过渡的延迟翼和过渡翼语义没参与到电机参与过渡相位差…我的意思就算
         过渡时长还是不对…过渡翼就是用来表示挨个执行的意思，延迟翼是用来表示
         整体执行的意思，两个分别拉时长，加和。"

    旧实现只认 `motor_transition_phase_amount` 这个**与翼无关的倍率**
    （`amount/(1+amount)`）→ 用户拖翼时电机的错峰完全不动，即"没参与"。

    新式：**两个翼在场时，相位差跨度直接由它们决定** ——
    整体（延迟翼）占"整体+挨个（过渡翼）"的比例，就是电机错峰该铺开的比例：

        fraction = 延迟翼 / (延迟翼 + 过渡翼)

    语义一致性：延迟翼 = 整体执行 = 错峰的总跨度；过渡翼 = 挨个执行 = 每个设备
    自己的过渡时长。于是"拖延迟翼 → 错峰铺得更开"、"拖过渡翼 → 每台自己的过渡更长"，
    与用户对两个翼的表述同构。

    无翼（旧工程/未设方向）→ 退回原倍率公式，**逐字节零回归**。
    """
    try:
        seq_in = max(0.0, float(getattr(clip, "seq_delay_in", 0.0) or 0.0))
    except (TypeError, ValueError):
        seq_in = 0.0
    total = _sequential_wing_total(clip, seq_in)
    if total > 1e-6:
        return min(1.0, max(0.0, seq_in / total))
    amt = max(0.0, float(amount or 0.0))
    return amt / (1.0 + amt) if amt > 0.0 else 0.0


def _sequential_single_window(clip, single: float, pauses: list,
                              batch_count: int, seq_in: float) -> tuple:
    """顺序扫掠的**两个独立量**：单台窗口 + 错峰步长（R152 拆开）。

    ★★ 用户实报（2026-09-22）："clip后处理的顺序执行批次，顺序分组发现bug，效果不按照
      我拖动的总时长来淡入"；追问确认「翼与总时长两个都拖过、都想要」。

    旧式 `single = 翼/(组数−1)` 的两个毛病：
      ① **批次一介入就不对**：扫掠总长 = `single×批次数` ≈ 翼×批次数/(组数−1)，
         与用户拖的翼长度无关（实测 翼3s/8组/批次2 → 扫掠仅 0.86s）；
      ② **拖 clip 总时长完全不响应**（duration 在翼分支里从不参与）。
    （根源是 R12 定案"翼是权威、duration 不得反推扫掠" —— 当时没把「顺序执行批次」
      与「拖总时长」这两个用户实际会用的入口考虑进去。）

    ★★ R145 用户定案（2026-09-22，**两个翼的分工**）::

        "过渡翼就是用来表示挨个执行的意思，延迟翼是用来表示整体执行的意思，
         两个分别拉时长，加和。"

    即 UI 上两个把手各有分工（`clip_view` 里：底部蓝色把手=过渡翼 `fade_in/out`，
    顶部黄色条带=延迟翼 `seq_delay_in/out`）：

    | 翼 | 字段 | 语义 | 角色 |
    |---|---|---|---|
    | **过渡翼** | `fade_in` | 挨个执行 | 每一组自己的过渡时长 |
    | **延迟翼** | `seq_delay_in` | 整体执行 | 组间错峰铺开的总跨度 |

    ★★★ R152 **根因修复**（2026-09-22）—— 前 10 轮修不好的唯一原因::

        "最终的实际过渡和延迟时间只由我拖的延迟翼和过渡翼的长度决定。
         我现在调两个翼长度尤其是延迟翼没反应。根本不依据我拉的时间来过渡啊"

      R144~R151 全在改"**两翼之和怎么算**"（`total = 两翼相加` → `single = total/批次数`
      均分给每批次），于是**两翼被合并成一个标量**，分工在那行被永久丢弃。可证伪判据
      （`tools/_probe_r152_wings.py` 实测）：`延迟3+过渡1` 与 `延迟1+过渡3` 的每台
      到位时刻**完全相同** `[0.92,1.92,2.92,3.92]` —— 即"拖哪个翼都没反应"。

      **修复 = 把两个量拆开、各自归位**：
        - `single`（单台窗口）= **过渡翼** → 每组从起点走到目标的时长；
        - `step`（错峰步长）= **延迟翼/(批次数−1)** → 相邻批次起点的间隔（批次=1 时为 0）。

      于是「延迟3 + 过渡1 / 4 台」→ 起步 `[0,1,2,3]`、各自 1s 到位，总长 = 3+1 = 4s；
      「延迟1 + 过渡3」→ 起步 `[0,.33,.67,1]`、各自 3s → **两者不再相同**。

    返回 `(single, pauses, step)`；`step is None` = 未走翼路径（旧工程）→ 调用方退
    `step = single`，与旧公式**逐字节相同**。无翼仍走原路径（`duration/已存跨度` 缩放）。
    """
    seq_in_c = max(0.0, float(seq_in or 0.0))
    try:
        transitional = max(0.0, float(getattr(clip, "fade_in", 0.0) or 0.0))
    except (TypeError, ValueError):
        transitional = 0.0
    if str(getattr(clip, "fade_in_direction", "none") or "none").strip().lower() in ("", "none"):
        transitional = 0.0            # 未设方向 → 过渡翼不参与（与 R145 判据一致）
    wing_total = _sequential_wing_total(clip, seq_in_c)
    if wing_total <= 1e-4:
        wing_total = seq_in_c          # 未设方向 → 退回只算延迟翼
    if wing_total > 1e-4:
        return _wing_window(clip, pauses, batch_count, seq_in_c, transitional)
    return _legacy_window_scale(clip, single, pauses, batch_count)


def _wing_window(clip, pauses: list, batch_count: int,
                 seq_in_c: float, transitional: float) -> tuple:
    """两翼各自归位（R152 核心）：窗口取过渡翼、错峰步长取延迟翼。

    - 单台窗口 = **过渡翼**；没拖过渡翼 → 单台瞬变（`_R152_MIN_WINDOW`），
      而不是把延迟翼摊给每台（旧式 `single = total/批次数` = 用户报的"没反应"）；
    - 错峰步长 = **(延迟翼 − 停顿合计)/(批次数−1)**：组间停顿算在延迟翼预算内
      （用户："最终时长**只由**我拖的两个翼决定"）→ 不得顶出条带；批次=1 无错峰。
    """
    batches = max(1, int(batch_count))
    window = transitional if transitional > 1e-4 else _R152_MIN_WINDOW
    pause_total = max(0.0, float(sum(pauses)))
    step = (max(0.0, seq_in_c - pause_total) / float(batches - 1)
            if batches > 1 else 0.0)
    total = step * float(batches - 1) + pause_total + window if batches > 1 else window
    return _clamp_window_to_duration(clip, window, step, total, pauses,
                                     seq_in_c=seq_in_c,
                                     transitional=transitional, batches=batches)


def _legacy_window_scale(clip, single: float, pauses: list, batch_count: int) -> tuple:
    """无翼旧路径：按 `duration / 已存跨度` 等比缩放 single 与停顿（旧工程逐字节不变）。"""
    duration = max(0.0, float(getattr(clip, "duration", 0.0) or 0.0))
    if duration <= 1e-4:
        return single, pauses, None
    stored_span = max(0.0, single * max(1, int(batch_count)) + float(sum(pauses)))
    if stored_span <= 1e-6 or abs(stored_span - duration) <= 1e-6:
        return single, pauses, None
    scale = duration / stored_span
    return (max(0.001, single * scale),
            [max(0.0, p * scale) for p in pauses],
            None)


def _log_seq_clamp(clip, total: float, duration_c: float, scale: float,
                   ctx: dict) -> None:
    """[SEQ] 压紧诊断的**限频**落笔（R181 P2-2；从 `_clamp_window_to_duration` 抽出守 CC 门禁）。

    逐帧热路径 → 同内容 5s 一条；签名（clip/跨度/翼/批次）变化立即记，
    抑制条数附在下一次落笔尾部（诊断信息不丢）。
    """
    sig = (str(getattr(clip, "id", "") or "?")[:8], round(total, 3),
           round(duration_c, 3), int(ctx.get("batches", 1) or 1),
           round(float(ctx.get("seq_in_c", 0.0) or 0.0), 3),
           round(float(ctx.get("transitional", 0.0) or 0.0), 3))
    ok, supp = _SEQ_LOG_THROTTLE.allow(sig, sig)
    if not ok:
        return
    log.warning(
        "[SEQ] 分组过渡跨度 %.3fs > clip 时长 %.3fs → 等比压缩（含停顿）"
        "(延迟翼=%.3f 过渡翼=%.3f 批次=%s, 比=%.3f) clip=%s%s",
        total, duration_c, ctx.get("seq_in_c", 0.0), ctx.get("transitional", 0.0),
        ctx.get("batches", 1), scale, str(getattr(clip, "id", "") or "?")[:8],
        format_suppressed(supp))


def _clamp_window_to_duration(clip, window: float, step: float, total: float,
                              pauses: list, **ctx) -> tuple:
    """把扫掠跨度**夹进 clip 时长**（R149 用户硬要求；R152 改成"夹取有日志"）。

    用户原话::

        "clip之前可能根本跑不完，因为过渡时长过长，且没有遵循clip的实际长度"

    翼拖得比 clip 还长时按 clip 时长**等比压紧**（最后一组恰好在 clip 末尾到位）。
    ★ R152：不再**静默**夹逼 —— 记一条 warning 便于发现 UI 侧写入异常，但数值照夹
    （这是用户的硬要求，不能放任过渡跑不完）。
    ★ R181 P2-2：该 warning 逐帧重发（真工程 2 分钟 ~100KB）→ 改限频落笔。
    """
    duration_c = max(0.0, float(getattr(clip, "duration", 0.0) or 0.0))
    if duration_c > 1e-4 and total > duration_c + 1e-6:
        scale = duration_c / max(total, 1e-9)
        window = max(_R152_MIN_WINDOW, window * scale)
        step = max(0.0, step * scale)
        # ★ 停顿也必须一起压 —— 否则"停顿主导"时压完仍超时长（真工程 `ec94e6ee`：
        #   压后 1.0s 停顿 + 2.31s 窗口 = 3.31s > 时长 3.07s，过渡仍跑不完）。
        pauses = [max(0.0, float(p) * scale) for p in (pauses or [])]
        _log_seq_clamp(clip, total, duration_c, scale, ctx)
    return window, pauses, step


def _parallel_in_window(clip, duration, delay, fade, gp_ctx):
    """并行**淡入**：各台的 `(t_start, win)`（R166 抽成模块级以压方法行数/CC）。

    两组模型：拖了延迟翼 → `t_start = 位置×延迟翼`、`win = 过渡翼`（装不下时先由
    `_parallel_wing_fit` 渲染期压紧）；没拖 → 旧耦合式（窗口由 `fade/组内台数` 派生，
    保持逐字节一致）。
    """
    group_pos, group_sizes = gp_ctx
    if delay > 1e-4:
        delay, fade = _parallel_wing_fit(clip, duration, delay, fade, kind="in")
        return group_pos * delay, max(1e-6, fade)
    win = fade / np.maximum(group_sizes, 1.0)
    return group_pos * np.maximum(0.0, fade - win), win


def _parallel_out_window(clip, duration, delay, fade, gp_ctx):
    """并行**淡出**：各台的 `(t_start, win)`（镜像 `_parallel_in_window`）。"""
    group_pos, group_sizes = gp_ctx
    if delay > 1e-4:
        delay, fade = _parallel_wing_fit(clip, duration, delay, fade, kind="out")
        return (duration - (delay + fade)) + group_pos * delay, max(1e-6, fade)
    win = fade / np.maximum(group_sizes, 1.0)
    return (duration - fade) + group_pos * np.maximum(0.0, fade - win), win


def _group_ramp_gains(engine, clip, local_t, t_start, win, *, fade_in, ease_lut):
    """由 `(t_start, win)` 产出 512 通道的台阶增益（并行路径两个方向共用）。

    淡入：`t_start` 前为 0、`t_end` 后为 1；淡出：反向。与改前逐字节等价
    （只把同一算式搬到模块级，便于两方向复用与单测）。
    """
    t_end = t_start + win
    if fade_in:
        tt = np.clip((local_t - t_start) / np.maximum(win, 1e-6), 0.0, 1.0)
    else:
        tt = np.clip((t_end - local_t) / np.maximum(win, 1e-6), 0.0, 1.0)
    g = engine._clip_transition_gains(clip, tt, fade_in=fade_in, ease_lut=ease_lut)
    if fade_in:
        g = np.where(local_t < t_start, 0.0, g)
        return np.where(local_t >= t_end, 1.0, g).astype(np.float32)
    g = np.where(local_t >= t_end, 0.0, g)
    return np.where(local_t < t_start, 1.0, g).astype(np.float32)


def _parallel_wing_fit(clip, duration_eff: float, delay: float, fade: float,
                       *, kind: str) -> tuple:
    """把**并行路径**的「延迟翼 + 过渡翼」夹进 clip 时长（R166 自审补缺）。

    ## 为什么需要

    用户硬要求::

        "如果是过渡效果的话一个硬要求，必须在这个clip的生命周期内把效果跑完"

    顺序路径有 `_clamp_window_to_duration` 压紧，**并行路径一直没这道** —— R163 把
    UI 的破坏性裁切去掉（翼成为唯一权威）之后，"翼 > 时长"成为**合法声明**（真工程
    `94e08094`/`34a9720b`/`e48651ca` 三条即是），于是并行路径会出现：实测合成 clip
    （时长 1.0s / 延迟翼 3.0 / 过渡翼 2.0）→ 后 4 台**到 1.02s 才到位**，过渡跑到了
    clip 生命周期之外（用户明令禁止）。

    ## 做法

    并行路径第 i 台（`pos_i ∈ [0,1]`）的窗口是 `t_start = pos_i × 延迟翼`、
    `win = 过渡翼`，故**最晚到位 = 延迟翼 + 过渡翼**；超过时长就按
    `ratio = 时长 /(延迟翼 + 过渡翼)` **等比压紧两者**（最后一台恰好在 clip 末尾到位）。
    淡出侧同理（最早开始 = `时长 −(延迟翼+过渡翼)`，为负说明淡出跑到 clip 之外）。

    **非破坏性**：只压当次渲染用的局部量，**绝不改 clip 字段** —— 拉长 clip 后自动恢复
    （R163 定下的铁律）。

    返回 `(delay, fade)`。
    """
    duration_c = max(0.0, float(duration_eff or 0.0))
    total = float(delay) + float(fade)
    if duration_c <= 1e-4 or total <= duration_c + 1e-6:
        return delay, fade
    ratio = duration_c / max(total, 1e-9)
    _log_par_clamp(clip, (total, duration_c, float(delay), float(fade), kind, ratio))
    return float(delay) * ratio, float(fade) * ratio


def _log_par_clamp(clip, sig_vals: tuple) -> None:
    """[PAR] 压紧诊断的**限频**落笔（R181 P2-2；从 `_parallel_wing_fit` 抽出守门禁）。

    `sig_vals` = (跨度, 时长, 延迟翼, 过渡翼, 侧, 比)。本函数每帧对每个 clip 各调一次
    （in/out 两向），修前同一 clip 稳态下逐帧刷同一行 warning —— 真实工程「水仙十字」
    2 分钟约 100KB 日志。现按内容签名限频：签名变化（clip/翼比变化）立即记；
    同内容每 5s 最多一条，抑制条数附在下次落笔尾部（诊断信息不丢）。
    """
    total, duration_c, delay, fade, kind, ratio = sig_vals
    sig = (str(getattr(clip, "id", "") or "?")[:8], str(kind), round(total, 3),
           round(duration_c, 3), round(delay, 3), round(fade, 3))
    ok, supp = _PAR_LOG_THROTTLE.allow(sig, sig)
    if not ok:
        return
    log.warning(
        "[PAR] 并行过渡跨度 %.3fs > clip 时长 %.3fs → 等比压缩"
        "(延迟翼=%.3f 过渡翼=%.3f 侧=%s, 比=%.3f) clip=%s%s",
        total, duration_c, delay, fade, kind, ratio,
        str(getattr(clip, "id", "") or "?")[:8], format_suppressed(supp))


def _normal_mode_time(engine, clip, source_t: float, mode: str, end_t: float) -> float:
    """normal 模式的 source_t：优先"拖长按周期扩展"，否则照原样 clamp 到 end_t。"""
    cycled = _normal_cycle_time(engine, clip, source_t, mode, 0.0)
    return cycled if cycled is not None else min(source_t, end_t)


def _run_mode_span(engine, clip, source_duration: float) -> float:
    """reverse / pingpong 的映射范围：能按周期扩展时用**记录跨度**，否则声明源时长。

    只在确实能构成周期（`_recorded_cycle` 非 None）时收窄 —— 与 `_normal_cycle_time`
    同口径：`resize_cycle=False`（显式变速过）、单帧 / 同刻帧一律返回
    `source_duration`（旧语义、零行为变化）。
    """
    duration = max(1e-6, float(source_duration))
    if not bool(getattr(clip, "resize_cycle", True)):
        return duration
    if _recorded_cycle(engine, clip) is None:
        return duration
    recorded = engine._clip_recorded_source_span(clip, source_duration)
    return duration if recorded <= 1e-6 else recorded


def _run_mode_cycled_time(engine, clip, source_t: float, span: float) -> float:
    """把 source_t 折进 `[0, span]`：跨度内原样；超出按记录周期套回（同 normal）。"""
    t = max(0.0, float(source_t))
    if t <= span:
        return t
    cycled = _resize_cycle_time(engine, clip, t)
    return t if cycled is None else min(max(0.0, cycled), span)


def _back_and_forth_time(engine, clip, source_t: float, source_duration: float,
                         mode: str) -> float:
    """reverse / pingpong 的 source_t（映射范围见 `_run_mode_span`）。

    ★★ R141 修复：修前这两个模式用 `source_duration`（clip **声明**源时长）作映射
      范围，而 `normal` 早已按用户 2026-09-14 定案改用**记录跨度**周期扩展 —— 于是
      把 clip 拖长（录 2.0s、拖到 8.0s）后选 reverse / pingpong，映射出的 source_t
      远超记录跨度、那里没有帧：实测 reverse 要等到最后 2 秒才有内容、pingpong
      中间 6 秒全是空白/冻结。现与 normal 同源，返回值恒落在 `[0, span]`。
    """
    span = _run_mode_span(engine, clip, source_duration)
    end_t = max(0.0, span - 1e-4)
    t = _run_mode_cycled_time(engine, clip, source_t, span)
    if mode == "reverse":
        return max(0.0, end_t - min(t, end_t))
    progress = min(t / span, 1.0)
    phase = progress * 2.0
    mapped = phase if phase <= 1.0 else 2.0 - phase
    return min(max(0.0, mapped * span), end_t)
