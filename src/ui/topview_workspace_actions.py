"""TopView editor menus delegate to the original, undo-aware controls.

References: window regions (UI semantics), Qt QMenu/QToolButton APIs.
Icons: existing Tabler outline assets, MIT, via main_icon_library.
"""
from __future__ import annotations

from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QAbstractButton, QMenu, QToolButton

from .main_icon_library import load_tabler_icon


def original_buttons(header) -> dict:
    return {button.text(): button for button in header.findChildren(QAbstractButton)}


def _qobject_alive(obj) -> bool:
    """Return whether a Qt wrapper still owns a live C++ object.

    TopView keeps the workspace menu bar while its original child controls can
    be rebuilt.  A Python closure may therefore outlive the QPushButton it
    captured; touching that wrapper raises ``RuntimeError`` instead of simply
    returning a disabled state.
    """
    return obj is not None and not sip.isdeleted(obj)


def _sync_action_enabled(action, button) -> None:
    if not _qobject_alive(action):
        return
    action.setEnabled(_qobject_alive(button) and button.isEnabled())


def _click_button(button) -> None:
    if _qobject_alive(button):
        button.click()


def _forward_action(menu: QMenu, button: QAbstractButton) -> None:
    action = menu.addAction(button.text())
    action.setToolTip(button.toolTip())
    # Do not connect a bound ``button.click`` directly: the menu can survive a
    # TopView child rebuild and later dispatch the action after the original
    # button's C++ object has already been deleted.
    action.triggered.connect(lambda _checked=False, b=button: _click_button(b))
    menu.aboutToShow.connect(lambda a=action, b=button: _sync_action_enabled(a, b))


def _menu_button(title: str, parent) -> tuple:
    button = QToolButton(parent)
    button.setText(title)
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    button.setMinimumHeight(28)
    menu = QMenu(button)
    button.setMenu(menu)
    return button, menu


def build_menus(panel, *, buttons: dict, parent) -> list:
    menus = []
    groups = (
        ("页面", ("新增页", "复制页", "重命名页", "删除页")),
        ("布局", ("删除图形", "移除选中设备", "导入布局表", "导出布局表")),
        ("设备组", ("刷新设备组", "按类型合并", "清除合并", "自动编组", "换一组", "保存编组")),
    )
    for title, texts in groups:
        button, menu = _menu_button(title, parent)
        for text in texts:
            _forward_action(menu, buttons[text])
        menus.append(button)
    button, menu = _menu_button("视图", parent)
    menu.addAction("适配本页", panel._canvas._fit_view_to_content)
    menu.addAction("重置视图", panel._canvas.reset_view)
    menus.append(button)
    return menus


def region_toggle(text: str, *, icon: str, region, parent) -> QToolButton:
    button = QToolButton(parent)
    button.setText(text)
    button.setToolTip(f"显示或收起{text}分栏；画布和工程数据保持原作用域")
    button.setIcon(load_tabler_icon(icon, 16))
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    button.setCheckable(True)
    button.setChecked(True)
    button.setMinimumHeight(28)
    button.toggled.connect(region.setVisible)
    return button
