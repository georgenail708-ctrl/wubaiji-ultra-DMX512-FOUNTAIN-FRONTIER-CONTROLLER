# -*- coding: utf-8 -*-
"""Studio 编译字段 → TimelineWidget 真实菜单处理器路由（R172 W1d2 任务一）。

**假接线防线**：逐字段组复用 `timeline_widget.py` 既有 `_on_clip_*` 处理器的
**真实语义**（签名/快照/钳制/派生/刷新），宁可调处理器也不直写裸字段。
路由表（grep 处理器实名核对，2026-09-26）：

===========================  ===============================================
字段组                        调用原语（TimelineWidget 既有槽 / 直写）
===========================  ===============================================
过渡族（方向/组数/批次/停顿）  `_on_clip_dir_fade_set`（**完整 8 参**合成，
                             clip 现值 ∪ prev_fields ∪ fields，缺键不得部分传）
fade_direction_edge           槽是「多选 toggle」语义 → 改用**绝对设置**直写
                             （快照+写+`_refresh_clips_targeted`，同槽收尾语义）
fade_in / fade_out            `_on_clip_fade_commit`（NaN 哨兵=不改该侧）
seq_delay_in / out            `_on_clip_seq_delay_commit`（同 NaN 哨兵）
fade_group_spatial_scope      `_on_clip_spatial_scope_set`
fade_group_skip_base_order    `_on_clip_skip_base_order_set`
transition_strategy_in/out    `_on_clip_transition_strategy_set`
playback_rate                 `_on_clip_speed_changed`（含时长重算副作用）
smooth_preset(_name)          `_on_clip_auto_smooth`（槽自带离线重处理+名称）
light_tracking_* 全套         `_on_clip_light_tracking`（**完整 7 参**合成）
motor_force 族                `_on_clip_motor_force`（槽只认 5 预设；卡片的
                             "custom" 档位无槽路径 → 字段直写并注明）
motor_transition_phase_*      `_on_clip_motor_transition_phase_changed`
precharge_*                   `_on_clip_precharge_changed`
hold_mode                     `_on_clip_hold_mode_changed`
record_output_source_enabled  `_on_clip_record_output_source_changed`
motor_limit_scale 等三滤网     直写 attr + `_refresh_clips_targeted`
                             （= `_on_clip_advanced_changed` 的刷新语义）
fade_type_ratio_in/out        直写 attr + 精准刷新（原高级处理面板同款直写）
seq_delay_curve               直写 attr + 精准刷新
transitions_optimized         直写 attr + 精准刷新（优化器对话框是交互路径，
                             Studio 卡只落结果字段）
fade_group_direction_overrides 直写 attr + `_invalidate_engine_cache`
__light_suppressed__          `engine.clip_output_filters.set_light_suppressed`
                             （**全局会话级**，去重只调一次，绝不 setattr 到 clip）
===========================  ===============================================

收尾纪律：脏标记/刷新按各处理器自带语义；`fields` 里出现路由表外的键 →
`log.warning`（新键必须有归宿，防假接线）。弹窗与作用域说明：三个槽
（`_on_clip_auto_smooth` / `_on_clip_motor_force` / `_on_clip_precharge_changed`）
在菜单路径里弹一次性确认框；Studio 是 300ms 防抖的自动应用路径，弹模态框会
连环打断编辑 —— 故本模块在槽调用窗口内把 `timeline_widget.QMessageBox`
临时替换为静音桩（仅覆盖槽调用，不动槽的字段/快照/刷新语义，测试证明逐字段
与菜单信号路径一致）。这是一处对任务书"调处理器"的刻意补充，已记录偏差。
同窗口内还把 `timeline_widget._STUDIO_SINGLE_CLIP` 置 True（with/finally 恢复）：
sheet 只作用于绑定 clip，槽的 `_resolve_context_clip_ids` 不得向当前多选扩散。
失败回传：平滑槽的失败路径静默（did==0 直接 return）→ `_apply_smooth` 核对
`smooth_preset` 未落上时回传失败键，调用方（面板）提示并回退签名重试。
"""
from __future__ import annotations

import importlib
import logging
import math
from types import SimpleNamespace

