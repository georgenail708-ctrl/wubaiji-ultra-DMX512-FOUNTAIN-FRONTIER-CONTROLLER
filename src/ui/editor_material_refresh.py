"""Coalesce decorative preparation with Qt-owned queued receiver lifetime.

References: Qt qtbase v6.11.0 qapplication.cpp event compression and
qwidgetrepaintmanager.cpp UpdateRequest batching; no C++ source is copied.
Original background sources, pixels and cache limits remain in editor_backdrop.
"""
from __future__ import annotations

import weakref

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QWidget


def _live_widget(widget, window):
    return (widget is not None and not sip.isdeleted(widget)
            and widget.isVisible() and widget.window() is window)


def _live_material(layer, window):
    from . import editor_materials as materials
    if layer is None or sip.isdeleted(layer):
        return False
    owner = layer.parentWidget()
    if not _live_widget(owner, window) or materials.is_deep(owner):
        return False
    current = getattr(owner, "_editor_material_layer", layer)
    return current is layer and owner.property("editorMaterial") == layer.kind


def _outer_scopes(layers):
    scopes = []
    for layer in layers:
        owner = layer.parentWidget()
        if any(scope is owner or scope.isAncestorOf(owner) for scope in scopes):
            continue
        scopes = [scope for scope in scopes if not owner.isAncestorOf(scope)]
        scopes.append(owner)
    return scopes


def _merge_target(widget, window, layers, painted):
    from . import editor_materials as materials
    from .editor_chrome import PAINTED_BACKGROUNDS
    if isinstance(widget, materials.MaterialLayer):
        if _live_material(widget, window):
            layers[id(widget)] = widget
        return
    if type(widget).__name__ in PAINTED_BACKGROUNDS:
        if _live_widget(widget, window) and not materials.is_deep(widget):
            painted[id(widget)] = widget


def _collect_targets(layers, window):
    targets = {id(layer): layer for layer in layers}
    painted = {}
    for owner in _outer_scopes(layers):
        _merge_target(owner, window, targets, painted)
        for widget in owner.findChildren(QWidget):
            _merge_target(widget, window, targets, painted)
    return tuple(targets.values()), tuple(painted.values())


def _prepare_material(layer):
    from . import editor_materials as materials
    if layer.kind != "root":
        layer._backdrop_key = materials.prepare_backdrop(layer.parentWidget())
    layer.update()


def refresh_materials(layers, window=None):
    """Prepare all final sources before any dependent final frame, once each."""
    from . import editor_materials as materials
    from .qascade_theme import EDITOR_IVORY
    if not layers:
        return
    window = window or layers[0].window()
    live = [layer for layer in layers if _live_material(layer, window)]
    targets, painted = _collect_targets(live, window)
    for layer in targets:
        if layer.kind == "root":
            materials.provide_editor_background(layer.parentWidget(), EDITOR_IVORY)
    for layer in targets:
        _prepare_material(layer)
    for widget in painted:
        materials.prepare_painted_background(widget)
        widget.update()


class _MaterialRefreshBatch(QObject):
    """One pending native queued delivery per real window; targets are weak."""

    _wake = pyqtSignal()

    def __init__(self, window):
        super().__init__(window)
        self._pending = {}
        self._armed = False
        self._wake.connect(self._flush, Qt.ConnectionType.QueuedConnection)

    def request(self, layer):
        self._pending[id(layer)] = weakref.ref(layer)
        if not self._armed:
            self._armed = True
            self._wake.emit()

    @pyqtSlot()
    def _flush(self):
        references, self._pending = self._pending, {}
        self._armed = False
        layers = [reference() for reference in references.values()]
        refresh_materials(layers, self.parent())


def request_material_refresh(layer):
    """Geometry stays synchronous; only decorative CPU work joins a batch."""
    if sip.isdeleted(layer):
        return
    window = layer.window()
    if not _live_material(layer, window):
        return
    batch = getattr(window, "_editor_material_refresh_batch", None)
    if batch is None or sip.isdeleted(batch):
        batch = _MaterialRefreshBatch(window)
        window._editor_material_refresh_batch = batch
    batch.request(layer)
