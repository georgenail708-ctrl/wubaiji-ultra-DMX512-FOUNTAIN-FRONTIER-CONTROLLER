# -*- coding: utf-8 -*-
"""Patch 配接动作（R183/F2 · 深查③ P2/P9）：从冻结面板搬出的 `_do_patch` 本体。

搬运理由：`patch_panel._do_patch` 在架构基线里已顶格（79 行 / CC 19），而 P2 的
"配接前冲突显式警告"必须加在写入之前 —— 直接在原函数里加分支即门禁 ERROR。
故本体整体搬到本模块（面板方法降为 2 行委托），顺带把内联的重叠扫描换成
`patch_guard` 的具名函数，并按门禁硬限拆分（新函数 ≤100 行 / CC ≤8 / 位置参数 ≤5）。

**写入语义零变化**（任务书硬约束）：仍然是"先移除重叠旧设备、再写入新设备"，
变化只有一处 —— 移除前把清单**弹给用户确认**（不再静默丢配接）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from ..core.models import FixtureInstance
from .patch_guard import (fixtures_text, normalize_ranges, overlapping_fixtures)


@dataclass
class _Request:
    """一次配接请求（控件取值 + 校验结果）。"""

    def_id: str
    fdef: object
    count: int
    universe: int
    start_ch: int
    channel_count: int

    @property
    def needed(self) -> int:
        return self.channel_count * self.count

    @property
    def end_ch(self) -> int:
        return self.start_ch + self.needed - 1


def _read_request(panel) -> Optional[_Request]:
    """从左侧控件读请求；无选中类型 / 类型失效 → None（并给出提示）。"""
    item = panel._def_list.currentItem()
    if not item:
        QMessageBox.information(panel, "提示", "请先在左侧选择一个设备类型。")
        return None
    fdef = panel._project.get_fixture_def(item.data(Qt.ItemDataRole.UserRole))
    if not fdef:
        return None
    return _Request(def_id=str(getattr(fdef, "id", "")), fdef=fdef,
                    count=int(panel._inst_count_spin.value()),
                    universe=int(panel._inst_universe_spin.value()),
                    start_ch=int(panel._inst_start_ch_spin.value()),
                    channel_count=int(getattr(fdef, "channel_count", 1) or 1))


def _check_overflow(panel, req: _Request) -> bool:
    """512 溢出校验（超限 → 提示并阻断）。"""
    if req.end_ch <= 512:
        return True
    QMessageBox.warning(
        panel, "通道溢出",
        f"配接 {req.count} 台 [{req.fdef.name}]（每台 {req.channel_count} 通道）\n"
        f"共需 {req.needed} 个通道，从 CH{req.start_ch} 开始将到达 CH{req.end_ch}，\n"
        f"超出宇宙 {req.universe} 的 512 通道限制。\n\n"
        f"请减少数量或降低起始通道。")
    return False


def _confirm_overwrite(panel, *, victims, defs_by_id, req: _Request) -> bool:
    """冲突确认：列出将被移除的设备（默认按钮 = 否，防误点回车）。"""
    ret = QMessageBox.warning(
        panel, "地址冲突（将覆盖已有配接）",
        f"宇宙 {req.universe} 的 CH{req.start_ch} – CH{req.end_ch} 已被 "
        f"{len(victims)} 台设备占用：\n\n"
        f"{fixtures_text(victims, defs_by_id)}\n\n"
        "继续配接会移除上述设备的原有配接（可用 Ctrl+Z 撤销）。是否继续？",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No)
    return ret == QMessageBox.StandardButton.Yes


def do_patch(panel) -> bool:
    """执行配接。返回是否真的写入（用户取消/校验失败 → False）。"""
    req = _read_request(panel)
    if req is None or not _check_overflow(panel, req):
        return False
    panel._ensure_universe_grid(req.universe)
    defs_by_id = {str(getattr(d, "id", "")): d for d in
                  (getattr(panel._project, "fixture_defs", []) or [])}
    ranges = normalize_ranges(req.start_ch, req.channel_count, req.count)
    victims = overlapping_fixtures(
        list(getattr(panel._project, "fixtures", []) or []),
        defs_by_id, req.universe, ranges)
    if victims and not _confirm_overwrite(panel, victims=victims,
                                          defs_by_id=defs_by_id, req=req):
        return False
    panel._snapshot_for_undo()
    _commit(panel, req, ranges, defs_by_id, len(victims))
    return True


def _commit(panel, req: _Request, ranges: Sequence[Tuple[int, int]],
            defs_by_id: dict, removed_count: int) -> None:
    """写入（去重叠 → 追加 → 广播）；提示文案与旧实现一致（仅"自动"二字去掉）。"""
    panel._project.fixtures = _drop_overlaps(panel, req, ranges, defs_by_id)
    for i in range(req.count):
        panel._project.fixtures.append(FixtureInstance(
            def_id=req.def_id,
            name=f"{req.fdef.name} {len(panel._project.fixtures) + 1}",
            universe=req.universe,
            start_channel=req.start_ch + i * req.channel_count,
        ))
    panel._refresh_grids()
    panel.fixtures_changed.emit()
    if getattr(panel, "_pm", None):
        panel._pm.project_changed.emit()
    if hasattr(panel._project, "project_changed"):
        panel._project.project_changed.emit()
    msg = (f"已成功配接 {req.count} 台 [{req.fdef.name}]\n"
           f"宇宙: {req.universe}，通道: CH{req.start_ch} – CH{req.end_ch}")
    if removed_count:
        msg += f"\n\n已移除 {removed_count} 台与该范围重复/重叠的旧设备。"
    QMessageBox.information(panel, "配接完成", msg)


def _drop_overlaps(panel, req: _Request, ranges: Sequence[Tuple[int, int]],
                   defs_by_id: dict) -> List[object]:
    """返回"保留的 fixtures"——与原内联实现同判据（按对象身份剔除重叠项）。"""
    victims = {id(fi) for fi in overlapping_fixtures(
        list(getattr(panel._project, "fixtures", []) or []),
        defs_by_id, req.universe, ranges)}
    return [fi for fi in (getattr(panel._project, "fixtures", []) or [])
            if id(fi) not in victims]