log = logging.getLogger(__name__)

__all__ = ["apply_compiled_fields", "merge_overrides", "LIGHT_SUPPRESS_KEY",
           "ROUTABLE_FIELD_KEYS"]

#: 开关灯卡的引擎滤网特殊键（全局会话级，见 studio_compile.py docstring）。
LIGHT_SUPPRESS_KEY = "__light_suppressed__"

_NAN = float("nan")
_LIGHT_TW_MODULE = "src.ui.timeline.timeline_widget"

#: 过渡族键（任一出现 → 合成完整 8 参调 `_on_clip_dir_fade_set`）。
_DIR_FADE_FAMILY = (
    "fade_in_direction", "fade_out_direction", "fade_groups",
    "fade_group_sequence_mode", "fade_group_sequence_batches",
    "fade_group_pause", "fade_group_pauses",
)
_LIGHT_TRACK_KEYS = (
    "light_tracking_enabled", "light_tracking_source", "light_tracking_threshold",
    "light_tracking_attack", "light_tracking_release", "light_tracking_color",
    "light_tracking_max_brightness",
)
_FORCE_PARAM_KEYS = (
    "motor_force_phase_amount", "motor_force_accel",
    "motor_force_cruise", "motor_force_decel",
)
#: 直写族（attr 直写 + 精准刷新；overrides 走引擎缓存失效）。
#: R182：`transition_curve_in/out` 并入直写族（引擎 `_clip_transition_curve`
#: 直接读 attr，无既有槽；主控过渡卡是唯一写者，同 `fade_type_ratio_*` 先例）。
_DIRECT_WRITE_KEYS = (
    "motor_limit_scale", "motor_speed_limit_dps", "pump_master_scale",
    "fade_type_ratio_in", "fade_type_ratio_out", "seq_delay_curve",
    "transition_curve_in", "transition_curve_out",
    "transitions_optimized", "fade_group_direction_overrides",
)
#: 本模块认识去向的全部键（路由表外键出现 → warning，防假接线）。
ROUTABLE_FIELD_KEYS = frozenset({
    *_DIR_FADE_FAMILY, "fade_direction",                 # fade_direction=槽内派生键
    "fade_in", "fade_out", "seq_delay_in", "seq_delay_out",
    "fade_direction_edge", "fade_group_spatial_scope", "fade_group_skip_base_order",
    "transition_strategy_in", "transition_strategy_out",
    "playback_rate", "smooth_preset", "smooth_preset_name", "smooth_custom_params",
    *_LIGHT_TRACK_KEYS, "motor_force_preset", *_FORCE_PARAM_KEYS,
    "motor_transition_phase_enabled", "motor_transition_phase_amount",
    "precharge_enabled", "precharge_lead_ms", "precharge_level",
    "hold_mode", "record_output_source_enabled",
    *_DIRECT_WRITE_KEYS, LIGHT_SUPPRESS_KEY,
})


# ── 编译结果 → 应用字段集 ───────────────────────────────────────────────────

def merge_overrides(res):
    """`CompileResult` → 应用字段集：fields + overrides 并入覆盖键。

    W1d2 假接线防线：复合过渡覆盖（设计 §4）在 `CompileResult.overrides` 里，
    面板只传 `res.fields` 的话覆盖就是"死数据" —— 故在此并入
    `fade_group_direction_overrides`（键 ∈ POSTPROCESS 白名单）。
    """
    fields = dict(getattr(res, "fields", None) or {})
    ov = [dict(o) if isinstance(o, dict) else o
          for o in (getattr(res, "overrides", None) or [])]
    if ov:
        fields["fade_group_direction_overrides"] = ov
    return fields


# ── 小工具 ──────────────────────────────────────────────────────────────────

class _QuietBox:
    """QMessageBox 静音桩（只供槽调用窗口内替换 module 全局）。"""

    @staticmethod
    def information(*_a, **_k):
        return None

    @staticmethod
    def warning(*_a, **_k):
        return None

    @staticmethod
    def question(*_a, **_k):
        return None

    @staticmethod
    def critical(*_a, **_k):
        return None


