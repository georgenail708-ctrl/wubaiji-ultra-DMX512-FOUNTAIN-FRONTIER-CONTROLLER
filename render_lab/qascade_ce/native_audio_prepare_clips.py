"""Original Clip metadata order; native admission skips every child instruction."""
from . import native_audio_prepare_points as points


def _text(value):
    return str(value or '')


def _clip(tape, raw, public):
    header = tape.add('CLIP', (raw.get('start', 0.), raw.get('end', 0.)), (public.MIN_CLIP_S, 0., 0., 0))
    targets = tape.metadata(public._targets, (raw.get('targets'),))
    identity = tape.metadata(_text, (raw.get('effect_id', ''),))
    wave = tape.metadata(_text, (raw.get('wave', ''),))
    frequency = tape.add('FREQ', (raw.get('wave_freq'),), (2., 0., 0., 0))
    group = points.prepare(tape, raw.get('points'))
    fade_in = tape.add('FADE', (raw.get('fade_in'),), (public.FADE_INHERIT, 0., 0., 0))
    fade_out = tape.add('FADE', (raw.get('fade_out'),), (public.FADE_INHERIT, 0., 0., 0))
    tape.steps[header].skip_to = len(tape.steps)
    return header, dict(effect_id=identity, wave=wave, frequency=frequency, targets=targets,
                        points=group, fade_in=fade_in, fade_out=fade_out)


def prepare(tape, raw, public):
    first, rows = len(tape.steps), {}
    values = raw if type(raw) is list else ()
    for row in values:
        if type(row) is dict:
            index, projection = _clip(tape, row, public)
            rows[index] = projection
    return rows, tape.group('CLIP_SORT', first)


def publish(result, prepared):
    projections, group = prepared
    rows = []
    for index in result.ordered(group):
        row, bounds = projections[index], result.row(index)
        rows.append(dict(start=bounds[0], end=bounds[1], effect_id=row['effect_id'], wave=row['wave'],
                         wave_freq=result.scalar(row['frequency']), targets=row['targets'],
                         points=points.publish(result, row['points']), fade_in=result.scalar(row['fade_in']),
                         fade_out=result.scalar(row['fade_out'])))
    return rows
