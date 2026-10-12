# -*- coding: utf-8 -*-
"""主控过渡卡（R182）：按通道类型单独设置淡入淡出比例 + 过渡曲线，画布内嵌控件。

**数据通路（grep 实证 2026-09-29，勿凭印象改）**

* 比例 → `fade_type_ratio_in` / `fade_type_ratio_out`：引擎
  `PlaybackEngine._clip_channel_fade_gains` 逐通道类型缩放过渡窗口（CE
  `CeDirBlock.ratio_in/ratio_out` 同源消费；原高级处理 Tab2 滑杆写的就是这两个字段）。
* 曲线 → `transition_curve_in` / `transition_curve_out`：引擎 `_clip_transition_curve`
  → `_clip_transition_gain`；CE `_CURVE_IDX` 同表（`fade_curve_fi/fo` 行）。
  ★ **曲线在引擎里是 clip 级**（没有任何逐通道类型曲线的消费点）→ 行内「曲线」
  下拉是整体曲线的视图：点任一行 = 改整体两侧（想分设淡入/淡出走双击弹窗）。
  这是对任务书「按通道类型调曲线」的实证收敛，已记录偏差。
* 应用 → `studio_apply` 直写族（R182 把 `transition_curve_in/out` 加入白名单）+
  引擎缓存失效；编译入口 `studio_compile._comp_master_transition`（唯一）。
* 反向 → `studio_reverse._rev_master_transition`（比例/曲线非默认即还原本卡）。

**曲线词表**：取 `studio_cards.TRANSITION_CURVE_CHOICES`（引擎 `TRANSITION_CURVES`
真词汇 linear/easein/easeout/easeinout/bezier）。前四项与 `EASE_FUNCS_V` 的
linear/ease_in/ease_out/ease_in_out **公式逐点一致**（t / t² / 1−(1−t)² / t²(3−2t)）；
`EASE_FUNCS_V` 的 exponential 引擎无对应实现 → **不提供**（提供了没有消费点 =
假接线，本仓明令禁止）。标签风格同 `matrix_slideshow.output_shaper.CURVE_CHOICES`。

几何全部按 `px`（画布 `_px(1.0)`：屏幕像素 → 世界单位）缩放，缩放画布时内嵌控件
保持屏幕恒定大小；行高与列宽由 `row_layout` 统一裁决（绘制/命中同源，防几何漂移）。
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPen

from src.ui import qascade_theme as _th
from src.ui.clip_studio.studio_cards import TRANSITION_CURVE_CHOICES, _RATIO_TYPES

__all__ = [
    "CARD_KEY", "ROWS_KEY", "CURVE_CHOICES", "CURVE_LABELS", "RATIO_MAX",
    "RATIO_NEUTRAL",
    "norm_ch_type", "normalize_rows", "default_rows", "rows_of", "is_master_card",
    "curve_of", "row_layout", "hit", "ratio_from_pos", "set_ratio", "cycle_curve",
    "row_add", "row_del", "paint_rows", "paint_add_button", "compile_fields",
    "types_for_project", "prefill_rows", "prefill_from_panel",
    "hit_node", "press_control", "drag_ratio",
]

#: 卡片 key（studio_cards 目录 + 面板拖入预填 + 编译分派共用唯一真源）。
CARD_KEY = "master_transition"
ROWS_KEY = "rows"                 # 节点 params 里的动态行表键
#: 比例量程（%，0=完全不动，100=原样，200=过渡时间翻倍）。
#: ★ 偏差记录：任务书写「滑杆 0-100%」，但引擎/原高级处理 Tab2 的比例真源是
#: `0.0~2.0`（`fade_type_ratio_*` 的钳制值与滑杆量程都是 0~200%）。只做 0-100
#: 会让反向编译把 clip 上已存的 150% 静默压成 100%（打开即丢数据）→ 按真源 0-200
#: 实现（滑杆 100% 处画中性刻度，语义一眼可辨）。
RATIO_MAX = 200
RATIO_NEUTRAL = 100               # 100% = 引擎默认（不写字段）

#: 曲线下拉：(引擎词汇, 中文标签)——见模块 docstring。
CURVE_CHOICES = TRANSITION_CURVE_CHOICES
CURVE_LABELS = tuple(label for _v, label in CURVE_CHOICES)
_CURVE_VALUES = tuple(v for v, _l in CURVE_CHOICES)
#: 无 clip 可推断通道类型时的预填（三大粗族，覆盖绝大多数现场）。
_TYPES_DEFAULT = ("dimmer", "motor_x", "pump")
#: 通道类型 → 中文（引擎粗类型口径；未收录类型直接显示原键）。
_TYPE_LABELS = dict(_RATIO_TYPES)
#: 内嵌控件几何（屏幕像素；乘 px 变世界单位）。
_ROW_H, _GAP = 17.0, 3.0
_TYPE_W, _CURVE_W, _SLIDER_MIN, _DEL_W = 36.0, 42.0, 28.0, 11.0
_ADD_W, _ICON_W, _PAD, _ROUND = 14.0, 18.0, 5.0, 3.0


# ── 数据模型（rows = [{"type", "ratio_in", "ratio_out"}]，比例 0..200 整数 %）──

def norm_ch_type(ch_type) -> str:
    """通道类型归一到引擎粗类型口径（`_norm_ratio_channel_type`：去 `_fine` 后缀）。

    引擎侧是上帝类静态方法（冻结，禁新增）→ 本模块按同一规则本地实现；
    两侧一致性由 tests/test_r182_studio_visual.py 对拍钉死。
    """
    ct = str(ch_type or "").strip().lower()
    return ct[:-len("_fine")] if ct.endswith("_fine") else ct


def _clamp_ratio(value) -> int:
    try:
        return max(0, min(RATIO_MAX, int(round(float(value)))))
    except (TypeError, ValueError, OverflowError):
        # OverflowError：工程档 1e999 之类解析成 inf，round(inf) 抛（审计 P4）
        return RATIO_NEUTRAL


def _row_of(item) -> dict | None:
    """单项归一；非法项（非 dict / type 空）→ None。"""
    if not isinstance(item, dict):
        return None
    ctype = norm_ch_type(item.get("type"))
    if not ctype:
        return None
    return {"type": ctype, "ratio_in": _clamp_ratio(item.get("ratio_in", RATIO_NEUTRAL)),
            "ratio_out": _clamp_ratio(item.get("ratio_out", RATIO_NEUTRAL))}


def normalize_rows(value) -> list:
    """任意输入 → 规范行表（保序、按 type 去重、比例钳到 0..100；永不抛）。"""
    out, seen = [], set()
    for item in (value if isinstance(value, (list, tuple)) else ()):
        row = _row_of(item)
        if row is None or row["type"] in seen:
            continue
        seen.add(row["type"])
        out.append(row)
    return out


def default_rows(types=None) -> list:
    """按通道类型序列造 100% 行（空序列 → 默认三粗族）。"""
    seq = [t for t in (types or ()) if norm_ch_type(t)] or list(_TYPES_DEFAULT)
    return normalize_rows([{"type": t} for t in seq])


def rows_of(node) -> list:
    return normalize_rows((node.get("params") or {}).get(ROWS_KEY))


def is_master_card(node) -> bool:
    """本卡是否为主控过渡卡（绘制/命中/编译的分派谓词）。"""
    return str(node.get("key", "")) == CARD_KEY


def curve_of(node, key: str = "curve_in") -> str:
    """读曲线参数（非法值回 linear）。"""
    val = str((node.get("params") or {}).get(key, "linear") or "linear")
    return val if val in _CURVE_VALUES else "linear"


def _put_rows(node, rows) -> None:
    node.setdefault("params", {})[ROWS_KEY] = normalize_rows(rows)


def prefill_rows(node, types=None) -> bool:
    """空行表按通道类型预填（已有行不动）；返回是否写入。"""
    if not is_master_card(node) or rows_of(node):
        return False
    _put_rows(node, default_rows(types))
    return True


def prefill_rows_from_clip(node, clip, types=None) -> bool:
    """空行表按通道类型预填，并回填 clip 上**已存**的比例/曲线（审计修）。"""
    if not is_master_card(node) or rows_of(node):
        return False
    if clip is None:
        return prefill_rows(node, types)
    from .studio_canvas_master import existing_rows, prefill_curves
    rows = existing_rows(clip, types)
    if not rows:
        return prefill_rows(node, types)
    _put_rows(node, normalize_rows(rows))
    prefill_curves(node, clip)
    return True




def row_add(node) -> bool:
    """追加一个尚未使用的类型（默认三族优先，再按 `_RATIO_TYPES`）；无候选 → False。"""
    if not is_master_card(node):
        return False
    rows = rows_of(node)
    used = {r["type"] for r in rows}
    for ctype in list(_TYPES_DEFAULT) + [t for t, _l in _RATIO_TYPES]:
        if ctype not in used:
            rows.append({"type": ctype, "ratio_in": RATIO_NEUTRAL,
                         "ratio_out": RATIO_NEUTRAL})
            _put_rows(node, rows)
            return True
    return False


def row_del(node, index: int) -> bool:
    """删第 index 行（越界 → False）。"""
    rows = rows_of(node)
    if not (0 <= int(index) < len(rows)):
        return False
    del rows[int(index)]
    _put_rows(node, rows)
    return True


def cycle_curve(node, index: int = 0) -> bool:
    """行内曲线下拉点击 → 整体曲线前进到下一档（clip 级，见模块 docstring）。

    `index` 保留（点哪一行都改整体），供命中回调签名统一。
    """
    if not is_master_card(node):
        return False
    cur = curve_of(node)
    nxt = _CURVE_VALUES[(_CURVE_VALUES.index(cur) + 1) % len(_CURVE_VALUES)]
    params = node.setdefault("params", {})
    params["curve_in"] = nxt
    params["curve_out"] = nxt
    return True


# ── 几何（绘制 / 命中同源；px = 画布 _px(1.0)）──────────────────────────────

def _row_cells(left: float, top: float, widths, gap: float, row_h: float) -> dict:
    """单行四列矩形（widths = type/curve/slider/del 世界宽，从左到右，gap 间距）。"""
    cell: dict = {}
    x = left
    for name, w in zip(("type", "curve", "slider", "del"), widths):
        cell[name] = QRectF(x, top, w, row_h)
        x += w + gap
    cell["rect"] = QRectF(left, top, x - gap - left, row_h)
    return cell


def row_layout(card_rect: QRectF, header_h: float, n_rows: int, px: float) -> dict:
    """行几何：`{"rows": [cell...], "add": QRectF, "hidden": n}`。

    cell = `{"rect", "type", "curve", "slider", "del"}`；超出卡高的行不布局
    （`hidden` 记被裁行数）。行高/列宽按 px 屏幕恒定。
    """
    gap = _GAP * px
    left = card_rect.left() + _PAD * px
    right = card_rect.right() - _PAD * px
    top = card_rect.top() + header_h + 3.0 * px
    row_h = _ROW_H * px
    fixed = (_TYPE_W + _CURVE_W + _DEL_W) * px + 3.0 * gap
    widths = (_TYPE_W * px, _CURVE_W * px, max(_SLIDER_MIN * px, (right - left) - fixed),
              _DEL_W * px)
    bottom = card_rect.bottom() - 4.0 * px
    rows: list = []
    for i in range(max(0, int(n_rows))):
        y = top + i * (row_h + gap)
        if y + row_h * 0.8 > bottom:
            break
        rows.append(_row_cells(left, y, widths, gap, row_h))
    add = QRectF(right - (_ADD_W + _ICON_W) * px - gap, card_rect.top() + 3.0 * px,
                 _ADD_W * px, max(8.0 * px, header_h - 6.0 * px))
    return {"rows": rows, "add": add, "hidden": max(0, int(n_rows) - len(rows))}


def hit(pos, card_rect: QRectF, header_h: float, node, px: float):
    """命中内嵌控件 → ("add", -1) | ("ratio"|"curve"|"del", 行号) | None。"""
    lay = row_layout(card_rect, header_h, len(rows_of(node)), px)
    if lay["add"].contains(pos):
        return "add", -1
    for i, cell in enumerate(lay["rows"]):
        for name in ("del", "curve", "slider"):
            if cell[name].contains(pos):
                return ("ratio" if name == "slider" else name), i
    return None


def ratio_from_pos(pos, slider_rect: QRectF) -> int:
    """滑块 x 位置 → 比例 0..100（几何由 `row_layout` 同一真源给出）。"""
    if slider_rect.width() <= 1.0:
        return RATIO_NEUTRAL
    return _clamp_ratio((pos.x() - slider_rect.left()) / slider_rect.width() * RATIO_MAX)


def set_ratio(node, index: int, pos, slider_rect: QRectF) -> int | None:
    """按位置写第 index 行的淡入/淡出比例（行内单一滑杆=两向同值）；越界 → None。"""
    rows = rows_of(node)
    if not (0 <= int(index) < len(rows)):
        return None
    val = ratio_from_pos(pos, slider_rect)
    rows[int(index)]["ratio_in"] = val
    rows[int(index)]["ratio_out"] = val
    _put_rows(node, rows)
    return val


def slider_rect_at(node, card_rect: QRectF, header_h: float, index: int, px: float) -> QRectF:
    """第 index 行的滑杆矩形（命中/拖动共用；越界 → 空矩形）。"""
    lay = row_layout(card_rect, header_h, int(index) + 1, px)
    if not (0 <= int(index) < len(lay["rows"])):
        return QRectF()
    return lay["rows"][int(index)]["slider"]


# ── 绘制（画布自绘控件；主题令牌 + 既有控件色，无新色值）────────────────────

def _txt(p, rect: QRectF, text: str, *, size: int, color: str,
         align=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
         bold: bool = False) -> None:
    """小字助手（控件文案统一：字号/颜色/对齐一处定义）。"""
    f = QFont()
    f.setPixelSize(size)
    f.setBold(bold)
    p.setFont(f)
    p.setPen(QPen(QColor(color)))
    p.drawText(rect, align, text)


def _draw_type(p, cell, row) -> None:
    _txt(p, cell["type"], _TYPE_LABELS.get(row["type"], row["type"]),
         size=9, color=_th.TEXT_TERTIARY)


def _draw_curve(p, cell, curve: str) -> None:
    rect = cell["curve"]
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(_th.BG_INPUT)))
    p.drawRoundedRect(rect, _ROUND, _ROUND)
    label = str(next((lab for v, lab in CURVE_CHOICES if v == curve), ""))
    _txt(p, rect, f"{label} ▾", size=9, color=_th.TEXT_PRIMARY,
         align=Qt.AlignmentFlag.AlignCenter)


def _draw_slider(p, cell, ratio: int) -> None:
    """比例滑杆：底槽 BG_INPUT + 已填充段 SELECT_BLUE + 白圆钮 + 100% 中性刻度。"""
    rect = cell["slider"]
    inset = rect.height() * 0.28
    track = rect.adjusted(0.0, inset, 0.0, -inset)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(_th.BG_INPUT)))
    p.drawRoundedRect(track, _ROUND, _ROUND)
    frac = max(0, min(RATIO_MAX, int(ratio))) / float(RATIO_MAX)
    p.setBrush(QBrush(QColor(_th.SELECT_BLUE)))
    p.drawRoundedRect(QRectF(track.left(), track.top(),
                             max(1.0, track.width() * frac), track.height()), _ROUND, _ROUND)
    # 100% 中性刻度（= 引擎默认；一眼分辨「调过 / 没调过」）
    neutral_x = track.left() + track.width() * (RATIO_NEUTRAL / float(RATIO_MAX))
    p.setPen(QPen(QColor(_th.TEXT_DISABLED), 1.0))
    p.drawLine(QPointF(neutral_x, track.top()), QPointF(neutral_x, track.bottom()))
    knob = track.height()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(_th.TEXT_PRIMARY)))
    p.drawEllipse(QRectF(track.left() + track.width() * frac - knob / 2.0,
                         track.center().y() - knob / 2.0, knob, knob))


def _draw_values(p, cell, row) -> None:
    """行右端比例数值（淡入/淡出不同值时显示 `in/out`）。"""
    text = (f"{row['ratio_in']}%" if row["ratio_in"] == row["ratio_out"]
            else f"{row['ratio_in']}/{row['ratio_out']}")
    cap = cell["del"]
    _txt(p, QRectF(cap.left() - 30.0, cap.top(), 32.0, cap.height()),
         text, size=9, color=_th.TEXT_SECONDARY)


def _draw_delete(p, cell) -> None:
    _txt(p, cell["del"], "×", size=10, color=_th.TEXT_DISABLED,
         align=Qt.AlignmentFlag.AlignCenter)


def paint_rows(p, node, card_rect: QRectF, header_h: float, px: float) -> None:
    """画内嵌控件区（每行：类型 / 曲线下拉 / 比例滑杆 / ×；行内曲线=整体曲线视图）。"""
    rows = rows_of(node)
    curve = curve_of(node)
    lay = row_layout(card_rect, header_h, len(rows), px)
    for i, cell in enumerate(lay["rows"]):
        _draw_type(p, cell, rows[i])
        _draw_curve(p, cell, curve)
        _draw_slider(p, cell, rows[i]["ratio_in"])
        _draw_values(p, cell, rows[i])
        _draw_delete(p, cell)
    if lay["hidden"]:
        _txt(p, QRectF(card_rect.left() + 3.0, card_rect.bottom() - 13.0, 30.0, 12.0),
             f"…+{lay['hidden']}", size=9, color=_th.TEXT_DISABLED)


def paint_add_button(p, card_rect: QRectF, header_h: float, px: float) -> None:
    """header 右端的「＋ 加一行」按钮（画在图标左侧）。"""
    rect = row_layout(card_rect, header_h, 0, px)["add"]
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(QColor(_th.BG_INPUT)))
    p.drawRoundedRect(rect, _ROUND, _ROUND)
    _txt(p, rect, "+", size=10, color=_th.TEXT_PRIMARY,
         align=Qt.AlignmentFlag.AlignCenter, bold=True)


# ── 编译（唯一入口：studio_compile._comp_master_transition 调用）────────────

def compile_fields(node) -> dict:
    """卡 → `{"transition_curve_in/out"}` ∪ 非 100% 的 `fade_type_ratio_in/out`。

    * 曲线**恒写**（否则「从 bezier 切回 linear」在差量下发里不可表达）；
    * 比例全 100% 的类型不写（= 引擎默认）；行删除后该类型的比例键由会话层差量
      （`studio_session.diff_fields`）复位为 Clip 默认 `{}`。
    """
    out = {"transition_curve_in": curve_of(node, "curve_in"),
           "transition_curve_out": curve_of(node, "curve_out"),
           "transitions_optimized": "studio"}
    d_in, d_out = {}, {}
    for row in rows_of(node):
        if row["ratio_in"] != RATIO_NEUTRAL:
            d_in[row["type"]] = row["ratio_in"] / 100.0
        if row["ratio_out"] != RATIO_NEUTRAL:
            d_out[row["type"]] = row["ratio_out"] / 100.0
    if d_in:
        out["fade_type_ratio_in"] = d_in
    if d_out:
        out["fade_type_ratio_out"] = d_out
    return out


# ── clip 通道类型推断 + 面板预填 ────────────────────────────────────────────

def types_for_project(project, clip) -> list:
    """clip 涉及设备的通道类型（粗类型序，去重）——原高级处理 Tab2 同款口径。"""
    if clip is None or project is None:
        return []
    defs, insts = _fixture_maps(project)
    out: list = []
    for fid in _clip_fixture_ids(clip, project):
        out.extend(t for t in _types_of_fixture(insts.get(fid), defs) if t not in out)
    return out


def _fixture_maps(project) -> tuple:
    """工程 → (def_id → 设备定义, fixture_id → 设备实例) 两张查表。"""
    defs = {getattr(fd, "id", None): fd for fd in getattr(project, "fixture_defs", []) or []}
    insts = {getattr(fi, "id", None): fi for fi in getattr(project, "fixtures", []) or []}
    return defs, insts


def _clip_fixture_ids(clip, project) -> list:
    """clip 的 base order 设备 id（为空 → 全工程设备，照原高级处理口径）。"""
    fids = list(getattr(clip, "base_order_fixture_ids", []) or [])
    return fids or [getattr(fi, "id", None) for fi in getattr(project, "fixtures", []) or []]


def _types_of_fixture(fixture, defs) -> list:
    """单台设备的通道类型（按 defs 查定义；无设备/无定义 → 空）。"""
    fd = defs.get(getattr(fixture, "def_id", None)) if fixture is not None else None
    out = []
    for ct in (getattr(fd, "channels", []) or []) if fd is not None else ():
        ctype = norm_ch_type(ct)
        if ctype:
            out.append(ctype)
    return out


def _last_master_node(sheet) -> dict | None:
    """sheet 内最后一张主控过渡卡（面板拖入/快捷创建后的预填目标）。"""
    nodes = (sheet.get("graph") or {}).get("nodes") or []
    return next((n for n in reversed(nodes) if is_master_card(n)), None)


def _clip_types(panel, sheet_id: str) -> list:
    """绑定 clip 的通道类型并集（面板注入的 list_clips/project_getter 口径）。"""
    getter = getattr(panel, "_project_getter", None)
    project = getter() if getter else None
    types: list = []
    for clip in panel._bound_clips(str(sheet_id)):
        types.extend(t for t in types_for_project(project, clip) if t not in types)
    return types


def prefill_from_panel(panel, sheet_id: str) -> bool:
    """面板拖入/快捷创建主控过渡卡后：空行表按绑定 clip 的通道类型预填。"""
    sheet = panel._sheet_by_id(str(sheet_id))
    if sheet is None:
        return False
    node = _last_master_node(sheet)
    if node is None:
        return False
    return prefill_rows(node, _clip_types(panel, str(sheet_id)))


# ── 画布交互胶水（宿主 = ClipStudioCanvas；命中/按下/拖动三段，控 CC）───────────
#
# 画布交互混入只调这四个入口：几何与参数写读全部留在本模块（绘制/命中同源）。

def hit_node(pos, card_rect: QRectF, header_h: float, node, px: float):
    """非主控卡 → None；否则控件命中（见 `hit`）。"""
    if not is_master_card(node):
        return None
    return hit(pos, card_rect, header_h, node, px)


def press_control(kind: str, index: int, node) -> bool:
    """点击类控件（add / del / curve）→ 是否真改（滑杆由 `drag_ratio` 处理）。"""
    if kind == "add":
        return row_add(node)
    if kind == "del":
        return row_del(node, index)
    if kind == "curve":
        return cycle_curve(node, index)
    return False


def drag_ratio(node, index: int, pos, slider: QRectF) -> bool:
    """滑杆拖动一帧：写入成功且该帧值有变化 → True（宿主据此置 changed 标记）。"""
    rows = rows_of(node)
    if not (0 <= int(index) < len(rows)):
        return False
    before = rows[int(index)]["ratio_in"]
    val = set_ratio(node, index, pos, slider)
    return val is not None and val != before
