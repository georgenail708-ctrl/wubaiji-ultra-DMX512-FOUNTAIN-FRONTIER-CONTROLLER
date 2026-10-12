"""Whole physical frame is the calculation domain; selection is an apply mask."""
from __future__ import annotations


def whole_frame_page(panel, page=None):
    """Resolve a partial duplicate sheet to its existing complete frame.

    Use actual page members, never saved base order or a current selection.
    A larger frame must contain every member of this sheet. Unrelated sheets
    are not joined. Equal full copies retain their own authored layout.
    """
    page = panel.current_page() if page is None else page
    members = set(panel._page_device_ids(page))
    if not members:
        return page
    candidates = [(p, set(panel._page_device_ids(p))) for p in panel._pages]
    supersets = [(p, ids) for p, ids in candidates if members < ids]
    maximal = [(p, ids) for p, ids in supersets
               if not any(ids < other for _, other in supersets)]
    domains = {frozenset(ids) for _, ids in maximal}
    # Incomparable frames have no proven parent; do not guess by page size.
    return maximal[0][0] if len(domains) == 1 else page


def restored_contexts(panel, references):
    """Resolve saved owners once per restore, without a per-fixture page scan."""
    rows = [(p, set(panel._page_device_ids(p))) for p in panel._pages]
    by_id = {str(p.get('id')): (p, members) for p, members in rows}
    owners = {}
    for p, members in rows:
        for fid in members:
            owners.setdefault(fid, (p, members))
    contexts, result = {}, {}
    for fid, cfg in references.items():
        owner = by_id.get(str(cfg.get('page_id')))
        if owner is None or str(fid) not in owner[1]:
            owner = owners.get(str(fid))
        if owner is None:
            result[fid] = []
            continue
        page, members = owner
        key = id(page)
        if key not in contexts:
            # Reuse the same whole-frame resolver and canonical target order
            # as Apply; no active-tab state participates in restored angles.
            contexts[key] = panel._page_xy_sync_target_ids(page)
        result[fid] = contexts[key]
    return result


def masked_targets(whole_ids, selected_ids):
    """Choose package baselines before the mask; a child cannot elect a rival."""
    selected = {str(fid) for fid in selected_ids}
    out = []
    for fid in whole_ids:
        if fid in selected:
            out.append(fid)
    return out
