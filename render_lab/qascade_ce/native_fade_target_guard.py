"""C03 exact supplier snapshots and small current predicates, old stamps intact."""
import ctypes as ct
import hashlib
import inspect
import sys
from types import FunctionType

import numpy as np

from .native_curve_scalar_guard import _signature

EXPECTED = {'native_fade_target': {'counters': '34fe12388f7fac4c01e4452b55411050e1e5195392857a835ec7a270c020c89a', '_plain': 'b9e807ee044a6997804a9ce4435c18e46afb0c60c24b63ac868140ad37f32d2b', '_array': 'd115fdf7d2a1416929ec4f2c68c9aa0fd5791686a4d96c1a797973dc130b14d1', '_call': 'd709b143e88d3e0711df5d97fce1fe1a2e67f94448378b7f11cfe1e508e1c323', 'signature': '5e8fd1e01222a2f9d99fe3aca6fa44a32b8e4a2dc67a0577c65676ff2974b638', 'target': '5a9335b778d51e7321070dc37a25ca4c7a2b8276095dcc88828faf3a05ce4e3b', 'install': '0ac051253b6d4aa7fc3519077c6147f15309fafab5f236e5810170d08dd75dc3'}, 'native_fade_target_runtime': {'_signature': 'de3c94127c7361a4269e6f6b5df54eb990eebb1818c763e5c5f0cc1ecba625ee', '_content': 'b281f6d0adde95e75c562af78ce102d8c2f428068c87cca372eef8d73343f41b', '_project_rows': '86c82641feb0ef82e1248dd1c8b317cd7a318c71ce018aa65522b50b4cf2b4da', '_retire_changed_motor': '8e314785cf7119cdc1f2ee8838e1e9a1c64a40f4bde68ed9af9e7c5222c767b5', '_default_frame': 'd04b64c523c0de7c8593d8bcc9fded1fe7891c37f1f3a84acb3721b35e6332e3', '_target': '01eecd979dcdd7a4688cf4c66373a12cfbf402700136357441dd883c92f7b704', 'render': 'b84913592692dc7d20a91e7bf544e395234b9d1ec655a2dd36b1bb60aff70d65', 'wrap': '5171456ac40354b7e3ac3cc2d1ba8b47bef262a4166d07db6c745ae96b2a9862'}, 'wrapper': '788412e56eb875ab85e19cdcf9cfbd85eb9242cd616f3d3b8529ec92a131a463', 'native_fade_target_guard': {'digest': '930eb8c49fae3a2f84f32679ecd9fd4b6a6e845b32a00981aab45ea5be20ad3b', 'stable': '077a0fa5f45e4011d29414806bce9bb9557eb95f32a6b10b4416e017075f9f13', 'metadata': '906810e3300c77fa3d749ef19a0880c35939f709655d2f0b3d0461f80d711245', 'metadata_current': '7b3848bbef252a21e0669975e9fba688cb36666fe7432b8b64d860ec3853a919', 'functions': '44d24368ce3510c3e42ab845e1569ba5d46aedda303070898210170a25f64c5d', 'supplier': '5af9264ed9ebf33fb4e519c52e4ae2692408f8c807c53212e7b3333095cde4b1', 'plain_field': '3d55ac16eefde782c815f78d3a54c7fa0edf0606dfae2d1972407f7b470b9c0b', 'plain_defaults': '3deebf1c3921de4d65db1b1c13375676d2542f3a6f599155f78dd76907169322', 'capture_functions': '859721fd8e2d3e266ba1d64a5c2fb726e6c02fd0117ad63857c630b0be8263a2', 'function_rows_current': '05942421f06fcc9ff9334d2ec1956c931d9cf60a3c1e5462414d9e98f1e002be', 'value_rows_current': '2a6b336d91ae647ce82839f46e31ccdd68e93b517bee503ec51a37d171092cde', 'capture_values': '81c4ff618af67bd2fe2cbfee2fcc8240b0a195d2a903bc46a176fa9e1a2083cb', 'FadeGuard.__init__': 'e797588ce60307f0ae43151ffbe4b7cbc322b92c21eba5fc6e1dbb9657c4cd8c', 'FadeGuard.attach': 'b952effabe305734c1984f1d1bc6989cb987309cddfd5b9ec0c3e6d7b02a70b8', 'FadeGuard._wrapper_current': 'e2f2fb9a47ae10c27cb6d8c7d63a72376a76a06d8c831819d0df7191dcdd7dec', 'FadeGuard._methods_current': '3b5ffa288dc940ff988a548c74bb487746a5d786cdf005847dc61fe3f28d9fd4', 'FadeGuard._numpy_current': '00429efa6c26b460b5b1d1b76faca8ec1a4444de6abee354e5dab1d790824cc2', 'FadeGuard._numpy_keywords_current': 'f886001d77014003311dedd266007cb7998c19d46fe33a7ba1e296a4344d1286', 'FadeGuard._suppliers_current': 'ac34aa23e2faa9cd230a4d74ef6b653cabc66c6af43ca1c655a230e6e2b31f3f', 'FadeGuard.source_current': 'e1b237e7fb296098be9de0b446787e8f97c96d54cd363354c9e6eafdc4553f67', 'FadeGuard.binding_current': '55d52fc833acfa1322930308d44c29181c59a68bb4eccafc17c889b3a5006daf', 'FadeGuard._classes_current': 'd8b0a11f8dc2ee9608de7d3cd4d9bd04c49398715b3879dd38911700d107865c', 'FadeGuard._project_current': 'cc499e48b69352a75f16531ab9809e2c5cb7f7e89fe3315c29c9e42ec50a7ddd', 'FadeGuard._owner_current': 'f8bcff856e5520cb43733965adca803010538652f70623749ace1a9536ca6ef5', 'FadeGuard.current': '5c45405857914fe96e60d88b38002f99127fc2892243627de1b6d02790cac6a4'}, 'native_fade_target_install': {'guard_for': '1c1c13bb3f374a724d12a67e7adac93140d3f919048c3a40fd4c950a35b135eb', 'wrapper_current': '9c6541150bd5ca3986f44ff8d67037f58f5f3b3453c0e15a34b5e7c35677166d', 'install': 'c8e6cd408524b9a039b9b8b7d3d9792f5d66cc5bed4d611ce24885c35cda7929'}, 'native_fade_target_schema': {'modules': '21dfc6b08ff98d463f969662b631a8a6ec715988c5bd810c2bb534b88bdc5e3a', 'arguments': '800ec244dd43326fae9525d81c9c1e243b058b4654aa9cefd38d8f3514d0a3c5', 'declarations': '03dc125556774af165c76aca7deab49a233811a43ba6aea6bca3fce53772fd64', 'arguments_current': '0c877b562fa10af7f0368a832e50849899df2002ffac7abf776d7bca991d1f97', 'binding_rows': '65dbd20e408b8de791cf48d3f8f0dcdb07bd4785ea5ba4d34ab96e27a7f5bce7', 'bound': 'ec7140ff6238690887d683088835a1936070016f37c8466c1709203ad1887fa4', 'aliases': 'f55a980257775f6730077be0c06f6705d2b58bdbce930a6b391353a5a3d59f0b', 'original_function': '622f372e613ce14e6a2b41ba43470223329d85f5fcb9659cd3702bb2fea76332', 'pointer_suppliers': '71bde7f351bb03dbc51687e534ad87c79c5ac188cb505b508ab7e1521c9a4f49', 'source_methods': '866a5f36cd83aaf81aeb1f5cc5ad4fccff03da16ebe1ae2113c59c8031adb94f', 'numpy_function': '75d660ace0e4346ff1bdd820804167f55b24646a3bb1b46d7f85d714e2e83dd3', 'numpy_suppliers': '8f3e90fd49763bc5ae05ca30664adbe8386b0aa5c135df8f9b2ae743ef46d78e', 'preflight': '9c7948f400c405faa1df222f9db61a57ce000de72f7e8f87c7c19bb1cd70e7a6', 'dependency_rows': 'bc9379a10ff28b05c8a0577eaf3b0ce6a8070664e30656d599316ab8b911d38a'}, 'native_binding': {'bind_native': '4ff447a0708fc9d3f2f7003e30f16910a7f4bed07002e4411cbb3c2f7f639b62', 'borrowed_pointer': 'df275281f0949069e2cad5d42f79e9019b55ddb65d0d0e2630a4a99c54db4472'}, 'numpy': {'sum': '9fe3321d60d35d48d3c4faaabad507756f046a9c8647c389ac4f23a2d50fe44b', 'count_nonzero': 'f6c63027ba8eac91dce2fe159d2554d345b6f7f95061d1650e5134e1d6d6ddc6'}, 'ctypes': {'cast': 'a76b3b8da965ccfc710256b7ca7967317d851737e69ff95373fb10fefea15dd4'}, 'functools': {'wraps': '48da44272ffc1130812635b1d2cf783b7394995df0f0db4f769de69b07713e91'}, 'scalar': {'_signature': 'e359eac7cd785f2c350848dab50b4c1ee36263346cbbd9e7a70cc2a86b43fd6a', '_normalize': '1d42cc8cafb9797957bae0e7ffd48171bacb7e2b40d25a9e8ad885fee83e4552'}}
ORIGINAL = {'_get_fade_out_target': '7f64f146d4f2d367d26f64f00dcdae06e1bb5c88b1403b815d0997a8020649e1', '_build_motor_channel_mask': 'b9cb60acf7a2a0b6882d9ecb117e06ec4365224ec7ec4bf8fa51759c01a5002c', '_store_clip_fade_geometry_cache': 'e95977ace781f2cb075ddf4488b5eaf9ff65ca834dfaeaa844faf1ec78de7e3a', '_evict_oldest': '4d1ace21e71fa5652ba99d701d2a39ef9022e9b235e8f2ad6a00d129770db9bc'}
MISSING = object()
NUMPY_FIELDS = ('asarray','ascontiguousarray','empty','zeros','sum','count_nonzero',
    'float32','uint8','bool_','uint32','ndarray')
