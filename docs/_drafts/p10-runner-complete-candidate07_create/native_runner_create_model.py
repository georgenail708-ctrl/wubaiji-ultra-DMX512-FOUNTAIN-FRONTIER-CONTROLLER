"""Bounded raw integer/point packing and metadata freshness; no numerical work."""
import ctypes as ct
from . import native_runner_types as raw
from . import native_runner_budget as budget
from . import native_runner_scope_model as scope
from . import native_runner_edit_model as edit
from . import native_runner_register_model as registry
from . import native_runner_create_types as t


def reservation(owner,module,spec):
    amount = 0 if owner is None else scope._preview_reservation(owner,module)
    if amount is None:
        raise RuntimeError('Create retained owner schema rejected')
    if owner is not None:
        if any(type(train.car_count) is not int for train in owner._trains.values()):
            raise RuntimeError('Create retained count metadata has a foreign supplier')
        amount += sum((train.car_count.bit_length()+8)//8*2 for train in owner._trains.values())
    amount += (len(spec['identity'])+len(spec['color']) if type(spec['color']) is str else len(spec['identity']))*16
    amount += (spec['count'].bit_length()+8)//8*4+200*512+16384
    if amount > budget.OWNER_LIMIT:
        raise budget.BudgetRefused('Create raw/default/readback source exceeds its owned allowance')
    return amount


def _token(owner):
    return (scope.geometry_token(owner),scope.state_token(owner),edit._extra_state(owner),
        tuple(owner._trains.items()),tuple(owner._edges.items()))


class Model:
    def __init__(self,owner,spec,allowance):
        self.owner,self.spec,self.refs = owner,spec,[]
        self.before = None if owner is None else _token(owner)
        self.aliases = None if owner is None else tuple(vars(owner).get(name) for name in
            ('_trains','_edges','_owner_map','_gap_cache'))
        count = spec['count']
        data = count.to_bytes((count.bit_length()+8)//8,'little',signed=True)
        packet = (ct.c_ubyte*len(data)).from_buffer_copy(data)
        self.refs.extend((data,packet))
        points = registry._point_array(spec['points'],self.refs)
        anticipated = spec.get('anticipated',0)
        # IDs/color are opaque host metadata; C needs no second string oracle.
        self.request = t.Request(spec['operation'],b'',b'',packet,len(data),spec['length'],
            spec['radius'],spec['speed'],spec['unit'],points,len(points),allowance,
            bool(anticipated),anticipated)
        self.names = [f"__legacy_track__{spec['identity']}"]

    def current(self):
        if self.owner is None:
            return True
        return (all(vars(self.owner).get(name) is value for name,value in zip(
            ('_trains','_edges','_owner_map','_gap_cache'),self.aliases)) and _token(self.owner) == self.before)
