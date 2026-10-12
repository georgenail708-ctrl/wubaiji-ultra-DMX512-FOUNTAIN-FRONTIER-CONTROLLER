static int ce_pl_quantize(ce_pl_context *c,double value,uint8_t *out,int64_t i) {
    double clamped=ce_cs_clamp(&c->scalar,value),scaled=clamped*255.;
    if(isnan(scaled)) return ce_pl_fail(c,CE_TR_NAN,PL_QUANT_STAGE,i);
    if(isinf(scaled)) return ce_pl_fail(c,CE_TR_INF,PL_QUANT_STAGE,i);
    *out=(uint8_t)ce_round_half_even(scaled);
    ++c->r->quantize; ++c->r->values; return 0;
}
static int ce_pl_value(ce_pl_context *c,int64_t i,double *v) {
    const ce_pl_request *q=c->q;
    if(!(q->flags&PL_TYPED) && !q->typed_a) { if(!q->a) return -1; *v=q->a[i]; return 0; }
    if(q->typed_a) {
        ce_traj_result result;int rc=ce_cs_clamped_value(&c->scalar,q->typed_a+i,&result);
        if(rc || ce_pl_scalar_result(c,&result)) return rc;*v=result.number;return 0;
    }
    if(!q->values || !ce_cs_value_valid(q->scalar,q->values+i)) return -1;
    ce_traj_result result; int rc=ce_cs_clamped_value(&c->scalar,q->values+i,&result);
    if(rc || ce_pl_scalar_result(c,&result)) return rc;
    *v=result.number; return 0;
}
static int ce_pl_quantize_rows(ce_pl_context *c) {
    for(int64_t i=0;i<c->q->n;++i) {
        double value; int rc=ce_pl_value(c,i,&value); if(rc) return rc;
        rc=ce_pl_quantize(c,value,c->q->out_b+i,i);
        if(rc || c->r->semantic) return rc;
    }
    return 0;
}
static int ce_pl_blend_rows(ce_pl_context *c) {
    const ce_pl_request *q=c->q;
    if(q->n && ((!q->a && !q->typed_a) || (!q->b && !q->typed_b) || !q->c)) return -1;
    for(int64_t i=0;i<q->n;++i) {
        ce_traj_value from_value=q->typed_a?q->typed_a[i]:ce_pl_float(q->a[i]);
        ce_traj_value to_value=q->typed_b?q->typed_b[i]:ce_pl_float(q->b[i]);
        if(q->flags&(PL_DEFAULT_FROM|PL_DEFAULT_TO)) {
            if(!q->values) return -1;
            ce_traj_result value;
            int rc=ce_cs_clamped_value(&c->scalar,q->values+i,&value);
            if(rc || ce_pl_scalar_result(c,&value)) return rc;
            if(q->flags&PL_DEFAULT_FROM) from_value=ce_pl_float(value.number);
            else to_value=ce_pl_float(value.number);
        }
        double delta,from;int selector=-1;
        int rc=ce_cs_difference(&c->scalar,&to_value,&from_value,&delta,&selector);
        if(rc>0) { ce_pl_fail(c,rc,PL_VALUE_STAGE,i);c->r->result.selector=selector;return 0; }
        if(rc) return rc;
        double product=delta*q->c[i];
        rc=ce_cs_double(&c->scalar,&from_value,&from);
        if(rc>0) { ce_pl_fail(c,rc,PL_VALUE_STAGE,i);c->r->result.selector=from_value.selector;return 0; }
        if(rc) return rc;
        q->out_f[i]=from+product;
        rc=ce_pl_quantize(c,q->out_f[i],q->out_b+i,i);
        if(rc || c->r->semantic) return rc;
        ++c->r->blend;
    }
    return 0;
}
static int ce_pl_effective(ce_pl_context *c,ce_traj_value *out) {
    const ce_pl_request *q=c->q; ce_traj_value zero=ce_pl_float(0.),one=ce_pl_float(1.);
    if(!ce_cs_value_valid(q->scalar,&q->groups)
        || !ce_cs_value_valid(q->scalar,&q->batches)) return -1;
    int rel,rc=ce_cs_compare(&c->scalar,&q->batches,&zero,&rel); if(rc) return rc;
    *out=q->groups;
    if(rel>0 && rel!=2) {
        rc=ce_cs_compare(&c->scalar,&q->batches,&q->groups,&rel); if(rc) return rc;
        if(rel<0) *out=q->batches;
    }
    rc=ce_cs_compare(&c->scalar,out,&one,&rel); if(rc) return rc;
    if(rel!=1) *out=one; return 0;
}
static int ce_pl_int_bound(ce_pl_context *c,const ce_traj_value *value,int64_t *out) {
    if(value->kind==CE_TR_FLOAT) {
        double number=value->number;
        if(isnan(number)) return ce_pl_fail(c,CE_TR_NAN,103,-1);
        if(isinf(number)) return ce_pl_fail(c,CE_TR_INF,103,-1);
        if(number<1.) { *out=1; return 0; }
        if(number>=(double)c->q->n) { *out=c->q->n>0?c->q->n:1; return 0; }
        *out=(int64_t)number; return 0;
    }
    if(!ce_cs_integer(value)) return -1;
    ce_traj_value one=ce_pl_float(1.),bound=ce_pl_float(c->q->n>0?(double)c->q->n:1.);
    int relation,rc=ce_cs_compare(&c->scalar,value,&one,&relation); if(rc) return rc;
    if(relation<=0) { *out=1; return 0; }
    rc=ce_cs_compare(&c->scalar,value,&bound,&relation); if(rc) return rc;
    if(relation>=0) { *out=c->q->n>0?c->q->n:1; return 0; }
    double number; rc=ce_cs_double(&c->scalar,value,&number); if(rc) return rc;
    *out=(int64_t)number; return 0;
}
static int ce_pl_batch_count(ce_pl_context *c,int64_t *out) {
    ce_traj_value value=c->q->groups; int rc=0;
    if(c->q->op!=PL_PARTITION) rc=ce_pl_effective(c,&value);
    if(rc || c->r->semantic) return rc;
    return ce_pl_int_bound(c,&value,out);
}
static int64_t ce_pl_batch_of(int64_t n,int64_t bc,int64_t i) {
    int64_t step=n>0?(n+bc-1)/bc:1,b=i/step;
    return b<bc?b:bc-1;
}
static int ce_pl_partition(ce_pl_context *c) {
    int64_t bc; int rc=ce_pl_batch_count(c,&bc);
    if(rc || c->r->semantic) return rc;
    for(int64_t i=0;i<c->q->n;++i) c->q->out_i[i]=ce_pl_batch_of(c->q->n,bc,i);
    c->q->out_f[0]=(double)bc;c->q->out_i[c->q->n]=bc;
    c->r->partition+=(uint64_t)c->q->n; return 0;
}
