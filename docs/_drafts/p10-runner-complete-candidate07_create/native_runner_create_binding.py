"""One actual DLL, complete create entry and retained kind-7 cleanup."""
import ctypes as ct
from . import native_runner_edit_binding as previous
from . import native_runner_retire_binding as retained
from . import native_runner_create_types as t


class Binding(previous.Binding):
    def __init__(self,engine):
        super().__init__(engine)
        token = ct.POINTER(ct.c_ulonglong)
        rows = {'life_destroy':('ce_runner_context_destroy_v7',(ct.c_void_p,ct.c_ulonglong),ct.c_int),
            'life_abort':('ce_runner_retire_abort_v7',(ct.c_void_p,ct.c_ulonglong,ct.c_uint32,token),ct.c_int),
            'life_state':('ce_runner_retire_state_v7',(ct.c_void_p,ct.c_ulonglong,ct.POINTER(ct.c_int),ct.POINTER(ct.c_uint32),token),ct.c_int),
            'train_initialize':('ce_runner_train_initialize_v7',(ct.POINTER(t.Request),ct.POINTER(t.Report)),ct.c_int),
            'train_create':('ce_runner_train_create_prepare_v7',(ct.c_void_p,ct.POINTER(t.Request),token,ct.POINTER(ct.POINTER(t.Report))),ct.c_int),
            'train_accept':('ce_runner_train_create_accept_v7',(ct.c_void_p,token),ct.c_int),
            'train_layout':('ce_runner_train_create_layout_v7',(ct.c_size_t,ct.c_size_t),ct.c_int),
            'install_ready':('ce_runner_create_install_ready_v7',(),ct.c_int)}
        self.rows = [row for row in self.rows if row[0] not in ('life_destroy','life_abort','life_state')]
        self.aliases = [row for row in self.aliases if row[0] not in
            ('ce_runner_context_destroy_v6','ce_runner_retire_abort_v6','ce_runner_retire_state_v6')]
        previous._bind(self,rows)
        self.rows,self.aliases = tuple(self.rows),tuple(self.aliases)
        self.addresses = tuple((function,ct.cast(function,ct.c_void_p).value) for _,function,_,_ in self.rows)
        self.retained = {key:retained.Entry(self.dll,function,args,result) for key,function,args,result in self.rows
            if key in ('life_create','life_destroy','life_abort','life_state','reserve','external')}
        self.create_layouts = tuple((kind,tuple(kind._fields_),tuple(vars(kind).items())) for kind in (t.Request,t.Report))
        if not all(self.functions['train_layout'](index,ct.sizeof(kind)) == 1 for index,(kind,_,_) in enumerate(self.create_layouts)):
            raise RuntimeError('Complete create layout disagrees with actual native DLL')

    def current(self):
        return super().current() and all(_layout_current(kind,fields,metadata) for kind,fields,metadata in self.create_layouts)


def _layout_current(kind,fields,metadata):
    actual = vars(kind).get('_fields_')
    return (type(actual) in (tuple,list) and len(actual) == len(fields)
        and all(a is b for a,b in zip(actual,fields)) and vars(t).get(kind.__name__) is kind
        and len(vars(kind)) == len(metadata) and all(vars(kind).get(name) is value for name,value in metadata))
