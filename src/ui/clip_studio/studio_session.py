# -*- coding: utf-8 -*-
"""Clip Studio 会话层（R173）—— 按 sheet 应用账本 / 单条撤销快照 / 全局开关灯派生。

从 studio_panel 拆出（面板 ≤500 行门禁）。R173 审计缺陷的收口点：

* **prev 串表**：旧面板 `_prev_fields/_last_signature` 是面板全局单槽，多 sheet 交替
  编辑时 A 的已应用值被当成 B 的 prev（过渡族补齐 / 撤覆盖错表）。`ApplyLedger`
  按 sheet 记「已应用 fields」。
* **差量应用**：旧回路每次图变更把整张 sheet 全部字段重放到全部绑定 clip（改一个
  淡入也重跑平滑离线重处理 / 速度重算，且覆盖用户在菜单里改过的其它字段）。账本
  只下发**变化键**；仅挪卡位置（fields 不变）→ 零应用。
* **删卡复位**：已应用而本次消失的键 → 复位 `models.Clip` 默认值（「删复合模块=撤
  覆盖」同一语义扩展到全部字段；平滑复位走 bypass 槽语义）。
* **绑定即应用**：绑定只写 `process_sheet_id` 不应用 → 卡片与 clip 实值不符；
  `ApplyLedger.apply_full` 供绑定时对新 clip 全量应用（prev 为空）。
* **一次编辑一条撤销**：图 + clip 字段联合域快照（`snapshot_studio_edit`），槽内
  各自的 `_snapshot_for_undo` 在应用窗口内静默（`no_inner_snapshots`）。
* **开关灯（全局会话级，R110）**：由账本派生（任一有绑定 clip 的 sheet 已应用禁灯 →
  禁），不走逐 sheet 下发（防 sheet B 的"开灯"覆盖 sheet A 的"禁灯"）；换工程加载
  时开关灯卡一律释放为"开"（白天临时状态，不跨工程/不跨会话，与引擎启动值一致）。
"""
from __future__ import annotations

import dataclasses
import json
import logging

from src.ui.clip_studio.studio_apply import LIGHT_SUPPRESS_KEY, merge_overrides
from src.ui.clip_studio.studio_compile import compile_sheet

log = logging.getLogger(__name__)

__all__ = ["ApplyLedger", "no_inner_snapshots", "release_light_cards",
           "snapshot_studio_edit", "sync_light_suppressed"]

#: 槽内派生键（随主键一起写，不单独复位）。
_NO_RESET_KEYS = frozenset({"smooth_preset_name", "fade_direction"})
#: 复位值与 Clip 默认值不同的键（平滑 "" 需走 bypass 槽语义才清名称/重处理）。
_RESET_VALUES = {"smooth_preset": "bypass"}

_CLIP_DEFAULTS: dict | None = None
_UNKNOWN = object()                     # 非 Clip 字段 / 无默认值（不复位）


def _clip_default(key: str):
    """`models.Clip` 字段默认值（default_factory 现造新对象；取不到 → `_UNKNOWN`）。"""
    global _CLIP_DEFAULTS
    if _CLIP_DEFAULTS is None:
        try:
            from src.core.models import Clip
            _CLIP_DEFAULTS = dict(Clip.__dataclass_fields__)
        except Exception:
            log.warning("[studio_session] models.Clip 默认值不可用", exc_info=True)
            _CLIP_DEFAULTS = {}
    f = _CLIP_DEFAULTS.get(key)
    if f is None:
        return _UNKNOWN
    if f.default is not dataclasses.MISSING:
        return f.default
    if f.default_factory is not dataclasses.MISSING:
        return f.default_factory()
    return _UNKNOWN


def _reset_value(key: str):
    if key in _RESET_VALUES:
        return _RESET_VALUES[key]
    return _clip_default(key)


def _canon(value) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(value)


def _strip_light(fields: dict) -> dict:
    return {k: v for k, v in fields.items() if k != LIGHT_SUPPRESS_KEY}


def diff_fields(fields: dict, prev: dict) -> dict:
    """本次应下发的键：新增/变化键原值 + 消失键复位默认值（开关灯键不在此列）。"""
    out = {k: v for k, v in fields.items()
           if k not in prev or _canon(prev[k]) != _canon(v)}
    for key in prev:
        if key in fields or key in _NO_RESET_KEYS:
            continue
        default = _reset_value(key)
        if default is not _UNKNOWN:
            out[key] = default
    return _strip_light(out)


def _call_apply(apply_fn, clip_ids, eff: dict, prev: dict) -> list:
    if apply_fn is None or not eff:
        return []
    return [str(k) for k in (apply_fn(list(clip_ids), eff, _strip_light(prev)) or [])]


