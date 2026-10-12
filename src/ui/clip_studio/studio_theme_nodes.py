# -*- coding: utf-8 -*-
"""studio_theme_nodes.py — 节点卡片像素常量沉淀（R183-B1）。

来源：r183-candidates §一（raw 已验证）—— 全套数字中**本批采用的四项**：

* 卡片圆角 4.0
* 标题底条 QColor(0,0,0,80) + padding(3,2) + 圆角 3
* header 高 = 文字高 + 4.0
* 描边 常态 0.8px / 选中 1.2px
* Backdrop 分组框（R183 深查② #5）：默认色 (5,129,138)、填充 alpha 50、
  圆角 2.6、描边 0.8 / 选中 1.0、``Z_VAL_BACKDROP = -2``（本仓画在边**与卡之前**）

出处（常量名对照 仓）：

  （NODE_SHAPE=NodeEnum.ROUNDED_RECT 圆角、NODE_SEL_STROKE、Z_VAL_BACKDROP=-2 等）
  （ABSTRACT_NODE_DRAW：标题底条 base_color 叠 QColor(0,0,0,80)、padding=(3,2)、
   圆角 3、header 高 = 文字高 + 4.0；描边未选中 0.8 / 选中 1.2）
  （BackdropNodeItem.paint：``color[:3] + (50,)`` 填充、radius 2.6、border 0.8/1.0、
   BackdropSizer(26)；``_min_size = 80, 80``）
  （``calc_backdrop_size``：成员包围盒四周 ``padding = 40``；``wrap_nodes``）

**明确不采用**（本仓定稿，见 ai-memory/_references/b1b2-adoption.md）：
卡片 160×60 默认尺寸 / 端口 12px+热区 15px / 连线 1.2 与四色 / 选中白 30 叠加
—— 端口与连线为 R172 定稿（任务红线），画布为单 widget 自绘。Backdrop 的
**Z 序**（`Z_VAL_BACKDROP = -2` 在 pipe -1 之下）本仓等价落在 paintEvent 顺序
「框 → 边 → 卡」；``wrap_nodes`` 的显式成员列表换成本仓「卡中心落在框内」几何
判定（框选后无需额外包裹一步，见 `studio_frames.frame_members`）。

纪律：本模块只放常量与出处注释，不含绘制逻辑；绘制方
`clip_studio.studio_canvas_paint` 逐项引用（改值只改这里 + r183-candidates 表）。
"""
from __future__ import annotations

#: 卡片圆角半径（参考: 圆角 4.0；旧值 6.0）。
NODE_RADIUS = 4.0

#: 标题底条：深色叠加 alpha（参考 node_base: QColor(0, 0, 0, 80)）。
HEADER_DARK_ALPHA = 80

#: 标题底条内缩 padding（水平, 垂直），padding = (3, 2)。
HEADER_PAD = (3.0, 2.0)

#: 标题底条圆角（参考 node_base: 圆角 3）。
HEADER_RADIUS = 3.0

#: header 高 = 标题文字高 + 4.0（参考 node_base: header 高公式）。
HEADER_TEXT_EXTRA = 4.0

#: 未选中描边宽（参考: 0.8px；旧值 1.2）。
STROKE_NORMAL = 0.8

#: 选中/状态描边宽（参考: 1.2px；旧值 2.2）。
STROKE_SELECTED = 1.2

# ── Backdrop 分组框（R183 深查② #5；qgraphics/node_backdrop.py）──
#: 填充色（默认 ``(5, 129, 138, 255)``）；
#: 本仓存 ``#RRGGBB`` 串入 sheet，绘制时叠 BACKDROP_ALPHA。
BACKDROP_COLOR = "#05818A"

#: 填充透明度（绘制层: ``color[:3] + (50,)`` —— 半透明，压在卡下方）。
BACKDROP_ALPHA = 50

#: 圆角（绘制层: rounded rect radius 2.6）。
BACKDROP_RADIUS = 2.6

#: 描边宽：常态 0.8 / 选中 1.0（绘制层: border = 0.8 / 选中 1.0）。
BACKDROP_STROKE = 0.8
BACKDROP_STROKE_SELECTED = 1.0

#: 标题条底色叠加 alpha（照卡片标题底条同款 0,0,0,80 → 半透明更深一档）。
BACKDROP_TITLE_ALPHA = 70

#: 右下角缩放手柄边长（屏幕像素；缩放手柄(26) 的比例化版本）。
BACKDROP_HANDLE_PX = 12.0

__all__ = [
    "NODE_RADIUS", "HEADER_DARK_ALPHA", "HEADER_PAD", "HEADER_RADIUS",
    "HEADER_TEXT_EXTRA", "STROKE_NORMAL", "STROKE_SELECTED",
    "BACKDROP_COLOR", "BACKDROP_ALPHA", "BACKDROP_RADIUS",
    "BACKDROP_STROKE", "BACKDROP_STROKE_SELECTED", "BACKDROP_TITLE_ALPHA",
    "BACKDROP_HANDLE_PX",
]
