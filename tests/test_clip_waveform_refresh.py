"""Offline regression for reused ClipItem waveform caches after model replacement.

Each case runs in a fresh process so QApplication and import adapters cannot
contaminate another test's Qt lifetime or modules. Real ClipView, ClipItem,
ProjectManager, smoothing, and history helpers run unchanged. Main/Timeline
refresh methods are compiled unchanged from their repository ASTs to avoid
constructing the full application. Unused menu/art/gesture imports, audio,
save-title updates, and layout-only hosts are isolated; no hardware is started.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("kind", ["eager", "lazy-b64", "lazy-bytes"])
@pytest.mark.parametrize("scenario", ["history", "replacement", "unchanged", "dragging"])
def test_clip_waveform_refresh(kind, scenario, tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QASCADE_CT_ENGINE="0", QASCADE_NUMBA="0",
               HOME=str(tmp_path / "home"), XDG_CONFIG_HOME=str(tmp_path / "config"),
               XDG_CACHE_HOME=str(tmp_path / "cache"))
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--worker", str(root), kind, scenario, str(tmp_path)],
        cwd=root, env=env, text=True, capture_output=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _run_case(root, kind, scenario, output):
    import ast
    import copy
    import importlib.abc
    import socket
    from types import MethodType, ModuleType, SimpleNamespace

    sys.path.insert(0, str(root))

    def deny_socket(*args, **kwargs):
        raise AssertionError("Network sockets are forbidden in this offline regression")

    socket.socket = deny_socket

    class HardwareGuard(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            prefixes = ("src.network", "pygame", "sounddevice", "mido", "serial", "render_lab")
            if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in prefixes):
                raise AssertionError("Hardware/runtime import is forbidden: " + fullname)

    sys.meta_path.insert(0, HardwareGuard())

    def module(name, **members):
        result = ModuleType(name)
        result.__dict__.update(members)
        sys.modules[name] = result
        return result

    for name in ("src.processing", "src.processing.dispatch", "src.ui.dispatch"):
        module(name).__path__ = [str(root / name.replace(".", "/"))]
    module("src.ui.qascade_theme")
    module("src.ui.timeline.clip_context_menu", build_clip_context_menu=lambda *args: None)
    module("src.ui.timeline.timeline_grid", second_grid_positions=lambda *args: [])
    module("src.ui.timeline.timeline_group_gesture")
    module("src.ui.timeline.timeline_drag_tip")
    module("src.ui.timeline.timeline_snap", snap_guide_time=None, snap_move_start=None, snap_resize_end=None)
    module("src.ui.main_window", _studio_flush_pending=lambda win: None,
           _restore_mod_after_undo=lambda win, project: None)
    module("src.ui.project_save_session", note_project_edit=lambda win: None)

    import numpy as np
    from PyQt6.QtGui import QColor, QImage, QPainter
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from src.core.models import Clip, DMXFrame, TimelineProgram, Track, _LazyDMXFrame
    from src.core.project_manager import DOMAIN_TIMELINE, ProjectManager
    from src.ui.timeline.clip_smoothing_ops import apply_smoothing
    from src.ui.timeline.clip_view import ClipView
    from src.ui.timeline.timeline_perf import (
        invalidate_scoped_snapshot_cache,
        schedule_scoped_snapshot_prime,
        timeline_gesture_snapshot,
    )

    app = QApplication(["clip-waveform-offline-regression"])
    QMessageBox.information = lambda *args: None
    QMessageBox.warning = lambda *args: None

    def bind(owner, path, cls_name, method, **context):
        source = root / path
        tree = ast.parse(source.read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
        node = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == method)
        name = path.removesuffix(".py").replace("/", ".")
        namespace = {"__name__": name, "__package__": name.rpartition(".")[0], **context}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
        setattr(owner, method, MethodType(namespace[method], owner))

    def clip(cid, reverse=False):
        values = np.rint(np.linspace(10, 230, 81) + np.tile([0, 2, -2], 27)).astype(np.uint8)
        frames = []
        for index, value in enumerate(values):
            data = np.zeros(512, dtype=np.uint8)
            data[0] = 255 - value if reverse else value
            frame = DMXFrame(1, index * .025, data)
            if kind == "lazy-b64":
                frame = _LazyDMXFrame.from_frame_dict(frame.to_dict())
            elif kind == "lazy-bytes":
                frame = _LazyDMXFrame(1, index * .025, raw_bytes=data.tobytes())
            frames.append(frame)
        return Clip(id=cid, track_id="track", duration=2.1, frames=frames,
                    active_channels_by_universe={1: [1]})

    def data_state(value):
        return tuple((f.universe, f.timestamp, np.asarray(f.data).tobytes()) for f in value.frames)

    manager = ProjectManager()
    track = Track(id="track", clips=[clip("a"), clip("b", reverse=True)])
    manager._project.tracks = [track]
    manager._project.programs = [TimelineProgram(id="program", tracks=copy.deepcopy([track]))]
    manager._project.current_program_id = "program"
    view = ClipView(track, 80)
    view.set_selected_ids({"a"})
    items = dict(view._items)
    items["a"]._hover = True

    container = SimpleNamespace(_views=[view], _layout_views=lambda: None, _update_geometry=lambda: None,
                                update=lambda: None, _playhead_overlay=SimpleNamespace(raise_=lambda: None))
    for method in ("refresh_tracks", "_rebind_or_create_view"):
        bind(container, "src/ui/timeline/clip_area.py", "_TrackContainer", method,
             _view_kind_changed=lambda old, new: False)
    area = SimpleNamespace(_container=container, sync_scroll_y=lambda value: None)
    bind(area, "src/ui/timeline/clip_area.py", "ClipArea", "refresh_tracks")
    timeline = SimpleNamespace(_session=manager.project, ctrl=SimpleNamespace(_session=manager.project),
                               _track_list=SimpleNamespace(refresh_tracks=lambda tracks: None, get_scroll_y=lambda: 0),
                               _clip_area=area, _bind_mod_lane_editors=lambda: None,
                               _sync_timeline_duration=lambda: None, _workspace_context=SimpleNamespace(refresh=lambda: None),
                               _setup_marker_support=lambda: None, _get_pm=lambda: manager)
    for method in ("rebind_session", "_reload_tracks"):
        bind(timeline, "src/ui/timeline/timeline_widget.py", "TimelineWidget", method,
             schedule_scoped_snapshot_prime=schedule_scoped_snapshot_prime, DOMAIN_TIMELINE=DOMAIN_TIMELINE)
    window = SimpleNamespace(_pm=manager, _engine=None, _timeline_widget=timeline, _use_timeline_widget=True,
                             _refresh_program_list=lambda: None, _update_title=lambda: None)
    for method in ("_sync_timeline_project", "_refresh_tracks", "_undo", "_redo"):
        bind(window, "src/ui/main_window.py", "MainWindow", method)
    manager.project_changed.connect(lambda: window._refresh_tracks())

    class SmoothingHost:
        @property
        def ctrl(self):
            def find(cid):
                current_track = manager.project.tracks[0]
                return current_track, next(c for c in current_track.clips if c.id == cid)
            return SimpleNamespace(_session=manager.project, _find_track_and_clip=find)

        def _resolve_context_clip_ids(self, cid):
            return [cid]

        def _snapshot_for_undo(self):
            assert timeline_gesture_snapshot(manager, DOMAIN_TIMELINE)

        def _refresh_clips_targeted(self, ids, reset_waveform):
            for cid in ids:
                view.refresh_geometry_only(cid, reset_waveform=reset_waveform)

        def _mark_project_dirty(self):
            invalidate_scoped_snapshot_cache(manager)
            manager._mark_dirty()

    def draw(item, filename):
        image = QImage(180, 64, QImage.Format.Format_ARGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        item._draw_waveform(painter, 180, 64, QColor("#4A90D9"))
        painter.end()
        image.save(str(output / filename))
        return tuple(item._waveform_cache_bars), image.constBits().asstring(image.sizeInBytes())

    before = {cid: draw(item, f"{cid}-before.png") for cid, item in items.items()}
    before_data = {cid: data_state(item._clip) for cid, item in items.items()}
    original_b_cache = items["b"]._waveform_cache_bars
    geometry = {cid: item.geometry().getRect() for cid, item in items.items()}

    def ui_state_preserved():
        assert all(view._items[cid] is item for cid, item in items.items())
        assert items["a"]._selected and items["a"]._hover
        assert not items["b"]._selected and view._selected_ids == {"a"}
        assert view._px_per_sec == 80 and all(item._px_per_sec == 80 for item in items.values())
        assert {cid: item.geometry().getRect() for cid, item in items.items()} == geometry
        assert data_state(items["b"]._clip) == before_data["b"]

    if scenario == "unchanged":
        original_cache = items["a"]._waveform_cache_bars
        for _ in range(20):
            window._refresh_tracks()
            assert draw(items["a"], "a-unchanged.png") == before["a"]
            assert items["a"]._waveform_cache_bars is original_cache
            assert items["b"]._waveform_cache_bars is original_b_cache
            ui_state_preserved()
    elif scenario in ("replacement", "dragging"):
        replacement = clip("a", reverse=True)
        items["a"]._is_dragging = scenario == "dragging"
        new_track = Track(id="track", clips=[replacement, items["b"]._clip])
        container.refresh_tracks([new_track])
        assert items["a"]._clip is replacement and items["a"]._track is new_track
        assert items["a"]._waveform_cache_bars is None, "Rebound clip retains previous waveform cache"
        assert items["b"]._waveform_cache_bars is original_b_cache, "Unchanged peer cache was cleared"
        assert draw(items["a"], "a-replacement.png") == before["b"]
        assert draw(items["b"], "b-unchanged.png") == before["b"]
        assert data_state(items["a"]._clip) == before_data["b"]
        ui_state_preserved()
    else:
        assert apply_smoothing(SmoothingHost(), "a", "heavy")
        after = draw(items["a"], "a-smoothed.png")
        assert after != before["a"], "The fixture must visibly change after real smoothing"
        after_data = data_state(items["a"]._clip)
        for iteration in range(4):
            window._undo()
            assert items["a"]._clip is manager.project.tracks[0].clips[0]
            assert timeline.ctrl._session is manager.project
            assert data_state(items["a"]._clip) == before_data["a"], "Data undo failed independently of painting"
            assert draw(items["a"], f"a-undo-{iteration}.png") == before["a"], "Undo retains smoothed waveform"
            assert draw(items["b"], f"b-undo-{iteration}.png") == before["b"]
            ui_state_preserved()
            window._redo()
            assert data_state(items["a"]._clip) == after_data
            assert draw(items["a"], f"a-redo-{iteration}.png") == after, "Redo retains original waveform"
            assert draw(items["b"], f"b-redo-{iteration}.png") == before["b"]
            ui_state_preserved()
    assert app is QApplication.instance()


if __name__ == "__main__" and len(sys.argv) == 6 and sys.argv[1] == "--worker":
    _run_case(Path(sys.argv[2]), sys.argv[3], sys.argv[4], Path(sys.argv[5]))
