"""Actual per-channel axis UI, physical signal routing and memory output."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("channel", ["motor_x", "motor_y", "motor_z", "dimmer", "global"])
@pytest.mark.parametrize("axis", ["自动", "X轴", "Y轴", "Z轴", "关闭"])
@pytest.mark.parametrize("owner", [False, True])
def test_effect_channel_axis_selection(channel, axis, owner, tmp_path):
    _invoke(channel, axis, owner, tmp_path)


@pytest.mark.parametrize("curve", ["linear", "gamma", "stepped", "inverted", "feel_balance", "s_curve"])
@pytest.mark.parametrize("moving", [False, True])
def test_channel_axes_owner_scene_roundtrip(curve, moving, tmp_path):
    _invoke("roundtrip", curve, moving, tmp_path)



def test_auto_and_unrecognized_legacy_choices_are_exact_passthrough():
    from types import SimpleNamespace

    from src.ui.effect_target_scope import strip_joy_amp

    keys = ("joy_amplitude_x", "joy_amplitude_y", "joy_amplitude_z")
    for axis in ("自动", "", None, "old-unknown", [], {}, 17):
        panel = SimpleNamespace(_joy_amp_axis_for_channel=lambda *_args, value=axis: value)
        for owner in (None, {"group_ids": ["2", "4", "6"]}):
            for values in ({}, {keys[0]: 0.0}, {keys[1]: None}, {keys[2]: -.5},
                           {keys[0]: .25, keys[1]: -.75, keys[2]: None}):
                params = dict(values, _joy_amp_axis=axis, speed=0.0)
                assert strip_joy_amp(panel, params, "motor_x", owner, keys) is params


def _invoke(channel, axis, owner, tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QASCADE_CT_ENGINE="0", QASCADE_NUMBA="0",
               HOME=str(tmp_path), USERPROFILE=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / "config"),
               XDG_CACHE_HOME=str(tmp_path / "cache"))
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(root),
                             channel, axis, str(int(owner)), str(tmp_path)],
                            cwd=root, env=env, text=True, capture_output=True, timeout=90, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "result.json").exists(), "Worker must complete the real scenario"


def _run_case(root, channel, axis, owner, output):
    import json
    import socket
    import threading
    from copy import deepcopy

    sys.path.insert(0, str(root))

    def forbidden(*args, **kwargs):
        raise AssertionError("No hardware threads or sockets are allowed")

    socket.socket = forbidden
    threading.Thread.start = forbidden
    import numpy as np
    from PyQt6.QtCore import QObject, QThread, pyqtSignal
    from PyQt6.QtWidgets import QApplication, QComboBox

    QThread.start = forbidden
    from src.ui import joystick_control_binding as bindings
    from src.ui import midi_joystick_driver, physical_joystick_driver

    class IdlePhysical(QObject):
        axis3d_changed = pyqtSignal(float, float, float, float)

    class IdleMidi(QObject):
        axis3d_changed = pyqtSignal(float, float, float)

    drivers = []

    def physical_factory():
        driver = IdlePhysical()
        drivers.append(driver)
        return driver

    def midi_factory():
        driver = IdleMidi()
        drivers.append(driver)
        return driver

    bindings.get_physical_joystick_driver = physical_factory
    physical_joystick_driver.get_physical_joystick_driver = physical_factory
    midi_joystick_driver.get_midi_joystick_driver = midi_factory
    from src.core.models import FixtureDefinition, FixtureInstance, Project
    from src.processing.dispatch.effect_cache import EffectCacheStore
    from src.ui.effect_browser import EffectBrowserWidget
    from src.ui.programmer_panel import ProgrammerPanel

    class Sink:
        def __init__(self):
            self._layers = {}

        def update_layer(self, layer, universe, data, active=None, **kwargs):
            self._layers.setdefault(layer, {})[universe] = (np.asarray(data).copy(), np.asarray(active).copy())

        def clear_layer(self, layer):
            self._layers.pop(layer, None)

    def sample(sink):
        data, active = sink._layers["effect"][1]
        return {int(i + 1): int(data[i]) for i in np.flatnonzero(active)}

    def panel_pair(project):
        browser, programmer, sink = EffectBrowserWidget(), ProgrammerPanel(project), Sink()
        browser._project, browser._nm, browser._programmer_panel = project, sink, programmer
        programmer._effect_browser_cache, programmer._nm_ref = browser, sink
        programmer.load_fixture_group("__topview_live__", ["2", "4", "6"], "even", ["2", "4", "6"])
        browser.set_live_fixture_selection(["2", "4", "6"], ["1", "2", "3", "4", "5", "6"])
        return browser, programmer, sink

    def row_for(programmer):
        return next(row for row in programmer._ch_list._rows if "motor_x" in row._ch_types)

    def mode_combo(programmer):
        row = row_for(programmer)
        row._ensure_controller_built()
        return next(combo for combo in row.findChildren(QComboBox) if combo.findText("幅度控制模式") >= 0)

    def input_values(browser, xyz):
        browser._joy_mgr.disable_simulator()
        browser._joy_mgr._driver.axis3d_changed.emit(*(value / 255 for value in xyz), .5)

    def tick(browser, sink):
        browser._play_timer.stop()
        browser._tick_play()
        return sample(sink)

    def recall(browser, entries):
        for entry in entries:
            assert browser.dispatch_recall_state(entry["node"], entry["state"])
        browser._play_timer.stop()

    app = QApplication([])
    EffectBrowserWidget._populate_list = lambda self: None
    project = Project()
    channels = ["motor_x", "motor_y", "motor_z", "dimmer"]
    project.fixture_defs = [FixtureDefinition(id="d", name="motor", channels=channels, channel_count=4)]
    project.fixtures = [FixtureInstance(id=str(i), def_id="d", universe=1, start_channel=(i - 1) * 4 + 1)
                        for i in range(1, 7)]
    browser, programmer, sink = panel_pair(project)
    mode_combo(programmer).setCurrentText("幅度控制模式")
    manager = browser._joy_mgr
    manager.clear_all_bindings()
    if channel == "roundtrip":
        row = row_for(programmer)
        # Programmer maps physical axes into logical amplitude keys first.
        for a, target in zip("xyz", ("joy_amplitude_z", "joy_amplitude_x", "joy_amplitude_y"), strict=True):
            combo = next(c for c in row.findChildren(QComboBox) if c.itemText(0) == f"joy_amplitude_{a}")
            combo.setCurrentText(target)
    for physical_axis in "xyz":
        rule = manager.add_binding(f"motor_{physical_axis}", physical_axis, sub_axis=physical_axis,
                                   space_mode="3d", target_def_id="d")
        rule.deadzone = .05 if channel == "roundtrip" else 0.0
        if channel == "roundtrip":
            rule.curve, rule.gamma, rule.steps, rule.invert = axis, 1.5, 6, physical_axis == "z"
    manager.save()
    input_values(browser, (64, 192, 32))
    original_overrides = dict(browser._param_overrides)
    assert len(original_overrides) == 3
    if channel != "roundtrip":
        assert len(set(original_overrides.values())) == 3
    browser._on_preset_click("joystick_circle" if channel == "global" else "wave_sine")
    if channel == "roundtrip":
        moving = owner
        speed = .8 if moving else 0.0
        selections = {"motor_x": "Y轴", "motor_y": "Z轴", "motor_z": "X轴", "dimmer": "关闭"}
        forms = {}
        for ch in channels:
            form = browser._add_channel_form(ch)
            forms[ch] = form
            browser._apply_channel_form_values(form, {"speed": speed, "min": 0, "max": 255})
            form["params"]["_joy_amp_axis"]._ctrl.setCurrentText(selections[ch])
        # Build a multi-channel owner from the actual captured UI form values.
        assert browser._play_current_selection_as_independent_effect()
        even_key = next(iter(browser._external_states))
        even_owner = browser._external_states[even_key]
        even_params = deepcopy(even_owner["params"])
        browser.set_live_fixture_selection(["1", "3", "5"], ["1", "2", "3", "4", "5", "6"])
        forms = {ch: browser._add_channel_form(ch) for ch in channels}
        for ch in channels:
            browser._apply_channel_form_values(forms[ch], {"speed": speed, "min": 0, "max": 255})
            forms[ch]["params"]["_joy_amp_axis"]._ctrl.setCurrentText("Z轴" if ch == "motor_x" else "关闭")
        assert browser._play_current_selection_as_independent_effect()
        assert len(browser._external_states) == 2, [(k,v["group_ids"]) for k,v in browser._external_states.items()]
        assert browser._external_states[even_key] is even_owner
        assert even_owner["params"] == even_params
        for state in browser._external_states.values():
            state["speed_factor"], state["virtual_t"] = 0.0, .375

        def expected_output(panel, output_sink, overrides):
            reference = {}
            for choice_axis in "xyz":
                panel.set_param_overrides({f"joy_amplitude_{a}": overrides[f"joy_amplitude_{choice_axis}"] for a in "xyz"})
                reference[choice_axis] = tick(panel, output_sink)
            panel.clear_param_overrides()
            reference["off"] = tick(panel, output_sink)
            panel.set_param_overrides(overrides)
            result = {}
            for i in range(1, 7):
                choices = selections if i % 2 == 0 else {ch: "Z轴" if ch == "motor_x" else "关闭" for ch in channels}
                for offset, ch in enumerate(channels):
                    choice_axis = "off" if choices[ch] == "关闭" else choices[ch][0].lower()
                    address = (i - 1) * 4 + offset + 1
                    result[address] = reference[choice_axis][address]
            return result

        expected = expected_output(browser, sink, original_overrides)
        assert tick(browser, sink) == expected, "Each owner/form must use its own explicit logical axis"
        assert browser._param_overrides == original_overrides
        store = EffectCacheStore()
        store.save_from_nodes("mapped axes", browser._bus_nodes(), capture_state=lambda _: browser.dispatch_capture_state())
        project.dispatch_state = store.to_dict()
        path = output / "axes.dmxt"
        project.save(str(path))
        loaded = Project.load(str(path))
        restored = EffectCacheStore()
        restored.from_dict(loaded.dispatch_state)
        entries = restored.list_caches()[0].nodes
        assert len(entries) == 2
        bindings._global_bind_mgr = None
        fresh, fresh_programmer, fresh_sink = panel_pair(loaded)
        mode_combo(fresh_programmer).setCurrentText("幅度控制模式")
        fresh_row = row_for(fresh_programmer)
        for a, target in zip("xyz", ("joy_amplitude_z", "joy_amplitude_x", "joy_amplitude_y"), strict=True):
            combo = next(c for c in fresh_row.findChildren(QComboBox) if c.itemText(0) == f"joy_amplitude_{a}")
            combo.setCurrentText(target)
        fresh._on_preset_click("wave_sine")
        fresh._channel_forms[0]["params"]["_joy_amp_axis"]._ctrl.setCurrentText("关闭")
        editor_before = fresh._capture_current_param_values()
        programmer_before = fresh_programmer.save_state()
        driver_before = fresh._joy_mgr._driver
        input_values(fresh, (64, 192, 32))
        assert fresh._param_overrides == original_overrides
        recall(fresh, entries)
        assert tick(fresh, fresh_sink) == expected
        assert fresh._capture_current_param_values() == editor_before
        assert fresh_programmer.save_state() == programmer_before
        assert fresh._joy_mgr._driver is driver_before
        for xyz in ((192, 32, 224), (0, 255, 128)):
            input_values(fresh, xyz)
            live = dict(fresh._param_overrides)
            assert tick(fresh, fresh_sink) == expected_output(fresh, fresh_sink, live)
            assert fresh._param_overrides == live
        fresh.stop_external()
        input_values(fresh, (64, 192, 32))
        recall(fresh, entries)
        assert tick(fresh, fresh_sink) == expected
        for _ in range(2):
            loaded.dispatch_state = restored.to_dict()
            loaded.save(str(path))
            loaded = Project.load(str(path))
            restored.from_dict(loaded.dispatch_state)
            recall(fresh, restored.list_caches()[0].nodes)
            assert tick(fresh, fresh_sink) == expected
        (output / "result.json").write_text(json.dumps({"curve": axis, "moving": moving, "output": expected}))
        return
    if channel == "global":
        assert not browser._channel_forms
        form = None
        browser._param_widgets["speed"]._ctrl.set_out(0.0)
        axis_widget = browser._param_widgets["_joy_amp_axis"]
    else:
        form = browser._channel_forms[0]
        index = form["combo"].findData(channel)
        assert index >= 0
        form["combo"].setCurrentIndex(index)
        browser._apply_channel_form_values(form, {"speed": 0.0, "min": 0, "max": 255})
        axis_widget = form["params"]["_joy_amp_axis"]
    choice = axis_widget._ctrl
    assert [choice.itemText(i) for i in range(choice.count())] == ["自动", "X轴", "Y轴", "Z轴", "关闭"]
    assert "X/Y/Z" in axis_widget._spec.tooltip
    choice.setCurrentText(axis)
    assert axis_widget.get_value() == axis
    if owner:
        assert browser._play_current_selection_as_independent_effect()
    else:
        if form is not None:
            form["playing"] = True
        browser._is_playing = True
    actual = tick(browser, sink)
    offsets = [0, 1] if channel == "global" else [channels.index(channel)]
    assert set(actual) == {(i - 1) * 4 + offset + 1 for i in (2, 4, 6) for offset in offsets}
    # Oracle: equal axis values make the existing natural-axis selection
    # irrelevant, while all Effect/shape/quantization logic stays production.
    if axis in ("X轴", "Y轴", "Z轴"):
        selected = original_overrides[f"joy_amplitude_{axis[0].lower()}"]
        browser.set_param_overrides({f"joy_amplitude_{a}": selected for a in "xyz"})
    elif axis == "关闭":
        browser.clear_param_overrides()
    expected = tick(browser, sink)
    assert actual == expected, (channel, axis, "actual", actual, "expected", expected, original_overrides)
    browser.set_param_overrides(original_overrides)
    assert tick(browser, sink) == expected
    if axis in ("X轴", "Y轴", "Z轴"):
        selected_key = f"joy_amplitude_{axis[0].lower()}"
        # Missing/None means the chosen source is disconnected, not another axis
        # and not a fabricated zero. Exercise speed-zero engine behavior too.
        for value in ("missing", None, 0.0, -1.0, 1.0):
            trial = dict(original_overrides)
            if value == "missing":
                trial.pop(selected_key)
            else:
                trial[selected_key] = value
            browser.set_param_overrides(trial)
            trial_output = tick(browser, sink)
            if value is None or value == "missing":
                browser.clear_param_overrides()
            else:
                browser.set_param_overrides({f"joy_amplitude_{a}": value for a in "xyz"})
            assert trial_output == tick(browser, sink), (axis, value, trial_output, sample(sink))
        browser.set_param_overrides(original_overrides)
    assert row_for(programmer)._joy_mode == "amplitude"
    assert app is QApplication.instance()
    (output / "result.json").write_text(json.dumps({"channel": channel, "axis": axis, "owner": owner,
                                                   "actual": actual, "expected": expected}))


if __name__ == "__main__" and len(sys.argv) == 7 and sys.argv[1] == "--worker":
    _run_case(Path(sys.argv[2]), sys.argv[3], sys.argv[4], bool(int(sys.argv[5])), Path(sys.argv[6]))
