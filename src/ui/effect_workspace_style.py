"""Apply the shared ivory editor surface to original effect controls.

No new value model, stylesheet-per-frame pass, global theme mutation or blur.
Original per-widget geometry, state selectors and semantic indicators survive.
References: Qt palette/stylesheets, Tabler MIT, R185 Effects design adoption.
"""
from __future__ import annotations

import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QWidget

from . import qascade_theme as theme
from .main_icon_library import load_tabler_icon
from .number_slider_style import numeric_style
from .editor_materials import apply_material, chrome_icons, editor_palette, is_deep

_DECLARATION = re.compile(r"(?P<prop>[-\w]+)\s*:\s*(?P<value>[^;{}]+)")
_COLOR = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![0-9a-fA-F])")
_WHITE_ALPHA = re.compile(r"rgba\(\s*255\s*,\s*255\s*,\s*255\s*,\s*(\d+)\s*\)")
_COLORS = theme.EDITOR_IVORY
_WARM_SURFACES = {item.lower() for item in _COLORS.values()}
_TEXT = {
    theme.TEXT_PRIMARY.lower(): _COLORS["ink"],
    theme.TEXT_SELECTED_FG.lower(): _COLORS["ink"],
    theme.TEXT_SECONDARY.lower(): _COLORS["muted"],
    theme.TEXT_TERTIARY.lower(): _COLORS["muted"],
    theme.TEXT_DISABLED.lower(): _COLORS["disabled"],
    theme.FOCUS_BLUE.lower(): _COLORS["accent"],
    theme.BRIGHT_ERROR.lower(): _COLORS["danger"],
    theme.BRIGHT_WARN.lower(): _COLORS["accent"],
    theme.BRIGHT_SUCCESS.lower(): theme.STATUS_SUCCESS_INK,
    theme.BRIGHT_INFO.lower(): theme.INFO_BLUE,
    theme.ACTIVE_ORANGE.lower(): _COLORS["accent"],
}
_TEXT.update({old: _COLORS[role] for old, role in theme.LEGACY_EFFECT_TEXT_ROLES.items()})
_SELECT = {color.lower() for color in (theme.SELECT_BLUE, theme.SELECT_BLUE_HOVER,
                                      theme.SELECT_BLUE_PRESSED, theme.ACTIVE_ORANGE)}
_SELECT |= theme.LEGACY_EFFECT_SELECTED_COLORS
_INPUT = {color.lower() for color in (theme.BG_INPUT, theme.BG_INPUT_EDIT,
                                     theme.BG_ROOT, theme.BG_DEEP)}
_NEUTRAL = _INPUT | {color.lower() for color in (theme.BG_EDITOR, theme.BG_PANEL,
                  theme.BG_PANEL_SUB, theme.BG_BOX, theme.BG_BUTTON,
                  theme.BG_BUTTON_HOVER, theme.BORDER_NORMAL)}
_NEUTRAL |= theme.LEGACY_EFFECT_NEUTRAL_COLORS
_INPUT |= theme.LEGACY_EFFECT_INPUT_COLORS
_BORDER = dict.fromkeys(_NEUTRAL, _COLORS["border"])
_BORDER.update(dict.fromkeys(_SELECT, _COLORS["focus"]))


def ivory_palette():
    return editor_palette()


def recolor_qss(stylesheet):
    """Map declarations by role, preserving sizes, selectors and state rules."""
    return _DECLARATION.sub(_recolor_declaration, str(stylesheet or ""))


def _recolor_declaration(match):
    prop, value = match["prop"].lower(), match["value"]
    if prop not in ("color", "background", "background-color", "border-color",
                    "selection-color", "selection-background-color") and not prop.startswith("border"):
        return match.group()
    selector = match.string[:match.start()].rsplit("{", 1)[0].rsplit("}", 1)[-1]
    if "QScrollBar::handle" in selector and prop.startswith("background"):
        return f'{match["prop"]}:{_COLORS["border"]}'
    updated = _COLOR.sub(lambda color: _surface_color(prop, color.group()), value)
    updated = _recolor_white_alpha(prop, updated)
    return f'{match["prop"]}:{updated}'


