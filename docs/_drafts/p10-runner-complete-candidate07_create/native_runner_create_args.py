"""Original argument conversions only; native computes all normalized values."""
from . import native_runner_budget as budget
from . import native_runner_create_types as t

V2_NAMES = ('train_id','car_count','car_length','radius','base_speed','color')
LEGACY_NAMES = ('train_id','path_points','car_count','car_unit','radius','base_speed','color')
DEFAULTS = (1,0.0,0.0,1.0,'#FFB86C')
PLAIN = (int,float,bool,str,type(None),bytes,bytearray)


def bound(call,operation):
    args,keywords = call
    legacy = operation == t.LEGACY
    names = LEGACY_NAMES if legacy else V2_NAMES
    required = 2 if legacy else 1
    if operation == t.DIRECT:
        required = len(names)
    if len(args) > len(names) or any(name in keywords for name in names[:len(args)]):
        return None
    if not legacy and any(name not in names for name in keywords):
        return None
    values = dict(zip(names,args))
    values.update((name,keywords[name]) for name in names if name in keywords)
    if any(name not in values for name in names[:required]):
        return None
    for name,value in zip(names[required:],DEFAULTS):
        values.setdefault(name,value)
    return values


def ordinary(values,operation):
    ignored = ('path_points','color') if operation == t.DIRECT else ('path_points',)
    return all(type(value) in PLAIN for name,value in values.items() if name not in ignored)


def plain_points(points):
    if points is None:
        return True
    return (type(points) in (list,tuple) and all(type(point) in (list,tuple)
        and len(point) == 2 and all(type(value) in PLAIN for value in point) for point in points))


def path_budget(points):
    if points is None:
        return 0
    if type(points) not in (list,tuple):
        raise budget.BudgetRefused('Unbounded foreign path conversion is refused without creating a train')
    amount = len(points)
    if amount*1024+200*512+16384 > budget.OWNER_LIMIT:
        raise budget.BudgetRefused('Legacy raw points exceed the owned default allowance')
    return amount


def converted(values,operation):
    identity = str(values['train_id'])
    if operation == t.DIRECT:
        return _direct(values,identity)
    if operation == t.LEGACY:
        return _legacy(values,identity)
    count,length,radius = int(values['car_count']),float(values['car_length']),float(values['radius'])
    speed,color = float(values['base_speed']),str(values['color'])
    return {'identity':identity,'count':count,'length':length,'radius':radius,'speed':speed,'color':color,'unit':0.0}


def _direct(values,identity):
    # Original direct constructor converts base speed before count and length.
    speed = float(values['base_speed'])
    count,length,radius = int(values['car_count']),float(values['car_length']),float(values['radius'])
    return {'identity':identity,'count':count,'length':length,'radius':radius,
        'speed':speed,'color':values['color'],'unit':0.0}


def _legacy(values,identity):
    unit = float(values['car_unit'] or 0.0)
    speed = float(values['base_speed'])
    count,radius,color = int(values['car_count']),float(values['radius']),str(values['color'])
    return {'identity':identity,'count':count,'length':0.0,'radius':radius,
        'speed':speed,'color':color,'unit':unit}


def points(values):
    try:
        return [tuple(map(float,point)) for point in values or []],None
    except (ValueError,TypeError,OverflowError) as error:
        # MemoryError deliberately does not produce an ordinary partial-create.
        return [],error