class _quiet_dialogs:
    """槽调用窗口内屏蔽一次性确认弹窗（防抖自动应用路径不宜弹模态框）。"""

    def __enter__(self):
        mod = importlib.import_module(_LIGHT_TW_MODULE)
        self._mod = mod
        self._saved = getattr(mod, "QMessageBox", None)
        mod.QMessageBox = _QuietBox
        return self

    def __exit__(self, *_exc):
        if self._saved is not None:
            self._mod.QMessageBox = self._saved
        return False


class _single_clip_scope:
    """槽调用窗口内置 `timeline_widget._STUDIO_SINGLE_CLIP=True`（with/finally 恢复）。

    设计 §2.4：sheet 只作用于**绑定 clip**；而 15 个槽内部的
    `_resolve_context_clip_ids` 会把单 id 扩散到当前多选 —— Studio 防抖自动应用
    若撞上用户多选就会向未绑定的 clip 扩散。与 `_quiet_dialogs` 同款模块属性
    置位模式（菜单/信号路径不受影响，开关默认 False）。"""

    def __enter__(self):
        mod = importlib.import_module(_LIGHT_TW_MODULE)
        self._mod = mod
        self._saved = getattr(mod, "_STUDIO_SINGLE_CLIP", False)
        mod._STUDIO_SINGLE_CLIP = True
        return self

    def __exit__(self, *_exc):
        self._mod._STUDIO_SINGLE_CLIP = self._saved
        return False


def _find_clip(tw, cid: str):
    try:
        _track, clip = tw.ctrl._find_track_and_clip(cid)
        return clip
    except Exception:
        return None


def _as_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return _NAN


def _pick(c, key: str, default=None):
    """fields ∪ prev_fields ∪ clip 现值 三级取值（键存在性优先，防 0 值吞掉）。"""
    if key in c.eff:
        return c.eff[key]
    if key in c.prev:
        return c.prev[key]
    return getattr(c.clip, key, default)


def _num(c, key: str, default):
    """_pick + 数值收敛（坏值回默认）。"""
    v = _pick(c, key, default)
    try:
        return type(default)(v)
    except (TypeError, ValueError):
        return default


def _write_attrs(c, pairs) -> bool:
    """逐 attr 直写（值未变不写，memcmp 纪律）；返回是否真改。"""
    changed = False
    for attr, value in pairs:
        if getattr(c.clip, attr, None) != value:
            setattr(c.clip, attr, value)
            changed = True
    return changed


# ── 各字段组 applier（签名统一 `_apply_xxx(c)`，c=SimpleNamespace 上下文）────

def _apply_edge_absolute(c) -> None:
    """应用边：槽 `_on_clip_dir_edge_set` 对多选是 toggle 语义（全目标已中 → 回
    both），Studio 语义=**绝对设置** → 按槽的写/快照/刷新语义直写（先于过渡族，
    使族调用读到新边并按其过滤写入侧，与菜单「先设边再设方向」顺序一致）。"""
    edge = str(_pick(c, "fade_direction_edge", "both") or "both").strip().lower()
    if edge not in ("in", "out"):
        edge = "both"
    cur = str(getattr(c.clip, "fade_direction_edge", "both") or "both").strip().lower()
    if cur not in ("in", "out"):
        cur = "both"
    if cur == edge:
        return
    c.tw._snapshot_for_undo()
    c.clip.fade_direction_edge = edge
    c.tw._refresh_clips_targeted([c.cid])


def _apply_transition_family(c) -> None:
    """过渡族 → `_on_clip_dir_fade_set`（完整 8 参，缺键按现值/prev 补齐）。"""
    pauses = _pick(c, "fade_group_pauses", None)
    args = (
        str(_pick(c, "fade_in_direction", "none") or "none"),
        str(_pick(c, "fade_out_direction", "none") or "none"),
        max(1, _num(c, "fade_groups", 1) or 1),
        str(_pick(c, "fade_group_sequence_mode", "parallel") or "parallel"),
        max(0.0, _num(c, "fade_group_pause", 0.0)),
        max(0, _num(c, "fade_group_sequence_batches", 0) or 0),
        list(pauses) if isinstance(pauses, (list, tuple)) else None,
    )
    c.tw._on_clip_dir_fade_set(c.cid, *args)


