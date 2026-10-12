"""Native controls for actual music capabilities; canonical state stays in engine."""
from PyQt6.QtWidgets import QLabel, QPushButton
from .. import audio_pitch_controls as pitch

from .matrix_pixel_music_catalog import (MUSIC_COMBOS, MUSIC_DEFAULTS, MUSIC_MODES,
                                         MUSIC_NUMBERS)


def _default(key):
    return MUSIC_DEFAULTS.get(key, {"audio_bar_gamma": 0.85, "audio_bar_smoothing": 0.35}.get(key, 0.0))


def build_music_controls(panel, parent):
    from .matrix_pixel_properties_ui import combo_row, number_row, section_grid
    section, grid = section_grid("音乐空间编排", parent)
    panel._music_description = QLabel()
    panel._music_description.setWordWrap(True)
    grid.addWidget(panel._music_description, 0, 0, 1, 3)
    panel._music_widget_groups = {}
    panel._music_number_fields = {}
    panel._music_combo_fields = {}
    for row, (key, spec) in enumerate(MUSIC_NUMBERS.items(), 1):
        label, lo, hi, step, decimals, suffix = spec
        field = "_music_" + key
        number_row(panel, grid, row, (field, label, key, lo, hi, _default(key),
                                     step, decimals, False, {"suffix": suffix}))
        panel._music_number_fields[key] = field
        panel._music_widget_groups[key] = [getattr(panel, field + ending)
                                           for ending in ("_caption", "_slider", "_spin")]
    for row, (key, spec) in enumerate(MUSIC_COMBOS.items(), len(MUSIC_NUMBERS) + 1):
        field = "_music_" + key
        combo_row(panel, grid, row, (field, spec[0], key, spec[1]))
        panel._music_combo_fields[key] = field
        panel._music_widget_groups[key] = [getattr(panel, field + "_caption"), getattr(panel, field)]
    panel._music_pitch_note = QLabel("参考音频需启用已有音高检测；没有可信音高的片段保持不出光。")
    panel._music_pitch_note.setWordWrap(True)
    grid.addWidget(panel._music_pitch_note, len(MUSIC_NUMBERS) + len(MUSIC_COMBOS) + 1, 0, 1, 3)
    panel._music_pitch_button = QPushButton("分析当前参考音频的音高")
    panel._music_pitch_button.clicked.connect(lambda: request_pitch_analysis(panel))
    grid.addWidget(panel._music_pitch_button, len(MUSIC_NUMBERS) + len(MUSIC_COMBOS) + 2, 0, 1, 3)
    panel._music_section = section
    section.hide()
    return section


def _show_original_number(panel, field, visible):
    for suffix in ("_caption", "_slider", "_spin"):
        widget = getattr(panel, field + suffix, None)
        if widget is not None:
            widget.setVisible(visible)


def configure_music_controls(panel, mode):
    section = getattr(panel, "_music_section", None)
    if section is None:
        return
    specification = MUSIC_MODES.get(mode)
    panel._music_current_mode = mode
    section.setVisible(specification is not None)
    _show_original_number(panel, "_norm_offset", specification is None or "audio_band_layout" in specification["parameters"])
    _show_original_number(panel, "_amplitude", mode != "audio_music_pitch_steps")
    panel._speed_caption.setText("空间变化速度" if specification is not None else "效果速度")
    if specification is None:
        return
    panel._music_description.setText(specification["description"])
    for key, widgets in panel._music_widget_groups.items():
        for widget in widgets:
            widget.setVisible(key in specification["parameters"])
    panel._music_pitch_note.setVisible(mode == "audio_music_pitch_steps")
    panel._music_pitch_button.setVisible(mode == "audio_music_pitch_steps")
    refresh_pitch_status(panel)


def sync_music_controls(panel):
    engine = panel._engine
    if engine is None:
        return
    for key, field in panel._music_number_fields.items():
        panel._set_slider_spin_value(getattr(panel, field + "_slider"),
                                     getattr(panel, field + "_spin"), getattr(engine, key, _default(key)))
    for key, field in panel._music_combo_fields.items():
        panel._set_combo_data(getattr(panel, field), getattr(engine, key, "linear"))


def _existing_analyzer(panel):
    engine = getattr(panel, "_engine", None)
    return pitch.existing_analyzer(getattr(engine, "_analyzer", None))


def _pitch_file_ready(analyzer):
    return pitch.file_ready(analyzer)


def refresh_pitch_status(panel):
    if getattr(panel, "_music_current_mode", "") == "audio_music_pitch_steps":
        pitch.refresh_pitch_controls(_existing_analyzer(panel),
                                     panel._music_pitch_note, panel._music_pitch_button)


def request_pitch_analysis(panel):
    pitch.request_pitch_analysis(_existing_analyzer(panel))
    refresh_pitch_status(panel)