class ApplyLedger:
    """按 sheet 记录「已应用 fields」（取代面板全局单槽 prev/签名）。"""

    def __init__(self) -> None:
        self._applied: dict = {}
        self._full_retry: dict = {}        # 新绑定目标失败，不能由其它目标的页账本抹去

    def applied(self, sid) -> dict:
        return dict(self._applied.get(str(sid)) or {})

    def reset(self, sheets) -> None:
        """重灌（换工程 / 撤销重做）：以各 sheet 当前图的编译结果为已应用基线。"""
        self._applied = {}
        self._full_retry = {}
        for sheet in sheets or []:
            self.record(sheet)

    def record(self, sheet) -> None:
        """只记基线不下发（重灌 / 反向编译新建页：卡值即 clip 现值）；有 error 不记。"""
        res = compile_sheet(sheet)
        if not res.errors:
            self._applied[str(sheet.get("id"))] = merge_overrides(res)

    def forget(self, sid) -> None:
        self._applied.pop(str(sid), None)
        self._full_retry.pop(str(sid), None)

    def apply(self, sid, clip_ids, fields: dict, apply_fn) -> list:
        """图编辑 → 对 sheet 全部绑定 clip 下发差量；无绑定 clip 只记账。返回未生效键。"""
        sid = str(sid)
        fields = dict(fields or {})
        prev = self._applied.get(sid) or {}
        failed = []
        if clip_ids:
            failed = self._apply_bound(sid, clip_ids, fields, prev=prev, apply_fn=apply_fn)
        else:
            self._full_retry.pop(sid, None)
        # 失败键记回旧值（clip 实际仍是旧值）→ 下次编辑差量必含该键 = 自动重试
        applied = {k: v for k, v in fields.items() if k not in failed}
        applied.update({k: prev[k] for k in failed if k in prev})
        self._applied[sid] = applied
        return failed

    def _apply_bound(self, sid, clip_ids, fields, *, prev, apply_fn) -> list:
        """正常目标仍差量应用；绑定失败目标复用完整应用，不重复处理其它目标。"""
        retry = self._full_retry.get(sid, set()) & set(clip_ids)
        regular = [cid for cid in clip_ids if cid not in retry]
        delta = diff_fields(fields, prev)
        failed = _call_apply(apply_fn, regular, delta, prev) if regular else []
        full = dict(delta)
        full.update(_strip_light(fields))
        for cid in clip_ids:
            if cid not in retry:
                continue
            keys = _call_apply(apply_fn, [cid], full, {})
            failed.extend(keys)
            if not keys:
                retry.discard(cid)
        self._full_retry[sid] = retry
        return list(dict.fromkeys(failed))

    def apply_full(self, sid, clip_ids, apply_fn) -> list:
        """绑定新 clip → 按该 sheet 已应用 fields 全量下发（prev 为空，按 clip 现值补齐）。"""
        fields = self._applied.get(str(sid)) or {}
        if not clip_ids:
            return []
        failed = _call_apply(apply_fn, clip_ids, _strip_light(fields), {})
        retry = self._full_retry.setdefault(str(sid), set())
        if failed:
            retry.update(clip_ids)
        else:
            retry.difference_update(clip_ids)
        return failed

    def light_wanted(self, has_clips) -> bool:
        """全局禁灯派生：任一**有绑定 clip** 的 sheet 已应用禁灯 → True。"""
        return any(bool(f.get(LIGHT_SUPPRESS_KEY)) and has_clips(sid)
                   for sid, f in self._applied.items())


def sync_light_suppressed(value: bool, project=None) -> None:
    """全局禁灯落引擎（值未变不重复置位）。"""
    from .program_light_legacy import studio_owns_light
    if not studio_owns_light(project):
        return
    try:
        from src.engine.clip_output_filters import light_suppressed, set_light_suppressed
        if light_suppressed() != bool(value):
            set_light_suppressed(bool(value))
    except Exception:
        log.warning("[studio_session] set_light_suppressed 失败", exc_info=True)


def _light_nodes(state: dict):
    """state 内全部开关灯卡节点（跨 sheet）。"""
    for sheet in (state or {}).get("sheets") or []:
        nodes = (sheet.get("graph") or {}).get("nodes") or []
        yield from (n for n in nodes if str(n.get("key", "")) == "light_switch")


def release_light_cards(state: dict, project=None) -> int:
    """换工程加载：全部开关灯卡释放为"开"（R110 临时状态不跨工程）；返回改动卡数。"""
    from .program_light_legacy import studio_owns_light
    if not studio_owns_light(project):
        return 0
    n = 0
    for node in _light_nodes(state):
        params = node.setdefault("params", {})
        if params.get("mode", "on") != "on":
            params["mode"] = "on"
            n += 1
    return n


def snapshot_studio_edit(pm) -> bool:
    """一次 Studio 编辑 = 一条撤销：时间线域（clip 字段）∪ Studio 图域，编辑前压栈。"""
    try:
        from src.core.project_manager import DOMAIN_CLIP_STUDIO, DOMAIN_TIMELINE
        from src.ui.timeline.timeline_perf import timeline_gesture_snapshot
        return timeline_gesture_snapshot(pm, DOMAIN_TIMELINE | DOMAIN_CLIP_STUDIO)
    except Exception:
        log.warning("[studio_session] 撤销快照失败", exc_info=True)
        return False


class no_inner_snapshots:
    """应用窗口内静默 TimelineWidget 槽自带的 `_snapshot_for_undo`（实例属性遮蔽，
    with 结束恢复）：编辑前已由 `snapshot_studio_edit` 压过一条联合域快照。"""

    def __init__(self, tw) -> None:
        self._tw = tw
        self._had = False
        self._saved = None

    def __enter__(self):
        tw = self._tw
        if tw is not None and hasattr(tw, "__dict__"):
            self._had = "_snapshot_for_undo" in tw.__dict__
            self._saved = tw.__dict__.get("_snapshot_for_undo")
            tw._snapshot_for_undo = _noop_snapshot
        return self

    def __exit__(self, *_exc):
        tw = self._tw
        if tw is None or not hasattr(tw, "__dict__"):
            return False
        if self._had:
            tw._snapshot_for_undo = self._saved
        else:
            tw.__dict__.pop("_snapshot_for_undo", None)
        return False


def _noop_snapshot(*_a, **_k) -> None:
    return None
