"""Owned small edit state permits storing large count metadata without tick."""
from . import native_runner_runtime as old
from . import native_runner_scope_runtime as scope
from . import native_runner_proof as proof
from . import native_runner_budget as budget
from . import native_runner_retire_ledger as ledger


class State:
    def __init__(self,context,owner,native):
        self.context,self.owner,self.native = context,owner,native
        self.lock = native.lock

    def ready(self):
        self.context[2](self.owner)
        if self.native.invalid_adoption or not self.native.binding.current():
            raise ledger.RetirementRefused('Complete create source/binding/adoption qualification refused')
        if budget._count(self.owner) is None or not budget._structures(self.owner,self.context[0].base.module):
            raise ledger.RetirementRefused('Complete create original model schema refused')


def owned(context,owner):
    context[2](owner)
    state = vars(owner).get('_ce_complete_create_state')
    if state is not None:
        if not _retired(context,owner,state):
            return _existing(context,owner,state)
        vars(owner).pop('_ce_complete_create_state')
    native,created,previous = scope._native_scope(context[:3],owner)
    try:
        state = State(context,owner,native)
        state.ready()
        owner._ce_complete_runner,owner._ce_complete_create_state = native,state
        return state
    except BaseException:
        if created:
            vars(owner).pop('_ce_complete_create_state',None)
            old._restore_owner_attachment(owner,previous,proof.MISSING)
            native.close()
        raise


def _retired(context,owner,state):
    if type(state) is not State or state.owner is not owner or state.context is not context:
        raise ledger.RetirementRefused('Complete create state belongs to a foreign owner/provider')
    native = state.native
    if not old._closed_native(native):
        return False
    receipt = vars(native).get('_retirement')
    if type(receipt) is not ledger.Ledger or not receipt.retired or receipt.handle.value or receipt.stage is not None:
        raise ledger.RetirementRefused('Complete create closed state has no completed generation receipt')
    ledger._entry(receipt,())
    return True


def _existing(context,owner,state):
    if type(state) is not State or state.owner is not owner or state.context is not context:
        raise ledger.RetirementRefused('Complete create state belongs to a foreign owner/provider')
    if vars(owner).get('_ce_complete_runner') is not state.native:
        raise ledger.RetirementRefused('Complete create native attachment changed')
    if not old.NativeRunner.current(state.native,context[1]):
        raise ledger.RetirementRefused('Complete create retained native generation changed')
    state.ready()
    return state
