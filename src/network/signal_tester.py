"""
Qascade V1.0 — Signal Tester（多模式动态版）
★ v2: 写入独立的 tester 层，避免与 programmer 层互相覆盖

支持的模式：
  静态（单次发送）:
    full / zero / ramp / checkerboard / single

  动态（定时 tick）:
    chase / pingpong / fade / rainbow / strobe /
    groupchase / zipper / raindrop / pulse / staircase
"""

from __future__ import annotations

import logging
import math
import random
from typing import Optional

logger = logging.getLogger(__name__)

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal, QTimer

# 静态模式集合
_STATIC_MODES = {"full", "zero", "ramp", "checkerboard", "single"}

# 动态模式集合
_DYNAMIC_MODES = {
    "chase", "pingpong", "fade", "rainbow", "strobe",
    "groupchase", "zipper", "raindrop", "pulse", "staircase",
}


class SignalTester(QObject):
    test_status = pyqtSignal(str)

    def __init__(self, network_manager, parent=None):
        super().__init__(parent)
        self._nm = network_manager
        self._timer: Optional[QTimer] = None
        self._mode = "full"
        self._universe = 0
        self._step = 0          # 整数帧计数（chase / pingpong / zipper 等用）
        self._t = 0.0           # 浮点时间累计（秒）
        self._running = False
        self._params: dict = {}

        # raindrop / strobe 内部状态
        self._drop_state: list = []        # [[ch, val, remaining_frames], ...]
        self._strobe_timers: list[int] = [0] * 512   # 单位：tick 数
        # 确定性随机：按帧重播种，使同一测试会话可复现（替代全局 random）
        self._rng = random.Random(0)

    # ─────────────────────────────────────────────────────────────────────────
    # 公共接口
    # ─────────────────────────────────────────────────────────────────────────

    _ZERO_MODE = "zero"

    def _write_layer(self, data: np.ndarray) -> None:
        """把图案帧写进 tester 层（★ R141）。

        `active` 显式给成「图案真的写了值的通道」（`data > 0`）。修前三个入口都
        不传 active → `NetworkManager.update_layer` 默认全 512 True，而 tester 在
        `BASE_LAYERS` 里排在 programmer 之后（LTP 靠后覆盖）→「图案之外的 0」成了
        该宇宙最高优先级的显式 0，Programmer/效果/时间线被整片压黑（探针实测：
        programmer ch1=200 → 0）。
        例外：`zero`（「全灭 Blackout」）是**刻意的整帧接管** → 保持全 512 活跃，
        否则"全灭"清不掉任何东西。
        """
        active = None if self._mode == self._ZERO_MODE else (data > 0)
        self._nm.update_layer("tester", self._universe, data, active=active)

    def start_test(self, mode: str, universe: int, params: Optional[dict] = None):
        """
        启动测试。

        params 可选键（所有模式通用，按需读取）：
          level       int   0-255   整体亮度
          speed       float 0.1-20  速度倍率
          width       int   1-32    跑灯点宽（chase / pingpong）
          ch_count    int   1-512   作用通道数
          lo / hi     int   0-255   呼吸低/高值（fade）
          freq        float 0.5-16  频率（rainbow / pulse）
          group_width int   1-64    组宽（groupchase）
          steps       int   2-32    阶数（staircase）
          density     float 0-1     密度（strobe / raindrop）
          duration    float 0.01-1  闪烁持续帧数比（strobe）
          channel     int   0-511   单通道编号（single）
        """
        self._params = params or {}
        self.stop_test()
        self._mode     = mode.lower()
        self._universe = universe
        self._params   = params or {}
        self._step     = 0
        self._t        = 0.0
        self._running  = True
        self._drop_state    = []
        self._strobe_timers = [0] * 512

        if self._mode in _STATIC_MODES:
            self._send_static()
        elif self._mode in _DYNAMIC_MODES:
            interval = max(16, int(1000 / (30 * self._params.get("speed", 1.0))))
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._send_step)
            self._timer.start(33)   # 固定 ~30fps；速度通过 step 步长控制
        else:
            self.test_status.emit(f"未知模式: {mode}")
            return

        self.test_status.emit(f"测试中 [{mode}] U{universe}")

    def start_channel_test(self, universe: int, channels: list, value: int = 255):
        """指定通道写指定值（通道测试）。

        ★ R141：显式传 `active` = **用户点名的那些通道**。修前不传 active →
        `NetworkManager.update_layer` 默认全 512 True → tester 在 BASE_LAYERS 里
        排在 programmer 之后（LTP 靠后覆盖）→「其余通道的 0」成了该宇宙最高
        优先级的显式 0，Programmer/效果/时间线全部不出光（探针实测：programmer
        ch1=200 被压成 0）。点名通道本身按用户要求写值（含 value=0 的定向清零），
        未点名的通道完全不参与接管。
        """
        self.stop_test()
        self._universe = universe
        data = np.zeros(512, dtype=np.uint8)
        touched = np.zeros(512, dtype=bool)
        for ch in channels:
            if 1 <= ch <= 512:
                data[ch - 1] = value
                touched[ch - 1] = True
        # ★ v2: 写入 tester 层
        self._nm.update_layer("tester", universe, data, active=touched)
        self.test_status.emit(
            f"通道测试 U{universe} ch={len(channels)}个 val={value}")

    def stop_test(self):
        self._running = False
        if self._timer:
            self._timer.stop()
            self._timer = None
        try:
            # ★ v2: 清除 tester 层
            self._nm.clear_layer("tester", self._universe)
        except Exception:
            logger.debug("signal_tester: failed to clear tester layer on stop")
        self.test_status.emit("测试已停止")

    # ─────────────────────────────────────────────────────────────────────────
    # 静态模式
    # ─────────────────────────────────────────────────────────────────────────

    def _send_static(self):
        p   = self._params
        lv  = p.get("level", 255)
        data = np.zeros(512, dtype=np.uint8)

        if self._mode == "full":
            data[:] = lv

        elif self._mode == "zero":
            pass   # 全零

        elif self._mode == "ramp":
            data = np.linspace(0, lv, 512, dtype=np.uint8)

        elif self._mode == "checkerboard":
            data[0::2] = lv

        elif self._mode == "single":
            # ★ R178 修：NumSlider.float_value() 使 channel 为 float（5.0）→
            #   data[5.0] IndexError（用户实报）。int() 钳位到 [0,511]。
            ch = int(p.get("channel", 0) or 0)
            ch = max(0, min(511, ch))
            if 0 <= ch < 512:
                data[ch] = lv

        self._write_layer(data)

    # ─────────────────────────────────────────────────────────────────────────
    # 动态模式 tick（每 33ms 调用一次）
    # ─────────────────────────────────────────────────────────────────────────

    def _send_step(self):
        if not self._running:
            return

        # 按帧确定性重播种：给定步序 + 确定性状态演化 → 整段测试可复现
        self._rng.seed((self._step * 2654435761) & 0xFFFFFFFF)

        p   = self._params
        lv  = p.get("level", 255)
        spd = max(0.1, p.get("speed", 1.0))
        cnt = max(1, min(512, p.get("ch_count", 512)))
        data = np.zeros(512, dtype=np.uint8)

        # 每帧时间增量（秒），speed 倍率作用于时间轴
        dt  = 0.033 * spd
        self._t += dt
        t = self._t

        # ── 跑灯 Chase ────────────────────────────────────────────────────────
        if self._mode == "chase":
            wid = max(1, p.get("width", 1))
            pos = int(t * 30) % cnt
            for k in range(wid):
                data[(pos + k) % cnt] = lv

        # ── 乒乓 Ping-Pong ────────────────────────────────────────────────────
        elif self._mode == "pingpong":
            wid   = max(1, p.get("width", 2))
            cycle = cnt * 2 - 2
            raw   = int(t * 30) % max(1, cycle)
            pos   = raw if raw < cnt else cycle - raw
            for k in range(wid):
                data[min(cnt - 1, pos + k)] = lv

        # ── 呼吸 Fade ─────────────────────────────────────────────────────────
        elif self._mode == "fade":
            lo    = p.get("lo", 0)
            hi    = p.get("hi", 255)
            phase = math.sin(t * math.pi) * 0.5 + 0.5
            val   = int(lo + (hi - lo) * phase)
            data[:] = val

        # ── 彩虹 Rainbow ──────────────────────────────────────────────────────
        elif self._mode == "rainbow":
            freq   = p.get("freq", 4.0)
            offset = t * 2 * math.pi
            for i in range(cnt):
                v = math.sin(offset + i / cnt * freq * math.pi)
                data[i] = int((v * 0.5 + 0.5) * lv)

        # ── 随机闪烁 Strobe ───────────────────────────────────────────────────
        elif self._mode == "strobe":
            density = p.get("density", 0.05)
            dur_ticks = max(1, int(p.get("duration", 0.05) / 0.033))
            for i in range(512):
                if self._strobe_timers[i] > 0:
                    self._strobe_timers[i] -= 1
                    data[i] = lv
                elif self._rng.random() < density:
                    self._strobe_timers[i] = dur_ticks
                    data[i] = lv

        # ── 分组跑灯 Group Chase ──────────────────────────────────────────────
        elif self._mode == "groupchase":
            gw    = max(1, p.get("group_width", 8))
            n_grp = math.ceil(cnt / gw)
            grp   = int(t * 8) % max(1, n_grp)
            start = grp * gw
            for k in range(gw):
                idx = start + k
                if idx < 512:
                    data[idx] = lv

        # ── 拉链 Zipper ───────────────────────────────────────────────────────
        elif self._mode == "zipper":
            phase = int(t * 4) % 2
            data[phase::2] = lv

        # ── 雨滴 Raindrop ─────────────────────────────────────────────────────
        elif self._mode == "raindrop":
            density = p.get("density", 0.3)
            n_new   = max(1, int(density * 10 * 0.033))
            for _ in range(n_new):
                ch  = self._rng.randint(0, 511)
                val = self._rng.randint(int(lv * 0.3), max(1, lv))
                dur = self._rng.randint(1, 12)   # 单位：tick
                self._drop_state.append([ch, val, dur])
            alive = []
            for drop in self._drop_state:
                ch, val, rem = drop
                if rem > 0:
                    data[ch] = max(int(data[ch]), val)
                    alive.append([ch, val, rem - 1])
            self._drop_state = alive

        # ── 脉冲波 Pulse ──────────────────────────────────────────────────────
        elif self._mode == "pulse":
            freq = p.get("freq", 2.0)
            for i in range(cnt):
                phase = t * 2 * math.pi * freq - i * 0.05
                data[i] = int(max(0.0, math.sin(phase)) * lv)

        # ── 楼梯 Staircase ────────────────────────────────────────────────────
        elif self._mode == "staircase":
            steps  = max(2, p.get("steps", 8))
            seg    = max(1, cnt // steps)
            offset = int(t * steps) % steps
            for i in range(cnt):
                seg_idx = (i // seg + offset) % steps
                data[i] = int(lv * seg_idx / (steps - 1))

        self._write_layer(data)
        self._step += 1

    # ─────────────────────────────────────────────────────────────────────────
    # 模式名映射（供 UI 层查询）
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def mode_key(cls_name: str) -> str:
        """将 PatternXxx 类名转换为后端 mode 字符串"""
        _map = {
            "PatternFull":         "full",
            "PatternZero":         "zero",
            "PatternRamp":         "ramp",
            "PatternCheckerboard": "checkerboard",
            "PatternSingleChannel":"single",
            "PatternChase":        "chase",
            "PatternPingPong":     "pingpong",
            "PatternFade":         "fade",
            "PatternRainbow":      "rainbow",
            "PatternStrobe":       "strobe",
            "PatternGroupChase":   "groupchase",
            "PatternZipper":       "zipper",
            "PatternRaindrop":     "raindrop",
            "PatternPulse":        "pulse",
            "PatternStaircase":    "staircase",
        }
        return _map.get(cls_name, "full")