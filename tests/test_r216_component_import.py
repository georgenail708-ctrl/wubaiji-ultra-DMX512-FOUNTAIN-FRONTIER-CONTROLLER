"""Public synthetic component import contracts; no private reader or vendor data."""
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import math
import pytest

from src.ui.fixture_import_semantics import (
    source_channel_type, physical_slot_layout, logical_members,
    axis_family_name, instance_identity_map,
)
from src.ui.fixture_world_transform import world_position, ZERO_GUID
from src.ui.import_patch_csv import classify_layout, build_fit_shape


def source(name, linked=False, continuous=True):
    item=ET.Element('Source', Name=name)
    ET.SubElement(item,'Feature',TypeName='TFeature_MotorAngle',
                  Value_Type='3' if continuous else '0')
    if linked:
        ET.SubElement(item,'Object',LinkedGUID='synthetic-axis')
    return item


@pytest.mark.parametrize('name',['Pan 仪动中心','Pan Center','Pan Speed','Control Tilt'])
def test_function_parameters_retain_their_role(name):
    assert source_channel_type(source(name),'motor_x','analog','TFeature_MotorPan') == ''


@pytest.mark.parametrize('linked,continuous,expected',[(True,True,'motor_x'),(False,True,''),(True,False,'')])
def test_continuous_generic_axes_require_binding_evidence(linked,continuous,expected):
    assert source_channel_type(source('Position',linked,continuous),'','analog','TFeature_MotorAngle') == expected


@pytest.mark.parametrize('slots,expected', [([1,2,6],[1,2,3,4,5,6]),([2,5],[2,3,4,5]),([0,2],[0,2]),([1,1],[1,1])])
def test_slot_layout_preserves_known_offsets_without_guessing(slots,expected):
    channels=[SimpleNamespace(slot1=s,channel_type='known') for s in slots]
    out=physical_slot_layout(channels,SimpleNamespace)
    assert [c.slot1 for c in out] == expected
    assert all(out[expected.index(s)] is c for s,c in zip(slots,channels)) if len(set(slots))==len(slots) else out is channels


def test_dict_and_object_members_have_the_same_identity():
    assert logical_members({'member_fixture_ids':['b','a']}) == logical_members(SimpleNamespace(member_fixture_ids=['a','b']))


def test_actual_instance_identity_uses_type_and_sequence():
    fi=SimpleNamespace(id='instance')
    rows={'g':SimpleNamespace(type_name='Axis',seq_id=1)}
    assert instance_identity_map(rows,{('Axis',1):fi.id},{fi.id:fi}) == {'g':fi}


def test_family_alias_applies_only_to_an_annotated_component():
    nodes={'a':SimpleNamespace(name='XY',axis_owner_guid=''),
           'b':SimpleNamespace(name='Upper',axis_owner_guid='a'),
           'c':SimpleNamespace(name='Other',axis_owner_guid='')}
    assert axis_family_name('b',nodes)=='XY'
    assert axis_family_name('c',nodes)=='Other'


def node(**values):
    fields=dict(pos_x=0.,pos_y=0.,pos_z=0.,rot_x=0.,rot_y=0.,rot_z=0.,
                quaternion=None,scale=(1.,1.,1.),parent_guid=ZERO_GUID)
    fields.update(values)
    return SimpleNamespace(**fields)


def test_full_parent_transform_uses_height_and_scale():
    nodes={'p':node(pos_x=100,pos_z=200,rot_x=90,scale=(1,2,1)),
           'c':node(pos_y=10,pos_z=3,parent_guid='p')}
    assert world_position('c',nodes) == pytest.approx((100,-3,220))


def test_quaternion_is_authoritative_and_missing_nodes_are_safe():
    nodes={'p':node(pos_x=100,rot_y=90,quaternion=(0,0,0,1)),
           'c':node(pos_x=10,pos_z=3,parent_guid='p')}
    assert world_position('c',nodes) == pytest.approx((110,0,3))
    assert world_position('missing',nodes)==(0,0,0)


def test_zero_guid_and_cycles_do_not_reapply_parent_transforms():
    nodes={'p':node(pos_x=10,parent_guid='c'),'c':node(pos_x=3,parent_guid='p')}
    assert world_position('c',nodes) == pytest.approx((13,0,0))
    nodes={ZERO_GUID:node(pos_x=99),'c':node(pos_x=3)}
    assert world_position('c',nodes) == pytest.approx((3,0,0))


@pytest.mark.parametrize('start,span,count',[(170,150,14),(-160,320,41),(30,80,9)])
def test_open_arcs_remain_open_across_the_angular_origin(start,span,count):
    points=[(500*math.cos(math.radians(start+span*i/(count-1))),
             500*math.sin(math.radians(start+span*i/(count-1)))) for i in range(count)]
    assert classify_layout(points)=='arc'
    fitted=build_fit_shape(points)
    assert fitted['span_angle']==pytest.approx(span)


def test_closed_ring_remains_a_ring():
    points=[(500*math.cos(math.tau*i/8),500*math.sin(math.tau*i/8)) for i in range(8)]
    assert classify_layout(points)=='circle'


@pytest.mark.parametrize('kind',['package','composite','unit'])
def test_linker_does_not_pair_members_from_different_component_units(kind):
    from src.processing.track_linker_engine import TrackLinkerEngine
    fixtures=[SimpleNamespace(id=k,universe=u) for k,u in [('a',1),('b',4),('c',1),('d',4)]]
    units=TrackLinkerEngine._group_units(fixtures,[{'kind':kind,'member_fixture_ids':['a','b']},
                                                {'kind':kind,'member_fixture_ids':['c','d']}])
    assert [[f.id for f in unit] for unit in units]==[['a','b'],['c','d']]
