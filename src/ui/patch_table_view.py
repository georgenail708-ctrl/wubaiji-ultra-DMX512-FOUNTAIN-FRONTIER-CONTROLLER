# -*- coding: utf-8 -*-
"""Patch 表格视图（R183/F2 · 深查③ P1/P2/P3/P4）。

Qt 内建 `QTableView + QAbstractTableModel + QSortFilterProxyModel`，数据源就是
`project.fixtures`（**不引入第二份真源**：写回时直接改 `FixtureInstance`）。
`PatchPanel._build_ui` 基线已顶格（183 行，再涨即门禁 ERROR），故整页在
`attach_patch_table(panel)` 里**运行时注入**：把既有 `_grid_tabs` 塞进一个外层
QTabWidget 的第一页，第二页放表格 —— 面板源码只多一行调用（见 `__init__`）。

能力对照 MA3 / Avolites Titan 的 Patch 表：
  * 列：名称 / 类型 / 宇宙 / 起始通道 / 通道数 / 占用区间 / 状态（排序+筛选）；
  * **冲突行红底**（P2）+ 顶部摘要条（点开看冲突详情）；
  * **自动地址建议**（P3）：`suggest_start_channel` 找连续空位 → 一键填到左侧
    "起始通道" QSpinBox（不自动改写用户已填的值，避免惊吓）；
  * **重编号**（P4）：选中行 → 起始宇宙/通道对话框 → `renumber_plan`（跨域
    自动进位）→ 写回；**先 `_snapshot_for_undo()` 登记撤销域**。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
                            QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                            QMessageBox, QPushButton, QSpinBox, QTabWidget,
                            QTableView, QVBoxLayout, QWidget)

from . import qascade_theme as _th
from . import patch_guard as _pg

#: 表格列（标题, 取值键）。
COLUMNS = (
    ("设备名称", "name"), ("类型", "type"), ("宇宙", "universe"),
    ("起始通道", "start"), ("通道数", "count"), ("占用区间", "span"),
    ("状态", "status"),
)
COL_NAME, COL_TYPE, COL_UNI, COL_START, COL_COUNT, COL_SPAN, COL_STATUS = range(7)
#: 冲突行底色（哑光红，与主题 BRIGHT_ERROR 同族）。
CONFLICT_BG = QColor(153, 22, 22, 90)
CONFLICT_FG = QColor(_th.BRIGHT_ERROR)


def rows_for(project, defs_by_id: Dict[str, object],
             conflicts: Sequence[dict] = ()) -> List[dict]:
    """设备 → 表格行（**位置/状态都从 fixture 现读**，不含缓存副本）。"""
    bad = _pg.conflict_ids(conflicts)
    out: List[dict] = []
    for fi in getattr(project, "fixtures", []) or []:
        fdef = defs_by_id.get(str(getattr(fi, "def_id", "")))
        count = _pg.channel_count_of(fi, defs_by_id)
        span = _pg.span_of(fi, defs_by_id)
        fid = str(getattr(fi, "id", ""))
        out.append({
            "id": fid,
            "fixture": fi,
            "name": str(getattr(fi, "name", "") or fid),
            "type": str(getattr(fdef, "name", "") or ""),
            "universe": int(getattr(fi, "universe", 1) or 1),
            "start": int(getattr(fi, "start_channel", 1) or 1),
            "count": int(count),
            "span": f"CH{span[1]}–CH{span[2]}" if span else "—",
            "status": "地址冲突" if fid in bad else "正常",
            "conflict": fid in bad,
        })
    return out


class PatchTableModel(QAbstractTableModel):
    """只读表格模型（编辑一律走"重编号/自动地址"两个受控入口）。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: List[dict] = []

    def set_rows(self, rows: Sequence[dict]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def row_at(self, index: QModelIndex) -> Optional[dict]:
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        return self._rows[index.row()]

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return COLUMNS[section][0] if 0 <= section < len(COLUMNS) else None
        return str(section + 1)

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):  # noqa: C901
        row = self.row_at(index)
        if row is None:
            return None
        col = index.column()
        if role == Qt.ItemDataRole.ToolTipRole:
            return (f"{row['name']}　{row['type']}\n"
                    f"宇宙 {row['universe']}　{row['span']}（{row['count']} ch）\n"
                    f"状态：{row['status']}")
        if role == Qt.ItemDataRole.DisplayRole:
            return row.get(COLUMNS[col][1], "")
        if role == Qt.ItemDataRole.UserRole:
            return row["id"]
        return self._decorate(row, col, role)

    def _decorate(self, row: dict, col: int, role: int):
        """角色装饰：冲突行红底/红字/加粗；数值列居中。"""
        if row.get("conflict"):
            if role == Qt.ItemDataRole.BackgroundRole:
                return QBrush(CONFLICT_BG)
            if role == Qt.ItemDataRole.ForegroundRole:
                return QBrush(CONFLICT_FG)
            if role == Qt.ItemDataRole.FontRole:
                return _th.cached_font(11, bold=True)
        if role == Qt.ItemDataRole.TextAlignmentRole and col in (COL_UNI, COL_COUNT):
            return int(Qt.AlignmentFlag.AlignCenter)
        return None

    def sort(self, column: int, order=Qt.SortOrder.AscendingOrder) -> None:  # noqa: N802
        """按列排序（数值列走原值，字符串列忽略大小写）。"""
        key = COLUMNS[column][1] if 0 <= column < len(COLUMNS) else "name"
        reverse = order == Qt.SortOrder.DescendingOrder

        def sort_key(row: dict):
            value = row.get(key, "")
            if isinstance(value, (int, float)):
                return (0, float(value), row["name"])
            return (1, 0.0, str(value))

        self.beginResetModel()
        self._rows.sort(key=sort_key, reverse=reverse)
        self.endResetModel()


