"""Shared warm editor materials; bounded paint cache and native backdrop evidence.

Qt QWidget/QPainter/QPalette and documented DWM APIs are reused directly.
Only explicit background pixels are softened by editor_backdrop; no capture,
timer, worker or frameless window conversion.
References: r185-basic-closeout-frontend CLOSE-F-VIS (Qt
material analysis; website source licence unverified, no source block copied).
"""
from __future__ import annotations

from collections import OrderedDict
import ctypes
import re
import sys

from PyQt6.QtCore import QEvent, QObject, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPalette, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QAbstractButton, QDialog, QDockWidget, QMainWindow, QWidget
from .editor_backdrop import (
    background_pixmap, backdrop_pixmap, prepare_backdrop, provide_editor_background,
    prepare_painted_background,
)

_STRIPS = OrderedDict()
_DECLARATION = re.compile(r"(?P<prop>[-\w]+)\s*:\s*(?P<value>[^;{}]+)")
_HEX = re.compile(r"#[0-9a-fA-F]{6}(?![0-9a-fA-F])")
_PALETTES = {}
_ICONS = OrderedDict()


def warm_control_qss(stylesheet, colors):
    """Translate only legacy chrome colors; source selectors and geometry survive."""
    from .qascade_theme import LEGACY_CHROME_COLOR_ROLES
    roles = LEGACY_CHROME_COLOR_ROLES

    def declaration(match):
        prop, value = match["prop"], match["value"]
        if "color" not in prop and not prop.startswith(("background", "border")):
            return match.group()
        value = _HEX.sub(lambda item: _warm_qss_color(prop, item[0], roles, colors), value)
        value = re.sub(r"rgba\(255,\s*255,\s*255,\s*(?:4|13|15|17|21)\)",
                       colors["border"], value)
        value = re.sub(r"rgba\(71,\s*114,\s*179,\s*(?:110|120)\)",
                       colors["selected"], value)
        return f"{prop}:{value}"

    return _DECLARATION.sub(declaration, str(stylesheet or ""))


def _warm_qss_color(prop, value, roles, colors):
    key = roles.get(value.lower())
    if prop in ("color", "selection-color"):
        if key in ("root", "input", "panel", "surface", "border"):
            key = "ink"
    elif prop.startswith(("background", "selection-background", "alternate-background")):
        if key == "ink":
            key = "surface"
    return colors.get(key, value)


def _palette_colors(deep):
    from . import qascade_theme as theme
    if not deep:
        return theme.EDITOR_IVORY
    return {"root": theme.OUTPUT_BG_ROOT, "input": theme.OUTPUT_BG_INPUT_EDIT,
            "panel": theme.OUTPUT_BG_EDITOR, "surface": theme.OUTPUT_BG_PANEL,
            "border": theme.OUTPUT_BORDER_NORMAL, "ink": theme.OUTPUT_TEXT_PRIMARY,
            "muted": theme.OUTPUT_TEXT_SECONDARY, "selected": theme.SELECT_BLUE,
            "focus": theme.FOCUS_BLUE, "disabled": theme.OUTPUT_TEXT_DISABLED}


def editor_palette(*, deep=False):
    """Cached chrome/deep palettes; a canvas cannot inherit warm Window/Base."""
    if deep not in _PALETTES:
        colors = _palette_colors(deep)
        palette = QPalette()
        for role, key in (("Window", "root"), ("WindowText", "ink"),
                          ("Base", "input"), ("AlternateBase", "panel"),
                          ("Text", "ink"), ("Button", "surface"),
                          ("ButtonText", "ink"), ("Highlight", "selected"),
                          ("HighlightedText", "ink"), ("Light", "surface"),
                          ("Midlight", "panel"), ("Dark", "border"),
                          ("Mid", "border"), ("Shadow", "border")):
            palette.setColor(getattr(QPalette.ColorRole, role), QColor(colors[key]))
        for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText,
                     QPalette.ColorRole.ButtonText):
            palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled"]))
        _PALETTES[deep] = palette
    return _PALETTES[deep]


def is_deep(widget):
    """A declared data owner protects its complete descendant tree."""
    while isinstance(widget, QWidget):
        if widget.property("editorMaterial") == "deep":
            return True
        if widget.isWindow():
            return False
        widget = widget.parentWidget()
    return False


