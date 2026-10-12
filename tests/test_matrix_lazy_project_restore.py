"""Project reopen before first Matrix dock creation uses the original shell route."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("scenario", ["ppt", "pixel", "legacy", "missing", "empty", "empty_targets"])
@pytest.mark.parametrize("topview", [False, True])
def test_matrix_project_before_first_dock(scenario, topview, tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QASCADE_CT_ENGINE="0", QASCADE_NUMBA="0",
               PYTHONDONTWRITEBYTECODE="1", HOME=str(tmp_path), USERPROFILE=str(tmp_path),
               APPDATA=str(tmp_path / "appdata"), QASCADE_EFFECT_USER_DIR=str(tmp_path / "effect-user"),
               XDG_CONFIG_HOME=str(tmp_path / "config"), XDG_CACHE_HOME=str(tmp_path / "cache"))
    for phase in ("save", "reopen"):
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--worker", str(root), str(tmp_path),
             phase, scenario, str(int(topview))], env=env, cwd=root, capture_output=True, text=True,
            timeout=60, check=False,
        )
        (tmp_path / f"{phase}.log").write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "result.json").exists()


def worker(root, output, phase, scenario, with_topview):
    import ast
    import json
    import socket
    import threading
    import types
    from copy import deepcopy

    sys.path.insert(0, str(root))
    blocked = []

    def forbidden(*args, **kwargs):
        blocked.append("external I/O or worker start")
        raise AssertionError(blocked[-1])

    socket.socket = socket.create_connection = forbidden
    threading.Thread.start = forbidden
    from PyQt6.QtCore import QCoreApplication, QEvent, QObject, Qt, QThread, QThreadPool, pyqtSignal
    from PyQt6.QtTest import QSignalSpy, QTest
    from PyQt6.QtWidgets import QApplication, QDockWidget, QMainWindow

    QThread.start = QThreadPool.start = forbidden
    # MainWindow is imported but its hardware-starting constructor is never called.
    network = types.ModuleType("src.network.network_manager")

    class NetworkManager:
        instance = staticmethod(forbidden)

    network.NetworkManager = NetworkManager
    sys.modules[network.__name__] = network
    from src.core.models import FixtureDefinition, FixtureInstance
    from src.core.project_manager import ProjectManager
    from src.ui.main_window import MainWindow
    from src.ui.matrix_slideshow.matrix_slideshow_panel import MatrixSlideshowPanel

    app = QApplication([])
    pm = ProjectManager()
    project_path = output / "matrix.dmxt"
    expected_path = output / "expected.json"
    widgets = []

    def choose(combo, channel):
        spy = QSignalSpy(combo.activated)
        combo.showPopup()
        app.processEvents()
        index = combo.findData(channel)
        assert index >= 0
        item = combo.model().index(index, 0)
        combo.view().scrollTo(item)
        QTest.mouseClick(combo.view().viewport(), Qt.MouseButton.LeftButton,
                         pos=combo.view().visualRect(item).center())
        app.processEvents()
        assert list(spy[-1]) == [index] and combo.currentData() == channel

    if phase == "save":
        pm.project.fixture_defs = [FixtureDefinition(
            id="d", name="multi channel", channels=["pump", "valve", "motor_y"], channel_count=3)]
        pm.project.fixtures = [FixtureInstance(id=str(i), def_id="d", universe=1,
                                              start_channel=(i - 1) * 3 + 1, pos_x=i, pos_y=0)
                               for i in range(1, 7)]
        author = MatrixSlideshowPanel(pm)
        widgets.append(author)
        author.resize(900, 700)
        author.show()
        app.processEvents()
        channel = "motor_y" if scenario == "pixel" else "valve"
        choose(author._ch_type_combo, channel)
        ids = [] if scenario == "empty_targets" else ["2", "4", "6"]
        author.load_fixture_group("even", ids, "saved subset", pm, author._positions_from_pm(pm))
        if scenario not in ("empty", "legacy"):
            author._ppt_density_spin.setValue(.63)
            author._canvas.set_states({fid: fid != "4" for fid in ids})
            author._save_as_new_slide()
            author._canvas.set_states({fid: fid == "4" for fid in ids})
            author._save_as_new_slide()
        if scenario == "pixel":
            author._pixel_mode_btn.setChecked(True)
            author._pixel_engine.speed = .37
        state = author.save_state()
        if scenario == "empty":
            state = {}
        elif scenario == "legacy":
            state = {"ch_type": "dimmer"}
        elif scenario == "missing":
            state["ch_type"] = "function_1@removed-definition"
        pm.project.matrix_slideshow_state = deepcopy(state)
        assert pm.save(str(project_path))
        expected_path.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    else:
        assert pm.load(str(project_path))
        state = json.loads(expected_path.read_text())
        assert pm.project.matrix_slideshow_state == state
        untouched_project = deepcopy(pm.project.to_dict())

        class TopView(QObject):
            layout_changed = pyqtSignal()
            selection_changed = pyqtSignal(list)

            def __init__(self):
                super().__init__()
                self.ids, self.writes = ["1", "3", "5"], []

            def _ordered_selected_ids(self):
                return list(self.ids)

            def get_all_fixture_positions(self):
                return MatrixSlideshowPanel._positions_from_pm(pm)

            def set_external_fixture_selection(self, ids, emit=False):
                self.writes.append(list(ids))
                self.ids = list(ids)

        def lazy_host():
            host = QMainWindow()
            widgets.append(host)
            tree = ast.parse((root / "src/ui/main_window.py").read_text())
            function = next(node for node in ast.walk(tree)
                            if isinstance(node, ast.FunctionDef) and node.name == "_lazy_load_panels")
            # Initialize absent lazy-panel references. No production branch is copied.
            for attr in {node.attr for node in ast.walk(function)
                         if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                         and node.value.id == "self"}:
                if attr.endswith(("_dock", "_widget")):
                    setattr(host, attr, None)
            host._pm, host._nm = pm, None
            host._DOCK_MIN_WIDTHS = MainWindow._DOCK_MIN_WIDTHS
            for name in ("_lazy_load_panels", "_install_dock_widget", "_sync_fixture_selection_to_topview",
                         "_sync_initial_fixture_selection_to_topview", "_topview_explicit_selection_ids",
                         "_is_project_all_fixture_selection", "_project_fixture_ids", "_project"):
                setattr(host, name, types.MethodType(getattr(MainWindow, name), host))
            tv = TopView() if with_topview else None
            host._topview_widget = tv
            dock = QDockWidget("Matrix", host)
            host.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
            host._matrix_pattern_dock = dock
            host._lazy_load_panels(dock)
            panel = host._matrix_pattern_widget
            assert panel is not None and dock.widget() is panel
            return host, dock, panel, tv

        host, dock, panel, tv = lazy_host()
        host.show()
        QTest.qWait(150)
        expected_channel = state.get("ch_type", "pump")
        assert panel._ch_type == expected_channel
        assert panel._ch_type_combo.currentData() == expected_channel
        assert panel._engine.config.ch_type == expected_channel
        assert panel._mode == state.get("mode", "ppt")
        assert not panel._engine.is_playing and not panel._pixel_running
        assert not panel._play_btn.isChecked()
        assert pm.project.to_dict() == untouched_project, "Passive restore must not overwrite project data"
        if tv:
            assert tv.ids == ["1", "3", "5"] and tv.writes == [], tv.writes
        if "fixture_ids" in state:
            assert panel.get_live_selected_fixture_ids() == state["fixture_ids"]
        if "slideshow_engine" in state:
            assert panel._engine.to_dict()["slides"] == state["slideshow_engine"]["slides"]
            assert panel._ppt_density_spin.value() == state["ppt_density"]
        if scenario == "pixel":
            assert panel._pixel_engine.speed == .37
        passive_dirty = pm.is_dirty
        assert passive_dirty is False, "Restoring saved controls must not mark the project edited"
        first_state = deepcopy(panel.save_state())
        # Opening an existing dock must not replay the saved project over edits.
        choose(panel._ch_type_combo, "valve" if expected_channel != "valve" else "pump")
        changed = deepcopy(panel.save_state())
        host._lazy_load_panels(dock)
        assert panel.save_state() == changed
        # Existing-panel project restore and subsequent project B are the reference route.
        panel.load_state(state)
        if state:
            assert panel.save_state() == first_state
        else:
            # Existing empty-state semantics clear content, retaining channel preference.
            assert not panel._engine.slides
            assert panel._ch_type == changed["ch_type"]
        other = deepcopy(state)
        other["ch_type"] = "motor_y"
        pm.project.matrix_slideshow_state = other
        assert pm.save(str(output / "other.dmxt"))
        assert pm.load(str(output / "other.dmxt"))
        panel.load_state(pm.project.matrix_slideshow_state)
        assert panel._ch_type_combo.currentData() == "motor_y"
        assert not panel._engine.is_playing and not panel._pixel_running
        (output / "result.json").write_text(json.dumps({"scenario": scenario, "topview": with_topview,
                                                        "channel": expected_channel, "blocked": blocked,
                                                        "passive_dirty": passive_dirty,
                                                        "mode": first_state["mode"], "passed": True}, indent=2))
    assert not blocked
    for widget in app.topLevelWidgets():
        widget.hide()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


if __name__ == "__main__":
    worker(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], sys.argv[5], bool(int(sys.argv[6])))