def _recolor_white_alpha(prop, value):
    if prop in ("color", "selection-color"):
        return _WHITE_ALPHA.sub(lambda item: _COLORS["ink"] if int(item[1]) >= 180
                                else _COLORS["muted"], value)
    if prop.startswith("border"):
        return _WHITE_ALPHA.sub(_COLORS["border"], value)
    return value


def _surface_color(prop, color):
    value = _normalized_color(color)
    if value in _WARM_SURFACES:
        return color
    if prop in ("color", "selection-color"):
        return _TEXT.get(value, color)
    if prop.startswith("border"):
        return _BORDER.get(value, color)
    if value in _SELECT:
        return _COLORS["selected"]
    if value in (theme.BG_EDITOR.lower(), theme.BG_BUTTON.lower()):
        return _COLORS["surface"]
    if value in _INPUT:
        return _COLORS["input"]
    if value in _NEUTRAL:
        return _COLORS["panel"]
    return color


def _normalized_color(color):
    value = color.lower()
    return "#" + "".join(part * 2 for part in value[1:]) if len(value) == 4 else value


def decorate_tree(root):
    """Only original/new field trees, during assembly or preset changes."""
    for widget in (root, *root.findChildren(QWidget)):
        _decorate_widget(widget)
    chrome_icons(root)


def _decorate_widget(widget):
    if is_deep(widget):
        return
    _decorate_stylesheet(widget)
    if not getattr(widget, "_ivory_palette_set", False):
        widget.setPalette(ivory_palette())
        widget._ivory_palette_set = True
    _decorate_numeric_style(widget)


def _decorate_stylesheet(widget):
    source = widget.styleSheet()
    prior = getattr(widget, "_ivory_stylesheet", None)
    if prior is None or source != prior:
        styled = _scope_plain_stylesheet(widget, recolor_qss(source))
        if type(widget) is QFrame and styled.lstrip().startswith("QFrame{"):
            name = widget.objectName() or f"fxSurface{id(widget)}"
            widget.setObjectName(name)
            styled = styled.replace("QFrame{", f"QFrame#{name}{{")
        if styled != source:
            widget.setStyleSheet(styled)
        widget._ivory_stylesheet = styled


def _scope_plain_stylesheet(widget, styled):
    """Bare owner declarations cannot wash backgrounds over its descendant tree."""
    if not styled or "{" in styled:
        return styled
    name = widget.objectName() or f"editorScope{id(widget)}"
    widget.setObjectName(name)
    return f"QWidget#{name}{{{styled}}}"


def _decorate_numeric_style(widget):
    if hasattr(widget, "float_value") and hasattr(widget, "_scale"):
        widget._numeric_style = numeric_style(ivory=True)
    if hasattr(widget, "_expanded") and hasattr(widget, "_title"):
        widget._numeric_style = numeric_style(ivory=True)


