"""Compiled patch and shared final-payload decoder; no evaluation or output.

References: coarse/fine channel semantics, Qt/model adapter:
ai-memory/_references/r185-basic-closeout-preview.md. Final bytes are immutable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite

from .output_preview_profiles import compile_profile, water_level


def _number(value, default=0.0):
    try:
        result = float(value)
        return result if isfinite(result) else default
    except (TypeError, ValueError):
        return default


def channel_kind(name):
    name = str(getattr(name, "value", name))
    base = name.split("#", 1)[0]
    if name.endswith("_fine") and not base.endswith("_fine"):
        base += "_fine"
    return base


@dataclass(frozen=True, slots=True)
class PreviewChannel:
    name: str
    kind: str
    slot: int
    fine_slot: int | None = None
    angle_range: tuple = (-180.0, 180.0)
    modes: tuple = ()


@dataclass(frozen=True, slots=True)
class PreviewFixture:
    fixture_id: str
    name: str
    universe: int
    channels: tuple
    position_2d: tuple
    position_3d: tuple
    rotation: tuple
    patch_error: str = ""
    profile: dict = field(default_factory=dict)


@dataclass(slots=True)
class FixtureOutput:
    fixture: PreviewFixture
    values: tuple = ()
    rgb: tuple = (0.0, 0.0, 0.0)
    light: float = 0.0
    water: float = 0.0
    motors: tuple = ()
    observed: bool = False
    attempted_at: float = 0.0
    movements: tuple = ()
    zoom: float = 0.0


def _fine_slot(channels, index, kind):
    if kind.endswith("_fine"):
        return None
    if index + 1 >= len(channels):
        return None
    coarse = str(getattr(channels[index], "value", channels[index]))
    following = str(getattr(channels[index + 1], "value", channels[index + 1]))
    base, separator, suffix = coarse.partition("#")
    alternate = base + "_fine" + separator + suffix
    paired = following in (coarse + "_fine", alternate)
    return index + 1 if paired else None


def _channels(definition, start):
    channels = list(getattr(definition, "channels", ()) or ())
    ranges = getattr(definition, "motor_ranges", {}) or {}
    modes = getattr(definition, "channel_modes", {}) or {}
    result = []
    for index, name in enumerate(channels):
        kind = channel_kind(name)
        fine = _fine_slot(channels, index, kind)
        angle = ranges.get(str(name), ranges.get(kind, (-180.0, 180.0)))
        result.append(PreviewChannel(
            str(name), kind, start + index,
            None if fine is None else start + fine,
            tuple(_number(x) for x in angle[:2]),
            tuple(modes.get(str(name), ()) or ())))
    return tuple(result)


def _fixture(fi, definition, positions):
    fid = str(fi.id)
    pos = positions.get(fid, (fi.pos_x, fi.pos_y))
    start = int(fi.start_channel) - 1
    channels = _channels(definition, start) if definition is not None else ()
    error = "" if channels else "未定义通道"
    if start < 0 or any(ch.slot >= 512 for ch in channels):
        error = "配接超出 512 通道"
    # Schema has no imported-world validity flag: preserve valid (0, 0),
    # consistently display local installation coordinates and label them.
    xyz = tuple(_number(getattr(fi, name, 0)) for name in ("pos_x", "pos_z", "pos_y"))
    rotation = tuple(_number(getattr(fi, name, 0)) for name in ("rot_x", "rot_y", "rot_z"))
    profile = compile_profile(fi, definition)
    return PreviewFixture(fid, str(fi.name), int(fi.universe), channels,
                          (_number(pos[0]), _number(pos[1])), xyz, rotation, error, profile)


def channel_value(channel, payload):
    if channel.fine_slot is None:
        return payload[channel.slot] / 255.0
    return ((payload[channel.slot] << 8) | payload[channel.fine_slot]) / 65535.0


def _light_value(levels):
    has_rgb = any(key in levels for key in ("R", "G", "B", "W"))
    dimmer = levels.get("dimmer", 1.0 if has_rgb else 0.0)
    white = levels.get("W", 0.0)
    rgb = tuple(min(1.0, levels.get(key, 0.0) + white) for key in ("R", "G", "B"))
    if not has_rgb:
        rgb = (1.0, 1.0, 1.0)
    return tuple(value * dimmer for value in rgb), dimmer * max(rgb)


def decode_fixture(output, frame):
    fixture = output.fixture
    if fixture.patch_error:
        return
    values = tuple(channel_value(ch, frame.payload) for ch in fixture.channels)
    levels = {}
    motors = []
    movements = []
    raw = {}
    for channel, value in zip(fixture.channels, values):
        levels[channel.kind] = max(levels.get(channel.kind, 0.0), value)
        raw[channel.kind] = frame.payload[channel.slot]
        if channel.kind.startswith("motor_") and not channel.kind.endswith("_fine"):
            motion = fixture.profile["motors"].get(channel.kind)
            if motion is not None and motion["role"] == "rotate":
                fraction = 1 - value if motion["invert"] else value
                actual = motion["min"] + (motion["max"] - motion["min"]) * fraction
                movements.append((channel.kind, motion, actual, value))
                motors.append((channel.kind, actual))
    output.values, output.motors = values, tuple(motors)
    output.movements = tuple(movements)
    output.rgb, output.light = _light_value(levels)
    output.water = water_level(levels, raw, fixture.profile)
    output.zoom = levels.get(fixture.profile["water"].get("zoom_channel", ""), 0)
    output.observed, output.attempted_at = True, frame.attempted_at_monotonic


@dataclass
class PreviewData:
    """One bounded latest-payload entry per patched universe, shared by views."""

    outputs: list = field(default_factory=list)
    by_universe: dict = field(default_factory=dict)
    frames: dict = field(default_factory=dict)
    epoch: int = -1
    revision: int = 0

    def configure(self, project, positions):
        definitions = {str(fd.id): fd for fd in project.fixture_defs}
        self.outputs = [FixtureOutput(_fixture(fi, definitions.get(str(fi.def_id)), positions))
                        for fi in project.fixtures]
        self.by_universe = {}
        for output in self.outputs:
            self.by_universe.setdefault(output.fixture.universe, []).append(output)
        self.clear()

    def clear(self):
        self.frames.clear()
        for output in self.outputs:
            output.observed = False
        self.revision += 1

    def update(self, reader):
        epoch = reader.epoch
        changed = epoch != self.epoch
        if changed:
            self.clear()
            self.epoch = epoch
        frames = reader.latest(universes=self.by_universe)
        # The binding can change between the two public reads; never retain
        # an old epoch or upgrade it with the current epoch.
        if reader.epoch != epoch:
            self.clear()
            return True
        for frame in frames:
            changed = self._accept(frame) or changed
        if changed:
            self.revision += 1
        return changed

    def _accept(self, frame):
        if frame.epoch != self.epoch:
            return False
        previous = self.frames.get(frame.universe_1based)
        if previous is not None and previous.attempt_revision == frame.attempt_revision:
            return False
        self.frames[frame.universe_1based] = frame
        outputs = self.by_universe.get(frame.universe_1based, ())
        same = previous is not None and previous.payload == frame.payload
        for output in outputs:
            if same:
                output.attempted_at = frame.attempted_at_monotonic
            else:
                decode_fixture(output, frame)
        return not same


def _describe_channel(channel, payload):
    """Format one original raw/fine/mode channel without changing its value."""
    raw = payload[channel.slot]
    text = f"{channel.name} [{channel.slot + 1}] = {raw}"
    if channel.fine_slot is not None:
        text += f" / 16 bit {(raw << 8) | payload[channel.fine_slot]}"
    for mode in channel.modes:
        if int(mode.get("dmx_min", 0)) <= raw <= int(mode.get("dmx_max", 255)):
            text += " · " + str(mode.get("name", ""))
    return text


def describe_output(output, frame):
    fi = output.fixture
    lines = [fi.name, f"U{fi.universe} · {fi.fixture_id}"]
    lines.append("空间: " + str(fi.profile.get("source", "未校准")))
    water = fi.profile.get("water", {})
    if water.get("role") != "unknown":
        label = "源参数水高" if water.get("height_known") else "相对水高（未校准）"
        lines.append(label)
    for kind, motion, actual, value in output.movements:
        lines.append(f"{kind}: {actual:.2f} {motion['unit']} · {motion['role']}")
    if not output.observed or frame is None:
        return "\n".join(lines + [fi.patch_error or "尚无当前最终输出"])
    for channel in fi.channels:
        lines.append(_describe_channel(channel, frame.payload))
    return "\n".join(lines)
