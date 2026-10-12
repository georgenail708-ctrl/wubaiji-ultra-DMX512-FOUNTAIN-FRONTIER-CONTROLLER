# -*- coding: utf-8 -*-
"""ProgrammerPanel 的生命周期 / 构建碎片（从上帝类里搬出的模块级 helper）。

搬移动机（R178 架构门禁，CLAUDE.md §10.1/§10.2）：

  * ``ProgrammerPanel.closeEvent`` 是 R17x 新增的**唯一**新方法，把上帝类方法数
    从 161 顶到 162 → `[上帝类新增方法]` ERROR。Qt 生命周期回调不能删，于是改成
    装在本模块的 :class:`ProgrammerCloseFilter` 上的事件过滤器：定时器/输出照旧
    清理，但 ProgrammerPanel 类体上不再新增方法。
  * ``ProgrammerPanel._build_ui`` 被 R173 的 ``status_feedback`` 转发挤到 101 行
    → `[复杂度]` 新超限。滚动区构建段原样搬到 :func:`build_programmer_scroll`。
  * ``ChannelTypeRow._apply_channel_range`` 被 R173 的状态栏反馈撑到 79 行（基线
    75）→ `[复杂度恶化]`。批次发信 + 状态反馈原样搬到
    :func:`emit_range_batch_status`。

本模块只做"搬移"，不改任何数值常量、不改任何顺序。对 ``src.ui.programmer_panel``
的引用一律**函数内延迟 import**（该模块在顶层 import 本模块，顶层反向 import 会
成环）。
"""
from __future__ import annotations

from typing import Dict

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QFrame, QScrollArea


def cleanup_programmer_resources(panel) -> None:
    """停掉面板自身与行级的定时器并释放输出（原 ``closeEvent`` 正文）。

    ★ Resource leak fix: _live_output_timer 与 _keepalive_timer 在面板销毁后仍会
      触发，可能访问已删除的 C++ 对象，或用定时器引用把 widget 吊着（内存泄漏）。
    ★ 面板运行期被反复创建/销毁（多场景工作流）时这步是必须的。
    """
    from src.ui.programmer_panel import _drain_row_timers
    try:
        panel.clear_live_output()  # Stops both timers + clears layers
    except Exception:
        pass
    try:
        _drain_row_timers(panel)  # Stop row-level timers
    except Exception:
        pass


class ProgrammerCloseFilter(QObject):
    """把"关闭即清理"挂成事件过滤器，避免给上帝类新增 ``closeEvent`` 方法。

    过滤器一律返回 ``False``（不吞事件）→ 面板继续走 ``QWidget.closeEvent``
    默认处理，与搬移前 ``super().closeEvent(event)`` 的行为一致。
    """

    def __init__(self, panel) -> None:
        super().__init__(panel)
        self._panel = panel

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt 回调命名
        if event is not None and event.type() == QEvent.Type.Close:
            cleanup_programmer_resources(self._panel)
        return False


def install_programmer_close_filter(panel) -> ProgrammerCloseFilter:
    """在面板上装好关闭清理过滤器，并挂强引用防 GC。"""
    close_filter = ProgrammerCloseFilter(panel)
    panel.installEventFilter(close_filter)
    panel._close_filter = close_filter
    return close_filter


def finalize_programmer_init(panel) -> None:
    """``ProgrammerPanel.__init__`` 的收尾段（顺序与搬移前逐行一致）。"""
    panel._build_ui()
    panel._refresh_fixtures()
    try:
        panel._pm.project_changed.connect(panel._refresh_fixtures)
    except Exception:
        pass
    panel._init_joystick_binding()
    install_programmer_close_filter(panel)


def build_programmer_scroll(panel) -> QScrollArea:
    """构建包住操作台主体的滚动区（原 ``_build_ui`` 内联段，逐行搬移）。"""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setMinimumSize(0, 0)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    # 滚动条外观由全局 QSS 提供；滚动区保持透明无边框
    scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
    scroll.setWidget(panel._main_body_widget)
    return scroll


