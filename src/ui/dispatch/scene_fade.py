# -*- coding: utf-8 -*-
"""场景**淡入淡出**引擎（把"自动淡入淡出时间"落到 NetworkManager 的每层增益上）。

用户 2026-09-14 定案：
  "可以设置自动的淡入淡出时间，这样即使是 flash 模式也能轻柔。"
  "淡入淡出时间这个跟哪种模式无关，flash 还是 latch 都可以生效。"
  "这个是独立的新增功能也就是说即使不绑定按键，我鼠标点击一样生效。"

为什么必须**按层增益**而不是"写渐变值"：动效面板（effect/matrix/programmer）
每帧会**整帧重写自己的层**，任何一次性的写值都会被下一帧覆盖。只有让
NetworkManager 在这一层被**混音时**缩放（`set_layer_gain`），正在跑的效果才会平滑
淡入/淡出 —— 也因此它与触发模式无关（对"正在输出的那一层"生效）。

★★ 关键设计（深查后重写；原"单场景 + 单回调 + 单层增益"实现有四个真 bug）：
  1. **按场景记账**，每层取"所有场景在该层的**最大值**"（`_entries`）。
     · H3：多个场景可能写同一层（两个 effect 场景都写 `effect`）。若共用一个层增益，
       A 场景淡出会把 B 场景一起压暗、甚至永久静音。取 max：只要还有一个场景全亮，
       这一层就不该被压暗。
     · H2：一个键可以映射多个场景 —— 每个场景各自一条斜坡，谁都不覆盖谁。
  2. **回调按场景绑定**（`_callbacks`）。原实现只有一个 `_on_done`：连点/重触发时旧
     回调会在新淡入结束时误触发 → 刚点亮的场景被立刻释放（H1）。
  3. **诚实的淡入起点**。原实现"增益已在 1.0 就从 1.0 起步"——新建场景首次触发时
     增益本来就是 1.0，于是淡入**恒为空操作**（用户设了淡化却毫无感觉）。现在
     `activate(secs>0)` 明确从 **0** 起坡：触发瞬间先压暗再平滑升起，这才是淡入。
  4. **完成后归还增益**（`_return_stale`）：该层已无任何场景记账时写回 1.0（移除条目），
     否则淡出留下的 0.0 会让该层永久静音（用户的效果从此不出光）。
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Dict, List, Optional

from PyQt6.QtCore import QObject, Qt, QTimer

log = logging.getLogger(__name__)

#: 斜坡刷新率（40fps，与 effect 切片淡化的既有选择一致）
_TICK_MS = 25


def smoothstep(raw: float) -> float:
    """0..1 → 0..1 的平滑曲线（缓入缓出；与 effect 切片淡化同一公式）。"""
    t = max(0.0, min(1.0, float(raw)))
    return t * t * (3.0 - 2.0 * t)


class FadeRunner(QObject):
    """推进若干**场景各自**的层增益斜坡；每层取所有场景的**最大**增益。

    对外四动作：
      · `activate(key, layers, secs)`：场景点亮。`secs>0` → 从 0 平滑升到 1；
        `secs<=0` → 立即全亮（仍**记账**，好让别的场景淡出时不误伤它）。
      · `deactivate(key, layers, secs, on_done)`：场景熄灭。`secs>0` → 平滑降到 0，
        降到 0 后回调（真正释放）；`secs<=0` → 立即回调，不留斜坡。
      · `drop(key)`：无条件丢弃该场景的记账与回调（外部已经自己释放了）。
      · `reset()`：全部归还（切工程 / 面板销毁 / 异常兜底）。
    """

    def __init__(self, nm_provider, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._nm = nm_provider
        #: {场景键: {层名: (起值, 终值, 起始时刻, 时长)}}；时长 0 = 静止在该终值
        self._entries: Dict[str, Dict[str, tuple]] = {}
        #: {场景键: 释放回调}（仅 deactivate 带）
        self._callbacks: Dict[str, Callable] = {}
        #: {场景键: 0..1 增益上限}（R178 场景推杆；缺省 = 1.0 不缩放）
        self._scales: Dict[str, float] = {}
        #: 当前已写入 <1.0 增益的层（归还时要知道清哪些）
        self._applied: set = set()
        self._timer: Optional[QTimer] = None

    # ── 对外 ────────────────────────────────────────────────────────────
    def activate(self, key, layers, seconds: float = 0.0) -> None:
        """场景点亮：`seconds>0` 从 0 淡入，否则立即全亮（但仍记账，见类说明）。"""
        skey = str(key or "")
        names = self._names(layers)
        if not skey or not names:
            return
        secs = max(0.0, float(seconds or 0.0))
        now = time.monotonic()
        entry = {}
        for n in names:
            if secs > 0.0:
                entry[n] = (0.0, 1.0, now, secs)      # 从 0 起坡（诚实淡入）
            else:
                entry[n] = (1.0, 1.0, now, 0.0)       # 静止全亮
        self._entries[skey] = entry
        self._callbacks.pop(skey, None)               # ★ H1：清掉旧的释放回调
        if secs > 0.0:
            self._ensure_timer()
            self._tick()
        else:
            self._write_all()
            self._stop_if_idle()

    def deactivate(self, key, layers, seconds: float = 0.0, on_done=None) -> bool:
        """场景熄灭。`seconds>0` 平滑降到 0 后回调；否则立即回调并清除记账。

        返回是否真的启动了斜坡（调用方据此决定要不要走"等淡完再释放"的分支）。
        """
        skey = str(key or "")
        names = self._names(layers) or sorted(self._entries.get(skey, {}))
        if not skey:
            self._fire(on_done)
            return False
        secs = max(0.0, float(seconds or 0.0))
        if secs <= 0.0 or not names:
            self._entries.pop(skey, None)
            self._callbacks.pop(skey, None)
            self._write_all()
            self._fire(on_done)
            self._stop_if_idle()
            return False
        now = time.monotonic()
        cur = {n: self._layer_gain_now(n) for n in names}
        self._entries[skey] = {n: (cur[n], 0.0, now, secs) for n in names}
        if callable(on_done):
            self._callbacks[skey] = on_done
        else:
            self._callbacks.pop(skey, None)
        self._ensure_timer()
        self._tick()          # 立即写一帧，避免首帧跳变
        return True

    def drop(self, key) -> None:
        """丢弃该场景的记账（含未触发的回调）并立即按剩余场景重算层增益。"""
        skey = str(key or "")
        self._entries.pop(skey, None)
        self._callbacks.pop(skey, None)
        self._scales.pop(skey, None)      # ★ R178：场景走人 → 推杆缩放一并归还
        self._write_all()
        self._stop_if_idle()

    # ── 场景推杆（R178 革新）────────────────────────────────────────────
    def set_scene_gain_scale(self, key, scale: float) -> None:
        """设置某场景的**增益上限**（0..1；1.0 = 不缩放并摘账）。

        `_envelope` 合成时对该场景的各层包络**乘上 scale** —— 于是正在播的场景
        可被一把"场景推杆"实时压暗/推亮，且不影响同层其它场景（per-scene 记账，
        每层仍取各场景包络的 max）。场景释放（drop/reset）即归还。
        """
        skey = str(key or "")
        if not skey:
            return
        try:
            val = max(0.0, min(1.0, float(scale)))
        except (TypeError, ValueError):
            return
        if val >= 1.0:
            self._scales.pop(skey, None)
        else:
            self._scales[skey] = val
        if skey in self._entries:
            self._write_all()             # 静止条目无定时器，缩放变更须即时落层
            self._stop_if_idle()

    def scene_gain_scale(self, key) -> float:
        """该场景当前的增益上限（未设 = 1.0）。"""
        return float(self._scales.get(str(key or ""), 1.0))

    def reset(self) -> None:
        """全部归还（层增益移除 → 回 1.0）。切工程 / 面板销毁 / 异常兜底用。

        ★★ R136 用户实报（"满值复活"）：`_callbacks` 里挂的是**淡出结束才做的
          真释放**（`scene_release._finish_scene_release`：停各面板输出 + 清层 +
          熄高亮）。原实现 `clear()` 只清不触发 → 用户给场景设了淡化、连点两下
          关闭它（开始淡出），在淡出**没走完**时存新场景（`_release_after_save`
          → `_reset_scene_fades` → 本函数）→ 该回调被**丢弃** → 面板输出从未停、
          高亮与入口都已消失 → 场景满值复活且再也关不掉（实测探针：
          `probe_r136/probe_1_fade_reset.py`，`_release_nodes` 调用 0 次）。

          语义纠正：**"全局清台"本来就该包含"把每个还亮着的场景正式关掉"** ——
          故先把遗留回调全部触发，再归还层增益。（先清记账再触发：回调里会
          `recall` 幸存场景 / 再 `drop`，重入本函数看到的已是空记账，不会重复。）
        """
        pending = list(self._callbacks.values())
        self._callbacks.clear()
        self._entries.clear()
        self._scales.clear()            # ★ R178：推杆缩放随全局清台一并归还
        for cb in pending:
            self._fire(cb)
        self._return(self._applied)
        self._applied = set()
        if self._timer is not None:
            self._timer.stop()

    def is_active(self) -> bool:
        return bool(self._entries)

    # ── 内部 ────────────────────────────────────────────────────────────
    @staticmethod
    def _names(layers) -> List[str]:
        out: List[str] = []
        for x in (layers or ()):
            s = str(x or "")
            if s and s not in out:
                out.append(s)
        return out

    def _layer_gain_now(self, layer: str) -> float:
        """该层**当前生效**增益（新斜坡的起点，保证不跳变）。"""
        nm = self._nm() if callable(self._nm) else None
        try:
            return float(nm.layer_gain(layer)) if nm is not None else 1.0
        except Exception:
            return 1.0

    def _ensure_timer(self) -> None:
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(_TICK_MS)
            self._timer.setTimerType(Qt.TimerType.PreciseTimer)
            self._timer.timeout.connect(self._tick)
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self) -> None:
        now = time.monotonic()
        per_layer = self._envelope(now)
        self._write_layers(per_layer)
        # 到点的场景：触发它自己的释放回调；无 scale 记账的才清记账。
        # ★ R178 审计 H2 修复：带 scale 的淡入场景到点后**改写为静止条目**
        #   (1.0,1.0,now,0.0)——pop 会让 _return_stale 把层增益归还 1.0，
        #   推杆 scale 失效跳回 100%，且同层其他场景淡出时它被连带压暗。
        #   无 scale 的场景维持原语义（pop + 回调，R136 口径）。
        finished = self._finished(now)
        for skey in finished:
            entry = self._entries.get(skey, {})
            has_scale = skey in self._scales and self._scales.get(skey, 1.0) != 1.0
            # ★ 审计 A/P1-4：只对**升坡**（淡入到 1.0）保持静止记账；淡出条目
            #   （end<1.0）若也改写成 (1.0,1.0) 会永不 pop → 幽灵增益钉死。
            all_rising = all(e >= 1.0 for (_s, e, _t, _d) in entry.values())
            if has_scale and all_rising:
                now2 = now
                self._entries[skey] = {
                    layer: (1.0, 1.0, now2, 0.0) for layer in entry}
            else:
                self._entries.pop(skey, None)
            self._fire(self._callbacks.pop(skey, None))
        self._write_all()          # 完成后按剩余场景重算（并归还空层）
        self._stop_if_idle()

    def _envelope(self, now: float) -> Dict[str, float]:
        """当前每层应有的增益 = 各场景在该层包络（×各自推杆 scale）的**最大值**。

        ★ R178：per-scene `scale`（场景推杆）乘在**该场景的包络**上、之后才取
          max —— 同层其它场景不被这把推杆压暗（与 H3 同一条设计纪律）。
        """
        per_layer: Dict[str, float] = {}
        for skey, entry in self._entries.items():
            scale = self._scales.get(skey, 1.0)
            for layer, (start, end, t0, dur) in entry.items():
                if dur and dur > 0.0:
                    raw = (now - float(t0)) / float(dur)
                    val = (start + (end - start) * smoothstep(raw)) if raw < 1.0 else end
                else:
                    val = end
                val = val * scale
                if val > per_layer.get(layer, -1.0):
                    per_layer[layer] = val
        return per_layer

    def _finished(self, now: float) -> List[str]:
        """所有条目都已到点（且不是"静止"条目）的场景键。"""
        out: List[str] = []
        for skey, entry in self._entries.items():
            if not entry:
                continue
            moving = [d for (_s, _e, _t, d) in entry.values() if d and d > 0.0]
            if not moving:
                continue                      # 静止条目：由 drop/deactivate 收尾
            if all((now - float(t0)) / float(dur) >= 1.0
                   for (_s, _e, t0, dur) in entry.values() if dur and dur > 0.0):
                out.append(skey)
        return out

    def _write_all(self) -> None:
        self._write_layers(self._envelope(time.monotonic()))
        self._return_stale()

    def _write_layers(self, per_layer: Dict[str, float]) -> None:
        nm = self._nm() if callable(self._nm) else None
        if nm is None or not hasattr(nm, "set_layer_gain"):
            return
        for name, gain in per_layer.items():
            try:
                nm.set_layer_gain(str(name), float(gain))
                if float(gain) < 1.0:
                    self._applied.add(str(name))
            except Exception:
                # R181 P2-3：层增益写失败 → 本层本次淡化不生效（跳到下一层）
                log.debug("写入层增益失败 layer=%s gain=%.3f", name, gain, exc_info=True)
                continue

    def _return_stale(self) -> None:
        """把"已不再被任何场景记账"的层归还为 1.0（移除条目）。"""
        live = {lay for entry in self._entries.values() for lay in entry}
        stale = self._applied - live
        self._return(stale)
        self._applied = {lay for lay in self._applied if lay in live}

    def _return(self, layers) -> None:
        nm = self._nm() if callable(self._nm) else None
        if nm is None or not hasattr(nm, "set_layer_gain"):
            return
        for lay in list(layers):
            try:
                nm.set_layer_gain(str(lay), 1.0)
            except Exception:
                # R181 P2-3：归还增益失败 → 该层可能被永久压暗（最严重的"灯死"型残留）
                log.debug("归还层增益 1.0 失败 layer=%s（该层可能被永久压暗）", lay,
                          exc_info=True)
                continue

    def _stop_if_idle(self) -> None:
        if self._timer is None:
            return
        moving = any(d and d > 0.0
                     for entry in self._entries.values()
                     for (_s, _e, _t, d) in entry.values())
        if not moving:
            self._timer.stop()

    @staticmethod
    def _fire(cb) -> None:
        if cb is None:
            return
        try:
            cb()
        except Exception:
            # R181 P2-3：释放回调（`_finish_scene_release`）抛错曾静默 —— 场景
            # "关不掉"却无任何线索；改 debug 级含栈（不影响其它场景推进）。
            log.debug("淡入淡出完成回调失败（该场景收尾可能未完成）", exc_info=True)


__all__ = ["FadeRunner", "smoothstep"]
