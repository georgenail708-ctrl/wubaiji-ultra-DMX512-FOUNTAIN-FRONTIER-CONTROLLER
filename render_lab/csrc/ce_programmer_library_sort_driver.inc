/* Fixed list_sort_impl3051–3098 control order; public ownership is container-only. */
static int ce_ps_pipeline(ce_ps_MergeState *ms,ce_ps_sortslice lo,ptrdiff_t remaining) {
    ptrdiff_t minrun=ce_ps_merge_compute_minrun(remaining);
    do {
        ptrdiff_t n=ce_ps_count_run(ms,&lo,remaining);
        if(n<0) return -1;
        ++ms->report->runs;
        if(n<minrun) {
            ptrdiff_t force=remaining<=minrun?remaining:minrun;
            if(ce_ps_binarysort(ms,&lo,force,n)<0) return -1;
            n=force;
        }
        int before=ms->n;
        if(ce_ps_found_new_run(ms,n)<0) return -1;
        ms->report->merges+=(uint64_t)(before-ms->n);
        if(ms->n>=CE_PS_MAX_MERGE_PENDING) return -1;
        ms->pending[ms->n].base=lo; ms->pending[ms->n].len=n; ++ms->n;
        ce_ps_sortslice_advance(&lo,n); remaining-=n;
    } while(remaining);
    int before=ms->n;
    if(ce_ps_merge_force_collapse(ms)<0) return -1;
    ms->report->merges+=(uint64_t)(before-ms->n); return 0;
}
static int ce_ps_sort_rows(ce_ps_context *c,ce_ps_key *keys,int64_t *rows) {
    if(c->q->n<2) return 0;
    ce_ps_MergeState ms; memset(&ms,0,sizeof(ms));
    ce_ps_sortslice slice={rows,NULL};
    ce_ps_merge_init(&ms,(ptrdiff_t)c->q->n,0,&slice);
    ms.prepared=keys; ms.report=c->r; ms.key_compare=ce_ps_compare;
    int reverse=c->q->direction==7;
    if(reverse) ce_ps_reverse_slice(rows,rows+c->q->n);
    int rc=ce_ps_pipeline(&ms,slice,(ptrdiff_t)c->q->n);
    if(reverse) ce_ps_reverse_slice(rows,rows+c->q->n);
    ce_ps_merge_freemem(&ms); return ms.alloc_failed?-2:rc;
}
/* Shared fixed CPython selector sort; scalar callbacks own typed comparison. */
static int ce_ps_sort_selectors(int64_t n,int64_t *rows,
    int (*compare)(int64_t,int64_t,ce_ps_MergeState *),void *context,ce_ps_report *report) {
    if(n<0 || (uint64_t)n>PTRDIFF_MAX || (n && !rows) || !compare || !report) return -1;
    if(n<2) return 0;
    ce_ps_MergeState ms;memset(&ms,0,sizeof(ms));ce_ps_sortslice slice={rows,NULL};
    ce_ps_merge_init(&ms,(ptrdiff_t)n,0,&slice);
    ms.report=report;ms.key_compare=compare;ms.compare_context=context;
    int rc=ce_ps_pipeline(&ms,slice,(ptrdiff_t)n);
    ce_ps_merge_freemem(&ms);return ms.alloc_failed?-2:rc;
}
