"""Explain the current width capability through existing parameter controls."""
from .effect_width_capabilities import condition


def _parameters(panel):
    form = panel._active_channel_form()
    widgets = form["params"] if form is not None else panel._param_widgets
    params = {key: widget.get_value() for key, widget in widgets.items()
              if hasattr(widget, "get_value")}
    if form is not None:
        params["ch_type"] = panel._channel_form_type(form)
    return widgets, params


def refresh(panel):
    from .effect_engine import get_engine
    engine = get_engine()
    fn = engine._compute_fns.get(getattr(panel, "_selected_id", ""))
    if fn is None:
        return
    widgets, params = _parameters(panel)
    widget = widgets.get("effect_width")
    if widget is None:
        return
    preset = engine.get_preset(panel._selected_id)
    enabled, hint = condition(fn, params, getattr(preset, "target_channels", ()))
    original = getattr(widget, "_width_original_hint", None)
    if original is None:
        original = widget.toolTip()
        widget._width_original_hint = original
    widget._ctrl.setEnabled(enabled)
    widget.setToolTip(hint or original)
    widget._ctrl.setToolTip(hint or original)


def current_params(panel):
    params = panel._get_current_params()
    refresh(panel)
    return params


def sync_form(panel):
    panel._sync_transport_controls_to_active_form()
    refresh(panel)


def finish_parameters(panel, decorate):
    decorate(panel)
    refresh(panel)
