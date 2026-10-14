/* Generation-safe cleanup knows new kind 7; old R05/R06 functions stay intact. */
CE_API int ce_runner_retire_abort_v7(void* pointer,unsigned long long generation,
    uint32_t kind,const unsigned long long* token)
{
    if (kind != 7) return ce_runner_retire_abort_v6(pointer,generation,kind,token);
    if (!token || !*token) return -1;
    AcquireSRWLockExclusive(&rr_budget_lock);
    rr_life_slot* life=rr_life_find(generation);
    if (!rr_life_valid(life,pointer)) { ReleaseSRWLockExclusive(&rr_budget_lock); return -1; }
    if (life->aborted_kind == 7 && life->aborted_token == *token) {
        int rc=life->abort_completed ? 1 : -5;
        ReleaseSRWLockExclusive(&rr_budget_lock); return rc;
    }
    rr_context* ctx=life->owner; rr_create_slot* row=rr_create_find(ctx);
    if (!ctx->busy || !row || row->token != *token) { ReleaseSRWLockExclusive(&rr_budget_lock); return -5; }
    rr_create_unlink(row); life->aborted_kind=7; life->aborted_token=*token; life->abort_completed=0;
    ReleaseSRWLockExclusive(&rr_budget_lock);
    size_t previous=row->previous_external;
    rr_create_clear(row); rr_restore_external(ctx,previous);
    AcquireSRWLockExclusive(&rr_budget_lock);
    life->abort_completed=1; ctx->busy=0;
    ReleaseSRWLockExclusive(&rr_budget_lock); return 0;
}
CE_API int ce_runner_context_destroy_v7(void* pointer,unsigned long long generation)
{
    AcquireSRWLockExclusive(&rr_budget_lock);
    rr_life_slot* life=rr_life_find(generation);
    if (!life) {
        int rc=generation && generation <= rr_life_nonce ? 1 : -1;
        ReleaseSRWLockExclusive(&rr_budget_lock); return rc;
    }
    if (!rr_life_valid(life,pointer)) { ReleaseSRWLockExclusive(&rr_budget_lock); return -1; }
    rr_context* ctx=life->owner;
    rr_pending_slot* tick=rr_find_pending(ctx);
    rr_scope_slot* scope=rr_scope_find(rr_scope_pending,ctx),*scope_last=rr_scope_find(rr_scope_last,ctx);
    rr_reg_slot* reg=rr_reg_find(rr_reg_pending,ctx),*reg_last=rr_reg_find(rr_reg_last,ctx);
    rr_edit_slot* edit=rr_edit_find(ctx); rr_create_slot* created=rr_create_find(ctx);
    if (ctx->busy && !tick && !scope && !reg && !edit && !created) { ReleaseSRWLockExclusive(&rr_budget_lock); return -5; }
    if (tick) rr_unlink_pending(tick);
    if (scope) rr_scope_unlink(&rr_scope_pending,scope);
    if (scope_last) rr_scope_unlink(&rr_scope_last,scope_last);
    if (reg) rr_reg_unlink(&rr_reg_pending,reg);
    if (reg_last) rr_reg_unlink(&rr_reg_last,reg_last);
    if (edit) rr_edit_unlink(edit);
    if (created) rr_create_unlink(created);
    rr_context** head=&rr_contexts;
    while (*head != ctx) head=&(*head)->next_context;
    *head=ctx->next_context; rr_life_unlink(life);
    ReleaseSRWLockExclusive(&rr_budget_lock);
    rr_release_pending(tick); rr_scope_clear(scope); rr_scope_clear(scope_last);
    rr_reg_clear(reg); rr_reg_clear(reg_last); rr_edit_clear(edit); rr_create_clear(created); rr_free(life);
    rr_clear_report(&ctx->report); rr_clear_work(&ctx->work); rr_clear_geometry(&ctx->geometry);
    rr_system_release(ctx,&ctx->budget,sizeof(rr_context)); return 0;
}
CE_API int ce_runner_retire_state_v7(void* pointer,unsigned long long generation,
    int* busy,uint32_t* kind,unsigned long long* token)
{
    if (!busy || !kind || !token) return -1;
    AcquireSRWLockExclusive(&rr_budget_lock);
    rr_life_slot* life=rr_life_find(generation);
    if (!rr_life_valid(life,pointer)) { ReleaseSRWLockExclusive(&rr_budget_lock); return -1; }
    rr_context* ctx=life->owner; *busy=ctx->busy; *kind=0; *token=0;
    rr_pending_slot* tick=rr_find_pending(ctx);
    rr_scope_slot* scope=rr_scope_find(rr_scope_pending,ctx);
    rr_reg_slot* reg=rr_reg_find(rr_reg_pending,ctx);
    rr_edit_slot* edit=rr_edit_find(ctx); rr_create_slot* created=rr_create_find(ctx);
    if (tick) { *kind=2; *token=tick->token; }
    else if (scope) { *kind=3; *token=scope->token; }
    else if (reg) { *kind=4; *token=reg->token; }
    else if (edit) { *kind=6; *token=edit->token; }
    else if (created) { *kind=7; *token=created->token; }
    ReleaseSRWLockExclusive(&rr_budget_lock); return 0;
}
