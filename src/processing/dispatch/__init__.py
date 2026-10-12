# -*- coding: utf-8 -*-
"""调度台后端包：效果制作统一总线 + 效果缓存 + 表格视图模型（纯数据，无 Qt）。

- `effect_bus.EffectBus`       效果制作总线（只读聚合，按身份键 upsert）
- `effect_cache.EffectCache*`  效果缓存（可追溯快照 + 持久化）
- `fixture_table.*`            表格纯函数（设备→通道地址 / 列 / 单位格式化）
- `table_model.*`              表格模型（节点+设备+值 → 分区/列/行/格）
- `scene_grid.*`               存储页编号槽位纯布局

纪律：本包**不得** import Qt 或任何 UI 模块（保持可无 GUI 单测）；
输出真源永远在 NetworkManager，本包只描述、不输出。
"""
from .effect_bus import EffectBus, EffectNode, CellKey  # noqa: F401
from .effect_cache import (  # noqa: F401
    PRIORITY_MAX,
    EffectCache,
    EffectCacheStore,
    get_effect_cache_store,
)
from .group_resolver import (  # noqa: F401
    build_group_index,
    resolve_for_node,
    resolve_parent_group,
)
from .fixture_table import (  # noqa: F401
    GROUP_OTHER,
    LIGHT_CHANNELS,
    UNIT_DMX,
    UNIT_HEX,
    UNIT_LABELS,
    UNIT_PCT,
    UNITS,
    ColSpec,
    build_columns,
    build_fixture_info,
    channel_addr_for,
    channel_address_at,
    channel_addrs_for,
    channel_label,
    dimmer_source_addrs,
    format_value,
    group_of_channel,
    group_order_index,
    is_virtual_dimmer,
    level_to_dmx,
    norm_channel_type,
)
from .release_registry import ControlReleaseRegistry, get_release_registry  # noqa: F401
from .scene_grid import (  # noqa: F401
    compact_positions,
    index_of_position,
    move_position,
    next_free_position,
    position_for_index,
    slot_at,
    slot_count,
)
from .table_build import build_selection_table, build_table  # noqa: F401
# ★ 包根**不再**从 table_build_agg 再导一次 `build_group_index`：
#   `group_resolver.build_group_index`（→ Dict[str, Tuple[str,int]]，供 UI/解析）
#   与 `table_build_agg.build_group_index`（→ Dict[str,str]，供表格建组索引）
#   **同名但返回类型不同**；此前第二次导入把第一次遮蔽，包根暴露的到底是哪个
#   取决于导入顺序 —— 属隐性坑。现在包根只暴露 group_resolver 版，
#   表格构建侧一律从 `.table_build_agg` 直接 import（`table_build.py` 已如此）。
from .table_model import (  # noqa: F401
    CHANNEL_PANELS,
    CLIP_PANEL,
    COL_CHANNEL,
    COL_CLIP,
    COL_EFFECT,
    COL_GROUPINGS,
    COL_GROUP_LABELS,
    COL_GROUP_MERGE,
    COL_GROUP_SOURCE,
    LAYER_TO_PANEL,
    MIXED_LABEL,
    ROW_MODE_EFFECT_ONLY,
    ROW_MODE_GROUPS,
    ROW_MODE_LABELS,
    ROW_MODES,
    SCOPE_EFFECT_ONLY,
    SCOPE_GROUP_ALL,
    SELECTION_PANEL,
    SELECTION_TITLE,
    SUMMARY_PANEL,
    SUMMARY_TITLE,
    TableCell,
    TableModel,
    TableRow,
    TableSection,
    ValueReader,
    panel_for_layer,
    cell_display,
)
from .trajectory import ControlTrajectoryBuffer  # noqa: F401
from .trajectory_replay import (  # noqa: F401  （包根统一出口，免深层 import）
    ReplayContext,
    build_clip_traj_frame,
    sample_trajectory,
)

__all__ = [
    "EffectBus", "EffectNode", "CellKey",
    "EffectCache", "EffectCacheStore", "get_effect_cache_store", "PRIORITY_MAX",
    "ControlReleaseRegistry", "get_release_registry",
    "ControlTrajectoryBuffer",
    "ReplayContext", "build_clip_traj_frame", "sample_trajectory",
    "resolve_parent_group", "resolve_for_node", "build_group_index",
    "UNIT_DMX", "UNIT_PCT", "UNIT_HEX", "UNITS", "UNIT_LABELS",
    "norm_channel_type", "build_fixture_info", "channel_addr_for",
    "format_value", "level_to_dmx",
    "LIGHT_CHANNELS", "GROUP_OTHER", "ColSpec",
    "channel_addrs_for", "channel_address_at", "is_virtual_dimmer",
    "dimmer_source_addrs", "channel_label", "group_of_channel",
    "group_order_index", "build_columns",
    "TableModel", "TableSection", "TableRow", "TableCell", "ValueReader",
    "build_table", "cell_display",
    "COL_CHANNEL", "COL_EFFECT", "COL_CLIP",
    "CHANNEL_PANELS", "CLIP_PANEL",
    "SCOPE_EFFECT_ONLY", "SCOPE_GROUP_ALL",
    "ROW_MODE_GROUPS", "ROW_MODE_EFFECT_ONLY",
    "COL_GROUP_MERGE", "COL_GROUP_SOURCE", "COL_GROUPINGS", "COL_GROUP_LABELS",
    "ROW_MODES", "ROW_MODE_LABELS", "MIXED_LABEL",
    "SUMMARY_PANEL", "SUMMARY_TITLE", "LAYER_TO_PANEL", "panel_for_layer",
    "SELECTION_PANEL", "SELECTION_TITLE", "build_selection_table",
    "slot_count", "index_of_position", "position_for_index",
    "move_position", "compact_positions", "next_free_position", "slot_at",
]
