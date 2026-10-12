# -*- coding: utf-8 -*-
"""Clip 输出滤网（临场后处理）—— 泵主控 / 电机（限位·限速）/ 灯禁用。

用户需求（2026-09-20 R110）::

    "「电机（限位／限速）」「泵主控」「灯启用和禁用」—— 用于调节临场播放时的
     功能限位需求：
     · 泵主控：把 clip 里所有泵输出往低同比例缩放（像 AU 调响度），不支持放大，
       最低全部压到 0；
     · 灯禁用：白天时把所有设备的灯输出最高优先级设成全 0（不是输出 0 值，
       而是直接不输出）；
     · 电机（限位／限速）：把电机摆动往 128 默认归位角缩放控制幅度；限速则是
       虚拟一个最大摆动速度，把 clip 里的电机输出滤一遍。
     三个功能都是**不改原来录制的数据、加一层滤网**，随时开关不影响。"

设计约束（架构门禁 tools/arch_baseline.json）：
  · `PlaybackEngine` 是上帝类 → 本模块**全部为模块级函数**，不加类方法；
  · 挂点函数行数余量极小 → 对外收口为 `finalize_clip_output`（含钳位，
    调用点可**替换**既有 `np.clip` 行 → 净零行）；
  · `engine` 不得 import `src.ui`（反层规则）→ 分类常量取自 `core.constants`。

三滤网只改**输出侧帧数据**（`frame.data` 原样不动）：
  泵主控 `pump_master_scale` ∈ [0,1]：泵通道 v ← v×scale
  电机限位 `motor_limit_scale` ∈ [0,1]：粗通道 v ← home+(v-home)×scale
            （home = 128 常规 / 0 绝对类），精通道 v ← v×scale
  电机限速 `motor_speed_limit_dps`（度/秒，0=关）：按设备的
            `FixtureDefinition.motor_ranges[ct]` 折算每帧最大 DMX 步长逐帧限幅；
            推进量取 clip **局部时间**，seek/循环/变速时局部时间跳变即自动重对齐。

★ 代码风格注意：本模块受 CC≤8 门禁约束，而 CC 计入 `and/or/if/for/except`
  —— 故刻意使用**三元表达式**（`IfExp` 不计 CC）与小工具函数替代布尔运算。
"""
from __future__ import annotations

import sys

import numpy as np

try:
    from ..core.constants import (
        ABSOLUTE_HOME_MOTOR_CHANNELS,
        LIGHT_OUTPUT_CHANNEL_TYPES,
        MOTOR_CENTER_HOME,
        MOTOR_COARSE_CHANNELS,
        MOTOR_FINE_CHANNELS,
        PUMP_CHANNEL_TYPES,
    )
except Exception:  # pragma: no cover - 独立探针运行时的回退
    ABSOLUTE_HOME_MOTOR_CHANNELS = frozenset({"motor_z"})
    LIGHT_OUTPUT_CHANNEL_TYPES = frozenset({"r", "g", "b", "w", "dimmer"})
    MOTOR_CENTER_HOME = 128
    MOTOR_COARSE_CHANNELS = frozenset({"motor_x", "motor_y", "motor_z"})
    MOTOR_FINE_CHANNELS = {"motor_x": "motor_x_fine", "motor_y": "motor_y_fine",
                           "motor_z": "motor_z_fine"}
    PUMP_CHANNEL_TYPES = frozenset({"pump"})

_EPS = 1e-6
#: 局部时间跳变超过此值（秒）= seek / 循环 / 大跳 → 该帧当新起点（不对齐历史）
_RATE_REENTRY_DT = 0.5
#: 无角度范围时的回退幅面（度）—— UI 侧标注"未配置角度范围"
_FALLBACK_DEG_SPAN = 360.0
#: 限速状态表容量（按 clip 分键；超限整体清空防无界增长）
_RATE_STATE_CAP = 64

_FINE_NAMES = frozenset(MOTOR_FINE_CHANNELS.values())
_STATE_ATTR = "_clip_output_filter_state"
_GEOM_ATTR = "_clip_fade_geometry_cache"


