"""Reuse the current Effect form only when its complete render contract matches.

References: Qt v6.11 QStackedWidget ownership and QSignalBlocker; R141 restore.
No widget pool or output owner is created here. Rebuilding remains the fallback.
"""
from contextlib import ExitStack
from copy import deepcopy

from PyQt6.QtCore import QSignalBlocker
from PyQt6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox


def _freeze(value):
    """Capture all schema fields, including nested choices and unified slots."""
    kind = type(value)
    if value is None or kind in (bool, int, float, str, bytes):
        return kind, value
    if isinstance(value, dict):
        return kind, tuple((_freeze(key), _freeze(item)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return kind, tuple(_freeze(item) for item in value)
    if hasattr(value, "__dict__"):
        return kind, _freeze(vars(value))
    raise TypeError("Unsupported Effect schema field")


def _render_specs(preset):
    from . import effect_browser as browser

    channel, regular = None, []
    for spec in preset.params:
        if browser._is_global_speed_param(spec) or spec.key == "spatial_scope":
            continue
        if channel is None and browser._is_channel_choice_param(spec):
            channel = spec
        else:
            regular.append(spec)
    if not any(spec.key == "_joy_amp_axis" for spec in regular):
        regular.append(browser._JOY_AMP_AXIS_PARAM)
    return channel, regular


def signature(preset):
    """Fail closed if a custom specification cannot be captured completely."""
    try:
        return _freeze((preset.params, getattr(preset, "unified_slots", []),
                        preset.target_channels, _render_specs(preset)))
    except (AttributeError, TypeError, RecursionError):
        return None


def _refresh_specs(panel, preset, specs):
    channel, regular = specs
    panel._current_preset = preset
    panel._current_unified_slots = list(getattr(preset, "unified_slots", []) or [])
    panel._channel_param_spec = channel
    panel._channel_param_key = channel.key if channel is not None else None
    panel._channel_form_specs = regular if channel is not None else []
    panel._param_preset_id = str(preset.id)


def _matching_choices(panel, form, channel):
    from . import effect_browser as browser
    from . import effect_operating_preferences as preferences

    allowed = browser._module_channel_choices(panel)
    default = preferences.default_channel(panel, channel, allowed)
    default = preferences.form_channel(default, allowed, None)
    if panel._channel_form_type(form) != default:
        return False
    expected = [(key, browser._module_channel_label(panel, key)) for key in allowed]
    if default not in allowed:
        expected.append((default, f"{default}（当前工程不可用）"))
    combo = form["combo"]
    actual = [(combo.itemData(index), combo.itemText(index)) for index in range(combo.count())]
    return actual == expected


def _reusable_form(panel, preset, channel):
    """Keep complex/restored or independently running layouts on the old path."""
    forms = getattr(panel, "_channel_forms", [])
    if channel is None or len(forms) != 1 or panel._param_widgets:
        return None
    if getattr(preset, "unified_slots", None) or panel._running_states_for_current_preset():
        return None
    form = forms[0]
    if not _matching_choices(panel, form, channel):
        return None
    if not _matching_widget_context(panel, form):
        return None
    return form


def _resettable(widget, browser):
    if isinstance(widget, browser.RangeParamWidget):
        return True
    control = getattr(widget, "_ctrl", None)
    if isinstance(control, QComboBox):
        default = widget._spec.default
        return control.findData(default) >= 0 or control.findText(str(default)) >= 0
    return isinstance(control, (browser._ParamNumSlider, QDoubleSpinBox, QSpinBox,
                                QCheckBox, browser.WingDirectionToolbar))


def _matching_widget_context(panel, form):
    from . import effect_browser as browser

    project = panel._project_object()
    return all(getattr(widget, "_project", project) is project and _resettable(widget, browser)
               for widget in form["params"].values())


def _reset_form(panel, form, specs):
    """Use original value setters without publishing intermediate author edits."""
    defaults = {spec.key: deepcopy(spec.default) for spec in specs}
    defaults.update(playing=False, speed_factor=1.0)
    widgets = {id(widget): widget for widget in form["params"].values()}
    with ExitStack() as stack:
        for widget in widgets.values():
            stack.enter_context(QSignalBlocker(widget))
            if hasattr(widget, "_low_spec"):
                widget._set_pair_values(widget._low_spec.default, widget._high_spec.default)
        form.pop("_unrepresented_params", None)
        form.pop("_ltp_takeover_pending", None)
        form["virtual_t"] = 0.0
        form["last_real_t"] = 0.0
        panel._apply_channel_form_values(form, defaults)
    panel._update_channel_form_item(form)
    panel._set_active_channel_form(form)


def reuse(panel, preset_id, sig):
    """Same ID preserves edits; another compatible ID gets fresh form defaults."""
    from .effect_engine import get_engine

    if sig is None or getattr(panel, "_param_signature", None) != sig:
        return False
    if getattr(panel, "_param_widgets", None) is None:
        return False
    preset = get_engine().get_preset(preset_id)
    if preset is None:
        return False
    specs = _render_specs(preset)
    if str(getattr(panel, "_param_preset_id", "")) == str(preset_id):
        _refresh_specs(panel, preset, specs)
        return True
    form = _reusable_form(panel, preset, specs[0])
    if form is None:
        return False
    _refresh_specs(panel, preset, specs)
    _reset_form(panel, form, specs[1])
    return True
