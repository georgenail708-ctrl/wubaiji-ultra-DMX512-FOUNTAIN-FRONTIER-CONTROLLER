# -*- coding: utf-8 -*-
"""Patch 配接表导入/导出（R183/F2 · 深查③ P2/P6/P7）。

从 `patch_panel.py` 搬出的两个上帝类方法体（`_export_patch_excel` /
`_import_patch_excel`，基线行数/CC 已顶格、再改即门禁恶化），并修掉两处
"隐形不可用"：

  * **openpyxl 缺席时不再是错误框**：导出/导入自动降级 stdlib csv
    （`table_io_fallback`，utf-8-sig 带 BOM，Excel/WPS 双击直接开中文不乱码），
    并提示实际走的哪条路；
  * **配接前冲突警告（P2）**：导入批里出现地址区间重叠时，先弹**冲突详情**
    再写入（行为兼容：仍然写入，因为 Patch 表导入本就是"以表为准"的覆写；
    变化只在于不再静默）。`_do_patch` 侧的同款警告在 `patch_guard` +
    面板 `_do_patch` 的显式清单里。

列结构与旧 Excel 版**逐列相同**（fixture_id/name/type/def_id/universe/
start_channel/channel_count/channels/备注），保证老表格仍可导入。
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from ..core.models import FixtureInstance
from .patch_guard import conflict_text, find_conflicts, normalize_ranges
from .table_io_fallback import (cell_str, fmt_note, notify_user,
                                openpyxl_pack, read_table, write_table)

#: 导出的列（与旧实现一致，勿改顺序：用户台账里已有表头）。
EXPORT_HEADERS: Tuple[str, ...] = (
    "fixture_id", "fixture_name", "fixture_type", "def_id", "universe",
    "start_channel", "channel_count", "channels", "备注")
EXPORT_WIDTHS: Tuple[int, ...] = (38, 24, 24, 38, 12, 14, 14, 42, 28)
EXPORT_FILL = "7030A0"          # 旧实现表头紫底（openpyxl 路径保持观感）
SHEET_TITLE = "配接关系"
HELP_TITLE = "说明"
HELP_LINES: Tuple[str, ...] = (
    "Patch 配接关系表说明",
    "本文件可用 Microsoft Office / WPS / LibreOffice 打开编辑，再导入软件。",
    "请保留表头。导入时优先按 fixture_id 更新已有设备；找不到时会按 fixture_name 更新或新增。",
    "fixture_type 必须能匹配项目中的设备类型名称，或填写 def_id。universe 为宇宙号，start_channel 为起始 DMX 通道。",
    "channel_count 和 channels 主要用于阅读注释；导入时通道数量以项目中的设备类型定义为准。",
)
REQUIRED_HEADERS: Tuple[str, ...] = (
    "fixture_id", "fixture_name", "fixture_type", "def_id", "universe", "start_channel")


def _rows_for(panel) -> List[List[object]]:
    """设备 → 行（与旧实现同式：def 缺席时后四列留空）。"""
    rows: List[List[object]] = []
    for fi in getattr(panel._project, "fixtures", []) or []:
        fdef = panel._project.get_fixture_def(fi.def_id) if hasattr(
            panel._project, "get_fixture_def") else None
        rows.append([
            fi.id, fi.name, fdef.name if fdef else "", fi.def_id,
            fi.universe, fi.start_channel,
            fdef.channel_count if fdef else "",
            ",".join(getattr(fdef, "channels", []) or []) if fdef else "", "",
        ])
    return rows


def export_patch_table(panel) -> bool:
    """导出配接关系表：openpyxl 在 → .xlsx；不在 → .csv（并提示两种格式差异）。"""
    from PyQt6.QtWidgets import QFileDialog, QMessageBox
    default = "patch_table.xlsx" if openpyxl_pack() else "patch_table.csv"
    filt = ("Excel 工作簿 (*.xlsx);;CSV 表格 (*.csv)" if openpyxl_pack()
            else "CSV 表格 (*.csv);;Excel 工作簿 (*.xlsx)")
    path, _ = QFileDialog.getSaveFileName(
        panel, "导出 Patch 配接关系表", default, filt)
    if not path:
        return False
    try:
        out, fmt = write_table(
            path, EXPORT_HEADERS, _rows_for(panel),
            sheet_title=SHEET_TITLE, header_fill=EXPORT_FILL,
            widths=EXPORT_WIDTHS, help_title=HELP_TITLE, help_lines=HELP_LINES)
    except Exception as exc:
        QMessageBox.critical(panel, "导出失败", f"无法写出配接表：\n{exc}")
        return False
    msg = f"Patch 配接关系已导出（{len(_rows_for(panel))} 台）"
    notify_user(panel, msg, f"{fmt_note(fmt)} {out}", kind="success")
    return True


def _strip_help_rows(rows: Sequence[Sequence]) -> List[List]:
    """csv 路径把「说明」写成 `#` 注释行 → 导入时先滤掉。"""
    out = []
    for row in rows:
        first = str(row[0]).strip() if row else ""
        if first.startswith("#"):
            continue
        out.append(list(row))
    return out


def _int_or_none(value) -> object:
    """严格 int 解析（旧实现的 try/except 判据：非法即跳过该行，不静默取默认值）。"""
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        return int(float(text))
    except Exception:
        return None


def _row_to_pending(row, idx: dict, defs_by_id: dict, defs_by_name: dict):
    """单行 → 待写元组；不可用行返回 None（调用方计 skipped）。"""
    fid = cell_str(row, idx["fixture_id"])
    fname = cell_str(row, idx["fixture_name"])
    ftype = cell_str(row, idx["fixture_type"])
    def_id = cell_str(row, idx["def_id"])
    fdef = defs_by_id.get(def_id) or defs_by_name.get(ftype)
    if not fdef:
        return None
    raw_u = row[idx["universe"]] if idx["universe"] < len(row) else None
    raw_s = row[idx["start_channel"]] if idx["start_channel"] < len(row) else None
    universe = _int_or_none(raw_u)
    start_channel = _int_or_none(raw_s)
    if universe is None or start_channel is None:
        return None
    return (fid, fname, fdef, max(1, int(universe)),
            max(1, min(512, int(start_channel))))


def _parse_pending(panel, headers: Sequence[str], rows: Sequence[Sequence]):
    """行 → (pending, skipped, err)：与旧实现逐条同判据（缺类型/非法数字即跳过）。"""
    idx = {h: i for i, h in enumerate(headers)}
    missing = [h for h in REQUIRED_HEADERS if h not in idx]
    if missing:
        return None, 0, "缺少字段：" + ", ".join(missing)
    defs = getattr(panel._project, "fixture_defs", []) or []
    defs_by_id = {str(fd.id): fd for fd in defs}
    defs_by_name = {str(fd.name): fd for fd in defs}
    pending: List[tuple] = []
    skipped = 0
    for row in _strip_help_rows(rows):
        if not row or all(str(v).strip() == "" for v in row):
            continue
        parsed = _row_to_pending(row, idx, defs_by_id, defs_by_name)
        if parsed is None:
            skipped += 1
            continue
        pending.append(parsed)
    return pending, skipped, ""


def import_patch_table(panel) -> bool:
    """导入配接关系表（xlsx 或 csv）：冲突先警告 → 写入 → 刷新。"""
    from PyQt6.QtWidgets import QFileDialog, QMessageBox
    path, _ = QFileDialog.getOpenFileName(
        panel, "导入 Patch 配接关系表", "",
        "表格文件 (*.xlsx *.csv);;Excel 工作簿 (*.xlsx);;CSV 表格 (*.csv)")
    if not path:
        return False
    try:
        headers, rows, fmt = read_table(path, sheet_title=SHEET_TITLE)
    except Exception as exc:
        QMessageBox.critical(panel, "导入失败", f"无法读取配接表：\n{exc}")
        return False
    pending, skipped, err = _parse_pending(panel, headers, rows)
    if pending is None:
        QMessageBox.warning(panel, "表格格式错误", err)
        return False
    if not pending:
        QMessageBox.information(
            panel, "导入完成",
            "已导入/更新 0 条配接关系。"
            + (f"\n跳过 {skipped} 行：未找到设备类型或字段无效。" if skipped else ""))
        return True
    if not _confirm_conflicts(panel, pending):
        return False
    _write_pending(panel, pending)
    tail = f"\n跳过 {skipped} 行：未找到设备类型或字段无效。" if skipped else ""
    notify_user(panel, f"已导入/更新 {len(pending)} 条配接关系",
                f"{fmt_note(fmt, '读取')} {path}{tail}", kind="success")
    return True


def _confirm_conflicts(panel, pending) -> bool:
    """导入前的地址冲突检查（P2）：有冲突 → 红字详情 + 用户确认。"""
    from PyQt6.QtWidgets import QMessageBox
    incoming: List[dict] = []
    for fid, fname, fdef, universe, start_channel in pending:
        ranges = normalize_ranges(start_channel, int(getattr(fdef, "channel_count", 1) or 1), 1)
        incoming.append({"id": fid or fname, "name": fname or getattr(fdef, "name", ""),
                         "universe": universe, "start": ranges[0][0], "end": ranges[0][1]})
    conflicts = _conflicts_among(incoming)
    if not conflicts:
        return True
    detail = conflict_text(conflicts, limit=10)
    ret = QMessageBox.warning(
        panel, "配接表存在地址冲突",
        f"本次导入的表内检测到 {len(conflicts)} 处地址重叠：\n\n{detail}\n\n"
        "继续导入会按表格地址覆盖同名设备。是否继续？",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No)
    return ret == QMessageBox.StandardButton.Yes


def _conflicts_among(records: Sequence[dict]) -> List[dict]:
    """对"即将写入的行"做本地冲突检测（同宇宙区间两两重叠）。"""
    buckets: Dict[int, List[dict]] = {}
    for r in records:
        buckets.setdefault(int(r["universe"]), []).append(r)
    out: List[dict] = []
    for universe in sorted(buckets):
        rows = sorted(buckets[universe], key=lambda r: (r["start"], r["end"], r["id"]))
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                if b["start"] > a["end"]:
                    break
                out.append({"universe": universe,
                            "a": {"id": a["id"], "name": a["name"],
                                  "start": a["start"], "end": a["end"]},
                            "b": {"id": b["id"], "name": b["name"],
                                  "start": b["start"], "end": b["end"]},
                            "overlap_start": max(a["start"], b["start"]),
                            "overlap_end": min(a["end"], b["end"])})
    return out


def _write_pending(panel, pending) -> None:
    """写入（与旧实现同语义）：按 id/名字匹配更新，找不到则新增。"""
    import uuid as _uuid
    project = panel._project
    fixtures = getattr(project, "fixtures", []) or []
    by_id = {str(fi.id): fi for fi in fixtures}
    by_name = {str(fi.name): fi for fi in fixtures}
    panel._snapshot_for_undo()
    for fid, fname, fdef, universe, start_channel in pending:
        fi = by_id.get(fid) or by_name.get(fname)
        if fi is None:
            fi = FixtureInstance(id=fid or str(_uuid.uuid4()), def_id=fdef.id,
                                 name=fname or f"{fdef.name} {len(fixtures) + 1}")
            fixtures.append(fi)
            by_id[str(fi.id)] = fi
            by_name[str(fi.name)] = fi
        fi.name = fname or fi.name
        fi.def_id = fdef.id
        fi.universe = universe
        fi.start_channel = start_channel
    project.fixtures = fixtures
    panel._refresh_all()
    _broadcast(panel, project)


def _broadcast(panel, project) -> None:
    """配接变更广播（信号 + PM + 工程对象三连，与面板既有写法一致）。"""
    panel.fixtures_changed.emit()
    if getattr(panel, "_pm", None):
        panel._pm.project_changed.emit()
    if hasattr(project, "project_changed"):
        project.project_changed.emit()


def project_conflicts(panel) -> List[dict]:
    """整表冲突（表格红底 + 顶部摘要共用）。"""
    project = panel._project
    defs_by_id = {str(getattr(d, "id", "")): d for d in
                  (getattr(project, "fixture_defs", []) or [])}
    return find_conflicts(getattr(project, "fixtures", []) or [], defs_by_id)


__all__ = ["export_patch_table", "import_patch_table", "project_conflicts",
           "EXPORT_HEADERS", "SHEET_TITLE", "REQUIRED_HEADERS"]
