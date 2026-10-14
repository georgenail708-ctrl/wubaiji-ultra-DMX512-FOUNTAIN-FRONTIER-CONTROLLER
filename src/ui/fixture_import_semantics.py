"""General channel roles, explicit slot layout and component identity at import.

This module reads no files, encrypted data, proprietary libraries or keys.
It accepts already decoded source metadata from a format adapter.
"""
from __future__ import annotations

_FUNCTION_NAMES = ('中心', '仪动', 'center', 'centre', '速度', 'speed',
                   '控制方式', 'control mode', 'control pan', 'control tilt')
_AXIS_TYPES = frozenset(('motor_x', 'motor_y', 'motor_z'))


def _ln(tag):
    return tag.rsplit('}', 1)[-1]


def _at(element, key):
    return next((value for name, value in element.attrib.items() if _ln(name) == key), None)


def source_channel_type(source, candidate, kind, feature):
    """Distinguish a continuous motion axis from its centre/speed/mode controls."""
    name = str(_at(source, 'Name') or '').strip().lower()
    axis_like = candidate in _AXIS_TYPES or feature == 'TFeature_MotorAngle'
    if axis_like and (kind == 'mode' or _is_axis_parameter(name)):
        return ''
    if feature != 'TFeature_MotorAngle' or kind != 'analog':
        return candidate
    return 'motor_x' if _has_continuous_binding(source, feature) else candidate


def _is_axis_parameter(name):
    return any(word in name for word in _FUNCTION_NAMES)


def _has_continuous_binding(source, feature):
    linked = any(_ln(item.tag) == 'Object' and _at(item, 'LinkedGUID')
                 for item in source.iter())
    continuous = any(_ln(item.tag) == 'Feature'
                     and _at(item, 'TypeName') == feature
                     and _at(item, 'Value_Type') == '3' for item in source.iter())
    return bool(linked and continuous)


def physical_slot_layout(channels, channel_factory):
    """Keep gaps between explicit 1-based physical slots and retain leading offsets."""
    if not channels or any(ch.slot1 <= 0 for ch in channels):
        return channels
    by_slot = {ch.slot1: ch for ch in channels}
    if len(by_slot) != len(channels):
        return channels
    return [by_slot.get(slot) or channel_factory(
        channel_type='placeholder', source_name='', feature='', default_value=0,
        is_virtual=False, slot1=slot) for slot in range(min(by_slot), max(by_slot) + 1)]


def axis_family_name(guid, fmap, fallback=''):
    node = fmap.get(guid)
    owner = fmap.get(getattr(node, 'axis_owner_guid', ''))
    return str(getattr(owner or node, 'name', '') or fallback).strip()


def instance_identity_map(rows, sequence_ids, fixtures):
    """Bridge imported rows to real instances without changing rows or addresses."""
    return {guid: fixtures[fid] for guid, row in rows.items()
            if (fid := sequence_ids.get((row.type_name, row.seq_id))) in fixtures}


def logical_members(record):
    values = (record.get('member_fixture_ids', []) if isinstance(record, dict)
              else getattr(record, 'member_fixture_ids', []))
    return frozenset(str(value) for value in (values or []))
