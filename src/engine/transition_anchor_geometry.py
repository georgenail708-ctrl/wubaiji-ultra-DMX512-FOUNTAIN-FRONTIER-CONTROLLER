"""Source-owned token permits for canonical private spatial materialization.

The original complete mutable-input fingerprint is the only source proof.
Restore preserves every anchor/context. True live fallback may adopt only
source-owned private spatial metadata; no clip/project data is mutated.
"""
from __future__ import annotations

import hashlib
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from types import FunctionType

SOURCE = "_transition_anchor_geometry_source"
PERMIT = "_transition_anchor_geometry_permit"
PENDING = "_transition_anchor_geometry_live_pending"
FIELD = "source_geometry"
METHODS = ("freeze_clip_spatial_snapshot", "_topview_fixture_positions_for_ids",
           "_topview_page_fixture_ids", "_record_private_spatial_snapshot",
           "_timeline_render_runtime_signature_value")
EXPECTED = {'freeze_clip_spatial_snapshot': '9d96ef6e2b8b9d0c850670c6b36728412f0c082ab6ebfc75c20c6009da43c67d', '_topview_page_fixture_ids': '7e26718afa42576101a040e5095424eb7e6f740f7a6f03306ce4413bb52b39d9', '_topview_fixture_positions_for_ids': '2ebf8157e5a09caf0e11d1903b79ec10ea3f15c877ddbcb8f601e0eb4ab8f2e8', '_record_private_spatial_snapshot': '04c9c202016a490d6a3e865812d69980f4766665a57354507c0e64307b97471a', '_timeline_render_runtime_signature_value': '78f9dc641f120b961af9d1ae091bf11c7a3d3493bc88cceb8445e41a2825489e'}
DEFAULTS = {"freeze_clip_spatial_snapshot": (None,),
            "_topview_fixture_positions_for_ids": (None, None, True),
            "_topview_page_fixture_ids": None, "_record_private_spatial_snapshot": None,
            "_timeline_render_runtime_signature_value": None}
_QUALIFIED = None
_CURRENT_SOURCE = ContextVar("qascade_current_anchor_source", default=None)


def _function_metadata(function):
    return function, function.__code__, function.__defaults__, function.__kwdefaults__


def _method(owner, name):
    namespace = vars(sys.modules[owner.__module__]) if name == "_record_private_spatial_snapshot" else vars(owner)
    value = namespace.get(name)
    return value.__func__ if type(value) is staticmethod else value


def _initial_methods(owner):
    from qascade_ce.native_curve_scalar_guard import _signature
    from .transition_anchor_causality import _defaults_current
    namespace, qualified = vars(sys.modules[owner.__module__]), {}
    for name in METHODS:
        function = _method(owner, name)
        if type(function) is not FunctionType or function.__globals__ is not namespace:
            return None
        digest = hashlib.sha256(repr(_signature(function.__code__)).encode()).hexdigest()
        if digest != EXPECTED[name]:
            return None
        if not _defaults_current(function, DEFAULTS[name]) or function.__kwdefaults__ is not None:
            return None
        qualified[name] = _function_metadata(function)
    return qualified


def _current_methods(owner):
    for name, previous in _QUALIFIED.items():
        function = _method(owner, name)
        if function is not previous[0] or function.__code__ is not previous[1]:
            return False
        if function.__defaults__ is not previous[2] or function.__kwdefaults__ is not previous[3]:
            return False
    return True


def spatial_scope(engine):
    from .playback_engine import PlaybackEngine
    from .transition_anchor_causality import default_scope, _model_access_current
    global _QUALIFIED
    if not _model_access_current() or not default_scope(engine) or any(name in vars(engine) for name in METHODS):
        return False
    if _QUALIFIED is None:
        try:
            _QUALIFIED = _initial_methods(PlaybackEngine)
        except ModuleNotFoundError as error:
            if error.name in ("qascade_ce", "qascade_ce.native_curve_scalar_guard"):
                return False
            raise
    return _QUALIFIED is not None and _current_methods(PlaybackEngine)


def _empty_raw_snapshot(clip):
    from src.core.models import Clip
    if type(clip) is not Clip or "base_fixture_positions" in vars(Clip):
        return False
    value = vars(clip).get("base_fixture_positions")
    return type(value) is dict and not value


def _ordinary_track_clips(track):
    from src.core.models import Track
    if type(track) is not Track:
        return None
    clips = vars(track).get("clips", ())
    if type(clips) not in (list, tuple):
        return None
    return clips


def _ordinary_clips(project):
    from src.core.models import Clip, Project
    if type(project) is not Project:
        return []
    tracks = vars(project).get("tracks", ())
    if type(tracks) not in (list, tuple):
        return []
    result = []
    for track in tracks:
        clips = _ordinary_track_clips(track)
        if clips is None:
            return []
        for clip in clips:
            if type(clip) is not Clip or type(vars(clip).get("id")) is not str:
                return []
            result.append(clip)
    return result


