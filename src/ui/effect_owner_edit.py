"""Resolve a child editor against existing Effect owners without moving them.

Membership is always the captured runtime membership, never the mother order.
Exact cells win; inheritance needs one unambiguous containing membership set.
Selection is read-only. Only an actual parameter edit writes subset metadata.
"""
from __future__ import annotations

# These are real author controls despite their legacy private-looking names.
# Captured membership, ordering and grouping metadata remain owner-only.
EDITABLE_AUTHOR_FIELDS = frozenset({
    "_app_direction", "_app_scope", "_skip_base_order", "_joy_amp_axis",
})


def _candidates(panel, channel):
    for key, state in getattr(panel, "_external_states", {}).items():
        if not isinstance(state, dict) or state.get("preset_id") != panel._selected_id:
            continue
        channels = [str(ch) for ch in state.get("ch_types", []) if str(ch)]
        if channel and channels != [channel]:
            continue
        yield key, state


def running_keys(panel, channel="") -> list:
    if not panel._selected_id:
        return []
    ids, base = panel._effective_output_scope_ids()
    group_key = panel._current_live_selection_key()
    candidates = list(_candidates(panel, channel))
    exact = [key for key, state in candidates
             if panel._external_state_group_matches(key, state, ids, group_key)]
    if exact or not ids:
        return exact
    selected = set(ids)
    parents = [(key, panel._external_state_fixture_id_set(state))
               for key, state in candidates
               if selected < panel._external_state_fixture_id_set(state)]
    preferred = [(key, members) for key, members in parents if members == set(base)]
    parents = preferred or parents
    memberships = {frozenset(members) for _key, members in parents}
    if len(memberships) != 1:
        return []
    return [key for key, _members in parents]


def is_child_edit(panel, state) -> bool:
    selected = set(getattr(panel, "_selected_group_ids", []) or [])
    return bool(selected and selected < panel._external_state_fixture_id_set(state))


def edit_params(panel, params, state, changed_key) -> dict:
    """Keep the owner's output binding and grouping during a child edit."""
    if not is_child_edit(panel, state):
        return panel._params_with_current_output_scope(params)
    result = dict(state.get("params", {}) or {})
    # Preserve captured metadata while accepting every actual author control.
    # Selection-dependent grouping must not shrink per-shape or whole controls.
    result.update({key: value for key, value in params.items()
                   if not str(key).startswith("_") or key in EDITABLE_AUTHOR_FIELDS})
    result.setdefault("_output_fixture_ids", list(state.get("group_ids", [])))
    result.setdefault("_output_base_order_ids", list(state.get("base_order_ids", [])))
    if changed_key == "control_mode":
        result["_control_mode"] = panel._resolved_control_mode()
    if changed_key == "subset_delay_phase":
        result["_subset_ids"] = list(panel._selected_group_ids)
    return result


def restore_child_recipe(panel) -> bool:
    """Use the owner's saved editor recipe even for a newly hand-picked subset."""
    from . import effect_dispatch_state, effect_target_scope
    from .effect_browser import _group_key_from_ids, _apply_param_values_smart

    keys = running_keys(panel)
    if not keys:
        return False
    state = panel._external_states[keys[0]]
    if not is_child_edit(panel, state):
        return False
    recipe = panel._group_states.get(_group_key_from_ids(state.get("group_ids")))
    if recipe:
        effect_target_scope.restore_editor_recipe(panel, recipe, _apply_param_values_smart)
    else:
        states = [panel._external_states[key] for key in keys]
        with effect_dispatch_state._preserve_transport(panel):
            panel._restore_running_effect_ui_state(states)
    return True
