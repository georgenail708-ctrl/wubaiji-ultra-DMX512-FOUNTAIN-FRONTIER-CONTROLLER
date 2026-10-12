# -*- coding: utf-8 -*-
"""注释便签伪卡（R183 W2-ST #3）—— 无端口、不入编译的非效果节点。

**为什么进不了 `CARD_CATALOG`**：R183 之前发布的测试将目录规模钉死为 46
（`test_clip_studio_ui_r172` / `test_r183_palette_sections` / `test_r183_b1b2_art_ui`
三处断言 `len(CARD_CATALOG) == 46` 且「卡片栏并集 == 目录」），而本批不拥有那些
测试文件的所有权。因此便签作为**伪卡**放在本模块的 `ANNOTATION_CARDS`，由
`lookup_spec()` 统一并查 —— 功能语义与「CARD_CATALOG 加 note 伪卡」完全一致
（画布可建/可存/可编辑），只是不占卡片栏、不参与效果目录计数。

语义（节点编辑器便签惯例）：

* `ports = ()` → 无桩位：不能拖线、也不能作为连线目标（画布命中即红叉拒绝）；
* 不参与编译（`studio_compile.compile_sheet` 过滤注释卡，见 `is_note`）；
* 文本存 `node["params"]["text"]`（ParamSpec 收敛 + 归一保留，随工程落盘）；
* Z 序与普通卡同层（可自由摆放，通常放框内当说明）。

**为什么不用现成**：现成节点库的注释节点是节点对象 /
自定义 `Node` 子类（PySide + QGraphicsItem 体系，引库有 PyQt6 混用风险）；本仓
画布是单 QWidget 自绘、sheet 是纯 dict，按既定纪律「只搬语义」。
"""
from __future__ import annotations

from src.ui import qascade_theme as _th
from src.ui.clip_studio.studio_cards import CARD_CATALOG, CardSpec, ParamSpec

__all__ = [
    "NOTE_KEY", "GROUP_NOTE", "NOTE_MAX_LEN", "NOTE_SPEC", "ANNOTATION_CARDS",
    "NOTE_FILL", "NOTE_LINE", "NOTE_TEXT", "is_note", "lookup_spec",
    "note_text", "set_note_text",
]

#: 便签卡 key（伪卡；不入 `CARD_CATALOG` 计数）。
NOTE_KEY = "note"
#: 伪卡分组（画布色条查表回退 BORDER_NORMAL，贴纸底色由本模块常量承载）。
GROUP_NOTE = "note"
#: 文本长度上限（防畸形存档塞入巨串；编辑弹窗同样截断）。
NOTE_MAX_LEN = 400
#: 贴纸配色：暖黄底 + 同色系描边 + 深色字（全部取 qascade_theme 既有令牌，不新造色）。
NOTE_FILL = _th.KEYFRAME_GOLD
NOTE_LINE = _th.KEYFRAME_GOLD
NOTE_TEXT = _th.BG_ROOT

#: 便签规格：无端口、唯一参数 = 多行文本。
NOTE_SPEC = CardSpec(
    key=NOTE_KEY, group=GROUP_NOTE, name="注释便签",
    desc="画布便签：双击编辑纯文本（不上墙、不参与编译、不可接线）。",
    icon="note", art="note", ports=(),
    params=(ParamSpec("text", "便签文本", "str", default=""),),
)

#: 伪卡目录（`lookup_spec` 并查的第二源；勿复制进 `CARD_CATALOG`）。
ANNOTATION_CARDS = {NOTE_KEY: NOTE_SPEC}


def is_note(node) -> bool:
    """节点是否注释便签（按 key；畸形项 → False）。"""
    return isinstance(node, dict) and str(node.get("key", "")) == NOTE_KEY


def lookup_spec(key) -> CardSpec | None:
    """统一规格查询：效果目录 ∪ 伪卡目录（未知 key → None）。

    画布/归一/绘制一律走本函数；`CARD_CATALOG` 仍是效果卡唯一真源（计数不变）。
    """
    k = str(key)
    return CARD_CATALOG.get(k) or ANNOTATION_CARDS.get(k)


def note_text(node) -> str:
    """便签文本（缺失/畸形 → ""；截断到上限）。"""
    if not isinstance(node, dict):
        return ""
    params = node.get("params")
    raw = params.get("text", "") if isinstance(params, dict) else ""
    return str(raw or "")[:NOTE_MAX_LEN]


def set_note_text(node, text) -> str:
    """写便签文本（就地写 `params["text"]`）；返回截断后的最终值。"""
    value = str(text or "")[:NOTE_MAX_LEN]
    if isinstance(node, dict):
        node.setdefault("params", {})["text"] = value
    return value
