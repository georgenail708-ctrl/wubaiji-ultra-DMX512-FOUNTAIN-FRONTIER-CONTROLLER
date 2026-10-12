"""Real manager/QSettings regressions; no joystick, MIDI, or lighting output.

Run with: python -m pytest tests/test_joystick_control_binding_persistence.py
Only the two hardware-driver factories and the QSettings *destination* are
replaced. Binding, serialization, loading, Qt signals, and INI I/O are real.
"""

import json

import pytest
from PyQt6.QtCore import QObject, QSettings, pyqtSignal

from src.ui import joystick_control_binding as binding
from src.ui import midi_joystick_driver


class _IdlePhysicalDriver(QObject):
    axis3d_changed = pyqtSignal(float, float, float, float)


class _IdleMidiDriver(QObject):
    axis3d_changed = pyqtSignal(float, float, float)


@pytest.fixture
def isolated_manager(monkeypatch, tmp_path):
    """Use a fresh on-disk INI and inert signal sources for every test."""
    for variable in ("HOME", "USERPROFILE", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(variable, str(tmp_path))
    settings_path = tmp_path / "bindings.ini"

    def settings_factory(organization, application):
        assert organization == binding._SETTINGS_ORG
        assert application == binding._SETTINGS_APP
        settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
        settings.setFallbacksEnabled(False)
        return settings

    physical_driver = _IdlePhysicalDriver()
    midi_driver = _IdleMidiDriver()
    monkeypatch.setattr(binding, "get_physical_joystick_driver", lambda: physical_driver)
    monkeypatch.setattr(midi_joystick_driver, "get_midi_joystick_driver", lambda: midi_driver)
    monkeypatch.setattr(binding, "QSettings", settings_factory)

    def new_manager():
        return binding.JoystickControlBindManager()

    def settings():
        return settings_factory(binding._SETTINGS_ORG, binding._SETTINGS_APP)

    def reload_manager(manager):
        manager.save()
        stored = settings()
        stored.sync()
        assert stored.status() == QSettings.Status.NoError
        assert settings_path.is_file()
        return new_manager()

    return new_manager, reload_manager, settings


def test_effect_rule_key_uses_target_identity():
    rule = binding.JoystickControlBindingRule(
        ch_type="速度",
        physical_axis="x",
        target_kind="effect_param",
        channel="motor_x",
        param_key="speed",
        space_mode="3d",
    )
    assert rule.binding_key == ("effect:motor_x:speed", "")
    assert rule.to_dict()["binding_key"] == ["effect:motor_x:speed", ""]


def test_effect_lookup_survives_real_settings_roundtrip(isolated_manager):
    new_manager, reload_manager, _ = isolated_manager
    manager = new_manager()
    original = manager.add_effect_param_binding(
        "motor_x",
        "speed",
        "速度",
        "y",
        preset_id="wave",
        invert=True,
        range_lo=10,
        range_hi=1000,
        gamma=1.5,
    )
    assert manager.get_effect_param_binding("motor_x", "speed") is original
    loaded = reload_manager(manager)
    assert loaded.has_effect_param_binding("motor_x", "speed")
    restored = loaded.get_effect_param_binding("motor_x", "speed")
    assert restored.to_dict() == original.to_dict()
    assert restored.ch_type == "速度"


def test_same_label_on_two_channels_does_not_overwrite(isolated_manager):
    new_manager, reload_manager, _ = isolated_manager
    manager = new_manager()
    manager.add_effect_param_binding("motor_x", "speed", "速度", "x", preset_id="wave")
    manager.add_effect_param_binding("motor_y", "speed", "速度", "y", preset_id="sweep")
    loaded = reload_manager(manager)
    assert len(loaded.get_all_bindings()) == 2
    assert loaded.get_effect_param_binding("motor_x", "speed").physical_axis == "x"
    assert loaded.get_effect_param_binding("motor_y", "speed").physical_axis == "y"


def test_same_label_for_two_parameters_does_not_overwrite(isolated_manager):
    new_manager, reload_manager, _ = isolated_manager
    manager = new_manager()
    manager.add_effect_param_binding("motor_x", "speed", "参数", "x")
    manager.add_effect_param_binding("motor_x", "amplitude", "参数", "z")
    loaded = reload_manager(manager)
    assert len(loaded.get_all_bindings()) == 2
    assert loaded.has_effect_param_binding("motor_x", "speed")
    assert loaded.has_effect_param_binding("motor_x", "amplitude")


def test_effect_label_does_not_collide_with_channel_binding(isolated_manager):
    new_manager, reload_manager, _ = isolated_manager
    manager = new_manager()
    manager.add_binding("dimmer", "x")
    manager.add_effect_param_binding("motor_x", "speed", "dimmer", "y")
    loaded = reload_manager(manager)
    assert len(loaded.get_all_bindings()) == 2
    assert loaded.get_binding("dimmer").target_kind == "channel"
    assert loaded.get_effect_param_binding("motor_x", "speed").physical_axis == "y"


def test_removal_after_reload_is_saved_and_keeps_other_channel(isolated_manager):
    new_manager, reload_manager, _ = isolated_manager
    manager = new_manager()
    manager.add_effect_param_binding("motor_x", "speed", "速度", "x")
    manager.add_effect_param_binding("motor_y", "speed", "速度", "y")
    loaded = reload_manager(manager)
    removed = []
    loaded.binding_removed.connect(removed.append)
    assert loaded.remove_effect_param_binding("motor_x", "speed")
    assert not loaded.remove_effect_param_binding("motor_x", "speed")
    assert removed == [("effect:motor_x:speed", "")]
    loaded = reload_manager(loaded)
    assert not loaded.has_effect_param_binding("motor_x", "speed")
    assert loaded.has_effect_param_binding("motor_y", "speed")
    assert len(loaded.get_all_bindings()) == 1


def test_replacement_after_reload_does_not_leave_stale_binding(isolated_manager):
    new_manager, reload_manager, _ = isolated_manager
    manager = new_manager()
    manager.add_effect_param_binding("motor_x", "speed", "速度", "x")
    loaded = reload_manager(manager)
    loaded.add_effect_param_binding("motor_x", "speed", "新速度", "w")
    assert len(loaded.get_all_bindings()) == 1
    loaded = reload_manager(loaded)
    assert loaded.get_effect_param_binding("motor_x", "speed").physical_axis == "w"


def test_repeated_roundtrips_preserve_bindings_scales_and_routing(isolated_manager):
    new_manager, reload_manager, _ = isolated_manager
    manager = new_manager()
    manager.add_binding("dimmer", "z")
    manager.add_effect_param_binding("motor_x", "speed", "速度", "x")
    manager.add_effect_param_binding("motor_y", "speed", "速度", "y")
    manager.set_axis_scale("x", 0.5)
    for _ in range(3):
        manager = reload_manager(manager)
        assert len(manager.get_all_bindings()) == 3
        assert manager.get_axis_scale("x") == 0.5
        assert manager.has_effect_param_binding("motor_x", "speed")
        assert manager.has_effect_param_binding("motor_y", "speed")
    emitted = []
    channel_values = []
    manager.effect_param_changed.connect(lambda *values: emitted.append(values))
    manager.set_value_handler(lambda rule, value: channel_values.append((rule.ch_type, value)))
    manager._route_axis_values(1.0, 0.0, 0.5, 0.5)
    assert emitted == [("motor_x", "speed", 192), ("motor_y", "speed", 0)]
    assert channel_values == [("dimmer", 128.0)]


def test_normal_channel_and_space_mode_bindings_remain_compatible(isolated_manager):
    new_manager, reload_manager, _ = isolated_manager
    manager = new_manager()
    manager.add_binding("dimmer", "x")
    manager.add_binding("motor_x", "y", sub_axis="x", space_mode="2d", target_def_id="fixture-a")
    manager.add_binding("motor_x", "z", sub_axis="x", space_mode="3d", target_def_id="fixture-b")
    loaded = reload_manager(manager)
    assert loaded.get_binding("dimmer").binding_key == ("dimmer", "")
    assert loaded.get_binding("motor_x", "2d").target_def_id == "fixture-a"
    assert loaded.get_binding("motor_x", "3d").target_def_id == "fixture-b"
    assert loaded.add_binding("motor_x", "w", space_mode="2d") is None
    assert loaded.remove_binding("motor_x", "2d")
    assert loaded.has_binding("motor_x", "3d")


@pytest.mark.parametrize("saved_key", [None, ["速度", ""]])
def test_legacy_effect_without_optional_fields_uses_payload_identity(isolated_manager, saved_key):
    new_manager, reload_manager, settings = isolated_manager
    legacy = {
        "ch_type": "速度",
        "physical_axis": "x",
        "target_kind": "effect_param",
        "channel": "motor_x",
        "param_key": "speed",
    }
    if saved_key is not None:
        legacy["binding_key"] = saved_key
    stored = settings()
    stored.setValue(binding._SETTINGS_KEY, json.dumps([legacy], ensure_ascii=False))
    stored.sync()
    loaded = new_manager()
    restored = loaded.get_effect_param_binding("motor_x", "speed")
    assert restored is not None
    assert restored.param_label == restored.preset_id == restored.target_def_id == ""
    assert reload_manager(loaded).has_effect_param_binding("motor_x", "speed")


def test_legacy_effect_without_channel_uses_global_namespace(isolated_manager):
    new_manager, _, settings = isolated_manager
    stored = settings()
    stored.setValue(
        binding._SETTINGS_KEY,
        json.dumps(
            [
                {
                    "ch_type": "速度",
                    "physical_axis": "x",
                    "target_kind": "effect_param",
                    "param_key": "speed",
                    "binding_key": ["速度", ""],
                }
            ],
            ensure_ascii=False,
        ),
    )
    stored.sync()
    loaded = new_manager()
    assert loaded.has_effect_param_binding("", "speed")
    assert loaded.get_effect_param_binding("", "speed").channel == ""


def test_legacy_channel_without_extension_fields_keeps_default_target(isolated_manager):
    new_manager, reload_manager, settings = isolated_manager
    stored = settings()
    stored.setValue(
        binding._SETTINGS_KEY,
        json.dumps(
            [
                {
                    "ch_type": "dimmer",
                    "physical_axis": "x",
                    "binding_key": ["dimmer", ""],
                }
            ]
        ),
    )
    stored.sync()
    loaded = new_manager()
    rule = loaded.get_binding("dimmer")
    assert rule.target_kind == "channel"
    assert rule.channel == rule.param_key == rule.target_def_id == ""
    assert reload_manager(loaded).has_binding("dimmer")
