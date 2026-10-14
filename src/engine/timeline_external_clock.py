"""Optional clock provider on the existing render owner; no UI dependency."""
import threading


def prepare_generator_frame(engine, layer_name):
    provider = vars(engine).get('_sntc_bridge')
    if provider is None or layer_name != 'clip_playback':
        return None
    from .lock4_admission import thread_source_current
    if not thread_source_current():
        raise RuntimeError('Generator thread supplier is not the qualified stdlib source')
    if threading.current_thread() is not vars(engine).get('_gen_thread'):
        return None
    from .timeline_temporal_replay import outer_owns_lock, replay_owns_lock
    if replay_owns_lock(engine):
        return None
    if not outer_owns_lock(engine):
        raise RuntimeError('Generator clock needs the actual acquired render owner')
    from .timeline_sntc_source_guard import provider_current
    from .lock4_admission import source_current
    if not provider_current(engine, provider) or not source_current(provider):
        raise RuntimeError('Ordinary generator source is not the qualified current owner')
    return provider.prepare_generator_frame()




def prepare_external_frame(engine, now, layer_name):
    provider = vars(engine).get('_sntc_bridge')
    if provider is not None and layer_name == 'clip_playback':
        from .timeline_temporal_replay import replay_owns_lock
        if replay_owns_lock(engine):
            # Genuine shadow frames must retain the canonical cache grid. They
            # are temporal reconstruction, not an external output consumer.
            return now
        from .timeline_sntc_source_guard import provider_current
        if not provider_current(engine, provider):
            raise RuntimeError('External timeline provider is not the qualified current owner')
        return provider.prepare_render_frame(now)
    return now


def finish_external_frame(engine, succeeded):
    provider = vars(engine).get('_sntc_bridge')
    if provider is not None:
        from .timeline_temporal_replay import replay_owns_lock
        if replay_owns_lock(engine):
            # The outer actual output frame retains its own pending view until
            # it succeeds; shadow reconstruction cannot acknowledge that view.
            return
        from .timeline_sntc_source_guard import provider_current
        if provider_current(engine, provider):
            provider.finish_render_frame(succeeded)
