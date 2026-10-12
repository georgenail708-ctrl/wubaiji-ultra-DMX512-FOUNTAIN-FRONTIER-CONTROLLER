"""Keep Matrix engines and compiled output bindings across editor selections.

Runs share the panel's existing timer, original engines and sparse writer. The
short synchronous binding context never processes UI events or rebuilds an
engine: it only presents the owning run to those existing output methods.
"""
from contextlib import contextmanager
from copy import deepcopy
from functools import wraps

from PyQt6.QtCore import QSignalBlocker

_FIELDS = (
    '_engine', '_pixel_engine', '_fixtures', '_current_group_id', '_current_group_name',
    '_mode', '_ch_type', '_pixel_running', '_pixel_paused', '_pixel_last_t',
    '_slice_key', '_manual_slice_latched', '_manual_slice_states', '_wing_direction', '_wing_mirror', '_wing_reverse',
    '_skip_base_order', '_bus_user_touched', '_recalled_play', '_matrix_editor_passive',
    '_matrix_target_base_order',
    '_channel_addr_cache', '_fixture_addr_cache', '_matrix_fixture_order',
    '_matrix_fixture_reverse_order', '_matrix_vector_maps', '_matrix_raw_vector_maps',
    '_matrix_channel_fixture_info', '_matrix_channel_roles', '_matrix_legacy_targets',
    '_matrix_profile_definitions', '_matrix_profile_rules', '_matrix_output_cache',
    '_matrix_base_frame_cache', '_matrix_pattern_cache', '_matrix_pattern_pending',
    '_matrix_owned_addresses', '_matrix_owned_scope', '_matrix_owned_complete',
    '_matrix_allowed_addresses', '_last_matrix_pattern_emit', '_last_matrix_full_output_emit',
    '_force_matrix_full_output', '_matrix_ltp_takeover_pending', '_last_matrix_output_layer',
    '_output_shaper', '_output_shaper_ts',
    '_last_load_group_sig', '_pixel_transition_busy',
)
_MISSING = object()


def _capture(panel):
    return {name: panel.__dict__.get(name, _MISSING) for name in _FIELDS}


def _install(panel, fields):
    for name, value in fields.items():
        if value is _MISSING:
            panel.__dict__.pop(name, None)
        else:
            panel.__dict__[name] = value


@contextmanager
def bound(panel, run):
    current = _capture(panel)
    hidden = panel._ui_hidden
    previous = getattr(panel, '_matrix_bound_run', False)
    panel._matrix_bound_run = True
    panel._ui_hidden = True
    _install(panel, run)
    try:
        yield
    finally:
        run.update(_capture(panel))
        _install(panel, current)
        panel._ui_hidden = hidden
        panel._matrix_bound_run = previous


def _key(fixtures):
    return frozenset(str(f['id']) for f in fixtures)


def _runs(panel):
    return panel.__dict__.setdefault('_matrix_retained_runs', {})


def has_runs(panel):
    return bool(_runs(panel))


def selection_change(method):
    @wraps(method)
    def load(panel, *args, **kwargs):
        previous = getattr(panel, '_matrix_selecting', False)
        panel._matrix_selecting = True
        try:
            return method(panel, *args, **kwargs)
        finally:
            panel._matrix_selecting = previous
    return load


def before_selection(panel, ids):
    if getattr(panel, '_matrix_refreshing', False):
        return
    if frozenset(map(str, ids)) == _key(panel._fixtures):
        return
    if not (panel._engine.is_playing or panel._pixel_running
            or getattr(panel, '_matrix_owned_addresses', {})):
        _save_idle_recipe(panel)
        return
    # Keep the original live objects; only the new editing copy is deserialized.
    from .matrix_slide_engine import MatrixSlideEngine
    from .matrix_pixel_engine import MatrixPixelEngine
    from .matrix_slideshow_panel import _collect_pixel_params
    recipe = deepcopy(panel._engine.to_dict())
    pixel = deepcopy(_collect_pixel_params(panel._pixel_engine, panel))
    _runs(panel)[_key(panel._fixtures)] = _capture(panel)
    panel._engine = MatrixSlideEngine()
    panel._engine.from_dict(recipe)
    panel._engine.on_output = panel._on_engine_output
    panel._pixel_engine = MatrixPixelEngine()
    _copy_pixel_settings(panel._pixel_engine, pixel)
    panel._pixel_param_panel.set_engine(panel._pixel_engine)
    panel._pixel_running = panel._pixel_paused = False
    panel._bus_user_touched = panel._recalled_play = False
    panel._matrix_owned_addresses = {}
    panel._matrix_owned_scope = None
    panel._matrix_vector_maps = {}
    panel._matrix_editor_passive = True
    with QSignalBlocker(panel._play_btn):
        panel._play_btn.setChecked(False)
    panel._set_play_button_text(False)