METHODS = ('_get_fade_out_target','_build_motor_channel_mask',
    '_store_clip_fade_geometry_cache','_evict_oldest')


def digest(function):
    return hashlib.sha256(repr(stable(_signature(function.__code__))).encode()).hexdigest()


def stable(value):
    if type(value) is frozenset:
        return ('frozen',tuple(sorted(value)))
    if type(value) is tuple:
        return tuple(stable(item) for item in value)
    return value


def metadata(function):
    return function,function.__code__,function.__defaults__,function.__kwdefaults__


def metadata_current(row):
    function = row[0]
    return (function.__code__ is row[1] and function.__defaults__ is row[2]
        and function.__kwdefaults__ is row[3])


def functions(module):
    rows = [(name,value) for name,value in vars(module).items()
        if type(value) is FunctionType and value.__module__ == module.__name__]
    for cls in (value for value in vars(module).values() if isinstance(value,type)
            and value.__module__ == module.__name__):
        rows.extend((cls.__name__+'.'+name,value) for name,value in vars(cls).items()
            if type(value) is FunctionType)
    return rows


def supplier(module):
    wanted = EXPECTED[module.__name__.rsplit('.',1)[-1]]
    actual = functions(module)
    return ({name for name,_ in actual} == set(wanted)
        and all(digest(function) == wanted[name]
            and function.__globals__ is vars(module)
            and function.__kwdefaults__ is None
            and (function.__defaults__ == (None,) if name == 'wrapper_current'
                else function.__defaults__ is None) for name,function in actual))


