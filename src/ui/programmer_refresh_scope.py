"""Retain Programmer's actual authoring selection during a source refresh.

Reuse existing channel rows and address compilation. References: Qt v6.11.0
QWidget show/polish contract and original Programmer scope/version ownership.
No transport, persistent preference, output layer or selection expansion here.
"""
from __future__ import annotations


def _ids(fixtures):
    return [str(fixture.id) for fixture in fixtures]


def capture_scope(panel):
    selected = panel._current_fixtures_selected
    group = str(panel._current_group_id or '')
    if group == '__all__' and _ids(selected) == _ids(panel._current_fixtures):
        return None
    return dict(selected=selected, group=group, name=panel._current_group_name,
                base=list(panel._current_base_order_ids),
                live=list(panel._topview_live_fixture_ids),
                live_base=list(panel._topview_live_base_order_ids))


def restore_scope(panel, scope, fixtures, definitions):
    if scope is None:
        return False
    available = {str(fixture.id): fixture for fixture in fixtures}
    previous = scope['selected']
    selected = _selected_fixtures(previous, available)
    if selected is not panel._current_fixtures_selected:
        panel._current_fixtures_selected = selected
    _restore_metadata(panel, scope, available)
    label = scope['name'] or f'设备组（{len(selected)} 台）'
    panel._ch_list.load_fixtures(selected, definitions, label)
    panel._rebuild_addr_cache(selected, definitions)
    if _ids(previous) != _ids(selected):
        panel._emit_fixture_selection_to_topview(_ids(selected))
    panel._update_light_mode_capability()
    return True


def _selected_fixtures(previous, available):
    selected = [available[str(fixture.id)] for fixture in previous if str(fixture.id) in available]
    if len(selected) == len(previous) and all(a is b for a, b in zip(selected, previous)):
        return previous
    return selected


def _restore_metadata(panel, scope, available):
    panel._current_group_id = scope['group']
    panel._current_group_name = scope['name']
    base = [fid for fid in scope['base'] if fid in available]
    if base != panel._current_base_order_ids:
        panel._current_base_order_ids = base
    panel._topview_live_fixture_ids = [fid for fid in scope['live'] if fid in available]
    panel._topview_live_base_order_ids = [fid for fid in scope['live_base'] if fid in available]
