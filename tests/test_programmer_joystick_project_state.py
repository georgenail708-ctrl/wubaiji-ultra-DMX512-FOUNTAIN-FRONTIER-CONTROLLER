"""Project joystick configuration through real Qt controls, save/load and output.

Each case uses its own process, HOME and INI. Physical/MIDI driver factories and
the NetworkManager output boundary are inert; production UI, bindings, scene
cache, Project serializer, recall and EffectEngine are unchanged. Only the
expensive effect library-list presentation is skipped. No device is initialized.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("mode", ["amplitude", "additive"])
@pytest.mark.parametrize("target", ["x", "y", "z", "off"])
@pytest.mark.parametrize("built", [False, True])
def test_project_joystick_controls_roundtrip(mode, target, built, tmp_path):
    _invoke(mode, target, built, tmp_path)


@pytest.mark.parametrize("payload", ["missing", "bad-version", "bad-target", "foreign-row", "empty-rows"])
def test_legacy_or_invalid_joystick_controls_do_not_override(payload, tmp_path):
    _invoke("compat", payload, True, tmp_path)


@pytest.mark.parametrize("saved", ["additive", "off", "same"])
def test_in_place_project_reload_clears_only_changed_controls(saved, tmp_path):
    _invoke("in-place", saved, True, tmp_path)


def _invoke(mode, target, built, tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QASCADE_CT_ENGINE="0", QASCADE_NUMBA="0",
               HOME=str(tmp_path), USERPROFILE=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / "config"),
               XDG_CACHE_HOME=str(tmp_path / "cache"))
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--worker", str(root), mode, target,
         str(int(built)), str(tmp_path)], cwd=root, env=env, text=True, capture_output=True, timeout=90, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "result.json").exists(), "Worker must execute the scenario to completion"


def _run_case(root, mode, target, built, output):
    import json
    import socket
    import threading

    sys.path.insert(0, str(root))

    def forbidden(*args, **kwargs):
        raise AssertionError("No socket or hardware threads are allowed in this regression")

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
    from src.ui import effect_target_scope
    from src.ui.effect_browser import EffectBrowserWidget
    from src.ui.programmer_panel import ProgrammerPanel

    class Sink:
        def __init__(self):
            self._layers = {}

        def update_layer(self, layer, universe, data, active=None, **kwargs):
            self._layers.setdefault(layer, {})[universe] = (np.asarray(data).copy(), np.asarray(active).copy())

        def clear_layer(self, layer):
            self._layers.pop(layer, None)

    def sample(sink, layer="effect"):
        data, active = sink._layers[layer][1]
        return {int(i + 1): int(data[i]) for i in np.flatnonzero(active)}

    def row_for(panel, def_id="d"):
        return next(row for row in panel._ch_list._rows
                    if row._def_id == def_id and "motor_x" in row._ch_types)

    def controls(row):
        row._ensure_controller_built()
        combos = row.findChildren(QComboBox)
        mode_combo = next(combo for combo in combos if combo.findText("幅度控制模式") >= 0)
        axes = {axis: next(combo for combo in combos
                           if combo.itemText(0) == f"joy_amplitude_{axis}") for axis in "xyz"}
        return mode_combo, axes

    def configure(row, wanted_mode, targets):
        combo, axes = controls(row)
        combo.setCurrentText("幅度控制模式")
        for axis, value in zip("xyz", targets, strict=True):
            axes[axis].setCurrentText(value or "(不使用)")
        # Exercise the real amplitude -> additive clearing callback too.
        if wanted_mode == "additive":
            combo.setCurrentText("叠加模式")

    def configuration(row):
        return row._joy_mode, [getattr(row, f"_joy_amp_{axis}_target") for axis in "xyz"]

    def input_value(manager, value):
        manager.disable_simulator()
        manager._driver.axis3d_changed.emit(value / 255.0, .5, .5, .5)

    app = QApplication([])
    EffectBrowserWidget._populate_list = lambda self: None
    project = Project()
    project.fixture_defs = [
        FixtureDefinition(id="d", name="motor", channels=["motor_x", "motor_y", "motor_z"], channel_count=3),
        FixtureDefinition(id="peer", name="peer", channels=["motor_x"], channel_count=1),
    ]
    project.fixtures = [FixtureInstance(id=str(i), def_id="d", universe=1, start_channel=(i - 1) * 3 + 1)
                        for i in range(1, 7)]
    project.fixtures.append(FixtureInstance(id="peer", def_id="peer", universe=1, start_channel=100))
    browser = EffectBrowserWidget()
    programmer = ProgrammerPanel(project)
    programmer._effect_browser_cache = browser
    programmer.load_fixture_group("__topview_live__", ["2", "4", "6"], "even", ["2", "4", "6"])
    programmer._current_base_order_ids = ["1", "2", "3", "4", "5", "6"]
    row = row_for(programmer)
    if mode == "compat":
        configure(row, "amplitude", ["joy_amplitude_z", None, "joy_amplitude_y"])
        expected = configuration(row)
        saved = programmer.save_state()
        valid = {"version": 1, "rows": [{"def_id": "d", "ch_types": list(row._ch_types),
                                         "mode": "additive", "targets": [None, None, None]}]}
        saved["joystick_controls"] = valid
        if target == "missing":
            saved.pop("joystick_controls")
        elif target == "bad-version":
            valid["version"] = True
        elif target == "bad-target":
            valid["rows"][0]["targets"][0] = {"invalid": True}
        elif target == "foreign-row":
            valid["rows"][0]["def_id"] = "missing-device-type"
        elif target == "empty-rows":
            valid["rows"] = []
        emitted = []
        row.joy_amplitude_changed.connect(emitted.append)
        assert programmer.load_state(saved)
        assert configuration(row_for(programmer)) == expected
        assert emitted == [], "Configuration restoration must not simulate hardware input"
        (output / "result.json").write_text(json.dumps({"compat": target, "result": "passed"}))
        return
    in_place = mode == "in-place"
    in_place_case = target
    if in_place:
        mode = "additive" if target == "additive" else "amplitude"
        target = "off" if target == "off" else "x"
    targets = [None if target == "off" else f"joy_amplitude_{target}", None, "joy_amplitude_x"]
    configure(row, mode, targets)
    peer = row_for(programmer, "peer")
    configure(peer, "additive", [None, "joy_amplitude_z", None])
    peer_configuration = configuration(peer)
    sink = Sink()
    programmer._nm_ref = sink
    browser._project, browser._nm, browser._programmer_panel = project, sink, programmer
    effect_target_scope.assign_targets(browser, ["2", "4", "6"], ["1", "2", "3", "4", "5", "6"])
    manager = browser._joy_mgr
    manager.clear_all_bindings()
    rule = manager.add_binding("motor_x", "x", target_def_id="d")
    rule.deadzone = 0.0
    manager.save()
    input_value(manager, 64)
    programmer._flush_live_output()
    effect_channel = f"motor_{target}" if target != "off" else "motor_x"
    params = {"ch_type": effect_channel, "speed": 0.0, "min": 0, "max": 255,
              "_output_fixture_ids": ["2", "4", "6"], "_output_base_order_ids": ["1", "2", "3", "4", "5", "6"]}
    browser.play_preset_for_type("manual:test", "wave_sine", params, [effect_channel], "index")
    browser._play_timer.stop()
    browser._tick_play()
    before = sample(sink)
    channel_offset = "xyz".index(target) if target != "off" else 0
    assert set(before) == {(i - 1) * 3 + channel_offset + 1 for i in (2, 4, 6)}
    store = EffectCacheStore()
    store.save_from_nodes("amplitude", browser._bus_nodes(), capture_state=lambda panel: browser.dispatch_capture_state())
    project.dispatch_state = store.to_dict()
    project.programmer_state = programmer.save_state()
    project.effect_browser_state = browser.save_state()
    # Project-only metadata must not change pure scene/Dispatch editor semantics.
    assert "joystick_controls" not in programmer.dispatch_capture_state()
    path = output / "roundtrip.dmxt"
    project.save(str(path))
    loaded = Project.load(str(path))
    if in_place:
        if in_place_case != "same":
            configure(row, "amplitude", ["joy_amplitude_x", None, None])
        input_value(manager, 100 if in_place_case == "same" else 64)
        browser._tick_play()
        current_output = sample(sink)
        assert current_output != before
        # Mirror MainWindow._load_project's relevant release/rehydration calls
        # on the existing real panels, without constructing hardware/UI shell.
        browser.stop_external()
        programmer.clear_live_output()
        sink._layers.clear()
        programmer._pm = loaded
        browser._project = loaded
        programmer._refresh_fixtures()
        browser.load_state(loaded.effect_browser_state)
        browser._param_overrides["unrelated_override"] = 123
        old_overrides = dict(browser._param_overrides)
        cleared = []
        programmer._ch_list.joy_amplitude_changed.connect(cleared.append)
        assert programmer.load_state(loaded.programmer_state)
        assert configuration(row_for(programmer)) == (mode, targets)
        assert browser._param_overrides["unrelated_override"] == 123
        if in_place_case == "same":
            assert cleared == []
            assert browser._param_overrides == old_overrides
        else:
            assert cleared == [{f"joy_amplitude_{axis}": None for axis in "xyz"}]
            assert browser._param_overrides == {"unrelated_override": 123}
        saved_entry = store.list_caches()[0].nodes[0]
        assert browser.dispatch_recall_state(saved_entry["node"], saved_entry["state"])
        browser._play_timer.stop()
        browser._tick_play()
        # No new input: changed configurations release the obsolete override;
        # unchanged configuration preserves its live value.
        assert sample(sink) == (current_output if in_place_case == "same" else before)
        input_value(manager, 64)
        programmer._flush_live_output()
        browser._tick_play()
        assert sample(sink) == before
        assert set(sample(sink)) == {4, 10, 16}
        assert programmer.get_live_selected_fixture_ids() == ["2", "4", "6"]
        (output / "result.json").write_text(json.dumps({"in_place": in_place_case, "output": sample(sink)}))
        return
    bindings._global_bind_mgr = None
    fresh = EffectBrowserWidget()
    fresh_sink = Sink()
    fresh._project, fresh._nm = loaded, fresh_sink
    fresh_programmer = ProgrammerPanel(loaded)
    fresh_programmer._effect_browser_cache = fresh
    fresh_programmer._nm_ref = fresh_sink
    fresh._programmer_panel = fresh_programmer
    if built:
        controls(row_for(fresh_programmer))
    assert fresh_programmer.load_state(loaded.programmer_state)
    fresh_row = row_for(fresh_programmer)
    # Lazy construction must not reset the restored state; built UI must agree.
    mode_combo, axes = controls(fresh_row)
    assert configuration(fresh_row) == (mode, targets)
    assert mode_combo.currentText() == ("幅度控制模式" if mode == "amplitude" else "叠加模式")
    assert [axes[axis].currentText() for axis in "xyz"] == [value or "(不使用)" for value in targets]
    fresh_peer = row_for(fresh_programmer, "peer")
    controls(fresh_peer)
    assert configuration(fresh_peer) == peer_configuration
    assert fresh._param_overrides == {}, "Loading configuration must not invent a driver value"
    assert fresh_programmer.get_live_selected_fixture_ids() == ["2", "4", "6"]
    fresh_store = EffectCacheStore()
    fresh_store.from_dict(loaded.dispatch_state)
    entry = fresh_store.list_caches()[0].nodes[0]
    assert entry["node"]["fixture_ids"] == ["2", "4", "6"]
    assert fresh.dispatch_recall_state(entry["node"], entry["state"])
    fresh._play_timer.stop()
    assert configuration(fresh_row) == (mode, targets), "Scene playback must not change editor controls"
    input_value(fresh._joy_mgr, 64)
    fresh_programmer._flush_live_output()
    fresh._tick_play()
    assert sample(fresh_sink) == before, "Same project and same physical input must preserve the real Effect output"
    assert fresh._joy_mgr.get_all_bindings()[0].to_dict() == rule.to_dict()
    if mode == "additive":
        assert fresh._param_overrides == {}
        assert sample(fresh_sink, "joystick") == sample(sink, "joystick")
        assert set(sample(fresh_sink, "joystick")) == {4, 10, 16}
    for _ in range(3):
        loaded.programmer_state = fresh_programmer.save_state()
        loaded.save(str(path))
        loaded = Project.load(str(path))
        assert fresh_programmer.load_state(loaded.programmer_state)
        assert configuration(row_for(fresh_programmer)) == (mode, targets)
        input_value(fresh._joy_mgr, 64)
        fresh._tick_play()
        assert sample(fresh_sink) == before
    assert app is QApplication.instance()
    (output / "result.json").write_text(json.dumps({"mode": mode, "target": target, "built": built,
                                                   "output_before": before, "output_after": sample(fresh_sink)}))


if __name__ == "__main__" and len(sys.argv) == 7 and sys.argv[1] == "--worker":
    _run_case(Path(sys.argv[2]), sys.argv[3], sys.argv[4], bool(int(sys.argv[5])), Path(sys.argv[6]))