def plain_field(cls,name):
    value = inspect.getattr_static(cls,name,MISSING)
    return value is MISSING or not hasattr(value,'__get__')


def plain_defaults(nm,universe):
    if nm is None:
        return True
    defaults = vars(nm).get('_channel_defaults')
    if type(defaults) is not dict:
        return False
    entry = defaults.get(int(universe))
    return (entry is None or (type(entry) is tuple and len(entry) == 2
        and type(entry[0]) is np.ndarray and entry[0].dtype == np.uint8
        and type(entry[1]) is np.ndarray and entry[1].dtype == np.bool_))


def capture_functions(modules):
    rows = []
    for module in modules:
        namespace = vars(module)
        rows.extend((namespace,name,metadata(function)) for name,function in namespace.items()
            if type(function) is FunctionType)
    return rows


def function_rows_current(rows):
    return all(namespace.get(name) is row[0] and metadata_current(row)
        for namespace,name,row in rows)


def value_rows_current(rows):
    return all(namespace.get(name,MISSING) is value for namespace,name,value in rows)


def capture_values(modules):
    return [(vars(module),name,value) for module in modules
        for name,value in vars(module).items() if not name.startswith('__')
        and name not in ('NATIVE','COUNTERS','_GUARD') and type(value) is not FunctionType]


