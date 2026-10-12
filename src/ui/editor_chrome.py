"""Assembly/show seams for consistent light QWidget chrome and late popups.

Shared tokens drive paint resources; this adapter handles original local QSS and
material owners. It never changes widget identities, values, signals or geometry.
References: Qt QWidget show/palette.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QDockWidget, QLabel, QMenu, QWidget

from .editor_materials import (apply_material, chrome_icons, deep_region, is_deep,
                              owns_content_paint, protect_content_paint)
from .effect_workspace_style import _decorate_widget
from .editor_backdrop import prepare_painted_background

ROOT_CLASSES = frozenset({
    "MainWindow", "ProgrammerPanel", "EffectBrowserWidget", "ClipStudioPanel",
    "AudioModDrawPanel", "AudioModEditorPage", "TimelineWidget",
    "TopViewPanel", "PatchPanel", "DispatchPanel", "MatrixSlideshowPanel",
    "MatrixChildWindow", "MatrixLevelWindow", "RouteRunnerPanel", "TrackLinkerPanel",
    "NetworkConfigPanel", "MonitorPanel", "TutorialPanel", "OutputPreviewPanel",
})
PANEL_CLASSES = frozenset({
    "PanelSection", "CardPalette", "SourcePalette", "EffectCategoryBar",
    "GroupSelector", "ChannelTypeList", "FilterNumpad", "NetworkStatusCard",
})
RAISED_CLASSES = frozenset({"PanelToolbar", "TransportBar", "StatusLine"})
OUTPUT_CLASSES = frozenset({"FixtureCanvas", "RouteCanvas", "_GameCanvas",
                            "_ScreenPreviewCanvas", "OutputPreviewCanvas"})
CAPTURE_OVERLAYS = frozenset({"ScreenCaptureOverlay"})
PAINTED_BACKGROUNDS = frozenset({"SceneStoreView", "FixtureTableView"})


def prepare_chrome_widget(widget):
    """One real widget on Show; covers lazy children and Window/Tool/Popup alike."""
    if not isinstance(widget, QWidget) or type(widget).__name__ in CAPTURE_OVERLAYS:
        return
    if type(widget).__name__ == "MaterialLayer":
        return
    if type(widget).__name__ in OUTPUT_CLASSES:
        deep_region(widget)
    if is_deep(widget):
        return
    if getattr(widget, "_editor_chrome_busy", False):
        return
    widget._editor_chrome_busy = True
    try:
        _decorate_widget(widget)
        _material_owner(widget)
        if type(widget).__name__ in PAINTED_BACKGROUNDS:
            prepare_painted_background(widget)
    finally:
        widget._editor_chrome_busy = False


def _material_owner(widget):
    name = type(widget).__name__
    if owns_content_paint(widget):
        protect_content_paint(widget)
        return
    # QMenu/tooltip foreground is painted by its owner, not child controls.
    # A background child would cover that text; retain the shared light QSS.
    if isinstance(widget, (QDockWidget, QMenu, QLabel)) or name in PAINTED_BACKGROUNDS:
        return
    if widget.isWindow() or name in ROOT_CLASSES:
        apply_material(widget, kind="root")
        chrome_icons(widget)
    elif name in PANEL_CLASSES:
        apply_material(widget, kind="panel")
        chrome_icons(widget)
    elif name in RAISED_CLASSES:
        apply_material(widget, kind="raised")
        chrome_icons(widget)


def install_editor_chrome(root):
    """Explicit factory seam; app Show adapter covers subsequent lazy widgets."""
    # Protect already declared output owners before any neutral QSS translation.
    for widget in (root, *root.findChildren(QWidget)):
        prepare_chrome_widget(widget)
    chrome_icons(root)
