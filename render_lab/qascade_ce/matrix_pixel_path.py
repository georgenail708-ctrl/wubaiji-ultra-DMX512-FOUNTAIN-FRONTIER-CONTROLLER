# -*- coding: utf-8 -*-
"""矩阵像素引擎（matrix slideshow pixel engine）CE 计算层。

背景（2026-09-18 实测归因, `render_lab/bench/probe_matrix_pixel_profile.py`
+ `_ui_validation/probe_v40_segments.py`）:

  1356 台设备, 面板 40ms 主循环 `_tick()` → `_pixel_engine.tick_values/tick`:

    effect_ripple（内置数组族, numpy 向量化）  0.061 ms/帧 —— **不是热点**
    matrix_v40_asym_motion（v40 图案族）       6.46  ms/帧
    matrix_v40_audio_bar（v40 音频条族）       7.70  ms/帧
    matrix_v40_particle_physics（v40 粒子族）  27.79 ms/帧 ← 单帧吃掉 70% 预算

  两族的内部归因（1356 台）:
    `_compute_pattern`  5.69ms = coords 49.3% + metric 18.9% + style/dict 31.9%
    `_compute_particle` 29.87ms = 逐设备×逐粒子 exp 核 82.9%
  共同点 = **逐设备 Python 循环 + 标量超越函数**（每帧 1356 次 atan2/hypot,
  粒子族更叠加 1356×count 次 exp）——正是 C 化收益最大的形态。

  ★ 与内置族的边界: `_compute_weights_array_fast` 那批（effect_ripple/
  audio_fft/…）早已 numpy 向量化（1356 台 0.032ms/帧）, C 化收益 < 5% 且
  需复刻 numpy 的批处理浮点顺序 → **明确不 C 化**（数据见上）。

接入点（用户指令「新功能的计算层应该及时更新到 CE 里面并通过它完成计算
调用, 保证全命中」）: `matrix_pixel_engine_ext_v40._compute_pattern` /
`._compute_particle` —— `_EXT_COMPUTE_MAP` 里的 lambda 在**调用时**按
模块全局名查找, 故替换模块属性即全量生效（v40 的 964/1254 个模式:
pattern 784 + particle 180; 另 `patch_matrix_pixel_engine` 生成的
`_compute_<mode_id>` 类属性走同一条链）。

## 位级等价（与既有 CE 内核同标准）

* C 内核数值全程 f64 + ucrtbase 动态解析（sin/cos/atan2/exp/floor/sqrt/pow）
  ——实证见 `_ui_validation/probe_math_ucrt_equiv.py`。
* `math.hypot` 是唯一例外（CPython 自研 vector_norm ≠ ucrtbase hypot, 实测
  67% 采样差 1ulp）→ C 侧用 Dekker double-double 复刻精确舍入语义, 实证见
  `_ui_validation/prevalidate_c_math.py`（20 万×8 域 0 失配）。
* 整数哈希 `_hash01` 的 Python 任意精度 → C int64 回绕（同一探针 40 万样本
  0 失配）。
* **参数一律由 Python 侧 `_params()` 算好传入**（音频包络/节拍判定留 Python）
  ⇒ 输入逐位一致; 输出 double 原样返回（`dict(zip(ids, out.tolist()))`）。
* 契约不满足（网格缺失/长度不符/数组非常规/内核返回非 0）→ 返回 None,
  由调用方回退 Python 原版, **输出逐字节不变**。

## 反例纪律（为什么不用 numpy 批量重写而不是 C）

numpy 版本必须复刻 `math.hypot`/`math.atan2`/`math.exp` 的 CPython 语义
（numpy 的 hypot/atan2 实现不同）, 且逐设备标量顺序无法在批量下保持
—— 直接判"不适合"。C 逐设备同序复刻是唯一能保证逐字节一致的做法。
"""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger("qascade_ce.matrix_pixel")

# 与 csrc/_ce_mtx_kernel.c.inc 的 CE_MTX_MAXN / CE_MTX_MAXPARTS 同源
MAXN = 8192
MAXPARTS = 72


def _col_row_pointers(engine, n):
    """(col_ptr, row_ptr, cols, rows, aspect) —— 契约不满足返回 None。

    直接借 `MatrixGrid.col_np/row_np` 的缓冲（零拷贝）: 二者是 `int32`
    连续数组且 `grid.get_col_row(i) == int(col_np[i])`（MatrixGrid 内同源）。
    长度/类型/连续性任一不符 → None（回退原版, 不做隐式拷贝）。
    """
    grid = getattr(engine, "_grid", None)
    grid_n = getattr(grid, "n", None)
    if grid is None or grid_n is None:
        return None
    if int(grid_n) != int(n):
        return None
    cols = int(getattr(grid, "cols", 0) or 0)
    rows = int(getattr(grid, "rows", 0) or 0)
    if cols < 1 or rows < 1:
        return None
    col = getattr(grid, "col_np", None)
    row = getattr(grid, "row_np", None)
    if col is None or row is None:
        return None
    if (col.dtype != np.int32 or row.dtype != np.int32
            or col.shape != (int(n),) or row.shape != (int(n),)
            or not col.flags["C_CONTIGUOUS"] or not row.flags["C_CONTIGUOUS"]):
        return None
    return col, row, cols, rows, float(cols) / float(rows)


def _ids_of(engine):
    ids = getattr(engine, "_sorted_ids", None)
    return ids if ids is not None else []


def pattern_arrays(engine, variant, family_offset, compute_params):
    """`_compute_pattern` 的 CE 计算: 成功返回 dict, 契约外返回 None。

    compute_params = `_params(engine, variant)` 的结果（调用方提供以保证
    参数与 Python 原版逐位一致——音频包络/节拍判定留在 Python）。
    """
    if variant < 0 or family_offset < 0:
        return None
    ids = _ids_of(engine)
    n = len(ids)
    if n > MAXN:
        return None
    gv = _col_row_pointers(engine, n) if n else None
    if n and gv is None:
        return None
    (density, width, twist, mix, _speed, t, phase, freq, soft, bass, mid,
     high, rms, _beat) = compute_params
    if n == 0:
        return {}
    col, row, cols, rows, _aspect = gv
    return (col, row, cols, rows, width, twist, mix, t, phase, freq, soft,
            bass, mid, high, rms, int(variant), int(family_offset))


def particle_arrays(engine, variant, compute_params):
    """`_compute_particle` 的 CE 计算输入（同 pattern_arrays 约定）。"""
    if variant < 0:
        return None
    ids = _ids_of(engine)
    n = len(ids)
    if n > MAXN:
        return None
    gv = _col_row_pointers(engine, n) if n else None
    if n and gv is None:
        return None
    (density, width, twist, mix, _speed, t, phase, _freq, soft, bass, mid,
     high, rms, beat) = compute_params
    if n == 0:
        return {}
    col, row, cols, rows, _aspect = gv
    return (col, row, cols, rows, density, width, twist, mix, t, phase, soft,
            bass, mid, high, rms, 1 if beat else 0, int(variant))


def results_to_dict(ids, out):
    """C 输出 float64 缓冲 → `{fid: float}`（与 Python 原版逐位同值）。

    `ndarray.tolist()` 对 float64 → Python float 是**无损**转换（同一
    IEEE double）, 且比逐元素 `float(...)` 循环快约 10 倍。
    """
    return dict(zip(ids, out.tolist()))