class FadeGuard:
    def __init__(self,engine):
        from src.core import models
        from src.engine import playback_engine
        from src.network.network_manager import NetworkManager
        from . import native_fade_target_schema as schema
        self.safe,self.wrapper,self.wrapper_metadata = False,None,None
        self.owner,self.project,self.nm = playback_engine.PlaybackEngine,models.Project,NetworkManager
        self.engine,self.namespace = engine,vars(playback_engine)
        self.modules = schema.modules()
        self.math,self.runtime = self.modules[:2]
        if not schema.preflight(engine) or not schema.bound(engine,self.math):
            return
        self.dll = vars(engine)['dll']
        self.originals = {name:metadata(inspect.getattr_static(self.owner,name)) for name in METHODS}
        self.original = self.originals['_get_fade_out_target'][0]
        self.module_rows = capture_functions(self.modules)
        self.class_rows = {name:metadata(value) for name,value in vars(FadeGuard).items()
            if type(value) is FunctionType}
        self.globals = capture_values(self.modules)
        self.bindings,self.native_table,self.counter_store = dict(self.math.NATIVE),self.math.NATIVE,self.math.COUNTERS
        self.arguments = schema.arguments()
        self.binding_rows = schema.binding_rows(self.dll,self.bindings)
        self.supplier_rows,self.supplier_values = schema.dependency_rows()
        self.numpy = {name:getattr(np,name) for name in NUMPY_FIELDS}
        self.numpy_wrapped = [(function,getattr(function,'__wrapped__',None))
            for function in (np.sum,np.count_nonzero)]
        self.numpy_rows = [metadata(function) for _,function in self.numpy_wrapped]
        self.accessors = {cls:cls.__getattribute__ for cls in (self.owner,self.project,self.nm)}
        self.class_fields = {cls:{name:inspect.getattr_static(cls,name,MISSING) for name in names}
            for cls,names in ((self.owner,('_nm','_clip_fade_geometry_cache')),
                (self.project,('fixtures','fixture_defs')),(self.nm,('_channel_defaults',)))}
        self.source_globals = {name:self.namespace.get(name,MISSING) for name in schema.SOURCE_BUILTINS}
        self.safe = (all(plain_field(cls,name) for cls,row in self.class_fields.items() for name in row)
            and all(value is object.__getattribute__ for value in self.accessors.values()))

    def attach(self,wrapper):
        self.wrapper,self.wrapper_metadata = wrapper,metadata(wrapper)
        return (self.safe and wrapper.__wrapped__ is self.original
            and wrapper.__globals__ is vars(self.runtime)
            and digest(wrapper) == EXPECTED['wrapper']
            and wrapper.__defaults__ == (None,) and wrapper.__kwdefaults__ is None
            and dict(zip(wrapper.__code__.co_freevars,
                (cell.cell_contents for cell in wrapper.__closure__ or ()))) == {'original':self.original})

    def _wrapper_current(self):
        if not self.safe or self.wrapper is None or not metadata_current(self.wrapper_metadata):
            return False
        cells = self.wrapper.__closure__ or ()
        return (inspect.getattr_static(self.owner,'_get_fade_out_target') is self.wrapper
            and self.wrapper.__wrapped__ is self.original
            and vars(self.modules[3]).get('_GUARD') is self
            and len(cells) == 1 and cells[0].cell_contents is self.original)

    def _methods_current(self):
        for name,row in self.originals.items():
            current = self.wrapper if name == '_get_fade_out_target' else row[0]
            if inspect.getattr_static(self.owner,name) is not current or not metadata_current(row):
                return False
        return all(vars(FadeGuard).get(name) is row[0] and metadata_current(row)
            for name,row in self.class_rows.items())

    def _numpy_current(self):
        return (all(getattr(function,'__wrapped__',None) is original
                    for function,original in self.numpy_wrapped)
            and all(metadata_current(row) for row in self.numpy_rows)
            and self._numpy_keywords_current()
            and self.namespace.get('np') is np
            and all(getattr(np,name) is value for name,value in self.numpy.items()))

    def _numpy_keywords_current(self):
        keywords = self.numpy_rows[1][0].__kwdefaults__
        return (type(keywords) is dict and len(keywords) == 1
            and keywords.get('keepdims') is False)

    def _suppliers_current(self):
        return (function_rows_current(self.module_rows)
            and function_rows_current(self.supplier_rows)
            and value_rows_current(self.globals)
            and value_rows_current(self.supplier_values)
            and all(self.namespace.get(name,MISSING) is value for name,value in self.source_globals.items()))

    def source_current(self):
        return (self._wrapper_current() and self._methods_current()
            and self._suppliers_current() and self._numpy_current())

    def binding_current(self):
        from . import _engine
        from .native_fade_target_schema import arguments_current
        if (_engine is not self.engine or vars(self.engine).get('dll') is not self.dll
                or self.math.NATIVE is not self.native_table or self.math.COUNTERS is not self.counter_store):
            return False
        if self.math.NATIVE.keys() != self.bindings.keys():
            return False
        return (value_rows_current(self.binding_rows)
            and arguments_current(ct._cast,(ct.c_void_p,ct.py_object,ct.py_object),ct.py_object)
            and all(self.math.NATIVE[name] is function
                and arguments_current(function,self.arguments[name],ct.c_int)
                for name,function in self.bindings.items()))

    def _classes_current(self):
        return (all(cls.__getattribute__ is value for cls,value in self.accessors.items())
            and all(inspect.getattr_static(cls,name,MISSING) is value
                for cls,row in self.class_fields.items() for name,value in row.items()))

    def _project_current(self,proj):
        if proj is None:
            return True
        return (type(proj) is self.project and type(vars(proj).get('fixtures')) is list
            and type(vars(proj).get('fixture_defs')) is list)

    def _owner_current(self,owner,universe):
        return (type(owner) is self.owner and type(universe) in (int,bool)
            and not any(name in vars(owner) for name in METHODS)
            and type(vars(owner).get('_clip_fade_geometry_cache')) is dict)

    def current(self,owner,universe,proj):
        if not self.safe or not self._classes_current() or not self._owner_current(owner,universe):
            return False
        if not self._project_current(proj):
            return False
        nm = vars(owner).get('_nm')
        return ((nm is None or type(nm) is self.nm) and self.source_current()
            and self.binding_current() and plain_defaults(nm,universe))
