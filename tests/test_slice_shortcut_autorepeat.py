"""Real Qt shortcut repeats must obey both pages' existing single-press guard."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("page", ["effect", "matrix-ppt", "matrix-pixel"])
@pytest.mark.parametrize("binding_name", ["F8", "K", "Ctrl+F9"])
@pytest.mark.parametrize("reopen", [False, True])
def test_slice_shortcut_ignores_os_repeat(page, binding_name, reopen, tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QASCADE_CT_ENGINE="0",
               QASCADE_NUMBA="0", PYTHONDONTWRITEBYTECODE="1", HOME=str(tmp_path),
               USERPROFILE=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / "config"),
               XDG_CACHE_HOME=str(tmp_path / "cache"))
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--worker", str(root),
         page, binding_name, str(int(reopen)), str(tmp_path)],
        cwd=root, env=env, text=True, capture_output=True, timeout=90, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "result.json").exists()


def run_case(root, page, binding_name, reopen, output):
    import json
    import socket
    import threading
    import time
    import traceback

    sys.path.insert(0, str(root))
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append("socket/thread/hardware")
        raise AssertionError("No network or hardware workers in this regression")

    socket.socket = forbidden
    socket.create_connection = forbidden
    threading.Thread.start = forbidden
    import numpy as np
    from PyQt6.QtCore import QEvent, QEventLoop, QObject, Qt, QThread, QThreadPool, QTimer, pyqtSignal
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QPushButton, QWidget

    QThread.start = forbidden
    QThreadPool.start = forbidden
    from src.ui import joystick_control_binding as bindings
    from src.ui import midi_joystick_driver, physical_joystick_driver

    class Physical(QObject):
        axis3d_changed = pyqtSignal(float, float, float, float)

    class Midi(QObject):
        axis3d_changed = pyqtSignal(float, float, float)

    drivers = []

    def physical():
        drivers.append(Physical())
        return drivers[-1]

    def midi():
        drivers.append(Midi())
        return drivers[-1]

    bindings.get_physical_joystick_driver = physical
    physical_joystick_driver.get_physical_joystick_driver = physical
    midi_joystick_driver.get_midi_joystick_driver = midi
    from src.core.models import FixtureDefinition, FixtureInstance
    from src.core.project_manager import ProjectManager
    from src.ui.effect_browser import EffectBrowserWidget
    from src.ui.matrix_slideshow.matrix_slide_engine import MatrixSlide
    from src.ui.matrix_slideshow.matrix_slideshow_panel import MatrixSlideshowPanel

    class Sink:
        def __init__(self):
            self.layers = {}

        def update_layer(self, layer, universe, data, active=None, **kwargs):
            self.layers.setdefault(layer, {})[int(universe)] = {
                int(i): int(data[i]) for i in np.flatnonzero(active)}

        def update_layer_sparse(self, layer, universe, updates, **kwargs):
            self.layers.setdefault(layer, {}).setdefault(int(universe), {}).update(
                {int(k): int(v) for k, v in updates.items()})
            return [int(universe)]

        def update_layer_sparse_batch(self, layer, updates, **kwargs):
            for universe, values in updates.items():
                self.update_layer_sparse(layer, universe, values, **kwargs)
            return list(updates)

        def update_layer_sparse_arrays(self, layer, universes, addresses, values, **kwargs):
            for universe, address, value in zip(universes, addresses, values, strict=True):
                self.update_layer_sparse(layer, universe, {address: value}, **kwargs)
            return list(set(map(int, universes)))

        def update_layer_sparse_grouped_arrays(self, layer, groups, **kwargs):
            for universe, addresses, values in groups:
                self.update_layer_sparse(layer, universe, dict(zip(addresses, values, strict=True)), **kwargs)
            return [int(group[0]) for group in groups]

        def clear_layer(self, layer):
            self.layers.pop(layer, None)

        def clear_layer_channels(self, layer, universe, addresses):
            values = self.layers.get(layer, {}).get(int(universe), {})
            for address in addresses:
                values.pop(int(address), None)

    app = QApplication([])
    pm = ProjectManager()
    pm.project.fixture_defs = [FixtureDefinition(id="d", name="dimmers", channels=["dimmer"], channel_count=1)]
    pm.project.fixtures = [FixtureInstance(id=str(i), def_id="d", universe=1, start_channel=i,
                                         pos_x=i, pos_y=0) for i in range(1, 7)]
    selected = ["2", "4", "6"]
    key, mods = {
        "F8": (Qt.Key.Key_F8, Qt.KeyboardModifier.NoModifier),
        "K": (Qt.Key.Key_K, Qt.KeyboardModifier.NoModifier),
        "Ctrl+F9": (Qt.Key.Key_F9, Qt.KeyboardModifier.ControlModifier),
    }[binding_name]
    panels = []
    effect = page == "effect"

    def make(manager, state=None):
        sink = Sink()
        if effect:
            panel = EffectBrowserWidget()
            panel._joy_mgr.disable_simulator()
            panel._project, panel._nm = manager.project, sink
            panel.set_live_fixture_selection(selected, [str(i) for i in range(1, 7)])
            panel._on_preset_click("chase_single")
            form = panel._channel_forms[0]
            form["combo"].setCurrentIndex(form["combo"].findData("dimmer"))
            panel._apply_channel_form_values(form, {"speed": 1.0, "min": 0, "max": 255})
            panel._global_speed_spin.setValue(0)
        else:
            panel = MatrixSlideshowPanel(manager)
            positions = [{"id": f.id, "pos_x": f.pos_x, "pos_y": f.pos_y} for f in manager.project.fixtures]
            panel.load_fixture_group("subset", selected, "2/4/6", manager, positions)
            panel.set_nm(sink)
            panel._ch_type_combo.setCurrentIndex(panel._ch_type_combo.findData("dimmer"))
            panel._engine.slides = [MatrixSlide(states={fid: fid == current for fid in selected})
                                    for current in selected]
            panel._rebuild_thumbs()
            if page == "matrix-pixel":
                panel._pixel_mode_btn.click()
                combo = panel._pixel_effect_combo
                combo.setCurrentIndex(combo.findData("matrix_sw_diamond_rings"))
        if state is not None:
            panel.load_state(state)
        panels.append(panel)
        panel.resize(1200, 850)
        panel.show()
        panel.activateWindow()
        panel.setFocus()
        QTest.qWait(10)
        for timer in panel.findChildren(QTimer):
            timer.stop()
        return panel, sink

    def focus(panel, widget=None):
        panel.activateWindow()
        (widget or panel).setFocus()
        app.processEvents()

    def index(panel):
        if effect:
            return panel._manual_slice_index
        if page == "matrix-ppt":
            return panel._engine.current_idx
        return panel._manual_pixel_slice_index

    def output_state(sink):
        values = sink.layers.get("effect" if effect else "matrix", {}).get(1, {})
        assert set(values) == {1, 3, 5}, values
        return dict(values)

    def press(panel, *, repeated=False, target=None, use_key=None, use_mods=None):
        time.sleep(.09)  # Existing Matrix debounce is 80 ms; not an OS repeat guard.
        pressed_key = key if use_key is None else use_key
        modifiers = mods if use_mods is None else use_mods
        if repeated:
            event = QKeyEvent(QEvent.Type.KeyPress, pressed_key, modifiers, "", True, 1)
            assert event.isAutoRepeat()
            QApplication.sendEvent(target or panel, event)
        else:
            QTest.keyClick(target or panel, pressed_key, modifiers)
        app.processEvents()

    def bind(panel):
        focus(panel)
        panel._slice_key_bind_btn.click()
        QTest.keyClick(panel, key, mods)
        QTest.qWait(5)
        assert panel._slice_key == int(key)
        assert panel._slice_key_modifiers == int(mods.value)

    def start(panel):
        if page == "matrix-ppt" and not panel._play_btn.isChecked():
            panel._play_btn.click()
        focus(panel)
        for timer in panel.findChildren(QTimer):
            timer.stop()

    observations = []

    def exercise():
        assert QThread.currentThread().loopLevel() >= 1
        panel, sink = make(pm)
        bind(panel)
        start(panel)
        if reopen:
            name = "effect_browser_state" if effect else "matrix_slideshow_state"
            setattr(pm.project, name, panel.save_state())
            path = output / "slice.dmxt"
            assert pm.save(str(path))
            panel._slice_key_clear_btn.click()
            panel.hide()
            loaded = ProjectManager()
            assert loaded.load(str(path))
            panel, sink = make(loaded, getattr(loaded.project, name))
            start(panel)
            assert panel._slice_key == int(key)
            assert panel._slice_key_modifiers == int(mods.value)
        signals = []
        panel._slice_shortcut.activated.connect(lambda: signals.append("activated"))
        panel._slice_shortcut.activatedAmbiguously.connect(lambda: signals.append("ambiguous"))
        first = index(panel)
        press(panel)
        assert index(panel) != first, (page, "physical press did not advance")
        before, frozen = index(panel), output_state(sink)
        guard_event = QKeyEvent(QEvent.Type.KeyPress, key, mods, "", True, 1)
        assert panel._handle_slice_key_event(guard_event) is False
        assert index(panel) == before
        signals.clear()
        for _ in range(3):
            press(panel, repeated=True)
            actual = output_state(sink)
            observations.append({"before": before, "after": index(panel), "output": actual,
                                 "shortcut_auto_repeat": panel._slice_shortcut.autoRepeat(),
                                 "signals": list(signals)})
            assert index(panel) == before and actual == frozen, observations[-1]
        assert signals == []
        press(panel)
        assert index(panel) != before and signals == ["activated"]
        # Keep each page's existing shortcut scope when an unrelated window is active.
        other = QWidget()
        button = QPushButton("Other window", other)
        other.show()
        other.activateWindow()
        button.setFocus()
        QTest.qWait(5)
        assert other.isActiveWindow()
        before = index(panel)
        press(panel, target=button)
        assert (index(panel) != before) is (not effect)
        before, frozen = index(panel), output_state(sink)
        press(panel, target=button, repeated=True)
        assert index(panel) == before and output_state(sink) == frozen
        other.close()
        focus(panel)
        # Existing focus contract differs intentionally between Effect function keys and Matrix inputs.
        editor = panel._slice_stride_spin if effect else panel._fps_spin
        focus(panel, editor)
        before = index(panel)
        press(panel, target=editor)
        permitted = effect and binding_name != "K"
        assert (index(panel) != before) is permitted
        focus(panel)
        panel._slice_key_clear_btn.click()
        QTest.qWait(5)
        before = index(panel)
        press(panel)
        assert panel._slice_key is None and index(panel) == before
        bind(panel)
        start(panel)
        before = index(panel)
        press(panel)
        assert index(panel) != before
        before, frozen = index(panel), output_state(sink)
        press(panel, repeated=True)
        assert index(panel) == before and output_state(sink) == frozen
        # Check the pre-existing speed/external-driver safety gates on the actual Effect owner path.
        if effect:
            panel._global_speed_spin.setValue(1)
            press(panel)
            assert index(panel) == before and "速度为 0" in panel._status_lbl.text()
            panel._global_speed_spin.setValue(0)
            panel._play_btn.click()
            panel._play_timer.stop()
            assert panel._external_states
            press(panel)
            assert index(panel) != before
            panel.stop_external()
            panel.play_preset_external("chase_single", {"speed": 1, "min": 0, "max": 255, "ch_type": "dimmer"},
                                       ["dimmer"], "index")
            panel._play_timer.stop()
            before = index(panel)
            press(panel)
            assert index(panel) == before and "外部驱动" in panel._status_lbl.text()
            panel.stop_external()
        assert not attempts
        (output / "result.json").write_text(json.dumps({"page": page, "binding": binding_name,
                                                        "reopened": reopen, "observations": observations,
                                                        "blocked": attempts}, indent=2))

    errors = []
    loop = QEventLoop()

    def execute():
        try:
            exercise()
        except BaseException:
            errors.append(traceback.format_exc())
        finally:
            for panel in panels:
                panel._slice_key_clear_btn.click()
                for timer in panel.findChildren(QTimer):
                    timer.stop()
                panel.hide()
                for attr in ("_ppt_window", "_pixel_window"):
                    window = getattr(panel, attr, None)
                    if window is not None:
                        window.hide()
            loop.quit()

    QTimer.singleShot(0, execute)
    loop.exec()
    assert not errors, "\n".join(errors)
    assert not attempts


if __name__ == "__main__":
    run_case(Path(sys.argv[2]), sys.argv[3], sys.argv[4], bool(int(sys.argv[5])), Path(sys.argv[6]))