def _apply_fade_durations(c) -> None:
    """淡入/淡出时长 → `_on_clip_fade_commit`（NaN 哨兵=不改该侧）。"""
    fin = _as_float(c.eff["fade_in"]) if "fade_in" in c.eff else _NAN
    fout = _as_float(c.eff["fade_out"]) if "fade_out" in c.eff else _NAN
    if not math.isnan(fin) or not math.isnan(fout):
        c.tw._on_clip_fade_commit(c.cid, fin, fout)


def _apply_seq_delays(c) -> None:
    """延迟翼 → `_on_clip_seq_delay_commit`（NaN 哨兵同款）。"""
    din = _as_float(c.eff["seq_delay_in"]) if "seq_delay_in" in c.eff else _NAN
    dout = _as_float(c.eff["seq_delay_out"]) if "seq_delay_out" in c.eff else _NAN
    if not math.isnan(din) or not math.isnan(dout):
        c.tw._on_clip_seq_delay_commit(c.cid, din, dout)


def _apply_spatial_scope(c) -> None:
    c.tw._on_clip_spatial_scope_set(c.cid, str(c.eff.get("fade_group_spatial_scope") or "per_shape"))


def _apply_skip_base_order(c) -> None:
    c.tw._on_clip_skip_base_order_set(c.cid, bool(c.eff.get("fade_group_skip_base_order")))


def _apply_transition_strategies(c) -> None:
    if "transition_strategy_in" in c.eff:
        c.tw._on_clip_transition_strategy_set(c.cid, "in", str(c.eff["transition_strategy_in"] or ""))
    if "transition_strategy_out" in c.eff:
        c.tw._on_clip_transition_strategy_set(c.cid, "out", str(c.eff["transition_strategy_out"] or ""))


def _apply_playback_rate(c) -> None:
    """速度 → `_on_clip_speed_changed`（槽内含 source_duration/duration 重算）。"""
    c.tw._on_clip_speed_changed(c.cid, _as_float(c.eff.get("playback_rate")))


def _apply_smooth(c) -> list:
    from .studio_smoothing import apply
    return apply(c)


def _apply_light_tracking(c) -> None:
    """灯跟踪 → `_on_clip_light_tracking`（完整 7 参合成，缺键按现值/prev 补齐）。"""
    args = (
        bool(_pick(c, "light_tracking_enabled", False)),
        max(0, min(255, _num(c, "light_tracking_threshold", 128))),
        max(0.01, _num(c, "light_tracking_attack", 0.08)),
        max(0.01, _num(c, "light_tracking_release", 0.22)),
        str(_pick(c, "light_tracking_color", "#FFFFFF") or "#FFFFFF"),
        str(_pick(c, "light_tracking_source", "pump") or "pump"),
        max(0, min(255, _num(c, "light_tracking_max_brightness", 255))),
    )
    c.tw._on_clip_light_tracking(c.cid, *args)


def _force_direct_pairs(c, preset: str) -> list:
    """作者显式力度参数 → 直写对；在命名预设应用后覆盖其默认值。"""
    pairs = []
    if preset or "motor_force_preset" in c.eff:
        pairs.append(("motor_force_preset", preset))
    for key in _FORCE_PARAM_KEYS:
        if key in c.eff:
            pairs.append((key, max(0.0, _num(c, key, 0.0))))
    return pairs


def _apply_motor_force(c) -> None:
    """力度族 → `_on_clip_motor_force`（5 预设走槽）；"custom" 槽不认 → 字段直写。"""
    preset = str(c.eff.get("motor_force_preset", "") or "")
    if preset and preset != "custom":
        c.tw._on_clip_motor_force(c.cid, preset)
        if preset == "bypass":
            return
    pairs = [(key, value) for key, value in _force_direct_pairs(c, preset)
             if getattr(c.clip, key, None) != value]
    if not pairs:
        return
    c.tw._snapshot_for_undo()
    if _write_attrs(c, pairs):
        c.tw._refresh_clips_targeted([c.cid])
        c.tw._mark_project_dirty()


