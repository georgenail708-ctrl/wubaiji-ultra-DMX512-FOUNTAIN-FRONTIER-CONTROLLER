# -*- coding: utf-8 -*-
"""TopView 设备布局表导入/导出（R183/F2 · 深查③ T9/P7）。

从 `topview_panel._export_layout_excel` / `_import_layout_excel` 搬出的方法体
（两个函数在架构基线里已顶格：53 行/73 行，再加 csv 分支即门禁 ERROR），并修掉
"openpyxl 缺失 → Excel 导入导出双双不可用"的隐形 bug：

  * 导出：有 openpyxl 落 .xlsx（含「说明」工作表与列宽，观感与旧版一致）；
    没有则落 .csv（utf-8-sig 带 BOM，Excel/WPS 双击直接开中文不乱码），
    「说明」写成表尾 `#` 注释行（导入端自动忽略）；
  * 导入：.xlsx 与 .csv 同一条通路（`table_io_fallback.read_table`），
    字段判据、双写（fixtures + visible_fixture_ids）、建页行为与旧实现逐条一致。

坐标真源说明：坐标落在 `page['fixtures'][fid] = {x, y}`，与本模块读写一致。
真工程 `../To The Moon项目.dmxt` 的 `fixture` 记录 `pos_x/pos_y` 全为 0 —— 所有
"按坐标"的功能都必须读 TopView 页数据（本模块正是如此），不得读设备记录。
"""
from __future__ import annotations

import uuid as _uuid
from typing import List, Sequence, Tuple

from .table_io_fallback import (cell_float, cell_str, fmt_note, notify_user,
                               read_table, write_table)

#: 导出列（与旧实现一致；导入按表头名取列，顺序变更不受影响）。
EXPORT_HEADERS: Tuple[str, ...] = (
    "fixture_id", "fixture_name", "sheet_name", "x", "y", "visible", "备注")
EXPORT_WIDTHS: Tuple[int, ...] = (38, 24, 18, 12, 12, 12, 28)
SHEET_TITLE = "设备布局"
EXPORT_FILL = "305496"
HELP_TITLE = "说明"
HELP_LINES: Tuple[str, ...] = (
    "TopView 设备布局表说明",
    "本文件可用 Microsoft Office / WPS / LibreOffice 打开编辑，再导入软件。",
    "请保留表头。导入时优先按 fixture_id 匹配设备；找不到 id 时按 fixture_name 匹配。",
    "sheet_name 表示 TopView 页面；x/y 为画布坐标；visible 可填 1/0、true/false、是/否。",
    "只导入设备位置与显示信息，不会修改 Patch 配接地址。Patch 请使用独立的配接关系表。",
)
REQUIRED_HEADERS: Tuple[str, ...] = ("fixture_id", "fixture_name", "sheet_name", "x", "y")
#: visible 列的假值集合（旧实现的 or 链，语义不变）。
FALSE_TOKENS = ("0", "false", "否", "no", "n")


def _page_row_ids(page: dict) -> List[str]:
    """该页要导出的设备 id：**可见集为准**（可见集为空时兜底坐标表键）。

    ★ 语义依据（真工程实测）：页的 `fixtures` 是**整页坐标表**，会保留大量
    "不属于本页"的历史条目 —— 真工程 `../To The Moon项目.dmxt` 10 页共 16620 条，
    而各页 `visible_fixture_ids` 合计仅 3156 条（LED 页 960、数控页 174、
    气爆页 222×4 等）。旧实现导 `fixtures` 的全部键：
      ① 行数虚高 5 倍；
      ② 那些条目的 `visible` 键要么缺失（读作 True）、要么属于别的页 —— 往返导入
         会把"别的页的设备"写进本页可见集，并注入别页坐标（P1 同族缺陷）。
    判据用「键是否存在」而不是「列表是否非空」：真工程 LED 首屏就是
    `visible_fixture_ids: []`（显式空 = 本页什么都没放）—— 那种页按旧实现会导出
    2376 行"别的页的设备"。只有**老存档根本没有该键**时才回退坐标表键。
    """
    if "visible_fixture_ids" in page:
        return [str(v) for v in (page.get("visible_fixture_ids") or [])]
    return [str(k) for k in (page.get("fixtures") or {}).keys()]


def _rows_for(panel) -> List[List[object]]:
    """各页实际放置的设备 → 行（外层按页、内层按 `_page_row_ids`）。"""
    by_id = panel.fixtures_by_id()
    out: List[List[object]] = []
    for page in panel._pages:
        sheet_name = page.get("name", "Sheet")
        fpos = page.get("fixtures", {}) or {}
        fids = _page_row_ids(page)
        for fid in fids:
            sfid = str(fid)
            fi = by_id.get(sfid)
            pos = fpos.get(fid, {})
            if not isinstance(pos, dict):
                pos = {}
            out.append([
                sfid,
                getattr(fi, "name", "") if fi is not None else "",
                sheet_name,
                pos.get("x", getattr(fi, "pos_x", 0) if fi is not None else 0),
                pos.get("y", getattr(fi, "pos_y", 0) if fi is not None else 0),
                pos.get("visible", True),
                "",
            ])
    return out