def _source_slots(originals, project):
    return {id(clip): vars(clip)["id"] for clip in _ordinary_clips(project)
            if _empty_raw_snapshot(originals.get(vars(clip)["id"])) and _empty_raw_snapshot(clip)}


def prepare_source_geometry(engine, inputs):
    """One worker job, before its original private precompile/freeze."""
    if not spatial_scope(engine):
        return
    originals = {vars(clip)["id"]: clip for clip in _ordinary_clips(inputs["source"])}
    slots = _source_slots(originals, inputs["project"])
    setattr(engine, SOURCE, {"token": inputs["token"], "interval": inputs["interval"],
                            "runtime": inputs["runtime"], "slots": slots, "filled": {}})


def _spatial_slot(clip):
    """Same static-signature coordinate projection, exact primitive inputs only."""
    positions = vars(clip).get("base_fixture_positions")
    if type(positions) is not dict:
        return None
    if not all(type(key) is str and type(value) in (tuple, list) and len(value) >= 2
               and type(value[0]) in (float, int, bool) and type(value[1]) in (float, int, bool)
               for key, value in positions.items()):
        return None
    return tuple((key, round(float(value[0]), 3), round(float(value[1]), 3))
                 for key, value in sorted(positions.items()))


def record_private_geometry(engine, clip):
    """Called only after original freeze actually writes private default positions."""
    source = getattr(engine, SOURCE, None)
    if source is None or id(clip) not in source["slots"] or not spatial_scope(engine):
        return
    slot = _spatial_slot(clip)
    if slot:
        source["filled"][id(clip)] = slot


def attach_source_geometry(engine, clip, entry):
    source = getattr(engine, SOURCE, None)
    if source is None or not spatial_scope(engine):
        return
    slot = source["filled"].get(id(clip))
    if slot and entry["context"][1][3] == slot:
        entry[FIELD] = {"schema": 1, "token": source["token"], "slot": (),
                        "interval": source["interval"], "runtime": source["runtime"]}


@contextmanager
def current_source_geometry(engine, source_certificate):
    """Caller first passes original current-owner/input gate; scope one restore."""
    saved = _CURRENT_SOURCE.set((engine, source_certificate))
    try:
        yield
    finally:
        _CURRENT_SOURCE.reset(saved)


def _current_source_scope(engine, source_certificate):
    current = _CURRENT_SOURCE.get()
    return current is not None and current[0] is engine and current[1] is source_certificate


def _certificate_values(engine, source_certificate):
    """Only the caller's actual current published/ready owner, never an external dict."""
    current = _CURRENT_SOURCE.get()
    if source_certificate is None and current is not None and current[0] is engine:
        source_certificate = current[1]
    owners = (getattr(engine, "_timeline_published_inputs", None),
              getattr(engine, "_timeline_ready_range", None))
    if not _current_source_scope(engine, source_certificate):
        return None
    if not any(source_certificate is owner for owner in owners if owner is not None):
        return None
    if source_certificate.get("source") is not engine._pm.project:
        return None
    interval = source_certificate.get("interval_ms", source_certificate.get("interval"))
    return source_certificate.get("token"), interval, source_certificate.get("runtime")


def authorize_source_geometry(engine, state, *, source_certificate=None):
    """Low-frequency manual restore hashes once; certified playback supplies its owner."""
    setattr(engine, PERMIT, None)
    setattr(engine, PENDING, None)
    if getattr(engine, "_timeline_bake_owned", False):
        return  # Private contexts are already normalized; never hash the mutated clone.
    if not any(FIELD in entry for entry in state.values()) or not spatial_scope(engine):
        return
    values = _certificate_values(engine, source_certificate)
    manual = values is None
    if manual:
        from .timeline_bake_inputs import input_signature
        values = (input_signature(engine._pm.project, {}), engine._timer.interval(),
                  engine._timeline_render_runtime_signature_value())
    setattr(engine, PERMIT, values)
    setattr(engine, PENDING, (state, engine._pm.project, values, manual))


def _empty_source_context_difference(stored, context):
    if type(stored) is not tuple or type(context) is not tuple:
        return False
    if len(stored) != 4 or len(context) != 4:
        return False
    if not stored[1][3] or context[1][3] != ():
        return False
    static = context[1][:3] + (stored[1][3],) + context[1][4:]
    return stored == (context[0], static, context[2], context[3])


def source_geometry_matches(engine, entry, context):
    """Accept this exact empty-source/private-fill difference; preserve entry bits."""
    proof = entry.get(FIELD)
    if proof is None or proof.get("schema") != 1 or proof.get("slot") != ():
        return False
    values = proof["token"], proof["interval"], proof["runtime"]
    if getattr(engine, PERMIT, None) != values or not spatial_scope(engine):
        return False
    return _empty_source_context_difference(entry["context"], context)