def _apply_motor_phase(c) -> None:
    """电机相位差 → `_on_clip_motor_transition_phase_changed`（两参合成）。"""
    enabled = bool(_pick(c, "motor_transition_phase_enabled", False))
    amount = max(0.0, min(10.0, _num(c, "motor_transition_phase_amount", 1.0)))
    c.tw._on_clip_motor_transition_phase_changed(c.cid, enabled, amount)


def _apply_precharge(c) -> None:
    """泵预充 → `_on_clip_precharge_changed`（三参合成）。"""
    enabled = bool(_pick(c, "precharge_enabled", False))
    lead_ms = max(0.0, _num(c, "precharge_lead_ms", 1000.0))
    level = max(0, min(255, _num(c, "precharge_level", 13)))
    c.tw._on_clip_precharge_changed(c.cid, enabled, lead_ms, level)


def _apply_record_source(c) -> None:
    c.tw._on_clip_record_output_source_changed(c.cid, bool(c.eff.get("record_output_source_enabled")))


def _apply_hold_mode(c) -> None:
    c.tw._on_clip_hold_mode_changed(c.cid, str(c.eff.get("hold_mode") or "none"))


def _norm_direct_value(key: str, value):
    """dict/list 型直写值的收敛拷贝（防与编译结果共享引用）。"""
    if key in ("fade_type_ratio_in", "fade_type_ratio_out"):
        return dict(value) if isinstance(value, dict) else {}
    if key == "fade_group_direction_overrides":
        return [dict(o) if isinstance(o, dict) else o for o in (value or [])]
    return value


def _direct_pairs(c) -> list:
    """本帧编译里的直写键 → [(attr, 收敛值)]（保持 _DIRECT_WRITE_KEYS 序）。"""
    return [(k, _norm_direct_value(k, c.eff[k])) for k in _DIRECT_WRITE_KEYS if k in c.eff]


def _apply_direct_writes(c) -> None:
    """直写族：三滤网/比例/曲线/优化标记 → 直写+精准刷新；覆盖 → 写+引擎缓存失效。"""
    pairs = _direct_pairs(c)
    dirty = [k for k, v in pairs if getattr(c.clip, k, None) != v]
    if not dirty:
        return
    c.tw._snapshot_for_undo()
    _write_attrs(c, pairs)                       # 真正落盘（值未变不写）
    if "fade_group_direction_overrides" in dirty:
        c.tw._invalidate_engine_cache(c.cid)     # 引擎 `_sequential_fixture_group_data` 消费
    others = [k for k in dirty if k != "fade_group_direction_overrides"]
    if others:
        c.tw._refresh_clips_targeted([c.cid])    # = `_on_clip_advanced_changed` 刷新语义


#: 路由表：触发键集 → applier（顺序敏感：应用边先于过渡族，见 `_apply_edge_absolute`）。
_GROUP_ROUTES = (
    (frozenset({"fade_direction_edge"}), _apply_edge_absolute),
    (frozenset(_DIR_FADE_FAMILY) | {"fade_direction"}, _apply_transition_family),
    (frozenset({"fade_in", "fade_out"}), _apply_fade_durations),
    (frozenset({"seq_delay_in", "seq_delay_out"}), _apply_seq_delays),
    (frozenset({"fade_group_spatial_scope"}), _apply_spatial_scope),
    (frozenset({"fade_group_skip_base_order"}), _apply_skip_base_order),
    (frozenset({"transition_strategy_in", "transition_strategy_out"}), _apply_transition_strategies),
    (frozenset({"playback_rate"}), _apply_playback_rate),
    (frozenset({"smooth_preset", "smooth_preset_name", "smooth_custom_params"}), _apply_smooth),
    (frozenset(_LIGHT_TRACK_KEYS), _apply_light_tracking),
    (frozenset({"motor_force_preset", *_FORCE_PARAM_KEYS}), _apply_motor_force),
    (frozenset({"motor_transition_phase_enabled", "motor_transition_phase_amount"}),
     _apply_motor_phase),
    (frozenset({"precharge_enabled", "precharge_lead_ms", "precharge_level"}), _apply_precharge),
    (frozenset({"record_output_source_enabled"}), _apply_record_source),
    (frozenset({"hold_mode"}), _apply_hold_mode),
    (frozenset(_DIRECT_WRITE_KEYS), _apply_direct_writes),
)


