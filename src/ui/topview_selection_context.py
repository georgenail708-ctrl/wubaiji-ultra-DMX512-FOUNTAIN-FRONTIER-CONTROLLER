"""TopView 母序读取的公共合同；成员与母集合相位依据分别传递。"""
from src.core.fixture_selection import ordered_members


def visible_members(panel):
    """可见散台及逻辑包成员，沿用 TopView 的选择范围。"""
    visible = set()
    if hasattr(panel, "fixture_ids"):
        visible.update(str(fid) for fid in (panel.fixture_ids() or []))
    if hasattr(panel, "_selectable_member_ids"):
        visible.update(panel._selectable_member_ids())
    return visible


def live_base_if_established(panel, selected):
    """仅当前实时组可继承母序；不把母序裁成子集或用它扩张成员。"""
    base = ordered_members(getattr(panel, "_live_base_ids", []))
    live = ordered_members(getattr(panel, "_live_selection_ids", []))
    if not base or set(selected) != set(live):
        return None
    visible = visible_members(panel)
    return [fid for fid in base if not visible or fid in visible] or None


def reset_manual_context(panel, *, preserve=False):
    """明确手选建立新母序；拖动已有选择保留其合法派生关系。"""
    if not preserve:
        panel._reset_live_selection_context()