class PatchTableWidget(QWidget):
    """表格页（工具栏 + 表 + 冲突摘要条）。"""

    def __init__(self, panel, parent=None) -> None:
        super().__init__(parent)
        self._panel = panel
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(6)
        self._search = QLineEdit()
        self._search.setPlaceholderText("筛选：名称 / 类型 / 宇宙 / 通道…")
        self._search.setClearButtonEnabled(True)
        self._search.setFixedHeight(24)
        self._search.textChanged.connect(self._on_filter_changed)
        row.addWidget(self._search, 1)
        self._uni_combo = QComboBox()
        self._uni_combo.setFixedHeight(24)
        self._uni_combo.setMinimumWidth(110)
        self._uni_combo.currentIndexChanged.connect(lambda _i: self._on_filter_changed(""))
        row.addWidget(self._uni_combo)
        btn_suggest = QPushButton("建议起始通道")
        btn_suggest.setFixedHeight(24)
        btn_suggest.setToolTip(
            "在左侧「目标宇宙」里找能容纳当前数量与类型的连续空位，"
            "填入左侧「起始通道」（只填不配接，先看再决定）")
        btn_suggest.clicked.connect(self._on_suggest)
        row.addWidget(btn_suggest)
        btn_renumber = QPushButton("重编号…")
        btn_renumber.setFixedHeight(24)
        btn_renumber.setToolTip(
            "对选中行按各自通道数顺序重排地址；跨 512 自动进位到下一宇宙；"
            "可撤销（撤销域=Patch）")
        btn_renumber.clicked.connect(self._on_renumber)
        row.addWidget(btn_renumber)
        lay.addLayout(row)

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet(f"color:{_th.TEXT_SECONDARY};font-size:11px;")
        lay.addWidget(self._summary)

        self._model = PatchTableModel(self)
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSortingEnabled(True); self._table.horizontalHeader().sortIndicatorChanged.connect(lambda *_: self._apply_filter())  # ★ R193/QT-01：排序后重跑过滤器（隐藏行随新序重算，修复错位）
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setDefaultSectionSize(22)
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.doubleClicked.connect(self._on_double_clicked)
        lay.addWidget(self._table, 1)

    # ── 刷新 ──
    def refresh(self) -> None:
        project = self._panel._project
        defs_by_id = {str(getattr(d, "id", "")): d for d in
                      (getattr(project, "fixture_defs", []) or [])}
        conflicts = _pg.find_conflicts(
            getattr(project, "fixtures", []) or [], defs_by_id)
        rows = rows_for(project, defs_by_id, conflicts)
        self._model.set_rows(rows)
        self._rebuild_universe_combo(rows)
        self._apply_filter()
        self._update_summary(conflicts)
        self._mark_grid_conflicts(defs_by_id, conflicts)

    def _mark_grid_conflicts(self, defs_by_id: dict, conflicts: Sequence[dict]) -> None:
        """P2 画布侧：把冲突设备的**整个占用区间**标红（不只重叠段）。

        只标重叠段会让"到底哪两台撞了"看不出来（两段都被裁成中间一小截）；
        标全段则在画布上直接呈现两台各自的范围 + 表格里的重叠明细，互补。
        """
        by_universe = conflict_channels(conflicts, defs_by_id, self._fixture_by_id)
        grids = getattr(self._panel, "_universe_grids", {}) or {}
        for universe, grid in grids.items():
            if grid is not None and getattr(grid, "_built", False):
                grid.set_conflict_channels(by_universe.get(int(universe), ()))

    def _fixture_by_id(self, fid: str):
        for fi in getattr(self._panel._project, "fixtures", []) or []:
            if str(getattr(fi, "id", "")) == str(fid):
                return fi
        return None

    def _rebuild_universe_combo(self, rows: Sequence[dict]) -> None:
        current = self._uni_combo.currentData()
        self._uni_combo.blockSignals(True)
        self._uni_combo.clear()
        self._uni_combo.addItem("全部宇宙", None)
        for u in sorted({int(r["universe"]) for r in rows}):
            used, pct = _pg.universe_usage(
                getattr(self._panel._project, "fixtures", []) or [],
                {str(getattr(d, "id", "")): d for d in
                 (getattr(self._panel._project, "fixture_defs", []) or [])}, u)
            self._uni_combo.addItem(f"宇宙 {u}（{pct}%）", u)
        if current is not None:
            idx = self._uni_combo.findData(current)
            if idx >= 0:
                self._uni_combo.setCurrentIndex(idx)
        self._uni_combo.blockSignals(False)

    def _on_filter_changed(self, _text: str = "") -> None:
        self._apply_filter()

    def _apply_filter(self) -> None:
        needle = self._search.text().strip().lower()
        want_u = self._uni_combo.currentData()
        shown = 0
        for r in range(self._model.rowCount()):
            row = self._model._rows[r]
            ok = True
            if needle:
                hay = " ".join(str(row.get(k, "")) for k in
                               ("name", "type", "universe", "start", "span")).lower()
                ok = needle in hay
            if ok and want_u is not None:
                ok = int(row["universe"]) == int(want_u)
            self._table.setRowHidden(r, not ok)
            shown += 1 if ok else 0
        self._shown = shown

    def _update_summary(self, conflicts: Sequence[dict]) -> None:
        total = self._model.rowCount()
        parts = [f"共 {total} 台设备"]
        if conflicts:
            parts.append("地址冲突 %d 处（红底行）" % len(conflicts))
            self._summary.setStyleSheet(
                f"color:{_th.BRIGHT_ERROR};font-size:11px;font-weight:bold;")
            self._summary.setToolTip(_pg.conflict_text(conflicts, limit=20))
        else:
            parts.append("地址无冲突")
            self._summary.setStyleSheet(f"color:{_th.TEXT_SECONDARY};font-size:11px;")
            self._summary.setToolTip("")
        self._summary.setText("　·　".join(parts))

    # ── P3 自动地址建议 ──
    def _on_suggest(self) -> None:
        panel = self._panel
        item = panel._def_list.currentItem() if hasattr(panel, "_def_list") else None
        if item is None:
            QMessageBox.information(panel, "自动地址", "请先在左侧选择一个设备类型。")
            return
        def_id = item.data(Qt.ItemDataRole.UserRole)
        fdef = panel._project.get_fixture_def(def_id)
        if fdef is None:
            return
        universe = int(panel._inst_universe_spin.value())
        count = int(panel._inst_count_spin.value())
        defs_by_id = {str(getattr(d, "id", "")): d for d in
                      (getattr(panel._project, "fixture_defs", []) or [])}
        start = _pg.suggest_start_channel(
            getattr(panel._project, "fixtures", []) or [], defs_by_id,
            universe, int(getattr(fdef, "channel_count", 1) or 1), count=count)
        if start is None:
            QMessageBox.warning(
                panel, "自动地址",
                f"宇宙 {universe} 已无足够连续空位容纳 {count} 台 "
                f"[{getattr(fdef, 'name', '')}]"
                f"（每台 {getattr(fdef, 'channel_count', 1)} 通道）。\n\n请换一个宇宙。")
            return
        panel._inst_start_ch_spin.setValue(int(start))
        end = start + int(getattr(fdef, "channel_count", 1) or 1) * count - 1
        self._summary.setText(
            f"建议：宇宙 {universe} 从 CH{start} 起（占至 CH{end}），"
            f"已填入左侧「起始通道」")
        self._summary.setStyleSheet(f"color:{_th.BRIGHT_SUCCESS};font-size:11px;")

    # ── P4 重编号 ──
    def _selected_rows(self) -> List[dict]:
        rows = []
        seen = set()
        for index in self._table.selectionModel().selectedRows():
            row = self._model.row_at(index)
            if row is not None and row["id"] not in seen:
                seen.add(row["id"])
                rows.append(row)
        return rows

    def _on_renumber(self) -> None:
        panel = self._panel
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(
                panel, "重编号", "请先在表格里选中要重排的设备（可多选，按行序推进）。")
            return
        first = rows[0]
        dlg = _RenumberDialog(panel, first_universe=int(first["universe"]),
                              first_start=int(first["start"]), count=len(rows))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        start_universe, start_channel = dlg.values()
        defs_by_id = {str(getattr(d, "id", "")): d for d in
                      (getattr(panel._project, "fixture_defs", []) or [])}
        plan = _pg.renumber_plan([r["fixture"] for r in rows], start_universe,
                                 start_channel, defs_by_id)
        if not plan:
            return
        panel._snapshot_for_undo()          # 撤销域登记（DOMAIN_PATCH）
        for step in plan:
            fi = step["fixture"]
            fi.universe = int(step["universe"])
            fi.start_channel = int(step["start"])
        refresh_grids = getattr(panel, "_refresh_grids", None)
        if callable(refresh_grids):
            refresh_grids()
        self.refresh()
        _broadcast(panel)
        QMessageBox.information(panel, "重编号完成", _pg.plan_summary(plan))

    def _on_double_clicked(self, index: QModelIndex) -> None:
        row = self._model.row_at(index)
        if row is None:
            return
        universe = int(row["universe"])
        self._panel._ensure_universe_grid(universe)
        keys = list(self._panel._universe_grids)
        if universe in keys:
            self._panel._grid_tabs.setCurrentIndex(keys.index(universe))
        grid = self._panel._universe_grids.get(universe)
        if grid is not None:
            grid.ensure_built()
            cell = getattr(grid, "_cells", {}).get(int(row["start"]))
            if cell is not None:
                grid._on_cell_clicked(cell)


