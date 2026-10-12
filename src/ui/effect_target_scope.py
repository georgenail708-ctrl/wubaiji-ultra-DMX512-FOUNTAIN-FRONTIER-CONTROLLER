"""Actual Effect targets are separate from mother-order and UI inheritance metadata.

Existing ordered selection, versioned cache and per-cell state remain the owners.
An explicitly empty selection must not become the legacy unbound ALL scope.
"""
from __future__ import annotations

from src.core.fixture_selection import ordered_members as ordered_members


def is_explicit(panel) -> bool:
    return bool(getattr(panel, "_scope_explicit", False)
                or getattr(panel, "_selected_group_ids", None))


def timeline_fixtures(browser, programmer):
    """显式空目标不回退全集；未绑定的旧入口仍复用 Programmer 选择。"""
    fixtures = browser._get_active_fixtures() if hasattr(browser, "_get_active_fixtures") else []
    if fixtures or is_explicit(browser):
        return fixtures
    return programmer._current_fixtures_selected or programmer._current_fixtures


def current_scope(panel):
    if not is_explicit(panel):
        return None
    ids = getattr(panel, "_selected_group_ids", [])
    base = getattr(panel, "_selected_group_base_order_ids", [])
    return ids, (base or ids)


def assign_targets(panel, ids, base, *, follow=False, explicit=True) -> None:
    """Assign editor targets; running owners are bound by explicit Play/Apply.

    ``follow`` remains accepted for old callers, but selection cannot rebind an
    existing owner. This is equally true for authored and recalled effects.
    """
    panel._selected_group_ids = list(ids)
    panel._selected_group_base_order_ids = list(base)
    panel._scope_explicit = explicit


def _follows_editor(key, state, previous) -> bool:
    return (len(key) >= 2 and str(key[0]).startswith("manual:")
            and isinstance(state, dict) and not state.get("recalled", False)
            and set(state.get("group_ids", [])) == set(previous))


def restore_live_editor_view(panel) -> None:
    """Deselecting keeps the visible recipe; bound owners remain independent."""
    from .effect_owner_edit import restore_child_recipe

    states = getattr(panel, "_external_states", {})
    ids = getattr(panel, "_selected_group_ids", [])
    if not ids:
        return
    if restore_child_recipe(panel):
        return
    if panel._is_playing and any(
            _follows_editor(key, states[key], ids)
            for key in panel._running_keys_for_current_selection()):
        return
    panel._restore_group_ui_state()


def editor_recipe(panel):
    """Store the active form along with the existing per-mother recipe."""
    form = panel._active_channel_form()
    return {"selected_id": panel._selected_id,
            "params": panel._capture_current_param_values(),
            "active_channel": panel._channel_form_type(form) if form else ""}


def restore_editor_recipe(panel, state, apply_params):
    from . import effect_catalog_curation, effect_dispatch_state

    if not state or not effect_catalog_curation.can_restore_preset(panel, state.get("selected_id")):
        return
    with effect_dispatch_state._preserve_transport(panel):
        panel._on_preset_click(state["selected_id"], save_current=False)
        apply_params(panel, dict(state.get("params", {}) or {}))
        active = state.get("active_channel")
        form = next((form for form in panel._channel_forms
                     if panel._channel_form_type(form) == active), None)
        if form is not None:
            panel._set_active_channel_form(form)
        panel._sync_transport_controls_to_active_form()


def has_empty_editor(panel) -> bool:
    """An empty authoring selection waits for targets while retaining its state."""
    return any(str(key[0]).startswith("manual:")
               and not state.get("recalled", False)
               and not state.get("group_ids")
               and state.get("params", {}).get("_output_scope_explicit", False)
               for key, state in getattr(panel, "_external_states", {}).items())


def capture_runtime_params(panel, params, ids):
    """Resolve missing author metadata once, retaining an archived recipe."""
    bound = bound_params(panel, params)
    result = panel._inject_output_spatial_groups(bound, ids)
    result.update(bound)
    return result


def frame_params(panel, params, ids, owner):
    """Bound owners consume their own recipe; the editor supplies previews only."""
    if isinstance(owner, dict) and "group_ids" in owner:
        return params
    return panel._inject_output_spatial_groups(params, ids)


def strip_joy_amp(panel, params, channel, owner, override_keys):
    """A running owner uses its captured axis even while another form is shown."""
    if not params or not any(key in params for key in override_keys):
        return params
    axis = (params.get("_joy_amp_axis", "自动")
            if isinstance(owner, dict) and "group_ids" in owner
            else panel._joy_amp_axis_for_channel(channel, params))
    if axis != "关闭":
        return params
    return {key: value for key, value in params.items() if key not in override_keys}


def project_groups(panel):
    """Reuse the project group model even while its editor is still lazy."""
    group_panel = panel._fixture_group_panel
    if group_panel is not None:
        return group_panel.get_groups()
    from .fixture_group_panel import FixtureGroup
    project = panel._project_object()
    rows = getattr(project, "fixture_groups", []) or []
    return [FixtureGroup.from_dict(row) for row in rows
            if isinstance(row, dict) and row.get("id")]


def matches_saved_scope(panel, project) -> bool:
    state = getattr(project, "effect_browser_state", {}) or {}
    if not isinstance(state, dict) or "selected_group_ids" not in state:
        return False
    saved_ids = state["selected_group_ids"]
    explicit = state.get("scope_explicit", bool(saved_ids))
    return (saved_ids == getattr(panel, "_selected_group_ids", [])
            and state.get("selected_group_base_order_ids", []) ==
            getattr(panel, "_selected_group_base_order_ids", [])
            and bool(explicit) == is_explicit(panel))


def refresh_group_choices(panel) -> None:
    from PyQt6.QtCore import QSignalBlocker
    project = panel._project_object()
    keep_scope = (getattr(panel, "_group_list_project", None) is project
                  or matches_saved_scope(panel, project))
    previous = panel._group_combo.currentData()
    with QSignalBlocker(panel._group_combo):
        panel._group_combo.clear()
        panel._group_combo.addItem("全部设备", None)
        for group in project_groups(panel):
            panel._group_combo.addItem(group.name, group.id)
        index = panel._group_combo.findData(previous)
        panel._group_combo.setCurrentIndex(max(0, index))
    panel._group_list_project = project
    if not keep_scope:
        panel._on_group_combo_changed(0)


def spatial_target_ids(panel, params, fixture_ids):
    if fixture_ids is not None:
        return fixture_ids
    if "_output_fixture_ids" in params:
        return params["_output_fixture_ids"]
    return panel._effective_output_scope_ids()[0]


def empty_targets_are_all(panel, params=None) -> bool:
    if isinstance(params, dict):
        return not bool(params.get("_output_scope_explicit", False))
    return not is_explicit(panel)


def bound_params(panel, params) -> dict:
    result = dict(params or {})
    result.setdefault("_output_scope_explicit",
                      "_output_fixture_ids" in result or is_explicit(panel))
    return result


def selection_key(panel, ids, key_for_ids) -> str:
    if not ids and is_explicit(panel):
        return "__empty__"
    return key_for_ids(ids)


def bound_group_key(panel, params, ids, key_for_ids) -> str:
    explicit = bound_params(panel, params)["_output_scope_explicit"]
    return "__empty__" if explicit and not ids else key_for_ids(ids)


def state_for_save(panel) -> dict:
    return {"scope_explicit": is_explicit(panel)}


def restore_explicit_scope(panel, state) -> None:
    value = state.get("scope_explicit")
    panel._scope_explicit = (value if isinstance(value, bool)
                             else bool(getattr(panel, "_selected_group_ids", [])))
