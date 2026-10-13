"""Original state field/copy order; numeric field results come only from C."""
from . import native_audio_prepare_clips as clips
from . import native_audio_prepare_processors as processors


def _feature(value):
    return str(value or 'beat_strength')


def prepare(tape, raw, suppliers):
    public, catalog, lifetime = suppliers
    state = dict(raw) if type(raw) is dict else {}
    result = dict(version=public.STATE_VERSION, audio_sha=tape.metadata(public._text, (state.get('audio_sha'),)),
                  scope_folder_id=tape.metadata(public._text, (state.get('scope_folder_id'),)),
                  target_ch_types=tape.metadata(public._targets, (state.get('target_ch_types'),)))
    values = dict(amount=tape.add('CLAMP', (state.get('amount', 1.),), (0., 0., 1., 0)),
                  attack=tape.add('MAX', (state.get('attack'),), (.05, 0., 0., 0)),
                  release=tape.add('MAX', (state.get('release'),), (.2, 0., 0., 0)))
    result.update(feature=tape.metadata(_feature, (state.get('feature', 'beat_strength'),)),
                  default_wave=tape.metadata(public._text, (state.get('default_wave'),)))
    for name in ('spread_deg', 'phase_from_deg', 'phase_to_deg'):
        values[name] = tape.add('CLAMP', (state.get(name),), (0., 0., 360., 0))
    clip_projection = clips.prepare(tape, state.get('clips'), public)
    result.update(sources=tape.metadata(public._dict_rows, (state.get('sources'),)),
                  rules=tape.metadata(public._dict_rows, (state.get('rules'),)),
                  draw_graph=tape.metadata(public._copy_graph, (state.get('draw_graph'),)))
    chain = _optional(tape, state, result, (catalog, lifetime))
    return result, values, clip_projection, chain


def _optional(tape, state, result, suppliers):
    catalog, lifetime = suppliers
    if lifetime.timeline_gated(state):
        result['timeline_gated'] = True
    if 'processors' not in state and 'processor_mode' not in state:
        return None
    result['processor_mode'] = 'chain' if state.get('processor_mode') == 'chain' else 'legacy'
    return processors.prepare(tape, state.get('processors'), catalog)


def publish(result, prepared):
    metadata, values, clip_projection, chain = prepared
    ordered = dict(version=metadata['version'], audio_sha=metadata['audio_sha'], scope_folder_id=metadata['scope_folder_id'],
                   target_ch_types=metadata['target_ch_types'], amount=result.scalar(values['amount']),
                   attack=result.scalar(values['attack']), release=result.scalar(values['release']),
                   feature=metadata['feature'], default_wave=metadata['default_wave'])
    ordered.update((name, result.scalar(values[name])) for name in ('spread_deg', 'phase_from_deg', 'phase_to_deg'))
    ordered.update(clips=clips.publish(result, clip_projection), sources=metadata['sources'], rules=metadata['rules'],
                   draw_graph=metadata['draw_graph'])
    if 'timeline_gated' in metadata:
        ordered['timeline_gated'] = True
    if chain is not None:
        ordered.update(processor_mode=metadata['processor_mode'], processors=processors.publish(result, chain))
    return ordered