def decorate_shell(panel):
    page = panel._subpage_stack.widget(0)
    page.setObjectName("fxIvoryRoot")
    page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    decorate_tree(page)
    page.setStyleSheet(
        f'QWidget#fxIvoryRoot{{background:{_COLORS["root"]};}}'
        f'QLabel{{color:{_COLORS["ink"]};background:transparent;}}'
        f'QGroupBox,QGroupBox::title{{color:{_COLORS["muted"]};}}'
        f'QToolButton{{color:{_COLORS["ink"]};background:{_COLORS["surface"]};}}'
        f'QToolButton:checked,QToolButton:hover{{background:{_COLORS["selected"]};}}')
    _decorate_navigation(panel)
    panel._main_split.setObjectName("fxMainSplit")
    panel._main_split.setStyleSheet(
        f'QSplitter#fxMainSplit::handle{{background:{_COLORS["root"]};border:none;}}')
    for index in range(1, panel._main_split.count()):
        panel._main_split.handle(index).setStyleSheet(
            f'background:{_COLORS["root"]};border:0px;padding:0px;margin:0px;')
    _decorate_scrollbars(page)
    panel._preset_list.setStyleSheet(
        f'QListView{{background:{_COLORS["panel"]};border:none;outline:none;}}'
        'QListView::item{background:transparent;border:none;}'
        f'QScrollBar:vertical{{background:transparent;width:12px;}}'
        f'QScrollBar::handle:vertical{{background:{_COLORS["border"]};'
        'min-height:32px;border-radius:6px;}'
        'QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}')
    panel._search.setClearButtonEnabled(True)
    panel._search.setMaximumWidth(260)
    panel._search.setAccessibleName("搜索效果名称、ID、标签")
    panel._search.setPlaceholderText("搜索名称 / ID / 标签…")
    panel._preset_name_lbl.setFont(theme.cached_font(
        20, families=theme.DISPLAY_FONT_FAMILIES))
    panel._preset_name_lbl.setStyleSheet(
        f'color:{_COLORS["ink"]};background:transparent;')
    for label in (panel._preset_name_lbl, panel._preset_desc_lbl, panel._preset_cat_lbl):
        label.setTextFormat(Qt.TextFormat.PlainText)
    _decorate_transport(panel)
    apply_material(page, kind="shell")


def _decorate_scrollbars(root):
    from PyQt6.QtWidgets import QScrollBar

    stylesheet = (
        f'QScrollBar:vertical{{background:{_COLORS["panel"]};border:none;width:10px;}}'
        f'QScrollBar::handle:vertical{{background:{_COLORS["border"]};border:none;'
        'min-height:32px;border-radius:5px;}'
        'QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;border:none;}'
        'QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}')
    for bar in root.findChildren(QScrollBar):
        if bar.orientation() == Qt.Orientation.Vertical:
            bar.setStyleSheet(stylesheet)


def _decorate_navigation(panel):
    from PyQt6.QtWidgets import QLabel, QScrollArea

    for widget in panel._effect_navigation.findChildren(QWidget):
        if isinstance(widget, QScrollArea) or widget is panel._cat_bar._rows:
            widget.setStyleSheet(f'background:{_COLORS["panel"]};border:none;')
        elif isinstance(widget, QLabel) and widget.parentWidget() is panel._effect_quick_panel:
            widget.setStyleSheet(
                f'color:{_COLORS["muted"]};background:transparent;'
                'font-size:12px;padding:5px 8px;border:none;')
    panel._cat_bar.setStyleSheet(
        f'QWidget#fxCategoryBar{{background:{_COLORS["panel"]};border:none;}}')


def _decorate_transport(panel):
    from PyQt6.QtWidgets import QPushButton

    icons = {"开始播放": "player-play", "全部停止": "player-stop",
             "→ 时间线": "arrow-right", "另存为我的效果": "device-floppy"}
    for button in panel.findChildren(QPushButton):
        glyph = icons.get(button.text())
        if glyph:
            ink = _COLORS["danger"] if button.text() == "全部停止" else _COLORS["ink"]
            button.setIcon(load_tabler_icon(glyph, 16, ink))


def decorate_transport_state(panel):
    _decorate_widget(panel._play_btn)


def decorate_parameters(panel):
    signature = (tuple(id(widget) for widget in panel._param_widgets.values()),
                 tuple(id(form) for form in panel._channel_forms))
    if signature == getattr(panel, "_ivory_parameter_signature", None):
        return
    panel._ivory_parameter_signature = signature
    for root in (panel._param_container, panel._param_channel_container):
        decorate_tree(root)
    panel._preset_name_lbl.setFont(theme.cached_font(
        20, families=theme.DISPLAY_FONT_FAMILIES))
    panel._preset_name_lbl.setStyleSheet(
        f'color:{_COLORS["ink"]};background:transparent;')
    for label in panel._w2_macro_labels.values():
        label.setWordWrap(True)
        label.setMinimumWidth(52)
