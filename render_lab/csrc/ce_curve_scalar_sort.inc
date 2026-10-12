/* Numeric-key fallback shares the fixed CPython Powersort selector kernel.
 * Scalar key '<' intentionally has no tuple owner-equality/NaN shortcut.
 * See the preserved full PSF license and R214 source/adaptation ledger. */
static int ce_cs_sort_compare(int64_t left,int64_t right,ce_ps_MergeState *ms) {
    ce_cs_context *c=(ce_cs_context *)ms->compare_context;
    const ce_cs_pair *points=c->request->fallback_points;
    int relation=0;
    ++ms->report->comparisons;
    int rc=ce_cs_compare(c,&points[left].x,&points[right].x,&relation);
    return rc?-1:relation==-1;
}
static int ce_cs_fallback_order(ce_cs_context *c,int64_t **ordered) {
    int64_t n=c->request->fallback_count;
    *ordered=NULL;
    if(n<2) return 0;
    if((uint64_t)n>(uint64_t)PTRDIFF_MAX || !ce_cs_count(n,sizeof(int64_t))) return -1;
    int64_t *rows=(int64_t *)malloc((size_t)n*sizeof(int64_t));
    if(!rows) return -2;
    for(int64_t i=0;i<n;++i) rows[i]=i;
    ce_ps_report report;memset(&report,0,sizeof(report));
    if(c->sort_report) { ++c->sort_report->calls;c->sort_report->keys+=(uint64_t)n; }
    int rc=ce_ps_sort_selectors(n,rows,ce_cs_sort_compare,c,&report);
    if(c->sort_report) {
        c->sort_report->comparisons+=report.comparisons;
        c->sort_report->runs+=report.runs;c->sort_report->merges+=report.merges;
    }
    if(rc) { free(rows);return rc; }
    *ordered=rows;return 0;
}
static const ce_cs_pair *ce_cs_fallback_at(const ce_cs_context *c,
    const int64_t *ordered,int64_t position) {
    return c->request->fallback_points+(ordered?ordered[position]:position);
}
