"""PPT-M03 pending generator options, persisted state and scoped edit adapter.

Reuse native Qt number rows and the repository's Matrix scoped undo path.
References: Chaser Editor per-step/common controls, r189-library-ppt.md.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, QSignalBlocker, pyqtSlot
from PyQt6.QtWidgets import QDoubleSpinBox, QLabel, QSpinBox, QVBoxLayout, QWidget

from .matrix_ppt_composition_sequences import (
    SEQUENCE_OPTION_FIELDS, consumed_sequence_options, normalize_sequence_options,
)
from .matrix_slide_properties_ui import native_form


def _valid_key(key) -> str:
    from .matrix_slide_engine import BUILTIN_SEQUENCES
    return key if isinstance(key, str) and key in BUILTIN_SEQUENCES else ""


def _normalized_state(raw=None) -> dict:
    state = raw if isinstance(raw, dict) and raw.get("version") == 1 else {}
    applied = state.get("applied") if isinstance(state.get("applied"), dict) else {}
    applied_key = _valid_key(applied.get("id"))
    return {"version": 1, "selected_id": _valid_key(state.get("selected_id")),
            "values": normalize_sequence_options(state.get("values")),
            "applied": {"id": applied_key,
                        "values": consumed_sequence_options(applied_key, applied.get("values"))}}


def _panel_options(panel) -> dict:
    return _normalized_state(getattr(panel, "_ppt_sequence_options", None))


def save_composition_state(panel, state: dict) -> dict:
    """Capture pending selection/options and actual applied source without reading frames."""
    options = _panel_options(panel)
    combo = getattr(panel, "_preset_combo", None)
    if combo is not None:
        options["selected_id"] = _valid_key(combo.currentData())
    state["ppt_sequence_options"] = options
    return state


def restore_composition_state(panel, state) -> None:
    raw = state.get("ppt_sequence_options") if isinstance(state, dict) else None
    panel._ppt_sequence_options = _normalized_state(raw)
    context = getattr(panel, "_ppt_composition_context", None)
    if context is not None:
        context.restore()


def load_composition_options(panel, key) -> dict:
    return consumed_sequence_options(key, _panel_options(panel)["values"])


def record_applied_composition(panel, key, values) -> None:
    state = _panel_options(panel)
    state["selected_id"] = _valid_key(key)
    state["applied"] = {"id": _valid_key(key), "values": consumed_sequence_options(key, values)}
    panel._ppt_sequence_options = state
    context = getattr(panel, "_ppt_composition_context", None)
    if context is not None:
        context.refresh_selection()


class CompositionOptionsContext(QObject):
    def __init__(self, panel, host, form, hint):
        super().__init__(panel)
        self._host, self._form, self._hint = host, form, hint

    @pyqtSlot()
    def refresh_selection(self) -> None:
        from .matrix_ppt_catalog import entry_for_key
        panel = self.parent()
        key = _valid_key(panel._preset_combo.currentData())
        fields = SEQUENCE_OPTION_FIELDS.get(key, ())
        for field, spin in panel._ppt_composition_spins.items():
            self._form.setRowVisible(spin, field in fields)
        self._host.setVisible(bool(fields))
        state = _panel_options(panel)
        state["selected_id"] = key
        panel._ppt_sequence_options = state
        applied = entry_for_key(state["applied"]["id"])
        prefix = f"生成来源：{applied.label}。" if applied else ""
        self._hint.setText(prefix + "更改生成选项后点击“加载”。")

    @pyqtSlot()
    def commit(self) -> None:
        from .matrix_ppt_catalog import snapshot_sequence_edit, store_sequence_edit
        panel = self.parent()
        state = _panel_options(panel)
        values = normalize_sequence_options({key: spin.value()
                                            for key, spin in panel._ppt_composition_spins.items()})
        if values == state["values"]:
            return
        snapshot_sequence_edit(panel)
        state["values"] = values
        panel._ppt_sequence_options = state
        self.restore_values()
        store_sequence_edit(panel)
        pm = getattr(panel, "_pm", None)
        if pm is not None and pm.project is not None:
            pm._mark_dirty()

    def restore_values(self) -> None:
        panel = self.parent()
        values = _panel_options(panel)["values"]
        spins = panel._ppt_composition_spins
        blockers = [QSignalBlocker(spin) for spin in spins.values()]
        try:
            spins["rhythm_pulses"].setMaximum(values["meter_steps"])
            for field, spin in spins.items():
                spin.setValue(values[field])
        finally:
            del blockers

    def restore(self) -> None:
        from .matrix_ppt_catalog import entry_for_key
        panel = self.parent()
        state = _panel_options(panel)
        entry = entry_for_key(state["selected_id"])
        combo, category, search = panel._preset_combo, panel._ppt_category_combo, panel._ppt_search_edit
        blockers = [QSignalBlocker(widget) for widget in (combo, category, search)]
        try:
            search.clear()
            category.setCurrentText(entry.category if entry else "基础开合")
            panel._ppt_catalog_context.refill()
            combo.setCurrentIndex(max(0, combo.findData(state["selected_id"])))
        finally:
            del blockers
        self.restore_values()
        panel._ppt_catalog_context.refresh_selection()
        self.refresh_selection()


def _number_spin(field):
    if field == "damping":
        spin = QDoubleSpinBox()
        spin.setRange(.5, .9)
        spin.setDecimals(2)
        spin.setSingleStep(.05)
        spin.setToolTip("每次落点保留前一次强度的比例；同时决定收拢与间歇增长。")
    else:
        spin = QSpinBox()
        ranges = {"clock_ratio": (2, 4), "meter_steps": (8, 32), "rhythm_pulses": (1, 16)}
        spin.setRange(*ranges[field])
        tips = {"clock_ratio": "第二亮团相对第一亮团的推进速度。",
                "meter_steps": "一小节的总步数，包含有落点与静默的步。",
                "rhythm_pulses": "均匀分布在一小节里的落点数；强、弱、次强循环。"}
        spin.setToolTip(tips[field])
    spin.setKeyboardTracking(False)
    return spin


def build_composition_controls(panel) -> QWidget:
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    hint = QLabel("更改生成选项后点击“加载”。")
    hint.setWordWrap(True)
    layout.addWidget(hint)
    form = native_form()
    labels = {"clock_ratio": "第二亮团速度比", "meter_steps": "每节步数",
              "rhythm_pulses": "每节落点数", "damping": "余音保留比例"}
    panel._ppt_sequence_options = _panel_options(panel)
    panel._ppt_composition_spins = {}
    context = CompositionOptionsContext(panel, host, form, hint)
    panel._ppt_composition_context = context
    for field, label in labels.items():
        spin = _number_spin(field)
        spin.setAccessibleName(label)
        spin.setValue(panel._ppt_sequence_options["values"][field])
        spin.editingFinished.connect(context.commit)
        form.addRow(label, spin)
        panel._ppt_composition_spins[field] = spin
    layout.addLayout(form)
    panel._preset_combo.currentIndexChanged.connect(context.refresh_selection)
    context.refresh_selection()
    return host
