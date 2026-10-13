"""Exact builtin metadata only; native code owns clamp/span/median decisions."""
import ctypes as ct
import inspect
import struct

from .native_filter_prepare_types import Numbers, array

PLAIN = (bool, int, float, str, bytes, type(None))
FIELDS = ('motor_limit_scale', 'motor_speed_limit_dps', 'pump_master_scale')
MISSING = object()


def plain_fields(owner, fields):
    kind = type(owner)
    if kind.__getattribute__ is not object.__getattribute__:
        return False
    for name in fields:
        field = inspect.getattr_static(kind, name, MISSING)
        if field is not MISSING and hasattr(type(field), '__get__'):
            return False
    return True


def clip_values(clip, clip_type):
    if type(clip) is not clip_type or not plain_fields(clip, FIELDS):
        return None
    values = tuple(getattr(clip, name, default) for name, default in
                   zip(FIELDS, (1.0, 0.0, 1.0)))
    return values if all(type(value) in PLAIN for value in values) else None


def signature(values):
    return tuple((type(value), struct.pack('=d', value) if type(value) is float else value)
                 for value in values)


def number(value, caught):
    try:
        return float(value), 0, None
    except caught:
        return 0.0, 1, None
    except Exception as error:
        return 0.0, 2, error


def numbers(values):
    parsed = [number(value, (TypeError, ValueError)) for value in values]
    return Numbers(array(ct.c_double, [row[0] for row in parsed]),
                   array(ct.c_int, [row[1] for row in parsed]),
                   {index: row[2] for index, row in enumerate(parsed) if row[2] is not None})


def _range_values(value):
    if type(value) in PLAIN:
        return True
    if type(value) in (list, tuple):
        return all(type(item) in PLAIN for item in value)
    if type(value) is dict:
        return all(type(key) in PLAIN and type(item) in PLAIN for key, item in value.items())
    return False


def _fixture_safe(fixture, kind):
    names = ('universe', 'def_id')
    return (type(fixture) is kind and plain_fields(fixture, names)
            and all(type(getattr(fixture, name)) in PLAIN for name in names))


def _definition_safe(definition, kind):
    if type(definition) is not kind or not plain_fields(definition, ('id', 'motor_ranges')):
        return False
    ranges = definition.motor_ranges
    return (type(definition.id) in PLAIN and type(ranges) is dict
            and all(type(key) is str and _range_values(value) for key, value in ranges.items()))


def source_safe(project, types):
    project_type, fixture_type, definition_type = types
    if type(project) is not project_type or not plain_fields(project, ('fixtures', 'fixture_defs', 'patch_rev')):
        return False
    if type(project.fixtures) not in (list, tuple) or type(project.fixture_defs) not in (list, tuple):
        return False
    return (all(_fixture_safe(item, fixture_type) for item in project.fixtures)
            and all(_definition_safe(item, definition_type) for item in project.fixture_defs))


def warm_source_safe(engine, project, project_type):
    if type(project) is not project_type or not plain_fields(project, ('fixtures', 'fixture_defs', 'patch_rev')):
        return False
    if not plain_fields(engine, ('_clip_fade_geometry_cache',)):
        return False
    if type(vars(engine).get('_clip_fade_geometry_cache')) not in (dict, type(None)):
        return False
    return all((type(project.fixtures) in (list, tuple), type(project.fixture_defs) in (list, tuple),
                type(getattr(project, 'patch_rev', 0)) in PLAIN))


def _endpoint_pair(value):
    try:
        right = value[1]
    except (TypeError, ValueError, IndexError):
        return (0.0, 1, None), (0.0, 1, None)
    except Exception as error:
        return (0.0, 0, None), (0.0, 2, error)
    parsed_right = number(right, (TypeError, ValueError, IndexError))
    if parsed_right[1]:
        return (0.0, 0, None), parsed_right
    try:
        left = value[0]
    except (TypeError, ValueError, IndexError):
        return (0.0, 1, None), parsed_right
    except Exception as error:
        return (0.0, 2, error), parsed_right
    return number(left, (TypeError, ValueError, IndexError)), parsed_right


def span_rows(public, project, universe, axes):
    definitions = {str(getattr(item, 'id', '')): item for item in project.fixture_defs}
    rows = []
    for fixture in project.fixtures:
        if public._to_int(getattr(fixture, 'universe', 1), 1) != int(universe):
            continue
        definition = definitions.get(str(getattr(fixture, 'def_id', '')))
        ranges = definition.motor_ranges if definition is not None else {}
        for index, axis in enumerate(axes):
            left, right = _endpoint_pair(ranges.get(axis))
            rows.append((index, left, right))
    return rows


def span_arrays(rows):
    errors = {}
    for index, (_, left, right) in enumerate(rows):
        if left[2] is not None:
            errors[2 * index] = left[2]
        if right[2] is not None:
            errors[2 * index + 1] = right[2]
    return (array(ct.c_int, [row[0] for row in rows]),
            array(ct.c_double, [row[1][0] for row in rows]),
            array(ct.c_double, [row[2][0] for row in rows]),
            array(ct.c_int, [row[1][1] for row in rows]),
            array(ct.c_int, [row[2][1] for row in rows]), errors)
