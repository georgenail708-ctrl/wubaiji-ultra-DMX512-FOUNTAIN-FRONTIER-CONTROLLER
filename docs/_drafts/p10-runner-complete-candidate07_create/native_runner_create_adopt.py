"""Materialize all original train slots/legacy aliases before native acceptance."""
from . import native_runner_register_adopt as registry
from . import native_runner_edit_adopt as edit
from . import native_runner_create_types as t

FLOATS = (('base_speed','base_speed'),('throttle','throttle'),('car_length','car_length'),
    ('chain_length','chain_length'),('radius','radius'),('dir_sign','dir_sign'),
    ('throttle_target','target'),('ramp_sec','ramp_sec'),('ramp_from','ramp_from'),
    ('ramp_elapsed','ramp_elapsed'),('start_delay','start_delay'),('_nose_s','nose'))


def _shape(model,report):
    operation = model.spec['operation']
    if report.count_selector not in (0,1) or report.count_fits not in (0,1):
        raise RuntimeError('Native count normalization selector is invalid')
    if report.phase != (1 if operation == t.PARTIAL else 0):
        raise RuntimeError('Native create error stage differs from requested publication')
    output,source = report.edit,report.edit.train
    flags = ('has_train','has_geometry','replace_cars','clear_owner','dirty','update_home','scalar_only','sync_found')
    if any(getattr(output,name) not in (0,1) for name in flags):
        raise RuntimeError('Native create boolean report fields are invalid')
    attached = operation == t.LEGACY and model.request.n_points != 0
    if output.has_train != 1 or bool(output.has_geometry) != (operation == t.LEGACY):
        raise RuntimeError('Native constructor/legacy geometry adoption shape is invalid')
    if bool(output.replace_cars) != attached or bool(output.clear_owner) != (operation == t.LEGACY):
        raise RuntimeError('Native create placement/cache route differs from the original')
    if bool(output.dirty) != (operation != t.DIRECT) or output.update_home or output.scalar_only:
        raise RuntimeError('Native create dirty/home/scalar route differs from the original')
    if bool(output.sync_found) != attached or output.sync_edge != (0 if attached else -1):
        raise RuntimeError('Native create legacy synchronization report is invalid')
    _counts(model,source,attached)
    if source.mode != 0 or source.easing != 105 or source.has_links or source.has_span or source.n_links:
        raise RuntimeError('Native constructor default modes/optional state are invalid')


def _counts(model,source,attached):
    if source.n_cars < 0 or not 0 <= source.n_trail <= 1:
        raise RuntimeError('Native create readback counts are invalid')
    if source.n_cars and source.n_cars > model.spec['count']:
        raise RuntimeError('Native create cars exceed the original positive count')
    if attached and source.n_cars != source.car_count or not attached and source.n_cars:
        raise RuntimeError('Native create cars do not match the complete placement report')
    if bool(source.n_trail) != attached or bool(source.n_cars and source.cars) != bool(source.n_cars):
        raise RuntimeError('Native create cars/trail readback pointer is invalid')
    if source.n_trail and (not source.trail or source.trail[0] != 0):
        raise RuntimeError('Native create legacy trail identity is invalid')
    if source.home_edge != (0 if attached else -1):
        raise RuntimeError('Native create home identity is invalid')


def values(model,report):
    source = report.edit.train
    result = {name:getattr(source,native) for name,native in FLOATS}
    result.update(train_id=model.spec['identity'],color=model.spec['color'],
        car_count=model.spec['count'] if report.count_selector else 0,
        chain_lengths=None,mode='loop_forward',ramp_easing='OutCubic',
        ended=bool(source.ended),skip_gaps=bool(source.skip_gaps),_attracted=bool(source.attracted),
        held_node=None,_home_edge_id=None,cars=[],trail=[],path_points=[],cum=[],
        path_length=report.edit.path_length,car_unit=source.car_length,
        loop_span=None,loop_span_edge=None,proj_s=None,proj_d=None,s_order=None,s_sorted=None,d_sorted=None)
    return result


def initialize(train,fields):
    for name,value in fields.items():
        setattr(train,name,value)


class Prepared:
    def __init__(self,model,report,module):
        _shape(model,report)
        self.model,self.report = model,report
        self.train = object.__new__(module._Train)
        fields = values(model,report)
        self.edge = registry._edge(module,model.names[0],report.edit.geometry,model.request.n_points) if report.edit.has_geometry else None
        if report.edit.replace_cars:
            _placed(fields,module,model,report,self.edge)
        initialize(self.train,fields)
        self.new_gaps = {}

    def commit(self):
        owner,output = self.model.owner,self.report.edit
        # Original publication order: train and dirty before legacy ensure/attach.
        owner._trains[self.model.spec['identity']] = self.train
        if output.dirty:
            owner._ce_ctx_dirty = True
        if self.edge is not None:
            owner._edges[self.model.names[0]] = self.edge
        if output.clear_owner:
            owner._owner_map,owner._gap_cache = None,self.new_gaps


def _placed(fields,module,model,report,edge):
    source = report.edit.train
    fields['cars'] = [edit._car(module,source.cars[index],model.names) for index in range(source.n_cars)]
    fields['trail'],fields['_home_edge_id'] = [model.names[0]],model.names[0]
    fields['path_points'],fields['cum'] = [tuple(point) for point in edge.points],list(edge.cum)
