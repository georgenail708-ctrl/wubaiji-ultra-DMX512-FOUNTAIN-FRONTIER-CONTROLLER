# -*- coding: utf-8 -*-
"""控件轨迹 Buffer（纯数据，无 Qt）—— 录制期控件级路径捕获。

用户语义：录制=把各页面拖动的控件（条带/摇杆/effect 参数滑条…）全过程路径
记录成**叠加的效果函数轨迹**；回放按 clip 时长周期循环，默认叠加，可 release。

设计要点：
  - 事件即到即记，**值不变不记**（天然抽稀）；t_rel 由 RecordEngine 给
    （与 DMX 帧同一时钟：timestamp = start_position + elapsed）。
  - 多轨同录：Buffer 是**会话全局**事件流；`stop()` 时按每个已录 clip 的
    时间窗 [t0, t1] **切片**写入各自 `clip.trajectories`（各 clip 独立拥有，
    单 clip 周期回放语义自洽 —— 设计 v2.1 §5.2）。
  - 值为 JSON 安全标量/tuple/dict（range=(mode,base,target)、forms=dict…），
    由各 control_kind 的回放 applier 解释（S2 注册表，非 if/else 蔓延）。
"""
from __future__ import annotations

from typing import Dict, List, Tuple


class ControlTrajectoryBuffer:
    """录制会话的控件轨迹事件流（一份；stop 时按 clip 切片）。"""

    def __init__(self) -> None:
        self._events: List[Tuple[float, str, object]] = []   # (t_rel, key, value)
        self._last_by_key: Dict[str, object] = {}

    # ── 录制期 ────────────────────────────────────────────────────────────
    def begin(self) -> None:
        """录制开始：清空会话流。"""
        self._events.clear()
        self._last_by_key.clear()

    def record(self, t_rel: float, key: str, value) -> None:
        """记录一个控件事件；同 key 值不变不记（抽稀）。异常输入静默丢弃。"""
        try:
            k = str(key or "")
            if not k:
                return
            if k in self._last_by_key and self._last_by_key[k] == value:
                return
            self._last_by_key[k] = value
            self._events.append((float(t_rel), k, value))
        except Exception:
            pass

    # ── 停止期 ────────────────────────────────────────────────────────────
    @staticmethod
    def slice_to_window(events: List[Tuple[float, str, object]],
                        t0: float, t1: float) -> Dict[str, List]:
        """把事件流切进 [t0, t1) → {control_key: [(t_rel_in_clip, value), ...]}。

        - 边界前最近的一次事件也带入（t 钳 0）：保证 clip 起点即有"当前值"，
          否则周期回放首帧会丢基线。
        - t0==t1（零长 clip）→ 空 dict。
        """
        out: Dict[str, List] = {}
        if t1 <= t0:
            return out
        baseline: Dict[str, object] = {}
        for (t, k, v) in events:
            if t < t0:
                baseline[k] = v          # 窗口前最近值（后被同 key 更近者覆盖）
                continue
            if t >= t1:
                break                    # 事件流按时间追加，可提前结束
            out.setdefault(k, []).append((round(t - t0, 4), v))
        for k, v in baseline.items():
            if k not in out:
                out[k] = [(0.0, v)]
            else:
                out[k].insert(0, (0.0, v))
        return out

    def dump_to_clips(self, windows: List[Tuple[object, float, float]]) -> None:
        """把会话流切片写到各 clip.trajectories。

        windows: [(clip, t0, t1), ...]（t 为录制时间轴上的绝对窗口）。
        """
        for clip, t0, t1 in (windows or []):
            try:
                clip.trajectories = self.slice_to_window(self._events, float(t0), float(t1))
            except Exception:
                continue

    # ── 视图/测试 ─────────────────────────────────────────────────────────
    def event_count(self) -> int:
        return len(self._events)

    def keys(self) -> List[str]:
        seen: List[str] = []
        for _t, k, _v in self._events:
            if k not in seen:
                seen.append(k)
        return seen
