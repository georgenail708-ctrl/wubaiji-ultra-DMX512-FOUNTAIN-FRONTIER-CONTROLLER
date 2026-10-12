# -*- coding: utf-8 -*-
"""★ R183 T5：调度台 **Grand Master 总控**（滑杆 + 0-100% 乘性总输出）。

语义：`setValue` → `m_fraction = value/255`
+ `engine/src/universe.cpp:526-538`（`applyGM`：Limit 取 min / Reduce 乘性四舍五入）。
本仓取 Reduce（乘性压暗，行业默认）+ 全场通道（AllChannels：总闸不该漏掉任何通道）。

插入点（"最小侵入点"核实结论）：`NetworkManager._prepare_batch_frame`（混音器批量
唯一末级）与 `_send_to_hw`（单帧/直发末级）——两处都是"即将下发硬件的字节"的最后
一站，且在**混音与快照之后**：监控区/取值路径仍看到编程值，总控只改真正下发的字节
（这正是 把 GM 放在 `Universe::writeValue` 最后的原因）。

持久化用**QSettings**（与存储区分界 `split_persistence`、吸附档同一套偏好机制），
刻意**不写** `Project.dispatch_state`：`main_window._on_dispatch_cache_changed` 会
用 `{**store.to_dict(), "releases": ...}` **整体替换**该字段（增量保存路径），
任何挂在里面的自定义键都会被下一次保存静默抹掉 —— 总控是"控台级"控制而非节目数据，
放偏好里语义也更正确。
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSlider, QWidget

from .. import qascade_theme as _th

_log = logging.getLogger(__name__)

_QS_ORG, _QS_APP, _QS_KEY = "Qascade", "MainWindow", "dispatch/grand_master_pct"

#: 滑杆满量程（百分比整数：0..100，的 0..255 在 UI 上折成百分比）
SLIDER_MAX = 100


def load_gm_percent() -> int:
    """读持久化总控百分比（QSettings 不可用 → 100，即不压暗）。"""
    try:
        from PyQt6.QtCore import QSettings
        raw = QSettings(_QS_ORG, _QS_APP).value(_QS_KEY, 100)
        return max(0, min(SLIDER_MAX, int(raw)))
    except Exception:
        return 100


def save_gm_percent(pct: int) -> None:
    """写持久化总控百分比（失败静默：偏好存不进去不影响功能）。"""
    try:
        from PyQt6.QtCore import QSettings
        QSettings(_QS_ORG, _QS_APP).setValue(_QS_KEY, max(0, min(SLIDER_MAX, int(pct))))
    except Exception:
        pass


def build_grand_master(panel) -> QWidget:
    """构建总控控件（滑杆 + 百分比读数）并返回；装配进面板顶栏。"""
    box = QWidget(panel)
    box.setObjectName("dispatchGM")
    box.setStyleSheet(f"#dispatchGM{{background:{_th.BG_BUTTON};"
                      f"border:1px solid {_th.BORDER_NORMAL};border-radius:4px;}}")
    lay = QHBoxLayout(box)
    lay.setContentsMargins(6, 0, 6, 0)
    lay.setSpacing(4)
    lbl = QLabel("总控", box)
    lbl.setStyleSheet(f"color:{_th.TEXT_SECONDARY};font-size:10px;background:transparent;")
    lay.addWidget(lbl)
    slider = QSlider(Qt.Orientation.Horizontal, box)
    slider.setRange(0, SLIDER_MAX)
    slider.setFixedWidth(96)
    slider.setFixedHeight(18)
    slider.setToolTip("Grand Master：0-100% 乘性总输出（只压真正下发的 DMX，不改编程值）\n"
                      "双击滑杆复位到 100%")
    lay.addWidget(slider)
    read = QLabel("100%", box)
    read.setFixedWidth(34)
    read.setStyleSheet(f"color:{_th.TEXT_PRIMARY};font-size:10px;"
                       "background:transparent;")
    lay.addWidget(read)
    # 双击复位（的 GM 滑杆也有"回到满值"习惯动作；双击最省 UI 面积）
    slider.mouseDoubleClickEvent = lambda e, s=slider: _reset_gm(s)
    panel._gm_slider = slider
    panel._gm_read = read
    slider.valueChanged.connect(lambda v, p=panel: _on_gm_changed(p, int(v)))
    slider.setValue(load_gm_percent())          # 触发一次 _on_gm_changed（含 NM 落地）
    _on_gm_changed(panel, slider.value())       # 值未变时 valueChanged 不发 → 显式同步一次
    return box


def _reset_gm(slider) -> None:
    """双击复位到 100%。"""
    slider.setValue(SLIDER_MAX)


def _on_gm_changed(panel, pct: int) -> None:
    """滑杆变化 → 读数字/写 NM/持久化（NM 未注入时只更新 UI）。"""
    try:
        panel._gm_read.setText(f"{int(pct)}%")
    except Exception:
        _log.debug("总控读数更新失败", exc_info=True)
    try:
        save_gm_percent(int(pct))
    except Exception:
        _log.debug("总控百分比持久化失败", exc_info=True)
    _apply_to_nm(panel, int(pct))


def _nm_of(panel):
    """取 NetworkManager：面板注入的 provider 优先，退路进程内单例（取不到 None）。

    退路是必需的：`install_grand_master` 在面板构建时就装控件，而 `set_services`
    （注入 `_nm_provider`）可能晚于它；没有退路时总控在"打开工程但没点过调度台"
    的窗口里是空操作。
    """
    prov = getattr(panel, "_nm_provider", None)
    try:
        nm = prov() if callable(prov) else None
        if nm is not None:
            return nm
    except Exception:
        nm = None
    try:
        from ...network.network_manager import NetworkManager
        return NetworkManager.instance()
    except Exception:
        return None


def _apply_to_nm(panel, pct: int) -> None:
    """把百分比落到 NetworkManager（`set_grand_master`；失败留痕不抛）。"""
    from ...network.grand_master import gain_from_percent
    nm = _nm_of(panel)
    fn = getattr(nm, "set_grand_master", None) if nm is not None else None
    if not callable(fn):
        return                       # 面板未接 NM（单测直连）→ 只保留 UI 值
    try:
        fn(gain_from_percent(pct))
    except Exception:
        _log.debug("总控落地 NM 失败 pct=%s", pct, exc_info=True)


def install_grand_master(panel) -> None:
    """把总控控件装进面板顶栏（提示文案左侧；幂等；无顶栏则静默跳过）。"""
    if getattr(panel, "_gm_slider", None) is not None:
        return
    try:
        parent_bar = _toolbar_of(panel)
        if parent_bar is None:
            return
        widget = build_grand_master(panel)
        lay = parent_bar.layout()
        if lay is not None:
            lay.insertWidget(max(0, lay.count() - 1), widget)   # 插到末尾提示之前
    except Exception:
        _log.debug("总控控件装配失败", exc_info=True)


def _toolbar_of(panel):
    """取面板顶栏 QWidget（按 objectName 找，取不到 → None）。"""
    try:
        for child in panel.findChildren(QWidget):
            if child.objectName() == "dispatchBar":
                return child
    except Exception:
        pass
    return None


def install_grand_master_to_panel(cls) -> None:
    """把 T5 总控接进面板**构建**流程（幂等；包装 `_build_toolbar`）。

    `DispatchPanel._build_toolbar` 在架构基线里是 105 行 / CC 6 的既有函数
    （门禁只许缩小），故不改它本尊 —— 用包装在装配末尾追加总控控件
    （与 R179 `_setup_timeline_tool_extensions` 同款"另开挂点"纪律）。
    实现放本模块而非 `dispatch_panel`：后者已是 500 行上限的既有文件。
    """
    if getattr(cls, "_gm_installed", False):
        return
    cls._gm_installed = True
    orig = cls._build_toolbar

    def _build_toolbar_with_gm(self):     # noqa: N802 - 保留原方法名语义
        bar = orig(self)
        try:
            install_grand_master(self)
        except Exception:
            _log.debug("总控挂载失败（面板其余功能不受影响）", exc_info=True)
        return bar

    cls._build_toolbar = _build_toolbar_with_gm


__all__ = ["SLIDER_MAX", "load_gm_percent", "save_gm_percent",
           "build_grand_master", "install_grand_master",
           "install_grand_master_to_panel"]
