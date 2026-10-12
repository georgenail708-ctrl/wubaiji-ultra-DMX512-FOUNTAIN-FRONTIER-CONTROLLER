"""Reverse follows the authored default sequence, including captured scene owners."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_reverse_is_relative_for_all_phase_families():
    from src.ui.effect_spatial_semantics import PHASE_ADVANCE_PRESETS, effective_direction, effective_params

    for preset in (*sorted(PHASE_ADVANCE_PRESETS), "chase_single", "unknown"):
        params = {"_app_direction": "reverse", "speed": 1.0}
        assert effective_direction(preset, "reverse") == "reverse"
        assert effective_params(preset, params) is params


@pytest.mark.parametrize("scope", ["global", "per_shape"])
@pytest.mark.parametrize("skip", [False, True])
def test_reverse_coordinate_phase_requires_explicit_coordinate_mode(scope, skip):
    from src.ui.effect_order_execution import coordinate_phase_enabled, prepare_params

    params = {"spatial_scope": scope, "_skip_base_order": skip, "_app_direction": "reverse",
              "_matrix_coords": {"stale": 123}, "_matrix_x_span": (123, 123)}
    expected = scope == "global" and skip
    assert coordinate_phase_enabled(params) is expected
    actual = prepare_params(params, {"a": {"pos_x": 0, "pos_y": 0}, "b": {"pos_x": 7, "pos_y": 2}})
    assert ("_matrix_coords" in actual) is expected
    assert "stale" not in actual.get("_matrix_coords", {})
    assert params["_matrix_coords"] == {"stale": 123}


@pytest.mark.parametrize("preset", ["wave_sine", "wave_cosine", "wave_triangle", "chase_single"])
@pytest.mark.parametrize("scope", ["global", "per_shape"])
@pytest.mark.parametrize("selection", ["single", "multi", "subset"])
def test_reverse_authored_sequence_and_scene_roundtrip(preset, scope, selection, tmp_path):
    root = Path(os.environ.get("EFFECT_REVERSE_SOURCE", Path(__file__).resolve().parents[1]))
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", QT_QPA_PLATFORM="offscreen",
               QASCADE_CT_ENGINE="0", QASCADE_NUMBA="0", HOME=str(tmp_path),
               USERPROFILE=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / "config"),
               XDG_CACHE_HOME=str(tmp_path / "cache"))
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(root),
                             preset, scope, selection, str(tmp_path)], cwd=root, env=env,
                            text=True, capture_output=True, timeout=90, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "result.json").exists()


def run_case(root, preset, scope, selection, output):
    import json
    import socket
    import threading
    from copy import deepcopy

    sys.path.insert(0, str(root))
    forbidden_calls = []

    def forbidden(*args, **kwargs):
        forbidden_calls.append("hardware/socket/thread")
        raise AssertionError("No real hardware, socket or worker thread")

    socket.socket = forbidden
    threading.Thread.start = forbidden
    import numpy as np
    from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication

    QThread.start = forbidden
    from src.ui import joystick_control_binding as bindings
    from src.ui import midi_joystick_driver, physical_joystick_driver

    class IdlePhysical(QObject):
        axis3d_changed = pyqtSignal(float, float, float, float)

    class IdleMidi(QObject):
        axis3d_changed = pyqtSignal(float, float, float)

    drivers = []

    def physical_factory():
        drivers.append(IdlePhysical())
        return drivers[-1]

    def midi_factory():
        drivers.append(IdleMidi())
        return drivers[-1]

    bindings.get_physical_joystick_driver = physical_factory
    physical_joystick_driver.get_physical_joystick_driver = physical_factory
    midi_joystick_driver.get_midi_joystick_driver = midi_factory
    from src.core.models import FixtureDefinition, FixtureInstance, Project
    from src.processing.dispatch.effect_cache import EffectCacheStore
    from src.ui.effect_browser import EffectBrowserWidget
    from src.ui.programmer_panel import ProgrammerPanel

    class Sink:
        def __init__(self):
            self.layers = {}

        def update_layer(self, layer, universe, data, active=None, **kwargs):
            self.layers.setdefault(layer, {})[universe] = (np.asarray(data).copy(), np.asarray(active).copy())

        def clear_layer(self, layer):
            self.layers.pop(layer, None)

    app = QApplication([])
    EffectBrowserWidget._populate_list = lambda self: None  # Static catalog list presentation only.
    project = Project()
    project.fixture_defs = [FixtureDefinition(id="d", name="dimmer", channels=["dimmer"], channel_count=1)]
    base_a, base_b = ["5", "1", "7", "3"], ["6", "2", "8", "4"]
    group_a = base_a[::2] if selection == "subset" else base_a
    group_b = base_b[:3] if selection == "subset" else base_b
    coordinates = [(0, 0), (3, 0), (0, 2), (3, 2)]
    project.fixtures = [FixtureInstance(
        id=str(i), def_id="d", universe=1, start_channel=i,
        pos_x=coordinates[(i - 1) // 2][0] + (20 if i % 2 == 0 else 0),
        pos_y=coordinates[(i - 1) // 2][1]) for i in range(1, 9)]
    project.fixture_groups = [
        {"id": "A", "name": "A", "fixture_ids": list(reversed(group_a)), "base_order_fixture_ids": base_a},
        {"id": "B", "name": "B", "fixture_ids": list(reversed(group_b)), "base_order_fixture_ids": base_b},
    ]
    selected = group_a if selection == "single" else group_a + group_b

    def panel_pair(model):
        browser, programmer, sink = EffectBrowserWidget(), ProgrammerPanel(model), Sink()
        browser._joy_mgr.disable_simulator()
        browser._project, browser._nm, browser._programmer_panel = model, sink, programmer
        programmer._effect_browser_cache, programmer._nm_ref = browser, sink
        # Forward the same member/base payload as the Main/TopView bridge without constructing its hardware shell.
        programmer.fixture_selection_changed.connect(
            lambda ids: browser.set_live_fixture_selection(ids, list(programmer._current_base_order_ids)))
        selector = programmer._group_selector
        selector.load_groups(model.fixture_groups, model.fixtures)
        selector.show()
        selector._group_btns[0].click()
        if selection != "single":
            QTest.mouseClick(selector._group_btns[1], Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier)
        assert programmer.get_live_selected_fixture_ids() == selected
        assert browser._selected_group_ids == selected
        browser._on_preset_click(preset)
        form = browser._channel_forms[0]
        form["combo"].setCurrentIndex(form["combo"].findData("dimmer"))
        browser._apply_channel_form_values(form, {"speed": 1.0, "min": 0, "max": 255})
        browser._spatial_scope_combo.setCurrentIndex(browser._spatial_scope_combo.findData(scope))
        browser._sort_combo.setCurrentIndex(browser._sort_combo.findData("index"))
        browser._skip_base_order_chk.setChecked(False)
        return browser, programmer, sink

    def start(browser, direction):
        browser._direction_combo.setCurrentIndex(browser._direction_combo.findData(direction))
        assert browser._play_current_selection_as_independent_effect()
        browser._play_timer.stop()
        assert len(browser._external_states) == 1
        owner = next(iter(browser._external_states.values()))
        owner["speed_factor"] = 0.0
        return owner

    def samples(browser, sink, owner):
        frames = []
        for t in (.123, .49, 1.11):
            owner["virtual_t"] = t
            browser._tick_play()
            data, active = sink.layers["effect"][1]
            assert set(np.flatnonzero(active) + 1) == set(map(int, selected))
            frames.append({str(i + 1): int(data[i]) for i in np.flatnonzero(active)})
        return frames

    browser, programmer, sink = panel_pair(project)
    default_owner = start(browser, "default")
    default = samples(browser, sink, default_owner)
    browser.stop_external()
    reverse_owner = start(browser, "reverse")
    assert reverse_owner["group_ids"] == selected
    actual = samples(browser, sink, reverse_owner)
    store = EffectCacheStore()
    store.save_from_nodes("reverse authored order", browser._bus_nodes(), capture_state=lambda _: browser.dispatch_capture_state())
    project.dispatch_state = store.to_dict()
    path = output / "reverse.dmxt"
    project.save(str(path))
    browser.stop_external()

    # Independent real-owner oracle: reverse the author-supplied member sequence, keeping the same default kernel.
    reference_group = {"id": "reference", "name": "reverse author sequence",
                       "fixture_ids": list(reversed(selected)), "base_order_fixture_ids": list(reversed(selected))}
    selector = programmer._group_selector
    selector.load_groups([reference_group], project.fixtures)
    selector._group_btns[0].click()
    assert browser._selected_group_ids == list(reversed(selected))
    reference_owner = start(browser, "default")
    reference = samples(browser, sink, reference_owner)
    assert actual == reference, {"preset": preset, "scope": scope, "selection": selection,
                                 "default": default, "actual_reverse": actual, "default_reversed_owner": reference}
    assert actual != default, "Nonconstant chosen frames must exercise reversal, not merely equality of static output"
    browser.stop_external()

    # Reopen the actual project and recall the saved reverse scene without changing the editor's default direction.
    loaded = Project.load(str(path))
    fresh, fresh_programmer, fresh_sink = panel_pair(loaded)
    editor_before = deepcopy(fresh._capture_current_param_values())
    programmer_before = deepcopy(fresh_programmer.save_state())
    cache = EffectCacheStore()
    for _ in range(2):
        cache.from_dict(loaded.dispatch_state)
        entries = cache.list_caches()[0].nodes
        assert len(entries) == 1
        for entry in entries:
            assert fresh.dispatch_recall_state(entry["node"], entry["state"])
        fresh._play_timer.stop()
        owner = next(iter(fresh._external_states.values()))
        owner["speed_factor"] = 0.0
        assert owner["group_ids"] == selected
        assert owner["params"]["_app_direction"] == "reverse"
        assert samples(fresh, fresh_sink, owner) == reference
        assert fresh._capture_current_param_values() == editor_before
        assert fresh_programmer.save_state() == programmer_before
        fresh.stop_external()
        loaded.save(str(path))
        loaded = Project.load(str(path))
    assert not forbidden_calls
    (output / "result.json").write_text(json.dumps({"preset": preset, "scope": scope,
                                                   "selection": selection, "reverse": actual}, indent=2))
    app.processEvents()


if __name__ == "__main__":
    run_case(Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5], Path(sys.argv[6]))
