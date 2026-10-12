# -*- coding: utf-8 -*-
"""Clip Studio 核心逻辑包（R172 W1c，设计 §3.2/§3.3）—— 纯逻辑，不写 UI。

模块一览（UI 波次 W1d 直接按模块引用，本包不做再导出）：

* `studio_cards`   卡片目录：`CARD_CATALOG` / `PALETTE_GROUPS`（设计 §3.2 全表）。
* `studio_graph`   sheet 图模型：新建 / 归一 / 环检测 / 变更签名。
* `studio_compile` 编译（sheet → clip 字段）与校验（V1-V8）；并 re-export
  `studio_reverse` 的 `reverse_compile` / `recommendations` 作为单一入口。
* `studio_reverse` 反向编译（clip → 建议卡）与推荐（B8）——因单文件 ≤500 行
  架构门禁从 studio_compile 拆出。

约定：卡片目录与字段白名单（`POSTPROCESS_FIELD_NAMES` ∪ 显式特殊键）的唯一
实现点在本包；UI 与应用层不得自建第二份清单。
"""