def owns_content_paint(widget):
    """A Python paintEvent owns its foreground; children always paint above it.

    Inspect the MRO rather than a canvas name list: this also protects lazy
    waveform, minimap, delegate-like QWidget and plugin painter subclasses.
    Native Qt containers may host a film beneath their original child controls.
    """
    return any("paintEvent" in cls.__dict__ and
               not cls.__module__.startswith("PyQt6.")
               for cls in type(widget).__mro__)


def protect_content_paint(widget):
    """Retire a film installed during transient top-level layout construction."""
    layer = getattr(widget, "_editor_material_layer", None)
    if layer is not None:
        widget.removeEventFilter(layer)
        layer._backdrop_key = None
        layer.hide()
    if widget.property("editorMaterial") in ("root", "panel", "raised", "input"):
        widget.setProperty("editorMaterial", "painted")
    widget.setPalette(editor_palette())


def deep_region(widget):
    """Keep the original canvas and supply a full-area dark background fallback."""
    if not isinstance(widget, QWidget):
        return
    from .qascade_theme import OUTPUT_BG_ROOT, OUTPUT_TEXT_PRIMARY
    widget.setProperty("editorMaterial", "deep")
    widget.setPalette(editor_palette(deep=True))
    widget.setAutoFillBackground(True)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    name = widget.objectName() or f"editorDeep{id(widget)}"
    widget.setObjectName(name)
    scope = f"\nQWidget#{name}{{background:{OUTPUT_BG_ROOT};color:{OUTPUT_TEXT_PRIMARY};}}"
    if scope not in widget.styleSheet():
        widget.setStyleSheet(widget.styleSheet() + _deep_controls_qss() + scope)
    layer = getattr(widget, "_editor_material_layer", None)
    if layer is not None:
        layer.hide()
    _deep_numeric_styles(widget)


def _deep_controls_qss():
    """Local descendant controls use the deep palette, retaining global geometry."""
    return """
QWidget {color:palette(window-text);}
QLabel {background:transparent;color:palette(window-text);}
QLineEdit,QTextEdit,QPlainTextEdit,QSpinBox,QDoubleSpinBox,QComboBox {
 background:palette(base);color:palette(text);border-color:palette(mid);}
QPushButton,QToolButton {background:palette(button);color:palette(button-text);border-color:palette(mid);}
QPushButton:checked,QPushButton:pressed,QToolButton:checked,QToolButton:pressed {
 background:palette(highlight);color:palette(highlighted-text);}
QPushButton:hover,QToolButton:hover {background:palette(light);}
QListWidget,QTreeWidget,QTreeView,QTableWidget,QTableView,QAbstractItemView {
 background:palette(base);color:palette(text);alternate-background-color:palette(alternate-base);}
QHeaderView::section,QTabBar::tab {background:palette(button);color:palette(window-text);}
QTabWidget::pane,QGroupBox {background:palette(window);color:palette(window-text);}
QSlider::groove {background:palette(base);}
QSlider::sub-page {background:palette(highlight);}
"""


def _deep_numeric_styles(root):
    from .number_slider_style import numeric_style
    for widget in (root, *root.findChildren(QWidget)):
        if hasattr(widget, "float_value") or hasattr(widget, "_expanded"):
            widget._numeric_style = numeric_style(ivory=False)


def _neutral_icon(pixmap):
    """Preserve colored status/data glyphs; only monochrome chrome is tinted."""
    pixels = pixmap.toImage()
    for y in range(0, pixels.height(), 2):
        for x in range(0, pixels.width(), 2):
            color = pixels.pixelColor(x, y)
            if color.alpha() > 80:
                channels = (color.red(), color.green(), color.blue())
                if max(channels) - min(channels) > 24:
                    return False
    return True


def chrome_icon(icon, size, ink):
    """Bounded cached monochrome tint, retaining semantic colored icons."""
    key = (icon.cacheKey(), size.width(), size.height(), ink)
    if key not in _ICONS:
        pixmap = icon.pixmap(size)
        if pixmap.isNull() or not _neutral_icon(pixmap):
            return icon
        tinted = QPixmap(pixmap)
        painter = QPainter(tinted)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), QColor(ink))
        painter.end()
        _ICONS[key] = QIcon(tinted)
        if len(_ICONS) > 128:
            _ICONS.popitem(last=False)
    _ICONS.move_to_end(key)
    return _ICONS[key]


