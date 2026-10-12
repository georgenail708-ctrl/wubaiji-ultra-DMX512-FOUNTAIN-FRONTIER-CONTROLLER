# -*- coding: utf-8 -*-
"""R10：火车卡片「链式编辑」锁（改任一卡 → 同步全部选中卡）。

深查③ R10：卡片参数逐个调（`_set_train_field`），而批量能力只有油门/模式
（`_fleet_apply_*`）。本模块给「运行控制」区注入一枚开关：开启后，在任一卡片
上改的字段（车厢/车长/锁链/半径/最大速度）会同步写到车队树里**全部选中**的火车；
关闭时行为与修前逐字一致（零回归）。

本模块只做「同步 + 卡片控件回填」；字段写入仍走面板 `_set_train_field` 唯一
咽喉（引擎重建/重挂/撤销口径不重复实现）。
"""
from __future__ import annotations

import logging

from PyQt6.QtWidgets import QCheckBox

log = logging.getLogger(__name__)

#: 可链式同步的卡片字段（与 `_build_train_card` 的 specs 同源）
CHAIN_FIELDS = ("car_count", "car_length", "chain_length", "radius",
                "base_speed")

#: 同步时不做二次链式（防递归深度）
_SYNCING = "_chain_edit_syncing"


def chain_edit_enabled(panel) -> bool:
    """链式编辑锁是否开启（控件未建 → 属性缺省 False）。"""
    return bool(getattr(panel, "_chain_edit_lock", False))


def set_chain_edit_enabled(panel, on: bool) -> None:
    panel._chain_edit_lock = bool(on)


def chain_source_tid(panel, tid: str) -> str:
    """源车 id 归一（改分队的卡片时代理到主列，避免只同步半列车）。"""
    return str(tid)


def chain_target_tids(panel, source_tid: str) -> list:
    """链式目标 = 车队树选中集（去源车）；无选中 → 空表（不动任何车）。"""
    if not chain_edit_enabled(panel):
        return []
    try:
        picked = [str(t) for t in panel._fleet_selected_train_ids()]
    except Exception:
        log.debug("chain edit: fleet selection read failed", exc_info=True)
        return []
    return [t for t in picked if t and t != str(source_tid)]


def _card_value(panel, tid: str, field: str):
    """读某卡卡片控件当前值（读不到给 None → 跳过该车）。"""
    spin = (getattr(panel, "_param_spins", None) or {}).get(tid, {}).get(field)
    if spin is None:
        return None
    try:
        return spin.value()
    except Exception:
        return None


def _fill_slider(slider, spec, value) -> None:
    """滑块回填：量程 = 默认上限与当前值较大者（与 _add_dual_spec 同口径）。"""
    if slider is None or spec is None:
        return
    lo, hi = float(spec[0]), max(float(spec[1]), float(value))
    span = hi - lo
    slider.blockSignals(True)
    slider.setValue(0 if span <= 0
                    else int(round((float(value) - lo) / span * 1000.0)))
    slider.blockSignals(False)


def _fill_label(label, spec, value) -> None:
    """值标签回填（小数位随字段规格；无规格按整数）。"""
    if label is None:
        return
    dec = int(spec[2]) if spec is not None else 0
    label.setText(f"{float(value):.{dec}f}" if dec > 0
                  else str(int(round(float(value)))))


def _fill_card(panel, tid: str, field: str, value) -> None:
    """把值回填到某卡（滑块/数值框/值标签三件套，全部屏蔽信号防回写风暴）。"""
    spins = (getattr(panel, "_param_spins", None) or {}).get(tid, {})
    spin = spins.get(field)
    if spin is None:
        return
    spec = (getattr(panel, "_param_specs", None) or {}).get(tid, {}).get(field)
    try:
        spin.blockSignals(True)
        spin.setValue(value)
        spin.blockSignals(False)
        _fill_slider((getattr(panel, "_param_sliders", None) or {})
                     .get(tid, {}).get(field), spec, value)
        _fill_label((getattr(panel, "_param_value_labels", None) or {})
                    .get(tid, {}).get(field), spec, value)
    except Exception:
        log.debug("chain edit: card fill failed (tid=%s)", tid, exc_info=True)


def sync_chain_edit(panel, source_tid: str, field: str, value) -> int:
    """把源车的一次字段改动同步到全部选中卡（返回同步车数）。

    调用点 = 面板 `_set_train_field` 尾部（唯一咽喉）。加锁/无选中/字段不在
    白名单/正在同步中 → 立即返回 0（零副作用）。
    """
    if field not in CHAIN_FIELDS or not chain_edit_enabled(panel):
        return 0
    if getattr(panel, _SYNCING, False):
        return 0
    targets = chain_target_tids(panel, source_tid)
    if not targets:
        return 0
    setattr(panel, _SYNCING, True)
    done = 0
    try:
        for tid in targets:
            tr = getattr(panel, "_engine", None)._trains.get(tid)
            if tr is None:
                continue
            try:
                panel._set_train_field(tid, field, value)
            except Exception:
                log.debug("chain edit: write failed (tid=%s)", tid, exc_info=True)
                continue
            _fill_card(panel, tid, field, float(getattr(tr, field, value)))
            done += 1
    finally:
        setattr(panel, _SYNCING, False)
    return done


def inject_chain_edit_toggle(panel, tools_layout) -> None:
    """在「运行控制」区注入「链式编辑」开关（面板源码零新逻辑）。"""
    if tools_layout is None or getattr(panel, "_chain_edit_injected", False):
        return
    panel._chain_edit_injected = True
    panel._chain_edit_lock = False
    box = QCheckBox("链式编辑（同步选中卡）")
    box.setToolTip("开启后：在任一火车卡片上改的车厢/车长/锁链/半径/最大速度，"
                   "会同步写到车队树里所有选中的火车（未选中任何车时不生效）")
    box.toggled.connect(lambda on, p=panel: set_chain_edit_enabled(p, bool(on)))
    tools_layout.addWidget(box)


__all__ = ["CHAIN_FIELDS", "chain_edit_enabled", "inject_chain_edit_toggle",
           "set_chain_edit_enabled", "sync_chain_edit"]