class _RenumberDialog(QDialog):
    """重编号参数（起始宇宙 + 起始通道；步长=各台自身通道数）。"""

    def __init__(self, parent=None, *, first_universe: int = 1,
                 first_start: int = 1, count: int = 0) -> None:
        super().__init__(parent)
        self.setWindowTitle("重编号")
        lay = QFormLayout(self)
        self._universe = QSpinBox()
        self._universe.setRange(1, 32767)
        self._universe.setValue(max(1, int(first_universe)))
        self._channel = QSpinBox()
        self._channel.setRange(1, 512)
        self._channel.setValue(max(1, int(first_start)))
        lay.addRow("起始宇宙", self._universe)
        lay.addRow("起始通道", self._channel)
        note = QLabel(f"将按选中顺序重排 {int(count)} 台设备；"
                      "每台占其自身通道数，跨 512 自动进位到下一宇宙。")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{_th.TEXT_SECONDARY};font-size:11px;")
        lay.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addRow(buttons)

    def values(self):
        return int(self._universe.value()), int(self._channel.value())


def conflict_channels(conflicts: Sequence[dict], defs_by_id: dict, lookup) -> Dict[int, set]:
    """冲突记录 → {宇宙: 需标红的通道集合}（整段，非只重叠段）。

    `lookup(fid)` 返回设备实例（None 表示已删除）。纯函数便于单测。
    """
    by_universe: Dict[int, set] = {}
    for c in conflicts or ():
        universe = int(c["universe"])
        for key in ("a", "b"):
            fi = lookup(c[key]["id"])
            if fi is None:
                continue
            span = _pg.span_of(fi, defs_by_id)
            if span is None or span[0] != universe:
                continue
            by_universe.setdefault(universe, set()).update(range(span[1], span[2] + 1))
    return by_universe


