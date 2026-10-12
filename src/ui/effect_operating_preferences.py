"""Stable user defaults for new Effect channel sheets, never object state."""


def channel_key(value):
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if len(value) > 160 or any(ord(char) < 32 for char in value):
        return ""
    return value


def default_channel(panel, spec, allowed):
    from . import effect_user_store

    preferred = effect_user_store.channel_type_pref()
    if preferred:
        return preferred
    original = str(getattr(spec, "default", "") or "")
    choices = getattr(spec, "choices", None)
    if not original and choices:
        original = str(choices[0])
    return original or "dimmer"


def remember_user_channel(panel, form):
    """Only user activation calls this; programmatic restore remains silent."""
    from . import effect_user_store

    return effect_user_store.set_channel_type_pref(panel._channel_form_type(form))


def connect_channel_choice(panel, combo, form):
    combo.currentIndexChanged.connect(
        lambda _index: panel._on_channel_form_type_changed(form))
    combo.activated.connect(lambda _index: remember_user_channel(panel, form))


def form_channel(key, allowed, params):
    """Keep a new unavailable user default; preserve explicit-object restore."""
    from . import effect_user_store

    if allowed and key not in allowed:
        if params is None and key == effect_user_store.channel_type_pref():
            return key
        return allowed[0]
    return key


def select_channel(combo, key):
    index = combo.findData(key)
    if index < 0:
        combo.addItem(f"{key}（当前工程不可用）", key)
        index = combo.count() - 1
        combo.model().item(index).setEnabled(False)
        combo.setToolTip("惯用通道在当前工程不可用，播放已暂停；请选择可用通道。")
    combo.setCurrentIndex(index)


def prepare_play(panel):
    """An unavailable sheet needs an explicit user choice before output starts."""
    if not panel._selected_id:
        panel._status_lbl.setText("请先选择一个预设")
        return False
    if not panel._has_channel_forms():
        return True
    form = panel._active_channel_form()
    if form is None:
        return False
    from PyQt6.QtCore import Qt

    combo = form["combo"]
    current = combo.model().index(combo.currentIndex(), 0)
    available = bool(combo.model().flags(current) & Qt.ItemFlag.ItemIsEnabled)
    if not available:
        panel._status_lbl.setText("惯用通道在当前工程不可用，播放已暂停；请选择可用通道。")
    return available
