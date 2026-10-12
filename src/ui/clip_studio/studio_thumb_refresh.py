"""Refresh only the committed page through Studio's existing overview renderer.

R25 evidence and reuse: r189-studio-audio-visual.md. No second graph, thread,
timer or renderer; other page pixmaps and the native strip selection stay intact.
"""
from .studio_minimap import THUMB_H, THUMB_W, render_overview_pixmap


def refresh_committed_thumbnail(panel, sheet_id):
    strip = getattr(panel, "_sheet_thumbs", None)
    sheet = panel._sheet_by_id(str(sheet_id or panel._state.get("active_sheet", "")))
    if strip is None or sheet is None:
        return
    sid = str(sheet["id"])
    for index, item in enumerate(strip._items):
        if item[0] != sid:
            continue
        pixmap = render_overview_pixmap(sheet, THUMB_W, THUMB_H)
        strip._items[index] = (sid, item[1], pixmap, item[3])
        strip.update(strip.item_rect(index).toRect())
        return
