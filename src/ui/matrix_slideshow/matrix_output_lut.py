# -*- coding: utf-8 -*-
"""matrix_output_lut.py — 每分区输出 Gamma LUT（深查②三.Matrix #8 P2）。

用途：混装灯一致性 —— 大楼不同层/段常用不同型号灯珠，同一 255 观感不一；
按**水平分区**（设备 pos_y 分带）各挂一张 256 项 gamma 查表，输出链在
写层/预览前查表，预览与 NM 输出同一曲线（与 output_shaper 同哲学）。

纯逻辑、无 Qt；`open_gamma_dialog` 提供非模态设置弹层（改动即时生效，
持久化走 `MatrixSlideshowConfig.output_gamma`）。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

#: 分区数上下限
ZONE_MIN, ZONE_MAX = 1, 8
#: gamma 参数范围
GAMMA_MIN, GAMMA_MAX = 0.05, 4.0

#: 工程文件默认（enabled=False ⇒ 旧工程行为逐字节不变）
ZONE_GAMMA_DEFAULTS: Dict[str, object] = {
    "enabled": False,
    "zone_count": 2,
    "gammas": [1.0, 1.0],
}


def _clamp_gamma(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 1.0
    if f != f:  # NaN
        return 1.0
    return min(GAMMA_MAX, max(GAMMA_MIN, f))


def normalize_zone_gamma_dict(d: Optional[dict]) -> Dict[str, object]:
    """任意 dict → 合法 output_gamma dict（保旧工程兼容）。"""
    out: Dict[str, object] = dict(ZONE_GAMMA_DEFAULTS)
    for k, v in (d or {}).items():
        if k in out:
            out[k] = v
    try:
        zc = int(out.get("zone_count", 2))
    except (TypeError, ValueError):
        zc = 2
    zc = min(ZONE_MAX, max(ZONE_MIN, zc))
    raw = out.get("gammas") or []
    gammas = [1.0] * zc
    for i in range(zc):
        try:
            gammas[i] = _clamp_gamma(raw[i])
        except (IndexError, TypeError):
            gammas[i] = 1.0
    out["enabled"] = bool(out.get("enabled"))
    out["zone_count"] = zc
    out["gammas"] = gammas
    return out


def build_gamma_lut(gamma: float) -> np.ndarray:
    """gamma → 256 项 uint8 查表（v' = (v/255)^(1/gamma)·255；gamma=1 恒等）。"""
    g = _clamp_gamma(gamma)
    base = np.arange(256, dtype=np.float32) / 255.0
    if abs(g - 1.0) <= 1e-4:
        return np.arange(256, dtype=np.uint8)
    lut = np.power(base, 1.0 / g) * 255.0
    return np.clip(np.round(lut), 0.0, 255.0).astype(np.uint8)


def build_zone_luts(gammas: Sequence[float]) -> List[np.ndarray]:
    return [build_gamma_lut(g) for g in gammas]


def zone_assignment(fixtures: Sequence[dict],
                    zone_count: int) -> Tuple[np.ndarray, Dict[str, int]]:
    """设备列表 → (与列表同序的分区号数组, {fid: 分区号})。

    分区按 pos_y（顶=0）划分；fid 缺失的设备保留空 id 键（不会命中）。
    """
    ids: List[str] = []
    pos: List[float] = []
    for f in fixtures or []:
        ids.append(str(f.get("id", "") or ""))
        pos.append(float(f.get("pos_y", 0.5) or 0.0))
    arr = zone_index_for_positions(pos, zone_count)
    return arr, dict(zip(ids, (int(z) for z in arr)))


def zone_index_for_positions(pos_y: Sequence[float],
                             zone_count: int) -> np.ndarray:
    """pos_y 列表 → 分区号（顶部=0；单区全 0）。"""
    zc = min(ZONE_MAX, max(ZONE_MIN, int(zone_count)))
    n = len(pos_y)
    if n == 0:
        return np.zeros(0, dtype=np.int32)
    arr = np.asarray([float(v) for v in pos_y], dtype=np.float32)
    lo, hi = float(arr.min()), float(arr.max())
    if hi - lo < 1e-9 or zc <= 1:
        return np.zeros(n, dtype=np.int32)
    # pos_y 大 = 靠下 → 分区号从顶（y 小）往底递增
    z = ((arr - lo) / (hi - lo) * zc).astype(np.int32)
    return np.minimum(z, zc - 1).astype(np.int32)


def apply_zone_gamma_values(values: np.ndarray,
                            zone_idx: np.ndarray,
                            luts: Sequence[np.ndarray]) -> np.ndarray:
    """uint8 值数组按分区查表（zone 缺失/越界原样保留）。"""
    vals = np.asarray(values)
    if vals.size == 0 or not luts or zone_idx.size != vals.size:
        return vals
    out = vals.copy()
    for z, lut in enumerate(luts):
        sel = zone_idx == z
        if not np.any(sel):
            continue
        src = vals[sel].astype(np.int32)
        np.clip(src, 0, 255, out=src)
        out[sel] = lut[src]
    return out.astype(vals.dtype, copy=False)


def apply_zone_gamma_weights(weights: Dict[str, float],
                             fid_zone: Dict[str, int],
                             luts: Sequence[np.ndarray]) -> Dict[str, float]:
    """预览权重 dict 按分区查表（与 apply_zone_gamma_values 同曲线）。"""
    if not luts or not weights:
        return weights
    out: Dict[str, float] = {}
    for fid, w in weights.items():
        z = fid_zone.get(str(fid))
        if z is None or z < 0 or z >= len(luts):
            out[fid] = w
            continue
        lut = luts[z]
        v = int(round(min(1.0, max(0.0, float(w))) * 255.0))
        out[fid] = float(lut[v]) / 255.0
    return out


