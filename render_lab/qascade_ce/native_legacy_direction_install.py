"""Install native retained-row preparation without touching the dirty builder."""
import functools
import logging

from . import native_legacy_direction_builder as builder
from . import native_legacy_direction_paths as paths
from . import native_legacy_direction_prepare as prepare

log = logging.getLogger(__name__)
_LEGACY_DIRECTION_BUILD_CNT = [0]
_LEGACY_DIRECTION_PATH_A_CNT = [0]
_LEGACY_DIRECTION_PATH_B_CNT = [0]
_LEGACY_DIRECTION_ERROR_CNT = [0]
_LEGACY_DIRECTION_FALLBACK_CNT = [0]
_LEGACY_DIRECTION_CALL_CNT = prepare._LEGACY_DIRECTION_CALL_CNT
_LEGACY_DIRECTION_NATIVE_SUCCESS_CNT = prepare._LEGACY_DIRECTION_NATIVE_SUCCESS_CNT
METHODS = {"_build": (builder.build, _LEGACY_DIRECTION_BUILD_CNT),
           "_build_path_a": (paths.path_a, _LEGACY_DIRECTION_PATH_A_CNT),
           "_build_path_b": (paths.path_b, _LEGACY_DIRECTION_PATH_B_CNT)}


def counters():
    return {name: value[0] for name, value in globals().items()
            if name.startswith("_LEGACY_DIRECTION_") and name.endswith("_CNT")}


def _wrapper(original, implementation, successes):
    @functools.wraps(original)
    def invoke(owner, *args, **kwargs):
        before = _LEGACY_DIRECTION_NATIVE_SUCCESS_CNT[0]
        try:
            result = implementation(owner, *args, **kwargs)
        except Exception:
            _LEGACY_DIRECTION_ERROR_CNT[0] += 1
            _LEGACY_DIRECTION_FALLBACK_CNT[0] += 1
            log.warning("native retained-row preparation failed: %s", original.__name__, exc_info=True)
            return original(owner, *args, **kwargs)
        if _LEGACY_DIRECTION_NATIVE_SUCCESS_CNT[0] > before:
            successes[0] += 1
        return result
    return invoke


def install(engine):
    if not prepare.install(engine):
        return False
    from .timeline_ctx import DirRowBuilder
    if DirRowBuilder.__dict__.get("_ce_legacy_direction_installed"):
        return True
    for name, (implementation, successes) in METHODS.items():
        original = getattr(DirRowBuilder, name)
        setattr(DirRowBuilder, name, _wrapper(original, implementation, successes))
    DirRowBuilder._ce_legacy_direction_installed = True
    return True
