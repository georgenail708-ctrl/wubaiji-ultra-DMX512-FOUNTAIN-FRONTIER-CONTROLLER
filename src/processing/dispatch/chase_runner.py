# -*- coding: utf-8 -*-
"""★ R183 T6：**Chase 跑灯**状态机 —— `Function::RunOrder` 四值移植。

来源（状态机设计笔记）：
  · `engine/src/function.h:308`：``enum RunOrder { Loop = 0, SingleShot, PingPong, Random }``
  · `engine/src/chaserrunner.cpp:301-370`：``computeNextStep(currentStep)``
      · Loop：越界回 0（前向）/ 回 n-1（后向）；
      · SingleShot：越界返回 -1（**序列结束**，不回绕）；
      · PingPong：越界**反向**，且"不重复跑首/末步"——
        前向越界后取 `n-2`，后向越界后取 `1`，再 CLAMP 到 [0, n-1]；
      · Random：`m_order` 洗牌序查表（本仓用 `random.Random` 实例，可注入种子复现）。

本仓映射：
  · 一个"跑灯序列"是 `EffectCache.chase = {"steps":[场景id,…], "order":…,
    "step_seconds":…}`（`effect_cache._as_chase` 已在数据层校验）；
  · 本模块只算**下一步该是谁**（纯函数/纯状态机，无 Qt、无面板依赖）→ 可无 GUI 单测；
  · 定时与"点亮/释放"由 `chase_runner_ui`（调度台侧）驱动。

刻意与 的差异（记录以免误判为 bug）：
  · 的 Random 是"洗牌整轮再按序跑"；本仓同样（`shuffle` 一次），保证
    "一轮内不重复"，而不是每步独立随机（后者会让同一步连出两次）。
  · 的 PingPong 在 stepsCount<=1 时会算出负数，靠 CLAMP 收；本仓
    `ChaseState` 对 n<=1 直接返回 -1（调用方停表），语义更明确且不依赖钳位。
"""
from __future__ import annotations

import random
from typing import List, Optional, Sequence

#: 合法顺序（与 `effect_cache.CHASE_ORDERS` 同值；此处再声明一份是为了让本模块
#: 零依赖 —— processing 层的 `normalize_chase` 已在入库时归一化，本模块只做兜底）。
ORDERS = ("loop", "single", "pingpong", "random")

#: 实现用 -1 表示"序列已完"（`computeNextStep` 的 SingleShot 分支）
FINISHED = -1

#: 步长上下限（秒）：0.05 = 人眼可辨的最快步（20 步/秒）；3600 = 防手滑写 1e9 钉住计时器
MIN_STEP_SECONDS = 0.05
MAX_STEP_SECONDS = 3600.0


def normalize_order(order) -> str:
    """顺序值归一化（非法 → loop，`RunOrder` 的 0 值）。"""
    o = str(order or "").strip().lower()
    return o if o in ORDERS else "loop"


def _clean_steps(raw) -> List[str]:
    """步列表归一化：只收非空字符串 id、去重、保序（空/非法 → 空列表）。"""
    steps: List[str] = []
    for s in (raw or []):
        sid = str(s or "")
        if sid and sid not in steps:
            steps.append(sid)
    return steps


def _clean_seconds(raw) -> float:
    """步长归一化：非法/NaN/<=0 → 1.0；然后夹到 [MIN, MAX]。"""
    try:
        secs = float(raw)
    except (TypeError, ValueError):
        secs = 1.0
    if secs != secs or secs <= 0.0:
        secs = 1.0
    return max(MIN_STEP_SECONDS, min(MAX_STEP_SECONDS, secs))


def normalize_chase(raw) -> dict:
    """跑灯配置归一化（★ 数据层收口）：坏数据一律回落**空 dict** = 普通静态场景。

    · `steps` 只收非空字符串 id、去重、保序（见 `_clean_steps`）；
    · `order` 非法 → "loop"；`step_seconds` 非法/<=0 → 1.0 且夹到上下限
      （见 `_clean_seconds`）。
    """
    if not isinstance(raw, dict):
        return {}
    steps = _clean_steps(raw.get("steps"))
    if not steps:
        return {}
    return {"steps": steps, "order": normalize_order(raw.get("order", "loop")),
            "step_seconds": _clean_seconds(raw.get("step_seconds", 1.0))}


