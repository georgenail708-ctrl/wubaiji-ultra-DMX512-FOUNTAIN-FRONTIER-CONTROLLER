# -*- coding: utf-8 -*-
"""关联器（track linker）CE 计算层 —— `_process_once` 整个规则遍历段 → C 内核。

背景（2026-09-18 实测归因, render_lab/bench/probe_linker_tick_profile.py）:

    40 台设备 / 120 条规则 / 3 域（实测 200 tick 均值）:
      _process_rule    7.452 ms/tick  (82.1%)   ← 本模块接管
      _get_snapshot    0.841 ms/tick  ( 9.3%)
      update_layer     0.845 ms/tick  ( 9.3%)
      _clear_stale     0.132 ms/tick  ( 1.5%)
      活跃掩码         0.075 ms/tick  ( 0.8%)

    `_process_rule` 内部 cProfile tottime 首位是 **886 次/tick 的 np.clip 标量
    调用**（`_write_dst` L2629/L2637 各 22.6%, `_color_whiten_writes` L246
    18.1%, `_light_track_writes` L264 18.1%, ...）——每个标量 np.clip 走
    numpy 的 `_wrapfunc/_wrapit/_clip_dispatcher` 五层 Python 封装 ~2.5µs。
    其次是 dict 查找（132420 次/30 tick）、嵌套闭包重建、逐通道函数调用。

方案：整段搬到 C（`ce_linker_rule`），Python 侧只做「静态表构建 / dict 翻译 /
写入累积」。

## 位级等价三件套（与既有 CE 内核同标准）

1. C 侧数值全 f64 + 动态 ucrtbase 超越函数（CPython 同实现 ⇒ 逐位相等;
   实证 40 万样本 cos/sin/exp/sqrt/fabs/pow 全 0 失配）。
2. 参数解析（本模块 `pack_params`）走**声明式默认表** `_FN_DEFAULTS`
   （逐条对齐 track_linker_engine.py:380-704 各 fn 内 `.get(key, 字面量)`），
   `render_lab/bench/ce_fuzz_linker.py` 对**每个预设 × 缺 key 的随机参数**
   逐字节对拍 `preset_def.fn` vs `ce_linker_preset_eval` → 默认值漂移当场
   被门禁抓住（与既有 ce_fuzz_*.py 同纪律）。
3. 两遍式 C 内核: pass1 只读决策, pass2 才提交 → 返回码非 0 = 本规则零副
   作用回退 Python 原版（输出逐字节不变）。

## 与 Python 原版的结构差异（有意为之, 语义等价）

* `_rule_prep_cache` 里新增 `(kind, params_arr)` 静态项——原版每 tick 重建
  `dict(rule.params)`; 参数不变则数组也不变（规则变更经 rules_changed →
  `_invalidate_rule_caches` 清 `_rule_prep_cache`, 语义与 key_of/fine_of
  缓存同一条失效链）。
* 掩码指针表（snap/motion/act/motact/parked/pend/pend_has）每 tick 重建一次
  （O(域数) 的列表推导, 与既有 `_get_snapshot` 的 dict 遍历同阶）；表项为
  None（域缺失）或 ndarray.ctypes.data。
* C 输出的写入计划（表索引/地址/值）由本模块翻译回 `writes` 字典——保持
  `_process_once` 既有的 `{universe: {addr: int}}` 结构与写回 NM 的路径全不变。
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict

import numpy as np

log = logging.getLogger("qascade_ce.linker")

# ── 参数槽（与 csrc/_ce_linker_kernel.c.inc 的 CE_LP_* 同源, 逐一对应）──
NPARAM = 40
SLOT: Dict[str, int] = {
    "center": 0, "center_x": 1, "center_y": 2, "cutoff": 3, "max_pump": 4,
    "sigma": 5, "scale": 6, "base": 7, "invert": 8, "span": 9, "white": 10,
    "src_min": 11, "src_max": 12, "pump_max": 13, "pump_min": 14,
    "gamma": 15, "phase": 16, "lo": 17, "hi": 18, "freq": 19, "depth": 20,
    "threshold": 21, "max_val": 22, "max": 23, "smooth": 24, "gain": 25,
    "max_speed": 26, "width": 27,
    # 灯跟踪目标参数槽（_LIGHT_TRACK_CH_MAP → dimmer/R/G/B/W）
    "dimmer": 28, "R": 29, "G": 30, "B": 31, "W": 32,
}
_LIGHT_KEYS = ("dimmer", "R", "G", "B", "W")

# ── 预设 id → 数值内核编号（与 C 的 CE_LK_* 同源; 37 个内置预设全在内）──
KIND: Dict[str, int] = {
    "motor_pump_cosine": 0, "motor_pump_linear": 1, "motor_pump_step": 2,
    "motor_pump_gaussian": 3, "motor_pump_bidir_pos": 4,
    "motor_pump_bidir_neg": 5, "motor_xy_pump": 6,
    "motor_valve_linear": 7, "motor_valve_cosine": 8,
    "motor_color_whiten": 9,
    "pump_dimmer_linear": 10, "pump_dimmer_sqrt": 11, "pump_dimmer_power": 12,
    "motor_color_hue": 13,
    "intensity_pump_linear": 14, "intensity_pump_threshold": 15,
    # fn 同为 _channel_linear 的六条 + passthrough（_passthrough 同式）
    "channel_linear": 16, "passthrough": 16, "dimmer_to_rgb_mix": 16,
    "r_to_gb_tint": 16, "b_to_rg_depth": 16, "dimmer_to_white": 16,
    "pump_to_valve_linear": 16, "valve_to_pump_linear": 16,
    "invert": 17, "clamp_range": 18, "sine_modulate": 19,
    "vario_pump_linear": 20, "vario_pump_curve": 21,
    # fn 同为 _center_scale_map
    "motor_x_to_y_rotate": 22, "motor_z_to_xy_sync": 22,
    "motor_to_dimmer_follow": 22,
    "motor_y_to_x_mirror": 23,
    # fn 同为 _deviation_map
    "motor_xy_to_z": 24, "motor_deviation_dimmer": 24,
    "dimmer_to_motor_home": 25,
    "pump_to_valve_pulse": 26,
    "pump_light_track": 27, "pump_light_track_linear": 28,
    "pump_light_track_step": 29, "pump_light_track_pulse": 30,
}

# ── fn 内 `.get(key, 字面量)` 默认值（逐条对齐 :380-704; 单值给所有预设）──
# 读法: 某 key 在 params_def 里**没有** default 时, 用这里的值; params_def
# 有 default 时 params_def 优先（= fn 的 .get 第二参由 params_def 的 default
# 表达, 两者在 src/ 里本就是同一数字——见各 LinkPresetDef params_def）。
_FN_DEFAULTS: Dict[str, float] = {
    "center": 128.0, "center_x": 128.0, "center_y": 128.0,
    "cutoff": 64.0, "max_pump": 255.0, "sigma": 40.0, "scale": 1.0,
    "base": 0.0, "invert": 0.0, "span": 128.0, "white": 255.0,
    "src_min": 0.0, "src_max": 255.0, "pump_max": 255.0, "pump_min": 0.0,
    "gamma": 1.0, "phase": 0.0, "lo": 0.0, "hi": 255.0, "freq": 1.0,
    "depth": 64.0, "threshold": 30.0, "max_val": 255.0, "max": 255.0,
    "smooth": 20.0, "gain": 1.0, "max_speed": 255.0, "width": 40.0,
    "dimmer": 255.0, "R": 255.0, "G": 255.0, "B": 255.0, "W": 255.0,
}

# ── 逐预设覆盖（fn 内字面量与 _FN_DEFAULTS 不同, 或 params_def 未声明）──
# 每条都已核对 track_linker_engine.py 对应 fn 的 `.get` 第二参。
_PER_PRESET: Dict[str, Dict[str, float]] = {
    # _motor_valve_linear: scale 默认 2.0; base 默认 128.0
    "motor_valve_linear": {"scale": 2.0, "base": 128.0},
    # _motor_valve_cosine: max_val 默认 255.0（_FN_DEFAULTS 已含）
    # _motor_xy_to_pump: cutoff 默认 sqrt(2)*127（不是 64）
    "motor_xy_pump": {"cutoff": math.sqrt(2) * 127.0},
    # _pump_dimmer_power: gamma = max(0.01, p.get("gamma", 2.0))
    "pump_dimmer_power": {"gamma": 2.0},
    # _pump_to_valve_pulse（_threshold_map）: threshold 30.0 / max 255.0
    "pump_to_valve_pulse": {"threshold": 30.0, "max": 255.0},
    # _motor_y_to_x_mirror（_mirror_map）: center 128.0
    "motor_y_to_x_mirror": {"center": 128.0},
    # _dimmer_to_motor_home（_home_drive）: threshold 60.0 / max_speed 255.0
    "dimmer_to_motor_home": {"threshold": 60.0, "max_speed": 255.0},
    # _motor_xy_to_z / _motor_deviation_dimmer（_deviation_map）: max 255.0
    "motor_xy_to_z": {"max": 255.0},
    "motor_deviation_dimmer": {"max": 255.0},
    # _pump_light_track_pulse: center 200.0 / width 40.0
    "pump_light_track_pulse": {"center": 200.0, "width": 40.0},
    # _vario_pump_map: gamma 由 gamma 参数（linear 传 1.0）
    "vario_pump_curve": {"gamma": 1.0},
    # ★ R93 修: _intensity_pump_threshold 的 `.get("threshold", 128.0)`;
    #   通用表里 threshold=30.0 是 pump_light_track / pump_to_valve_pulse 的
    #   字面量（fuzz 实测: params={} 时 src=64 → Python 0.0 / C 38.53）。
    "intensity_pump_threshold": {"threshold": 128.0},
}

# ── `offset` 的槽位别名（R93 修）────────────────────────────────────────
# `_pump_dimmer_linear` / `_intensity_pump_linear` / `_passthrough` /
# `_channel_linear`（dimmer_to_rgb_mix 等 6 条）都读 `p.get("offset", 0.0)`,
# 而 C 内核这几族统一用 `p[CE_LP_BASE]`（`src*scale + base`）——slot 表里
# 只有 "base" 没有 "offset" → 用户设了「偏移量」滑杆后 C 侧恒按 0 算
# （fuzz 实测 9 个预设全部失配, UI 可达范围内 19/20 组参数）。
# 这几个预设的 fn 都不读 "base", 故 offset 直接落 base 槽即可。
_OFFSET_AS_BASE_KINDS = frozenset((10, 14, 16))   # pump_dimmer_linear /
#   intensity_pump_linear / _channel_linear(含 passthrough)


def defaults_for(preset_id: str) -> Dict[str, float]:
    """预设的完整默认参数（逐 key 一个定值; C 侧不做 .get）。"""
    out = dict(_FN_DEFAULTS)
    out.update(_PER_PRESET.get(str(preset_id or ""), {}))
    return out


def pack_params(preset_id: str, params: Dict[str, Any]) -> np.ndarray:
    """规则 params dict → CE_LNK_NPARAM 个 float64（缺 key 用 fn 硬默认）。"""
    arr = np.zeros(NPARAM, dtype=np.float64)
    dflt = defaults_for(preset_id)
    for key, slot in SLOT.items():
        v = params.get(key, None) if params else None
        if v is None:
            arr[slot] = float(dflt.get(key, 0.0))
        else:
            try:
                arr[slot] = float(v)
            except Exception:
                arr[slot] = float(dflt.get(key, 0.0))
    # `offset` → base 槽（见 _OFFSET_AS_BASE_KINDS 注释; C 内核该族读 base）
    if KIND.get(str(preset_id or "")) in _OFFSET_AS_BASE_KINDS:
        off = params.get("offset", None) if params else None
        if off is None:
            arr[SLOT["base"]] = float(dflt.get("offset", 0.0))
        else:
            try:
                arr[SLOT["base"]] = float(off)
            except Exception:
                arr[SLOT["base"]] = float(dflt.get("offset", 0.0))
    # ★ R93 修: _pump_dimmer_power 的 `.get("gamma", 2.0)` 外面还有
    #   `max(0.01, ...)` 钳位; C 侧直接 pow(x, gamma) → gamma<=0 时整段不同
    #   （实测 gamma=-0.798: Python 254.28 / C 319.5; gamma=-42: C inf）。
    if str(preset_id or "") == "pump_dimmer_power":
        try:
            g = float(arr[SLOT["gamma"]])
        except Exception:
            g = 2.0
        if not (g > 0.01):
            arr[SLOT["gamma"]] = 0.01
    # center_x/center_y: _xy_deviation 的 p.get("center_x", p.get("center", 128))
    if params:
        if params.get("center_x") is not None:
            pass          # 上面已按 key 取到
        elif params.get("center") is not None:
            arr[SLOT["center_x"]] = arr[SLOT["center"]]
        if params.get("center_y") is not None:
            pass
        elif params.get("center") is not None:
            arr[SLOT["center_y"]] = arr[SLOT["center"]]
    return arr


# ── 模式 / 类型码（与 C 的 CE_LM_* / CE_LT_* 同源）──────────────────────
MODE_BROADCAST = 0
MODE_PAIRED = 1
MODE_XY_MULTI = 2
MODE_WHITEN = 3
MODE_LIGHT = 4

TYPE_CODE: Dict[str, int] = {
    "motor_x": 1, "motor_y": 2, "motor_z": 3,
    "motor_x_fine": 4, "motor_y_fine": 5, "motor_z_fine": 6,
    "pump": 7, "dimmer": 8,
    "r": 9, "g": 10, "b": 11, "w": 12,
}
# 灯跟踪目标 → 参数槽下标（与 C 的 _ce_lt_light_slot 同源）
LIGHT_SLOT: Dict[str, int] = {"dimmer": 0, "r": 1, "g": 2, "b": 3, "w": 4}

MAXN = 2048

def _canon_key(ch_type: Any) -> str:
    from src.processing.track_linker_engine import _channel_type_key
    return _channel_type_key(ch_type)


def type_code(ch_type: Any) -> int:
    """通道类型 → C 类型码; 未识别 → 0（CE_LT_PLAIN, 无 16bit/fine 语义）。"""
    return TYPE_CODE.get(_canon_key(ch_type), 0)


def light_slot(ch_type: Any) -> int:
    """灯跟踪目标参数槽（未命中 → dimmer=0, 同 _LIGHT_TRACK_CH_MAP 兜底）。"""
    return LIGHT_SLOT.get(_canon_key(ch_type), 0)


# 注（R93）: 本模块此前还有一个 `_ptr()` 助手, 函数体引用未定义的 `_C_U8P`,
# 且 `except Exception` 把 NameError 一并吞掉 → 对任何数组都静默返回 None。
# 全库无调用点（钩子侧走自己的 `_raw_ptr`/`_data_ptr`）→ 已删除, 消除同型陷阱。
