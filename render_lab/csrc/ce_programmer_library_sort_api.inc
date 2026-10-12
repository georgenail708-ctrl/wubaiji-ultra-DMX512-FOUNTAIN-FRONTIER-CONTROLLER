static int ce_ps_execute(ce_ps_context *c) {
    if(!c->q->n) return 0;
    size_t n=(size_t)c->q->n;
    ce_ps_key *keys=malloc(n*sizeof(*keys));
    double *coordinates=malloc(n*2*sizeof(double));
    int64_t *rows=malloc(n*sizeof(int64_t));
    if(!keys || !coordinates || !rows) {
        free(keys); free(coordinates); free(rows); return -2;
    }
    for(size_t i=0;i<n;++i) rows[i]=(int64_t)i;
    int rc=ce_ps_build_keys(c,keys,coordinates);
    if(!rc && !c->r->semantic) rc=ce_ps_sort_rows(c,keys,rows);
    if(!rc && !c->r->semantic) {
        memcpy(c->q->indices,rows,n*sizeof(int64_t)); c->r->values+=(uint64_t)n;
    }
    free(keys); free(coordinates); free(rows); return rc;
}
CE_API int ce_programmer_library_sort(const ce_ps_request *q,ce_ps_report *r) {
    if(!r) return -1;
    memset(r,0,sizeof(*r)); r->index=-1; ce_cs_result_init(&r->result);
    int rc=ce_ps_valid(q); if(rc) { r->status=rc; return rc; }
    ce_ps_context context; memset(&context,0,sizeof(context));
    context.q=q; context.r=r; context.scalar.request=q->scalar;
    context.scalar.report=&r->scalar; context.scalar.error_index=-1;
    context.scalar.integer_view.integers=q->scalar->integers;
    context.scalar.integer_view.integer_size=q->scalar->integer_size;
    rc=ce_ps_execute(&context); r->status=rc; return rc;
}
CE_API int ce_programmer_library_sort_abi(void) { return CE_PS_ABI; }
CE_API int ce_programmer_library_sort_request_size(void) { return (int)sizeof(ce_ps_request); }
CE_API int ce_programmer_library_sort_report_size(void) { return (int)sizeof(ce_ps_report); }
