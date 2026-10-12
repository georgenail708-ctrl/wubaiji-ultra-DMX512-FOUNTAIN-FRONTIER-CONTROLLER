static int ce_pl_shape(ce_pl_context *c) {
    const ce_pl_request *q=c->q;
    for(int64_t g=0;g<q->group_n;++g) {
        int64_t begin=q->offsets[g],end=q->offsets[g+1],n=end-begin;
        for(int64_t i=begin;i<end;++i) {
            double rank=n<=1?0.:(double)(i-begin)/(double)(n-1);
            ce_traj_value start=ce_pl_parameter(q,0),end=ce_pl_parameter(q,1);
            double delta,s;int rc=ce_pl_difference(c,&end,&start,&delta);
            if(rc || c->r->semantic) return rc;
            double product=rank*delta;
            rc=ce_pl_to_double(c,&start,&s);if(rc || c->r->semantic) return rc;
            double phase=s+product;
            if(q->op==PL_UNIT_PHASE) { q->out_f[i]=phase; ++c->r->values; continue; }
            ce_traj_value value=ce_pl_float(phase); ce_traj_result result;
            rc=ce_cs_eval_phase(&c->scalar,&value,&result);
            if(rc || ce_pl_scalar_result(c,&result)) return rc;
            q->out_f[i]=result.number; ++c->r->shape; ++c->r->values;
        }
    }
    return 0;
}
static int ce_pl_effective_result(ce_pl_context *c) {
    ce_traj_value value; int rc=ce_pl_effective(c,&value); if(rc) return rc;
    ce_cs_result_init(&c->r->result);
    if(value.selector>=0) {
        c->r->result.kind=CE_TR_SELECTED; c->r->result.selector=value.selector;
    } else { c->r->result.kind=CE_TR_NUMBER; c->r->result.number=value.number; }
    return 0;
}
