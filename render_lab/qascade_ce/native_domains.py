"""Install current full-frame domains after legacy CE hooks finish mounting."""
import logging
import sys

log = logging.getLogger(__name__)


def install(engine):
    from . import native_audio_api, native_output_domain, native_transition, native_output_filter
    from . import native_source_motion, native_source_install
    from . import native_source_motion_geometry, native_source_motion_geometry_install
    from . import native_render_timing
    from . import native_legacy_direction_install
    from . import native_curve_lut, native_trajectory
    from . import native_curve_scalar, native_programmer_trajectory
    from . import native_playback_index_install
    from . import native_anchor_public
    from . import native_clip_change_times
    owner = sys.modules[__package__]
    for name, module, statistics in (
        ("audio", native_audio_api, native_audio_api._COUNTERS),
        ("output", native_output_domain, native_output_domain.COUNTERS),
        ("transition", native_transition, native_transition.COUNTERS),
        ("source_motion", native_source_motion, None),
        ("source_geometry", native_source_motion_geometry, None),
        ("source_geometry_public", native_source_motion_geometry_install, None),
        ("filter", native_output_filter, None),
        ("source_public", native_source_install, None),
        ("render_timing", native_render_timing, native_render_timing.COUNTERS),
        ("playback_index", native_playback_index_install, None),
        ("anchor", native_anchor_public, native_anchor_public.COUNTERS),
        ("clip_change_times", native_clip_change_times, native_clip_change_times.COUNTERS),
        ("legacy_direction", native_legacy_direction_install, None),
        ("curve_lut", native_curve_lut, None),
        ("trajectory", native_trajectory, None),
        ("programmer", native_programmer_trajectory, None),
    ):
        if module.install(engine):
            if statistics is not None:
                setattr(owner, "_K2_" + name.upper() + "_CNT", statistics)
            for key, value in vars(module).items():
                if key.endswith("_CNT") and isinstance(value, list):
                    setattr(owner, "_K2" + key, value)
            log.info("[CE] K2 %s mathematical domain enabled", name)
        else:
            log.warning("[CE] K2 %s mathematical domain could not bind", name)
    if native_programmer_trajectory.available():
        for key, value in vars(native_curve_scalar).items():
            if key.endswith("_CNT") and isinstance(value, list):
                setattr(owner, "_K2" + key, value)
