# -*- coding: utf-8 -*-
"""Clip 覆盖接管的逐设备到达守卫（2026-08-22）。

背景：
  现有 clip 合成按「clip 全局优先级」做 winner-take-all，新 clip 一旦进入
  就会覆盖旧 clip 的全部活跃通道。但对设置了方向/分组过渡的旧 clip，设备是
  按时间差逐个到达的：在某个设备还没被旧 clip“跑到”之前，新 clip 不应强行
  覆盖它，否则会打断泵/电机从左到右或按组的实际到达顺序。

本模块只做两件小事：
  1. 计算“未到达”掩码：方向性淡入窗口内 `dir_gains==0` 且该 clip 活跃的通道。
  2. 给这些未到达通道加超大分数，使它们在逐通道竞争中保持旧值，直到旧 clip
     真正到达该设备后再交给正常优先级接管。

整体过渡（无方向/分组）不启用守卫，行为与旧版完全一致。
"""
from __future__ import annotations

import numpy as np


def reset_playback_consumption(engine, *, clear_boundary=True):
    """Start a playback pass with ownership and its derived memos in sync."""
    from .transition_anchor_causality import clear_anchor_state
    clear_anchor_state(engine)
    engine._hold_consumed_masks.clear()
    engine._takeover_consumed_masks.clear()
    engine._dirty_render_item_memo.clear()
    engine._dirty_render_uni_memo.clear()
    if clear_boundary:
        engine._hold_boundary_latch.clear()


def reset_consumption_on_rewind(engine, now):
    """Forward active ticks retain takeover; a backwards render begins a pass."""
    from .native_frame_clock import rewind_needed
    previous = engine._last_render_time
    if rewind_needed(now, previous):
        reset_playback_consumption(engine)

# 远大于 transition_boost（(total_priority+1)*1e9），保证未到达设备优先于
# 任何过渡/新 clip 的接管请求。
_PRE_ARRIVAL_BOOST_SCALE = 1e12


def clip_pre_arrival_mask(
    active: np.ndarray,
    dir_gains_arrived: np.ndarray | None,
    is_in_directional_in: bool,
) -> np.ndarray:
    """返回 512 维 bool：方向性淡入中尚未到达、但旧 clip 仍持有的通道。

    - dir_gains_arrived 为 None（无方向/分组过渡）→ 全 False。
    - 不在方向性淡入窗口内 → 全 False。
    - 否则 = active & (~arrived)。
    """
    if dir_gains_arrived is None or not is_in_directional_in:
        return np.zeros(512, dtype=bool)
    from .native_ownership import mask_without
    return mask_without(active, dir_gains_arrived)


def apply_pre_arrival_boost(
    scores: np.ndarray,
    pre_arrival_mask: np.ndarray,
    total_priority: int,
) -> None:
    """给未到达通道加超大分数，使其不被后到/更高优先级 clip 强覆盖。"""
    if np.any(pre_arrival_mask):
        scores[pre_arrival_mask] = scores[pre_arrival_mask] + (
            (float(total_priority) + 1.0) * _PRE_ARRIVAL_BOOST_SCALE)


def fade_in_branch_taken(directional: bool, directional_in: bool, fade_in: bool, fade_out: bool) -> bool:
    """本帧数据是否走了淡入分支 `prev + g*(raw-prev)`（与渲染分支同序：方向性先判淡出，整体先判淡入）。"""
    if directional:
        return bool(directional_in and not fade_out)
    return bool(fade_in)


