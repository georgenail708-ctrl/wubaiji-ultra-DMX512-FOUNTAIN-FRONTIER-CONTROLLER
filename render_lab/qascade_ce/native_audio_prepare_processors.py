"""Ordered catalog/string/member projection, with one native numeric tape."""


def _parameter(tape, value, spec):
    if 'choices' in spec:
        return 'metadata', value if value in spec['choices'] else spec['default']
    options = spec['default'], spec['min'], spec['max'], isinstance(spec['default'], int)
    index = tape.add('PROCESSOR', (value,), options)
    return 'integer' if options[3] else 'number', index


def _row(tape, raw, position, public):
    kind = tape.metadata(str, (raw.get('kind', ''),))
    spec = public.PROCESSOR_CATALOG.get(kind)
    if spec is None:
        return None
    values = raw.get('params')
    values = values if type(values) is dict else {}
    params = {key: _parameter(tape, values.get(key, item['default']), item)
              for key, item in spec['params'].items()}
    pair = None
    if kind == 'motion':
        pair = tape.pair(params['min_scale'][1], params['max_scale'][1])
    source = tape.metadata(str, (raw.get('source', spec['source']),))
    identity = tape.metadata(str, (raw.get('id') or f'{kind}-{position}',))
    wet = tape.add('PROCESSOR', (raw.get('wet', 1.),), (1., 0., 1., 0))
    targets = tape.metadata(public._targets, (raw.get('targets'), spec['targets']))
    row = dict(id=identity, kind=kind, enabled=bool(raw.get('enabled', True)), wet=wet,
               source=source if source in public.SOURCE_KEYS else spec['source'], targets=targets, params=params)
    return row, pair


def prepare(tape, raw, public):
    rows = []
    values = raw if type(raw) in (list, tuple) else ()
    for position, item in enumerate(values[:32]):
        if type(item) is not dict:
            continue
        row = _row(tape, item, position, public)
        if row is not None:
            rows.append(row)
    return rows


def _params(result, projection):
    values = {}
    for key, (kind, index) in projection.items():
        if kind == 'metadata':
            values[key] = index
        else:
            value = result.scalar(index)
            values[key] = int(value) if kind == 'integer' else value
    return values


def publish(result, projections):
    rows = []
    for row, pair in projections:
        params = _params(result, row['params'])
        if pair is not None:
            params['min_scale'], params['max_scale'] = result.row(pair)[:2]
        rows.append(dict(id=row['id'], kind=row['kind'], enabled=row['enabled'], wet=result.scalar(row['wet']),
                         source=row['source'], targets=row['targets'], params=params))
    return rows