def _native_filter(request):
    native = sys.modules.get("qascade_ce.native_output_filter")
    if native is None:
        return None
    return native.apply(request)


def _filter_request(engine, clip, universe, data, context):
    return dict(engine=engine, clip=clip, universe=universe, data=data, **context)


# ── 小工具（吞掉条件分支，压低各函数 CC）───────────────────────────────────

def _to_float(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _to_int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _clamp01(v, default: float = 1.0) -> float:
    return max(0.0, min(1.0, _to_float(v, default)))


def _span_of(rng) -> float:
    """角度范围 (min,max) → 幅面（度）；非法/退化 → 0.0。"""
    try:
        span = abs(float(rng[1]) - float(rng[0]))
    except (TypeError, ValueError, IndexError):
        return 0.0
    return span if span > 1.0 else 0.0


def _seq(v) -> list:
    """`None → []` 归一（避免 `or []` 的 BoolOp 计费）。"""
    return v if isinstance(v, (list, tuple)) else []


# ── 通道分类掩码（按 universe，带身份校验缓存）─────────────────────────────

def _empty_masks() -> dict:
    return {
        "pump": np.zeros(512, dtype=bool),
        "motor_coarse": np.zeros(512, dtype=bool),
        "motor_fine": np.zeros(512, dtype=bool),
        "light": np.zeros(512, dtype=bool),
        "motor_home": np.full(512, float(MOTOR_CENTER_HOME), dtype=np.float32),
    }


def _classify_one(channels, start: int, out: dict) -> None:
    """把一台设备的通道序列按类型填进掩码（addr = start + offset）。"""
    for offset, raw in enumerate(_seq(channels)):
        addr = start + offset
        if addr >= 512:
            return
        ct = str(raw).strip().lower()
        out["pump"][addr] |= ct in PUMP_CHANNEL_TYPES
        out["motor_coarse"][addr] |= ct in MOTOR_COARSE_CHANNELS
        out["motor_fine"][addr] |= ct in _FINE_NAMES
        out["light"][addr] |= ct in LIGHT_OUTPUT_CHANNEL_TYPES
        out["motor_home"][addr] = (
            0.0 if ct in ABSOLUTE_HOME_MOTOR_CHANNELS else float(MOTOR_CENTER_HOME)
        )


def _proj_patch_rev(proj) -> int:
    """该工程的配接版本号（无该字段的旧对象 → 0，行为与加版本号之前一致）。

    ★ R141：**原地改配接**（`start_channel`/`universe`/`def_id`，或
      `FixtureDefinition.channels`）时 `id(proj)/len(fixtures)/len(fixture_defs)`
      与列表身份**全都不变** → 掩码缓存陈旧命中 → 泵主控/电机限位限速按**旧地址**
      打（实测确认：设备从地址 1 挪到 100，掩码仍命中地址 2）。
      版本号由 `Project.bump_patch_rev()` 递增，配接面板改动后调用。
    """
    return int(getattr(proj, "patch_rev", 0) or 0)


def _mask_cache(engine, kind: str, universe: int, proj):
    """取/建该 (kind, universe) 的缓存项；返回 `(cache, key, hit_or_None)`。

    缓存字典按需建在宿主对象上（PlaybackEngine 已有；NetworkManager 无 → 惰性
    创建，避免每帧重建掩码）。
    """
    fixtures = _seq(getattr(proj, "fixtures", None))
    fdefs = _seq(getattr(proj, "fixture_defs", None))
    key = (kind, int(universe), id(proj), len(fixtures), len(fdefs),
           _proj_patch_rev(proj))
    cache = getattr(engine, _GEOM_ATTR, None)
    if cache is None:
        cache = {}
        try:
            setattr(engine, _GEOM_ATTR, cache)
        except Exception:
            return None, key, None
    hit = cache.get(key)
    if hit is not None and hit[0] is proj and hit[1] is fixtures and hit[2] is fdefs:
        return cache, key, hit[3]
    return cache, key, None


def _mask_store(engine, cache, key, proj, value) -> None:
    if cache is None:
        return
    fixtures = _seq(getattr(proj, "fixtures", None))
    fdefs = _seq(getattr(proj, "fixture_defs", None))
    cache[key] = (proj, fixtures, fdefs, value)


def channel_type_masks(engine, universe: int, proj) -> dict:
    """该 universe 的通道分类：`{pump, motor_coarse, motor_fine, light}` 各
    bool[512] ＋ `motor_home`（每通道归位基准）。

    缓存复用引擎既有几何缓存，命中前 `is` 复核 proj/fixtures/fixture_defs 身份
    —— 列表被替换后不会陈旧命中。
    """
    cache, key, hit = _mask_cache(engine, "__output_filter_masks__", universe, proj)
    if hit is not None:
        return hit
    out = _empty_masks()
    by_id = {str(getattr(fd, "id", "")): fd
             for fd in _seq(getattr(proj, "fixture_defs", None))}
    for fi in _seq(getattr(proj, "fixtures", None)):
        uni = _to_int(getattr(fi, "universe", 1), 1)
        try:
            start = _to_int(getattr(fi, "start_channel", 1), 1) - 1
        except Exception:
            continue
        fd = by_id.get(str(getattr(fi, "def_id", "")))
        if uni == int(universe) and 0 <= start < 512 and fd is not None:
            _classify_one(getattr(fd, "channels", None), start, out)
    _mask_store(engine, cache, key, proj, out)
    return out


# ── 角度范围（限速换算用）────────────────────────────────────────────────────

def universe_deg_spans(engine, universe: int, proj) -> dict:
    """该 universe 的 `{粗通道名: 幅面角度}`（跨设备取中位；缺失 → 满幅回退）。"""
    cache, key, hit = _mask_cache(engine, "__output_filter_span__", universe, proj)
    if hit is not None:
        return hit
    buckets: dict = {}
    by_id = {str(getattr(fd, "id", "")): fd
             for fd in _seq(getattr(proj, "fixture_defs", None))}
    for fi in _seq(getattr(proj, "fixtures", None)):
        if _to_int(getattr(fi, "universe", 1), 1) != int(universe):
            continue
        fd = by_id.get(str(getattr(fi, "def_id", "")))
        for ct in MOTOR_COARSE_CHANNELS:
            span = _span_of((getattr(fd, "motor_ranges", None) if fd is not None else None
                             or {}).get(ct))
            if span > 0.0:
                buckets.setdefault(ct, []).append(span)
    out = {ct: (float(np.median(v)) if v else _FALLBACK_DEG_SPAN)
           for ct, v in buckets.items()}
    for ct in MOTOR_COARSE_CHANNELS:
        out.setdefault(ct, _FALLBACK_DEG_SPAN)
    _mask_store(engine, cache, key, proj, out)
    return out


def has_angle_ranges(proj) -> bool:
    """工程里是否至少有一个设备类型带电机角度范围（UI 提示用）。"""
    for fd in _seq(getattr(proj, "fixture_defs", None)):
        ranges = getattr(fd, "motor_ranges", None)
        for ct in MOTOR_COARSE_CHANNELS:
            if _span_of((ranges if isinstance(ranges, dict) else {}).get(ct)) > 0.0:
                return True
    return False


# ── 参数与主滤网 ─────────────────────────────────────────────────────────────

def clip_filter_params(clip) -> tuple:
    """`(限位系数, 限速度/秒, 泵系数)`，带钳位与容错。"""
    return (
        _clamp01(getattr(clip, "motor_limit_scale", 1.0)),
        max(0.0, _to_float(getattr(clip, "motor_speed_limit_dps", 0.0), 0.0)),
        _clamp01(getattr(clip, "pump_master_scale", 1.0)),
    )


def filters_active(clip) -> bool:
    """是否任一滤网生效（默认值 = 全不生效）。"""
    scale_m, dps, scale_p = clip_filter_params(clip)
    return scale_m < 1.0 - _EPS or dps > _EPS or scale_p < 1.0 - _EPS


def output_filters_signature(clip) -> tuple:
    """滤网参数签名（渲染 memo / 烘焙缓存失效判定用）。"""
    scale_m, dps, scale_p = clip_filter_params(clip)
    return (round(scale_m, 4), round(dps, 4), round(scale_p, 4))


def _apply_motor_limit(arr: np.ndarray, masks: dict, scale: float) -> None:
    """电机限位：粗通道围绕各自归位基准缩放；精通道同系数缩放。"""
    idx = np.flatnonzero(masks["motor_coarse"])
    if idx.size:
        home = masks["motor_home"][idx]
        arr[idx] = home + (arr[idx] - home) * scale
    fine = masks["motor_fine"]
    if np.any(fine):
        arr[fine] *= scale


def _max_step_per_addr(engine, universe: int, masks: dict, proj, *,
                       dps: float, dt: float) -> np.ndarray:
    """每通道本帧允许的最大 DMX 变化量（float32[512]）。

    粗通道 `dps(度/秒) × (255/幅面度) × dt`；精通道取粗通道步长 / 256
    （16bit 拆分语义：精字节是粗字节的小数位）。
    """
    spans = universe_deg_spans(engine, universe, proj)
    step = np.zeros(512, dtype=np.float32)
    coarse, fine = masks["motor_coarse"], masks["motor_fine"]
    for ct in MOTOR_COARSE_CHANNELS:
        per_deg = 255.0 / max(1.0, _to_float(spans.get(ct), _FALLBACK_DEG_SPAN))
        step[coarse] = np.maximum(step[coarse], np.float32(dps * per_deg * dt))
    for coarse_name in MOTOR_FINE_CHANNELS:
        per_deg = 255.0 / max(1.0, _to_float(spans.get(coarse_name), _FALLBACK_DEG_SPAN))
        step[fine] = np.maximum(step[fine], np.float32(dps * per_deg * dt / 256.0))
    return step


def _rate_store(engine) -> dict:
    store = getattr(engine, _STATE_ATTR, None)
    if store is None:
        store = {}
        setattr(engine, _STATE_ATTR, store)
    elif len(store) > _RATE_STATE_CAP:
        store.clear()
    return store


def _rate_dt(prev, local_t: float) -> float:
    """本帧局部时间推进量；状态缺失 / 异常 → inf（调用方按"重入"处理）。"""
    if prev is None:
        return float("inf")
    try:
        return float(local_t) - float(prev[1])
    except Exception:
        return float("inf")


def _apply_motor_rate(engine, clip, universe: int, arr: np.ndarray, masks: dict,
                      *, dps: float, local_t: float, proj) -> None:
    """按虚拟最大摆动速度逐帧限幅（状态按 clip×universe×dps 缓存）。"""
    target = masks["motor_coarse"] | masks["motor_fine"]
    if not np.any(target):
        return
    store = _rate_store(engine)
    key = (str(getattr(clip, "id", "") or id(clip)), int(universe), round(float(dps), 4))
    prev = store.get(key)
    dt = _rate_dt(prev, local_t)
    if not np.isfinite(dt) or dt <= 1e-9 or dt > _RATE_REENTRY_DT:
        store[key] = (arr.copy(), float(local_t))     # 重入/首帧：对齐不过滤
        return
    last = prev[0]
    step = _max_step_per_addr(engine, universe, masks, proj, dps=dps, dt=dt)
    idx = np.flatnonzero(target)
    s = step[idx]
    arr[idx] = np.clip(arr[idx], last[idx] - s, last[idx] + s)
    store[key] = (arr.copy(), float(local_t))


def clear_rate_state(engine, clip=None) -> None:
    """清限速状态（滤网关闭 / clip 变更 → 再次开启从当前帧对齐）。"""
    store = getattr(engine, _STATE_ATTR, None)
    if not store:
        return
    if clip is None:
        store.clear()
        return
    cid = str(getattr(clip, "id", "") or id(clip))
    for k in [k for k in store if k[0] == cid]:
        store.pop(k, None)


def apply_clip_output_filters(engine, clip, universe: int, data: np.ndarray,
                              *, local_t: float, proj) -> np.ndarray:
    """对一帧 clip 输出应用三个滤网，返回（可能是同一个）float32 数组。

    ★ 全默认值时**原样返回**（零拷贝 / 零影响）：保证"不改录制数据"，也让未用
    该功能的工程输出逐字节不变。
    """
    if clip is None or data is None or not filters_active(clip):
        return data
    native = _native_filter(_filter_request(engine, clip, universe, data,
        dict(local_t=local_t, proj=proj, finalize=False)))
    if native is not None:
        return native
    return _apply_clip_output_filters_python(engine, clip, universe, data,
                                             dict(local_t=local_t, proj=proj))


def _apply_clip_output_filters_python(engine, clip, universe, data, context):
    local_t, proj = context["local_t"], context["proj"]
    scale_m, dps, scale_p = clip_filter_params(clip)
    arr = np.asarray(data)
    if arr.dtype != np.float32:
        arr = arr.astype(np.float32)
    masks = channel_type_masks(engine, universe, proj)
    if scale_p < 1.0 - _EPS and np.any(masks["pump"]):
        arr[masks["pump"]] *= scale_p
    if scale_m < 1.0 - _EPS:
        _apply_motor_limit(arr, masks, scale_m)
    if dps > _EPS:
        _apply_motor_rate(engine, clip, universe, arr, masks,
                          dps=dps, local_t=float(local_t), proj=proj)
    else:
        clear_rate_state(engine, clip)
    return arr


def finalize_clip_output(engine, clip, universe: int, data: np.ndarray,
                         *, local_t: float, proj) -> np.ndarray:
    """滤网 + 钳位（**原地**），返回同数组 —— 供调用点**替换**既有 `np.clip` 行。

    把钳位收进来是为了让挂点净零行（`_sample_transition_clip_output` 等函数在
    架构门禁里 0 余量）。原地语义与直接 `np.clip(..., out=data)` 一致。
    """
    native = _native_filter(_filter_request(engine, clip, universe, data,
        dict(local_t=local_t, proj=proj, finalize=True)))
    if native is not None:
        return native
    arr = apply_clip_output_filters(engine, clip, universe, data,
                                    local_t=local_t, proj=proj)
    arr = apply_light_suppress(engine, universe, arr, proj)     # 全局灯禁用（含离线/导出）
    np.clip(arr, 0.0, 255.0, out=arr)
    return arr


# ── 灯禁用（全局，会话级）────────────────────────────────────────────────────

#: 全局"灯输出禁用"标志（会话级，不写入工程 —— 现场白天临时状态）。
#: 置位后：实时输出/监视器/离线渲染与导出都不含灯输出；**录制照常录原始值**。
_LIGHT_SUPPRESSED = False


def set_light_suppressed(on: bool) -> bool:
    """设置全局灯禁用（返回设置后的状态）。"""
    global _LIGHT_SUPPRESSED
    _LIGHT_SUPPRESSED = bool(on)
    return _LIGHT_SUPPRESSED


def light_suppressed(proj=None) -> bool:
    """新节目读取持久字段；无节目/未迁移旧档沿用原会话门控。"""
    from ..core.program_lighting import active_light_enabled
    enabled = active_light_enabled(proj)
    return _LIGHT_SUPPRESSED if enabled is None else not enabled


def light_channel_mask(engine, universe: int, proj) -> np.ndarray:
    """该 universe 的灯通道掩码（灯禁用 / 监视器归属扣除共用）。"""
    try:
        return channel_type_masks(engine, universe, proj)["light"]
    except Exception:
        return np.zeros(512, dtype=bool)


def apply_light_suppress(engine, universe: int, data: np.ndarray, proj) -> np.ndarray:
    """把灯通道置 0（**原地**，返回同数组）。

    语义 = 用户要的"直接不输出"（不是写 0 值）：在输出末级把灯通道清零，故
    任何层（clip/base/shape）来的灯值都不会到硬件。关闭时立即原样返回（零影响）。
    """
    if not light_suppressed(proj) or data is None:
        return data
    mask = light_channel_mask(engine, int(universe), proj)
    if mask.size != np.asarray(data).size:
        return data
    native = sys.modules.get("qascade_ce.native_output_filter")
    if native is not None:
        result = native.suppress(data, mask)
        if result is not None:
            return result
    arr = np.asarray(data)
    arr[mask] = 0
    return arr
