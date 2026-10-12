# -*- coding: utf-8 -*-
"""Draw 大页（R182 用户定形态）——卡片画布 + 连线，纯 QWidget 自绘。

包结构（与 `src/ui/clip_studio` 同架构视觉体系，独立实现、不 import 其私有件）：
  · `draw_cards.py`  —— 源卡/规则卡目录与规格（数据唯一来源）
  · `draw_graph.py`   —— 画布图结构操作（建卡/连线/删除/命中；纯逻辑）
  · `draw_compile.py` / `draw_decompile.py` —— 图 ↔ 调制状态编译/反编译入口
  · `draw_canvas_paint.py` —— 画布绘制子过程（点阵网格/卡片/连线/HUD）
  · `draw_canvas.py` —— 画布（缩放/平移/交互；禁 QGraphicsView）
  · `draw_panel.py`  —— 左栏源卡库 + 画布 + 顶栏（应用/关闭）

数据编译走 `src/engine/audio_mod_draw_compile`（图 → Project.audio_modulation_state
的 sources/rules/target_ch_types/draw_graph 字段）。
"""
from .draw_panel import AudioModDrawPanel

__all__ = ["AudioModDrawPanel"]