def _broadcast(panel) -> None:
    """配接变更广播（与面板既有三连一致：信号 + PM + 工程对象）。"""
    try:
        panel.fixtures_changed.emit()
        if getattr(panel, "_pm", None):
            panel._pm.project_changed.emit()
        if hasattr(panel._project, "project_changed"):
            panel._project.project_changed.emit()
    except Exception:
        pass


def attach_patch_table(panel) -> bool:
    """在 PatchPanel 上注入「表格」页（幂等；由 `__init__` 调用一次）。"""
    if getattr(panel, "_patch_table", None) is not None:
        return False
    grid_tabs = getattr(panel, "_grid_tabs", None)
    if grid_tabs is None:
        return False
    outer = QTabWidget()
    outer.setDocumentMode(True)
    parent_layout = grid_tabs.parentWidget().layout() if grid_tabs.parentWidget() else None
    if parent_layout is None:
        return False
    table_widget = PatchTableWidget(panel)
    parent_layout.replaceWidget(grid_tabs, outer)
    grid_tabs.setParent(outer)
    outer.addTab(grid_tabs, "配接网格")
    outer.addTab(table_widget, "表格")
    panel._patch_table_tabs = outer
    panel._patch_table = table_widget
    try:
        panel.fixtures_changed.connect(lambda *_: table_widget.refresh())
    except Exception:
        pass
    table_widget.refresh()
    return True
