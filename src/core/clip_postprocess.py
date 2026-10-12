# -*- coding: utf-8 -*-
"""Clip 后处理字段的**唯一真源**（R111）—— 总览展示 / 方案存取共用一张表。

用户需求（2026-09-20）::

    "高级处理页面里面增加一个查看面板，平级于这些功能，就专门看 clip 当前的
     所有的后处理功能的设置情况，包括高级处理页面的效果，和 clip 右击页面的效果。
     全部显示，并且支持存储一个 clip 的全套后处理效果和应用到其它 clip 去。"

设计要点：
  · `POSTPROCESS_FIELDS` = 字段白名单（含中文标签与所属分组）。总览面板与"方案"
    的抓取/套用都读它 → 不存在两套清单漂移的可能。
  · **刻意排除**内容/几何字段（`frames`/`duration`/`source_duration`/
    `playback_rate`/`resize_cycle`/`active_channels_by_universe`/
    `base_order_fixture_ids`/`xy_reference_*`）：跨 clip 套用会破坏时长与录制内容
    （`timeline_widget` 的过渡优化注释亦明确"duration 只属于内容"）。
  · 方案存储：`Project.clip_postprocess_profiles: {name: {field: value}}`
    —— 与既有 `Project.delay_function_presets` 同模式（随工程保存、可撤销）。
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: 分组名（总览面板显示顺序）
GROUP_ADVANCED = "高级处理页"
GROUP_CONTEXT = "Clip 右击页"
GROUP_NEW = "临场滤网（R110）"

#: `(字段名, 中文标签, 分组)` —— 方案抓取/套用与总览共用（唯一清单）。
POSTPROCESS_FIELDS: tuple = (
    # ── 高级处理页 ──────────────────────────────────────────────────────────
    ("smooth_preset", "自动平滑预设", GROUP_ADVANCED),
    ("smooth_preset_name", "自动平滑预设名", GROUP_ADVANCED),
    ("smooth_custom_params", "自定义平滑参数", GROUP_ADVANCED),
    ("fade_type_ratio_in", "通道延迟比例（淡入）", GROUP_ADVANCED),
    ("fade_type_ratio_out", "通道延迟比例（淡出）", GROUP_ADVANCED),
    ("seq_delay_curve", "延迟函数曲线", GROUP_ADVANCED),
    # ── 右击页：过渡与方向 ─────────────────────────────────────────────────
    ("fade_in", "淡入时长(秒)", GROUP_CONTEXT),
    ("fade_out", "淡出时长(秒)", GROUP_CONTEXT),
    ("fade_in_direction", "淡入方向", GROUP_CONTEXT),
    ("fade_out_direction", "淡出方向", GROUP_CONTEXT),
    ("fade_direction", "统一方向（兼容）", GROUP_CONTEXT),
    ("fade_direction_edge", "应用边", GROUP_CONTEXT),
    ("fade_groups", "分组数", GROUP_CONTEXT),
    ("fade_group_sequence_mode", "组间运行方式", GROUP_CONTEXT),
    ("fade_group_pause", "组间停顿(秒)", GROUP_CONTEXT),
    ("fade_group_pauses", "各组停顿列表", GROUP_CONTEXT),
    ("fade_group_sequence_batches", "顺序批次", GROUP_CONTEXT),
    ("fade_group_spatial_scope", "坐标系", GROUP_CONTEXT),
    ("fade_group_skip_base_order", "跳过编号排序", GROUP_CONTEXT),
    ("fade_group_direction_overrides", "复合方向覆盖", GROUP_CONTEXT),  # R172 复合过渡（list，默认空表不计非默认）
    ("seq_delay_in", "淡入延迟(秒)", GROUP_CONTEXT),
    ("seq_delay_out", "淡出延迟(秒)", GROUP_CONTEXT),
    ("transition_strategy", "过渡方式（兼容）", GROUP_CONTEXT),
    ("transition_strategy_in", "淡入过渡方式", GROUP_CONTEXT),
    ("transition_strategy_out", "淡出过渡方式", GROUP_CONTEXT),
    ("transition_curve", "过渡曲线（兼容）", GROUP_CONTEXT),
    ("transition_curve_in", "淡入过渡曲线", GROUP_CONTEXT),
    ("transition_curve_out", "淡出过渡曲线", GROUP_CONTEXT),
    ("run_mode", "运行方式", GROUP_CONTEXT),
    ("transitions_optimized", "优化过渡方式预设", GROUP_CONTEXT),
    # ── 右击页：电机相关 ───────────────────────────────────────────────────
    ("motor_transition_phase_enabled", "电机参与过渡相位差", GROUP_CONTEXT),
    ("motor_transition_phase_amount", "相位差强度", GROUP_CONTEXT),
    ("motor_force_preset", "电机力度优化预设", GROUP_CONTEXT),
    ("motor_force_phase_amount", "力度层相位差", GROUP_CONTEXT),
    ("motor_force_accel", "力度曲线·加速段", GROUP_CONTEXT),
    ("motor_force_cruise", "力度曲线·匀速段", GROUP_CONTEXT),
    ("motor_force_decel", "力度曲线·减速段", GROUP_CONTEXT),
    # ── 右击页：其它 ───────────────────────────────────────────────────────
    ("hold_mode", "保持模式", GROUP_CONTEXT),
    ("hold_restore_fade_out", "保持恢复淡出(秒)", GROUP_CONTEXT),
    ("precharge_enabled", "泵预充", GROUP_CONTEXT),
    ("precharge_lead_ms", "预充提前量(ms)", GROUP_CONTEXT),
    ("precharge_level", "预充水平", GROUP_CONTEXT),
    ("record_output_source_enabled", "允许录制接收该 Clip 输出", GROUP_CONTEXT),
    ("light_tracking_enabled", "自动灯跟踪", GROUP_CONTEXT),
    ("light_tracking_source", "灯跟踪来源", GROUP_CONTEXT),
    ("light_tracking_threshold", "灯跟踪阈值", GROUP_CONTEXT),
    ("light_tracking_attack", "灯跟踪淡入(秒)", GROUP_CONTEXT),
    ("light_tracking_release", "灯跟踪淡出(秒)", GROUP_CONTEXT),
    ("light_tracking_color", "灯跟踪颜色", GROUP_CONTEXT),
    ("light_tracking_max_brightness", "灯跟踪最大亮度", GROUP_CONTEXT),
    # ── R110 临场滤网 ──────────────────────────────────────────────────────
    ("motor_limit_scale", "电机限位（幅度系数）", GROUP_NEW),
    ("motor_speed_limit_dps", "电机限速（度/秒，0=不限）", GROUP_NEW),
    ("pump_master_scale", "泵主控（输出比例）", GROUP_NEW),
)

#: 字段名集合（方案套用时只认这些键 —— 防止手工编辑的存档注入任意属性）。
POSTPROCESS_FIELD_NAMES: frozenset = frozenset(f for f, _l, _g in POSTPROCESS_FIELDS)

#: 工程级方案池字段名（`Project` 上的属性）。
PROFILES_ATTR = "clip_postprocess_profiles"


def _get_field(clip, name):
    return getattr(clip, name, None)


def capture_postprocess(clip) -> dict:
    """抓取该 clip 的全套后处理设置（只含白名单字段；值浅拷贝）。"""
    out: dict = {}
    for name in POSTPROCESS_FIELD_NAMES:
        val = _get_field(clip, name)
        try:
            out[name] = dict(val) if isinstance(val, dict) else val
        except Exception:
            out[name] = val
    return out


def _apply_one(clip, name: str, value) -> bool:
    if name not in POSTPROCESS_FIELD_NAMES:
        return False
    try:
        cur = getattr(clip, name, None)
        setattr(clip, name, dict(value) if isinstance(value, dict) else value)
        return True
    except Exception:
        log.debug("apply postprocess field %s failed", name, exc_info=True)
        return False


def apply_postprocess(clip, data) -> int:
    """把一套后处理设置套到 clip 上；返回成功套用的字段数（白名单外的键忽略）。"""
    if not isinstance(data, dict):
        return 0
    n = 0
    for name, value in data.items():
        if _apply_one(clip, str(name), value):
            n += 1
    return n


# ── 总览展示 ─────────────────────────────────────────────────────────────────

def _fmt_value(value) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "开" if value else "关"
    if isinstance(value, dict):
        if not value:
            return "—"
        return "、".join(f"{k}={_fmt_value(v)}" for k, v in list(value.items())[:6])
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _is_non_default(clip, name: str, value) -> bool:
    """该字段是否处于"非默认"（用于总览里高亮/计数）。"""
    try:
        from ..core.models import Clip
        default = Clip.__dataclass_fields__[name].default
        if callable(default) or default.__class__.__name__ == "_MISSING_TYPE":
            return bool(value)
        return value != default
    except Exception:
        return bool(value)


def describe_groups(clip) -> list:
    """`[(组名, [(标签, 值文本), ...]), ...]` —— 只列非默认项，全默认时给一行提示。"""
    out: list = []
    for group in (GROUP_ADVANCED, GROUP_CONTEXT, GROUP_NEW):
        rows = []
        for name, label, g in POSTPROCESS_FIELDS:
            if g != group:
                continue
            val = _get_field(clip, name)
            if not _is_non_default(clip, name, val):
                continue
            rows.append((label, _fmt_value(val)))
        if rows:
            out.append((group, rows))
    if not out:
        out.append(("后处理", [("（全部为默认，未启用任何后处理）", "—")]))
    return out


# ── 方案库（存于工程）────────────────────────────────────────────────────────

def _store(proj) -> dict:
    if proj is None:
        return {}
    store = getattr(proj, PROFILES_ATTR, None)
    if not isinstance(store, dict):
        store = {}
        try:
            setattr(proj, PROFILES_ATTR, store)
        except Exception:
            return {}
    return store


def profile_names(proj) -> tuple:
    """工程内已保存的方案名（排序）。"""
    return tuple(sorted(_store(proj).keys()))


def _norm_pos(pos) -> int:
    """槽位号归一（非法/缺失 → -1 = 未指定）。"""
    try:
        v = int(pos)
    except (TypeError, ValueError):
        return -1
    return v if v >= 0 else -1


def save_profile(proj, name: str, clip, position=None) -> int:
    """把 clip 的全套后处理存成命名方案（覆盖同名）；返回该方案的槽位号。

    `position` 给定且未被占用时用该槽（对齐"点空槽存档"交互）；
    未给定或已占用 → 取第一个空槽（`next_free_position`）。
    """
    store = _store(proj)
    pos = _norm_pos(position)
    used = {int(v.get("_position", -1)) for v in store.values() if isinstance(v, dict)}
    if pos < 0 or pos in used:
        pos = 0
        while pos in used:
            pos += 1
    data = capture_postprocess(clip)
    data["_position"] = int(pos)
    store[str(name)] = data
    return int(pos)


def count_active_fields(data) -> int:
    """方案里**非默认**字段数（= 用户真正设置了几个后处理项）。

    ★ 修（2026-09-20 视觉验收发现）：原实现数的是"白名单里的键总数"（恒 52），
      槽位上写着 "52 项" 而实际只设了 7 项 —— 数字毫无意义。
    """
    if not isinstance(data, dict):
        return 0
    n = 0
    for key, val in data.items():
        if key not in POSTPROCESS_FIELD_NAMES:
            continue
        if _is_default_value(str(key), val):
            continue
        n += 1
    return n


def _is_default_value(name: str, value) -> bool:
    """该字段值是否等于 dataclass 默认值（无法判定时按"非默认"处理，宁可多计）。"""
    try:
        from .models import Clip as _Clip
        default = _Clip.__dataclass_fields__[name].default
        if callable(default) or default.__class__.__name__ == "_MISSING_TYPE":
            return not bool(value)
        return value == default
    except Exception:
        return not bool(value)


def profile_entries(proj) -> tuple:
    """`((position, name, field_count), ...)` —— 槽位网格用（按 position 升序）。

    自动为没有 `_position` 的旧方案分配稳定槽位（按名字序补空位），
    保证旧工程打开后也能在网格里看到。
    """
    store = _store(proj)
    names = sorted(store.keys())
    used = {_norm_pos((store[n] or {}).get("_position")) for n in names}
    used.discard(-1)
    nxt = 0
    out = []
    for n in names:
        pos = _norm_pos((store[n] or {}).get("_position"))
        if pos < 0:
            while nxt in used:
                nxt += 1
            pos = nxt
            used.add(pos)
            try:
                store[n]["_position"] = int(pos)
            except Exception:
                pass
        out.append((int(pos), str(n), count_active_fields(store[n])))
    out.sort(key=lambda t: (t[0], t[1]))
    return tuple(out)


def rename_profile(proj, old: str, new: str) -> bool:
    """重命名方案（保留槽位；新名为空或同名 → False）。"""
    store = _store(proj)
    old_s, new_s = str(old), str(new).strip()
    if not new_s or old_s not in store or new_s == old_s:
        return False
    store[new_s] = store.pop(old_s)
    return True


def delete_profile(proj, name: str) -> bool:
    store = _store(proj)
    return store.pop(str(name), None) is not None


def get_profile(proj, name: str):
    return _store(proj).get(str(name))


def apply_profile_to_clips(proj, name: str, clips) -> int:
    """把方案套到若干 clip；返回套用成功的 clip 数（`_position` 等内部键不套）。"""
    data = get_profile(proj, name)
    if not data:
        return 0
    data = {k: v for k, v in data.items() if k in POSTPROCESS_FIELD_NAMES}
    n = 0
    for clip in (clips or []):
        if apply_postprocess(clip, data) > 0:
            n += 1
    return n