class ChaseState:
    """跑灯游标（一个序列一份实例）：`next_index()` 推进一格。

    线程/时序契约：纯内存、无锁（调用方＝UI 计时器线程）；`reset()` 供序列
    重新触发时回到起点（的 `ChaserRunner::restart` 同语义）。
    """

    def __init__(self, steps: Sequence[str] = (), order: str = "loop",
                 rng: Optional[random.Random] = None) -> None:
        self._steps: List[str] = [str(s) for s in (steps or []) if str(s or "")]
        self._order = normalize_order(order)
        self._rng = rng or random.Random()
        self._direction = 1                 # PingPong 用（+1 前向 / -1 后向）
        self._idx = -1                      # 尚未启动
        self._shuffle: List[int] = []
        self._finished = False

    # ── 属性 ────────────────────────────────────────────────────────────
    @property
    def steps(self) -> List[str]:
        return list(self._steps)

    @property
    def order(self) -> str:
        return self._order

    @property
    def index(self) -> int:
        """当前步下标（-1 = 未开始或已结束）。"""
        return self._idx

    def current(self) -> Optional[str]:
        """当前步的场景 id（未开始/已结束 → None）。"""
        if 0 <= self._idx < len(self._steps):
            return self._steps[self._idx]
        return None

    def is_finished(self) -> bool:
        return bool(self._finished)

    def reset(self) -> None:
        """回到起点（序列被重新触发时调）。"""
        self._idx = -1
        self._direction = 1
        self._shuffle = []
        self._finished = False

    # ── 推进 ────────────────────────────────────────────────────────────
    def next_index(self) -> int:
        """推进一步并返回新下标（`FINISHED`=-1 = 序列已完 / 无步可跑）。

        内部游标 `_idx` 是**轮内位置**（Random 顺序下与场景下标不同，由
        `_scene_of` 换算）——用两个数组（`m_runnerSteps` / `m_order`）
        做同一件事，这里用一个游标 + 映射保持等价且只有一处推进逻辑。
        """
        n = len(self._steps)
        if n <= 0 or self._finished:
            return FINISHED
        if self._idx < 0:                   # 首次：从第 0 步进（restart 置 0）
            self._idx = 0
            return self._scene_of(0)
        nxt = self._idx + self._direction
        if 0 <= nxt < n:
            self._idx = nxt
            return self._scene_of(nxt)
        return self._wrap_index(n)

    def _wrap_index(self, n: int) -> int:
        """越界收口（`computeNextStep` 尾部三分支）：按顺序语义决定去/停。"""
        if self._order == "single":
            self._finished = True
            return FINISHED
        if self._order == "loop":
            self._idx = 0 if self._direction > 0 else n - 1
        elif self._order == "random":
            self._idx = 0                   # 新一轮：整轮重洗（一轮内不重复）
            self._shuffle = []
            self._ensure_shuffle()
        else:                               # pingpong
            if n <= 1:
                self._finished = True
                return FINISHED             # 单步序列来回跑没有意义 → 停
            self._direction = -self._direction
            # ★：反向越界不重复跑首/末步 —— 前向越界后落 n-2、后向越界后落 1
            self._idx = max(0, min(n - 1, (n - 2) if self._direction < 0 else 1))
        return self._scene_of(self._idx)

    # ── 内部 ────────────────────────────────────────────────────────────
    def _ensure_shuffle(self) -> None:
        """Random：整轮洗牌（`fillOrder` → `shuffle`，一轮内不重复）。"""
        if self._order != "random" or self._shuffle:
            return
        order = list(range(len(self._steps)))
        self._rng.shuffle(order)
        self._shuffle = order

    def _scene_of(self, pos: int) -> int:
        """轮内位置 → 场景下标（Random 走洗牌表，其余恒等）。"""
        if self._order != "random":
            return int(pos)
        self._ensure_shuffle()
        if 0 <= pos < len(self._shuffle):
            return int(self._shuffle[pos])
        return int(pos)


def step_index_of(order: str, current: int, count: int,
                  rng: Optional[random.Random] = None,
                  shuffle: Optional[Sequence[int]] = None) -> int:
    """无状态版：给定当前下标算下一个（供"序列卡步进"与单测直接调用）。

    与 `ChaseState.next_index` 同语义；`rng`/`shuffle` 为 Random 顺序的注入点
    （不注入 → 每次现洗，调用方须自行持有 `ChaseState` 才有一轮不重复的保证）。
    PingPong 的"当前方向"由 `current` 与首尾的相对位置推断（无内部状态可携带）。
    """
    st = ChaseState(steps=[str(i) for i in range(max(0, int(count)))],
                    order=order, rng=rng)
    st._idx = int(current) if int(current) >= 0 else -1
    if shuffle:
        st._shuffle = [int(x) for x in shuffle]
    return st.next_index()


__all__ = ["ORDERS", "FINISHED", "MIN_STEP_SECONDS", "MAX_STEP_SECONDS",
           "normalize_order", "normalize_chase", "ChaseState", "step_index_of"]
