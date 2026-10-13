"""Point shape metadata; native batch owns clamp/round/order/dedup decisions."""


def _values(row):
    if type(row) is dict:
        return row.get('t'), row.get('v'), row.get('c')
    if type(row) in (list, tuple) and len(row) >= 2:
        return row[0], row[1], row[2] if len(row) >= 3 else 0.
    return None


def prepare(tape, raw):
    first = len(tape.steps)
    values = raw if type(raw) in (list, tuple) else ()
    for row in values:
        parsed = _values(row)
        if parsed is not None:
            tape.add('POINT', parsed, (0., 0., 0., 0))
    return tape.group('POINT_SORT', first)


def publish(result, group):
    rows = []
    for index in result.ordered(group):
        values = result.row(index)
        rows.append(list(values if result.flags[index] == 3 else values[:2]))
    return rows
