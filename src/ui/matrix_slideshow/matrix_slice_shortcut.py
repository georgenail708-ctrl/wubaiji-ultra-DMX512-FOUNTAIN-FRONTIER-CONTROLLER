"""Retire the owned Matrix binding before Qt delivers deferred destruction.

Uses Qt's existing shortcut/deferred-delete lifecycle; see R189 R14 native
registry evidence in r189-matrix-slice-selection.md. Other owners are untouched.
"""


def retire_shortcut(shortcut):
    if shortcut is None:
        return
    try:
        shortcut.setEnabled(False)
        shortcut.deleteLater()
    except Exception:
        pass