def _apply_light_suppressed(fields: dict, project=None) -> None:
    """开关灯卡 → 引擎滤网（全局会话级；去重：引擎现值未变不重复调用）。

    R173：去重读引擎真值（原模块级 `_LAST_LIGHT_SUPPRESSED` 缓存与会话层
    `studio_session.sync_light_suppressed` 两个写者并存时会过期 → 该置位时被跳过）。"""
    from .program_light_legacy import studio_owns_light
    if LIGHT_SUPPRESS_KEY not in fields or not studio_owns_light(project):
        return
    value = bool(fields[LIGHT_SUPPRESS_KEY])
    try:
        from src.engine.clip_output_filters import light_suppressed, set_light_suppressed
        if light_suppressed() != value:
            set_light_suppressed(value)
    except Exception:
        log.warning("[studio_apply] set_light_suppressed 失败", exc_info=True)


def _effective_fields(fields) -> dict:
    """fields 归一（键转 str）。R173：本模块只下发调用方给出的键 —— 消失键的复位
    （删卡复位默认值 / 覆盖表撤销 / 开关灯派生）归会话层 `studio_session.diff_fields`
    / `ApplyLedger.light_wanted`；在此按"prev 有本次无"推断会把差量下发误判为删除。"""
    return {str(k): v for k, v in dict(fields or {}).items()}


def _warn_unroutable(fields: dict) -> None:
    """路由表外键 → warning（新键必须有归宿，防假接线）。"""
    for key in sorted(k for k in fields if k not in ROUTABLE_FIELD_KEYS):
        log.warning("[studio_apply] 字段「%s」无处理器归宿（假接线防线），已忽略", key)


def _apply_one_clip(tw, cid: str, eff: dict, prev: dict) -> list:
    """单 clip 应用全部命中组；返回**未生效键**列表（如平滑失败，见 `_apply_smooth`）。"""
    clip = _find_clip(tw, cid)
    if clip is None:
        log.warning("[studio_apply] clip 不存在，跳过：%s", str(cid)[:8])
        return []
    c = SimpleNamespace(tw=tw, cid=cid, clip=clip, eff=eff, prev=prev)
    failed: list = []
    for triggers, applier in _GROUP_ROUTES:
        if triggers & eff.keys():
            failed.extend(applier(c) or [])
    return failed


def apply_compiled_fields(tw, clip_ids, fields, prev_fields=None) -> list:
    """Studio 编译 fields → 逐 clip 走 TimelineWidget 真实处理器（W1d2 唯一入口）。

    `fields` 键必须 ∈ `studio_compile.ALLOWED_FIELD_KEYS`（编译器已白名单）= 本次要
    下发的键（面板经 `studio_session.ApplyLedger` 传差量，消失键已换成默认值）；
    `prev_fields` = 该 sheet 已应用的 fields（仅供过渡族/灯跟踪等合成补齐缺键）。
    返回**未生效键**列表（当前仅平滑失败会产生；空列表 = 全部生效）——账本据此
    不记账、下次编辑重试，不影响其它键的已应用结果。
    """
    prev = {str(k): v for k, v in dict(prev_fields or {}).items()}
    eff = _effective_fields(fields)
    if not eff:
        return []
    _warn_unroutable(eff)
    ids = [str(x) for x in (clip_ids or []) if str(x)]
    if not ids or tw is None:
        return []
    failed: list = []
    with _quiet_dialogs(), _single_clip_scope():
        for cid in ids:
            failed.extend(_apply_one_clip(tw, cid, eff, prev))
    # ★ R194/R4-01：TimelineController 只有 `_session`（无公有 session 属性）
    #   ——原 `"session"` 恒取 None，灯所有权门禁被死参数击穿（防御性修复）。
    _apply_light_suppressed(eff, getattr(getattr(tw, "ctrl", None), "_session", None))
    return failed
