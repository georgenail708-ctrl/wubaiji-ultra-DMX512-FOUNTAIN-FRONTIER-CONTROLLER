"""Minimal public synthetic fixtures; no original-show dependency."""
from contextlib import ExitStack
from pathlib import Path

import numpy as np
import pytest

ROOT = next(path for path in Path(__file__).resolve().parents if (path/'src/core/models.py').is_file())

def payload(value):
    data = (np.arange(512, dtype=np.uint16) + value).astype(np.uint8)
    mask = np.arange(512) % 7 == value % 7
    return data, mask

def make_reference(project):
    from src.core.project_manager import ProjectManager
    from src.engine.playback_engine import PlaybackEngine
    from src.network.network_manager import NetworkManager
    pm, nm = ProjectManager(), NetworkManager()
    pm._project = project
    nm.set_fixture_defaults_from_project(project)
    return PlaybackEngine(nm, pm), nm

def synthetic_project():
    from src.core.models import Clip, DMXFrame, FixtureDefinition, FixtureInstance, Project, Track
    definition = FixtureDefinition(id="a2-def", name="Pump", channels=["pump"], channel_count=1)
    fixtures = [FixtureInstance(id=f"a2-fx-{i}", def_id=definition.id, universe=1,
                                start_channel=i + 1, pos_x=float(i)) for i in range(8)]
    tracks = []
    for index, (start, duration, hold) in enumerate(((0., 1., True), (1.5, 1., False), (4., 2., False))):
        frames = [DMXFrame(timestamp=j / 10., data=payload(41 + index * 70 + j)[0], universe=1)
                  for j in range(11)]
        clip = Clip(id=f"a2-clip-{index}", track_id=f"a2-track-{index}", start_time=start,
                    duration=duration, source_duration=1., frames=frames, clip_universes=[1],
                    fade_in=.5, fade_out=.4, hold_mode="last_frame" if hold else "none",
                    hold_last_frame=hold, active_channels_by_universe={1: list(range(1, 9))},
                    record_output_source_enabled=True)
        tracks.append(Track(id=clip.track_id, name=f"Track {index}", universes=[1], clips=[clip]))
    seq = Clip(id="a2-seq", track_id="a2-seq-track", start_time=3., duration=2., source_duration=1.,
               frames=tracks[-1].clips[0].frames[:], clip_universes=[1], fade_in=.7,
               fade_groups=2, fade_in_direction="ltr", fade_group_sequence_mode="sequential",
               fade_group_sequence_batches=2, active_channels_by_universe={1: list(range(1, 9))})
    tracks.append(Track(id=seq.track_id, name="Original sequential fallback", universes=[1], clips=[seq]))
    return Project(name="A2 original CE history", fixtures=fixtures, fixture_defs=[definition], tracks=tracks)

@pytest.fixture
def mounted_world(qapp, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "render_lab"))
    monkeypatch.setenv("QASCADE_CT_ENGINE", "1")
    from qascade_ce._mount_ce import _mount_ce
    status = _mount_ce()
    assert status.active and status.installed, status.to_dict()
    stack = ExitStack()
    def owned(project):
        engine, nm = make_reference(project)
        stack.callback(engine.deleteLater)
        stack.callback(nm.deleteLater)
        stack.callback(nm.stop)
        stack.callback(engine._timer.stop)
        stack.callback(engine.stop_gen_thread)
        for layer in ("effect", "fader", "linker"):
            nm.update_layer(layer, 7, *payload(0))  # sparse active-zero is protected too
        return engine, nm
    try:
        yield owned
    finally:
        stack.close()