def chrome_icons(root):
    from .qascade_theme import EDITOR_IVORY
    for button in root.findChildren(QAbstractButton):
        if is_deep(button) or button.icon().isNull():
            continue
        if getattr(button, "_editor_icon_key", None) == button.icon().cacheKey():
            continue
        button.setIcon(chrome_icon(button.icon(), button.iconSize(), EDITOR_IVORY["ink"]))
        button._editor_icon_key = button.icon().cacheKey()


def _light_strip(dpr, kind):
    """Cache a one-pixel-wide optical film, not a window-sized screenshot."""
    key = (round(float(dpr), 3), kind)
    if key not in _STRIPS:
        scale = max(1.0, float(dpr))
        pixmap = QPixmap(max(1, round(scale)), round(64 * scale))
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        gradient = QLinearGradient(0, 0, 0, 64)
        strength = {"root": 24, "panel": 105, "raised": 170, "input": 0}[kind]
        gradient.setColorAt(0, QColor(255, 255, 250, strength))
        gradient.setColorAt(0.12, QColor(255, 252, 242, round(strength * 0.18)))
        gradient.setColorAt(1, QColor(255, 252, 242, 0))
        painter.fillRect(QRectF(0, 0, 1, 64), gradient)
        painter.end()
        _STRIPS[key] = pixmap
        if len(_STRIPS) > 8:
            _STRIPS.popitem(last=False)
    _STRIPS.move_to_end(key)
    return _STRIPS[key]


class MaterialLayer(QWidget):
    """An inert background child; original controls remain above it."""

    def __init__(self, owner, kind):
        super().__init__(owner)
        self._edge = QPen(QColor(255, 255, 250, 135), 1)
        self._shade = QPen(QColor(45, 43, 38, 42), 1)
        self._shadows = tuple(QPen(QColor(45, 43, 38, alpha), 1) for alpha in (24, 15, 8, 4))
        self._backdrop_key = None
        self.set_kind(kind)
        self.setObjectName("editorMaterialFilm")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        owner.installEventFilter(self)
        self.setGeometry(owner.rect())
        self.lower()
        self.show()

    def set_kind(self, kind):
        from .qascade_theme import EDITOR_IVORY
        self.kind = kind
        role = {"root": "root", "panel": "panel", "raised": "surface", "input": "input"}[kind]
        self._fill = QColor(EDITOR_IVORY[role])
        self._fill.setAlpha({"root": 255, "panel": 176, "raised": 205, "input": 255}[kind])
        self._strip = _light_strip(self.parentWidget().devicePixelRatioF(), kind)
        self.refresh_backdrop()
        self.update()

    def refresh_backdrop(self):
        from .editor_material_refresh import refresh_materials
        refresh_materials([self])

    def eventFilter(self, owner, event):
        if event.type() == QEvent.Type.Move and owner.isWindow():
            return False
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Move):
            self.setGeometry(owner.rect())
            self.lower()
            from .editor_material_refresh import request_material_refresh
            if event.type() == QEvent.Type.Show:
                self.refresh_backdrop()
            else:
                request_material_refresh(self)
        if event.type() in (QEvent.Type.DevicePixelRatioChange,
                            QEvent.Type.ApplicationPaletteChange):
            self._strip = _light_strip(owner.devicePixelRatioF(), self.kind)
            from .editor_material_refresh import request_material_refresh
            request_material_refresh(self)
            self.update()
        return False

    def paintEvent(self, event):
        painter = QPainter(self)
        backdrop = (background_pixmap(self.parentWidget()) if self.kind == "root"
                    else backdrop_pixmap(self._backdrop_key))
        if backdrop is not None:
            painter.drawPixmap(QRectF(self.rect()), backdrop, QRectF(backdrop.rect()))
        if self.kind != "root" or backdrop is None:
            painter.fillRect(event.rect(), self._fill)
        painter.drawPixmap(QRectF(0, 0, self.width(), 64), self._strip,
                           QRectF(0, 0, self._strip.width(), self._strip.height()))
        if self.kind == "root":
            painter.end()
            return
        painter.setPen(self._edge)
        painter.drawLine(1, 0, self.width() - 2, 0)
        painter.drawLine(0, 1, 0, self.height() - 5)
        painter.drawLine(self.width() - 1, 1, self.width() - 1, self.height() - 5)
        painter.setPen(self._shade)
        painter.drawLine(1, self.height() - 5, self.width() - 2, self.height() - 5)
        for offset, pen in enumerate(self._shadows):
            painter.setPen(pen)
            painter.drawLine(2, self.height() - 4 + offset, self.width() - 3,
                             self.height() - 4 + offset)
        painter.end()


