static rr_create_slot* rr_create_find(rr_context* owner)
{
    for (rr_create_slot* row=rr_create_pending;row;row=row->link) if (row->owner == owner) return row;
    return NULL;
}
static void rr_create_unlink(rr_create_slot* row)
{
    rr_create_slot** head=&rr_create_pending;
    while (*head && *head != row) head=&(*head)->link;
    if (*head) *head=row->link;
}
static void rr_create_clear(rr_create_slot* row)
{
    if (!row) return;
    rr_edge* edge=&row->report.edit.geometry.edge;
    rr_free(edge->points); rr_free(edge->cum); rr_free(edge->projected_s);
    rr_free(edge->projected_d); rr_free(edge->sorted); rr_free(row->report.edit.geometry.markers);
    rr_free_train(&row->report.edit.train); rr_free(row);
}
static int rr_create_reserve(rr_context* ctx,size_t bytes,size_t* previous)
{
    AcquireSRWLockExclusive(&rr_budget_lock);
    rr_budget* budget=&ctx->budget; *previous=budget->external;
    if (bytes < *previous) bytes=*previous;
    size_t global=rr_global_used-budget->external;
    int rc=bytes <= RR_OWNER_LIMIT && budget->used <= RR_OWNER_LIMIT-bytes
        && global <= RR_GLOBAL_LIMIT && bytes <= RR_GLOBAL_LIMIT-global ? 0 : RR_BUDGET_REFUSED;
    if (!rc) { rr_global_used=global+bytes; budget->external=bytes; }
    ReleaseSRWLockExclusive(&rr_budget_lock); return rc;
}
CE_API int ce_runner_train_create_prepare_v7(void* pointer,const rr_create_request* input,
    unsigned long long* token,const rr_create_report** result)
{
    if (!token || !result) return -1;
    int rc=rr_create_admission(input); if (rc) return rc;
    if (input->operation == RR_CREATE_DIRECT) return -1;
    size_t needed; rc=rr_create_host_size(input,&needed); if (rc) return rc;
    unsigned long long nonce; rr_context* ctx=rr_begin_pending(pointer,&nonce); if (!ctx) return -5;
    size_t prior=0; rc=rr_create_reserve(ctx,needed,&prior);
    if (rc) { rr_end_pending(ctx); return rc; }
    rr_budget* previous=rr_current_budget; rr_current_budget=&ctx->budget;
    rr_create_slot* row=(rr_create_slot*)rr_calloc(1,sizeof(rr_create_slot));
    if (!row) rc=-2;
    else { row->owner=ctx; row->token=nonce; row->previous_external=prior; }
    if (!rc) rc=rr_create_calculate(&row->report,input);
    if (!rc) row->report.host_bytes=ctx->budget.external;
    if (ctx->budget.refused) rc=RR_BUDGET_REFUSED;
    if (!rc) {
        AcquireSRWLockExclusive(&rr_budget_lock);
        row->link=rr_create_pending; rr_create_pending=row;
        ReleaseSRWLockExclusive(&rr_budget_lock);
        *token=nonce; *result=&row->report;
    } else { rr_create_clear(row); rr_restore_external(ctx,prior); rr_end_pending(ctx); }
    rr_current_budget=previous; return rc;
}
static rr_create_slot* rr_create_take(void* pointer,unsigned long long token)
{
    AcquireSRWLockExclusive(&rr_budget_lock);
    rr_context* ctx=rr_known_context(pointer);
    rr_create_slot* row=ctx ? rr_create_find(ctx) : NULL;
    if (!row || !ctx->busy || row->token != token) row=NULL;
    if (row) rr_create_unlink(row);
    ReleaseSRWLockExclusive(&rr_budget_lock); return row;
}
CE_API int ce_runner_train_create_accept_v7(void* pointer,const unsigned long long* token)
{
    if (!token) return -1;
    rr_create_slot* row=rr_create_take(pointer,*token); if (!row) return -5;
    rr_context* ctx=row->owner; rr_create_clear(row); rr_end_pending(ctx); return 0;
}
CE_API int ce_runner_train_create_layout_v7(size_t kind,size_t bytes)
{
    return kind == 0 ? bytes == sizeof(rr_create_request) : kind == 1 ? bytes == sizeof(rr_create_report) : 0;
}
CE_API int ce_runner_create_install_ready_v7(void)
{
    /* The composed provider is installed by the owner thread before owners. */
    AcquireSRWLockExclusive(&rr_budget_lock);
    int ready=rr_contexts == NULL && rr_create_pending == NULL && rr_edit_pending == NULL
        && rr_pending_slots == NULL && rr_scope_pending == NULL && rr_scope_last == NULL
        && rr_reg_pending == NULL && rr_reg_last == NULL;
    ReleaseSRWLockExclusive(&rr_budget_lock); return ready;
}
