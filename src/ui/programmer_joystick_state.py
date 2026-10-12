"""Project-only joystick mode/axis targets; never capture driver objects."""
from __future__ import annotations

from PyQt6.QtCore import QSignalBlocker

_TARGETS = ("joy_amplitude_x", "joy_amplitude_y", "joy_amplitude_z")


def capture_controls(panel) -> dict:
    rows = []
    for row in getattr(getattr(panel, "_ch_list", None), "_rows", []) or []:
        rows.append({
            "def_id": str(getattr(row, "_def_id", "") or ""),
            "ch_types": list(getattr(row, "_ch_types", []) or []),
            "mode": getattr(row, "_joy_mode", "additive"),
            "targets": [getattr(row, f"_joy_amp_{axis}_target", default)
                        for axis, default in zip("xyz", _TARGETS, strict=True)],
        })
    return {"version": 1, "rows": rows}


def _valid_row(saved) -> bool:
    return (isinstance(saved, dict)
            and isinstance(saved.get("def_id"), str)
            and isinstance(saved.get("ch_types"), list)
            and all(isinstance(ch, str) for ch in saved["ch_types"])
            and saved.get("mode") in ("additive", "amplitude")
            and isinstance(saved.get("targets"), list)
            and len(saved["targets"]) == 3
            and all(target is None or target in _TARGETS for target in saved["targets"]))


def restore_controls(panel, state) -> None:
    if (not isinstance(state, dict) or type(state.get("version")) is not int
            or state["version"] != 1 or not isinstance(state.get("rows"), list)):
        return
    index = {(saved["def_id"], tuple(saved["ch_types"])): saved
             for saved in state["rows"] if _valid_row(saved)}
    changed_row = None
    for row in getattr(getattr(panel, "_ch_list", None), "_rows", []) or []:
        saved = index.get((str(getattr(row, "_def_id", "") or ""),
                           tuple(getattr(row, "_ch_types", []) or [])))
        if saved is None:
            continue
        current_targets = [getattr(row, f"_joy_amp_{axis}_target", default)
                           for axis, default in zip("xyz", _TARGETS, strict=True)]
        if (getattr(row, "_joy_mode", "additive") != saved["mode"]
                or current_targets != saved["targets"]):
            changed_row = row
        row._joy_mode = saved["mode"]
        for axis, target in zip("xyz", saved["targets"], strict=True):
            setattr(row, f"_joy_amp_{axis}_target", target)
            combo = getattr(row, f"_joy_amp_{axis}_combo", None)
            if combo is not None:
                with QSignalBlocker(combo):
                    combo.setCurrentText(target or "(不使用)")
        combo = getattr(row, "_joy_mode_combo", None)
        if combo is not None:
            with QSignalBlocker(combo):
                combo.setCurrentText("幅度控制模式" if row._joy_mode == "amplitude" else "叠加模式")
        frame = getattr(row, "_joy_amp_config_frame", None)
        if frame is not None:
            frame.setVisible(row._joy_mode == "amplitude")
    if changed_row is not None:
        # Match the real mode-switch release without synthesizing driver input.
        # Keep live values when an unchanged project/undo state is restored.
        changed_row._clear_joy_amplitude_overrides()
