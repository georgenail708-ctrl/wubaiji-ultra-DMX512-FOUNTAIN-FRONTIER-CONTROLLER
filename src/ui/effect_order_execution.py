"""效果计算与 CE 共用编号链、空间相位和缓存失效合同。

默认方向沿操作者建立的编号链推进；显式空间方向和跳过编号才使用
坐标相位。位置表由浏览器的 layout_changed 缓存失效合同提供新对象。
"""
from __future__ import annotations

from src.utils.identity_cache import fixture_ids_signature
from . import effect_direction_groups
from .effect_directions import ORDER_DIRECTIONS, PHASE_DIRECTIONS, direction_phases, normalize


_DERIVED_PHASE_KEYS = ("_matrix_coords", "_matrix_x_span")
_GROUP_KEYS = ("_unit_groups", "_output_spatial_groups", "_fixture_groups")


def coordinate_phase_enabled(params) -> bool:
    """显式空间操作才覆盖编号相位，坐标系本身不能抹掉作者的序。"""
    from .effect_engine import normalize_spatial_scope

    direction = normalize(params.get("_app_direction", "default") or "default")
    return (direction not in ORDER_DIRECTIONS
            and normalize_spatial_scope(params.get("spatial_scope")) == "global"
            and (bool(params.get("_skip_base_order", False))
                 or direction != "default"))


def _inject_order_phases(params, ordered) -> bool:
    direction = normalize(params.get("_app_direction"))
    if ordered is None or direction not in PHASE_DIRECTIONS:
        return False
    keys = [str(fixture.id) for _index, fixture in ordered]
    phases = direction_phases(direction, keys, n=len(keys))
    if phases:
        params["_matrix_coords"] = phases
        params["_matrix_x_span"] = (min(phases.values()), max(phases.values()))
    return True


def per_shape_phase_enabled(params) -> bool:
    """各自坐标系的相位曲线必须以每个实际计算块的编号重新起算。"""
    from .effect_engine import normalize_spatial_scope

    return (normalize_spatial_scope(params.get("spatial_scope")) == "per_shape"
            and normalize(params.get("_app_direction")) in PHASE_DIRECTIONS)


def prepare_params(params, positions, *, ordered=None):
    """派生坐标仅属于本次计算，不写回配方，切换坐标系时清理残留。"""
    from .effect_engine import _inject_matrix_coords

    project_coordinates = coordinate_phase_enabled(params)
    numbered_phase = ordered is not None and normalize(params.get("_app_direction")) in PHASE_DIRECTIONS
    if not (project_coordinates or numbered_phase or not params.keys().isdisjoint(_DERIVED_PHASE_KEYS)):
        return params
    result = dict(params)
    for key in _DERIVED_PHASE_KEYS:
        result.pop(key, None)
    if numbered_phase:
        _inject_order_phases(result, ordered)
    elif project_coordinates:
        _inject_matrix_coords(result, positions)
    return result


def has_independent_groups(params) -> bool:
    """所有既有逐组时钟来源都必须进入 CE 资格判断。"""
    from .effect_engine import normalize_spatial_scope

    if normalize_spatial_scope(params.get("spatial_scope")) != "per_shape":
        return False
    return any(isinstance(params.get(key), (list, tuple)) and params.get(key)
               for key in _GROUP_KEYS)


def _control_groups_enabled(params, capabilities) -> bool:
    groups = params.get("_control_groups")
    mode = str(params.get("_control_mode", "whole") or "whole").strip().lower()
    if mode == "single":
        groups = capabilities.get("fixed_whole_groups")
    return isinstance(groups, (list, tuple)) and bool(groups)


def requires_python_finish(params) -> bool:
    """扁平 C 内核尚未实现的后处理必须走现有 Python 唯一实现。"""
    from . import effect_engine

    capabilities = params.get("_light_caps")
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    if _control_groups_enabled(params, capabilities):
        return True
    fill_map = capabilities.get("fill_map")
    if isinstance(fill_map, dict) and fill_map:
        return True
    registered_mask = (effect_engine._route_mask is not None
                       or effect_engine._route_mask_map is not None)
    return bool(params.get("route_mask_enabled", False)) and registered_mask


def ordered_fixtures(engine, fixtures, positions, *, sort_mode, base_order_ids, params):
    """复用唯一排序链；同对象改序或改组也必须让播放缓存失效。"""
    from .effect_engine import apply_effect_direction, apply_spatial_scope, sort_fixtures

    unit_groups = params.get("_unit_groups")
    key = (fixture_ids_signature(fixtures), id(positions), len(positions or {}),
           sort_mode, fixture_ids_signature(base_order_ids),
           params.get("spatial_scope"), effect_direction_groups.groups_signature(params),
           params.get("_app_direction"), bool(params.get("_skip_base_order", False)))
    guards = (fixtures, positions, base_order_ids, unit_groups)
    ordered = engine._sort_chain_cache.get(guards, key)
    if ordered is not None:
        return ordered
    ordered = sort_fixtures(
        fixtures, positions, sort_mode, base_order_ids=base_order_ids,
        skip_base_order=bool(params.get("_skip_base_order", False)))
    ordered = apply_spatial_scope(ordered, positions, params, sort_mode=sort_mode)
    ordered = apply_effect_direction(
        ordered, positions, params.get("_app_direction", "default"),
        unit_groups=effect_direction_groups.direction_groups(engine, ordered, params))
    engine._sort_chain_cache.put(guards, key, ordered)
    return ordered
