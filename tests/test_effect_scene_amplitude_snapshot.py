"""Real Effect scene capture/reload/output with inert input and memory sinks.

No hardware thread, socket, native engine or full main-window shell is started.
The production Qt controls, binding manager, serializer and Effect runtime run
unchanged; only driver factories and effect-library list presentation are inert.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("layout", ["single", "forms", "nested"])
@pytest.mark.parametrize("values", ["all", "zero", "partial", "none", "legacy"])
@pytest.mark.parametrize("speed", [0.0, 0.8])
def test_scene_amplitude_snapshot_roundtrip(layout, values, speed, tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QASCADE_CT_ENGINE="0", QASCADE_NUMBA="0",
               HOME=str(tmp_path), USERPROFILE=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / "config"),
               XDG_CACHE_HOME=str(tmp_path / "cache"))
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--worker", str(root), layout, values,
         str(speed), str(tmp_path)], cwd=root, env=env, text=True, capture_output=True,
        timeout=90, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "result.json").exists(), "Worker must run the full scenario"


def _run_case(root, layout, values, speed, output):
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

    def sample(sink):
        data, active = sink._layers["effect"][1]
        return {int(i + 1): int(data[i]) for i in np.flatnonzero(active)}

    def panel_pair(project):
        browser, programmer, sink = EffectBrowserWidget(), ProgrammerPanel(project), Sink()
        browser._project, browser._nm, browser._programmer_panel = project, sink, programmer
        programmer._effect_browser_cache, programmer._nm_ref = browser, sink
        programmer.load_fixture_group("__topview_live__", ["2", "4", "6"], "even", ["2", "4", "6"])
        effect_target_scope.assign_targets(browser, ["2", "4", "6"], ["1", "2", "3", "4", "5", "6"])
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
    project.fixture_defs = [FixtureDefinition(id="d", name="motor", channels=["motor_x", "motor_y", "motor_z"], channel_count=3)]
    project.fixtures = [FixtureInstance(id=str(i), def_id="d", universe=1, start_channel=(i - 1) * 3 + 1)
                        for i in range(1, 7)]
    browser, programmer, sink = panel_pair(project)
    mode_combo(programmer).setCurrentText("叠加模式" if values == "legacy" else "幅度控制模式")
    manager = browser._joy_mgr
    manager.clear_all_bindings()
    for axis in ("x" if values == "partial" else "xyz"):
        rule = manager.add_binding(f"motor_{axis}", axis, sub_axis=axis, space_mode="3d", target_def_id="d")
        rule.deadzone = 0.0
    manager.save()
    input_values(browser, (64, 192, 32))
    if values == "zero":
        # Exact zero is a valid value from the real controller's amplitude path.
        row_for(programmer)._send_joy_amplitude_overrides(0.0, 0.0, 0.0)
    if values == "none":
        browser.update_param_overrides({"joy_amplitude_x": None})
    browser.update_param_overrides({"unrelated_override": 123})
    for label, channel, ids, axis_mode in (
        ("a", "motor_x", ["2", "4", "6"], "自动"),
        ("b", "motor_y", ["1", "3", "5"], "Y轴"),
        ("c", "motor_z", ["2", "4", "6"], "关闭"),
    ):
        params = {"ch_type": channel, "speed": speed, "min": 0, "max": 255,
                  "_joy_amp_axis": axis_mode, "_state_speed_factor": 0.0,
                  "_output_fixture_ids": ids, "_output_base_order_ids": ["1", "2", "3", "4", "5", "6"]}
        channels = [channel]
        if values in ("partial", "none"):
            params.update(joy_amplitude_x=.125, joy_amplitude_y=.375, joy_amplitude_z=-.75)
        if label == "a" and layout != "single":
            channels = ["motor_x", "motor_y"]
            params["ch_type"] = channels
            params["_channel_forms"] = [{"ch_type": ch, "params": {"speed": speed, "min": 0, "max": 255,
                                                                         "_joy_amp_axis": "自动"}}
                                        for ch in channels]
            if layout == "nested" and values != "legacy":
                params["_channel_forms"][0]["params"]["joy_amplitude_x"] = -.25
        browser.play_preset_for_type(f"manual:{label}", "wave_sine", params, channels, "index")
    for owner in browser._external_states.values():
        owner["speed_factor"], owner["virtual_t"] = 0.0, .375
    before_a = tick(browser, sink)
    expected_addresses = {4, 10, 16, 2, 8, 14, 6, 12, 18}
    if layout != "single":
        expected_addresses.update({5, 11, 17})
    assert set(before_a) == expected_addresses
    original_params = {key: deepcopy(owner["params"]) for key, owner in browser._external_states.items()}
    original_overrides = dict(browser._param_overrides)
    store = EffectCacheStore()
    store.save_from_nodes("scene A", browser._bus_nodes(), capture_state=lambda _: browser.dispatch_capture_state())
    assert {key: owner["params"] for key, owner in browser._external_states.items()} == original_params
    assert browser._param_overrides == original_overrides
    input_values(browser, (224, 48, 176))
    before_b = tick(browser, sink)
    store.save_from_nodes("scene B", browser._bus_nodes(), capture_state=lambda _: browser.dispatch_capture_state())
    project.dispatch_state = store.to_dict()
    project.effect_browser_state = browser.save_state()
    assert all("unrelated_override" not in entry["params"]
               for entry in project.effect_browser_state["active_effects"])
    path = output / "scene.dmxt"
    project.save(str(path))
    loaded = Project.load(str(path))
    fresh_store = EffectCacheStore()
    fresh_store.from_dict(loaded.dispatch_state)
    scenes = {cache.name: cache.nodes for cache in fresh_store.list_caches()}
    assert all(len(nodes) == 3 for nodes in scenes.values())
    bindings._global_bind_mgr = None
    fresh, fresh_programmer, fresh_sink = panel_pair(loaded)
    fresh.load_state(loaded.effect_browser_state)
    assert tick(fresh, fresh_sink) == before_b, "Project active owners must restore their amplitude snapshot"
    assert fresh._param_overrides == {}
    fresh.stop_external()
    editor_before = fresh_programmer.save_state()
    browser_editor_before = (fresh._selected_id, list(fresh._selected_group_ids))
    driver_before = fresh._joy_mgr._driver
    assert fresh._param_overrides == {}
    recall(fresh, scenes["scene A"])
    after_a = tick(fresh, fresh_sink)
    assert after_a == before_a, ("No-input scene amplitude output", before_a, after_a)
    assert fresh._param_overrides == {}
    assert fresh_programmer.save_state() == editor_before
    assert (fresh._selected_id, list(fresh._selected_group_ids)) == browser_editor_before
    assert fresh._joy_mgr._driver is driver_before
    # Recall only B's second owner; the other two A owners must remain intact.
    b_entry = next(entry for entry in scenes["scene B"] if entry["node"]["track_type"] == "manual:b")
    peer_keys = [key for key in fresh._external_states if key[0] != "manual:b"]
    peers = {key: fresh._external_states[key] for key in peer_keys}
    recall(fresh, [b_entry])
    mixed = dict(before_a)
    mixed.update({address: before_b[address] for address in (2, 8, 14)})
    assert tick(fresh, fresh_sink) == mixed
    assert all(fresh._external_states[key] is owner for key, owner in peers.items())
    assert fresh._param_overrides == {}
    # Existing live controls still win over the saved top-level owner values.
    mode_combo(fresh_programmer).setCurrentText("叠加模式" if values == "legacy" else "幅度控制模式")
    input_values(browser, (144, 80, 208))
    live_expected = tick(browser, sink)
    input_values(fresh, (144, 80, 208))
    live_overrides = dict(fresh._param_overrides)
    assert tick(fresh, fresh_sink) == live_expected
    recall(fresh, scenes["scene A"])
    assert fresh._param_overrides == live_overrides
    assert tick(fresh, fresh_sink) == live_expected
    # Releasing a live override reveals the owner's saved base, not another
    # scene's snapshot. Pure recall never installs a parallel global controller.
    row_for(fresh_programmer)._clear_joy_amplitude_overrides()
    assert fresh._param_overrides == {}
    assert tick(fresh, fresh_sink) == before_a
    fresh.stop_external_type("manual:b")
    assert tick(fresh, fresh_sink) == {key: value for key, value in before_a.items() if key not in (2, 8, 14)}
    recall(fresh, [b_entry])
    assert tick(fresh, fresh_sink) == mixed
    fresh.stop_external()
    assert not fresh._external_states
    recall(fresh, scenes["scene A"])
    assert tick(fresh, fresh_sink) == before_a
    # Repeated real project files retain both independent scene snapshots.
    for _ in range(3):
        loaded.dispatch_state = fresh_store.to_dict()
        loaded.save(str(path))
        loaded = Project.load(str(path))
        fresh_store.from_dict(loaded.dispatch_state)
        scenes = {cache.name: cache.nodes for cache in fresh_store.list_caches()}
        recall(fresh, scenes["scene B"])
        assert tick(fresh, fresh_sink) == before_b
        recall(fresh, scenes["scene A"])
        assert tick(fresh, fresh_sink) == before_a
    assert app is QApplication.instance()
    (output / "result.json").write_text(json.dumps({"layout": layout, "values": values, "speed": speed,
                                                   "before_a": before_a, "before_b": before_b,
                                                   "after": sample(fresh_sink)}))


if __name__ == "__main__" and len(sys.argv) == 7 and sys.argv[1] == "--worker":
    _run_case(Path(sys.argv[2]), sys.argv[3], sys.argv[4], float(sys.argv[5]), Path(sys.argv[6]))
