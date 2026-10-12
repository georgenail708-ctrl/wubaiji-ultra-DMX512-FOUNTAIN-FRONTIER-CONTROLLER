static int ce_pl_pause(ce_pl_context *c,int64_t i,double *out) {
    ce_traj_value value=ce_pl_pause_value(c->q,i);return ce_pl_to_double(c,&value,out);
}
static int ce_pl_offsets(ce_pl_context *c) {
    const ce_pl_request *q=c->q;if(q->group_n && !q->a && !q->typed_a) return -1;
    ce_traj_value count;int rc=ce_pl_effective(c,&count);if(rc) return rc;
    if(!ce_cs_integer(&count) && count.selector>=0) {
        ce_pl_fail(c,CE_TR_PARSE_TYPE,104,-1);c->r->result.selector=count.selector;return 0;
    }
    if(q->flags&32u) {
        rc=ce_pl_span(c,q->n);if(!rc && !c->r->semantic) c->r->offsets+=(uint64_t)q->n;
        return rc;
    }
    double cursor=0.;
    for(int64_t b=0;b<q->n;++b) {
        double per,pause;q->out_f[b]=cursor;
        rc=ce_pl_parameter_double(c,0,&per);if(rc || c->r->semantic) return rc;
        cursor+=per;
        if(b<q->n-1) {
            rc=ce_pl_pause(c,b,&pause);if(rc || c->r->semantic) return rc;cursor+=pause;
        }
    }
    c->r->offsets+=(uint64_t)q->n; return 0;
}
static int ce_pl_group_duration(ce_pl_context *c,double *out) {
    const ce_pl_request *q=c->q;double parameter;
    int rc=ce_pl_parameter_double(c,1,&parameter);if(rc || c->r->semantic) return rc;
    *out=parameter>0.?parameter:1.;
    if(!q->values) return 0;
    const ce_traj_value *v=q->values; if(v->kind==CS_NONE) { *out=1.; return 0; }
    if(!ce_cs_value_valid(q->scalar,v)) return -1;
    ce_traj_value zero=ce_pl_float(0.); int rel;rc=ce_cs_compare(&c->scalar,v,&zero,&rel);
    if(rc) return rc;
    if(rel<=0 || rel==2) { *out=1.; return 0; }
    rc=ce_cs_double(&c->scalar,v,out);
    if(rc>0) { ce_pl_fail(c,rc,PL_VALUE_STAGE,-1); c->r->result.selector=v->selector; return 0; }
    return rc;
}
static int ce_pl_group_schedule(ce_pl_context *c,int64_t bc,double f,
                                 double *per_group,double *elapsed,double *offsets) {
    const ce_pl_request *q=c->q;
    double duration; int rc=ce_pl_group_duration(c,&duration);
    if(rc || c->r->semantic) return rc;
    double sum;rc=ce_pl_pause_sum_double(c,bc,&sum);if(rc || c->r->semantic) return rc;
    double computed=(duration-sum)/(double)bc;
    *per_group=computed>0.?computed:0.; *elapsed=f*duration;
    double cursor=0.;
    for(int64_t b=0;b<bc;++b) {
        offsets[b]=cursor; cursor+=*per_group;
        if(b<bc-1) {
            double pause;rc=ce_pl_pause(c,b,&pause);if(rc || c->r->semantic) return rc;
            cursor+=pause;
        }
    }
    return 0;
}
static int ce_pl_group_row(ce_pl_context *c,int64_t i,int64_t bc,
                            const double *offsets,double f,double per,double elapsed) {
    const ce_pl_request *q=c->q;
    int64_t b=ce_pl_batch_of(q->n,bc,i),step=(q->n+bc-1)/bc;
    int64_t begin=b*step,end=begin+step; if(end>q->n) end=q->n;
    int64_t m=end-begin,local=i-begin; double local_f=f;
    int arrived=1;
    if((q->flags&PL_SEQUENTIAL) && bc>1) {
        if(f>=1.-1e-9) local_f=1.;
        else if(elapsed+1e-9<offsets[b]) { local_f=0.; arrived=0; }
        else if(per>1e-9) {
            double value=(elapsed-offsets[b])/per;
            local_f=q->op==PL_REVEAL?(value<1.?value:1.):ce_cs_clamp(&c->scalar,value);
        } else local_f=1.;
    }
    double rank=m<=1?0.:(double)local/(double)(m-1);
    if(q->op==PL_REVEAL) q->out_flags[i]=(uint8_t)(arrived && rank<=local_f+1e-6);
    else {
        double sweep=q->flags&PL_WHOLE?0.:.6,ramp=1.-sweep;
        if(ramp<1e-6) ramp=1e-6;
        q->out_f[i]=ce_cs_clamp(&c->scalar,(local_f-rank*sweep)/ramp);
    }
    return 0;
}
static int ce_pl_group_rows(ce_pl_context *c) {
    const ce_pl_request *q=c->q; if(q->group_n && !q->a && !q->typed_a) return -1;
    int64_t bc; int rc=ce_pl_batch_count(c,&bc);
    if(rc || c->r->semantic) return rc;
    ce_traj_result progress;
    rc=ce_cs_clamped_value(&c->scalar,&q->scalar->argument,&progress);
    if(rc || ce_pl_scalar_result(c,&progress)) return rc;
    double f=progress.number; q->out_f[q->n]=f;
    if(q->op==PL_REVEAL) { double v=f/.9; f=v<1.?v:1.; }
    double *offsets=malloc((size_t)bc*sizeof(double)); if(!offsets) return -2;
    double per=0.,elapsed=0.;
    if((q->flags&PL_SEQUENTIAL) && bc>1 && !(f>=1.-1e-9))
        rc=ce_pl_group_schedule(c,bc,f,&per,&elapsed,offsets);
    if(!rc && !c->r->semantic) for(int64_t i=0;i<q->n;++i)
        ce_pl_group_row(c,i,bc,offsets,f,per,elapsed);
    free(offsets);
    if(q->op==PL_REVEAL) c->r->reveal+=(uint64_t)q->n;
    else c->r->morph+=(uint64_t)q->n;
    return rc;
}