def emit_range_batch_status(
    row, batch: Dict[str, Dict[str, int]], layer: str, ch_type: str,
) -> None:
    """发批次层变更 + R173 状态栏反馈（原 ``_apply_channel_range`` 尾段）。"""
    if not batch:
        return
    row.fixture_layer_batch_changed.emit(batch, layer)
    # ★ R173: 发送状态反馈到状态栏
    from src.ui.programmer_panel import _channel_type_label
    affected_count = len(batch)
    ch_label = _channel_type_label(ch_type)
    row.status_feedback.emit(f"{ch_label} 已应用到 {affected_count} 台设备", 3000)


# ── R188：「Full」批量开启（方案A·边界合并）─────────────────────────────────
#
# 搬移动机：``ChannelTypeRow.full_open`` 由逐格 ``_set_value`` 改为批量体后
# 行数/CC 触顶（75 行 CC~19 > 门禁）；按工程规范把批量 helper 放本模块。
# 语义与逐格路径**逐字节等价**（详见 ``apply_full_open`` 注释），只合并
# 信号通知边界：逐格 emit → 行末一次 ``fixture_layer_batch_changed``。

def _full_open_write_cell(row, fader_layer, fid: str, ct: str, batch) -> None:
    """写入一格：fader 层=255 + ``_values/_marked`` 无条件写 + batch 聚合。

    · **不做同值早退**：旧路径 `_set_value` → `_set_layer_value` 的
      `old == value and not (emit_value or emit_layer)` 分支在默认双 emit 下
      永不命中 → 恒写；本 helper 照此无条件写。
    · merged 口径与 `restore_static_snapshot`/`_apply_channel_range` 相同：
      `int(round(_compute_merged_value(fid, ct)))`（其它层可能有值）。
    """
    fader_layer.setdefault(fid, {})[ct] = 255
    merged = int(round(row._compute_merged_value(fid, ct)))
    row._values.setdefault(fid, {})[ct] = merged
    row._marked.setdefault(fid, {})[ct] = merged
    batch.setdefault(fid, {})[ct] = 255


def _full_open_row(row, batch) -> list:
    """写一行全部支持的 (fi, ct)；返回本行实际写过的通道类型序（供轨迹补齐）。

    作用域与旧实现完全一致：`ct in row._ch_types` × `_fixtures`，
    且仅 `_fixture_supports_logical_channel(fd, ct)` 为真时写入。
    """
    from src.ui.programmer_panel import (
        _fixture_supports_logical_channel,
        _is_light_channel,
    )
    fader_layer = row._layers.setdefault("fader", {})
    wrote_cts: list = []
    touched_light = False
    for ct in row._ch_types:
        n = 0
        for fi in row._fixtures:
            fd = row._fixture_defs.get(fi.def_id)
            if not _fixture_supports_logical_channel(fd, ct):
                continue
            _full_open_write_cell(row, fader_layer, fi.id, ct, batch)
            n += 1
        if n:
            wrote_cts.append(ct)
            if _is_light_channel(ct):
                touched_light = True
    if touched_light:
        row._bump_vd_color_version()
    return wrote_cts


def apply_full_open(row) -> None:
    """``ChannelTypeRow.full_open`` 的批量体（R188 方案A·边界合并）。

    与旧逐格路径的等价性（对拍见 R188 台账）：
      · 写入集合不变（支持判定/遍历顺序原样）；
      · fader/_values/_marked 末态逐格相同（含 merged 计算）；
      · 色通道版本号由逐格递增收敛为行末一次（只需失效，净效果≥+1）；
      · 录制轨迹：旧路径逐台发 `programmer|strip|{ct}`（参数格式见
        `_on_value_changed`），同 key 同值被 ControlTrajectoryBuffer 抽稀
        → 每 ct 净效果至多一条；此处按 ct 各补 1 条 255（=旧路径最终值）。
      · 折叠 body 置 `_body_needs_rebuild`（同 restore_static_snapshot 范式）。
    """
    batch: Dict[str, Dict[str, int]] = {}
    wrote_cts = _full_open_row(row, batch)
    if not (row._expanded and row._body.isVisible()):
        row._body_needs_rebuild = True
    for ct in wrote_cts:
        try:
            row.controlTrajectoryEvent.emit(f"programmer|strip|{ct}", 255)
        except Exception:
            pass
    if batch:
        row.fixture_layer_batch_changed.emit(batch, "fader")

