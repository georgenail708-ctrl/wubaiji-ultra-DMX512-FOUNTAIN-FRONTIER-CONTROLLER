"""Preserve restored channel recipes until their actual author controls edit them.

The existing widgets remain the runtime parameter source. This adapter retains
the saved shape only for serialization, including unknown legacy parameters.
New forms still serialize every registered default.
"""
from __future__ import annotations

from copy import deepcopy


def widget_values(form):
    values = {}
    seen = set()
    for key, widget in (form.get("params", {}) or {}).items():
        if hasattr(widget, "get_values"):
            if id(widget) not in seen:
                seen.add(id(widget))
                values.update(widget.get_values())
        else:
            values[key] = widget.get_value()
    return values


def forget(form):
    for key in ("_author_params", "_author_dirty", "_author_widget_values", "_author_preset"):
        form.pop(key, None)


def restore(panel, form, params):
    """Hydrate widgets through the original API without treating signals as edits."""
    form["_restoring_author_params"] = True
    try:
        panel._apply_channel_form_values(form, params)
        form["_author_params"] = deepcopy({
            key: value for key, value in (params or {}).items()
            if key not in {"playing", "speed_factor"}})
        form["_author_dirty"] = set()
        form["_author_widget_values"] = widget_values(form)
        form["_author_preset"] = panel._selected_id
    finally:
        form.pop("_restoring_author_params", None)


def mark_edit(panel, key, value):
    """An actual widget signal owns its field, even when edited back to default."""
    sender = panel.sender()
    for form in panel._channel_forms or []:
        widgets = form.get("params", {}) or {}
        if (form.get("_restoring_author_params") or "_author_params" not in form
                or widgets.get(key) is not sender):
            continue
        dirty = form.setdefault("_author_dirty", set())
        dirty.add(key)
        if hasattr(sender, "get_values"):
            baseline = form.get("_author_widget_values", {})
            dirty.update(name for name, value in sender.get_values().items()
                         if baseline.get(name) != value)
    try:
        panel.controlTrajectoryEvent.emit(f"effect|param|{key}", value)
    except Exception:
        pass


def edit_values(form, values):
    """Explicit Reset/Random operations own requested fields even without a signal."""
    if "_author_params" in form:
        form.setdefault("_author_dirty", set()).update(
            key for key in values if key in (form.get("params", {}) or {}))


def capture(panel, form, current):
    if form.get("_author_preset") != panel._selected_id:
        forget(form)
    if "_author_params" not in form:
        return current
    params = deepcopy(form["_author_params"])
    params.update({key: deepcopy(current[key]) for key in form.get("_author_dirty", ())
                   if key in current})
    return params


def prepare_preset(panel, preset_id):
    """Compatible widget reuse for a different preset creates a new recipe."""
    for form in panel._channel_forms or []:
        if form.get("_author_preset") != preset_id:
            forget(form)


def editor_recipe(panel, key, current):
    """Retain legacy group metadata without adding an untouched active-page field."""
    original = panel._group_states.get(key)
    if not isinstance(original, dict) or original.get("selected_id") != current["selected_id"]:
        return current
    result = deepcopy(original)
    result.update(selected_id=current["selected_id"], params=current["params"])
    forms = (original.get("params", {}) or {}).get("_channel_forms", [])
    original_active = original.get("active_channel")
    if original_active is None and forms:
        original_active = forms[0].get("ch_type", "")
    if "active_channel" in original or current.get("active_channel") != original_active:
        result["active_channel"] = current.get("active_channel", "")
    return result
