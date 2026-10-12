"""Fixed original filter metadata guards; source fingerprints run only once."""
import hashlib
import inspect
import sys
from types import FunctionType

from .native_curve_scalar_guard import _signature

_EXPECTED = {'_expand_motor_pair_mask_for_universe': '643f0de4031502ac59c4901ca5359215662ce17bd7319d718df3219c1d9c3cc6',
 '_track_filter_rule_for_universe': 'de18fb417ffb3edb320bc61894ff35da63af7a5ecccf47fbf9c71fc518ac0b64',
 '_track_filter_mask_for_universe': 'c69cb47c8c2fc1196c7ac1bfbd518fbbffceddee0cf0d93493029120b3a78e6b',
 '_project_of': '0b4958c3de795fde438afb1803b64796f933daa14454d7d1d6760c1d6b7abe26',
 'get_filter_for_universe': 'ccc9885ab029ca5d95c7a18bf15653072f8571a5377a660521aaff0b1c77c586',
 'is_empty': '00765046240db564076a14f3ad360e8c3232410254b2a5d1b02591498107b067',
 'allows_all': 'fe5fced7d83f300a40676952029a0cc54dc58e084d9d5094cd3bba3d593438da'}


def _function(owner, name):
    return inspect.getattr_static(owner, name)


def _metadata(function):
    return function, function.__code__, function.__defaults__, function.__kwdefaults__


def _same(function, row):
    return (function is row[0] and function.__code__ is row[1]
            and function.__defaults__ is row[2] and function.__kwdefaults__ is row[3])


def _canonical(function, name):
    if type(function) is not FunctionType:
        return False
    module = "src.core.models" if name in ("get_filter_for_universe", "is_empty", "allows_all") else "src.engine.playback_engine"
    if function.__globals__ is not vars(sys.modules[module]) or function.__kwdefaults__ is not None:
        return False
    defaults = function.__defaults__
    expected_default = name in ("_track_filter_mask_for_universe", "_expand_motor_pair_mask_for_universe")
    if expected_default:
        if type(defaults) is not tuple or len(defaults) != 1 or defaults[0] is not None:
            return False
    elif defaults is not None:
        return False
    digest = hashlib.sha256(repr(_signature(function.__code__)).encode("utf-8")).hexdigest()
    return digest == _EXPECTED[name]


class FilterGuard:
    def __init__(self, engine_type, installer):
        from src.core.models import FilterRule, Track, Project, FixtureInstance, FixtureDefinition
        from src.engine import playback_engine
        self.engine_type, self.installer = engine_type, installer
        self.rule, self.track, self.project = FilterRule, Track, Project
        self.fixture, self.definition = FixtureInstance, FixtureDefinition
        self.project_of = playback_engine._project_of
        originals = ((engine_type, "_track_filter_rule_for_universe"),
                     (engine_type, "_track_filter_mask_for_universe"),
                     (engine_type, "_expand_motor_pair_mask_for_universe"),
                     (Track, "get_filter_for_universe"), (FilterRule, "is_empty"),
                     (FilterRule, "allows_all"))
        self.originals = {name: _metadata(_function(owner, name))
                          for owner, name in originals}
        self.safe = all(_canonical(row[0], name) for name, row in self.originals.items())
        self.originals["_project_of"] = _metadata(self.project_of)
        self.safe = self.safe and _canonical(self.project_of, "_project_of")
        self.wrappers = {name: _metadata(function) for name, function in installer.METHODS
                         if name in ("_track_filter_mask_for_universe", "_expand_motor_pair_mask_for_universe")}
        self.accessors = {cls: cls.__getattribute__ for cls in
                          (engine_type, Track, FilterRule, Project, FixtureInstance, FixtureDefinition)}

    def entry_current(self, engine):
        if not self.safe or type(engine) is not self.engine_type:
            return False
        for name, row in self.wrappers.items():
            if name in vars(engine) or not _same(_function(type(engine), name), row):
                return False
            if not _same(self.installer._ORIGINALS.get(name), self.originals[name]):
                return False
        name = "_track_filter_rule_for_universe"
        return name not in vars(engine) and _same(_function(type(engine), name), self.originals[name])

    def inputs_current(self, engine, track, rule):
        if not self.entry_current(engine) or type(track) is not self.track or type(rule) is not self.rule:
            return False
        if any(cls.__getattribute__ is not function for cls, function in self.accessors.items()):
            return False
        for owner, names in ((track, ("get_filter_for_universe",)),
                             (rule, ("is_empty", "allows_all"))):
            for name in names:
                if name in vars(owner) or not _same(_function(type(owner), name), self.originals[name]):
                    return False
        from src.engine import playback_engine
        return _same(playback_engine._project_of, self.originals["_project_of"])