def _live_adoption_scope(engine, pending):
    from .transition_anchor_causality import STATE
    from .timeline_temporal_live_scope import default_live_scope
    return (not getattr(engine, "_timeline_bake_owned", False)
            and pending[0] is getattr(engine, STATE, None)
            and pending[1] is engine._pm.project
            and pending[2] == getattr(engine, PERMIT, None)
            and spatial_scope(engine) and default_live_scope(engine))


def _live_source_values(engine, pending):
    values = _certificate_values(engine, None)
    if values is None:
        if pending[3]:
            values = pending[2]  # Manual restore's one fresh, single-use permit.
        else:
            from .timeline_bake_inputs import input_signature
            values = (input_signature(pending[1], {}), engine._timer.interval(),
                      engine._timeline_render_runtime_signature_value())
    if (values[1] != engine._timer.interval()
            or values[2] != engine._timeline_render_runtime_signature_value()):
        return None
    return values


def _adoption_source_clips(project):
    from .transition_anchor_causality import _primitive_filter_parameters
    clips = _ordinary_clips(project)
    if not clips or not all(_primitive_filter_parameters(clip) for clip in clips):
        return None
    result = {vars(clip)["id"]: clip for clip in clips}
    return result if len(result) == len(clips) else None


def _source_proof_current(proof, values):
    if type(proof) is not dict or proof.get("schema") != 1 or proof.get("slot") != ():
        return False
    return (proof["token"], proof["interval"], proof["runtime"]) == values


def _source_owned_context(entry, values):
    if not _source_proof_current(entry.get(FIELD), values):
        return None
    context = entry.get("context")
    if type(context) is not tuple or len(context) != 4:
        return None
    static = context[1]
    if type(static) is not tuple or len(static) != 5 or type(static[3]) is not tuple:
        return None
    source_static = static[:3] + ((),) + static[4:]
    return context[0], source_static, context[2], context[3]


def _source_context_for_key(key, entry, clips, values):
    if type(key) is not tuple or len(key) != 2 or type(key[0]) is not str:
        return None
    if type(key[1]) is not int or not _empty_raw_snapshot(clips.get(key[0])):
        return None
    return _source_owned_context(entry, values)


def _adopted_source_state(state, clips, values):
    updated = None
    for key, entry in state.items():
        if FIELD not in entry:
            continue
        context = _source_context_for_key(key, entry, clips, values)
        if context is None:
            return None
        if updated is None:
            updated = dict(state)
        value = dict(entry, context=context)
        value.pop(FIELD)
        updated[key] = value
    return updated


def adopt_live_source_geometry(engine):
    """Consume a restored proof once, only after the original cache miss/reject.

    Cached serving and restore return retain the original graph. This adapts
    only the source-owned private spatial metadata; no anchor is recomputed.
    """
    pending = getattr(engine, PENDING, None)
    if pending is None:
        return False
    setattr(engine, PENDING, None)  # A rejected ticket is not a future permit.
    if not _live_adoption_scope(engine, pending):
        return False
    clips = _adoption_source_clips(pending[1])
    if clips is None or _live_source_values(engine, pending) != pending[2]:
        return False
    updated = _adopted_source_state(pending[0], clips, pending[2])
    if updated is None:
        return False
    from .transition_anchor_causality import STATE, REVISION
    setattr(engine, STATE, updated)
    setattr(engine, REVISION, getattr(engine, REVISION, 0) + 1)
    return True


def bind_source_geometry_tokens(anchors, token):
    """Current journal owns any reused prefix; no 512-array or full-context copying."""
    if token is None:
        return anchors
    updated = None
    for key, entry in anchors.items():
        proof = entry.get(FIELD)
        if proof is not None and proof["token"] != token:
            if updated is None:
                updated = dict(anchors)
            value = dict(entry)
            value[FIELD] = dict(proof, token=token)
            updated[key] = value
    return anchors if updated is None else updated


def bind_owned_anchor_tokens(engine, anchors):
    source = getattr(engine, SOURCE, None)
    return bind_source_geometry_tokens(anchors, source["token"]) if source is not None else anchors


def bind_prior_checkpoint_source(engine, point):
    """Only after original dirty_start/choose_point accepts the inherited prefix."""
    from .transition_anchor_causality import STATE
    original = point["python"]["state"].get(STATE, {})
    anchors = bind_owned_anchor_tokens(engine, original)
    if anchors is original:
        return point
    state = dict(point["python"]["state"])
    state[STATE] = anchors
    python = dict(point["python"], state=state)
    return dict(point, python=python)