def _copy_pixel_settings(engine, settings):
    for key, value in settings.items():
        if key != 'mode' and hasattr(engine, key):
            setattr(engine, key, value)
    engine.set_mode(settings.get('mode', engine.mode))


def _save_idle_recipe(panel):
    if not getattr(panel, '_matrix_saved_tracking', False) or not panel._fixtures:
        return
    state = panel.save_state()
    state.pop('running_outputs', None)
    panel.__dict__.setdefault('_matrix_saved_outputs', {})[_key(panel._fixtures)] = deepcopy(state)


def accepts_ppt_output(panel):
    return (panel._mode == 'ppt' and not panel._slice_mode_enabled()
            and not getattr(panel, '_matrix_selecting', False))


def preview_ppt(panel, weights):
    weights = panel._shape_output_weights(weights)
    if not getattr(panel, '_matrix_bound_run', False):
        panel._canvas.set_pixel_weights(weights, 0.5)
    return weights


def activate(panel):
    panel._matrix_editor_passive = False
    panel._bus_user_touched = True


def passive(panel):
    return (getattr(panel, '_matrix_editor_passive', False)
            and not panel._bus_user_touched)


def apply_recall_recipe(panel, node, state, for_edit):
    from .matrix_slideshow_panel import (_restore_panel_mode, _quiesce_other_transport,
                                         restore_runtime_channel, _apply_pixel_runtime_params)
    from .matrix_channel_nodes import recall_edit_state
    if not state:
        return
    if for_edit:
        panel.load_state(recall_edit_state(node, state))
    else:
        _restore_panel_mode(panel, state)
        _quiesce_other_transport(panel, panel._mode)
        restore_runtime_channel(panel, state, _apply_pixel_runtime_params)
    panel._bus_user_touched = False


def after_selection(panel):
    if getattr(panel, '_matrix_refreshing', False):
        return
    run = _runs(panel).pop(_key(panel._fixtures), None)
    if run is None:
        saved = panel.__dict__.get('_matrix_saved_outputs', {}).pop(_key(panel._fixtures), None)
        if saved is not None:
            panel.load_state(saved)
        return
    panel._pixel_engine.stop(full_release=True)
    _install(panel, run)
    panel._pixel_param_panel.set_engine(panel._pixel_engine)
    with QSignalBlocker(panel._play_btn):
        panel._play_btn.setChecked(panel._engine.is_playing or panel._pixel_running)
    panel._set_play_button_text(panel._play_btn.isChecked())


def show_selected_frame(panel):
    panel._update_thumb_selection()
    index = min(panel._engine.current_idx, len(panel._engine.slides) - 1)
    if index >= 0:
        slide = panel._engine.slides[index]
        panel._canvas.set_states(slide.states)
        panel._slide_param_panel.load_slide(slide)


def tick(panel):
    if getattr(panel, '_matrix_bound_run', False):
        return
    for run in list(_runs(panel).values()):
        with bound(panel, run):
            panel._tick()


def bus_nodes(panel):
    if getattr(panel, '_matrix_bound_run', False):
        return []
    nodes = []
    for run in list(_runs(panel).values()):
        with bound(panel, run):
            nodes.extend(panel._bus_nodes())
    return nodes


def clear_current(panel, layers=('matrix', 'matrix_audio')):
    """Release only addresses written by this run; keep sibling contributions."""
    owned = getattr(panel, '_matrix_owned_addresses', {})
    network = panel._nm_ref
    if network is None:
        return
    for layer in layers:
        for universe, addresses in owned.pop(layer, {}).items():
            remaining = set(addresses)
            for other in _runs(panel).values():
                other_owned = other.get('_matrix_owned_addresses', {})
                if other_owned is not owned:
                    remaining.difference_update(other_owned.get(layer, {}).get(universe, ()))
            network.clear_layer_channels(layer, universe, sorted(remaining))
    panel._matrix_owned_complete = set()
    for other in _runs(panel).values():
        other['_force_matrix_full_output'] = True
        other['_engine']._hold_output_sig = None


