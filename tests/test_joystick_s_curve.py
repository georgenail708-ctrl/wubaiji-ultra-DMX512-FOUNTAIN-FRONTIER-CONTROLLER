"""Match actual S-preset UI, saved rules, and hardware-free Qt routing."""

import pytest
from PyQt6.QtCore import QObject, QSettings, pyqtSignal
from PyQt6.QtWidgets import QApplication

from src.ui import joystick_control_binding as binding
from src.ui import joystick_control_binding_dialogs as dialogs
from src.ui import midi_joystick_driver, physical_joystick_driver


class _IdlePhysicalDriver(QObject):
    axis3d_changed = pyqtSignal(float, float, float, float)


class _IdleMidiDriver(QObject):
    axis3d_changed = pyqtSignal(float, float, float)


@pytest.fixture(scope="session")
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def isolated_binding(monkeypatch, tmp_path, application):
    """Only replace device factories and QSettings destination; all logic is real."""
    for variable in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(variable, str(tmp_path))
    physical = _IdlePhysicalDriver()
    midi = _IdleMidiDriver()
    monkeypatch.setattr(binding, "get_physical_joystick_driver", lambda: physical)
    monkeypatch.setattr(physical_joystick_driver, "get_physical_joystick_driver", lambda: physical)
    monkeypatch.setattr(midi_joystick_driver, "get_midi_joystick_driver", lambda: midi)
    managers = []

    def settings_factory(organization, application_name):
        assert (organization, application_name) == (binding._SETTINGS_ORG, binding._SETTINGS_APP)
        settings = QSettings(str(tmp_path / "bindings.ini"), QSettings.Format.IniFormat)
        settings.setFallbacksEnabled(False)
        return settings

    monkeypatch.setattr(binding, "QSettings", settings_factory)

    def new_manager():
        manager = binding.JoystickControlBindManager()
        manager.disable_simulator()
        managers.append(manager)
        return manager

    monkeypatch.setattr(dialogs, "get_joystick_bind_manager", lambda: managers[-1])
    return new_manager, physical


@pytest.fixture
def s_dialog(isolated_binding):
    new_manager, _ = isolated_binding
    manager = new_manager()
    rule = manager.add_binding("dimmer", "x")
    dialog = dialogs.BindingMappingDialog(rule)
    dialog._curve_cb.setCurrentIndex(dialog._curve_cb.findData("s_curve"))
    yield dialog
    dialog.close()


@pytest.mark.parametrize("gamma", [0.1, 0.5, 1.0, 2.0, 3.0, 5.0])
def test_s_curve_matches_actual_ui_preset_at_1001_inputs(s_dialog, gamma):
    # S hides Gamma; its preset points are fixed by _on_curve_preset().
    assert s_dialog._gamma_row.isHidden()
    editor = s_dialog._curve_editor
    assert editor.get_points() == [(0.0, 0.0), (0.25, 25.5), (0.5, 127.5), (0.75, 229.5), (1.0, 255.0)]
    for i in range(1001):
        value = i / 1000
        assert binding._apply_curve(value, "s_curve", gamma) == pytest.approx(editor.evaluate(value) / 255)


@pytest.mark.parametrize("gamma", [0.1, 0.5, 1.0, 2.0, 3.0, 5.0])
def test_s_curve_endpoints_midpoint_bounded_monotone_and_symmetric(gamma):
    assert binding._apply_curve(0.0, "s_curve", gamma) == 0.0
    assert binding._apply_curve(0.5, "s_curve", gamma) == 0.5
    assert binding._apply_curve(1.0, "s_curve", gamma) == 1.0
    values = [binding._apply_curve(i / 1000, "s_curve", gamma) for i in range(1001)]
    assert all(0.0 <= value <= 1.0 for value in values)
    assert values == sorted(values)
    assert [a + b for a, b in zip(values, reversed(values), strict=True)] == pytest.approx([1.0] * len(values))


@pytest.mark.parametrize("knot", [0.25, 0.5, 0.75])
def test_s_curve_is_continuous_at_preset_knots(knot):
    left = binding._apply_curve(knot - 1e-8, "s_curve")
    right = binding._apply_curve(knot + 1e-8, "s_curve")
    assert 0.0 <= right - left < 1e-6


def test_s_curve_clamps_out_of_range_inputs():
    assert binding._apply_curve(-10, "s_curve") == 0.0
    assert binding._apply_curve(10, "s_curve") == 1.0


@pytest.mark.parametrize("invert", [False, True])
@pytest.mark.parametrize("deadzone", [0.0, 0.2, 0.5])
def test_dialog_apply_settings_reload_and_axis_signal_match_preview(isolated_binding, invert, deadzone):
    new_manager, physical = isolated_binding
    manager = new_manager()
    rule = manager.add_binding("dimmer", "x")
    dialog = dialogs.BindingMappingDialog(rule)
    try:
        dialog._curve_cb.setCurrentIndex(dialog._curve_cb.findData("s_curve"))
        dialog._invert_cb.setChecked(invert)
        dialog._deadzone_sl.setValue(round(deadzone * 100))
        dialog._apply()
        assert rule.curve == "s_curve"
        assert rule.invert == invert
        assert rule.deadzone == deadzone
        loaded = new_manager()
        restored = loaded.get_binding("dimmer")
        assert restored.to_dict() == rule.to_dict()
        received = []
        loaded.set_value_handler(lambda _rule, value: received.append(value))
        inputs = [0.0, 0.1, 0.249, 0.25, 0.375, 0.499, 0.5, 0.501, 0.625, 0.75, 0.9, 1.0]
        for value in inputs:
            physical.axis3d_changed.emit(value, 0.5, 0.5, 0.5)
            norm = binding._normalize_axis(value, deadzone, invert)
            assert received[-1] == round(dialog._curve_editor.evaluate(norm))
        assert received[0] == (255 if invert else 0)
        assert received[-1] == (0 if invert else 255)
        assert received[inputs.index(0.5)] == 128
    finally:
        dialog.close()


