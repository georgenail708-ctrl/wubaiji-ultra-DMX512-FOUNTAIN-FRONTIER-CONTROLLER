"""Studio autoapply is quiet on success; real manual/error dialogs stay visible.

Runs the real inspector/canvas/panel timer/compiler/ledger/Timeline chain in
isolated processes. Dialogs are observed and accepted, never replaced by mocks.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("case", ["edit", "repeat", "delete", "preset_only",
                                  "manual_success", "manual_failure", "failure_retry",
                                  "other_window"])
def test_studio_smoothing_feedback(case, tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QASCADE_CT_ENGINE="0",
               QASCADE_NUMBA="0", PYTHONDONTWRITEBYTECODE="1", HOME=str(tmp_path),
               USERPROFILE=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / "config"),
               XDG_CACHE_HOME=str(tmp_path / "cache"), OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(root), case],
                            cwd=root, env=env, capture_output=True, text=True, timeout=45, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def _prepare(root):
    import importlib.abc
    import socket
    import threading

    sys.path.insert(0, str(root))

    def forbidden(*args, **kwargs):
        raise AssertionError("No network, hardware or worker threads in this regression")

    socket.socket = forbidden
    socket.create_connection = forbidden
    threading.Thread.start = forbidden

    class DriverGuard(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.startswith(("src.network", "pygame", "sounddevice", "mido", "serial")):
                forbidden(fullname)

    sys.meta_path.insert(0, DriverGuard())
    from PyQt6.QtCore import QThread, QThreadPool

    QThread.start = forbidden
    QThreadPool.start = forbidden


def _dialog_observer(app):
    from PyQt6.QtCore import QEvent, QObject, QTimer
    from PyQt6.QtWidgets import QMessageBox

    class Observer(QObject):
        def __init__(self):
            super().__init__()
            self.dialogs = []
            self.on_warning = None

        def eventFilter(self, obj, event):  # noqa: N802
            if isinstance(obj, QMessageBox) and event.type() == QEvent.Type.Show:
                self.dialogs.append((obj.icon().name, obj.text(), obj.isModal()))
                QTimer.singleShot(20, obj.accept)
                if obj.icon() == QMessageBox.Icon.Warning and self.on_warning is not None:
                    callback, self.on_warning = self.on_warning, None
                    QTimer.singleShot(0, callback)
            return False

    observer = Observer()
    app.installEventFilter(observer)
    return observer


def _frames():
    import numpy as np

    from src.core.models import DMXFrame

    values = [0] * 5 + [10] * 3 + [20] * 4 + [255] * 3
    return [DMXFrame(1, i * .025, np.array([value] + [17] * 511, dtype=np.uint8))
            for i, value in enumerate(values)]


def _same_frames(actual, expected):
    import numpy as np

    assert len(actual) == len(expected)
    for left, right in zip(actual, expected, strict=True):
        assert (left.timestamp, left.universe) == (right.timestamp, right.universe)
        assert np.array_equal(left.data, right.data)


def _setup(app, *, legacy=False, existing=True):
    from types import SimpleNamespace

    from src.core.models import Clip, Track
    from src.core.project_manager import ProjectManager
    from src.engine.clip_smoothing import commit_smoothing, prepare_smoothing
    from src.ui.clip_studio.studio_apply import apply_compiled_fields
    from src.ui.clip_studio.studio_graph import new_node
    from src.ui.clip_studio.studio_panel import ClipStudioPanel
    from src.ui.clip_studio.studio_session import no_inner_snapshots, snapshot_studio_edit
    from src.ui.timeline.timeline_widget import TimelineWidget

    ProjectManager.reset_singleton()
    pm = ProjectManager.instance()
    clips = [Clip(id=cid, track_id="t0", label=cid, start_time=i * 2, duration=.375,
                  frames=_frames(), active_channels_by_universe={1: [1]},
                  process_sheet_id="sheet1" if cid in ({"A"} if legacy else {"A", "B"}) else "")
             for i, cid in enumerate(("A", "B", "C"))]
    for clip in clips:
        if clip.process_sheet_id and existing:
            if legacy:
                clip.smooth_preset, clip.smooth_preset_name = "motor_servo", "电机伺服"
            else:
                commit_smoothing(clip, prepare_smoothing(clip, "motor_servo", project=pm.project))
    pm.project.tracks = [Track(id="t0", name="Recordings", clips=clips)]
    node = new_node("smooth_motor_servo", 0, 0)
    state = {"sheets": [{"id": "sheet1", "name": "Test", "graph":
                        {"nodes": [node] if existing else [], "edges": [], "frames": []}}],
             "active_sheet": "sheet1", "view_mode": "single"}
    pm.project.clip_studio_state = state
    tw = TimelineWidget(pm.project)
    tw.show()
    app.processEvents()
    for view in tw._iter_clip_views():
        view.set_selected_ids({clip.id for clip in clips})
    assert tw._selected_clip_ids() == ["A", "B", "C"]
    calls = []

    def apply(ids, fields, prev):
        # Same public composition seam as MainWindow._lazy_install_clip_studio.
        with no_inner_snapshots(tw):
            failed = apply_compiled_fields(tw, ids, fields, prev)
            if fields and ids:
                tw._mark_project_dirty()
        calls.append({"ids": ids, "fields": fields.copy(), "failed": failed})
        return failed

    panel = ClipStudioPanel(apply_fields_fn=apply,
                            snapshot_fn=lambda: snapshot_studio_edit(pm),
                            list_clips_fn=lambda: clips, project_getter=lambda: pm.project)
    panel.load_state(state, pm.project)
    panel.resize(1300, 800)
    panel.show()
    app.processEvents()
    if existing:
        panel.canvas().focus_node(node["id"])
        panel.canvas().selection_changed.emit(node["id"])
        app.processEvents()
    return SimpleNamespace(app=app, pm=pm, clips=clips, tw=tw, panel=panel, calls=calls)


def _wait_apply(h, count):
    from PyQt6.QtCore import QElapsedTimer
    from PyQt6.QtTest import QTest

    timer = QElapsedTimer()
    timer.start()
    while (len(h.calls) < count or h.panel._apply_timer.isActive()) and timer.elapsed() < 3000:
        QTest.qWait(20)
    assert len(h.calls) == count and not h.panel._apply_timer.isActive()


def _edit(h, value):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QDoubleSpinBox

    from src.ui.clip_studio.studio_param_form import CardParamForm

    form = h.panel._inspector._form
    assert isinstance(form, CardParamForm)
    spin = form.findChild(QDoubleSpinBox, "strength")
    spin.setFocus()
    spin.selectAll()
    QTest.keyClicks(spin, str(value))
    count = len(h.calls)
    QTest.keyClick(spin, Qt.Key.Key_Return)
    h.app.processEvents()
    assert h.panel._apply_timer.isActive()
    assert h.panel._status_label.text() == "待应用"
    _wait_apply(h, count + 1)


def _cleanup(h):
    from PyQt6.QtCore import QTimer

    h.panel._inspector.discard()
    for widget in (h.panel, h.tw):
        for timer in widget.findChildren(QTimer):
            timer.stop()
        widget.close()
        widget.deleteLater()
    h.app.processEvents()


def _success_case(h, observer, case):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest

    before = len(h.pm._undo_stack)
    if case == "preset_only":
        h.panel._palette.quick_create_requested.emit("smooth_motor_servo")
        assert h.panel._apply_timer.isActive()
        _wait_apply(h, 1)
        assert "smooth_custom_params" not in h.calls[-1]["fields"]
    else:
        _edit(h, .85)
        if case == "repeat":
            observer.dialogs.clear()
            before = len(h.pm._undo_stack)
            _edit(h, .9)
        elif case == "delete":
            observer.dialogs.clear()
            before = len(h.pm._undo_stack)
            h.panel.canvas().setFocus()
            QTest.keyClick(h.panel.canvas(), Qt.Key.Key_Delete)
            h.app.processEvents()
            assert h.panel._apply_timer.isActive()
            _wait_apply(h, 2)
    assert h.calls[-1]["ids"] == ["A", "B"] and h.calls[-1]["failed"] == []
    assert len(h.pm._undo_stack) == before + 1
    assert h.panel._status_label.text() == "已应用"
    _same_frames(h.clips[2].frames, _frames())
    assert h.clips[2].smooth_preset == "" and h.clips[2].smooth_source_frames == []
    for clip in h.clips[:2]:
        assert clip.duration == .375 and clip.start_time in (0, 2)
        if case == "delete":
            _same_frames(clip.frames, _frames())
            assert clip.smooth_preset == "" and clip.smooth_source_frames == []
        else:
            _same_frames(clip.smooth_source_frames, _frames())
            assert clip.smooth_preset == "motor_servo"
    assert observer.dialogs == [], f"Automatic {case} opened modal feedback: {observer.dialogs}"


def _manual_case(h, observer, *, failure=False):
    # Run after a Studio application, so stale quiet state would be detected.
    _edit(h, .85)
    observer.dialogs.clear()
    targets = {"A"} if failure else {"B", "C"}
    for view in h.tw._iter_clip_views():
        view.set_selected_ids(targets)
    before = len(h.pm._undo_stack)
    h.tw._clip_area.clip_auto_smooth.emit("A" if failure else "C", "motor_servo")
    expected = "Warning" if failure else "Information"
    assert len(observer.dialogs) == 1 and observer.dialogs[0][0] == expected
    assert observer.dialogs[0][2] is True
    if failure:
        assert "没有未处理来源" in observer.dialogs[0][1]
        assert len(h.pm._undo_stack) == before
        _same_frames(h.clips[0].frames, _frames())
    else:
        assert "2 个 Clip" in observer.dialogs[0][1]
        assert len(h.pm._undo_stack) == before + 1
        assert all(clip.smooth_preset == "motor_servo" for clip in h.clips[1:])


def _failure_retry(h, observer):
    _edit(h, .85)
    assert [d[0] for d in observer.dialogs] == ["Warning"]
    assert "没有未处理来源" in observer.dialogs[-1][1]
    assert h.calls[-1]["failed"] == ["smooth_custom_params"]
    _same_frames(h.clips[0].frames, _frames())
    assert h.panel._ledger.applied("sheet1").get("smooth_custom_params", {}).get("strength") != .85
    status = h.panel._status_label.text()
    assert "未生效" in status and "平滑参数" in h.panel._status_label.toolTip()
    h.panel._refresh_compile_status(reset_canvas=False)
    assert h.panel._status_label.text() == status
    h.panel.canvas()._commit_graph_change()
    _wait_apply(h, 2)
    assert [d[0] for d in observer.dialogs] == ["Warning", "Warning"]
    assert h.calls[-1]["failed"] == ["smooth_custom_params"]
    h.clips[0].smooth_preset = ""
    h.clips[0].smooth_preset_name = ""
    observer.dialogs.clear()
    h.panel.canvas()._commit_graph_change()
    _wait_apply(h, 3)
    assert h.calls[-1]["failed"] == []
    assert h.panel._status_label.text() == "已应用"
    assert h.panel._ledger.applied("sheet1")["smooth_custom_params"]["strength"] == .85
    assert observer.dialogs == [], f"Successful retry opened modal feedback: {observer.dialogs}"


def _other_window(h, observer):
    from PyQt6.QtCore import QTimer

    from src.ui.timeline.timeline_widget import TimelineWidget

    other = TimelineWidget(h.pm.project)
    other.show()
    h.app.processEvents()
    for view in other._iter_clip_views():
        view.set_selected_ids({"C"})
    # While Studio is in its error dialog's nested event loop, manually apply
    # on a second real timeline. No global success-suppression state may leak.
    observer.on_warning = lambda: other._clip_area.clip_auto_smooth.emit("C", "motor_servo")
    _edit(h, .85)
    assert [d[0] for d in observer.dialogs] == ["Warning", "Information"]
    assert h.clips[2].smooth_preset == "motor_servo"
    assert "未生效" in h.panel._status_label.text()
    for timer in other.findChildren(QTimer):
        timer.stop()
    other.close()
    other.deleteLater()
    h.app.processEvents()


def _run(case):
    from PyQt6.QtWidgets import QApplication

    app = QApplication([])
    observer = _dialog_observer(app)
    h = _setup(app, legacy=case in {"manual_failure", "failure_retry", "other_window"},
               existing=case != "preset_only")
    try:
        if case in {"edit", "repeat", "delete", "preset_only"}:
            _success_case(h, observer, case)
        elif case in {"manual_success", "manual_failure"}:
            _manual_case(h, observer, failure=case == "manual_failure")
        elif case == "failure_retry":
            _failure_retry(h, observer)
        else:
            _other_window(h, observer)
    finally:
        _cleanup(h)


if __name__ == "__main__" and sys.argv[1:2] == ["--worker"]:
    _prepare(Path(sys.argv[2]))
    _run(sys.argv[3])