def _gamma_spin_rows(form, spins: List, zc: int, gammas: Sequence[float],
                     on_change) -> None:
    """重建每分区 gamma 旋钮行（先删旧行，再建新行）。

    C5 修（截图 13-18-52 行距过大）+ 顺带修真 bug：修前每行包一层
    QWidget+QHBoxLayout 且未清边距 → 平台默认 ~11px 上下边距让行距暴涨；并且
    只删旋钮不删行 → **每改一次分区数，旧行的「第 N 区:」标签残留累积**。
    现在直接 ``addRow(标签, 旋钮)``，重建时删掉第 0 行（分区数）之后的全部行。
    """
    from PyQt6.QtWidgets import QDoubleSpinBox
    while form.rowCount() > 1:
        form.removeRow(form.rowCount() - 1)
    spins.clear()
    for i in range(zc):
        sp = QDoubleSpinBox()
        sp.setRange(GAMMA_MIN, GAMMA_MAX)
        sp.setDecimals(2)
        sp.setSingleStep(0.05)
        sp.setValue(float(gammas[i]) if i < len(gammas) else 1.0)
        sp.setToolTip(f"第 {i + 1} 区（从上往下第 {i + 1} 带）gamma；1.0=不校正")
        sp.valueChanged.connect(on_change)
        form.addRow(f"第 {i + 1} 区:", sp)
        spins.append(sp)


def _rebuild_from_combo(form, spins: List, zone_combo, engine, on_change) -> None:
    """按分区下拉当前值重建旋钮行（helper；D5-2 读引擎真源——修前用弹层
    打开时的 cfg 快照，用户已改未提交的 gamma 会被旧值覆盖并静默提交）。"""
    zc = int(zone_combo.currentData() or 2)
    gammas = normalize_zone_gamma_dict(
        getattr(engine.config, "output_gamma", None))["gammas"]
    _gamma_spin_rows(form, spins, zc, gammas, on_change)


def _apply_gamma_cfg(panel, engine, chk, zone_combo, spins, *_a) -> None:
    """弹层控件 → config.output_gamma（即时生效 + 置脏 + 同步像素引擎）。"""
    engine.config.output_gamma = normalize_zone_gamma_dict({
        "enabled": bool(chk.isChecked()),
        "zone_count": int(zone_combo.currentData() or 2),
        "gammas": [float(s.value()) for s in spins],
    })
    sync = getattr(panel, "_sync_output_gamma", None)
    if callable(sync):
        try:
            sync()
        except Exception:
            logger.debug("matrix: sync output gamma failed", exc_info=True)
    try:
        from .matrix_slideshow_panel import _mark_panel_dirty
        _mark_panel_dirty(panel)
    except Exception:
        logger.debug("matrix: mark dirty after gamma change failed", exc_info=True)


def open_gamma_dialog(panel) -> None:
    """「输出 Gamma」非模态弹层：启用 + 分区数 + 每分区 gamma（改动即时生效）。"""
    from functools import partial

    from PyQt6.QtWidgets import (
        QCheckBox, QComboBox, QDialog, QFormLayout, QLabel, QVBoxLayout,
    )
    engine = getattr(panel, "_engine", None)
    if engine is None:
        return
    old = getattr(panel, "_zgd_dialog", None)
    if old is not None and old.isVisible():
        old.raise_()
        old.activateWindow()
        return
    cfg = normalize_zone_gamma_dict(getattr(engine.config, "output_gamma", None))

    dlg = QDialog(panel)
    dlg.setWindowTitle("输出 Gamma（每分区查表）")
    dlg.setModal(False)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(12, 12, 12, 12)   # C5：02 §1 间距（面板内边距 12 / 组距 8）
    lay.setSpacing(8)

    chk = QCheckBox("启用分区 Gamma（预览与输出同曲线）")
    chk.setChecked(bool(cfg.get("enabled")))
    lay.addWidget(chk)

    form = QFormLayout()
    form.setVerticalSpacing(8)
    zone_combo = QComboBox()
    for z in range(ZONE_MIN, ZONE_MAX + 1):
        zone_combo.addItem(f"{z} 区（按高度分带）", z)
    zone_combo.setCurrentIndex(max(0, zone_combo.findData(int(cfg.get("zone_count", 2)))))
    form.addRow("分区数:", zone_combo)
    lay.addLayout(form)

    spins: List = []
    on_change = partial(_apply_gamma_cfg, panel, engine, chk, zone_combo, spins)
    zone_combo.currentIndexChanged.connect(
        lambda _i: (_rebuild_from_combo(form, spins, zone_combo, engine, on_change),
                    _apply_gamma_cfg(panel, engine, chk, zone_combo, spins)))
    chk.toggled.connect(on_change)
    hint = QLabel(
        "按设备纵坐标把矩阵分成若干水平带，每带独立 gamma 查表（256 级）。\n"
        "混装灯时给偏暗的区调低 gamma（如 0.8）、偏亮的调高（如 1.2）。")
    from ..qascade_theme import TEXT_TERTIARY
    hint.setStyleSheet(f"color:{TEXT_TERTIARY};font-size:11px;")
    lay.addWidget(hint)
    dlg.finished.connect(lambda _r: setattr(panel, "_zgd_dialog", None))
    dlg.finished.connect(dlg.deleteLater)
    panel._zgd_dialog = dlg
    _rebuild_from_combo(form, spins, zone_combo, cfg, on_change)
    dlg.show()