def stop_slide(panel):
    engine, network = panel._engine, panel._engine._nm
    engine._nm = None  # The panel owns addresses; engine fallback clears a whole layer.
    try:
        engine.stop()
    finally:
        engine._nm = network
    clear_current(panel)


def _stop_bound(panel):
    panel._engine.pause()
    panel._pixel_engine.stop(full_release=True)
    panel._pixel_running = panel._pixel_paused = False
    panel._manual_slice_latched = False
    panel._bus_user_touched = panel._recalled_play = False
    panel._matrix_editor_passive = True
    clear_current(panel)


def stop_all(panel):
    panel._pixel_effect_apply_timer.stop()
    panel._pixel_resource_release_timer.stop()
    panel._pending_pixel_mode_id = None
    for run in list(_runs(panel).values()):
        with bound(panel, run):
            _stop_bound(panel)
    _runs(panel).clear()
    panel.__dict__.pop('_matrix_saved_outputs', None)
    panel._matrix_saved_tracking = False
    _stop_bound(panel)
    with QSignalBlocker(panel._play_btn):
        panel._play_btn.setChecked(False)
    panel._set_play_button_text(False)
    panel.report_to_bus(force=True)
    panel._bus_last_report = 0.0
    panel.settle_hidden_timer()


def release_node(panel, node):
    from .matrix_channel_nodes import authoring_ids
    ids = authoring_ids(node) if 'fixture_ids' in node else None
    if ids is None:
        stop_all(panel)
        return True
    key = frozenset(map(str, ids))
    run = _runs(panel).pop(key, None)
    if run is not None:
        with bound(panel, run):
            _stop_bound(panel)
    elif key == _key(panel._fixtures):
        _stop_bound(panel)
        with QSignalBlocker(panel._play_btn):
            panel._play_btn.setChecked(False)
        panel._set_play_button_text(False)
    else:
        return False
    # Original sparse mirrors must resend overlapping surviving contributions.
    for other in _runs(panel).values():
        other['_force_matrix_full_output'] = True
        other['_engine']._hold_output_sig = None
    panel._force_matrix_full_output = True
    panel._engine._hold_output_sig = None
    panel._tick()
    panel.report_to_bus(force=True)
    panel._bus_last_report = 0.0
    return True


def save_others(panel, state):
    if not getattr(panel, '_matrix_bound_run', False):
        records = list(panel.__dict__.get('_matrix_saved_outputs', {}).values())
        for run in _runs(panel).values():
            with bound(panel, run):
                records.append(panel.save_state())
        if records:
            state['running_outputs'] = records
    return state


def state_for_node(panel, node, state):
    if not state:
        return state
    from .matrix_channel_nodes import authoring_ids
    ids = frozenset(authoring_ids(node))
    for candidate in [state, *state.get('running_outputs', [])]:
        if frozenset(map(str, candidate.get('fixture_ids', []))) == ids:
            return candidate
    return state


def restore_saved(panel, state):
    if 'running_outputs' in state:
        panel._matrix_saved_tracking = True
        panel._matrix_saved_outputs = {
            frozenset(map(str, item.get('fixture_ids', []))): deepcopy(item)
            for item in state['running_outputs'] if isinstance(item, dict)
        }


def running(panel):
    return bool(panel._engine.is_playing or panel._pixel_running)


def refresh_retained(panel):
    """A real patch change recompiles owners; an editor selection never does."""
    rows = {str(row['id']): row for row in panel._positions_from_pm(panel._pm)}
    for key, run in list(_runs(panel).items()):
        with bound(panel, run):
            panel._fixtures = [dict(old, **{name: rows[old['id']][name]
                              for name in ('universe', 'start_channel', 'def_id', 'channels')
                              if name in rows[old['id']]})
                               for old in panel._fixtures if old['id'] in rows]
            panel._rebuild_channel_addr_cache()
            panel._engine.set_fixtures(panel._fixtures)
            panel._pixel_engine.set_fixtures(panel._fixtures)
            available = _key(panel._fixtures)
            for slide in panel._engine.slides:
                slide.states = {fid: value for fid, value in slide.states.items() if fid in available}
            panel._force_matrix_full_output = True
            panel._engine._hold_output_sig = None
        if not run['_fixtures']:
            _runs(panel).pop(key)
            with bound(panel, run):
                _stop_bound(panel)
        elif available != key:
            _runs(panel).pop(key)
            _runs(panel)[available] = run
