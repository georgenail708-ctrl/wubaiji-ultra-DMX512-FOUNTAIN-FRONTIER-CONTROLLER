"""HSV↔RGB 颜色数学统一薄封装（R183 3.3 计算层残留第 4 项：四处 `_hsv_to_rgb` 分叉归一）。

四处原式（2026-09-30 grep 实锤，行为互不一致——域/舍入/h 归一各不同）:

* ``src/ui/effect_engine.py:4818``                 int 截断；s==0 短路 int(v*255)；不归一 h
* ``src/ui/effect_presets_curated.py:58``          h%1.0 归一后 int 截断（被 r8_color 复用）
* ``src/ui/effect_presets_v26.py:1378``（闭包内）  浮点 0..1 域（调用方自行 ×255 后 _clamp）
* ``src/engine/auto_arrange/voice_clip_renderer.py:503``  h%1.0 + s/v 钳位 + round（半偶舍入）

统一口径 = 标准库 ``colorsys.hsv_to_rgb``：它是四处数学的公共内核（选择表、p/q/t
公式逐项同构），也是 CE C 侧 ``_ce_hsv_to_rgb``（render_lab/csrc/ce_engine.c）逐位
复刻的对象。本模块只做与各处原式**逐位相同**的域变换（截断/归一/钳位/round 各自
保留），因此四处 Python 调用点的输出字节不变、CE 对拍契约不动。

对拍证据（本批探针 6530 采样 + 20 万随机含负 h/越界 s,v：4 组 0 差异；
金标断言固化在 ``tests/test_r183_perf.py``）。本模块为纯函数、无 Qt、无第三方依赖。
"""
from __future__ import annotations

import colorsys
from typing import Tuple


def hsv_to_rgb_float(h: float, s: float, v: float) -> Tuple[float, float, float]:
    """浮点 0..1 三通道（v26 族口径；``s == 0`` → (v, v, v)）。"""
    return colorsys.hsv_to_rgb(h, s, v)


def hsv_to_rgb_u8(h: float, s: float, v: float) -> Tuple[int, int, int]:
    """0..255 int，**不归一 h**、int 截断（effect_engine 口径）。"""
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def hsv_to_rgb_u8_norm(h: float, s: float, v: float) -> Tuple[int, int, int]:
    """0..255 int，先 h%1.0 再 int 截断（effect_presets_curated 口径）。"""
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def hsv_to_rgb_u8_round_clamped(h: float, s: float, v: float) -> Tuple[int, int, int]:
    """0..255 int，h%1.0 + s/v 钳到 [0,1] + round（voice_clip_renderer 口径）。"""
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)),
                                  max(0.0, min(1.0, v)))
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))