def transition_boost_mask(active: np.ndarray, data, idle_target, content=None, ramp_in: bool = False) -> np.ndarray:
    """过渡加分该保护的通道 = 本次过渡**真的在动**的通道（R143）。

    用户实报（2026-09-22）::

        "一个 clip 管泵，另一个管电机，两个效果根本无交集，但是电机却必须等泵的
         过渡做完之后才会突然有效果了"

    真因：录制时"没动过"的通道也会进 `active_channels_by_universe` 并集 ——
    泵 clip 的并集里带着**电机停在默认 128（归中）**的地址。它一进淡入窗口，
    `_transition_boost = (total_priority+1)*1e9` 就把它在**电机通道**上的分数抬到
    后起的电机 clip 抢不过 → 电机被压在 128；等泵的淡入窗口走完、boost 归零，
    电机才**突然**变成 200（与用户描述逐字吻合，探针实测
    `probe_r143_boost.py`：假设组 1.0~2.5s 恒 128，3.0s 起才 200）。

    判据：**渲染值等于该 clip 的 "空闲基准值"（`idle_target`）→ 这一帧它没有在
    这个通道上表达任何内容**，不该借过渡加分把可达的后起 clip 挡在外面。
    真实内容（泵 255、电机 200）不等于基准值 → 加分原样保留，过渡帧保护不回归。

    只**收窄**、绝不放大：排除掉的通道全是"值恰好等于基准值"的，属保守方向。

    ★ R173：调用方传**量化前的 float 渲染值**。默认仍按原 R143 口径判"输出（截成 uint8
    后）等于基准值"；唯一例外是 `ramp_in=True`（本帧走淡入分支 `prev+g*(raw-prev)`）、
    基准值 >0（电机归中 128）且内容 `content`（源帧 raw）离基准值超过一个量化步时只认
    float **精确**等于：

      * 淡入是从锚点**穿越**居中基准值走向内容，途经 128.6 截成 128 那一帧通道正在动，
        按 uint8 判会丢加分、被后起 clip 抢走一帧 = 单帧闪（水仙十字 d5d00ccd@99.74 /
        cefec294@127.52 / a086df27@136.20，全在淡入窗口）。
      * 内容本身就在基准值 ±1 内（录制的"归中"值 129）：淡入 128.2→129 全程截成 128 = 没在
        表达内容，照原口径让位；只认精确值会借加分把后起 clip 的真实电机动作整段压平（至冬7.0
        c4ca9529@172.86 把 bb64d7e2 的 128→58→126 压成恒 128 共 0.7s = R143 用户实报形态）。
      * 淡出是 `target+g*(raw-target)` 向基准值**收敛**，截成基准值 = 这台已经淡完，照原
        口径让位；若淡出也只认精确值，会把 128.0x/128.6 的收敛尾巴按住挡后起 clip
        （至冬7.0 761220ef@92.96 压 128 共 11 帧、2b310c2f@157.20 压 9 帧、
        a80b7b7a@134.64 单帧 128 闪）。
      * 基准 0（灯/泵）：0 是量程下界只会从上方逼近，淡入也按 uint8 判；只认精确 0 时
        灯跟踪衰减尾巴 0.8/0.5/… 会借加分把后起 clip 的 255 挡成黑隙（至冬7.0
        75bdc2f4@71.48 u5c31 黑 5 帧）。
      * 已知残留（与 HEAD 逐帧一致，非本口径引入）：淡出尾巴里源值自身穿越基准值时，截断值在
        128/127 间跳，加分随之来回让位（至冬7.0 2c6e700e@187.98 128→174×3→127）；根治需淡出
        让位迟滞（有状态），不在本判据内做。

      **不能改看源 raw**：定向淡出里已走完的设备 data 恰为基准值、raw 仍是内容，看 raw
      会让淡出方把这些设备按在基准值上挡住后起 clip（实测至冬7.0@134.56 灯 255→0 压
      1.4s、@27.32 电机 128→0）；预到达设备 data=锚点、raw 途经基准值也会闪
      （水仙十字 6c64ce46@149.14）。
    """
    mask = np.asarray(active, dtype=bool)
    if not np.any(mask) or data is None or idle_target is None:
        return mask
    try:
        val = np.asarray(data, dtype=np.float32).reshape(-1)
        idle = np.asarray(idle_target, dtype=np.float32).reshape(-1)
        raw = val if content is None else np.asarray(content, dtype=np.float32).reshape(-1)
        n = min(mask.size, val.size, idle.size, raw.size)
        if n <= 0:
            return mask
        out = mask.copy()
        v, d = val[:n], idle[:n]
        static_default = np.floor(v) == d
        if ramp_in:
            crossing = (d > 0.0) & (np.abs(v - d) > 1e-6)
            if content is not None:
                crossing &= np.abs(raw[:n] - d) > 1.0
            static_default &= ~crossing
        out[:n] &= ~static_default
        return out
    except Exception:
        return mask