@pytest.mark.parametrize("axis_scale", [0.0, 0.5, 1.0])
def test_s_curve_effect_signal_respects_extended_range_and_axis_scale(isolated_binding, axis_scale):
    new_manager, physical = isolated_binding
    manager = new_manager()
    manager.add_effect_param_binding(
        "motor_x",
        "amplitude",
        "Amplitude",
        curve="s_curve",
        range_lo=100,
        range_hi=1000,
    )
    manager.set_axis_scale("x", axis_scale)
    received = []
    manager.effect_param_changed.connect(lambda *values: received.append(values))
    for value in [0.0, 0.5, 1.0]:
        physical.axis3d_changed.emit(value, 0.5, 0.5, 0.5)
    assert received == [
        ("motor_x", "amplitude", round(550 - 450 * axis_scale)),
        ("motor_x", "amplitude", 550),
        ("motor_x", "amplitude", round(550 + 450 * axis_scale)),
    ]


@pytest.mark.parametrize(
    ("curve", "gamma", "steps", "expected"),
    [
        ("linear", 1.0, 8, [0.0, 0.25, 0.5, 0.75, 1.0]),
        ("gamma", 2.0, 8, [0.0, 0.0625, 0.25, 0.5625, 1.0]),
        ("inverted", 1.0, 8, [1.0, 0.75, 0.5, 0.25, 0.0]),
        ("stepped", 1.0, 4, [0.0, 1 / 3, 2 / 3, 1.0, 1.0]),
        ("feel_balance", 0.0, 8, [0.0, 0.25, 0.5, 0.75, 1.0]),
    ],
)
def test_other_curve_modes_keep_their_existing_values(curve, gamma, steps, expected):
    actual = [binding._apply_curve(value, curve, gamma, steps) for value in [0.0, 0.25, 0.5, 0.75, 1.0]]
    assert actual == pytest.approx(expected)


@pytest.mark.parametrize("limits", [(0, 255), (10, 200), (100, 1000), (17, 981), (0, 1), (100, 100)])
def test_s_curve_axis_signals_match_exact_preview_integers_at_1001_inputs(isolated_binding, limits):
    new_manager, physical = isolated_binding
    manager = new_manager()
    rule = manager.add_effect_param_binding("motor_x", "amplitude", "Amplitude", range_lo=limits[0], range_hi=limits[1])
    received = []
    manager.effect_param_changed.connect(lambda _channel, _param, value: received.append(value))
    dialog = dialogs.BindingMappingDialog(rule)
    try:
        dialog._curve_cb.setCurrentIndex(dialog._curve_cb.findData("s_curve"))
        dialog._apply()
        for i in range(1001):
            value = i / 1000
            physical.axis3d_changed.emit(value, 0.5, 0.5, 0.5)
            normalized = binding._normalize_axis(value, rule.deadzone, rule.invert)
            assert received[-1] == round(dialog._curve_editor.evaluate(normalized)), (limits, value)
    finally:
        dialog.close()


def test_s_preview_uses_edited_effect_range_before_apply(isolated_binding):
    new_manager, _ = isolated_binding
    manager = new_manager()
    rule = manager.add_effect_param_binding("motor_x", "amplitude", "Amplitude")
    dialog = dialogs.BindingMappingDialog(rule)
    try:
        dialog._curve_cb.setCurrentIndex(dialog._curve_cb.findData("s_curve"))
        dialog._range_lo.setValue(100)
        dialog._range_hi.setValue(1000)
        assert (rule.range_lo, rule.range_hi) == (0, 255)
        for value, expected in [(0.0, 100), (0.5, 550), (1.0, 1000)]:
            dialog._update_preview(value)
            assert dialog._preview_val.text() == str(expected)
    finally:
        dialog.close()


@pytest.mark.parametrize("limits", [(10, 200), (100, 1000), (17, 981)])
def test_changing_range_after_selecting_s_preset_updates_preview_and_output(isolated_binding, limits):
    new_manager, physical = isolated_binding
    manager = new_manager()
    rule = manager.add_effect_param_binding("motor_x", "amplitude", "Amplitude")
    received = []
    manager.effect_param_changed.connect(lambda _channel, _param, value: received.append(value))
    dialog = dialogs.BindingMappingDialog(rule)
    try:
        dialog._curve_cb.setCurrentIndex(dialog._curve_cb.findData("s_curve"))
        dialog._range_lo.setValue(limits[0])
        dialog._range_hi.setValue(limits[1])
        lo, hi = map(float, limits)
        assert dialog._curve_editor.get_points() == [
            (0.0, lo),
            (0.25, lo + (hi - lo) * 0.1),
            (0.5, lo + (hi - lo) * 0.5),
            (0.75, lo + (hi - lo) * 0.9),
            (1.0, hi),
        ]
        dialog._apply()
        for i in range(1001):
            value = i / 1000
            physical.axis3d_changed.emit(value, 0.5, 0.5, 0.5)
            normalized = binding._normalize_axis(value, rule.deadzone, rule.invert)
            assert received[-1] == round(dialog._curve_editor.evaluate(normalized)), (limits, value)
    finally:
        dialog.close()
