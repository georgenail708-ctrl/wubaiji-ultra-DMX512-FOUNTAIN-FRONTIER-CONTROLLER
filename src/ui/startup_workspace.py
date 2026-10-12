"""Restore workspace placement while child editors wait for explicit opening."""


def hide_startup_floating_editors(win):
    for attr in getattr(win, '_R54_ALL_DOCK_ATTRS', ()):
        dock = getattr(win, attr, None)
        if dock is not None and dock.isFloating():
            dock.hide()