def export_layout_table(panel) -> bool:
    """导出布局表（xlsx 或 csv 自动降级）。"""
    from PyQt6.QtWidgets import QFileDialog, QMessageBox
    from .table_io_fallback import openpyxl_pack
    has_xlsx = openpyxl_pack() is not None
    filt = ("Excel 工作簿 (*.xlsx);;CSV 表格 (*.csv)" if has_xlsx
            else "CSV 表格 (*.csv);;Excel 工作簿 (*.xlsx)")
    path, _ = QFileDialog.getSaveFileName(
        panel, "导出 TopView 设备布局表",
        "topview_layout.xlsx" if has_xlsx else "topview_layout.csv", filt)
    if not path:
        return False
    rows = _rows_for(panel)
    try:
        out, fmt = write_table(
            path, EXPORT_HEADERS, rows, sheet_title=SHEET_TITLE,
            header_fill=EXPORT_FILL, widths=EXPORT_WIDTHS,
            help_title=HELP_TITLE, help_lines=HELP_LINES)
    except Exception as exc:
        QMessageBox.critical(panel, "导出失败", f"无法写出布局表：\n{exc}")
        return False
    notify_user(panel, f"TopView 布局已导出（{len(rows)} 条）",
                f"{fmt_note(fmt)} {out}", kind="success")
    return True


def _apply_row(row, idx: dict, sess: dict) -> bool:
    """单行写入（返回是否计入成功）——字段判据与旧实现逐条一致。"""
    panel, by_id, by_name = sess["panel"], sess["by_id"], sess["by_name"]
    page_by_name = sess["pages"]
    fid = cell_str(row, idx["fixture_id"])
    name = cell_str(row, idx["fixture_name"])
    fi = by_id.get(fid) or by_name.get(name)
    if fi is None:
        return False
    sheet_name = cell_str(row, idx["sheet_name"]) or \
        panel.current_page().get("name", "Sheet1")
    x = cell_float(row, idx["x"], 0.0)
    y = cell_float(row, idx["y"], 0.0)
    visible = True
    if "visible" in idx:
        raw = cell_str(row, idx["visible"])
        if raw:
            visible = raw.lower() not in FALSE_TOKENS
    if sheet_name not in page_by_name:
        new_pg = {"id": str(_uuid.uuid4()), "name": sheet_name, "fixtures": {},
                  "cad_shapes": [], "pattern_matrices": [],
                  "visible_fixture_ids": [], "logical_devices": []}
        panel._pages.append(new_pg)
        page_by_name[sheet_name] = new_pg
    _write_position(page_by_name[sheet_name], str(fi.id), x, y, visible)
    return True


def _write_position(page: dict, sfid: str, x: float, y: float, visible: bool) -> None:
    """双写：坐标写 fixtures、可见性同步 visible_fixture_ids（画布只渲染可见集）。

    旧实现只写前者 → 导入的坐标永不显示（P1 已修，此处保持修好的语义）。
    """
    page.setdefault("fixtures", {})[sfid] = {"x": x, "y": y, "visible": visible}
    vis_ids = page.setdefault("visible_fixture_ids", [])
    has = sfid in [str(v) for v in vis_ids]
    if visible and not has:
        vis_ids.append(sfid)
    elif not visible and has:
        page["visible_fixture_ids"] = [v for v in vis_ids if str(v) != sfid]


def _place_rows(panel, headers: Sequence[str], rows: Sequence[Sequence]):
    """按表头名字取列 → 逐行写入页（返回 (写入数, 错误信息)）。

    设备索引按 id/名字各建一次：大表（真工程 16620 行 × 1356 台）逐行线性扫
    会退化成平方级。
    """
    idx = {h: i for i, h in enumerate(headers)}
    missing = [h for h in REQUIRED_HEADERS if h not in idx]
    if missing:
        return 0, "缺少字段：" + ", ".join(missing)
    fixtures = getattr(panel._current_project(), "fixtures", []) or []
    sess = {
        "panel": panel,
        "by_id": {str(fi.id): fi for fi in fixtures},
        "by_name": {str(fi.name): fi for fi in fixtures},
        "pages": {pg.get("name", "Sheet"): pg for pg in panel._pages},
    }
    count = 0
    for row in rows:
        if not row or all(str(v).strip() == "" for v in row):
            continue
        if _apply_row(row, idx, sess):
            count += 1
    return count, ""


def import_layout_table(panel) -> bool:
    """导入布局表（xlsx / csv 同通路），写入后刷新并回写工程状态。"""
    from PyQt6.QtWidgets import QFileDialog, QMessageBox
    path, _ = QFileDialog.getOpenFileName(
        panel, "导入 TopView 设备布局表", "",
        "表格文件 (*.xlsx *.csv);;Excel 工作簿 (*.xlsx);;CSV 表格 (*.csv)")
    if not path:
        return False
    try:
        headers, rows, fmt = read_table(path, sheet_title=SHEET_TITLE)
    except Exception as exc:
        QMessageBox.critical(panel, "导入失败", f"无法读取布局表：\n{exc}")
        return False
    try:
        count, err = _place_rows(panel, headers, rows)
        if err:
            QMessageBox.warning(panel, "表格格式错误", err)
            return False
        panel.refresh()
        from .topview_panel import _persist_topview_state
        proj = panel._current_project()
        if hasattr(proj, "topview_state"):
            _persist_topview_state(panel, proj)
        notify_user(panel, f"已从表格导入 {count} 条 TopView 设备布局",
                    f"{fmt_note(fmt, '读取')} {path}", kind="success")
        return True
    except Exception as exc:
        QMessageBox.critical(panel, "导入失败", f"无法读取布局表：\n{exc}")
        return False
