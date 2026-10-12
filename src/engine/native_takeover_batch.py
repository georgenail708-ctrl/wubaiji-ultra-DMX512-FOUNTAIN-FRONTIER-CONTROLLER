"""Count exact native lost rows once; state stays per row.

References: existing selected_mask_rows/count_rows/ce_domain_mask_counts,
fixed first-party source receipts, NumPy 2.4.4 external-loop batching design.
No input, output, count, native pointer or numerical result is cached.
"""
import hashlib
import sys
from types import FunctionType

import numpy as np

_GUARD = None
_EXPECTED = {
    "group": "1fb6a71a6f3e265234471b42767b935c5c2b3b44f1fbddc3a93c64177a28a60d",
    "producer": "0995fd40d103db97f5f872ada2814fbeec7ee15e33a798533660348613e92cf8",
    "domain": "1cdf4f2e2b871e02ca0c98af01614ace5a5db94c5948a458941cc5adfe07ce2a",
    "count": "ce757baab12c1c14d9ada420dd4c9189908e2a7c9f63f09a1169deefcb425a7e",
    "merge": "449ac36464f5e79b31e36bb30830bbe2ac0108423a85444b751c6082c38dcb69",
    "selected": "7e9a4b1942a5c3c8f55238d9da4fdbeead5aeb341ddb899075c876daade0456b",
    "marker": "5af2235718bc78b54843d299cdebd519b457869b8946a6faa4e86277bc10d378",
    "compact": "3207e11d5715fda6d3c600b9e11472422415308f9966ff581d7550979557c04d",
    "counts": "1e96e072b84c434ecac629dab8c4c30c7983dd988559de732711c9a324af23c7",
    "single": "a18cd7deae7143cf720bc15698a354f45be315fbb78f44e986a83ece784b045c",
    "math": "95ef243bd62e07c83a0799cdd0bcca1f14b6ccb2726076049c9efe611af12c78",
    "mask": "c7c08fb24fa612583967f7e451ed04360c1d7a78097536150f597e74d63caa51",
}


def _defaults_match(key, defaults):
    if key == "group":
        return (type(defaults) is tuple and len(defaults) == 1
                and type(defaults[0]) is tuple and not defaults[0])
    if key in ("math", "mask"):
        return type(defaults) is tuple and len(defaults) == 1 and defaults[0] is None
    return defaults is None


def _capture(owner, core, public, module):
    from qascade_ce.native_curve_scalar_guard import _signature
    engine_type, domain_type = public.PlaybackEngine, module.OutputDomain
    specs = (("group", vars(owner), "_group_math", vars(owner)),
             ("producer", vars(owner), "ownership_math", vars(owner)),
             *((key, vars(core), name, vars(core)) for key, name in
               (("domain", "domain"), ("count", "mask_count"),
                ("merge", "merge_consumed"), ("selected", "selected_mask_rows"),
                ("math", "mask_math"))),
             ("marker", vars(engine_type), "_mark_takeover_consumed", vars(public)),
             ("compact", vars(domain_type), "ownership_compact", vars(module)),
             ("counts", vars(domain_type), "mask_counts", vars(module)),
             ("single", vars(domain_type), "mask_count", vars(module)),
             ("mask", vars(domain_type), "mask", vars(module)))
    rows = []
    for key, namespace, name, globals_ in specs:
        function = namespace.get(name)
        if type(function) is not FunctionType or function.__globals__ is not globals_:
            return False
        defaults = function.__defaults__
        if not _defaults_match(key, defaults):
            return False
        if function.__kwdefaults__ is not None:
            return False
        digest = hashlib.sha256(repr(_signature(function.__code__)).encode("utf-8")).hexdigest()
        if digest != _EXPECTED[key]:
            return False
        rows.append((namespace, name, function, function.__code__, defaults))
    return engine_type, domain_type, tuple(rows)


def _metadata_current(rows):
    return all(namespace.get(name) is function and function.__code__ is code
               and function.__defaults__ is defaults and function.__kwdefaults__ is None
               for namespace, name, function, code, defaults in rows)


def _shared_math(owner, core, public):
    return (owner.mask_count is core.mask_count and owner.domain is core.domain
            and public.mask_count is core.mask_count and public.merge_consumed is core.merge_consumed)


def _native_entries_current(native):
    return not any(name in vars(native) for name in ("ownership_compact", "mask_counts", "mask_count", "mask"))


def _types_current(engine, native, engine_type, domain_type):
    return type(engine) is engine_type and type(native) is domain_type


def _current(engine, owner, core, public, module):
    global _GUARD
    if _GUARD is None:
        captured = _capture(owner, core, public, module)
        if captured is False:
            return False
        _GUARD = captured
    engine_type, domain_type, rows = _GUARD
    if not _types_current(engine, module.NATIVE, engine_type, domain_type):
        return False
    if vars(engine).get("_timeline_bake_owned") is not True or "_mark_takeover_consumed" in vars(engine):
        return False
    if not _native_entries_current(module.NATIVE):
        return False
    if not _metadata_current(rows):
        return False
    return _shared_math(owner, core, public)


def _previous_mask_current(previous, universe):
    if type(previous) is not dict:
        return False
    value = previous.get(universe)
    return value is None or (type(value) is np.ndarray
                            and value.dtype == np.bool_ and value.shape == (512,))


def _state_current(engine, universe, metas):
    state = vars(engine)
    table = state.get("_takeover_consumed_masks")
    if (type(table) is not dict or type(universe) is not int
            or any(type(state.get(name)) is not dict for name in
                   ("_dirty_render_item_memo", "_dirty_render_uni_memo"))):
        return False
    for key, _chain in metas:
        previous = table.get(key)
        if previous is None:
            continue
        if not _previous_mask_current(previous, universe):
            return False
    return True


def _plain_matrix(lost, metas):
    return type(lost) is np.ndarray and lost.dtype == np.bool_ and lost.shape == (len(metas), 512)


def _plain_meta(row):
    return (type(row) is tuple and len(row) == 2 and type(row[0]) is str
            and type(row[1]) is tuple and all(type(value) is str for value in row[1]))


def _eligible_rows(results, metas):
    if type(results) is not tuple or len(results) != 4 or type(metas) is not list:
        return False
    lost = results[2]
    if not _plain_matrix(lost, metas):
        return False
    if len(metas) <= 1 or not lost.flags.c_contiguous:
        return False
    return all(_plain_meta(row) for row in metas)


def selected_indices(engine, owner, universe, results, metas):
    """Return native selected indices, or None before consuming foreign inputs."""
    if not _eligible_rows(results, metas):
        return None
    core = sys.modules["src.core.native_mask_math"]
    public = sys.modules["src.engine.playback_engine"]
    module = sys.modules.get("qascade_ce.native_output_domain")
    if (module is None or module.NATIVE is None
            or not _current(engine, owner, core, public, module)
            or not _state_current(engine, universe, metas)):
        return None
    # The tuple and exact matrix remain strongly owned across synchronous C.
    return core.selected_mask_rows(results[2])
