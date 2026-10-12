# -*- coding: utf-8 -*-
"""灯跟踪（LT）状态的播放期作用域（R173）。

`_light_tracking_state/_time` 的键是 ``lt:<域>:<设备>[:level]:<源>``——**不含 clip**，
时间基却是各 clip 自己的局部时间。一段 LT clip 播完后这些条目仍留在字典里，下一段
LT clip（可能隔了好几秒）起播时会读到：

  * 泵记忆 ``lt:..:pump``：本 clip 不管该泵（非 active、帧值 0）时回退读它 → 早已停掉的
    泵"还开着" → 灯按超阈值淡入到最大亮度 = **泵实际输出 0 却亮灯**；
  * 包络 ``..:level:..``：起点沿用旧 clip 末帧亮度，跨 clip 时间基的时间差被钳成 1ms →
    拖着一段慢慢衰减的残光。

实测 TTM 7aeb3aeb@148.94~165.2：u1c276 泵恒 0，HJC 二维 35 却 170/85/255 亮 16s
（上一段 LT clip 8be0f0f1 在 141.84 结束时末帧泵在开）。

规则：某一播放帧**没有任何开启灯跟踪的 clip 在播**（LT 断档）→ 清空状态与时间（颜色
缓存保留：它是采样得到的设备颜色，与时间无关）。断档后第一段 LT clip 从全新状态起步，
与整场第一段 LT clip 同口径。有 LT clip 连续在播的帧一律不动（交叉淡化等既有语义不变）。

CE 侧（qascade_ce 挂载时）引擎上另有 C 状态槽对象 `_lt_c_state`（实时 hook 与 K2 离线束
共用同一对象）：断档时一并**就地**清槽（`clear_state`），不换对象 → 两条 C 路径不分叉。
K2 离线逐帧也经本函数（qascade_ce `_hook_timeline_range`）。
"""
from __future__ import annotations


def lt_clips_with_gap_reset(engine, clips: list) -> list:
    """返回 clips 本身（`_lt_active_clips` 快照）；本帧 LT 断档时顺带清空 LT 状态。"""
    reset_light_tracking_on_gap(engine, clips)
    return clips


def reset_light_tracking_on_gap(engine, clips) -> bool:
    """clips 中无开启灯跟踪者 → 清空 `_light_tracking_state/_time`（及 C 槽）；返回是否清过。"""
    state = engine._light_tracking_state
    stime = engine._light_tracking_time
    if not (state or stime):
        return False
    for clip in clips:
        if bool(getattr(clip, "light_tracking_enabled", False)):
            return False
    state.clear()
    stime.clear()
    clear_c_state = getattr(getattr(engine, "_lt_c_state", None), "clear_state", None)
    if clear_c_state is not None:
        clear_c_state()
    return True
