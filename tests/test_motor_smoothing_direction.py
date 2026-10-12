"""Motor offline smoothing must not add reversals to authored monotone runs."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("bits", [8, 16])
@pytest.mark.parametrize("strength", ["default", "0", ".01", ".1", ".4", ".75", "1"])
def test_recorded_motor_direction(bits, strength, tmp_path):
    _worker(tmp_path, "numeric", str(bits), strength)


@pytest.mark.parametrize("bits", [8, 16])
@pytest.mark.parametrize("mode", ["defaults", "custom"])
@pytest.mark.parametrize("direction", [1, -1])
def test_studio_motor_transaction(bits, mode, direction, tmp_path):
    _worker(tmp_path, "studio", str(bits), mode, str(direction))


@pytest.mark.parametrize("bits", [8, 16])
@pytest.mark.parametrize("direction", [1, -1])
def test_motor_preserves_direction_after_duplicate_time_anchors(bits, direction, tmp_path):
    _worker(tmp_path, "duplicates", str(bits), str(direction))


def test_motor_guard_leaves_healthy_and_authored_turns_unchanged(tmp_path):
    _worker(tmp_path, "guard")


def _worker(tmp_path, *args):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QASCADE_CT_ENGINE="0",
               QASCADE_NUMBA="0", PYTHONDONTWRITEBYTECODE="1", HOME=str(tmp_path),
               USERPROFILE=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path / "config"),
               XDG_CACHE_HOME=str(tmp_path / "cache"), OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", str(root), *args],
                            cwd=root, env=env, capture_output=True, text=True, timeout=60, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def _prepare(root):
    import socket
    import threading

    sys.path.insert(0, str(root))

    def forbidden(*args, **kwargs):
        raise AssertionError("No network or hardware workers in this regression")

    socket.socket = forbidden
    socket.create_connection = forbidden
    threading.Thread.start = forbidden
    from PyQt6.QtCore import QThread, QThreadPool

    QThread.start = forbidden
    QThreadPool.start = forbidden


def _source(bits, custom=False):
    if custom:
        return [value * (257 if bits == 16 else 1) for value in [0] * 5 + [10] * 3 + [20] * 4 + [255] * 3]
    if bits == 16:
        return [36991, 37880, 42327, 42327, 42415, 42504, 42504, 42504, 42504, 60289]
    return [63, 135, 135, 135, 135, 139, 139, 139, 142, 142, 143, 161]


def _project_clip(bits, values, times=None):
    import numpy as np

    from src.core.models import Clip, DMXFrame, FixtureDefinition, FixtureInstance, Project

    project = Project()
    project.fixture_defs = [FixtureDefinition(id="motor", channels=["motor_x", "motor_x_fine"])]
    project.fixtures = [FixtureInstance(id="f", def_id="motor", universe=1, start_channel=1)]
    frames = []
    if times is None:
        times = np.arange(len(values)) * .025
    for time, value in zip(times, values, strict=True):
        data = np.full(512, 17, dtype=np.uint8)
        data[0] = int(value) >> 8 if bits == 16 else int(value)
        if bits == 16:
            data[1] = int(value) & 255
        frames.append(DMXFrame(1, float(time), data))
    clip = Clip(id="A", track_id="t0", start_time=2, duration=float(times[-1]) + .025,
                frames=frames, active_channels_by_universe={1: [1, 2] if bits == 16 else [1]},
                playback_rate=.75, fade_in=.02)
    return project, clip


def _values(frames, bits):
    import numpy as np

    return np.array([int(f.data[0]) * 256 + int(f.data[1]) if bits == 16 else int(f.data[0]) for f in frames])


def _numeric(bits, strength):
    import numpy as np

    from src.engine.clip_smoothing import prepare_smoothing

    maximum = (1 << bits) - 1
    rng = np.random.default_rng(412)
    sources = [_source(bits), _source(bits, True), [maximum // 2] * 12,
               [0, maximum], [0, maximum // 2, maximum],
               [250, 252, 254, 256, 259, 262, 270, 280, 290, 300] if bits == 16 else [1, 2, 3, 4, 5, 6]]
    sources += [np.sort(rng.integers(0, maximum + 1, 23)).tolist() for _ in range(4)]
    for source in sources:
        for direction in (1, -1):
            raw = np.array(source) if direction == 1 else maximum - np.array(source)
            for window in (.01, .2, 5):
                custom = {} if strength == "default" else {"strength": float(strength), "window_seconds": window}
                for irregular in (False, True):
                    times = np.arange(len(raw)) * .025 if not irregular else np.r_[0., np.cumsum(np.resize([.002, .01, .1], len(raw) - 1))]
                    project, clip = _project_clip(bits, raw, times)
                    before = [(f.timestamp, f.data.tobytes()) for f in clip.frames]
                    change = prepare_smoothing(clip, "motor_servo", custom, project)
                    actual = _values(change.frames, bits)
                    assert np.all(direction * np.diff(actual) >= 0), (bits, custom, raw, actual)
                    assert actual[0] == raw[0] and actual[-1] == raw[-1]
                    assert actual.min() >= raw.min() and actual.max() <= raw.max()
                    assert before == [(f.timestamp, f.data.tobytes()) for f in clip.frames]
                    assert [f.timestamp for f in change.frames] == list(times)
                    assert all(np.array_equal(a.data[2 if bits == 16 else 1:], b.data[2 if bits == 16 else 1:])
                               for a, b in zip(clip.frames, change.frames, strict=True))
                    if strength == "0":
                        assert np.array_equal(actual, raw)
    # Explicit peaks, valleys and long dwell runs survive, including a one-sample pulse.
    for source in ([0] * 4 + [maximum // 4, maximum // 2, maximum] + [maximum] * 4
                   + [maximum // 2, maximum // 4] + [0] * 4,
                   [0] * 5 + [maximum] + [0] * 5):
        project, clip = _project_clip(bits, source)
        actual = _values(prepare_smoothing(clip, "motor_servo", {}, project).frames, bits)
        for i, value in enumerate(source):
            if value in (0, maximum):
                assert actual[i] == value
        peak = source.index(maximum)
        assert np.all(np.diff(actual[:peak + 1]) >= 0)
        assert np.all(np.diff(actual[peak:]) <= 0)


def _guard():
    from dataclasses import replace

    import numpy as np

    from src.engine.clip_smoothing_math import smooth_channel
    from src.engine.output_smoother import BUILTIN_SMOOTH_PRESETS, EASE_FUNCS_V

    preset = BUILTIN_SMOOTH_PRESETS["motor_servo"]
    # Healthy ramp and authored non-monotone runs retain the original output.
    for source, expected in [([0, 10, 20, 30, 40], [0, 10, 20, 30, 40]),
                             ([0, 20, 10, 30, 40], [0, 15, 13, 30, 40]),
                             ([20, 40, 38, 70, 90, 100, 98, 130, 140],
                              [20, 37, 42, 68, 86, 99, 104, 129, 140])]:
        raw = np.array(source, dtype=np.uint8)
        actual = smooth_channel(np.arange(len(raw)) * .025, raw, preset, {"detail_threshold": 255})
        assert np.array_equal(actual, expected)
    # Sub-unit floating-point ringing which already rounds away must not change healthy DMX.
    raw = np.array(_source(8), dtype=np.uint8)
    actual = smooth_channel(np.arange(len(raw)) * .025, raw, preset,
                            {"curve": "ease_out", "strength": .4, "window_seconds": .4})
    assert np.array_equal(actual, [63, 135, 135, 135, 135, 139, 139, 140, 143, 145, 148, 161])
    # All accepted curves and dense strength values preserve direction after quantization.
    for bits in (8, 16):
        maximum = (1 << bits) - 1
        dtype = np.uint16 if bits == 16 else np.uint8
        for source in (_source(bits, True), [maximum // 2] * 15):
            for direction in (1, -1):
                values = np.array(source) if direction == 1 else maximum - np.array(source)
                raw = values.astype(dtype)
                for curve in EASE_FUNCS_V:
                    for strength in np.linspace(0, 1, 101):
                        actual = smooth_channel(np.arange(len(raw)) * .025, raw,
                                                replace(preset, curve=curve),
                                                {"strength": strength, "window_seconds": .2})
                        assert np.all(direction * np.diff(actual.astype(int)) >= 0), (bits, curve, strength)
                        assert actual[0] == raw[0] and actual[-1] == raw[-1]
                        if source[0] == source[-1]:
                            assert np.array_equal(actual, raw)


def _duplicates(bits, direction):
    import numpy as np

    from src.engine.clip_smoothing import prepare_smoothing
    from src.engine.output_smoother import EASE_FUNCS_V

    raw = np.array([0, 200, 50, 54, 58, 62, 67, 71, 75, 79, 83, 88, 92, 96, 100, 95])
    raw *= 257 if bits == 16 else 1
    if direction == -1:
        raw = (1 << bits) - 1 - raw
    times = np.r_[0., .025, np.arange(13) * .025 + .025, .65]
    for curve in EASE_FUNCS_V:
        for strength in (.4, .75, 1.):
            project, clip = _project_clip(bits, raw, times)
            change = prepare_smoothing(clip, "motor_servo", {
                "curve": curve, "strength": strength, "window_seconds": .2,
            }, project)
            actual = _values(change.frames, bits)
            assert np.array_equal(actual[[0, 1, 2, 14, 15]], raw[[0, 1, 2, 14, 15]])
            assert np.all(direction * np.diff(actual[2:15]) >= 0), (bits, curve, strength, actual)
            assert [frame.timestamp for frame in change.frames] == list(times)

    # A zero-time-span segment still uses stable original frame order.
    raw = np.array([0, 200, 50, 60, 70, 100, 95]) * (257 if bits == 16 else 1)
    if direction == -1:
        raw = (1 << bits) - 1 - raw
    for times in (np.array([0., .025, .025, .025, .025, .025, .65]), np.zeros(len(raw))):
        project, clip = _project_clip(bits, raw, times)
        actual = _values(prepare_smoothing(clip, "motor_servo", {"strength": 1.}, project).frames, bits)
        assert np.array_equal(actual[[0, 1, 2, 5, 6]], raw[[0, 1, 2, 5, 6]])
        assert np.all(direction * np.diff(actual[2:6]) >= 0)
        if not np.any(times):
            assert np.array_equal(actual, raw)


def _studio(bits, mode, direction):
    import numpy as np
    from PyQt6.QtCore import Qt, QTimer
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QDoubleSpinBox, QMessageBox

    from src.core.models import Clip, Project, Track
    from src.core.project_manager import ProjectManager
    from src.ui.clip_studio.studio_apply import apply_compiled_fields
    from src.ui.clip_studio.studio_cards import CARD_CATALOG
    from src.ui.clip_studio.studio_param_form import CardParamForm
    from src.ui.clip_studio.studio_smoothing import compile_node
    from src.ui.timeline.timeline_widget import TimelineWidget

    app = QApplication([])

    def accept_information():
        for widget in app.topLevelWidgets():
            if isinstance(widget, QMessageBox):
                assert widget.icon() == QMessageBox.Icon.Information, widget.text()
                widget.accept()

    timer = QTimer()
    timer.timeout.connect(accept_information)
    timer.start(10)
    raw = np.array(_source(bits, mode == "custom"))
    if direction == -1:
        raw = (1 << bits) - 1 - raw
    project, clip = _project_clip(bits, raw)
    frames = [f.copy() for f in clip.frames]
    peer = Clip(id="B", track_id="t0", start_time=5, duration=1, frames=[f.copy() for f in frames])
    project.tracks = [Track(id="t0", name="Motor", clips=[clip, peer])]
    pm = ProjectManager.instance()
    pm.project.fixture_defs = project.fixture_defs
    pm.project.fixtures = project.fixtures
    pm.project.tracks = project.tracks
    project = pm.project
    tw = TimelineWidget(project)
    tw.show()
    spec = CARD_CATALOG["smooth_motor_servo"]
    form = CardParamForm(spec, {}, live=True)
    form.show()
    app.processEvents()
    emitted = []
    form.values_edited.connect(emitted.append)
    if mode == "custom":
        for key, value in [("strength", 1.), ("window_seconds", .2)]:
            spin = form.findChild(QDoubleSpinBox, key)
            spin.setFocus()
            spin.selectAll()
            QTest.keyClicks(spin, str(value))
            QTest.keyClick(spin, Qt.Key.Key_Return)
            app.processEvents()
        assert emitted and emitted[-1] == form.values()
    fields = compile_node({"key": spec.key, "params": form.values()})
    assert apply_compiled_fields(tw, [clip.id], fields) == []
    assert len(pm._undo_stack) == 1
    actual = _values(clip.frames, bits)
    assert np.all(direction * np.diff(actual) >= 0), (raw, actual)
    assert actual[0] == raw[0] and actual[-1] == raw[-1]
    assert clip.playback_rate == .75 and clip.fade_in == .02 and clip.start_time == 2
    assert clip.duration == frames[-1].timestamp + .025
    for original, processed, backup, untouched in zip(frames, clip.frames, clip.smooth_source_frames, peer.frames, strict=True):
        assert original.timestamp == processed.timestamp
        assert np.array_equal(original.data[2 if bits == 16 else 1:], processed.data[2 if bits == 16 else 1:])
        assert np.array_equal(original.data, backup.data)
        assert np.array_equal(original.data, untouched.data)
    path = Path(os.environ["HOME"]) / "motor.dmxt"
    project.save(str(path))
    reopened = Project.load(str(path)).tracks[0].clips[0]
    assert np.array_equal(_values(reopened.frames, bits), actual)
    assert np.array_equal(_values(reopened.smooth_source_frames, bits), raw)
    assert reopened.smooth_custom_params == form.values()
    # Reapplying processes original samples, rather than compounding the previous result.
    assert apply_compiled_fields(tw, [clip.id], fields) == []
    assert np.array_equal(_values(clip.frames, bits), actual)
    assert apply_compiled_fields(tw, [clip.id], compile_node({"key": "smooth_bypass"})) == []
    assert np.array_equal(_values(clip.frames, bits), raw)
    assert clip.playback_rate == .75 and clip.fade_in == .02
    for widget in (form, tw):
        for child in widget.findChildren(QTimer):
            child.stop()
        widget.close()
    timer.stop()


if __name__ == "__main__":
    _prepare(Path(sys.argv[2]))
    if sys.argv[3] == "numeric":
        _numeric(int(sys.argv[4]), sys.argv[5])
    elif sys.argv[3] == "duplicates":
        _duplicates(int(sys.argv[4]), int(sys.argv[5]))
    elif sys.argv[3] == "studio":
        _studio(int(sys.argv[4]), sys.argv[5], int(sys.argv[6]))
    else:
        _guard()
