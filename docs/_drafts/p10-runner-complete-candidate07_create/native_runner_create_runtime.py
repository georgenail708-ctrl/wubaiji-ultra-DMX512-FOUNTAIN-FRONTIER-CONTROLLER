"""Complete create transaction and direct constructor; no Python signal math."""
import ctypes as ct
from . import native_runner_register_runtime as statuses
from . import native_runner_retire_ledger as ledger
from . import native_runner_create_model as model
from . import native_runner_create_adopt as adopt
from . import native_runner_create_state as owners
from . import native_runner_create_types as t
from . import native_runner_edit_runtime as edit


class Call:
    def __init__(self,native,request):
        self.native,self.request = native,request
        self.token,self.result,self.external = ct.c_ulonglong(),ct.POINTER(t.Report)(),ct.c_size_t()
        self.token_ref = ct.byref(self.token)
        self.prepare_args = None
        if request is not None:
            self.bind(request)
        self.finish_args = native.context,self.token_ref
        self.pending = self.committing = self.reserved = False
        statuses._status(native.binding.functions['external'](native.context,ct.byref(self.external)))
        self.previous = self.external.value
        ledger.attach(native._retirement,self,7)

    def bind(self,request):
        self.request = request
        self.prepare_args = self.native.context,ct.byref(request),self.token_ref,ct.byref(self.result)

    def reserve(self,amount):
        amount = self.previous if amount < self.previous else amount
        statuses._status(self.native.binding.functions['reserve'](self.native.context,amount))
        self.reserved = True

    def calculate(self):
        # C atomically reserves complete host + cars bytes before any allocation.
        status = self.native.binding.functions['train_create'](*self.prepare_args)
        self.pending = status == 0
        self.reserved = self.pending
        statuses._status(status)
        return self.result.contents

    def abort(self):
        ledger.cleanup(self.native._retirement)
        self.pending = self.reserved = False


def create(context,owner,spec):
    context[2](owner)
    allowance = model.reservation(owner,context[0].base.module,spec)
    state = owners.owned(context,owner)
    with state.lock:
        state.ready()
        # Original state may have changed while acquiring the real owner lock.
        allowance = model.reservation(owner,context[0].base.module,spec)
        return _transaction(context,state,spec,allowance)


def _transaction(context,state,spec,allowance):
    call = Call(state.native,None)
    try:
        # Charge the actual DLL before raw/current snapshots and readback packing.
        # The kind7 token-zero ledger already retains the original external charge.
        call.reserve(allowance)
        packed = model.Model(state.owner,spec,allowance)
        call.bind(packed.request)
        state.ready()
        report = call.calculate()
        plan = adopt.Prepared(packed,report,context[0].base.module)
        state.ready()
        if not packed.current():
            raise ledger.RetirementRefused('Create owner/source changed before publication')
        call.committing = True
        plan.commit()
        state.ready()  # Recheck actual DLL/address/prototype before acceptance.
        statuses._status(state.native.binding.functions['train_accept'](*call.finish_args))
        call.pending = False
        ledger.accepted(state.native._retirement)
        return packed.spec['identity']
    except BaseException:
        if call.committing:
            state.native.invalid_adoption = True
        _abort(state,call)
        raise


def _abort(state,call):
    try:
        call.abort()
    except BaseException:
        state.native.invalid_adoption = True
        raise


def initialize(context,train,spec):
    context[3](train)
    allowance = model.reservation(None,context[0].base.module,spec)
    packed = model.Model(None,spec,allowance)
    report = t.Report()
    statuses._status(context[1].functions['train_initialize'](ct.byref(packed.request),ct.byref(report)))
    adopt._shape(packed,report)
    fields = adopt.values(packed,report)
    context[3](train)
    adopt.initialize(train,fields)