def apply_material(widget, *, kind="panel"):
    """Apply to explicit chrome owners; preserve existing child identities/layouts."""
    if not isinstance(widget, QWidget) or is_deep(widget):
        return
    if owns_content_paint(widget):
        protect_content_paint(widget)
        return
    kind = "root" if kind == "shell" else kind
    widget.setPalette(editor_palette())
    name = widget.objectName() or f"editorMaterial{id(widget)}"
    widget.setObjectName(name)
    widget.setProperty("editorMaterial", kind)
    widget.setAutoFillBackground(False)
    scope = f"\nQWidget#{name}{{background:transparent;}}"
    if not widget.styleSheet().endswith(scope):
        widget.setStyleSheet(widget.styleSheet() + scope)
    layer = getattr(widget, "_editor_material_layer", None)
    if layer is None:
        widget._editor_material_layer = MaterialLayer(widget, kind)
    elif layer.kind != kind:
        layer.set_kind(kind)


def _native_backdrop(window):
    """Record API acceptance; visible native acrylic still requires real image review."""
    result = {"status": "fallback", "visual_verified": False,
              "fallback": "cached_warm_optical_film"}
    if sys.platform != "win32" or QApplication.platformName() != "windows":
        result["reason"] = "non_windows_platform"
        return result
    if sys.getwindowsversion().build < 22621:
        result["reason"] = "windows_build_below_22621"
        return result
    try:
        result.update(_request_native_frame(window))
    except (OSError, AttributeError) as error:
        result["reason"] = str(error)
    return result


def _request_native_frame(window):
    """Preserve native window flags and record both documented DWM HRESULTs."""
    dwm = ctypes.WinDLL("dwmapi")
    call = dwm.DwmSetWindowAttribute
    call.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
    call.restype = ctypes.c_long
    effect = ctypes.c_int(3 if isinstance(window, QDialog) else 2)
    handle = int(window.winId())
    code = int(call(handle, 38, ctypes.byref(effect), ctypes.sizeof(effect)))
    margins = (ctypes.c_int * 4)(-1, -1, -1, -1)
    extend = dwm.DwmExtendFrameIntoClientArea
    extend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    extend.restype = ctypes.c_long
    extended = int(extend(handle, ctypes.byref(margins))) if code == 0 else code
    return {"hresult": code, "frame_hresult": extended,
            "effect": "acrylic" if effect.value == 3 else "mica",
            "status": "accepted_unverified" if code == 0 and extended == 0 else "fallback"}


class WindowMaterials(QObject):
    """Apply once on real window show; no timer or recursive paint work."""

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind not in (QEvent.Type.Show, QEvent.Type.WinIdChange,
                        QEvent.Type.Resize, QEvent.Type.Move,
                        QEvent.Type.DevicePixelRatioChange):
            return False
        if not isinstance(watched, QWidget):
            return False
        if kind == QEvent.Type.Show:
            from .editor_chrome import prepare_chrome_widget
            prepare_chrome_widget(watched)
        elif kind in (QEvent.Type.Resize, QEvent.Type.Move,
                       QEvent.Type.DevicePixelRatioChange):
            from .editor_chrome import PAINTED_BACKGROUNDS
            if type(watched).__name__ in PAINTED_BACKGROUNDS:
                prepare_painted_background(watched)
        if watched.isWindow():
            self._apply_native(watched)
        return False

    def _apply_native(self, window):
        if not window.isWindow() or not window.isVisible():
            return
        if getattr(window, "_editor_native_busy", False):
            return
        if window.windowType() in (Qt.WindowType.Popup, Qt.WindowType.ToolTip):
            return
        window._editor_native_busy = True
        try:
            handle = int(window.winId())
            if getattr(window, "_editor_native_handle", None) != handle:
                window.setProperty("editorNativeBackdrop", _native_backdrop(window))
                window._editor_native_handle = handle
        finally:
            window._editor_native_busy = False


def apply_editor_palette(app):
    """Main startup seam: warm fallback palette plus owned window material adapter."""
    app.setPalette(editor_palette())
    if getattr(app, "_editor_window_materials", None) is None:
        app._editor_window_materials = WindowMaterials(app)
        app.installEventFilter(app._editor_window_materials)
