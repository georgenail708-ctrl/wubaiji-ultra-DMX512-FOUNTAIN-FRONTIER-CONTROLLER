"""Once fixed-source qualification, then current source identity at live use.

Owned worker permission is not permission to skip later foreign live methods.
Reuse original 24/44 guard and its installed-originals metadata unchanged.
No numeric callback executes here. Qualification/check elapsed cost is measured.
"""
import hashlib
import inspect
import sys
from types import FunctionType

_QUALIFIED = None
_EXTRA = {'_render_position': '2d7c4c87a65f82797c903e18c96996e5fa4de0dff532776e3882d317d5c78a2d', '_render_from_timeline_frame_cache': '0e4b4759a7a3c1f31754b53f6100946f70b63fc2f3a82d5ccb680e99ca819e9f', 'seek': 'b6b2b15f4d98d7af5a6da811cf3b4751b5d65d84fc52762af66c4b3557b6bce4', 'render_preview_at': 'ac5c0b48cd921322e6bf1220a9ba62f0ac3ee7f9997a2cb455d6e192c4990613', 'render_playback_at': '6b5837c35cf0d07263f8214461e1da26a945346a40c7eff1211ec5561768c8c6', '_fade_in_anchor_ready_context': '6fb033160af131b8c1fbcf7fb12307f9a101dcdf48ade7068d5960fd0afa2295', '_get_fade_out_target': '7f64f146d4f2d367d26f64f00dcdae06e1bb5c88b1403b815d0997a8020649e1', '_tick': 'f99111649991c8b5010d4c28db3bf53177635c5a2e0efe7195c06b17fe6726ed', '_gen_loop': 'c28b3a3c792fd6f1a31caca40e2bd1d172284dc5c3f12ffa54e27e595367dc7b', 'set_gen_clock_source': '5c035d1b959a63712fd1138a780848985235b44b516b19128480b703a07c4de4'}
_PROJECT_WRAPPER = '35c3419f41714bf12eda4102abe4f94f2430eb5db0a4ca4e57c4191b89f0be46'


def _class_rows(owner):
    return tuple((name, value) for name, value in vars(owner).items()
                 if name.startswith(("_clip_", "_track_", "_playback_")) or name in
                 ("_render_track_state", "_render_struct_for_tracks", "_target_universes_for_clip",
                  "_indexed_active_clip_items", *_EXTRA))


def _function_tree(function, seen):
    from qascade_ce.native_curve_scalar_guard import function_metadata
    if type(function) is not FunctionType or id(function) in seen:
        return []
    seen.add(id(function))
    rows = function_metadata(function)
    wrapped = getattr(function, "__wrapped__", None)
    rows.append((vars(function), "__wrapped__", wrapped))
    rows.extend(_function_tree(wrapped, seen))
    for cell in function.__closure__ or ():
        rows.append((_CellMetadata(cell), "cell_contents", cell.cell_contents))
        rows.extend(_function_tree(cell.cell_contents, seen))
    return rows


class _CellMetadata:
    __slots__ = ("cell",)

    def __init__(self, cell):
        self.cell = cell

    def get(self, name):
        return self.cell.cell_contents


def _class_current(owner, previous):
    current = _class_rows(owner)
    return (len(current) == len(previous)
            and all(name == old_name and function is old_function
                    for (name, function), (old_name, old_function) in zip(current, previous)))


def _extra_original(owner):
    from qascade_ce.native_curve_scalar_guard import _signature
    for name, wanted in _EXTRA.items():
        function = inspect.getattr_static(owner, name)
        if type(function) is not FunctionType:
            return False
        if name == "_render_position" and not _render_wrapper_original(function):
            return False
        function = getattr(function, "__wrapped__", function)
        if type(function) is not FunctionType or function.__globals__ is not vars(sys.modules[owner.__module__]):
            return False
        if hashlib.sha256(repr(_signature(function.__code__)).encode()).hexdigest() != wanted:
            return False
    return True


def _render_wrapper_original(function):
    from qascade_ce.native_curve_scalar_guard import _signature
    namespace = vars(sys.modules["src.core.project_frame_origin"])
    if type(function) is not FunctionType or function.__globals__ is not namespace:
        return False
    cells = dict(zip(function.__code__.co_freevars, (cell.cell_contents for cell in function.__closure__ or ())))
    return (hashlib.sha256(repr(_signature(function.__code__)).encode()).hexdigest() == _PROJECT_WRAPPER
            and cells.get("render") is getattr(function, "__wrapped__", None))


def _initial_scope(engine):
    from qascade_ce.prepared_prepass import _METHODS
    from qascade_ce.prepared_prepass_guard import default_methods
    from qascade_ce import render_path, native_playback_index_install as installed
    if not default_methods(engine, _METHODS) or not _extra_original(type(engine)):
        return None
    class_rows = _class_rows(type(engine))
    members = [value.__func__ if isinstance(value, staticmethod) else value for _, value in class_rows]
    members.extend((render_path.ce_indexed_active_clip_items, *installed._ORIGINALS.values()))
    metadata, seen = [], set()
    for function in members:
        metadata.extend(_function_tree(function, seen))
    dependencies = [(vars(render_path), "ce_indexed_active_clip_items", render_path.ce_indexed_active_clip_items)]
    dependencies.extend((installed._ORIGINALS, name, value) for name, value in installed._ORIGINALS.items())
    return type(engine), class_rows, metadata + dependencies, tuple(installed._ORIGINALS)


def _metadata_current(rows):
    return all(source.get(name) is captured for source, name, captured in rows)


def default_live_scope(engine):
    from .transition_anchor_causality import default_scope
    from qascade_ce import native_playback_index_install as installed
    global _QUALIFIED
    if _QUALIFIED is None:
        _QUALIFIED = _initial_scope(engine)
    if _QUALIFIED is None or type(engine) is not _QUALIFIED[0]:
        return False
    _, class_rows, metadata, original_keys = _QUALIFIED
    if any(name in vars(engine) for name, _ in class_rows):
        return False
    if not _class_current(type(engine), class_rows) or tuple(installed._ORIGINALS) != original_keys:
        return False
    return _metadata_current(metadata) and default_scope(engine)
